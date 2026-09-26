"""
URL 路由：聚合所有 REST 模块 + 静态资源 + 健康检查 / 版本。

所有业务路由前缀 /api，与原 NestJS app.setGlobalPrefix('api') 一致。
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as static_serve

from apps.auth.views import HealthView, VersionView
from apps.realtime.internal import internal_emit_view

urlpatterns = [
    # C1-a 内部实时转发端点（hub 进程受理 WSGI 端转发）：仅回环 + 共享令牌鉴权，
    # 路径固定为 settings.REALTIME_INTERNAL_PATH；nginx 不代理 /_internal/，公网不可达。
    path(
        getattr(settings, "REALTIME_INTERNAL_PATH", "/_internal/realtime/emit").lstrip("/"),
        internal_emit_view,
        name="realtime-internal-emit",
    ),
    # 管理后台（Django admin）：仅用于临时排查/修数，业务管理仍走前端 Vue 界面
    path("admin/", admin.site.urls),
    # 健康检查与版本（无鉴权，对应原 health.controller / version.controller）
    path("api/health", HealthView.as_view(), name="health"),
    path("api/version", VersionView.as_view(), name="version"),
    # 认证与用户
    path("api/auth/", include("apps.auth.urls")),
    # users 挂在 api/ 下（与 competitions 一致），子路由非空，避免 POST /api/users 触发尾随斜杠重定向
    path("api/", include("apps.users.urls")),
    # 业务模块（保持原 NestJS Controller 前缀）
    path("api/", include("apps.competitions.urls")),
    path("api/", include("apps.materials.urls")),
    path("api/", include("apps.parts.urls")),
    path("api/", include("apps.products.urls")),
    path("api/", include("apps.tech_tree.urls")),
    path("api/", include("apps.maps.urls")),
    path("api/", include("apps.infrastructures.urls")),
    path("api/", include("apps.fuels.urls")),
    path("api/", include("apps.vehicles.urls")),
    path("api/", include("apps.warehouses.urls")),
    path("api/", include("apps.production_lines.urls")),
    path("api/", include("apps.industry_types.urls")),
    path("api/", include("apps.companies.urls")),
    path("api/", include("apps.company_fields.urls")),
    path("api/", include("apps.contracts.urls")),
    path("api/", include("apps.regions.urls")),
    path("api/", include("apps.consumer_demands.urls")),
    path("api/", include("apps.messages.urls")),
    path("api/", include("apps.stock.urls")),
    path("api/", include("apps.files.urls")),
    path("api/", include("apps.audit.urls")),
    path("api/", include("apps.announcements.urls")),
    path("api/", include("apps.widget_packages.urls")),
    # 比赛准备总览与归档导出（只读；需 competition:manage）
    path("api/", include("apps.preparation.urls")),
    # 快照与回退（全量记录 + 强制暂停 + 及时回退；需 snapshot:view/manage/restore，均超管专属）
    path("api/", include("apps.snapshots.urls")),
]

# /uploads 静态托管（CORP cross-origin 由中间件设置）
# 注意：django.conf.urls.static.static() 在 DEBUG=False 时不挂载（生产静默 404，
# 症状：地图背景图上传成功但加载失败），故与 /static/ 一样无条件以 re_path 托管。
urlpatterns += [
    re_path(r"^uploads/(?P<path>.*)$", static_serve, {"document_root": settings.MEDIA_ROOT}),
]

# 管理后台静态资源（/admin 样式与脚本）。DEBUG=False 时 django.conf.urls.static 不挂载，
# 故此处无条件以 re_path 托管 STATIC_ROOT，仅匹配 /static/，不影响 /api、/uploads、/socket.io。
urlpatterns += [
    re_path(r"^static/(?P<path>.*)$", static_serve, {"document_root": settings.STATIC_ROOT}),
]
