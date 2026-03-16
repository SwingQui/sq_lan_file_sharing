# Web UI 重构实现计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为局域网文件共享工具实现Web UI界面，使用PyQt5 + QWebEngineView + HTML/CSS/JavaScript

**Architecture:** 采用三层架构 - Web前端(HTML/CSS/JS) + Qt WebChannel通信层 + 业务逻辑层

**Tech Stack:** PyQt5, QWebEngineView, HTML5, CSS3, JavaScript (ES6+)

---

## 文件结构设计

在开始实现前，先规划文件结构：

```
sq_lan_file_sharing/
├── ui/                          # UI模块 (新建)
│   ├── __init__.py
│   ├── main_window.py           # 主窗口 + QWebEngineView
│   ├── bridge.py                # WebChannel桥接 (前后端通信)
│   └── web/                     # Web前端资源
│       ├── index.html           # 主页面
│       ├── styles.css           # 样式 (清新绿主题)
│       └── app.js               # 前端逻辑
├── config.py                    # 配置 (已存在)
├── file_handler.py              # 文件处理 (已存在)
├── main.py                      # 入口 (修改)
├── network/                     # 网络通信 (已存在)
│   ├── __init__.py
│   ├── client.py
│   ├── discovery.py
│   ├── protocol.py
│   ├── reconnect.py
│   └── server.py
├── transfer/                    # 文件传输 (已存在)
│   ├── __init__.py
│   ├── chunk_receiver.py
│   ├── chunk_sender.py
│   └── state_manager.py
└── trust/                       # 设备信任 (已存在)
    └── device_manager.py
```

---

## Chunk 1: 项目基础设置

### Task 1: 创建UI模块目录结构

**Files:**
- Create: `ui/__init__.py`
- Create: `ui/web/.gitkeep`

- [ ] **Step 1: 创建 ui/__init__.py**

```python
"""UI模块 - Web UI界面"""

from .main_window import MainWindow
from .bridge import UIBridge

__all__ = ['MainWindow', 'UIBridge']
```

- [ ] **Step 2: 创建 web 目录占位文件**

```bash
touch ui/web/.gitkeep
```

- [ ] **Step 3: 提交**

```bash
git add ui/
git commit -m "feat: 创建UI模块基础结构"
```

---

### Task 2: 更新 main.py 入口文件

**Files:**
- Modify: `main.py:1-109`

