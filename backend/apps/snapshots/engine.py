"""快照与回退引擎。

对外主入口：
- `create_snapshot(...)`  —— 逐表流式落盘 + manifest（全量记录）
- `restore_snapshot(...)` —— 事务内整表还原 + 回退后一致性校验 + dataVersion 递增
- `verify_snapshot(...)`  —— 离线校验归档完整性（sha256 比对）
- `diff_snapshot(...)`    —— 回退前预览：每张表将删除 / 写回多少行
- `delete_snapshot(...)` / `cleanup_snapshots(...)` —— 生命周期与保留策略

一致性保证
----------
1. 调用方（views）先通过 `gate` 把系统切到 PAUSED / RESTORING 并排空在途写请求，
   因此快照读到的是静止点、回退期间没有任何并发写入。
2. 快照读取在一个事务内完成（`transaction.atomic`），保证跨表一致。
3. 回退整体在一个事务内完成：删 → 写回 → 校验，任何异常都会整体回滚，不存在半截状态。
4. 回退完成后按与写入完全相同的算法重算每张表的 sha256，与 manifest 比对；
   `full` 表要求逐位一致，`upsert` 表只要求快照内的行存在且内容一致（允许库中多出
   快照之后新建的行，因为这类表按设计不删除）。
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Callable, Iterable

from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone

from apps.common.fields import suspend_exact_decimal_guard
from apps.common.signals import suppress_signals

from . import archive
from .gate import bump_data_version
from .gate import data_version as current_data_version
from .models import Snapshot, SnapshotPolicy, SnapshotTable
from .registry import TableSpec, build_registry, scope_queryset

logger = logging.getLogger("gipfel")

ProgressFn = Callable[[str], None]

#: 永不参与快照/回退的记账表（快照系统自身）
_SELF_TABLES = {"Snapshot", "SnapshotTable", "SystemGate", "SnapshotPolicy"}


class SnapshotError(RuntimeError):
    """快照/回退过程中的可预期错误（对用户可见）。"""


# ====================================================================
# 通用
# ====================================================================
def _noop(_msg: str) -> None:
    return None


def app_version() -> str:
    try:
        from apps.auth.views import _read_version

        return str(_read_version())
    except Exception:  # noqa: BLE001
        return ""


def _server_seq() -> int:
    try:
        from apps.realtime.emit import server_seq

        return int(server_seq())
    except Exception:  # noqa: BLE001
        return 0


def _competition_name(competition_id: int | None) -> str:
    if competition_id is None:
        return ""
    try:
        from apps.competitions.models import Competition

        row = (
            Competition.objects.filter(pk=competition_id)
            .values_list("name", flat=True)
            .first()
        )
        return row or ""
    except Exception:  # noqa: BLE001
        return ""


def resolve_restore_policy(
    spec: TableSpec | None,
    *,
    competition_id: int | None,
    include_users: bool = False,
    restore_global: bool = False,
) -> str:
    """决定某张表在本次回退中的回写策略。

    - 快照系统自身的记账表：不参与
    - 审计日志：永远只记录不回写（避免抹掉取证痕迹）
    - 账号：默认只记录；`include_users=true` 时改为 upsert（绝不删除账号行，
      也绝不回滚 token_version —— 否则已吊销的旧令牌会「复活」）
    - 比赛：upsert（删除比赛会级联删掉账号等历史数据，风险远大于收益）
    - 比赛维度快照下的全局表：默认只记录（回写会误伤其它比赛）
    """
    if spec is None:
        return "record"
    if spec.name in _SELF_TABLES:
        return "record"
    if spec.name == "AuditLog":
        return "record"
    if spec.name == "Competition":
        return "upsert"
    if spec.name == "User":
        return "upsert" if include_users else "record"
    if competition_id is not None and not spec.in_competition_scope and not restore_global:
        return "record"
    return "full"


# ====================================================================
# 创建快照
# ====================================================================
def create_snapshot(
    *,
    label: str,
    note: str = "",
    kind: str = "manual",
    competition_id: int | None = None,
    include_files: bool = False,
    operator: Any = None,
    progress: ProgressFn | None = None,
) -> Snapshot:
    """创建一份快照。

    调用方负责先把系统暂停 / 排空在途写请求（`gate.exclusive_window`），
    否则得到的是「读取过程中仍可能有写入」的弱一致快照。
    """
    report = progress or _noop
    registry = build_registry()
    started = time.monotonic()

    scope = "system" if competition_id is None else "competition"
    competition_name = _competition_name(competition_id)
    if competition_id is not None and not competition_name:
        raise SnapshotError(f"比赛 #{competition_id} 不存在，无法创建快照")

    snapshot = Snapshot.objects.create(
        label=(label or "").strip()[:128] or f"快照 {timezone.localtime():%Y-%m-%d %H:%M:%S}",
        note=note or "",
        kind=kind if kind in dict(Snapshot.KIND_CHOICES) else "manual",
        status="building",
        scope=scope,
        competition_id=competition_id,
        competition_name=competition_name,
        include_files=bool(include_files),
        data_version=current_data_version(),
        server_seq=_server_seq(),
        app_version=app_version(),
        created_by_id=getattr(operator, "id", None),
        created_by_name=getattr(operator, "username", "") or "",
    )
    directory = archive.snapshot_dir(snapshot.id)
    snapshot.storage_path = str(directory)
    Snapshot.objects.filter(pk=snapshot.id).update(storage_path=str(directory))

    table_rows: list[SnapshotTable] = []
    total_rows = 0
    total_bytes = 0
    problems: list[str] = []
    try:
        report(f"正在读取数据（{len(registry.specs)} 张表）…")
        with transaction.atomic():
            for spec in registry.specs:
                qs = scope_queryset(spec, competition_id)
                path = archive.table_file(snapshot.id, spec.table)
                rows, size, digest = archive.write_table(spec, qs, path)
                total_rows += rows
                total_bytes += size
                table_rows.append(
                    SnapshotTable(
                        snapshot=snapshot,
                        model_label=spec.label,
                        model_name=spec.name,
                        table_name=spec.table,
                        resource=spec.resource or "",
                        policy=resolve_restore_policy(
                            spec, competition_id=competition_id
                        ),
                        row_count=rows,
                        byte_size=size,
                        sha256=digest,
                        file_name=f"{archive.TABLES_DIR}/{spec.table}.jsonl.gz",
                    )
                )

        if include_files:
            report("正在归档上传文件…")
            file_count, file_bytes, file_problems = archive.archive_files(
                archive.files_dir(snapshot.id)
            )
            problems.extend(file_problems)
        else:
            file_count, file_bytes = 0, 0

        manifest = {
            "schemaVersion": archive.SCHEMA_VERSION,
            "generator": archive.GENERATOR,
            "snapshotId": snapshot.id,
            "label": snapshot.label,
            "note": snapshot.note,
            "kind": snapshot.kind,
            "scope": scope,
            "competitionId": competition_id,
            "competitionName": competition_name,
            "createdAt": timezone.localtime().isoformat(),
            "createdBy": snapshot.created_by_name,
            "appVersion": snapshot.app_version,
            "dataVersion": snapshot.data_version,
            "serverSeq": snapshot.server_seq,
            "includeFiles": bool(include_files),
            "fileCount": file_count,
            "fileBytes": file_bytes,
            "problems": problems,
            "totals": {
                "tables": len(table_rows),
                "rows": total_rows,
                "bytes": total_bytes,
            },
            "tables": [row.to_dict() for row in table_rows],
        }
        manifest_sha = archive.write_manifest(directory, manifest)
        disk_bytes = archive.dir_size(directory)

        with transaction.atomic():
            SnapshotTable.objects.bulk_create(table_rows, batch_size=200)
            Snapshot.objects.filter(pk=snapshot.id).update(
                status="ready",
                table_count=len(table_rows),
                row_count=total_rows,
                byte_size=disk_bytes,
                file_count=file_count,
                file_byte_size=file_bytes,
                manifest_sha256=manifest_sha,
                duration_ms=int((time.monotonic() - started) * 1000),
                error="\n".join(problems)[:4000],
                storage_path=str(directory),
            )
        snapshot.refresh_from_db()
        report(
            f"快照 #{snapshot.id} 创建完成：{total_rows} 行 / {len(table_rows)} 张表"
        )
        return snapshot
    except Exception as exc:  # noqa: BLE001
        logger.error("创建快照失败：%s", exc, exc_info=True)
        Snapshot.objects.filter(pk=snapshot.id).update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:4000],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        snapshot.refresh_from_db()
        raise


# ====================================================================
# 校验归档
# ====================================================================
def verify_snapshot(snapshot: Snapshot, *, recompute_current: bool = False) -> dict:
    """校验归档文件与 manifest 是否自洽（可选附带「与当前库的差异」）。"""
    directory = archive.snapshot_dir(snapshot.id)
    problems: list[str] = []
    try:
        manifest = archive.read_manifest(directory)
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "problems": [str(exc)], "tables": [], "totalRows": 0}

    registry = build_registry()
    rows_out: list[dict] = []
    for meta in manifest.get("tables", []):
        label = meta.get("model") or ""
        spec = registry.by_label(label)
        entry: dict[str, Any] = {
            "model": label,
            "table": meta.get("table"),
            "expectedRows": meta.get("rows"),
            "rows": None,
            "sha256Ok": None,
            "missing": False,
        }
        if spec is None:
            entry["missing"] = True
            problems.append(f"{label}：当前代码中已不存在该模型，回退时将跳过")
            rows_out.append(entry)
            continue
        path = archive.table_file(snapshot.id, spec.table)
        if not path.exists():
            entry["missing"] = True
            problems.append(f"{spec.label}：归档文件缺失 {path.name}")
            rows_out.append(entry)
            continue
        try:
            rows, digest = archive.table_checksum(path)
        except Exception as exc:  # noqa: BLE001 - 文件损坏（非 gzip / 被截断）
            entry["missing"] = True
            entry["error"] = f"{type(exc).__name__}: {exc}"
            problems.append(f"{spec.label}：归档文件无法读取（{exc}）")
            rows_out.append(entry)
            continue
        entry["rows"] = rows
        entry["sha256Ok"] = digest == (meta.get("sha256") or "")
        if rows != meta.get("rows"):
            problems.append(
                f"{spec.label}：行数不一致（归档 {meta.get('rows')}，实际 {rows}），文件可能被截断"
            )
        if not entry["sha256Ok"]:
            problems.append(f"{spec.label}：内容校验和不一致，归档文件已损坏或被改动")
        if recompute_current and entry["sha256Ok"]:
            current_rows, current_digest = archive.db_table_checksum(
                spec, scope_queryset(spec, snapshot.competition_id)
            )
            entry["currentRows"] = current_rows
            entry["changed"] = current_digest != digest or current_rows != rows
        rows_out.append(entry)

    return {
        "ok": not problems,
        "problems": problems,
        "tables": rows_out,
        "totalRows": sum(m.get("rows") or 0 for m in manifest.get("tables", [])),
    }


# ====================================================================
# 回退预览
# ====================================================================
def diff_snapshot(
    snapshot: Snapshot, *, include_users: bool = False, restore_global: bool = False
) -> dict:
    """回退前预览：每张表「当前行数 / 快照行数 / 将删除 / 将写回」。"""
    registry = build_registry()
    entries: list[dict] = []
    for meta in SnapshotTable.objects.filter(snapshot=snapshot).order_by("id").values():
        spec = registry.by_label(meta["model_label"])
        if spec is None:
            entries.append(
                {
                    "model": meta["model_label"],
                    "table": meta["table_name"],
                    "policy": "skipped",
                    "snapshotRows": meta["row_count"],
                    "currentRows": None,
                    "deleteRows": 0,
                    "insertRows": 0,
                    "note": "当前代码中已不存在该模型",
                }
            )
            continue
        policy = resolve_restore_policy(
            spec,
            competition_id=snapshot.competition_id,
            include_users=include_users,
            restore_global=restore_global,
        )
        current = scope_queryset(spec, snapshot.competition_id).count()
        entries.append(
            {
                "model": spec.label,
                "modelName": spec.name,
                "table": spec.table,
                "policy": policy,
                "scope": spec.scope,
                "snapshotRows": meta["row_count"],
                "currentRows": current,
                "deleteRows": current if policy == "full" else 0,
                "insertRows": meta["row_count"] if policy != "record" else 0,
                "note": {
                    "full": "整表还原（先删后插）",
                    "upsert": "仅按主键回写，不删除任何行",
                    "record": "只记录，不回写",
                }.get(policy, ""),
            }
        )
    return {
        "snapshotId": snapshot.id,
        "scope": snapshot.scope,
        "competitionId": snapshot.competition_id,
        "tables": entries,
        "totals": {
            "deleteRows": sum(e["deleteRows"] or 0 for e in entries),
            "insertRows": sum(e["insertRows"] or 0 for e in entries),
            "affectedTables": sum(
                1 for e in entries if (e["deleteRows"] or e["insertRows"])
            ),
            "skippedTables": sum(1 for e in entries if e["policy"] == "record"),
        },
    }


# ====================================================================
# 回退
# ====================================================================
def _delete_scope(spec: TableSpec, competition_id: int | None, *, report: ProgressFn) -> int:
    """删除某表在作用域内的所有行（分批取主键，避免一次性载入）。"""
    model = spec.model
    qs = scope_queryset(spec, competition_id)
    total = 0
    while True:
        pks = list(qs.values_list("pk", flat=True)[: archive.BATCH_SIZE])
        if not pks:
            break
        deleted, _detail = model._default_manager.filter(pk__in=pks).delete()
        total += deleted
        if total and total % (archive.BATCH_SIZE * 4) == 0:
            report(f"清理 {spec.table}：已删除 {total} 行")
        if len(pks) < archive.BATCH_SIZE:
            break
    return total


def _restore_auto_fields(model, spec: TableSpec, rows: list[dict]) -> None:
    """bulk_create 会覆写 auto_now / auto_now_add 字段，这里批量回写原值。"""
    if not spec.auto_fields:
        return
    from django.db.models import Case, Value, When

    pk = spec.pk_attname
    for field_name in spec.auto_fields:
        field = model._meta.get_field(field_name)
        pairs = [
            (row.get(pk), row.get(field_name))
            for row in rows
            if row.get(pk) is not None and row.get(field_name) is not None
        ]
        for chunk in archive.chunks(pairs, 200):
            whens = [When(pk=pk_val, then=Value(val)) for pk_val, val in chunk]
            model._default_manager.filter(pk__in=[p for p, _ in chunk]).update(
                **{field_name: Case(*whens, output_field=field)}
            )


def _insert_rows(spec: TableSpec, rows: list[dict], *, report: ProgressFn) -> int:
    model = spec.model
    created = 0
    for chunk in archive.chunks(rows, archive.BATCH_SIZE):
        model._default_manager.bulk_create([model(**row) for row in chunk], batch_size=200)
        _restore_auto_fields(model, spec, chunk)
        created += len(chunk)
        if created % (archive.BATCH_SIZE * 4) == 0:
            report(f"写回 {spec.table}：已写入 {created} 行")
    return created


def _upsert_rows(spec: TableSpec, rows: list[dict], *, report: ProgressFn) -> int:
    """按主键回写（存在则 update，不存在则 insert），绝不删除。"""
    model = spec.model
    pk = spec.pk_attname
    pks = [row.get(pk) for row in rows if row.get(pk) is not None]
    existing: set = set()
    for chunk in archive.chunks(pks, 500):
        existing.update(
            model._default_manager.filter(pk__in=chunk).values_list("pk", flat=True)
        )
    updated = 0
    for row in rows:
        pk_val = row.get(pk)
        if pk_val in existing:
            values = {k: v for k, v in row.items() if k != pk}
            if values:
                model._default_manager.filter(pk=pk_val).update(**values)
            updated += 1
    to_create = [row for row in rows if row.get(pk) not in existing]
    if to_create:
        for chunk in archive.chunks(to_create, archive.BATCH_SIZE):
            model._default_manager.bulk_create([model(**row) for row in chunk], batch_size=200)
            _restore_auto_fields(model, spec, chunk)
    report(f"{spec.table}：更新 {updated} 行 / 新增 {len(to_create)} 行")
    return updated + len(to_create)


def _reset_sequences(specs: Iterable[TableSpec]) -> None:
    """重置自增序列，避免回退后新插入的行与历史主键冲突。"""
    try:
        from django.core.management.color import no_style

        style = no_style()
    except Exception:  # noqa: BLE001
        return
    targets = []
    for spec in specs:
        pk = spec.model._meta.pk
        if isinstance(pk, (models.AutoField, models.BigAutoField, models.SmallAutoField)):
            targets.append(spec.model)
    if not targets:
        return
    try:
        statements = connection.ops.sequence_reset_sql(style, targets)
    except Exception:  # noqa: BLE001
        logger.debug("生成序列重置语句失败", exc_info=True)
        return
    with connection.cursor() as cursor:
        for statement in statements:
            try:
                cursor.execute(statement)
            except Exception:  # noqa: BLE001
                logger.debug("执行序列重置语句失败：%s", statement, exc_info=True)


def _verify_full(spec: TableSpec, entry: SnapshotTable, competition_id: int | None) -> str | None:
    rows_now, digest_now = archive.db_table_checksum(
        spec, scope_queryset(spec, competition_id)
    )
    if rows_now != entry.row_count:
        return f"{spec.label}：期望 {entry.row_count} 行，实际 {rows_now} 行"
    if digest_now != entry.sha256:
        return f"{spec.label}：内容校验和不一致"
    return None


def _verify_upsert(spec: TableSpec, raw_rows: list[dict]) -> str | None:
    """upsert 表只校验「快照内的行都存在且内容一致」，允许库中多出其它行。"""
    pk = spec.pk_attname
    expected = {
        raw.get(pk): archive.row_json(raw) for raw in raw_rows if raw.get(pk) is not None
    }
    if not expected:
        return None
    actual: dict = {}
    for chunk in archive.chunks(list(expected.keys()), 500):
        for row in spec.model._default_manager.filter(pk__in=chunk).values():
            actual[row.get(pk)] = archive.row_json(archive.encode_row(spec, row))
    missing = [p for p in expected if p not in actual]
    if missing:
        return f"{spec.label}：回退后缺失 {len(missing)} 行（如主键 {missing[:3]}）"
    mismatched = [p for p, body in expected.items() if actual.get(p) != body]
    if mismatched:
        return f"{spec.label}：{len(mismatched)} 行内容与快照不一致（如主键 {mismatched[:3]}）"
    return None


def restore_snapshot(
    snapshot: Snapshot,
    *,
    operator: Any = None,
    include_users: bool = False,
    restore_global: bool = False,
    restore_files: bool = False,
    verify: bool = True,
    progress: ProgressFn | None = None,
) -> dict:
    """把库内容回退到 `snapshot` 记录的状态。

    前置条件：调用方必须已经把门禁切到 RESTORING 并排空在途写请求
    （见 `views.py` 的回退流程）。本函数不负责门禁切换。
    """
    report = progress or _noop
    registry = build_registry()
    directory = archive.snapshot_dir(snapshot.id)
    try:
        manifest = archive.read_manifest(directory)
    except (FileNotFoundError, ValueError) as exc:
        raise SnapshotError(f"快照归档不存在或已损坏：{exc}") from exc

    if snapshot.status not in ("ready", "restored", "restoring", "building"):
        raise SnapshotError(f"快照状态为 {snapshot.status}，不可用于回退")

    started = time.monotonic()
    competition_id = snapshot.competition_id
    entries = list(SnapshotTable.objects.filter(snapshot=snapshot).order_by("id"))
    if not entries:
        raise SnapshotError("快照内没有任何表记录，无法回退")

    # 归档完整性预检：损坏的归档绝不允许进入回退流程
    check = verify_snapshot(snapshot)
    if not check["ok"]:
        raise SnapshotError(
            "快照归档校验未通过，已中止回退：" + "；".join(check["problems"][:5])
        )

    plans: list[tuple[TableSpec, SnapshotTable, str]] = []
    for entry in entries:
        spec = registry.by_label(entry.model_label)
        if spec is None:
            report(f"跳过 {entry.model_label}（当前代码中不存在该模型）")
            continue
        policy = resolve_restore_policy(
            spec,
            competition_id=competition_id,
            include_users=include_users,
            restore_global=restore_global,
        )
        if policy == "record":
            report(f"跳过 {spec.label}（策略：只记录不回写）")
        plans.append((spec, entry, policy))

    plans.sort(key=lambda item: item[0].order)
    delete_plan = [(s, e, p) for s, e, p in plans if p == "full"]
    write_plan = [(s, e, p) for s, e, p in plans if p in ("full", "upsert")]

    deleted_rows = 0
    inserted_rows = 0
    file_result: dict[str, Any] = {}
    failures: list[str] = []

    Snapshot.objects.filter(pk=snapshot.id).update(status="restoring")
    try:
        # 回退期间的写入不得触发审计与实时广播（否则会产生成千上万条噪声事件，
        # 且审计写的是「回退中」的中间态）；统一由回退完成后的 system:restored 通知。
        with suppress_signals(), suspend_exact_decimal_guard(), transaction.atomic():
            # ---------- 1) 删除（依赖倒序） ----------
            report("正在清理当前数据…")
            for spec, _entry, _policy in reversed(delete_plan):
                deleted_rows += _delete_scope(spec, competition_id, report=report)

            # ---------- 2) 写回（依赖正序） ----------
            upsert_payload: list[tuple[TableSpec, list[dict]]] = []
            for spec, entry, policy in write_plan:
                path = archive.table_file(snapshot.id, spec.table)
                if not path.exists():
                    raise SnapshotError(f"{spec.label}：归档文件缺失，回退中止")
                report(f"正在写回 {spec.table}…")
                raw_rows = list(archive.iter_rows(path))
                if policy == "upsert":
                    upsert_payload.append((spec, raw_rows))
                    continue
                rows = [archive.decode_row(spec, raw) for raw in raw_rows]
                inserted_rows += _insert_rows(spec, rows, report=report)

            for spec, raw_rows in upsert_payload:
                rows = [archive.decode_row(spec, raw) for raw in raw_rows]
                inserted_rows += _upsert_rows(spec, rows, report=report)

            # ---------- 3) 序列重置 ----------
            _reset_sequences([s for s, _e, p in write_plan if p == "full"])

            # ---------- 4) 回退后一致性校验 ----------
            if verify:
                report("正在校验回退结果…")
                for spec, entry, policy in write_plan:
                    if policy == "full":
                        problem = _verify_full(spec, entry, competition_id)
                    else:
                        raw_rows = dict(upsert_payload).get(spec)
                        problem = _verify_upsert(spec, raw_rows or [])
                    if problem:
                        failures.append(problem)
                if failures:
                    raise SnapshotError(
                        "回退后校验失败，已整体回滚（数据未发生任何变化）："
                        + "；".join(failures[:5])
                    )

            # ---------- 5) 版本号递增（与回退同事务，失败一起回滚） ----------
            new_version = bump_data_version()

        if restore_files and bool(manifest.get("includeFiles")):
            report("正在还原上传文件…")
            count, size, problems = archive.restore_files(archive.files_dir(snapshot.id))
            file_result = {"files": count, "bytes": size, "problems": problems}

        duration_ms = int((time.monotonic() - started) * 1000)
        Snapshot.objects.filter(pk=snapshot.id).update(
            status="restored",
            restore_count=(snapshot.restore_count or 0) + 1,
            restored_at=timezone.now(),
            last_restored_by_name=getattr(operator, "username", "") or "",
            error="",
        )
        snapshot.refresh_from_db()
        result = {
            "snapshotId": snapshot.id,
            "label": snapshot.label,
            "scope": snapshot.scope,
            "competitionId": competition_id,
            "deletedRows": deleted_rows,
            "insertedRows": inserted_rows,
            "durationMs": duration_ms,
            "dataVersion": new_version,
            "tables": len(write_plan),
            "skippedTables": len(plans) - len(write_plan),
            "files": file_result,
            "verified": bool(verify),
        }
        report(
            f"回退完成：删除 {deleted_rows} 行 / 写回 {inserted_rows} 行，"
            f"数据版本 {new_version}"
        )
        return result
    except Exception as exc:
        logger.error("回退失败：%s", exc, exc_info=True)
        Snapshot.objects.filter(pk=snapshot.id).update(
            error=f"回退失败：{type(exc).__name__}: {exc}"[:4000],
        )
        raise


# ====================================================================
# 生命周期
# ====================================================================
def delete_snapshot(snapshot: Snapshot, *, force: bool = False) -> None:
    if snapshot.locked and not force:
        raise SnapshotError("该快照已锁定，如需删除请先解锁或显式强制删除")
    archive.remove_snapshot_dir(snapshot.id)
    snapshot.delete()


def cleanup_snapshots(
    *,
    keep_last: int | None = None,
    keep_days: int | None = None,
    dry_run: bool = False,
) -> dict:
    """按保留策略清理快照（锁定的一律保留）。"""
    policy = get_policy()
    keep_last = policy.keep_last if keep_last is None else int(keep_last)
    keep_days = policy.keep_days if keep_days is None else int(keep_days)
    qs = Snapshot.objects.filter(status__in=("ready", "restored", "failed")).order_by(
        "-created_at", "-id"
    )
    kept: list[int] = []
    removed: list[dict] = []
    now = timezone.now()
    for index, snapshot in enumerate(qs):
        if snapshot.locked:
            kept.append(snapshot.id)
            continue
        drop = False
        reason = ""
        if keep_last and index >= keep_last:
            drop = True
            reason = f"超出保留份数 {keep_last}"
        if not drop and keep_days and snapshot.created_at:
            if (now - snapshot.created_at) >= timedelta(days=keep_days):
                drop = True
                reason = f"超出保留天数 {keep_days}"
        if drop:
            removed.append(
                {
                    "id": snapshot.id,
                    "label": snapshot.label,
                    "createdAt": (
                        snapshot.created_at.isoformat() if snapshot.created_at else None
                    ),
                    "reason": reason,
                }
            )
            if not dry_run:
                archive.remove_snapshot_dir(snapshot.id)
                snapshot.delete()
        else:
            kept.append(snapshot.id)
    return {
        "removed": removed,
        "removedCount": len(removed),
        "keptCount": len(kept),
        "dryRun": dry_run,
        "keepLast": keep_last,
        "keepDays": keep_days,
    }


def get_policy() -> SnapshotPolicy:
    policy, _created = SnapshotPolicy.objects.get_or_create(pk=SnapshotPolicy.SENTINEL_ID)
    return policy


def auto_snapshot_if_due(
    *, force: bool = False, progress: ProgressFn | None = None
) -> Snapshot | None:
    """按策略判断是否到期，到期则创建一份自动快照（供定时命令调用）。"""
    policy = get_policy()
    if not policy.auto_enabled and not force:
        return None
    now = timezone.now()
    if (
        not force
        and policy.last_auto_at
        and (now - policy.last_auto_at)
        < timedelta(minutes=max(1, int(policy.auto_interval_minutes or 30)))
    ):
        return None

    competition_id: int | None = None
    if policy.auto_scope == "competition":
        from apps.competitions.models import Competition

        competition_id = (
            Competition.objects.filter(status="ACTIVE").order_by("id").values_list("id", flat=True).first()
        )
        if competition_id is None:
            logger.warning("自动快照：没有进行中的比赛，跳过")
            return None

    with exclusive_window_for_snapshot("自动快照", operator=None):
        snapshot = create_snapshot(
            label=f"自动快照 {timezone.localtime(now):%Y-%m-%d %H:%M}",
            note="由自动快照策略创建",
            kind="auto",
            competition_id=competition_id,
            include_files=policy.auto_include_files,
            operator=None,
            progress=progress,
        )
    SnapshotPolicy.objects.filter(pk=policy.pk).update(last_auto_at=now)
    try:
        cleanup_snapshots()
    except Exception:  # noqa: BLE001
        logger.warning("自动快照后清理失败", exc_info=True)
    return snapshot


class exclusive_window_for_snapshot:
    """轻量包装：自动快照期间短暂暂停写入，拿到一致的静止点。"""

    def __init__(self, reason: str, operator: Any = None):
        self.reason = reason
        self.operator = operator

    def __enter__(self):
        from . import gate

        self._ctx = gate.exclusive_window(
            mode=gate.MODE_PAUSED,
            reason=self.reason,
            message="系统正在创建自动快照，请稍候…",
            ttl_seconds=int(getattr(settings, "SNAPSHOT_AUTO_PAUSE_SECONDS", 60)),
            operator_id=getattr(self.operator, "id", None),
            operator_name=getattr(self.operator, "username", "") or "system",
            resume_reason="自动快照完成，系统已恢复",
        )
        return self._ctx.__enter__()

    def __exit__(self, exc_type, exc, tb):
        return self._ctx.__exit__(exc_type, exc, tb)


def snapshot_status() -> dict:
    """概览：门禁状态 + 快照数量与占用。"""
    from .gate import load_state

    root = archive.snapshots_root()
    return {
        "gate": load_state(force=True),
        "policy": get_policy().to_dict(),
        "counts": {
            "ready": Snapshot.objects.filter(status__in=("ready", "restored")).count(),
            "failed": Snapshot.objects.filter(status="failed").count(),
            "building": Snapshot.objects.filter(
                status__in=("building", "restoring")
            ).count(),
            "total": Snapshot.objects.count(),
        },
        "latest": (
            Snapshot.objects.order_by("-created_at").first().to_summary()
            if Snapshot.objects.exists()
            else None
        ),
        "storagePath": str(root),
        "storageBytes": archive.dir_size(root),
        "registryTables": len(build_registry().specs),
        "writers": _writer_stats(),
    }


def _writer_stats() -> dict:
    try:
        from .gate import writer_stats

        return writer_stats()
    except Exception:  # noqa: BLE001
        return {}
