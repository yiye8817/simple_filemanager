# file_manager

一个基于 Flask 的「文件中心 + 设备同步 + 服务托管」一站式工具。前端有 Web UI（`app/templates/index.html`），同时配套一个 Android 客户端（位于符号链接 `test-update-apks/android-app`）实现「设备公共目录 → 服务器」的自动/手动增量同步。

主要能力：

- **文件管理**：分层目录、按类型浏览、批量上传/下载/共享、文档新建、APK 版本管理。
- **图片/视频缩略图**：`/api/thumbnail/<file_id>` 服务端用 Pillow + moviepy 生成，磁盘缓存。
- **设备 → 服务器自动同步**（Android 端）：按设备名建远端目录、增量上传、失败记录、最近 N 次同步历史。
- **服务托管**：上传 `tar.gz` → 选择端口 → 解压 → 跑 `run.sh <localPort>` → 通过 frpc 暴露到外网。
- **设备管理**：把任意 Linux/macOS 主机变成"远程设备"。基于 HTTP 反向连接（设备 → 服务器单向出向请求，天然穿透 NAT），Web UI 集中查看每台设备的唯一 ID / IP / 位置 / CPU / 内存 / 磁盘 / GPU / 在线状态 / 内网穿透信息，并支持按名字、IP、标签、主机名搜索过滤。能力包括：① 远程伪终端（异步下发命令，agent 拉到后回传 stdout/stderr/exit_code）；② 远程 `tar.gz` 服务托管（与本机服务同款契约，agent 拉归档 → 安全解压 → `bash run.sh <port>`，支持 install/start/stop/log/delete）。配套独立客户端 `clients/device_agent/` 一文件可运行。详见 [设备管理使用流程](#设备管理使用流程)。
- **文件夹 Webhook 监听**：在任意文件夹上挂回调 URL，文件夹内文件 `created` / `modified` / `deleted` 时自动 POST 文件链接 + 状态给业务方，支持 HMAC 签名、子目录递归、事件过滤。
- **页面动态刷新**：文件列表每 10s 静默轮询当前视图，diff 命中字段（id/size/modified/child_count/is_public/version）才重渲染；tab 不可见 / 有 modal 打开 / 焦点在输入框 / 最近 3s 内点击过 → 自动暂停以免打断操作。顶栏有「自动刷新」开关 + 手动刷新按钮 + 状态徽章。
- **文件夹显示子项数**：目录条目 `size_formatted` 显示为 "N 项"，后端 list 接口一次 `GROUP BY parent_id` 批量算 `child_count`，不会 N+1。
- **插件公开访问**：插件加 `public` 开关，开启后任意客户端 / 浏览器都能通过 `/api/public/folders/<id>/plugins/<name>/invoke` 无授权调用；插件返回的下载 URL 自带 HMAC 签名 (`/api/public/download/<id>?p=<plugin_id>&sig=<hex>`)，关闭开关或重置 API Key 后所有旧链接立即失效。UI 上每个插件展示"访问示例"区块（URL + curl 一键复制）。
- **MusicFree 安卓播放器对接**：`app/static/musicfree/file-manager.js` 是一个 MusicFree 插件，配合后端蓝图 `music_bp`（`/api/music/{info,search,sheets,sheet/<id>,track/<id>/source,track/<id>/lyric}`）把 file_manager 当成一个音源——MusicFree 里能搜索、按文件夹浏览（即"歌单"）、播放、读取同名 `.lrc` 歌词。**白名单机制**：顶栏新增"MusicFree 分享"按钮，包含总开关 + 文件夹勾选清单 (`User.musicfree_enabled` + `File.music_shared`)；只有被勾选的目录及其下音频才会暴露给 `/api/music/*`，播放时 `getMediaSource` 先调 `/track/<id>/source` 做白名单校验后再透传 `X-API-Key`，ExoPlayer 直接拉流。
- **独立 MusicFree 网关服务**（`servers/musicfree_server/`）：可选的"网关 / 缓存"形态——通过 file_manager 的「文件夹 Webhook」推送增量事件维护清单，再以 MusicFree 协议对客户端服务；适合 file_manager 在内网而播放器在外网、或想把"音乐查询"放到独立进程的场景。**符合服务托管契约**（`bash run.sh <localPort>`），启动日志直接打印 baseUrl / curl 配置 / 健康检查 URL。详见 [`servers/musicfree_server/README.md`](servers/musicfree_server/README.md)。
- **独立服务打包脚本**（`servers/pack.sh`）：把 `servers/<name>/` 子项目打包为 `dist/<name>.tar.gz` 可直接上传服务托管；**自动排除运行时产物**（`.venv` / `__pycache__` / `data/` / `*.log` / `.env`），支持 `--list` / `--dry-run` / `--all` / 交互式选择，打包前会校验目录里有 `run.sh`、打包后会校验归档里确有 `run.sh`。设置 `SOURCE_DATE_EPOCH` 可生成可重现归档。详见 [`servers/README.md`](servers/README.md)。
- 多模块数据库（主库 `filemanager.db`，bind: vocabulary / resources_manager / version_manager）。
- LLM Provider 管理、词汇查询等业务模块（详见各子目录）。

## 目录结构

```
app/
  __init__.py             create_app() 入口；注册所有 Blueprint，自动建表
  config.py               主库 + binds + UPLOAD_FOLDER / BACKUP_FOLDER
  models/
    user.py / file.py     用户与文件树（File.parent_id 指向自身形成树）
    managed_service.py    服务托管的元数据（本次新增）
    resource.py / version_model.py / llm_provider.py / vocabulary.py
  routes/
    auth_routes.py        登录/注册/会话
    file_routes.py        ★ 文件 CRUD / 分类浏览 / 上传 / 缩略图 / 共享 / 删除
    service_manager_routes.py  ★ 本次新增：服务托管
    device_routes.py        ★ 设备管理（用户侧 /api/devices/* + agent 侧 /api/agent/*）
    llm_routes.py / version_routes.py / query_routes.py / docs_routes.py
    user_routes.py / vocabulary.py / resource_routes.py
  services/
    file_service.py       文件备份等
    service_manager_service.py  ★ 本次新增：frpc 解析、端口检查、安全解压、启停
    backup_service.py / version_service.py / app_update_service.py / ota_formatters.py
  static/                 Web UI 资源
  templates/              Jinja 模板（主入口 index.html）
  utils/
    decorators.py         login_required / api_key_required
    helpers.py            FILE_TYPES、resolve_upload_physical_path 等
instance/                 SQLite DB 文件（运行时自动创建）
uploads/                  用户上传文件根目录（受 UPLOAD_FOLDER 配置）
  services/<user_id>/<id>/   ★ 服务托管：每条服务一个独立目录
    archive.tar.gz
    work/                 解压后的工作目录
    run.log               run.sh 输出
  .thumbnails/            缩略图缓存
scripts/                  辅助脚本（如 cleanup_duplicate_folders.py）
clients/
  device_agent/           ★ 设备客户端（独立运行）；含 agent.py + run.sh + README
test-update-apks/         Android 客户端项目（符号链接到外部仓库）
run.py                    入口：`python run.py [port]`
```

## 安装与运行

需要 Python 3.11+（项目当前在 3.12 上跑）。

```bash
python -m venv yenv
source yenv/bin/activate
pip install -r requirements.txt
python run.py 5000
```

或者直接 `./run.sh [PORT]`，会自动做这些事：

1. 选 `python3` / `python` + 校验 ≥ 3.9。
2. 复用 `.venv` / `venv` / `yenv` / `env` 任意一个 venv，没有就建 `.venv`。
3. 按 `requirements.txt` 装依赖（用 md5 sentinel 避免每次重装）。
4. **媒体依赖预检 (yt-dlp / aria2c)**: 没装就自动装 ——
   - `yt-dlp` 缺失 → 用 `pip` 装到当前 venv（视频站导入必备）；
   - `aria2c` 缺失 → 自动识别系统包管理器（apt / dnf / yum / pacman / apk / zypper / brew）+ **非交互 sudo** 装；非 root 且 sudo 需要密码时只打印手动安装命令，不阻塞启动（yt-dlp 会自动退回内置 downloader）。
   - 想跳过这一步：`FM_SKIP_MEDIA_DEPS=1 ./run.sh`。
5. Docker 预检（仅诊断，不试图自动装 docker）。
6. `exec python run.py "$PORT"`。

监听 `0.0.0.0:5000`，DEBUG 模式默认开启（见 `app/config.py`）。

数据库与上传目录会在第一次启动时自动创建：

- `instance/filemanager.db`、`vocabulary.db`、`resources_manager.db`、`version_manager.db`
- `uploads/`、`vm_backup/`

如需修改默认端口、密钥、存储上限等，编辑 `app/config.py`，或通过环境变量覆盖（`SECRET_KEY` / `JWT_SECRET_KEY` / `DATABASE_URL`）。

## 鉴权

- Web 路由用 Session：`@login_required` 装饰器，未登录 302 到 `/login`。
- 外部 API 用 `X-API-Key` Header：`@api_key_required`；用户在「我的页面」可生成/重置 API Key。
- 所有 `/api/...` 在 `Flask-Cors` 中放行 `Authorization` Header。

## API 速查

下表只列常用接口；完整列表可在运行时执行：

```bash
python -c "from app import create_app; [print(r) for r in create_app().url_map.iter_rules()]"
```

### 文件管理

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/files` / `/api/files/<path>` | 按目录或分类浏览（详见 `file_routes.get_files`） |
| GET | `/api/files/type/<type>` | 按文件类型列出（all / images / videos / ...） |
| POST | `/api/upload` | multipart 上传（字段 `files[]`、`path`） |
| POST | `/api/upload/check` `/chunk` `/merge` | 大文件分片上传 |
| POST | `/api/folder/create` | 创建文件夹（**幂等**：同名已存在直接返回该记录） |
| DELETE | `/api/files/<int:id>` `/<path:path>` | 删除文件/目录（**容错**：物理不存在也清 DB） |
| POST | `/api/files/<int:id>/share` `/unshare` | 公开分享开关 |
| GET | `/api/download/<int:id>` `/api/preview/<int:id>` | 下载/预览 |
| GET | `/api/thumbnail/<int:id>?size=200` | 图片/视频缩略图（JPEG，磁盘缓存） |
| POST | `/api/newfile` `/api/save-file` | 在线新建/保存文档 |
| GET | `/api/import/capabilities` | 探测 yt-dlp / aria2c 是否就绪。返回 `{ytdlp, ytdlp_path, aria2c, aria2c_path, video_hosts}`。 |
| POST | `/api/import/url` | 通过 URL 导入（异步任务）。请求体 `{url, mode, path, engine, proxy, ytdlp_args, aria2c, aria2c_threads}`：`engine` ∈ `auto`(默认) / `http` / `ytdlp`，`auto` 时检测 youtube/bilibili 自动切 `yt-dlp`；`proxy` 支持 `host:port` / `http://…` / `socks5://…`，仅本次生效；`ytdlp_args` 是字符串，会按 `shlex` 切分拼到 yt-dlp 命令尾部（可覆盖默认 `-f` 等）；`aria2c=true` 时若系统有 aria2c 则自动 `--downloader aria2c --downloader-args 'aria2c:-x16 -s16 -k1M …'`，`aria2c_threads` 默认 16，可调到 64。响应体含 `engine` / `is_video_site` / `aria2c_available`。 |
| GET | `/api/import/job/<job_id>` | 任务状态。返回 `status` / `message` / `imported_names` / `engine` / `proxy` / `log`（yt-dlp 完整 stdout/stderr，含 `$ <cmd>` 命令行，失败时给前端排错用，可 `?log=0` 关掉）。 |

### 文件夹 Webhook 监听

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/folder-watches` | 列当前用户的全部 watch |
| POST | `/api/folder-watches` | 新建：`folder_id`/`url`/`secret`/`events`/`include_subdirs`/`enabled` |
| GET | `/api/folder-watches/<id>` | 单个详情（含 `last_status_code` / `last_error` / `trigger_count`） |
| PUT \| PATCH | `/api/folder-watches/<id>` | 部分字段更新 |
| DELETE | `/api/folder-watches/<id>` | 删除 |
| POST | `/api/folder-watches/<id>/test` | 手动触发一次测试 payload |
| GET | `/api/folders/<folder_id>/watches` | 某个文件夹上已配置的所有 watch |

> 前端入口：在「文件」页面**右键**任意文件夹 → "启动监听并设置远端接口…"，弹出监听管理对话框，可以查看 / 新增 / 编辑 / 测试 / 删除该文件夹上的所有 watch。

### 外部 API · 上传到指定文件夹

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/external/folder` | **本次新增**：幂等创建/取回文件夹（`name`/`parent_id`/`parent_path`），脚本里非常方便 |
| POST | `/api/external/folder-upload` | **推荐**：`X-API-Key` 鉴权；支持 `folder_id` / `path` 选目录、`files[]` 多文件、`overwrite` 覆盖语义；上传成功后会按文件夹的 `FolderWatch` 自动触发 webhook |
| POST | `/api/external/upload`         | 兼容旧版本：单文件 `file`、同名自动改名（不覆盖）；同样支持 `folder_id` / `path` |
| POST | `/api/external/download/<file_id>` | 按文件 id 下载（API Key 鉴权），插件 ctx 生成的 URL 默认就指向这里 |

### 文件夹插件机制（本次新增）

把一个文件夹变成一个 mini API：插件读取该文件夹下的文件并按固定格式响应给客户端。支持两种形态：

- **声明式（`declarative`，JSON）**：在 UI 里粘 JSON 配置，按规则匹配文件、按模板组装响应；安全可控、跨语言友好。
- **Python 代码（`python`）**：上传一段 Python 源码，实现 `def invoke(action, params, ctx)`，最大灵活。**任意代码执行**，仅 owner 自己可调用（信任模型）。

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/folders/<folder_id>/plugins` | 列文件夹下的全部插件 |
| POST | `/api/folders/<folder_id>/plugins` | 新建插件（body: `name`/`kind`/`description`/`config`/`code`/`include_subdirs`/`enabled`） |
| GET | `/api/folder-plugins/<id>` | 单个详情（含 `config` / `code` 源码） |
| PUT \| PATCH | `/api/folder-plugins/<id>` | 部分字段更新 |
| DELETE | `/api/folder-plugins/<id>` | 删除 |
| POST | `/api/folder-plugins/<id>/invoke` | 登录态调用（自测） |
| POST | `/api/external/folder-plugins/<id>/invoke` | 外部客户端 API Key 调用（按 id 寻址） |
| POST | `/api/external/folders/<folder_id>/plugins/<name>/invoke` | 外部客户端 API Key 调用（按 name 寻址，更易记） |

前端入口：右键文件夹 → 「插件管理…」，或网格视图的 ⋮ 菜单 / 列表视图的「插件」按钮。模态框里支持创建/编辑/启停/删除，并在底部内置「调用测试」面板，可即时看到响应 JSON。

### 服务托管

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/services/port-options` | 解析 `/etc/frpc.toml`，返回可选 (localPort, remotePort) + serverAddr |
| GET | `/api/devices/<id>/tunnels?user=root` | 读取该设备 agent 心跳上报的 `stats.frpc_tunnels`，加工出 `is_ssh` / `ssh_command` / `external_url` 等派生字段。**frpc 跑在被管设备本机上**, 服务器只是透传; `localPort==22` 标 `is_ssh=true` 并给 `ssh -p <remotePort> <user>@<serverAddr>` |
| POST | `/api/ssh/sessions` | 创建一次性 SSH 凭据 (body: `host` `port` `username` `password?` `private_key?` `key_passphrase?`)，返回 `sid` 和 `ttl=300`；不落盘只暂存在进程内存 |
| GET | `/api/ssh/sessions/<sid>` | 查 session 是否仍有效（peek，不消费）；不同 user 的 sid 返回 404 |
| DELETE | `/api/ssh/sessions/<sid>` | 主动注销（用户关 modal 时调） |
| WS | `/api/ssh/ws/<sid>?cols=&rows=` | 网页 SSH 终端 WebSocket。前端 xterm.js → JSON 帧 → paramiko 后端转发 |
| GET | `/api/services` | 列当前用户所有服务 |
| POST | `/api/services` | multipart：`name` `local_port` `remote_port` `archive`(.tar.gz) |
| GET | `/api/services/<id>` | 服务详情（含 `access_url`） |
| POST | `/api/services/<id>/start` | 解压 + 端口检查 + 启动 `run.sh <localPort>` |
| POST | `/api/services/<id>/stop` | 终止进程组 |
| GET | `/api/services/<id>/log?lines=200` | 读最近 N 行 `run.log` |
| DELETE | `/api/services/<id>` | 停止 + 清理目录 + 删 DB |

### 设备管理

用户侧（session 鉴权）：

| Method | Path | 说明 |
|---|---|---|
| GET | `/devices` | 设备管理 Web 页面 |
| GET | `/api/devices?q=&online=1` | 列设备（搜索 / 仅在线） |
| POST | `/api/devices` | 新建设备（返回一次性 `device_token`） |
| GET | `/api/devices/<id>` | 设备详情（含资源快照、`tunnel_info`、`online`） |
| PATCH | `/api/devices/<id>` | 改名 / 位置 / 标签 / 备注 |
| POST | `/api/devices/<id>/rotate-token` | 重置 token（旧 token 立刻失效） |
| DELETE | `/api/devices/<id>` | 级联删除：任务 + 服务 + 归档 + DB |
| POST | `/api/devices/<id>/commands` | 下发 shell 命令（异步任务） |
| GET | `/api/devices/<id>/tasks` | 任务列表 |
| GET | `/api/devices/<id>/tasks/<tid>` | 单任务详情（含 stdout/stderr/exit_code） |
| GET / POST | `/api/devices/<id>/services` | 列 / 上传设备上的 tar.gz 服务 |
| POST | `/api/devices/<id>/services/<sid>/start` `/stop` `/log` | 启停 / 拉日志（都是入队任务） |
| DELETE | `/api/devices/<id>/services/<sid>` | 删除服务（下发 `service_delete` 任务 + 服务器侧清归档） |

设备客户端（agent）侧：

> **自助注册新增** (v0.2+): agent 不再需要预先创建好设备并配 token；启动时只要给 `--server-url` 就行，自己生成持久化的 `device_uid`，调用下面的 `/api/agent/enroll` 自助登记，服务器分配 `device_token` 写回本地。后续接口仍走 `X-Device-Token` 头。

| Method | Path | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/agent/enroll` | `X-Enroll-Token` (可选) | 自助注册。请求体 `{device_uid, hostname, os_*, arch, agent_version, stats, tunnel_info, …}`。已存在的 `device_uid` 会返回当前 token (不重置)；新的会归到默认 owner (`DEVICE_AGENT_DEFAULT_USER_ID` 或第一个用户)，自动按 hostname 命名。受 `DEVICE_AGENT_OPEN_ENROLL` (默认 True) + `DEVICE_AGENT_ENROLL_TOKEN` 控制。 |

`X-Device-Token` 鉴权的接口：

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/agent/register` | 启动时调用，等价一次完整心跳 |
| POST | `/api/agent/heartbeat` | 周期上报 `{hostname, os_*, arch, agent_version, local_ip, public_ip, stats, tunnel_info}` |
| GET | `/api/agent/poll?wait=20` | 长轮询任务（最大 25s，避免反代 60s 超时） |
| POST | `/api/agent/tasks/<tid>/result` | 上报 `{status, exit_code, stdout, stderr, error, result, claim_token}` |
| GET | `/api/agent/services/<sid>/archive` | 拉取服务 tar.gz |
| POST | `/api/agent/services/<sid>/status` | 主动上报状态变化（带外 watchdog 通道） |

### 其它常用

| Method | Path | 说明 |
|---|---|---|
| POST | `/login` `/register` | 登录/注册 |
| GET | `/api/user` | 当前会话用户信息 |
| GET | `/api/storage` | 存储用量 |
| GET | `/api/version/<id>` `/api/version/...` | 文件版本管理 |
| GET / POST / PUT / DELETE | `/api/llm` 等 | LLM Provider 管理 |
| GET / POST | `/api/v1/vocabulary/...` | 词汇查询 |

## 文件夹 Webhook 监听使用流程

> 适用场景：你希望「客户端把文件传到 file_manager 的某个目录后，**业务后端**立刻收到一条带下载链接的通知」——典型场景如：上传 APK → CI 拉去做扫描；上传图片 → 推送给图床/检测服务；上传报表 → 触发 ETL。

**触发点是业务层而不是磁盘 watchdog**：所有的 `/api/upload`、`/api/upload/merge`、`/api/newfile`、`/api/save-file`、`/api/import/url`、`/api/files/<id> DELETE` 在落库后会同步调用 `webhook_service.notify_file_event(file, event)`，按"被变更文件的祖先目录链"匹配所有命中的 watch，异步发起 HTTP POST。

1. **确定要监听的文件夹 id**：通过 `GET /api/files/<path>` 找到目标文件夹，记下 `id`（也可以直接传 `folder_path`）。

2. **创建 watch**：

   ```bash
   curl -b cookies.txt -X POST http://127.0.0.1:5000/api/folder-watches \
     -H 'Content-Type: application/json' \
     -d '{
       "folder_id": 10,
       "url": "https://your-business.example.com/hooks/file-manager",
       "secret": "topsecret",
       "events": ["created", "modified"],
       "include_subdirs": true,
       "enabled": true
     }'
   ```

   字段说明：

   - `folder_id` / `folder_path`：二选一，标识要监听的目录（path 是从用户根目录起的相对路径）。
   - `url`：业务方 webhook 入口（仅允许 http/https）。
   - `secret`：可选；非空时请求会带 `X-Signature: sha256=<HMAC-SHA256(body)>`，业务方可校验来源。
   - `events`：可选；不填表示三种事件（`created` / `modified` / `deleted`）都监听；可以填子集。
   - `include_subdirs`：默认 `true`；为 `false` 时仅"直接放到这个文件夹里的文件"会触发，深层子文件不通知。
   - `enabled`：默认 `true`；可后续 `PUT /api/folder-watches/<id>` 切换。

3. **手动测一下**：

   ```bash
   curl -b cookies.txt -X POST http://127.0.0.1:5000/api/folder-watches/1/test
   ```

   会立即给配置的 URL 发一条 `event=test` 的 payload。然后 `GET /api/folder-watches/1` 看 `last_status_code` / `last_error` 即可确认对端是否可达。

4. **真实触发**：之后只要把文件上传到该文件夹（或其子目录），webhook 就会自动发出。

### Webhook Payload 结构

`Content-Type: application/json`，body 形如：

```json
{
  "event": "created",
  "timestamp": "2025-05-21T03:14:00.000Z",
  "watch": {
    "id": 1,
    "folder_id": 10,
    "folder_name": "地理",
    "folder_path": "Shi_Pin/Di_Li",
    "include_subdirs": true
  },
  "file": {
    "id": 31,
    "name": "ut.txt",
    "path": "Shi_Pin/Di_Li/ut.txt",
    "size": 4,
    "file_type": "documents",
    "is_dir": false,
    "user_id": 1,
    "parent_id": 10,
    "modified_at": "2025-05-21T03:13:59.123456",
    "download_url": "http://your-host/api/download/31",
    "preview_url": "http://your-host/api/preview/31",
    "serve_url": "http://your-host/api/serve/31"
  }
}
```

字段说明：

- `event`：`created` / `modified` / `deleted` / `test`（手动测试）。
- `file.download_url` / `preview_url` / `serve_url`：可直接给用户下载/预览/串流；接口本身需要登录或 API Key，业务方通常会用同一账号或外部 API。
- 删除事件下，`file.id` 是被删 ORM 记录的快照（业务方可用 `name`/`path` 与历史 payload 关联，不再可下载）。
- 如果配了 `secret`，HTTP 头会有 `X-Signature: sha256=<hex>`：

  ```python
  import hmac, hashlib
  expected = "sha256=" + hmac.new(b"topsecret", raw_body_bytes, hashlib.sha256).hexdigest()
  assert hmac.compare_digest(expected, request.headers["X-Signature"])
  ```

### 注意事项

- **失败不阻塞主流程**：webhook 发送在 daemon 线程里，HTTP 错误/超时只写到 `last_error`、不会让上传/编辑/删除接口报错。
- **超时**：默认每次请求 8 秒；不会自动重试（请业务方自身做幂等接收 + 失败兜底）。
- **链接的可达性**：`download_url` 等是基于 `PUBLIC_BASE_URL` 配置或当前请求的 `host_url` 推断的。如果服务跑在反向代理后面或需要外网域名，请在 `app/config.py` / 环境变量里设置 `PUBLIC_BASE_URL=https://your-host`。
- **不会监听 `folder/create`**：当前只对**文件**的 created/modified/deleted 触发；目录的创建与删除不通知（删除目录时，里面的每个子文件会以 `deleted` 事件单独通知）。

## 上传文件到指定文件夹（外部 API）

业务方通常的场景是「**带着 API Key 把文件丢进 file_manager 的某个目录，然后业务后端立刻收到带下载链接的 webhook**」。这条链路已在仓库里完整打通：

```
+------------+   X-API-Key   +-------------------+   FolderWatch    +---------------+
|  你的脚本  | ------------> | file_manager      | --------------->  |  你的业务后端 |
|  CI / 应用 |  multipart    | /api/external/    |  POST application |  (webhook)    |
+------------+               | folder-upload     |  /json (HMAC)     +---------------+
                             +-------------------+
```

### 接口契约：`POST /api/external/folder-upload`

- **鉴权**：`X-API-Key` Header（在「我的页面 → API 密钥」里取）。
- **Content-Type**：`multipart/form-data`。
- **字段**：

  | 字段 | 必填 | 说明 |
  |---|---|---|
  | `folder_id` | 选 | 目标文件夹 id（推荐，避免中文路径的转码问题） |
  | `path` | 选 | 目标文件夹相对路径（如 `pictures/2025`）；与 `folder_id` 二选一；都不传 → 上传到用户根目录 |
  | `file` 或 `files[]` 或 `files` | 必 | 单/多文件；三种字段名都接受 |
  | `overwrite` | 选 | `"1"`（默认）同名覆盖并触发 `modified`；`"0"` 自动改名 `_1` `_2` 并触发 `created` |

- **响应**：

  ```json
  {
    "success": true,
    "folder": {"id": 32, "name": "demo-watched-folder", "path": "demo-watched-folder"},
    "files": [
      {"id": 33, "name": "a.txt", "path": "demo-watched-folder/a.txt",
       "size": 5, "type": "documents", "event": "created", ...}
    ],
    "failed": []
  }
  ```

  `files[*].event` = `"created"` 或 `"modified"`，与发往业务方 webhook 的事件类型一致。

### Demo · 用一条命令验证完整闭环

仓库提供 `scripts/demo_folder_upload.py`，一条命令完成 **登录 → 取 API Key → ensure 目录 → 注册 watch → 启动本地 webhook 接收器 → 上传文件 → 收回调 → HMAC 校验** 全流程：

```bash
./yenv/bin/python scripts/demo_folder_upload.py \
    --base-url http://127.0.0.1:5000 \
    --user youruser --password yourpwd \
    --folder-path demo-watched-folder \
    --files /tmp/a.txt /tmp/b.png
```

预期输出（简化）：

```
[1/6] 登录 youruser@... ...
[2/6] 取 API Key ...        api_key = 9253e9...e6f3 (len=36)
[3/6] 确认目标文件夹 path='demo-watched-folder' ...
      folder_id = 32, path = demo-watched-folder
[4/6] 启动本地 webhook 接收器 ...
      hook_url = http://192.168.1.10:41643/hook
[5/6] 注册 watch 并上传文件 ...
      watch_id = 1, events = ['created', 'modified', 'deleted']
[6/6] 等待 webhook 回调 (2 个文件) ...
      [1] event=created file=a.txt download_url=http://.../api/download/33
           signature=sha256=1c7ca7...
           verify  = 签名匹配 ✓
DONE. webhook 闭环验证完成。
```

### Demo · curl 一行版（无 webhook）

```bash
# 假设已知 folder_id=32 且 X-API-Key 在 $FM_API_KEY
curl -X POST http://127.0.0.1:5000/api/external/folder-upload \
    -H "X-API-Key: $FM_API_KEY" \
    -F "folder_id=32" \
    -F "files[]=@/tmp/a.txt" \
    -F "files[]=@/tmp/b.png" \
    -F "overwrite=1"
```

或者按路径定位文件夹（路径里允许包含中文）：

```bash
curl -X POST http://127.0.0.1:5000/api/external/folder-upload \
    -H "X-API-Key: $FM_API_KEY" \
    --data-urlencode "path=pictures/2025" \
    -F "file=@/tmp/cover.jpg"
```

### Demo · Python（含 webhook 接收 + 签名校验）

> 仓库脚本 `scripts/demo_folder_upload.py` 已经是完整可运行版本，无需第三方依赖。下面是一个**裁剪过**的最小片段，方便直接拷贝到业务方代码里：

```python
import hashlib, hmac, json, mimetypes, uuid
from urllib.request import Request, urlopen

API_KEY = "在「我的页面 → API 密钥」里复制"
BASE = "http://127.0.0.1:5000"
SECRET = "topsecret"  # 与 watch 注册时一致

def post_multipart(url, fields, files, headers=None):
    boundary = "----B" + uuid.uuid4().hex
    parts = []
    for k, v in (fields or {}).items():
        parts += [f"--{boundary}\r\n".encode(),
                  f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode(),
                  str(v).encode() + b"\r\n"]
    for k, fname, content in files:
        ct = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        parts += [f"--{boundary}\r\n".encode(),
                  f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'.encode(),
                  f"Content-Type: {ct}\r\n\r\n".encode(),
                  content, b"\r\n"]
    parts.append(f"--{boundary}--\r\n".encode())
    h = {"Content-Type": f"multipart/form-data; boundary={boundary}",
         "X-API-Key": API_KEY, **(headers or {})}
    req = Request(url, data=b"".join(parts), headers=h, method="POST")
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read())

with open("/tmp/a.txt", "rb") as f:
    body = f.read()
res = post_multipart(
    f"{BASE}/api/external/folder-upload",
    fields={"folder_id": 32, "overwrite": "1"},
    files=[("files[]", "a.txt", body)],
)
print(res)
# {'success': True, 'folder': {...}, 'files': [{'id': 33, 'event': 'created', ...}], 'failed': []}


# === 业务后端：验证 webhook 来源（HMAC-SHA256 签名） ===
def verify_webhook(raw_body: bytes, x_signature_header: str) -> bool:
    expected = "sha256=" + hmac.new(SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, x_signature_header or "")
```

### Demo · Node.js / Fetch（浏览器或服务端通用）

```js
const fd = new FormData();
fd.append('folder_id', '32');
fd.append('overwrite', '1');
fd.append('files[]', new Blob([fileBytesA]), 'a.txt');
fd.append('files[]', new Blob([fileBytesB]), 'b.png');

const res = await fetch('http://127.0.0.1:5000/api/external/folder-upload', {
    method: 'POST',
    headers: { 'X-API-Key': process.env.FM_API_KEY },
    body: fd,
});
const j = await res.json();
console.log(j.files.map(f => `${f.event} ${f.path}`));
```

### 在前端右键启用监听

打开「文件」页面，**右键**点击目标文件夹 → 选择 **「启动监听并设置远端接口…」**。在弹出的对话框里：

- 填业务方 webhook URL；
- 填可选 `secret`（开启 HMAC 签名）；
- 勾选要监听的事件（`created` / `modified` / `deleted`）；
- 选择是否 **监听子目录**；
- 保存。

同一个文件夹可以挂多条 watch（同时推给多个业务方）。每条记录都有 **测试** 按钮，会立即给目标 URL 发一条 `event=test` 的 payload，配合 `last_status_code` / `last_error` 可以快速排查对端是否可达。

## 服务托管使用流程

> 适用场景：你已经在服务器上跑了 `frpc`，需要把"任意一个本地服务"通过 frpc 的隧道暴露到外网；这套 API 让你**通过浏览器/移动端上传 tar.gz、点一下就启动**。

1. **配置 frpc**：在服务器上安装 frp 客户端并将 `/etc/frpc.toml`（或 `app.config['FRPC_TOML']` 指定的路径）写好若干 `[[proxies]]`，例如：

   ```toml
   serverAddr = "your.frps.host"
   serverPort = 7000

   [[proxies]]
   name = "demo-http"
   type = "tcp"
   localPort = 18080
   remotePort = 28080

   [[proxies]]
   name = "demo-ws"
   type = "tcp"
   localPort = 18090
   remotePort = 28090
   ```

   > 启动 `frpc -c /etc/frpc.toml` 让通道处于活跃状态；本仓库**不**负责 frpc 进程本身，只负责把你的业务进程跑在 `localPort` 上。

2. **拉端口选项**：

   ```bash
   curl -b cookies.txt -c cookies.txt http://127.0.0.1:5000/api/services/port-options
   ```

   返回：

   ```json
   {
     "server_addr": "your.frps.host",
     "server_port": 7000,
     "options": [
       {"name":"demo-http","local_port":18080,"remote_port":28080,"in_use":false}
     ]
   }
   ```

3. **准备 tar.gz**：归档内必须包含 `run.sh`，可执行脚本接受第一个位置参数为 `localPort`：

   ```bash
   #!/usr/bin/env bash
   set -e
   PORT="$1"
   exec ./your-binary --listen "127.0.0.1:$PORT"
   ```

   归档结构示例（顶层有一层目录是允许的，会被自动识别）：

   ```
   demo.tar.gz
   └── demo/
       ├── run.sh
       └── your-binary
   ```

4. **上传 + 创建服务**：

   ```bash
   curl -b cookies.txt -c cookies.txt \
     -F "name=My Demo" \
     -F "local_port=18080" \
     -F "remote_port=28080" \
     -F "archive=@demo.tar.gz" \
     http://127.0.0.1:5000/api/services
   ```

   服务端会：

   - 校验 `(local_port, remote_port)` 必须在 `frpc.toml` 中存在；
   - 把上传的归档写到 `uploads/services/<user_id>/<service_id>/archive.tar.gz`；
   - DB 记录 `status=idle`。

5. **启动**：

   ```bash
   curl -b cookies.txt -X POST http://127.0.0.1:5000/api/services/123/start
   ```

   服务端会：

   - 安全解压（拒绝 `..`、绝对路径、链接逃逸；总大小默认 ≤ 1GB）；
   - 找 `run.sh`（在归档顶层目录或解压目录根下）；
   - **检查 `localPort` 是否被占用**（用 connect 探测，避免 `SO_REUSEADDR` 误判）；
   - `bash run.sh <localPort>` + `setsid` 起新进程组；stdout/stderr 重定向到 `run.log`；
   - 启动 0.4s 后做一次活性探测：若 `run.sh` 立刻挂了，返回错误并附带日志尾部。

6. **查看状态 / 日志**：

   ```bash
   curl -b cookies.txt http://127.0.0.1:5000/api/services/123
   curl -b cookies.txt 'http://127.0.0.1:5000/api/services/123/log?lines=100'
   ```

   `service.access_url` 字段 = `http://<serverAddr>:<remote_port>`。

7. **停止 / 删除**：

   ```bash
   curl -b cookies.txt -X POST   http://127.0.0.1:5000/api/services/123/stop
   curl -b cookies.txt -X DELETE http://127.0.0.1:5000/api/services/123
   ```

   - Stop：先 SIGTERM 整个进程组，5s 不退则 SIGKILL；
   - Delete：自动 stop + 清理 `uploads/services/<user_id>/<id>/` + 删 DB 记录。

### 安全约束

- **端口白名单**：所有进入服务托管的 `localPort/remotePort` 必须存在于 `frpc.toml`，避免越权监听。
- **路径越界保护**：`safe_extract_tarball` 拒绝绝对路径、`..`、链接指向目录外的成员，并限制总大小（默认 1GB）。
- **运行用户**：`run.sh` 进程继承 Flask 进程的用户身份；如果你需要更强隔离，建议把 Flask 跑在专用低权限账号里或叠加 `systemd-nspawn`/容器。
- **日志体积**：`run.log` 不会自动滚动；长跑服务请在 `run.sh` 内自行轮转或周期性 `truncate -s 0`。

## 设备管理使用流程

> 适用场景：你有若干台远程主机（家里的 NAS、IDC 的服务器、内网树莓派），希望通过一个统一的 Web 面板查看每台机器的资源、远程执行命令、把 `tar.gz` 服务下发上去跑。代码在 `clients/device_agent/`。

### 架构

只有客户端 → 服务器的 **出向 HTTP 请求**，对 NAT / 防火墙完全透明：

```
+--------+   POST /api/agent/register    +---------------+
| agent  | -----------------------------> | file_manager  |
|        |   POST /api/agent/heartbeat    |  /api/agent/* |
|        | --- 资源 / 在线状态 ---------> | 任务队列      |
|        |   GET  /api/agent/poll?wait=20 |               |
|        | <-- pending 任务 -----------   |               |
|        |   POST /api/agent/tasks/<id>/result -> 入库
+--------+                                +---------------+
```

任务一共 6 种：`exec`（远程命令）、`service_install` / `service_start` / `service_stop` / `service_delete`、`service_log`。

### 一、在远程主机部署 agent（v0.2+：只配服务器地址就行）

```bash
# 把目录拷过去（仅需 agent.py + run.sh + requirements.txt + README.md）
scp -r clients/device_agent/  user@your-host:/opt/fm-agent/
ssh user@your-host

cd /opt/fm-agent
python3 -m pip install -r requirements.txt   # 可选，没装 psutil/requests 也能跑

# 最小启动：只需要服务器地址；agent 会自己生成 device_uid + 自助 enroll
./run.sh --server-url http://your-host:5000
```

第一次启动会在 `~/.fm_device_agent/` 下落两个文件：

- `device.uid` — 持久化的设备 UID（hostname + MAC 的 sha1 截断，同一台机器重装 agent 后还是同一个）
- `device.token` — enroll 后服务器分配的 token，权限 `0600`

1~2 秒后，Web UI [设备管理页](http://127.0.0.1:5000/devices) 自动出现这台设备，红点变成绿点 🟢，CPU/内存/磁盘/GPU/网卡全量指标实时更新。

公网部署请关闭开放注册 + 配 enroll token：

```bash
# 服务器端
export DEVICE_AGENT_OPEN_ENROLL=0
export DEVICE_AGENT_ENROLL_TOKEN=$(openssl rand -hex 32)

# agent 端
./run.sh --server-url https://your.domain --enroll-token <上面那个>
```

### 二、(可选) 在 Web 上预创建设备 + 显式 token

老的"创建设备 → 拿 token → 配给 agent"流程仍然支持。进 [设备管理页](http://127.0.0.1:5000/devices) 点 **「创建设备」** 填名字/位置/标签后，弹窗里有一次性显示的 `device_uid` 和 `device_token`：

```bash
./run.sh \
    --server-url   http://your-host:5000 \
    --device-token <token> \
    --device-uid   <uid>

# 也可以用 ENV
export FM_SERVER_URL=http://your-host:5000
export FM_DEVICE_TOKEN=...
export FM_DEVICE_UID=...
./run.sh
```

### 三、远程命令（伪终端）

在设备详情页的 **远程终端** 区域键入命令 → 后端把命令包装成一条 `exec` 任务 → agent 长轮询到任务 → 用 `bash -c <cmd>` 执行 → `stdout` / `stderr` / `exit_code` 全部回传 → 浏览器侧轮询任务状态 → 命令终态时把输出渲染出来。

- 上下方向键可以翻历史命令；
- `cwd` 可填工作目录，`timeout` 默认 30s（最大 600s）；
- `clear` 是前端伪命令，仅清空显示，不会下发到设备。

> 这是异步执行模型，不是真 PTY。如果需要交互式 shell，建议在设备上跑 SSH（或用 `ttyd` + 一个 `tar.gz` 服务暴露 web 终端）。

### 四、远程 tar.gz 服务

完全复刻本机服务托管的契约：归档内必须有 `run.sh`，agent 执行时调用 `bash run.sh <local_port> <run_args>`，注入 `LOCAL_PORT` / `PORT` / 自定义 env。

Web UI 「上传 tar.gz」 表单字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✓ | 服务名（同一设备下唯一） |
| `local_port` | – | 第一个位置参数；空表示不传 |
| `run_args` | – | 附加参数，按空格切（如 `--prod --workers=2`） |
| `env_json` | – | JSON 对象，merge 到 agent 启动 run.sh 的环境 |
| `archive` | ✓ | `.tar.gz` / `.tgz` |
| `auto_install` | – | 勾选时同时下发 install 任务（推荐） |

agent 端的实际动作：

1. 拉 `service_install` 任务 → `GET /api/agent/services/<sid>/archive` 下载 → sha256 校验 → `safe_extract_tarball` 解压到 `~/.fm_device_agent/services/<sid>/work/`。
2. `setsid` 起新进程组运行 `bash run.sh <port> <args>`，stdout/stderr 重定向到 `run.log`。
3. 把 pid 写文件 + 上报。stop 时先 SIGTERM 整个进程组、5s 不退再 SIGKILL，避免遗留子进程。

### 五、最小可运行 demo

下面是一个能在任何机器上 1 分钟跑通的例子（假设服务器在 `http://127.0.0.1:5000`）：

```bash
# 1) 在浏览器上 /devices 创建 demo 设备, 拿到 TOKEN, 写到环境变量
export FM_SERVER_URL=http://127.0.0.1:5000
export FM_DEVICE_TOKEN=<paste>

# 2) 启动 agent（前台跑, Ctrl+C 退出）
cd clients/device_agent && ./run.sh

# 3) 在另一个终端做一个最小 tar.gz, 内含 run.sh
mkdir -p /tmp/demo-svc && cat > /tmp/demo-svc/run.sh <<'EOF'
#!/usr/bin/env bash
PORT="${1:-8000}"
exec python3 -m http.server "$PORT"
EOF
chmod +x /tmp/demo-svc/run.sh
tar -czf /tmp/demo.tar.gz -C /tmp/demo-svc run.sh

# 4) Web UI → 设备详情 → 上传 tar.gz → name=demo / local_port=8001 / archive=/tmp/demo.tar.gz / 勾选 auto_install
# 5) 几秒后回到列表, 服务状态应当变成 "running", agent 主机上 curl http://127.0.0.1:8001/ 能拿到目录列表
```

### 六、systemd 部署（推荐）

参考 `clients/device_agent/README.md` 末尾的 `fm-agent.service` 单元文件模板。常用命令：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fm-agent
sudo journalctl -u fm-agent -f
```

### 七、安全模型

- **token 即权限**：拥有 `device_token` 等价于能在这台设备上执行任意 shell 命令。请只把 token 分发给可信运维。
- **agent 运行身份**：agent 进程 == 远程命令身份 == 服务进程身份。建议用专用低权限账号跑（如 `useradd -r fm-agent`）。
- **解压防护**：tar.gz 解压拒绝绝对路径 / `..` / 链接逃逸；归档 `sha256` 在服务端记录，agent 下载后强校验，防中间人篡改。
- **进程组管理**：服务进程用 `start_new_session=True`，stop 时一次性 `SIGTERM` / `SIGKILL` 整个进程组。
- **token rotate**：UI 上「Token」按钮重置后旧 token 立刻 401，旧 agent 必须更新配置才能继续。

### 八、与本机服务托管 (`ManagedService`) 的关系

|         | 本机 ManagedService (`/services`) | 远程 DeviceManagedService (`/devices`) |
| :--- | :--- | :--- |
| 宿主 | file_manager 进程同机 | 远程设备 agent 进程同机 |
| 端口校验 | 必须在 `frpc.toml` 白名单 | 由设备 owner 自管（通常内网） |
| 启动方式 | 同步 `subprocess.Popen` | enqueue 任务 → agent 异步执行 |
| 日志查看 | 直接 tail 服务器侧 `run.log` | enqueue `service_log` → agent 上报 stdout |
| 状态权威 | 服务器进程表 | agent 通过任务结果 / `service_status` 上报 |

两套机制可同时使用：服务器自己跑高频对内服务、把"边缘 / 离用户更近"的能力下沉到 agent。

### 九、设备页 UI 折叠 / 隐藏 / FRPC 一键 SSH 登录

为了减少 `/devices` 与 `/services` 页面的视觉噪音，前端做了 3 个增强（所有状态都写到 `localStorage`，刷新页面后保留）：

1. **卡片折叠**：每张主卡片右上角自动出现 `▾ / ▴` 按钮，点击后整张 card-body 隐藏。具体来说：
   - `/devices`：注册表单（默认折叠）、设备列表、设备基本信息、硬件详情、FRPC 隧道、远程终端、设备服务，共 7 张卡片
   - `/services`：资源监控、上传 tar.gz、我的服务、服务详情、运行日志，以及 Docker tab 下的对应卡片
   - 折叠状态以 `data-collapse-key` 为索引保存到 `localStorage('fm.cardCollapsed.v1')`
   - 实现见 `app/static/js/collapse.js`，任何新模板只要在 card 上加 `data-collapsible="1"` 和 `data-collapse-key="<unique>"` 就自动接管
2. **左侧设备栏可隐藏**：`/devices` 右上角的「隐藏设备列表」按钮把左侧 `col-lg-4`（搜索 + 注册新设备 + 设备列表）整体收起，右侧详情区从 `col-lg-8` 升级成 `col-12` 充满屏幕。
3. **设备 frpc 隧道 & 一键网页 SSH**：
   - 设备详情区有「设备 frpc 隧道」卡片，**数据来源于 agent 心跳**：每次 `collect_stats` 都会在设备本机用 `psutil`（回退到 `ps -eo pid=,args=`）扫所有 frpc 进程，从 `-c <path>` 抽 toml，再用 `tomllib` 解析（Python 3.10 兜底纯文本 parser），把结果塞进 `stats['frpc_tunnels']` 一起上报
   - 服务器侧 `GET /api/devices/<id>/tunnels?user=<ssh_user>` 只读 `device.stats_json` 里的字段，按 device owner 鉴权，再派生 `is_ssh` / `ssh_command` / `external_url`
   - 左边选中某台设备 → 卡片自动刷新该设备的隧道，未选设备时为空。每条 proxy 显示「本地端口 → 远端端口」；`localPort == 22` 的行高亮黄底 + 绿色「网页终端登录」按钮 + 可复制 ssh 命令
   - `ssh_command` = `ssh -p <remotePort> <user>@<serverAddr>` 直接用的是设备本机 frpc.toml 里的 `serverAddr`（外部入口），用户名输入框默认 `root` 本地持久化
   - 其他端口（`localPort != 22`）显示 `tcp://host:port` 提示，作为给这台**设备**部署 `tar.gz` 服务时的端口参考

   **常见坑：`-c frpc.toml` 用相对路径且 frpc 是 root 跑的**

   实际部署里非常常见的写法：`cd /opt/frp && sudo ./frpc -c frpc.toml`。此时:
   - frpc 的 cwd 是 `/opt/frp`、`-c` 后是相对路径 `frpc.toml`
   - agent 一般以非 root 用户跑，**读不到 `/proc/<frpc_pid>/cwd`**（root 拥有的符号链接）
   - 结果：agent 知道有 frpc 进程，但解析不到 toml → 隧道为空，卡片显示一条 warning

   三种解决方式任选其一：

   1. **改 frpc 启动命令为绝对路径**：`sudo ./frpc -c /opt/frp/frpc.toml` —— 此后即便 agent 读不到 cwd 也能解析
   2. **agent 用 root 跑**：`sudo bash run.sh ...`（这样 `/proc/<pid>/cwd` 直接能读）
   3. **给 agent 加 `--frpc-config <abs-path>` 或环境变量 `FM_AGENT_FRPC_CONFIG=<abs-path>`**：当 agent 检测到相对路径 + 读不到 cwd 时，自动用这个绝对路径作 fallback。例：

      ```bash
      bash run.sh \
        --server-url http://your-server:5000 \
        --frpc-config /home/yiye/bin/frp_0.63.0_linux_amd64/frpc.toml
      ```

      或在 systemd 里：
      ```ini
      Environment=FM_AGENT_FRPC_CONFIG=/etc/frpc.toml
      ```

   UI 在 fallback 命中时会显示 `fallback toml` 灰色徽章，方便确认。

4. **网页 SSH 终端 (xterm.js + paramiko + WebSocket)**：
   - 隧道卡片里 22 端口的行新增了「网页终端登录」按钮，点击直接弹出全屏 modal、加载 `xterm.js`，无需本机有 ssh 客户端，也不依赖 `ssh://` URL handler
   - 登录目标是**被管设备**：`host` 填的是该设备 frpc 上报的 `serverAddr`，`port` 填的是 `remotePort`（即 frpc 在公网入口为这台设备的 SSH 暴露的外部端口）——file_manager 服务器只是 paramiko 客户端转发代理，最终 TCP 走到 `serverAddr:remotePort` 即设备的 22
   - 登录表单：`host` / `port` / `username` / `password`（自动从隧道信息预填）；展开「使用私钥 (PEM)」可粘贴 OpenSSH/RSA/ECDSA/Ed25519/DSS 私钥 + 可选 passphrase
   - **认证 fallback**：很多 sshd（尤其 Ubuntu / RHEL）默认走 PAM + `KbdInteractiveAuthentication`，paramiko 的 `auth_password` 方法会被服务器返回 `BadAuthenticationType('keyboard-interactive')` 直接拒掉——这是一个常见坑。后端做了自动 fallback：
     1. 先 `auth_password(username, password)`
     2. 失败 + 服务器声明支持 `keyboard-interactive` → 自动 `auth_interactive`，把同一份密码塞进每个 prompt（典型场景里 sshd 只问一个 `Password:`）
     3. 都失败才向前端抛错，错误里会带 paramiko 异常类型，方便你判断到底是密码错还是策略不接 (`[BadAuthenticationType]: 服务器拒绝密码认证: 仅允许 ['publickey']; 请改用私钥登录`)
   - 已知**未覆盖**的认证场景：
     * 真正的 2FA / OTP（kbd-interactive 问"Password:"后再问"Verification code:"）—— Web 终端只能填一个密码, 第二步会被填同样的值导致失败. 想用的话先登录到设备改 sshd 配置或用 publickey
     * Kerberos / GSSAPI 认证
   - 服务端流程（依赖 `paramiko>=3.0` 和 `flask-sock>=0.7`）：
     1. `POST /api/ssh/sessions` 把凭据存到进程内 `_SESSION_STORE`（仅内存，5 分钟未消费即过期；用户隔离），返回 `sid`
     2. 浏览器打开 `ws(s)://<host>/api/ssh/ws/<sid>?cols=&rows=`
     3. 服务端用 `pop_session(sid, user_id)` 一次性消费凭据 → `paramiko.SSHClient.connect` + `invoke_shell` → 起后台线程把 `chan.recv` 转 ws，主线程把 ws JSON 输入转 `chan.send`
     4. modal 关闭 / WebSocket 断开时 `chan.close()` + `client.close()`
   - WebSocket 协议（全部 JSON 文本帧）：
     - C→S: `{"type":"input","data":"…"}` / `{"type":"resize","cols":N,"rows":N}` / `{"type":"ping"}`
     - S→C: `{"type":"status","status":"connected","msg":"…"}` / `{"type":"data","data":"…"}` / `{"type":"error","msg":"…"}` / `{"type":"closed","code":?,"msg":"…"}`
   - 安全约束：
     - 凭据**不落盘、不进日志**（日志只打 host/port/user，不打 password）；`atexit` 进程退出时 wipe store
     - `sid` 一次性消费（`pop_session`），无法重放；不同 user 的 `sid` 互不可见
     - WebSocket 入口手动做 `session['user_id']` 检查，没登录直接 close
   - 调试不依赖前端：直接在 Python 里用 `from app.services.ssh_bridge_service import create_session; create_session(user_id=1, host='localhost', username='you', password='x')` 拿到 `sid`，再用 `wscat -c 'ws://localhost:5000/api/ssh/ws/<sid>?cols=80&rows=24'` 测
   - 生产部署要点：
     - Flask 内置 dev server 已经能跑 WebSocket（`flask-sock` 走 WSGI + 后台线程），生产建议 `gunicorn --worker-class gthread --threads 16 run:app`（**不要**用 `gevent` worker 除非全栈 monkey-patch）
     - 反向代理（nginx/traefik）记得对 `/api/ssh/ws/` 路径开启 `Upgrade: websocket`、`proxy_read_timeout` 调大（默认 60s 会中断长连接）

## 设备 → 服务器 自动同步（Android 端）

代码位于 `test-update-apks/android-app/`：

- `sync/DeviceFileScanner.kt`：扫描 DCIM/Pictures/Movies/Music/Download/Documents（不递归 sdcard 全盘），可中断、含异常兜底。
- `sync/DeviceSyncManager.kt`：按 `(relativePath, size, mtime)` 增量上传；切到 `Dispatchers.IO` 避免主线程 ANR；进度回调 200ms 节流；上传成功立即 persist 索引。
- `sync/SyncIndex.kt`：JSON 持久化的本地索引，含三类数据：
  - `uploaded`：已成功条目签名；
  - `failed`：失败原因 + 次数（仅记录，不阻止下次重试）；
  - `history`：最近 20 次同步 `Summary`。
- `sync/SyncWorker.kt`：WorkManager 走 UNMETERED（WiFi）网络的后台前台服务任务。
- `sync/WifiAutoSync.kt`：监听网络回调，达到条件自动入队。
- UI：`ui/AppNav.kt` 提供「立即同步」按钮、进度面板、同步历史对话框、远端图片/视频缩略图（Coil 加载 `${baseUrl}/api/thumbnail/<id>`）。

构建：

```bash
cd test-update-apks/android-app
./gradlew :app:assembleDebug
```

## 文件夹插件 · 使用流程

> 适用场景：把"文件夹 + 文件" 一键变成一个面向客户端的 mini API。典型例子是**英语单词发音**——文件夹下放 `hello.mp3` `world.mp3` ...，给一个外部客户端按单词查询接口即可拿到对应音频 URL。

### 步骤 1：在 UI 里创建插件

1. 进入文件管理页面，**右键**目标文件夹（或网格 ⋮ 菜单 / 列表"插件"按钮）→ **「插件管理…」**
2. 在弹出的对话框下半部分填表单：
   - **插件名**：同一文件夹下唯一，URL 寻址用。如 `word_audio`
   - **类型**：`declarative`（JSON 规则，推荐）或 `python`（自定义代码）
   - **配置 / 源码**：默认会填一个"按文件名查单词"的样例，照着改即可
   - **递归子目录**：插件 ctx 列文件时是否走子树
   - **启用**：软开关
3. 点「保存插件」，然后在底部「调用测试」面板填 `action=lookup` `params={"word":"hello"}`，点「调用」即时看到响应。

### 步骤 2：客户端调用

**最易记的端点**（按文件夹 id + 插件 name 寻址）：

```bash
curl -X POST http://127.0.0.1:5000/api/external/folders/123/plugins/word_audio/invoke \
  -H "X-API-Key: $FILEMANAGER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action": "lookup", "params": {"word": "hello"}}'
# -> {"success": true, "word": "hello", "file_id": 35,
#     "url": "http://127.0.0.1:5000/api/external/download/35", ...}
```

拿到 `url` 后再带同一个 `X-API-Key` 头下载即可。

### 步骤 3：声明式 vs Python 形态对照

**声明式**（`config` 字段，JSON）：

```json
{
  "actions": {
    "lookup": {
      "params": [{"name": "word", "required": true, "transform": "lower"}],
      "match": {
        "strategy": "filename",
        "patterns": ["{word}.mp3", "{word}-us.mp3"],
        "case_insensitive": true,
        "recursive": true
      },
      "response": {
        "success": true, "word": "{word}",
        "file_id": "{file.id}", "url": "{file.url}", "name": "{file.name}"
      },
      "not_found": {"success": false, "message": "word not found: {word}"}
    }
  }
}
```

支持的匹配策略：`filename`（精确名匹配）、`glob`（fnmatch 通配）、`list`（直接返回文件列表）。
占位符语法：`{paramName}` 来自请求 params，`{file.id}` / `{file.name}` / `{file.url}` / `{file.size}` 来自命中文件。整串就是单个占位符时保留原类型（数字仍是数字）。

**Python 代码**（`code` 字段）：

```python
def invoke(action, params, ctx):
    if action == 'lookup':
        word = (params.get('word') or '').strip().lower()
        f = ctx.find_file(basename=word, ext='mp3')
        if not f:
            return {'success': False, 'message': 'word not found: ' + word}
        return {
            'success': True, 'word': word,
            'file_id': f['id'], 'url': f['url'], 'name': f['name'],
        }
    return {'success': False, 'error': 'unknown action: ' + str(action)}
```

`ctx` 提供的接口：

- `ctx.folder_info` —— `{'id', 'name', 'path', 'user_id'}` 只读视图
- `ctx.list_files(recursive=None)` —— 列文件（不含目录），默认遵循 `plugin.include_subdirs`
- `ctx.find_file(name=, basename=, ext=, glob=, recursive=, case_insensitive=)` —— 找首条
- `ctx.find_files(...)` —— 找全部
- `ctx.get_file_url(file_id)` —— 返回 `/api/external/download/<id>` 完整 URL
- `ctx.log(msg)` —— 写到 server 日志

> ⚠️ **安全模型**：Python 插件用 `exec` 执行用户代码，仅做 builtins 限制不是真沙箱。只有 plugin owner 本人（用自己的 API Key 或 session）能调用。如果你需要把插件暴露给第三方调用，**请只用 declarative 形态**。

## 单词发音 demo（端到端示范）

> 准备一个"英语单词美音" 文件夹 → 下载常用 5000 词的美式真人发音 → 上传 → 配置插件 → 客户端按单词查 → 拿到 URL 下载

### 1. 准备 API Key

登录后在「我的页面」生成 API Key（或读 `User.api_key`），导出环境变量：

```bash
export FILEMANAGER_API_KEY=xxxxxxxxxxxx
export FILEMANAGER_BASE_URL=http://127.0.0.1:5000
```

### 2. 下载发音 + 上传（`scripts/seed_word_audio.py`）

```bash
# 先 smoke test 50 词, 验证流程
./yenv/bin/python scripts/seed_word_audio.py --limit 50

# 全量 5000 词（耗时较长, 30-60 分钟）
./yenv/bin/python scripts/seed_word_audio.py --limit 5000
```

脚本会：

1. 拉 [google-10000-english](https://github.com/first20hours/google-10000-english) 词表前 N 个，缓存到 `data/common_5000_words.txt`
2. 依次试每个音频源（用 `--prefer` 控制顺序，默认 `dict-api,oxford`）：
   - **dict-api** — [Free Dictionary API](https://dictionaryapi.dev/), 取 `phonetics[].audio` 里带 `-us.mp3` 的美式真人录音 (CC BY-SA 4.0)。快, 但 5000 高频词里约 20% 的核心词 (`to`/`for`/`is`/`with`/`be`/`are` 等) 没有 US 录音。
   - **oxford** — 抓 [Oxford Learner's Dictionaries](https://www.oxfordlearnersdictionaries.com/) 词条页 HTML 里 `pron-us` 标签的 `data-src-mp3`, 几乎全覆盖。脚本默认每词 ~1.2s 请求, 不要在多机并发跑。
   - **wikimedia** (可选, `--use-wikimedia`) — `En-us-<word>.ogg`, 需要 `ffmpeg` 转 mp3。
3. 命中的写到 `data/word_audio_cache/<word>.mp3`；**每个源单独记 miss** (`_misses_dict-api.txt` / `_misses_oxford.txt`...) ，下次只对未试过的源补抓。`--retry-misses` 可强制重试所有历史 miss。旧版统一 `_misses.txt` 会自动迁移成 `_misses_dict-api.txt`。
4. 调 `/api/external/folder` 幂等创建目标文件夹（默认名 `英语单词美音`）
5. **上传前去重**：调 `/api/external/files/<folder_path>` 拿到远端已存在文件名，本地待传列表里同名的直接跳过 —— 重跑脚本时只补传新增 mp3，不浪费带宽。`--overwrite` 强制覆盖；列远端失败时降级为全量上传。
6. 调 `/api/external/folder-upload` 批量上传

支持的 flag：`--limit / --wordlist / --prefer / --folder-name / --folder-id / --use-wikimedia / --retry-misses / --skip-download / --skip-upload / --overwrite / --force-upload / --dry-run`。

> 典型场景：先 `--prefer dict-api,oxford` 跑一遍（dict-api 快速覆盖 ~80%），再 `--prefer oxford --retry-misses` 用 Oxford 补完剩下的 ~20% 高频词。

### 3. 在 UI 创建 `word_audio` 插件

打开「英语单词美音」文件夹的「插件管理…」，粘贴上面的 declarative JSON 或 Python 代码，保存。

### 4. 客户端调用（`scripts/demo_word_plugin.py`）

两种模式：

```bash
# 默认: API Key 模式 — 调用和下载都带 X-API-Key
./yenv/bin/python scripts/demo_word_plugin.py --word hello

# 公开模式: 插件需先在 UI 上勾选「公开访问」, 然后任意客户端都能用 (浏览器/curl)
./yenv/bin/python scripts/demo_word_plugin.py --public --folder-id 38 \
    --plugin-name word_audio --word hello

# 批量
./yenv/bin/python scripts/demo_word_plugin.py --words hello world example --out-dir ./out_audio

# 仅打印 URL 不下载
./yenv/bin/python scripts/demo_word_plugin.py --word hello --no-download
```

> **公开模式如何工作**：插件 `public=True` 时, `/api/public/folders/<id>/plugins/<name>/invoke`
> 无需任何鉴权。响应里的 `url` 形如
> `/api/public/download/<file_id>?p=<plugin_id>&sig=<hex16>` —— 签名绑定 `(plugin, file)`，
> 用 user.api_key 作密钥，关闭开关或重置 API Key 后所有旧链接立即 403。

输出示例：

```
目标文件夹: 已存在 id=10 path='Ying_Yu_Dan_Ci_Mei_Yin'

=== 调用插件 ===
  base_url:    http://127.0.0.1:5000
  folder_id:   10
  plugin_name: word_audio
  action:      lookup

[hello] hit: http://127.0.0.1:5000/api/external/download/35
  ✓ saved ./out_audio/hello.mp3 (12345 bytes)

== Summary == ok=1 miss=0 err=0
```

## MusicFree 安卓 / 桌面播放器对接

[MusicFree](https://github.com/maotoumao/MusicFree) 是开源的 Android / Desktop 音乐播放器，通过 CommonJS 插件接入任意自定义音源。本仓库自带一个对接插件 `app/static/musicfree/file-manager.js`，把 file_manager 里 `file_type=audio` 的文件（mp3 / wav / ogg / flac / aac / m4a）当作音源暴露给 MusicFree。

### 分享白名单（开关 + 文件夹勾选）

设计上 **默认拒绝**，必须用户主动开启并选定目录才会有内容暴露给 MusicFree：

- `User.musicfree_enabled`（**总开关**）：关闭时所有 `/api/music/*` 一律返回 403
- `File.music_shared`（**目录白名单**）：只有标记 `True` 的目录及其下音频被列出 / 可拉流
- `/api/music/track/<id>/source` 是 `getMediaSource` 的中间层，做完两层校验后才返回最终下载 URL + headers，防止越权拉流

UI 入口：顶栏「MusicFree 分享」按钮 → 弹出配置 modal，里面：
- 总开关
- 含音频目录清单（每行显示音频数 + 路径），勾选要分享的
- 「全选 / 全清 / 刷新」快捷按钮
- 顶栏 badge 实时显示当前已分享目录数（开启但未选时显示"开"以警示用户）

### 配置 API（`/api/music-share/*`，登录态）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/music-share/config` | 返回 `{enabled, shared_folder_ids, shared_count}` |
| PUT | `/api/music-share/config` | 一次提交 `{enabled, shared_folder_ids[]}`；非本人目录会被过滤进 `invalid_ids` |
| GET | `/api/music-share/folders` | 列出当前用户所有含音频的目录（含 `audio_count`、`shared`） |
| POST | `/api/music-share/folders/<id>/toggle` | 单目录翻转或显式设置 `{shared: bool}` |

### MusicFree 适配接口（`/api/music/*`，X-API-Key / `?api_key=`）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/music/info` | 无需鉴权；带 key 时附带 `musicfree_enabled` / `shared_folder_count` 状态便于客户端排错 |
| GET | `/api/music/search?q=&page=&per_page=&type=music\|sheet` | 模糊搜（仅命中白名单内目录及其音频） |
| GET | `/api/music/sheets?page=&per_page=` | 列出白名单目录作为"歌单" |
| GET | `/api/music/sheet/<folder_id>` | 歌单详情；目录不在白名单返回 404 |
| GET | `/api/music/track/<file_id>` | 单曲信息；不在白名单返回 404 |
| GET | `/api/music/track/<file_id>/source` | **核心**：返回 `{url, headers}` 给插件 `getMediaSource` 透传，做完白名单校验后才发出 |
| GET | `/api/music/track/<file_id>/lyric` | 同目录同名 `.lrc` 内容；无则 `{"rawLrc": ""}` |

返回字段严格遵循 MusicFree 协议（`IMusicItem` / `IMusicSheetItem`），实际播放复用 `/api/external/download/<id>`。

### 安装插件到 MusicFree

1. 在浏览器打开 `http://<你的服务地址>/static/musicfree/file-manager.js` 确认能下载
2. 在 MusicFree「设置 → 插件管理 → 从网络安装」中粘贴该 URL，点击确认
   - 也可下载 js 文件后用「从本地安装」
3. 安装完进入「插件设置」，在用户变量里填两项：
   - `baseUrl` — file_manager 服务地址，如 `http://192.168.1.100:5000`（**不要带末尾斜杠**）
   - `apiKey` — 在 file_manager 用户中心生成的 API Key
4. **在 file_manager Web 顶栏点「MusicFree 分享」**：
   - 打开总开关
   - 在目录列表里勾选要让 MusicFree 看到的文件夹
   - 保存
5. 完成后即可：
   - 在「探索 → 推荐歌单」看到刚才勾选的目录
   - 在「搜索」栏切到 `FileManager` 平台搜歌（仅在已分享目录内匹配）
   - 点击播放，插件 `getMediaSource` 会先调 `/api/music/track/<id>/source`，后端做白名单校验后返回 `{url, headers}`，MusicFree 带 `X-API-Key` 头去 `/api/external/download/<id>` 拉流

### 端到端调通（Node 跑插件）

发版前我用 Node 20 模拟 MusicFree 注入的 `env.getUserVariables()`，把插件 `require` 进来逐个方法跑：

```bash
./yenv/bin/python -c "from app import create_app; create_app().run(port=5099, use_reloader=False)" &
node - <<'EOF'
globalThis.env = { getUserVariables: () => ({ baseUrl: 'http://127.0.0.1:5099', apiKey: process.env.FM_API_KEY }) };
const p = require('./app/static/musicfree/file-manager.js');
(async () => {
  console.log(await p.search('he', 1, 'music'));
  const sheets = await p.getRecommendSheetsByTag('all', 1);
  const info   = await p.getMusicSheetInfo(sheets.data[0], 1);
  const src    = await p.getMediaSource(info.musicList[0], 'standard');
  console.log(src.url, src.headers);  // MusicFree 内部播放器会带 headers 去拉 url
})();
EOF
```

### 注意事项与故障排查

- **Hermes 引擎限制**：插件代码不能使用 `async () => {}` 箭头 async 函数，只能用 `async function ()`；本仓库的 `file-manager.js` 已规避
- **CORS**：MusicFree 客户端不走浏览器, 不受 CORS 影响; 但若用 Web 版调试请确保后端允许跨域
- **公网访问**：建议套 https 反代（caddy / nginx），把 `baseUrl` 填成 https 地址
- **歌词扩展**：若想给某首音频带歌词，把同名 `.lrc` 文件（如 `hello.lrc`）放进同一文件夹即可
- **更新插件**：MusicFree 的「检查更新」会拉 `srcUrl` 重新下载；本插件默认 `srcUrl` 写的是占位符 `<host>:<port>`，自部署时可改成你实际的公网 URL 后再分发

## 维护脚本

- `scripts/cleanup_duplicate_folders.py`：把 `parent_id IS NULL` 的孤儿挂回根目录、合并 `(user_id, parent_id, name)` 重复目录；`--prune-empty-dups` 还会清空物理目录。
- `scripts/cleanup_orphan_files.py`：递归清理两类 File 表脏数据
  - `--mode null-parent` (默认) — `parent_id IS NULL` 子树
  - `--mode missing-physical` — 磁盘不存在对应路径的"反向孤儿"
  - `--mode all` — 串行跑两类；`--dry-run` 先看影响范围
- `scripts/seed_word_audio.py`：下载 + 上传 5000 词美式发音（见上一节）
- `scripts/demo_word_plugin.py`：单词发音查询客户端 demo（见上一节）
- `scripts/test_file_manager_api.py`：登录会话、文件列表、上传探测等冒烟测试

```bash
./yenv/bin/python scripts/cleanup_duplicate_folders.py --dry-run
./yenv/bin/python scripts/cleanup_orphan_files.py --mode all --dry-run
./yenv/bin/python scripts/test_file_manager_api.py --user <name> --password <pwd>
```

## 测试

```bash
./yenv/bin/python -m pytest tests/
```

## License

Internal / Personal use.
