import errno
import json
import logging
from collections import defaultdict
import math
import mimetypes
import os
import re
import socket
import shutil
import threading
import time
import uuid
import ssl
import urllib.request
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler
from urllib.error import URLError, HTTPError
from flask import Blueprint, app, current_app, flash, redirect, render_template, request, jsonify, session, send_file, abort, url_for
from app.utils.decorators import login_required, api_key_required
from app.utils.helpers import (
    FILE_TYPES,
    download_file_from,
    get_directory_by_path,
    get_file_details,
    parse_apk,
    resolve_upload_physical_path,
)
from app.models import File, User
from app.models.version_model import FileVersion
from app import db
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
from unidecode import unidecode
from app.models.resource import get_resource_poster, update_resource_fields
from app.services.file_service import FileService
from app.services import webhook_service
file_bp = Blueprint('file', __name__)
logger = logging.getLogger(__name__)

_import_jobs = {}
_import_jobs_lock = threading.Lock()
_MAX_CRAWL_FILES = 25
_DOWNLOAD_EXTENSIONS = set()
for _exts in FILE_TYPES.values():
    for _e in _exts:
        _DOWNLOAD_EXTENSIONS.add(_e.lower())


def _normalize_folder_path_for_upload(path_str):
    if path_str is None or path_str in ('', '/'):
        return ''
    s = str(path_str).strip()
    if s.startswith('/'):
        s = s[1:]
    return s


def _import_url_allowed(url):
    try:
        p = urlparse(url)
        if p.scheme not in ('http', 'https'):
            return False
        host = (p.hostname or '').lower()
        if host in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
            return False
        if host.startswith('192.168.') or host.startswith('10.') or host.startswith('172.16.'):
            return False
        return True
    except Exception:
        return False


def _filename_from_content_disposition(header_val):
    if not header_val:
        return None
    m = re.search(r"filename\*=UTF-8''([^;\r\n]+)", header_val, re.I)
    if m:
        try:
            return unquote(m.group(1).strip().strip('"'))
        except Exception:
            return m.group(1).strip().strip('"')
    m = re.search(r'filename\s*=\s*("?)([^";\r\n]+)\1', header_val, re.I)
    if m:
        raw = m.group(2).strip().strip('"')
        try:
            return unquote(raw)
        except Exception:
            return raw
    return None


def _http_error_message(exc):
    """将底层网络/HTTP 异常转换为用户可读说明（中文）。"""
    if isinstance(exc, HTTPError):
        return f'服务器返回 HTTP {exc.code}'
    if isinstance(exc, URLError):
        r = exc.reason
        if isinstance(r, ConnectionRefusedError) or (
            isinstance(r, OSError) and getattr(r, 'errno', None) == errno.ECONNREFUSED
        ):
            return (
                '连接被拒绝：对方未接受连接。'
                '若本机 wget 能下而此处失败，多半是 Flask 进程未继承终端里的 http_proxy/https_proxy，'
                '或服务跑在容器内网络与宿主机不同；请为运行服务的进程配置相同代理或可达地址。'
            )
        if isinstance(r, (TimeoutError, socket.timeout)):
            return '连接或读取超时，请稍后重试或检查链接是否可用。'
        if isinstance(r, OSError) and getattr(r, 'errno', None) == errno.ETIMEDOUT:
            return '网络超时，请检查目标是否可达。'
        return f'无法访问链接: {exc.reason!s}'
    if isinstance(exc, ssl.SSLError):
        return f'SSL/TLS 错误: {exc}'
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return '请求超时，请稍后重试。'
    if isinstance(exc, OSError):
        if exc.errno == errno.ECONNREFUSED:
            return (
                '连接被拒绝：若 wget 可用，请检查服务进程的代理环境变量是否与终端一致，或是否需使用宿主机/网关 IP。'
            )
        if exc.errno == errno.ETIMEDOUT:
            return '连接超时，请检查网络与防火墙。'
        return f'网络错误: {exc.strerror or exc}'
    return str(exc)


# 与 wget/curl 类似：走系统代理（HTTP_PROXY/HTTPS_PROXY），并避免仅用 urlopen(..., context=) 时与代理链不一致
_HTTP_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


def _normalize_proxy(proxy_raw):
    """前端可能填写 ``127.0.0.1:7890`` / ``http://...:7890`` / 完整 socks5 URL,
    统一格式化为 ``http://host:port`` 等标准 URL; 空串返回 None。"""
    s = (proxy_raw or '').strip()
    if not s:
        return None
    if '://' not in s:
        # 默认按 http 代理处理
        s = 'http://' + s
    # 用 urlparse 做一次基本合法性检查
    pu = urlparse(s)
    if not pu.scheme or not pu.hostname:
        raise ValueError(f'代理格式不正确: {proxy_raw}')
    if pu.scheme not in ('http', 'https', 'socks5', 'socks5h', 'socks4'):
        raise ValueError(f'不支持的代理协议: {pu.scheme}')
    return s


def _build_proxy_dict(proxy_url):
    """构造 ``urllib`` 的 proxies 字典; ``proxy_url`` 为 None 时回退系统代理。"""
    if not proxy_url:
        return urllib.request.getproxies()
    return {'http': proxy_url, 'https': proxy_url}


def _http_get(url, timeout=90, *, proxy=None):
    ctx = ssl.create_default_context()
    proxies = _build_proxy_dict(proxy)
    opener = urllib.request.build_opener(
        ProxyHandler(proxies),
        HTTPSHandler(context=ctx),
    )
    req = Request(
        url,
        headers={
            'User-Agent': _HTTP_UA,
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        },
    )
    try:
        resp = opener.open(req, timeout=timeout)
        try:
            data = resp.read()
            return data, resp.headers, resp.geturl()
        finally:
            resp.close()
    except (HTTPError, URLError, OSError, TimeoutError, ssl.SSLError, socket.timeout) as e:
        raise ValueError(_http_error_message(e)) from e


# ---------------------------------------------------------------------------
# yt-dlp 适配 (youtube / bilibili / 通用视频站)
# ---------------------------------------------------------------------------

# 通过 host 匹配是否是 yt-dlp 擅长的视频站
_YTDLP_HOSTS = (
    'youtube.com', 'youtu.be', 'm.youtube.com',
    'bilibili.com', 'b23.tv', 'm.bilibili.com',
    'space.bilibili.com',
)


def _is_video_site(url):
    try:
        host = (urlparse(url).hostname or '').lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == h or host.endswith('.' + h) for h in _YTDLP_HOSTS)


def _find_bin(name, extra_candidates=()):
    """优先 PATH; 然后查若干常见路径; 兜底 None。"""
    p = shutil.which(name)
    if p:
        return p
    candidates = list(extra_candidates) + [
        os.path.expanduser(f'~/bin/{name}'),
        os.path.expanduser(f'~/.local/bin/{name}'),
        f'/usr/local/bin/{name}',
        f'/opt/homebrew/bin/{name}',
        f'/usr/bin/{name}',
        os.path.join(os.path.dirname(os.sys.executable), name),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _find_ytdlp_binary():
    """Flask 进程在 systemd / IDE 下启动时 PATH 通常不包含 ``~/bin``, 这里显式
    fallback 一组常见位置。"""
    return _find_bin('yt-dlp')


def _find_aria2c_binary():
    return _find_bin('aria2c')


def _split_extra_args(s):
    """安全切分用户填的 yt-dlp 额外参数, 用 shlex POSIX 模式 (跟 shell 行为一致)。

    返回 ``(args_list, error_msg)``; 解析失败时 ``args_list`` 是 ``None``。
    """
    import shlex
    if not s:
        return [], None
    s = str(s).strip()
    if not s:
        return [], None
    try:
        return shlex.split(s, posix=True), None
    except ValueError as e:
        return None, f'额外参数解析失败: {e}'


def _ytdlp_download(url, target_dir, *, proxy=None, progress_cb=None, timeout=900,
                    extra_args=None, use_aria2c=True, aria2c_threads=16):
    """用 yt-dlp 下载到 ``target_dir``; 返回 ``(stdout_log, downloaded_files, error_msg)``。

    - ``stdout_log`` 是 yt-dlp 完整 stdout/stderr (合并); 不论成功失败都会返回。
    - ``downloaded_files`` 是落到 ``target_dir`` 的文件绝对路径列表 (按 mtime 升序)。
    - ``error_msg`` 为 None 表示 yt-dlp 退出码 0; 否则是中文化的错误说明。

    ``extra_args`` 是用户的自定义参数列表 (已经 shlex 切好), 拼在 yt-dlp 命令尾部,
    可以覆盖默认 ``-f`` / ``--merge-output-format`` 等。

    ``use_aria2c=True`` 时, 如果系统装了 aria2c, yt-dlp 会用 aria2c 作为底层下载器,
    默认 ``-x N -s N -k 1M`` 启用 N 线程多连接, 显著提升大文件 / 海外站点下载速度。
    """
    import subprocess as _sp

    bin_path = _find_ytdlp_binary()
    if not bin_path:
        return (
            '[file_manager] yt-dlp 未安装。可执行: pip install yt-dlp\n'
            '或在 app.config 里设置 YTDLP_BIN 指向二进制路径。',
            [],
            'yt-dlp 未安装',
        )

    os.makedirs(target_dir, exist_ok=True)
    before = set(os.listdir(target_dir))

    # 用 %(title).100B 限制文件名长度避免超长被部分文件系统拒绝
    tmpl = '%(title).100B [%(id)s].%(ext)s'
    cmd = [
        bin_path,
        '--no-progress',
        '--newline',                      # 让进度按行输出, 方便采集
        '--restrict-filenames',           # 文件名只用 ASCII, 避免后续 secure_filename 全删
        '--no-playlist',                  # 单个视频; 用户要拉播放列表请自行打开
        '-f', 'bv*+ba/b',                 # 合并 best video + best audio
        '--merge-output-format', 'mp4',
        '--no-mtime',
        '-o', os.path.join(target_dir, tmpl),
    ]

    # aria2c 多线程加速
    aria2c_bin = _find_aria2c_binary() if use_aria2c else None
    if use_aria2c and aria2c_bin:
        n = max(1, min(int(aria2c_threads or 16), 64))
        cmd.extend([
            '--downloader', 'aria2c',
            '--downloader-args',
            # -x N: 单 server 连接数; -s N: 分片数; -k 1M: 每片 1MB; --file-allocation=none 避免预分配阻塞
            f'aria2c:-x{n} -s{n} -k1M --file-allocation=none --console-log-level=warn',
        ])
    elif use_aria2c and not aria2c_bin:
        # 用户勾了 aria2c 但系统没装, 不强失败, 退回 yt-dlp 内置 downloader 并在日志里说明
        if progress_cb:
            try:
                progress_cb('[aria2c] 未在 PATH 找到 aria2c, 退回 yt-dlp 默认下载器')
            except Exception:  # noqa: BLE001
                pass

    # 用户自定义参数放在末尾, 这样 ``-f bestvideo[height<=720]`` 这种覆盖默认的会生效
    if extra_args:
        cmd.extend(list(extra_args))

    if proxy:
        cmd.extend(['--proxy', proxy])

    cmd.append(url)

    log_lines = []
    cmd_line = f'$ {" ".join(_shellquote(c) for c in cmd)}'
    cwd_line = f'(cwd={target_dir})'
    log_lines.append(cmd_line)
    log_lines.append(cwd_line)
    # 同步写到外部 job log; 之前只放在局部 list, 失败后用户看不到完整命令行
    if progress_cb:
        try:
            progress_cb(cmd_line); progress_cb(cwd_line)
        except Exception:  # noqa: BLE001
            pass

    try:
        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                         text=True, errors='replace', cwd=target_dir)
    except OSError as exc:
        return ('\n'.join(log_lines) + f'\n[file_manager] 启动 yt-dlp 失败: {exc}',
                [], f'启动 yt-dlp 失败: {exc}')

    deadline = time.time() + timeout if timeout else None
    try:
        # 流式读取, 同时给前端 progress 回调
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip('\r\n')
            log_lines.append(line)
            if progress_cb:
                try:
                    progress_cb(line)
                except Exception:  # noqa: BLE001
                    pass
            if deadline and time.time() > deadline:
                proc.kill()
                log_lines.append(f'[file_manager] 超时 ({timeout}s), 已强制结束 yt-dlp')
                break
        proc.wait(timeout=10)
    except Exception as exc:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:
            pass
        log_lines.append(f'[file_manager] 监听 yt-dlp 输出异常: {exc}')

    rc = proc.returncode
    # 找出 yt-dlp 新增的文件 (排除 .part / .ytdl 临时残留)
    after = set(os.listdir(target_dir))
    new_files = []
    for name in sorted(after - before):
        if name.endswith(('.part', '.ytdl', '.temp')):
            continue
        full = os.path.join(target_dir, name)
        if os.path.isfile(full) and os.path.getsize(full) > 0:
            new_files.append(full)
    new_files.sort(key=lambda p: os.path.getmtime(p))

    err = None
    if rc != 0:
        err = f'yt-dlp 退出码 {rc}'
        if not new_files:
            err += ' (无任何文件落盘)'
    elif not new_files:
        err = 'yt-dlp 退出码 0 但未生成任何文件'

    log_lines.append(f'[file_manager] yt-dlp 退出码: {rc}, 新增 {len(new_files)} 个文件')
    return ('\n'.join(log_lines), new_files, err)


