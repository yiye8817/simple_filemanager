// 全局变量
let currentPath = '';
let selectedFiles = [];
let currentViewMode = 'grid'; // 'grid' 或 'list'
/** 列表视图排序：name | size | modified */
let fileListSortKey = 'name';
let fileListSortDir = 'asc'; // asc | desc
/** 最近一次接口返回的原始列表，用于表头排序不重拉接口 */
let lastRenderedFiles = [];

function handleFetchErrors(response) {
    if (response.status === 401) {
        return Promise.reject(new Error('未授权'));
    }
    if (!response.ok) {
        return response.json()
            .then(err => Promise.reject(new Error(err.error || err.message || response.statusText)))
            .catch(() => Promise.reject(new Error(response.statusText)));
    }
    return response;
}

/** 侧边栏「按类型浏览」对应的路径名，与后端 FILE_TYPES 键一致 */
const TYPE_VIEW_PATHS = [
    'all',
    'images', 'documents', 'videos', 'audio', 'archives', 'code', 'others',
    'applications_android', 'applications_linux', 'applications_windows'
];
const SPECIAL_NAV_PATHS = ['recent', 'search', 'shared'].concat(TYPE_VIEW_PATHS);

/** 上传、分片合并、新建文件夹时使用的物理父路径（相对根）；分类/共享/最近等视图下为根目录 */
function getUploadPath() {
    const p = currentPath;
    if (p === 'shared' || p === 'recent' || p === 'search' || TYPE_VIEW_PATHS.includes(p)) {
        return '';
    }
    if (!p || p === '/') {
        return '';
    }
    if (typeof p === 'string' && p.startsWith('/')) {
        return p.slice(1);
    }
    return String(p);
}

/** 后端 file_type → 资源管理「添加资源」页的分类下拉值 */
function mapFileTypeToResourceCategory(typeStr) {
    const m = {
        images: '图片',
        documents: '文档',
        videos: '视频',
        audio: '音频',
        archives: '压缩包',
        code: '文档',
        applications_android: '应用',
        applications_linux: '应用',
        applications_windows: '应用',
        others: '其他',
        linker: '链接',
        文件夹: '其他',
    };
    if (!typeStr) return '其他';
    return m[String(typeStr)] || '其他';
}

/** 优先用文件 id，避免路径中的 /、空格、# 等导致 /api/download/<path> 解析失败或与 <int:id> 歧义 */
function downloadUrlForPath(filePath) {
    if (!filePath) return '/api/files';
    const f = lastRenderedFiles.find(x => x.path === filePath);
    if (f && f.id != null) {
        return `/api/download/${f.id}`;
    }
    const enc = String(filePath).split('/').map(encodeURIComponent).join('/');
    return `/api/download/${enc}`;
}

/** 当前站点下的文件下载绝对 URL（用于资源链接字段） */
function getFileDownloadAbsoluteUrl(file) {
    if (!file) return '';
    if (file.id != null && !file.is_dir) {
        return `${window.location.origin}/api/download/${file.id}`;
    }
    if (!file.path) return '';
    return `${window.location.origin}${downloadUrlForPath(file.path)}`;
}

/**
 * 跳转到资源管理「添加资源」页，预填：标题=文件名、资源链接=下载地址、分类、关联 file_id
 */
function openAddResourceFromFile(file) {
    if (!file || file.is_dir) {
        showMessage('提示', '仅支持文件，不支持文件夹');
        return false;
    }
    const params = new URLSearchParams();
    params.set('title', file.name);
    params.set('resource_link', getFileDownloadAbsoluteUrl(file));
    params.set('category', mapFileTypeToResourceCategory(file.type));
    params.set('file_id', String(file.id));
    window.location.href = `/api/add?${params.toString()}`;
    return false;
}

/** 从当前列表缓存取文件（避免内联 onclick 传入 JSON 时双引号截断 HTML 属性） */
function getFileFromRenderCache(fileId) {
    const id = Number(fileId);
    return lastRenderedFiles.find(f => f.id === id) || null;
}

function openVersionModalById(fileId) {
    const file = getFileFromRenderCache(fileId);
    if (file && !file.is_dir) openVersionModal(file);
}

/** 网格视图的「⋮ 启动监听」入口；与右键菜单走同一个对话框。 */
function openFolderWatchById(fileId) {
    const file = getFileFromRenderCache(fileId);
    if (!file || !file.is_dir) {
        showMessage('提示', '监听仅支持文件夹');
        return;
    }
    openFolderWatchModal({ id: file.id, name: file.name, path: file.path });
}

/** 网格视图/右键菜单 → 文件夹插件管理。仅文件夹有效。 */
function openFolderPluginById(fileId) {
    const file = getFileFromRenderCache(fileId);
    if (!file || !file.is_dir) {
        showMessage('提示', '插件仅支持文件夹');
        return;
    }
    openFolderPluginModal({ id: file.id, name: file.name, path: file.path });
}

function openAddResourceFromFileById(fileId) {
    const file = getFileFromRenderCache(fileId);
    if (file) openAddResourceFromFile(file);
}

function previewFileById(fileId) {
    const file = getFileFromRenderCache(fileId);
    if (file) {
        if (file.is_dir) loadFiles(file.path);
        else previewFile(file.path);
    }
}

/** JSON 用于内联 onclick（仅适合无引号风险场景；含 JSON 时请用 *ById） */
function fileJsonForOnclick(file) {
    return JSON.stringify(file).replace(/'/g, '&apos;');
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text ?? '').replace(/[&<>"']/g, m => map[m]);
}

/**
 * 跨场景的"复制到剪贴板"工具。
 * 业务背景：`navigator.clipboard.writeText` 只在 secure context（HTTPS/localhost）下可用；
 * 内网通过 `http://192.168.x.x` 访问时 `navigator.clipboard` 为 `undefined`，直接调用会抛
 * `TypeError`/`NotAllowedError`，外层 catch 只会给出"复制失败"，用户搞不清原因。
 * 这里优先 clipboard API，失败回落到 `execCommand('copy')` + 临时 textarea，兼容老浏览器与
 * 非 secure context；都失败再返回 false，由上层弹"手动复制"。
 * @returns {Promise<boolean>}
 */
async function copyTextToClipboard(text) {
    const str = String(text ?? '');
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(str);
            return true;
        }
    } catch (_) { /* 走 fallback */ }
    try {
        const ta = document.createElement('textarea');
        ta.value = str;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-1000px';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, str.length);
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        return !!ok;
    } catch (_) {
        return false;
    }
}

/** 终极兜底：让用户在 prompt 里手动 Ctrl+C 复制。 */
function promptManualCopy(text, tip) {
    try { window.prompt(tip || '请手动复制以下内容：', text); }
    catch (_) { alert(text); }
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {

    console.log("DOMContentLoaded called to initializeBootstrapComponents");
    // 初始化Bootstrap组件
    initializeBootstrapComponents();
    
    // 加载存储使用情况
    loadStorageInfo();
    
    // 初始加载全部文件
    loadFiles('/');
     console.log("loadFiles called");
    // 绑定事件处理
    bindEventHandlers();
    getUserApiKey();
    syncDocumentTitle('/', false);
    // 初始化拖放上传
    initializeDragAndDrop();
    console.log("initializeDragAndDrop called");
    //新建文件和添加链接
    initializeNewFileAddLinker();
    const apiKeyModal = document.getElementById('apiKeyModal');
    if (apiKeyModal) {
        apiKeyModal.addEventListener('show.bs.modal', getUserApiKey);
    }
    const importModal = document.getElementById('importUrlModal');
    if (importModal) {
        importModal.addEventListener('hidden.bs.modal', () => {
            document.getElementById('import-progress-wrap').classList.add('d-none');
            const bar = document.getElementById('import-progress-bar');
            bar.className = 'progress-bar progress-bar-striped progress-bar-animated';
            bar.style.width = '0%';
            document.getElementById('import-start-btn').disabled = false;
        });
    }
});
// 获取当前文件夹ID (假设在页面上有一个隐藏字段存储当前目录ID)
function getCurrentFolderId() {
    const pathSpan = document.getElementById('current-path');
    pathText = pathSpan.textContent || pathSpan.innerText;
    pathText = pathText.replace(/^根目录\s*\/?\s*/, '');
    pathText = pathText.replace(/\s+/g,'')
    return pathText
    //return document.getElementById('currentFolderId')?.value || null;
}
// 富文本编辑器（基于 contenteditable）
let rfTurndown = null;
let rfEditorReady = false;
let rfSavedRange = null;

/** 初始化一次性事件 / 工具栏，编辑器就绪 */
function ensureRichFileEditor() {
    if (rfEditorReady) return;
    rfEditorReady = true;

    const editor = document.getElementById('rfEditor');

    if (typeof TurndownService !== 'undefined') {
        rfTurndown = new TurndownService({
            headingStyle: 'atx',
            codeBlockStyle: 'fenced',
            bulletListMarker: '-',
            emDelimiter: '*'
        });
        rfTurndown.keep(['sub', 'sup', 'ins', 'del']);
        rfTurndown.addRule('tables', {
            filter: 'table',
            replacement: (content, node) => '\n\n' + htmlTableToMarkdown(node) + '\n\n'
        });
        rfTurndown.addRule('codeBlock', {
            filter: function (n) {
                return n.nodeName === 'PRE'
                    && (n.firstChild ? n.firstChild.nodeName === 'CODE' : true);
            },
            replacement: function (_content, node) {
                const codeEl = node.querySelector('code');
                const code = (codeEl ? codeEl.textContent : node.textContent) || '';
                const lang = (node.getAttribute('data-language')
                            || (codeEl && codeEl.className.match(/language-(\S+)/) || [])[1]
                            || '').trim();
                return '\n\n```' + lang + '\n' + code.replace(/\n$/, '') + '\n```\n\n';
            }
        });
    } else {
        console.warn('Turndown 未加载，保存为 Markdown 时将退化为纯文本');
    }

    // 记录最近一次选区，便于点击工具栏后仍能在编辑区操作
    const saveRange = () => {
        const sel = window.getSelection();
        if (sel && sel.rangeCount > 0) {
            const range = sel.getRangeAt(0);
            if (editor.contains(range.commonAncestorContainer)) {
                rfSavedRange = range.cloneRange();
            }
        }
    };
    editor.addEventListener('keyup', saveRange);
    editor.addEventListener('mouseup', saveRange);
    editor.addEventListener('focus', saveRange);

    const restoreRange = () => {
        if (!rfSavedRange) {
            editor.focus();
            return;
        }
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(rfSavedRange);
    };

    // 工具栏：基础 execCommand 按钮
    document.querySelectorAll('#rfEditorToolbar [data-rf-cmd]').forEach(btn => {
        btn.addEventListener('mousedown', e => e.preventDefault());
        btn.addEventListener('click', () => {
            restoreRange();
            const cmd = btn.getAttribute('data-rf-cmd');
            try { document.execCommand(cmd, false, null); } catch (e) { console.warn(e); }
            saveRange();
        });
    });

    // 段落/标题选择：手工替换 block 标签（避免 execCommand('formatBlock') 在
    // 部分浏览器下从 H1 切回 P 不生效的问题）。
    // 注意：不要给 <select> 加 mousedown.preventDefault，否则下拉无法展开；
    // 改为在打开下拉前主动保存编辑器选区。
    const blockSel = document.querySelector('#rfEditorToolbar [data-rf-block]');
    if (blockSel) {
        const captureRangeBeforeOpen = () => saveRange();
        blockSel.addEventListener('focus', captureRangeBeforeOpen);
        blockSel.addEventListener('pointerdown', captureRangeBeforeOpen);
        blockSel.addEventListener('change', () => {
            restoreRange();
            setBlockTagAtSelection(blockSel.value);
            saveRange();
            scheduleTocRefresh();
        });
    }

    // 选区/输入变化时同步下拉框的当前 block 标签
    const syncBlockSelect = () => {
        if (!blockSel) return;
        const block = currentBlockElement();
        const tag = block ? block.tagName.toUpperCase() : 'P';
        if ([...blockSel.options].some(o => o.value === tag)) {
            blockSel.value = tag;
        } else {
            blockSel.value = 'P';
        }
    };
    editor.addEventListener('keyup', syncBlockSelect);
    editor.addEventListener('mouseup', syncBlockSelect);
    editor.addEventListener('input', () => { syncBlockSelect(); scheduleTocRefresh(); });

    // 颜色选择
    document.getElementById('rfFontColor').addEventListener('input', e => {
        restoreRange();
        document.execCommand('foreColor', false, e.target.value);
        saveRange();
    });
    document.getElementById('rfBgColor').addEventListener('input', e => {
        restoreRange();
        try { document.execCommand('hiliteColor', false, e.target.value); }
        catch (_) { document.execCommand('backColor', false, e.target.value); }
        saveRange();
    });

    // 插入链接
    document.getElementById('rfInsertLinkBtn').addEventListener('click', () => {
        const url = window.prompt('请输入链接地址：', 'https://');
        if (!url) return;
        restoreRange();
        document.execCommand('createLink', false, url);
        saveRange();
    });

    // 插入图片：本地选择 -> base64 内嵌
    document.getElementById('rfInsertImageBtn').addEventListener('click', () => {
        document.getElementById('rfInsertImageInput').click();
    });
    document.getElementById('rfInsertImageInput').addEventListener('change', ev => {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = '';
        if (!file) return;
        const reader = new FileReader();
        reader.onload = e => {
            const dataUrl = e.target.result;
            restoreRange();
            insertHtmlAtCaret('<img src="' + dataUrl + '" alt="' + escapeHtml(file.name) + '" style="max-width:100%;">');
            saveRange();
        };
        reader.readAsDataURL(file);
    });

    // 插入表格：弹出小窗选择行列
    const insertTableEl = document.getElementById('rfInsertTableModal');
    const insertTableModal = insertTableEl ? new bootstrap.Modal(insertTableEl) : null;
    document.getElementById('rfInsertTableBtn').addEventListener('click', () => {
        saveRange();
        if (insertTableModal) {
            insertTableModal.show();
        } else {
            const input = window.prompt('请输入表格大小（行x列）：', '3x3');
            if (!input) return;
            const m = input.match(/^(\d+)\s*[xX×*]\s*(\d+)$/);
            if (!m) return alert('格式应为：行x列（如 3x4）');
            const r = parseInt(m[1], 10), c = parseInt(m[2], 10);
            insertHtmlAtCaret(buildEmptyTableHtml(r, c, true) + '<p><br></p>');
        }
    });
    if (insertTableModal) {
        document.getElementById('rfInsertTableConfirm').addEventListener('click', () => {
            const rows = Math.max(1, Math.min(50, parseInt(document.getElementById('rfTableRows').value || '3', 10)));
            const cols = Math.max(1, Math.min(20, parseInt(document.getElementById('rfTableCols').value || '3', 10)));
            const withHeader = document.getElementById('rfTableHeader').checked;
            insertTableModal.hide();
            // 等 modal 关闭后再恢复编辑器选区，否则焦点会被吞
            setTimeout(() => insertHtmlAtCaret(buildEmptyTableHtml(rows, cols, withHeader) + '<p><br></p>'), 150);
        });
    }

    // 插入代码块
    document.getElementById('rfInsertCodeBtn').addEventListener('click', () => {
        restoreRange();
        const sel = window.getSelection();
        const text = sel && sel.toString() ? sel.toString() : '';
        const html = '<pre><code>' + (escapeHtml(text) || '在此输入代码…') + '</code></pre><p><br></p>';
        insertHtmlAtCaret(html);
        saveRange();
    });

    // 插入分割线
    document.getElementById('rfInsertHrBtn').addEventListener('click', () => {
        restoreRange();
        document.execCommand('insertHorizontalRule', false, null);
        saveRange();
    });

    // 切换 Markdown 预览
    document.getElementById('rfTogglePreviewBtn').addEventListener('click', () => {
        const previewEl = document.getElementById('rfPreview');
        const editorEl = document.getElementById('rfEditor');
        const toolbarEl = document.getElementById('rfEditorToolbar');
        if (previewEl.classList.contains('d-none')) {
            const md = htmlToMarkdown(editorEl.innerHTML);
            const html = (typeof marked !== 'undefined') ? marked.parse(md) : ('<pre>' + escapeHtml(md) + '</pre>');
            previewEl.innerHTML = '<div class="rf-preview-body">' + html + '</div>';
            previewEl.classList.remove('d-none');
            editorEl.classList.add('d-none');
            toolbarEl.classList.add('rf-toolbar-disabled');
        } else {
            previewEl.classList.add('d-none');
            editorEl.classList.remove('d-none');
            toolbarEl.classList.remove('rf-toolbar-disabled');
        }
    });

    // 导入文件
    document.getElementById('rfImportBtn').addEventListener('click', () => {
        document.getElementById('rfImportInput').click();
    });
    document.getElementById('rfImportInput').addEventListener('change', handleRichFileImport);

    // 粘贴时清洗 HTML，避免带入大量样式
    editor.addEventListener('paste', e => {
        const cd = e.clipboardData || window.clipboardData;
        if (!cd) return;
        const html = cd.getData('text/html');
        if (html) {
            e.preventDefault();
            insertHtmlAtCaret(sanitizeHtmlBasic(html));
            scheduleTocRefresh();
        }
    });

    // 目录：切换显示 + 刷新
    const tocBtn = document.getElementById('rfToggleTocBtn');
    const tocEl = document.getElementById('rfToc');
    if (tocBtn && tocEl) {
        tocBtn.addEventListener('click', () => {
            const showing = !tocEl.classList.contains('d-none');
            if (showing) {
                tocEl.classList.add('d-none');
                tocBtn.classList.remove('active');
            } else {
                tocEl.classList.remove('d-none');
                tocBtn.classList.add('active');
                rfBuildToc();
            }
        });
        const refreshBtn = document.getElementById('rfTocRefreshBtn');
        if (refreshBtn) refreshBtn.addEventListener('click', rfBuildToc);
    }

    // 最大化 / 还原
    const maxBtn = document.getElementById('rfToggleMaxBtn');
    const modalEl = document.getElementById('newFileModal');
    if (maxBtn && modalEl) {
        const dialog = modalEl.querySelector('.modal-dialog');
        const setMaxState = (max) => {
            if (max) {
                dialog.classList.add('modal-fullscreen');
                dialog.classList.remove('modal-xl', 'modal-dialog-scrollable');
                modalEl.classList.add('rf-modal-max');
                maxBtn.innerHTML = '<i class="bi bi-fullscreen-exit"></i>';
                maxBtn.setAttribute('title', '还原窗口');
            } else {
                dialog.classList.remove('modal-fullscreen');
                dialog.classList.add('modal-xl', 'modal-dialog-scrollable');
                modalEl.classList.remove('rf-modal-max');
                maxBtn.innerHTML = '<i class="bi bi-arrows-fullscreen"></i>';
                maxBtn.setAttribute('title', '最大化');
            }
        };
        maxBtn.addEventListener('click', () => {
            setMaxState(!dialog.classList.contains('modal-fullscreen'));
        });
        // 模态框关闭时自动还原，下次打开仍是默认大小
        modalEl.addEventListener('hidden.bs.modal', () => setMaxState(false));
    }
}

/** 判断节点是否为编辑器中可识别的 block 容器 */
function isRfBlockEl(node) {
    if (!node || node.nodeType !== 1) return false;
    return /^(P|H1|H2|H3|H4|H5|H6|BLOCKQUOTE|PRE|DIV|LI)$/i.test(node.tagName);
}

/** 返回当前选区所在的最近 block 容器（不含 editor 本身） */
function currentBlockElement() {
    const editor = document.getElementById('rfEditor');
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return null;
    let node = sel.anchorNode;
    if (!node) return null;
    if (node.nodeType === 3) node = node.parentNode;
    while (node && node !== editor && !isRfBlockEl(node)) {
        node = node.parentNode;
    }
    return (node && node !== editor) ? node : null;
}

/**
 * 把当前选区所在 block 的 tagName 替换为目标标签。
 * - 如果当前已经是该标签：什么都不做
 * - 如果当前不在 block 内（直接位于 editor 下的文本节点）：包一层
 * - 替换时保留子节点；标题元素自动赋 id 以便 TOC 跳转
 */
function setBlockTagAtSelection(tag) {
    const editor = document.getElementById('rfEditor');
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) {
        editor.focus();
        return;
    }
    let block = currentBlockElement();
    const targetTag = tag.toUpperCase();

    if (!block) {
        const range = sel.getRangeAt(0);
        const newEl = document.createElement(targetTag);
        if (range.collapsed) {
            newEl.innerHTML = '<br>';
        } else {
            newEl.appendChild(range.extractContents());
        }
        range.insertNode(newEl);
        block = newEl;
    } else if (block.tagName.toUpperCase() !== targetTag) {
        const newEl = document.createElement(targetTag);
        Array.from(block.attributes || []).forEach(a => newEl.setAttribute(a.name, a.value));
        while (block.firstChild) newEl.appendChild(block.firstChild);
        block.parentNode.replaceChild(newEl, block);
        block = newEl;
    }

    // 标题：确保有 id
    if (/^H[1-6]$/.test(targetTag) && !block.id) {
        block.id = generateHeadingId(block.textContent || '');
    }

    // 选区落到块末尾，便于继续输入
    const range = document.createRange();
    range.selectNodeContents(block);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
    rfSavedRange = range.cloneRange();
}

