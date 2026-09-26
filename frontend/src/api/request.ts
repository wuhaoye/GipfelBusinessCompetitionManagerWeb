import axios, { AxiosInstance, AxiosRequestConfig } from "axios";
import { ElMessage } from "element-plus";
import { getApiBaseUrl, versionBlocked } from "@/config";
import { getAccountItem, removeAccountItem } from "@/utils/accountStorage";
import { applyGateState, requestBlockReason, type GateState } from "@/system/gate";
// 本地全量副本 → 响应形态（纯函数，独立成模块以便单测）：
// 未显式传 pageSize 时返回本地全量，不再按 50 条静默截断（审计 A-01/V-04/W-05）。
import { applyLocalPaging as reconstruct } from "./localPaging";
// 响应体归类（纯函数）：blob 下载与非信封 2xx 不再被判为失败（审计 F-01）。
import { interpretResponse } from "./envelope";
// 请求层内存 memo（登出/换账号清空 + epoch 防旧响应回填，见模块注释；审计 F-06）。
import {
  bumpResourceEvent,
  getMemoEntry,
  isMemoFresh,
  memoEpoch,
  resetResponseMemo,
  writeMemo,
} from "./responseMemo";

// 实时事件到达时标记资源「最近有变更」的实现来自 ./responseMemo，这里保持对外导出不变。
export { bumpResourceEvent };

// axios 自定义请求配置字段类型增强（request.ts 与 stores/version.ts 均使用这些字段）。
declare module "axios" {
  export interface AxiosRequestConfig {
    /** 绕过版本硬封锁（仅版本校验请求使用，否则请求会被拦截器拒绝、不发网络）。 */
    bypassVersionBlock?: boolean;
    /** 显式 false 时绕过本地缓存层、直接走网络；缺省走缓存。 */
    cache?: boolean;
    /** 为 true 时请求失败不弹错误提示（后台静默同步 / 校验请求使用）。 */
    silent?: boolean;
    /** 为 false 时跳过列表响应降维：返回原始 {items,total,...} 分页对象而非裸数组
     *  （供需要 total/分页字段的调用方使用，如审计日志页）。缺省降维为裸数组。 */
    normalize?: boolean;
    /** 绕过全局门禁（强制暂停 / 回退中）的客户端侧拦截。
     *  仅门禁状态查询与「恢复运行」等管理端点需要，避免暂停期间连状态都读不到。 */
    bypassGate?: boolean;
    /** 条件请求（C3 自适应心跳等）：**仅对显式声明该字段的调用**放开 304 并保留响应元信息。
     *  - 该调用的 validateStatus 追加「304 视为成功」，304 无响应体也不会被判失败；
     *  - 成功分支返回 { status, headers, data }（而非既有的「只返回解包数据」），
     *    调用方才能读到 ETag 并区分 304 / 200；
     *  - 其余请求的拦截器行为（信封解包、失败弹错、401 踢出）完全不变；
     *  - 需与 cache:false 搭配（条件请求走真实网络，不参与本地全量副本/memo 那一套）。 */
    conditional?: boolean;
  }
}

const api = axios.create({
  timeout: 15000,
});

