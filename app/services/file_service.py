import os
import shutil
from werkzeug.utils import secure_filename
from flask import current_app
import hashlib
from datetime import datetime

class FileService:
    @staticmethod
    def allowed_file(filename):
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']
    
    @staticmethod
    def save_file(file):
        """保存上传的文件"""
        if file and FileService.allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{filename}"
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            return filepath, filename
        return None, None
    #后续考虑异步备份
    @staticmethod
    def backup_file(original_path, version):
        """备份文件到指定目录"""
        if not os.path.exists(original_path):
            return None
        
        filename = os.path.basename(original_path)
        name, ext = os.path.splitext(filename)
        backup_filename = f"{name}-v{version}{ext}"
        backup_path = os.path.join(current_app.config['BACKUP_FOLDER'], backup_filename)
        
        shutil.copy2(original_path, backup_path)
        return backup_path
    
    @staticmethod
    def calculate_md5(filepath):
        """计算文件的MD5值"""
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    #  @staticmethod
    # def calculate_md5(filepath):
    #     """计算文件的MD5校验和"""
    #     hash_md5 = hashlib.md5()
    #     with open(filepath, "rb") as f:
    #         for chunk in iter(lambda: f.read(4096), b""):
    #             hash_md5.update(chunk)
    #     return hash_md5.hexdigest()
    
    @staticmethod
    def calculate_sha1(filepath):
        """计算文件的SHA1校验和"""
        hash_sha1 = hashlib.sha1()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha1.update(chunk)
        return hash_sha1.hexdigest()
    
    @staticmethod
    def calculate_sha256(filepath):
        """计算文件的SHA256校验和"""
        hash_sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    @staticmethod
    def calculate_sha512(filepath):
        """计算文件的SHA512校验和"""
        hash_sha512 = hashlib.sha512()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha512.update(chunk)
        return hash_sha512.hexdigest()
    
    @staticmethod
    def calculate_crc32(filepath):
        """计算文件的CRC32校验和"""
        crc = 0
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                crc = zlib.crc32(chunk, crc)
        return format(crc & 0xFFFFFFFF, '08x')  # 转换为8位十六进制字符串
    @staticmethod
    def get_file_size(filepath):
        """获取文件大小"""
        return os.path.getsize(filepath)