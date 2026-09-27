#!/usr/bin/env bash
# X-10 自检：语法 + 关键片段（不属于交付物）
REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"   # code_audit/harness/ -> 仓库根
cd "$REPO" || exit 3
for f in scripts/deploy-linux.sh scripts/update-from-github.sh scripts/lib/deploy-common.sh scripts/quick-sync.sh scripts/migrate-server.sh; do
    if bash -n "$f"; then echo "  OK   $f"; else echo "  FAIL $f"; fi
done
echo
echo "-- 是否 source 公共库 --"
for f in scripts/deploy-linux.sh scripts/update-from-github.sh scripts/quick-sync.sh; do
    printf '  %-32s deploy-common=%s\n' "$f" "$(grep -c 'deploy-common.sh' "$f")"
done
echo
echo "-- 关键片段计数（deploy-linux / update-from-github） --"
for f in scripts/deploy-linux.sh scripts/update-from-github.sh; do
    printf '  %-32s trap=%s MIGRATE_STARTED=%s MIGRATE_OK=%s snapshot=%s rollback_hint=%s\n' \
        "$f" "$(grep -c 'trap _on_exit EXIT' "$f")" "$(grep -c '^MIGRATE_STARTED=1' "$f")" \
        "$(grep -c '^MIGRATE_OK=1' "$f")" "$(grep -c 'snapshot_sqlite_consistent' "$f")" \
        "$(grep -c 'print_rollback_hint' "$f")"
done
