import os
import shutil
from datetime import datetime
from flask import current_app

class BackupService:
    @staticmethod
    def backup_file(file_path, version, backup_type='version'):
        """
        备份文件
        :param file_path: 原始文件路径
        :param version: 版本号
        :param backup_type: 备份类型
        :return: 备份文件路径
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        # 创建备份目录结构
        timestamp = datetime.now().strftime('%Y%m%d')
        backup_dir = os.path.join(current_app.config['BACKUP_FOLDER'], timestamp)
        os.makedirs(backup_dir, exist_ok=True)
        
        # 生成备份文件名
        filename = os.path.basename(file_path)
        name, ext = os.path.splitext(filename)
        backup_filename = f"{name}_v{version}_{timestamp}{ext}"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        # 复制文件
        shutil.copy2(file_path, backup_path)
        
        return backup_path
    
    @staticmethod
    def list_backups():
        """列出所有备份文件"""
        backups = []
        
        if os.path.exists(current_app.config['BACKUP_FOLDER']):
            for root, dirs, files in os.walk(current_app.config['BACKUP_FOLDER']):
                for file in files:
                    file_path = os.path.join(root, file)
                    stat = os.stat(file_path)
                    backups.append({
                        'filename': file,
                        'path': file_path,
                        'size': stat.st_size,
                        'backup_date': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    })
        
        return backups