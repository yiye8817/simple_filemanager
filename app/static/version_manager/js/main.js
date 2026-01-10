// 全局变量
let currentFile = null;

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // loadFileList();
});

// 上传文件
// async function uploadFile() {
//     const fileInput = document.getElementById('fileInput');
//     const file = fileInput.files[0];
    
//     if (!file) {
//         alert('请选择文件');
//         return;
//     }
    
//     const formData = new FormData();
//     formData.append('file', file);
    
//     // 显示进度条
//     const progressBar = document.getElementById('progressBar');
//     const progressBarInner = progressBar.querySelector('.progress-bar');
//     progressBar.style.display = 'block';
    
//     try {
//         const response = await axios.post('/api/upload/file', formData, {
//             headers: {
//                 'Content-Type': 'multipart/form-data'
//             },
//             onUploadProgress: (progressEvent) => {
//                 const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
//                 progressBarInner.style.width = percentCompleted + '%';
//                 progressBarInner.textContent = percentCompleted + '%';
//             }
//         });
        
//         if (response.data.success) {
//             alert('文件上传成功');
//             loadFileList();
//             fileInput.value = '';
//             setTimeout(() => {
//                 progressBar.style.display = 'none';
//                 progressBarInner.style.width = '0%';
//                 progressBarInner.textContent = '';
//             }, 1000);
//         } else {
//             alert('上传失败: ' + response.data.error);
//             progressBar.style.display = 'none';
//         }
//     } catch (error) {
//         console.error('上传错误:', error);
//         alert('上传失败: ' + (error.response?.data?.error || '网络错误'));
//         progressBar.style.display = 'none';
//     }
// }

