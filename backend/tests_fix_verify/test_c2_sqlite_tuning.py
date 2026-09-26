# -*- coding: utf-8 -*-
"""C2 阶段 1 验证：SQLite PRAGMA 调优 + 审计降噪 + audit_archive。

覆盖（对应 `docs/运维约束整改设计说明.md` §3/§6 与 task-3 的 5 条交付）：
1. 新连接 PRAGMA：`journal_mode=wal`、`busy_timeout=20000`、`synchronous=1(NORMAL)`；
   且**故意不给** `OPTIONS["timeout"]`——证明生效的是 `connection_created` 信号而非 OPTIONS；
2. `SQLITE_TUNING_ENABLED=false` → 回到改造前（delete / 5000 / 2）；
3. 单条 PRAGMA 失败只 warning，不阻断连接、不影响后续语句；非 sqlite 后端 / 内存库安全跳过；
   接线幂等（`connect_db_pragmas()` 重复调用不会重复连信号）；
4. 审计降噪：sampled 下 4xx 采样两向（random=1.0 不落库 / 0.0 落库）、5xx 恒落库、
   all/off 两端、三种模式都写 logger（method/path/status/operator）、
   `log_write` 业务写审计**永远落库**；
5. `audit_archive`：--dry-run 不删、导出后只删超期行、导出失败不删且非零退出、
   --no-archive、--batch-size 分批、默认 --days 取 settings.AUDIT_RETENTION_DAYS、
   默认输出目录 <BASE_DIR>/logs/audit_archive、WAL 下不跑 VACUUM。

安全：PRAGMA 用例全部使用**工作区内临时文件库**（`backend/.c2_test_tmp/`），
绝不触碰 `backend/db.sqlite3`；Archive 用例用 Django 测试库（内存库）建行、临时目录收归档。
"""
from __future__ import annotations

import gzip
import json
import shutil
import threading
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.db.backends.signals import connection_created
from django.db.utils import ConnectionHandler
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.common import audit as audit_module
from apps.common.audit import (
    log_exception,
    log_write,
    should_persist_http_error,
)
from apps.common.db_pragmas import (
    connect_db_pragmas,
    is_in_memory_database,
)

#: 临时库根目录：**放在仓库工作区内**（系统临时目录在受限沙箱下可能不可写），
#: 且必须是独立文件库——这样才能观察到 journal_mode/synchronous 的真实落地值。
TMP_ROOT = Path(__file__).resolve().parent.parent / ".c2_test_tmp"


def _new_connection(name, options=None):
    """用独立的 ConnectionHandler 建一条连接（不污染 django.db.connections）。

    独立 handler 的 settings 必须含 "default" 键（Django 校验），这里就借用它。
    """
    handler = ConnectionHandler(
        {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(name),
                "OPTIONS": dict(options or {}),
            }
        }
    )
    conn = handler["default"]
    conn.ensure_connection()
    return conn


def _pragma(conn, name: str):
    with conn.cursor() as cursor:
        cursor.execute(f"PRAGMA {name}")
        row = cursor.fetchone()
    return row[0] if row else None


class _FakeConnection:
    """非 sqlite 后端的替身：一旦被调用 cursor() 就说明跳过错失效。"""

    def __init__(self, engine="django.db.backends.postgresql", vendor="postgresql"):
        self.settings_dict = {"ENGINE": engine, "NAME": "gipfel"}
        if vendor is not None:
            self.vendor = vendor

    def cursor(self):  # pragma: no cover - 走到这里即为失败
        raise AssertionError("非 sqlite 后端不应执行 SQLite PRAGMA")


