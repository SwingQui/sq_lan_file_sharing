"""传输会话管理 - 封装传输协调逻辑，实现UI层解耦"""
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Callable, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import CHUNK_SIZE, MAX_CONCURRENT_FILES
from network.protocol import MessageBuilder
from transfer.chunk_sender import ChunkedFileSender
from transfer.chunk_receiver import ChunkedFileReceiver
from transfer.state_manager import TransferStateManager


class TransferSession:
    """
    传输会话管理器

    封装所有传输协调逻辑，包括:
    - 多文件并发发送（滑动窗口）
    - 文件接收管理（按file_hash路由）
    - ACK处理（按file_hash路由）
    - 断点续传支持

    不创建传输通道，由UI层注入 transport (server/client)
    """

    def __init__(
        self,
        download_dir: Path,
        state_manager: TransferStateManager = None,
        # UI回调（纯Python callable，不依赖Qt）
        on_send_progress: Callable[[int, int, int, str, str], None] = None,
        on_send_file_done: Callable[[bool, str, int], None] = None,
        on_send_completed: Callable[[bool, str], None] = None,
        on_recv_progress: Callable[[str, int, str, str], None] = None,
        on_recv_file_info: Callable[[str, str, int], None] = None,
        on_recv_completed: Callable[[str, bool, str], None] = None,
        on_log: Callable[[str], None] = None,
    ):
        """
        Args:
            download_dir: 下载目录
            state_manager: 传输状态管理器
            on_send_progress: 发送进度回调 (percent, filename, transferred_str, total_str)
            on_send_file_done: 单个文件完成回调 (success, filename, file_size)
            on_send_completed: 全部发送完成回调 (success, message)
            on_recv_progress: 接收进度回调 (percent, received_str, total_str)
            on_recv_file_info: 收到文件信息回调 (filename, size)
            on_recv_completed: 接收完成回调 (success, message)
            on_log: 日志回调 (message)
        """
        self.download_dir = download_dir
        self.state_manager = state_manager or TransferStateManager()

        # UI回调
        self.on_send_progress = on_send_progress
        self.on_send_file_done = on_send_file_done
        self.on_send_completed = on_send_completed
        self.on_recv_progress = on_recv_progress
        self.on_recv_file_info = on_recv_file_info
        self.on_recv_completed = on_recv_completed
        self.on_log = on_log

        # 传输通道（由UI层通过set_transport注入）
        self._transport = None

        # 活跃的发送器和接收器
        self._active_senders: Dict[str, ChunkedFileSender] = {}
        self._active_receivers: Dict[str, ChunkedFileReceiver] = {}

        # 线程安全锁
        self._senders_lock = threading.Lock()
        self._receivers_lock = threading.Lock()

        # 发送状态
        self._sending = False

        # 发送聚合进度追踪
        self._total_send_size = 0
        self._completed_send_bytes = 0
        self._total_send_files = 0
        self._completed_send_files = 0

    # ==================== 传输管理 ====================

    def set_transport(self, transport):
        """设置传输通道（UI层连接成功后调用）"""
        self._transport = transport

    @property
    def is_sending(self) -> bool:
        """是否正在发送"""
        return self._sending

    def stop_all(self):
        """停止所有传输（保留临时文件以便续传）"""
        self._sending = False

        with self._senders_lock:
            for sender in self._active_senders.values():
                try:
                    sender.cancel()
                except Exception:
                    pass
            self._active_senders.clear()

        with self._receivers_lock:
            for receiver in list(self._active_receivers.values()):
                try:
                    receiver.cancel()
                except Exception:
                    pass
            self._active_receivers.clear()

    def cleanup_all_state(self):
        """清理所有传输状态和临时文件（最终断开时调用）"""
        self._sending = False

        with self._senders_lock:
            for sender in self._active_senders.values():
                try:
                    sender.cancel()
                except Exception:
                    pass
            self._active_senders.clear()

        with self._receivers_lock:
            for receiver in list(self._active_receivers.values()):
                try:
                    receiver.cleanup()
                except Exception:
                    pass
            self._active_receivers.clear()

    def send_files(self, file_paths: List[str]):
        if not self._transport:
            self._log("未连接到对方设备")
            return

        if self._sending:
            self._log("正在发送中，请等待完成")
            return

        if not file_paths:
            return

        self._sending = True
        total_size = sum(self._get_path_size(f) for f in file_paths)
        total_count = len(file_paths)
        self._log(f"开始发送 {total_count} 个文件 (并发数: {MAX_CONCURRENT_FILES})...")

        self._total_send_size = total_size
        self._completed_send_bytes = 0
        self._total_send_files = total_count
        self._completed_send_files = 0

        if self.on_send_progress:
            self.on_send_progress(0, 0, total_count, "0 B", self._format_size(total_size))

        def send_one(file_path: str) -> tuple:
            sender = ChunkedFileSender(state_manager=self.state_manager)

            filename, file_size, file_hash, is_folder = sender.prepare(file_path)

            with self._senders_lock:
                self._active_senders[file_hash] = sender

            sender.on_send_chunk = lambda idx, data, fh=file_hash: (
                self._transport.send(MessageBuilder.file_data(idx, data, fh))
                if self._transport else False
            )

            sender.on_progress = lambda acked, total: self._emit_aggregate_send_progress()

            self._transport.send(MessageBuilder.file_info(
                filename, file_size, file_hash, is_folder
            ))
            time.sleep(0.05)

            success = sender.send_with_window()
            sender.complete()

            with self._senders_lock:
                self._active_senders.pop(file_hash, None)
                if success:
                    self._completed_send_bytes += file_size
                    self._completed_send_files += 1

            self._emit_aggregate_send_progress()

            return (success, filename, file_size)

        def coordinator():
            has_failed = False
            completed = 0

            try:
                if self._transport and hasattr(self._transport, 'set_transfer_mode'):
                    self._transport.set_transfer_mode(True)

                with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_FILES) as executor:
                    future_to_path = {
                        executor.submit(send_one, fp): fp for fp in file_paths
                    }

                    for future in as_completed(future_to_path):
                        try:
                            success, filename, file_size = future.result()
                            if success:
                                completed += 1
                                self._log(f"已发送: {filename}")
                            else:
                                has_failed = True
                                self._log(f"发送失败: {filename}")

                            if self.on_send_file_done:
                                self.on_send_file_done(success, filename, file_size)

                        except Exception as e:
                            has_failed = True
                            self._log(f"发送异常: {e}")

                if has_failed:
                    self._send_completed(
                        False, f"部分文件发送失败 ({completed}/{len(file_paths)})"
                    )
                else:
                    self._send_completed(
                        True, f"成功发送 {len(file_paths)} 个文件"
                    )

            except Exception as e:
                self._send_completed(False, f"发送失败: {e}")
            finally:
                if self._transport and hasattr(self._transport, 'set_transfer_mode'):
                    self._transport.set_transfer_mode(False)
                self._sending = False

        threading.Thread(target=coordinator, daemon=True).start()

    def _emit_aggregate_send_progress(self):
        """聚合所有活跃sender的进度，统一发射"""
        if not self.on_send_progress:
            return

        active_bytes = 0
        with self._senders_lock:
            for s in self._active_senders.values():
                acked, total = s.get_progress()
                active_bytes += acked * CHUNK_SIZE
            completed = self._completed_send_bytes
            files_done = self._completed_send_files

        total_acked = completed + active_bytes
        total_size = self._total_send_size
        pct = min(int(total_acked / total_size * 100), 100) if total_size > 0 else 0

        self.on_send_progress(
            pct, files_done, self._total_send_files,
            self._format_size(total_acked),
            self._format_size(total_size)
        )

    # ==================== 网络回调（注册到server/client） ====================

    def handle_file_info(self, msg_data: dict):
        """
        收到文件信息 - 创建Receiver

        由UI层注册到server/client的on_file_info回调
        """
        filename = msg_data.get('filename', 'unknown')
        file_size = msg_data.get('filesize', 0)
        file_hash = msg_data.get('hash', '')
        chunk_size = CHUNK_SIZE

        self._recv_file_info(file_hash, filename, file_size)
        self._log(f"正在接收: {filename} ({self._format_size(file_size)})")

        try:
            receiver = ChunkedFileReceiver(
                state_manager=self.state_manager,
                download_dir=self.download_dir,
                on_progress=lambda r, t, fh=file_hash: self._recv_progress(
                    fh,
                    int(r / t * 100) if t > 0 else 0,
                    self._format_size(r * chunk_size),
                    self._format_size(t * chunk_size)
                ),
                on_send_ack=lambda acks, fh=file_hash: self._send_recv_ack(fh, acks)
            )
            receiver.start_receive(filename, file_size, file_hash, chunk_size=chunk_size)

            with self._receivers_lock:
                self._active_receivers[file_hash] = receiver

            # 断点续传：通知发送方已有哪些块
            if receiver._received_set:
                self._log(f"断点续传: 已有 {len(receiver._received_set)} 个块，跳过")
                self._send_recv_ack(file_hash, sorted(receiver._received_set))

        except Exception as e:
            self._log(f"准备接收失败: {e}")

    def handle_file_data(self, data: bytes):
        """
        收到文件数据 - 按file_hash路由到对应Receiver

        由UI层注册到server/client的on_file_data回调
        """
        try:
            file_hash, chunk_index, actual_data = MessageBuilder.decode_file_data(data)

            with self._receivers_lock:
                receiver = self._active_receivers.get(file_hash)

            if not receiver:
                return

            receiver.write_chunk(chunk_index, actual_data)

            # 检查是否完成
            if receiver.is_complete():
                result = receiver.complete()
                with self._receivers_lock:
                    self._active_receivers.pop(file_hash, None)
                    remaining = len(self._active_receivers)

                if result:
                    self._recv_completed(file_hash, True, f"已保存: {result}")
                else:
                    self._recv_completed(file_hash, False, "接收完成但保存失败")

        except Exception as e:
            self._log(f"接收数据错误: {e}")

    def handle_data_ack(self, msg_data: dict):
        """
        收到数据确认 - 按file_hash路由到对应Sender

        由UI层注册到server/client的on_data_ack回调
        """
        file_hash = msg_data.get('file_hash', '')
        chunk_indices = msg_data.get('chunk_indices', [])

        if not chunk_indices:
            return

        with self._senders_lock:
            sender = self._active_senders.get(file_hash)

        if sender:
            sender.handle_ack_batch(chunk_indices)

    # ==================== 内部方法 ====================

    def _send_recv_ack(self, file_hash: str, chunk_indices: list):
        """发送接收确认（带file_hash）"""
        if self._transport:
            try:
                self._transport.send(MessageBuilder.data_ack_batch(chunk_indices, file_hash))
            except Exception:
                pass

    def _log(self, message: str):
        """发送日志"""
        if self.on_log:
            self.on_log(message)

    def _send_completed(self, success: bool, message: str):
        """发送完成回调"""
        if self.on_send_completed:
            self.on_send_completed(success, message)

    def _recv_progress(self, file_hash: str, percent: int, received: str, total: str):
        """接收进度回调"""
        if self.on_recv_progress:
            self.on_recv_progress(file_hash, percent, received, total)

    def _recv_file_info(self, file_hash: str, filename: str, size: int):
        """接收文件信息回调"""
        if self.on_recv_file_info:
            self.on_recv_file_info(file_hash, filename, size)

    def _recv_completed(self, file_hash: str, success: bool, message: str):
        """接收完成回调"""
        if self.on_recv_completed:
            self.on_recv_completed(file_hash, success, message)

    @staticmethod
    def _format_size(bytes_val: float) -> str:
        """格式化文件大小"""
        import math
        if bytes_val <= 0:
            return "0 B"
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        i = min(int(math.log(bytes_val) / math.log(1024)), len(units) - 1)
        size = bytes_val / (1024 ** i)
        if size >= 100:
            return f"{size:.0f} {units[i]}"
        elif size >= 10:
            return f"{size:.1f} {units[i]}"
        else:
            return f"{size:.2f} {units[i]}"

    @staticmethod
    def _get_path_size(path_str: str) -> int:
        """获取路径大小（文件夹递归计算）"""
        p = Path(path_str)
        if p.is_file():
            return p.stat().st_size
        elif p.is_dir():
            return sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
        return 0
