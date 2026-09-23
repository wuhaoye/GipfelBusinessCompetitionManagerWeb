"""财年更迭信号（主项目对外暴露的「财年变化」通知点）。

背景
----
财年（FiscalYear）是比赛的时间轴，也是「按财年结账」类外部工具（如
`contract_watcher`）最关心的状态：财年由 ACTIVE 变为 CLOSED（FY_END）或出现新的
ACTIVE 财年（FY_START）时，外部工具需要把本财年未结的账一次性落盘。

前端通过 Socket.IO 的 `fiscal-year:changed` 事件即时同步（见 views._broadcast_fiscal_year），
但**外部进程**（纯 HTTP 客户端）收不到 Socket.IO 事件，因此这里同时提供两条通道：

1. **进程内信号**（本模块）：`fiscal_year_changed` / `fiscal_year_started` /
   `fiscal_year_ended`，任何进程内消费者都可 connect；
2. **可轮询的持久信号**：`GET /api/competitions/:id/fiscal-years` 支持 `updatedAfter`
   增量协议（见 views.FiscalYearListView）。财年的任意写入都会刷新 `updated_at`，
   轮询方按游标拉取后与本地状态比对，即可推导出 FY_START / FY_END，不会因为
   「轮询间隔内错过了信号」而丢事件。

信号 kwargs
-----------
    fiscal_year     FiscalYear 实例（删除场景为删除前的实例）
    competition_id  int            所属比赛
    fiscal_year_id  int | None     财年主键（删除时仍取删除前的值）
    year            int | None     财年年份
    status          str | None     变更后状态；删除时为 None
    prev_status     str | None     变更前状态；新建时为 None，无法判定时也为 None
    action          str            created / updated / deleted
    transition      str | None     FY_START / FY_END / None

transition 判定口径
-------------------
- FY_START：新建且状态为 ACTIVE，或由「非 ACTIVE」变为 ACTIVE；
- FY_END  ：由「非 CLOSED」变为 CLOSED；
- 其它变更（如仅改年份）与删除均为 None（删除事件带 prev_status，消费者可自行判断）。

注意：`QuerySet.update()` 不触发 Django 模型信号（框架既有行为），这类写入不会发信号，
但 `updated_at` 仍会变化，轮询通道不受影响。
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import Signal

logger = logging.getLogger("gipfel")

# 财年发生变化（新建 / 更新 / 删除）——最通用的一个
fiscal_year_changed = Signal()
# 语义化子信号：财年开始 / 财年结束（transition 为 FY_START / FY_END 时额外发送）
fiscal_year_started = Signal()
fiscal_year_ended = Signal()


def resolve_transition(prev_status: str | None, status: str | None) -> str | None:
    """按「状态迁移」推导更迭语义；无迁移返回 None。

    单独抽出为纯函数，便于测试与消费者复用同一口径。
    """
    if status is None:
        return None
    if status == "ACTIVE" and prev_status != "ACTIVE":
        return "FY_START"
    if status == "CLOSED" and prev_status != "CLOSED":
        return "FY_END"
    return None


def notify_fiscal_year_changed(
    fiscal_year=None,
    *,
    action: str,
    prev_status: str | None = None,
    competition_id=None,
    fiscal_year_id=None,
    year=None,
    status=None,
    instance=None,
):
    """发送财年更迭信号（供模型钩子与其它写路径调用）。

    参数既可传实例（自动取字段），也可显式传值（删除后实例字段可能已不可用）。
    `instance` 是 `fiscal_year` 的兼容别名。
    """
    fy = fiscal_year if fiscal_year is not None else instance
    if fy is not None:
        competition_id = competition_id if competition_id is not None else getattr(fy, "competition_id", None)
        fiscal_year_id = fiscal_year_id if fiscal_year_id is not None else getattr(fy, "pk", None)
        year = year if year is not None else getattr(fy, "year", None)
        status = status if status is not None else getattr(fy, "status", None)
        # FiscalYear.save() 覆写会在保存前记录原状态，供此处的迁移判定使用
        if prev_status is None:
            prev_status = getattr(fy, "_fy_prev_status", None)

    transition = resolve_transition(prev_status, status)
    kwargs = {
        "fiscal_year": fy,
        "competition_id": competition_id,
        "fiscal_year_id": fiscal_year_id,
        "year": year,
        "status": status,
        "prev_status": prev_status,
        "action": action,
        "transition": transition,
    }
    if transition:
        logger.info(
            "[fiscal-year] %s：比赛 #%s 财年 %s（id=%s）%s → %s",
            transition, competition_id, year, fiscal_year_id, prev_status or "新建", status,
        )
    fiscal_year_changed.send(sender=None, **kwargs)
    if transition == "FY_START":
        fiscal_year_started.send(sender=None, **kwargs)
    elif transition == "FY_END":
        fiscal_year_ended.send(sender=None, **kwargs)
    return transition


# ==================== 模型钩子：覆盖所有写路径 ====================
def _on_fiscal_year_post_save(sender, instance, created, raw=False, **kwargs):
    if raw:  # loaddata / fixture 场景跳过
        return
    notify_fiscal_year_changed(
        instance,
        action="created" if created else "updated",
    )


def _on_fiscal_year_pre_delete(sender, instance, **kwargs):
    """删除前把主键与状态记在实例上。

    Django 的 `Model.delete()` 会在 `post_delete` 之后把 `instance.pk` 置空，
    为了在 post_delete 里仍能给出可用的 `fiscal_year_id`，这里先留一份快照。
    """
    instance._fy_deleted_pk = instance.pk
    instance._fy_deleted_status = instance.status


def _on_fiscal_year_post_delete(sender, instance, **kwargs):
    notify_fiscal_year_changed(
        instance,
        action="deleted",
        status=getattr(instance, "status", None),
        prev_status=getattr(instance, "status", None),
        fiscal_year_id=getattr(instance, "_fy_deleted_pk", None),
    )


def connect_fiscal_year_signals() -> None:
    """在 CompetitionsConfig.ready() 中调用：把财年模型接入上面的信号分发。"""
    from .models import FiscalYear

    post_save.connect(
        _on_fiscal_year_post_save, sender=FiscalYear, weak=False,
        dispatch_uid="competitions.fiscal_year.post_save",
    )
    pre_delete.connect(
        _on_fiscal_year_pre_delete, sender=FiscalYear, weak=False,
        dispatch_uid="competitions.fiscal_year.pre_delete",
    )
    post_delete.connect(
        _on_fiscal_year_post_delete, sender=FiscalYear, weak=False,
        dispatch_uid="competitions.fiscal_year.post_delete",
    )
    logger.debug("fiscal year signals connected")