- [ ] **Step 1: 更新 main.py 导入和启动逻辑**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""局域网文件共享工具 - 程序入口"""
import sys
import platform
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt

from ui.main_window import MainWindow


def check_system_compatibility():
    """检查系统兼容性"""
    system = platform.system()
    version = platform.version()

    if system == 'Windows':
        try:
            major = int(version.split('.')[0])
            minor = int(version.split('.')[1]) if '.' in version else 0

            if major < 6 or (major == 6 and minor < 1):
                return (False, "此程序不支持 Windows Vista 或更早版本。")

            elif major == 6 and minor == 1:
                return (False, "此程序不支持 Windows 7。请使用 Windows 10 或 Windows 11。")

            elif major == 6 and minor >= 2:
                return (False, "此程序不支持 Windows 8/8.1。请使用 Windows 10 或 Windows 11。")

            elif major >= 10:
                return (True, None)
        except Exception:
            return (True, None)

    return (True, None)


def main():
    """程序入口"""
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
                None, "系统兼容性警告", message,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if result == QMessageBox.No:
                sys.exit(0)

        temp_app.quit()
    else:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # 创建并显示主窗口
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 测试导入**

```bash
cd /e/projects/tools/tools/sq_lan_file_sharing
python -c "from ui import MainWindow; print('OK')"
```

Expected: ImportError (因为 ui/main_window.py 还不存在) - 这是预期的

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "refactor: 更新入口文件，集成Web UI主窗口"
```

---

## Chunk 2: Web前端实现

### Task 3: 创建 HTML 主页面

**Files:**
- Create: `ui/web/index.html`

- [ ] **Step 1: 创建 index.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SQ 局域网文件共享</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <div id="app">
        <!-- 顶部状态栏 -->
        <header class="status-bar">
            <div class="status-item">
                <span class="status-label">本机IP:</span>
                <span class="status-value clickable" id="local-ip" title="点击复制">-</span>
            </div>
            <div class="status-item">
                <span class="status-label">状态:</span>
                <span class="status-value" id="connection-status">未连接</span>
            </div>
            <div class="status-item">
                <span class="status-label">对方:</span>
                <span class="status-value" id="peer-device">-</span>
            </div>
        </header>

        <!-- 主内容区 -->
        <main class="main-content">
            <!-- 连接卡片 -->
            <section class="card" id="connection-card">
                <div class="card-header">
                    <h2>连接</h2>
                </div>
                <div class="card-body">
                    <!-- 按钮行 -->
                    <div class="btn-row">
                        <button class="btn btn-primary" id="btn-create-room">创建房间</button>
                        <button class="btn btn-secondary" id="btn-join-room">加入房间</button>
                    </div>

                    <!-- 等待连接模式 -->
                    <div class="waiting-mode" id="waiting-mode" style="display: none;">
                        <div class="pair-code-display">
                            <span class="label">配对码:</span>
                            <span class="pair-code" id="display-pair-code">------</span>
                        </div>
                        <button class="btn btn-outline" id="btn-cancel-wait">取消</button>
                    </div>

                    <!-- 房间列表 (加入房间模式) -->
                    <div class="room-list-section" id="room-list-section" style="display: none;">
                        <h3>局域网房间 (双击连接)</h3>
                        <div class="room-list" id="room-list">
                            <div class="empty-tip">正在扫描...</div>
                        </div>
                    </div>

                    <!-- 手动连接 -->
                    <div class="manual-connect" id="manual-connect" style="display: none;">
                        <h3>手动连接</h3>
                        <div class="form-group">
                            <label>IP地址:</label>
                            <input type="text" id="input-ip" placeholder="例如: 192.168.1.100">
                        </div>
                        <div class="form-group">
                            <label>配对码:</label>
                            <input type="text" id="input-pair-code" placeholder="6位数字" maxlength="6">
                        </div>
                        <button class="btn btn-primary" id="btn-connect">连接</button>
                    </div>

                    <!-- 已连接模式 -->
                    <div class="connected-mode" id="connected-mode" style="display: none;">
                        <div class="connection-info">
                            <span class="label">已连接到:</span>
                            <span id="connected-peer">-</span>
                        </div>
                        <button class="btn btn-danger" id="btn-disconnect">断开连接</button>
                    </div>
                </div>
            </section>

            <!-- 文件传输卡片 -->
            <section class="card" id="transfer-card">
                <div class="card-header">
                    <h2>文件传输</h2>
                </div>
                <div class="card-body">
                    <!-- 文件列表 -->
                    <div class="file-list-section">
                        <h3>待发送文件 <span class="hint">(拖拽文件到窗口)</span></h3>
                        <div class="file-list" id="file-list">
                            <div class="empty-tip">拖拽文件或文件夹到窗口，或点击下方按钮添加</div>
                        </div>
                    </div>

                    <!-- 按钮行 -->
                    <div class="btn-row">
                        <button class="btn btn-primary" id="btn-add-files">添加文件</button>
                        <button class="btn btn-primary" id="btn-add-folder">添加文件夹</button>
                        <button class="btn btn-outline" id="btn-clear-files">清空</button>
                        <div class="spacer"></div>
                        <button class="btn btn-success" id="btn-send" disabled>发送</button>
                    </div>

                    <!-- 进度区域 -->
                    <div class="progress-section" id="progress-section" style="display: none;">
                        <div class="progress-info">
                            <div class="progress-text" id="progress-text">准备传输...</div>
                            <div class="progress-stats" id="progress-stats"></div>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" id="progress-fill" style="width: 0%"></div>
                        </div>
                        <div class="progress-details">
                            <span id="progress-percent">0%</span>
                            <span id="progress-speed">0 MB/s</span>
                            <span id="progress-size">0 / 0 MB</span>
                            <span id="progress-eta">剩余: --</span>
                        </div>
                    </div>

                    <!-- 下载目录 -->
                    <div class="download-dir-section">
                        <span class="label">下载目录:</span>
                        <span class="dir-path" id="download-dir">-</span>
                        <button class="btn btn-sm" id="btn-open-dir">打开</button>
                        <button class="btn btn-sm" id="btn-change-dir">更改</button>
                    </div>
                </div>
            </section>

            <!-- 日志卡片 -->
            <section class="card" id="log-card">
                <div class="card-header">
                    <h2>操作日志</h2>
                    <button class="btn btn-sm btn-outline" id="btn-clear-log">清空</button>
                </div>
                <div class="card-body">
                    <div class="log-list" id="log-list"></div>
                </div>
            </section>
        </main>
    </div>

    <!-- 隐藏的文件输入 -->
    <input type="file" id="file-input" multiple style="display: none;">
    <input type="file id="folder-input" webkitdirectory style="display: none;">

    <script src="qwebchannel.js"></script>
    <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 提交**

```bash
git add ui/web/index.html
git commit -m "feat: 创建Web UI主页面HTML结构"
```

---

### Task 4: 创建 CSS 样式 (清新绿主题)

**Files:**
- Create: `ui/web/styles.css`

- [ ] **Step 1: 创建 styles.css**

```css
/* ==================== 基础变量 ==================== */
:root {
    --color-primary: #10B981;
    --color-primary-hover: #059669;
    --color-primary-active: #047857;
    --color-primary-light: #D1FAE5;

    --color-bg: #F0FDF4;
    --color-bg-card: #FFFFFF;
    --color-bg-hover: #F3F4F6;
    --color-bg-border: #E5E7EB;

    --color-text: #1F2937;
    --color-text-secondary: #6B7280;
    --color-text-light: #9CA3AF;

    --color-success: #10B981;
    --color-warning: #F59E0B;
    --color-error: #EF4444;
    --color-info: #3B82F6;

    --radius: 12px;
    --radius-sm: 8px;
    --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);

    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing: 16px;
    --spacing-lg: 24px;
    --spacing-xl: 32px;
}

/* ==================== 基础样式 ==================== */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: "Microsoft YaHei", "Segoe UI", -apple-system, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    color: var(--color-text);
    background-color: var(--color-bg);
    overflow: hidden;
}

#app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    max-height: 100vh;
}

/* ==================== 状态栏 ==================== */
.status-bar {
    display: flex;
    align-items: center;
    gap: var(--spacing-xl);
    padding: var(--spacing) var(--spacing-lg);
    background-color: var(--color-bg-card);
    border-bottom: 1px solid var(--color-bg-border);
    flex-shrink: 0;
}

.status-item {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
}

.status-label {
    color: var(--color-text-secondary);
    font-weight: 600;
}

.status-value {
    font-weight: 700;
    color: var(--color-primary);
}

.status-value.clickable {
    cursor: pointer;
    transition: color 0.2s;
}

.status-value.clickable:hover {
    color: var(--color-primary-hover);
}

.status-value.disconnected {
    color: var(--color-text-secondary);
}

.status-value.connecting {
    color: var(--color-warning);
}

.status-value.connected {
    color: var(--color-success);
}

/* ==================== 主内容区 ==================== */
.main-content {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 1.2fr;
    gap: var(--spacing);
    padding: var(--spacing);
    overflow: hidden;
    min-height: 0;
}

/* ==================== 卡片 ==================== */
.card {
    background-color: var(--color-bg-card);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--spacing);
    border-bottom: 1px solid var(--color-bg-border);
    flex-shrink: 0;
}

.card-header h2 {
    font-size: 16px;
    font-weight: 700;
    color: var(--color-text);
}

.card-body {
    flex: 1;
    padding: var(--spacing);
    overflow-y: auto;
    min-height: 0;
}

.card-body h3 {
    font-size: 13px;
    font-weight: 600;
    color: var(--color-text-secondary);
    margin-bottom: var(--spacing-sm);
}

.card-body h3 .hint {
    font-weight: 400;
    color: var(--color-text-light);
}

/* ==================== 按钮 ==================== */
.btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 10px 20px;
    border: none;
    border-radius: var(--radius-sm);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    white-space: nowrap;
}

.btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.btn-primary {
    background-color: var(--color-primary);
    color: white;
}

.btn-primary:hover:not(:disabled) {
    background-color: var(--color-primary-hover);
}

.btn-primary:active:not(:disabled) {
    background-color: var(--color-primary-active);
}

.btn-secondary {
    background-color: var(--color-bg-hover);
    color: var(--color-text);
    border: 1px solid var(--color-bg-border);
}

.btn-secondary:hover:not(:disabled) {
    background-color: var(--color-bg-border);
}

.btn-success {
    background-color: var(--color-success);
    color: white;
}

.btn-success:hover:not(:disabled) {
    background-color: #059669;
}

.btn-danger {
    background-color: var(--color-error);
    color: white;
}

.btn-danger:hover:not(:disabled) {
    background-color: #DC2626;
}

.btn-outline {
    background-color: transparent;
    color: var(--color-primary);
    border: 1px solid var(--color-primary);
}

.btn-outline:hover:not(:disabled) {
    background-color: var(--color-primary-light);
}

.btn-sm {
    padding: 6px 12px;
    font-size: 12px;
}

.btn-row {
    display: flex;
    gap: var(--spacing-sm);
    margin-bottom: var(--spacing);
    flex-wrap: wrap;
}

.spacer {
    flex: 1;
}

/* ==================== 表单 ==================== */
.form-group {
    margin-bottom: var(--spacing-sm);
}

.form-group label {
    display: block;
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-secondary);
    margin-bottom: var(--spacing-xs);
}

.form-group input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--color-bg-border);
    border-radius: var(--radius-sm);
    font-size: 14px;
    transition: border-color 0.2s;
}

.form-group input:focus {
    outline: none;
    border-color: var(--color-primary);
}

/* ==================== 连接卡片 ==================== */
.waiting-mode,
.room-list-section,
.manual-connect,
.connected-mode {
    padding: var(--spacing);
    background-color: var(--color-bg);
    border-radius: var(--radius-sm);
    margin-top: var(--spacing);
}

.pair-code-display {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--spacing);
    margin-bottom: var(--spacing);
}

.pair-code-display .label {
    font-size: 14px;
    color: var(--color-text-secondary);
}

.pair-code {
    font-size: 32px;
    font-weight: 800;
    color: var(--color-primary);
    letter-spacing: 8px;
}

.room-list {
    max-height: 150px;
    overflow-y: auto;
}

.room-item {
    padding: var(--spacing-sm) var(--spacing);
    background-color: var(--color-bg-card);
    border-radius: var(--radius-sm);
    margin-bottom: var(--spacing-xs);
    cursor: pointer;
    transition: background-color 0.2s;
}

.room-item:hover {
    background-color: var(--color-primary-light);
}

.room-item .name {
    font-weight: 600;
    color: var(--color-text);
}

.room-item .info {
    font-size: 12px;
    color: var(--color-text-secondary);
}

.connection-info {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    margin-bottom: var(--spacing);
}

.connection-info .label {
    color: var(--color-text-secondary);
}

.connection-info #connected-peer {
    font-weight: 700;
    color: var(--color-success);
}

.empty-tip {
    text-align: center;
    color: var(--color-text-light);
    padding: var(--spacing);
    font-size: 13px;
}

/* ==================== 文件列表 ==================== */
.file-list-section {
    margin-bottom: var(--spacing);
}

.file-list {
    max-height: 120px;
    overflow-y: auto;
    background-color: var(--color-bg);
    border-radius: var(--radius-sm);
    padding: var(--spacing-sm);
}

.file-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--spacing-xs) var(--spacing-sm);
    background-color: var(--color-bg-card);
    border-radius: var(--radius-sm);
    margin-bottom: var(--spacing-xs);
}

.file-item .name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.file-item .size {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin-left: var(--spacing-sm);
}

.file-item .remove {
    color: var(--color-text-light);
    cursor: pointer;
    margin-left: var(--spacing-sm);
    font-size: 16px;
}

.file-item .remove:hover {
    color: var(--color-error);
}

/* ==================== 进度区域 ==================== */
.progress-section {
    background-color: var(--color-bg);
    border-radius: var(--radius-sm);
    padding: var(--spacing);
    margin-bottom: var(--spacing);
}

.progress-info {
    display: flex;
    justify-content: space-between;
    margin-bottom: var(--spacing-sm);
}

.progress-text {
    font-weight: 600;
    color: var(--color-text);
}

.progress-stats {
    font-size: 12px;
    color: var(--color-text-secondary);
}

.progress-bar {
    height: 24px;
    background-color: var(--color-bg-border);
    border-radius: var(--radius-sm);
    overflow: hidden;
    margin-bottom: var(--spacing-sm);
}

.progress-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--color-primary), #34D399);
    transition: width 0.3s;
    display: flex;
    align-items: center;
    justify-content: center;
}

.progress-details {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--color-text-secondary);
}

/* ==================== 下载目录 ==================== */
.download-dir-section {
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: var(--spacing-sm);
    background-color: var(--color-bg);
    border-radius: var(--radius-sm);
    flex-wrap: wrap;
}

.download-dir-section .label {
    color: var(--color-text-secondary);
    font-size: 13px;
}

.download-dir-section .dir-path {
    flex: 1;
    color: var(--color-text);
    font-size: 13px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* ==================== 日志卡片 ==================== */
#log-card .card-body {
    padding: 0;
}

.log-list {
    height: 100%;
    overflow-y: auto;
    padding: var(--spacing-sm);
}

.log-item {
    padding: var(--spacing-xs) var(--spacing-sm);
    font-size: 12px;
    border-bottom: 1px solid var(--color-bg-border);
}

.log-item:last-child {
    border-bottom: none;
}

.log-item .time {
    color: var(--color-text-light);
    margin-right: var(--spacing-sm);
}

.log-item .message {
    color: var(--color-text);
}

/* ==================== 滚动条 ==================== */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: var(--color-bg);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb {
    background: var(--color-bg-border);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--color-text-light);
}
```

- [ ] **Step 2: 提交**

```bash
git add ui/web/styles.css
git commit -m "feat: 创建Web UI样式 (清新绿主题)"
```

---

### Task 5: 创建前端 JavaScript 逻辑

**Files:**
- Create: `ui/web/app.js`

- [ ] **Step 1: 创建 app.js**

```javascript
/**
 * SQ 局域网文件共享 - 前端逻辑
 */

class App {
    constructor() {
        this.ui = null;
        this.files = [];
        this.currentTransfer = null;
        this.init();
    }

    async init() {
        try {
            // 等待 QWebChannel 初始化
            await this.initWebChannel();
            this.bindEvents();
            this.loadDownloadDir();
            console.log('App initialized');
        } catch (error) {
            console.error('Init failed:', error);
            this.log('初始化失败: ' + error.message);
        }
    }

    initWebChannel() {
        return new Promise((resolve, reject) => {
            if (typeof qtWebChannel === 'undefined') {
                // 开发模式或WebEngine未加载时跳过
                console.log('WebChannel not available, running in browser mode');
                resolve();
                return;
            }

            new QWebChannel(qtWebChannel, (channel) => {
                this.ui = channel.objects.ui;
                this.setupUI();
                resolve();
            });
        });
    }

    setupUI() {
        if (!this.ui) return;

        // 监听后端事件
        this.ui.ipChanged.connect((ip) => this.onIpChanged(ip));
        this.ui.statusChanged.connect((status) => this.onStatusChanged(status));
        this.ui.peerChanged.connect((name) => this.onPeerChanged(name));
        this.ui.roomDiscovered.connect((name, ip, code) => this.onRoomDiscovered(name, ip, code));
        this.ui.roomRemoved.connect((ip) => this.onRoomRemoved(ip));
        this.ui.progressUpdated.connect((percent, speed, transferred, total, eta) =>
            this.onProgressUpdated(percent, speed, transferred, total, eta));
        this.ui.transferCompleted.connect((success, message) =>
            this.onTransferCompleted(success, message));
        this.ui.logMessage.connect((message) => this.log(message));

        // 初始化请求
        this.ui.requestLocalIp();
    }

    // ==================== 事件绑定 ====================
    bindEvents() {
        // 创建/加入房间
        document.getElementById('btn-create-room')?.addEventListener('click', () => this.createRoom());
        document.getElementById('btn-join-room')?.addEventListener('click', () => this.showJoinMode());
        document.getElementById('btn-cancel-wait')?.addEventListener('click', () => this.cancelWait());
        document.getElementById('btn-connect')?.addEventListener('click', () => this.manualConnect());
        document.getElementById('btn-disconnect')?.addEventListener('click', () => this.disconnect());

        // 文件操作
        document.getElementById('btn-add-files')?.addEventListener('click', () => this.addFiles());
        document.getElementById('btn-add-folder')?.addEventListener('click', () => this.addFolder());
        document.getElementById('btn-clear-files')?.addEventListener('click', () => this.clearFiles());
        document.getElementById('btn-send')?.addEventListener('click', () => this.sendFiles());

        // 下载目录
        document.getElementById('btn-open-dir')?.addEventListener('click', () => this.openDownloadDir());
        document.getElementById('btn-change-dir')?.addEventListener('click', () => this.changeDownloadDir());

        // 日志
        document.getElementById('btn-clear-log')?.addEventListener('click', () => this.clearLog());

        // IP点击复制
        document.getElementById('local-ip')?.addEventListener('click', () => this.copyIp());

        // 文件输入
        document.getElementById('file-input')?.addEventListener('change', (e) => this.onFilesSelected(e));
        document.getElementById('folder-input')?.addEventListener('change', (e) => this.onFolderSelected(e));

        // 拖拽
        this.setupDragDrop();
    }

    // ==================== 连接相关 ====================
    createRoom() {
        this.log('正在创建房间...');
        if (this.ui) {
            this.ui.createRoom();
        } else {
            this.log('模拟: 创建房间 (Web模式)');
            this.showWaitingMode('123456');
        }
    }

    showJoinMode() {
        document.getElementById('waiting-mode').style.display = 'none';
        document.getElementById('room-list-section').style.display = 'block';
        document.getElementById('manual-connect').style.display = 'block';
        document.getElementById('connected-mode').style.display = 'none';
        this.log('显示加入房间模式');

        if (this.ui) {
            this.ui.startDiscovery();
        }
    }

    cancelWait() {
        this.showIdleMode();
        if (this.ui) {
            this.ui.cancelCreateRoom();
        }
    }

    manualConnect() {
        const ip = document.getElementById('input-ip')?.value.trim();
        const code = document.getElementById('input-pair-code')?.value.trim().toUpperCase();

        if (!ip || !code) {
            this.log('请输入IP地址和配对码');
            return;
        }

        this.log(`正在连接到 ${ip}...`);
        if (this.ui) {
            this.ui.connectToRoom(ip, code);
        } else {
            this.log('模拟: 连接 ' + ip + ' ' + code);
            this.showConnectedMode('测试设备');
        }
    }

    disconnect() {
        this.log('断开连接');
        if (this.ui) {
            this.ui.disconnect();
        }
        this.showIdleMode();
    }

    // ==================== 文件相关 ====================
    addFiles() {
        document.getElementById('file-input')?.click();
    }

    addFolder() {
        document.getElementById('folder-input')?.click();
    }

    onFilesSelected(event) {
        const files = event.target.files;
        if (files.length > 0) {
            for (const file of files) {
                this.addFile({
                    name: file.name,
                    size: file.size,
                    path: file.path || file.name
                });
            }
        }
        event.target.value = '';
    }

    onFolderSelected(event) {
        const files = event.target.files;
        if (files.length > 0) {
            for (const file of files) {
                this.addFile({
                    name: file.name,
                    size: file.size,
                    path: file.path || file.webkitRelativePath || file.name
                });
            }
        }
        event.target.value = '';
    }

    addFile(file) {
        // 检查是否已存在
        if (this.files.some(f => f.path === file.path)) {
            return;
        }
        this.files.push(file);
        this.renderFileList();
        this.updateSendButton();
    }

    removeFile(index) {
        this.files.splice(index, 1);
        this.renderFileList();
        this.updateSendButton();
    }

    clearFiles() {
        this.files = [];
        this.renderFileList();
        this.updateSendButton();
    }

    renderFileList() {
        const container = document.getElementById('file-list');
        if (!container) return;

        if (this.files.length === 0) {
            container.innerHTML = '<div class="empty-tip">拖拽文件或文件夹到窗口，或点击下方按钮添加</div>';
            return;
        }

        container.innerHTML = this.files.map((file, index) => `
            <div class="file-item">
                <span class="name" title="${file.path}">${file.name}</span>
                <span class="size">${this.formatSize(file.size)}</span>
                <span class="remove" onclick="app.removeFile(${index})">&times;</span>
            </div>
        `).join('');
    }

    updateSendButton() {
        const btn = document.getElementById('btn-send');
        if (btn) {
            btn.disabled = this.files.length === 0;
        }
    }

    sendFiles() {
        if (this.files.length === 0) return;

        const paths = this.files.map(f => f.path);
        this.log(`开始发送 ${this.files.length} 个文件...`);

        if (this.ui) {
            this.ui.sendFiles(paths);
        } else {
            this.log('模拟: 开始发送文件');
            this.simulateTransfer();
        }
    }

    simulateTransfer() {
        let percent = 0;
        const interval = setInterval(() => {
            percent += 5;
            this.onProgressUpdated(percent, '2.5 MB/s', '25 MB', '500 MB', '3分钟');
            if (percent >= 100) {
                clearInterval(interval);
                this.onTransferCompleted(true, '传输完成');
            }
        }, 200);
    }

    // ==================== 下载目录 ====================
    loadDownloadDir() {
        if (this.ui) {
            this.ui.getDownloadDir((dir) => {
                document.getElementById('download-dir').textContent = dir;
            });
        } else {
            document.getElementById('download-dir').textContent = 'C:\\Users\\Public\\Downloads';
        }
    }

    openDownloadDir() {
        if (this.ui) {
            this.ui.openDownloadDir();
        } else {
            this.log('模拟: 打开下载目录');
        }
    }

    changeDownloadDir() {
        if (this.ui) {
            this.ui.changeDownloadDir((dir) => {
                document.getElementById('download-dir').textContent = dir;
            });
        } else {
            this.log('模拟: 选择下载目录');
        }
    }

    // ==================== 拖拽 ====================
    setupDragDrop() {
        const dropZone = document.body;

        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();

            const files = e.dataTransfer.files;
            if (files.length > 0) {
                for (const file of files) {
                    this.addFile({
                        name: file.name,
                        size: file.size,
                        path: file.path || file.name
                    });
                }
            }
        });
    }

    // ==================== 状态显示 ====================
    showIdleMode() {
        document.querySelector('.btn-row').style.display = 'flex';
        document.getElementById('waiting-mode').style.display = 'none';
        document.getElementById('room-list-section').style.display = 'none';
        document.getElementById('manual-connect').style.display = 'none';
        document.getElementById('connected-mode').style.display = 'none';
    }

    showWaitingMode(pairCode) {
        document.querySelector('.btn-row').style.display = 'none';
        document.getElementById('waiting-mode').style.display = 'block';
        document.getElementById('room-list-section').style.display = 'none';
        document.getElementById('manual-connect').style.display = 'none';
        document.getElementById('connected-mode').style.display = 'none';
        document.getElementById('display-pair-code').textContent = pairCode;
    }

    showConnectedMode(peerName) {
        document.querySelector('.btn-row').style.display = 'none';
        document.getElementById('waiting-mode').style.display = 'none';
        document.getElementById('room-list-section').style.display = 'none';
        document.getElementById('manual-connect').style.display = 'none';
        document.getElementById('connected-mode').style.display = 'block';
        document.getElementById('connected-peer').textContent = peerName;
    }

    // ==================== 后端回调 ====================
    onIpChanged(ip) {
        const el = document.getElementById('local-ip');
        if (el) el.textContent = ip || '-';
    }

    onStatusChanged(status) {
        const el = document.getElementById('connection-status');
        if (!el) return;

        el.textContent = status;
        el.className = 'status-value';

        if (status === '未连接') {
            el.classList.add('disconnected');
            this.showIdleMode();
        } else if (status === '等待连接' || status === '连接中') {
            el.classList.add('connecting');
        } else if (status === '已连接') {
            el.classList.add('connected');
        }
    }

    onPeerChanged(name) {
        const el = document.getElementById('peer-device');
        if (el) el.textContent = name || '-';
    }

    onRoomDiscovered(name, ip, code) {
        const list = document.getElementById('room-list');
        if (!list) return;

        // 检查是否已存在
        const existing = list.querySelector(`[data-ip="${ip}"]`);
        if (existing) {
            existing.querySelector('.info').textContent = `${ip} - ${code}`;
            return;
        }

        const item = document.createElement('div');
        item.className = 'room-item';
        item.dataset.ip = ip;
        item.innerHTML = `
            <div class="name">${name}</div>
            <div class="info">${ip} - ${code}</div>
        `;
        item.addEventListener('dblclick', () => {
            if (this.ui) {
                this.ui.connectToRoom(ip, code);
            }
        });
        list.appendChild(item);
    }

    onRoomRemoved(ip) {
        const item = document.querySelector(`[data-ip="${ip}"]`);
        if (item) item.remove();
    }

    onProgressUpdated(percent, speed, transferred, total, eta) {
        document.getElementById('progress-section').style.display = 'block';
        document.getElementById('progress-fill').style.width = percent + '%';
        document.getElementById('progress-percent').textContent = percent + '%';
        document.getElementById('progress-speed').textContent = speed;
        document.getElementById('progress-size').textContent = transferred + ' / ' + total;
        document.getElementById('progress-eta').textContent = '剩余: ' + eta;
    }

    onTransferCompleted(success, message) {
        this.log(message);
        if (success) {
            document.getElementById('progress-section').style.display = 'none';
            this.clearFiles();
        }
    }

    // ==================== 工具 ====================
    log(message) {
        const list = document.getElementById('log-list');
        if (!list) return;

        const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
        const item = document.createElement('div');
        item.className = 'log-item';
        item.innerHTML = `<span class="time">${time}</span><span class="message">${message}</span>`;
        list.appendChild(item);
        list.scrollTop = list.scrollHeight;
    }

    clearLog() {
        const list = document.getElementById('log-list');
        if (list) list.innerHTML = '';
    }

    copyIp() {
        const ip = document.getElementById('local-ip')?.textContent;
        if (ip && ip !== '-') {
            navigator.clipboard.writeText(ip);
            this.log('IP地址已复制到剪贴板');
        }
    }

    formatSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }
}

// 初始化应用
const app = new App();
```

- [ ] **Step 2: 提交**

```bash
git add ui/web/app.js
git commit -m "feat: 创建Web UI前端逻辑"
```

---

## Chunk 3: PyQt 后端实现

### Task 6: 创建 WebChannel 桥接

**Files:**
- Create: `ui/bridge.py`

- [ ] **Step 1: 创建 bridge.py**

```python
"""WebChannel桥接 - 前后端通信"""
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebChannel import QWebChannel

from config import DEFAULT_DOWNLOAD_DIR
import os


class UIBridge(QObject):
    """UI桥接对象 - 暴露给前端调用"""

    # 信号 - 后端推送到前端
    ipChanged = pyqtSignal(str)
    statusChanged = pyqtSignal(str)
    peerChanged = pyqtSignal(str)
    roomDiscovered = pyqtSignal(str, str, str)  # name, ip, pair_code
    roomRemoved = pyqtSignal(str)
    progressUpdated = pyqtSignal(int, str, str, str, str)  # percent, speed, transferred, total, eta
    transferCompleted = pyqtSignal(bool, str)
    logMessage = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._local_ip = ""
        self._connection_status = "未连接"
        self._peer_name = ""
        self._download_dir = DEFAULT_DOWNLOAD_DIR
        self._server = None
        self._client = None

    # ==================== 前端调用后端 ====================

    @pyqtSlot()
    def requestLocalIp(self):
        """前端请求本机IP"""
        import socket
        try:
            # 获取本机IP地址
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
        from network.server import LanShareServer
        import random
        import string

        # 生成配对码
        pair_code = ''.join(random.choices(string.digits, k=6))

        # 创建服务器
        try:
            self._server = LanShareServer(
                pair_code=pair_code,
                on_client_connected=self._on_client_connected,
                on_client_disconnected=self._on_client_disconnected
            )
            self._server.start()

            self._connection_status = "等待连接"
            self.statusChanged.emit(self._connection_status)

            # 广播房间
            self._start_broadcast(pair_code)

            self.logMessage.emit(f"已创建房间，配对码: {pair_code}")
        except Exception as e:
            self.logMessage.emit(f"创建房间失败: {e}")
            self._server = None

    @pyqtSlot(str, str)
    def connectToRoom(self, ip: str, pair_code: str):
        """连接到房间"""
        from network.client import LanShareClient

        self._connection_status = "连接中"
        self.statusChanged.emit(self._connection_status)

        try:
            self._client = LanShareClient(
                server_ip=ip,
                pair_code=pair_code,
                on_connected=self._on_connected,
                on_disconnected=self._on_disconnected
            )
            self._client.connect()

            self.logMessage.emit(f"正在连接到 {ip}...")
        except Exception as e:
            self.logMessage.emit(f"连接失败: {e}")
            self._connection_status = "未连接"
            self.statusChanged.emit(self._connection_status)

    @pyqtSlot()
    def disconnect(self):
        """断开连接"""
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
        from network.discovery import DiscoveryClient

        def on_room_found(name, ip, code):
            self.roomDiscovered.emit(name, ip, code)

        def on_room_lost(ip):
            self.roomRemoved.emit(ip)

        self._discovery = DiscoveryClient(
            on_room_found=on_room_found,
            on_room_lost=on_room_lost
        )
        self._discovery.start()
        self.logMessage.emit("开始扫描局域网房间...")

    @pyqtSlot()
    def cancelCreateRoom(self):
        """取消创建房间"""
        if self._server:
            self._server.stop()
            self._server = None
        self._connection_status = "未连接"
        self.statusChanged.emit(self._connection_status)
        self.logMessage.emit("已取消创建房间")

    @pyqtSlot(list)
    def sendFiles(self, file_paths: list):
        """发送文件"""
        if not self._client or self._connection_status != "已连接":
            self.logMessage.emit("未连接到对方设备")
            return

        from transfer.chunk_sender import ChunkedFileSender

        def on_progress(percent, speed, transferred, total, eta):
            self.progressUpdated.emit(percent, speed, transferred, total, eta)

        def on_complete(success, message):
            self.transferCompleted.emit(success, message)

        sender = ChunkedFileSender(
            client=self._client,
            file_paths=file_paths,
            on_progress=on_progress,
            on_complete=on_complete
        )
        sender.start()

        self.logMessage.emit(f"开始发送 {len(file_paths)} 个文件")

    @pyqtSlot()
    def getDownloadDir(self):
        """获取下载目录 - 回调给前端"""
        return self._download_dir

    @pyqtSlot()
    def openDownloadDir(self):
        """打开下载目录"""
        import subprocess
        try:
            subprocess.Popen(f'explorer "{self._download_dir}"')
        except Exception as e:
            self.logMessage.emit(f"打开目录失败: {e}")

    @pyqtSlot()
    def changeDownloadDir(self):
        """更改下载目录"""
        # TODO: 实现目录选择对话框
        pass

    # ==================== 内部回调 ====================

    def _on_client_connected(self, client_socket, peer_name):
        """客户端连接回调"""
        self._connection_status = "已连接"
        self._peer_name = peer_name
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit(peer_name)
        self.logMessage.emit(f"已连接到: {peer_name}")

    def _on_client_disconnected(self):
        """客户端断开回调"""
        self._connection_status = "未连接"
        self._peer_name = ""
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit("-")
        self.logMessage.emit("对方已断开连接")

    def _on_connected(self, peer_name):
        """连接成功回调"""
        self._connection_status = "已连接"
        self._peer_name = peer_name
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit(peer_name)
        self.logMessage.emit(f"已连接到: {peer_name}")

    def _on_disconnected(self):
        """断开连接回调"""
        self._connection_status = "未连接"
        self._peer_name = ""
        self.statusChanged.emit(self._connection_status)
        self.peerChanged.emit("-")
        self.logMessage.emit("连接已断开")

    def _start_broadcast(self, pair_code):
        """开始广播房间"""
        from network.discovery import RoomBroadcaster
        import platform

        hostname = platform.node()
        self._broadcaster = RoomBroadcaster(
            hostname=hostname,
            pair_code=pair_code,
            port=9527
        )
        self._broadcaster.start()
```

- [ ] **Step 2: 提交**

```bash
git add ui/bridge.py
git commit -m "feat: 创建WebChannel桥接"
```

---

### Task 7: 创建主窗口

**Files:**
- Create: `ui/main_window.py`

- [ ] **Step 1: 创建 main_window.py**

```python
"""主窗口 - Web UI容器"""
import os
from pathlib import Path

from PyQt5.QtWidgets import QMainWindow, QMessageBox
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import QUrl, pyqtSlot

from ui.bridge import UIBridge


class MainWindow(QMainWindow):
    """主窗口 - 承载Web UI"""

    def __init__(self):
        super().__init__()
        self._init_ui()
        self._init_web_engine()

    def _init_ui(self):
        """初始化UI"""
        self.setWindowTitle("SQ 局域网文件共享")
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)

        # 尝试居中显示
        self._center_window()

    def _center_window(self):
        """窗口居中"""
        from PyQt5.QtWidgets import QDesktopWidget
        screen = QDesktopWidget().screenGeometry()
        size = self.geometry()
        self.move(
            (screen.width() - size.width()) // 2,
            (screen.height() - size.height()) // 2
        )

    def _init_web_engine(self):
        """初始化Web引擎"""
        # 创建Web视图
        self.web_view = QWebEngineView(self)
        self.setCentralWidget(self.web_view)

        # 创建WebChannel
        self.channel = QWebChannel()
        self.ui_bridge = UIBridge()
        self.channel.registerObject('ui', self.ui_bridge)
        self.web_view.page().setWebChannel(self.channel)

        # 加载HTML
        self._load_html()

        # 设置WebEngine配置
        self._configure_web_engine()

    def _load_html(self):
        """加载HTML页面"""
        # 获取web目录路径
        base_dir = Path(__file__).parent
        web_dir = base_dir / 'web'
        index_file = web_dir / 'index.html'

        if index_file.exists():
            url = QUrl.fromLocalFile(str(index_file.absolute()))
            self.web_view.setUrl(url)
            print(f"Loading: {index_file}")
        else:
            # 开发模式：显示错误
            self._show_error(f"找不到HTML文件: {index_file}")

    def _configure_web_engine(self):
        """配置Web引擎"""
        # 启用开发者工具（可选）
        settings = self.web_view.page().settings()
        settings.setAttribute(
            settings.LocalContentCanAccessFileUrls, True
        )
        settings.setAttribute(
            settings.LocalContentCanAccessRemoteUrls, True
        )

    def _show_error(self, message: str):
        """显示错误页面"""
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Error</title>
            <style>
                body {{
                    font-family: Microsoft YaHei, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background: #f5f5f5;
                }}
                .error {{
                    text-align: center;
                    color: #666;
                }}
                h1 {{ color: #e74c3c; }}
            </style>
        </head>
        <body>
            <div class="error">
                <h1>页面加载失败</h1>
                <p>{message}</p>
            </div>
        </body>
        </html>
        """
        self.web_view.setHtml(html)

    def closeEvent(self, event):
        """窗口关闭事件"""
        # 清理资源
        try:
            if hasattr(self.ui_bridge, '_server') and self.ui_bridge._server:
                self.ui_bridge._server.stop()
            if hasattr(self.ui_bridge, '_client') and self.ui_bridge._client:
                self.ui_bridge._client.disconnect()
            if hasattr(self.ui_bridge, '_broadcaster') and self.ui_bridge._broadcaster:
                self.ui_bridge._broadcaster.stop()
            if hasattr(self.ui_bridge, '_discovery') and self.ui_bridge._discovery:
                self.ui_bridge._discovery.stop()
        except Exception as e:
            print(f"Cleanup error: {e}")

        event.accept()
