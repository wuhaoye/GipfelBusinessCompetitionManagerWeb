# -*- coding: utf-8 -*-
"""FY-01 验证：财年更迭信号 + 财年列表增量轮询（主项目为外部监听程序提供的两条通道）。

背景：`contract_watcher` 是纯 HTTP 轮询的独立进程，收不到 Socket.IO 的
`fiscal-year:changed`；「财年结束时把本财年未结的账落盘」要求主项目提供可靠的
财年更迭通知。本文件验证：

1. `apps.competitions.signals`：新建财年（ACTIVE）→ FY_START；ACTIVE→CLOSED → FY_END；
   CLOSED→ACTIVE → FY_START；仅改年份 → 无更迭；删除 → action=deleted 且带出主键。
2. 信号由**模型钩子**统一分发，因此 ORM / 管理后台 / 归档导入等所有写路径都能触发
   （不依赖 views 里那一段 prev_status 比对）。
3. `GET /api/competitions/:cid/fiscal-years?updatedAfter=<ISO>` 增量协议可用：
   返回 `incremental=true` / `serverTime` / `existingIds`，且不破坏原分页响应。

用法（backend 目录）：
    .\\.venv\\Scripts\\python.exe manage.py test tests_fix_verify.test_fy01_signals -v 2
"""
from __future__ import annotations

import json

from django.test import Client, TestCase

from apps.auth.authentication import create_jwt
from apps.competitions.models import Competition, FiscalYear
from apps.competitions.signals import (
    fiscal_year_changed,
    fiscal_year_ended,
    fiscal_year_started,
    resolve_transition,
)
from apps.users.models import User


class SignalRecorder:
    """把三个财年信号记录成列表，便于断言（用完 disconnect）。"""

    def __init__(self):
        self.changed: list[dict] = []
        self.started: list[dict] = []
        self.ended: list[dict] = []
        fiscal_year_changed.connect(self._on_changed, weak=False)
        fiscal_year_started.connect(self._on_started, weak=False)
        fiscal_year_ended.connect(self._on_ended, weak=False)

    def _on_changed(self, sender, **kwargs):
        self.changed.append(kwargs)

    def _on_started(self, sender, **kwargs):
        self.started.append(kwargs)

    def _on_ended(self, sender, **kwargs):
        self.ended.append(kwargs)

    def disconnect(self):
        fiscal_year_changed.disconnect(self._on_changed)
        fiscal_year_started.disconnect(self._on_started)
        fiscal_year_ended.disconnect(self._on_ended)


class ResolveTransitionTests(TestCase):
    """纯函数口径：与 views 里的定时器触发口径一致。"""

    def test_start_when_created_active(self):
        self.assertEqual(resolve_transition(None, "ACTIVE"), "FY_START")

    def test_start_when_closed_to_active(self):
        self.assertEqual(resolve_transition("CLOSED", "ACTIVE"), "FY_START")

    def test_no_start_when_already_active(self):
        self.assertIsNone(resolve_transition("ACTIVE", "ACTIVE"))

    def test_end_when_active_to_closed(self):
        self.assertEqual(resolve_transition("ACTIVE", "CLOSED"), "FY_END")

    def test_no_end_when_already_closed(self):
        self.assertIsNone(resolve_transition("CLOSED", "CLOSED"))

    def test_none_when_status_missing(self):
        self.assertIsNone(resolve_transition("ACTIVE", None))


