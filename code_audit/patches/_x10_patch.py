"""X-10：部署/升级脚本加"migrate 后失败"的回滚路径与失败陷阱（行尾自适应）。

deploy-linux.sh：备份改用一致性快照（不再 cp 活库）、注册 EXIT trap、migrate 前后置标志。
update-from-github.sh：同上（它已有 CODE_HEAD_BEFORE）。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "scripts" / "deploy-linux.sh"
UPDATE = REPO / "scripts" / "update-from-github.sh"

DEPLOY_PATCHES: list[tuple[str, str]] = []

# 1) 标志 + EXIT trap + 回滚指引函数（插在 problem() 之后）
DEPLOY_PATCHES.append((
    """problem() { DEPLOY_PROBLEMS=$((DEPLOY_PROBLEMS + 1)); warn "$@"; }
""",
    """problem() { DEPLOY_PROBLEMS=$((DEPLOY_PROBLEMS + 1)); warn "$@"; }

# 审计 X-10：改前脚本在 `migrate` 之后还有 collectstatic / 前端构建 / 重启服务 / nginx
# 等步骤，任何一步失败都会留下"新库结构 + 旧代码 + 服务停摆"的状态，而且**没有任何失败陷阱
# 或回滚路径**；备份也只是 `cp -a` 活库（WAL 下可能拿到不一致的副本）且失败被 `|| true` 吞掉。
# 现在：migrate 前先做一致性快照（失败即中止，因为它是唯一回滚副本），并在 EXIT trap 里
# 把"怎么退回去"的确切命令打出来。
MIGRATE_STARTED=0
MIGRATE_OK=0
PRE_MIGRATE_DB_SNAPSHOT=""
CODE_HEAD_BEFORE=""

_on_exit() {
    local rc=$?
    if [[ "$rc" != "0" && "$MIGRATE_STARTED" == "1" && "$MIGRATE_OK" != "1" ]]; then
        echo
        warn "脚本以退出码 $rc 结束，且已执行过 migrate（或正在执行）—— 数据库结构可能已改变。"
        print_rollback_hint "$INSTALL_DIR" "$PRE_MIGRATE_DB_SNAPSHOT" "$BACKUP_DIR" "$CODE_HEAD_BEFORE"
    fi
    exit "$rc"
}
trap _on_exit EXIT
""",
))

# 2) 备份改用一致性快照
DEPLOY_PATCHES.append((
    """    log "发现现有部署 → 备份到 $BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    cp -a "$INSTALL_DIR/backend/db.sqlite3" "$BACKUP_DIR/" 2>/dev/null || true
    cp -a "$INSTALL_DIR/backend/uploads"    "$BACKUP_DIR/" 2>/dev/null || true
    cp -a "$INSTALL_DIR/backend/.env"       "$BACKUP_DIR/" 2>/dev/null || true""",
    """    log "发现现有部署 → 备份到 $BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    # 审计 X-10：数据库是整个部署里**唯一无法从代码重建**的东西，它的副本必须自洽。
    # 改前是 `cp -a <活库> … || true`：WAL 模式下可能抓到不一致的快照，而且失败被静默吞掉。
    CODE_HEAD_BEFORE="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo '')"
    if [[ -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
        if snapshot_sqlite_consistent "$INSTALL_DIR/backend/db.sqlite3" "$BACKUP_DIR/db.sqlite3"; then
            PRE_MIGRATE_DB_SNAPSHOT="$BACKUP_DIR/db.sqlite3"
            ok "数据库一致性快照已就绪：$PRE_MIGRATE_DB_SNAPSHOT"
        else
            err "无法为现有数据库生成一致性快照（$BACKUP_DIR/db.sqlite3）—— 没有它就无法回滚，已中止。请先手动备份后重跑。"
        fi
    fi
    cp -a "$INSTALL_DIR/backend/uploads"    "$BACKUP_DIR/" 2>/dev/null || warn "uploads 备份失败（可稍后手动复制）"
    cp -a "$INSTALL_DIR/backend/.env"       "$BACKUP_DIR/" 2>/dev/null || warn ".env 备份失败（可稍后手动复制）\"""",
))

