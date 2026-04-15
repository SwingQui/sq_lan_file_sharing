"""主窗口模块 - 卡片式布局"""
import os
import threading
import time
import random
import string
import platform
from pathlib import Path
from typing import Optional, List

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit,
    QFileDialog, QMessageBox, QApplication,
    QFrame, QDesktopWidget, QGridLayout,
    QProgressBar, QSplitter, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer, QMimeData
from PyQt5.QtGui import QFont, QDragEnterEvent, QDropEvent, QCursor, QMouseEvent

from network.server import LanShareServer
from network.client import LanShareClient
from network.reconnect import ReconnectManager
from network.discovery import RoomBroadcaster, RoomScanner, RoomInfo
from file_handler import FileHandler
from transfer.session import TransferSession
from transfer.state_manager import TransferStateManager
from config import (
    DEFAULT_DOWNLOAD_DIR, DEFAULT_PORT, MAX_CONCURRENT_FILES,
    get_last_file_dir, set_last_file_dir,
    get_last_folder_dir, set_last_folder_dir
)
from utils import get_local_ip, format_size


# ==================== 主题颜色 ====================
THEME = {
    'primary': '#2D8C6F',
    'primary_hover': '#236B55',
    'primary_active': '#1A5040',
    'primary_light': '#D1FAE5',
    'bg': '#F5FAF8',
    'card_bg': '#FFFFFF',
    'text': '#374151',
    'text_secondary': '#6B7280',
    'border': '#E5E7EB',
    'success': '#2D8C6F',
    'warning': '#D97706',
    'error': '#DC2626',
}


class ClickableLabel(QLabel):
    """可点击的标签，点击复制内容到剪贴板"""

    def __init__(self, text="", parent=None, color=THEME['primary']):
        super().__init__(text, parent)
        self.original_color = color
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip("点击复制")
        self.setStyleSheet(f"color: {color}; font-weight: bold;")

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            text = self.text()
            if text and text != '-':
                clipboard = QApplication.clipboard()
                clipboard.setText(text)
                self.setToolTip("已复制!")
                self.setStyleSheet(f"color: {THEME['warning']}; font-weight: bold;")
                QTimer.singleShot(1000, self._reset_style)

    def _reset_style(self):
        self.setStyleSheet(f"color: {self.original_color}; font-weight: bold;")
        self.setToolTip("点击复制")


class WorkerSignals(QObject):
    """工作线程信号 - 所有跨线程UI通信必须通过信号"""
    log = pyqtSignal(str)
    status_changed = pyqtSignal(str, str)        # status_text, color
    peer_changed = pyqtSignal(str)               # peer_name
    connected = pyqtSignal(str)                  # peer_name
    disconnected = pyqtSignal(bool)                      # intentional
    send_progress = pyqtSignal(int, int, int, str, str)  # percent, done_files, total_files, transferred, total_size
    send_completed = pyqtSignal(bool, str)       # success, message
    recv_progress = pyqtSignal(str, int, str, str)    # file_hash, percent, received, total
    recv_file_info = pyqtSignal(str, str, int)        # file_hash, filename, size
    recv_completed = pyqtSignal(str, bool, str)       # file_hash, success, message
    room_found = pyqtSignal(str, str, str)       # name, ip, pair_code
    room_expired = pyqtSignal(str)               # ip


class CardWidget(QFrame):
    """卡片组件"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME['card_bg']};
                border-radius: 12px;
                border: none;
            }}
        """)
        self._init_ui(title)

    def _init_ui(self, title: str):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"""
            font-size: 16px;
            font-weight: bold;
            color: {THEME['text']};
        """)
        title_label.setAlignment(Qt.AlignLeft)
        main_layout.addWidget(title_label)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        main_layout.addWidget(self.content_widget, 1)

        main_layout.addStretch()

    def add_widget(self, widget: QWidget, stretch: int = 0):
        self.content_layout.addWidget(widget, stretch)

    def add_layout(self, layout):
        self.content_layout.addLayout(layout)


