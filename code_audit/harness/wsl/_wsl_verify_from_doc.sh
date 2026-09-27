#!/usr/bin/env bash
# 生产相关 bash 内容的 WSL(Ubuntu) 复验脚本
# 用法：wsl -d Ubuntu-26.04 -- bash code_audit/_wsl_verify.sh
# 退出码：0 = 全部通过；非 0 = 有失败项
set -uo pipefail

REPO="${REPO:-/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb}"
WORK="$(mktemp -d /tmp/gipfel-wslverify-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m %s\n' "$*"; }
bad()  { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m %s\n' "$*"; }
chk()  { local d="$1" got="$2" exp="$3"; [[ "$got" == "$exp" ]] && ok "$d（=$got）" || bad "$d（got=$got exp=$exp）"; }
sec()  { printf '\n\033[36m== %s ==\033[0m\n' "$*"; }

cd "$REPO" || { echo "找不到仓库 $REPO"; exit 2; }

sec "0. 环境自述"
printf '  %s\n' "$(bash --version | head -1)"
printf '  %s / init=%s / systemd=%s\n' "$(python3 -V 2>&1)" "$(ps -p 1 -o comm=)" "$(systemctl is-system-running 2>&1)"
printf '  python3 sqlite3=%s\n' "$(python3 -c 'import sqlite3;print(sqlite3.sqlite_version)' 2>&1)"

sec "1. 全部 shell 脚本语法（真 Linux bash）"
for f in $(git ls-files '*.sh'); do
    bash -n "$f" 2>/dev/null && ok "bash -n $f" || bad "bash -n $f"
done

sec "2. tests/deploy_public_ip_test.sh（三种 cwd）"
for d in "$REPO/tests" "$REPO" /tmp; do
    out="$(cd "$d" && bash "$REPO/tests/deploy_public_ip_test.sh" 2>&1)"
    line="$(printf '%s' "$out" | grep -E '^结果：' | tail -1)"
    if printf '%s' "$line" | grep -q 'FAIL=0'; then ok "cwd=$d → $line"; else bad "cwd=$d → $line"; fi
done

sec "3. scripts/lib/deploy-common.sh 纯函数"
# shellcheck source=scripts/lib/deploy-common.sh
source scripts/lib/deploy-common.sh
chk "absolutize_dir ./a/b/../c" "$(cd /tmp && absolutize_dir ./a/b/../c)" "/tmp/a/c"
chk "absolutize_dir /opt/gipfel//x/.." "$(absolutize_dir /opt/gipfel//x/..)" "/opt/gipfel"
printf 'LOG_VIEWER_PORT=9000\n' > "$WORK/env.9000"
printf 'LOG_VIEWER_PORT=abc\n'  > "$WORK/env.bad"
chk "log_viewer_port 读取 9000" "$(log_viewer_port "$WORK/env.9000")" "9000"
chk "log_viewer_port 非法回退"  "$(log_viewer_port "$WORK/env.bad")" "8120"

sec "4. snapshot_sqlite_consistent（真实 python3 + WAL 活库）"
python3 - "$WORK/live.sqlite3" <<'PY' &
import sqlite3, sys, time
con = sqlite3.connect(sys.argv[1], timeout=30)
con.execute("pragma journal_mode=wal")
con.execute("create table t(id integer primary key, v text)")
for b in range(15):
    con.executemany("insert into t(v) values(?)", [(f"r{b}-{i}" * 20,) for i in range(500)])
    con.commit(); time.sleep(0.3)
con.close()
PY
WRITER=$!
sleep 1.5
cp -a "$WORK/live.sqlite3" "$WORK/plain.sqlite3"
if snapshot_sqlite_consistent "$WORK/live.sqlite3" "$WORK/snap.sqlite3"; then ok "snapshot_sqlite_consistent 成功"; else bad "snapshot_sqlite_consistent 失败"; fi
kill "$WRITER" 2>/dev/null; wait "$WRITER" 2>/dev/null
python3 - "$WORK" <<'PY'
import sqlite3, sys, pathlib
work = pathlib.Path(sys.argv[1])
for name in ("plain.sqlite3", "snap.sqlite3"):
    p = work / name
    if not p.exists():
        print(f"  INFO {name}: 不存在"); continue
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            print(f"  INFO {name}: integrity={con.execute('pragma integrity_check').fetchone()[0]} rows={con.execute('select count(*) from t').fetchone()[0]}")
        finally:
            con.close()
    except sqlite3.Error as exc:
        print(f"  INFO {name}: 打不开/损坏 -> {exc}")
PY

sec "5. scripts/migrate-server.sh 退出码与 --dry-run"
run_rc() { ( cd "$WORK" && bash "$REPO/scripts/migrate-server.sh" "$@" >/dev/null 2>&1 ); echo $?; }
chk "--help"                    "$(run_rc --help)" "0"
chk "未知参数"                   "$(run_rc --install_dir /opt/gipfel)" "2"
chk "缺 --mode"                 "$(run_rc --target u@127.0.0.1)" "1"
chk "--ssh-port abc"            "$(run_rc --mode push --target u@127.0.0.1 --install-dir /tmp --ssh-port abc)" "2"
before="$(ls -d /tmp/gipfel-migration-* 2>/dev/null | wc -l)"
out="$( cd "$WORK" && bash "$REPO/scripts/migrate-server.sh" --dry-run --mode pull --source u@127.0.0.1 --install-dir /tmp 2>&1 )"; rc=$?
after="$(ls -d /tmp/gipfel-migration-* 2>/dev/null | wc -l)"
chk "--dry-run --mode pull 退出码" "$rc" "0"
printf '%s' "$out" | grep -q 'DRY-RUN' && ok "--dry-run 打印了预演计划" || bad "--dry-run 没有预演输出"
printf '%s' "$out" | grep -q '无法连接到源服务器' && bad "--dry-run 仍发起了真实连接" || ok "--dry-run 未发起真实连接"
chk "--dry-run 无临时目录残留" "$before" "$after"

sec "6. scripts/quick-sync.sh（相对 INSTALL_DIR / push 源校验 / SSH 选项）"
mkdir -p "$WORK/empty-install/backend"
q() { ( cd "$WORK" && bash "$REPO/scripts/quick-sync.sh" "$@" 2>&1 ); echo "RC=$?"; }
o1="$(q push user@nonexistent ./no-such-dir)"
printf '%s' "$o1" | grep -q '推送源不可用' && printf '%s' "$o1" | grep -q 'RC=1' && ok "不存在的推送源被拒绝" || bad "不存在的推送源未被拒绝：$(printf '%s' "$o1" | tail -2 | tr '\n' ' ')"
o2="$(q push user@nonexistent empty-install --ssh-port 2222 --ssh-key /tmp/k --ssh-known-hosts /tmp/kh)"
printf '%s' "$o2" | grep -q '已用 -i 指定私钥' && ok "SSH 选项被识别" || bad "SSH 选项未被识别"
printf '%s' "$o2" | grep -q '未指定 --ssh-known-hosts' && bad "给了 known_hosts 仍告警 accept-new" || ok "给了 known_hosts 不再告警 accept-new"
chk "非法 --ssh-port" "$(printf '%s' "$(q push user@nonexistent empty-install --ssh-port abc)" | tail -1)" "RC=2"

sec "7. scripts/deploy-linux.sh（帮助 / 口令默认不打印 / 退出码语义）"
out="$(bash scripts/deploy-linux.sh --help 2>&1)"
printf '%s' "$out" | grep -q -- '--print-seed-password' && ok "帮助里有 --print-seed-password" || bad "帮助缺 --print-seed-password"
printf '%s' "$out" | grep -q -- '--allow-partial' && ok "帮助里有 --allow-partial" || bad "帮助缺 --allow-partial"
n_guard="$(grep -c 'PRINT_SEED_PASSWORD" == "1" && -t 1' scripts/deploy-linux.sh)"
chk "TTY+显式开关守卫处数" "$n_guard" "2"
chk "收尾无条件 ok 行数" "$(grep -c '^ok "部署完成！"$' scripts/deploy-linux.sh)" "0"

sec "8. scripts/update-from-github.sh 与 verify-migration.sh"
out="$(bash scripts/update-from-github.sh --help 2>&1)"; rc=$?
[[ $rc -eq 0 || $rc -eq 1 || $rc -eq 2 ]] && ok "update-from-github --help 以 $rc 结束（非崩溃）" || bad "update-from-github --help rc=$rc"
out="$(bash scripts/verify-migration.sh --help 2>&1)"; rc=$?
[[ $rc -eq 0 || $rc -eq 1 || $rc -eq 2 ]] && ok "verify-migration --help 以 $rc 结束（非崩溃）" || bad "verify-migration --help rc=$rc"
grep -q 'PASS=$((PASS + 1))' scripts/verify-migration.sh && ok "verify-migration 计数不再用 ((PASS++))" || bad "verify-migration 计数器仍是 ((PASS++))"

sec "9. systemd 单元（真 systemd-analyze verify）"
mkdir -p "$WORK/units"
for u in deploy/gipfel.service deploy/logviewer.service; do
    b="$(basename "$u")"
    sed -e 's|__INSTALL_DIR__|/opt/gipfel|g' "$u" > "$WORK/units/$b"
done
if command -v systemd-analyze >/dev/null 2>&1; then
    # 只关心**指令级**错误；"Command … is not executable / could not be found" 是因为
    # /opt/gipfel 下没有真实安装，属于预期噪音，过滤掉。
    verr="$(systemd-analyze verify "$WORK/units/gipfel.service" "$WORK/units/logviewer.service" 2>&1 \
            | grep -viE 'is not executable|could not be found|not found|does not exist|Failed to (prepare|load)|Cannot (find|load)' || true)"
    if [[ -z "$verr" ]]; then ok "systemd-analyze verify 无指令级错误"; else bad "systemd-analyze verify：$verr"; fi
else
    bad "缺 systemd-analyze"
fi
grep -q -- '-u /run/gipfel/gipfel.sock' deploy/gipfel.service && bad "gipfel.service 仍在监听 Unix socket" || ok "gipfel.service 已移除 Unix socket"
grep -q 'RuntimeDirectoryMode=0750' deploy/gipfel.service && ok "gipfel.service 运行目录 0750" || bad "gipfel.service 运行目录未收紧"
grep -q 'UMask=0027' deploy/logviewer.service && ok "logviewer.service UMask=0027" || bad "logviewer.service 缺 UMask"

sec "10. print_rollback_hint 输出"
out="$(print_rollback_hint /opt/gipfel "$WORK/snap.sqlite3" "$WORK" abc1234 2>&1)"
for n in 'systemctl stop gipfel gipfel-logviewer' "checkout 'abc1234'" 'npm ci' 'api/health' '只含数据'; do
    printf '%s' "$out" | grep -qF -- "$n" && ok "回滚指引含：$n" || bad "回滚指引缺：$n"
done

printf '\n\033[36m════ 汇总 ════\033[0m\n'
printf '  PASS=%d FAIL=%d\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