def _shellquote(s):
    """简易 shell-quote, 只用于日志展示, 不会真的喂给 shell, 因此不需要 robust。"""
    if not s:
        return "''"
    if re.match(r'^[A-Za-z0-9_./:=@%+\-]+$', s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def _extract_file_urls_from_html(base_url, html_bytes):
    try:
        text = html_bytes.decode('utf-8', errors='ignore')
    except Exception:
        text = str(html_bytes)
    found = []
    for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', text, re.I):
        href = m.group(1).strip()
        if not href or href.startswith(('#', 'javascript:', 'mailto:')):
            continue
        abs_url = urljoin(base_url, href)
        path = urlparse(abs_url).path.lower()
        ext = os.path.splitext(path)[1]
        if ext and ext in _DOWNLOAD_EXTENSIONS:
            found.append(abs_url)
    # 去重且保持顺序
    seen = set()
    uniq = []
    for u in found:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq[:_MAX_CRAWL_FILES]


def _persist_imported_bytes(user_id, suggested_name, content_bytes, current_path_raw):
    """与上传逻辑一致：写入磁盘并写入/更新 File 记录，自动 file_type 归类。"""
    current_path = _normalize_folder_path_for_upload(current_path_raw)
    parent = None
    if current_path or current_path == '':
        parent = get_directory_by_path(current_path, user_id)
        if not parent:
            return None, '目标目录不存在'

    current_usage = get_user_storage_usage(user_id)
    max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024
    if current_usage + len(content_bytes) > max_storage:
        return None, '存储空间不足'

    filename0 = suggested_name or 'imported.bin'
    filename = secure_filename(unidecode(filename0))
    if not filename:
        filename = 'imported.bin'

    file_path = os.path.join(current_path, filename) if current_path else filename
    if file_path.startswith('/'):
        file_path = file_path[1:]
    physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), file_path)
    os.makedirs(os.path.dirname(physical_path), exist_ok=True)

    existing_file = File.query.filter_by(
        path=filename,
        parent_id=parent.id if parent else None,
        user_id=user_id
    ).first()

    with open(physical_path, 'wb') as wf:
        wf.write(content_bytes)
    file_size = os.path.getsize(physical_path)

    if existing_file:
        existing_file.size = file_size
        existing_file.modified_at = datetime.utcnow()
        new_file = existing_file
        if get_file_type(filename) == 'applications_android':
            try:
                json_apkinfo = parse_apk(physical_path)
                if existing_file.resource_id:
                    update_resource_fields(existing_file.resource_id, tags=json_apkinfo.get('details'))
            except Exception as ex:
                logger.debug('apk parse skip: %s', ex)
    else:
        new_file = File(
            name=filename0,
            path=file_path,
            size=file_size,
            file_type=get_file_type(filename),
            is_directory=False,
            user_id=user_id,
            parent_id=parent.id if parent else 1
        )
        db.session.add(new_file)
    db.session.commit()
    webhook_service.notify_file_event(
        new_file, 'modified' if existing_file else 'created'
    )
    return new_file, None


def _persist_local_file(user_id, src_path, suggested_name, current_path_raw, *,
                        move=True):
    """把一个**已经在磁盘上的本地文件**搬到该用户的目标目录, 写入 File 表。

    与 ``_persist_imported_bytes`` 的差异:
    - 不读进内存 (yt-dlp 的视频文件常常几百 MB ~ 几 GB)
    - 同名先做 mtime/ size 计算后直接 ``shutil.move`` (跨设备会自动 copy+remove)
    """
    current_path = _normalize_folder_path_for_upload(current_path_raw)
    parent = None
    if current_path or current_path == '':
        parent = get_directory_by_path(current_path, user_id)
        if not parent:
            return None, '目标目录不存在'

    src_size = os.path.getsize(src_path) if os.path.isfile(src_path) else 0
    current_usage = get_user_storage_usage(user_id)
    max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024
    if current_usage + src_size > max_storage:
        return None, '存储空间不足'

    # 文件名归一: 优先用 yt-dlp 给的原名 (它已经做了 --restrict-filenames),
    # 实在还有奇怪字符就用 unidecode 兜底, 但保留扩展名
    filename0 = suggested_name or os.path.basename(src_path)
    base, ext = os.path.splitext(filename0)
    safe_base = secure_filename(unidecode(base)) or 'imported'
    filename = safe_base + (ext or '.bin')

    file_path = os.path.join(current_path, filename) if current_path else filename
    if file_path.startswith('/'):
        file_path = file_path[1:]
    physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), file_path)
    os.makedirs(os.path.dirname(physical_path), exist_ok=True)

    if os.path.abspath(src_path) != os.path.abspath(physical_path):
        if move:
            shutil.move(src_path, physical_path)
        else:
            shutil.copy2(src_path, physical_path)
    file_size = os.path.getsize(physical_path)

    existing_file = File.query.filter_by(
        path=filename,
        parent_id=parent.id if parent else None,
        user_id=user_id,
    ).first()
    if existing_file:
        existing_file.size = file_size
        existing_file.modified_at = datetime.utcnow()
        new_file = existing_file
    else:
        new_file = File(
            name=filename0,
            path=file_path,
            size=file_size,
            file_type=get_file_type(filename),
            is_directory=False,
            user_id=user_id,
            parent_id=parent.id if parent else 1,
        )
        db.session.add(new_file)
    db.session.commit()
    webhook_service.notify_file_event(
        new_file, 'modified' if existing_file else 'created'
    )
    return new_file, None


def _run_import_job(job_id, user_id, url, mode, current_path_raw,
                    *, proxy=None, engine='auto',
                    ytdlp_args=None, use_aria2c=True, aria2c_threads=16):
    with _import_jobs_lock:
        job = _import_jobs.get(job_id)
        if not job:
            return
        job['status'] = 'running'
        job['message'] = '开始处理...'
        job.setdefault('log', '')

    def progress(step, total, msg, names=None):
        with _import_jobs_lock:
            j = _import_jobs.get(job_id)
            if j:
                j['step'] = step
                j['total'] = total
                j['message'] = msg
                if names is not None:
                    j['imported_names'] = names

    def append_log(line):
        """yt-dlp 流式输出会调这个 ; 也用于 http 分支的运行注记。"""
        if not line:
            return
        with _import_jobs_lock:
            j = _import_jobs.get(job_id)
            if not j:
                return
            tail = j.get('log') or ''
            # 控制单次 job 日志总长在 200KB 以内, 避免 OOM
            chunk = (line if line.endswith('\n') else line + '\n')
            if len(tail) > 200_000:
                tail = tail[-150_000:]
            j['log'] = tail + chunk

    # 决定使用哪个引擎: auto 时仅在视频站才用 yt-dlp; ytdlp/http 是用户强制
    engine = (engine or 'auto').lower()
    if engine not in ('auto', 'http', 'ytdlp'):
        engine = 'auto'
    use_ytdlp = (engine == 'ytdlp') or (engine == 'auto' and _is_video_site(url))

    try:
        if not _import_url_allowed(url):
            raise ValueError('不允许的链接地址')

        # 校验代理格式
        try:
            proxy_url = _normalize_proxy(proxy) if proxy else None
        except ValueError as exc:
            append_log(f'[proxy] {exc}')
            raise

        if proxy_url:
            append_log(f'[proxy] 使用代理 {proxy_url}')

        imported_names = []

        # ----- 视频站 / yt-dlp 分支 -----
        if use_ytdlp:
            append_log(f'[engine] yt-dlp ({_find_ytdlp_binary() or "未找到"})')
            aria_bin = _find_aria2c_binary()
            append_log(f'[aria2c] use={use_aria2c} bin={aria_bin or "未找到"} threads={aria2c_threads}')
            if ytdlp_args:
                append_log(f'[ytdlp_args] {" ".join(_shellquote(a) for a in ytdlp_args)}')
            append_log(f'[url] {url}')
            progress(0, 1, '正在调用 yt-dlp ...')
            with _make_ytdlp_tmpdir(user_id) as tmpdir:
                log_text, files, err = _ytdlp_download(
                    url, tmpdir, proxy=proxy_url,
                    progress_cb=append_log,
                    extra_args=ytdlp_args,
                    use_aria2c=use_aria2c,
                    aria2c_threads=aria2c_threads,
                )
                # 上面 progress_cb 已经把行追加进 log; 但万一 progress_cb 没接, 兜底再合并一次
                if log_text and not (_import_jobs.get(job_id) or {}).get('log', '').endswith(log_text[-100:] or ''):
                    pass  # 已增量写入, 不重复
                if err:
                    progress(1, 1, err)
                    raise ValueError(err)
                if not files:
                    raise ValueError('yt-dlp 没有生成任何文件')
                total = len(files)
                progress(0, total, f'下载完成, 正在入库 ({total} 个文件)...')
                for i, src in enumerate(files):
                    name = os.path.basename(src)
                    nf, perr = _persist_local_file(user_id, src, name, current_path_raw)
                    if perr:
                        append_log(f'[persist] 跳过 {name}: {perr}')
                        continue
                    imported_names.append(nf.name)
                    append_log(f'[persist] 入库 {nf.name} ({nf.size} bytes)')
                    progress(i + 1, total, f'入库 ({i + 1}/{total}) {nf.name}', list(imported_names))
                if not imported_names:
                    raise ValueError('yt-dlp 下载成功但入库全部失败 (见日志)')
                progress(total, total, '完成', imported_names)

        elif mode == 'direct':
            append_log(f'[engine] http direct')
            append_log(f'[url] {url}')
            progress(0, 1, '正在下载...')
            data, headers, final_url = _http_get(url, proxy=proxy_url)
            name = _filename_from_content_disposition(headers.get('Content-Disposition'))
            if not name:
                path = urlparse(final_url).path
                name = unquote(os.path.basename(path)) or 'download.bin'
            append_log(f'[http] {len(data)} bytes, 文件名 {name}')
            nf, err = _persist_imported_bytes(user_id, name, data, current_path_raw)
            if err:
                raise ValueError(err)
            imported_names.append(nf.name)
            progress(1, 1, '完成', imported_names)
        else:
            append_log(f'[engine] http crawl')
            append_log(f'[url] {url}')
            progress(0, 1, '正在获取页面...')
            page_bytes, headers, final_url = _http_get(url, proxy=proxy_url)
            ct = (headers.get('Content-Type') or '').lower()
            links = []
            if 'html' in ct or url.lower().endswith(('.htm', '.html')) or page_bytes[:1] in (b'<', b'\xef'):
                links = _extract_file_urls_from_html(final_url, page_bytes)
            if not links:
                raise ValueError('页面中未发现可下载的文件链接（按扩展名识别）')
            append_log(f'[crawl] 发现 {len(links)} 个候选链接')
            total = len(links)
            for i, link in enumerate(links):
                progress(i, total, f'正在下载 ({i + 1}/{total})...', list(imported_names))
                try:
                    data, h2, u2 = _http_get(link, proxy=proxy_url)
                    name = _filename_from_content_disposition(h2.get('Content-Disposition'))
                    if not name:
                        path = urlparse(u2).path
                        name = unquote(os.path.basename(path)) or f'file_{i + 1}.bin'
                    nf, err = _persist_imported_bytes(user_id, name, data, current_path_raw)
                    if err:
                        logger.warning('import skip %s: %s', link, err)
                        append_log(f'[crawl] 跳过 {link}: {err}')
                        continue
                    imported_names.append(nf.name)
                    append_log(f'[crawl] ok {nf.name}')
                except Exception as ex:
                    logger.warning('import fail %s: %s', link, ex)
                    append_log(f'[crawl] 失败 {link}: {ex}')
            progress(total, total, '完成', imported_names)

        with _import_jobs_lock:
            j = _import_jobs.get(job_id)
            if j:
                j['status'] = 'done'
                j['message'] = '导入完成'
                j['imported_names'] = imported_names
    except ValueError as e:
        # 业务错误与 _http_get 转换后的网络错误：不打完整栈, 但保留运行日志
        logger.warning('import job %s: %s', job_id, e)
        append_log(f'[error] {e}')
        with _import_jobs_lock:
            j = _import_jobs.get(job_id)
            if j:
                j['status'] = 'error'
                j['message'] = str(e)
    except Exception as e:
        logger.exception('import job %s', job_id)
        append_log(f'[exception] {e}')
        with _import_jobs_lock:
            j = _import_jobs.get(job_id)
            if j:
                j['status'] = 'error'
                j['message'] = str(e) or '导入失败'


def _make_ytdlp_tmpdir(user_id):
    """返回一个 context manager: 在用户上传根下临时目录 ``_ytdlp_<job>`` ,
    退出时尽量清理 (失败 / 中断时残留可由 cleanup 脚本处理)。"""
    import contextlib
    upload_root = current_app.config['UPLOAD_FOLDER']
    base = os.path.join(upload_root, '_ytdlp_tmp', str(user_id))
    os.makedirs(base, exist_ok=True)
    @contextlib.contextmanager
    def _ctx():
        path = os.path.join(base, uuid.uuid4().hex)
        os.makedirs(path, exist_ok=True)
        try:
            yield path
        finally:
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
    return _ctx()


def _query_files_by_file_type(user_id, path):
    """按分类查询文件；applications_android 兼容数据库中 file_type=apk 的旧记录。"""
    if path == 'applications_android':
        return (
            File.query.filter_by(user_id=user_id)
            .filter(File.file_type.in_(['applications_android', 'apk']))
            .all()
        )
    return File.query.filter_by(user_id=user_id, file_type=path).all()


def _normalize_physical_upload_path(path):
    """侧栏「按类型浏览」与共享/最近等为虚拟路径，并非磁盘上的文件夹；上传应落到用户根目录。"""
    if path is None:
        return ''
    path = str(path).strip()
    if path.startswith('/'):
        path = path[1:]
    if path in FILE_TYPES or path in ('shared', 'recent', 'search', 'all'):
        return ''
    return path


# 主页路由
@file_bp.route('/')
@login_required
def index():
    return render_template('index.html', user_name=session.get('user_name'))


