# U11 frontend views biz2（分支归属：master 基线）

## 概述

**审计范围（严格限定）**：`frontend/src/views/` 下 8 个文件（master 基线代码）

| 文件 | 行数 | 结论数 |
| --- | --- | --- |
| `frontend/src/views/account-management/AccountManagementView.vue` | 572 | 6 |
| `frontend/src/views/companies/CompanyListView.vue` | 330 | 2 |
| `frontend/src/views/companies/CompanyDetailView.vue` | 390 | 1 |
| `frontend/src/views/competitions/CompetitionListView.vue` | 511 | 2 |
| `frontend/src/views/messages/MessageCenterView.vue` | 689 | 4 |
| `frontend/src/views/login/LoginView.vue` | 240 | 2 |
| `frontend/src/views/system/AuditLogView.vue` | 302 | 2 |
| `frontend/src/views/settings/SettingsView.vue` | 371（master 版本，`git show master:...` 取得，已剔除 602eab1 新增的「比赛准备」17 行） | 3 |

**方法**：逐个 read 目标文件；对每条结论都向前端请求层（`api/request.ts` 的缓存降维 + 全量副本 + `reconstruct` 切片）、鉴权/权限层（`stores/auth.ts`、`permissions/catalog.ts`、`router/index.ts`）、后端端点（`apps/users|companies|competitions|messages|audit|announcements`）与 `apps/common/pagination.py`、`middleware.py` 交叉取证，确保「前端看到的字节」与「后端真正返回的字节」一致。未审计 `components/**`（涉及处只给位置引用，不单独成条）。

**结论分布**：P0 × 0，P1 × 1，P2 × 9，P3 × 9，共 19 条。

**系统性问题（多条缺陷的共同根因）**

1. **请求层分页降维 + `reconstruct` 隐式切片**：`api/request.ts:312-324` 把 `{items,total,...}` 降维成裸数组且丢弃 `total`；`api/request.ts:336-343` 的 `reconstruct` 在请求未带 `pageSize` 时**默认 50**。于是「服务端分页 + 前端不分页」的写法会让列表静默截断，且页面拿不到 total、无法察觉（A-01/A-03）。
2. **契约漂移**：`AuditLog.changes` 前端声明为 `string`（`types/api.ts:533`），后端 `audit/serializers.py:49` 经 `_parse_json` 返回**对象**，前端 `JSON.parse` 必然抛错（A-02）。
3. **错误被吞后继续当作成功**：`stores/message.ts:101-108` 的 `markAllRead` 永不 reject，导致视图里的 `try/catch` 是死代码（A-06）；`AccountManagementView`/`CompanyListView` 多处 `catch { console.error }` 使失败被当作空数据（A-04/A-17）。
4. **无并发/竞态保护**：列表加载无请求代次校验（A-05）、登录无重复提交防护（A-07），配合后端「每次登录递增 token_version 顶号」的设计会放大后果。

---

## 缺陷清单

### [P1] A-01 账号管理列表只取服务端第一页（pageSize=20），第 21 个起的账号在界面上完全不可见

- 位置：`frontend/src/views/account-management/AccountManagementView.vue:365`
- 代码：
```vue
async function loadSystemUsers() {
  try {
    const res = await usersApi.list({ competitionId: "null" });
    // 后端返回 { items, total } 分页对象，但 cachedApi 已把列表响应降维为裸数组，
    // 故 res 可能是数组；统一兼容两种形态。
    systemUsers.value = Array.isArray(res) ? res : res?.items ?? [];
  } catch (e) {
    console.error("加载系统账号失败:", e);
  }
}
```
- 触发条件：任一 tab 的账号数 > 20。链路已核实：`api/index.ts:59-66` 在未传分页参数时补 `page=1&pageSize=20`；`backend/apps/users/views.py:64-69` 按 `parse_pagination` 真分页并按 `-updated_at` 排序返回 `{items,total}`；`api/request.ts:403` 的 `reconstruct(items, "paged", params)` 用 `params.pageSize=20` 切出前 20 条，`request.ts:321` 再把 `items` 降维成裸数组（`total` 被丢弃）。该页既没有 `el-pagination`，也没有刷新按钮（模板 1-14 行只有「新建账号」）。
- 后果：比赛账号/系统账号超过 20 个时，排名 21 之后的账号（按最近更新时间排序，**越久没动过的账号越靠后**）在本页永久不可见、不可编辑、不可重置密码、不可删除；连"列表被截断了"都无从发现（无 total、无页码）。典型事故：某选手忘记密码，管理员在列表里找不到该账号，只能反复重试；管理员按名单补建账号时被后端以"用户名已存在"拒绝，却找不到冲突的那个账号。**同源位置**：`frontend/src/views/companies/CompanyListView.vue:198`（`api.get("/companies", {params:{competitionId}})` 不带分页 → `reconstruct` 默认 pageSize=50，超过 50 家的公司同样消失）、`frontend/src/views/competitions/CompetitionListView.vue:307`（`/competitions` 同样被切到 50 条）。
- 修复建议：显式请求大页（`pageSize` 上限 200，必要时翻页拉全）或在页面加 `el-pagination` 并展示 `total`（用 `normalize:false` 保留分页对象）；短期至少在拿到 `total > items.length` 时给出"仅显示前 N 条"的提示与跳页入口。

