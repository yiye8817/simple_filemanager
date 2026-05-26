#!/usr/bin/env python3
"""往一个文件夹里上传文件 + 实时验证 FolderWatch webhook 的端到端 Demo。

完整流程：

1. 用 ``X-API-Key`` 鉴权调 ``GET /api/external/files`` 找到（或创建）目标文件夹；
2. 在脚本本地起一个简单的 HTTP receiver，作为 webhook 业务接收端；
3. 用浏览器会话登录 file_manager（仅为了调 ``POST /api/folder-watches``，因为
   ``folder-watches`` 接口走 ``@login_required`` Session 而不是 API Key）；
4. 在目标文件夹上注册一个 watch 指向本地 receiver；
5. 用 ``POST /api/external/folder-upload`` 上传文件到该文件夹；
6. 等待 receiver 收到 webhook 回调，打印 payload 与 HMAC 签名校验结果。

用法::

    ./yenv/bin/python scripts/demo_folder_upload.py \
        --base-url http://127.0.0.1:5000 \
        --user youruser --password yourpwd \
        --folder-path my-watched-dir \
        --files /tmp/a.txt /tmp/b.png

如果本机没装 ``requests``，脚本会回退到标准库 ``urllib`` 与手写 multipart 编码。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import uuid
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPCookieProcessor,
    HTTPHandler,
    HTTPSHandler,
    Request,
    build_opener,
)


# ---------------------------------------------------------------------------
# HTTP 工具：用 stdlib 即可，避免要求 requests 依赖
# ---------------------------------------------------------------------------

class HttpClient:
    """轻量 HTTP 客户端：保持 cookie 用于 session 登录 + 可单独传 X-API-Key。"""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.cookie_jar = CookieJar()
        self.opener = build_opener(
            HTTPHandler(),
            HTTPSHandler(),
            HTTPCookieProcessor(self.cookie_jar),
        )

    def _abs(self, path: str) -> str:
        return path if path.startswith('http') else f'{self.base_url}{path}'

    def request(self, method: str, path: str, *,
                json_body: Any = None,
                data: bytes | None = None,
                content_type: str | None = None,
                api_key: str | None = None,
                expect_json: bool = True) -> Any:
        url = self._abs(path)
        headers: dict[str, str] = {'Accept': 'application/json'}
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        elif data is not None:
            body = data
            if content_type:
                headers['Content-Type'] = content_type
        if api_key:
            headers['X-API-Key'] = api_key
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not expect_json:
                    return raw
                if not raw:
                    return None
                try:
                    return json.loads(raw.decode('utf-8'))
                except json.JSONDecodeError:
                    return {'_raw': raw.decode('utf-8', errors='replace')}
        except HTTPError as exc:
            body_text = ''
            try:
                body_text = exc.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            raise SystemExit(f'HTTP {exc.code} on {method} {url}: {body_text or exc.reason}') from None
        except URLError as exc:
            raise SystemExit(f'网络错误 on {method} {url}: {exc.reason}') from None

    def get(self, path, **kw):  return self.request('GET', path, **kw)
    def post(self, path, **kw): return self.request('POST', path, **kw)
    def put(self, path, **kw):  return self.request('PUT', path, **kw)


def encode_multipart_form(fields: dict[str, str], files: list[tuple[str, str, bytes]]):
    """很小的 multipart/form-data 编码器；返回 (content_type, body_bytes)。

    files 元素是 ``(field_name, filename, content_bytes)``。
    """
    boundary = '----DemoBoundary' + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in (fields or {}).items():
        parts.append(f'--{boundary}\r\n'.encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        parts.append(str(value).encode('utf-8') + b'\r\n')
    for field_name, filename, content in files:
        ct = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        parts.append(f'--{boundary}\r\n'.encode())
        parts.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
        )
        parts.append(f'Content-Type: {ct}\r\n\r\n'.encode())
        parts.append(content)
        parts.append(b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    body = b''.join(parts)
    return f'multipart/form-data; boundary={boundary}', body


# ---------------------------------------------------------------------------
# Webhook 接收器
# ---------------------------------------------------------------------------

class WebhookCollector:
    """把所有 POST 请求体收下来，供主流程比对。"""

    def __init__(self):
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def add(self, headers: dict, body_bytes: bytes):
        try:
            payload = json.loads(body_bytes.decode('utf-8'))
        except Exception:
            payload = {'_raw': body_bytes.decode('utf-8', errors='replace')}
        with self.lock:
            self.events.append({
                'headers': headers,
                'payload': payload,
                'raw': body_bytes,
            })

    def wait_for(self, expected_files: set[str], timeout: float = 8.0) -> list[dict]:
        """阻塞等，直到所有 expected_files 都在 payload 里出现过 created/modified 事件。"""
        deadline = time.time() + timeout
        seen_names: set[str] = set()
        while time.time() < deadline:
            with self.lock:
                events_now = list(self.events)
            for ev in events_now:
                p = ev['payload']
                f = (p.get('file') or {}) if isinstance(p, dict) else {}
                if (p.get('event') in ('created', 'modified')) and f.get('name'):
                    seen_names.add(f['name'])
            if expected_files.issubset(seen_names):
                return events_now
            time.sleep(0.15)
        with self.lock:
            return list(self.events)


def start_webhook_server(host: str, collector: WebhookCollector) -> tuple[HTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):  # 静默
            pass

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length) if length else b''
            collector.add(dict(self.headers.items()), body)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    srv = HTTPServer((host, 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def detect_local_ip(target_host: str) -> str:
    """探测本机对 target_host 的"出口 IP"，避免给远端配 127.0.0.1。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target_host, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


