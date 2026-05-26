"""浏览器内网页 SSH 终端 - 服务端桥接层。

整体结构::

    [xterm.js] <-- WebSocket --> [Flask + flask-sock] <-- paramiko ssh client --> [SSH 服务器]

设计要点
--------
1. **凭据不落盘**: ``SshSessionConfig`` 仅存在进程内 ``_SESSION_STORE``,
   且 5 分钟未消费即过期 (惰性清理)。
2. **一次性使用**: WebSocket 一旦消费 ``sid`` 就把 ``SshSessionConfig`` pop 出来,
   再开新窗口需要重新填密码 — 降低被复用 / 抓包的风险。
3. **进程内并发**: 每个 WebSocket 调用 ``open_ssh_channel`` 单独建一个 paramiko
   ``SSHClient`` + ``Channel``, 互不影响; 关闭由调用方负责。
4. **超时**: connect 12s, 认证失败立即抛; channel 默认 ``settimeout(0.05)``,
   方便上层用 polling 把 ``chan.recv`` 与 ws receive 并发处理而不阻塞 GIL。
"""
from __future__ import annotations

import atexit
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SshSessionConfig:
    """一次 SSH 会话的全部参数。``password`` 仅存在内存里, 不落盘也不进日志。"""
    sid: str
    user_id: int
    host: str
    port: int
    username: str
    password: str | None = None
    private_key: str | None = None      # 可选, PEM 字符串
    key_passphrase: str | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0


# session_id -> SshSessionConfig
_SESSION_STORE: dict[str, SshSessionConfig] = {}
_STORE_LOCK = threading.Lock()

# 默认会话有效期 (秒): 创建后必须在该时间内打开 WebSocket 才有效
DEFAULT_SESSION_TTL = 5 * 60


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------

def _purge_expired(now: float | None = None) -> int:
    """删除已过期 session, 返回清理掉的条数。调用方需持锁。"""
    n = 0
    now = now or time.time()
    expired = [k for k, v in _SESSION_STORE.items() if v.expires_at <= now]
    for k in expired:
        _SESSION_STORE.pop(k, None)
        n += 1
    return n


def create_session(
    *,
    user_id: int,
    host: str,
    port: int = 22,
    username: str,
    password: str | None = None,
    private_key: str | None = None,
    key_passphrase: str | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL,
) -> SshSessionConfig:
    """创建一次性 SSH 会话凭据并存入内存。

    返回的对象包含 ``sid``, 前端拿 ``sid`` 打开 WebSocket 即可建立终端。
    """
    if not host or not isinstance(host, str):
        raise ValueError('host 必填')
    if not username or not isinstance(username, str):
        raise ValueError('username 必填')
    if not isinstance(port, int) or not (0 < port < 65536):
        raise ValueError('port 必须是 1-65535 之间的整数')
    if password is None and not private_key:
        raise ValueError('必须提供 password 或 private_key 其一')

    now = time.time()
    cfg = SshSessionConfig(
        sid=secrets.token_urlsafe(24),
        user_id=int(user_id),
        host=host.strip(),
        port=int(port),
        username=username.strip(),
        password=password,
        private_key=private_key,
        key_passphrase=key_passphrase,
        created_at=now,
        expires_at=now + max(30, int(ttl_seconds)),
    )
    with _STORE_LOCK:
        _purge_expired(now)
        _SESSION_STORE[cfg.sid] = cfg
    logger.info('ssh session created sid=%s user=%s host=%s port=%s username=%s',
                cfg.sid[:8] + '...', user_id, cfg.host, cfg.port, cfg.username)
    return cfg


def pop_session(sid: str, user_id: int) -> SshSessionConfig | None:
    """消费 sid (一次性): 命中且 user_id 一致才返回, 否则 None。"""
    if not sid:
        return None
    with _STORE_LOCK:
        _purge_expired()
        cfg = _SESSION_STORE.get(sid)
        if not cfg:
            return None
        if cfg.user_id != int(user_id):
            logger.warning('ssh session sid=%s 用户不匹配: 期望 %s 实际 %s',
                           sid[:8] + '...', cfg.user_id, user_id)
            return None
        _SESSION_STORE.pop(sid, None)
        return cfg


def peek_session(sid: str, user_id: int) -> SshSessionConfig | None:
    """只读查询 (不消费), 用于 /api/ssh/sessions/<sid>/health。"""
    if not sid:
        return None
    with _STORE_LOCK:
        _purge_expired()
        cfg = _SESSION_STORE.get(sid)
        if cfg and cfg.user_id == int(user_id):
            return cfg
        return None


def revoke_session(sid: str, user_id: int) -> bool:
    """用户主动注销 (例如关闭 modal)。"""
    if not sid:
        return False
    with _STORE_LOCK:
        cfg = _SESSION_STORE.get(sid)
        if cfg and cfg.user_id == int(user_id):
            _SESSION_STORE.pop(sid, None)
            return True
    return False


def session_count() -> int:
    with _STORE_LOCK:
        _purge_expired()
        return len(_SESSION_STORE)


# ---------------------------------------------------------------------------
# paramiko 包装: 建立 SSH 通道 (interactive shell)
# ---------------------------------------------------------------------------

