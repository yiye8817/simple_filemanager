"""ManagedService 业务层。

集中处理 4 类操作，便于路由层薄薄一层、单元测试也好写：

1. **frpc.toml 解析**：从 ``[[proxies]]`` 数组中读出可用的 ``(name, localPort, remotePort)``，
   以及全局 ``serverAddr``。
2. **端口检查**：启动前检测 localPort 是否被占用、是否在 frpc 白名单内。
3. **安全解压 tar.gz**：阻断 path traversal（绝对路径 / 上跳）、阻断符号链接逃逸。
4. **进程启停**：用 ``subprocess.Popen`` 拉起 ``run.sh <localPort>``，stdout/stderr
   重定向到 ``run.log``；通过 PID 做活性探测；停止时优先 SIGTERM，超时再 SIGKILL。
"""
from __future__ import annotations

import errno
import logging
import os
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_FRPC_PATH = '/etc/frpc.toml'

# 单文件归档体积上限（默认 200MB），避免被传超大文件占爆磁盘
DEFAULT_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024

# 解压后所有文件总大小上限（防 zip-bomb 式爆膨胀）
DEFAULT_MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024

# Popen 启动后默认等待这么久，让 run.sh 至少打印一次错误便于即时反馈
START_GRACE_SECONDS = 0.4

# stop 时给 SIGTERM 多少秒优雅退出，超时再 SIGKILL
STOP_GRACE_SECONDS = 5.0


# ---------------------------------------------------------------------------
# frpc.toml 解析
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrpcProxy:
    name: str
    local_port: int
    remote_port: int
    proxy_type: str | None = None


@dataclass(frozen=True)
class FrpcConfig:
    server_addr: str | None
    server_port: int | None
    proxies: list[FrpcProxy]


def parse_frpc_toml(path: str = DEFAULT_FRPC_PATH) -> FrpcConfig:
    """读取并解析 frpc 配置；文件不存在或格式异常时抛 ``FileNotFoundError`` / ``ValueError``。"""
    if not os.path.isfile(path):
        raise FileNotFoundError(f'frpc 配置不存在: {path}')

    with open(path, 'rb') as fh:
        data = tomllib.load(fh)

    server_addr = data.get('serverAddr') or data.get('server_addr')
    server_port = data.get('serverPort') or data.get('server_port')

    raw_proxies = data.get('proxies') or []
    if not isinstance(raw_proxies, list):
        raise ValueError('frpc.toml 中 proxies 字段必须为数组')

    proxies: list[FrpcProxy] = []
    for i, p in enumerate(raw_proxies):
        if not isinstance(p, dict):
            continue
        name = (p.get('name') or '').strip()
        local_port = p.get('localPort') or p.get('local_port')
        remote_port = p.get('remotePort') or p.get('remote_port')
        if not name or not local_port or not remote_port:
            logger.warning('frpc proxies[%d] 缺少 name/localPort/remotePort，已跳过', i)
            continue
        proxies.append(FrpcProxy(
            name=name,
            local_port=int(local_port),
            remote_port=int(remote_port),
            proxy_type=str(p.get('type') or '').strip() or None,
        ))

    return FrpcConfig(
        server_addr=str(server_addr) if server_addr else None,
        server_port=int(server_port) if server_port else None,
        proxies=proxies,
    )


def find_proxy(cfg: FrpcConfig, local_port: int, remote_port: int) -> FrpcProxy | None:
    """按 (localPort, remotePort) 在 frpc proxies 中精确匹配；用于校验请求合法性。"""
    for p in cfg.proxies:
        if p.local_port == local_port and p.remote_port == remote_port:
            return p
    return None


# ---------------------------------------------------------------------------
# 自动发现正在运行的 frpc 进程 + 推断配置文件路径
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrpcInstance:
    """一台正在运行的 frpc 进程 + 它对应的 ``FrpcConfig``。

    ``cmdline``  保留原始命令行, 给前端展示 / 调试。
    ``cfg`` 解析失败 (找不到 toml / 格式错) 时仍然返回, 此时 ``error`` 非空。
    """
    pid: int
    cmdline: list[str]
    config_path: str | None
    cfg: FrpcConfig | None
    error: str | None = None


