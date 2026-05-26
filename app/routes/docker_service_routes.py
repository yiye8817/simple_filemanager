"""Docker 服务管理 HTTP 路由。

URL 设计（前缀 ``/api/docker-services``）：
- ``GET    /availability``              docker / compose 是否可用 + 版本
- ``GET    /``                          列表
- ``GET    /<id>``                      详情（含 docker compose ps 实时状态）
- ``POST   /``                          上传创建（multipart：name/local_port/remote_port/compose）
- ``POST   /<id>/pull``                 ``docker compose pull``（前台等待）
- ``POST   /<id>/start``                ``docker compose up -d``
- ``POST   /<id>/stop``                 ``docker compose stop``
- ``DELETE /<id>``                      ``docker compose down -v`` + 清磁盘 + 删 DB
- ``GET    /<id>/log``                  ``docker compose logs --tail`` + 命令历史

所有接口都依赖与 service_manager_routes 一致的 ``_frpc_path()`` 逻辑，
所以前端可以共享同一个 frpc_path 切换器。
"""
from __future__ import annotations

import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.utils import secure_filename

from app import db
from app.models import DockerService
from app.services import docker_service_service as dsvc
from app.services import service_manager_service as svc
from app.services import system_monitor_service as monitor
from app.utils.decorators import login_required

# 共用 frpc 路径解析（避免重复逻辑漂移）
from app.routes.service_manager_routes import (  # noqa: E402
    _default_frpc_path,
    _frpc_path,
    _read_frpc_override,
)

docker_service_bp = Blueprint('docker_service', __name__)


ALLOWED_COMPOSE_SUFFIXES = ('.yml', '.yaml', '.tar.gz', '.tgz')
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # compose 文件本身一般 < 几十 KB；tar.gz 限制 50MB


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _own_or_404(sid: int):
    user_id = session.get('user_id')
    s = DockerService.query.filter_by(id=sid, user_id=user_id).first()
    if not s:
        return None, (jsonify({'error': '服务不存在或无权访问'}), 404)
    return s, None


def _serialize(s: DockerService) -> dict:
    server_addr = None
    # 同 ManagedService 的 _serialize：先用请求 override，失败再回默认；不抛
    for path_provider in (_frpc_path, _default_frpc_path):
        try:
            cfg = svc.parse_frpc_toml(path_provider())
            server_addr = cfg.server_addr
            break
        except Exception:  # noqa: BLE001
            continue
    d = s.to_dict(server_addr=server_addr)
    d['containers'] = dsvc.deserialize_container_ids(s.container_ids)
    return d


def _refresh_status(s: DockerService) -> DockerService:
    """根据 docker compose ps 校准 DB 状态，避免容器被外部 kill 导致脏数据。"""
    if not s.compose_path or not os.path.isfile(s.compose_path):
        return s
    try:
        ps = dsvc.compose_ps(s.work_dir, s.compose_path, s.project_name)
    except Exception:  # noqa: BLE001
        return s
    if ps:
        alive = any((c.get('state') or '').lower() == 'running' for c in ps)
        s.status = 'running' if alive else 'stopped'
        s.container_ids = dsvc.serialize_container_ids([c['id'] for c in ps if c.get('id')])
    elif s.status == 'running':
        # ps 为空说明容器都没了
        s.status = 'stopped'
        s.container_ids = dsvc.serialize_container_ids([])
        s.last_stopped_at = datetime.utcnow()
    db.session.commit()
    return s


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------

@docker_service_bp.route('/api/docker-services/availability', methods=['GET'])
@login_required
def availability():
    av = dsvc.check_availability()
    return jsonify({
        'success': True,
        'ok': av.ok,
        'docker_version': av.docker_version,
        'compose_version': av.compose_version,
        'reason': av.reason,
        # 结构化诊断 — 前端横幅根据这些字段决定提示文案
        'docker_in_path': av.docker_in_path,
        'docker_path': av.docker_path,
        'daemon_ok': av.daemon_ok,
        'daemon_reason': av.daemon_reason,
        'compose_ok': av.compose_ok,
        'compose_reason': av.compose_reason,
        'install_hint': av.install_hint,
        'start_hint': av.start_hint,
    })


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------

@docker_service_bp.route('/api/docker-services', methods=['GET'])
@login_required
def list_docker_services():
    user_id = session.get('user_id')
    rows = (
        DockerService.query
        .filter_by(user_id=user_id)
        .order_by(DockerService.id.desc())
        .all()
    )
    # 列表场景不每条都跑 ps（开销不小）；只看 running 的
    for s in rows:
        if s.status == 'running':
            _refresh_status(s)
    return jsonify({'success': True, 'services': [_serialize(s) for s in rows]})


@docker_service_bp.route('/api/docker-services/<int:sid>', methods=['GET'])
@login_required
def get_docker_service(sid: int):
    s, err = _own_or_404(sid)
    if err:
        return err
    _refresh_status(s)
    payload = _serialize(s)
    # 顺手把容器列表也带上（含 image/state/status）
    try:
        payload['containers_detail'] = dsvc.compose_ps(s.work_dir, s.compose_path, s.project_name)
    except Exception as exc:  # noqa: BLE001
        payload['containers_detail_error'] = str(exc)
    return jsonify({'success': True, 'service': payload})


