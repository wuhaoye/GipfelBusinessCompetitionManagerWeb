"""Django Admin 集成：快照/门禁/策略的可视化查看（只读 + 少量操作）。

⚠️ 与项目其它模块一致：后台直接改库会绕过业务校验，快照元数据仅供运维排查。
"""
from django.contrib import admin

from .models import Snapshot, SnapshotPolicy, SnapshotTable, SystemGate


class SnapshotTableInline(admin.TabularInline):
    model = SnapshotTable
    extra = 0
    can_delete = False
    readonly_fields = (
        "model_label",
        "table_name",
        "policy",
        "row_count",
        "byte_size",
        "sha256",
        "file_name",
    )

    def has_add_permission(self, request, obj=None):  # pragma: no cover - admin
        return False


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "label",
        "scope",
        "competition_id",
        "kind",
        "status",
        "row_count",
        "byte_size",
        "locked",
        "created_by_name",
        "created_at",
    )
    list_filter = ("scope", "kind", "status", "locked")
    search_fields = ("label", "note", "competition_name")
    readonly_fields = ("created_at", "restored_at", "manifest_sha256", "storage_path")
    inlines = [SnapshotTableInline]


@admin.register(SystemGate)
class SystemGateAdmin(admin.ModelAdmin):
    list_display = ("id", "mode", "reason", "operator_name", "since", "data_version", "updated_at")
    readonly_fields = ("updated_at",)


@admin.register(SnapshotPolicy)
class SnapshotPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "auto_enabled",
        "auto_interval_minutes",
        "auto_scope",
        "keep_last",
        "keep_days",
        "last_auto_at",
    )