### [P2] A-02 审计日志「变更内容」必然解析失败：概要列恒为 `-`，详情弹窗显示 `[object Object]`

- 位置：`frontend/src/views/system/AuditLogView.vue:185`
- 代码：
```ts
function brief(row: AuditLog): string {
  if (row.kind === "error") return row.errorSummary || "-";
  if (row.changes) {
    try {
      const keys = Object.keys(JSON.parse(row.changes));
      return keys.length ? `变更字段：${keys.join("、")}` : "-";
    } catch {
      return "-";
    }
  }
  return "-";
}
```
- 触发条件：查看任意 `kind=write` 的日志（只要它有 `changes`）。后端 `apps/audit/serializers.py:49` 是 `"changes": _parse_json(instance.changes)`，`_parse_json` 对 JSON 字符串做 `json.loads` 后返回 **dict**；前端 `types/api.ts:533` 却声明 `changes: string | null`。`JSON.parse(对象)` → `JSON.parse("[object Object]")` → SyntaxError → 命中 `catch` 返回 `"-"`；同样的错误也发生在 `AuditLogView.vue:203-211` 的 `prettyChanges`：`JSON.stringify(JSON.parse(raw), null, 2)` 抛错后 `return raw` 直接返回对象，模板 `<pre>{{ prettyChanges }}</pre>`（146 行）渲染为 `[object Object]`。
- 后果：审计日志最核心的信息（改了哪些字段、改前改后是什么）在界面上完全不可读——列表「概要」列恒为 `-`，详情「变更内容」恒为 `[object Object]`。追责/回溯场景下等于没有留痕可用，而"（敏感字段已脱敏）"的提示会让查看者以为内容是完整可信的。
- 修复建议：按对象处理（`typeof row.changes === "string" ? JSON.parse(...) : row.changes`），并把 `types/api.ts` 的 `changes` 类型改为 `Record<string, unknown> | unknown[] | null`，与后端序列化器对齐。

### [P2] A-03 消息中心收件箱静默截断为最近 50 条，历史消息在界面上永久不可达

- 位置：`frontend/src/views/messages/MessageCenterView.vue:253`
- 代码：
```ts
async function loadInbox() {
  loading.value = true;
  try {
    inboxItems.value = await messagesApi.inbox();
  } catch (e) {
    console.error("加载收件箱失败:", e);
  } finally {
    loading.value = false;
```
- 触发条件：收件箱累计消息 > 50 条。`api/index.ts:373` 的 `inbox()` 不带分页参数；`backend/apps/messages/views.py:149-163` 用 `parse_pagination` 默认 `pageSize=50`、按 `-created_at` 排序返回 `{items,total}`；`api/request.ts:321` 降维成 50 条裸数组，`total` 被丢弃；模板 38-77 行直接 `v-for="item in inboxItems"`，没有任何分页/「加载更多」，加载失败时还显示「暂无消息」（40 行）。对照：同一页「已发布」用的 `SentView`（`messages/views.py:166-179`）返回**裸数组**、上限 `_SENT_TAKE=500`，所以是收件箱单方面少一个数量级。
- 后果：收到第 51 条消息后，最早的消息在界面上永久消失且毫无提示；比赛期间管理员发布的规则/通知类消息被静默吞掉，选手无从得知。刷新按钮与「全部标为已读」都无法触达这些消息。
- 修复建议：给收件箱加分页（或至少取 `total` 并在超出时提示 + 提供翻页），与「已发布」保持一致的数量级；加载失败要区分「空」与「失败」两种态。

### [P2] A-04 创建/编辑账号未校验「至少选择一家公司」，可静默产出「无公司范围但持比赛级合同执行权」的账号

