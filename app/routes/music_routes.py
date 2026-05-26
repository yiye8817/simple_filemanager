"""MusicFree 插件配套后端 API（蓝图前缀 ``/api/music``）。

MusicFree 是开源的 Android/Desktop 音乐播放器, 通过 CommonJS 插件接入任意音源
（详见 https://musicfree.catcat.work/plugin/introduction.html）。

本模块为 ``app/static/musicfree/file-manager.js`` 提供后端 endpoint, 让
MusicFree 客户端把 file_manager 当成一个音源使用。

**分享白名单**:
- 用户必须在 ``User.musicfree_enabled`` 上把总开关打开 (UI: "MusicFree 分享" modal),
  否则所有 ``/api/music/*`` 端点返回 403。
- 只有标记 ``File.music_shared=True`` 的目录及其下的 audio 文件才会被暴露;
  分享列表通过 ``/api/music-share/*`` 维护 (见 ``music_share_routes.py``)。

返回的字段名严格遵循 MusicFree 协议:

- ``IMusicItem``: ``id`` / ``title`` / ``artist`` / ``album`` / ``duration`` /
  ``platform`` / ``artwork`` (+ 任意自定义字段会被原样保留)
- ``IMusicSheetItem``: ``id`` / ``title`` / ``description`` / ``worksNum`` /
  ``coverImg`` / ``platform``

实际播放走 ``GET /api/music/track/<id>/source`` —— 该 endpoint 校验过白名单后
返回 ``{url, headers}``, 插件 JS 直接透传给 MusicFree, ExoPlayer 带头拉流。
"""
from __future__ import annotations

import os
from typing import Optional

from flask import Blueprint, current_app, jsonify, request, session

from app import db
from app.models import File, User
from app.utils.helpers import (
    FILE_TYPES,
    resolve_upload_physical_path,
)

music_bp = Blueprint('music', __name__)

PLATFORM_NAME = 'FileManager'
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 200

# 所有合法的音频扩展名（用于 search 兜底匹配, 不依赖 file_type 字段）
_AUDIO_EXTS = tuple(FILE_TYPES.get('audio') or ['.mp3'])


# ---------------------------------------------------------------------------
# 鉴权: 支持两种姿势
# ---------------------------------------------------------------------------
# - 浏览器/UI 走 cookie 登录态 (login_required)
# - MusicFree 插件走 X-API-Key 头 (api_key_required)
# 我们用一个 helper, 根据请求是否带 X-API-Key 选择路径; 这样测试也方便。

def _resolve_user() -> Optional[User]:
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    if api_key:
        return User.query.filter_by(api_key=api_key).first()
    uid = session.get('user_id')
    if uid:
        return User.query.get(uid)
    return None


def _auth_or_401():
    """统一鉴权 + 分享总开关校验。

    返回 ``(user, None)`` 表示通过; 否则返回 ``(None, (jsonify, status))``。
    把"未登录" 和 "未开启分享" 拆成两种错误码, 方便客户端区分:
    - 401: 没认证 (X-API-Key 缺失/无效, 或 cookie 过期)
    - 403: 认证过但用户没开 musicfree_enabled
    """
    user = _resolve_user()
    if not user:
        return None, (jsonify({'error': 'unauthorized: 需要 X-API-Key 头或登录态'}), 401)
    if not bool(user.musicfree_enabled):
        return None, (
            jsonify({
                'error': 'forbidden: 当前账号未启用 MusicFree 分享',
                'code': 'musicfree_disabled',
                'hint': '请在 file_manager 顶部 "MusicFree 分享" 中启用开关并选择要分享的文件夹',
            }),
            403,
        )
    return user, None


def _shared_folder_ids(user_id: int) -> list:
    """返回当前用户的 music_shared 目录 id 列表; 空列表代表"啥都没分享"。"""
    rows = (
        db.session.query(File.id)
        .filter(
            File.user_id == user_id,
            File.is_directory.is_(True),
            File.music_shared.is_(True),
        )
        .all()
    )
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# 数据装配
# ---------------------------------------------------------------------------

