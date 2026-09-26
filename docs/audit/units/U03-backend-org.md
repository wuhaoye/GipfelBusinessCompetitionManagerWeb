# U03 backend org（分支归属：master 基线）

## 概述

审计范围：`backend/apps/` 下 7 个 app 的全部源码 —— `competitions`（比赛/财年）、`companies`（公司）、`company_fields`（乐观锁 + 级联重算 + 计算图引擎 + 财年定时器）、`industry_types`（产业类型 + 计算图字段）、`regions`（地区 + 概览卡片）、`maps`（地图节点/边）、`tech_tree`（科技树）。共约 30 个源文件（models/serializers/views/urls/calc/timer），全部逐行读过；依赖的公共层（`apps/common/*`、`apps/contracts/engine.py` 的调用点、`backend/settings.py`）按需交叉阅读以确认可达性与真实后果。

运行环境事实（已实测确认，非推测）：Django 5.0.14 + DRF 3.15.1，数据库为 SQLite（`backend/settings.py:305-310`），DRF 默认解析器（JSON/Form/MultiPart）全部启用，`DEFAULT_PERMISSION_CLASSES` 含 `CompetitionScopePermission` 兜底，全局异常处理器对非 `APIException` 一律返回 500。

本次审计中实际用解释器（只读、不触库）验证过的关键事实：
- `CompanySerializer` 的 `validated_data` 键为 camelCase（`name/industryTypeId/competitionId/regionId/status`），而 `save(update_fields=[...])` 过滤器用 snake_case 匹配 → 见 O-01。
- `serializers.FloatField` 接受字符串 `"inf"/"-inf"/"nan"/"1e400"`（`float()` 语义）→ 见 O-04、O-07。
- SQLite 存入 `inf` 成功；存 `NaN` 被转成 NULL 触发 `NOT NULL constraint failed`；超过 int64 的整数触发 `OverflowError: Python int too large to convert to SQLite INTEGER` → 见 O-04、O-07、O-17。
- `apps.common.response.JSONRenderer` 底层 `json.dumps(..., allow_nan=False)`，渲染 `inf` 直接抛 `ValueError`，且渲染发生在 `finalize_response`（不在 DRF 的 try/except 内）→ 见 O-04。
- `timer._serialize("NUMBER", _parse_number_raw("1e999999999"))` 实测产出长度 **1,000,000,000** 的字符串 → 见 O-05。
- `PRAGMA foreign_keys = ON`（Django SQLite 后端默认）+ `company_field_values` 的 `unique_together(company, industry_field)` 存在于迁移 → 见 O-02、O-10。

结论摘要：无 P0（未发现未授权访问、RCE、SQL 注入类问题）；P1 共 5 条（跨比赛读取、静默写丢失、持久化 DoS、跨归属写入），P2 共 11 条（并发/事务/边界），P3 共 4 条（健壮性与性能）。最高危的方向是「**多比赛租户隔离在按 id 取对象 / 解析 JSON 内嵌 id 时被绕过**」与「**数值字段无任何范围校验（NaN/Inf/指数）**」。

---

## 缺陷清单

### [P1] O-01 companies PATCH 只带 regionId / industryTypeId 时静默不落库（接口返回新值，数据库未变）

- 位置：`backend/apps/companies/serializers.py:78`
- 代码：
```python
    def update(self, instance: Company, validated_data: dict) -> Company:
        if "name" in validated_data:
            instance.name = validated_data["name"]
        if "industryTypeId" in validated_data:
            instance.industry_type_id = validated_data["industryTypeId"]
        # 禁止跨比赛迁移公司：忽略 competitionId 字段
        if "regionId" in validated_data:
            instance.region_id = validated_data["regionId"]
        if "status" in validated_data:
            instance.status = validated_data["status"]
        instance.save(update_fields=[
            f for f in ["name", "industry_type_id", "region_id", "status", "updated_at"]
            if f in validated_data or f == "updated_at"
        ])
```
- 触发条件：`PATCH /api/companies/<id>`，body 为 `{"regionId": 3}` 或 `{"industryTypeId": 7}`（或两者同传但不带 name/status）。
  `validated_data` 的键是 camelCase（已实测：`['name','industryTypeId','competitionId','regionId','status']`），而 `update_fields` 的候选列表是 snake_case（`industry_type_id` / `region_id`），`f in validated_data` 恒为 False；最终 `update_fields` 只剩 `["updated_at"]`。`instance.region_id` 只在内存里被赋值，`save(update_fields=["updated_at"])` 不会写该列。
- 后果：公司改区域 / 改产业类型**静默失败**，但 `ItemAPIView.patch` 随后返回 `_serialize(company)`（读内存实例），响应里带着「已生效」的新值，前端与调用方无法察觉；`updated_at` 却被刷新，增量同步（`updatedAfter`）还会把这条没变的记录当成「已更新」推给所有客户端。误以为区域已归属 → 区域总览卡片（`_local_companies` 按 location 匹配）一直缺公司；跨比赛/跨产业数据长期错位且无告警。
- 修复建议：`update_fields` 用 camelCase 键名（`if "regionId" in validated_data` → `"region_id"`），或直接 `instance.save()`（本接口只会变更这几个字段）；更稳妥的做法是修正后加一条回归测试，断言 `Company.objects.get(pk=...).region_id` 已变。
  （同一行还有一处小缺陷：`data = {k: v for k, v in request.data.items() ...}`（`backend/apps/companies/views.py:123`）在 body 为 JSON 数组时 `list.items()` → AttributeError → 500，而非 400。）

---

