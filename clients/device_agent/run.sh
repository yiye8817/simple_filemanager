#!/usr/bin/env bash
# 一键启动 device_agent。
#
# 用法:
#   FM_SERVER_URL=http://your-host:5000 \
#   FM_DEVICE_TOKEN=...  \
#   bash run.sh
#
# 或者直接传 CLI 参数:
#   bash run.sh --server-url http://... --device-token ...
#
# 脚本会:
#   1. 优先用 ./venv/bin/python (若存在), 否则用系统 python3
#   2. 自动 pip install requirements.txt 里的两个可选依赖 (一次性, 失败也继续)
#   3. 把 stdout/stderr 重定向到 ./agent.log, 并 tail -f
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "${PYTHON_BIN}" ]; then
    if [ -x "./venv/bin/python" ]; then PYTHON_BIN="./venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then PYTHON_BIN="$(command -v python3)"
    else PYTHON_BIN="$(command -v python || true)"
    fi
fi

if [ -z "${PYTHON_BIN}" ] || [ ! -x "${PYTHON_BIN}" ]; then
    echo "[run.sh] 找不到 python3, 请安装 Python 3.9+ 或设置 PYTHON_BIN 环境变量" >&2
    exit 1
fi

if [ -f requirements.txt ] && [ ! -f .deps_installed ]; then
    echo "[run.sh] 安装可选依赖 (失败不影响启动)..."
    "${PYTHON_BIN}" -m pip install --user -q -r requirements.txt || true
    touch .deps_installed
fi

LOG_FILE="${LOG_FILE:-$(pwd)/agent.log}"
echo "[run.sh] python=${PYTHON_BIN}"
echo "[run.sh] log=${LOG_FILE}"
echo "[run.sh] args: $*"
exec "${PYTHON_BIN}" agent.py "$@" 2>&1 | tee -a "${LOG_FILE}"
