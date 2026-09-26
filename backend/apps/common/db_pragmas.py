"""SQLite PRAGMA 调优（C2 阶段 1）：connection_created 信号处理器。

背景（见《架构性运维约束整改简报.md》C2.1 与 `docs/运维约束整改设计说明.md` §3）：
SQLite 默认 `journal_mode=delete`（回滚日志）+ `busy_timeout=5000ms`，写并发一高就抛
`database is locked`；WAL 让「读不阻塞写」，busy_timeout 20s 给写者更长的排队窗口。

为什么必须两层实现（不要在 settings 里找 `init_command`）：
Django 5.0.14 的 sqlite3 后端 **没有** `OPTIONS["init_command"]` / `transaction_mode`——
`django/db/backends/sqlite3/base.py::get_connection_params()` 只挑走 `database`/`timeout`
等形参，其余键会**原样** `**kwargs` 传给 `sqlite3.connect()`（多传即 TypeError）。
因此：
  ① `settings.DATABASES["default"]["OPTIONS"]["timeout"]` → `sqlite3.connect(timeout=...)`
     → `PRAGMA busy_timeout = timeout*1000`（开不了 WAL，也管不了 synchronous）；
  ② 本模块：`connection_created` 信号 → 对**每一条**新建连接执行
     `settings.SQLITE_PRAGMA_STATEMENTS`（journal_mode / synchronous / busy_timeout /
     wal_autocheckpoint）。每条连接都要跑一遍，所以必须挂在信号上而不是只跑一次。
两者缺一不可。

边界与降级（本模块的硬约束）：
- `settings.SQLITE_TUNING_ENABLED=false` → 直接跳过，行为与改造前完全一致（可现场秒回退）；
- 非 sqlite 后端 → 安全跳过（将来切 PostgreSQL 时本模块不应报错）；
- `:memory:` / `file:...?mode=memory` 内存库 → 跳过（测试库就是内存库；WAL 对内存库
  无意义，`PRAGMA journal_mode=WAL` 只会返回 `memory`，跳过可避免每条测试连接刷日志）；
- **单条** PRAGMA 失败只记 warning，不抛、不阻断连接建立、也不影响后续语句；
- 启动日志明确写出「WAL 生效 / 未生效 / 非 sqlite / 已关闭」，避免"以为开了 WAL 其实没开"。
"""
from __future__ import annotations

import logging
import os
import threading

from django.conf import settings as django_settings
from django.db.backends.signals import connection_created

logger = logging.getLogger("gipfel")

#: SQLite 引擎名后缀（django.db.backends.sqlite3）
_SQLITE_ENGINE_SUFFIX = "sqlite3"

_wire_lock = threading.Lock()
_wired = False
_note_lock = threading.Lock()
_notes: set[str] = set()


def _mark_note_once(key: str) -> bool:
    """记录「本进程首次出现该情形」并返回是否首次（用于把启动结论压成一条日志）。"""
    with _note_lock:
        if key in _notes:
            return False
        _notes.add(key)
        return True


def is_in_memory_database(name) -> bool:
    """判断 sqlite 目标是否为内存库（含 Django 测试库的 `file:memorydb_x?mode=memory`）。"""
    if name is None:
        return True
    if isinstance(name, os.PathLike):
        return False
    text = str(name).strip()
    if not text:
        return True
    if text == ":memory:":
        return True
    return "mode=memory" in text


def _is_sqlite(connection) -> bool:
    """是否 sqlite 后端：优先用 connection.vendor，退化到 settings_dict["ENGINE"]。"""
    vendor = getattr(connection, "vendor", None)
    if vendor is not None:
        return vendor == "sqlite"
    engine = (getattr(connection, "settings_dict", None) or {}).get("ENGINE", "")
    return str(engine).endswith(_SQLITE_ENGINE_SUFFIX)


def _execute_pragma(connection, statement: str):
    """执行单条 PRAGMA，返回其首个标量结果（无结果返回 None）。失败只 warning。"""
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            try:
                row = cursor.fetchone()
            except Exception:  # noqa: BLE001 - 无结果集的 PRAGMA（如 synchronous）
                row = None
        if row and len(row) > 0:
            return row[0]
        return None
    except Exception:  # noqa: BLE001 - 单条失败不阻断连接
        logger.warning(
            "SQLite PRAGMA 执行失败（已忽略，不阻断连接）：%s", statement, exc_info=True
        )
        return None


