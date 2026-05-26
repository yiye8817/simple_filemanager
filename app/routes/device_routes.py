"""设备管理 HTTP 路由。

两套接口共存:

1. **用户侧** (``/api/devices/*``, session 鉴权): Web 前端调用,
   做 CRUD + 任务下发 + 服务上传。
2. **设备侧** (``/api/agent/*``, ``X-Device-Token`` 鉴权): 远程设备上的 agent 调用,
   做注册、心跳、长轮询任务、上报结果、下载服务归档。

页面入口: ``GET /devices`` 渲染 ``templates/devices/index.html``。
"""
from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, current_app, jsonify, render_template, request,
    send_file, session,
)
from werkzeug.utils import secure_filename

from app import db
from app.models import Device, DeviceManagedService, DeviceTask
from app.services import device_service as svc
from app.utils.decorators import login_required

device_bp = Blueprint('device', __name__)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@device_bp.route('/devices')
@login_required
def devices_page():
    return render_template('devices/index.html', user_name=session.get('user_name'))


# ---------------------------------------------------------------------------
# 用户侧: 设备 CRUD
# ---------------------------------------------------------------------------

def _own_device_or_404(device_id: int):
    user_id = session.get('user_id')
    d = svc.own_device(user_id, device_id)
    if not d:
        return None, (jsonify({'error': '设备不存在或无权访问'}), 404)
    return d, None


@device_bp.route('/api/devices', methods=['GET'])
@login_required
def list_devices():
    user_id = session.get('user_id')
    query = (request.args.get('q') or '').strip()
    online_only = (request.args.get('online') or '0') == '1'
    devices = svc.list_user_devices(user_id, query=query, online_only=online_only)
    return jsonify({
        'success': True,
        'devices': [d.to_dict() for d in devices],
        'total': len(devices),
    })


