#!/usr/bin/env bash
# file_manager 服务启动脚本
#
# 用法:
#   ./run.sh [PORT]
#     PORT  监听端口；缺省 5000。服务管理面板会把 frpc.toml 里的 localPort 作为第一个参数传进来。
#
# 行为:
#   1. 切到脚本所在目录（避免被通过软链/符号路径调用时 cwd 错位）
#   2. 选可用的 python（优先 python3）
#   3. 准备 venv：复用已有的 .venv / venv / yenv；否则在 .venv 自动创建
#   4. 按 requirements.txt 装依赖；用 md5 sentinel 避免每次启动都重装
#   5. exec 替换当前进程为 python，让父进程（setsid 启动）能直接 SIGTERM 控制

set -euo pipefail

PORT="${1:-5000}"

# ---- 0. cwd ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log() { printf '[run.sh] %s\n' "$*" >&2; }

# ---- 1. python ------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  log "未找到 python / python3，请先安装 Python 3.9+"
  exit 127
fi

# 简单版本校验（>= 3.9）
$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || {
  log "Python 版本过低（需要 >= 3.9），当前: $($PY -V 2>&1)"
  exit 1
}

# ---- 2. venv --------------------------------------------------------------
VENV_DIR=""
for cand in .venv venv yenv env; do
  if [ -x "$cand/bin/python" ]; then VENV_DIR="$cand"; break; fi
done
if [ -z "$VENV_DIR" ]; then
  VENV_DIR=".venv"
  log "创建 venv: $VENV_DIR"
  "$PY" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

# ---- 3. dependencies ------------------------------------------------------
if [ -f requirements.txt ]; then
  SENTINEL="$VENV_DIR/.requirements.md5"
  CUR_MD5="$(md5sum requirements.txt | awk '{print $1}')"
  if [ ! -f "$SENTINEL" ] || [ "$(cat "$SENTINEL" 2>/dev/null)" != "$CUR_MD5" ]; then
    log "安装依赖 ..."
    # 优先在线装；失败后尝试用现有环境继续（离线机器可能已预装依赖）。
    # 用较短 timeout / retries 避免卡死整个启动流程。
    if pip install --disable-pip-version-check -q \
         --timeout 15 --retries 1 \
         -r requirements.txt; then
      echo "$CUR_MD5" >"$SENTINEL"
    else
      log "pip 安装失败（可能网络不可用），尝试用现有环境继续启动"
    fi
  fi
fi