- 位置：`frontend/src/views/account-management/AccountManagementView.vue:236`
- 代码：
```ts
const formRules = {
  username: [{ required: true, message: "请输入用户名", trigger: "blur" }],
  password: [
    { required: true, message: "请输入密码", trigger: "blur" },
    { min: 8, max: 64, message: "密码长度需 8-64 位", trigger: "blur" },
    { pattern: /^(?=.*[a-zA-Z])(?=.*\d).+$/, message: "密码需同时包含字母和数字", trigger: "blur" },
  ],
};
```
- 触发条件：`managedCompanies`（第 246 行）没有对应的 `el-form-item` 校验规则（模板 153-163 行的公司多选框只有 `prop` 缺省的空 `el-form-item`），而 `handleSubmit`（451-495 行）无条件把 `derivePermissions(form.role, managedCompanies.value)` 的结果落库。因此：选择「管理员」后**不选任何公司**直接确定，或 `loadCompanies()` 失败（315-324 行 `catch { console.error }`，下拉为空）时直接确定，都会提交 `companyScopes/viewCompanyScopes/... = []`。此时派生的权限仍含 `contract:manage`/`contract:audit`/`contract:execute`/`stock:edit`（296-304 行），后端 `assert_grant_allowed` 对 COMPETITION_ADMIN 的 `grantCeiling` 恰好包含这些 key（`backend/apps/common/permissions.py:341-356`），**校验会通过**。
- 后果：产生一个"看起来是管理员、实际没有任何公司范围"的账号。而按 `stores/auth.ts:52-59` 的 `canAuditCompany`，持有 `contract:execute` 的账号"比赛级执行不受公司限制"——即该账号对本比赛**全部**公司的合同拥有执行权、并可用 `stock:edit` 操作资金账户，范围限制形同虚设。运营方以为"没选公司 = 无权限"，实际相反。
- 修复建议：给公司选择加必填校验（管理员/选手都必须 ≥1 家），或在角色为非超管且范围为空时禁止提交并给出明确提示；`loadCompanies` 失败要显式报错而不是静默留空。

### [P2] A-05 切换比赛时「比赛用账号」列表存在请求竞态，可显示另一个比赛的账号

- 位置：`frontend/src/views/account-management/AccountManagementView.vue:377`
- 代码：
```ts
  if (!competitionId.value) {
    competitionUsers.value = [];
    return;
  }
  try {
    const res = await usersApi.list({ competitionId: competitionId.value });
    competitionUsers.value = Array.isArray(res) ? res : res?.items ?? [];
  } catch (e) {
    console.error("加载比赛账号失败:", e);
  }
```
- 触发条件：`watch(competitionId, () => { loadCompetitionUsers(); })`（502-504 行）以及 `loadAll()`（389-396 行，`onMounted` 与 `useResourceChanged("users")` 都会触发）都能并发发起该请求。在慢网下先选比赛 A（请求 A 在途），再快速切到 B（请求 B 在途）：B 先返回并把表格刷成 B 的账号，随后 A 的响应到达并覆盖 → 表格显示 A 的账号，而 71 行的标签写着「所属比赛：{{ competitionName }}」（来自 store，已是 B）。代码里没有任何请求代次/序号校验。
- 后果：管理员在"以为正在管理 B 比赛"的前提下对 A 比赛的账号执行重置密码/删除/改权限（尤其后端允许超管跨比赛操作），造成误操作且难以复盘；表格与标题不一致时也没有任何提示。
- 修复建议：加载前记录 `const reqId = ++seq`（或 `const cid = competitionId.value`），响应回来先判断 `reqId === seq && cid === competitionId.value` 再赋值；切比赛时先清空 `competitionUsers`。

### [P2] A-06 「全部标为已读」失败时前端仍把列表置为已读（错误被 store 吞掉，视图的 catch 是死代码）

- 位置：`frontend/src/views/messages/MessageCenterView.vue:304`
- 代码：
```ts
async function markAllRead() {
  try {
    await messageStore.markAllRead();
    inboxItems.value.forEach((i) => (i.read = true));
  } catch (e) {
    console.error("全部已读失败:", e);
  }
}
```
- 触发条件：`stores/message.ts:101-108` 的 `markAllRead` 内部已经 `try { await messagesApi.markAllRead(); unreadCount.value = 0 } catch { /* 忽略 */ }`——**它永不 reject**。因此无论 `POST /messages/read-all` 因网络中断、500 还是 token 失效而失败，`inboxItems.value.forEach(i => i.read = true)` 都会执行；而 store 在失败分支不会把 `unreadCount` 归零。
- 后果：未读小圆点与「未读」标签全部消失（用户以为已读完），但侧边栏未读徽标仍然 > 0，两者自相矛盾；刷新页面后这些消息又变回未读——用户会认为"系统把我的已读状态弄丢了"。上面的 `catch`/`console.error` 永远不会执行，运维排查时也看不到失败痕迹。
- 修复建议：`markAllRead` 失败必须向上抛出（或在 store 里返回成功标志），视图仅在成功后再改本地状态；失败时提示用户并提供重试。

### [P2] A-07 登录页无重复提交防护：连按回车会并发登录并被自己顶号

