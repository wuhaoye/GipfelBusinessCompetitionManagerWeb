/**
 * 全局门禁（快照 / 强制暂停 / 回退）客户端状态 —— 叶子模块。
 *
 * 为什么不放在 Pinia store 里：`api/request.ts` 的请求拦截器需要在**不引入 Pinia**、
 * 也不产生循环依赖的前提下判断「现在能不能发写请求」。参照 `config/index.ts` 的
 * `versionBlocked` 做法，这里只暴露响应式 ref / computed 与纯函数，
 * 由 `stores/gate.ts` 包装成业务 store，由 `realtime/socket.ts` 直接写入。
 *
 * 事件契约（后端 apps/realtime/emit.py）：
 *   system:state     全量状态（Socket 握手 / 状态变更时下发）
 *   system:paused    强制暂停（写请求全部被冻结）
 *   system:restoring 回退中（读写全部被冻结）
 *   system:resumed   恢复运行（客户端比对 dataVersion 决定是否整体重载）
 *   system:restored  回退完成（携带快照信息与新数据版本）
 *   system:progress  进度文本
 */
import { computed, ref } from "vue";

export type GateMode = "RUNNING" | "PAUSED" | "RESTORING";

export interface GateState {
  mode: GateMode;
  reason: string;
  message: string;
  operatorId: number | null;
  operatorName: string;
  since: string | null;
  ttlSeconds: number;
  expiresAt: string | null;
  activeSnapshotId: number | null;
  progress: string;
  dataVersion: number;
  updatedAt?: string | null;
  seq?: number;
  ts?: number;
}

export interface RestoredInfo {
  snapshotId: number;
  label?: string;
  scope?: string;
  competitionId?: number | null;
  deletedRows?: number;
  insertedRows?: number;
  durationMs?: number;
  dataVersion?: number;
  safetySnapshotId?: number | null;
  operatorName?: string;
  files?: { files?: number; bytes?: number; problems?: string[] };
}

/** 本地记录的数据版本号 key（与服务端 SystemGate.data_version 对应） */
const DATA_VERSION_KEY = "gipfel:dataVersion";
/** 与服务端断开时的兜底状态 */
const UNKNOWN_STATE: GateState = {
  mode: "RUNNING",
  reason: "",
  message: "",
  operatorId: null,
  operatorName: "",
  since: null,
  ttlSeconds: 0,
  expiresAt: null,
  activeSnapshotId: null,
  progress: "",
  dataVersion: 0,
};

/** 当前门禁状态（null 表示尚未从服务端获取过） */
export const gateState = ref<GateState | null>(null);
/** 最近一次「回退完成」事件 */
export const restoredInfo = ref<RestoredInfo | null>(null);
/** 回退完成后等待整体重载（用于展示「正在同步…」） */
export const pendingResync = ref(false);
/** 服务端要求客户端重载但尚未完成时的提示文本 */
export const resyncMessage = ref("");

export const gateMode = computed<GateMode>(() => gateState.value?.mode ?? "RUNNING");
export const gateRunning = computed(() => gateMode.value === "RUNNING");
/** 处于暂停/回退中：写请求一律拦截 */
export const gateBlocked = computed(() => gateMode.value !== "RUNNING");
/** 回退中：连读请求也要拦截（库内容正在被整体替换） */
export const gateBlocksReads = computed(() => gateMode.value === "RESTORING");
export const gateSnapshotId = computed(() => gateState.value?.activeSnapshotId ?? null);

export function readLocalDataVersion(): number {
  try {
    const raw = localStorage.getItem(DATA_VERSION_KEY);
    const n = raw === null ? NaN : Number(raw);
    return Number.isFinite(n) ? n : -1;
  } catch {
    return -1;
  }
}

export function writeLocalDataVersion(version: number): void {
  try {
    localStorage.setItem(DATA_VERSION_KEY, String(version ?? 0));
  } catch {
    /* localStorage 不可用时忽略 */
  }
}

export interface ApplyResult {
  /** 状态是否发生变化 */
  changed: boolean;
  /** 模式是否发生变化（RUNNING ↔ 暂停/回退） */
  modeChanged: boolean;
  /** dataVersion 是否变化 */
  versionChanged: boolean;
  /** 是否是「恢复运行且数据版本变化」—— 需要清缓存并整体重载 */
  needsResync: boolean;
}

/** 把服务端下发的门禁状态写入本地（幂等）。 */
export function applyGateState(payload: Partial<GateState> | null | undefined): ApplyResult {
  if (!payload || typeof payload.mode !== "string") {
    return { changed: false, modeChanged: false, versionChanged: false, needsResync: false };
  }
  const next: GateState = { ...UNKNOWN_STATE, ...payload } as GateState;
  const prev = gateState.value;
  const prevMode = prev?.mode ?? "RUNNING";
  const prevVersion = prev?.dataVersion ?? 0;
  const modeChanged = prevMode !== next.mode;
  const versionChanged = prevVersion !== next.dataVersion;
  const changed =
    modeChanged ||
    versionChanged ||
    prev?.reason !== next.reason ||
    prev?.progress !== next.progress ||
    prev?.message !== next.message;

  gateState.value = next;

  // 服务端数据版本与本地记录不一致：说明发生过回退，本地缓存必须作废
  const localVersion = readLocalDataVersion();
  const needsResync = next.mode === "RUNNING" && localVersion >= 0 && localVersion !== next.dataVersion;
  if (needsResync) {
    pendingResync.value = true;
    resyncMessage.value = "数据已回退到历史版本，正在清理本地缓存并重新同步…";
  }
  if (next.mode === "RUNNING" && !needsResync && localVersion < 0) {
    // 首次访问：仅记录基线，不做重载
    writeLocalDataVersion(next.dataVersion);
  }
  return { changed, modeChanged, versionChanged, needsResync };
}

/** 记录「回退完成」事件（此时客户端通常仍处于遮罩态）。 */
export function markRestored(info: RestoredInfo | null | undefined): void {
  if (!info || typeof info.snapshotId !== "number") return;
  restoredInfo.value = info;
  pendingResync.value = true;
  resyncMessage.value = `快照 #${info.snapshotId} 已回退完成，正在重新同步数据…`;
}

/** 客户端侧请求拦截判定：返回 null 表示放行，否则返回拒绝原因。 */
export function requestBlockReason(method: string): string | null {
  if (!gateBlocked.value) return null;
  const upper = (method || "GET").toUpperCase();
  const isRead = upper === "GET" || upper === "HEAD" || upper === "OPTIONS";
  if (gateBlocksReads.value) {
    return "系统正在回退数据，期间禁止访问；请稍候，回退完成后页面会自动同步";
  }
  if (!isRead) {
    const reason = gateState.value?.reason ? `（${gateState.value.reason}）` : "";
    return `系统已被管理员强制暂停${reason}，所有写入已冻结，请等待恢复后再操作`;
  }
  return null;
}

/** 重置本地门禁状态（登出时调用，避免下一个账号继承上一个账号的遮罩态）。 */
export function resetGateState(): void {
  gateState.value = null;
  restoredInfo.value = null;
  pendingResync.value = false;
  resyncMessage.value = "";
}