api.interceptors.request.use(
  (config) => {
    // 版本硬封锁：客户端版本与服务端不一致时，除显式 bypassVersionBlock 的版本校验请求外，
    // 一律拒绝并阻断网络，实现「无法使用任何功能、发出任何请求」。
    if (versionBlocked.value && !config.bypassVersionBlock) {
      return Promise.reject(
        new Error("客户端版本与服务端不一致，已禁用全部请求，请联系管理员获取最新版本"),
      );
    }
    // 全局门禁（快照强制暂停 / 回退中）：客户端侧先行拦截，不发无谓的网络请求。
    // 服务端 middleware 同样会以 423 拒绝，这里只是第一道闸门（保证提示即时、无噪声）。
    if (!config.bypassGate) {
      const reason = requestBlockReason(config.method || "GET");
      if (reason) return Promise.reject(new Error(reason));
    }
    // 条件请求（C3 心跳等）：**只对该调用**放开 304 —— 对条件请求而言「304 未变更」是成功结果，
    // 而 axios 默认只把 2xx 视作成功、304 会被 reject 成错误（进而触发全局错误提示）。
    // 这里逐调用覆写 validateStatus（保留调用方自带的判定），不影响任何其它请求。
    if (config.conditional) {
      const prevValidate = config.validateStatus;
      config.validateStatus = (status: number) =>
        status === 304 || (prevValidate ? prevValidate(status) : status >= 200 && status < 300);
    }
    const token = getAccountItem("token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    config.baseURL = getApiBaseUrl() + "/api";
    return config;
  },
  (error) => Promise.reject(error),
);

// 将任意错误统一转换为中文提示，避免暴露 axios / HTTP 的英文消息
export function getErrorMessage(error: unknown): string {
  const err = error as any;
  // 优先返回抛出的普通 Error.message（如改密成功后自动重登失败的诚实降级提示
  // "密码修改成功，请使用新密码重新登录"），避免被兜底的"网络错误"覆盖。
  if (err?.message && !err?.response) {
    return err.message;
  }
  if (err?.response?.data?.message) {
    return err.response.data.message;
  }
  if (err?.response) {
    const status = err.response.status;
    const statusText: Record<number, string> = {
      400: "请求参数错误，请检查输入",
      401: "登录已过期，请重新登录",
      403: "没有权限执行此操作",
      404: "请求的资源不存在",
      409: "数据冲突，请刷新后重试",
      422: "请求参数校验失败",
      500: "服务器内部错误，请稍后重试",
      502: "网关错误，请确认服务已启动",
      503: "服务暂不可用，请稍后重试",
      504: "网关超时，请稍后重试",
    };
    return statusText[status] || `请求失败（错误码 ${status}）`;
  }
  return "网络错误，请检查网络连接或服务器是否启动";
}

// ---------- 会话刷新屏蔽（改密自动重登期间抑制 401 踢出）----------
// 改密成功会吊销当前 token，前端随即自动重登换发新 token；窗口期内旧会话的后台请求
// （心跳 / 缓存增量同步等）会返回 401。这些是「旧会话的迟到 401」，若按常规 401 处理
// 会把刚换发的新 token 一并清掉、跳登录页并误弹「账号已在其他设备登录」。
// 改密流程用 setSessionRefreshing(true/false) 包裹，拦截器在此期间静默丢弃 401。
let _sessionRefreshing = false;
export function setSessionRefreshing(v: boolean): void {
  _sessionRefreshing = v;
}
export function isSessionRefreshing(): boolean {
  return _sessionRefreshing;
}

// 423（Locked）提示去抖：门禁期间可能有多个并发请求同时被拒，只提示一次。
let _lastGateToastAt = 0;
function _toastGateBlocked(message: string): void {
  const now = Date.now();
  if (now - _lastGateToastAt < 4000) return;
  _lastGateToastAt = now;
  ElMessage.warning(message);
}

api.interceptors.response.use(
  (response) => {
    // 条件请求（C3 心跳等）：只对该调用返回 { status, headers, data }，
    // 让调用方能区分「304 未变更」与「200 有新内容」并读取 ETag。
    // 304 没有响应体，直接返回 data=null，绝不能进入信封判定（否则会被当成空响应/错误）。
    if (response.config?.conditional) {
      if (response.status === 304) {
        return { status: 304, headers: response.headers ?? {}, data: null } as any;
      }
      const conditionalDecision = interpretResponse(response.data);
      if (conditionalDecision.kind === "error") {
        if (!response.config?.silent) ElMessage.error(conditionalDecision.message);
        return Promise.reject(new Error(conditionalDecision.message));
      }
      return {
        status: response.status,
        headers: response.headers ?? {},
        data: conditionalDecision.value,
      } as any;
    }
    // 响应体归类（纯函数，见 ./envelope.ts）：二进制下载原样返回、带 code 的按信封语义、
    // 其余非信封 2xx 也原样返回 —— 改前直接读 res.code 判定，导致 blob 下载与
    // 非信封响应被一律判为失败（审计 F-01）。
    const decision = interpretResponse(response.data);
    if (decision.kind === "error") {
      ElMessage.error(decision.message);
      return Promise.reject(new Error(decision.message));
    }
    // axios 拦截器按「已解包的业务数据」返回（本文件既有约定，故此处断言为 any）
    return decision.value as any;
  },
  (error) => {
    // 安全加固：立即剥离 error 对象上的 Authorization 头——下游各视图的
    // console.error(e) 会把整个 axios error 打进控制台，连带 Bearer token 泄露。
    // 401「迟到判定」所需的原 token 在剥离前先取出。
    const reqAuth = (error.config?.headers as Record<string, unknown> | undefined)
      ?.Authorization;
    if (error.config?.headers) delete (error.config.headers as Record<string, unknown>).Authorization;
    if (error.headers) delete (error.headers as Record<string, unknown>).Authorization;
    // 后台静默同步请求（缓存增量轮询 / 离线降级）失败不弹提示，由缓存层自行降级。
    if (error.config?.silent && error.response?.status !== 401) {
      return Promise.reject(error);
    }
    // 全局门禁（HTTP 423 Locked）：系统被强制暂停 / 正在回退。
    // 同步服务端下发的门禁快照（含 dataVersion），让遮罩与请求拦截立即生效；
    // 提示去抖，避免并发请求刷屏。
    if (error.response?.status === 423) {
      const gate = error.response.data?.gate as GateState | undefined;
      if (gate) applyGateState(gate);
      if (!error.config?.silent) _toastGateBlocked(getErrorMessage(error));
      return Promise.reject(error);
    }
    if (error.response?.status === 401) {
      // 后端「强制改密」门禁（errorCode=must_change_password）**不是**会话过期：绝不能清 token、
      // 不能跳登录页、不能派发 auth:kicked —— 否则用户正在填写的「修改初始密码」表单会因为
      // 登录态被清空而提交成匿名请求，后端返回「登录已过期，请重新登录」（真机事故：
      // 新部署的超管永远改不了初始密码，等于被挡在门外）。
      // 注：必须用 errorCode（后端 401 的机器码）而不是 data.code —— 后者是 HTTP 状态码 401。
      if (error.response?.data?.errorCode === "must_change_password") {
        if (!error.config?.silent) ElMessage.warning(getErrorMessage(error));
        return Promise.reject(error);
      }
      // 会话刷新窗口（改密自动重登中）：旧会话的 401 一律静默丢弃，不踢出、不弹提示
      if (isSessionRefreshing()) {
        return Promise.reject(error);
      }
      // 迟到 401 判定：触发 401 的请求所携带的 token 已不是当前登录态的 token
      // （改密/顶号后已换发新 token），说明这是旧会话的过期响应——静默忽略，
      // 不清新 token、不跳登录页、不弹「账号已在其他设备登录」。
      const reqToken = typeof reqAuth === "string" ? reqAuth.replace(/^Bearer\s+/i, "") : "";
      const curToken = getAccountItem("token") || "";
      // 登录接口的 401 = 用户名或密码错误：必须先于下面的「本地无登录态静默丢弃」判断——
      // 登录页本来就没有 token，若顺序颠倒，登录失败会被静默吞掉、界面毫无提示（真实事故）。
      const isLoginRequest = error.config?.url?.includes("/auth/login");
      if (isLoginRequest) {
        ElMessage.error(getErrorMessage(error));
        return Promise.reject(error);
      }
      // 本地已无登录态（此前已被踢出/已登出）：残留页面的请求 401 静默丢弃，
      // 不重复弹错（避免「身份认证信息未提供」连环提示）、不重复跳转登录页
      if (!curToken) {
        return Promise.reject(error);
      }
      if (reqToken && reqToken !== curToken) {
        return Promise.reject(error);
      }
      {
        removeAccountItem("token");
        clearCurrentAccountCache().catch(() => {}); // fire-and-forget：同步标记已清除，异步清理 IndexedDB
        _resetMemo();
        window.location.hash = "#/login";
        // 优先采用后端明确提示：被新设备登录顶掉时后端返回「您的账号已在其他设备登录，请重新登录」；
        // 其余 401（token 过期 / 用户被删）后端返回通用「Unauthorized」，回退为「登录已过期」。
        const backendMsg = error.response?.data?.message;
        const msg =
          backendMsg && backendMsg !== "Unauthorized"
            ? backendMsg
            : "登录已过期，请重新登录";
        ElMessage.error(msg);
        // 派发「被顶号 / 登录过期」事件：通知 authStore 同步清空内存登录态，
        // 否则 localStorage 已清但内存 token ref 仍在，路由守卫会把刚跳到的 /login 又弹回首页（回弹）。
        window.dispatchEvent(new CustomEvent("auth:kicked"));
      }
    } else {
      ElMessage.error(getErrorMessage(error));
    }
    return Promise.reject(error);
  },
);

export interface ApiInstance {
  get: <T = any>(url: string, config?: AxiosRequestConfig) => Promise<T>;
  post: <T = any>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>;
  put: <T = any>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>;
  patch: <T = any>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>;
  delete: <T = any>(url: string, config?: AxiosRequestConfig) => Promise<T>;
  defaults: AxiosInstance["defaults"];
  interceptors: AxiosInstance["interceptors"];
}

// ===================== 本地全量副本 + 增量同步（降低服务器压力）=====================
// 设计（详见 client/src/api/cache.ts）：
//   1) 每个「资源集合」在本地维护全量副本；刷新时优先走增量（携带 updatedAfter），仅拉变更数据。
//   2) 首次 / 基线过期 / 被写失效 → 全量同步（大 pageSize 一次取回）。
//   3) 离线 → 用本地全量副本构造响应降级。
//   4) 复合地图（/maps/full）特殊对待：拆成 nodes/edges/nodeTypes/pathTypes 四个本地全量副本。
import {
  cacheGet,
  cacheSet,
  invalidateResource,
  clearCurrentAccountCache,
  SEG_TO_RESOURCE,
  getFull,
  setFull,
  getBaseline,
  setBaseline,
  getFullSyncAt,
  setFullSyncAt,
  patchFullItems,
  extractItems,
  maxUpdatedAtOf,
  inferShape,
  listFullCollections,
  listMapSyncKeys,
} from "./cache";

// 周期强制全量对账：避免基线漂移 / 漏推导致本地副本长期偏离服务端。
const FULL_SYNC_INTERVAL_MS = 5 * 60 * 1000;
// 全量同步时一次性取回的最大条数（分页接口用此覆盖默认 pageSize）。
const LARGE_PAGE_SIZE = 10000;
// 这些参数不参与集合键（分页/时间戳是「视图」参数，不改变集合身份）。
const VIEW_PARAMS = new Set(["page", "pageSize", "updatedAfter"]);

const _getInflight = new Map<string, Promise<unknown>>();

// ---------- 写后强制直连：写操作后下次 GET 绕过缓存直连服务器 ----------
// 写操作（POST/PUT/PATCH/DELETE）成功后，将涉及的资源标记为「强制刷新」，
// 下次该资源的 GET 请求跳过 IndexedDB 缓存 + 增量同步，直接走全量网络请求。
// 彻底避免写后读因 IndexedDB 并发事务 / 基线时序 / 缓存残留等原因返回空数据。
const _forceRefresh = new Set<string>();

function _deriveResourceKey(url: string): string {
  const path = (url || "").split("?")[0];
  const seg = path.split("/").filter(Boolean)[0] || "";
  return SEG_TO_RESOURCE[seg] || seg;
}

// ---------- O3：跨挂载新鲜度窗口（stale-while-revalidate）----------
// 内存 memo：按「请求键」缓存最近一次成功响应；窗口内（且无该资源实时事件）直接返回，
// 避免同一资源在多个组件/多次挂载被重复发往服务端（仍是后台增量请求，但能省则省）。
// 状态与读写器在 ./responseMemo.ts（含「登出/换账号清空」与 epoch 防旧响应回填）。

function _resourceOf(url: string): string {
  const path = (url || "").split("?")[0];
  const seg = path.split("/").filter(Boolean)[0] || "";
  return SEG_TO_RESOURCE[seg] || seg;
}

/** 写操作 / 登录失效后清空内存 memo，避免返回被写失效前的陈旧数据。 */
function _resetMemo(): void {
  resetResponseMemo();
}

/** 对外暴露：清空内存 memo（设置页「清空本地缓存」等场景调用，配合清空 IndexedDB 后重载页面）。 */
export function resetRequestMemo(): void {
  _resetMemo();
}

// 切换服务器后清空内存 memo：缓存库已按 realm（服务器身份）隔离，但内存 memo 键与服务器无关，
// 若不清空，旧服务器的响应可能被新服务器命中，造成串档。由 config/index.ts 的 setServerUrl 派发。
if (typeof window !== "undefined") {
  window.addEventListener("server:changed", _resetMemo);
}

function _reqKey(method: string, url: string, config?: AxiosRequestConfig): string {
  const params = config?.params ? JSON.stringify(config.params) : "";
  return `${method.toUpperCase()} ${url} ${params}`;
}

function _cacheable(config?: AxiosRequestConfig): boolean {
  // 仅当显式 cache === false 时绕过本地缓存；未设置（默认）或 true 均走缓存。
  return config?.cache !== false && !config?.signal;
}

/** 合并 URL 查询串与 axios params 为单一对象。 */
function collectParams(url: string, config?: AxiosRequestConfig): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  const qIdx = (url || "").indexOf("?");
  if (qIdx >= 0) {
    const sp = new URLSearchParams(url.slice(qIdx + 1));
    sp.forEach((v, k) => (params[k] = v));
  }
  if (config?.params) Object.assign(params, config.params);
  return params;
}

