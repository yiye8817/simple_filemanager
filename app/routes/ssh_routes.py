"""浏览器内网页 SSH 终端 - 路由层。

接口
----
- ``POST /api/ssh/sessions``       建会话 (返回 sid + ttl)
- ``GET  /api/ssh/sessions/<sid>`` 查 session 是否还有效 (peek)
- ``DELETE /api/ssh/sessions/<sid>`` 主动注销
- ``WS   /api/ssh/ws/<sid>``       双向终端流 (xterm.js <-> paramiko)

WebSocket 协议 (全部 JSON 文本帧, 一行一条):

  C -> S:
    {"type":"input","data":"<utf8 string>"}     键盘输入
    {"type":"resize","cols":N,"rows":N}         窗口大小变更
    {"type":"ping"}                             心跳 (可选)

  S -> C:
    {"type":"status","status":"connected","msg":"..."}   连接建立
    {"type":"data","data":"<utf8 string>"}              终端输出
    {"type":"error","msg":"..."}                         任意阶段的错误
    {"type":"closed","code":<int|null>,"msg":"..."}     远端 shell 退出 / 我们主动关
"""
from __future__ import annotations

import json
import logging
import threading
import time

from flask import Blueprint, current_app, jsonify, request, session

from app.services import ssh_bridge_service as ssh_svc
from app.utils.decorators import login_required

logger = logging.getLogger(__name__)

ssh_bp = Blueprint('ssh', __name__)


# ---------------------------------------------------------------------------
# HTTP 接口: 创建 / 查询 / 注销会话
# ---------------------------------------------------------------------------

