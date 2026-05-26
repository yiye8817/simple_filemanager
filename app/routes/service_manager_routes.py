"""ManagedService HTTP 路由。

URL 设计（蓝图前缀 ``/api/services``）：

- ``GET    /api/services/port-options``  — 列出 frpc.toml 中可用的 (localPort, remotePort) 组合
- ``GET    /api/services``                — 列当前用户的全部服务
- ``POST   /api/services``                — 上传 tar.gz 创建一个服务（multipart）
- ``GET    /api/services/<id>``           — 查询单个服务
- ``POST   /api/services/<id>/start``     — 解压（首次或被外部清理后）+ 启动 run.sh
- ``POST   /api/services/<id>/stop``      — 停止当前进程
- ``GET    /api/services/<id>/log``       — 取最近 N 行日志
- ``DELETE /api/services/<id>``           — 停止 + 清理磁盘 + 删除 DB

所有接口都使用 ``@login_required``，并且只允许操作 ``user_id`` 与会话一致的记录。
"""
from __future__ import annotations

import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, session
from werkzeug.utils import secure_filename

from app import db
from app.models import ManagedService
from app.services import service_manager_service as svc
from app.services import system_monitor_service as monitor
from app.utils.decorators import login_required

service_bp = Blueprint('service_manager', __name__)


# ---------------------------------------------------------------------------
# 页面入口
# ---------------------------------------------------------------------------

@service_bp.route('/services')
@login_required
def services_page():
    """渲染服务管理页面（前端通过 JS 调下面一组 /api/services 接口）。"""
    return render_template('services/index.html', user_name=session.get('user_name'))

ALLOWED_ARCHIVE_SUFFIXES = ('.tar.gz', '.tgz')


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _default_frpc_path() -> str:
    """系统默认 frpc.toml 路径（不读 request）。"""
    return current_app.config.get('FRPC_TOML') or svc.DEFAULT_FRPC_PATH


def _read_frpc_override() -> str:
    """从当前请求里取用户选择的 frpc 路径（query 或 form），未填返回空串。"""
    raw = ''
    try:
        raw = (request.args.get('frpc_path') or '').strip()
        if not raw and request.method != 'GET':
            raw = (request.form.get('frpc_path') or '').strip()
    except RuntimeError:
        # 不在请求上下文里（理论上不会发生在路由内）
        return ''
    return raw


def _validate_frpc_path(p: str) -> str:
    """路径白盒校验：绝对路径 + .toml 后缀 + 必须是已存在的文件。"""
    if not os.path.isabs(p):
        raise ValueError('frpc_path 必须为绝对路径')
    if not p.lower().endswith('.toml'):
        raise ValueError('frpc_path 必须以 .toml 结尾')
    if not os.path.isfile(p):
        raise FileNotFoundError(f'frpc 配置不存在: {p}')
    return p


def _frpc_path() -> str:
    """
    解析当前请求要使用的 frpc.toml 路径。
    优先级：?frpc_path / form[frpc_path]  >  app.config['FRPC_TOML']  >  /etc/frpc.toml
    校验失败抛 ValueError / FileNotFoundError，由调用方转成 4xx。
    """
    override = _read_frpc_override()
    if override:
        return _validate_frpc_path(override)
    return _default_frpc_path()


def _own_service_or_404(service_id: int):
    user_id = session.get('user_id')
    s = ManagedService.query.filter_by(id=service_id, user_id=user_id).first()
    if not s:
        return None, (jsonify({'error': '服务不存在或无权访问'}), 404)
    return s, None


def _refresh_status(s: ManagedService) -> ManagedService:
    """根据 PID 真实状态修正 DB 字段，避免"DB 是 running，进程其实已死"的脏数据。"""
    if s.status == 'running':
        if not svc.is_pid_running(s.pid):
            s.status = 'stopped'
            s.last_stopped_at = datetime.utcnow()
            db.session.commit()
    return s


def _serialize(s: ManagedService) -> dict:
    server_addr = None
    # 优先用请求里指定路径；任何错误都回退到默认路径再试一次，最后吞掉异常 —
    # 列表渲染绝不能因 frpc.toml 不可用而整体 500
    for path_provider in (_frpc_path, _default_frpc_path):
        try:
            cfg = svc.parse_frpc_toml(path_provider())
            server_addr = cfg.server_addr
            break
        except Exception:  # noqa: BLE001
            continue
    return s.to_dict(server_addr=server_addr)


