# -*- coding: utf-8 -*-
"""字段层写入守卫。

背景（实测）：SQLite 的 `decimal` 列是 NUMERIC 亲和性，超过 15 位有效数字的
十进制值会被转成 REAL(double) 存储；读回时 Django 的 SQLite 转换器以
`Context(prec=15)` 还原 —— 即 `DecimalField(max_digits=60, decimal_places=4)`
在 SQLite 上**并不精确**（仓库自带 `tests/sqlite_decimal_roundtrip.py` 实测 FAIL：
写入 12345678901234567890123.4567 读回 12345678901234600000000.0000）。

`ExactDecimalField` 在不改变列类型（因此不产生迁移）的前提下，拦下「写进去就会被
静默截断」的值，把静默丢精度变成明确的 400 报错：

- Django 的写库路径是 `Field.get_db_prep_save()`（`DecimalField` 覆写了它，
  不经过 `get_prep_value`），普通 `save()` / `create()` / `get_or_create()`、
  `bulk_create()` 与 `QuerySet.update()` 都汇聚到这里 —— 覆写一处即覆盖全部写路径；
- `get_prep_value()` / `get_db_prep_value()` 一并校验，兼顾查询条件与表达式路径；
- 仅在连接为 SQLite 时校验：PostgreSQL 等精确 numeric 后端不受任何限制
  （部署切库后该守卫自动失效，无需改代码）。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from django.db import connection as default_connection
from django.db import models

from apps.common.exceptions import BusinessError

# SQLite 以 double 存储十进制，Django 读回时按 prec=15 还原：
# 有效数字超过 15 位即无法原样往返。
SQLITE_EXACT_SIGNIFICANT_DIGITS = 15


# ==================== 守卫开关（供历史数据原样回写使用） ====================
# 场景：快照回退要把「从库里读出来的原值」原样写回。SQLite 读回的大数 Decimal 会带
# 上 REAL 展开后的尾数（有效数字 > 15），若仍走本守卫，回退会因「写进去会被截断」
# 被自己拦下 —— 而该值本来就是这样存的，写回并不会造成新的精度损失。
# 因此回退引擎在写库期间用 suspend_exact_decimal_guard() 临时放行；
# 常规业务写路径不受任何影响（默认 false）。
_guard_state = threading.local()


def _guard_suspended() -> bool:
    return bool(getattr(_guard_state, "suspended", False))


@contextmanager
def suspend_exact_decimal_guard():
    """临时关闭 SQLite 十进制精度守卫（仅快照回退等「原值回写」场景使用）。"""
    prev = _guard_suspended()
    _guard_state.suspended = True
    try:
        yield
    finally:
        _guard_state.suspended = prev


def significant_digits(value: Decimal) -> int:
    """Decimal 的最短有效数字位数（忽略前导零与尾随零：1E+14 → 1，0.0001 → 1）。"""
    if not value.is_finite():
        return SQLITE_EXACT_SIGNIFICANT_DIGITS + 1
    if value == 0:
        return 1
    return len(value.normalize().as_tuple().digits)


def assert_sqlite_exact(value: Decimal, label: str, connection=None) -> None:
    """SQLite 上拒绝无法精确往返的 Decimal（其余后端直接放行）。"""
    if _guard_suspended():
        return
    conn = connection if connection is not None else default_connection
    if getattr(conn, "vendor", None) != "sqlite":
        return
    digits = significant_digits(value)
    if digits > SQLITE_EXACT_SIGNIFICANT_DIGITS:
        raise BusinessError(
            f"{label} 需要 {digits} 位有效数字，超过 SQLite 能精确保存的 "
            f"{SQLITE_EXACT_SIGNIFICANT_DIGITS} 位（超出部分会被静默截断丢失）。"
            "请核对金额量纲，或改用 PostgreSQL。",
            code=400,
            status_code=400,
        )


def _as_check_decimal(value):
    """把待校验入参归一为 Decimal；无法解析时返回 None（交由 DRF/Django 报错）。

    浮点入参按**最短十进制表示**判定（`repr`），与仓库 `to_number()` /
    `_parse_exact_number()` 的口径一致：`float` 的二进制展开有 50+ 位，
    直接判定会把 `0.1` 这类正常入参误判为超精度。
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


class ExactDecimalField(models.DecimalField):
    """SQLite 上拒绝会被静默截断的 Decimal 写入（列类型不变，无需迁移）。"""

    def _label(self) -> str:
        model = getattr(self, "model", None)
        return f"{model.__name__}.{self.name}" if model is not None else str(self.name)

    def _check(self, value, connection=None) -> None:
        parsed = _as_check_decimal(value)
        if parsed is not None:
            assert_sqlite_exact(parsed, self._label(), connection)

    # ---------- 写库路径 ----------
    def get_db_prep_save(self, value, connection):
        if not hasattr(value, "as_sql"):
            self._check(value, connection)
        return super().get_db_prep_save(value, connection)

    def get_db_prep_value(self, value, connection, prepared=False):
        if not hasattr(value, "as_sql"):
            self._check(value, connection)
        return super().get_db_prep_value(value, connection, prepared)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        self._check(value)
        return value

    def deconstruct(self):
        """按父类 `DecimalField` 汇报。

        列类型与父类完全一致（本字段只增加运行期写入校验），若按子类路径汇报会为
        每个字段生成一条无实际 DDL 变化的 `AlterField` 迁移；此处保持迁移状态不变。
        """
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.DecimalField", args, kwargs
