"""分块文件发送器 - 支持滑动窗口流量控制"""
import threading
import time
from pathlib import Path
from typing import Optional, Callable, Set, Tuple, Dict
from collections import OrderedDict

from config import CHUNK_SIZE, MAX_RETRY, ACK_TIMEOUT, SEND_WINDOW_SIZE, ACK_WAIT_TIMEOUT
from transfer.state_manager import TransferStateManager, SendingState
from file_handler import FileHandler


class SlidingWindowSender:
    """滑动窗口发送器 - 实现流量控制"""

    def __init__(self, window_size: int = SEND_WINDOW_SIZE):
        self.window_size = window_size
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)

        # 窗口状态
        self._window_start: int = 0      # 窗口起始位置
        self._window_end: int = 0        # 窗口结束位置
        self._acked_count: int = 0       # 已确认的数量

        # 待确认的块 {chunk_index: send_time}
        self._pending_acks: Dict[int, float] = OrderedDict()

        # 已确认的块集合
        self._acked_set: Set[int] = set()

        # 发送失败或超时的块
        self._failed_chunks: Set[int] = set()

        # 总块数
        self._total_chunks: int = 0

    def set_total_chunks(self, total: int):
        """设置总块数"""
        self._total_chunks = total
        self._window_end = min(self.window_size, total)

    def can_send(self) -> bool:
        """检查是否可以发送更多数据"""
        with self.lock:
            return len(self._pending_acks) < self.window_size

    def get_send_window(self) -> list:
        """获取当前可发送的块索引列表"""
        with self.lock:
            available = []
            for i in range(self._total_chunks):
                if i in self._acked_set:
                    continue
                if i in self._pending_acks:
                    continue
                if len(self._pending_acks) >= self.window_size:
                    break
                available.append(i)
            return available

    def mark_sent(self, chunk_index: int):
        """标记块已发送，等待确认"""
        with self.lock:
            if chunk_index not in self._acked_set:
                self._pending_acks[chunk_index] = time.time()
                self.condition.notify_all()

    def mark_acked(self, chunk_index: int) -> bool:
        """
        标记块已确认
        Returns:
            是否是新的确认
        """
        with self.lock:
            if chunk_index in self._acked_set:
                return False

            self._acked_set.add(chunk_index)
            self._acked_count += 1

            # 从待确认列表移除
            if chunk_index in self._pending_acks:
                del self._pending_acks[chunk_index]

            # 滑动窗口
            self._slide_window()
            self.condition.notify_all()
            return True

    def mark_acked_batch(self, chunk_indices: list) -> int:
        """
        批量标记确认
        Returns:
            新确认的数量
        """
        with self.lock:
            new_count = 0
            for idx in chunk_indices:
                if idx not in self._acked_set:
                    self._acked_set.add(idx)
                    self._acked_count += 1
                    new_count += 1

                if idx in self._pending_acks:
                    del self._pending_acks[idx]

            self._slide_window()
            self.condition.notify_all()
            return new_count

    def _slide_window(self):
        """滑动窗口"""
        while self._window_start < self._total_chunks:
            if self._window_start in self._acked_set:
                self._window_start += 1
            else:
                break

        self._window_end = min(
            self._window_start + self.window_size,
            self._total_chunks
        )

    def wait_for_window(self, timeout: float = ACK_WAIT_TIMEOUT) -> bool:
        """
        等待窗口有空间
        Returns:
            是否有空间可用
        """
        with self.condition:
            start_time = time.time()
            while len(self._pending_acks) >= self.window_size:
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return True

    def get_timeout_chunks(self, timeout: float = ACK_WAIT_TIMEOUT) -> list:
        """获取超时未确认的块"""
        with self.lock:
            now = time.time()
            timeout_chunks = []
            for idx, send_time in list(self._pending_acks.items()):
                if now - send_time > timeout:
                    timeout_chunks.append(idx)
                    del self._pending_acks[idx]
            return timeout_chunks

    def is_complete(self) -> bool:
        """检查是否全部完成"""
        with self.lock:
            return self._acked_count >= self._total_chunks

    def get_progress(self) -> Tuple[int, int]:
        """获取进度 (已确认块数, 总块数)"""
        with self.lock:
            return (self._acked_count, self._total_chunks)

    def get_pending_count(self) -> int:
        """获取待确认的数量"""
        with self.lock:
            return len(self._pending_acks)


