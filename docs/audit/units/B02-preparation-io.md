# B02 比赛准备（导出/导入/体检）（分支归属：origin/prep-export-import，合入 bugfix / feature_wuhaoye）

## 概述

**审计范围**（严格按分支归属，均为提交 602eab1 新增行）：

- 新增：`backend/apps/preparation/{__init__,apps,archive,checklist,plan,urls,views}.py`
- 修改（仅新增行）：`backend/backend/settings.py`（INSTALLED_APPS 加 `apps.preparation`）、`backend/backend/urls.py`（`path("api/", include("apps.preparation.urls"))`）——两处均为单行注册，无缺陷
- 新增：`frontend/src/components/preparation/PreparationImportDialog.vue`、`PreparationOverviewDialog.vue`
- 修改（仅新增行）：`frontend/src/api/index.ts`、`frontend/src/types/api.ts`、`frontend/src/views/settings/SettingsView.vue`
- 未审计（按分工）：`apps/preparation/builder/**`、`management/**`、`tests/**`

**经核对确认没有问题的点**（先排除，避免下游重复排查）：

1. **权限装饰器覆盖完整**：`views.py:150/162/177/203/233` 五个入口（plan GET、plan/export GET、scopes GET、archive/export GET、archive/import POST）全部带 `@require_permissions("competition:manage")`，且 `permission_classes = (IsAuthenticated, PermissionsPermission)`；`competition:manage` 列在 `apps/common/permissions.py:319-323` 的 `SUPER_ADMIN_ONLY_PERMISSIONS`，`has_permission` 对 SUPER_ADMIN 直接放行。**不存在漏加装饰器的入口**。
2. **dryRun 确实不落库（数据库层面）**：所有写操作都是 ORM 写，且全部在 `apply_import` 的同一个 `transaction.atomic()`（`archive.py:2657`）内，`dry_run` 时 `transaction.set_rollback(True)`（2675-2676）。审计/实时广播走 `apps/common/signals.py` → `apps/realtime/emit.py:244 _after_commit` → `transaction.on_commit`（`signals.py:10-13` 有明确说明），回滚即取消投递；本模块不写文件、不落外部副作用。所以"预览留痕"不成立——但见 R-01：异常路径下 `dry_run=false` 的**真实**导入会静默全回滚而仍报成功。
3. **账号归档不含密码**：`_exp_users`（`archive.py:1092-1133`）只导出 username/displayName/role/isActive/permissions/四套范围，无 `password` 字段；导入侧新建账号用 `secrets.token_urlsafe(16)` + `must_change_password=True`（2464-2470），**导入后无法直接登录**，需超管重置。
4. **科技前置成环检测不会死循环**：`plan.py:831-850` 是三色标记 DFS，GRAY 命中即记环，节点最多访问一次（无 `stack.index` 之外的复杂度问题）。
5. **体检的除法/分母**：`plan.py` 内所有除法分母都是常量（`round(x, n)`、`/1024/1024`），`engine.py:532` 的 `sum/len` 有 `if vals` 保护，未发现分母为 0。

**核心风险结论**：本模块的"安全默认"（dryRun 预览、非空拒绝、无密码）实现是真的；真正的缺陷集中在**导入侧的异常/事务处理**、**"追加模式保留已存在对象"的父子跳过规则**（把不该跳过的整类数据静默丢掉）、以及**主键/自然键重映射的顺序与范围**上。P0 无，P1 四条，其余见清单。

---

## 缺陷清单

### [P1] R-01 导入循环吞掉数据库异常且无 savepoint：一次 DB 错误即污染整个事务，接口仍返回 200 与"新增 N"的成功统计

