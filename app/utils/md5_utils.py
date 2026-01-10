import hashlib

def calculate_file_md5(filepath, chunk_size=8192):
    """
    计算文件的MD5值
    :param filepath: 文件路径
    :param chunk_size: 读取块大小
    :return: MD5值字符串
    """
    md5 = hashlib.md5()
    
    with open(filepath, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
    
    return md5.hexdigest()

def verify_md5(filepath, expected_md5):
    """
    验证文件的MD5值
    :param filepath: 文件路径
    :param expected_md5: 期望的MD5值
    :return: 是否匹配
    """
    actual_md5 = calculate_file_md5(filepath)
    return actual_md5.lower() == expected_md5.lower()