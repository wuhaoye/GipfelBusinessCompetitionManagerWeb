#!/usr/bin/env bash
# 终检：所有被跟踪的 shell 脚本语法（不属于交付物）
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"   # code_audit/harness/ -> 仓库根
cd "$REPO" || exit 3
n=0
bad=""
while IFS= read -r f; do
    n=$((n + 1))
    if ! bash -n "$f" 2>/dev/null; then bad="$bad $f"; fi
done < <(git ls-files '*.sh')
echo "  共 $n 个 shell 脚本；语法失败：${bad:-无}"
[[ -z "$bad" ]] || exit 1
