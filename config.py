"""配置管理模块"""
import os
import sys
from pathlib import Path


def get_base_dir() -> Path:
    """获取基础目录（exe所在目录或脚本目录）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的 exe
        return Path(sys.executable).parent
    else:
        # 开发环境，使用脚本目录
        return Path(__file__).parent


def get_app_dir() -> Path:
    """获取应用程序数据目录（SQLanFileShare 文件夹）"""
    base_dir = get_base_dir()
    app_dir = base_dir / 'SQLanFileShare'
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_default_download_dir() -> str:
    """获取默认下载目录（SQLanFileShare 目录下的 Downloads 文件夹）"""
    app_dir = get_app_dir()
    downloads = app_dir / 'Downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    return str(downloads)


# 基础目录（exe所在目录）
BASE_DIR = get_base_dir()

# 应用程序数据目录（SQLanFileShare 文件夹）
APP_DIR = get_app_dir()

# 下载目录
DEFAULT_DOWNLOAD_DIR = get_default_download_dir()

# LAN Share 数据目录（放在 SQLanFileShare 目录下）
LAN_SHARE_DIR = APP_DIR / 'data'
LAN_SHARE_DIR.mkdir(parents=True, exist_ok=True)

# 临时文件目录
TEMP_DIR = LAN_SHARE_DIR / 'temp'
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 网络配置
DEFAULT_PORT = 9527
BUFFER_SIZE = 64 * 1024  # 64KB

# 配对码配置
PAIR_CODE_LENGTH = 6

# 传输配置
CHUNK_SIZE = 64 * 1024        # 64KB 块大小
ACK_TIMEOUT = 60              # ACK 超时秒数
MAX_RETRY = 3                 # 最大重试次数

# 重连配置
RECONNECT_INTERVAL = 5        # 重连间隔秒数
MAX_RECONNECT_ATTEMPTS = 5    # 最大重连次数

# 心跳配置
HEARTBEAT_INTERVAL = 10       # 心跳间隔秒数
HEARTBEAT_TIMEOUT = 30        # 心跳超时秒数

# 状态同步配置
STATE_SYNC_INTERVAL = 5       # 状态同步间隔秒数
CHUNKS_PER_SYNC = 50          # 每多少块同步一次状态

# UDP 发现配置
DISCOVERY_PORT = 9528         # UDP 发现端口
DISCOVERY_TIMEOUT = 5         # 发现超时秒数

# Socket 配置
SOCKET_CONFIG = {
    'connect_timeout': 30,    # 连接超时 30 秒
    'recv_timeout': 60,       # 接收超时 60 秒（等待 ACK）
    'send_timeout': None,     # 发送不设超时，由 TCP 自己控制
}

# 用户配置文件路径
USER_CONFIG_FILE = LAN_SHARE_DIR / 'user_config.json'


def load_user_config() -> dict:
    """加载用户配置"""
    if USER_CONFIG_FILE.exists():
        try:
            import json
            with open(USER_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_user_config(config: dict):
    """保存用户配置"""
    import json
    temp_file = USER_CONFIG_FILE.with_suffix('.tmp')
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        temp_file.replace(USER_CONFIG_FILE)
    except Exception:
        if temp_file.exists():
            temp_file.unlink()


def get_last_file_dir() -> str:
    """获取上次选择文件的目录"""
    config = load_user_config()
    last_dir = config.get('last_file_dir', '')
    if last_dir and Path(last_dir).exists():
        return last_dir
    return str(APP_DIR)


def set_last_file_dir(dir_path: str):
    """记录上次选择文件的目录"""
    config = load_user_config()
    config['last_file_dir'] = dir_path
    save_user_config(config)


def get_last_folder_dir() -> str:
    """获取上次选择文件夹的目录"""
    config = load_user_config()
    last_dir = config.get('last_folder_dir', '')
    if last_dir and Path(last_dir).exists():
        return last_dir
    return str(APP_DIR)


def set_last_folder_dir(dir_path: str):
    """记录上次选择文件夹的目录"""
    config = load_user_config()
    config['last_folder_dir'] = dir_path
    save_user_config(config)
