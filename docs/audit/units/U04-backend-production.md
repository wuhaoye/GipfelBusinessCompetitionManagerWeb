# U04 backend production chain（分支归属：master 基线）

## 概述

**审计范围（严格）**：`backend/apps/` 下 9 个生产链/基础数据 app 的全部文件（含 `migrations/**`，作为约束证据引用）：

| app | 文件（行数） | 说明 |
| --- | --- | --- |
| `materials` | `models.py` 30 / `serializers.py` 77 / `views.py` 65 / `urls.py` 23 / `admin.py` 10 | 原料（含 `node_prices` 地点价 JSON 文本） |
| `parts` | `models.py` 73 / `serializers.py` 71 / `views.py` 176 / `urls.py` 15 / `admin.py` 14 | 零件 + 配比 + 科技需求（嵌套数组全量替换） |
| `products` | `models.py` 72 / `serializers.py` 71 / `views.py` 181 / `urls.py` 17 / `admin.py` 12 | 产品 + 零件配比 + 科技需求 |
| `fuels` | `models.py` 25 / `serializers.py` 54 / `views.py` 65 / `urls.py` 23 / `admin.py` 10 | 燃料单价（Decimal） |
| `warehouses` | `models.py` 34 / `serializers.py` 66 / `views.py` 53 / `urls.py` 21 / `admin.py` 10 | 仓库容量/价格/类型 |
| `vehicles` | `models.py` 63 / `serializers.py` 68 / `views.py` 180 / `urls.py` 17 / `admin.py` 10 | 载具（唯一带 `fuel` PROTECT 与全字段 `min_value` 的 app） |
| `infrastructures` | `models.py` 35 / `serializers.py` 95 / `views.py` 53 / `urls.py` 21 / `admin.py` 10 | 基建（价格 + 6 个浮点 bonus） |
| `production_lines` | `models.py` 27 / `serializers.py` 64 / `views.py` 53 / `urls.py` 21 / `admin.py` 10 | 产线（价格/用工人数/年产能） |
| `consumer_demands` | `models.py` 38 / `serializers.py` 91 / `views.py` 182 / `urls.py` 18 / `admin.py` 10 | 消费需求（自建视图，不走 base_crud） |

**分支归属**：`git diff --stat master -- <上述 9 个 app 路径>` 与 `git diff --stat master...feature/contract-watcher -- <同上>` 均为空输出（当前 HEAD = `feature/contract-watcher`，工作区无改动），结论归属 master 基线。

**方法与约束**：全程 `read`/`grep` 静态阅读；另用一次性只读探针（`django.setup()` + 序列化器 `is_valid()`，**未连接数据库、未调用 create/save、未跑迁移**）实测 DRF 3.15.1 的边界行为，结论已标注"实测"。共享层 `apps/common/{base_crud,guards,helpers,permissions,renderers,response,exceptions,scope}.py`、`apps/contracts/engine.py`、`apps/company_fields/calc.py`、`apps/preparation/builder/core.py` 作为**交叉证据**读取（其中缺陷归属 common/preparation，不在本报告清单内）。部署为 SQLite（`backend/backend/settings.py:305-310`）。

**9 个 app 均无 `tests.py`/`tests/`**（glob 结果为空），下述边界无任何回归测试覆盖。

**权限背景（决定严重度校准，务必先读）**：这 9 个 app 的写接口统一要求 `data:<res>:edit`（`backend/apps/common/base_crud.py:26-43`）。而 `ROLE_TEMPLATES`（`apps/common/permissions.py:334-366`）中 `PLAYER` 只持有 `BASE_VIEW_PERMISSIONS`（view 级），`COMPETITION_ADMIN` 的默认集与 `grantCeiling` 也只含 view 级；`data:*:edit` 不在任何角色的授予上限内，`assert_grant_allowed`（`permissions.py:369-401`）对"扩展集"同样记 violation，即**当前只有 SUPER_ADMIN 能获得写权限**（`has_permission` 对超管隐式全放行，`permissions.py:264-265`；`account:manage` 亦为超管专属，`permissions.py:319-323`）。因此：**本报告不含 P0**——没有"普通玩家账号可远程直接触发"的数据污染链；但所有 P1/P2 都是"一次误配置/一次脚本化写入即造成全比赛范围错误结算或页面不可用"的缺陷，且一旦把 `data:*:edit` 下放给比赛管理员（该 app 的注释与 `_COMPETITION_ADMIN_EXTRAS` 都指向这一演进方向），P-03/P-04/P-10 立即升级为跨比赛越权写。

**缺陷统计**：P0 ×0、P1 ×2、P2 ×5、P3 ×4，共 11 条（P-01 ~ P-11）。