# 下列路由须在 /api/files/<path:path> 之前注册，否则会被通配路径吞掉。
@file_bp.route('/api/files/by-type/<file_type>', methods=['GET'])
@file_bp.route('/api/files/type/<file_type>', methods=['GET'])
@login_required
def get_files_by_type(file_type):
    """按 FILE_TYPES 中的类型键列出当前用户文件（如 documents、applications_android）。file_type=all 表示全部文件（扁平，不含文件夹）。"""
    user_id = session.get('user_id')
    if file_type == 'all':
        query_param = request.args.get('query')
        files = (
            File.query.filter_by(user_id=user_id, is_directory=False)
            .order_by(File.modified_at.desc())
            .all()
        )
        if query_param == 'remain':
            files = [f for f in files if not f.resource_id]
        per_page = request.args.get('per_page', type=int)
        page = max(1, request.args.get('page', 1, type=int) or 1)
        if per_page:
            per_page = max(1, min(per_page, 2000))
            total = len(files)
            start = (page - 1) * per_page
            page_files = files[start:start + per_page]
            pages = math.ceil(total / per_page) if total else 0
            return jsonify({
                'success': True,
                'files': [f.to_dict() for f in page_files],
                'current_path': 'all',
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': pages,
            })
        return jsonify({
            'success': True,
            'files': [f.to_dict() for f in files],
            'current_path': 'all',
            'total': len(files),
        })
    if file_type not in FILE_TYPES:
        return jsonify({
            'success': False,
            'error': f'未知的文件类型: {file_type}',
            'allowed': ['all'] + list(FILE_TYPES.keys()),
        }), 400
    query_param = request.args.get('query')
    files = _query_files_by_file_type(user_id, file_type)
    if query_param == 'remain':
        files = [f for f in files if not f.resource_id]
    per_page = request.args.get('per_page', type=int)
    page = max(1, request.args.get('page', 1, type=int) or 1)
    if per_page:
        per_page = max(1, min(per_page, 2000))
        total = len(files)
        start = (page - 1) * per_page
        page_files = files[start:start + per_page]
        pages = math.ceil(total / per_page) if total else 0
        return jsonify({
            'success': True,
            'files': [f.to_dict() for f in page_files],
            'current_path': file_type,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': pages,
        })
    return jsonify({
        'success': True,
        'files': [f.to_dict() for f in files],
        'current_path': file_type,
        'total': len(files),
    })


@file_bp.route('/api/files/all-with-versions', methods=['GET'])
@login_required
def get_all_files_with_versions():
    """
    获取当前用户全部文件及关联的版本管理记录。

    查询参数：
    - include_dirs：1 时包含文件夹，默认 0 仅文件
    - page、per_page：分页，默认 page=1、per_page=500，单页最多 2000 条
    """
    user_id = session.get('user_id')
    include_dirs = request.args.get('include_dirs', '0') == '1'
    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = request.args.get('per_page', 500, type=int) or 500
    per_page = max(1, min(per_page, 2000))

    base = File.query.filter_by(user_id=user_id)
    if not include_dirs:
        base = base.filter_by(is_directory=False)

    pagination = base.order_by(File.modified_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    files = pagination.items
    file_ids = [f.id for f in files]

    versions_by_fid = defaultdict(list)
    if file_ids:
        rows = FileVersion.query.filter(
            FileVersion.file_id.in_(file_ids),
            FileVersion.status == 'active',
        ).all()
        for v in rows:
            versions_by_fid[v.file_id].append(v.to_dict())
        for fid in versions_by_fid:
            versions_by_fid[fid].sort(
                key=lambda x: x.get('timestamp') or '',
                reverse=True,
            )

    out = []
    for f in files:
        row = f.to_dict()
        vers = versions_by_fid.get(f.id, [])
        row['versions'] = vers
        row['version_latest'] = vers[0] if vers else None
        out.append(row)

    return jsonify({
        'success': True,
        'files': out,
        'total': pagination.total,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'pages': pagination.pages,
    })


@file_bp.route('/api/files/download/<int:file_id>', methods=['GET'])
@login_required
def files_download_by_id_redirect(file_id):
    """
    兼容误用的 /api/files/download/<id>：重定向到真实下载地址 /api/download/<id>。
    否则 path 会被当成「download」目录下的子路径而 404。
    """
    return redirect(f'/api/download/{file_id}', code=302)


# 文件管理API
def _list_with_child_counts(files):
    """对 File 列表中的目录条目批量算 ``child_count``, 返回 to_dict 列表。

    用一次 ``GROUP BY parent_id`` 查询拿到所有子项数, 避免 N+1; 非目录条目走原来
    的 ``size_formatted`` 逻辑不变。
    """
    dir_ids = [f.id for f in files if f.is_directory]
    cnt_map: dict = {}
    if dir_ids:
        rows = (
            db.session.query(File.parent_id, db.func.count(File.id))
            .filter(File.parent_id.in_(dir_ids))
            .group_by(File.parent_id)
            .all()
        )
        cnt_map = {pid: cnt for pid, cnt in rows}
    return [
        f.to_dict(child_count=cnt_map.get(f.id) if f.is_directory else None)
        for f in files
    ]


@file_bp.route('/api/files', defaults={'path': ''})
@file_bp.route('/api/files/', defaults={'path': ''})
@file_bp.route('/api/files/<path:path>')
@login_required
def get_files(path):
    user_id = session.get('user_id')
    
    query_param = request.args.get('query')  # 返回 'remain' 或 None
    print(f"get_files in:{path,query_param}")
    if path.startswith("type/"):
        path = path[5:]
        if path == 'all':
            files = (
                File.query.filter_by(user_id=user_id, is_directory=False)
                .order_by(File.modified_at.desc())
                .all()
            )
            if query_param == 'remain':
                files = [f for f in files if not f.resource_id]
            return jsonify({
                'files': _list_with_child_counts(files),
                'current_path': 'all',
            })
        if path in FILE_TYPES.keys():
            files = _query_files_by_file_type(user_id, path)
            if(query_param == 'remain'):#增加过滤已经添加过程的
                ret_fs=[]
                for f in files:
                    print(f.resource_id)
                    if not f.resource_id:
                        ret_fs.append(f)
            else:
                ret_fs = files
            
            return jsonify({
                'files': _list_with_child_counts(ret_fs),
                'current_path': path
            })
    
    # 规范化路径，删除重复的斜杠
    path = '/'.join([p for p in path.split('/') if p])
    print(f"get_files:{path}")
    
    if not path:
        # 根目录：以"用户自己的根记录 id"为父，避免历史上写死 parent_id=1
        # 同时把孤儿（parent_id IS NULL）也并入根目录展示，方便用户能看到并删除它们
        root = get_or_create_user_root(user_id)
        files = (
            File.query
            .filter(
                File.user_id == user_id,
                db.or_(File.parent_id == root.id, File.parent_id.is_(None)),
            )
            .all()
        )
    else:
        # 根据路径查找父目录
        parent = get_directory_by_path(path, user_id)
        if not parent:
            # 兼容 /api/files/applications_android（无前缀 type/）：无同名物理目录时按分类返回
            if '/' not in path and path in FILE_TYPES.keys():
                files = _query_files_by_file_type(user_id, path)
                return jsonify({
                    'files': _list_with_child_counts(files),
                    'current_path': path
                })
            return jsonify({'error': '路径不存在'}), 404
        
        if not parent.is_directory:
            return jsonify({'error': '请求的路径是一个文件，而不是目录'}), 400
        
        files = File.query.filter_by(user_id=user_id, parent_id=parent.id).all()
    
    return jsonify({
        'files': _list_with_child_counts(files),
        'current_path': path
    })


@file_bp.route('/api/shared-files')
@login_required
def list_shared_files():
    """当前用户已开启公开分享的文件与文件夹列表。"""
    user_id = session.get('user_id')
    files = (
        File.query.filter_by(user_id=user_id, is_public=True)
        .order_by(File.modified_at.desc())
        .all()
    )
    return jsonify({
        'files': _list_with_child_counts(files),
        'current_path': 'shared'
    })


@file_bp.route('/api/import/capabilities', methods=['GET'])
@login_required
def import_capabilities():
    """前端打开导入 modal 前先查一下: yt-dlp / aria2c 是否就绪, 让 UI 给出准确提示。"""
    ytdlp_bin = _find_ytdlp_binary()
    aria2c_bin = _find_aria2c_binary()
    return jsonify({
        'ytdlp': bool(ytdlp_bin),
        'ytdlp_path': ytdlp_bin,
        'aria2c': bool(aria2c_bin),
        'aria2c_path': aria2c_bin,
        'video_hosts': list(_YTDLP_HOSTS),
    })


@file_bp.route('/api/import/url', methods=['POST'])
@login_required
def start_url_import():
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    mode = (data.get('mode') or 'direct').strip()
    if mode not in ('direct', 'crawl'):
        mode = 'direct'
    engine = (data.get('engine') or 'auto').strip().lower()
    if engine not in ('auto', 'http', 'ytdlp'):
        engine = 'auto'
    proxy_raw = (data.get('proxy') or '').strip()
    current_path = data.get('path', '')
    if not url:
        return jsonify({'error': '请提供链接'}), 400
    # 提前校验代理格式, 让用户立刻拿到 400 而不是异步任务里再失败
    try:
        proxy_norm = _normalize_proxy(proxy_raw) if proxy_raw else None
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    # yt-dlp 额外参数 (字符串, shlex 切分) — 提前校验, 错了直接 400
    ytdlp_args_raw = (data.get('ytdlp_args') or '').strip()
    extra_args, parse_err = _split_extra_args(ytdlp_args_raw)
    if parse_err:
        return jsonify({'error': parse_err}), 400

    # aria2c 配置
    use_aria2c_raw = data.get('aria2c', True)
    if isinstance(use_aria2c_raw, str):
        use_aria2c = use_aria2c_raw.lower() in ('1', 'true', 'yes', 'on')
    else:
        use_aria2c = bool(use_aria2c_raw)
    try:
        aria2c_threads = int(data.get('aria2c_threads') or 16)
    except (TypeError, ValueError):
        aria2c_threads = 16
    aria2c_threads = max(1, min(aria2c_threads, 64))

    user_id = session.get('user_id')
    job_id = str(uuid.uuid4())
    is_video = _is_video_site(url)
    will_use_ytdlp = (engine == 'ytdlp') or (engine == 'auto' and is_video)
    aria2c_bin = _find_aria2c_binary()
    with _import_jobs_lock:
        _import_jobs[job_id] = {
            'user_id': user_id,
            'status': 'queued',
            'step': 0,
            'total': 1,
            'message': '排队中',
            'imported_names': [],
            'engine': 'ytdlp' if will_use_ytdlp else 'http',
            'is_video_site': is_video,
            'proxy': proxy_norm or None,
            'log': '',
            'ytdlp_args': extra_args,
            'aria2c': bool(use_aria2c and aria2c_bin) if will_use_ytdlp else False,
            'aria2c_threads': aria2c_threads,
        }
    # 子线程里访问 db / current_app.config 需要 app context, 这里抓住 app 对象
    # 一并传进去 (旧实现是依赖父线程的隐式 ctx, 在 systemd / gunicorn 下不稳定)。
    app_obj = current_app._get_current_object()
    t = threading.Thread(
        target=_run_import_job_in_app_context,
        args=(app_obj, job_id, user_id, url, mode, current_path),
        kwargs={
            'proxy': proxy_norm,
            'engine': engine,
            'ytdlp_args': extra_args,
            'use_aria2c': use_aria2c,
            'aria2c_threads': aria2c_threads,
        },
        daemon=True,
    )
    t.start()
    return jsonify({
        'job_id': job_id,
        'engine': 'ytdlp' if will_use_ytdlp else 'http',
        'is_video_site': is_video,
        'aria2c_available': bool(aria2c_bin),
    })


def _run_import_job_in_app_context(app_obj, *args, **kwargs):
    """子线程入口: 推一个独立的 app context, 让 ``current_app`` / ``db.session`` 都可用。"""
    with app_obj.app_context():
        _run_import_job(*args, **kwargs)


@file_bp.route('/api/import/job/<job_id>', methods=['GET'])
@login_required
def url_import_job_status(job_id):
    user_id = session.get('user_id')
    with _import_jobs_lock:
        job = _import_jobs.get(job_id)
    if not job:
        return jsonify({'error': '任务不存在'}), 404
    if job.get('user_id') != user_id:
        return jsonify({'error': '无权访问'}), 403
    # 客户端可传 ``?log=0`` 关掉日志返回; 默认带上日志便于失败时显示
    include_log = (request.args.get('log') or '1') != '0'
    body = {
        'status': job.get('status'),
        'step': job.get('step', 0),
        'total': job.get('total', 1),
        'message': job.get('message', ''),
        'imported_names': job.get('imported_names', []),
        'engine': job.get('engine'),
        'is_video_site': job.get('is_video_site'),
        'proxy': job.get('proxy'),
    }
    if include_log:
        body['log'] = job.get('log') or ''
    return jsonify(body)


# 这里应该添加更多的文件相关路由，例如上传、下载、共享等
# 文件管理API
# @file_bp.route('/api/files', defaults={'path': ''})
# @file_bp.route('/api/files/', defaults={'path': ''})
# @file_bp.route('/api/files/<path:path>')
# @login_required
# def get_files(path):
#     user_id = session.get('user_id')
#     print(f"get_files in:{path}")
#     if path.startswith("type/"):
#         path = path[5:]
#         if path in FILE_TYPES.keys():
#             files = File.query.filter_by(user_id=user_id, file_type=path).all()
#             return jsonify({
#             'files': [file.to_dict() for file in files],
#             'current_path': path
#             })
#     # 规范化路径，删除重复的斜杠
#     path = '/'.join([p for p in path.split('/') if p])
#     print(f"get_files:{path}")
#     if not path:
#         # 根目录
#         files = File.query.filter_by(user_id=user_id, parent_id=1).all()
#     else:
#         # 根据路径查找父目录
#         parent = get_directory_by_path(path, user_id)
#         if not parent:
#             return jsonify({'error': '路径不存在'}), 404
        
#         if not parent.is_directory:
#             return jsonify({'error': '请求的路径是一个文件，而不是目录'}), 400
        
#         files = File.query.filter_by(user_id=user_id, parent_id=parent.id).all()
    
#     return jsonify({
#         'files': [file.to_dict() for file in files],
#         'current_path': path
#     })

# 外部API访问 - 根据API密钥获取文件
@file_bp.route('/api/external/files', defaults={'path': ''})
@file_bp.route('/api/external/files/<path:path>')
@api_key_required
def external_get_files(path):
    user = request.user
    user_id = user.id
    print(f"external:get_files in:{path}")
    
    if path.startswith("type/"):
        path = path[5:]
        if path == 'all':
            files = (
                File.query.filter_by(user_id=user_id, is_directory=False)
                .order_by(File.modified_at.desc())
                .all()
            )
            return jsonify({
                'files': [file.to_dict() for file in files],
                'current_path': 'all',
            })
        if path in FILE_TYPES.keys():
            files = _query_files_by_file_type(user_id, path)
            return jsonify({
                'files': [file.to_dict() for file in files],
                'current_path': path
            })
    
    # user = request.user
    
    # 规范化路径，删除重复的斜杠
    path = '/'.join([p for p in path.split('/') if p])
    
    if not path:
        # 根目录
        files = File.query.filter_by(user_id=user.id, parent_id=None).all()
    else:
        # 根据路径查找父目录
        parent = get_directory_by_path(path, user.id)
        if not parent:
            if '/' not in path and path in FILE_TYPES.keys():
                files = _query_files_by_file_type(user_id, path)
                return jsonify({
                    'files': [file.to_dict() for file in files],
                    'current_path': path
                })
            return jsonify({'error': '路径不存在'}), 404
        
        if not parent.is_directory:
            return jsonify({'error': '请求的路径是一个文件，而不是目录'}), 400
        
        files = File.query.filter_by(user_id=user.id, parent_id=parent.id).all()
    
    return jsonify({
        'files': [file.to_dict() for file in files],
        'current_path': path
    })

# 公共分享链接访问
@file_bp.route('/shared/<share_id>')
def access_shared_file(share_id):
    file = File.query.filter_by(public_share_id=share_id, is_public=True).first()
    
    if not file:
        flash('分享的文件不存在或已取消分享')
        return redirect(url_for('auth.login'))
    
    if file.is_directory:
        # 如果是目录，显示目录内容
        files = File.query.filter_by(parent_id=file.id).all()
        return render_template('shared_folder.html', folder=file, files=[f.to_dict() for f in files])
    else:
        # 如果是文件，直接下载
        return download_file_by_id(file.id, public=True)

# 文件上传
@file_bp.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    print(f"upload_file:{user},{user_id}")
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    if 'files[]' not in request.files:
        return jsonify({'error': '请选择要上传的文件'}), 400
    
    files = request.files.getlist('files[]')
    current_path = request.form.get('path', '')
    #去除开始的绝对路径
    if current_path.startswith('/'):
        current_path = current_path[1:]
    current_path = _normalize_physical_upload_path(current_path)

    print(f"curren_path:{current_path}")
    # 检查当前路径是否有效
    parent = None
    if current_path or current_path == '':
        parent = get_directory_by_path(current_path, user_id)
        if not parent:
            # parent = current_path
            return jsonify({'error': '上传目录不存在'}), 404
    
    # 检查存储空间是否足够
    current_usage = get_user_storage_usage(user_id)
    max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024  # 将GB转换为字节
    
    total_upload_size = sum(len(file.read()) for file in files)
    for file in files:
        file.seek(0)  # 重置文件指针
    
    if current_usage + total_upload_size > max_storage:
        return jsonify({'error': '存储空间不足'}), 400
    
    uploaded_files = []
    notify_events = []  # [(file_obj, 'created'|'modified'), ...] — commit 后统一推送
    
    # parent.path 是 DB 中已规范化的磁盘相对路径（ASCII 化），用作所有子文件的统一前缀，
    # 而不再使用客户端传来的 current_path（可能含中文「显示名」段，与磁盘实际不一致）。
    parent_disk_path = (
        '' if (parent is None or parent.path in ('', '/')) else parent.path.strip('/')
    )
    parent_id_for_create = parent.id if parent else get_or_create_user_root(user_id).id

    for file in files:
        if file.filename:
            filename0 = file.filename
            filename = unidecode(file.filename)
            filename = secure_filename(filename)

            # 拼接相对路径：基于 parent_disk_path，保证与磁盘真实结构一致
            file_path = f"{parent_disk_path}/{filename}" if parent_disk_path else filename

            # 同 parent + 同文件名视为同一文件（覆盖语义）；用完整 path 兜底匹配旧数据
            existing_file = (
                File.query.filter_by(
                    parent_id=parent_id_for_create,
                    user_id=user_id,
                    is_directory=False,
                    name=filename0,
                ).first()
                or File.query.filter_by(
                    path=file_path,
                    user_id=user_id,
                    is_directory=False,
                ).first()
            )
            print(f"find exist:{filename},count:{existing_file},{parent_id_for_create}")
            file_size = 0
            physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), file_path)
            print(f"physical_path:{physical_path}:{current_app.config['UPLOAD_FOLDER'], str(user_id), file_path}")
            # 确保目录存在
            os.makedirs(os.path.dirname(physical_path), exist_ok=True)
            
            #如果该文件有版本管理
            if existing_file and  len(existing_file.version_str)!=0:
                print(f'cp file to backup,by version {existing_file.version_str}')
                FileService.backup_file(physical_path,existing_file.version_str)
            # 保存文件
            file.save(physical_path)
            file_size = os.path.getsize(physical_path)
            if existing_file:
                   # 如果文件已存在（可能是由于并发操作），更新 size 和 modified_at
                existing_file.size = file_size
                existing_file.modified_at = datetime.utcnow()
                new_file = existing_file
                event_kind = 'modified'
                #上传apk,更新apk的信息,后面new一个线程进行更新
                if get_file_type(filename) == 'applications_android':
                    print("update apk,info")
                    json_apkinfo = parse_apk(physical_path)
                    if existing_file.resource_id :
                        update_resource_fields(existing_file.resource_id,tags=json_apkinfo.get('details'))
                #如果该文件有版本管理
                # if existing_file.version_id>0:
                #     print('cp file to backup,by version id')

                #     pass
            else:
                new_file = File(
                    name=filename0,
                    path=file_path,
                    size=file_size,
                    file_type=get_file_type(filename),
                    is_directory=False,
                    user_id=user_id,
                    parent_id=parent_id_for_create,
                )
                db.session.add(new_file)
                event_kind = 'created'
            db.session.commit()

            uploaded_files.append(new_file.to_dict())
            notify_events.append((new_file, event_kind))

    for f_obj, kind in notify_events:
        webhook_service.notify_file_event(f_obj, kind)

    return jsonify({
        'success': True,
        'files': uploaded_files
    })

