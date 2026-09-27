#!/usr/bin/env bash
# X-25 运行时探针：SSH 选项数组化与 --ssh-port/--ssh-key/--ssh-known-hosts。
#
# 安全性：不会真的连任何服务器 ——
#   A 段只把脚本里的 SSH_OPTS 构建块抽出来求值并打印 RSYNC_SSH 字符串；
#   B 段用 `.tmp/x25/empty-install`（只有空的 backend/，没有任何同步项）跑真实脚本，
#       循环里每一项都是"跳过（不存在）"，不会触发 rsync。
#
# 用法：bash code_audit/harness/_x25_harness.sh
set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"   # code_audit/harness/ -> 仓库根
SRC="$REPO/scripts/quick-sync.sh"
MIG="$REPO/scripts/migrate-server.sh"
WORK="$REPO/.tmp/x25"
rm -rf "$WORK"; mkdir -p "$WORK/empty-install/backend"

echo "### X-25 探针"

echo "[A] SSH_OPTS 构建块（从 $SRC 原样抽出）"
A1="$(grep -n -F 'SSH_PORT="${SSH_PORT:-22}"' "$SRC" | head -1 | cut -d: -f1)"
A2="$(grep -n -F 'RSYNC_SSH="ssh ${_ssh_opts_q% }"' "$SRC" | head -1 | cut -d: -f1)"
if [[ -n "${A1:-}" && -n "${A2:-}" ]]; then
    sed -n "${A1},${A2}p" "$SRC" > "$WORK/ssh_opts.sh"
    printf '  抽取行 %s-%s（%s 行）\n' "$A1" "$A2" "$(wc -l < "$WORK/ssh_opts.sh" | tr -d ' ')"
    run_case() {
        local label="$1"; shift
        local frag="$WORK/case.sh"
        {
            echo 'log_error() { echo "[ERROR] $*"; }'
            echo 'log_warn()  { echo "[WARN] $*"; }'
            for kv in "$@"; do echo "$kv"; done
            cat "$WORK/ssh_opts.sh"
            echo 'echo "  RSYNC_SSH=[$RSYNC_SSH]"'
        } > "$frag"
        local out rc
        out="$(bash "$frag" 2>&1)"; rc=$?
        printf '  -- %s -> exit=%s\n' "$label" "$rc"
        printf '%s\n' "$out" | sed 's/^/     /'
    }
    run_case "默认（22、无密钥、无 known_hosts）" 'SSH_PORT=' 'SSH_KEY=' 'SSH_KNOWN_HOSTS='
    run_case "2222 端口 + 带空格的私钥路径" \
        'SSH_PORT=2222' 'SSH_KEY="/home/First Last/.ssh/id_ed25519"' 'SSH_KNOWN_HOSTS='
    run_case "指定 known_hosts + 端口非法" \
        'SSH_PORT=abc' 'SSH_KEY=' 'SSH_KNOWN_HOSTS=/etc/ssh/known_hosts'
    run_case "指定 known_hosts（合法端口）" \
        'SSH_PORT=2222' 'SSH_KEY=' 'SSH_KNOWN_HOSTS=/etc/ssh/known_hosts'
else
    echo "  （改前：脚本里没有 SSH_OPTS 构建块，A 段跳过）"
fi

echo
echo "[B] 真实脚本的前置路径（push 到不存在的远端，但没有任何同步项 → 不触发 rsync）"
run_quick() {
    local label="$1"; shift
    local out rc
    out="$(cd "$WORK" && bash "$SRC" "$@" 2>&1)"; rc=$?
    printf '  -- %s -> exit=%s\n' "$label" "$rc"
    printf '%s\n' "$out" | grep -E 'WARN|ERROR|INFO|STEP' | sed 's/^/     /' | head -6
}
run_quick "push empty-install（默认）" push user@nonexistent empty-install
run_quick "push empty-install --ssh-port 2222 --ssh-key /tmp/k --ssh-known-hosts /tmp/kh" \
    push user@nonexistent empty-install --ssh-port 2222 --ssh-key /tmp/k --ssh-known-hosts /tmp/kh
run_quick "push empty-install --ssh-port abc" push user@nonexistent empty-install --ssh-port abc
run_quick "缺参时打印用法" push

echo
echo "[C] migrate-server.sh 的 --ssh-known-hosts"
grep -c -F -- '--ssh-known-hosts' "$MIG" | sed 's/^/      --ssh-known-hosts 出现次数: /'
grep -c -F 'UserKnownHostsFile=' "$MIG" | sed 's/^/      UserKnownHostsFile= 出现次数: /'
out="$(cd "$WORK" && bash "$MIG" --ssh-known-hosts /tmp/nonexistent-kh 2>&1)"; rc=$?
printf '  仅给 --ssh-known-hosts 不给 --mode -> exit=%s（应 1）\n' "$rc"
printf '%s\n' "$out" | head -2 | sed 's/^/     /'
