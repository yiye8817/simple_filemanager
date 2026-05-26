#!/usr/bin/env bash
# servers/pack.sh — 打包 servers/<name>/ 子项目为 servers/dist/<name>.tar.gz,
#   只保留可运行的代码 (排除 venv / pycache / 持久化数据 / 日志 / VCS 等)。
#   产物可直接喂给 file_manager 的「服务托管」上传 (POST /api/services)。
#
# 用法:
#   ./pack.sh                        # 没装其它 server 时, 自动选第一个; 有多个则交互式
#   ./pack.sh musicfree_server       # 直接指定 (可写多个: pack.sh a b c)
#   ./pack.sh --all                  # 全部打包
#   ./pack.sh --list                 # 只列出可打包的 server (含每个的 run.sh 是否就绪)
#   ./pack.sh --dry-run NAME         # 模拟打包: 打印将被包含 / 排除的文件 + 体积, 不写盘
#   ./pack.sh --output PATH NAME     # 指定输出 tar.gz 路径 (默认 dist/<name>.tar.gz)
#   ./pack.sh --verbose NAME         # 打包时把文件清单也打印出来
#
# 设计要点:
#   * "可运行的代码" = 不依赖宿主机 / 不带敏感配置 / 不带历史日志的最小子集
#   * 排除规则: .venv / venv / __pycache__ / *.pyc / data / *.log / .git / *.tar.gz ...
#   * 必须存在 run.sh (服务托管协议) 才允许打包
#   * tar 顶层带一层目录 <name>/  → 解压后符合服务托管「顶层一层目录是允许的」契约

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR_DEFAULT="$SCRIPT_DIR/dist"

# tar --exclude 的 pattern; 这里集中维护, 见 README 解释为什么排除每一项
EXCLUDES=(
    # Python 虚拟环境 / 字节码缓存
    '.venv' 'venv' 'env' '__pycache__' '*.pyc' '*.pyo' '*.pyd'
    '.pytest_cache' '.mypy_cache' '.ruff_cache' '.tox'
    # Node / 前端
    'node_modules' '.next' 'dist-frontend'
    # 运行时持久化与日志 (服务起来后自己生成的)
    'data' 'logs' 'log' '*.log' '*.log.*' 'state.json' '*.sock' '*.pid'
    'run.log'
    # 包内可能出现的旧打包产物 (避免 tar-in-tar 自包含)
    'dist' '*.tar.gz' '*.tgz' '*.zip'
    # VCS / IDE / OS 元数据
    '.git' '.gitignore' '.gitattributes' '.svn' '.hg'
    '.idea' '.vscode' '.cursor'
    '.DS_Store' 'Thumbs.db' 'desktop.ini'
    # secrets / 临时凭证 (兜底; 真正放敏感文件请用 .env)
    '.env' '.env.local' 'secrets.json' 'credentials.json'
)

# --------------------------- 颜色/格式辅助 --------------------------------
if [ -t 1 ]; then
    _C_BLUE=$'\033[34m'; _C_GREEN=$'\033[32m'; _C_YELLOW=$'\033[33m'
    _C_RED=$'\033[31m'; _C_DIM=$'\033[2m'; _C_BOLD=$'\033[1m'; _C_RESET=$'\033[0m'
else
    _C_BLUE=''; _C_GREEN=''; _C_YELLOW=''; _C_RED=''; _C_DIM=''; _C_BOLD=''; _C_RESET=''
fi

log()    { printf '%s\n' "$*"; }
info()   { printf '%s%s%s\n' "$_C_BLUE" "$*" "$_C_RESET"; }
ok()     { printf '%s✓ %s%s\n' "$_C_GREEN" "$*" "$_C_RESET"; }
warn()   { printf '%s! %s%s\n' "$_C_YELLOW" "$*" "$_C_RESET"; }
err()    { printf '%s✗ %s%s\n' "$_C_RED" "$*" "$_C_RESET" >&2; }
dim()    { printf '%s%s%s\n' "$_C_DIM" "$*" "$_C_RESET"; }

# ---------------------------- 通用工具 -------------------------------------
human_size() {
    # KiB / MiB / GiB 自适应
    local bytes="${1:-0}"
    awk -v b="$bytes" '
        BEGIN {
            split("B KiB MiB GiB TiB", units, " ");
            i = 1;
            while (b >= 1024 && i < 5) { b /= 1024; i++ }
            printf("%.1f %s", b, units[i]);
        }'
}