# ---------------------------------------------------------------------------
# 创建（上传 compose）
# ---------------------------------------------------------------------------

@docker_service_bp.route('/api/docker-services', methods=['POST'])
@login_required
def create_docker_service():
    av = dsvc.check_availability()
    if not av.ok:
        return jsonify({'error': f'Docker 不可用：{av.reason}'}), 503

    user_id = session.get('user_id')
    name = (request.form.get('name') or '').strip()
    if not name:
        return jsonify({'error': '服务名不能为空'}), 400
    try:
        local_port = int((request.form.get('local_port') or '').strip())
        remote_port = int((request.form.get('remote_port') or '').strip())
    except ValueError:
        return jsonify({'error': 'local_port / remote_port 必须为整数'}), 400

    # frpc 白名单校验
    try:
        cfg = svc.parse_frpc_toml(_frpc_path())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    proxy = svc.find_proxy(cfg, local_port, remote_port)
    if not proxy:
        return jsonify({'error': '端口组合不在 frpc.toml 配置中，已拒绝'}), 400

    if 'compose' not in request.files:
        return jsonify({'error': '请上传 compose 字段（docker-compose.yml 或 .tar.gz）'}), 400
    f = request.files['compose']
    if not f.filename:
        return jsonify({'error': '上传文件名为空'}), 400
    safe_name = secure_filename(f.filename) or 'docker-compose.yml'
    lower = safe_name.lower()
    if not (lower.endswith('.yml') or lower.endswith('.yaml')
            or lower.endswith('.tar.gz') or lower.endswith('.tgz')):
        return jsonify({'error': f'仅支持 {", ".join(ALLOWED_COMPOSE_SUFFIXES)}'}), 400

    upload_root = current_app.config['UPLOAD_FOLDER']

    # 先入库拿 id，作目录名（与 ManagedService 的策略保持一致）
    s = DockerService(
        user_id=user_id,
        name=name,
        project_name='__pending__',  # 占位，下一步用 id 重写
        upload_filename=safe_name,
        compose_path='',
        work_dir='',
        local_port=local_port,
        remote_port=remote_port,
        proxy_name=proxy.name,
        status='idle',
    )
    db.session.add(s)
    db.session.commit()

    s.project_name = dsvc.project_name_for(user_id, s.id)
    sdir = dsvc.docker_service_dir(upload_root, user_id, s.id)
    work_dir = os.path.join(sdir, 'work')
    log_path = os.path.join(sdir, 'compose.log')
    os.makedirs(work_dir, exist_ok=True)

    try:
        if lower.endswith('.yml') or lower.endswith('.yaml'):
            # 单文件直接保存
            compose_path = os.path.join(work_dir, '.yml')
            f.save(compose_path)
            size = os.path.getsize(compose_path)
            if size <= 0 or size > MAX_UPLOAD_BYTES:
                raise ValueError(f'compose 文件大小非法（{size} 字节）')
        else:
            # tar.gz：先保存归档，再解压
            archive_path = os.path.join(sdir, safe_name)
            f.save(archive_path)
            size = os.path.getsize(archive_path)
            if size <= 0 or size > MAX_UPLOAD_BYTES:
                raise ValueError(f'归档大小非法（{size} 字节）')
            svc.safe_extract_tarball(archive_path, work_dir)
            found = dsvc.find_compose_file(work_dir)
            if not found:
                raise ValueError('归档中未找到 docker-compose.y(a)ml')
            compose_path = found
            # compose 在子目录时，把 work_dir 收窄到 compose 所在目录
            work_dir = os.path.dirname(compose_path)

        ok, msg = dsvc.quick_validate_compose(compose_path)
        if not ok:
            raise ValueError(f'compose 校验失败：{msg}')
    except Exception as exc:  # noqa: BLE001
        db.session.delete(s)
        db.session.commit()
        dsvc.cleanup_service_dir(sdir)
        return jsonify({'error': f'保存/解析 compose 失败: {exc}'}), 400

    s.compose_path = compose_path
    s.work_dir = work_dir
    s.log_path = log_path
    db.session.commit()
    return jsonify({'success': True, 'service': _serialize(s)})


# ---------------------------------------------------------------------------
# pull / start / stop
# ---------------------------------------------------------------------------

def _env_for(s: DockerService) -> dict:
    """compose 文件里推荐用 ``${PORT}`` 引用本地端口；我们顺便把 REMOTE_PORT 也注进去。"""
    return {
        'PORT': str(s.local_port),
        'REMOTE_PORT': str(s.remote_port),
        'COMPOSE_PROJECT_NAME': s.project_name,
    }