- 位置：`frontend/src/views/login/LoginView.vue:7`
- 代码：
```vue
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="0"
        @keyup.enter="handleLogin"
      >
```
- 触发条件：`handleLogin`（95-115 行）开头只有 `if (!formRef.value) return;`，**没有 `if (loading.value) return;`**。`@keyup.enter` 挂在 `el-form` 根元素上（原生监听透传），回车键在输入框内冒泡即触发，而 `:loading="loading"` 只禁用按钮、拦不住回车。用户在密码框连按两次回车即发出两个 `POST /auth/login`。后端 `apps/auth/views.py:156-162` 每次登录都 `token_version += 1` 并向旧设备广播 `auth:required` 顶号。
- 后果：两个并发登录互相顶号。若响应乱序（请求 A 先被服务端处理拿到 v+1、请求 B 后处理拿到 v+2，但 A 的响应后到），`login()`（`stores/auth.ts:124-132`）最后一次赋值会把**已失效的 v+1 token** 写进内存与 localStorage，随后所有请求 401，用户被自己踢回登录页并看到「您的账号已在其他设备登录，请重新登录」（`api/request.ts:138-145`）——刚登录成功就被登出，且原因无法理解。此外每个请求都会额外触发一次成功登录审计与一次 `auth:required` 广播。
- 修复建议：`handleLogin` 首行加 `if (loading.value) return;`，并把提交按钮/回车统一走同一入口；或在表单上 `@keyup.enter` 改为仅当 `!loading` 时调用。

### [P2] A-08 「日志查看器」入口硬编码 `http://<host>:8120`，HTTPS/域名部署下按钮失效且令牌明文出网

- 位置：`frontend/src/views/settings/SettingsView.vue:267`（master 版本）
- 代码：
```ts
  try {
    const res = (await api.post("/auth/logviewer-token")) as { token?: string };
    const token = res?.token;
    if (!token) throw new Error("未获取到访问令牌");
    const base = `http://${window.location.hostname}:${versionStore.logViewerPort || 8120}/`;
    const sep = base.includes("?") ? "&" : "?";
    const url = `${base}${sep}token=${encodeURIComponent(token)}`;
    window.open(url, "_blank", "noopener,noreferrer");
```
- 触发条件：以域名 + HTTPS 部署（`backend/apps/auth/views.py:88-114` 为这种部署专门返回了 `log_viewer_url = "https://log.<host>/"`，并在 docstring 里说明这是"供前端跳转按钮动态拼地址"用的）。但前端从未读取该字段（全仓库 grep `log_viewer_url|logViewerUrl` 在 `frontend/src` 下 0 命中），`stores/version.ts:12,27-29` 也只保存了 `logViewerPort`，于是这里用 `window.location.hostname` + 硬编码 `http://` + 端口自行拼装。
- 后果：域名部署下按钮指向 `http://<域名>:8120/`——若日志查看器只经反向代理的 443/子域暴露，则端口不通、按钮点了没反应；若 8120 确实对公网开放，则 120 秒有效的网关令牌（`backend/apps/common/backend_gate.py:61` `BACKEND_GATE_TTL=120`）以**明文 HTTP** 在公网传输，可被同网段嗅探后直接用于通过 `/admin/` 网关。
- 修复建议：优先使用后端 `/api/version` 返回的 `log_viewer_url`（该字段已处理 IP/域名两种情况），仅在缺失时才回退到 `window.location.protocol + "//" + hostname + ":" + port`。

### [P2] A-09 登录令牌无条件持久化在 localStorage，且没有「记住我」开关

- 位置：`frontend/src/views/login/LoginView.vue:85`
- 代码：
```ts
const form = reactive({
  username: "",
  password: "",
});

const rules = {
  username: [{ required: true, message: "请输入用户名", trigger: "blur" }],
  password: [{ required: true, message: "请输入密码", trigger: "blur" }],
};
```
- 触发条件：登录表单没有"记住我/保持登录"选项，`handleLogin` 只有在成功后调用 `authStore.login`，而 `stores/auth.ts:129-131` 无条件执行 `setActiveUser(res.user.id); setAccountItem("token", res.token);` → `utils/accountStorage.ts:62-66` 写进 `localStorage["acct_<realm>_u<id>__token"]`。后端 JWT 默认有效期 24h（`backend/backend/settings.py:148` `JWT_EXPIRES_IN=24h`）。
- 后果：在比赛机房/共用电脑这类真实场景下，用户关闭浏览器再打开仍是登录态，只有显式点"退出登录"才会清除；任何一次 XSS（例如未来把公告正文改成用户可控、或引入第三方脚本）都能一行 `localStorage.getItem` 读走 24h 有效的 JWT 并异地重放，而令牌在服务端只能靠"再次登录递增 token_version"作废。用户也没有"只在此次会话有效"的选择权。
- 修复建议：提供"记住我"开关（不勾选时把 token 放 sessionStorage/内存）；更彻底的做法是改用 HttpOnly Cookie 承载会话，前端不再持有可被 JS 读取的令牌。

### [P2] A-10 用户名无长度/字符集/大小写校验：可创建仅大小写不同的重名账号，且登录大小写敏感只报「用户名或密码错误」

