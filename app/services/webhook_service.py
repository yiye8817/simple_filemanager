"""文件夹监听 → 远端 Webhook 通知服务。

实现要点：

1. **触发点是业务层**而非磁盘 watchdog：所有变更都在
   ``file_routes`` 的 upload/merge/new/save/import/delete 接口里写库，那些
   位置调用 :func:`notify_file_event` 即可。
2. **祖先匹配**：一个文件变化时，要把它的**所有祖先目录**都拿出来，凡是
   配了 :class:`FolderWatch` 的祖先都要触发；``include_subdirs=False``
   时仅匹配直接父目录。
3. **异步**：HTTP POST 在 daemon Thread 里发，主请求线程不阻塞。
4. **HMAC 签名**：若配置了 ``secret``，请求头会带 ``X-Signature: sha256=...``
   方便接收方验证请求来源；body 用 JSON。
5. **失败不阻塞**：发送失败只写到 DB 的 ``last_error`` + 日志，业务接口
   不感知；调用方传错或对端 5xx 都不会影响主流程。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime
from types import SimpleNamespace
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app, has_app_context, has_request_context, request

logger = logging.getLogger(__name__)


def make_file_snapshot(file_obj):
    """从 ORM ``File`` 实例复制一份事件需要的字段，返回 :class:`SimpleNamespace`。

    用于"删除事件"等 ORM 实例已被 ``db.session.delete`` 标记的场景：直接持有
    被删实例可能在 session commit 后访问属性时抛 ``DetachedInstanceError``。先
    复制一份关键字段就稳了。
    """
    if file_obj is None:
        return None
    return SimpleNamespace(
        id=getattr(file_obj, 'id', None),
        name=getattr(file_obj, 'name', None),
        path=getattr(file_obj, 'path', None),
        size=getattr(file_obj, 'size', 0) or 0,
        file_type=getattr(file_obj, 'file_type', None),
        is_directory=bool(getattr(file_obj, 'is_directory', False)),
        user_id=getattr(file_obj, 'user_id', None),
        parent_id=getattr(file_obj, 'parent_id', None),
        modified_at=getattr(file_obj, 'modified_at', None),
    )


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ancestor_ids(file_obj, *, include_self_if_dir: bool = False) -> list[int]:
    """收集 ``file_obj`` 的所有祖先目录 id。

    - 普通文件：返回所有祖先目录链
    - 文件夹：默认仅返回上层祖先；``include_self_if_dir=True`` 时把自身也算进去
      （删除一个被监听的目录时使用，让该目录上的 watch 也能收到事件）。
    """
    from app.models.file import File  # 延迟导入避免循环

    if file_obj is None:
        return []

    ids: list[int] = []
    if file_obj.is_directory and include_self_if_dir:
        ids.append(file_obj.id)

    cur = file_obj
    visited: set[int] = set()
    while True:
        parent_id = getattr(cur, 'parent_id', None)
        if not parent_id or parent_id in visited:
            break
        visited.add(parent_id)
        parent = File.query.filter_by(id=parent_id).first()
        if parent is None or not parent.is_directory:
            break
        ids.append(parent.id)
        cur = parent
    return ids


def _build_base_url() -> str:
    """优先用配置 ``PUBLIC_BASE_URL``，否则从当前请求推断；最后兜底空串。"""
    if has_app_context():
        base = current_app.config.get('PUBLIC_BASE_URL')
        if base:
            return str(base).rstrip('/')
    if has_request_context():
        return request.host_url.rstrip('/')
    return ''


def _file_payload(file_obj, base_url: str) -> dict:
    is_dir = bool(getattr(file_obj, 'is_directory', False))
    fid = getattr(file_obj, 'id', None)
    payload = {
        'id': fid,
        'name': getattr(file_obj, 'name', None),
        'path': getattr(file_obj, 'path', None),
        'size': _safe_int(getattr(file_obj, 'size', 0)),
        'file_type': getattr(file_obj, 'file_type', None),
        'is_dir': is_dir,
        'user_id': getattr(file_obj, 'user_id', None),
        'parent_id': getattr(file_obj, 'parent_id', None),
        'modified_at': (
            file_obj.modified_at.isoformat()
            if getattr(file_obj, 'modified_at', None) else None
        ),
    }
    if base_url and fid and not is_dir:
        payload['download_url'] = f"{base_url}/api/download/{fid}"
        payload['preview_url'] = f"{base_url}/api/preview/{fid}"
        payload['serve_url'] = f"{base_url}/api/serve/{fid}"
        # 上面三个都是 @login_required (要 session cookie), 不适合给 server-to-server
        # 或第三方客户端直接拉。external_download_url 走 @api_key_required, 客户端
        # 在请求时带上 X-API-Key 头即可下载 — 这才是给 musicfree_server / 第三方插件
        # 真正能用的拉流 URL。老字段保留不动, 兼容已有下游。
        payload['external_download_url'] = f"{base_url}/api/external/download/{fid}"
    return payload


def _sign_body(body: bytes, secret: str | None) -> str | None:
    if not secret:
        return None
    mac = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _send_once(app, watch_id: int, url: str, body: bytes, signature: str | None,
               timeout: float = 8.0) -> None:
    """单次 HTTP POST。在后台线程内执行，需要自己拿 app_context 才能写 DB。"""
    from app import db
    from app.models.folder_watch import FolderWatch

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        logger.warning('webhook[%s] skip: scheme=%s', watch_id, parsed.scheme)
        return

    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'file_manager-webhook/1.0',
    }
    if signature:
        headers['X-Signature'] = signature

    status: int | None = None
    err_msg: str | None = None
    try:
        req = Request(url, data=body, headers=headers, method='POST')
        with urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            # 主动读一点点 body，避免连接半挂
            try:
                resp.read(2048)
            except Exception:
                pass
    except HTTPError as exc:
        status = exc.code
        err_msg = f'HTTP {exc.code} {exc.reason}'
    except URLError as exc:
        err_msg = f'URLError: {exc.reason}'
    except (TimeoutError, OSError) as exc:
        err_msg = f'网络错误: {exc}'
    except Exception as exc:  # noqa: BLE001
        err_msg = f'未预期错误: {exc}'

    # 回写状态：必须在 app_context 内
    try:
        with app.app_context():
            w = FolderWatch.query.get(watch_id)
            if w is None:
                return
            w.last_triggered_at = datetime.utcnow()
            w.last_status_code = status
            w.last_error = (err_msg or '')[:500] if err_msg else None
            w.trigger_count = (w.trigger_count or 0) + 1
            db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning('webhook[%s] DB 更新失败: %s', watch_id, exc)

    if err_msg:
        logger.info('webhook[%s] -> %s failed: %s', watch_id, url, err_msg)
    else:
        logger.info('webhook[%s] -> %s ok status=%s', watch_id, url, status)


def _matching_watches(file_obj, event: str) -> list:
    """返回**生效的**、且监听了 ``event`` 的 FolderWatch 列表（按 user_id 自动隔离）。

    匹配规则：
        - ``include_subdirs=True``  → 所有祖先目录中的 watch 都命中
        - ``include_subdirs=False`` → 仅"直接父目录"上的 watch 命中
    """
    from app.models.folder_watch import FolderWatch

    user_id = getattr(file_obj, 'user_id', None)
    if not user_id:
        return []

    parent_id = getattr(file_obj, 'parent_id', None)
    if not parent_id:
        return []

    # 删除目录时，希望该目录上的 watch 自己也能收到（事件类型为 deleted/created 不重要，
    # 关键是路径祖先链是否包含 watch）。普通文件不带自身。
    include_self_if_dir = (
        bool(getattr(file_obj, 'is_directory', False))
        and event in ('deleted', 'created', 'modified')
    )
    ancestor_ids = _ancestor_ids(file_obj, include_self_if_dir=include_self_if_dir)
    if not ancestor_ids:
        return []

    rows = (
        FolderWatch.query
        .filter(
            FolderWatch.user_id == user_id,
            FolderWatch.enabled.is_(True),
            FolderWatch.folder_id.in_(ancestor_ids),
        )
        .all()
    )
    if not rows:
        return []

    matched = []
    for w in rows:
        if not w.matches_event(event):
            continue
        # include_subdirs=False 时只允许直接父目录命中
        if not w.include_subdirs and w.folder_id != parent_id:
            continue
        matched.append(w)
    return matched


def notify_file_event(file_obj, event: str, *, base_url: str | None = None) -> int:
    """业务侧统一入口：上传/修改/删除等动作完成后调用。

    返回触发的 watch 数（仅"调度成功"计数，HTTP 响应在后台线程异步更新）。
    任何异常都被吞掉 —— 文件主流程绝不能因为通知失败而被中断。
    """
    try:
        if event not in ('created', 'modified', 'deleted'):
            logger.debug('notify_file_event: skip unknown event=%s', event)
            return 0
        watches = _matching_watches(file_obj, event)
        if not watches:
            return 0

        # 在主线程内拼好 base_url 与 payload —— 后台线程没有 request 上下文
        eff_base = base_url if base_url is not None else _build_base_url()
        file_data = _file_payload(file_obj, eff_base)

        if not has_app_context():
            logger.debug('notify_file_event: 无 app 上下文，跳过调度')
            return 0
        app = current_app._get_current_object()  # type: ignore[attr-defined]

        scheduled = 0
        for w in watches:
            try:
                from app.models.file import File
                folder = File.query.get(w.folder_id)
            except Exception:  # noqa: BLE001
                folder = None
            payload = {
                'event': event,
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'watch': {
                    'id': w.id,
                    'folder_id': w.folder_id,
                    'folder_name': folder.name if folder else None,
                    'folder_path': folder.path if folder else None,
                    'include_subdirs': w.include_subdirs,
                },
                'file': file_data,
            }
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            sig = _sign_body(body, w.secret)

            t = threading.Thread(
                target=_send_once,
                args=(app, w.id, w.url, body, sig),
                daemon=True,
            )
            t.start()
            scheduled += 1
        return scheduled
    except Exception as exc:  # noqa: BLE001
        logger.warning('notify_file_event 异常被吞: %s', exc)
        return 0


def notify_files_event(file_objs: Iterable, event: str, *, base_url: str | None = None) -> int:
    """批量通知（如多文件上传时一次 commit、最后批量通知）。"""
    total = 0
    eff_base = base_url if base_url is not None else None
    for f in file_objs or []:
        total += notify_file_event(f, event, base_url=eff_base)
    return total
