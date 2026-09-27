"""X-20：quick-sync.sh 的 INSTALL_DIR 绝对化与推送源前置校验（CRLF 保持）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "quick-sync.sh"

OLD_HEADER = """#   指定安装目录：
#     bash scripts/quick-sync.sh push user@remote-ip /opt/gipfel
# ============================================================

set -euo pipefail
"""

NEW_HEADER = """#   指定安装目录：
#     bash scripts/quick-sync.sh push user@remote-ip /opt/gipfel
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
"""

OLD_PARSE = """if [[ -z "$ACTION" || -z "$REMOTE" ]]; then
    echo "用法: $0 <push|pull> <user@host> [install-dir]"
    echo ""
    echo "示例:"
    echo "  $0 push root@192.168.1.100"
    echo "  $0 pull root@192.168.1.50 /opt/gipfel"
    exit 1
fi
"""

NEW_PARSE = """if [[ -z "$ACTION" || -z "$REMOTE" ]]; then
    echo "用法: $0 <push|pull> <user@host> [install-dir]"
    echo ""
    echo "示例:"
    echo "  $0 push root@192.168.1.100"
    echo "  $0 pull root@192.168.1.50 /opt/gipfel"
    echo ""
    echo "install-dir 可省略（默认 /opt/gipfel）。写成相对路径时按当前目录解析。"
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
"""

OLD_UNKNOWN = """    else
        log_error "未知操作: $ACTION（应为 push 或 pull）"
        exit 1
    fi
"""

NEW_UNKNOWN = """    fi
"""


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    changed = 0
    for old, new in (
        (OLD_HEADER, NEW_HEADER),
        (OLD_PARSE, NEW_PARSE),
        (OLD_UNKNOWN, NEW_UNKNOWN),
    ):
        o = old.replace("\n", "\r\n")
        n = new.replace("\n", "\r\n")
        if n in text and o not in text:
            print("[skip] 已打过补丁")
            continue
        if o not in text:
            print(f"[fail] 未找到锚点: {old.splitlines()[0][:60]!r}")
            return 1
        text = text.replace(o, n, 1)
        changed += 1
    TARGET.write_bytes(text.encode("utf-8"))
    b = TARGET.read_bytes()
    crlf = b.count(b"\r\n")
    print(f"[ok]   替换 {changed} 处；CRLF={crlf} 裸LF={b.count(chr(10).encode()) - crlf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
