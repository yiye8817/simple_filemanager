"""Docker 服务模型。

用户上传一个 docker-compose.yml（或包含它的 tar.gz）→ 我们解析、保存、
按 frpc 选定的端口注入 ``PORT`` 环境变量，再用 ``docker compose up -d`` 拉起。
"""
from datetime import datetime

from app import db


class DockerService(db.Model):
    __tablename__ = 'docker_service'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    name = db.Column(db.String(120), nullable=False)
    """服务展示名（同时用于生成 ``docker compose -p <project>`` 项目名）"""

    project_name = db.Column(db.String(120), nullable=False, unique=True)
    """compose 项目名：``svc_<user>_<id>``，受 docker 项目名规则约束（小写 / 短杠）"""

    upload_filename = db.Column(db.String(255), nullable=False)
    """上传时的原始文件名，可能是 ``docker-compose.yml`` 或 ``demo.tar.gz``"""

    compose_path = db.Column(db.String(512), nullable=False)
    """实际生效的 compose 文件绝对路径（解压后或单文件保存路径）"""

    work_dir = db.Column(db.String(512), nullable=False)
    """compose 执行时的 cwd"""

    local_port = db.Column(db.Integer, nullable=False)
    remote_port = db.Column(db.Integer, nullable=False)
    proxy_name = db.Column(db.String(120))

    status = db.Column(db.String(16), default='idle', nullable=False)
    """``idle`` / ``pulling`` / ``running`` / ``stopped`` / ``failed``"""

    container_ids = db.Column(db.Text)
    """JSON 文本：``["abc123", ...]``；启动后用 docker compose ps 写入"""

    last_started_at = db.Column(db.DateTime)
    last_stopped_at = db.Column(db.DateTime)
    last_error = db.Column(db.String(1024))

    log_path = db.Column(db.String(512))
    """compose up 时 stdout/stderr 重定向到此文件，供操作回放查看"""

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def to_dict(self, server_addr: str | None = None) -> dict:
        access_url = None
        if server_addr and self.remote_port:
            access_url = f"http://{server_addr}:{self.remote_port}"
        return {
            'id': self.id,
            'name': self.name,
            'project_name': self.project_name,
            'upload_filename': self.upload_filename,
            'local_port': self.local_port,
            'remote_port': self.remote_port,
            'proxy_name': self.proxy_name,
            'status': self.status,
            'last_started_at': self.last_started_at.isoformat() if self.last_started_at else None,
            'last_stopped_at': self.last_stopped_at.isoformat() if self.last_stopped_at else None,
            'last_error': self.last_error,
            'access_url': access_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
