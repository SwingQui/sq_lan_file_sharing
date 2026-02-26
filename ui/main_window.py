"""主窗口模块"""
import sys
import threading
import time
from pathlib import Path
from typing import Optional, List

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit,
    QGroupBox, QProgressBar, QFileDialog,
    QMessageBox, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QDragEnterEvent, QDropEvent, QCursor, QMouseEvent

from network.server import LanShareServer
from network.client import LanShareClient
from network.protocol import MessageBuilder, MessageType
from file_handler import FileHandler
from transfer.chunk_receiver import ChunkedFileReceiver
from transfer.chunk_sender import ChunkedFileSender
from transfer.state_manager import TransferStateManager
from config import (
    DEFAULT_DOWNLOAD_DIR, BUFFER_SIZE, CHUNK_SIZE,
    get_last_file_dir, set_last_file_dir,
    get_last_folder_dir, set_last_folder_dir
)


class ClickableLabel(QLabel):
    """可点击的标签，点击复制内容到剪贴板"""

    def __init__(self, text="", parent=None, color="#4CAF50"):
        super().__init__(text, parent)
        self.original_color = color
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip("点击复制")
        self.setStyleSheet(f"color: {color};")

    def mousePressEvent(self, event: QMouseEvent):
        """鼠标点击事件"""
        if event.button() == Qt.LeftButton:
            text = self.text()
            if text:
                clipboard = QApplication.clipboard()
                clipboard.setText(text)
                self.setToolTip("已复制!")
                self.setStyleSheet("color: #FF9800;")
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(500, self._reset_style)

    def _reset_style(self):
        """恢复样式"""
        self.setStyleSheet(f"color: {self.original_color};")
        self.setToolTip("点击复制")


class WorkerSignals(QObject):
    """工作线程信号"""
    error = pyqtSignal(str)
    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    file_info = pyqtSignal(dict)
    file_progress = pyqtSignal(int, int)
    file_complete = pyqtSignal(str)
    log = pyqtSignal(str)
    reconnect_status = pyqtSignal(str)


