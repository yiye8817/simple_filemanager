from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
import logging
import os

db = SQLAlchemy()
jwt = JWTManager()

# flask-sock 实例; 在 create_app 里 init_app(app) 绑定。
# 任何模块都可以 from app import sock 拿到 (浏览器内网页 SSH 终端就用它).
try:
    from flask_sock import Sock  # type: ignore
    sock = Sock()
except ImportError:  # 部署环境没装 flask-sock 时, 退化为占位对象, 仅禁用 WS 功能
    sock = None

def create_app(config_name='default'):
    app = Flask(__name__)
    
    # 加载配置
    from .config import config_by_name
    app.config.from_object(config_by_name[config_name])

    # 相对路径依赖进程 cwd，转为绝对路径避免下载/预览拼路径与磁盘实际位置不一致
    for _key in ('UPLOAD_FOLDER', 'BACKUP_FOLDER'):
        _p = app.config.get(_key)
        if _p and not os.path.isabs(_p):
            app.config[_key] = os.path.abspath(_p)

    # 确保上传目录存在
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    #增加备份文件
    if not os.path.exists(app.config['BACKUP_FOLDER']):
        os.makedirs(app.config['BACKUP_FOLDER'])
    
    # 初始化扩展
    db.init_app(app)
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
    from .routes.docs_routes import docs_bp
    from .routes.llm_routes import llm_bp
    from .routes.service_manager_routes import service_bp
    from .routes.docker_service_routes import docker_service_bp
    from .routes.folder_watch_routes import folder_watch_bp
    from .routes.folder_plugin_routes import folder_plugin_bp
    from .routes.music_routes import music_bp
    from .routes.music_share_routes import music_share_bp
    from .routes.device_routes import device_bp
    from .routes.ssh_routes import ssh_bp, register_ssh_websocket

    # app.register_blueprint(upload_bp, url_prefix='/api/upload')
    app.register_blueprint(version_bp, url_prefix='/api/version')
    app.register_blueprint(query_bp, url_prefix='/api/query')
    app.register_blueprint(docs_bp, url_prefix='/api')
    app.register_blueprint(llm_bp)

    #添加拷贝的蓝图到这里并注册
    from .routes.resource_routes import resource_bp
    app.register_blueprint(resource_bp, url_prefix='/api')
    
    app.register_blueprint(vocabulary_bp, url_prefix='/api/v1')
    app.register_blueprint(auth_bp)
    app.register_blueprint(file_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(service_bp)
    app.register_blueprint(docker_service_bp)
    app.register_blueprint(folder_watch_bp)
    app.register_blueprint(folder_plugin_bp)
    app.register_blueprint(music_bp)
    app.register_blueprint(music_share_bp)
    app.register_blueprint(device_bp)
    app.register_blueprint(ssh_bp)

    # flask-sock: 把 WebSocket 端点挂上去 (需要 flask-sock 已安装)
    if sock is not None:
        sock.init_app(app)
        register_ssh_websocket(sock)
    else:
        app.logger.warning('flask-sock 未安装, /api/ssh/ws/<sid> 不可用; pip install flask-sock 后再试')
    # app.register_blueprint(resource_bp, url_prefix='/api')

    
    # 创建数据库表（主库 + 各 SQLALCHEMY_BINDS，否则 file_versions 等表不会生成）
    # 注意：在函数内勿写 import app.models，否则会覆盖局部变量 app（Flask 实例）为包 app
    with app.app_context():
        from . import models  # noqa: F401 — 注册所有 Model 元数据

        db.create_all()
        for bind_key in (app.config.get('SQLALCHEMY_BINDS') or {}):
            db.create_all(bind_key=bind_key)

        _run_lightweight_migrations(app)

    app.logger.info('数据库表已就绪（含 version_manager / vocabulary / resources_manager 等 bind）')

    return app


def _run_lightweight_migrations(app):
    """SQLAlchemy ``create_all`` 不会给已存在的表追加新列。这里用 SQLite 友好的
    ALTER TABLE 自动补齐字段, 避免每次升级都让用户手动跑 migration。

    每条规则: (表名, 列名, 列 DDL)。新增字段时在这里加一行即可。
    """
    from sqlalchemy import inspect, text

    rules = [
        ('folder_plugin', 'public', 'BOOLEAN NOT NULL DEFAULT 0'),
        ('user', 'musicfree_enabled', 'BOOLEAN NOT NULL DEFAULT 0'),
        ('file', 'music_shared', 'BOOLEAN NOT NULL DEFAULT 0'),
    ]

    try:
        insp = inspect(db.engine)
        existing_tables = set(insp.get_table_names())
        for table, column, ddl in rules:
            if table not in existing_tables:
                continue  # create_all 已经按新 schema 建好
            cols = {c['name'] for c in insp.get_columns(table)}
            if column in cols:
                continue
            app.logger.info('migrate: ALTER TABLE %s ADD COLUMN %s %s', table, column, ddl)
            with db.engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))
    except Exception:  # noqa: BLE001 — 迁移失败不应阻塞启动, 详细堆栈进日志
        app.logger.exception('lightweight migrations failed')