**风险主线**：（1）**数值无下界**——同一批"玩家购买/结算"字段里，vehicles 全线 `min_value`，而 fuels/warehouses/production_lines/infrastructures/consumer_demands 全线缺失，负数价格经合同引擎 `SUB` 落账后**反向加钱**（P-01）；（2）**非法浮点入站**——`FloatField` 接受 `"1e400"/"Infinity"/"NaN"`，出站 JSON 出现裸 `Infinity`，前端 axios 拦截器把整段响应判为"请求失败"，**一个字段即让整个列表接口对全比赛账号不可用**（P-02）；（3）**引用完整性靠注释而非代码**——嵌套关系数组不校验存在性/归属（P-03），`competitionId` 更新不剥离（P-04），基线改名/改类型后合同按名实时解析静默归零（P-05）。

---

## 缺陷清单

### [P1] P-01 负数价格/容量/用工/需求量全无下界校验：合同聚合出现负值，`SUB` 落账即反向加钱

- 位置：`backend/apps/fuels/serializers.py:14`（同类：`backend/apps/warehouses/serializers.py:16-18`、`backend/apps/production_lines/serializers.py:14-16`、`backend/apps/infrastructures/serializers.py:19,23`、`backend/apps/consumer_demands/serializers.py:31`）
- 代码：

```python
    name = serializers.CharField(max_length=255, trim_whitespace=True)
    pricePerLiter = serializers.DecimalField(max_digits=60, decimal_places=4)
    competitionId = serializers.IntegerField()
    createdAt = serializers.DateTimeField(read_only=True)
    updatedAt = serializers.DateTimeField(read_only=True)
```

- 对照组（同一批"玩家购买"字段，vehicles 写全了边界，证明这是遗漏而非设计）：

```python
    fuelConsumptionPerKm = serializers.FloatField(min_value=0)
    maxCargo = serializers.FloatField(min_value=0)
    price = serializers.DecimalField(max_digits=60, decimal_places=4, min_value=Decimal("0"))
    carbonEmission = serializers.FloatField(min_value=0)
```

（`backend/apps/vehicles/serializers.py:52-55`）

- 触发条件（实测，只读序列化器探针）：`FuelSerializer` 传 `{"pricePerLiter": "-1"}` → `valid=True`，`Decimal('-1.0000')`；`WarehouseSerializer` 传 `{"capacity":"-5","price":"-9"}` → `valid=True`；`ProductionLineSerializer` 传 `{"laborCount":-3,"maxPerYear":"-100"}` → `valid=True`；`InfrastructureSerializer` 传 `{"price":"-1","activationPrice":"-2","footprint":-1}` → `valid=True`；`ConsumerDemandSerializer` 传 `{"quantity":-10}` → `valid=True`。对应请求：`PATCH /api/fuels/<id> {"pricePerLiter":"-7.5"}`、`PATCH /api/warehouses/<id> {"capacity":"-1000"}`、`PATCH /api/production-lines/<id> {"laborCount":-5}`、`POST /api/consumer-demands {"quantity":-10}`，全部 200。
- 后果：这些值被合同 DSL 直接聚合并落账，且**全程保号**：`apps/contracts/engine.py:1373-1375` 按名求和（`sum(val.get(name,0)*to_number(q))`）、`to_number` 不取绝对值（`engine.py:160-195`）；聚合值作为合同 INPUT 进入 `combine_values`（`engine.py:544-548`）与效果应用（`engine.py:441-448`，`SUB → after = n_before - n_val`）。于是"燃料单价 = -7.5"使 `compute_fuel_total_price`（`engine.py:1403-1405`）为负，运费/货款类效果的 `SUB` 变成**给公司加钱**（凭空造币）；`SET` 类字段直接得到负现金。仓库 `capacity` 为负使 `compute_warehouse_total_storage`（`engine.py:1417-1427`）分组存储量为负；`laborCount` 为负使用工/人力成本为负（`preparation/builder/core.py:786` 的导入侧用 `positive_int` 强制 >0，两条写入路径口径不一致）；消费需求 `quantity` 为负使 `_consumer_demand_total`（`apps/company_fields/calc.py:208-211` 的 `Sum("quantity")`）为负，公司计算图字段长期为负。
- 修复建议：所有"价格/容量/产能/工时/数量"字段补 `min_value=Decimal("0")` / `min_value=0`（数量用 `IntegerField(min_value=0)`），与 vehicles 对齐；如需允许 0 应在业务层显式说明；`footprint` 与 6 个 bonus 字段另加合理区间（如 `[-1, 1]`）；建议在模型层加 DB `CheckConstraint`，避免 admin/导入路径绕过。

### [P1] P-02 `FloatField` 接受 `NaN/±Infinity`：出站 JSON 出现裸 `Infinity`，整表接口在前端被判定失败；`NaN` 落库为 NULL 后静默按 0 计费

