#!/usr/bin/env bash
# musicfree_server 服务托管入口脚本。
# file_manager 的 service_manager 用 `bash run.sh <localPort>` 启动。
# stdout / stderr 都会被重定向到 run.log。
set -e

PORT="${1:-8765}"

cd "$(dirname "$0")"

PYTHON="${PYTHON_BIN:-python3}"

# 1) 准备虚拟环境 (幂等; 已存在则跳过)
if [ ! -d ".venv" ]; then
    echo "[musicfree_server] 创建虚拟环境 .venv"
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
PYTHON=python  # 切到 venv 内的 python

# 2) 安装依赖 (幂等; 用 hash 文件加速二次启动)
REQ_HASH_FILE=".venv/.requirements.sha"
CUR_HASH=$(sha256sum requirements.txt | awk '{print $1}')
if [ ! -f "$REQ_HASH_FILE" ] || [ "$(cat "$REQ_HASH_FILE")" != "$CUR_HASH" ]; then
    echo "[musicfree_server] 安装依赖 ..."
    "$PYTHON" -m pip install --quiet --disable-pip-version-check -r requirements.txt
    echo "$CUR_HASH" > "$REQ_HASH_FILE"
else
    echo "[musicfree_server] 依赖已是最新, 跳过 pip install"
fi

# 3) 必要目录
mkdir -p ./data

# 4) 不带 BUFFERING + Flask 内置 server 即可; 单进程足够 webhook 量
export PYTHONUNBUFFERED=1
export FLASK_ENV="${FLASK_ENV:-production}"

# 5) 若用户没配 public_base_url, 帮 ta 兜底一份, 让日志里 baseUrl 不显示 "(未配置)"
if [ -z "${MFS_PUBLIC_BASE_URL:-}" ]; then
    # 这里只是给日志看; 服务托管对外其实是通过 frpc 暴露 remote_port
    export MFS_PUBLIC_BASE_URL="http://127.0.0.1:${PORT}"
fi

echo "[musicfree_server] 端口 ${PORT}, MFS_PUBLIC_BASE_URL=${MFS_PUBLIC_BASE_URL}"
echo "[musicfree_server] 提示: 真正对外的地址应该是 frpc 暴露的 remote_port (在 file_manager 服务列表查看 access_url),"
echo "    若与上面不一致, 重启前设置 MFS_PUBLIC_BASE_URL 即可。"
echo

# 6) 启动主进程 (前台运行, service_manager 会 setsid 起新进程组)
exec "$PYTHON" app.py --host 127.0.0.1 --port "${PORT}"