```

- [ ] **Step 2: 检查是否需要额外依赖**

```bash
pip show PyQtWebEngine 2>/dev/null || echo "PyQtWebEngine not installed"
```

如果未安装，需要在 requirements.txt 添加:
```
PyQtWebEngine>=5.15
```

- [ ] **Step 3: 提交**

```bash
git add ui/main_window.py
git commit -m "feat: 创建Web UI主窗口"
```

---

### Task 8: 创建 QWebChannel JS文件

**Files:**
- Create: `ui/web/qwebchannel.js`

- [ ] **Step 1: 创建 qwebchannel.js**

这个文件是 Qt 官方的 QWebChannel JavaScript 库，需要从Qt安装目录复制或下载。

```javascript
// QWebChannel JavaScript Library
// From Qt framework - qtwebchannel/qwebchannel.js

/****************************************************************************
**
** Copyright (C) 2016 The Qt Company Ltd.
** Contact: https://www.qt.io/licensing/
**
** This file is part of the Qt WebChannel module.
**
** $QT_BEGIN_LICENSE:LGPL$
** Commercial License Usage
** Licensees holding valid commercial Qt licenses may use this file in
** accordance with the commercial license agreement provided with the
** Software or, alternatively, in accordance with the terms contained in
** a written agreement between you and The Qt Company. For licensing terms
** and conditions see https://www.qt.io/terms-conditions. For further
** information use the contact form at https://www.qt.io/contact-us.
**
** GNU Lesser General Public License Usage
** Alternatively, this file may be used under the terms of the GNU Lesser
** General Public License version 3 as published by the Free Software
** Foundation and appearing in the file LICENSE.LGPL3 included in the
** packaging of this file. Please review the following information to
** ensure the GNU Lesser General Public License version 3 requirements
** will be met: https://www.gnu.org/licenses/lgpl-3.0.html.
**
** $QT_END_LICENSE$
**
****************************************************************************/