/** 由 URL + params 推导稳定的「集合键」：资源名 | 非视图参数的升序拼接。
 *  注意：嵌套路由（如 /competitions/123/fiscal-years）必须把子路径编入键，
 *  否则会与父列表（/competitions）撞同一个集合键，互相覆盖本地全量副本。 */
function collectionKeyFor(url: string, config?: AxiosRequestConfig): string {
  const path = (url || "").split("?")[0];
  const parts = path.split("/").filter(Boolean);
  const seg = parts[0] || "";
  const resource = SEG_TO_RESOURCE[seg] || seg;
  const subPath = parts.slice(1).join("/"); // 嵌套子路径，如 "123/fiscal-years"
  const params = collectParams(url, config);
  const restParts: string[] = [];
  if (subPath) restParts.push(`_p=${subPath}`);
  for (const k of Object.keys(params)
    .filter((k) => !VIEW_PARAMS.has(k))
    .sort()) {
    restParts.push(`${k}=${params[k]}`);
  }
  return `${resource}|${restParts.join("&")}`;
}

function isMapFullUrl(url: string): boolean {
  return (url || "").split("?")[0].replace(/\/$/, "").endsWith("/maps/full");
}

/**
 * 列表响应统一「降维」为裸数组：
 *  - 裸数组原样返回；
 *  - 分页对象 { items, total, ... } 取 items 展开为数组；
 *  - 复合地图 /maps/full、公司产业字段 /company-fields/:id、详情对象（含 id 但非数组）保持原样。
 *  保证下游列表组件写 `Array.isArray(res) ? res : []` 时不再把分页对象误判为空，
 *  同时不影响需要消费 total/page 等字段的调用方（若有此类场景，用参数 opt.normalize=false 跳过降维）。
 */
function normalizeListResponse(url: string, v: unknown): unknown {
  // 明确不做降维的特殊形态 URL
  if (isMapFullUrl(url) || isCompanyFieldsUrl(url)) return v;
  // 裸数组：直接返回（行业类型 / 比赛列表 / 合同类型等返回裸数组的接口）
  if (Array.isArray(v)) return v;
  // 非对象 / 为空：原样
  if (!v || typeof v !== "object") return v;
  const rec = v as Record<string, unknown>;
  // 分页对象：必须有 items 数组 + total 字段；避免把 { data: [...] }、详情 { id, name } 等误判
  if (Array.isArray(rec.items) && "total" in rec) return rec.items;
  // 其余（详情、派生对象等）：原样返回
  return v;
}

const MAP_SUB_RESOURCES = ["mapNode", "mapEdge", "mapNodeType", "pathType"] as const;

function mapSyncKey(competitionId: string | number | undefined): string {
  return `mapFull|competitionId=${competitionId ?? ""}`;
}
function mapSubKey(resource: string, competitionId: string | number | undefined): string {
  return `${resource}|competitionId=${competitionId ?? ""}`;
}

/** 按集合的 shape 与请求的分页参数，把本地全量副本「还原」成组件期望的响应形态。
 *  实现见 ./localPaging.ts 的 applyLocalPaging（顶部已别名导入为 reconstruct）。 */

/** 全量同步：循环分页拉取，直到取满 total，避免单集合超过 LARGE_PAGE_SIZE 时本地副本被截断。
 *  返回合并后的响应（items 为全量，total 为真实总数），供 storeAndReturn 写入本地全量副本。 */
async function fetchFullSync(url: string, config: AxiosRequestConfig, params: Record<string, unknown>): Promise<unknown> {
  // 全量同步必须剥离增量专用参数（updatedAfter / requireExistingIds）：
  // 否则服务端会因收到 updatedAfter 走增量分支、仅返回 delta，被 storeAndReturn 误当全量副本
  // 覆盖写，导致「创建数据后本地显示暂无数据」/ 列表被清空。
  const fullParams: Record<string, unknown> = { ...params };
  delete fullParams.updatedAfter;
  delete fullParams.requireExistingIds;
  const syncParams: Record<string, unknown> = { ...fullParams, page: 1, pageSize: LARGE_PAGE_SIZE };
  const first: unknown = await (api as any).get(url, { ...config, params: syncParams });
  // 裸数组接口（competitions / companies / industry-types / warehouses / production-lines 等）：
  // 服务端忽略分页参数、一次返回全量数组。直接原样返回，避免被下方分页分支误裹成
  // { items, total, page, pageSize } 对象 —— 否则组件拿到非数组对象，[...res] 会抛 TypeError
  // 导致列表空白（且本地全量副本被错存为 "paged" 形状，后续增量刷新也一并出错）。
  if (Array.isArray(first)) return first;
  const ex = extractItems(first);
  if (!ex) return first; // 非列表（详情）：原样返回，无需分页
  const firstRec = first as Record<string, unknown> | null;
  const knownTotal = typeof firstRec?.total === "number" ? firstRec.total : null;
  if (knownTotal != null && knownTotal <= LARGE_PAGE_SIZE) return first; // 单页足以覆盖
  let items = ex.items.slice();
  let page = 1;
  let lastLen = ex.items.length;
  // 已知 total：拉到取满为止；未知 total（防御）：直到某页返回不足一页为止。
  // 最多迭代 100 次，防止异常数据导致无限循环。
  const MAX_PAGES = 100;
  while ((knownTotal == null ? lastLen === LARGE_PAGE_SIZE : items.length < knownTotal) && page < MAX_PAGES) {
    page++;
    const r: unknown = await (api as any).get(url, { ...config, params: { ...syncParams, page, pageSize: LARGE_PAGE_SIZE } });
    const rx = extractItems(r);
    if (!rx || rx.items.length === 0) break;
    items = items.concat(rx.items);
    lastLen = rx.items.length;
    if (knownTotal != null && items.length >= knownTotal) break;
  }
  return { ...(first as Record<string, unknown>), items, total: knownTotal ?? items.length };
}

