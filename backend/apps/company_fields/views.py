"""公司产业字段视图。

权限：读 company:view，写 company:manage。
路由前缀 /api（由 backend.urls include）。

C3 新增**批量读**端点（契约见 docs/运维约束整改设计说明.md §4.2）：
    GET /api/company-fields?companyIds=1,2,3
既有逐个端点（`GET/PUT /company-fields/{cid}`、`PUT /company-fields/{cid}/{fieldId}`）
**原样保留、行为不变**——老前端与 contract_watcher 可能依赖（简报 §C3.3 向后兼容要求）。
"""
from __future__ import annotations

import json
import re

from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.exceptions import BusinessError, FieldWriteConflictException
from apps.common.guards import PermissionsPermission, require_permissions
from apps.common.permissions import has_permission
from apps.realtime.emit import emit_resource_changed

from apps.companies.models import Company, CompanyFieldValue

from .serializers import SetFieldSerializer, SetValuesSerializer
from apps.common.helpers import parse_previous_ids as _parse_previous_ids
from apps.common.helpers import truthy as _truthy
from apps.common.sync import parse_baseline as _parse_baseline
from apps.common.sync import server_now_iso as _server_now_iso

_VIEW_PERM = "company:view"
_MANAGE_PERM = "company:manage"
_PERM_CLASSES = (IsAuthenticated, PermissionsPermission)

# 批量端点单次上限（§4.2：上限与超限行为由实现方定，但必须写进注释与测试）。
# 取 50 = 与服务端 PAGE_SIZE 默认值一致：一次请求的字段量级与「一页列表」相当，
# 既能把「重连时 N 家公司各发一个请求」压成 1 个请求（C3.3），又不会让单请求的
# SQL/序列化开销放大到不可控。超限**返回 400 而不是截断**：截断会让调用方
# 误以为「没返回的公司就是没权限/不存在」，静默丢数据。
_MAX_BATCH_COMPANIES = 50


def _can_manage(user) -> bool:
    return has_permission(user.role, user.permissions_list, _MANAGE_PERM)



# 公司读取统一走 apps.common.helpers.get_company_scoped：比赛域 + viewCompanyScopes
# 双重隔离。
from apps.common.helpers import get_company_scoped as _get_company


def _parse_json(value):
    """JSON 字符串 → 对象；已是对象/None 原样返回。"""
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _field_to_dict(field, fv, company_name: str | None) -> dict:
    """单字段 + 其值 → 前端契约 dict。"""
    default_value = getattr(field, "default_value", "") or ""
    return {
        "industryFieldId": field.id,
        "fieldKey": getattr(field, "field_key", None),
        "name": getattr(field, "name", None),
        "fieldType": getattr(field, "field_type", None),
        "config": _parse_json(getattr(field, "config", None)),
        "defaultValue": default_value,
        "value": fv.value if fv else default_value,
        "version": fv.version if fv else 0,
        "visible": getattr(field, "visible", True),
        "isCalculated": getattr(field, "is_calculated", False),
        "sortOrder": getattr(field, "sort_order", 0),
        "companyName": company_name,
    }


def _write_field_value(
    company_id: int, industry_field_id: int, value: str, version: int | None
) -> None:
    """乐观锁写入单个字段值。

    - 不存在：创建（version=1）
    - 存在：WHERE id AND version=<期望版本> 更新并自增；未命中则 409 冲突
      期望版本取 body.version，缺省取当前 version（即无条件更新）
    """
    fv = CompanyFieldValue.objects.filter(
        company_id=company_id, industry_field_id=industry_field_id
    ).first()
    if fv is None:
        CompanyFieldValue.objects.create(
            company_id=company_id,
            industry_field_id=industry_field_id,
            value=value,
            version=1,
        )
        return
    expected_version = version if version is not None else fv.version
    updated = CompanyFieldValue.objects.filter(
        pk=fv.pk, version=expected_version
    ).update(
        value=value,
        version=expected_version + 1,
    )
    if not updated:
        raise FieldWriteConflictException()


