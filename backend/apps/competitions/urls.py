"""比赛与财年路由：挂在 /api 前缀下（子路由非空、无尾随斜杠）。

由 backend.urls 以 path("api/", include("apps.competitions.urls")) 引入，
故本文件路由前缀为 api/。前端契约：
- GET    /api/competitions                          列表
- POST   /api/competitions                          创建
- GET    /api/competitions/:id                      详情
- PATCH  /api/competitions/:id                      更新
- DELETE /api/competitions/:id                      删除
- GET    /api/competitions/:id/fiscal-years         财年列表（分页；带 updatedAfter 时走增量协议，
                                                    外部监听程序据此轮询「财年更迭」信号）
- POST   /api/competitions/:id/fiscal-years         创建财年
- PATCH  /api/competitions/fiscal-years/:id         更新财年
- DELETE /api/competitions/fiscal-years/:id          删除财年

财年变化还会通过 apps.competitions.signals 发出进程内信号
（fiscal_year_changed / fiscal_year_started / fiscal_year_ended），
并沿用 Socket.IO 的 fiscal-year:changed 广播。
"""
from django.urls import path

from .views import (
    CompetitionCollectionAPIView,
    CompetitionItemAPIView,
    FiscalYearCollectionAPIView,
    FiscalYearItemAPIView,
)

app_name = "competitions"

urlpatterns = [
    path(
        "competitions",
        CompetitionCollectionAPIView.as_view(),
        name="competition-collection",
    ),
    path(
        "competitions/<int:pk>",
        CompetitionItemAPIView.as_view(),
        name="competition-item",
    ),
    path(
        "competitions/<int:cid>/fiscal-years",
        FiscalYearCollectionAPIView.as_view(),
        name="fiscal-year-collection",
    ),
    path(
        "competitions/fiscal-years/<int:pk>",
        FiscalYearItemAPIView.as_view(),
        name="fiscal-year-item",
    ),
]