/** 全量同步后写入本地副本并设立基线；按请求的分页参数「还原」成组件期望的响应形态返回。
 *  非列表形态（详情）按原 key 缓存以离线降级。 */
async function storeAndReturn(
  ck: string,
  v: unknown,
  params: Record<string, unknown>,
  url: string,
  config?: AxiosRequestConfig,
): Promise<unknown> {
  const shape = inferShape(v);
  if (shape) {
    const items = extractItems(v)!.items;
    await setFull(ck, { items, shape });
    const vRec = v as Record<string, unknown> | null;
    const base = (vRec?.serverTime as string | undefined) || maxUpdatedAtOf(v);
    if (base) await setBaseline(ck, base);
    await setFullSyncAt(ck, Date.now());
    // 还原分页形态：组件看到的是「当前页切片 + total=全量条数」，与改造前一致，
    // 同时本地已存全量副本，后续翻页/增量都无需再打服务器。
    return reconstruct(items, shape, params);
  }
  // 详情：保留原请求键的缓存，供离线降级
  await cacheSet(_reqKey("GET", url, config), v);
  return v;
}

// ---------- 复合地图 /maps/full ----------
function maxUpdatedAtComposite(v: unknown): string | null {
  let maxStr: string | null = null;
  let maxMs = -Infinity;
  const vRec = v as Record<string, unknown> | null;
  for (const key of ["nodes", "edges", "nodeTypes", "pathTypes"]) {
    const arr = (vRec && vRec[key]) || [];
    if (!Array.isArray(arr)) continue;
    for (const it of arr) {
      const u = it?.updatedAt;
      if (u != null) {
        const ms = Date.parse(String(u));
        if (!Number.isNaN(ms) && ms > maxMs) {
          maxMs = ms;
          maxStr = String(u);
        }
      }
    }
  }
  return maxStr;
}

async function reconstructMap(competitionId: string | number | undefined): Promise<Record<string, unknown>> {
  const subs = MAP_SUB_RESOURCES.map((r) => mapSubKey(r, competitionId));
  const [n, e, nt, pt] = await Promise.all(subs.map(getFull));
  return {
    nodes: n?.items || [],
    edges: e?.items || [],
    nodeTypes: nt?.items || [],
    pathTypes: pt?.items || [],
  };
}

async function storeMapAndReturn(
  ck: string,
  competitionId: string | number | undefined,
  v: unknown,
): Promise<unknown> {
  const subs = MAP_SUB_RESOURCES.map((r) => mapSubKey(r, competitionId));
  const vRec = v as Record<string, unknown> | null;
  await Promise.all([
    setFull(subs[0], { items: (vRec?.nodes as unknown[] | undefined) || [], shape: "array" }),
    setFull(subs[1], { items: (vRec?.edges as unknown[] | undefined) || [], shape: "array" }),
    setFull(subs[2], { items: (vRec?.nodeTypes as unknown[] | undefined) || [], shape: "array" }),
    setFull(subs[3], { items: (vRec?.pathTypes as unknown[] | undefined) || [], shape: "array" }),
  ]);
  const base = (vRec?.serverTime as string | undefined) || maxUpdatedAtComposite(v);
  if (base) await setBaseline(ck, base);
  await setFullSyncAt(ck, Date.now());
  return v;
}

async function syncMapFull(url: string, config: AxiosRequestConfig, competitionId: string | number | undefined): Promise<unknown> {
  const ck = mapSyncKey(competitionId);
  const subs = MAP_SUB_RESOURCES.map((r) => mapSubKey(r, competitionId));
  const [fulls, baseline, fullSyncAt] = await Promise.all([
    Promise.all(subs.map(getFull)),
    getBaseline(ck),
    getFullSyncAt(ck),
  ]);
  const hasCopy = fulls.every((f) => f != null) && baseline != null;
  // 过期时带 requireExistingIds 复核删除；新鲜窗口下服务端 getFullMap 默认不下发 existingIds。
  const needReconcile = fullSyncAt != null && Date.now() - fullSyncAt >= FULL_SYNC_INTERVAL_MS;

  if (hasCopy && baseline) {
    // 本地已有全量副本：走增量，过期时带 requireExistingIds 复核删除（O2），不再整表重拉。
    const params = collectParams(url, config);
    const incParams: Record<string, unknown> = { ...params, updatedAfter: baseline };
    if (needReconcile) incParams.requireExistingIds = "true";
    const vRaw = await (api as any).get(url, { ...config, params: incParams });
    const v = vRaw as Record<string, unknown> | null;
    if (v && v.incremental) {
      const existingIds = v.existingIds as Record<string, unknown> | undefined;
      const deletedIds = v.deletedIds as Record<string, unknown> | undefined;
      // 优先使用deletedIds（新协议：客户端发送previousIds，服务器返回deletedIds）
      // 向后兼容：如果服务器返回existingIds（旧协议），则使用existingIds
      await patchFullItems(subs[0], (v.nodes as unknown[]) || [], existingIds?.nodes as number[] | undefined, deletedIds?.nodes as number[] | undefined);
      await patchFullItems(subs[1], (v.edges as unknown[]) || [], existingIds?.edges as number[] | undefined, deletedIds?.edges as number[] | undefined);
      await patchFullItems(subs[2], (v.nodeTypes as unknown[]) || [], existingIds?.nodeTypes as number[] | undefined, deletedIds?.nodeTypes as number[] | undefined);
      await patchFullItems(subs[3], (v.pathTypes as unknown[]) || [], existingIds?.pathTypes as number[] | undefined, deletedIds?.pathTypes as number[] | undefined);
      await setBaseline(ck, (v.serverTime as string) || baseline);
      await setFullSyncAt(ck, Date.now());
      return reconstructMap(competitionId);
    }
    return storeMapAndReturn(ck, competitionId, v);
  }
  const v = await (api as any).get(url, config);
  return storeMapAndReturn(ck, competitionId, v);
}

async function degradeMap(competitionId: string | number | undefined, e: unknown): Promise<unknown> {
  const reconstructed = await reconstructMap(competitionId);
  if (
    (reconstructed.nodes as unknown[]).length ||
    (reconstructed.edges as unknown[]).length ||
    (reconstructed.nodeTypes as unknown[]).length ||
    (reconstructed.pathTypes as unknown[]).length
  ) {
    return reconstructed;
  }
  throw e;
}

