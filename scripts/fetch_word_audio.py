#!/usr/bin/env python3
"""
下载常用 5000 单词的美式发音并批量上传到 file_manager 的指定文件夹。

数据源
------
- 词表: google-10000-english 的 USA / no-swears 版本（按频率排序，已过滤脏话），
  在线地址::

      https://raw.githubusercontent.com/first20hours/google-10000-english/master/google-10000-english-usa-no-swears.txt

  脚本会首次拉取后缓存到 ``scripts/.cache/word_audio/wordlist.txt``，后续复用。
  也可用 ``--words-file`` 指定本地文件覆盖默认源。

- 音频: 有道词典 ``dictvoice`` 接口（无需 API key、稳定、美音）::

      https://dict.youdao.com/dictvoice?audio=<word>&type=2

  ``type=2`` 是美音, ``type=1`` 是英音。

工作流
------
1. 加载词表，取前 ``--limit`` 个词（默认 5000）
2. 并发下载 mp3 到本地缓存目录 ``scripts/.cache/word_audio/audio/<word>.mp3``,
   已存在的跳过（断点续传）
3. 用 ``/api/folder/create`` 逐级 ensure 目标文件夹（需账号密码登录拿 cookie）
4. 用 ``/api/external/folder-upload`` 分批上传（API key 鉴权）

用法
----
::

    # 只下载, 不上传（先确认下载链路）
    ./yenv/bin/python scripts/fetch_word_audio.py --download-only --limit 20

    # 一把梭: 下载 + 上传到 "单词发音/美音"
    ./yenv/bin/python scripts/fetch_word_audio.py \\
        --base-url http://localhost:5008 \\
        --username demo_uploader --password demo123 \\
        --folder "单词发音/美音" \\
        --limit 5000

    # 已有 API key 时跳过登录, 但需要确保目标文件夹已存在
    ./yenv/bin/python scripts/fetch_word_audio.py \\
        --base-url http://localhost:5008 \\
        --api-key xxxxx --folder-id 42 --limit 200

注意
----
- 有道接口对单 IP 有速率限制，默认并发 6，过高会被临时封；下载失败会自动重试 2 次。
- mp3 文件名形如 ``hello.mp3``，匹配后续 ``word_audio`` 插件的查找规则。
- 上传走 ``overwrite=1``（默认），同名时覆盖；用 ``--no-overwrite`` 改名上传。
"""
from __future__ import annotations

import argparse
import io
import os
import random
import string
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar
from typing import Iterable, List, Optional, Tuple
from urllib import parse as urlparse
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

# 让脚本能 import 项目内的其他工具（暂未使用，预留）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WORDLIST_URL = (
    'https://raw.githubusercontent.com/first20hours/google-10000-english/'
    'master/google-10000-english-usa-no-swears.txt'
)
YOUDAO_VOICE = 'https://dict.youdao.com/dictvoice?audio={word}&type=2'  # type=2: 美音

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache', 'word_audio')
WORDLIST_CACHE = os.path.join(CACHE_DIR, 'wordlist.txt')
AUDIO_CACHE_DIR = os.path.join(CACHE_DIR, 'audio')

UA = 'Mozilla/5.0 (X11; Linux x86_64) word-audio-fetcher/1.0'


# ---------------------------------------------------------------------------
# 轻量 HTTP 客户端: 复用 cookie + 简单重试
# ---------------------------------------------------------------------------