# 外部API上传文件
@file_bp.route('/api/external/upload', methods=['POST'])
@api_key_required
def external_upload_file():
    """外部 API 单文件上传（兼容旧版本）。

    multipart/form-data 字段：

    - ``file``        必填，单个文件
    - ``folder_id``   可选，目标文件夹 id（推荐，避免中文路径解析问题）
    - ``path``        可选，目标文件夹相对路径（``folder_id`` 与 ``path`` 二选一）

    行为：**同名自动改名**（追加 ``_1`` / ``_2`` ...），不覆盖。要"覆盖"语义请用
    :py:func:`external_folder_upload` 的 ``overwrite=1``（默认）。
    """
    user = request.user

    if 'file' not in request.files:
        return jsonify({'error': '请选择要上传的文件'}), 400

    file = request.files['file']
    parent, err_resp = _resolve_external_target_folder(user.id)
    if err_resp is not None:
        return err_resp

    # 检查存储空间是否足够
    current_usage = get_user_storage_usage(user.id)
    max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024

    file_content = file.read()
    file.seek(0)

    if current_usage + len(file_content) > max_storage:
        return jsonify({'error': '存储空间不足'}), 400

    if file.filename:
        filename0 = file.filename
        filename = secure_filename(unidecode(filename0)) or 'upload.bin'
        parent_disk_path = (
            '' if (parent is None or parent.path in ('', '/')) else parent.path.strip('/')
        )
        parent_id_for_create = parent.id if parent else None

        # 确保文件名不重复（自动改名）
        base_name, extension = os.path.splitext(filename)
        counter = 1
        while File.query.filter_by(
            name=filename0, parent_id=parent_id_for_create, user_id=user.id,
        ).first() or File.query.filter_by(
            path=(f"{parent_disk_path}/{filename}" if parent_disk_path else filename),
            user_id=user.id,
        ).first():
            filename = f"{base_name}_{counter}{extension}"
            filename0 = f"{base_name}_{counter}{extension}"
            counter += 1
            if counter > 999:
                return jsonify({'error': '同名文件过多，请更换文件名'}), 400

        file_path = f"{parent_disk_path}/{filename}" if parent_disk_path else filename
        physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user.id), file_path)

        os.makedirs(os.path.dirname(physical_path), exist_ok=True)
        file.save(physical_path)
        file_size = os.path.getsize(physical_path)

        new_file = File(
            name=filename0,
            path=file_path,
            size=file_size,
            file_type=get_file_type(filename),
            is_directory=False,
            user_id=user.id,
            parent_id=parent_id_for_create,
        )

        db.session.add(new_file)
        db.session.commit()
        webhook_service.notify_file_event(new_file, 'created')

        return jsonify({
            'success': True,
            'file': new_file.to_dict()
        })

    return jsonify({'error': '上传失败'}), 400


def _resolve_external_target_folder(user_id):
    """根据 form 中的 ``folder_id`` / ``path`` 解析目标文件夹。

    返回 ``(parent_or_None, error_response_or_None)``：
    - 二者都不传 → ``(None, None)`` 落到用户根目录
    - 只能传一个；同时传以 ``folder_id`` 为准
    - 找不到时返回 404 错误响应
    """
    folder_id_raw = (request.form.get('folder_id') or '').strip()
    if folder_id_raw:
        try:
            fid = int(folder_id_raw)
        except ValueError:
            return None, (jsonify({'error': 'folder_id 必须为整数'}), 400)
        folder = File.query.filter_by(
            id=fid, user_id=user_id, is_directory=True
        ).first()
        if not folder:
            return None, (jsonify({'error': f'文件夹不存在或无权访问: folder_id={fid}'}), 404)
        return folder, None

    current_path = (request.form.get('path') or '').strip()
    if current_path.startswith('/'):
        current_path = current_path[1:]
    current_path = _normalize_physical_upload_path(current_path)
    if not current_path:
        # 落到用户根目录，便于 webhook 接收方按"用户根目录的 watch"匹配
        return get_or_create_user_root(user_id), None
    parent = get_directory_by_path(current_path, user_id)
    if not parent:
        return None, (jsonify({'error': f'上传目录不存在: {current_path}'}), 404)
    return parent, None


def _save_one_external_file(file_storage, user_id, parent, overwrite=True):
    """把单个 ``FileStorage`` 持久化到 ``parent`` 下，返回 (File, event_kind, error_str)。

    与 :func:`upload_file` 单文件分支语义一致：
    - 同 parent 同 name 视作"已存在"，``overwrite=True`` 时直接覆盖原文件（modified）；
    - ``overwrite=False`` 时按 ``base_1.ext`` / ``base_2.ext`` 改名新建（created）。
    """
    if not file_storage or not file_storage.filename:
        return None, None, '空文件'

    filename0 = file_storage.filename
    filename = secure_filename(unidecode(filename0)) or 'upload.bin'
    parent_disk_path = (
        '' if (parent is None or parent.path in ('', '/')) else parent.path.strip('/')
    )
    parent_id_for_create = parent.id if parent else None

    file_path = f"{parent_disk_path}/{filename}" if parent_disk_path else filename

    existing = (
        File.query.filter_by(
            parent_id=parent_id_for_create,
            user_id=user_id,
            is_directory=False,
            name=filename0,
        ).first()
        or File.query.filter_by(
            path=file_path,
            user_id=user_id,
            is_directory=False,
        ).first()
    )

    # 不覆盖模式：自动改名
    if existing and not overwrite:
        base_name, extension = os.path.splitext(filename)
        counter = 1
        while True:
            cand = f"{base_name}_{counter}{extension}"
            cand_path = f"{parent_disk_path}/{cand}" if parent_disk_path else cand
            taken = (
                File.query.filter_by(
                    parent_id=parent_id_for_create,
                    user_id=user_id,
                    is_directory=False,
                    name=cand,
                ).first()
                or File.query.filter_by(
                    path=cand_path,
                    user_id=user_id,
                    is_directory=False,
                ).first()
            )
            if not taken:
                filename = cand
                filename0 = cand
                file_path = cand_path
                existing = None
                break
            counter += 1
            if counter > 999:
                return None, None, '同名文件过多，请更换文件名'

    physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), file_path)
    os.makedirs(os.path.dirname(physical_path), exist_ok=True)

    if existing and existing.version_str:
        try:
            FileService.backup_file(physical_path, existing.version_str)
        except Exception as exc:  # noqa: BLE001
            logger.warning('backup_file failed: %s', exc)

    file_storage.save(physical_path)
    file_size = os.path.getsize(physical_path)

    if existing:
        existing.size = file_size
        existing.modified_at = datetime.utcnow()
        new_file = existing
        event_kind = 'modified'
        if get_file_type(filename) == 'applications_android':
            try:
                json_apkinfo = parse_apk(physical_path)
                if existing.resource_id:
                    update_resource_fields(existing.resource_id, tags=json_apkinfo.get('details'))
            except Exception as exc:  # noqa: BLE001
                logger.warning('apk parse skip: %s', exc)
    else:
        new_file = File(
            name=filename0,
            path=file_path,
            size=file_size,
            file_type=get_file_type(filename),
            is_directory=False,
            user_id=user_id,
            parent_id=parent_id_for_create,
        )
        db.session.add(new_file)
        event_kind = 'created'

    db.session.commit()
    return new_file, event_kind, None