/** 为标题生成稳定 id（包含可读 slug + 短随机后缀，避免重复） */
function generateHeadingId(text) {
    const slug = String(text || '').trim()
        .toLowerCase()
        .replace(/\s+/g, '-')
        .replace(/[^a-z0-9\-_\u4e00-\u9fa5]/g, '')
        .slice(0, 40) || 'h';
    return 'rf-h-' + slug + '-' + Math.random().toString(36).slice(2, 6);
}

let rfTocTimer = null;
/** 节流刷新 TOC（仅 TOC 面板可见时执行） */
function scheduleTocRefresh() {
    const tocEl = document.getElementById('rfToc');
    if (!tocEl || tocEl.classList.contains('d-none')) return;
    clearTimeout(rfTocTimer);
    rfTocTimer = setTimeout(rfBuildToc, 250);
}

/** 扫描编辑器中的 H1-H6，构建嵌套目录列表 */
function rfBuildToc() {
    const editor = document.getElementById('rfEditor');
    const tocBody = document.getElementById('rfTocBody');
    if (!editor || !tocBody) return;

    const headings = Array.from(editor.querySelectorAll('h1, h2, h3, h4, h5, h6'));
    if (headings.length === 0) {
        tocBody.innerHTML = '<div class="text-muted small p-2">尚未添加任何标题。<br>选中段落后从工具栏左侧切换为"标题 1～6"。</div>';
        return;
    }

    headings.forEach(h => {
        if (!h.id) h.id = generateHeadingId(h.textContent || '');
    });

    // 构建嵌套 ul（按 level 缩进）
    const minLevel = Math.min(...headings.map(h => parseInt(h.tagName[1], 10)));
    let html = '<ul class="rf-toc-list">';
    let curLevel = minLevel;
    headings.forEach(h => {
        const level = parseInt(h.tagName[1], 10);
        while (curLevel < level) { html += '<ul class="rf-toc-list">'; curLevel++; }
        while (curLevel > level) { html += '</ul>'; curLevel--; }
        const text = (h.textContent || '').trim() || '(空标题)';
        html += '<li><a href="#' + h.id + '" data-rf-toc="' + h.id
              + '" class="rf-toc-link rf-toc-l' + level + '">'
              + escapeHtml(text) + '</a></li>';
    });
    while (curLevel > minLevel) { html += '</ul>'; curLevel--; }
    html += '</ul>';
    tocBody.innerHTML = html;

    tocBody.querySelectorAll('[data-rf-toc]').forEach(a => {
        a.addEventListener('click', e => {
            e.preventDefault();
            const target = document.getElementById(a.getAttribute('data-rf-toc'));
            if (!target) return;
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            target.classList.add('rf-toc-flash');
            setTimeout(() => target.classList.remove('rf-toc-flash'), 1200);
        });
    });
}

/** 在当前光标处插入 HTML（无选区则追加到末尾） */
function insertHtmlAtCaret(html) {
    const editor = document.getElementById('rfEditor');
    editor.focus();
    const sel = window.getSelection();
    let range;
    if (rfSavedRange && editor.contains(rfSavedRange.commonAncestorContainer)) {
        range = rfSavedRange;
        sel.removeAllRanges();
        sel.addRange(range);
    } else if (sel && sel.rangeCount > 0 && editor.contains(sel.anchorNode)) {
        range = sel.getRangeAt(0);
    } else {
        range = document.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
    }
    range.deleteContents();
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const frag = tpl.content;
    const lastNode = frag.lastChild;
    range.insertNode(frag);
    if (lastNode) {
        const newRange = document.createRange();
        newRange.setStartAfter(lastNode);
        newRange.collapse(true);
        sel.removeAllRanges();
        sel.addRange(newRange);
        rfSavedRange = newRange.cloneRange();
    }
}

/** 生成 r×c 空表格 HTML */
function buildEmptyTableHtml(rows, cols, withHeader) {
    let html = '<table class="rf-table"><tbody>';
    for (let r = 0; r < rows; r++) {
        html += '<tr>';
        for (let c = 0; c < cols; c++) {
            const tag = (withHeader && r === 0) ? 'th' : 'td';
            html += '<' + tag + '><br></' + tag + '>';
        }
        html += '</tr>';
    }
    html += '</tbody></table>';
    return html;
}

/** HTML 表格 -> Markdown 表格 */
function htmlTableToMarkdown(tableNode) {
    const rows = Array.from(tableNode.querySelectorAll('tr'));
    if (!rows.length) return '';
    const matrix = rows.map(tr => Array.from(tr.querySelectorAll('th,td'))
        .map(td => (td.textContent || '').trim().replace(/\|/g, '\\|').replace(/\n+/g, ' ')));
    const colCount = Math.max(...matrix.map(r => r.length));
    matrix.forEach(r => { while (r.length < colCount) r.push(''); });

    const hasTh = !!rows[0].querySelector('th');
    const lines = [];
    const headerRow = hasTh ? matrix[0] : Array(colCount).fill('');
    lines.push('| ' + headerRow.join(' | ') + ' |');
    lines.push('| ' + Array(colCount).fill('---').join(' | ') + ' |');
    const bodyStart = hasTh ? 1 : 0;
    for (let i = bodyStart; i < matrix.length; i++) {
        lines.push('| ' + matrix[i].join(' | ') + ' |');
    }
    return lines.join('\n');
}

/** HTML -> Markdown（无 turndown 时退化为纯文本） */
function htmlToMarkdown(html) {
    if (rfTurndown) {
        try { return rfTurndown.turndown(html || ''); } catch (e) { console.warn(e); }
    }
    const tmp = document.createElement('div');
    tmp.innerHTML = html || '';
    return tmp.innerText || '';
}

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[m]));
}

/** 二维数组（CSV / Excel sheet）-> HTML 表格 */
function aoaToHtmlTable(aoa, withHeader) {
    if (!Array.isArray(aoa) || aoa.length === 0) return '';
    const cols = Math.max(...aoa.map(r => r.length));
    let html = '<table class="rf-table"><tbody>';
    aoa.forEach((row, idx) => {
        html += '<tr>';
        for (let c = 0; c < cols; c++) {
            const cell = row[c] == null ? '' : String(row[c]);
            const tag = (withHeader && idx === 0) ? 'th' : 'td';
            html += '<' + tag + '>' + escapeHtml(cell) + '</' + tag + '>';
        }
        html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
}

/** 极简 CSV 解析（支持引号/逗号/换行） */
function parseCsv(text) {
    const out = [];
    let row = [], field = '', inQuotes = false;
    for (let i = 0; i < text.length; i++) {
        const ch = text[i];
        if (inQuotes) {
            if (ch === '"') {
                if (text[i + 1] === '"') { field += '"'; i++; } else { inQuotes = false; }
            } else {
                field += ch;
            }
        } else {
            if (ch === '"') {
                inQuotes = true;
            } else if (ch === ',') {
                row.push(field); field = '';
            } else if (ch === '\n' || ch === '\r') {
                if (ch === '\r' && text[i + 1] === '\n') i++;
                row.push(field); field = '';
                out.push(row); row = [];
            } else {
                field += ch;
            }
        }
    }
    if (field.length > 0 || row.length > 0) { row.push(field); out.push(row); }
    return out.filter(r => r.length && !(r.length === 1 && r[0] === ''));
}

function handleRichFileImport(ev) {
    const input = ev.target;
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const status = document.getElementById('rfStatus');
    status.textContent = '正在解析 ' + file.name + ' …';

    const nameInput = document.getElementById('fileName');
    if (!nameInput.value.trim()) {
        nameInput.value = file.name.replace(/\.[^.]+$/, '');
    }

    const editor = document.getElementById('rfEditor');
    const reader = new FileReader();
    reader.onload = function (e) {
        try {
            if (ext === 'md' || ext === 'markdown') {
                const md = e.target.result || '';
                const html = (typeof marked !== 'undefined') ? marked.parse(md) : ('<pre>' + escapeHtml(md) + '</pre>');
                editor.innerHTML = sanitizeHtmlBasic(html);
                document.getElementById('fileSaveFormat').value = 'md';
            } else if (ext === 'html' || ext === 'htm') {
                editor.innerHTML = sanitizeHtmlBasic(e.target.result || '');
                document.getElementById('fileSaveFormat').value = 'html';
            } else if (ext === 'txt') {
                editor.innerHTML = '<pre>' + escapeHtml(e.target.result || '') + '</pre>';
                document.getElementById('fileSaveFormat').value = 'txt';
            } else if (ext === 'csv') {
                const aoa = parseCsv(e.target.result || '');
                editor.innerHTML = aoaToHtmlTable(aoa, true);
                document.getElementById('fileSaveFormat').value = 'md';
            } else if (ext === 'xls' || ext === 'xlsx') {
                if (typeof XLSX === 'undefined') {
                    alert('未加载 SheetJS，无法解析 Excel');
                    return;
                }
                const data = new Uint8Array(e.target.result);
                const wb = XLSX.read(data, { type: 'array' });
                let combined = '';
                wb.SheetNames.forEach(name => {
                    const sheet = wb.Sheets[name];
                    const aoa = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' });
                    combined += '<h3>' + escapeHtml(name) + '</h3>' + aoaToHtmlTable(aoa, true);
                });
                editor.innerHTML = combined || '<p>（空工作簿）</p>';
                document.getElementById('fileSaveFormat').value = 'md';
            } else {
                alert('暂不支持的导入格式: .' + ext);
                return;
            }
            status.textContent = '已导入：' + file.name;
        } catch (err) {
            console.error(err);
            alert('导入失败：' + err.message);
            status.textContent = '';
        }
    };
    reader.onerror = function () {
        alert('文件读取失败');
        status.textContent = '';
    };

    if (ext === 'xls' || ext === 'xlsx') {
        reader.readAsArrayBuffer(file);
    } else {
        reader.readAsText(file, 'utf-8');
    }
}

/** 简易 HTML 清洗：去掉 script/style/iframe 与 on* 事件属性 */
function sanitizeHtmlBasic(html) {
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    tmp.querySelectorAll('script, style, link, meta, iframe, object, embed').forEach(n => n.remove());
    tmp.querySelectorAll('*').forEach(el => {
        Array.from(el.attributes).forEach(attr => {
            if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
            if (attr.name === 'href' && /^\s*javascript:/i.test(attr.value)) el.removeAttribute(attr.name);
        });
    });
    const body = tmp.querySelector('body');
    return body ? body.innerHTML : tmp.innerHTML;
}

/** 根据保存格式与文件名生成最终文件名/内容/file_type */
function buildSaveFilePayload() {
    const rawName = document.getElementById('fileName').value.trim();
    const fmt = document.getElementById('fileSaveFormat').value;
    if (!rawName) return null;

    const extMap = { md: '.md', html: '.html', txt: '.txt' };
    let finalName = rawName;
    if (!/\.[a-z0-9]{1,8}$/i.test(rawName)) {
        finalName = rawName + extMap[fmt];
    }

    const editorHtml = document.getElementById('rfEditor').innerHTML || '';
    const editorText = document.getElementById('rfEditor').innerText || '';

    let content;
    if (fmt === 'md') {
        content = htmlToMarkdown(editorHtml);
    } else if (fmt === 'html') {
        content = '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8">'
                + '<title>' + escapeHtml(finalName) + '</title></head><body>\n'
                + editorHtml
                + '\n</body></html>';
    } else {
        content = editorText;
    }

    const fileTypeByExt = (function (n) {
        const e = (n.split('.').pop() || '').toLowerCase();
        if (['md', 'txt'].includes(e)) return 'documents';
        if (['html', 'htm', 'css', 'js', 'json', 'xml'].includes(e)) return 'code';
        return 'documents';
    })(finalName);

    return { name: finalName, content, file_type: fileTypeByExt };
}

function initializeNewFileAddLinker(){
    console.log("initializeNewFileAddLinker called");
    // 初始化模态框
    const newFileModalEl = document.getElementById('newFileModal');
    const newFileModal = new bootstrap.Modal(newFileModalEl);
    const addLinkModal = new bootstrap.Modal(document.getElementById('addLinkModal'));

    // 模态框首次显示时初始化富文本编辑器（确保容器可见，光标聚焦）
    newFileModalEl.addEventListener('shown.bs.modal', () => {
        ensureRichFileEditor();
        const editor = document.getElementById('rfEditor');
        editor.focus();
    });

    // 新建文件按钮事件：重置编辑器与表单
    document.getElementById('new-file-btn').addEventListener('click', function() {
        document.getElementById('fileName').value = '';
        document.getElementById('fileSaveFormat').value = 'md';
        const status = document.getElementById('rfStatus');
        if (status) status.textContent = '';
        const previewEl = document.getElementById('rfPreview');
        const editorEl = document.getElementById('rfEditor');
        const toolbarEl = document.getElementById('rfEditorToolbar');
        if (previewEl) { previewEl.innerHTML = ''; previewEl.classList.add('d-none'); }
        if (editorEl) { editorEl.innerHTML = ''; editorEl.classList.remove('d-none'); }
        if (toolbarEl) toolbarEl.classList.remove('rf-toolbar-disabled');
        rfSavedRange = null;
        newFileModal.show();
    });

    // 保存文件按钮事件
    document.getElementById('saveFileBtn').addEventListener('click', function() {
        const payload = buildSaveFilePayload();
        if (!payload) {
            alert('请输入文件名');
            return;
        }
        const requestData = {
            name: payload.name,
            content: payload.content,
            parent_id: getCurrentFolderId(),
            file_type: payload.file_type
        };

        const btn = document.getElementById('saveFileBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>保存中…';

        fetch('/api/newfile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                newFileModal.hide();
                loadFiles('/' + (data.parent_path || ''));
            } else {
                alert('文件保存失败: ' + (data.message || '未知错误'));
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('保存文件时发生错误');
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-save me-1"></i>保存';
        });
    });
    
    // 添加链接按钮事件
    document.getElementById('add-link-btn').addEventListener('click', function() {
        document.getElementById('linkName').value = '';
        document.getElementById('linkUrl').value = '';
        
        // 获取文件分类
        fetch('/api/get_file_type')
        .then(response => response.json())
        .then(data => {
            const selectElement = document.getElementById('linkType');
            // 清空现有选项
            selectElement.innerHTML = '<option value="" selected>请选择分类</option>';
            
            // 添加从后端获取的分类
            data.types.forEach(type => {
                const option = document.createElement('option');
                option.value = type.id;
                option.textContent = type.name;
                selectElement.appendChild(option);
            });
            
            addLinkModal.show();
        })
        .catch(error => {
            console.error('Error:', error);
            alert('获取分类失败');
        });
    });
    
    // 保存链接按钮事件
    document.getElementById('saveLinkBtn').addEventListener('click', function() {
        const linkName = document.getElementById('linkName').value.trim();
        const linkUrl = document.getElementById('linkUrl').value.trim();
        const linkType = document.getElementById('linkType').value;
        
        if (!linkName) {
            alert('请输入链接名称');
            return;
        }
        
        if (!linkUrl) {
            alert('请输入链接地址');
            return;
        }
        
        if (!linkType) {
            alert('请选择分类');
            return;
        }
        
        // 创建请求数据
        const requestData = {
            name: linkName,
            path: linkUrl,
            file_type: linkType,
            is_directory: false,
            parent_id: getCurrentFolderId()
        };
        
        // 发送请求到后端
        fetch('/api/add_linker', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert('链接添加成功');
                addLinkModal.hide();
                // 刷新文件列表
                loadFiles('/');
            } else {
                alert('链接添加失败: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('添加链接时发生错误');
        });
    });
    
    // // 加载文件列表函数（示例）
    // function loadFiles() {
    //     // 这里实现刷新文件列表的逻辑
    //     console.log('刷新文件列表');
    // }
}
// 初始化Bootstrap组件
function initializeBootstrapComponents() {
    // 初始化所有tooltips
    const tooltips = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltips.forEach(tooltip => new bootstrap.Tooltip(tooltip));
}

// 绑定事件处理器
function bindEventHandlers() {
    // 绑定导航菜单项点击事件
    bindNavigationEvents();
    
    // 绑定按钮点击事件
    bindButtonEvents();
    
    // 绑定搜索框事件
    bindSearchEvents();
    
    // 绑定视图切换事件
    bindViewModeEvents();
    
    // 列表表头排序（文件名 / 大小 / 时间）
    bindFileListSortEvents();
    
    // 绑定键盘导航事件
    bindKeyboardNavigation();
    
    // 绑定点击空白区域取消选择事件
    bindClearSelectionEvent();
}

// 绑定导航事件
function bindNavigationEvents() {
    // 侧边栏分类点击事件
    document.getElementById('all-files').addEventListener('click', () => {
        setActiveNavItem('all-files');
        loadFiles('/');
    });

    document.getElementById('every-files').addEventListener('click', () => {
        setActiveNavItem('every-files');
        loadFilesByType('all');
    });

    document.getElementById('images').addEventListener('click', () => {
        setActiveNavItem('images');
        loadFilesByType('images');
    });
    
    document.getElementById('documents').addEventListener('click', () => {
        setActiveNavItem('documents');
        loadFilesByType('documents');
    });
    
    document.getElementById('videos').addEventListener('click', () => {
        setActiveNavItem('videos');
        loadFilesByType('videos');
    });
    
    document.getElementById('audio').addEventListener('click', () => {
        setActiveNavItem('audio');
        loadFilesByType('audio');
    });
    
    document.getElementById('archives').addEventListener('click', () => {
        setActiveNavItem('archives');
        loadFilesByType('archives');
    });
    
    document.getElementById('code').addEventListener('click', () => {
        setActiveNavItem('code');
        loadFilesByType('code');
    });
    
    document.getElementById('applications-android').addEventListener('click', () => {
        setActiveNavItem('applications-android');
        loadFilesByType('applications_android');
    });
    document.getElementById('applications-linux').addEventListener('click', () => {
        setActiveNavItem('applications-linux');
        loadFilesByType('applications_linux');
    });
    document.getElementById('applications-windows').addEventListener('click', () => {
        setActiveNavItem('applications-windows');
        loadFilesByType('applications_windows');
    });
    
    document.getElementById('others').addEventListener('click', () => {
        setActiveNavItem('others');
        loadFilesByType('others');
    });
    
    document.getElementById('recent').addEventListener('click', () => {
        setActiveNavItem('recent');
        loadRecentFiles();
    });
    
    document.getElementById('shared').addEventListener('click', () => {
        setActiveNavItem('shared');
        loadSharedFiles();
    });
    
    // 返回上级按钮
    document.getElementById('back-button').addEventListener('click', navigateUp);
}

// 绑定键盘导航事件
function bindKeyboardNavigation() {
    document.addEventListener('keydown', function(event) {
        // 如果有一个焦点项目
        const focusedItem = document.querySelector('.file-item.focused');
        if (!focusedItem) return;
        
        // 删除键
        if (event.key === 'Delete' && selectedFiles.length > 0) {
            event.preventDefault();
            deleteSelectedFiles();
        }
        
        // 回车键 - 打开或预览
        if (event.key === 'Enter') {
            event.preventDefault();
            const filePath = focusedItem.dataset.path;
            const isDir = focusedItem.dataset.isDir === 'true';
            
            if (isDir) {
                loadFiles(filePath);
            } else {
                previewFile(filePath);
            }
        }
        
        // 空格键 - 选择/取消选择
        if (event.key === ' ' || event.key === 'Spacebar') {
            event.preventDefault();
            focusedItem.click();
        }
    });
}

// 绑定点击空白区域取消选择
function bindClearSelectionEvent() {
    document.addEventListener('click', function(event) {
        // 如果点击的不是文件项或其子元素，也不是操作按钮；表头排序/全选不触发清空
        if (!event.target.closest('.file-item') && 
            !event.target.closest('.action-buttons') && 
            !event.target.closest('.modal') &&
            !event.target.closest('#files-list thead')) {
            clearSelection();
        }
    });
}

// 设置活动的导航项
function setActiveNavItem(id) {
    document.querySelectorAll('.sidebar .list-group-item').forEach(item => {
        item.classList.remove('active');
    });
    document.getElementById(id).classList.add('active');
}

// 绑定按钮事件
function bindButtonEvents() {
    // 上传按钮
    document.getElementById('upload-btn').addEventListener('click', () => {
        const uploadModal = new bootstrap.Modal(document.getElementById('upload-modal'));
        resetUploadModal();
        uploadModal.show();
    });
    
    // 选择文件按钮
    document.getElementById('select-files-btn').addEventListener('click', () => {
        document.getElementById('file-input').click();
    });
    
    // 文件选择变化事件
    document.getElementById('file-input').addEventListener('change', handleFileSelection);
    
    // 上传提交按钮
    document.getElementById('upload-submit').addEventListener('click', uploadSelectedFiles);
    
    // 新建文件夹按钮
    document.getElementById('new-folder-btn').addEventListener('click', () => {
        const newFolderModal = new bootstrap.Modal(document.getElementById('new-folder-modal'));
        document.getElementById('folder-name').value = '';
        newFolderModal.show();
    });
    
    // 创建文件夹提交按钮
    document.getElementById('create-folder-btn').addEventListener('click', createNewFolder);
    
    // 删除按钮
    document.getElementById('delete-btn').addEventListener('click', deleteSelectedFiles);
    
    // 下载按钮
    document.getElementById('download-btn').addEventListener('click', downloadSelectedFiles);
    
    // 预览按钮
    document.getElementById('preview-btn').addEventListener('click', previewSelectedFile);
    
    // 分享（工具栏）
    document.getElementById('share-btn').addEventListener('click', shareSelectedFile);
    
    // 列表全选
    const selectAll = document.getElementById('select-all-files');
    if (selectAll) {
        selectAll.addEventListener('change', onSelectAllChange);
    }
    
    // 从链接导入
    document.getElementById('import-url-btn').addEventListener('click', openImportUrlModal);
    document.getElementById('import-start-btn').addEventListener('click', startUrlImport);
    // URL 输入 / 引擎切换时实时刷新提示, 让用户提前看到会走哪条路径
    const _imUrl = document.getElementById('import-url-input');
    const _imEng = document.getElementById('import-engine');
    if (_imUrl) _imUrl.addEventListener('input', refreshImportEngineHint);
    if (_imEng) _imEng.addEventListener('change', refreshImportEngineHint);
    // 导入日志的折叠 / 复制
    const _imLogToggle = document.getElementById('import-log-toggle');
    if (_imLogToggle) {
        _imLogToggle.addEventListener('click', () => {
            const pane = document.getElementById('import-log-pane');
            const collapsed = _imLogToggle.getAttribute('data-collapsed') === '1';
            const icon = _imLogToggle.querySelector('i');
            if (collapsed) {
                pane.style.display = '';
                if (icon) icon.className = 'bi bi-chevron-up';
                _imLogToggle.setAttribute('data-collapsed', '0');
            } else {
                pane.style.display = 'none';
                if (icon) icon.className = 'bi bi-chevron-down';
                _imLogToggle.setAttribute('data-collapsed', '1');
            }
        });
    }
    const _imLogCopy = document.getElementById('import-log-copy');
    if (_imLogCopy) {
        _imLogCopy.addEventListener('click', (e) => {
            const text = document.getElementById('import-log-pane').textContent || '';
            window.copyTextWithFeedback(text, { btn: e.currentTarget, successMsg: '日志已复制' });
        });
    }
}

// 绑定搜索事件
function bindSearchEvents() {
    const searchInput = document.getElementById('search-input');
    searchInput.addEventListener('keyup', event => {
        if (event.key === 'Enter') {
            const query = searchInput.value.trim();
            if (query) {
                searchFiles(query);
            }
        }
    });
}

// 绑定视图模式切换事件
function bindViewModeEvents() {
    document.getElementById('grid-view').addEventListener('click', () => {
        setViewMode('grid');
    });
    
    document.getElementById('list-view').addEventListener('click', () => {
        setViewMode('list');
    });
}

// 设置视图模式
function setViewMode(mode) {
    currentViewMode = mode;
    
    if (mode === 'grid') {
        document.getElementById('files-grid').classList.remove('d-none');
        document.getElementById('files-list').classList.add('d-none');
        document.getElementById('grid-view').classList.add('active');
        document.getElementById('list-view').classList.remove('active');
    } else {
        document.getElementById('files-grid').classList.add('d-none');
        document.getElementById('files-list').classList.remove('d-none');
        document.getElementById('grid-view').classList.remove('active');
        document.getElementById('list-view').classList.add('active');
    }
    // 网格与列表各有一套复选框，切换视图时按 selectedFiles 同步勾选状态
    syncFileSelectionToDom();
}

/** 根据 selectedFiles 同步两套视图中的勾选与样式 */
function syncFileSelectionToDom() {
    document.querySelectorAll('#files-grid .file-item, #files-list-body tr.file-item').forEach(item => {
        const path = item.dataset.path;
        const cb = item.querySelector('.file-row-checkbox');
        if (!path || !cb) return;
        const on = selectedFiles.includes(path);
        item.classList.toggle('selected', on);
        cb.checked = on;
    });
    syncSelectAllCheckbox();
}

/** 当前视图下用于全选/同步的复选框选择器（网格与列表各有一份 DOM，避免重复勾选） */
function getVisibleFileCheckboxSelector() {
    return currentViewMode === 'list'
        ? '#files-list-body .file-row-checkbox'
        : '#files-grid .file-row-checkbox';
}

/** 列表排序：目录优先，其次按列与升降序 */
function sortFileList(files, key, dir) {
    const arr = files.slice();
    const mul = dir === 'asc' ? 1 : -1;
    const cmpName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    const parseTime = (s) => {
        if (!s) return 0;
        const t = Date.parse(String(s).replace(' ', 'T'));
        return Number.isNaN(t) ? 0 : t;
    };
    arr.sort((a, b) => {
        if (a.is_dir !== b.is_dir) {
            return a.is_dir ? -1 : 1;
        }
        if (key === 'name') {
            return mul * cmpName(a, b);
        }
        if (key === 'size') {
            const sa = a.is_dir ? 0 : (Number(a.size) || 0);
            const sb = b.is_dir ? 0 : (Number(b.size) || 0);
            const c = sa - sb;
            if (c !== 0) return mul * c;
            return cmpName(a, b);
        }
        if (key === 'modified') {
            const c = parseTime(a.modified) - parseTime(b.modified);
            if (c !== 0) return mul * c;
            return cmpName(a, b);
        }
        return 0;
    });
    return arr;
}

function updateSortHeaderIndicators() {
    document.querySelectorAll('#files-list thead th[data-sort]').forEach(th => {
        const span = th.querySelector('.sort-indicator');
        if (!span) return;
        const k = th.getAttribute('data-sort');
        if (k === fileListSortKey) {
            span.textContent = fileListSortDir === 'asc' ? '↑' : '↓';
            span.classList.remove('d-none');
        } else {
            span.textContent = '';
        }
    });
}

function bindFileListSortEvents() {
    const thead = document.querySelector('#files-list thead');
    if (!thead) return;
    thead.addEventListener('click', (e) => {
        if (e.target.closest('#select-all-files')) return;
        const th = e.target.closest('th[data-sort]');
        if (!th) return;
        const key = th.getAttribute('data-sort');
        if (!key) return;
        if (fileListSortKey === key) {
            fileListSortDir = fileListSortDir === 'asc' ? 'desc' : 'asc';
        } else {
            fileListSortKey = key;
            fileListSortDir = 'asc';
        }
        if (lastRenderedFiles.length) {
            renderFiles(lastRenderedFiles);
        }
    });
}

// 初始化拖放上传
function initializeDragAndDrop() {
    const dropArea = document.getElementById('drop-area');
    
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, preventDefaults, false);
    });
    
    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }
    
    ['dragenter', 'dragover'].forEach(eventName => {
        dropArea.addEventListener(eventName, () => {
            dropArea.classList.add('highlight');
        }, false);
    });
    
    ['dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, () => {
            dropArea.classList.remove('highlight');
        }, false);
    });
    
    dropArea.addEventListener('drop', e => {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    }, false);
}