class MainWindow(QMainWindow):
    """主窗口 - 卡片式双列布局"""

    def __init__(self):
        super().__init__()
        self._local_ip = "-"
        self._connection_status = "未连接"
        self._peer_name = "-"
        self._download_dir = DEFAULT_DOWNLOAD_DIR
        self._server = None
        self._client = None
        self._broadcaster = None
        self._scanner = None
        self._files_to_send = []
        self._file_handler = FileHandler(self._download_dir)
        self._transfer_state_manager = TransferStateManager()
        self._session = TransferSession(
            download_dir=Path(self._download_dir),
            state_manager=self._transfer_state_manager,
            on_send_progress=lambda p, df, tf, t, tt: self._signals.send_progress.emit(p, df, tf, t, tt),
            on_send_file_done=lambda s, fn, fs: None,
            on_send_completed=lambda s, m: self._signals.send_completed.emit(s, m),
            on_recv_progress=lambda fh, p, r, t: self._signals.recv_progress.emit(fh, p, r, t),
            on_recv_file_info=lambda fh, fn, sz: self._signals.recv_file_info.emit(fh, fn, sz),
            on_recv_completed=lambda fh, s, m: self._signals.recv_completed.emit(fh, s, m),
            on_log=lambda m: self._signals.log.emit(m),
        )
        self._signals = WorkerSignals()
        self._reconnect_manager = None
        self._reconnect_timer = None
        self._setup_ui()
        self._connect_signals()
        self._local_ip = get_local_ip()
        self.ip_value.setText(self._local_ip)
        self.setAcceptDrops(True)

    # ==================== UI 构建 ====================

    def _setup_ui(self):
        self.setWindowTitle("SQ 局域网文件共享")
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)
        self._center_window()
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {THEME['bg']};
            }}
            QPushButton {{
                background-color: {THEME['primary']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {THEME['primary_hover']};
            }}
            QPushButton:pressed {{
                background-color: {THEME['primary_active']};
            }}
            QPushButton:disabled {{
                background-color: {THEME['border']};
                color: {THEME['text_secondary']};
            }}
            QLineEdit, QTextEdit {{
                border: 1px solid {THEME['border']};
                border-radius: 8px;
                padding: 8px 12px;
                background-color: white;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border: 2px solid {THEME['primary']};
            }}
            QProgressBar {{
                border-radius: 8px;
                text-align: center;
                background-color: {THEME['border']};
                min-height: 20px;
            }}
            QProgressBar::chunk {{
                background-color: {THEME['primary']};
                border-radius: 8px;
            }}
            QLabel {{
                color: {THEME['text']};
            }}
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 顶部状态栏
        main_layout.addWidget(self._create_status_bar())

        # 中间内容区 - 使用 QGridLayout 双列
        content_widget = QWidget()
        content_layout = QGridLayout(content_widget)
        content_layout.setSpacing(12)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setColumnStretch(0, 1)
        content_layout.setColumnStretch(1, 1)
        content_layout.setRowStretch(0, 1)

        # 左侧: 连接卡片
        self.connection_card = self._create_connection_card()
        self.connection_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout.addWidget(self.connection_card, 0, 0)

        # 右侧: 传输卡片
        self.transfer_card = self._create_transfer_card()
        self.transfer_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout.addWidget(self.transfer_card, 0, 1)

        main_layout.addWidget(content_widget, 1)

        # 底部: 日志卡片
        self.log_card = self._create_log_card()
        main_layout.addWidget(self.log_card)

    def _create_status_bar(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(f"""
            QWidget {{
                background-color: {THEME['card_bg']};
                border-radius: 8px;
            }}
        """)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(16, 12, 16, 12)

        ip_label = QLabel("本机IP:")
        ip_label.setStyleSheet(f"color: {THEME['text_secondary']}; font-weight: 600;")
        self.ip_value = ClickableLabel("-", color=THEME['primary'])
        layout.addWidget(ip_label)
        layout.addWidget(self.ip_value)
        layout.addSpacing(40)

        status_label = QLabel("状态:")
        status_label.setStyleSheet(f"color: {THEME['text_secondary']}; font-weight: 600;")
        self.status_value = QLabel("未连接")
        self.status_value.setStyleSheet(f"color: {THEME['text_secondary']}; font-weight: bold;")
        layout.addWidget(status_label)
        layout.addWidget(self.status_value)
        layout.addSpacing(40)

        peer_label = QLabel("对方:")
        peer_label.setStyleSheet(f"color: {THEME['text_secondary']}; font-weight: 600;")
        self.peer_value = QLabel("-")
        self.peer_value.setStyleSheet(f"color: {THEME['primary']}; font-weight: bold;")
        layout.addWidget(peer_label)
        layout.addWidget(self.peer_value)

        layout.addStretch()
        return widget

    def _create_connection_card(self) -> CardWidget:
        card = CardWidget("连接", self)

        # 按钮垂直排布，撑满容器
        btn_container = QWidget()
        btn_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        btn_layout = QVBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(16)

        self.btn_create = QPushButton("创建房间")
        self.btn_create.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_create.setStyleSheet("font-size: 20px;")
        self.btn_create.clicked.connect(self._on_create_room)
        btn_layout.addWidget(self.btn_create, 1)

        self.btn_join = QPushButton("加入房间")
        self.btn_join.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_join.setStyleSheet("font-size: 20px;")
        self.btn_join.clicked.connect(self._on_join_room)
        btn_layout.addWidget(self.btn_join, 1)

        self.btn_manage_devices = self._create_outline_button("管理信任设备")
        self.btn_manage_devices.clicked.connect(self._on_manage_devices)
        btn_layout.addWidget(self.btn_manage_devices)

        card.add_widget(btn_container)

        # 等待连接模式
        self.waiting_widget = QWidget()
        self.waiting_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        waiting_layout = QVBoxLayout(self.waiting_widget)
        waiting_layout.setContentsMargins(0, 8, 0, 0)

        waiting_layout.addStretch()

        pair_layout = QHBoxLayout()
        pair_layout.addStretch()
        pair_label = QLabel("配对码:")
        pair_label.setStyleSheet(f"color: {THEME['text_secondary']};")
        self.pair_code_label = ClickableLabel("------", color=THEME['primary'])
        font = self.pair_code_label.font()
        font.setPointSize(20)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        self.pair_code_label.setFont(font)
        pair_layout.addWidget(pair_label)
        pair_layout.addWidget(self.pair_code_label)
        pair_layout.addStretch()
        waiting_layout.addLayout(pair_layout)

        tip_label = QLabel("请将配对码告知对方，或等待对方自动发现")
        tip_label.setStyleSheet(f"color: {THEME['text_secondary']}; font-size: 12px;")
        tip_label.setAlignment(Qt.AlignCenter)
        waiting_layout.addWidget(tip_label)

        self.btn_cancel_wait = self._create_outline_button("取消")
        self.btn_cancel_wait.clicked.connect(self._on_cancel_wait)
        waiting_layout.addWidget(self.btn_cancel_wait, alignment=Qt.AlignCenter)

        waiting_layout.addStretch()

        self.waiting_widget.hide()
        card.add_widget(self.waiting_widget, 1)

        # 房间列表
        self.room_list_widget = QWidget()
        self.room_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        room_layout = QVBoxLayout(self.room_list_widget)
        room_layout.setContentsMargins(0, 8, 0, 0)

        room_title = QLabel("局域网房间 (双击连接)")
        room_title.setStyleSheet(f"color: {THEME['text_secondary']}; font-weight: 600; font-size: 13px;")
        room_layout.addWidget(room_title)

        self.room_list = QTextEdit()
        self.room_list.setReadOnly(True)
        self.room_list.setMaximumHeight(120)
        self.room_list.setStyleSheet(f"""
            QTextEdit {{
                background-color: {THEME['bg']};
                border-radius: 8px;
            }}
        """)
        self.room_list.hide()
        room_layout.addWidget(self.room_list)

        # 房间按钮容器
        self.room_buttons_widget = QWidget()
        self.room_buttons_layout = QVBoxLayout(self.room_buttons_widget)
        self.room_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.room_buttons_layout.setSpacing(8)
        # 存储房间按钮 {ip: QPushButton}
        self._room_buttons = {}
        room_layout.addWidget(self.room_buttons_widget)

        self.btn_cancel_discovery = self._create_outline_button("取消扫描")
        self.btn_cancel_discovery.clicked.connect(self._on_cancel_discovery)
        room_layout.addWidget(self.btn_cancel_discovery)

        self.room_list_widget.hide()
        card.add_widget(self.room_list_widget, 1)

        # 手动连接
        self.manual_widget = QWidget()
        self.manual_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        manual_layout = QVBoxLayout(self.manual_widget)
        manual_layout.setContentsMargins(0, 8, 0, 0)

        manual_layout.addStretch()
        manual_layout.setSpacing(8)

        manual_title = QLabel("手动连接")
        manual_title.setStyleSheet(f"color: {THEME['text_secondary']}; font-weight: 600; font-size: 13px;")
        manual_layout.addWidget(manual_title)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(12)

        ip_label = QLabel("IP:")
        ip_label.setStyleSheet(f"color: {THEME['text_secondary']};")
        self.input_ip = QLineEdit()
        self.input_ip.setPlaceholderText("192.168.1.100")
        self.input_ip.setFixedWidth(140)
        input_layout.addWidget(ip_label)
        input_layout.addWidget(self.input_ip)

        code_label = QLabel("配对码:")
        code_label.setStyleSheet(f"color: {THEME['text_secondary']};")
        self.input_code = QLineEdit()
        self.input_code.setPlaceholderText("6位数字")
        self.input_code.setMaxLength(6)
        self.input_code.setFixedWidth(100)
        input_layout.addWidget(code_label)
        input_layout.addWidget(self.input_code)

        self.btn_connect = QPushButton("连接")
        self.btn_connect.clicked.connect(self._on_manual_connect)
        input_layout.addWidget(self.btn_connect)

        manual_layout.addLayout(input_layout)

        manual_layout.addStretch()

        self.manual_widget.hide()
        card.add_widget(self.manual_widget, 1)

        # 已连接模式
        self.connected_widget = QWidget()
        self.connected_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        connected_layout = QVBoxLayout(self.connected_widget)
        connected_layout.setContentsMargins(0, 8, 0, 0)

        connected_layout.addStretch()

        info_layout = QHBoxLayout()
        info_label = QLabel("已连接到:")
        info_label.setStyleSheet(f"color: {THEME['text_secondary']};")
        self.connected_peer_label = QLabel("-")
        self.connected_peer_label.setStyleSheet(f"color: {THEME['success']}; font-weight: bold; font-size: 15px;")
        info_layout.addWidget(info_label)
        info_layout.addWidget(self.connected_peer_label)
        info_layout.addStretch()
        connected_layout.addLayout(info_layout)

        self.btn_disconnect = QPushButton("断开连接")
        self.btn_disconnect.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME['error']};
                color: white;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #B91C1C;
            }}
        """)
        self.btn_disconnect.clicked.connect(self._on_disconnect)
        connected_layout.addWidget(self.btn_disconnect)

        self.reconnect_status_label = QLabel("")
        self.reconnect_status_label.setStyleSheet(f"color: {THEME['warning']}; font-size: 12px;")
        self.reconnect_status_label.setAlignment(Qt.AlignCenter)
        self.reconnect_status_label.hide()
        connected_layout.addWidget(self.reconnect_status_label)

        connected_layout.addStretch()

        self.connected_widget.hide()
        card.add_widget(self.connected_widget, 1)

        return card

    def _create_transfer_card(self) -> CardWidget:
        card = CardWidget("文件传输", self)

        # 文件列表标题
        file_title = QLabel("待发送文件 (拖拽文件到窗口)")
        file_title.setStyleSheet(f"color: {THEME['text_secondary']}; font-weight: 600; font-size: 13px;")
        card.add_widget(file_title)

        # 文件列表 - 自动撑满，不限制高度
        self.file_list = QTextEdit()
        self.file_list.setReadOnly(True)
        self.file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.file_list.setStyleSheet(f"""
            QTextEdit {{
                background-color: {THEME['bg']};
                border-radius: 8px;
            }}
        """)
        card.add_widget(self.file_list, 1)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_add_files = QPushButton("添加文件")
        self.btn_add_files.clicked.connect(self._on_add_files)
        btn_layout.addWidget(self.btn_add_files)

        self.btn_add_folder = QPushButton("添加文件夹")
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        btn_layout.addWidget(self.btn_add_folder)

        self.btn_clear = self._create_outline_button("清空")
        self.btn_clear.clicked.connect(self._on_clear_files)
        btn_layout.addWidget(self.btn_clear)

        btn_layout.addStretch()

        self.btn_send = QPushButton("发送")
        self.btn_send.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME['success']};
                color: white;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #059669;
            }}
            QPushButton:disabled {{
                background-color: {THEME['border']};
                color: {THEME['text_secondary']};
            }}
        """)
        self.btn_send.setEnabled(False)
        self.btn_send.clicked.connect(self._on_send_files)
        btn_layout.addWidget(self.btn_send)

        card.add_layout(btn_layout)

        # 发送进度区域（聚合所有并发文件）
        self.send_progress_widget = QWidget()
        send_progress_layout = QVBoxLayout(self.send_progress_widget)
        send_progress_layout.setContentsMargins(0, 8, 0, 0)

        self.send_progress_text = QLabel("发送: 准备传输...")
        self.send_progress_text.setStyleSheet(f"color: {THEME['text']}; font-weight: 600;")
        send_progress_layout.addWidget(self.send_progress_text)

        self.send_progress_bar = QProgressBar()
        self.send_progress_bar.setValue(0)
        send_progress_layout.addWidget(self.send_progress_bar)

        send_stats = QHBoxLayout()
        self.send_progress_percent = QLabel("0%")
        self.send_progress_size = QLabel("0 / 0 MB")
        for lbl in [self.send_progress_percent, self.send_progress_size]:
            lbl.setStyleSheet(f"color: {THEME['text_secondary']}; font-size: 12px;")
            send_stats.addWidget(lbl)
        send_stats.addStretch()
        send_progress_layout.addLayout(send_stats)

        self.send_progress_widget.hide()
        card.add_widget(self.send_progress_widget)

        # 接收进度区域（动态多进度条，每个文件一条）
        self.recv_progress_container = QWidget()
        recv_container_layout = QVBoxLayout(self.recv_progress_container)
        recv_container_layout.setContentsMargins(0, 8, 0, 0)
        recv_container_layout.setSpacing(4)

        self.recv_title_label = QLabel("接收: 等待中...")
        self.recv_title_label.setStyleSheet(f"color: {THEME['text']}; font-weight: 600;")
        recv_container_layout.addWidget(self.recv_title_label)

        self.recv_bars_layout = QHBoxLayout()
        self.recv_bars_layout.setSpacing(8)
        recv_container_layout.addLayout(self.recv_bars_layout)

        self.recv_progress_container.hide()
        self._recv_progress_bars = {}
        card.add_widget(self.recv_progress_container)

        # 下载目录
        dir_layout = QHBoxLayout()
        dir_layout.setContentsMargins(0, 8, 0, 0)

        dir_label = QLabel("下载目录:")
        dir_label.setStyleSheet(f"color: {THEME['text_secondary']}; font-size: 13px;")
        dir_layout.addWidget(dir_label)

        self.dir_label = QLabel(self._download_dir)
        self.dir_label.setStyleSheet(f"color: {THEME['text']}; font-size: 13px;")
        self.dir_label.setMinimumWidth(150)
        dir_layout.addWidget(self.dir_label, 1)

        self.btn_open_dir = QPushButton("打开")
        self.btn_open_dir.setStyleSheet("padding: 6px 12px; font-size: 12px;")
        self.btn_open_dir.clicked.connect(self._on_open_dir)
        dir_layout.addWidget(self.btn_open_dir)

        self.btn_change_dir = self._create_outline_button("更改")
        self.btn_change_dir.setStyleSheet("padding: 6px 12px; font-size: 12px;")
        self.btn_change_dir.clicked.connect(self._on_change_dir)
        dir_layout.addWidget(self.btn_change_dir)

        card.add_layout(dir_layout)

        return card

    def _create_log_card(self) -> CardWidget:
        card = CardWidget("操作日志", self)

        top_layout = QHBoxLayout()
        top_layout.addStretch()

        self.btn_clear_log = self._create_outline_button("清空")
        self.btn_clear_log.setStyleSheet("padding: 6px 12px; font-size: 12px;")
        self.btn_clear_log.clicked.connect(self._on_clear_log)
        top_layout.addWidget(self.btn_clear_log)
        card.add_layout(top_layout)

        self.log_list = QTextEdit()
        self.log_list.setReadOnly(True)
        self.log_list.setMinimumHeight(80)
        self.log_list.setMaximumHeight(150)
        self.log_list.setStyleSheet(f"""
            QTextEdit {{
                background-color: {THEME['bg']};
                border-radius: 8px;
            }}
        """)
        card.add_widget(self.log_list)

        return card

    def _create_outline_button(self, text: str) -> QPushButton:
        """创建描边按钮"""
        btn = QPushButton(text)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {THEME['primary']};
                border: 1px solid {THEME['primary']};
            }}
            QPushButton:hover {{
                background-color: #D1FAE5;
            }}
        """)
        return btn

    def _center_window(self):
        screen = QDesktopWidget().screenGeometry()
        size = self.geometry()
        self.move(
            (screen.width() - size.width()) // 2,
            (screen.height() - size.height()) // 2
        )

    # ==================== 信号连接 ====================

    def _connect_signals(self):
        self._signals.log.connect(self._append_log)
        self._signals.status_changed.connect(self._update_status)
        self._signals.peer_changed.connect(self._set_peer_name)
        self._signals.connected.connect(self._handle_connected)
        self._signals.disconnected.connect(self._handle_disconnected)
        self._signals.send_progress.connect(self._update_send_progress)
        self._signals.send_completed.connect(self._handle_send_completed)
        self._signals.recv_progress.connect(self._update_recv_progress)
        self._signals.recv_file_info.connect(self._handle_recv_file_info)
        self._signals.recv_completed.connect(self._handle_recv_completed)
        self._signals.room_found.connect(self._handle_room_found)
        self._signals.room_expired.connect(self._handle_room_expired)

    # ==================== 事件处理 ====================

    def _on_create_room(self):
        pair_code = ''.join(random.choices(string.digits, k=6))

        try:
            self._server = LanShareServer()
            self._server.on_connected = lambda name: self._signals.connected.emit(name)
            self._server.on_disconnected = lambda intentional=False: self._signals.disconnected.emit(intentional)
            self._server.on_error = lambda err: self._signals.log.emit(f"服务器错误: {err}")
            self._server.on_file_info = self._session.handle_file_info
            self._server.on_file_data = self._session.handle_file_data
            self._server.on_data_ack = self._session.handle_data_ack
            self._server.on_trusted_connect = lambda did, name: self._signals.connected.emit(name)
            self._server.start()

            # 设置传输通道
            self._session.set_transport(self._server)

            # 设置配对码 (供客户端配对用)
            self._server.pair_code = pair_code

            self._signals.status_changed.emit("等待连接", THEME['warning'])
            self._signals.log.emit(f"已创建房间，配对码: {pair_code}")
            self._show_waiting_mode(pair_code)

            self._start_broadcast(pair_code)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"创建房间失败: {e}")
            self._signals.log.emit(f"创建房间失败: {e}")

    def _on_join_room(self):
        self.btn_create.hide()
        self.btn_join.hide()
        self.room_list_widget.show()
        self.manual_widget.show()
        self._signals.log.emit("正在扫描局域网房间...")
        self._start_discovery()

    def _on_cancel_wait(self):
        self._stop_broadcast()
        if self._server:
            self._server.stop()
            self._server = None
        self._signals.status_changed.emit("未连接", THEME['text_secondary'])
        self._show_idle_mode()
        self._signals.log.emit("已取消创建房间")

    def _on_cancel_discovery(self):
        self._stop_discovery()
        self._signals.status_changed.emit("未连接", THEME['text_secondary'])
        self._show_idle_mode()
        self._signals.log.emit("已取消扫描")

    def _on_manual_connect(self):
        ip = self.input_ip.text().strip()
        code = self.input_code.text().strip().upper()

        if not ip or not code:
            QMessageBox.warning(self, "提示", "请输入IP地址和配对码")
            return

        self._stop_discovery()
        self._signals.status_changed.emit("连接中", THEME['warning'])
        self._signals.log.emit(f"正在连接到 {ip}...")

        def connect_thread():
            try:
                client = LanShareClient()
                client.on_connected = lambda name: self._signals.connected.emit(name)
                client.on_disconnected = lambda intentional=False: self._signals.disconnected.emit(intentional)
                client.on_error = lambda err: self._signals.log.emit(f"连接错误: {err}")
                client.on_file_info = self._session.handle_file_info
                client.on_file_data = self._session.handle_file_data
                client.on_data_ack = self._session.handle_data_ack

                success = client.connect(ip, code)
                if success:
                    self._client = client
                    self._session.set_transport(client)
                else:
                    self._signals.status_changed.emit("未连接", THEME['text_secondary'])
                    self._signals.log.emit("连接失败")
            except Exception as e:
                self._signals.status_changed.emit("未连接", THEME['text_secondary'])
                self._signals.log.emit(f"连接失败: {e}")

        threading.Thread(target=connect_thread, daemon=True).start()

    def _on_disconnect(self):
        self._session.cleanup_all_state()
        self._session.set_transport(None)
        self._stop_broadcast()
        self._stop_discovery()
        self._stop_reconnect()

        if self._server:
            self._server.stop()
            self._server = None
        if self._client:
            self._client.disconnect()
            self._client = None

        self._signals.status_changed.emit("未连接", THEME['text_secondary'])
        self._signals.peer_changed.emit("-")
        self._show_idle_mode()
        self._signals.log.emit("已断开连接")

    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文件", get_last_file_dir()
        )
        if files:
            set_last_file_dir(str(Path(files[0]).parent))
            for f in files:
                if f not in self._files_to_send:
                    self._files_to_send.append(f)
            self._update_file_list()

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择文件夹", get_last_folder_dir()
        )
        if folder:
            set_last_folder_dir(folder)
            if folder not in self._files_to_send:
                self._files_to_send.append(folder)
            self._update_file_list()

    def _on_clear_files(self):
        self._files_to_send = []
        self._update_file_list()

    def _on_send_files(self):
        if not self._files_to_send:
            return

        if not self._session:
            self._signals.log.emit("未初始化传输会话")
            return

        files = self._files_to_send.copy()

        self.btn_send.setEnabled(False)
        self.send_progress_widget.show()

        self._session.send_files(files)

    def _on_open_dir(self):
        import subprocess
        subprocess.Popen(f'explorer "{self._download_dir}"')

    def _on_change_dir(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择下载目录", self._download_dir
        )
        if folder:
            self._download_dir = folder
            self.dir_label.setText(folder)
            self._session.download_dir = Path(folder)
            self._file_handler = FileHandler(folder)
            self._signals.log.emit(f"下载目录已更改为: {folder}")

    def _on_clear_log(self):
        self.log_list.clear()

    def _on_manage_devices(self):
        from trust.device_manager import DeviceManager
        dm = DeviceManager()
        devices = dm.get_trusted_devices()

        if not devices:
            QMessageBox.information(self, "信任设备管理", "暂无信任设备")
            return

        device_list = "\n".join(
            f"  {d.get('hostname', 'Unknown')} ({d.get('last_ip', '?')})"
            for d in devices
        )

        items = [
            f"{d.get('hostname', '?')} ({d.get('last_ip', '?')})"
            for d in devices
        ]

        from PyQt5.QtWidgets import QInputDialog
        item, ok = QInputDialog.getItem(
            self, "信任设备管理",
            f"已信任 {len(devices)} 个设备（选择后点击OK移除）：\n\n"
            f"提示：点击 Cancel 关闭窗口\n{device_list}\n",
            items, 0, False
        )

        if ok and item:
            idx = items.index(item)
            dm.remove_trusted_device(devices[idx]['device_id'])
            self._signals.log.emit(f"已移除信任设备: {item}")
            QMessageBox.information(self, "提示", f"已移除: {item}")

    # ==================== 拖拽支持 ====================

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if path:
                p = Path(path)
                if p.is_file():
                    if path not in self._files_to_send:
                        self._files_to_send.append(path)
                elif p.is_dir():
                    if path not in self._files_to_send:
                        self._files_to_send.append(path)
        self._update_file_list()
        event.acceptProposedAction()

    # ==================== 广播与发现 ====================

    def _start_broadcast(self, pair_code: str):
        self._stop_broadcast()
        hostname = platform.node()
        self._broadcaster = RoomBroadcaster(
            port=DEFAULT_PORT,
            hostname=hostname,
            pair_code=pair_code
        )
        self._broadcaster.start()

    def _stop_broadcast(self):
        if self._broadcaster:
            self._broadcaster.stop()
            self._broadcaster = None

    def _start_discovery(self):
        self._stop_discovery()
        self.room_list.clear()
        # 清空旧的房间按钮
        for ip, btn in self._room_buttons.items():
            self.room_buttons_layout.removeWidget(btn)
            btn.deleteLater()
        self._room_buttons.clear()
        self.room_buttons_widget.hide()

        def on_room_found(room: RoomInfo):
            self._signals.room_found.emit(room.hostname, room.ip, room.pair_code)

        def on_room_expired(room: RoomInfo):
            self._signals.room_expired.emit(room.ip)

        self._scanner = RoomScanner(
            on_room_found=on_room_found,
            on_room_expired=on_room_expired
        )
        self._scanner.start()

    def _stop_discovery(self):
        if self._scanner:
            self._scanner.stop()
            self._scanner = None

    # ==================== UI 更新 (通过信号, GUI线程安全) ====================

    def _append_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_list.append(
            f"<span style='color: #9CA3AF;'>[{timestamp}]</span> {message}"
        )

    def _update_status(self, status: str, color: str):
        self._connection_status = status
        self.status_value.setText(status)
        self.status_value.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _set_peer_name(self, name: str):
        self._peer_name = name
        self.peer_value.setText(name)

    def _handle_connected(self, peer_name: str):
        self._update_status("已连接", THEME['success'])
        self._set_peer_name(peer_name)
        self._stop_discovery()
        self._stop_broadcast()
        self._stop_reconnect()
        self._show_connected_mode(peer_name)
        self._append_log(f"已连接到: {peer_name}")

    def _handle_disconnected(self, intentional=False):
        if intentional:
            self._do_full_disconnect()
            self._append_log("对方已断开连接")
            return

        if self._reconnect_manager and self._reconnect_manager.reconnecting:
            return

        can_client_reconnect = (
            self._client
            and hasattr(self._client, 'server_device_id')
            and self._client.server_device_id
        )

        server_has_trusted_client = (
            self._server
            and self._server.client_device_id
            and not self._server.connected
            and self._server.running
        )

        if can_client_reconnect:
            self._start_client_reconnect()
        elif server_has_trusted_client:
            self._start_server_reconnect_wait()
        else:
            self._do_full_disconnect()

    def _do_full_disconnect(self):
        self._session.cleanup_all_state()
        self._session.set_transport(None)
        self._stop_broadcast()
        self._stop_discovery()
        self._stop_reconnect()
        if self._server:
            self._server.stop()
            self._server = None
        if self._client:
            self._client.disconnect()
            self._client = None
        self._update_status("未连接", THEME['text_secondary'])
        self._set_peer_name("-")
        self._show_idle_mode()

    def _start_client_reconnect(self):
        server_device_id = self._client.server_device_id
        last_ip = self._client.server_ip or ''
        self._client.disconnect()
        self._client = None

        self._update_status("重连中...", THEME['warning'])
        self._append_log("连接断开，正在尝试自动重连...")
        self.reconnect_status_label.setText("正在重连...")
        self.reconnect_status_label.show()

        self._reconnect_manager = ReconnectManager(
            device_id=self._get_device_id(),
            hostname=platform.node(),
            on_reconnected=self._handle_reconnect_success,
            on_reconnect_failed=self._on_reconnect_failed,
            on_state_changed=lambda s: self._signals.log.emit(s)
        )
        self._reconnect_manager.start_reconnect(server_device_id, last_ip or '')

    def _handle_reconnect_success(self, sock):
        client = LanShareClient()
        client.socket = sock
        client.connected = True
        client.running = True
        client.on_disconnected = lambda intentional=False: self._signals.disconnected.emit(intentional)
        client.on_error = lambda err: self._signals.log.emit(f"连接错误: {err}")
        client.on_file_info = self._session.handle_file_info
        client.on_file_data = self._session.handle_file_data
        client.on_data_ack = self._session.handle_data_ack

        self._client = client
        self._session.set_transport(client)
        client._start_heartbeat()
        threading.Thread(target=client._message_loop, daemon=True).start()

        self.reconnect_status_label.hide()
        self._signals.connected.emit("对方设备")
        self._signals.log.emit("自动重连成功！")

    def _on_reconnect_failed(self):
        self.reconnect_status_label.hide()
        self._signals.log.emit("自动重连失败")
        self._do_full_disconnect()

    def _start_server_reconnect_wait(self):
        self._update_status("等待重连...", THEME['warning'])
        self._append_log("连接断开，等待对方重连...")
        self.reconnect_status_label.setText("等待对方重连 (30s)...")
        self.reconnect_status_label.show()
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._on_reconnect_timeout)
        self._reconnect_timer.start(30000)

    def _on_reconnect_timeout(self):
        if self._server and not self._server.connected:
            self._append_log("等待重连超时")
            self._do_full_disconnect()

    def _stop_reconnect(self):
        if self._reconnect_manager:
            self._reconnect_manager.stop()
            self._reconnect_manager = None
        if self._reconnect_timer:
            self._reconnect_timer.stop()
            self._reconnect_timer = None
        self.reconnect_status_label.hide()

    def _get_device_id(self):
        from trust.device_manager import DeviceManager
        return DeviceManager().device_id

    def _update_send_progress(self, percent: int, done_files: int, total_files: int, transferred: str, total: str):
        if total_files > 1:
            self.send_progress_text.setText(f"发送中... {done_files}/{total_files} 个文件")
        else:
            self.send_progress_text.setText("发送中...")
        self.send_progress_bar.setValue(percent)
        self.send_progress_percent.setText(f"{percent}%")
        self.send_progress_size.setText(f"{transferred} / {total}")

    def _handle_send_completed(self, success: bool, message: str):
        self._append_log(message)
        self.send_progress_widget.hide()
        if success:
            self._files_to_send = []
            self._update_file_list()
        self.btn_send.setEnabled(len(self._files_to_send) > 0 and self._is_connected())

    def _handle_recv_file_info(self, file_hash: str, filename: str, size: int):
        self.recv_progress_container.show()
        self.recv_title_label.setText("接收中...")

        bar_widget = QWidget()
        bar_layout = QVBoxLayout(bar_widget)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(2)

        name_label = QLabel(filename)
        name_label.setStyleSheet(f"color: {THEME['text']}; font-size: 11px; font-weight: 600;")
        name_label.setAlignment(Qt.AlignCenter)
        bar_layout.addWidget(name_label)

        progress_bar = QProgressBar()
        progress_bar.setValue(0)
        progress_bar.setMinimumHeight(16)
        bar_layout.addWidget(progress_bar)

        size_label = QLabel("0%")
        size_label.setStyleSheet(f"color: {THEME['text_secondary']}; font-size: 10px;")
        size_label.setAlignment(Qt.AlignCenter)
        bar_layout.addWidget(size_label)

        self._recv_progress_bars[file_hash] = {
            'widget': bar_widget,
            'bar': progress_bar,
            'size_label': size_label,
            'name_label': name_label,
        }
        self.recv_bars_layout.addWidget(bar_widget, 1)

    def _update_recv_progress(self, file_hash: str, percent: int, received: str, total: str):
        info = self._recv_progress_bars.get(file_hash)
        if info:
            info['bar'].setValue(percent)
            info['size_label'].setText(f"{percent}%  {received} / {total}")

    def _handle_recv_completed(self, file_hash: str, success: bool, message: str):
        self._append_log(message)
        info = self._recv_progress_bars.pop(file_hash, None)
        if info:
            if success:
                info['bar'].setValue(100)
                info['bar'].setStyleSheet(f"""
                    QProgressBar {{ border-radius: 4px; text-align: center; background-color: {THEME['border']}; }}
                    QProgressBar::chunk {{ background-color: {THEME['success']}; border-radius: 4px; }}
                """)
                info['size_label'].setText("完成")
                info['size_label'].setStyleSheet(f"color: {THEME['success']}; font-size: 10px;")
            else:
                info['bar'].setStyleSheet(f"""
                    QProgressBar {{ border-radius: 4px; text-align: center; background-color: {THEME['border']}; }}
                    QProgressBar::chunk {{ background-color: {THEME['error']}; border-radius: 4px; }}
                """)

            QTimer.singleShot(1500, lambda w=info['widget']: self._remove_recv_bar(w))

        if not self._recv_progress_bars:
            QTimer.singleShot(2000, self._hide_recv_progress)

    def _remove_recv_bar(self, widget):
        self.recv_bars_layout.removeWidget(widget)
        widget.deleteLater()

    def _hide_recv_progress(self):
        if not self._recv_progress_bars:
            self.recv_progress_container.hide()

    def _handle_room_found(self, name: str, ip: str, pair_code: str):
        if ip in self._room_buttons:
            return
        btn = QPushButton(f"{name} ({ip})\n配对码: {pair_code}")
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME['bg']};
                color: {THEME['text']};
                border: 1px solid {THEME['border']};
                border-radius: 8px;
                padding: 12px 16px;
                text-align: left;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {THEME['primary_light']};
                border-color: {THEME['primary']};
            }}
        """)
        btn.clicked.connect(lambda checked, i=ip, c=pair_code: self._on_room_clicked(i, c))
        self._room_buttons[ip] = btn
        self.room_buttons_layout.addWidget(btn)
        self.room_list.hide()
        self.room_buttons_widget.show()

    def _on_room_clicked(self, ip: str, pair_code: str):
        self._signals.log.emit(f"正在连接到 {ip}...")
        self._stop_discovery()

        def connect_thread():
            try:
                client = LanShareClient()
                client.on_connected = lambda name: self._signals.connected.emit(name)
                client.on_disconnected = lambda intentional=False: self._signals.disconnected.emit(intentional)
                client.on_error = lambda err: self._signals.log.emit(f"连接错误: {err}")
                client.on_file_info = self._session.handle_file_info
                client.on_file_data = self._session.handle_file_data
                client.on_data_ack = self._session.handle_data_ack

                success = client.connect(ip, pair_code)
                if success:
                    self._client = client
                    self._session.set_transport(client)
                else:
                    self._signals.status_changed.emit("未连接", THEME['text_secondary'])
                    self._signals.log.emit("连接失败")
            except Exception as e:
                self._signals.status_changed.emit("未连接", THEME['text_secondary'])
                self._signals.log.emit(f"连接失败: {e}")

        threading.Thread(target=connect_thread, daemon=True).start()

    def _handle_room_expired(self, ip: str):
        btn = self._room_buttons.pop(ip, None)
        if btn:
            self.room_buttons_layout.removeWidget(btn)
            btn.deleteLater()
            if not self._room_buttons:
                self.room_buttons_widget.hide()

    # ==================== 模式切换 ====================

    def _show_idle_mode(self):
        self.btn_create.show()
        self.btn_join.show()
        self.btn_manage_devices.show()
        self.waiting_widget.hide()
        self.room_list_widget.hide()
        self.manual_widget.hide()
        self.connected_widget.hide()
        self.btn_send.setEnabled(False)

    def _show_waiting_mode(self, pair_code: str):
        self.btn_create.hide()
        self.btn_join.hide()
        self.btn_manage_devices.hide()
        self.waiting_widget.show()
        self.room_list_widget.hide()
        self.manual_widget.hide()
        self.connected_widget.hide()
        self.pair_code_label.setText(pair_code)

    def _show_connected_mode(self, peer_name: str):
        self.btn_create.hide()
        self.btn_join.hide()
        self.btn_manage_devices.hide()
        self.waiting_widget.hide()
        self.room_list_widget.hide()
        self.manual_widget.hide()
        self.connected_widget.show()
        self.connected_peer_label.setText(peer_name)
        self.btn_send.setEnabled(len(self._files_to_send) > 0)

    # ==================== 文件列表更新 ====================

    def _update_file_list(self):
        self.file_list.clear()
        for f in self._files_to_send:
            path = Path(f)
            try:
                size = path.stat().st_size
                size_str = format_size(size)
                self.file_list.append(f"{path.name} ({size_str})")
            except OSError:
                self.file_list.append(f"{path.name} (文件不存在)")
        self.btn_send.setEnabled(len(self._files_to_send) > 0 and self._is_connected())

    # ==================== 辅助方法 ====================

    def _is_connected(self):
        return self._connection_status == "已连接"

    def _get_path_size(self, path_str: str) -> int:
        p = Path(path_str)
        if p.is_file():
            return p.stat().st_size
        elif p.is_dir():
            return sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
        return 0

    def closeEvent(self, event):
        self._session.cleanup_all_state()
        self._stop_broadcast()
        self._stop_discovery()
        self._stop_reconnect()
        if self._server:
            self._server.stop()
        if self._client:
            self._client.disconnect()
        event.accept()