_FRPC_CONFIG_FLAGS = ('-c', '--config', '-config')


def _extract_frpc_config_path(cmdline: list[str]) -> str | None:
    """从命令行参数列表里抽出 ``frpc -c /path/to/frpc.toml`` 的 path。

    兼容这些形态:
    - ``frpc -c /etc/frpc.toml``
    - ``frpc --config /etc/frpc.toml``
    - ``frpc --config=/etc/frpc.toml``
    - ``frpc -c=/etc/frpc.toml`` (个别脚本写法)

    都没匹配上时 → ``None`` (调用方再 fallback 到 ``DEFAULT_FRPC_PATH``)。
    """
    if not cmdline:
        return None
    n = len(cmdline)
    for i, tok in enumerate(cmdline):
        for flag in _FRPC_CONFIG_FLAGS:
            if tok == flag and i + 1 < n:
                return cmdline[i + 1]
            prefix = flag + '='
            if tok.startswith(prefix) and len(tok) > len(prefix):
                return tok[len(prefix):]
    return None


def discover_frpc_processes(fallback_path: str | None = None) -> list[FrpcInstance]:
    """扫所有正在运行的 frpc 进程, 解析它们各自的 toml 配置。

    实现策略:
    1. 优先用 ``psutil`` 拿干净的 cmdline / pid。
    2. ``psutil`` 不可用时回退到 ``ps -eo pid=,args=`` 解析 (Linux/macOS 都支持)。
    3. 完全没扫到 frpc 进程时, 至少试一次 ``fallback_path`` 或 ``DEFAULT_FRPC_PATH``,
       这样 frpc 在 systemd 下被隐藏 / 在容器里跑 / 当前用户读不到 ``/proc/<pid>`` 也不影响。

    每条 FrpcInstance 都尝试做 toml 解析, **任何异常都不抛**, 转成 ``error`` 字段。
    """
    instances: list[FrpcInstance] = []
    seen_pids: set[int] = set()

    candidates = _list_frpc_candidates()
    for pid, cmdline in candidates:
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        cfg_path = _extract_frpc_config_path(cmdline)
        cfg_obj, err = None, None
        if cfg_path:
            try:
                cfg_obj = parse_frpc_toml(cfg_path)
            except (FileNotFoundError, ValueError, OSError) as exc:
                err = f'{type(exc).__name__}: {exc}'
        else:
            err = '命令行未指定 -c <config>, 无法读取配置'
        instances.append(FrpcInstance(
            pid=pid, cmdline=cmdline, config_path=cfg_path,
            cfg=cfg_obj, error=err,
        ))

    # Fallback: 没扫到任何 frpc 进程, 但 toml 可能仍在; 至少给出 "配置文件视角" 的数据
    if not instances:
        fb = fallback_path or DEFAULT_FRPC_PATH
        if os.path.isfile(fb):
            try:
                cfg_obj = parse_frpc_toml(fb)
                instances.append(FrpcInstance(
                    pid=0, cmdline=[], config_path=fb, cfg=cfg_obj,
                    error='未检测到 frpc 进程, 仅按配置文件兜底', 
                ))
            except (FileNotFoundError, ValueError, OSError) as exc:
                instances.append(FrpcInstance(
                    pid=0, cmdline=[], config_path=fb, cfg=None,
                    error=f'未检测到 frpc 进程, 配置文件解析失败: {exc}',
                ))
    return instances


