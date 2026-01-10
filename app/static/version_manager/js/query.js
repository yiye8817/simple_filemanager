// 全局变量
let currentPage = 1;
const perPage = 10;
let totalPages = 0;

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    queryVersions();
});

// 查询所有版本
async function queryVersions() {
    const form = document.getElementById('queryForm');
    const formData = new FormData(form);
    
    const params = new URLSearchParams();
    for (let [key, value] of formData.entries()) {
        if (value && key !== 'version') params.append(key, value);
    }
    params.append('page', currentPage);
    params.append('per_page', perPage);
    
    try {
        const response = await axios.get(`/api/query/all?${params}`);
        if (response.data.success) {
            displayResults(response.data.data);
            updateResultCount(response.data.total);
            updatePagination(response.data.pages, response.data.current_page);
        }
    } catch (error) {
        console.error('查询失败:', error);
        alert('查询失败: ' + (error.response?.data?.error || '网络错误'));
    }
}

// 查询最新版本
async function queryLatest() {
    const form = document.getElementById('queryForm');
    const formData = new FormData(form);
    
    const params = new URLSearchParams();
    for (let [key, value] of formData.entries()) {
        if (value && key !== 'version') params.append(key, value);
    }
    
    try {
        const response = await axios.get(`/api/query/latest?${params}`);
        if (response.data.success) {
            displayResults(response.data.data);
            updateResultCount(response.data.data.length);
            document.getElementById('pagination').innerHTML = '';
        }
    } catch (error) {
        console.error('查询失败:', error);
        alert('查询失败: ' + (error.response?.data?.error || '网络错误'));
    }
}

// 查询特定版本
async function querySpecific() {
    const form = document.getElementById('queryForm');
    const formData = new FormData(form);
    
    if (!formData.get('version')) {
        alert('请输入版本号');
        return;
    }
    
    const params = new URLSearchParams();
    for (let [key, value] of formData.entries()) {
        if (value) params.append(key, value);
    }
    
    try {
        const response = await axios.get(`/api/query/specific?${params}`);
        if (response.data.success) {
            displayResults([response.data.data]);
            updateResultCount(1);
            document.getElementById('pagination').innerHTML = '';
        }
    } catch (error) {
        console.error('查询失败:', error);
        if (error.response?.status === 404) {
            alert('未找到指定版本');
            displayResults([]);
            updateResultCount(0);
        } else {
            alert('查询失败: ' + (error.response?.data?.error || '网络错误'));
        }
    }
}

