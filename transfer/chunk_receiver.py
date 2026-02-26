"""内存安全的分块文件接收器"""
import os
from pathlib import Path
from typing import Optional, Callable, Set

from config import LAN_SHARE_DIR, CHUNK_SIZE
from transfer.state_manager import TransferStateManager, ReceivingState


class ChunkedFileReceiver:
    """分块文件接收器 - 内存安全，边接收边写入磁盘"""

    def __init__(self, state_manager: TransferStateManager = None,
                 download_dir: Path = None,
                 on_progress: Callable[[int, int], None] = None):
        self.state_manager = state_manager or TransferStateManager()
        self.download_dir = download_dir or LAN_SHARE_DIR.parent
        self.on_progress = on_progress

        self.current_state: Optional[ReceivingState] = None
        self.file_handle = None
        self._received_set: Set[int] = set()

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
            return True  # 已接收，跳过

        try:
            # 计算写入位置
            offset = chunk_index * self.current_state.chunk_size

            # 随机位置写入
            self.file_handle.seek(offset)
            self.file_handle.write(data)

            # 记录已接收
            self._received_set.add(chunk_index)

            # 更新状态
            self.state_manager.update_received_chunks(
                self.current_state.file_hash,
                [chunk_index]
            )

            # 回调进度
            if self.on_progress:
                total = self.current_state.total_chunks
                received = len(self._received_set)
                self.on_progress(received, total)

            return True

        except Exception as e:
            print(f"写入块 {chunk_index} 失败: {e}")
            return False

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

        # 关闭文件句柄
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None

        # 检查是否接收完整
        if not self.is_complete():
            print("接收不完整，无法完成")
            return None

        try:
            # 获取临时文件路径
            temp_path = self.state_manager.get_temp_file_path(self.current_state.file_hash)

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
