"""配置管理模块"""
import os
import sys
import json
from pathlib import Path
from typing import Any, Dict

from utils import atomic_write_json


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


def load_config_file() -> Dict[str, Any]:
    """
    加载配置文件 config.json（从 SQLanFileShare/data 目录）
    Returns:
        配置字典，如果文件不存在则返回空字典
    """
    config_file = LAN_SHARE_DIR / 'config.json'
    if not config_file.exists():
        # 如果配置文件不存在，创建默认配置文件
        create_default_config(config_file)
        return {}

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            raw_config = json.load(f)

        # 解析配置，提取 value 字段
        config = {}
        for section_name, section in raw_config.items():
            if section_name.startswith('_'):
                # 跳过说明字段
                continue
            if isinstance(section, dict):
                for key, item in section.items():
                    if isinstance(item, dict) and 'value' in item:
                        config[key] = item['value']
                    else:
                        config[key] = item

        return config
    except Exception as e:
        print(f"加载配置文件失败: {e}")
        return {}


def create_default_config(config_file: Path):
    """创建默认配置文件"""
    default_config = {
        "_说明": "========================================",
        "_标题": "SQ局域网文件共享工具 - 配置文件",
        "_提示": "修改此文件后重启程序即可生效",
        "_说明2": "========================================",

        "网络设置": {
            "port": {
                "value": 9527,
                "description": "TCP端口号，用于文件传输连接"
            }
        },

        "传输设置": {
            "chunk_size": {
                "value": 262144,
                "description": "数据块大小(字节)，默认256KB。增大可提高速度但占用更多内存"
            },
            "max_concurrent_files": {
                "value": 2,
                "description": "同时传输的最大文件数量"
            },
            "send_window_size": {
                "value": 64,
                "description": "发送窗口大小，同时发送的数据块数量。增大可提高速度但占用更多内存"
            },
            "ack_batch_size": {
                "value": 32,
                "description": "批量确认大小，每收到多少块发送一次确认。增大减少网络开销"
            },
            "max_retry": {
                "value": 3,
                "description": "发送失败最大重试次数"
            },
            "ack_wait_timeout": {
                "value": 60,
                "description": "等待确认超时时间(秒)"
            }
        },

        "心跳设置": {
            "heartbeat_interval": {
                "value": 10,
                "description": "心跳发送间隔(秒)"
            },
            "heartbeat_timeout": {
                "value": 60,
                "description": "心跳超时时间(秒)，传输大文件时会自动延长3倍"
            }
        },

        "重连设置": {
            "reconnect_interval": {
                "value": 5,
                "description": "重连尝试间隔(秒)"
            },
            "max_reconnect_attempts": {
                "value": 5,
                "description": "最大重连尝试次数"
            }
        },

        "Socket设置": {
            "connect_timeout": {
                "value": 30,
                "description": "连接超时时间(秒)"
            },
            "recv_timeout": {
                "value": 60,
                "description": "接收超时时间(秒)"
            }
        }
    }

    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, ensure_ascii=False, indent=4)
        print(f"已创建默认配置文件: {config_file}")
    except PermissionError:
        print(f"无法创建配置文件，权限不足: {config_file}")
    except Exception as e:
        print(f"创建配置文件失败: {e}")


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

# 加载用户配置文件
_USER_CONFIG = load_config_file()

# ==================== 默认配置值 ====================

# 网络配置
DEFAULT_PORT = _USER_CONFIG.get('port', 9527)
BUFFER_SIZE = 64 * 1024  # 64KB

# 配对码配置
PAIR_CODE_LENGTH = 6

# 传输配置
CHUNK_SIZE = _USER_CONFIG.get('chunk_size', 256 * 1024)  # 默认256KB
MAX_CONCURRENT_FILES = _USER_CONFIG.get('max_concurrent_files', 2)
ACK_TIMEOUT = 60
MAX_RETRY = _USER_CONFIG.get('max_retry', 3)

# 滑动窗口配置（流量控制）
SEND_WINDOW_SIZE = _USER_CONFIG.get('send_window_size', 64)
ACK_BATCH_SIZE = _USER_CONFIG.get('ack_batch_size', 32)
ACK_WAIT_TIMEOUT = _USER_CONFIG.get('ack_wait_timeout', 60)

# 状态同步配置
STATE_SYNC_INTERVAL = 5
CHUNKS_PER_SYNC = 50

# 重连配置
RECONNECT_INTERVAL = _USER_CONFIG.get('reconnect_interval', 5)
MAX_RECONNECT_ATTEMPTS = _USER_CONFIG.get('max_reconnect_attempts', 5)

# 心跳配置
HEARTBEAT_INTERVAL = _USER_CONFIG.get('heartbeat_interval', 10)
HEARTBEAT_TIMEOUT = _USER_CONFIG.get('heartbeat_timeout', 60)

# UDP 发现配置
DISCOVERY_PORT = 9528
DISCOVERY_TIMEOUT = 5

# 房间广播配置
ROOM_ANNOUNCE_INTERVAL = 3  # 房间广播间隔(秒)
ROOM_TIMEOUT = 10  # 房间超时时间(秒)，超过此时间未收到广播则移除

# Socket 配置
SOCKET_CONFIG = {
    'connect_timeout': _USER_CONFIG.get('connect_timeout', 30),
    'recv_timeout': _USER_CONFIG.get('recv_timeout', 60),
    'send_timeout': None,
}

# 用户配置文件路径（用于保存上次选择的目录等）
USER_CONFIG_FILE = LAN_SHARE_DIR / 'user_config.json'


def load_user_config() -> dict:
    """加载用户配置（上次选择目录等）"""
    if USER_CONFIG_FILE.exists():
        try:
            with open(USER_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_user_config(config: dict):
    """保存用户配置"""
    atomic_write_json(USER_CONFIG_FILE, config)


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