### [P1] O-02 company_fields 写入不校验 industryField 归属与存在性：跨产业/跨比赛写值、FK 500、任意用户可阻断字段删除

- 位置：`backend/apps/company_fields/views.py:71`
- 代码：
```python
def _write_field_value(
    company_id: int, industry_field_id: int, value: str, version: int | None
) -> None:
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
```
- 触发条件：`PUT /api/company-fields/<自己的 companyId>/<任意 fieldId>`（`FieldValueItemSerializer` / `SetFieldSerializer` 只有 `IntegerField`，无任何归属校验；`company` 已按比赛域 + viewCompanyScopes 校验，`field_id` 没有）。三种输入：
  1. `fieldId` 属于**另一个产业类型**（甚至另一个比赛的公司所用产业）→ 直接建行；
  2. `fieldId` 不存在（如 `99999999`）→ `industry_field_id` 触发 SQLite 外键约束（已确认 `PRAGMA foreign_keys = ON`）→ `IntegrityError` → 全局异常处理器对非 APIException 返回 500；
  3. 批量接口中，事务内任一 field 非法 → 整批 500，用户无法分辨是哪一条。
- 后果：① 有 `company:manage` 的普通账号可以对自己公司写入本不属于它的产业字段值（数据污染；`CompanySerializer` 的 `_count.companies` 计数被抬高）；② 只要往某字段写一条值，`FieldItemView.delete` 的 `if count > 0: raise "该产业字段已被 N 家公司填写"`（`backend/apps/industry_types/views.py:639-644`）就会被永久触发 —— 普通选手账号可用一次 PUT **阻断超管删除任意字段**（客服/运维层面的软 DoS）；③ 请求体里的 `industryTypeId` 字段被序列化器接收但完全未使用，前端以为能连带改产业类型，实际被静默忽略。
- 修复建议：写入前 `IndustryField.objects.filter(id=industry_field_id, industry_type_id=company.industry_type_id).exists()`，不存在则 404/400；批量接口对每个 item 单独校验并返回明确的失败下标，避免整批 500。

---

### [P1] O-03 regions 概览卡片解析忽略 competition_id：跨比赛读取任意公司产业字段值（IDOR）

- 位置：`backend/apps/regions/views.py:138`
- 代码：
```python
    company_ids = [c.get("companyId") for c in cards if c.get("companyId") is not None]
    field_ids = [c.get("industryFieldId") for c in cards if c.get("industryFieldId") is not None]

    companies = {c.id: c for c in Company.objects.filter(pk__in=company_ids)}
    fields = {f.id: f for f in IndustryField.objects.filter(pk__in=field_ids)}
```
- 触发条件：比赛 A 中持有 `data:region:edit` 的账号（`COMPETITION_ADMIN` 扩展权限即含此权）：
  1. `PUT /api/regions/<自己在 A 比赛的 regionId>/overview-cards`，body `{"cards":[{"id":"x","displayName":"d","companyId":<B 比赛公司 id>,"industryFieldId":<该产业字段 id>}]}` —— `_get_region` 只校验 region 属于自己比赛，`OverviewCardItemSerializer` 只校验类型；
  2. 再 `GET /api/regions/<regionId>/overview`（或 `GET /api/regions/map-overview`），响应中 `cards[].value` 就是 B 比赛公司的字段值，`valid:true`。
  函数签名 `_resolve_cards(cards, competition_id)` 收了 `competition_id`（两个调用点都传了：`backend/apps/regions/views.py:259`、`:437`），但函数体内**从未使用**它来过滤 `Company`。
- 后果：跨比赛租户隔离失效——商业模拟比赛里公司的产业字段值就是各队的经营机密（产能、成本、资金相关口径）。攻击者只需枚举自增 id 即可批量拉取其它比赛的全部公司字段值，且写入路径合法（改的是自己的 region），审计日志里看不出异常。
- 修复建议：`Company.objects.filter(pk__in=company_ids, competition_id=competition_id)`；`IndustryField` 若需跨产业共享可保留全局，但必须在 `_resolve_cards` 内校验 `field.industry_type_id == company.industry_type_id`；并在写入侧（`_validate_cards` / `SaveOverviewCardsView.put`）就校验「卡片引用的公司必须属于本比赛」，从源头拒绝非法卡片。

---

### [P1] O-04 maps / tech_tree 的 FloatField 无范围校验：`"inf"` 可入库，之后该模块列表接口永久 500

- 位置：`backend/apps/maps/serializers.py:95`（同型：`backend/apps/maps/serializers.py:153`、`backend/apps/tech_tree/serializers.py:17`）
- 代码：
```python
class MapNodeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=128, trim_whitespace=True)
    region = serializers.CharField(max_length=128, default="")
    nodeTypeId = serializers.IntegerField()
    x = serializers.FloatField(default=0)
    y = serializers.FloatField(default=0)
    competitionId = serializers.IntegerField()
```
- 触发条件：DRF 的 `JSONParser` 虽然用 `strict_constant` 拦掉了裸 `Infinity` 字面量，但**字符串形态不拦**：`POST /api/map-nodes` body `{"name":"n","nodeTypeId":1,"competitionId":1,"x":"inf","y":"nan"}`（或 `{"x":"1e400"}`、或表单编码 `x=inf`）。已实测：`serializers.FloatField().to_internal_value("inf")` → `inf`，`"nan"` → `nan`，序列化器 `is_valid()` 通过。
  - `x:"inf"` → SQLite 存为 `inf`；
  - `x:"nan"` → SQLite 把 NaN 转成 NULL → `NOT NULL constraint failed: map_nodes.x` → IntegrityError → 500（写入即失败）。
  同类：`MapEdge.distance`（`backend/apps/maps/serializers.py:153`）、`TechNode.researchCost`（`backend/apps/tech_tree/serializers.py:18`）同样接受 `"inf"` 并入库（已实测 `TechNodeSerializer` 接受 `researchCost:"inf"`）。
