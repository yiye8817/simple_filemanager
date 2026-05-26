/* 通用卡片折叠工具 (跨页面共享)。
 *
 * 用法:
 *   1) 在需要支持折叠的 ``<div class="card">`` 上加 ``data-collapsible="1"`` 和
 *      ``data-collapse-key="<unique-key>"``; 可选 ``data-default-collapsed="1"`` 表示
 *      首次访问默认折叠 (没有 localStorage 时生效).
 *   2) 页面引入这个脚本, DOMContentLoaded 时自动给所有命中卡片注入折叠按钮 +
 *      读写 localStorage('fm.cardCollapsed.v1') 持久化状态.
 *
 * CSS 依赖 (任一页面都需要):
 *   .card-collapse-toggle { background:none;border:0;color:#6c757d;padding:0 4px;cursor:pointer; }
 *   .card.is-collapsed > .card-body,
 *   .card.is-collapsed > .card-body-wrap,
 *   .card.is-collapsed > .list-group,
 *   .card.is-collapsed > .table-responsive { display:none !important; }
 */
(function () {
  'use strict';

  const LS_KEY = 'fm.cardCollapsed.v1';

  function loadMap() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}') || {}; }
    catch (_) { return {}; }
  }

  function saveMap(m) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(m)); } catch (_) { /* ignore */ }
  }

  function setCollapsed(card, collapsed) {
    card.classList.toggle('is-collapsed', !!collapsed);
    const icon = card.querySelector(':scope > .card-header .card-collapse-toggle i');
    if (icon) icon.className = collapsed ? 'bi bi-chevron-down' : 'bi bi-chevron-up';
  }

  function attach(card, map) {
    if (card.__fmCollapseBound) return;
    const header = card.querySelector(':scope > .card-header');
    if (!header) return;
    const key = card.getAttribute('data-collapse-key') || '';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'card-collapse-toggle ms-2';
    btn.title = '折叠 / 展开';
    btn.innerHTML = '<i class="bi bi-chevron-up"></i>';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const next = !card.classList.contains('is-collapsed');
      setCollapsed(card, next);
      if (key) {
        const m = loadMap();
        m[key] = next;
        saveMap(m);
      }
    });
    header.appendChild(btn);
    let initial = false;
    if (key && Object.prototype.hasOwnProperty.call(map, key)) {
      initial = !!map[key];
    } else if (card.getAttribute('data-default-collapsed') === '1') {
      initial = true;
    }
    setCollapsed(card, initial);
    card.__fmCollapseBound = true;
  }

  function injectStyleOnce() {
    if (document.getElementById('fm-collapse-style')) return;
    const s = document.createElement('style');
    s.id = 'fm-collapse-style';
    s.textContent = `
      .card[data-collapsible] .card-header { user-select: none; }
      .card-collapse-toggle {
        background: none; border: 0; color: #6c757d; padding: 0 4px;
        cursor: pointer; line-height: 1; font-size: .9rem;
      }
      .card-collapse-toggle:hover { color: #0d6efd; }
      .card.is-collapsed > .card-body,
      .card.is-collapsed > .card-body-wrap,
      .card.is-collapsed > .list-group,
      .card.is-collapsed > .table-responsive { display: none !important; }
    `;
    document.head.appendChild(s);
  }

  function init() {
    injectStyleOnce();
    const map = loadMap();
    document.querySelectorAll('.card[data-collapsible]').forEach((c) => attach(c, map));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 暴露给页面 JS 后续动态插入卡片时再次调用
  window.__fmCollapse = { init, setCollapsed };
})();
