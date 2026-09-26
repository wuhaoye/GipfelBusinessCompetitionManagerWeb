# -*- coding: utf-8 -*-
"""C3 探针（后端侧）：/auth/me 契约、401 语义、批量端点与单点端点一致性与权限隔离。

数据全部在**副本** `_artifacts/db_copy.sqlite3` 上准备与写入，绝不触碰 backend/db.sqlite3。
用到的真实数据（副本自带）：比赛 189 有公司 172/173/174…（产业类型 33）、用户 2 (PLAYER, 比赛 4)、
用户 1 (SUPER_ADMIN)；探针另外创建 3 个受控账号做「跨公司/无权重叠」验证。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\probe_c3_api.py
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

pc.bootstrap()

from django.test import Client  # noqa: E402
from django.test.utils import override_settings  # noqa: E402

from apps.auth.authentication import create_jwt  # noqa: E402
from apps.users.models import User  # noqa: E402

REPO = pc.REPO
COMPANY_A, COMPANY_B = 172, 173          # 同属比赛 189
COMPETITION = 189
MANAGE_FIELD_ID = 1                       # 任意产业字段 id（写路径只为验证路由/权限）
HOSTS = override_settings(ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost"])


# --------------------------------------------------------------------------
def make_user(name: str, perms: list[str], scopes: list[int]) -> User:
    """在副本里准备受控账号（幂等：先删同名）。"""
    User.objects.filter(username=name).delete()
    u = User(
        username=name,
        password_hash="x",
        role="PLAYER",
        permissions=json.dumps(perms),
        company_scopes=json.dumps(scopes),
        view_company_scopes=json.dumps(scopes),
        contract_view_company_scopes=json.dumps(scopes),
        stock_company_scopes=json.dumps(scopes),
        competition_id=COMPETITION,
        is_active=True,
        token_version=1,
        must_change_password=False,
    )
    u.save()
    return u


def auth(user: User) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {create_jwt(user)}"}


def get_json(client: Client, url: str, **kw):
    resp = client.get(url, **kw)
    try:
        body = json.loads(resp.content.decode("utf-8"))
    except Exception:  # noqa: BLE001
        body = None
    return resp, body


# --------------------------------------------------------------------------
def baseline_serialize_keys() -> list[str]:
    """从 HEAD 基线里取出 serialize_user() 的字段名（ast 解析，不靠注释）。"""
    out = subprocess.run(["git", "show", "HEAD:backend/apps/auth/views.py"],
                         cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    tree = ast.parse(out.stdout)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "serialize_user":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    return [k.value for k in sub.value.keys if isinstance(k, ast.Constant)]
    return []


def current_serialize_keys() -> list[str]:
    src = (REPO / "backend" / "apps" / "auth" / "views.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "serialize_user":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    return [k.value for k in sub.value.keys if isinstance(k, ast.Constant)]
    return []


def check_me_contract(admin: User, player: User) -> str | None:
    """F1/F2/F3/F4/F6：/auth/me 逐字段一致、ETag/304、light、开关关闭。"""
    base_keys = baseline_serialize_keys()
    cur_keys = current_serialize_keys()
    verdict = "一致" if base_keys == cur_keys else f"不一致：{base_keys} vs {cur_keys}"
    pc.check("F1-serialize-user-keys-equal", base_keys == cur_keys and bool(base_keys),
             f"serialize_user 字段集与 HEAD 基线{verdict}（基线 {len(base_keys)} 键）")

    client = Client()
    with HOSTS:
        resp, body = get_json(client, "/api/auth/me", **auth(player))
        data_keys = list((body or {}).get("data", {}).keys())
        pc.check("F1-me-data-keys-equal", sorted(data_keys) == sorted(base_keys),
                 f"无参 /auth/me 的 data 字段集与基线一致（{len(data_keys)} 键）"
                 f"；差集={set(data_keys) ^ set(base_keys)}")
        etag = resp.headers.get("ETag")
        pc.check("F2-me-etag-present", bool(etag), f"新增 ETag 响应头：{etag}")
        pc.check("F2-me-envelope", set((body or {}).keys()) == {"code", "message", "data"},
                 f"全局信封不变：{sorted((body or {}).keys())}")

        resp2, body2 = get_json(client, "/api/auth/me", **auth(player))
        pc.check("F2-etag-stable", resp2.headers.get("ETag") == etag,
                 f"同一资料两次请求 ETag 相同（{etag}）")

        # 304：命中 If-None-Match
        resp304 = client.get("/api/auth/me", HTTP_IF_NONE_MATCH=etag, **auth(player))
        pc.check("F3-304-status", resp304.status_code == 304, f"命中 If-None-Match → {resp304.status_code}")
        pc.check("F3-304-empty-body", len(resp304.content) == 0, f"304 空体（长度 {len(resp304.content)}）")
        pc.check("F3-304-same-etag", resp304.headers.get("ETag") == etag,
                 f"304 带同一 ETag：{resp304.headers.get('ETag')}")
        weak = etag[2:] if etag and etag.startswith("W/") else f"W/{etag}"
        resp_weak = client.get("/api/auth/me", HTTP_IF_NONE_MATCH=weak, **auth(player))
        pc.check("F3-weak-prefix-tolerant", resp_weak.status_code == 304,
                 f"弱/强校验前缀差异仍命中 304（发送 {weak}）→ {resp_weak.status_code}")
        resp_nomatch = client.get("/api/auth/me", HTTP_IF_NONE_MATCH='W/"other"', **auth(player))
        pc.check("F3-nomatch-200", resp_nomatch.status_code == 200,
                 f"不匹配的 If-None-Match → {resp_nomatch.status_code}（应 200 完整体）")

        # light
        resp_l, body_l = get_json(client, "/api/auth/me?light=1", **auth(player))
        lkeys = set((body_l or {}).get("data", {}).keys())
        pc.check("F4-light-keys",
                 lkeys == {"id", "tokenVersion", "isActive", "mustChangePassword"},
                 f"light=1 的 data 字段集={sorted(lkeys)}")
        etag_l = resp_l.headers.get("ETag")
        pc.check("F4-light-etag-differs", etag_l and etag_l != etag,
                 f"light 与完整资料的 ETag 不同（{etag_l} vs {etag}）")
        resp_cross = client.get("/api/auth/me", HTTP_IF_NONE_MATCH=etag_l, **auth(player))
        pc.check("F4-light-etag-not-304-for-full", resp_cross.status_code == 200,
                 f"拿 light 的 ETag 请求完整资料 → {resp_cross.status_code}（必须 200，否则丢字段）")
        pc.check("F4-light-truthy-variants",
                 get_json(client, "/api/auth/me?light=true", **auth(player))[1]["data"].keys()
                 == {"id", "tokenVersion", "isActive", "mustChangePassword"},
                 "light=true 同样生效（truthy 口径与既有 _truthy 一致）")

        # 开关关闭
        with override_settings(AUTH_ME_CONDITIONAL_ENABLED=False):
            resp_off = client.get("/api/auth/me", HTTP_IF_NONE_MATCH=etag, **auth(player))
            has_etag = bool(resp_off.headers.get("ETag"))
            off_keys = list(json.loads(resp_off.content.decode("utf-8")).get("data", {}).keys())
            resp_off_l = client.get("/api/auth/me?light=1", **auth(player))
            off_l_keys = list(json.loads(resp_off_l.content.decode("utf-8")).get("data", {}).keys())
        pc.check("F6-off-no-etag", resp_off.status_code == 200 and not has_etag,
                 f"开关关闭：状态 {resp_off.status_code}，ETag={resp_off.headers.get('ETag')!r}（应无）")
        pc.check("F6-off-ignores-light", sorted(off_l_keys) == sorted(base_keys),
                 f"开关关闭：忽略 light，仍返回完整资料（{len(off_l_keys)} 键）")
        pc.check("F6-off-full-body", sorted(off_keys) == sorted(base_keys),
                 "开关关闭：无参响应体与基线逐字段一致")

        # 401 语义（顶号 / 失效）——必须仍然 401，且**不得**被 If-None-Match 变成 304
        no_auth = client.get("/api/auth/me", HTTP_IF_NONE_MATCH=etag)
        bad = client.get("/api/auth/me", HTTP_IF_NONE_MATCH=etag, HTTP_AUTHORIZATION="Bearer not-a-jwt")
        token = create_jwt(player)
        stale = client.get("/api/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        User.objects.filter(pk=player.pk).update(token_version=player.token_version + 1)
        kicked = client.get("/api/auth/me", HTTP_IF_NONE_MATCH=etag,
                            HTTP_AUTHORIZATION=f"Bearer {token}")
        User.objects.filter(pk=player.pk).update(token_version=player.token_version)
        pc.check("F5-401-no-auth", no_auth.status_code == 401, f"无凭据 → {no_auth.status_code}")
        pc.check("F5-401-bad-token", bad.status_code == 401, f"无效 token → {bad.status_code}")
        pc.check("F5-401-kicked-with-if-none-match", kicked.status_code == 401,
                 f"顶号（token_version 变化）+ 匹配的 If-None-Match → {kicked.status_code}"
                 "（必须 401，心跳靠它感知顶号；绝不能 304）")
        pc.check("F5-token-valid-before-kick", stale.status_code == 200,
                 f"顶号前的同一 token（不带 If-None-Match）→ {stale.status_code}")
        pc.check("F5-401-body-machine-code", kicked.status_code == 401 and
                 isinstance(json.loads(kicked.content.decode("utf-8")).get("errorCode"), str),
                 "401 仍带机器可读 errorCode（前端据此区分顶号）")
    return etag


def check_batch(client: Client, user_a: User, user_b: User, mgr: User) -> None:
    """F7~F11：批量端点与单点端点同构、权限一致、无泄露、上限与非法参数、旧端点回归。"""
    with HOSTS:
        single_a, _ = get_json(client, f"/api/company-fields/{COMPANY_A}", **auth(user_a))
        single_b, _ = get_json(client, f"/api/company-fields/{COMPANY_B}", **auth(user_a))
        batch_a, body = get_json(client, f"/api/company-fields?companyIds={COMPANY_A},{COMPANY_B}",
                                 **auth(user_a))
        data = (body or {}).get("data") or {}
        companies = data.get("companies") or {}

        pc.check("F7-batch-200", batch_a.status_code == 200, f"批量端点 → {batch_a.status_code}")
        pc.check("F7-batch-shape",
                 set(data.keys()) == {"companyIds", "serverTime", "companies", "missing"},
                 f"全量响应字段集={sorted(data.keys())}")
        pc.check("F7-batch-companyIds-echo", data.get("companyIds") == [COMPANY_A, COMPANY_B],
                 f"companyIds 回显={data.get('companyIds')}")

        a_single = json.loads(single_a.content.decode("utf-8")).get("data") or {}
        a_batch = companies.get(str(COMPANY_A)) or {}
        pc.check("F7-fields-identical",
                 a_single.get("fields") == a_batch.get("fields"),
                 f"同一公司 fields 与单点端点逐字段一致（{len(a_single.get('fields') or [])} 个字段）")
        pc.check("F7-industryType-identical",
                 a_single.get("industryTypeId") == a_batch.get("industryTypeId"),
                 f"industryTypeId 一致={a_batch.get('industryTypeId')}")

        # 权限一致：单点 404 ↔ 批量 missing
        pc.check("F8-out-of-scope-single-404", single_b.status_code == 404,
                 f"无权公司单点 → {single_b.status_code}（期望 404）")
        pc.check("F8-out-of-scope-batch-missing", data.get("missing") == [COMPANY_B],
                 f"无权公司进 missing={data.get('missing')}")
        pc.check("F8-no-leak-in-company-map", str(COMPANY_B) not in companies,
                 f"响应 companies 键={sorted(companies)}（不得含无权公司 {COMPANY_B}）")
        raw = json.dumps(body, ensure_ascii=False)
        pc.check("F8-no-leak-raw-body", f'"{COMPANY_B}"' not in raw or str(COMPANY_B) in str(data.get("missing")),
                 f"原始响应体未泄露公司 {COMPANY_B} 的字段数据")

        # 反向：B 账号看 A 的公司
        _, body_b = get_json(client, f"/api/company-fields?companyIds={COMPANY_B},{COMPANY_A}",
                             **auth(user_b))
        data_b = (body_b or {}).get("data") or {}
        companies_b = data_b.get("companies") or {}
        single_b_b, _ = get_json(client, f"/api/company-fields/{COMPANY_B}", **auth(user_b))
        pc.check("F8-symmetric-ok", list(companies_b.keys()) == [str(COMPANY_B)]
                 and data_b.get("missing") == [COMPANY_A],
                 f"账号 B：companies={sorted(companies_b)} missing={data_b.get('missing')}")
        pc.check("F8-symmetric-fields",
                 (json.loads(single_b_b.content.decode("utf-8")).get("data") or {}).get("fields")
                 == (companies_b.get(str(COMPANY_B)) or {}).get("fields"),
                 "账号 B 的批量/单点 fields 一致")
        # 两个账号各自的"对方公司"都只能出现在 missing 里，绝不出现在 companies
        pc.check("F8-two-accounts-no-cross-leak",
                 str(COMPANY_B) not in companies and str(COMPANY_A) not in companies_b
                 and COMPANY_B in (data.get("missing") or [])
                 and COMPANY_A in (data_b.get("missing") or []),
                 f"A: companies={sorted(companies)} missing={data.get('missing')}；"
                 f"B: companies={sorted(companies_b)} missing={data_b.get('missing')}")

        # 上限与非法参数
        ids51 = ",".join(str(1000 + i) for i in range(51))
        r51, _ = get_json(client, f"/api/company-fields?companyIds={ids51}", **auth(user_a))
        rbad, _ = get_json(client, f"/api/company-fields?companyIds=172,abc", **auth(user_a))
        rmiss, _ = get_json(client, "/api/company-fields", **auth(user_a))
        rempty, _ = get_json(client, "/api/company-fields?companyIds=", **auth(user_a))
        rtol, body_tol = get_json(client, f"/api/company-fields?companyIds={COMPANY_A},,{COMPANY_A}",
                                  **auth(user_a))
        pc.check("F9-limit-51-400", r51.status_code == 400, f"51 家公司 → {r51.status_code}（期望 400，不截断）")
        pc.check("F9-nonnumeric-400", rbad.status_code == 400, f"companyIds 含非数字 → {rbad.status_code}")
        pc.check("F9-missing-400", rmiss.status_code == 400, f"缺 companyIds → {rmiss.status_code}")
        pc.check("F9-empty-400", rempty.status_code == 400, f"companyIds 为空 → {rempty.status_code}")
        pc.check("F9-tolerate-empty-segments", rtol.status_code == 200,
                 f"`172,,172` 容忍空段并去重 → {rtol.status_code}，companyIds="
                 f"{((body_tol or {}).get('data') or {}).get('companyIds')}")
        pc.check("F9-limit-50-ok",
                 get_json(client, "/api/company-fields?companyIds="
                          + ",".join(str(2000 + i) for i in range(50)), **auth(user_a))[0].status_code == 200,
                 "恰好 50 家 → 200（边界值）")

        # 增量协议
        server_time = data.get("serverTime")
        inc, body_inc = get_json(
            client,
            f"/api/company-fields?companyIds={COMPANY_A}&updatedAfter={server_time}",
            **auth(user_a))
        dinc = (body_inc or {}).get("data") or {}
        one = (dinc.get("companies") or {}).get(str(COMPANY_A)) or {}
        pc.check("F10-incremental-flag", inc.status_code == 200 and dinc.get("incremental") is True,
                 f"带 updatedAfter → incremental={dinc.get('incremental')}")
        pc.check("F10-incremental-shape", one.get("incremental") is True and isinstance(one.get("existingIds"), list),
                 f"公司对象含 incremental/existingIds：keys={sorted(one.keys())}")
        pc.check("F10-incremental-existingIds-full",
                 len(one.get("existingIds") or []) == len(a_single.get("fields") or []),
                 f"existingIds={len(one.get('existingIds') or [])} = 全部可见字段数"
                 f"{len(a_single.get('fields') or [])}")
        inc_prev, body_prev = get_json(
            client,
            f"/api/company-fields?companyIds={COMPANY_A}&updatedAfter={server_time}&previousIds=1,2,3",
            **auth(user_a))
        dprev = ((body_prev or {}).get("data") or {}).get("companies", {}).get(str(COMPANY_A)) or {}
        pc.check("F10-previousIds-deletedIds", "deletedIds" in dprev and isinstance(dprev.get("deletedIds"), list),
                 f"带 previousIds → deletedIds={dprev.get('deletedIds')}")
        pc.check("F10-bad-updatedAfter-falls-back-full",
                 (get_json(client, f"/api/company-fields?companyIds={COMPANY_A}&updatedAfter=not-a-date",
                           **auth(user_a))[1] or {}).get("data", {}).get("incremental") is None,
                 "非法 updatedAfter → 按全量处理（无 incremental 标记，沿用既有口径）")

        # 旧端点原样保留
        from apps.industry_types.models import IndustryField

        real_field_id = (
            IndustryField.objects.filter(industry_type_id=33).order_by("id").values_list("id", flat=True).first()
        )
        single_ok = client.get(f"/api/company-fields/{COMPANY_A}", **auth(user_a))
        put_item = client.put(f"/api/company-fields/{COMPANY_A}/{real_field_id}",
                              data=json.dumps({"value": "ops_check"}),
                              content_type="application/json", **auth(mgr))
        put_batch = client.put(f"/api/company-fields/{COMPANY_A}",
                               data=json.dumps({"fields": [{"industryFieldId": real_field_id,
                                                            "value": "ops_check2"}]}),
                               content_type="application/json", **auth(mgr))
        no_perm = client.put(f"/api/company-fields/{COMPANY_A}/{real_field_id}",
                             data=json.dumps({"value": "x"}),
                             content_type="application/json", **auth(user_a))
        pc.check("F11-legacy-single-get", single_ok.status_code == 200,
                 f"GET /api/company-fields/{COMPANY_A} → {single_ok.status_code}")
        pc.check("F11-legacy-item-put", put_item.status_code == 200,
                 f"PUT /api/company-fields/{COMPANY_A}/{real_field_id} → {put_item.status_code}")
        pc.check("F11-legacy-batch-put", put_batch.status_code == 200,
                 f"PUT /api/company-fields/{COMPANY_A} → {put_batch.status_code}")
        pc.check("F11-legacy-write-perm", no_perm.status_code == 403,
                 f"无 company:manage 写 → {no_perm.status_code}（期望 403）")
        # 单点/批量读的 50 上限不适用于单点：仍能读单公司
        single_big = client.get(f"/api/company-fields/{COMPANY_A}", **auth(user_a))
        pc.check("F11-single-unaffected-by-batch-limit", single_big.status_code == 200,
                 "单点端点不受批量 50 上限影响")


def main() -> int:
    for name in ("ops_check_a", "ops_check_b", "ops_check_mgr"):
        User.objects.filter(username=name).delete()
    admin = User.objects.filter(pk=1).first() or User.objects.first()
    player = User.objects.filter(pk=2).first() or admin
    user_a = make_user("ops_check_a", ["company:view"], [COMPANY_A])
    user_b = make_user("ops_check_b", ["company:view"], [COMPANY_B])
    mgr = make_user("ops_check_mgr", ["company:view", "company:manage"], [COMPANY_A])
    pc.info(f"副本内受控账号：a={user_a.pk}(scope {COMPANY_A}) b={user_b.pk}(scope {COMPANY_B}) "
            f"mgr={mgr.pk}(+manage)；player={player.pk} admin={admin.pk}")
    check_me_contract(admin, player)
    client = Client()
    check_batch(client, user_a, user_b, mgr)
    return pc.finish()


if __name__ == "__main__":
    raise SystemExit(main())
