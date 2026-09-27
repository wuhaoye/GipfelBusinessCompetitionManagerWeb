#!/usr/bin/env bash
# X-10 运行时探针：一致性快照 vs 活库 cp（写入进程保持运行），以及回滚指引的实际输出。
# 不属于交付物。
set -uo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"   # code_audit/harness/ -> 仓库根
LIB="$REPO/scripts/lib/deploy-common.sh"
WORK="$REPO/.tmp/x10"
rm -rf "$WORK"; mkdir -p "$WORK"
PY="$REPO/backend/.venv/Scripts/python.exe"
export PYTHONIOENCODING=utf-8
export PYTHON_FOR_SNAPSHOT="$PY"     # 本机没有可用的 python3（PATH 里是 Store 存根），显式指定

echo "### X-10 探针"
echo "[A] 活库（写入进程保持运行、WAL 未 checkpoint）上的副本对比"

"$PY" - "$WORK/live.sqlite3" <<'PY' &
import sqlite3, sys, time
con = sqlite3.connect(sys.argv[1], timeout=30)
con.execute("pragma journal_mode=wal")
con.execute("create table t(id integer primary key, v text)")
for batch in range(20):
    con.executemany("insert into t(v) values(?)", [(f"row-{batch}-{i}" * 20,) for i in range(500)])
    con.commit()
    time.sleep(0.35)
con.close()
PY
WRITER=$!
sleep 2   # 让它写完前几批、且保持连接不关闭（WAL 里有未 checkpoint 的数据）

ls -l "$WORK"/live.sqlite3* 2>/dev/null | awk '{print "     " $5, $9}'

cp -a "$WORK/live.sqlite3" "$WORK/copy-plain.sqlite3"
# shellcheck source=scripts/lib/deploy-common.sh
source "$LIB"
if snapshot_sqlite_consistent "$WORK/live.sqlite3" "$WORK/copy-snapshot.sqlite3"; then
    echo "  snapshot_sqlite_consistent: 成功"
else
    echo "  snapshot_sqlite_consistent: 失败"
fi

"$PY" - "$WORK" <<'PY'
import sqlite3, sys, pathlib
work = pathlib.Path(sys.argv[1])
for name in ("copy-plain.sqlite3", "copy-snapshot.sqlite3"):
    p = work / name
    if not p.exists():
        print(f"  {name}: 不存在"); continue
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            ok = con.execute("pragma integrity_check").fetchone()[0]
            n = con.execute("select count(*) from t").fetchone()[0]
        finally:
            con.close()
        print(f"  {name}: integrity_check={ok} 可见行数={n}")
    except sqlite3.Error as exc:
        print(f"  {name}: 打不开/损坏 -> {exc}")
print("  （写入进程仍在跑；活库此刻的真实行数见下方 live 计数）")
try:
    con = sqlite3.connect(f"file:{work / 'live.sqlite3'}?mode=ro", uri=True)
    try:
        print("  live.sqlite3: 可见行数=", con.execute("select count(*) from t").fetchone()[0])
    finally:
        con.close()
except sqlite3.Error as exc:
    print("  live.sqlite3 读取失败:", exc)
PY

kill "$WRITER" 2>/dev/null || true
wait "$WRITER" 2>/dev/null || true

echo
echo "[B] print_rollback_hint 的实际输出"
D1="$(grep -n -F 'print_rollback_hint() {' "$LIB" | head -1 | cut -d: -f1)"
D2="$(awk -v s="$D1" 'NR>s && /^}/ {print NR; exit}' "$LIB")"
sed -n "${D1},${D2}p" "$LIB" > "$WORK/hint.sh"
{
    echo 'warn() { echo "[WARN] $*"; }'
    cat "$WORK/hint.sh"
    echo 'print_rollback_hint /opt/gipfel /opt/gipfel/_backup/2026-01-01_0000/db.sqlite3 /opt/gipfel/_backup/2026-01-01_0000 abc1234def'
} > "$WORK/run_hint.sh"
bash "$WORK/run_hint.sh"