def _music_item(file: File, *, album_name: Optional[str] = None) -> dict:
    """File → MusicFree IMusicItem。"""
    name = file.name or ''
    stem, _ext = os.path.splitext(name)
    album = album_name
    if album is None:
        parent = File.query.get(file.parent_id) if file.parent_id else None
        album = parent.name if parent else ''
    return {
        'id': file.id,
        'title': stem or name,
        'artist': '未知歌手',
        'album': album or '',
        'duration': 0,
        'platform': PLATFORM_NAME,
        'artwork': None,
        # MusicFree 会原样保留自定义字段, 传回 getMediaSource / getLyric 时方便用
        '_file_id': file.id,
        '_folder_id': file.parent_id,
        '_path': file.path,
    }


def _sheet_item(folder: File, *, works_num: Optional[int] = None) -> dict:
    """目录 → MusicFree IMusicSheetItem (歌单)。"""
    if works_num is None:
        works_num = (
            File.query.filter_by(parent_id=folder.id, is_directory=False)
            .filter(File.file_type == 'audio')
            .count()
        )
    return {
        'id': folder.id,
        'title': folder.name,
        'description': f'{works_num} 首音频',
        'worksNum': works_num,
        'platform': PLATFORM_NAME,
        'coverImg': None,
        '_folder_id': folder.id,
        '_path': folder.path,
    }


def _paged(items, page, per_page):
    page = max(1, page or 1)
    per_page = max(1, min(per_page or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE))
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    slice_ = items[start:end]
    is_end = end >= total
    return slice_, is_end, total, page, per_page


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@music_bp.route('/api/music/info', methods=['GET'])
def music_info():
    """探测端点: 让 MusicFree 插件 / 客户端能拿到平台元信息。

    无需鉴权 (方便用户在客户端 ping 一下确认 baseUrl); 但若带了有效 X-API-Key,
    则附带当前用户的分享开关状态, 便于插件给用户更明确的提示。
    """
    payload = {
        'platform': PLATFORM_NAME,
        'api_version': 1,
        'auth_required': True,
        'description': 'file_manager 的 MusicFree 适配层',
    }
    user = _resolve_user()
    if user is not None:
        payload['authenticated'] = True
        payload['user'] = user.username
        payload['musicfree_enabled'] = bool(user.musicfree_enabled)
        payload['shared_folder_count'] = len(_shared_folder_ids(user.id))
    return jsonify(payload)


@music_bp.route('/api/music/search', methods=['GET'])
def music_search():
    """按文件名模糊搜索音频文件或目录。

    Query:
    - q          关键词 (必填)
    - page       从 1 起 (默认 1)
    - per_page   每页条数 (默认 30, 上限 200)
    - type       music (默认) / sheet
    """
    user, err = _auth_or_401()
    if err:
        return err
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'isEnd': True, 'data': []})

    page = request.args.get('page', type=int) or 1
    per_page = request.args.get('per_page', type=int) or DEFAULT_PAGE_SIZE
    type_ = (request.args.get('type') or 'music').lower()
    like = f'%{query}%'

    # 白名单: 用户没勾任何目录 → 直接空结果
    shared_ids = _shared_folder_ids(user.id)
    if not shared_ids:
        return jsonify({'isEnd': True, 'data': [], 'shared_folder_count': 0})

    if type_ == 'sheet':
        # 只在已分享的目录里搜
        folders = (
            File.query.filter(
                File.user_id == user.id,
                File.is_directory.is_(True),
                File.id.in_(shared_ids),
                File.name.ilike(like),
            )
            .order_by(File.modified_at.desc())
            .all()
        )
        sliced, is_end, total, page, per_page = _paged(folders, page, per_page)
        return jsonify({
            'isEnd': is_end,
            'data': [_sheet_item(f) for f in sliced],
            'total': total, 'page': page, 'per_page': per_page,
        })

    # type=music (默认) — 只搜分享目录下的音频
    audio = (
        File.query.filter(
            File.user_id == user.id,
            File.is_directory.is_(False),
            File.file_type == 'audio',
            File.parent_id.in_(shared_ids),
            File.name.ilike(like),
        )
        .order_by(File.modified_at.desc())
        .all()
    )
    sliced, is_end, total, page, per_page = _paged(audio, page, per_page)
    # 一次性把这批文件的 parent (专辑名) 拿到, 避免 N+1
    parent_ids = list({f.parent_id for f in sliced if f.parent_id})
    parent_map = {
        p.id: p.name for p in
        (File.query.filter(File.id.in_(parent_ids)).all() if parent_ids else [])
    }
    return jsonify({
        'isEnd': is_end,
        'data': [_music_item(f, album_name=parent_map.get(f.parent_id, '')) for f in sliced],
        'total': total, 'page': page, 'per_page': per_page,
    })


