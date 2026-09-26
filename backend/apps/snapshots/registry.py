"""快照注册表：把 Django 的全部业务模型登记成「可快照 / 可回退」的表清单。

这里解决四件事：

1. **全量发现**：遍历所有 `apps.*` 应用的具体模型（排除快照系统自身），
   保证「记录所有数据」——新增业务模型无需改动快照代码即可自动纳入。
2. **依赖拓扑序**：按外键依赖做拓扑排序（被引用的表在前）。回退时正序插入、
   倒序删除，即可在不关闭外键约束的前提下完成整表还原。
3. **作用域**：比赛维度快照只取该比赛的数据；没有 `competition` 外键的子表
   （如零件-原料配比）通过父表递归收敛；真正的全局表（产业类型/合同类型/公告/
   控件包等）只记录、不回写，避免回退一个比赛时误伤其它比赛或全局配置。
4. **回写策略**：
   - `full`   —— 回退时先按作用域删除、再整表写回（默认）
   - `upsert` —— 只按主键回写，绝不删除（Competition / User：避免级联删账号、
                 避免 token_version 回滚导致旧令牌复活）
   - `record` —— 只记录不回写（审计日志、比赛维度下的全局表）

新增业务模型若不在 `apps.*` 下，需在 `EXTRA_APP_LABELS` 里登记。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.apps import apps as django_apps
from django.db import models

logger = logging.getLogger("gipfel")

#: 参与快照的 app label 前缀
APP_LABEL_PREFIXES = ("apps.",)
#: 额外纳入的 app label（如需把第三方应用一起快照）
EXTRA_APP_LABELS: set[str] = set()
#: 排除的 app label（快照系统自身的记账表不参与快照/回退）
EXCLUDED_APP_LABELS = {"snapshots"}

#: 永远只记录、不回写的模型（追加型日志；回写会抹掉取证痕迹）
RECORD_ONLY_MODELS = {"AuditLog"}

#: 默认只按主键回写、绝不删除的模型（避免级联删除账号 / 令牌版本回滚）
UPSERT_ONLY_MODELS = {"Competition", "User"}

#: 账号数据默认不参与回写（安全默认；显式 includeUsers=true 时改为 upsert 回写）
USER_MODELS = {"User"}

#: 子表的作用域归属外键覆盖。仅用于「有多个候选父表、需要挑出真正决定比赛归属的那个」
#: 的歧义表：MessageRecipient 同时外键 message 与 user，其中 message 才是比赛归属。
SCOPE_FK_OVERRIDES: dict[str, tuple[str, ...]] = {
    "MessageRecipient": ("message",),
}

#: 广播资源名映射（与 apps.realtime.emit.MODEL_TO_RESOURCE 同源，缺失则为空）
def _resource_of(model) -> str | None:
    try:
        from apps.realtime.emit import MODEL_TO_RESOURCE

        return MODEL_TO_RESOURCE.get(model.__name__)
    except Exception:  # noqa: BLE001 - 实时模块不可用时不影响快照
        return None


class RegistryError(RuntimeError):
    """注册表构建失败（模型外键成环等）。"""


@dataclass(frozen=True)
class TableSpec:
    """一张参与快照的表。"""

    label: str  # 'materials.Material'
    app_label: str  # 'materials'
    name: str  # 'Material'
    table: str  # 'materials'
    model: type = field(compare=False, repr=False)
    resource: str | None = None
    #: 'root'（比赛自身）/ 'competition'（含 competition 外键）/ 'child'（经父表收敛）/ 'global'
    scope: str = "global"
    #: 'full' | 'upsert' | 'record'
    restore: str = "full"
    #: 指向「比赛作用域」父表的 FK 字段名（scope == 'child' 时使用）
    scope_fks: tuple[str, ...] = ()
    #: auto_now / auto_now_add 字段（bulk_create 会覆写，需回写原值）
    auto_fields: tuple[str, ...] = ()
    #: 主键 attname
    pk_attname: str = "id"
    order: int = 0

    @property
    def in_competition_scope(self) -> bool:
        return self.scope in ("root", "competition", "child")


@dataclass(frozen=True)
class Registry:
    """全部表的注册结果（按依赖拓扑序排列）。"""

    specs: tuple[TableSpec, ...]

    def by_label(self, label: str) -> TableSpec | None:
        for spec in self.specs:
            if spec.label == label or spec.name == label:
                return spec
        return None

    def by_table(self, table: str) -> TableSpec | None:
        for spec in self.specs:
            if spec.table == table:
                return spec
        return None

    @property
    def labels(self) -> list[str]:
        return [s.label for s in self.specs]


_REGISTRY: Registry | None = None


def _is_included_app(app_config) -> bool:
    """是否纳入快照：`apps.*` 下的业务应用，排除快照系统自身与明确排除项。"""
    if app_config.label in EXCLUDED_APP_LABELS:
        return False
    name = app_config.name or ""
    if any(name.startswith(p) for p in APP_LABEL_PREFIXES):
        return True
    return app_config.label in EXTRA_APP_LABELS


def _collect_models() -> list[type]:
    out: list[type] = []
    for app_config in django_apps.get_app_configs():
        if not _is_included_app(app_config):
            # 注意：本项目业务模型都在 apps.* 下；Django 自带 contrib 应用不快照
            continue
        try:
            models_module = app_config.models_module
        except Exception:  # noqa: BLE001
            continue
        if models_module is None:
            continue
        for model in app_config.get_models():
            meta = model._meta
            if meta.proxy or meta.abstract or not meta.managed or meta.auto_created:
                continue
            out.append(model)
    out.sort(key=lambda m: m._meta.label_lower)
    return out


def _auto_fields(model) -> tuple[str, ...]:
    names = []
    for f in model._meta.concrete_fields:
        if isinstance(f, (models.DateTimeField, models.DateField, models.TimeField)):
            if getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False):
                names.append(f.attname)
    return tuple(names)


def _fk_fields_to(model, targets: set[str]) -> list[str]:
    """返回指向 targets（model label）的外键字段名。"""
    out = []
    for f in model._meta.concrete_fields:
        if not f.is_relation or not (f.many_to_one or f.one_to_one):
            continue
        related = getattr(f, "related_model", None)
        if related is None:
            continue
        if related._meta.label_lower in targets or related.__name__ in targets:
            out.append(f.name)
    return out


def _topological_order(models_list: list[type]) -> list[type]:
    """按外键依赖排序：被引用的模型在前。同层按 label 字典序，保证结果稳定。"""
    by_label_lower = {m._meta.label_lower: m for m in models_list}
    names = {m.__name__ for m in models_list}
    deps: dict[str, set[str]] = {m._meta.label_lower: set() for m in models_list}
    for model in models_list:
        key = model._meta.label_lower
        for f in model._meta.concrete_fields:
            if not f.is_relation or not (f.many_to_one or f.one_to_one):
                continue
            related = getattr(f, "related_model", None)
            if related is None:
                continue
            rel_key = related._meta.label_lower
            if rel_key == key:
                continue  # 自引用不影响建表顺序
            if rel_key in by_label_lower:
                deps[key].add(rel_key)
    ordered: list[type] = []
    resolved: set[str] = set()
    pending = sorted(by_label_lower)
    while pending:
        progressed = False
        for key in list(pending):
            if deps[key] <= resolved:
                ordered.append(by_label_lower[key])
                resolved.add(key)
                pending.remove(key)
                progressed = True
        if not progressed:
            cycle = ", ".join(sorted(pending))
            raise RegistryError(
                "模型外键存在循环依赖，无法确定回退顺序："
                f"{cycle}。请在 registry.py 中显式声明顺序（ORDER_OVERRIDES）。"
            )
    return ordered


def build_registry(force: bool = False) -> Registry:
    """构建（并缓存）注册表。"""
    global _REGISTRY
    if _REGISTRY is not None and not force:
        return _REGISTRY

    models_list = _collect_models()
    ordered = _topological_order(models_list)
    labels_lower = {m._meta.label_lower for m in models_list}
    names = {m.__name__ for m in models_list}

    # 先判定「直接带 competition 外键」与「比赛根」的表
    direct: dict[str, bool] = {}
    has_comp_fk: dict[str, bool] = {}
    for m in ordered:
        key = m._meta.label_lower
        direct[key] = any(f.name == "competition" for f in m._meta.concrete_fields)
        has_comp_fk[key] = direct[key]
    roots = {m._meta.label_lower for m in ordered if m.__name__ == "Competition"}

    # 子表：经父表收敛。BFS 直到不再有新表被判定为「比赛作用域」。
    scoped: set[str] = {k for k, v in direct.items() if v} | roots
    scope_fks: dict[str, tuple[str, ...]] = {}
    changed = True
    while changed:
        changed = False
        for m in ordered:
            key = m._meta.label_lower
            if key in scoped:
                continue
            override = SCOPE_FK_OVERRIDES.get(m.__name__)
            candidates: list[str] = []
            for f in m._meta.concrete_fields:
                if not f.is_relation or not (f.many_to_one or f.one_to_one):
                    continue
                related = getattr(f, "related_model", None)
                if related is None:
                    continue
                rel_key = related._meta.label_lower
                if rel_key not in labels_lower or rel_key == key:
                    continue
                if rel_key not in scoped:
                    continue
                if related.__name__ == "User" and m.__name__ != "User":
                    # User 虽然在比赛维度可收敛，但多数子表通过它收敛会过度扩大范围
                    # （例如跨比赛的消息收件人），仅在显式覆盖时才用它。
                    continue
                candidates.append(f.name)
            if override:
                candidates = [c for c in candidates if c in override] or [
                    f.name
                    for f in m._meta.concrete_fields
                    if f.is_relation
                    and getattr(f, "related_model", None) is not None
                    and f.name in override
                ]
            if candidates:
                scope_fks[key] = tuple(sorted(candidates))
                scoped.add(key)
                changed = True

    specs: list[TableSpec] = []
    for order, model in enumerate(ordered):
        meta = model._meta
        key = meta.label_lower
        if key in roots:
            scope = "root"
        elif direct[key]:
            scope = "competition"
        elif key in scoped:
            scope = "child"
        else:
            scope = "global"

        name = model.__name__
        if name in RECORD_ONLY_MODELS:
            policy = "record"
        elif name in UPSERT_ONLY_MODELS:
            policy = "upsert"
        else:
            policy = "full"

        specs.append(
            TableSpec(
                label=meta.label,
                app_label=meta.app_label,
                name=name,
                table=meta.db_table,
                model=model,
                resource=_resource_of(model),
                scope=scope,
                restore=policy,
                scope_fks=scope_fks.get(key, ()),
                auto_fields=_auto_fields(model),
                pk_attname=meta.pk.attname,
                order=order,
            )
        )

    registry = Registry(specs=tuple(specs))
    _REGISTRY = registry
    logger.debug(
        "快照注册表就绪：%d 张表（比赛作用域 %d / 全局 %d）",
        len(specs),
        sum(1 for s in specs if s.in_competition_scope),
        sum(1 for s in specs if not s.in_competition_scope),
    )
    return registry


def names_in_registry() -> set[str]:
    """注册表中的模型名集合（供测试/自检使用）。"""
    return {s.name for s in build_registry().specs}


# ====================================================================
# 作用域查询
# ====================================================================
def scope_queryset(spec: TableSpec, competition_id: int | None, *, memo: dict | None = None):
    """返回某表在指定作用域下的行集合。

    - `competition_id is None` → 全系统作用域：整表
    - 比赛作用域：
        · root        → pk == competition_id
        · competition → competition_id == competition_id
        · child       → 任一「比赛作用域父表」命中的行（子查询实现）
        · global      → 空集（只记录、不回写）
    """
    from django.db.models import Q

    model = spec.model
    if competition_id is None:
        return model._default_manager.all()

    if spec.scope == "global":
        return model._default_manager.none()
    if spec.scope == "root":
        return model._default_manager.filter(pk=competition_id)
    if spec.scope == "competition":
        return model._default_manager.filter(competition_id=competition_id)

    memo = memo if memo is not None else {}
    if spec.label in memo:
        return memo[spec.label]
    cond = Q()
    for fk_name in spec.scope_fks:
        f = model._meta.get_field(fk_name)
        parent_spec = _spec_of_model(f.related_model, memo)
        if parent_spec is None:
            continue
        sub = scope_queryset(parent_spec, competition_id, memo=memo)
        cond |= Q(**{f"{fk_name}__in": sub})
    qs = model._default_manager.filter(cond) if cond else model._default_manager.none()
    memo[spec.label] = qs
    return qs


def _spec_of_model(model, memo: dict | None = None) -> TableSpec | None:
    registry = build_registry()
    label = model._meta.label
    for spec in registry.specs:
        if spec.label == label:
            return spec
    return None