- 位置：`backend/apps/preparation/archive.py:2657-2676`（配合 `archive.py:1637`）
- 代码：
```python
    with transaction.atomic():
        for res in ordered:
            rows = (resources.get(res) or {}).get("rows") or []
            ...
            try:
                fn(rows, ctx)
            except Exception as e:  # noqa: BLE001 - 单资源失败不中断整批
                ctx.problem(f"资源 {RESOURCE_LABELS.get(res, res)} 导入失败：{type(e).__name__}: {e}")
                ctx.bump(res, "skipped", len(rows))
        if dry_run:
            transaction.set_rollback(True)
```
```python
# archive.py:1637  _simple_lookup_import 的建库分支（未用 get_or_create 包裹）
        obj = model.objects.create(competition_id=ctx.competition_id, name=name, **defaults)
```
- 触发条件：`_simple_lookup_import` 的 `defaults` 只收录"行里存在且非 None"的字段（1616-1620），因此归档中 `warehouses` 缺 `capacity/price/type`、或 `fuel` 缺 `pricePerLiter`、或 `productionLines` 缺 `laborCount`、或 `infrastructures` 缺 `footprint/price/activationPrice` 时，`create()` 会缺 NOT NULL 列 → `IntegrityError`（这些字段都是 NOT NULL 且无 default：`warehouses/models.py:17-19`、`fuels/models.py:10`、`production_lines/models.py:10-12`、`infrastructures/models.py:10,16,20`）。另一个自然触发点是并发写：本项目是 SQLite（`settings.py` DATABASES ENGINE=sqlite3，默认 5s lock timeout）+ daphne 多写者，导入长事务期间任何其它写请求（定时器/广播落库）都可能让事务内的 SQL 抛 `OperationalError: database is locked`。**注意 `get_or_create` 内部自带 savepoint 能自愈，而裸 `create()` 不能**。
- 后果：`DatabaseError` 会把连接的 `needs_rollback` 置位。异常被 `except Exception` 吞掉后循环继续，此后每一条 SQL 都抛 `TransactionManagementError: An error occurred in the current transaction...`，被同一个 except 记成"资源 X 导入失败"（用户看到的是一堆莫名其妙的失败原因）；而 `with transaction.atomic()` 退出时没有异常抛出、只有 `needs_rollback=True`，Django 走的是"回滚整个事务"分支（不抛错）。最终 **HTTP 200 + `created/updated` 统计齐全**，前端弹"导入完成：新增 N"，但数据库里什么都没写（或在异常点之前的资源被提交、之后的全部丢失，取决于 Django 版本细节，见"存疑"）。这是本模块最危险的静默数据丢失路径。
- 修复建议：把每个资源的导入放进独立的 `with transaction.atomic():`（savepoint 语义）内，异常只回滚该资源并在 problems 里注明；对 `DatabaseError` 不要继续循环，直接 `raise`（让整个请求失败并返回 5xx/400 明确报错）；总数统计必须在真正 commit 之后再组装。可复用项目已有的 `suppress_signals` 思路统一收口。

### [P1] R-02 追加模式下"合同实例"因全局合同类型已存在而被整类跳过：预设合同一份都不会导入

- 位置：`backend/apps/preparation/archive.py:1525-1538`（规则）、`2127`（父资源被标记 kept）、`1552-1587`（统一跳过）
- 代码：
```python
_CHILD_OF: dict[str, tuple[tuple[str, str], ...]] = {
    ...
    "contractInstances": (("contractTypes", "contractTypeId"),),
```
```python
    for row in rows:
        skip = False
        for owner_resource, field in fk_specs:
            if ctx.ids.kept(owner_resource, row.get(field)):
                skip = True
                break
        ...
        if skip:
            kept += 1
        else:
            out.append(row)
```
- 触发条件：默认 `mode=append`。`contractTypes` 是**全局资源**（`GLOBAL_RESOURCES`，跨比赛共享，见 `archive.py:171`）：同一套部署里第二个比赛导入时，`_imp_contract_types` 必然走 `get_or_create` 命中已存在分支 → `keep_existing_id(...)`（2127）把它登记进 `_kept`。随后 `contractInstances` 的行因 `ctx.ids.kept("contractTypes", contractTypeId) == True` 被整行丢弃。导出侧确实带了 `contractTypeId`（`_exp_contract_instances`，996）。
- 后果：只要库里已有同名合同类型（同一数据库的第二场比赛、或复用历史比赛时几乎必然），**所有预设合同实例都不会创建**，结果只记一条中性 note（"有 N 条因所属对象已存在而保留未改动"），不是 problem。用户以为预置合同导入成功，实际比赛开局没有可履约合同。同类问题见 R-04（users/companies）。
- 修复建议：`_CHILD_OF` 只应表达"子数据归属的**比赛级**父对象"，全局资源（contractTypes）与"引用型"父（users 的 companyScopes）必须移出该表；跳过判定应基于子资源自身的自然键是否已存在，而不是父对象是否被保留。

### [P1] R-03 同类型多份合同被合并成一条且被改名：合同实例导入丢数据

- 位置：`backend/apps/preparation/archive.py:2168-2179`（对照导出 `977-1003` 的 `name=c.name`）
- 代码：
```python
        obj = Contract.objects.filter(
            competition_id=ctx.competition_id, contract_type_id=ct_id, name=ct.name if ct else (row.get("name") or "合同")
        ).first()
        if obj is None:
            obj = Contract.objects.create(
                competition_id=ctx.competition_id,
                contract_type_id=ct_id,
                name=ct.name if ct else (row.get("name") or "合同"),
                status=row.get("status") or "DRAFT",
```
- 触发条件：归档里同一 `contractTypeKey` 下有多份合同（这是常态：一个合同类型可预置多份不同参与方/编号的合同，`Contract.name` 是实例名，模型无唯一约束也无 ordering，见 `contracts/models.py:64`）。去重键用的是**合同类型的名字** `ct.name`，而不是行里的 `row["name"]`。
- 后果：第 1 行创建 `name = 合同类型名`（丢掉原实例名），第 2..N 行被 `filter(...).first()` 命中同一对象 → 全部 `skipped`，并被 `ctx.ids.put("contractInstances", old_id, obj.id)` 映射到同一个新合同。结果：N 份合同变 1 份、名称被改写、其余参与方（已映射好的 `parties`）**算完直接丢弃**（2181-2183 的分支只 bump skipped，注释声称"只补参与方公司引用"但代码没有任何回写）。
- 修复建议：去重键改为 `(competition, contract_type, name)` 且 `name` 取 `row.get("name")`；已存在分支要么按 overwrite 语义更新 `parties/inputs/status`，要么至少把参与方映射结果写回。

