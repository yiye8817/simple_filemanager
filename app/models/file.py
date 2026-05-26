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
    #添加版本管理字段
    version_str = db.Column(db.String(16))
    # 仅对 is_directory=True 有意义: 标记该目录是否被分享到 MusicFree 插件
    # ("白名单" 模式: 只有 music_shared=True 的目录及其下音频才会暴露给 /api/music/*)
    music_shared = db.Column(db.Boolean, default=False, nullable=False)
    
    children = db.relationship('File', backref=db.backref('parent', remote_side=[id]), lazy=True)
    
    def to_dict(self, child_count=None):
        """序列化为 dict。

        Args:
            child_count: 仅对 ``is_directory=True`` 有意义。若提供，``size_formatted``
                会显示为 "N 项" 而不是 "0 B"，并额外暴露 ``child_count`` 字段。
                批量场景下请用 ``_list_with_child_counts``（路由层 helper）一次性算好
                传入，避免 N+1 查询。
        """
        if self.is_directory and child_count is not None:
            size_formatted = f'{int(child_count)} 项'
        else:
            size_formatted = format_size(self.size)
        return {
            'id': self.id,
            'name': self.name,
            'path': self.path,
            'size': self.size,
            'size_formatted': size_formatted,
            'type': self.file_type,
            'is_dir': self.is_directory,
            'modified': self.modified_at.strftime('%Y-%m-%d %H:%M'),
            'icon': 'bi-folder' if self.is_directory else get_file_icon(self.path),
            'is_public': self.is_public,
            'public_share_id': self.public_share_id,
            'resource_id':self.resource_id,
            'version_str':self.version_str,
            'child_count': int(child_count) if (self.is_directory and child_count is not None) else None,
            'music_shared': bool(self.music_shared) if self.is_directory else None,
        }
    
    def generate_share_id(self):
        self.public_share_id = str(uuid.uuid4())
        return self.public_share_id
#更新文件管理版本
def update_file_version(file_id,version_str):
    file = File.query.get(file_id)
    if not file:
        return False, "File not found"
    
    # resource = Resource.query.get(resource_id)
    # if not resource:
    #     return False, "Resource not found"
    
    try:
        file.version_str = version_str
        db.session.commit()
        return True, {"file_id": file.id, "version_str": file.version_str}
    except Exception as e:
        db.session.rollback()
        return False, f"Database error: {str(e)}"
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
def reset_resource_id(resource_id):
    """
    批量更新指定resource_id的File记录为0
    
    :param resource_id: 要查询的resource_id
    :return: 更新的记录数量
    """
    try:
        # 使用update方法批量更新
        updated_count = File.query.filter_by(resource_id=resource_id).update(
            {'resource_id': 0}
        )
        
        db.session.commit()
        return updated_count
        
    except Exception as e:
        db.session.rollback()
        print(f"Error: {e}")
        return -1