"""客户端模块"""
import socket
import threading
import platform
from typing import Optional, Callable

from .protocol import Protocol, MessageType, MessageBuilder
from .reconnect import HeartbeatManager, ConnectionMonitor
from config import (
    DEFAULT_PORT, SOCKET_CONFIG,
    HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT
)
from trust.device_manager import DeviceManager


class LanShareClient:
    """局域网文件共享客户端"""

    def __init__(self):
        self.socket: Optional[socket.socket] = None
        self.running = False
        self.connected = False
        self.hostname = platform.node()

        # 设备管理
        self.device_manager = DeviceManager()
        self.server_device_id: Optional[str] = None

        # 心跳管理
        self.heartbeat: Optional[HeartbeatManager] = None

        # 回调函数
        self.on_connected: Optional[Callable[[str], None]] = None
        self.on_disconnected: Optional[Callable[[], None]] = None
        self.on_file_info: Optional[Callable[[dict], None]] = None
        self.on_file_data: Optional[Callable[[dict], None]] = None
        self.on_file_ack: Optional[Callable[[int, bool], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_resume_ok: Optional[Callable[[dict], None]] = None
        self.on_file_complete: Optional[Callable[[dict], None]] = None

    def connect(self, server_ip: str, pair_code: str, port: int = DEFAULT_PORT) -> bool:
        """
        连接到服务器（使用配对码）
        Args:
            server_ip: 服务器IP地址
            pair_code: 配对码
            port: 端口号
        Returns:
            是否成功发起连接
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(SOCKET_CONFIG['connect_timeout'])
            self.socket.connect((server_ip, port))

            # 发送配对请求（包含device_id）
            self.socket.send(MessageBuilder.pair_request(pair_code, self.hostname))

            # 等待配对响应
            header = self.socket.recv(Protocol.HEADER_SIZE)
            if not header:
                self.socket.close()
                if self.on_error:
                    self.on_error("服务器无响应")
                return False

            msg_type, data_len = Protocol.decode_header(header)
            data = self.socket.recv(data_len) if data_len > 0 else b''
            msg_data = Protocol.decode_data(data) if data else {}

            if msg_type == MessageType.PAIR_ACCEPT:
                self.connected = True
                self.running = True

                # 保存服务器设备信息
                server_hostname = msg_data.get('hostname', server_ip)
                if self.server_device_id:
                    self.device_manager.add_trusted_device(
                        device_id=self.server_device_id,
                        hostname=server_hostname,
                        ip=server_ip
                    )

                if self.on_connected:
                    self.on_connected(server_hostname)

                self._start_heartbeat()
                thread = threading.Thread(target=self._message_loop, daemon=True)
                thread.start()

                return True

            elif msg_type == MessageType.PAIR_REJECT:
                reason = msg_data.get('reason', '配对被拒绝')
                self.socket.close()
                if self.on_error:
                    self.on_error(reason)
                return False

            else:
                self.socket.close()
                if self.on_error:
                    self.on_error("无效的服务器响应")
                return False

        except socket.timeout:
            if self.on_error:
                self.on_error("连接超时")
            return False
        except ConnectionRefusedError:
            if self.on_error:
                self.on_error("连接被拒绝，请检查目标IP和端口")
            return False
        except Exception as e:
            if self.on_error:
                self.on_error(f"连接失败: {str(e)}")
            return False

    def reconnect(self, server_ip: str, port: int = DEFAULT_PORT) -> bool:
        """
        重连到服务器（信任设备，无需配对码）
        Args:
            server_ip: 服务器IP地址
            port: 端口号
        Returns:
            是否成功重连
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(SOCKET_CONFIG['connect_timeout'])
            self.socket.connect((server_ip, port))

            # 发送重连请求
            self.socket.send(MessageBuilder.reconnect(
                self.device_manager.device_id,
                self.hostname
            ))

            # 等待响应
            header = self.socket.recv(Protocol.HEADER_SIZE)
            if not header:
                self.socket.close()
                return False

            msg_type, data_len = Protocol.decode_header(header)
            data = self.socket.recv(data_len) if data_len > 0 else b''
            msg_data = Protocol.decode_data(data) if data else {}

            if msg_type == MessageType.PAIR_ACCEPT:
                self.connected = True
                self.running = True

                server_hostname = msg_data.get('hostname', server_ip)
                if self.on_connected:
                    self.on_connected(server_hostname)

                self._start_heartbeat()
                thread = threading.Thread(target=self._message_loop, daemon=True)
                thread.start()

                return True

            else:
                reason = msg_data.get('reason', '重连被拒绝')
                self.socket.close()
                if self.on_error:
                    self.on_error(reason)
                return False

        except Exception as e:
            if self.on_error:
                self.on_error(f"重连失败: {str(e)}")
            return False

    def _start_heartbeat(self):
        """启动心跳"""
        self.heartbeat = HeartbeatManager(
            sock=self.socket,
            interval=HEARTBEAT_INTERVAL,
            timeout=HEARTBEAT_TIMEOUT,
            on_timeout=self._on_heartbeat_timeout
        )
        self.heartbeat.start()

    def _on_heartbeat_timeout(self):
        """心跳超时"""
        self._handle_disconnect()

    def _message_loop(self):
        """消息接收循环"""
        buffer = b''

        while self.running and self.connected:
            try:
                self.socket.settimeout(1.0)
                data = self.socket.recv(4096)
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
            if self.on_file_ack:
                for idx in msg_data.get('chunk_indices', []):
                    self.on_file_ack(idx, True)

        elif msg_type == MessageType.FILE_RESUME_OK:
            # 续传确认
            if self.on_resume_ok:
                self.on_resume_ok(msg_data)

        elif msg_type == MessageType.FILE_COMPLETE:
            # 传输完成
            if self.on_file_complete:
                self.on_file_complete(msg_data)

        elif msg_type == MessageType.FILE_ERROR:
            if self.on_error:
                self.on_error(msg_data.get('error', '未知错误'))

        elif msg_type == MessageType.DISCONNECT:
            self._handle_disconnect()

    def _handle_disconnect(self):
        """处理断开连接"""
        self.connected = False

        if self.heartbeat:
            self.heartbeat.stop()
            self.heartbeat = None

        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        if self.on_disconnected:
            self.on_disconnected()

    def send(self, data: bytes) -> bool:
        """发送数据"""
        if not self.connected or not self.socket:
            return False

        try:
            self.socket.send(data)
            return True
        except Exception as e:
            if self.on_error:
                self.on_error(f"发送失败: {str(e)}")
            return False

    def send_file_resume(self, file_hash: str, received_chunks: list) -> bool:
        """发送续传请求"""
        return self.send(MessageBuilder.file_resume(
            file_hash,
            received_chunks,
            self.device_manager.device_id
        ))

    def send_file_complete(self, file_hash: str, success: bool = True) -> bool:
        """发送传输完成确认"""
        return self.send(MessageBuilder.file_complete(file_hash, success))

    def disconnect(self):
        """断开连接"""
        if self.connected and self.socket:
            try:
                self.socket.send(MessageBuilder.disconnect())
            except:
                pass

        self.running = False
        self.connected = False

        if self.heartbeat:
            self.heartbeat.stop()
            self.heartbeat = None

        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        self.socket = None

    @property
    def device_id(self) -> str:
        """获取本机设备ID"""
        return self.device_manager.device_id