### [P1] R-04 追加模式下账号被"引用的公司已存在"整行跳过：分步导入时一个账号都不会创建

- 位置：`backend/apps/preparation/archive.py:1541-1549`（规则）+ `1552-1587`（跳过）
- 代码：
```python
_CHILD_OF_LIST: dict[str, tuple[tuple[str, str], ...]] = {
    # users 的公司范围引用 companies（列表）
    "users": (
        ("companies", "companyScopes"),
        ("companies", "viewCompanyScopes"),
        ("companies", "contractViewCompanyScopes"),
        ("companies", "stockCompanyScopes"),
    ),
}
```
```python
            for owner_resource, field in list_specs:
                parents = row.get(field)
                if isinstance(parents, list) and any(
                    ctx.ids.kept(owner_resource, p) for p in parents
                ):
                    skip = True
                    break
```
- 触发条件：`mode=append`（默认）且目标比赛里已存在账号范围里引用的**任意一家**同名公司。这正是产品设计的推荐流程：先"导入本组"③参赛主体、再"导入本组"⑧账号与权限；此时 `_imp_companies` 走 `keep_existing_id`（1434）把公司登记为 kept。
- 后果：`users` 的每一行（只要范围里含这家已存在的公司）被整行丢弃 → **账号一个都不建**，`ctx.ids` 里也没有 user 映射（连带 R-06、R-07 的引用全部落空）。用户看到的是 note："账号与权限：追加模式下有 N 条因所属对象已存在而保留未改动"——但账号根本不存在，属于误导性提示。
- 修复建议：`_CHILD_OF_LIST` 的语义应改为"仅跳过与该父对象相关的子数据字段"，而不是跳过整行；对 users 应该照常建号（账号的存在性只取决于 username），只在范围合并时避免污染既有公司的数据。

### [P2] R-05 competitionMeta 用归档里的 status 覆盖目标比赛状态：与实现声明相矛盾

- 位置：`backend/apps/preparation/archive.py:1229-1246`（对照函数 docstring `1223`）
- 代码：
```python
def _imp_competition_meta(rows: list[dict], ctx: ImportContext) -> None:
    """比赛名称/状态/背景图：导入时只在目标比赛上补空缺，不覆盖已有名称。"""
    ...
    if src.get("status") and comp.status != src["status"]:
        comp.status = src["status"]
        changed.append("status")
```
- 触发条件：归档来自一场 `CLOSED` 的比赛（复用上一届数据是主要用法），目标是新建的 `ACTIVE` 比赛；`competitionMeta` 资源在前端默认勾选列表里（count>0 即被 `applyDefaultSelection` 选中）。
- 后果：目标比赛状态被静默改成 `CLOSED`（`competitions/models.py:8` 只有 ACTIVE/CLOSED），前端"比赛准备"页与比赛列表的进行中标记随之改变，且该写入绕过了正常的比赛更新入口；反向（CLOSED→ACTIVE）同样会发生。文档/提交信息声称"只在目标比赛上补空缺"，实际覆盖了状态且没有任何提示。
- 修复建议：状态是否覆盖应作为显式导入选项（默认不覆盖），或至少在 `ctx.note/problem` 里给出"源比赛状态为 CLOSED，目标比赛状态未变"的提示。

### [P2] R-06 `IMPORT_ORDER` 把 `users` 放在最后，两个消费方在它之前执行 → 用户引用永远解析不到

- 位置：`backend/apps/preparation/archive.py:161-167`（顺序）、`2242`（资金账户）、`2334-2341`（消息收件人）；`_REF_FALLBACKS`（333-346）中没有 `users`
- 代码：
```python
    "stocks",
    "stockFundsAccounts",
    "contractInstances",
    "overviewCards",
    "messages",
    # 账号
    "users",
]
```
```python
        user_id = ctx.ref.resolve("users", row.get("userId"), name=row.get("username"))   # archive.py:2242
```
```python
        old_ids = _json_list(row.get("targetUserIds"))
        new_ids = []
        for old in old_ids:
            mapped = ctx.ids.get("users", old)      # users 尚未导入 → 永远 None
            if mapped is None:
                continue
```
- 触发条件：任何一次导入（含全部/市场/账号分组的任意组合）。
- 后果：(1) `ownerType=USER` 的资金账户被建成 `user_id=None`（2249），归属静默丢失且不记 problem；(2) 消息的指定收件人列表被清空成 `[]`，只留一条 note；(3) `_CHILD_OF["stockFundsAccounts"] = (("users","userId"),)`（1536）这条父规则同样永不生效。
- 修复建议：把 `users` 提到 `stocks/stockFundsAccounts/contractInstances/messages` 之前（账号只依赖 companies），或给 `_REF_FALLBACKS` 补 `users`（按 username 兜底）并让消费方按名解析。

