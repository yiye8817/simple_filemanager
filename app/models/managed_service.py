"""服务管理模块的数据模型。

围绕「上传 tar.gz → 解压 → 通过 run.sh 拉起 → 通过 frpc 暴露」这条链路，
我们只需要一个 ``ManagedService`` 表保存元信息：上传归档路径、解压目录、绑定的
本地端口/远端端口、运行时的 PID 与状态、最近一次日志路径。

进程级运行时状态（pid、status）会随启停被刷新；查询前必须用 ``os.kill(pid, 0)``
做一次活性探测以避免出现"DB 显示 running 但其实进程早已退出"的脏状态。
"""
from datetime import datetime

from app import db


class ManagedService(db.Model):
    __tablename__ = 'managed_service'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    name = db.Column(db.String(120), nullable=False)
    """服务展示名（用户填）"""

    archive_filename = db.Column(db.String(255), nullable=False)
    """上传时的原文件名，如 ``my-bot.tar.gz``"""

    archive_path = db.Column(db.String(512), nullable=False)
    """tar.gz 在磁盘上的绝对路径"""

    extract_dir = db.Column(db.String(512), nullable=False)
    """解压目录的绝对路径；run.sh 会在此目录下执行"""

    local_port = db.Column(db.Integer, nullable=False)
    """frpc.toml 中某个 proxy 的 ``localPort``，会作为 run.sh 的入参传入"""

    remote_port = db.Column(db.Integer, nullable=False)
    """frpc.toml 中对应 proxy 的 ``remotePort``，用于拼接外网访问地址"""

    proxy_name = db.Column(db.String(120))
    """frpc.toml 中 proxy 的 ``name``，仅用于 UI 展示"""

    status = db.Column(db.String(16), default='idle', nullable=False)
    """``idle`` / ``running`` / ``stopped`` / ``failed``"""

    pid = db.Column(db.Integer)
    """运行中进程 pid；停止后置 None"""

    last_started_at = db.Column(db.DateTime)
    last_stopped_at = db.Column(db.DateTime)
    last_exit_code = db.Column(db.Integer)
    last_error = db.Column(db.String(512))

    log_path = db.Column(db.String(512))
    """run.sh 的标准输出/错误重定向到此文件；``GET /log`` 直接读取"""

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
            'archive_filename': self.archive_filename,
            'local_port': self.local_port,
            'remote_port': self.remote_port,
            'proxy_name': self.proxy_name,
            'status': self.status,
            'pid': self.pid,
            'last_started_at': self.last_started_at.isoformat() if self.last_started_at else None,
            'last_stopped_at': self.last_stopped_at.isoformat() if self.last_stopped_at else None,
            'last_exit_code': self.last_exit_code,
            'last_error': self.last_error,
            'access_url': access_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
