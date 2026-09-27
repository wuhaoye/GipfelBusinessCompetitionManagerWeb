"""X-25：quick-sync.sh 支持 --ssh-port/--ssh-key/--ssh-known-hosts，SSH 选项数组化（CRLF 保持）。

migrate-server.sh 侧：X-03 已把 SSH_OPTS 改成数组、X-21 已补 --ssh-port 校验；本次再补
--ssh-known-hosts（给了就切到 StrictHostKeyChecking=yes）。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUICK = REPO / "scripts" / "quick-sync.sh"
MIG = REPO / "scripts" / "migrate-server.sh"

QUICK_PATCHES: list[tuple[str, str]] = []

# 1) 用法说明
QUICK_PATCHES.append((
    """#   指定安装目录：
#     bash scripts/quick-sync.sh push user@remote-ip /opt/gipfel
#""",
    """#   指定安装目录：
#     bash scripts/quick-sync.sh push user@remote-ip /opt/gipfel
#
#   非 22 端口 / 指定私钥 / 指定 known_hosts：
#     bash scripts/quick-sync.sh push user@remote-ip /opt/gipfel \\
#         --ssh-port 2222 --ssh-key ~/.ssh/id_ed25519 --ssh-known-hosts ~/.ssh/known_hosts
#
# 审计 X-25：改前本脚本**完全不支持**自定义端口与私钥，只用
#   `-e "ssh -o StrictHostKeyChecking=accept-new"`
# 在非 22 端口的服务器上根本无法工作，且首次连接会自动信任任意主机密钥。
# 现在与 migrate-server.sh 一致：`-o BatchMode=yes`（不再回退到交互式口令提示而挂住）、
# 端口范围校验、可选 known_hosts 固定（给了就改 StrictHostKeyChecking=yes）。
# 注意：`ssh -i <私钥路径>` 会把路径暴露在同机 `ps` 里 —— 生产环境更推荐把 Host/Port/IdentityFile
# 写进 `~/.ssh/config`，脚本里就不必再传 --ssh-key。
#""",
))

# 2) 参数解析：位置参数 + 选项
QUICK_PATCHES.append((
    """# 参数
ACTION="${1:-}"
REMOTE="${2:-}"
INSTALL_DIR="${3:-/opt/gipfel}"
""",
    """# 参数（审计 X-25：位置参数保持不变，另支持若干 --ssh-* 选项）
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
""",
))

# 3) usage 文本
QUICK_PATCHES.append((
    """    echo "用法: $0 <push|pull> <user@host> [install-dir]"
    echo ""
    echo "示例:"
    echo "  $0 push root@192.168.1.100"
    echo "  $0 pull root@192.168.1.50 /opt/gipfel"
    echo ""
    echo "install-dir 可省略（默认 /opt/gipfel）。写成相对路径时按当前目录解析。"
    exit 1""",
    """    echo "用法: $0 <push|pull> <user@host> [install-dir] [--ssh-port PORT] [--ssh-key PATH] [--ssh-known-hosts PATH]"
    echo ""
    echo "示例:"
    echo "  $0 push root@192.168.1.100"
    echo "  $0 pull root@192.168.1.50 /opt/gipfel"
    echo "  $0 push root@192.168.1.100 /opt/gipfel --ssh-port 2222 --ssh-key ~/.ssh/id_ed25519"
    echo ""
    echo "install-dir 可省略（默认 /opt/gipfel）。写成相对路径时按当前目录解析。"
    echo "--ssh-known-hosts 给出后，主机密钥按该文件严格校验（StrictHostKeyChecking=yes）。"
    exit 1""",
))

# 4) SSH_OPTS 数组（插在 SYNC_ITEMS 之前）
QUICK_PATCHES.append((
    """# 同步的数据列表
SYNC_ITEMS=(""",
    """# 审计 X-25：SSH 选项改为 bash 数组，逐参数独立传递（含空格的私钥路径不再被分词拆开），
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
SYNC_ITEMS=(""",
))

# 5) 四处 rsync -e 改为统一变量
QUICK_PATCHES.append((
    """                rsync -avz --progress -e "ssh -o StrictHostKeyChecking=accept-new" \\
                    "$snap" "$REMOTE:$dst\"""",
    """                rsync -avz --progress -e "$RSYNC_SSH" \\
                    "$snap" "$REMOTE:$dst\"""",
))

QUICK_PATCHES.append((
    """                    rsync -avz --progress --chmod=F600 \\
                        -e "ssh -o StrictHostKeyChecking=accept-new" "$src" "$REMOTE:$dst\"""",
    """                    rsync -avz --progress --chmod=F600 \\
                        -e "$RSYNC_SSH" "$src" "$REMOTE:$dst\"""",
))