is_excluded() {
    # 给定一个相对路径片段, 判断它是否会被 tar --exclude 命中。
    # 仅用于 --dry-run / --verbose 报告; 真正打包用 tar 自己的 --exclude。
    local path="$1"
    local pattern
    for pattern in "${EXCLUDES[@]}"; do
        # 把 tar 风格的 glob 转成 case 风格 (匹配文件名分量, 不跨 / )
        case "$path" in
            "$pattern"|*/"$pattern"|*/"$pattern"/*|"$pattern"/*) return 0 ;;
        esac
        # 后缀型 (*.pyc): 用 [[ ... ]]
        if [[ "$pattern" == \** ]] && [[ "$path" == "${pattern}"  || "$path" == *"${pattern#\*}" ]]; then
            return 0
        fi
    done
    return 1
}

# 列出 servers/ 下的所有 "候选 server" (子目录, 不含 dist/, 排除 hidden)
discover_servers() {
    local d
    for d in "$SCRIPT_DIR"/*/; do
        [ -d "$d" ] || continue
        local name; name="$(basename "$d")"
        # 跳过 dist/, hidden, 以及非项目目录
        case "$name" in
            dist|.|..|.*) continue ;;
        esac
        printf '%s\n' "$name"
    done
}

validate_server() {
    # 检查是否符合服务托管契约 (有 run.sh, 可读)
    local name="$1"
    local dir="$SCRIPT_DIR/$name"
    if [ ! -d "$dir" ]; then
        err "目录不存在: $dir"
        return 2
    fi
    if [ ! -f "$dir/run.sh" ]; then
        err "$name/run.sh 不存在 — 服务托管要求顶层必须有 run.sh"
        return 3
    fi
    if [ ! -r "$dir/run.sh" ]; then
        err "$name/run.sh 不可读"
        return 3
    fi
    return 0
}

# 计算"打包后/排除的文件统计" (count, bytes); 仅用于报告。
# 用法: tally_files <server_dir> <"included"|"excluded">
tally_files() {
    local dir="$1" mode="$2"
    local count=0 bytes=0 file rel
    while IFS= read -r -d '' file; do
        rel="${file#"$dir"/}"
        if is_excluded "$rel"; then
            [ "$mode" = "excluded" ] || continue
        else
            [ "$mode" = "included" ] || continue
        fi
        count=$((count + 1))
        local sz
        sz=$(stat -c '%s' "$file" 2>/dev/null || stat -f '%z' "$file" 2>/dev/null || echo 0)
        bytes=$((bytes + sz))
    done < <(find "$dir" -type f -print0)
    printf '%d %d\n' "$count" "$bytes"
}

# 实际执行打包; 输出 tar.gz 大小
do_pack() {
    local name="$1" output="$2" verbose="$3"
    local server_dir="$SCRIPT_DIR/$name"
    local out_dir; out_dir="$(dirname "$output")"
    mkdir -p "$out_dir"

    # 用 tar -C 切到 servers/, 让归档顶层带 <name>/ 一层目录
    local tar_excludes=()
    local p
    for p in "${EXCLUDES[@]}"; do
        tar_excludes+=("--exclude=$p")
    done
    local verbose_flag=()
    [ "$verbose" = "1" ] && verbose_flag+=("-v")

    # 复用 SOURCE_DATE_EPOCH 让产物可重现 (相同输入 → 相同 sha256), 不强求, 容错失败
    local tar_extra=()
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        tar_extra+=("--mtime=@$SOURCE_DATE_EPOCH" "--sort=name")
    fi

    tar -czf "$output" \
        -C "$SCRIPT_DIR" \
        "${tar_excludes[@]}" \
        "${verbose_flag[@]}" \
        "${tar_extra[@]}" \
        "$name"

    chmod 644 "$output"
}

# ---------------------------- 子命令: list ---------------------------------
cmd_list() {
    info "$_C_BOLD== servers/ 候选 ==$_C_RESET"
    local found=0 s
    while IFS= read -r s; do
        found=1
        local ok_run_sh="✗" status_color="$_C_RED"
        if [ -f "$SCRIPT_DIR/$s/run.sh" ]; then
            ok_run_sh="✓"; status_color="$_C_GREEN"
        fi
        local read_size
        read_size=$(du -sb "$SCRIPT_DIR/$s" 2>/dev/null | awk '{print $1}')
        printf '  %s[%s]%s  %-30s  源码大小: %s\n' \
            "$status_color" "$ok_run_sh" "$_C_RESET" \
            "$s" "$(human_size "${read_size:-0}")"
    done < <(discover_servers)
    if [ "$found" = "0" ]; then
        warn '没有发现可打包的 server (没有任何子目录)'
        return 1
    fi
    dim '提示: [✓] 表示该目录已就绪可打包; [✗] 表示缺少 run.sh, 服务托管会拒绝'
}

