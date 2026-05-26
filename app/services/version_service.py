from app.models.version_model import db, FileVersion
import json

class VersionService:
    @staticmethod
    def get_next_version(system, type, vendor, device_type):
        """自动生成下一个版本号"""
        latest = FileVersion.query.filter_by(
            system=system,
            type=type,
            vendor=vendor,
            device_type=device_type,
            status='active'
        ).order_by(FileVersion.version.desc()).first()
        print(f"get_next_version:{latest}")
        if latest:
            # 解析版本号并递增
            try:
                parts = latest.version.split('.')
                if len(parts) >= 3:
                    # 假设版本号格式为 x.y.z
                    parts[-1] = str(int(parts[-1]) + 1)
                    return '.'.join(parts)
                else:
                    # 如果格式不标准，直接递增
                    return f"{latest.version}.1"
            except Exception:
                return "0.0.1"
        # 首条记录：0.0.0 易被误认为「未解析」占位，从 0.0.1 起算
        return "0.0.1"
    
    @staticmethod
    def create_version(data):
        """创建新版本"""
        # 将当前最新版本标记为非最新
        FileVersion.query.filter_by(
            system=data.get('system', 'liangeos'),
            type=data['type'],
            vendor=data['vendor'],
            device_type=data['device_type'],
            is_latest=True,
            status='active'
        ).update({'is_latest': False})
        
        # 创建新版本（system 与前端/查询页默认一致，勿用错误的 lineageos 拼写）
        version = FileVersion(
            system=data.get('system') or 'liangeos',
            type=data['type'],
            vendor=data['vendor'],
            device_type=data['device_type'],
            version=data['version'],
            upgrade_desc=data.get('upgrade_desc'),
            version_desc=data.get('version_desc'),
            checksum_type=data.get('checksum_type'),
            file_size=data.get('file_size'),
            file_path=data['file_path'],
            is_latest=True,
            file_id=data.get('file_id'),
            user_id = data.get('user_id'),
            extra_fields=json.dumps(data.get('extra_fields', {}))
            
        )
        
        db.session.add(version)
        db.session.commit()
        return version
    
    @staticmethod
    def update_checksum(version_id, checksum):
        """更新checksum值"""
        from flask import current_app
        app = current_app._get_current_object()
        with app.app_context():
            version = FileVersion.query.get(version_id)
            if version:
                version.checksum = checksum
                db.session.commit()
                return True
        return False