### [P2] R-07 导入的消息不创建 `MessageRecipient`：收件箱与未读数永远看不到这些消息

- 位置：`backend/apps/preparation/archive.py:2346-2354`（对照 `messages/models.py:3-5`、`messages/views.py:151,187,372`）
- 代码：
```python
        Message.objects.create(
            competition_id=ctx.competition_id,
            title=title,
            content=row.get("content") or "",
            sender_id=sender.pk,
            targets_all=bool(row.get("targetsAll")),
            target_user_ids=json.dumps(new_ids, ensure_ascii=False),
        )
        ctx.bump("messages", "created")
```
- 触发条件：导入任意包含 `messages` 的归档。
- 后果：模型注释明确"收件箱与未读状态以 MessageRecipient 为权威来源"，而 `InboxView`（`MessageRecipient.objects.filter(user_id=...)`）、`UnreadCountView` 都只读 `MessageRecipient`；该资源导入只写 Message 行，从不 `bulk_create` 收件人（发消息视图在 `views.py:372` 才做）。结果：导入的消息对**任何角色都不可见**（包括 `targetsAll` 的全体消息），比赛内消息资源实际完全无效，而统计里显示"消息 新增 N"。
- 修复建议：导入时按 `targets_all`/收件人范围结算并 `bulk_create` MessageRecipient（可复用 messages 视图里的收件人解析逻辑），并在 targetsAll 但收件人无法确定时记 problem。

### [P2] R-08 追加模式覆盖既有区域的概览卡片：`_CHILD_OF` 用了导出侧不存在的字段，且导入侧无条件写回

- 位置：`backend/apps/preparation/archive.py:1537`（死规则）+ `2295-2302`（无条件 save）
- 代码：
```python
    "overviewCards": (("regions", "regionId"),),
```
```python
        region, created = Region.objects.get_or_create(
            competition_id=ctx.competition_id, name=region_name, defaults={"overview_cards": "[]"}
        )
        if created:
            ctx.bump("overviewCards", "created")
        region.overview_cards = json.dumps(kept, ensure_ascii=False)
        region.save(update_fields=["overview_cards", "updated_at"])
```
- 触发条件：`mode=append`，目标比赛已有同名区域且该区域已配置概览卡片；归档中该区域的卡片引用的公司不在包内时 `kept` 会进一步变少（2277-2289 直接丢弃卡片）。
- 后果：`_exp_overview_cards`（1064-1073）只导出 `_id/regionName/cards`，**没有 `regionId`**，`ctx.ids.kept("regions", None)` 恒为 False → R-04 那套"保留不动"保护对本资源完全失效；随后 2300-2301 无条件把 `overview_cards` 覆盖为归档值（可能是被裁掉的子集甚至 `[]`）。用户在追加模式下期望"已存在的整块保留"，实际既有卡片被清空/替换，股票碳排与幸福度的卡片引用随之失效（`stock/engine.py:507-534` 会回退到 current 值）。
- 修复建议：导出侧补 `regionId`（或改按 `regionName` 判定 kept），并且只有当 `created` 或 `ctx.is_overwrite` 时才写 `overview_cards`。

### [P2] R-09 自然键兜底在重名公司上会绑错对象；重名公司还会让"公司"整类资源导入失败

- 位置：`backend/apps/preparation/archive.py:391-401`（按名兜底）、`1422-1430`（`get_or_create`）；`companies/models.py` 无 `(competition,name)` 唯一约束；`plan.py:358-361` 明确把"同名公司"当作正常状态告警
- 代码：
```python
        if name:
            qs = model.objects.filter(**{name_field: name})
            try:
                qs = qs.filter(competition_id=self.ctx.competition_id)
            except Exception:  # noqa: BLE001 - 模型无 competition 字段
                pass
            found = qs.values_list("id", flat=True).first()
            if found is not None:
                self.ctx.ids.put(resource, old_id, found)
                return found
```
```python
        obj, created = Company.objects.get_or_create(
            competition_id=ctx.competition_id,
            name=name,
```
- 触发条件：目标比赛存在两家同名公司（`_c_companies` 会专门提示"存在同名公司（不同 ID）"，说明这是可预期状态）。
- 后果：(1) `RefResolver.resolve('companies', ..., name=...)` 只取 `.first()`（无排序约束），公司字段值（1461）、合同参与方（2157）、概览卡片（2282）、股票归属（2196）、资金账户（2241）、账号范围（2369）都可能静默挂到**另一家同名公司**上；(2) `get_or_create` 内部 `self.get(**kwargs)` 抛 `MultipleObjectsReturned`（不是 DB 错误，不能被自愈），被 `apply_import` 的 per-resource except 捕获 → **整个 companies 资源全部跳过**，只留一条"资源 公司 导入失败：MultipleObjectsReturned"。
- 修复建议：`resolve` 用 `filter(...).order_by("id")` 并在命中多条时记 problem（或返回 None 让调用方跳过）；`_imp_companies` 改为 `.filter(...).order_by("id").first()` + `create()` 的显式写法，避免 `get_or_create` 的多结果异常。