def _recompute_calc_fields(company_id: int) -> None:
    """计算字段级联重算（calcGraph 求值引擎见 calc.py，复用合同引擎算子与沙箱）。"""
    from .calc import recompute_calc_fields

    recompute_calc_fields(company_id)


# ==================== 读取口径（单公司端点与批量端点共用，保证严格同构） ====================
def _can_see_hidden(user) -> bool:
    """includeHidden=true 时的放行条件（与改造前单公司端点逐字一致）。"""
    return getattr(user, "role", None) == "SUPER_ADMIN" or _can_manage(user)


def _visible_fields(industry_type, user, include_hidden: bool) -> list:
    """某产业类型下对当前用户可见的字段定义（可见性与排序口径与单公司端点完全一致）。"""
    # 延迟导入：industry_types app 可能尚未就绪，避免本模块导入期失败
    from apps.industry_types.models import IndustryField

    fields_qs = IndustryField.objects.filter(industry_type=industry_type)
    # 发布可见性：includeHidden=true 且有 company:manage 权限 → 全部；否则仅可见字段
    if not (include_hidden and _can_see_hidden(user)):
        fields_qs = fields_qs.filter(visible=True)
    return list(fields_qs.order_by("sort_order", "id"))


def _field_values_map(company_ids: list[int]) -> dict:
    """{(company_id, industry_field_id): CompanyFieldValue} —— 一次查询覆盖全部公司。

    单公司端点等价于 filter(company=company)；批量端点用 company_id__in 一次取回，
    避免 N 家公司 N 次查询（正是本端点要消除的放大源）。
    """
    if not company_ids:
        return {}
    return {
        (fv.company_id, fv.industry_field_id): fv
        for fv in CompanyFieldValue.objects.filter(company_id__in=company_ids)
    }


def _company_fields_payload(company, fields: list, values_map: dict) -> list:
    """字段定义 + 值 → 前端契约元素数组（与单公司端点逐字段相同）。"""
    return [_field_to_dict(f, values_map.get((company.id, f.id)), company.name) for f in fields]


def _parse_company_ids(raw) -> list[int]:
    """解析 `companyIds=1,2,3`（去重保序）。

    缺失 / 全空 / 含非数字 / 超过单次上限 → 400 + 明确中文提示（不静默截断）。
    容忍 `1,,2`、`1,2,` 这类空段（前端拼接残留）。
    """
    empty_msg = "companyIds 不能为空，格式为逗号分隔的公司 id（如 companyIds=1,2,3）"
    if raw is None or not str(raw).strip():
        raise BusinessError(empty_msg, code=400, status_code=400)
    ids: list[int] = []
    seen: set[int] = set()
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        if not re.fullmatch(r"[0-9]+", part):
            raise BusinessError(
                f"companyIds 含非法公司 id：{part}", code=400, status_code=400
            )
        cid = int(part)
        if cid not in seen:
            seen.add(cid)
            ids.append(cid)
    if not ids:
        raise BusinessError(empty_msg, code=400, status_code=400)
    if len(ids) > _MAX_BATCH_COMPANIES:
        raise BusinessError(
            f"单次最多查询 {_MAX_BATCH_COMPANIES} 家公司（本次 {len(ids)} 家）",
            code=400,
            status_code=400,
        )
    return ids


def _changed_field_value_ids(company_ids: list[int], baseline) -> dict[int, set[int]]:
    """{company_id: {基线之后有值变更的 industry_field_id}}。"""
    if not company_ids:
        return {}
    out: dict[int, set[int]] = {}
    rows = CompanyFieldValue.objects.filter(
        company_id__in=company_ids, updated_at__gt=baseline
    ).values_list("company_id", "industry_field_id")
    for company_id, field_id in rows:
        out.setdefault(company_id, set()).add(field_id)
    return out


def _changed_field_definition_ids(industry_type_ids, baseline) -> dict[int, set[int]]:
    """{industry_type_id: {基线之后变更（含新增）的字段定义 id}}。

    只按 CompanyFieldValue.updated_at 过滤会漏掉「新增字段定义」——客户端本地永远补不上
    这个字段；因此字段定义自身的 updated_at 也要纳入增量判定。
    """
    from apps.industry_types.models import IndustryField

    ids = [i for i in industry_type_ids if i is not None]
    if not ids:
        return {}
    out: dict[int, set[int]] = {}
    rows = IndustryField.objects.filter(
        industry_type_id__in=ids, updated_at__gt=baseline
    ).values_list("industry_type_id", "id")
    for industry_type_id, field_id in rows:
        out.setdefault(industry_type_id, set()).add(field_id)
    return out


