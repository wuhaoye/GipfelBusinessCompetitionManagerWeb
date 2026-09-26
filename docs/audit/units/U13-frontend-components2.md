# U13 frontend components2（分支归属：master 基线）

## 概述

审计范围（严格限定，未越界）：`frontend/src/components/layout/**`（AppLayout/Sidebar/TopBar）、`frontend/src/components/common/**`（BigNumberInput/DataManager/ErrorBoundary/MobileCards/SearchToggle）、`frontend/src/components/dashboard/**`（DashboardWidget/registerCustomWidgets/types）、`frontend/src/components/{AnnouncementDialog,AnnouncementHistoryDialog,MessageToastHost,VersionUpdateDialog}.vue`。未审计 contracts/**、industry-types/**、preparation/**、views/**；仅在需要解释后果时简要引用 view / store / composable 的行号。

三条影响定级的背景事实（先确认，避免误判优先级）：

1. **`DataManager.vue` 当前不可达（死代码）**。唯一使用方 `frontend/src/views/data-management/DataManagementView.vue` 中：`managerComponent = MANAGER_COMPONENTS[type] ?? DEFAULT_MANAGER`（第 49-51 行），而分支条件是 `v-if="currentConfig"`（第 2 行）与 `v-else-if="isManagerType"`（第 3 行），`isManagerType = MANAGER_TYPES.includes(type)`，`MANAGER_TYPES = Object.keys(MANAGER_COMPONENTS)`（第 41-43 行）——进入第二个分支时 `MANAGER_COMPONENTS[type]` 必然命中，`DEFAULT_MANAGER` 永远不会被渲染；第一个分支要求 `currentConfig` 非空，而 `frontend/src/config/dataModules.ts:27` 的 `moduleConfigs` 是空对象 `{}`（无任何模块配置）。因此 M-06/M-14 属**潜在缺陷**（一旦有人往 `moduleConfigs` 填配置即刻生效），本次按潜在影响定级，不占 P0/P1。
2. 任务清单中提到的 DataManager「分页 / 批量选择 / 导出」能力在当前代码中**并不存在**（无 `el-pagination`、无 `type="selection"` 列、无导出按钮），因此不产生「删除最后一页越界」「刷新后选中项残留」类结论；只报真实存在的竞态、边界与错误处理问题。
3. `BigNumberInput.vue` 被 17 个视图直接使用（合同 ContractManageView、股票 StockMarketView/StockManageView、数据管理各 Manager、区域总览、仪表盘等），是本范围内实际影响面最大的组件，其缺陷按 P2 处理。

其余已实际读码排除的项（避免下游重复劳动）：`el-input` 的 IME 组合态由 Element Plus 自身守卫（`frontend/node_modules/element-plus/es/components/input/src/input.vue_vue_type_script_setup_true_lang.mjs:216-217` `if (isComposing.value) return;`），BigNumberInput 不存在组合态误 emit 问题；`MessageToastHost`、`MobileCards`、`VersionUpdateDialog`、`AnnouncementDialog` 模板均为文本插值，无其它 v-html 注入点；控件包 `componentUrl` 由后端拼 `settings.MEDIA_URL + extract_dir`（`backend/apps/widget_packages/views.py:43`），上传仅超管（同文件 59-61 行），不是「远端任意 URL 脚本注入」。

## 缺陷清单

### [P1] M-01 控件包在「登录前」加载：新会话自定义控件全部缺失，并把用户仪表盘布局永久删掉
- 位置：`frontend/src/components/dashboard/registerCustomWidgets.ts:80`（另见 78-85、29-35）
- 代码：
```ts
// 启动时异步加载，不阻塞应用初始化

loadWidgetPackages();

// 监听控件包变更广播（超管上传/启停/删除时后端推送），所有在线用户自动刷新
onRealtime("widget-package:changed", () => {
  window.location.reload();
});
```
- 触发条件：`frontend/src/main.ts:12` 在 `app.mount` 之前静态 import 本模块，于是 `loadWidgetPackages()` 在**页面加载瞬间**（通常是 /login 登录页、localStorage 里无有效 token）就发出 `GET /api/widget-packages`；该接口 `permission_classes = (IsAuthenticated, PermissionsPermission)`（`backend/apps/widget_packages/views.py:24,51`），匿名请求必然 401（`frontend/src/api/request.ts:127-129` 对「本地已无登录态」的 401 静默丢弃），`list()` 抛错后被 `catch {}` 吞掉（第 74-76 行），**全程没有重试**。登录流程是纯 SPA 跳转（`frontend/src/views/login/LoginView.vue:108,150` → `router.push("/dashboard")`，全仓无登录后 `location.reload()`），因此本次会话内控件包再也不会被加载。第 83 行的 `onRealtime` 同理：`connectRealtime()` 在无 token 时返回 null，`socket?.on` 被跳过（`frontend/src/realtime/socket.ts:112-115`），relay 广播监听也被丢弃。
- 后果：(1) 本次会话所有自定义控件未注册，`DashboardWidget` 落到「未知控件」占位（`DashboardWidget.vue:69-72`）；(2) 更严重的是 `frontend/src/views/dashboard/DashboardView.vue:349-353` 会把「类型未注册」的控件从 `widgets` 中过滤掉，而第 371 行 `watch(widgets, saveWidgets, { deep: true })` 会在 200ms 后把过滤结果写回 localStorage（第 360-368 行）——用户已保存的自定义控件配置被**静默永久删除**，即使之后手动刷新让控件包加载成功，布局也回不来了；(3) 控件包变更广播监听失效，超管上传新控件后该会话不会自动刷新。
- 修复建议：把 `loadWidgetPackages()` 与 `onRealtime` 注册从「模块顶层副作用」改为登录后显式调用（例如放在 `AppLayout` 的 `fetchProfile().then(...)` 旁，与 `messageStore.initRealtime()` 同一时机），失败时按需重试；注册成功后用 `listCustomWidgets()` 的长度变化触发一次仪表盘重载；`DashboardView.loadWidgets` 的过滤必须先确认「控件包已加载完成」再执行（或对未注册类型保留配置而不落盘）。

### [P1] M-02 退出登录未重置消息实时监听：同标签页换账号后实时消息弹窗与红点永久失效
- 位置：`frontend/src/components/layout/TopBar.vue:108-111`（另见 `AppLayout.vue:66-71`、`stores/message.ts:76-98`）
- 代码：
```ts
function handleLogout() {
  authStore.logout();
  router.push("/login");
}
```
- 触发条件：账号 A 登录 → `AppLayout` 挂载 → `messageStore.initRealtime()`（`AppLayout.vue:69`）把 `initialized` 置为 `true` 并把 `message:new` 绑在 socket 实例 S1 上 → 用户点「退出」（`TopBar.vue:51`）→ `authStore.logout()` 调 `disconnectRealtime()` 销毁 S1（`stores/auth.ts:189`），但**退出路径不派发 `auth:kicked` 事件**（`auth:kicked` 只在 401 拦截器与 socket 连接错误时派发，见 `api/request.ts:148`、`realtime/socket.ts:66,77`），而重置幂等锁的 `handleAuthKicked` 只监听该事件（`stores/message.ts:92-98`）→ `initialized` 保持 `true` → 同标签页登录账号 B → `AppLayout` 再次挂载 → `initRealtime()` 在 `if (initialized) return` 处直接返回（`stores/message.ts:77`）→ 新 socket S2 上**没有** `message:new` 处理器。
- 后果：账号 B 在本次会话内完全收不到实时消息滑入弹窗，侧边栏红点也不会随新消息增长（只有首次 `fetchUnread()` 的值），直到用户手动刷新页面；同时 A 账号尚未自动关闭的 toast（`toasts` 数组未清空）会继续显示在 B 的界面上，造成跨账号信息泄露。对照组：被顶号/登录过期路径因为派发了 `auth:kicked`，`initialized` 会被正确重置，所以该缺陷只在「主动退出→重新登录」这条路径出现，容易被漏测。
- 修复建议：让 `authStore.logout()` 统一派发 `auth:kicked`（或直接调用 `messageStore.handleAuthKicked()`），并在登出时清空 `toasts`、`unreadCount`；`initRealtime` 改为按 socket 实例判断是否已绑定（例如比较 `getSocketInstance()`），而不是用一次性布尔锁。

### [P2] M-03 DashboardWidget 拖拽/缩放未处理 pointercancel 与组件卸载：幽灵拖拽 + 全局监听泄漏
- 位置：`frontend/src/components/dashboard/DashboardWidget.vue:166-171`（另见 139-154 的清理逻辑）
- 代码：
```ts
  // 捕获指针：移动端触摸拖动时手指可能移出元素边界，capture 保证后续事件仍送达
  (e.target as HTMLElement).setPointerCapture(e.pointerId);
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  e.preventDefault();
}
```
- 触发条件：注册的只有 `pointermove` + `pointerup`，**没有 `pointercancel` / `lostpointercapture`**（全目录 grep 无匹配），也**没有 `onUnmounted` 清理**（该文件只 import 了 `computed`）。两种触发方式：(a) 触摸设备上浏览器接管手势（双指缩放、系统边缘手势、来电等）会发 `pointercancel` 而不是 `pointerup`，`dragging`/`resizing` 永远停在 `true`，两个 window 监听也永不摘除；(b) 拖拽过程中组件被卸载（父级切换到别的控件集合、路由跳走、`locked` 由外部改写的重渲染等），监听同样留在 window 上。
- 后果：(a) 之后鼠标/手指在页面任意位置移动都会继续 `emit("patch", { x: oy + … })`（第 128-138 行），控件不受控地跟着指针跑，并在 200ms 后被 `DashboardView` 的 `watch(widgets, saveWidgets, {deep:true})` 持久化到 localStorage（`DashboardView.vue:360-371`），刷新后布局仍是乱的；(b) 泄漏的闭包一直持有 `props.widget` 与已卸载组件实例，对每个挂载过的控件持续累积，属于长期会话的稳定泄漏。
- 修复建议：把 `onUp` 同时绑到 `pointerup` 与 `pointercancel`，`onUnmounted(() => onUp())` 兜底摘除监听并复位 `dragging/resizing`；更稳妥的是改用 `element.setPointerCapture` + 元素上的 `@pointermove/@pointerup/@pointercancel`，避免向 window 挂全局监听。

### [P2] M-04 拖拽不裁剪 x/y：控件可被拖到画布左上角之外并被持久化，永久不可达
- 位置：`frontend/src/components/dashboard/DashboardWidget.vue:128-138`（配合 110-114、`DashboardView.vue:647-656`）
- 代码：
```ts
function onMove(e: PointerEvent) {
  if (dragging) {
    emit("patch", { x: ox + (e.clientX - sx), y: oy + (e.clientY - sy) });
  } else if (resizing) {
    // 非等比缩放：宽度随横向拖动、高度随纵向拖动，各自独立
    emit("patch", {
      w: clamp(ow + (e.clientX - rsx)),
      h: clamp(oh + (e.clientY - rsy)),
    });
  }
}
```
- 触发条件：`clamp()` 只用于缩放得到的 `w/h`，位移得到的 `x/y` 一路无边界（第 130 行写入，142 行松手时也只做 `snap()` 取整，负数照旧）。用户在触屏/鼠标上把控件往左上方拖出画布即可（例如把「新建」后默认在 `x:60,y:60` 的控件拖到 `x:-300, y:-300`）。
- 后果：`.dash-canvas` 是 `position:relative; overflow:auto`（`DashboardView.vue:647-656`），滚动条只能补足右下方向的溢出，负坐标区域永远滚不到——控件（连它的缩放柄和右键菜单）既看不见也点不到，用户无法再编辑或删除它；坐标经 `watch(widgets, saveWidgets)` 写入 localStorage（`DashboardView.vue:332-371`），刷新、换设备（同账号同比赛）后依旧「消失」。极端情况下控件完全叠在画布外的深链接位置，用户只会认为「控件丢了」。
- 修复建议：给 `x/y` 也加边界（`x = Math.max(0, Math.min(canvasW - w, v))`，`y` 同理），在 `onUp` 里统一钳制而不只是 `snap`；渲染/反序列化时（`loadWidgets`）对越界坐标做一次自愈钳制，避免历史脏数据继续不可达。

### [P2] M-05 BigNumberInput 的 min/max 校验在超大数上被静默跳过，且非法输入仍写入 v-model 并可提交
- 位置：`frontend/src/components/common/BigNumberInput.vue:88-95`（另见 80-82、97）
- 代码：
```ts
  if (props.min != null && Number.isFinite(props.min)) {
    const n = Number(s);
    if (Number.isFinite(n) && n < props.min) return true;
  }
  if (props.max != null && Number.isFinite(props.max)) {
    const n = Number(s);
    if (Number.isFinite(n) && n > props.max) return true;
  }
```
- 触发条件：(1) 用户粘贴/输入一个超过 double 上限的纯数字串（约 309 位以上，例如把 `9` 连按 400 次或粘贴 1000 位数字）：`NUM_RE` 通过（全是数字）→ `Number(s)` 为 `Infinity` → `Number.isFinite(n)` 为 false → **min 与 max 两项检查都被跳过** → `invalid` 返回 false，界面**完全没有错误提示**；组件也不做任何长度/位数上限（无 `maxlength`、无 `max_digits` 对应参数）。(2) 输入 `-5`、`abc`、`1e5`、`Infinity` 等：`invalid` 为 true 只表现为红框（第 101-103 行样式），`onInput` 依旧把清洗后的字符串 emit 出去（第 80-82 行），而组件自带文档也把拦截责任推给表单（第 32-33 行注释）；实际调用方普遍没有数字规则，例如 `views/data-management/MaterialsManager.vue:157-169` 的价格输入甚至不在任何 `el-form-item prop` 之内，`onNodePriceChange` 直接写进 `form.nodePrices`，最终随 JSON 提交。
- 后果：极大/非法数值在没有前端提示的情况下进入请求体，后端 DecimalField 的 `max_digits` 校验失败返回 400，用户只看到笼统的「请求参数错误，请检查输入」，无法定位是哪一格；而在 `StockMarketView` 这类用 `Number(price) > 0` 自行判定的页面（`views/stocks/StockMarketView.vue:368-374`），`"1e5"`、`"Infinity"` 会让 `canTrade` 为 true 并真的提交，前端顺序/限价校验被绕过。声明为「大数安全」的组件在最关键的超大数分支上反而失效。
- 修复建议：用字符串/`BigInt` 比较替代 `Number`（按符号、整数位长度、字典序比较，或 `BigInt` 转 `BigDecimal` 风格比较），非有限数一律判为 `invalid`；为输入加位数上限（与后端 `max_digits`/`decimal_places` 对齐，作为 prop 暴露）；`invalid` 为 true 时提供 `emit("invalid")` 或在插槽内提示，供表单 `rules` 直接拦截提交。

### [P2] M-06 DataManager 无请求时序保护：切比赛时旧响应覆盖新数据，loading 提前结束，旧行可被误删【当前不可达】
- 位置：`frontend/src/components/common/DataManager.vue:257-267`（另见 253-255、302-320）
- 代码：
```ts
async function loadData() {
  loading.value = true;
  try {
    if (!compStore.competitionId) {
      data.value = [];
      return;
    }
    const res: any = await (props.api as any).list({ competitionId: compStore.competitionId });
    if (Array.isArray(res)) data.value = res;
    else if (res && (res as any).items) data.value = (res as any).items;
```
- 触发条件：`loadData` 既被 `onMounted` 调用（第 250 行），又被 `useCompetitionReload` 在 `competitionId` 变化时调用（第 253-255 行，且该 composable 在 id 变空/变化时都会 `reload()`，`frontend/src/composables/useCompetitionReload.ts:17-27`），但没有任何请求序号、`AbortController` 或「最后写入者校验」。真实时序：账号带 localStorage 残留比赛 A → 首屏 `loadData(A)` 发出（慢）→ `AppLayout` 的 `fetchProfile().then(applyOwnCompetition)`（`AppLayout.vue:66-71`）把比赛切成 B（`stores/competition.ts:80-102`，先同步写 `selected.value = {id:B}`）→ watcher 触发 `data.value = []` 后 `loadData(B)` → B 先返回并渲染 → **A 的响应后到，直接覆盖 `data.value`**，于是界面显示的是另一个比赛的记录。同时 `loading` 是单一布尔（第 258、271 行）：先返回的请求把它置 false，后发的请求其实还在飞。
- 后果：跨比赛数据串档（越权可见：A 比赛的数据出现在当前比赛 B 的列表中）；列表中的删除/编辑按钮仍然可点，`handleDelete` 会把**旧比赛的行 id** 与**新比赛的 `competitionId`** 一起提交（第 313-314 行 `props.api.remove(id, compStore.competitionId)`），若后端按 id 删除而不二次校验归属，即造成跨比赛误删；此外 `loadData` 的 `catch` 只 `console.error`（第 268-270 行），而走本地缓存的 GET 被缓存层强制标记为 `silent: true`（`api/request.ts:679-682`），失败时用户既看不到错误提示又看到空列表，会误以为数据被清空。
- 修复建议：引入自增 `requestSeq`（或 `AbortController`），响应回来时比对序号，非最新请求直接丢弃；`loading` 用计数或只允许最新请求复位；把当前请求的 `competitionId` 一并记住，`remove/update` 前校验行归属比赛；`catch` 中改用 `ElMessage.error` 明确提示「加载失败」而不是静默清空。
- 备注：本组件当前不可达（见概述第 1 点），但 `moduleConfigs` 一旦启用即成为真实缺陷；同一模式在所有 Manager 视图中都存在，建议下游一并核对。

### [P2] M-07 BigNumberInput 的 display 直接 String(number)：指数记法被显示为非法值并原样回传
- 位置：`frontend/src/components/common/BigNumberInput.vue:68-78`（另见 84-97）
- 代码：
```ts
const NUM_RE = /^[+-]?\d+(\.\d+)?$/;

/** 剥离千分位逗号 / 空格 / 全角逗号 / 下划线，容忍粘贴带分隔符的数字。 */
function clean(raw: string): string {
  return raw.replace(/[,，\s_]/g, "");
}