- 位置：`backend/apps/materials/serializers.py:17`（同类：`parts/serializers.py:47`、`products/serializers.py:47`、`vehicles/serializers.py:52,53,55`、`infrastructures/serializers.py:14-22`）
- 代码：

```python
    name = serializers.CharField(max_length=255, trim_whitespace=True)
    origin = serializers.CharField(max_length=255, trim_whitespace=True)
    carbonEmissionCoefficient = serializers.FloatField()
    type = serializers.ChoiceField(choices=_TYPE_CHOICES, default="NORMAL")
    nodePrices = serializers.CharField(allow_blank=True, default="{}", required=False)
```

- 证据（DRF 3.15.1 无有限性校验）：`backend/.venv/Lib/site-packages/rest_framework/fields.py:949-957`

```python
    def to_internal_value(self, data):

        if isinstance(data, str) and len(data) > self.MAX_STRING_LENGTH:
            self.fail('max_string_length')

        try:
            return float(data)
        except (TypeError, ValueError):
            self.fail('invalid')
```

- 触发条件（实测）：`POST/PUT /api/materials/<id> {"name":"m","origin":"o","carbonEmissionCoefficient":"1e400"}` → `valid=True`，`validated={'carbonEmissionCoefficient': inf}`；`"Infinity"` 同样得到 `inf`，`"NaN"` 得到 `nan`。`min_value=0` 也拦不住——实测 `VehicleSerializer` 传 `{"fuelConsumptionPerKm":"NaN","maxCargo":"NaN","carbonEmission":"NaN"}` 与 `{"carbonEmission":"NaN"}` 均 `valid=True`（Django `MinValueValidator` 用 `nan < 0` 比较，恒为 False），故车辆的 `carbonEmission` 同样可写入 `inf`/`nan`。
- 后果：（1）出站渲染链不做有限性处理——`apps/common/renderers.py:34-35` 对 `float` 原样透传，`apps/common/response.py:28-46` 只包信封，`json.dumps` 默认 `allow_nan=True` 输出**非标准 JSON**（实测 `json.dumps({'a': inf})` → `{"a": Infinity}`）。（2）前端 `frontend/src/api/request.ts:21-23,87-95`：axios 默认 `silentJSONParsing` 下 `JSON.parse` 失败会把**原始字符串**作为 `response.data`，拦截器执行 `res.code !== 0` → `undefined !== 0` 成立 → 弹「请求失败」并 reject。因此 `GET /api/materials` 只要含一条 `Infinity`，**该比赛所有账号的原料/零件/产品/载具列表与详情全部加载失败**（嵌套 include 会把该原料/燃料对象带进 parts/products/vehicles 的响应），且由于编辑页面自己也要先拉列表，故障无法通过 UI 自愈。（3）`NaN` 实测在 SQLite 中落库为 `NULL`（in-memory 验证：`nan → null`），回读 `None` 后引擎 `to_number(None)` 取 fallback 0（`engine.py:181-182`），碳排/环保费/碳税静默按 0 计算，无任何提示。
- 修复建议：全部 `FloatField` 增加 `min_value`/`max_value` 并显式拒绝非有限值（自定义 `validate_<field>` 用 `math.isfinite`，或统一改 `DecimalField`）；在 `renderers._convert_big_numbers` 里把非有限 `float` 转为 `None` 并记 warning，避免一条脏数据打挂整个列表接口；入站侧同时校验 `nodePrices`（见 P-06）。

### [P2] P-03 嵌套关系数组与直接外键 id 不做存在性、比赛归属、重复校验：跨比赛引用 + `IntegrityError` 500

- 位置：`backend/apps/parts/views.py:46-62`（同类：`backend/apps/products/views.py:46-62`、`backend/apps/vehicles/views.py:59-67` 与 `vehicles/serializers.py:50` + `vehicles/views.py:124`）
- 代码：

```python
def _replace_relations(instance: Part, data: dict) -> None:
    """按提交的 partMaterials / techRequirements 全量替换（须在事务内调用）。"""
    if "partMaterials" in data:
        instance.part_materials.all().delete()
        for pm in data["partMaterials"]:
            PartMaterial.objects.create(
                part=instance,
                material_id=pm["materialId"],
                ratio=pm["ratio"],
            )
```

