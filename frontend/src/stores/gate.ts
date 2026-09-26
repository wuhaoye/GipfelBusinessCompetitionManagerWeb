/**
 * 全局门禁 store：把 `system/gate.ts` 的叶子状态包装成业务 store。
 *
 * 职责：
 * 1. 从 `/api/snapshots/gate` 拉取权威状态（登录后、页面启动时）；
 * 2. 监听「回退完成后恢复运行」并**清空本地缓存 + 整体重载**，保证所有客户端
 *    在回退后看到的是同一代数据（这是「保证同步性」的最后一环）；
 * 3. 为遮罩组件提供暂停原因 / 进度 / 剩余时间等展示数据，以及管理员操作入口。
 */
import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";
import api from "@/api/request";
import { clearCurrentAccountCache } from "@/api/cache";
import { resetRequestMemo } from "@/api/request";
import {
  applyGateState,
  gateBlocked,
  gateBlocksReads,
  gateMode,
  gateSnapshotId,
  gateState,
  markRestored,
  pendingResync,
  resetGateState,
  resyncMessage,
  restoredInfo,
  writeLocalDataVersion,
  type GateState,
  type RestoredInfo,
} from "@/system/gate";

export const useGateStore = defineStore("systemGate", () => {
  const loading = ref(false);
  const lastError = ref("");
  /** 每秒递增的时钟，用于「已暂停 N 秒」这类展示 */
  const tick = ref(0);
  let timer: ReturnType<typeof setInterval> | null = null;

  const state = computed<GateState | null>(() => gateState.value);
  const mode = computed(() => gateMode.value);
  const blocked = computed(() => gateBlocked.value);
  const blocksReads = computed(() => gateBlocksReads.value);
  const activeSnapshotId = computed(() => gateSnapshotId.value);
  const isRestoring = computed(() => mode.value === "RESTORING");
  const isPaused = computed(() => mode.value === "PAUSED");
  const syncing = computed(() => pendingResync.value);

  /** 已暂停/回退时长（秒） */
  const elapsedSeconds = computed(() => {
    void tick.value;
    const since = state.value?.since;
    if (!since) return 0;
    const t = new Date(since).getTime();
    if (!Number.isFinite(t)) return 0;
    return Math.max(0, Math.floor((Date.now() - t) / 1000));
  });

  /** TTL 剩余秒数（0 表示不限时） */
  const remainingSeconds = computed(() => {
    void tick.value;
    const expires = state.value?.expiresAt;
    if (!expires) return 0;
    const t = new Date(expires).getTime();
    if (!Number.isFinite(t)) return 0;
    return Math.max(0, Math.floor((t - Date.now()) / 1000));
  });

  const dataVersion = computed(() => state.value?.dataVersion ?? 0);

  let resyncInFlight = false;
  let bound = false;

  function startTimer() {
    if (timer) return;
    timer = setInterval(() => {
      tick.value += 1;
    }, 1000);
  }

  function stopTimer() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  /** 拉取服务端权威门禁状态（登录后 / 页面启动 / 恢复后复核）。 */
  async function fetchState(): Promise<GateState | null> {
    loading.value = true;
    lastError.value = "";
    try {
      const data = (await api.get("/snapshots/gate", {
        cache: false,
        silent: true,
        bypassGate: true,
      })) as GateState;
      const result = applyGateState(data);
      if (result.needsResync) void resync();
      return data;
    } catch (e) {
      lastError.value = e instanceof Error ? e.message : String(e);
      return null;
    } finally {
      loading.value = false;
    }
  }

  /** 接收实时事件（由 realtime/socket.ts 通过 window 事件转发）。 */
  function onState(payload: GateState) {
    const result = applyGateState(payload);
    if (result.needsResync) void resync();
  }

  function onRestored(info: RestoredInfo) {
    markRestored(info);
    startTimer();
  }

  function onResumed(payload: GateState) {
    const result = applyGateState(payload);
    if (result.needsResync) void resync();
  }

  /**
   * 清空全部本地数据缓存并整体重载。
   * 回退是「整库级」变更，逐资源增量刷新无法覆盖（主键复用、行被删/被还原），
   * 因此这里直接作废旧缓存后重载页面，确保与服务端完全一致。
   */
  async function resync() {
    if (resyncInFlight) return;
    resyncInFlight = true;
    try {
      writeLocalDataVersion(dataVersion.value);
      resetRequestMemo();
      await clearCurrentAccountCache();
    } catch {
      /* 清缓存失败也要继续重载，避免卡在旧数据上 */
    }
    // 等一帧让遮罩渲染出「正在同步」，再重载
    setTimeout(() => {
      window.location.reload();
    }, 400);
  }

  /**
   * 绑定实时门禁事件（由 realtime/socket.ts 在收到 system:* 事件后派发的
   * window 事件转发过来）。重复调用幂等。
   */
  function bindSystemEvents() {
    if (bound) return;
    bound = true;
    window.addEventListener("system-gate", ((event: Event) => {
      const detail = (event as CustomEvent).detail as {
        kind: string;
        payload?: Record<string, unknown>;
      };
      if (!detail || !detail.kind) return;
      const payload = detail.payload as GateState | undefined;
      if (detail.kind === "state" && payload) onState(payload);
      else if (detail.kind === "paused" && payload) onState(payload);
      else if (detail.kind === "restoring" && payload) onState(payload);
      else if (detail.kind === "resumed" && payload) onResumed(payload);
      else if (detail.kind === "restored") onRestored(detail.payload as unknown as RestoredInfo);
    }) as EventListener);

    // 顶号/登出：清掉遮罩态，避免下个账号看到上一个账号的暂停提示
    window.addEventListener("auth:kicked", () => reset());
  }

  /** 管理员：强制暂停全体 */
  async function pause(options: { reason?: string; message?: string; ttlSeconds?: number } = {}) {
    const data = (await api.post(
      "/snapshots/gate/pause",
      {
        reason: options.reason ?? "管理员手动暂停",
        message: options.message ?? "",
        ttlSeconds: options.ttlSeconds ?? 0,
      },
      { bypassGate: true, cache: false },
    )) as GateState;
    applyGateState(data);
    return data;
  }

  /** 管理员：恢复运行 */
  async function resume(reason = "管理员手动恢复") {
    const data = (await api.post(
      "/snapshots/gate/resume",
      { reason },
      { bypassGate: true, cache: false },
    )) as GateState;
    applyGateState(data);
    return data;
  }

  /** 登出/切换账号时清空本地门禁态 */
  function reset() {
    resetGateState();
    stopTimer();
  }

  // 进入非 RUNNING 时开始计时（用于展示已暂停时长）
  watch(mode, (now) => {
    if (now === "RUNNING") stopTimer();
    else startTimer();
  });

  // 任何来源（实时事件 / 423 响应 / 主动拉取）发现「本地数据版本落后」都要触发重载
  watch(pendingResync, (needs) => {
    if (needs) void resync();
  });

  return {
    // 状态
    state,
    mode,
    blocked,
    blocksReads,
    activeSnapshotId,
    isPaused,
    isRestoring,
    syncing,
    loading,
    lastError,
    dataVersion,
    elapsedSeconds,
    remainingSeconds,
    restoredInfo,
    resyncMessage,
    // 动作
    fetchState,
    onState,
    onRestored,
    onResumed,
    resync,
    pause,
    resume,
    reset,
    bindSystemEvents,
  };
});
