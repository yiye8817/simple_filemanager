import os
import math
import mimetypes
import hashlib
from flask import Flask, current_app, request, jsonify, send_file, send_from_directory
from PIL import Image
from moviepy import VideoFileClip
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.wave import WAVE
# from axmlparserpy.axmlprinter import AXMLPrinter
from androguard.misc import AnalyzeAPK

# 文件类型映射
FILE_TYPES = {
    'images': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp'],
    'documents': ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.md'],
    'videos': ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm'],
    'audio': ['.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'],
    'archives': ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'],
    'code': ['.py', '.js', '.html', '.css', '.java', '.c', '.cpp', '.php', '.rb', '.go', '.json', '.xml'],
    'apk':['.apk']
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
# --- 辅助函数 ---增加内容提取的相关函数
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads') # 存放用户上传文件的目录
THUMBNAIL_FOLDER = os.path.join(BASE_DIR, 'static', 'thumbnails') # 存放缩略图的目录
STATIC_ICON_FOLDER = '/static/icons/' # 存放通用图标的Web路径
def get_file_details(relative_path,user_id,is_dbclick,file_id):
    """根据文件类型调用不同的解析器"""
    
    # 安全性检查：防止目录遍历攻击
    # 将相对路径转换为绝对路径，并检查它是否在允许的 UPLOAD_FOLDER 内
    full_path = os.path.abspath(os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id),relative_path))
    # if not full_path.startswith(current_app.config['UPLOAD_FOLDER']):
    #     raise ValueError("非法的文件路径")
    print(full_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError("文件不存在")
        
    _, extension = os.path.splitext(relative_path.lower())
    
    try:
        if extension in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
            return parse_image(full_path, relative_path)
        elif extension in ['.mp4', '.mov', '.avi', '.mkv']:
            return parse_video(full_path, relative_path,is_dbclick)
        elif extension in ['.mp3', '.flac', '.wav']:
            return parse_audio(full_path, relative_path)
        elif extension == '.apk':
            return parse_apk(full_path, relative_path)
        elif extension in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt']:
            return parse_document(full_path, relative_path)
        else:
            return {
                'category': 'other',
                'thumbnail_url': f'{STATIC_ICON_FOLDER}file.png', # 通用文件图标
                'details': {'filename': os.path.basename(relative_path)}
            }
    except Exception as e:
        # 解析过程中发生任何错误
        print(f"Error parsing {relative_path}: {e}")
        return {
            'category': 'error',
            'thumbnail_url': f'{STATIC_ICON_FOLDER}error.png', # 错误图标
            'details': {'error': f'无法解析文件: {str(e)}'}
        }


def generate_thumbnail_path(original_path, ext='.jpg'):
    """为原始文件路径生成一个唯一的缩略图文件名和路径"""
    # 使用原始路径的md5哈希作为文件名，避免重名和特殊字符问题
    hasher = hashlib.md5(original_path.encode('utf-8')).hexdigest()
    filename = f"{hasher}{ext}"
    print(f"generate_thumbnail_path:=>{filename,os.path.join("static",current_app.config['THUMBNAIL_FOLDER'], filename)}")
    return os.path.join("static",current_app.config['THUMBNAIL_FOLDER'], filename), f"/static/thumbnails/{filename}"

def parse_image(full_path, relative_path):
    """解析图片文件"""
    thumb_path, thumb_url = generate_thumbnail_path(relative_path)
    if not os.path.exists(thumb_path):
        with Image.open(full_path) as img:
            img.thumbnail((200, 200)) # 创建一个最大200x200的缩略图
            img.save(thumb_path, 'JPEG')
    with Image.open(full_path) as img:
        width, height = img.size
    return {
        'category': 'image',
        'thumbnail_url': thumb_url,
        'details': {'width': width, 'height': height, 'format': img.format}
    }


def parse_audio(full_path, relative_path):
    """解析音频文件"""
    _, extension = os.path.splitext(relative_path.lower())
    try:
        if extension == '.mp3':
            audio = MP3(full_path)
            duration = audio.info.length
        elif extension == '.flac':
            audio = FLAC(full_path)
            duration = audio.info.length
        elif extension == '.wav':
            audio = WAVE(full_path)
            duration = audio.info.length
        else:
            duration = "N/A"
    except Exception:
        duration = "N/A"
        
    return {
        'category': 'audio',
        'thumbnail_url': f'{STATIC_ICON_FOLDER}music.png', # 使用通用音乐图标
        'details': {'duration_seconds': duration}
    }

# highlight-start
# CHANGED: parse_video 现在处理 is_dbclick
def parse_video(full_path, relative_path, is_dbclick=False):
    """解析视频文件，根据 is_dbclick 截取不同帧"""
    print(f"parse_video:{relative_path}")
    # 为第一帧和最后一帧生成不同的缓存键，防止冲突
    cache_key_suffix = '_last' if is_dbclick else '_first'
    thumb_path, thumb_url = generate_thumbnail_path(relative_path + cache_key_suffix)
    thumb_dir = os.path.dirname(thumb_path)
    if not os.path.exists(thumb_dir):
        os.mkdir(thumb_dir)
    print(f"parse_video:{thumb_path, thumb_url}")
    with VideoFileClip(full_path) as clip:
        # 如果对应的缩略图不存在，则生成它
        if not os.path.exists(thumb_path):
            if is_dbclick:
                # 双击：截取视频结束前0.5秒的帧作为缩略图
                # (减去一个小的偏移量以避免读到文件末尾的错误)
                frame_time = max(0, clip.duration - 0.5)
            else:
                # 单击：截取视频第1秒的帧作为缩略图
                frame_time = min(1, clip.duration / 2) # 如果视频很短，取中间
            
            clip.save_frame(thumb_path, t=frame_time)
        
        # 获取视频元数据
        duration = clip.duration
        width, height = clip.size
        
    return {
        'category': 'video',
        'thumbnail_url': thumb_url,
        'details': {
            'duration_seconds': round(duration, 2), 
            'width': width, 
            'height': height
        }
    }
# highlight-end

# highlight-start
# CHANGED: parse_apk 现在返回 versionCode
def parse_apk(full_path, relative_path=None):
    """解析APK文件，增加版本号信息"""
    thumb_path, thumb_url = generate_thumbnail_path(relative_path, ext='.png')
    app_name = "Unknown"
    version_name = "Unknown"
    version_code = "Unknown" # 新增
    package = "Unknown"

    
        # 提取应用信息
    try:
             # 使用 androguard 的 APK 对象加载文件
        a, d, dx = AnalyzeAPK(full_path)

        # 提取基本信息
        # a.is_valid_apk() 可以用来做初步检查，但加载本身就会抛出异常
        package = a.get_package()
        main_activity = a.get_main_activity()
        app_name = a.get_app_name()
        version_name = a.get_androidversion_name()
        version_code = a.get_androidversion_code()

        # 检查并提取应用图标
        # 仅在缩略图尚未缓存时执行提取操作
        if not os.path.exists(thumb_path):
            # a.get_app_icon() 会返回图标在APK内的路径，例如 'res/mipmap-xxxhdpi-v4/ic_launcher.png'
            if hasattr(a, 'get_app_icon'):  # 检查方法是否存在
                icon_path = a.get_app_icon()  # 优先使用官方方法
            else:
                # 备用方法：从资源文件中查找图标
                for f in a.get_files():
                    if 'ic_launcher' in f or 'app_icon' in f:
                        icon_path = f
                        break
            if icon_path:
                # a.get_file() 可以获取APK内任意文件的二进制数据
                icon_data = a.get_file(icon_path)
                
                # 将图标数据写入到我们的缩略图文件中
                with open(thumb_path, 'wb') as f:
                    f.write(icon_data)
            else:
                # 如果androguard找不到图标，则使用通用图标
                thumb_url = f'{STATIC_ICON_FOLDER}apk.png'

    except Exception as e:
        # 如果androguard解析失败（例如，APK文件损坏），记录错误并使用通用图标
        print(f"Androguard failed to parse {relative_path}: {e}")
        thumb_url = f'{STATIC_ICON_FOLDER}apk.png'
    return {
        'category': 'apk',
        'thumbnail_url': thumb_url,
        'details': {
            'app_name': app_name, 
            'package': package, 
            'main_activity':main_activity,
            'version_name': version_name,
            'version_code': version_code # 新增: 返回版本号
        }
    }

def parse_document(full_path, relative_path):
    """解析文档文件（仅分类和图标）"""
    _, extension = os.path.splitext(relative_path.lower())
    icon_map = {
        '.pdf': 'pdf.png',
        '.doc': 'word.png',
        '.docx': 'word.png',
        '.xls': 'excel.png',
        '.xlsx': 'excel.png',
        '.ppt': 'powerpoint.png',
        '.pptx': 'powerpoint.png',
        '.txt': 'text.png'
    }
    icon_name = icon_map.get(extension, 'file.png')
    
    return {
        'category': 'document',
        'thumbnail_url': f'{STATIC_ICON_FOLDER}{icon_name}',
        'details': {'filename': os.path.basename(relative_path)}
    }
#增加下载文件的接口，方便file里减少代码
def download_file_from(user_id,file):
    physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), file.path)
    print(f"external_download_file:{user_id,file,physical_path}")
    if not os.path.exists(physical_path):
        return jsonify({'error': '文件不存在'}), 404
    
    if file.is_directory:
        return jsonify({'error': '不支持下载整个目录，请指定具体文件'}), 400
    
    return send_file(physical_path, as_attachment=True, download_name=file.name)