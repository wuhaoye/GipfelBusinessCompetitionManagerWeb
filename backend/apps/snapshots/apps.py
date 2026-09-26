from django.apps import AppConfig


class SnapshotsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.snapshots"
    label = "snapshots"
    verbose_name = "快照与回退"