### [P2] R-10 overwrite 模式对 mapNodes / regions.description / stocks / stockFundsAccounts 实际不更新（与"按归档更新字段"的承诺不符）

- 位置：`backend/apps/preparation/archive.py:1697-1699`、`1518-1520`、`2222-2224`、`2256-2258`
- 代码：
```python
        if created:
            ctx.bump("mapNodes", "created")
        else:
            ctx.bump("mapNodes", "skipped")       # 覆盖模式同样不更新 x/y/node_type/region
        ctx.ids.put("mapNodes", row.get("_id"), obj.id)
```
```python
        if created:
            ctx.bump("stocks", "created")
        else:
            ctx.bump("stocks", "skipped")         # 覆盖模式同样不更新价格/股本/公司/轮次
        ctx.ids.put("stocks", row.get("_id"), obj.id)
```
- 触发条件：用户在前端选择"覆盖（已存在的按归档更新）"并期望把源比赛的股票行情参数/资金账户初始现金/节点坐标刷新到目标比赛。
- 后果：这四类资源在已存在时永远走 `skipped` 分支，归档值被忽略；其中"资金账户初始现金"是清单里明确强调"开赛前是最后一次可自由设定"的字段（`checklist.py:414-426`），"股票初始价/总股本"同理。用户会以为覆盖成功（统计显示 skipped 而非 problem，rollup 里 `updated=0` 也容易被忽略）。
- 修复建议：统一走 `_simple_lookup_import` 的 `touched/updated` 分支，或在资源不支持覆盖时明确报 problem/note，而不是静默 skipped。

### [P2] R-11 归档导出把明细静默截断到 2000 行，`count` 却是全量：大比赛导入丢数据且无任何提示

- 位置：`backend/apps/preparation/archive.py:39`、`1195-1199`
- 代码：
```python
# 明细上限：与 plan.py 保持一致，避免导出文件失控（导入不受此限制影响）
_DETAIL_LIMIT = 2000
```
```python
        resources[res] = {
            "label": RESOURCE_LABELS.get(res, res),
            "count": len(rows),
            "rows": rows[:_DETAIL_LIMIT],
        }
```
- 触发条件：某资源行数 > 2000（`companyFieldValues`＝公司×字段、`partMaterials`、`consumerDemands`、`messages`、`contractInstances` 等在大比赛里很容易超过；20 家公司 × 100 字段即 2000）。
- 后果：导出文件只含前 2000 行，`count` 却是真实条数；导入侧 `validate_archive` 不校验 `count` 与 `rows` 是否一致，直接按 rows 导入，既不报 problem 也不提示被截断。前端归档条数列（`PreparationImportDialog.vue:111`）显示 count（3000），实际只导入 2000 —— 用户以为全量搬运完成。
- 修复建议：截断时写入 `truncated: true` / `dropped: n` 字段，导入侧检测到即记 problem（"归档不完整，已导入前 2000 条"）；或按资源分片导出。

### [P2] R-12 "非空比赛"判定探针不全：只含消息/财年/节点类型等数据的比赛被当成空比赛，跳过 allowNonEmpty 保护

- 位置：`backend/apps/preparation/archive.py:2542-2561`、`2621-2644`
- 代码：
```python
_NON_EMPTY_PROBES: list[tuple[str, str]] = [
    ("公司", "companies.Company"),
    ("原料", "materials.Material"),
    ...
    ("消费者需求", "consumer_demands.ConsumerDemand"),
    ("参赛账号", "users.User"),
]
```
- 触发条件：目标比赛只存在以下任一类数据（全部不在探针里）：`messages.Message`（比赛内消息，导入资源之一）、`competitions.FiscalYear`、`maps.MapNodeType` / `maps.PathType`、`maps.MapEdge`、`tech_tree.TechPrerequisite`、`regions` 的 `overview_cards`（此条被 Region 探针覆盖）。
- 后果："默认拒绝非空比赛"这道安全闸被绕过：这类比赛被判为"空"，导入在**没有 allowNonEmpty 确认**的情况下直接写入，与 R-05 组合后可能把正在使用的比赛改状态/混数据。注意 dryRun 预览此时显示 `blocked=false`，前端也不会提示风险。
- 修复建议：探针表补全（Message/FiscalYear/MapNodeType/PathType/MapEdge/TechPrerequisite），或改为按"该比赛下所有带 competition 外键的模型"动态枚举。

### [P2] R-13 消费者需求把 `quantity` 放进去重键：改数量后重复导入会追加出重复需求

