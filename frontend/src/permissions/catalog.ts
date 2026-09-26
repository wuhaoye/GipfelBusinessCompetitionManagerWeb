/**
 * 权限目录（前端镜像，与后端 backend/apps/common/permissions.py 同构）。
 * 修改时需与后端同步。
 */

export interface PermissionAction {
  key: string;
  action: string;
  label: string;
}

export interface PermissionDomain {
  key: string;
  label: string;
  group: string;
  actions: PermissionAction[];
  actionRank?: Record<string, number>;
}

export const DEFAULT_ACTION_RANKS: Record<string, number> = {
  view: 10,
  edit: 20,
  manage: 30,
  execute: 40,
  audit: 50,
};

const CONTRACT_ACTION_RANKS: Record<string, number> = {
  view: 10,
  audit: 20,
  execute: 30,
  manage: 40,
};

// 快照域自定义等级：restore（强制暂停 + 回退）最高，持有 restore 蕴含 manage / view。
const SNAPSHOT_ACTION_RANKS: Record<string, number> = {
  view: 10,
  manage: 20,
  restore: 30,
};

export const PERMISSION_CATALOG: PermissionDomain[] = [
  { key: "competition", label: "比赛管理", group: "比赛", actions: [{ key: "competition:manage", action: "manage", label: "管理（增删改）" }] },
  { key: "data:material", label: "原料管理", group: "数据", actions: [ { key: "data:material:view", action: "view", label: "查看" }, { key: "data:material:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:part", label: "零件管理", group: "数据", actions: [ { key: "data:part:view", action: "view", label: "查看" }, { key: "data:part:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:product", label: "产品管理", group: "数据", actions: [ { key: "data:product:view", action: "view", label: "查看" }, { key: "data:product:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:map", label: "地图管理", group: "数据", actions: [ { key: "data:map:view", action: "view", label: "查看" }, { key: "data:map:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:infrastructure", label: "基建管理", group: "数据", actions: [ { key: "data:infrastructure:view", action: "view", label: "查看" }, { key: "data:infrastructure:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:tech", label: "科技树管理", group: "数据", actions: [ { key: "data:tech:view", action: "view", label: "查看" }, { key: "data:tech:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:fuel", label: "燃料管理", group: "数据", actions: [ { key: "data:fuel:view", action: "view", label: "查看" }, { key: "data:fuel:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:vehicle", label: "载具管理", group: "数据", actions: [ { key: "data:vehicle:view", action: "view", label: "查看" }, { key: "data:vehicle:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:warehouse", label: "仓库管理", group: "数据", actions: [ { key: "data:warehouse:view", action: "view", label: "查看" }, { key: "data:warehouse:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:productionLine", label: "生产线管理", group: "数据", actions: [ { key: "data:productionLine:view", action: "view", label: "查看" }, { key: "data:productionLine:edit", action: "edit", label: "编辑（增删改）" } ] },
  { key: "data:region", label: "区域管理", group: "区域", actions: [ { key: "data:region:view", action: "view", label: "查看" }, { key: "data:region:edit", action: "edit", label: "编辑（增删改）" } ] },
  // 消费者需求不再单设权限：与区域总览卡片同属「区域」数据，统一由 data:region:view/edit 管控。
  { key: "contractType", label: "合同类型管理", group: "合同", actions: [ { key: "contractType:view", action: "view", label: "查看" }, { key: "contractType:manage", action: "manage", label: "管理（增删改）" } ] },
  { key: "contract", label: "合同管理", group: "合同", actionRank: CONTRACT_ACTION_RANKS, actions: [ { key: "contract:view", action: "view", label: "查看" }, { key: "contract:audit", action: "audit", label: "审核（公司范围）" }, { key: "contract:execute", action: "execute", label: "执行（比赛级）" }, { key: "contract:manage", action: "manage", label: "管理（新建/删除）" } ] },
  { key: "industryType", label: "产业类型管理", group: "产业", actions: [ { key: "industryType:view", action: "view", label: "查看" }, { key: "industryType:manage", action: "manage", label: "管理（增删改）" } ] },
  { key: "company", label: "公司管理", group: "产业", actions: [ { key: "company:view", action: "view", label: "查看（读取公司产业字段）" }, { key: "company:manage", action: "manage", label: "管理（增删改公司）" } ] },
  { key: "account", label: "账户管理", group: "系统", actions: [{ key: "account:manage", action: "manage", label: "管理（增删改账号与权限）" }] },
  { key: "message", label: "消息中心", group: "消息", actions: [ { key: "message:view", action: "view", label: "查看（收件箱/已发布/接收弹窗）" }, { key: "message:manage", action: "manage", label: "管理（发布/删除消息）" } ] },
  { key: "stock", label: "股票系统", group: "股票", actions: [ { key: "stock:view", action: "view", label: "查看行情" }, { key: "stock:edit", action: "edit", label: "低级管理" }, { key: "stock:manage", action: "manage", label: "高级管理" } ] },
  { key: "snapshot", label: "快照与回退", group: "系统", actionRank: SNAPSHOT_ACTION_RANKS, actions: [ { key: "snapshot:view", action: "view", label: "查看快照列表 / 详情 / 校验" }, { key: "snapshot:manage", action: "manage", label: "创建 / 删除 / 清理快照，设置保留策略" }, { key: "snapshot:restore", action: "restore", label: "强制暂停全体并回退数据" } ] },
];

function domainOf(key: string): string { const p = key.split(":"); return p.slice(0, -1).join(":"); }
function actionOf(key: string): string { return key.split(":").slice(-1)[0]; }
function domainActionRank(domain: string): Record<string, number> {
  const d = PERMISSION_CATALOG.find((x) => x.key === domain);
  return d?.actionRank ?? DEFAULT_ACTION_RANKS;
}

export function hasPermission(
  role: string | undefined,
  permissions: string[] | null | undefined,
  required: string | string[],
): boolean {
  if (role === "SUPER_ADMIN") return true;
  const req = Array.isArray(required) ? required : [required];
  if (req.length === 0) return true;
  const perms = permissions || [];
  for (const reqKey of req) {
    const domain = domainOf(reqKey);
    const reqAction = actionOf(reqKey);
    const reqRank = domainActionRank(domain)[reqAction] ?? 0;
    const satisfied = perms.some((p) => {
      if (domainOf(p) !== domain) return false;
      const userRank = domainActionRank(domain)[actionOf(p)] ?? 0;
      return userRank >= reqRank;
    });
    if (!satisfied) return false;
  }
  return true;
}