- 触发条件：
  1. `POST /api/parts {"name":"A","competitionId":1,"partMaterials":[{"materialId":<另一比赛的原料 id>,"ratio":1}]}` → 不存在的 id 与**别的比赛的 id** 在代码里完全等价（`PartMaterial.objects.create(material_id=...)` 无任何 `competition_id` 过滤），存在性由 DB 兜底：SQLite 外键已开启（Django `sqlite3/base.py:184` 执行 `PRAGMA foreign_keys = ON`），不存在的 id → `IntegrityError` → 无 DRF 接管 → 通用 500（`apps/common/exceptions.py:52-55`）。
  2. 传**另一比赛**真实存在的 `materialId` → 写入成功 → 本比赛零件的 BOM 依赖别家原料；`GET /api/parts` / `:id` 经 `_serialize_part`（`parts/serializers.py:27-33`）把关联对象用 `instance_to_camel` **全字段**内联输出（含 `competitionId`、`node_prices` 定价），而 `data:part:view` 是 PLAYER 默认持有的权限（`permissions.py:298-316`）→ 跨比赛原料定价泄漏给别家比赛账号；同时合同引擎按**本比赛**名称查原料（`engine.py:1230`），跨比赛引用永远匹配不到 → 聚合静默少算。
  3. 同一数组内重复 `materialId`（或含已存在的同一对）→ `unique_together(part, material)`（`parts/migrations/0002_initial.py:30-33`）违约 → `IntegrityError` → 500（`transaction.atomic()` 回滚，重试同样失败）。`techRequirements` 重复、`productParts` 重复（`products/migrations/0002_initial.py:30-33`）、`vehiclePathTypes` 重复（`vehicles/migrations/0001_initial.py:55-58`）同理。
  4. 同类：`POST /api/vehicles {"fuelId":999999,...}` 或 `{"vehiclePathTypes":[{"pathTypeId":<别比赛 PathType id>}]}`（`maps.PathType` 是比赛级模型，`maps/models.py:29-44`）→ 同上两类后果。
- 后果：跨比赛数据耦合（BOM/通行路径指向别家基线）、跨比赛字段泄漏、以及可稳定复现的 500（用户看到"服务器内部错误"且无字段级提示）；`parts`/`products`/`vehicles` 的创建/更新无"非法引用返回 400"的路径。
- 修复建议：`_replace_relations` 前先做批量校验：`ids = {...}` → `filter(pk__in=ids, competition_id=instance.competition_id).count() == len(ids)`，不满足抛 `BusinessError(400)`；数组内 `id` 去重后再写入；`fuelId` 同样校验 `Fuel.objects.filter(pk=..., competition_id=...).exists()`；建议把校验收进序列化器（`validate_partMaterials`）以便错误字段可定位。

### [P2] P-04 更新路径未剥离 `competitionId`：基线数据可被静默迁移到其它比赛；仅改 `competitionId` 时重名检查被跳过 → 500

- 位置：`backend/apps/parts/views.py:135-147`（同类：`backend/apps/products/views.py:137-149`、`backend/apps/vehicles/views.py:147-159`）
- 代码：

```python
        with transaction.atomic():
            if "name" in data:
                instance.name = data["name"]
            if "competitionId" in data:
                instance.competition_id = data["competitionId"]
            instance.save()
            _replace_relations(instance, data)
```

- 通用基类明确禁止这件事（被这三个 app 的覆写绕过）：

```python
        # 禁止跨比赛迁移：剔除 competitionId，冲突检测基于实例当前所属比赛
        data = {
            k: v for k, v in serializer.validated_data.items()
            if k not in ("competitionId", "competition_id")
        }
```

（`backend/apps/common/base_crud.py:173-177`；`consumer_demands/views.py:152-156` 也有同样的剥离逻辑，只有这三个 app 漏了）

- 触发条件：`PATCH /api/parts/<id> {"competitionId": 2}`（products/vehicles 同理）。`_get_object`（`parts/views.py:85-96`）只校验**当前**归属，不校验目标；`serializer` 里的 `competitionId` 是普通 `IntegerField`，写入前无 `_assert_competition_exists`、无 `create_competition_id`。三个变体：（a）目标比赛存在 → 数据静默迁移，原比赛页面消失、目标比赛凭空多出基线数据，`materials`/`parts` 的 `ImpactView` 与审计都无法解释；（b）目标不存在 → FK 违约 500；（c）目标比赛已有同名记录 → 因 `if "name" in data`（`parts/views.py:135`）不成立而**跳过** `_name_conflict` → `unique_together(competition,name)` 违约 500。
- 后果：比赛域隔离的"写入侧"防线（docstring 自称"由 base_crud + _get_object 保证"，`parts/views.py:3`）在更新路径失效：数据可在比赛之间漂移，破坏租户边界与历史留痕。当前仅 `data:part:edit` 持有者（现网 = 超管，见"权限背景"）可触发，故定级 P2；一旦该权限下放给比赛管理员即为跨比赛越权写。
- 修复建议：与 `base_crud.CrudUpdateView._update` 保持一致，更新前 `data = strip_competition_fields(data)`；若确实需要"迁移比赛"，另设显式接口并校验调用者对新比赛的管理权；把重名检查改为无条件用"实例现值 + 本次变更"的完整唯一元组（base_crud 的 `conflict_data` 写法可直接复用）。

