"""权限目录与 RBAC。

39 个权限 key，19 个域，动作等级蕴含：
    manage ⊇ execute ⊇ audit ⊇ edit ⊇ view
（合同域自定义：manage ⊇ execute ⊇ audit ⊇ view）
"""
from __future__ import annotations

from typing import Iterable

# ==================== 动作等级表 ====================
DEFAULT_ACTION_RANKS = {
    "view": 10,
    "edit": 20,
    "manage": 30,
    "execute": 40,
    "audit": 50,
}

# 合同域自定义等级（manage ⊇ execute ⊇ audit ⊇ view）
CONTRACT_ACTION_RANKS = {
    "view": 10,
    "audit": 20,
    "execute": 30,
    "manage": 40,
}

# 快照域自定义等级：restore（强制暂停 + 回退）是最高等级动作，
# 持有 restore 自动蕴含 manage / view；反之不成立。
SNAPSHOT_ACTION_RANKS = {
    "view": 10,
    "manage": 20,
    "restore": 30,
}


# ==================== 目录定义 ====================
PERMISSION_CATALOG = [
    {
        "key": "competition",
        "label": "比赛管理",
        "group": "比赛",
        "actions": [{"key": "competition:manage", "action": "manage", "label": "管理（增删改）"}],
    },
    {
        "key": "data:material",
        "label": "原料管理",
        "group": "数据",
        "actions": [
            {"key": "data:material:view", "action": "view", "label": "查看"},
            {"key": "data:material:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:part",
        "label": "零件管理",
        "group": "数据",
        "actions": [
            {"key": "data:part:view", "action": "view", "label": "查看"},
            {"key": "data:part:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:product",
        "label": "产品管理",
        "group": "数据",
        "actions": [
            {"key": "data:product:view", "action": "view", "label": "查看"},
            {"key": "data:product:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:map",
        "label": "地图管理",
        "group": "数据",
        "actions": [
            {"key": "data:map:view", "action": "view", "label": "查看"},
            {"key": "data:map:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:infrastructure",
        "label": "基建管理",
        "group": "数据",
        "actions": [
            {"key": "data:infrastructure:view", "action": "view", "label": "查看"},
            {"key": "data:infrastructure:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:tech",
        "label": "科技树管理",
        "group": "数据",
        "actions": [
            {"key": "data:tech:view", "action": "view", "label": "查看"},
            {"key": "data:tech:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:fuel",
        "label": "燃料管理",
        "group": "数据",
        "actions": [
            {"key": "data:fuel:view", "action": "view", "label": "查看"},
            {"key": "data:fuel:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:vehicle",
        "label": "载具管理",
        "group": "数据",
        "actions": [
            {"key": "data:vehicle:view", "action": "view", "label": "查看"},
            {"key": "data:vehicle:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:warehouse",
        "label": "仓库管理",
        "group": "数据",
        "actions": [
            {"key": "data:warehouse:view", "action": "view", "label": "查看"},
            {"key": "data:warehouse:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:productionLine",
        "label": "生产线管理",
        "group": "数据",
        "actions": [
            {"key": "data:productionLine:view", "action": "view", "label": "查看"},
            {"key": "data:productionLine:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    {
        "key": "data:region",
        "label": "区域管理",
        "group": "区域",
        "actions": [
            {"key": "data:region:view", "action": "view", "label": "查看"},
            {"key": "data:region:edit", "action": "edit", "label": "编辑（增删改）"},
        ],
    },
    # 消费者需求不再单设权限：与区域总览卡片同属「区域」数据，统一由
    # data:region:view / data:region:edit 管控（见 apps/consumer_demands/views.py）。
    {
        "key": "contractType",
        "label": "合同类型管理",
        "group": "合同",
        "actions": [
            {"key": "contractType:view", "action": "view", "label": "查看"},
            {"key": "contractType:manage", "action": "manage", "label": "管理（增删改）"},
        ],
    },
    {
        "key": "contract",
        "label": "合同管理",
        "group": "合同",
        "actionRank": CONTRACT_ACTION_RANKS,
        "actions": [
            {"key": "contract:view", "action": "view", "label": "查看"},
            {"key": "contract:audit", "action": "audit", "label": "审核（公司范围，仅限范围内公司合同）"},
            {"key": "contract:execute", "action": "execute", "label": "执行（比赛级，不限公司）"},
            {"key": "contract:manage", "action": "manage", "label": "管理（新建/删除）"},
        ],
    },
    {
        "key": "industryType",
        "label": "产业类型管理",
        "group": "产业",
        "actions": [
            {"key": "industryType:view", "action": "view", "label": "查看"},
            {"key": "industryType:manage", "action": "manage", "label": "管理（增删改）"},
        ],
    },
    {
        "key": "company",
        "label": "公司管理",
        "group": "产业",
        "actions": [
            {"key": "company:view", "action": "view", "label": "查看（读取公司产业字段）"},
            {"key": "company:manage", "action": "manage", "label": "管理（增删改公司）"},
        ],
    },
    {
        "key": "account",
        "label": "账户管理",
        "group": "系统",
        "actions": [{"key": "account:manage", "action": "manage", "label": "管理（增删改账号与权限）"}],
    },
    {
        "key": "message",
        "label": "消息中心",
        "group": "消息",
        "actions": [
            {"key": "message:view", "action": "view", "label": "查看（收件箱 / 已发布 / 接收弹窗）"},
            {"key": "message:manage", "action": "manage", "label": "管理（发布 / 删除消息）"},
        ],
    },
    {
        "key": "stock",
        "label": "股票系统",
        "group": "股票",
        "actions": [
            {"key": "stock:view", "action": "view", "label": "查看行情，并用本公司账户买卖（下单 / 撤单）"},
            {"key": "stock:edit", "action": "edit", "label": "资金账户管理（创建 / 编辑账户）"},
            {"key": "stock:manage", "action": "manage", "label": "高级管理（看全部 / 增删股票 / 推进轮次）"},
        ],
    },
    {
        "key": "snapshot",
        "label": "快照与回退",
        "group": "系统",
        "actionRank": SNAPSHOT_ACTION_RANKS,
        "actions": [
            {"key": "snapshot:view", "action": "view", "label": "查看快照列表 / 详情 / 校验"},
            {"key": "snapshot:manage", "action": "manage", "label": "创建 / 删除 / 清理快照，设置保留策略"},
            {"key": "snapshot:restore", "action": "restore", "label": "强制暂停全体并回退数据"},
        ],
    },
]

ALL_PERMISSION_KEYS = [a["key"] for d in PERMISSION_CATALOG for a in d["actions"]]

# UI 分组
PERMISSION_GROUPS = []
_group_map = {}
for d in PERMISSION_CATALOG:
    _group_map.setdefault(d["group"], []).append(d)
for g, domains in _group_map.items():
    PERMISSION_GROUPS.append({"group": g, "domains": domains})

# 已废止但视为合法的 key（兼容旧数据）
DEPRECATED_PERMISSION_KEYS = [
    "settings:view",
    "settings:manage",
    "dashboard:view",
]


def is_valid_permissions(perms) -> bool:
    if not isinstance(perms, list):
        return False
    return all(
        isinstance(p, str)
        and (p in ALL_PERMISSION_KEYS or p in DEPRECATED_PERMISSION_KEYS)
        for p in perms
    )


def _domain_action_rank(domain: str) -> dict:
    for d in PERMISSION_CATALOG:
        if d["key"] == domain:
            return d.get("actionRank", DEFAULT_ACTION_RANKS)
    return DEFAULT_ACTION_RANKS


def _domain_of(key: str) -> str:
    """从权限 key 提取域前缀。

    合同/比赛等单段域 key 形如 'contract:view' → 域 'contract'；
    数据域 key 形如 'data:material:view' → 域 'data:material'。
    """
    parts = key.split(":")
    return ":".join(parts[:-1])


def _action_of(key: str) -> str:
    return key.split(":")[-1]


def has_permission(
    role: str | None,
    permissions: Iterable[str] | None,
    required: str | list[str],
) -> bool:
    """判断是否满足所需权限。

    - SUPER_ADMIN 隐式拥有全部权限
    - 其余角色：required 中每一项都要被满足（AND 语义）
    - 动作蕴含：用户持有该域任一动作 actionZ 且 rank(Z) ≥ rank(X) 即满足 domain:actionX
    """
    if role == "SUPER_ADMIN":
        return True
    req_list = required if isinstance(required, list) else [required]
    if not req_list:
        return True
    perms = list(permissions or [])
    for req_key in req_list:
        domain = _domain_of(req_key)
        req_action = _action_of(req_key)
        ranks = _domain_action_rank(domain)
        if req_action not in ranks:
            # fail-closed：required key 的 action 不在目录等级表内（拼错 key）
            # 时直接拒绝，防止被该域任意合法动作满足。
            return False
        req_rank = ranks[req_action]
        # 用户持有该域任一动作且等级 ≥ 所需即满足
        satisfied = False
        for p in perms:
            if _domain_of(p) != domain:
                continue
            user_action = _action_of(p)
            user_rank = _domain_action_rank(domain).get(user_action, 0)
            if user_rank >= req_rank:
                satisfied = True
                break
        if not satisfied:
            return False
    return True


# ==================== 角色模板与授予上限 ====================
# 后端权威定义角色默认权限集合与「授予上限」，用于账号权限授予校验。

# 基础视图权限（17 个）
BASE_VIEW_PERMISSIONS = [
    "data:material:view",
    "data:part:view",
    "data:product:view",
    "data:map:view",
    "data:infrastructure:view",
    "data:tech:view",
    "data:fuel:view",
    "data:vehicle:view",
    "data:warehouse:view",
    "data:productionLine:view",
    "data:region:view",
    "industryType:view",
    "contractType:view",
    "company:view",
    "contract:view",
    "message:view",
    "stock:view",
]

# 超管专属权限：任何非超管角色禁止持有
SUPER_ADMIN_ONLY_PERMISSIONS = [
    "competition:manage",
    "account:manage",
    "stock:manage",
    # 快照/回退会读写全库、强制暂停全体用户，且涉及跨比赛数据，仅超管可持有
    "snapshot:view",
    "snapshot:manage",
    "snapshot:restore",
]

# COMPETITION_ADMIN 可选扩展集（默认不开放，超管可按需放开）
_COMPETITION_ADMIN_EXTRAS = [
    "message:manage",
    "contractType:manage",
    "industryType:manage",
    "company:manage",
    "data:region:edit",
]

ROLE_TEMPLATES = {
    "SUPER_ADMIN": {
        "defaultPermissions": [],  # 隐式全放行
        "grantCeiling": [],  # 任意（不受限）
        "grantExtras": [],
        "isSuperAdmin": True,
    },
    "COMPETITION_ADMIN": {
        "defaultPermissions": BASE_VIEW_PERMISSIONS + [
            "contract:manage",
            "contract:audit",
            "contract:execute",
            "stock:edit",
        ],
        "grantCeiling": BASE_VIEW_PERMISSIONS + [
            "contract:manage",
            "contract:audit",
            "contract:execute",
            "stock:edit",
        ],
        "grantExtras": _COMPETITION_ADMIN_EXTRAS,
        "isSuperAdmin": False,
    },
    "PLAYER": {
        # 选手只读行情（stock:view）：资金账户由管理员统一创建并派给选手所在公司，
        # 选手在下单/撤单时经 _assert_account_operable 校验「自己公司的账户」；
        # 建账户/改账户需要 stock:edit，选手不持有 → 不可自建账户。
        "defaultPermissions": BASE_VIEW_PERMISSIONS,
        "grantCeiling": BASE_VIEW_PERMISSIONS,
        "grantExtras": [],
        "isSuperAdmin": False,
    },
}


def assert_grant_allowed(actor_role, target_role, permissions, allow_extras: bool = False):
    """校验权限授予是否在上限范围内。

    allow_extras：是否放开「扩展集」（角色模板 `grantExtras` 里的权限）。
    扩展集默认不开放，必须由超管在请求体里显式声明 `allowExtras=true` 才可授予 ——
    改前该分支恒记违规，导致扩展集**永远授不出去**（`company:manage` /
    `message:manage` / `industryType:manage` / `contractType:manage` /
    `data:region:edit` 实际只有超管可用），与文档承诺的「超管可按需放开」不符（审计 I-13）。

    返回 (allowed: bool, violations: list[str])。
    """
    # 非超管不能写权限
    if actor_role != "SUPER_ADMIN":
        return False, ["仅超管可修改权限"]

    # 超管角色只能设为空数组（不落库）
    if target_role == "SUPER_ADMIN":
        if len(permissions) > 0:
            return False, ["超管权限不落库，必须为空数组"]
        return True, []

    template = ROLE_TEMPLATES.get(target_role)
    if not template:
        return False, [f"未知角色: {target_role}"]

    # 检查是否在授予上限范围内（扩展集需显式放开）
    ceiling = set(template["grantCeiling"])
    extras = template["grantExtras"]
    violations = []
    for perm in permissions:
        # 超管专属权限检查（无论是否放开扩展集都不可授予）
        if perm in SUPER_ADMIN_ONLY_PERMISSIONS:
            violations.append(f"{perm} 为超管专属权限，不可授予 {target_role}")
            continue
        if perm not in ceiling:
            if perm in extras:
                if not allow_extras:
                    violations.append(f"{perm} 在扩展集中，需显式 allowExtras=true 放开")
            else:
                violations.append(f"{perm} 超出 {target_role} 的授予上限")
    return len(violations) == 0, violations


def role_default_permissions(role: str | None) -> list[str]:
    """角色模板的默认权限（供 `permissions=null` 的「按角色继承」语义使用，审计 I-19）。

    - SUPER_ADMIN：隐式全权，不落库，返回空列表（判定由 `has_permission` 的 role 短路负责）
    - 其它角色：返回模板 defaultPermissions 的副本（调用方可能就地修改）
    """
    if role == "SUPER_ADMIN":
        return []
    template = ROLE_TEMPLATES.get(role or "")
    if not template:
        return []
    return list(template["defaultPermissions"])