@file_bp.route('/api/external/folder', methods=['POST'])
@api_key_required
def external_ensure_folder():
    """外部 API：**幂等**创建文件夹（按 name + parent 二元组定位）。

    multipart/form-data 或 JSON 字段：

    - ``name``         必填，文件夹名（支持中文，物理路径会用 ASCII 安全名）
    - ``parent_id``    可选，父文件夹 id；不传 → 用户根目录
    - ``parent_path``  可选，父文件夹相对路径（``parent_id`` 与 ``parent_path`` 二选一）

    幂等：当 (parent, name) 已存在 ``is_directory=True`` 记录时，直接返回 ``existed=True``
    与该记录；否则新建 DB 行 + 物理目录。
    """
    user = request.user

    # 接受 JSON 或 form
    data = request.get_json(silent=True) or {}
    name0 = (data.get('name') or request.form.get('name') or '').strip()
    parent_id_raw = (
        str(data.get('parent_id') or request.form.get('parent_id') or '').strip()
    )
    parent_path_raw = (data.get('parent_path') or request.form.get('parent_path') or '').strip()

    if not name0:
        return jsonify({'error': 'name 必填'}), 400

    parent = None
    if parent_id_raw:
        try:
            pid = int(parent_id_raw)
        except ValueError:
            return jsonify({'error': 'parent_id 必须为整数'}), 400
        parent = File.query.filter_by(id=pid, user_id=user.id, is_directory=True).first()
        if not parent:
            return jsonify({'error': f'父文件夹不存在或无权访问: parent_id={pid}'}), 404
    elif parent_path_raw:
        p = parent_path_raw[1:] if parent_path_raw.startswith('/') else parent_path_raw
        p = _normalize_physical_upload_path(p)
        parent = get_directory_by_path(p, user.id) if p else get_or_create_user_root(user.id)
        if not parent:
            return jsonify({'error': f'父目录不存在: {parent_path_raw}'}), 404
    else:
        parent = get_or_create_user_root(user.id)

    name_safe = secure_filename(unidecode(name0)) or name0

    existing = (
        File.query.filter_by(
            name=name0, parent_id=parent.id, user_id=user.id, is_directory=True
        ).first()
        or File.query.filter_by(
            name=name_safe, parent_id=parent.id, user_id=user.id, is_directory=True
        ).first()
    )
    if existing:
        try:
            phys = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user.id), existing.path)
            os.makedirs(phys, exist_ok=True)
        except OSError as exc:
            logger.warning('ensure folder physical dir failed: %s', exc)
        return jsonify({'success': True, 'folder': existing.to_dict(), 'existed': True})

    parent_disk_path = '' if parent.path in ('', '/') else parent.path.strip('/')
    folder_path = f'{parent_disk_path}/{name_safe}' if parent_disk_path else name_safe
    phys = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user.id), folder_path)
    try:
        os.makedirs(phys, exist_ok=True)
    except OSError as exc:
        return jsonify({'error': f'创建物理目录失败: {exc}'}), 500

    new_folder = File(
        name=name0,
        path=folder_path,
        size=0,
        file_type='文件夹',
        is_directory=True,
        user_id=user.id,
        parent_id=parent.id,
    )
    db.session.add(new_folder)
    db.session.commit()
    return jsonify({'success': True, 'folder': new_folder.to_dict(), 'existed': False}), 201


@file_bp.route('/api/external/folder-upload', methods=['POST'])
@api_key_required
def external_folder_upload():
    """**推荐**的外部上传入口：往指定文件夹批量上传文件，触发 FolderWatch webhook。

    multipart/form-data 字段：

    - ``folder_id``    可选，目标文件夹 id（推荐）
    - ``path``         可选，目标文件夹相对路径（``folder_id`` / ``path`` 二选一；都不传 → 用户根目录）
    - ``file`` / ``files[]``  必填，单文件 or 多文件
    - ``overwrite``    可选，``"1"``（默认）覆盖同名文件并触发 ``modified``；``"0"`` 自动改名并触发 ``created``

    返回：

    ```json
    {
      "success": true,
      "folder": {"id": 10, "name": "...", "path": "..."},
      "files": [{"id":31,"name":"...","path":"...","event":"created", ...}],
      "failed": [{"filename": "...", "error": "..."}]
    }
    ```

    上传成功后，会按文件夹及其祖先目录上的 :class:`FolderWatch` 配置异步推送 webhook。
    """
    user = request.user
    parent, err_resp = _resolve_external_target_folder(user.id)
    if err_resp is not None:
        return err_resp

    # 收集要上传的文件：兼容三种字段名
    files = []
    if 'files[]' in request.files:
        files.extend(request.files.getlist('files[]'))
    if 'files' in request.files:
        files.extend(request.files.getlist('files'))
    if 'file' in request.files:
        files.append(request.files['file'])
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({'error': '请提供要上传的文件（file / files[]）'}), 400

    overwrite_raw = (request.form.get('overwrite') or '1').strip().lower()
    overwrite = overwrite_raw not in ('0', 'false', 'no', 'off')

    # 存储空间预校验（一次性读出大小，避免逐个上传中途超额）
    total_upload_size = 0
    for fs in files:
        try:
            total_upload_size += len(fs.read())
        finally:
            fs.seek(0)
    current_usage = get_user_storage_usage(user.id)
    max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024
    if current_usage + total_upload_size > max_storage:
        return jsonify({'error': '存储空间不足'}), 400

    ok_files = []
    notify_events = []
    failed = []
    for fs in files:
        try:
            new_file, event_kind, err = _save_one_external_file(
                fs, user.id, parent, overwrite=overwrite
            )
            if err or not new_file:
                failed.append({'filename': fs.filename, 'error': err or '未知错误'})
                continue
            row = new_file.to_dict()
            row['event'] = event_kind
            ok_files.append(row)
            notify_events.append((new_file, event_kind))
        except Exception as exc:  # noqa: BLE001
            logger.exception('external_folder_upload failed: %s', fs.filename)
            db.session.rollback()
            failed.append({'filename': fs.filename, 'error': str(exc)})

    # 异步触发 webhook（在批量 commit 之后一次性发起）
    for f_obj, kind in notify_events:
        webhook_service.notify_file_event(f_obj, kind)

    return jsonify({
        'success': True,
        'folder': {
            'id': parent.id if parent else None,
            'name': parent.name if parent else None,
            'path': parent.path if parent else None,
        },
        'files': ok_files,
        'failed': failed,
    })
#添加保存文件的接口:8-12 yiye
@file_bp.route('/api/save-file', methods=['POST'])
@login_required
def save_file():
    """保存编辑后的文件内容"""
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    # 获取请求数据
    data = request.get_json()
    if not data:
        return jsonify({'error': '请求数据无效'}), 400
    
    file_path = data.get('path')
    content = data.get('content')
    parent_path=data.get('parent_path')
    print(f"save-file:{user_id}->{parent_path+"/"+file_path}-{content}")
    file_path = parent_path+"/"+file_path
    if not file_path:
        return jsonify({'error': '文件路径不能为空'}), 400
    
    if content is None:  # 允许空内容
        return jsonify({'error': '文件内容不能为空'}), 400
    
    # 去除开始的绝对路径
    if file_path.startswith('/'):
        file_path = file_path[1:]
    
    # 查询文件记录
    file_record = File.query.filter_by(
        path=file_path,
        user_id=user_id,
        is_directory=False
    ).first()
    
    if not file_record:
        return jsonify({'error': '文件不存在或无权限访问'}), 404
    
    # 检查文件类型是否支持编辑（只允许编辑文本类文件）
    editable_extensions = ['.txt', '.md', '.json', '.xml', '.html', '.css', '.js', 
                          '.py', '.java', '.c', '.cpp', '.php', '.rb', '.go', 
                          '.sh', '.bat', '.yml', '.yaml', '.ini', '.conf', '.log']
    
    file_extension = os.path.splitext(file_path)[1].lower()
    if file_extension not in editable_extensions:
        return jsonify({'error': '该文件类型不支持编辑'}), 400
    
    # 构建物理路径
    physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), file_path)
    
    # 检查文件是否存在
    if not os.path.exists(physical_path):
        return jsonify({'error': '文件不存在'}), 404
    
    try:
        # 备份原文件（可选）
        backup_path = physical_path + '.backup'
        import shutil
        shutil.copy2(physical_path, backup_path)
        
        # 将内容编码为字节并保存
        content_bytes = content.encode('utf-8')
        
        # 检查新内容大小是否超出限制
        new_size = len(content_bytes)
        old_size = file_record.size
        size_diff = new_size - old_size
        
        # 检查存储空间
        if size_diff > 0:  # 文件变大了
            current_usage = get_user_storage_usage(user_id)
            max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024
            
            if current_usage + size_diff > max_storage:
                # 删除备份文件
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                return jsonify({'error': '存储空间不足'}), 400
        
        # 保存文件
        with open(physical_path, 'wb') as f:
            f.write(content_bytes)
        
        # 更新数据库记录
        file_record.size = new_size
        file_record.updated_at = datetime.utcnow()
        
        # 添加版本记录（可选）
        if hasattr(file_record, 'version'):
            file_record.version = (file_record.version or 0) + 1
        
        db.session.commit()
        
        # 删除备份文件（成功保存后）
        if os.path.exists(backup_path):
            os.remove(backup_path)

        webhook_service.notify_file_event(file_record, 'modified')
        return jsonify({
            'success': True,
            'message': '文件保存成功',
            'file': {
                'name': file_record.name,
                'path': file_record.path,
                'size': file_record.size,
                # 'size_formatted': format_file_size(file_record.size),
                'updated_at': file_record.updated_at.isoformat() if file_record.updated_at else None
            }
        })
        
    except UnicodeDecodeError:
        return jsonify({'error': '文件编码错误，无法保存'}), 400
    except IOError as e:
        # 如果保存失败，尝试恢复备份
        backup_path = physical_path + '.backup'
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, physical_path)
                os.remove(backup_path)
            except:
                pass
        
        return jsonify({'error': f'文件保存失败: {str(e)}'}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'保存失败: {str(e)}'}), 500
# 创建文件夹
@file_bp.route('/api/folder/create', methods=['POST'])
@login_required
def create_folder():
    """
    创建（或确认存在）一个文件夹。语义为**幂等**：
    - 当目标 parent 下已存在同名 ``is_directory=True`` 记录时，直接返回该记录而非 400。
      这样客户端在自动同步时调用 ``ensureRemoteFolder`` 不会因为已存在而失败。
    - 检查与创建用同一个 ``parent_id``：根目录通过 :func:`get_or_create_user_root` 解析，
      避免「检查用 None / 创建用 1」之类的不一致导致同名重复。
    """
    user_id = session.get('user_id')
    data = request.get_json(silent=True) or {}

    folder_name0 = (data.get('name') or '').strip()
    current_path = (data.get('path') or '').strip()
    if current_path.startswith('/'):
        current_path = current_path[1:]

    if not folder_name0:
        return jsonify({'error': '文件夹名称不能为空'}), 400

    # 物理路径用 ASCII 安全名（中文转拼音、特殊字符过滤）；name 字段保留原始可读名
    folder_name_safe = secure_filename(unidecode(folder_name0)) or folder_name0
    print(f"/api/folder/create => name={folder_name0!r} safe={folder_name_safe!r} path={current_path!r}")

    # 解析 parent —— 根目录与子目录都走"取真实记录"的路径，保证 parent_id 唯一
    if current_path:
        parent = get_directory_by_path(current_path, user_id)
        if not parent:
            return jsonify({'error': '目标目录不存在'}), 404
    else:
        parent = get_or_create_user_root(user_id)
    parent_id = parent.id

    # 幂等检查：先按原名匹配，再按安全名匹配（兼容历史上用安全名存的记录）
    existing = (
        File.query.filter_by(
            name=folder_name0, parent_id=parent_id, user_id=user_id, is_directory=True
        ).first()
        or File.query.filter_by(
            name=folder_name_safe, parent_id=parent_id, user_id=user_id, is_directory=True
        ).first()
    )
    if existing:
        # 顺手补建可能丢失的物理目录，避免后续上传写到不存在的路径
        try:
            phys_existing = os.path.join(
                current_app.config['UPLOAD_FOLDER'], str(user_id), existing.path
            )
            os.makedirs(phys_existing, exist_ok=True)
        except OSError as exc:
            logger.warning("ensure existing folder dir failed: %s", exc)
        return jsonify({'success': True, 'folder': existing.to_dict(), 'existed': True})

    # 拼接相对路径：基于 parent.path（已落库的真实磁盘相对路径），
    # 而不是客户端传来的 ``current_path``——后者可能是「显示名链」（含中文），
    # 直接拼接会让 db.path 与磁盘真实路径脱节，导致后续 `get_directory_by_path`
    # 查不到、上传写到不存在的目录。
    parent_disk_path = '' if parent.path in ('', '/') else parent.path.strip('/')
    folder_path = (
        f"{parent_disk_path}/{folder_name_safe}" if parent_disk_path else folder_name_safe
    )

    physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), folder_path)
    try:
        os.makedirs(physical_path, exist_ok=True)
    except OSError as exc:
        logger.error("create physical folder failed: %s", exc)
        return jsonify({'error': f'创建物理目录失败: {exc}'}), 500

    new_folder = File(
        name=folder_name0,
        path=folder_path,
        size=0,
        file_type='文件夹',
        is_directory=True,
        user_id=user_id,
        parent_id=parent_id,
    )
    db.session.add(new_folder)
    db.session.commit()

    return jsonify({'success': True, 'folder': new_folder.to_dict()})

# 删除文件/文件夹
@file_bp.route('/api/files/<int:file_id>', methods=['DELETE'])
@login_required
def delete_file_by_id(file_id):
    """
    删除文件或文件夹。语义为**幂等**：
    - 物理路径不存在/已被外部删除 → 仍然清掉 DB 记录（不再保留"幽灵"项）。
    - 物理删除失败（权限、被占用等）只记 warning 不阻塞 DB 提交，避免 DB 与磁盘
      因为单次错误而长期失同步、用户在 UI 上反复看到删不掉的项。

    实现要点：先把 DB 树一次性删干净并 commit，再做 best-effort 的物理清理；
    这样即便磁盘清理抛错，数据库也不会回滚回来。
    """
    user_id = session.get('user_id')

    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    if not file:
        return jsonify({'error': '文件或文件夹不存在'}), 404

    upload_root = current_app.config['UPLOAD_FOLDER']
    physical_path = os.path.join(upload_root, str(user_id), file.path or '')
    is_directory = bool(file.is_directory)

    # 删除事件需要"原 file 的快照"——commit 之后 ORM 实例已被标记 deleted，
    # 直接访问属性可能抛 DetachedInstanceError；对子项同理。
    delete_snapshots = [webhook_service.make_file_snapshot(file)]
    if is_directory:
        try:
            for child in _collect_descendant_files(file):
                delete_snapshots.append(webhook_service.make_file_snapshot(child))
        except Exception as exc:  # noqa: BLE001
            logger.warning("收集子项快照失败 file_id=%s: %s", file_id, exc)

    # 1) 先收集要清理的物理路径，再删 DB（递归），最后 commit
    physical_targets = [physical_path]
    try:
        if is_directory:
            for child_path in _collect_descendant_physical_paths(file, upload_root, user_id):
                physical_targets.append(child_path)
            delete_directory_recursively(file)
        else:
            db.session.delete(file)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("删除 DB 记录失败 file_id=%s: %s", file_id, e)
        return jsonify({'error': f'删除失败: {e}'}), 500

    for snap in delete_snapshots:
        webhook_service.notify_file_event(snap, 'deleted')

    # 2) 物理清理 —— 任一失败都不影响响应；不存在直接跳过
    physical_errors = []
    if is_directory:
        if os.path.isdir(physical_path):
            try:
                shutil.rmtree(physical_path)
            except OSError as exc:
                physical_errors.append(f"{physical_path}: {exc}")
                logger.warning("rmtree 失败（DB 已清，磁盘残留）: %s", exc)
    else:
        if os.path.exists(physical_path):
            try:
                os.remove(physical_path)
            except OSError as exc:
                physical_errors.append(f"{physical_path}: {exc}")
                logger.warning("os.remove 失败（DB 已清，磁盘残留）: %s", exc)

    resp = {'success': True}
    if physical_errors:
        # 物理残留以非致命警告反馈给前端，便于排查
        resp['warning'] = '数据库已清理；磁盘清理部分失败：' + '; '.join(physical_errors)
    return jsonify(resp)


