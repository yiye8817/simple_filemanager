"""文件夹变化监听 + Webhook 模型。

设计目的：让用户对**某一个文件夹**配置一个回调 URL，当该文件夹（及其子目录）下
任意文件发生 ``created`` / ``modified`` / ``deleted`` 事件时，由服务端异步 POST
事件 payload 到配置的 URL。

事件触发是"业务层挂钩"而非"磁盘 watchdog"：所有文件变更都走 ``/api/upload``、
``/api/upload/merge``、``/api/newfile``、``/api/save-file``、``/api/import/url``、
``/api/files/<id> DELETE`` 这几个 API 写库；只要在这些点上调用
``webhook_service.notify_file_event`` 即可保证不漏报。

字段说明：

- ``folder_id``        关联 :class:`app.models.file.File`（``is_directory=True``）
- ``url``               回调地址（http/https）
- ``secret``            可选；非空则在请求头追加 ``X-Signature: sha256=<hmac>``，方便接收方校验
- ``events``            逗号分隔，子集 of {``created``, ``modified``, ``deleted``}；空 = 全部
- ``include_subdirs``   是否递归监听子目录，默认 True
- ``enabled``           开/关
"""
from datetime import datetime

from app import db


VALID_EVENTS = ('created', 'modified', 'deleted')


class FolderWatch(db.Model):
    __tablename__ = 'folder_watch'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    folder_id = db.Column(db.Integer, db.ForeignKey('file.id'), nullable=False, index=True)

    url = db.Column(db.String(1024), nullable=False)
    secret = db.Column(db.String(255))
    events = db.Column(db.String(64), default='', nullable=False)
    include_subdirs = db.Column(db.Boolean, default=True, nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)

    last_triggered_at = db.Column(db.DateTime)
    last_status_code = db.Column(db.Integer)
    last_error = db.Column(db.String(512))
    trigger_count = db.Column(db.Integer, default=0, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def event_list(self) -> list[str]:
        return [e.strip() for e in (self.events or '').split(',') if e.strip()]

    def matches_event(self, event: str) -> bool:
        evs = self.event_list()
        return not evs or event in evs

    def to_dict(self, folder=None) -> dict:
        return {
            'id': self.id,
            'folder_id': self.folder_id,
            'folder_name': folder.name if folder else None,
            'folder_path': folder.path if folder else None,
            'url': self.url,
            'has_secret': bool(self.secret),
            'events': self.event_list() or list(VALID_EVENTS),
            'include_subdirs': self.include_subdirs,
            'enabled': self.enabled,
            'last_triggered_at': self.last_triggered_at.isoformat() if self.last_triggered_at else None,
            'last_status_code': self.last_status_code,
            'last_error': self.last_error,
            'trigger_count': self.trigger_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
