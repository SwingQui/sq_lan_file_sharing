"""分块文件发送器"""
import os
from pathlib import Path
from typing import Optional, Callable, Set, Tuple

from config import CHUNK_SIZE, MAX_RETRY, ACK_TIMEOUT
from transfer.state_manager import TransferStateManager, SendingState
from file_handler import FileHandler


class ChunkedFileSender:
    """分块文件发送器 - 支持断点续传和状态持久化"""

    def __init__(self, state_manager: TransferStateManager = None,
                 on_progress: Callable[[int, int], None] = None,
                 on_chunk_sent: Callable[[int, bytes], bool] = None):
        """
        Args:
            state_manager: 状态管理器
            on_progress: 进度回调 (已发送块数, 总块数)
            on_chunk_sent: 块发送回调 (块索引, 块数据) -> 是否成功
        """
        self.state_manager = state_manager or TransferStateManager()
        self.on_progress = on_progress
        self.on_chunk_sent = on_chunk_sent

        self.current_state: Optional[SendingState] = None
        self.file_handle = None
        self._sent_set: Set[int] = set()
        self._current_index: int = 0
        self._is_folder: bool = False
        self._temp_zip_path: Optional[str] = None

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
            self._current_index = 0
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
            self._current_index = 0

        # 打开文件
        self.file_handle = open(send_path, 'rb')

        return filename, file_size, file_hash, self._is_folder

    def get_next_chunk(self) -> Optional[Tuple[int, bytes]]:
        """
        获取下一个未发送的数据块
        Returns:
            (块索引, 块数据) 或 None（已全部发送）
        """
        if not self.current_state or not self.file_handle:
            return None

        total = self.current_state.total_chunks

        # 找到下一个未发送的块
        while self._current_index < total:
            if self._current_index not in self._sent_set:
                break
            self._current_index += 1

        if self._current_index >= total:
            return None

        # 读取数据
        offset = self._current_index * self.current_state.chunk_size
        self.file_handle.seek(offset)
        data = self.file_handle.read(self.current_state.chunk_size)

        if not data:
            return None

        chunk_index = self._current_index
        self._current_index += 1

        return (chunk_index, data)

    def get_chunks_to_send(self, max_chunks: int = None) -> list:
        """
        获取需要发送的块索引列表
        Args:
            max_chunks: 最多返回多少个块索引（None表示全部）
        Returns:
            块索引列表
        """
        if not self.current_state:
            return []

        total = self.current_state.total_chunks
        missing = []

        for i in range(total):
            if i not in self._sent_set:
                missing.append(i)
                if max_chunks and len(missing) >= max_chunks:
                    break

        return missing

    def mark_chunk_sent(self, chunk_index: int):
        """标记块已发送"""
        if not self.current_state:
            return

        self._sent_set.add(chunk_index)
        self.state_manager.update_sent_chunks(
            self.current_state.file_hash,
            [chunk_index]
        )

        # 回调进度
        if self.on_progress:
            total = self.current_state.total_chunks
            sent = len(self._sent_set)
            self.on_progress(sent, total)

    def send_chunk(self, chunk_index: int, data: bytes) -> bool:
        """
        发送单个块（通过回调函数）
        Args:
            chunk_index: 块索引
            data: 块数据
        Returns:
            是否发送成功
        """
        if self.on_chunk_sent:
            success = self.on_chunk_sent(chunk_index, data)
            if success:
                self.mark_chunk_sent(chunk_index)
            return success
        return False

    def send_all_chunks(self) -> bool:
        """
        发送所有未发送的块
        Returns:
            是否全部发送成功
        """
        while True:
            chunk = self.get_next_chunk()
            if chunk is None:
                return True

            chunk_index, data = chunk

            # 重试机制
            for attempt in range(MAX_RETRY):
                if self.send_chunk(chunk_index, data):
                    break
                if attempt == MAX_RETRY - 1:
                    return False

        return True

    def get_needed_chunks(self, received_chunks: list) -> list:
        """
        根据接收方的已接收列表，返回需要发送的块
        Args:
            received_chunks: 接收方已接收的块索引列表
        Returns:
            需要发送的块索引列表
        """
        if not self.current_state:
            return []

        received_set = set(received_chunks)
        total = self.current_state.total_chunks
        return [i for i in range(total) if i not in received_set]

    def resume_from_chunks(self, received_chunks: list):
        """
        根据接收方的已接收列表，设置发送位置
        Args:
            received_chunks: 接收方已接收的块索引列表
        """
        self._sent_set = set(received_chunks)
        self._current_index = 0

        # 更新状态文件
        if self.current_state:
            self.state_manager.update_sent_chunks(
                self.current_state.file_hash,
                list(received_chunks),
                force_sync=True
            )

    def is_complete(self) -> bool:
        """检查是否发送完成"""
        if not self.current_state:
            return False
        return len(self._sent_set) >= self.current_state.total_chunks

    def get_progress(self) -> tuple:
        """获取进度 (已发送块数, 总块数)"""
        if not self.current_state:
            return (0, 0)
        return (len(self._sent_set), self.current_state.total_chunks)

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
        self._current_index = 0

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
