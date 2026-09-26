import { io, type Socket } from "socket.io-client";
import { getApiBaseUrl, versionBlocked } from "@/config";
import { getAccountItem } from "@/utils/accountStorage";
import { logger } from "@/utils/logger";
import { applyGateState, markRestored, type GateState } from "@/system/gate";

let socket: Socket | null = null;
// 记录当前 socket 连接所用的 baseUrl；serverUrl 变更后用于检测并重建单例。
let connectedBaseUrl: string | null = null;
// 断线自动重连成功后触发的回调（由业务层注册，用于重订阅房间 + 回源刷新）。
let reconnectHandler: (() => void) | null = null;
// 记录最后一个接收到的事件序号，用于重连后补发遗漏事件
let lastReceivedSeq: number = 0;

function getBaseUrl(): string {
  return getApiBaseUrl();
}

function getToken(): string | null {
  return getAccountItem("token");
}

/** 建立（或复用）实时连接。无 token 时不连接。返回 socket 或 null。
 *  若 serverUrl（baseUrl）已变更，旧单例仍指向旧服务器，这里会先断开再用新地址重建，
 *  使 WebSocket 通道与 HTTP 通道（拦截器每个请求读 localStorage）在改地址后保持一致。
 *  版本硬封锁期间禁止建连 / 维持实时通道。 */
export function connectRealtime(): Socket | null {
  // 版本封锁：禁止建立或维持实时连接，确保锁定期间无任何后台通道。
  if (versionBlocked.value) {
    disconnectRealtime();
    return null;
  }
  // 单例：只要实例存在（连接中/已连/断开）都复用，避免重复创建
  if (socket) {
    if (connectedBaseUrl && connectedBaseUrl !== getBaseUrl()) {
      socket.disconnect();
      socket = null;
    } else {
      return socket;
    }
  }
  const token = getToken();
  if (!token) return null;
  connectedBaseUrl = getBaseUrl();
  socket = io(connectedBaseUrl, {
    auth: { token },
    // 仅用 websocket 传输：跳过 socket.io 默认「先轮询再升级」的长轮询链路。
    // 长轮询连接在升级时被中断会产生 ECONNRESET 噪声；且经 Vite 开发代理转发时，
    // 单一 websocket 通道比 polling+upgrade 更稳定。生产同源部署同样支持 websocket。
    transports: ["websocket"],
    reconnection: true,
    reconnectionAttempts: Infinity,
    // ---------- 重连退避 + 抖动（C3.3）----------
    // socket.io 内建指数退避：第 n 次重连等待 =
    //   min(reconnectionDelay × 2^n, reconnectionDelayMax) × random(1 ± randomizationFactor)。
    // 三个参数**显式写出**（取值与 socket.io-client 默认一致），一是避免库默认值漂移后
    // 全场客户端在同一毫秒重连，二是让「退避 + 抖动」在本仓库可被核对
    // （见 tests/fix_verify/frontend/test_c3_heartbeat_backoff.mjs 的静态断言）。
    // 注：真正会把后端打满的扇出发生在「重连成功后的对账请求」，其并发上限（2）与
    // 启动抖动 + 指数退避重试在 api/request.ts::reconcileAllIncremental 中实现。
    reconnectionDelay: 2000,
    reconnectionDelayMax: 5000,
    randomizationFactor: 0.5,
  });
  // ---------- 连接错误处理：检测认证失败（被顶号）并立即触发登出 ----------
  // 当设备 B 登录顶掉设备 A 后，设备 A 的 socket 断连后会尝试用旧 token 重连。
  // 服务端检测到 tokenVersion 不匹配后抛出 ConnectionRefusedError，携带明确标识
  // "auth_required"。客户端据此立即触发 auth:kicked → logout，而非无限重试。
  socket.on("connect_error", (err: Error & { data?: { reason?: string } }) => {
    const msg = err?.message || "";
    // 服务端 ConnectionRefusedError({"message":"auth_required","reason":"token_version_mismatch"})
    // 的 message 字段为 "auth_required"
    if (msg === "auth_required") {
      logger.warn("[Realtime] 被顶号/认证失败，触发登出");
      // 立即停止重连：避免 socket.io 在 logout/disconnect 之前再发起一次连接
      socket?.disconnect();
      window.dispatchEvent(new CustomEvent("auth:kicked"));
      return;
    }
    logger.error("[Realtime] 连接失败:", msg);
  });
  // ---------- 顶号事件：注册时机早于 resource-changed.ts 的 bindResourceChanged ----------
  // bindResourceChanged 在 competition store 选择比赛后才调用，存在竞态窗口：
  // socket 已连接 → 服务端立即广播 auth:required → 但 handler 尚未注册 → 事件丢失。
  // 此处在 socket 创建时即注册，保证任何阶段都能即时响应顶号。
  socket.on("auth:required", (payload: { reason?: string }) => {
    if (payload && payload.reason === "token_version_mismatch") {
      window.dispatchEvent(new CustomEvent("auth:kicked"));
    }
  });
  // ---------- 系统门禁事件：强制暂停 / 回退中 / 已恢复 / 回退完成 ----------
  // 与 auth:required 同理，必须在 socket 创建时立即注册 —— 否则在 store 初始化前
  // 到达的「强制暂停」会丢失，用户会在暂停期间继续操作。
  // 这里只写入叶子状态（请求拦截器据此冻结写请求）并派发 window 事件，
  // 由 stores/gate.ts 负责后续的缓存清理与整体重载。
  const dispatchGate = (kind: string, payload?: unknown) => {
    window.dispatchEvent(new CustomEvent("system-gate", { detail: { kind, payload } }));
  };
  socket.on("system:state", (payload: GateState) => {
    applyGateState(payload);
    dispatchGate("state", payload);
  });
  socket.on("system:paused", (payload: GateState) => {
    applyGateState(payload);
    dispatchGate("paused", payload);
    logger.warn("[Realtime] 系统已强制暂停：", payload?.reason || "");
  });
  socket.on("system:restoring", (payload: GateState) => {
    applyGateState(payload);
    dispatchGate("restoring", payload);
  });
  socket.on("system:progress", (payload: GateState) => {
    applyGateState(payload);
  });
  socket.on("system:resumed", (payload: GateState) => {
    applyGateState(payload);
    dispatchGate("resumed", payload);
  });
  socket.on("system:restored", (payload: Record<string, unknown>) => {
    markRestored(payload as never);
    dispatchGate("restored", payload);
  });
  // 断线自动重连成功（仅 reconnection，不含首次 connect）：通知业务层重订阅房间 + 回源刷新。
  // 注意：遗漏事件的补发统一由 resource-changed.ts 在 "connect" 事件（含重连后的 connect）中发起，
  // 此处不再重复发 sync:replay，避免与 connect 处理重复补发导致事件被处理两遍。
  socket.io.on("reconnect", () => {
    reconnectHandler?.();
  });
  return socket;
}