var QWebChannelMessageTypes = {
    signal: 1,
    propertyUpdate: 2,
    init: 3,
    idle: 4,
    debug: 5,
    invokeMethod: 6,
    connectSignal: 7,
    disconnectSignal: 8,
    setProperty: 9,
    response: 10
};

var QWebChannel = function(transport, initCallback)
{
    if (typeof transport === "undefined") {
        var scriptPaths = document.getElementsByTagName("script");
        // this will only work if at least one script tag is present
        if (scriptPaths.length > 0) {
            // account for script path being directory
            var scriptPath = scriptPaths[scriptPaths.length - 1].src;
            var path = scriptPath.substring(0, scriptPath.lastIndexOf("/"));
            // explicitly require the qwebchannel.js file
            var basePath = path + "/";
            require(path + "/qwebchannel.js");
        }
    }

    this.transport = transport;
    this.initCallback = initCallback;
    this.objects = {};

    this._updatePendingRequestNum = 0;

    this._resetState();

    transport.messageReceived.connect(this._handleMessage.bind(this));
};

QWebChannel.prototype._resetState = function()
{
    this._responseStack = [];
    this._objectIdToObject = { "1": this };
    this._objectPathToId = { "": 1 };
};

QWebChannel.prototype._handleMessage = function(message)
{
    var messageJson = typeof message.data === "string" ? JSON.parse(message.data) : message.data;
    var type = messageJson.type;
    var data = messageJson.data;

    switch (type) {
        case QWebChannelMessageTypes.signal:
            this._handleSignal(data);
            break;
        case QWebChannelMessageTypes.response:
            this._handleResponse(data);
            break;
        case QWebChannelMessageTypes.propertyUpdate:
            this._handlePropertyUpdate(data);
            break;
        case QWebChannelMessageTypes.debug:
            console.debug.apply(console, data);
            break;
        case QWebChannelMessageTypes.invokeMethod:
            this._handleInvokeMethod(data);
            break;
        case QWebChannelMessageTypes.init:
            this._handleInit(data);
            break;
        case QWebChannelMessageTypes.idle:
            break;
        default:
            console.error("invalid message type:", type);
    }
};