// 处理选择的文件
function handleFileSelection(e) {
    const files = e.target.files;
    handleFiles(files);
}

// 处理文件
function handleFiles(files) {
    const filesList = document.getElementById('selected-files-list');
    const selectedFilesContainer = document.querySelector('.selected-files');
    
    if (files.length > 0) {
        selectedFilesContainer.classList.remove('d-none');
        filesList.innerHTML = '';
        
        Array.from(files).forEach(file => {
            const listItem = document.createElement('li');
            listItem.className = 'list-group-item d-flex justify-content-between align-items-center';
            listItem.innerHTML = `
                <div>
                    <i class="${getFileIconClass(file.name)}"></i>
                    <span class="ms-2">${file.name}</span>
                </div>
                <span class="badge bg-primary rounded-pill">${formatSize(file.size)}</span>
            `;
            filesList.appendChild(listItem);
        });
    }
}

// 上传选择的文件
// function uploadSelectedFiles() {
//     const fileInput = document.getElementById('file-input');
//     const files = fileInput.files;
    
//     if (files.length === 0) {
//         showMessage('错误', '请选择要上传的文件');
//         return;
//     }
    
//     const formData = new FormData();
//     for (let i = 0; i < files.length; i++) {
//         formData.append('files[]', files[i]);
//     }
//     formData.append('path', currentPath);
    
//     const progressBar = document.querySelector('.upload-progress .progress-bar');
//     const statusText = document.getElementById('upload-status');
//     const progressContainer = document.querySelector('.upload-progress');
    
//     progressContainer.classList.remove('d-none');
//     progressBar.style.width = '0%';
//     statusText.textContent = '上传中...';
    
//     fetch('/api/upload', {
//         method: 'POST',
//         body: formData
//     })
//     .then(response => {
//         if (!response.ok) {
//             throw new Error('上传失败');
//         }
//         return response.json();
//     })
//     .then(data => {
//         progressBar.style.width = '100%';
//         statusText.textContent = '上传完成！';
        
//         setTimeout(() => {
//             const uploadModal = bootstrap.Modal.getInstance(document.getElementById('upload-modal'));
//             uploadModal.hide();
//             resetUploadModal();
            
//             // 刷新文件列表
//             loadFiles(currentPath);
            
//             // 更新存储信息
//             loadStorageInfo();
//         }, 1000);
//     })
//     .catch(error => {
//         progressBar.classList.add('bg-danger');
//         statusText.textContent = `上传失败: ${error.message}`;
//     });
// }

// 重置上传模态框
function resetUploadModal() {
    document.getElementById('file-input').value = '';
    document.getElementById('selected-files-list').innerHTML = '';
    document.querySelector('.selected-files').classList.add('d-none');
    document.querySelector('.upload-progress').classList.add('d-none');
    document.querySelector('.upload-progress .progress-bar').style.width = '0%';
    document.querySelector('.upload-progress .progress-bar').classList.remove('bg-danger');
    document.getElementById('upload-status').textContent = '准备上传...';
}

// 创建新文件夹
function createNewFolder() {
    const folderName = document.getElementById('folder-name').value.trim();
    
    if (!folderName) {
        showMessage('错误', '文件夹名称不能为空');
        return;
    }
    
    fetch('/api/folder/create', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            name: folderName,
            path: getUploadPath()
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.error || '创建文件夹失败'); });
        }
        return response.json();
    })
    .then(data => {
        const modal = bootstrap.Modal.getInstance(document.getElementById('new-folder-modal'));
        modal.hide();
        
        // 刷新文件列表
        refreshAfterImport();
    })
    .catch(error => {
        showMessage('错误', error.message);
    });
}

// 加载文件列表
function loadFiles(path) {
    console.log("loadFiles:"+path);
    // 规范化路径，去除连续斜杠
    if (path && path !== '/') {
        // 删除开头和结尾的斜杠，然后重建路径
        path = path.replace(/^\/+|\/+$/g, '');
        // 对于非根路径，检查是否需要添加前导斜杠
        if (path) {
            path = '/' + path;
        }
    } else {
        // 根路径使用空字符串
        path = '';
    }
    
    currentPath = path || '/';
    clearSelection();
    updatePathNavigation(currentPath);
    
    // 使用规范化后的路径进行API请求
    const apiPath = path ? path : '';
    
    fetch(`/api/files/${apiPath}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('无法加载文件');
            }
            return response.json();
        })
        .then(data => {
            renderFiles(data.files);
        })
        .catch(error => {
            showMessage('错误', error.message);
        });
}

// 按类型加载文件
function loadFilesByType(type) {
    clearSelection();

    fetch(`/api/files/type/${type}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('无法加载文件');
            }
            return response.json();
        })
        .then(data => {
            currentPath = data.current_path;
            const label = NAV_TYPE_LABELS[data.current_path] || data.current_path;
            updatePathNavigation(label, true);
            renderFiles(data.files);
        })
        .catch(error => {
            showMessage('错误', error.message);
        });
}

// 加载最近文件
function loadRecentFiles() {
    clearSelection();
    
    fetch('/api/recent')
        .then(response => {
            if (!response.ok) {
                throw new Error('无法加载最近文件');
            }
            return response.json();
        })
        .then(data => {
            currentPath = 'recent';
            updatePathNavigation('最近文件', true);
            renderFiles(data.files);
        })
        .catch(error => {
            showMessage('错误', error.message);
        });
}

