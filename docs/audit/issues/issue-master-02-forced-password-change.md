> 以下整段可直接粘贴为 GitHub Issue（标题为第一行 `title:` 后的内容）。测试分支：`master`（提交 `34013bb`）。

---

**title:** `[auth] 强制改密期间 20s 心跳被门禁 401 打成「会话过期」，导致新部署超管无法修改初始密码`

## 环境

| 项 | 值 |
| --- | --- |
| 分支 / commit | `master` / `34013bb` |
| OS | Debian GNU/Linux 13 (trixie)（真机 VMware，端到端）＋ Windows/Chrome 前端 |
| 复现命令 | 全新部署后，浏览器打开站点，用 `.env` 的 `SEED_ADMIN_PASSWORD` 登录（`admin`） |

## 问题描述

首次部署后超管用种子口令**登录成功**，界面弹出「请修改初始密码」，但提交时提示 **「登录已过期」**，初始密码永远改不掉 —— 等价于**新部署无法登录**（除非在 20 秒内手速够快）。

## 复现步骤（真机实测）

1. 全新部署 → `GET /opt/gipfel/backend/.env` 的 `SEED_ADMIN_PASSWORD`；
2. 浏览器用 `admin` + 该口令登录 → **HTTP 200**，响应 `data.user.mustChangePassword = true`；
3. 界面弹出「请修改初始密码」对话框；
4. **等待约 20 秒**（不要立刻提交），填写原密码/新密码/确认并提交；
5. 提示「登录已过期，请重新登录」，改密失败；重复登录仍复现。

在 `master` 代码上用 Django 内存测试库直接验证门禁行为（不触碰开发库）：

```
[1] POST /api/auth/login            -> HTTP 200  mustChangePassword=True
[2] GET  /api/auth/me               -> HTTP 401  {"code":401,"message":"账号需先修改初始密码","data":null}
[2] GET  /api/competitions          -> HTTP 401  {"code":401,"message":"账号需先修改初始密码","data":null}
```

注意响应体里**只有 HTTP 状态码，没有机器可读的错误码**（无 `errorCode` 字段），前端无法区分"必须先改密"和"会话过期"。

## 根因（四个缺陷叠加）

### ① 后端门禁只放行改密接口，`/api/auth/me` 也被拒

```python
# backend/apps/auth/authentication.py:22-23
# 强制改密放行路径（仅改密接口允许在 must_change_password=true 时通过）
_CHANGE_PASSWORD_PATHS = ("/api/auth/change-password",)

# :117-121
if getattr(user, "must_change_password", False) and not _is_change_password_endpoint(request):
    raise exceptions.AuthenticationFailed("账号需先修改初始密码", code="must_change_password")
```

### ② 前端登录成功后立即启动 20s 心跳，心跳打 `/api/auth/me`

```ts
// frontend/src/stores/auth.ts:165-167（master）
function startHeartbeat() {
  stopHeartbeat();
  if (!token.value) return;          // ← 只判 token，没有 mustChangePassword 守卫
  heartbeatTimer = window.setInterval(async () => {
      await api.get("/auth/me", { cache: false, silent: true });
  }, 20 * 1000);                     // HEARTBEAT_INTERVAL_MS = 20 * 1000
}
```

### ③ 全局 401 拦截器把门禁 401 当成「会话过期」：清 token / 跳登录 / 派发 auth:kicked

```ts
// frontend/src/api/request.ts:129-148（master）
if (error.response?.status === 401) {
  ...
  removeAccountItem("token");                       // :134 清 token
  window.location.hash = "#/login";                 // 跳登录页
  const backendMsg = error.response?.data?.message;
  const msg = backendMsg && backendMsg !== "Unauthorized" ? backendMsg : "登录已过期，请重新登录";
  ElMessage.error(msg);
  window.dispatchEvent(new CustomEvent("auth:kicked"));   // :148 → authStore.logout() 清空内存 user
}
```

### ④ 用户在仍开着的改密弹窗里提交时，请求已不带 Authorization

`frontend/src/views/login/LoginView.vue:147` → `authStore.changePassword(oldPassword, newPassword)`；store 里"401 自动重登"的兜底依赖 `user.value?.username`，而它已被 `logout()` 清空 → 不进兜底 → 后端返回 **401「登录已过期，请重新登录」**（实测文案与用户所见逐字一致）。

**故障链**：登录成功 → 心跳 20s 后打 `/api/auth/me` → 401（门禁）→ 拦截器判定"会话过期"→ 清 token/跳登录/清空内存登录态 → 改密请求变匿名 → 后端 401「登录已过期」→ **必然失败**（20 秒内提交才能侥幸成功，表现为"时好时坏"）。

## 期望结果

- 强制改密期间 `/api/auth/me`（只读自身资料）应放行，心跳与改密弹窗都能正常取资料；其它业务接口继续被门禁拦截；
- 前端应能区分"必须先改密"与"会话过期"（需机器可读的错误码，而不是只能匹配中文文案）；
- 强制改密期间不应启动会话心跳；改密请求在登录态意外丢失时应能用「用户名 + 刚输入的旧密码」恢复会话。

## 建议修复（供参考，未提交）

1. **后端**：`_CHANGE_PASSWORD_PATHS` 增加 `/api/auth/me`（只读自身资料，不放大业务权限）；
2. **后端**：401 响应体增加**增量**字段 `errorCode`，保留 DRF 异常的机器码（`must_change_password` / `token_version_mismatch` / `expired`）——当前 envelope 只有 `{code(HTTP 状态码), message, data}`，前端只能按中文文案猜语义；
3. **前端**：`request.ts` 的 401 分支先判 `errorCode === "must_change_password"` → 只提示，**绝不清 token / 跳登录 / 派发 auth:kicked**；
4. **前端**：`startHeartbeat()` 在 `user.value?.mustChangePassword` 期间直接返回；`changePassword()` 增加可选 `username` 参数，并在 `!token.value` 时先 `login(username, oldPassword)` 恢复会话；401 重试兜底不再依赖可能已被清空的 `user`；
5. **前端**：`LoginView.vue` 提交改密时把用户名传给 store，并预填刚输入的初始密码。

## 影响

`must_change_password` 是默认超管的首次登录门禁（`backend/apps/auth/bootstrap.py` 建种子账号时置 `True`），因此**每一次全新部署都会被卡住**：管理员拿不到可用会话，也就无法进入系统或修改密码。