def _list_frpc_candidates() -> list[tuple[int, list[str]]]:
    """返回 ``[(pid, cmdline_argv), ...]``, 仅包含 frpc 可执行 (不是 frps / frpc.toml 编辑器)。

    判定规则: argv[0] basename 必须是 ``frpc`` 或 ``frpc.exe``, 或者完整路径里含
    ``/frpc`` 后跟空格/结尾 (避免 ``/usr/bin/frpcheck`` 误命中)。
    """
    out: list[tuple[int, list[str]]] = []
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None  # type: ignore

    if psutil:
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = p.info.get('cmdline') or []
                if not cmdline:
                    continue
                if _is_frpc_cmdline(cmdline, p.info.get('name') or ''):
                    out.append((int(p.info['pid']), list(cmdline)))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:  # noqa: BLE001
                continue
        return out

    # psutil 不可用 → ps fallback (Linux/macOS); Windows 上没有 ps 就放弃, 返回空
    try:
        proc = subprocess.run(
            ['ps', '-eo', 'pid=,args='],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return out
    if proc.returncode != 0:
        return out

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_str, args = line.split(None, 1)
            pid = int(pid_str)
        except ValueError:
            continue
        # ps args 是空格分隔, 暴力 split 已经够 frpc 这种场景
        argv = args.split()
        if not argv:
            continue
        if _is_frpc_cmdline(argv, os.path.basename(argv[0])):
            out.append((pid, argv))
    return out


def _is_frpc_cmdline(cmdline: list[str], name_hint: str = '') -> bool:
    """命中规则: argv[0] basename == 'frpc' (允许 .exe), 或 name_hint == 'frpc'。

    严格区分 ``frpc`` 和 ``frps``: 服务端的 frps 完全不在我们的范围内。
    """
    if not cmdline:
        return False
    name_hint = (name_hint or '').strip().lower()
    if name_hint in ('frpc', 'frpc.exe'):
        return True
    base = os.path.basename(cmdline[0] or '').lower()
    if base in ('frpc', 'frpc.exe'):
        return True
    # 命令行里有 ``/frpc`` (e.g. ``go run main.go`` 启动的奇怪情况) 也算
    return any(s.lower().endswith('/frpc') for s in cmdline[:2])


# ---------------------------------------------------------------------------
# 端口检查
# ---------------------------------------------------------------------------

def is_port_in_use(port: int, host: str = '0.0.0.0') -> bool:
    """通过 ``connect`` 探测端口是否被占用，避免 SO_REUSEADDR 误判。

    - bind 测试在 Linux 下有时和真实占用语义不一致（比如 ``IPV6_V6ONLY``）。
    - 这里改为：尝试 connect 到 127.0.0.1:port，如果能连上，说明已经有程序在监听。
    - connect 失败（``ECONNREFUSED``）才认为空闲。
    """
    if port <= 0 or port > 65535:
        raise ValueError(f'非法端口号: {port}')
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect(('127.0.0.1', port))
        return True
    except (ConnectionRefusedError, socket.timeout):
        return False
    except OSError as exc:
        # 例如 EADDRNOTAVAIL；保守按"未被占用"处理
        logger.debug('connect 探测端口 %s 失败，忽略: %s', port, exc)
        return False
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 安全解压
# ---------------------------------------------------------------------------

def safe_extract_tarball(archive_path: str, target_dir: str,
                         max_total_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES) -> str:
    """把 ``archive_path`` 解压到 ``target_dir``，全程做 path traversal 防御。

    返回**真正的根目录**：tar 包内通常会带一层 ``my-app/`` 顶层目录，解压完成后
    我们尽量返回该顶层目录路径，便于上层定位 ``run.sh``。

    防御要点：
    1. 拒绝绝对路径成员；
    2. 解析后的最终路径必须在 ``target_dir`` 之内（``os.path.realpath`` 比较）；
    3. 拒绝硬/软链接逃逸（链接目标必须也指向目录内）；
    4. 总解压大小不能超过 ``max_total_bytes``。
    """
    os.makedirs(target_dir, exist_ok=True)
    target_real = os.path.realpath(target_dir)

    if not tarfile.is_tarfile(archive_path):
        raise ValueError('上传文件不是合法的 tar(.gz) 归档')

    total = 0
    extracted_top: set[str] = set()
    with tarfile.open(archive_path, 'r:*') as tf:
        for member in tf.getmembers():
            if member.name.startswith('/') or '..' in member.name.split('/'):
                raise ValueError(f'归档中存在非法路径: {member.name}')

            dest = os.path.realpath(os.path.join(target_real, member.name))
            if not (dest == target_real or dest.startswith(target_real + os.sep)):
                raise ValueError(f'归档成员越界: {member.name}')

            if member.issym() or member.islnk():
                # 链接目标也必须落在 target_real 之内
                link_target = os.path.realpath(
                    os.path.join(os.path.dirname(dest), member.linkname)
                )
                if not link_target.startswith(target_real + os.sep) and link_target != target_real:
                    raise ValueError(f'链接 {member.name} 指向目录外: {member.linkname}')

            total += max(member.size or 0, 0)
            if total > max_total_bytes:
                raise ValueError('归档解压后总大小超过限制')

            top = member.name.split('/', 1)[0]
            if top:
                extracted_top.add(top)

        # 上面只遍历了 metadata，这里真实写盘；filter='data' 是 3.12 推荐的安全过滤器
        try:
            tf.extractall(target_real, filter='data')  # type: ignore[arg-type]
        except TypeError:
            tf.extractall(target_real)

    # 若归档只有一个顶层目录，使用它作为工作根；否则使用 target_real
    if len(extracted_top) == 1:
        candidate = os.path.join(target_real, next(iter(extracted_top)))
        if os.path.isdir(candidate):
            return candidate
    return target_real


def find_run_script(work_dir: str) -> str | None:
    """在 ``work_dir`` 根下找 ``run.sh``；找不到返回 None。"""
    p = os.path.join(work_dir, 'run.sh')
    return p if os.path.isfile(p) else None


# ---------------------------------------------------------------------------
# 进程启停
# ---------------------------------------------------------------------------

def is_pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但属于别的用户/无权 signal
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


def start_run_script(run_sh: str, local_port: int, log_path: str,
                     cwd: str | None = None) -> int:
    """启动 ``run.sh <local_port>``，stdout/stderr 重定向到 ``log_path``，返回 pid。

    - 自动给 ``run.sh`` 加上执行位（避免 tar 解压后丢失 +x 模式）。
    - 用 ``setsid`` 起新进程组，停止时一次性 kill 整个进程组，避免子进程残留。
    """
    if not os.path.isfile(run_sh):
        raise FileNotFoundError(f'run.sh 不存在: {run_sh}')

    try:
        st = os.stat(run_sh)
        os.chmod(run_sh, st.st_mode | stat.S_IXUSR | stat.S_IXGRP)
    except OSError as exc:
        logger.warning('给 run.sh 加执行位失败: %s', exc)

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_fp = open(log_path, 'ab', buffering=0)
    log_fp.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} START run.sh {local_port} =====\n".encode())
    log_fp.flush()

    proc = subprocess.Popen(
        ['bash', run_sh, str(local_port)],
        cwd=cwd or os.path.dirname(run_sh),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=_child_env(local_port),
    )
    # 短暂等一下让 run.sh 至少完成 fork；如果立刻 exit，更容易抓到失败
    time.sleep(START_GRACE_SECONDS)
    if proc.poll() is not None:
        rc = proc.returncode
        log_fp.close()
        raise RuntimeError(f'run.sh 启动后立即退出（exit={rc}），请查看日志')
    return proc.pid


