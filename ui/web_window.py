"""Web UI 主窗口 - 使用 QWebEngineView"""
import os
from pathlib import Path

from PyQt5.QtWidgets import QMainWindow
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel

from ui.bridge import UIBridge


class WebMainWindow(QMainWindow):
    """Web UI 主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SQ 局域网文件共享")
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)
        self._center_window()

        # 创建 WebEngineView
        self.web_view = QWebEngineView(self)
        self.setCentralWidget(self.web_view)

        # 创建 WebChannel 和桥接对象
        self.channel = QWebChannel(self)
        self.bridge = UIBridge(self)
        self.channel.registerObject('ui', self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        # 加载页面
        web_dir = Path(__file__).parent / 'web'
        index_path = web_dir / 'index.html'
        self.web_view.setUrl(QUrl.fromLocalFile(str(index_path)))

    def _center_window(self):
        from PyQt5.QtWidgets import QDesktopWidget
        screen = QDesktopWidget().screenGeometry()
        size = self.geometry()
        self.move(
            (screen.width() - size.width()) // 2,
            (screen.height() - size.height()) // 2
        )

    def closeEvent(self, event):
        self.bridge.cleanup()
        event.accept()