- 位置：`frontend/src/views/account-management/AccountManagementView.vue:123`
- 代码：
```vue
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" :disabled="isEdit" />
        </el-form-item>
        <el-form-item v-if="!isEdit" label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password />
          <div class="form-tip">密码至少 8 位，且含字母和数字</div>
```
- 触发条件：用户名的唯一校验规则只有 `required`（第 237 行），没有 `maxlength`、没有字符集/空白校验、没有大小写不敏感的查重。后端 `apps/users/serializers.py:73-80` 只做 `strip()` + 精确 `User.objects.filter(username=value).exists()`；数据库是 SQLite（`settings.py:307`），`username = models.CharField(max_length=128, unique=True)`（`apps/users/models.py:36`，无 `db_collation`）→ **唯一性与登录查询 `User.objects.filter(username=username).first()`（`apps/auth/views.py:135`）都是大小写敏感的 BINARY 比较**。
- 后果：(1) 可同时存在 `Player1` 与 `player1` 两个账号，管理列表里只显示用户名/显示名，肉眼几乎无法区分，重置密码/删除/改权限极易打错对象；(2) 发账号时写错大小写的选手登录只会得到统一的「用户名或密码错误」（后端刻意不区分，防枚举），无法自助发现是大小写问题，现场需要人工排查；(3) 超长（>128）或含 emoji/纯空格的用户名在提交前不拦截：纯空格经后端 `strip` 后报"用户名不能为空"，超长报 DRF 字段错误，而这里 `handleSubmit` 的 `catch { ElMessage.error("操作失败，请重试") }`（489-491 行）会再叠一层误导性提示——"重试"永远不会成功。
- 修复建议：前端加 `maxlength=128`、`pattern` 限制字符集并 `trim`；创建前用不区分大小写的方式查重（后端加 `username__iexact` 校验或把列改为 `COLLATE NOCASE`）；登录失败文案补充"请注意用户名大小写"。

### [P3] A-11 未选择比赛时公司列表同时显示「请先选择比赛」和永不消失的「正在加载公司数据…」

- 位置：`frontend/src/views/companies/CompanyListView.vue:85`
- 代码：
```vue
      </template>
    </MobileCards>
    <div v-else class="cl-loading">正在加载公司数据…</div>
```
- 触发条件：`compStore.competitionId` 为空（未选比赛）。此时 21-23 行的 `v-if="!compStore.competitionId"` 警告条已渲染；而 26 行 `el-table` 的 `v-if` 与 61 行 `MobileCards` 的 `v-else-if` 都要求 `compStore.competitionId` 为真，二者不成立时 85 行的 `v-else` 成立——注意这个 `v-else` 只与 `MobileCards` 的 `v-else-if` 成链，与 26 行的 `v-if` 无关。
- 后果：未选比赛的用户在同一屏看到两条矛盾信息：「请先在〈比赛管理〉中选择一个比赛」+「正在加载公司数据…」，后者永远不会结束（`loadCompanies` 在无比赛时直接把 `dataLoading` 置 false 并返回，不会再有状态变化），用户会一直等待或反复刷新。
- 修复建议：把 85 行改为 `v-else-if="compStore.competitionId"`，或在无比赛时隐藏该占位。

### [P3] A-12 取消二次确认会抛出未处理的 Promise 拒绝（公司删除/比赛删除、开关、财年操作）

- 位置：`frontend/src/views/competitions/CompetitionListView.vue:402`
- 代码：
```ts
async function handleDelete(row: any) {
  await ElMessageBox.confirm(`确定删除"${row.name}"？该比赛下的所有数据将被永久删除`, {
    type: "warning",
    confirmButtonText: "确认删除",
  });
  await ElMessageBox.confirm(`再次确认：删除"${row.name}"后数据不可恢复，继续吗？`, {
    type: "error",
    confirmButtonText: "最终确认",
  });
```
- 触发条件：三次 `ElMessageBox.confirm` 都在 `try` 之外（`try` 只包住 415-434 行的 `api.delete`），`ElMessageBox.confirm` 在用户点「取消」时是 **reject**("cancel")。同类写法还有：同文件 `toggleStatus`（392 行）、`startNextFiscalYear`（445 行）、`endFiscalYear`（461 行），以及 `frontend/src/views/companies/CompanyListView.vue:270-286`（`confirm` + `prompt` 均裸奔）。
- 后果：用户每次点「取消」都会在页面产生一个 unhandled promise rejection（控制台 `Uncaught (in promise) cancel`），污染错误上报/调试信息；一旦将来接入全局 `unhandledrejection` 处理或 Sentry 类上报，正常的取消操作会被当成异常；同时也说明这些确认流程没有统一的取消分支，后续改动容易误把取消当失败处理。功能本身未出错。
- 修复建议：统一改为 `try { await ElMessageBox.confirm(...) } catch { return; }`（`MessageCenterView.vue:313-322`、`AccountManagementView.vue:441-449` 已是正确写法，可作模板）。

### [P3] A-13 审计日志「操作人 ID / 比赛 ID」输入非数字时筛选被静默忽略，界面却像已筛选

