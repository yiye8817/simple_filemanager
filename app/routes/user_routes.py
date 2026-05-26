from flask import Blueprint, jsonify, request, session
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy.exc import IntegrityError
from app.utils.decorators import login_required
from app.models import User
from app import db

user_bp = Blueprint('user', __name__)

# API - 用户信息
@user_bp.route('/api/user')
@login_required
def get_user():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    if not user.api_key:
        user.generate_api_key()
        db.session.commit()
    
    return jsonify({
        'id': user.id,
        'username': user.username,
        'display_name': user.display_name,
        'email': user.email,
        'api_key': user.api_key
    })


@user_bp.route('/api/user/api-key/regenerate', methods=['POST'])
@login_required
def regenerate_user_api_key():
    """网页会话下重新生成 API 密钥（与 JWT 的 /api/auth/api-key/regenerate 区分）"""
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    user.generate_api_key()
    db.session.commit()
    return jsonify({'api_key': user.api_key})

# API - 用户设置
@user_bp.route('/api/user/settings', methods=['PUT'])
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