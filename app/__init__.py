from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
import logging
import os

# 初始化扩展
db = SQLAlchemy()
jwt = JWTManager()

def create_app(config_name='default'):
    app = Flask(__name__)
    
    # 加载配置
    from .config import config_by_name
    app.config.from_object(config_by_name[config_name])
    
    # 确保上传目录存在
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    #增加备份文件
    if not os.path.exists(app.config['BACKUP_FOLDER']):
        os.makedirs(app.config['BACKUP_FOLDER'])
    
    # 初始化扩展
    db.init_app(app)
    # 为每个绑定的数据库创建表
    # for bind_key in app.config['SQLALCHEMY_BINDS'].keys():
    #     db.create_all(bind_key=bind_key)
    
    app.logger.info("所有数据库表已创建完成")
    jwt.init_app(app)
    CORS(app, resources={r"/api/*": {"allow_headers": ["Authorization"]}})
    
    # 配置日志
    logging.basicConfig(level=logging.DEBUG)
    
    # 注册蓝图
    from .routes.auth_routes import auth_bp
    from .routes.file_routes import file_bp
    from .routes.user_routes import user_bp
    from .routes.vocabulary import vocabulary_bp
    #拷贝版本管理的蓝图
        # 注册蓝图
    # from routes.upload_routes import upload_bp
   
    from .routes.query_routes import query_bp
    from .routes.version_routes import version_bp
    
    # app.register_blueprint(upload_bp, url_prefix='/api/upload')
    app.register_blueprint(version_bp, url_prefix='/api/version')
    app.register_blueprint(query_bp, url_prefix='/api/query')
    
    #添加拷贝的蓝图到这里并注册
    from .routes.resource_routes import resource_bp
    app.register_blueprint(resource_bp, url_prefix='/api')
    
    app.register_blueprint(vocabulary_bp, url_prefix='/api/v1')
    app.register_blueprint(auth_bp)
    app.register_blueprint(file_bp)
    app.register_blueprint(user_bp)
    # app.register_blueprint(resource_bp, url_prefix='/api')

    
    # 创建数据库表
    with app.app_context():
        db.create_all()
    
    return app