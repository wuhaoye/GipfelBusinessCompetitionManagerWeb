# U10 frontend views biz1（分支归属：master 基线）

## 概述

审计对象严格限定为以下 4 个文件（master 基线代码，当前 checkout 为 `feature/contract-watcher`）：

| 文件 | 行数 | 角色 |
| --- | --- | --- |
| `frontend/src/views/stocks/StockMarketView.vue` | 1329 | 股票行情：自选列表 / 分时·K 线 / 买卖面板 / 持仓 / 订单 |
| `frontend/src/views/stocks/StockManageView.vue` | 1248 | 股票管理：股票 CRUD / 资金账户 / 账户总览 / 推进轮次与定价诊断 |
| `frontend/src/views/dashboard/DashboardView.vue` | 803 | 仪表盘：字段绑定控件布局（localStorage 持久化） |
| `frontend/src/views/regions/RegionOverviewView.vue` | 678 | 区域总览：消费者需求 / 数据框（产业字段实时值） |

方法：用 read/grep 逐行读取上述 4 个文件；对其依赖但**不在上报范围**的代码做了交叉取证（`frontend/src/utils/format.ts`、`frontend/src/api/request.ts`、`frontend/src/api/index.ts`、`frontend/src/realtime/*`、`frontend/src/composables/useDashboardFields.ts`、`frontend/src/components/common/BigNumberInput.vue`、`frontend/src/components/dashboard/DashboardWidget.vue`、`frontend/src/components/layout/AppLayout.vue`、`backend/apps/stock/{models,views,serializers,engine}.py`、`backend/apps/regions/views.py`、`backend/apps/common/{renderers,pagination,signals}.py`），结论中以「取证」标注。

统计：P0 × 0、P1 × 2、P2 × 8、P3 × 7，共 17 条。

未发现 P0 级（资金账目损坏 / 越权 / 跨租户）问题：下单、撤单、持仓、订单列表的身份归属与租户隔离全部由后端强制（取证 `backend/apps/stock/views.py:852-853` `_assert_account_operable`、`781-793`/`990-1002` 按可操作账户过滤、`466-469` 账户列表过滤），前端篡改 `selectedAccountId` / `fundsAccountId` 无法以他人身份交易或读取他人持仓。

与其他审计员的重叠处理（已按去重原则**不重复上报**，仅引用）：

- `BigNumberInput.vue` 允许 `1e5`/`Infinity` 进入 `v-model`、以及 `canTrade`（StockMarketView.vue:368-374）对其放行：根因在组件，已由 U13 的 `M-05` 记录，本报告不再单列。
- `DashboardWidget.vue` 拖拽/缩放的 window 监听泄漏与 pointercancel（U13 `M-03`）、拖拽不裁剪坐标导致控件永久不可达（U13 `M-04`）、`locked` 时右键仍可编辑/删除（U13 `M-09`），以及「类型未注册控件被 `DashboardView.vue:349-353` 过滤后由 360-371 的防抖持久化写回 localStorage 造成布局永久丢失」（U13 `M-01`）：根因均在组件 U13 的处理范围内，本报告不重复；本报告只覆盖 `DashboardView.vue` 自身的画布闸门与保存时机问题（T-07 / T-08 / T-14）。

已排除的「看似可疑但实测无问题」项（不作为缺陷上报）：见文末「存疑/待确认」。

---

## 缺陷清单

### [P1] T-01 交易面板「预计金额」在整数价时算错 2~4 个数量级（`slice(0, -0)` 取空 → 整数部分被兜底成 0）

- 位置：`frontend/src/views/stocks/StockMarketView.vue:355-362`（函数体 344-367，展示处 143-148）

- 代码：
```ts
    const pInt = BigInt(p.replace('.', '').replace(/[^0-9]/g, '') || '0');
    const qInt = BigInt(q.replace('.', '').replace(/[^0-9]/g, '') || '0');
    const result = pInt * qInt;
    const totalDecimals = pDecimals + qDecimals;
    const resultStr = result.toString().padStart(totalDecimals + 1, '0');
    const intPart = resultStr.slice(0, -totalDecimals) || '0';
    const decPart = resultStr.slice(-totalDecimals).slice(0, 2);
    return Number(`${intPart}.${decPart}`);
```

