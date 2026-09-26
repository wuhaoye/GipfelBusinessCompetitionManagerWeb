<template>
  <!--
    全局强制暂停遮罩：门禁处于 PAUSED / RESTORING 时铺满全屏，阻断一切交互。
    没有关闭按钮 —— 这是「强制」暂停，只能由管理员恢复或被服务端恢复事件解除。
  -->
  <transition name="gate-fade">
    <div v-if="visible" class="gate-overlay" role="alertdialog" aria-modal="true">
      <div class="gate-card">
        <div class="gate-icon" :class="iconClass">
          <el-icon v-if="isRestoring || syncing" class="is-loading"><Loading /></el-icon>
          <el-icon v-else><VideoPause /></el-icon>
        </div>

        <h2 class="gate-title">{{ title }}</h2>
        <p class="gate-message">{{ message }}</p>

        <div v-if="reason" class="gate-reason">
          <span class="label">暂停原因</span>
          <span class="value">{{ reason }}</span>
        </div>

        <div class="gate-meta">
          <div class="row">
            <span class="label">状态</span>
            <span class="value">{{ modeLabel }}</span>
          </div>
          <div v-if="state?.operatorName" class="row">
            <span class="label">操作人</span>
            <span class="value">{{ state.operatorName }}</span>
          </div>
          <div v-if="state?.since" class="row">
            <span class="label">开始时间</span>
            <span class="value">{{ formatTime(state.since) }}</span>
          </div>
          <div v-if="elapsedSeconds > 0" class="row">
            <span class="label">已持续</span>
            <span class="value">{{ elapsedText }}</span>
          </div>
          <div v-if="remainingSeconds > 0" class="row">
            <span class="label">剩余</span>
            <span class="value">{{ remainingText }}</span>
          </div>
          <div v-if="activeSnapshotId" class="row">
            <span class="label">目标快照</span>
            <span class="value">#{{ activeSnapshotId }}</span>
          </div>
          <div v-if="dataVersion" class="row">
            <span class="label">数据版本</span>
            <span class="value">v{{ dataVersion }}</span>
          </div>
        </div>

        <div v-if="progress" class="gate-progress">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>{{ progress }}</span>
        </div>

        <el-alert
          v-if="isPaused && !syncing"
          type="warning"
          :closable="false"
          show-icon
          title="所有写入已被冻结"
          description="此期间任何数据修改都会被服务端拒绝（HTTP 423），请先暂停手头操作，等待系统恢复后再继续。"
        />

        <div class="gate-actions">
          <el-button
            v-if="canResume"
            type="primary"
            :loading="busy"
            @click="handleResume"
          >
            解除暂停，恢复运行
          </el-button>
          <el-button :loading="refreshing" @click="refresh">刷新状态</el-button>
          <router-link v-if="canManage" class="gate-link" to="/snapshots" @click="goManage">
            前往快照管理
          </router-link>
        </div>

        <p class="gate-footnote">
          {{ footnote }}
        </p>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { Loading, VideoPause } from "@element-plus/icons-vue";
import { useGateStore } from "@/stores/gate";
import { useAuthStore } from "@/stores/auth";
import { formatTime } from "@/utils/format";

const gateStore = useGateStore();
const authStore = useAuthStore();
const router = useRouter();
const busy = ref(false);

const visible = computed(
  () => gateStore.blocked || (gateStore.syncing && !!gateStore.restoredInfo),
);
const syncing = computed(() => gateStore.syncing);
const isRestoring = computed(() => gateStore.isRestoring);
const isPaused = computed(() => gateStore.isPaused);
const state = computed(() => gateStore.state);
const reason = computed(() => state.value?.reason || "");
const progress = computed(() => state.value?.progress || "");
const activeSnapshotId = computed(() => gateStore.activeSnapshotId);
const dataVersion = computed(() => gateStore.dataVersion);
const elapsedSeconds = computed(() => gateStore.elapsedSeconds);
const remainingSeconds = computed(() => gateStore.remainingSeconds);
const canManage = computed(() => authStore.isSuperAdmin);
const canResume = computed(() => authStore.isSuperAdmin && isPaused.value && !syncing.value);
const refreshing = computed(() => gateStore.loading);

