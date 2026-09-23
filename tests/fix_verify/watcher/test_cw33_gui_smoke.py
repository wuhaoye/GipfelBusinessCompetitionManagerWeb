# -*- coding: utf-8 -*-
"""N-04 验证：Tkinter 界面（`gui.py`）——登录、公司选择、手动记账、合同查看。

要求映射：「给 contract_watcher 使用 Tkinter 创建一个简单的窗口，给登录，选择公司，
手动要求记录这些功能添加可视化操作，同时通过对 SQLite 数据库读取，可视化的查看
某一公司的合同」。

本用例不需要真实后端：`gui.cw.Backend` 换成替身；也不需要 Excel：处理函数注册表为空，
`flush_company` 在「无处理函数」时不会启动 Excel（这正是被验证的行为之一）。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe -m unittest tests.fix_verify.watcher.test_cw33_gui_smoke -v
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _cw_helpers import TempDir, load_sibling, make_contract  # noqa: E402

store = load_sibling("store")
try:
    gui = load_sibling("gui")
    import tkinter as tk
except Exception as e:  # noqa: BLE001 - 无 tkinter/无显示环境时整组跳过
    gui = None
    tk = None
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


class FakeBackend:
    def __init__(self, *, role="COMPETITION_ADMIN", scopes=(1,), companies=None):
        self.role = role
        self.user_info = {"role": role, "companyScopes": list(scopes), "competitionId": 189}
        self.competition_id = 189
        self._companies = companies if companies is not None else [
            {"id": 1, "name": "公司一", "competitionId": 189},
            {"id": 2, "name": "公司二", "competitionId": 189},
        ]

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
        return []

    def fetch_contract_detail(self, contract_id):  # pragma: no cover - 本用例用不到
        raise KeyError(contract_id)

    def fetch_fiscal_years(self, competition_id, updated_after=None, previous_ids=None):
        return {"items": [], "existingIds": [], "serverTime": "T", "incremental": True}


def _tk_available() -> bool:
    if tk is None:
        return False
    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001 - 无显示环境
        return False
    root.destroy()
    return True


@unittest.skipUnless(_tk_available(), "当前环境没有可用的 Tk 显示")
class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(lambda: self._tmp.__exit__(None, None, None))
        self.conn = store.open_db(self.tmp / "watcher.db")
        self.addCleanup(lambda: self.conn.close())
        store.sync_companies(
            self.conn,
            [{"id": 1, "name": "公司一", "competitionId": 189},
             {"id": 2, "name": "公司二", "competitionId": 189}],
            {1},
        )
        # 让界面用临时目录（界面里的 db_path/books_dir 都从 overrides 来）
        self.cfg = {
            "server": "http://fake", "username": "u", "password": "p",
            "record_company_ids": None, "flush_threshold": 10,
        }
        self.overrides = {
            "db_path": str(self.tmp / "watcher.db"),
            "books_dir": str(self.tmp / "books"),
            "book_template": str(self.tmp / "template.xlsx"),
            "out_dir": str(self.tmp / "records"),
        }
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self._destroy)
        # 界面模块里的 contract_watcher 也指向临时目录（避免写真实 records/handlers/state）
        gui.cw.STATE_FILE = self.tmp / "data" / "state.json"
        gui.cw.HANDLERS_FILE = self.tmp / "handlers.py"
        gui.cw.HANDLERS_FILE.write_text("# 空模板\n", encoding="utf-8")
        (self.tmp / "data").mkdir(parents=True, exist_ok=True)
        self.app = gui.WatcherApp(self.root, dict(self.cfg), str(self.tmp / "config.json"),
                                  dict(self.overrides))
        self.app.conn = self.conn            # 复用用例连接（界面只读）
        self.app.backend = FakeBackend()
        self.app.var_identity.set("已登录（测试）")
        self.app.refresh_companies()

    def _destroy(self):
        try:
            if self.app.engine is not None:
                self.app.engine.stop()
            if self.app._logging_handler is not None:
                import logging

                logging.getLogger("contract_watcher").removeHandler(self.app._logging_handler)
                self.app._logging_handler = None
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 界面构建 / 公司列表 ----------
    def test_company_tree_lists_only_manageable_companies(self):
        ids = [int(i) for i in self.app.tree_companies.get_children()]
        self.assertEqual(ids, [1], "只应列出公司管理范围内的公司（范围外/不可管理的公司不出现）")

    def test_selection_toggle_persists_to_sqlite_and_config(self):
        self.assertEqual(store.selected_ids(self.conn), [1])
        # 取消勾选
        self.app.set_all_selected(False)
        self.assertEqual(store.selected_ids(self.conn), [])
        self.assertEqual(self.app.cfg.get("record_company_ids"), [])
        self.app.set_all_selected(True)
        self.assertEqual(store.selected_ids(self.conn), [1])
        self.assertEqual(self.app.cfg.get("record_company_ids"), [1])

    def test_contract_view_reads_sqlite(self):
        store.upsert_contract(self.conn, make_contract(3, company_id=1, name="查看测试"), 1)
        self.app.tree_companies.selection_set("1")
        self.app.refresh_contracts()
        rows = self.app.tree_contracts.get_children()
        self.assertEqual(len(rows), 1)
        values = self.app.tree_contracts.item(rows[0], "values")
        self.assertEqual(str(values[0]), "3")
        self.assertEqual(values[3], "查看测试")
        self.assertEqual(values[6], "待记账")

    def test_pending_only_filter(self):
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)
        store.upsert_contract(self.conn, make_contract(2, company_id=1), 1)
        batch = store.create_batch(self.conn, 1, trigger="manual", contract_ids=[1])
        store.mark_contracts_booked(self.conn, 1, [1], batch, "已记")
        self.app.tree_companies.selection_set("1")
        self.app.refresh_contracts()
        self.assertEqual(len(self.app.tree_contracts.get_children()), 2)
        self.app.var_pending_only.set("1")
        self.app.refresh_contracts()
        rows = self.app.tree_contracts.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(self.app.tree_contracts.item(rows[0], "values")[0]), "2")

    # ---------- 登录 ----------
    def test_login_syncs_companies_and_role_gate(self):
        app = self.app
        app.conn = None                     # 让 on_login 自己开库（走 overrides 路径）
        gui.cw.Backend = lambda s, u, p: FakeBackend()
        try:
            app.var_server.set("http://fake")
            app.var_user.set("u")
            app.var_password.set("p")
            app.on_login()
        finally:
            gui.cw.Backend = gui_backend_original
        self.assertIn("已登录", app.var_identity.get())
        self.assertEqual([int(i) for i in app.tree_companies.get_children()], [1])
        conn = store.open_db(self.tmp / "watcher.db")
        try:
            self.assertEqual(store.manageable_ids(conn), [1])
            self.assertEqual(store.selected_ids(conn), [1])
        finally:
            conn.close()
        app.conn = self.conn

    def test_player_login_is_rejected(self):
        errors: list = []
        self.app.var_identity.set("未登录")
        original = gui.messagebox.showerror
        gui.messagebox.showerror = lambda title, msg, **k: errors.append((title, msg))
        gui.cw.Backend = lambda s, u, p: FakeBackend(role="PLAYER", scopes=(1,))
        try:
            self.app.var_user.set("player")
            self.app.var_password.set("p")
            self.app.on_login()
        finally:
            gui.messagebox.showerror = original
            gui.cw.Backend = gui_backend_original
        self.assertTrue(errors, "PLAYER 登录必须弹窗拒绝")
        self.assertIn("PLAYER", errors[0][1])
        self.assertNotIn("已登录", self.app.var_identity.get())

    # ---------- 手动记账 ----------
    def test_manual_flush_without_engine_books_contracts(self):
        store.upsert_contract(self.conn, make_contract(1, company_id=1), 1)
        store.upsert_contract(self.conn, make_contract(2, company_id=1), 1)
        orig_ask, orig_info = gui.messagebox.askyesno, gui.messagebox.showinfo
        gui.messagebox.askyesno = lambda *a, **k: True
        gui.messagebox.showinfo = lambda *a, **k: None
        try:
            self.app.tree_companies.selection_set("1")
            self.app.on_manual_flush()
            deadline = time.monotonic() + 10
            # 注意：仓库里既有用例会替换 `time.sleep`（且不还原），这里用 Event.wait 等待
            waiter = threading.Event()
            while time.monotonic() < deadline:
                if self.app._worker is not None and not self.app._worker.is_alive():
                    break
                waiter.wait(0.05)
        finally:
            gui.messagebox.askyesno = orig_ask
            gui.messagebox.showinfo = orig_info
        self.assertEqual(store.pending_count(self.conn, 1), 0, "手动记账后应为已记账")
        batch = store.last_batch(self.conn, 1)
        self.assertEqual(batch["trigger"], "manual")
        self.assertEqual(batch["status"], "success")
        # 没有处理函数 → 不开 Excel，但合同仍被标记（避免无限重复触发）
        self.assertIn("未打开 Excel", batch["message"])

    def test_manual_flush_without_pending_is_noop(self):
        infos: list = []
        orig = gui.messagebox.showinfo
        gui.messagebox.showinfo = lambda title, msg, **k: infos.append((title, msg))
        try:
            self.app.tree_companies.selection_set("1")
            self.app.on_manual_flush()
        finally:
            gui.messagebox.showinfo = orig
        self.assertTrue(any("无需记账" in t for t, _ in infos), infos)
        self.assertIsNone(store.last_batch(self.conn, 1))

    # ---------- 监听开关 ----------
    def test_engine_start_and_stop(self):
        gui.cw.STATE_FILE = self.tmp / "data" / "state.json"
        self.app.var_threshold.set("1")
        self.app.toggle_engine()
        self.assertTrue(self.app.engine.running, "点击后监听线程应启动")
        waiter = threading.Event()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.app.engine.runner is None:
            waiter.wait(0.05)
        self.app.toggle_engine()
        self.assertFalse(self.app.engine.running, "再次点击应停止监听")


gui_backend_original = getattr(gui.cw, "Backend", None) if gui is not None else None


if __name__ == "__main__":
    unittest.main(verbosity=2)