### [P2] P-05 基线数据改名/改类型后合同按名实时解析 → 已发布合同静默改变结算口径；warehouses/infrastructures/production_lines 连 impact 端点都没有

- 位置：`backend/apps/warehouses/serializers.py:52-61`（同类：`fuels`、`materials`、`production_lines`、`infrastructures`、`vehicles` 的 `update`）
- 代码（合同侧按 name 实时查库，改名即失配）：

```python
    names = [n for n, _ in entries]
    recs = model_cls.objects.filter(competition_id=competition_id, name__in=names).values("name", field_attr)
    val = {r["name"]: to_number(r[field_attr]) for r in recs}
    return sum(val.get(name, 0) * to_number(q) for name, q in entries)
```

（`backend/apps/contracts/engine.py:1372-1375`；`engine.py:1230`、`engine.py:1418` 同样按 name 匹配）

- 触发条件：`PATCH /api/warehouses/<id> {"type": "PART"}`（类型可自由改，长度/取值仅受 ChoiceField 约束）或 `{"name": "旧名→新名"}`、`PATCH /api/fuels/<id> {"name": ...}`。合同类型的输入项在创建时把实体**名称**固化成 key（`_compute_named_field_aggregate` 的 `names`），执行期再按名称回查。
- 后果：（1）改名后所有引用旧名的合同节点（燃料清单总价格/仓库总存储量/载具总载价/基建聚合…，`engine.py:1378-1432`）**静默取 0**，合同照样可执行完成，运费/货款/碳税全部少算或为 0；（2）改类型后 `compute_warehouse_total_storage`（`engine.py:1417-1427`）按 `type` 分组，原本计在 `FUEL` 的容量被计到 `PART` 组，同一份合同的"燃料仓容量"约束口径被改；（3）`warehouses/urls.py:17-21`、`infrastructures/urls.py:17-21`、`production_lines/urls.py:17-21` 只注册了 collection + item，**没有 impact 端点**（对比 `materials/urls.py:18-23`、`parts/urls.py:12-14`、`products/urls.py:15-17`、`vehicles/urls.py:15-17`、`fuels/urls.py:18-23`），删除/改名时没有任何"被 N 个合同引用"的提示；`materials/views.py:57-65` 的 impact 也只统计 `PartMaterial`，遗漏合同引用。
- 修复建议：为这 9 类资源统一提供 `GET /:id/impact`（统计合同模板 + 运行中合同按名引用的数量）；改名/改类型/删除前若有引用则要求显式 `?force=true` 二次确认并写审计；根治方案是合同侧改为按实体 id 引用或在创建合同时快照基线值。

### [P2] P-06 `materials.nodePrices` 是无校验自由文本：合同按 0 计价、负价入库、超长文本无上限

- 位置：`backend/apps/materials/serializers.py:19`（模型侧 `backend/apps/materials/models.py:14`）
- 代码：

```python
    name = models.CharField(max_length=255)
    origin = models.CharField(max_length=255)
    carbon_emission_coefficient = models.FloatField()
    # 按地点（地图节点）价格：JSON 字符串 { [mapNodeId]: 价格 }
    node_prices = models.TextField(default="{}")
```

- 触发条件（实测均 `valid=True`）：`PATCH /api/materials/<id> {"nodePrices": "<<<not json>>>"}`、`{"nodePrices": ""}`、`{"nodePrices": "{\"1\": -500}"}`、`{"nodePrices": "x"*100000}`（无 `max_length`，TextField 无上限）。契约字段只是 `serializers.CharField(allow_blank=True, default="{}")`。
- 后果：（1）非法/空值被静默吞掉——`engine.py:1233-1239` 用 `try: json.loads(...) except (ValueError, TypeError): np = {}`，`contracts/builder/refs.py:484-497` 的 `_parse_node_prices` 同样返回 `{}` → 该原料均价缺失 → `compute_material_list_price`（`engine.py:1246-1252`）取 0 → **原料清单总价格为 0**，采购合同免费拿料，且不报错不告警（导入侧 `preparation/plan.py:490` 的体检才提示）；（2）`-500` 是合法 JSON，直接产生负地点价（与 P-01 同一条反向加钱链路）；（3）非数值/字符串价格混入 `np.values()`，只有 `is_finite_num` 过滤后的子集参与均价，均价口径随脏数据漂移；（4）10 万字符无上限入库，且每次读取都要 `json.loads` 整段。
- 修复建议：改为 `JSONField` 或自定义 `validate_nodePrices`：必须是 `dict`、key 必须是本比赛存在的 `MapNode.id`、value 必须是非负有限数（不接受字符串数字以外的类型）、`len(raw) <= 上限`；非法直接 400 并给出字段级错误；对已有脏数据加一次性体检。