- 触发条件：
  1. `totalDecimals === 0`（委托价与数量都不带小数点）。数量默认就是数字 100（第 279 行 `const trade = ref({ side: "BUY", price: 0, quantity: 100 })` → `String(100) = "100"`），所以只要价格是整数串就命中：
     - 玩家手动把委托价改成整数（例如把 96.50 改成 `100`）——BigNumberInput 原样回传字符串（取证 `components/common/BigNumberInput.vue:80-82`）；
     - 或直接点选一只现价为整数的股票：`Stock.current_price` 是 `DecimalField(max_digits=60, decimal_places=4)`（取证 `backend/apps/stock/models.py:36`），整数值的 Decimal 会被统一渲染器改写为 JSON number（取证 `backend/apps/common/renderers.py:28-33`：`if obj == obj.to_integral_value(): return _convert_big_numbers(int(obj))`），前端拿到的就是数字 `100`，`selectStock` 又直接把它灌进 `trade.price`（第 549 行）。初始价按 `ROUND(净利润×10000/股本/PE, 2)` 计算（取证 `backend/apps/stock/engine.py:324-339`），落在 `.00` 的情形常见。
  2. `totalDecimals` 为 0 时 `-totalDecimals` 就是 `-0`：`"10000".slice(0, -0)` 等价于 `slice(0, 0)` → `""` → `intPart` 被 `|| '0'` 兜底成 `"0"`；而 `"10000".slice(-0)` 等价于 `slice(0)` → 取回整串，`decPart` 于是拿到总数的前两位。

  实测（node 复现该行逻辑）：price `"97"`、quantity `"100"` → `intPart="0"`、`decPart="97"` → 返回 `0.97`（真值 9700）；price `"100"`、quantity `"100"` → 返回 `0.1`（真值 10000）。

- 后果：交易面板给出的「预计金额」与真实金额相差 2~4 个数量级（0.1 / 0.2 / 0.97 …），而玩家正是用这个数字判断资金是否够、买多少股；订单金额本身由后端按 Decimal 精确计算（取证 `backend/apps/stock/views.py:913`），所以不会算错钱，但决策依据完全错误。这是「P1-#9 大数精度修复」引入的回归，且只在最常见的整数输入下出现。

- 修复建议：对 `totalDecimals === 0` 特判：
```ts
const intPart = totalDecimals > 0 ? (resultStr.slice(0, -totalDecimals) || "0") : resultStr;
const decPart = totalDecimals > 0 ? resultStr.slice(-totalDecimals).slice(0, 2) : "";
```

### [P1] T-02 下单按钮没有「提交中」闸门：双击 / 连点会创建两笔委托

- 位置：`frontend/src/views/stocks/StockMarketView.vue:150-158`（按钮）、`368-374`（canTrade）、`792-807`（submitOrder）

- 代码：
```vue
            <el-button
              class="trade-submit"
              :class="trade.side === 'BUY' ? 'is-buy' : 'is-sell'"
              style="width: 100%; height: 36px; font-size: 14px;"
              :disabled="!canTrade"
              @click="submitOrder"
            >
```
```ts
const canTrade = computed(
  () =>
    !!selectedAccountId.value &&
    !!selectedStockId.value &&
    Number(trade.value.price) > 0 &&
    Number(trade.value.quantity) > 0,
);
```

- 触发条件：`canTrade` 只依赖「选中账户/股票 + 输入为正」，没有任何 `submitting` 标志；按钮既无 `:loading` 也无提交中禁用（对比同仓 `RegionOverviewView.vue:134` 的 `:loading="saving"`）。请求发出后按钮仍可点，快速双击 / 手机双击 / 等待响应期间再点 → 两次 `POST /stocks/orders`。后端只校验「余额与持仓够不够」（取证 `backend/apps/stock/views.py:884-905`；同股票同账户的挂单占用会累加），因此只要现金 ≥ 2× 单笔金额，两笔都会落库为 `PENDING`。

- 后果：一次操作意图产生两笔重复挂单 → 下一轮撮合后持仓翻倍、或卖出量翻倍导致超卖被拒；若玩家没注意，撮合后订单已 `FILLED`，而撤单只允许 `PENDING`（取证 `backend/apps/stock/views.py:960-961`），损失不可逆。同时会连弹两次「委托已提交」，进一步掩盖重复。

- 修复建议：加 `const submitting = ref(false)`，在 `submitOrder` 首尾置位，按钮改为 `:loading="submitting" :disabled="!canTrade || submitting"`（Element Plus 在 `loading` 时内部会拦截点击，取证 U13 对 `use-button.mjs` 的核对）；撤单按钮同样加在途集合。

### [P2] T-03 切换资金账户时持仓/订单没有请求时序保护：旧账户的响应会覆盖新账户的数据

- 位置：`frontend/src/views/stocks/StockMarketView.vue:416-438`（配合 555-557 的 `onAccountChange`、834-843 的实时防抖刷新）

- 代码：
```ts
async function reloadAccountData() {
  if (!selectedAccountId.value) {
    holdings.value = [];
    orders.value = [];
    return;
  }
  loadingAccountData.value = true;
  try {
    holdings.value = await stockApi.accountHoldings(selectedAccountId.value);
    // 统一展示该账户的所有订单（不再按选中股票过滤）
    const rawOrders = await stockApi.listOrders(compStore.competitionId!, undefined, selectedAccountId.value);
```