def stop_pid(pid: int | None, grace_seconds: float = STOP_GRACE_SECONDS) -> int | None:
    """终止由 [start_run_script] 启动的进程组。返回退出码（无法获取时返回 None）。"""
    if not pid or pid <= 0:
        return None
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return None
    except PermissionError as exc:
        logger.warning('SIGTERM 无权限: %s', exc)

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not is_pid_running(pid):
            return _reap_exit_code(pid)
        time.sleep(0.1)

    # 还活着 → SIGKILL
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        # 沙箱 / 跨用户场景：仅记录不抛，调用方可由 [is_pid_running] 持续探测
        logger.warning('SIGKILL 无权限（pgid=%s）: %s', pgid, exc)
    time.sleep(0.2)
    return _reap_exit_code(pid)


def _reap_exit_code(pid: int) -> int | None:
    """非父进程时无法 waitpid；这里只能尽力探测，失败返回 None。"""
    try:
        # WNOHANG 不阻塞；非子进程会抛 ECHILD
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid == 0:
            return None
        return os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        return None
    except OSError:
        return None


# file_manager 自己是用 Flask `debug=True` 启动的, Werkzeug 的 reloader 会在父进程
# 环境里塞这些变量。如果原样继承给子服务, 子服务的 Werkzeug 会误判 "我是 reloader
# 拉起的子进程", 然后拿父进程那个早已无效的 fd 去 socket.fromfd(),
# 触发 `OSError: [Errno 9] Bad file descriptor`。
# 这里在拉子服务前显式擦掉这些脏变量, 让子服务以 "干净世界" 启动。
_WERKZEUG_DIRTY_ENV_KEYS = (
    'WERKZEUG_RUN_MAIN',
    'WERKZEUG_SERVER_FD',
)


