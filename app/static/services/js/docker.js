/* Docker 服务管理 — 与 /api/docker-services/* 对接。
 *
 * 复用 main.js 暴露在 window.__svcShared 上的工具函数（jfetch / toast / escapeHtml
 * / getFrpcPath / fmtDate / statusBadge），避免重复实现。
 */
(function () {
  'use strict';

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(async function init() {
    const shared = window.__svcShared;
    if (!shared) {
      console.error('docker.js: __svcShared 未就绪，加载顺序异常');
      return;
    }
    const { jfetch, toast, escapeHtml, fmtDate, statusBadge, copyToClipboard } = shared;
    const $ = (s) => document.querySelector(s);
    const $$ = (s) => Array.from(document.querySelectorAll(s));

    const state = {
      list: [],
      currentId: null,
      portOptions: [],
      logTimer: null,
      listTimer: null,
      av: null,         // availability cache
      activated: false, // 用户首次切到 docker tab 后才开始周期任务
    };

    // -----------------------------------------------------------------
    // availability
    // -----------------------------------------------------------------
    async function checkAvailability() {
      try {
        state.av = await jfetch('/api/docker-services/availability');
      } catch (e) {
        state.av = { ok: false, reason: e.message };
      }
      const banner = $('#dk-availability');
      banner.classList.remove('alert-warning', 'alert-danger', 'alert-info', 'd-none');
      if (state.av.ok) {
        banner.classList.add('d-none');
      } else {
        // 根据「具体哪一环坏了」给出精确的修复建议块；保持 HTML 结构便于复制命令
        const blocks = [];
        const av = state.av;
        // 标题行
        let title = 'Docker 不可用';
        let cls = 'alert-warning';
        if (!av.docker_in_path) {
          title = 'Docker 未安装';
          cls = 'alert-danger';
        } else if (!av.daemon_ok) {
          title = 'Docker daemon 未启动 / 无权访问';
        } else if (!av.compose_ok) {
          title = 'Docker Compose 不可用';
        }
        blocks.push(
          `<div class="d-flex align-items-start mb-2">
             <i class="bi bi-exclamation-triangle me-2 mt-1"></i>
             <div>
               <div class="fw-semibold">${title}</div>
               <div class="small text-muted">${escapeHtml(av.reason || '未知原因')}</div>
             </div>
             <button class="btn btn-sm btn-outline-secondary ms-auto" id="dk-recheck-btn">
               <i class="bi bi-arrow-clockwise me-1"></i>重新检测
             </button>
           </div>`
        );

        // 修复建议块（cmd 框内可复制）
        const hintBlock = (label, text) => {
          if (!text) return '';
          return `<div class="mt-2">
            <div class="small fw-semibold mb-1">${label}</div>
            <pre class="bg-light border rounded p-2 mb-0 small" style="white-space:pre-wrap;">${escapeHtml(text)}</pre>
          </div>`;
        };

        if (av.install_hint) blocks.push(hintBlock('🔧 安装命令', av.install_hint));
        if (av.start_hint) blocks.push(hintBlock('▶ 启动 / 权限', av.start_hint));
        if (av.docker_in_path && !av.daemon_ok && av.daemon_reason) {
          blocks.push(hintBlock('原始错误', av.daemon_reason));
        }
        if (av.docker_in_path && av.daemon_ok && !av.compose_ok && av.compose_reason) {
          blocks.push(hintBlock('原始错误', av.compose_reason));
        }
        blocks.push(`<div class="small text-muted mt-2">
          配置好后点上方「重新检测」即可。原生服务（tar.gz + run.sh）不受影响。
        </div>`);

        banner.classList.add(cls);
        banner.innerHTML = blocks.join('');
        // 绑定重新检测
        const recheck = banner.querySelector('#dk-recheck-btn');
        if (recheck) recheck.addEventListener('click', async () => {
          recheck.disabled = true;
          recheck.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>检测中…';
          await checkAvailability();
          // 如果变成 ok，把上传按钮启用 + 同步刷新一下 list
          if (state.av && state.av.ok) {
            await Promise.all([loadPortOptions(), loadList()]);
            toast('Docker 现在可用了', 'success');
          }
        });
      }
      $('#dk-create-btn').disabled = !state.av.ok;
    }

    // -----------------------------------------------------------------
    // port options（与原生服务共享同一份）
    // -----------------------------------------------------------------
    async function loadPortOptions() {
      const sel = $('#dk-port-combo');
      sel.innerHTML = '<option value="" disabled selected>加载中…</option>';
      try {
        const data = await jfetch('/api/services/port-options');
        state.portOptions = data.options || [];
        if (data.frpc_path) $('#dk-port-source').textContent = data.frpc_path;
        if (!state.portOptions.length) {
          sel.innerHTML = '<option value="" disabled selected>未发现可用 proxy（请检查 frpc.toml）</option>';
          return;
        }
        sel.innerHTML = ['<option value="" disabled selected>请选择…</option>']
          .concat(state.portOptions.map((p) => {
            const used = p.in_use;
            const label = `${p.name}${p.type ? ' / ' + p.type : ''} · ${p.local_port} → ${p.remote_port}` + (used ? '（占用中）' : '');
            return `<option value="${p.local_port}|${p.remote_port}" ${used ? 'disabled' : ''}>${label}</option>`;
          })).join('');
      } catch (e) {
        sel.innerHTML = `<option value="" disabled selected>加载失败：${e.message}</option>`;
      }
    }

    $('#dk-refresh-ports').addEventListener('click', loadPortOptions);
    $('#dk-port-combo').addEventListener('change', (e) => {
      const v = e.target.value;
      const form = $('#dk-create-form');
      if (!v) { form.local_port.value = ''; form.remote_port.value = ''; return; }
      const [lp, rp] = v.split('|');
      form.local_port.value = lp;
      form.remote_port.value = rp;
    });

    // -----------------------------------------------------------------
    // create
    // -----------------------------------------------------------------
    $('#dk-create-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = e.target;
      const fd = new FormData(form);
      if (!fd.get('local_port') || !fd.get('remote_port')) {
        toast('请先选择端口组合', 'warn');
        return;
      }
      if (!fd.get('compose') || !fd.get('compose').size) {
        toast('请选择 compose 文件', 'warn');
        return;
      }
      const btn = $('#dk-create-btn');
      btn.disabled = true;
      try {
        const res = await jfetch('/api/docker-services', { method: 'POST', body: fd });
        toast('上传成功', 'success');
        form.reset();
        $('#dk-port-combo').value = '';
        const newId = res.service && res.service.id;
        await loadList();
        if (newId) selectService(newId);
      } catch (err) {
        toast(`上传失败：${err.message}`, 'error');
      } finally {
        btn.disabled = !state.av || !state.av.ok ? true : false;
      }
    });

    // -----------------------------------------------------------------
    // list / detail
    // -----------------------------------------------------------------
    async function loadList() {
      try {
        const data = await jfetch('/api/docker-services');
        state.list = data.services || [];
        renderList();
        if (state.currentId && !state.list.find((s) => s.id === state.currentId)) {
          clearDetail();
        } else if (state.currentId) {
          const cur = state.list.find((s) => s.id === state.currentId);
          if (cur) renderDetail(cur);
        }
      } catch (err) {
        $('#dk-list').innerHTML = `<div class="text-center py-4 empty-pane text-danger">加载失败：${err.message}</div>`;
      }
    }

    function renderList() {
      const list = $('#dk-list');
      if (!state.list.length) {
        list.innerHTML = '<div class="text-center py-4 empty-pane">还没有 Docker 服务</div>';
        return;
      }
      list.innerHTML = state.list.map((s) => {
        const sb = statusBadge(s.status);
        const active = s.id === state.currentId ? 'active' : '';
        const dot = `<span class="status-dot status-${s.status || 'idle'}"></span>`;
        return `<a href="#" class="list-group-item list-group-item-action svc-card ${active}" data-dk-id="${s.id}">
          <div class="d-flex align-items-center">
            ${dot}
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
      list.querySelectorAll('[data-dk-id]').forEach((el) => {
        el.addEventListener('click', (e) => {
          e.preventDefault();
          selectService(parseInt(el.getAttribute('data-dk-id'), 10));
        });
      });
    }

    $('#dk-refresh-list').addEventListener('click', loadList);

    async function selectService(id) {
      state.currentId = id;
      const s = state.list.find((x) => x.id === id);
      if (!s) { clearDetail(); return; }
      renderDetail(s);
      $$('#dk-list [data-dk-id]').forEach((el) => {
        el.classList.toggle('active', parseInt(el.getAttribute('data-dk-id'), 10) === id);
      });
      // 拉一次详情拿 containers_detail
      try {
        const d = await jfetch(`/api/docker-services/${id}`);
        if (d.service) renderDetail(d.service);
      } catch (_) { /* ignore */ }
      refreshLog(true);
    }

    function clearDetail() {
      state.currentId = null;
      stopLogPolling();
      $('#dk-detail-card').style.display = 'none';
      $('#dk-log-card').style.display = 'none';
      $('#dk-empty-card').style.display = '';
    }

    function renderDetail(s) {
      $('#dk-empty-card').style.display = 'none';
      $('#dk-detail-card').style.display = '';
      $('#dk-log-card').style.display = '';

      const sb = statusBadge(s.status);
      $('#dk-detail-name').textContent = s.name;
      const badge = $('#dk-detail-status');
      badge.className = `badge ${sb.cls}`;
      badge.textContent = sb.text;
      $('#dk-detail-compose').textContent = s.upload_filename || '--';
      $('#dk-detail-proxy').textContent = s.proxy_name || '--';
      $('#dk-detail-local').textContent = s.local_port;
      $('#dk-detail-remote').textContent = s.remote_port;
      $('#dk-detail-times').textContent =
        `启动：${fmtDate(s.last_started_at)} · 停止：${fmtDate(s.last_stopped_at)}`;

      const urlEl = $('#dk-detail-url');
      if (s.access_url) { urlEl.textContent = s.access_url; urlEl.href = s.access_url; }
      else { urlEl.textContent = '（serverAddr 未配置）'; urlEl.removeAttribute('href'); }

      const cd = s.containers_detail || [];
      if (cd.length) {
        $('#dk-detail-containers').innerHTML = cd.map((c) => {
          const stCls = (c.state || '').toLowerCase() === 'running' ? 'bg-success' : 'bg-secondary';
          return `<div class="text-truncate">
            <span class="badge ${stCls} me-2">${escapeHtml(c.state || '?')}</span>
            <code class="me-2">${escapeHtml((c.id || '').slice(0, 12))}</code>
            <span class="me-2">${escapeHtml(c.name || c.service)}</span>
            <span class="text-muted small">${escapeHtml(c.image || '')}</span>
          </div>`;
        }).join('');
      } else if (s.containers && s.containers.length) {
        $('#dk-detail-containers').innerHTML = s.containers.map((id) =>
          `<code>${escapeHtml(id.slice(0, 12))}</code>`).join(' ');
      } else {
        $('#dk-detail-containers').textContent = '（未启动 / 无容器）';
      }

      const errRow = $('#dk-detail-error-row');
      if (s.last_error) {
        errRow.style.display = '';
        $('#dk-detail-error').textContent = s.last_error;
      } else {
        errRow.style.display = 'none';
      }

      $('#dk-start-btn').disabled = s.status === 'running' || s.status === 'pulling';
      $('#dk-stop-btn').disabled = s.status !== 'running';
      $('#dk-pull-btn').disabled = s.status === 'pulling' || s.status === 'running';
    }

    // -----------------------------------------------------------------
    // 日志
    // -----------------------------------------------------------------
    async function refreshLog(restartTimer) {
      const id = state.currentId;
      if (!id) return;
      try {
        const data = await jfetch(`/api/docker-services/${id}/log?lines=200`);
        let txt = data.log || '';
        if (data.cmd_log) txt += '\n\n--- 操作日志 ---\n' + data.cmd_log;
        $('#dk-log-pane').textContent = txt || '（暂无日志）';
        const cur = state.list.find((x) => x.id === id);
        if (cur && data.status && cur.status !== data.status) {
          cur.status = data.status;
          renderList(); renderDetail(cur);
        }
      } catch (err) {
        $('#dk-log-pane').textContent = `日志读取失败：${err.message}`;
      }
      if (restartTimer) {
        stopLogPolling();
        if ($('#dk-log-autorefresh').checked) {
          state.logTimer = setInterval(() => refreshLog(false), 3000);
        }
      }
    }
    function stopLogPolling() {
      if (state.logTimer) { clearInterval(state.logTimer); state.logTimer = null; }
    }
    $('#dk-log-refresh').addEventListener('click', () => refreshLog(true));
    $('#dk-log-autorefresh').addEventListener('change', () => refreshLog(true));
    $('#dk-log-copy').addEventListener('click', (e) => {
      const text = $('#dk-log-pane').textContent || '';
      copyToClipboard(text, { btn: e.currentTarget, successMsg: '日志已复制' });
    });

    // -----------------------------------------------------------------
    // 启停 / 卸载 / 拉镜像
    // -----------------------------------------------------------------
    async function actionOn(id, path, method, opts = {}) {
      if (!id) return;
      const buttons = ['#dk-start-btn', '#dk-stop-btn', '#dk-delete-btn', '#dk-pull-btn'].map($);
      buttons.forEach((b) => (b.disabled = true));
      if (opts.preMsg) toast(opts.preMsg, 'info');
      try {
        const data = await jfetch(`/api/docker-services/${id}${path}`, { method });
        toast(opts.okMsg || '操作成功', 'success');
        await loadList();
        if (state.currentId === id) {
          // 拉一次详情更新 containers_detail
          try {
            const d = await jfetch(`/api/docker-services/${id}`);
            if (d.service) renderDetail(d.service);
          } catch (_) { /* ignore */ }
          refreshLog(true);
        }
        return data;
      } catch (err) {
        toast(`${opts.errMsg || '操作失败'}：${err.message}`, 'error');
        try { await loadList(); } catch (_) {}
      } finally {
        const cur = state.list.find((s) => s.id === state.currentId);
        if (cur) renderDetail(cur);
        else buttons.forEach((b) => (b.disabled = false));
      }
    }

    $('#dk-pull-btn').addEventListener('click', () =>
      actionOn(state.currentId, '/pull', 'POST', {
        preMsg: '正在拉取镜像，可能需要一段时间…',
        okMsg: '镜像拉取完成',
        errMsg: '拉取失败',
      }));
    $('#dk-start-btn').addEventListener('click', () =>
      actionOn(state.currentId, '/start', 'POST', { okMsg: '已启动', errMsg: '启动失败' }));
    $('#dk-stop-btn').addEventListener('click', () =>
      actionOn(state.currentId, '/stop', 'POST', { okMsg: '已停止', errMsg: '停止失败' }));
    $('#dk-delete-btn').addEventListener('click', () => {
      const cur = state.list.find((s) => s.id === state.currentId);
      if (!cur) return;
      if (!confirm(`卸载「${cur.name}」？\n将执行 docker compose down -v 并清理目录和数据卷。`)) return;
      actionOn(state.currentId, '', 'DELETE', { okMsg: '已卸载', errMsg: '卸载失败' })
        .then(() => clearDetail());
    });

    // -----------------------------------------------------------------
    // tab 激活时才启动周期任务
    // -----------------------------------------------------------------
    const tabBtn = $('#svc-tab-docker-btn');
    tabBtn.addEventListener('shown.bs.tab', async () => {
      if (!state.activated) {
        state.activated = true;
        await checkAvailability();
        await Promise.all([loadPortOptions(), loadList()]);
      } else {
        loadList();
      }
      if (!state.listTimer) {
        state.listTimer = setInterval(() => {
          if (document.hidden) return;
          loadList();
        }, 8000);
      }
    });
    tabBtn.addEventListener('hidden.bs.tab', () => {
      stopLogPolling();
      if (state.listTimer) { clearInterval(state.listTimer); state.listTimer = null; }
    });

    // 切到原生 tab 时（页面初始状态）不主动加载 docker；用户点过来时再加载
  });
})();