def open_ssh_channel(
    cfg: SshSessionConfig,
    cols: int = 80,
    rows: int = 24,
    term: str = 'xterm-256color',
    connect_timeout: float = 12.0,
):
    """打开 SSH 连接 + 申请 PTY, 返回 ``(client, channel)``。

    认证顺序与 fallback 策略 (按需启用, 任一成功即返回):

    1. ``private_key`` (PEM): 走 ``auth_publickey``;
    2. ``password`` (普通方法): 走 ``auth_password``;
    3. **keyboard-interactive fallback**: 当 sshd 启用 ``KbdInteractiveAuthentication``
       / PAM challenge-response 时 (Ubuntu / RHEL 默认配置), 步骤 2 会被服务器返回
       ``BadAuthenticationType`` 拒掉; 这里自动把同样的密码塞进 ``auth_interactive``
       的每个 prompt, 让用户感知不到这个差异。

    任何异常都会冒泡出去, 调用方在 ws.send 里写 error 信息。
    成功后 ``channel.settimeout(0.05)``, 上层 poll 用 ``chan.recv_ready`` 判断。
    """
    import paramiko  # 延迟导入: 不开 SSH 功能的部署也能跑

    cols = max(20, min(int(cols or 80), 500))
    rows = max(5, min(int(rows or 24), 200))

    transport: 'paramiko.Transport | None' = None
    try:
        transport = paramiko.Transport((cfg.host, cfg.port))
        transport.banner_timeout = connect_timeout
        transport.auth_timeout = connect_timeout
        # 浏览器终端的 host key 验证由用户在前端确认, 这里不持久化 known_hosts;
        # ``hostkey=None`` = 直接信任服务端返回的 key
        transport.connect(hostkey=None)

        # 1) 私钥
        if cfg.private_key:
            pkey = _load_pkey(cfg.private_key, cfg.key_passphrase)
            transport.auth_publickey(cfg.username, pkey)
        elif cfg.password is not None:
            _password_then_kbd_interactive(transport, cfg.username, cfg.password)
        else:
            raise paramiko.AuthenticationException(
                '会话凭据无 password 也无 private_key, 无法发起认证')

        if not transport.is_authenticated():
            raise paramiko.AuthenticationException(
                '认证未完成 (transport.is_authenticated() == False)')

        channel = transport.open_session()
        channel.get_pty(term=term, width=cols, height=rows)
        channel.invoke_shell()
        channel.settimeout(0.05)
    except Exception:
        try:
            if transport is not None:
                transport.close()
        except Exception:  # noqa: BLE001
            pass
        raise

    # 用 SSHClient 包一下, 让上层 ``client.close()`` 一行就能清理 transport
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # paramiko 5.x: _transport 是公认的 hook 点 (其 close() 也是直接读这个属性)
    client._transport = transport
    return client, channel


def _password_then_kbd_interactive(transport, username: str, password: str) -> None:
    """先尝试 ``auth_password``; sshd 不接受 ``password`` 方法但接受 ``keyboard-interactive``
    时 (PAM 后端 / Ubuntu 默认), 退到 ``auth_interactive`` 把密码塞进每个 prompt。

    成功直接返回; 失败原样抛 paramiko 异常 (``AuthenticationException`` /
    ``BadAuthenticationType`` 等), 让上层把信息回给前端。
    """
    import paramiko

    if not password:
        raise paramiko.AuthenticationException('密码为空')

    def _kbd_handler(title, instructions, prompt_list):  # noqa: ARG001
        # 多数 PAM kbd-interactive 只问一个 'Password:'; 偶尔会问多个 (例如 2FA);
        # 没法在 Web UI 一次拿到多 step 信息, 先把密码塞给所有 prompt, 多数场景能过。
        return [password for _ in prompt_list]

    try:
        transport.auth_password(username, password, fallback=False)
        return
    except paramiko.BadAuthenticationType as exc:
        allowed = list(getattr(exc, 'allowed_types', None) or [])
        if 'keyboard-interactive' not in allowed:
            # 服务器既不接 password 也不接 kbd-interactive (例如只允许 publickey)
            raise paramiko.AuthenticationException(
                f'服务器拒绝密码认证: 仅允许 {allowed or "(unknown)"}; 请改用私钥登录'
            ) from exc
        # 落到 kbd-interactive
    except paramiko.AuthenticationException:
        # password 方法被服务器接受但凭据错; 仍试一次 kbd-interactive
        # (有些 sshd / pam 配置只对 kbd-interactive 通过 PAM 校验)
        pass

    try:
        transport.auth_interactive(username, _kbd_handler)
    except paramiko.AuthenticationException as exc:
        # 透出更友好的提示: 让用户判断是密码错 vs 服务器策略不接
        raise paramiko.AuthenticationException(
            f'密码 / keyboard-interactive 都被拒: {exc}'
        ) from exc


def _load_pkey(pem_text: str, passphrase: str | None):
    """容错地把 PEM 文本转 paramiko Key (尝试多种 key 类型)。"""
    import io

    import paramiko

    last_err: Exception | None = None
    for klass in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return klass.from_private_key(io.StringIO(pem_text), password=passphrase or None)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise ValueError(f'无法解析私钥 (尝试了 Ed25519/RSA/ECDSA/DSS): {last_err}')


# ---------------------------------------------------------------------------
# 兜底: 进程退出前清空 session_store (避免日志里有可疑残留)
# ---------------------------------------------------------------------------

def _atexit_clear():
    with _STORE_LOCK:
        _SESSION_STORE.clear()


atexit.register(_atexit_clear)