class ChunkedFileSender:
    """分块文件发送器 - 支持滑动窗口流量控制和断点续传"""

    def __init__(self, state_manager: TransferStateManager = None,
                 on_progress: Callable[[int, int], None] = None,
                 on_send_chunk: Callable[[int, bytes], bool] = None):
        """
        Args:
            state_manager: 状态管理器
            on_progress: 进度回调 (已确认块数, 总块数)
            on_send_chunk: 块发送回调 (块索引, 块数据) -> 是否成功
        """
        self.state_manager = state_manager or TransferStateManager()
        self.on_progress = on_progress
        self.on_send_chunk = on_send_chunk

        self.current_state: Optional[SendingState] = None
        self.file_handle = None
        self._sent_set: Set[int] = set()
        self._is_folder: bool = False
        self._temp_zip_path: Optional[str] = None

        # 滑动窗口
        self.window = SlidingWindowSender()

        # 重试计数
        self._retry_count: Dict[int, int] = {}

        # 完成回调
        self._complete_event = threading.Event()

    def prepare(self, filepath: str, receiver_device_id: str = '') -> Tuple[str, int, str, bool]:
        """
        准备发送文件
        Args:
            filepath: 文件或文件夹路径
            receiver_device_id: 接收方设备ID
        Returns:
            (文件名, 大小, 哈希, 是否为文件夹)
        """
        path = Path(filepath)

        # 处理文件夹
        if path.is_dir():
            self._is_folder = True
            file_handler = FileHandler()
            self._temp_zip_path = file_handler.create_temp_zip(filepath)
            send_path = self._temp_zip_path
            filename = f"{path.name}.zip"
        else:
            self._is_folder = False
            send_path = str(path)
            filename = path.name

        # 获取文件信息
        file_size = Path(send_path).stat().st_size
        file_hash = FileHandler.get_file_hash(send_path)

        # 检查是否有未完成的发送
        existing_state = self.state_manager.load_sending_state(file_hash)
        if existing_state and existing_state.file_path == send_path:
            self.current_state = existing_state
            self._sent_set = set(existing_state.sent_chunks)
        else:
            # 创建新的发送状态
            self.current_state = self.state_manager.create_sending_state(
                file_path=send_path,
                file_name=filename,
                file_size=file_size,
                file_hash=file_hash,
                receiver_device_id=receiver_device_id
            )
            self._sent_set = set()

        # 初始化滑动窗口
        self.window.set_total_chunks(self.current_state.total_chunks)

        # 如果有已发送的块，标记为已确认
        if self._sent_set:
            self.window.mark_acked_batch(list(self._sent_set))

        # 打开文件
        self.file_handle = open(send_path, 'rb')

        return filename, file_size, file_hash, self._is_folder

    def read_chunk(self, chunk_index: int) -> Optional[bytes]:
        """读取指定块的数据"""
        if not self.current_state or not self.file_handle:
            return None

        offset = chunk_index * self.current_state.chunk_size
        self.file_handle.seek(offset)
        return self.file_handle.read(self.current_state.chunk_size)

    def send_with_window(self, max_retries: int = MAX_RETRY) -> bool:
        """
        使用滑动窗口发送数据
        Returns:
            是否全部发送成功
        """
        if not self.current_state:
            return False

        total = self.current_state.total_chunks
        self._complete_event.clear()

        # 退避重试参数
        base_backoff = 0.1  # 基础退避时间 100ms
        max_backoff = 5.0   # 最大退避时间 5s

        while not self.window.is_complete():
            # 获取可发送的块
            available = self.window.get_send_window()

            send_failed = False
            for chunk_index in available:
                # 读取数据
                data = self.read_chunk(chunk_index)
                if data is None:
                    continue

                # 发送
                if self.on_send_chunk:
                    success = self.on_send_chunk(chunk_index, data)
                    if success:
                        self.window.mark_sent(chunk_index)
                        self._retry_count.pop(chunk_index, None)
                    else:
                        send_failed = True
                        # 发送失败，记录重试次数
                        retry_count = self._retry_count.get(chunk_index, 0) + 1
                        self._retry_count[chunk_index] = retry_count

                        if retry_count >= max_retries:
                            return False

            # 发送失败后退避等待
            if send_failed:
                failed_count = sum(1 for v in self._retry_count.values() if v > 0)
                backoff = min(base_backoff * (2 ** min(failed_count, 5)), max_backoff)
                time.sleep(backoff)

            # 等待窗口有空间
            if not self.window.wait_for_window(ACK_WAIT_TIMEOUT):
                # 超时，检查是否有超时的块需要重发
                timeout_chunks = self.window.get_timeout_chunks(ACK_WAIT_TIMEOUT)
                for idx in timeout_chunks:
                    self._retry_count[idx] = self._retry_count.get(idx, 0) + 1
                    if self._retry_count[idx] >= max_retries:
                        return False
                    # 重发会在下一轮循环中处理

            # 更新进度
            if self.on_progress:
                acked, total = self.window.get_progress()
                self.on_progress(acked, total)

            # 短暂等待避免CPU占用过高
            if self.window.get_pending_count() > 0:
                time.sleep(0.005)

        return True

    def handle_ack(self, chunk_index: int):
        """处理单个确认"""
        if self.window.mark_acked(chunk_index):
            # 更新状态管理器
            self._sent_set.add(chunk_index)
            self.state_manager.update_sent_chunks(
                self.current_state.file_hash,
                [chunk_index]
            )

            # 更新进度
            if self.on_progress:
                acked, total = self.window.get_progress()
                self.on_progress(acked, total)

            # 检查是否完成
            if self.window.is_complete():
                self._complete_event.set()

    def handle_ack_batch(self, chunk_indices: list):
        """处理批量确认"""
        new_count = self.window.mark_acked_batch(chunk_indices)

        if new_count > 0:
            # 更新状态管理器
            self._sent_set.update(chunk_indices)
            self.state_manager.update_sent_chunks(
                self.current_state.file_hash,
                chunk_indices
            )

            # 更新进度
            if self.on_progress:
                acked, total = self.window.get_progress()
                self.on_progress(acked, total)

            # 检查是否完成
            if self.window.is_complete():
                self._complete_event.set()

    def wait_complete(self, timeout: float = None) -> bool:
        """
        等待发送完成
        Args:
            timeout: 超时时间（None表示无限等待）
        Returns:
            是否完成
        """
        return self._complete_event.wait(timeout)

    def is_complete(self) -> bool:
        """检查是否发送完成"""
        return self.window.is_complete()

    def get_progress(self) -> tuple:
        """获取进度 (已确认块数, 总块数)"""
        return self.window.get_progress()

    def get_needed_chunks(self, received_chunks: list) -> list:
        """
        根据接收方的已接收列表，返回需要发送的块
        """
        if not self.current_state:
            return []

        received_set = set(received_chunks)
        total = self.current_state.total_chunks
        return [i for i in range(total) if i not in received_set]

    def resume_from_chunks(self, received_chunks: list):
        """根据接收方的已接收列表，设置发送位置"""
        self._sent_set = set(received_chunks)
        self.window.mark_acked_batch(received_chunks)

        # 更新状态文件
        if self.current_state:
            self.state_manager.update_sent_chunks(
                self.current_state.file_hash,
                list(received_chunks),
                force_sync=True
            )

    def complete(self):
        """完成发送，清理资源"""
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None

        # 清理临时zip文件
        if self._temp_zip_path:
            try:
                Path(self._temp_zip_path).unlink(missing_ok=True)
            except:
                pass
            self._temp_zip_path = None

        # 清理状态
        if self.current_state:
            self.state_manager.complete_sending(self.current_state.file_hash)

        self.current_state = None
        self._sent_set.clear()

    def cancel(self):
        """取消发送"""
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None

        # 清理临时文件
        if self._temp_zip_path:
            try:
                Path(self._temp_zip_path).unlink(missing_ok=True)
            except:
                pass
            self._temp_zip_path = None

        # 保留状态文件以便后续续传
        self.current_state = None
        self._sent_set.clear()

    def __del__(self):
        """析构时关闭文件句柄"""
        if self.file_handle:
            self.file_handle.close()