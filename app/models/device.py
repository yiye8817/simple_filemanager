"""设备管理模块的数据模型。

围绕「远程设备 ↔ 文件管理服务器」的反向连接模型设计:

1. **Device**:   一台远程设备的元信息。客户端 (agent) 安装在远程机器上,
   启动时拿着 ``device_token`` 登录, 之后周期性 ``/heartbeat`` 上报资源。
2. **DeviceTask**:  服务端要让设备执行的工作 (执行命令 / 启停服务 / 同步归档)。
   客户端通过 ``GET /api/agent/poll`` 长轮询拿到 ``pending`` 任务, 执行后通过
   ``POST /api/agent/tasks/<id>/result`` 上报结果, 状态变为 ``done``/``error``。
3. **DeviceManagedService**:  设备上运行的 tar.gz 服务 (与 ManagedService 同构,
   只是宿主从"本机"变成"远程设备")。状态由 agent 上报维持。

整体协议是 **pull 模型**:  客户端始终发起请求, 服务端只接受/响应。这种设计
对 NAT / 防火墙完全透明, 不需要在客户端开端口, 也不依赖额外的内网穿透组件。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from app import db


# 心跳超时阈值: 超过此时间没收到心跳则视为离线 (UI 用 last_seen_at 实时算)
ONLINE_THRESHOLD_SECONDS = 90


class Device(db.Model):
    __tablename__ = 'device'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    device_uid = db.Column(db.String(64), nullable=False, unique=True, index=True)
    """随机生成的设备唯一标识 (32 字节 hex), 给客户端在 URL/标识中使用"""

    device_token = db.Column(db.String(96), nullable=False, unique=True, index=True)
    """客户端鉴权用 token; 通过 ``X-Device-Token`` 头携带"""

    name = db.Column(db.String(120), nullable=False)
    """设备展示名 (用户填), 同一用户下唯一"""

    description = db.Column(db.String(512))
    location = db.Column(db.String(255))
    """位置信息 (例如 ``Beijing / IDC-2`` 或 ``192.168.1.0/24 内网``); 手动填或自动推断"""

    tags = db.Column(db.String(255))
    """逗号分隔的标签, 便于前端检索过滤"""

    hostname = db.Column(db.String(255))
    os_name = db.Column(db.String(64))
    os_version = db.Column(db.String(120))
    arch = db.Column(db.String(32))
    agent_version = db.Column(db.String(32))

    local_ip = db.Column(db.String(64))
    public_ip = db.Column(db.String(64))
    """客户端心跳里上报; public_ip 也兜底用请求 ``request.remote_addr``"""

    tunnel_info = db.Column(db.Text)
    """JSON: 内网穿透信息 (如 frp 远端 host:port 列表 / nat type / 自描述串)"""

    stats_json = db.Column(db.Text)
    """JSON: 最近一次心跳的资源快照 (cpu/memory/disk/gpu/load/uptime/net 等)"""

    last_seen_at = db.Column(db.DateTime, index=True)
    last_heartbeat_at = db.Column(db.DateTime)
    last_ip = db.Column(db.String(64))
    """心跳请求来源 IP (服务器视角), 区别于客户端自报 public_ip"""

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint('user_id', 'name', name='uq_device_user_name'),
    )

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------
    @property
    def online(self) -> bool:
        if not self.last_seen_at:
            return False
        return (datetime.utcnow() - self.last_seen_at) < timedelta(seconds=ONLINE_THRESHOLD_SECONDS)

    # ------------------------------------------------------------------
    # 工厂 / token 处理
    # ------------------------------------------------------------------
    @staticmethod
    def gen_uid() -> str:
        return secrets.token_hex(16)

    @staticmethod
    def gen_token() -> str:
        return secrets.token_urlsafe(48)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self, *, include_token: bool = False) -> dict:
        stats = {}
        if self.stats_json:
            try:
                stats = json.loads(self.stats_json)
            except (TypeError, ValueError):
                stats = {}

        tunnel = {}
        if self.tunnel_info:
            try:
                tunnel = json.loads(self.tunnel_info)
            except (TypeError, ValueError):
                tunnel = {}

        d = {
            'id': self.id,
            'device_uid': self.device_uid,
            'name': self.name,
            'description': self.description,
            'location': self.location,
            'tags': [t.strip() for t in (self.tags or '').split(',') if t.strip()],
            'hostname': self.hostname,
            'os_name': self.os_name,
            'os_version': self.os_version,
            'arch': self.arch,
            'agent_version': self.agent_version,
            'local_ip': self.local_ip,
            'public_ip': self.public_ip,
            'last_ip': self.last_ip,
            'tunnel_info': tunnel,
            'stats': stats,
            'online': self.online,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'last_heartbeat_at': self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_token:
            d['device_token'] = self.device_token
        return d


class DeviceTask(db.Model):
    """下发给设备的待执行任务 (命令 / 服务管理)。

    生命周期: ``pending`` → (agent poll 后) ``running`` → ``done`` / ``error`` / ``timeout``。
    每个任务有 ``kind`` 区分具体动作, ``payload_json`` 是参数, ``result_json`` 是结果。
    """
    __tablename__ = 'device_task'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    kind = db.Column(db.String(32), nullable=False)
    """``exec`` (执行 shell 命令)
    / ``service_install`` / ``service_start`` / ``service_stop`` / ``service_delete``
    / ``service_log`` (拉取日志)
    """

    payload_json = db.Column(db.Text)
    result_json = db.Column(db.Text)

    status = db.Column(db.String(16), nullable=False, default='pending', index=True)
    """``pending`` / ``running`` / ``done`` / ``error`` / ``timeout`` / ``cancelled``"""

    exit_code = db.Column(db.Integer)
    error = db.Column(db.String(1024))

    # 大文本日志独立存; result_json 只放摘要避免列体积失控
    stdout = db.Column(db.Text)
    stderr = db.Column(db.Text)

    # 关联的设备服务 (service_* 类任务)
    service_id = db.Column(db.Integer, db.ForeignKey('device_managed_service.id'))

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)

    # 长轮询/调度用: agent 拉到后写入, 避免重复派发
    claimed_at = db.Column(db.DateTime)
    claim_token = db.Column(db.String(64))

    def to_dict(self) -> dict:
        try:
            payload = json.loads(self.payload_json) if self.payload_json else {}
        except (TypeError, ValueError):
            payload = {}
        try:
            result = json.loads(self.result_json) if self.result_json else {}
        except (TypeError, ValueError):
            result = {}
        return {
            'id': self.id,
            'device_id': self.device_id,
            'kind': self.kind,
            'payload': payload,
            'result': result,
            'status': self.status,
            'exit_code': self.exit_code,
            'error': self.error,
            'stdout': self.stdout or '',
            'stderr': self.stderr or '',
            'service_id': self.service_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
        }


class DeviceManagedService(db.Model):
    """远程设备上运行的 tar.gz 服务的元信息。

    与本机 ``ManagedService`` 的区别:
    - ``archive_path`` 指向服务器侧的 tar.gz 存储路径 (uploads/device-services/...)
    - 设备客户端通过 ``GET /api/agent/services/<id>/archive`` 拉取归档
    - ``status`` / ``pid`` / ``last_*`` 由 agent 通过任务结果与心跳维护
    """
    __tablename__ = 'device_managed_service'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)

    name = db.Column(db.String(120), nullable=False)
    archive_filename = db.Column(db.String(255), nullable=False)
    archive_path = db.Column(db.String(512), nullable=False)
    """服务器侧保存的 tar.gz 绝对路径"""

    archive_size = db.Column(db.Integer)
    archive_sha256 = db.Column(db.String(64))

    local_port = db.Column(db.Integer)
    """传给 ``run.sh`` 的端口参数 (设备本地端口); 可选"""

    run_args = db.Column(db.String(255))
    """附加启动参数, 空格分隔; 拼到 ``bash run.sh <port> <extra>``"""

    env_json = db.Column(db.Text)
    """JSON: 设备端启动 run.sh 时叠加的环境变量"""

    status = db.Column(db.String(16), default='idle', nullable=False)
    """``idle`` / ``installing`` / ``running`` / ``stopped`` / ``failed``"""

    pid = db.Column(db.Integer)
    last_started_at = db.Column(db.DateTime)
    last_stopped_at = db.Column(db.DateTime)
    last_exit_code = db.Column(db.Integer)
    last_error = db.Column(db.String(1024))
    last_log = db.Column(db.Text)
    """最近一次拉日志任务的快照, agent 上报后覆盖"""
    last_log_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint('device_id', 'name', name='uq_device_service_name'),
    )

    def to_dict(self) -> dict:
        try:
            env = json.loads(self.env_json) if self.env_json else {}
        except (TypeError, ValueError):
            env = {}
        return {
            'id': self.id,
            'device_id': self.device_id,
            'name': self.name,
            'archive_filename': self.archive_filename,
            'archive_size': self.archive_size,
            'archive_sha256': self.archive_sha256,
            'local_port': self.local_port,
            'run_args': self.run_args,
            'env': env,
            'status': self.status,
            'pid': self.pid,
            'last_started_at': self.last_started_at.isoformat() if self.last_started_at else None,
            'last_stopped_at': self.last_stopped_at.isoformat() if self.last_stopped_at else None,
            'last_exit_code': self.last_exit_code,
            'last_error': self.last_error,
            'last_log_at': self.last_log_at.isoformat() if self.last_log_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