- 位置：`backend/apps/preparation/archive.py:2076-2084`
- 代码：
```python
        quantity = row.get("quantity") or 0
        # 自然键（比赛 + 区域 + 产品类型 + 数量）去重：合并导入时不重复追加需求
        obj, created = ConsumerDemand.objects.get_or_create(
            competition_id=ctx.competition_id,
            region=region,
            product_type=product_type,
            quantity=quantity,
            defaults={"product_id": product_id, "note": row.get("note")},
        )
```
- 触发条件：源比赛把某区域某产品的需求量从 100 改成 150 后重新导入（或归档里同一 (region, product_type) 存在两个不同数量的条目）。
- 后果：模型对 (competition, region, product_type) 没有唯一约束（`consumer_demands/models.py:30` 只有索引），新数量的行会被**新建**而不是更新既有行 → 同区域同产品出现两条需求（100 与 150），订单/需求总量被放大。overwrite 模式也一样（去重键不随 mode 变化）。
- 修复建议：去重键去掉 `quantity`（用 competition+region+product_type），命中后按 overwrite 语义更新 `quantity/product_id/note`。

### [P2] R-14 `plan.py` 的明细上限常量从未使用：/preparations/plan 返回全量明细，前端全量渲染

- 位置：`backend/apps/preparation/plan.py:28`、`107-115`（对照前端 `PreparationOverviewDialog.vue:327-329`）
- 代码：
```python
# 明细行上限（超出只记录条数，避免导出过大）
_DETAIL_LIMIT = 200
...
def _detail(title: str, columns: list[str], rows: list[list]) -> dict:
    """构造明细表：超出上限的行被截断，但保留总条数供导出时说明。"""
    total = len(rows)
    return {"title": title, "columns": columns, "rows": rows, "total": total}
```
- 触发条件：任何一次打开"比赛准备总览"（`load()` 每次都拉全量 plan）。全仓 grep 显示 `_DETAIL_LIMIT` 只有定义、没有任何引用。
- 后果：模块 docstring（第 10 行）承诺的"超出部分以『…还有 N 条』提示"不成立；`total == len(rows)` 恒成立，前端 `isTruncated()` 永远 false，"仅显示前 N 条"分支是死代码。明细表按记录数增长（`_c_messages`/`_c_users`/`_c_scopes`/`_c_map_edges`/`_c_parts` 的 extraTable 等），响应体无界；前端 `el-table` 未开虚拟滚动（`PreparationOverviewDialog.vue:190-200`，只有 max-height），几千行的表会显著卡顿甚至卡死主线程。Markdown 导出（`plan.py:1553-1554`）同样逐行输出全量。
- 修复建议：在 `_detail()` 里真正应用 `_DETAIL_LIMIT`（`rows[:limit]`，`total=len(rows)`），或给 el-table 换虚拟滚动并分页拉明细。

### [P3] R-15 未勾选的资源仍会被创建，与前端"未勾选的资源会原样保留、不受影响"的承诺不符

- 位置：`backend/apps/preparation/archive.py:1418-1421`、`1672-1680`、`1717-1725`、`2295-2297`；前端 `PreparationImportDialog.vue:96-98`
- 代码：
```python
            if region_id is None:
                # 区域不在本包内（只导了 company 分组）：按名建一个，保证归属不丢
                region_id = Region.objects.create(competition_id=ctx.competition_id, name=region_name).id
                ctx.note(f"区域「{region_name}」不在导入包内，已按名称自动创建")
```
```python
            if type_id is None:
                type_id = MapNodeType.objects.create(
                    competition_id=ctx.competition_id, name=type_name
                ).id
                ctx.note(f"节点类型「{type_name}」不在导入包内，已自动创建")
```
- 触发条件：只勾选 `companies`（不勾 `regions`）、只勾 `mapNodes`（不勾 `mapNodeTypes`）、只勾 `overviewCards`（不勾 `regions`）。
- 后果：`only_resources` 只在主循环里过滤（2652），这些"顺手补建"绕过勾选，向目标比赛写入用户明确未选择的资源类型；与前端提示"未勾选的资源会原样保留、不受影响"直接冲突，也让 dryRun 预览的"跳过资源"清单（`skippedResources`）与实际不符。
- 修复建议：补建前检查 `ctx.wants(resource)`，不允许时记 problem 并跳过该行；前端提示同步改为"可能自动补建被引用的对象"。

### [P3] R-16 `rows` 元素类型未校验，且 `_filter_rows_for_mode` 在 try 之外 → 非对象行触发未捕获异常（500）

- 位置：`backend/apps/preparation/archive.py:2531-2538`（只校验是 list）、`1564-1577`（`row.get`）、`2667`（在 try 之外调用）
- 代码：
```python
        rows = block.get("rows")
        if rows is not None and not isinstance(rows, list):
            raise ArchiveError(f"资源 {res} 的 rows 必须是数组")
```
```python
            rows = _filter_rows_for_mode(res, rows, ctx)
            if not rows:
                continue
            try:
                fn(rows, ctx)
            except Exception as e:  # noqa: BLE001 - 单资源失败不中断整批
```
- 触发条件：手工构造/第三方生成的归档里某资源 `rows` 为 `["x"]` 之类的字符串数组，且该资源出现在 `_CHILD_OF`/`_CHILD_OF_LIST`（partMaterials、partTechRequirements、productParts、productTechRequirements、vehiclePathTypes、companyFieldValues、techPrerequisites、mapEdges、contractInstances、stockFundsAccounts、overviewCards、users）。
- 后果：`_filter_rows_for_mode` 里的 `row.get(field)` 抛 `AttributeError: 'str' object has no attribute 'get'`，这条路径**不在** per-resource try 内，异常直接冒泡出 `apply_import` → 500（`views.py` 只把 `ArchiveError` 转成 400）。事务随之整体回滚（无脏数据），但错误信息不可读；其它资源因为被 try 包住，只表现为"资源 X 导入失败"。
- 修复建议：`validate_archive` 里校验每个 row 是 dict（不符即 `ArchiveError`），或把 `_filter_rows_for_mode` 也纳入 try。