QWebChannel.prototype._handleSignal = function(message)
{
    var object = this._objectIdToObject[message.object];
    var signalName = message.signal;
    var signalParams = message.params;

    if (!object) {
        console.error("cannot find object for signal:", message.object);
        return;
    }
    var signal = object[signalName];
    if (!signal) {
        console.error("cannot find signal '" + signalName + "' on object '" + object + "'");
        return;
    }

    // because signal connected to QObject we get params already in qt style
    // (QVariant, QList, QMap, ... as plain JavaScript values)
    signal.apply(object, signalParams);
};

QWebChannel.prototype._handlePropertyUpdate = function(message)
{
    for (var i = 0; i < message.length; ++i) {
        var objectId = message[i].object;
        var changedProperties = message[i].props;
        var object = this._objectIdToObject[objectId];

        if (!object) {
            console.error("cannot find object for property update:", objectId);
            return;
        }

        for (var propertyName in changedProperties) {
            var propertyValue = changedProperties[propertyName];
            object[propertyName] = propertyValue;
        }
    }
    if (this._updatePendingRequestNum > 0) {
        --this._updatePendingRequestNum;
    }
};

QWebChannel.prototype._handleInit = function(data)
{
    for (var i = 0; i < data.length; ++i) {
        var objectDescription = data[i];
        this._createObject(objectDescription);
    }

    // now send the pending responses, because they might trigger further init signals
    this._handleResponse();

    if (this.initCallback) {
        this.initCallback(this);
    }
};