// 搜索文件
function searchFiles(query) {
    clearSelection();
    
    fetch(`/api/search?q=${encodeURIComponent(query)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('搜索失败');
            }
            return response.json();
        })
        .then(data => {
            currentPath = 'search';
            updatePathNavigation(`搜索: ${query}`, true);
            renderFiles(data.files);
        })
        .catch(error => {
            showMessage('错误', error.message);
        });
}

const NAV_TYPE_LABELS = {
    all: '全部文件',
    images: '图片',
    documents: '文档',
    videos: '视频',
    audio: '音频',
    archives: '压缩包',
    code: '代码文件',
    others: '其它',
    applications_android: '应用程序 (Android)',
    applications_linux: '应用程序 (Linux)',
    applications_windows: '应用程序 (Windows)'
};

function syncDocumentTitle(path, isSpecial) {
    const brand = document.body.dataset.appBrand || '云端文件管理';
    let label;
    if (isSpecial) {
        label = NAV_TYPE_LABELS[path] || path;
    } else if (!path || path === '/') {
        label = '我的文件';
    } else {
        const parts = String(path).split('/').filter(p => p);
        label = parts.length ? parts.join(' / ') : '我的文件';
    }
    document.title = `${label} · ${brand}`;
}

function loadSharedFiles() {
    clearSelection();
    fetch('/api/shared-files')
        .then(response => {
            if (!response.ok) throw new Error('无法加载共享文件');
            return response.json();
        })
        .then(data => {
            currentPath = 'shared';
            updatePathNavigation('共享文件', true);
            renderFiles(data.files);
        })
        .catch(error => {
            showMessage('错误', error.message);
        });
}

// 获取当前用户的API密钥
function getUserApiKey() {
    fetch('/api/user')
        .then(handleFetchErrors)
        .then(response => response.json())
        .then(data => {
            const apiKeyElement = document.getElementById('user-api-key');
            if (apiKeyElement && data.api_key) {
                apiKeyElement.value = data.api_key;
            }
        })
        .catch(error => {
            if (error.message !== '未授权') {
                showMessage('错误', '获取API密钥失败');
            }
        });
}

// 生成新的API密钥
function regenerateApiKey() {
    if (!confirm('确定要重新生成API密钥吗？这将使现有的密钥失效。')) {
        return;
    }
    
    fetch('/api/user/api-key/regenerate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(handleFetchErrors)
    .then(response => response.json())
    .then(data => {
        const apiKeyElement = document.getElementById('user-api-key');
        if (apiKeyElement && data.api_key) {
            apiKeyElement.value = data.api_key;
            showMessage('成功', 'API密钥已重新生成');
        }
    })
    .catch(error => {
        showMessage('错误', `生成API密钥失败: ${error.message}`);
    });
}

// 复制API密钥到剪贴板（兼容非 secure context）
function copyApiKey() {
    const apiKeyElement = document.getElementById('user-api-key');
    if (!apiKeyElement) return;
    const text = apiKeyElement.value || '';
    if (!text) { showMessage('提示', 'API 密钥为空'); return; }
    copyTextToClipboard(text).then(ok => {
        if (ok) {
            showMessage('成功', 'API密钥已复制到剪贴板');
        } else {
            promptManualCopy(text, '复制失败，请手动复制 API 密钥（Ctrl+C）：');
        }
    });
}

function shareSelectedFile() {
    if (selectedFiles.length !== 1) return;
    const item = document.querySelector('.file-item.selected');
    if (!item || item.dataset.isDir === 'true') return;
    const fid = item.dataset.fileId;
    if (!fid) return;
    shareFile(parseInt(fid, 10));
}

function onSelectAllChange(e) {
    const checked = e.target.checked;
    const selector = getVisibleFileCheckboxSelector();
    document.querySelectorAll(selector).forEach(cb => {
        cb.checked = checked;
        const item = cb.closest('.file-item');
        if (!item) return;
        const path = item.dataset.path;
        if (checked) {
            item.classList.add('selected');
            if (!selectedFiles.includes(path)) selectedFiles.push(path);
        } else {
            item.classList.remove('selected');
            selectedFiles = selectedFiles.filter(p => p !== path);
        }
    });
    document.querySelectorAll('.file-item').forEach(item => item.classList.remove('focused'));
    updateActionButtons();
}

function onFileCheckboxChange(e) {
    e.stopPropagation();
    const cb = e.target;
    const item = cb.closest('.file-item');
    if (!item) return;
    const path = item.dataset.path;
    if (cb.checked) {
        item.classList.add('selected');
        if (!selectedFiles.includes(path)) selectedFiles.push(path);
    } else {
        item.classList.remove('selected');
        selectedFiles = selectedFiles.filter(p => p !== path);
    }
    document.querySelectorAll('.file-item.focused').forEach(el => el.classList.remove('focused'));
    item.classList.add('focused');
    syncSelectAllCheckbox();
    updateActionButtons();
}

function syncSelectAllCheckbox() {
    const sel = document.getElementById('select-all-files');
    if (!sel) return;
    const boxes = document.querySelectorAll(getVisibleFileCheckboxSelector());
    if (!boxes.length) {
        sel.checked = false;
        sel.indeterminate = false;
        return;
    }
    const n = [...boxes].filter(b => b.checked).length;
    sel.checked = n === boxes.length && n > 0;
    sel.indeterminate = n > 0 && n < boxes.length;
}

/** 通用复制到剪贴板，含按钮反馈和 execCommand fallback。导出到 window 以便其它模块复用。 */
window.copyTextWithFeedback = window.copyTextWithFeedback || async function copyTextWithFeedback(text, opts) {
    const { btn = null, successMsg = '已复制', failMsg = '复制失败' } = (opts || {});
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
        setTimeout(() => { if (icon) icon.className = old; }, 1200);
    }
    if (typeof showMessage === 'function') {
        // 用 toast 风格的轻提示更好, 但项目里 showMessage 是阻塞 modal, 失败时再提示就够
        if (!ok) showMessage(failMsg, '复制到剪贴板被浏览器阻止, 请改用 Ctrl+C 手动复制');
    }
    return ok;
};

const VIDEO_HOST_RE = /(?:^|\.)(youtube\.com|youtu\.be|bilibili\.com|b23\.tv)$/i;

function isVideoSiteUrl(s) {
    if (!s) return false;
    try {
        const u = new URL(s);
        return VIDEO_HOST_RE.test(u.hostname || '');
    } catch (_) { return false; }
}

/** 由 capabilities 接口刷新; 决定是否能用 aria2c, 给用户写实情况说明。 */
const importCaps = { ytdlp: null, aria2c: null, loaded: false };

function loadImportCapabilities() {
    if (importCaps.loaded) return Promise.resolve();
    return fetch('/api/import/capabilities')
        .then(r => r.json())
        .then(data => {
            importCaps.ytdlp = !!data.ytdlp;
            importCaps.aria2c = !!data.aria2c;
            importCaps.loaded = true;
            refreshImportEngineHint();
        })
        .catch(() => { importCaps.loaded = true; });
}

/** 根据当前 URL + 引擎选择, 决定最终用 ytdlp 还是 http; 同步 UI 提示和 mode 段的可见性。 */
function refreshImportEngineHint() {
    const urlInput = document.getElementById('import-url-input');
    const engineSel = document.getElementById('import-engine');
    const hint = document.getElementById('import-engine-hint');
    const modeWrap = document.getElementById('import-mode-wrap');
    const ytWrap = document.getElementById('import-ytdlp-opts');
    if (!urlInput || !engineSel || !hint || !modeWrap) return;
    const url = urlInput.value.trim();
    const engine = engineSel.value;
    const isVideo = isVideoSiteUrl(url);
    const useYtdlp = engine === 'ytdlp' || (engine === 'auto' && isVideo);
    if (useYtdlp) {
        modeWrap.classList.add('d-none');
        if (ytWrap) ytWrap.classList.remove('d-none');
        const ytStatus = document.getElementById('import-ytdlp-status');
        if (ytStatus) {
            ytStatus.textContent = importCaps.loaded
                ? (importCaps.ytdlp ? '· yt-dlp 已就绪' : '· yt-dlp 未安装，导入会失败')
                : '· 检测中…';
            ytStatus.className = 'text-muted small ' + (importCaps.loaded && !importCaps.ytdlp ? 'text-danger' : '');
        }
        const aStatus = document.getElementById('import-aria2c-status');
        if (aStatus) {
            aStatus.textContent = importCaps.loaded
                ? (importCaps.aria2c ? '· 已检测到 aria2c' : '· 系统未安装 aria2c，会退回 yt-dlp 内置下载器')
                : '· 检测中…';
            aStatus.className = 'text-muted small ' + (importCaps.loaded && !importCaps.aria2c ? 'text-warning' : '');
        }
        hint.innerHTML = isVideo
            ? '<i class="bi bi-camera-video me-1"></i>检测到视频站，将用 <code>yt-dlp</code> 下载完整视频 + 音频；如未安装请先 <code>pip install yt-dlp</code>。'
            : '<i class="bi bi-camera-video me-1"></i>强制走 <code>yt-dlp</code>。';
    } else {
        modeWrap.classList.remove('d-none');
        if (ytWrap) ytWrap.classList.add('d-none');
        hint.innerHTML = engine === 'auto'
            ? '<i class="bi bi-info-circle me-1"></i>普通 HTTP 链接，按下面方式下载。'
            : '<i class="bi bi-info-circle me-1"></i>强制走 HTTP / 直链下载。';
    }
}

function openImportUrlModal() {
    document.getElementById('import-url-input').value = '';
    document.getElementById('importModeDirect').checked = true;
    document.getElementById('import-engine').value = 'auto';
    const proxyEl = document.getElementById('import-proxy-input');
    const argsEl = document.getElementById('import-ytdlp-args');
    const ariaEl = document.getElementById('import-aria2c');
    const ariaNEl = document.getElementById('import-aria2c-threads');
    // 输入保留上次填写, 给反复在同一代理 / 同一参数下工作的用户方便
    try {
        const saved = localStorage.getItem('fm.importProxy') || '';
        if (saved && !proxyEl.value) proxyEl.value = saved;
        if (argsEl) argsEl.value = localStorage.getItem('fm.importYtdlpArgs') || '';
        if (ariaEl) {
            const v = localStorage.getItem('fm.importAria2c');
            ariaEl.checked = v == null ? true : (v === '1');
        }
        if (ariaNEl) {
            const t = parseInt(localStorage.getItem('fm.importAria2cThreads'), 10);
            if (t > 0 && t <= 64) ariaNEl.value = String(t);
        }
    } catch (_) { /* ignore */ }
    document.getElementById('import-progress-wrap').classList.add('d-none');
    document.getElementById('import-log-wrap').classList.add('d-none');
    document.getElementById('import-log-pane').textContent = '';
    document.getElementById('import-start-btn').disabled = false;
    const bar = document.getElementById('import-progress-bar');
    bar.className = 'progress-bar progress-bar-striped progress-bar-animated';
    bar.style.width = '0%';
    refreshImportEngineHint();
    loadImportCapabilities();
    const m = new bootstrap.Modal(document.getElementById('importUrlModal'));
    m.show();
}

function refreshAfterImport() {
    if (currentPath === 'shared') loadSharedFiles();
    else if (currentPath === 'recent') loadRecentFiles();
    else if (currentPath === 'search') {
        const q = document.getElementById('search-input').value.trim();
        if (q) searchFiles(q);
    } else if (TYPE_VIEW_PATHS.includes(currentPath)) {
        loadFilesByType(currentPath);
    } else {
        loadFiles(currentPath);
    }
}

/** 把 yt-dlp / 网络的 stdout 文本写到日志面板; 自动滚到底, 控制最大行数防止 DOM 卡死。 */
function updateImportLog(text) {
    const pane = document.getElementById('import-log-pane');
    const wrap = document.getElementById('import-log-wrap');
    if (!pane || !wrap) return;
    if (text == null || text === '') return;
    if (wrap.classList.contains('d-none')) wrap.classList.remove('d-none');
    // 全量替换比 diff 简单且足够 (后端做了上限截断)
    const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 4;
    pane.textContent = text;
    if (atBottom) pane.scrollTop = pane.scrollHeight;
}

function pollImportJob(jobId, bar, txt, list) {
    const tick = () => {
        fetch(`/api/import/job/${jobId}`)
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    bar.classList.add('bg-danger');
                    txt.textContent = data.error;
                    document.getElementById('import-start-btn').disabled = false;
                    return;
                }
                const total = Math.max(1, data.total || 1);
                const step = Math.min(data.step || 0, total);
                const pct = Math.round((step / total) * 100);
                bar.style.width = `${pct}%`;
                txt.textContent = data.message || '…';
                if (data.imported_names && data.imported_names.length) {
                    list.innerHTML = data.imported_names.map(n => {
                        const t = String(n).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        return `<li>${t}</li>`;
                    }).join('');
                }
                if (data.log) updateImportLog(data.log);
                // 视频站 / 强制 yt-dlp 时, 把日志面板默认展开
                if ((data.engine === 'ytdlp') && document.getElementById('import-log-wrap').classList.contains('d-none')) {
                    document.getElementById('import-log-wrap').classList.remove('d-none');
                }
                if (data.status === 'done') {
                    bar.classList.remove('progress-bar-animated');
                    document.getElementById('import-start-btn').disabled = false;
                    refreshAfterImport();
                    return;
                }
                if (data.status === 'error') {
                    bar.classList.add('bg-danger');
                    document.getElementById('import-start-btn').disabled = false;
                    // 失败时强制把日志面板展开, 这样用户能立刻看到 yt-dlp 报错堆栈
                    document.getElementById('import-log-wrap').classList.remove('d-none');
                    showMessage('导入失败', (data.message || '') + (data.log ? '\n\n----- 运行日志 -----\n' + data.log : ''));
                    return;
                }
                setTimeout(tick, 600);
            })
            .catch(() => {
                document.getElementById('import-start-btn').disabled = false;
            });
    };
    tick();
}

function startUrlImport() {
    const url = document.getElementById('import-url-input').value.trim();
    if (!url) {
        showMessage('提示', '请输入链接');
        return;
    }
    const mode = document.querySelector('input[name="importMode"]:checked').value;
    const engine = document.getElementById('import-engine').value;
    const proxy = document.getElementById('import-proxy-input').value.trim();
    const ytdlpArgs = (document.getElementById('import-ytdlp-args').value || '').trim();
    const useAria2c = document.getElementById('import-aria2c').checked;
    let aria2cThreads = parseInt(document.getElementById('import-aria2c-threads').value, 10);
    if (!(aria2cThreads >= 1 && aria2cThreads <= 64)) aria2cThreads = 16;
    try {
        localStorage.setItem('fm.importProxy', proxy);
        localStorage.setItem('fm.importYtdlpArgs', ytdlpArgs);
        localStorage.setItem('fm.importAria2c', useAria2c ? '1' : '0');
        localStorage.setItem('fm.importAria2cThreads', String(aria2cThreads));
    } catch (_) { /* ignore */ }

    const wrap = document.getElementById('import-progress-wrap');
    const bar = document.getElementById('import-progress-bar');
    const txt = document.getElementById('import-progress-text');
    const list = document.getElementById('import-done-list');
    wrap.classList.remove('d-none');
    bar.style.width = '0%';
    bar.classList.remove('bg-danger');
    bar.className = 'progress-bar progress-bar-striped progress-bar-animated';
    txt.textContent = '正在提交…';
    list.innerHTML = '';
    document.getElementById('import-log-pane').textContent = '';
    document.getElementById('import-log-wrap').classList.add('d-none');
    document.getElementById('import-start-btn').disabled = true;

    let pathParam = currentPath;
    if (pathParam === 'shared' || pathParam === 'recent' || pathParam === 'search' ||
        TYPE_VIEW_PATHS.includes(pathParam)) {
        pathParam = '';
    }
    if (pathParam === '/' || pathParam === '') pathParam = '';
    else if (pathParam.startsWith('/')) pathParam = pathParam.slice(1);

    fetch('/api/import/url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            url, mode, path: pathParam, engine, proxy,
            ytdlp_args: ytdlpArgs,
            aria2c: useAria2c,
            aria2c_threads: aria2cThreads,
        })
    })
    .then(r => r.json().then(j => ({ ok: r.ok, body: j })))
    .then(({ ok, body }) => {
        if (!ok || !body.job_id) throw new Error(body.error || '创建任务失败');
        if (body.engine === 'ytdlp') {
            // 立刻展开日志面板, 让用户看到 "正在调用 yt-dlp ..."
            document.getElementById('import-log-wrap').classList.remove('d-none');
        }
        pollImportJob(body.job_id, bar, txt, list);
    })
    .catch(err => {
        txt.textContent = err.message || '失败';
        document.getElementById('import-start-btn').disabled = false;
        showMessage('错误', err.message);
    });
}

/** 根据 public_share_id 生成公开访问链接（与后端 /shared/<share_id> 一致） */
function shareUrlForPublicId(publicShareId) {
    if (!publicShareId) return '';
    return `${window.location.origin}/shared/${encodeURIComponent(publicShareId)}`;
}

/** 从当前列表缓存复制已分享文件的链接 */
function showShareLinkCopy(fileId) {
    const f = getFileFromRenderCache(fileId);
    if (!f || !f.public_share_id) {
        showMessage('提示', '该条目暂无公开分享链接');
        return;
    }
    showShareUrlDialog(shareUrlForPublicId(f.public_share_id));
}

// 添加文件分享功能
function shareFile(fileId) {
    fetch(`/api/files/${fileId}/share`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(handleFetchErrors)
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showShareUrlDialog(data.share_url);
            // 刷新当前目录以更新分享状态
            if (currentPath === 'recent') {
                loadRecentFiles();
            } else if (currentPath === 'search') {
                const query = document.getElementById('search-input').value.trim();
                searchFiles(query);
            } else if (currentPath === 'shared') {
                loadSharedFiles();
            } else if (TYPE_VIEW_PATHS.includes(currentPath)) {
                loadFilesByType(currentPath);
            } else {
                loadFiles(currentPath);
            }
        }
    })
    .catch(error => {
        showMessage('错误', `分享失败: ${error.message}`);
    });
}

// 显示分享URL对话框
function showShareUrlDialog(url) {
    // 创建模态框
    const modalHtml = `
        <div class="modal fade" id="shareUrlModal" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">文件分享链接</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <p>您可以将以下链接分享给他人，以便他们访问此文件：</p>
                        <div class="input-group mb-3">
                            <input type="text" class="form-control" id="share-url-input" value="${url}" readonly>
                            <button class="btn btn-outline-primary" type="button" id="copy-share-url">复制</button>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // 添加到DOM
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('shareUrlModal'));
    modal.show();
    
    // 添加复制按钮事件
    document.getElementById('copy-share-url').addEventListener('click', function() {
        const input = document.getElementById('share-url-input');
        const btn = this;
        input.select();
        copyTextToClipboard(input.value).then(ok => {
            if (ok) {
                btn.textContent = '已复制!';
                setTimeout(() => { btn.textContent = '复制'; }, 2000);
            } else {
                promptManualCopy(input.value, '复制失败，请手动复制链接（Ctrl+C）：');
            }
        });
    });
    
    // 模态框关闭后移除DOM
    document.getElementById('shareUrlModal').addEventListener('hidden.bs.modal', function() {
        this.remove();
    });
}

// 取消分享文件
function unshareFile(fileId) {
    if (!confirm('确定要取消分享此文件吗？')) {
        return;
    }
    
    fetch(`/api/files/${fileId}/unshare`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(handleFetchErrors)
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showMessage('成功', '已取消分享');
            // 刷新当前目录以更新分享状态
            if (currentPath === 'recent') {
                loadRecentFiles();
            } else if (currentPath === 'search') {
                const query = document.getElementById('search-input').value.trim();
                searchFiles(query);
            } else if (currentPath === 'shared') {
                loadSharedFiles();
            } else if (TYPE_VIEW_PATHS.includes(currentPath)) {
                loadFilesByType(currentPath);
            } else {
                loadFiles(currentPath);
            }
        }
    })
    .catch(error => {
        showMessage('错误', `取消分享失败: ${error.message}`);
    });
}
// 渲染文件列表
function renderFiles(files) {
    lastRenderedFiles = Array.isArray(files) ? files.slice() : [];
    const sorted = sortFileList(lastRenderedFiles, fileListSortKey, fileListSortDir);

    const gridContainer = document.getElementById('files-grid');
    const listBody = document.getElementById('files-list-body');
    const emptyFolder = document.getElementById('empty-folder');
    
    gridContainer.innerHTML = '';
    listBody.innerHTML = '';
    
    if (sorted.length === 0) {
        emptyFolder.classList.remove('d-none');
    } else {
        emptyFolder.classList.add('d-none');
        
        // 渲染网格视图,在文件项中添加分享状态和操作
        sorted.forEach(file => {
            const col = document.createElement('div');
            col.className = 'col-6 col-sm-4 col-md-3 col-lg-2';
            
            let fileSize = file.is_dir ? `${file.size_formatted}` : file.size_formatted;
            
            // 添加适当的类和更好的卡片布局
            col.innerHTML = `
                <div class="file-item card h-100 position-relative" data-path="${file.path}" data-is-dir="${file.is_dir}" data-file-id="${file.id}" tabindex="0">
                    <input type="checkbox" class="form-check-input file-row-checkbox position-absolute" style="top:8px;left:8px;z-index:3;" title="多选">
                    <div class="card-body text-center p-3">
                        <div class="file-icon mb-2">
                            <i class="bi ${file.icon} fs-1"></i>
                        </div>
                        <h6 class="card-title mb-0 text-truncate" title="${file.name}">${file.name}</h6>
                        <p class="card-text small text-muted mb-0">${fileSize}</p>
                    </div>
                    <div class="select-indicator position-absolute top-0 end-0 p-2 d-none">
                        <i class="bi bi-check-circle-fill text-primary"></i>
                    </div>
                </div>
            `;
            // 已分享：角标可点击直接弹出公开链接
        if (file.is_public) {
            const shareIcon = document.createElement('div');
            shareIcon.className = 'position-absolute bottom-0 start-0 p-2';
            if (file.public_share_id) {
                shareIcon.innerHTML = '<a href="#" class="text-success text-decoration-none" title="查看/复制分享链接"><i class="bi bi-share-fill"></i></a>';
                shareIcon.querySelector('a').addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    showShareLinkCopy(file.id);
                });
            } else {
                shareIcon.innerHTML = '<i class="bi bi-share-fill text-success"></i>';
            }
            col.querySelector('.file-item').appendChild(shareIcon);
        }
        
        // 在文件卡片的操作菜单中追加项；文件夹与文件分两套，避免"对文件夹做版本/添加资源"这种无意义项
        const cardBody = col.querySelector('.card-body');
        const actionsMenu = document.createElement('div');
        actionsMenu.className = 'file-actions dropdown';
        const shareLine = file.is_public
            ? `<li><a class="dropdown-item" href="#" onclick="unshareFile(${file.id}); return false;"><i class="bi bi-x-circle me-2"></i>取消分享</a></li>`
            : `<li><a class="dropdown-item" href="#" onclick="shareFile(${file.id}); return false;"><i class="bi bi-share me-2"></i>分享</a></li>`;
        const shareLinkLine =
            file.is_public && file.public_share_id
                ? `<li><a class="dropdown-item" href="#" onclick="showShareLinkCopy(${file.id}); return false;"><i class="bi bi-link-45deg me-2"></i>复制分享链接</a></li>`
                : '';
        const menuItems = file.is_dir
            ? `
                <li><a class="dropdown-item" href="#" onclick="previewFileById(${file.id}); return false;"><i class="bi bi-folder2-open me-2"></i>打开</a></li>
                <li><a class="dropdown-item" href="#" onclick="openFolderWatchById(${file.id}); return false;"><i class="bi bi-broadcast me-2"></i>启动监听...</a></li>
                <li><a class="dropdown-item" href="#" onclick="openFolderPluginById(${file.id}); return false;"><i class="bi bi-puzzle me-2"></i>插件管理...</a></li>
                ${shareLine}
                ${shareLinkLine}
                <li><hr class="dropdown-divider"></li>
                <li><a class="dropdown-item text-danger" href="#" onclick="deleteSelectedFiles(); return false;"><i class="bi bi-trash me-2"></i>删除</a></li>`
            : `
                <li><a class="dropdown-item" href="#" onclick="previewFileById(${file.id}); return false;"><i class="bi bi-eye me-2"></i>预览</a></li>
                <li><a class="dropdown-item" href="#" onclick="downloadSelectedFiles(); return false;"><i class="bi bi-download me-2"></i>下载</a></li>
                ${shareLine}
                ${shareLinkLine}
                <li><a class="dropdown-item" href="#" onclick="openVersionModalById(${file.id}); return false;"><i class="bi bi-layers me-2"></i>版本管理</a></li>
                <li><a class="dropdown-item" href="#" onclick="openAddResourceFromFileById(${file.id}); return false;"><i class="bi bi-collection me-2"></i>添加到资源</a></li>
                <li><hr class="dropdown-divider"></li>
                <li><a class="dropdown-item text-danger" href="#" onclick="deleteSelectedFiles(); return false;"><i class="bi bi-trash me-2"></i>删除</a></li>`;
        actionsMenu.innerHTML = `
            <button class="btn btn-sm btn-link dropdown-toggle position-absolute top-0 end-0" type="button" data-bs-toggle="dropdown">
                <i class="bi bi-three-dots-vertical"></i>
            </button>
            <ul class="dropdown-menu">
                ${menuItems}
            </ul>
        `;
        cardBody.appendChild(actionsMenu);
            gridContainer.appendChild(col);
            
            // 添加点击事件
            const fileItem = col.querySelector('.file-item');
            const gridCb = fileItem.querySelector('.file-row-checkbox');
            if (selectedFiles.includes(file.path)) {
                fileItem.classList.add('selected');
                if (gridCb) gridCb.checked = true;
            }
            if (gridCb) {
                gridCb.addEventListener('click', ev => ev.stopPropagation());
                gridCb.addEventListener('change', onFileCheckboxChange);
            }
            addFileItemEvents(fileItem);
        });
        
        // 渲染列表视图（操作列为按钮 + 事件绑定，避免内联 JSON 导致「添加到资源」无法跳转）
        sorted.forEach(file => {
            const row = document.createElement('tr');
            row.className = 'file-item';
            row.setAttribute('data-path', file.path);
            row.setAttribute('data-is-dir', file.is_dir);
            row.setAttribute('data-file-id', file.id);
            row.setAttribute('tabindex', '0');

            const safeName = escapeHtml(String(file.name ?? ''));
            row.innerHTML = `
                <td><input type="checkbox" class="form-check-input file-checkbox file-row-checkbox"></td>
                <td>
                    <i class="bi ${file.icon} me-2"></i>
                    <span title="${safeName}">${safeName}</span>
                </td>
                <td>${escapeHtml(String(file.size_formatted ?? ''))}</td>
                <td>${escapeHtml(String(file.type ?? ''))}</td>
                <td>${escapeHtml(String(file.modified ?? ''))}</td>
            `;

            const tdAct = document.createElement('td');
            tdAct.className = 'file-actions-col text-end text-nowrap align-middle';

            if (file.is_public && file.public_share_id) {
                const btnShareLink = document.createElement('button');
                btnShareLink.type = 'button';
                btnShareLink.className = 'btn btn-sm btn-outline-info me-1';
                btnShareLink.innerHTML = '<i class="bi bi-link-45deg me-1"></i>分享链';
                btnShareLink.title = '复制分享链接';
                btnShareLink.addEventListener('click', (e) => {
                    e.stopPropagation();
                    showShareLinkCopy(file.id);
                });
                tdAct.appendChild(btnShareLink);
            }

            if (file.is_dir) {
                const btnOpen = document.createElement('button');
                btnOpen.type = 'button';
                btnOpen.className = 'btn btn-sm btn-outline-secondary me-1';
                btnOpen.innerHTML = '<i class="bi bi-folder2-open me-1"></i>打开';
                btnOpen.addEventListener('click', (e) => {
                    e.stopPropagation();
                    loadFiles(file.path);
                });
                tdAct.appendChild(btnOpen);

                const btnWatch = document.createElement('button');
                btnWatch.type = 'button';
                btnWatch.className = 'btn btn-sm btn-outline-primary me-1';
                btnWatch.innerHTML = '<i class="bi bi-broadcast me-1"></i>监听';
                btnWatch.title = '启动监听并设置远端接口';
                btnWatch.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openFolderWatchModal({ id: file.id, name: file.name, path: file.path });
                });
                tdAct.appendChild(btnWatch);

                const btnPlugin = document.createElement('button');
                btnPlugin.type = 'button';
                btnPlugin.className = 'btn btn-sm btn-outline-success';
                btnPlugin.innerHTML = '<i class="bi bi-puzzle me-1"></i>插件';
                btnPlugin.title = '管理该文件夹的插件';
                btnPlugin.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openFolderPluginModal({ id: file.id, name: file.name, path: file.path });
                });
                tdAct.appendChild(btnPlugin);
            } else {
                const btnVer = document.createElement('button');
                btnVer.type = 'button';
                btnVer.className = 'btn btn-sm btn-outline-primary me-1';
                btnVer.innerHTML = '<i class="bi bi-layers me-1"></i>版本';
                btnVer.title = '版本管理';
                btnVer.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openVersionModal(file);
                });

                const btnRes = document.createElement('button');
                btnRes.type = 'button';
                btnRes.className = 'btn btn-sm btn-outline-success';
                btnRes.innerHTML = '<i class="bi bi-collection me-1"></i>资源';
                btnRes.title = '添加到资源';
                btnRes.addEventListener('click', (e) => {
                    e.stopPropagation();
                    openAddResourceFromFile(file);
                });

                tdAct.appendChild(btnVer);
                tdAct.appendChild(btnRes);
            }

            row.appendChild(tdAct);
            listBody.appendChild(row);

            const listCb = row.querySelector('.file-row-checkbox');
            if (selectedFiles.includes(file.path)) {
                row.classList.add('selected');
                if (listCb) listCb.checked = true;
            }
            if (listCb) {
                listCb.addEventListener('click', ev => ev.stopPropagation());
                listCb.addEventListener('change', onFileCheckboxChange);
            }
            addFileItemEvents(row);
        });
        updateSortHeaderIndicators();
        syncSelectAllCheckbox();
    }
}