class CompanyFieldsView(APIView):
    """GET /company-fields/:companyId 读字段值；PUT 批量写。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(_VIEW_PERM)
    def get(self, request, company_id):
        # 权限/作用域校验必须在任何字段读取之前（无权/不存在 → 404，不泄露任何数据）
        company = _get_company(company_id, request.user)
        include_hidden = _truthy(request.query_params.get("includeHidden"))
        industry_type = company.industry_type
        if industry_type is None:
            return Response({"industryTypeId": None, "fields": []})

        fields = _visible_fields(industry_type, request.user, include_hidden)
        values_map = _field_values_map([company.id])
        result = _company_fields_payload(company, fields, values_map)
        # 对齐前端契约：返回 { industryTypeId, fields:[...] }（见 frontend/src/api/request.ts
        # storeCompanyFieldsAndReturn），而非裸数组，否则前端 fields 全空。
        return Response({"industryTypeId": industry_type.id, "fields": result})

    @require_permissions(_MANAGE_PERM)
    def put(self, request, company_id):
        company = _get_company(company_id, request.user)
        serializer = SetValuesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["fields"]
        with transaction.atomic():
            for item in items:
                _write_field_value(
                    company_id=company.id,
                    industry_field_id=item["industryFieldId"],
                    value=item.get("value") or "",
                    version=item.get("version"),
                )
        _recompute_calc_fields(company.id)
        emit_resource_changed(
            "company-field", company.id, company.competition_id, "updated"
        )
        return Response({"ok": True})


class CompanyFieldItemView(APIView):
    """PUT /company-fields/:companyId/:fieldId 单字段写入。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(_MANAGE_PERM)
    def put(self, request, company_id, field_id):
        company = _get_company(company_id, request.user)
        serializer = SetFieldSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data.get("value") or ""
        version = serializer.validated_data.get("version")
        with transaction.atomic():
            _write_field_value(company.id, field_id, value, version)
        _recompute_calc_fields(company.id)
        emit_resource_changed(
            "company-field", company.id, company.competition_id, "updated"
        )
        return Response({"ok": True})