// 显示查询结果
function displayResults(data) {
    const tbody = document.getElementById('resultList');
    tbody.innerHTML = '';
    
    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="11" class="text-center text-muted">暂无数据</td></tr>';
        return;
    }
    
    data.forEach(item => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${item.id}</td>
            <td>${item.system}</td>
            <td><span class="badge bg-info">${item.type}</span></td>
            <td>${item.vendor}</td>
            <td>${item.device_type}</td>
            <td><strong>${item.version}</strong></td>
            <td><small>${item.checksum || '<span class="text-warning">计算中...</span>'}</small></td>
            <td>${formatFileSize(item.file_size)}</td>
            <td><small>${item.timestamp}</small></td>
            <td>${item.is_latest ? '<span class="badge bg-success">是</span>' : '<span class="badge bg-secondary">否</span>'}</td>
            <td>
                <div class="btn-group btn-group-sm" role="group">
                    <button class="btn btn-info" onclick="showDetail(${item.id})" title="查看详情">
                        详情
                    </button>
                    <button class="btn btn-danger" onclick="deleteVersion(${item.id})" title="删除">
                        删除
                    </button>
                </div>
            </td>
        `;
        tbody.appendChild(row);
    });
}

// 更新结果计数
function updateResultCount(count) {
    document.getElementById('resultCount').textContent = `${count} 条记录`;
}

// 显示详情
async function showDetail(id) {
    try {
        const response = await axios.get(`/api/version/${id}`);
        if (response.data.success) {
            const data = response.data.data;
            
            const content = `
                <div class="row">
                    <div class="col-md-6">
                        <dl class="row">
                            <dt class="col-sm-4">系统:</dt>
                            <dd class="col-sm-8">${data.system}</dd>
                            
                            <dt class="col-sm-4">类型:</dt>
                            <dd class="col-sm-8"><span class="badge bg-info">${data.type}</span></dd>
                            
                            <dt class="col-sm-4">厂商:</dt>
                            <dd class="col-sm-8">${data.vendor}</dd>
                            
                            <dt class="col-sm-4">设备类型:</dt>
                            <dd class="col-sm-8">${data.device_type}</dd>
                            
                            <dt class="col-sm-4">版本号:</dt>
                            <dd class="col-sm-8"><strong>${data.version}</strong></dd>
                        </dl>
                    </div>
                    <div class="col-md-6">
                        <dl class="row">
                            <dt class="col-sm-4">校验和:</dt>
                            <dd class="col-sm-8"><small>${data.checksum || '计算中...'}</small></dd>
                            
                            <dt class="col-sm-4">文件大小:</dt>
                            <dd class="col-sm-8">${formatFileSize(data.file_size)}</dd>
                            
                            <dt class="col-sm-4">时间戳:</dt>
                            <dd class="col-sm-8">${data.timestamp}</dd>
                            
                            <dt class="col-sm-4">最新版本:</dt>
                            <dd class="col-sm-8">${data.is_latest ? '<span class="badge bg-success">是</span>' : '<span class="badge bg-secondary">否</span>'}</dd>
                            
                            <dt class="col-sm-4">状态:</dt>
                            <dd class="col-sm-8">${data.status === 'active' ? '<span class="badge bg-success">活动</span>' : '<span class="badge bg-danger">已删除</span>'}</dd>
                        </dl>
                    </div>
                </div>
                <hr>
                <div class="mb-3">
                    <h6>文件路径:</h6>
                    <p class="text-muted"><small>${data.file_path}</small></p>
                </div>
                <div class="mb-3">
                    <h6>升级说明:</h6>
                    <p>${data.upgrade_desc || '<span class="text-muted">无</span>'}</p>
                </div>
                <div class="mb-3">
                    <h6>版本说明:</h6>
                    <p>${data.version_desc || '<span class="text-muted">无</span>'}</p>
                </div>
                <div>
                    <h6>扩展字段:</h6>
                    <pre class="bg-light p-2">${JSON.stringify(data.extra_fields, null, 2)}</pre>
                </div>
            `;
            
            document.getElementById('detailContent').innerHTML = content;
            const modal = new bootstrap.Modal(document.getElementById('detailModal'));
            modal.show();
        }
    } catch (error) {
        console.error('获取详情失败:', error);
        alert('获取详情失败');
    }
}

// 删除版本
async function deleteVersion(id) {
    if (!confirm('确定要删除这个版本吗？此操作不可恢复。')) {
        return;
    }
    
    try {
        const response = await axios.delete(`/api/query/delete/${id}`);
        if (response.data.success) {
            alert('删除成功');
            queryVersions(); // 重新加载列表
        } else {
            alert('删除失败: ' + response.data.message);
        }
    } catch (error) {
        console.error('删除失败:', error);
        alert('删除失败: ' + (error.response?.data?.error || '网络错误'));
    }
}

// 更新分页
function updatePagination(pages, current) {
    totalPages = pages;
    currentPage = current;
    
    const pagination = document.getElementById('pagination');
    pagination.innerHTML = '';
    
    if (pages <= 1) return;
    
    // 上一页
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${current === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `<a class="page-link" href="#" onclick="goToPage(${current - 1})">上一页</a>`;
    pagination.appendChild(prevLi);
    
    // 页码
    let startPage = Math.max(1, current - 2);
    let endPage = Math.min(pages, current + 2);
    
    if (startPage > 1) {
        const li = document.createElement('li');
        li.className = 'page-item';
        li.innerHTML = `<a class="page-link" href="#" onclick="goToPage(1)">1</a>`;
        pagination.appendChild(li);
        
        if (startPage > 2) {
            const dots = document.createElement('li');
            dots.className = 'page-item disabled';
            dots.innerHTML = `<span class="page-link">...</span>`;
            pagination.appendChild(dots);
        }
    }
    
    for (let i = startPage; i <= endPage; i++) {
        const li = document.createElement('li');
        li.className = `page-item ${i === current ? 'active' : ''}`;
        li.innerHTML = `<a class="page-link" href="#" onclick="goToPage(${i})">${i}</a>`;
        pagination.appendChild(li);
    }
    
    if (endPage < pages) {
        if (endPage < pages - 1) {
            const dots = document.createElement('li');
            dots.className = 'page-item disabled';
            dots.innerHTML = `<span class="page-link">...</span>`;
            pagination.appendChild(dots);
        }
        
        const li = document.createElement('li');
        li.className = 'page-item';
        li.innerHTML = `<a class="page-link" href="#" onclick="goToPage(${pages})">${pages}</a>`;
        pagination.appendChild(li);
    }
    
    // 下一页
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${current === pages ? 'disabled' : ''}`;
    nextLi.innerHTML = `<a class="page-link" href="#" onclick="goToPage(${current + 1})">下一页</a>`;
    pagination.appendChild(nextLi);
}

// 跳转页面
function goToPage(page) {
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    queryVersions();
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}