- 位置：`frontend/src/views/system/AuditLogView.vue:216`
- 代码：
```ts
    const res: any = await auditApi.list({
      kind: filters.kind || undefined,
      model: filters.model || undefined,
      operatorId: filters.operatorId ? Number(filters.operatorId) : undefined,
      competitionId: filters.competitionId ? Number(filters.competitionId) : undefined,
      page: page.value,
      pageSize,
    });
```
- 触发条件：在两个 ID 输入框里输入非数字（如 `abc`、`1 2`、粘贴中文）。`Number("abc")` = `NaN`，`auditApi.list` 只清理 `null`/`""`（`api/index.ts:479`），`NaN` 会被序列化为字符串 `"NaN"` 发出；后端 `apps/audit/views.py:37-49` 对 `int(op)` / `int(cid)` 的 `ValueError` 是 `pass`——**静默跳过该过滤条件**。
- 后果：用户以为已按操作人/比赛筛选，实际拿到的是全量日志（含其他比赛、其他操作人），在大表上很容易据此得出错误结论（例如"某账号改过这批数据"）；界面上没有任何"筛选条件无效"的提示，`total` 变化也不会引起注意。
- 修复建议：前端用 `el-input-number` 或提交前校验 `/^\d+$/`，非法值给出明确提示并阻止查询；后端对非法参数返回 400 而不是静默忽略。

### [P3] A-14 系统账号 tab 漏订阅全局 users 事件，其他管理员对系统账号的增删改不会刷新本页

- 位置：`frontend/src/views/account-management/AccountManagementView.vue:508`
- 代码：
```ts
useResourceChanged("users", () => {
  loadAll();
});
```
- 触发条件：未传 `options.scope`，默认值为 `"competition"`（`frontend/src/realtime/useResourceChanged.ts:43`），而该分支要求 `eventCid != null && currentCid != null && eventCid === currentCid`（同文件 86 行）。系统账号的 `competitionId` 恒为 `null`，后端广播的 `resource:changed` 事件 `competitionId` 也是 `null` → **永远是 false**，系统账号的实时刷新从未生效。
- 后果：管理员 A 创建/删除/改权限一个系统账号后，管理员 B 打开着的「账号（系统）」页不会更新，仍显示旧账号列表——B 可能基于过期列表重置密码或重复创建同名账号（被后端拒绝后仍找不到原因）；而该页没有刷新按钮（模板 1-14 行），只能整页 F5。同一文件里 `MessageCenterView.vue:291` 正是因为同一原因显式写了 `{ scope: "any" }`，可作对照。
- 修复建议：改为 `useResourceChanged("users", () => loadAll(), { scope: "any" })`，或对系统账号单列一次 `scope: "global"` 订阅；同时补一个刷新按钮。

### [P3] A-15 发布消息的图片提示与后端限制不符：按页面提示选择图片必然失败

- 位置：`frontend/src/views/messages/MessageCenterView.vue:201`
- 代码：
```vue
                  multiple
                  hidden
                  :disabled="uploadingImg"
                  @change="onPickImages"
                />
              </label>
            </div>
            <span class="form-hint">可选，最多 9 张，单张 ≤ 15MB（PNG / JPEG / GIF / WebP / BMP）。</span>
```
- 触发条件：按提示操作。第 200 行是 `accept="image/*"`（不限具体格式），第 208 行文案承诺「≤ 15MB」「支持 BMP」，但后端 `apps/messages/views.py:48-63` 只按**魔数**放行 PNG/JPEG/GIF/WebP（**没有 BMP 分支**），`_MAX_IMAGE_BYTES = 10 * 1024 * 1024`（同文件 67 行，注释明确写 10MB）。因此：选一张 12MB 的 JPEG、或任意一张 BMP、或 iPhone 拍摄的 HEIC（`accept="image/*"` 允许选中）都会上传失败。
- 后果：用户严格按页面提示准备素材却收到"图片上传失败"，且失败原因（格式/体积）不明确；发布前需要反复试错，比赛现场容易耽误通知发布。前端也完全没有体积预检，大文件会先被完整 POST 出去才被拒。
- 修复建议：把 `accept` 收紧为 `image/png,image/jpeg,image/gif,image/webp`，文案改为「≤ 10MB」并去掉 BMP；`onPickImages` 里加 `f.size > 10*1024*1024` 的前置校验与提示。

### [P3] A-16 取消发布不清理已上传图片，且图片上传中可直接提交导致"发出缺图消息"

