"""Docker / docker-compose 业务层。

封装命令行细节，路由层只感知"启动 / 停止 / 拉取 / 卸载 / 取状态 / 看日志"。

关键约定：
- 每个 DockerService 用一个 **隔离的 compose 项目名** ``svc_<user>_<id>``，
  避免不同用户/服务之间互相干扰
- 启动时把 ``PORT=<localPort>`` 注入到子进程 env，让 compose 文件用 ``${PORT}``
  来决定主机端口（推荐 ``ports: ["${PORT}:8080"]``）
- 所有命令都打到 ``compose.log``，前端日志页直接 tail 这个文件 + ``docker compose logs``
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 可用性探测
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DockerAvailability:
    """Docker / compose 综合可用性诊断。

    ``ok`` 仅在 docker 在 PATH、daemon 在线、compose v2 可用时为 True。
    其它字段用于前端横幅给出"具体哪一环出问题 + 怎么修"的提示。
    """
    ok: bool
    docker_version: str | None
    compose_version: str | None
    reason: str | None = None
    # 结构化诊断
    docker_in_path: bool = False
    docker_path: str | None = None
    daemon_ok: bool = False
    daemon_reason: str | None = None
    compose_ok: bool = False
    compose_reason: str | None = None
    install_hint: str | None = None
    start_hint: str | None = None


# 平台无关的修复建议；前端展示用
_INSTALL_HINT = (
    'Debian/Ubuntu: curl -fsSL https://get.docker.com | sudo sh\n'
    'Fedora/RHEL  : sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin\n'
    'Arch         : sudo pacman -S docker docker-compose\n'
    'macOS        : brew install --cask docker  或安装 Docker Desktop'
)
_START_HINT = (
    'sudo systemctl start docker            # 启动 daemon\n'
    'sudo usermod -aG docker "$USER"        # 加入 docker 组，重新登录后生效\n'
    'ls -l /var/run/docker.sock             # 确认 socket 存在且当前用户能读'
)
_COMPOSE_HINT = (
    'sudo apt-get install -y docker-compose-plugin  # Debian/Ubuntu\n'
    '或：升级 Docker 到最新版（自带 compose v2 子命令）'
)


def check_availability(timeout: float = 3.0) -> DockerAvailability:
    """探测 docker / docker compose 是否可用。结果可缓存在路由层。

    返回结构化结果，前端可直接根据 ``docker_in_path / daemon_ok / compose_ok``
    决定显示哪段修复建议，无需自行解析 reason 文本。
    """
    docker_bin = shutil.which('docker')
    if not docker_bin:
        return DockerAvailability(
            ok=False,
            docker_version=None, compose_version=None,
            reason='docker 未安装或不在 PATH',
            docker_in_path=False, docker_path=None,
            install_hint=_INSTALL_HINT,
        )

    base = dict(docker_in_path=True, docker_path=docker_bin)

    try:
        v = subprocess.run([docker_bin, 'version', '--format', '{{.Server.Version}}'],
                           capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DockerAvailability(
            ok=False, docker_version=None, compose_version=None,
            reason=f'docker version 调用失败: {exc}',
            daemon_ok=False, daemon_reason=str(exc),
            start_hint=_START_HINT,
            **base,
        )
    if v.returncode != 0:
        daemon_msg = (v.stderr or v.stdout or '未知原因').strip()
        return DockerAvailability(
            ok=False, docker_version=None, compose_version=None,
            reason=f'docker daemon 不可访问: {daemon_msg[:200]}',
            daemon_ok=False, daemon_reason=daemon_msg,
            start_hint=_START_HINT,
            **base,
        )
    docker_ver = v.stdout.strip()

    try:
        cv = subprocess.run([docker_bin, 'compose', 'version', '--short'],
                            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DockerAvailability(
            ok=False, docker_version=docker_ver, compose_version=None,
            reason=f'docker compose 不可用: {exc}',
            daemon_ok=True, compose_ok=False, compose_reason=str(exc),
            install_hint=_COMPOSE_HINT,
            **base,
        )
    if cv.returncode != 0:
        compose_msg = (cv.stderr or cv.stdout or '').strip()
        return DockerAvailability(
            ok=False, docker_version=docker_ver, compose_version=None,
            reason=f'docker compose 不可用: {compose_msg[:200]}',
            daemon_ok=True, compose_ok=False, compose_reason=compose_msg,
            install_hint=_COMPOSE_HINT,
            **base,
        )

    return DockerAvailability(
        ok=True,
        docker_version=docker_ver,
        compose_version=cv.stdout.strip(),
        reason=None,
        daemon_ok=True, compose_ok=True,
        **base,
    )


# ---------------------------------------------------------------------------
# 路径助手
# ---------------------------------------------------------------------------

def docker_service_dir(upload_root: str, user_id: int, sid: int) -> str:
    return os.path.join(upload_root, 'docker-services', str(user_id), str(sid))


def project_name_for(user_id: int, sid: int) -> str:
    # docker 项目名要求：小写字母、数字、点、短杠；以字母数字开头
    return f"svc-{user_id}-{sid}"


# ---------------------------------------------------------------------------
# Compose 解析（只校验，不修改文件）
# ---------------------------------------------------------------------------

ALLOWED_COMPOSE_NAMES = ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml')


def find_compose_file(work_dir: str) -> str | None:
    """在 ``work_dir`` 及其首层子目录中查找 compose 文件。"""
    for name in ALLOWED_COMPOSE_NAMES:
        p = os.path.join(work_dir, name)
        if os.path.isfile(p):
            return p
    try:
        for entry in os.listdir(work_dir):
            sub = os.path.join(work_dir, entry)
            if os.path.isdir(sub):
                for name in ALLOWED_COMPOSE_NAMES:
                    p = os.path.join(sub, name)
                    if os.path.isfile(p):
                        return p
    except OSError:
        return None
    return None


def quick_validate_compose(compose_path: str, timeout: float = 8.0) -> tuple[bool, str]:
    """用 ``docker compose -f xxx config -q`` 静态校验一下，捕获明显语法错误。"""
    try:
        proc = subprocess.run(
            ['docker', 'compose', '-f', compose_path, 'config', '-q'],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f'docker compose config 调用失败: {exc}'
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or '校验失败').strip()
    return True, ''


# ---------------------------------------------------------------------------
# Compose 命令包装
# ---------------------------------------------------------------------------

def _run_compose(args: list[str], *, work_dir: str, project: str, env_extra: dict | None = None,
                 timeout: float | None = None, log_path: str | None = None) -> subprocess.CompletedProcess:
    """统一封装：cwd / 项目名 / 环境变量 / 命令日志落盘。"""
    env = os.environ.copy()
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    cmd = ['docker', 'compose', '-p', project] + args
    logger.info('docker compose: cwd=%s args=%s', work_dir, args)
    try:
        proc = subprocess.run(
            cmd, cwd=work_dir, env=env,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        if log_path:
            _append_log(log_path, f'\n[TIMEOUT] {" ".join(cmd)}\n{exc.stdout or ""}\n{exc.stderr or ""}\n')
        raise
    if log_path:
        _append_log(log_path,
                    f'\n$ {" ".join(cmd)}  (rc={proc.returncode})\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n')
    return proc


def _append_log(log_path: str, text: str):
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8', errors='replace') as f:
            f.write(text)
    except OSError as exc:
        logger.warning('append compose log failed: %s', exc)


def compose_pull(work_dir: str, compose_path: str, project: str, *,
                 env_extra: dict | None = None, log_path: str | None = None,
                 timeout: float = 600.0) -> tuple[bool, str]:
    args = ['-f', compose_path, 'pull']
    proc = _run_compose(args, work_dir=work_dir, project=project,
                        env_extra=env_extra, timeout=timeout, log_path=log_path)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, proc.stdout


def compose_up(work_dir: str, compose_path: str, project: str, *,
               env_extra: dict | None = None, log_path: str | None = None,
               timeout: float = 300.0) -> tuple[bool, str]:
    args = ['-f', compose_path, 'up', '-d', '--remove-orphans']
    proc = _run_compose(args, work_dir=work_dir, project=project,
                        env_extra=env_extra, timeout=timeout, log_path=log_path)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, proc.stdout


def compose_stop(work_dir: str, compose_path: str, project: str, *,
                 log_path: str | None = None, timeout: float = 60.0) -> tuple[bool, str]:
    args = ['-f', compose_path, 'stop']
    proc = _run_compose(args, work_dir=work_dir, project=project,
                        timeout=timeout, log_path=log_path)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def compose_down(work_dir: str, compose_path: str, project: str, *,
                 remove_volumes: bool = True,
                 log_path: str | None = None, timeout: float = 120.0) -> tuple[bool, str]:
    args = ['-f', compose_path, 'down']
    if remove_volumes:
        args.append('-v')
    proc = _run_compose(args, work_dir=work_dir, project=project,
                        timeout=timeout, log_path=log_path)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def compose_ps(work_dir: str, compose_path: str, project: str,
               timeout: float = 10.0) -> list[dict]:
    """返回容器列表（id / name / state / status）。"""
    args = ['-f', compose_path, 'ps', '--format', 'json']
    proc = _run_compose(args, work_dir=work_dir, project=project, timeout=timeout)
    if proc.returncode != 0:
        return []
    out = proc.stdout.strip()
    if not out:
        return []
    # docker compose ps --format json 在不同版本里：
    # - v2.21+：每行一个 JSON 对象
    # - v2.20-：一整个 JSON 数组
    items: list[dict] = []
    if out.startswith('['):
        try:
            items = json.loads(out)
        except json.JSONDecodeError:
            items = []
    else:
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    norm = []
    for c in items:
        norm.append({
            'id': c.get('ID') or c.get('Id') or '',
            'name': c.get('Name') or c.get('Service') or '',
            'service': c.get('Service') or '',
            'state': c.get('State') or '',
            'status': c.get('Status') or '',
            'image': c.get('Image') or '',
        })
    return norm


def compose_logs(work_dir: str, compose_path: str, project: str,
                 tail: int = 200, timeout: float = 10.0) -> str:
    args = ['-f', compose_path, 'logs', '--tail', str(tail), '--no-color', '--timestamps']
    try:
        proc = _run_compose(args, work_dir=work_dir, project=project, timeout=timeout)
    except subprocess.TimeoutExpired:
        return '(读取日志超时)'
    return (proc.stdout or proc.stderr or '').rstrip()


# ---------------------------------------------------------------------------
# 综合清理
# ---------------------------------------------------------------------------

def uninstall(work_dir: str, compose_path: str, project: str, log_path: str | None = None) -> tuple[bool, str]:
    """``down -v`` + 物理目录清理。物理清理失败只记 warn 不视为整体失败。"""
    ok, msg = compose_down(work_dir, compose_path, project, remove_volumes=True, log_path=log_path)
    return ok, msg


def cleanup_service_dir(sdir: str):
    if sdir and os.path.isdir(sdir):
        try:
            shutil.rmtree(sdir)
        except OSError as exc:
            logger.warning('cleanup docker service dir failed: %s -> %s', sdir, exc)


def serialize_container_ids(ids: Iterable[str]) -> str:
    return json.dumps(list(ids))


def deserialize_container_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []
