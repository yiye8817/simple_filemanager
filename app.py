from flask import Flask, render_template, request, jsonify, send_file, abort, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, jwt_required, create_access_token, get_jwt_identity, get_jwt
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import uuid
import json
import shutil
import mimetypes
import math
import functools
import logging

# 配置应用
app = Flask(__name__)
app.config['SECRET_KEY'] = '11223344'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///filemanager.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = '44332211'
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=1)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_STORAGE_GB'] = 10  # 10GB 最大存储空间

# 确保上传目录存在
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# 初始化扩展
db = SQLAlchemy(app)
jwt = JWTManager(app)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 文件类型映射
FILE_TYPES = {
    'images': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp'],
    'documents': ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.md'],
    'videos': ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm'],
    'audio': ['.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'],
    'archives': ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'],
    'code': ['.py', '.js', '.html', '.css', '.java', '.c', '.cpp', '.php', '.rb', '.go', '.json', '.xml']
}

# 数据模型
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(120))
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    api_key = db.Column(db.String(64), unique=True)
    files = db.relationship('File', backref='owner', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def generate_api_key(self):
        self.api_key = str(uuid.uuid4())
        return self.api_key

class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(512), nullable=False)
    size = db.Column(db.Integer, default=0)
    file_type = db.Column(db.String(50))
    is_directory = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    modified_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('file.id'))
    is_public = db.Column(db.Boolean, default=False)
    public_share_id = db.Column(db.String(64), unique=True)
    
    children = db.relationship('File', backref=db.backref('parent', remote_side=[id]), lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'path': self.path,
            'size': self.size,
            'size_formatted': format_size(self.size),
            'type': self.file_type,
            'is_dir': self.is_directory,
            'modified': self.modified_at.strftime('%Y-%m-%d %H:%M'),
            'icon': get_file_icon(self.path),
            'is_public': self.is_public,
            'public_share_id': self.public_share_id
        }
    
    def generate_share_id(self):
        self.public_share_id = str(uuid.uuid4())
        return self.public_share_id

# 登录验证装饰器
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# API密钥验证装饰器
def api_key_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return jsonify({'error': 'API密钥缺失'}), 401
        
        user = User.query.filter_by(api_key=api_key).first()
        if not user:
            return jsonify({'error': 'API密钥无效'}), 401
        
        # 将用户信息存储在请求上下文中
        request.user = user
        return f(*args, **kwargs)
    return decorated_function

# 路由 - 网页认证
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('请输入用户名和密码')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['user_name'] = user.display_name or user.username
            
            next_url = request.args.get('next', url_for('index'))
            return redirect(next_url)
        else:
            flash('用户名或密码错误')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_name', None)
    flash('您已成功退出')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        display_name = request.form.get('display_name', username)
        email = request.form.get('email')
        
        if not username or not password or not email:
            flash('请填写所有必填字段')
            return render_template('register.html')
        
        if password != confirm_password:
            flash('两次输入的密码不匹配')
            return render_template('register.html')
        
        try:
            user = User(username=username, display_name=display_name, email=email)
            user.set_password(password)
            user.generate_api_key()
            
            db.session.add(user)
            db.session.commit()
            
            flash('注册成功，请登录')
            return redirect(url_for('login'))
        except IntegrityError:
            db.session.rollback()
            flash('用户名或邮箱已存在')
            return render_template('register.html')
    
    return render_template('register.html')

# API - JWT认证
@app.route('/api/auth/login', methods=['POST'])
def api_login():
    if not request.is_json:
        return jsonify({'error': '需要JSON格式的请求'}), 400
    
    username = request.json.get('username')
    password = request.json.get('password')
    
    if not username or not password:
        return jsonify({'error': '用户名和密码为必填项'}), 400
    
    user = User.query.filter_by(username=username).first()
    
    if user and user.check_password(password):
        access_token = create_access_token(identity=user.id)
        return jsonify({
            'access_token': access_token,
            'user': {
                'id': user.id,
                'username': user.username,
                'display_name': user.display_name,
                'email': user.email
            }
        })
    
    return jsonify({'error': '用户名或密码错误'}), 401