- 触发条件：函数把 `selectedAccountId.value` 直接拼进 URL 后就不再校验，函数体内部还要串行等待两次网络往返（持仓 → 订单）。玩家在「个人账户 / 公司账户」之间切换（个人 + 公司双账户是本系统的常规用法），或切换账户的同时实时事件又触发了一次 `scheduleUnifiedReload()`（第 840 行会再次调用本函数）：发起 A → 切到 B → B 的请求先返回（`holdings`/`orders` = B）→ **A 的响应后到，`holdings.value`/`orders.value` 被 A 覆盖**，最终画面是「账户下拉 = B、现金 = B，但持仓与订单 = A」。

- 后果：「我的持仓 / 我的订单」显示另一个账户的数据（含可卖提示、订单状态、撤单按钮），玩家据错误数据决策；由于撤单接口按订单归属鉴权（取证 `backend/apps/stock/views.py:958-959`），不会越权，但操作对象是错的（撤掉了自己另一个账户的挂单）。`loadingAccountData` 也会被先完成的请求提前置 false。

- 修复建议：进入函数时 `const accId = selectedAccountId.value`，每个 `await` 之后 `if (accId !== selectedAccountId.value) return;`；或使用单调递增的请求序号。

### [P2] T-04 loadCandles 的竞态保护只覆盖「跨股票」：同一只股票的并发请求仍会被旧响应覆盖

- 位置：`frontend/src/views/stocks/StockMarketView.vue:441-468`（并见 546-554、834-843）

- 代码：
```ts
// 竞态保护：记录当前正在加载的股票ID
let loadingCandlesForId: number | null = null;

async function loadCandles(id: number) {
  loadingCandlesForId = id;
  loadingCandles.value = true;
  try {
    const res = await stockApi.candles(id);
    // 竞态检查：如果用户已经切换到其他股票，丢弃本次结果
    if (loadingCandlesForId !== id) return;
```

- 触发条件：守卫比较的是「股票 id」而不是「请求序号」。同一只股票短时间内被加载两次时，两次请求的 id 相同 → 先发后到的旧响应会通过检查并覆盖新响应。可达路径：实时事件的 800ms 防抖刷新与手动「刷新」/`selectStock` 重叠（841 行 `loadCandles(selectedStockId.value)`）；`onChartTabChange()`（284-286）只重绘不发请求，但 546-554 的快速 A→B→A 切换会让 A 的两次请求同时在飞。此外先完成的那次会把 `loadingCandles` 提前置 false（464-466）。

- 后果：K 线以及由 candles 派生的行情头部（`quoteStats`，294-323：今开 / 昨收 / 最高 / 最低 / 成交量 / 成交额 / 振幅）回退到上一轮旧快照，并在下一次事件到达前一直保持；推进轮次后若恰逢该时序，管理员看到的是推进前的走势与成交额，据此做运营判断。

- 修复建议：改用请求序号，并只在序号匹配时清 loading：
```ts
let loadSeq = 0;
async function loadCandles(id: number) {
  const seq = ++loadSeq;
  ...
  if (seq !== loadSeq) return;
  ...
  } finally { if (seq === loadSeq) loadingCandles.value = false; }
```

### [P2] T-05 两个股票视图未接 useCompetitionReload：比赛被切走后视图不重载，仍展示并操作上一个比赛的数据

- 位置：`frontend/src/views/stocks/StockMarketView.vue:844-852`、`frontend/src/views/stocks/StockManageView.vue:952-968`（对比 `RegionOverviewView.vue:471-481`，以及另外 12 处使用 `composables/useCompetitionReload.ts` 的视图）

- 代码：
```ts
useResourceChanged("company-field", scheduleUnifiedReload);
useResourceChanged("stocks", scheduleUnifiedReload);
useResourceChanged("stock-orders", scheduleUnifiedReload);
useResourceChanged("stock-holdings", scheduleUnifiedReload);

onMounted(async () => {
  window.addEventListener("resize", onResize);
  await reloadAll();
});
```

- 触发条件（需同时满足）：
  1. 路由 key 是 `route.fullPath`（取证 `components/layout/AppLayout.vue:19-20`），切换比赛不改路由 → 组件不会重建；
  2. `authStore.fetchProfile().then(() => compStore.applyOwnCompetition(...))`（取证 `AppLayout.vue:66-67`）在子视图**挂载之后**执行；当账号归属比赛与 localStorage 残留的 `currentCompetition` 不一致时，比赛会在此刻被切走（取证 `stores/competition.ts:72-102`，先写 `selected.value = {id}` 再补详情），而两个股票视图既没有 `watch(competitionId)` 也没有 `useCompetitionReload`；
  3. 之后若新比赛迟迟没有 `stocks/company-field/stock-orders/stock-holdings` 事件（对局空闲），`useResourceChanged` 也不会触发重载。