@music_bp.route('/api/music/sheets', methods=['GET'])
def music_sheets():
    """列出所有含音频文件的目录, 作为 MusicFree 的"推荐歌单"。

    一次 GROUP BY 算每个目录的音频数, 只返回 ``count > 0`` 的目录, 避免空目录污染。
    """
    user, err = _auth_or_401()
    if err:
        return err
    page = request.args.get('page', type=int) or 1
    per_page = request.args.get('per_page', type=int) or DEFAULT_PAGE_SIZE

    shared_ids = _shared_folder_ids(user.id)
    if not shared_ids:
        return jsonify({'isEnd': True, 'data': [], 'shared_folder_count': 0})

    # GROUP BY parent_id 拿到 (folder_id, audio_count); 限制在白名单内
    rows = (
        db.session.query(File.parent_id, db.func.count(File.id))
        .filter(
            File.user_id == user.id,
            File.is_directory.is_(False),
            File.file_type == 'audio',
            File.parent_id.in_(shared_ids),
        )
        .group_by(File.parent_id)
        .all()
    )
    if not rows:
        return jsonify({'isEnd': True, 'data': []})

    rows.sort(key=lambda r: r[1], reverse=True)  # 音频多的排前面
    sliced, is_end, total, page, per_page = _paged(rows, page, per_page)
    folder_ids = [r[0] for r in sliced]
    folder_map = {
        f.id: f for f in File.query.filter(File.id.in_(folder_ids)).all()
    }
    data = []
    for fid, cnt in sliced:
        folder = folder_map.get(fid)
        if folder is None:
            continue
        data.append(_sheet_item(folder, works_num=cnt))
    return jsonify({
        'isEnd': is_end, 'data': data,
        'total': total, 'page': page, 'per_page': per_page,
    })


@music_bp.route('/api/music/sheet/<int:folder_id>', methods=['GET'])
def music_sheet_detail(folder_id: int):
    """歌单(目录)详情: 列出该目录下所有音频文件。"""
    user, err = _auth_or_401()
    if err:
        return err
    folder = File.query.filter_by(
        id=folder_id, user_id=user.id, is_directory=True,
    ).first()
    if not folder:
        return jsonify({'error': '歌单不存在'}), 404
    if not bool(folder.music_shared):
        return jsonify({'error': '该歌单未分享到 MusicFree'}), 404

    page = request.args.get('page', type=int) or 1
    per_page = request.args.get('per_page', type=int) or DEFAULT_PAGE_SIZE

    audio = (
        File.query.filter_by(
            user_id=user.id, parent_id=folder.id,
            is_directory=False, file_type='audio',
        )
        .order_by(File.name.asc())
        .all()
    )
    sliced, is_end, total, page, per_page = _paged(audio, page, per_page)
    return jsonify({
        'isEnd': is_end,
        'musicList': [_music_item(f, album_name=folder.name) for f in sliced],
        'sheet': _sheet_item(folder, works_num=total),
        'total': total, 'page': page, 'per_page': per_page,
    })