class SqlitePragmaTuningTests(SimpleTestCase):
    """PRAGMA 调优：生效 / 关闭 / 失败隔离 / 跳过规则 / 幂等接线。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
        super().tearDownClass()

    def test_new_connection_applies_wal_and_busy_timeout(self):
        """核心收益：新连接 journal_mode=wal、busy_timeout=20000、synchronous=NORMAL。

        OPTIONS 故意留空（sqlite3 默认 busy_timeout=5000、journal_mode=delete）——
        因此这里观测到的值只可能来自 connection_created 信号处理器。
        """
        path = TMP_ROOT / "pragma_enabled.sqlite3"
        conn = _new_connection(path, options={})
        try:
            self.assertEqual(_pragma(conn, "journal_mode"), "wal")
            self.assertEqual(_pragma(conn, "busy_timeout"), 20000)
            self.assertEqual(_pragma(conn, "synchronous"), 1)  # 1 = NORMAL
            self.assertEqual(_pragma(conn, "wal_autocheckpoint"), 1000)
        finally:
            conn.close()

    def test_disabled_switch_keeps_pre_change_pragmas(self):
        """SQLITE_TUNING_ENABLED=false → 与改造前完全一致（现场秒回退）。"""
        path = TMP_ROOT / "pragma_disabled.sqlite3"
        with override_settings(SQLITE_TUNING_ENABLED=False):
            conn = _new_connection(path, options={})
            try:
                self.assertEqual(_pragma(conn, "journal_mode"), "delete")
                self.assertEqual(_pragma(conn, "busy_timeout"), 5000)  # sqlite3 默认
                self.assertEqual(_pragma(conn, "synchronous"), 2)  # 2 = FULL（默认）
            finally:
                conn.close()

    def test_broken_statement_only_warns_and_loop_continues(self):
        """单条 PRAGMA 失败只 warning：不抛、不阻断连接、后续语句照样生效。"""
        path = TMP_ROOT / "pragma_broken.sqlite3"
        statements = (
            "PRAGMA journal_mode=WAL",
            "THIS IS NOT SQL",  # 必然失败（OperationalError: near "THIS"）
            "PRAGMA busy_timeout=12345",
        )
        with override_settings(SQLITE_PRAGMA_STATEMENTS=statements):
            with self.assertLogs("gipfel", level="WARNING") as captured:
                conn = _new_connection(path, options={})
            try:
                self.assertEqual(_pragma(conn, "busy_timeout"), 12345)
                self.assertEqual(_pragma(conn, "journal_mode"), "wal")
            finally:
                conn.close()
        self.assertTrue(
            any("PRAGMA 执行失败" in line for line in captured.output),
            f"应记录 PRAGMA 失败 warning，实际：{captured.output}",
        )

    def test_in_memory_database_is_skipped(self):
        """内存库（含 Django 测试库 file:...?mode=memory）安全跳过。"""
        conn = _new_connection(":memory:", options={})
        try:
            self.assertEqual(_pragma(conn, "journal_mode"), "memory")
            self.assertEqual(_pragma(conn, "busy_timeout"), 5000)  # 未被改写
        finally:
            conn.close()
        self.assertTrue(is_in_memory_database(":memory:"))
        self.assertTrue(is_in_memory_database("file:memorydb_default?mode=memory&cache=shared"))
        self.assertFalse(is_in_memory_database(str(TMP_ROOT / "x.sqlite3")))

    def test_non_sqlite_backend_is_skipped(self):
        """非 sqlite 后端不执行任何 PRAGMA（否则切库时会报错）。"""
        from apps.common.db_pragmas import apply_sqlite_pragmas

        self.assertEqual(apply_sqlite_pragmas(connection=_FakeConnection()), {})
        # vendor 缺失时退回按 ENGINE 判断，同样跳过
        self.assertEqual(
            apply_sqlite_pragmas(connection=_FakeConnection(vendor=None)), {}
        )
        self.assertEqual(apply_sqlite_pragmas(connection=None), {})

    def test_connect_db_pragmas_is_idempotent(self):
        """幂等接线：ready() 重复触发不会重复挂信号（否则 PRAGMA 会跑多次）。"""
        before = len(connection_created.receivers)
        self.assertTrue(connect_db_pragmas())
        self.assertTrue(connect_db_pragmas())
        self.assertEqual(len(connection_created.receivers), before)

    def test_wiring_is_registered_at_app_ready(self):
        """apps/common/apps.py::ready() 已接线（dispatch_uid 可查）。"""
        uids = [r[0][0] for r in connection_created.receivers]
        self.assertIn("apps.common.db_pragmas.apply_sqlite_pragmas", uids)

    def test_concurrent_writes_no_database_locked(self):
        """调优目标：20 个并发写不出现 database is locked（busy_timeout 20s 生效）。"""
        path = TMP_ROOT / "pragma_concurrent.sqlite3"
        setup = _new_connection(path)
        with setup.cursor() as cursor:
            cursor.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
        setup.close()

        errors: list[Exception] = []
        written = [0]
        lock = threading.Lock()
        per_thread = 25

        def worker(idx: int):
            conn = None
            try:
                conn = _new_connection(path)
                for i in range(per_thread):
                    with conn.cursor() as cursor:
                        cursor.execute("INSERT INTO t (v) VALUES (?)", [f"{idx}-{i}"])
                with lock:
                    written[0] += per_thread
            except Exception as exc:  # noqa: BLE001 - 收集后统一断言
                errors.append(exc)
            finally:
                if conn is not None:
                    conn.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"并发写出现异常：{errors!r}")
        self.assertEqual(written[0], 4 * per_thread)
        check = _new_connection(path)
        try:
            with check.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM t")
                self.assertEqual(cursor.fetchone()[0], 4 * per_thread)
        finally:
            check.close()


class AuditHttpErrorNoiseTests(TestCase):
    """审计降噪：4xx 采样 / 5xx 恒落库 / off / log_write 红线。"""

    def setUp(self):
        AuditLog.objects.all().delete()

    @staticmethod
    def _request(method="GET", path="/api/companies/1"):
        return RequestFactory().generic(
            method, path, HTTP_X_REQUEST_ID="req-c2-1", REMOTE_ADDR="10.9.8.7"
        )

    @staticmethod
    def _response(status_code):
        return SimpleNamespace(status_code=status_code)

    def _errors(self):
        return AuditLog.objects.filter(kind="error")

    # ---------- sampled ----------
    def test_sampled_4xx_miss_is_not_persisted_but_always_logged(self):
        req = self._request(method="GET", path="/api/companies/1")
        with override_settings(
            AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=0.05
        ):
            with mock.patch("apps.common.audit.random") as fake_random:
                fake_random.random.return_value = 1.0  # 必定不命中
                with self.assertLogs("gipfel", level="WARNING") as captured:
                    log_exception(req, ValueError("boom"), self._response(404))
        self.assertEqual(self._errors().count(), 0, "4xx 未命中采样时不应落库")
        joined = "\n".join(captured.output)
        for token in ("GET", "/api/companies/1", "404", "operator=", "mode=sampled"):
            self.assertIn(token, joined, f"降噪后仍必须写日志：缺 {token}（{joined}）")

    def test_sampled_4xx_hit_is_persisted(self):
        req = self._request(method="PUT", path="/api/companies/9")
        with override_settings(
            AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=0.05
        ):
            with mock.patch("apps.common.audit.random") as fake_random:
                fake_random.random.return_value = 0.0  # 必定命中
                log_exception(req, ValueError("bad input"), self._response(400))
        row = self._errors().get()
        self.assertEqual(row.status_code, 400)
        self.assertEqual(row.action, "PUT")
        self.assertEqual(row.error_summary, "bad input")
        self.assertEqual(row.ip, "10.9.8.7")
        self.assertEqual(row.request_id, "req-c2-1")

    def test_sampled_5xx_always_persisted_even_when_sampling_misses(self):
        req = self._request(method="POST", path="/api/contracts/execute")
        with override_settings(
            AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=0.05
        ):
            with mock.patch("apps.common.audit.random") as fake_random:
                fake_random.random.return_value = 1.0  # 采样不命中
                log_exception(req, RuntimeError("db down"), self._response(500))
                fake_random.random.assert_not_called()  # 5xx 根本不走采样
        self.assertEqual(self._errors().count(), 1, "5xx 在 sampled 下必须全量落库")
        self.assertEqual(self._errors().get().status_code, 500)

    def test_unhandled_exception_none_response_treated_as_500(self):
        with override_settings(AUDIT_HTTP_ERROR_MODE="sampled"):
            with mock.patch("apps.common.audit.random") as fake_random:
                fake_random.random.return_value = 1.0
                log_exception(self._request(), RuntimeError("boom"), None)
        self.assertEqual(self._errors().get().status_code, 500)

    # ---------- all / off ----------
    def test_mode_all_is_pre_change_behaviour_without_sampling(self):
        with override_settings(AUDIT_HTTP_ERROR_MODE="all"):
            with mock.patch("apps.common.audit.random") as fake_random:
                log_exception(self._request(), ValueError("x"), self._response(401))
                fake_random.random.assert_not_called()
        self.assertEqual(self._errors().count(), 1, "all=改造前：4xx 全部落库")

    def test_mode_off_persists_nothing_but_still_logs(self):
        with override_settings(AUDIT_HTTP_ERROR_MODE="off"):
            with self.assertLogs("gipfel", level="WARNING") as captured:
                log_exception(self._request(path="/api/a"), ValueError("x"), self._response(403))
                log_exception(self._request(path="/api/b"), RuntimeError("y"), self._response(500))
        self.assertEqual(self._errors().count(), 0, "off：4xx/5xx 都只写日志")
        joined = "\n".join(captured.output)
        self.assertIn("/api/a", joined)
        self.assertIn("/api/b", joined)
        self.assertIn("仅日志（未落库）", joined)

    def test_should_persist_matrix(self):
        with override_settings(AUDIT_HTTP_ERROR_MODE="all"):
            self.assertTrue(should_persist_http_error(400))
            self.assertTrue(should_persist_http_error(500))
        with override_settings(AUDIT_HTTP_ERROR_MODE="off"):
            self.assertFalse(should_persist_http_error(400))
            self.assertFalse(should_persist_http_error(500))
        with override_settings(AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=1.0):
            self.assertTrue(should_persist_http_error(404), "采样率 1.0 = 全采样")
            self.assertTrue(should_persist_http_error(500))
        with override_settings(AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=0.0):
            self.assertFalse(should_persist_http_error(404), "采样率 0.0 = 不采样")
            self.assertTrue(should_persist_http_error(500), "5xx 不受采样率影响")

    # ---------- 红线：业务写审计 ----------
    def test_log_write_always_persisted_in_every_mode(self):
        for mode in ("off", "sampled", "all"):
            with self.subTest(mode=mode):
                AuditLog.objects.filter(kind="write").delete()
                with override_settings(AUDIT_HTTP_ERROR_MODE=mode):
                    log_write(
                        model="Material",
                        action="Material:create",
                        record_id=77,
                        changes={"name": "铁矿石"},
                    )
                self.assertEqual(
                    AuditLog.objects.filter(kind="write", model="Material").count(),
                    1,
                    f"业务写审计是红线，mode={mode} 也必须落库",
                )

    def test_log_write_survives_when_http_error_audit_disabled(self):
        """同一模式下：4xx 不落库，但业务写审计照旧落库。"""
        with override_settings(AUDIT_HTTP_ERROR_MODE="off"):
            with mock.patch("apps.common.audit.random"):
                log_exception(self._request(), ValueError("x"), self._response(400))
                log_write(model="Stock", action="Stock:create", record_id=5, changes={"cash": 1})
        self.assertEqual(AuditLog.objects.filter(kind="error").count(), 0)
        self.assertEqual(AuditLog.objects.filter(kind="write", model="Stock").count(), 1)


class AuditArchiveCommandTests(TestCase):
    """audit_archive：先导出后删除、失败不删、非零退出、批量与默认值。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        AuditLog.objects.all().delete()

    def _make_rows(self, count, age_days, prefix="row"):
        objs = [
            AuditLog.objects.create(
                kind="error",
                action="GET",
                status_code=401,
                error_summary=f"{prefix}-{i}",
            )
            for i in range(count)
        ]
        ids = [o.pk for o in objs]
        AuditLog.objects.filter(pk__in=ids).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
        return ids

    def _out(self):
        return StringIO()

    def _archives(self, directory: Path):
        return sorted(directory.glob("audit-*.jsonl.gz"))

    # ---------- dry-run ----------
    def test_dry_run_neither_exports_nor_deletes(self):
        old_ids = self._make_rows(3, age_days=10)
        fresh_ids = self._make_rows(2, age_days=1, prefix="fresh")
        out_dir = TMP_ROOT / "dry_run_out"
        out = self._out()
        call_command("audit_archive", "--dry-run", "--output-dir", str(out_dir), stdout=out)
        text = out.getvalue()
        self.assertEqual(AuditLog.objects.filter(pk__in=old_ids).count(), 3, "dry-run 不得删行")
        self.assertEqual(AuditLog.objects.filter(pk__in=fresh_ids).count(), 2)
        self.assertFalse(out_dir.exists(), "dry-run 不得创建归档目录/文件")
        self.assertIn("dry-run", text)
        self.assertIn("计划删除 3 行", text)

    # ---------- 导出后删除 ----------
    def test_export_then_delete_only_expired_rows(self):
        old_ids = self._make_rows(3, age_days=10)
        fresh_ids = self._make_rows(2, age_days=1, prefix="fresh")
        out_dir = TMP_ROOT / "export_out"
        out = self._out()
        call_command("audit_archive", "--days", "7", "--output-dir", str(out_dir), stdout=out)
        text = out.getvalue()

        self.assertEqual(AuditLog.objects.filter(pk__in=old_ids).count(), 0, "超期行应删除")
        self.assertEqual(AuditLog.objects.filter(pk__in=fresh_ids).count(), 2, "未超期行必须保留")
        self.assertIn("已删除 3 行", text)

        files = self._archives(out_dir)
        self.assertEqual(len(files), 1, f"应生成 1 个归档：{files}")
        self.assertRegex(files[0].name, r"^audit-\d{8}\.jsonl\.gz$")
        with gzip.open(files[0], "rt", encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual([r["id"] for r in records], old_ids)
        self.assertEqual(records[0]["kind"], "error")
        self.assertEqual(records[0]["status_code"], 401)
        self.assertIsNotNone(records[0]["created_at"])
        self.assertFalse(
            (out_dir / (files[0].name + ".part")).exists(), "临时文件应已原子替换"
        )

    # ---------- 导出失败 → 不删 + 非零退出 ----------
    def test_export_failure_keeps_rows_and_raises_nonzero(self):
        old_ids = self._make_rows(4, age_days=30)
        blocker = TMP_ROOT / "blocked_parent"
        blocker.mkdir(parents=True, exist_ok=True)
        blocked = blocker / "audit_archive"  # 同名普通文件 → mkdir 必失败
        blocked.write_text("not a directory", encoding="utf-8")

        with self.assertRaises(CommandError) as ctx:
            call_command(
                "audit_archive",
                "--days",
                "7",
                "--output-dir",
                str(blocked),
                stdout=self._out(),
            )
        self.assertEqual(ctx.exception.returncode, 1, "导出失败必须以非 0 退出码报错")
        self.assertEqual(
            AuditLog.objects.filter(pk__in=old_ids).count(), 4, "导出失败必须一行都不删"
        )
        self.assertTrue(blocked.is_file(), "失败的导出不得改写目标路径")

    # ---------- --no-archive ----------
    def test_no_archive_deletes_without_exporting(self):
        ids = self._make_rows(2, age_days=20)
        out_dir = TMP_ROOT / "no_archive_out"
        out = self._out()
        call_command(
            "audit_archive",
            "--no-archive",
            "--output-dir",
            str(out_dir),
            stdout=out,
        )
        self.assertEqual(AuditLog.objects.filter(pk__in=ids).count(), 0)
        self.assertFalse(out_dir.exists(), "--no-archive 不应产出归档文件")
        self.assertIn("--no-archive", out.getvalue())

    # ---------- 分批删除 ----------
    def test_batch_size_deletes_all_expired_rows(self):
        ids = self._make_rows(5, age_days=10)
        out_dir = TMP_ROOT / "batch_out"
        out = self._out()
        call_command(
            "audit_archive",
            "--days",
            "7",
            "--batch-size",
            "2",
            "--output-dir",
            str(out_dir),
            stdout=out,
        )
        self.assertEqual(AuditLog.objects.filter(pk__in=ids).count(), 0)
        self.assertIn("已删除 5/5 行", out.getvalue())

    # ---------- 默认值 ----------
    def test_defaults_come_from_settings_and_base_dir(self):
        """--days 默认取 AUDIT_RETENTION_DAYS；输出目录默认 <BASE_DIR>/logs/audit_archive。"""
        old_ids = self._make_rows(2, age_days=10, prefix="old")
        mid_ids = self._make_rows(1, age_days=3, prefix="mid")
        with override_settings(AUDIT_RETENTION_DAYS=5, BASE_DIR=TMP_ROOT / "basedir"):
            out = self._out()
            call_command("audit_archive", stdout=out)
        self.assertEqual(AuditLog.objects.filter(pk__in=old_ids).count(), 0)
        self.assertEqual(
            AuditLog.objects.filter(pk__in=mid_ids).count(), 1, "3 天前的行在保留期(5 天)内"
        )
        expected_dir = TMP_ROOT / "basedir" / "logs" / "audit_archive"
        files = self._archives(expected_dir)
        self.assertEqual(len(files), 1, f"默认目录应为 <BASE_DIR>/logs/audit_archive：{files}")

    def test_invalid_options_are_rejected_without_deleting(self):
        ids = self._make_rows(2, age_days=10)
        with self.assertRaises(CommandError):
            call_command("audit_archive", "--days", "0", stdout=self._out())
        with self.assertRaises(CommandError):
            call_command("audit_archive", "--batch-size", "0", stdout=self._out())
        self.assertEqual(AuditLog.objects.filter(pk__in=ids).count(), 2, "参数非法不得删行")

    def test_no_expired_rows_is_a_noop(self):
        ids = self._make_rows(2, age_days=1)
        out_dir = TMP_ROOT / "empty_out"
        out = self._out()
        call_command("audit_archive", "--days", "7", "--output-dir", str(out_dir), stdout=out)
        self.assertEqual(AuditLog.objects.filter(pk__in=ids).count(), 2)
        self.assertFalse(out_dir.exists())
        self.assertIn("没有超期数据", out.getvalue())

    # ---------- WAL 下不 VACUUM ----------
    def test_never_runs_vacuum_and_skips_checkpoint_on_non_wal(self):
        """命令不得执行 VACUUM（WAL 下会整库独占重写）；非 WAL 库安全跳过 checkpoint。"""
        self._make_rows(1, age_days=10)
        executed: list[str] = []
        real_cursor = connections["default"].cursor

        def spy_cursor(*args, **kwargs):
            cursor = real_cursor(*args, **kwargs)
            real_execute = cursor.execute

            def execute(sql, params=None):
                executed.append(str(sql))
                if params is None:
                    return real_execute(sql)
                return real_execute(sql, params)

            cursor.execute = execute
            return cursor

        out = self._out()
        with mock.patch.object(connections["default"], "cursor", side_effect=spy_cursor):
            call_command(
                "audit_archive",
                "--days",
                "7",
                "--no-archive",
                stdout=out,
            )
        self.assertTrue(executed, "应实际执行过 SQL")
        self.assertFalse(
            [sql for sql in executed if "VACUUM" in sql.upper()],
            f"WAL 下禁止 VACUUM，实际执行：{executed}",
        )
        # 测试库是内存库（journal_mode=memory）→ checkpoint 安全跳过
        self.assertIn("跳过 wal_checkpoint", out.getvalue())