QWebChannel.prototype._handleInvokeMethod = function(message)
{
    var requestId = message.id;
    var objectId = message.object;
    var method = message.method;
    var params = message.params;

    var object = this._objectIdToObject[objectId];
    if (!object) {
        console.error("cannot find object for method call:", objectId);
        return;
    }

    if (!object[method]) {
        console.error("cannot find method '" + method + "' on object '" + object + "'");
        return;
    }

    var responseCallback = (function() {
        var id = requestId;
        return function(returnValue) {
            this.transport.send({ type: QWebChannelMessageTypes.response, id: id, data: returnValue });
        };
    })();

    var args = [responseCallback].concat(params);
    object[method].apply(object, args);
};

QWebChannel.prototype._handleResponse = function(data)
{
    if (!data) {
        data = [];
    }
    for (var i = 0; i < data.length; ++i) {
        var request = this._responseStack.shift();
        if (!request) {
            console.error("Received response message without matching request", i, data.length);
            continue;
        }

        if (data[i] !== undefined) {
            request.callback.apply(null, [data[i]]);
        } else if (request.errorCallback) {
            request.errorCallback();
        }
    }
};

QWebChannel.prototype._createObject = function(objectDescription)
{
    var id = objectDescription[0];
    var qobject = objectDescription[1];
    var methods = objectDescription[2];
    var properties = objectDescription[3];
    var signals = objectDescription[4];
    var className = objectDescription[5];

    var qpyqtObject = new QObject(id, this);
    this._objectIdToObject[id] = qpyqtObject;
    this._objectPathToId[className] = id;

    // parse methods
    for (var methodName in methods) {
        var methodDesc = methods[methodName];
        var jsName = methodDesc[0];
        var slotName = methodDesc[1];
        var returnType = methodDesc[2];
        var params = methodDesc[3];

        qpyqtObject[jsName] = this._createMethod(qpyqtObject, slotName, params);
    }

    // parse properties
    for (var propertyName in properties) {
        var propertyDesc = properties[propertyName];
        var jsName = propertyDesc[0];
        var notifySignal = propertyDesc[1];
        var propertyType = propertyDesc[2];

        qpyqtObject[jsName] = undefined;
        qpyqtObject["__propertyChanged_" + jsName] = propertyDesc;

        // create the property getter and setter
        Object.defineProperty(qpyqtObject, jsName, {
            get: (function(name) {
                return function() {
                    return this["__property_" + name];
                };
            })(jsName),
            set: (function(name) {
                return function(value) {
                    this.__propertyChanged_signal[name].connect(this.__propertySetter[name]);
                    this["__property_" + name] = value;
                };
            })(jsName),
            configurable: true
        });
    }

    // parse signals
    for (var signalName in signals) {
        var signalDesc = signals[signalName];
        var signalParams = signalDesc[1];
        qpyqtObject[signalDesc[0]] = this._createSignal(qpyqtObject, signalParams);
    }

    return qpyqtObject;
};

