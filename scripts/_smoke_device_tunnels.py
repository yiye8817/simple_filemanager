"""冒烟: agent 端 frpc 隧道采集 + server 端按设备透传接口 + owner 隔离。

agent.collect_frpc_tunnels 走 monkeypatch (假 frpc 进程 + 临时 toml),
server 端把模拟出来的 stats 直接灌到 Device.stats_json, 然后调
``GET /api/devices/<id>/tunnels`` 验证派生字段 (is_ssh / ssh_command / external_url) 正确。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import time


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# agent.py 在 clients/device_agent 下, 直接当模块导
AGENT_PATH = os.path.join(ROOT, 'clients', 'device_agent')
if AGENT_PATH not in sys.path:
    sys.path.insert(0, AGENT_PATH)


def main() -> int:
    # ---- 1. agent 端: collect_frpc_tunnels ----
    import agent as ag  # type: ignore  # noqa: I001

    # _extract_frpc_config_path: 各形态
    cases = [
        (['frpc', '-c', '/etc/frpc.toml'], '/etc/frpc.toml'),
        (['/usr/local/bin/frpc', '--config', '/var/lib/frpc.toml'], '/var/lib/frpc.toml'),
        (['frpc', '--config=/tmp/a.toml'], '/tmp/a.toml'),
        (['frpc'], None),
    ]
    for argv, expect in cases:
        got = ag._extract_frpc_config_path(argv)
        assert got == expect, f'argv={argv} 期望={expect} 实际={got}'
    print('[OK] agent _extract_frpc_config_path 通过')

    # _is_frpc_cmdline: 区分 frpc / frps
    assert ag._is_frpc_cmdline(['frpc', '-c', 'x'], 'frpc') is True
    assert ag._is_frpc_cmdline(['/usr/bin/frps', '-c', 'x'], 'frps') is False
    assert ag._is_frpc_cmdline(['/opt/x/frpc'], '') is True
    print('[OK] agent _is_frpc_cmdline frpc / frps 区分正确')

    # 用临时 toml + mock 进程列表跑 collect_frpc_tunnels
    with tempfile.TemporaryDirectory() as td:
        toml_path = os.path.join(td, 'frpc.toml')
        with open(toml_path, 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent("""
                serverAddr = "vps.example.com"
                serverPort = 7000

                [[proxies]]
                name = "ssh-home"
                type = "tcp"
                localPort = 22
                remotePort = 22001

                [[proxies]]
                name = "web-home"
                type = "tcp"
                localPort = 18080
                remotePort = 38080
            """).strip())

        orig_list = ag._list_frpc_candidates
        ag._list_frpc_candidates = lambda: [  # type: ignore[assignment]
            (10001, ['/usr/bin/frpc', '-c', toml_path]),
        ]
        try:
            tunnels = ag.collect_frpc_tunnels()
        finally:
            ag._list_frpc_candidates = orig_list  # type: ignore[assignment]
        assert len(tunnels) == 1, tunnels
        inst = tunnels[0]
        assert inst['pid'] == 10001
        assert inst['config_path'] == toml_path
        assert inst['error'] is None
        assert inst['server_addr'] == 'vps.example.com'
        assert inst['server_port'] == 7000
        assert len(inst['proxies']) == 2
        ssh = next(p for p in inst['proxies'] if p['local_port'] == 22)
        assert ssh['remote_port'] == 22001 and ssh['type'] == 'tcp'
        print('[OK] agent collect_frpc_tunnels 解析临时 toml 通过, 2 条 proxy')

        # ---- 1b. 相对路径 + 读不到 cwd 时, --frpc-config fallback 必须生效 ----
        ag._list_frpc_candidates = lambda: [  # type: ignore[assignment]
            (10002, ['./frpc', '-c', 'frpc.toml']),  # 相对路径
        ]
        orig_cwd = ag._get_process_cwd
        ag._get_process_cwd = lambda pid: None  # type: ignore[assignment]  模拟读不到 /proc/<pid>/cwd
        try:
            # 没 fallback: 应给出诊断错误, 不应崩
            tns_no_fb = ag.collect_frpc_tunnels()
            assert len(tns_no_fb) == 1
            assert tns_no_fb[0]['error'] and '相对路径' in tns_no_fb[0]['error']
            assert tns_no_fb[0]['config_path'] is None
            assert tns_no_fb[0]['proxies'] == []
            # 有 fallback: 必须能解析出来
            tns_fb = ag.collect_frpc_tunnels(fallback_config_path=toml_path)
            assert len(tns_fb) == 1
            assert tns_fb[0]['error'] is None, tns_fb[0]['error']
            assert tns_fb[0]['used_fallback_config'] is True
            assert tns_fb[0]['config_path'] == toml_path
            assert tns_fb[0]['server_addr'] == 'vps.example.com'
            assert len(tns_fb[0]['proxies']) == 2
            print('[OK] cwd 读不到时, --frpc-config fallback 能正确兜底')
        finally:
            ag._list_frpc_candidates = orig_list  # type: ignore[assignment]
            ag._get_process_cwd = orig_cwd  # type: ignore[assignment]

        # ---- 1c. 模块级 set_frpc_fallback_config 注入后 collect_stats 自动用 ----
        ag.set_frpc_fallback_config(toml_path)
        ag._list_frpc_candidates = lambda: [  # type: ignore[assignment]
            (10003, ['./frpc', '-c', 'relative.toml']),
        ]
        ag._get_process_cwd = lambda pid: None  # type: ignore[assignment]
        try:
            st = ag.collect_stats()
            tns = st.get('frpc_tunnels') or []
            assert len(tns) == 1 and tns[0]['used_fallback_config'] is True, tns
            assert tns[0]['config_path'] == toml_path
            print('[OK] set_frpc_fallback_config + collect_stats 自动兜底')
        finally:
            ag._list_frpc_candidates = orig_list  # type: ignore[assignment]
            ag._get_process_cwd = orig_cwd  # type: ignore[assignment]
            ag.set_frpc_fallback_config(None)

        # ---- 2. agent collect_stats 应当把 frpc_tunnels 塞进 stats ----
        ag._list_frpc_candidates = lambda: [  # type: ignore[assignment]
            (10001, ['/usr/bin/frpc', '-c', toml_path]),
        ]
        try:
            stats = ag.collect_stats()
        finally:
            ag._list_frpc_candidates = orig_list  # type: ignore[assignment]
        assert 'frpc_tunnels' in stats, stats
        assert len(stats['frpc_tunnels']) == 1
        print('[OK] agent collect_stats 上报字段 stats.frpc_tunnels')

        # ---- 3. server 端 /api/devices/<id>/tunnels ----
        from app import create_app, db
        from app.models import Device, User

        app = create_app()
        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        suffix = str(int(time.time()))
        with app.app_context():
            db.create_all()
            u = User(username=f'tun_owner_{suffix}', email=f'tun_owner_{suffix}@e.com')
            u.set_password('pw')
            db.session.add(u)
            u_other = User(username=f'tun_other_{suffix}', email=f'tun_other_{suffix}@e.com')
            u_other.set_password('pw')
            db.session.add(u_other)
            db.session.commit()
            uid, uid_other = u.id, u_other.id

            d = Device(
                user_id=uid,
                device_uid=f'smoke-tun-{suffix}',
                device_token=f'tok-{suffix}',
                name=f'smoke-tun-{suffix}',
                stats_json=json.dumps({
                    'ts': int(time.time()),
                    'frpc_tunnels': tunnels,
                }),
            )
            db.session.add(d)
            db.session.commit()
            did = d.id

        with app.test_client() as cli:
            # 未登录 -> 302 / 401
            r = cli.get(f'/api/devices/{did}/tunnels')
            assert r.status_code in (302, 401), (r.status_code, r.data[:200])

            # 登录 owner
            with cli.session_transaction() as ss:
                ss['user_id'] = uid
                ss['user_name'] = f'tun_owner_{suffix}'
            r = cli.get(f'/api/devices/{did}/tunnels?user=ubuntu')
            assert r.status_code == 200, (r.status_code, r.data[:200])
            body = r.get_json()
            assert body['success'] is True
            assert len(body['instances']) == 1
            proxies = body['instances'][0]['proxies']
            assert len(proxies) == 2
            ssh = next(p for p in proxies if p['local_port'] == 22)
            assert ssh['is_ssh'] is True
            assert ssh['ssh_command'] == 'ssh -p 22001 ubuntu@vps.example.com', ssh
            assert ssh['remote_host'] == 'vps.example.com'
            web = next(p for p in proxies if p['local_port'] == 18080)
            assert web['is_ssh'] is False
            assert web['ssh_command'] is None
            assert web['external_url'] == 'tcp://vps.example.com:38080', web

            # 另一 user 不能看 (owner 隔离 by _own_device_or_404)
            with cli.session_transaction() as ss:
                ss['user_id'] = uid_other
            r = cli.get(f'/api/devices/{did}/tunnels')
            assert r.status_code == 404, (r.status_code, r.data[:200])
            print('[OK] /api/devices/<id>/tunnels owner 隔离 + 派生字段正确')

        # 旧的 /api/frpc/tunnels 应该没了
        with app.test_client() as cli:
            with cli.session_transaction() as ss:
                ss['user_id'] = uid
                ss['user_name'] = f'tun_owner_{suffix}'
            r = cli.get('/api/frpc/tunnels')
            assert r.status_code == 404, ('/api/frpc/tunnels 应已删除, 实际:', r.status_code)
            print('[OK] /api/frpc/tunnels 已下线 (返回 404)')

    print('\nALL DEVICE-TUNNELS SMOKES PASSED.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
