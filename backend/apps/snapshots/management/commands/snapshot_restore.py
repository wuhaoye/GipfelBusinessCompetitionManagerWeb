"""应急回退命令：不依赖 Web 界面，直接在服务器上把数据回退到某份快照。

用法：

    python manage.py snapshot_restore --list                 # 列出可用快照
    python manage.py snapshot_restore --id 12 --yes          # 回退到 #12（真实执行）
    python manage.py snapshot_restore --id 12 --dry-run      # 只看影响，不写库
    python manage.py snapshot_restore --id 12 --yes --include-users --restore-files

行为与 Web 界面完全一致：
1. 把全局门禁切到 RESTORING 并排空在途写请求（在线客户端会收到强制暂停事件）；
2. 默认先创建一份「回退前安全快照」，随时可以再回退回来；
3. 事务内整表还原 + 回退后 sha256 校验，任何不一致都会整体回滚；
4. 成功后 data_version +1，在线客户端据此丢弃本地缓存并整体重载。

注意：本命令会关闭「必须从 Web 操作」的限制，属运维通道。请务必带 `--id` 明确目标。
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.snapshots import engine, gate
from apps.snapshots.models import Snapshot


class Command(BaseCommand):
    help = "把数据回退到指定快照（应急运维通道）"

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="列出快照后退出")
        parser.add_argument("--id", type=int, default=None, help="目标快照编号")
        parser.add_argument("--yes", action="store_true", help="确认执行（缺省仅预览）")
        parser.add_argument("--dry-run", action="store_true", help="只输出影响，不写库")
        parser.add_argument("--include-users", action="store_true", help="同时回写账号数据")
        parser.add_argument(
            "--restore-global", action="store_true", help="同时回写全局表（产业类型等）"
        )
        parser.add_argument("--restore-files", action="store_true", help="同时还原上传文件")
        parser.add_argument(
            "--skip-safety", action="store_true", help="跳过回退前安全快照（不建议）"
        )
        parser.add_argument("--no-verify", action="store_true", help="跳过回退后内容校验")

    def handle(self, *args, **options):
        if options["list"] or options["id"] is None:
            self._print_list()
            if options["id"] is None:
                if not options["list"]:
                    raise CommandError("请用 --id <快照编号> 指定目标（或 --list 查看）")
                return

        snapshot = Snapshot.objects.filter(pk=options["id"]).first()
        if snapshot is None:
            raise CommandError(f"快照 #{options['id']} 不存在")
        if snapshot.status in ("failed", "building"):
            raise CommandError(f"快照 #{snapshot.id} 状态为 {snapshot.status}，不可回退")

        include_users = options["include_users"]
        restore_global = options["restore_global"]
        diff = engine.diff_snapshot(
            snapshot, include_users=include_users, restore_global=restore_global
        )
        self.stdout.write(
            f"目标快照 #{snapshot.id}「{snapshot.label}」（{snapshot.scope_label}）"
        )
        self.stdout.write(
            f"影响：删除 {diff['totals']['deleteRows']} 行 / 写回 "
            f"{diff['totals']['insertRows']} 行 / 涉及 {diff['totals']['affectedTables']} 张表"
        )
        for entry in diff["tables"]:
            if entry["deleteRows"] or entry["insertRows"]:
                self.stdout.write(
                    f"  · {entry['table']:<28} 当前 {entry['currentRows']:>7} → "
                    f"快照 {entry['snapshotRows']:>7}（{entry['note']}）"
                )

        if options["dry_run"] or not options["yes"]:
            self.stdout.write(
                self.style.WARNING("当前为预览模式，未做任何改动；确认无误后加 --yes 执行")
            )
            if not options["dry_run"]:
                self.stdout.write(
                    self.style.WARNING("真实执行请追加：--yes")
                )
            return

        verify = engine.verify_snapshot(snapshot)
        if not verify["ok"]:
            raise CommandError(
                "归档校验未通过：" + "；".join(verify["problems"][:5])
            )

        def _progress(msg: str) -> None:
            self.stdout.write(f"  · {msg}")

        with gate.exclusive_window(
            mode=gate.MODE_RESTORING,
            reason=f"运维命令 snapshot_restore 正在回退到快照 #{snapshot.id}",
            message="系统正在回退数据，请稍候；回退完成后页面会自动同步。",
            ttl_seconds=1800,
            snapshot_id=snapshot.id,
            operator_name="snapshot_restore(cli)",
            resume_reason="数据回退完成，系统已恢复",
        ):
            safety = None
            if not options["skip_safety"]:
                _progress("正在创建回退前安全快照…")
                safety = engine.create_snapshot(
                    label=f"回退前自动快照（目标 #{snapshot.id}）",
                    note="由 snapshot_restore 命令创建",
                    kind="pre-restore",
                    competition_id=snapshot.competition_id,
                    include_files=False,
                    progress=_progress,
                )
            result = engine.restore_snapshot(
                snapshot,
                operator=None,
                include_users=include_users,
                restore_global=restore_global,
                restore_files=options["restore_files"],
                verify=not options["no_verify"],
                progress=_progress,
            )
            result["safetySnapshotId"] = safety.id if safety else None
            from apps.realtime.emit import emit_system_restored

            emit_system_restored(result)

        self.stdout.write(
            self.style.SUCCESS(
                f"回退完成：删除 {result['deletedRows']} 行 / 写回 {result['insertedRows']} 行 / "
                f"耗时 {result['durationMs']}ms / 数据版本 {result['dataVersion']}"
            )
        )
        if result.get("safetySnapshotId"):
            self.stdout.write(
                f"如需撤销本次回退，可再回退到安全快照 #{result['safetySnapshotId']}"
            )

    def _print_list(self) -> None:
        rows = Snapshot.objects.order_by("-created_at")[:50]
        if not rows:
            self.stdout.write("（暂无快照）")
            return
        self.stdout.write(f"{'ID':>5}  {'状态':<10} {'范围':<18} {'行数':>8}  名称")
        for snap in rows:
            self.stdout.write(
                f"{snap.id:>5}  {snap.status:<10} {snap.scope_label:<18} "
                f"{snap.row_count:>8}  {snap.label}"
            )