# ---------------------------------------------------------------------------
# 端口选项
# ---------------------------------------------------------------------------

@service_bp.route('/api/services/port-options', methods=['GET'])
@login_required
def list_port_options():
    """前端"选择端口"下拉来源；按 frpc.toml 当前 proxies 实时返回。

    Query Params:
        - ``frpc_path`` 可选，临时覆盖配置路径；未传 → 使用默认路径。
    """
    default_path = _default_frpc_path()
    try:
        path = _frpc_path()
        cfg = svc.parse_frpc_toml(path)
    except ValueError as exc:
        return jsonify({
            'error': str(exc), 'options': [],
            'frpc_path': None, 'default_frpc_path': default_path,
        }), 400
    except FileNotFoundError as exc:
        return jsonify({
            'error': str(exc), 'options': [],
            'frpc_path': _read_frpc_override() or default_path,
            'default_frpc_path': default_path,
        }), 404
    except OSError as exc:
        return jsonify({
            'error': f'frpc.toml 解析失败: {exc}', 'options': [],
            'frpc_path': _read_frpc_override() or default_path,
            'default_frpc_path': default_path,
        }), 500

    options = [
        {
            'name': p.name,
            'type': p.proxy_type,
            'local_port': p.local_port,
            'remote_port': p.remote_port,
            'in_use': svc.is_port_in_use(p.local_port),
        }
        for p in cfg.proxies
    ]
    return jsonify({
        'success': True,
        'server_addr': cfg.server_addr,
        'server_port': cfg.server_port,
        'options': options,
        'frpc_path': path,
        'default_frpc_path': default_path,
    })


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------

@service_bp.route('/api/services', methods=['GET'])
@login_required
def list_services():
    user_id = session.get('user_id')
    rows = (
        ManagedService.query
        .filter_by(user_id=user_id)
        .order_by(ManagedService.id.desc())
        .all()
    )
    for s in rows:
        _refresh_status(s)
    return jsonify({'success': True, 'services': [_serialize(s) for s in rows]})


@service_bp.route('/api/services/<int:service_id>', methods=['GET'])
@login_required
def get_service(service_id: int):
    s, err = _own_service_or_404(service_id)
    if err:
        return err
    _refresh_status(s)
    return jsonify({'success': True, 'service': _serialize(s)})


# ---------------------------------------------------------------------------
# 创建（上传 tar.gz）
# ---------------------------------------------------------------------------