// 加载文件列表
async function loadFileList() {
    try {
        const response = await axios.get('/api/upload/list');
        const tbody = document.getElementById('fileList');
        tbody.innerHTML = '';
        
        if (response.data.files && response.data.files.length > 0) {
            response.data.files.forEach(file => {
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td title="${file.name}">${file.name}</td>
                    <td>${formatFileSize(file.size)}</td>
                    <td>${file.uploadTime}</td>
                    <td>
                        <button class="btn btn-sm btn-primary" onclick='openVersionModal(${JSON.stringify(file).replace(/'/g, "&apos;")})'>
                            版本管理
                        </button>
                    </td>
                `;
                tbody.appendChild(row);
            });
        } else {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">暂无文件</td></tr>';
        }
    } catch (error) {
        console.error('加载文件列表失败:', error);
        document.getElementById('fileList').innerHTML = 
            '<tr><td colspan="4" class="text-center text-danger">加载失败</td></tr>';
    }
}

// 刷新文件列表
function refreshFileList() {
    loadFileList();
}
async function loadVersionData(file) {
    try {
        // 调用接口获取版本信息
        const response = await axios.post('/api/version/query_by_file_id', {
            file_id: file.id
        });
        
        if (response.data) {
            console.log(response.data.data)
            // 填充表单数据
            fillVersionForm(response.data.data,file);
            
            // 显示模态框
            const modal = new bootstrap.Modal(document.getElementById('versionModal'));
            modal.show();
        }
    } catch (error) {
        console.error('获取版本信息失败:', error);
        alert('获取版本信息失败: ' + (error.response?.data?.message || error.message));
    }
}

// 填充表单数据的函数
function fillVersionForm(data,file) {
    const form = document.getElementById('versionForm');
    console.log(form.elements['version'])
    console.log(data.version)
    
    file_size = file.size
    console.log(file_size)
    // 填充基本字段
    if (form.elements['system']) {
        form.elements['system'].value = data.system || 'liangeos';
    }
    
    if (form.elements['type']) {
        form.elements['type'].value = data.type || '';
    }
    
    if (form.elements['vendor']) {
        form.elements['vendor'].value = data.vendor || '';
    }
    
    if (form.elements['device_type']) {
        form.elements['device_type'].value = data.device_type || '';
    }
    
    if (form.elements['version']) {
        form.elements['version'].value = data.version || '';
    }
    
    if (form.elements['file_size']) {
        // if(data.file_size){
        //     fz = data.file_size
        // }else{
        //     fz = file_size
        // }
        // 格式化文件大小显示
        form.elements['file_size'].value = formatFileSize(file_size) || '';
    }
    
    if (form.elements['upgrade_desc']) {
        form.elements['upgrade_desc'].value = data.upgrade_desc || '';
    }
    
    if (form.elements['version_desc']) {
        form.elements['version_desc'].value = data.version_desc || '';
    }
    
    // 填充扩展字段（JSON格式）
    if (form.elements['extra_fields']) {
        if (data.extra_fields && typeof data.extra_fields === 'object') {
            form.elements['extra_fields'].value = JSON.stringify(data.extra_fields, null, 2);
        } else {
            form.elements['extra_fields'].value = '{}';
        }
    }
    
    // 填充隐藏字段
    if (document.getElementById('filePath')) {

        document.getElementById('filePath').value = file.path||data.file_path || '';
    }
    console.log(file.path)
    // 保存其他需要的数据到表单的data属性中
    form.dataset.versionId = data.id || '';
    form.dataset.fileId = data.file_id || '';
    form.dataset.md5sum = data.md5sum || '';
    form.dataset.timestamp = data.timestamp || '';
    form.dataset.isLatest = data.is_latest || false;
    form.dataset.status = data.status || '';
    form.elements['checksum_type'].value = data.checksum_type || 'sha256'
    // if (data.checksum_type)
    // algorithmSelect.value = data.checksum_type || 'SHA1'
}
// 打开版本管理模态框,首先需要去通过file.id去查询最新的版本信息
async function openVersionModal(file) {
    console.log("openVersionModal:"+file.id);
    currentFile = file;
    loadVersionData(file)
    // try {
    //     const data = {
    //     file_id:file.id
    // };
    //     const response = await axios.post('/api/version/get_by_file_id');
        
    // } catch (error) {
        
    // }
    // document.getElementById('filePath').value = file.path;
    // document.querySelector('[name="file_size"]').value = file.size;
    
    // // 重置表单
    // const form = document.getElementById('versionForm');
    // form.reset();
    // document.getElementById('filePath').value = file.path;
    // document.querySelector('[name="file_size"]').value = file.size;
    // document.querySelector('[name="system"]').value = 'liangeos';
    // document.querySelector('[name="extra_fields"]').value = '{}';
    
    // const modal = new bootstrap.Modal(document.getElementById('versionModal'));
    // modal.show();
}

// 获取自动版本号
async function getAutoVersion() {
    const form = document.getElementById('versionForm');
    const formData = new FormData(form);
    
    const data = {
        system: formData.get('system'),
        type: formData.get('type'),
        vendor: formData.get('vendor'),
        device_type: formData.get('device_type')
    };
    
    // if (!data.type || !data.vendor || !data.device_type) {
    //     alert('请先填写类型、厂商和设备类型');
    //     return;
    // }
    
    try {
        const response = await axios.post('/api/version/auto-version', data);
        if (response.data.success) {
            document.getElementById('versionNumber').value = response.data.version;
        } else {
            alert('获取版本号失败: ' + response.data.error);
        }
    } catch (error) {
        console.error('获取版本号失败:', error);
        alert('获取版本号失败');
    }
}

// 保存版本
async function saveVersion() {
    const form = document.getElementById('versionForm');
    const formData = new FormData(form);
    
    // 验证必填字段
    // if (!formData.get('type') || !formData.get('vendor') || !formData.get('device_type')) {
    //     alert('请填写所有必填字段');
    //     return;
    // }
    
    // 验证JSON格式
    let extraFields = {};
    try {
        extraFields = JSON.parse(formData.get('extra_fields') || '{}');
    } catch (e) {
        alert('扩展字段必须是有效的JSON格式');
        return;
    }
    console.log("saveVersion to server:"+currentFile.id+",sum:"
        +formData.get('checksum_type')+","+currentFile.size+","+currentFile.uploadTime);
    const data = {
        system: formData.get('system'),
        type: formData.get('type'),
        vendor: formData.get('vendor'),
        device_type: formData.get('device_type'),
        version: formData.get('version'),
        upgrade_desc: formData.get('upgrade_desc'),
        version_desc: formData.get('version_desc'),
        file_path: formData.get('file_path'),
        file_size: currentFile.size,
        //parseInt(formData.get('file_size')),
        extra_fields: extraFields,
        file_id:currentFile.id,
        user_id:currentFile.user_id,
        checksum_type:formData.get('checksum_type')
    };
    
    try {
        const response = await axios.post('/api/version/create', data);
        
        if (response.data.success) {
            alert('版本创建成功');
            bootstrap.Modal.getInstance(document.getElementById('versionModal')).hide();
            form.reset();
        } else {
            alert('创建失败: ' + response.data.error);
        }
    } catch (error) {
        console.error('保存版本失败:', error);
        alert('保存失败: ' + (error.response?.data?.error || '网络错误'));
    }
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}