class FiscalYearSignalTests(TestCase):
    def setUp(self):
        self.comp = Competition.objects.create(name="fy01-competition")
        self.recorder = SignalRecorder()
        self.addCleanup(self.recorder.disconnect)

    def test_create_active_emits_start(self):
        fy = FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        self.assertEqual(len(self.recorder.changed), 1)
        ev = self.recorder.changed[0]
        self.assertEqual(ev["action"], "created")
        self.assertEqual(ev["transition"], "FY_START")
        self.assertEqual(ev["competition_id"], self.comp.id)
        self.assertEqual(ev["fiscal_year_id"], fy.id)
        self.assertEqual(ev["year"], 2026)
        self.assertEqual(ev["status"], "ACTIVE")
        self.assertEqual(len(self.recorder.started), 1, "应额外发送 fiscal_year_started")

    def test_create_closed_emits_end_not_start(self):
        """直接建一个 CLOSED 财年（补历史）不应被当成 FY_START。"""
        FiscalYear.objects.create(competition=self.comp, year=2025, status="CLOSED")
        ev = self.recorder.changed[0]
        self.assertEqual(ev["transition"], "FY_END")
        self.assertEqual(len(self.recorder.ended), 1)
        self.assertEqual(self.recorder.started, [])

    def test_close_active_year_emits_end(self):
        fy = FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        self.recorder.changed.clear()
        self.recorder.started.clear()
        self.recorder.ended.clear()

        fy.status = "CLOSED"
        fy.save(update_fields=["status", "updated_at"])

        self.assertEqual(len(self.recorder.changed), 1, "CLOSED 保存应发一次信号")
        ev = self.recorder.changed[0]
        self.assertEqual(ev["action"], "updated")
        self.assertEqual(ev["transition"], "FY_END")
        self.assertEqual(ev["prev_status"], "ACTIVE")
        self.assertEqual(ev["status"], "CLOSED")
        self.assertEqual(len(self.recorder.ended), 1, "应额外发送 fiscal_year_ended")

    def test_reopen_closed_year_emits_start(self):
        fy = FiscalYear.objects.create(competition=self.comp, year=2026, status="CLOSED")
        self.recorder.changed.clear()
        self.recorder.started.clear()
        self.recorder.ended.clear()

        fy.status = "ACTIVE"
        fy.save(update_fields=["status", "updated_at"])

        ev = self.recorder.changed[0]
        self.assertEqual(ev["transition"], "FY_START")
        self.assertEqual(ev["prev_status"], "CLOSED")
        self.assertEqual(len(self.recorder.started), 1)

    def test_year_only_change_is_not_a_transition(self):
        fy = FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        self.recorder.changed.clear()
        self.recorder.started.clear()
        self.recorder.ended.clear()
        fy.year = 2027
        fy.save(update_fields=["year", "updated_at"])
        ev = self.recorder.changed[0]
        self.assertEqual(ev["action"], "updated")
        self.assertIsNone(ev["transition"], "只改年份不应被判为财年更迭")
        self.assertEqual(self.recorder.started, [])
        self.assertEqual(self.recorder.ended, [])

    def test_delete_reports_id_and_no_transition(self):
        fy = FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        fy_id = fy.id
        self.recorder.changed.clear()
        self.recorder.started.clear()
        self.recorder.ended.clear()

        fy.delete()

        self.assertEqual(len(self.recorder.changed), 1)
        ev = self.recorder.changed[0]
        self.assertEqual(ev["action"], "deleted")
        self.assertEqual(ev["fiscal_year_id"], fy_id, "删除事件必须带出主键（供轮询方对账）")
        self.assertIsNone(ev["transition"])

    def test_signal_does_not_break_when_no_receiver(self):
        """无接收者时写入照常成功（信号是通知，不参与业务事务）。"""
        self.recorder.disconnect()
        FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        self.assertEqual(FiscalYear.objects.filter(competition=self.comp).count(), 1)


