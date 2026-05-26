# musicfree_server

把 file_manager 的「文件夹 Webhook」事件转换成 [MusicFree](https://github.com/maotoumao/MusicFree) 协议的**独立轻量服务**。设计目标：

- **解耦**：完全独立于 file_manager 进程，自带 JSON 持久化，没有数据库依赖
- **零侵入**：不需要碰 file_manager 的任何字段；只用现有的「文件夹 Webhook 监听」接口推送
- **可托管**：符合 file_manager「服务托管」契约（`bash run.sh <localPort>`），可以直接 tar 起来上传部署
- **可观测**：启动 banner、`/health`、`/`（HTML 欢迎页）都打印**引用方式**，复制就能用

```
              ┌─────────────┐  Webhook  ┌──────────────────┐  /api/music/*  ┌─────────────┐
   上传文件 → │ file_manager│ ────────→ │ musicfree_server │ ─────────────→ │ MusicFree   │
   或改动    └─────────────┘           └──────────────────┘                │ 客户端 (App) │
                                              ↓ JSON                       └─────────────┘
                                         state.json
```

## 目录结构

```
servers/musicfree_server/
├── app.py              # Flask 主入口 (含 Blueprint + /webhook/files + /api/music/*)
├── store.py            # 清单存储 (in-memory + JSON 持久化, 线程安全 + 节流 flush)
├── config.py           # 配置 (全部从环境变量读取)
├── requirements.txt    # 只依赖 Flask
├── run.sh              # 服务托管入口: 接受 $1=localPort, 自建 venv, 启动 Flask
├── README.md           # 你正在看的这份
└── tests/
    └── test_app.py     # 13 个 in-process E2E 测试 (webhook → 清单 → MusicFree 查询)
```

## 接口

### 接收 webhook (来自 file_manager)

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| POST | `/webhook/files` | 接收 file_manager 的 webhook payload（`event`/`watch`/`file` 三段式），自动 upsert 清单 |
| POST | `/webhook/test` | 本地构造一条 `created` 事件，调试用 |

Payload 字段与 file_manager 一致；body 形如：

```json
{
  "event": "created",
  "watch": { "id": 1, "folder_id": 300, "folder_name": "music", "folder_path": "music", "include_subdirs": true },
  "file": {
    "id": 555, "name": "song.mp3", "path": "music/song.mp3",
    "size": 4096, "file_type": "audio", "is_dir": false, "parent_id": 300,
    "download_url": "http://fm.local/api/download/555",
    "serve_url": "http://fm.local/api/serve/555"
  }
}
```

只有 `file_type=audio` 或扩展名 ∈ `{.mp3, .wav, .ogg, .flac, .aac, .m4a}` 的文件会被纳入清单；同目录 `.lrc` 文件会自动配对成歌词。

如果配了 `MFS_WEBHOOK_SECRET`，请求必须带 `X-Signature: sha256=<HMAC-SHA256>` 头。

### MusicFree 协议端点

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/music/info` | 探测端点（无需 token）；返回 platform / stats |
| GET | `/api/music/search?q=&page=&per_page=&type=music\|sheet` | 模糊搜文件 / 目录 |
| GET | `/api/music/sheets?page=&per_page=` | 列所有歌单（目录），按音频数倒序 |
| GET | `/api/music/sheet/<id>?page=&per_page=` | 歌单详情，返回 `musicList` |
| GET | `/api/music/track/<id>/source` | 返回 `{url, headers}` 给插件 `getMediaSource` 透传 |
| GET | `/api/music/track/<id>/lyric` | 同目录 `.lrc` 文本内容；无则 `{"rawLrc": ""}` |

字段名严格符合 MusicFree 协议（`IMusicItem` / `IMusicSheetItem`）。

### 管理 / 调试

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/` | HTML 欢迎页，含引用方式 + 当前清单统计 |
| GET | `/health` | JSON 健康检查 + `usage` 字段（webhook curl / plugin baseUrl / info URL） |
| GET | `/admin/state` | 完整内部清单 dump（含 tracks / sheets / stats） |
| POST | `/admin/clear` | 清空清单（仅允许 `127.0.0.1` 调用） |

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
| :--- | :--- | :--- |
| `MFS_PUBLIC_BASE_URL` | 空 | 自身对外可访问的 base URL，**只用于日志/欢迎页/health 输出引用方式**；不填则用请求的 host_url |
| `MFS_WEBHOOK_SECRET` | 空 | 配后强制要求 `X-Signature: sha256=...` 头，HMAC-SHA256(body, secret) |
| `MFS_STATE_PATH` | `./data/state.json` | 清单持久化文件路径 |
| `MFS_QUERY_TOKEN` | 空 | 配后 `/api/music/*` 都需要 `X-Music-Token` 头或 `?token=`；不配则公开 |
| `MFS_REWRITE_URL_FROM` / `MFS_REWRITE_URL_TO` | 空 | webhook 里 `download_url` 是 file_manager 内网地址时，用来改写成外网可达地址 |
| `MFS_FILE_MANAGER_API_KEY` | 空 | **单租户兜底**：给 `getMediaSource` 返回的 headers 注入 `X-API-Key`。**多租户模式推荐留空**，让每个客户端在自己的 MusicFree 插件「用户变量 → apiKey」里填，插件会以 `X-File-Manager-Key` 头透传过来。优先级: 客户端 header > query 参数 `?fm_key=` > 本环境变量 |
| `MFS_LOG_LEVEL` | `INFO` | 标准 logging level |

### file_manager API Key 的两种工作模式

MusicFree 客户端拉流时需要 `X-API-Key` 头（file_manager 才能放行 `/api/external/download/<id>`）。这个 key 怎么来，有两种部署姿势：

**A. 客户端管理（推荐，多租户）**

- musicfree_server **不配** `MFS_FILE_MANAGER_API_KEY`
- 每个使用本服务的 MusicFree 客户端都在自己的「插件设置 → 用户变量 → apiKey」里填自己的 file_manager API Key
- 插件 JS 在调 `/track/<id>/source` 和 `/track/<id>/lyric` 时通过 `X-File-Manager-Key` 头透传给本服务，本服务再原样塞回给 MusicFree 客户端的 `headers.X-API-Key`
- **服务端不持久化 API Key**（state.json 里也没有）；每个客户端互不可见对方的 key

**B. 服务端兜底（单租户）**

- 启动时设 `MFS_FILE_MANAGER_API_KEY=xxx`
- 客户端 `apiKey` 留空也能正常拉流
- 适合"只有你自己用这台服务"的家用场景

**优先级**：客户端 `X-File-Manager-Key` 头 > `?fm_key=` 查询参数（仅 curl 调试）> 服务端环境变量。`/track/<id>/source` 返回里有个 `_key_source` 字段告诉你这次用的是哪份 key（`client` / `query` / `server` / `none`）。

调试示例：

```bash
# 没配任何 key, 看 _warning
curl -s http://server:8765/api/music/track/42/source | jq

# 客户端模式: header 透传
curl -s -H "X-File-Manager-Key: $FM_KEY" http://server:8765/api/music/track/42/source | jq

# query 模式 (curl 调试)
curl -s "http://server:8765/api/music/track/42/source?fm_key=$FM_KEY" | jq
```

## 部署方式 A：直接跑

```bash
cd servers/musicfree_server
pip install -r requirements.txt   # 或: bash run.sh 8765 (自动建 venv)
export MFS_PUBLIC_BASE_URL=http://192.168.1.100:8765
export MFS_WEBHOOK_SECRET=topsecret
python app.py --port 8765
```

启动后日志末尾会打印类似：

```
======================================================================
musicfree_server 已启动
======================================================================
配置:
  public_base_url           = http://192.168.1.100:8765
  webhook_secret            = (已配置)
  state_path                = ./data/state.json
  music_query_token         = (未配置, /api/music/* 公开访问)
  file_manager_api_key      = (未配置, 播放 URL 不附带 X-API-Key)
  log_level                 = INFO
状态: tracks=0 sheets=0 events=0
======================================================================
【引用方式 1】 让 file_manager 把变化推到我:
  curl -X POST "$FM_BASE/api/folder-watches" \
      -H "X-API-Key: $FM_KEY" -H "Content-Type: application/json" \
      -d '{"folder_path":"music","url":"http://192.168.1.100:8765/webhook/files",
           "secret":"topsecret",
           "events":["created","modified","deleted"],
           "include_subdirs":true,"enabled":true}'

【引用方式 2】 让 MusicFree 客户端把我当成音源:
  插件 srcUrl  : <file_manager_base>/static/musicfree/file-manager.js  (来自 file_manager)
  baseUrl      : http://192.168.1.100:8765
  apiKey       : (可留空, 因为本服务没开 MFS_QUERY_TOKEN)

【健康检查】
  GET http://192.168.1.100:8765/health   ← 同时返回上面这些引导
  GET http://192.168.1.100:8765/         ← HTML 欢迎页
======================================================================
```

`GET /` 也是同一份信息的 HTML 版本，浏览器打开即可复制 curl 命令和 baseUrl。

## 部署方式 B：通过 file_manager 的「服务托管」托管

服务托管的契约是「打包 `tar.gz`、`bash run.sh <localPort>` 启动、用 frpc 暴露 remote_port」。本目录已经按这套写好了 `run.sh`，幂等创建 venv + 安装依赖 + 启动 Flask。

1. **打包**

   推荐用仓库自带的 [`servers/pack.sh`](../README.md#packsh--打包脚本)，会**自动排除运行时产物**（`.venv` / `__pycache__` / `data/` / `*.log` / `.env` 等）：

   ```bash
   cd servers
   ./pack.sh musicfree_server              # 产物在 servers/dist/musicfree_server.tar.gz
   ./pack.sh --dry-run musicfree_server    # 先看一眼会包含/排除什么
   ```

   也可以手工打包（但要小心别把虚拟环境一起塞进去导致 tar 几十 MB）：

   ```bash
   tar czf musicfree_server.tar.gz \
     --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
     --exclude='data' --exclude='*.log' \
     musicfree_server/
   ```

2. **在 file_manager 的 `instance/frpc.toml` 里**配置一对端口（参考主 README「服务托管」章节）

3. **上传 + 创建服务**

   ```bash
   curl -b cookies.txt -c cookies.txt \
     -F "name=musicfree_server" \
     -F "local_port=18765" \
     -F "remote_port=28765" \
     -F "archive=@musicfree_server.tar.gz" \
     http://127.0.0.1:5000/api/services
   ```

4. **启动**

   ```bash
   curl -b cookies.txt -X POST http://127.0.0.1:5000/api/services/<id>/start
   ```

   `run.sh` 会自建 `.venv` + `pip install Flask`（第一次几十秒；后续靠 `requirements.txt` 的 sha 校验跳过）。

5. **看运行日志**

   ```bash
   curl -b cookies.txt 'http://127.0.0.1:5000/api/services/<id>/log?lines=200'
   ```

   日志开头就是上面那段「引用方式」banner —— 把里面的 baseUrl 替换成 `http://<server>:<remote_port>`（即 `access_url`）就是 MusicFree 客户端应该填的地址。

   小贴士：在 file_manager 的 service-manager UI 里启动前可设置 `MFS_PUBLIC_BASE_URL=http://<server>:<remote_port>`，banner 直接显示对外地址，无需脑补。

## 完整端到端流程

```bash
# 1. 启动 musicfree_server (服务托管或直接跑都行), 假设它在 http://192.168.1.100:8765
# 2. 在 file_manager 给"音乐"文件夹挂 webhook 指向 musicfree_server:
curl -X POST http://<file_manager>/api/folder-watches \
  -H "X-API-Key: $FM_KEY" -H "Content-Type: application/json" \
  -d '{"folder_path":"music","url":"http://192.168.1.100:8765/webhook/files",
       "secret":"topsecret","include_subdirs":true,"events":["created","modified","deleted"]}'

# 3. 往 file_manager 的 "music" 文件夹里上传几首 mp3
# 4. musicfree_server 会自动收到 webhook, 更新内部清单
curl http://192.168.1.100:8765/api/music/sheets

# 5. 在 MusicFree 客户端「插件管理 → 从网络安装」填本服务自带的插件 URL:
#       http://192.168.1.100:8765/static/musicfree/file-manager.js
#    装好后插件「用户变量」里填:
#       baseUrl = (默认已注入, 留空也行)
#       apiKey  = 在 file_manager 用户中心生成的 API Key (多租户模式必填)
#       token   = (服务端开了 MFS_QUERY_TOKEN 时才填)
# 6. 现在 MusicFree 里搜索/浏览/播放, 全部走 musicfree_server
```

## 测试

```bash
cd servers/musicfree_server
python tests/test_app.py    # 13 个 in-process E2E
```

覆盖：webhook 接收 / 持久化 / sheets / sheet / search / source / lyric / HMAC 校验 / query token 鉴权 / URL 改写 / `.lrc` 配对 / 重启加载 / `/health` 引用方式 / HTML 欢迎页 / `/admin/clear` 仅本机。

## 与 file_manager 自带的 `music_bp` 的关系

file_manager 自身已经有一个 [`music_bp`](../../app/routes/music_routes.py)，在主进程里直接基于数据库列出音频。**musicfree_server 是个互补方案**，适用于：

- 想把"音乐查询"和"文件存储"放到不同进程 / 不同机器
- file_manager 在内网但 MusicFree 客户端在外网，需要一层"缓存 + URL 改写"
- 想用「服务托管」管理生命周期、看日志、统一启停
- 想让多个 file_manager 实例聚合到一个 MusicFree 音源里（多挂几条 webhook 就行）

如果你只在一台机器自用，可以直接用 `music_bp`；要分布式 / 网关化时用 `musicfree_server`。
