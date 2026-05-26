"""musicfree_server in-process 端到端测试。

覆盖:
  1. webhook 收到 file_manager payload → 清单更新
  2. /api/music/sheets / sheet / search / track/source / lyric 都返回符合 MusicFree 协议的数据
  3. 删除事件 → 从清单移除
  4. HMAC 验签 (开/关)
  5. .lrc 同名配对
  6. Query token 鉴权
  7. 启动 banner 含 "引用方式"

跑法:
    cd servers/musicfree_server
    python -m tests.test_app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest

# 把本目录上一层加入 sys.path, 方便 from app import / from store import
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from app import create_app  # noqa: E402
from config import Config  # noqa: E402
from store import TrackStore  # noqa: E402


def _make_cfg(tmpdir: str, **overrides) -> Config:
    cfg = Config()
    cfg.public_base_url = 'http://test.local:8765'
    cfg.state_path = os.path.join(tmpdir, 'state.json')
    cfg.webhook_secret = ''
    cfg.music_query_token = ''
    cfg.log_level = 'WARNING'
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _payload(event='created', file_id=101, name='hello.mp3', parent_id=200,
             folder_name='word_audio', size=2048, **extra):
    p = {
        'event': event,
        'timestamp': '2026-05-23T20:30:00.000Z',
        'watch': {
            'id': 1, 'folder_id': parent_id, 'folder_name': folder_name,
            'folder_path': folder_name, 'include_subdirs': True,
        },
        'file': {
            'id': file_id, 'name': name,
            'path': f'{folder_name}/{name}',
            'size': size, 'file_type': 'audio',
            'is_dir': False, 'user_id': 1, 'parent_id': parent_id,
            'modified_at': '2026-05-23T20:29:59',
            'download_url': f'http://fm.local/api/download/{file_id}',
            'serve_url': f'http://fm.local/api/serve/{file_id}',
        },
    }
    p['file'].update(extra)
    return p


class WebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = _make_cfg(self.tmp.name)
        self.store = TrackStore(self.cfg.state_path)
        self.app = create_app(self.cfg, self.store)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _post_webhook(self, payload, secret=None):
        body = json.dumps(payload).encode()
        headers = {'Content-Type': 'application/json'}
        if secret:
            sig = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers['X-Signature'] = sig
        return self.client.post('/webhook/files', data=body, headers=headers)

    # ----------------------------------------------------------- 1) 基本流转
    def test_created_then_search_then_source(self):
        # 加 3 首
        for fid, name in [(101, 'hello.mp3'), (102, 'world.mp3'), (103, 'demo.mp3')]:
            r = self._post_webhook(_payload(file_id=fid, name=name))
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(r.get_json()['action'], 'added')

        # sheets: 应该有 1 个 folder, 3 首
        r = self.client.get('/api/music/sheets')
        j = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(j['data']), 1)
        self.assertEqual(j['data'][0]['worksNum'], 3)
        self.assertEqual(j['data'][0]['title'], 'word_audio')

        # sheet detail
        sheet_id = j['data'][0]['id']
        r = self.client.get(f'/api/music/sheet/{sheet_id}')
        j2 = r.get_json()
        self.assertEqual(len(j2['musicList']), 3)
        titles = sorted(m['title'] for m in j2['musicList'])
        self.assertEqual(titles, ['demo', 'hello', 'world'])
        self.assertEqual(j2['musicList'][0]['platform'].startswith('FileManager'), True)
        # _file_id / _folder_id 透传字段必须存在 (MusicFree 客户端会原样保留)
        self.assertIn('_file_id', j2['musicList'][0])

        # search music
        r = self.client.get('/api/music/search?q=hello&type=music')
        self.assertEqual(r.status_code, 200)
        j3 = r.get_json()
        self.assertEqual(len(j3['data']), 1)
        self.assertEqual(j3['data'][0]['title'], 'hello')

        # search sheet
        r = self.client.get('/api/music/search?q=word&type=sheet')
        self.assertEqual(len(r.get_json()['data']), 1)

        # track/source: 老格式 webhook (只有 serve_url/download_url) 兜底替换为
        # /api/external/download/<id>, 因为 /api/serve/<id> 走 @login_required,
        # MusicFree 客户端没 session cookie 一定 401.
        r = self.client.get('/api/music/track/101/source')
        j4 = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(j4['url'], 'http://fm.local/api/external/download/101')
        self.assertEqual(j4['headers'], {})  # 客户端没传 key, 服务端也没配
        self.assertEqual(j4.get('_key_source'), 'none')
        # 没配 api_key 时应当带个 _warning 提示, 方便排错
        self.assertIsNotNone(j4.get('_warning'))
        self.assertIn('apiKey', j4['_warning'])

        # 客户端通过 X-File-Manager-Key 传 key, 即使服务端没配也应注入并清掉 _warning
        r = self.client.get('/api/music/track/101/source',
                            headers={'X-File-Manager-Key': 'CLIENT_KEY_aaa'})
        j4a = r.get_json()
        self.assertEqual(j4a['headers']['X-API-Key'], 'CLIENT_KEY_aaa')
        self.assertEqual(j4a.get('_key_source'), 'client')
        self.assertIsNone(j4a.get('_warning'))

        # 服务端配了 key 但客户端没传, 用服务端兜底
        self.cfg.file_manager_api_key = 'FM_KEY_xxx'
        r = self.client.get('/api/music/track/101/source')
        j4b = r.get_json()
        self.assertEqual(j4b['headers']['X-API-Key'], 'FM_KEY_xxx')
        self.assertEqual(j4b.get('_key_source'), 'server')
        self.assertIsNone(j4b.get('_warning'))

        # 两边都有时, 客户端 > 服务端 (多租户场景, 用户在他自己设备上覆盖)
        r = self.client.get('/api/music/track/101/source',
                            headers={'X-File-Manager-Key': 'CLIENT_KEY_bbb'})
        j4c = r.get_json()
        self.assertEqual(j4c['headers']['X-API-Key'], 'CLIENT_KEY_bbb')
        self.assertEqual(j4c.get('_key_source'), 'client')

        # 也支持 ?fm_key=... query 参数 (curl 调试用), 优先级在 header 之下
        self.cfg.file_manager_api_key = ''  # 关掉服务端配置
        r = self.client.get('/api/music/track/101/source?fm_key=QUERY_KEY_ccc')
        j4d = r.get_json()
        self.assertEqual(j4d['headers']['X-API-Key'], 'QUERY_KEY_ccc')
        self.assertEqual(j4d.get('_key_source'), 'query')
        # header 优先级 > query
        r = self.client.get('/api/music/track/101/source?fm_key=QUERY_KEY_ccc',
                            headers={'X-File-Manager-Key': 'HEADER_WINS'})
        self.assertEqual(r.get_json()['headers']['X-API-Key'], 'HEADER_WINS')
        self.assertEqual(r.get_json().get('_key_source'), 'client')

        # info 端点 (stats 应一致)
        r = self.client.get('/api/music/info')
        j5 = r.get_json()
        self.assertEqual(j5['stats']['tracks'], 3)
        self.assertEqual(j5['stats']['sheets'], 1)

    # ----------------------------------------------------------- 2) 删除事件
    def test_deleted_removes_track(self):
        self._post_webhook(_payload(file_id=101, name='hello.mp3'))
        self._post_webhook(_payload(file_id=102, name='world.mp3'))
        self.assertEqual(self.store.stats()['tracks'], 2)
        r = self._post_webhook(_payload(event='deleted', file_id=101, name='hello.mp3'))
        self.assertEqual(r.get_json()['action'], 'removed')
        self.assertEqual(self.store.stats()['tracks'], 1)
        # 已删的 track/source 应 404
        r = self.client.get('/api/music/track/101/source')
        self.assertEqual(r.status_code, 404)

    # ----------------------------------------------------------- 3) modified 是 upsert
    def test_modified_upserts(self):
        self._post_webhook(_payload(file_id=101, name='hello.mp3', size=100))
        r = self._post_webhook(_payload(event='modified', file_id=101, name='hello.mp3', size=300))
        self.assertEqual(r.get_json()['action'], 'updated')
        t = self.store.get_track(101)
        self.assertEqual(t.size, 300)

    # ----------------------------------------------------------- 4) 非 audio 被忽略
    def test_non_audio_ignored(self):
        p = _payload(file_id=999, name='note.txt')
        p['file']['file_type'] = 'documents'
        r = self._post_webhook(p)
        self.assertEqual(r.get_json()['action'], 'ignored')
        self.assertEqual(self.store.stats()['tracks'], 0)

    # ----------------------------------------------------------- 5) lrc 配对
    def test_lrc_pairs_with_audio(self):
        self._post_webhook(_payload(file_id=101, name='hello.mp3'))
        # 同目录 .lrc
        lrc_payload = _payload(file_id=102, name='hello.lrc')
        lrc_payload['file']['file_type'] = 'documents'  # .lrc 不是 audio
        lrc_payload['file']['download_url'] = 'http://fm.local/api/download/102'
        r = self._post_webhook(lrc_payload)
        self.assertEqual(r.get_json()['action'], 'updated')
        t = self.store.get_track(101)
        self.assertEqual(t.lyric, 'http://fm.local/api/download/102')

    # ----------------------------------------------------------- 6) 签名校验
    def test_hmac_signature(self):
        self.cfg.webhook_secret = 'topsecret'
        payload = _payload(file_id=201, name='auth.mp3')
        # 缺签名 → 401
        body = json.dumps(payload).encode()
        r = self.client.post('/webhook/files', data=body, content_type='application/json')
        self.assertEqual(r.status_code, 401)
        # 错签名 → 401
        r = self.client.post('/webhook/files', data=body, content_type='application/json',
                             headers={'X-Signature': 'sha256=bad'})
        self.assertEqual(r.status_code, 401)
        # 正签名 → 200
        r = self._post_webhook(payload, secret='topsecret')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['action'], 'added')

    # ----------------------------------------------------------- 7) query token
    def test_query_token(self):
        self.cfg.music_query_token = 'mytoken'
        self._post_webhook(_payload(file_id=101, name='hello.mp3'))
        # 无 token → 401
        r = self.client.get('/api/music/sheets')
        self.assertEqual(r.status_code, 401)
        # 错 token → 401
        r = self.client.get('/api/music/sheets', headers={'X-Music-Token': 'wrong'})
        self.assertEqual(r.status_code, 401)
        # 正 token (header) → 200
        r = self.client.get('/api/music/sheets', headers={'X-Music-Token': 'mytoken'})
        self.assertEqual(r.status_code, 200)
        # 正 token (query) → 200
        r = self.client.get('/api/music/sheets?token=mytoken')
        self.assertEqual(r.status_code, 200)
        # /info 不要求 token (即使配了)
        r = self.client.get('/api/music/info')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['auth_required'])

    # ----------------------------------------------------------- 8) URL 改写
    def test_rewrite_url(self):
        self.cfg.rewrite_url_from = 'http://fm.local'
        self.cfg.rewrite_url_to = 'https://public.example.com'
        self._post_webhook(_payload(file_id=101, name='hello.mp3'))
        r = self.client.get('/api/music/track/101/source')
        url = r.get_json()['url']
        self.assertTrue(url.startswith('https://public.example.com/'), url)
        self.assertNotIn('fm.local', url)
        # 即便经过 rewrite, 路径段也必须已经被替换成 external_download
        self.assertIn('/api/external/download/101', url)

    # ----------------------------------------------------------- 8b) 新 webhook 字段优先
    def test_external_download_url_takes_priority(self):
        """新版 file_manager webhook 会直接带 external_download_url; 这种情况
        musicfree_server 应该原样透传, 而不是再用路径替换规则去推 download_url。"""
        p = _payload(file_id=303, name='vip.mp3')
        p['file']['external_download_url'] = 'http://fm.local/api/external/download/303'
        # 同时给一个"形态怪异"的 download_url, 验证不会被错误地推导
        p['file']['download_url'] = 'http://fm.local/api/whatever/303'
        self._post_webhook(p)
        r = self.client.get('/api/music/track/303/source')
        self.assertEqual(r.get_json()['url'], 'http://fm.local/api/external/download/303')

    # ----------------------------------------------------------- 8c) 媒体列表也已替换
    def test_media_item_download_url_is_streamable(self):
        """sheet 详情里的 _download_url 也必须是客户端能直接拉的 URL,
        否则有些 MusicFree 客户端可能跳过 getMediaSource, 直接拿这个去播。"""
        self._post_webhook(_payload(file_id=101, name='hello.mp3'))
        # 先拿到 sheet id
        sheets = self.client.get('/api/music/sheets').get_json()['data']
        sid = sheets[0]['id']
        items = self.client.get(f'/api/music/sheet/{sid}').get_json()['musicList']
        self.assertEqual(items[0]['_download_url'],
                         'http://fm.local/api/external/download/101')

    # ----------------------------------------------------------- 9) /health 含引用方式
    def test_health_includes_usage(self):
        r = self.client.get('/health')
        j = r.get_json()
        self.assertTrue(j['ok'])
        self.assertIn('usage', j)
        self.assertIn('webhook_curl', j['usage'])
        # webhook_curl 中应当包含本服务的 URL
        self.assertIn('/webhook/files', j['usage']['webhook_curl'])
        # config 摘要应脱敏
        self.assertIn('public_base_url', j['config'])

    # ----------------------------------------------------------- 10) HTML 欢迎页
    def test_index_html(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/html', r.content_type)
        body = r.data.decode()
        self.assertIn('musicfree_server', body)
        self.assertIn('baseUrl', body)
        self.assertIn('/webhook/files', body)
        # 应当告诉用户怎么"从网络安装"插件
        self.assertIn('/static/musicfree/file-manager.js', body)

    # ----------------------------------------------------------- 10b) 插件 JS
    def test_plugin_js_served_with_base_url_injected(self):
        r = self.client.get('/static/musicfree/file-manager.js')
        self.assertEqual(r.status_code, 200)
        self.assertIn('javascript', r.content_type)
        body = r.data.decode()
        # 占位符必须被替换掉, 否则客户端拿到的是一份残缺模板
        self.assertNotIn('__MFS_PUBLIC_BASE_URL__', body)
        self.assertNotIn('__MFS_SRC_URL__', body)
        # public_base_url (test.local:8765) 必须被注入到 JS 里, 让客户端开箱即用
        self.assertIn('http://test.local:8765', body)
        # 确认是一份"针对 musicfree_server"的插件: 实际请求 headers 里塞的是
        # X-Music-Token, 而不是 file_manager 那一份直连版本的 X-API-Key。
        # (只校验代码层的 headers[...] 用法, 不被注释里的字面量干扰)
        self.assertIn('headers["X-Music-Token"]', body)
        self.assertNotIn('headers["X-API-Key"]', body)
        # 关键插件字段都在
        self.assertIn('srcUrl', body)
        self.assertIn('userVariables', body)

    # ----------------------------------------------------------- 11) 持久化
    def test_persistence_across_restart(self):
        self._post_webhook(_payload(file_id=101, name='hello.mp3'))
        self._post_webhook(_payload(file_id=102, name='world.mp3'))
        self.store.flush(force=True)
        # 重新加载 store
        store2 = TrackStore(self.cfg.state_path)
        self.assertEqual(store2.stats()['tracks'], 2)
        self.assertEqual(store2.get_track(101).name, 'hello.mp3')

    # ----------------------------------------------------------- 12) /webhook/test 调试
    def test_webhook_test_route(self):
        r = self.client.post('/webhook/test')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['action'], 'added')

    # ----------------------------------------------------------- 13) admin/clear 仅本机
    def test_admin_clear_local_only(self):
        self._post_webhook(_payload(file_id=101, name='hello.mp3'))
        # test_client 默认 remote_addr=127.0.0.1, 应允许
        r = self.client.post('/admin/clear')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.stats()['tracks'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