// 为文件项添加事件
function addFileItemEvents(fileItem) {
    const isListRow = fileItem.tagName === 'TR';

    fileItem.addEventListener('click', event => {
        if (event.target.classList.contains('form-check-input')) return;
        if (event.target.closest('.file-actions-col')) return;
        if (event.target.closest('.file-actions')) return;
        if (event.target.closest('button')) return;
        if (event.target.closest('.dropdown')) return;

        if (isListRow) {
            if (event.detail !== 1) return;
            const path = fileItem.dataset.path;
            const cb = fileItem.querySelector('.file-row-checkbox');
            fileItem.classList.toggle('selected');
            if (fileItem.classList.contains('selected')) {
                if (!selectedFiles.includes(path)) selectedFiles.push(path);
            } else {
                selectedFiles = selectedFiles.filter(p => p !== path);
            }
            if (cb) cb.checked = fileItem.classList.contains('selected');
            document.querySelectorAll('.file-item.focused').forEach(i => i.classList.remove('focused'));
            fileItem.classList.add('focused');
            syncSelectAllCheckbox();
            updateActionButtons();
        } else {
            document.querySelectorAll('.file-item.focused').forEach(item => {
                item.classList.remove('focused');
            });
            fileItem.classList.add('focused');
        }
    });

    // 右键菜单：仅对文件夹给出"管理监听"入口（其它项目仍可显示通用菜单）
    fileItem.addEventListener('contextmenu', event => {
        if (event.target.classList.contains('form-check-input')) return;
        if (event.target.closest('button')) return;
        event.preventDefault();
        // 让该项视觉聚焦，便于用户确认右键的是哪个
        document.querySelectorAll('.file-item.focused').forEach(i => i.classList.remove('focused'));
        fileItem.classList.add('focused');
        showFileContextMenu(event, fileItem);
    });

    fileItem.addEventListener('dblclick', event => {
        if (event.target.classList.contains('form-check-input')) return;
        if (event.target.closest('.file-actions-col')) return;
        if (event.target.closest('.file-actions')) return;
        if (event.target.closest('button')) return;
        if (event.target.closest('.dropdown')) return;

        const filePath = fileItem.dataset.path;
        const isDir = fileItem.dataset.isDir === 'true';

        if (isDir) {
            loadFiles(filePath);
        } else {
            previewFile(filePath);
        }
    });
}

// 更新操作按钮状态
function updateActionButtons() {
    const deleteBtn = document.getElementById('delete-btn');
    const downloadBtn = document.getElementById('download-btn');
    const previewBtn = document.getElementById('preview-btn');
    const shareBtn = document.getElementById('share-btn');
    
    if (selectedFiles.length > 0) {
        deleteBtn.removeAttribute('disabled');
        downloadBtn.removeAttribute('disabled');
    } else {
        deleteBtn.setAttribute('disabled', 'disabled');
        downloadBtn.setAttribute('disabled', 'disabled');
    }
    
    // 只有选择单个文件时才能预览或分享
    if (selectedFiles.length === 1) {
        const selectedItem = document.querySelector('.file-item.selected');
        const isDir = selectedItem && selectedItem.dataset.isDir === 'true';
        
        if (!isDir) {
            previewBtn.removeAttribute('disabled');
            shareBtn.removeAttribute('disabled');
        } else {
            previewBtn.setAttribute('disabled', 'disabled');
            shareBtn.setAttribute('disabled', 'disabled');
        }
    } else {
        previewBtn.setAttribute('disabled', 'disabled');
        shareBtn.setAttribute('disabled', 'disabled');
    }
    
    // 更新选择指示器
    updateFileSelectionIndicators();
}

// 更新文件选择指示器
function updateFileSelectionIndicators() {
    document.querySelectorAll('.file-item').forEach(item => {
        const indicator = item.querySelector('.select-indicator');
        if (!indicator) return;
        
        if (item.classList.contains('selected')) {
            indicator.classList.remove('d-none');
        } else {
            indicator.classList.add('d-none');
        }
    });
}

// 清除选择
function clearSelection() {
    document.querySelectorAll('.file-item').forEach(item => {
        item.classList.remove('selected');
        item.classList.remove('focused');
        const c = item.querySelector('.file-row-checkbox');
        if (c) c.checked = false;
    });
    selectedFiles = [];
    const sel = document.getElementById('select-all-files');
    if (sel) {
        sel.checked = false;
        sel.indeterminate = false;
    }
    updateActionButtons();
}

// 预览选中的文件
function previewSelectedFile() {
    if (selectedFiles.length === 1) {
        const filePath = selectedFiles[0];
        previewFile(filePath);
    }
}
//yiye add for 
function selectedFileIsDir(){
    const focusedItem = document.querySelector('.file-item.focused');
        if (!focusedItem) return;
    return focusedItem.dataset.isDir === 'true';
}
// 预览文件
function previewFile(filePath) {
    if (selectedFileIsDir()){
        loadFiles(filePath)
        return;
    }
    // console.trace(); // 直接在控制台输出调用栈
    console.log(filePath)
    const modal = new bootstrap.Modal(document.getElementById('preview-modal'));
    const modalTitle = document.getElementById('preview-title');
    const modalContent = document.getElementById('preview-content');
    const downloadBtn = document.getElementById('preview-download');
    
    // 清空之前的内容
    modalContent.innerHTML = '<div class="text-center"><div class="spinner-border" role="status"></div><p>加载中...</p></div>';
    modalTitle.textContent = '加载中...';
    if (filePath.startsWith("http://") || filePath.startsWith("https://")){
        window.open(filePath, '_blank'); // 在新标签页中打开
        return;
    }
    // 设置下载按钮（优先 id，与列表下载一致）
    downloadBtn.onclick = () => {
        window.location.href = downloadUrlForPath(filePath);
    };
    
    // 发起预览请求
    fetch(`/api/preview/${encodeURI(filePath)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('无法加载预览');
            }
            return response.json();
        })
        .then(data => {
            modalTitle.textContent = data.info.name;
            
            // 根据文件类型处理预览内容
            switch(data.type) {
                case 'image':
                    modalContent.innerHTML = `
                        <div class="text-center">
                            <img src="${data.url}" class="img-fluid" alt="${data.info.name}">
                        </div>
                        <div class="mt-3">
                            <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                            <p><strong>类型:</strong> ${data.info.type}</p>
                        </div>
                    `;
                    break;
                    
                case 'video':
                    modalContent.innerHTML = `
                        <div class="text-center">
                            <video controls class="img-fluid">
                                <source src="${data.url}" type="${data.info.type}">
                                您的浏览器不支持视频播放。
                            </video>
                        </div>
                        <div class="mt-3">
                            <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                            <p><strong>类型:</strong> ${data.info.type}</p>
                        </div>
                    `;
                    break;
                    
                case 'audio':
                    modalContent.innerHTML = `
                        <div class="text-center">
                            <audio controls class="w-100">
                                <source src="${data.url}" type="${data.info.type}">
                                您的浏览器不支持音频播放。
                            </audio>
                        </div>
                        <div class="mt-3">
                            <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                            <p><strong>类型:</strong> ${data.info.type}</p>
                        </div>
                    `;
                    break;
                    
                case 'pdf':
                    modalContent.innerHTML = `
                        <div class="ratio ratio-16x9">
                            <iframe src="${data.url}" allowfullscreen></iframe>
                        </div>
                        <div class="mt-3">
                            <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                            <p><strong>类型:</strong> ${data.info.type}</p>
                        </div>
                    `;
                    break;
                    
                case 'text':
                     // 添加编辑模式标志
                    let isEditMode = false;
                    let originalContent = data.content;
                    
                    // 创建工具栏
                    const toolbar = `
                        <div class="toolbar mb-3 d-flex justify-content-between align-items-center">
                            <div>
                                <button id="editToggleBtn" class="btn btn-sm btn-primary">
                                    <i class="fas fa-edit"></i> 编辑
                                </button>
                                <button id="saveBtn" class="btn btn-sm btn-success" style="display: none;">
                                    <i class="fas fa-save"></i> 保存
                                </button>
                                <button id="cancelBtn" class="btn btn-sm btn-secondary" style="display: none;">
                                    <i class="fas fa-times"></i> 取消
                                </button>
                            </div>
                            <div class="text-muted small">
                                <span>${data.info.type}</span> | 
                                <span>${data.info.size_formatted}</span>
                            </div>
                        </div>
                    `;
                    
                    // 根据文件类型渲染内容
                    function renderContent(content, editMode = false) {
                        if (editMode) {
                            // 编辑模式 - 显示文本编辑器
                            return `
                                <div class="editor-container">
                                    <textarea id="textEditor" class="form-control" 
                                        style="min-height: 400px; font-family: 'Consolas', 'Monaco', monospace;"
                                        spellcheck="false">${escapeHtml(content)}</textarea>
                                </div>
                            `;
                        } else {
                            // 预览模式
                            if (data.extension === 'md') {
                                // Markdown预览
                                return `
                                    <div class="bg-light p-3 rounded markdown-content overflow-auto" style="max-height: 500px;">
                                        ${marked.parse(content)}
                                    </div>
                                `;
                            } else if (['js', 'html', 'css', 'py', 'java', 'c', 'cpp', 'php', 'rb', 'go', 'json', 'xml'].includes(data.extension)) {
                                // 代码高亮预览
                                const highlighted = hljs.highlightAuto(content).value;
                                return `
                                    <div class="overflow-auto" style="max-height: 500px;">
                                        <pre class="bg-light p-3 rounded mb-0"><code class="hljs">${highlighted}</code></pre>
                                    </div>
                                `;
                            } else {
                                // 普通文本预览
                                return `
                                    <div class="overflow-auto" style="max-height: 500px;">
                                        <pre class="bg-light p-3 rounded mb-0">${escapeHtml(content)}</pre>
                                    </div>
                                `;
                            }
                        }
                    }
                    
                    // 初始渲染
                    modalContent.innerHTML = toolbar + `<div id="contentArea">${renderContent(data.content, false)}</div>`;
                    
                    // 添加事件监听器
                    setTimeout(() => {
                        const editToggleBtn = document.getElementById('editToggleBtn');
                        const saveBtn = document.getElementById('saveBtn');
                        const cancelBtn = document.getElementById('cancelBtn');
                        const contentArea = document.getElementById('contentArea');
                        
                        // 编辑/预览切换
                        editToggleBtn.addEventListener('click', function() {
                            isEditMode = !isEditMode;
                            
                            if (isEditMode) {
                                // 切换到编辑模式
                                contentArea.innerHTML = renderContent(data.content, true);
                                editToggleBtn.style.display = 'none';
                                saveBtn.style.display = 'inline-block';
                                cancelBtn.style.display = 'inline-block';
                                
                                // 如果是Markdown文件，可以添加实时预览
                                if (data.extension === 'md') {
                                    addMarkdownLivePreview();
                                }
                            }
                        });
                        
                        // 保存按钮
                        saveBtn.addEventListener('click', async function() {
                            const textEditor = document.getElementById('textEditor');
                            const newContent = textEditor.value;
                            
                            // 显示加载状态
                            saveBtn.disabled = true;
                            saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 保存中...';
                            
                            try {
                                // 调用保存API
                                const response = await fetch('/api/save-file', {
                                    method: 'POST',
                                    headers: {
                                        'Content-Type': 'application/json',
                                    },
                                    body: JSON.stringify({
                                        path: data.info.name,
                                        parent_path:getCurrentFolderId(),
                                        content: newContent
                                    })
                                });
                                
                                if (response.ok) {
                                    // 更新内容
                                    data.content = newContent;
                                    originalContent = newContent;
                                    
                                    // 切换回预览模式
                                    isEditMode = false;
                                    contentArea.innerHTML = renderContent(newContent, false);
                                    editToggleBtn.style.display = 'inline-block';
                                    saveBtn.style.display = 'none';
                                    cancelBtn.style.display = 'none';
                                    
                                    // 显示成功提示
                                    showNotification('文件保存成功', 'success');
                                } else {
                                    throw new Error('保存失败');
                                }
                            } catch (error) {
                                console.error('保存文件出错:', error);
                                showNotification('保存失败: ' + error.message, 'error');
                            } finally {
                                saveBtn.disabled = false;
                                saveBtn.innerHTML = '<i class="fas fa-save"></i> 保存';
                            }
                        });
                        
                        // 取消按钮
                        cancelBtn.addEventListener('click', function() {
                            // 恢复原始内容
                            data.content = originalContent;
                            isEditMode = false;
                            contentArea.innerHTML = renderContent(originalContent, false);
                            editToggleBtn.style.display = 'inline-block';
                            saveBtn.style.display = 'none';
                            cancelBtn.style.display = 'none';
                        });
                     }, 100);
                    break;
                    
                case 'unsupported':
                    modalContent.innerHTML = `
                        <div class="text-center">
                            <i class="bi bi-file-earmark-x fs-1 text-muted"></i>
                            <h4 class="mt-3">无法预览此文件类型</h4>
                            <p class="text-muted">请下载后在本地查看</p>
                        </div>
                        <div class="mt-3">
                            <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                            <p><strong>类型:</strong> ${data.info.type}</p>
                        </div>
                    `;
                    break;
                    
                case 'error':
                    modalContent.innerHTML = `
                        <div class="alert alert-danger">
                            <i class="bi bi-exclamation-triangle-fill me-2"></i> ${data.error}
                        </div>
                        <div class="mt-3">
                            <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                            <p><strong>类型:</strong> ${data.info.type}</p>
                        </div>
                    `;
                    break;
            }
        })
        .catch(error => {
            modalContent.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i> 加载预览失败: ${error.message}
                </div>
            `;
        });
    
    // 显示模态框
    modal.show();
}

// Markdown实时预览（可选）
function addMarkdownLivePreview() {
    const textEditor = document.getElementById('textEditor');
    const contentArea = document.getElementById('contentArea');
    
    // 创建分屏预览
    contentArea.innerHTML = `
        <div class="row">
            <div class="col-md-6">
                <h6 class="mb-2">编辑</h6>
                <textarea id="textEditor" class="form-control" 
                    style="min-height: 400px; font-family: 'Consolas', 'Monaco', monospace;"
                    spellcheck="false">${escapeHtml(data.content)}</textarea>
            </div>
            <div class="col-md-6">
                <h6 class="mb-2">预览</h6>
                <div id="markdownPreview" class="bg-light p-3 rounded markdown-content overflow-auto" 
                    style="min-height: 400px; max-height: 400px;">
                    ${marked.parse(data.content)}
                </div>
            </div>
        </div>
    `;
    
    // 实时更新预览
    document.getElementById('textEditor').addEventListener('input', function(e) {
        const preview = document.getElementById('markdownPreview');
        preview.innerHTML = marked.parse(e.target.value);
    });
}

// 通知函数
function showNotification(message, type = 'info') {
    // 创建通知元素
    const notification = document.createElement('div');
    notification.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
    notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 250px;';
    notification.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(notification);
    
    // 自动移除
    setTimeout(() => {
        notification.remove();
    }, 3000);
}

// 删除选中的文件
function deleteSelectedFiles() {
    console.log('deleteSelectedFiles:'+selectedFiles)
    if (selectedFiles.length === 0) return;
    
    if (!confirm(`确定要删除${selectedFiles.length > 1 ? '这些文件' : '这个文件'}吗？此操作不可恢复。`)) {
        return;
    }
    
    let deletePromises = selectedFiles.map(filePath => {
        return fetch(`/api/files/${filePath}`, {
            method: 'DELETE'
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => { throw new Error(err.error || '删除失败'); });
            }
            return response.json();
        });
    });
    
    Promise.all(deletePromises)
        .then(() => {
            // 刷新文件列表
            if (currentPath === 'recent') {
                loadRecentFiles();
            } else if (currentPath.startsWith('search')) {
                const query = document.getElementById('search-input').value.trim();
                searchFiles(query);
            } else if (currentPath === 'shared') {
                loadSharedFiles();
            } else if (TYPE_VIEW_PATHS.includes(currentPath)) {
                loadFilesByType(currentPath);
            } else {
                loadFiles(currentPath);
            }
            
            // 更新存储信息
            loadStorageInfo();
        })
        .catch(error => {
            showMessage('错误', error.message);
        });
}

// 下载选中的文件（使用文件 id，避免相对路径在 URL 中被误解析）
function downloadSelectedFiles() {
    if (selectedFiles.length === 0) return;
    
    if (selectedFiles.length === 1) {
        window.location.href = downloadUrlForPath(selectedFiles[0]);
        return;
    }
    
    selectedFiles.forEach((filePath, index) => {
        setTimeout(() => {
            window.open(downloadUrlForPath(filePath), '_blank');
        }, index * 500);
    });
}

// 更新路径导航
function updatePathNavigation(path, isSpecial = false) {
    const pathElement = document.getElementById('current-path');
    
    if (isSpecial) {
        // 对于特殊路径，直接显示名称
        pathElement.textContent = path;
    } else {
        // 构建面包屑导航
        pathElement.innerHTML = '';
        
        // 根目录
        const rootLink = document.createElement('a');
        rootLink.href = '#';
        rootLink.textContent = '根目录';
        rootLink.addEventListener('click', event => {
            event.preventDefault();
            loadFiles('/');
        });
        pathElement.appendChild(rootLink);
        
        // 如果不是根目录，添加子路径
        if (path && path !== '/') {
            const parts = path.split('/').filter(p => p);
            let currentPath = '';
            
            parts.forEach((part, index) => {
                pathElement.appendChild(document.createTextNode(' / '));
                
                currentPath += '/' + part;
                
                const link = document.createElement('a');
                link.href = '#';
                link.textContent = part;
                
                // 如果是最后一项，不添加点击事件
                if (index < parts.length - 1) {
                    const pathCopy = currentPath;
                    link.addEventListener('click', event => {
                        event.preventDefault();
                        loadFiles(pathCopy);
                    });
                }
                
                pathElement.appendChild(link);
            });
        }
    }
    syncDocumentTitle(path, isSpecial);
}

// 导航到上级目录
function navigateUp() {
    if (currentPath === '' || currentPath === '/' || 
        SPECIAL_NAV_PATHS.includes(currentPath)) {
        loadFiles('/');
        return;
    }
    
    const parts = currentPath.split('/').filter(p => p);
    parts.pop();
    
    const newPath = parts.length > 0 ? '/' + parts.join('/') : '/';
    loadFiles(newPath);
}

// 加载存储使用情况
function loadStorageInfo() {
    fetch('/api/storage')
        .then(response => response.json())
        .then(data => {
            document.getElementById('storage-text').textContent = `已使用: ${data.used_gb} GB / ${data.total_formatted}`;
            document.getElementById('storage-bar').style.width = `${data.percentage}%`;
            
            // 根据使用量设置颜色
            const storageBar = document.getElementById('storage-bar');
            if (data.percentage > 90) {
                storageBar.className = 'progress-bar rounded-pill bg-danger';
            } else if (data.percentage > 70) {
                storageBar.className = 'progress-bar rounded-pill bg-warning';
            } else {
                storageBar.className = 'progress-bar rounded-pill bg-success';
            }
        })
        .catch(error => {
            console.error('无法加载存储信息', error);
        });
}

