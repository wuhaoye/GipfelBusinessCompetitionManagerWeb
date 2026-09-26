"""审计归档：把超过保留期的 AuditLog 导出为 jsonl.gz，**成功后才**分批删除。

用法：
    python manage.py audit_archive                     # 默认保留 settings.AUDIT_RETENTION_DAYS（7 天）
    python manage.py audit_archive --days 3            # 覆盖保留期
    python manage.py audit_archive --dry-run           # 只统计，不导出、不删除
    python manage.py audit_archive --no-archive        # 不导出直接删（危险，需自行确认已备份）
    python manage.py audit_archive --batch-size 500    # 每批删除行数（默认 1000）
    python manage.py audit_archive --output-dir D:\\bak\\audit

安全约束（C2 阶段 1，见《架构性运维约束整改简报.md》C2.1「审计只增不删」）：
1. **先导出、后删除**：导出文件以临时名写完后 `os.replace` 原子落盘，成功才进入删除阶段；
   导出失败 / 归档目录不可写 / 行数不一致 → **不删除任何行**，抛 CommandError（退出码非 0）。
2. 分批删除（默认 1000 行/批，每批一个事务）：避免单个长写事务把 WAL 撑大、
   也避免写锁长时间挡住并发读（WAL 只允许一个写者，不允许多写并行）。
3. WAL 下**不做 VACUUM**：VACUUM 需要独占锁并整库重写，赛时不可接受；
   结束时只做 `PRAGMA wal_checkpoint(TRUNCATE)` 回收 WAL 文件，失败仅 warning。
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone

from apps.audit.models import AuditLog

logger = logging.getLogger("gipfel")

_DEFAULT_BATCH_SIZE = 1000


def _local_stamp() -> datetime:
    """本地当前时间（USE_TZ=True 时转本地时区；防御性兼容 USE_TZ=False）。"""
    now = timezone.now()
    if getattr(settings, "USE_TZ", False):
        return timezone.localtime(now)
    return now


def _decode_changes(raw):
    """changes 在库里是 JSON 字符串（TextField），导出时还原为 JSON 值便于排查。"""
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


class Command(BaseCommand):
    help = (
        "归档并清理超期审计日志：先导出 logs/audit_archive/audit-<YYYYMMDD>.jsonl.gz，"
        "导出成功后再分批删除（导出失败则不删除并以非 0 退出码报错）"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="保留天数（默认 settings.AUDIT_RETENTION_DAYS）",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_DEFAULT_BATCH_SIZE,
            help=f"每批删除行数（默认 {_DEFAULT_BATCH_SIZE}）",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只统计并打印计划，不导出、不删除",
        )
        parser.add_argument(
            "--no-archive",
            action="store_true",
            help="跳过导出直接删除（危险：仅在确认这些行已另有备份时使用）",
        )
        parser.add_argument(
            "--output-dir",
            default=None,
            help="归档目录（默认 <BASE_DIR>/logs/audit_archive）",
        )

    # ---------------- 主流程 ----------------
    def handle(self, *args, **options):
        days = options.get("days")
        if days is None:
            days = int(getattr(settings, "AUDIT_RETENTION_DAYS", 7))
        if days < 1:
            raise CommandError(
                f"--days 必须 ≥ 1（收到 {days}）：保留期过短会误删赛时审计，"
                "确需清空历史请显式指定 --days 1 并先确认已导出"
            )

        raw_batch = options.get("batch_size")
        batch_size = _DEFAULT_BATCH_SIZE if raw_batch is None else int(raw_batch)
        if batch_size < 1:
            raise CommandError(f"--batch-size 必须 ≥ 1（收到 {batch_size}）")

        dry_run = bool(options.get("dry_run"))
        no_archive = bool(options.get("no_archive"))
        out_dir = (
            Path(options["output_dir"])
            if options.get("output_dir")
            else Path(settings.BASE_DIR) / "logs" / "audit_archive"
        )

        cutoff = timezone.now() - timedelta(days=days)
        expired = AuditLog.objects.filter(created_at__lt=cutoff).count()
        total = AuditLog.objects.count()
        target = self._archive_path(out_dir)

        self.stdout.write(
            f"审计归档：保留 {days} 天，cutoff={cutoff.isoformat()}，"
            f"当前共 {total} 行（超期 {expired} 行 / 保留 {total - expired} 行）"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run：不导出、不删除"))
            self.stdout.write(f"  计划导出 → {target}")
            self.stdout.write(f"  计划删除 {expired} 行（batch-size={batch_size}）")
            return

        if expired == 0:
            self.stdout.write("没有超期数据，无需导出/删除")
            self._checkpoint()
            return

        if no_archive:
            self.stdout.write(
                self.style.WARNING(
                    "--no-archive：跳过导出直接删除（请确认这些行已在别处备份）"
                )
            )
        else:
            archived = self._export(cutoff, expired, out_dir)
            self.stdout.write(self.style.SUCCESS(f"已导出 {expired} 行 → {archived}"))

        deleted = self._delete_expired(cutoff, batch_size, expired)
        remaining = AuditLog.objects.count()
        self.stdout.write(
            self.style.SUCCESS(f"已删除 {deleted} 行；剩余 {remaining} 行")
        )
        self._checkpoint()

    # ---------------- 归档文件 ----------------
    @staticmethod
    def _archive_path(out_dir: Path, stamp: datetime | None = None) -> Path:
        stamp = stamp or _local_stamp()
        return out_dir / f"audit-{stamp:%Y%m%d}.jsonl.gz"

    def _export(self, cutoff, expected: int, out_dir: Path) -> Path:
        """导出超期行 → jsonl.gz（临时文件 + 原子替换）。失败抛 CommandError（不删任何行）。"""
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            if not out_dir.is_dir():
                raise NotADirectoryError(f"归档路径不是目录：{out_dir}")
        except Exception as exc:  # noqa: BLE001
            raise CommandError(
                f"归档目录不可用，未删除任何行：{out_dir}（{type(exc).__name__}: {exc}）"
            ) from exc

        target = self._archive_path(out_dir)
        if target.exists():
            # 同一天重复执行不覆盖既有归档（避免静默丢历史）
            stamp = _local_stamp()
            target = out_dir / f"audit-{stamp:%Y%m%d-%H%M%S}.jsonl.gz"
        if target.exists():
            target = out_dir / f"audit-{_local_stamp():%Y%m%d-%H%M%S}-{os.getpid()}.jsonl.gz"

        tmp = target.with_name(target.name + ".part")
        written = 0
        try:
            with gzip.open(tmp, "wt", encoding="utf-8", newline="\n") as fh:
                for row in self._iter_rows(cutoff):
                    fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                    fh.write("\n")
                    written += 1
            if written != expected:
                raise RuntimeError(
                    f"导出行数不一致：期望 {expected}，实际 {written}（可能有并发归档/删除）"
                )
            os.replace(tmp, target)  # 原子落盘：只有完整文件才会出现在最终路径
        except Exception as exc:  # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - 清理失败不影响判定
                pass
            raise CommandError(
                f"导出失败，未删除任何行：{tmp}（{type(exc).__name__}: {exc}）"
            ) from exc
        return target

    @staticmethod
    def _iter_rows(cutoff):
        qs = AuditLog.objects.filter(created_at__lt=cutoff).order_by("pk")
        for obj in qs.iterator(chunk_size=_DEFAULT_BATCH_SIZE):
            yield {
                "id": obj.pk,
                "kind": obj.kind,
                "operator_id": obj.operator_id,
                "operator_name": obj.operator_name,
                "action": obj.action,
                "model": obj.model,
                "record_id": obj.record_id,
                "competition_id": obj.competition_id,
                "changes": _decode_changes(obj.changes),
                "status_code": obj.status_code,
                "error_summary": obj.error_summary,
                "ip": obj.ip,
                "device": obj.device,
                "request_id": obj.request_id,
                "created_at": obj.created_at.isoformat() if obj.created_at else None,
            }

    # ---------------- 分批删除 ----------------
    def _delete_expired(self, cutoff, batch_size: int, expected: int) -> int:
        deleted = 0
        while True:
            pks = list(
                AuditLog.objects.filter(created_at__lt=cutoff)
                .order_by("pk")
                .values_list("pk", flat=True)[:batch_size]
            )
            if not pks:
                break
            with transaction.atomic():
                count, _detail = AuditLog.objects.filter(pk__in=pks).delete()
            if count <= 0:  # 理论不可达；防御死循环
                logger.warning("分批删除未生效（pk 数=%s），提前退出", len(pks))
                break
            deleted += count
            self.stdout.write(f"  已删除 {deleted}/{expected} 行")
        return deleted

    # ---------------- WAL 回收 ----------------
    def _checkpoint(self) -> None:
        """WAL 库结束时回收 WAL 文件；非 WAL / 非 sqlite / 失败都只打印或 warning。

        这里**不跑 VACUUM**：VACUUM 需要独占锁并重写整库，赛时会把所有读写挡住，
        WAL 下清理空间靠 checkpoint 即可（见简报 C2.4「不该做」）。
        """
        connection = connections["default"]
        if getattr(connection, "vendor", None) != "sqlite":
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA journal_mode")
                row = cursor.fetchone()
            mode = str(row[0]).lower() if row else ""
            if mode != "wal":
                self.stdout.write(f"  跳过 wal_checkpoint（journal_mode={mode or 'unknown'}）")
                return
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                cursor.fetchall()
            self.stdout.write("  已执行 PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001 - 回收失败不影响归档结果
            logger.warning("PRAGMA wal_checkpoint(TRUNCATE) 失败（已忽略）", exc_info=True)
