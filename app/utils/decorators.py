from flask import session, redirect, url_for, request, jsonify
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def api_key_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            return jsonify({'error': 'API密钥缺失'}), 401
        
        from app.models import User
        user = User.query.filter_by(api_key=api_key).first()
        if not user:
            return jsonify({'error': 'API密钥无效'}), 401
        
        # 将用户信息存储在请求上下文中
        request.user = user
        return f(*args, **kwargs)
    return decorated_function