const title = computed(() => {
  if (syncing.value) return "数据已回退，正在同步";
  if (isRestoring.value) return "系统正在回退数据";
  return "系统已被强制暂停";
});

const message = computed(() => {
  if (syncing.value) return gateStore.resyncMessage || "正在清理本地缓存并重新加载最新数据…";
  if (isRestoring.value) {
    return (
      state.value?.message ||
      "数据正在被整体还原，期间所有读写均已冻结。回退完成后页面会自动同步，无需手动刷新。"
    );
  }
  return (
    state.value?.message ||
    "管理员已强制暂停系统，所有写入已冻结；请暂停一切操作，等待恢复。"
  );
});

const modeLabel = computed(() => {
  if (syncing.value) return "同步中";
  if (isRestoring.value) return "回退中（读写全部冻结）";
  if (isPaused.value) return "已暂停（写入冻结）";
  return "运行中";
});

const iconClass = computed(() => ({
  "is-danger": isPaused.value && !syncing.value,
  "is-warning": isRestoring.value || syncing.value,
}));

const elapsedText = computed(() => formatDuration(elapsedSeconds.value));
const remainingText = computed(() => formatDuration(remainingSeconds.value));

const footnote = computed(() => {
  if (syncing.value) return "本页面即将自动重新加载，加载完成后即可继续操作。";
  if (isRestoring.value) return "回退为整库级操作，请勿关闭页面；完成后会自动同步。";
  return "恢复运行后本遮罩会自动消失。";
});

function formatDuration(seconds: number): string {
  if (seconds <= 0) return "0 秒";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h} 小时 ${m} 分`;
  if (m > 0) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}

async function refresh() {
  await gateStore.fetchState();
}

async function handleResume() {
  busy.value = true;
  try {
    await gateStore.resume("管理员在遮罩上手动恢复");
    ElMessage.success("系统已恢复运行");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "恢复失败，请稍后重试");
  } finally {
    busy.value = false;
  }
}

function goManage() {
  void router.push("/snapshots");
}
</script>

<style scoped>
.gate-overlay {
  position: fixed;
  inset: 0;
  z-index: 3200; /* 高于普通弹窗，低于 element 的 message 提示层 */
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.62);
  backdrop-filter: blur(6px);
  user-select: none;
}
.gate-card {
  width: min(560px, 100%);
  max-height: 92vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 16px;
  padding: 32px 28px 24px;
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.35);
  text-align: center;
}
.gate-icon {
  width: 64px;
  height: 64px;
  margin: 0 auto 16px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 30px;
  color: #fff;
  background: linear-gradient(135deg, #f59e0b, #f97316);
}
.gate-icon.is-danger {
  background: linear-gradient(135deg, #ef4444, #dc2626);
}
.gate-title {
  margin: 0 0 10px;
  font-size: 21px;
  font-weight: 700;
  color: var(--color-text-primary, #1f2937);
}
.gate-message {
  margin: 0 0 18px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--color-text-secondary, #4b5563);
}
.gate-reason {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  text-align: left;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 14px;
  font-size: 13px;
}
.gate-reason .label {
  color: #b45309;
  font-weight: 600;
  white-space: nowrap;
}
.gate-reason .value {
  color: #7c2d12;
  word-break: break-all;
}
.gate-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 8px 12px;
  text-align: left;
  background: #f8fafc;
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.gate-meta .row {
  display: flex;
  gap: 6px;
  font-size: 12.5px;
}
.gate-meta .label {
  color: #94a3b8;
  white-space: nowrap;
}
.gate-meta .value {
  color: #334155;
  font-weight: 500;
  word-break: break-all;
}
.gate-progress {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 13px;
  color: #2563eb;
  background: #eff6ff;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 14px;
}
.gate-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
  align-items: center;
  margin-top: 18px;
}
.gate-link {
  font-size: 13px;
  color: var(--color-primary, #2563eb);
  text-decoration: none;
}
.gate-link:hover {
  text-decoration: underline;
}
.gate-footnote {
  margin: 16px 0 0;
  font-size: 12px;
  color: #94a3b8;
}
.gate-fade-enter-active,
.gate-fade-leave-active {
  transition: opacity 0.2s ease;
}
.gate-fade-enter-from,
.gate-fade-leave-to {
  opacity: 0;
}
</style>