const display = computed(() => {
  if (props.modelValue == null) return "";
  return String(props.modelValue);
});
```
- 触发条件：`modelValue` 是从 HTTP JSON 直接反序列化来的 JS `number`，`String()` 在 |x| ≥ 1e21 或 |x| < 1e-6 时会产出指数形式（`String(1e21) === "1e+21"`、`String(0.0000001) === "1e-7"`）。调用方一律把行数据直接喂回来，例如 `openEdit` 用 `getNested(row, prop)` 原样赋值（`DataManager.vue:293-300`），视图侧同理（如 `DashboardView` 的 `editForm.total`）。此时 `NUM_RE` 不匹配 → `invalid` 为 true（红框「请输入有效数字」），但 `display` 仍显示 `1e+21`，且用户不改动直接提交时 `form` 里就是字符串 `"1e+21"`。
- 后果：一个完全合法的后端数值被判为「无效数字」，用户被迫手工重敲（且极可能敲错精度）；不改就提交时把指数记法发给后端（DRF `DecimalField` 会接受 `Decimal("1e+21")`，但 `max_digits` 校验/业务校验行为依字段而定，可能 400 或存成非预期精度）。同时 `clean()` 把空格、下划线从数字中间无条件删除（`"1 2 3"` → `"123"`、`"1_0"` → `"10"`），粘贴带空格的文本会被静默改值而不是拒绝。
- 修复建议：`display` 先归一化数值来源——后端应下发字符串（Decimal→str），前端对 `number` 类型用 `toFixed`/`toPrecision` 展开或用 `Number.prototype.toLocaleString('fullwide', {useGrouping:false})`；`NUM_RE` 增加对指数记法的显式处理（要么接受并转普通记法，要么明确拒绝并提示）；`clean()` 只剥离首尾分组符与纯千分位模式（如 `/^(?:\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?$/`），避免把中间分隔符当噪声删除。

### [P3] M-08 MessageToastHost 无数量上限且点击即关闭、正文截断三行：批量消息会铺满屏幕且无法读全文
- 位置：`frontend/src/components/MessageToastHost.vue:4-11`（另见 97-106 样式、`stores/message.ts:57-59`）
- 代码：
```html
      <div v-for="t in toasts" :key="t.key" class="toast-card" @click="close(t.key)">
        <div class="toast-icon"><Bell /></div>
        <div class="toast-body">
          <div class="toast-head">
            <span class="toast-title">{{ t.title }}</span>
            <span v-if="t.senderName" class="toast-sender">{{ t.senderName }}</span>
          </div>
          <div class="toast-content">{{ t.content }}</div>
```
- 触发条件：`toasts` 无上限（store 每次 `message:new` 都 push，仅 6s 后按 key 移除，`stores/message.ts:57-59`），宿主也只做 `flex-direction: column`、没有 `max-height`/`overflow`（第 42-47 行）。当管理员群发/批量发送消息（短时间内 20+ 条）时，卡片会向下无限堆叠；正文被 `-webkit-line-clamp: 3` 截断（第 101-104 行），标题 `white-space: nowrap` 截断（第 88-91 行），而卡片唯一的交互 `@click` 是**关闭**，没有「查看全部」入口。
- 后果：短时间批量消息把可视区右侧铺满（每张 320px 宽、含阴影），长消息被截断且点一下就消失，用户只能去消息中心翻记录；超出视口高度的卡片虽然看不见却因 `pointer-events: auto` 占据点击区域，遮挡下层 UI 直到 6s 自动关闭；极端情况会造成「界面卡住」的错觉。
- 修复建议：宿主限制同时展示条数（如只渲染最新 3-5 条并在尾部显示「还有 N 条」）或对 `.toast-stack` 设 `max-height: calc(100vh - 80px); overflow: hidden`；正文提供 `title` 属性或点击展开原文（把「关闭」改为独立的 ✕，卡片点击改为跳转消息中心对应消息）。

### [P3] M-09 仪表盘「锁定布局」下右键仍可选中并编辑/删除控件
- 位置：`frontend/src/components/dashboard/DashboardWidget.vue:75-84`（另见 172-175、155-159）
- 代码：
```html
    <div v-if="selected" class="dw-tool">
      <button title="编辑" @pointerdown.stop @click.stop="emit('edit')">✎</button>
      <button title="删除" @pointerdown.stop @click.stop="emit('remove')">✕</button>
    </div>
    <div
      v-if="selected && !locked"
      class="dw-resize"
      title="拖动缩放"
      @pointerdown="onResizeDown"
    ></div>
```
- 触发条件：`onDown` 在 `props.locked` 时提前 return（第 156 行），所以左键点选确实被禁止；但 `onCtx`（右键菜单回调）**不检查 locked**，先 `emit("select")` 再 `emit("contextmenu")`（第 172-175 行），`DashboardView` 收到后弹出含「编辑 / 删除」的右键菜单（`views/dashboard/DashboardView.vue:82-90`），并且此时 `selected` 为 true，工具条上的 ✎/✕ 也会出现（只有缩放柄被 `!locked` 挡掉）。
- 后果：用户显式「锁定布局」的语义是「防误改」，但一个右键即可进入编辑/删除流程（删除还会改写 localStorage 中的布局），锁定形同虚设，误操作丢失控件布局。属一致性缺陷，非权限绕过（`locked` 只是本页局部开关，非权限位，`DashboardView.vue:302`）。
- 修复建议：`onCtx` 同样在 `props.locked` 时直接返回（或只允许「查看」类菜单项）；工具条渲染条件改为 `v-if="selected && !locked"`，与缩放柄保持一致。

### [P3] M-10 Sidebar 菜单高亮对详情路由失效，`dataRoutes` 分支是死代码
- 位置：`frontend/src/components/layout/Sidebar.vue:274-279`（另见 260-279、`router/index.ts:237-242`）
- 代码：
```ts
  if (dataRoutes.includes(path)) {
    return path;
  }
  // 顶级菜单（合同管理、公司、账户、仪表盘等）直接按路径高亮
  return path;
});
```
- 触发条件：`if` 分支与 finally 返回完全相同（都返回 `path`），因此 `activeMenu` 恒等于 `route.path`。而路由表里存在子路径 `companies/:id`（`frontend/src/router/index.ts:237-242`，名称为「公司详情」），从公司列表点进详情后 `route.path` 变为 `/companies/12`，`el-menu` 的 `:default-active` 匹配不到任何 `index`（菜单项 index 只有 `/companies`）。
- 后果：进入公司详情页后左侧「公司管理」高亮消失，用户失去位置感，也看不出当前处于哪个一级模块；该 `activeMenu` 计算属性显然是为映射详情路由而写却未实现（死分支）。
- 修复建议：改为按「路由记录的前缀/父级」匹配，例如 `const hit = menuPaths.find(p => path === p || path.startsWith(p + "/")); return hit ?? path;`（`menuPaths` 显式列出菜单 index），或直接读取 `route.matched` 中最靠近一级菜单的那条记录。

### [P3] M-11 AnnouncementHistoryDialog 的 v-html 净化未归一化控制字符，`href` 协议白名单可被绕过
- 位置：`frontend/src/components/AnnouncementHistoryDialog.vue:9-14`（根因在 `frontend/src/utils/sanitizeHtml.ts:29-37`）
- 代码：
```html
      <div class="ah-head">
        <span class="ah-title">{{ a.title }}</span>
        <span class="ah-meta">v{{ a.version }} · {{ a.date }}</span>
        <el-tag v-if="i === 0" size="small" type="success" effect="light">最新</el-tag>
      </div>
      <div class="ah-content" v-html="sanitizeHtml(a.content)"></div>
