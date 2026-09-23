# -*- coding: utf-8 -*-
"""N-02 验证：批量记账（`bookkeeping.py`）——只在触发时开一次 Excel，失败不丢合同。

要求映射：
- 「同一个公司的合同在积累到一个设定值或者在财年结束时和手动要求时记录进入 xlsx 文件，
   也就是调用 shang.py 中数个 add_ 开头的函数所构成的一串操作，其余时候不需要调用它，
   减少 COM 崩溃问题」→ 本文件验证：
   * 没有待记账合同 / 没有处理函数时**一次 Excel 都不开**；
   * N 份合同只开 **1 次** Excel 会话（book_factory 调用次数 == 1）；
   * 处理函数在 `phase="book"` 时通过 ctx 的 add_entries/add_item/add_assets 记账；
   * 中途失败 ⇒ 放弃会话（不保存）、合同保持未记账、批次 failed（下个触发点重试）；
   * 崩溃残留的 running 批次 ⇒ 不自动重放（避免重复入账），标记为需人工核对。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe -m unittest tests.fix_verify.watcher.test_cw31_bookkeeping_batch -v
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from decimal import getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cw_helpers import REPO, FakeBook, TempDir, load_sibling, make_contract  # noqa: E402

store = load_sibling("store")
bookkeeping = load_sibling("bookkeeping")


class BatchTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(lambda: self._tmp.__exit__(None, None, None))
        self.conn = store.open_db(self.tmp / "watcher.db")
        store.sync_companies(
            self.conn,
            [{"id": 1, "name": "测试公司", "competitionId": 189}],
            {1},
        )
        self.books_dir = self.tmp / "books"
        self.factory_calls: list = []

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _factory(self, book=None, fail=False):
        def factory(path):
            self.factory_calls.append(str(path))
            if fail:
                raise RuntimeError("模拟 Excel 打不开（被占用）")
            return book if book is not None else FakeBook()

        return factory

    def _flush(self, registry=None, book=None, factory=None, trigger="threshold"):
        return bookkeeping.flush_company(
            self.conn, 1, trigger=trigger, books_dir=self.books_dir,
            registry={} if registry is None else registry,
            book_factory=factory or self._factory(book),
        )


class NoExcelCases(BatchTestCase):
    def test_nothing_pending_skips_without_excel(self):
        result = self._flush(registry={"demo": lambda c, x: None})
        self.assertEqual(result.status, "skipped")
        self.assertEqual(self.factory_calls, [], "没有待记账合同时不得打开 Excel")
        self.assertEqual(store.recent_batches(self.conn, 1), [])

    def test_no_handler_marks_booked_without_excel(self):
        """没有注册处理函数 ⇒ 不产生分录、也不开 Excel；合同标记为已记账以免反复触发。"""
        for i in (1, 2, 3):
            store.upsert_contract(self.conn, make_contract(i, company_id=1), 1)
        result = self._flush(registry={})
        self.assertEqual(result.status, "success")
        self.assertEqual(result.entry_count, 0)
        self.assertEqual(self.factory_calls, [], "无处理函数（默认不写分录）时不得打开 Excel")
        self.assertEqual(store.pending_count(self.conn, 1), 0)
        row = store.last_batch(self.conn, 1)
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["contract_count"], 3)
        self.assertIn("未打开 Excel", row["message"])

    def test_mixed_handlers_open_excel_once_and_skip_rest(self):
        for i in (1, 2, 3):
            store.upsert_contract(self.conn, make_contract(i, company_id=1), 1)
        calls = []

        def handler(contract, ctx):
            calls.append((contract["id"], ctx["phase"]))
            if ctx["phase"] == "book":
                ctx["add_entries"](add=1, minus=0, number="HT", about="测试")

        result = self._flush(registry={"demo": handler})
        self.assertEqual(len(self.factory_calls), 1, "整批只开一次 Excel")
        self.assertEqual(calls, [(1, "book"), (2, "book"), (3, "book")])
        self.assertEqual(result.entry_count, 3)
        self.assertEqual(store.pending_count(self.conn, 1), 0)


class BatchWriteTests(BatchTestCase):
    def test_one_session_for_many_contracts(self):
        for i in range(1, 6):
            store.upsert_contract(self.conn, make_contract(i, company_id=1), 1)
        book = FakeBook()
        seen = []

        def handler(contract, ctx):
            seen.append(contract["id"])
            ctx["add_entries"](add=100, minus=0, number=f"HT-{contract['id']}", about="货款")
            ctx["add_assets"](type=1, name="银行存款", add=100, minus=0)

        result = self._flush(registry={"demo": handler}, book=book)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(self.factory_calls), 1, "5 份合同必须只开 1 次 Excel（COM 次数 = 1）")
        self.assertEqual(len(seen), 5)
        self.assertEqual(result.entry_count, 10, "每份合同 2 笔分录")
        self.assertEqual([c[0] for c in book.calls].count("entries"), 5)
        self.assertEqual([c[0] for c in book.calls].count("assets"), 5)
        self.assertTrue(book.saved, "批次结束必须保存（save() 内部会关闭并退出 Excel）")
        self.assertFalse(book.discarded)
        self.assertEqual(store.pending_count(self.conn, 1), 0)
        row = store.last_batch(self.conn, 1)
        self.assertEqual(row["entry_count"], 10)
        self.assertEqual(row["trigger"], "threshold")
        self.assertIn("平衡校验", row["message"])
        self.assertIn("right", row["message"])

    def test_book_path_is_per_company(self):
        path = bookkeeping.book_path_for(self.books_dir, 42)
        self.assertEqual(path.name, "company_42.xlsx")
        self.assertEqual(path.parent, self.books_dir)

    def test_failure_keeps_contracts_pending_and_discards_session(self):
        for i in (1, 2):
            store.upsert_contract(self.conn, make_contract(i, company_id=1), 1)
        book = FakeBook(fail_on=2, fail_message="RPC 服务器不可用")

        def handler(contract, ctx):
            ctx["add_entries"](add=1, minus=0, number="HT", about="x")

        result = self._flush(registry={"demo": handler}, book=book)
        self.assertEqual(result.status, "failed")
        self.assertIn("RPC 服务器不可用", result.message)
        self.assertFalse(book.saved, "中途失败绝不允许保存半成品账本")
        self.assertEqual(store.pending_count(self.conn, 1), 2, "失败的合同必须保持未记账以便重试")
        row = store.last_batch(self.conn, 1)
        self.assertEqual(row["status"], "failed")
        self.assertIn("未保存", row["message"])
        events = [e["message"] for e in store.recent_events(self.conn)]
        self.assertTrue(any("批量记账失败" in m for m in events), events)

    def test_open_failure_is_retried_later(self):
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)
        result = self._flush(
            registry={"demo": lambda c, x: x["add_entries"](add=1, minus=0, number="n", about="a")},
            factory=self._factory(fail=True),
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(store.pending_count(self.conn, 1), 1)

    def test_stale_running_batch_is_not_replayed(self):
        """上一次批次停在 running（进程被杀）⇒ 标记需人工核对，不自动重放（防重复入账）。"""
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)
        batch = store.create_batch(self.conn, 1, trigger="threshold", contract_ids=[1],
                                   book_path="company_1.xlsx")
        store.mark_batch_running(self.conn, batch)

        result = self._flush(registry={"demo": lambda c, x: None})
        self.assertEqual(result.status, "skipped", "残留批次处理完后已无待记账合同")
        self.assertEqual(self.factory_calls, [], "存在结果未知的批次时不得再开 Excel 重放")
        old = self.conn.execute("SELECT * FROM batches WHERE id = ?", (batch,)).fetchone()
        self.assertEqual(old["status"], "unknown")
        self.assertIn("人工核对", old["message"])
        marked = store.contract_rows_by_ids(self.conn, 1, [1])[0]
        self.assertIsNotNone(marked["booked_at"])
        self.assertIn("未知", marked["book_note"])
        self.assertEqual(store.pending_count(self.conn, 1), 0, "不得被再次触发")

    def test_trigger_and_requested_by_recorded(self):
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)
        self._flush(registry={"demo": lambda c, x: None}, trigger="manual")
        row = store.last_batch(self.conn, 1)
        self.assertEqual(row["trigger"], "manual")


class AutoDefaultHandlerTests(BatchTestCase):
    """自动生成的默认函数（带 # [auto-default]）只存档、不记账、不触发 Excel。"""

    def test_auto_default_handler_is_not_an_accounting_rule(self):
        for i in (1, 2):
            contract = make_contract(i, company_id=1)
            store.upsert_contract(self.conn, contract, 1)

        def auto_handler(contract, ctx):
            """监听程序自动生成的默认函数（只做存档）。"""
            # [auto-default]
            ctx["default_archive"](contract, ctx)

        self.assertTrue(bookkeeping.is_auto_default(auto_handler))
        result = self._flush(registry={"demo": auto_handler})
        self.assertEqual(result.status, "success")
        self.assertEqual(result.entry_count, 0)
        self.assertEqual(self.factory_calls, [],
                         "默认函数不是记账规则：不得为它启动 Excel 会话")
        self.assertEqual(store.pending_count(self.conn, 1), 0, "否则同一批合同会反复触发记账")
        self.assertIn("未打开 Excel", store.last_batch(self.conn, 1)["message"])

    def test_custom_handler_with_marker_deleted_is_a_real_rule(self):
        """按文档删掉 [auto-default] 注释后，函数就是真实记账规则。"""
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)

        def custom_handler(contract, ctx):
            """用户自己写的记账规则（已删除 auto-default 注释）。"""
            ctx["add_entries"](add=1, minus=0, number="N", about="a")

        self.assertFalse(bookkeeping.is_auto_default(custom_handler))
        result = self._flush(registry={"demo": custom_handler}, book=FakeBook())
        self.assertEqual(len(self.factory_calls), 1)
        self.assertEqual(result.entry_count, 1)

    def test_source_unavailable_is_treated_as_real_rule(self):
        """取不到源码时不误判为默认函数（宁可多开一次 Excel，也不漏记用户规则）。"""
        fake = type("H", (), {})()          # 无源码的对象
        self.assertFalse(bookkeeping.is_auto_default(fake))


