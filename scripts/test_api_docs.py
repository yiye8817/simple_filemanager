#!/usr/bin/env python3
"""
快速验证公开文档接口（无需登录）。

用法::

    python3 scripts/test_api_docs.py
    python3 scripts/test_api_docs.py --base-url http://192.168.1.10:5000

若设置了 http_proxy 且访问本机失败，可配合环境变量 NO_PROXY=127.0.0.1,localhost。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    p = argparse.ArgumentParser(description='测试 GET /api/docs 与 /api/docs.json')
    p.add_argument('--base-url', default='http://127.0.0.1:5000', help='服务根 URL')
    args = p.parse_args()
    base = args.base_url.rstrip('/')

    for path in ('/api/docs.json', '/api/docs'):
        url = base + path
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            print(f'FAIL {path}: HTTP {e.code}', file=sys.stderr)
            return 1
        except urllib.error.URLError as e:
            print(f'FAIL {path}: {e}', file=sys.stderr)
            return 1
        if code != 200:
            print(f'FAIL {path}: status {code}', file=sys.stderr)
            return 1
        if path.endswith('.json'):
            try:
                data = json.loads(body.decode())
            except json.JSONDecodeError:
                print(f'FAIL {path}: 非 JSON', file=sys.stderr)
                return 1
            if not data.get('sections'):
                print(f'FAIL {path}: 缺少 sections', file=sys.stderr)
                return 1
        print(f'OK {path} ({len(body)} bytes)')
    print('全部通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