def _collect_descendant_physical_paths(directory, upload_root, user_id):
    """递归收集目录下**所有后代**对应的磁盘路径（仅用于事后清理参考）。

    主物理目录会被 :func:`shutil.rmtree` 一次性清掉，这里收集子项是为了在
    部分子项 path 与父目录脱节（历史脏数据）时也尽力清掉。
    """
    paths = []
    stack = [directory]
    while stack:
        cur = stack.pop()
        children = File.query.filter_by(parent_id=cur.id).all()
        for c in children:
            if c.path:
                paths.append(os.path.join(upload_root, str(user_id), c.path))
            if c.is_directory:
                stack.append(c)
    return paths


def _collect_descendant_files(directory):
    """递归返回目录下所有后代的 File 实例列表（含子目录与子文件）。

    与 :func:`_collect_descendant_physical_paths` 类似，但返回 ORM 对象，
    用于 :class:`FolderWatch` 删除事件的"批量通知"——逐项通知子项被删，
    避免业务方只知道父目录消失却拿不到子文件的路径。
    """
    out = []
    stack = [directory]
    while stack:
        cur = stack.pop()
        children = File.query.filter_by(parent_id=cur.id).all()
        for c in children:
            out.append(c)
            if c.is_directory:
                stack.append(c)
    return out

# 文件路径删除
@file_bp.route('/api/files/<path:path>', methods=['DELETE'])
@login_required
def delete_file_by_path(path):
    """
    通过路径删除文件或文件夹。前端 ``deleteSelectedFiles`` 走此路由。

    历史上 :func:`get_file_by_path` 全程 ``is_directory=False`` 只能定位文件，
    导致根目录下的文件夹删除返回 404。这里改为先尝试**含目录**的查找，找不到再
    退回原文件查找，最后调用 :func:`delete_file_by_id` 完成删除。
    """
    user_id = session.get('user_id')
    target = _resolve_file_or_dir_by_path(path, user_id)
    if target is None:
        # 兜底：用原 get_file_by_path（仅文件）再尝试一次
        target = get_file_by_path(path, user_id)
    if target is None:
        return jsonify({'error': '文件或文件夹不存在'}), 404
    return delete_file_by_id(target.id)


def _resolve_file_or_dir_by_path(path, user_id):
    """按相对路径解析文件**或**目录记录。优先级：
    1. ``File.path == path`` 全匹配（通常用于已规范化的相对路径）。
    2. 多级路径：去掉末段后用 :func:`get_directory_by_path` 取父目录，再按 name 找子项。
    3. 单段路径：在用户根目录（:func:`get_or_create_user_root`）下按 name 找子项。
    """
    parts = [p for p in path.split('/') if p]
    if not parts:
        return None
    name = parts[-1]

    # 1) path 全匹配（不限定 is_directory，文件/目录都能命中）
    f = File.query.filter_by(path=path, user_id=user_id).first()
    if f:
        return f

    # 2) 多级：父目录 + 名字
    if len(parts) > 1:
        parent = get_directory_by_path('/'.join(parts[:-1]), user_id)
        if parent is None:
            return None
        return File.query.filter_by(
            name=name, parent_id=parent.id, user_id=user_id
        ).first()

    # 3) 单段：根目录下找
    root = get_or_create_user_root(user_id)
    return File.query.filter_by(
        name=name, parent_id=root.id, user_id=user_id
    ).first()

# 文件下载
@file_bp.route('/api/thumbnail/<int:file_id>')
@login_required
def thumbnail_by_id(file_id):
    """
    返回图片/视频的 JPEG 缩略图，磁盘缓存到 ``UPLOAD_FOLDER/.thumbnails/<user_id>/``。

    - 仅对当前登录用户的非目录文件生效；非图片/非视频返回 404。
    - 查询参数 ``size`` 控制最大边（64~512，默认 200）。
    - 缓存键 = ``<file_id>_<size>_<mtime>.jpg``，文件改动后 mtime 变化会自动失效。
    - 视频缩略图依赖 moviepy + ffmpeg；缺失时静默降级为 404，前端会落到默认图标。
    """
    user_id = session.get('user_id')
    file = File.query.filter_by(id=file_id, user_id=user_id, is_directory=False).first()
    if not file:
        abort(404)

    upload_root = current_app.config['UPLOAD_FOLDER']
    physical_path = resolve_upload_physical_path(file.path, file.user_id, upload_root)
    if not os.path.exists(physical_path):
        abort(404)

    ext = os.path.splitext(file.path)[1].lower()
    is_image = ext in FILE_TYPES.get('images', [])
    is_video = ext in FILE_TYPES.get('videos', [])
    if not (is_image or is_video):
        abort(404)

    try:
        size = int(request.args.get('size', 200))
    except (TypeError, ValueError):
        size = 200
    size = max(64, min(size, 512))

    cache_dir = os.path.join(upload_root, '.thumbnails', str(user_id))
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError as exc:
        current_app.logger.warning("thumbnail 缓存目录创建失败: %s", exc)
        abort(500)

    mtime = int(os.path.getmtime(physical_path))
    cache_path = os.path.join(cache_dir, f"{file_id}_{size}_{mtime}.jpg")

    if not os.path.exists(cache_path):
        ok = _generate_thumbnail(physical_path, cache_path, size, is_video)
        if not ok:
            abort(404)

    return send_file(cache_path, mimetype='image/jpeg', max_age=86400)


def _generate_thumbnail(src_path, out_path, max_size, is_video):
    """生成 JPEG 缩略图。成功返回 True，失败返回 False（让接口 404 由前端兜底）。"""
    try:
        from PIL import Image
    except ImportError:
        current_app.logger.warning("Pillow 未安装，无法生成缩略图")
        return False

    try:
        if is_video:
            try:
                # moviepy v2: ``moviepy.video.io.VideoFileClip``；v1: ``moviepy.editor``
                try:
                    from moviepy import VideoFileClip  # type: ignore
                except ImportError:
                    from moviepy.editor import VideoFileClip  # type: ignore
            except Exception as exc:
                current_app.logger.warning("moviepy 不可用，跳过视频缩略图: %s", exc)
                return False
            with VideoFileClip(src_path) as clip:
                duration = float(getattr(clip, 'duration', 0) or 0)
                t = min(1.0, duration / 2) if duration > 0 else 0
                frame = clip.get_frame(t)
            img = Image.fromarray(frame)
        else:
            img = Image.open(src_path)

        try:
            img.thumbnail((max_size, max_size))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.save(out_path, 'JPEG', quality=85, optimize=True)
        finally:
            try:
                img.close()
            except Exception:
                pass
        return True
    except Exception as exc:
        current_app.logger.warning("缩略图生成失败 src=%s: %s", src_path, exc)
        # 半成品文件清理掉，避免下次命中坏缓存
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
        return False


@file_bp.route('/api/download/<int:file_id>')
@login_required
def download_file_by_id(file_id, public=False):
    upload_root = current_app.config['UPLOAD_FOLDER']
    if public:
        file = File.query.filter_by(id=file_id, is_public=True).first()
        session_user = None
    else:
        session_user = session.get('user_id')
        file = File.query.filter_by(id=file_id, user_id=session_user).first()

    if not file:
        row = File.query.filter_by(id=file_id).first()
        if row:
            current_app.logger.warning(
                "download_file_by_id: 无权限或用户不匹配 file_id=%s db_owner=%s session_user=%s public=%s",
                file_id, row.user_id, session_user, public,
            )
        else:
            current_app.logger.warning(
                "download_file_by_id: 数据库无记录 file_id=%s session_user=%s public=%s",
                file_id, session_user, public,
            )
        abort(404)

    physical_path = resolve_upload_physical_path(file.path, file.user_id, upload_root)
    exists = os.path.exists(physical_path)
    current_app.logger.info(
        "download_file_by_id: file_id=%s name=%r db_path=%r file_user=%s phys=%r exists=%s is_dir=%s",
        file_id, file.name, file.path, file.user_id, physical_path, exists, file.is_directory,
    )

    if not exists:
        parent = os.path.dirname(physical_path)
        current_app.logger.warning(
            "download_file_by_id: 磁盘不存在 phys=%r parent=%r parent_isdir=%s",
            physical_path, parent, os.path.isdir(parent),
        )
        abort(404)

    if file.is_directory:
        # 如果是目录，创建一个临时的zip文件
        temp_zip = f"{physical_path}.zip"
        try:
            shutil.make_archive(physical_path, 'zip', physical_path)
            return send_file(temp_zip, as_attachment=True, download_name=f"{file.name}.zip")
        finally:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
    else:
        # 如果是文件，直接下载（磁盘与 DB 不一致时避免 500）
        try:
            return send_file(physical_path, as_attachment=True, download_name=file.name)
        except FileNotFoundError:
            current_app.logger.warning("download_file_by_id: send_file 时 FileNotFoundError phys=%r", physical_path)
            abort(404)

# ---------------------------------------------------------------------------
# 公开下载（plugin.public=True 时通过 HMAC 签名授权, 无需 API Key）
# ---------------------------------------------------------------------------

@file_bp.route('/api/public/download/<int:file_id>')
def public_download_file(file_id):
    """无授权下载入口, 必须由公开插件签发的 URL 才能访问。

    URL 形如 ``/api/public/download/<file_id>?p=<plugin_id>&sig=<hex16>``:

    - 校验 plugin 存在且 ``public=True`` 且 ``enabled=True``
    - 校验 file 属于 plugin owner, 并且在 plugin 绑定的文件夹 (或其子树, 看
      ``include_subdirs``) 范围内 —— 防止用单一插件签名访问 owner 名下其它文件
    - 校验 HMAC 签名 —— 防止有人爆破 file_id

    签名生成/校验逻辑见 :mod:`app.services.plugin_service`。
    """
    from app.models import FolderPlugin
    from app.services.plugin_service import verify_public_file

    plugin_id = request.args.get('p', type=int)
    sig = request.args.get('sig', type=str) or ''
    if not plugin_id or not sig:
        return jsonify({'error': '缺少签名参数 p / sig'}), 400

    plugin = FolderPlugin.query.filter_by(id=plugin_id).first()
    if not plugin or not plugin.public or not plugin.enabled:
        return jsonify({'error': '链接已失效或插件未开启公开访问'}), 403

    if not verify_public_file(plugin, file_id, sig):
        return jsonify({'error': '签名校验失败'}), 403

    file = File.query.filter_by(id=file_id, user_id=plugin.user_id).first()
    if not file or file.is_directory:
        return jsonify({'error': '文件不存在'}), 404

    # 范围检查: 文件必须落在 plugin.folder 的子树里
    bound = File.query.get(plugin.folder_id)
    if not bound or not bound.is_directory:
        return jsonify({'error': '插件绑定文件夹不存在'}), 404
    bound_path = (bound.path or '').strip('/')
    file_path = (file.path or '').strip('/')
    if bound_path:
        if not file_path.startswith(bound_path + '/') and file_path != bound_path:
            return jsonify({'error': '文件不在插件作用域内'}), 403
        # 非递归插件: 只允许直属子项
        if not plugin.include_subdirs and file.parent_id != bound.id:
            return jsonify({'error': '文件不在插件作用域内 (非递归)'}), 403
    else:
        # bound 是根目录: 仅允许直属或全量, 按 include_subdirs 控制
        if not plugin.include_subdirs and file.parent_id != bound.id:
            return jsonify({'error': '文件不在插件作用域内 (非递归)'}), 403

    upload_root = current_app.config['UPLOAD_FOLDER']
    physical_path = resolve_upload_physical_path(file.path, file.user_id, upload_root)
    if not os.path.exists(physical_path):
        return jsonify({'error': '文件不存在'}), 404
    return send_file(physical_path, as_attachment=True, download_name=file.name)


# 外部API下载文件
@file_bp.route('/api/external/download/<int:file_id>')
@api_key_required
def external_download_file(file_id):
    user = request.user
    file = File.query.filter_by(id=file_id, user_id=user.id).first()
   
    if not file:
        return jsonify({'error': '文件不存在'}), 404

    upload_root = current_app.config['UPLOAD_FOLDER']
    physical_path = resolve_upload_physical_path(file.path, file.user_id, upload_root)
    current_app.logger.info(
        "external_download_file: file_id=%s db_path=%r phys=%r exists=%s",
        file_id, file.path, physical_path, os.path.exists(physical_path),
    )
    if not os.path.exists(physical_path):
        current_app.logger.warning("external_download_file: 磁盘不存在 phys=%r", physical_path)
        return jsonify({'error': '文件不存在'}), 404

    if file.is_directory:
        return jsonify({'error': '不支持下载整个目录，请指定具体文件'}), 400
    
    return send_file(physical_path, as_attachment=True, download_name=file.name)


