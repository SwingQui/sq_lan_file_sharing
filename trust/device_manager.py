"""设备标识与信任管理模块"""
import json
import threading
import uuid
import platform
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
from config import LAN_SHARE_DIR
from utils import atomic_write_json


class DeviceManager:
    """设备标识与信任管理"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or LAN_SHARE_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.device_id_file = self.data_dir / 'device_id.json'
        self.trusted_devices_file = self.data_dir / 'trusted_devices.json'

        self.device_id: str = self._load_or_create_device_id()
        self.hostname = platform.node()

        # 内存缓存
        self._devices_cache: Optional[Dict] = None
        self._cache_lock = threading.Lock()

    def _load_or_create_device_id(self) -> str:
        """加载或创建设备标识"""
        if self.device_id_file.exists():
            try:
                with open(self.device_id_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('device_id', '')
            except (json.JSONDecodeError, IOError):
                pass

        # 创建新的设备标识
        username = os.environ.get('USERNAME') or os.environ.get('USER', 'unknown')
        unique_id = str(uuid.uuid4())
        device_id = f"{platform.node()}-{username}-{unique_id}"

        # 持久化
        atomic_write_json(self.device_id_file, {
            'device_id': device_id,
            'created_at': datetime.now().isoformat()
        })

        return device_id

    def _load_trusted_devices(self) -> dict:
        """加载信任设备列表（优先从缓存读取）"""
        with self._cache_lock:
            if self._devices_cache is not None:
                return self._devices_cache

        if self.trusted_devices_file.exists():
            try:
                with open(self.trusted_devices_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    with self._cache_lock:
                        self._devices_cache = data
                    return data
            except (json.JSONDecodeError, IOError):
                pass
        return {'devices': []}

    def _save_trusted_devices(self, data: dict):
        """保存信任设备列表并更新缓存"""
        atomic_write_json(self.trusted_devices_file, data)
        with self._cache_lock:
            self._devices_cache = data

    def is_trusted(self, device_id: str) -> bool:
        """检查设备是否在信任列表中"""
        if not device_id:
            return False

        data = self._load_trusted_devices()
        for device in data.get('devices', []):
            if device.get('device_id') == device_id:
                return True
        return False

    def add_trusted_device(self, device_id: str, hostname: str = '', ip: str = ''):
        """添加信任设备"""
        if not device_id:
            return

        data = self._load_trusted_devices()
        devices = data.get('devices', [])

        # 检查是否已存在
        for device in devices:
            if device.get('device_id') == device_id:
                # 更新信息
                device['last_ip'] = ip
                device['last_seen'] = datetime.now().isoformat()
                if hostname:
                    device['hostname'] = hostname
                self._save_trusted_devices(data)
                return

        # 添加新设备
        devices.append({
            'device_id': device_id,
            'hostname': hostname,
            'last_ip': ip,
            'trusted_at': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat()
        })
        data['devices'] = devices
        self._save_trusted_devices(data)

    def remove_trusted_device(self, device_id: str) -> bool:
        """移除信任设备"""
        data = self._load_trusted_devices()
        devices = data.get('devices', [])
        original_len = len(devices)

        devices = [d for d in devices if d.get('device_id') != device_id]

        if len(devices) < original_len:
            data['devices'] = devices
            self._save_trusted_devices(data)
            return True
        return False

    def update_device_seen(self, device_id: str, ip: str = ''):
        """更新设备最后见到时间和 IP"""
        if not device_id:
            return

        data = self._load_trusted_devices()
        devices = data.get('devices', [])

        for device in devices:
            if device.get('device_id') == device_id:
                device['last_seen'] = datetime.now().isoformat()
                if ip:
                    device['last_ip'] = ip
                self._save_trusted_devices(data)
                return

    def get_trusted_devices(self) -> List[Dict]:
        """获取所有信任设备列表"""
        data = self._load_trusted_devices()
        return data.get('devices', [])

    def get_device_ip(self, device_id: str) -> Optional[str]:
        """获取信任设备的最后 IP"""
        data = self._load_trusted_devices()
        for device in data.get('devices', []):
            if device.get('device_id') == device_id:
                return device.get('last_ip')
        return None

    def get_device_by_ip(self, ip: str) -> Optional[Dict]:
        """通过 IP 获取信任设备信息"""
        data = self._load_trusted_devices()
        for device in data.get('devices', []):
            if device.get('last_ip') == ip:
                return device
        return None

    def reload(self):
        """强制重新从磁盘加载信任设备列表"""
        with self._cache_lock:
            self._devices_cache = None
