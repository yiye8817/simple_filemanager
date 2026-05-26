# file_manager 设备客户端 (device_agent)

把任意 Linux/macOS 主机变成 file_manager 「设备管理」页面里的一台远程设备。

## 能做什么

* **自助注册** (v0.2+)：只要给 agent 一个 `--server-url`，它会自己生成持久化的 `device_uid`，调 `/api/agent/enroll` 拿到 token 写到本地——不需要先去 Web UI 创建设备。
* **详细资源上报**：每隔 N 秒把以下指标完整上报给服务器，Web UI「设备详情」的硬件标签页会逐项渲染——
  - CPU：型号 / 物理 + 逻辑核心数 / 当前 + 最大 + 最小频率 / 每核占用率 / 整体使用率 / 上下文切换 + 中断数 / loadavg；
  - 内存：物理 + Swap 的 total / used / available / percent；
  - 磁盘：每个分区的挂载点 / 设备 / 文件系统 / 容量 / 已用 / 可用 / 使用率，并通过 `lsblk` 补上硬盘**型号 + HDD/SSD 标识**；
  - GPU：`nvidia-smi` 一次查 driver / 利用率 / 显存 / 温度 / 功耗 / 风扇 / SM 与显存频率；
  - 网卡：每张网卡的 name / MAC / IPv4 / IPv6 / UP-DOWN / 链路速率 / MTU；
  - 系统：hostname / OS PRETTY_NAME / kernel / arch / Python / Agent 版本 + PID / 启动时间 / `/etc/os-release` 全量字段。
* **远程命令**：服务端在 Web UI 「设备 → 远程终端」里输入命令，agent 拉到任务后用 `bash -c` 执行，stdout / stderr / exit code 一起回传。
* **远程服务托管**：服务端上传 `tar.gz`，agent 拉下来安全解压，按 `bash run.sh <port> <run_args>` 拉起，支持 start / stop / log / delete。
* **内网穿透感知**：可以在配置里塞一段 `tunnel_info` JSON（例如 frp 的 remote host:port），Web UI 会显示出来。
* **token 自动恢复**：服务器侧 rotate-token 后，agent 收到 401 会自动用本地 `device.uid` 重新 enroll 拿新 token。

## 通信模型

只有客户端 → 服务器的 **出向** HTTP 请求，对 NAT / 防火墙完全透明：

```
+--------+   POST /api/agent/register   +-----------------+
| agent  | ---------------------------> | file_manager    |
|        |   POST /api/agent/heartbeat  |  /api/agent/*   |
|        | <- 心跳 + 资源 ----- 30s --> |  长轮询任务队列 |
|        |   GET  /api/agent/poll       |                 |
|        | <- pending 任务 ------------ |                 |
|        |   POST /api/agent/tasks/<id>/result -----> 入库
+--------+                              +-----------------+
```

## 快速开始 (v0.2+：自助 enroll, 一行命令)

```bash
# 把整个目录拷过去 (或 git clone 整个 file_manager 仓库, 用 clients/device_agent/ 即可)
scp -r clients/device_agent/  user@your-host:/opt/fm-agent/
ssh user@your-host

cd /opt/fm-agent
python3 -m pip install -r requirements.txt  # 可选, 不装也能跑

# 一行启动: 只配服务器地址, agent 自动生成 device_uid + 自助 enroll
./run.sh --server-url http://your-host:5000
```

`run.sh` 第一次跑会在 `~/.fm_device_agent/` 下落两个文件：

* `device.uid` — 持久化的设备 UID（hostname + MAC 的 sha1 截断；权限 `0600`），同一台机器重装 agent 还是同一个 UID，服务器端会复用同一条记录。
* `device.token` — enroll 后服务器分配的 token，权限 `0600`。

