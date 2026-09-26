# U12 frontend components1（分支归属：master 基线）

## 概述

本次审计对象严格限定为 4 个文件（master 基线代码，当前 checkout 为 `feature/contract-watcher`）：

| 文件 | 行数 | 角色 |
| --- | --- | --- |
| `frontend/src/components/contracts/ContractTypeGraphEditor.vue` | 2502 | 合同类型可视化图编辑器（节点/端口/连线/公式/试算/保存） |
| `frontend/src/components/contracts/simple/SimpleContractTypeEditor.vue` | 476 | 合同类型「简单模式」表单编辑器（参与方/输入字段/效果/检查） |
| `frontend/src/components/contracts/simple/FormulaInput.vue` | 135 | 简单模式的值编辑器（常量 / 输入字段 / 公式） |
| `frontend/src/components/industry-types/IndustryFieldGraphEditor.vue` | 1573 | 产业计算字段「蓝图」计算图编辑器（输出/数值源/条件/赋值 + 跨字段环检测） |

方法：用 read/grep 逐行读取上述 4 个文件；为避免误报，对其**依赖但不在上报范围**的代码做了交叉取证（`frontend/src/contracts/graph-model.ts`、`frontend/src/utils/calcGraphRefs.ts`、`frontend/src/composables/useGraphViewport.ts`、`backend/apps/contracts/engine.py`、`serializers.py`、`views.py`、`management/commands/build_contract_types.py`、`frontend/src/views/data-management/*.vue`、`node_modules/element-plus`），结论中以「取证」标注。

统计：P0 × 1、P1 × 2、P2 × 6、P3 × 6，共 15 条。

已排除的「看似可疑但实测无问题」项（不作为缺陷上报）：

- 保存按钮重复提交：`el-button` 在 `loading` 时 Element Plus 内部 `handleClick` 直接 return 并置 `disabled`（取证 `node_modules/element-plus/es/components/button/src/use-button.mjs`），故 `saving.value` 未做入口拦截**不构成缺陷**。
- 删除节点后的悬空连线：两个编辑器的 `removeNode` 都做了 `edges.filter(e => e.source !== id && e.target !== id)`，无悬空边。
- 自环连线：`onPortClick` 中 `pending.nodeId === nodeId` 时直接取消，无法自连。
- 缩放/平移除零、NaN：`useGraphViewport` 的 zoom 被 clamp 在 `[0.3, 2.5]`，拖拽坐标有 `Math.max(0, ...)` 兜底，节点坐标不会为负、不会 NaN。
- `uid()` 同毫秒冲突：`${prefix}_${Date.now().toString(36)}_${++_seq}` 在同一实例内单调递增，同毫秒也不会重复。

---

## 缺陷清单

### [P0] G-01 简单模式编辑器把效果字段写成 `type`，与全库统一的 `kind` 不一致：保存即抹掉全部效果，新建的效果后端直接报「未知效果类型」

- 位置：`frontend/src/components/contracts/simple/SimpleContractTypeEditor.vue:284`

- 代码：
```vue
    form.effects = Array.isArray(ct.effects) ? ct.effects.filter((e: any) => e.type === 'FIELD').map((e: any) => ({ ...e })) : [];
    const conds = Array.isArray(ct.conditions) ? ct.conditions : [];
    form.checks = conds
      .filter((c: any) => c.kind === 'FIELD_COMPARE')
```
（同一文件内的配套写法：`230-236` 定义 `interface FieldEffect { type: 'FIELD'; ... }`；`337-345` `addEffect()` 推入 `{ type: 'FIELD', party, fieldKey, op, value }`；`407` 保存负载 `effects: form.effects`）

- 触发条件：
  1. 全库的 effect 结构是 `{kind:"FIELD", party, fieldKey, op, value[, valueOp, value2]}`——取证：`graph-model.ts:1591-1599`（`graphToFlat` 产出 `kind`，并支持 `kind:"IF"/"FOREACH"/"ASSIGN"` 嵌套）、`backend/apps/contracts/engine.py:1887`（`if eff.get("kind") != "FIELD": raise BusinessError(f"未知效果类型: {eff.get('kind')}")`）、`engine.py:1938-1955`（IF/FOREACH/ASSIGN 全部按 `kind` 分派）、`ContractManageView.vue:716-723`（也按 `e.kind` 读取）。
  2. 场景 A（静默数据丢失）：任一由「可视化新建 / 可视化编辑」产生或历史上由 builder 命令导入的合同类型，其 `effects` 是 `[{kind:'FIELD',...}]`。管理员点「简单编辑」（`ContractTypeManageView.vue:51/94`），仅改一下「说明」后点保存 → `filter(e => e.type === 'FIELD')` 命中 0 条 → 第 407 行把 `effects: []` 提交 → 后端 `ContractTypeSerializer.update`（`serializers.py:119/131-142` 区段）原样落库。**所有效果（含 IF/FOREACH 效果树）被清空，界面无任何提示**。注意紧邻的 `conditions` 有 `preservedConditions`（`297`、`191-198` 的提示条）做保留，effects 没有对应机制，属于明显的实现遗漏。
  3. 场景 B（硬失败）：用「简单新建」创建一个带效果的合同类型，落库的 effect 是 `{type:'FIELD'}`；引擎执行/试算时 `kind` 为 `None` → 抛 `BusinessError("未知效果类型: None", 400)`，合同完全无法执行。

