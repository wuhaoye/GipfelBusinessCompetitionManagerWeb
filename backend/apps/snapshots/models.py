"""快照系统数据模型。

四张表：
- Snapshot       一次快照的元数据（作用域、统计、状态、操作者、归档目录）
- SnapshotTable  快照内每张表的清单（行数、字节数、内容校验和、回写策略）
- SystemGate     单例：全局工作门禁（RUNNING / PAUSED / RESTORING）+ 数据版本号
- SnapshotPolicy 单例：自动快照与保留策略

注意：本应用的模型**不注册**到 `apps.realtime.emit.MODEL_TO_RESOURCE`，
因此不会触发 post_save 审计与实时广播（避免快照自身的写入噪声）。
快照相关操作的审计由 `engine.py` / `views.py` 显式调用 `log_write` 落库。
"""
from __future__ import annotations

from django.db import models


class Snapshot(models.Model):
    """一次数据快照。"""

    KIND_CHOICES = [
        ("manual", "manual"),  # 管理员手动创建
        ("auto", "auto"),  # 自动（保留策略 / 定时命令）
        ("pre-restore", "pre-restore"),  # 回退前的自动安全快照
        ("system", "system"),  # 系统升级前（预留）
    ]
    STATUS_CHOICES = [
        ("building", "building"),  # 正在写入归档
        ("ready", "ready"),  # 归档完整可用
        ("failed", "failed"),  # 归档失败（error 字段有原因）
        ("restoring", "restoring"),  # 正在被回退
        ("restored", "restored"),  # 已成功回退过（仍可再次回退）
    ]
    SCOPE_CHOICES = [("competition", "competition"), ("system", "system")]

    label = models.CharField(max_length=128, help_text="快照名称（管理员可读）")
    note = models.TextField(blank=True, default="", help_text="备注")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default="manual")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="building")
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, default="competition")
    #: 作用域比赛；None 表示全系统快照
    competition_id = models.IntegerField(null=True, blank=True, db_index=True)
    competition_name = models.CharField(max_length=128, blank=True, default="")

    # ---------- 统计 ----------
    table_count = models.IntegerField(default=0)
    row_count = models.BigIntegerField(default=0)
    byte_size = models.BigIntegerField(default=0)
    include_files = models.BooleanField(default=False)
    file_count = models.IntegerField(default=0)
    file_byte_size = models.BigIntegerField(default=0)

    # ---------- 内容指纹 ----------
    #: 快照建立时刻的 SystemGate.data_version（用于判断「这份快照对应哪一代数据」）
    data_version = models.IntegerField(default=0)
    #: 快照建立时刻的实时事件序号（便于与前端 seq 对齐排查）
    server_seq = models.BigIntegerField(default=0)
    app_version = models.CharField(max_length=32, blank=True, default="")
    manifest_sha256 = models.CharField(max_length=64, blank=True, default="")

    # ---------- 操作者与耗时 ----------
    created_by_id = models.IntegerField(null=True, blank=True)
    created_by_name = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    duration_ms = models.IntegerField(default=0)

    # ---------- 生命周期 ----------
    #: 锁定后不会被保留策略清理，也不能直接删除（需 force=true）
    locked = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")
    storage_path = models.CharField(max_length=255, blank=True, default="")
    restore_count = models.IntegerField(default=0)
    restored_at = models.DateTimeField(null=True, blank=True)
    last_restored_by_name = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        db_table = "snapshots"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["competition_id", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - 展示用
        where = f"比赛 {self.competition_id}" if self.competition_id else "全系统"
        return f"#{self.id} {self.label}（{where}）"

    @property
    def scope_label(self) -> str:
        if self.scope == "system":
            return "全系统"
        return f"比赛：{self.competition_name or self.competition_id}"

    def to_summary(self) -> dict:
        """列表用的精简结构（camelCase，与前端契约一致）。"""
        return {
            "id": self.id,
            "label": self.label,
            "note": self.note,
            "kind": self.kind,
            "status": self.status,
            "scope": self.scope,
            "competitionId": self.competition_id,
            "competitionName": self.competition_name,
            "scopeLabel": self.scope_label,
            "tableCount": self.table_count,
            "rowCount": self.row_count,
            "byteSize": self.byte_size,
            "includeFiles": self.include_files,
            "fileCount": self.file_count,
            "fileByteSize": self.file_byte_size,
            "dataVersion": self.data_version,
            "serverSeq": self.server_seq,
            "appVersion": self.app_version,
            "createdById": self.created_by_id,
            "createdByName": self.created_by_name,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "durationMs": self.duration_ms,
            "locked": self.locked,
            "error": self.error,
            "storagePath": self.storage_path,
            "restoreCount": self.restore_count,
            "restoredAt": self.restored_at.isoformat() if self.restored_at else None,
            "lastRestoredByName": self.last_restored_by_name,
        }


class SnapshotTable(models.Model):
    """快照内一张表的清单条目。"""

    #: full = 回退时整表还原；upsert = 只按主键回写不删除；record = 只记录不回写
    POLICY_CHOICES = [
        ("full", "full"),
        ("upsert", "upsert"),
        ("record", "record"),
    ]

    snapshot = models.ForeignKey(
        Snapshot, on_delete=models.CASCADE, related_name="tables"
    )
    model_label = models.CharField(max_length=64, help_text="如 materials.Material")
    model_name = models.CharField(max_length=64, help_text="如 Material")
    table_name = models.CharField(max_length=64)
    resource = models.CharField(max_length=64, blank=True, default="")
    policy = models.CharField(max_length=16, choices=POLICY_CHOICES, default="full")
    row_count = models.BigIntegerField(default=0)
    byte_size = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    file_name = models.CharField(max_length=160, blank=True, default="")

    class Meta:
        db_table = "snapshot_tables"
        ordering = ["id"]
        unique_together = (("snapshot", "table_name"),)

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.model_label} × {self.row_count}"

    def to_dict(self) -> dict:
        return {
            "model": self.model_label,
            "modelName": self.model_name,
            "table": self.table_name,
            "resource": self.resource,
            "policy": self.policy,
            "rows": self.row_count,
            "bytes": self.byte_size,
            "sha256": self.sha256,
            "file": self.file_name,
        }