- 后果：视图继续显示上一个比赛的价格、涨跌幅、账户现金、持仓、订单与「轮次」标签，且 `selectedStockId` / `selectedAccountId` 仍是旧比赛的 id；`submitOrder` 只下发 `stockId + fundsAccountId`（795-801），比赛归属由后端从股票推导（取证 `backend/apps/stock/views.py:844`），因此玩家可能在「已经切走的那个比赛」里下单。新比赛一旦有任一实时事件到达即自愈，所以表现为「偶发地长时间显示/操作错误比赛的数据」。手动「刷新」也只能修好股票列表（`reloadStocks` 会在选中股票消失时重选，400-401），`selectedAccountId` 仍留在旧比赛。

- 修复建议：两个文件都接 `useCompetitionReload(reloadAll, () => { stocks.value=[]; candles.value=[]; holdings.value=[]; orders.value=[]; selectedStockId.value=null; selectedAccountId.value=null; })`，并在 clear 中 dispose 图表实例；`canTrade` 建议加 `!!compStore.competitionId` 守卫。

- [待确认]：步骤 2 的前提是账号被改派到另一比赛（`applyOwnCompetition` 对 `ownId == null` 的超管早退）。超管手动选比赛发生在「比赛管理」页，随后进入 /stocks 会重建组件，不受影响；因此本条属「特定账号改派 + 深链接/刷新」条件下的健壮性缺陷。

### [P2] T-06 「可卖 N 股」未扣挂单冻结量，与后端可用持仓口径不一致

- 位置：`frontend/src/views/stocks/StockMarketView.vue:325-329`（计算）、`145-147`（展示）

- 代码：
```ts
const myHoldingShares = computed(() => {
  if (!selectedStockId.value) return 0;
  const h = holdings.value.find((x) => x.stockId === selectedStockId.value);
  return h ? h.shares : 0;
});
```
```vue
              <span v-if="trade.side === 'SELL' && myHoldingShares > 0" class="muted">
                （可卖 {{ fmt(myHoldingShares) }} 股）
              </span>
```

- 触发条件：持仓 1000 股 → 先挂一张 1000 股的卖单（`PENDING`）→ 再看面板，提示仍为「可卖 1,000 股」。后端下单时可用持仓是 `holding.shares - pending_sell_shares`（取证 `backend/apps/stock/views.py:900-905`）。

- 后果：玩家按前端提示提交第二张卖单，被后端以「持仓不足」400 拒绝；在行情快速变化时误判可卖数量、错过卖出时点。前端其实已经加载了该账户全部订单（174-192，字段含 `side/quantity/status`），完全可以自行扣减。

- 修复建议：`myHoldingShares` 减去该股票 `status === 'PENDING' && side === 'SELL'` 的 quantity 之和（同一口径也建议用于「预计金额」区）。

### [P2] T-07 仪表盘：任何字段实时刷新都会把整块画布卸载成加载态

- 位置：`frontend/src/views/dashboard/DashboardView.vue:23-33`（渲染）、`568-576`（订阅）；取证 `composables/useDashboardFields.ts:36-39`

- 代码：
```vue
    <div class="dash-canvas" :class="{ 'is-empty': widgets.length === 0 }" @click.self="deselect">
      <!-- 字段数据（网络聚合）未确认前：画布保持空白 / 加载态，不渲染控件，
           避免控件先显示「—」再跳成真实值的跳变（首屏等网络再渲染）。 -->
      <div v-if="loading" class="dash-loading">
        <span class="dash-loading-text">正在加载数据…</span>
      </div>
      <template v-else>
```

- 触发条件：`useDashboardFields.load()` **每次**调用都先 `loading.value = true`（取证 `useDashboardFields.ts:36-39`），而 DashboardView 在 `company-field`、`region`、`map-nodes`、`consumer-demand` 四类事件上都调用 `load()`（573-576）。进行中的比赛里，合同执行、财年定时器、计算图重算都会改写产业字段并广播 `company-field`（取证 `backend/apps/stock/engine.py:1667-1673` 等），事件频率高。

- 后果：每次事件都会卸载并重建 `<template v-else>` 里的整棵控件树 —— 画布闪成「正在加载数据…」，控件内部状态（滚动位置、动画、临时输入）全部丢失，拖拽/缩放进行中的控件 DOM 被移除；接口慢或事件密集时仪表盘几乎无法操作。第 24-25 行注释说明该闸门只为「首屏等网络」，但实现上被套用到了所有后续刷新。

- 修复建议：把「首屏闸门」与「刷新」分离，例如 `const bootstrapped = ref(false)`，首次 `loading` 由 true→false 后置 true，仅 `!bootstrapped` 时渲染加载态；后续刷新保留控件树（局部 loading 或进度条）。

### [P2] T-08 仪表盘表格控件：字典 JSON 解析失败被静默丢弃，却提示「已保存」

