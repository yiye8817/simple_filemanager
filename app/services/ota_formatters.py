# ota_formatters.py
from abc import ABC, abstractmethod
from datetime import datetime
import hashlib
from flask import request  # 导入request以获取当前服务器信息

class OTAFormatter(ABC):
    """OTA格式处理器抽象基类"""
    
    @abstractmethod
    def format_response(self, version):
        """格式化响应数据 - 现在处理单个版本对象"""
        pass
    
    @abstractmethod
    def get_system_name(self):
        """返回系统名称"""
        pass
    
    def _get_base_url(self):
        """获取当前服务器的基本URL"""
        # 从请求上下文中获取当前服务器的host和port
        if request and hasattr(request, 'host'):
            # 使用请求的host信息（包含端口）
            host = request.host
            scheme = request.scheme  # http 或 https
            return f"{scheme}://{host}"
        else:
            # 如果不在请求上下文中，使用默认配置
            # 这里可以从配置文件中读取，或者使用默认值
            return "http://localhost:5000"  # 默认值
    
    def _generate_download_url(self, version):
        """生成指向当前服务器的下载URL"""
        if not hasattr(version, 'file_path') or not version.file_path:
            return ""
        
        base_url = self._get_base_url()
        user_id = getattr(version, 'user_id', 'unknown')
        version_str = getattr(version, 'version', 'unknown')
        
        # 构建下载URL
        download_url = f"{base_url}/api/external/download/{version.file_path}?user_id={user_id}&version={version_str}"
        
        return download_url

class LineageOSFormatter(OTAFormatter):
    """LineageOS格式处理器"""
    
    def get_system_name(self):
        return "lineageos"
    
    def format_response(self, version):
        """格式化LineageOS OTA响应格式 - 返回单个对象"""
        if version is None:
            return {'response': {}}
        
        # 根据实际数据字段进行映射
        version_data = {
            "datetime": self._get_timestamp(version.timestamp),
            "filename": version.file_path or f"lineage-{version.version}-{version.type}-{version.device_type}.zip",
            "id": version.checksum or str(version.file_id) if hasattr(version, 'file_id') else "",
            "romtype": version.type or "ota",
            "size": version.file_size or 0,
            "url": self._generate_download_url(version),  # 使用新的URL生成方法
            "version": version.version or "0.0.0",
            "device": version.device_type or "",
            "channel": self._get_channel(version)
        }
        
        # 添加额外字段（如果存在）
        if hasattr(version, 'upgrade_desc') and version.upgrade_desc:
            version_data["description"] = version.upgrade_desc
            
        if hasattr(version, 'version_desc') and version.version_desc:
            version_data["changelog"] = version.version_desc
        
        return {"response": [version_data], "error": None}
    
    def _get_timestamp(self, timestamp_str):
        """将时间字符串转换为时间戳"""
        if timestamp_str:
            try:
                # 解析时间字符串，格式如："2025-09-27 17:29:32"
                dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                return int(dt.timestamp())
            except (ValueError, TypeError):
                pass
        return int(datetime.now().timestamp())
    
    def _get_channel(self, version):
        """根据type字段确定channel"""
        build_type = getattr(version, 'type', 'ota')
        if build_type == 'nightly':
            return 'nightly'
        elif build_type == 'snapshot':
            return 'snapshot'
        elif build_type == 'release':
            return 'stable'
        else:
            return 'unofficial'  # 对于UNOFFICIAL构建

class AndroidFormatter(OTAFormatter):
    """Android格式处理器"""
    
    def get_system_name(self):
        return "android"
    
    def format_response(self, version):
        """格式化Android A/B OTA响应格式 - 返回单个对象"""
        if version is None:
            return {'update': {}}
        
        update_data = {
            "android_version": getattr(version, 'version', ''),
            "security_patch_level": self._get_security_patch(version),
            "build_date": self._format_date(getattr(version, 'timestamp', '')),
            "file_size": getattr(version, 'file_size', 0),
            "download_url": self._generate_download_url(version),  # 使用新的URL生成方法
            "sha256": getattr(version, 'md5sum', ''),  # 注意：实际应该用sha256
            "device": getattr(version, 'device_type', ''),
            "type": getattr(version, 'type', 'full')
        }
        
        # 添加描述信息
        if hasattr(version, 'upgrade_desc'):
            update_data["description"] = version.upgrade_desc
        
        return {"update": update_data}
    
    def _format_date(self, timestamp_str):
        """格式化日期"""
        if timestamp_str:
            try:
                dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                return dt.isoformat()
            except (ValueError, TypeError):
                pass
        return ""
    
    def _get_security_patch(self, version):
        """从extra_fields获取安全补丁信息"""
        if hasattr(version, 'extra_fields') and version.extra_fields:
            return version.extra_fields.get('security_patch', '')
        return ""

