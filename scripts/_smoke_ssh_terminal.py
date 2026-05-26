"""冒烟: 网页 SSH 终端会话 + 鉴权 + WebSocket 路由是否挂上去。

跑法:
    yenv/bin/python scripts/_smoke_ssh_terminal.py

不需要真实的 SSH 服务器: paramiko 部分用 monkeypatch 替换掉, 重点验证:
1. ``create_session`` 校验 host/username/port/auth, 返回 sid; ``peek/pop/revoke`` 行为正确;
   不同用户互相隔离。
2. ``POST /api/ssh/sessions`` 走通; 返回的 sid 能被 ``GET /api/ssh/sessions/<sid>`` 读到;
   ``DELETE`` 后再 ``peek`` 拿不到。
3. flask-sock 已注册 ``/api/ssh/ws/<sid>``: 用 Flask url_map 验证存在。
"""
from __future__ import annotations

import os
import sys
import time


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    from app.services import ssh_bridge_service as svc

    # ---- 1. service 层: 边界 ----
    try:
        svc.create_session(user_id=1, host='', port=22, username='root', password='x')
    except ValueError:
        pass
    else:
        raise AssertionError('host 空应该报错')

    try:
        svc.create_session(user_id=1, host='h', port=22, username='', password='x')
    except ValueError:
        pass
    else:
        raise AssertionError('username 空应该报错')

    try:
        svc.create_session(user_id=1, host='h', port=0, username='r', password='x')
    except ValueError:
        pass
    else:
        raise AssertionError('port=0 应该报错')

    try:
        svc.create_session(user_id=1, host='h', port=22, username='r')
    except ValueError:
        pass
    else:
        raise AssertionError('password/private_key 都缺应该报错')

    cfg1 = svc.create_session(user_id=10, host='h1', port=22, username='u1', password='p1')
    cfg2 = svc.create_session(user_id=20, host='h2', port=2222, username='u2', password='p2')
    assert cfg1.sid != cfg2.sid
    assert svc.session_count() >= 2

    # peek 必须用户隔离
    assert svc.peek_session(cfg1.sid, 10) is not None
    assert svc.peek_session(cfg1.sid, 20) is None, '不同 user 不应拿到别人的 session'
    assert svc.peek_session('not-exist', 10) is None

    # pop 一次性消费
    popped = svc.pop_session(cfg1.sid, 10)
    assert popped is not None and popped.password == 'p1'
    assert svc.pop_session(cfg1.sid, 10) is None, 'pop 之后应消失'

    # revoke
    assert svc.revoke_session(cfg2.sid, 99) is False, '不同 user revoke 应失败'
    assert svc.revoke_session(cfg2.sid, 20) is True
    print('[OK] ssh_bridge_service 边界 + 用户隔离 + pop/revoke 正确')

    # ---- 2. 过期清理 ----
    cfg = svc.create_session(user_id=1, host='h', port=22, username='u', password='x',
                             ttl_seconds=30)
    # 强行把过期时间提前
    cfg.expires_at = time.time() - 1
    # 任何调用都会触发 _purge_expired
    assert svc.peek_session(cfg.sid, 1) is None
    print('[OK] 过期会话自动清理')

    # ---- 3. Flask 接口 ----
    from app import create_app, db
    from app.models import User
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['WTF_CSRF_ENABLED'] = False
    suffix = str(int(time.time()))
    with app.app_context():
        db.create_all()
        u = User(username=f'smoke_ssh_{suffix}', email=f'smoke_ssh_{suffix}@example.com')
        u.set_password('pw')
        db.session.add(u)
        u2 = User(username=f'smoke_ssh2_{suffix}', email=f'smoke_ssh2_{suffix}@example.com')
        u2.set_password('pw')
        db.session.add(u2)
        db.session.commit()
        uid = u.id
        uid2 = u2.id

    with app.test_client() as cli:
        # 未登录拒
        r = cli.post('/api/ssh/sessions', json={'host': 'h', 'username': 'u', 'password': 'p'})
        # login_required 会重定向到 /login (302) 或返回 401, 二者都算
        assert r.status_code in (302, 401), (r.status_code, r.data[:200])

        # 登录后创建
        with cli.session_transaction() as ss:
            ss['user_id'] = uid
            ss['user_name'] = 'smoke_ssh'
        r = cli.post('/api/ssh/sessions',
                     json={'host': '127.0.0.1', 'port': 22, 'username': 'root', 'password': 'pw'})
        assert r.status_code == 200, (r.status_code, r.data[:200])
        body = r.get_json()
        assert body['success'] is True and body['sid'], body
        sid = body['sid']
        # 缺字段
        r = cli.post('/api/ssh/sessions', json={'host': '', 'username': 'r', 'password': 'p'})
        assert r.status_code == 400
        # peek
        r = cli.get(f'/api/ssh/sessions/{sid}')
        assert r.status_code == 200, r.data[:200]
        # 别的用户 peek 拿不到
        with cli.session_transaction() as ss:
            ss['user_id'] = uid2
        r = cli.get(f'/api/ssh/sessions/{sid}')
        assert r.status_code == 404, r.data[:200]
        # revoke (作为不匹配 user) 不会真删
        r = cli.delete(f'/api/ssh/sessions/{sid}')
        assert r.status_code == 200
        assert r.get_json()['success'] is False
        # 切回原 user revoke
        with cli.session_transaction() as ss:
            ss['user_id'] = uid
        r = cli.delete(f'/api/ssh/sessions/{sid}')
        assert r.status_code == 200 and r.get_json()['success'] is True
        # 再 peek 应该 404
        r = cli.get(f'/api/ssh/sessions/{sid}')
        assert r.status_code == 404
        print('[OK] /api/ssh/sessions CRUD + 跨用户隔离 + revoke 行为正确')

    # ---- 3b. 密码认证 fallback 到 keyboard-interactive ----
    import paramiko
    from app.services.ssh_bridge_service import _password_then_kbd_interactive

    class _MockTransport:
        """模拟 sshd 只接受 ``keyboard-interactive`` 的场景 (Ubuntu / PAM 默认)。"""
        def __init__(self, password_raises=True, kbd_ok=True):
            self.calls: list = []
            self.password_raises = password_raises
            self.kbd_ok = kbd_ok

        def auth_password(self, user, pwd, fallback=False):
            self.calls.append(('password', user, pwd, fallback))
            if self.password_raises:
                raise paramiko.BadAuthenticationType(
                    'password not allowed', ['keyboard-interactive', 'publickey'])

        def auth_interactive(self, user, handler):
            self.calls.append(('kbd-start', user))
            # 模拟 sshd 问 1 个 'Password:'
            answers = handler('SSH 2FA', 'please enter password', ['Password: '])
            self.calls.append(('kbd-answers', answers))
            if not self.kbd_ok:
                raise paramiko.AuthenticationException('wrong password')

    # Case A: password method 被拒, kbd-interactive 成功 -> 密码正确透传
    t = _MockTransport(password_raises=True, kbd_ok=True)
    _password_then_kbd_interactive(t, 'alice', 's3cret!')
    assert ('password', 'alice', 's3cret!', False) in t.calls
    assert ('kbd-start', 'alice') in t.calls
    assert ('kbd-answers', ['s3cret!']) in t.calls
    print('[OK] password 被拒 -> kbd-interactive 自动 fallback 把同样密码透传给 handler')

    # Case B: password method 被拒 + kbd-interactive 也失败 -> 抛 AuthenticationException
    t = _MockTransport(password_raises=True, kbd_ok=False)
    try:
        _password_then_kbd_interactive(t, 'alice', 'wrong')
    except paramiko.AuthenticationException as exc:
        assert 'kbd-interactive' in str(exc) or '都被拒' in str(exc), exc
    else:
        raise AssertionError('密码错时应抛 AuthenticationException')
    print('[OK] password + kbd-interactive 都失败时抛 AuthenticationException 透传')

    # Case C: 服务器既不接 password 也不接 kbd-interactive (只允许 publickey)
    class _MockNoPwdTransport:
        def auth_password(self, user, pwd, fallback=False):
            raise paramiko.BadAuthenticationType('only key', ['publickey'])
        def auth_interactive(self, user, handler):
            raise AssertionError('不应该尝试 kbd-interactive')

    try:
        _password_then_kbd_interactive(_MockNoPwdTransport(), 'u', 'p')
    except paramiko.AuthenticationException as exc:
        assert 'publickey' in str(exc), exc
    else:
        raise AssertionError('应直接抛 AuthenticationException')
    print('[OK] 服务器只允许 publickey 时, 不再死磕 kbd-interactive')

    # ---- 4. WebSocket 路由是否注册 ----
    # flask-sock 把 ws 路由挂到 app.url_map, 我们可以反查
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert any(r == '/api/ssh/ws/<sid>' for r in rules), (
        f'WebSocket 路由未注册. 实际 rules: {[r for r in rules if "ssh" in r]}')
    print('[OK] /api/ssh/ws/<sid> WebSocket 路由已注册')

    print('\nALL SSH TERMINAL SMOKES PASSED.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
