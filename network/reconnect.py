"""自动重连管理模块"""
import socket
import threading
import time
from typing import Optional, Callable
from pathlib import Path

from config import (
    RECONNECT_INTERVAL, MAX_RECONNECT_ATTEMPTS,
    HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT,
    DEFAULT_PORT
)
from network.protocol import MessageBuilder
from network.discovery import DiscoveryClient


class ReconnectManager:
    """自动重连管理器"""

    def __init__(self,
                 device_id: str,
                 hostname: str,
                 port: int = DEFAULT_PORT,
                 on_reconnected: Callable[[socket.socket], None] = None,
                 on_reconnect_failed: Callable[[], None] = None,
                 on_state_changed: Callable[[str], None] = None):
        """
        Args:
            device_id: 本机设备ID
            hostname: 主机名
            port: TCP端口
            on_reconnected: 重连成功回调
            on_reconnect_failed: 重连失败回调
            on_state_changed: 状态变化回调
        """
        self.device_id = device_id
        self.hostname = hostname
        self.port = port
        self.on_reconnected = on_reconnected
        self.on_reconnect_failed = on_reconnect_failed
        self.on_state_changed = on_state_changed

        self.reconnecting = False
        self._reconnect_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 重连目标
        self.target_device_id: Optional[str] = None
        self.target_ip: Optional[str] = None

    def start_reconnect(self, target_device_id: str, last_known_ip: str = ''):
        """
        启动重连
        Args:
            target_device_id: 目标设备ID
            last_known_ip: 最后已知的IP
        """
        if self.reconnecting:
            return

        self.target_device_id = target_device_id
        self.target_ip = last_known_ip
        self._stop_event.clear()
        self.reconnecting = True

        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        """重连循环"""
        attempts = 0

        while attempts < MAX_RECONNECT_ATTEMPTS and not self._stop_event.is_set():
            attempts += 1

            if self.on_state_changed:
                self.on_state_changed(f"正在重连 ({attempts}/{MAX_RECONNECT_ATTEMPTS})...")

            # 尝试连接
            sock = self._try_connect()

            if sock:
                # 重连成功
                if self.on_state_changed:
                    self.on_state_changed("重连成功")
                if self.on_reconnected:
                    self.on_reconnected(sock)
                self.reconnecting = False
                return

            # 等待下次重试
            if not self._stop_event.wait(RECONNECT_INTERVAL):
                continue

        # 重连失败
        if self.on_state_changed:
            self.on_state_changed("重连失败")
        if self.on_reconnect_failed:
            self.on_reconnect_failed()

        self.reconnecting = False

    def _try_connect(self) -> Optional[socket.socket]:
        """
        尝试连接
        Returns:
            成功返回socket，失败返回None
        """
        # 先尝试已知IP
        if self.target_ip:
            sock = self._connect_to_ip(self.target_ip)
            if sock:
                return sock

        # 尝试UDP发现
        if self.target_device_id:
            discovered_ip = DiscoveryClient.find_device(self.target_device_id)
            if discovered_ip:
                sock = self._connect_to_ip(discovered_ip)
                if sock:
                    self.target_ip = discovered_ip  # 更新IP
                    return sock

        return None

    def _connect_to_ip(self, ip: str) -> Optional[socket.socket]:
        """连接到指定IP"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)

            # 连接
            sock.connect((ip, self.port))

            # 发送重连请求
            sock.send(MessageBuilder.reconnect(self.device_id, self.hostname))

            # 等待响应
            from network.protocol import Protocol, MessageType
            header = Protocol.recv_exactly(sock, Protocol.HEADER_SIZE)
            if header:
                msg_type, data_len = Protocol.decode_header(header)
                data = Protocol.recv_exactly(sock, data_len) if data_len > 0 else b''

                if msg_type == MessageType.PAIR_ACCEPT:
                    # 重连被接受
                    return sock

            sock.close()

        except Exception as e:
            print(f"连接 {ip} 失败: {e}")

        return None

    def stop(self):
        """停止重连"""
        self._stop_event.set()
        self.reconnecting = False

        if self._reconnect_thread:
            self._reconnect_thread.join(timeout=2)


class HeartbeatManager:
    """心跳管理器 - 优化版，支持传输期间延长超时"""

    def __init__(self,
                 sock: socket.socket,
                 interval: float = HEARTBEAT_INTERVAL,
                 timeout: float = HEARTBEAT_TIMEOUT,
                 on_timeout: Callable[[], None] = None,
                 send_func: Optional[Callable[[bytes], bool]] = None):
        """
        Args:
            sock: socket连接
            interval: 心跳间隔
            timeout: 超时时间
            on_timeout: 超时回调
            send_func: 发送函数（用于线程安全发送），如果提供则优先使用
        """
        self.sock = sock
        self.interval = interval
        self.timeout = timeout
        self.on_timeout = on_timeout
        self._send_func = send_func

        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._last_response_time: float = time.time()
        self._lock = threading.Lock()

        # 传输模式标记（传输期间延长超时）
        self._transfer_mode = False
        self._transfer_timeout: float = timeout * 3  # 传输期间超时时间延长3倍

    def start(self):
        """启动心跳"""
        self.running = True
        self._last_response_time = time.time()

        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def set_transfer_mode(self, enabled: bool):
        """
        设置传输模式
        Args:
            enabled: 是否启用传输模式
        """
        with self._lock:
            self._transfer_mode = enabled
            if enabled:
                # 进入传输模式，重置最后响应时间
                self._last_response_time = time.time()

    def _heartbeat_loop(self):
        """心跳循环"""
        while self.running:
            try:
                # 发送心跳（优先使用 send_func 保证线程安全）
                heartbeat_data = MessageBuilder.heartbeat()
                if self._send_func:
                    self._send_func(heartbeat_data)
                else:
                    self.sock.send(heartbeat_data)

                # 检查超时
                with self._lock:
                    current_timeout = self._transfer_timeout if self._transfer_mode else self.timeout
                    elapsed = time.time() - self._last_response_time

                    if elapsed > current_timeout:
                        if self.on_timeout:
                            self.on_timeout()
                        self.running = False
                        return

                time.sleep(self.interval)

            except Exception as e:
                print(f"心跳错误: {e}")
                if self.on_timeout:
                    self.on_timeout()
                self.running = False
                return

    def received_response(self):
        """收到心跳响应"""
        with self._lock:
            self._last_response_time = time.time()

    def stop(self):
        """停止心跳"""
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)


