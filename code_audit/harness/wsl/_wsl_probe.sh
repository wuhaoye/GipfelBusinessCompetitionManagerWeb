#!/usr/bin/env bash
# WSL 能力探针（不属于交付物）
echo "== os =="
. /etc/os-release 2>/dev/null
echo "${PRETTY_NAME:-unknown}"
echo "== kernel / init =="
uname -r
echo "PID1=$(ps -p 1 -o comm= 2>/dev/null || echo '?')"
echo "== tools =="
for c in bash python3 sqlite3 rsync ssh curl nginx systemctl git node npm sudo ufw ss pip3; do
    printf '%-12s %s\n' "$c" "$(command -v "$c" 2>/dev/null || echo MISSING)"
done
echo "== python =="
python3 -V 2>&1
python3 -c 'import sqlite3; print("sqlite3 module", sqlite3.sqlite_version)' 2>&1
echo "== venv 可用? =="
python3 -c 'import venv, ensurepip; print("venv+ensurepip OK")' 2>&1 | tail -1
echo "== systemd =="
systemctl is-system-running 2>&1 | head -1
echo "== 仓库路径 =="
REPO=/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb
ls -d "$REPO" 2>&1 | head -2
echo "== 仓库内 shell 脚本 =="
ls "$REPO"/scripts/*.sh "$REPO"/tests/*.sh 2>/dev/null | sed 's|.*/||'