- 后果：合同类型的效果定义被静默清空——此后基于该类型创建/执行的合同不再对任何产业字段产生变更（已执行的历史合同已落账部分不受影响，但类型定义不可复现），且无审计痕迹、无撤销；或新建的合同类型在创建/执行阶段 400 报错，报错文案「未知效果类型: None」无法指向真正原因。

- 修复建议：统一为 `kind`：`interface FieldEffect { kind: 'FIELD'; ... }`、`addEffect()` 写 `kind`、加载时用 `ct.effects.filter(e => e.kind === 'FIELD')`；同时仿照 `preservedConditions` 增加 `preservedEffects` 保留非 FIELD 的效果树（IF/FOREACH/ASSIGN 及嵌套），或在检测到非 FIELD 效果时禁用「简单编辑」并提示改用可视化编辑器。

---

### [P1] G-02 产业计算图「自动布局」在含环图上是死循环：点一次按钮浏览器标签页被彻底卡死

- 位置：`frontend/src/components/industry-types/IndustryFieldGraphEditor.vue:619`

- 代码：
```js
  while (queue.length) {
    const curId = queue.shift()!;
    const curLevel = levels.get(curId)!;
    for (const childId of children.get(curId) || []) {
      const newLevel = curLevel + 1;
      if (!levels.has(childId) || levels.get(childId)! < newLevel) {
        levels.set(childId, newLevel);
        queue.push(childId);
      }
    }
  }
```
（对比：`ContractTypeGraphEditor.vue:1102-1115` 的同名函数用 `inDegree` 递减 + `deg === 0` 才入队，是有终止保证的拓扑 BFS；产业编辑器丢掉了入度递减，只保留「层级变大就重新入队」。）

- 触发条件：
  1. 在蓝图中连出一个**从无父节点可达**的环：例如 `数值源(CONST/根)` 的 `out` → `数值源A(运算 OP)` 的 `a`；`A.out` → `B.a`；`B.out` → `A.b`。`A`、`B` 有父节点所以不进入初始队列，但根节点 CONST 在初始队列（`613-618`），BFS 从 CONST 到达 A（level 1）→ B（level 2）→ 又回到 A，`newLevel = 3 > 1` 于是 `levels.set(A, 3)` 且 `queue.push(A)`，此后每绕一圈层级 +1、永远 `deg` 不减、队列永不空。
  2. 该环**不会被现有校验拦住**：`cycleResult`（`855-861`）用的是 `extractCalcGraphFieldRefs`（只统计 `value(FIELD).fieldKey` 与 `value(FORMULA).expr` 里的字段引用），节点级环路（不引用任何字段）检测不到；父组件保存按钮的 `:disabled="... || !!graphCycle"`（`IndustryTypeManageView.vue:490`）也不会拦。
  3. 用户点工具栏「自动布局」（`IndustryFieldGraphEditor.vue:7`）。

- 后果：`applyAutoLayout` 是同步 `while` 循环，阻塞主线程且 `levels`/队列持续增长 → 页面 100% CPU 假死，用户只能强杀标签页；若此时前一步刚保存过这个成环图，重开编辑器再点「自动布局」会再次卡死。属于可稳定复现的可用性缺陷（DoS 自身）。

- 修复建议：改为与合同编辑器一致的「入度递减 + 拓扑」实现，或加显式保护：`let guard = nodes.length * nodes.length + 1000; while (queue.length && guard-- > 0)`，并在 `guard` 耗尽时提示「图中存在环，已按原顺序布局」；更彻底的做法是把节点级环也纳入 `cycleResult` 的检测范围并在成环时禁用保存。

---

