# -*- coding: utf-8 -*-
"""I-04 验证：强制改密（must_change_password）门禁与会话心跳的兼容。

真机事故（Debian 13 全新部署后首次登录）：
- 超管首次登录拿到 `must_change_password=true`，后端门禁只放行 `/api/auth/change-password`；
- 前端登录成功即启动 20s 会话心跳，打 `GET /api/auth/me` → 401（门禁）→ 被全局 401 拦截器
  当成「会话过期」→ 清 token、跳登录页、派发 `auth:kicked` 清空内存登录态；
- 用户在「修改初始密码」弹窗里提交时请求已不带 Authorization → 后端 401「登录已过期，
  请重新登录」→ 初始密码永远改不掉，等于**新部署无法登录**。

改后：
1. `/api/auth/me` 纳入强制改密豁免（只读自身资料），心跳与改密弹窗都能正常取资料；
2. 401 响应体带机器可读的 `errorCode`（must_change_password / token_version_mismatch /
   expired），前端据此区分「必须先改密」与「会话过期」，不再误判；
3. 其它业务接口仍然被门禁拦截（安全性未被削弱）。
"""
from __future__ import annotations

from django.test import Client, TestCase

from apps.users.models import User

INIT_PW = "InitPw!12345"
NEW_PW = "ChangedPw!67890"


class MustChangePasswordGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="i04-admin",
            password=INIT_PW,
            role="SUPER_ADMIN",
            must_change_password=True,
        )
        self.client = Client()

    # ---------- 辅助 ----------
    def _auth(self, token: str) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _login(self, password: str = INIT_PW):
        return self.client.post(
            "/api/auth/login",
            data={"username": "i04-admin", "password": password},
            content_type="application/json",
        )

    def _login_token(self) -> str:
        resp = self._login()
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertTrue(data["user"]["mustChangePassword"], "种子超管应带强制改密标记")
        return data["token"]

    # ---------- 门禁边界 ----------
    def test_me_is_exempt_during_forced_change(self):
        """心跳/改密弹窗要读自身资料 —— /api/auth/me 必须放行，且仍带 mustChangePassword 标记。"""
        token = self._login_token()
        resp = self.client.get("/api/auth/me", **self._auth(token))
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()["data"]
        self.assertEqual(body["username"], "i04-admin")
        self.assertTrue(body["mustChangePassword"])

    def test_business_endpoints_still_blocked_with_machine_code(self):
        """门禁不能被削弱：业务接口仍 401，并给出机器码供前端区分语义。"""
        token = self._login_token()
        for path in ("/api/competitions", "/api/users"):
            resp = self.client.get(path, **self._auth(token))
            self.assertEqual(resp.status_code, 401, f"{path} 应仍被强制改密门禁拦截")
            body = resp.json()
            self.assertEqual(body["code"], 401)
            self.assertEqual(
                body.get("errorCode"), "must_change_password",
                "401 必须带机器码，前端才能把「必须先改密」与「会话过期」区分开",
            )
            self.assertIn("初始密码", body["message"])

    # ---------- 改密全流程 ----------
    def test_change_password_clears_flag_and_rotates_token(self):
        token = self._login_token()
        resp = self.client.post(
            "/api/auth/change-password",
            data={"oldPassword": INIT_PW, "newPassword": NEW_PW},
            content_type="application/json",
            **self._auth(token),
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()["data"]
        self.assertTrue(data["ok"])
        self.assertTrue(data["token"], "改密响应必须带回新 token，前端直接续接会话")
        self.assertFalse(data["user"]["mustChangePassword"])

        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password(NEW_PW))

        # 旧 token 立即失效（token_version 递增）——且必须给出机器码，便于前端区分「被顶号」
        stale = self.client.get("/api/auth/me", **self._auth(token))
        self.assertEqual(stale.status_code, 401)
        self.assertEqual(stale.json().get("errorCode"), "token_version_mismatch")

        # 新 token 可正常使用，且不再带强制改密标记
        fresh = self.client.get("/api/auth/me", **self._auth(data["token"]))
        self.assertEqual(fresh.status_code, 200, fresh.content)
        self.assertFalse(fresh.json()["data"]["mustChangePassword"])

    def test_anonymous_change_password_reports_session_expired(self):
        """无 token 提交改密 = 会话被清空后的真实表现（前端把它显示成「登录已过期」）。"""
        resp = self.client.post(
            "/api/auth/change-password",
            data={"oldPassword": INIT_PW, "newPassword": NEW_PW},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["message"], "登录已过期，请重新登录")