class SystemGate(models.Model):
    """全局工作门禁（单例，pk 恒为 1）。

    RUNNING    —— 正常，读写放行
    PAUSED     —— 强制暂停：所有写请求被拒（HTTP 423），读放行；客户端弹全屏遮罩
    RESTORING  —— 回退中：读写全部被拒（仅白名单端点可用），客户端弹「回退中」遮罩

    `data_version` 在每次成功回退后 +1，客户端据此判断本地缓存是否已过期。
    """

    MODE_RUNNING = "RUNNING"
    MODE_PAUSED = "PAUSED"
    MODE_RESTORING = "RESTORING"
    MODE_CHOICES = [
        (MODE_RUNNING, MODE_RUNNING),
        (MODE_PAUSED, MODE_PAUSED),
        (MODE_RESTORING, MODE_RESTORING),
    ]

    SENTINEL_ID = 1

    id = models.IntegerField(primary_key=True, default=SENTINEL_ID, editable=False)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_RUNNING)
    reason = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField(blank=True, default="")
    operator_id = models.IntegerField(null=True, blank=True)
    operator_name = models.CharField(max_length=128, blank=True, default="")
    since = models.DateTimeField(null=True, blank=True)
    #: 0 表示不自动恢复；>0 表示 TTL 秒数（到期自动恢复为 RUNNING）
    ttl_seconds = models.IntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    #: 回退中时的目标快照
    active_snapshot_id = models.IntegerField(null=True, blank=True)
    progress = models.CharField(max_length=255, blank=True, default="")
    #: 数据版本号：每次成功回退 +1（客户端用它判断是否需要整体重载）
    data_version = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "system_gate"

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"SystemGate({self.mode})"


class SnapshotPolicy(models.Model):
    """自动快照与保留策略（单例，pk 恒为 1）。"""

    SENTINEL_ID = 1

    id = models.IntegerField(primary_key=True, default=SENTINEL_ID, editable=False)
    auto_enabled = models.BooleanField(default=False)
    auto_interval_minutes = models.IntegerField(default=30)
    auto_scope = models.CharField(max_length=16, default="system")
    #: 保留最近 N 份（0 表示不按数量清理）
    keep_last = models.IntegerField(default=20)
    #: 保留最近 N 天（0 表示不按时间清理）
    keep_days = models.IntegerField(default=7)
    #: 自动快照是否包含上传文件（文件较大，默认关闭）
    auto_include_files = models.BooleanField(default=False)
    last_auto_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "snapshot_policy"

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"SnapshotPolicy(auto={self.auto_enabled})"

    def to_dict(self) -> dict:
        return {
            "autoEnabled": self.auto_enabled,
            "autoIntervalMinutes": self.auto_interval_minutes,
            "autoScope": self.auto_scope,
            "keepLast": self.keep_last,
            "keepDays": self.keep_days,
            "autoIncludeFiles": self.auto_include_files,
            "lastAutoAt": self.last_auto_at.isoformat() if self.last_auto_at else None,
        }
