"""文件夹插件 CRUD + invoke 路由（蓝图前缀 ``/api``）。

接口设计（与 ``folder_watch_routes`` 风格对齐）：

- ``GET    /api/folders/<folder_id>/plugins``                列文件夹下的插件
- ``POST   /api/folders/<folder_id>/plugins``                新建插件
- ``GET    /api/folder-plugins/<id>``                        详情（含 config/code）
- ``PUT    /api/folder-plugins/<id>``                        更新（仅修改提供的字段）
- ``DELETE /api/folder-plugins/<id>``                        删除
- ``POST   /api/folder-plugins/<id>/invoke``                 登录态调用（自测）
- ``POST   /api/external/folder-plugins/<id>/invoke``        外部客户端调用（API Key）
- ``POST   /api/external/folders/<folder_id>/plugins/<name>/invoke``
       按 (folder, name) 寻址的外部调用入口（更易记），同样 API Key 鉴权
- ``POST   /api/public/folder-plugins/<id>/invoke``          **无授权**, 插件需 ``public=True``
- ``POST   /api/public/folders/<folder_id>/plugins/<name>/invoke``  同样无授权
   公开通道下, 响应里的 ``url`` 自带 HMAC 签名, 客户端直接 GET 就能下载。

调用 body 统一格式::

    {"action": "lookup", "params": {"word": "hello"}}

响应::

    {"success": true, ...插件返回的字段...}

或者错误::

    {"success": false, "error": "..."}
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from flask import Blueprint, current_app, jsonify, request, session

from app import db
from app.models import File, FolderPlugin
from app.models.folder_plugin import VALID_KINDS
from app.services import plugin_service
from app.utils.decorators import api_key_required, login_required

folder_plugin_bp = Blueprint('folder_plugin', __name__)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _own_folder_or_404(folder_id: int, user_id: int):
    f = File.query.filter_by(id=folder_id, user_id=user_id, is_directory=True).first()
    if not f:
        return None, (jsonify({'error': '文件夹不存在或无权访问'}), 404)
    return f, None


def _own_plugin_or_404(plugin_id: int, user_id: int):
    p = FolderPlugin.query.filter_by(id=plugin_id, user_id=user_id).first()
    if not p:
        return None, (jsonify({'error': 'plugin 不存在或无权访问'}), 404)
    return p, None


def _request_base_url() -> str:
    """组装下载 URL 用的 base：优先 PUBLIC_BASE_URL，回退 request.host_url。"""
    base = (current_app.config.get('PUBLIC_BASE_URL') or '').rstrip('/')
    if base:
        return base
    return (request.host_url or 'http://localhost:5000').rstrip('/')


def _build_payload_for_create(data: dict) -> tuple[dict, Optional[str]]:
    """从 JSON body 取出建插件的字段并做基本规整。"""
    name = (data.get('name') or '').strip()
    if not name:
        return {}, 'name 必填'
    if len(name) > 80:
        return {}, 'name 过长 (>80)'
    if '/' in name or '\\' in name:
        return {}, 'name 不允许包含路径分隔符'

    kind = (data.get('kind') or 'declarative').strip().lower()
    if kind not in VALID_KINDS:
        return {}, f'kind 必须是 {list(VALID_KINDS)} 之一'

    description = (data.get('description') or '').strip() or None
    include_subdirs = data.get('include_subdirs', True)
    if isinstance(include_subdirs, str):
        include_subdirs = include_subdirs.lower() in ('1', 'true', 'yes', 'on')
    enabled = data.get('enabled', True)
    if isinstance(enabled, str):
        enabled = enabled.lower() in ('1', 'true', 'yes', 'on')
    public = data.get('public', False)
    if isinstance(public, str):
        public = public.lower() in ('1', 'true', 'yes', 'on')

    # config 既允许传 dict (会 json.dumps), 也允许传字符串
    config_raw = data.get('config')
    config_json: Optional[str] = None
    if config_raw is not None:
        if isinstance(config_raw, str):
            config_json = config_raw
        else:
            try:
                config_json = json.dumps(config_raw, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                return {}, f'config 序列化失败: {e}'

    code = data.get('code')
    if code is not None and not isinstance(code, str):
        return {}, 'code 必须是字符串'

    errs = plugin_service.validate_plugin_spec(
        kind=kind, config_json=config_json, code=code,
    )
    if errs:
        return {}, '; '.join(errs)

    return {
        'name': name,
        'kind': kind,
        'description': description,
        'include_subdirs': bool(include_subdirs),
        'enabled': bool(enabled),
        'public': bool(public),
        'config': config_json,
        'code': code,
    }, None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@folder_plugin_bp.route('/api/folders/<int:folder_id>/plugins', methods=['GET'])
@login_required
def list_plugins_by_folder(folder_id: int):
    user_id = session.get('user_id')
    folder, err = _own_folder_or_404(folder_id, user_id)
    if err:
        return err
    rows = (
        FolderPlugin.query
        .filter_by(user_id=user_id, folder_id=folder.id)
        .order_by(FolderPlugin.id.desc())
        .all()
    )
    return jsonify({
        'success': True,
        'folder': {'id': folder.id, 'name': folder.name, 'path': folder.path},
        'plugins': [r.to_dict(folder=folder) for r in rows],
    })


@folder_plugin_bp.route('/api/folders/<int:folder_id>/plugins', methods=['POST'])
@login_required
def create_plugin(folder_id: int):
    user_id = session.get('user_id')
    folder, err = _own_folder_or_404(folder_id, user_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}

    payload, perr = _build_payload_for_create(data)
    if perr:
        return jsonify({'error': perr}), 400

    # 唯一性: (folder, name)
    if FolderPlugin.query.filter_by(folder_id=folder.id, name=payload['name']).first():
        return jsonify({'error': f'同名插件已存在: {payload["name"]}'}), 409

    p = FolderPlugin(user_id=user_id, folder_id=folder.id, **payload)
    db.session.add(p)
    db.session.commit()
    return jsonify({'success': True, 'plugin': p.to_dict(folder=folder, include_source=True)}), 201


@folder_plugin_bp.route('/api/folder-plugins/<int:plugin_id>', methods=['GET'])
@login_required
def get_plugin(plugin_id: int):
    user_id = session.get('user_id')
    p, err = _own_plugin_or_404(plugin_id, user_id)
    if err:
        return err
    folder = File.query.get(p.folder_id)
    return jsonify({'success': True, 'plugin': p.to_dict(folder=folder, include_source=True)})


@folder_plugin_bp.route('/api/folder-plugins/<int:plugin_id>', methods=['PUT', 'PATCH'])
@login_required
def update_plugin(plugin_id: int):
    user_id = session.get('user_id')
    p, err = _own_plugin_or_404(plugin_id, user_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}

    # 允许的字段
    if 'name' in data:
        new_name = (data.get('name') or '').strip()
        if not new_name:
            return jsonify({'error': 'name 不可为空'}), 400
        if new_name != p.name and FolderPlugin.query.filter_by(
            folder_id=p.folder_id, name=new_name
        ).first():
            return jsonify({'error': f'同名插件已存在: {new_name}'}), 409
        p.name = new_name

    if 'description' in data:
        p.description = (data.get('description') or '').strip() or None

    if 'enabled' in data:
        v = data.get('enabled')
        if isinstance(v, str):
            v = v.lower() in ('1', 'true', 'yes', 'on')
        p.enabled = bool(v)

    if 'include_subdirs' in data:
        v = data.get('include_subdirs')
        if isinstance(v, str):
            v = v.lower() in ('1', 'true', 'yes', 'on')
        p.include_subdirs = bool(v)

    if 'public' in data:
        v = data.get('public')
        if isinstance(v, str):
            v = v.lower() in ('1', 'true', 'yes', 'on')
        p.public = bool(v)

    new_kind = data.get('kind', p.kind)
    if new_kind not in VALID_KINDS:
        return jsonify({'error': f'kind 必须是 {list(VALID_KINDS)} 之一'}), 400

    config_raw = data.get('config', '__skip__')
    if config_raw != '__skip__':
        if config_raw is None:
            p.config = None
        elif isinstance(config_raw, str):
            p.config = config_raw
        else:
            try:
                p.config = json.dumps(config_raw, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                return jsonify({'error': f'config 序列化失败: {e}'}), 400

    code_raw = data.get('code', '__skip__')
    if code_raw != '__skip__':
        if code_raw is not None and not isinstance(code_raw, str):
            return jsonify({'error': 'code 必须是字符串'}), 400
        p.code = code_raw

    p.kind = new_kind

    errs = plugin_service.validate_plugin_spec(
        kind=p.kind, config_json=p.config, code=p.code,
    )
    if errs:
        return jsonify({'error': '; '.join(errs)}), 400

    db.session.commit()
    folder = File.query.get(p.folder_id)
    return jsonify({'success': True, 'plugin': p.to_dict(folder=folder, include_source=True)})


@folder_plugin_bp.route('/api/folder-plugins/<int:plugin_id>', methods=['DELETE'])
@login_required
def delete_plugin(plugin_id: int):
    user_id = session.get('user_id')
    p, err = _own_plugin_or_404(plugin_id, user_id)
    if err:
        return err
    db.session.delete(p)
    db.session.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# Invoke 入口（登录态自测 + 外部 API Key 调用）
# ---------------------------------------------------------------------------

def _do_invoke(plugin: FolderPlugin, *, public: bool = False):
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()
    params = data.get('params') or {}
    if not action:
        return jsonify({'success': False, 'error': 'action 必填'}), 400
    if not isinstance(params, dict):
        return jsonify({'success': False, 'error': 'params 必须是对象'}), 400

    body, status = plugin_service.invoke_plugin(
        plugin, action, params,
        base_url=_request_base_url(),
        api_key=request.headers.get('X-API-Key'),
        public=public,
    )
    return jsonify(body), status


@folder_plugin_bp.route('/api/folder-plugins/<int:plugin_id>/invoke', methods=['POST'])
@login_required
def invoke_plugin_logged_in(plugin_id: int):
    """登录态自测入口：用 session 验明 owner 身份后调用。"""
    user_id = session.get('user_id')
    p, err = _own_plugin_or_404(plugin_id, user_id)
    if err:
        return err
    return _do_invoke(p)


@folder_plugin_bp.route('/api/external/folder-plugins/<int:plugin_id>/invoke', methods=['POST'])
@api_key_required
def invoke_plugin_external(plugin_id: int):
    """外部客户端用 API Key 调用插件：API Key 必须属于该插件的 owner。"""
    user = request.user
    p = FolderPlugin.query.filter_by(id=plugin_id, user_id=user.id).first()
    if not p:
        return jsonify({'success': False, 'error': 'plugin 不存在或无权访问'}), 404
    return _do_invoke(p)


@folder_plugin_bp.route(
    '/api/external/folders/<int:folder_id>/plugins/<plugin_name>/invoke',
    methods=['POST'],
)
@api_key_required
def invoke_plugin_external_by_name(folder_id: int, plugin_name: str):
    """更易记的外部调用入口：``/api/external/folders/<id>/plugins/<name>/invoke``。"""
    user = request.user
    p = FolderPlugin.query.filter_by(
        user_id=user.id, folder_id=folder_id, name=plugin_name,
    ).first()
    if not p:
        return jsonify({'success': False, 'error': 'plugin 不存在或无权访问'}), 404
    return _do_invoke(p)


# ---------------------------------------------------------------------------
# 公开通道：plugin.public=True 时无需任何鉴权
# ---------------------------------------------------------------------------

def _public_plugin_or_403(plugin: Optional[FolderPlugin]):
    """公开入口的统一校验。失败时返回 (None, error_response_tuple)。"""
    if not plugin:
        return None, (jsonify({'success': False, 'error': 'plugin 不存在'}), 404)
    if not plugin.enabled:
        return None, (jsonify({'success': False, 'error': 'plugin disabled'}), 403)
    if not plugin.public:
        return None, (
            jsonify({
                'success': False,
                'error': '该插件未开启公开访问 (public=false); 请使用 /api/external/... 入口并提供 API Key',
            }),
            403,
        )
    return plugin, None


@folder_plugin_bp.route('/api/public/folder-plugins/<int:plugin_id>/invoke', methods=['POST'])
def invoke_plugin_public(plugin_id: int):
    """**无授权** 调用入口, 要求 ``plugin.public=True``。

    响应 ``url`` 自带 HMAC 签名, 客户端直接 GET 该 URL 即可下载文件。
    """
    p = FolderPlugin.query.filter_by(id=plugin_id).first()
    p, err = _public_plugin_or_403(p)
    if err:
        return err
    return _do_invoke(p, public=True)


@folder_plugin_bp.route(
    '/api/public/folders/<int:folder_id>/plugins/<plugin_name>/invoke',
    methods=['POST'],
)
def invoke_plugin_public_by_name(folder_id: int, plugin_name: str):
    """按 (folder_id, plugin_name) 寻址的无授权入口。"""
    p = FolderPlugin.query.filter_by(
        folder_id=folder_id, name=plugin_name,
    ).first()
    p, err = _public_plugin_or_403(p)
    if err:
        return err
    return _do_invoke(p, public=True)