@app.route('/api/auth/refresh', methods=['POST'])
@jwt_required()
def refresh_token():
    identity = get_jwt_identity()
    access_token = create_access_token(identity=identity)
    return jsonify({'access_token': access_token})

@app.route('/api/auth/api-key', methods=['GET'])
@jwt_required()
def get_api_key():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    if not user.api_key:
        user.generate_api_key()
        db.session.commit()
    
    return jsonify({'api_key': user.api_key})

@app.route('/api/auth/api-key/regenerate', methods=['POST'])
@jwt_required()
def regenerate_api_key():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    user.generate_api_key()
    db.session.commit()
    
    return jsonify({'api_key': user.api_key})

# 主页路由
@app.route('/')
@login_required
def index():
    return render_template('index.html', user_name=session.get('user_name'))

# API - 用户信息
@app.route('/api/user')
@login_required
def get_user():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    return jsonify({
        'id': user.id,
        'username': user.username,
        'display_name': user.display_name,
        'email': user.email,
        'api_key': user.api_key
    })

# API - 用户设置
@app.route('/api/user/settings', methods=['PUT'])
@login_required
def update_user_settings():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    data = request.json
    
    if 'display_name' in data:
        user.display_name = data['display_name']
    
    if 'email' in data:
        # 检查邮箱是否已被其他用户使用
        existing_user = User.query.filter_by(email=data['email']).first()
        if existing_user and existing_user.id != user.id:
            return jsonify({'error': '该邮箱已被使用'}), 400
        user.email = data['email']
    
    if 'password' in data and 'current_password' in data:
        if not user.check_password(data['current_password']):
            return jsonify({'error': '当前密码不正确'}), 400
        user.set_password(data['password'])
    
    try:
        db.session.commit()
        return jsonify({'success': True})
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': '更新失败，请检查您的输入'}), 400

# 文件管理API
@app.route('/api/files', defaults={'path': ''})
@app.route('/api/files/', defaults={'path': ''})
@app.route('/api/files/<path:path>')
@login_required
def get_files(path):
    user_id = session.get('user_id')
    print(f"get_files in:{path}")
    if path.startswith("type/"):
        path = path[5:]
        if path in FILE_TYPES.keys():
            files = File.query.filter_by(user_id=user_id, file_type=path).all()
            return jsonify({
            'files': [file.to_dict() for file in files],
            'current_path': path
            })
    # 规范化路径，删除重复的斜杠
    path = '/'.join([p for p in path.split('/') if p])
    print(f"get_files:{path}")
    if not path:
        # 根目录
        files = File.query.filter_by(user_id=user_id, parent_id=1).all()
    else:
        # 根据路径查找父目录
        parent = get_directory_by_path(path, user_id)
        if not parent:
            return jsonify({'error': '路径不存在'}), 404
        
        if not parent.is_directory:
            return jsonify({'error': '请求的路径是一个文件，而不是目录'}), 400
        
        files = File.query.filter_by(user_id=user_id, parent_id=parent.id).all()
    
    return jsonify({
        'files': [file.to_dict() for file in files],
        'current_path': path
    })

# 外部API访问 - 根据API密钥获取文件
@app.route('/api/external/files', defaults={'path': ''})
@app.route('/api/external/files/<path:path>')
@api_key_required
def external_get_files(path):
    user = request.user
    
    # 规范化路径，删除重复的斜杠
    path = '/'.join([p for p in path.split('/') if p])
    
    if not path:
        # 根目录
        files = File.query.filter_by(user_id=user.id, parent_id=None).all()
    else:
        # 根据路径查找父目录
        parent = get_directory_by_path(path, user.id)
        if not parent:
            return jsonify({'error': '路径不存在'}), 404
        
        if not parent.is_directory:
            return jsonify({'error': '请求的路径是一个文件，而不是目录'}), 400
        
        files = File.query.filter_by(user_id=user.id, parent_id=parent.id).all()
    
    return jsonify({
        'files': [file.to_dict() for file in files],
        'current_path': path
    })

