"""MusicFree 分享配置路由 (蓝图前缀 ``/api/music-share``)。

负责管理用户 → MusicFree 插件分享内容的"白名单"配置:

- ``musicfree_enabled`` (User 维度): 总开关; 关闭后 /api/music/* 一律返回 403, 客户端就拿不到任何东西
- ``music_shared`` (File 目录维度): 决定哪些目录的音频被暴露 (空白名单 = 啥都不分享)

UI 侧典型流程:
1. 打开 modal: 先 ``GET /api/music-share/config`` 拿当前总开关 + 已勾选目录
2. 列出可选目录: ``GET /api/music-share/folders`` (只返回当前用户拥有 audio 文件的目录)
3. 用户勾选并保存: ``PUT /api/music-share/config`` 一次性提交 (enabled + folder_ids)
4. 或者增量切换某个目录: ``POST /api/music-share/folders/<id>/toggle``
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import db
from app.models import File, User
from app.utils.decorators import login_required
from flask import session


music_share_bp = Blueprint('music_share', __name__)


def _current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)


def _audio_folder_rows(user_id: int):
    """一次 GROUP BY 拿出 (folder_id, audio_count); 用在多个 endpoint 里, 抽出来。"""
    return (
        db.session.query(File.parent_id, db.func.count(File.id))
        .filter(
            File.user_id == user_id,
            File.is_directory.is_(False),
            File.file_type == 'audio',
            File.parent_id.isnot(None),
        )
        .group_by(File.parent_id)
        .all()
    )


@music_share_bp.route('/api/music-share/config', methods=['GET'])
@login_required
def get_config():
    user = _current_user()
    if not user:
        return jsonify({'error': 'unauthorized'}), 401
    shared_ids = [
        f.id for f in
        File.query.filter_by(user_id=user.id, is_directory=True, music_shared=True).all()
    ]
    return jsonify({
        'enabled': bool(user.musicfree_enabled),
        'shared_folder_ids': shared_ids,
        'shared_count': len(shared_ids),
    })


@music_share_bp.route('/api/music-share/config', methods=['PUT'])
@login_required
def update_config():
    """一次提交完整配置: ``{enabled: bool, shared_folder_ids: [int]}``。

    设计上把"全选 / 全清"也归到这条 endpoint —— UI 只需在前端算好 ids 一起 PUT, 后端
    用一次事务覆盖, 不会出现"半同步"中间态。
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))
    ids = data.get('shared_folder_ids') or []
    if not isinstance(ids, list):
        return jsonify({'error': 'shared_folder_ids 必须是数组'}), 400
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        return jsonify({'error': 'shared_folder_ids 元素必须是整数'}), 400

    user.musicfree_enabled = enabled

    # 校验这些 id 都是当前用户的目录, 避免越权设置别人的目录
    legal = set()
    if ids:
        rows = (
            File.query.filter(
                File.user_id == user.id,
                File.is_directory.is_(True),
                File.id.in_(ids),
            ).all()
        )
        legal = {f.id for f in rows}

    # 先清空当前用户的所有 music_shared, 再把合法 id 设为 True; 一次事务保证一致性
    File.query.filter_by(user_id=user.id, is_directory=True, music_shared=True).update(
        {'music_shared': False}, synchronize_session=False,
    )
    if legal:
        File.query.filter(
            File.user_id == user.id,
            File.is_directory.is_(True),
            File.id.in_(list(legal)),
        ).update({'music_shared': True}, synchronize_session=False)

    db.session.commit()
    return jsonify({
        'success': True,
        'enabled': enabled,
        'shared_folder_ids': sorted(legal),
        'invalid_ids': sorted(set(ids) - legal),
    })


@music_share_bp.route('/api/music-share/folders', methods=['GET'])
@login_required
def list_audio_folders():
    """列出当前用户所有 "含音频的目录"; 供 UI 渲染勾选清单。"""
    user = _current_user()
    if not user:
        return jsonify({'error': 'unauthorized'}), 401
    rows = _audio_folder_rows(user.id)
    if not rows:
        return jsonify({'folders': []})
    rows.sort(key=lambda r: r[1], reverse=True)
    folder_ids = [r[0] for r in rows]
    folder_map = {f.id: f for f in File.query.filter(File.id.in_(folder_ids)).all()}
    folders = []
    for fid, cnt in rows:
        folder = folder_map.get(fid)
        if folder is None:
            continue
        folders.append({
            'id': folder.id,
            'name': folder.name,
            'path': folder.path,
            'audio_count': int(cnt),
            'shared': bool(folder.music_shared),
        })
    return jsonify({'folders': folders})


@music_share_bp.route('/api/music-share/folders/<int:folder_id>/toggle', methods=['POST'])
@login_required
def toggle_folder(folder_id: int):
    """切换单个目录的 shared 状态; body 可选 ``{shared: bool}``, 不传则翻转当前值。

    用在文件页右键菜单 / 一个开关组件上, 比一次性提交完整列表更顺手。
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'unauthorized'}), 401
    folder = File.query.filter_by(id=folder_id, user_id=user.id, is_directory=True).first()
    if not folder:
        return jsonify({'error': '文件夹不存在'}), 404
    data = request.get_json(silent=True) or {}
    new_value = data['shared'] if 'shared' in data else (not folder.music_shared)
    folder.music_shared = bool(new_value)
    db.session.commit()
    return jsonify({
        'success': True,
        'folder_id': folder.id,
        'music_shared': folder.music_shared,
    })