class FiscalYearIncrementalApiTests(TestCase):
    """财年列表的 updatedAfter 增量协议（contract_watcher 的轮询通道）。"""

    def setUp(self):
        self.comp = Competition.objects.create(name="fy01-api")
        self.admin = User.objects.create_user(
            username="fy01-admin", password="AdminPw!123", role="SUPER_ADMIN"
        )
        self.token = create_jwt(self.admin)
        self.client = Client()
        self.url = f"/api/competitions/{self.comp.id}/fiscal-years"

    def _get(self, query: str = ""):
        return self.client.get(
            self.url + query, HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )

    def test_full_list_still_paginated(self):
        FiscalYear.objects.create(competition=self.comp, year=2025, status="CLOSED")
        FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        resp = self._get()
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()["data"]
        self.assertIn("items", body)
        self.assertEqual(body["total"], 2)
        self.assertNotIn("incremental", body, "无 updatedAfter 时应保持原分页响应")

    def test_incremental_returns_changed_rows_and_server_time(self):
        FiscalYear.objects.create(competition=self.comp, year=2025, status="CLOSED")
        cursor = "2000-01-01T00:00:00Z"
        resp = self._get(f"?updatedAfter={cursor}")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()["data"]
        self.assertTrue(body.get("incremental"), body)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["year"], 2025)
        self.assertTrue(body.get("serverTime"), "增量响应必须带 serverTime 作为下一次游标")
        self.assertEqual(len(body["existingIds"]), 1)

    def test_incremental_catches_transition_and_supports_deleted_ids(self):
        fy = FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        cursor = "2000-01-01T00:00:00Z"
        body = self._get(f"?updatedAfter={cursor}").json()["data"]
        self.assertEqual(body["items"][0]["status"], "ACTIVE")

        # 财年结束：状态迁移 + updated_at 刷新 ⇒ 下一轮增量仍能拉到
        cursor = body["serverTime"]
        fy.status = "CLOSED"
        fy.save(update_fields=["status", "updated_at"])
        body2 = self._get(f"?updatedAfter={cursor}").json()["data"]
        self.assertEqual(len(body2["items"]), 1, "财年结束时行必须出现在增量结果里")
        self.assertEqual(body2["items"][0]["status"], "CLOSED")

        # 删除后按 previousIds 得到 deletedIds，轮询方可对账
        previous = ",".join(str(i) for i in body2["existingIds"])
        fy_id = fy.id
        fy.delete()
        body3 = self._get(
            f"?updatedAfter={body2['serverTime']}&previousIds={previous}"
        ).json()["data"]
        self.assertEqual(body3["items"], [])
        self.assertEqual(body3["deletedIds"], [fy_id])

    def test_non_super_admin_cannot_read_other_competition(self):
        other = Competition.objects.create(name="fy01-other")
        outsider = User.objects.create_user(
            username="fy01-outsider", password="Pw!123456", role="COMPETITION_ADMIN",
            competition=self.comp,
        )
        resp = self.client.get(
            f"/api/competitions/{other.id}/fiscal-years",
            HTTP_AUTHORIZATION=f"Bearer {create_jwt(outsider)}",
        )
        self.assertEqual(resp.status_code, 404, resp.content)


class FiscalYearTimerIntegrationTests(TestCase):
    """回归：原有「财年定时器」触发口径不得因信号改动而改变。"""

    def setUp(self):
        self.comp = Competition.objects.create(name="fy01-timer")
        self.admin = User.objects.create_user(
            username="fy01-timer-admin", password="AdminPw!123", role="SUPER_ADMIN"
        )
        self.client = Client()
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {create_jwt(self.admin)}"}

    def test_create_via_api_still_triggers_fy_start_timer(self):
        calls: list[tuple] = []
        from apps.competitions import views as comp_views

        original = comp_views._apply_fiscal_year_timer
        comp_views._apply_fiscal_year_timer = lambda cid, trigger: calls.append((cid, trigger))
        try:
            resp = self.client.post(
                f"/api/competitions/{self.comp.id}/fiscal-years",
                data=json.dumps({"year": 2026}),
                content_type="application/json",
                **self.headers,
            )
        finally:
            comp_views._apply_fiscal_year_timer = original
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(calls, [(self.comp.id, "FY_START")])

    def test_patch_to_closed_still_triggers_fy_end_timer(self):
        fy = FiscalYear.objects.create(competition=self.comp, year=2026, status="ACTIVE")
        calls: list[tuple] = []
        from apps.competitions import views as comp_views

        original = comp_views._apply_fiscal_year_timer
        comp_views._apply_fiscal_year_timer = lambda cid, trigger: calls.append((cid, trigger))
        try:
            resp = self.client.patch(
                f"/api/competitions/fiscal-years/{fy.id}",
                data=json.dumps({"status": "CLOSED"}),
                content_type="application/json",
                **self.headers,
            )
        finally:
            comp_views._apply_fiscal_year_timer = original
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(calls, [(self.comp.id, "FY_END")])
