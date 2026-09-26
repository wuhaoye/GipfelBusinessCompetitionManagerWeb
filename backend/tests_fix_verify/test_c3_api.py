# -*- coding: utf-8 -*-
"""C3 验收（后端）：/auth/me 条件请求（ETag + light）与 /company-fields 批量端点。

契约真源：docs/运维约束整改设计说明.md §4.1 / §4.2（**接口与数据契约冻结**，
前端 frontend-c3 按同一字面量对接，本文件即后端侧的契约回归）。
基线：分支 bugfix-merged，改造前 `manage.py test apps tests_fix_verify` = Ran 484 tests OK；
本文件为 C3 新增项，不改动任何既有用例。

覆盖：
A. GET /api/auth/me（§4.1）
   1. 无参响应体逐字段与改前一致（键集 + 取值 + 信封）；
   2. ETag 稳定；If-None-Match 命中 → 304 空体 + 同 ETag；不命中 / 资料变更 → 200；
   3. ?light=1 → data 仅 {id, tokenVersion, isActive, mustChangePassword}；
   4. 开关 AUTH_ME_CONDITIONAL_ENABLED=false → 不写 ETag / 不 304 / 忽略 light（改前行为）；
   5. 401 语义回归：匿名 / 过期 / 被顶号 / 禁用 / 强制改密门禁，
      **带 If-None-Match 也仍 401**（心跳靠它感知顶号，绝不能被 304 掩盖）。
B. GET /api/company-fields?companyIds=…（§4.2，新增）
   6. 全量与单公司端点 fields **逐字段相同**（含无产业类型公司）；
   7. 增量：仅回传变更字段 + existingIds（含新增字段定义）；previousIds → deletedIds；
   8. 权限与单点完全一致：无权 / 跨比赛 / 不在 viewCompanyScopes → missing 且零泄露；
   9. 非法 companyIds / 超 50 家 → 400；50 家边界 → 200；
  10. 旧端点（GET/PUT /company-fields/{cid}、PUT /company-fields/{cid}/{fieldId}）行为不变；
  11. 路由互不冲突（/api/company-fields 与 /api/company-fields/<id>）。
"""
from __future__ import annotations

import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import resolve
from django.utils import timezone

from apps.auth.authentication import create_jwt
from apps.companies.models import Company, CompanyFieldValue
from apps.competitions.models import Competition
from apps.company_fields.views import (
    CompanyFieldItemView,
    CompanyFieldsBatchView,
    CompanyFieldsView,
)
from apps.industry_types.models import IndustryField, IndustryType
from apps.users.models import User

def _user(username: str, role: str, **extra) -> User:
    """测试账号工厂：直接落 password_hash，不跑 bcrypt。

    本文件的鉴权全部走 create_jwt（不经过 /auth/login），无需真实口令哈希；
    而 cost=12 的 bcrypt 每个账号约 0.3s，32 个用例 × 4 账号会把模块拖慢近一分钟。
    """
    user = User(
        username=username, role=role, password_hash="!c3-not-a-bcrypt-hash!", **extra
    )
    user.save()
    return user


# /auth/me 完整资料的字段集（改造前 serialize_user 的逐字段契约，不得增删）
ME_FULL_KEYS = {
    "id",
    "username",
    "role",
    "displayName",
    "mustChangePassword",
    "isActive",
    "permissions",
    "companyScopes",
    "viewCompanyScopes",
    "contractViewCompanyScopes",
    "stockCompanyScopes",
    "competitionId",
}
# /auth/me?light=1 的轻响应字段集（§4.1）
ME_LIGHT_KEYS = {"id", "tokenVersion", "isActive", "mustChangePassword"}
# 字段元素键集（单公司端点与批量端点共用 _field_to_dict，不得因批量端点而变形）
FIELD_ITEM_KEYS = {
    "industryFieldId",
    "fieldKey",
    "name",
    "fieldType",
    "config",
    "defaultValue",
    "value",
    "version",
    "visible",
    "isCalculated",
    "sortOrder",
    "companyName",
}
# 批量端点上限（apps/company_fields/views.py::_MAX_BATCH_COMPANIES）
MAX_BATCH = 50