# ---------------------------------------------------------------------------
# 业务步骤封装
# ---------------------------------------------------------------------------

def login(client: HttpClient, user: str, password: str) -> dict:
    """走 /login（HTML form POST，依赖 Cookie）。"""
    body = urlencode({'username': user, 'password': password}).encode()
    return client.request(
        'POST', '/login',
        data=body, content_type='application/x-www-form-urlencoded',
        expect_json=False,
    )


def fetch_api_key(client: HttpClient) -> str:
    """通过 session 取当前用户的 api_key；首次访问会自动生成。"""
    j = client.get('/api/user')
    if isinstance(j, dict) and j.get('api_key'):
        return j['api_key']
    raise SystemExit('未能获取当前用户的 API Key，请先登录或检查 /api/user 接口')


def ensure_folder(client: HttpClient, folder_path: str) -> dict:
    """逐级 ensure 目标文件夹。``/api/folder/create`` 是幂等的：已存在则直接返回。"""
    parts = [p for p in folder_path.strip('/').split('/') if p]
    if not parts:
        raise SystemExit('folder_path 不能为空，请指定要监听的相对路径')
    parent = ''
    last = None
    for seg in parts:
        j = client.post('/api/folder/create', json_body={'name': seg, 'path': parent})
        if not isinstance(j, dict) or not j.get('success'):
            raise SystemExit(f'创建/确认文件夹失败 segment={seg}: {j}')
        last = j['folder']
        parent = (parent + '/' + seg).strip('/')
    return last


def register_watch(client: HttpClient, folder_id: int, hook_url: str, secret: str) -> dict:
    payload = {
        'folder_id': folder_id,
        'url': hook_url,
        'secret': secret,
        'events': ['created', 'modified', 'deleted'],
        'include_subdirs': True,
        'enabled': True,
    }
    j = client.post('/api/folder-watches', json_body=payload)
    if not isinstance(j, dict) or not j.get('success'):
        raise SystemExit(f'注册 watch 失败: {j}')
    return j['watch']


def upload_files(client: HttpClient, api_key: str, folder_id: int,
                 paths: list[str], overwrite: bool = True) -> dict:
    files = []
    for p in paths:
        if not os.path.isfile(p):
            raise SystemExit(f'文件不存在: {p}')
        with open(p, 'rb') as f:
            files.append(('files[]', os.path.basename(p), f.read()))
    fields = {'folder_id': str(folder_id), 'overwrite': '1' if overwrite else '0'}
    content_type, body = encode_multipart_form(fields, files)
    return client.post(
        '/api/external/folder-upload',
        data=body, content_type=content_type, api_key=api_key,
    )


