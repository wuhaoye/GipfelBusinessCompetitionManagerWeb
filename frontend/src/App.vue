<template>
  <router-view />
  <AnnouncementDialog />
  <VersionUpdateDialog />
  <MessageToastHost />
  <!-- 全局强制暂停 / 回退遮罩：门禁非 RUNNING 时铺满全屏，阻断一切交互 -->
  <SystemGateOverlay />
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";
import AnnouncementDialog from "@/components/AnnouncementDialog.vue";
import VersionUpdateDialog from "@/components/VersionUpdateDialog.vue";
import MessageToastHost from "@/components/MessageToastHost.vue";
import SystemGateOverlay from "@/components/system/SystemGateOverlay.vue";
import { useVersionStore } from "@/stores/version";
import { useGateStore } from "@/stores/gate";
import { getSocketInstance } from "@/realtime/socket";

const versionStore = useVersionStore();
const gateStore = useGateStore();

// 周期复核定时器：应对运行中服务端升级导致版本不一致，或版本恢复一致后自动解锁。
let recheckTimer: ReturnType<typeof setInterval> | null = null;
// 门禁状态兜底轮询：实时通道未连上（未选比赛 / 断网）时也不会漏掉「强制暂停」。
let gateTimer: ReturnType<typeof setInterval> | null = null;

/** 页面重新可见时立刻复核门禁：用户切走再回来是最容易错过暂停广播的时刻。 */
function onVisibilityChange() {
  if (document.visibilityState === "visible") void gateStore.fetchState();
}

onMounted(async () => {
  // 应用启动先校验版本：若版本不一致则硬封锁并弹提示。
  await versionStore.checkVersion();
  // 每 5 分钟复核一次版本一致性（校验请求自带 bypassVersionBlock，不受封锁影响）。
  recheckTimer = setInterval(() => {
    versionStore.checkVersion();
  }, 5 * 60 * 1000);

  // 门禁：绑定实时事件 + 拉取一次权威状态（Socket 握手也会下发，两者互为兜底）。
  gateStore.bindSystemEvents();
  await gateStore.fetchState();
  // 兜底轮询：仅在实时通道未连上时轮询（未选比赛 / 断网 / 被顶号），
  // 每 20 秒一次；该端点只返回门禁状态，体积很小。实时通道正常时完全由 system:* 事件驱动。
  gateTimer = setInterval(() => {
    const sock = getSocketInstance();
    if (sock && sock.connected) return;
    void gateStore.fetchState();
  }, 20 * 1000);
  document.addEventListener("visibilitychange", onVisibilityChange);
});

onUnmounted(() => {
  if (recheckTimer) clearInterval(recheckTimer);
  if (gateTimer) clearInterval(gateTimer);
  document.removeEventListener("visibilitychange", onVisibilityChange);
});
</script>