# 3) migrate 前后置标志
DEPLOY_PATCHES.append((
    """# ---------------- 4. 数据库迁移 + seed 默认 admin ----------------
log "执行 migrate（首次会自动建 admin，密码自动生成或取自 .env SEED_ADMIN_PASSWORD）"
".venv/bin/python" manage.py check --fail-level ERROR
".venv/bin/python" manage.py migrate --noinput""",
    """# ---------------- 4. 数据库迁移 + seed 默认 admin ----------------
log "执行 migrate（首次会自动建 admin，密码自动生成或取自 .env SEED_ADMIN_PASSWORD）"
".venv/bin/python" manage.py check --fail-level ERROR
# 审计 X-10：从这里开始数据库结构可能改变；若后续任一步失败，EXIT trap 会打印回滚指引。
MIGRATE_STARTED=1
".venv/bin/python" manage.py migrate --noinput""",
))

DEPLOY_PATCHES.append((
    """cd "$INSTALL_DIR/backend"
ok "数据库迁移完成，静态资源收集完成\"""",
    """cd "$INSTALL_DIR/backend"
MIGRATE_OK=1
ok "数据库迁移完成，静态资源收集完成\"""",
))

UPDATE_PATCHES: list[tuple[str, str]] = []

UPDATE_PATCHES.append((
    """BACKUP_DIR="$INSTALL_DIR/_backup/$(date +%F_%H%M%S)"
mkdir -p "$BACKUP_DIR\"""",
    """# 审计 X-10：migrate 之后还有 collectstatic / 前端构建 / 重启服务等步骤，任何一步失败都会
# 留下"新库结构 + 旧代码 + 服务停摆"；这里注册失败陷阱，把回滚命令说清楚。
MIGRATE_STARTED=0
MIGRATE_OK=0
PRE_MIGRATE_DB_SNAPSHOT=""
_on_exit() {
    local rc=$?
    if [[ "$rc" != "0" && "$MIGRATE_STARTED" == "1" && "$MIGRATE_OK" != "1" ]]; then
        echo
        warn "脚本以退出码 $rc 结束，且已执行过 migrate（或正在执行）—— 数据库结构可能已改变。"
        print_rollback_hint "$INSTALL_DIR" "$PRE_MIGRATE_DB_SNAPSHOT" "$BACKUP_DIR" "$CODE_HEAD_BEFORE"
    fi
    exit "$rc"
}
trap _on_exit EXIT

BACKUP_DIR="$INSTALL_DIR/_backup/$(date +%F_%H%M%S)"
mkdir -p "$BACKUP_DIR\"""",
))

UPDATE_PATCHES.append((
    """    cp -a "$INSTALL_DIR/backend/db.sqlite3" "$BACKUP_DIR/" && log "已备份数据库 → $BACKUP_DIR/db.sqlite3\"""",
    """    # 审计 X-10：改前是 `cp -a` 活库 —— WAL 下可能得到不一致的副本，而它是唯一回滚副本。
    if snapshot_sqlite_consistent "$INSTALL_DIR/backend/db.sqlite3" "$BACKUP_DIR/db.sqlite3"; then
        PRE_MIGRATE_DB_SNAPSHOT="$BACKUP_DIR/db.sqlite3"
        log "已做数据库一致性快照 → $PRE_MIGRATE_DB_SNAPSHOT"
    else
        err "无法为现有数据库生成一致性快照（$BACKUP_DIR/db.sqlite3）—— 没有它就无法回滚，已中止。"
    fi""",
))

UPDATE_PATCHES.append((
    """".venv/bin/python" manage.py migrate --noinput""",
    """# 审计 X-10：从这里开始数据库结构可能改变；若后续任一步失败，EXIT trap 会打印回滚指引。
MIGRATE_STARTED=1
".venv/bin/python" manage.py migrate --noinput""",
))


def _patch(path: Path, patches: list[tuple[str, str]]) -> int:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    changed = 0
    for old, new in patches:
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        if o not in text:
            if n in text:
                print(f"[skip] {path.name}: 某处已打过补丁")
                continue
            print(f"[fail] {path.name}: 未找到锚点 {old.splitlines()[0][:70]!r}")
            return 1
        text = text.replace(o, n, 1)
        changed += 1
    path.write_bytes(text.encode("utf-8"))
    print(f"[ok]   {path.name}: 替换 {changed}/{len(patches)} 处")
    return 0


def main() -> int:
    if _patch(DEPLOY, DEPLOY_PATCHES):
        return 1
    return _patch(UPDATE, UPDATE_PATCHES)


if __name__ == "__main__":
    raise SystemExit(main())
