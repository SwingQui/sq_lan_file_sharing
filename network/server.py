"""服务器模块"""
import socket
import threading
import random
import string
import platform
from typing import Optional, Callable, Dict
from pathlib import Path

from .protocol import Protocol, MessageType, MessageBuilder
from .reconnect import HeartbeatManager
from config import (
    DEFAULT_PORT, PAIR_CODE_LENGTH, SOCKET_CONFIG,
    HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT
)
from trust.device_manager import DeviceManager


class LanShareServer:
    """局域网文件共享服务器"""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.client_address: Optional[tuple] = None
        self.running = False
        self.connected = False
        self.pair_code: Optional[str] = None
        self.hostname = platform.node()

        # 设备管理
        self.device_manager = DeviceManager()
        self.client_device_id: Optional[str] = None

        # 心跳管理
        self.heartbeat: Optional[HeartbeatManager] = None

        # 回调函数
        self.on_connected: Optional[Callable[[str], None]] = None
        self.on_disconnected: Optional[Callable[[], None]] = None
        self.on_file_info: Optional[Callable[[dict], None]] = None
        self.on_file_data: Optional[Callable[[dict], None]] = None
        self.on_file_ack: Optional[Callable[[int, bool], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_trusted_connect: Optional[Callable[[str, str], None]] = None
        self.on_resume_request: Optional[Callable[[dict], None]] = None

    @staticmethod
    def get_local_ip() -> str:
        """获取本机局域网IP地址"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except Exception:
            return "127.0.0.1"

    def generate_pair_code(self) -> str:
        """生成配对码"""
        local_ip = self.get_local_ip()
        ip_suffix = local_ip.split('.')[-1]

        chars = string.ascii_uppercase + string.digits
        random_part = ''.join(random.choice(chars) for _ in range(PAIR_CODE_LENGTH - 2))

        self.pair_code = f"{int(ip_suffix) % 36:02X}"[:2] + random_part
        return self.pair_code

    def start(self) -> bool:
        """启动服务器"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.listen(1)
            self.running = True

            thread = threading.Thread(target=self._accept_loop, daemon=True)
            thread.start()
            return True
        except Exception as e:
            if self.on_error:
                self.on_error(f"启动服务器失败: {str(e)}")
            return False

    def _accept_loop(self):
        """等待客户端连接循环"""
        while self.running:
            try:
                self.socket.settimeout(1.0)
                client, address = self.socket.accept()
                self._handle_client(client, address)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running and self.on_error:
                    self.on_error(f"接受连接错误: {str(e)}")

    def _handle_client(self, client: socket.socket, address: tuple):
        """处理客户端连接"""
        try:
            client.settimeout(SOCKET_CONFIG['connect_timeout'])
            header = client.recv(Protocol.HEADER_SIZE)
            if not header:
                client.close()
                return

            msg_type, data_len = Protocol.decode_header(header)
            data = client.recv(data_len) if data_len > 0 else b''
            msg_data = Protocol.decode_data(data) if data else {}

            # 处理重连请求（信任设备）
            if msg_type == MessageType.RECONNECT:
                self._handle_reconnect(client, address, msg_data)
                return

            # 处理配对请求
            if msg_type == MessageType.PAIR_REQUEST:
                self._handle_pair_request(client, address, msg_data)
                return

            client.close()

        except Exception as e:
            if self.on_error:
                self.on_error(f"处理客户端错误: {str(e)}")
            client.close()

    def _handle_reconnect(self, client: socket.socket, address: tuple, msg_data: dict):
        """处理重连请求（信任设备）"""
        device_id = msg_data.get('device_id', '')
        hostname = msg_data.get('hostname', '')

        if self.device_manager.is_trusted(device_id):
            # 信任设备，允许重连
            self.client_socket = client
            self.client_address = address
            self.connected = True
            self.client_device_id = device_id

            # 更新设备信息
            self.device_manager.update_device_seen(device_id, address[0])

            # 发送接受消息
            client.send(MessageBuilder.pair_accept(self.hostname))

            if self.on_trusted_connect:
                self.on_trusted_connect(device_id, hostname)
            elif self.on_connected:
                self.on_connected(hostname)

            # 开始消息循环
            self._start_heartbeat()
            self._message_loop()
        else:
            # 非信任设备，拒绝
            client.send(MessageBuilder.pair_reject("设备未受信任，请使用配对码连接"))
            client.close()

    def _handle_pair_request(self, client: socket.socket, address: tuple, msg_data: dict):
        """处理配对请求"""
        received_code = msg_data.get('pair_code', '')
        device_id = msg_data.get('device_id', '')  # 新版本客户端可能发送device_id

        if received_code == self.pair_code:
            self.client_socket = client
            self.client_address = address
            self.connected = True

            # 保存设备ID
            if device_id:
                self.client_device_id = device_id
                # 添加到信任设备
                self.device_manager.add_trusted_device(
                    device_id=device_id,
                    hostname=msg_data.get('hostname', ''),
                    ip=address[0]
                )

            client.send(MessageBuilder.pair_accept(self.hostname))

            if self.on_connected:
                peer_name = msg_data.get('hostname', address[0])
                self.on_connected(peer_name)

            self._start_heartbeat()
            self._message_loop()
        else:
            client.send(MessageBuilder.pair_reject("配对码错误"))
            client.close()

    def _start_heartbeat(self):
        """启动心跳"""
        self.heartbeat = HeartbeatManager(
            sock=self.client_socket,
            interval=HEARTBEAT_INTERVAL,
            timeout=HEARTBEAT_TIMEOUT,
            on_timeout=self._on_heartbeat_timeout
        )
        self.heartbeat.start()

    def _on_heartbeat_timeout(self):
        """心跳超时"""
        self._handle_disconnect("连接超时")

    def _message_loop(self):
        """消息接收循环"""
        buffer = b''

        while self.running and self.connected:
            try:
                self.client_socket.settimeout(1.0)
                data = self.client_socket.recv(4096)
                if not data:
                    self._handle_disconnect()
                    break

                buffer += data

                while len(buffer) >= Protocol.HEADER_SIZE:
                    msg_type, data_len = Protocol.decode_header(buffer)

                    if len(buffer) < Protocol.HEADER_SIZE + data_len:
                        break

                    msg_data = buffer[Protocol.HEADER_SIZE:Protocol.HEADER_SIZE + data_len]
                    buffer = buffer[Protocol.HEADER_SIZE + data_len:]

                    self._handle_message(msg_type, msg_data)

            except socket.timeout:
                continue
            except Exception as e:
                if self.running and self.on_error:
                    self.on_error(f"接收消息错误: {str(e)}")
                self._handle_disconnect()
                break

    def _handle_message(self, msg_type: MessageType, data: bytes):
        """处理接收到的消息"""
        # 心跳响应
        if msg_type == MessageType.HEARTBEAT:
            if self.heartbeat:
                self.heartbeat.received_response()
            return

        # FILE_DATA使用二进制格式
        if msg_type == MessageType.FILE_DATA:
            if self.on_file_data:
                self.on_file_data(data)
            return

        # 其他消息使用JSON解码
        msg_data = Protocol.decode_data(data)

        if msg_type == MessageType.FILE_INFO:
            if self.on_file_info:
                self.on_file_info(msg_data)

        elif msg_type == MessageType.FILE_ACK:
            if self.on_file_ack:
                self.on_file_ack(
                    msg_data.get('chunk_index', 0),
                    msg_data.get('success', False)
                )

        elif msg_type == MessageType.FILE_ACK_BATCH:
            # 批量确认
            if self.on_file_ack:
                for idx in msg_data.get('chunk_indices', []):
                    self.on_file_ack(idx, True)

        elif msg_type == MessageType.FILE_RESUME:
            # 续传请求
            if self.on_resume_request:
                self.on_resume_request(msg_data)

        elif msg_type == MessageType.FILE_ERROR:
            if self.on_error:
                self.on_error(msg_data.get('error', '未知错误'))

        elif msg_type == MessageType.DISCONNECT:
            self._handle_disconnect()

    def _handle_disconnect(self, reason: str = ""):
        """处理断开连接"""
        self.connected = False

        if self.heartbeat:
            self.heartbeat.stop()
            self.heartbeat = None

        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
            self.client_socket = None

        if self.on_disconnected:
            self.on_disconnected()

    def send(self, data: bytes) -> bool:
        """发送数据"""
        if not self.connected or not self.client_socket:
            return False

        try:
            self.client_socket.send(data)
            return True
        except Exception as e:
            if self.on_error:
                self.on_error(f"发送失败: {str(e)}")
            return False

    def send_resume_ok(self, file_hash: str, needed_chunks: list) -> bool:
        """发送续传确认"""
        return self.send(MessageBuilder.file_resume_ok(file_hash, needed_chunks))

    def send_file_complete(self, file_hash: str, success: bool = True) -> bool:
        """发送传输完成确认"""
        return self.send(MessageBuilder.file_complete(file_hash, success))

    def stop(self):
        """停止服务器"""
        self.running = False
        self.connected = False

        if self.heartbeat:
            self.heartbeat.stop()
            self.heartbeat = None

        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass

        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        self.client_socket = None
        self.socket = None