export function disconnectRealtime() {
  if (socket) {
    socket.disconnect();
    socket = null;
  }
}

/** 订阅某比赛的实时房间（仅同比赛客户端收到广播） */
export function subscribeCompetition(competitionId: number) {
  if (!socket) connectRealtime();
  socket?.emit("subscribe", { competitionId });
}

export function unsubscribeCompetition(competitionId: number) {
  socket?.emit("unsubscribe", { competitionId });
}

/** 获取当前 socket 实例引用（供外部判断 socket 是否已重建）。 */
export function getSocketInstance(): Socket | null {
  return socket;
}

/** 注册实时事件监听（连接前注册同样有效，socket.io 内部会缓冲） */
export function onRealtime(event: string, handler: (payload: any) => void) {
  if (!socket) connectRealtime();
  socket?.on(event, handler);
}

export function offRealtime(event: string, handler?: (payload: any) => void) {
  socket?.off(event, handler as any);
}

/** 注册「断线自动重连成功」回调（仅 reconnection 触发，不含首次连接）。 */
export function onReconnect(handler: () => void) {
  reconnectHandler = handler;
}

/** 更新最后一个接收到的事件序号（用于重连后补发遗漏事件） */
export function updateLastReceivedSeq(seq: number) {
  if (seq > lastReceivedSeq) {
    lastReceivedSeq = seq;
  }
}