class CompanyFieldsBatchView(APIView):
    """GET /company-fields?companyIds=1,2,3 —— 批量读多个公司的产业字段（C3 新增）。

    契约字面量（docs/运维约束整改设计说明.md §4.2，前端按此对接，不得各自改名）：

    - 全量（无 `updatedAfter`）：
        ``data = {companyIds:[1,2], serverTime:"<ISO>", companies:{"1":{"industryTypeId":..,"fields":[…]}}, missing:[3]}``
    - 增量（带 `updatedAfter=<ISO>`）：
        顶部额外 `incremental: true`；每个公司对象 = 单公司协议同构
        ``{industryTypeId, fields(仅变更字段), existingIds(当前全部可见字段定义 id), incremental:true}``，
        请求带 `previousIds` 时另附 `deletedIds`；顶部 `serverTime` 即本次基线，客户端据此推进。
    - `fields` 元素与单公司端点 `_field_to_dict` **逐字段相同**，其 id 语义为
      `industryFieldId`（该端点历史上不返回 `id` 键）；`existingIds`/`deletedIds` 里的 id
      同样是 `industryFieldId`。
    - 权限：逐公司套用与单公司端点**完全相同**的 `_get_company` 校验（比赛域 +
      viewCompanyScopes）；无权 / 不存在的公司进 `missing`，**绝不返回其数据**。
    - 上限：`companyIds` 去重后最多 `_MAX_BATCH_COMPANIES`(50) 家，超限 400（不截断）。
    - 非法 `companyIds`（缺失 / 全空 / 含非数字）→ 400；非法 `updatedAfter`（无法解析）
      → 按全量处理（与 apps/common/sync.py 既有 `apply_updated_after` 口径一致）。
    """

    permission_classes = _PERM_CLASSES

    @require_permissions(_VIEW_PERM)
    def get(self, request):
        company_ids = _parse_company_ids(request.query_params.get("companyIds"))
        user = request.user
        include_hidden = _truthy(request.query_params.get("includeHidden"))

        # 基线在查询**之前**取：本次请求期间发生的写入都会大于该基线，
        # 因而落在下一次增量里，不会丢变更（build_incremental_result 是查询后取，
        # 批量场景下多公司共用一条基线，先取更安全）。
        server_time = _server_now_iso()
        baseline = _parse_baseline(request.query_params.get("updatedAfter"))
        previous_ids = _parse_previous_ids(request.query_params.get("previousIds"))

        missing: list[int] = []
        accessible: list = []
        for cid in company_ids:
            try:
                accessible.append(_get_company(cid, user))
            except BusinessError:
                # 不存在 / 跨比赛 / 不在 viewCompanyScopes —— 与单公司端点的 404 同一判定；
                # 批量端点只把它们降级进 missing，不因个别无权公司让整批失败，
                # 也绝不把这些公司的任何字段数据放进响应。
                missing.append(cid)

        # 字段定义按产业类型缓存：单请求内每种产业类型只查一次（批量不放大查询数）
        fields_cache: dict[int, list] = {}
        for company in accessible:
            it = company.industry_type
            if it is not None and it.id not in fields_cache:
                fields_cache[it.id] = _visible_fields(it, user, include_hidden)

        # 值一次取回（替代 N 次 filter(company=...)）
        values_map = _field_values_map([c.id for c in accessible])

        # ---------- 全量 ----------
        if baseline is None:
            companies: dict[str, dict] = {}
            for company in accessible:
                it = company.industry_type
                if it is None:
                    companies[str(company.id)] = {"industryTypeId": None, "fields": []}
                    continue
                companies[str(company.id)] = {
                    "industryTypeId": it.id,
                    "fields": _company_fields_payload(
                        company, fields_cache.get(it.id, []), values_map
                    ),
                }
            return Response(
                {
                    "companyIds": company_ids,
                    "serverTime": server_time,
                    "companies": companies,
                    "missing": missing,
                }
            )

        # ---------- 增量 ----------
        changed_def_ids = _changed_field_definition_ids(
            {c.industry_type_id for c in accessible}, baseline
        )
        changed_value_ids = _changed_field_value_ids([c.id for c in accessible], baseline)

        companies = {}
        for company in accessible:
            it = company.industry_type
            if it is None:
                companies[str(company.id)] = {
                    "industryTypeId": None,
                    "fields": [],
                    "existingIds": [],
                    "incremental": True,
                }
                continue
            fields = fields_cache.get(it.id, [])
            existing_ids = [f.id for f in fields]
            changed_here = changed_def_ids.get(it.id, set()) | changed_value_ids.get(
                company.id, set()
            )
            payload: dict = {
                "industryTypeId": it.id,
                # 只回传基线之后「值有变更」或「字段定义有变更/新增」的字段：
                # 定义变更也要回传，否则客户端本地永远补不上新增字段。
                "fields": _company_fields_payload(
                    company, [f for f in fields if f.id in changed_here], values_map
                ),
                # existingIds = 当前全部可见字段定义 id，**不受 updatedAfter 过滤影响**；
                # 客户端据此剔除本地多余字段（等价 apps/common/sync.py 的 existingIds 语义）。
                "existingIds": existing_ids,
                "incremental": True,
            }
            if previous_ids is not None:
                # 照抄既有 deletedIds 语义：previousIds 中已不存在的 id。
                # 注意 previousIds 是「单集合」语义（其它列表端点亦然），批量下按同一集合
                # 逐公司套用；多公司场景调用方应只用 existingIds 自行 diff。
                existing_set = set(existing_ids)
                payload["deletedIds"] = [i for i in previous_ids if i not in existing_set]
            companies[str(company.id)] = payload

        return Response(
            {
                "companyIds": company_ids,
                "serverTime": server_time,
                "companies": companies,
                "missing": missing,
                "incremental": True,
            }
        )
