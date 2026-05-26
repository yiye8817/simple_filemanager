"""设备管理业务层。

集中三类操作:
1. **设备生命周期**: 注册 (Web/API 创建)、agent 心跳、删除。
2. **任务队列**: 用户侧 enqueue, agent 侧 claim/finalize。同一个任务最多被一个
   agent 持有 (用 ``claim_token`` 简易锁), 防止重复执行。
3. **设备服务**: 服务器侧保存归档 + 元数据, 任务化下发安装/启停/卸载。

不依赖 Flask 上下文; 只用 SQLAlchemy session, 单测/脚本可直接 import 使用。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from app import db
from app.models import Device, DeviceManagedService, DeviceTask, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

_DEVICE_NAME_RE = re.compile(r'^[A-Za-z0-9_\-\.\s\u4e00-\u9fa5]{1,120}$')


def validate_device_name(name: str) -> str:
    name = (name or '').strip()
    if not name:
        raise ValueError('设备名不能为空')
    if not _DEVICE_NAME_RE.match(name):
        raise ValueError('设备名仅允许字母/数字/中文/空格/_-.，长度 ≤120')
    return name


def sha256_of_file(path: str, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for buf in iter(lambda: fh.read(chunk), b''):
            h.update(buf)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Device 注册 / 查询
# ---------------------------------------------------------------------------

def create_device(user_id: int, *, name: str, description: str = '',
                  location: str = '', tags: str = '') -> Device:
    name = validate_device_name(name)
    if Device.query.filter_by(user_id=user_id, name=name).first():
        raise ValueError(f'已存在同名设备: {name}')
    d = Device(
        user_id=user_id,
        device_uid=Device.gen_uid(),
        device_token=Device.gen_token(),
        name=name,
        description=(description or '').strip()[:512],
        location=(location or '').strip()[:255],
        tags=(tags or '').strip()[:255],
    )
    db.session.add(d)
    db.session.commit()
    return d


def rotate_token(device: Device) -> str:
    device.device_token = Device.gen_token()
    db.session.commit()
    return device.device_token


def get_device_by_token(token: str) -> Device | None:
    if not token:
        return None
    return Device.query.filter_by(device_token=token).first()


def get_device_by_uid(device_uid: str) -> Device | None:
    if not device_uid:
        return None
    return Device.query.filter_by(device_uid=device_uid).first()


def enroll_device(
    *, device_uid: str, owner_user_id: int,
    suggested_name: str = '', description: str = '',
    location: str = '', tags: str = '',
) -> tuple[Device, bool]:
    """**自助注册**: 给定 ``device_uid`` 找设备, 没有就建一个。

    返回 ``(device, created)``; ``created=True`` 表示是新建的。

    - 已有 device → 直接返回, 不动 token (允许 agent 反复调用 enroll 拿到当前 token)。
    - 不存在 → 创建 (生成新 token)。如果 ``suggested_name`` 已被同 owner 用过,
      自动追加 ``-2``/``-3``... 直到唯一, 避免 enroll 时硬失败。
    """
    device_uid = (device_uid or '').strip().lower()
    if not device_uid or len(device_uid) < 8 or len(device_uid) > 64:
        raise ValueError('device_uid 长度须在 [8, 64]')
    # 简单字符校验, 避免任意字符
    import re as _re
    if not _re.fullmatch(r'[A-Za-z0-9._\-]+', device_uid):
        raise ValueError('device_uid 仅允许 A-Z a-z 0-9 . _ -')

    existed = get_device_by_uid(device_uid)
    if existed:
        # 注意: enroll 不切 owner; agent 跨用户搬迁请通过 rotate-token 走 Web UI
        return existed, False

    # 自动取名: 优先 suggested_name (主机名), 再退到 ``device-<uid 前 8 位>``
    base_name = (suggested_name or '').strip() or f'device-{device_uid[:8]}'
    try:
        base_name = validate_device_name(base_name)
    except ValueError:
        # 主机名里可能有非法字符 (e.g. ``my_host.local``), 兜底用 uid 前缀
        base_name = f'device-{device_uid[:8]}'

    name = base_name
    suffix = 2
    while Device.query.filter_by(user_id=owner_user_id, name=name).first():
        name = f'{base_name}-{suffix}'
        suffix += 1
        if suffix > 99:
            raise ValueError('无法生成唯一设备名 (尝试了 100 次)')

    d = Device(
        user_id=owner_user_id,
        device_uid=device_uid,
        device_token=Device.gen_token(),
        name=name,
        description=(description or '')[:512],
        location=(location or '')[:255],
        tags=(tags or '')[:255],
    )
    db.session.add(d)
    db.session.commit()
    logger.info('agent 自助 enroll: uid=%s name=%s owner=%s', device_uid, name, owner_user_id)
    return d, True


def resolve_default_owner_user_id(app_config) -> int | None:
    """决定一个 agent 自助注册的归属用户。

    优先级:
    1. ``DEVICE_AGENT_DEFAULT_USER_ID`` (config)
    2. 任意一个 admin 用户 (如果 User 模型有 ``is_admin``)
    3. id 最小的用户

    没有任何用户 → ``None`` (路由层应 503)。
    """
    explicit = (app_config or {}).get('DEVICE_AGENT_DEFAULT_USER_ID')
    if explicit:
        try:
            uid = int(explicit)
        except (TypeError, ValueError):
            uid = None
        if uid and User.query.filter_by(id=uid).first():
            return uid

    if hasattr(User, 'is_admin'):
        admin = User.query.filter_by(is_admin=True).order_by(User.id.asc()).first()
        if admin:
            return admin.id

    any_user = User.query.order_by(User.id.asc()).first()
    return any_user.id if any_user else None


def own_device(user_id: int, device_id: int) -> Device | None:
    return Device.query.filter_by(id=device_id, user_id=user_id).first()


def list_user_devices(user_id: int, *, query: str = '',
                      online_only: bool = False) -> list[Device]:
    q = Device.query.filter_by(user_id=user_id)
    query = (query or '').strip()
    if query:
        like = f'%{query}%'
        q = q.filter(db.or_(
            Device.name.ilike(like),
            Device.description.ilike(like),
            Device.location.ilike(like),
            Device.tags.ilike(like),
            Device.hostname.ilike(like),
            Device.local_ip.ilike(like),
            Device.public_ip.ilike(like),
            Device.last_ip.ilike(like),
        ))
    devices = q.order_by(Device.id.desc()).all()
    if online_only:
        devices = [d for d in devices if d.online]
    return devices


def delete_device(device: Device, *, upload_root: str | None = None) -> None:
    """级联删除设备 + 任务 + 服务 + 服务归档目录。"""
    # 1) 服务归档目录清理
    if upload_root:
        try:
            sdir = device_storage_root(upload_root, device.user_id, device.id)
            _safe_rmtree(sdir)
        except Exception as exc:  # noqa: BLE001
            logger.warning('清理设备 %s 存储目录失败: %s', device.id, exc)
    # 2) DB 级联
    DeviceTask.query.filter_by(device_id=device.id).delete(synchronize_session=False)
    DeviceManagedService.query.filter_by(device_id=device.id).delete(synchronize_session=False)
    db.session.delete(device)
    db.session.commit()


# ---------------------------------------------------------------------------
# 心跳 / 资源上报
# ---------------------------------------------------------------------------

def apply_heartbeat(device: Device, *, payload: dict, request_ip: str | None) -> Device:
    """合并 agent 上报的字段; payload 字段都是 optional。"""
    now = datetime.utcnow()
    device.last_heartbeat_at = now
    device.last_seen_at = now
    if request_ip:
        device.last_ip = request_ip[:64]

    def _take(field: str, max_len: int | None = None):
        v = payload.get(field)
        if v is None:
            return None
        s = str(v)
        if max_len:
            s = s[:max_len]
        return s

    for attr, max_len in [
        ('hostname', 255), ('os_name', 64), ('os_version', 120),
        ('arch', 32), ('agent_version', 32),
        ('local_ip', 64), ('public_ip', 64),
    ]:
        v = _take(attr, max_len)
        if v:
            setattr(device, attr, v)

    if 'stats' in payload and isinstance(payload['stats'], (dict, list)):
        try:
            # 详细 stats (CPU model / per-core / 多磁盘 / 多 GPU / 多网卡) 可能上 100KB,
            # 留 256KB 上限给后续扩展; SQLite Text 实际可以更大, 没问题。
            device.stats_json = json.dumps(payload['stats'], ensure_ascii=False)[:262144]
        except (TypeError, ValueError):
            pass
    if 'tunnel_info' in payload and isinstance(payload['tunnel_info'], (dict, list)):
        try:
            device.tunnel_info = json.dumps(payload['tunnel_info'], ensure_ascii=False)[:65536]
        except (TypeError, ValueError):
            pass

    db.session.commit()
    return device


# ---------------------------------------------------------------------------
# Task: enqueue / claim / finalize
# ---------------------------------------------------------------------------

TASK_KIND_EXEC = 'exec'
TASK_KIND_SERVICE_INSTALL = 'service_install'
TASK_KIND_SERVICE_START = 'service_start'
TASK_KIND_SERVICE_STOP = 'service_stop'
TASK_KIND_SERVICE_DELETE = 'service_delete'
TASK_KIND_SERVICE_LOG = 'service_log'

ALL_TASK_KINDS = {
    TASK_KIND_EXEC,
    TASK_KIND_SERVICE_INSTALL,
    TASK_KIND_SERVICE_START,
    TASK_KIND_SERVICE_STOP,
    TASK_KIND_SERVICE_DELETE,
    TASK_KIND_SERVICE_LOG,
}


def enqueue_task(device: Device, *, user_id: int, kind: str,
                 payload: dict | None = None,
                 service: DeviceManagedService | None = None) -> DeviceTask:
    if kind not in ALL_TASK_KINDS:
        raise ValueError(f'未知任务类型: {kind}')
    t = DeviceTask(
        device_id=device.id,
        user_id=user_id,
        kind=kind,
        payload_json=json.dumps(payload or {}, ensure_ascii=False)[:65536],
        status='pending',
        service_id=service.id if service else None,
    )
    db.session.add(t)
    db.session.commit()
    return t


def claim_next_tasks(device: Device, *, limit: int = 8) -> list[DeviceTask]:
    """agent 长轮询返回前调用; 把 ``pending`` 任务批量切到 ``running``。

    为避免并发 race condition, 每个任务写一个 ``claim_token``; finalize 时校验。
    """
    rows = (
        DeviceTask.query
        .filter_by(device_id=device.id, status='pending')
        .order_by(DeviceTask.created_at.asc())
        .limit(limit)
        .all()
    )
    if not rows:
        return []
    now = datetime.utcnow()
    for t in rows:
        t.status = 'running'
        t.started_at = now
        t.claimed_at = now
        t.claim_token = secrets.token_hex(16)
    db.session.commit()
    return rows


def get_task(task_id: int) -> DeviceTask | None:
    return DeviceTask.query.filter_by(id=task_id).first()


def finalize_task(task: DeviceTask, *, status: str,
                  exit_code: int | None = None,
                  stdout: str = '', stderr: str = '',
                  error: str | None = None,
                  result: dict | None = None,
                  claim_token: str | None = None) -> DeviceTask:
    if claim_token and task.claim_token and claim_token != task.claim_token:
        raise PermissionError('claim_token 不匹配, 拒绝写入结果')
    if status not in ('done', 'error', 'timeout', 'cancelled'):
        raise ValueError(f'非法终态: {status}')
    task.status = status
    task.exit_code = exit_code
    task.error = (error or None)
    task.stdout = (stdout or '')[:200_000]
    task.stderr = (stderr or '')[:200_000]
    task.finished_at = datetime.utcnow()
    if result is not None:
        try:
            task.result_json = json.dumps(result, ensure_ascii=False)[:65536]
        except (TypeError, ValueError):
            task.result_json = None

    # 任务终态同步到 service 状态机
    if task.service_id:
        s = DeviceManagedService.query.get(task.service_id)
        if s:
            _apply_task_to_service(s, task)
    db.session.commit()
    return task


def cancel_pending_tasks(device: Device) -> int:
    rows = DeviceTask.query.filter_by(device_id=device.id, status='pending').all()
    for t in rows:
        t.status = 'cancelled'
        t.finished_at = datetime.utcnow()
    if rows:
        db.session.commit()
    return len(rows)


def list_recent_tasks(device: Device, *, limit: int = 50,
                      kind: str | None = None,
                      service_id: int | None = None) -> list[DeviceTask]:
    q = DeviceTask.query.filter_by(device_id=device.id)
    if kind:
        q = q.filter_by(kind=kind)
    if service_id:
        q = q.filter_by(service_id=service_id)
    return q.order_by(DeviceTask.id.desc()).limit(limit).all()


def reap_stale_running_tasks(*, age_seconds: int = 60 * 30) -> int:
    """超过 age_seconds 还在 running 的任务标记成 timeout, 避免幽灵任务卡住状态。"""
    cutoff = datetime.utcnow() - timedelta(seconds=age_seconds)
    rows = (
        DeviceTask.query
        .filter(DeviceTask.status == 'running')
        .filter(DeviceTask.started_at < cutoff)
        .all()
    )
    for t in rows:
        t.status = 'timeout'
        t.finished_at = datetime.utcnow()
        t.error = (t.error or '') + ' [server reaped]'
    if rows:
        db.session.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# 设备上的服务 (DeviceManagedService)
# ---------------------------------------------------------------------------

def device_storage_root(upload_root: str, user_id: int, device_id: int) -> str:
    return os.path.join(upload_root, 'devices', str(user_id), str(device_id))


def service_archive_path(upload_root: str, user_id: int, device_id: int,
                         service_id: int, filename: str) -> str:
    safe_name = filename.strip().replace('/', '_').replace('\\', '_') or 'archive.tar.gz'
    base = os.path.join(device_storage_root(upload_root, user_id, device_id),
                        'services', str(service_id))
    return os.path.join(base, safe_name)


def create_device_service(*, device: Device, user_id: int, name: str,
                          archive_file_storage, archive_filename: str,
                          local_port: int | None = None,
                          run_args: str = '',
                          env: dict | None = None,
                          upload_root: str) -> DeviceManagedService:
    name = validate_device_name(name)
    if DeviceManagedService.query.filter_by(device_id=device.id, name=name).first():
        raise ValueError(f'设备上已存在同名服务: {name}')
    if not archive_filename.lower().endswith(('.tar.gz', '.tgz')):
        raise ValueError('仅支持 .tar.gz / .tgz 归档')

    # 先建 DB 拿 id, 再用 id 拼路径
    s = DeviceManagedService(
        device_id=device.id,
        user_id=user_id,
        name=name,
        archive_filename=archive_filename,
        archive_path='',  # 占位
        local_port=local_port,
        run_args=(run_args or '').strip()[:255],
        env_json=json.dumps(env or {}, ensure_ascii=False),
        status='idle',
    )
    db.session.add(s)
    db.session.commit()

    dest = service_archive_path(upload_root, user_id, device.id, s.id, archive_filename)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    archive_file_storage.save(dest)
    s.archive_path = dest
    s.archive_size = os.path.getsize(dest)
    try:
        s.archive_sha256 = sha256_of_file(dest)
    except OSError as exc:
        logger.warning('sha256 计算失败 %s: %s', dest, exc)
        s.archive_sha256 = None
    db.session.commit()
    return s


def delete_device_service(s: DeviceManagedService, *, upload_root: str) -> None:
    # 关联任务也清理
    DeviceTask.query.filter_by(service_id=s.id).delete(synchronize_session=False)
    archive = s.archive_path
    base = os.path.dirname(archive) if archive else None
    db.session.delete(s)
    db.session.commit()
    if base and os.path.isdir(base):
        _safe_rmtree(base)


def own_service(user_id: int, service_id: int) -> DeviceManagedService | None:
    return DeviceManagedService.query.filter_by(id=service_id, user_id=user_id).first()


def list_device_services(device: Device) -> list[DeviceManagedService]:
    return (
        DeviceManagedService.query
        .filter_by(device_id=device.id)
        .order_by(DeviceManagedService.id.desc())
        .all()
    )


def update_service_meta(s: DeviceManagedService, *,
                        local_port: int | None = None,
                        run_args: str | None = None,
                        env: dict | None = None) -> DeviceManagedService:
    if local_port is not None:
        s.local_port = local_port if local_port > 0 else None
    if run_args is not None:
        s.run_args = run_args.strip()[:255]
    if env is not None:
        s.env_json = json.dumps(env or {}, ensure_ascii=False)
    db.session.commit()
    return s


def _apply_task_to_service(s: DeviceManagedService, t: DeviceTask) -> None:
    """根据任务终态推进 service 状态机。

    业务规则:
    - install/start 成功 → ``running`` (并尝试取 pid)
    - install/start 失败 → ``failed``
    - stop  成功 → ``stopped``
    - delete 成功 → ``stopped`` (Service 记录由调用方决定是否删 DB)
    - log   不改变状态; 把 stdout 存到 last_log
    """
    if t.kind == TASK_KIND_SERVICE_LOG:
        if t.status == 'done':
            s.last_log = t.stdout or ''
            s.last_log_at = datetime.utcnow()
        return

    if t.status != 'done':
        s.status = 'failed'
        s.last_error = (t.error or t.stderr or '任务失败')[:1024]
        return

    s.last_error = None
    result = {}
    try:
        result = json.loads(t.result_json) if t.result_json else {}
    except (TypeError, ValueError):
        pass

    if t.kind in (TASK_KIND_SERVICE_INSTALL, TASK_KIND_SERVICE_START):
        s.status = 'running'
        s.pid = result.get('pid') or s.pid
        s.last_started_at = datetime.utcnow()
    elif t.kind == TASK_KIND_SERVICE_STOP:
        s.status = 'stopped'
        s.pid = None
        s.last_stopped_at = datetime.utcnow()
        s.last_exit_code = result.get('exit_code') if isinstance(result.get('exit_code'), int) else None
    elif t.kind == TASK_KIND_SERVICE_DELETE:
        s.status = 'stopped'
        s.pid = None
        s.last_stopped_at = datetime.utcnow()


# ---------------------------------------------------------------------------
# 杂项
# ---------------------------------------------------------------------------

def _safe_rmtree(path: str) -> None:
    if not path or not os.path.isdir(path):
        return
    import shutil
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning('rmtree(%s) 失败: %s', path, exc)


def user_owns_device(user: User, device_id: int) -> Device | None:
    if not user:
        return None
    return Device.query.filter_by(id=device_id, user_id=user.id).first()