- 位置：`frontend/src/views/messages/MessageCenterView.vue:366`
- 代码：
```ts
  if (publishForm.images.length + files.length > 9) {
    ElMessage.warning("最多添加 9 张图片");
    files.splice(9 - publishForm.images.length);
  }
  uploadingImg.value = true;
  try {
    for (const f of files) {
      const meta = await messagesApi.uploadImage(f);
      publishForm.images.push(meta);
    }
```
- 触发条件：(1) 用户在对话框里选了图片（`uploadImage` 已把文件落盘并返回 `{url,filename}`，见 `api/index.ts:367-371`），随后点「取消」或按 ESC 关闭——`resetPublish`（406-415 行）只清空 `publishForm.images` 数组，**不会删除服务端已落盘的文件**；只有删除整条消息时后端才清理图片（`views.py:441` 附近）。(2) `uploadingImg` 只用于给「+」加 `disabled` 样式（194 行），发布按钮（214 行）只受 `submitting` 控制，因此图片还在上传时点「发布」，`submitPublish` 会把**尚未 push 进 `images` 的图片**排除在外。
- 后果：频繁起草/取消的管理员会在服务器 `MEDIA_ROOT` 累积大量无主图片，长期占用磁盘且没有清理入口；更严重的是发布者看到缩略图仍在转圈、以为图片已随消息发出，实际接收方永远看不到这些图（内容缺失且无人察觉原因）。
- 修复建议：对话框关闭时对已上传但未使用的图片调用删除接口（或改为"提交时才上传"）；`uploadingImg` 为真时禁用发布按钮并给出"图片上传中"提示。

### [P3] A-17 公司详情：字段加载失败被伪装成「该产业类型尚未定义字段」；非法 `:id` 让页面永久停在「加载中...」

- 位置：`frontend/src/views/companies/CompanyDetailView.vue:107`
- 代码：
```vue
      <el-alert
        v-else
        type="info"
        :closable="false"
        title="该产业类型尚未定义字段"
      />
```
- 触发条件：(1) `loadFieldValues`（220-244 行）的 `catch` 把 `fieldEditors` 置空且只留注释「错误提示由全局响应拦截器统一弹出」，于是 `fieldEditors.length === 0` 时模板落到 107 行的 `v-else`，把"请求失败（网络抖动 / 403 / 500）"渲染成"该产业类型尚未定义字段"。(2) `const companyId = Number(route.params.id)`（128 行）对 `/companies/abc` 得到 `NaN`，`api.get("/companies/NaN")` 失败 → `catch` 只弹一条 toast，`company` 始终为 `null`，标题 5 行的 `{{ company?.name || "加载中..." }}` 永久显示「加载中...」。
- 后果：超管看到"该产业类型尚未定义字段"后会去产业类型管理里重复配置字段（或误以为数据丢失），真实的加载失败被掩盖；非法 URL 进入时页面成为无信息的死页面，用户不知道是参数错误还是系统故障。
- 修复建议：为空态区分 `loaded` 与 `error` 两种状态（失败时显示"字段加载失败，请重试"并提供重试按钮）；对 `Number.isInteger(companyId) && companyId > 0` 做前置校验，非法时直接提示并跳回列表。

### [P3] A-18 `window.open(..., "noopener")` 返回 null，SettingsView 的令牌 URL 清洗是死代码

- 位置：`frontend/src/views/settings/SettingsView.vue:252`（master 版本）
- 代码：
```ts
    const sep = base.includes("?") ? "&" : "?";
    const url = `${base}${sep}token=${encodeURIComponent(token)}`;
    const win = window.open(url, "_blank", "noopener,noreferrer");
    if (win) {
      win.addEventListener("load", () => {
        try { win.history.replaceState(null, "", base); } catch { /* */ }
      });
    }
```
- 触发条件：点击「后端管理界面」。按规范，当 `windowFeatures` 含 `noopener`（`noreferrer` 亦隐含）时 `window.open` **返回 `null`** —— MDN：「If this feature is set, the new window will not have access to the originating window via `Window.opener` and returns `null`」（https://developer.mozilla.org/en-US/docs/Web/API/Window/open ）。因此 `if (win)` 恒为 false，`replaceState` 清理地址栏的意图从未执行；`openLogViewer`（266-278 行）则完全没有清理逻辑。
- 后果：签发出来的后台网关令牌（TTL 120s，`backend/apps/common/backend_gate.py:61`）会以 `?token=...` 形式出现在地址栏并进入浏览器历史/会话恢复记录中。影响有上限：后端网关在校验通过后会自行 302 到不含查询串的干净地址（同文件 110-115 行），令牌只在极短窗口内可见，但截屏、肩窥、历史记录命中仍可拿到这 120s 的入口凭证。也就是说：**这段"防泄漏"代码从写下的那天起就没有生效**，谁把 `noopener` 当成无副作用的安全加固，就会误判这里已有保护。
- 修复建议：不要依赖 `window.open` 的返回值做清理——要么去掉 `noopener` 但改为 `const win = window.open("", "_blank")` 后 `win.location = url` 并立即 `win.history.replaceState`（跨源时会受限），要么信任后端的 302 兜底并删掉这段死代码；`openLogViewer` 同理应对齐。

### [P3] A-19 更新公告的「日期」是自由文本且前后端都无格式校验，可写入任意字符串并展示给全员

