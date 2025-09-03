from app import db
from datetime import datetime
import uuid
from ..utils.helpers import format_size, get_file_icon

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
        # 新增的Resource外键, db.ForeignKey('resources.id')
    resource_id = db.Column(db.Integer)
    
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
            'public_share_id': self.public_share_id,
            'resource_id':self.resource_id
        }
    
    def generate_share_id(self):
        self.public_share_id = str(uuid.uuid4())
        return self.public_share_id
def update_file_resource(file_id, resource_id):
    """
    更新文件的关联资源ID
    :param file_id: 要更新的文件ID
    :param resource_id: 要关联的资源ID
    :return: (success, result) 元组，success为布尔值表示是否成功
    """
    file = File.query.get(file_id)
    if not file:
        return False, "File not found"
    
    # resource = Resource.query.get(resource_id)
    # if not resource:
    #     return False, "Resource not found"
    
    try:
        file.resource_id = resource_id
        db.session.commit()
        return True, {"file_id": file.id, "resource_id": file.resource_id}
    except Exception as e:
        db.session.rollback()
        return False, f"Database error: {str(e)}"
