#!/usr/bin/env bash
# ============================================================
# Gipfel 快速数据同步脚本
# 用途：在两台服务器之间快速同步数据（增量）
#
# 使用方法：
#   从本机推送到远程：
#     bash scripts/quick-sync.sh push user@remote-ip
#
#   从远程拉取到本机：
#     bash scripts/quick-sync.sh pull user@remote-ip
#
#   指定安装目录：
#     bash scripts/quick-sync.sh push user@remote-ip /opt/gipfel
#
#   非 22 端口 / 指定私钥 / 指定 known_hosts：
#     bash scripts/quick-sync.sh push user@remote-ip /opt/gipfel \
#         --ssh-port 2222 --ssh-key ~/.ssh/id_ed25519 --ssh-known-hosts ~/.ssh/known_hosts
#
# 审计 X-25：改前本脚本**完全不支持**自定义端口与私钥，只用
#   `-e "ssh -o StrictHostKeyChecking=accept-new"`
# 在非 22 端口的服务器上根本无法工作，且首次连接会自动信任任意主机密钥。
# 现在与 migrate-server.sh 一致：`-o BatchMode=yes`（不再回退到交互式口令提示而挂住）、
# 端口范围校验、可选 known_hosts 固定（给了就改 StrictHostKeyChecking=yes）。
# 注意：`ssh -i <私钥路径>` 会把路径暴露在同机 `ps` 里 —— 生产环境更推荐把 Host/Port/IdentityFile
# 写进 `~/.ssh/config`，脚本里就不必再传 --ssh-key。
#
#  注意（审计 X-20）：install-dir 若写成相对路径，会按**当前工作目录**解析成绝对路径，
#  而不是按脚本所在目录。脚本会把它规范化并打印出来，push 前还会校验该目录确实是
#  一份完整的安装目录（含 backend/），避免把别的目录里的同名文件推给生产机。
# ============================================================

set -euo pipefail

# 审计 X-20：以脚本自身位置为基准拿到仓库根，用于"推错源"提醒（不再依赖 $PWD）。
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

# 公共函数（含 absolutize_dir）；缺失时用下面的兜底实现，行为一致。
_DEPLOY_COMMON="$SCRIPT_DIR/lib/deploy-common.sh"
if [[ -f "$_DEPLOY_COMMON" ]]; then
    # shellcheck source=scripts/lib/deploy-common.sh
    source "$_DEPLOY_COMMON"