### [P2] P-07 删除被载具引用的燃料：`ProtectedError` 未处理 → 500（impact 只统计不阻断）

- 位置：`backend/apps/fuels/views.py:57-65`（配合 `backend/apps/vehicles/models.py:15-19` 的 `PROTECT` 与 `backend/apps/common/base_crud.py:196-199` 的裸 `instance.delete()`）
- 代码：

```python
class ImpactView(_CrudBase, CrudImpactView):
    def get_delete_impact(self, instance: Fuel) -> dict:
        from apps.vehicles.models import Vehicle

        count = Vehicle.objects.filter(fuel_id=instance.id).count()
        return {
            "name": str(instance),
            "children": [{"label": "关联的载具", "count": count}],
        }
```

- 触发条件：`DELETE /api/fuels/<id>`，且该燃料被任一 `Vehicle.fuel` 引用（`on_delete=models.PROTECT`，`vehicles/models.py:15-19`）。`CrudDeleteView.delete` 直接 `instance.delete()`，不捕获异常。
- 后果：抛 `django.db.models.ProtectedError`（`IntegrityError` 子类，DRF 不接管）→ `apps/common/exceptions.py:52-55` 返回 500「服务器内部错误，请稍后重试」。前端只能看到通用 500，而 impact 端点其实已经算出了引用数，属于"已知有引用但删除路径不做前置判断"的不一致；对运营而言是"删不掉且不知道为什么"。
- 修复建议：删除前先做与 impact 相同的引用统计，有引用则返回 409 + 中文原因（"该燃料被 N 个载具引用，请先解除绑定"）；或在 `CrudDeleteView` 统一捕获 `ProtectedError`。

### [P3] P-08 名称唯一性判定过弱（大小写/零宽字符/内部空白）+ 检查在事务外：视觉同名记录并存、并发重名 409 变 500

- 位置：`backend/apps/parts/views.py:35-43`（同类：`materials`/`fuels`/`warehouses`/`production_lines`/`infrastructures` 走 `base_crud.CrudMixin._check_conflict`，`backend/apps/common/base_crud.py:100-116`）
- 代码：

```python
def _name_conflict(competition_id, name, exclude_id=None) -> None:
    """比赛域内名称唯一冲突检测。"""
    if not name:
        return
    qs = Part.objects.filter(competition_id=competition_id, name=name)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        raise BusinessError("零件名称已存在", code=409, status_code=409)
```

- 触发条件：（a）精确匹配 + 仅 `strip()`（`parts/serializers.py:67-71`）：`"柴油"` / `"柴油\u200b"`（零宽空格，Python `str.strip()` 不剥离）/ `"柴油  调和"`（内部双空格）/ `"Diesel"` vs `"diesel"` 都会绕过 409 并各自入库（SQLite `=` 区分大小写，`unique_together` 同样放行）；（b）两个并发同名 `POST`：`_name_conflict` 与 `INSERT` 分别在不同事务边界（`parts/views.py:113-118`），检查通过后第二个 INSERT 撞 `unique_together` → `IntegrityError` → 500 而非 409。9 个 app 的模型都没有版本列，不存在乐观锁。
- 后果：（a）UI 上无法区分的两条同名基线进入系统，而合同/聚合全部按 `name` 精确匹配（`engine.py:1373`）→ 只有一条参与计价，另一条静默计 0；且删除/改名时极易改错对象；（b）并发/重试场景下用户看到 500"服务器内部错误"而不是"名称已存在"。
- 修复建议：入库前统一归一化（`unicodedata.normalize("NFKC", name)`、折叠连续空白、`casefold()` 比较），或对归一化列建唯一索引；把"冲突检查 + 写入"放入同一事务并捕获 `IntegrityError` 转 409（`CrudCreateView.post` 目前是检查后裸 `serializer.create`，`base_crud.py:147-148`）。

### [P3] P-09 消费需求：无唯一约束与 region 归属校验 → 重复计数 / 静默失效；列表不分页、`note` 无长度上限

- 位置：`backend/apps/consumer_demands/serializers.py:25-34`（配合 `models.py:30-35` 的 Meta、`views.py:110-121`）
- 代码：

```python
class ConsumerDemandSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    competitionId = serializers.IntegerField()
    region = serializers.CharField(max_length=128, trim_whitespace=True)
    productId = serializers.IntegerField(required=False, allow_null=True)
    productType = serializers.CharField(read_only=True)
    quantity = serializers.IntegerField(default=0)
    note = serializers.CharField(required=False, allow_null=True, allow_blank=True)
```

