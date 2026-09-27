"""X-21：migrate-server.sh 的 --dry-run 零网络副作用、退出码语义与 --ssh-port 校验（CRLF 保持）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "migrate-server.sh"

PATCHES: list[tuple[str, str]] = []

# ---------- 1. usage() 接受状态码 ----------
PATCHES.append((
    """# ==================== 参数解析 ====================
usage() {
    cat <<EOF""",
    """# ==================== 参数解析 ====================
# 审计 X-21：改前 usage() 固定 `exit 0`，于是三类调用——用户主动求助（-h）、参数拼错、
# 必填参数缺失——退出码全是 0，`&&` 串联或监控包装无法区分"看过帮助"和"参数写错了"。
# 现在接受一个状态码：-h/--help → 0；参数错误 → 2；必填缺失 → 1。
usage() {
    local _usage_status="${1:-0}"
    cat <<EOF""",
))

PATCHES.append((
    """  sudo $0 --mode push --target root@192.168.1.100 --ssh-port 2222 --ssh-key ~/.ssh/id_rsa
EOF
    exit 0
}""",
    """  sudo $0 --mode push --target root@192.168.1.100 --ssh-port 2222 --ssh-key ~/.ssh/id_rsa
EOF
    exit "$_usage_status"
}""",
))

# ---------- 2. 分派与必填校验的退出码 ----------
PATCHES.append((
    """        -h|--help)    usage ;;
        *)            log_error "未知参数: $1"; usage ;;""",
    """        -h|--help)    usage 0 ;;
        *)            log_error "未知参数: $1"; usage 2 ;;""",
))

PATCHES.append((
    """if [[ -z "$MODE" ]]; then
    log_error "必须指定 --mode (push 或 pull)"
    usage
fi

if [[ "$MODE" == "push" && -z "$TARGET" ]]; then
    log_error "推送模式必须指定 --target USER@HOST"
    usage
fi

if [[ "$MODE" == "pull" && -z "$SOURCE" ]]; then
    log_error "拉取模式必须指定 --source USER@HOST"
    usage
fi""",
    """if [[ -z "$MODE" ]]; then
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
fi""",
))

# ---------- 3. --ssh-port 校验 ----------
PATCHES.append((
    """if [[ "$INSTALL_DIR" == *".."* ]]; then
    log_error "--install-dir 不得包含 '..'（收到: $INSTALL_DIR）"
    exit 1
fi""",
    """if [[ "$INSTALL_DIR" == *".."* ]]; then
    log_error "--install-dir 不得包含 '..'（收到: $INSTALL_DIR）"
    exit 1
fi

# 审计 X-21 / X-25：`--ssh-port` 改前不做数字/范围校验，`-p abc` 或 `-p 99999` 会被原样
# 拼进 `ssh -p`，报错信息晦涩（"Bad port 'abc'"）。这里按"参数错误"提前拦下（退出码 2）。
if ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || (( SSH_PORT < 1 || SSH_PORT > 65535 )); then
    log_error "--ssh-port 必须是 1-65535 的整数（收到: $SSH_PORT）"
    exit 2
fi""",
))

# ---------- 4. dry-run 下不得真实建连 ----------
PATCHES.append((
    """# 检查远程命令是否存在
check_remote_command() {
    local host="$1"
    local cmd="$2"
    ssh "${SSH_OPTS[@]}" "$host" "command -v $cmd >/dev/null 2>&1" 2>/dev/null
}""",
    """# 检查远程命令是否存在（审计 X-21：--dry-run 下不得建立真实连接）
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
}""",
))

PATCHES.append((
    """    log_step "[2/6] 检查目标服务器连接..."
    if ! ssh "${SSH_OPTS[@]}" "$REMOTE" "echo ok" >/dev/null 2>&1; then""",
    """    log_step "[2/6] 检查目标服务器连接..."
    if ! check_remote_conn "$REMOTE"; then""",
))

PATCHES.append((
    """    log_step "[1/6] 检查源服务器连接..."
    if ! ssh "${SSH_OPTS[@]}" "$REMOTE" "echo ok" >/dev/null 2>&1; then""",
    """    log_step "[1/6] 检查源服务器连接..."
    if ! check_remote_conn "$REMOTE"; then""",
))

PATCHES.append((
    """    log_step "[3/6] 创建本地备份目录..."
    mkdir -p "$BACKUP_DIR\"""",
    """    log_step "[3/6] 创建本地备份目录..."
    if [[ "$DRY_RUN" != true ]]; then
        mkdir -p "$BACKUP_DIR"
    fi""",
))

PATCHES.append((
    """        if ssh "${SSH_OPTS[@]}" "$REMOTE" "test -e $INSTALL_DIR/$item" 2>/dev/null; then""",
    """        if remote_exec "$REMOTE" "test -e $INSTALL_DIR/$item" 2>/dev/null; then""",
))


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    changed = 0
    for old, new in PATCHES:
        o = old.replace("\n", "\r\n")
        n = new.replace("\n", "\r\n")
        if o not in text:
            if n in text:
                print("[skip] 已打过补丁")
                continue
            print(f"[fail] 未找到锚点: {old.splitlines()[0][:70]!r}")
            return 1
        text = text.replace(o, n, 1)
        changed += 1
    TARGET.write_bytes(text.encode("utf-8"))
    b = TARGET.read_bytes()
    crlf = b.count(b"\r\n")
    print(f"[ok]   替换 {changed}/{len(PATCHES)} 处；CRLF={crlf} 裸LF={b.count(chr(10).encode()) - crlf}")
    return 0 if changed == len(PATCHES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
