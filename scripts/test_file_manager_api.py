#!/usr/bin/env python3
"""
测试文件管理相关接口：登录会话、**文件/目录列表与分类**（/api/files、按类型、**全部文件 type/all**、含版本列表）、
共享列表、存储用量、外部文件 API（X-API-Key）、公开文档（/api/docs）、
应用更新检测（/api/query/app-update）、文件夹创建探测等。

用法（须先在本机或其它机器启动 Flask，且 --base-url 能访问到该服务）::

    ./yenv/bin/python scripts/test_file_manager_api.py --user 你的用户名 --password 你的密码

若终端里设置了 http_proxy，访问 127.0.0.1 可能被错误转发导致「连接被拒绝」，可加::

    --no-proxy

常见示例::

    --base-url http://127.0.0.1:5000
    --base-url http://192.168.1.10:5000
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Callable, Optional, Tuple

# 与 app.utils.helpers.FILE_TYPES 键一致，用于按类型列文件接口
FILE_TYPE_KEYS = (
    'images',
    'documents',
    'videos',
    'audio',
    'archives',
    'code',
    'applications_android',
    'applications_linux',
    'applications_windows',
)


def _json_loads(raw: str) -> Any:
    if not raw.strip():
        return None
    return json.loads(raw)


def _build_opener(cookies: CookieJar, no_proxy: bool) -> urllib.request.OpenerDirector:
    """构建 opener：可选禁用系统代理，避免本机测试被 HTTP_PROXY 干扰。"""
    handlers = [urllib.request.HTTPCookieProcessor(cookies)]
    if no_proxy:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    else:
        handlers.insert(0, urllib.request.ProxyHandler(urllib.request.getproxies()))
    return urllib.request.build_opener(*handlers)


def login_session(
    base_url: str, username: str, password: str, *, no_proxy: bool = False
) -> urllib.request.OpenerDirector:
    """表单登录，返回带 Cookie 的 opener（跟随 302 到首页并保存会话）。"""
    cj = CookieJar()
    opener = _build_opener(cj, no_proxy)
    data = urllib.parse.urlencode({'username': username, 'password': password}).encode()
    login_url = base_url.rstrip('/') + '/login'
    req = urllib.request.Request(
        login_url,
        data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    opener.open(req)
    return opener


def verify_session(opener: urllib.request.OpenerDirector, base_url: str) -> bool:
    """登录后应能访问 /api/user，否则视为未登录。"""
    url = base_url.rstrip('/') + '/api/user'
    code, body = request_json(opener, 'GET', url)
    if code == 200 and isinstance(body, dict) and body.get('username'):
        return True
    return False


def request_json(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    data: Optional[bytes] = None,
) -> Tuple[int, Any]:
    req = urllib.request.Request(url, data=data, method=method.upper())
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with opener.open(req) as resp:
            raw = resp.read().decode()
            return resp.status, _json_loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ''
        try:
            body = _json_loads(raw)
        except json.JSONDecodeError:
            body = raw
        return e.code, body


def request_http(
    opener: urllib.request.OpenerDirector,
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    data: Optional[bytes] = None,
) -> Tuple[int, bytes]:
    """原始 HTTP，不解析 JSON（用于 HTML 等）。"""
    req = urllib.request.Request(url, data=data, method=method.upper())
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with opener.open(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b''
        return e.code, raw


def run_get_html_ok(
    opener: urllib.request.OpenerDirector,
    base: str,
    name: str,
    path: str,
) -> bool:
    url = base + path
    try:
        code, raw = request_http(opener, 'GET', url)
    except (urllib.error.URLError, OSError) as e:
        print_case(name, False, str(e))
        return False
    if code != 200:
        print_case(name, False, f'HTTP {code}')
        return False
    text = raw.decode('utf-8', errors='replace').lower()
    if '<html' in text or '<!doctype' in text:
        print_case(name, True)
        return True
    print_case(name, False, '响应非 HTML')
    return False


def print_case(name: str, ok: bool, detail: str = '') -> None:
    status = 'OK' if ok else 'FAIL'
    line = f'[{status}] {name}'
    if detail:
        line += f' — {detail}'
    print(line)


def print_files_table(title: str, body: Any, *, max_rows: int = 50) -> None:
    """打印接口返回的 files 列表（id、名称、路径、类型、是否目录、大小等）。"""
    if not isinstance(body, dict):
        return
    files = body.get('files')
    if not isinstance(files, list):
        return
    cp = body.get('current_path', '')
    print()
    print(f'--- {title}  current_path={cp!r}  共 {len(files)} 条 ---')
    if not files:
        print('  (无文件)')
        print()
        return
    for i, f in enumerate(files[:max_rows]):
        if not isinstance(f, dict):
            print(f'  [{i}] {f!r}')
            continue
        fid = f.get('id', '')
        name = f.get('name', '')
        path = f.get('path', '')
        ftype = f.get('type', '')
        is_dir = f.get('is_dir', False)
        size = f.get('size_formatted', '')
        modified = f.get('modified', '')
        is_pub = f.get('is_public')
        extra = f' public={is_pub}' if is_pub is not None else ''
        kind = '目录' if is_dir else '文件'
        print(
            f'  id={fid}  [{kind}] {name!r}  path={path!r}  type={ftype!r}  '
            f'size={size!r}  mtime={modified!r}{extra}'
        )
    if len(files) > max_rows:
        print(f'  ... 省略 {len(files) - max_rows} 条（可用 --max-file-rows 调整）')
    print()


def print_user_summary(body: Any) -> None:
    if not isinstance(body, dict):
        return
    key = body.get('api_key') or ''
    if len(key) > 12:
        key_show = f'{key[:8]}…{key[-4:]}'
    elif key:
        key_show = key[:4] + '…'
    else:
        key_show = '(空)'
    print()
    print('--- GET /api/user ---')
    print(f'  username:     {body.get("username")!r}')
    print(f'  display_name: {body.get("display_name")!r}')
    print(f'  email:        {body.get("email")!r}')
    print(f'  api_key:      {key_show}')
    print()


def print_app_update_summary(body: Any) -> None:
    """打印应用更新检测接口返回摘要。"""
    if not isinstance(body, dict):
        return
    print()
    print('--- GET /api/query/app-update ---')
    print(f'  success:      {body.get("success")!r}')
    print(f'  has_update:   {body.get("has_update")!r}')
    if 'compare_reason' in body:
        print(f'  compare_reason: {body.get("compare_reason")!r}')
    if body.get('message'):
        print(f'  message:      {body.get("message")!r}')
    latest = body.get('latest')
    if isinstance(latest, dict):
        print(f'  latest.version: {latest.get("version")!r}')
        print(f'  latest.download_url: {(latest.get("download_url") or "")[:72]!r}...')
    elif latest is None:
        print('  latest:       (无匹配版本记录)')
    print()


def print_storage_summary(body: Any) -> None:
    if not isinstance(body, dict):
        return
    print()
    print('--- GET /api/storage ---')
    print(f'  used_formatted: {body.get("used_formatted")!r}')
    print(f'  total_formatted: {body.get("total_formatted")!r}')
    print(f'  percentage:     {body.get("percentage")!r}')
    print()


def print_file_types_summary(body: Any) -> None:
    if not isinstance(body, dict) or not body.get('success'):
        return
    types_list = body.get('types')
    if not isinstance(types_list, list):
        return
    print()
    print('--- GET /api/get_file_type（分类选项） ---')
    for item in types_list:
        if isinstance(item, dict):
            print(f'  id={item.get("id")}  name={item.get("name")!r}')
        else:
            print(f'  {item!r}')
    print()


def build_opener_no_cookie(*, no_proxy: bool) -> urllib.request.OpenerDirector:
    """仅用于带 X-API-Key 的请求，与登录会话共用相同的代理策略。"""
    if no_proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies()))


def run_get_no_session(
    base: str,
    name: str,
    path: str,
    expect_keys: Callable[[Any], bool],
    *,
    no_proxy: bool,
) -> Tuple[bool, Any]:
    """无 Cookie，用于 /api/docs 等公开 GET。"""
    opener = build_opener_no_cookie(no_proxy=no_proxy)
    return run_get(opener, base, name, path, expect_keys)


def run_get(
    opener: urllib.request.OpenerDirector,
    base: str,
    name: str,
    path: str,
    expect_keys: Callable[[Any], bool],
    *,
    headers: Optional[dict] = None,
) -> Tuple[bool, Any]:
    """GET JSON，校验字段，返回 (是否成功, body)。"""
    url = base + path
    try:
        code, body = request_json(opener, 'GET', url, headers=headers)
    except (urllib.error.URLError, OSError) as e:
        print_case(name, False, str(e))
        return False, None
    if code != 200:
        print_case(name, False, f'HTTP {code} {body!r}')
        return False, body
    if not expect_keys(body):
        print_case(name, False, f'响应不符合预期: {body!r}')
        return False, body
    print_case(name, True)
    return True, body


def _explain_connection_error(exc: BaseException, base_url: str) -> str:
    msg = str(exc)
    if 'Connection refused' in msg or 'Errno 111' in msg:
        return (
            f'无法连接到 {base_url}（连接被拒绝）。\n'
            '  · 请确认 Flask 已启动，且监听地址可被本脚本访问（例如本机: flask run 或 python -m flask run，默认 127.0.0.1:5000）。\n'
            '  · 若服务跑在其它机器或端口，请使用: --base-url http://IP:端口\n'
            '  · 若终端设置了 http_proxy，访问本机可能被误代理，请尝试加参数: --no-proxy'
        )
    if 'Name or service not known' in msg or 'getaddrinfo failed' in msg:
        return f'无法解析或连接主机，请检查 --base-url: {base_url}\n原始错误: {msg}'
    return msg


def main() -> int:
    parser = argparse.ArgumentParser(description='测试文件管理 HTTP 接口')
    parser.add_argument(
        '--base-url',
        default='http://127.0.0.1:5000',
        help='应用根 URL（须与浏览器访问地址一致）',
    )
    parser.add_argument('--user', required=True, help='登录用户名')
    parser.add_argument('--password', required=True, help='登录密码')
    parser.add_argument(
        '--no-proxy',
        action='store_true',
        help='不使用系统 HTTP(S)_PROXY，避免本机 127.0.0.1 被代理导致连接失败',
    )
    parser.add_argument(
        '--max-file-rows',
        type=int,
        default=50,
        metavar='N',
        help='每个文件列表接口最多打印多少条文件（默认 50）',
    )
    args = parser.parse_args()
    base = args.base_url.rstrip('/')

    try:
        opener = login_session(base, args.user, args.password, no_proxy=args.no_proxy)
    except (urllib.error.URLError, OSError) as e:
        print(_explain_connection_error(e, base), file=sys.stderr)
        print(f'原始异常: {e!r}', file=sys.stderr)
        return 1

    if not verify_session(opener, base):
        print(
            '登录后会话无效：GET /api/user 未返回用户信息。请检查用户名、密码是否正确。',
            file=sys.stderr,
        )
        return 1

    ok_all = True
    mr = args.max_file_rows

    # 先取用户信息（含 api_key），供外部文件 API 使用
    ok, user_body = run_get(
        opener,
        base,
        'GET /api/user',
        '/api/user',
        lambda b: isinstance(b, dict) and 'api_key' in b and b.get('username'),
    )
    ok_all &= ok
    if ok:
        print_user_summary(user_body)

    ok, types_body = run_get(
        opener,
        base,
        'GET /api/get_file_type',
        '/api/get_file_type',
        lambda b: isinstance(b, dict) and b.get('success') and isinstance(b.get('types'), list),
    )
    ok_all &= ok
    if ok:
        print_file_types_summary(types_body)

    # ---------- 文件与目录（会话）：列表、分类、版本、存储 ----------
    ok, _body_root = run_get(
        opener,
        base,
        'GET /api/files（根目录）',
        '/api/files',
        lambda b: isinstance(b, dict) and 'files' in b and 'current_path' in b,
    )
    ok_all &= ok
    if ok:
        print_files_table('GET /api/files（根目录）', _body_root, max_rows=mr)

    ok, _body_slash = run_get(
        opener,
        base,
        'GET /api/files/（根目录）',
        '/api/files/',
        lambda b: isinstance(b, dict) and 'files' in b and 'current_path' in b,
    )
    ok_all &= ok
    if ok:
        print_files_table('GET /api/files/（根目录）', _body_slash, max_rows=mr)

    ok, st_body = run_get(
        opener,
        base,
        'GET /api/storage',
        '/api/storage',
        lambda b: isinstance(b, dict) and 'used' in b and 'percentage' in b,
    )
    ok_all &= ok
    if ok:
        print_storage_summary(st_body)

    for ft in FILE_TYPE_KEYS:
        ok, body = run_get(
            opener,
            base,
            f'GET /api/files/type/{ft}',
            f'/api/files/type/{ft}',
            lambda b, p=ft: isinstance(b, dict) and 'files' in b and b.get('current_path') == p,
        )
        ok_all &= ok
        if ok:
            print_files_table(f'GET /api/files/type/{ft}', body, max_rows=mr)

    ok, body_all = run_get(
        opener,
        base,
        'GET /api/files/type/all（全部文件，仅文件不含目录）',
        '/api/files/type/all',
        lambda b: isinstance(b, dict)
        and b.get('success') is True
        and 'files' in b
        and b.get('current_path') == 'all'
        and 'total' in b,
    )
    ok_all &= ok
    if ok:
        print_files_table('GET /api/files/type/all', body_all, max_rows=mr)
        fl = body_all.get('files') if isinstance(body_all, dict) else None
        if isinstance(fl, list):
            dirs = [x for x in fl if isinstance(x, dict) and x.get('is_dir')]
            if dirs:
                print_case(
                    'GET /api/files/type/all（列表项应均为文件）',
                    False,
                    f'发现 {len(dirs)} 条 is_dir=true',
                )
                ok_all = False
            else:
                print_case('GET /api/files/type/all（列表项应均为文件）', True)

    ok, _body_by_all = run_get(
        opener,
        base,
        'GET /api/files/by-type/all（与 type/all 同义）',
        '/api/files/by-type/all',
        lambda b: isinstance(b, dict)
        and b.get('success') is True
        and 'files' in b
        and b.get('current_path') == 'all',
    )
    ok_all &= ok

    ok, awv = run_get(
        opener,
        base,
        'GET /api/files/all-with-versions',
        '/api/files/all-with-versions?per_page=20&page=1',
        lambda b: isinstance(b, dict)
        and b.get('success') is True
        and 'files' in b
        and 'total' in b,
    )
    ok_all &= ok
    if ok and isinstance(awv, dict):
        print()
        print(
            f'--- GET /api/files/all-with-versions  total={awv.get("total")!r}  '
            f'page={awv.get("page")!r}  pages={awv.get("pages")!r} ---'
        )
        print()

    ok, _byp = run_get(
        opener,
        base,
        'GET /api/files/by-type/documents（分页）',
        '/api/files/by-type/documents?page=1&per_page=10',
        lambda b: isinstance(b, dict) and b.get('success') is True and 'page' in b,
    )
    ok_all &= ok

    ok, body_sh = run_get(
        opener,
        base,
        'GET /api/shared-files',
        '/api/shared-files',
        lambda b: isinstance(b, dict) and 'files' in b and b.get('current_path') == 'shared',
    )
    ok_all &= ok
    if ok:
        print_files_table('GET /api/shared-files', body_sh, max_rows=mr)

    # 公开接口文档（无需登录）
    ok, _dj = run_get_no_session(
        base,
        'GET /api/docs.json（公开）',
        '/api/docs.json',
        lambda b: isinstance(b, dict) and 'sections' in b,
        no_proxy=args.no_proxy,
    )
    ok_all &= ok

    ext_html = build_opener_no_cookie(no_proxy=args.no_proxy)
    ok = run_get_html_ok(ext_html, base, 'GET /api/docs（HTML 公开）', '/api/docs')
    ok_all &= ok

    # 应用更新检测：无 package 且未提供四维渠道时应 400
    try:
        code_au0, body_au0 = request_json(opener, 'GET', base + '/api/query/app-update')
    except (urllib.error.URLError, OSError) as e:
        print_case('GET /api/query/app-update（无参数应 400）', False, str(e))
        ok_all = False
    else:
        if code_au0 == 400 and isinstance(body_au0, dict) and body_au0.get('error'):
            print_case('GET /api/query/app-update（无参数应 400）', True)
        else:
            print_case(
                'GET /api/query/app-update（无参数应 400）',
                False,
                f'HTTP {code_au0} {body_au0!r}',
            )
            ok_all = False

    # 应用更新检测：带 package（不要求登录）；无匹配版本时 success=true、has_update=false、latest=null
    qs_pkg = urllib.parse.urlencode(
        {'package': 'com.example.integration.test', 'system': 'liangeos'}
    )
    ok_au, body_au = run_get(
        opener,
        base,
        'GET /api/query/app-update（package + system，检测最新版）',
        '/api/query/app-update?' + qs_pkg,
        lambda b: isinstance(b, dict)
        and b.get('success') is True
        and 'has_update' in b
        and ('latest' in b),
    )
    ok_all &= ok_au
    if ok_au:
        print_app_update_summary(body_au)

    # 应用更新检测：不提供 package 时须带 system/type/vendor/device_type 四维
    qs_four = urllib.parse.urlencode(
        {
            'system': 'liangeos',
            'type': 'ota',
            'vendor': 'test_vendor',
            'device_type': 'test_device',
        }
    )
    ok_au4, body_au4 = run_get(
        opener,
        base,
        'GET /api/query/app-update（仅四维渠道，无 package）',
        '/api/query/app-update?' + qs_four,
        lambda b: isinstance(b, dict)
        and b.get('success') is True
        and 'has_update' in b,
    )
    ok_all &= ok_au4
    if ok_au4:
        print_app_update_summary(body_au4)

    # 外部 API：使用 /api/user 返回的 api_key（opener 与 --no-proxy 一致，避免默认 opener 走代理导致拒绝连接）
    api_key = None
    if isinstance(user_body, dict):
        api_key = user_body.get('api_key')

    if api_key:
        ext_opener = build_opener_no_cookie(no_proxy=args.no_proxy)
        ext_headers = {'X-API-Key': api_key}
        ok, body_root = run_get(
            ext_opener,
            base,
            'GET /api/external/files（根，X-API-Key）',
            '/api/external/files',
            lambda b: isinstance(b, dict) and 'files' in b,
            headers=ext_headers,
        )
        ok_all &= ok
        if ok:
            print_files_table('GET /api/external/files（根）', body_root, max_rows=mr)
        for plat in FILE_TYPE_KEYS:
            path = f'/api/external/files/type/{plat}'
            name = f'GET /api/external/files/type/{plat} (X-API-Key)'
            ok, body = run_get(
                ext_opener,
                base,
                name,
                path,
                lambda b: isinstance(b, dict) and 'files' in b,
                headers=ext_headers,
            )
            ok_all &= ok
            if ok:
                print_files_table(name, body, max_rows=mr)
        ok, body_ext_all = run_get(
            ext_opener,
            base,
            'GET /api/external/files/type/all (X-API-Key)',
            '/api/external/files/type/all',
            lambda b: isinstance(b, dict)
            and 'files' in b
            and b.get('current_path') == 'all',
            headers=ext_headers,
        )
        ok_all &= ok
        if ok:
            print_files_table('GET /api/external/files/type/all', body_ext_all, max_rows=mr)
            fl = body_ext_all.get('files') if isinstance(body_ext_all, dict) else None
            if isinstance(fl, list):
                dirs = [x for x in fl if isinstance(x, dict) and x.get('is_dir')]
                if dirs:
                    print_case(
                        'GET /api/external/files/type/all（列表项应均为文件）',
                        False,
                        f'发现 {len(dirs)} 条 is_dir=true',
                    )
                    ok_all = False
                else:
                    print_case('GET /api/external/files/type/all（列表项应均为文件）', True)
    else:
        print_case('外部 API（跳过：未取得 api_key）', True)

    # 创建文件夹：空名称应 400（不写盘）
    try:
        code_fc, body_fc = request_json(
            opener,
            'POST',
            base + '/api/folder/create',
            headers={'Content-Type': 'application/json'},
            data=json.dumps({'name': '', 'path': ''}).encode(),
        )
    except (urllib.error.URLError, OSError) as e:
        print_case('POST /api/folder/create（空名称应 400）', False, str(e))
        ok_all = False
    else:
        if code_fc == 400 and isinstance(body_fc, dict) and body_fc.get('error'):
            print_case('POST /api/folder/create（空名称应 400）', True)
        else:
            print_case(
                'POST /api/folder/create（空名称应 400）',
                False,
                f'HTTP {code_fc} {body_fc!r}',
            )
            ok_all = False

    # 导入任务状态：不存在的 job_id 应 404
    try:
        code_ij, body_ij = request_json(
            opener, 'GET', base + '/api/import/job/__no_such_job__'
        )
    except (urllib.error.URLError, OSError) as e:
        print_case('GET /api/import/job（不存在应 404）', False, str(e))
        ok_all = False
    else:
        if code_ij == 404:
            print_case('GET /api/import/job（不存在应 404）', True)
        else:
            print_case(
                'GET /api/import/job（不存在应 404）',
                False,
                f'HTTP {code_ij} {body_ij!r}',
            )
            ok_all = False

    # 从链接导入：仅探测接口存在（不真正拉取网络）
    try:
        code, body = request_json(
            opener,
            'POST',
            base + '/api/import/url',
            headers={'Content-Type': 'application/json'},
            data=json.dumps({'url': '', 'mode': 'direct'}).encode(),
        )
    except (urllib.error.URLError, OSError) as e:
        print_case('POST /api/import/url（空 URL 应 400）', False, str(e))
        ok_all = False
    else:
        if code == 400 and isinstance(body, dict) and body.get('error'):
            print_case('POST /api/import/url（空 URL 应 400）', True)
        else:
            print_case('POST /api/import/url（空 URL 应 400）', False, f'HTTP {code} {body!r}')
            ok_all = False

    print()
    if ok_all:
        print('全部用例通过。')
        return 0
    print('存在失败用例。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