def _shared_audio_or_404(user: User, file_id: int):
    """取出归属于该用户、在分享白名单内的 audio 文件; 越权直接 404。

    返回 ``(file, None)`` 或 ``(None, (resp, status))``。
    """
    f = File.query.filter_by(id=file_id, user_id=user.id, is_directory=False).first()
    if not f:
        return None, (jsonify({'error': '文件不存在'}), 404)
    if f.parent_id is None or f.parent_id not in _shared_folder_ids(user.id):
        return None, (jsonify({'error': '该文件未通过 MusicFree 分享'}), 404)
    return f, None


@music_bp.route('/api/music/track/<int:file_id>/source', methods=['GET'])
def music_track_source(file_id: int):
    """返回 ``{url, headers}`` 给 MusicFree 插件 ``getMediaSource`` 直接透传。

    这个 endpoint 做了两层校验:
    1. ``_auth_or_401`` 校验 X-API-Key + 总开关
    2. ``_shared_audio_or_404`` 校验该文件的父目录是否在分享白名单内

    然后吐出指向 ``/api/external/download/<id>`` 的 URL + 复用同一个 API Key 的
    headers。这样 ExoPlayer 等播放器拿到 URL 后直接带 header 去拉流, 后端的
    external download 路由已有的鉴权/防越权保护继续生效。
    """
    user, err = _auth_or_401()
    if err:
        return err
    track, err = _shared_audio_or_404(user, file_id)
    if err:
        return err

    # 优先用 X-API-Key 头里来的 key, 而不是 user.api_key (不直接泄露用户 token,
    # 即便客户端是 query param 走的, 也用 query 里同一个值返回)
    api_key = (
        request.headers.get('X-API-Key')
        or request.args.get('api_key')
        or user.api_key
    )
    base = request.host_url.rstrip('/')
    return jsonify({
        'url': f'{base}/api/external/download/{track.id}',
        'headers': {'X-API-Key': api_key},
        'file_id': track.id,
        'platform': PLATFORM_NAME,
    })


@music_bp.route('/api/music/track/<int:file_id>/lyric', methods=['GET'])
def music_track_lyric(file_id: int):
    """歌词: 同目录下找同名 .lrc 文件, 返回 ``{rawLrc}``。

    MusicFree ``getLyric`` 协议接受 ``{rawLrc: str}`` 或 ``{lrc: url}``。
    """
    user, err = _auth_or_401()
    if err:
        return err
    track, err = _shared_audio_or_404(user, file_id)
    if err:
        return err

    # 找同目录同 basename 的 .lrc
    stem, _ext = os.path.splitext(track.name or '')
    lrc = File.query.filter_by(
        user_id=user.id, parent_id=track.parent_id,
        is_directory=False, name=f'{stem}.lrc',
    ).first()
    if not lrc:
        return jsonify({'rawLrc': ''})

    upload_root = current_app.config['UPLOAD_FOLDER']
    phys = resolve_upload_physical_path(lrc.path, lrc.user_id, upload_root)
    if not os.path.exists(phys):
        return jsonify({'rawLrc': ''})
    try:
        with open(phys, 'r', encoding='utf-8', errors='replace') as fp:
            content = fp.read()
    except OSError as exc:
        return jsonify({'error': f'读取歌词失败: {exc}'}), 500
    return jsonify({'rawLrc': content})


@music_bp.route('/api/music/track/<int:file_id>', methods=['GET'])
def music_track_info(file_id: int):
    """单曲信息 (主要给客户端调试用; MusicFree 一般不主动调)。"""
    user, err = _auth_or_401()
    if err:
        return err
    f, err = _shared_audio_or_404(user, file_id)
    if err:
        return err
    parent = File.query.get(f.parent_id) if f.parent_id else None
    return jsonify({
        'success': True,
        'track': _music_item(f, album_name=parent.name if parent else ''),
    })