@device_bp.route('/api/devices', methods=['POST'])
@login_required
def create_device():
    user_id = session.get('user_id')
    data = request.get_json(silent=True) or request.form
    try:
        d = svc.create_device(
            user_id=user_id,
            name=data.get('name') or '',
            description=data.get('description') or '',
            location=data.get('location') or '',
            tags=data.get('tags') or '',
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    # 返回 token 供前端首次展示, 之后只能 rotate
    return jsonify({'success': True, 'device': d.to_dict(include_token=True)})


@device_bp.route('/api/devices/<int:device_id>', methods=['GET'])
@login_required
def get_device(device_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    return jsonify({'success': True, 'device': d.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/tunnels', methods=['GET'])
@login_required
def list_device_tunnels(device_id: int):
    """读 agent 心跳里上报的 ``stats.frpc_tunnels``, 加工成前端可直接渲染的结构。

    frpc 进程跑在**被管设备本机**上, 服务器只是透传; agent 在 ``collect_stats`` 时扫
    自己机器的 ``frpc`` 进程 + 解析 ``-c <toml>``, 结果通过心跳带回。

    Query:
        ``user`` 可选, 默认 ``root``; 用来拼 ``ssh -p <remotePort> <user>@<serverAddr>``。

    返回:
        ``{instances: [{pid, config_path, server_addr, server_port, error, proxies:[
            {name, type, local_port, remote_port, remote_host, is_ssh, ssh_command,
             external_url}
        ]}], stats_ts: <agent 采集时间>, last_seen_at: ...}``
    """
    import json as _json
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    ssh_user = (request.args.get('user') or 'root').strip()[:64] or 'root'

    stats: dict = {}
    if d.stats_json:
        try:
            stats = _json.loads(d.stats_json) or {}
        except (TypeError, ValueError):
            stats = {}

    raw_instances = stats.get('frpc_tunnels') or []
    if not isinstance(raw_instances, list):
        raw_instances = []

    out_instances = []
    for inst in raw_instances:
        if not isinstance(inst, dict):
            continue
        server_addr = inst.get('server_addr')
        server_port = inst.get('server_port')
        proxies_in = inst.get('proxies') or []
        proxies_out = []
        for p in proxies_in:
            if not isinstance(p, dict):
                continue
            try:
                lp = int(p.get('local_port'))
                rp = int(p.get('remote_port'))
            except (TypeError, ValueError):
                continue
            is_ssh = (lp == 22)
            ssh_cmd = None
            if is_ssh and server_addr and rp:
                ssh_cmd = f'ssh -p {rp} {ssh_user}@{server_addr}'
            ext_url = None
            if not is_ssh and server_addr and rp:
                ptype = (p.get('type') or '').lower()
                if ptype in ('http', 'https'):
                    ext_url = f'{ptype}://{server_addr}:{rp}'
                elif ptype in ('tcp', ''):
                    ext_url = f'tcp://{server_addr}:{rp}'
            proxies_out.append({
                'name': p.get('name'),
                'type': p.get('type'),
                'local_port': lp,
                'remote_port': rp,
                'remote_host': server_addr,
                'is_ssh': is_ssh,
                'ssh_command': ssh_cmd,
                'external_url': ext_url,
            })
        out_instances.append({
            'pid': inst.get('pid'),
            'cmdline': inst.get('cmdline'),
            'cwd': inst.get('cwd'),
            'config_path': inst.get('config_path'),
            'used_fallback_config': bool(inst.get('used_fallback_config')),
            'server_addr': server_addr,
            'server_port': server_port,
            'error': inst.get('error'),
            'proxies': proxies_out,
        })

    return jsonify({
        'success': True,
        'device_id': d.id,
        'device_name': d.name,
        'instances': out_instances,
        'frpc_error': stats.get('frpc_error'),
        'stats_ts': stats.get('ts'),
        'last_seen_at': d.last_seen_at.isoformat() if d.last_seen_at else None,
        'online': d.online,
    })


@device_bp.route('/api/devices/<int:device_id>', methods=['PATCH', 'PUT'])
@login_required
def update_device(device_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    data = request.get_json(silent=True) or request.form
    if 'name' in data:
        try:
            new_name = svc.validate_device_name(data.get('name') or '')
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        if new_name != d.name and Device.query.filter_by(
                user_id=d.user_id, name=new_name).first():
            return jsonify({'error': f'已存在同名设备: {new_name}'}), 400
        d.name = new_name
    for attr, max_len in [('description', 512), ('location', 255), ('tags', 255)]:
        if attr in data:
            v = (data.get(attr) or '').strip()
            setattr(d, attr, v[:max_len])
    db.session.commit()
    return jsonify({'success': True, 'device': d.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/rotate-token', methods=['POST'])
@login_required
def rotate_token(device_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    new_token = svc.rotate_token(d)
    return jsonify({
        'success': True,
        'device': d.to_dict(include_token=True),
        'token': new_token,
    })


@device_bp.route('/api/devices/<int:device_id>', methods=['DELETE'])
@login_required
def delete_device(device_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    upload_root = current_app.config.get('UPLOAD_FOLDER')
    svc.delete_device(d, upload_root=upload_root)
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# 用户侧: 命令下发 + 任务
# ---------------------------------------------------------------------------

@device_bp.route('/api/devices/<int:device_id>/commands', methods=['POST'])
@login_required
def post_command(device_id: int):
    """下发一条 shell 命令到设备 (异步, agent poll 后才执行)。

    body: ``{ "command": "ls /tmp", "timeout": 30, "cwd": "/home/foo" }``
    """
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    cmd = (data.get('command') or '').strip()
    if not cmd:
        return jsonify({'error': 'command 不能为空'}), 400
    if len(cmd) > 8000:
        return jsonify({'error': '命令过长 (>8000)'}), 400
    try:
        timeout = int(data.get('timeout') or 30)
    except (TypeError, ValueError):
        timeout = 30
    timeout = max(1, min(timeout, 600))
    payload = {
        'command': cmd,
        'timeout': timeout,
        'cwd': (data.get('cwd') or '').strip()[:512] or None,
        'shell': bool(data.get('shell', True)),
    }
    t = svc.enqueue_task(d, user_id=session.get('user_id'),
                         kind=svc.TASK_KIND_EXEC, payload=payload)
    return jsonify({'success': True, 'task': t.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/tasks', methods=['GET'])
@login_required
def list_tasks(device_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    try:
        limit = int(request.args.get('limit') or 50)
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))
    kind = (request.args.get('kind') or '').strip() or None
    service_id = request.args.get('service_id')
    try:
        sid_int = int(service_id) if service_id else None
    except ValueError:
        sid_int = None
    tasks = svc.list_recent_tasks(d, limit=limit, kind=kind, service_id=sid_int)
    return jsonify({'success': True, 'tasks': [t.to_dict() for t in tasks]})


@device_bp.route('/api/devices/<int:device_id>/tasks/<int:task_id>', methods=['GET'])
@login_required
def get_task(device_id: int, task_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    t = DeviceTask.query.filter_by(id=task_id, device_id=d.id).first()
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify({'success': True, 'task': t.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/tasks/<int:task_id>/cancel', methods=['POST'])
@login_required
def cancel_task(device_id: int, task_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    t = DeviceTask.query.filter_by(id=task_id, device_id=d.id).first()
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    if t.status not in ('pending', 'running'):
        return jsonify({'error': f'任务已是 {t.status} 状态'}), 400
    t.status = 'cancelled'
    t.finished_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'task': t.to_dict()})


# ---------------------------------------------------------------------------
# 用户侧: 设备上的服务
# ---------------------------------------------------------------------------

@device_bp.route('/api/devices/<int:device_id>/services', methods=['GET'])
@login_required
def list_services(device_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    services = svc.list_device_services(d)
    return jsonify({
        'success': True,
        'services': [s.to_dict() for s in services],
    })


@device_bp.route('/api/devices/<int:device_id>/services', methods=['POST'])
@login_required
def upload_service(device_id: int):
    """上传一个 tar.gz 创建设备服务。multipart:
       name / local_port / run_args / env_json / archive(.tar.gz)
       auto_install: 1 表示同时下发 install 任务
    """
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    name = (request.form.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name 不能为空'}), 400
    local_port_raw = (request.form.get('local_port') or '').strip()
    try:
        local_port = int(local_port_raw) if local_port_raw else None
    except ValueError:
        return jsonify({'error': 'local_port 必须为整数'}), 400
    run_args = (request.form.get('run_args') or '').strip()

    env: dict = {}
    env_raw = (request.form.get('env_json') or '').strip()
    if env_raw:
        try:
            env = json.loads(env_raw)
            if not isinstance(env, dict):
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({'error': 'env_json 必须是合法的 JSON 对象'}), 400

    if 'archive' not in request.files:
        return jsonify({'error': '请上传 archive 文件 (.tar.gz)'}), 400
    f = request.files['archive']
    if not f.filename:
        return jsonify({'error': '上传文件名为空'}), 400
    safe_name = secure_filename(f.filename) or 'archive.tar.gz'

    upload_root = current_app.config['UPLOAD_FOLDER']
    try:
        s = svc.create_device_service(
            device=d,
            user_id=session.get('user_id'),
            name=name,
            archive_file_storage=f,
            archive_filename=safe_name,
            local_port=local_port,
            run_args=run_args,
            env=env,
            upload_root=upload_root,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    task = None
    if (request.form.get('auto_install') or '0') == '1':
        task = svc.enqueue_task(d, user_id=session.get('user_id'),
                                kind=svc.TASK_KIND_SERVICE_INSTALL,
                                payload=_install_payload(s), service=s)
    return jsonify({
        'success': True,
        'service': s.to_dict(),
        'task': task.to_dict() if task else None,
    })


def _install_payload(s: DeviceManagedService) -> dict:
    """构造一个标准化的 install/start payload, agent 端按此解析。"""
    env = {}
    if s.env_json:
        try:
            env = json.loads(s.env_json) or {}
        except (TypeError, ValueError):
            env = {}
    return {
        'service_id': s.id,
        'name': s.name,
        'archive_filename': s.archive_filename,
        'archive_sha256': s.archive_sha256,
        'archive_size': s.archive_size,
        'local_port': s.local_port,
        'run_args': s.run_args or '',
        'env': env,
    }


@device_bp.route('/api/devices/<int:device_id>/services/<int:service_id>', methods=['PATCH', 'PUT'])
@login_required
def update_service(device_id: int, service_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    s = DeviceManagedService.query.filter_by(id=service_id, device_id=d.id).first()
    if not s:
        return jsonify({'error': '服务不存在'}), 404
    data = request.get_json(silent=True) or {}
    local_port = data.get('local_port')
    try:
        lp = int(local_port) if local_port not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'local_port 必须为整数'}), 400
    env = data.get('env')
    if env is not None and not isinstance(env, dict):
        return jsonify({'error': 'env 必须是 JSON 对象'}), 400
    svc.update_service_meta(s, local_port=lp,
                            run_args=data.get('run_args'),
                            env=env)
    return jsonify({'success': True, 'service': s.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/services/<int:service_id>/start', methods=['POST'])
@login_required
def start_service(device_id: int, service_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    s = DeviceManagedService.query.filter_by(id=service_id, device_id=d.id).first()
    if not s:
        return jsonify({'error': '服务不存在'}), 404
    # 第一次启动或归档变了 → install; 否则 start (复用已解压目录)
    kind = svc.TASK_KIND_SERVICE_INSTALL if s.status == 'idle' else svc.TASK_KIND_SERVICE_START
    t = svc.enqueue_task(d, user_id=session.get('user_id'), kind=kind,
                         payload=_install_payload(s), service=s)
    s.status = 'installing' if kind == svc.TASK_KIND_SERVICE_INSTALL else s.status
    db.session.commit()
    return jsonify({'success': True, 'service': s.to_dict(), 'task': t.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/services/<int:service_id>/stop', methods=['POST'])
@login_required
def stop_service(device_id: int, service_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    s = DeviceManagedService.query.filter_by(id=service_id, device_id=d.id).first()
    if not s:
        return jsonify({'error': '服务不存在'}), 404
    t = svc.enqueue_task(d, user_id=session.get('user_id'),
                         kind=svc.TASK_KIND_SERVICE_STOP,
                         payload={'service_id': s.id, 'name': s.name},
                         service=s)
    return jsonify({'success': True, 'task': t.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/services/<int:service_id>/log', methods=['POST'])
@login_required
def fetch_service_log(device_id: int, service_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    s = DeviceManagedService.query.filter_by(id=service_id, device_id=d.id).first()
    if not s:
        return jsonify({'error': '服务不存在'}), 404
    lines = 200
    try:
        lines = int(request.args.get('lines') or 200)
    except ValueError:
        pass
    lines = max(10, min(lines, 5000))
    t = svc.enqueue_task(d, user_id=session.get('user_id'),
                         kind=svc.TASK_KIND_SERVICE_LOG,
                         payload={'service_id': s.id, 'name': s.name, 'lines': lines},
                         service=s)
    return jsonify({'success': True, 'task': t.to_dict()})


@device_bp.route('/api/devices/<int:device_id>/services/<int:service_id>', methods=['DELETE'])
@login_required
def delete_service(device_id: int, service_id: int):
    d, err = _own_device_or_404(device_id)
    if err:
        return err
    s = DeviceManagedService.query.filter_by(id=service_id, device_id=d.id).first()
    if not s:
        return jsonify({'error': '服务不存在'}), 404
    keep_db = (request.args.get('keep_db') or '0') == '1'
    # 先下发一个 delete 任务让 agent 清理本地; 哪怕设备离线, 后续 DB 也能立刻清掉
    svc.enqueue_task(d, user_id=session.get('user_id'),
                     kind=svc.TASK_KIND_SERVICE_DELETE,
                     payload={'service_id': s.id, 'name': s.name},
                     service=s)
    if keep_db:
        return jsonify({'success': True, 'message': '已下发卸载任务, DB 记录保留'})
    upload_root = current_app.config['UPLOAD_FOLDER']
    svc.delete_device_service(s, upload_root=upload_root)
    return jsonify({'success': True})


# ===========================================================================
# Agent (设备客户端) 侧 API: 使用 X-Device-Token 鉴权
# ===========================================================================

def device_token_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = (request.headers.get('X-Device-Token') or '').strip()
        if not token:
            return jsonify({'error': '缺少 X-Device-Token'}), 401
        d = svc.get_device_by_token(token)
        if not d:
            return jsonify({'error': 'token 无效'}), 401
        request.device = d  # type: ignore[attr-defined]
        return f(*args, **kwargs)
    return wrapper


def _ip_from_request() -> str | None:
    fwd = request.headers.get('X-Forwarded-For') or ''
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr


@device_bp.route('/api/agent/enroll', methods=['POST'])
def agent_enroll():
    """**自助注册**: agent 自己拿 ``device_uid`` 来登记, 服务器返回 ``device_token``。

    安全策略 (任选其一, 通过 ``app.config`` 配置):

    - ``DEVICE_AGENT_OPEN_ENROLL = True`` (默认): 任何人都可以 enroll。
       适合内网 / 单机部署, 部署门槛最低。
    - ``DEVICE_AGENT_ENROLL_TOKEN = '...'``: agent 必须带 ``X-Enroll-Token`` 才能注册,
       推荐用于公网部署。

    request body 字段 (都是 optional 但建议给):

    - ``device_uid`` (必填, 8-64 字符): agent 端持久化的设备唯一 ID
    - ``hostname`` / ``os_name`` / ``os_version`` / ``arch`` / ``agent_version``
    - ``suggested_name``: 用户希望的设备名 (重名会自动加后缀)
    - ``stats`` / ``tunnel_info`` / ``local_ip`` / ``public_ip``: 跟心跳一致, 一并 apply

    response: ``{ device: {... with device_token}, created: bool }``
    """
    cfg = current_app.config
    enroll_token = (cfg.get('DEVICE_AGENT_ENROLL_TOKEN') or '').strip()
    if enroll_token:
        provided = (request.headers.get('X-Enroll-Token') or '').strip()
        if provided != enroll_token:
            return jsonify({'error': 'enroll token 无效'}), 401
    elif not cfg.get('DEVICE_AGENT_OPEN_ENROLL', True):
        return jsonify({'error': '服务器未开启自助注册'}), 403

    payload = request.get_json(silent=True) or {}
    device_uid = (payload.get('device_uid') or '').strip().lower()
    if not device_uid:
        return jsonify({'error': '缺少 device_uid'}), 400

    owner_id = svc.resolve_default_owner_user_id(cfg)
    if not owner_id:
        return jsonify({'error': '服务器尚未配置用户, 无法 enroll'}), 503

    try:
        d, created = svc.enroll_device(
            device_uid=device_uid,
            owner_user_id=owner_id,
            suggested_name=payload.get('hostname') or payload.get('suggested_name') or '',
            location=payload.get('location') or '',
            tags=payload.get('tags') or '',
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    # 顺手把 enroll 时带上的硬件信息 / stats 也写进去, 这样 Web UI 立刻能看到
    svc.apply_heartbeat(d, payload=payload, request_ip=_ip_from_request())

    return jsonify({
        'success': True,
        'created': created,
        'device': d.to_dict(include_token=True),
        'server_time': datetime.utcnow().isoformat(),
    })


@device_bp.route('/api/agent/register', methods=['POST'])
@device_token_required
def agent_register():
    """客户端首次启动调用; 等价于一次完整心跳。"""
    d: Device = request.device  # type: ignore[attr-defined]
    payload = request.get_json(silent=True) or {}
    svc.apply_heartbeat(d, payload=payload, request_ip=_ip_from_request())
    return jsonify({
        'success': True,
        'device': d.to_dict(),
        'server_time': datetime.utcnow().isoformat(),
    })


@device_bp.route('/api/agent/heartbeat', methods=['POST'])
@device_token_required
def agent_heartbeat():
    d: Device = request.device  # type: ignore[attr-defined]
    payload = request.get_json(silent=True) or {}
    svc.apply_heartbeat(d, payload=payload, request_ip=_ip_from_request())
    pending = DeviceTask.query.filter_by(device_id=d.id, status='pending').count()
    return jsonify({
        'success': True,
        'pending_tasks': pending,
        'server_time': datetime.utcnow().isoformat(),
    })


@device_bp.route('/api/agent/poll', methods=['GET'])
@device_token_required
def agent_poll():
    """长轮询拿任务。客户端可以在没拿到任务时阻塞 ``wait`` 秒, 服务器每 1s
    扫一次, 一旦有 pending 立即下发。
    """
    d: Device = request.device  # type: ignore[attr-defined]
    # 更新 last_seen 顺便记 IP
    d.last_seen_at = datetime.utcnow()
    if (ip := _ip_from_request()):
        d.last_ip = ip[:64]
    db.session.commit()

    try:
        wait = int(request.args.get('wait') or 0)
    except ValueError:
        wait = 0
    wait = max(0, min(wait, 25))  # 最大 25s 防 nginx/proxy 60s 超时

    try:
        limit = int(request.args.get('limit') or 4)
    except ValueError:
        limit = 4
    limit = max(1, min(limit, 16))

    tasks = svc.claim_next_tasks(d, limit=limit)
    deadline = time.time() + wait
    while not tasks and time.time() < deadline:
        time.sleep(1.0)
        tasks = svc.claim_next_tasks(d, limit=limit)

    return jsonify({
        'success': True,
        'tasks': [
            {
                **t.to_dict(),
                'claim_token': t.claim_token,
            } for t in tasks
        ],
        'server_time': datetime.utcnow().isoformat(),
    })


@device_bp.route('/api/agent/tasks/<int:task_id>/result', methods=['POST'])
@device_token_required
def agent_finalize(task_id: int):
    d: Device = request.device  # type: ignore[attr-defined]
    t = DeviceTask.query.filter_by(id=task_id, device_id=d.id).first()
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    if t.status in ('done', 'error', 'timeout', 'cancelled'):
        return jsonify({'error': f'任务已是 {t.status} 状态'}), 409

    data = request.get_json(silent=True) or {}
    status = (data.get('status') or 'done').strip()
    try:
        svc.finalize_task(
            t,
            status=status,
            exit_code=data.get('exit_code'),
            stdout=(data.get('stdout') or ''),
            stderr=(data.get('stderr') or ''),
            error=data.get('error'),
            result=data.get('result') if isinstance(data.get('result'), dict) else None,
            claim_token=data.get('claim_token'),
        )
    except (ValueError, PermissionError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'success': True, 'task': t.to_dict()})


@device_bp.route('/api/agent/services/<int:service_id>/archive', methods=['GET'])
@device_token_required
def agent_download_archive(service_id: int):
    """agent 拿到 install 任务后调用此接口下载 tar.gz。"""
    d: Device = request.device  # type: ignore[attr-defined]
    s = DeviceManagedService.query.filter_by(id=service_id, device_id=d.id).first()
    if not s:
        return jsonify({'error': '服务不存在'}), 404
    if not s.archive_path or not os.path.isfile(s.archive_path):
        return jsonify({'error': '归档文件丢失'}), 410
    return send_file(
        s.archive_path,
        mimetype='application/gzip',
        as_attachment=True,
        download_name=s.archive_filename,
    )


@device_bp.route('/api/agent/services/<int:service_id>/status', methods=['POST'])
@device_token_required
def agent_report_status(service_id: int):
    """agent 主动上报服务状态变化 (除任务结果外的带外通道, 比如 watchdog 检测到进程退出)。"""
    d: Device = request.device  # type: ignore[attr-defined]
    s = DeviceManagedService.query.filter_by(id=service_id, device_id=d.id).first()
    if not s:
        return jsonify({'error': '服务不存在'}), 404
    data = request.get_json(silent=True) or {}
    new_status = (data.get('status') or '').strip()
    if new_status in ('running', 'stopped', 'failed', 'idle'):
        s.status = new_status
        if new_status == 'running':
            s.last_started_at = datetime.utcnow()
        elif new_status == 'stopped':
            s.last_stopped_at = datetime.utcnow()
            s.pid = None
        elif new_status == 'failed':
            s.last_error = (data.get('error') or s.last_error or '')[:1024]
            s.pid = None
    if 'pid' in data and isinstance(data['pid'], int):
        s.pid = data['pid']
    if 'last_error' in data and isinstance(data['last_error'], str):
        s.last_error = data['last_error'][:1024]
    if 'log_tail' in data and isinstance(data['log_tail'], str):
        s.last_log = data['log_tail'][:200_000]
        s.last_log_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'service': s.to_dict()})