def verify_signature(secret: str, raw_body: bytes, signature_header: str | None) -> str:
    if not secret:
        return '无 secret，跳过签名校验'
    if not signature_header:
        return '响应缺少 X-Signature 头'
    expected = 'sha256=' + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return ('签名匹配 ✓' if hmac.compare_digest(expected, signature_header)
            else f'签名不匹配（expected={expected}, got={signature_header}）')


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base-url', default='http://127.0.0.1:5000', help='file_manager 服务地址')
    ap.add_argument('--user', required=True, help='登录用户名')
    ap.add_argument('--password', required=True, help='登录密码')
    ap.add_argument('--folder-path', default='demo-watched-folder',
                    help='要监听的文件夹相对路径（自动 ensure 创建）')
    ap.add_argument('--files', nargs='+', required=True, help='本地要上传的文件路径列表')
    ap.add_argument('--secret', default='demo-secret-' + uuid.uuid4().hex[:8],
                    help='HMAC secret；用于在 Demo 中校验签名')
    ap.add_argument('--hook-host', default='', help='webhook 接收器对外可达的主机名/IP；不传则自动探测')
    ap.add_argument('--keep-watch', action='store_true',
                    help='不在 Demo 结束时清理 watch 记录（默认会清理）')
    args = ap.parse_args()

    client = HttpClient(args.base_url)
    print(f'[1/6] 登录 {args.user}@{args.base_url} ...', flush=True)
    login(client, args.user, args.password)

    print('[2/6] 取 API Key ...', flush=True)
    api_key = fetch_api_key(client)
    print(f'      api_key = {api_key[:6]}...{api_key[-4:]} (len={len(api_key)})')

    print(f'[3/6] 确认目标文件夹 path={args.folder_path!r} ...', flush=True)
    folder = ensure_folder(client, args.folder_path)
    print(f"      folder_id = {folder['id']}, path = {folder.get('path')}")

    print('[4/6] 启动本地 webhook 接收器 ...', flush=True)
    collector = WebhookCollector()
    srv, hook_port = start_webhook_server('0.0.0.0', collector)
    target_host = args.hook_host or detect_local_ip(_host_only(args.base_url))
    hook_url = f'http://{target_host}:{hook_port}/hook'
    print(f'      hook_url = {hook_url}')

    print('[5/6] 注册 watch 并上传文件 ...', flush=True)
    watch = register_watch(client, folder['id'], hook_url, args.secret)
    print(f"      watch_id = {watch['id']}, events = {watch['events']}")

    try:
        result = upload_files(client, api_key, folder['id'], args.files, overwrite=True)
        print('      upload response:')
        print('      ' + json.dumps(result, ensure_ascii=False, indent=2).replace('\n', '\n      '))
        if result.get('failed'):
            print(f"      [warn] 有 {len(result['failed'])} 个文件失败")

        expected_names = {os.path.basename(p) for p in args.files}
        print(f'[6/6] 等待 webhook 回调 ({len(expected_names)} 个文件) ...', flush=True)
        events = collector.wait_for(expected_names, timeout=10.0)
        if not events:
            print('      [error] 未收到 webhook，请检查 hook_host / 防火墙；')
            print(f'              本地 receiver: {hook_url}')
            print(f'              如服务跑在容器/其它主机，确保它能反过来访问到 {target_host}:{hook_port}')
            return 1
        for i, ev in enumerate(events, 1):
            sig = ev['headers'].get('X-Signature') or ev['headers'].get('x-signature')
            verdict = verify_signature(args.secret, ev['raw'], sig)
            p = ev['payload']
            f = (p.get('file') or {}) if isinstance(p, dict) else {}
            print(f'      [{i}] event={p.get("event")} file={f.get("name")} '
                  f'download_url={f.get("download_url")}')
            print(f'           signature={sig}')
            print(f'           verify  = {verdict}')
        print('\nDONE. webhook 闭环验证完成。')
    finally:
        if not args.keep_watch:
            try:
                client.request('DELETE', f"/api/folder-watches/{watch['id']}")
                print(f"\n[cleanup] 已删除 watch #{watch['id']}")
            except SystemExit as exc:
                print(f'\n[cleanup] 删除 watch 失败: {exc}')
        srv.shutdown()
    return 0


def _host_only(base_url: str) -> str:
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or '127.0.0.1'
        return host
    except Exception:
        return '127.0.0.1'


if __name__ == '__main__':
    raise SystemExit(main())