@file_bp.route('/api/download/by-name', methods=['GET'])
@login_required
def download_file_by_name():
    """
    按文件名下载当前用户文件。

    查询参数：
    - name：文件名（必填），可与 URL 编码
    - path：可选，数据库中的相对路径 File.path，用于同名文件消歧
    """
    name = request.args.get('name')
    rel_path = request.args.get('path')
    if not name:
        return jsonify({'error': '缺少查询参数 name（文件名）'}), 400
    name = unquote(name)
    user_id = session.get('user_id')
    if rel_path:
        rel_path = unquote(rel_path)
        fobj = File.query.filter_by(
            user_id=user_id, path=rel_path, is_directory=False
        ).first()
        if not fobj:
            return jsonify({'error': '未找到该路径对应的文件'}), 404
        if fobj.name != name:
            return jsonify({
                'error': '文件名与 path 不一致',
                'expected_name': fobj.name,
            }), 400
        return download_file_by_id(fobj.id)
    matches = File.query.filter_by(
        user_id=user_id, name=name, is_directory=False
    ).all()
    if len(matches) == 1:
        return download_file_by_id(matches[0].id)
    if len(matches) == 0:
        return jsonify({'error': '未找到该文件'}), 404
    return jsonify({
        'error': '存在多个同名文件，请附加 path 参数指定 File.path（相对路径）',
        'matches': [{'id': m.id, 'name': m.name, 'path': m.path} for m in matches],
    }), 409


@file_bp.route('/api/download/<path:path>')
@login_required
def download_file_by_path(path):
    user_id = session.get('user_id')
    
    file = get_file_by_path(path, user_id)
    
    if not file:
        abort(404)
    
    return download_file_by_id(file.id)
@file_bp.route('/api/external/download/<path:path>')
# @login_required
def ex_download_file_by_path(path):
    user_id =  request.args.get('user_id')
    version = request.args.get('version')
    file = get_file_by_path(path, user_id)
    print(f"download {user_id}==>{version} by version:{path}")
    if not file:
        abort(404)
   
    if file.version_str == version:
        return download_file_from(user_id,file)
    else:
        return jsonify({'msg':"input args error :user_id,version must"})