@docker_service_bp.route('/api/docker-services/<int:sid>/pull', methods=['POST'])
@login_required
def pull_docker_service(sid: int):
    s, err = _own_or_404(sid)
    if err:
        return err
    s.status = 'pulling'
    s.last_error = None
    db.session.commit()
    try:
        ok, msg = dsvc.compose_pull(s.work_dir, s.compose_path, s.project_name,
                                    env_extra=_env_for(s), log_path=s.log_path)
    except Exception as exc:  # noqa: BLE001
        s.status = 'failed'
        s.last_error = f'pull 异常: {exc}'
        db.session.commit()
        return jsonify({'error': s.last_error}), 500
    if not ok:
        s.status = 'failed'
        s.last_error = f'pull 失败: {msg[:500]}'
        db.session.commit()
        return jsonify({'error': s.last_error}), 500
    s.status = 'stopped'  # 拉好镜像还没启动
    s.last_error = None
    db.session.commit()
    return jsonify({'success': True, 'service': _serialize(s)})


@docker_service_bp.route('/api/docker-services/<int:sid>/start', methods=['POST'])
@login_required
def start_docker_service(sid: int):
    s, err = _own_or_404(sid)
    if err:
        return err

    # frpc 校验 + 本地端口占用检查
    try:
        cfg = svc.parse_frpc_toml(_frpc_path())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    if not svc.find_proxy(cfg, s.local_port, s.remote_port):
        return jsonify({'error': '当前端口已不在 frpc.toml 配置中'}), 400
    if svc.is_port_in_use(s.local_port):
        # 但要排除"端口本来就被我们这套 compose 占用"的情况
        _refresh_status(s)
        if s.status != 'running':
            return jsonify({
                'error': f'本地端口 {s.local_port} 已被占用，无法启动',
                'local_port': s.local_port,
            }), 409

    try:
        ok, msg = dsvc.compose_up(s.work_dir, s.compose_path, s.project_name,
                                  env_extra=_env_for(s), log_path=s.log_path)
    except Exception as exc:  # noqa: BLE001
        s.status = 'failed'
        s.last_error = f'up 异常: {exc}'
        db.session.commit()
        return jsonify({'error': s.last_error, 'log_tail': dsvc.compose_logs(s.work_dir, s.compose_path, s.project_name, tail=50)}), 500
    if not ok:
        s.status = 'failed'
        s.last_error = f'up 失败: {msg[:500]}'
        db.session.commit()
        return jsonify({'error': s.last_error}), 500

    # 拉取 ps 写入 container_ids
    try:
        ps = dsvc.compose_ps(s.work_dir, s.compose_path, s.project_name)
        s.container_ids = dsvc.serialize_container_ids([c['id'] for c in ps if c.get('id')])
    except Exception:  # noqa: BLE001
        pass
    s.status = 'running'
    s.last_started_at = datetime.utcnow()
    s.last_error = None
    db.session.commit()
    return jsonify({'success': True, 'service': _serialize(s)})


@docker_service_bp.route('/api/docker-services/<int:sid>/stop', methods=['POST'])
@login_required
def stop_docker_service(sid: int):
    s, err = _own_or_404(sid)
    if err:
        return err
    try:
        ok, msg = dsvc.compose_stop(s.work_dir, s.compose_path, s.project_name,
                                    log_path=s.log_path)
    except Exception as exc:  # noqa: BLE001
        return jsonify({'error': f'stop 异常: {exc}'}), 500
    if not ok:
        return jsonify({'error': f'stop 失败: {msg[:500]}'}), 500
    s.status = 'stopped'
    s.last_stopped_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'service': _serialize(s)})


# ---------------------------------------------------------------------------
# 日志 / 卸载
# ---------------------------------------------------------------------------

@docker_service_bp.route('/api/docker-services/<int:sid>/log', methods=['GET'])
@login_required
def get_docker_log(sid: int):
    s, err = _own_or_404(sid)
    if err:
        return err
    try:
        tail = int(request.args.get('lines', 200))
    except ValueError:
        tail = 200
    container_log = dsvc.compose_logs(s.work_dir, s.compose_path, s.project_name, tail=tail)
    cmd_log = svc.tail_log(s.log_path or '', max_lines=80)
    _refresh_status(s)
    return jsonify({
        'success': True,
        'service_id': s.id,
        'status': s.status,
        'log': container_log,
        'cmd_log': cmd_log,
    })


@docker_service_bp.route('/api/docker-services/<int:sid>', methods=['DELETE'])
@login_required
def delete_docker_service(sid: int):
    s, err = _own_or_404(sid)
    if err:
        return err
    try:
        dsvc.uninstall(s.work_dir, s.compose_path, s.project_name, log_path=s.log_path)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning('docker uninstall 失败 svc=%s: %s', s.id, exc)
    user_id = session.get('user_id')
    upload_root = current_app.config['UPLOAD_FOLDER']
    sdir = dsvc.docker_service_dir(upload_root, user_id, s.id)
    dsvc.cleanup_service_dir(sdir)
    db.session.delete(s)
    db.session.commit()
    return jsonify({'success': True})