### [P3] R-17 导入未使用 `suppress_signals`：逐行审计落库 + 逐行实时广播

- 位置：`backend/apps/preparation/archive.py:2657-2674`（循环内逐行 create/save）；对照 `backend/apps/common/signals.py:39-49`
- 代码：
```python
class suppress_signals:
    """上下文管理器：临时关闭所有写操作信号（审计 + 广播）。
    ...
    典型场景：stock advance_round 对 StockOrder/Holding/Candle 大量 save，
    若不抑制会每 save 都发出 resource:changed（单条），前端会收到上百条事件。
    """
```
- 触发条件：任何一次真实导入（数千行级别）。
- 后果：`apps/common/signals.py:201-202` 为 User/Company/Message 等模型都挂了 post_save，每行都会写一条审计记录（同事务内）+ 通过 `on_commit` 排一条 `resource:changed` 广播。导入 3000 行 → commit 后瞬间推送约 3000 条 socket 事件、审计表增 3000 行；前端若按事件刷新列表会被打爆，且事务内多出的 3000 条审计写会显著加长 SQLite 写锁持有时间（与 R-01 的 `database is locked` 相互放大）。
- 修复建议：导入循环用 `with suppress_signals():` 包裹，结束后一次性 `emit_resource_changed(..., "bulk")`（项目内已有此约定）。

### [P3] R-18 导入向导状态不同步：预览后勾选状态与实际提交的资源集合可能不一致，改勾选后不会重新预览

- 位置：`frontend/src/components/preparation/PreparationImportDialog.vue:302-315`、`415-418`、`426-438`
- 代码：
```js
    const res = (await preparationApi.importArchive(payload.value, {...})) as PrepImportResult;
    // 预览结果会带目标比赛占用情况（首次预览通常为空，因为默认拒绝非空比赛）
    rebuildResourceRows(res.occupancy || []);
    // 目标比赛是空的 → 覆盖与追加等价，统一按追加语义展示
    preview.value = res;
```
```js
    const res = (await preparationApi.importArchive(payload.value, {
      scope: scope.value,
      competitionId: props.competitionId ?? null,
      config: currentConfig(false),      // resources: selectedResources.value
    })) as PrepImportResult;
```
- 触发条件：预览成功后（步骤 2）增/减勾选任何资源，然后直接点"确认导入"。
- 后果：`rebuildResourceRows()` 用**全新对象**替换 `resourceRows`，但没有重新同步 `selectedResources`、也没有重放 `toggleRowSelection`（对比 `applyDefaultSelection` 的做法）。于是表格勾选框的显示与"已选 N/M 类"计数、以及真正提交的 `resources` 三者可能不一致（Element Plus 的 selection 以行对象身份为准，数据数组被替换后旧选择集失效）。另外确认导入时不校验预览是否对应当前配置，界面展示的"新增/更新/保留/问题"是旧配置的预览结果。见"存疑"第 3 条：具体渲染表现需实测确认，但"提交集合与展示集合没有强一致性保证"是确定的。
- 修复建议：预览后固定一次勾选快照，确认导入时提交该快照；任何勾选变更都置空 `preview` 并要求重新预览（或在确认时重新 dryRun 一次再落库）。

### [P3] R-19 归档整体作为一个 JSON body 传输，前后端都没有大小限制

- 位置：`frontend/src/components/preparation/PreparationImportDialog.vue:366-388`；`frontend/src/api/index.ts` 的 `importArchive`（把整个归档放进请求体）；`backend/apps/preparation/views.py:238-242`
- 代码：
```js
  fileSizeText.value = file.size > 1024 * 1024
    ? `${(file.size / 1024 / 1024).toFixed(2)} MB`
    : `${Math.max(1, Math.round(file.size / 1024))} KB`;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const obj = JSON.parse(String(reader.result));
```
- 触发条件：用大比赛的"导出全部 JSON"（`_DETAIL_LIMIT=2000` 上限下仍可达数十 MB）再导入；前端只把大小**展示**出来，从不校验。
- 后果：`FileReader.readAsText` 把整文件读成字符串、`JSON.parse` 在主线程同步解析、序列化后再整包发出（预览一次、导入再一次），大文件会卡死页面；后端 DRF 的 JSONParser 直接从请求流读取并由 `json.load` 一次性构建对象，应用层没有任何大小上限（`DATA_UPLOAD_MAX_MEMORY_SIZE` 只在 `request.body`/表单解析路径生效，见"存疑"第 2 条），再叠加提交时才开始的单事务写入，容易造成内存峰值与长事务。
- 修复建议：前端加文件大小阈值校验（如 >20MB 拒绝并提示分组导出）；后端按 `CONTENT_LENGTH` 做上限校验并返回 413；大归档改为分组流式/多次导入。