fi
if ! command -v absolutize_dir >/dev/null 2>&1; then
    absolutize_dir() {
        local p="${1:-}"
        [[ -n "$p" ]] || return 1
        if command -v realpath >/dev/null 2>&1; then
            local resolved=""
            if resolved=$(realpath -m -- "$p" 2>/dev/null) && [[ -n "$resolved" ]]; then
                printf '%s' "$resolved"
                return 0
            fi
        fi
        [[ "$p" == /* ]] || p="$PWD/$p"
        local out="" seg
        local IFS='/'
        for seg in $p; do
            case "$seg" in
                ""|".") continue ;;
                "..")   out="${out%/*}" ;;
                *)      out="${out}/${seg}" ;;
            esac
        done
        printf '%s' "${out:-/}"
    }
fi

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# 参数（审计 X-25：位置参数保持不变，另支持若干 --ssh-* 选项）
ACTION=""
REMOTE=""
INSTALL_DIR="/opt/gipfel"
SSH_PORT=""
SSH_KEY=""
SSH_KNOWN_HOSTS=""

_args=("$@")
_i=0
while [[ "$_i" -lt "${#_args[@]}" ]]; do
    _a="${_args[$_i]}"
    case "$_a" in
        --ssh-port)        SSH_PORT="${_args[$((_i + 1))]:-}"; _i=$((_i + 2)) ;;
        --ssh-key)         SSH_KEY="${_args[$((_i + 1))]:-}"; _i=$((_i + 2)) ;;
        --ssh-known-hosts) SSH_KNOWN_HOSTS="${_args[$((_i + 1))]:-}"; _i=$((_i + 2)) ;;
        *)                 _i=$((_i + 1)) ;;
    esac
done

# 位置参数（跳过所有 --ssh-* 及其取值）
_pos=()
_i=0
while [[ "$_i" -lt "${#_args[@]}" ]]; do
    _a="${_args[$_i]}"
    case "$_a" in
        --ssh-port|--ssh-key|--ssh-known-hosts) _i=$((_i + 2)) ;;
        *) _pos+=("$_a"); _i=$((_i + 1)) ;;
    esac
done
ACTION="${_pos[0]:-}"
REMOTE="${_pos[1]:-}"
INSTALL_DIR="${_pos[2]:-/opt/gipfel}"

if [[ -z "$ACTION" || -z "$REMOTE" ]]; then
    echo "用法: $0 <push|pull> <user@host> [install-dir] [--ssh-port PORT] [--ssh-key PATH] [--ssh-known-hosts PATH]"
    echo ""
    echo "示例:"
    echo "  $0 push root@192.168.1.100"
    echo "  $0 pull root@192.168.1.50 /opt/gipfel"
    echo "  $0 push root@192.168.1.100 /opt/gipfel --ssh-port 2222 --ssh-key ~/.ssh/id_ed25519"
    echo ""
    echo "install-dir 可省略（默认 /opt/gipfel）。写成相对路径时按当前目录解析。"
    echo "--ssh-known-hosts 给出后，主机密钥按该文件严格校验（StrictHostKeyChecking=yes）。"
    exit 1
fi

# 审计 X-20：先把 INSTALL_DIR 规范化成绝对路径，并把它打印出来。
# 改前原样透传 —— 相对路径（`push host ./opt/gipfel`，或在别的 cwd 下用相对路径调用）
# 会按 $PWD 解析，rsync 可能把另一个目录里的同名文件当成数据源推给生产机，
# 直接覆盖目标机的 db.sqlite3 / .env；pull 方向也会在意外位置建目录。
_RAW_INSTALL_DIR="$INSTALL_DIR"
if ! INSTALL_DIR="$(absolutize_dir "$_RAW_INSTALL_DIR")"; then
    log_error "无法把安装目录规范成绝对路径: $_RAW_INSTALL_DIR"
    exit 1
fi
if [[ "$INSTALL_DIR" != "$_RAW_INSTALL_DIR" ]]; then
    log_warn "安装目录已规范化为绝对路径: $INSTALL_DIR（原值: $_RAW_INSTALL_DIR，按当前目录 $(pwd) 解析）"
fi

# fail fast：方向与（push 方向的）源目录必须先成立，别再让循环静默"跳过"到"同步完成"。
case "$ACTION" in
    push)
        if [[ ! -d "$INSTALL_DIR/backend" ]]; then
            log_error "推送源不可用：$INSTALL_DIR/backend 不存在"
            log_error "INSTALL_DIR 必须是**本机**那份安装目录的绝对路径（相对路径按当前目录解析）。"
            exit 1
        fi
        if [[ "$INSTALL_DIR" != "$REPO_ROOT" ]]; then
            log_warn "INSTALL_DIR（$INSTALL_DIR）与本脚本所在仓库（$REPO_ROOT）不是同一份，"
            log_warn "推送前请再确认一次源目录，避免把别的目录里的同名文件推给生产机。"
        fi
        ;;
    pull)
        if ! mkdir -p "$INSTALL_DIR"; then
            log_error "无法创建本地目标目录: $INSTALL_DIR"
            exit 1
        fi
        ;;
    *)
        log_error "未知操作: $ACTION（应为 push 或 pull）"
        exit 1
        ;;
esac

# 审计 X-25：SSH 选项改为 bash 数组，逐参数独立传递（含空格的私钥路径不再被分词拆开），
# 并补 `-o BatchMode=yes`（密钥不可用时直接失败，而不是回退到交互式口令提示把脚本挂住）。
SSH_PORT="${SSH_PORT:-22}"
if ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || (( SSH_PORT < 1 || SSH_PORT > 65535 )); then
    log_error "--ssh-port 必须是 1-65535 的整数（收到: $SSH_PORT）"
    exit 2
fi
SSH_OPTS=(-p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=10)
if [[ -n "$SSH_KNOWN_HOSTS" ]]; then
    SSH_OPTS+=(-o "UserKnownHostsFile=$SSH_KNOWN_HOSTS" -o StrictHostKeyChecking=yes)
else
    SSH_OPTS+=(-o StrictHostKeyChecking=accept-new)
    log_warn "未指定 --ssh-known-hosts：首次连接将自动信任目标主机密钥（只防后续变更，不防首次中间人）。"
fi
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS+=(-i "$SSH_KEY")
    log_warn "已用 -i 指定私钥；该路径会出现在同机 ps 中，生产环境建议改用 ~/.ssh/config 的 IdentityFile。"
fi
printf -v _ssh_opts_q '%q ' "${SSH_OPTS[@]}"
RSYNC_SSH="ssh ${_ssh_opts_q% }"

# 同步的数据列表
SYNC_ITEMS=(
    "backend/db.sqlite3"
    "backend/uploads/"
    "backend/.env"
    "backend/logs/"
)

log_info "开始同步..."
log_info "方向: $ACTION"
log_info "目标: $REMOTE"
log_info "目录: $INSTALL_DIR"
echo ""

# 审计 X-05：改前直接 rsync 正在被服务写入的 `db.sqlite3`（没有 WAL/SHM、没有锁、也不停服）——
# 目标端可能收到一个缺页/半写的数据库文件，打开时报损坏或静默丢最近事务。
# 现在：同步数据库前先用 SQLite 的 `VACUUM INTO` 做一次**一致性快照**（不需要停服），
# 再把快照发出去；拉取方向则在落盘后校验完整性。
DB_ITEMS=("backend/db.sqlite3")
SNAP_DIR=""

cleanup_snapshot() {
    if [[ -n "$SNAP_DIR" && -d "$SNAP_DIR" ]]; then
        rm -rf "$SNAP_DIR"
    fi
}
trap cleanup_snapshot EXIT INT TERM

snapshot_sqlite() {
    # 把 $1（活库）一致性地导出到 $2；需要 python3
    local src="$1"
    local dst="$2"
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "缺少 python3，无法做数据库一致性快照（直接拷贝活库可能损坏目标库）"
        return 1
    fi
    python3 - "$src" "$dst" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
try:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        con.execute("VACUUM INTO ?", (dst,))
    finally:
        con.close()
except sqlite3.Error as exc:
    print(f"VACUUM INTO 失败: {exc}", file=sys.stderr)
    sys.exit(1)
PY
}

check_sqlite_file() {
    # 校验 $1 是合法且完整的 SQLite 库
    local path="$1"
    [[ -s "$path" ]] || { log_error "数据库文件为空: $path"; return 1; }
    if [[ "$(head -c 15 "$path" 2>/dev/null || true)" != "SQLite format 3" ]]; then
        log_error "不是合法 SQLite 文件: $path"
        return 1
    fi
    if command -v python3 >/dev/null 2>&1; then
        if ! python3 - "$path" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    try:
        row = con.execute("pragma integrity_check").fetchone()
    finally:
        con.close()
except sqlite3.Error as exc:
    print(f"打开失败: {exc}", file=sys.stderr)
    sys.exit(1)
if not row or row[0] != "ok":
    print(f"integrity_check: {row}", file=sys.stderr)
    sys.exit(1)
PY
        then
            log_error "数据库完整性校验未通过: $path"
            return 1
        fi
    fi
    return 0
}

for item in "${SYNC_ITEMS[@]}"; do
    src="$INSTALL_DIR/$item"
    dst="$INSTALL_DIR/$(dirname "$item")/"

    if [[ "$ACTION" == "push" ]]; then
        if [[ -e "$src" ]]; then
            if [[ "$item" == "backend/db.sqlite3" ]]; then
                SNAP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gipfel-sync-XXXXXX")"
                snap="$SNAP_DIR/db.sqlite3"
                log_info "为数据库创建一致性快照（VACUUM INTO，无需停服）..."
                snapshot_sqlite "$src" "$snap"
                check_sqlite_file "$snap"
                log_info "推送: $item（快照）"
                rsync -avz --progress -e "$RSYNC_SSH" \
                    "$snap" "$REMOTE:$dst"
            else
                log_info "推送: $item"
                # 审计 X-02 同类：`.env` 含全部密钥，强制 600
                if [[ "$item" == *".env" ]]; then
                    rsync -avz --progress --chmod=F600 \
                        -e "$RSYNC_SSH" "$src" "$REMOTE:$dst"
                else
                    rsync -avz --progress -e "$RSYNC_SSH" "$src" "$REMOTE:$dst"
                fi
            fi
        else
            log_warn "跳过（不存在）: $item"
        fi
    elif [[ "$ACTION" == "pull" ]]; then
        log_info "拉取: $item"
        mkdir -p "$(dirname "$src")"
        rsync -avz --progress -e "$RSYNC_SSH" "$REMOTE:$src" "$(dirname "$src")/"
        if [[ "$item" == "backend/db.sqlite3" ]]; then
            check_sqlite_file "$src" || {
                log_error "拉取到的数据库不可用: $src —— 请勿在此状态下启动后端（会新建空库）"
                exit 1
            }
            log_info "数据库完整性校验通过"
        fi
    fi
    echo ""
done

log_info "同步完成！"
echo ""
log_info "如需重启服务，请在目标服务器执行："
log_info "  sudo systemctl restart gipfel gipfel-wsgi gipfel-logviewer"
# ★ WAL 提示（C2 阶段 1 起 SQLite 默认 journal_mode=WAL）：
#   push 方向已用 VACUUM INTO 快照（自洽、不含 -wal），是安全的；
#   但 **pull 方向是直接覆盖目标机的 db.sqlite3**：目标机服务若仍在跑，它旁边的
#   db.sqlite3-wal/-shm 会被 SQLite 当成"新库的未提交事务"重放 → 库内容被带偏甚至损坏。
#   本脚本不改服务状态（只同步数据），故在此明确给出正确顺序。
if [[ "$ACTION" == "pull" ]]; then
    log_warn "★ WAL 注意：pull 会覆盖目标机的 backend/db.sqlite3，请务必按此顺序操作："
    log_warn "  1) sudo systemctl stop gipfel gipfel-wsgi gipfel-logviewer"
    log_warn "  2) sudo rm -f <安装目录>/backend/db.sqlite3-wal <安装目录>/backend/db.sqlite3-shm"
    log_warn "  3) 再执行本次 pull（或确认已 pull 完成后删除上面两个文件）"
    log_warn "  4) sudo systemctl start gipfel gipfel-wsgi gipfel-logviewer"
fi