- 后果：`inf` 一旦落库，**整个地图模块的读取全部 500**：`MapNodeSerializer.to_representation` 输出 `x/y`，`MapEdgeSerializer` 输出 `distance`，`MapFullView`（`GET /api/maps/full`）与列表/详情都要序列化该行；`apps.common.response.JSONRenderer` 底层是 `json.dumps(..., allow_nan=False)`（已用项目渲染器实测 `render({'x': inf})` → `ValueError: Out of range float values are not JSON compliant: inf`），且渲染发生在 `finalize_response`（DRF 的 `try/except` 之外），异常处理器接不到 → Django 裸 500，响应体都不是统一的 `{code,message,data}`。`_convert_big_numbers` 对 float 是原样透传（`backend/apps/common/renderers.py:34-35`），不做兜底。
  一个持有 `data:map:edit` 的账号提交一个节点即可让**同比赛所有用户**的地图页、依赖地图节点的区域公司匹配、以及 `MapFullView` 全部不可用；`tech-nodes` 列表同理（`researchCost:"inf"` 后 `GET /api/tech-nodes` 恒 500）。清理只能靠猜到 id 直接 `DELETE`（DELETE 响应不含浮点，仍可用）。
- 修复建议：`FloatField(min_value=..., max_value=...)` 或自定义校验拒绝非有限值（`math.isfinite`）；模型层改用 `DecimalField` 或加 `CheckConstraint`；响应渲染前对 float 做 `isfinite` 兜底（`_convert_big_numbers` 里把 NaN/Inf 转 `None`），避免单行脏数据把整个接口打挂。

---

### [P1] O-05 NUMBER 字段序列化对指数形态值展开定点表示：单请求生成 10 亿字符字符串（内存耗尽）

- 位置：`backend/apps/company_fields/calc.py:427`（触发点）与 `backend/apps/company_fields/timer.py:76`
- 代码：
```python
            stored = _serialize(f.field_type, raw)
            if f.field_type == "NUMBER":
                # 小数部分无有效内容时不补位 0（'1.0'→'1'，'1.50'→'1.5'）
                stored = _trim_number_trailing_zeros(stored)
            values[f.field_key] = stored
            _write_calc_value(company_id, f.id, stored)
```
```python
    if field_type == "NUMBER":
        # Decimal 规范化为定点表示（避免 1E+23 科学计数形态入库），int/其余走 str
        if isinstance(raw, Decimal):
            return format(raw, "f")
        return str(raw)
```
- 触发条件：NUMBER 字段值链路全程不做指数上限检查 —— `_parse_number_raw`（`timer.py:37-58`）对 `"1e999999999"` 走 `Decimal(s)` 分支并返回有限 Decimal，`_serialize` 再用 `format(raw, "f")` 展开成定点串。实测（本机解释器复刻这两函数）：

  `_serialize('NUMBER', _parse_number_raw('1e999999999'))` → 返回长度 **1000000000** 的字符串（`'1e6'`→7，`'1E+23'`→24，线性于指数）。
  可达路径（任一即可）：① 先 `PUT /api/company-fields/<cid>` 把某个 NUMBER 基础字段写成 `"1e999999999"`，随后触发的 `recompute_calc_fields` 中只要有一个计算字段的 calcGraph 把 `value(FIELD)` 节点直接连到 output（引用该基础字段），`_field_raw_with_default` → `_stored_to_raw` 就会返回该 Decimal，再经 `_serialize` 展开；② 财年定时器（`timer._apply_timer_to_company` → `_serialize`）对引用该值的 `timer_value="field:x"` 展开；③ FORMULA 节点里对该字段做指数运算（如 `x*1`）后同样落回 Decimal 分支。
- 后果：一次 PUT 触发 GB 级字符串分配与后续 `rstrip`/DB 写入/事件广播 → 单 worker 内存暴涨（Daphne 多 worker 下被打满一个进程即 OOM 重启，全站瞬时不可用）；即使侥幸不 OOM，请求也会长时间挂死（同步视图，无超时保护）。这是普通的 `company:manage` 账号就能发起的 DoS，不需要超管权限。
- 修复建议：`_parse_number_raw` / `_serialize` 对 Decimal 加指数与位数上限（如 `abs(raw.adjusted()) > 1000` 或 `len(digits) > 100` 即视为非法/裁剪），超限抛业务错误并按「单字段失败跳过」处理；DB 落库前对字符串长度设硬上限。

---

### [P2] O-06 tech_tree 前置依赖不校验存在性/自依赖/重复，且 create 不在事务内 —— 节点已落库但接口 500

