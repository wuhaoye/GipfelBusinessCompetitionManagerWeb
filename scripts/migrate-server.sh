#!/usr/bin/env bash
# ============================================================
# Gipfel 服务器迁移脚本
# 用途：将服务从旧服务器完整迁移到新服务器
#
# 使用方法：
#   在旧服务器上执行（推送模式）：
#     sudo bash scripts/migrate-server.sh --mode push --target user@new-server-ip --install-dir /opt/gipfel
#
#   在新服务器上执行（拉取模式）：
#     sudo bash scripts/migrate-server.sh --mode pull --source user@old-server-ip --install-dir /opt/gipfel
#
# 功能：
#   1. 自动备份旧服务器数据（SQLite、uploads、.env、logs）
#   2. 通过 rsync 安全传输到新服务器
#   3. 在新服务器恢复数据并启动服务
#   4. 支持增量同步（仅传输变更文件）
# ============================================================

set -euo pipefail

# 审计 X-30：本脚本要调用 /usr/sbin 下的命令（useradd、nginx）。以 `su`（不带 `-`）运行时
# PATH 不含 /usr/sbin，`useradd` 会变成 command not found 并被 `|| true` 吞掉，最终以 chown
# 的 “invalid user/group” 报错收场。这里显式补齐 sbin 路径。
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

# ==================== 颜色输出 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

# ==================== 默认参数 ====================
MODE=""
SOURCE=""
TARGET=""
INSTALL_DIR="/opt/gipfel"
# 审计 X-02：改前是 `/tmp/gipfel-migration-<ts>` 里放 `.env`（含 JWT_SECRET / DJANGO_SECRET_KEY
# 等全部密钥），目录由 umask 决定权限（通常 755）、内容 644 ⇒ 同机任意用户可读；
# 且脚本没有任何清理逻辑，迁移中途失败会长期残留。现在用 `mktemp -d`（自带 700）
# 并注册退出清理（`--keep-backup` 可保留，便于排障）。
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gipfel-migration-XXXXXX")"
KEEP_BACKUP=false
_cleanup_backup() {
    # 审计 X-21：改前 dry-run 也跳过清理，于是每次 `--dry-run` 都在 /tmp 留下一个
    # `gipfel-migration-XXXXXX` 空目录（预演本应零副作用）。现在只有 --keep-backup 才保留。
    if [[ "$KEEP_BACKUP" != true && -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
        rm -rf "$BACKUP_DIR"
    fi
}
trap _cleanup_backup EXIT INT TERM
SKIP_SERVICES=false
DRY_RUN=false
SSH_PORT=22
SSH_KEY=""
SSH_KNOWN_HOSTS=""

# ==================== 参数解析 ====================
# 审计 X-21：改前 usage() 固定 `exit 0`，于是三类调用——用户主动求助（-h）、参数拼错、
# 必填参数缺失——退出码全是 0，`&&` 串联或监控包装无法区分"看过帮助"和"参数写错了"。
# 现在接受一个状态码：-h/--help → 0；参数错误 → 2；必填缺失 → 1。
usage() {
    local _usage_status="${1:-0}"
    cat <<EOF
用法: $0 [选项]

必选参数（二选一）：
  --mode push --target USER@HOST    推送模式：从本机推送到目标服务器
  --mode pull --source USER@HOST    拉取模式：从源服务器拉取到本机

可选参数：
  --install-dir DIR     安装目录（默认: /opt/gipfel；只允许绝对路径与 [A-Za-z0-9._/-]）
  --backup-dir DIR      临时备份目录（默认: mktemp -d，权限 700，退出时自动清理）
  --keep-backup         保留临时备份目录（默认退出时清理，避免含密钥的 .env 长期残留）
  --skip-services       跳过服务配置（仅传输数据）
  --dry-run             模拟运行，不实际执行
  --ssh-port PORT       SSH 端口（默认: 22）
  --ssh-key PATH        SSH 私钥路径（注意：路径会出现在同机 ps 中，
                        生产环境建议改用 ~/.ssh/config 的 IdentityFile）
  --ssh-known-hosts PATH  known_hosts 文件；给出后按该文件严格校验主机密钥
                        （StrictHostKeyChecking=yes），不再 accept-new
  -h, --help            显示帮助

示例：
  # 推送到新服务器
  sudo $0 --mode push --target root@192.168.1.100 --install-dir /opt/gipfel

  # 从旧服务器拉取
  sudo $0 --mode pull --source root@192.168.1.50 --install-dir /opt/gipfel

  # 使用自定义 SSH 端口和密钥
  sudo $0 --mode push --target root@192.168.1.100 --ssh-port 2222 --ssh-key ~/.ssh/id_rsa
EOF
    exit "$_usage_status"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)       MODE="$2"; shift 2 ;;
        --source)     SOURCE="$2"; shift 2 ;;
        --target)     TARGET="$2"; shift 2 ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
        --keep-backup) KEEP_BACKUP=true; shift ;;
        --skip-services) SKIP_SERVICES=true; shift ;;
        --dry-run)    DRY_RUN=true; shift ;;
        --ssh-port)   SSH_PORT="$2"; shift 2 ;;
        --ssh-key)    SSH_KEY="$2"; shift 2 ;;
        --ssh-known-hosts) SSH_KNOWN_HOSTS="$2"; shift 2 ;;
        -h|--help)    usage 0 ;;
        *)            log_error "未知参数: $1"; usage 2 ;;
    esac