class _C3Base(TestCase):
    """公共夹具：两个比赛、两个产业类型、跨比赛机密数据、四类账号。"""

    def setUp(self):
        self.comp_a = Competition.objects.create(name="C3-比赛A")
        self.comp_b = Competition.objects.create(name="C3-比赛B")

        self.itype_a = IndustryType.objects.create(name="C3-产业A", code=93001)
        self.f_num = IndustryField.objects.create(
            industry_type=self.itype_a,
            name="产能",
            field_key="capacity",
            field_type="NUMBER",
            sort_order=1,
            visible=True,
        )
        self.f_hidden = IndustryField.objects.create(
            industry_type=self.itype_a,
            name="隐藏项",
            field_key="hidden_note",
            field_type="STRING",
            sort_order=2,
            visible=False,
        )

        # 跨比赛的「机密」产业/字段/取值：用于证明无权公司的数据一个字节都不出现在响应里
        self.itype_b = IndustryType.objects.create(name="C3-产业B", code=93002)
        self.f_secret = IndustryField.objects.create(
            industry_type=self.itype_b,
            name="B机密字段",
            field_key="secret_b_key",
            field_type="STRING",
            sort_order=1,
            visible=True,
        )

        self.company_a1 = Company.objects.create(
            name="C3-公司A1", competition=self.comp_a, industry_type=self.itype_a
        )
        self.company_a2 = Company.objects.create(
            name="C3-公司A2", competition=self.comp_a, industry_type=self.itype_a
        )
        self.company_b1 = Company.objects.create(
            name="C3-公司B1", competition=self.comp_b, industry_type=self.itype_b
        )
        self.company_no_type = Company.objects.create(
            name="C3-公司A无产业", competition=self.comp_a, industry_type=None
        )

        CompanyFieldValue.objects.create(
            company=self.company_a1, industry_field=self.f_num, value="100", version=1
        )
        CompanyFieldValue.objects.create(
            company=self.company_a2,
            industry_field=self.f_num,
            value="SCOPE-SECRET-A2",
            version=1,
        )
        CompanyFieldValue.objects.create(
            company=self.company_b1,
            industry_field=self.f_secret,
            value="SECRET-B-VALUE",
            version=1,
        )

        self.player = _user(
            "c3-player", "PLAYER", competition=self.comp_a
        )
        # 只被授权看 A1 一个公司（viewCompanyScopes）
        self.scoped = _user(
            "c3-scoped",
            "PLAYER",
            competition=self.comp_a,
            view_company_scopes=json.dumps([self.company_a1.id]),
        )
        # 显式零权限（permissions="[]"）：company:view 都没有
        self.noperm = _user(
            "c3-noperm", "PLAYER", competition=self.comp_a, permissions="[]"
        )
        self.superadmin = _user("c3-super", "SUPER_ADMIN")

        self.t_player = create_jwt(self.player)
        self.t_scoped = create_jwt(self.scoped)
        self.t_noperm = create_jwt(self.noperm)
        self.t_super = create_jwt(self.superadmin)

    # ---------- 辅助 ----------
    def _h(self, token: str) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _me(self, token: str | None = None, query: str = "", **extra):
        headers = self._h(token if token is not None else self.t_player)
        headers.update(extra)
        return self.client.get(f"/api/auth/me{query}", **headers)

    def _single(self, token: str, company_id: int, query: str = ""):
        return self.client.get(f"/api/company-fields/{company_id}{query}", **self._h(token))

    def _batch(self, token: str, company_ids, **params):
        if isinstance(company_ids, str):
            data = {"companyIds": company_ids}
        else:
            data = {"companyIds": ",".join(str(i) for i in company_ids)}
        data.update(params)
        return self.client.get("/api/company-fields", data, **self._h(token))

    def _data(self, resp) -> dict:
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["code"], 0, body)
        return body["data"]


