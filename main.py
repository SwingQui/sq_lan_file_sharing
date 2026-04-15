#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""局域网文件共享工具 - 程序入口

业务逻辑模块：
- config: 配置管理
- file_handler: 文件处理
- network: 网络通信（server, client, discovery, protocol, reconnect）
- transfer: 文件传输（chunk_sender, chunk_receiver, state_manager)
- trust: 设备信任管理
- ui: 用户界面（PyQt原生 / Web UI)
"""
import sys
import os
import platform
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt


def check_system_compatibility():
    """
    检查系统兼容性
    Returns:
        (is_compatible, message)
    """
    system = platform.system()
    version = platform.version()

    if system == 'Windows':
        try:
            major = int(version.split('.')[0])
            minor = int(version.split('.')[1]) if '.' in version else 0

            if major < 6 or (major == 6 and minor < 1):
                return (False,
                    "此程序不支持 Windows Vista 或更早版本。\n\n"
                    "请使用 Windows 7 SP1 或更高版本。")

            elif major == 6 and minor == 1:
                return (False,
                    "此程序不支持 Windows 7。\n\n"
                    "原因：程序使用 Python 3.13 编译，需要新版 Windows API。\n"
                    "请使用 Windows 10 或 Windows 11 系统。")
            elif major == 6 and minor >= 2:
                return (False,
                    "此程序不支持 Windows 8/8.1.\n\n"
                    "原因：程序使用 Python 3.13 编译，需要新版 Windows API.\n"
                    "请使用 Windows 10 或 Windows 11 系统。")
            elif major >= 10:
                return (True, None)
        except Exception:
            return (True, None)
    return (True, None)


def main():
    """程序入口"""
    # 检查系统兼容性
    is_compatible, message = check_system_compatibility()

    if message:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

        temp_app = QApplication(sys.argv)
        if not is_compatible:
            QMessageBox.critical(None, "系统不兼容", message)
            sys.exit(1)
        else:
            result = QMessageBox.warning(
                None,
                "系统兼容性警告",
                message,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if result == QMessageBox.No:
                sys.exit(0)
        temp_app.quit()
    else:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    from file_handler import FileHandler
    FileHandler.cleanup_all_temp_files()

    # 选择 UI 模式
    # 设置环境变量 SQ_UI=web 使用 Web UI，默认使用 PyQt 原生 UI
    ui_mode = os.environ.get('SQ_UI', 'native')

    if ui_mode == 'web':
        try:
            from ui.web_window import WebMainWindow
            window = WebMainWindow()
            window.show()
        except ImportError:
            # QWebEngineWidgets 不可用时回退到原生 UI
            print("Web UI 不可用，回退到原生 UI (需要安装 PyQtWebEngine)")
            from ui.main_window import MainWindow
            window = MainWindow()
            window.show()
    else:
        from ui.main_window import MainWindow
        window = MainWindow()
        window.show()
    sys.exit(app.exec_())
if __name__ == '__main__':
    main()