- 位置：`backend/apps/tech_tree/serializers.py:53`
- 代码：
```python
    def create(self, validated_data: dict) -> TechNode:
        prerequisites = validated_data.pop("prerequisites", None) or []
        node = TechNode.objects.create(
            name=validated_data["name"],
            description=validated_data.get("description"),
            tier=validated_data.get("tier", 0),
            research_cost=validated_data.get("researchCost", 0),
            competition_id=validated_data["competitionId"],
        )
        if prerequisites:
            for p in prerequisites:
                pid = p.get("prerequisiteNodeId")
                if pid:
                    TechPrerequisite.objects.create(node=node, prerequisite_id=pid)
        return node
```
- 触发条件：`POST /api/tech-nodes`，`prerequisites:[{"prerequisiteNodeId": null}]` 之外的任意 id：不存在（如 `9999999`）、属于**另一个比赛**的科技节点、重复两次同一个 id、或在 `PATCH` 时填自己的 id（`backend/apps/tech_tree/serializers.py:83-88` 先 `instance.prerequisites.all().delete()` 再按 body 重建，可形成 A→A 自依赖，也可构造 A→B→A 环）。
- 后果：① 不存在的 id → 外键 IntegrityError（`PRAGMA foreign_keys=ON`）→ 500，且 `create` 的 `TechNode.objects.create` **没有包在事务里**（对比 `update` 用了 `transaction.atomic()`）→ 科技节点已落库、依赖关系没建、客户端收到 500，重试会撞 `unique_together(competition, name)` 再次 500，产生「幽灵节点」；② 跨比赛前置 → 节点列表序列化时 `prerequisite: {id, name}` 把其它比赛的科技名泄露出去，比赛数据被串味；③ 自依赖/环在契约层被完全接受（无任何环检测，对比 `industry_types._detect_calc_field_cycle` 对计算图做了环检测），前端科技树图会出现自指节点，`compute_tech_prerequisites`（`apps/contracts/engine.py:1447`）会把节点名当成自己的前置返回，形成永不可满足的研发条件。
- 修复建议：`create` 整体包 `transaction.atomic()`；建关系前批量校验 `TechNode.objects.filter(competition_id=..., id__in=pids).count() == len(set(pids))`，否则 400；显式拒绝 `precondition == node.id` 并用 DFS 做环检测（可复用 `industry_types` 的同款算法）。

---

### [P2] O-07 tech_tree `tier` 序列化器是 FloatField、模型是 IntegerField：静默截断 + NaN/超大值 500

- 位置：`backend/apps/tech_tree/serializers.py:17`（模型：`backend/apps/tech_tree/models.py:13`）
- 代码：
```python
    name = serializers.CharField(max_length=128, trim_whitespace=True)
    description = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    tier = serializers.FloatField(default=0)
    researchCost = serializers.FloatField(default=0)
    competitionId = serializers.IntegerField()
```
```python
    tier = models.IntegerField(default=0)
```
- 触发条件（已用本机解释器实测各分支）：
  - `PATCH /api/tech-nodes/<id>` body `{"tier": 1.5}` → 序列化器通过，`TechNode._meta.get_field('tier').get_prep_value(1.5)` → `1`（Django `IntegerField` 直接 `int()`）→ **静默向下取整**，「科技层级」被悄悄改小，前置关系与层级校验（若有）随之失真；
  - `{"tier": "nan"}` → 序列化器通过（实测 `validated_data={'tier': nan}`）→ `get_prep_value` 抛 `ValueError: Field 'tier' expected a number but got nan.` → 非 APIException → 500；
  - `{"tier": "inf"}` → 抛 `OverflowError: cannot convert float infinity to integer` → 500；
  - `{"tier": 9999999999999999999}` 或 `1e30` → `int(1e30)` 得到超 int64 的整数 → SQLite 驱动 `OverflowError: Python int too large to convert to SQLite INTEGER` → 500。
- 后果：等级/层级这类「必须整数且有小范围」的字段出现三类不一致行为（悄悄截断 / 500 / 脏值），`preparation/plan.py` 的层级体检与研发费用统计都按 `tier` 分组，取值被截断后统计口径与策划案不符且无人报警。
- 修复建议：改成 `serializers.IntegerField(min_value=0, max_value=99)`（与业务层级上限一致），`researchCost` 用 `DecimalField(max_digits=…, decimal_places=…)` + `min_value=0`。

---

### [P2] O-08 乐观锁默认关闭：`version` 缺省等于无条件更新，且已交付前端从不发送 version

- 位置：`backend/apps/company_fields/views.py:91`
- 代码：
```python
    expected_version = version if version is not None else fv.version
    updated = CompanyFieldValue.objects.filter(
        pk=fv.pk, version=expected_version
    ).update(
        value=value,
        version=expected_version + 1,
    )
    if not updated:
        raise FieldWriteConflictException()
```
- 触发条件：`PUT /api/company-fields/<cid>` 时 body 不带 `version`（`SetValuesSerializer.version` 是 `required=False`）。已核对前端：`frontend/src/api/index.ts:234` 的 `companyFieldsApi.set` 类型就是 `{industryFieldId, value}[]`，**全仓库检索无任何调用点发送 `version`**；`CompanyDetailView.vue` 的字段编辑已改为只读展示。于是 `version is None` → `expected_version = fv.version` → WHERE 恒命中 → 永不 409。
- 后果：两个管理员（或「管理员编辑基础字段」与「财年定时器/recompute 写同一字段」）并发写同一公司同一字段时后写覆盖先写，`FieldWriteConflictException`（409）在实践中不可达，模型里的 `version` 列只起到「自增计数器」作用，模块文档声称的乐观锁语义并不成立。商业模拟里这意味着并发填报会静默丢失一支队伍的填报结果。
- 修复建议：让 `version` 成为必填（或对「未提供」视为 0 并拒绝），后端在 `fields` 批量接口一次性返回各字段当前 `version`，前端回传；同时把「无条件覆盖」做成显式的 `force=true` 参数，避免默认即裸奔。

---

### [P2] O-09 字段值「先查后建」并发竞态：unique_together 冲突直接 500（无 get_or_create / 无重试）

