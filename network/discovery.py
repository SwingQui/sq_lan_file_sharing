"""UDP 设备发现模块"""
import socket
import json
import threading
import time
from typing import Optional, Callable, Dict, List

from config import DISCOVERY_PORT, DISCOVERY_TIMEOUT, ROOM_TIMEOUT
from utils import get_local_ip


class DeviceDiscovery:
    """UDP 设备发现"""

    def __init__(self, device_id: str, hostname: str,
                 port: int = DISCOVERY_PORT,
                 on_device_found: Callable[[str, str], None] = None):
        """
        Args:
            device_id: 本机设备ID
            hostname: 本机主机名
            port: UDP 发现端口
            on_device_found: 发现设备回调 (device_id, ip)
        """
        self.device_id = device_id
        self.hostname = hostname
        self.port = port
        self.on_device_found = on_device_found

        self.socket: Optional[socket.socket] = None
        self.running = False
        self.listen_thread: Optional[threading.Thread] = None

    def start_listening(self):
        """启动监听"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(1.0)
            self.running = True

            self.listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.listen_thread.start()
            return True
        except Exception as e:
            print(f"启动UDP监听失败: {e}")
            return False

    def _listen_loop(self):
        """监听循环"""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
                self._handle_message(data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"UDP监听错误: {e}")

    def _handle_message(self, data: bytes, sender_addr: tuple):
        """处理接收到的消息"""
        try:
            msg = json.loads(data.decode('utf-8'))
            msg_type = msg.get('type')

            if msg_type == 'discover':
                target_device_id = msg.get('target_device_id', '')
                if target_device_id == self.device_id or not target_device_id:
                    self._send_response(sender_addr[0], sender_addr[1])

            elif msg_type == 'discover_response':
                # 收到响应
                device_id = msg.get('device_id', '')
                ip = msg.get('ip', sender_ip)
                if device_id and self.on_device_found:
                    self.on_device_found(device_id, ip)

        except (json.JSONDecodeError, KeyError) as e:
            print(f"解析UDP消息失败: {e}")

    def _send_response(self, target_ip: str, target_port: int):
        """发送响应"""
        try:
            response = {
                'type': 'discover_response',
                'device_id': self.device_id,
                'hostname': self.hostname,
                'ip': get_local_ip()
            }
            data = json.dumps(response).encode('utf-8')

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(data, (target_ip, target_port))
            sock.close()
        except Exception as e:
            print(f"发送UDP响应失败: {e}")

    def stop(self):
        """停止监听"""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        if self.listen_thread:
            self.listen_thread.join(timeout=2)


class DiscoveryClient:
    """UDP 发现客户端（用于主动发现设备）"""

    @staticmethod
    def find_device(target_device_id: str, port: int = DISCOVERY_PORT,
                    timeout: float = DISCOVERY_TIMEOUT) -> Optional[str]:
        """
        查找指定设备
        Args:
            target_device_id: 目标设备ID
            port: UDP端口
            timeout: 超时时间
        Returns:
            设备IP，未找到返回None
        """
        found_ip = None

        def on_response(data: bytes, addr: tuple):
            nonlocal found_ip
            try:
                msg = json.loads(data.decode('utf-8'))
                if msg.get('type') == 'discover_response':
                    if msg.get('device_id') == target_device_id:
                        found_ip = msg.get('ip', addr[0])
            except json.JSONDecodeError as e:
                print(f"解析UDP响应失败: {e}")
            except Exception as e:
                print(f"处理UDP响应失败: {e}")

        try:
            # 创建监听socket（使用随机端口避免与RoomScanner冲突）
            listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind(('', 0))  # 随机端口
            listen_sock.settimeout(timeout)

            # 广播发现请求到发现端口
            msg = {
                'type': 'discover',
                'target_device_id': target_device_id
            }
            data = json.dumps(msg).encode('utf-8')

            broadcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            broadcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            broadcast_sock.sendto(data, ('<broadcast>', port))
            broadcast_sock.close()

            # 等待响应
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    response_data, response_addr = listen_sock.recvfrom(4096)
                    on_response(response_data, response_addr)
                    if found_ip:
                        break
                except socket.timeout:
                    break

            listen_sock.close()
            return found_ip

        except Exception as e:
            print(f"发现设备失败: {e}")
            return None


class RoomInfo:
    """房间信息"""

    def __init__(self, ip: str, port: int, hostname: str, pair_code: str, last_seen: float = None):
        self.ip = ip
        self.port = port
        self.hostname = hostname
        self.pair_code = pair_code
        self.last_seen = last_seen or time.time()

    def is_expired(self, timeout: float = ROOM_TIMEOUT) -> bool:
        """检查房间是否已过期"""
        return time.time() - self.last_seen > timeout

    def to_dict(self) -> dict:
        return {
            'ip': self.ip,
            'port': self.port,
            'hostname': self.hostname,
            'pair_code': self.pair_code
        }

    def __eq__(self, other):
        if isinstance(other, RoomInfo):
            return self.ip == other.ip and self.port == other.port
        return False

    def __hash__(self):
        return hash((self.ip, self.port))


class RoomBroadcaster:
    """房间广播器 - 服务器端使用"""

    def __init__(self, port: int, hostname: str, pair_code: str,
                 broadcast_port: int = DISCOVERY_PORT):
        self.port = port
        self.hostname = hostname
        self.pair_code = pair_code
        self.broadcast_port = broadcast_port

        self.running = False
        self.broadcast_thread: Optional[threading.Thread] = None

    def start(self):
        """启动广播"""
        if self.running:
            return

        self.running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.broadcast_thread.start()

    def _broadcast_loop(self):
        """广播循环"""
        from config import ROOM_ANNOUNCE_INTERVAL

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        try:
            while self.running:
                try:
                    broadcast_addr = self._get_broadcast_address()
                    msg = {
                        'type': 'room_announce',
                        'ip': get_local_ip(),
                        'port': self.port,
                        'hostname': self.hostname,
                        'pair_code': self.pair_code
                    }
                    data = json.dumps(msg).encode('utf-8')
                    sock.sendto(data, (broadcast_addr, self.broadcast_port))
                except Exception as e:
                    print(f"广播房间信息失败: {e}")

                # 每隔几秒广播一次
                for _ in range(int(ROOM_ANNOUNCE_INTERVAL * 10)):
                    if not self.running:
                        break
                    time.sleep(0.1)
        finally:
            sock.close()

    def _get_broadcast_address(self) -> str:
        """获取子网定向广播地址 (如 192.168.1.255)"""
        try:
            local_ip = get_local_ip()
            parts = local_ip.split('.')
            if len(parts) == 4:
                return '.'.join(parts[:3]) + '.255'
        except:
            pass
        return '<broadcast>'

    def stop(self):
        """停止广播"""
        self.running = False
        if self.broadcast_thread:
            self.broadcast_thread.join(timeout=2)


class RoomScanner:
    """房间扫描器 - 客户端使用"""

    def __init__(self, on_room_found: Callable[[RoomInfo], None] = None,
                 on_room_expired: Callable[[RoomInfo], None] = None,
                 port: int = DISCOVERY_PORT):
        self.on_room_found = on_room_found
        self.on_room_expired = on_room_expired
        self.port = port

        self.socket: Optional[socket.socket] = None
        self.running = False
        self.listen_thread: Optional[threading.Thread] = None
        self.cleanup_thread: Optional[threading.Thread] = None

        # 房间列表 {ip: RoomInfo}
        self._rooms: Dict[str, RoomInfo] = {}
        self._rooms_lock = threading.Lock()

    def start(self):
        """启动扫描"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(1.0)
            self.running = True

            self.listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.listen_thread.start()

            self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self.cleanup_thread.start()

            return True
        except Exception as e:
            print(f"启动房间扫描失败: {e}")
            return False

    def _listen_loop(self):
        """监听循环"""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
                self._handle_message(data, addr[0])
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"房间扫描错误: {e}")

    def _handle_message(self, data: bytes, sender_ip: str):
        """处理接收到的消息"""
        try:
            msg = json.loads(data.decode('utf-8'))
            msg_type = msg.get('type')

            if msg_type == 'room_announce':
                room = RoomInfo(
                    ip=msg.get('ip', sender_ip),
                    port=msg.get('port', 9527),
                    hostname=msg.get('hostname', 'Unknown'),
                    pair_code=msg.get('pair_code', '')
                )
                self._add_or_update_room(room)

        except (json.JSONDecodeError, KeyError) as e:
            pass  # 忽略无效消息

    def _add_or_update_room(self, room: RoomInfo):
        """添加或更新房间"""
        with self._rooms_lock:
            is_new = room.ip not in self._rooms
            self._rooms[room.ip] = room

        if is_new and self.on_room_found:
            self.on_room_found(room)

    def _cleanup_loop(self):
        """清理过期房间"""
        while self.running:
            time.sleep(1)
            self._cleanup_expired_rooms()

    def _cleanup_expired_rooms(self):
        """清理过期房间"""
        expired_rooms = []
        with self._rooms_lock:
            expired_ips = [ip for ip, room in self._rooms.items() if room.is_expired()]
            for ip in expired_ips:
                expired_rooms.append(self._rooms.pop(ip))

        for room in expired_rooms:
            if self.on_room_expired:
                self.on_room_expired(room)

    def get_rooms(self) -> List[RoomInfo]:
        """获取当前房间列表"""
        with self._rooms_lock:
            return list(self._rooms.values())

    def stop(self):
        """停止扫描"""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        if self.listen_thread:
            self.listen_thread.join(timeout=2)
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=2)