class DecimalPrecisionTests(unittest.TestCase):
    """shang.py 在模块级把 Decimal 精度改成 2；导入它不得污染本进程的金额运算。"""

    def test_synthetic_shang_restores_precision(self):
        with TempDir() as tmp:
            (tmp / "shang_probe.py").write_text(
                "from decimal import getcontext\n"
                "getcontext().prec = 2\n"
                "class xledit:\n"
                "    def __init__(self, *a, **k):\n"
                "        pass\n",
                encoding="utf-8",
            )
            before = getcontext().prec
            try:
                mod = bookkeeping.import_shang(tmp, module_name="shang_probe")
            finally:
                sys.modules.pop("shang_probe", None)
            self.assertEqual(getcontext().prec, before, "导入记账脚本后必须恢复进程级 Decimal 精度")
            self.assertTrue(hasattr(mod, "xledit"))

    def test_real_shang_import_restores_precision(self):
        """对真实的 bookkeeping_example/shang.py 做同样检查（注入假 xlwings 以便导入）。"""
        shang_path = REPO / "contract_watcher" / "bookkeeping_example" / "shang.py"
        if not shang_path.exists():
            self.skipTest("bookkeeping_example/shang.py 不存在")
        fake = types.ModuleType("xlwings")
        fake.App = object
        fake.Book = object
        fake.constants = types.SimpleNamespace()
        original = sys.modules.get("xlwings")
        sys.modules["xlwings"] = fake
        sys.modules.pop("shang", None)
        before = getcontext().prec
        try:
            mod = bookkeeping.import_shang(shang_path.parent, module_name="shang")
            self.assertTrue(hasattr(mod, "xledit"))
            self.assertEqual(
                getcontext().prec, before,
                "导入真实 shang.py 后精度必须恢复（shang.py 模块级会把它设为 2）",
            )
        finally:
            if original is not None:
                sys.modules["xlwings"] = original
            else:
                sys.modules.pop("xlwings", None)
            sys.modules.pop("shang", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
