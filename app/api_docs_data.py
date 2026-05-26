# API 文档数据源（供 /api/docs 与 /api/docs.json 使用）

API_DOC_META = {
    'title': 'File Manager API 参考',
    'version': '1.0',
    'base_url_hint': '与运行服务同源，例如 http://127.0.0.1:5000',
}

# 每项: method, path, auth, description, params(可选列表字符串), tests(可选 curl 示例)
API_SECTIONS = [
    {
        'id': 'docs',
        'title': '文档与自检',
        'endpoints': [
            {
                'method': 'GET',
                'path': '/api/docs',
                'auth': '无',
                'description': '返回本接口文档页面（HTML），浏览器可直接打开。',
                'params': [],
                'tests': 'curl -sS "http://127.0.0.1:5000/api/docs" | head',
            },
            {
                'method': 'GET',
                'path': '/api/docs.json',
                'auth': '无',
                'description': '返回与文档相同的结构化 JSON，便于脚本或 Postman 导入。',
                'params': [],
                'tests': 'curl -sS "http://127.0.0.1:5000/api/docs.json" | python3 -m json.tool | head',
            },
        ],
    },
    {
        'id': 'auth',
        'title': '认证与用户',
        'endpoints': [
            {
                'method': 'POST',
                'path': '/login',
                'auth': '无',
                'description': '表单登录，建立 Session Cookie。',
                'params': ['username', 'password'],
                'tests': 'curl -sS -c cookies.txt -d "username=u&password=p" -X POST "http://127.0.0.1:5000/login"',
            },
            {
                'method': 'GET',
                'path': '/api/user',
                'auth': 'Session',
                'description': '当前登录用户信息（含 api_key）。',
                'params': [],
                'tests': 'curl -sS -b cookies.txt "http://127.0.0.1:5000/api/user"',
            },
            {
                'method': 'POST',
                'path': '/api/auth/login',
                'auth': '无',
                'description': 'JSON 登录（若项目启用 JWT）。',
                'params': ['username', 'password'],
            },
        ],
    },
    {
        'id': 'files',
        'title': '文件与目录',
        'endpoints': [
            {
                'method': 'GET',
                'path': '/api/files 或 /api/files/<path>',
                'auth': 'Session',
                'description': '列出目录或按分类浏览；path 可为 type/分类键 或物理路径。',
                'params': ['query=remain（可选）'],
            },
            {
                'method': 'GET',
                'path': '/api/files/by-type/<file_type>',
                'auth': 'Session',
                'description': '按 FILE_TYPES 分类键列出文件（与 /api/files/type/<file_type> 等价）。',
                'params': ['file_type: documents|images|applications_android|…', 'query', 'page', 'per_page'],
                'tests': 'curl -sS -b cookies.txt "http://127.0.0.1:5000/api/files/by-type/documents"',
            },
            {
                'method': 'GET',
                'path': '/api/files/all-with-versions',
                'auth': 'Session',
                'description': '分页列出当前用户文件及关联版本管理记录。',
                'params': ['include_dirs=1', 'page', 'per_page'],
            },
            {
                'method': 'POST',
                'path': '/api/upload',
                'auth': 'Session',
                'description': '上传文件（multipart）。',
            },
            {
                'method': 'GET',
                'path': '/api/download/<int:file_id>',
                'auth': 'Session',
                'description': '按文件 ID 下载。',
                'tests': 'curl -sS -OJ -b cookies.txt "http://127.0.0.1:5000/api/download/1"',
            },
            {
                'method': 'GET',
                'path': '/api/download/by-name',
                'auth': 'Session',
                'description': '按文件名下载；同名多文件时需传 path（File.path）。',
                'params': ['name（必填）', 'path（可选）'],
                'tests': 'curl -sS -OJ -b cookies.txt "http://127.0.0.1:5000/api/download/by-name?name=hello.txt"',
            },
            {
                'method': 'GET',
                'path': '/api/download/<path:path>',
                'auth': 'Session',
                'description': '按相对路径下载。',
            },
            {
                'method': 'GET',
                'path': '/api/external/files/<path>',
                'auth': 'X-API-Key',
                'description': '与文件列表类似，使用 API 密钥访问。',
            },
            {
                'method': 'GET',
                'path': '/api/external/download/<int:file_id>',
                'auth': 'X-API-Key',
                'description': '外部下载指定文件。',
            },
            {
                'method': 'GET',
                'path': '/api/shared-files',
                'auth': 'Session',
                'description': '已开启公开分享的文件列表。',
            },
            {
                'method': 'GET',
                'path': '/api/get_file_type',
                'auth': 'Session',
                'description': '获取文件分类下拉数据。',
            },
        ],
    },
    {
        'id': 'version',
        'title': '版本管理',
        'endpoints': [
            {
                'method': 'POST',
                'path': '/api/version/create',
                'auth': 'Session',
                'description': '创建版本记录并关联文件。',
            },
            {
                'method': 'POST',
                'path': '/api/version/query_by_file_id',
                'auth': '无（建议加鉴权）',
                'description': '按 file_id 查询最新版本信息。',
                'params': ['file_id'],
            },
            {
                'method': 'POST',
                'path': '/api/version/apk-version-from-file',
                'auth': 'Session',
                'description': '从已上传 APK/AAB 解析 Manifest 版本信息。',
                'params': ['file_id'],
            },
            {
                'method': 'POST',
                'path': '/api/version/auto-version',
                'auth': '无（建议加鉴权）',
                'description': '按 system/type/vendor/device_type 规则递增版本号。',
            },
            {
                'method': 'GET',
                'path': '/api/version/<int:id>',
                'auth': '无',
                'description': '版本详情。',
            },
        ],
    },
    {
        'id': 'query',
        'title': '版本查询（OTA/管理）',
        'endpoints': [
            {
                'method': 'GET',
                'path': '/api/query/l',
                'auth': '无',
                'description': '按系统维度查询「最新」一条（OTA 格式）。',
                'params': ['system', 'type', 'vendor', 'dtype'],
            },
            {
                'method': 'GET',
                'path': '/api/query/all',
                'auth': '无',
                'description': '分页查询全部版本。',
                'params': ['page', 'per_page', 'system', 'type', 'vendor', 'device_type'],
            },
            {
                'method': 'GET',
                'path': '/api/query/latest',
                'auth': '无',
                'description': '查询最新一条（UI 用，返回 data 数组）。',
            },
            {
                'method': 'GET',
                'path': '/api/query/specific',
                'auth': '无',
                'description': '查询指定版本号记录。',
                'params': ['version', 'system', 'type', 'vendor', 'device_type'],
            },
            {
                'method': 'GET',
                'path': '/api/query/app-update',
                'auth': '无',
                'description': '检测应用更新并返回最新包下载信息（支持 GET/POST）。',
                'params': [
                    'package / android_package',
                    'system, type, vendor, device_type',
                    'current_version / version_name',
                    'current_version_code / version_code',
                ],
                'tests': 'curl -sS "http://127.0.0.1:5000/api/query/app-update?package=com.example.app&system=liangeos&type=ota&v=x&d=y"',
            },
        ],
    },
    {
        'id': 'vocab',
        'title': '词汇 API（/api/v1）',
        'endpoints': [
            {
                'method': 'GET',
                'path': '/api/v1/vocabulary/search',
                'auth': '视部署而定',
                'description': '词汇搜索等。',
            },
        ],
    },
    {
        'id': 'pytest',
        'title': '自动化测试（开发机）',
        'endpoints': [
            {
                'method': '本地',
                'path': 'pytest tests/',
                'auth': '无',
                'description': '运行项目内 pytest，覆盖文件类型列表、按名下载、文档路由等。',
                'tests': 'cd 项目根目录 && python3 -m pytest tests/ -v',
            },
            {
                'method': '本地',
                'path': 'scripts/test_file_manager_api.py',
                'auth': '无',
                'description': '对已启动服务做 HTTP 集成检查（需账号密码）。',
                'tests': 'python3 scripts/test_file_manager_api.py --user USER --password PASS',
            },
        ],
    },
]
