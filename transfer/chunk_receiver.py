"""内存安全的分块文件接收器 - 支持批量确认"""
import os
import threading
import time
from pathlib import Path
from typing import Optional, Callable, Set, List

from config import LAN_SHARE_DIR, CHUNK_SIZE, ACK_BATCH_SIZE
from transfer.state_manager import TransferStateManager, ReceivingState
from file_handler import FileHandler


class ChunkedFileReceiver:
    """分块文件接收器 - 内存安全，边接收边写入磁盘，支持批量确认"""

    def __init__(self, state_manager: TransferStateManager = None,
                 download_dir: Path = None,
                 on_progress: Callable[[int, int], None] = None,
                 on_send_ack: Callable[[list], None] = None):
        """
        Args:
            state_manager: 状态管理器
            download_dir: 下载目录
            on_progress: 进度回调 (已接收块数, 总块数)
            on_send_ack: 发送确认回调 (已接收块索引列表)
        """
        self.state_manager = state_manager or TransferStateManager()
        self.download_dir = download_dir or LAN_SHARE_DIR.parent
        self.on_progress = on_progress
        self.on_send_ack = on_send_ack

        self.current_state: Optional[ReceivingState] = None
        self.file_handle = None
        self._received_set: Set[int] = set()

        # 批量确认缓冲
        self._pending_acks: List[int] = []
        self._ack_lock = threading.Lock()
        self._last_ack_time: float = 0
        self._ack_interval: float = 0.1  # 100ms 发送一次批量确认

    def start_receive(self, file_name: str, file_size: int, file_hash: str,
                      sender_device_id: str = '', chunk_size: int = CHUNK_SIZE) -> bool:
        """
        开始接收文件
        Args:
            file_name: 文件名
            file_size: 文件大小
            file_hash: 文件哈希
            sender_device_id: 发送方设备ID
            chunk_size: 块大小
        Returns:
            是否成功开始接收
        """
        # 检查是否有未完成的接收
        existing_state = self.state_manager.load_receiving_state(file_hash)
        if existing_state:
            self.current_state = existing_state
            self._received_set = set(existing_state.received_chunks)
        else:
            # 创建新的接收状态
            self.current_state = self.state_manager.create_receiving_state(
                file_name=file_name,
                file_size=file_size,
                file_hash=file_hash,
                sender_device_id=sender_device_id
            )
            self._received_set = set()

        # 确保目录存在
        self.state_manager.receiving_dir.mkdir(parents=True, exist_ok=True)

        # 打开/创建临时文件
        temp_path = self.state_manager.get_temp_file_path(file_hash)

        if not temp_path.exists():
            # 创建稀疏文件（预分配空间但不占实际磁盘）
            with open(temp_path, 'wb') as f:
                f.truncate(file_size)
        elif temp_path.stat().st_size != file_size:
            # 文件大小不匹配，重新创建
            with open(temp_path, 'wb') as f:
                f.truncate(file_size)

        # 打开文件用于随机写入
        self.file_handle = open(temp_path, 'r+b')

        # 清空待确认列表
        self._pending_acks.clear()
        self._last_ack_time = time.time()

        return True

    def write_chunk(self, chunk_index: int, data: bytes) -> bool:
        """
        写入一个数据块
        Args:
            chunk_index: 块索引（从0开始）
            data: 块数据
        Returns:
            是否写入成功
        """
        if not self.current_state or not self.file_handle:
            return False

        # 检查是否已接收
        if chunk_index in self._received_set:
            # 已接收，不重复发送ACK（减少网络开销）
            return True

        try:
            # 计算写入位置
            offset = chunk_index * self.current_state.chunk_size

            # 随机位置写入
            self.file_handle.seek(offset)
            self.file_handle.write(data)

            # 不在每块写入后fsync，这会严重影响性能
            # 数据会由操作系统缓存，传输完成后再fsync

            # 记录已接收
            self._received_set.add(chunk_index)

            # 添加到待确认列表
            self._add_pending_ack(chunk_index)

            # 回调进度
            if self.on_progress:
                total = self.current_state.total_chunks
                received = len(self._received_set)
                self.on_progress(received, total)

            return True

        except Exception as e:
            print(f"写入块 {chunk_index} 失败: {e}")
            return False

    def _add_pending_ack(self, chunk_index: int):
        """添加待发送的确认"""
        with self._ack_lock:
            self._pending_acks.append(chunk_index)

            # 检查是否需要发送确认
            should_send = (
                len(self._pending_acks) >= ACK_BATCH_SIZE or
                (time.time() - self._last_ack_time) >= self._ack_interval
            )

            if should_send and self.on_send_ack:
                acks = self._pending_acks.copy()
                self._pending_acks.clear()
                self._last_ack_time = time.time()
                # 在锁外调用回调，避免死锁
                self._send_acks_async(acks)

    def _send_acks_async(self, acks: list):
        """异步发送确认"""
        if self.on_send_ack:
            try:
                self.on_send_ack(acks)
            except Exception as e:
                print(f"发送确认失败: {e}")

    def flush_acks(self):
        """强制发送所有待发送的确认"""
        with self._ack_lock:
            if self._pending_acks and self.on_send_ack:
                acks = self._pending_acks.copy()
                self._pending_acks.clear()
                self._last_ack_time = time.time()
                self._send_acks_async(acks)

    def get_missing_chunks(self) -> list:
        """获取未接收的块索引列表"""
        if not self.current_state:
            return []

        all_chunks = set(range(self.current_state.total_chunks))
        missing = all_chunks - self._received_set
        return sorted(missing)

    def is_complete(self) -> bool:
        """检查是否接收完成"""
        if not self.current_state:
            return False
        return len(self._received_set) >= self.current_state.total_chunks

    def get_progress(self) -> tuple:
        """获取进度 (已接收块数, 总块数)"""
        if not self.current_state:
            return (0, 0)
        return (len(self._received_set), self.current_state.total_chunks)

    def complete(self) -> Optional[str]:
        """
        完成接收，将临时文件重命名为正式文件
        Returns:
            最终文件路径，失败返回 None
        """
        if not self.current_state:
            return None

        # 发送最后的确认
        self.flush_acks()

        # 确保所有数据写入磁盘后再关闭
        if self.file_handle:
            try:
                self.file_handle.flush()
                os.fsync(self.file_handle.fileno())
            except:
                pass
            self.file_handle.close()
            self.file_handle = None

        # 检查是否接收完整
        if not self.is_complete():
            print("接收不完整，无法完成")
            return None

        try:
            # 获取临时文件路径
            temp_path = self.state_manager.get_temp_file_path(self.current_state.file_hash)

            # ===== 哈希验证（无损传输关键） =====
            expected_hash = self.current_state.file_hash
            actual_hash = FileHandler.get_file_hash(str(temp_path))

            if actual_hash != expected_hash:
                print(f"文件哈希不匹配！预期: {expected_hash}, 实际: {actual_hash}")
                # 删除损坏的文件
                temp_path.unlink(missing_ok=True)
                self.state_manager.complete_receiving(self.current_state.file_hash)
                self.current_state = None
                self._received_set.clear()
                return None

            print(f"文件哈希验证通过: {actual_hash[:16]}...")

            # 目标路径
            final_path = self.download_dir / self.current_state.file_name

            # 处理重名
            if final_path.exists():
                stem = final_path.stem
                suffix = final_path.suffix
                counter = 1
                while final_path.exists():
                    final_path = final_path.parent / f"{stem} ({counter}){suffix}"
                    counter += 1

            # 重命名
            temp_path.rename(final_path)

            # 清理状态
            self.state_manager.complete_receiving(self.current_state.file_hash)

            result_path = str(final_path)
            self.current_state = None
            self._received_set.clear()

            return result_path

        except Exception as e:
            print(f"完成接收失败: {e}")
            return None

    def cancel(self):
        """取消接收"""
        # 发送最后的确认
        self.flush_acks()

        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None

        # 删除临时文件
        if self.current_state:
            temp_path = self.state_manager.get_temp_file_path(self.current_state.file_hash)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except:
                    pass
            self.state_manager.complete_receiving(self.current_state.file_hash)

        self.current_state = None
        self._received_set.clear()

    def __del__(self):
        """析构时关闭文件句柄"""
        if self.file_handle:
            self.file_handle.close()