# ---- 4a. 媒体依赖预检（yt-dlp / aria2c, 给「从链接导入」用）---------------
# 跟 docker_diag 不同, 这两个我们会尝试自动装:
#   - yt-dlp:  pip 装到当前 venv, 不需要 root, 必装。
#   - aria2c:  系统 binary, pip 装不了; 用本机包管理器 + 非交互 sudo 装;
#              失败仅给提示, 不阻塞启动 (yt-dlp 会自动退回内置 downloader)。
#
# 想跳过整步:  FM_SKIP_MEDIA_DEPS=1 ./run.sh
media_deps_check() {
  if [ "${FM_SKIP_MEDIA_DEPS:-0}" = "1" ]; then
    printf '[run.sh] FM_SKIP_MEDIA_DEPS=1, 跳过 yt-dlp / aria2c 预检\n' >&2
    return 0
  fi
  printf '[run.sh] ─ 媒体依赖预检 (yt-dlp / aria2c) ─\n' >&2

  # ---- yt-dlp ----
  if command -v yt-dlp >/dev/null 2>&1; then
    yt_ver="$(yt-dlp --version 2>/dev/null || echo '?')"
    printf '[run.sh] ✓ yt-dlp 已就绪: %s (v%s)\n' "$(command -v yt-dlp)" "$yt_ver" >&2
  else
    printf '[run.sh] ✗ yt-dlp 未安装, 用 pip 装到当前 venv ...\n' >&2
    if pip install --disable-pip-version-check -q --timeout 30 --retries 1 yt-dlp; then
      printf '[run.sh] ✓ yt-dlp 安装完成: v%s\n' "$(yt-dlp --version 2>/dev/null || echo '?')" >&2
    else
      printf '[run.sh] ⚠ yt-dlp pip 安装失败 (网络 / PyPI 不可用?), 「从链接导入」遇到视频站会报错\n' >&2
      printf '[run.sh]   手动安装: pip install yt-dlp   (或换镜像 pip install -i https://pypi.tuna.tsinghua.edu.cn/simple yt-dlp)\n' >&2
    fi
  fi

  # ---- aria2c ----
  if command -v aria2c >/dev/null 2>&1; then
    printf '[run.sh] ✓ aria2c 已就绪: %s\n' "$(command -v aria2c)" >&2
    printf '[run.sh] ─ 媒体依赖预检完成 ─\n' >&2
    return 0
  fi

  printf '[run.sh] ✗ aria2c 未安装, 尝试用系统包管理器自动安装 ...\n' >&2

  # 决定要不要前置 sudo: root 不需要; 否则只用 `sudo -n` (非交互), 拿不到密码就放弃
  sudo_cmd=""
  if [ "$(id -u)" = "0" ]; then
    sudo_cmd=""
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo_cmd="sudo -n"
  else
    printf '[run.sh]   ⚠ 当前不是 root 且 sudo 需要密码; 无法在脚本里非交互装 aria2c\n' >&2
    printf '[run.sh]   (跳过本次自动安装, 仅给手动建议)\n' >&2
  fi

  install_ok=0
  if [ "$(id -u)" = "0" ] || [ -n "$sudo_cmd" ]; then
    if command -v apt-get >/dev/null 2>&1; then
      printf '[run.sh]   执行: %s env DEBIAN_FRONTEND=noninteractive apt-get install -y aria2\n' "$sudo_cmd" >&2
      if $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 \
         && $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y aria2 >/dev/null 2>&1; then
        install_ok=1
      fi
    elif command -v dnf >/dev/null 2>&1; then
      $sudo_cmd dnf install -y aria2 >/dev/null 2>&1 && install_ok=1 || true
    elif command -v yum >/dev/null 2>&1; then
      $sudo_cmd yum install -y aria2 >/dev/null 2>&1 && install_ok=1 || true
    elif command -v pacman >/dev/null 2>&1; then
      $sudo_cmd pacman -S --noconfirm aria2 >/dev/null 2>&1 && install_ok=1 || true
    elif command -v apk >/dev/null 2>&1; then
      $sudo_cmd apk add --no-cache aria2 >/dev/null 2>&1 && install_ok=1 || true
    elif command -v zypper >/dev/null 2>&1; then
      $sudo_cmd zypper --non-interactive install aria2 >/dev/null 2>&1 && install_ok=1 || true
    elif command -v brew >/dev/null 2>&1; then
      # brew 不建议 sudo; 用当前用户跑
      brew install aria2 >/dev/null 2>&1 && install_ok=1 || true
    else
      printf '[run.sh]   ⚠ 未识别系统包管理器, 不知道怎么装 aria2c\n' >&2
    fi
  fi

  if [ "$install_ok" = "1" ] && command -v aria2c >/dev/null 2>&1; then
    printf '[run.sh] ✓ aria2c 安装完成: %s\n' "$(command -v aria2c)" >&2
  else
    printf '[run.sh] ⚠ aria2c 自动安装失败 (sudo 受限 / 网络 / 未识别系统)\n' >&2
    printf '[run.sh]   手动安装 (任选一条):\n' >&2
    printf '[run.sh]     • Debian/Ubuntu: sudo apt-get install -y aria2\n' >&2
    printf '[run.sh]     • Fedora/RHEL  : sudo dnf install -y aria2\n' >&2
    printf '[run.sh]     • Arch         : sudo pacman -S aria2\n' >&2
    printf '[run.sh]     • Alpine       : sudo apk add aria2\n' >&2
    printf '[run.sh]     • openSUSE     : sudo zypper install aria2\n' >&2
    printf '[run.sh]     • macOS        : brew install aria2\n' >&2
    printf '[run.sh]   不装不影响功能, 仅 yt-dlp 多线程加速会自动退回 yt-dlp 内置 downloader\n' >&2
  fi
  printf '[run.sh] ─ 媒体依赖预检完成 ─\n' >&2
}