class HttpClient:
    """支持 cookie 持久化的轻量 HTTP 客户端，用于跟 file_manager 交互。"""

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.cookies = CookieJar()
        self.opener = urlrequest.build_opener(
            urlrequest.HTTPCookieProcessor(self.cookies)
        )
        self.api_key: Optional[str] = None
        self._lock = threading.Lock()  # multipart 上传时序列化（避免拼包并发出错）

    def _full(self, path: str) -> str:
        if path.startswith('http'):
            return path
        return self.base_url + path

    def request(
        self,
        path: str,
        method: str = 'GET',
        data: bytes | None = None,
        headers: Optional[dict] = None,
    ) -> Tuple[int, dict, bytes]:
        req = urlrequest.Request(self._full(path), data=data, method=method)
        req.add_header('User-Agent', UA)
        if self.api_key:
            req.add_header('X-API-Key', self.api_key)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                body = resp.read()
                return resp.getcode(), dict(resp.headers), body
        except HTTPError as e:
            body = e.read() if hasattr(e, 'read') else b''
            return e.code, dict(e.headers or {}), body

    def post_form(self, path: str, form: dict) -> Tuple[int, dict, bytes]:
        body = urlparse.urlencode(form).encode()
        return self.request(
            path, method='POST', data=body,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )

    def post_json(self, path: str, payload: dict) -> Tuple[int, dict, bytes]:
        import json
        body = json.dumps(payload).encode()
        return self.request(
            path, method='POST', data=body,
            headers={'Content-Type': 'application/json'},
        )

    def post_multipart(
        self, path: str, fields: dict, files: List[Tuple[str, str, bytes]]
    ) -> Tuple[int, dict, bytes]:
        """fields: dict of str->str; files: list of (field_name, filename, content)."""
        boundary = '----wordAudio' + ''.join(
            random.choices(string.ascii_letters + string.digits, k=16)
        )
        buf = io.BytesIO()
        for k, v in fields.items():
            buf.write(f'--{boundary}\r\n'.encode())
            buf.write(
                f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
            )
            buf.write(str(v).encode())
            buf.write(b'\r\n')
        for field, filename, content in files:
            buf.write(f'--{boundary}\r\n'.encode())
            buf.write(
                (
                    f'Content-Disposition: form-data; name="{field}"; '
                    f'filename="{filename}"\r\n'
                ).encode()
            )
            buf.write(b'Content-Type: audio/mpeg\r\n\r\n')
            buf.write(content)
            buf.write(b'\r\n')
        buf.write(f'--{boundary}--\r\n'.encode())
        return self.request(
            path, method='POST', data=buf.getvalue(),
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        )


# ---------------------------------------------------------------------------
# 词表获取
# ---------------------------------------------------------------------------

def fetch_wordlist(words_file: Optional[str], limit: int) -> List[str]:
    """读取或下载词表，返回去重后的前 ``limit`` 个词。"""
    if words_file:
        src = words_file
    else:
        os.makedirs(CACHE_DIR, exist_ok=True)
        if not os.path.exists(WORDLIST_CACHE):
            print(f'[wordlist] 缓存不存在, 从 GitHub 拉取: {WORDLIST_URL}')
            req = urlrequest.Request(WORDLIST_URL, headers={'User-Agent': UA})
            with urlrequest.urlopen(req, timeout=30) as resp:
                content = resp.read()
            with open(WORDLIST_CACHE, 'wb') as f:
                f.write(content)
        src = WORDLIST_CACHE
        print(f'[wordlist] 使用 {src}')

    seen = set()
    words = []
    with open(src, 'r', encoding='utf-8', errors='ignore') as f:
        for raw in f:
            w = raw.strip().lower()
            # 过滤空行、注释、明显非纯字母的（带连字符/数字的也跳过, 简化下载文件名规则）
            if not w or w.startswith('#') or not w.isalpha():
                continue
            if w in seen:
                continue
            seen.add(w)
            words.append(w)
            if len(words) >= limit:
                break
    return words


# ---------------------------------------------------------------------------
# 音频下载
# ---------------------------------------------------------------------------

def _audio_path(word: str) -> str:
    return os.path.join(AUDIO_CACHE_DIR, f'{word}.mp3')


def download_one(word: str, max_retries: int = 2, timeout: int = 15) -> Tuple[str, bool, str]:
    """下载一个单词的美音 mp3 到本地缓存。

    Returns: ``(word, ok, message)`` —— ok=True 表示文件已在本地（已存在或下载成功）。
    """
    dst = _audio_path(word)
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return word, True, 'cached'
    url = YOUDAO_VOICE.format(word=urlparse.quote(word))
    last_err = ''
    for attempt in range(max_retries + 1):
        try:
            req = urlrequest.Request(url, headers={'User-Agent': UA})
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                if resp.getcode() != 200:
                    last_err = f'HTTP {resp.getcode()}'
                    continue
                content = resp.read()
                # 极小内容多半是错误响应
                if len(content) < 256:
                    last_err = f'too small ({len(content)}B)'
                    continue
                # 原子写
                tmp = dst + '.part'
                with open(tmp, 'wb') as f:
                    f.write(content)
                os.replace(tmp, dst)
                return word, True, f'downloaded {len(content)}B'
        except (HTTPError, URLError, TimeoutError) as e:
            last_err = str(e)
        if attempt < max_retries:
            time.sleep(0.5 + attempt * 0.5)
    return word, False, last_err or 'unknown'


