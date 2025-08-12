from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from app import db
# db = SQLAlchemy()
#替换db为目前已有系统的db

class Resource(db.Model):
    __bind_key__ = 'resources_manager'  # 添加数据库的指定使用resources_manager数据库
    __tablename__ = 'resources'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    resource_link = db.Column(db.String(500), nullable=False)
    poster_url = db.Column(db.String(500))
    category = db.Column(db.String(50), nullable=False)  # 视频、音频、文档、应用、链接、其他
    tags = db.Column(db.String(200))  # 特征字
    proxy = db.Column(db.String(100))
    details = db.Column(db.Text)
    author = db.Column(db.String(100))
    source = db.Column(db.String(200))
    record_time = db.Column(db.DateTime, default=datetime.utcnow)
    rating = db.Column(db.Float, default=0.0)
    subcategory = db.Column(db.String(100))
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'resource_link': self.resource_link,
            'poster_url': self.poster_url,
            'category': self.category,
            'tags': self.tags,
            'proxy': self.proxy,
            'details': self.details,
            'author': self.author,
            'source': self.source,
            'record_time': self.record_time.strftime('%Y-%m-%d %H:%M:%S') if self.record_time else None,
            'rating': self.rating,
            'subcategory': self.subcategory
        }