@ssh_bp.route('/api/ssh/sessions', methods=['POST'])
@login_required
def create_ssh_session():
    """body JSON: ``host`` ``port`` ``username`` ``password?`` ``private_key?`` ``key_passphrase?``。

    成功 → ``{"sid": "...", "ttl": 300}``。
    """
    data = request.get_json(silent=True) or {}
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': '未登录'}), 401

    host = (data.get('host') or '').strip()
    port_raw = data.get('port') if data.get('port') is not None else 22
    username = (data.get('username') or '').strip()
    password = data.get('password')
    private_key = data.get('private_key')
    key_passphrase = data.get('key_passphrase')

    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return jsonify({'error': 'port 必须是整数'}), 400

    try:
        cfg = ssh_svc.create_session(
            user_id=int(user_id),
            host=host,
            port=port,
            username=username,
            password=password,
            private_key=private_key,
            key_passphrase=key_passphrase,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    return jsonify({
        'success': True,
        'sid': cfg.sid,
        'host': cfg.host,
        'port': cfg.port,
        'username': cfg.username,
        'ttl': int(cfg.expires_at - cfg.created_at),
        'expires_at': cfg.expires_at,
    })


@ssh_bp.route('/api/ssh/sessions/<sid>', methods=['GET'])
@login_required
def health_ssh_session(sid: str):
    user_id = session.get('user_id')
    cfg = ssh_svc.peek_session(sid, int(user_id))
    if not cfg:
        return jsonify({'error': '会话不存在或已过期'}), 404
    return jsonify({
        'success': True,
        'sid': cfg.sid,
        'host': cfg.host,
        'port': cfg.port,
        'username': cfg.username,
        'expires_at': cfg.expires_at,
        'remaining': max(0, int(cfg.expires_at - time.time())),
    })


@ssh_bp.route('/api/ssh/sessions/<sid>', methods=['DELETE'])
@login_required
def revoke_ssh_session(sid: str):
    user_id = session.get('user_id')
    ok = ssh_svc.revoke_session(sid, int(user_id))
    return jsonify({'success': ok})


# ---------------------------------------------------------------------------
# WebSocket 入口: 由 register_ssh_websocket(app, sock) 在 app/__init__.py 装配
# ---------------------------------------------------------------------------

def register_ssh_websocket(sock):
    """把 SSH 终端 WebSocket 路由挂到给定的 ``flask_sock.Sock`` 实例上。

    分开成函数是因为 flask-sock 的 ``@sock.route`` 不支持蓝图, 必须直接绑全局
    路由表; 我们在 ``app/__init__.py`` 里 ``sock = Sock(app)`` 之后调用一次本函数。
    """

    @sock.route('/api/ssh/ws/<sid>')
    def ssh_ws(ws, sid):
        # WebSocket 路由不能用 @login_required (flask-sock 不走 view 包装),
        # 这里手动检查 session
        user_id = session.get('user_id')
        if not user_id:
            _safe_send(ws, {'type': 'error', 'msg': '未登录'})
            return
        try:
            cols = int(request.args.get('cols') or 80)
            rows = int(request.args.get('rows') or 24)
        except (TypeError, ValueError):
            cols, rows = 80, 24

        cfg = ssh_svc.pop_session(sid, int(user_id))
        if not cfg:
            _safe_send(ws, {'type': 'error', 'msg': '会话不存在或已过期'})
            return

        client = None
        channel = None
        try:
            try:
                client, channel = ssh_svc.open_ssh_channel(cfg, cols=cols, rows=rows)
            except Exception as exc:  # noqa: BLE001 — SSH 失败原因要原样回给前端
                # 带上异常类型, 方便前端 / 用户判断 (BadAuthenticationType / AuthenticationException / socket.gaierror ...)
                err_class = type(exc).__name__
                _safe_send(ws, {
                    'type': 'error',
                    'msg': f'SSH 连接失败 [{err_class}]: {exc}',
                })
                logger.warning('ssh connect failed sid=%s host=%s port=%s user=%s err=%s: %s',
                               sid[:8] + '...', cfg.host, cfg.port, cfg.username,
                               err_class, exc)
                return

            _safe_send(ws, {
                'type': 'status', 'status': 'connected',
                'msg': f'{cfg.username}@{cfg.host}:{cfg.port}',
            })

            stop = threading.Event()

            # 把 paramiko channel 收到的数据转发到 ws (后台线程)
            reader = threading.Thread(
                target=_pump_channel_to_ws,
                args=(channel, ws, stop),
                name=f'ssh-reader-{sid[:6]}',
                daemon=True,
            )
            reader.start()

            # 主线程负责从 ws 拿到的输入推到 channel
            while not stop.is_set():
                try:
                    msg = ws.receive(timeout=1.0)
                except Exception:  # noqa: BLE001
                    stop.set()
                    break
                if msg is None:
                    # timeout, 继续循环检查 stop / 让 reader 跑
                    continue
                try:
                    payload = json.loads(msg)
                except (TypeError, ValueError):
                    continue
                mtype = payload.get('type')
                if mtype == 'input':
                    data = payload.get('data') or ''
                    if data:
                        try:
                            channel.send(data.encode('utf-8', errors='replace'))
                        except Exception:  # noqa: BLE001
                            stop.set()
                            break
                elif mtype == 'resize':
                    try:
                        c = max(20, min(int(payload.get('cols') or 80), 500))
                        r = max(5, min(int(payload.get('rows') or 24), 200))
                        channel.resize_pty(width=c, height=r)
                    except Exception:  # noqa: BLE001
                        pass
                elif mtype == 'ping':
                    _safe_send(ws, {'type': 'pong', 'ts': time.time()})
                # 其他类型一律忽略
        finally:
            try:
                if channel is not None:
                    channel.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if client is not None:
                    client.close()
            except Exception:  # noqa: BLE001
                pass
            _safe_send(ws, {'type': 'closed', 'msg': 'session ended'})
            logger.info('ssh ws closed sid=%s user=%s', sid[:8] + '...', user_id)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _safe_send(ws, payload: dict) -> bool:
    """把 dict 序列化成 JSON 并发到 ws; 任何异常都吞掉返回 False。"""
    try:
        ws.send(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:  # noqa: BLE001
        return False


def _pump_channel_to_ws(channel, ws, stop: threading.Event):
    """paramiko channel -> ws 的搬运工; 死循环, 直到 stop 被 set 或对端关闭。"""
    import socket
    try:
        while not stop.is_set():
            if channel.closed or channel.exit_status_ready():
                _safe_send(ws, {'type': 'closed', 'code': channel.recv_exit_status(),
                                'msg': 'remote exited'})
                stop.set()
                return
            try:
                data = channel.recv(8192)
            except socket.timeout:
                continue
            except Exception as exc:  # noqa: BLE001
                _safe_send(ws, {'type': 'error', 'msg': f'读 channel 失败: {exc}'})
                stop.set()
                return
            if not data:
                _safe_send(ws, {'type': 'closed', 'msg': 'channel eof'})
                stop.set()
                return
            text = data.decode('utf-8', errors='replace')
            if not _safe_send(ws, {'type': 'data', 'data': text}):
                stop.set()
                return
    finally:
        stop.set()
