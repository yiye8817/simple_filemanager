from flask import Blueprint, render_template, request, jsonify, session, flash, redirect, url_for
from flask_jwt_extended import jwt_required, create_access_token, get_jwt_identity
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import User

auth_bp = Blueprint('auth', __name__)

# 网页认证路由
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('file.index'))
        
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
            
            next_url = request.args.get('next', url_for('file.index'))
            return redirect(next_url)
        else:
            flash('用户名或密码错误')
    
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_name', None)
    flash('您已成功退出')
    return redirect(url_for('auth.login'))

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('file.index'))
        
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
            return redirect(url_for('auth.login'))
        except IntegrityError:
            db.session.rollback()
            flash('用户名或邮箱已存在')
            return render_template('register.html')
    
    return render_template('register.html')

# API - JWT认证
@auth_bp.route('/api/auth/login', methods=['POST'])
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

@auth_bp.route('/api/auth/refresh', methods=['POST'])
@jwt_required()
def refresh_token():
    identity = get_jwt_identity()
    access_token = create_access_token(identity=identity)
    return jsonify({'access_token': access_token})

@auth_bp.route('/api/auth/api-key', methods=['GET'])
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

@auth_bp.route('/api/auth/api-key/regenerate', methods=['POST'])
@jwt_required()
def regenerate_api_key():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    user.generate_api_key()
    db.session.commit()
    
    return jsonify({'api_key': user.api_key})