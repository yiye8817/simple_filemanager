// 全局变量
let currentPath = '';
let selectedFiles = [];
let currentViewMode = 'grid'; // 'grid' 或 'list'

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // 初始化Bootstrap组件
    initializeBootstrapComponents();
    
    // 加载存储使用情况
    loadStorageInfo();
    
    // 初始加载全部文件
    loadFiles('/');
    
    // 绑定事件处理
    bindEventHandlers();
      // 加载用户API密钥
      getUserApiKey();
    // 初始化拖放上传
    initializeDragAndDrop();
});

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
        // 隐藏空间功能可以根据需求实现
        showMessage('警告', '隐藏空间功能尚未实现');
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
        // 如果点击的不是文件项或其子元素，也不是操作按钮
        if (!event.target.closest('.file-item') && 
            !event.target.closest('.action-buttons') && 
            !event.target.closest('.modal')) {
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
function uploadSelectedFiles() {
    const fileInput = document.getElementById('file-input');
    const files = fileInput.files;
    
    if (files.length === 0) {
        showMessage('错误', '请选择要上传的文件');
        return;
    }
    
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append('files[]', files[i]);
    }
    formData.append('path', currentPath);
    
    const progressBar = document.querySelector('.upload-progress .progress-bar');
    const statusText = document.getElementById('upload-status');
    const progressContainer = document.querySelector('.upload-progress');
    
    progressContainer.classList.remove('d-none');
    progressBar.style.width = '0%';
    statusText.textContent = '上传中...';
    
    fetch('/api/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('上传失败');
        }
        return response.json();
    })
    .then(data => {
        progressBar.style.width = '100%';
        statusText.textContent = '上传完成！';
        
        setTimeout(() => {
            const uploadModal = bootstrap.Modal.getInstance(document.getElementById('upload-modal'));
            uploadModal.hide();
            resetUploadModal();
            
            // 刷新文件列表
            loadFiles(currentPath);
            
            // 更新存储信息
            loadStorageInfo();
        }, 1000);
    })
    .catch(error => {
        progressBar.classList.add('bg-danger');
        statusText.textContent = `上传失败: ${error.message}`;
    });
}

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
            path: currentPath
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
        loadFiles(currentPath);
    })
    .catch(error => {
        showMessage('错误', error.message);
    });
}

