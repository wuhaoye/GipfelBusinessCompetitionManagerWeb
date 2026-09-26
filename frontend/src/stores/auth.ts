import { defineStore } from "pinia";
import { ref, computed } from "vue";
import api, { authApi } from "@/api";
import { resetRequestMemo, setSessionRefreshing } from "@/api/request";
import { getAccountItem, setAccountItem, removeAccountItem, setActiveUser } from "@/utils/accountStorage";
import { logger } from "@/utils/logger";
import { disconnectRealtime } from "@/realtime/socket";
import { hasPermission } from "@/permissions/catalog";
import { resetGateState } from "@/system/gate";

export interface UserInfo {
  id: number;
  username: string;
  role: string;
  displayName?: string;
  /** 是否需要在首次登录后修改初始密码（后端强制改密机制） */
  mustChangePassword?: boolean;
  /** 账号是否启用 */
  isActive?: boolean;
  permissions?: string[];
  /** 公司审核范围：可作为管理员/审核员审核其合同的公司 id 列表 */
  companyScopes?: number[];
  /** 公司查看范围：仅持 company:view 的账号，仅在范围内公司可见全量 / 可列示 */
  viewCompanyScopes?: number[];
  /** 股票系统管理范围（stock:edit 低级管理专属）：仅可在范围内公司的资金账户 + 自己的账户操作 */
  stockCompanyScopes?: number[];
  /** 归属比赛 id：归属比赛的账号（PLAYER/COMPETITION_ADMIN 等）非空，登录后自动锁定该比赛；
   *  超管 / 未分配账号为空（null），保持手动选择。 */
  competitionId?: number | null;
}