```
```ts
/** 危险 URI 协议（svg+xml 可携带可执行脚本，需一并拦截）。 */
function isDangerousUri(value: string): boolean {
  const v = value.trim().toLowerCase();
  return (
    v.startsWith("javascript:") ||
```
- 触发条件：公告正文来自服务端（`annStore.history`，`announcementsApi.list()`）并直接进 `v-html`，唯一防线是 `sanitizeHtml`。该函数对 `href/src` 只做 `trim + toLowerCase + startsWith` 前缀判断，**不剥离 ASCII 控制字符**，也不做协议归一化；HTML 解析器会把属性里的字符引用解码（如 `&#x09;` → TAB），于是 `<a href="jav&#x09;ascript:alert(document.cookie)">点我</a>` 解码后为 `"jav\tascript:…"`，不满足任何 `startsWith` 前缀 → 属性被保留、原样输出到 DOM；浏览器在按 URL 规范解析时会先「移除所有 ASCII tab 与换行」，最终得到 `javascript:` 并执行（`&#x0A;` 换行同理）。同一函数对 `style` 的判定也依赖子串包含，存在同类归一化缺口。
- 后果：存储型 XSS 的注入面（点击链接即执行任意脚本，可读取 localStorage 中的 token）。**可利用性受限**：公告的写接口仅超管可写（`backend/apps/announcements/views.py:44-45,89,107` 均校验 `role == "SUPER_ADMIN"`），因此当前更接近「防御纵深失效 + 未来放开权限即成 P1」；另外 `AnnouncementDialog` 渲染的是开发者硬编码内容（`frontend/src/data/announcement.ts`），无此风险。浏览器最终执行一步依赖 URL 规范的控制字符剥离行为，本次环境无 jsdom 无法端到端复现，标 [待确认]。
- 修复建议：净化前先做协议归一化——去掉属性值中的所有 C0 控制字符与不可见字符（`/[\u0000-\u0020\u00a0\u1680\u2000-\u200f\u2028\u202f\u205f\u3000]/g`）再判定协议；采用白名单而非黑名单（`http/https/mailto` + 同源相对路径），非白名单一律 `removeAttribute`；`style` 建议整体替换为白名单属性（color/font-weight…）。长期建议改用成熟库（DOMPurify）并配 CSP。

### [P3] M-12 ErrorBoundary 的「返回首页」错误提示分支永不触发，且先清空错误态
- 位置：`frontend/src/components/common/ErrorBoundary.vue:54-60`
- 代码：
```ts
/** 返回首页：清空错误状态并跳转到仪表盘。 */
function goHome(): void {
  error.value = null;
  router.push("/").catch(() => {
    ElMessage.warning("已在首页");
  });
}
```
- 触发条件：vue-router 4 的 `router.push` 在「重复导航」时是 **resolve 一个 NavigationFailure**，而不是 reject；只有在导航守卫内部抛错时才 reject。因此当用户就在 `/`（或被 `needsPasswordChange` 守卫重定向、导航被取消）时，`.catch` 不会执行。
- 后果：`error.value` 已被置空，`<slot>` 立刻按同一 key 重新渲染崩溃页面 → 组件重新挂载 → 多半再次抛出同样的异常 → 再次进入错误页，而用户点「返回首页」后**没有任何反馈**（既不提示「已在首页」，也没有可感知的变化），表现为按钮「点了没反应」，只能反复点或手动改地址栏。
- 修复建议：改用 `router.push("/").then((failure) => { if (failure) ElMessage.warning("已在首页"); })`，或在跳转前判断 `route.path === "/"`；`error.value = null` 应放在导航成功之后（或先 `router.replace` 到目标、由新的 route key 触发重建），避免清空错误态后原地重渲染崩溃组件。

### [P3] M-13 动态控件包 manifest 字段不做校验：绑定键冲突、NaN 尺寸、未知配置类型直接进注册表
- 位置：`frontend/src/components/dashboard/registerCustomWidgets.ts:54-62`（另见 47-69、`types.ts:127-135`）
- 代码：
```ts
          fieldSlots: Array.isArray(manifest.fields)
            ? manifest.fields.map((f: any) => ({ key: f.key, label: f.label || f.key, required: !!f.required }))
            : undefined,
          configFields: Array.isArray(manifest.configFields)
            ? manifest.configFields.map((f: any) => ({
                key: f.key,
                label: f.label || f.key,
                type: f.type || "string",
                default: f.default,
```
- 触发条件：`manifest` 来自服务端 JSON（`pkg.manifest || {}`，第 35 行），上述映射只做「数组判断」，不校验元素形状：`f.key` 缺失/为空串/重复时 `fieldSlots` 会出现 `key: undefined` 或多个同键项；`manifest.defaultSize` 只做类型断言 `as {w,h}`（第 52 行），字符串或缺失字段会透传成 `w: "abc"`；`configFields[].type` 未按 `ConfigField` 联合类型校验（`types.ts:67` 只允许 string/number/color/boolean/select），可传任意字符串。`registerCustomWidget` 本身只校验 `type/label/component`（`types.ts:128-132`），不校验这些字段。
- 后果：控件绑定按 `values[key]` 取值，键为 `undefined`/重复时多个字段互相覆盖，控件显示错值；尺寸异常会让 `createWidget` 生成 `w/h` 非法值，控件在画布上塌缩成不可见或撑爆布局（`types.ts:169-177`）；未知的 `configFields.type` 会让编辑弹窗渲染出无法交互的配置项，用户设置的配置无法保存。均为「控件包作者出错即污染所有使用者仪表盘」的低成本故障。
- 修复建议：注册前做一次 manifest 归一化校验：`key` 必须是非空字符串且唯一（否则跳过该字段并 `console.warn`）、`defaultSize` 必须为有限正数并做 `clamp`、`configFields.type` 不在白名单时回退 `"string"`；校验失败整体拒绝注册并提示控件包作者，而不是「尽力填充」。
- 备注：`loadScript` 用 `<script>` 注入 `pkg.componentUrl`（第 18-26 行）。已核对后端拼 URL 的方式（`backend/apps/widget_packages/views.py:43` 用 `settings.MEDIA_URL + extract_dir`，上传仅超管，59-61 行），因此不是「远端任意 URL 脚本注入」；残留风险是控件包 JS 与主应用同源执行、可直读 localStorage token（`window.__widget_module__` 全局约定也无完整性校验），建议在控件包上传端加签名/校验并纳入 CSP，此项归安全加固、不计入缺陷清单。

### [P3] M-14 DataManager 搜索把 0/false 当空串，且切换比赛后过滤词残留【当前不可达】
- 位置：`frontend/src/components/common/DataManager.vue:217-224`（另见 253-255）
- 代码：
```ts
  const q = searchText.value.toLowerCase();
  return data.value.filter((item: any) =>
    props.columns.some((col) => {
      const val = getNested(item, col.prop);
      return String(val || "")
        .toLowerCase()
        .includes(q);
    }),
  );
```
- 触发条件：(1) 用 `val || ""` 归一化，值为 `0`、`false`、`NaN` 时全部塌缩成空串，搜索框输入 `0` 无法命中任何数量/价格为 0 的行（`null/undefined/""` 与 `0` 不可区分）。(2) `searchText` 是组件本地状态，`useCompetitionReload` 只清 `data`（第 253-255 行）不清搜索词；切换比赛后列表被上一个比赛输入的关键词继续过滤，手机端 `SearchToggle` 收起为放大镜按钮（`SearchToggle.vue:16-24`），界面上看不到过滤条件仍在生效。
- 后果：用户搜 `0` 得到「无数据」而实际有记录，误判数据丢失；切换比赛后看到「空列表/少了几行」且找不到原因（需先点开放大镜才能看到残留关键词）。
- 修复建议：用 `val == null ? "" : String(val)`；切换比赛时一并 `searchText.value = ""`（或让 `useCompetitionReload` 的 `clear` 回调同时复位搜索词）；`SearchToggle` 在 `modelValue` 非空而处于收起态时给出「已过滤」视觉标记。

## 存疑/待确认

1. **[待确认] DataManager 是否确实不可达**：结论基于 `frontend/src/config/dataModules.ts:27` 的 `moduleConfigs = {}` 与 `DataManagementView.vue:41-51` 的分支构造；若本 feature 分支计划在别处填充 `moduleConfigs`（或动态注册模块配置），则 M-06/M-14 立即由「潜在」变为「在线」，建议由数据管理模块的负责人确认。
2. **[待确认] 列表 50 条截断的跨模块机制**：`frontend/src/api/request.ts:336-343` 的 `reconstruct()` 在未显式传 `page/pageSize` 时按 `page=1/pageSize=50` 切片，随后 `normalizeListResponse` 只把 `items` 交给组件（第 312-324 行）。若某列表接口返回分页形态且总条数 > 50，DataManager 及其它列表组件会静默只显示前 50 条（组件侧既无分页 UI 也不读 `total`）。此处只做交叉引用，根因在 api 层，请归口给 request/cache 的审计项，避免重复上报。
3. **[待确认] `widget-package:changed` 强制整页刷新**：`registerCustomWidgets.ts:83-85` 收到广播即 `window.location.reload()`，超管上传/启停控件包会让所有在线用户强刷，未保存的表单内容（如正在填写的合同/数据）会丢失。是否属可接受设计需产品确认；若不可接受，建议改为「热更新注册表 + 提示刷新」。
4. **[待确认] 消息去重与重连重复**：`stores/message.ts:63-73` 的 `onNewMessage` 不按 `payload.id` 去重，`MessageToastHost` 也按本地自增 `key` 渲染。若后端在重连（`sync:replay` 之外的通道）补发未确认消息，界面上会出现重复 toast。需要后端确认 `message:new` 的投递语义（at-most-once / at-least-once）后才能定级，本次不计入缺陷。
5. **[待确认] 断线重连后的列表刷新**：本次范围内没有任何组件监听 `sync:reconciled`（`api/request.ts:846-854` 派发该事件），DataManager 之类长驻列表在断线期间的增删改需等下一次用户操作/切比赛才可见。因 DataManager 当前不可达且各 Manager 视图可能自行订阅，此处仅记录观察，不作结论。
6. **已核对无问题项**（记录以免重复投入）：`el-input` 组合态守卫（见概述第 3 条）；Toast/卡片/版本弹窗无 v-html；`VersionUpdateDialog.vue` 通过 `:show-close="false"` + 禁用 ESC/遮罩关闭实现「必须刷新」的硬提示，逻辑自洽；`MobileCards.vue` 的取值、插槽与空态未发现缺陷；`ErrorBoundary` 的 `onErrorCaptured` 返回 `false` 用法正确（同步渲染/生命周期异常确实被隔离，但不覆盖 Promise/定时器内的异步异常——这属 Vue 机制限制，且 `main.ts:52-55` 已有全局兜底）。
