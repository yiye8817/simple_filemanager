"""文件夹插件模型。

一个"插件"绑定到某个文件夹（``folder_id``），客户端通过 ``invoke`` API 调
用插件、传 ``action`` 和 ``params``，由插件读取文件夹下的文件并返回结构化
结果。

设计目标：
- **两种形态**（``kind`` 字段）：

  - ``declarative``：纯配置（JSON），按规则在文件夹下匹配文件并组装响应；
    安全、可控
  - ``python``：用户上传一段 Python 代码（实现 ``invoke(action, params, ctx)``
    函数），最大灵活但只能让插件 owner 自己调用——**信任模型**

- **作用域**：插件只能访问其绑定文件夹下的文件信息；通过 ``include_subdirs``
  控制是否递归
- **可观测性**：记录最近一次调用时间/状态/错误，支持 ``invoke_count`` 计数

字段说明：

- ``folder_id``        关联 :class:`app.models.file.File`（``is_directory=True``）
- ``name``             插件名（同一文件夹下唯一），用于 URL 寻址
- ``kind``             ``declarative`` / ``python``
- ``description``      可选，给前端展示
- ``config``           ``kind='declarative'`` 时使用，JSON 字符串，描述 action 规则
- ``code``             ``kind='python'`` 时使用，Python 源码
- ``include_subdirs``  插件 ctx 列文件时是否递归（默认 True）
- ``enabled``          软开关
"""
from __future__ import annotations

import json
from datetime import datetime

from app import db


VALID_KINDS = ('declarative', 'python')


class FolderPlugin(db.Model):
    __tablename__ = 'folder_plugin'
    __table_args__ = (
        db.UniqueConstraint('folder_id', 'name', name='uq_folder_plugin_name'),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    folder_id = db.Column(db.Integer, db.ForeignKey('file.id'), nullable=False, index=True)

    name = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default='declarative')
    description = db.Column(db.String(512))

    config = db.Column(db.Text)
    code = db.Column(db.Text)

    include_subdirs = db.Column(db.Boolean, default=True, nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    # public=True 时, 该插件可被任意客户端通过 /api/public/... 调用 (无授权),
    # 同时 PluginContext.get_file_url 会返回带 HMAC 签名的公开下载 URL。默认
    # False, 保持"仅 owner / API Key 可访问"的语义。
    public = db.Column(db.Boolean, default=False, nullable=False)

    invoke_count = db.Column(db.Integer, default=0, nullable=False)
    last_invoked_at = db.Column(db.DateTime)
    last_status = db.Column(db.String(20))
    last_error = db.Column(db.String(512))

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow,
        onupdate=datetime.utcnow, nullable=False,
    )

    def parsed_config(self) -> dict:
        if not self.config:
            return {}
        try:
            return json.loads(self.config)
        except json.JSONDecodeError:
            return {}

    def to_dict(self, folder=None, *, include_source: bool = False) -> dict:
        out = {
            'id': self.id,
            'folder_id': self.folder_id,
            'folder_name': folder.name if folder else None,
            'folder_path': folder.path if folder else None,
            'name': self.name,
            'kind': self.kind,
            'description': self.description,
            'include_subdirs': self.include_subdirs,
            'enabled': self.enabled,
            'public': bool(self.public),
            'invoke_count': self.invoke_count,
            'last_invoked_at': self.last_invoked_at.isoformat() if self.last_invoked_at else None,
            'last_status': self.last_status,
            'last_error': self.last_error,
            'has_config': bool(self.config),
            'has_code': bool(self.code),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_source:
            out['config'] = self.parsed_config() if self.kind == 'declarative' else None
            out['code'] = self.code if self.kind == 'python' else None
        return out
