# servers/

放置 file_manager 外挂的「独立服务」子项目，每个子项目一个目录。约定：

- 目录顶层必须有 `run.sh`，接受第一个位置参数 `<localPort>`（与 file_manager「服务托管」契约一致）
- 推荐顶层也放 `README.md` 说明依赖 / 端点 / 配置；放 `requirements.txt` 或 `package.json` 等依赖清单
- 任何运行时产物（虚拟环境、缓存、状态文件、日志）由 `run.sh` 自己在运行目录里生成，**不要提交到仓库**

当前子项目：

| 目录 | 作用 |
| :--- | :--- |
| [`musicfree_server/`](musicfree_server/README.md) | 把 file_manager 的 webhook 转成 MusicFree 协议的网关服务 |

---

## `pack.sh` — 打包脚本

把任一子项目打包成可上传到「服务托管」的 `tar.gz`，**自动排除运行时产物**（虚拟环境 / `__pycache__` / `data/` / `*.log` / `.env` / VCS 元数据 / 旧 `dist/*.tar.gz` 等）。

### 快速上手

```bash
cd servers
./pack.sh                       # 交互式: 列出可选, 输入序号 / "a" 全选
./pack.sh musicfree_server      # 直接指定一个或多个
./pack.sh --all                 # 全部打包
./pack.sh --list                # 只列出, 不打包; [✓] 表示就绪, [✗] 表示缺 run.sh
./pack.sh --dry-run musicfree_server  # 模拟一次, 看哪些文件会被 INCLUDE/EXCLUDE
./pack.sh --output /tmp/foo.tar.gz musicfree_server
./pack.sh --verbose musicfree_server   # 打包时打印每个被加入的文件
```

输出默认放在 `servers/dist/<name>.tar.gz`。归档顶层会带一层目录 `<name>/`，符合服务托管「顶层目录可选」的解压约定。

### 默认排除规则

集中维护在 `pack.sh` 顶部的 `EXCLUDES` 数组里。归类：

| 类别 | 模式 |
| :--- | :--- |
| Python 虚拟环境 | `.venv` `venv` `env` |
| Python 缓存 | `__pycache__` `*.pyc` `*.pyo` `*.pyd` `.pytest_cache` `.mypy_cache` `.ruff_cache` `.tox` |
| Node / 前端 | `node_modules` `.next` `dist-frontend` |
| 运行时产物 | `data` `logs` `log` `*.log` `*.log.*` `state.json` `*.sock` `*.pid` `run.log` |
| 旧打包产物 | `dist` `*.tar.gz` `*.tgz` `*.zip` |
| VCS / IDE | `.git` `.gitignore` `.gitattributes` `.svn` `.hg` `.idea` `.vscode` `.cursor` |
| OS 元数据 | `.DS_Store` `Thumbs.db` `desktop.ini` |
| 敏感配置 | `.env` `.env.local` `secrets.json` `credentials.json` |

如果你的项目有特殊需求，可以临时编辑该数组；要长期定制建议给子项目放一个 `.packignore` 之后再扩展脚本支持。

### `--dry-run` 输出示例

```
== dry-run: musicfree_server ==
  将包含: 7 个文件 (58.6 KiB)
  将排除: 7 个文件 (100.0 KiB) — 见下方按规则分类
  总计:   14 个文件 (158.7 KiB)

按顶层条目分组:
  INCLUDE  app.py                     18.6 KiB
  INCLUDE  config.py                   3.6 KiB
  EXCLUDE  data                         11.0 B   (命中排除规则)
  EXCLUDE  dist                          6.0 B   (命中排除规则)
  EXCLUDE  __pycache__                   0.0 B   (命中排除规则)
  INCLUDE  README.md                  10.3 KiB
  INCLUDE  requirements.txt             16.0 B
  EXCLUDE  run.log                       9.0 B   (命中排除规则)
  INCLUDE  run.sh                       1.9 KiB
  INCLUDE  store.py                   12.4 KiB
  INCLUDE  tests                      11.7 KiB
  EXCLUDE  .env                         17.0 B   (命中排除规则)
  EXCLUDE  .venv                      100.0 KiB  (命中排除规则)

关键完整性检查:
✓ run.sh 存在
✓ run.sh 可执行
✓ 找到依赖清单
```

### 打包后上传到「服务托管」

`pack.sh` 打包成功后会自动打印 curl 范例，按需替换端口和 host 即可：

```bash
curl -b cookies.txt -c cookies.txt \
  -F 'name=musicfree_server' \
  -F 'local_port=<frpc 中的 local_port>' \
  -F 'remote_port=<frpc 中的 remote_port>' \
  -F 'archive=@servers/dist/musicfree_server.tar.gz' \
  http://<file_manager_host>/api/services
```

### 可重现构建

把 `SOURCE_DATE_EPOCH` 设为你想固定的 mtime（秒），脚本会传 `--mtime=@$SOURCE_DATE_EPOCH --sort=name` 给 tar，相同源码会输出相同 sha256，便于供应链审计：

```bash
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) ./pack.sh musicfree_server
sha256sum dist/musicfree_server.tar.gz
```

### 退出码

| 码 | 含义 |
| :--- | :--- |
| 0 | 全部成功 |
| 1 | 有目标失败（参数错误、未选择等） |
| 2 | 参数解析失败 |
| 3 | 目标目录缺 `run.sh` |
| 4 | 归档生成后未发现 `run.sh`（理论上不会触发，除非排除规则配置错误） |
