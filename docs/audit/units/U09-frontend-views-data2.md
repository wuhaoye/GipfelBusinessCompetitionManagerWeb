# U09 frontend views data2（分支归属：master 基线）

## 概述

**审计范围（严格）**：仅以下 8 个文件（`components/**` 与其他 data-management 视图未审计，根因落在共享组件/工具时只作引用）。

| 文件 | 总行数 | 说明 |
| --- | --- | --- |
| `frontend/src/views/data-management/IndustryTypeManageView.vue` | 1676 | 产业类型 + 产业字段（字典/列表/计算字段/财年定时器/全屏蓝图编辑器） |
| `frontend/src/views/data-management/MaterialsManager.vue` | 587 | 原料 CRUD（产地多选 + 按地图节点的地点价 JSON） |
| `frontend/src/views/data-management/PartsManager.vue` | 505 | 零件 CRUD（原料配比 + 科技需求） |
| `frontend/src/views/data-management/ProductsManager.vue` | 503 | 产品 CRUD（零件配比 + 科技需求） |
| `frontend/src/views/data-management/VehiclesManager.vue` | 462 | 载具 CRUD（燃料 / 油耗 / 可通行路径类型） |
| `frontend/src/views/data-management/WarehousesManager.vue` | 336 | 仓库 CRUD（容量 / 价格 / 种类） |
| `frontend/src/views/data-management/InfrastructureManager.vue` | 324 | 基建 CRUD（9 个数值字段动态渲染） |
| `frontend/src/views/data-management/ProductionLinesManager.vue` | 295 | 生产线 CRUD（价格 / 劳动力 / 年加工上限） |

**基线确认**：`git diff --stat master...feature/contract-watcher -- <上述 8 个文件>` 无输出 → 8 个文件与 master 完全一致，结论归属 master 基线。

**方法与约束**：全程 `read`/`grep` 静态阅读，**未修改任何代码**，未启动服务、未连数据库（唯一执行的动态探针是 `node -e` 调 `pinyin-pro` 复现字段键生成，见 W-11）。
为判定「前端缺陷 vs 后端已兜住」，交叉阅读了以下运行时依赖作为旁证（不作为独立缺陷报告）：
`api/request.ts`（拦截器统一弹错、缓存层 `fetchFullSync`/`reconstruct` 分页重建、GET 默认 `silent`）、`api/cache.ts`（`inferShape`/`extractItems`）、`composables/useCompetitionReload.ts`、`realtime/useResourceChanged.ts`、`stores/competition.ts`（`clearSelection` 的可达性）、`stores/auth.ts` + `permissions/catalog.ts`（超管 `can()` 恒真）、`main.ts`（全局 `unhandledrejection` 只打 console）、`components/common/BigNumberInput.vue`（min 只是红框、不改写/不拦截取值）、`utils/format.ts`（`isValidNumberString` 未被这些视图使用）、`utils/deleteConfirm.ts`，以及后端 `apps/common/base_crud.py|guards.py|pagination.py|exceptions.py`、`apps/{materials,parts,products,vehicles,warehouses,production_lines,infrastructures,industry_types}/*`、`apps/vehicles/models.py`、`apps/contracts/engine.py`、`apps/preparation/{archive.py,plan.py,builder/core.py}`。

**缺陷统计**：P0 ×1、P1 ×3、P2 ×7、P3 ×5，共 16 条（W-01 ~ W-16）。

**风险主线**：
1. **静默数据破坏 / 静默错算**：原料地点的「字符串形态价格」在编辑弹窗被丢弃，保存即清空全部地点价（W-01）；地点价可存非数字文本，合同引擎按 0 计入均价（W-04）。
2. **前后端契约不一致导致功能静默失效**：载具「可通行路径类型」字段名写错，配置永远存不进、界面永远显示空（W-02）；跨比赛选项池（W-03）。
3. **校验缺口**：`BigNumberInput` 的 `min` 只是红框，非法值/空值照样出站（W-06）；配比子表的空行/重复行分别换来 400 与 500（W-07）；小数位与 `Decimal(60,4)` 不匹配（W-12）；产业字段键自动生成可能非法且用户无法修正（W-11）。
4. **并发与切换比赛**：无时序保护的 `loadData`（W-08）；未选比赛时原料列表残留且仍可删除（W-09）。
5. **共享机制的已知同因缺陷**（本次范围文件内的独立证据，供父级合并）：列表被缓存层切片为前 50/100 条（W-05 ≈ U08 V-04）、删除确认取消未捕获（W-13 ≈ U08 V-13）、名称拼 HTML 的存储型注入（U08 V-01 已覆盖，本次调用点见「存疑/待确认」，不重复编号）。

---

## 缺陷清单

### [P0] W-01 编辑原料时丢弃「字符串形态」地点价，保存即把该原料全部地点价清空（静默数据丢失，直接改变合同计价）

- 位置：`frontend/src/views/data-management/MaterialsManager.vue:410-417`（另见同文件 `246-248` 与 `436`）
- 代码：

```ts
// MaterialsManager.vue:410-417（openEdit：只收 number，字符串一律丢弃）
  if (row.nodePrices) {
    try {
      const parsed = JSON.parse(row.nodePrices);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        for (const [k, v] of Object.entries(parsed)) {
          if (typeof v === "number") np[Number(k)] = v;
        }
      }
    } catch {
```

```ts
// MaterialsManager.vue:246-248（同一文件里列表侧却明确兼容「数字字符串」——自相矛盾）
    // 兼容数字与数字字符串（大数安全：新数据以字符串形态存储）
    .filter(([, v]) => typeof v === "number" || (typeof v === "string" && /^[+-]?\d+(\.\d+)?$/.test(v)))
    .map(([k, v]) => ({ nodeId: Number(k), price: v, nodeName: nodeNameById.get(Number(k)) || `节点#${k}` }));