QWebChannel.prototype._createMethod = function(qobject, name, params)
{
    var self = this;
    return function() {
        var args = Array.prototype.slice.call(arguments);
        var callback = args.length > 0 && args[0] instanceof Function ? args.shift() : (function() {});
        var errorCallback = args.length > 0 && args[0] instanceof Function ? args.shift() : (function() {});

        var methodId = self._objectPathToId[qobject.__id__] + "." + name;
        var request = { callback: callback, errorCallback: errorCallback, methodId: methodId };
        self._responseStack.push(request);

        self.transport.send({
            type: QWebChannelMessageTypes.invokeMethod,
            id: request.id,
            object: qobject.__id__,
            method: name,
            params: args
        });
    };
};

QWebChannel.prototype._createSignal = function(qobject, params)
{
    var signal = function() {
        var args = Array.prototype.slice.call(arguments);
        signal.signalEmitted.apply(signal, args);
    };

    signal.signalEmitted = function() {
        var args = Array.prototype.slice.call(arguments);
        this._qwebChannel.transport.send({
            type: QWebChannelMessageTypes.signal,
            object: qobject.__id__,
            signal: qobject.__id__ + "." + this._signalName,
            params: args
        });
    };

    return signal;
};

function QObject(id, webChannel)
{
    this.__id__ = id;
    this._qwebChannel = webChannel;
}