// 显示消息
function showMessage(title, message) {
    alert(`${title}: ${message}`);
}

// 获取文件图标类
function getFileIconClass(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    
    // 图片类型
    if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp'].includes(ext)) {
        return 'bi-file-image';
    }
    
    // 文档类型
    if (['pdf'].includes(ext)) {
        return 'bi-file-pdf';
    }
    
    if (['doc', 'docx'].includes(ext)) {
        return 'bi-file-word';
    }
    
    if (['xls', 'xlsx'].includes(ext)) {
        return 'bi-file-excel';
    }
    
    if (['ppt', 'pptx'].includes(ext)) {
        return 'bi-file-ppt';
    }
    
    if (['txt', 'csv'].includes(ext)) {
        return 'bi-file-text';
    }
    
    if (['md'].includes(ext)) {
        return 'bi-markdown';
    }
    
    // 视频文件
    if (['mp4', 'avi', 'mov', 'wmv', 'flv', 'mkv', 'webm'].includes(ext)) {
        return 'bi-file-play';
    }
    
    // 音频文件
    if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a'].includes(ext)) {
        return 'bi-file-music';
    }
    
    // 压缩文件
    if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2'].includes(ext)) {
        return 'bi-file-zip';
    }
    
    // 代码文件
    if (['py', 'js', 'html', 'css', 'java', 'c', 'cpp', 'php', 'rb', 'go', 'json', 'xml'].includes(ext)) {
        return 'bi-file-code';
    }
    
    // 默认图标
    return 'bi-file';
}

// 格式化文件大小
function formatSize(bytes) {
    if (bytes === 0) return '0 B';
    
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    
    return (bytes / Math.pow(1024, i)).toFixed(2) + ' ' + units[i];
}
// 加载用户设置
function loadUserSettings() {
    fetch('/api/user')
        .then(handleFetchErrors)
        .then(response => response.json())
        .then(data => {
            document.getElementById('display-name').value = data.display_name || '';
            document.getElementById('email').value = data.email || '';
        })
        .catch(error => {
            if (error.message !== '未授权') {
                showMessage('错误', '加载用户信息失败');
            }
        });
}

// 保存用户设置
function saveUserSettings() {
    const displayName = document.getElementById('display-name').value;
    const email = document.getElementById('email').value;
    const currentPassword = document.getElementById('current-password').value;
    const newPassword = document.getElementById('new-password').value;
    const confirmPassword = document.getElementById('confirm-password').value;
    
    // 基本验证
    if (!email) {
        showMessage('错误', '请填写电子邮箱');
        return;
    }
    
    // 如果填写了密码字段，进行验证
    if (newPassword || confirmPassword || currentPassword) {
        if (!currentPassword) {
            showMessage('错误', '请输入当前密码');
            return;
        }
        
        if (newPassword !== confirmPassword) {
            showMessage('错误', '两次输入的新密码不匹配');
            return;
        }
    }
    
    // 构建请求数据
    const data = {
        display_name: displayName,
        email: email
    };
    
    if (newPassword && currentPassword) {
        data.password = newPassword;
        data.current_password = currentPassword;
    }
    
    // 发送请求
    fetch('/api/user/settings', {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })
    .then(handleFetchErrors)
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showMessage('成功', '用户设置已更新');
            
            // 清空密码字段
            document.getElementById('current-password').value = '';
            document.getElementById('new-password').value = '';
            document.getElementById('confirm-password').value = '';
            
            // 关闭模态框
            const modal = bootstrap.Modal.getInstance(document.getElementById('userSettingsModal'));
            modal.hide();
            
            // 刷新页面以更新用户名显示
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        }
    })
    .catch(error => {
        showMessage('错误', `保存设置失败: ${error.message}`);
    });
}

// 用户设置模态框打开时加载数据
document.getElementById('userSettingsModal').addEventListener('show.bs.modal', function() {
    loadUserSettings();
});
// 大文件断点续传类
class ChunkedUploader {
    constructor(file, options = {}) {
        this.file = file;
        this.fileName = file.name;
        this.fileSize = file.size;
        this.chunkSize = options.chunkSize || 5 * 1024 * 1024; // 默认5MB每片
        this.chunks = Math.ceil(this.fileSize / this.chunkSize);
        this.currentChunk = 0;
        this.uploadedChunks = new Set();
        this.startTime = null;
        this.uploadedBytes = 0;
        this.lastUploadedBytes = 0;
        this.lastTime = null;
        this.isPaused = false;
        this.onProgress = options.onProgress || (() => {});
        this.onComplete = options.onComplete || (() => {});
        this.onError = options.onError || (() => {});
        this.onSpeed = options.onSpeed || (() => {});
        this.path = options.path || '';
        this.fileHash = null;
        this.speedInterval = null;
    }

    // 计算文件MD5哈希
    async calculateHash() {
        return new Promise((resolve, reject) => {
            const spark = new SparkMD5.ArrayBuffer();
            const reader = new FileReader();
            const chunkSize = 2 * 1024 * 1024; // 2MB chunks for hash calculation
            let currentChunk = 0;
            const chunks = Math.ceil(this.file.size / chunkSize);

            reader.onload = (e) => {
                spark.append(e.target.result);
                currentChunk++;

                if (currentChunk < chunks) {
                    loadNext();
                } else {
                    const hash = spark.end();
                    resolve(hash);
                }
            };

            reader.onerror = reject;

            const loadNext = () => {
                const start = currentChunk * chunkSize;
                const end = Math.min(start + chunkSize, this.file.size);
                reader.readAsArrayBuffer(this.file.slice(start, end));
            };

            loadNext();
        });
    }

    // 检查断点信息
    async checkBreakpoint() {
        console.log('checkbreakpoint')
        try {
            const response = await fetch('/api/upload/check', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    fileName: this.fileName,
                    fileSize: this.fileSize,
                    fileHash: this.fileHash,
                    path: this.path
                })
            });

            if (response.ok) {
                const data = await response.json();
                if (data.uploadedChunks) {
                    this.uploadedChunks = new Set(data.uploadedChunks);
                    this.uploadedBytes = data.uploadedBytes || 0;
                    this.currentChunk = this.findNextChunk();
                }
                return data;
            }
        } catch (error) {
            console.error('检查断点失败:', error);
        }
        return null;
    }

    // 查找下一个需要上传的分片
    findNextChunk() {
        for (let i = 0; i < this.chunks; i++) {
            if (!this.uploadedChunks.has(i)) {
                return i;
            }
        }
        return this.chunks;
    }

    // 开始上传
    async start() {
        this.isPaused = false;
        this.startTime = Date.now();
        this.lastTime = this.startTime;
        
        // 计算文件哈希
        this.fileHash = await this.calculateHash();
        console.log(this.fileHash)
        // 检查断点
        await this.checkBreakpoint();
        
        // 开始速度监控
        this.startSpeedMonitor();
        
        // 开始上传分片
        await this.uploadChunks();
    }

    // 上传分片
    async uploadChunks() {
        while (this.currentChunk < this.chunks && !this.isPaused) {
            if (this.uploadedChunks.has(this.currentChunk)) {
                this.currentChunk++;
                continue;
            }

            try {
                await this.uploadChunk(this.currentChunk);
                this.uploadedChunks.add(this.currentChunk);
                this.currentChunk = this.findNextChunk();
                
                // 更新进度
                const progress = Math.min(100, (this.uploadedBytes / this.fileSize) * 100);
                this.onProgress(progress, this.uploadedBytes, this.fileSize);
                
            } catch (error) {
                console.error(`上传分片 ${this.currentChunk} 失败:`, error);
                this.onError(error);
                
                // 重试机制
                await this.delay(1000);
                continue;
            }
        }

        if (this.currentChunk >= this.chunks) {
            await this.mergeChunks();
        }
    }

    // 上传单个分片
    async uploadChunk(chunkIndex) {
        const start = chunkIndex * this.chunkSize;
        const end = Math.min(start + this.chunkSize, this.fileSize);
        const chunk = this.file.slice(start, end);
        
        const formData = new FormData();
        formData.append('chunk', chunk);
        formData.append('chunkIndex', chunkIndex);
        formData.append('chunks', this.chunks);
        formData.append('fileName', this.fileName);
        formData.append('fileHash', this.fileHash);
        formData.append('path', this.path);

        const response = await fetch('/api/upload/chunk', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`上传失败: ${response.statusText}`);
        }

        const chunkSize = end - start;
        this.uploadedBytes += chunkSize;
        
        return response.json();
    }

    // 合并分片
    async mergeChunks() {
        try {
            const response = await fetch('/api/upload/merge', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    fileName: this.fileName,
                    fileHash: this.fileHash,
                    chunks: this.chunks,
                    path: this.path,
                    fileSize: this.fileSize
                })
            });

            if (response.ok) {
                this.stopSpeedMonitor();
                this.onComplete(await response.json());
            } else {
                throw new Error('合并文件失败');
            }
        } catch (error) {
            this.onError(error);
        }
    }

    // 开始速度监控
    startSpeedMonitor() {
        this.speedInterval = setInterval(() => {
            const now = Date.now();
            const timeDiff = (now - this.lastTime) / 1000; // 秒
            const bytesDiff = this.uploadedBytes - this.lastUploadedBytes;
            const speed = bytesDiff / timeDiff; // 字节/秒
            
            this.onSpeed(this.formatSpeed(speed));
            
            this.lastTime = now;
            this.lastUploadedBytes = this.uploadedBytes;
        }, 1000);
    }

    // 停止速度监控
    stopSpeedMonitor() {
        if (this.speedInterval) {
            clearInterval(this.speedInterval);
            this.speedInterval = null;
        }
    }

    // 格式化速度显示
    formatSpeed(bytesPerSecond) {
        if (bytesPerSecond < 1024) {
            return `${bytesPerSecond.toFixed(2)} B/s`;
        } else if (bytesPerSecond < 1024 * 1024) {
            return `${(bytesPerSecond / 1024).toFixed(2)} KB/s`;
        } else {
            return `${(bytesPerSecond / (1024 * 1024)).toFixed(2)} MB/s`;
        }
    }

    // 暂停上传
    pause() {
        this.isPaused = true;
        this.stopSpeedMonitor();
    }

    // 恢复上传
    resume() {
        this.isPaused = false;
        this.startSpeedMonitor();
        this.uploadChunks();
    }

    // 延迟函数
    delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}

// 修改原有的上传函数
let currentUploader = null;

function uploadSelectedFiles() {
    const fileInput = document.getElementById('file-input');
    const files = fileInput.files;
    
    if (files.length === 0) {
        showMessage('错误', '请选择要上传的文件');
        return;
    }
    
    const progressBar = document.querySelector('.upload-progress .progress-bar');
    const statusText = document.getElementById('upload-status');
    const progressContainer = document.querySelector('.upload-progress');
    
    progressContainer.classList.remove('d-none');
    
    // 添加速度显示元素
    if (!document.getElementById('upload-speed')) {
        const speedElement = document.createElement('p');
        speedElement.id = 'upload-speed';
        speedElement.className = 'text-center text-muted';
        statusText.parentNode.insertBefore(speedElement, statusText.nextSibling);
    }
    
    // 添加暂停/恢复按钮
    if (!document.getElementById('pause-resume-btn')) {
        const pauseBtn = document.createElement('button');
        pauseBtn.id = 'pause-resume-btn';
        pauseBtn.className = 'btn btn-warning btn-sm mt-2';
        pauseBtn.textContent = '暂停';
        pauseBtn.onclick = togglePauseResume;
        statusText.parentNode.appendChild(pauseBtn);
    }
    
    uploadNextFile(Array.from(files), 0, progressBar, statusText);
}

function uploadNextFile(files, index, progressBar, statusText) {
    if (index >= files.length) {
        // 所有文件上传完成
        setTimeout(() => {
            const uploadModal = bootstrap.Modal.getInstance(document.getElementById('upload-modal'));
            uploadModal.hide();
            resetUploadModal();
            refreshAfterImport();
            loadStorageInfo();
        }, 1000);
        return;
    }
    
    const file = files[index];
    const speedElement = document.getElementById('upload-speed');
    
    statusText.textContent = `上传中: ${file.name} (${index + 1}/${files.length})`;
    progressBar.style.width = '0%';
    progressBar.classList.remove('bg-danger');
    console.log(file)
    // 判断是否为大文件（超过10MB使用分片上传）
    if (file.size > 10 * 1024 * 1024) {
        currentUploader = new ChunkedUploader(file, {
            path: getUploadPath(),
            chunkSize: 5 * 1024 * 1024, // 5MB per chunk
            onProgress: (progress, uploaded, total) => {
                progressBar.style.width = `${progress}%`;
                progressBar.textContent = `${progress.toFixed(1)}%`;
                
                const uploadedMB = (uploaded / (1024 * 1024)).toFixed(2);
                const totalMB = (total / (1024 * 1024)).toFixed(2);
                statusText.textContent = `上传中: ${file.name} (${uploadedMB}MB / ${totalMB}MB)`;
            },
            onSpeed: (speed) => {
                speedElement.textContent = `上传速度: ${speed}`;
            },
            onComplete: (data) => {
                progressBar.style.width = '100%';
                statusText.textContent = `${file.name} 上传完成`;
                speedElement.textContent = '';
                
                // 上传下一个文件
                setTimeout(() => {
                    uploadNextFile(files, index + 1, progressBar, statusText);
                }, 500);
            },
            onError: (error) => {
                progressBar.classList.add('bg-danger');
                statusText.textContent = `上传失败: ${error.message}`;
                speedElement.textContent = '';
            }
        });
        
        currentUploader.start();
    } else {
        // 小文件使用原有的上传方式
        uploadSmallFile(file, progressBar, statusText, () => {
            uploadNextFile(files, index + 1, progressBar, statusText);
        });
    }
}

// 小文件上传（保留原有逻辑）
function uploadSmallFile(file, progressBar, statusText, onComplete) {
    const formData = new FormData();
    formData.append('files[]', file);
    formData.append('path', getUploadPath());
    
    const xhr = new XMLHttpRequest();
    
    xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
            const progress = (e.loaded / e.total) * 100;
            progressBar.style.width = `${progress}%`;
            progressBar.textContent = `${progress.toFixed(1)}%`;
        }
    });
    
    xhr.addEventListener('load', () => {
        if (xhr.status === 200) {
            progressBar.style.width = '100%';
            statusText.textContent = `${file.name} 上传完成`;
            onComplete();
        } else {
            progressBar.classList.add('bg-danger');
            statusText.textContent = `上传失败: ${xhr.statusText}`;
        }
    });
    
    xhr.addEventListener('error', () => {
        progressBar.classList.add('bg-danger');
        statusText.textContent = '上传失败: 网络错误';
    });
    
    xhr.open('POST', '/api/upload');
    xhr.send(formData);
}

// 暂停/恢复功能
function togglePauseResume() {
    const btn = document.getElementById('pause-resume-btn');
    if (currentUploader) {
        if (currentUploader.isPaused) {
            currentUploader.resume();
            btn.textContent = '暂停';
        } else {
            currentUploader.pause();
            btn.textContent = '恢复';
        }
    }
}

// 重置上传模态框
function resetUploadModal() {
    document.getElementById('file-input').value = '';
    document.querySelector('.selected-files').classList.add('d-none');
    document.querySelector('.upload-progress').classList.add('d-none');
    document.getElementById('selected-files-list').innerHTML = '';
    
    const speedElement = document.getElementById('upload-speed');
    if (speedElement) {
        speedElement.remove();
    }
    
    const pauseBtn = document.getElementById('pause-resume-btn');
    if (pauseBtn) {
        pauseBtn.remove();
    }
    
    currentUploader = null;
}

/* ===========================================================================
 * 文件右键菜单 + 文件夹监听（FolderWatch）UI
 *
 * 设计：
 *   - 全局只有一个 <div id="file-context-menu"> 浮层；右键不同 file-item 时重建内部 HTML
 *     并定位到鼠标处。
 *   - 文件夹独享"管理监听"入口；这条菜单点击后弹出 #folderWatchModal，里面拉
 *     /api/folders/<id>/watches 列出已有 watch，并提供 表单 新建/编辑/测试/删除。
 *   - 与文件 CRUD 触发 webhook 的链路解耦：这里只是 watch 元数据的可视化管理。
 * ========================================================================= */
const FOLDER_WATCH_API = '/api/folder-watches';
let _fwActiveFolder = null;     // { id, name, path }
let _fwEditingId = null;        // 当前正在编辑的 watch id；null = 新建

function getFileContextMenu() {
    let menu = document.getElementById('file-context-menu');
    if (!menu) {
        // 兼容历史模板：动态补一个
        menu = document.createElement('div');
        menu.id = 'file-context-menu';
        menu.className = 'dropdown-menu shadow';
        menu.style.cssText = 'display:none;position:fixed;z-index:2080;min-width:200px;';
        document.body.appendChild(menu);
    }
    return menu;
}

function hideFileContextMenu() {
    const menu = document.getElementById('file-context-menu');
    if (menu) { menu.style.display = 'none'; menu.innerHTML = ''; }
}

function showFileContextMenu(ev, fileItem) {
    const menu = getFileContextMenu();
    const isDir = fileItem.dataset.isDir === 'true';
    const fileId = Number(fileItem.dataset.fileId);
    const filePath = fileItem.dataset.path || '';
    const file = lastRenderedFiles.find(f => f.id === fileId);
    const name = file ? file.name : filePath;

    // 构造菜单项；文件夹专属：监听管理 / 打开
    const items = [];
    if (isDir) {
        items.push({
            icon: 'bi-broadcast',
            label: '启动监听并设置远端接口...',
            handler: () => openFolderWatchModal({ id: fileId, name, path: filePath }),
        });
        items.push({
            icon: 'bi-puzzle',
            label: '插件管理...',
            handler: () => openFolderPluginModal({ id: fileId, name, path: filePath }),
        });
        items.push({
            icon: 'bi-folder2-open',
            label: '打开文件夹',
            handler: () => loadFiles(filePath),
        });
    } else {
        items.push({
            icon: 'bi-eye',
            label: '预览',
            handler: () => previewFile(filePath),
        });
        items.push({
            icon: 'bi-download',
            label: '下载',
            handler: () => { window.location.href = downloadUrlForPath(filePath); },
        });
        items.push({
            icon: 'bi-share',
            label: '分享',
            handler: () => shareFile(fileId),
        });
    }
    items.push({ divider: true });
    items.push({
        icon: 'bi-clipboard',
        label: '复制路径',
        handler: () => {
            copyTextToClipboard(filePath).then(ok => {
                if (ok) showMessage('成功', '路径已复制');
                else promptManualCopy(filePath, '请手动复制路径（Ctrl+C）：');
            });
        },
    });
    items.push({
        icon: 'bi-trash',
        label: '删除',
        danger: true,
        handler: () => {
            // 复用现有"选中即删"流程
            selectedFiles = [filePath];
            deleteSelectedFiles();
        },
    });

    menu.innerHTML = items.map(it => {
        if (it.divider) return '<div class="dropdown-divider my-1"></div>';
        const danger = it.danger ? 'text-danger' : '';
        return `<a href="#" class="dropdown-item ${danger}" data-fw-action="1">
                    <i class="bi ${it.icon} me-2"></i>${escapeHtml(it.label)}
                </a>`;
    }).join('');

    // 绑定事件
    Array.from(menu.querySelectorAll('a.dropdown-item')).forEach((a, i) => {
        a.addEventListener('click', e => {
            e.preventDefault();
            hideFileContextMenu();
            const it = items.filter(x => !x.divider)[i];
            if (it && typeof it.handler === 'function') {
                try { it.handler(); } catch (err) { console.error(err); }
            }
        });
    });

    // 边界保护：菜单溢出时贴边显示
    const padding = 4;
    menu.style.display = 'block';
    const rect = menu.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = ev.clientX, top = ev.clientY;
    if (left + rect.width + padding > vw) left = Math.max(padding, vw - rect.width - padding);
    if (top + rect.height + padding > vh) top = Math.max(padding, vh - rect.height - padding);
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
}

// 任意点击/Esc/滚动 都关闭右键菜单
document.addEventListener('click', e => {
    if (!e.target.closest('#file-context-menu')) hideFileContextMenu();
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') hideFileContextMenu();
});
window.addEventListener('scroll', hideFileContextMenu, true);
window.addEventListener('blur', hideFileContextMenu);

/* ---------------- 监听管理模态框 ---------------- */

function openFolderWatchModal(folder) {
    _fwActiveFolder = folder;
    _fwEditingId = null;
    const label = document.getElementById('fw-folder-label');
    if (label) label.textContent = `${folder.name || ''} (${folder.path || '/'})`;
    document.getElementById('fw-folder-id').value = folder.id;
    fwResetForm();
    fwLoadList();
    const modalEl = document.getElementById('folderWatchModal');
    if (!modalEl) return;
    const m = bootstrap.Modal.getOrCreateInstance(modalEl);
    m.show();
}

