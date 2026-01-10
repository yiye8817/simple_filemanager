from app.services.file_service import FileService
from app.services.version_service import VersionService
import threading
from flask import current_app

def calculate_checksum_async(version_id, filepath, checksum_type='md5'):
    """异步计算校验和值（使用线程）"""
    app = current_app._get_current_object()
    
    # 校验和类型到计算方法的映射
    checksum_methods = {
        'md5': FileService.calculate_md5,
        'sha1': FileService.calculate_sha1,
        'sha256': FileService.calculate_sha256,
        'sha512': FileService.calculate_sha512,
        'crc32': FileService.calculate_crc32
    }
    
    def _calculate():
        try:
            with app.app_context():
                print(f"calculate_checksum_async - 计算类型: {checksum_type}")
                
                # 获取对应的计算方法，如果没有找到则使用MD5
                calculate_method = checksum_methods.get(
                    checksum_type, 
                    FileService.calculate_md5
                )
                
                # 计算校验和
                checksum = calculate_method(filepath)
                
                # 更新数据库中的校验和值
                VersionService.update_checksum(version_id, checksum)
                print(f"{checksum_type.upper()}计算完成 - 版本ID: {version_id}, 校验和: {checksum}")
        except Exception as e:
            print(f"{checksum_type.upper()}计算错误: {e}")
    
    # 使用线程进行异步处理
    thread = threading.Thread(target=_calculate)
    thread.daemon = True
    thread.start()
def calculate_md5_async(version_id, filepath):
    """异步计算MD5值（使用线程）"""
    app = current_app._get_current_object()
    def _calculate():
        try:
            with app.app_context():
                print("calculate_md5_async")
                checksum = FileService.calculate_md5(filepath)
                VersionService.update_checksum(version_id, checksum)
                print(f"MD5计算完成 - 版本ID: {version_id}, MD5: {checksum}")
        except Exception as e:
            print(f"MD5计算错误: {e}")
    
    # 使用线程进行异步处理
    thread = threading.Thread(target=_calculate)
    thread.daemon = True
    thread.start()