- 位置：`backend/apps/company_fields/views.py:80`（同型：`backend/apps/company_fields/calc.py:322`、`calc.py:335`）
- 代码：
```python
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
```
- 触发条件：同一公司同一字段的两个并发首次写入（两个浏览器/两次快速双击、或「用户 PUT」与「财年定时器/合同落账重算」同时发生）。两者都查到 `fv is None`（迁移 `0002_initial.py:43` 确认存在 `unique_together(company, industry_field)`），第二个 `create` → `IntegrityError` → 500；由于该写包在 `transaction.atomic()` 里，整批 fields 一起回滚，用户看到「服务器内部错误」且不知道该重试什么。`calc._write_calc_value` 的两次重试只覆盖了「更新未命中」分支，`fv is None` 的创建分支同样裸奔。
- 后果：并发下随机 500（而非 409），前端把用户的整张填报表单判为失败；如果拿到锁的那一侧恰好是定时器/重算，用户侧数据还可能被覆盖。
- 修复建议：改用 `get_or_create` + `update`（捕获 IntegrityError 后重读再按乐观锁更新），或统一收敛到一个带 `select_for_update` 的写函数；把冲突翻译成 409 而非 500。

---

### [P2] O-10 级联重算在事务外：基础字段已提交、计算字段失败后永久不一致

- 位置：`backend/apps/company_fields/views.py:147`
- 代码：
```python
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
```
- 触发条件：`_recompute_calc_fields`（→ `calc.recompute_calc_fields`）被放在 `transaction.atomic()` **之外**（单字段接口 `views.py:174-176`、财年定时器 `timer.py:240-245` 同样如此）。`recompute_calc_fields` 自身也不带事务，而是逐字段 `_write_calc_value` 各自提交（`calc.py:419-432`），单字段异常只记 warning 后 `continue`。
- 后果：三类不一致都会长期留存（前端只展示计算值，用户看不到基础值已经变了）——① 重算过程中进程被重启/超时 → 部分计算字段更新、部分停在旧值；② 某个计算字段求值抛异常（如 O-05 的大数、除零、公式引用被删字段）→ 该字段永久保留旧值且无任何用户可见提示，只有服务端日志；③ 定时器场景里 `_apply_timer_to_company` 逐公司事务提交 + 逐公司重算，中途失败导致「同一比赛内各公司口径不同」。
- 修复建议：把「基础字段写入 + 级联重算」并入同一事务（注意大 batch 的锁时间），或引入「重算失败标记/待重算队列」并在字段上返回 `stale` 标记，至少让前端能感知；`recompute_calc_fields` 内部对失败字段汇总成返回值而不是静默 continue。

---

### [P2] O-11 唯一性「先查后插」普遍存在 TOCTOU：并发创建 → IntegrityError 500（无统一兜底）

- 位置：`backend/apps/competitions/views.py:113`（同型分布见下）
- 代码：
```python
        name = serializer.validated_data["name"]
        if Competition.objects.filter(name=name).exists():
            raise BusinessError("比赛名称已存在", code=409, status_code=409)
        serializer.save()
        return Response(CompetitionSerializer(serializer.instance).data)
```
- 触发条件：并发/重放两个相同唯一键的创建或改名请求。同类点（都已核对模型层有 `unique` / `unique_together`，但无 `IntegrityError` 捕获，全仓库 grep `IntegrityError` 零命中）：
  - `backend/apps/competitions/views.py:113`（`Competition.name` unique）、`:140`（改名）、`:192`（财年 `(competition, year)`）；
  - `backend/apps/industry_types/views.py:389`（`IndustryType.code` unique）、`:417`（改 code）、`:492`（`(industry_type, field_key)`）；
  - `backend/apps/companies/serializers.py:60`（同比赛同名公司，**且模型层没有唯一约束 → 见 O-16，并发下会直接产生两行同名公司，连 500 都不会有**）；
  - `backend/apps/regions/views.py:80`、`backend/apps/maps/views.py:72/115/161`（`unique_fields` + `_check_conflict`；`MapEdge` 的 `unique_fields` 为空，其「先查后插」在 `backend/apps/maps/serializers.py:187`，同上）。
- 后果：并发场景（双击提交、弱网重试、批量脚本）返回 500「服务器内部错误」而非 409，前端通常提示「失败」并诱导用户重试；对 `Competition.name` / `IndustryType.code` 这类全局唯一的字段，用户会陷入「明明没建成功却已存在」的死循环（因为第一次其实成功了一半）。
- 修复建议：这类检查一律「以 DB 约束为准」——直接 `create` 并 `except IntegrityError: raise BusinessError(..., code=409, status_code=409)`（放在 `transaction.atomic()` 内）；或在公共层给 `exception_handler` 增加 `IntegrityError → 409` 的兜底映射，避免任何遗漏点冒 500。

---

### [P2] O-12 industry_types PATCH 用**旧** fieldType 校验**新** config：可造出非法字典字段 / 误拒合法修改

- 位置：`backend/apps/industry_types/views.py:155`（调用点 `:545`）
- 代码：
```python
def validate_field(data: dict, effective_type: str | None = None) -> None:
    """字段联合校验：类型/config/计算图/定时器。"""
    field_type = effective_type or data.get("fieldType") or "NUMBER"
    ft = data.get("fieldType")
    if ft is not None and ft not in FIELD_TYPES:
        raise BusinessError(f"字段类型只能是 {' / '.join(FIELD_TYPES)}")
    if "config" in data:
        validate_field_config(field_type, data["config"])
```
```python
        validate_field(data, effective_type=field.field_type)
```
- 触发条件：`PATCH /api/industry-types/fields/<id>`，同时（或先后）改 `fieldType` 与 `config`。`effective_type` 是**库里的旧类型**，优先级还高于 body 里的新类型：`field_type = effective_type or data.get("fieldType")`。于是
  ① 旧类型 STRING、新类型 DICTIONARY、`config:{}` → `validate_field_config("STRING", {})` 走空分支什么都不校验 → 落库一个 `{"field_type":"DICTIONARY","config":"{}"}` 的字段（无 `entries`/`valueType`），前端字典编辑器与 `_field_to_dict` 拿到的 config 不合法；
  ② 旧类型 DICTIONARY、新类型 STRING、`config:{}` → 反向误拒（`entries 必须是数组`），用户无法把字典字段改回普通字段。