# 公共分享链接访问
@app.route('/shared/<share_id>')
def access_shared_file(share_id):
    file = File.query.filter_by(public_share_id=share_id, is_public=True).first()
    
    if not file:
        flash('分享的文件不存在或已取消分享')
        return redirect(url_for('login'))
    
    if file.is_directory:
        # 如果是目录，显示目录内容
        files = File.query.filter_by(parent_id=file.id).all()
        return render_template('shared_folder.html', folder=file, files=[f.to_dict() for f in files])
    else:
        # 如果是文件，直接下载
        return download_file_by_id(file.id, public=True)

# 文件上传
@app.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    print(f"upload_file:{user},{user_id}")
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    if 'files[]' not in request.files:
        return jsonify({'error': '请选择要上传的文件'}), 400
    
    files = request.files.getlist('files[]')
    current_path = request.form.get('path', '')

    print(f"{current_path}")
    # 检查当前路径是否有效
    parent = None
    if current_path:
        parent = get_directory_by_path(current_path, user_id)
        if not parent:
            # parent = current_path
            return jsonify({'error': '上传目录不存在'}), 404
    
    # 检查存储空间是否足够
    current_usage = get_user_storage_usage(user_id)
    max_storage = app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024  # 将GB转换为字节
    
    total_upload_size = sum(len(file.read()) for file in files)
    for file in files:
        file.seek(0)  # 重置文件指针
    
    if current_usage + total_upload_size > max_storage:
        return jsonify({'error': '存储空间不足'}), 400
    
    uploaded_files = []
    
    for file in files:
        if file.filename:
            filename = secure_filename(file.filename)
            
            # 确保文件名不重复
            base_name, extension = os.path.splitext(filename)
            # file_type = get_file_type_info(extension)
            counter = 1
            while File.query.filter_by(name=filename, parent_id=parent.id if parent else None, user_id=user_id).first():
                filename = f"{base_name}_{counter}{extension}"
                counter += 1
            
            # 创建文件记录
            file_size = 0
            #查询文件的分类是否存在，如果不存在则创建对应的文件夹
            # f_dir = File.query.filter_by(path=file_type, is_directory=True, user_id=user_id).first()
            # if not f_dir :
            #     create_directory(file_type,0,user_id)
            file_path = os.path.join(current_path, filename) if current_path else filename
            if file_path.startswith("/"):
                file_path = file_path[1:]
            physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id), file_path)
            print(f"physical_path:{physical_path}:{app.config['UPLOAD_FOLDER'], str(user_id), file_path}")
            # 确保目录存在
            os.makedirs(os.path.dirname(physical_path), exist_ok=True)
            
            # 保存文件
            file.save(physical_path)
            file_size = os.path.getsize(physical_path)
            
            # 创建数据库记录
            new_file = File(
                name=filename,
                path=file_path,
                size=file_size,
                file_type=get_file_type(filename),
                is_directory=False,
                user_id=user_id,
                parent_id=parent.id if parent else None
            )
            
            db.session.add(new_file)
            db.session.commit()
            
            uploaded_files.append(new_file.to_dict())
    
    return jsonify({
        'success': True,
        'files': uploaded_files
    })

