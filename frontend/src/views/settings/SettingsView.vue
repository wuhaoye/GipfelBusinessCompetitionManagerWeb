<template>
  <div class="settings">
    <h2 class="page-title">系统设置</h2>
    <div class="settings-section">
      <h3>关于</h3>
      <p>Gipfel商赛系统</p>
      <el-button @click="historyVisible = true">查看更新记录</el-button>
    </div>
    <AnnouncementHistoryDialog v-model="historyVisible" />
    <div class="settings-section">
      <h3>本地数据</h3>
      <el-button type="warning" @click="clearLocalData">清空本地缓存</el-button>
    </div>
    <div class="settings-section" v-if="isSuperAdmin">
      <h3>后端管理</h3>
      <el-button type="danger" @click="openAdmin">后端管理界面</el-button>
      <el-button type="warning" @click="openLogViewer">日志查看器</el-button>
    </div>
    <div class="settings-section" v-if="isSuperAdmin">
      <h3>系统管理</h3>
      <el-button @click="openAnnManager">管理更新公告</el-button>
      <el-button @click="openWidgetPkg">管理控件包</el-button>
    </div>
    <div class="settings-section" v-if="isSuperAdmin">
      <h3>比赛准备</h3>
      <p>
        开赛前的准备清单与开赛前体检：逐项列出比赛基础、行业口径、参赛主体、物资与产能、
        地理与物流、科技与需求、市场与规则、账号与权限、开赛前验收共 35 项，
        并自动提示配置风险。可一键导出为 Markdown 报告或 JSON 快照，用于复用比赛设置与赛后归档。
      </p>
      <el-button type="primary" @click="prepVisible = true">比赛准备总览 / 导出</el-button>
    </div>

    <PreparationOverviewDialog v-model="prepVisible" :competition-id="compStore.competitionId" />

    <!-- 管理更新公告弹窗 -->
    <el-dialog v-model="annDialogVisible" title="管理更新公告" width="700px" append-to-body destroy-on-close>
      <!-- 列表模式 -->
      <template v-if="!annEditing">
        <div style="margin-bottom: 12px; text-align: right">
          <el-button type="primary" size="small" @click="startCreate">＋ 新增公告</el-button>
        </div>
        <el-table :data="annList" v-loading="annLoading" stripe max-height="400">
          <el-table-column prop="version" label="版本" width="100" />
          <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
          <el-table-column prop="date" label="日期" width="110" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.isActive !== false ? 'success' : 'info'" size="small">
                {{ row.isActive !== false ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="startEdit(row)">编辑</el-button>
              <el-popconfirm title="确定删除此公告？" @confirm="handleDelete(row.id)">
                <template #reference>
                  <el-button link type="danger" size="small">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </template>

      <!-- 编辑/新增模式 -->
      <template v-else>
        <el-form :model="annForm" label-width="80px">
          <el-form-item label="版本号" required>
            <el-input v-model="annForm.version" placeholder="如 1.4.0" />
          </el-form-item>
          <el-form-item label="标题" required>
            <el-input v-model="annForm.title" placeholder="如 更新公告 v1.4.0" />
          </el-form-item>
          <el-form-item label="日期">
            <el-input v-model="annForm.date" placeholder="留空自动取今天" />
          </el-form-item>
          <el-form-item label="内容" required>
            <el-input
              v-model="annForm.content"
              type="textarea"
              :rows="8"
              placeholder="支持 HTML，如 <ul><li>...</li></ul>"
            />
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="annForm.isActive" />
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <template v-if="!annEditing">
          <el-button @click="annDialogVisible = false">关闭</el-button>
        </template>
        <template v-else>
          <el-button @click="annEditing = false">返回列表</el-button>
          <el-button type="primary" :loading="annSaving" @click="handleSave">保存</el-button>
        </template>
      </template>
    </el-dialog>

    <!-- 管理控件包弹窗 -->
    <el-dialog v-model="wpDialogVisible" title="管理控件包" width="700px" append-to-body destroy-on-close>
      <div style="margin-bottom: 12px; text-align: right">
        <el-upload :show-file-list="false" accept=".zip" :before-upload="handleUploadWidget" :disabled="wpUploading">
          <el-button type="primary" size="small" :loading="wpUploading">上传控件包</el-button>
        </el-upload>
      </div>
      <el-table :data="wpList" v-loading="wpLoading" stripe max-height="400">
        <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
        <el-table-column prop="widgetType" label="类型标识" width="160" show-overflow-tooltip />
        <el-table-column prop="version" label="版本" width="80" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.isActive ? 'success' : 'info'" size="small">{{ row.isActive ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="toggleWidgetActive(row)">{{ row.isActive ? '停用' : '启用' }}</el-button>
            <el-popconfirm title="确定删除此控件包？" @confirm="handleDeleteWidget(row.id)">
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="wpDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { clearCurrentAccountCache } from "@/api/cache";
import { resetRequestMemo } from "@/api/request";
import api, { announcementsApi, type AnnouncementItem, widgetPackagesApi, type WidgetPackageItem } from "@/api";
import { removeAccountItem } from "@/utils/accountStorage";
import { useVersionStore } from "@/stores/version";
import { useAuthStore } from "@/stores/auth";
import { useCompetitionStore } from "@/stores/competition";
import AnnouncementHistoryDialog from "@/components/AnnouncementHistoryDialog.vue";
import PreparationOverviewDialog from "@/components/preparation/PreparationOverviewDialog.vue";

const historyVisible = ref(false);
const versionStore = useVersionStore();
const authStore = useAuthStore();
const compStore = useCompetitionStore();
const isSuperAdmin = computed(() => authStore.isSuperAdmin);

// ===== 比赛准备总览 / 导出 =====
const prepVisible = ref(false);

// ===== 管理更新公告 =====
const annDialogVisible = ref(false);
const annLoading = ref(false);
const annSaving = ref(false);
const annEditing = ref(false);
const annEditingId = ref<number | null>(null);
const annList = ref<AnnouncementItem[]>([]);
const annForm = ref({ version: "", title: "", date: "", content: "", isActive: true });

async function openAnnManager() {
  annEditing.value = false;
  annDialogVisible.value = true;
  await fetchAnnList();
}

async function fetchAnnList() {
  annLoading.value = true;
  try {
    annList.value = (await announcementsApi.list()) || [];
  } catch {
    annList.value = [];
  } finally {
    annLoading.value = false;
  }
}

function startCreate() {
  annEditingId.value = null;
  annForm.value = { version: "", title: "", date: "", content: "", isActive: true };
  annEditing.value = true;
}

function startEdit(row: AnnouncementItem) {
  annEditingId.value = row.id;
  annForm.value = {
    version: row.version,
    title: row.title,
    date: row.date,
    content: row.content,
    isActive: row.isActive,
  };
  annEditing.value = true;
}

async function handleSave() {
  if (!annForm.value.version.trim()) return ElMessage.warning("请填写版本号");
  if (!annForm.value.title.trim()) return ElMessage.warning("请填写标题");
  if (!annForm.value.content.trim()) return ElMessage.warning("请填写内容");
  annSaving.value = true;
  try {
    const payload = {
      version: annForm.value.version.trim(),
      title: annForm.value.title.trim(),
      date: annForm.value.date.trim() || new Date().toISOString().slice(0, 10),
      content: annForm.value.content,
      isActive: annForm.value.isActive,
    };
    if (annEditingId.value != null) {
      await announcementsApi.update(annEditingId.value, payload);
      ElMessage.success("已保存");
    } else {
      await announcementsApi.create(payload);
      ElMessage.success("已发布");
    }
    annEditing.value = false;
    await fetchAnnList();
  } catch {
    ElMessage.error("操作失败");
  } finally {
    annSaving.value = false;
  }
}

async function handleDelete(id: number) {
  try {
    await announcementsApi.remove(id);
    ElMessage.success("已删除");
    await fetchAnnList();
  } catch {
    ElMessage.error("删除失败");
  }
}

// ===== 清空本地缓存 =====
async function clearLocalData() {
  try {
    await ElMessageBox.confirm(
      "将清空当前账号的本地缓存（比赛、原料、公司等全部请求数据）与当前比赛选择，下次将从服务端重新加载。确定继续？",
      "清空本地缓存",
      { type: "warning" },
    );
  } catch {
    return;
  }
  resetRequestMemo();
  await clearCurrentAccountCache();
  removeAccountItem("currentCompetition");
  ElMessage.success("本地缓存已清空，正在重新加载…");
  setTimeout(() => window.location.reload(), 300);
}

// ===== 后端管理 =====
const adminUrl = computed(() => `/admin/`);

async function openAdmin() {
  try {
    const res = (await api.post("/auth/backend-token")) as { token?: string };
    const token = res?.token;
    if (!token) throw new Error("未获取到访问令牌");
    const base = adminUrl.value;
    const sep = base.includes("?") ? "&" : "?";
    const url = `${base}${sep}token=${encodeURIComponent(token)}`;
    const win = window.open(url, "_blank", "noopener,noreferrer");
    if (win) {
      win.addEventListener("load", () => {
        try { win.history.replaceState(null, "", base); } catch { /* */ }
      });
    }
  } catch (e: unknown) {
    ElMessage.error((e as { message?: string })?.message || "打开后端管理界面失败");
  }
}

// ===== 日志查看器 =====
async function openLogViewer() {
  try {
    const res = (await api.post("/auth/logviewer-token")) as { token?: string };
    const token = res?.token;
    if (!token) throw new Error("未获取到访问令牌");

    // ★ 优先用后端下发的 log_viewer_url（/api/version）。
    //   它已考虑 LOG_VIEWER_PUBLIC_URL 显式覆盖，以及三种部署形态：
    //     · 子域：      https://log.<域名>/
    //     · 非标准端口：https://<域名>:8443/   （CF 只代理固定端口，8120 不在其中）
    //     · 纯 IP：     http://<IP>:8120/
    //   此处**现取**而不用 versionStore 里的缓存值：该字段只对已登录用户返回，
    //   而 store 的 checkVersion() 在登录前就已执行过，缓存到的会是 undefined。
    let base = "";
    try {
      const v = await api.get<{ log_viewer_url?: string }>("/version");
      base = (v?.log_viewer_url || "").trim();
    } catch {
      /* 取不到则走下面的回退 */
    }

    if (!base) {
      // 回退（仅在拿不到后端地址时使用）：按当前页面协议 + 主机名 + 端口推导。
      // 注意：此处过去**写死了 http://**，HTTPS 部署下会生成不可达地址；
      // 现改为跟随页面协议。带端口的形态仅在「同机、该端口对外可达」时成立。
      const scheme = window.location.protocol === "https:" ? "https:" : "http:";
      const port = versionStore.logViewerPort || 8120;
      base = `${scheme}//${window.location.hostname}:${port}/`;
    }

    const sep = base.includes("?") ? "&" : "?";
    const url = `${base}${sep}token=${encodeURIComponent(token)}`;
    window.open(url, "_blank", "noopener,noreferrer");
  } catch (e: unknown) {
    ElMessage.error((e as { message?: string })?.message || "打开日志查看器失败");
  }
}

// ===== 管理控件包 =====
const wpDialogVisible = ref(false);
const wpLoading = ref(false);
const wpUploading = ref(false);
const wpList = ref<WidgetPackageItem[]>([]);

async function openWidgetPkg() {
  wpDialogVisible.value = true;
  await fetchWidgetList();
}

async function fetchWidgetList() {
  wpLoading.value = true;
  try {
    wpList.value = (await widgetPackagesApi.list()) || [];
  } catch {
    wpList.value = [];
  } finally {
    wpLoading.value = false;
  }
}

async function handleUploadWidget(file: File) {
  wpUploading.value = true;
  try {
    await widgetPackagesApi.upload(file);
    ElMessage.success("控件包已上传，正在刷新…");
    setTimeout(() => window.location.reload(), 500);
  } catch (e: unknown) {
    ElMessage.error((e as { message?: string })?.message || "上传失败");
  } finally {
    wpUploading.value = false;
  }
  return false;
}

async function toggleWidgetActive(row: WidgetPackageItem) {
  try {
    await widgetPackagesApi.update(row.id, { isActive: !row.isActive });
    ElMessage.success(row.isActive ? "已停用，正在刷新…" : "已启用，正在刷新…");
    setTimeout(() => window.location.reload(), 500);
  } catch {
    ElMessage.error("操作失败");
  }
}

async function handleDeleteWidget(id: number) {
  try {
    await widgetPackagesApi.remove(id);
    ElMessage.success("已删除，正在刷新…");
    setTimeout(() => window.location.reload(), 500);
  } catch {
    ElMessage.error("删除失败");
  }
}
</script>

<style scoped>
.page-title {
  font-size: 20px;
  font-weight: 500;
  color: #1f1f1f;
  margin: 0 0 24px;
}
.settings-section {
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 24px;
  margin-bottom: 16px;
}
.settings-section h3 {
  font-size: 16px;
  font-weight: 500;
  color: #1f1f1f;
  margin: 0 0 16px;
}
.settings-section p {
  font-size: 14px;
  color: #8c8c8c;
  margin: 4px 0;
}
@media (max-width: 640px) {
  .settings-section .el-button {
    width: 100%;
    margin: 0 0 10px;
  }
  .settings-section .el-button:last-child {
    margin-bottom: 0;
  }
}
</style>