- 后果：字段定义进入前端无法渲染、引擎无法解析的状态（`_stored_to_raw` 对 DICTIONARY 会退化成 `{}`），且无法通过界面自愈（反向修改被误拒），需手工改库。
- 修复建议：`effective_type` 只在 body 未提供 `fieldType` 时生效：`field_type = data.get("fieldType") or effective_type or "NUMBER"`；`config` 的校验必须与「本次落库后的类型」一致。

---

### [P2] O-13 industry_types PATCH 可把名称改成空串（POST 有校验、PATCH 没有）

- 位置：`backend/apps/industry_types/views.py:419`
- 代码：
```python
        if "code" in data and IndustryType.objects.filter(code=data["code"]).exclude(pk=pk).exists():
            raise BusinessError(f"产业编号 {data['code']} 已被占用")
        if "name" in data:
            industry_type.name = data["name"].strip()
        if "code" in data:
            industry_type.code = data["code"]
```
- 触发条件：`PATCH /api/industry-types/<id>` body `{"name": "   "}`（或 `"\t\n"`）。已实测：`IndustryTypeSerializer` 对 `{"name": ""}` 会以 `blank` 拒绝（400），但对 `{"name": "   "}` **通过**（`CharField` 的 `allow_blank` 只判空串，`trim_whitespace=False` 又阻止了自动裁剪），随后视图里的 `.strip()` 把它变成 `""` 落库。`code` 同样没有范围校验（实测 `{"code": -5}` 通过；`code` 参与 `_next_code()` 的 `max+1`，负数会把后续自动编号带偏）。
- 后果：产业类型名变成空串后，公司详情/合同字段下拉/删除阻断提示（`f"「{c.name}」"`）都出现空白条目，用户无法辨认；因为 `name` 无唯一约束，多个空名产业类型并存后无法通过 UI 区分与修复。
- 修复建议：`if "name" in data: name = (data["name"] or "").strip(); if not name: raise BusinessError("产业类型名称不能为空")`；`code` 加 `min_value=1, max_value=99999`。

---

### [P2] O-14 maps `distance` 无范围校验（0/负数）：Dijkstra 不适用负权，合同路程费用可为负

- 位置：`backend/apps/maps/serializers.py:189`（模型 `backend/apps/maps/models.py:88`）
- 代码：
```python
        fid, tid = (from_id, to_id) if from_id < to_id else (to_id, from_id)
        if MapEdge.objects.filter(from_node_id=fid, to_node_id=tid).exists():
            raise BusinessError("这两个节点之间已存在路径", code=409, status_code=409)
        return MapEdge.objects.create(
            from_node_id=fid,
            to_node_id=tid,
            distance=validated_data.get("distance", 0),
            path_type_id=validated_data["pathTypeId"],
            competition_id=validated_data["competitionId"],
        )
```
- 触发条件：`POST /api/map-edges {"fromNodeId":1,"toNodeId":2,"distance":-1000000,"pathTypeId":1,"competitionId":1}`（`distance` 是裸 `FloatField`，可负、可 0、可 inf，见 O-04）；另外注意归一化写法的边界：`from_id == to_id` 时 `from_id < to_id` 为 False，`fid == tid == to_id` → **自环边**被正常创建。`PATCH /api/map-edges/<id> {"distance": -100}`（`backend/apps/maps/serializers.py:197-202`）同样放行。
- 后果：`compute_route_distance`（`apps/contracts/engine.py:1466-1488`）用 `_dijkstra` 求相邻节点最短路并求和，`_dijkstra`（`engine.py:1551-1569`）是标准 Dijkstra —— 遇到负权边时「已 settled 即返回」的提前返回会给出非最小结果，且总距离可能为负；路程距离直接喂给合同引擎的 `ROUTE_DISTANCE` 聚合（`engine.py:1717-1718`），于是运费/成本类条款可以算出负数（凭空产生资金），自环边还会污染路径类型收集。0 距离则让跨区域运输免费。
- 修复建议：`distance = serializers.FloatField(min_value=0.000001)`（或 `DecimalField`），并在 `create` 中显式拒绝 `from_id == to_id`；`compute_route_distance` 侧加「距离必须非负」的断言，避免脏数据静默进入结算。

---

### [P2] O-15 全量重算同步串行执行且跨比赛扫描：单请求可长时占用 worker（超时/雪崩）

