#!/usr/bin/env bash
# 把 file_manager 项目打包成 tar.gz（用于「服务管理」面板上传）
#
# 用法:
#   scripts/pack.sh [输出文件路径]
#     缺省输出 dist/<basename>-YYYYmmdd-HHMMSS.tar.gz
#
# 不打包:
#   - 虚拟环境（.venv / venv / yenv / env）
#   - Python 缓存（__pycache__ / *.pyc / *.pyo / .pytest_cache / .mypy_cache / .ruff_cache）
#   - 测试目录与文件（tests/、test-update-apks/、test_*.py、conftest.py）
#   - 用户数据（uploads/、vm_backup/、thumbnails/、*.db / *.sqlite*）
#   - 历史打包产物（dist/、build/、*.tar.gz / *.tgz / *.zip）
#   - VCS / IDE / OS 临时文件（.git/、.cursor/、.vscode/、.idea/、.DS_Store ...）
#
# 注意:
#   - 解压后顶层目录就是项目名，run.sh 在该目录下，符合服务管理的 safe_extract_tarball 约定。
#   - 不打包 instance/*.db 但保留 instance/ 目录结构（首次启动时 SQLite 会自动重建）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

TS="$(date +%Y%m%d-%H%M%S)"
DEFAULT_OUT="$PROJECT_DIR/dist/${PROJECT_NAME}-${TS}.tar.gz"
OUT="${1:-$DEFAULT_OUT}"

# 把相对路径补成绝对
case "$OUT" in
  /*) ;;
  *) OUT="$PWD/$OUT" ;;
esac

mkdir -p "$(dirname "$OUT")"

log() { printf '[pack.sh] %s\n' "$*" >&2; }

# --- 排除清单（GNU tar 的 --exclude 默认是 glob，匹配任意层级的文件名）-----
EXCLUDES=(
  # 虚拟环境
  .venv venv yenv env
  # Python 缓存
  __pycache__ '*.pyc' '*.pyo' .pytest_cache .mypy_cache .ruff_cache
  # 测试
  tests test-update-apks 'test_*.py' conftest.py
  # 用户数据
  uploads vm_backup thumbnails
  # 数据库
  '*.db' '*.sqlite' '*.sqlite3' '*.db-journal'
  # 打包输出
  dist build '*.tar.gz' '*.tgz' '*.zip'
  # VCS / IDE / OS
  .git .gitignore .cursor .vscode .idea .DS_Store Thumbs.db
  # 编辑器临时
  '*.swp' '*.swo' '*~'
  # 日志
  '*.log' logs
  # 历史遗留
  exclude.txt
)

EXCLUDE_ARGS=()
for p in "${EXCLUDES[@]}"; do
  EXCLUDE_ARGS+=(--exclude="$p")
done

log "项目目录: $PROJECT_DIR"
log "输出文件: $OUT"
log "排除条目: ${#EXCLUDES[@]} 条"

# cd 到父目录，把整个项目作为顶层目录打包
cd "$(dirname "$PROJECT_DIR")"

tar -czf "$OUT" \
  "${EXCLUDE_ARGS[@]}" \
  "$PROJECT_NAME"

SIZE_H="$(du -h "$OUT" | awk '{print $1}')"
COUNT="$(tar -tzf "$OUT" | wc -l)"
log "✅ 打包完成: $OUT ($SIZE_H, $COUNT 个条目)"

# 关键文件校验：缺任何一个直接非零退出
REQUIRED=(
  "$PROJECT_NAME/run.sh"
  "$PROJECT_NAME/run.py"
  "$PROJECT_NAME/requirements.txt"
  "$PROJECT_NAME/app/__init__.py"
)
MISSING=0
LISTING="$(tar -tzf "$OUT")"
# 注意: 用 here-string (<<<), 不用 `printf | grep -q` 管道。
# 因为 `set -o pipefail` + grep -q 一旦命中立刻退出, printf 写大 listing 时会收到
# SIGPIPE, 整条管道返回非 0, if 分支被误判成 "未找到"。
for need in "${REQUIRED[@]}"; do
  if grep -qx -F -- "$need" <<<"$LISTING"; then
    log "  ✓ $need"
  else
    log "  ✗ $need 缺失！"
    MISSING=$((MISSING + 1))
  fi
done

# 反向校验：不应该有数据库 / venv / 上传文件 / 测试文件
NOT_ALLOWED_PATTERNS=(
  '\.db$'
  '\.sqlite[0-9]*$'
  '(^|/)\.venv/'
  '(^|/)yenv/'
  '(^|/)__pycache__/'
  '(^|/)uploads/'
  '(^|/)tests/'
  '(^|/)test-update-apks/'
)
LEAKED=0
for pat in "${NOT_ALLOWED_PATTERNS[@]}"; do
  # head -3 同样会触发 SIGPIPE; 用 here-string + awk 内部限行规避 pipefail
  hit="$(awk -v pat="$pat" 'BEGIN{n=0} $0 ~ pat { print; if (++n>=3) exit }' <<<"$LISTING")"
  if [ -n "$hit" ]; then
    log "  ⚠ 检测到不应包含的内容（pattern=$pat）:"
    printf '%s\n' "$hit" | sed 's/^/      /' >&2
    LEAKED=$((LEAKED + 1))
  fi
done

if [ $MISSING -gt 0 ] || [ $LEAKED -gt 0 ]; then
  log "❌ 校验未通过：missing=$MISSING leaked=$LEAKED"
  exit 1
fi

log "🎉 校验通过，可直接到 /services 页面上传 $OUT"
