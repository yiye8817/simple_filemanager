#!/usr/bin/env python3
"""
demo 客户端：通过文件夹插件查询「英语单词美式发音」并下载音频。

支持两种调用模式
================

1. **外部 API Key 模式**（默认）—— 调用 ``/api/external/folders/<id>/plugins/<name>/invoke``：

   - 需要 ``--api-key`` / ``FILEMANAGER_API_KEY``
   - 插件返回的 URL 是 ``/api/external/download/<id>``，下载时也要带 API Key

2. **公开模式** (``--public``) —— 调用 ``/api/public/folders/<id>/plugins/<name>/invoke``：

   - **完全无需 API Key**, 浏览器/任意客户端都能用
   - 前提: 插件 owner 在 UI 上勾选了"公开访问"开关
   - 插件返回的 URL 是 ``/api/public/download/<id>?p=<plugin_id>&sig=<hex>``，
     HMAC 签名嵌在 URL 中，关闭开关或重置 API Key 后所有旧链接立即失效

用法
====
::

    # 1. 先跑 seed_word_audio.py 上传 mp3
    export FILEMANAGER_API_KEY=xxxxxxxxxxxx
    export FILEMANAGER_BASE_URL=http://localhost:5000

    # 2. 默认 (API Key 模式)
    ./yenv/bin/python scripts/demo_word_plugin.py --word hello

    # 3. 公开模式 (插件需先在 UI 上开"公开访问"开关)
    ./yenv/bin/python scripts/demo_word_plugin.py --public --word hello \
        --folder-id 38 --plugin-name word_audio

    # 4. 批量
    ./yenv/bin/python scripts/demo_word_plugin.py --words hello world example --out-dir ./out_audio

    # 5. 仅打印 URL 不下载
    ./yenv/bin/python scripts/demo_word_plugin.py --word hello --no-download

参数
====
- ``--public``              走 /api/public/ 无授权入口 (插件需开启公开访问)
- ``--folder-name NAME``    目标文件夹 (与 seed_word_audio.py 同名一致); ``--public`` 时需要 ``--folder-id``
- ``--folder-id ID``        覆盖 ``--folder-name``
- ``--plugin-name NAME``    插件名，默认 ``word_audio``
- ``--word WORD``           单个单词
- ``--words W1 W2 ...``     批量
- ``--out-dir DIR``         下载目录，默认 ``./out_audio``
- ``--no-download``         只查 URL 不下载
- ``--base-url URL``        默认 ``$FILEMANAGER_BASE_URL`` 或 http://localhost:5000
- ``--api-key KEY``         默认 ``$FILEMANAGER_API_KEY`` (``--public`` 模式下不需要)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional

USER_AGENT = 'file_manager-demo-word-plugin/1.0'


def _post_json(
    url: str, *, api_key: Optional[str], body: dict, timeout: int = 30,
) -> tuple[int, dict]:
    """POST JSON; api_key 为空时不带 X-API-Key 头 (公开模式)。"""
    data = json.dumps(body).encode('utf-8')
    headers = {'Content-Type': 'application/json', 'User-Agent': USER_AGENT}
    if api_key:
        headers['X-API-Key'] = api_key
    req = urllib.request.Request(url, data=data, method='POST', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode('utf-8'))
        except Exception:
            payload = {'error': f'HTTP {e.code}'}
        return e.code, payload


def ensure_folder(base_url: str, api_key: str, name: str) -> Optional[int]:
    url = f'{base_url.rstrip("/")}/api/external/folder'
    status, body = _post_json(url, api_key=api_key, body={'name': name})
    if status not in (200, 201):
        print(f'! ensure folder failed: {status} {body}')
        return None
    folder = body.get('folder') or {}
    fid = folder.get('id')
    tag = '已存在' if body.get('existed') else '新建'
    print(f'目标文件夹: {tag} id={fid} path={folder.get("path")!r}')
    return fid


def invoke_plugin(
    base_url: str, api_key: Optional[str],
    folder_id: int, plugin_name: str,
    *, action: str, params: dict, public: bool = False,
) -> tuple[int, dict]:
    prefix = 'public' if public else 'external'
    url = f'{base_url.rstrip("/")}/api/{prefix}/folders/{folder_id}/plugins/{plugin_name}/invoke'
    # public 模式无视 api_key
    return _post_json(
        url, api_key=None if public else api_key,
        body={'action': action, 'params': params},
    )


def download(url: str, api_key: Optional[str], out_path: str) -> bool:
    """下载 URL 到本地; public 签名 URL 时不带 X-API-Key 头。"""
    headers = {'User-Agent': USER_AGENT}
    # /api/public/download/... 自带签名, 不需要 API Key; 其它情况下需要带
    if '/api/public/download/' not in url and api_key:
        headers['X-API-Key'] = api_key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        print(f'  ! 下载失败: HTTP {e.code}')
        return False
    except Exception as e:
        print(f'  ! 下载失败: {e}')
        return False
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'wb') as fp:
        fp.write(data)
    print(f'  ✓ saved {out_path} ({len(data)} bytes)')
    return True


def main() -> int:
    p = argparse.ArgumentParser(
        description='调用文件夹插件查询单词发音并下载 mp3',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--public', action='store_true',
                   help='走 /api/public/ 无授权入口 (插件需先开启公开访问开关)')
    p.add_argument('--folder-name', default='英语单词美音')
    p.add_argument('--folder-id', type=int, default=None)
    p.add_argument('--plugin-name', default='word_audio')
    p.add_argument('--word', default=None)
    p.add_argument('--words', nargs='+', default=None)
    p.add_argument('--action', default='lookup')
    p.add_argument('--out-dir', default='./out_audio')
    p.add_argument('--no-download', action='store_true')
    p.add_argument('--base-url', default=os.environ.get('FILEMANAGER_BASE_URL', 'http://localhost:5000'))
    p.add_argument('--api-key', default=os.environ.get('FILEMANAGER_API_KEY', ''))
    args = p.parse_args()

    # public 模式无需 API Key; 默认模式才强制
    if not args.public and not args.api_key:
        print('! 缺少 API Key. 设置 FILEMANAGER_API_KEY / --api-key, 或加 --public 走公开通道')
        return 2

    words = []
    if args.word:
        words.append(args.word)
    if args.words:
        words.extend(args.words)
    if not words:
        print('! 至少指定一个单词: --word hello 或 --words hello world ...')
        return 2

    folder_id = args.folder_id
    if folder_id is None:
        if args.public:
            # 公开模式没有 /api/public/folder 端点 (创建文件夹必须授权);
            # 让用户显式给 --folder-id
            print('! --public 模式必须显式传 --folder-id (公开通道不支持按名字幂等创建)')
            return 2
        folder_id = ensure_folder(args.base_url, args.api_key, args.folder_name)
        if not folder_id:
            return 3

    print(f'\n=== 调用插件 ===')
    print(f'  模式:        {"PUBLIC (无授权)" if args.public else "EXTERNAL (API Key)"}')
    print(f'  base_url:    {args.base_url}')
    print(f'  folder_id:   {folder_id}')
    print(f'  plugin_name: {args.plugin_name}')
    print(f'  action:      {args.action}')
    print()

    ok, miss, err = 0, 0, 0
    for w in words:
        status, body = invoke_plugin(
            args.base_url, args.api_key, folder_id, args.plugin_name,
            action=args.action, params={'word': w}, public=args.public,
        )
        if status == 200 and body.get('success'):
            url = body.get('url')
            print(f'[{w}] hit: {url}')
            if not args.no_download:
                out = os.path.join(args.out_dir, f'{w}.mp3')
                download(url, args.api_key, out)
            ok += 1
        elif status == 404:
            print(f'[{w}] miss: {body.get("message") or body.get("error")}')
            miss += 1
        else:
            print(f'[{w}] error {status}: {body}')
            err += 1

    print(f'\n== Summary == ok={ok} miss={miss} err={err}')
    return 0 if err == 0 else 4


if __name__ == '__main__':
    raise SystemExit(main())