# ====================================================================
# A. /auth/me 条件请求（§4.1）
# ====================================================================
class AuthMeConditionalTests(_C3Base):
    def test_full_body_fields_unchanged(self):
        """无参响应体逐字段与改前一致；仅新增 ETag 响应头。"""
        resp = self._me()
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["message"], "成功")
        data = body["data"]
        self.assertEqual(set(data.keys()), ME_FULL_KEYS, "完整资料字段集不得增删")
        self.assertEqual(data["id"], self.player.id)
        self.assertEqual(data["username"], "c3-player")
        self.assertEqual(data["role"], "PLAYER")
        self.assertEqual(data["displayName"], None)
        self.assertEqual(data["mustChangePassword"], False)
        self.assertEqual(data["isActive"], True)
        self.assertIn("company:view", data["permissions"])
        self.assertEqual(data["companyScopes"], [])
        self.assertEqual(data["viewCompanyScopes"], [])
        self.assertEqual(data["contractViewCompanyScopes"], [])
        self.assertEqual(data["stockCompanyScopes"], [])
        self.assertEqual(data["competitionId"], self.comp_a.id)
        self.assertTrue(resp.headers.get("ETag"), "无参 /auth/me 必须带 ETag 响应头")

    def test_etag_stable_and_if_none_match_304(self):
        """ETag 稳定；命中 → 304 空体 + 同 ETag；不命中 → 200。"""
        etag = self._me().headers.get("ETag")
        self.assertEqual(self._me().headers.get("ETag"), etag, "状态未变时 ETag 必须稳定")

        hit = self._me(HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(hit.status_code, 304, hit.content)
        self.assertEqual(hit.content, b"", "304 必须空体（不得再包 {code,message,data}）")
        self.assertEqual(hit.headers.get("ETag"), etag, "304 必须带同一 ETag")

        miss = self._me(HTTP_IF_NONE_MATCH='W/"f-deadbeef"')
        self.assertEqual(miss.status_code, 200)
        self.assertEqual(set(miss.json()["data"].keys()), ME_FULL_KEYS)

        # 多值 / 通配（HTTP 语义：* 表示任一表示存在即命中）
        self.assertEqual(
            self._me(HTTP_IF_NONE_MATCH=f'"x", {etag}').status_code, 304
        )
        self.assertEqual(self._me(HTTP_IF_NONE_MATCH="*").status_code, 304)

    def test_etag_changes_when_profile_changes(self):
        """资料变化后 ETag 必须变化，否则客户端会一直拿旧副本。"""
        etag = self._me().headers["ETag"]
        self.player.display_name = "改了个名字"
        self.player.save()
        resp = self._me(HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(resp.status_code, 200, "资料已变更，不得再返回 304")
        self.assertNotEqual(resp.headers["ETag"], etag)
        self.assertEqual(resp.json()["data"]["displayName"], "改了个名字")

    def test_light_payload_and_conditional(self):
        """light=1 → data 仅 4 个字段；信封不变；轻/全两种表示的 ETag 必须不同。"""
        resp = self._me(query="?light=1")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(set(body.keys()), {"code", "message", "data"})
        self.assertEqual((body["code"], body["message"]), (0, "成功"))
        self.assertEqual(set(body["data"].keys()), ME_LIGHT_KEYS, "轻响应字段集不得多/少")
        self.assertEqual(
            body["data"],
            {
                "id": self.player.id,
                "tokenVersion": self.player.token_version,
                "isActive": True,
                "mustChangePassword": False,
            },
        )

        light_etag = resp.headers["ETag"]
        hit = self._me(query="?light=1", HTTP_IF_NONE_MATCH=light_etag)
        self.assertEqual(hit.status_code, 304)
        self.assertEqual(hit.content, b"")
        self.assertEqual(hit.headers["ETag"], light_etag)

        full_etag = self._me().headers["ETag"]
        self.assertNotEqual(
            light_etag, full_etag, "轻响应与完整资料是两种表示，ETag 必须不同"
        )
        # 拿轻响应 ETag 请求完整资料 → 不得误判 304（否则前端会丢字段）
        full = self._me(HTTP_IF_NONE_MATCH=light_etag)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(set(full.json()["data"].keys()), ME_FULL_KEYS)

    def test_light_truthy_variants(self):
        """light 的口径与全局 truthy 一致（1/true/yes/on）。"""
        for value in ("1", "true", "TRUE", "yes", "on"):
            resp = self._me(query=f"?light={value}")
            self.assertEqual(
                set(resp.json()["data"].keys()), ME_LIGHT_KEYS, f"light={value}"
            )
        for value in ("0", "false", ""):
            resp = self._me(query=f"?light={value}")
            self.assertEqual(set(resp.json()["data"].keys()), ME_FULL_KEYS, f"light={value}")

    @override_settings(AUTH_ME_CONDITIONAL_ENABLED=False)
    def test_switch_off_returns_pre_change_behaviour(self):
        """开关关闭 → 不写 ETag、不校验 If-None-Match、忽略 light（完全回到改前）。"""
        first = self._me()
        self.assertEqual(first.status_code, 200)
        self.assertNotIn("ETag", first.headers)

        # 即便带上一个（格式正确的）If-None-Match，也必须 200 完整响应
        second = self._me(HTTP_IF_NONE_MATCH='W/"f-anything"')
        self.assertEqual(second.status_code, 200)
        self.assertNotIn("ETag", second.headers)
        self.assertEqual(set(second.json()["data"].keys()), ME_FULL_KEYS)

        # light 被忽略 → 仍是完整资料
        light = self._me(query="?light=1")
        self.assertEqual(light.status_code, 200)
        self.assertEqual(set(light.json()["data"].keys()), ME_FULL_KEYS)

    # ---------- 401 语义回归 ----------
    def test_401_anonymous_semantics_unchanged(self):
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["code"], 401)
        self.assertEqual(body["message"], "登录已过期，请重新登录")
        self.assertIsNone(body["data"])

    def test_401_expired_token_semantics_unchanged(self):
        resp = self._me(token="not-a-jwt")
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["message"], "登录已过期，请重新登录")
        self.assertEqual(body.get("errorCode"), "expired")

    def test_401_token_version_mismatch_semantics_unchanged(self):
        """顶号：旧 token 立即 401，机器码仍是 token_version_mismatch。"""
        stale = create_jwt(self.player)
        self.player.token_version = (self.player.token_version or 0) + 1
        self.player.save(update_fields=["token_version", "updated_at"])

        resp = self._me(token=stale)
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["message"], "账号已在其他设备登录")
        self.assertEqual(body.get("errorCode"), "token_version_mismatch")

        fresh = self._me(token=create_jwt(self.player))
        self.assertEqual(fresh.status_code, 200)

    def test_401_inactive_semantics_unchanged(self):
        token = create_jwt(self.player)
        self.player.is_active = False
        self.player.save(update_fields=["is_active", "updated_at"])
        resp = self._me(token=token)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["message"], "该账号已被禁用，请联系管理员")
        self.assertEqual(resp.json().get("errorCode"), "inactive")

    def test_must_change_password_gate_unchanged(self):
        """/auth/me 仍在强制改密豁免名单内；其它业务接口仍 401 门禁。"""
        self.player.must_change_password = True
        self.player.save(update_fields=["must_change_password", "updated_at"])
        token = create_jwt(self.player)

        resp = self._me(token=token)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["data"]["mustChangePassword"])
        light = self._me(token=token, query="?light=1")
        self.assertEqual(light.status_code, 200)
        self.assertTrue(light.json()["data"]["mustChangePassword"])

        blocked = self.client.get("/api/companies", **self._h(token))
        self.assertEqual(blocked.status_code, 401)
        self.assertEqual(blocked.json().get("errorCode"), "must_change_password")

    def test_conditional_request_never_masks_401(self):
        """心跳带 If-None-Match 时，被顶号仍必须 401（否则前端永远发现不了顶号）。"""
        etag = self._me().headers["ETag"]
        stale = create_jwt(self.player)
        self.player.token_version = (self.player.token_version or 0) + 1
        self.player.save(update_fields=["token_version", "updated_at"])

        resp = self._me(token=stale, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(resp.status_code, 401, "401 不得被条件请求降级为 304")
        self.assertEqual(resp.json().get("errorCode"), "token_version_mismatch")

        anon = self.client.get("/api/auth/me", HTTP_IF_NONE_MATCH="*")
        self.assertEqual(anon.status_code, 401)


# ====================================================================
# B. /company-fields 批量端点（§4.2）
# ====================================================================
class CompanyFieldsBatchTests(_C3Base):
    def test_batch_full_matches_single_endpoint(self):
        single = self._data(self._single(self.t_player, self.company_a1.id))
        resp = self._batch(self.t_player, [self.company_a1.id, self.company_a2.id])
        data = self._data(resp)
        self.assertEqual(
            set(data.keys()), {"companyIds", "serverTime", "companies", "missing"}
        )
        self.assertEqual(data["companyIds"], [self.company_a1.id, self.company_a2.id])
        self.assertEqual(data["missing"], [])
        self.assertIsInstance(data["serverTime"], str)

        a1 = data["companies"][str(self.company_a1.id)]
        self.assertEqual(a1["industryTypeId"], single["industryTypeId"])
        self.assertEqual(
            a1["fields"], single["fields"], "批量全量必须与单公司端点逐字段一致"
        )
        self.assertEqual(set(a1["fields"][0].keys()), FIELD_ITEM_KEYS)
        self.assertEqual(
            [f["industryFieldId"] for f in a1["fields"]], [self.f_num.id],
            "普通查看者不应看到 visible=false 的字段",
        )
        # 第二个公司同样是完整字段集（各自的值）
        a2 = data["companies"][str(self.company_a2.id)]
        self.assertEqual(a2["fields"][0]["value"], "SCOPE-SECRET-A2")

    def test_batch_full_matches_single_for_company_without_industry_type(self):
        single = self._data(self._single(self.t_player, self.company_no_type.id))
        data = self._data(self._batch(self.t_player, [self.company_no_type.id]))
        payload = data["companies"][str(self.company_no_type.id)]
        self.assertEqual(payload, {"industryTypeId": None, "fields": []})
        self.assertEqual(payload, single, "无产业类型公司的批量结果必须与单点完全相同")

    def test_batch_super_admin_can_read_cross_competition(self):
        data = self._data(
            self._batch(self.t_super, [self.company_a1.id, self.company_b1.id])
        )
        self.assertEqual(data["missing"], [])
        self.assertEqual(
            sorted(int(k) for k in data["companies"]),
            sorted([self.company_a1.id, self.company_b1.id]),
        )
        b1 = data["companies"][str(self.company_b1.id)]
        self.assertEqual([f["industryFieldId"] for f in b1["fields"]], [self.f_secret.id])

    def test_batch_cross_competition_and_unknown_go_missing_without_leak(self):
        """跨比赛 / 不存在的公司进 missing，且其数据一个字节都不出现在响应里。"""
        resp = self._batch(
            self.t_player, [self.company_b1.id, 900999, self.company_a1.id]
        )
        data = self._data(resp)
        self.assertEqual(data["missing"], [self.company_b1.id, 900999])
        self.assertEqual([int(k) for k in data["companies"]], [self.company_a1.id])

        raw = resp.content.decode("utf-8")
        self.assertNotIn("SECRET-B-VALUE", raw, "跨比赛公司的字段值泄露")
        self.assertNotIn("secret_b_key", raw, "跨比赛公司的字段定义泄露")
        self.assertNotIn("C3-公司B1", raw, "跨比赛公司名泄露")
        self.assertNotIn("C3-产业B", raw, "跨比赛产业类型泄露")

    def test_batch_view_scope_missing_matches_single_404(self):
        """viewCompanyScopes 之外的公司：批量进 missing，单点 404 —— 同一判定。"""
        resp = self._batch(self.t_scoped, [self.company_a1.id, self.company_a2.id])
        data = self._data(resp)
        self.assertEqual(data["missing"], [self.company_a2.id])
        self.assertEqual([int(k) for k in data["companies"]], [self.company_a1.id])
        self.assertNotIn("SCOPE-SECRET-A2", resp.content.decode("utf-8"))

        single = self._single(self.t_scoped, self.company_a2.id)
        self.assertEqual(single.status_code, 404)
        self.assertEqual(single.json()["message"], "请求的资源不存在")
        # 未被授权的公司，单点也不返回数据
        self.assertNotIn("SCOPE-SECRET-A2", single.content.decode("utf-8"))

    def test_batch_permission_parity_with_single(self):
        """company:view 缺失 → 403；未认证 → 401；两者与单点端点状态/文案完全相同。"""
        batch = self._batch(self.t_noperm, [self.company_a1.id])
        single = self._single(self.t_noperm, self.company_a1.id)
        self.assertEqual(batch.status_code, 403)
        self.assertEqual(single.status_code, 403)
        self.assertEqual(batch.json()["message"], single.json()["message"])

        anon_batch = self.client.get(
            "/api/company-fields", {"companyIds": str(self.company_a1.id)}
        )
        anon_single = self.client.get(f"/api/company-fields/{self.company_a1.id}")
        self.assertEqual(anon_batch.status_code, 401)
        self.assertEqual(anon_single.status_code, 401)
        self.assertEqual(anon_batch.json()["message"], anon_single.json()["message"])

    def test_batch_include_hidden_parity_with_single(self):
        """includeHidden 的口径与单点一致（仅 SUPER_ADMIN / company:manage 可见隐藏字段）。"""
        for token, label in ((self.t_super, "超管"), (self.t_player, "选手")):
            single = self._data(
                self._single(token, self.company_a1.id, "?includeHidden=1")
            )
            batch = self._data(
                self._batch(token, [self.company_a1.id], includeHidden="1")
            )
            got = batch["companies"][str(self.company_a1.id)]["fields"]
            self.assertEqual(got, single["fields"], f"{label}的 includeHidden 口径不一致")
            ids = [f["industryFieldId"] for f in got]
            if token == self.t_super:
                self.assertIn(self.f_hidden.id, ids, "超管应能看到隐藏字段")
            else:
                self.assertNotIn(self.f_hidden.id, ids, "普通查看者不得看到隐藏字段")

    def test_batch_incremental_returns_only_changed_fields(self):
        baseline = timezone.now()
        changed_at = baseline + timedelta(seconds=5)  # 显式设定，避免时钟粒度导致漏判
        CompanyFieldValue.objects.filter(
            company=self.company_a1, industry_field=self.f_num
        ).update(value="999", version=2, updated_at=changed_at)

        resp = self._batch(
            self.t_player,
            [self.company_a1.id, self.company_a2.id],
            updatedAfter=baseline.isoformat(),
        )
        data = self._data(resp)
        self.assertTrue(data["incremental"])
        self.assertIsInstance(data["serverTime"], str)

        a1 = data["companies"][str(self.company_a1.id)]
        self.assertTrue(a1["incremental"])
        self.assertEqual(
            [f["industryFieldId"] for f in a1["fields"]],
            [self.f_num.id],
            "增量只应回传基线之后变更的字段",
        )
        self.assertEqual(a1["fields"][0]["value"], "999")
        self.assertEqual(a1["fields"][0]["version"], 2)
        self.assertEqual(
            a1["existingIds"], [self.f_num.id], "existingIds = 当前全部可见字段定义 id"
        )

        # 未变更的公司：fields 为空，但 existingIds 仍完整（客户端据此 diff 删除）
        a2 = data["companies"][str(self.company_a2.id)]
        self.assertEqual(a2["fields"], [])
        self.assertEqual(a2["existingIds"], [self.f_num.id])
        self.assertNotIn("SCOPE-SECRET-A2", resp.content.decode("utf-8"))

        # 增量元素与单点端点的同一字段元素逐字段相同
        single_item = self._data(self._single(self.t_player, self.company_a1.id))["fields"][0]
        self.assertEqual(a1["fields"][0], single_item)

    def test_batch_incremental_includes_new_field_definition(self):
        """基线之后新增的字段定义也必须回传，否则客户端本地永远补不上。"""
        baseline = timezone.now()
        new_field = IndustryField.objects.create(
            industry_type=self.itype_a,
            name="新增字段",
            field_key="new_key_c3",
            field_type="STRING",
            sort_order=3,
            visible=True,
        )
        IndustryField.objects.filter(pk=new_field.pk).update(
            updated_at=baseline + timedelta(seconds=5)
        )

        data = self._data(
            self._batch(
                self.t_player, [self.company_a1.id], updatedAfter=baseline.isoformat()
            )
        )
        a1 = data["companies"][str(self.company_a1.id)]
        self.assertEqual([f["industryFieldId"] for f in a1["fields"]], [new_field.id])
        self.assertEqual(
            a1["existingIds"], [self.f_num.id, new_field.id], "existingIds 按 (sortOrder,id) 排序"
        )

    def test_batch_incremental_previous_ids_yields_deleted_ids(self):
        baseline = timezone.now()
        previous = f"{self.f_num.id},{self.f_hidden.id},900999"
        data = self._data(
            self._batch(
                self.t_player,
                [self.company_a1.id],
                updatedAfter=baseline.isoformat(),
                previousIds=previous,
            )
        )
        a1 = data["companies"][str(self.company_a1.id)]
        self.assertEqual(a1["fields"], [], "基线之后无变更")
        self.assertEqual(a1["existingIds"], [self.f_num.id])
        self.assertEqual(
            a1["deletedIds"], [self.f_hidden.id, 900999], "previousIds 中已不存在的 id"
        )

    def test_batch_invalid_updated_after_falls_back_to_full(self):
        """updatedAfter 无法解析 → 按全量处理（与其它列表端点同一口径，不报 400）。"""
        data = self._data(
            self._batch(
                self.t_player, [self.company_a1.id], updatedAfter="not-a-date"
            )
        )
        self.assertNotIn("incremental", data)
        self.assertEqual(
            [f["industryFieldId"] for f in data["companies"][str(self.company_a1.id)]["fields"]],
            [self.f_num.id],
        )
        self.assertNotIn("existingIds", data["companies"][str(self.company_a1.id)])

    def test_batch_invalid_company_ids_400(self):
        bad_values = ["", "   ", "abc", "1,abc", "-1", "1;2", "1.5", "null"]
        for value in bad_values:
            with self.subTest(companyIds=value):
                resp = self._batch(self.t_player, value)
                self.assertEqual(resp.status_code, 400, resp.content)
                body = resp.json()
                self.assertEqual(body["code"], 400)
                self.assertIsNone(body["data"])
                self.assertIn("companyIds", body["message"])

        # 参数缺失同样是 400（而不是 500 / 空 companies）
        missing = self.client.get("/api/company-fields", {}, **self._h(self.t_player))
        self.assertEqual(missing.status_code, 400)
        self.assertIn("companyIds", missing.json()["message"])

    def test_batch_over_limit_400(self):
        ids = [900000 + i for i in range(1, MAX_BATCH + 2)]  # 51 家
        resp = self._batch(self.t_player, ids)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn(str(MAX_BATCH), resp.json()["message"])

    def test_batch_limit_boundary_50_ok(self):
        ids = [900000 + i for i in range(1, MAX_BATCH + 1)]  # 恰好 50 家
        data = self._data(self._batch(self.t_player, ids))
        self.assertEqual(data["companyIds"], ids)
        self.assertEqual(data["missing"], ids, "不存在的公司一律进 missing")
        self.assertEqual(data["companies"], {})

    def test_batch_dedupes_and_preserves_order(self):
        data = self._data(self._batch(self.t_player, "900003,900001,900003,900002"))
        self.assertEqual(data["companyIds"], [900003, 900001, 900002])
        self.assertEqual(data["missing"], [900003, 900001, 900002])

    def test_batch_route_does_not_conflict_with_legacy_routes(self):
        self.assertIs(
            resolve("/api/company-fields").func.view_class, CompanyFieldsBatchView
        )
        self.assertIs(
            resolve("/api/company-fields/1").func.view_class, CompanyFieldsView
        )
        self.assertIs(
            resolve("/api/company-fields/1/2").func.view_class, CompanyFieldItemView
        )


# ====================================================================
# C. 旧端点原样保留、行为不变
# ====================================================================
class LegacyCompanyFieldsTests(_C3Base):
    def test_single_get_shape_unchanged(self):
        resp = self._single(self.t_player, self.company_a1.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertEqual(set(data.keys()), {"industryTypeId", "fields"})
        self.assertEqual(data["industryTypeId"], self.itype_a.id)
        self.assertEqual(set(data["fields"][0].keys()), FIELD_ITEM_KEYS)
        self.assertEqual(data["fields"][0]["value"], "100")
        self.assertNotIn("ETag", resp.headers, "旧端点不得被 C3 改动影响")
        self.assertNotIn("serverTime", data)

    def test_single_get_404_unchanged(self):
        for cid in (self.company_b1.id, 900999):
            with self.subTest(company_id=cid):
                resp = self._single(self.t_player, cid)
                self.assertEqual(resp.status_code, 404)
                self.assertEqual(resp.json()["message"], "请求的资源不存在")

    def test_single_put_batch_write_unchanged(self):
        resp = self.client.put(
            f"/api/company-fields/{self.company_a1.id}",
            data=json.dumps(
                {
                    "fields": [
                        {
                            "industryFieldId": self.f_num.id,
                            "value": "555",
                            "version": 1,
                        }
                    ]
                }
            ),
            content_type="application/json",
            **self._h(self.t_super),
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"ok": True})
        fv = CompanyFieldValue.objects.get(
            company=self.company_a1, industry_field=self.f_num
        )
        self.assertEqual((fv.value, fv.version), ("555", 2))

        # 乐观锁冲突语义不变（409 + 中文提示）
        conflict = self.client.put(
            f"/api/company-fields/{self.company_a1.id}",
            data=json.dumps(
                {
                    "fields": [
                        {
                            "industryFieldId": self.f_num.id,
                            "value": "666",
                            "version": 1,
                        }
                    ]
                }
            ),
            content_type="application/json",
            **self._h(self.t_super),
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["message"], "数据冲突，请刷新后重试")

    def test_single_field_put_unchanged(self):
        resp = self.client.put(
            f"/api/company-fields/{self.company_a1.id}/{self.f_num.id}",
            data=json.dumps({"value": "7"}),
            content_type="application/json",
            **self._h(self.t_super),
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["data"], {"ok": True})
        fv = CompanyFieldValue.objects.get(
            company=self.company_a1, industry_field=self.f_num
        )
        self.assertEqual(fv.value, "7")
        # 夹具初始 version=1，不带 version 的写入按「当前版本」乐观锁 → 2
        self.assertEqual(fv.version, 2)

        # 无 company:manage 的账号仍被拒（写权限不变）
        denied = self.client.put(
            f"/api/company-fields/{self.company_a1.id}/{self.f_num.id}",
            data=json.dumps({"value": "8"}),
            content_type="application/json",
            **self._h(self.t_player),
        )
        self.assertEqual(denied.status_code, 403)
