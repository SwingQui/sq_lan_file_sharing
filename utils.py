"""公共工具函数"""
import json
import math
import socket
from pathlib import Path


def get_local_ip() -> str:
    """通过 UDP 连接 8.8.8.8 获取本机局域网 IP 地址"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"


def format_size(bytes_val: float) -> str:
    """格式化文件大小为可读字符串"""
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


def atomic_write_json(filepath: Path, data: dict):
    """原子写入 JSON 文件（先写临时文件再重命名）"""
    temp_file = filepath.with_suffix('.tmp')
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_file.replace(filepath)
    except Exception as e:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass
        raise e