- 触发条件：（a）`ConsumerDemand.Meta` 只有索引、**没有任何 `unique_together`**（`models.py:30-35`），同一 `(competition, region, product)` 可重复 POST（双击/网络重试/复制粘贴）→ 多行并存；而聚合口径是"按 region 求和、**不分产品**"（`apps/company_fields/calc.py:205-211`：`filter(competition_id=..., region=region).aggregate(Sum("quantity"))`）→ 重复行直接把该公司"消费者需求总数"翻倍；（b）`region` 是自由文本，不校验是否命中本比赛的 `Region.name`，而重算逻辑按 `Region.objects.filter(competition_id=..., name__in=regions)` 匹配（`consumer_demands/views.py:79-91`），匹配不到就**静默 return**（`views.py:83-84`），公司 calcGraph 的 `CONSUMER_DEMAND` 值长期停留在旧值/0，无任何提示；（c）`note` 无 `max_length`（模型为 `TextField`，实测 200000 字符通过），列表接口 `views.py:120-121` 一次返回全部行、不分页、不裁剪字段 → 单行超大 note 即可让整表响应膨胀；（d）`quantity` 可负（见 P-01）。
- 后果：需求数据被重复/错误计数，且驱动公司计算字段（需求→产量/收入类字段）的结果偏差；region 拼写错误不会报错，赛事中途难以定位；列表接口在大数据量下无界。
- 修复建议：加 `unique_together(competition, region, product)`（或对 `(competition, region, product)` 建唯一索引）并把 POST 改为 upsert 语义；`validate_region` 校验必须命中本比赛 Region 名（或改为 `regionId` 外键）；`note` 加 `max_length`；列表接口接入 `parse_pagination` 并支持字段裁剪。

### [P3] P-10 消费需求的 `productId` 无比赛归属校验：跨比赛产品引用与名称泄漏

- 位置：`backend/apps/consumer_demands/serializers.py:13-22`
- 代码：

```python
def _resolve_product(product_id):
    """按 productId 解析 Product；缺失则 404。"""
    if product_id is None:
        return None
    from apps.products.models import Product

    try:
        return Product.objects.get(pk=product_id)
    except Product.DoesNotExist:
        raise BusinessError("请求的资源不存在", code=404, status_code=404)
```

- 触发条件：`POST /api/consumer-demands {"competitionId":<自己比赛>,"region":"华东","productId":<另一比赛的产品 id>,"quantity":1}` → 200；`serializer.create`（`serializers.py:61-73`）把该产品名冗余写进 `product_type`。
- 后果：（1）本比赛的消费需求行挂着别家比赛的产品名/`productId`，`to_representation`（`serializers.py:36-53`）把 `product: {id, name}` 返回，而 `data:region:view` 是 PLAYER 默认权限（`permissions.py:298-316`）→ 跨比赛产品名泄漏；（2）`ProductImpactView`（`products/views.py:168-172`）按 `product_id` 统计"消费者需求引用"，把别家比赛的引用也算进本比赛产品的影响报告（计数越界）；（3）删除该产品时 `SET_NULL`（`models.py:17-23`）把别家的行置空，`product_type` 冗余值却保留旧名，成为无法解释的脏数据。
- 修复建议：`_resolve_product(product_id, competition_id)` 改为 `Product.objects.filter(pk=product_id, competition_id=cid)`，不匹配按 404/400 处理；`ProductImpactView` 的需求计数加上 `competition_id=instance.competition_id`。

### [P3] P-11 删除原料/零件会静默级联删除配比关系：DELETE 直接成功，impact 仅供参考且统计不全

- 位置：`backend/apps/materials/views.py:57-65`（同类：`parts/views.py:151-170`、`products/views.py:153-173`；删除路径 `backend/apps/common/base_crud.py:196-199`）
- 代码：

```python
class ImpactView(_CrudBase, CrudImpactView):
    def get_delete_impact(self, instance: Material) -> dict:
        from apps.parts.models import PartMaterial

        count = PartMaterial.objects.filter(material_id=instance.id).count()
        return {
            "name": str(instance),
            "children": [{"label": "关联的零件配比关系", "count": count}],
        }
```

- 触发条件：`DELETE /api/materials/<id>`，该原料已被若干零件配比引用（`PartMaterial.material` 为 `CASCADE`，`parts/models.py:39-43`）；前端若未先调用 `/:id/impact`（该端点纯提示、服务端删除不做任何引用检查），则无任何确认。
- 后果：零件 BOM 行随原料一起被删掉，`PartMaterial` 静默缺失 → 合同「零件所需原料」/「所需原料总数量」（`engine.py:1262-1296`）少算 → 下游成本与库存效果偏小；同理删零件会静默改变产品 BOM（`products/models.py:38-42` CASCADE），而 `parts/views.py:162-166` 只把它列为"作为组件被产品引用"的计数提示。无审计留痕、无法回滚。
- 修复建议：删除前做强制引用检查（存在引用 → 409 并列出前 N 条引用对象），仅在 `?force=true` 时级联删除；把被删除的关联行写入审计日志（`apps/common/audit.py`）以便追溯；impact 端点补齐合同引用（见 P-05）。

