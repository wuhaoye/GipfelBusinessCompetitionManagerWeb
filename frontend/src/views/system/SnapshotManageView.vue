<template>
  <div class="snapshot-view">
    <div class="mm-toolbar">
      <h2 class="mm-title">快照与回退</h2>
      <div class="mm-actions">
        <el-tag :type="modeTagType" effect="dark" size="large">{{ modeLabel }}</el-tag>
        <el-button :icon="Refresh" :loading="loading" @click="reloadAll">刷新</el-button>
      </div>
    </div>

    <!-- ============ ① 全局门禁（强制暂停） ============ -->
    <el-card shadow="never" class="block-card">
      <template #header>
        <div class="card-head">
          <span class="card-title">全局门禁（强制暂停所有人）</span>
          <span class="hint">
            暂停后所有用户的写入会被服务端拒绝（HTTP 423），在线客户端立即弹出全屏遮罩
          </span>
        </div>
      </template>

      <el-descriptions :column="isPhone ? 1 : 4" size="small" border>
        <el-descriptions-item label="当前状态">
          <el-tag :type="modeTagType" size="small">{{ modeLabel }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="暂停原因">
          {{ gate.reason || "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="操作人">{{ gate.operatorName || "-" }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">
          {{ gate.since ? fmtTime(gate.since) : "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="数据版本">v{{ gate.dataVersion }}</el-descriptions-item>
        <el-descriptions-item label="在途写请求">
          {{ status?.writers?.active ?? 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="目标快照">
          {{ gate.activeSnapshotId ? `#${gate.activeSnapshotId}` : "-" }}
        </el-descriptions-item>
        <el-descriptions-item label="预计恢复">
          {{ gate.expiresAt ? fmtTime(gate.expiresAt) : "需手动恢复" }}
        </el-descriptions-item>
      </el-descriptions>

      <div v-if="gate.progress" class="progress-line">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>{{ gate.progress }}</span>
      </div>

      <div class="gate-actions">
        <el-button
          v-if="canRestore"
          type="danger"
          :disabled="gate.mode !== 'RUNNING'"
          @click="pauseDialogVisible = true"
        >
          强制暂停全体
        </el-button>
        <el-button
          v-if="canRestore"
          type="primary"
          :disabled="gate.mode === 'RUNNING'"
          :loading="acting"
          @click="handleResume"
        >
          解除暂停，恢复运行
        </el-button>
        <span class="hint">
          已注册数据表 {{ status?.registryTables ?? "-" }} 张；归档目录
          {{ status?.storagePath || "-" }}（占用 {{ formatBytes(status?.storageBytes) }}）
        </span>
      </div>
    </el-card>

    <!-- ============ ② 创建快照 ============ -->
    <el-card shadow="never" class="block-card">
      <template #header>
        <div class="card-head">
          <span class="card-title">创建快照（记录当前全部数据）</span>
          <span class="hint">逐表原样落盘 + sha256 指纹，可用于后续一键回退</span>
        </div>
      </template>

      <el-form :model="createForm" label-width="96px" class="create-form">
        <el-form-item label="快照范围">
          <el-radio-group v-model="createForm.scope">
            <el-radio-button label="competition">当前比赛</el-radio-button>
            <el-radio-button label="system">全系统（所有比赛）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="createForm.scope === 'competition'" label="比赛">
          <el-select
            v-model="createForm.competitionId"
            placeholder="选择比赛"
            style="width: 260px"
          >
            <el-option
              v-for="c in competitions"
              :key="c.id"
              :label="`${c.name}（#${c.id}）`"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="名称">
          <el-input
            v-model="createForm.label"
            placeholder="留空自动取时间，如「第 3 财年初基线」"
            maxlength="128"
            style="width: 320px"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createForm.note"
            placeholder="可选，例如：财年推进前的完整基线"
            maxlength="200"
            style="width: 420px"
          />
        </el-form-item>
        <el-form-item label="上传文件">
          <el-switch v-model="createForm.includeFiles" />
          <span class="hint inline">一并归档 uploads 下的地图背景图等文件</span>
        </el-form-item>
        <el-form-item label="一致性">
          <el-checkbox v-model="createForm.pauseFirst">
            先强制暂停再快照（会短暂冻结所有写入，得到严格静止点）
          </el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="creating" @click="handleCreate">
            创建快照
          </el-button>
          <span v-if="creating" class="hint inline">{{ createProgress || "正在创建…" }}</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- ============ ③ 快照列表 ============ -->
    <el-card shadow="never" class="block-card">
      <template #header>
        <div class="card-head">
          <span class="card-title">快照列表（共 {{ total }} 份）</span>
          <div class="filters">
            <el-select
              v-model="filters.status"
              placeholder="状态"
              clearable
              style="width: 120px"
              @change="reload"
            >
              <el-option label="可用" value="ready" />
              <el-option label="创建中" value="building" />
              <el-option label="失败" value="failed" />
              <el-option label="已回退过" value="restored" />
            </el-select>
            <el-select
              v-model="filters.kind"
              placeholder="类型"
              clearable
              style="width: 130px"
              @change="reload"
            >
              <el-option label="手动" value="manual" />
              <el-option label="自动" value="auto" />
              <el-option label="回退前安全快照" value="pre-restore" />
            </el-select>
            <el-input
              v-model="filters.q"
              placeholder="名称 / 备注"
              clearable
              style="width: 180px"
              @change="reload"
            />
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="items" size="small" stripe>
        <el-table-column label="ID" width="64" prop="id" />
        <el-table-column label="名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="snap-label">{{ row.label }}</span>
            <el-tag v-if="row.locked" size="small" type="warning" effect="plain" class="ml4">
              已锁定
            </el-tag>
            <div v-if="row.note" class="snap-note">{{ row.note }}</div>
          </template>
        </el-table-column>
        <el-table-column label="范围" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag size="small" :type="row.scope === 'system' ? 'danger' : 'success'" effect="plain">
              {{ row.scope === "system" ? "全系统" : row.competitionName || "比赛" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">{{ kindLabel(row.kind) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tooltip :disabled="!row.error" :content="row.error" placement="top">
              <el-tag size="small" :type="statusTagType(row.status)">
                {{ statusLabel(row.status) }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="数据量" width="140">
          <template #default="{ row }">
            {{ row.rowCount.toLocaleString() }} 行 / {{ formatBytes(row.byteSize) }}
          </template>
        </el-table-column>
        <el-table-column label="创建" width="180">
          <template #default="{ row }">
            <div>{{ fmtTime(row.createdAt) }}</div>
            <div class="snap-note">{{ row.createdByName || "-" }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="330" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="showDetail(row)">详情</el-button>
            <el-button
              v-if="canRestore"
              size="small"
              text
              type="danger"
              :disabled="row.status === 'failed' || row.status === 'building'"
              @click="openRestore(row)"
            >
              回退
            </el-button>
            <el-button size="small" text @click="handleVerify(row)">校验</el-button>
            <el-button size="small" text @click="handleDownload(row)">下载</el-button>
            <el-button v-if="canManage" size="small" text @click="handleLock(row)">
              {{ row.locked ? "解锁" : "锁定" }}
            </el-button>
            <el-button
              v-if="canManage"
              size="small"
              text
              type="danger"
              @click="handleDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next, total"
          background
          @current-change="load"
        />
      </div>
    </el-card>

    <!-- ============ ④ 保留策略 ============ -->
    <el-card shadow="never" class="block-card">
      <template #header>
        <div class="card-head">
          <span class="card-title">自动快照与保留策略</span>
          <span class="hint">
            自动快照由 <code>manage.py snapshot_auto</code> 触发（建议 cron / systemd timer）
          </span>
        </div>
      </template>
      <el-form :model="policy" label-width="130px" class="create-form">
        <el-form-item label="启用自动快照">
          <el-switch v-model="policy.autoEnabled" />
        </el-form-item>
        <el-form-item label="间隔（分钟）">
          <el-input-number v-model="policy.autoIntervalMinutes" :min="1" :max="10080" />
        </el-form-item>
        <el-form-item label="自动快照范围">
          <el-radio-group v-model="policy.autoScope">
            <el-radio-button label="system">全系统</el-radio-button>
            <el-radio-button label="competition">单个比赛</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="保留份数">
          <el-input-number v-model="policy.keepLast" :min="0" :max="1000" />
          <span class="hint inline">0 = 不按份数清理</span>
        </el-form-item>
        <el-form-item label="保留天数">
          <el-input-number v-model="policy.keepDays" :min="0" :max="3650" />
          <span class="hint inline">0 = 不按天数清理（锁定的快照永不被清理）</span>
        </el-form-item>
        <el-form-item label="自动快照含文件">
          <el-switch v-model="policy.autoIncludeFiles" />
        </el-form-item>
        <el-form-item>
          <el-button v-if="canManage" type="primary" :loading="savingPolicy" @click="savePolicy">
            保存策略
          </el-button>
          <el-button v-if="canManage" @click="handleCleanup(true)">预览清理</el-button>
          <el-button v-if="canManage" type="danger" plain @click="handleCleanup(false)">
            立即清理
          </el-button>
          <span v-if="policy.lastAutoAt" class="hint inline">
            上次自动快照：{{ fmtTime(policy.lastAutoAt) }}
          </span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- ============ ⑤ 回退确认与影响预览 ============ -->
    <el-dialog
      v-model="restoreVisible"
      title="强制回退数据（会暂停所有人的工作）"
      width="min(880px, 94vw)"
      :close-on-click-modal="false"
    >
      <template v-if="restoreTarget">
        <el-alert
          type="error"
          :closable="false"
          show-icon
          title="回退会覆盖当前数据，且过程不可中断"
          description="开始后：① 系统立即进入「回退中」，所有用户被强制暂停并弹出遮罩；② 先自动创建一份「回退前安全快照」（可再回退回来）；③ 事务内整表还原并逐表校验 sha256，任何不一致都会整体回滚；④ 完成后广播新数据版本，所有客户端自动清缓存重载。"
        />
        <el-descriptions :column="isPhone ? 1 : 2" size="small" border class="mt12">
          <el-descriptions-item label="目标快照">
            #{{ restoreTarget.id }} {{ restoreTarget.label }}
          </el-descriptions-item>
          <el-descriptions-item label="范围">{{ restoreTarget.scopeLabel }}</el-descriptions-item>
          <el-descriptions-item label="快照数据量">
            {{ restoreTarget.rowCount.toLocaleString() }} 行 / {{ formatBytes(restoreTarget.byteSize) }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{ fmtTime(restoreTarget.createdAt) }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="diff-block">
          <div class="diff-head">
            <span>回退影响预览</span>
            <el-button size="small" text :loading="diffLoading" @click="loadDiff">刷新预览</el-button>
          </div>
          <div v-if="diff" class="diff-totals">
            将删除 <b>{{ diff.totals.deleteRows.toLocaleString() }}</b> 行、写回
            <b>{{ diff.totals.insertRows.toLocaleString() }}</b> 行，涉及
            <b>{{ diff.totals.affectedTables }}</b> 张表（{{ diff.totals.skippedTables }} 张按策略跳过）
          </div>
          <el-table
            v-if="diff"
            :data="diffRows"
            size="small"
            max-height="280"
            :row-class-name="diffRowClass"
          >
            <el-table-column prop="table" label="数据表" min-width="180" show-overflow-tooltip />
            <el-table-column label="策略" width="180">
              <template #default="{ row }">{{ row.note || row.policy }}</template>
            </el-table-column>
            <el-table-column label="当前" width="80" align="right" prop="currentRows" />
            <el-table-column label="快照" width="80" align="right" prop="snapshotRows" />
            <el-table-column label="删除" width="80" align="right" prop="deleteRows" />
            <el-table-column label="写回" width="80" align="right" prop="insertRows" />
          </el-table>
        </div>

        <el-form label-width="120px" class="mt12">
          <el-form-item label="回退原因">
            <el-input
              v-model="restoreForm.reason"
              placeholder="展示给全体用户的暂停原因"
              maxlength="200"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="高级选项">
            <div class="adv-options">
              <el-checkbox v-model="restoreForm.skipSafetySnapshot">
                跳过「回退前安全快照」（更快，但无法撤销本次回退）
              </el-checkbox>
              <el-checkbox v-model="restoreForm.includeUsers">
                同时回写账号数据（不会删除账号，也不会回滚 token 版本）
              </el-checkbox>
              <el-checkbox v-model="restoreForm.restoreGlobal" :disabled="restoreTarget.scope !== 'system'">
                同时回写全局表（产业类型 / 合同类型等；仅全系统快照有意义）
              </el-checkbox>
              <el-checkbox
                v-model="restoreForm.restoreFiles"
                :disabled="!restoreTarget.includeFiles"
              >
                同时还原上传文件{{ restoreTarget.includeFiles ? "" : "（该快照未归档文件）" }}
              </el-checkbox>
              <el-checkbox v-model="restoreForm.verify">回退后做 sha256 内容校验（建议保留）</el-checkbox>
            </div>
          </el-form-item>
          <el-form-item label="确认回退">
            <el-input
              v-model="restoreForm.confirmText"
              :placeholder="`请输入快照编号 ${restoreTarget.id} 或「回退」`"
              style="width: 260px"
            />
            <span class="hint inline">防止误触</span>
          </el-form-item>
        </el-form>

        <div v-if="restoreResult" class="restore-result">
          <el-alert
            type="success"
            :closable="false"
            show-icon
            :title="`回退完成：删除 ${restoreResult.deletedRows} 行 / 写回 ${restoreResult.insertedRows} 行`"
            :description="`耗时 ${restoreResult.durationMs} ms，数据版本已更新为 v${restoreResult.dataVersion}。${
              restoreResult.safetySnapshotId
                ? `回退前安全快照：#${restoreResult.safetySnapshotId}（如需撤销本次回退可回退到它）`
                : ''
            }`"
          />
        </div>
      </template>

      <template #footer>
        <el-button @click="restoreVisible = false">取消</el-button>
        <el-button
          type="danger"
          :loading="restoring"
          :disabled="!restoreTarget || restoreForm.confirmText.trim() === ''"
          @click="handleRestore"
        >
          {{ restoring ? restoreStage || "正在回退…" : "确认强制回退" }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ============ ⑦ 强制暂停对话框 ============ -->
    <el-dialog
      v-model="pauseDialogVisible"
      title="强制暂停全体用户"
      width="min(520px, 92vw)"
      :close-on-click-modal="false"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="暂停期间所有用户的写入都会被拒绝"
        description="在线客户端会立刻弹出全屏遮罩并停止操作；读请求仍可继续。建议在需要人工修数/核对账目时使用，完成后点击「解除暂停」。"
      />
      <el-form :model="pauseForm" label-width="90px" class="mt12">
        <el-form-item label="暂停原因">
          <el-input v-model="pauseForm.reason" maxlength="120" placeholder="如：数据校验 / 人工修数" />
        </el-form-item>
        <el-form-item label="提示文案">
          <el-input
            v-model="pauseForm.message"
            type="textarea"
            :rows="2"
            maxlength="200"
            placeholder="展示给全体用户的说明（可选）"
          />
        </el-form-item>
        <el-form-item label="自动恢复">
          <el-input-number v-model="pauseForm.ttlSeconds" :min="0" :max="86400" :step="60" />
          <span class="hint inline">秒；0 = 不自动恢复，需手动解除</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pauseDialogVisible = false">取消</el-button>
        <el-button type="danger" :loading="acting" @click="handlePause">确认强制暂停</el-button>
      </template>
    </el-dialog>

    <!-- ============ ⑥ 快照详情 ============ -->
    <el-drawer v-model="detailVisible" title="快照详情" size="min(760px, 94vw)">
      <template v-if="detail">
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="编号">#{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="名称">{{ detail.label }}</el-descriptions-item>
          <el-descriptions-item label="备注">{{ detail.note || "-" }}</el-descriptions-item>
          <el-descriptions-item label="范围">{{ detail.scopeLabel }}</el-descriptions-item>
          <el-descriptions-item label="创建">
            {{ fmtTime(detail.createdAt) }} / {{ detail.createdByName || "-" }}
          </el-descriptions-item>
          <el-descriptions-item label="数据量">
            {{ detail.tableCount }} 张表 / {{ detail.rowCount.toLocaleString() }} 行 /
            {{ formatBytes(detail.byteSize) }}
          </el-descriptions-item>
          <el-descriptions-item label="上传文件">
            {{ detail.includeFiles ? `${detail.fileCount} 个 / ${formatBytes(detail.fileByteSize)}` : "未归档" }}
          </el-descriptions-item>
          <el-descriptions-item label="数据版本">
            v{{ detail.dataVersion }}（服务端事件序号 {{ detail.serverSeq }}）
          </el-descriptions-item>
          <el-descriptions-item label="归档目录">
            <code class="path">{{ detail.storagePath || "-" }}</code>
          </el-descriptions-item>
          <el-descriptions-item label="回退记录">
            已回退 {{ detail.restoreCount }} 次
            <span v-if="detail.restoredAt">，最近 {{ fmtTime(detail.restoredAt) }} / {{ detail.lastRestoredByName }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="detail.error" label="提示/错误">
            <pre class="detail-pre">{{ detail.error }}</pre>
          </el-descriptions-item>
        </el-descriptions>

        <div class="detail-block">
          <div class="detail-label">逐表清单（{{ detail.tables.length }} 张）</div>
          <el-table :data="detail.tables" size="small" max-height="420">
            <el-table-column prop="table" label="数据表" min-width="170" show-overflow-tooltip />
            <el-table-column prop="modelName" label="模型" width="150" show-overflow-tooltip />
            <el-table-column label="回退策略" width="150">
              <template #default="{ row }">{{ policyLabel(row.policy) }}</template>
            </el-table-column>
            <el-table-column label="行数" width="90" align="right">
              <template #default="{ row }">{{ row.rows.toLocaleString() }}</template>
            </el-table-column>
            <el-table-column label="大小" width="90" align="right">
              <template #default="{ row }">{{ formatBytes(row.bytes) }}</template>
            </el-table-column>
            <el-table-column label="sha256" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">
                <code class="hash">{{ (row.sha256 || "").slice(0, 16) }}…</code>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Loading, Refresh } from "@element-plus/icons-vue";
import {
  snapshotsApi,
  type GatePayload,
  type SnapshotDiff,
  type SnapshotDiffEntry,
  type SnapshotDetail,
  type SnapshotPolicyPayload,
  type SnapshotRestoreResult,
  type SnapshotSummary,
} from "@/api";
import api from "@/api/request";
import { useAuthStore } from "@/stores/auth";
import { useGateStore } from "@/stores/gate";
import { useBreakpoint } from "@/composables/useBreakpoint";
import { formatTime } from "@/utils/format";

const authStore = useAuthStore();
const gateStore = useGateStore();
const { isPhone } = useBreakpoint();

const canManage = computed(() => authStore.can("snapshot:manage"));
const canRestore = computed(() => authStore.can("snapshot:restore"));

const loading = ref(false);
const acting = ref(false);
const creating = ref(false);
const createProgress = ref("");
const savingPolicy = ref(false);
const restoring = ref(false);
const restoreStage = ref("");

const items = ref<SnapshotSummary[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 20;
const filters = reactive({ status: "", kind: "", q: "" });

const status = ref<any>(null);
const gate = ref<GatePayload>({
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
});
const policy = reactive<SnapshotPolicyPayload>({
  autoEnabled: false,
  autoIntervalMinutes: 30,
  autoScope: "system",
  keepLast: 20,
  keepDays: 7,
  autoIncludeFiles: false,
  lastAutoAt: null,
});

const competitions = ref<{ id: number; name: string }[]>([]);
const createForm = reactive({
  scope: "system" as "system" | "competition",
  competitionId: null as number | null,
  label: "",
  note: "",
  includeFiles: false,
  pauseFirst: false,
});

const pauseDialogVisible = ref(false);
const pauseForm = reactive({ reason: "管理员数据校验", message: "", ttlSeconds: 0 });

const detail = ref<SnapshotDetail | null>(null);
const detailVisible = ref(false);

const restoreVisible = ref(false);
const restoreTarget = ref<SnapshotSummary | null>(null);
const diff = ref<SnapshotDiff | null>(null);
const diffLoading = ref(false);
const restoreResult = ref<SnapshotRestoreResult | null>(null);
const restoreForm = reactive({
  confirmText: "",
  reason: "",
  includeUsers: false,
  restoreGlobal: false,
  restoreFiles: false,
  skipSafetySnapshot: false,
  verify: true,
});

let pollTimer: ReturnType<typeof setInterval> | null = null;

const modeLabel = computed(() => {
  if (gate.value.mode === "RESTORING") return "回退中（读写冻结）";
  if (gate.value.mode === "PAUSED") return "已强制暂停（写入冻结）";
  return "运行中";
});
const modeTagType = computed(() => {
  if (gate.value.mode === "RESTORING") return "danger";
  if (gate.value.mode === "PAUSED") return "warning";
  return "success";
});
const diffRows = computed<SnapshotDiffEntry[]>(() => {
  if (!diff.value) return [];
  return [...diff.value.tables]
    .filter((t) => t.deleteRows || t.insertRows)
    .sort((a, b) => (b.deleteRows || 0) + (b.insertRows || 0) - ((a.deleteRows || 0) + (a.insertRows || 0)));
});

function fmtTime(v?: string | null): string {
  return v ? formatTime(v) : "-";
}

function formatBytes(bytes?: number | null): string {
  const n = Number(bytes || 0);
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let value = n;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function kindLabel(kind: string): string {
  return { manual: "手动", auto: "自动", "pre-restore": "回退前", system: "系统" }[kind] || kind;
}

function statusLabel(s: string): string {
  return (
    { ready: "可用", building: "创建中", failed: "失败", restoring: "回退中", restored: "已回退过" }[s] ||
    s
  );
}

function statusTagType(s: string): "success" | "info" | "danger" | "warning" {
  if (s === "ready") return "success";
  if (s === "failed") return "danger";
  if (s === "building" || s === "restoring") return "warning";
  return "info";
}

function policyLabel(p: string): string {
  return { full: "整表还原", upsert: "按主键回写", record: "只记录不回写" }[p] || p;
}

function diffRowClass({ row }: { row: SnapshotDiffEntry }): string {
  return row.policy === "record" ? "row-skip" : "";
}

// ==================== 数据加载 ====================
async function loadStatus() {
  try {
    const data: any = await snapshotsApi.status();
    status.value = data;
    if (data?.gate) gate.value = data.gate;
    if (data?.policy) Object.assign(policy, data.policy);
  } catch {
    /* 错误提示由拦截器统一处理 */
  }
}

async function load() {
  loading.value = true;
  try {
    const res: any = await snapshotsApi.list({
      page: page.value,
      pageSize,
      status: filters.status || undefined,
      kind: filters.kind || undefined,
      q: filters.q || undefined,
    });
    items.value = (res?.items ?? []) as SnapshotSummary[];
    total.value = res?.total ?? 0;
  } finally {
    loading.value = false;
  }
}

async function loadCompetitions() {
  try {
    const res: any = await api.get("/competitions", { cache: false, bypassGate: true });
    const list = (res?.items ?? res ?? []) as any[];
    competitions.value = list.map((c) => ({ id: c.id, name: c.name }));
    if (!createForm.competitionId && competitions.value.length) {
      createForm.competitionId = competitions.value[0].id;
    }
  } catch {
    competitions.value = [];
  }
}

function reload() {
  page.value = 1;
  void load();
}

async function reloadAll() {
  await Promise.all([loadStatus(), load()]);
}

// ==================== 门禁操作 ====================
async function handlePause() {
  acting.value = true;
  try {
    await snapshotsApi.pause({
      reason: pauseForm.reason,
      message: pauseForm.message,
      ttlSeconds: Number(pauseForm.ttlSeconds) || 0,
    });
    ElMessage.success("已强制暂停全体写入");
    pauseDialogVisible.value = false;
    await loadStatus();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "暂停失败");
  } finally {
    acting.value = false;
  }
}

async function handleResume() {
  acting.value = true;
  try {
    await snapshotsApi.resume();
    ElMessage.success("系统已恢复运行");
    await loadStatus();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "恢复失败");
  } finally {
    acting.value = false;
  }
}

// ==================== 快照操作 ====================
async function handleCreate() {
  if (createForm.scope === "competition" && !createForm.competitionId) {
    ElMessage.warning("请先选择比赛");
    return;
  }
  creating.value = true;
  createProgress.value = "";
  try {
    const payload = {
      label: createForm.label,
      note: createForm.note,
      scope: createForm.scope,
      competitionId: createForm.scope === "competition" ? createForm.competitionId : null,
      includeFiles: createForm.includeFiles,
      pauseFirst: createForm.pauseFirst,
    };
    const created = await snapshotsApi.create(payload);
    ElMessage.success(`快照 #${created.id} 创建完成：${created.rowCount} 行`);
    createForm.label = "";
    createForm.note = "";
    reload();
    await loadStatus();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "创建失败");
  } finally {
    creating.value = false;
    createProgress.value = "";
  }
}

async function showDetail(row: SnapshotSummary) {
  try {
    detail.value = await snapshotsApi.get(row.id);
    detailVisible.value = true;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "加载详情失败");
  }
}

async function handleVerify(row: SnapshotSummary) {
  try {
    const res: any = await snapshotsApi.verify(row.id);
    if (res?.ok) ElMessage.success(`快照 #${row.id} 归档校验通过（${res.totalRows} 行）`);
    else ElMessage.error(`校验未通过：${(res?.problems || []).slice(0, 2).join("；")}`);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "校验失败");
  }
}

async function handleDownload(row: SnapshotSummary) {
  try {
    const blob = (await snapshotsApi.download(row.id)) as unknown as Blob;
    const url = URL.createObjectURL(blob instanceof Blob ? blob : new Blob([blob as any]));
    const a = document.createElement("a");
    a.href = url;
    a.download = `快照_${row.label}_${row.id}.tar.gz`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "下载失败");
  }
}

async function handleLock(row: SnapshotSummary) {
  try {
    await snapshotsApi.lock(row.id, !row.locked);
    ElMessage.success(row.locked ? "已解锁" : "已锁定（不会被保留策略清理）");
    reload();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "操作失败");
  }
}

async function handleDelete(row: SnapshotSummary) {
  try {
    await ElMessageBox.confirm(
      `确定删除快照 #${row.id}「${row.label}」？归档文件会一并删除且不可恢复。` +
        (row.locked ? "（该快照已锁定，需二次确认）" : ""),
      "删除快照",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await snapshotsApi.remove(row.id, row.locked);
    ElMessage.success("已删除");
    reload();
    await loadStatus();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "删除失败");
  }
}

// ==================== 回退 ====================
async function openRestore(row: SnapshotSummary) {
  restoreTarget.value = row;
  restoreResult.value = null;
  restoreForm.confirmText = "";
  restoreForm.reason = `管理员正在执行数据回退（快照 #${row.id} ${row.label}）`;
  restoreForm.includeUsers = false;
  restoreForm.restoreGlobal = false;
  restoreForm.restoreFiles = false;
  restoreForm.skipSafetySnapshot = false;
  restoreForm.verify = true;
  restoreVisible.value = true;
  await loadDiff();
}

async function loadDiff() {
  if (!restoreTarget.value) return;
  diffLoading.value = true;
  try {
    diff.value = await snapshotsApi.diff(restoreTarget.value.id, {
      includeUsers: restoreForm.includeUsers,
      restoreGlobal: restoreForm.restoreGlobal,
    });
  } catch (e) {
    diff.value = null;
    ElMessage.error(e instanceof Error ? e.message : "影响预览加载失败");
  } finally {
    diffLoading.value = false;
  }
}

async function handleRestore() {
  const target = restoreTarget.value;
  if (!target) return;
  restoring.value = true;
  restoreStage.value = "正在暂停全体用户…";
  const stageTimer = setInterval(() => {
    restoreStage.value = gateStore.isRestoring
      ? gateStore.state?.progress || "正在回退数据…"
      : "正在暂停全体用户…";
  }, 800);
  try {
    const result = await snapshotsApi.restore(target.id, {
      confirmText: restoreForm.confirmText.trim(),
      reason: restoreForm.reason,
      includeUsers: restoreForm.includeUsers,
      restoreGlobal: restoreForm.restoreGlobal,
      restoreFiles: restoreForm.restoreFiles,
      skipSafetySnapshot: restoreForm.skipSafetySnapshot,
      verify: restoreForm.verify,
    });
    restoreResult.value = result;
    ElMessage.success(`回退完成，数据版本已更新为 v${result.dataVersion}`);
    reload();
    await loadStatus();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "回退失败（数据未发生变化）");
  } finally {
    clearInterval(stageTimer);
    restoring.value = false;
    restoreStage.value = "";
  }
}

// ==================== 策略 ====================
async function savePolicy() {
  savingPolicy.value = true;
  try {
    const saved = await snapshotsApi.policy.save({ ...policy });
    Object.assign(policy, saved);
    ElMessage.success("策略已保存");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    savingPolicy.value = false;
  }
}

async function handleCleanup(dryRun: boolean) {
  try {
    const res: any = await snapshotsApi.cleanup({
      dryRun,
      keepLast: policy.keepLast,
      keepDays: policy.keepDays,
    });
    const detailText = (res?.removed || [])
      .slice(0, 5)
      .map((r: any) => `#${r.id} ${r.label}（${r.reason}）`)
      .join("\n");
    if (res?.removedCount) {
      await ElMessageBox.alert(
        `${dryRun ? "预览：" : "已"}清理 ${res.removedCount} 份快照${detailText ? `\n${detailText}` : ""}`,
        dryRun ? "清理预览" : "清理完成",
        { type: "info" },
      );
    } else {
      ElMessage.success("没有需要清理的快照");
    }
    if (!dryRun) {
      reload();
      await loadStatus();
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "清理失败");
  }
}

onMounted(async () => {
  await loadCompetitions();
  await reloadAll();
  // 门禁/快照状态：回退进行中由实时事件驱动，这里做 5 秒兜底轮询
  pollTimer = setInterval(() => {
    if (gateStore.blocked) void loadStatus();
  }, 5000);
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<style scoped>
.snapshot-view {
  padding: 16px;
}
.mm-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
  flex-wrap: wrap;
}
.mm-title {
  font-size: 20px;
  font-weight: 500;
  margin: 0;
}
.mm-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.block-card {
  margin-bottom: 16px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.card-title {
  font-weight: 600;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.hint.inline {
  margin-left: 10px;
}
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.gate-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 14px;
  flex-wrap: wrap;
}
.progress-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: #2563eb;
  font-size: 13px;
}
.create-form {
  max-width: 760px;
}
.snap-label {
  font-weight: 500;
}
.snap-note {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.ml4 {
  margin-left: 6px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.mt12 {
  margin-top: 12px;
}
.diff-block {
  margin-top: 14px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  padding: 10px 12px;
}
.diff-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
  font-size: 13px;
}
.diff-totals {
  margin: 8px 0;
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
}
.adv-options {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.restore-result {
  margin-top: 12px;
}
.detail-block {
  margin-top: 14px;
}
.detail-label {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 6px;
}
.detail-pre {
  margin: 0;
  padding: 8px;
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 6px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 220px;
  overflow: auto;
}
.hash,
.path {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  word-break: break-all;
}
:deep(.row-skip) {
  color: var(--el-text-color-secondary, #a8abb2);
}
</style>
