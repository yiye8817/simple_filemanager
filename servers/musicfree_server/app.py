"""musicfree_server — 把 file_manager webhook 转成 MusicFree 协议的独立服务。

启动方式 (服务托管):
    bash run.sh <localPort>

启动方式 (本地直跑):
    MFS_PUBLIC_BASE_URL=http://127.0.0.1:8765 \
        python -m servers.musicfree_server.app --port 8765

端点速查:
    POST /webhook/files                     接收 file_manager webhook (含 X-Signature)
    POST /webhook/test                      手动塞一条 test payload, 调试用
    GET  /health                            健康检查 + 引用方式
    GET  /                                  欢迎页 (HTML), 打印配置引导
    GET  /admin/state                       完整内部清单 (调试用)
    POST /admin/clear                       清空清单 (危险操作, 仅本地调用)

    GET  /api/music/info                    MusicFree 探测端点
    GET  /api/music/search                  q=&type=music|sheet&page=
    GET  /api/music/sheets
    GET  /api/music/sheet/<id>
    GET  /api/music/track/<id>/source       {url, headers} for getMediaSource
    GET  /api/music/track/<id>/lyric        {rawLrc}

    GET  /static/musicfree/file-manager.js  MusicFree 插件 JS (开箱即用,
                                             响应时会注入当前服务的 base URL)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import urllib.request
from typing import Optional

from flask import Blueprint, Flask, abort, jsonify, request

# 允许 "python -m servers.musicfree_server.app" 与 "python app.py" 两种姿势
if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import Config  # type: ignore
    from store import Track, TrackStore  # type: ignore
else:
    from .config import Config
    from .store import Track, TrackStore  # noqa: F401


PLATFORM_NAME = 'FileManager-MusicFree-Server'
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 200

# 插件 JS 在仓库里的路径 (相对本文件), 启动时由 / static/musicfree/file-manager.js
# 路由动态读取 + 占位符替换后返回。
PLUGIN_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'static', 'musicfree', 'file-manager.js')
PLUGIN_JS_URL_PATH = '/static/musicfree/file-manager.js'

# file_manager 的几条"需要 session cookie"的 URL 路径片段, MusicFree 客户端拿到
# 也用不了; 兜底时统一替换成 /api/external/download/<id> (走 @api_key_required)。
_LEGACY_PATH_RE = re.compile(r'/api/(?:serve|download|preview)/(\d+)\b')


def _to_streamable_url(track: Track) -> Optional[str]:
    """挑出真正能被 MusicFree 客户端用 X-API-Key 拉流的 URL。

    优先级:
        1. webhook 直接给的 external_download_url (新版 file_manager)
        2. download_url / serve_url, 但把路径从 ``/api/(serve|download|preview)/<id>``
           替换为 ``/api/external/download/<id>`` (老版本 file_manager 兜底)
        3. 实在没有就返回 None, 上层会 404
    """
    if track.external_download_url:
        return track.external_download_url
    for raw in (track.download_url, track.serve_url):
        if not raw:
            continue
        new_url, n = _LEGACY_PATH_RE.subn(r'/api/external/download/\1', raw, count=1)
        if n:
            return new_url
        return raw  # 形态不认识就原样返回, 让用户能从浏览器里诊断
    return None


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        stream=sys.stdout,  # 走 stdout, 服务托管会重定向到 run.log
    )


def create_app(config: Optional[Config] = None, store: Optional[TrackStore] = None) -> Flask:
    cfg = config or Config()
    _setup_logging(cfg.log_level)
    log = logging.getLogger('musicfree_server')

    app = Flask(__name__)
    app.config['_mfs_config'] = cfg
    app.config['_mfs_store'] = store or TrackStore(cfg.state_path)

    _print_startup_banner(app, log)

    # ---------------------------------------------------------------- shared
    def _get_store() -> TrackStore:
        return app.config['_mfs_store']

    def _get_cfg() -> Config:
        return app.config['_mfs_config']

    def _require_query_token():
        token = _get_cfg().music_query_token
        if not token:
            return None
        provided = request.headers.get('X-Music-Token') or request.args.get('token')
        if provided != token:
            return jsonify({'error': 'unauthorized: bad token'}), 401
        return None

    def _media_item(t: Track) -> dict:
        cfg2 = _get_cfg()
        url = cfg2.rewrite(_to_streamable_url(t)) or ''
        return {
            'id': t.id,
            'title': t.title,
            'artist': '未知歌手',
            'album': t.parent_name or '',
            'duration': 0,
            'platform': PLATFORM_NAME,
            'artwork': None,
            '_file_id': t.id,
            '_folder_id': t.parent_id,
            '_path': t.path,
            '_download_url': url,
        }

    def _sheet_item(s) -> dict:  # type: ignore[no-untyped-def]
        return {
            'id': s.id,
            'title': s.name or '',
            'description': f'{s.works_num} 首音频',
            'worksNum': s.works_num,
            'platform': PLATFORM_NAME,
            'coverImg': None,
            '_folder_id': s.id,
            '_path': s.path,
        }

    def _paged(items, page, per_page):
        page = max(1, page or 1)
        per_page = max(1, min(per_page or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE))
        total = len(items)
        start = (page - 1) * per_page
        end = start + per_page
        return items[start:end], end >= total, total, page, per_page

    # ================================================================ webhook
    @app.route('/webhook/files', methods=['POST'])
    def webhook_files():
        cfg2 = _get_cfg()
        raw_body = request.get_data() or b''
        # HMAC 验签 (如果配了 secret)
        if cfg2.webhook_secret:
            sig = request.headers.get('X-Signature') or ''
            expected = 'sha256=' + hmac.new(
                cfg2.webhook_secret.encode('utf-8'), raw_body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                log.warning('webhook 签名校验失败: provided=%r', sig)
                return jsonify({'error': '签名校验失败'}), 401
        try:
            payload = json.loads(raw_body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return jsonify({'error': 'invalid json'}), 400

        event = payload.get('event') or ''
        file_data = payload.get('file') or {}
        watch_data = payload.get('watch') or {}

        store_ = _get_store()
        if event == 'test':
            log.info('收到 test 事件, watch=%r', watch_data.get('id'))
            return jsonify({'ok': True, 'echo': payload})

        action = store_.apply_event(event, file_data, watch_data)
        log.info(
            'webhook event=%s file.id=%r name=%r → %s; stats=%s',
            event, file_data.get('id'), file_data.get('name'), action, store_.stats(),
        )
        return jsonify({'ok': True, 'action': action, 'stats': store_.stats()})

    @app.route('/webhook/test', methods=['POST'])
    def webhook_test():
        """本地构造一条 created 事件, 主要给 README / curl 例子用。"""
        sample = request.get_json(silent=True) or {
            'event': 'created',
            'file': {
                'id': 9001,
                'name': 'demo.mp3',
                'path': 'demo-folder/demo.mp3',
                'size': 1024,
                'file_type': 'audio',
                'parent_id': 9000,
                'is_dir': False,
                'modified_at': '2026-05-23T20:30:00',
                'download_url': 'http://localhost/api/download/9001',
                'serve_url': 'http://localhost/api/serve/9001',
            },
            'watch': {'id': 1, 'folder_id': 9000, 'folder_name': 'demo-folder'},
        }
        action = _get_store().apply_event(sample.get('event'), sample.get('file', {}), sample.get('watch', {}))
        return jsonify({'ok': True, 'action': action, 'sample': sample})

    # =============================================================== /api/music
    music_bp = Blueprint('music', __name__)

    @music_bp.route('/api/music/info', methods=['GET'])
    def music_info():
        s = _get_store().stats()
        # 不要求 token; 让用户能 ping 探测
        return jsonify({
            'platform': PLATFORM_NAME,
            'api_version': 1,
            'description': '自托管的 MusicFree 音源 (来自 file_manager webhook)',
            'auth_required': bool(_get_cfg().music_query_token),
            'stats': s,
        })

    @music_bp.route('/api/music/sheets', methods=['GET'])
    def music_sheets():
        err = _require_query_token()
        if err:
            return err
        page = request.args.get('page', type=int) or 1
        per_page = request.args.get('per_page', type=int) or DEFAULT_PAGE_SIZE
        sheets = _get_store().sheets()
        sliced, is_end, total, page, per_page = _paged(sheets, page, per_page)
        return jsonify({
            'isEnd': is_end,
            'data': [_sheet_item(s) for s in sliced],
            'total': total, 'page': page, 'per_page': per_page,
        })

    @music_bp.route('/api/music/sheet/<int:sheet_id>', methods=['GET'])
    def music_sheet(sheet_id: int):
        err = _require_query_token()
        if err:
            return err
        store_ = _get_store()
        s = store_.sheet(sheet_id)
        if not s:
            return jsonify({'error': 'sheet 不存在'}), 404
        tracks = [t for t in (store_.get_track(tid) for tid in s.track_ids) if t]
        tracks.sort(key=lambda x: x.name.lower())
        page = request.args.get('page', type=int) or 1
        per_page = request.args.get('per_page', type=int) or DEFAULT_PAGE_SIZE
        sliced, is_end, total, page, per_page = _paged(tracks, page, per_page)
        return jsonify({
            'isEnd': is_end,
            'musicList': [_media_item(t) for t in sliced],
            'sheet': _sheet_item(s),
            'total': total, 'page': page, 'per_page': per_page,
        })

    @music_bp.route('/api/music/search', methods=['GET'])
    def music_search():
        err = _require_query_token()
        if err:
            return err
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'isEnd': True, 'data': []})
        type_ = (request.args.get('type') or 'music').lower()
        page = request.args.get('page', type=int) or 1
        per_page = request.args.get('per_page', type=int) or DEFAULT_PAGE_SIZE
        store_ = _get_store()
        if type_ == 'sheet':
            results = store_.search_sheets(q)
            sliced, is_end, total, page, per_page = _paged(results, page, per_page)
            return jsonify({
                'isEnd': is_end,
                'data': [_sheet_item(s) for s in sliced],
                'total': total, 'page': page, 'per_page': per_page,
            })
        results = store_.search_tracks(q, limit=500)
        sliced, is_end, total, page, per_page = _paged(results, page, per_page)
        return jsonify({
            'isEnd': is_end,
            'data': [_media_item(t) for t in sliced],
            'total': total, 'page': page, 'per_page': per_page,
        })

    def _pick_fm_api_key() -> tuple[str, str]:
        """选用哪份 file_manager API Key 给客户端拉流用。

        优先级 (高→低):
            1. 请求头 ``X-File-Manager-Key`` —— 客户端在插件设置里填的 apiKey
            2. 查询参数 ``?fm_key=...`` —— 主要给 curl 调试用
            3. 环境变量 ``MFS_FILE_MANAGER_API_KEY`` —— 服务端兜底配置

        返回 ``(api_key, source)``;  source ∈ {'client', 'query', 'server', ''}。
        把 source 记到日志, 用户排错时一眼看出是哪一层兜底了。
        """
        from_header = (request.headers.get('X-File-Manager-Key') or '').strip()
        if from_header:
            return from_header, 'client'
        from_query = (request.args.get('fm_key') or '').strip()
        if from_query:
            return from_query, 'query'
        if _get_cfg().file_manager_api_key:
            return _get_cfg().file_manager_api_key, 'server'
        return '', ''

    @music_bp.route('/api/music/track/<int:track_id>/source', methods=['GET'])
    def music_track_source(track_id: int):
        err = _require_query_token()
        if err:
            return err
        t = _get_store().get_track(track_id)
        if not t:
            return jsonify({'error': 'track 不存在'}), 404
        cfg2 = _get_cfg()
        url = cfg2.rewrite(_to_streamable_url(t))
        if not url:
            return jsonify({'error': '该 track 没有 download_url (file_manager 未在 webhook 中提供)'}), 404

        api_key, key_source = _pick_fm_api_key()
        headers: dict = {}
        warning = None
        if api_key:
            headers['X-API-Key'] = api_key
            log.info('track %s source 使用 %s 提供的 X-API-Key', track_id, key_source)
        else:
            # 没拿到任何 key, 拉流会 401。这里给客户端一个明确诊断。
            log.warning(
                'track %s 返回播放 URL 但没拿到 file_manager API Key '
                '(请求未带 X-File-Manager-Key, 也没配 MFS_FILE_MANAGER_API_KEY), '
                '客户端拉流会 401', track_id,
            )
            warning = (
                '没拿到 file_manager API Key. 请在 MusicFree 插件设置里填 apiKey, '
                '或在 musicfree_server 启动时配 MFS_FILE_MANAGER_API_KEY.'
            )
        return jsonify({
            'url': url,
            'headers': headers,
            'file_id': t.id,
            'platform': PLATFORM_NAME,
            '_key_source': key_source or 'none',
            '_warning': warning,
        })

    @music_bp.route('/api/music/track/<int:track_id>/lyric', methods=['GET'])
    def music_track_lyric(track_id: int):
        err = _require_query_token()
        if err:
            return err
        t = _get_store().get_track(track_id)
        if not t:
            return jsonify({'rawLrc': ''})
        # lyric 字段可能是占位符, 也可能是直接的 URL; 我们从远端拉一下
        if not t.lyric or t.lyric == '__lrc_placeholder__':
            return jsonify({'rawLrc': ''})
        if t.lyric.startswith(('http://', 'https://')):
            api_key, key_source = _pick_fm_api_key()
            try:
                req = urllib.request.Request(t.lyric)
                if api_key:
                    req.add_header('X-API-Key', api_key)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    content = resp.read().decode('utf-8', errors='replace')
                log.info('track %s lyric 已拉取 (key_source=%s)', track_id, key_source or 'none')
                return jsonify({'rawLrc': content})
            except Exception as e:  # noqa: BLE001
                log.warning('拉歌词失败 %s (key_source=%s): %s', t.lyric, key_source or 'none', e)
                return jsonify({'rawLrc': ''})
        return jsonify({'rawLrc': t.lyric})

    app.register_blueprint(music_bp)

    # ================================================================ admin / health
    # ============================================================== plugin js
    @app.route(PLUGIN_JS_URL_PATH, methods=['GET'])
    def musicfree_plugin_js():
        """把 ``static/musicfree/file-manager.js`` 模板里的占位符替换成
        当前服务的对外地址后返回。这样客户端从这里"网络安装"插件后,
        baseUrl / srcUrl 都默认指向本服务, 无需再手填。"""
        try:
            with open(PLUGIN_JS_PATH, encoding='utf-8') as fp:
                body = fp.read()
        except OSError as exc:
            log.error('插件 JS 读取失败: %s', exc)
            return jsonify({'error': '插件 JS 不可用'}), 500
        cfg2 = _get_cfg()
        base = (cfg2.public_base_url or request.host_url.rstrip('/')).rstrip('/')
        body = body.replace('__MFS_PUBLIC_BASE_URL__', base)
        body = body.replace('__MFS_SRC_URL__', base + PLUGIN_JS_URL_PATH)
        return body, 200, {
            'Content-Type': 'application/javascript; charset=utf-8',
            'Cache-Control': 'no-store',
        }

    @app.route('/admin/state', methods=['GET'])
    def admin_state():
        store_ = _get_store()
        with store_._lock:  # noqa: SLF001 — 仅本进程调试用
            return jsonify({
                'stats': store_.stats(),
                'tracks': [
                    {
                        'id': t.id, 'name': t.name, 'parent_id': t.parent_id,
                        'parent_name': t.parent_name, 'size': t.size,
                        'modified_at': t.modified_at, 'added_at': t.added_at,
                    } for t in store_.all_tracks()
                ],
                'sheets': [
                    {'id': s.id, 'name': s.name, 'works_num': s.works_num,
                     'path': s.path, 'track_ids': s.track_ids}
                    for s in store_.sheets()
                ],
            })

    @app.route('/admin/clear', methods=['POST'])
    def admin_clear():
        # 只允许 127.0.0.1 访问 (避免被外部 frpc 代理过来误清)
        if request.remote_addr not in ('127.0.0.1', '::1'):
            abort(403)
        _get_store().clear()
        log.warning('管理员通过 /admin/clear 清空了清单')
        return jsonify({'ok': True})

    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({
            'ok': True,
            'stats': _get_store().stats(),
            'usage': _usage_hints(_get_cfg(), request.host_url),
            'config': _get_cfg().as_summary(),
        })

    @app.route('/', methods=['GET'])
    def index():
        cfg2 = _get_cfg()
        s = _get_store().stats()
        usage = _usage_hints(cfg2, request.host_url)
        # 简洁 HTML; 让用户复制就能用
        html_parts = [
            '<!DOCTYPE html>',
            '<html lang="zh-CN"><head><meta charset="UTF-8"><title>musicfree_server</title>',
            '<style>body{font-family:system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;color:#222;line-height:1.55}',
            'code,pre{font-family:Menlo,Consolas,monospace}'
            'pre{background:#f5f5f5;padding:1rem;border-radius:6px;overflow:auto}'
            'h2{margin-top:2rem;border-bottom:1px solid #eee;padding-bottom:.3rem}'
            '.badge{display:inline-block;background:#198754;color:#fff;padding:.1rem .5rem;border-radius:4px;font-size:.8rem}'
            '</style></head><body>',
            '<h1>musicfree_server <span class="badge">运行中</span></h1>',
            f'<p>清单: <strong>{s["tracks"]}</strong> 首音频 / <strong>{s["sheets"]}</strong> 个歌单 · '
            f'共处理 <strong>{s["events_seen"]}</strong> 个 webhook 事件</p>',
            '<h2>1. 让 file_manager 把变化推到这里</h2>',
            '<p>在 file_manager 给一个含音频的文件夹挂 webhook:</p>',
            f'<pre>{_escape(usage["webhook_curl"])}</pre>',
            '<h2>2. 让 MusicFree 客户端从这里读</h2>',
            '<p>在 MusicFree「设置 → 插件管理 → 从网络安装」填下面这个 URL:</p>',
            f'<pre>{_escape(usage["plugin_src_url"])}</pre>',
            '<p>装好后还需要在「插件设置 → 用户变量」里填:</p>',
            f'<pre>baseUrl = {_escape(usage["plugin_base_url"])}   (默认已注入, 留空也行)\n',
            f'apiKey  = <在 file_manager 用户中心生成的 API Key>',
            ('   ' + ('# 服务端已配 MFS_FILE_MANAGER_API_KEY, 此处可留空'
                     if cfg2.file_manager_api_key
                     else '# 必填, 否则点播放会 401 (除非给服务端配 MFS_FILE_MANAGER_API_KEY)')),
            '\n',
            f'token   = (可留空, 除非启用了 MFS_QUERY_TOKEN)</pre>',
            '<p class="hint" style="color:#666;font-size:.9rem">'
            'apiKey 通过 <code>X-File-Manager-Key</code> 头从插件透传给本服务, '
            '本服务再原样塞回给 MusicFree 客户端的 <code>headers.X-API-Key</code>, '
            'ExoPlayer 拉流时携带. apiKey 不会被持久化到 state.json.</p>',
            '<p>探测端点 (浏览器直接打开):</p>',
            f'<pre>{_escape(usage["info_url"])}</pre>',
            '<h2>3. 调试入口</h2>',
            f'<pre>GET  /admin/state\nPOST /admin/clear   (仅 127.0.0.1)\nPOST /webhook/test  (本地构造一条 created 事件)</pre>',
            '</body></html>',
        ]
        return ''.join(html_parts), 200, {'Content-Type': 'text/html; charset=utf-8'}

    return app


def _escape(s: str) -> str:
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _usage_hints(cfg: Config, host_url: str) -> dict:
    """统一组装 "引用方式" 文案; 在 /, /health, 启动 banner 都复用同一份。"""
    base = (cfg.public_base_url or (host_url.rstrip('/'))) or 'http://<this-host>:<port>'
    secret_part = f' \\\n    -H "X-Signature: sha256=$(...HMAC...)"' if cfg.webhook_secret else ''
    webhook_curl = (
        f'# file_manager 后台调用 (假设你的 file_manager API Key 是 FM_KEY)\n'
        f'curl -X POST "$FM_BASE/api/folder-watches" \\\n'
        f'    -H "X-API-Key: $FM_KEY" -H "Content-Type: application/json" \\\n'
        f'    -d \'{{"folder_path":"music","url":"{base}/webhook/files",\n'
        f'         "secret":"{cfg.webhook_secret or "(可选)"}",\n'
        f'         "events":["created","modified","deleted"],\n'
        f'         "include_subdirs":true,"enabled":true}}\''
        + secret_part
    )
    return {
        'plugin_base_url': base,
        'plugin_src_url': base + PLUGIN_JS_URL_PATH,
        'info_url': base + '/api/music/info',
        'webhook_url': base + '/webhook/files',
        'webhook_curl': webhook_curl,
    }


def _print_startup_banner(app: Flask, log: logging.Logger) -> None:
    cfg: Config = app.config['_mfs_config']
    store_: TrackStore = app.config['_mfs_store']
    summary = cfg.as_summary()
    usage = _usage_hints(cfg, '')
    bar = '=' * 70
    log.info('\n%s', bar)
    log.info('musicfree_server 已启动')
    log.info(bar)
    log.info('配置:')
    for k, v in summary.items():
        log.info('  %-25s = %s', k, v)
    log.info('状态: tracks=%(tracks)d sheets=%(sheets)d events=%(events_seen)d', store_.stats())
    log.info('%s', bar)
    log.info('【引用方式 1】 让 file_manager 把变化推到我:')
    for line in usage['webhook_curl'].splitlines():
        log.info('  %s', line)
    log.info('')
    log.info('【引用方式 2】 让 MusicFree 客户端把我当成音源:')
    log.info('  插件 srcUrl  : %s   (本服务直接托管, 模板里的 baseUrl 已自动注入)',
             usage['plugin_src_url'])
    log.info('  baseUrl      : %s   (装好后默认就是它, 无需再填)',
             usage['plugin_base_url'])
    if cfg.file_manager_api_key:
        log.info('  file_manager apiKey: 服务端已配 MFS_FILE_MANAGER_API_KEY, '
                 '即便客户端不填也能播放 (单租户模式)')
    else:
        log.info('  file_manager apiKey: !! 服务端没配 MFS_FILE_MANAGER_API_KEY !!')
        log.info('    → 请客户端在 MusicFree 插件「用户变量」里填 apiKey, '
                 '插件会以 X-File-Manager-Key 头传给本服务 (多租户模式)')
        log.info('    → 或者在本服务环境变量加 MFS_FILE_MANAGER_API_KEY=xxx 后重启 '
                 '(单租户兜底)')
    if cfg.music_query_token:
        log.info('  查询 token    : %s   (X-Music-Token)', '已配置, 见 MFS_QUERY_TOKEN')
    else:
        log.info('  查询 token    : (未配置, 任何人都能查清单)')
    log.info('')
    log.info('【健康检查】')
    log.info('  GET %s/health   ← 同时返回上面这些引导', usage['plugin_base_url'])
    log.info('  GET %s/         ← HTML 欢迎页', usage['plugin_base_url'])
    log.info('%s', bar)


# --------------------------------------------------------------- CLI 入口
def main() -> None:
    parser = argparse.ArgumentParser(description='musicfree_server')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8765)),
                        help='监听端口 (默认 8765 或 $PORT)')
    parser.add_argument('--host', default=os.environ.get('HOST', '127.0.0.1'),
                        help='监听地址 (默认 127.0.0.1)')
    args = parser.parse_args()

    # 防御: 父进程若是用 Flask `debug=True` 跑的 (例如 file_manager 本身),
    # Werkzeug reloader 会把 WERKZEUG_RUN_MAIN/WERKZEUG_SERVER_FD 塞进环境。
    # 那些 fd 在我们这个全新进程里是无效的, 不擦掉会让 app.run() 直接挂在
    # `socket.fromfd()` 上抛 OSError(Errno 9) Bad file descriptor.
    for _leaked in ('WERKZEUG_RUN_MAIN', 'WERKZEUG_SERVER_FD'):
        os.environ.pop(_leaked, None)

    app = create_app()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
