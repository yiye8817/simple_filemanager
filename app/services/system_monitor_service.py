"""系统资源监控 service 层。

仅做"探测 + 数据格式化"，不直接写 DB，也不依赖 Flask 上下文。

设计要点：
- 所有探测都是 best-effort，单点失败不影响其它指标返回（前端只显示拿到的部分）
- GPU 通过 ``nvidia-smi`` 二进制读取，无驱动时安静返回空数组
- 进程级指标用 psutil 拿，未装时整段降级；caller 可以根据 ``ok=False`` 区分
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Iterable

logger = logging.getLogger(__name__)

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except Exception:  # noqa: BLE001
    psutil = None  # type: ignore
    _HAS_PSUTIL = False


# ---------------------------------------------------------------------------
# 系统级
# ---------------------------------------------------------------------------

def _cpu() -> dict:
    if not _HAS_PSUTIL:
        return {'ok': False, 'reason': 'psutil 未安装'}
    try:
        return {
            'ok': True,
            'percent': psutil.cpu_percent(interval=None),
            'count_logical': psutil.cpu_count(logical=True) or 0,
            'count_physical': psutil.cpu_count(logical=False) or 0,
            # per_cpu 一行也提供，前端有空间就画
            'per_cpu': psutil.cpu_percent(interval=None, percpu=True),
        }
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'reason': str(exc)}


def _memory() -> dict:
    if not _HAS_PSUTIL:
        return {'ok': False, 'reason': 'psutil 未安装'}
    try:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {
            'ok': True,
            'total': vm.total,
            'used': vm.used,
            'available': vm.available,
            'percent': vm.percent,
            'swap_total': sw.total,
            'swap_used': sw.used,
            'swap_percent': sw.percent,
        }
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'reason': str(exc)}


def _disk(paths: Iterable[str] | None = None) -> dict:
    """报告几个关键挂载点的容量。默认报根目录；调用方可以追加 UPLOAD_FOLDER 等。"""
    if not _HAS_PSUTIL:
        return {'ok': False, 'reason': 'psutil 未安装'}
    targets = list(paths or [])
    if '/' not in targets:
        targets.insert(0, '/')
    out = []
    for p in targets:
        try:
            du = psutil.disk_usage(p)
            out.append({
                'path': p,
                'total': du.total,
                'used': du.used,
                'free': du.free,
                'percent': du.percent,
            })
        except Exception as exc:  # noqa: BLE001
            out.append({'path': p, 'error': str(exc)})
    return {'ok': True, 'items': out}


def _load_avg() -> dict:
    try:
        la1, la5, la15 = os.getloadavg()
        return {'ok': True, '1m': la1, '5m': la5, '15m': la15}
    except (OSError, AttributeError) as exc:
        return {'ok': False, 'reason': str(exc)}


def _gpus() -> dict:
    """通过 nvidia-smi 拿 GPU 状态。无驱动或非 NV 卡环境直接 ok=True, items=[]。"""
    smi = shutil.which('nvidia-smi')
    if not smi:
        return {'ok': True, 'items': [], 'reason': 'nvidia-smi 不可用'}
    try:
        proc = subprocess.run(
            [
                smi,
                '--query-gpu=index,name,utilization.gpu,memory.total,memory.used,temperature.gpu',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {'ok': False, 'items': [], 'reason': 'nvidia-smi 超时'}
    except OSError as exc:
        return {'ok': False, 'items': [], 'reason': str(exc)}

    if proc.returncode != 0:
        # 常见：驱动未安装。前端展示成"无 GPU"即可，不当作错误
        msg = (proc.stderr or proc.stdout or '').strip().splitlines()[:1]
        return {'ok': True, 'items': [], 'reason': msg[0] if msg else 'nvidia-smi 失败'}

    items = []
    for line in proc.stdout.splitlines():
        parts = [s.strip() for s in line.split(',')]
        if len(parts) < 6:
            continue
        try:
            items.append({
                'index': int(parts[0]),
                'name': parts[1],
                'util_percent': float(parts[2]),
                'mem_total_mib': float(parts[3]),
                'mem_used_mib': float(parts[4]),
                'temp_c': float(parts[5]) if parts[5] not in ('', 'N/A') else None,
            })
        except ValueError:
            continue
    return {'ok': True, 'items': items}


def get_system_stats(extra_disk_paths: Iterable[str] | None = None) -> dict:
    """一次性返回所有系统级指标，单点失败不影响其它。"""
    return {
        'ts': int(time.time()),
        'cpu': _cpu(),
        'memory': _memory(),
        'disk': _disk(extra_disk_paths),
        'load_avg': _load_avg(),
        'gpus': _gpus(),
        'psutil': _HAS_PSUTIL,
    }


# ---------------------------------------------------------------------------
# 进程级
# ---------------------------------------------------------------------------

def _proc_safe(pid: int | None):
    if not _HAS_PSUTIL or not pid:
        return None
    try:
        return psutil.Process(int(pid))
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
        return None


def get_proc_stats(pid: int | None) -> dict:
    """返回单个 PID 的资源占用；不存在 / 无权限时 alive=False。

    注意：``cpu_percent(interval=None)`` 首次调用会返回 0；前端建议轮询。
    """
    p = _proc_safe(pid)
    if p is None:
        return {'pid': pid, 'alive': False}
    try:
        with p.oneshot():
            cpu = p.cpu_percent(interval=None)
            mem = p.memory_info()
            create_time = p.create_time()
            num_threads = p.num_threads()
            try:
                cmdline = ' '.join(p.cmdline())[:200]
            except Exception:  # noqa: BLE001
                cmdline = ''
            children_pids = []
            try:
                for ch in p.children(recursive=True):
                    children_pids.append(ch.pid)
            except Exception:  # noqa: BLE001
                pass
        return {
            'pid': p.pid,
            'alive': True,
            'cpu_percent': cpu,
            'rss': mem.rss,
            'vms': mem.vms,
            'num_threads': num_threads,
            'create_time': create_time,
            'cmdline': cmdline,
            'children': children_pids,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {'pid': pid, 'alive': False}
    except Exception as exc:  # noqa: BLE001
        return {'pid': pid, 'alive': False, 'error': str(exc)}


def get_procs_stats(pids: Iterable[int | None]) -> list[dict]:
    """批量；过滤掉 None 后保证按入参顺序返回。"""
    out = []
    for pid in pids:
        out.append(get_proc_stats(pid))
    return out


# ---------------------------------------------------------------------------
# 预热：psutil.cpu_percent(interval=None) 首次调用是 0，需要"打底"一次
# ---------------------------------------------------------------------------

def warmup():
    if not _HAS_PSUTIL:
        return
    try:
        psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        pass


warmup()
