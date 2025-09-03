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
    tags = db.Column(db.String(200))  # 特征字,用来描述关键的信息，比如apk版本号
    proxy = db.Column(db.String(100))
    details = db.Column(db.Text)
    author = db.Column(db.String(100))
    source = db.Column(db.String(200))
    record_time = db.Column(db.DateTime, default=datetime.utcnow)
    rating = db.Column(db.Float, default=0.0)
    subcategory = db.Column(db.String(100))
    # resource = db.relationship('Resource', backref='files', lazy=True)
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
def update_resource_fields(resource_id, poster_url=None, tags=None):
    """
    更新资源的poster_url或tags字段
    :param resource_id: 资源ID
    :param poster_url: 新的poster_url（可选）
    :param tags: 新的tags（可选）
    :return: (success, result) 元组
    """
    if poster_url is None and tags is None:
        return False, "No fields to update"
    
    resource = Resource.query.get(resource_id)
    if not resource:
        return False, "Resource not found"
    
    try:
        if poster_url is not None:
            resource.poster_url = poster_url
        if tags is not None:
            resource.tags = tags
        
        db.session.commit()
        return True, {
            "resource_id": resource.id,
            "updated_fields": {
                "poster_url": poster_url,
                "tags": tags
            }
        }
    except Exception as e:
        db.session.rollback()
        return False, f"Database error: {str(e)}"
def get_resource_poster(resource_id):
    """
    获取资源的poster_url
    :param resource_id: 资源ID
    :return: (success, result) 元组
    """
    resource = Resource.query.get(resource_id)
    if not resource:
        return False, "Resource not found"
    
    return True, resource.poster_url
# {
#         "resource_id": resource.id,
#         "poster_url": resource.poster_url,
#         "title": resource.title
#     }
