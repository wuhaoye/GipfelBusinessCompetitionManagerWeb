#!/usr/bin/env bash
# X-24 运行时探针：把 deploy-linux.sh 的「问题计数定义」与「收尾块」抽出来跑退出码决策表。
#
# 抽取的是脚本里的**真实片段**（不改一字）：定义块 = `DEPLOY_PROBLEMS=0` 到 `problem() {...}`，
# 收尾块 = `# ---------------- 收尾 ----------------` 到 `echo "━` 之前。
# 然后注入模拟的问题数 SIM_PROBLEMS / SIM_ALLOW 跑三种组合。
#
# 用法：bash code_audit/harness/_x24_harness.sh
set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"   # code_audit/harness/ -> 仓库根
SRC="$REPO/scripts/deploy-linux.sh"
WORK="$REPO/.tmp/x24"
rm -rf "$WORK"; mkdir -p "$WORK"

D1="$(grep -n -F 'DEPLOY_PROBLEMS=0' "$SRC" | head -1 | cut -d: -f1)"
D2="$(grep -n -F 'problem() {' "$SRC" | head -1 | cut -d: -f1)"
T1="$(grep -n -F '# ---------------- 收尾 ----------------' "$SRC" | head -1 | cut -d: -f1)"
T2="$(( $(grep -n -F 'echo "━' "$SRC" | head -1 | cut -d: -f1) - 1 ))"

: > "$WORK/defs.sh"
: > "$WORK/tail.sh"
[[ -n "${D1:-}" && -n "${D2:-}" ]] && sed -n "${D1},${D2}p" "$SRC" > "$WORK/defs.sh"
[[ -n "${T1:-}" && -n "${T2:-}" ]] && sed -n "${T1},${T2}p" "$SRC" > "$WORK/tail.sh"

echo "### X-24 探针（定义块=${D1:-无}-${D2:-无}，收尾块=${T1}-${T2}）"
printf '  defs.sh 行数=%s（0 表示改前没有该定义块）\n' "$(wc -l < "$WORK/defs.sh" | tr -d ' ')"

for problems in 0 3; do
    for allow in 0 1; do
        frag="$WORK/frag_p${problems}_a${allow}.sh"
        {
            echo 'ok()   { echo "[OK] $*"; }'
            echo 'warn() { echo "[WARN] $*"; }'
            echo 'err()  { echo "[ERROR] $*"; exit 1; }'
            cat "$WORK/defs.sh"
            echo "DEPLOY_PROBLEMS=$problems"
            echo "ALLOW_PARTIAL=$allow"
            cat "$WORK/tail.sh"
            echo 'echo "---END---"'
        } > "$frag"
        out="$(bash "$frag" 2>&1)"
        rc=$?
        last="$(printf '%s' "$out" | grep -E '^\[(OK|ERROR)\]' | tail -1)"
        printf '  问题数=%s --allow-partial=%s -> exit=%s  最后一行: %s\n' \
            "$problems" "$allow" "$rc" "$last"
    done
done

echo
echo "### 源码级：nginx 相关降级分支是否都计入问题数"
printf '  reload 失败分支用 problem: %s\n' "$(grep -c 'problem "nginx reload 失败' "$SRC")"
printf '  start  失败分支用 problem: %s\n' "$(grep -c 'problem "nginx 启动失败' "$SRC")"
printf '  nginx 非 active 用 problem: %s\n' "$(grep -c 'problem "nginx 未处于 active' "$SRC")"
printf '  日志查看器探针失败用 problem: %s\n' "$(grep -c 'problem "日志查看器站点探针' "$SRC")"
printf '  /api/health 探针失败用 problem: %s\n' "$(grep -c 'problem "后端 /api/health' "$SRC")"
printf '  收尾处仍无条件 ok "部署完成！": %s（应为 0）\n' \
    "$(grep -c '^ok "部署完成！"$' "$SRC")"