- 位置：`backend/apps/companies/views.py:202`（同型：`backend/apps/industry_types/views.py:278`）
- 代码：
```python
        ok_count = 0
        failed: list[int] = []
        for cid in company_ids:
            try:
                recompute_calc_fields(cid)
            except Exception as e:  # noqa: BLE001 单公司失败不中断整体
                logger.warning(
                    "[companies] 全量重算：公司 #%s 失败：%s", cid, getattr(e, "message", e)
                )
                failed.append(cid)
                continue
            ok_count += 1
            emit_resource_changed("company-field", cid, competition_id, "updated")
```
- 触发条件：`POST /api/companies/recompute-all`（超管）在比赛公司数较多时；以及更常见的**非超管路径**：`PATCH /api/industry-types/fields/<id>` 改公式/默认值/类型时，`set(data) & _CALC_RELEVANT_KEYS` 为真即调用 `_recompute_industry_type`，它按 `industry_type_id` 扫描**所有比赛**的公司并逐公司重算（`backend/apps/industry_types/views.py:278-288`），而产业类型是全局资源。
- 后果：没有异步任务、没有分页/限流/超时控制，HTTP 请求线程被长时间占用：N 家公司 × 每公司「读字段值 + 逐计算字段求值（含业务查询，如 `CONSUMER_DEMAND` 每次聚合）+ 逐字段 UPDATE + 广播」。前端 drag 一下公式即可能触发全站级别的长事务与连接池耗尽（Daphne worker 被打满后其它接口一并超时）；`recompute-all` 的失败信息只落在响应 `failed` 数组里，前端目前也只读 `ok`。
- 修复建议：改为后台任务（管理命令/Celery/线程池）并把进度落到可查询的状态表；`_recompute_industry_type` 至少限定在「本次请求的比赛上下文」（或显式提示影响范围）；`recompute_calc_fields` 内的 `CONSUMER_DEMAND` 聚合做批量预取。

---

### [P2] O-16 公司重名判定只做「精确字符串」比较，且模型层无唯一约束：可并存同名公司 / 并发绕过

- 位置：`backend/apps/companies/serializers.py:56`
- 代码：
```python
    def validate(self, attrs: dict) -> dict:
        name = attrs.get("name") or (self.instance.name if self.instance else None)
        cid = attrs.get("competitionId") or (self.instance.competition_id if self.instance else None)
        if name and cid:
            qs = Company.objects.filter(competition_id=cid, name=name)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"name": "同一比赛下已存在同名公司"})
        return attrs
```
- 触发条件：`Company.name` 在模型上**没有任何唯一约束**（`backend/apps/companies/models.py:10`，仅 `indexes=[("competition","updated_at")]`），唯一性完全依赖这段 Python 校验：① 大小写不折叠（SQLite 默认 BINARY 排序规则）→ `"A队"`/`"a队"`、`"ALPHA"`/`"alpha"` 可并存；② 不做全角/半角、内部连续空格、零宽字符归一化 → `"ＡＢＣ"` 与 `"ABC"`、`"A 队"`（中间一个空格）与 `"A  队"`（中间两个空格）均可并存；③ 两个并发 create 都通过 `exists()` 检查（无 DB 约束兜底）→ 直接产生两行同名公司（连 O-11 的 500 都不会出现）。
- 后果：合同/股票/资金账户大量按「公司名」或按下拉选择引用公司，同名公司会让参与者选错对象、报表口径混乱，而且事后无任何约束阻止继续产生；比赛管理员无法通过重命名修好（改名也会与另一行冲突报错）。
- 修复建议：模型加 `unique_together(("competition", "name"))`（迁移前先清历史重复），校验时对名称做 `casefold()` + 空白归一化后再比较；如需保留「同名不同写法」的业务含义，则必须在展示层附带差异标识（如编号）。

---

### [P3] O-17 财年 year 无范围校验，`validate_year` 是死代码；超大/负数年份 500 或污染「当前财年」

- 位置：`backend/apps/competitions/serializers.py:110`
- 代码：
```python
    def validate_year(self, value: int) -> int:
        if value is None:
            raise serializers.ValidationError("财年不能为空")
        return value
```
- 触发条件：`year = serializers.IntegerField()`（`:93`）不带 `min_value/max_value`，而 DRF 在字段自带 `required=True` 时不可能把 `None` 传进 `validate_year`，这段校验永远不会命中。`POST /api/competitions/<id>/fiscal-years {"year": -1}`、`{"year": 0}`、`{"year": 99999999999999999999}` 均通过序列化器；后者在 SQLite 触发 `OverflowError: Python int too large to convert to SQLite INTEGER` → 500。
- 后果：负数/0 财年可入库，前端「当前财年」与财年切换（`fiscal-year:changed` 广播、`order_by("-year")`）会选中异常年份；超大值只能报 500，用户无法从提示中知道是数值越界。
- 修复建议：`year = serializers.IntegerField(min_value=1900, max_value=2999)`（或按业务实际范围），删掉无意义的 `validate_year`。

---

### [P3] O-18 N+1 查询：科技树前置逐条查、区域总览按区域逐个聚合

- 位置：`backend/apps/tech_tree/serializers.py:32`（同型：`backend/apps/regions/views.py:253-267`）
- 代码：
```python
    def to_representation(self, instance: TechNode) -> dict:
        prereqs = []
        for tp in instance.prerequisites.select_related("prerequisite").all():
```
- 触发条件：`GET /api/tech-nodes` 列表（`CrudListView` 只 `order_by`，无 `prefetch_related`），每个节点序列化时各发一次 `select_related` 查询；区域侧 `_get_map_overview` 对每个区域名调用 `_local_companies`（地图节点名 / location 字段 id / 字段值 / 公司，最多 4 次查询）与 `_resolve_cards`（公司 / 字段 / 字段值，3 次查询），区域数 N → 最多约 7N 次查询。
- 后果：分页上限 200（`MAX_PAGE_SIZE`）下科技节点列表最多 200 次额外查询；区域总览在地图节点区域较多时（几十个区域）每次打开地图页都是上百次查询，响应时间随数据量线性增长，比赛进行中（多人同时刷新地图）容易拖慢整站。
- 修复建议：`ListView` 加 `prefetch_related("prerequisites__prerequisite")`；`_get_map_overview` 一次性把全部区域的 `CompanyFieldValue`/`Company` 捞出来在内存里分组，而不是按区域反复查询。