### [P3] R-20 产业字段体检在"没有任何公司归属产业类型"时整体跳过，仍显示"就绪"（漏报）

- 位置：`backend/apps/preparation/plan.py:266`、`308-310`、`312-318`
- 代码：
```python
    # 仅统计本比赛在用产业类型的字段，避免其它行业的历史配置干扰体检
    relevant = [f for f in fields if f.industry_type_id in used_type_ids] if used_type_ids else []
    ...
    used_types = set(used_type_ids)
    if used_types and not relevant:
        warnings.append("本比赛在用的产业类型下没有任何字段")
```
```python
        "stats": [
            ("字段总数", len(fields)),
            ("本比赛在用", len(relevant)),
```
- 触发条件：目标比赛有公司但都没设 `industry_type`（`used_type_ids` 为空），或比赛还没有公司。
- 后果：`relevant`/`timer_enabled` 全为空 → 计算图缺失、定时器缺触发时机、定时器引用不存在字段、计算字段与定时器互斥这些**专门为"开赛前最容易漏配"设计的检查全部不执行**；而 stats 里的"字段总数"（全局库数量）非 0，`_status_of`（1358-1364）判定为 READY，清单显示"就绪"。这正是该必做项最需要报警的场景却给了绿灯。
- 修复建议：`used_type_ids` 为空时给出显式 warning（"未检测到在用的产业类型，无法校验字段配置"），并让状态落回 warning/empty 而不是 ready。

---

## 存疑/待确认

1. **[待确认] R-01 的最终落库结果（整体回滚 vs 部分提交）**。确定的是：DB 异常被吞掉后连接进入 `needs_rollback` 状态，后续每条 SQL 抛 `TransactionManagementError`（Django 文档明确行为），因此"后续资源全失败 + 接口仍 200"成立。但"退出 `with transaction.atomic()` 时是否需要回滚"取决于 Django 5.0 `Atomic.__exit__` 在 `exc_type is None and needs_rollback=True` 时的分支（我读到的语义是走 rollback 分支并静默回滚），本机未安装 Django（`python -c "import django"` 失败）、无外网，无法核对源码，请以最小复现为准：构造 `{"resources":{"warehouses":{"rows":[{"name":"x"}]}}}` + `dryRun=false` 导入到空比赛，比对返回统计与库内实际数据。
2. **[待确认] 请求体大小上限**：DRF `JSONParser` 走 `request.stream`（即 Django 请求对象）读取，Django 的 `DATA_UPLOAD_MAX_MEMORY_SIZE` 校验点在 `request.body` / 表单解析路径；JSON 路径是否受该限制未经验证，settings.py 中也没有显式配置该项。若确实不受限，R-19 的后果从"卡顿"升级为"可控的内存放大/DoS"（仅超管可达，故仍按 P3 记）。
3. **[待确认] R-18 的具体渲染表现**：Element Plus `el-table` 在 `data` 数组被替换为全新对象后是否会清空 selection（从而触发 `selection-change` 把 `selectedResources` 置空、并使"确认导入"按钮变灰），需实测确认；确定的只有"提交集合 `selectedResources` 与重建后的 `resourceRows`/表格显示之间没有同步逻辑"。
4. **[待确认] DecimalField 的 max_digits/decimal_places 在 save 路径不做校验**（Django 只在 `full_clean()`/序列化器中校验），而本项目用的是 SQLite，超大数值不会抛 `DataError` 而是原样写入。也就是说手工构造的归档可以把 60 位以上的价格/股本/现金写进库，之后 `stock/engine.py` 的 Decimal 运算可能抛 `InvalidOperation`（表现为其它模块 500）。这属于"缺少输入校验"的通用问题，不是本模块独有的逻辑缺陷，故未单列编号。
5. **[待确认] 权限链路的遗留数据风险**：`competition:manage` 由 `SUPER_ADMIN_ONLY_PERMISSIONS` 保护的是"授予"入口（`assert_grant_allowed`），而 `has_permission` 只检查账号已存的 `permissions` 列表。若历史数据里存在持有该 key 的非超管账号，仍可访问本模块全部接口。非本次提交引入，仅作提醒。
6. **[待确认] `_imp_map_nodes` 从不调用 `keep_existing_id`**，使 `_CHILD_OF["mapEdges"]` 的父规则永不生效（既有的节点仍会被连线）。这与 R-02/R-04 同源但后果是"多写"而非"少写"，是否算缺陷取决于设计意图，未单列。
7. **[待确认] `RefResolver.resolve` 的 pk 回退分支用 `except Exception: pass` 吞掉 FieldError**（395-396、407-408）：当前 `_REF_FALLBACKS` 里的模型都有 `competition` 字段，暂无实际影响；若将来往兜底表里加无比赛归属的模型，会退化为跨比赛按裸 id 命中。
