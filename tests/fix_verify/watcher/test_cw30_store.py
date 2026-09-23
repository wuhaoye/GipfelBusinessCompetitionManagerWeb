# -*- coding: utf-8 -*-
"""N-01 验证：contract_watcher 的 SQLite 分账存储层（`store.py`）。

要求映射：
- 「按公司 companyId 分开记账，先进入 SQLite 数据库」→ `upsert_contract` 以
  (company_id, contract_id) 为主键，同一合同对两家公司入库得到两行；重复入库幂等；
- 「用户只能记录有权限管理的公司」→ `manageable` 标记 + `selected`（记账目标）；
- 「阈值 / 财年结束 / 手动」→ 未记账合同（booked_at IS NULL）、批次、手动请求表；
- 财年更迭 → `sync_fiscal_years` 由本地前后状态推导 FY_START / FY_END。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe -m unittest tests.fix_verify.watcher.test_cw30_store -v
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cw_helpers import TempDir, load_sibling, make_contract  # noqa: E402

store = load_sibling("store")


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(lambda: self._tmp.__exit__(None, None, None))
        self.conn = store.open_db(self.tmp / "watcher.db")

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


class ContractBookkeepingTests(StoreTestCase):
    def test_schema_tables_created(self):
        names = {
            r["name"]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table in ("companies", "contracts", "batches", "batch_contracts",
                      "flush_requests", "fiscal_years", "meta", "events"):
            self.assertIn(table, names)

    def test_contract_is_split_per_company(self):
        """同一份合同涉及两家公司 ⇒ 两行（按 companyId 分开记账）。"""
        contract = make_contract(7, company_id=1)
        contract["parties"].append(
            {"role": "乙方", "companyId": 2, "companyName": "公司2", "contractNumber": "HT-B"}
        )
        self.assertTrue(store.upsert_contract(self.conn, contract, 1))
        self.assertTrue(store.upsert_contract(self.conn, contract, 2))
        self.assertEqual(store.pending_count(self.conn, 1), 1)
        self.assertEqual(store.pending_count(self.conn, 2), 1)
        rows = store.contract_rows_by_ids(self.conn, 2, [7])
        self.assertEqual(rows[0]["contract_number"], "HT-B", "合同编号取该公司自己的参与方编号")

    def test_upsert_is_idempotent_and_keeps_booked_mark(self):
        contract = make_contract(11, company_id=3)
        self.assertTrue(store.upsert_contract(self.conn, contract, 3))
        batch = store.create_batch(self.conn, 3, trigger="manual", contract_ids=[11])
        store.mark_contracts_booked(self.conn, 3, [11], batch, "已记")
        # 同一合同再次入库（例如详情刷新）：不得新增行、不得清掉已记账标记
        self.assertFalse(store.upsert_contract(self.conn, contract, 3))
        self.assertEqual(store.pending_count(self.conn, 3), 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM contracts").fetchone()["n"], 1
        )
        row = store.contract_rows_by_ids(self.conn, 3, [11])[0]
        self.assertEqual(row["book_note"], "已记")

    def test_amount_kept_as_decimal_string(self):
        """大额金额不得退化成 float（要求 2 的金额元数据）。"""
        big = "12345678901234567890.123456789"
        store.upsert_contract(self.conn, make_contract(5, company_id=1, amount=big), 1)
        row = store.contract_rows_by_ids(self.conn, 1, [5])[0]
        self.assertEqual(row["amount"], big)
        self.assertEqual(Decimal(row["amount"]), Decimal(big))

    def test_company_contracts_filter_and_order(self):
        for i, cid in enumerate([1, 2, 3], start=1):
            store.upsert_contract(
                self.conn,
                make_contract(i, company_id=1, name=f"合同{i}", number=f"HT-{i}",
                              executed_at=f"2026-09-0{i}T00:00:00Z"),
                1,
            )
        rows = store.company_contracts(self.conn, 1, limit=10)
        self.assertEqual([r["contract_id"] for r in rows], [3, 2, 1], "默认新→旧")
        hit = store.company_contracts(self.conn, 1, keyword="HT-2")
        self.assertEqual([r["contract_id"] for r in hit], [2])
        pending = store.company_contracts(self.conn, 1, only_pending=True)
        self.assertEqual(len(pending), 3)
        self.assertEqual(store.company_contracts(self.conn, 2), [])

    def test_readable_json_saved_once_per_contract(self):
        store.upsert_contract(self.conn, make_contract(9, company_id=1), 1,
                              readable={"合同ID": 9, "状态": "已执行"})
        row = store.contract_rows_by_ids(self.conn, 1, [9])[0]
        self.assertIn("已执行", row["readable_json"])


class CompanyScopeTests(StoreTestCase):
    def _sync(self, manageable_ids):
        rows = [
            {"id": 1, "name": "甲", "competitionId": 189},
            {"id": 2, "name": "乙", "competitionId": 189},
            {"id": 3, "name": "丙", "competitionId": 190},
        ]
        return store.sync_companies(self.conn, rows, manageable_ids)

    def test_only_manageable_companies_can_be_selected(self):
        self._sync({1, 2})
        self.assertEqual(store.manageable_ids(self.conn), [1, 2])
        # 首次同步默认全选「可管理」的公司；范围外的公司即使是 selected 也不参与记账
        self.assertEqual(store.selected_ids(self.conn), [1, 2])
        store.set_company_selected(self.conn, 3, True)
        self.assertEqual(store.selected_ids(self.conn), [1, 2],
                         "范围外的公司不得成为记账目标")

    def test_selection_is_preserved_across_sync(self):
        self._sync({1, 2})
        store.set_company_selected(self.conn, 1, False)
        self._sync({1, 2})
        self.assertEqual(store.selected_ids(self.conn), [2], "再次同步不得覆盖用户选择")

    def test_empty_scope_means_no_company(self):
        self._sync(set())
        self.assertEqual(store.manageable_ids(self.conn), [])
        self.assertEqual(store.selected_ids(self.conn), [])

    def test_overview_reports_pending_and_last_batch(self):
        self._sync({1})
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)
        batch = store.create_batch(self.conn, 1, trigger="threshold", contract_ids=[])
        store.finish_batch(self.conn, batch, status="success", entry_count=2)
        overview = {row["company_id"]: row for row in store.company_overview(self.conn)}
        self.assertEqual(overview[1]["pending"], 1)
        self.assertEqual(overview[1]["last_batch_status"], "success")
        self.assertTrue(overview[1]["selected"])


class BatchAndManualRequestTests(StoreTestCase):
    def test_batch_lifecycle(self):
        batch = store.create_batch(self.conn, 1, competition_id=189, trigger="threshold",
                                   requested_by="auto", contract_ids=[1, 2], book_path="x.xlsx")
        self.assertEqual(store.batch_contract_ids(self.conn, batch), [1, 2])
        store.mark_batch_running(self.conn, batch)
        self.assertEqual([r["id"] for r in store.running_batches(self.conn)], [batch])
        store.finish_batch(self.conn, batch, status="success", entry_count=3, message="ok")
        self.assertEqual(store.running_batches(self.conn), [])
        row = store.last_batch(self.conn, 1)
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["entry_count"], 3)

    def test_manual_request_queue(self):
        rid = store.request_flush(self.conn, company_id=5, trigger="manual", requested_by="gui")
        pending = store.pending_flush_requests(self.conn)
        self.assertEqual([r["id"] for r in pending], [rid])
        self.assertEqual(pending[0]["company_id"], 5)
        store.finish_flush_request(self.conn, rid, "done", "已处理")
        self.assertEqual(store.pending_flush_requests(self.conn), [])
        row = self.conn.execute("SELECT * FROM flush_requests WHERE id = ?", (rid,)).fetchone()
        self.assertEqual(row["status"], "done")

    def test_unbook_contracts_for_manual_recheck(self):
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)
        batch = store.create_batch(self.conn, 1, trigger="manual", contract_ids=[1])
        store.mark_contracts_booked(self.conn, 1, [1], batch, "note")
        self.assertEqual(store.pending_count(self.conn, 1), 0)
        self.assertEqual(store.unbook_contracts(self.conn, 1, [1]), 1)
        self.assertEqual(store.pending_count(self.conn, 1), 1)


class FiscalYearTransitionTests(StoreTestCase):
    """财年更迭推导：本地前后两次状态比对（后端只提供行级 updatedAt 变化）。"""

    def test_first_sync_is_baseline_without_transition(self):
        rows = [{"id": 1, "year": 2026, "status": "ACTIVE", "updatedAt": "T1"}]
        self.assertEqual(store.sync_fiscal_years(self.conn, 189, rows), [],
                         "首次同步只落状态，不得把「进行中的财年」当成 FY_START 触发记账")
        self.assertTrue(store.fy_baseline_done(self.conn, 189))

    def test_close_year_reports_end(self):
        store.sync_fiscal_years(self.conn, 189, [{"id": 1, "year": 2026, "status": "ACTIVE"}])
        found = store.sync_fiscal_years(
            self.conn, 189, [{"id": 1, "year": 2026, "status": "CLOSED"}]
        )
        self.assertEqual([t["transition"] for t in found], ["FY_END"])
        self.assertEqual(found[0]["year"], 2026)

    def test_new_active_year_reports_start(self):
        store.sync_fiscal_years(self.conn, 189, [{"id": 1, "year": 2026, "status": "CLOSED"}])
        found = store.sync_fiscal_years(
            self.conn, 189,
            [{"id": 1, "year": 2026, "status": "CLOSED"},
             {"id": 2, "year": 2027, "status": "ACTIVE"}],
        )
        self.assertEqual([t["transition"] for t in found], ["FY_START"])

    def test_deleted_active_year_reports_end(self):
        store.sync_fiscal_years(self.conn, 189, [{"id": 1, "year": 2026, "status": "ACTIVE"}])
        found = store.sync_fiscal_years(self.conn, 189, [], existing_ids=[])
        self.assertEqual([t["transition"] for t in found], ["FY_END"])
        self.assertEqual(store.fiscal_years(self.conn, 189), [])

    def test_no_transition_when_status_unchanged(self):
        rows = [{"id": 1, "year": 2026, "status": "ACTIVE"}]
        store.sync_fiscal_years(self.conn, 189, rows)
        self.assertEqual(store.sync_fiscal_years(self.conn, 189, rows), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