### [P1] G-03 加载时无条件信任 `graph` 画布、保存时用画布反向覆盖四份 DSL：跨编辑器/工具改过的配置被静默回滚

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:1884`

- 代码：
```js
  if (g && Array.isArray(g.nodes)) {
    nodes = JSON.parse(JSON.stringify(g.nodes));
    edges = JSON.parse(JSON.stringify(g.edges || []));
  } else {
    const fg = flatToGraph({
      partyRoles: ct?.partyRoles,
      inputSchema: ct?.inputSchema,
      effects: ct?.effects,
      conditions: ct?.conditions,
    });
```
（保存侧：`1931` `const flat = graphToFlat(graph)`，`1950-1956` 用该 `flat` 覆盖 `partyRoles / inputSchema / effects / conditions`。）

- 触发条件：`graph` 与 `partyRoles/inputSchema/effects/conditions` 是同一实体的两套表示，二者可以合法地不一致，且仓库里存在会制造不一致的路径：
  1. 产品内路径：先用「简单编辑」改了 effects（`SimpleContractTypeEditor.vue:409` 只回传 `graph: props.contractType?.graph || null`，即**原样保留旧画布**），再用「可视化编辑」打开该合同类型 → 画布渲染的是**旧图**；不做任何改动直接点「保存」（`onSave`）→ `graphToFlat(旧图)` 把四份 DSL 全部改回旧图内容，简单模式的修改被无声抹掉。
  2. 运维路径：`backend/apps/contracts/management/commands/build_contract_types.py:316`（`body.pop("graph", None)`）与 `:340-341`（`# graph 不覆盖：把已有的 graph 一并提交，保持画布可用`）——该命令**故意**只更新 DSL、保留旧 picture，正是本缺陷的典型前置状态。
  3. API 路径：`PATCH /api/contract-types/:id`（`views.py:114-119`）可只更新 `effects` 而保留 `graph`。

- 后果：管理员看到的是过期画布，一次「打开-保存」即把他人/其它工具对参与方角色、输入项、效果、检查的全部修改回滚，且界面没有任何「画布与配置不一致」的提示或差异确认，事后只能靠 DB 备份恢复。

- 修复建议：加载时同时解析两套表示并比较（`deepEqual(graphToFlat(graph), {partyRoles, inputSchema, effects, conditions})`），不一致时给出显式选择（「以画布为准」/「以配置为准」/「只读」）而不是静默二选一；或在保存 payload 中带上加载时的 `updatedAt`，由后端拒绝陈旧写入。

---

### [P2] G-04 公式只校验括号配对，不校验标识符：改名/拼错变量后保存即静默按 0 计算

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:1175`

- 代码：
```js
function validateFormulaExpr(expr: string): string {
  if (!expr || !expr.trim()) return "";
  let depth = 0;
  let inStr = false;
  let strChar = "";
  for (const ch of expr) {
    if (inStr) {
      if (ch === strChar) inStr = false;
      continue;
    }
    if (ch === '"' || ch === "'") {
      inStr = true;
      strChar = ch;
      continue;
    }
    if (ch === "(" || ch === "[") depth++;
```
（同一实现原样复制在 `IndustryFieldGraphEditor.vue:679-703`；`onFormulaInput` 只把该校验结果写进 `formulaValidationError`，且该 ref 是单例，切换节点后仍显示上一个节点的报错。）

- 触发条件：`FORMULA` 数值源的表达式作用域是「输入项 key 集合」（`getFormulaFieldKeys()`，`1172-1174`）。用户先写了 `amount * 0.1`，之后在画布里把输入项的 `key` 从 `amount` 改成 `amountCN`（`selectedNode.data.key` 可自由编辑，`207`），或者直接手打一个不存在的名字；`validateFormulaExpr` 只看括号，返回 `""`（无错），保存成功。执行时后端沙箱对该标识符的处理是 **`return 0  # 未定义变量回退为 0`**（取证 `backend/apps/contracts/engine.py:901-905`），于是 `amount * 0.1` → `0 * 0.1 = 0`。

- 后果：效果里写 `SET price = amount * 0.1` 时字段被静默设为 0；检查里写 `revenue >= cost * 1.2` 时比较基准变成 0，检查结果随机地恒真/恒假。全程无任何报错，只能靠人工比对发现。（对比：产业编辑器对 FIELD/FORMULA 引用做了跨字段环检测 `calcGraphRefs.ts`，说明「已知字段键集合」在这一层是现成的，只是没用于公式语法校验。）

- 修复建议：在 `validateFormulaExpr` 里增加标识符校验：用 `extractFormulaFieldRefs(expr, knownKeys)`（已存在，`@/utils/calcGraphRefs`）找出既非已知字段键、也非 `formulaFunctions` / `EXPR_HELPERS` / 保留字的标识符并报错；`formulaValidationError` 改为按节点 id 存 `Map`；保存前对 `valueType === 'FORMULA'` 的节点做同样的硬校验。

---

### [P2] G-05 `FormulaInput.vue` 的公式与输入字段完全没有校验，且改 key 不会同步：检查条件静默失效

- 位置：`frontend/src/components/contracts/simple/FormulaInput.vue:109`

- 代码：
```js
function updateFormula(expr: string) {
  emit('update:modelValue', { type: 'FORMULA', expr });
}
```
（`100-103` 常量分支把空串改成数字 `0`：`emit('update:modelValue', { type: 'CONST', value: value === '' ? 0 : value })`，与 `9` 行注释「常量以字符串形态保存」自相矛盾；`87-98` 切换 `valueType` 时直接重建对象。）

- 触发条件：
  1. 「公式」是纯 `el-input`（`29-36`），可用变量只在帮助气泡里列出（`42-61`），没有任何校验、没有自动补全、没有非法标识符提示；输入 `amount*0.1` 与 `amout*0.1` 的界面表现完全一致。
  2. 「输入字段」下拉的 key 来自 `inputs`（`18-26`），而 `props.inputs` 就是 `SimpleContractTypeEditor` 的 `form.inputSchema`，其 `key` 在表格里可直接编辑（`SimpleContractTypeEditor.vue:66`）——改完 key 后，已有公式/已选 INPUT 引用不会同步，`updateInput` 也不校验 key 是否仍存在。
  3. 执行时后端按 `inputs` 取名，取不到即 `0`（`engine.py:901-905`）。
  4. 附加：把「公式」切到「常量」再切回「公式」时，`valueType` setter 立即 `emit({type:'FORMULA', expr:''})`（`94-96`），已写好的表达式被直接丢弃，无提示、无撤销。

- 后果：简单模式里配置的效果/检查引用了不存在的输入项后静默按 0 参与运算——例如检查「金额 ≥ amount * 0.1」变成「金额 ≥ 0」恒通过，效果「+ amount * 0.1」变成 +0，均无任何前端提示；改输入项 key 或误切类型还会直接丢失已填公式/常量。

- 修复建议：`updateFormula` 增加标识符白名单校验（对 `props.inputs.map(i => i.key)` 做 `extractFormulaFieldRefs` 过滤）并内联报错、阻止保存空表达式；`updateConst/updateFormula` 保持字符串类型一致（空串不要转 `0`）；`valueType` 切换时把旧值缓存起来（或二次确认），切换回来可恢复；输入项 key 变更时在同一表单内高亮受影响的公式。

---

### [P2] G-06 保存前校验只看顶层效果、且漏掉「空公式」：嵌套在 IF 分支里的空值效果照样保存并静默变成 +0

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:1934`

- 代码：
```js
  const badEffects = (flat.effects || []).filter((e: any) => {
    if (e.kind !== "FIELD") return false;
    const v = e.value;
    const noValue =
      !v ||
      (v.type === "INPUT" && !v.key) ||
      (v.type === "CONST" && (v.value == null || String(v.value).trim() === ""));
    return noValue;
  });
```
（`flat.effects` 只含 `rootEffects`——见 `graph-model.ts:1602-1605`；`kind:"IF"/"FOREACH"` 的效果树其子效果在 `then/else/body` 里，被这里 `kind !== "FIELD"` 直接跳过。）

- 触发条件（两条独立路径，都能通过这个"守门"检查）：
  1. **嵌套漏检**：搭 `条件(IF).then → 效果节点`，效果节点只连了「参与方」没连「值」端口 → `resolveValueSource(graph, undefined)` 返回 `{type:'INPUT', key:''}`（`graph-model.ts:1287`）。顶层元素是 `kind:"IF"`，`filter` 返回 false，不进入 `badEffects`；保存成功。执行时 `apply_effect` 递归进 `then`（`engine.py:1939-1943`）→ `resolve_value({type:'INPUT',key:''})` → `inputs.get('')` 为 None → `apply_field_effect` 写入量按 0 处理 → 「效果不生效且不报错」，正是这段校验注释里说要避免的静默失败。
  2. **空公式漏检**：搭 `数值源(FORMULA，表达式留空) → 效果.值`，`resolveValueSpec` 返回 `{type:'FORMULA', expr:''}`（`graph-model.ts:1280-1281`）；`noValue` 的三个条件都不命中（`v` 为真、`type` 既不是 INPUT 也不是 CONST），保存成功；后端 `safe_evaluate` 对空表达式 `return 0`（`engine.py:926-932`）。

- 后果：合同「执行成功」，字段却毫无变化；效果日志里记 `value: 0`，排查时前端画布看不出任何异常。

- 修复建议：把校验改为对效果树递归（`walk(effects)`，进入 `then/else/body`）；`noValue` 增加 `(v.type === 'FORMULA' && !String(v.expr || '').trim())`、`(v.type === 'FIELD' && !v.fieldKey)`；对 `kind:"IF"` 且 `cond` 为空（`{type:'INPUT',key:''}`）的情况也要报错。

---

### [P2] G-07 简单模式保存不校验效果：空参与方/空字段/空输入的「效果」直接落库，运行期才以 400 或静默 +0 暴露

- 位置：`frontend/src/components/contracts/simple/SimpleContractTypeEditor.vue:381`

- 代码：
```js
  for (const p of form.partyRoles) {
    if (!p.role.trim() || !p.label.trim()) {
      ElMessage.error('参与方角色标识和名称不能为空');
      return;
    }
  }
  for (const chk of form.checks) {
    if (!chk.party) {
      ElMessage.error('检查需要选择参与方');
      return;
    }
    if (!chk.fieldKey.trim()) {
      ElMessage.error('检查的字段标识不能为空');
      return;
    }
  }
```
（`handleSave` 校验了名称、key、参与方、以及每一条 check，唯独没有遍历 `form.effects`；`400-410` 把 `effects: form.effects` 原样提交。）

- 触发条件：点「添加效果」后不选参与方 / 不填字段标识就保存；或者把参与方下拉置空后再保存（`addEffect()` 只在新增时取 `form.partyRoles[0]?.role`，若此时还没有参与方角色则为 `''`，`340`）；或者先删光参与方角色再添加效果。上述情况都会保存成功。执行时：`eff.get("party")` 为空 → `engine.py:1891-1892` 抛 `BusinessError("合同「产业字段」效果未指定参与方…")`；`fieldKey` 为空 → `_resolve_industry_field` 在 `engine.py:2263-2264/2272-2273` 抛「不存在字段「」」。另外「值」选 INPUT 且 `inputs` 为空时 `FormulaInput.vue:93` 会写入 `key: ''`，执行时按 0 参与运算（静默）。

- 后果：配置侧的错误被推迟到「创建/执行合同」阶段才以服务端错误抛出，且简单模式给出的效果配置没有和画布等价的自检；空 key 的 INPUT 引用则是彻底静默。

- 修复建议：仿照 checks 增加 effects 循环校验（`party` 必填且存在于 `partyRoles`、`fieldKey` 必填、`value.type === 'INPUT'` 时 `key` 必须非空、`value.type === 'FORMULA'` 时 `expr` 必须非空），并同步校验 `form.inputSchema` 的 key 非空且唯一。

---

### [P2] G-08 150ms 防抖回写 + 卸载不清定时器：最后一次改动可能丢失，或写到「下一个字段」上

- 位置：`frontend/src/components/industry-types/IndustryFieldGraphEditor.vue:1139`

- 代码：
```js
let emitTimer: any = null;
function scheduleEmit() {
  if (emitTimer) clearTimeout(emitTimer);
  emitTimer = setTimeout(() => {
    emit("update:modelValue", JSON.stringify({ nodes: graph.nodes, edges: graph.edges }));
  }, 150);
}
watch(() => graph, scheduleEmit, { deep: true });
```
（`1160-1162` 的 `onUnmounted` 只解绑了 `keydown`，没有 `clearTimeout(emitTimer)`。）

- 触发条件：
  1. 父组件用 `v-model="fieldForm.calcGraph"` 接这个字符串（`IndustryTypeManageView.vue:583`），保存时读的是 `fieldForm.calcGraph`（`:1237`）。用户在画布上做最后一次操作（连线/拖动/改公式）后 **150ms 内**点到父组件的「保存」，此刻 `fieldForm.calcGraph` 还是旧字符串 → 保存的是改动前的图（末次改动丢失，且因为第二次点保存不会出现，用户以为已保存）。
  2. 更糟的错位写入：若在最后一个编辑的 150ms 窗口内切换字段（`fieldForm.calcGraph` 换成另一个字段的值），回调仍持有**旧图**并 `emit`，父组件的 `fieldForm.calcGraph` 被写成旧字段的图；子组件的 `watch(() => props.modelValue)`（`1148-1154`）发现 `v !== cur` 于是 `loadFrom(v)`，把旧图**装载成当前字段的图**——接着按保存就会把 A 字段的计算图写进 B 字段。
  3. 组件被销毁（父对话框重新挂载/路由切换）时 `emitTimer` 仍会触发一次卸载后的 `emit`，向已切换的 `fieldForm` 回写陈旧 JSON。

- 后果：计算字段表达式的「最后一次编辑」随机丢失，或跨字段串图，两者都无提示、无日志，只能靠人工重配。

- 修复建议：`emit` 改为同步（每次 deep watch 直接 emit，父组件只持有字符串，代价很小）；若必须防抖，则 `onUnmounted` 里 `clearTimeout`，并暴露 `flush()` 供父组件在「保存/关闭」前调用（或用 `watch(modelValue)` 的 `flush: 'sync'` + 显式 `emitNow()`）。

---

### [P2] G-09 并发保存无版本校验：后保存整体覆盖先保存，且已打开的对话框永远看不到他人改动

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:1950`

- 代码：
```js
  const payload: any = {
    partyRoles: flat.partyRoles,
    inputSchema: flat.inputSchema,
    effects: flat.effects,
    conditions: flat.conditions,
    graph: JSON.parse(JSON.stringify(graph)),
  };
  saving.value = true;
  try {
    if (meta.id) {
      await contractTypesApi.update(meta.id, payload);
```
（`meta` 里只有 `id/key/name/enabled`，没有 `updatedAt`/`version` 参与比较，`953-958`；`PATCH /api/contract-types/:id` 为全量覆盖，`views.py:114-119`。）

- 触发条件：两个管理员同时打开同一合同类型的可视化编辑器（A 先打开）。B 先保存（改了 effects/条件）。A 的对话框未被刷新——父组件的实时回调只做 `load()`（`ContractTypeManageView.vue:338-340`）替换列表数组，而 `graphTarget` 仍指向 A 打开时的**旧行对象**（`268-271`），`props.contractType` 引用不变，`watch(() => props.contractType, load)` 不触发。A 随后点保存 → 用旧画布 + 旧 DSL 整体覆盖 B 的修改。

- 后果：并发编辑下「后保存覆盖先保存」，B 的工作静默丢失；合同类型是全局模板，影响面是全部比赛的合同执行结果。

- 修复建议：编辑器保存时带上已加载的 `updatedAt`（或 `schemaVersion`），保存成功后更新本地基线；后端（`ContractTypeItemAPIView.patch`）比对 `updatedAt` 不一致时返回 409，前端提示「该合同类型已被他人修改，请重新打开」。同时在对话框打开期间订阅 `contract-types` 的 `resourceChanged`，发现本 id 变化时提示刷新。

---

### [P3] G-10 「清空」一键抹掉整张画布且无二次确认、无撤销（两个编辑器同样）

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:1986`

- 代码：
```js
function onClear() {
  graph.nodes = [];
  graph.edges = [];
  selectedId.value = null;
  pending.value = null;
}
```
（`14` 的按钮是紧邻「试算」的红色按钮：`<el-button type="danger" @click="onClear">清空</el-button>`；产业编辑器为 `IndustryFieldGraphEditor.vue:1108-1113` + `8` 的按钮。）

- 触发条件：误点工具栏的「清空」（红色按钮与「试算」相邻，无 `ElMessageBox.confirm`）。画布立即清空且**没有任何撤销/回收站**；此时若用户继续做别的编辑并点「保存」，整个合同类型/计算字段的图被持久化为空。

- 后果：整张业务图（可能包含上百个节点）一次性丢失；由于保存前校验只检查「效果的值来源」，空图能通过校验被写库（产业侧字段则变成无输出节点的空计算图，`IndustryTypeManageView.vue:1240` 的「必须且只能有一个输出节点」会拦住保存，但合同侧没有等价拦截）。

- 修复建议：`onClear` 前加 `ElMessageBox.confirm`，或实现一次性 undo（保存清空前的快照，提供「撤销清空」）；合同侧保存前增加结构化校验（至少 1 个参与方、效果/检查非空）。

---

### [P3] G-11 未保存改动无任何提示：点「返回」或按 Esc 关闭 `destroy-on-close` 对话框即整体丢弃

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:5`

- 代码：
```vue
    <div class="ge-toolbar">
      <el-button @click="$emit('close')">返回</el-button>
      <template v-if="!meta.id">
        <el-input v-model="meta.key" disabled placeholder="自动生成（名称拼音）" style="width: 150px" />
        <el-input v-model="meta.name" placeholder="名称" style="width: 140px" />
      </template>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
```
（宿主对话框：`ContractTypeManageView.vue:143-157`，含 `destroy-on-close`，且只关掉了 `close-on-click-modal`，未关 `close-on-press-escape`。）

- 触发条件：在画布上做了任意改动（模型里可能有几十次操作）后点「返回」，或按 Esc（Element Plus `el-dialog` 的 `close-on-press-escape` 默认为 `true`）→ 对话框关闭、组件 `destroy-on-close` 被销毁，`graph` 直接丢弃；编辑器没有 dirty 标记，也没有 `before-close`/`onBeforeUnmount` 提示。

- 后果：一次误触（Esc 是很容易碰到的键）即丢失全部未保存的图编辑；对大量节点的图而言是数十分钟的重做成本。

- 修复建议：维护 `dirty` 标记（`load()` 后置 false，任何 `graph` 变更置 true），「返回」与对话框 `before-close` 都走 `ElMessageBox.confirm('有未保存的修改，确定放弃吗？')`；父对话框补 `:close-on-press-escape="false"`（或按 dirty 动态控制）。

---

### [P3] G-12 公式自动补全浮层不会随节点切换关闭：候选词会被插入到「另一个节点」的公式里

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:1237`

- 代码：
```js
function insertFormulaCompletion(item: string, nodeData: any) {
  const isFunc = formulaFunctions.some((f) => f.key === item);
  const suffix = isFunc ? "()" : "";
  const ta = document.querySelector(".ge-formula-textarea") as HTMLTextAreaElement;
  if (ta) {
    const cursor = ta.selectionStart;
    const val = ta.value;
    const before = val.slice(0, cursor);
    const wordMatch = before.match(/[a-zA-Z_]\w*$/);
    const wordStart = wordMatch ? cursor - wordMatch[0].length : cursor;
    nodeData.expr = val.slice(0, wordStart) + item + suffix + val.slice(cursor);
```
（`1230-1235` 设置 `show: true` 后只在「插入」或「无候选」时关闭；`selectedNode` 变化、面板切换节点都没有重置 `formulaAutocomplete.show`；产业编辑器为 `IndustryFieldGraphEditor.vue:733-751`。）

- 触发条件：选中公式节点、Ctrl+Space 弹出自补全浮层（`Teleport to="body"`，`563-578`），不选词，改去点画布上的另一个节点 → 浮层仍停留在原位置且候选仍是旧节点的字段。此时点浮层里的候选词：`nodeData` 是**模板当前渲染的 `selectedNode.data`**（新节点的 data），`document.querySelector('.ge-formula-textarea')` 取的也是新节点刚渲染出的 textarea，于是把旧节点的候选字段名插进新节点的表达式。

- 后果：另一个公式节点的表达式被写入意料之外的变量名，且因为标识符不校验（G-04）不会报错，运行期按 0 参与计算；浮层也没有点击外部关闭逻辑，会一直悬在界面上。

- 修复建议：`watch(selectedId, () => (formulaAutocomplete.value.show = false))`；`insertFormulaCompletion` 不要用全局 `querySelector`，改用 textarea 的 `ref` 或事件源；补一个 `click-outside`/`blur` 关闭。

---

### [P3] G-13 卸载不清理拖拽期间的 window 监听：拖拽中关闭对话框会留下悬挂监听并在卸载后继续改图

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:1682`

- 代码：
```js
function startDrag(node: GNode, e: MouseEvent) {
  select(node.id);
  drag.value = { id: node.id, sx: e.clientX, sy: e.clientY, ox: node.x, oy: node.y };
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mouseup", onDragUp);
}
```
（`1993-1999`：`onMounted` 只加了 `keydown`，`onUnmounted` 只 `removeEventListener("keydown", onGlobalKeydown)`，没有 `onDragUp()`/移除 `mousemove`/`mouseup`；产业编辑器同样，`1056-1061` 与 `1156-1162`。）

- 触发条件：按住节点标题开始拖动（`mousedown` 已绑定 window 监听），拖动过程中按 Esc（或产业编辑器里父组件的「关闭」被其它方式触发）→ 对话框关闭、`destroy-on-close` 销毁组件，但 `mousemove`/`mouseup` 仍在 window 上；继续移动鼠标会持续执行 `nodeById(...)` 并按 `zoom.value` 改写已卸载实体的 `n.x/n.y`。另外在产业编辑器里，这与 G-08 的 `emitTimer` 叠加，会在组件卸载后继续 `emit("update:modelValue", ...)`。

- 后果：监听器与定时器悬挂（虽然最终 `mouseup` 会解绑，但 `mouseup` 之前的所有移动都在改动一个已销毁的图对象；若同时存在 pending emit，则会把卸载后的状态写回父组件）。清理不完整也意味着该组件不是可重入的，后续若改为 keep-alive/多开就会变成真实泄漏。

- 修复建议：`onUnmounted(() => { onDragUp(); if (emitTimer) clearTimeout(emitTimer); })`（产业编辑器）；`startDrag` 前先 `onDragUp()` 兜底，避免重复绑定。

---

### [P3] G-14 载入的图 JSON 不做结构校验：节点 type 不认识就整块渲染崩溃；产业侧解析失败则静默变成空图

- 位置：`frontend/src/components/contracts/ContractTypeGraphEditor.vue:98`

- 代码：
```vue
          <div
            class="ge-node-header"
            :style="{ background: NODE_META[n.type].color }"
            @mousedown.stop.prevent="startDrag(n, $event)"
          >
            <span>
              <span
                v-if="n.type === 'if' || n.type === 'foreach'"
                class="ge-fold-btn"
                @click.stop="toggleCollapse(n.id)"
              >{{ isCollapsed(n.id) ? '▶' : '▼' }}</span>
              {{ NODE_META[n.type].title }}
```
（其余同类无保护取值：`161` `{{ NODE_META[selectedNode.type].title }}`、`1485` `borderColor: NODE_META[node.type].color`、`1404/1415-1417` `NODE_META[node.type].inputs[idx]`；产业编辑器同构，见 `94/103/176/933`。而 `load()`（`1884-1896`）把 `ct.graph.nodes` 原样 JSON 克隆进响应式图，未做任何类型/字段校验。）

- 触发条件：`graph` 是后端 `TextField`、由 `PATCH /api/contract-types/:id` 接收任意 JSON（`serializers.py:64/140-142`），编辑器从不校验。只要节点里出现当前 `NODE_META` 没有的 `type`（历史版本节点类型被移除/改名、builder 或脚本写入的手工图、API 直写），`NODE_META[n.type]` 为 `undefined`，`.color/.title` 抛 `TypeError` → 画布渲染整体失败，编辑该合同类型只剩空白对话框，且没有任何兜底入口去修数据。产业编辑器还有第二条更隐蔽的路径：`loadFrom`（`1120-1136`）在 `JSON.parse` 失败或 `g.nodes` 非数组时**静默**把图置空（无任何提示），随后 `watch(() => graph, scheduleEmit, {deep:true})` 触发 150ms 防抖 emit，把 `{"nodes":[],"edges":[]}` 写回父组件的 `fieldForm.calcGraph`；若用户此时保存，字段的计算图被持久化为空图。

- 后果：一份格式可疑的历史数据能让编辑器完全不可用（合同侧），或让计算图在"打开-保存"后被静默清空（产业侧），两者都缺少可诊断的提示。

- 修复建议：`load()`/`loadFrom()` 做结构校验（节点必须有 `id`、`type ∈ NODE_META`、`data` 为对象；边必须引用存在的节点），非法节点跳过并在面板顶部用 `el-alert` 列出；渲染处统一用 `const meta = NODE_META[n.type] || FALLBACK_META` 兜底；产业侧 `loadFrom` 解析失败/结构不符时不要 emit 回写，并提示「计算图 JSON 无法解析」。

---

### [P3] G-15 产业编辑器的 IF 折叠是死代码：点 ▼ 只显示「0 个节点已隐藏」，功能实际不可用

- 位置：`frontend/src/components/industry-types/IndustryFieldGraphEditor.vue:564`

- 代码：
```js
function hiddenChildCount(nodeId: string): number {
  const visited = new Set<string>();
  const queue: string[] = [nodeId];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const e of graph.edges) {
      if (e.source === cur && (e.sourceHandle === "then" || e.sourceHandle === "else")) {
        if (!visited.has(e.target)) {
          visited.add(e.target);
          queue.push(e.target);
        }
      }
    }
  }
  return visited.size;
}
```
（`isHiddenByFold`（`580-592`）按 `e.targetHandle === "parent"` 上溯；`isEdgeHiddenByFold`/`visibleNodes` 依赖它。但本编辑器的 IF 端口定义是 `indInputHandles(if) = ["cond","then","else"]`、`indOutputHandles(if) = ["out"]`（`449-464`），`then/else` 是**输入**端口，节点输出只有 `out`，因此：① 不存在 `source === if && sourceHandle ∈ {then,else}` 的边 → `hiddenChildCount` 恒为 0；② 不存在 `targetHandle === "parent"` 的边 → `isHiddenByFold` 恒为 false。折叠按钮见 `98-102`，徽标见 `109-111`。）

- 触发条件：在蓝图中加一个「条件(IF)」节点，点标题栏的 ▼（`toggleCollapse`）→ 除按钮图标变为 ▶ 外什么都不会发生，并显示「0 个节点已隐藏」；同时该分支下的节点不会被隐藏。

- 后果：从合同编辑器复制过来的折叠逻辑在产业图上是无效代码，用户看到「0 个节点已隐藏」这种自相矛盾的提示，会误判为图数据损坏；成环/大图时也无法借助折叠降噪。

- 修复建议：产业图没有"子节点"概念，应直接移除 IF 节点上的折叠按钮与 `hiddenChildCount`/`isHiddenByFold`/`collapsedNodes` 相关代码（或按 `if` 的 `then/else` 输入连线实现真正的高亮/淡化，而不是隐藏）。

---

## 存疑/待确认

1. **[待确认] G-01 的生产影响面**：代码层面 `type` / `kind` 不一致已确认（前端写 `type`，引擎与库内其它读取方全部用 `kind`）。但线上是否已有「用简单模式创建的合同类型」以及它们是否在创建合同时 400，需要查库确认：`ContractType.objects.filter(effects__contains='"type"')` 与 `Contract.objects` 的实际失败日志。若确认存在，建议优先修 G-01。
2. **[待确认] G-03 的触发频率**：`build_contract_types` 命令是否在目标环境执行过（`build_contract_types.py:316/340-341` 会制造 graph 与 DSL 的持久不一致）无法从前端代码断定；但即使不跑该命令，纯产品内的「简单编辑 → 可视化编辑 → 保存」链路（`SimpleContractTypeEditor.vue:409` 回传旧 `graph`）已足以触发，故仍按 P1 上报。
3. **[待确认] `argLiterals` 缺字段的崩溃点**：两个编辑器的运算参数输入都用 `v-model="selectedNode.data.argLiterals[h]"`（`ContractTypeGraphEditor.vue:828`、`IndustryFieldGraphEditor.vue:345`）。经本编辑器自己的 `defaultData`/`onKindChange`/`flatToGraph`（`graph-model.ts:1977`）产生的图都带 `argLiterals`，因此只有「外部直写 graph JSON 且 OP 节点无 `argLiterals`」才会在选中该节点时抛 `TypeError`。属低概率，未单列为缺陷，建议顺手加 `?? {}` 兜底。
4. **[待确认] 只读账号路径**：两个编辑器都没有 `readonly`/`disabled` 入参，编辑入口的权限控制完全在宿主页面按钮上（`ContractTypeManageView.vue:48-60/89-100` 用 `authStore.can('contractType:manage')` 禁用；`IndustryTypeManageView.vue:490` 在保存按钮上判断 `industryType:manage`）。若存在其它入口（深链、keep-alive 复用、未来新增页面）绕过按钮禁用，编辑器本身没有任何二次校验。本次范围内未发现可直接触达的只读编辑路径，故不作为缺陷上报。
5. **[待确认] 试算面板的公司列表范围**：`ContractTypeGraphEditor.vue:1267` 用 `companiesApi.list()`（无参赛参数）。后端 `ContractTrialAPIView`（`views.py:482-485`）对非超管校验了「公司必须属于本人比赛」，因此不构成越权，只是下拉可能列出范围外公司并给出「试算失败：公司不存在」的困惑提示——取决于 `/api/companies` 集合接口是否已按比赛过滤，未进一步核实。
6. **未覆盖的相邻代码**：`frontend/src/contracts/graph-model.ts`（`graphToFlat`/`flatToGraph`/`edgeTypeCheck`）、`useGraphViewport.ts`、`views/data-management/*.vue` 均不在本次上报范围，仅作为取证引用；其中 `graph-model.ts:1243-1251`（CONST 值优先取所连输入节点的 `default`，从而可能与面板显示的常量值不一致）疑似存在与 G-06 同类的"界面与实际保存值不一致"问题，建议由负责该文件的审计项确认。
