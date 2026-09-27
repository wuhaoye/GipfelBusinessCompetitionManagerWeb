#!/usr/bin/env bash
# 行尾诊断：工作区 vs git 对象库（不属于交付物）
REPO=/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb
cd "$REPO" || exit 3

echo "== 工作区 .sh 的行尾 =="
for f in $(git ls-files '*.sh'); do
    crlf=$(grep -c $'\r$' "$f" 2>/dev/null || echo 0)
    printf '  %-34s CRLF行=%s\n' "$f" "$crlf"
done

echo "== git 对象库里 .sh 的行尾（HEAD） =="
for f in $(git ls-files '*.sh'); do
    n=$(git show "HEAD:$f" | grep -c $'\r$' || true)
    printf '  %-34s CRLF行=%s\n' "$f" "$n"
done

echo "== bash -n 直接对工作区文件 =="
for f in $(git ls-files '*.sh'); do
    if bash -n "$f" 2>/dev/null; then echo "  OK   $f"; else echo "  FAIL $f"; fi
done

echo "== bash -n 对 LF 化副本 =="
TMP=$(mktemp -d)
for f in $(git ls-files '*.sh'); do
    mkdir -p "$TMP/$(dirname "$f")"
    tr -d '\r' < "$f" > "$TMP/$f"
    if bash -n "$TMP/$f" 2>/dev/null; then echo "  OK   $f"; else echo "  FAIL $f"; fi
done
rm -rf "$TMP"

echo "== bash 版本 =="
bash --version | head -1