@service_bp.route('/api/services', methods=['POST'])
@login_required
def create_service():
    """multipart/form-data 字段：
    - ``name``         展示名（必填）
    - ``local_port``   必须是 frpc.toml 中存在的 localPort
    - ``remote_port``  与 local_port 同属一个 proxy
    - ``archive``      .tar.gz / .tgz 文件
    """
    user_id = session.get('user_id')
    name = (request.form.get('name') or '').strip()
    local_port_raw = (request.form.get('local_port') or '').strip()
    remote_port_raw = (request.form.get('remote_port') or '').strip()

    if not name:
        return jsonify({'error': '服务名不能为空'}), 400
    try:
        local_port = int(local_port_raw)
        remote_port = int(remote_port_raw)
    except ValueError:
        return jsonify({'error': 'local_port / remote_port 必须为整数'}), 400

    # 校验端口必须在 frpc.toml 白名单中（避免用户随便填）
    try:
        cfg = svc.parse_frpc_toml(_frpc_path())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    proxy = svc.find_proxy(cfg, local_port, remote_port)
    if not proxy:
        return jsonify({'error': '端口组合不在 frpc.toml 配置中，已拒绝'}), 400

    if 'archive' not in request.files:
        return jsonify({'error': '请上传 archive 文件（.tar.gz）'}), 400
    f = request.files['archive']
    if not f.filename:
        return jsonify({'error': '上传文件名为空'}), 400
    safe_name = secure_filename(f.filename) or 'archive.tar.gz'
    if not safe_name.lower().endswith(ALLOWED_ARCHIVE_SUFFIXES):
        return jsonify({'error': f'仅支持 {", ".join(ALLOWED_ARCHIVE_SUFFIXES)}'}), 400

    upload_root = current_app.config['UPLOAD_FOLDER']

    # 先建 DB 记录拿到 id，再用 id 作目录名，避免不同请求并发冲突
    s = ManagedService(
        user_id=user_id,
        name=name,
        archive_filename=safe_name,
        archive_path='',  # 占位，下面写入实际路径后再 update
        extract_dir='',
        local_port=local_port,
        remote_port=remote_port,
        proxy_name=proxy.name,
        status='idle',
    )
    db.session.add(s)
    db.session.commit()

    sdir = svc.service_dir(upload_root, user_id, s.id)
    os.makedirs(sdir, exist_ok=True)
    apath = svc.archive_path(sdir)
    wdir = svc.work_dir(sdir)
    lpath = svc.log_path(sdir)

    try:
        f.save(apath)
        size = os.path.getsize(apath)
        if size <= 0:
            raise ValueError('上传文件为空')
        if size > svc.DEFAULT_MAX_ARCHIVE_BYTES:
            raise ValueError(f'文件过大（>{svc.DEFAULT_MAX_ARCHIVE_BYTES // (1024*1024)}MB）')
    except Exception as exc:  # noqa: BLE001
        # 失败回滚：DB 删除 + 磁盘清理
        db.session.delete(s)
        db.session.commit()
        svc.cleanup_service_dir(sdir)
        return jsonify({'error': f'保存上传文件失败: {exc}'}), 400

    s.archive_path = apath
    s.extract_dir = wdir
    s.log_path = lpath
    db.session.commit()

    return jsonify({'success': True, 'service': _serialize(s)})


# ---------------------------------------------------------------------------
# 启动 / 停止
# ---------------------------------------------------------------------------

@service_bp.route('/api/services/<int:service_id>/start', methods=['POST'])
@login_required
def start_service(service_id: int):
    s, err = _own_service_or_404(service_id)
    if err:
        return err
    _refresh_status(s)
    if s.status == 'running' and svc.is_pid_running(s.pid):
        return jsonify({'success': True, 'service': _serialize(s), 'message': '已在运行'}), 200

    # 1) 端口验证：必须仍然在 frpc.toml 白名单 + 没被其他程序占用
    try:
        cfg = svc.parse_frpc_toml(_frpc_path())
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    if not svc.find_proxy(cfg, s.local_port, s.remote_port):
        return jsonify({'error': '当前端口已不在 frpc.toml 配置中，请重新创建服务'}), 400
    if svc.is_port_in_use(s.local_port):
        return jsonify({
            'error': f'本地端口 {s.local_port} 已被占用，无法启动',
            'local_port': s.local_port,
        }), 409

    # 2) 解压（首次或被外部清理）
    work = s.extract_dir
    run_script_path = svc.find_run_script(work) if work else None
    if not run_script_path:
        try:
            real_root = svc.safe_extract_tarball(s.archive_path, work)
        except (ValueError, OSError, FileNotFoundError) as exc:
            s.status = 'failed'
            s.last_error = f'解压失败: {exc}'
            db.session.commit()
            return jsonify({'error': str(exc)}), 400
        run_script_path = svc.find_run_script(real_root) or svc.find_run_script(work)
        if not run_script_path:
            s.status = 'failed'
            s.last_error = '归档中未找到 run.sh'
            db.session.commit()
            return jsonify({'error': '归档中未找到 run.sh'}), 400

    # 3) 启动
    try:
        pid = svc.start_run_script(
            run_sh=run_script_path,
            local_port=s.local_port,
            log_path=s.log_path,
            cwd=os.path.dirname(run_script_path),
        )
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        s.status = 'failed'
        s.pid = None
        s.last_error = str(exc)
        db.session.commit()
        return jsonify({'error': str(exc), 'log_tail': svc.tail_log(s.log_path)}), 500

    s.pid = pid
    s.status = 'running'
    s.last_started_at = datetime.utcnow()
    s.last_error = None
    db.session.commit()
    return jsonify({'success': True, 'service': _serialize(s)})


