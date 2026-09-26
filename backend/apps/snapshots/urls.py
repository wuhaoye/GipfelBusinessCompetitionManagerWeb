"""快照系统路由（挂在 /api 前缀下）。

注意顺序：静态子路径（status / gate / policy / cleanup）必须写在
`<int:pk>` 之前，否则会被主键路由抢先匹配。
"""
from django.urls import path

from .views import (
    SnapshotCleanupAPIView,
    SnapshotDetailAPIView,
    SnapshotDiffAPIView,
    SnapshotDownloadAPIView,
    SnapshotGateAPIView,
    SnapshotGatePauseAPIView,
    SnapshotGateResumeAPIView,
    SnapshotListAPIView,
    SnapshotLockAPIView,
    SnapshotPolicyAPIView,
    SnapshotRestoreAPIView,
    SnapshotStatusAPIView,
    SnapshotVerifyAPIView,
)

urlpatterns = [
    path("snapshots", SnapshotListAPIView.as_view(), name="snapshots-collection"),
    path("snapshots/status", SnapshotStatusAPIView.as_view(), name="snapshots-status"),
    path("snapshots/gate", SnapshotGateAPIView.as_view(), name="snapshots-gate"),
    path(
        "snapshots/gate/pause",
        SnapshotGatePauseAPIView.as_view(),
        name="snapshots-gate-pause",
    ),
    path(
        "snapshots/gate/resume",
        SnapshotGateResumeAPIView.as_view(),
        name="snapshots-gate-resume",
    ),
    path("snapshots/policy", SnapshotPolicyAPIView.as_view(), name="snapshots-policy"),
    path(
        "snapshots/cleanup",
        SnapshotCleanupAPIView.as_view(),
        name="snapshots-cleanup",
    ),
    path("snapshots/<int:pk>", SnapshotDetailAPIView.as_view(), name="snapshots-item"),
    path(
        "snapshots/<int:pk>/diff",
        SnapshotDiffAPIView.as_view(),
        name="snapshots-diff",
    ),
    path(
        "snapshots/<int:pk>/verify",
        SnapshotVerifyAPIView.as_view(),
        name="snapshots-verify",
    ),
    path(
        "snapshots/<int:pk>/download",
        SnapshotDownloadAPIView.as_view(),
        name="snapshots-download",
    ),
    path(
        "snapshots/<int:pk>/restore",
        SnapshotRestoreAPIView.as_view(),
        name="snapshots-restore",
    ),
    path(
        "snapshots/<int:pk>/lock",
        SnapshotLockAPIView.as_view(),
        name="snapshots-lock",
    ),
]