def _child_env(local_port: int) -> dict:
    env = os.environ.copy()
    for key in _WERKZEUG_DIRTY_ENV_KEYS:
        env.pop(key, None)
    env['LOCAL_PORT'] = str(local_port)
    env['PYTHONUNBUFFERED'] = '1'
    return env


# ---------------------------------------------------------------------------
# 日志读取
# ---------------------------------------------------------------------------

def tail_log(log_path: str, max_lines: int = 200, max_bytes: int = 64 * 1024) -> str:
    """读取日志末尾 ``max_lines`` 行（同时受 ``max_bytes`` 上限保护），编码异常 → utf-8 替换。"""
    if not log_path or not os.path.isfile(log_path):
        return ''
    size = os.path.getsize(log_path)
    read_from = max(0, size - max_bytes)
    with open(log_path, 'rb') as fh:
        fh.seek(read_from)
        data = fh.read()
    text = data.decode('utf-8', errors='replace')
    if read_from > 0:
        # 头部可能截到了一行中间，丢弃首行
        nl = text.find('\n')
        if nl >= 0:
            text = text[nl + 1:]
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 路径布局
# ---------------------------------------------------------------------------

def service_dir(upload_root: str, user_id: int, service_id: int) -> str:
    return os.path.join(upload_root, 'services', str(user_id), str(service_id))


def archive_path(service_dir_: str) -> str:
    return os.path.join(service_dir_, 'archive.tar.gz')


def work_dir(service_dir_: str) -> str:
    return os.path.join(service_dir_, 'work')


def log_path(service_dir_: str) -> str:
    return os.path.join(service_dir_, 'run.log')


def cleanup_service_dir(service_dir_: str) -> None:
    """删除某个 service 的全部磁盘文件；幂等，失败仅 warn。"""
    if not service_dir_ or not os.path.isdir(service_dir_):
        return
    try:
        shutil.rmtree(service_dir_)
    except OSError as exc:
        logger.warning('rmtree(%s) 失败: %s', service_dir_, exc)


# ---------------------------------------------------------------------------
# 增量同步（用于"在已存在的服务上上传新归档"场景）
# ---------------------------------------------------------------------------

# 单文件 hash 比对的最大体积；超过此阈值视为差异（避免对大二进制反复读两遍）
SYNC_HASH_MAX_BYTES = 64 * 1024 * 1024  # 64MB