// ---------- 公司产业字段（派生集合，特殊处理）----------
// 公司产业字段返回 { industryTypeId, fields:[...] } 而非列表形态，故不走通用列表逻辑，
// 而是像复合地图一样维护「每公司一份本地全量副本」（items = fields 数组，每项带 id/updatedAt）。
function isCompanyFieldsUrl(url: string): boolean {
  return (url || "").split("?")[0].replace(/\/$/, "").startsWith("/company-fields/");
}

function companyFieldId(url: string): number | null {
  const m = (url || "").split("?")[0].match(/\/company-fields\/(\d+)/);
  return m ? Number(m[1]) : null;
}

function companyFieldKey(companyId: string | number | undefined): string {
  return `companyField|companyId=${companyId ?? ""}`;
}

/**
 * 产业字段元素 → 本地副本条目：补一个 `id` 别名。
 *
 * 后端 `/company-fields/{cid}` 与批量端点的 fields 元素**没有 `id` 键**，其 id 语义是
 * `industryFieldId`（见 backend/apps/company_fields/views.py 的契约注释与设计说明 §4.2），
 * `existingIds` / `deletedIds` 里同样是 industryFieldId。而：
 *   - `patchFullItems` 按 `it.id` 建 Map，元素没有 id 时「变更项不被合并」且
 *     existingIds 非空时会把本地副本**整份过滤掉**（重连对账后公司产业字段变空）；
 *   - 既有消费方两种口径都有：`StockManageView` 用 `fv.id`、`CompanyDetailView` 用 `f.id`、
 *     `useDashboardFields` 用 `f.industryFieldId`。
 * 故在此统一补 `id = industryFieldId`（已有 id 时原样保留），三处消费方与增量合并同时正确。
 */
export function normalizeCompanyFieldItems(fields: unknown[]): unknown[] {
  const out: unknown[] = [];
  for (const f of fields || []) {
    if (!f || typeof f !== "object") {
      out.push(f);
      continue;
    }
    const rec = f as Record<string, unknown>;
    const iid = rec.industryFieldId ?? rec.industry_field_id;
    if (rec.id == null && typeof iid === "number") out.push({ ...rec, id: iid });
    else out.push(rec);
  }
  return out;
}

async function storeCompanyFieldsAndReturn(ck: string, v: unknown): Promise<unknown> {
  const vRec = v as Record<string, unknown> | null;
  const fields: unknown[] = normalizeCompanyFieldItems((vRec?.fields as unknown[]) || []);
  await setFull(ck, { items: fields, shape: "array" });
  const base = (vRec?.serverTime as string | undefined) || maxUpdatedAtOf(fields);
  if (base) await setBaseline(ck, base);
  await setFullSyncAt(ck, Date.now());
  return { industryTypeId: vRec?.industryTypeId ?? null, fields };
}

async function syncCompanyFields(url: string, config: AxiosRequestConfig, companyId: string | number): Promise<unknown> {
  const ck = companyFieldKey(companyId);
  const [full, baseline] = await Promise.all([
    getFull(ck),
    getBaseline(ck),
  ]);
  const hasCopy = full != null && baseline != null;

  try {
    if (hasCopy && baseline) {
      // 本地已有副本：走增量；服务端 getValues 始终回传 existingIds（含可见字段定义 id），
      // 前端据此核对被隐藏/被移除的字段，无需整表重拉（O2）。
      const params = collectParams(url, config);
      const vRaw = await (api as any).get(url, {
        ...config,
        params: { ...params, updatedAfter: baseline },
      });
      const v = vRaw as Record<string, unknown> | null;
      if (v && v.incremental) {
        // 优先使用deletedIds（新协议：客户端发送previousIds，服务器返回deletedIds）
        // 向后兼容：如果服务器返回existingIds（旧协议），则使用existingIds
        const deletedIds = v.deletedIds as number[] | undefined;
        const existingIds = v.existingIds as number[] | undefined;
        // 元素无 id 键（id 语义 = industryFieldId）→ 先归一，否则 patchFullItems 会把本地副本清空
        const merged = await patchFullItems(
          ck,
          normalizeCompanyFieldItems((v.fields as unknown[]) || []),
          existingIds,
          deletedIds,
        );
        await setBaseline(ck, (v.serverTime as string) || baseline);
        await setFullSyncAt(ck, Date.now());
        const firstMerged = merged[0] as Record<string, unknown> | undefined;
        return { industryTypeId: v.industryTypeId ?? firstMerged?.industryTypeId ?? null, fields: merged };
      }
      // 服务端未返回增量形态（兜底）：按全量处理
      return storeCompanyFieldsAndReturn(ck, v);
    }
    // 首次 / 写失效：本地无副本，走全量同步
    const v = await (api as any).get(url, config);
    return storeCompanyFieldsAndReturn(ck, v);
  } catch (e: unknown) {
    const fullNow = await getFull(ck);
    if (fullNow) {
      const firstItem = fullNow.items[0] as Record<string, unknown> | undefined;
      return {
        industryTypeId: firstItem?.industryTypeId ?? null,
        fields: fullNow.items,
      };
    }
    throw e;
  }
}

// ---------- 通用列表 ----------
async function cachedGetImpl(url: string, config: AxiosRequestConfig): Promise<unknown> {
  // 复合地图
  if (isMapFullUrl(url)) {
    const params = collectParams(url, config);
    const cid = params.competitionId as string | number | undefined;
    try {
      return await syncMapFull(url, config, cid);
    } catch (e) {
      return degradeMap(cid, e);
    }
  }

  // 公司产业字段（派生集合，每公司一份本地全量副本）
  if (isCompanyFieldsUrl(url)) {
    const cid = companyFieldId(url);
    if (cid == null) return (api as any).get(url, config);
    return syncCompanyFields(url, config, cid);
  }

  const ck = collectionKeyFor(url, config);
  const params = collectParams(url, config);

  // 写后强制直连：跳过本地缓存，直接全量同步
  const resourceKey = _deriveResourceKey(url);
  const isForceRefresh = _forceRefresh.has(resourceKey);
  if (isForceRefresh) {
    _forceRefresh.delete(resourceKey);
    try {
      const v = await fetchFullSync(url, config, params);
      return await storeAndReturn(ck, v, params, url, config);
    } catch (e: unknown) {
      // 直连也失败 → 降级到本地缓存（若有的话）
      const fullNow = await getFull(ck);
      if (fullNow) return reconstruct(fullNow.items, fullNow.shape, params);
      throw e;
    }
  }

  const full = await getFull(ck);
  const baseline = await getBaseline(ck);
  const fullSyncAt = await getFullSyncAt(ck);
  const hasCopy = full != null && baseline != null;
  // 基线过期（>= FULL_SYNC_INTERVAL_MS）→ 走「对账」增量：携带 requireExistingIds，
  // 用服务端回传的全体 id 复核被删除/被移除的本地副本；新鲜窗口内仅拉变更，删除由实时事件精确处理。
  const needReconcile = fullSyncAt != null && Date.now() - fullSyncAt >= FULL_SYNC_INTERVAL_MS;

  try {
    if (hasCopy && baseline) {
      // 本地已有全量副本：始终走增量，不再整表重拉（O2）。
      const incParams: Record<string, unknown> = { ...params, updatedAfter: baseline };
      if (needReconcile) incParams.requireExistingIds = "true";
      const vRaw = await (api as any).get(url, { ...config, params: incParams });
      const v = vRaw as Record<string, unknown> | null;
      if (v && v.incremental) {
        // 优先使用deletedIds（新协议：客户端发送previousIds，服务器返回deletedIds）
        // 向后兼容：如果服务器返回existingIds（旧协议），则使用existingIds
        const deletedIds = v.deletedIds as number[] | undefined;
        const existingIds = v.existingIds as number[] | undefined;
        const merged = await patchFullItems(ck, (v.items as unknown[]) || [], existingIds, deletedIds);
        await setBaseline(ck, (v.serverTime as string) || baseline);
        // 对账成功后刷新「上次全量同步时间」，使 existingIds 开销每 FULL_SYNC_INTERVAL_MS 才发生一次
        await setFullSyncAt(ck, Date.now());
        return reconstruct(merged, full!.shape, params);
      }
      // 服务端未返回增量形态（兜底）：按全量处理
      const fv = await fetchFullSync(url, config, params);
      return await storeAndReturn(ck, fv, params, url, config);
    }
    // 首次 / 写失效 / 401 清缓存：本地无副本，走全量同步（大 pageSize 一次取回）
    const v = await fetchFullSync(url, config, params);
    return await storeAndReturn(ck, v, params, url, config);
  } catch (e: unknown) {
    // 离线降级
    const fullNow = await getFull(ck);
    if (fullNow) return reconstruct(fullNow.items, fullNow.shape, params);
    const cached = await cacheGet(_reqKey("GET", url, config));
    if (cached !== null) return cached;
    throw e;
  }
}