---

## 存疑/待确认

1. **[待确认] 列表接口的数值类型不一致**：`to_representation` 直接返回 `Decimal`，经 `apps/common/renderers.py:28-33` 后**非整数 Decimal 变成字符串**（`"7.5000"`），而 `FloatField` 字段仍是 JSON number。即 `GET /api/fuels` 的 `pricePerLiter` 是字符串、`GET /api/vehicles` 的 `fuelConsumptionPerKm` 是数字。这是引擎"防 double 丢精度"的既定口径（`engine.py:68-98`），未见前端做统一转换，是否已被前端适配需前端侧确认；若未适配，任何"数值比较/排序/直接算术"的前端逻辑在字符串形态下会静默走错分支。
2. **[待确认] 仓库容量上限校验无处可查**：9 个 app 内没有任何"库存/储存"实体，`capacity` 只作为聚合值参与 `compute_warehouse_total_storage`（`engine.py:1408-1427`），没有"已存 ≤ capacity"的校验点。是否存在超容校验、由哪一层负责，需在合同模板/公司字段侧确认（不在本次范围）。`capacity` 为 0/负数在当前代码里不会触发除零（合同 DSL 的 `DIV` 有守卫，见下条）。
3. **除零与极大值边界已核对，未发现缺陷**（不作为缺陷上报）：`maxPerYear`/`laborCount` 在仓库内仅参与聚合与 DSL 属性读取（`refs.py:127-129`），全仓未发现对它们的除法；DSL 的 `DIV` 对 `y == 0` 直接返回 0（`engine.py:1136-1143`），`EXP/LOG` 走 `float` 且有 `>= 0` 分支保护。`DecimalField(max_digits=60)` 的溢出由 DRF 拦截（实测 61 位数字与 `"1e400"` 均返回 `max_digits` 校验错误 → 400），小数位超限同样是 400（`"0.00005"` 被拒），没有静默四舍五入。SQLite 侧无 CHECK 约束，若将来切换 PostgreSQL/直接写库（admin、`preparation/archive.py` 导入）则缺少数据库级兜底。
4. **[待确认] `consumer_demands` 的权限前提不成立（根因在共享层，不在本 9 个 app）**：`consumer_demands/views.py:3-6` 注释称"`data:region:*` 在各角色模板默认集中，避免非超管账号 403"，但 `data:region:edit` 只在 `_COMPETITION_ADMIN_EXTRAS`（`permissions.py:326-332`）里，而 `assert_grant_allowed` 对扩展集同样记为 violation（`permissions.py:392-401`）→ 比赛管理员既不在默认集里、也无法被"显式放开"，**消费需求的写接口实际只有超管可用**（与该文件的注释矛盾）。同一处的副作用：`data:material:edit` 等基线写权限同样无法授予比赛管理员，故本报告 P-03/P-04/P-10 的"越权"表述已按"当前仅超管可触发"校准。因根因位于 `apps/common/permissions.py`，未计入本报告缺陷清单，供 common/权限线审计确认是否为有意收紧。
5. **强制改密门禁已核对无缺口**：这些 app 的视图未挂 `MustChangePasswordPermission`（`base_crud.py:46`），但该门禁在认证层统一实施（`apps/auth/authentication.py:117-121`），故 `mustChangePassword=true` 的账号无法绕过。
6. **超管旁路属设计基线**：`base_crud._get_object`（`base_crud.py:78-82`）与 parts/products/vehicles 的覆写（`parts/views.py:91-96`）对 `SUPER_ADMIN` 一律跳过比赛域校验，与 `apply_competition_scope` 的 `competitionId` 可空语义一致，未见意外绕过；非超管越权读其他比赛详情会返回 404（`parts/views.py:94-95`），跨比赛列表读取被 `apply_competition_scope` 强制按自身比赛过滤（`guards.py:191-195`）。
7. **未覆盖项**：`migrations/**` 仅作为约束证据引用，未逐条审计数据迁移风险；`admin.py` 只做 `admin.site.register`（`materials/admin.py:10` 等），后台写库绕过全部业务校验属已知设计（文件头注释已声明），未计入缺陷；这 9 个 app 无任何测试文件，上述边界（负数、非有限浮点、跨比赛引用、重名归一化）均无回归保护。