- 位置：`frontend/src/views/settings/SettingsView.vue:188`（master 版本）
- 代码：
```ts
  if (!annForm.value.title.trim()) return ElMessage.warning("请填写标题");
  if (!annForm.value.content.trim()) return ElMessage.warning("请填写内容");
  annSaving.value = true;
  try {
    const payload = {
      version: annForm.value.version.trim(),
      title: annForm.value.title.trim(),
      date: annForm.value.date.trim() || new Date().toISOString().slice(0, 10),
```
- 触发条件：模板 65-67 行是普通 `el-input`（占位符「留空自动取今天」），没有 `el-date-picker` 也没有 `pattern`；`handleSave` 只校验 version/title/content 非空，`date` 仅 `trim()` 后直接提交。后端同样不校验：`backend/apps/announcements/views.py:48-65` 只做 `strip()`，模型 `date = models.CharField(max_length=16)`（`announcements/models.py:11`）而 SQLite 不强制 VARCHAR 长度。于是可以保存 `2024-13-45`、`待定`、`明天`，甚至一段数百字符的文本；`version`（max_length=32）、`title`（max_length=255）同理无长度拦截。
- 后果：全员可见的「更新记录」弹窗中会出现非法/超长日期与版本号，横向撑破公告历史表格（`components/AnnouncementHistoryDialog.vue` 的日期/版本列），并污染随包发布的版本提示语义；由于内容 `content` 是受信 HTML（`utils/sanitizeHtml.ts` 已做净化，此处不构成 XSS），但日期/版本会作为纯文本原样展示，错误数据无法在前端被识别。
- 修复建议：改用 `el-date-picker`（`value-format="YYYY-MM-DD"`）并加 `maxlength`/正则校验；后端 serializer 侧对 `date` 做 `YYYY-MM-DD` 校验、对 `version/title` 限长。

---

## 存疑/待确认

1. **`CompanyDetailView` 未监听 `route.params.id` 变化** [待确认]：`companyId` 在 setup 期一次性取值（`CompanyDetailView.vue:128`），若存在"从 A 公司详情直接跳到 B 公司详情"的入口（`/companies/:id` 同名路由复用组件、不重新 mount），页面会继续显示 A 的数据。本仓库其余视图不在本次审计范围内，未能确认是否存在这样的入口（列表页的「查看」是先从列表进入，会重新 mount），故未列为缺陷。
2. **审计日志时间列的时区语义** [待确认]：后端 `USE_TZ=True` + `TIME_ZONE="Asia/Shanghai"`（`backend/backend/settings.py:392-395`），`AuditLogSerializer` 直接返回 aware datetime，经 DRF 编码为 UTC `Z` 串；前端 `fmtTime`（`AuditLogView.vue:181-183`）用 `toLocaleString("zh-CN")` **按浏览器本地时区**渲染，页面上没有任何"时区"标注。若存在跨时区使用的账号，"时间"列会与比赛组织方（Asia/Shanghai）的记录口径不一致。是否算缺陷取决于部署是否限定单一时区，未下结论；另 `fmtTime` 对无法解析的字符串会渲染 `Invalid Date`（当前后端格式固定，未触发）。
3. **`permSummary` 的计数口径** [待确认]：`AccountManagementView.vue:358-363` 用 `viewCompanyScopes` 的长度显示「管理员·N 公司」，而"管理范围"语义上对应 `companyScopes`；同时 `handleEdit`（422 行）用 `viewCompanyScopes` 还原选择、`handleSubmit` 再用它覆盖四个范围。对于界面创建的数据四者恒等，无影响；若存在历史/脚本写入的 `companyScopes` 与 `viewCompanyScopes` 不一致的账号，仅打开编辑框点确定就会把审核范围（`companyScopes`）重置掉。需要一份线上数据分布才能判断影响面。
4. **非超管是否实际持有 `account:manage`** [待确认]：`backend/apps/common/permissions.py:319-323` 把 `account:manage` 列为 `SUPER_ADMIN_ONLY_PERMISSIONS`，且 `assert_grant_allowed` 要求 actor 必须是超管（375-376 行），故本报告按"账号管理页实际只有超管能进"评估 A-04/A-05/A-10 的影响面；若存在直接改库产生的持有 `account:manage` 的非超管账号，A-04/A-05 的严重度需上调。
5. **`fetchFullSync` 的 `LARGE_PAGE_SIZE=10000` 与后端 `MAX_PAGE_SIZE=200` 冲突** [待确认]：`api/request.ts:196,354` 请求 10000 条，服务端 `apps/common/pagination.py:34-35` 硬截断为 200，于是每次全量同步都按 200/页循环多轮（`request.ts:372-380`）。仅影响同步耗时/请求数，未观察到数据错误，未列为缺陷。
6. **未覆盖项说明**：`MobileCards`、`AnnouncementHistoryDialog`、`AppLayout` 等组件不属于本次范围；与之相关的 `v-html` 已在 `utils/sanitizeHtml.ts` 做 DOMParser 净化（移除 script/style/on* 事件/危险协议），未发现可绕过点，故未就公告 HTML 出具 XSS 结论。