# 文件预览
@file_bp.route('/api/preview/<int:file_id>')
@login_required
def preview_file_by_id(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    if not file:
        return jsonify({'error': '文件不存在'}), 404

    if file.is_directory:
        return jsonify({'error': '不能预览文件夹'}), 400

    upload_root = current_app.config['UPLOAD_FOLDER']
    physical_path = resolve_upload_physical_path(file.path, file.user_id, upload_root)
    current_app.logger.info(
        "preview_file_by_id: file_id=%s db_path=%r phys=%r exists=%s",
        file_id, file.path, physical_path, os.path.exists(physical_path),
    )
    if not os.path.exists(physical_path):
        current_app.logger.warning("preview_file_by_id: 磁盘不存在 phys=%r", physical_path)
        return jsonify({'error': '文件不存在'}), 404
    
    file_ext = os.path.splitext(file.name)[1].lower()
    
    # 检测MIME类型
    mime_type, _ = mimetypes.guess_type(physical_path)
    
    # 基本文件信息
    file_info = {
        'name': file.name,
        'size': file.size,
        'size_formatted': format_size(file.size),
        'type': mime_type or '未知类型'
    }
    
    # 处理图片预览
    if file_ext in FILE_TYPES['images']:
        preview_url = f"/api/serve/{file_id}"
        return jsonify({
            'type': 'image',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理视频预览
    elif file_ext in FILE_TYPES['videos']:
        preview_url = f"/api/serve/{file_id}"
        print(f"preview_url:{preview_url}")
        return jsonify({
            'type': 'video',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理音频预览
    elif file_ext in FILE_TYPES['audio']:
        preview_url = f"/api/serve/{file_id}"
        return jsonify({
            'type': 'audio',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理PDF预览
    elif file_ext == '.pdf':
        preview_url = f"/api/serve/{file_id}"
        return jsonify({
            'type': 'pdf',
            'url': preview_url,
            'info': file_info
        })
    
    # 处理文本/代码文件预览
    elif file_ext in ['.txt', '.md', '.html', '.css', '.js', '.json', '.xml', '.py', '.java', '.c', '.cpp', '.php', '.rb', '.go']:
        try:
            # 限制读取文件大小，防止过大文件占用内存
            max_size = 1024 * 1024  # 1MB
            if file.size > max_size:
                content = "文件过大，仅显示前1MB内容...\n\n"
                with open(physical_path, 'r', encoding='utf-8', errors='replace') as f:
                    content += f.read(max_size)
            else:
                with open(physical_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            
            return jsonify({
                'type': 'text',
                'content': content,
                'extension': file_ext[1:] if file_ext else '',  # 移除点号
                'info': file_info
            })
        except Exception as e:
            return jsonify({
                'type': 'error',
                'error': f'无法读取文件内容: {str(e)}',
                'info': file_info
            })
    
    # 不支持的文件类型
    else:
        return jsonify({
            'type': 'unsupported',
            'info': file_info
        })

@file_bp.route('/api/preview/<path:path>')
@login_required
def preview_file_by_path(path):
    print(f"preview:{path}")
    user_id = session.get('user_id')
    
    file = get_file_by_path(path, user_id)
    print(f"preview_file_by_path:{path,file}")
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    return preview_file_by_id(file.id)

# 外部API预览文件
@file_bp.route('/api/external/preview/<int:file_id>')
@api_key_required
def external_preview_file(file_id):
    user = request.user
    file = File.query.filter_by(id=file_id, user_id=user.id).first()
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    # 使用已有的预览函数
    response = preview_file_by_id(file.id)
    
    # 修改URL，以便外部访问
    if isinstance(response, tuple):
        return response
    
    data = response.get_json()
    if 'url' in data:
        data['url'] = data['url'].replace('/api/serve/', '/api/external/serve/')
        return jsonify(data)
    
    return response

# 文件服务
@file_bp.route('/api/serve/<int:file_id>')
@login_required
def serve_file_by_id(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file or file.is_directory:
        abort(404)

    upload_root = current_app.config['UPLOAD_FOLDER']
    physical_path = resolve_upload_physical_path(file.path, file.user_id, upload_root)
    current_app.logger.info(
        "serve_file_by_id: file_id=%s phys=%r exists=%s",
        file_id, physical_path, os.path.exists(physical_path),
    )
    if not os.path.exists(physical_path):
        current_app.logger.warning("serve_file_by_id: 磁盘不存在 phys=%r", physical_path)
        abort(404)

    # 获取MIME类型
    mime_type, _ = mimetypes.guess_type(physical_path)

    return send_file(physical_path, mimetype=mime_type)

# 外部API服务文件
@file_bp.route('/api/external/serve/<int:file_id>')
@api_key_required
def external_serve_file(file_id):
    user = request.user
    file = File.query.filter_by(id=file_id, user_id=user.id).first()

    if not file or file.is_directory:
        abort(404)

    upload_root = current_app.config['UPLOAD_FOLDER']
    physical_path = resolve_upload_physical_path(file.path, file.user_id, upload_root)
    if not os.path.exists(physical_path):
        current_app.logger.warning("external_serve_file: 磁盘不存在 phys=%r", physical_path)
        abort(404)
    
    # 获取MIME类型
    mime_type, _ = mimetypes.guess_type(physical_path)
    
    return send_file(physical_path, mimetype=mime_type)

# 文件共享
@file_bp.route('/api/files/<int:file_id>/share', methods=['POST'])
@login_required
def share_file(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    # 生成或更新共享ID
    if not file.public_share_id:
        file.generate_share_id()
    
    file.is_public = True
    db.session.commit()
    
    share_url = url_for('file.access_shared_file', share_id=file.public_share_id, _external=True)
    
    return jsonify({
        'success': True,
        'share_id': file.public_share_id,
        'share_url': share_url
    })

@file_bp.route('/api/files/<int:file_id>/unshare', methods=['POST'])
@login_required
def unshare_file(file_id):
    user_id = session.get('user_id')
    
    file = File.query.filter_by(id=file_id, user_id=user_id).first()
    
    if not file:
        return jsonify({'error': '文件不存在'}), 404
    
    file.is_public = False
    db.session.commit()
    
    return jsonify({'success': True})

# 获取存储信息
@file_bp.route('/api/storage')
@login_required
def get_storage():
    user_id = session.get('user_id')
    
    total_size = get_user_storage_usage(user_id)
    max_size = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024  # 将GB转换为字节
    
    used_gb = total_size / (1024 * 1024 * 1024)
    percentage = (total_size / max_size) * 100 if max_size > 0 else 0
    
    return jsonify({
        'used': total_size,
        'used_formatted': format_size(total_size),
        'total': max_size,
        'total_formatted': f"{current_app.config['MAX_STORAGE_GB']} GB",
        'used_gb': round(used_gb, 2),
        'percentage': round(percentage, 2)
    })
# 获取文件类型
_TYPE_DISPLAY_NAMES = {
    'applications_android': '应用程序 (Android)',
    'applications_linux': '应用程序 (Linux)',
    'applications_windows': '应用程序 (Windows)',
}


@file_bp.route('/api/get_file_type', methods=['GET'])
@login_required
def get_file_type():
    file_types = list(FILE_TYPES.keys())
    types_list = []
    for i, tname in enumerate(file_types):
        types_list.append({'id': i, 'name': _TYPE_DISPLAY_NAMES.get(tname, tname)})

    return jsonify({'success': True, 'types': types_list})

# 新建文件接口（支持富文本/Markdown/HTML/纯文本等）
@file_bp.route('/api/newfile', methods=['POST'])
@login_required
def new_file():
    try:
        data = request.json or {}
        user_id = session.get('user_id')

        file_name = (data.get('name') or '').strip()
        if not file_name:
            return jsonify({'success': False, 'message': '文件名不能为空'}), 400

        content = data.get('content', '')
        if not isinstance(content, str):
            content = str(content)

        # 兼容历史前端：parent_id 字段实为父级路径字符串
        parent_path = (data.get('parent_id') or '').strip() or '/'
        if not parent_path.startswith('/'):
            parent_path = '/' + parent_path
        normalized_parent = '/'.join(p for p in parent_path.split('/') if p)
        # 分类视图等虚拟路径回落到根目录
        if normalized_parent in FILE_TYPES or normalized_parent in ('shared', 'recent', 'search', 'all', ''):
            normalized_parent = ''

        # 解析父目录
        if normalized_parent:
            parent = get_directory_by_path(normalized_parent, user_id)
            if not parent:
                return jsonify({'success': False, 'message': '父目录不存在'}), 404
            parent_id = parent.id
        else:
            root = get_directory_by_path('', user_id)
            parent_id = root.id if root else 1

        # 防止使用非法字符 / 路径穿越
        if any(ch in file_name for ch in ('/', '\\')) or file_name in ('.', '..'):
            return jsonify({'success': False, 'message': '文件名包含非法字符'}), 400

        # 同目录下重名 -> 自动追加 _1 / _2 ...（按 path 列）
        base_name, ext = os.path.splitext(file_name)
        candidate = file_name
        counter = 1
        rel_dir = normalized_parent  # 用于构造 File.path（与上传一致）
        while True:
            try_path = os.path.join(rel_dir, candidate) if rel_dir else candidate
            if try_path.startswith('/'):
                try_path = try_path[1:]
            existed = File.query.filter_by(
                path=try_path, user_id=user_id, is_directory=False
            ).first()
            if not existed:
                file_name = candidate
                tpath = try_path
                break
            candidate = f"{base_name}_{counter}{ext}"
            counter += 1
            if counter > 999:
                return jsonify({'success': False, 'message': '同名文件过多，请更换文件名'}), 400

        # 物理路径
        physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), tpath)
        os.makedirs(os.path.dirname(physical_path), exist_ok=True)

        # 校验存储空间
        content_bytes = content.encode('utf-8')
        current_usage = get_user_storage_usage(user_id)
        max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024
        if current_usage + len(content_bytes) > max_storage:
            return jsonify({'success': False, 'message': '存储空间不足'}), 400

        # 写入磁盘
        with open(physical_path, 'wb') as f:
            f.write(content_bytes)

        file_size = os.path.getsize(physical_path)
        # 优先按扩展名推断；前端传入的 file_type 仅作回退
        inferred_type = get_file_type_info(os.path.splitext(file_name)[1].lower())
        if inferred_type == 'others':
            inferred_type = (data.get('file_type') or 'documents')

        new_file = File(
            name=file_name,
            path=tpath,
            size=file_size,
            file_type=inferred_type,
            is_directory=False,
            user_id=user_id,
            parent_id=parent_id,
            is_public=False
        )

        db.session.add(new_file)
        db.session.commit()
        webhook_service.notify_file_event(new_file, 'created')

        return jsonify({
            'success': True,
            'message': '文件创建成功',
            'file_id': new_file.id,
            'name': new_file.name,
            'path': new_file.path,
            'file_type': new_file.file_type,
            'size': new_file.size,
            'parent_path': normalized_parent
        })

    except Exception as e:
        logger.exception('new_file failed')
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

# 添加链接接口
@file_bp.route('/api/add_linker', methods=['POST'])
@login_required
def add_linker():
    try:
        data = request.json
        
        # 从会话中获取用户ID（假设已经实现了认证）
        user_id = session.get('user_id')  # 示例用户ID
        
        # 创建链接记录
        new_link = File(
            name=data['name'],
            path=data['path'],  # 链接URL保存在path字段
            size=0,  # 链接没有大小
            file_type="linker",
            is_directory=0,
            user_id=user_id,
            parent_id=1,
            is_public=False
        )
        
        db.session.add(new_link)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': '链接添加成功',
            'link_id': new_link.id
        })
        
    except Exception as e:
        db.session
# 辅助函数
def get_file_by_path(path, user_id):
    """
    根据相对路径解析文件记录。DB 中 File.path 多为「分类/文件名」等完整相对路径。
    URL 仅含文件名时，用 name 匹配（多同名时取 id 较大的一条）。
    """
    parts = [p for p in path.split('/') if p]
    if not parts:
        return None
    filename = parts[-1]
    print(f"get_file_by_path:{filename!r} full={path!r}")

    # 1) 与 File.path 完全一致（推荐：传完整相对路径）
    f = File.query.filter_by(
        path=path, user_id=user_id, is_directory=False
    ).first()
    if f:
        return f

    # 2) 多级路径：父目录 + 文件名
    if len(parts) > 1:
        parent_path = '/'.join(parts[:-1])
        parent = get_directory_by_path(parent_path, user_id)
        if not parent:
            return None
        return File.query.filter_by(
            name=filename,
            parent_id=parent.id,
            user_id=user_id,
            is_directory=False,
        ).first()

    # 3) 仅一段（如仅有 apk 名）：按 name 匹配非目录文件
    return (
        File.query.filter_by(user_id=user_id, is_directory=False, name=filename)
        .order_by(File.id.desc())
        .first()
    )
def create_directory(name, parent_id, user_id):
    """创建目录记录"""
    newf = File(
        name=name,
        parent_id=parent_id,
        path=name,
        user_id=user_id,
        size=0,
        file_type='文件夹',
        is_directory=True,
        created_at= datetime.now(timezone.utc)
    ) 
    #将root添加到系统中
    db.session.add(newf)
    db.session.commit()
    return newf


def get_or_create_user_root(user_id):
    """
    取得（或按需创建）当前用户的"根目录"占位记录。

    历史上 :func:`get_files` 把根目录列表写死为 ``parent_id=1``，但实际每个用户
    应有各自的 root（``parent_id=0`` 的占位记录）。这个函数统一约定：
    - 根目录 = ``user_id=user_id, is_directory=True, parent_id=0`` 的记录
    - 不存在则创建一条 name='/'、path='/'、parent_id=0 的占位

    所有"根目录下创建/查找/删除"必须经此函数取得 ``parent_id``，
    避免「检查用 None / 创建用 1」之类的不一致引发同名重复。
    """
    root = (
        File.query
        .filter_by(user_id=user_id, is_directory=True, parent_id=0)
        .order_by(File.id.asc())
        .first()
    )
    if root:
        return root
    return create_directory('/', 0, user_id)

def get_directory_by_path(path: str, user_id):
    """根据路径获取目录。

    路径解析按下列顺序兜底（任一命中即返回）：

    1. ``File.path`` 字段**精确匹配**：兼容内部已规范化（ASCII 化）的相对路径，
       例如导入流程内部拼出的 ``Yue_Shun_De_Redmi-K70-Ultra-e5f4/Pictures``。
    2. 从用户根目录（:func:`get_or_create_user_root`）起，按 **``name`` 逐段匹配**：
       客户端自动同步走 ``ensureRemoteFolder(name, parentPath)``，``parentPath`` 是
       「显示名」拼接（如 ``岳顺的Redmi-K70-Ultra-e5f4/Pictures``），DB 里 ``path`` 字段
       已被 unidecode/secure_filename 改写过，靠 path 全匹配会失败 → 退化到 name 链解析。
    """
    parts = [p for p in path.split('/') if p]

    if not parts:
        return get_or_create_user_root(user_id)

    # 1) path 字段精确匹配
    d = File.query.filter_by(path=path, user_id=user_id, is_directory=True).first()
    if d:
        print(f"get_directory_by_path: path={path!r} hit by path-field id={d.id}")
        return d

    # 2) name 链逐级解析（从 root 起一段一段往下找）
    current = get_or_create_user_root(user_id)
    for part in parts:
        nxt = (
            File.query.filter_by(
                name=part,
                parent_id=current.id,
                user_id=user_id,
                is_directory=True,
            )
            .order_by(File.id.asc())
            .first()
        )
        if nxt is None:
            print(f"get_directory_by_path: path={path!r} miss at segment={part!r} (parent_id={current.id})")
            return None
        current = nxt
    print(f"get_directory_by_path: path={path!r} hit by name-chain id={current.id}")
    return current

def delete_directory_recursively(directory):
    """递归删除目录及其内容"""
    # 删除子文件和文件夹
    children = File.query.filter_by(parent_id=directory.id).all()
    for child in children:
        if child.is_directory:
            delete_directory_recursively(child)
        else:
            db.session.delete(child)
    
    # 删除目录本身
    db.session.delete(directory)

def get_user_storage_usage(user_id):
    """获取用户已使用的存储空间"""
    result = db.session.query(db.func.sum(File.size)).filter_by(user_id=user_id, is_directory=False).scalar()
    return result or 0
#通过扩展名，获取文件类型
def get_file_type_info(ext):
    for type_name, extensions in FILE_TYPES.items():
        if ext in extensions:
            return type_name
    return 'others'
def get_file_type(filename):
    """获取文件类型"""
    ext = os.path.splitext(filename)[1].lower()
    return get_file_type_info(ext)
    # for type_name, extensions in FILE_TYPES.items():
    #     if ext in extensions:
    #         return {
    #             'images': '图片',
    #             'documents': '文档',
    #             'videos': '视频',
    #             'audio': '音频',
    #             'archives': '压缩包',
    #             'code': '代码文件'
    #         }.get(type_name, '其它')
    #         # return type_name
    
    # return '其它'

def get_file_icon(path,file=None):
    # if file.id 
    if file.resource_id:
        T,path= get_resource_poster(file.resource_id)
        if T:
            return path
    """获取文件图标"""
    if path.startswith("http://") or  path.startswith("https://"):
        return 'bi-link-45deg'
    if os.path.isdir(path) or path.endswith('/'):
        return 'bi-folder'
    
    ext = os.path.splitext(os.path.basename(path))[1].lower()
    
    # 图片类型
    if ext in FILE_TYPES['images']:
        return 'bi-file-image'
    
    # 文档类型
    if ext in FILE_TYPES['documents']:
        if ext == '.pdf':
            return 'bi-file-pdf'
        elif ext in ['.doc', '.docx']:
            return 'bi-file-word'
        elif ext in ['.xls', '.xlsx']:
            return 'bi-file-excel'
        elif ext in ['.ppt', '.pptx']:
            return 'bi-file-ppt'
        elif ext == '.md':
            return 'bi-markdown'
        else:
            return 'bi-file-text'
    
    # 视频类型
    if ext in FILE_TYPES['videos']:
        return 'bi-file-play'
    
    # 音频类型
    if ext in FILE_TYPES['audio']:
        return 'bi-file-music'
    
    # 压缩文件类型
    if ext in FILE_TYPES['archives']:
        return 'bi-file-zip'
    
    # 代码文件类型
    if ext in FILE_TYPES['code']:
        return 'bi-file-code'
    
    if ext in FILE_TYPES.get('applications_android', ()):
        return 'bi-google-play'
    if ext in FILE_TYPES.get('applications_linux', ()):
        return 'bi-terminal'
    if ext in FILE_TYPES.get('applications_windows', ()):
        return 'bi-windows'
    
    # 默认图标
    return 'bi-file'

def format_size(size):
    """格式化文件大小"""
    if size == 0:
        return "0 B"
    
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size, 1024)))
    i = min(i, len(units) - 1)
    
    size = size / (1024 ** i)
    return f"{size:.2f} {units[i]}"
@file_bp.route('/api/parse_file_info', methods=['POST'])
@login_required
def parse_file_info():
    """API端点，用于解析文件并返回信息"""
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({'error': '请求体中缺少 "path" 字段'}), 400
    user_id = session.get('user_id')
    relative_path = data['path']
    #双击生成视频的最后一帧
    is_dbclick = data['is_dbclick']
    file_id = data.get('id')
    print(f"pparse_file_info:{relative_path}:{is_dbclick}:{file_id}")
    try:
        file_details = get_file_details(relative_path,user_id,is_dbclick,file_id)

        return jsonify(file_details)
    except ValueError as e:
        return jsonify({'error': str(e)}), 403 # 403 Forbidden for illegal path
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404 # 404 Not Found
    except Exception as e:
        # 捕获所有其他意外错误
        print(f"Unhandled error in parse_file_info: {e}")
        return jsonify({'error': '服务器内部错误，无法解析文件'}), 500
 # 添加断点续传相关路由
@file_bp.route('/api/upload/check', methods=['POST'])
@login_required
def check_breakpoint():
    """检查文件断点信息"""
    user_id = session.get('user_id')
    data = request.json
    
    file_name = data.get('fileName')
    file_size = data.get('fileSize')
    file_hash = data.get('fileHash')
    path = data.get('path', '')
    
    # 构建临时文件路径
    temp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'temp', str(user_id), file_hash)
    
    uploaded_chunks = []
    uploaded_bytes = 0
    
    if os.path.exists(temp_dir):
        # 获取已上传的分片
        for chunk_file in os.listdir(temp_dir):
            if chunk_file.startswith('chunk_'):
                chunk_index = int(chunk_file.split('_')[1])
                uploaded_chunks.append(chunk_index)
                chunk_path = os.path.join(temp_dir, chunk_file)
                uploaded_bytes += os.path.getsize(chunk_path)
    
    return jsonify({
        'uploadedChunks': uploaded_chunks,
        'uploadedBytes': uploaded_bytes,
        'fileHash': file_hash
    })

@file_bp.route('/api/upload/chunk', methods=['POST'])
@login_required
def upload_chunk():
    """上传文件分片"""
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    chunk = request.files.get('chunk')
    chunk_index = int(request.form.get('chunkIndex'))
    chunks = int(request.form.get('chunks'))
    file_name = request.form.get('fileName')
    file_hash = request.form.get('fileHash')
    path = request.form.get('path', '')
    
    if not chunk:
        return jsonify({'error': '分片数据不存在'}), 400
    
    # 创建临时目录存储分片
    temp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'temp', str(user_id), file_hash)
    os.makedirs(temp_dir, exist_ok=True)
    
    # 保存分片
    chunk_path = os.path.join(temp_dir, f'chunk_{chunk_index}')
    chunk.save(chunk_path)
    
    # 记录分片信息
    info_file = os.path.join(temp_dir, 'info.json')
    info = {}
    if os.path.exists(info_file):
        with open(info_file, 'r') as f:
            info = json.load(f)
    
    info.update({
        'fileName': file_name,
        'chunks': chunks,
        'path': path,
        'fileHash': file_hash,
        'lastUpdate': datetime.utcnow().isoformat()
    })
    
    with open(info_file, 'w') as f:
        json.dump(info, f)
    
    return jsonify({
        'success': True,
        'chunkIndex': chunk_index,
        'message': f'分片 {chunk_index + 1}/{chunks} 上传成功'
    })

@file_bp.route('/api/upload/merge', methods=['POST'])
@login_required
def merge_chunks():
    """合并文件分片"""
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    
    data = request.json
    file_name = data.get('fileName')
    file_hash = data.get('fileHash')
    chunks = data.get('chunks')
    path = data.get('path', '')
    file_size = data.get('fileSize')
    
    # 检查存储空间
    current_usage = get_user_storage_usage(user_id)
    max_storage = current_app.config['MAX_STORAGE_GB'] * 1024 * 1024 * 1024
    
    if current_usage + file_size > max_storage:
        return jsonify({'error': '存储空间不足'}), 400
    
    # 临时目录路径
    temp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'temp', str(user_id), file_hash)
    
    if not os.path.exists(temp_dir):
        return jsonify({'error': '分片文件不存在'}), 404
    
    # 检查所有分片是否都已上传
    for i in range(chunks):
        chunk_path = os.path.join(temp_dir, f'chunk_{i}')
        if not os.path.exists(chunk_path):
            return jsonify({'error': f'分片 {i} 不存在'}), 400
    
    # 处理文件路径（分类视图等虚拟路径按根目录处理）
    if path.startswith('/'):
        path = path[1:]
    path = _normalize_physical_upload_path(path)
    
    # 获取父目录
    parent = None
    if path or path == '':
        parent = get_directory_by_path(path, user_id)
        if not parent:
            return jsonify({'error': '上传目录不存在'}), 404
    
    # 安全的文件名处理
    filename0 = file_name
    filename = unidecode(file_name)
    filename = secure_filename(filename)

    # 基于 parent.path（DB 中的真实磁盘相对路径）拼路径，避免客户端 path 是中文「显示名」时
    # 与磁盘真实结构脱节。
    parent_disk_path = (
        '' if (parent is None or parent.path in ('', '/')) else parent.path.strip('/')
    )
    parent_id_for_create = parent.id if parent else get_or_create_user_root(user_id).id
    file_path = f"{parent_disk_path}/{filename}" if parent_disk_path else filename

    physical_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(user_id), file_path)

    # 确保目录存在
    os.makedirs(os.path.dirname(physical_path), exist_ok=True)
    # 检查文件是否已存在（同 parent + 同显示名 / 兜底用完整 path 匹配）
    existing_file = (
        File.query.filter_by(
            parent_id=parent_id_for_create,
            user_id=user_id,
            is_directory=False,
            name=filename0,
        ).first()
        or File.query.filter_by(
            path=file_path,
            user_id=user_id,
            is_directory=False,
        ).first()
    )
    #如果该文件有版本管理
    if existing_file and  len(existing_file.version_str)!=0:
        print(f'cp largefile to backup,by version {existing_file.version_str}')
        FileService.backup_file(physical_path,existing_file.version_str)
    # 合并分片
    with open(physical_path, 'wb') as output_file:
        for i in range(chunks):
            chunk_path = os.path.join(temp_dir, f'chunk_{i}')
            with open(chunk_path, 'rb') as chunk_file:
                output_file.write(chunk_file.read())
    
    # 验证文件大小
    actual_size = os.path.getsize(physical_path)
    
    # 清理临时文件
    shutil.rmtree(temp_dir)
    
   
    
    if existing_file:
        # 更新现有文件
        existing_file.size = actual_size
        existing_file.modified_at = datetime.utcnow()
        new_file = existing_file
        event_kind = 'modified'
        
        # 处理APK文件
        if get_file_type(filename) == 'applications_android':
            json_apkinfo = parse_apk(physical_path)
            if existing_file.resource_id:
                update_resource_fields(existing_file.resource_id, tags=json_apkinfo.get('details'))
    else:
        new_file = File(
            name=filename0,
            path=file_path,
            size=actual_size,
            file_type=get_file_type(filename),
            is_directory=False,
            user_id=user_id,
            parent_id=parent_id_for_create,
        )
        db.session.add(new_file)
        event_kind = 'created'

    db.session.commit()
    webhook_service.notify_file_event(new_file, event_kind)

    return jsonify({
        'success': True,
        'file': new_file.to_dict(),
        'message': '文件上传成功'
    })