# ---------------------------- 子命令: dry-run ------------------------------
cmd_dry_run() {
    local name="$1"
    validate_server "$name" || return $?
    local dir="$SCRIPT_DIR/$name"
    info "$_C_BOLD== dry-run: $name ==$_C_RESET"

    # 计算 included / excluded 统计
    read -r in_cnt in_bytes < <(tally_files "$dir" included)
    read -r ex_cnt ex_bytes < <(tally_files "$dir" excluded)
    local total_cnt=$((in_cnt + ex_cnt))
    local total_bytes=$((in_bytes + ex_bytes))

    printf '  将包含: %s%d%s 个文件 (%s)\n' "$_C_GREEN" "$in_cnt" "$_C_RESET" "$(human_size "$in_bytes")"
    printf '  将排除: %s%d%s 个文件 (%s) — 见下方按规则分类\n' "$_C_YELLOW" "$ex_cnt" "$_C_RESET" "$(human_size "$ex_bytes")"
    printf '  总计:   %d 个文件 (%s)\n' "$total_cnt" "$(human_size "$total_bytes")"

    # 按 top-level 项分组展示, 直观看出哪些目录被整体排除
    echo
    info '按顶层条目分组:'
    local entry rel sz_b status reason
    for entry in "$dir"/* "$dir"/.[!.]*; do
        [ -e "$entry" ] || continue
        rel="${entry#"$dir"/}"
        sz_b=$(du -sb "$entry" 2>/dev/null | awk '{print $1}')
        if is_excluded "$rel"; then
            status="${_C_YELLOW}EXCLUDE${_C_RESET}"
            reason="(命中排除规则)"
        else
            status="${_C_GREEN}INCLUDE${_C_RESET}"
            reason=''
        fi
        printf '  %s  %-25s  %8s   %s\n' "$status" "$rel" "$(human_size "$sz_b")" "$reason"
    done

    echo
    info '关键完整性检查:'
    local r=0
    if [ -f "$dir/run.sh" ]; then ok "run.sh 存在"; else err "run.sh 缺失"; r=1; fi
    if [ -x "$dir/run.sh" ]; then ok "run.sh 可执行"; else warn "run.sh 不可执行 (服务托管会用 bash 运行, 不强制)"; fi
    if [ -f "$dir/requirements.txt" ] || [ -f "$dir/package.json" ] || [ -f "$dir/go.mod" ]; then
        ok "找到依赖清单"
    else
        warn "没有 requirements.txt / package.json / go.mod — 确保 run.sh 自带依赖安装"
    fi
    return $r
}

# ---------------------------- 子命令: pack ---------------------------------
cmd_pack_one() {
    local name="$1" output="$2" verbose="$3"
    validate_server "$name" || return $?
    info "$_C_BOLD== 打包: $name → $output ==$_C_RESET"

    # 预先报告将排除哪些大件 (方便用户决定要不要先 clean)
    local entry rel sz_b excluded_total=0
    for entry in "$SCRIPT_DIR/$name"/* "$SCRIPT_DIR/$name"/.[!.]*; do
        [ -e "$entry" ] || continue
        rel="${entry#"$SCRIPT_DIR/$name/"}"
        if is_excluded "$rel"; then
            sz_b=$(du -sb "$entry" 2>/dev/null | awk '{print $1}')
            excluded_total=$((excluded_total + sz_b))
            dim "  跳过 $rel ($(human_size "$sz_b"))"
        fi
    done
    [ "$excluded_total" = "0" ] || dim "  共跳过 $(human_size "$excluded_total") 运行时产物"

    do_pack "$name" "$output" "$verbose"

    local out_bytes; out_bytes=$(stat -c '%s' "$output" 2>/dev/null || stat -f '%z' "$output" 2>/dev/null)
    local file_count; file_count=$(tar -tzf "$output" | wc -l)
    ok "$(printf '生成 %s (%s, 内含 %d 项)' "$output" "$(human_size "$out_bytes")" "$file_count")"

    # 关键文件存在校验
    if ! tar -tzf "$output" | grep -qE "^$name/run\.sh\$"; then
        err "归档内未发现 $name/run.sh — 服务托管将无法启动!"
        return 4
    fi
    ok "归档顶层目录 $name/ + run.sh 校验通过"

    echo
    dim '后续上传到服务托管 (示例):'
    dim "  curl -b cookies.txt -c cookies.txt \\"
    dim "    -F 'name=$name' \\"
    dim "    -F 'local_port=<frpc 中的 local_port>' \\"
    dim "    -F 'remote_port=<frpc 中的 remote_port>' \\"
    dim "    -F 'archive=@$output' \\"
    dim "    http://<file_manager_host>/api/services"
}

# ---------------------------- 交互式选择 -----------------------------------
# 注意: 这个函数通过 stdout 把"被选中的 server 名"一行一个返回给 caller, 因此
#   所有给人看的 prompt / 编号列表必须显式写到 stderr, 否则会被当成选择结果。
interactive_pick() {
    local servers=() s
    while IFS= read -r s; do servers+=("$s"); done < <(discover_servers)
    if [ "${#servers[@]}" = "0" ]; then
        err '没有发现任何 server 子目录'
        return 1
    fi
    if [ "${#servers[@]}" = "1" ]; then
        printf '%s\n' "${servers[0]}"
        return 0
    fi
    {
        printf '%s请选择要打包的 server (输入序号, 多个用空格分隔, 或 a 表示全部):%s\n' "$_C_BLUE" "$_C_RESET"
        local i=1
        for s in "${servers[@]}"; do
            local mark=' '
            [ -f "$SCRIPT_DIR/$s/run.sh" ] || mark='!'
            local hint=''
            [ "$mark" = '!' ] && hint=" ${_C_YELLOW}(缺 run.sh)${_C_RESET}"
            printf '  %s%d) %s%s\n' "$mark" "$i" "$s" "$hint"
            i=$((i + 1))
        done
        printf '> '
    } >&2

    local choice
    # 从 /dev/tty 读, 这样即便 stdin 被管道占用也能交互;
    # 若没有 tty (CI 等场景), 退回到普通 read
    if [ -r /dev/tty ] && [ -t 2 ]; then
        read -r choice </dev/tty
    else
        read -r choice
    fi
    [ -z "$choice" ] && { err '未选择'; return 1; }

    if [ "$choice" = "a" ] || [ "$choice" = "all" ]; then
        printf '%s\n' "${servers[@]}"
        return 0
    fi

    local idx
    for idx in $choice; do
        if ! [[ "$idx" =~ ^[0-9]+$ ]] || [ "$idx" -lt 1 ] || [ "$idx" -gt "${#servers[@]}" ]; then
            err "非法序号: $idx"
            return 1
        fi
        printf '%s\n' "${servers[$((idx - 1))]}"
    done
}

# ---------------------------- 参数解析 -------------------------------------
usage() {
    # 打印脚本开头连续的 # 注释段 (跳过 shebang, 遇到第一行非注释停)
    awk '
        NR == 1 { next }
        /^[^#]/ { exit }
        { sub(/^# ?/, ""); print }
    ' "${BASH_SOURCE[0]}"
}

main() {
    local mode='pack'
    local output_dir="$DIST_DIR_DEFAULT"
    local output_file=''
    local verbose=0
    local positional=()

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)        usage; exit 0 ;;
            --list)           mode='list'; shift ;;
            --all)            mode='all'; shift ;;
            --dry-run)        mode='dry-run'; shift ;;
            --verbose|-v)     verbose=1; shift ;;
            --output|-o)      output_file="$2"; shift 2 ;;
            --output-dir)     output_dir="$2"; shift 2 ;;
            --)               shift; positional+=("$@"); break ;;
            -*)               err "未知参数: $1"; usage; exit 2 ;;
            *)                positional+=("$1"); shift ;;
        esac
    done

    if [ "$mode" = "list" ]; then
        cmd_list
        return $?
    fi

    # 决定要处理的 server 列表
    local targets=()
    if [ "$mode" = "all" ]; then
        local s
        while IFS= read -r s; do targets+=("$s"); done < <(discover_servers)
    elif [ "${#positional[@]}" -gt 0 ]; then
        targets=("${positional[@]}")
    else
        local picked
        while IFS= read -r picked; do targets+=("$picked"); done < <(interactive_pick) || exit $?
    fi

    if [ "${#targets[@]}" = "0" ]; then
        err '没有选择任何 server'
        exit 1
    fi

    local name fail=0
    for name in "${targets[@]}"; do
        if [ "$mode" = "dry-run" ]; then
            cmd_dry_run "$name" || fail=1
        else
            local out="$output_file"
            if [ -z "$out" ] || [ "${#targets[@]}" -gt 1 ]; then
                out="$output_dir/$name.tar.gz"
            fi
            cmd_pack_one "$name" "$out" "$verbose" || fail=1
        fi
        echo
    done

    return $fail
}

main "$@"