def _summarize(results: dict) -> str:
    parts = []
    for statement, value in results.items():
        text = str(statement).strip()
        if text.upper().startswith("PRAGMA "):
            text = text[len("PRAGMA "):]
        parts.append(f"{text}→{value}" if value is not None else text)
    return "; ".join(parts)


def _report(connection, results: dict) -> None:
    """启动日志：明确说明**哪个库**的 WAL 是否生效（首次 INFO，之后同进程内降为 DEBUG）。"""
    journal = None
    for statement, value in results.items():
        if str(statement).strip().upper().startswith("PRAGMA JOURNAL_MODE"):
            journal = value
            break
    expected = getattr(django_settings, "SQLITE_JOURNAL_MODE", "WAL")
    is_wal = str(journal).strip().lower() == "wal" if journal is not None else False
    detail = _summarize(results)
    db_name = (getattr(connection, "settings_dict", None) or {}).get("NAME")
    if _mark_note_once("applied"):
        if is_wal:
            logger.info("SQLite 调优生效：WAL 已打开（db=%s；%s）", db_name, detail)
        else:
            logger.warning(
                "SQLite 调优未生效 WAL：db=%s journal_mode=%s（期望 %s）；已执行：%s。"
                "请确认数据库位于本地磁盘（网络盘/NFS 上 WAL 不可用）",
                db_name,
                journal,
                expected,
                detail,
            )
    else:
        logger.debug("SQLite PRAGMA 已应用（db=%s journal_mode=%s）：%s", db_name, journal, detail)


def apply_sqlite_pragmas(sender=None, connection=None, **kwargs) -> dict:
    """`connection_created` 处理器：对每条新建的 sqlite **文件**连接下发 PRAGMA。

    返回 `{语句: 返回值}`（跳过时返回 `{}`），便于排查与测试断言。
    """
    if connection is None:
        return {}
    try:
        enabled = bool(getattr(django_settings, "SQLITE_TUNING_ENABLED", False))
    except Exception:  # noqa: BLE001 - settings 未配置时不应炸
        return {}
    if not enabled:
        if _mark_note_once("disabled"):
            logger.info(
                "SQLite 调优已关闭（SQLITE_TUNING_ENABLED=false）：跳过 PRAGMA，"
                "WAL 未生效，行为同改造前"
            )
        return {}
    if not _is_sqlite(connection):
        if _mark_note_once("non-sqlite"):
            logger.info(
                "非 sqlite 后端（%s）：跳过 SQLite PRAGMA 调优（WAL 不适用）",
                getattr(connection, "vendor", "unknown"),
            )
        return {}
    name = (getattr(connection, "settings_dict", None) or {}).get("NAME")
    if is_in_memory_database(name):
        if _mark_note_once("in-memory"):
            logger.debug(
                "sqlite 内存库（NAME=%r）：跳过 PRAGMA 调优（WAL 不适用，测试库即内存库）",
                name,
            )
        return {}

    statements = tuple(getattr(django_settings, "SQLITE_PRAGMA_STATEMENTS", ()) or ())
    results: dict = {}
    for statement in statements:
        results[str(statement)] = _execute_pragma(connection, str(statement))
    _report(connection, results)
    return results


def connect_db_pragmas() -> bool:
    """幂等接线：把 `apply_sqlite_pragmas` 挂到 `connection_created`。

    在 `CommonConfig.ready()` 调用。`dispatch_uid` + 模块标志双重去重：
    ready() 在测试/自动重载等场景可能被多次触发，重复 connect 会导致同一条连接
    收到多次 PRAGMA（日志重复、无谓开销）。返回 True 表示"已接线（本次或更早）"。
    """
    global _wired
    with _wire_lock:
        if _wired:
            return True
        connection_created.connect(
            apply_sqlite_pragmas,
            dispatch_uid="apps.common.db_pragmas.apply_sqlite_pragmas",
        )
        _wired = True
    logger.debug("SQLite PRAGMA 信号已接线（connection_created）")
    return True