- 位置：`frontend/src/views/dashboard/DashboardView.vue:517-533`、`564-565`、占位符 `132-139`

- 代码：
```ts
  } else if (w.type === "table") {
    let dict: Record<string, unknown> | undefined;
    if (!ref) {
      const raw = editForm.value.dictText?.trim();
      if (raw) {
        try {
          dict = JSON.parse(raw);
        } catch {
          dict = undefined;
        }
      } else {
        dict = {};
      }
    }
```
```ts
  showEdit.value = false;
  ElMessage.success("已保存");
```

- 触发条件：按输入框自己的提示填写 —— 第 137 行 `placeholder="填写 JSON 对象，如 {项目A:10, 项目B:20}"` 并不是合法 JSON（键没有双引号）。点「保存」→ `JSON.parse` 抛错 → `dict = undefined` → 仍执行 `ElMessage.success("已保存")` 并关闭对话框。

- 后果：用户填写的字典内容被静默丢弃（`DashboardWidget.vue:219-223` 取到 `undefined` → 表格显示「（无数据）」），却收到成功反馈；重新打开编辑框内容也已消失（`openEdit` 的 `dictText: c.dict ? JSON.stringify(...) : ""`，461 行）。用户无从判断是保存失败还是数据丢失。

- 修复建议：解析失败时 `ElMessage.error("字典内容不是合法 JSON（键需加双引号）")` 并 `return`（不关对话框、不提示已保存）；同时把 placeholder 改成合法示例 `{"项目A":10,"项目B":20}`。

### [P2] T-09 区域总览：保存数据框用的是打开对话框那一刻的旧卡片快照，整表覆盖会删掉别人新增的数据框

- 位置：`frontend/src/views/regions/RegionOverviewView.vue:327-355`（配合 `222-236` 的重载、`471-488` 的实时订阅）

- 代码：
```ts
async function saveFrame() {
  if (!activeRegion.value) return;
  ...
    const cards = activeRegion.value.cards || [];
    const newCard = {
      id: editingCard.value?.id || `c-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
      ...
    };
    let next: any[];
    if (editingCard.value) {
      next = cards.map((c: any) => (c.id === editingCard.value.id ? newCard : c));
    } else {
      next = [...cards, newCard];
    }
    await regionsApi.saveOverviewCardsByName(activeRegion.value.region, next, compStore.competitionId ?? undefined);
```

- 触发条件：`activeRegion` 是打开对话框时从 `regions.value` 取出的**旧对象引用**，而 `loadRegions()` 每次都重建整个数组（`regions.value = (list as any[]).map((r: any) => ({ ...r, loading: false }))`，229-230），不会回写 `activeRegion`。因此在对话框打开期间只要发生一次区域重载，保存时用的就是过期快照：另一客户端修改该区域配置 → 广播 `region` → 483 行重载；或本机收到 `company-field` 事件 → 486 行重载。保存接口是**整数组覆盖**（取证 `backend/apps/regions/views.py:346-353` + `serializers.py:15-22` 卡片字段固定）。

- 后果：对方新增的数据框（或对其它卡片的改名/换字段）被静默删除，无冲突提示、无审计提示；区域总览是比赛运营的关键展示面，丢失后对方通常不会立刻发现。

- 修复建议：保存前按名字从最新列表重取基准：`const fresh = regions.value.find(r => r.region === activeRegion.value.region); const cards = fresh?.cards || [];`，并在 `editingCard` 场景下按 id 定位；或保存后对比数量并提示「已按最新配置覆盖 N 个数据框」。

### [P2] T-10 撤单无二次确认、无进行中状态：误点即撤，连点会出现语义错误的失败提示

- 位置：`frontend/src/views/stocks/StockMarketView.vue:187-189`（按钮）、`808-815`（处理函数）

- 代码：
```ts
async function cancelOrder(id: number) {
  try {
    await stockApi.cancelOrder(id);
    await reloadAccountData();
  } catch {
    // 错误提示由全局响应拦截器统一弹出，避免重复 toast
  }
}
```
```vue
                <el-button v-if="row.status === 'PENDING'" type="danger" size="small" @click="cancelOrder(row.id)" style="font-size:13px; padding: 6px 12px;">撤单</el-button>
