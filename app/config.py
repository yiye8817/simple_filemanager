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
    MAX_STORAGE_GB = 10  # 10GB 最大存储空间
        # 主数据库配置（词汇数据库）
    # SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///vocabulary.db'
    
    # 多数据库配置
    SQLALCHEMY_BINDS = {
        'vocabulary': os.environ.get('VOCABULARY_DB_URL') or 'sqlite:///vocabulary.db',
        #增加resources_manager的数据库到file_manager工程中来
        'resources_manager': os.environ.get('SRCS_MANAGER_DB_URL') or 'sqlite:///resources_manager.db',
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