function fwResetForm() {
    _fwEditingId = null;
    document.getElementById('fw-form-title').textContent = '新增监听';
    document.getElementById('fw-id').value = '';
    document.getElementById('fw-url').value = '';
    document.getElementById('fw-secret').value = '';
    document.getElementById('fw-ev-created').checked = true;
    document.getElementById('fw-ev-modified').checked = true;
    document.getElementById('fw-ev-deleted').checked = true;
    document.getElementById('fw-include-subdirs').checked = true;
    document.getElementById('fw-enabled').checked = true;
}

function fwFillFormFromWatch(w) {
    _fwEditingId = w.id;
    document.getElementById('fw-form-title').textContent = `编辑监听 #${w.id}`;
    document.getElementById('fw-id').value = w.id;
    document.getElementById('fw-url').value = w.url || '';
    document.getElementById('fw-secret').value = '';   // 不回填密文
    document.getElementById('fw-secret').placeholder = w.has_secret ? '（已设置 secret，留空则保持不变）' : '留空则不签名';
    const evs = Array.isArray(w.events) && w.events.length ? w.events : ['created', 'modified', 'deleted'];
    document.getElementById('fw-ev-created').checked = evs.includes('created');
    document.getElementById('fw-ev-modified').checked = evs.includes('modified');
    document.getElementById('fw-ev-deleted').checked = evs.includes('deleted');
    document.getElementById('fw-include-subdirs').checked = !!w.include_subdirs;
    document.getElementById('fw-enabled').checked = !!w.enabled;
}

function fwCollectFormPayload() {
    const events = [];
    if (document.getElementById('fw-ev-created').checked) events.push('created');
    if (document.getElementById('fw-ev-modified').checked) events.push('modified');
    if (document.getElementById('fw-ev-deleted').checked) events.push('deleted');
    const payload = {
        url: document.getElementById('fw-url').value.trim(),
        events,
        include_subdirs: document.getElementById('fw-include-subdirs').checked,
        enabled: document.getElementById('fw-enabled').checked,
    };
    const secret = document.getElementById('fw-secret').value;
    // 编辑模式下 secret 为空字符串 → 不下发，保留原值；用户想清除 secret 可在后端 PUT 单独传 null
    if (_fwEditingId == null || secret.length > 0) {
        payload.secret = secret;
    }
    return payload;
}

function fwLoadList() {
    const folder = _fwActiveFolder;
    if (!folder) return;
    const listEl = document.getElementById('fw-list');
    listEl.innerHTML = '<div class="list-group-item text-muted text-center">加载中...</div>';
    fetch(`/api/folders/${folder.id}/watches`, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(j => {
            if (!j.success) {
                listEl.innerHTML = `<div class="list-group-item text-danger">${escapeHtml(j.error || '加载失败')}</div>`;
                return;
            }
            fwRenderList(j.watches || []);
        })
        .catch(err => {
            listEl.innerHTML = `<div class="list-group-item text-danger">加载失败: ${escapeHtml(err.message || err)}</div>`;
        });
}

function fwRenderList(watches) {
    const listEl = document.getElementById('fw-list');
    if (!watches.length) {
        listEl.innerHTML = '<div class="list-group-item text-muted text-center">尚无监听，使用下方表单新增第一个</div>';
        return;
    }
    listEl.innerHTML = watches.map(w => {
        const statusBadge = w.enabled
            ? '<span class="badge bg-success-subtle text-success border">启用</span>'
            : '<span class="badge bg-secondary-subtle text-secondary border">已停用</span>';
        const subBadge = w.include_subdirs
            ? '<span class="badge bg-info-subtle text-info border">递归子目录</span>'
            : '<span class="badge bg-light text-secondary border">仅当前目录</span>';
        const evs = (w.events || []).map(e =>
            `<span class="badge bg-primary-subtle text-primary border">${escapeHtml(e)}</span>`
        ).join(' ');
        const lastInfo = w.last_triggered_at
            ? `最近触发: ${escapeHtml(w.last_triggered_at)} · 状态码: ${w.last_status_code ?? '—'}${w.last_error ? ' · 错误: ' + escapeHtml(w.last_error) : ''}`
            : '尚未触发过';
        return `
        <div class="list-group-item">
            <div class="d-flex align-items-center mb-1">
                <code class="me-2 text-truncate" title="${escapeHtml(w.url)}" style="max-width:60%;">${escapeHtml(w.url)}</code>
                ${statusBadge}
                <div class="ms-auto btn-group btn-group-sm">
                    <button type="button" class="btn btn-outline-secondary" data-fw-act="edit" data-fw-id="${w.id}" title="编辑">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button type="button" class="btn btn-outline-primary" data-fw-act="test" data-fw-id="${w.id}" title="发一条测试">
                        <i class="bi bi-send"></i>
                    </button>
                    <button type="button" class="btn btn-outline-danger" data-fw-act="delete" data-fw-id="${w.id}" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
            <div class="small mb-1">${evs} ${subBadge}
                <span class="text-muted ms-1">触发 ${w.trigger_count || 0} 次</span>
            </div>
            <div class="small text-muted">${lastInfo}</div>
        </div>`;
    }).join('');

    Array.from(listEl.querySelectorAll('button[data-fw-act]')).forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = Number(btn.dataset.fwId);
            const act = btn.dataset.fwAct;
            if (act === 'edit') {
                const w = watches.find(x => x.id === id);
                if (w) fwFillFormFromWatch(w);
            } else if (act === 'test') {
                btn.disabled = true;
                try {
                    const r = await fetch(`${FOLDER_WATCH_API}/${id}/test`, {
                        method: 'POST', credentials: 'same-origin',
                    });
                    const j = await r.json().catch(() => ({}));
                    if (r.ok && j.success) {
                        showMessage('提示', '已调度测试请求，稍后刷新查看状态');
                        setTimeout(fwLoadList, 1200);
                    } else {
                        showMessage('错误', j.error || '测试请求失败');
                    }
                } finally {
                    btn.disabled = false;
                }
            } else if (act === 'delete') {
                if (!confirm(`确定删除监听 #${id}？`)) return;
                const r = await fetch(`${FOLDER_WATCH_API}/${id}`, {
                    method: 'DELETE', credentials: 'same-origin',
                });
                const j = await r.json().catch(() => ({}));
                if (r.ok && j.success) {
                    if (_fwEditingId === id) fwResetForm();
                    fwLoadList();
                } else {
                    showMessage('错误', j.error || '删除失败');
                }
            }
        });
    });
}

async function fwSaveForm() {
    const folder = _fwActiveFolder;
    if (!folder) return;
    const payload = fwCollectFormPayload();
    if (!payload.url) { showMessage('提示', '请填写远端接口 URL'); return; }
    let url, method;
    if (_fwEditingId) {
        url = `${FOLDER_WATCH_API}/${_fwEditingId}`;
        method = 'PUT';
    } else {
        url = FOLDER_WATCH_API;
        method = 'POST';
        payload.folder_id = folder.id;
    }
    const r = await fetch(url, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.success) {
        showMessage('错误', j.error || '保存失败');
        return;
    }
    fwResetForm();
    fwLoadList();
    showMessage('成功', '已保存监听配置');
}

// 一次性绑定模态框内静态按钮（用 hidden flag 防止重复绑定）
function fwBindStaticHandlersOnce() {
    if (window.__fwBound) return;
    window.__fwBound = true;
    const saveBtn = document.getElementById('fw-save-btn');
    const resetBtn = document.getElementById('fw-reset-btn');
    const refreshBtn = document.getElementById('fw-refresh-btn');
    if (saveBtn) saveBtn.addEventListener('click', fwSaveForm);
    if (resetBtn) resetBtn.addEventListener('click', fwResetForm);
    if (refreshBtn) refreshBtn.addEventListener('click', fwLoadList);
}

// DOMContentLoaded 之后再绑定一次（脚本可能在 DOM 已就绪后才追加到末尾）
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fwBindStaticHandlersOnce);
} else {
    fwBindStaticHandlersOnce();
}

/* ---------------- 插件管理模态框 ---------------- */

const FOLDER_PLUGIN_API = '/api/folder-plugins';
let _fpActiveFolder = null;
let _fpEditingId = null;

const FP_DEFAULT_DECLARATIVE = `{
  "actions": {
    "lookup": {
      "params": [
        {"name": "word", "required": true, "transform": "lower"}
      ],
      "match": {
        "strategy": "filename",
        "patterns": ["{word}.mp3", "{word}-us.mp3", "{word}.wav"],
        "case_insensitive": true,
        "recursive": true
      },
      "response": {
        "success": true,
        "word": "{word}",
        "file_id": "{file.id}",
        "name": "{file.name}",
        "url": "{file.url}",
        "size": "{file.size}"
      },
      "not_found": {
        "success": false,
        "message": "word not found: {word}"
      }
    }
  }
}`;

const FP_DEFAULT_PYTHON = `# 文件夹插件: 实现 invoke(action, params, ctx) 即可。
# ctx 可用: ctx.list_files() / ctx.find_file(name=..., basename=..., ext=..., glob=...)
#          ctx.find_files(...) / ctx.get_file_url(id) / ctx.log(msg) / ctx.folder_info
def invoke(action, params, ctx):
    if action == 'lookup':
        word = (params.get('word') or '').strip().lower()
        if not word:
            return {'success': False, 'error': 'missing word'}
        f = ctx.find_file(basename=word, ext='mp3') or ctx.find_file(name=word + '-us.mp3')
        if not f:
            return {'success': False, 'message': 'word not found: ' + word}
        return {
            'success': True,
            'word': word,
            'file_id': f['id'],
            'name': f['name'],
            'url': f['url'],
            'size': f['size'],
        }
    return {'success': False, 'error': 'unknown action: ' + str(action)}
`;

function openFolderPluginModal(folder) {
    _fpActiveFolder = folder;
    _fpEditingId = null;
    const label = document.getElementById('fp-folder-label');
    if (label) label.textContent = `${folder.name || ''} (${folder.path || '/'})`;
    document.getElementById('fp-folder-id').value = folder.id;
    fpResetForm();
    fpLoadList();
    const modalEl = document.getElementById('folderPluginModal');
    if (!modalEl) return;
    const m = bootstrap.Modal.getOrCreateInstance(modalEl);
    m.show();
}

function fpToggleKindFields() {
    const kind = document.getElementById('fp-kind').value;
    document.getElementById('fp-config-group').style.display = (kind === 'declarative') ? '' : 'none';
    document.getElementById('fp-code-group').style.display = (kind === 'python') ? '' : 'none';
}

function fpResetForm() {
    _fpEditingId = null;
    document.getElementById('fp-form-title').textContent = '新增插件';
    document.getElementById('fp-id').value = '';
    document.getElementById('fp-name').value = '';
    document.getElementById('fp-description').value = '';
    document.getElementById('fp-kind').value = 'declarative';
    document.getElementById('fp-config').value = FP_DEFAULT_DECLARATIVE;
    document.getElementById('fp-code').value = FP_DEFAULT_PYTHON;
    document.getElementById('fp-include-subdirs').checked = true;
    document.getElementById('fp-enabled').checked = true;
    const pubEl = document.getElementById('fp-public');
    if (pubEl) pubEl.checked = false;
    fpToggleKindFields();
    document.getElementById('fp-test-result').style.display = 'none';
    fpRenderAccess(null);
}

function fpFillFromPlugin(p) {
    _fpEditingId = p.id;
    document.getElementById('fp-form-title').textContent = `编辑插件 #${p.id} · ${p.name}`;
    document.getElementById('fp-id').value = p.id;
    document.getElementById('fp-name').value = p.name || '';
    document.getElementById('fp-description').value = p.description || '';
    document.getElementById('fp-kind').value = p.kind || 'declarative';
    if (p.config) {
        document.getElementById('fp-config').value = JSON.stringify(p.config, null, 2);
    } else if (p.kind === 'declarative') {
        document.getElementById('fp-config').value = FP_DEFAULT_DECLARATIVE;
    }
    if (p.code) {
        document.getElementById('fp-code').value = p.code;
    } else if (p.kind === 'python') {
        document.getElementById('fp-code').value = FP_DEFAULT_PYTHON;
    }
    document.getElementById('fp-include-subdirs').checked = !!p.include_subdirs;
    document.getElementById('fp-enabled').checked = !!p.enabled;
    const pubEl = document.getElementById('fp-public');
    if (pubEl) pubEl.checked = !!p.public;
    fpToggleKindFields();
    document.getElementById('fp-test-result').style.display = 'none';
    // 测试区预填示例
    document.getElementById('fp-test-action').value = 'lookup';
    document.getElementById('fp-test-params').value = '{"word": "hello"}';
    fpRenderAccess(p);
}

function fpCollect() {
    const kind = document.getElementById('fp-kind').value;
    const pubEl = document.getElementById('fp-public');
    const payload = {
        name: document.getElementById('fp-name').value.trim(),
        kind,
        description: document.getElementById('fp-description').value.trim(),
        include_subdirs: document.getElementById('fp-include-subdirs').checked,
        enabled: document.getElementById('fp-enabled').checked,
        public: pubEl ? !!pubEl.checked : false,
    };
    if (kind === 'declarative') {
        const raw = document.getElementById('fp-config').value;
        try {
            payload.config = JSON.parse(raw);
        } catch (e) {
            return { error: 'config 不是合法的 JSON: ' + e.message };
        }
        payload.code = null;
    } else {
        payload.code = document.getElementById('fp-code').value;
        payload.config = null;
    }
    return { payload };
}

/**
 * 渲染"访问示例"区块。
 *
 * 设计:
 * - 仅在已保存的插件上展示 (新增表单未保存时隐藏)
 * - public=true: 显示 /api/public/folders/<id>/plugins/<name>/invoke, 提示"无需 API Key"
 * - public=false: 显示 /api/external/folders/<id>/plugins/<name>/invoke, 提示"需要 X-API-Key"
 * - URL/curl 都做了示例 body 占位, 客户端复制后只需替换 action/params
 */
function fpRenderAccess(plugin) {
    const section = document.getElementById('fp-access-section');
    if (!section) return;
    if (!plugin || !plugin.id) {
        section.style.display = 'none';
        return;
    }
    section.style.display = '';

    const origin = window.location.origin;
    const folderId = plugin.folder_id;
    const name = plugin.name;
    const isPublic = !!plugin.public;

    const path = isPublic
        ? `/api/public/folders/${folderId}/plugins/${encodeURIComponent(name)}/invoke`
        : `/api/external/folders/${folderId}/plugins/${encodeURIComponent(name)}/invoke`;
    const fullUrl = origin + path;
    document.getElementById('fp-access-url').value = fullUrl;

    const sampleBody = JSON.stringify(
        { action: 'lookup', params: { word: 'hello' } }, null, 2,
    );
    const curl = isPublic
        ? `curl -X POST '${fullUrl}' \\\n  -H 'Content-Type: application/json' \\\n  -d '${sampleBody.replace(/\n/g, '')}'`
        : `curl -X POST '${fullUrl}' \\\n  -H 'X-API-Key: <你的API Key>' \\\n  -H 'Content-Type: application/json' \\\n  -d '${sampleBody.replace(/\n/g, '')}'`;
    document.getElementById('fp-access-curl').value = curl;

    const badge = document.getElementById('fp-access-mode-badge');
    if (badge) {
        badge.textContent = isPublic ? '公开 · 无需 API Key' : '需 API Key';
        badge.classList.toggle('bg-success', isPublic);
        badge.classList.toggle('bg-secondary', !isPublic);
    }
    const tip = document.getElementById('fp-access-tip');
    if (tip) {
        tip.innerHTML = isPublic
            ? '插件已开启 <strong>公开访问</strong>: 任意客户端 / 浏览器都能 POST 该 URL，响应里的 <code>url</code> 字段已自带 HMAC 签名, 直接 GET 即可下载文件 — 关闭开关或重置 API Key 后所有旧链接立即失效。'
            : '外部客户端调用需要在请求头里带 <code>X-API-Key: &lt;你的 API Key&gt;</code>; 插件响应里的 <code>url</code> 也需要 <code>X-API-Key</code> 才能下载。如需让浏览器直接访问，请开启上方的 "公开访问" 开关。';
    }
}

function fpLoadList() {
    const folder = _fpActiveFolder;
    if (!folder) return;
    const listEl = document.getElementById('fp-list');
    listEl.innerHTML = '<div class="list-group-item text-muted text-center">加载中...</div>';
    fetch(`/api/folders/${folder.id}/plugins`, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(j => {
            if (!j.success) {
                listEl.innerHTML = `<div class="list-group-item text-danger">${escapeHtml(j.error || '加载失败')}</div>`;
                return;
            }
            fpRenderList(j.plugins || []);
        })
        .catch(err => {
            listEl.innerHTML = `<div class="list-group-item text-danger">加载失败: ${escapeHtml(err.message || err)}</div>`;
        });
}

function fpRenderList(plugins) {
    const listEl = document.getElementById('fp-list');
    if (!plugins.length) {
        listEl.innerHTML = '<div class="list-group-item text-muted text-center">尚无插件，使用下方表单新增第一个</div>';
        return;
    }
    listEl.innerHTML = plugins.map(p => {
        const enabledBadge = p.enabled
            ? '<span class="badge bg-success-subtle text-success border">启用</span>'
            : '<span class="badge bg-secondary-subtle text-secondary border">已停用</span>';
        const kindBadge = p.kind === 'python'
            ? '<span class="badge bg-warning-subtle text-warning border">python</span>'
            : '<span class="badge bg-info-subtle text-info border">declarative</span>';
        const subBadge = p.include_subdirs
            ? '<span class="badge bg-light text-secondary border">递归</span>'
            : '<span class="badge bg-light text-secondary border">不递归</span>';
        const publicBadge = p.public
            ? '<span class="badge bg-warning-subtle text-warning border" title="无需 API Key 即可调用 (/api/public/...)"><i class="bi bi-globe"></i> 公开</span>'
            : '';
        const lastInfo = p.last_invoked_at
            ? `最近调用: ${escapeHtml(p.last_invoked_at)} · 状态: ${escapeHtml(p.last_status || '-')}${p.last_error ? ' · ' + escapeHtml(p.last_error) : ''}`
            : '尚未调用过';
        const desc = p.description ? `<div class="small text-muted">${escapeHtml(p.description)}</div>` : '';
        return `
        <div class="list-group-item">
            <div class="d-flex align-items-center mb-1">
                <strong class="me-2">${escapeHtml(p.name)}</strong>
                ${kindBadge}
                ${enabledBadge}
                ${subBadge}
                ${publicBadge}
                <span class="text-muted small ms-2">调用 ${p.invoke_count || 0} 次</span>
                <div class="ms-auto btn-group btn-group-sm">
                    <button type="button" class="btn btn-outline-secondary" data-fp-act="edit" data-fp-id="${p.id}" title="编辑">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button type="button" class="btn btn-outline-primary" data-fp-act="toggle" data-fp-id="${p.id}" title="${p.enabled ? '停用' : '启用'}">
                        <i class="bi ${p.enabled ? 'bi-pause-circle' : 'bi-play-circle'}"></i>
                    </button>
                    <button type="button" class="btn btn-outline-danger" data-fp-act="delete" data-fp-id="${p.id}" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
            ${desc}
            <div class="small text-muted">${lastInfo}</div>
        </div>`;
    }).join('');

    Array.from(listEl.querySelectorAll('button[data-fp-act]')).forEach(btn => {
        btn.addEventListener('click', async () => {
            const id = Number(btn.dataset.fpId);
            const act = btn.dataset.fpAct;
            const p = plugins.find(x => x.id === id);
            if (!p) return;
            if (act === 'edit') {
                // 拉详情(含 config/code)
                const r = await fetch(`${FOLDER_PLUGIN_API}/${id}`, { credentials: 'same-origin' });
                const j = await r.json().catch(() => ({}));
                if (j.success) fpFillFromPlugin(j.plugin);
                else showMessage('错误', j.error || '加载失败');
            } else if (act === 'toggle') {
                const r = await fetch(`${FOLDER_PLUGIN_API}/${id}`, {
                    method: 'PATCH', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: !p.enabled }),
                });
                const j = await r.json().catch(() => ({}));
                if (j.success) fpLoadList();
                else showMessage('错误', j.error || '更新失败');
            } else if (act === 'delete') {
                if (!confirm(`确定删除插件 ${p.name}？`)) return;
                const r = await fetch(`${FOLDER_PLUGIN_API}/${id}`, {
                    method: 'DELETE', credentials: 'same-origin',
                });
                const j = await r.json().catch(() => ({}));
                if (j.success) {
                    if (_fpEditingId === id) fpResetForm();
                    fpLoadList();
                } else {
                    showMessage('错误', j.error || '删除失败');
                }
            }
        });
    });
}