@service_bp.route('/api/services/<int:service_id>/stop', methods=['POST'])
@login_required
def stop_service(service_id: int):
    s, err = _own_service_or_404(service_id)
    if err:
        return err
    if s.status != 'running' or not svc.is_pid_running(s.pid):
        s.status = 'stopped'
        s.pid = None
        s.last_stopped_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'service': _serialize(s), 'message': '未在运行'}), 200

    exit_code = svc.stop_pid(s.pid)
    s.status = 'stopped'
    s.pid = None
    s.last_stopped_at = datetime.utcnow()
    s.last_exit_code = exit_code
    db.session.commit()
    return jsonify({'success': True, 'service': _serialize(s)})


# ---------------------------------------------------------------------------
# 日志 / 删除
# ---------------------------------------------------------------------------

@service_bp.route('/api/services/<int:service_id>/log', methods=['GET'])
@login_required
def get_service_log(service_id: int):
    s, err = _own_service_or_404(service_id)
    if err:
        return err
    try:
        max_lines = int(request.args.get('lines', 200))
    except ValueError:
        max_lines = 200
    text = svc.tail_log(s.log_path or '', max_lines=max_lines)
    _refresh_status(s)
    return jsonify({
        'success': True,
        'service_id': s.id,
        'status': s.status,
        'pid': s.pid,
        'log': text,
    })


@service_bp.route('/api/services/<int:service_id>/update', methods=['POST'])
@login_required
def update_service_archive(service_id: int):
    """在已存在的服务上**增量同步**一个新 tar.gz。

    multipart/form-data:
        - ``archive``           必填，新归档（.tar.gz / .tgz）
        - ``replace_archive``   可选 "1"/"0"，默认 "1"；为 "1" 时同时把 ``archive_path`` 换成新归档，
                                这样下次"删除工作目录后重新启动"也是基于新内容
        - ``stop_first``        可选 "1"/"0"，默认 "0"；为 "1" 时先停止再同步、再不自动重启

    行为：
        1) 校验 + 安全解压到 staging（路径越界、链接逃逸均拒绝）
        2) 推断现有 work_root（与 ``safe_extract_tarball`` 同语义）
        3) sync_tree：复制新文件、覆盖差异、跳过未变；缺失目录自动创建；不删除
        4) （可选）``cp`` 新归档覆盖 ``archive_path``
        5) 返回增删替换摘要
    """
    import os as _os
    import shutil as _shutil
    import time as _time

    s, err = _own_service_or_404(service_id)
    if err:
        return err

    if 'archive' not in request.files:
        return jsonify({'error': '请上传 archive 文件（.tar.gz）'}), 400
    f = request.files['archive']
    if not f.filename:
        return jsonify({'error': '上传文件名为空'}), 400
    safe_name = secure_filename(f.filename) or 'archive.tar.gz'
    if not safe_name.lower().endswith(ALLOWED_ARCHIVE_SUFFIXES):
        return jsonify({'error': f'仅支持 {", ".join(ALLOWED_ARCHIVE_SUFFIXES)}'}), 400

    replace_archive = (request.form.get('replace_archive') or '1') != '0'
    stop_first = (request.form.get('stop_first') or '0') == '1'

    user_id = session.get('user_id')
    sdir = svc.service_dir(current_app.config['UPLOAD_FOLDER'], user_id, s.id)
    if not _os.path.isdir(sdir):
        return jsonify({'error': '服务目录不存在，请先删除后重新创建'}), 400

    # 可选：先停止
    was_running = False
    if stop_first and s.status == 'running' and svc.is_pid_running(s.pid):
        was_running = True
        try:
            svc.stop_pid(s.pid)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning('update: stop_pid 失败 svc=%s: %s', s.id, exc)
        s.status = 'stopped'
        s.pid = None
        s.last_stopped_at = datetime.utcnow()
        db.session.commit()

    staging_root = _os.path.join(sdir, '.staging')
    staging_tar = _os.path.join(staging_root, f'upload-{int(_time.time())}.tar.gz')
    staging_extract = _os.path.join(staging_root, 'extract')
    # 总是先清理 staging，避免上次失败残留
    if _os.path.isdir(staging_root):
        _shutil.rmtree(staging_root, ignore_errors=True)
    _os.makedirs(staging_extract, exist_ok=True)

    try:
        # 保存 → 校验大小 → 解压
        f.save(staging_tar)
        size = _os.path.getsize(staging_tar)
        if size <= 0:
            raise ValueError('上传文件为空')
        if size > svc.DEFAULT_MAX_ARCHIVE_BYTES:
            raise ValueError(f'文件过大（>{svc.DEFAULT_MAX_ARCHIVE_BYTES // (1024*1024)}MB）')

        src_root = svc.safe_extract_tarball(staging_tar, staging_extract)

        # 推断目标 work_root
        hint = _os.path.basename(src_root) if src_root != staging_extract else None
        dst_root = svc.resolve_work_root(s.extract_dir or _os.path.join(sdir, 'work'),
                                         hint_basename=hint)
        _os.makedirs(dst_root, exist_ok=True)

        summary = svc.sync_tree(src_root, dst_root)

        # 可选：替换 archive_path，让"清空 work 后重启"也基于新内容
        archive_replaced = False
        if replace_archive:
            try:
                target_archive = s.archive_path or svc.archive_path(sdir)
                _shutil.copy2(staging_tar, target_archive)
                s.archive_path = target_archive
                s.archive_filename = safe_name
                archive_replaced = True
            except OSError as exc:
                current_app.logger.warning('update: 替换 archive 失败 svc=%s: %s', s.id, exc)

        db.session.commit()
        summary.update({
            'archive_replaced': archive_replaced,
            'work_root': dst_root,
            'src_root_basename': _os.path.basename(src_root),
            'was_running_before_update': was_running,
        })
        return jsonify({
            'success': True,
            'service': _serialize(s),
            'summary': summary,
        })
    except (ValueError, OSError) as exc:
        return jsonify({'error': str(exc)}), 400
    finally:
        # 清理 staging（成败都清，避免占用空间）
        if _os.path.isdir(staging_root):
            _shutil.rmtree(staging_root, ignore_errors=True)


