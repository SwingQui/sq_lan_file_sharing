"""文件处理模块"""
import os
import hashlib
import shutil
import zipfile
from pathlib import Path
from typing import Tuple

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
            zip_path = self.create_temp_zip(filepath)
            filesize = Path(zip_path).stat().st_size
            file_hash = self.get_file_hash(zip_path)
            return f"{path.name}.zip", filesize, file_hash, True

        else:
            raise FileNotFoundError(f"路径不存在: {filepath}")

    def _zip_folder(self, folder_path: str, zip_path: str):
        """将文件夹打包成zip"""
        folder = Path(folder_path)
        try:
            compression = zipfile.ZIP_DEFLATED
        except:
            compression = zipfile.ZIP_STORED

        with zipfile.ZipFile(zip_path, 'w', compression) as zipf:
            for file in folder.rglob('*'):
                if file.is_file():
                    arcname = file.relative_to(folder)
                    zipf.write(file, arcname)

    def create_temp_zip(self, folder_path: str) -> str:
        """创建临时zip文件，返回路径（使用唯一文件名避免冲突）"""
        import uuid
        folder = Path(folder_path)
        zip_path = TEMP_DIR / f"{folder.name}_{uuid.uuid4().hex[:8]}.zip"
        self._zip_folder(folder_path, str(zip_path))
        return str(zip_path)

    @staticmethod
    def cleanup_all_temp_files():
        """清理所有临时文件（启动时调用）"""
        if TEMP_DIR.exists():
            for f in TEMP_DIR.iterdir():
                try:
                    f.unlink()
                except:
                    pass

    def cleanup_temp_file(self, filepath: str):
        """清理临时文件"""
        try:
            Path(filepath).unlink(missing_ok=True)
        except Exception as e:
            print(f"清理临时文件失败: {e}")

