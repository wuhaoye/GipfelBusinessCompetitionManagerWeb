"""自动快照命令：按策略创建快照并清理过期归档。

用法（Linux 生产环境建议交给 systemd timer 或 cron，每 5~10 分钟跑一次）：

    python manage.py snapshot_auto                 # 到期才创建（受 SnapshotPolicy 控制）
    python manage.py snapshot_auto --force         # 忽略间隔，立刻创建一份
    python manage.py snapshot_auto --scope system  # 强制全系统快照
    python manage.py snapshot_auto --scope competition --competition-id 3
    python manage.py snapshot_auto --include-files # 一并归档 uploads
    python manage.py snapshot_auto --no-pause      # 不暂停写入（一致性稍弱，但不打断用户）
    python manage.py snapshot_auto --cleanup-only  # 只按保留策略清理

命令自身会短暂把系统切到 PAUSED（默认，可用 --no-pause 关闭）并排空在途写请求，
从而得到静止点；在线客户端会收到 system:paused / system:resumed 事件，
表现为遮罩一闪而过。
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.snapshots import engine, gate


class Command(BaseCommand):
    help = "按保留策略创建自动快照并清理过期快照"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="忽略间隔，立刻创建")
        parser.add_argument(
            "--scope",
            choices=["policy", "system", "competition"],
            default="policy",
            help="快照范围（默认按策略）",
        )
        parser.add_argument("--competition-id", type=int, default=None)
        parser.add_argument("--label", type=str, default="")
        parser.add_argument("--note", type=str, default="")
        parser.add_argument("--include-files", action="store_true")
        parser.add_argument(
            "--no-pause", action="store_true", help="不暂停写入（弱一致快照）"
        )
        parser.add_argument("--cleanup-only", action="store_true", help="只清理，不创建")
        parser.add_argument("--keep-last", type=int, default=None)
        parser.add_argument("--keep-days", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["cleanup_only"]:
            result = engine.cleanup_snapshots(
                keep_last=options["keep_last"],
                keep_days=options["keep_days"],
                dry_run=options["dry_run"],
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"清理完成：删除 {result['removedCount']} 份 / 保留 {result['keptCount']} 份"
                )
            )
            return

        policy = engine.get_policy()
        if not options["force"] and not policy.auto_enabled:
            self.stdout.write("自动快照策略未启用（SnapshotPolicy.auto_enabled=false），跳过")
            return

        scope = options["scope"]
        if scope == "system":
            competition_id = None
        elif scope == "competition":
            competition_id = options["competition_id"] or getattr(policy, "auto_scope", None)
            if options["competition_id"] is None:
                from apps.competitions.models import Competition

                competition_id = (
                    Competition.objects.filter(status="ACTIVE")
                    .order_by("id")
                    .values_list("id", flat=True)
                    .first()
                )
        else:
            competition_id = options["competition_id"]
            if competition_id is None and policy.auto_scope == "system":
                competition_id = None

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] 将要创建快照：scope={'system' if competition_id is None else competition_id} "
                f"includeFiles={options['include_files']}"
            )
            return

        include_files = options["include_files"] or policy.auto_include_files

        def _run() -> None:
            snapshot = engine.create_snapshot(
                label=options["label"] or f"自动快照（{self._now()}）",
                note=options["note"] or "由 snapshot_auto 命令创建",
                kind="auto",
                competition_id=competition_id,
                include_files=include_files,
                operator=None,
                progress=lambda msg: self.stdout.write(f"  · {msg}"),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"快照 #{snapshot.id} 创建完成：{snapshot.row_count} 行 / "
                    f"{snapshot.table_count} 张表 / {snapshot.byte_size} 字节"
                )
            )
            from django.utils import timezone

            from apps.snapshots.models import SnapshotPolicy

            SnapshotPolicy.objects.filter(pk=policy.pk).update(last_auto_at=timezone.now())

        if options["no_pause"]:
            _run()
        else:
            try:
                with gate.exclusive_window(
                    mode=gate.MODE_PAUSED,
                    reason="系统正在创建自动快照",
                    message="系统正在创建自动快照，请稍候…",
                    ttl_seconds=600,
                    operator_name="snapshot_auto",
                    resume_reason="自动快照完成，系统已恢复",
                ):
                    _run()
            except gate.GateBusy as exc:
                raise CommandError(f"创建快照失败：{exc}") from exc

        if options["keep_last"] is None and options["keep_days"] is None:
            cleanup = engine.cleanup_snapshots()
            self.stdout.write(
                f"保留策略清理：删除 {cleanup['removedCount']} 份 / 保留 {cleanup['keptCount']} 份"
            )

    @staticmethod
    def _now() -> str:
        from django.utils import timezone

        return timezone.localtime().strftime("%Y-%m-%d %H:%M")
