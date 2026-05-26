#!/usr/bin/env python3
"""
下载常用 5000 英文单词的「美式真人发音」并上传到 file_manager 指定文件夹。

设计原则
========
- **不使用 TTS 合成** —— 按要求只采集真人录音
- 多源串联（``--prefer`` 指定顺序，缺省 ``dict-api,oxford``）：

  1. **Free Dictionary API**（dictionaryapi.dev）—— 返回的 ``phonetics[].audio``
     字段里带 ``-us.mp3`` 后缀是美式真人录音, CC BY-SA 4.0；快、稳，但 5000
     高频词里约 20% 的核心词（to/for/is/with/be/are/your/was 等）没有 US 录音
  2. **Oxford Learner's Dictionaries** —— 抓取词条页 HTML 里 ``pron-us`` 标签
     下的 ``data-src-mp3``；几乎覆盖所有英语常用词。该网站速率敏感，脚本默认
     每词请求间 ~1.2s，不要在多机并发跑
  3. **Wikimedia Commons**（仅在 ``--use-wikimedia`` 时启用）—— 命名约定
     ``En-us-<word>.ogg``，需要 ``ffmpeg`` 转 mp3

- **每源独立缓存 miss**：``_misses_<source>.txt`` 单独存。某词只在 dict-api
  miss 但下次仍会走 Oxford。已下载的 ``<word>.mp3`` 任意来源都不会被重下
- **上传**：通过 ``/api/external/folder-upload`` 批量上传，自动通过
  ``/api/external/folder`` 幂等创建目标文件夹
- **客户端去重**：上传前调 ``/api/external/files/<folder_path>`` 列出远端已
  有文件名, 过滤掉本地同名 mp3 —— 重复跑脚本时只补传新增的, 不会浪费带宽。
  ``--overwrite`` 时跳过去重并覆盖服务端; 列远端失败时降级为全量上传 (日志告警)

用法
====
::

    export FILEMANAGER_API_KEY=xxxxxxxxxxxx
    export FILEMANAGER_BASE_URL=http://localhost:5000

    # 默认: dict-api 先, oxford 兜底, 50 词冒烟
    ./yenv/bin/python scripts/seed_word_audio.py --limit 50

    # 仅用 Oxford（更慢但最全）
    ./yenv/bin/python scripts/seed_word_audio.py --limit 5000 --prefer oxford

    # 续跑 (cache 命中跳过, miss 的依次试每个源)
    ./yenv/bin/python scripts/seed_word_audio.py --limit 5000

    # 重新尝试所有历史 miss
    ./yenv/bin/python scripts/seed_word_audio.py --limit 5000 --retry-misses

    # 只下载 / 只上传
    ./yenv/bin/python scripts/seed_word_audio.py --skip-upload --limit 5000
    ./yenv/bin/python scripts/seed_word_audio.py --skip-download

参数
====
- ``--wordlist PATH``       一行一词的 txt；默认 google-10000-english 前 N
- ``--limit N``             最多处理多少词，默认 5000
- ``--prefer LIST``         来源优先级, 逗号分隔, 默认 ``dict-api,oxford``
                            可选值: ``dict-api`` / ``oxford`` / ``wikimedia``
- ``--folder-name NAME``    目标文件夹, 默认 "英语单词美音"
- ``--folder-id ID``        覆盖 ``--folder-name``
- ``--cache-dir DIR``       本地缓存目录, 默认 ``data/word_audio_cache``
- ``--use-wikimedia``       兼容旧参数, 等价于把 wikimedia 加入 ``--prefer``
- ``--retry-misses``        忽略 ``_misses_*.txt``, 对历史 miss 强制重试
- ``--skip-download`` / ``--skip-upload``
- ``--overwrite``           强制覆盖服务端同名文件 (跳过去重)
- ``--force-upload``        跳过去重, 全量上传但不覆盖 (一般不用, 调试场景)
- ``--base-url URL``        file_manager 服务地址
- ``--api-key KEY``         API Key
- ``--dry-run``             不真下载、不上传，仅打印计划
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATA_DIR = os.path.join(ROOT, 'data')
DEFAULT_CACHE_DIR = os.path.join(DATA_DIR, 'word_audio_cache')
WORDLIST_CACHE = os.path.join(DATA_DIR, 'common_5000_words.txt')

GOOGLE_10K_URL = (
    'https://raw.githubusercontent.com/first20hours/google-10000-english/'
    'master/google-10000-english-usa-no-swears.txt'
)
DICT_API_TEMPLATE = 'https://api.dictionaryapi.dev/api/v2/entries/en/{word}'
WIKIMEDIA_OGG_TEMPLATE = (
    'https://upload.wikimedia.org/wikipedia/commons/'
    '{hash_dir}/En-us-{word}.ogg'
)
OXFORD_BASE = 'https://www.oxfordlearnersdictionaries.com'
OXFORD_DEF_TEMPLATE = OXFORD_BASE + '/definition/english/{slug}'

# 单源 UA: dict-api / wikimedia 用脚本 UA 即可
UA_SCRIPT = 'file_manager-seed-word-audio/1.0 (+educational use)'
# Oxford 网站要像浏览器, 否则容易 403
UA_BROWSER = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)

# 速率: dict-api 宽松, Oxford 严格
SLEEP_DICT_API = 0.3
SLEEP_OXFORD = 1.2

# 历史 miss 文件名（兼容旧版的统一 _misses.txt → _misses_dict-api.txt）
LEGACY_MISSES_FILE = '_misses.txt'


# ---------- HTTP ----------

def _http_get(url: str, *, timeout: int = 30, user_agent: str = UA_SCRIPT) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------- 词表 ----------

def load_wordlist(path: Optional[str], limit: int) -> List[str]:
    if path:
        with open(path, 'r', encoding='utf-8') as fp:
            words = [w.strip() for w in fp if w.strip()]
        return words[:limit]
    if not os.path.exists(WORDLIST_CACHE):
        os.makedirs(DATA_DIR, exist_ok=True)
        print(f'本地无词表缓存, 从 GitHub 拉取: {GOOGLE_10K_URL}')
        data = _http_get(GOOGLE_10K_URL)
        with open(WORDLIST_CACHE, 'wb') as fp:
            fp.write(data)
        print(f'  -> 缓存到 {WORDLIST_CACHE}')
    with open(WORDLIST_CACHE, 'r', encoding='utf-8') as fp:
        words = [w.strip() for w in fp if w.strip() and not w.startswith('#')]
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w.lower())
        if len(out) >= limit:
            break
    return out


# ---------- 音频源: dict-api ----------

def fetch_from_dict_api(word: str) -> Optional[bytes]:
    url = DICT_API_TEMPLATE.format(word=urllib.parse.quote(word))
    try:
        raw = _http_get(url, timeout=15)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        return None
    except (urllib.error.URLError, TimeoutError):
        return None
    try:
        data = json.loads(raw.decode('utf-8'))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    candidates_us, candidates_any = [], []
    for entry in data:
        for ph in entry.get('phonetics', []) or []:
            audio = ph.get('audio') or ''
            if not audio or not audio.lower().endswith('.mp3'):
                continue
            if '-us' in audio.lower() or '/us/' in audio.lower():
                candidates_us.append(audio)
            else:
                candidates_any.append(audio)
    pick = (candidates_us + candidates_any)[:1]
    if not pick:
        return None
    try:
        return _http_get(pick[0], timeout=30)
    except Exception:
        return None


# ---------- 音频源: oxford ----------

# 匹配 <... pron-us ...> 标签里的 data-src-mp3
_OXFORD_TAG_RE = re.compile(r'<[^>]*\bpron-us\b[^>]*>', re.IGNORECASE)
_OXFORD_MP3_RE = re.compile(r'data-src-mp3=["\']([^"\']+)["\']', re.IGNORECASE)


def _oxford_extract_us_mp3(html: str) -> Optional[str]:
    """从 Oxford 词条页 HTML 抓 pron-us 的 mp3 URL。

    页面里美式发音的样子（属性顺序不稳定）::

        <div class="sound audio_play_button pron-us icon-audio"
             data-src-ogg="..." data-src-mp3="https://.../us_pron/.../word__us_1.mp3"
             title="...">
    """
    for m in _OXFORD_TAG_RE.finditer(html):
        m2 = _OXFORD_MP3_RE.search(m.group(0))
        if m2:
            url = m2.group(1).strip()
            if url.startswith('/'):
                url = OXFORD_BASE + url
            return url
    return None


def fetch_from_oxford(word: str) -> Optional[bytes]:
    """从 Oxford Learner's Dictionaries 抓取美式发音 mp3。

    多义词在 Oxford 上通常分为 ``<word>_1`` / ``<word>_2`` 词条，遇到 404
    会自动 fallback 试 ``_1``。
    """
    candidates = [word, f'{word}_1']
    for slug in candidates:
        url = OXFORD_DEF_TEMPLATE.format(slug=urllib.parse.quote(slug))
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': UA_BROWSER,
                    'Accept': 'text/html,application/xhtml+xml',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                html = resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                continue
            print(f'  ! oxford http {e.code} on {slug!r}')
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            print(f'  ! oxford network error on {slug!r}: {e}')
            return None
        mp3_url = _oxford_extract_us_mp3(html)
        if not mp3_url:
            continue
        try:
            return _http_get(mp3_url, timeout=30, user_agent=UA_BROWSER)
        except Exception as e:
            print(f'  ! oxford mp3 download fail on {slug!r}: {e}')
            continue
    return None


# ---------- 音频源: wikimedia ----------

def _wikimedia_hash_dir(name: str) -> str:
    import hashlib
    h = hashlib.md5(name.encode('utf-8')).hexdigest()  # noqa: S324 — wikimedia 约定
    return f'{h[0]}/{h[0]}{h[1]}'


def fetch_from_wikimedia(word: str) -> Optional[bytes]:
    filename = f'En-us-{word}.ogg'
    url = WIKIMEDIA_OGG_TEMPLATE.format(
        hash_dir=_wikimedia_hash_dir(filename),
        word=urllib.parse.quote(word),
    )
    try:
        ogg = _http_get(url, timeout=20)
    except Exception:
        return None
    return _ogg_to_mp3(ogg)


def _ogg_to_mp3(ogg_bytes: bytes) -> Optional[bytes]:
    if not shutil.which('ffmpeg'):
        return None
    try:
        proc = subprocess.run(
            ['ffmpeg', '-loglevel', 'error', '-i', 'pipe:0',
             '-vn', '-acodec', 'libmp3lame', '-q:a', '4', '-f', 'mp3', 'pipe:1'],
            input=ogg_bytes, capture_output=True, check=True, timeout=30,
        )
        return proc.stdout
    except Exception:
        return None


# ---------- Source 配置表 ----------

class Source:
    def __init__(self, name: str, fetcher: Callable[[str], Optional[bytes]], sleep: float) -> None:
        self.name = name
        self.fetch = fetcher
        self.sleep = sleep


ALL_SOURCES: dict[str, Source] = {
    'dict-api':  Source('dict-api',  fetch_from_dict_api,  SLEEP_DICT_API),
    'oxford':    Source('oxford',    fetch_from_oxford,    SLEEP_OXFORD),
    'wikimedia': Source('wikimedia', fetch_from_wikimedia, 0.5),
}


def parse_prefer(prefer: str, use_wikimedia: bool) -> List[Source]:
    names = [n.strip() for n in prefer.split(',') if n.strip()]
    if use_wikimedia and 'wikimedia' not in names:
        names.append('wikimedia')
    out: List[Source] = []
    for n in names:
        s = ALL_SOURCES.get(n)
        if not s:
            print(f'! 未知 source: {n!r}, 跳过')
            continue
        out.append(s)
    if not out:
        raise SystemExit('! 没有可用 source')
    return out


# ---------- 缓存 ----------

class AudioCache:
    """每源独立 miss 记忆 + 通用 hit 缓存。"""

    def __init__(self, cache_dir: str):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        # 兼容旧版 _misses.txt: 视为 dict-api 的 miss
        legacy = os.path.join(cache_dir, LEGACY_MISSES_FILE)
        new_dict_misses = os.path.join(cache_dir, '_misses_dict-api.txt')
        if os.path.exists(legacy) and not os.path.exists(new_dict_misses):
            try:
                os.rename(legacy, new_dict_misses)
                print(f'(migrate) {LEGACY_MISSES_FILE} -> _misses_dict-api.txt')
            except OSError:
                pass
        # 加载所有源的 miss 集合
        self._misses: dict[str, set[str]] = {}
        for src in ALL_SOURCES:
            self._misses[src] = self._load_misses(src)

    def _miss_path(self, src: str) -> str:
        return os.path.join(self.dir, f'_misses_{src}.txt')

    def _load_misses(self, src: str) -> set[str]:
        p = self._miss_path(src)
        if not os.path.exists(p):
            return set()
        with open(p, 'r', encoding='utf-8') as fp:
            return {ln.strip() for ln in fp if ln.strip()}

    def path_of(self, word: str) -> str:
        return os.path.join(self.dir, f'{word}.mp3')

    def has_hit(self, word: str) -> bool:
        return os.path.exists(self.path_of(word))

    def has_miss(self, word: str, src: str) -> bool:
        return word in self._misses.get(src, set())

    def all_miss(self, word: str, sources: Iterable[Source]) -> bool:
        names = [s.name for s in sources]
        return all(word in self._misses.get(n, set()) for n in names)

    def save(self, word: str, data: bytes) -> str:
        p = self.path_of(word)
        with open(p, 'wb') as fp:
            fp.write(data)
        return p

    def mark_miss(self, word: str, src: str) -> None:
        bucket = self._misses.setdefault(src, set())
        if word in bucket:
            return
        bucket.add(word)
        with open(self._miss_path(src), 'a', encoding='utf-8') as fp:
            fp.write(word + '\n')


# ---------- 主下载流程 ----------

def run_download(
    words: Iterable[str],
    cache: AudioCache,
    *,
    sources: List[Source],
    dry_run: bool,
    retry_misses: bool,
) -> dict:
    stats = {
        'hit': 0, 'cached_hit': 0,
        'miss_skipped': 0, 'newly_miss': 0, 'newly_ok': 0,
        'by_source': {s.name: 0 for s in sources},
    }
    for idx, w in enumerate(words, 1):
        prefix = f'[{idx:>4}] {w!r:<20}'
        if cache.has_hit(w):
            stats['hit'] += 1
            stats['cached_hit'] += 1
            print(f'{prefix} cached ok')
            continue
        if not retry_misses and cache.all_miss(w, sources):
            stats['miss_skipped'] += 1
            print(f'{prefix} cached miss (all sources), skip')
            continue
        if dry_run:
            tried = '/'.join(s.name for s in sources)
            print(f'{prefix} (dry-run) would try [{tried}]')
            continue

        got_bytes: Optional[bytes] = None
        got_src: Optional[str] = None
        for src in sources:
            if not retry_misses and cache.has_miss(w, src.name):
                continue
            time.sleep(src.sleep)
            data = src.fetch(w)
            if data:
                got_bytes, got_src = data, src.name
                break
            cache.mark_miss(w, src.name)

        if got_bytes and got_src:
            cache.save(w, got_bytes)
            stats['hit'] += 1
            stats['newly_ok'] += 1
            stats['by_source'][got_src] += 1
            print(f'{prefix} ok via {got_src} ({len(got_bytes)} bytes)')
        else:
            stats['newly_miss'] += 1
            print(f'{prefix} miss (no US audio found in any source)')
    return stats


# ---------- 上传 ----------

def _ensure_remote_folder(base_url: str, api_key: str, name: str) -> Optional[dict]:
    """幂等创建/取回远端文件夹, 返回 ``{'id', 'path', 'name'}``。失败返回 ``None``。"""
    url = f'{base_url.rstrip("/")}/api/external/folder'
    body = json.dumps({'name': name}).encode('utf-8')
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={
            'X-API-Key': api_key, 'Content-Type': 'application/json',
            'User-Agent': UA_SCRIPT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f'! ensure folder failed: HTTP {e.code} {e.read().decode("utf-8", errors="replace")}')
        return None
    except Exception as e:
        print(f'! ensure folder failed: {e}')
        return None
    folder = data.get('folder') or {}
    tag = '已存在' if data.get('existed') else '新建'
    print(f'目标文件夹: {tag} id={folder.get("id")} name={folder.get("name")!r} path={folder.get("path")!r}')
    return {'id': folder.get('id'), 'path': folder.get('path'), 'name': folder.get('name')}


def _list_remote_filenames(base_url: str, api_key: str, folder_path: str) -> Optional[set]:
    """列出远端文件夹下的「文件 basename 集合」（不含子目录）。

    用于上传前的客户端去重: 已存在的文件不再重发, 节省带宽和时间。

    返回 ``None`` 表示查询失败（例如网络问题）——调用方应**降级到全量上传**, 而不是
    误判为"远端为空"导致跳过所有文件。
    """
    if not folder_path:
        return None
    quoted = urllib.parse.quote(folder_path.strip('/'))
    url = f'{base_url.rstrip("/")}/api/external/files/{quoted}'
    req = urllib.request.Request(
        url, headers={'X-API-Key': api_key, 'User-Agent': UA_SCRIPT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return set()  # 刚建的空目录, 等同于"远端没有文件"
        print(f'! 列远端文件失败: HTTP {e.code}; 退化为全量上传')
        return None
    except Exception as e:
        print(f'! 列远端文件失败: {e}; 退化为全量上传')
        return None
    return {f['name'] for f in data.get('files', []) if not f.get('is_dir')}


def _encode_multipart(fields: dict, files: list) -> tuple[bytes, str]:
    boundary = '----seedwordaudio' + str(int(time.time() * 1000))
    lines: list[bytes] = []
    for k, v in fields.items():
        if v is None or v == '':
            continue
        lines.append(f'--{boundary}'.encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b'')
        lines.append(str(v).encode('utf-8'))
    for field, filename, data, mime in files:
        lines.append(f'--{boundary}'.encode())
        lines.append(
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'
            .encode('utf-8')
        )
        lines.append(f'Content-Type: {mime}'.encode())
        lines.append(b'')
        lines.append(data)
    lines.append(f'--{boundary}--'.encode())
    lines.append(b'')
    body = b'\r\n'.join(lines)
    return body, f'multipart/form-data; boundary={boundary}'


def upload_to_folder(
    files: List[str],
    *,
    base_url: str,
    api_key: str,
    folder_name: Optional[str],
    folder_id: Optional[int],
    overwrite: bool,
    force_upload: bool = False,
    batch_size: int = 50,
) -> dict:
    """把本地 mp3 上传到远端文件夹, 默认开启**客户端去重**。

    去重逻辑:
    - 上传前调 ``/api/external/files/<folder_path>`` 拿到远端已存在文件名
    - 本地待传列表过滤掉与远端重名的项, 避免重复传输
    - ``overwrite=True`` 时跳过去重, 直接全量上传 + 服务端覆盖
    - ``force_upload=True`` 时跳过去重, 全量上传但不强制覆盖 (适合"确定要重传 + 服务端拒重"的诊断场景)
    - 远端列表获取失败时**降级为全量上传** (而不是误判为空), 通过日志告警

    返回字段:
    - ``ok`` / ``failed``                       服务端响应里的成功/失败条目
    - ``folder_id``                              解析到的目标文件夹 id
    - ``skipped_existing``                       去重跳过的本地文件数
    """
    folder_path: Optional[str] = None
    if folder_name:
        # ensure 一定会调一次, 顺便拿到 path 用于后续 list 去重
        info = _ensure_remote_folder(base_url, api_key, folder_name)
        if info is None:
            return {'ok': [], 'failed': [], 'folder_id': folder_id, 'skipped_existing': 0}
        if folder_id is None:
            folder_id = info['id']
        folder_path = info['path']

    # 客户端去重
    skipped_existing = 0
    if overwrite:
        print('  (--overwrite) 跳过去重, 全量上传并覆盖服务端同名文件')
    elif force_upload:
        print('  (--force-upload) 跳过去重, 全量上传 (服务端可能拒重)')
    elif folder_path:
        existing = _list_remote_filenames(base_url, api_key, folder_path)
        if existing is None:
            print('  ! 无法获取远端文件列表, 降级为全量上传')
        else:
            before = len(files)
            files = [p for p in files if os.path.basename(p) not in existing]
            skipped_existing = before - len(files)
            print(
                f'  去重: 远端已有 {len(existing)} 个文件, '
                f'本地 {before} 个 → 跳过 {skipped_existing} 个, 待上传 {len(files)} 个'
            )
    else:
        print('  ! 无 folder_path (仅传了 --folder-id), 无法去重, 全量上传')

    if not files:
        print('  没有需要上传的新文件 ✓')
        return {'ok': [], 'failed': [], 'folder_id': folder_id, 'skipped_existing': skipped_existing}

    upload_url = f'{base_url.rstrip("/")}/api/external/folder-upload'
    ok, failed = [], []
    for i in range(0, len(files), batch_size):
        batch = files[i:i + batch_size]
        body, content_type = _encode_multipart(
            fields={
                'folder_id': str(folder_id) if folder_id else '',
                'overwrite': '1' if overwrite else '0',
            },
            files=[
                ('files[]', os.path.basename(p), open(p, 'rb').read(), 'audio/mpeg')
                for p in batch
            ],
        )
        req = urllib.request.Request(
            upload_url, data=body,
            headers={
                'X-API-Key': api_key, 'Content-Type': content_type,
                'User-Agent': UA_SCRIPT,
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            ok.extend(data.get('files', []))
            failed.extend(data.get('failed', []))
            print(f'  batch {i//batch_size+1}: ok={len(data.get("files", []))} fail={len(data.get("failed", []))}')
        except Exception as e:
            print(f'  ! batch upload failed: {e}')
            failed.extend({'filename': os.path.basename(p), 'error': str(e)} for p in batch)
    return {
        'ok': ok, 'failed': failed,
        'folder_id': folder_id,
        'skipped_existing': skipped_existing,
    }


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(
        description='下载 5000 常用单词美式真人发音并上传到 file_manager',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--wordlist', default=None)
    p.add_argument('--limit', type=int, default=5000)
    p.add_argument('--prefer', default='dict-api,oxford',
                   help='来源优先级 (逗号分隔, 默认 dict-api,oxford)')
    p.add_argument('--folder-name', default='英语单词美音')
    p.add_argument('--folder-id', type=int, default=None)
    p.add_argument('--cache-dir', default=DEFAULT_CACHE_DIR)
    p.add_argument('--use-wikimedia', action='store_true',
                   help='等价于把 wikimedia 加入 --prefer')
    p.add_argument('--retry-misses', action='store_true',
                   help='忽略历史 miss, 对每个词都重新尝试所有源')
    p.add_argument('--skip-download', action='store_true')
    p.add_argument('--skip-upload', action='store_true')
    p.add_argument('--overwrite', action='store_true',
                   help='强制覆盖服务端同名文件 (跳过去重)')
    p.add_argument('--force-upload', action='store_true',
                   help='跳过去重, 全量上传但不覆盖 (一般不用, 调试场景)')
    p.add_argument('--base-url', default=os.environ.get('FILEMANAGER_BASE_URL', 'http://localhost:5000'))
    p.add_argument('--api-key', default=os.environ.get('FILEMANAGER_API_KEY', ''))
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    sources = parse_prefer(args.prefer, args.use_wikimedia)
    print(f'来源顺序: {[s.name for s in sources]}\n')

    words = load_wordlist(args.wordlist, args.limit)
    print(f'词表: {len(words)} 个词（前 5 个: {words[:5]}）\n')
    cache = AudioCache(args.cache_dir)

    if not args.skip_download:
        print('=== 下载阶段 ===')
        stats = run_download(
            words, cache,
            sources=sources, dry_run=args.dry_run,
            retry_misses=args.retry_misses,
        )
        print(f'\n下载统计: {stats}\n')

    if not args.skip_upload and not args.dry_run:
        if not args.api_key:
            print('! 缺少 API Key. 请设置 FILEMANAGER_API_KEY 或传 --api-key')
            return 2
        print('=== 上传阶段 ===')
        mp3s = sorted(
            os.path.join(cache.dir, f)
            for f in os.listdir(cache.dir)
            if f.endswith('.mp3')
        )
        wl_set = set(words)
        mp3s = [p for p in mp3s if os.path.splitext(os.path.basename(p))[0] in wl_set]
        print(f'本地缓存命中词表的 mp3: {len(mp3s)} 个')
        result = upload_to_folder(
            mp3s,
            base_url=args.base_url, api_key=args.api_key,
            folder_name=args.folder_name, folder_id=args.folder_id,
            overwrite=args.overwrite, force_upload=args.force_upload,
        )
        print(
            f'\n上传完成: ok={len(result["ok"])} failed={len(result["failed"])} '
            f'skipped_existing={result.get("skipped_existing", 0)}'
        )
        if result['failed']:
            print('失败前 5 条:')
            for it in result['failed'][:5]:
                print(f'  {it}')
    elif args.dry_run:
        print('(dry-run) 跳过上传阶段')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
