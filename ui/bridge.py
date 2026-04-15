"""WebChannel桥接 - 前后端通信"""
import os
import threading
from pathlib import Path

import platform
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebChannel import QWebChannel

from config import DEFAULT_DOWNLOAD_DIR, CHUNK_SIZE, DEFAULT_PORT
from network.server import LanShareServer
from network.client import LanShareClient
from network.discovery import RoomBroadcaster, RoomScanner, RoomInfo
from transfer.session import TransferSession
from transfer.state_manager import TransferStateManager
from utils import get_local_ip, format_size


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
        self._transfer_state_manager = TransferStateManager()

        # 创建 TransferSession
        self._session = TransferSession(
            download_dir=Path(self._download_dir),
            state_manager=self._transfer_state_manager,
            on_send_progress=self._on_send_progress,
            on_send_file_done=self._on_send_file_done,
            on_send_completed=self._on_send_completed,
            on_recv_progress=self._on_recv_progress,
            on_recv_file_info=self._on_recv_file_info,
            on_recv_completed=self._on_recv_completed,
            on_log=lambda m: self.logMessage.emit(m),
        )

    # ==================== UI 回调方法 ====================

    def _on_send_progress(self, percent: int, done_files: int, total_files: int, transferred: str, total: str):
        """发送进度回调"""
        self.progressUpdated.emit(percent, "", transferred, total, "")

    def _on_send_file_done(self, success: bool, filename: str, file_size: int):
        """单个文件完成回调"""
        pass

    def _on_send_completed(self, success: bool, message: str):
        """发送完成回调"""
        self.transferCompleted.emit(success, message)

    def _on_recv_progress(self, file_hash: str, percent: int, received: str, total: str):
        """接收进度回调"""
        self.recvProgressUpdated.emit(percent, received, total)

    def _on_recv_file_info(self, file_hash: str, filename: str, size: int):
        """接收文件信息回调"""
        pass

    def _on_recv_completed(self, file_hash: str, success: bool, message: str):
        """接收完成回调"""
        self.recvCompleted.emit(success, message)

    # ==================== 前端调用后端 ====================

    @pyqtSlot()
    def requestLocalIp(self):
        """前端请求本机IP"""
        ip = get_local_ip()
        self._local_ip = ip
        self.ipChanged.emit(ip)

    @pyqtSlot()
    def createRoom(self):
        """创建房间"""
        import random
        import string

        pair_code = ''.join(random.choices(string.digits, k=6))

        try:
            self._server = LanShareServer()
            self._server.on_connected = lambda name: self._handle_connected(name)
            self._server.on_disconnected = lambda intentional=False: self._handle_disconnected(intentional)
            self._server.on_error = lambda err: self.logMessage.emit(f"服务器错误: {err}")
            self._server.on_file_info = self._session.handle_file_info
            self._server.on_file_data = self._session.handle_file_data
            self._server.on_data_ack = self._session.handle_data_ack
            self._server.start()

            # 设置配对码
            self._server.pair_code = pair_code

            # 设置传输通道
            self._session.set_transport(self._server)

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
                client.on_disconnected = lambda intentional=False: self._handle_disconnected(intentional)
                client.on_error = lambda err: self.logMessage.emit(f"连接错误: {err}")
                client.on_file_info = self._session.handle_file_info
                client.on_file_data = self._session.handle_file_data
                client.on_data_ack = self._session.handle_data_ack

                success = client.connect(ip, pair_code)
                if success:
                    self._client = client
                    self._session.set_transport(client)
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
        self._session.cleanup_all_state()
        self._session.set_transport(None)
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
        if not self._session:
            self.logMessage.emit("未初始化传输会话")
            return

        if self._session.is_sending:
            self.logMessage.emit("正在发送中，请等待完成")
            return

        # 更新下载目录
        self._session.download_dir = Path(self._download_dir)

        self.logMessage.emit(f"开始发送 {len(file_paths)} 个文件")
        self._session.send_files(file_paths)

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

    def _handle_disconnected(self, intentional=False):
        """断开连接"""
        self._session.cleanup_all_state()
        self._session.set_transport(None)
        self._stop_broadcast()
        self._stop_discovery()

        if self._server:
            self._server.stop()
        if self._client:
            self._client.disconnect()

        self._server = None
        self._client = None
        self._connection_status = "未连接"
        self._peer_name = ""
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit("-")
        if intentional:
            self.logMessage.emit("对方已断开连接")
        else:
            self.logMessage.emit("连接已断开")

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

    def cleanup(self):
        """清理资源"""
        self._session.cleanup_all_state()
        self._stop_broadcast()
        self._stop_discovery()
        if self._server:
            self._server.stop()
        if self._client:
            self._client.disconnect()