# 外部API上传文件
@app.route('/api/external/upload', methods=['POST'])
@api_key_required
def external_upload_file():
    user = request.user
    
    if 'file' not in request.files:
        return jsonify({'error': '请选择要上传的文件'}), 400
    
    file = request.files['file']
    current_path = request.form.get('path', '')
    
    # 检查当前路径是否有效
    parent = None
    if current_path:
        parent = get_directory_by_path(current_path, user.id)
        if not parent:
            return jsonify({'error': '上传目录不存在'}), 404
    
    # 检查存储空间是否足够
    current_usage = get_user_storage_usage(user.id)
    max_storage = app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024
    
    file_content = file.read()
    file.seek(0)
    
    if current_usage + len(file_content) > max_storage:
        return jsonify({'error': '存储空间不足'}), 400
    
    if file.filename:
        filename = secure_filename(file.filename)
        
        # 确保文件名不重复
        base_name, extension = os.path.splitext(filename)
        counter = 1
        while File.query.filter_by(name=filename, parent_id=parent.id if parent else None, user_id=user.id).first():
            filename = f"{base_name}_{counter}{extension}"
            counter += 1
        
        # 创建文件记录
        file_path = os.path.join(current_path, filename) if current_path else filename
        physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user.id), file_path)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(physical_path), exist_ok=True)
        
        # 保存文件
        file.save(physical_path)
        file_size = os.path.getsize(physical_path)
        
        # 创建数据库记录
        new_file = File(
            name=filename,
            path=file_path,
            size=file_size,
            file_type=get_file_type(filename),
            is_directory=False,
            user_id=user.id,
            parent_id=parent.id if parent else None
        )
        
        db.session.add(new_file)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'file': new_file.to_dict()
        })
    
    return jsonify({'error': '上传失败'}), 400

# 创建文件夹
@app.route('/api/folder/create', methods=['POST'])
@login_required
def create_folder():
    user_id = session.get('user_id')
    data = request.json
    
    folder_name = data.get('name')
    current_path = data.get('path', '')
    
    if not folder_name:
        return jsonify({'error': '文件夹名称不能为空'}), 400
    
    # 确保文件夹名称有效
    folder_name = secure_filename(folder_name)
    
    # 检查当前路径是否有效
    parent = None
    if current_path:
        parent = get_directory_by_path(current_path, user_id)
        if not parent:
            return jsonify({'error': '目标目录不存在'}), 404
    
    # 检查文件夹是否已存在
    if File.query.filter_by(name=folder_name, parent_id=parent.id if parent else None, user_id=user_id, is_directory=True).first():
        return jsonify({'error': '该文件夹已存在'}), 400
    
    # 创建物理文件夹
    folder_path = os.path.join(current_path, folder_name) if current_path else folder_name
    if folder_path.startswith("/"):
        folder_path = folder_path[1:]
    physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id), folder_path)
    
    os.makedirs(physical_path, exist_ok=True)
    
    # 创建数据库记录
    new_folder = File(
        name=folder_name,
        path=folder_path,
        size=0,
        file_type='文件夹',
        is_directory=True,
        user_id=user_id,
        parent_id=parent.id if parent else None
    )
    
    db.session.add(new_folder)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'folder': new_folder.to_dict()
    })

# 删除文件/文件夹
@app.route('/api/files/<int:file_id>', methods=['DELETE'])
@login_required
def delete_file_by_id(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file:
        return jsonify({'error': '文件或文件夹不存在'}), 404
    
    # 删除物理文件/文件夹
    physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id), file.path)
    
    try:
        if file.is_directory:
            # 递归删除数据库中的所有子文件和文件夹
            delete_directory_recursively(file)
            
            # 删除物理文件夹
            if os.path.exists(physical_path):
                shutil.rmtree(physical_path)
        else:
            # 删除数据库记录
            db.session.delete(file)
            
            # 删除物理文件
            if os.path.exists(physical_path):
                os.remove(physical_path)
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"删除文件错误: {str(e)}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500

# 文件路径删除
@app.route('/api/files/<path:path>', methods=['DELETE'])
@login_required
def delete_file_by_path(path):
    user_id = session.get('user_id')
    
    file = get_file_by_path(path, user_id)
    
    if not file:
        return jsonify({'error': '文件或文件夹不存在'}), 404
    
    return delete_file_by_id(file.id)

# 文件下载
@app.route('/api/download/<int:file_id>')
@login_required
def download_file_by_id(file_id, public=False):
    if public:
        file = File.query.filter_by(id=file_id, is_public=True).first()
    else:
        user_id = session.get('user_id')
        file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file:
        abort(404)
    
    physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(file.user_id), file.path)
    
    if not os.path.exists(physical_path):
        abort(404)
    
    if file.is_directory:
        # 如果是目录，创建一个临时的zip文件
        temp_zip = f"{physical_path}.zip"
        try:
            shutil.make_archive(physical_path, 'zip', physical_path)
            return send_file(temp_zip, as_attachment=True, download_name=f"{file.name}.zip")
        finally:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
    else:
        # 如果是文件，直接下载
        return send_file(physical_path, as_attachment=True, download_name=file.name)