export const useAuthStore = defineStore("auth", () => {
  const token = ref<string>(getAccountItem("token") || "");
  const user = ref<UserInfo | null>(null);

  const isLoggedIn = computed(() => !!token.value);
  const isSuperAdmin = computed(() => user.value?.role === "SUPER_ADMIN");

  /** 权限判断：直接使用前端镜像目录 @/permissions/catalog 的 hasPermission，
   *  与后端 hasPermission 同构（同域等级蕴含 + fail-closed），无需网络请求。
   *  原实现登录后从 /permissions/catalog 拉目录，但该端点后端从未实现，
   *  404 被静默吞掉后长期走旧版回退逻辑——view/edit 之外的蕴含全部失效。 */
  function can(perm: string): boolean {
    return hasPermission(user.value?.role, user.value?.permissions ?? [], perm);
  }

  /** 拥有给定权限中的任意一个即返回 true（含同域蕴含） */
  function canAny(perms: string[]): boolean {
    return perms.some((p) => can(p));
  }

  /** 是否为某公司的审核员/管理员（用于合同审核范围判断） */
  function canAuditCompany(companyId: number): boolean {
    if (user.value?.role === "SUPER_ADMIN") return true;
    const owned = user.value?.permissions ?? [];
    if (owned.includes("contract:execute")) return true; // 比赛级执行不受公司限制
    if (!owned.includes("contract:audit")) return false;
    const scopes = user.value?.companyScopes ?? [];
    return scopes.includes(companyId);
  }

  /** 后端强制改密：标记当前账号是否仍需修改初始密码 */
  const needsPasswordChange = computed(() => !!user.value?.mustChangePassword);

  /** 自助改密：用于首次登录强制改密流程。
   *  后端改密成功后会递增 token_version，吊销包括当前会话在内的所有旧 token（安全设计）。
   *  改密接口本身在同一请求内基于新 token_version 签发新 token 并随响应一起返回，
   *  前端直接拿这个新 token 替换内存 / localStorage 的旧值，心跳与后续请求立即续接，
   *  彻底规避「改密 200 → 再 login」的 SQLite 写后读竞态（曾复现：login 路径 10ms 即
   *  返回 401「未跑 bcrypt」，本质是同连接上 User 实例偶发读到旧 / 空 password_hash）。
   *  同时在改密→续接窗口期：停掉心跳、屏蔽 401 拦截器的踢出逻辑——
   *  旧会话后台请求（心跳/缓存同步）的迟到 401 不得清掉刚换发的新 token。 */
  async function changePassword(oldPassword: string, newPassword: string, username?: string) {
    stopHeartbeat();
    setSessionRefreshing(true);
    let changed = false;
    // 登录态可能已被清空（历史缺陷：门禁 401 被当成会话过期 → token/user 一起被 logout 清掉）。
    // 此时直接用「用户名 + 用户刚输入的旧密码」恢复登录态，否则改密请求不带 Authorization，
    // 后端只会返回「登录已过期，请重新登录」。
    const loginName = user.value?.username || username || "";
    if (!token.value && loginName) {
      await login(loginName, oldPassword);
    }
    try {
      try {
        const res: any = await authApi.changePassword({ oldPassword, newPassword });
        // 改密成功且后端返回了新 token：直接续接，不再二次 login
        if (res?.token) {
          token.value = res.token;
          setActiveUser(res.user?.id ?? user.value?.id);
          setAccountItem("token", res.token);
          if (res.user) user.value = res.user;
          else if (user.value) user.value.mustChangePassword = false;
          startHeartbeat();
          notifyLoggedIn();
          changed = true;
        } else {
          // 兼容旧版本后端：未返回 token 时回到 login 重登路径
          changed = true;
        }
      } catch (e: any) {
        // 会话在弹窗期间被顶掉（后端每次登录都递增 token_version：别处再登录一次，
        // 本页 token 即失效 → 改密请求 401「账号已在其他设备登录」）。
        // 用用户刚输入的旧密码静默重登换新 token（单设备设计 = 本机接管会话），再重试一次。
        // 注意：旧密码输错走 400「旧密码不正确」，不会进入此分支；重登失败则原样抛出。
        const status = e?.response?.status;
        const retryName = user.value?.username || loginName;
        if (status === 401 && retryName) {
          await login(retryName, oldPassword);
          // 重登后再次改密——重登时后端会递增一次 token_version，
          // 此处不再依赖改密响应里的 token（按登录路径续接），保持向后兼容。
          await authApi.changePassword({ oldPassword, newPassword });
          changed = true;
        } else {
          throw e;
        }
      }
      if (changed && user.value?.username && !token.value) {
        // 兜底：旧版本后端没回 token，登录态未续上，需要再走一次 login
        try {
          await login(user.value.username, newPassword);
        } catch (re) {
          logger.warn("改密成功但自动重登失败，转人工重登:", re);
          logout();
          throw new Error("密码修改成功，请使用新密码重新登录");
        }
      }
    } finally {
      setSessionRefreshing(false);
    }
    if (user.value) user.value.mustChangePassword = false;
  }

  async function login(username: string, password: string) {
    const res = await authApi.login({ username, password });
    token.value = res.token;
    user.value = res.user;
    // 账号隔离：先建立激活账号指针，再写入该账号命名空间下的 token，使后续请求 / 缓存都归属该账号。
    setActiveUser(res.user.id);
    setAccountItem("token", res.token);
    // 换账号 / 重新登录：丢弃上一个会话的条件请求凭据（ETag 与账号相关），下次心跳重新获取。
    heartbeatEtag = null;
    startHeartbeat();
    notifyLoggedIn();
  }

  async function fetchProfile() {
    if (!token.value) return;
    if (user.value) {
      // 已加载（如刷新后重入应用）：只需确保心跳在运行
      startHeartbeat();
      return;
    }
    try {
      user.value = await authApi.getProfile();
      startHeartbeat();
    } catch (e) {
      logger.error("Failed to fetch profile:", e);
      logout();
    }
  }

  // ---------- 会话心跳（单设备登录顶号）----------
  // 绝大多数 GET 走本地缓存、不发网络，旧设备停在界面浏览时不会触发任何被守卫的请求，
  // 也就不会被后端 tokenVersion 校验踢掉。心跳周期性向 /auth/me 真实打网络，
  // 一旦被新设备登录顶号（tokenVersion 不一致 → 后端 401），响应拦截器会清空登录态并跳转登录页。
  //
  // C3 改造（运维约束整改设计说明 §4.3 / 简报 C3.3）：
  //   ① 自适应间隔：页面可见 20s、document.hidden 时 60s —— 直接削掉稳态 5 req/s 的大头；
  //   ② 条件请求：?light=1 + If-None-Match，服务端未变更时回 304（空体）；
  //      304 视为成功、不更新任何状态；200 时保存新的 ETag 供下次条件请求（§4.1）；
  //   ③ 重新可见时补一次对账心跳 —— 仅当本次隐藏时长 ≥ 可见间隔（20s）才补：
  //      隐藏 <20s 时本就没有漏掉任何一次心跳（隐藏期间间隔 60s，刚隐藏就切回远未到点），
  //      补打只会把「主持人频繁切窗口」变成额外 /auth/me 请求，与 C3 削稳态请求数的目标相反。
  // 不变语义：mustChangePassword 期间不启动；logout/stopHeartbeat 行为不变；
  // 401 仍由全局响应拦截器处理（清登录态 + auth:kicked），本地绝不吞掉。
  const HEARTBEAT_INTERVAL_VISIBLE_MS = 20 * 1000;
  const HEARTBEAT_INTERVAL_HIDDEN_MS = 60 * 1000;
  let heartbeatTimer: number | null = null;
  /** 上一次 /auth/me 响应头里的 ETag（条件请求凭据）；登录 / 登出时清空。
   *  注意：后端 light 与完整两种表示的 ETag **不同**（api-c3 契约：W/"l-…" / W/"f-…"），
   *  这里存的始终是 `?light=1` 那一次的 ETag，因此**只能**回带给带 light=1 的心跳请求；
   *  完整资料的请求（fetchProfile / refreshProfile）一律不带 If-None-Match。 */
  let heartbeatEtag: string | null = null;
  /** 单次心跳在途标记：慢网络下不叠加并发心跳。 */
  let heartbeatInFlight = false;
  /** 本次「进入隐藏」的时刻；切回前台时据此判断隐藏时长（见 handleVisibilityChange）。
   *  null = 当前不在隐藏态（或隐藏起点未知）。 */
  let heartbeatHiddenAt: number | null = null;

  function isDocumentHidden(): boolean {
    return typeof document !== "undefined" && document.hidden === true;
  }

  /** 当前该用的心跳间隔：可见 20s / 隐藏 60s（自适应）。 */
  function heartbeatIntervalMs(): number {
    return isDocumentHidden() ? HEARTBEAT_INTERVAL_HIDDEN_MS : HEARTBEAT_INTERVAL_VISIBLE_MS;
  }

  /** 单次对账心跳：条件请求；304 = 成功且无副作用。 */
  async function beatHeartbeat(): Promise<void> {
    if (heartbeatInFlight) return;
    if (!token.value) {
      stopHeartbeat();
      return;
    }
    heartbeatInFlight = true;
    try {
      const headers: Record<string, string> = {};
      if (heartbeatEtag) headers["If-None-Match"] = heartbeatEtag;
      // cache:false 绕过本地缓存层真实打网络；silent:true 瞬时网络抖动不打扰用户；
      // conditional:true 只对本调用放开 304（validateStatus）并保留 status/headers 以便读 ETag。
      // light=1 只取 {id,tokenVersion,isActive,mustChangePassword}，响应体最小（设计说明 §4.1）。
      const res: any = await api.get("/auth/me", {
        cache: false,
        silent: true,
        conditional: true,
        params: { light: 1 },
        headers,
      });
      // 304：会话与资料均未变更（成功，无事可做，ETag 沿用服务端回传的同值）。
      // 200：保存新 ETag，供下一次条件请求使用。
      const etag = res?.headers?.etag ?? res?.headers?.ETag;
      if (typeof etag === "string" && etag) heartbeatEtag = etag;
    } catch {
      // 401（被顶号 / 会话失效）已由全局响应拦截器处理：清空登录态 + 派发 auth:kicked + 跳登录页。
      // 这里**绝不**本地吞掉 401 或自行清登录态；其余错误静默忽略，不中断心跳。
    } finally {
      heartbeatInFlight = false;
    }
  }

  function stopHeartbeat() {
    if (heartbeatTimer != null) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  function startHeartbeat() {
    stopHeartbeat(); // 避免重复启动
    if (!token.value) return;
    // 强制改密期间不启动心跳：门禁下 /auth/me 曾被拒（401），心跳的 401 会被全局拦截器
    // 误判成「会话过期」而清掉 token，用户提交改密即报「登录已过期」（真机事故）。
    // 后端现已把 /auth/me 列入豁免，这里再保一层：改密成功后再由 changePassword 启动心跳。
    if (user.value?.mustChangePassword) return;
    // 隐藏起点未知但当前是隐藏态（如后台标签页里登录）→ 以此刻为起点，切回前台时同样能判断隐藏时长。
    if (heartbeatHiddenAt == null && isDocumentHidden()) heartbeatHiddenAt = Date.now();
    heartbeatTimer = window.setInterval(() => { void beatHeartbeat(); }, heartbeatIntervalMs());
  }

  /** 可见性变化：按新的可见性重设定时器；重新可见且**本次隐藏 ≥ 可见间隔**时补一次对账心跳。
   *  心跳未在运行（未登录 / 强制改密中 / 已 stop）时不因可见性事件启动，保持既有语义。 */
  function handleVisibilityChange() {
    if (heartbeatTimer == null) return;
    if (isDocumentHidden()) {
      heartbeatHiddenAt = Date.now(); // 记录隐藏起点（切回前台时据此判断是否补打）
      startHeartbeat(); // 切到隐藏：60s
      return;
    }
    const hiddenMs = heartbeatHiddenAt == null ? null : Date.now() - heartbeatHiddenAt;
    heartbeatHiddenAt = null;
    startHeartbeat(); // 恢复可见：20s
    // 门槛：隐藏 <20s 时隐藏期间本就没有到点的心跳（隐藏间隔才 60s），补打纯属多发请求。
    if (hiddenMs != null && hiddenMs >= HEARTBEAT_INTERVAL_VISIBLE_MS) void beatHeartbeat();
  }

  // 注册可见性监听：真实浏览器把 visibilitychange 派发在 document（事件冒泡）；
  // Node 浏览器桩的 document 无 addEventListener，故回退到 window，便于回归用例驱动同一套代码。
  const visibilityTarget: EventTarget | null =
    typeof document !== "undefined" && typeof document.addEventListener === "function"
      ? document
      : typeof window !== "undefined"
        ? window
        : null;
  if (visibilityTarget) {
    visibilityTarget.removeEventListener("visibilitychange", handleVisibilityChange);
    visibilityTarget.addEventListener("visibilitychange", handleVisibilityChange);
  }

  /** 建立有效登录态后广播：启动阶段因无 token 而失败的一次性加载（自定义控件包，审计 M-01）
   *  据此重试。与「auth:kicked」成对：一个表示登录态建立、一个表示登录态失效。 */
  function notifyLoggedIn() {
    window.dispatchEvent(new CustomEvent("auth:login"));
  }

  function logout() {
    stopHeartbeat();
    // 条件请求凭据随会话一起失效（登出 / 被顶号后 ETag 不再代表当前账号的资料版本）。
    heartbeatEtag = null;
    // 隐藏起点同样作废：下次登录重新计时，避免用上一会话的隐藏时长触发补打。
    heartbeatHiddenAt = null;
    token.value = "";
    user.value = null;
    // 清空请求层内存 memo（登出 / 换账号 / 被顶号都走这里）：memo 是模块级共享状态且键不含
    // 账号，不清空则新账号在 15s 窗口内会命中上一账号的响应（如按 companyScopes 裁剪过的
    // 公司列表）且不发请求——审计 F-06。resetRequestMemo() 同时递增会话 epoch，
    // 使登出前已发出的在途请求返回后无法把旧账号数据写回（见 api/responseMemo.ts）。
    resetRequestMemo();
    // 断开实时 WebSocket 通道：被顶号 / 登录过期后旧 socket 若不断开，会以失效 token 无限重连，
    // 产生大量 401 噪声且实时事件在登录态恢复前可能错乱（见 request.ts 拦截器 401 处理）。
    disconnectRealtime();
    // 仅移除账号命名空间下的 token（保留该账号其余已持久化数据，下次登录可恢复）；
    // activeUserId 指针保留，由 token 是否存在决定登录态（见 competition.loadFromStorage 守卫）。
    removeAccountItem("token");
    // 清空全局门禁（强制暂停/回退）遮罩态：否则登出后停在登录页仍会被上一个账号的
    // 「系统已暂停」遮罩盖住，且写请求会被误拦。
    resetGateState();
    // 清除当前选中的比赛：登录态切换（登出 / 被顶号）后不应残留上一个账号/上一次会话选中的比赛，
    // 否则 competition.loadFromStorage 会以残留的比赛 id 拉取财年，触发归属校验（越权）返回空，
    // 表现为「登录后左上角财年显示错误 / 未开启财年」。下次登录由 applyOwnCompetition 按归属比赛重新锁定。
    removeAccountItem("currentCompetition");
  }

  // 监听「被顶号 / 登录过期」事件（请求拦截器在收到 401 时派发），
  // 同步清空内存登录态，避免「localStorage 已清但内存 token 仍在、路由守卫把登录页弹回首页」的回弹。
  window.removeEventListener("auth:kicked", logout);
  window.addEventListener("auth:kicked", logout);

  // 监听权限变更事件（实时推送）
  // 当管理员修改某账号的权限/角色/范围时，后端会定向推送 permissions:changed 事件
  // 前端收到后拉取最新的用户信息，更新 can()/菜单/按钮
  async function refreshProfile() {
    if (!token.value) return;
    try {
      user.value = await authApi.getProfile();
    } catch (e) {
      logger.error("Failed to refresh profile:", e);
      // 静默失败，保持旧状态
    }
  }

  window.removeEventListener("permissions-changed", handlePermissionsChanged);
  window.addEventListener("permissions-changed", handlePermissionsChanged);

  function handlePermissionsChanged(event: Event) {
    const detail = (event as CustomEvent).detail;
    if (!detail || !user.value) return;
    // 只处理当前用户的权限变更
    if (detail.userId === user.value.id) {
      void refreshProfile();
    }
  }

  return {
    token,
    user,
    isLoggedIn,
    isSuperAdmin,
    needsPasswordChange,
    can,
    canAny,
    canAuditCompany,
    changePassword,
    login,
    fetchProfile,
    refreshProfile,
    logout,
    startHeartbeat,
    stopHeartbeat,
  };
});