def download_all(words: List[str], concurrency: int) -> Tuple[List[str], List[str]]:
    """并发下载，返回 (succeeded_words, failed_words)。"""
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    print(
        f'[download] {len(words)} words, concurrency={concurrency}, dest={AUDIO_CACHE_DIR}'
    )
    ok: List[str] = []
    fail: List[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(download_one, w): w for w in words}
        for fut in as_completed(futures):
            word, success, msg = fut.result()
            done += 1
            if success:
                ok.append(word)
            else:
                fail.append(word)
                print(f'  [{done}/{len(words)}] FAIL {word}: {msg}')
            if done % 50 == 0 or done == len(words):
                print(
                    f'  [progress] {done}/{len(words)}  ok={len(ok)}  fail={len(fail)}'
                )
    return ok, fail


# ---------------------------------------------------------------------------
# 上传到 file_manager
# ---------------------------------------------------------------------------

def login_session(client: HttpClient, username: str, password: str) -> None:
    """通过 ``/login`` 拿 cookie；后续 /api/folder/create 等 login_required 接口靠 cookie。"""
    code, _, _ = client.post_form('/login', {'username': username, 'password': password})
    if code not in (200, 302):
        raise SystemExit(f'登录失败: HTTP {code}')
    print(f'[login] {username} 登录成功')


def fetch_api_key(client: HttpClient) -> str:
    """从 ``/api/user`` 取当前用户的 api_key。"""
    import json
    code, _, body = client.request('/api/user')
    if code != 200:
        raise SystemExit(f'拉取用户信息失败: HTTP {code} body={body[:200]!r}')
    j = json.loads(body)
    # 兼容两种字段命名
    api_key = (j.get('user') or j).get('api_key') or j.get('api_key')
    if not api_key:
        raise SystemExit(f'返回里没有 api_key: {j}')
    print(f'[api-key] 已获取: {api_key[:8]}***')
    return api_key


def ensure_folder(client: HttpClient, folder_path: str) -> dict:
    """逐级 ensure 目标文件夹。``/api/folder/create`` 是幂等的：已存在则直接返回。"""
    import json
    parts = [p for p in folder_path.strip('/').split('/') if p]
    if not parts:
        raise SystemExit('folder 路径不能为空')
    parent = ''
    last = None
    for seg in parts:
        code, _, body = client.post_json(
            '/api/folder/create', {'name': seg, 'path': parent}
        )
        if code != 200:
            raise SystemExit(f'创建/确认文件夹失败 segment={seg}: HTTP {code} body={body[:200]!r}')
        j = json.loads(body)
        if not j.get('success'):
            raise SystemExit(f'创建/确认文件夹失败 segment={seg}: {j}')
        last = j['folder']
        parent = last['path']
    print(f"[folder] 已 ensure -> id={last['id']} path={last['path']!r}")
    return last