class FileTransferManager:
    """文件传输管理器 - 使用分块传输"""

    def __init__(self, file_handler: FileHandler, signals: WorkerSignals, send_func):
        self.file_handler = file_handler
        self.signals = signals
        self.send = send_func

        # 状态管理器
        self.state_manager = TransferStateManager()

        # 发送状态
        self.is_sending = False
        self.sender: Optional[ChunkedFileSender] = None
        self.send_thread: Optional[threading.Thread] = None

        # 接收状态
        self.is_receiving = False
        self.receiver: Optional[ChunkedFileReceiver] = None
        self.receive_filesize: int = 0
        self.receive_file_hash: str = ''

    # ==================== 发送 ====================

    def start_send(self, filepath: str, peer_device_id: str = '', on_complete_callback=None):
        """开始发送文件"""
        if self.is_sending:
            return False

        self.is_sending = True
        self.on_complete_callback = on_complete_callback

        self.send_thread = threading.Thread(
            target=self._send_file_task,
            args=(filepath, peer_device_id),
            daemon=True
        )
        self.send_thread.start()
        return True

    def _send_file_task(self, filepath: str, peer_device_id: str):
        """发送文件任务"""
        try:
            # 创建发送器
            self.sender = ChunkedFileSender(
                state_manager=self.state_manager,
                on_progress=self._on_send_progress,
                on_chunk_sent=self._send_chunk
            )

            # 准备文件
            filename, filesize, file_hash, is_folder = self.sender.prepare(filepath, peer_device_id)

            self.signals.log.emit(f"发送: {filename} ({filesize} 字节)")

            # 发送文件信息
            self.send(MessageBuilder.file_info(filename, filesize, file_hash, is_folder))

            # 发送所有块
            retry_count = 0
            max_retry = 3

            while not self.sender.is_complete() and retry_count < max_retry:
                chunk = self.sender.get_next_chunk()
                if chunk is None:
                    break

                chunk_index, data = chunk
                if not self._send_chunk_with_data(chunk_index, data):
                    retry_count += 1
                    continue

                retry_count = 0  # 重置重试计数

            if self.sender.is_complete():
                self.signals.log.emit(f"发送完成: {filename}")
            else:
                self.signals.error.emit(f"发送失败: {filename}")

        except Exception as e:
            self.signals.error.emit(f"发送失败: {str(e)}")
        finally:
            if self.sender:
                self.sender.complete()
                self.sender = None
            self.is_sending = False
            if self.on_complete_callback:
                self.on_complete_callback()

    def _send_chunk(self, chunk_index: int, data: bytes) -> bool:
        """发送块回调"""
        return self._send_chunk_with_data(chunk_index, data)

    def _send_chunk_with_data(self, chunk_index: int, data: bytes) -> bool:
        """发送数据块"""
        try:
            msg = MessageBuilder.file_data(chunk_index, data)
            if self.send(msg):
                if self.sender:
                    self.sender.mark_chunk_sent(chunk_index)
                return True
        except Exception as e:
            print(f"发送块 {chunk_index} 失败: {e}")
        return False

    def _on_send_progress(self, sent: int, total: int):
        """发送进度回调"""
        self.signals.file_progress.emit(sent, total)

    def resume_send(self, received_chunks: list):
        """根据接收方的已接收列表恢复发送"""
        if self.sender:
            self.sender.resume_from_chunks(received_chunks)
            self.signals.log.emit(f"续传: 从块 {len(received_chunks)} 继续")

    # ==================== 接收 ====================

    def start_receive(self, info: dict):
        """开始接收文件"""
        if self.is_receiving:
            return

        self.is_receiving = True
        self.receive_filesize = info.get('filesize', 0)
        self.receive_file_hash = info.get('hash', '')

        filename = info.get('filename', 'unknown')
        is_folder = info.get('is_folder', False)

        # 检查是否有未完成的接收
        existing_state = self.state_manager.load_receiving_state(self.receive_file_hash)

        # 创建接收器
        self.receiver = ChunkedFileReceiver(
            state_manager=self.state_manager,
            download_dir=Path(self.file_handler.download_dir),
            on_progress=self._on_receive_progress
        )

        # 开始接收
        self.receiver.start_receive(
            file_name=filename,
            file_size=self.receive_filesize,
            file_hash=self.receive_file_hash,
            chunk_size=CHUNK_SIZE
        )

        if existing_state:
            # 有历史状态，发送续传请求
            self.signals.log.emit(f"续传接收: {filename} (已接收 {len(existing_state.received_chunks)} 块)")
            # 注意：续传请求需要通过UI层发送，因为需要访问send函数
            self._pending_resume = existing_state.received_chunks
        else:
            self.signals.log.emit(f"接收: {filename} ({self.receive_filesize} 字节)")
            self._pending_resume = None

    def get_pending_resume_chunks(self) -> Optional[list]:
        """获取待发送的续传块列表"""
        chunks = self._pending_resume
        self._pending_resume = None
        return chunks

    def receive_data(self, chunk_index: int, data: bytes):
        """接收文件数据"""
        if not self.is_receiving or not self.receiver:
            return

        # 写入块
        self.receiver.write_chunk(chunk_index, data)

        # 检查是否完成
        if self.receiver.is_complete():
            self._complete_receive()

    def _on_receive_progress(self, received: int, total: int):
        """接收进度回调"""
        self.signals.file_progress.emit(received, total)

    def _complete_receive(self):
        """完成接收"""
        if not self.receiver:
            return

        try:
            saved_path = self.receiver.complete()
            if saved_path:
                self.signals.log.emit(f"已保存: {saved_path}")
                self.signals.file_complete.emit(saved_path)
        except Exception as e:
            self.signals.error.emit(f"保存文件失败: {str(e)}")
        finally:
            self.is_receiving = False
            self.receiver = None

    def cancel(self):
        """取消传输"""
        self.is_sending = False
        self.is_receiving = False

        if self.sender:
            self.sender.cancel()
            self.sender = None

        if self.receiver:
            self.receiver.cancel()
            self.receiver = None


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()

        self.server: Optional[LanShareServer] = None
        self.client: Optional[LanShareClient] = None
        self.is_server_mode = False
        self.download_dir = DEFAULT_DOWNLOAD_DIR
        self.file_handler = FileHandler(self.download_dir)
        self.transfer_manager: Optional[FileTransferManager] = None

        self.pending_files: List[str] = []

        self.signals = WorkerSignals()
        self._setup_signals()
        self._init_ui()

    def _setup_signals(self):
        """设置信号连接"""
        self.signals.error.connect(self._show_error)
        self.signals.connected.connect(self._on_connected)
        self.signals.disconnected.connect(self._on_disconnected)
        self.signals.file_progress.connect(self._on_progress)
        self.signals.file_complete.connect(self._on_file_complete)
        self.signals.log.connect(self._log)
        self.signals.reconnect_status.connect(self._on_reconnect_status)

    def _init_ui(self):
        """初始化UI"""
        self.setWindowTitle("局域网文件共享")
        self.setMinimumSize(600, 550)
        self.setAcceptDrops(True)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        self._create_ip_section(main_layout)
        self._create_connection_section(main_layout)
        self._create_status_section(main_layout)
        self._create_transfer_section(main_layout)
        self._create_log_section(main_layout)

    def _create_ip_section(self, layout: QVBoxLayout):
        """创建IP显示区域"""
        group = QGroupBox("本机信息")
        group_layout = QHBoxLayout(group)

        ip_label = QLabel("本机 IP:")
        ip_label.setFont(QFont("Microsoft YaHei", 10))

        self.ip_display = ClickableLabel(color="#2196F3")
        self.ip_display.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))

        local_ip = LanShareServer.get_local_ip()
        self.ip_display.setText(local_ip)

        group_layout.addWidget(ip_label)
        group_layout.addWidget(self.ip_display)
        group_layout.addStretch()

        layout.addWidget(group)

    def _create_connection_section(self, layout: QVBoxLayout):
        """创建连接区域"""
        group = QGroupBox("连接设置")
        group_layout = QVBoxLayout(group)

        mode_layout = QHBoxLayout()

        self.server_btn = QPushButton("创建房间 (生成配对码)")
        self.server_btn.setMinimumHeight(40)
        self.server_btn.clicked.connect(self._start_server)

        self.client_btn = QPushButton("加入房间 (输入配对码)")
        self.client_btn.setMinimumHeight(40)
        self.client_btn.clicked.connect(self._show_client_input)

        mode_layout.addWidget(self.server_btn)
        mode_layout.addWidget(self.client_btn)
        group_layout.addLayout(mode_layout)

        code_layout = QHBoxLayout()

        code_label = QLabel("配对码:")
        code_label.setFont(QFont("Microsoft YaHei", 10))

        self.pair_code_display = ClickableLabel(color="#4CAF50")
        self.pair_code_display.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))

        self.pair_code_input = QLineEdit()
        self.pair_code_input.setPlaceholderText("输入对方配对码")
        self.pair_code_input.setMaximumWidth(150)
        self.pair_code_input.setFont(QFont("Microsoft YaHei", 12))
        self.pair_code_input.setMaxLength(6)
        self.pair_code_input.hide()

        self.server_ip_input = QLineEdit()
        self.server_ip_input.setPlaceholderText("对方IP地址")
        self.server_ip_input.setMaximumWidth(150)
        self.server_ip_input.hide()

        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._connect_to_server)
        self.connect_btn.hide()

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._cancel_wait)
        self.cancel_btn.hide()

        self.disconnect_btn = QPushButton("断开连接")
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.disconnect_btn.hide()

        code_layout.addWidget(code_label)
        code_layout.addWidget(self.pair_code_display)
        code_layout.addWidget(self.pair_code_input)
        code_layout.addWidget(self.server_ip_input)
        code_layout.addWidget(self.connect_btn)
        code_layout.addWidget(self.cancel_btn)
        code_layout.addWidget(self.disconnect_btn)
        code_layout.addStretch()

        group_layout.addLayout(code_layout)
        layout.addWidget(group)

    def _create_status_section(self, layout: QVBoxLayout):
        """创建状态区域"""
        group = QGroupBox("连接状态")
        group_layout = QHBoxLayout(group)

        self.status_label = QLabel("未连接")
        self.status_label.setFont(QFont("Microsoft YaHei", 10))
        self.status_label.setStyleSheet("color: #9E9E9E;")

        self.peer_label = QLabel()

        group_layout.addWidget(self.status_label)
        group_layout.addWidget(self.peer_label)
        group_layout.addStretch()

        layout.addWidget(group)

    def _create_transfer_section(self, layout: QVBoxLayout):
        """创建文件传输区域"""
        group = QGroupBox("文件传输")
        group_layout = QVBoxLayout(group)

        self.file_list_label = QLabel("待发送文件: (拖拽文件到窗口)")
        self.file_list = QTextEdit()
        self.file_list.setReadOnly(True)
        self.file_list.setMaximumHeight(80)
        self.file_list.setPlaceholderText("拖拽文件或文件夹到这里...")

        btn_layout = QHBoxLayout()

        self.add_file_btn = QPushButton("添加文件")
        self.add_file_btn.clicked.connect(self._add_files)

        self.add_folder_btn = QPushButton("添加文件夹")
        self.add_folder_btn.clicked.connect(self._add_folder)

        self.clear_files_btn = QPushButton("清空列表")
        self.clear_files_btn.clicked.connect(self._clear_files)

        self.send_btn = QPushButton("发送文件")
        self.send_btn.clicked.connect(self._send_files)
        self.send_btn.setEnabled(False)

        btn_layout.addWidget(self.add_file_btn)
        btn_layout.addWidget(self.add_folder_btn)
        btn_layout.addWidget(self.clear_files_btn)
        btn_layout.addWidget(self.send_btn)

        progress_layout = QHBoxLayout()
        progress_label = QLabel("传输进度:")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label = QLabel("")

        progress_layout.addWidget(progress_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)

        group_layout.addWidget(self.file_list_label)
        group_layout.addWidget(self.file_list)
        group_layout.addLayout(btn_layout)
        group_layout.addLayout(progress_layout)

        dir_layout = QHBoxLayout()
        self.dir_label = QLabel(f"下载目录: {DEFAULT_DOWNLOAD_DIR}")
        self.dir_label.setStyleSheet("color: #757575;")
        open_dir_btn = QPushButton("打开目录")
        open_dir_btn.clicked.connect(self._open_download_dir)
        change_dir_btn = QPushButton("更改目录")
        change_dir_btn.clicked.connect(self._change_download_dir)

        dir_layout.addWidget(self.dir_label)
        dir_layout.addStretch()
        dir_layout.addWidget(change_dir_btn)
        dir_layout.addWidget(open_dir_btn)

        group_layout.addLayout(dir_layout)
        layout.addWidget(group)

    def _create_log_section(self, layout: QVBoxLayout):
        """创建日志区域"""
        group = QGroupBox("传输日志")
        group_layout = QVBoxLayout(group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)

        group_layout.addWidget(self.log_text)
        layout.addWidget(group)

    def _log(self, message: str):
        """添加日志"""
        self.log_text.append(message)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ==================== 服务器模式 ====================

    def _start_server(self):
        """启动服务器模式"""
        if self.server or self.client:
            self._disconnect()

        self.is_server_mode = True
        self.server = LanShareServer()

        # 设置回调
        self.server.on_connected = lambda name: self.signals.connected.emit(name)
        self.server.on_disconnected = lambda: self.signals.disconnected.emit()
        self.server.on_error = lambda msg: self.signals.error.emit(msg)
        self.server.on_file_info = self._on_file_info
        self.server.on_file_data = self._on_file_data
        self.server.on_resume_request = self._on_resume_request

        if self.server.start():
            pair_code = self.server.generate_pair_code()
            self.pair_code_display.setText(pair_code)
            self.pair_code_display.show()

            self._update_status("等待连接...", "#FF9800")
            self._log(f"房间已创建，配对码: {pair_code}")

            self.server_btn.setEnabled(False)
            self.client_btn.setEnabled(False)
            self.cancel_btn.show()

            # 初始化传输管理器
            self.transfer_manager = FileTransferManager(
                self.file_handler,
                self.signals,
                self.server.send
            )
        else:
            self._show_error("启动服务器失败")
            self.server = None

    # ==================== 客户端模式 ====================

    def _show_client_input(self):
        """显示客户端输入框"""
        if self.server or self.client:
            self._disconnect()

        self.is_server_mode = False
        self.pair_code_display.hide()
        self.pair_code_input.show()
        self.server_ip_input.show()
        self.connect_btn.show()

        self.server_btn.setEnabled(False)
        self.client_btn.setEnabled(False)

    def _connect_to_server(self):
        """连接到服务器"""
        pair_code = self.pair_code_input.text().strip().upper()
        server_ip = self.server_ip_input.text().strip()

        if not pair_code:
            self._show_error("请输入配对码")
            return

        if not server_ip:
            self._show_error("请输入对方IP地址")
            return

        self.client = LanShareClient()

        # 设置回调
        self.client.on_connected = lambda name: self.signals.connected.emit(name)
        self.client.on_disconnected = lambda: self.signals.disconnected.emit()
        self.client.on_error = lambda msg: self.signals.error.emit(msg)
        self.client.on_file_info = self._on_file_info
        self.client.on_file_data = self._on_file_data
        self.client.on_resume_ok = self._on_resume_ok
        self.client.on_file_complete = self._on_file_complete_msg

        self._update_status("正在连接...", "#FF9800")
        self._log(f"正在连接 {server_ip}，配对码: {pair_code}")

        if self.client.connect(server_ip, pair_code):
            self.pair_code_input.hide()
            self.server_ip_input.hide()
            self.connect_btn.hide()
            self.disconnect_btn.show()

            # 初始化传输管理器
            self.transfer_manager = FileTransferManager(
                self.file_handler,
                self.signals,
                self.client.send
            )
        else:
            self.client = None

    # ==================== 连接管理 ====================

    def _disconnect(self):
        """断开连接"""
        if self.transfer_manager:
            self.transfer_manager.cancel()

        if self.server:
            self.server.stop()
            self.server = None

        if self.client:
            self.client.disconnect()
            self.client = None

        self._reset_ui()

    def _cancel_wait(self):
        """取消等待连接"""
        self._disconnect()
        self._log("已取消等待")

    def _reset_ui(self):
        """重置UI状态"""
        self.is_server_mode = False
        self.pair_code_display.clear()
        self.pair_code_display.show()
        self.pair_code_input.hide()
        self.server_ip_input.hide()
        self.connect_btn.hide()
        self.cancel_btn.hide()
        self.disconnect_btn.hide()

        self.server_btn.setEnabled(True)
        self.client_btn.setEnabled(True)
        self.send_btn.setEnabled(False)

        self._update_status("未连接", "#9E9E9E")
        self.peer_label.clear()
        self.progress_bar.setValue(0)
        self.progress_label.setText("")

    def _update_status(self, text: str, color: str = "#9E9E9E"):
        """更新状态显示"""
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def _on_reconnect_status(self, status: str):
        """重连状态更新"""
        self._update_status(status, "#FF9800")

    # ==================== 文件操作 ====================

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        """拖拽放下事件"""
        urls = event.mimeData().urls()
        for url in urls:
            filepath = url.toLocalFile()
            if Path(filepath).exists():
                self.pending_files.append(filepath)
        self._update_file_list()

    def _add_files(self):
        """添加文件"""
        last_dir = get_last_file_dir()
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", last_dir)
        if files:
            self.pending_files.extend(files)
            # 记住选择的目录
            set_last_file_dir(str(Path(files[0]).parent))
        self._update_file_list()

    def _add_folder(self):
        """添加文件夹"""
        last_dir = get_last_folder_dir()
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", last_dir)
        if folder:
            self.pending_files.append(folder)
            # 记住选择的目录
            set_last_folder_dir(folder)
        self._update_file_list()

    def _clear_files(self):
        """清空文件列表"""
        self.pending_files.clear()
        self._update_file_list()

    def _update_file_list(self):
        """更新文件列表显示"""
        if self.pending_files:
            file_names = [Path(f).name for f in self.pending_files]
            self.file_list.setText('\n'.join(file_names))
            self.send_btn.setEnabled(bool(self.server or self.client) and
                                     self.transfer_manager and
                                     not self.transfer_manager.is_sending)
        else:
            self.file_list.clear()
            self.send_btn.setEnabled(False)

    def _open_download_dir(self):
        """打开下载目录"""
        import subprocess
        if sys.platform == 'win32':
            subprocess.run(['explorer', self.download_dir])
        elif sys.platform == 'darwin':
            subprocess.run(['open', self.download_dir])
        else:
            subprocess.run(['xdg-open', self.download_dir])

    def _change_download_dir(self):
        """更改下载目录"""
        new_dir = QFileDialog.getExistingDirectory(
            self,
            "选择下载目录",
            self.download_dir
        )
        if new_dir:
            self.download_dir = new_dir
            self.dir_label.setText(f"下载目录: {new_dir}")
            self.file_handler = FileHandler(new_dir)
            if self.transfer_manager:
                self.transfer_manager.file_handler = self.file_handler
            self._log(f"下载目录已更改为: {new_dir}")

    # ==================== 文件传输 ====================

    def _send_files(self):
        """发送文件"""
        if not self.pending_files:
            return

        if self.transfer_manager and self.transfer_manager.is_sending:
            self._show_error("正在发送中...")
            return

        filepath = self.pending_files.pop(0)
        self._update_file_list()

        # 获取对方设备ID
        peer_device_id = ''
        if self.server and self.server.client_device_id:
            peer_device_id = self.server.client_device_id
        elif self.client:
            peer_device_id = self.client.server_device_id or ''

        def on_complete():
            self.send_btn.setEnabled(bool(self.pending_files))
            if self.pending_files:
                self._send_files()

        self.transfer_manager.start_send(filepath, peer_device_id, on_complete)

    def _on_file_info(self, info: dict):
        """处理接收到的文件信息"""
        if self.transfer_manager:
            self.transfer_manager.start_receive(info)

            # 检查是否有续传请求需要发送
            chunks = self.transfer_manager.get_pending_resume_chunks()
            if chunks and self.server:
                file_hash = info.get('hash', '')
                self.server.send(MessageBuilder.file_resume(file_hash, chunks, ''))

    def _on_file_data(self, data: bytes):
        """处理接收到的文件数据"""
        if self.transfer_manager:
            try:
                chunk_index, actual_data = MessageBuilder.decode_file_data(data)
                self.transfer_manager.receive_data(chunk_index, actual_data)
            except Exception as e:
                self.signals.error.emit(f"解析文件数据失败: {str(e)}")

    def _on_resume_request(self, msg_data: dict):
        """处理续传请求（服务器端）"""
        file_hash = msg_data.get('file_hash', '')
        received_chunks = msg_data.get('received_chunks', [])

        if self.transfer_manager and self.transfer_manager.sender:
            # 更新发送器状态
            self.transfer_manager.resume_send(received_chunks)

            # 发送确认
            needed = self.transfer_manager.sender.get_needed_chunks(received_chunks)
            self.server.send_resume_ok(file_hash, needed)

    def _on_resume_ok(self, msg_data: dict):
        """处理续传确认（客户端）"""
        file_hash = msg_data.get('file_hash', '')
        needed_chunks = msg_data.get('needed_chunks', [])

        if self.transfer_manager and self.transfer_manager.sender:
            self._log(f"续传确认: 需要发送 {len(needed_chunks)} 块")

    def _on_file_complete_msg(self, msg_data: dict):
        """处理传输完成消息"""
        success = msg_data.get('success', False)
        file_hash = msg_data.get('file_hash', '')
        if success:
            self._log(f"传输完成: {file_hash[:8]}...")

    def _on_progress(self, current: int, total: int):
        """更新进度"""
        if total > 0:
            percent = int(current / total * 100)
            self.progress_bar.setValue(percent)
            self.progress_label.setText(f"{current}/{total} 块")

    def _on_file_complete(self, filepath: str):
        """文件接收完成"""
        self.progress_bar.setValue(100)
        self.progress_label.setText("完成")

    # ==================== 信号处理 ====================

    def _on_connected(self, peer_name: str):
        """连接成功"""
        self._update_status("已连接", "#4CAF50")
        self.peer_label.setText(f"对方: {peer_name}")
        self.cancel_btn.hide()
        self.disconnect_btn.show()
        self.send_btn.setEnabled(bool(self.pending_files))
        self._log(f"已连接到: {peer_name}")

    def _on_disconnected(self):
        """连接断开"""
        self._log("连接已断开")
        self._reset_ui()

    def _show_error(self, message: str):
        """显示错误消息"""
        QMessageBox.warning(self, "错误", message)
        self._log(f"错误: {message}")

    def closeEvent(self, event):
        """窗口关闭事件"""
        self._disconnect()
        event.accept()
