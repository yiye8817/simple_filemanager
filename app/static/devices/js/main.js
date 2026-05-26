/* 设备管理页前端逻辑。
 *
 * 设计要点:
 * 1) 选中模型: 左侧选中一台设备 → 右边渲染详情/终端/服务; 列表轮询 5s 刷一次。
 * 2) 终端 = 异步任务: send → 后端 enqueue 一个 exec 任务 → 前端把 task_id 加入轮询;
 *    每 1.5s 拉一次 status, 直到 status ∈ {done, error, timeout, cancelled} 就渲染 stdout/stderr/exit_code。
 * 3) 服务任务也同理 (install/start/stop/delete/log) - 全部走 enqueue + 轮询 task。
 * 4) 不依赖 WebSocket; 长轮询/短轮询全部基于 fetch + setTimeout 实现, 部署简单。
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // utils
  // ---------------------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function toast(msg, level = 'info') {
    const el = $('#dev-toast');
    if (!el) { console[level === 'error' ? 'error' : 'log'](msg); return; }
    el.classList.remove('text-bg-dark', 'text-bg-danger', 'text-bg-success', 'text-bg-warning');
    if (level === 'error') el.classList.add('text-bg-danger');
    else if (level === 'success') el.classList.add('text-bg-success');
    else if (level === 'warn') el.classList.add('text-bg-warning');
    else el.classList.add('text-bg-dark');
    $('#dev-toast-body').textContent = msg;
    bootstrap.Toast.getOrCreateInstance(el, { delay: 2800 }).show();
  }

  async function jfetch(url, options = {}) {
    const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, options));
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) {
      const err = (data && data.error) || `${res.status} ${res.statusText}`;
      const e = new Error(err); e.status = res.status; e.data = data;
      throw e;
    }
    return data || {};
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
  }

  function fmtBytes(n) {
    if (n == null) return '--';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = +n; if (Number.isNaN(v)) return '--';
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
  }

  function fmtSince(iso) {
    if (!iso) return '--';
    const t = new Date(iso).getTime();
    if (!t) return '--';
    const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (sec < 60) return `${sec}s 前`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m 前`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h 前`;
    return `${Math.floor(sec / 86400)}d 前`;
  }

  function fmtUptime(seconds) {
    if (seconds == null) return '--';
    let s = Math.max(0, Math.floor(seconds));
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600;
    const m = Math.floor(s / 60);
    const parts = [];
    if (d) parts.push(`${d}d`);
    if (h || d) parts.push(`${h}h`);
    parts.push(`${m}m`);
    return parts.join(' ');
  }

  /** 复制纯文本到剪贴板; 非 secureContext / 旧浏览器 fallback 到 execCommand。
   *  传 btn 时会在按钮上做一次短暂的 ✓ / ✗ 视觉反馈, 不依赖 toast。
   */
  async function copyToClipboard(text, opts = {}) {
    const { btn = null, successMsg = '已复制', failMsg = '复制失败' } = opts;
    const value = text == null ? '' : String(text);
    let ok = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        ok = true;
      } else {
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-1000px';
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand('copy');
        document.body.removeChild(ta);
      }
    } catch (_) { ok = false; }
    if (btn) {
      const icon = btn.querySelector('i');
      const oldCls = icon ? icon.className : '';
      if (icon) icon.className = ok ? 'bi bi-check2 me-1' : 'bi bi-x-lg me-1';
      btn.classList.toggle('btn-success', ok);
      btn.classList.toggle('btn-outline-secondary', !ok);
      setTimeout(() => {
        if (icon) icon.className = oldCls;
        btn.classList.remove('btn-success');
        if (!btn.classList.contains('btn-outline-secondary')) {
          btn.classList.add('btn-outline-secondary');
        }
      }, 1200);
    }
    toast(ok ? successMsg : failMsg, ok ? 'success' : 'error');
    return ok;
  }

  // ---------------------------------------------------------------------
  // 全局状态
  // ---------------------------------------------------------------------
  const state = {
    devices: [],
    currentId: null,
    query: '',
    onlineOnly: false,
    listTimer: null,
    detailTimer: null,
    termPolls: new Map(),     // task_id → { kind, label }
    svcTaskPolls: new Map(),  // task_id → { svc_id, kind }
    cmdHistory: [],
    cmdIdx: -1,
  };

  // ---------------------------------------------------------------------
  // 列表
  // ---------------------------------------------------------------------
  async function loadList(silent = false) {
    if (!silent) $('#dev-list').innerHTML = '<div class="text-center py-4 empty-pane">加载中…</div>';
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.onlineOnly) params.set('online', '1');
    try {
      const data = await jfetch('/api/devices' + (params.toString() ? `?${params}` : ''));
      state.devices = data.devices || [];
      renderList();
      $('#dev-updated').textContent = new Date().toLocaleTimeString();
      if (state.currentId) {
        const cur = state.devices.find((d) => d.id === state.currentId);
        if (!cur) clearDetail();
        else renderDetail(cur);
      }
    } catch (err) {
      $('#dev-list').innerHTML = `<div class="text-center py-4 text-danger small">加载失败: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderList() {
    const list = $('#dev-list');
    $('#dev-count').textContent = `共 ${state.devices.length} 台`;
    if (!state.devices.length) {
      list.innerHTML = '<div class="text-center py-4 empty-pane">没有匹配的设备。先创建一台吧。</div>';
      return;
    }
    list.innerHTML = state.devices.map((d) => {
      const dot = d.online ? 'status-online' : 'status-offline';
      const active = d.id === state.currentId ? 'active' : '';
      const cpu = d.stats && d.stats.cpu && d.stats.cpu.percent != null
        ? `${Math.round(d.stats.cpu.percent)}%` : '--';
      const mem = d.stats && d.stats.memory && d.stats.memory.percent != null
        ? `${Math.round(d.stats.memory.percent)}%` : '--';
      const ip = d.public_ip || d.last_ip || d.local_ip || '--';
      const tags = (d.tags || []).slice(0, 3).map((t) =>
        `<span class="badge bg-light text-secondary me-1">${escapeHtml(t)}</span>`).join('');
      return `
        <a href="#" class="list-group-item list-group-item-action dev-card ${active}" data-dev-id="${d.id}">
          <div class="d-flex align-items-start">
            <span class="status-dot ${dot} me-2 mt-1"></span>
            <div class="flex-grow-1 text-truncate">
              <div class="d-flex align-items-center">
                <div class="fw-semibold text-truncate">${escapeHtml(d.name)}</div>
                <div class="ms-auto small text-muted">${escapeHtml(fmtSince(d.last_seen_at))}</div>
              </div>
              <div class="small text-muted text-truncate">${escapeHtml(d.location || '--')} · ${escapeHtml(ip)}</div>
              <div class="small mt-1">${tags}</div>
              <div class="small text-muted">CPU ${cpu} · MEM ${mem}</div>
            </div>
          </div>
        </a>`;
    }).join('');
    list.querySelectorAll('[data-dev-id]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        selectDevice(parseInt(el.getAttribute('data-dev-id'), 10));
      });
    });
  }

  function selectDevice(id) {
    state.currentId = id;
    const d = state.devices.find((x) => x.id === id);
    if (!d) { clearDetail(); return; }
    $$('#dev-list [data-dev-id]').forEach((el) => {
      el.classList.toggle('active', parseInt(el.getAttribute('data-dev-id'), 10) === id);
    });
    renderDetail(d);
    refreshServices();
    refreshTunnels();
    stopDetailTimer();
    state.detailTimer = setInterval(() => {
      if (document.hidden) return;
      if (state.currentId === id) refreshSingleDevice();
    }, 5000);
  }

  function clearDetail() {
    state.currentId = null;
    stopDetailTimer();
    $('#dev-detail').style.display = 'none';
    $('#dev-empty').style.display = '';
    // 隧道卡片清空 (没设备就没数据)
    const sumEl = $('#tun-summary');
    const listEl = $('#tun-list');
    if (sumEl) sumEl.textContent = '先在左边选一台设备';
    if (listEl) listEl.innerHTML = '';
  }

  function stopDetailTimer() {
    if (state.detailTimer) { clearInterval(state.detailTimer); state.detailTimer = null; }
  }

  async function refreshSingleDevice() {
    try {
      const data = await jfetch(`/api/devices/${state.currentId}`);
      const d = data.device;
      if (!d) return;
      // 更新内存中的 list 副本
      const idx = state.devices.findIndex((x) => x.id === d.id);
      if (idx >= 0) state.devices[idx] = d;
      renderDetail(d);
    } catch (_) { /* ignore */ }
  }

  function renderDetail(d) {
    $('#dev-empty').style.display = 'none';
    $('#dev-detail').style.display = '';
    $('#dd-name').textContent = d.name;
    $('#dd-uid').textContent = d.device_uid;
    $('#dd-host').textContent = d.hostname || '--';
    $('#dd-os').textContent = [d.os_name, d.os_version].filter(Boolean).join(' ') || '--';
    $('#dd-arch').textContent = d.arch || '--';
    $('#dd-agent').textContent = d.agent_version || '--';
    $('#dd-loc').textContent = d.location || '--';
    $('#dd-tags').innerHTML = (d.tags && d.tags.length)
      ? d.tags.map((t) => `<span class="badge bg-light text-secondary me-1">${escapeHtml(t)}</span>`).join('')
      : '--';
    $('#dd-localip').textContent = d.local_ip || '--';
    $('#dd-pubip').textContent = d.public_ip || '--';
    $('#dd-lastip').textContent = d.last_ip || '--';
    const tunnelText = d.tunnel_info && Object.keys(d.tunnel_info).length
      ? JSON.stringify(d.tunnel_info)
      : '--';
    $('#dd-tunnel').textContent = tunnelText;
    $('#dd-tunnel').title = tunnelText;

    $('#dd-dot').className = 'status-dot me-2 ' + (d.online ? 'status-online' : 'status-offline');
    const badge = $('#dd-online-badge');
    badge.className = 'badge ms-2 ' + (d.online ? 'bg-success' : 'bg-secondary');
    badge.textContent = d.online ? '在线' : '离线';
    $('#dd-last-seen').textContent = '最近心跳: ' + fmtSince(d.last_seen_at);

    const stats = d.stats || {};
    const cpu = stats.cpu || {}; const mem = stats.memory || {};
    const disk = stats.disk || {}; const gpu = (stats.gpus && stats.gpus[0]) || null;
    const cpuPct = cpu.percent != null ? Math.round(cpu.percent) : null;
    $('#dd-cpu').textContent = cpuPct != null ? `${cpuPct}%` : '--';
    $('#dd-cpu-bar').style.width = (cpuPct || 0) + '%';
    if (mem.percent != null) {
      $('#dd-mem').textContent = `${Math.round(mem.percent)}%`;
      $('#dd-mem-bar').style.width = mem.percent + '%';
      $('#dd-mem').title = `${fmtBytes(mem.used)} / ${fmtBytes(mem.total)}`;
    } else { $('#dd-mem').textContent = '--'; }

    let rootDisk = null;
    if (Array.isArray(disk.items)) rootDisk = disk.items.find((x) => x.path === '/') || disk.items[0];
    if (rootDisk && rootDisk.percent != null) {
      $('#dd-disk').textContent = `${rootDisk.percent}%`;
      $('#dd-disk-bar').style.width = rootDisk.percent + '%';
      $('#dd-disk').title = `${fmtBytes(rootDisk.used)} / ${fmtBytes(rootDisk.total)}`;
    } else { $('#dd-disk').textContent = '--'; }

    if (gpu) {
      $('#dd-gpu').textContent = `${gpu.util_percent || 0}%`;
      $('#dd-gpu-name').textContent = gpu.name || '';
      $('#dd-gpu-name').title = gpu.name || '';
    } else { $('#dd-gpu').textContent = '无'; $('#dd-gpu-name').textContent = ''; }

    $('#dd-uptime').textContent = fmtUptime(stats.uptime_seconds);
    if (d.description) {
      $('#dd-desc').classList.remove('d-none');
      $('#dd-desc').textContent = d.description;
    } else {
      $('#dd-desc').classList.add('d-none');
    }
    renderHardwareDetail(d);
  }

  // -------------------------------------------------------------------
  // 硬件详情区: 把 stats_json 里的 CPU / 内存 / 磁盘 / GPU / 网卡 / 系统 全部铺开
  // -------------------------------------------------------------------
  function renderHardwareDetail(d) {
    const stats = d.stats || {};
    const cpu = stats.cpu || {};
    const mem = stats.memory || {};
    const disk = stats.disk || {};
    const gpus = Array.isArray(stats.gpus) ? stats.gpus : [];
    const net = stats.net || {};
    const sys = stats.system || {};
    const load = stats.load_avg || {};

    $('#hw-ts').textContent = stats.ts
      ? '采集于 ' + new Date(stats.ts * 1000).toLocaleString()
      : '--';

    // ---- CPU ----
    $('#hwc-model').textContent = cpu.model || '--';
    $('#hwc-model').title = cpu.model || '';
    const phy = cpu.count_physical != null ? cpu.count_physical : '?';
    const log = cpu.count_logical != null ? cpu.count_logical : '?';
    $('#hwc-cores').textContent = `${phy} 物理 / ${log} 逻辑`;
    $('#hwc-freq').textContent = cpu.freq_current_mhz != null
      ? `${cpu.freq_current_mhz} MHz`
      : '--';
    const fmax = cpu.freq_max_mhz != null ? `${cpu.freq_max_mhz} MHz` : '?';
    const fmin = cpu.freq_min_mhz != null ? `${cpu.freq_min_mhz} MHz` : '?';
    $('#hwc-freq-range').textContent = `${fmax} / ${fmin}`;
    $('#hwc-percent').textContent = cpu.percent != null
      ? `${Math.round(cpu.percent)}%` : '--';
    $('#hwc-load').textContent = (load['1m'] != null)
      ? `${load['1m'].toFixed(2)} / ${load['5m'].toFixed(2)} / ${load['15m'].toFixed(2)}`
      : '--';
    $('#hwc-ctxsw').textContent = (cpu.ctx_switches != null && cpu.interrupts != null)
      ? `${cpu.ctx_switches.toLocaleString()} / ${cpu.interrupts.toLocaleString()}`
      : '--';

    const perCore = $('#hwc-percore');
    perCore.innerHTML = '';
    const cores = Array.isArray(cpu.per_core_percent) ? cpu.per_core_percent : [];
    if (cores.length) {
      cores.forEach((v, idx) => {
        const pct = Math.round(v || 0);
        const cls = pct >= 80 ? 'bg-danger' : pct >= 50 ? 'bg-warning' : 'bg-info';
        const row = document.createElement('div');
        row.className = 'd-flex align-items-center small';
        row.innerHTML = `
          <span class="text-muted" style="width:32px;">#${idx}</span>
          <div class="progress flex-grow-1 me-2" style="height:6px;">
            <div class="progress-bar ${cls}" style="width:${pct}%"></div>
          </div>
          <span class="text-muted" style="width:42px; text-align:right;">${pct}%</span>`;
        perCore.appendChild(row);
      });
    } else {
      perCore.innerHTML = '<div class="text-muted small">（无每核数据 — agent 端 psutil 不可用?）</div>';
    }

    // ---- 内存 ----
    $('#hwm-total').textContent = fmtBytes(mem.total);
    $('#hwm-used').textContent = fmtBytes(mem.used);
    $('#hwm-avail').textContent = fmtBytes(mem.available);
    $('#hwm-bar').style.width = (mem.percent || 0) + '%';
    $('#hwm-swap-total').textContent = fmtBytes(mem.swap_total);
    $('#hwm-swap-used').textContent = fmtBytes(mem.swap_used);
    $('#hwm-swap-bar').style.width = (mem.swap_percent || 0) + '%';

    // ---- 磁盘 ----
    const dt = $('#hwd-tbody');
    const items = Array.isArray(disk.items) ? disk.items : [];
    if (items.length) {
      dt.innerHTML = items.map((x) => {
        const pct = Math.round(x.percent || 0);
        const cls = pct >= 90 ? 'bg-danger' : pct >= 70 ? 'bg-warning' : 'bg-success';
        return `<tr>
          <td class="text-truncate" style="max-width:160px;" title="${escapeHtml(x.path || '')}">${escapeHtml(x.path || '--')}</td>
          <td class="text-truncate" style="max-width:140px;" title="${escapeHtml(x.device || '')}">${escapeHtml(x.device || '--')}</td>
          <td>${escapeHtml(x.fstype || '--')}</td>
          <td>${fmtBytes(x.total)}</td>
          <td class="text-end">${fmtBytes(x.used)}</td>
          <td class="text-end">${fmtBytes(x.free)}</td>
          <td>
            <div class="d-flex align-items-center small">
              <div class="progress flex-grow-1 me-2" style="height:6px;"><div class="progress-bar ${cls}" style="width:${pct}%"></div></div>
              <span class="text-muted" style="width:46px;text-align:right;">${pct}%</span>
            </div>
          </td>
          <td class="text-truncate" style="max-width:160px;" title="${escapeHtml(x.model || '')}">
            ${escapeHtml(x.model || (x.rotational === true ? 'HDD' : x.rotational === false ? 'SSD' : '--'))}
            ${x.rotational === true ? ' <span class="badge bg-light text-secondary">HDD</span>' : ''}
            ${x.rotational === false ? ' <span class="badge bg-light text-info">SSD</span>' : ''}
          </td>
        </tr>`;
      }).join('');
    } else {
      dt.innerHTML = '<tr><td colspan="8" class="text-muted text-center">无数据</td></tr>';
    }

    // ---- GPU ----
    const gl = $('#hwg-list');
    if (gpus.length) {
      gl.innerHTML = gpus.map((g) => {
        const util = g.util_percent != null ? Math.round(g.util_percent) : null;
        const memUsed = g.mem_used_mib, memTotal = g.mem_total_mib;
        const memPct = (memTotal && memUsed != null) ? Math.round(memUsed / memTotal * 100) : null;
        return `<div class="border rounded p-2">
          <div class="d-flex align-items-center">
            <i class="bi bi-gpu-card text-primary me-2"></i>
            <span class="fw-semibold">#${g.index} ${escapeHtml(g.name || '?')}</span>
            <span class="ms-2 small text-muted">driver ${escapeHtml(g.driver || '--')}</span>
          </div>
          <div class="row g-2 mt-1">
            <div class="col-md-4">
              <div class="small text-muted">GPU 利用率</div>
              <div class="d-flex align-items-center"><div class="progress flex-grow-1 me-2" style="height:6px;"><div class="progress-bar bg-info" style="width:${util || 0}%"></div></div><span class="small text-muted" style="width:42px;text-align:right;">${util != null ? util + '%' : '--'}</span></div>
            </div>
            <div class="col-md-4">
              <div class="small text-muted">显存 ${memUsed != null ? Math.round(memUsed) : '--'} / ${memTotal != null ? Math.round(memTotal) : '--'} MiB</div>
              <div class="d-flex align-items-center"><div class="progress flex-grow-1 me-2" style="height:6px;"><div class="progress-bar bg-warning" style="width:${memPct || 0}%"></div></div><span class="small text-muted" style="width:42px;text-align:right;">${memPct != null ? memPct + '%' : '--'}</span></div>
            </div>
            <div class="col-md-4 small">
              <div>温度: ${g.temp_c != null ? Math.round(g.temp_c) + ' °C' : '--'}</div>
              <div>功耗: ${g.power_w != null ? Math.round(g.power_w) + ' W' : '--'} / ${g.power_limit_w != null ? Math.round(g.power_limit_w) + ' W' : '--'}</div>
              <div>风扇: ${g.fan_percent != null ? Math.round(g.fan_percent) + ' %' : '--'} · SM ${g.sm_clock_mhz != null ? Math.round(g.sm_clock_mhz) : '--'} MHz · Mem ${g.mem_clock_mhz != null ? Math.round(g.mem_clock_mhz) : '--'} MHz</div>
            </div>
          </div>
        </div>`;
      }).join('');
    } else {
      gl.innerHTML = '<div class="text-muted small">无 GPU 数据 (设备未装 nvidia-smi 或没有 NVIDIA GPU)</div>';
    }

    // ---- 网络 ----
    const nt = $('#hwn-tbody');
    const ifaces = Array.isArray(net.interfaces) ? net.interfaces : [];
    if (ifaces.length) {
      nt.innerHTML = ifaces.map((i) => {
        const up = i.is_up === true;
        const upBadge = i.is_up == null ? '--'
          : `<span class="badge ${up ? 'bg-success' : 'bg-secondary'}">${up ? 'UP' : 'DOWN'}</span>`;
        return `<tr>
          <td>${escapeHtml(i.name || '--')}</td>
          <td>${upBadge}</td>
          <td class="font-monospace small">${escapeHtml(i.mac || '--')}</td>
          <td class="font-monospace small">${escapeHtml((i.ipv4 || []).join(', ') || '--')}</td>
          <td class="font-monospace small text-truncate" style="max-width:280px;">${escapeHtml((i.ipv6 || []).join(', ') || '--')}</td>
          <td>${i.speed_mbps != null ? i.speed_mbps + ' Mbps' : '--'}</td>
          <td>${i.mtu != null ? i.mtu : '--'}</td>
        </tr>`;
      }).join('');
    } else {
      nt.innerHTML = '<tr><td colspan="7" class="text-muted text-center">无数据</td></tr>';
    }

    // ---- 系统 ----
    const sg = $('#hws-grid');
    const sysItems = [
      ['主机名', sys.hostname || d.hostname],
      ['OS', sys.os_version_full || sys.os_release || d.os_version],
      ['内核', sys.distro_pretty_name || sys.uname || sys.platform],
      ['架构', sys.arch || d.arch],
      ['Agent', sys.agent_version || d.agent_version],
      ['Agent PID', sys.agent_pid],
      ['Python', sys.python_version],
      ['启动时间', sys.boot_time ? new Date(sys.boot_time * 1000).toLocaleString() : null],
      ['运行时长', stats.uptime_seconds ? fmtUptime(stats.uptime_seconds) : null],
    ];
    sg.innerHTML = sysItems.map(([k, v]) => v
      ? `<div class="col-md-4 kv-row d-flex"><span class="k">${escapeHtml(k)}</span><span class="text-truncate" title="${escapeHtml(String(v))}">${escapeHtml(String(v))}</span></div>`
      : ''
    ).join('') || '<div class="text-muted small">无系统信息</div>';

    // ---- 原始 JSON ----
    try {
      $('#hw-raw-json').textContent = JSON.stringify(stats, null, 2);
    } catch (_) {
      $('#hw-raw-json').textContent = '(无法序列化)';
    }
  }

  // ---------------------------------------------------------------------
  // 搜索 / 新建 / 编辑 / 删除 / token
  // ---------------------------------------------------------------------
  let _searchTimer = null;
  $('#dev-search').addEventListener('input', (e) => {
    state.query = e.target.value;
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => loadList(true), 200);
  });
  $('#dev-only-online').addEventListener('change', (e) => {
    state.onlineOnly = e.target.checked; loadList(true);
  });
  $('#dev-refresh').addEventListener('click', () => loadList());
  const _hwRefresh = document.getElementById('hw-refresh');
  if (_hwRefresh) {
    _hwRefresh.addEventListener('click', (e) => {
      e.preventDefault();
      if (state.currentId) refreshSingleDevice();
    });
  }

  $('#dev-create-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd.entries());
    const btn = $('#dev-create-btn');
    btn.disabled = true;
    try {
      const data = await jfetch('/api/devices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      toast('设备已创建', 'success');
      e.target.reset();
      const d = data.device;
      showNewToken(d);
      await loadList(true);
      selectDevice(d.id);
    } catch (err) {
      toast('创建失败: ' + err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  });

  let _newTokenModal = null;
  function showNewToken(d) {
    $('#nt-uid').value = d.device_uid;
    $('#nt-token').value = d.device_token;
    const baseUrl = window.location.origin;
    $('#nt-cmd').textContent =
      `python -m device_agent \\\n  --server-url ${baseUrl} \\\n  --device-token ${d.device_token} \\\n  --device-uid ${d.device_uid}`;
    if (!_newTokenModal) _newTokenModal = new bootstrap.Modal($('#dev-new-token-modal'));
    _newTokenModal.show();
  }

  // 编辑 modal
  $('#dd-edit-btn').addEventListener('click', () => {
    const cur = state.devices.find((d) => d.id === state.currentId);
    if (!cur) return;
    const form = $('#dev-edit-form');
    form.name.value = cur.name || '';
    form.location.value = cur.location || '';
    form.tags.value = (cur.tags || []).join(', ');
    form.description.value = cur.description || '';
  });
  $('#dev-edit-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!state.currentId) return;
    const fd = new FormData(e.target);
    const body = Object.fromEntries(fd.entries());
    try {
      await jfetch(`/api/devices/${state.currentId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      bootstrap.Modal.getInstance($('#dev-edit-modal')).hide();
      toast('已保存', 'success');
      await loadList(true);
    } catch (err) {
      toast('保存失败: ' + err.message, 'error');
    }
  });

  $('#dd-delete-btn').addEventListener('click', async () => {
    const cur = state.devices.find((d) => d.id === state.currentId);
    if (!cur) return;
    if (!confirm(`确定删除设备「${cur.name}」？\n所有任务、服务、归档都会一并清理。\n这个操作不可恢复。`)) return;
    try {
      await jfetch(`/api/devices/${cur.id}`, { method: 'DELETE' });
      toast('已删除', 'success');
      clearDetail();
      await loadList(true);
    } catch (err) {
      toast('删除失败: ' + err.message, 'error');
    }
  });

  $('#dev-token-rotate').addEventListener('click', async () => {
    if (!state.currentId) return;
    if (!confirm('确定重置 token？旧 token 立刻失效, 已部署的 agent 必须更新配置。')) return;
    try {
      const data = await jfetch(`/api/devices/${state.currentId}/rotate-token`, { method: 'POST' });
      $('#dev-token-text').value = data.token;
      $('#dev-token-new').classList.remove('d-none');
      toast('已生成新 token', 'success');
    } catch (err) {
      toast('重置失败: ' + err.message, 'error');
    }
  });

  // 复制按钮 (事件代理, 因为 modal 内的按钮可能动态)
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const sel = btn.getAttribute('data-copy-target');
    if (sel) copyToClipboard($(sel).value, { btn });
  });

  // 服务日志 modal 内的"复制"按钮
  $('#svc-log-copy').addEventListener('click', (e) => {
    const text = $('#svc-log-pane').textContent || '';
    copyToClipboard(text, { btn: e.currentTarget, successMsg: '日志已复制' });
  });

  // ---------------------------------------------------------------------
  // 远程终端
  // ---------------------------------------------------------------------
  function pushTerm(html, cls = '') {
    const pane = $('#term-pane');
    const div = document.createElement('div');
    if (cls) div.className = cls;
    div.innerHTML = html;
    pane.appendChild(div);
    pane.scrollTop = pane.scrollHeight;
  }
  function clearTerm() {
    $('#term-pane').innerHTML = '<span class="empty-pane">$ 等待输入命令…</span>';
  }
  $('#term-clear').addEventListener('click', clearTerm);

  $('#term-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!state.currentId) { toast('请先选择一台设备', 'warn'); return; }
    const input = $('#term-cmd');
    const cmd = (input.value || '').trim();
    if (!cmd) return;
    if (cmd === 'clear') { clearTerm(); input.value = ''; return; }
    const cwd = ($('#term-cwd').value || '').trim() || null;
    const timeout = Math.max(1, Math.min(parseInt($('#term-timeout').value || '30', 10), 600));
    if ($('#term-pane').querySelector('.empty-pane')) $('#term-pane').innerHTML = '';
    pushTerm(`<span class="prompt">$ ${escapeHtml(cmd)}</span>` +
             (cwd ? `  <span class="text-muted">[${escapeHtml(cwd)}]</span>` : ''));
    const placeholder = document.createElement('div');
    placeholder.className = 'pending';
    placeholder.innerHTML = '<i class="bi bi-hourglass-split"></i> 已下发, 等待 agent 执行…';
    $('#term-pane').appendChild(placeholder);
    state.cmdHistory.push(cmd);
    state.cmdIdx = state.cmdHistory.length;
    input.value = '';

    try {
      const data = await jfetch(`/api/devices/${state.currentId}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd, cwd, timeout }),
      });
      const t = data.task;
      pollTask(t.id, (final) => onCommandDone(placeholder, final));
    } catch (err) {
      placeholder.classList.remove('pending');
      placeholder.classList.add('err');
      placeholder.textContent = '下发失败: ' + err.message;
    }
  });

  // ↑↓ 历史
  $('#term-cmd').addEventListener('keydown', (e) => {
    if (e.key === 'ArrowUp') {
      if (state.cmdIdx > 0) state.cmdIdx--;
      const v = state.cmdHistory[state.cmdIdx] || '';
      e.target.value = v;
      e.preventDefault();
    } else if (e.key === 'ArrowDown') {
      if (state.cmdIdx < state.cmdHistory.length - 1) state.cmdIdx++;
      else { state.cmdIdx = state.cmdHistory.length; e.target.value = ''; e.preventDefault(); return; }
      e.target.value = state.cmdHistory[state.cmdIdx] || '';
      e.preventDefault();
    }
  });

  function onCommandDone(placeholder, t) {
    placeholder.classList.remove('pending');
    const parts = [];
    if (t.stdout) parts.push(`<div>${escapeHtml(t.stdout)}</div>`);
    if (t.stderr) parts.push(`<div class="err">${escapeHtml(t.stderr)}</div>`);
    const status = t.status;
    const exit = typeof t.exit_code === 'number' ? ` · exit=${t.exit_code}` : '';
    const tail = `<div class="small ${status === 'done' ? 'ok' : 'err'}">[${status}${exit}]</div>`;
    placeholder.innerHTML = (parts.join('') || '<span class="text-muted small">(无输出)</span>') + tail;
  }

  function pollTask(taskId, onDone) {
    const tick = async () => {
      try {
        const data = await jfetch(`/api/devices/${state.currentId}/tasks/${taskId}`);
        const t = data.task;
        if (!t) return;
        if (['done', 'error', 'timeout', 'cancelled'].includes(t.status)) {
          onDone(t);
        } else {
          setTimeout(tick, 1500);
        }
      } catch (err) {
        onDone({ status: 'error', stderr: '查询失败: ' + err.message });
      }
    };
    setTimeout(tick, 800);
  }

  // ---------------------------------------------------------------------
  // 设备上的服务
  // ---------------------------------------------------------------------
  let _svcLogModal = null;
  function svcLogModal() {
    if (!_svcLogModal) _svcLogModal = new bootstrap.Modal($('#svc-log-modal'));
    return _svcLogModal;
  }

  async function refreshServices() {
    if (!state.currentId) return;
    const list = $('#dev-svc-list');
    try {
      const data = await jfetch(`/api/devices/${state.currentId}/services`);
      const services = data.services || [];
      if (!services.length) {
        list.innerHTML = '<div class="text-center py-4 empty-pane">还没有上传任何服务。</div>';
        return;
      }
      list.innerHTML = services.map(renderServiceRow).join('');
      list.querySelectorAll('[data-svc-action]').forEach((btn) => {
        btn.addEventListener('click', () => onServiceAction(btn));
      });
    } catch (err) {
      list.innerHTML = `<div class="text-center py-4 text-danger small">加载失败: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderServiceRow(s) {
    const statusMap = {
      running: ['bg-success', '运行中'],
      stopped: ['bg-secondary', '已停止'],
      idle: ['bg-light text-dark', '未启动'],
      installing: ['bg-info text-dark', '安装中'],
      failed: ['bg-danger', '失败'],
    };
    const [cls, label] = statusMap[s.status] || ['bg-light text-dark', s.status || '--'];
    const env = s.env && Object.keys(s.env).length ? `· env=${escapeHtml(JSON.stringify(s.env))}` : '';
    const ports = s.local_port ? `<span class="badge bg-light text-dark badge-port">port=${s.local_port}</span>` : '';
    const isRunning = s.status === 'running';
    return `
      <div class="list-group-item svc-row status-${s.status || 'idle'}">
        <div class="d-flex align-items-start gap-2">
          <div class="flex-grow-1">
            <div class="d-flex align-items-center gap-2">
              <span class="fw-semibold">${escapeHtml(s.name)}</span>
              <span class="badge ${cls}">${label}</span>
              ${ports}
              <span class="small text-muted ms-2">${escapeHtml(s.archive_filename)} · ${fmtBytes(s.archive_size)}</span>
            </div>
            <div class="small text-muted mt-1">
              ${s.run_args ? `args=<code>${escapeHtml(s.run_args)}</code>` : ''}
              ${env}
              ${typeof s.pid === 'number' ? `· pid=${s.pid}` : ''}
              ${s.last_started_at ? `· 启动 ${fmtSince(s.last_started_at)}` : ''}
              ${s.last_stopped_at ? `· 停止 ${fmtSince(s.last_stopped_at)}` : ''}
            </div>
            ${s.last_error ? `<div class="small text-danger mt-1">${escapeHtml(s.last_error)}</div>` : ''}
          </div>
          <div class="btn-group btn-group-sm flex-shrink-0">
            <button class="btn btn-outline-success" data-svc-action="start" data-svc-id="${s.id}" ${isRunning ? 'disabled' : ''}>
              <i class="bi bi-play-fill"></i>
            </button>
            <button class="btn btn-outline-warning" data-svc-action="stop" data-svc-id="${s.id}" ${!isRunning ? 'disabled' : ''}>
              <i class="bi bi-stop-fill"></i>
            </button>
            <button class="btn btn-outline-secondary" data-svc-action="log" data-svc-id="${s.id}" data-svc-name="${escapeHtml(s.name)}">
              <i class="bi bi-terminal"></i>
            </button>
            <button class="btn btn-outline-danger" data-svc-action="delete" data-svc-id="${s.id}">
              <i class="bi bi-trash"></i>
            </button>
          </div>
        </div>
      </div>`;
  }

  async function onServiceAction(btn) {
    const action = btn.getAttribute('data-svc-action');
    const sid = parseInt(btn.getAttribute('data-svc-id'), 10);
    const did = state.currentId;
    if (!did || !sid) return;
    try {
      if (action === 'start') {
        const data = await jfetch(`/api/devices/${did}/services/${sid}/start`, { method: 'POST' });
        toast('已下发启动任务', 'info');
        watchServiceTask(data.task);
      } else if (action === 'stop') {
        const data = await jfetch(`/api/devices/${did}/services/${sid}/stop`, { method: 'POST' });
        toast('已下发停止任务', 'info');
        watchServiceTask(data.task);
      } else if (action === 'log') {
        $('#svc-log-name').textContent = btn.getAttribute('data-svc-name') || '服务日志';
        $('#svc-log-pane').textContent = '(请求拉取中…)';
        $('#svc-log-meta').textContent = '设备 agent 拉到任务后才会回传日志, 通常 < 10s';
        svcLogModal().show();
        const data = await jfetch(`/api/devices/${did}/services/${sid}/log?lines=300`, { method: 'POST' });
        watchServiceLogTask(data.task);
      } else if (action === 'delete') {
        if (!confirm('确定卸载该服务? 设备上的解压目录会被清理, 服务器端归档也会删除。')) return;
        await jfetch(`/api/devices/${did}/services/${sid}`, { method: 'DELETE' });
        toast('已删除', 'success');
        refreshServices();
      }
    } catch (err) {
      toast('操作失败: ' + err.message, 'error');
    }
  }

  function watchServiceTask(t) {
    if (!t) return;
    pollTask(t.id, (final) => {
      if (final.status === 'done') toast('任务成功', 'success');
      else if (final.status !== 'cancelled') toast('任务失败: ' + (final.error || final.stderr || '').slice(0, 80), 'error');
      refreshServices();
    });
  }
  function watchServiceLogTask(t) {
    if (!t) return;
    pollTask(t.id, (final) => {
      if (final.status === 'done') {
        $('#svc-log-pane').textContent = final.stdout || '(无日志)';
        $('#svc-log-meta').textContent = '更新于 ' + new Date().toLocaleString();
      } else {
        $('#svc-log-pane').textContent = '拉取失败: ' + (final.error || final.stderr || final.status);
      }
      refreshServices();
    });
  }
  $('#svc-log-fetch').addEventListener('click', () => {
    const did = state.currentId;
    if (!did) return;
    const sid = parseInt($('#svc-log-modal').getAttribute('data-svc-id') || '0', 10);
    if (!sid) return;
    onServiceAction({
      getAttribute: (k) => k === 'data-svc-action' ? 'log' :
                          k === 'data-svc-id' ? sid :
                          k === 'data-svc-name' ? $('#svc-log-name').textContent : '',
    });
  });

  $('#svc-refresh-btn').addEventListener('click', refreshServices);

  $('#svc-upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!state.currentId) return;
    const fd = new FormData(e.target);
    if (!fd.has('auto_install')) fd.append('auto_install', '0');
    const btn = $('#svc-upload-btn');
    btn.disabled = true;
    try {
      const data = await jfetch(`/api/devices/${state.currentId}/services`, {
        method: 'POST', body: fd,
      });
      toast('已上传并创建服务', 'success');
      bootstrap.Modal.getInstance($('#svc-upload-modal')).hide();
      e.target.reset();
      $('#svc-auto-install').checked = true;
      await refreshServices();
      if (data.task) watchServiceTask(data.task);
    } catch (err) {
      toast('上传失败: ' + err.message, 'error');
    } finally { btn.disabled = false; }
  });

  // ---------------------------------------------------------------------
  // 卡片折叠: 直接用共享 collapse.js (页面已经 include) 即可, 不在这里实现
  // ---------------------------------------------------------------------

  // ---------------------------------------------------------------------
  // 左侧 sidebar 折叠 (整个 col-lg-4 隐藏, 右侧充满)
  // ---------------------------------------------------------------------
  const SIDEBAR_LS_KEY = 'fm.devSidebar.hidden';
  function applySidebarState(hidden) {
    const sidebar = $('#dev-sidebar');
    const main = $('#dev-main');
    const btn = $('#dev-sidebar-toggle');
    if (!sidebar || !main) return;
    if (hidden) {
      sidebar.classList.add('d-none');
      main.classList.remove('col-lg-8');
      main.classList.add('col-12');
      if (btn) {
        btn.innerHTML = '<i class="bi bi-chevron-double-right me-1"></i><span class="d-none d-md-inline">显示设备列表</span>';
        btn.title = '显示设备列表';
      }
    } else {
      sidebar.classList.remove('d-none');
      main.classList.remove('col-12');
      main.classList.add('col-lg-8');
      if (btn) {
        btn.innerHTML = '<i class="bi bi-chevron-double-left me-1"></i><span class="d-none d-md-inline">隐藏设备列表</span>';
        btn.title = '隐藏设备列表';
      }
    }
  }
  function initSidebarToggle() {
    const btn = $('#dev-sidebar-toggle');
    if (!btn) return;
    let hidden = false;
    try { hidden = localStorage.getItem(SIDEBAR_LS_KEY) === '1'; } catch (_) {}
    applySidebarState(hidden);
    btn.addEventListener('click', () => {
      const cur = $('#dev-sidebar').classList.contains('d-none');
      const next = !cur;
      applySidebarState(next);
      try { localStorage.setItem(SIDEBAR_LS_KEY, next ? '1' : '0'); } catch (_) {}
    });
  }

  // ---------------------------------------------------------------------
  // 设备 frpc 隧道卡片: 拉 /api/devices/<id>/tunnels (agent 心跳上报), 22 端口 -> 一键网页 SSH
  // ---------------------------------------------------------------------
  async function refreshTunnels() {
    const listEl = $('#tun-list');
    const sumEl = $('#tun-summary');
    if (!listEl) return;
    if (!state.currentId) {
      sumEl.textContent = '先在左边选一台设备';
      listEl.innerHTML = '';
      return;
    }
    const user = (($('#tun-ssh-user') || {}).value || 'root').trim() || 'root';
    listEl.innerHTML = '<div class="text-center py-3 text-muted small">读取设备上报的 frpc 隧道…</div>';
    try {
      const data = await jfetch(`/api/devices/${state.currentId}/tunnels?user=` + encodeURIComponent(user));
      const insts = data.instances || [];
      const tsHint = data.stats_ts ? ` (采集时间 ${new Date(data.stats_ts * 1000).toLocaleString()})` : '';
      if (!insts.length) {
        if (data.frpc_error) {
          sumEl.innerHTML = `agent 报错: <code>${escapeHtml(data.frpc_error)}</code>${tsHint}`;
        } else {
          sumEl.textContent = '设备上没有正在运行的 frpc 进程, 或 agent 还没上报' + tsHint;
        }
        listEl.innerHTML = '<div class="text-muted small py-2">如果设备上确实在跑 frpc, 请确认 agent 已升级到 v0.2+ 并能读到 frpc.toml (例: <code>frpc -c /etc/frpc.toml</code>)。</div>';
        return;
      }
      let totalProxies = 0;
      const blocks = insts.map((inst) => {
        const proxies = inst.proxies || [];
        totalProxies += proxies.length;
        const fallbackBadge = inst.used_fallback_config
          ? '<span class="badge bg-info text-dark" title="agent --frpc-config fallback">fallback toml</span>' : '';
        const header = `
          <div class="d-flex flex-wrap gap-2 align-items-center small mb-2">
            <span class="badge bg-secondary">PID ${inst.pid || '-'}</span>
            <span class="text-muted">配置: <code>${escapeHtml(inst.config_path || '(未指定)')}</code></span>
            ${fallbackBadge}
            <span class="text-muted">服务端: <code>${escapeHtml(inst.server_addr || '--')}${inst.server_port ? ':' + inst.server_port : ''}</code></span>
            ${inst.error ? `<div class="w-100 alert alert-warning py-1 px-2 mb-0 small mt-1">
              <i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(inst.error)}
            </div>` : ''}
          </div>`;
        if (!proxies.length) {
          return header + '<div class="text-muted small mb-3">这个 frpc 实例没有任何 proxies。</div>';
        }
        const rows = proxies.map((p) => {
          const ssh = p.is_ssh && p.ssh_command ? `
            <div class="ssh-cmd text-muted">${escapeHtml(p.ssh_command)}</div>
            <div class="d-flex gap-2 mt-1 flex-wrap">
              <button class="btn btn-sm btn-success tun-open-webssh"
                      data-host="${escapeHtml(p.remote_host || '')}"
                      data-port="${p.remote_port}"
                      data-user="${escapeHtml(user)}">
                <i class="bi bi-terminal me-1"></i>网页终端登录
              </button>
              <button class="btn btn-sm btn-outline-primary tun-copy-ssh" data-cmd="${escapeHtml(p.ssh_command)}">
                <i class="bi bi-clipboard me-1"></i>复制 ssh 命令
              </button>
              <a class="btn btn-sm btn-outline-secondary" target="_blank"
                 href="ssh://${encodeURIComponent(user)}@${encodeURIComponent(p.remote_host || '')}:${p.remote_port}"
                 title="若本机注册了 ssh:// 协议处理器, 可调起系统 ssh 客户端">
                <i class="bi bi-box-arrow-up-right me-1"></i>ssh://
              </a>
            </div>` : '';
          const ext = (!p.is_ssh && p.external_url) ? `<div class="small text-muted">${escapeHtml(p.external_url)}</div>` : '';
          const usage = p.is_ssh
            ? '<span class="badge bg-warning text-dark">SSH 登录</span>'
            : `<span class="text-muted small">可分配给上传的服务 (localPort=${p.local_port})</span>`;
          const inUse = (p.in_use === true)
            ? '<span class="badge bg-success ms-1">已监听</span>'
            : (p.in_use === false ? '<span class="badge bg-secondary ms-1">空闲</span>' : '');
          return `
            <tr class="tunnel-row ${p.is_ssh ? 'is-ssh' : ''}">
              <td class="text-nowrap">
                <div class="fw-semibold">${escapeHtml(p.name)}</div>
                <div class="small text-muted">${escapeHtml(p.type || 'tcp')}</div>
              </td>
              <td class="text-nowrap">
                <span class="badge bg-light text-dark">本地 ${p.local_port}</span>
                <i class="bi bi-arrow-right mx-1 text-muted"></i>
                <span class="badge bg-light text-dark">远端 ${p.remote_port}</span>
                ${inUse}
              </td>
              <td>
                ${usage}
                ${ext}
                ${ssh}
              </td>
            </tr>`;
        }).join('');
        return header + `
          <div class="table-responsive mb-3">
            <table class="table table-sm align-middle">
              <thead class="table-light">
                <tr><th>Proxy</th><th>端口映射</th><th>用途 / 操作</th></tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>
          </div>`;
      }).join('');
      sumEl.textContent = `设备上有 ${insts.length} 个 frpc 实例 / ${totalProxies} 条隧道${tsHint}; 22 端口可一键网页 SSH 登录。`;
      listEl.innerHTML = blocks;
      listEl.querySelectorAll('.tun-copy-ssh').forEach((btn) => {
        btn.addEventListener('click', () => {
          copyToClipboard(btn.getAttribute('data-cmd') || '', { btn, successMsg: 'ssh 命令已复制' });
        });
      });
      listEl.querySelectorAll('.tun-open-webssh').forEach((btn) => {
        btn.addEventListener('click', () => {
          openSshTerminal({
            host: btn.getAttribute('data-host') || '',
            port: parseInt(btn.getAttribute('data-port') || '22', 10),
            user: btn.getAttribute('data-user') || 'root',
          });
        });
      });
    } catch (err) {
      sumEl.textContent = '加载失败';
      listEl.innerHTML = `<div class="text-danger small py-2">加载隧道失败: ${escapeHtml(err.message)}</div>`;
    }
  }
  function initTunnelsCard() {
    const refresh = $('#tun-refresh');
    if (refresh) refresh.addEventListener('click', refreshTunnels);
    const userInp = $('#tun-ssh-user');
    if (userInp) {
      try { userInp.value = localStorage.getItem('fm.tun.sshUser') || 'root'; } catch (_) {}
      userInp.addEventListener('change', () => {
        try { localStorage.setItem('fm.tun.sshUser', userInp.value || 'root'); } catch (_) {}
        refreshTunnels();
      });
    }
  }

  // ---------------------------------------------------------------------
  // 网页 SSH 终端 (xterm.js + /api/ssh/ws/<sid>)
  // ---------------------------------------------------------------------
  const ssh = {
    term: null,
    fit: null,
    ws: null,
    modal: null,
    sid: null,
    target: null,
    reconnectCfg: null,
  };

  function ensureXtermLoaded() {
    if (typeof window.Terminal === 'function') return true;
    toast('xterm.js 未加载, 请确认网络可访问 jsdelivr', 'error');
    return false;
  }

  function setSshStatus(text, kind = 'secondary') {
    const el = $('#ssh-term-status');
    if (!el) return;
    el.className = 'badge ms-2 bg-' + kind;
    el.textContent = text;
  }

  function openSshTerminal(prefill = {}) {
    if (!ensureXtermLoaded()) return;
    const modalEl = $('#ssh-term-modal');
    if (!modalEl) return;
    if (!ssh.modal) ssh.modal = new bootstrap.Modal(modalEl);

    // 预填登录字段
    if (prefill.host) $('#ssh-host').value = prefill.host;
    if (prefill.port) $('#ssh-port').value = String(prefill.port);
    if (prefill.user) $('#ssh-user-input').value = prefill.user;
    $('#ssh-pass').value = '';
    // 把登录表单显示出来, 隐藏终端
    $('#ssh-term-login').style.display = '';
    $('#ssh-term-pane').style.display = 'none';
    $('#ssh-term-reconnect').classList.add('d-none');
    setSshStatus('未连接', 'secondary');
    ssh.target = `${prefill.user || ''}@${prefill.host || ''}:${prefill.port || 22}`;
    $('#ssh-term-target').textContent = ssh.target;

    ssh.modal.show();
    // 关 modal 时收尾
    modalEl.addEventListener('hidden.bs.modal', cleanupSshSession, { once: true });
    setTimeout(() => { $('#ssh-pass').focus(); }, 200);
  }

  async function startSshSession(loginPayload) {
    setSshStatus('正在创建会话…', 'info');
    let sessionResp;
    try {
      sessionResp = await jfetch('/api/ssh/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(loginPayload),
      });
    } catch (err) {
      setSshStatus('创建会话失败', 'danger');
      toast('创建 SSH 会话失败: ' + err.message, 'error');
      return;
    }
    ssh.sid = sessionResp.sid;
    ssh.reconnectCfg = loginPayload;
    ssh.target = `${sessionResp.username}@${sessionResp.host}:${sessionResp.port}`;
    $('#ssh-term-target').textContent = ssh.target;

    // 切换 UI 到终端
    $('#ssh-term-login').style.display = 'none';
    $('#ssh-term-pane').style.display = '';

    // 创建/复用 xterm 实例
    if (!ssh.term) {
      ssh.term = new window.Terminal({
        cursorBlink: true,
        fontSize: 13,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        theme: { background: '#0b1020', foreground: '#d6deeb' },
        convertEol: false,
      });
      const FitCtor = (window.FitAddon && window.FitAddon.FitAddon) || null;
      ssh.fit = FitCtor ? new FitCtor() : null;
      if (ssh.fit) ssh.term.loadAddon(ssh.fit);
      ssh.term.open($('#ssh-term-pane'));
      ssh.term.onData((data) => sshWsSend({ type: 'input', data }));
      ssh.term.onResize(({ cols, rows }) => sshWsSend({ type: 'resize', cols, rows }));
      window.addEventListener('resize', sshFitNow);
    } else {
      ssh.term.reset();
    }
    sshFitNow();

    // 打开 WebSocket
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/api/ssh/ws/${encodeURIComponent(ssh.sid)}`
      + `?cols=${ssh.term.cols}&rows=${ssh.term.rows}`;
    setSshStatus('正在连接…', 'info');
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (err) {
      setSshStatus('WebSocket 失败', 'danger');
      toast('无法建立 WebSocket: ' + err.message, 'error');
      return;
    }
    ssh.ws = ws;
    ws.onopen = () => { setSshStatus('握手中…', 'info'); };
    ws.onmessage = (e) => {
      let m = null;
      try { m = JSON.parse(e.data); } catch (_) { return; }
      if (!m || !m.type) return;
      if (m.type === 'data') {
        ssh.term.write(m.data);
      } else if (m.type === 'status') {
        setSshStatus(m.status === 'connected' ? '已连接' : (m.status || ''),
          m.status === 'connected' ? 'success' : 'info');
        if (m.status === 'connected') {
          ssh.term.writeln(`\x1b[32m[connected ${m.msg || ''}]\x1b[0m`);
          // 重新发一次 resize 让对端的 PTY 拿到真实尺寸
          sshWsSend({ type: 'resize', cols: ssh.term.cols, rows: ssh.term.rows });
        }
      } else if (m.type === 'error') {
        setSshStatus('错误', 'danger');
        ssh.term.writeln(`\r\n\x1b[31m[error] ${m.msg || ''}\x1b[0m`);
        // 认证类错误给一段排错提示, 直接打到 xterm 里
        if (/AuthenticationException|BadAuthenticationType|authentication/i.test(m.msg || '')) {
          ssh.term.writeln('\x1b[33m排错提示:\x1b[0m');
          ssh.term.writeln('  1. 确认用户名 / 密码是否正确 (注意大小写、空格)');
          ssh.term.writeln('  2. 若 sshd 禁用了 password (只允许 publickey), 请改用私钥登录');
          ssh.term.writeln('  3. 若 sshd 启用了 2FA / OTP, Web 终端目前无法交互输入第二因子');
          ssh.term.writeln('  4. 可以 ssh -vvv 试一下相同凭据, 对比服务器实际接受的 auth method');
        }
        $('#ssh-term-reconnect').classList.remove('d-none');
      } else if (m.type === 'closed') {
        setSshStatus('已断开', 'secondary');
        ssh.term.writeln(`\r\n\x1b[33m[closed] ${m.msg || ''}\x1b[0m`);
        $('#ssh-term-reconnect').classList.remove('d-none');
      }
    };
    ws.onclose = () => {
      setSshStatus('已断开', 'secondary');
      $('#ssh-term-reconnect').classList.remove('d-none');
    };
    ws.onerror = () => {
      setSshStatus('WebSocket 错误', 'danger');
    };
  }

  function sshWsSend(payload) {
    if (!ssh.ws || ssh.ws.readyState !== 1) return;
    try { ssh.ws.send(JSON.stringify(payload)); } catch (_) { /* ignore */ }
  }

  function sshFitNow() {
    if (ssh.fit) {
      try { ssh.fit.fit(); } catch (_) { /* ignore */ }
    }
  }

  function cleanupSshSession() {
    if (ssh.ws) {
      try { ssh.ws.close(); } catch (_) { /* ignore */ }
      ssh.ws = null;
    }
    if (ssh.sid) {
      // best-effort 注销 (大多数情况 ws 已经消费了 sid, 这里只是兜底)
      fetch(`/api/ssh/sessions/${encodeURIComponent(ssh.sid)}`, {
        method: 'DELETE', credentials: 'same-origin',
      }).catch(() => {});
      ssh.sid = null;
    }
  }

  function initSshTerminal() {
    const connectBtn = $('#ssh-connect-btn');
    if (connectBtn) {
      connectBtn.addEventListener('click', () => {
        const host = ($('#ssh-host').value || '').trim();
        const port = parseInt($('#ssh-port').value || '22', 10);
        const username = ($('#ssh-user-input').value || '').trim();
        const password = $('#ssh-pass').value || '';
        const private_key = ($('#ssh-pkey').value || '').trim();
        const key_passphrase = $('#ssh-pkey-pass').value || '';
        if (!host || !username) {
          toast('host / username 必填', 'warn');
          return;
        }
        if (!password && !private_key) {
          toast('请填密码或私钥', 'warn');
          return;
        }
        startSshSession({
          host, port, username,
          password: password || null,
          private_key: private_key || null,
          key_passphrase: key_passphrase || null,
        });
      });
    }
    const reconnectBtn = $('#ssh-term-reconnect');
    if (reconnectBtn) {
      reconnectBtn.addEventListener('click', () => {
        if (!ssh.reconnectCfg) {
          $('#ssh-term-login').style.display = '';
          $('#ssh-term-pane').style.display = 'none';
          return;
        }
        if (ssh.ws) { try { ssh.ws.close(); } catch (_) {} ssh.ws = null; }
        startSshSession(ssh.reconnectCfg);
      });
    }
    // Enter in 密码框直接连
    const passEl = $('#ssh-pass');
    if (passEl) {
      passEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (connectBtn) connectBtn.click();
        }
      });
    }
  }
  // 暴露给外部触发
  window.openSshTerminal = openSshTerminal;

  // ---------------------------------------------------------------------
  // init
  // ---------------------------------------------------------------------
  async function init() {
    // 折叠由共享 collapse.js (DOMContentLoaded) 自动接管, 这里只兜底再 init 一次
    if (window.__fmCollapse) window.__fmCollapse.init();
    initSidebarToggle();
    initTunnelsCard();
    initSshTerminal();
    await loadList();
    state.listTimer = setInterval(() => {
      if (document.hidden) return;
      loadList(true);
    }, 8000);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopDetailTimer();
    else if (state.currentId) {
      refreshSingleDevice();
      state.detailTimer = setInterval(() => {
        if (document.hidden) return;
        refreshSingleDevice();
      }, 5000);
    }
  });

  init();
})();
