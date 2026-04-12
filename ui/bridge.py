"""WebChannel桥接 - 前后端通信"""
import platform
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebChannel import QWebChannel

from config import DEFAULT_DOWNLOAD_DIR, CHUNK_SIZE, DEFAULT_PORT
from network.server import LanShareServer
from network.client import LanShareClient
from network.protocol import MessageBuilder
from network.discovery import RoomBroadcaster, RoomScanner, RoomInfo
from file_handler import FileHandler
from transfer.chunk_receiver import ChunkedFileReceiver
from transfer.state_manager import TransferStateManager

import os
import math
import socket
import threading
import time


class UIBridge(QObject):
    """UI桥接对象 - 暴露给前端调用"""

    # 信号 - 后端推送到前端
    ipChanged = pyqtSignal(str)
    statusChanged = pyqtSignal(str)
    peerChanged = pyqtSignal(str)
    roomDiscovered = pyqtSignal(str, str, str)  # name, ip, pair_code
    roomRemoved = pyqtSignal(str)  # ip
    progressUpdated = pyqtSignal(int, str, str, str, str)  # percent, speed, transferred, total, eta
    recvProgressUpdated = pyqtSignal(int, str, str)  # percent, received, total
    transferCompleted = pyqtSignal(bool, str)
    recvCompleted = pyqtSignal(bool, str)
    logMessage = pyqtSignal(str)
    downloadDirChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._local_ip = ""
        self._connection_status = "未连接"
        self._peer_name = ""
        self._download_dir = DEFAULT_DOWNLOAD_DIR
        self._server = None
        self._client = None
        self._broadcaster = None
        self._scanner = None
        self._chunk_receiver = None
        self._transfer_state_manager = TransferStateManager()
        self._sending = False

    # ==================== 前端调用后端 ====================

    @pyqtSlot()
    def requestLocalIp(self):
        """前端请求本机IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            self._local_ip = ip
            self.ipChanged.emit(ip)
        except Exception:
            self.ipChanged.emit("127.0.0.1")

    @pyqtSlot()
    def createRoom(self):
        """创建房间"""
        import random
        import string

        pair_code = ''.join(random.choices(string.digits, k=6))

        try:
            self._server = LanShareServer()
            self._server.on_connected = lambda name: self._handle_connected(name)
            self._server.on_disconnected = lambda: self._handle_disconnected()
            self._server.on_error = lambda err: self.logMessage.emit(f"服务器错误: {err}")
            self._server.on_file_info = self._on_recv_file_info
            self._server.on_file_data = self._on_recv_file_data
            self._server.on_data_ack = self._on_data_ack
            self._server.start()

            # 设置配对码
            self._server.pair_code = pair_code

            self._connection_status = "等待连接"
            self.statusChanged.emit(self._connection_status)

            self._start_broadcast(pair_code)

            self.logMessage.emit(f"已创建房间，配对码: {pair_code}")
        except Exception as e:
            self.logMessage.emit(f"创建房间失败: {e}")
            self._server = None

    @pyqtSlot(str, str)
    def connectToRoom(self, ip: str, pair_code: str):
        """连接到房间"""
        self._stop_discovery()
        self._connection_status = "连接中"
        self.statusChanged.emit(self._connection_status)

        def connect_thread():
            try:
                client = LanShareClient()
                client.on_connected = lambda name: self._handle_connected(name)
                client.on_disconnected = lambda: self._handle_disconnected()
                client.on_error = lambda err: self.logMessage.emit(f"连接错误: {err}")
                client.on_file_info = self._on_recv_file_info
                client.on_file_data = self._on_recv_file_data
                client.on_data_ack = self._on_data_ack

                success = client.connect(ip, pair_code)
                if success:
                    self._client = client
                else:
                    self._connection_status = "未连接"
                    self.statusChanged.emit(self._connection_status)
                    self.logMessage.emit("连接失败")
            except Exception as e:
                self._connection_status = "未连接"
                self.statusChanged.emit(self._connection_status)
                self.logMessage.emit(f"连接失败: {e}")

        threading.Thread(target=connect_thread, daemon=True).start()

    @pyqtSlot()
    def disconnect(self):
        """断开连接"""
        self._stop_sending()
        self._stop_broadcast()
        self._stop_discovery()

        if self._server:
            self._server.stop()
            self._server = None
        if self._client:
            self._client.disconnect()
            self._client = None

        self._connection_status = "未连接"
        self._peer_name = ""
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit("-")
        self.logMessage.emit("已断开连接")

    @pyqtSlot()
    def startDiscovery(self):
        """开始发现房间"""
        self._stop_discovery()

        def on_room_found(room: RoomInfo):
            self.roomDiscovered.emit(room.hostname, room.ip, room.pair_code)

        def on_room_expired(room: RoomInfo):
            self.roomRemoved.emit(room.ip)

        self._scanner = RoomScanner(
            on_room_found=on_room_found,
            on_room_expired=on_room_expired
        )
        self._scanner.start()
        self.logMessage.emit("开始扫描局域网房间...")

    @pyqtSlot()
    def cancelCreateRoom(self):
        """取消创建房间"""
        self._stop_broadcast()
        if self._server:
            self._server.stop()
            self._server = None

        self._connection_status = "未连接"
        self.statusChanged.emit(self._connection_status)
        self.logMessage.emit("已取消创建房间")

    @pyqtSlot(list)
    def sendFiles(self, file_paths: list):
        """发送文件"""
        transport = self._get_transport()
        if not transport:
            self.logMessage.emit("未连接到对方设备")
            return

        if self._sending:
            self.logMessage.emit("正在发送中，请等待完成")
            return

        self._sending = True

        def send_thread():
            try:
                total_size = sum(os.path.getsize(f) for f in file_paths)
                total_sent = 0
                start_time = time.time()

                for file_path in file_paths:
                    path = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    file_hash = FileHandler.get_file_hash(file_path)

                    # 发送 FILE_INFO
                    transport.send(MessageBuilder.file_info(path, file_size, file_hash))
                    time.sleep(0.05)

                    # 发送 FILE_DATA 分块
                    with open(file_path, 'rb') as f:
                        chunk_index = 0
                        while True:
                            data = f.read(CHUNK_SIZE)
                            if not data:
                                break
                            transport.send(MessageBuilder.file_data(chunk_index, data))
                            chunk_index += 1
                            total_sent += len(data)

                            # 计算速度
                            elapsed = time.time() - start_time
                            speed = total_sent / elapsed if elapsed > 0 else 0
                            percent = int(total_sent / total_size * 100)
                            eta = self._format_time((total_size - total_sent) / speed) if speed > 0 else "--"

                            self.progressUpdated.emit(
                                percent,
                                self._format_size(speed) + "/s",
                                self._format_size(total_sent),
                                self._format_size(total_size),
                                eta
                            )

                    self.logMessage.emit(f"已发送: {path}")

                self._sending = False
                self.transferCompleted.emit(True, f"成功发送 {len(file_paths)} 个文件")
            except Exception as e:
                self._sending = False
                self.transferCompleted.emit(False, f"发送失败: {e}")

        threading.Thread(target=send_thread, daemon=True).start()
        self.logMessage.emit(f"开始发送 {len(file_paths)} 个文件")

    @pyqtSlot(result=str)
    def getDownloadDir(self):
        """获取下载目录"""
        return self._download_dir

    @pyqtSlot()
    def openDownloadDir(self):
        """打开下载目录"""
        import subprocess
        try:
            subprocess.Popen(f'explorer "{self._download_dir}"')
        except Exception as e:
            self.logMessage.emit(f"打开目录失败: {e}")

    @pyqtSlot(result=str)
    def changeDownloadDir(self):
        """更改下载目录"""
        from PyQt5.QtWidgets import QFileDialog

        dir_path = QFileDialog.getExistingDirectory(
            None,
            "选择下载目录",
            self._download_dir
        )

        if dir_path:
            self._download_dir = dir_path
            self.downloadDirChanged.emit(dir_path)
            self.logMessage.emit(f"下载目录已更改为: {dir_path}")
            return dir_path
        return ""

    # ==================== 内部回调 ====================

    def _handle_connected(self, peer_name):
        """连接成功"""
        self._connection_status = "已连接"
        self._peer_name = peer_name
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit(peer_name)
        self.logMessage.emit(f"已连接到: {peer_name}")

    def _handle_disconnected(self):
        """断开连接"""
        self._stop_sending()
        self._connection_status = "未连接"
        self._peer_name = ""
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit("-")
        self.logMessage.emit("连接已断开")
        self._server = None
        self._client = None

    def _on_recv_file_info(self, msg_data: dict):
        """收到文件信息"""
        filename = msg_data.get('filename', 'unknown')
        file_size = msg_data.get('filesize', 0)
        file_hash = msg_data.get('hash', '')

        self.logMessage.emit(f"正在接收: {filename} ({self._format_size(file_size)})")

        try:
            receiver = ChunkedFileReceiver(
                state_manager=self._transfer_state_manager,
                download_dir=os.path.dirname(self._download_dir) if os.path.isfile(self._download_dir) else self._download_dir,
                on_progress=lambda r, t: self.recvProgressUpdated.emit(
                    int(r / t * 100) if t > 0 else 0,
                    self._format_size(r * CHUNK_SIZE),
                    self._format_size(t * CHUNK_SIZE)
                ),
                on_send_ack=lambda acks: self._send_recv_ack(acks)
            )
            receiver.start_receive(filename, file_size, file_hash, chunk_size=CHUNK_SIZE)
            self._chunk_receiver = receiver
        except Exception as e:
            self.logMessage.emit(f"准备接收失败: {e}")

    def _on_recv_file_data(self, data: bytes):
        """收到文件数据"""
        if not self._chunk_receiver:
            return
        try:
            chunk_index, actual_data = MessageBuilder.decode_file_data(data)
            self._chunk_receiver.write_chunk(chunk_index, actual_data)

            if self._chunk_receiver.is_complete():
                result = self._chunk_receiver.complete()
                if result:
                    self.recvCompleted.emit(True, f"已保存: {result}")
                else:
                    self.recvCompleted.emit(False, "接收完成但保存失败")
                self._chunk_receiver = None
        except Exception as e:
            self.logMessage.emit(f"接收数据错误: {e}")

    def _on_data_ack(self, msg_data: dict):
        """收到数据确认"""
        pass

    def _send_recv_ack(self, chunk_indices: list):
        """发送接收确认"""
        transport = self._get_transport()
        if transport:
            try:
                transport.send(MessageBuilder.data_ack_batch(chunk_indices))
            except Exception:
                pass

    def _get_transport(self):
        """获取当前传输通道"""
        if self._client and self._client.connected:
            return self._client
        if self._server and self._server.connected:
            return self._server
        return None

    def _stop_sending(self):
        """停止发送"""
        self._sending = False
        if self._chunk_receiver:
            try:
                self._chunk_receiver.cancel()
            except Exception:
                pass
            self._chunk_receiver = None

    def _start_broadcast(self, pair_code: str):
        """开始广播房间"""
        self._stop_broadcast()
        hostname = platform.node()
        self._broadcaster = RoomBroadcaster(
            hostname=hostname,
            pair_code=pair_code,
            port=DEFAULT_PORT
        )
        self._broadcaster.start()

    def _stop_broadcast(self):
        if self._broadcaster:
            self._broadcaster.stop()
            self._broadcaster = None

    def _stop_discovery(self):
        if self._scanner:
            self._scanner.stop()
            self._scanner = None

    @staticmethod
    def _format_size(bytes_val: float) -> str:
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
    def _format_time(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}秒"
        elif seconds < 3600:
            return f"{int(seconds / 60)}分钟"
        else:
            return f"{seconds / 3600:.1f}小时"

    def cleanup(self):
        """清理资源"""
        self._stop_sending()
        self._stop_broadcast()
        self._stop_discovery()
        if self._server:
            self._server.stop()
        if self._client:
            self._client.disconnect()