media_deps_check || true   # 任何检测异常都不阻止启动


# ---- 4b. docker 预检（用于「服务」页面里的 Docker 服务功能；不阻止启动）------
# 仅诊断，不试图安装 —— 装 docker 通常需要 root，且不同发行版命令不一样
docker_diag() {
  printf '[run.sh] ─ Docker 预检 ─\n' >&2

  if ! command -v docker >/dev/null 2>&1; then
    printf '[run.sh] ✗ docker 未安装或不在 PATH\n' >&2
    printf '[run.sh]   修复建议（按你的系统选一条）:\n' >&2
    printf '[run.sh]     • Debian/Ubuntu: curl -fsSL https://get.docker.com | sudo sh\n' >&2
    printf '[run.sh]     • Fedora/RHEL  : sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin\n' >&2
    printf '[run.sh]     • Arch         : sudo pacman -S docker docker-compose\n' >&2
    printf '[run.sh]     • macOS        : brew install --cask docker  (或安装 Docker Desktop)\n' >&2
    printf '[run.sh]   装好后记得: sudo systemctl enable --now docker && sudo usermod -aG docker "$USER" 再重新登录\n' >&2
    printf '[run.sh]   不装也不影响「原生服务（tar.gz + run.sh）」功能，仅 Docker 服务页会受限。\n' >&2
    return 0
  fi

  printf '[run.sh] ✓ docker 在 PATH: %s\n' "$(command -v docker)" >&2

  # daemon 探活；非 0 通常是没启动或当前用户不在 docker 组
  if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    err_msg="$(docker version 2>&1 | tail -3 | tr '\n' ' ')"
    printf '[run.sh] ⚠ docker daemon 不可访问: %s\n' "$err_msg" >&2
    printf '[run.sh]   修复建议:\n' >&2
    printf '[run.sh]     • 启动: sudo systemctl start docker  (或在 macOS 打开 Docker Desktop)\n' >&2
    printf '[run.sh]     • 权限: sudo usermod -aG docker "$USER"  然后重新登录\n' >&2
    printf '[run.sh]     • 套接字: ls -l /var/run/docker.sock  确认存在且当前用户能读\n' >&2
  else
    srv_ver="$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
    printf '[run.sh] ✓ docker daemon 在线: server=%s\n' "$srv_ver" >&2
  fi

  # compose v2 子命令 / v1 独立二进制 都看一眼
  if docker compose version --short >/dev/null 2>&1; then
    printf '[run.sh] ✓ docker compose 可用: v%s\n' "$(docker compose version --short)" >&2
  elif command -v docker-compose >/dev/null 2>&1; then
    printf '[run.sh] ⚠ 仅检测到旧版 docker-compose (v1): %s\n' "$(docker-compose version --short 2>/dev/null || echo unknown)" >&2
    printf '[run.sh]   建议升级到 Compose V2: sudo apt-get install -y docker-compose-plugin\n' >&2
  else
    printf '[run.sh] ✗ docker compose 不可用\n' >&2
    printf '[run.sh]   修复建议: sudo apt-get install -y docker-compose-plugin  (Debian/Ubuntu)\n' >&2
  fi
  printf '[run.sh] ─ Docker 预检完成 ─\n' >&2
}

docker_diag || true   # 任何检测异常都不阻止启动

# ---- 5. run ---------------------------------------------------------------
log "启动 file_manager 监听端口 $PORT"
exec python run.py "$PORT"