---

### [P3] O-19 regions by-name 写入无任何名称校验，且 `request.data` 非 dict 时直接 500

- 位置：`backend/apps/regions/views.py:346`（同型：`:450`）
- 代码：
```python
        cleaned = _validate_cards(request.data.get("cards", []))
        region, _ = Region.objects.get_or_create(
            competition_id=competition_id,
            name=name,
            defaults={"overview_cards": "[]"},
        )
        region.overview_cards = json.dumps(cleaned, ensure_ascii=False)
        region.save()
```
- 触发条件：① `PUT /api/regions/by-name/%20/overview-cards` → `unquote` 得 `" "`，绕过 `RegionSerializer.validate_name` 的「名称不能为空」，创建空白名区域；超长名（>128）在 SQLite 下不报错直接入库（`max_length` 只在序列化器/表单层生效）；也不走 `_check_conflict`；② 请求体是 JSON 数组（`[]`）或纯字符串时 `request.data.get` → `AttributeError`（list 无 `.get`）→ 500；`SaveOverviewCardsView.put`（`:450`）与 `companies/views.py:123` 的 `request.data.items()` 同型。
- 后果：出现无法在地图上定位、也无法在区域列表里辨认的空白区域；畸形请求体返回 500 而不是 400，前端全局拦截器会弹出「服务器内部错误」，掩盖真实原因。
- 修复建议：by-name 路径复用 `RegionSerializer` 的 `validate_name`（并限制长度）；`request.data` 取值前统一 `isinstance(request.data, dict)` 判断，非法 body 返回 400。

---

### [P3] O-20 长文本字段无长度/条目数上限：单请求可写入超大 JSON 与超长字段值

- 位置：`backend/apps/industry_types/serializers.py:41`（同型：`backend/apps/company_fields/serializers.py:23`）
- 代码：
```python
    # 产业计算图（GGraph JSON 字符串）
    calcGraph = serializers.CharField(
        trim_whitespace=False, required=False, allow_null=True, allow_blank=True
    )
```
- 触发条件：`calcGraph` / `formula` / `timerValue` / `config` / `defaultValue` 在模型上是 `TextField`（无长度约束），序列化器也无 `max_length`；`SetValuesSerializer.fields`（`backend/apps/company_fields/serializers.py:23-25`）是 `ListField(..., allow_empty=True)`，**无 max_length**；`FieldValueItemSerializer.value` 也是无限长的 `CharField`。一次 `PUT /api/company-fields/<id>` 可携带几十万条 item 或单条 MB 级字符串。
- 后果：`transaction.atomic()` 内的循环写入 + 随后的全量重算（O-15）构成一个「一次请求打满 CPU/磁盘/内存」的放大入口；`calcGraph` 超大还会让每次字段变更触发的环检测与全量重算（`_detect_calc_field_cycle` 会解析全部兄弟字段的图）成本失控。
- 修复建议：给 `fields` 加 `max_length`（如 500）、`value` 加 `max_length`（如 10000）、`calcGraph` 加长度上限（如 256KB）并在校验层返回 400。

---

## 存疑/待确认

1. **[待确认] O-01 的实际触发面**：仓库内 `frontend/src/api/index.ts:267` 暴露了 `companiesApi.update`，但当前前端已无调用点（公司编辑页只支持创建/删除/只读详情）。该接口对任何直接调用 API 的客户端（脚本、移动端、后续版本）仍然生效，因此按真实缺陷上报；若产品确认该端点已下线，可降级为 P3。
2. **[待确认] O-03 的权限门槛**：`data:region:edit` 对 `COMPETITION_ADMIN` 属于「扩展集」（`backend/apps/common/permissions.py:326-332`，默认不授予，超管可按需放开）。若实际部署中从不放开，则触发者是「能编辑区域的账号」而非普通选手；但租户隔离失效本身仍然成立（跨比赛读取在 `_resolve_cards` 内无任何比赛过滤，与调用者角色无关）。
3. **[待确认] O-07 的 tier 语义**：模型是 `IntegerField` 而序列化器是 `FloatField`，无法从代码判断「层级允许小数」是否为有意设计（前端若允许输入 1.5 则截断会静默改数据）。建议向产品确认层级是否必须为整数。
4. **[待确认] O-14 负距离的业务影响面**：已确认 `distance` 无下界、`compute_route_distance` 会把它计入合同路程；具体「负数路程费」能造成多大金额影响取决于各比赛配置的合同类型公式（未在本次范围内逐条穷举）。
5. **[待确认] O-09/O-10 的并发窗口大小**：SQLite 默认单写者 + `transaction.atomic()` 会串行化写事务，因此 O-09 的「先查后建」竞态在 SQLite 部署下窗口较窄（但 O-10 的「重算在事务外」不受影响，仍然必现于任何失败场景）；若后续切换到 PostgreSQL/MySQL 多写连接，O-09/O-11 的触发概率会显著上升。
6. **[待确认] `company_list_scopes` 的作用域缺口**：`backend/apps/common/helpers.py:67` 只在用户**持有 `company:view`** 时才应用 `viewCompanyScopes`，因此「有 `company:manage` 但没有 `company:view`」的账号可对本比赛任意公司写入字段（`get_company_scoped` 的作用域过滤失效）。角色模板（`backend/apps/common/permissions.py:334-366`）中 `company:manage` 只作为扩展集单独授予超管放开，是否为真实可达组合需与权限配置确认。
