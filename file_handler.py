"""文件处理模块"""
import os
import hashlib
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, Callable, Tuple

from config import BUFFER_SIZE, DEFAULT_DOWNLOAD_DIR, TEMP_DIR


class FileHandler:
    """文件处理器"""

    def __init__(self, download_dir: str = DEFAULT_DOWNLOAD_DIR):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def get_file_hash(filepath: str) -> str:
        """计算文件MD5哈希值"""
        hash_md5 = hashlib.md5()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(BUFFER_SIZE), b''):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    @staticmethod
    def get_unique_filename(directory: Path, filename: str) -> str:
        """获取唯一的文件名（避免重名覆盖）"""
        filepath = directory / filename
        if not filepath.exists():
            return filename

        stem = filepath.stem
        suffix = filepath.suffix
        counter = 1

        while True:
            new_name = f"{stem} ({counter}){suffix}"
            if not (directory / new_name).exists():
                return new_name
            counter += 1

    def prepare_file(self, filepath: str) -> Tuple[str, int, str, bool]:
        """
        准备文件/文件夹用于传输
        Args:
            filepath: 文件或文件夹路径
        Returns:
            (文件名, 大小, 哈希, 是否为文件夹)
        """
        path = Path(filepath)

        if path.is_file():
            # 直接发送文件
            filesize = path.stat().st_size
            file_hash = self.get_file_hash(filepath)
            return path.name, filesize, file_hash, False

        elif path.is_dir():
            # 打包文件夹为zip
            zip_path = TEMP_DIR / f"{path.name}.zip"
            self._zip_folder(filepath, str(zip_path))
            filesize = zip_path.stat().st_size
            file_hash = self.get_file_hash(str(zip_path))
            return f"{path.name}.zip", filesize, file_hash, True

        else:
            raise FileNotFoundError(f"路径不存在: {filepath}")

    def _zip_folder(self, folder_path: str, zip_path: str):
        """将文件夹打包成zip"""
        folder = Path(folder_path)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in folder.rglob('*'):
                if file.is_file():
                    arcname = file.relative_to(folder)
                    zipf.write(file, arcname)

    def create_temp_zip(self, folder_path: str) -> str:
        """创建临时zip文件，返回路径"""
        folder = Path(folder_path)
        zip_path = TEMP_DIR / f"{folder.name}.zip"
        self._zip_folder(folder_path, str(zip_path))
        return str(zip_path)

    def cleanup_temp_file(self, filepath: str):
        """清理临时文件"""
        try:
            Path(filepath).unlink(missing_ok=True)
        except:
            pass

    def save_file(self, filename: str, data: bytes, is_folder: bool = False) -> str:
        """
        保存接收到的文件数据
        Args:
            filename: 文件名
            data: 文件数据
            is_folder: 是否为文件夹（zip格式）
        Returns:
            保存后的文件路径
        """
        # 获取唯一文件名
        unique_name = self.get_unique_filename(self.download_dir, filename)
        filepath = self.download_dir / unique_name

        # 写入文件
        with open(filepath, 'wb') as f:
            f.write(data)

        # 如果是文件夹，解压
        if is_folder and filename.endswith('.zip'):
            folder_name = filename[:-4]  # 去掉.zip
            unique_folder = self.get_unique_filename(self.download_dir, folder_name)
            extract_path = self.download_dir / unique_folder

            with zipfile.ZipFile(filepath, 'r') as zipf:
                zipf.extractall(extract_path)

            # 删除临时zip文件
            filepath.unlink()

            return str(extract_path)

        return str(filepath)


class FileSender:
    """文件发送器"""

    def __init__(self, file_handler: FileHandler):
        self.file_handler = file_handler
        self.current_file: Optional[str] = None
        self.current_file_handle = None
        self.current_index = 0
        self.total_chunks = 0
        self.is_folder = False
        self.temp_zip_path: Optional[str] = None

        # 回调
        self.on_progress: Optional[Callable[[int, int], None]] = None  # (current, total)
        self.on_complete: Optional[Callable[[], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    def prepare(self, filepath: str) -> Tuple[str, int, str, bool]:
        """
        准备发送文件
        Returns:
            (文件名, 大小, 哈希, 是否为文件夹)
        """
        path = Path(filepath)

        if path.is_dir():
            self.is_folder = True
            self.temp_zip_path = self.file_handler.create_temp_zip(filepath)
            self.current_file = self.temp_zip_path
        else:
            self.is_folder = False
            self.current_file = filepath

        filesize = Path(self.current_file).stat().st_size
        self.total_chunks = (filesize + BUFFER_SIZE - 1) // BUFFER_SIZE
        file_hash = FileHandler.get_file_hash(self.current_file)
        filename = Path(filepath).name + ('.zip' if self.is_folder else '')

        return filename, filesize, file_hash, self.is_folder

    def get_next_chunk(self) -> Optional[bytes]:
        """获取下一个数据块"""
        if not self.current_file:
            return None

        if self.current_file_handle is None:
            self.current_file_handle = open(self.current_file, 'rb')

        data = self.current_file_handle.read(BUFFER_SIZE)
        if not data:
            return None

        chunk_index = self.current_index
        self.current_index += 1

        if self.on_progress:
            self.on_progress(self.current_index, self.total_chunks)

        # 使用简单的二进制格式传输数据块
        return chunk_index.to_bytes(4, 'big') + data

    def complete(self):
        """发送完成，清理资源"""
        if self.current_file_handle:
            self.current_file_handle.close()
            self.current_file_handle = None

        if self.temp_zip_path:
            self.file_handler.cleanup_temp_file(self.temp_zip_path)
            self.temp_zip_path = None

        self.current_file = None
        self.current_index = 0

        if self.on_complete:
            self.on_complete()

    def cancel(self):
        """取消发送"""
        self.complete()


class FileReceiver:
    """文件接收器"""

    def __init__(self, file_handler: FileHandler):
        self.file_handler = file_handler
        self.temp_data: bytearray = bytearray()
        self.current_filename: Optional[str] = None
        self.expected_size: int = 0
        self.is_folder: bool = False

        # 回调
        self.on_progress: Optional[Callable[[int, int], None]] = None
        self.on_complete: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    def start_receive(self, filename: str, filesize: int, is_folder: bool = False):
        """开始接收文件"""
        self.current_filename = filename
        self.expected_size = filesize
        self.is_folder = is_folder
        self.temp_data = bytearray()

    def receive_chunk(self, data: bytes):
        """接收数据块"""
        self.temp_data.extend(data)

        if self.on_progress:
            self.on_progress(len(self.temp_data), self.expected_size)

    def complete_receive(self) -> str:
        """完成接收，保存文件"""
        if not self.current_filename:
            raise ValueError("没有正在接收的文件")

        saved_path = self.file_handler.save_file(
            self.current_filename,
            bytes(self.temp_data),
            self.is_folder
        )

        result_path = saved_path
        self.temp_data = bytearray()
        self.current_filename = None

        if self.on_complete:
            self.on_complete(result_path)

        return result_path

    def cancel(self):
        """取消接收"""
        self.temp_data = bytearray()
        self.current_filename = None