@service_bp.route('/api/services/<int:service_id>', methods=['DELETE'])
@login_required
def delete_service(service_id: int):
    s, err = _own_service_or_404(service_id)
    if err:
        return err
    # 先停进程；停失败也不阻塞删除
    if svc.is_pid_running(s.pid):
        try:
            svc.stop_pid(s.pid)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning('stop_pid 失败 svc=%s: %s', s.id, exc)

    user_id = session.get('user_id')
    sdir = svc.service_dir(current_app.config['UPLOAD_FOLDER'], user_id, s.id)
    svc.cleanup_service_dir(sdir)

    db.session.delete(s)
    db.session.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# 资源监控
# ---------------------------------------------------------------------------

@service_bp.route('/api/services/system-stats', methods=['GET'])
@login_required
def system_stats():
    """系统级 + 当前用户服务的进程级资源占用。

    Query Params:
        - ``include_procs`` 1/0，默认 1。为 0 时只回系统级，便于纯监控页快速刷
        - ``ids``           逗号分隔；只返回这些 service 的进程指标（缺省取当前用户全部 running）
    """
    upload_root = current_app.config.get('UPLOAD_FOLDER')
    extra_paths = [upload_root] if upload_root else []
    sys_stats = monitor.get_system_stats(extra_disk_paths=extra_paths)

    include_procs = (request.args.get('include_procs') or '1') != '0'
    procs: list[dict] = []
    if include_procs:
        user_id = session.get('user_id')
        ids_raw = (request.args.get('ids') or '').strip()
        q = ManagedService.query.filter_by(user_id=user_id)
        if ids_raw:
            try:
                wanted = [int(x) for x in ids_raw.split(',') if x.strip()]
            except ValueError:
                return jsonify({'error': 'ids 必须为整数列表'}), 400
            q = q.filter(ManagedService.id.in_(wanted))
        for s in q.all():
            _refresh_status(s)
            ps = monitor.get_proc_stats(s.pid)
            procs.append({
                'service_id': s.id,
                'name': s.name,
                'status': s.status,
                'pid': s.pid,
                'ps': ps,
            })

    return jsonify({
        'success': True,
        'system': sys_stats,
        'processes': procs,
    })