```

- 触发条件：撤单按钮就在订单行右侧、无确认框（同仓 `StockManageView.vue:829-832`、`RegionOverviewView.vue:368` 都用了 `ElMessageBox.confirm`），且没有「该订单正在撤单」的禁用/loading。连点两次会发两次 `DELETE /stocks/orders/:id`，第二次时订单已是 `CANCELLED` → 后端返回 400「仅可撤销挂单」（取证 `backend/apps/stock/views.py:960-961`）。

- 后果：(1) 误点即撤销挂单且不可恢复——挂单可能在下一轮成交，撤销等于放弃成交机会；(2) 连点时出现「仅可撤销挂单」的失败提示，玩家会误判为权限或系统异常；(3) 失败分支只弹错、不 `reloadAccountData()`，列表要等实时事件才自愈。

- 修复建议：加 `ElMessageBox.confirm`；用 `Set<number>` 记录在途撤单 id（或给按钮 `:loading`）避免重复提交；失败分支也刷新订单列表。

### [P3] T-11 手动「刷新」不刷新「我的持仓 / 我的订单」

- 位置：`frontend/src/views/stocks/StockMarketView.vue:817-819`、`407-414`

- 代码：
```ts
async function reloadAll() {
  await Promise.all([reloadStocks(), reloadAccounts()]);
}
```
```ts
async function reloadAccounts() {
  if (!compStore.competitionId) return;
  accounts.value = await stockApi.listAccounts(compStore.competitionId);
  if (!selectedAccountId.value && accounts.value.length) {
    selectedAccountId.value = accounts.value[0].id;
    await reloadAccountData();
  }
}
```

- 触发条件：`reloadAccountData()` 只在「首次还没有选中账户」时被调用。进入页面并自动选中账户后，再点右上角「刷新」（第 7 行 `@click="reloadAll"`）→ 持仓与订单不会重新拉取，只能依赖 `stock-orders` / `stock-holdings` 实时事件。

- 后果：实时通道断开、重连窗口或事件丢失时，用户唯一的手动补救手段无法恢复交易面板：现金余额更新了，持仓/订单还是旧的，账面看起来自相矛盾，玩家可能据此重复下单。

- 修复建议：`reloadAll` 中显式 `await reloadAccountData()`。

### [P3] T-12 K 线加载失败或为空时没有错误态：行情头部整块消失、图表空白，并产生未处理的 Promise rejection

- 位置：`frontend/src/views/stocks/StockMarketView.vue:443-468`（`try/finally` 无 catch）、`27-36`（`v-if="quoteStats"`）、`83-88`（空态只在未选股时渲染）

- 代码：
```ts
async function loadCandles(id: number) {
  loadingCandlesForId = id;
  loadingCandles.value = true;
  try {
    const res = await stockApi.candles(id);
    // 竞态检查：如果用户已经切换到其他股票，丢弃本次结果
    if (loadingCandlesForId !== id) return;
    ...
  } finally {
    if (loadingCandlesForId === id) {
      loadingCandles.value = false;
    }
  }
}
```

- 触发条件：(1) 请求失败：`stockApi.candles` 显式 `cache: false`（取证 `api/index.ts:395`），离线时请求层不会走本地副本降级（取证 `api/request.ts:674-678`），直接 reject；函数只有 `finally` 没有 `catch`，两个调用点都不 await 也不 catch（552 行 `loadCandles(id);`、841 行），rejection 无人处理。(2) 请求成功但股票尚未跑过轮次：后端返回 `candles: []`（取证 `backend/apps/stock/views.py:434-452`）。

- 后果：`candles` 为空 → `quoteStats` 返回 null → 第 27 行的 `v-if="quoteStats"` 把「今开/昨收/最高/最低/成交量/成交额/振幅/市盈率」整块隐藏；图表区只剩一块空的深色画布（「暂无 K 线」空态要求 `!selectedStock`），却仍显示「滚轮缩放 · 拖拽平移」提示。玩家无法区分「该股票暂无行情」与「加载失败」，会在一只没有任何行情数据的股票上继续下单。控制台出现未处理 rejection。

- 修复建议：`catch` 后置 `candlesError` 标记并在图表区渲染「K 线加载失败，点击重试」；`quoteStats === null` 时给出「暂无行情数据」占位而不是隐藏整行；`stockApi.candles(id)` 加 `.catch` 或改由 `loadCandles` 内部吞掉错误。

### [P3] T-13 列表硬截断无任何提示（股票上限 200、订单/持仓上限 500）

- 位置：`frontend/src/views/stocks/StockMarketView.vue:393`、`424-426`；`frontend/src/views/stocks/StockManageView.vue:732`

- 代码：
```ts
    const res = await stockApi.list(1, 200, compStore.competitionId);
```
```ts
    holdings.value = await stockApi.accountHoldings(selectedAccountId.value);
    // 统一展示该账户的所有订单（不再按选中股票过滤）
    const rawOrders = await stockApi.listOrders(compStore.competitionId!, undefined, selectedAccountId.value);