// 加载文件列表
function loadFiles(path) {
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
            updatePathNavigation(type, true);
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
// 获取当前用户的API密钥
function getUserApiKey() {
    fetch('/api/user')
        .then(handleFetchErrors)
        .then(response => response.json())
        .then(data => {
            const apiKeyElement = document.getElementById('user-api-key');
            if (apiKeyElement && data.api_key) {
                apiKeyElement.textContent = data.api_key;
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
    
    fetch('/api/auth/api-key/regenerate', {
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
            apiKeyElement.textContent = data.api_key;
            showMessage('成功', 'API密钥已重新生成');
        }
    })
    .catch(error => {
        showMessage('错误', `生成API密钥失败: ${error.message}`);
    });
}

// 复制API密钥到剪贴板
function copyApiKey() {
    const apiKeyElement = document.getElementById('user-api-key');
    if (apiKeyElement) {
        const text = apiKeyElement.textContent;
        navigator.clipboard.writeText(text).then(() => {
            showMessage('成功', 'API密钥已复制到剪贴板');
        })
        .catch(err => {
            showMessage('错误', '复制失败');
        });
    }
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
            } else if (currentPath.startsWith('search')) {
                const query = document.getElementById('search-input').value.trim();
                searchFiles(query);
            } else if (['images', 'documents', 'videos', 'audio', 'archives', 'code', 'others'].includes(currentPath)) {
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
        input.select();
        navigator.clipboard.writeText(input.value).then(() => {
            this.textContent = '已复制!';
            setTimeout(() => {
                this.textContent = '复制';
            }, 2000);
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
            } else if (currentPath.startsWith('search')) {
                const query = document.getElementById('search-input').value.trim();
                searchFiles(query);
            } else if (['images', 'documents', 'videos', 'audio', 'archives', 'code', 'others'].includes(currentPath)) {
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
    const gridContainer = document.getElementById('files-grid');
    const listBody = document.getElementById('files-list-body');
    const emptyFolder = document.getElementById('empty-folder');
    
    gridContainer.innerHTML = '';
    listBody.innerHTML = '';
    
    if (files.length === 0) {
        emptyFolder.classList.remove('d-none');
    } else {
        emptyFolder.classList.add('d-none');
        
        // 渲染网格视图,在文件项中添加分享状态和操作
        files.forEach(file => {
            const col = document.createElement('div');
            col.className = 'col-6 col-sm-4 col-md-3 col-lg-2';
            
            let fileSize = file.is_dir ? `${file.size_formatted}` : file.size_formatted;
            
            // 添加适当的类和更好的卡片布局
            col.innerHTML = `
                <div class="file-item card h-100" data-path="${file.path}" data-is-dir="${file.is_dir}" tabindex="0">
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
            // 添加分享状态图标
        if (file.is_public) {
            const shareIcon = document.createElement('div');
            shareIcon.className = 'position-absolute top-0 start-0 p-2';
            shareIcon.innerHTML = '<i class="bi bi-share-fill text-success"></i>';
            col.querySelector('.file-item').appendChild(shareIcon);
        }
        
        // 在文件卡片的操作菜单中添加分享选项
        const cardBody = col.querySelector('.card-body');
        const actionsMenu = document.createElement('div');
        actionsMenu.className = 'file-actions dropdown';
        actionsMenu.innerHTML = `
            <button class="btn btn-sm btn-link dropdown-toggle position-absolute top-0 end-0" type="button" data-bs-toggle="dropdown">
                <i class="bi bi-three-dots-vertical"></i>
            </button>
            <ul class="dropdown-menu">
                <li><a class="dropdown-item" href="#" onclick="previewFile('${file.path}')"><i class="bi bi-eye me-2"></i>预览</a></li>
                <li><a class="dropdown-item" href="#" onclick="downloadSelectedFiles()"><i class="bi bi-download me-2"></i>下载</a></li>
                ${file.is_public ? 
                    `<li><a class="dropdown-item" href="#" onclick="unshareFile(${file.id})"><i class="bi bi-x-circle me-2"></i>取消分享</a></li>` : 
                    `<li><a class="dropdown-item" href="#" onclick="shareFile(${file.id})"><i class="bi bi-share me-2"></i>分享</a></li>`
                }
                <li><hr class="dropdown-divider"></li>
                <li><a class="dropdown-item text-danger" href="#" onclick="deleteSelectedFiles()"><i class="bi bi-trash me-2"></i>删除</a></li>
            </ul>
        `;
        cardBody.appendChild(actionsMenu);
            gridContainer.appendChild(col);
            
            // 添加点击事件
            const fileItem = col.querySelector('.file-item');
            addFileItemEvents(fileItem);
        });
        
        // 渲染列表视图
        files.forEach(file => {
            const row = document.createElement('tr');
            row.className = 'file-item';
            row.setAttribute('data-path', file.path);
            row.setAttribute('data-is-dir', file.is_dir);
            row.setAttribute('tabindex', '0'); // 使项目可以获得焦点
            
            row.innerHTML = `
                <td><input type="checkbox" class="form-check-input file-checkbox"></td>
                <td>
                    <i class="bi ${file.icon} me-2"></i>
                    <span title="${file.name}">${file.name}</span>
                </td>
                <td>${file.size_formatted}</td>
                <td>${file.type}</td>
                <td>${file.modified}</td>
            `;
            
            listBody.appendChild(row);
            
            // 添加点击事件
            addFileItemEvents(row);
        });
    }
}

// 为文件项添加事件
function addFileItemEvents(fileItem) {
    // 单击选择
    fileItem.addEventListener('click', event => {
        // 防止复选框和双击处理冲突
        if (event.target.classList.contains('form-check-input')) {
            return;
        }
        
        // 移除所有项目的焦点状态
        document.querySelectorAll('.file-item.focused').forEach(item => {
            item.classList.remove('focused');
        });
        
        // 添加焦点状态到当前项目
        fileItem.classList.add('focused');
        
        // 按住Ctrl键可以多选
        if (!event.ctrlKey && !event.metaKey) {
            document.querySelectorAll('.file-item').forEach(item => {
                item.classList.remove('selected');
            });
            selectedFiles = [];
        }
        
        fileItem.classList.toggle('selected');
        
        const filePath = fileItem.dataset.path;
        if (fileItem.classList.contains('selected')) {
            if (!selectedFiles.includes(filePath)) {
                selectedFiles.push(filePath);
            }
        } else {
            selectedFiles = selectedFiles.filter(path => path !== filePath);
        }
        
        updateActionButtons();
    });
    
    // 双击打开
    fileItem.addEventListener('dblclick', event => {
        // 防止复选框和双击处理冲突
        if (event.target.classList.contains('form-check-input')) {
            return;
        }
        
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
    
    if (selectedFiles.length > 0) {
        deleteBtn.removeAttribute('disabled');
        downloadBtn.removeAttribute('disabled');
    } else {
        deleteBtn.setAttribute('disabled', 'disabled');
        downloadBtn.setAttribute('disabled', 'disabled');
    }
    
    // 只有选择单个文件时才能预览
    if (selectedFiles.length === 1) {
        const selectedItem = document.querySelector(`.file-item[data-path="${selectedFiles[0]}"]`);
        const isDir = selectedItem.dataset.isDir === 'true';
        
        if (!isDir) {
            previewBtn.removeAttribute('disabled');
        } else {
            previewBtn.setAttribute('disabled', 'disabled');
        }
    } else {
        previewBtn.setAttribute('disabled', 'disabled');
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
    });
    selectedFiles = [];
    updateActionButtons();
}

// 预览选中的文件
function previewSelectedFile() {
    if (selectedFiles.length === 1) {
        const filePath = selectedFiles[0];
        previewFile(filePath);
    }
}

// 预览文件
function previewFile(filePath) {
    const modal = new bootstrap.Modal(document.getElementById('preview-modal'));
    const modalTitle = document.getElementById('preview-title');
    const modalContent = document.getElementById('preview-content');
    const downloadBtn = document.getElementById('preview-download');
    
    // 清空之前的内容
    modalContent.innerHTML = '<div class="text-center"><div class="spinner-border" role="status"></div><p>加载中...</p></div>';
    modalTitle.textContent = '加载中...';
    
    // 设置下载按钮
    downloadBtn.onclick = () => {
        window.location.href = `/api/download/${filePath}`;
    };
    
    // 发起预览请求
    fetch(`/api/preview/${filePath}`)
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
                    // 如果是Markdown，使用marked库渲染
                    if (data.extension === 'md') {
                        modalContent.innerHTML = `
                            <div class="bg-light p-3 rounded markdown-content">
                                ${marked.parse(data.content)}
                            </div>
                            <div class="mt-3">
                                <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                                <p><strong>类型:</strong> ${data.info.type}</p>
                            </div>
                        `;
                    } 
                    // 如果是代码文件，使用highlight.js高亮显示
                    else if (['js', 'html', 'css', 'py', 'java', 'c', 'cpp', 'php', 'rb', 'go', 'json', 'xml'].includes(data.extension)) {
                        const highlighted = hljs.highlightAuto(data.content).value;
                        modalContent.innerHTML = `
                            <pre class="bg-light p-3 rounded"><code class="hljs">${highlighted}</code></pre>
                            <div class="mt-3">
                                <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                                <p><strong>类型:</strong> ${data.info.type}</p>
                            </div>
                        `;
                    } 
                    // 普通文本
                    else {
                        modalContent.innerHTML = `
                            <pre class="bg-light p-3 rounded">${data.content}</pre>
                            <div class="mt-3">
                                <p><strong>大小:</strong> ${data.info.size_formatted}</p>
                                <p><strong>类型:</strong> ${data.info.type}</p>
                            </div>
                        `;
                    }
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

// 删除选中的文件
function deleteSelectedFiles() {
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
            } else if (['images', 'documents', 'videos', 'audio', 'archives', 'code', 'others'].includes(currentPath)) {
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

// 下载选中的文件
function downloadSelectedFiles() {
    if (selectedFiles.length === 0) return;
    
    // 如果只选择了一个文件，直接下载
    if (selectedFiles.length === 1) {
        window.location.href = `/api/download/${selectedFiles[0]}`;
        return;
    }
    
    // 多个文件的情况，需要一个一个下载
    selectedFiles.forEach((filePath, index) => {
        setTimeout(() => {
            window.open(`/api/download/${filePath}`, '_blank');
        }, index * 500); // 每隔500毫秒下载一个文件
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
}

// 导航到上级目录
function navigateUp() {
    if (currentPath === '' || currentPath === '/' || 
        ['recent', 'search', 'images', 'documents', 'videos', 'audio', 'archives', 'code', 'others'].includes(currentPath)) {
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
                storageBar.className = 'progress-bar bg-danger';
            } else if (data.percentage > 70) {
                storageBar.className = 'progress-bar bg-warning';
            } else {
                storageBar.className = 'progress-bar bg-success';
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