QUICK_PATCHES.append((
    """                    rsync -avz --progress -e "ssh -o StrictHostKeyChecking=accept-new" "$src" "$REMOTE:$dst\"""",
    """                    rsync -avz --progress -e "$RSYNC_SSH" "$src" "$REMOTE:$dst\"""",
))

QUICK_PATCHES.append((
    """        rsync -avz --progress -e "ssh -o StrictHostKeyChecking=accept-new" "$REMOTE:$src" "$(dirname "$src")/\"""",
    """        rsync -avz --progress -e "$RSYNC_SSH" "$REMOTE:$src" "$(dirname "$src")/\"""",
))

MIG_PATCHES: list[tuple[str, str]] = []

MIG_PATCHES.append((
    """  --ssh-port PORT       SSH 端口（默认: 22）
  --ssh-key PATH        SSH 私钥路径""",
    """  --ssh-port PORT       SSH 端口（默认: 22）
  --ssh-key PATH        SSH 私钥路径（注意：路径会出现在同机 ps 中，
                        生产环境建议改用 ~/.ssh/config 的 IdentityFile）
  --ssh-known-hosts PATH  known_hosts 文件；给出后按该文件严格校验主机密钥
                        （StrictHostKeyChecking=yes），不再 accept-new""",
))

MIG_PATCHES.append((
    """SSH_PORT=22
SSH_KEY=\"\"""",
    """SSH_PORT=22
SSH_KEY=""
SSH_KNOWN_HOSTS=\"\"""",
))

MIG_PATCHES.append((
    """        --ssh-key)    SSH_KEY="$2"; shift 2 ;;""",
    """        --ssh-key)    SSH_KEY="$2"; shift 2 ;;
        --ssh-known-hosts) SSH_KNOWN_HOSTS="$2"; shift 2 ;;""",
))

MIG_PATCHES.append((
    """SSH_OPTS=(-p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes)
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS+=(-i "$SSH_KEY")
fi""",
    """SSH_OPTS=(-p "$SSH_PORT" -o ConnectTimeout=10 -o BatchMode=yes)
# 审计 X-25：改前无条件 `accept-new` —— 首次连接自动信任任意主机密钥，只防后续变更、
# 不防首次中间人；而本脚本的流程是"以 root 推送 .env（含全部密钥）"。现在允许传入
# known_hosts：给了就按文件严格校验（yes），没给才退回 accept-new 并明确告警。
if [[ -n "$SSH_KNOWN_HOSTS" ]]; then
    SSH_OPTS+=(-o "UserKnownHostsFile=$SSH_KNOWN_HOSTS" -o StrictHostKeyChecking=yes)
else
    SSH_OPTS+=(-o StrictHostKeyChecking=accept-new)
    warn "未指定 --ssh-known-hosts：首次连接将自动信任目标主机密钥（只防后续变更，不防首次中间人）。"
fi
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS+=(-i "$SSH_KEY")
    warn "已用 -i 指定私钥；该路径会出现在同机 ps 中，生产环境建议改用 ~/.ssh/config 的 IdentityFile。"
fi""",
))


def _patch(path: Path, patches: list[tuple[str, str]]) -> int:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"   # migrate-server.sh 现在是 LF，quick-sync.sh 是 CRLF
    changed = 0
    for old, new in patches:
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        if o not in text:
            if n in text:
                continue
            print(f"[fail] {path.name}: 未找到锚点 {old.splitlines()[0][:60]!r}")
            return 1
        text = text.replace(o, n, 1)
        changed += 1
    path.write_bytes(text.encode("utf-8"))
    b = path.read_bytes()
    print(f"[ok]   {path.name}: 替换 {changed}/{len(patches)} 处；CRLF={b.count(bytes([13, 10]))}")
    return 0


def main() -> int:
    # 幂等守卫：已打过补丁就直接跳过（避免重复插入——`# 同步的数据列表 / SYNC_ITEMS=(`
    # 这个锚点在打补丁后仍存在，二次运行会再插一份 SSH_OPTS 块）。
    quick = QUICK.read_text(encoding="utf-8")
    if "RSYNC_SSH=" in quick:
        print("[skip] quick-sync.sh 已打过补丁")
    elif _patch(QUICK, QUICK_PATCHES):
        return 1
    mig = MIG.read_text(encoding="utf-8")
    if "SSH_KNOWN_HOSTS" in mig:
        print("[skip] migrate-server.sh 已打过补丁")
        return 0
    return _patch(MIG, MIG_PATCHES)


if __name__ == "__main__":
    raise SystemExit(main())