```

- 触发条件：`pageSize` 上限硬约束为 200（取证 `backend/apps/common/pagination.py:9`、`34-35`），而两个股票视图都没有分页入口；订单与持仓在后端被 `qs.order_by("-created_at")[:500]` / `qs[:500]` 截断，并通过 `X-Total-Count`、`X-Truncated` 头返回真实总数（取证 `backend/apps/stock/views.py:747-752`、`817-821`）——但请求层只把 `res.data` 交给调用方（取证 `api/request.ts:87-95`），前端从未读取这两个头。

- 后果：单场比赛股票超过 200 只时，「股票管理」标题「股票（200）」与实际不符，被截断的股票无法编辑/删除，行情页也看不到它们；某账户订单超过 500 笔时，被截掉的最老挂单在「我的订单」里既看不到也撤不掉（前端还按 PENDING 优先排序这 500 条，更容易误以为挂单已消失）。

- 修复建议：读取 `X-Truncated`/`X-Total-Count`（需在请求层透出 headers）并在列表底部提示「仅显示最近 N 条」；或为股票列表加分页/搜索。

### [P3] T-14 仪表盘：卸载时直接丢弃未落盘的布局变更

- 位置：`frontend/src/views/dashboard/DashboardView.vue:359-369`、`581-586`

- 代码：
```ts
let saveTimer: ReturnType<typeof setTimeout> | null = null;
function saveWidgets() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(storageKey.value, JSON.stringify(widgets.value));
    } catch {
      /* 忽略写入失败 */
    }
  }, 200);
}
```
```ts
onBeforeUnmount(() => {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
});
```

- 触发条件：所有布局修改（拖动、缩放、新增、删除）都要经过 200ms 防抖才写入 localStorage；`onBeforeUnmount` 只 `clearTimeout` 而没有 flush。调整控件后 200ms 内点击菜单跳转（`router-view` 以 `route.fullPath` 为 key，跳转即卸载组件）→ 定时器被清掉、修改未落盘。

- 后果：最后一次调整静默丢失，回到仪表盘时控件位置/尺寸仍是改动前的值，用户会认为「改了不生效」。

- 修复建议：`onBeforeUnmount` 中先同步写一次再清定时器（把 `setTimeout` 的回调体抽成 `flushSave()` 复用）。

### [P3] T-15 区域总览：formatValue 依赖的 card.fieldType 后端并不返回，三个类型分支全是死代码

- 位置：`frontend/src/views/regions/RegionOverviewView.vue:450-464`

- 代码：
```ts
function formatValue(card: any) {
  if (!card.valid) return "字段已失效";
  const v = card.value;
  if (v == null || v === "") return "—";
  if (card.fieldType === "BOOLEAN") return v === "true" ? "是" : "否";
  if (card.fieldType === "NUMBER") return String(v);
  if (card.fieldType === "DICTIONARY" || card.fieldType === "LIST") {
    try {
      return JSON.stringify(JSON.parse(v));
    } catch {
      return String(v);
    }
  }
  return String(v);
}
```

- 触发条件：区域带数据来自 `GET /regions/map-overview`，卡片由 `_resolve_cards` 组装，只产出 `{id, displayName, companyId, industryFieldId, zone, value, valid}`（取证 `backend/apps/regions/views.py:169-179`），**不含 `fieldType`**。

- 后果：`card.fieldType` 恒为 `undefined` → 布尔字段在页面上显示 `true`/`false` 而不是「是/否」（`CompanyFieldValue.value` 是 TextField，取证 `backend/apps/companies/models.py:55`，所以恒为字符串，`v === "true"` 的判断永远走不到）；字典/列表字段不做 `JSON.stringify(JSON.parse(v))` 归一，直接输出存库的原始 JSON 串。三处类型分支全部失效。

- 修复建议：在 `_resolve_cards` 里补 `fieldType`（该函数已按 id 取到 `IndustryField`，直接带出即可），前端无需改动。

### [P3] T-16 区域总览：删除数据框/需求未捕获确认框的「取消」，每次都产生未处理的 Promise rejection

- 位置：`frontend/src/views/regions/RegionOverviewView.vue:367-368`、`437-440`

- 代码：
```ts
async function removeFrame(region: any, card: any) {
  await ElMessageBox.confirm(`移除数据框「${card.displayName}」？`, { type: "warning" });
  try {
```
```ts
async function removeDemand(_region: any, d: any) {
  await ElMessageBox.confirm(`删除需求「${d.productType}（${d.quantity} 件）」？`, {
    type: "warning",
  });
  try {
```

- 触发条件：`ElMessageBox.confirm` 在用户点「取消」或关闭时 reject，而这两个函数把 confirm 放在 `try` **之外**（对比 `StockManageView.vue:827-832` 的 `removeStock`、`883-888` 的 `removeAccount` 都用 try/catch 包了 confirm 并 return）。第 42、55、81 行的删除图标都会走到。

- 后果：每次取消删除都抛出一个未处理的 rejection（控制台报错；若应用注册了全局 `unhandledrejection` 或 `app.config.errorHandler`，会升级为用户可见的错误提示）。功能本身不受损（后续代码不执行）。

- 修复建议：`try { await ElMessageBox.confirm(...) } catch { return; }`，与同仓其它视图保持一致。

### [P3] T-17 股票管理：任一前置请求失败会让「账户总览」在首屏完全不加载

- 位置：`frontend/src/views/stocks/StockManageView.vue:783-786`（配合 `758-762` 无 catch 的 `reloadCompanies`、`962` 的 `onMounted(reloadAll)`）

- 代码：
```ts
async function reloadAll() {
  await Promise.all([reloadStocks(), reloadAccounts(), reloadCompanies(), loadRegionOverview(), loadPbSources()]);
  if (canSuper.value) await reloadOverview();
}
```

- 触发条件：`reloadCompanies()` 内部对 `companiesApi.list(...)` 没有 try/catch（758-762），服务不可达/离线且无本地副本时该 Promise reject → `Promise.all` reject → 第 785 行的 `reloadOverview()` 永远不会执行；`onMounted(reloadAll)` 同时产生一个未处理的 rejection。

- 后果：超管打开「股票管理」时「账户总览」整块空白（可用资金/持仓市值/总资产/历史盈亏），要等下一次实时事件触发 `scheduleAccountReload()`（938-951）才有数据；而这类列表请求走的是静默降级的后台同步路径（取证 `api/request.ts:679-682`），失败连提示都没有，用户只看到空白。

- 修复建议：把 `reloadOverview()` 并入 `Promise.all`，或让 `reloadCompanies` 自带 try/catch，避免总览依赖公司列表拉取成功。

---

## 存疑/待确认

1. **[待确认] T-01 的触发面**：我按后端渲染规则推定 `currentPrice` 会以 JSON number 下发（`renderers.py:28-33` 把整数值 Decimal 转 int）。若实际部署还存在其它把 Decimal 统一字符串化的渲染配置，则「点选现价为整数的股票即命中」这条路径不成立，但「用户手动输入整数委托价」这条路径仍然必然命中，缺陷成立。
2. **[待确认] 数量允许非整数/非整手**：`BigNumberInput :min="1"` 只在 UI 上把边框标红（取证 `components/common/BigNumberInput.vue:84-97`），不阻断提交；`canTrade` 只判 `Number(quantity) > 0`，因此 `0.5` 股也能提交。后端 `quantity = DecimalField(max_digits=60, decimal_places=4, min_value=0.0001)`（取证 `serializers.py:328-329`）、持仓 `shares = DecimalField(max_digits=60, decimal_places=4)`（取证 `models.py:111`）本身就是小数口径，未找到「必须整手/100 股整数倍」的约定，**故未作为缺陷上报**；若产品口径要求整手，需前后端同时补校验。
3. **[待确认] ±10% 限价的文案口径**：前端 `priceLimit`（331-340）只用于 `BigNumberInput` 的 min/max（仅标红），`canTrade` 不校验；下单侧后端硬校验 `stock.current_price ± 10%`（取证 `views.py:856-868`），两端一致。但推进轮次的限幅取比赛配置 `limitPct`（取证 `engine.py:1353-1354`，`serializers.py:343` 允许 0.01~0.5），界面文案「限价范围：¥x ~ ¥y（±10%）」在非默认配置的比赛里与实际撮合限幅不符——属展示文案问题，未单列。
4. **[待确认] 「昨收」口径**：`quoteStats.prevClose` 取倒数第二根 K 线的 close（299-300），只有一根 K 线时退化为当根 close，页面标签写作「昨收」；未在后端找到「昨收」字段，属口径定义问题，未作为缺陷上报。
5. **已核对、确认无缺陷（不作为缺陷上报）**：账户与下单的身份归属、跨账户/跨比赛读取均由后端强制（`views.py:852-853`、`781-793`、`990-1002`、`466-469`、`629-637`）；`stock-holdings` / `stock-accounts` 事件由 `apps/common/signals.py` 统一经 `MODEL_TO_RESOURCE` 广播（取证 `realtime/emit.py:157-158`、`common/signals.py:91-133`），两个股票视图的订阅是有效的；订单状态只有 `PENDING/FILLED/CANCELLED` 三种（取证 `models.py:146`），不存在「部分成交被前端显示成已撤」的问题。
6. **已核对、确认无缺陷**：K 线 OHLC 与 volume 在后端均非空（取证 `models.py:194-200`），不会出现 null → `Number(null)=0` 或 NaN 污染 ECharts；涨跌幅/振幅的分母都有 0 值保护（`StockMarketView.vue:304`、`704`、`740-748`），无除零放大。
7. **已核对、确认无缺陷**：区域总览消费的需求/数据框卡片字段与后端 schema 一致（`{id, displayName, companyId, industryFieldId, zone}`，取证 `regions/serializers.py:15-22`），保存不会丢字段；`removeFrame` 之外的数据框保存有 `saving` loading 闸门，无重复提交。