def upload_batch(
    client: HttpClient,
    folder_id: int,
    words: Iterable[str],
    overwrite: bool,
) -> Tuple[int, int]:
    """把 ``words`` 对应的本地 mp3 一次性 multipart 上传，返回 (ok_count, fail_count)。"""
    import json
    files = []
    for w in words:
        p = _audio_path(w)
        if not os.path.exists(p):
            continue
        with open(p, 'rb') as f:
            files.append(('files[]', f'{w}.mp3', f.read()))
    if not files:
        return 0, 0
    fields = {
        'folder_id': str(folder_id),
        'overwrite': '1' if overwrite else '0',
    }
    code, _, body = client.post_multipart('/api/external/folder-upload', fields, files)
    if code != 200:
        print(f'  [upload] HTTP {code} body={body[:300]!r}')
        return 0, len(files)
    j = json.loads(body)
    ok = len(j.get('files') or [])
    failed = j.get('failed') or []
    if failed:
        for item in failed[:5]:
            print(f'  [upload] failed: {item}')
        if len(failed) > 5:
            print(f'  [upload] ... 还有 {len(failed) - 5} 条 failed 未展示')
    return ok, len(failed)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description='下载常用 5000 单词美音并上传到 file_manager',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # 词源
    p.add_argument('--words-file', help='本地词表 txt (每行一个词); 不传则用 google top5000')
    p.add_argument('--limit', type=int, default=5000, help='取前 N 个词 (默认 5000)')
    # 下载
    p.add_argument('--concurrency', type=int, default=6, help='下载并发 (默认 6)')
    p.add_argument(
        '--download-only',
        action='store_true',
        help='只下载到本地缓存, 不上传',
    )
    # 上传相关
    p.add_argument('--base-url', default='http://localhost:5008', help='file_manager 服务地址')
    p.add_argument('--username', help='账号 (用于登录 + ensure 文件夹)')
    p.add_argument('--password', help='密码')
    p.add_argument('--api-key', help='API key (跳过登录, 但需 --folder-id)')
    p.add_argument(
        '--folder', help='目标文件夹相对路径 (如 "单词发音/美音"); 自动逐级 ensure'
    )
    p.add_argument(
        '--folder-id', type=int, help='直接指定目标文件夹 id (跳过 ensure)'
    )
    p.add_argument(
        '--batch-size', type=int, default=50, help='上传分批大小 (默认 50)'
    )
    p.add_argument(
        '--no-overwrite',
        action='store_true',
        help='同名时改名 (默认覆盖)',
    )
    args = p.parse_args()

    # ---------- 词表 ----------
    words = fetch_wordlist(args.words_file, args.limit)
    print(f'[wordlist] 取到 {len(words)} 个词, 前 5: {words[:5]}')

    # ---------- 下载 ----------
    ok_words, fail_words = download_all(words, concurrency=args.concurrency)
    print(
        f'[download] 完成: ok={len(ok_words)} fail={len(fail_words)} '
        f'缓存目录={AUDIO_CACHE_DIR}'
    )
    if fail_words:
        print(f'[download] 失败样本: {fail_words[:10]}')

    if args.download_only:
        print('--download-only 模式, 不上传, 退出')
        return 0

    if not ok_words:
        print('!! 没有任何成功下载的音频, 跳过上传')
        return 1

    # ---------- 上传准备 ----------
    if not args.api_key and not (args.username and args.password):
        print(
            '!! 上传需要 --api-key（跳过登录, 配合 --folder-id），'
            '或 --username + --password（用于登录 + ensure 文件夹）'
        )
        return 2

    client = HttpClient(args.base_url)
    if args.username and args.password:
        login_session(client, args.username, args.password)
        client.api_key = args.api_key or fetch_api_key(client)
    else:
        client.api_key = args.api_key

    if args.folder_id:
        folder_id = args.folder_id
        print(f'[folder] 使用指定 folder_id={folder_id}')
    elif args.folder:
        if not (args.username and args.password):
            print('!! 用 --folder 自动 ensure 时必须提供 --username/--password (需要 session)')
            return 2
        folder = ensure_folder(client, args.folder)
        folder_id = folder['id']
    else:
        print('!! 必须指定 --folder 或 --folder-id')
        return 2

    # ---------- 分批上传 ----------
    overwrite = not args.no_overwrite
    total_ok = total_fail = 0
    batches = [ok_words[i:i + args.batch_size] for i in range(0, len(ok_words), args.batch_size)]
    print(f'[upload] {len(ok_words)} 文件 -> {len(batches)} 个批次, overwrite={overwrite}')
    for idx, batch in enumerate(batches, 1):
        t0 = time.time()
        ok_n, fail_n = upload_batch(client, folder_id, batch, overwrite)
        total_ok += ok_n
        total_fail += fail_n
        print(
            f'  [batch {idx}/{len(batches)}] words={len(batch)} '
            f'ok={ok_n} fail={fail_n} elapsed={time.time() - t0:.2f}s'
        )

    print()
    print('== Summary ==')
    print(f'  词表数:        {len(words)}')
    print(f'  下载成功:      {len(ok_words)}')
    print(f'  下载失败:      {len(fail_words)}')
    print(f'  上传成功:      {total_ok}')
    print(f'  上传失败:      {total_fail}')
    print(f'  目标 folder_id: {folder_id}')
    print(f'  缓存目录:      {AUDIO_CACHE_DIR}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
