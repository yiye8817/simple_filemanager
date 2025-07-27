import os
import math
import mimetypes

# 文件类型映射
FILE_TYPES = {
    'images': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp'],
    'documents': ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.md'],
    'videos': ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm'],
    'audio': ['.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'],
    'archives': ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'],
    'code': ['.py', '.js', '.html', '.css', '.java', '.c', '.cpp', '.php', '.rb', '.go', '.json', '.xml']
}

def format_size(size_bytes):
    """格式化文件大小为人类可读格式"""
    if size_bytes == 0:
        return "0B"
    
    size_names = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    
    return f"{s} {size_names[i]}"

def get_file_icon(file_path):
    """根据文件类型返回图标类名"""
    if os.path.isdir(file_path):
        return "folder"
    
    file_ext = os.path.splitext(file_path)[1].lower()
    
    for type_name, extensions in FILE_TYPES.items():
        if file_ext in extensions:
            return type_name
    
    return "file"

def get_directory_by_path(path, user_id):
    """根据路径获取目录对象"""
    from app.models import File
    
    parts = path.split('/')
    current_dir = File.query.filter_by(user_id=user_id, parent_id=None).first()  # 根目录
    
    for part in parts:
        if not part:
            continue
        
        found = False
        for child in current_dir.children:
            if child.name == part and child.is_directory:
                current_dir = child
                found = True
                break
        
        if not found:
            return None
    
    return current_dir