async function fpSaveForm() {
    const folder = _fpActiveFolder;
    if (!folder) return;
    const { payload, error } = fpCollect();
    if (error) { showMessage('错误', error); return; }
    if (!payload.name) { showMessage('提示', '请填写插件名'); return; }

    let url, method;
    if (_fpEditingId) {
        url = `${FOLDER_PLUGIN_API}/${_fpEditingId}`;
        method = 'PUT';
    } else {
        url = `/api/folders/${folder.id}/plugins`;
        method = 'POST';
    }
    const r = await fetch(url, {
        method, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.success) {
        showMessage('错误', j.error || '保存失败');
        return;
    }
    showMessage('成功', _fpEditingId ? '已更新插件' : '已创建插件');
    // 保存后保持当前编辑态, 切到编辑模式以便用户继续测试
    fpFillFromPlugin(j.plugin);
    _fpEditingId = j.plugin.id;
    fpLoadList();
}

async function fpDoInvoke() {
    if (!_fpEditingId) {
        showMessage('提示', '请先保存当前编辑的插件，然后再调用');
        return;
    }
    const action = document.getElementById('fp-test-action').value.trim();
    if (!action) { showMessage('提示', '请填写 action'); return; }
    const paramsRaw = document.getElementById('fp-test-params').value.trim();
    let params = {};
    if (paramsRaw) {
        try { params = JSON.parse(paramsRaw); }
        catch (e) { showMessage('错误', 'params 不是合法 JSON: ' + e.message); return; }
    }
    const r = await fetch(`${FOLDER_PLUGIN_API}/${_fpEditingId}/invoke`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, params }),
    });
    const text = await r.text();
    let pretty;
    try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { pretty = text; }
    const out = document.getElementById('fp-test-result');
    out.style.display = '';
    out.textContent = `HTTP ${r.status}\n${pretty}`;
}

function fpBindStaticHandlersOnce() {
    if (window.__fpBound) return;
    window.__fpBound = true;
    const saveBtn = document.getElementById('fp-save-btn');
    const resetBtn = document.getElementById('fp-reset-btn');
    const refreshBtn = document.getElementById('fp-refresh-btn');
    const kindSel = document.getElementById('fp-kind');
    const testBtn = document.getElementById('fp-test-btn');
    if (saveBtn) saveBtn.addEventListener('click', fpSaveForm);
    if (resetBtn) resetBtn.addEventListener('click', fpResetForm);
    if (refreshBtn) refreshBtn.addEventListener('click', fpLoadList);
    if (kindSel) kindSel.addEventListener('change', fpToggleKindFields);
    if (testBtn) testBtn.addEventListener('click', fpDoInvoke);

    // 访问示例区"复制"按钮: 用事件委托避免每次重渲染都要重绑
    const modalEl = document.getElementById('folderPluginModal');
    if (modalEl) {
        modalEl.addEventListener('click', async (e) => {
            const btn = e.target.closest('[data-fp-copy]');
            if (!btn) return;
            const targetId = btn.getAttribute('data-fp-copy');
            const targetEl = document.getElementById(targetId);
            if (!targetEl) return;
            const text = targetEl.value;
            try {
                await navigator.clipboard.writeText(text);
                const icon = btn.querySelector('i');
                if (icon) {
                    icon.className = 'bi bi-check2';
                    setTimeout(() => { icon.className = 'bi bi-clipboard'; }, 1200);
                }
            } catch (err) {
                // fallback: 选中文本让用户手动 Ctrl+C
                targetEl.focus();
                targetEl.select();
                showMessage('提示', '剪贴板权限被拒，已选中文本，请手动复制 (Ctrl+C)');
            }
        });
    }

    // 切换 public switch 即时刷新示例区: 让用户实时看到 URL 形态变化
    const pubEl = document.getElementById('fp-public');
    if (pubEl) {
        pubEl.addEventListener('change', () => {
            if (!_fpEditingId) return;
            // 用当前表单字段构造"伪 plugin" 重新渲染示例
            fpRenderAccess({
                id: _fpEditingId,
                folder_id: _fpActiveFolder && _fpActiveFolder.id,
                name: document.getElementById('fp-name').value.trim(),
                public: pubEl.checked,
            });
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fpBindStaticHandlersOnce);
} else {
    fpBindStaticHandlersOnce();
}

/* ============================================================
 * 文件页面动态刷新 (Live Refresh)
 *
 * 设计目标:
 * - 后台周期性轮询当前视图的接口, 保持页面信息最新
 * - 不打断用户操作: tab 不可见 / 有 modal 打开 / 焦点在输入框 / 最近 3s 有点击 → 暂停
 * - 签名比对: 新旧文件列表如果关键字段无变化, 不触发重渲染
 * - 路径变化 / 页面回到前台 → 立刻刷一次
 * - 用户主动操作(上传/删除/重命名等)后无需手动调用, 下一轮自然会拉到新数据
 * - 顶栏有可视化状态 + 手动刷新按钮 + 自动刷新开关
 * ============================================================ */

const LIVE_REFRESH_INTERVAL_MS = 10000;   // 每 10s 一次, 平衡实时性和负载
const LIVE_REFRESH_SNOOZE_MS  = 3000;     // 用户操作后暂停 3s

let _liveRefreshTimer = null;
let _liveRefreshLastSig = null;
let _liveRefreshSnoozedUntil = 0;
let _liveRefreshLastSuccessAt = 0;
let _liveRefreshInFlight = false;

function _liveRefreshSignature(path, files) {
    // 关键字段: id / size / modified / child_count / public 状态 / 版本号
    return path + '|' + files.map(f =>
        `${f.id}:${f.size}:${f.modified}:${f.child_count || 0}:${f.is_public ? 1 : 0}:${f.version_str || ''}`
    ).join('||');
}

function _liveRefreshUrlForCurrentPath() {
    const p = currentPath;
    if (p === 'shared') return '/api/shared-files';
    // 类型视图: 走 /api/files/type/<type>, 已支持
    if (TYPE_VIEW_PATHS.includes(p)) return `/api/files/type/${p}`;
    // recent / search 视图后端无对应轮询接口(参数语义复杂或未实现) —
    // 跳过轮询, 用户切换路径或显式点「刷新」按钮才会重拉
    if (p === 'recent' || p === 'search') return null;
    // 普通路径
    const apiPath = (p && p !== '/') ? p.replace(/^\/+/, '') : '';
    return `/api/files/${apiPath}`;
}

function _isLiveRefreshAllowed() {
    if (document.hidden) return false;
    if (Date.now() < _liveRefreshSnoozedUntil) return false;
    const toggle = document.getElementById('live-refresh-toggle');
    if (toggle && !toggle.checked) return false;
    // 任意 modal 打开时暂停 — 避免破坏编辑表单
    if (document.querySelector('.modal.show')) return false;
    // 焦点在输入框 — 避免重渲染清空输入
    const el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) {
        return false;
    }
    // 文件被多选时不暂停: renderFiles 会自动恢复选中态
    return true;
}

function _setLiveRefreshStatus(text, kind) {
    const el = document.getElementById('live-refresh-status');
    if (!el) return;
    el.textContent = text;
    el.classList.remove('text-muted', 'text-success', 'text-danger', 'text-warning');
    el.classList.add(
        kind === 'ok' ? 'text-success' :
        kind === 'err' ? 'text-danger' :
        kind === 'wait' ? 'text-warning' : 'text-muted'
    );
}

function _liveRefreshTick(force) {
    if (_liveRefreshInFlight) return;
    if (!force && !_isLiveRefreshAllowed()) return;
    const url = _liveRefreshUrlForCurrentPath();
    if (!url) return;

    _liveRefreshInFlight = true;
    fetch(url, { credentials: 'same-origin' })
        .then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
        })
        .then(data => {
            const files = Array.isArray(data && data.files) ? data.files : [];
            const sig = _liveRefreshSignature(currentPath, files);
            if (sig !== _liveRefreshLastSig) {
                _liveRefreshLastSig = sig;
                // 只在用户允许时才更新 DOM (二次检查避免在请求飞行中用户开始操作)
                if (_isLiveRefreshAllowed() || force) {
                    renderFiles(files);
                }
            }
            _liveRefreshLastSuccessAt = Date.now();
            _setLiveRefreshStatus('刚刚刷新', 'ok');
        })
        .catch(err => {
            _setLiveRefreshStatus('刷新失败', 'err');
            console.warn('[live-refresh]', err);
        })
        .finally(() => { _liveRefreshInFlight = false; });
}

function _liveRefreshUpdateStatusText() {
    if (_liveRefreshInFlight) {
        _setLiveRefreshStatus('刷新中…', 'wait');
        return;
    }
    if (!_liveRefreshLastSuccessAt) {
        _setLiveRefreshStatus('就绪', 'mute');
        return;
    }
    const sec = Math.floor((Date.now() - _liveRefreshLastSuccessAt) / 1000);
    _setLiveRefreshStatus(sec < 5 ? '刚刚刷新' : `${sec} 秒前`, 'mute');
}

let _liveRefreshLastFetchAt = 0;
// 1s 跳一次, 内部按 LIVE_REFRESH_INTERVAL_MS 节流实际 fetch; 状态文本每秒更新
function _liveRefreshTickThrottled() {
    const now = Date.now();
    if (now - _liveRefreshLastFetchAt >= LIVE_REFRESH_INTERVAL_MS) {
        _liveRefreshLastFetchAt = now;
        _liveRefreshTick(false);
    }
    _liveRefreshUpdateStatusText();
}

function startLiveRefresh() {
    stopLiveRefresh();
    _liveRefreshTimer = setInterval(_liveRefreshTickThrottled, 1000);
}

function stopLiveRefresh() {
    if (_liveRefreshTimer) {
        clearInterval(_liveRefreshTimer);
        _liveRefreshTimer = null;
    }
}

function snoozeLiveRefresh(ms) {
    _liveRefreshSnoozedUntil = Date.now() + (ms || LIVE_REFRESH_SNOOZE_MS);
}

// 用户活动时短暂 snooze: 减少打断
['click', 'contextmenu', 'keydown', 'wheel'].forEach(ev => {
    document.addEventListener(ev, () => snoozeLiveRefresh(LIVE_REFRESH_SNOOZE_MS), true);
});

// tab 切换: 隐藏时停轮询, 回到前台立即刷一次
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        stopLiveRefresh();
    } else {
        _liveRefreshTick(true);
        startLiveRefresh();
    }
});

function _bindLiveRefreshControlsOnce() {
    if (window.__lrBound) return;
    window.__lrBound = true;
    const toggle = document.getElementById('live-refresh-toggle');
    const nowBtn = document.getElementById('live-refresh-now');
    if (toggle) {
        toggle.addEventListener('change', () => {
            if (toggle.checked) {
                _liveRefreshTick(true);
                startLiveRefresh();
            } else {
                stopLiveRefresh();
                _setLiveRefreshStatus('已停用', 'mute');
            }
        });
    }
    if (nowBtn) {
        nowBtn.addEventListener('click', () => {
            _liveRefreshLastFetchAt = Date.now();
            _liveRefreshTick(true);
        });
    }
    startLiveRefresh();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _bindLiveRefreshControlsOnce);
} else {
    _bindLiveRefreshControlsOnce();
}


// ============================================================================
// MusicFree 分享配置 (顶部按钮 + modal)
// ----------------------------------------------------------------------------
// 流程: 点按钮 → 拉 /api/music-share/config + /folders → 渲染勾选清单 →
//      用户改完保存 → PUT /config 一次性提交 → 同步刷新顶部 badge
// ----------------------------------------------------------------------------

const MS_API = {
    config: '/api/music-share/config',
    folders: '/api/music-share/folders',
};

let _msState = {
    enabled: false,
    folders: [], // 每项: { id, name, path, audio_count, shared, _selected }
    loaded: false,
};

function _msUpdateBadge() {
    const badge = document.getElementById('musicfree-share-badge');
    if (!badge) return;
    const cnt = _msState.folders.filter(f => f.shared).length;
    if (_msState.enabled && cnt > 0) {
        badge.textContent = String(cnt);
        badge.classList.remove('d-none', 'text-bg-secondary');
        badge.classList.add('text-bg-success');
    } else if (_msState.enabled) {
        badge.textContent = '开';
        badge.classList.remove('d-none', 'text-bg-secondary');
        badge.classList.add('text-bg-warning');
    } else {
        badge.classList.add('d-none');
    }
}

async function _msFetchInitialBadge() {
    // 页面加载后静默拉一次配置, 把 badge 刷上; 不弹 modal, 不阻塞渲染
    try {
        const r = await fetch(MS_API.config, { credentials: 'same-origin' });
        if (!r.ok) return;
        const cfg = await r.json();
        _msState.enabled = !!cfg.enabled;
        // 不拉 folders 全表, 只用 shared_count 算 badge
        const cnt = Number(cfg.shared_count || 0);
        const badge = document.getElementById('musicfree-share-badge');
        if (badge) {
            if (_msState.enabled && cnt > 0) {
                badge.textContent = String(cnt);
                badge.classList.remove('d-none', 'text-bg-secondary');
                badge.classList.add('text-bg-success');
            } else if (_msState.enabled) {
                badge.textContent = '开';
                badge.classList.remove('d-none', 'text-bg-secondary');
                badge.classList.add('text-bg-warning');
            }
        }
    } catch (_) { /* ignore */ }
}

async function _msLoadConfigAndFolders() {
    const cfgResp = await fetch(MS_API.config, { credentials: 'same-origin' });
    if (!cfgResp.ok) throw new Error('加载配置失败: ' + cfgResp.status);
    const cfg = await cfgResp.json();
    _msState.enabled = !!cfg.enabled;
    const sharedSet = new Set(cfg.shared_folder_ids || []);

    const foldersResp = await fetch(MS_API.folders, { credentials: 'same-origin' });
    if (!foldersResp.ok) throw new Error('加载目录列表失败: ' + foldersResp.status);
    const fj = await foldersResp.json();
    _msState.folders = (fj.folders || []).map(f => ({
        ...f,
        shared: sharedSet.has(f.id),  // 以 cfg 为准, 防止 list_audio_folders 返回的 shared 字段过期
        _selected: sharedSet.has(f.id),
    }));
    _msState.loaded = true;
}

function _msRenderModal() {
    const switchEl = document.getElementById('ms-enabled-switch');
    if (switchEl) switchEl.checked = !!_msState.enabled;

    const list = document.getElementById('ms-folders-list');
    if (!list) return;
    if (!_msState.folders.length) {
        list.innerHTML = `<div class="text-muted text-center py-4">
            没有发现任何含音频文件的目录。先用文件管理上传一些 mp3/wav/flac 等再来配置。
        </div>`;
    } else {
        list.innerHTML = _msState.folders.map((f) => `
            <label class="list-group-item d-flex align-items-center gap-2">
                <input type="checkbox" class="form-check-input m-0" data-ms-folder-id="${f.id}"
                       ${f._selected ? 'checked' : ''}>
                <span class="flex-grow-1">
                    <span class="fw-semibold">${_escapeHtml(f.name)}</span>
                    <span class="text-muted small ms-1">(${f.audio_count} 首)</span>
                </span>
                <code class="small text-muted">${_escapeHtml(f.path || '')}</code>
            </label>
        `).join('');
        list.querySelectorAll('input[data-ms-folder-id]').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const id = Number(e.target.dataset.msFolderId);
                const f = _msState.folders.find(x => x.id === id);
                if (f) f._selected = e.target.checked;
                _msUpdateSummary();
            });
        });
    }
    _msUpdateSummary();

    const baseUrlEl = document.getElementById('ms-base-url');
    if (baseUrlEl) baseUrlEl.textContent = location.origin;
    const pluginUrlEl = document.getElementById('ms-plugin-url');
    if (pluginUrlEl) pluginUrlEl.textContent = location.origin + '/static/musicfree/file-manager.js';
}

function _escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
}

function _msUpdateSummary() {
    const summary = document.getElementById('ms-summary');
    if (!summary) return;
    const total = _msState.folders.length;
    const selected = _msState.folders.filter(f => f._selected).length;
    const enabled = !!document.getElementById('ms-enabled-switch')?.checked;
    summary.textContent =
        `${enabled ? '已启用' : '已禁用'} · 已勾选 ${selected}/${total} 个目录`;
    summary.className = 'me-auto small ' + (enabled ? 'text-success' : 'text-muted');
    const statusText = document.getElementById('ms-status-text');
    if (statusText) {
        statusText.textContent = enabled
            ? `共 ${selected} 个目录会被 MusicFree 看到`
            : '总开关关闭, 即便勾选也不会被分享';
    }
}

async function _msSave() {
    const switchEl = document.getElementById('ms-enabled-switch');
    const enabled = !!(switchEl && switchEl.checked);
    const ids = _msState.folders.filter(f => f._selected).map(f => f.id);
    const btn = document.getElementById('ms-save-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>保存中...'; }
    try {
        const r = await fetch(MS_API.config, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled, shared_folder_ids: ids }),
        });
        if (!r.ok) {
            const j = await r.json().catch(() => ({}));
            throw new Error(j.error || ('HTTP ' + r.status));
        }
        const j = await r.json();
        _msState.enabled = !!j.enabled;
        // 同步状态后, 把 folder 的 shared 标志刷新
        const okSet = new Set(j.shared_folder_ids || []);
        _msState.folders.forEach(f => { f.shared = okSet.has(f.id); f._selected = f.shared; });
        _msUpdateBadge();
        if (typeof showToast === 'function') {
            showToast('MusicFree 分享配置已保存', 'success');
        }
        const modal = bootstrap.Modal.getInstance(document.getElementById('musicfreeShareModal'));
        if (modal) modal.hide();
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast('保存失败: ' + e.message, 'error');
        } else {
            alert('保存失败: ' + e.message);
        }
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-save me-1"></i>保存'; }
    }
}

async function _msOpenModal() {
    const modalEl = document.getElementById('musicfreeShareModal');
    if (!modalEl) return;
    const list = document.getElementById('ms-folders-list');
    if (list) list.innerHTML = '<div class="text-muted text-center py-3">加载中…</div>';
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
    try {
        await _msLoadConfigAndFolders();
        _msRenderModal();
    } catch (e) {
        if (list) list.innerHTML = `<div class="text-danger text-center py-3">加载失败: ${_escapeHtml(e.message)}</div>`;
    }
}

function _bindMusicShareOnce() {
    const btn = document.getElementById('musicfree-share-btn');
    if (btn && !btn.dataset.bound) {
        btn.dataset.bound = '1';
        btn.addEventListener('click', _msOpenModal);
    }
    const switchEl = document.getElementById('ms-enabled-switch');
    if (switchEl && !switchEl.dataset.bound) {
        switchEl.dataset.bound = '1';
        switchEl.addEventListener('change', _msUpdateSummary);
    }
    const selAll = document.getElementById('ms-select-all');
    if (selAll && !selAll.dataset.bound) {
        selAll.dataset.bound = '1';
        selAll.addEventListener('click', () => {
            _msState.folders.forEach(f => f._selected = true);
            document.querySelectorAll('#ms-folders-list input[data-ms-folder-id]')
                .forEach(cb => cb.checked = true);
            _msUpdateSummary();
        });
    }
    const selNone = document.getElementById('ms-select-none');
    if (selNone && !selNone.dataset.bound) {
        selNone.dataset.bound = '1';
        selNone.addEventListener('click', () => {
            _msState.folders.forEach(f => f._selected = false);
            document.querySelectorAll('#ms-folders-list input[data-ms-folder-id]')
                .forEach(cb => cb.checked = false);
            _msUpdateSummary();
        });
    }
    const refresh = document.getElementById('ms-refresh-folders');
    if (refresh && !refresh.dataset.bound) {
        refresh.dataset.bound = '1';
        refresh.addEventListener('click', _msOpenModal);
    }
    const save = document.getElementById('ms-save-btn');
    if (save && !save.dataset.bound) {
        save.dataset.bound = '1';
        save.addEventListener('click', _msSave);
    }
    // 复制按钮
    document.querySelectorAll('[data-ms-copy]').forEach(b => {
        if (b.dataset.bound) return;
        b.dataset.bound = '1';
        b.addEventListener('click', () => {
            const sel = b.dataset.msCopy;
            const el = document.querySelector(sel);
            if (!el) return;
            navigator.clipboard.writeText(el.textContent || '')
                .then(() => {
                    const orig = b.textContent;
                    b.textContent = '已复制';
                    setTimeout(() => { b.textContent = orig; }, 1200);
                })
                .catch(() => {});
        });
    });
    // 顶部 badge 首次同步
    _msFetchInitialBadge();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _bindMusicShareOnce);
} else {
    _bindMusicShareOnce();
}