// default implementation for the signal wrapper that is added as dynamic property
QObject.prototype.__signalHelper = function() {};

// here we will store the signal wrappers
QObject.prototype.__propertyChanged_signal = {};
QObject.prototype.__propertySetter = {};

// and we will add a generic getter/setter for each property
QObject.prototype.__propertyChanged_helper = function(name, value) {
    if (value === undefined) {
        return this["__property_" + name];
    } else {
        this["__property_" + name] = value;
        var property = this["__propertyChanged_" + name];
        if (property && property[0] !== undefined) {
            var signal = this[property[0]];
            if (signal) {
                signal.signalEmitted(value);
            }
        }
    }
};

// Expose to global scope for use without module loader
window.QWebChannel = QWebChannel;
window.qtWebChannel = undefined; // will be set in the HTML when available
```

- [ ] **Step 2: 提交**

```bash
git add ui/web/qwebchannel.js
git commit -m "feat: 添加QWebChannel JavaScript库"
```

---

## Chunk 4: 集成测试

### Task 9: 测试运行

**Files:**
- Test: `python main.py`

- [ ] **Step 1: 检查依赖**

```bash
cd /e/projects/tools/tools/sq_lan_file_sharing
pip show PyQtWebEngine 2>/dev/null || pip install PyQtWebEngine
```

- [ ] **Step 2: 运行应用**

```bash
python main.py
```

Expected: 显示Web UI窗口

- [ ] **Step 3: 提交**

```bash
git status
git add -A
git commit -m "feat: 完成Web UI重构

- 添加UI模块结构
- 创建Web前端(HTML/CSS/JS)
- 实现QWebChannel桥接
- 创建PyQt主窗口
- 集成业务逻辑层"
```

---

## 验证清单

完成所有任务后检查：

- [ ] 窗口默认大小 1000x700，最小 900x650
- [ ] 状态栏显示IP、状态、对方设备
- [ ] 连接卡片支持创建/加入房间
- [ ] 传输卡片支持文件添加、发送
- [ ] 进度显示完整信息
- [ ] 日志显示操作记录
- [ ] 清新绿主题样式正确

---

## Plan Complete

Plan complete and saved to `docs/superpowers/plans/2026-03-16-web-ui-implementation.md`. Ready to execute?