// 全局变量
let currentPath = '';
let selectedFiles = [];
let currentViewMode = 'grid'; // 'grid' 或 'list'

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
      // 加载用户API密钥
    //   getUserApiKey();
    // 初始化拖放上传
    initializeDragAndDrop();
    console.log("initializeDragAndDrop called");
    //新建文件和添加链接
    initializeNewFileAddLinker();
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
function initializeNewFileAddLinker(){
    console.log("initializeNewFileAddLinker called");
    // 初始化模态框
    const newFileModal = new bootstrap.Modal(document.getElementById('newFileModal'));
    const addLinkModal = new bootstrap.Modal(document.getElementById('addLinkModal'));
    
   
    
    // 新建文件按钮事件
    document.getElementById('new-file-btn').addEventListener('click', function() {
        document.getElementById('fileName').value = '';
        document.getElementById('fileContent').value = '';
        console.log("newFileModal to show");
        newFileModal.show();
    });
    
    // 保存文件按钮事件
    document.getElementById('saveFileBtn').addEventListener('click', function() {
        const fileName = document.getElementById('fileName').value.trim();
        const fileContent = document.getElementById('fileContent').value;
        
        if (!fileName) {
            alert('请输入文件名');
            return;
        }
        
        // 确保文件名有.txt后缀,暂时不考虑只有txt的形式
        const finalFileName = fileName;
        //fileName.endsWith('.txt') ? fileName : `${fileName}.txt`;
        
        // 创建请求数据
        const requestData = {
            name: finalFileName,
            content: fileContent,
            parent_id: getCurrentFolderId(),
            file_type: 'text'
        };
        
        // 发送请求到后端
        fetch('/api/newfile', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestData)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert('文件保存成功');
                newFileModal.hide();
                // 刷新文件列表
                loadFiles('/'+data.parent_path);
            } else {
                alert('文件保存失败: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('保存文件时发生错误');
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
    console.trace()
    console.log(fileItem)
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
// 辅助函数
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
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
//yiye add @8-12 增加文档编辑的功能
// 辅助函数
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text.replace(/[&<>"']/g, m => map[m]);
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
//yiye add @8-12 增加文档编辑的功能

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
            loadFiles(currentPath);
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
            path: currentPath,
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
    formData.append('path', currentPath);
    
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