```

- 触发条件：
  1. 原料 `node_prices` 里存的是**字符串**价格，如 `{"12":"120","15":"138"}`。这是本项目自身的常规形态：示例包 `backend/examples/competitions/demo_competition.py:120` `node_prices={n_mine: "120", n_port: "138"}`；导入侧 `backend/apps/preparation/archive.py:1782-1800` 把 Excel 文本原样写入 `new_prices[str(node_id)] = price`，`1812` `json.dumps(new_prices)`；前端 `onNodePriceChange`（`MaterialsManager.vue:378-385`）保存的也永远是字符串；后端 `apps/materials/serializers.py:19,31` 的 `nodePrices` 是 `CharField`，内层 value 完全不做校验，写入什么就是什么。
  2. 这种原料在列表里价格能正常显示（走 `getRowNodePrices`，兼容字符串），于是管理员打开「编辑」——**每个地点的价格输入框都是空的**（`form.nodePrices[id] ?? ''`），但界面没有任何提示。
  3. 管理员此时哪怕只改「名称」或「碳排放系数」并点确定：`hasOrigin` 仍为真（`origin` 非空），payload 里 `nodePrices` 变成 `JSON.stringify({})`（`MaterialsManager.vue:436`），PATCH 到后端 `apps/materials/serializers.py:70-71` 覆盖 `instance.node_prices = "{}"`。
- 后果：**一次普通的「打开编辑 → 保存」就把该原料的全部地点价静默清空**，提示仍是「已更新」。之后合同引擎 `apps/contracts/engine.py:1216-1253` 取不到任何地点价，`avg_prices` 与 `loc_prices` 都命中不到 → 该原料按 0 计价，`compute_material_list_price` 的原料总价被静默算小，比赛内所有引用该原料的合同金额随之错误；数据无版本、无回收路径（`PATCH` 直接覆盖 TextField）。
- 修复建议：
  1. `openEdit` 放开字符串：与列表侧口径统一为「number 或数字字符串」，例如 `if (typeof v === "number" || (typeof v === "string" && /^[+-]?\d+(\.\d+)?$/.test(v))) np[Number(k)] = v as any;`（可复用 `utils/format.ts:108-111` 的 `isValidNumberString`）。
  2. 兜底防重演：提交前若 `row.nodePrices` 非空而 `form.nodePrices` 为空，弹确认或直接拒绝提交。
  3. 后端 `MaterialSerializer.validate_nodePrices` 对 JSON 结构 + 每个 value 做数字校验（`Decimal` 可解析），从源头保证形态一致。

### [P1] W-02 载具「可通过路径类型」字段名与后端契约不一致（`pathTypeIds` ≠ `vehiclePathTypes`）→ 配置永远存不进、界面永远显示空

- 位置：`frontend/src/views/data-management/VehiclesManager.vue:401-408`（提交）＋ `:371`（回填）＋ `:85`（详情回显）
- 代码：

```ts
// VehiclesManager.vue:401-408（提交体用了后端不存在的 pathTypeIds）
      competitionId: compStore.competitionId,
      name: form.name,
      fuelId: form.fuelId,
      fuelConsumptionPerKm: form.fuelConsumptionPerKm,
      maxCargo: form.maxCargo,
      price: form.price,
      carbonEmission: form.carbonEmission,
      pathTypeIds: form.pathTypeIds,
```

```ts
// VehiclesManager.vue:371 / :85（回填与回显同样读 pathTypeIds）
  form.pathTypeIds = row.pathTypeIds ?? [];
  ...
  <template v-if="detailData.pathTypeIds?.length">
