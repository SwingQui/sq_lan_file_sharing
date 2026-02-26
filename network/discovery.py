"""UDP 设备发现模块"""
import socket
import json
import threading
import time
from typing import Optional, Callable, Dict

from config import DISCOVERY_PORT, DISCOVERY_TIMEOUT


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
                self._handle_message(data, addr[0])
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"UDP监听错误: {e}")

    def _handle_message(self, data: bytes, sender_ip: str):
        """处理接收到的消息"""
        try:
            msg = json.loads(data.decode('utf-8'))
            msg_type = msg.get('type')

            if msg_type == 'discover':
                # 收到发现请求，检查是否是找自己的
                target_device_id = msg.get('target_device_id', '')
                if target_device_id == self.device_id or not target_device_id:
                    # 响应
                    self._send_response(sender_ip)

            elif msg_type == 'discover_response':
                # 收到响应
                device_id = msg.get('device_id', '')
                ip = msg.get('ip', sender_ip)
                if device_id and self.on_device_found:
                    self.on_device_found(device_id, ip)

        except (json.JSONDecodeError, KeyError) as e:
            print(f"解析UDP消息失败: {e}")

    def _send_response(self, target_ip: str):
        """发送响应"""
        try:
            response = {
                'type': 'discover_response',
                'device_id': self.device_id,
                'hostname': self.hostname,
                'ip': self._get_local_ip()
            }
            data = json.dumps(response).encode('utf-8')

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(data, (target_ip, self.port))
            sock.close()
        except Exception as e:
            print(f"发送UDP响应失败: {e}")

    def discover_device(self, target_device_id: str, timeout: float = DISCOVERY_TIMEOUT) -> Optional[str]:
        """
        发现指定设备
        Args:
            target_device_id: 目标设备ID
            timeout: 超时时间
        Returns:
            设备IP，未找到返回None
        """
        found_ip = None
        found_event = threading.Event()

        def on_found(device_id: str, ip: str):
            nonlocal found_ip
            if device_id == target_device_id:
                found_ip = ip
                found_event.set()

        # 临时设置回调
        old_callback = self.on_device_found
        self.on_device_found = on_found

        try:
            # 广播发现请求
            self._broadcast_discover(target_device_id)

            # 等待响应
            found_event.wait(timeout)

            return found_ip
        finally:
            self.on_device_found = old_callback

    def _broadcast_discover(self, target_device_id: str):
        """广播发现请求"""
        try:
            msg = {
                'type': 'discover',
                'target_device_id': target_device_id,
                'sender_device_id': self.device_id
            }
            data = json.dumps(msg).encode('utf-8')

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # 广播到局域网
            sock.sendto(data, ('<broadcast>', self.port))
            sock.close()
        except Exception as e:
            print(f"广播发现请求失败: {e}")

    def _get_local_ip(self) -> str:
        """获取本机IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

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
            except:
                pass

        try:
            # 创建监听socket
            listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind(('0.0.0.0', port + 1))  # 使用不同端口监听响应
            listen_sock.settimeout(timeout)

            # 广播发现请求
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
