"""通信协议模块"""
import struct
import json
from enum import IntEnum
from typing import Optional, Tuple


class MessageType(IntEnum):
    """消息类型枚举"""
    PAIR_REQUEST = 1      # 配对请求
    PAIR_ACCEPT = 2       # 配对接受
    PAIR_REJECT = 3       # 配对拒绝
    FILE_INFO = 4         # 文件信息
    FILE_DATA = 5         # 文件数据块
    FILE_ACK = 6          # 文件接收确认
    FILE_ERROR = 7        # 文件传输错误
    DISCONNECT = 8        # 断开连接
    FILE_LIST_REQUEST = 9 # 文件列表请求
    FILE_LIST_RESPONSE = 10 # 文件列表响应
    FILE_ACK_BATCH = 11   # 批量确认
    FILE_RESUME = 12      # 续传请求
    FILE_RESUME_OK = 13   # 续传确认
    FILE_COMPLETE = 14    # 传输完成确认
    HEARTBEAT = 15        # 心跳包
    RECONNECT = 16        # 重连请求（信任设备）


class Protocol:
    """通信协议处理类"""

    HEADER_SIZE = 8  # 4字节类型 + 4字节数据长度

    @staticmethod
    def encode(message_type: MessageType, data: dict) -> bytes:
        """
        编码消息
        格式: [类型4字节][长度4字节][JSON数据]
        """
        json_data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        header = struct.pack('>II', message_type, len(json_data))
        return header + json_data

    @staticmethod
    def decode_header(header: bytes) -> Tuple[MessageType, int]:
        """
        解码消息头
        返回: (消息类型, 数据长度)
        """
        if len(header) < Protocol.HEADER_SIZE:
            raise ValueError("Header too short")
        msg_type, data_len = struct.unpack('>II', header[:Protocol.HEADER_SIZE])
        return MessageType(msg_type), data_len

    @staticmethod
    def decode_data(data: bytes) -> dict:
        """解码消息体"""
        return json.loads(data.decode('utf-8'))


class MessageBuilder:
    """消息构建器"""

    @staticmethod
    def pair_request(pair_code: str, hostname: str) -> bytes:
        """构建配对请求消息"""
        return Protocol.encode(MessageType.PAIR_REQUEST, {
            'pair_code': pair_code,
            'hostname': hostname
        })

    @staticmethod
    def pair_accept(hostname: str) -> bytes:
        """构建配对接受消息"""
        return Protocol.encode(MessageType.PAIR_ACCEPT, {
            'hostname': hostname
        })

    @staticmethod
    def pair_reject(reason: str) -> bytes:
        """构建配对拒绝消息"""
        return Protocol.encode(MessageType.PAIR_REJECT, {
            'reason': reason
        })

    @staticmethod
    def file_info(filename: str, filesize: int, file_hash: str, is_folder: bool = False) -> bytes:
        """构建文件信息消息"""
        return Protocol.encode(MessageType.FILE_INFO, {
            'filename': filename,
            'filesize': filesize,
            'hash': file_hash,
            'is_folder': is_folder
        })

    @staticmethod
    def file_data(chunk_index: int, data: bytes) -> bytes:
        """
        构建文件数据消息
        格式: [类型4字节][总长度4字节][块序号4字节][数据N字节]
        注意：FILE_DATA使用二进制格式，不走JSON
        """
        total_len = 4 + len(data)  # 块序号4字节 + 数据
        header = struct.pack('>II', MessageType.FILE_DATA, total_len)
        chunk_header = struct.pack('>I', chunk_index)
        return header + chunk_header + data

    @staticmethod
    def decode_file_data(data: bytes) -> Tuple[int, bytes]:
        """
        解码文件数据消息体
        返回: (块序号, 实际数据)
        """
        if len(data) < 4:
            raise ValueError("File data too short")
        chunk_index = struct.unpack('>I', data[:4])[0]
        actual_data = data[4:]
        return chunk_index, actual_data

    @staticmethod
    def file_ack(chunk_index: int, success: bool) -> bytes:
        """构建文件确认消息"""
        return Protocol.encode(MessageType.FILE_ACK, {
            'chunk_index': chunk_index,
            'success': success
        })

    @staticmethod
    def file_error(error_msg: str) -> bytes:
        """构建文件错误消息"""
        return Protocol.encode(MessageType.FILE_ERROR, {
            'error': error_msg
        })

    @staticmethod
    def disconnect() -> bytes:
        """构建断开连接消息"""
        return Protocol.encode(MessageType.DISCONNECT, {})

    @staticmethod
    def file_list_request() -> bytes:
        """构建文件列表请求消息"""
        return Protocol.encode(MessageType.FILE_LIST_REQUEST, {})

    @staticmethod
    def file_list_response(files: list) -> bytes:
        """构建文件列表响应消息"""
        return Protocol.encode(MessageType.FILE_LIST_RESPONSE, {
            'files': files
        })

    @staticmethod
    def file_ack_batch(chunk_indices: list) -> bytes:
        """构建批量确认消息"""
        return Protocol.encode(MessageType.FILE_ACK_BATCH, {
            'chunk_indices': chunk_indices
        })

    @staticmethod
    def file_resume(file_hash: str, received_chunks: list, device_id: str = '') -> bytes:
        """构建续传请求消息"""
        return Protocol.encode(MessageType.FILE_RESUME, {
            'file_hash': file_hash,
            'received_chunks': received_chunks,
            'device_id': device_id
        })

    @staticmethod
    def file_resume_ok(file_hash: str, needed_chunks: list) -> bytes:
        """构建续传确认消息"""
        return Protocol.encode(MessageType.FILE_RESUME_OK, {
            'file_hash': file_hash,
            'needed_chunks': needed_chunks
        })

    @staticmethod
    def file_complete(file_hash: str, success: bool = True) -> bytes:
        """构建传输完成确认消息"""
        return Protocol.encode(MessageType.FILE_COMPLETE, {
            'file_hash': file_hash,
            'success': success
        })

    @staticmethod
    def heartbeat() -> bytes:
        """构建心跳包"""
        return Protocol.encode(MessageType.HEARTBEAT, {
            'timestamp': __import__('time').time()
        })

    @staticmethod
    def reconnect(device_id: str, hostname: str) -> bytes:
        """构建重连请求消息"""
        return Protocol.encode(MessageType.RECONNECT, {
            'device_id': device_id,
            'hostname': hostname
        })