```

（后端契约：`backend/apps/vehicles/serializers.py:42-44,57` `VehiclePathTypeItemSerializer{ pathTypeId }` / `vehiclePathTypes = ...ItemSerializer(many=True)`；`apps/vehicles/views.py:59-67` 只在 `if "vehiclePathTypes" in data` 时才全量替换关联；输出为 `_serialize_vehicle` 的 `vehiclePathTypes`（`serializers.py:31-37`）。模型 `apps/vehicles/models.py` 上没有 `path_type_ids` 字段。）

- 触发条件：
  1. 任何账号在载具弹窗里勾选「可通过路径类型」（表单还标了红色必填星号）并确定。前端发的 `pathTypeIds` 不在 `VehicleSerializer` 声明字段内，DRF 直接忽略；`_replace_relations` 因缺 `vehiclePathTypes` 而整段跳过 → 关系表 `vehicle_path_types` 一行都不会写。提示依旧是「已创建 / 已更新」。
  2. 后端返回值里没有 `pathTypeIds`，所以 `form.pathTypeIds` 永远 `undefined → []`：**编辑已有载具时多选框永远空白**，详情弹窗的「可通过路径类型」永远是 `-`，即使库里确实有 `VehiclePathType` 行（可用同一行「删除」时的级联影响提示「支持的路径类型：N 条」自证矛盾）。
- 后果：路径类型这一配置项在 UI 上完全失效且**界面陈述与真实数据相反**。运载载具是否允许走某类路径决定运输可达性；按后端自己的赛前自检 `apps/preparation/plan.py:631-633`，`未勾选可通行路径类型` 的载具会导致「运输路径校验会失败」。管理员以为已限制/已配置，实际库里一条都没有（或仍是历史值），比赛开始后才暴露；且因为没有报错，管理员会反复保存、始终无效。
- 修复建议：
  1. 前端统一改名为后端契约字段：提交 `vehiclePathTypes: form.pathTypeIds.map((id) => ({ pathTypeId: id }))`；回填 `form.pathTypeIds = (row.vehiclePathTypes || []).map((x) => x.pathTypeId ?? x.pathType?.id)`；详情回显同理。
  2. 表单校验补齐：`formRules` 增加 `fuelId`（当前 `<el-form-item label="燃料" prop="fuelId" required>`（`:118`）只有星号没有规则）与 `pathTypeIds`（`:162` 连 `prop` 都没有）的必填规则。
  3. 后端 `VehicleSerializer` 加 `pathTypeIds` 别名或对未知字段做 `strict` 拒绝，避免同类契约漂移再次静默丢字段。

### [P1] W-03 零件/产品的原料、零件、科技节点下拉未带 `competitionId` → 超管可跨比赛选实体并写入关联

- 位置：`frontend/src/views/data-management/PartsManager.vue:333-341`、`:343-351`；`frontend/src/views/data-management/ProductsManager.vue:331-339`、`:341-349`
- 代码：

```ts
// PartsManager.vue:333-341（选项请求缺 competitionId）
async function loadMaterialOptions() {
  try {
    const res: any = await api.get("/materials", { params: { page: 1, pageSize: 200 } });
    const items = Array.isArray(res) ? res : res?.items || [];
    materialOptions.value = items.map((m: any) => ({ label: m.name, value: m.id }));
  } catch {
    /* handled by interceptor */
  }
}
```

（`PartsManager.vue:345` 的 `/tech-nodes`、`ProductsManager.vue:333` 的 `/parts`、`ProductsManager.vue:343` 的 `/tech-nodes` 同样是裸 `{ page: 1, pageSize: 200 }`。对照同目录 `VehiclesManager.vue:307-309` 明确带 `competitionId`，`MaterialsManager.vue:342-344` 有条件下带——本 8 个文件里只有这两个视图漏了。）

- 触发条件：
  1. 后端 `apps/common/base_crud.py:62-64` 用 `apply_competition_scope(qs, user, request.query_params.get("competitionId"))`；`apps/common/guards.py:187-190`：**超管未显式传 competitionId 时不过滤**，返回全部比赛的行（非超管强制按自身比赛过滤，因此该缺陷只影响超管/多比赛管理账号——正是本系统的主要运维账号）。
  2. 超管在比赛 A 下新建零件/产品 → 下拉里混入比赛 B、C 的同名原料/零件/科技节点（`label` 只有名称，没有任何比赛标识）→ 选中并保存。
  3. `PartsManager.vue:417-425` / `ProductsManager.vue:415-423` 把 `materialId`/`partId`/`techNodeId` 原样提交，后端 `_replace_relations`（`apps/parts/views.py:46-62`、`apps/products/views.py`）直接 `create(material_id=...)`，**不校验被引用对象是否属于同一比赛**；`max_length=200` 上限还会让超过 200 条的比赛漏掉目标实体。
- 后果：
  - 产生跨比赛引用：比赛 A 的零件挂上比赛 B 的原料/零件（配比、科技需求），此后 `engine.py` 的 `compute_part_materials`/`compute_material_list_price` 按名字或 id 在本比赛内查不到对应物 → 配比与价格静默按 0/空计算；两个比赛的数据互相污染，且删除 B 的原料会级联删除 A 的零件配比（`PartMaterial.material` 为 `CASCADE`）。
  - 超过 200 条时目标实体根本不在候选里，编辑旧数据时 `el-select` 显示原始 id 而非名称，管理员无法判断关系是否存在。
- 修复建议：
  1. 四处选项请求统一加 `competitionId: compStore.competitionId`（并在无比赛时不请求），与 `VehiclesManager` 对齐。
  2. 选项 `label` 附比赛名（超管场景）或直接改为「按当前比赛过滤 + 服务端强校验同比赛」。
  3. 后端 `_replace_relations` 增加归属校验（`material_id`/`part_id`/`tech_node_id` 的 `competition_id` 必须等于实例的 `competition_id`），返回明确的中文错误。

### [P1] W-04 地点价允许存入非数字文本，合同引擎把它按 0 计入均价 → 原料总价静默算错

- 位置：`frontend/src/views/data-management/MaterialsManager.vue:378-385`（写入点）＋ `:163-169`（输入组件）
- 代码：

```ts
// MaterialsManager.vue:378-385（NaN 走不进 <= 0 分支，被原样存下）
function onNodePriceChange(nodeId: number, v: string) {
  // 大数安全：价格以字符串保存（BigNumberInput 输出干净数字串），后端按 Decimal 解析
  if (v === "" || Number(v) <= 0) {
    delete (form.nodePrices as Record<number, number>)[nodeId];
  } else {
    (form.nodePrices as Record<number, number>)[nodeId] = v as unknown as number;
  }
}
```

- 触发条件：
  1. `BigNumberInput` 对非法输入**只加红框、照传字符串**：`components/common/BigNumberInput.vue:84-97` `if (!NUM_RE.test(s)) return true;`（`invalid` 仅用于 `b-ni-invalid` 样式），`onInput` 依旧 `emit("update:modelValue", clean(raw))`，`clean` 只剥千分位/空格/下划线。故输入 `abc`（或 `１２３` 全角、`1,2,3.` 之类）时 `v === "abc"`。
  2. `Number("abc")` 为 `NaN`，`NaN <= 0` 为 `false`，于是走 else 分支，把 `"abc"` 存进 `form.nodePrices`。
  3. 提交时 `payload.nodePrices = JSON.stringify(form.nodePrices)`（`:436`）→ 后端 `apps/materials/serializers.py:19,71` 是 `CharField`，**内层 value 无任何校验**，原样入库（例如 `{"12":"abc"}`，且 `getRowNodePrices` 的正则过滤只影响显示，不影响已存数据）。
  4. 生产侧：`apps/contracts/engine.py:1240` `nums = [to_number(v) for v in np.values() if is_finite_num(to_number(v))]`，`to_number("abc")` 走 fallback = 0（`engine.py:160-195`）→ `0` 是有限数，**被计入均价数组**；`1243-1244` 命中该地点时 `loc_prices[name] = to_number("abc") = 0`。
- 后果：该原料的「市场均价」被这个 0 拉低（值越大偏差越大），指定地点的合同则直接按 **0 元**结算原料，`compute_material_list_price` 结果静默偏小；前端列表仍然用 `formatMoney` 把 `"abc"` 显示为 `—`（不显眼），因此错误会带着「一切正常」的外观流入比赛金额。
- 修复建议：
  1. `onNodePriceChange` 改为白名单：`if (!isValidNumberString(v)) { 提示非法并删除该键; return; }`（`utils/format.ts:108-111` 已有该工具，这些视图一个都没用）。
  2. 提交前整体校验 `nodePrices` 的每个 value，非法时阻止保存并给出字段级提示。
  3. 后端补 `validate_nodePrices`：必须能 `json.loads` 为 `{int: 数字}`，值用 `Decimal` 解析失败即 400；历史脏数据在引擎侧至少不要按 0 计入均价（应跳过而非取 0）。

### [P2] W-05 列表请求未按数据量取全，被缓存层的分页重建切片成前 50/100 条，且全项目无分页控件/总数提示

- 位置：`frontend/src/views/data-management/WarehousesManager.vue:221-224`（同类：`ProductionLinesManager.vue:189-191`、`MaterialsManager.vue:331-333`、`VehiclesManager.vue:294`、`PartsManager.vue:322`、`ProductsManager.vue:320`、`InfrastructureManager.vue:208-212`）
- 代码：

```ts
// WarehousesManager.vue:221-224（没有 page/pageSize）
    const res = await api.get("/warehouses", {
      params: { competitionId: compStore.competitionId },
    });
    data.value = Array.isArray(res) ? res : [];