启动后 1~2 秒就能在 Web UI [设备管理页](http://127.0.0.1:5000/devices) 看到设备出现并变成「在线」🟢，CPU / 内存 / 磁盘 / GPU / 网卡的详细数据立刻渲染出来。

> **公网部署**：服务器端把 `DEVICE_AGENT_OPEN_ENROLL=0` + `DEVICE_AGENT_ENROLL_TOKEN=<随机串>`；agent 端加 `--enroll-token <同样的随机串>` (或 `FM_ENROLL_TOKEN=...`)。否则任何人扫到 `/api/agent/enroll` 都能注册一台设备。

### 老路径：预先创建设备 + 显式 token

如果你想精确控制 UID / 设备归属，仍然可以走老流程：在 Web UI 点 **「创建设备」**，拿到一次性显示的 `device_uid` 和 `device_token`，然后：

```bash
# 方式 A: CLI 参数
./run.sh \
    --server-url http://your-host:5000 \
    --device-token <粘贴 token> \
    --device-uid   <粘贴 uid>

# 方式 B: 环境变量 (推荐 systemd 部署)
export FM_SERVER_URL=http://your-host:5000
export FM_DEVICE_TOKEN=...
export FM_DEVICE_UID=...
./run.sh
```

只要 `--device-token` 或 `FM_DEVICE_TOKEN` 任意一个有值, agent 就跳过 enroll 直接用现成 token。

## CLI 参数

| 参数 | 默认 | 说明 |
| :--- | :--- | :--- |
| `--server-url` | (必填, 或 `FM_SERVER_URL`) | file_manager 服务器 base URL |
| `--device-token` | (`FM_DEVICE_TOKEN`) | 可选, 不填则自助 enroll |
| `--device-uid` | (`FM_DEVICE_UID`) | 可选, 不填则从 `workdir/device.uid` 读 / 自动生成 |
| `--enroll-token` | (`FM_ENROLL_TOKEN`) | 服务器要求的 `X-Enroll-Token`, 公网部署用 |
| `--workdir` | `~/.fm_device_agent` | 本地存放服务解压目录 / 日志 / `device.uid` / `device.token` 的位置 |
| `--heartbeat-interval` | `20` | 心跳间隔秒数 |
| `--poll-wait` | `20` | 长轮询单次等待秒数（受服务端 25s 上限约束） |
| `--no-verify-tls` | False | HTTPS 不校验证书 (自签场景) |
| `--tunnel-info` | "" | JSON 字符串, 写入心跳的 `tunnel_info` 字段 |
| `--frpc-config` | (`FM_AGENT_FRPC_CONFIG`) | 可选: frpc.toml 绝对路径; 当 frpc 用相对路径 `-c` 且 agent 读不到其 cwd（典型: frpc 是 root 跑的，agent 是普通用户）时作为兜底, 让 agent 仍能把隧道上报给服务器 |
| `--log-level` | `INFO` | DEBUG / INFO / WARNING / ERROR |

环境变量对应（CLI 优先）：`FM_SERVER_URL` / `FM_DEVICE_TOKEN` / `FM_DEVICE_UID` / `FM_ENROLL_TOKEN` / `FM_AGENT_WORKDIR` / `FM_TUNNEL_INFO` / `FM_AGENT_FRPC_CONFIG`。

### 自动采集 frpc 隧道

agent 每次心跳都会扫本机的 `frpc` 进程 + 解析 `-c <path>` 指向的 toml，把 `[[proxies]]` 列表塞进 `stats.frpc_tunnels`。服务器侧 `/api/devices/<id>/tunnels` 用这份数据展示「设备 frpc 隧道」卡片；`localPort=22` 的会被识别为 SSH 入口，Web UI 直接给「网页终端登录」按钮。

如果你看到隧道卡片显示一条 warning "`-c frpc.toml` 是相对路径, 但读不到 `/proc/<pid>/cwd`"，说明 frpc 是用 root（或别的 uid）跑的、agent 没权限读它的 cwd。任选其一修复：

1. 改 frpc 启动命令成绝对路径：`sudo ./frpc -c /opt/frp/frpc.toml`
2. 用 root 跑 agent：`sudo bash run.sh ...`
3. 给 agent 加 `--frpc-config /abs/path/to/frpc.toml` 兜底，或在 systemd 里写 `Environment=FM_AGENT_FRPC_CONFIG=...`

## 服务托管 (tar.gz)

设备服务的执行约定，与本机服务托管一致：

* 归档内必须有可执行的 `run.sh`；
* `run.sh` 的第一个参数是端口（创建时填的 `local_port`，留空时不会传）；
* 之后接你在 Web UI 填的 **附加参数**（`run_args`），按空格切分；
* 注入的环境变量：
  - `LOCAL_PORT` / `PORT` —— 端口；
  - `PYTHONUNBUFFERED=1`；
  - 你填的 `env_json` 全部 merge。

示例 `run.sh`:

```bash
#!/usr/bin/env bash
set -e
PORT="${1:-8000}"
shift || true
exec python3 -m http.server "$PORT" "$@"
```

打包：`tar -czf demo.tar.gz run.sh`，去 Web UI 「设备 → 上传 tar.gz」上传，勾选「立即下发 install 任务」即可。

## 安全模型

* **token 即权限**：拥有 token 等价于能在这台设备上执行任意 shell 命令，请妥善保管。
* **agent 运行身份**：agent 进程是谁，远程命令 / 服务进程就是谁；强烈建议用专用低权限账号。
* **解压防护**：tar.gz 解压会拒绝绝对路径、`..`、链接逃逸；归档 sha256 在服务端记录，下载后强校验。
* **进程组管理**：服务进程用 `start_new_session=True`，stop 时一次性 SIGTERM / SIGKILL 整个进程组，避免遗留子进程。

## systemd 单元模板

```ini
# /etc/systemd/system/fm-agent.service
[Unit]
Description=file_manager device agent
After=network-online.target
Wants=network-online.target

[Service]
User=fm-agent
WorkingDirectory=/opt/fm-agent
Environment=FM_SERVER_URL=http://your-host:5000
Environment=FM_DEVICE_TOKEN=xxxxxxxxxxxxxxxxxxxx
Environment=FM_DEVICE_UID=xxxxxxxxxxxxxxxxxxxx
ExecStart=/opt/fm-agent/run.sh
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fm-agent
sudo journalctl -u fm-agent -f
```

## 故障排查

| 现象 | 排查 |
| :--- | :--- |
| 启动报 `token 无效` | 在 Web UI「设备 → Token」重置一次, 拷新 token 进配置 |
| 一直离线 (UI 灰点) | 看 `agent.log` 是否周期打印 `heartbeat 失败`; 大概率是 server URL / 网络问题 |
| `命令不存在` | exec 任务默认走 `bash -c`, 但脚本里用了相对路径 / 没装的二进制 |
| `run.sh 启动后立即退出` | 服务端拉到的 stderr 会写到日志, 用 Web UI「日志」按钮看 tail |
| GPU 不显示 | 设备上没有 `nvidia-smi`; 这是预期的, 上报字段会留空 |
| 心跳里 `public_ip` 为空 | 设备主动出向 HTTPS 不通 (api.ipify.org / ifconfig.me); 不影响其它功能 |

## 与服务器侧 ManagedService 的区别

|         | 本机 ManagedService (`/services`) | 远程 DeviceManagedService (`/devices`) |
| :--- | :--- | :--- |
| 宿主 | file_manager 进程同机 | 远程设备 agent 进程同机 |
| 端口校验 | 必须在 `frpc.toml` 白名单里 | 由设备 owner 自己保证, 不强校验 (设备一般在内网) |
| 启动 | `subprocess.Popen` 同步 | enqueue 一个 `service_install` / `service_start` 任务, agent 拉到后异步执行 |
| 日志 | 直接 tail 服务器侧 `run.log` | enqueue `service_log`, agent 上报 stdout 才能看 |
| 删除 | 服务端清理本地目录 | 服务端 enqueue `service_delete` + 清归档; agent 收到后清解压目录 |

## 协议参考

| Method | Path | 说明 |
| :--- | :--- | :--- |
| POST | `/api/agent/register` | 启动一次, 等价于一次完整 heartbeat |
| POST | `/api/agent/heartbeat` | 周期性上报 `{hostname, os_name, os_version, arch, agent_version, local_ip, public_ip, stats, tunnel_info}` |
| GET | `/api/agent/poll?wait=20` | 长轮询拿任务; 返回 `tasks[].claim_token` 用于结果回写校验 |
| POST | `/api/agent/tasks/<id>/result` | 回传 `{status, exit_code, stdout, stderr, error, result, claim_token}` |
| GET | `/api/agent/services/<id>/archive` | 下载服务 tar.gz |
| POST | `/api/agent/services/<id>/status` | 主动上报服务状态变化 (watchdog 模式, 选用) |

所有接口都用 `X-Device-Token` 头鉴权, 404/401 都返回 JSON `{"error": "..."}`。
