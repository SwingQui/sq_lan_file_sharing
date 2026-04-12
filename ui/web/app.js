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