def _sha256_of(path: str, max_bytes: int = SYNC_HASH_MAX_BYTES) -> str | None:
    """返回文件 sha256；过大或不可读时返回 None（视为差异）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > max_bytes:
        return None
    import hashlib  # 延迟 import：sync_tree 才会用到
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b''):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _files_equal(src: str, dst: str) -> bool:
    """先比 size，再比 hash；任一文件不存在视为不等。"""
    try:
        if os.path.getsize(src) != os.path.getsize(dst):
            return False
    except OSError:
        return False
    h_src = _sha256_of(src)
    h_dst = _sha256_of(dst)
    return h_src is not None and h_dst is not None and h_src == h_dst


def sync_tree(src_root: str, dst_root: str, *,
              max_report: int = 200) -> dict:
    """把 ``src_root`` 下的文件树**增量**同步到 ``dst_root``。

    规则：
    - ``src`` 中存在但 ``dst`` 缺失 → 复制（含父目录自动 mkdir）
    - 双方都存在但内容不同 → 覆盖（``shutil.copy2`` 保留权限/时间戳）
    - 双方都存在且 sha256 相同 → 跳过
    - ``dst`` 中存在但 ``src`` 缺失 → **保留**（增量更新语义；删除必须显式调用别的接口）
    - 符号链接：按"复制为同名链接"处理；目标若指向 ``src_root`` 外则跳过避免逃逸

    返回摘要：``{'added': [...], 'replaced': [...], 'unchanged': N, 'skipped': [...]}``，
    其中列表只保留前 ``max_report`` 条以控制 payload。
    """
    if not os.path.isdir(src_root):
        raise ValueError(f'src_root 不是目录: {src_root}')
    os.makedirs(dst_root, exist_ok=True)

    added: list[str] = []
    replaced: list[str] = []
    skipped: list[tuple[str, str]] = []
    unchanged = 0

    src_root_abs = os.path.realpath(src_root)
    dst_root_abs = os.path.realpath(dst_root)

    def record(target: list[str], rel: str):
        if len(target) < max_report:
            target.append(rel)

    for dirpath, dirnames, filenames in os.walk(src_root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, src_root)
        # 目标侧对应目录；不存在就建
        target_dir = os.path.join(dst_root, rel_dir) if rel_dir != '.' else dst_root
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            logger.warning('sync_tree: mkdir 失败 %s: %s', target_dir, exc)
            record(skipped, (rel_dir, f'mkdir 失败: {exc}'))
            # 子树也跳过
            dirnames[:] = []
            continue

        for name in filenames:
            src_path = os.path.join(dirpath, name)
            dst_path = os.path.join(target_dir, name)
            rel = os.path.normpath(os.path.join(rel_dir, name)) if rel_dir != '.' else name

            # 软链：保留为软链；目标越界则跳过
            if os.path.islink(src_path):
                try:
                    link_target_raw = os.readlink(src_path)
                    link_target_abs = os.path.realpath(
                        os.path.join(os.path.dirname(src_path), link_target_raw)
                    )
                    if not (link_target_abs == src_root_abs
                            or link_target_abs.startswith(src_root_abs + os.sep)):
                        record(skipped, (rel, f'symlink 越界: {link_target_raw}'))
                        continue
                    if os.path.lexists(dst_path):
                        os.unlink(dst_path)
                    os.symlink(link_target_raw, dst_path)
                    record(added if not os.path.exists(dst_path) else replaced, rel)
                except OSError as exc:
                    record(skipped, (rel, str(exc)))
                continue

            try:
                if not os.path.exists(dst_path):
                    shutil.copy2(src_path, dst_path)
                    record(added, rel)
                elif _files_equal(src_path, dst_path):
                    unchanged += 1
                else:
                    shutil.copy2(src_path, dst_path)
                    record(replaced, rel)
            except (OSError, shutil.SameFileError) as exc:
                record(skipped, (rel, str(exc)))

    # 防御性：再做一次 dst_root 边界确认（多用户场景下 race condition）
    if not (os.path.realpath(dst_root) == dst_root_abs):
        logger.warning('sync_tree: dst_root realpath 在同步过程中发生变化')

    return {
        'added': added,
        'replaced': replaced,
        'unchanged': unchanged,
        'skipped': [{'path': p, 'reason': r} for p, r in skipped],
        'total_added': len(added),
        'total_replaced': len(replaced),
        'total_skipped': len(skipped),
        'truncated': (len(added) >= max_report or len(replaced) >= max_report),
    }


def resolve_work_root(extract_dir: str, hint_basename: str | None = None) -> str:
    """在 ``extract_dir`` 下推断「真实工作根目录」。

    优先级（与 ``safe_extract_tarball`` 的返回语义对齐）：
    1. 如果 ``extract_dir/<hint_basename>`` 存在 → 用它（最稳定，新旧 archive 顶层目录同名时）
    2. 如果 ``extract_dir`` 下只有一个子目录且没有别的文件 → 用那个子目录
    3. 如果在 ``extract_dir`` 下能找到 ``run.sh`` → 用 ``extract_dir``
    4. fallback → 用 ``extract_dir`` 本身
    """
    if not os.path.isdir(extract_dir):
        return extract_dir
    if hint_basename:
        candidate = os.path.join(extract_dir, hint_basename)
        if os.path.isdir(candidate):
            return candidate
    try:
        entries = os.listdir(extract_dir)
    except OSError:
        return extract_dir
    sub_dirs = [e for e in entries if os.path.isdir(os.path.join(extract_dir, e))]
    files = [e for e in entries if not os.path.isdir(os.path.join(extract_dir, e))]
    if len(sub_dirs) == 1 and not files:
        return os.path.join(extract_dir, sub_dirs[0])
    return extract_dir