# 外部API下载文件
@app.route('/api/external/download/<int:file_id>')
@api_key_required
def external_download_file(file_id):
    user = request.user
    file = File.query.filter_by(id=file_id, user_id=user.id).first()
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user.id), file.path)
    
    if not os.path.exists(physical_path):
        return jsonify({'error': '文件不存在'}), 404
    
    if file.is_directory:
        return jsonify({'error': '不支持下载整个目录，请指定具体文件'}), 400
    
    return send_file(physical_path, as_attachment=True, download_name=file.name)

@app.route('/api/download/<path:path>')
@login_required
def download_file_by_path(path):
    user_id = session.get('user_id')
    
    file = get_file_by_path(path, user_id)
    
    if not file:
        abort(404)
    
    return download_file_by_id(file.id)

# 文件预览
@app.route('/api/preview/<int:file_id>')
@login_required
def preview_file_by_id(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    print(f"file_id:{file_id},user_id:{user_id}")
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    if file.is_directory:
        return jsonify({'error': '不能预览文件夹'}), 400
    
    physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id), file.path)
    print(f"physical_path:{physical_path},{file}")
    if not os.path.exists(physical_path):
        return jsonify({'error': '文件不存在'}), 404
    
    file_ext = os.path.splitext(file.name)[1].lower()
    
    # 检测MIME类型
    mime_type, _ = mimetypes.guess_type(physical_path)
    
    # 基本文件信息
    file_info = {
        'name': file.name,
        'size': file.size,
        'size_formatted': format_size(file.size),
        'type': mime_type or '未知类型'
    }
    
    # 处理图片预览
    if file_ext in FILE_TYPES['images']:
        preview_url = f"/api/serve/{file_id}"
        return jsonify({
            'type': 'image',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理视频预览
    elif file_ext in FILE_TYPES['videos']:
        preview_url = f"/api/serve/{file_id}"
        return jsonify({
            'type': 'video',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理音频预览
    elif file_ext in FILE_TYPES['audio']:
        preview_url = f"/api/serve/{file_id}"
        return jsonify({
            'type': 'audio',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理PDF预览
    elif file_ext == '.pdf':
        preview_url = f"/api/serve/{file_id}"
        return jsonify({
            'type': 'pdf',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理文本/代码文件预览
    elif file_ext in ['.txt', '.md', '.html', '.css', '.js', '.json', '.xml', '.py', '.java', '.c', '.cpp', '.php', '.rb', '.go']:
        try:
            # 限制读取文件大小，防止过大文件占用内存
            max_size = 1024 * 1024  # 1MB
            if file.size > max_size:
                content = "文件过大，仅显示前1MB内容...\n\n"
                with open(physical_path, 'r', encoding='utf-8', errors='replace') as f:
                    content += f.read(max_size)
            else:
                with open(physical_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            
            return jsonify({
                'type': 'text',
                'content': content,
                'extension': file_ext[1:] if file_ext else '',  # 移除点号
                'info': file_info
            })
        except Exception as e:
            return jsonify({
                'type': 'error',
                'error': f'无法读取文件内容: {str(e)}',
                'info': file_info
            })
    
    # 不支持的文件类型
    else:
        return jsonify({
            'type': 'unsupported',
            'info': file_info
        })

@app.route('/api/preview/<path:path>')
@login_required
def preview_file_by_path(path):
    user_id = session.get('user_id')
    
    file = get_file_by_path(path, user_id)
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    return preview_file_by_id(file.id)

# 外部API预览文件
@app.route('/api/external/preview/<int:file_id>')
@api_key_required
def external_preview_file(file_id):
    user = request.user
    file = File.query.filter_by(id=file_id, user_id=user.id).first()
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    # 使用已有的预览函数
    response = preview_file_by_id(file.id)
    
    # 修改URL，以便外部访问
    if isinstance(response, tuple):
        return response
    
    data = response.get_json()
    if 'url' in data:
        data['url'] = data['url'].replace('/api/serve/', '/api/external/serve/')
        return jsonify(data)
    
    return response

# 文件服务
@app.route('/api/serve/<int:file_id>')
@login_required
def serve_file_by_id(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file or file.is_directory:
        abort(404)
    
    physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id), file.path)
    
    if not os.path.exists(physical_path):
        abort(404)
    
    # 获取MIME类型
    mime_type, _ = mimetypes.guess_type(physical_path)
    
    return send_file(physical_path, mimetype=mime_type)

# 外部API服务文件
@app.route('/api/external/serve/<int:file_id>')
@api_key_required
def external_serve_file(file_id):
    user = request.user
    file = File.query.filter_by(id=file_id, user_id=user.id).first()
    
    if not file or file.is_directory:
        abort(404)
    
    physical_path = os.path.join(app.config['UPLOAD_FOLDER'], str(user.id), file.path)
    
    if not os.path.exists(physical_path):
        abort(404)
    
    # 获取MIME类型
    mime_type, _ = mimetypes.guess_type(physical_path)
    
    return send_file(physical_path, mimetype=mime_type)

# 文件共享
@app.route('/api/files/<int:file_id>/share', methods=['POST'])
@login_required
def share_file(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    # 生成或更新共享ID
    if not file.public_share_id:
        file.generate_share_id()
    
    file.is_public = True
    db.session.commit()
    
    share_url = url_for('access_shared_file', share_id=file.public_share_id, _external=True)
    
    return jsonify({
        'success': True,
        'share_id': file.public_share_id,
        'share_url': share_url
    })

@app.route('/api/files/<int:file_id>/unshare', methods=['POST'])
@login_required
def unshare_file(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    file.is_public = False
    db.session.commit()
    
    return jsonify({'success': True})

# 获取存储信息
@app.route('/api/storage')
@login_required
def get_storage():
    user_id = session.get('user_id')
    
    total_size = get_user_storage_usage(user_id)
    max_size = app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024  # 将GB转换为字节
    
    used_gb = total_size / (1024 * 1024 * 1024)
    percentage = (total_size / max_size) * 100 if max_size > 0 else 0
    
    return jsonify({
        'used': total_size,
        'used_formatted': format_size(total_size),
        'total': max_size,
        'total_formatted': f"{app.config['MAX_STORAGE_GB']} GB",
        'used_gb': round(used_gb, 2),
        'percentage': round(percentage, 2)
    })

# 辅助函数
def get_file_by_path(path, user_id):
    """根据路径获取文件"""
    parts = [p for p in path.split('/') if p]
    
    if not parts:
        return None
    
    # 查找文件名
    filename = parts[-1]
    print(f"get_file_by_path:{filename}")
    # 查找父目录
    parent_id = 1
    if len(parts) > 1:
        parent_path = '/'.join(parts[:-1])
        parent = get_directory_by_path(parent_path, user_id)
        if not parent:
            return None
        parent_id = parent.id
    
    # 查找文件
    return File.query.filter_by(name=filename, parent_id=parent_id, user_id=user_id).first()
def create_directory(name, parent_id, user_id):
    """创建目录记录"""
    newf = File(
        name=name,
        parent_id=parent_id,
        path=name,
        user_id=user_id,
        size=0,
        file_type='文件夹',
        is_directory=True,
        created_at=datetime.datetime.now(datetime.UTC)
    ) 
    #将root添加到系统中
    db.session.add(newf)
    db.session.commit()
    return newf

def get_directory_by_path(path, user_id):
    """根据路径获取目录"""
    parts = [p for p in path.split('/') if p]
    
    if not parts:
        #add 根目录作为有效目录
        return File.query.filter_by(
            parent_id=0, 
            user_id=user_id,
            is_directory=True
        ).first()  or create_directory('/', 0, user_id)  # 根目录默认命名为'root' # 返回用户的根目录
        # return None
    print(f"get_directory_by_path:{parts}")
    current_directory = None
    for i, part in enumerate(parts):
        # 如果这是第一部分，则在根目录中查找
        if i == 0:
            directory = File.query.filter_by(name=part, parent_id=1, user_id=user_id, is_directory=True).first()
        else:
            directory = File.query.filter_by(name=part, parent_id=current_directory.id, user_id=user_id, is_directory=True).first()
        
        if not directory:
            return None
        
        current_directory = directory
    
    return current_directory

def delete_directory_recursively(directory):
    """递归删除目录及其内容"""
    # 删除子文件和文件夹
    children = File.query.filter_by(parent_id=directory.id).all()
    for child in children:
        if child.is_directory:
            delete_directory_recursively(child)
        else:
            db.session.delete(child)
    
    # 删除目录本身
    db.session.delete(directory)

def get_user_storage_usage(user_id):
    """获取用户已使用的存储空间"""
    result = db.session.query(db.func.sum(File.size)).filter_by(user_id=user_id, is_directory=False).scalar()
    return result or 0
#通过扩展名，获取文件类型
def get_file_type_info(ext):
    for type_name, extensions in FILE_TYPES.items():
        if ext in extensions:
            return type_name
    return 'others'
def get_file_type(filename):
    """获取文件类型"""
    ext = os.path.splitext(filename)[1].lower()
    return get_file_type_info(ext)
    # for type_name, extensions in FILE_TYPES.items():
    #     if ext in extensions:
    #         return {
    #             'images': '图片',
    #             'documents': '文档',
    #             'videos': '视频',
    #             'audio': '音频',
    #             'archives': '压缩包',
    #             'code': '代码文件'
    #         }.get(type_name, '其它')
    #         # return type_name
    
    # return '其它'

def get_file_icon(path):
    """获取文件图标"""
    if os.path.isdir(path) or path.endswith('/'):
        return 'bi-folder'
    
    ext = os.path.splitext(os.path.basename(path))[1].lower()
    
    # 图片类型
    if ext in FILE_TYPES['images']:
        return 'bi-file-image'
    
    # 文档类型
    if ext in FILE_TYPES['documents']:
        if ext == '.pdf':
            return 'bi-file-pdf'
        elif ext in ['.doc', '.docx']:
            return 'bi-file-word'
        elif ext in ['.xls', '.xlsx']:
            return 'bi-file-excel'
        elif ext in ['.ppt', '.pptx']:
            return 'bi-file-ppt'
        elif ext == '.md':
            return 'bi-markdown'
        else:
            return 'bi-file-text'
    
    # 视频类型
    if ext in FILE_TYPES['videos']:
        return 'bi-file-play'
    
    # 音频类型
    if ext in FILE_TYPES['audio']:
        return 'bi-file-music'
    
    # 压缩文件类型
    if ext in FILE_TYPES['archives']:
        return 'bi-file-zip'
    
    # 代码文件类型
    if ext in FILE_TYPES['code']:
        return 'bi-file-code'
    
    # 默认图标
    return 'bi-file'

def format_size(size):
    """格式化文件大小"""
    if size == 0:
        return "0 B"
    
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size, 1024)))
    i = min(i, len(units) - 1)
    
    size = size / (1024 ** i)
    return f"{size:.2f} {units[i]}"

# 初始化数据库和创建管理员账户
# @app.before_first_request
def initialize_db():
    db.create_all()
    
    # 创建默认管理员账户
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            display_name='管理员',
            email='admin@example.com',
            is_admin=True
        )
        admin.set_password('adminpassword')
        admin.generate_api_key()
        
        db.session.add(admin)
        db.session.commit()
        
        logger.info("已创建默认管理员账户 - 用户名: admin, 密码: adminpassword")

if __name__ == '__main__':
    
    with app.app_context():
        initialize_db()
    app.run(debug=True,host='0.0.0.0')