async function _cachedGet<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T> {
  // 列表响应统一出口：缺省降维为裸数组；config.normalize === false 时保留原始
  // {items,total,...} 分页对象（供需要 total/分页字段的调用方，如审计日志页）。
  const unwrap = (v: unknown): T =>
    config?.normalize === false ? (v as T) : (normalizeListResponse(url, v) as T);
  if (!_cacheable(config)) {
    // 显式退出缓存的请求视为用户主动操作，仍正常弹错提示。
    const raw = await (api as any).get(url, config);
    return unwrap(raw);
  }
  // 走本地全量副本 / 增量同步的 GET 均为「后台数据同步」，失败应静默降级
  // （缓存层已做离线/基线回退），不应向用户弹「权限不足」等提示，
  // 否则无权限的账号会被后台周期轮询频繁打扰。
  const silentConfig = { ...config, silent: true };
  const key = _reqKey("GET", url, silentConfig);
  const pending = _getInflight.get(key);
  if (pending) {
    return pending.then((v) => unwrap(v)) as Promise<T>;
  }

  // O3：窗口内且无该资源实时事件 → 直接返回内存副本，不打网络（含后台增量请求）。
  const resource = _resourceOf(url);
  const m = getMemoEntry(key);
  if (isMemoFresh(m, resource)) {
    return unwrap(m!.value) as Promise<T>;
  }

  // F2 修复：记录请求发起时刻（而非完成时刻），确保事件晚于发起时刻时 memo 失效
  const startedAt = Date.now();
  // 记录发起时的会话 epoch：登出/换账号会递增它，旧响应返回后不得再写回 memo（审计 F-06）
  const epoch = memoEpoch();
  const p = cachedGetImpl(url, silentConfig).finally(() => _getInflight.delete(key));
  _getInflight.set(key, p);
  const result = await p;
  // 使用 startedAt 而非 Date.now()，消除时序竞态窗口；memo 存原始结构（保留分页 total 等）
  writeMemo(key, { time: startedAt, value: result }, epoch);
  // 对外返回：列表统一降维为裸数组（normalize=false 时保留分页对象），兼容下游 `Array.isArray(res)` 写法
  return unwrap(result);
}

function _mutating(
  method: "post" | "put" | "patch" | "delete",
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
) {
  const res =
    method === "delete"
      ? (api as any).delete(url, config)
      : (api as any)[method](url, data, config);
  // 写操作完成后（无论成败）清空内存 memo，避免 O3 窗口返回陈旧数据；
  // 写成功时额外失效本地全量副本，保证后续读取走全量同步拿到最新。
  // 传递响应数据使 invalidateResource 能按 competitionId 精确失效，避免误清其他比赛的缓存。
  Promise.resolve(res).then(
    (data) => {
      invalidateResource(url, data);
      _resetMemo();
      // 标记该资源下次 GET 强制直连服务器，彻底绕过 IndexedDB 缓存 / 增量同步
      _forceRefresh.add(_deriveResourceKey(url));
    },
    () => {
      _resetMemo();
    },
  );
  return res;
}

// 反向映射：resource 名 → URL 首段（如 "material" → "materials"）
const RESOURCE_TO_SEG: Record<string, string> = Object.fromEntries(
  Object.entries(SEG_TO_RESOURCE).map(([seg, res]) => [res, seg]),
);

// ===================== C3：重连对账的并发上限 / 指数退避 / 抖动 / 批量端点 =====================
// 背景（简报 C3.2）：断线重连会对「本地已加载的每个集合 + 每张复合地图 + 每个访问过的公司」
// 一次性扇出（100 客户端 × 10 集合 ≈ 上千请求在同一毫秒涌向后端）。这里做三件事：
//   1) 并发上限：同一时刻最多 RECONCILE_CONCURRENCY（默认 2）个请求在途；
//   2) 启动抖动 + 失败重试的指数退避：避免全体客户端同一毫秒一起对账；
//   3) 产业字段改走批量端点 GET /company-fields?companyIds=…（设计说明 §4.2），
//      批量不可用时回落逐个 /company-fields/{cid}，对账不丢。
// 语义不变：单个集合失败仍静默（不弹提示、不中断其余集合），完成后照旧派发 sync:reconciled。

/** 重连对账的在途请求并发上限（具名常量，可调大以换取更快对账）。
 *  现场如需调整：改这里并重新 `npm run build`（不做构建期可配置，避免引入构建变量复杂度）；
 *  建议按简报 C3.4 的压测结果（断网恢复后的请求数与耗时）再定 2 还是 4。 */
export const RECONCILE_CONCURRENCY = 2;
/** 单个对账任务的最大尝试次数（首次 + 2 次退避重试）；耗尽后静默放弃（与改造前一致）。 */
export const RECONCILE_MAX_ATTEMPTS = 3;
/** 退避基数（ms）：第 n 次重试等待 ≈ base × 2^n，再乘抖动系数。 */
export const RECONCILE_RETRY_BASE_MS = 500;
/** 退避上限（ms）：指数增长到此封顶。 */
export const RECONCILE_RETRY_MAX_MS = 8000;
/** 对账开始前的全场抖动窗口（ms）：每个客户端随机等待 [0, 该值)，避免同一毫秒一起重连。 */
export const RECONCILE_START_JITTER_MS = 1000;
/** 批量端点单次请求的公司数上限（与设计说明 §4.2 建议值 50 对齐；超出则分批）。 */
export const COMPANY_FIELDS_BATCH_LIMIT = 50;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 抖动系数：0.5x ~ 1.5x（rand ∈ [0,1]）。 */
function jitterFactor(rand: () => number): number {
  const r = rand();
  const v = Number.isFinite(r) ? r : 0.5;
  return 0.5 + Math.min(Math.max(v, 0), 1);
}

/** 指数退避 + 抖动（纯函数，便于单测）：
 *  第 attempt 次重试等待 = min(RECONCILE_RETRY_BASE_MS × 2^attempt, RECONCILE_RETRY_MAX_MS) × [0.5, 1.5)。 */
export function reconcileRetryDelayMs(attempt: number, rand: () => number = Math.random): number {
  const n = Number.isFinite(attempt) ? Math.max(0, Math.floor(attempt)) : 0;
  const capped = Math.min(RECONCILE_RETRY_BASE_MS * 2 ** n, RECONCILE_RETRY_MAX_MS);
  return Math.round(capped * jitterFactor(rand));
}

