"""传输状态持久化模块 - 优化版（内存缓存）"""
import json
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Set, List, Dict
from dataclasses import dataclass, field, asdict

from config import LAN_SHARE_DIR, CHUNK_SIZE


@dataclass
class SendingState:
    """发送端状态"""
    file_path: str
    file_name: str
    file_size: int
    file_hash: str
    chunk_size: int = CHUNK_SIZE
    total_chunks: int = 0
    sent_chunks: List[int] = field(default_factory=list)
    receiver_device_id: str = ''
    created_at: str = ''
    updated_at: str = ''

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.total_chunks == 0 and self.file_size > 0:
            self.total_chunks = (self.file_size + self.chunk_size - 1) // self.chunk_size


@dataclass
class ReceivingState:
    """接收端状态"""
    file_name: str
    file_size: int
    file_hash: str
    chunk_size: int = CHUNK_SIZE
    total_chunks: int = 0
    received_chunks: List[int] = field(default_factory=list)
    temp_file: str = ''
    sender_device_id: str = ''
    created_at: str = ''
    updated_at: str = ''

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.total_chunks == 0 and self.file_size > 0:
            self.total_chunks = (self.file_size + self.chunk_size - 1) // self.chunk_size


class TransferStateManager:
    """传输状态管理器 - 使用内存缓存优化性能"""

    def __init__(self, data_dir: Path = None):
        self.data_dir = data_dir or LAN_SHARE_DIR
        self.sending_dir = self.data_dir / 'sending'
        self.receiving_dir = self.data_dir / 'receiving'

        self.sending_dir.mkdir(parents=True, exist_ok=True)
        self.receiving_dir.mkdir(parents=True, exist_ok=True)

        # ===== 内存缓存（关键优化）=====
        self._sending_cache: Dict[str, SendingState] = {}
        self._receiving_cache: Dict[str, ReceivingState] = {}
        self._cache_lock = threading.Lock()

        # 同步控制
        self._last_sync_time: float = 0
        self._chunks_since_sync: int = 0

    def _atomic_write_json(self, filepath: Path, data: dict):
        """原子写入 JSON 文件"""
        temp_file = filepath.with_suffix('.tmp')
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_file.replace(filepath)
        except Exception as e:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except:
                    pass
            raise e

    def _read_json(self, filepath: Path) -> Optional[dict]:
        """读取 JSON 文件"""
        if filepath.exists():
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"读取状态文件失败: {e}")
        return None

    # ==================== 发送状态管理 ====================

    def create_sending_state(self, file_path: str, file_name: str, file_size: int,
                             file_hash: str, receiver_device_id: str = '') -> SendingState:
        """创建发送状态"""
        state = SendingState(
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            file_hash=file_hash,
            receiver_device_id=receiver_device_id
        )
        with self._cache_lock:
            self._sending_cache[file_hash] = state
        self._save_sending_state(state)
        return state

    def _save_sending_state(self, state: SendingState):
        """保存发送状态到磁盘"""
        state.updated_at = datetime.now().isoformat()
        filepath = self.sending_dir / f"{state.file_hash}.json"
        self._atomic_write_json(filepath, asdict(state))

    def load_sending_state(self, file_hash: str) -> Optional[SendingState]:
        """加载发送状态（优先从缓存读取）"""
        with self._cache_lock:
            if file_hash in self._sending_cache:
                return self._sending_cache[file_hash]

        filepath = self.sending_dir / f"{file_hash}.json"
        data = self._read_json(filepath)
        if data:
            state = SendingState(**data)
            with self._cache_lock:
                self._sending_cache[file_hash] = state
            return state
        return None

    def update_sent_chunks(self, file_hash: str, chunk_indices: List[int],
                           force_sync: bool = False, chunks_per_sync: int = 50,
                           sync_interval: float = 5.0):
        """更新已发送块，按需持久化（使用内存缓存）"""
        with self._cache_lock:
            state = self._sending_cache.get(file_hash)
            if not state:
                # 缓存中没有，从磁盘加载
                state = self.load_sending_state(file_hash)
                if not state:
                    return

            # 添加新块（使用set去重）
            existing = set(state.sent_chunks)
            for idx in chunk_indices:
                if idx not in existing:
                    state.sent_chunks.append(idx)
                    existing.add(idx)

            self._chunks_since_sync += len(chunk_indices)

            # 判断是否需要同步到磁盘
            now = time.time()
            should_sync = (
                force_sync or
                self._chunks_since_sync >= chunks_per_sync or
                (now - self._last_sync_time) >= sync_interval
            )

            if should_sync:
                state.sent_chunks = sorted(state.sent_chunks)
                self._save_sending_state(state)
                self._last_sync_time = now
                self._chunks_since_sync = 0

    def get_missing_chunks(self, file_hash: str) -> List[int]:
        """获取未发送的块索引"""
        state = self.load_sending_state(file_hash)
        if not state:
            return []

        sent = set(state.sent_chunks)
        return [i for i in range(state.total_chunks) if i not in sent]

    def complete_sending(self, file_hash: str):
        """完成发送，清理状态"""
        with self._cache_lock:
            self._sending_cache.pop(file_hash, None)

        filepath = self.sending_dir / f"{file_hash}.json"
        if filepath.exists():
            try:
                filepath.unlink()
            except:
                pass

    # ==================== 接收状态管理 ====================

    def create_receiving_state(self, file_name: str, file_size: int, file_hash: str,
                               sender_device_id: str = '') -> ReceivingState:
        """创建接收状态"""
        temp_file = f"receiving/{file_hash}.part"
        state = ReceivingState(
            file_name=file_name,
            file_size=file_size,
            file_hash=file_hash,
            temp_file=temp_file,
            sender_device_id=sender_device_id
        )
        with self._cache_lock:
            self._receiving_cache[file_hash] = state
        self._save_receiving_state(state)
        return state

    def _save_receiving_state(self, state: ReceivingState):
        """保存接收状态到磁盘"""
        state.updated_at = datetime.now().isoformat()
        filepath = self.receiving_dir / f"{state.file_hash}.json"
        self._atomic_write_json(filepath, asdict(state))

    def load_receiving_state(self, file_hash: str) -> Optional[ReceivingState]:
        """加载接收状态（优先从缓存读取）"""
        with self._cache_lock:
            if file_hash in self._receiving_cache:
                return self._receiving_cache[file_hash]

        filepath = self.receiving_dir / f"{file_hash}.json"
        data = self._read_json(filepath)
        if data:
            state = ReceivingState(**data)
            with self._cache_lock:
                self._receiving_cache[file_hash] = state
            return state
        return None

    def update_received_chunks(self, file_hash: str, chunk_indices: List[int],
                               force_sync: bool = False, chunks_per_sync: int = 50,
                               sync_interval: float = 5.0):
        """更新已接收块，按需持久化（使用内存缓存）"""
        with self._cache_lock:
            state = self._receiving_cache.get(file_hash)
            if not state:
                state = self.load_receiving_state(file_hash)
                if not state:
                    return

            existing = set(state.received_chunks)
            for idx in chunk_indices:
                if idx not in existing:
                    state.received_chunks.append(idx)
                    existing.add(idx)

            self._chunks_since_sync += len(chunk_indices)

            now = time.time()
            should_sync = (
                force_sync or
                self._chunks_since_sync >= chunks_per_sync or
                (now - self._last_sync_time) >= sync_interval
            )

            if should_sync:
                state.received_chunks = sorted(state.received_chunks)
                self._save_receiving_state(state)
                self._last_sync_time = now
                self._chunks_since_sync = 0

    def get_missing_chunks_for_receive(self, file_hash: str) -> List[int]:
        """获取未接收的块索引"""
        state = self.load_receiving_state(file_hash)
        if not state:
            return []

        received = set(state.received_chunks)
        return [i for i in range(state.total_chunks) if i not in received]

    def is_receive_complete(self, file_hash: str) -> bool:
        """检查接收是否完成"""
        state = self.load_receiving_state(file_hash)
        if not state:
            return False
        return len(state.received_chunks) >= state.total_chunks

    def complete_receiving(self, file_hash: str):
        """完成接收，清理状态"""
        with self._cache_lock:
            self._receiving_cache.pop(file_hash, None)

        filepath = self.receiving_dir / f"{file_hash}.json"
        if filepath.exists():
            try:
                filepath.unlink()
            except:
                pass

    def get_temp_file_path(self, file_hash: str) -> Path:
        """获取临时文件路径"""
        return self.receiving_dir / f"{file_hash}.part"

    # ==================== 通用方法 ====================

    def get_all_pending_sends(self) -> List[SendingState]:
        """获取所有未完成的发送"""
        states = []
        for filepath in self.sending_dir.glob('*.json'):
            data = self._read_json(filepath)
            if data:
                states.append(SendingState(**data))
        return states

    def get_all_pending_receives(self) -> List[ReceivingState]:
        """获取所有未完成的接收"""
        states = []
        for filepath in self.receiving_dir.glob('*.json'):
            data = self._read_json(filepath)
            if data:
                states.append(ReceivingState(**data))
        return states

    def cleanup_all(self):
        """清理所有状态文件（谨慎使用）"""
        import shutil
        with self._cache_lock:
            self._sending_cache.clear()
            self._receiving_cache.clear()

        for d in [self.sending_dir, self.receiving_dir]:
            if d.exists():
                try:
                    shutil.rmtree(d)
                    d.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    print(f"清理目录失败: {e}")