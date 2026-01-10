from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json
from app import db
# db = SQLAlchemy()
#替换db为目前已有系统的db

class FileVersion(db.Model):
    __bind_key__ = 'version_manager'  # 添加数据库的指定使用version_manager数据库
    __tablename__ = 'file_versions'
    
    id = db.Column(db.Integer, primary_key=True)
    system = db.Column(db.String(50), nullable=False, default='liangeos')
    type = db.Column(db.String(50), nullable=False)  # ota, firmware
    vendor = db.Column(db.String(100), nullable=False)  # 厂商
    device_type = db.Column(db.String(100), nullable=False)  # 设备类型
    version = db.Column(db.String(50), nullable=False)  # 版本号
    upgrade_desc = db.Column(db.Text)  # 升级说明
    version_desc = db.Column(db.Text)  # 版本说明
    checksum = db.Column(db.String(32))  # 校验和
    
    file_size = db.Column(db.BigInteger)  # 文件大小
    file_path = db.Column(db.String(500), nullable=False)  # 文件路径
    timestamp = db.Column(db.DateTime, default=datetime.now)  # 时间戳
    is_latest = db.Column(db.Boolean, default=True)  # 是否最新版本
    extra_fields = db.Column(db.Text)  # 扩展字段（JSON格式）
    status = db.Column(db.String(20), default='active')  # active, deleted
    file_id = db.Column(db.Integer) #管控上传的文件id
    user_id = db.Column(db.Integer) #管控用户访问权限
    checksum_type = db.Column(db.String(32),default='md5')  # 校验和方式：md5,sha256
    
    def to_dict(self):
        return {
            'id': self.id,
            'system': self.system,
            'type': self.type,
            'vendor': self.vendor,
            'device_type': self.device_type,
            'version': self.version,
            'upgrade_desc': self.upgrade_desc,
            'version_desc': self.version_desc,
            'checksum': self.checksum,
            'file_size': self.file_size,
            'file_path': self.file_path,
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S') if self.timestamp else None,
            'is_latest': self.is_latest,
            'extra_fields': json.loads(self.extra_fields) if self.extra_fields else {},
            'status': self.status,
            'file_id':self.file_id,
            'user_id':self.user_id,
            'checksum_type':self.checksum_type
        }