class UbuntuFormatter(OTAFormatter):
    """Ubuntu Touch格式处理器"""
    
    def get_system_name(self):
        return "ubuntu"
    
    def format_response(self, version):
        """格式化Ubuntu Touch OTA响应格式 - 返回单个对象"""
        if version is None:
            return {'image': {}}
        
        image_data = {
            "version": getattr(version, 'version', ''),
            "build_date": self._format_date(getattr(version, 'timestamp', '')),
            "file_size": getattr(version, 'file_size', 0),
            "download_url": self._generate_download_url(version),  # 使用新的URL生成方法
            "checksum": getattr(version, 'md5sum', ''),
            "device": getattr(version, 'device_type', ''),
            "channel": self._get_channel(version)
        }
        
        if hasattr(version, 'version_desc'):
            image_data["release_notes"] = version.version_desc
        
        return {"image": image_data}
    
    def _format_date(self, timestamp_str):
        """格式化日期"""
        if timestamp_str:
            try:
                dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                return dt.isoformat()
            except (ValueError, TypeError):
                pass
        return ""
    
    def _get_channel(self, version):
        """获取渠道信息"""
        build_type = getattr(version, 'type', 'ota')
        if build_type in ['stable', 'beta', 'devel']:
            return build_type
        return 'stable'

class DefaultFormatter(OTAFormatter):
    """默认格式处理器"""
    
    def get_system_name(self):
        return "default"
    
    def format_response(self, version):
        """默认响应格式 - 返回单个对象"""
        if version is None:
            return {'success': True, 'data': {}}
        
        # 如果有to_dict方法，使用它
        if hasattr(version, 'to_dict'):
            data = version.to_dict()
            # 添加下载URL
            data['download_url'] = self._generate_download_url(version)
            return {
                'success': True,
                'data': data
            }
        
        # 否则手动构建字典
        item = {}
        # 动态获取所有属性
        for attr in dir(version):
            if not attr.startswith('_') and not callable(getattr(version, attr)):
                item[attr] = getattr(version, attr)
        
        # 添加下载URL
        item['download_url'] = self._generate_download_url(version)
        
        return {'success': True, 'data': item}

# 工厂类和便捷函数保持不变...
class OTAFormatterFactory:
    """OTA格式处理器工厂"""
    
    def __init__(self):
        self._formatters = {}
        self._register_default_formatters()
    
    def _register_default_formatters(self):
        """注册默认的格式处理器"""
        default_formatters = [
            LineageOSFormatter(),
            AndroidFormatter(),
            UbuntuFormatter(),
            DefaultFormatter()
        ]
        
        for formatter in default_formatters:
            self.register_formatter(formatter)
    
    def register_formatter(self, formatter):
        """注册格式处理器"""
        if isinstance(formatter, OTAFormatter):
            self._formatters[formatter.get_system_name()] = formatter
    
    def get_formatter(self, system_name):
        """获取格式处理器"""
        system_key = system_name.lower() if system_name else 'default'
        return self._formatters.get(system_key, self._formatters["default"])
    
    def get_available_systems(self):
        """获取可用的系统列表"""
        return list(self._formatters.keys())

# 创建全局工厂实例
formatter_factory = OTAFormatterFactory()

# 便捷函数
def format_ota_response(system, version):
    """便捷函数：根据系统名称格式化响应"""
    formatter = formatter_factory.get_formatter(system)
    return formatter.format_response(version)

def get_available_systems():
    """获取支持的系​​统列表"""
    return formatter_factory.get_available_systems()