done

# ==================== 参数验证 ====================
if [[ -z "$MODE" ]]; then
    log_error "必须指定 --mode (push 或 pull)"
    usage 1
fi

if [[ "$MODE" == "push" && -z "$TARGET" ]]; then
    log_error "推送模式必须指定 --target USER@HOST"
    usage 1
fi

if [[ "$MODE" == "pull" && -z "$SOURCE" ]]; then
    log_error "拉取模式必须指定 --source USER@HOST"
    usage 1
fi

# 审计 X-03：`--install-dir` 会被拼进远端命令里由 root shell 执行，必须先做**白名单校验**。
# 改前无任何校验：含空格会创建出错误目录（`/opt/my app/backend` → `/opt/my` 与 `app/backend`），
# 含 shell 元字符则可让目标机以 root 执行任意命令（`--install-dir '/opt/gipfel;curl x|bash'`）。
if [[ "$INSTALL_DIR" != /* || ! "$INSTALL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    log_error "--install-dir 必须是绝对路径且只含 [A-Za-z0-9._/-]（收到: $INSTALL_DIR）"
    exit 1
fi
if [[ "$INSTALL_DIR" == *".."* ]]; then
    log_error "--install-dir 不得包含 '..'（收到: $INSTALL_DIR）"
    exit 1
fi

# 审计 X-21 / X-25：`--ssh-port` 改前不做数字/范围校验，`-p abc` 或 `-p 99999` 会被原样
# 拼进 `ssh -p`，报错信息晦涩（"Bad port 'abc'"）。这里按"参数错误"提前拦下（退出码 2）。
if ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || (( SSH_PORT < 1 || SSH_PORT > 65535 )); then
    log_error "--ssh-port 必须是 1-65535 的整数（收到: $SSH_PORT）"
    exit 2
fi

# SSH 命令构建
# 审计 X-03：改前用字符串累加（`SSH_OPTS="... -i $SSH_KEY"`）再交给 `ssh $SSH_OPTS`，
# 键路径含空格时会被分词拆开；改为 bash 数组后每个参数都是一个独立 argv。
SSH_OPTS=(-p "$SSH_PORT" -o ConnectTimeout=10 -o BatchMode=yes)
# 审计 X-25：改前无条件 `accept-new` —— 首次连接自动信任任意主机密钥，只防后续变更、
# 不防首次中间人；而本脚本的流程是"以 root 推送 .env（含全部密钥）"。现在允许传入
# known_hosts：给了就按文件严格校验（yes），没给才退回 accept-new 并明确告警。
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

# `-e` 的参数是「一条命令字符串」，这里用 printf %q 转义后拼装
printf -v _ssh_opts_q '%q ' "${SSH_OPTS[@]}"
RSYNC_SSH="ssh ${_ssh_opts_q% }"

# ==================== 辅助函数 ====================

# 检查远程命令是否存在（审计 X-21：--dry-run 下不得建立真实连接）
check_remote_command() {
    local host="$1"
    local cmd="$2"
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY-RUN] 将检查 $host 上是否存在: $cmd"
        return 0
    fi
    ssh "${SSH_OPTS[@]}" "$host" "command -v $cmd >/dev/null 2>&1" 2>/dev/null
}

# 连通性探测（审计 X-21：改前这里是裸 `ssh`，`--dry-run` 依然会真实建连并要求免密登录与
# 主机指纹，离线/CI 环境根本无法预演——与帮助文本承诺的"模拟运行，不实际执行"不符）
check_remote_conn() {
    local host="$1"
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY-RUN] 将检查与 $host 的 SSH 连通性"
        return 0
    fi
    ssh "${SSH_OPTS[@]}" "$host" "echo ok" >/dev/null 2>&1
}

# 在远程执行命令
remote_exec() {
    local host="$1"
    shift
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY-RUN] 远程执行: $*"
        return 0
    fi
    ssh "${SSH_OPTS[@]}" "$host" "$@"
}

# 在远端以 root 执行一条命令；参数逐个用 printf %q 转义后再拼接（审计 X-03）。
# 用法：remote_sudo "$REMOTE" mkdir -p "$INSTALL_DIR/backend" "$INSTALL_DIR/frontend-dist"
remote_sudo() {
    local host="$1"
    shift
    local quoted=""
    local arg
    for arg in "$@"; do
        printf -v arg_q '%q' "$arg"
        quoted+="$arg_q "
    done
    remote_exec "$host" "sudo ${quoted% }"
}

# 生成「确保运行用户/组 gipfel 存在」的命令片段（审计 X-30）。
#
# 改前 push 模式先 `sudo chown -R gipfel:gipfel '$INSTALL_DIR/backend' 2>/dev/null || true`
# （静默吞错），而建用户（`sudo useradd -r -s /usr/sbin/nologin gipfel || true`，**无 `-U`**）
# 排在它后面的服务配置步骤里：目标机没有该用户时 chown 静默失败，文件留在 root:root ——
# 服务能起来，但以 gipfel 身份写 db.sqlite3 / uploads 时权限拒绝（不报错的隐性故障）。
# 另外 `useradd` 不带 `-U` 时是否建同名组取决于目标机 `/etc/login.defs` 的 `USERGROUPS_ENAB`：
# 为 no 的机器上只建用户不建组 → `chown gipfel:gipfel` 报 `invalid group`，
# 且 `deploy/*.service` 的 `Group=gipfel` 会让服务以 216/GROUP 启动失败。
# pull 模式的 chown 更是连 `|| true` 都没有，`set -e` 下直接中止迁移。
#
# 片段语义：用户与组一起校验；组已存在则用 `-g` 复用（`-U` 会以 "group … exists" 失败）；
# 仍失败则 exit 1 并给出可操作提示。用法：
#   remote_exec "$REMOTE" "$(_ensure_runtime_user_cmd)"
#   eval "$(_ensure_runtime_user_cmd)"     # 本机执行
_ensure_runtime_user_cmd() {
    cat <<EOF
if ! id gipfel >/dev/null 2>&1 || ! getent group gipfel >/dev/null 2>&1; then
    if getent group gipfel >/dev/null 2>&1; then
        sudo useradd -r -s /usr/sbin/nologin -g gipfel -d '$INSTALL_DIR' gipfel
    else
        sudo useradd -r -s /usr/sbin/nologin -U -d '$INSTALL_DIR' gipfel
    fi
fi
if ! id gipfel >/dev/null 2>&1 || ! getent group gipfel >/dev/null 2>&1; then
    echo '[ERROR] 运行用户/组 gipfel 创建失败：请在目标机手工执行 useradd -r -s /usr/sbin/nologin -U gipfel 后重试' >&2
    exit 1
fi
EOF
}

# rsync 传输
rsync_transfer() {
    local src="$1"
    local dst="$2"
    local extra_opts="${3:-}"

    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY-RUN] rsync $src → $dst"
        return 0
    fi

    rsync -avz --progress \
        -e "$RSYNC_SSH" \
        --timeout=300 \
        $extra_opts \
        "$src" "$dst"
}

# ==================== 数据清单 ====================
# 需要迁移的数据（相对于 INSTALL_DIR）
DATA_ITEMS=(
    "backend/db.sqlite3"          # SQLite 数据库
    "backend/uploads"             # 用户上传文件
    "backend/.env"                # 环境变量配置
    "backend/logs"                # 应用日志
    "backend/staticfiles"         # Django 静态文件
    "backend/logviewer/staticfiles"  # 日志查看器静态文件
)

# 需要迁移的配置文件
CONFIG_ITEMS=(
    "deploy/gipfel.service"
    "deploy/gipfel-wsgi.service"   # C1-a：gunicorn(WSGI) 的 unit，缺它 /api/ 会 502
    "deploy/logviewer.service"
    "deploy/nginx-gipfel.conf"
)

# ==================== 主流程 ====================

log_step "=========================================="
log_step "Gipfel 服务器迁移工具"
log_step "模式: $MODE"
log_step "安装目录: $INSTALL_DIR"
log_step "=========================================="

# ---------- 推送模式 ----------
if [[ "$MODE" == "push" ]]; then
    REMOTE="$TARGET"

    log_step "[1/6] 检查本机源目录..."
    if [[ ! -d "$INSTALL_DIR" ]]; then
        log_error "本机安装目录不存在: $INSTALL_DIR"
        exit 1
    fi
    log_info "源目录检查通过"

    log_step "[2/6] 检查目标服务器连接..."
    if ! check_remote_conn "$REMOTE"; then
        log_error "无法连接到目标服务器: $REMOTE"
        log_error "请检查 SSH 连接和防火墙设置"
        exit 1
    fi
    log_info "目标服务器连接正常"

    # 检查目标服务器依赖
    log_step "[3/6] 检查目标服务器环境..."
    for cmd in rsync python3 pip3 nginx systemctl; do
        if check_remote_command "$REMOTE" "$cmd"; then
            log_info "  ✓ $cmd 已安装"
        else
            log_warn "  ✗ $cmd 未安装（后续可能需要手动安装）"
        fi
    done

    # 在目标创建安装目录（审计 X-03：改用逐参数转义的 remote_sudo，不再拼裸字符串）
    log_step "[4/6] 准备目标目录..."
    remote_sudo "$REMOTE" mkdir -p "$INSTALL_DIR/backend" "$INSTALL_DIR/frontend-dist"

    # 同步数据文件
    log_step "[5/6] 同步数据文件..."
    for item in "${DATA_ITEMS[@]}"; do
        src="$INSTALL_DIR/$item"
        if [[ -e "$src" ]]; then
            log_info "同步: $item"
            # 确保目标目录存在
            remote_sudo "$REMOTE" mkdir -p "$(dirname "$INSTALL_DIR/$item")"
            # 审计 X-02：`.env` 含全部密钥，传输时**强制 600**（改前依赖 umask，落到 644 世界可读）
            if [[ "$item" == *".env" ]]; then
                rsync_transfer "$src" "$REMOTE:$(dirname "$INSTALL_DIR/$item")/" "--chmod=F600"
            else
                rsync_transfer "$src" "$REMOTE:$(dirname "$INSTALL_DIR/$item")/"
            fi
        else
            log_warn "跳过（不存在）: $item"
        fi
    done

    # 同步代码（如果目标目录为空或 --force）
    log_info "同步项目代码..."
    rsync_transfer "$INSTALL_DIR/backend/" "$REMOTE:$INSTALL_DIR/backend/" \
        "--exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='db.sqlite3' --exclude='uploads' --exclude='logs' --exclude='.env'"

    rsync_transfer "$INSTALL_DIR/frontend-dist/" "$REMOTE:$INSTALL_DIR/frontend-dist/"

    rsync_transfer "$INSTALL_DIR/deploy/" "$REMOTE:$INSTALL_DIR/deploy/"

    # 修复权限（审计 X-03：用 remote_sudo 逐参数转义；chmod/chown 的参数不再裸拼）
    # 审计 X-30：先确保运行用户/组存在再 chown，且不再用 2>/dev/null 吞错。改前顺序相反
    # （chown 在前、建用户在后且无 -U）：目标机没有该用户时 chown 静默失败，文件留在
    # root:root —— 服务能起来但写 db.sqlite3/uploads 时权限拒绝（不报错的隐性故障）。
    log_info "修复文件权限..."
    remote_exec "$REMOTE" "$(_ensure_runtime_user_cmd)"
    remote_exec "$REMOTE" "sudo chown -R gipfel:gipfel '$INSTALL_DIR/backend'"
    remote_exec "$REMOTE" "sudo chmod 600 '$INSTALL_DIR/backend/.env' 2>/dev/null || true"
    remote_exec "$REMOTE" "sudo chmod 755 '$INSTALL_DIR/backend/uploads' 2>/dev/null || true"

    # 服务配置
    if [[ "$SKIP_SERVICES" == false ]]; then
        log_step "[6/6] 配置目标服务器服务..."
        remote_exec "$REMOTE" "
            # 运行用户/组已在 [5/6] 步确保存在（审计 X-30：建用户移到 chown 之前，此处不再重复）

            # 安装 systemd 服务
            # C1-a：三个 unit 都要装 —— 少了 gipfel-wsgi.service，nginx 的准 upstream
            # gipfel_django(:8002) 会指向空气，/api/ 全 502（daphne 只在 :8000 回环上兜底）。
            sudo cp '$INSTALL_DIR/deploy/gipfel.service' /etc/systemd/system/
            sudo cp '$INSTALL_DIR/deploy/gipfel-wsgi.service' /etc/systemd/system/
            sudo cp '$INSTALL_DIR/deploy/logviewer.service' /etc/systemd/system/
            sudo systemctl daemon-reload

            # 提示用户手动完成剩余配置
            echo ''
            echo '=========================================='
            echo '数据同步完成！请在目标服务器上完成以下步骤：'
            echo '=========================================='
            echo ''
            echo '1. 安装 Python 依赖：'
            echo '   cd $INSTALL_DIR/backend'
            echo '   python3 -m venv .venv'
            echo '   source .venv/bin/activate'
            echo '   pip install -r requirements.txt'
            echo ''
            echo '2. 运行数据库迁移：'
            echo '   python manage.py migrate'
            echo '   python manage.py collectstatic --noinput'
            echo ''
            echo '3. 配置 nginx：'
            echo '   sudo cp $INSTALL_DIR/deploy/nginx-gipfel.conf /etc/nginx/sites-available/gipfel.conf'
            echo '   sudo sed -i \"s|__INSTALL_DIR__|$INSTALL_DIR|g\" /etc/nginx/sites-available/gipfel.conf'
            echo '   sudo sed -i \"s|__DOMAIN__|YOUR_DOMAIN|g\" /etc/nginx/sites-available/gipfel.conf'
            echo '   sudo ln -sf /etc/nginx/sites-available/gipfel.conf /etc/nginx/sites-enabled/'
            echo '   sudo nginx -t && sudo systemctl reload nginx'
            echo ''
            echo '4. 启动服务：'
            echo '   sudo systemctl enable gipfel gipfel-wsgi gipfel-logviewer'
            echo '   sudo systemctl start gipfel gipfel-wsgi gipfel-logviewer'
            echo ''
            echo '5. 验证（C1-a：两个后端进程都要在）：'
            echo '   curl http://127.0.0.1:8002/api/health                                  # WSGI(gunicorn)'
            echo '   curl \"http://127.0.0.1:8000/socket.io/?EIO=4&transport=polling\"        # daphne(ASGI) 期望 200'
            echo ''
        "
    else
        log_info "跳过服务配置（--skip-services）"
    fi

    log_info "=========================================="
    log_info "推送完成！"
    log_info "=========================================="

# ---------- 拉取模式 ----------
elif [[ "$MODE" == "pull" ]]; then
    REMOTE="$SOURCE"

    log_step "[1/6] 检查源服务器连接..."
    if ! check_remote_conn "$REMOTE"; then
        log_error "无法连接到源服务器: $REMOTE"
        exit 1
    fi
    log_info "源服务器连接正常"

    # 检查源目录
    log_step "[2/6] 检查源服务器安装目录..."
    if ! remote_exec "$REMOTE" "test -d $INSTALL_DIR"; then
        log_error "源服务器安装目录不存在: $INSTALL_DIR"
        exit 1
    fi
    log_info "源目录检查通过"

    # 创建本地备份目录
    log_step "[3/6] 创建本地备份目录..."
    if [[ "$DRY_RUN" != true ]]; then
        mkdir -p "$BACKUP_DIR"
    fi
    log_info "备份目录: $BACKUP_DIR"

    # 拉取数据文件
    log_step "[4/6] 从源服务器拉取数据..."
    for item in "${DATA_ITEMS[@]}"; do
        remote_path="$REMOTE:$INSTALL_DIR/$item"
        local_path="$BACKUP_DIR/$item"

        # 检查远程文件是否存在
        if remote_exec "$REMOTE" "test -e $INSTALL_DIR/$item" 2>/dev/null; then
            log_info "拉取: $item"
            mkdir -p "$(dirname "$local_path")"
            rsync_transfer "$remote_path" "$(dirname "$local_path")/"
        else
            log_warn "跳过（不存在）: $item"
        fi
    done

    # 拉取代码
    log_info "拉取项目代码..."
    mkdir -p "$BACKUP_DIR/backend" "$BACKUP_DIR/frontend-dist" "$BACKUP_DIR/deploy"
    rsync_transfer "$REMOTE:$INSTALL_DIR/backend/" "$BACKUP_DIR/backend/" \
        "--exclude='.venv' --exclude='__pycache__' --exclude='*.pyc'"
    rsync_transfer "$REMOTE:$INSTALL_DIR/frontend-dist/" "$BACKUP_DIR/frontend-dist/"
    rsync_transfer "$REMOTE:$INSTALL_DIR/deploy/" "$BACKUP_DIR/deploy/"

    # 恢复到本地安装目录
    log_step "[5/6] 恢复到本地安装目录..."
    RESTORED_ITEMS=()
    if [[ "$DRY_RUN" == false ]]; then
        mkdir -p "$INSTALL_DIR"
        # 备份现有数据（如有）
        if [[ -d "$INSTALL_DIR/backend" ]]; then
            local existing_backup="$INSTALL_DIR/_backup/$(date +%Y%m%d_%H%M%S)"
            log_info "备份现有数据到: $existing_backup"
            mkdir -p "$existing_backup"
            # 审计 X-04：这里是「本机原有数据」的唯一副本，失败必须显式报错（改前是 `|| true` 吞掉）
            for keep in backend/db.sqlite3 backend/.env backend/uploads; do
                if [[ -e "$INSTALL_DIR/$keep" ]]; then
                    if ! cp -a "$INSTALL_DIR/$keep" "$existing_backup/"; then
                        log_error "备份本机原有数据失败: $keep —— 已中止，避免继续覆盖"
                        exit 1
                    fi
                fi
            done
        fi

        # 审计 X-04：改前每条 `cp -a ... 2>/dev/null || true` 会把失败吞掉，随后仍打印
        # 「拉取完成！数据已恢复到 …」—— 新机上没有 db.sqlite3 时 Django 会新建空库并 migrate，
        # 表现为「迁移成功但数据全没了」，运维很可能据此删掉旧服务器（不可逆）。
        # 现在：关键项（数据库/配置/上传）失败即中止；其余项失败如实记入问题列表。
        RESTORE_FAILED=()
        for item in "backend/db.sqlite3" "backend/.env" "backend/uploads" \
                    "backend/logs" "backend/staticfiles" "frontend-dist" "deploy"; do
            src="$BACKUP_DIR/$item"
            if [[ ! -e "$src" ]]; then
                if [[ "$item" == "backend/db.sqlite3" ]]; then
                    log_error "拉取结果里没有 backend/db.sqlite3 —— 数据库未成功拉取，已中止恢复"
                    exit 1
                fi
                continue
            fi
            if cp -a "$src" "$INSTALL_DIR/$(dirname "$item")/"; then
                RESTORED_ITEMS+=("$item")
            else
                log_error "恢复失败: $item"
                RESTORE_FAILED+=("$item")
            fi
        done

        # 恢复后必须真的校验数据库（非空 + SQLite 文件头）
        db_path="$INSTALL_DIR/backend/db.sqlite3"
        if [[ ! -s "$db_path" ]]; then
            log_error "恢复后数据库缺失或为空: $db_path（已中止，未报告成功）"
            exit 1
        fi
        if [[ "$(head -c 15 "$db_path" 2>/dev/null || true)" != "SQLite format 3" ]]; then
            log_error "恢复后的数据库不是合法 SQLite 文件: $db_path（已中止，未报告成功）"
            exit 1
        fi
        if [[ ${#RESTORE_FAILED[@]} -gt 0 ]]; then
            log_error "以下条目恢复失败：${RESTORE_FAILED[*]}（数据库本身已校验通过）"
        fi
    fi

    # 服务配置
    if [[ "$SKIP_SERVICES" == false ]]; then
        log_step "[6/6] 配置本地服务..."
        if [[ "$DRY_RUN" == false ]]; then
            # 运行用户/组（审计 X-30：用户与组一起校验、组已存在用 -g 复用、失败即中止；
            # 改前 `useradd … || true` 不带 -U，USERGROUPS_ENAB=no 的机器上只建用户不建组）
            eval "$(_ensure_runtime_user_cmd)"

            # 设置权限
            sudo chown -R gipfel:gipfel "$INSTALL_DIR/backend"
            sudo chmod 600 "$INSTALL_DIR/backend/.env" 2>/dev/null || true

            # 安装 systemd 服务（C1-a：daphne + gunicorn WSGI 两个后端 unit，缺一会让 /api/ 502）
            sudo cp "$INSTALL_DIR/deploy/gipfel.service" /etc/systemd/system/
            sudo cp "$INSTALL_DIR/deploy/gipfel-wsgi.service" /etc/systemd/system/
            sudo cp "$INSTALL_DIR/deploy/logviewer.service" /etc/systemd/system/
            sudo systemctl daemon-reload
        fi
    fi

    log_info "=========================================="
    # 审计 X-04：收尾按**事实**报告（列出实际恢复成功的条目），不再无条件宣称「数据已恢复」
    log_info "拉取完成！已恢复条目: ${RESTORED_ITEMS[*]:-（无）}"
    log_info "数据库已校验: $INSTALL_DIR/backend/db.sqlite3（非空 + SQLite 文件头）"
    if [[ "$KEEP_BACKUP" == true ]]; then
        log_info "备份保存在: $BACKUP_DIR"
    else
        log_info "备份目录（退出时自动清理，如需保留请加 --keep-backup）: $BACKUP_DIR"
    fi
    log_info "=========================================="
    log_info ""
    log_info "请完成以下步骤："
    log_info "1. 安装 Python 依赖: cd $INSTALL_DIR/backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    log_info "2. 运行迁移: python manage.py migrate && python manage.py collectstatic --noinput"
    log_info "3. 配置 nginx: 参考 deploy/README.md"
    log_info "4. 启动服务: sudo systemctl enable gipfel gipfel-wsgi gipfel-logviewer && sudo systemctl start gipfel gipfel-wsgi gipfel-logviewer"
fi

log_info ""
log_info "迁移脚本执行完毕！"
