/* 服务管理页前端 — 与 /api/services/* 一组接口对接。
 *
 * 设计要点：
 * 1) 单选模型：左侧服务列表点选 → currentId 变化 → 拉详情 + 启动日志轮询。
 * 2) 状态自洽：每次 list/detail 接口返回均覆盖本地 cache，避免「点了启动按钮但 UI 还是 stopped」。
 * 3) 日志轮询：仅在选中且开关开启时轮询；切走/关闭/页面隐藏立刻停。
 * 4) 防重复点击：start/stop/delete 期间禁用按钮，避免 422/竞争。
 */
(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // 简单工具
  // ---------------------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function toast(msg, level = 'info') {
    const el = $('#svc-toast');
    if (!el) { console[level === 'error' ? 'error' : 'log'](msg); return; }
    el.classList.remove('text-bg-dark', 'text-bg-danger', 'text-bg-success', 'text-bg-warning');
    if (level === 'error') el.classList.add('text-bg-danger');
    else if (level === 'success') el.classList.add('text-bg-success');
    else if (level === 'warn') el.classList.add('text-bg-warning');
    else el.classList.add('text-bg-dark');
    $('#svc-toast-body').textContent = msg;
    bootstrap.Toast.getOrCreateInstance(el, { delay: 2800 }).show();
  }

  // ---------------------------------------------------------------------
  // frpc 路径偏好（localStorage 持久化）
  // ---------------------------------------------------------------------
  const FRPC_KEY = 'svc.frpc_path';
  function getFrpcPath() {
    try { return localStorage.getItem(FRPC_KEY) || ''; } catch (_) { return ''; }
  }
  function setFrpcPath(p) {
    try {
      if (p) localStorage.setItem(FRPC_KEY, p);
      else localStorage.removeItem(FRPC_KEY);
    } catch (_) { /* 私有模式忽略 */ }
  }

  /** 给请求 URL 自动追加 frpc_path 查询参数；POST/PUT/DELETE 同时塞进 body。 */
  function withFrpcParam(url, options = {}) {
    const p = getFrpcPath();
    if (!p) return [url, options];
    // GET / DELETE / 任何方法都先加 query（最简单可靠）
    const sep = url.indexOf('?') >= 0 ? '&' : '?';
    const newUrl = url + sep + 'frpc_path=' + encodeURIComponent(p);
    // 如果 body 是 FormData，再补一份，方便 form 解析
    if (options.body instanceof FormData && !options.body.has('frpc_path')) {
      options.body.append('frpc_path', p);
    }
    return [newUrl, options];
  }

  async function jfetch(url, options = {}) {
    const [u, opts] = withFrpcParam(url, options);
    const res = await fetch(u, Object.assign({ credentials: 'same-origin' }, opts));
    let data = null;
    try { data = await res.json(); } catch (_) { /* 空响应/HTML */ }
    if (!res.ok) {
      const err = (data && data.error) || `${res.status} ${res.statusText}`;
      const e = new Error(err);
      e.status = res.status;
      e.data = data;
      throw e;
    }
    return data || {};
  }

  function fmtDate(s) {
    if (!s) return '--';
    try {
      return new Date(s).toLocaleString();
    } catch (_) { return s; }
  }

  function statusBadge(status) {
    const map = {
      running: { cls: 'bg-success', text: '运行中' },
      stopped: { cls: 'bg-secondary', text: '已停止' },
      idle: { cls: 'bg-secondary', text: '未启动' },
      failed: { cls: 'bg-danger', text: '失败' },
    };
    return map[status] || { cls: 'bg-light text-dark', text: status || '--' };
  }

  function statusDot(status) {
    return `<span class="status-dot status-${status || 'idle'}" aria-hidden="true"></span>`;
  }

  // ---------------------------------------------------------------------
  // 状态
  // ---------------------------------------------------------------------
  const state = {
    services: [],
    currentId: null,
    serverAddr: null,
    portOptions: [],
    defaultFrpcPath: '/etc/frpc.toml',
    logTimer: null,
    listTimer: null,
    inflight: false,
    monTimer: null,
  };

  /** 复制纯文本到剪贴板; 不可用时 fallback 到隐藏 textarea + document.execCommand。
   *  按钮上的 <i class="bi bi-clipboard"> 会临时换成 ✓ 给用户反馈。
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
      const old = icon ? icon.className : '';
      if (icon) icon.className = ok ? 'bi bi-check2' : 'bi bi-x-lg';
      btn.classList.toggle('text-success', ok);
      btn.classList.toggle('text-danger', !ok);
      setTimeout(() => {
        if (icon) icon.className = old;
        btn.classList.remove('text-success', 'text-danger');
      }, 1200);
    }
    toast(ok ? successMsg : failMsg, ok ? 'success' : 'error');
    return ok;
  }

  // 暴露一些工具给 docker 子模块复用
  window.__svcShared = {
    jfetch, toast, escapeHtml, getFrpcPath, fmtDate, statusBadge, copyToClipboard,
  };

  // ---------------------------------------------------------------------
  // 端口下拉
  // ---------------------------------------------------------------------
  async function loadPortOptions() {
    const sel = $('#svc-port-combo');
    sel.innerHTML = '<option value="" disabled selected>加载中…</option>';
    try {
      const data = await jfetch('/api/services/port-options');
      state.portOptions = data.options || [];
      state.serverAddr = data.server_addr || state.serverAddr;
      // 显示当前生效路径
      if (data.frpc_path) $('#svc-port-source').textContent = data.frpc_path;
      if (data.default_frpc_path) state.defaultFrpcPath = data.default_frpc_path;
      if (!state.portOptions.length) {
        sel.innerHTML = '<option value="" disabled selected>未发现可用 proxy（请检查 frpc.toml）</option>';
        $('#svc-port-hint').innerHTML = '<span class="text-danger">frpc.toml 中没有 [[proxies]] 项，或文件不存在</span>';
        return;
      }
      sel.innerHTML = ['<option value="" disabled selected>请选择…</option>']
        .concat(state.portOptions.map((p) => {
          const used = p.in_use;
          const label = `${p.name}${p.type ? ' / ' + p.type : ''} · ${p.local_port} → ${p.remote_port}` + (used ? '（占用中）' : '');
          return `<option value="${p.local_port}|${p.remote_port}" ${used ? 'disabled' : ''}>${label}</option>`;
        })).join('');
      $('#svc-port-hint').textContent = `共 ${state.portOptions.length} 个 proxy。serverAddr=${state.serverAddr || '未配置'}。占用中的端口已禁用。`;
    } catch (e) {
      sel.innerHTML = `<option value="" disabled selected>加载失败：${e.message}</option>`;
      // 即使失败，后端也会带回 default_frpc_path，前端依然能展示
      if (e.data && e.data.default_frpc_path) state.defaultFrpcPath = e.data.default_frpc_path;
    }
  }

  $('#svc-refresh-ports').addEventListener('click', loadPortOptions);

  // -------- frpc 路径输入框 --------
  function syncFrpcInputFromStorage() {
    const cur = getFrpcPath();
    $('#svc-frpc-path').value = cur;
    if (cur) {
      $('#svc-frpc-hint').innerHTML =
        `当前使用：<code>${escapeHtml(cur)}</code>（仅当前浏览器记住）。点击右上角恢复默认。`;
    } else {
      const def = state.defaultFrpcPath || '/etc/frpc.toml';
      $('#svc-frpc-hint').innerHTML = `默认读取 <code>${escapeHtml(def)}</code>。可填其它绝对路径，仅当前浏览器记住。`;
    }
  }

  $('#svc-frpc-apply').addEventListener('click', async () => {
    const v = ($('#svc-frpc-path').value || '').trim();
    setFrpcPath(v);
    syncFrpcInputFromStorage();
    toast(v ? `已切换到 ${v}` : '已恢复默认路径', 'info');
    await Promise.all([loadPortOptions(), loadList()]);
  });

  $('#svc-frpc-reset').addEventListener('click', async () => {
    setFrpcPath('');
    syncFrpcInputFromStorage();
    toast('已恢复默认路径', 'info');
    await Promise.all([loadPortOptions(), loadList()]);
  });

  $('#svc-frpc-path').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      $('#svc-frpc-apply').click();
    }
  });

  // 选中 port 组合时拆出 hidden 字段
  $('#svc-port-combo').addEventListener('change', (e) => {
    const v = e.target.value;
    const form = $('#svc-create-form');
    if (!v) { form.local_port.value = ''; form.remote_port.value = ''; return; }
    const [lp, rp] = v.split('|');
    form.local_port.value = lp;
    form.remote_port.value = rp;
  });

  // ---------------------------------------------------------------------
  // 创建（上传）
  // ---------------------------------------------------------------------
  $('#svc-create-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (state.inflight) return;
    const form = e.target;
    const fd = new FormData(form);
    if (!fd.get('local_port') || !fd.get('remote_port')) {
      toast('请先选择端口组合', 'warn');
      return;
    }
    const archive = fd.get('archive');
    if (!archive || !archive.size) {
      toast('请选择 .tar.gz 文件', 'warn');
      return;
    }

    const btn = $('#svc-create-btn');
    btn.disabled = true; state.inflight = true;
    try {
      const res = await jfetch('/api/services', { method: 'POST', body: fd });
      toast('上传成功', 'success');
      form.reset();
      $('#svc-port-combo').value = '';
      const newId = res.service && res.service.id;
      await loadList();
      if (newId) selectService(newId);
    } catch (err) {
      toast(`上传失败：${err.message}`, 'error');
    } finally {
      btn.disabled = false; state.inflight = false;
    }
  });

  // ---------------------------------------------------------------------
  // 列表
  // ---------------------------------------------------------------------
  async function loadList() {
    try {
      const data = await jfetch('/api/services');
      state.services = data.services || [];
      if (state.services[0] && state.services[0].access_url) {
        // 顺手把 server_addr 同步出来用于本地 cache
        const url = state.services[0].access_url;
        const m = /^https?:\/\/([^:/]+)/.exec(url || '');
        if (m) state.serverAddr = m[1];
      }
      renderList();
      // 若当前选中已不存在，清掉
      if (state.currentId && !state.services.find((s) => s.id === state.currentId)) {
        clearDetail();
      } else if (state.currentId) {
        // 让详情从最新 list 数据中刷新
        const cur = state.services.find((s) => s.id === state.currentId);
        if (cur) renderDetail(cur);
      }
    } catch (err) {
      $('#svc-list').innerHTML = `<div class="text-center py-4 empty-pane text-danger">加载失败：${err.message}</div>`;
    }
  }

  function renderList() {
    const list = $('#svc-list');
    if (!state.services.length) {
      list.innerHTML = '<div class="text-center py-4 empty-pane">还没有服务，先在上方上传一个 tar.gz</div>';
      return;
    }
    list.innerHTML = state.services.map((s) => {
      const sb = statusBadge(s.status);
      const active = s.id === state.currentId ? 'active' : '';
      return `
        <a href="#" class="list-group-item list-group-item-action svc-card ${active}" data-svc-id="${s.id}">
          <div class="d-flex align-items-center">
            ${statusDot(s.status)}
            <div class="ms-2 flex-grow-1 text-truncate">
              <div class="fw-semibold text-truncate">${escapeHtml(s.name)}</div>
              <div class="small text-muted text-truncate">
                ${escapeHtml(s.proxy_name || '')} · ${s.local_port} → ${s.remote_port}
              </div>
            </div>
            <span class="badge ${sb.cls} ms-2">${sb.text}</span>
          </div>
        </a>`;
    }).join('');
    list.querySelectorAll('[data-svc-id]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        selectService(parseInt(el.getAttribute('data-svc-id'), 10));
      });
    });
  }

  $('#svc-refresh-list').addEventListener('click', loadList);

  // ---------------------------------------------------------------------
  // 详情 / 日志
  // ---------------------------------------------------------------------
  function selectService(id) {
    state.currentId = id;
    const s = state.services.find((x) => x.id === id);
    if (!s) { clearDetail(); return; }
    renderDetail(s);
    refreshLog(true);
    // 更新选中样式
    $$('#svc-list [data-svc-id]').forEach((el) => {
      el.classList.toggle('active', parseInt(el.getAttribute('data-svc-id'), 10) === id);
    });
  }

  function clearDetail() {
    state.currentId = null;
    stopLogPolling();
    $('#svc-detail-card').style.display = 'none';
    $('#svc-log-card').style.display = 'none';
    $('#svc-empty-card').style.display = '';
  }

  function renderDetail(s) {
    $('#svc-empty-card').style.display = 'none';
    $('#svc-detail-card').style.display = '';
    $('#svc-log-card').style.display = '';

    const sb = statusBadge(s.status);
    $('#svc-detail-name').textContent = s.name;
    const badge = $('#svc-detail-status');
    badge.className = `badge ${sb.cls}`;
    badge.textContent = sb.text;
    $('#svc-detail-archive').textContent = s.archive_filename || '--';
    $('#svc-detail-proxy').textContent = s.proxy_name || '--';
    $('#svc-detail-local').textContent = s.local_port;
    $('#svc-detail-remote').textContent = s.remote_port;
    $('#svc-detail-pid').textContent =
      `${s.pid || '--'}` + (typeof s.last_exit_code === 'number' ? ` / exit=${s.last_exit_code}` : '');
    $('#svc-detail-times').textContent =
      `启动：${fmtDate(s.last_started_at)} · 停止：${fmtDate(s.last_stopped_at)}`;

    const urlEl = $('#svc-detail-url');
    if (s.access_url) {
      urlEl.textContent = s.access_url;
      urlEl.href = s.access_url;
    } else {
      urlEl.textContent = '（serverAddr 未配置）';
      urlEl.removeAttribute('href');
    }

    const errRow = $('#svc-detail-error-row');
    if (s.last_error) {
      errRow.style.display = '';
      $('#svc-detail-error').textContent = s.last_error;
    } else {
      errRow.style.display = 'none';
    }

    // running/stopped 时按钮可用性微调
    $('#svc-start-btn').disabled = s.status === 'running';
    $('#svc-stop-btn').disabled = s.status !== 'running';
  }

  // ---------------------------------------------------------------------
  // 日志
  // ---------------------------------------------------------------------
  async function refreshLog(restartTimer) {
    const id = state.currentId;
    if (!id) return;
    try {
      const data = await jfetch(`/api/services/${id}/log?lines=200`);
      $('#svc-log-pane').textContent = data.log && data.log.length ? data.log : '（暂无日志）';
      // 顺便用 status 字段刷新左侧 / 详情，避免日志接口拿到的状态比 list 更新
      const cur = state.services.find((x) => x.id === id);
      if (cur && data.status && cur.status !== data.status) {
        cur.status = data.status;
        cur.pid = data.pid || cur.pid;
        renderList();
        renderDetail(cur);
      }
    } catch (err) {
      $('#svc-log-pane').textContent = `日志读取失败：${err.message}`;
    }
    if (restartTimer) {
      stopLogPolling();
      if ($('#svc-log-autorefresh').checked) {
        state.logTimer = setInterval(() => refreshLog(false), 2500);
      }
    }
  }

  function stopLogPolling() {
    if (state.logTimer) { clearInterval(state.logTimer); state.logTimer = null; }
  }

  $('#svc-log-refresh').addEventListener('click', () => refreshLog(true));
  $('#svc-log-autorefresh').addEventListener('change', () => refreshLog(true));
  $('#svc-log-copy').addEventListener('click', (e) => {
    const text = $('#svc-log-pane').textContent || '';
    copyToClipboard(text, { btn: e.currentTarget, successMsg: '日志已复制' });
  });

  // 页面隐藏 / 切到后台 → 暂停轮询，节省流量
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopLogPolling();
    else if (state.currentId && $('#svc-log-autorefresh').checked) refreshLog(true);
  });

  // ---------------------------------------------------------------------
  // 启停 / 删除
  // ---------------------------------------------------------------------
  async function actionOn(id, path, method) {
    if (!id) return;
    const buttons = ['#svc-start-btn', '#svc-stop-btn', '#svc-delete-btn'].map($);
    buttons.forEach((b) => (b.disabled = true));
    try {
      const data = await jfetch(`/api/services/${id}${path}`, { method });
      if (data && data.message) toast(data.message, 'info');
      else toast('操作成功', 'success');
      // 刷新列表 + 详情 + 日志
      await loadList();
      // 选中没换的话 selectService 不会重新轮询；强制刷一次
      if (state.currentId === id) refreshLog(true);
      return data;
    } catch (err) {
      toast(`操作失败：${err.message}`, 'error');
      // 失败也刷新一次状态，避免按钮卡住
      try { await loadList(); } catch (_) { /* ignore */ }
    } finally {
      // 状态由 renderDetail 决定按钮可用性
      const cur = state.services.find((s) => s.id === state.currentId);
      if (cur) renderDetail(cur);
      else buttons.forEach((b) => (b.disabled = false));
    }
  }

  $('#svc-start-btn').addEventListener('click', () => actionOn(state.currentId, '/start', 'POST'));
  $('#svc-stop-btn').addEventListener('click', () => actionOn(state.currentId, '/stop', 'POST'));
  $('#svc-delete-btn').addEventListener('click', () => {
    const cur = state.services.find((s) => s.id === state.currentId);
    if (!cur) return;
    if (!confirm(`确定删除「${cur.name}」？\n该操作会停止进程并清理上传的归档与解压目录。`)) return;
    actionOn(state.currentId, '', 'DELETE').then(() => clearDetail());
  });

  // ---------------------------------------------------------------------
  // 更新归档（增量同步）
  // ---------------------------------------------------------------------
  let _updateModal = null;
  function getUpdateModal() {
    if (!_updateModal) {
      _updateModal = new bootstrap.Modal(document.getElementById('svc-update-modal'));
    }
    return _updateModal;
  }

  $('#svc-update-btn').addEventListener('click', () => {
    if (!state.currentId) return;
    $('#svc-update-form').reset();
    $('#svc-update-result').classList.add('d-none');
    $('#svc-update-result').textContent = '';
    getUpdateModal().show();
  });

  $('#svc-update-submit').addEventListener('click', async () => {
    const id = state.currentId;
    if (!id) return;
    const form = $('#svc-update-form');
    const fd = new FormData(form);
    const f = fd.get('archive');
    if (!f || !f.size) {
      toast('请选择 .tar.gz 文件', 'warn');
      return;
    }
    // FormData 的 checkbox 未勾选时不会出现该字段，做显式归一
    if (!fd.has('replace_archive')) fd.append('replace_archive', '0');
    if (!fd.has('stop_first')) fd.append('stop_first', '0');

    const btn = $('#svc-update-submit');
    const resultBox = $('#svc-update-result');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>同步中…';
    resultBox.classList.remove('d-none');
    resultBox.classList.remove('alert-success', 'alert-danger');
    resultBox.classList.add('alert-light');
    resultBox.textContent = '正在解压并比对差异…';

    try {
      const data = await jfetch(`/api/services/${id}/update`, { method: 'POST', body: fd });
      const s = data.summary || {};
      const lines = [
        `已更新：新增 ${s.total_added} · 替换 ${s.total_replaced} · 未变 ${s.unchanged} · 跳过 ${s.total_skipped}`,
      ];
      if (s.archive_replaced) lines.push('基础归档已替换为新版本。');
      if (s.added && s.added.length) {
        lines.push(`新增文件：\n  ${s.added.slice(0, 20).join('\n  ')}` + (s.added.length > 20 ? `\n  …等 ${s.added.length} 个` : ''));
      }
      if (s.replaced && s.replaced.length) {
        lines.push(`替换文件：\n  ${s.replaced.slice(0, 20).join('\n  ')}` + (s.replaced.length > 20 ? `\n  …等 ${s.replaced.length} 个` : ''));
      }
      if (s.skipped && s.skipped.length) {
        lines.push(`跳过项：\n  ` + s.skipped.slice(0, 10).map((x) => `${x.path} (${x.reason})`).join('\n  '));
      }
      resultBox.classList.remove('alert-light');
      resultBox.classList.add('alert-success');
      resultBox.textContent = lines.join('\n');
      resultBox.style.whiteSpace = 'pre-wrap';
      toast(`已同步：+${s.total_added} ~${s.total_replaced} =${s.unchanged}`, 'success');
      await loadList();
    } catch (err) {
      resultBox.classList.remove('alert-light');
      resultBox.classList.add('alert-danger');
      resultBox.textContent = `失败：${err.message}`;
      toast(`同步失败：${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>开始同步';
    }
  });

  // ---------------------------------------------------------------------
  // helpers
  // ---------------------------------------------------------------------
  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }

  // ---------------------------------------------------------------------
  // 资源监控（顶部面板）
  // ---------------------------------------------------------------------
  function fmtBytes(n) {
    if (!n && n !== 0) return '--';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
  }

  function setBarColor(el, percent) {
    el.classList.remove('bg-primary', 'bg-success', 'bg-warning', 'bg-danger');
    if (percent >= 90) el.classList.add('bg-danger');
    else if (percent >= 75) el.classList.add('bg-warning');
    else if (percent >= 40) el.classList.add('bg-primary');
    else el.classList.add('bg-success');
  }

  async function loadMonitor() {
    const ids = state.services.map((s) => s.id).join(',');
    const url = '/api/services/system-stats' + (ids ? `?ids=${ids}` : '?include_procs=0');
    let data;
    try {
      data = await jfetch(url);
    } catch (e) {
      $('#mon-updated').textContent = '加载失败：' + e.message;
      return;
    }
    const sys = data.system || {};
    $('#mon-updated').textContent = sys.ts ? `更新于 ${new Date(sys.ts * 1000).toLocaleTimeString()}` : '';

    // CPU
    const cpu = sys.cpu || {};
    if (cpu.ok) {
      const pct = Math.round(cpu.percent || 0);
      $('#mon-cpu-percent').textContent = pct + '%';
      $('#mon-cpu-cores').textContent =
        `${cpu.count_logical}核 (${cpu.count_physical || '?'} 物理)`;
      const bar = $('#mon-cpu-bar');
      bar.style.width = pct + '%';
      setBarColor(bar, pct);
    } else {
      $('#mon-cpu-percent').textContent = '--';
      $('#mon-cpu-cores').textContent = cpu.reason || '不可用';
    }
    const la = sys.load_avg || {};
    $('#mon-load').textContent = la.ok
      ? `load: ${la['1m'].toFixed(2)} / ${la['5m'].toFixed(2)} / ${la['15m'].toFixed(2)}`
      : 'load: --';

    // 内存
    const mem = sys.memory || {};
    if (mem.ok) {
      $('#mon-mem-percent').textContent = `${mem.percent}%`;
      $('#mon-mem-text').textContent = `${fmtBytes(mem.used)} / ${fmtBytes(mem.total)}`;
      const bar = $('#mon-mem-bar');
      bar.style.width = (mem.percent || 0) + '%';
      setBarColor(bar, mem.percent || 0);
      $('#mon-swap').textContent =
        `swap: ${fmtBytes(mem.swap_used)} / ${fmtBytes(mem.swap_total)} (${mem.swap_percent}%)`;
    }

    // 磁盘
    const disk = sys.disk || {};
    if (disk.ok && disk.items) {
      $('#mon-disks').innerHTML = disk.items.map((d) => {
        if (d.error) return `<div>${escapeHtml(d.path)}: <span class="text-danger">${escapeHtml(d.error)}</span></div>`;
        return `<div class="d-flex align-items-center" title="${escapeHtml(d.path)}">
          <span class="text-truncate flex-grow-1 me-2" style="max-width:160px;">${escapeHtml(d.path)}</span>
          <span class="text-muted me-2">${fmtBytes(d.used)}/${fmtBytes(d.total)}</span>
          <span class="badge bg-light text-dark">${d.percent}%</span>
        </div>`;
      }).join('');
    } else {
      $('#mon-disks').textContent = '不可用';
    }

    // GPU
    const gpus = sys.gpus || {};
    const items = gpus.items || [];
    if (items.length) {
      $('#mon-gpus').innerHTML = items.map((g) => {
        const memPct = g.mem_total_mib > 0 ? Math.round(g.mem_used_mib / g.mem_total_mib * 100) : 0;
        return `<div class="text-truncate">
          <span class="me-2">#${g.index}</span>
          <span class="me-2">${escapeHtml(g.name)}</span>
        </div>
        <div class="d-flex align-items-center small text-muted">
          <span class="me-2">util ${g.util_percent}%</span>
          <span class="me-2">mem ${g.mem_used_mib.toFixed(0)}/${g.mem_total_mib.toFixed(0)} MiB (${memPct}%)</span>
          ${g.temp_c != null ? `<span>${g.temp_c}°C</span>` : ''}
        </div>`;
      }).join('');
    } else {
      $('#mon-gpus').textContent = gpus.reason || '无 GPU';
    }

    // 进程指标：合并到 service 列表展示
    if (Array.isArray(data.processes) && data.processes.length) {
      const map = new Map(data.processes.map((p) => [p.service_id, p.ps]));
      let needRender = false;
      state.services.forEach((s) => {
        const ps = map.get(s.id);
        if (ps) { s.__ps = ps; needRender = true; }
      });
      if (needRender) renderList();
      if (state.currentId) {
        const cur = state.services.find((x) => x.id === state.currentId);
        if (cur && cur.__ps) renderDetailProcStats(cur.__ps);
      }
    }
  }

  function renderDetailProcStats(ps) {
    // 详情卡片的"PID / 上次退出码"那行追加 cpu/mem
    const el = $('#svc-detail-pid');
    if (!ps || !ps.alive) return;
    const cpu = ps.cpu_percent != null ? `${ps.cpu_percent.toFixed(1)}%` : '--';
    const rss = fmtBytes(ps.rss);
    el.innerHTML = `${ps.pid} · CPU ${cpu} · RSS ${rss}` +
      (ps.num_threads ? ` · ${ps.num_threads} 线程` : '');
  }

  function startMonitor() {
    stopMonitor();
    loadMonitor();
    if ($('#mon-auto').checked) {
      state.monTimer = setInterval(() => {
        if (document.hidden) return;
        loadMonitor();
      }, 2500);
    }
  }
  function stopMonitor() {
    if (state.monTimer) { clearInterval(state.monTimer); state.monTimer = null; }
  }

  $('#mon-refresh').addEventListener('click', () => loadMonitor());
  $('#mon-auto').addEventListener('change', startMonitor);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopMonitor();
    else startMonitor();
  });

  // ---------------------------------------------------------------------
  // init
  // ---------------------------------------------------------------------
  async function init() {
    syncFrpcInputFromStorage();
    await Promise.all([loadPortOptions(), loadList()]);
    syncFrpcInputFromStorage();
    startMonitor();
    state.listTimer = setInterval(() => {
      if (document.hidden) return;
      loadList();
    }, 8000);
  }

  init();
})();