/** 对账启动抖动（纯函数，便于单测）：[0, RECONCILE_START_JITTER_MS)。 */
export function reconcileStartDelayMs(rand: () => number = Math.random): number {
  const r = rand();
  const v = Number.isFinite(r) ? r : 0.5;
  return Math.round(Math.min(Math.max(v, 0), 1) * RECONCILE_START_JITTER_MS);
}

/** 具名并发上限执行器：同一时刻最多 limit 个任务在途，逐个从队首取任务。
 *  单个任务失败不影响其余（对账整体静默）。 */
export async function runWithConcurrency<T>(
  tasks: Array<() => Promise<T>>,
  limit: number = RECONCILE_CONCURRENCY,
): Promise<void> {
  const queue = Array.isArray(tasks) ? tasks.slice() : [];
  if (!queue.length) return;
  const size = Math.max(1, Math.min(Math.floor(limit) || RECONCILE_CONCURRENCY, queue.length));
  const worker = async (): Promise<void> => {
    for (;;) {
      const task = queue.shift();
      if (!task) return;
      try {
        await task();
      } catch {
        /* 单个任务失败不影响其余（对账整体静默） */
      }
    }
  };
  await Promise.all(Array.from({ length: size }, () => worker()));
}

/** 是否值得退避重试：5xx / 408 / 429 / 无响应的网络错误可重试；
 *  确定性失败（404 端点不存在、403 无权限、401 失效、400 参数错）不重试，
 *  避免在「批量端点尚未部署」这类情况下放大请求量与日志。 */
function isRetriableReconcileError(e: unknown): boolean {
  const status = (e as { response?: { status?: number } } | null)?.response?.status;
  if (typeof status === "number") return status >= 500 || status === 408 || status === 429;
  return true;
}

/** 带指数退避 + 抖动的重试：不可重试或尝试耗尽 → 返回 null（调用方静默）。 */
export async function reconcileWithRetry<T>(task: () => Promise<T>): Promise<T | null> {
  for (let attempt = 0; attempt < RECONCILE_MAX_ATTEMPTS; attempt += 1) {
    try {
      return await task();
    } catch (e) {
      if (attempt >= RECONCILE_MAX_ATTEMPTS - 1 || !isRetriableReconcileError(e)) return null;
      await sleep(reconcileRetryDelayMs(attempt));
    }
  }
  return null;
}

export interface CompanyFieldReconcileEntry {
  /** 本地全量副本集合键，如 companyField|companyId=1 */
  collectionKey: string;
  companyId: number;
  /** 该集合的本地基线（作为 updatedAfter 发出） */
  baseline: string;
}

/** 把单公司 / 批量返回的增量对象合入本地副本并推进基线（与单点端点语义一致）。
 *  只要带 fields 数组就合并：patchFullItems 仅在有非空 existingIds/deletedIds 时才做删除核对，
 *  对「服务端误按全量返回」同样安全；放宽后也不会因后端把 incremental 标记只放在顶层而静默失效。 */
async function applyCompanyFieldIncremental(
  collectionKey: string,
  baseline: string,
  v: unknown,
  fallbackServerTime?: string,
): Promise<void> {
  const rec = v as Record<string, unknown> | null;
  if (!rec || typeof rec !== "object" || !Array.isArray(rec.fields)) return;
  // 优先使用 deletedIds（新协议：客户端发送 previousIds，服务器返回 deletedIds）
  // 向后兼容：如果服务器返回 existingIds（旧协议），则使用 existingIds
  const deletedIds = rec.deletedIds as number[] | undefined;
  const existingIds = rec.existingIds as number[] | undefined;
  // 元素无 id 键（id 语义 = industryFieldId）→ 先归一并补 id 别名，与 existingIds/deletedIds 对齐
  await patchFullItems(
    collectionKey,
    normalizeCompanyFieldItems((rec.fields as unknown[]) || []),
    existingIds,
    deletedIds,
  );
  await setBaseline(collectionKey, (rec.serverTime as string) || fallbackServerTime || baseline);
}

/** 单公司产业字段增量对账（旧路径；批量端点不可用时的回落，带退避重试且失败静默）。 */
async function reconcileCompanyFieldOne(entry: CompanyFieldReconcileEntry): Promise<void> {
  await reconcileWithRetry(async () => {
    const v = await (api as any).get(`/company-fields/${entry.companyId}`, {
      params: { updatedAfter: entry.baseline },
      silent: true,
    });
    await applyCompanyFieldIncremental(entry.collectionKey, entry.baseline, v);
  });
}

/** 批量端点增量对账：一次请求多公司（设计说明 §4.2）。
 *  返回 true = 批量端点可用且已按公司合入；false = 需回落逐个端点
 *  （旧后端 404 / 400 超限 / 网络失败 / 响应不含 companies），回落由调用方负责。 */
async function reconcileCompanyFieldsBatch(
  entries: CompanyFieldReconcileEntry[],
  baseline: string,
): Promise<boolean> {
  try {
    const v = await (api as any).get("/company-fields", {
      params: { companyIds: entries.map((e) => e.companyId).join(","), updatedAfter: baseline },
      silent: true,
    });
    const data = v as Record<string, unknown> | null;
    const companies = data?.companies as Record<string, Record<string, unknown>> | undefined;
    if (!companies || typeof companies !== "object") return false;
    const fallbackServerTime = data?.serverTime as string | undefined;
    for (const entry of entries) {
      // 无权 / 不存在的公司进 missing，服务端不返回其数据（§4.2）→ 保持本地副本不动
      const one = companies[String(entry.companyId)];
      if (!one) continue;
      await applyCompanyFieldIncremental(entry.collectionKey, baseline, one, fallbackServerTime);
    }
    return true;
  } catch {
    return false; // 批量失败 → 回落逐个端点，对账不丢（失败静默）
  }
}

/**
 * 产业字段对账（C3.3）：按基线分组，同一基线的多家公司合并为一次批量请求；
 * 批量不可用则回落逐个 /company-fields/{cid}。
 * 导出以便单测：Node 侧无 IndexedDB，真实 reconcileAllIncremental 无法在无 IndexedDB 环境枚举集合。
 */
