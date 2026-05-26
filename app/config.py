import os
from datetime import timedelta

class Config:
    SECRET_KEY = '11223344'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///filemanager.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = '44332211'
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=1)
    UPLOAD_FOLDER = 'uploads'
    THUMBNAIL_FOLDER = 'thumbnails'
    #版本管理的文件备份路径
    BACKUP_FOLDER='vm_backup'
    MAX_STORAGE_GB = 100  # 100GB 最大存储空间

    # FolderWatch / Webhook 用：拼 download_url / preview_url 时的基址；
    # 不配则用当前请求的 host_url 推断（适合直连场景）。反向代理或外网域名建议显式设置。
    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL')

    # ===== 设备 agent 自助注册 (/api/agent/enroll) =====
    # 默认开放: 内网部署友好, agent 只需配 --server-url 即可 enroll。
    # 公网部署请把 OPEN_ENROLL 设为 False, 并配 ENROLL_TOKEN, agent 必须带 X-Enroll-Token。
    DEVICE_AGENT_OPEN_ENROLL = (os.environ.get('DEVICE_AGENT_OPEN_ENROLL', '1') == '1')
    DEVICE_AGENT_ENROLL_TOKEN = os.environ.get('DEVICE_AGENT_ENROLL_TOKEN', '')
    # 新设备自助注册时归属的 user_id; 不配则取第一个 / admin 用户
    DEVICE_AGENT_DEFAULT_USER_ID = os.environ.get('DEVICE_AGENT_DEFAULT_USER_ID')

        # 主数据库配置（词汇数据库）
    # SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///vocabulary.db'
    
    # 多数据库配置
    SQLALCHEMY_BINDS = {
        'vocabulary': os.environ.get('VOCABULARY_DB_URL') or 'sqlite:///vocabulary.db',
        #增加resources_manager的数据库到file_manager工程中来
        'resources_manager': os.environ.get('SRCS_MANAGER_DB_URL') or 'sqlite:///resources_manager.db',
         #增加version_manager的数据库到file_manager工程中来,step 1,--> copy model 到数据model下并修改
        'version_manager': os.environ.get('SRCS_VER_MANAGER_DB_URL') or 'sqlite:///version_manager.db',
        # 'users': os.environ.get('USERS_DB_URL') or 'sqlite:///users.db',
        # 'analytics': os.environ.get('ANALYTICS_DB_URL') or 'sqlite:///analytics.db',
        # 'cache': os.environ.get('CACHE_DB_URL') or 'sqlite:///cache.db'
    }
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    JSON_AS_ASCII = False
    
    # 数据库连接池配置
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_timeout': 20,
        'max_overflow': 0
    }

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    # 在生产环境中，可以使用环境变量来设置密钥
    SECRET_KEY = os.getenv('SECRET_KEY', Config.SECRET_KEY)
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', Config.JWT_SECRET_KEY)
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', Config.SQLALCHEMY_DATABASE_URI)

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///test.db'

config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}