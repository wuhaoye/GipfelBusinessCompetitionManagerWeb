#!/usr/bin/env bash
# X-22 运行时探针：抽取 deploy-linux.sh 中"打印初始口令"的两段**真实代码**跑决策表，
# 验证不变式：stdout 是终端 → 显示；不是终端（管道/CI/重定向）→ 绝不显示。
#
# 本机没有 pty（Windows），无法真的造出 TTY，因此**只在抽取出来的片段里**把 `-t 1`
# 替换成 `[[ "${FAKE_TTY:-0}" == "1" ]]`（除此之外一字不改），从而把四种组合都跑一遍。
# 真实脚本里用的是 `-t 1`，这一点由 Python 侧的静态断言负责。
#
# 用法：bash code_audit/harness/_x22_harness.sh
#
# 曾踩过的坑（本次修正）：旧版按"下一行 echo ━ / 下一个 fi"截取片段，加入一行输出后
# 边界即错位 → 片段 `else/fi` 不配对 → bash 语法错误被 `|| true` 吞掉 → 四行恒报"否"，
# 探针变成永远绿灯。现在：① 用 if/fi 嵌套计数取**完整块**；② 片段先 `bash -n` 校验并
# 打印"片段语法 OK"；③ 预置 ADMIN_PW / _SEED_PW（旧版从未注入 → 生成块的泄露永远检测不到）。
set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"   # code_audit/harness/ -> 仓库根
SRC="$REPO/scripts/deploy-linux.sh"
WORK="$REPO/.tmp/x22"
SECRET="Sup3rSecretPw123"
GUARD='if [[ -t 1 ]]; then'

rm -rf "$WORK"
mkdir -p "$WORK/backend"
printf 'SEED_ADMIN_PASSWORD=%s\n' "$SECRET" > "$WORK/backend/.env"

mapfile -t GUARDS < <(grep -n -F -- "$GUARD" "$SRC" | cut -d: -f1)
if [[ "${#GUARDS[@]}" -ne 2 ]]; then
    echo "### X-22 探针失败：守卫语句应为 2 处，实际 ${#GUARDS[@]}"
    exit 1
fi

# 取「从指定行开始、到配对 fi 结束」的完整块（按 if ... then / fi 计数）
extract_block() {
    awk -v s="$1" '
        NR >= s {
            print
            if ($0 ~ /(^|[[:space:]])if[[:space:]].*then[[:space:]]*$/) { depth++ }
            else if ($0 ~ /^[[:space:]]*fi[[:space:]]*$/) { depth--; if (depth <= 0) exit }
        }' "$SRC"
}

echo "### X-22 探针（脚本形态：after；守卫行 ${GUARDS[0]} / ${GUARDS[1]}）"

for fake_tty in 0 1; do
    for flag in 0 1; do
        frag="$WORK/frag_t${fake_tty}_p${flag}.sh"
        {
            echo 'ok()   { echo "[OK] $*"; }'
            echo 'warn() { echo "[WARN] $*" >&2; }'
            printf 'INSTALL_DIR=%q\n' "$WORK"
            printf 'ADMIN_PW=%q\n' "$SECRET"
            printf '_SEED_PW=%q\n' "$SECRET"
            echo "PRINT_SEED_PASSWORD=$flag"
            echo "FAKE_TTY=$fake_tty"
            extract_block "${GUARDS[0]}"
            extract_block "${GUARDS[1]}"
            echo 'echo "---END---"'
        } | sed 's/-t 1/"${FAKE_TTY:-0}" == "1"/g' > "$frag"

        if ! bash -n "$frag" 2>"$WORK/syntax.err"; then
            echo "  片段语法错误（探针自身缺陷，必须修）：$(head -1 "$WORK/syntax.err")"
            exit 1
        fi

        out="$(bash "$frag" 2>&1 || true)"
        if ! grep -q -- '---END---' <<<"$out"; then
            echo "  片段未执行到结尾（探针自身缺陷）：$(head -2 <<<"$out")"
            exit 1
        fi
        if grep -qF "$SECRET" <<<"$out"; then leaked="是"; else leaked="否"; fi
        hint=""
        grep -q '查看命令' <<<"$out" && hint="（给了查看命令）"
        printf '  片段语法 OK | TTY=%s --print-seed-password=%s -> 输出含明文口令: %s %s\n' \
            "$fake_tty" "$flag" "$leaked" "$hint"
    done
done

echo
echo "### 真实脚本里两处打印的守卫情况（源码级）"
printf '  守卫语句 "if [[ -t 1 ]]; then" 出现次数: %s（期望 2）\n' \
    "$(grep -c -F "$GUARD" "$SRC")"
printf '  "echo \"  🔑 管理员密码：${_SEED_PW}\"" 出现次数: %s（必须落在守卫块内）\n' \
    "$(grep -c -F 'echo "  🔑 管理员密码：${_SEED_PW}"' "$SRC")"
printf '  "ok \"管理员密码已生成：admin / ${ADMIN_PW}" 出现次数: %s（必须落在守卫块内）\n' \
    "$(grep -c -F 'ok "管理员密码已生成：admin / ${ADMIN_PW}' "$SRC")"
printf '  "[DIAG] ... JWT_SECRET/DJANGO_SECRET_KEY/... 已就绪" 枚举行: %s（期望 0）\n' \
    "$(grep -c -F 'JWT_SECRET/DJANGO_SECRET_KEY/LOGVIEWER_SECRET_KEY/SEED_ADMIN_PASSWORD 已就绪' "$SRC")"
