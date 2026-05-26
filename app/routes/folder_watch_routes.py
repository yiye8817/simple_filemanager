"""文件夹 Webhook 监听管理路由（蓝图前缀 ``/api``）。

URL 设计：

- ``GET    /api/folder-watches``                    当前用户的全部 watch
- ``POST   /api/folder-watches``                    新建：``folder_id`` / ``url`` / 可选 ``secret`` / ``events`` / ``include_subdirs`` / ``enabled``
- ``GET    /api/folder-watches/<id>``               查询单个
- ``PUT    /api/folder-watches/<id>``               更新（仅修改提供的字段）
- ``DELETE /api/folder-watches/<id>``               删除
- ``POST   /api/folder-watches/<id>/test``          手动触发一次测试 payload，验证对端可达
- ``GET    /api/folders/<folder_id>/watches``       某个文件夹下当前用户已配置的 watch（便于"目录详情页"展示）
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request, session

from app import db
from app.models import File, FolderWatch
from app.models.folder_watch import VALID_EVENTS
from app.services import webhook_service
from app.utils.decorators import login_required

folder_watch_bp = Blueprint('folder_watch', __name__)
logger = logging.getLogger(__name__)


def _own_watch_or_404(watch_id: int):
    user_id = session.get('user_id')
    w = FolderWatch.query.filter_by(id=watch_id, user_id=user_id).first()
    if not w:
        return None, (jsonify({'error': 'watch 不存在或无权访问'}), 404)
    return w, None


def _own_folder_or_404(folder_id: int):
    user_id = session.get('user_id')
    f = File.query.filter_by(id=folder_id, user_id=user_id, is_directory=True).first()
    if not f:
        return None, (jsonify({'error': '文件夹不存在或无权访问'}), 404)
    return f, None


def _parse_events(raw) -> tuple[str, str | None]:
    """把 events 字段（list 或 csv 字符串）规范化成 csv 存库。返回 (csv, error)。"""
    if raw is None or raw == '':
        return '', None
    if isinstance(raw, str):
        items = [x.strip() for x in raw.split(',') if x.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw if str(x).strip()]
    else:
        return '', 'events 字段格式不支持（应为字符串或数组）'
    bad = [x for x in items if x not in VALID_EVENTS]
    if bad:
        return '', f'events 含非法值: {bad}，合法值: {list(VALID_EVENTS)}'
    return ','.join(items), None


def _validate_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return 'url 必填'
    p = urlparse(url)
    if p.scheme not in ('http', 'https'):
        return 'url 必须是 http/https'
    if not p.netloc:
        return 'url 无效'
    return None


def _resolve_folder(data) -> tuple[File | None, tuple | None]:
    """支持 ``folder_id``（推荐）或 ``folder_path``（相对路径，从用户根目录起）。"""
    user_id = session.get('user_id')
    folder_id = data.get('folder_id')
    if folder_id:
        try:
            fid = int(folder_id)
        except (TypeError, ValueError):
            return None, (jsonify({'error': 'folder_id 必须是整数'}), 400)
        f = File.query.filter_by(id=fid, user_id=user_id, is_directory=True).first()
        if not f:
            return None, (jsonify({'error': '文件夹不存在或无权访问'}), 404)
        return f, None

    folder_path = data.get('folder_path')
    if folder_path:
        # 延迟引用，避免循环
        from app.routes.file_routes import get_directory_by_path
        p = (folder_path or '').strip()
        if p.startswith('/'):
            p = p[1:]
        f = get_directory_by_path(p, user_id)
        if not f or not f.is_directory or f.user_id != user_id:
            return None, (jsonify({'error': f'文件夹路径不存在: {folder_path}'}), 404)
        return f, None

    return None, (jsonify({'error': '必须提供 folder_id 或 folder_path'}), 400)


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------

@folder_watch_bp.route('/api/folder-watches', methods=['GET'])
@login_required
def list_watches():
    user_id = session.get('user_id')
    rows = (
        FolderWatch.query
        .filter_by(user_id=user_id)
        .order_by(FolderWatch.id.desc())
        .all()
    )
    # 一次性把文件夹查出来，避免 N+1
    folder_ids = {r.folder_id for r in rows}
    folder_map = {
        f.id: f for f in File.query.filter(File.id.in_(folder_ids)).all()
    } if folder_ids else {}
    return jsonify({
        'success': True,
        'watches': [r.to_dict(folder=folder_map.get(r.folder_id)) for r in rows],
    })


@folder_watch_bp.route('/api/folder-watches/<int:watch_id>', methods=['GET'])
@login_required
def get_watch(watch_id: int):
    w, err = _own_watch_or_404(watch_id)
    if err:
        return err
    folder = File.query.get(w.folder_id)
    return jsonify({'success': True, 'watch': w.to_dict(folder=folder)})


@folder_watch_bp.route('/api/folders/<int:folder_id>/watches', methods=['GET'])
@login_required
def list_watches_by_folder(folder_id: int):
    folder, err = _own_folder_or_404(folder_id)
    if err:
        return err
    rows = (
        FolderWatch.query
        .filter_by(user_id=session.get('user_id'), folder_id=folder.id)
        .order_by(FolderWatch.id.desc())
        .all()
    )
    return jsonify({
        'success': True,
        'folder': {'id': folder.id, 'name': folder.name, 'path': folder.path},
        'watches': [r.to_dict(folder=folder) for r in rows],
    })


# ---------------------------------------------------------------------------
# 创建 / 更新 / 删除
# ---------------------------------------------------------------------------

@folder_watch_bp.route('/api/folder-watches', methods=['POST'])
@login_required
def create_watch():
    user_id = session.get('user_id')
    data = request.get_json(silent=True) or {}

    folder, err = _resolve_folder(data)
    if err:
        return err

    url = (data.get('url') or '').strip()
    url_err = _validate_url(url)
    if url_err:
        return jsonify({'error': url_err}), 400

    events_csv, ev_err = _parse_events(data.get('events'))
    if ev_err:
        return jsonify({'error': ev_err}), 400

    include_subdirs = data.get('include_subdirs', True)
    if isinstance(include_subdirs, str):
        include_subdirs = include_subdirs.lower() in ('1', 'true', 'yes', 'on')
    enabled = data.get('enabled', True)
    if isinstance(enabled, str):
        enabled = enabled.lower() in ('1', 'true', 'yes', 'on')

    secret = (data.get('secret') or '').strip() or None

    w = FolderWatch(
        user_id=user_id,
        folder_id=folder.id,
        url=url,
        secret=secret,
        events=events_csv,
        include_subdirs=bool(include_subdirs),
        enabled=bool(enabled),
    )
    db.session.add(w)
    db.session.commit()
    return jsonify({'success': True, 'watch': w.to_dict(folder=folder)}), 201


@folder_watch_bp.route('/api/folder-watches/<int:watch_id>', methods=['PUT', 'PATCH'])
@login_required
def update_watch(watch_id: int):
    w, err = _own_watch_or_404(watch_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}

    if 'folder_id' in data or 'folder_path' in data:
        folder, ferr = _resolve_folder(data)
        if ferr:
            return ferr
        w.folder_id = folder.id

    if 'url' in data:
        url = (data.get('url') or '').strip()
        url_err = _validate_url(url)
        if url_err:
            return jsonify({'error': url_err}), 400
        w.url = url

    if 'secret' in data:
        sec = data.get('secret')
        w.secret = (sec.strip() or None) if isinstance(sec, str) else None

    if 'events' in data:
        events_csv, ev_err = _parse_events(data.get('events'))
        if ev_err:
            return jsonify({'error': ev_err}), 400
        w.events = events_csv

    if 'include_subdirs' in data:
        v = data.get('include_subdirs')
        if isinstance(v, str):
            v = v.lower() in ('1', 'true', 'yes', 'on')
        w.include_subdirs = bool(v)

    if 'enabled' in data:
        v = data.get('enabled')
        if isinstance(v, str):
            v = v.lower() in ('1', 'true', 'yes', 'on')
        w.enabled = bool(v)

    db.session.commit()
    folder = File.query.get(w.folder_id)
    return jsonify({'success': True, 'watch': w.to_dict(folder=folder)})


@folder_watch_bp.route('/api/folder-watches/<int:watch_id>', methods=['DELETE'])
@login_required
def delete_watch(watch_id: int):
    w, err = _own_watch_or_404(watch_id)
    if err:
        return err
    db.session.delete(w)
    db.session.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# 手动测试
# ---------------------------------------------------------------------------

@folder_watch_bp.route('/api/folder-watches/<int:watch_id>/test', methods=['POST'])
@login_required
def test_watch(watch_id: int):
    """手动触发一次回调，向配置的 URL 发送一条 ``event=test`` 的 payload。

    与真实事件不同的是：``event=test`` 不经过祖先匹配，直接 POST。响应内容仅
    告诉调用方"已调度"；具体 HTTP 响应可在 ``GET /api/folder-watches/<id>``
    的 ``last_status_code`` / ``last_error`` 看到。
    """
    from flask import current_app

    w, err = _own_watch_or_404(watch_id)
    if err:
        return err

    folder = File.query.get(w.folder_id)
    base_url = (
        (request.host_url or '').rstrip('/')
        or (current_app.config.get('PUBLIC_BASE_URL') or '').rstrip('/')
    )
    payload = {
        'event': 'test',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'watch': {
            'id': w.id,
            'folder_id': w.folder_id,
            'folder_name': folder.name if folder else None,
            'folder_path': folder.path if folder else None,
            'include_subdirs': w.include_subdirs,
        },
        'file': None,
        'message': 'manual test from file_manager',
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    sig = webhook_service._sign_body(body, w.secret)  # noqa: SLF001 — 故意复用私有工具

    app_obj = current_app._get_current_object()  # type: ignore[attr-defined]
    t = threading.Thread(
        target=webhook_service._send_once,  # noqa: SLF001
        args=(app_obj, w.id, w.url, body, sig),
        daemon=True,
    )
    t.start()
    return jsonify({
        'success': True,
        'message': '已调度测试请求，请稍后查看 last_status_code / last_error',
        'url': w.url,
    })