export async function reconcileCompanyFields(entries: CompanyFieldReconcileEntry[]): Promise<void> {
  const groups = new Map<string, CompanyFieldReconcileEntry[]>();
  const seen = new Set<string>();
  for (const e of entries || []) {
    if (!e || e.companyId == null || !e.baseline) continue;
    // 同一基线同一公司只对账一次：companyIds 去重后 >50 家会被后端 400（§4.2 上限）
    const dedupeKey = `${e.baseline}|${e.companyId}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    const g = groups.get(e.baseline);
    if (g) g.push(e);
    else groups.set(e.baseline, [e]);
  }
  for (const [baseline, group] of groups) {
    for (let i = 0; i < group.length; i += COMPANY_FIELDS_BATCH_LIMIT) {
      const chunk = group.slice(i, i + COMPANY_FIELDS_BATCH_LIMIT);
      // 单公司分组：批量端点无收益（还多一次「旧后端 404」探测），直接走单点端点。
      if (chunk.length === 1) {
        await reconcileCompanyFieldOne(chunk[0]);
        continue;
      }
      const okBatch = await reconcileCompanyFieldsBatch(chunk, baseline);
      if (!okBatch) {
        for (const e of chunk) await reconcileCompanyFieldOne(e);
      }
    }
  }
}

/** 单个普通集合的增量对账（带指数退避重试；失败静默）。 */
async function reconcileCollectionIncremental(
  c: { collectionKey: string; resource: string; rest: string },
  seg: string,
): Promise<void> {
  await reconcileWithRetry(async () => {
    const baseline = await getBaseline(c.collectionKey);
    if (!baseline) return;
    const params: Record<string, unknown> = {};
    if (c.rest) {
      for (const kv of c.rest.split("&")) {
        const eq = kv.indexOf("=");
        if (eq > 0) params[kv.slice(0, eq)] = kv.slice(eq + 1);
      }
    }
    const vRaw = await (api as any).get(`/${seg}`, {
      params: { ...params, updatedAfter: baseline, requireExistingIds: "true" },
      silent: true,
    });
    const v = vRaw as Record<string, unknown> | null;
    if (v && v.incremental) {
      // 优先使用deletedIds（新协议：客户端发送previousIds，服务器返回deletedIds）
      // 向后兼容：如果服务器返回existingIds（旧协议），则使用existingIds
      const deletedIds = v.deletedIds as number[] | undefined;
      const existingIds = v.existingIds as number[] | undefined;
      await patchFullItems(c.collectionKey, (v.items as unknown[]) || [], existingIds, deletedIds);
      await setBaseline(c.collectionKey, (v.serverTime as string) || baseline);
    }
  });
}

/** 单张复合地图的增量对账（带指数退避重试；失败静默）。 */
async function reconcileMapIncremental(m: {
  syncKey: string;
  competitionId: string | number;
}): Promise<void> {
  await reconcileWithRetry(async () => {
    const baseline = await getBaseline(m.syncKey);
    if (!baseline) return;
    const vRaw = await (api as any).get("/maps/full", {
      params: { competitionId: m.competitionId, updatedAfter: baseline, requireExistingIds: "true" },
      silent: true,
    });
    const v = vRaw as Record<string, unknown> | null;
    if (v && v.incremental) {
      const existingIds = v.existingIds as Record<string, unknown> | undefined;
      const deletedIds = v.deletedIds as Record<string, unknown> | undefined;
      const subs = MAP_SUB_RESOURCES.map((r) => mapSubKey(r, m.competitionId));
      // 优先使用deletedIds（新协议：客户端发送previousIds，服务器返回deletedIds）
      // 向后兼容：如果服务器返回existingIds（旧协议），则使用existingIds
      await patchFullItems(subs[0], (v.nodes as unknown[]) || [], existingIds?.nodes as number[] | undefined, deletedIds?.nodes as number[] | undefined);
      await patchFullItems(subs[1], (v.edges as unknown[]) || [], existingIds?.edges as number[] | undefined, deletedIds?.edges as number[] | undefined);
      await patchFullItems(subs[2], (v.nodeTypes as unknown[]) || [], existingIds?.nodeTypes as number[] | undefined, deletedIds?.nodeTypes as number[] | undefined);
      await patchFullItems(subs[3], (v.pathTypes as unknown[]) || [], existingIds?.pathTypes as number[] | undefined, deletedIds?.pathTypes as number[] | undefined);
      await setBaseline(m.syncKey, (v.serverTime as string) || baseline);
    }
  });
}

/**
 * 断线重连后主动对账：遍历本地已加载的全量副本，逐个发一次增量请求（带各自基线），
 * 用服务端回传的 existingIds/deletedIds 清理掉「断线 / 实时事件丢失期间」被删除的条目，
 * 无需等用户手动刷新或 5 分钟强制全量周期。仅对已有基线的集合生效（首次进入尚无
 * 副本的集合本就无脏数据，跳过）。
 *
 * C3 改造：扇出不再是无上限的 Promise.all，而是「启动抖动 + 并发上限 2 + 指数退避重试」；
 * 产业字段按基线分组走批量端点。事件派发（sync:reconciled）与失败静默语义保持不变。
 */
export async function reconcileAllIncremental(): Promise<void> {
  // 未登录（无 token）时不发起对账：避免匿名客户端轰炸服务器、产生大量 401 噪声与审计日志。
  if (!getAccountItem("token")) return;
  // 声明提升到 try 之外：finally 中需要读取本次对账涉及的集合列表，
  // 而 const [cols, maps] 若写在 try 内则对 finally 不可见（块级作用域），会导致 TS 报错且对账事件丢失集合信息。
  let cols: { collectionKey: string; resource: string; rest: string }[] = [];
  let maps: { syncKey: string; competitionId: string | number }[] = [];
  try {
    [cols, maps] = await Promise.all([listFullCollections(), listMapSyncKeys()]);

    const tasks: Array<() => Promise<unknown>> = [];
    const cfCols: { collectionKey: string; companyId: number }[] = [];
    const cfEntries: CompanyFieldReconcileEntry[] = [];

    for (const c of cols) {
      // 公司产业字段：companyId 是路径参数，不能走通用 `/<seg>` 拼法；
      // 这里只收集「集合键 + 公司 id」，基线稍后并发读出（本地 IndexedDB 读，不发网络），
      // 再交给批量对账（按基线分组，一次请求多公司）。
      if (c.resource === "companyField") {
        const m = c.rest.match(/companyId=(\d+)/);
        const cid = m ? Number(m[1]) : null;
        if (cid == null) continue;
        cfCols.push({ collectionKey: c.collectionKey, companyId: cid });
        continue;
      }
      const seg = RESOURCE_TO_SEG[c.resource];
      if (!seg || seg === "maps") continue; // 复合地图单独处理
      tasks.push(() => reconcileCollectionIncremental(c, seg));
    }

    // 复合地图：对每个已加载比赛发一次 /maps/full 增量请求
    for (const m of maps) tasks.push(() => reconcileMapIncremental(m));

    // 产业字段：并发读基线 → 一个任务内完成「批量优先 + 失败回落单点」（不占用额外并发额度）
    const cfBaselines = await Promise.all(cfCols.map((x) => getBaseline(x.collectionKey)));
    cfCols.forEach((x, i) => {
      const base = cfBaselines[i];
      if (base) cfEntries.push({ collectionKey: x.collectionKey, companyId: x.companyId, baseline: base });
    });
    if (cfEntries.length) tasks.push(() => reconcileCompanyFields(cfEntries));

    if (tasks.length) {
      // 全场抖动：Wi-Fi 恢复后各客户端在同一毫秒一起对账是尖峰的根因（简报 C3.2），
      // 先随机等待 [0, RECONCILE_START_JITTER_MS) 再扇出。
      await sleep(reconcileStartDelayMs());
      // 并发上限（默认 2）+ 每任务指数退避重试：把上千请求摊平，失败仍静默。
      await runWithConcurrency(tasks);
    }
  } catch {
    /* 忽略：对账失败不阻断主流程 */
  } finally {
    // F3 修复：对账完成后派发 sync:reconciled 事件，通知组件统一重载
    // 使用 400ms 防抖，避免一次重连触发多次组件重拉
    window.dispatchEvent(
      new CustomEvent("sync:reconciled", {
        detail: { collections: cols?.map((c) => c.collectionKey) || [] },
      }),
    );
  }
}

const cachedApi = {
  defaults: api.defaults,
  interceptors: api.interceptors,
  get: _cachedGet,
  post: (u: string, d?: unknown, c?: AxiosRequestConfig) => _mutating("post", u, d, c),
  put: (u: string, d?: unknown, c?: AxiosRequestConfig) => _mutating("put", u, d, c),
  patch: (u: string, d?: unknown, c?: AxiosRequestConfig) => _mutating("patch", u, d, c),
  delete: (u: string, c?: AxiosRequestConfig) => _mutating("delete", u, undefined, c),
} as unknown as ApiInstance;

export default cachedApi;