```

- 触发条件：
  1. 后端这些列表都是 `CrudListView`（`apps/common/base_crud.py:130-133` 返回 `{items,total,page,pageSize}`），`parse_pagination` 默认 `pageSize = 50`、硬上限 200（`apps/common/pagination.py:9-11,26-35`）。
  2. 前端 GET 走本地全量副本层：`api/request.ts:347-365` `fetchFullSync` 以 `pageSize=10000`（被后端夹到 200）取回后，`storeAndReturn` 调 `reconstruct(items, "paged", params)`（`:336-343`）——`params.pageSize == null` 时取 **50**，返回 `items.slice(0, 50)`；随后 `normalizeListResponse`（`:312-324`）把 `items` 拆成裸数组，`total` 在前端彻底消失。
  3. 数据量门槛：仓库/生产线/原料 > 50；载具/零件/产品/基建 > 100（这四个显式传了 `pageSize: 100`）。本 8 个文件中 `el-pagination` 出现 0 次（`grep el-pagination` 无命中），也没有任何「共 N 条」展示。
- 后果：超出部分**既看不到也搜不到**（`filteredData`/`filteredMaterials` 只在已加载数组上过滤，如 `WarehousesManager.vue:175-180`、`MaterialsManager.vue:217-225`），管理员会误判数据已被删除或从未创建，也无法对旧记录做编辑/删除；由于没有总数，用户没有任何「被截断」的线索。此为共享缓存机制的缺陷，与 U08 V-04 同因（父级可合并）。
- 修复建议：
  1. 这 7 处显式请求 `pageSize: 200`，或在 `loadData` 里按 `total` 循环取全（参考 `api/index.ts:108-112` 的 `mapsApi.nodes.list` 用法）。
  2. 更彻底：真正做服务端分页（分页器 + `normalize:false` 读 `total`），或让缓存层在还原切片时也带上 `total` 以便提示。
  3. 至少在表格上方展示「已加载 X 条 / 共 Y 条」，避免静默截断。

### [P2] W-06 `BigNumberInput` 的 `min` 只是红框不算校验（且基建 9 个数值项连 `rules` 都没有）→ `abc`/`-5`/空串照样出站

- 位置：`frontend/src/views/data-management/InfrastructureManager.vue:174-176`（另见 `:82-93` 的动态表单项）；同类：`WarehousesManager.vue:104-119,196-201`、`ProductionLinesManager.vue:99-107,164-169`、`VehiclesManager.vue:134-150`
- 代码：

```ts
// InfrastructureManager.vue:174-176（全部 9 个数值字段只有名称有规则）
const formRules = {
  name: [{ required: true, message: "请输入基建名称", trigger: "blur" }],
};
```

```ts
// components/common/BigNumberInput.vue:84-90（invalid 只控制红框样式，不拦截取值）
const invalid = computed(() => {
  const s = display.value.trim();
  if (s === "") return false; // 空值交由表单必填校验处理
  if (!NUM_RE.test(s)) return true;
  if (props.min != null && Number.isFinite(props.min)) {
    const n = Number(s);
    if (Number.isFinite(n) && n < props.min) return true;
```

- 触发条件：
  1. 基建弹窗（`:82-93` 用 `v-for="f in fields"` 渲染 9 个 `BigNumberInput`，`prop="footprint"` 等）在 `formRules` 里没有任何规则 → 清空某个数值框（`BigNumberInput` 回传 `""`，`:86` 明确「空值交由表单必填校验处理」，但这里没有必填校验）或输入 `abc`/`-5` → 校验通过 → 提交体带 `""`/`"abc"`。
  2. 其余视图靠 `required: true` 能挡住空串（async-validator 视 `""` 为空，如 `WarehousesManager.vue:196-201`），但挡不住 `abc`（非空字符串）与负数；`BigNumberInput` 也不会像 `el-input-number` 那样把值夹回 `[min, max]`（对照 `node_modules/element-plus` 的 `verifyValue`：`if (newVal > max || newVal < min) newVal = newVal > max ? max : min;`）。
  3. 与仓库/生产线/基建/载具对应的是 `DecimalField(max_digits=60, decimal_places=4)` / `FloatField()`（`backend/apps/warehouses/serializers.py:16-17`、`production_lines/serializers.py:15,17`、`vehicles/serializers.py:52-55`、`infrastructures/serializers.py:14-25`）。
- 后果：
  - `abc`/`""` → 400，弹窗不关、**没有任何字段级错误提示**（拦截器只给一句「请求参数错误，请检查输入」，或英文原文「A valid number is required.」），管理员不知道是哪一项出错；基建有 9 个同样长相的数值框，只能靠试。
  - 负数在基建/仓库上呈现更糟：`-0.05` 会显示红框（标注「请输入有效数字」）却**被后端正常存下**（`FloatField(default=0)` 无 `min_value`；`DecimalField` 无 `min_value`），界面语义与实际数据相反。
- 修复建议：
  1. 让 `BigNumberInput` 成为真正受控输入：非法或越界时不 `emit`（或 emit 后由父组件回滚并提示），并在组件内用 `isValidNumberString` 校验；`el-input-number` 已有夹取语义，二者行为应对齐。
  2. 基建为 9 个字段生成规则数组（`required: true` + 数字/非负校验），其余视图补 `validator` 拦 `abc` 与负数。
  3. 提交前统一用 `utils/format.ts:108-111` 的 `isValidNumberString` 做一次整体体检，失败给出字段名提示。

### [P2] W-07 零件/产品配比子表允许「空行」与「重复实体行」：前者 400，后者撞唯一约束 → 500 且整笔回滚

- 位置：`frontend/src/views/data-management/PartsManager.vue:270-279`（另见 `:383-391`、`:417-425`；产品侧同构：`ProductsManager.vue:266-278,381-389,415-423`）
- 代码：

```ts
// PartsManager.vue:270-279（只要「存在一行合法」，其余空行/重复行不拦）
  partMaterials: [
    {
      required: true,
      validator: (_rule: any, _value: any, callback: any) => {
        const has = form.partMaterials.some((pm) => pm.materialId && pm.ratio > 0);
        has ? callback() : callback(new Error("请至少添加一项原料配比（选择原料且比率>0）"));
      },
      trigger: "change",
    },
  ],
```

（提交体：`PartsManager.vue:420-423` `partMaterials: form.partMaterials.map((pm) => ({ materialId: pm.materialId, ratio: pm.ratio }))`，不筛空行、不去重。）

- 触发条件：
  1. 点「添加原料」后多出一行默认 `{ materialId: null, ratio: 0 }`（`:384`），若管理员填好第一行、**不填第二行**（或事后删掉选择），`has` 判定仍为真 → 校验通过 → 提交 `materialId: null`。后端 `apps/parts/serializers.py:45-47` `materialId = serializers.IntegerField()`（`required`、不允许 null）→ 400。
  2. 若两行选了**同一个原料**（下拉无禁用/去重逻辑），`_replace_relations`（`apps/parts/views.py:46-55`）连续 `PartMaterial.objects.create(part=..., material_id=同一值)`，撞 `apps/parts/models.py:32-33` `unique_together = (("part", "material"),)` → `IntegrityError`。后端没有任何 `IntegrityError` 处理（`grep IntegrityError backend/apps` 无命中），DRF 也接不住非 `APIException` → 500。
- 后果：
  - 空行：保存失败但前端不给原因（提示为英文 `This field may not be null.` 或通用「请求参数错误」），管理员反复点确定无效；同一份表单里的其它修改也一并没保存上。
  - 重复行：`transaction.atomic()`（`views.py:114-119`、`141-147`）整体回滚 → 若是在**编辑**已有零件，`_replace_relations` 已先 `delete()` 旧配比再重建，回滚虽能保住旧数据，但对用户表现为「服务器内部错误，请稍后重试」（500），完全无法定位；产品侧若引用同一零件两次亦然。
- 修复建议：
  1. 提交前清洗：过滤 `materialId == null` 或 `ratio <= 0` 的空行；用 `Set` 检出重复 `materialId`/`partId` 并给出「该原料已存在，请合并比率」的字段级错误。
  2. 行内 `el-select` 对已选值置 `disabled`，并在删除/新增行后触发全表单校验（当前只 `validateField("partMaterials")`）。
  3. 后端：`_replace_relations` 前做 `material_id` 去重与归属校验（同 W-03），并在异常处理器里把 `IntegrityError` 转成 409 + 中文提示。

### [P2] W-08 切比赛/实时事件触发的并发 `loadData` 无请求令牌保护，晚到的旧响应会覆盖新数据

- 位置：`frontend/src/views/data-management/WarehousesManager.vue:213-220`（本 8 个文件的 `loadData`/`reloadAll` 同构：`ProductionLinesManager.vue:181-199`、`MaterialsManager.vue:328-359`、`VehiclesManager.vue:287-303`、`PartsManager.vue:315-331`、`ProductsManager.vue:313-329`、`InfrastructureManager.vue:201-219`）
- 代码：

```ts
// WarehousesManager.vue:213-220（无序号 / 无 AbortController / 无「请求是否仍属于当前比赛」判定）
async function loadData() {
  loading.value = true;
  try {
    if (!compStore.competitionId) {
      data.value = [];
      loading.value = false;
      return;
    }
    const res = await api.get("/warehouses", {
      params: { competitionId: compStore.competitionId },
    });
```

（调用来源有三条并发路径：`onMounted`、`useCompetitionReload` 的 watcher（`composables/useCompetitionReload.ts:19-27`）、`useResourceChanged` 的实时回调（如 `:210-212`）；参数不同 → 请求键不同，缓存层的 `_getInflight` 去重（`api/request.ts:200,684-687`）也拦不住。）

- 触发条件：在比赛 A 的列表请求尚未返回时切到比赛 B（或用「比赛管理」里点当前比赛取消选择、又立刻重新选择），或恰有他人改动的实时事件插入。两次请求同时在飞，`data.value` 以**到达顺序**而非**发起顺序**为准。
- 后果：顶部/上下文已是比赛 B，表格里却渲染比赛 A（或更早一次）的行；管理员据此点「编辑/删除」作用到错误的对象上（`handleDelete(row)` 用 `row.id` 直接发请求，后端 `_get_object` 对超管不做比赛校验，见 `apps/common/base_crud.py:73-83`）。同时 `filteredData` 的搜索、`loading` 的收尾也会互相干扰。此为共享模式缺陷，与 U08 V-09 同因（父级可合并）。
- 修复建议：
  1. 统一的请求令牌：`const seq = ++reqSeq; ... if (seq !== reqSeq) return;`（或 `AbortController` + `signal`），切比赛时自增令牌作废旧请求。
  2. 或把 `competitionId` 快照进闭包，响应回来时与 `compStore.competitionId` 比对，不一致直接丢弃。
  3. `useCompetitionReload` 里提供「取消上一次 reload」的能力，避免每个视图各写一遍。

### [P2] W-09 未选择比赛时原料列表不清空：残留上一比赛的数据且仍可编辑/删除

- 位置：`frontend/src/views/data-management/MaterialsManager.vue:328-334`（对照 `WarehousesManager.vue:216-220`、`VehiclesManager.vue:290-293` 等均显式清空）
- 代码：

```ts
// MaterialsManager.vue:328-334（无比赛上下文直接 return，不清 materials/mapNodes）
async function loadMaterials() {
  if (!compStore.competitionId) return;
  try {
    const res = await api.get("/materials", {
      params: { competitionId: compStore.competitionId },
    });
    materials.value = res?.items || res || [];
```

```ts
// MaterialsManager.vue:364-367（useCompetitionReload 的 clear 只在 newId 非空时调用）
useCompetitionReload(reloadAll, () => {
  materials.value = [];
  mapNodes.value = [];
});
```

- 触发条件：
  1.「未选比赛」是**一键可达**的正常状态：在「比赛管理」里点当前已选中的比赛即 `clearSelection()`（`views/competitions/CompetitionListView.vue:332-338`，提示「已取消选择」）；删除当前比赛也会清空（`:419-422`）；比赛被清库时 `stores/competition.ts:88-91,113-116` 同样清空。
  2. `competitionId` 变为 `null` 时 `useCompetitionReload` 的 watcher 走 `newId` 假值分支 → 不执行 `clear`（`composables/useCompetitionReload.ts:22-25`），随后 `reload()` → `loadMaterials()` 第一行直接 return → `materials.value` 保持上一场比赛的数组；`mapNodes` 同样残留。
- 后果：页面顶部显示「请先在「比赛管理」中选择一个比赛」，表格里却仍是上一个比赛的原料（`filteredMaterials` 照常渲染、搜索照常工作），管理员会以为是当前上下文的数据。此时点「删除」仍会成功——`materialsApi.remove(row.id, compStore.competitionId)` 在 `competitionId == null` 时不带参数（`api/index.ts:80-81`），后端删除只按 id（`apps/common/base_crud.py:196-199`），于是**在「未选比赛」状态下删掉了原比赛的原料**；点「编辑」则因 payload 带 `competitionId: null` 被后端拒（400，英文报错）。其余 7 个视图都有清空处理，行为不一致。
- 修复建议：
  1. `loadMaterials` 改成与其它视图一致的 `if (!compStore.competitionId) { materials.value = []; mapNodes.value = []; return; }`；`loadMapNodes` 同理。
  2. 或在 `useCompetitionReload` 中：`newId` 为空时也调用 `clear?.()`（语义上「无比赛 = 空列表」），从机制上消除该类偏差。
  3. 编辑/删除入口加 `if (!compStore.competitionId) return ElMessage.warning(...)` 守卫。

### [P2] W-10 产业类型「说明」清空后 `|| undefined` 导致该字段永远清不掉

- 位置：`frontend/src/views/data-management/IndustryTypeManageView.vue:821-825`
- 代码：

```ts
    const payload: any = {
      name: form.name.trim(),
      description: form.description || undefined,
    };
    if (form.code !== undefined && form.code !== null) payload.code = form.code;
```

（后端：`backend/apps/industry_types/views.py:423-424` `if "description" in data: industry_type.description = data["description"]`；`serializers.py:87-89` `description = CharField(required=False, allow_null=True, allow_blank=True)`。）

- 触发条件：编辑一个已有说明的产业类型 → 用文本域删光说明（`form.description === ""`）→ 保存。`"" || undefined` 得到 `undefined`，`JSON.stringify` 时该键被整个丢掉 → PATCH 里没有 `description` → 后端 `"description" in data` 为假 → 不修改。提示为「产业类型已更新」。
- 后果：管理员无法把已有说明清空（也看不到任何失败提示），刷新后旧文案原样回来，表现为「保存不生效」；若改用空格则能存下纯空白（后端 `CharField` 不 `trim_whitespace`），列表 `row.description || "—"` 判断为真值 → 显示一片空白。
- 修复建议：与同文件字段提交处的既有做法保持一致——空串显式发 `null`（`:1271-1275` 注释即为此场景）：`description: form.description.trim() === "" ? null : form.description.trim()`；并在后端对 `name`/`description` 统一 `strip()`（`views.py:420` 已对 name 做 strip，description 未做）。

### [P2] W-11 字段名以数字开头时自动生成的字段键非法，而"字段键"输入框被禁用 → 这类字段永远建不出来

- 位置：`frontend/src/views/data-management/IndustryTypeManageView.vue:1052-1058`（另见 `:1065-1072`、`:265-271`、`:1199-1201`）
- 代码：

```ts
// IndustryTypeManageView.vue:1052-1058
function generateFieldKey(): string {
  const base = toPinyinKey((fieldForm.name || "").trim()) || "field";
  const used = new Set((fields.value || []).map((f: any) => f.fieldKey));
  let key = base;
  let i = 1;
  while (used.has(key)) {
    key = `${base}_${i}`;
```

```vue
<!-- IndustryTypeManageView.vue:265-271（字段键只读，用户无法手动修正） -->
            <el-form-item label="字段键">
              <el-input
                v-model="fieldForm.fieldKey"
                placeholder="自动生成"
                disabled
              />
            </el-form-item>
```

- 触发条件：
  1. 新建字段时输入以数字开头的名称（很常见：「2024年产量」「3号矿点数量」「5G基站数」）→ `watch` (`:1065-1072`) 调 `generateFieldKey()` → `utils/pinyin.ts:8-14` 的 `toPinyinKey` 只剥非 `[a-z0-9_]` 字符，**保留数字**。实测（`node -e` 直调 `pinyin-pro`，与前端同一依赖）：
     `"2024年产量" → ["2","0","2","4","nian","chan","liang"] → "2024nianchanliang"`；`"3号矿点" → "3haokuangdian"`；`"5G基站数" → "5gjizhanshu"`。
  2. 该键以数字开头，后端 `backend/apps/industry_types/views.py:46` `FIELD_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")`，`:488-489` 直接拒绝：400「字段键只能包含字母、数字、下划线，且不能以数字开头」。
  3. 前端唯一的修正入口被 `disabled` 封死（`:265-271`；全屏编辑器 `:505-511` 同样 `disabled`），`submitField` 的兜底（`:1199-1201`）也只在「键为空」时补生成，不会改写已生成的非法键。
- 后果：这类名称的产业字段**无论怎么操作都保存不了**，只能改名（丢掉业务语义）；报错文案虽然可读，但用户看着「自动生成」的只读字段键无从下手。附带：纯 emoji/符号名会退化成 `field` / `field_1`，语义尽失（`"😀" → ""`）。
- 修复建议：
  1. `generateFieldKey` 加首字符兜底：若结果以数字开头则前置固定前缀（如 `f_2024nianchanliang`）或整体退化为 `field_<序号>`，并在生成后自校验 `^[A-Za-z_][A-Za-z0-9_]*$` 与后端正则同源（可把该正则提到共享常量）。
  2. 字段键输入框改为「默认自动生成、允许手动编辑」（去掉 `disabled`，加同样的正则校验与查重提示），让用户能自救。

### [P3] W-12 `BigNumberInput` 不限小数位与量级，后端 `Decimal(60,4)` 只接受 4 位小数 → 5 位小数即保存失败且无字段提示

- 位置：`frontend/src/views/data-management/WarehousesManager.vue:112-119`（同类：`ProductionLinesManager.vue:99-107`、`InfrastructureManager.vue:88-92`、`VehiclesManager.vue:143-150`）
- 代码：

```vue
<!-- WarehousesManager.vue:112-119（容量/价格都用 BigNumberInput，无 precision/max） -->
        <el-form-item label="价格" prop="price">
          <BigNumberInput
            v-model="form.price"
            :min="0"
            style="width: 100%"
            placeholder="仓库价格"
          />
        </el-form-item>
```

（`components/common/BigNumberInput.vue:25-35` 的定位是「千万京 10^23 级可输入」，只校验 `^[+-]?\d+(\.\d+)?$`，不限制小数位；后端 `apps/warehouses/serializers.py:16-17` `DecimalField(max_digits=60, decimal_places=4)`，`production_lines/serializers.py:15,17`、`infrastructures/serializers.py`、`vehicles/serializers.py:54` 同为 `Decimal(60,4)`。）

- 触发条件：在「价格」里输入 `12.34567`（或从 Excel 粘贴 5 位以上小数）→ 前端无任何拦截（红框都不亮，因为 `min=0` 只比较大小）→ 后端 `DecimalField` 校验 `decimal_places` → 400。
- 后果：保存失败，弹窗不关，提示只有「请求参数错误，请检查输入」，用户看不到「最多 4 位小数」这类信息；同表单其它合法修改也一并提交失败。反之，输入超长整数（如 70 位）同样只能得到通用报错。
- 修复建议：为 `BigNumberInput` 增加 `precision`（对齐后端 4 位）与 `maxDigits`（60 位）属性并在提交前校验；或在各表单的 `rules` 里加 `validator` 统一提示「最多 4 位小数」。

### [P3] W-13 取消删除确认时 Promise 拒绝未被捕获（`confirmDeleteWithImpact` 写在 `try` 之外）

- 位置：`frontend/src/views/data-management/MaterialsManager.vue:456-465`（同类：`PartsManager.vue:393-402`、`ProductsManager.vue:391-400`）
- 代码：

```ts
// MaterialsManager.vue:456-463（confirm 在 try 之外，取消即 reject 穿透出去）
async function handleDelete(row: any) {
  let impact: any = null;
  try {
    impact = await materialsApi.impact(row.id);
  } catch {
    // 取级联影响信息失败时不阻塞删除，按普通删除提示处理
  }
  await confirmDeleteWithImpact(row.name, impact, {
```

- 触发条件：点「删除」后在确认框点「取消」（或按 Esc、点遮罩关闭）。`utils/deleteConfirm.ts:32`/`:43` 用 `await ElMessageBox.confirm(...)`，取消时 Promise 以 `"cancel"` 拒绝，异常从 `handleDelete` 冒出；模板 `@click="handleDelete(row)"` 不接返回值 → 成为未处理的 Promise 拒绝。
- 后果：控制台出现 `[未处理的 Promise 异常] cancel`（`main.ts:52-55` 打印）——功能上删除已正确中止，但把「用户主动取消」记录成异常，污染错误监控与排查；同目录其它视图（如 `WarehousesManager.vue:289-298`、`InfrastructureManager.vue:252-261`）都把 confirm 包在 `try` 内，行为不一致。此为与 U08 V-13 同因的实现模式（父级可合并）。
- 修复建议：三处统一改为 `try { await confirmDeleteWithImpact(...) } catch { return; }`，或让 `confirmDeleteWithImpact` 内部吞掉 cancel（区分「取消」与真实错误）后返回 `boolean`。

### [P3] W-14 原料「产地」多选拼成 JSON 字符串入库，超过 `max_length=255` 即保存失败且报错为英文

- 位置：`frontend/src/views/data-management/MaterialsManager.vue:432-437`
- 代码：

```ts
    const payload = {
      ...form,
      competitionId: compStore.competitionId,
      origin: JSON.stringify(form.origin || []),
      nodePrices: hasOrigin.value ? JSON.stringify(form.nodePrices || {}) : "{}",
    };
```

（`origin` 在后端是短字段：`backend/apps/materials/serializers.py:16` `origin = serializers.CharField(max_length=255, trim_whitespace=True)`；前端多选无数量/长度上限，`origin` 以节点**名称**数组存储。）

- 触发条件：地图节点较多时全选产地。`max_length` 按**字符数**计，每个产地名在 JSON 里占 `名称长度 + 3`（两个引号 + 一个逗号），按 6 字节点名约 9 字符/条 → **约 28 个产地触顶**；节点名上限 128 字（`backend/apps/maps/models.py:51`），因此**3 个接近上限的长名节点（如 3×120 字）同样直接超限**。`nodePrices` 落在 TextField 不受限，所以先撞墙的必然是 `origin`。
- 后果：保存失败，提示为 DRF 原文 `Ensure this field has no more than 255 characters.`（`apps/common/exceptions.py:100-104` 会取首个校验消息，故不会被中文化），用户不知道是「产地选太多」还是别的字段；已填的按地点价格一并白填。
- 修复建议：前端对 `origin` 累计长度设阈值（如 200 字符）并在超限时禁用更多选择/给出中文提示；或后端把 `origin` 改为 `TextField`、与 `node_prices` 口径一致（注意旧数据的 `CharField(max_length=255)` 迁移）。

### [P3] W-15 产业字段「字典项」只要 key 或 label 为空就被静默丢弃：提示成功但条目消失

- 位置：`frontend/src/views/data-management/IndustryTypeManageView.vue:1251-1258`
- 代码：

```ts
  if (fieldForm.fieldType === "DICTIONARY") {
    const entries = (fieldForm.config.entries || [])
      .filter((e: any) => (e.key || "").trim() && (e.label || "").trim())
      .map((e: any) => ({
        key: e.key.trim(),
        label: e.label.trim(),
        defaultValue: e.defaultValue ?? "",
      }));
```

- 触发条件：
  1. 点「+ 添加字典项」后只填了 `key`（还没填「显示名」）就点保存 → 该行被 `filter` 掉，请求体里没有它，弹窗关闭并提示「字段已添加/字段已更新」。
  2. 编辑已有字典字段时把某行的 key 清空 → 该项被丢弃 → 后端 `views.py:551-575` 以整个 `config` 覆盖 `field.config`，该 key 从定义中消失（已录入该 key 的公司数据成为孤儿/悬空）。
- 后果：用户输入被静默吃掉（无任何「已忽略 N 项」提示），既有字典项的误删不可逆；两处入口（小弹窗与全屏编辑器）都有此行为。
- 修复建议：不要过滤，改为阻止提交并给出字段级错误（如「第 2 项缺少显示名」）；删除既有项需二次确认；`defaultValue` 按 `valueType`（NUMBER/BOOLEAN）做一次可解析性校验（后端 `views.py:106-108` 只校验 `valueType` 合法性，未校验默认值）。

### [P3] W-16 字段列表加载失败时残留上一个产业类型的字段，而删除按钮直接作用于这些行

- 位置：`frontend/src/views/data-management/IndustryTypeManageView.vue:1101-1107`（另见 `:215-221` 的删除按钮、`:1094-1099` 的 `openFields`）
- 代码：

```ts
// IndustryTypeManageView.vue:1101-1107（失败时不清空 fields，沿用上一次的内容）
async function loadFields() {
  if (!fieldTarget.value) return;
  fieldLoading.value = true;
  try {
    const res: any = await industryTypesApi.listFields(fieldTarget.value.id);
    fields.value = Array.isArray(res) ? res : res?.items || res?.data || [];
```

```vue
<!-- IndustryTypeManageView.vue:215-221（按钮直接用 row.id 调删除接口） -->
            <el-button
              size="small"
              type="danger"
              :disabled="!authStore.can('industryType:manage')"
              @click="deleteField(row)"
              >删除</el-button
            >
```

- 触发条件：`openFields(row)`（`:1094-1099`）只重置表单、**不重置 `fields`**；若 `industryTypesApi.listFields` 抛出异常（网络中断/超时且本地无该集合副本，`api/request.ts:659-666` 的离线降级也拿不到缓存时才 rethrow），`catch`（`:1112-1114`）只留注释，于是弹窗标题是「产业字段 · 新类型」，表格里却是上一次打开的那个产业类型的字段。
- 后果：管理员看到的是**别的产业类型**的字段定义（可能据此判断「这个类型已配好」），而「删除」按钮按 `row.id` 直接调 `industryTypesApi.removeField`（`:1349`，接口只收 fieldId，不校验 industryTypeId）→ 误删其它产业类型的字段；`formulaFields`（`:870-881`）也基于错误集合，新建计算字段的引用候选与自动字段键查重都会算错。
- 修复建议：`openFields`/`loadFields` 开头 `fields.value = []`；`catch` 里清空并 `ElMessage.error("字段加载失败，请重试")`（不要只留注释）；删除前校验 `row.industryTypeId === fieldTarget.id` 或让后端 `removeField` 带上并校验所属产业类型。

---

## 存疑/待确认

1. **[已由 U08 V-01 覆盖，不重复编号] 删除确认弹窗的存储型 HTML 注入**：根因在共享工具 `frontend/src/utils/deleteConfirm.ts:40-48`（`dangerouslyUseHTMLString: true` + `${name}` 未转义），本次范围内的调用点同样命中：`MaterialsManager.vue:463-465`（`row.name`）、`PartsManager.vue:400-402`、`ProductsManager.vue:398-400`。经素材名/零件名/产品名污染后，任何账号在级联删除确认框中触发。建议父级把这三处补进 U08 V-01 的调用点清单，本报告不再单列。
2. **[待确认] `origin` 以「节点名」而非节点 id 存储**：`MaterialsManager.vue:286-288` 用 `mapNodes.filter(n => form.origin.includes(n.name))` 反查节点、`nodePrices` 却以节点 id 为键。地图节点改名/删除后，该原料的产地名对不上任何节点 → 价格编辑区显示「（所选产地未匹配到地图节点）」，用户无法维护价格（保存不会丢数据，因为 `form.nodePrices` 来自行数据）。是否算缺陷取决于产品口径（后端 `Material.origin` 也是文本），未列为正式条目。
3. **[待确认] 地点价 JSON 键为「历史节点 id」**：`preparation/archive.py:1790-1800` 导入时按节点名重建 id；节点被删后旧键残留，`engine.py:1243` 用 `str(location_node_id) in np` 匹配 → 已删节点的价不会命中但**仍参与 `1240` 的均价计算**（`np.values()` 全量平均）。若产品期望「均价只统计现存节点」，则此处口径与前端展示（`getRowNodePrices` 显示 `节点#<id>`）都不准确；因涉及后端语义，未列为前端条目。
4. **[待确认] 全屏蓝图编辑器未保存即关闭**：`showGraphFullscreen = false`（关闭按钮 `:486`）没有任何脏数据提示，复杂计算图/公式的编辑会静默丢失；`el-dialog` 无 `before-close` 守卫，本 8 个文件的表单弹窗（`WarehousesManager.vue:130` 取消、`MaterialsManager.vue:178` 等）亦然。属交互体验缺口，未按缺陷计数。
5. **[已用代码否证，记录以免误报] `graphCycle` 会串场**：`IndustryTypeManageView.vue:490` 用 `!!graphCycle` 禁用保存，而 `graphCycle` 只由编辑器的 `@cycle` 更新。核对 `components/industry-types/IndustryFieldGraphEditor.vue:855-888`：`cycleResult` 是 `computed` 且 `watch(..., { immediate: true })` → props 变化（切换字段）即重算并重新上报，故不会把上一个字段的环状态留给下一个字段。**不是缺陷**。
6. **[已用代码否证] 必填星号与 `submitField` 的双击**：`:458-466` / `:487-494` 两个保存按钮共用 `fieldSaving`，Element Plus 的 `loading` 会同时置 `disabled`，加上后端 `fieldKey` 查重（`views.py:492-495`）与 `_check_conflict`（`common/base_crud.py:100-116`）兜底，未发现可复现的重复提交；同类「名称重名」也由后端返回 409，故未列入缺陷清单。
7. **[范围外但影响本 8 个视图] `industryType:view` 实际不可达**：`router/index.ts:157-158` 该路由 `requiresPermission: "industryType:manage"`，因此视图内为只读用户准备的 `:disabled="!authStore.can('industryType:manage')"` 分支（`:18,88,95,119,126,190,202,210,218,236,241,244,246,255,463,490`）在路由层面就到不了。归属 U07/U08 的路由审计，此处仅记录。
8. **未执行的验证**：本次为纯静态审计，未启动前端/后端，未用真实比赛数据跑「编辑原料→保存→读回 node_prices」的端到端复现（W-01 的结论由 `examples/competitions/demo_competition.py:120`、`preparation/archive.py:1782-1812`、`materials/serializers.py:19,31` 与前端 `openEdit`/`getRowNodePrices` 的口径矛盾共同支撑）；若需要，可用 `scripts/` 下的示例比赛包构建一个字符串地点价的原料做一次 PATCH 验证。
