# -*- coding: utf-8 -*-
"""N-03 验证：角色门禁 / 公司范围 / 三种记账触发（阈值、财年更迭、手动）。

要求映射：
1. 「只允许 COMPETITION_ADMIN 和 SUPER_ADMIN 用户使用，PLAYER 默认关闭」；
2. 「按公司 companyId 分开记账，先进入 SQLite；用户只能记录有权限管理的公司，
   同时能选择记录的公司」；
3. 「达到设定值 / 财年结束 / 手动要求时才写 xlsx（其余时候不调用 shang.py）」。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe -m unittest tests.fix_verify.watcher.test_cw32_watcher_workflow -v
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cw_helpers import TempDir, load_sibling, load_watcher, make_contract  # noqa: E402

store = load_sibling("store")


class PureFunctionTests(unittest.TestCase):
    """角色门禁与公司范围判定的纯函数口径。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.tmp = cls._tmp.__enter__()
        cls.cw = load_watcher(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_role_gate_allows_admin_roles(self):
        for role in ("SUPER_ADMIN", "COMPETITION_ADMIN"):
            with self.subTest(role=role):
                self.assertIsNone(self.cw.find_role_denial(role))

    def test_role_gate_blocks_player_by_default(self):
        reason = self.cw.find_role_denial("PLAYER")
        self.assertIsNotNone(reason, "PLAYER 默认必须被拒绝")
        self.assertIn("--allow-player", reason)

    def test_role_gate_allows_player_when_explicitly_enabled(self):
        self.assertIsNone(self.cw.find_role_denial("PLAYER", allow_player=True))

    def test_role_gate_blocks_unknown_roles(self):
        self.assertIsNotNone(self.cw.find_role_denial("AUDITOR"))

    def test_role_gate_allows_missing_role_for_compatibility(self):
        """登录响应没有 role 字段（自定义后端/测试替身）时不拦，只由调用方告警。"""
        self.assertIsNone(self.cw.find_role_denial(None))

    def test_manageable_ids_from_company_scopes(self):
        self.assertEqual(self.cw.resolve_manageable_ids("COMPETITION_ADMIN", [3, 5]), {3, 5})
        self.assertEqual(self.cw.resolve_manageable_ids("COMPETITION_ADMIN", []), set(),
                         "公司管理范围为空 ⇒ 没有任何可记录公司")
        self.assertIsNone(self.cw.resolve_manageable_ids("SUPER_ADMIN", []),
                          "超管不受公司范围限制")

    def test_parse_company_ids(self):
        self.assertIsNone(self.cw.parse_company_ids(None))
        self.assertIsNone(self.cw.parse_company_ids("all"))
        self.assertEqual(self.cw.parse_company_ids(""), [])
        self.assertEqual(self.cw.parse_company_ids("1, 2 ,3"), [1, 2, 3])
        self.assertEqual(self.cw.parse_company_ids([4, "5"]), [4, 5])

    def test_party_company_ids_skips_host_dedupes_and_rejects_non_numeric(self):
        """参与方公司 id 只接受数值（与后端 _parties_list_in_scopes 同口径）。"""
        contract = {
            "parties": [
                {"role": "甲方", "companyId": 3},
                {"role": "主办方", "isHost": True, "companyId": None},
                {"role": "乙方", "companyId": 3},
                {"role": "丙方", "companyId": "7"},
            ]
        }
        self.assertEqual(self.cw.party_company_ids(contract), [3])


class FakeWorkflowBackend:
    """带公司/财年/合同的最小后端替身。"""

    def __init__(self, *, role="COMPETITION_ADMIN", scopes=(1, 2), companies=None,
                 rows=(), details=None, fiscal_years=None, competition_id=189):
        self.role = role
        self.user_info = {"role": role, "companyScopes": list(scopes), "competitionId": competition_id}
        self.competition_id = competition_id
        self._companies = companies if companies is not None else [
            {"id": 1, "name": "公司一", "competitionId": competition_id},
            {"id": 2, "name": "公司二", "competitionId": competition_id},
            {"id": 99, "name": "范围外", "competitionId": competition_id},
        ]
        self.rows = list(rows)
        self.details = details or {}
        self.fiscal_years = list(fiscal_years or [])
        self.fy_calls: list = []
        self.last_server_time = None

    @property
    def company_scopes(self):
        return list(self.user_info.get("companyScopes") or [])

    def login(self):
        return None

    def fetch_contract_types(self):
        return []

    def fetch_companies(self, competition_id=None):
        return list(self._companies)

    def fetch_executed_ids(self, competition_id=None, updated_after=None):
        return list(self.rows)

    def fetch_contract_detail(self, contract_id):
        return self.details[contract_id]

    def fetch_fiscal_years(self, competition_id, updated_after=None, previous_ids=None):
        self.fy_calls.append((competition_id, updated_after, previous_ids))
        return {
            "items": list(self.fiscal_years),
            "existingIds": [r["id"] for r in self.fiscal_years],
            "serverTime": f"2026-09-10T00:00:{len(self.fy_calls):02d}Z",
            "incremental": True,
        }


class WorkflowTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(lambda: self._tmp.__exit__(None, None, None))
        self.cw = load_watcher(self.tmp)
        self.conn = store.open_db(self.tmp / "watcher.db")
        self.addCleanup(lambda: self.conn.close())
        store.sync_companies(
            self.conn,
            [
                {"id": 1, "name": "公司一", "competitionId": 189},
                {"id": 2, "name": "公司二", "competitionId": 189},
                {"id": 99, "name": "范围外", "competitionId": 189},
            ],
            {1, 2},
        )

    def _session(self, backend, **kwargs):
        self.cw.STATE_FILE = self.tmp / "data" / "state.json"
        params = dict(
            backend=backend, state={}, registry=[{}], out_dir=self.tmp / "records",
            competition_id=189, conn=self.conn, threshold=kwargs.pop("threshold", 3),
            books_dir=self.tmp / "books", book_template=self.tmp / "template.xlsx",
            fiscal_year_interval=0,
        )
        params.update(kwargs)
        return self.cw.WatcherSession(**params)


class CollectorTests(WorkflowTestCase):
    def test_collector_records_only_manageable_companies(self):
        collect = self.cw.make_contract_collector(self.conn)
        contract = make_contract(5, company_id=1)
        contract["parties"].append(
            {"role": "乙方", "companyId": 99, "companyName": "范围外", "contractNumber": "X"}
        )
        recorded = collect(contract)
        self.assertEqual(recorded, [1], "范围外公司必须被跳过")
        self.assertEqual(store.pending_count(self.conn, 1), 1)
        self.assertEqual(store.pending_count(self.conn, 99), 0)

    def test_collector_is_idempotent(self):
        collect = self.cw.make_contract_collector(self.conn)
        contract = make_contract(5, company_id=1)
        collect(contract)
        collect(contract)
        self.assertEqual(store.pending_count(self.conn, 1), 1)

    def test_collector_without_scope_records_nothing(self):
        """公司管理范围为空 ⇒ 无可记录公司（宁可少记，不可越权记）。"""
        store.set_selected_companies(self.conn, store.manageable_ids(self.conn), False)
        self.conn.execute("UPDATE companies SET manageable = 0")
        self.conn.commit()
        collect = self.cw.make_contract_collector(self.conn)
        self.assertEqual(collect(make_contract(6, company_id=1)), [])
        self.assertEqual(store.pending_count(self.conn, 1), 0)


class FakeLock:
    """单实例锁替身：只统计心跳次数。"""

    def __init__(self):
        self.beats = 0

    def heartbeat(self):
        self.beats += 1


class TriggerTests(WorkflowTestCase):
    def _seed_pending(self, count, company_id=1):
        for i in range(1, count + 1):
            store.upsert_contract(self.conn, make_contract(i, company_id=company_id), company_id)

    def test_lock_is_heartbeaten_during_flush(self):
        """长批次（一次 Excel 会话可能几分钟）期间必须刷新单实例锁心跳。"""
        self._seed_pending(2)
        lock = FakeLock()
        session = self._session(FakeWorkflowBackend(), threshold=2, lock=lock)
        session.check_thresholds()
        self.assertGreaterEqual(lock.beats, 1, "记账期间必须刷新锁心跳，否则锁会被别的实例接管")
        # 正常结束时也刷一次（run_round 末尾）
        session.run_round()
        self.assertGreaterEqual(lock.beats, 2)

    def test_threshold_trigger_writes_once(self):
        self._seed_pending(3)
        session = self._session(FakeWorkflowBackend(), threshold=3)
        summaries = session.check_thresholds()
        self.assertEqual(len(summaries), 1)
        batch = store.last_batch(self.conn, 1)
        self.assertEqual(batch["trigger"], "threshold")
        self.assertEqual(batch["contract_count"], 3)
        self.assertEqual(store.pending_count(self.conn, 1), 0, "记账后待记账数清零")

    def test_threshold_not_reached_does_nothing(self):
        self._seed_pending(2)
        session = self._session(FakeWorkflowBackend(), threshold=3)
        self.assertEqual(session.check_thresholds(), [])
        self.assertEqual(store.last_batch(self.conn, 1), None)

    def test_unselected_company_never_triggers(self):
        self._seed_pending(5, company_id=2)
        store.set_company_selected(self.conn, 2, False)
        session = self._session(FakeWorkflowBackend(), threshold=1)
        self.assertEqual(session.check_thresholds(), [])
        self.assertEqual(store.pending_count(self.conn, 2), 5, "未选中的公司不得被记账")

    def test_manual_request_triggers_selected_company(self):
        self._seed_pending(1)
        store.upsert_contract(self.conn, make_contract(9, company_id=2), 2)
        rid = store.request_flush(self.conn, company_id=None, competition_id=189,
                                  trigger="manual", requested_by="gui")
        session = self._session(FakeWorkflowBackend())
        session.consume_flush_requests()
        self.assertEqual(store.pending_count(self.conn, 1), 0)
        self.assertEqual(store.pending_count(self.conn, 2), 0)
        self.assertEqual(store.last_batch(self.conn, 1)["trigger"], "manual")
        self.assertEqual(store.last_batch(self.conn, 2)["trigger"], "manual")
        row = self.conn.execute("SELECT * FROM flush_requests WHERE id = ?", (rid,)).fetchone()
        self.assertEqual(row["status"], "done")
        self.assertIn("manual", row["message"])

    def test_manual_request_for_single_company(self):
        self._seed_pending(1, company_id=1)
        self._seed_pending(1, company_id=2)
        store.request_flush(self.conn, company_id=1, trigger="manual", requested_by="gui")
        session = self._session(FakeWorkflowBackend())
        session.consume_flush_requests()
        self.assertEqual(store.pending_count(self.conn, 1), 0)
        self.assertEqual(store.pending_count(self.conn, 2), 1, "只处理被点名的公司")

    def test_fiscal_year_end_triggers_flush(self):
        backend = FakeWorkflowBackend(
            fiscal_years=[{"id": 1, "year": 2026, "status": "ACTIVE", "updatedAt": "T1"}]
        )
        session = self._session(backend)
        self.assertEqual(session.poll_fiscal_years(), [], "首次同步只是建立基线")
        self._seed_pending(1)
        backend.fiscal_years = [{"id": 1, "year": 2026, "status": "CLOSED", "updatedAt": "T2"}]
        summaries = session.poll_fiscal_years()
        self.assertEqual(len(summaries), 1)
        batch = store.last_batch(self.conn, 1)
        self.assertEqual(batch["trigger"], "fiscal_year_end")
        self.assertEqual(store.pending_count(self.conn, 1), 0)

    def test_fiscal_year_start_also_flushes_pending(self):
        backend = FakeWorkflowBackend(
            fiscal_years=[{"id": 1, "year": 2026, "status": "CLOSED", "updatedAt": "T1"}]
        )
        session = self._session(backend)
        session.poll_fiscal_years()
        self._seed_pending(1)
        backend.fiscal_years = [
            {"id": 1, "year": 2026, "status": "CLOSED", "updatedAt": "T1"},
            {"id": 2, "year": 2027, "status": "ACTIVE", "updatedAt": "T2"},
        ]
        summaries = session.poll_fiscal_years()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(store.last_batch(self.conn, 1)["trigger"], "fiscal_year_start")

    def test_fiscal_year_interval_throttles(self):
        backend = FakeWorkflowBackend(
            fiscal_years=[{"id": 1, "year": 2026, "status": "ACTIVE", "updatedAt": "T1"}]
        )
        session = self._session(backend, fiscal_year_interval=60.0)
        session.poll_fiscal_years()
        session.poll_fiscal_years()
        self.assertEqual(len(backend.fy_calls), 1, "财年轮询必须有最小间隔（默认 60s）")

    def test_no_excel_when_auto_bookkeeping_disabled(self):
        self._seed_pending(5)
        session = self._session(FakeWorkflowBackend(), threshold=1, auto_bookkeeping=False,
                                fiscal_year_flush=False)
        self.assertEqual(session.post_process(), [])
        self.assertEqual(store.pending_count(self.conn, 1), 5, "关闭自动记账后只能手动触发")

    def test_flush_failure_backs_off_instead_of_opening_excel_every_round(self):
        """记账失败后 60 秒内不自动重试（否则每 3 秒开一次 Excel ⇒ COM 崩溃）。"""
        self._seed_pending(1)

        def handler(contract, ctx):
            ctx["add_entries"](add=1, minus=0, number="N", about="a")

        session = self._session(FakeWorkflowBackend(), threshold=1)
        session.registry[0] = {"demo": handler}
        from _cw_helpers import load_sibling
        bk = load_sibling("bookkeeping")

        attempts = {"n": 0}
        real_open = bk.open_book

        def failing_open(*a, **k):
            attempts["n"] += 1
            raise RuntimeError("模拟 Excel 打不开")

        bk.open_book = failing_open
        try:
            first = session.check_thresholds()
            second = session.check_thresholds()
            self.assertEqual(attempts["n"], 1, "第二次必须被退避拦住，不得再开 Excel")
            self.assertTrue(first and "failed" in first[0], first)
            self.assertEqual(second, [], "退避期间不产生记账动作")
            self.assertEqual(store.pending_count(self.conn, 1), 1, "合同保持未记账等待重试")

            # 手动请求可以越过退避（用户明确要求）
            store.request_flush(self.conn, company_id=1, trigger="manual", requested_by="gui")
            session.consume_flush_requests()
            self.assertEqual(attempts["n"], 2, "手动请求应强制重试一次")
        finally:
            bk.open_book = real_open


class RunRoundIntegrationTests(WorkflowTestCase):
    def test_new_contract_lands_in_sqlite(self):
        """合同通过 → 只入库（零 Excel）；重复轮询不重复入库；达阈值才写账本。"""
        contract = make_contract(1, company_id=1, type_key="demo")
        backend = FakeWorkflowBackend(rows=[(1, "2026-09-10T00:00:00Z")], details={1: contract})
        session = self._session(backend, threshold=10)
        session.state = {"lastExecutedAt": "", "baselineAt": "x"}
        self.assertEqual(session.run_round(), "ok")
        self.assertEqual(store.pending_count(self.conn, 1), 1, "只入库、不记账")
        self.assertIsNone(store.last_batch(self.conn, 1), "未达阈值不得写 xlsx")

        # 再跑一轮：同一合同不得重复入库（幂等），水位也不重复推进
        session.run_round()
        self.assertEqual(store.pending_count(self.conn, 1), 1)
        rows = self.conn.execute("SELECT COUNT(*) AS n FROM contracts").fetchone()["n"]
        self.assertEqual(rows, 1)

        # 把阈值降到 1 → 触发批量记账
        session.threshold = 1
        session.check_thresholds()
        self.assertEqual(store.pending_count(self.conn, 1), 0)
        self.assertEqual(store.last_batch(self.conn, 1)["trigger"], "threshold")


class MainRoleGateTests(unittest.TestCase):
    """走真实 main()：PLAYER 账号必须被拒绝（非零退出码 + 中文提示）。"""

    def _run_main(self, role):
        with TempDir() as tmp:
            cw = load_watcher(tmp)
            backend = FakeWorkflowBackend(role=role)
            cw.Backend = lambda server, username, password: backend
            old_argv = sys.argv
            sys.argv = [
                "contract_watcher.py", "--server", "http://fake", "--username", "u",
                "--password", "p", "--port", "0", "--out-dir", str(tmp / "out"),
            ]
            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    code = cw.main()
            finally:
                sys.argv = old_argv
            return code, err.getvalue()

    def test_player_is_rejected(self):
        code, stderr = self._run_main("PLAYER")
        self.assertEqual(code, 3, f"PLAYER 必须被拒绝启动，stderr={stderr!r}")
        self.assertIn("PLAYER", stderr)

    def test_competition_admin_is_accepted(self):
        """COMPETITION_ADMIN 通过门禁（进入主循环后由 fake 后端触发停止）。"""

        class StopLoop(Exception):
            pass

        with TempDir() as tmp:
            cw = load_watcher(tmp)
            backend = FakeWorkflowBackend(role="COMPETITION_ADMIN")
            cw.Backend = lambda server, username, password: backend
            cw.ensure_handlers_file = lambda: None
            cw.load_handlers = lambda: {}
            cw.sync_catalog = lambda *a, **k: False

            def fake_sleep(_s):
                raise StopLoop()

            real_sleep = cw.time.sleep
            cw.time.sleep = fake_sleep
            old_argv = sys.argv
            sys.argv = [
                "contract_watcher.py", "--server", "http://fake", "--username", "u",
                "--password", "p", "--port", "0", "--out-dir", str(tmp / "out"),
                "--db", str(tmp / "data" / "watcher.db"),
            ]
            try:
                with self.assertRaises(StopLoop):
                    cw.main()
            finally:
                sys.argv = old_argv
                # `cw.time` 就是全局 time 模块：必须还原，避免污染同进程内其它用例
                cw.time.sleep = real_sleep
            # 公司目录已同步：可管理公司 1/2，范围外 99 不入选
            conn = store.open_db(tmp / "data" / "watcher.db")
            try:
                self.assertEqual(store.manageable_ids(conn), [1, 2])
                self.assertEqual(store.selected_ids(conn), [1, 2])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
