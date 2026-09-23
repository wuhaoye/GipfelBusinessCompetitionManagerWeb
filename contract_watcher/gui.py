# -*- coding: utf-8 -*-
"""contract_watcher 的 Tkinter 图形界面（登录 · 选公司 · 手动记账 · 查看合同）。

启动方式：
    python gui.py                      # 独立窗口
    python contract_watcher.py --gui   # 同上（复用命令行/配置文件参数）

界面分区
--------
① 登录：服务器 / 账号 / 密码 / 比赛 id（可空）→ 只允许 SUPER_ADMIN / COMPETITION_ADMIN
   （PLAYER 默认关闭，可在「高级」里显式放开）。
② 公司：只列出**账号有管理权限**（companyScopes）的公司；勾选 = 记账目标；
   显示待记账数 / 已记账数 / 最近一次记账。
③ 合同：选中公司在 SQLite 里的合同（可只看待记账），数据全部来自本地库，不访问后端。
④ 手动记账：对选中公司（或全部勾选公司）**立刻**触发一次批量写 xlsx。
⑤ 监听开关：在后台线程跑与命令行相同的主循环（轮询合同 / 财年 / 触发记账），
   由此界面启动的监听与命令行启动的完全同源（`contract_watcher.WatcherSession`）。

线程模型（重要）
----------------
- Tk 主线程：只操作界面 + 自己那条 SQLite 连接（读为主）；
- 监听线程 / 手动记账线程：各自打开自己的 SQLite 连接，**绝不共享连接**；
- Excel/COM 只在监听线程或手动记账线程里发生，且同一时刻只有一个（`flush_company` 串行）。
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, StringVar, Tk, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

WATCHER_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str):
    """按路径加载同目录模块（与 contract_watcher.py 同口径，避免依赖 sys.path）。"""
    import importlib.util

    key = f"contract_watcher_{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, WATCHER_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载同目录模块 {name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(key, None)
        raise
    return mod


cw = _load_sibling("contract_watcher")
store = _load_sibling("store")
bookkeeping = _load_sibling("bookkeeping")
watcher_config = _load_sibling("watcher_config")

logging.getLogger("contract_watcher").setLevel(logging.INFO)


class QueueLogHandler(logging.Handler):
    """把日志塞进队列，由界面线程取出显示（日志处理器不得直接碰 Tk 组件）。"""

    def __init__(self, sink: "queue.Queue[str]"):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        try:
            self.sink.put_nowait(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class GuiEngine:
    """在后台线程跑监听主循环（与命令行共用 WatcherSession/RoundRunner）。"""

    def __init__(self, session, interval: float, on_status=None, stall_timeout: float = 120.0):
        self.session = session
        self.interval = interval
        self.on_status = on_status
        self.stall_timeout = float(stall_timeout)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runner = None
        self.stopped_reason = ""
        # 心跳：Excel/COM 挂起时主循环不再推进，界面据此提示（线程无法安全强杀 COM 调用）
        self.last_activity = time.monotonic()
        self.rounds = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stalled(self) -> bool:
        return self.running and (time.monotonic() - self.last_activity) > self.stall_timeout

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.stopped_reason = ""
        self.last_activity = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="watcher-gui", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """请求停止并等待；返回线程是否**真的**退出了。

        Excel/COM 卡死时线程无法被安全中断（Python 无异步取消），因此调用方必须
        知道「没停下来」：此时绝不能再启动第二个监听线程（会并发开 Excel、写同一账本）。
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        return not self.running

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(max(0.05, float(seconds)))

    def _run(self):
        self.runner = cw.RoundRunner(
            self.session, interval=self.interval, sleep_fn=self._sleep,
            on_round=self._tick,
        )
        try:
            while not self._stop.is_set():
                outcome = self.runner.step()
                if outcome == "auth_exit":
                    self.stopped_reason = self.runner.auth_exit_message
                    break
                self._sleep(self.runner.current_delay)
        except Exception as e:  # noqa: BLE001 - 线程内异常只记录，不能让界面崩
            self.stopped_reason = f"{type(e).__name__}: {e}"
            cw.log.exception("界面监听线程异常退出")

    def _tick(self):
        self.rounds += 1
        self.last_activity = time.monotonic()


class WatcherApp:
    """界面主体。"""

    def __init__(self, root: Tk, cfg: dict, config_path: str, overrides: dict | None = None):
        self.root = root
        self.cfg = dict(cfg)
        self.config_path = config_path
        self.overrides = dict(overrides or {})
        self.backend = None
        self.conn = None
        self.engine: GuiEngine | None = None
        self.instance_lock = None
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self._logging_handler = None
        self._worker: threading.Thread | None = None
        self._company_rows: dict[int, dict] = {}

        root.title("合同记账监听器 · contract_watcher")
        root.geometry("1080x720")
        self._build_ui()
        self._install_log_handler()
        self._apply_defaults()
        self.root.after(800, self._pump)

    # ==================== 界面搭建 ====================
    def _build_ui(self):
        pad = {"padx": 6, "pady": 3}

        top = ttk.LabelFrame(self.root, text="① 登录")
        top.pack(fill=X, **pad)
        self.var_server = StringVar()
        self.var_user = StringVar()
        self.var_password = StringVar()
        self.var_competition = StringVar()
        self.var_remember = StringVar(value="0")
        ttk.Label(top, text="服务器").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(top, textvariable=self.var_server, width=28).grid(row=0, column=1, sticky="w")
        ttk.Label(top, text="账号").grid(row=0, column=2, sticky="e", padx=4)
        ttk.Entry(top, textvariable=self.var_user, width=18).grid(row=0, column=3, sticky="w")
        ttk.Label(top, text="密码").grid(row=0, column=4, sticky="e", padx=4)
        ttk.Entry(top, textvariable=self.var_password, width=18, show="*").grid(row=0, column=5, sticky="w")
        ttk.Label(top, text="比赛 id").grid(row=0, column=6, sticky="e", padx=4)
        ttk.Entry(top, textvariable=self.var_competition, width=8).grid(row=0, column=7, sticky="w")
        ttk.Checkbutton(top, text="记住密码", variable=self.var_remember,
                        onvalue="1", offvalue="0").grid(row=0, column=8, padx=6)
        self.btn_login = ttk.Button(top, text="登录", command=self.on_login)
        self.btn_login.grid(row=0, column=9, padx=6)
        self.var_identity = StringVar(value="未登录")
        ttk.Label(top, textvariable=self.var_identity, foreground="#555").grid(
            row=1, column=0, columnspan=10, sticky="w", padx=4, pady=(0, 3)
        )

        middle = ttk.Frame(self.root)
        middle.pack(fill=BOTH, expand=True, **pad)

        left = ttk.LabelFrame(middle, text="② 公司（仅列出有管理权限的公司；勾选=记账目标）")
        left.pack(side=LEFT, fill=BOTH, expand=True)
        self.tree_companies = ttk.Treeview(
            left, columns=("pick", "id", "name", "pending", "booked", "last"),
            show="headings", height=12, selectmode="browse",
        )
        for col, text, width in (
            ("pick", "记账", 46), ("id", "ID", 50), ("name", "公司", 150),
            ("pending", "待记账", 60), ("booked", "已记账", 60), ("last", "最近记账", 140),
        ):
            self.tree_companies.heading(col, text=text)
            self.tree_companies.column(col, width=width, anchor="center")
        self.tree_companies.pack(side=LEFT, fill=BOTH, expand=True)
        self.tree_companies.bind("<Button-1>", self._on_company_click)
        self.tree_companies.bind("<<TreeviewSelect>>", lambda _e: self.refresh_contracts())
        bar = ttk.Frame(left)
        bar.pack(side=RIGHT, fill=Y)
        ttk.Button(bar, text="全选", width=8, command=lambda: self.set_all_selected(True)).pack(pady=2)
        ttk.Button(bar, text="全不选", width=8, command=lambda: self.set_all_selected(False)).pack(pady=2)
        ttk.Button(bar, text="刷新公司", width=8, command=self.refresh_companies).pack(pady=2)

        right = ttk.LabelFrame(middle, text="③ 合同（来自本地 SQLite，按公司分开）")
        right.pack(side=LEFT, fill=BOTH, expand=True, padx=(6, 0))
        self.var_pending_only = StringVar(value="0")
        head = ttk.Frame(right)
        head.pack(fill=X)
        ttk.Checkbutton(head, text="只看待记账", variable=self.var_pending_only,
                        onvalue="1", offvalue="0",
                        command=self.refresh_contracts).pack(side=LEFT, padx=4)
        ttk.Button(head, text="刷新合同", command=self.refresh_contracts).pack(side=LEFT)
        self.var_contract_hint = StringVar(value="（先登录并选择公司）")
        ttk.Label(head, textvariable=self.var_contract_hint, foreground="#555").pack(side=RIGHT, padx=4)
        self.tree_contracts = ttk.Treeview(
            right, columns=("id", "number", "type", "name", "executed", "amount", "state"),
            show="headings", height=12,
        )
        for col, text, width in (
            ("id", "ID", 50), ("number", "合同编号", 110), ("type", "类型", 110),
            ("name", "名称", 160), ("executed", "执行时间", 140),
            ("amount", "金额", 90), ("state", "状态", 80),
        ):
            self.tree_contracts.heading(col, text=text)
            self.tree_contracts.column(col, width=width, anchor="center")
        self.tree_contracts.pack(fill=BOTH, expand=True)

        actions = ttk.LabelFrame(self.root, text="④ 操作")
        actions.pack(fill=X, **pad)
        ttk.Label(actions, text="阈值(条)").pack(side=LEFT, padx=4)
        self.var_threshold = StringVar(value=str(cw.DEFAULT_FLUSH_THRESHOLD))
        ttk.Spinbox(actions, from_=1, to=9999, width=6, textvariable=self.var_threshold).pack(side=LEFT)
        self.var_auto = StringVar(value="1")
        ttk.Checkbutton(actions, text="阈值自动记账", variable=self.var_auto,
                        onvalue="1", offvalue="0").pack(side=LEFT, padx=4)
        self.var_fy = StringVar(value="1")
        ttk.Checkbutton(actions, text="财年结束自动结账", variable=self.var_fy,
                        onvalue="1", offvalue="0").pack(side=LEFT, padx=4)
        self.var_allow_player = StringVar(value="0")
        ttk.Checkbutton(actions, text="允许 PLAYER 角色", variable=self.var_allow_player,
                        onvalue="1", offvalue="0").pack(side=LEFT, padx=4)
        ttk.Button(actions, text="立即记账（选中公司）", command=self.on_manual_flush).pack(side=LEFT, padx=6)
        self.btn_engine = ttk.Button(actions, text="开始监听", command=self.toggle_engine)
        self.btn_engine.pack(side=LEFT, padx=6)
        ttk.Button(actions, text="打开账本目录", command=self.open_books_dir).pack(side=LEFT, padx=6)
        self.var_status = StringVar(value="就绪")
        ttk.Label(actions, textvariable=self.var_status, foreground="#1a5").pack(side=RIGHT, padx=8)

        bottom = ttk.LabelFrame(self.root, text="日志")
        bottom.pack(fill=BOTH, expand=False, **pad)
        self.txt_log = ScrolledText(bottom, height=10, state="disabled")
        self.txt_log.pack(fill=BOTH, expand=True)

    def _apply_defaults(self):
        o = self.overrides
        cfg = self.cfg
        self.var_server.set(str(o.get("server") or cfg.get("server") or ""))
        self.var_user.set(str(o.get("username") or cfg.get("username") or ""))
        self.var_password.set(str(o.get("password") or cfg.get("password") or ""))
        comp = o.get("competition_id", cfg.get("competition_id"))
        self.var_competition.set("" if comp in (None, "") else str(comp))
        self.var_remember.set("1" if cfg.get("remember_password") else "0")
        self.var_threshold.set(str(o.get("flush_threshold") or cfg.get("flush_threshold")
                                   or cw.DEFAULT_FLUSH_THRESHOLD))
        self.var_auto.set("1" if o.get("auto_bookkeeping", cfg.get("auto_bookkeeping", True)) else "0")
        self.var_fy.set("1" if o.get("fiscal_year_flush", cfg.get("fiscal_year_flush", True)) else "0")
        self.var_allow_player.set("1" if o.get("allow_player", cfg.get("allow_player")) else "0")

    def _install_log_handler(self):
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger("contract_watcher").addHandler(handler)
        self._logging_handler = handler

    # ==================== 路径与配置 ====================
    @property
    def db_path(self) -> Path:
        raw = self.overrides.get("db_path") or self.cfg.get("db_path")
        return Path(raw) if raw else cw.default_db_path()

    @property
    def books_dir(self) -> Path:
        raw = self.overrides.get("books_dir") or self.cfg.get("books_dir")
        return Path(raw) if raw else cw.default_books_dir()

    @property
    def book_template(self) -> Path:
        raw = self.overrides.get("book_template") or self.cfg.get("book_template")
        return Path(raw) if raw else cw.default_book_template()

    @property
    def out_dir(self) -> Path:
        raw = self.overrides.get("out_dir")
        return Path(raw) if raw else cw.RECORDS_DIR

    @property
    def interval(self) -> float:
        try:
            return float(self.overrides.get("interval") or self.cfg.get("interval") or 3.0)
        except (TypeError, ValueError):
            return 3.0

    @property
    def lock_file(self) -> Path:
        """单实例锁文件：与命令行 `--lock-file` 默认值同一个（互相排斥）。"""
        raw = self.overrides.get("lock_file") or self.cfg.get("lock_file")
        return Path(raw) if raw else cw.WATCHER_DIR / "data" / "watcher.lock"

    @property
    def allow_player(self) -> bool:
        return self.var_allow_player.get() == "1"

    def collect_config(self) -> dict:
        cfg = dict(self.cfg)
        cfg.update({
            "server": self.var_server.get().strip(),
            "username": self.var_user.get().strip(),
            "password": self.var_password.get(),
            "remember_password": self.var_remember.get() == "1",
            "competition_id": int(self.var_competition.get()) if self.var_competition.get().strip() else None,
            "flush_threshold": self.threshold,
            "auto_bookkeeping": self.var_auto.get() == "1",
            "fiscal_year_flush": self.var_fy.get() == "1",
            "allow_player": self.allow_player,
        })
        return cfg

    @property
    def threshold(self) -> int:
        try:
            return max(1, int(float(self.var_threshold.get())))
        except (TypeError, ValueError):
            return cw.DEFAULT_FLUSH_THRESHOLD

    def save_config(self) -> None:
        try:
            watcher_config.save_config(self.collect_config(), self.config_path)
        except Exception as e:  # noqa: BLE001 - 配置保存失败不影响使用
            self.log(f"配置保存失败：{e}", level="WARNING")

    def store_selection_to_config(self) -> None:
        """把「记账目标」写回配置文件，下次启动保持一致。"""
        if self.conn is None:
            return
        self.cfg["record_company_ids"] = store.selected_ids(self.conn)
        self.save_config()

    # ==================== 登录 / 公司 ====================
    def on_login(self):
        server = self.var_server.get().strip()
        username = self.var_user.get().strip()
        password = self.var_password.get()
        if not server or not username or not password:
            messagebox.showwarning("缺少参数", "请填写服务器、账号与密码")
            return
        try:
            backend = cw.Backend(server, username, password)
            backend.login()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("登录失败", f"检查服务器地址/账号密码/是否已改密：\n{e}")
            self.log(f"登录失败：{e}", level="ERROR")
            return

        role = getattr(backend, "role", None)
        denial = cw.find_role_denial(role, self.allow_player)
        if denial:
            messagebox.showerror("角色不允许", f"当前账号角色 {role} 不能使用本工具。\n{denial}")
            self.log(f"角色门禁拒绝：{role}（{denial}）", level="ERROR")
            return

        self.backend = backend
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.conn = store.open_db(self.db_path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("本地数据库打不开", str(e))
            return

        scopes = getattr(backend, "company_scopes", [])
        info = getattr(backend, "user_info", None) or {}
        comp_id = info.get("competitionId")
        fy_year = store.current_fiscal_year(self.conn, int(comp_id)) if comp_id else None
        self.var_identity.set(
            f"已登录：{username}（角色 {role or '未知'}，比赛 {comp_id}，"
            f"公司管理范围 {scopes or '空'}"
            + (f"，当前财年 {fy_year}" if fy_year else "，财年状态未知（启动监听后同步）")
            + "）"
        )
        self.log(f"登录成功：{username} 角色={role} 公司管理范围={scopes}")

        try:
            companies = backend.fetch_companies(
                None if str(role or "").upper() == "SUPER_ADMIN" else getattr(backend, "competition_id", None)
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("获取公司列表失败", f"需要 company:view 权限：\n{e}")
            companies = []
        manageable = cw.resolve_manageable_ids(role, scopes, [c.get("id") for c in companies])
        store.sync_companies(self.conn, companies, manageable)
        # 配置里显式给了清单（或 --gui 时带了 --companies）就按它选择；否则沿用本地已保存的勾选
        spec = self.overrides.get("companies")
        if spec is None:
            spec = self.cfg.get("record_company_ids")
        cw.apply_company_selection(self.conn, spec, manageable)
        self.refresh_companies()
        self.save_config()
        self.log(
            f"公司目录：{len(companies)} 家，可管理 {len(store.manageable_ids(self.conn))} 家，"
            f"已勾选 {len(store.selected_ids(self.conn))} 家"
        )

    def refresh_companies(self):
        if self.conn is None:
            return
        selected = set(store.selected_ids(self.conn))
        overview = store.company_overview(self.conn)
        self._company_rows = {row["company_id"]: row for row in overview}
        self.tree_companies.delete(*self.tree_companies.get_children())
        for row in overview:
            if not row["manageable"]:
                continue
            cid = row["company_id"]
            self.tree_companies.insert(
                "", END, iid=str(cid),
                values=(
                    "☑" if cid in selected else "☐",
                    cid, row["name"],
                    row["pending"], row["booked"],
                    store.to_local_text(row["last_batch_at"]) or "—",
                ),
            )
        self.refresh_contracts()

    def _on_company_click(self, event):
        """点击「记账」列 = 勾选/取消勾选（Treeview 没有原生复选框，用该列模拟）。"""
        if self.conn is None:
            return
        if self.tree_companies.identify_region(event.x, event.y) != "cell":
            return
        if self.tree_companies.identify_column(event.x) != "#1":
            return
        iid = self.tree_companies.identify_row(event.y)
        if not iid:
            return
        cid = int(iid)
        current = set(store.selected_ids(self.conn))
        store.set_company_selected(self.conn, cid, cid not in current)
        self.refresh_companies()
        self.store_selection_to_config()

    def set_all_selected(self, flag: bool) -> None:
        if self.conn is None:
            return
        ids = store.manageable_ids(self.conn)
        store.set_selected_companies(self.conn, ids, flag)
        self.refresh_companies()
        self.store_selection_to_config()
        self.log(f"{'全选' if flag else '全不选'}记账目标公司（{len(ids)} 家可管理）")

    def current_company_id(self):
        sel = self.tree_companies.selection()
        return int(sel[0]) if sel else None

    def refresh_contracts(self):
        if self.conn is None:
            return
        cid = self.current_company_id()
        self.tree_contracts.delete(*self.tree_contracts.get_children())
        if cid is None:
            self.var_contract_hint.set("（请在上方选择公司）")
            return
        only_pending = self.var_pending_only.get() == "1"
        rows = store.company_contracts(self.conn, cid, limit=1000, only_pending=only_pending)
        for row in rows:
            self.tree_contracts.insert(
                "", END, iid=f"{row['company_id']}:{row['contract_id']}",
                values=(
                    row["contract_id"],
                    row["contract_number"] or "—",
                    row["type_key"] or "—",
                    row["name"] or "—",
                    store.to_local_text(row["executed_at"]) or "—",
                    row["amount"] or "—",
                    "已记账" if row["booked_at"] else "待记账",
                ),
            )
        name = (self._company_rows.get(cid) or {}).get("name") or f"公司#{cid}"
        pending = store.pending_count(self.conn, cid)
        self.var_contract_hint.set(f"{name}：待记账 {pending} 份，本页 {len(rows)} 行")

    # ==================== 手动记账 ====================
    def on_manual_flush(self):
        if self.conn is None:
            messagebox.showwarning("未登录", "请先登录")
            return
        cid = self.current_company_id()
        targets = [cid] if cid is not None else store.selected_ids(self.conn)
        if not targets:
            messagebox.showwarning("没有目标", "请先勾选记账目标公司，或在左侧选中一家公司")
            return
        pending = {c: store.pending_count(self.conn, c) for c in targets}
        if not any(pending.values()):
            messagebox.showinfo("无需记账", "所选公司没有待记账合同")
            return
        detail = "\n".join(
            f"公司#{c}：{pending[c]} 份待记账" for c in targets
        )
        if not messagebox.askyesno(
            "确认记账",
            f"将立即把以下公司的合同写入各自的 xlsx 账本：\n\n{detail}\n\n"
            "（会启动一次 Excel，耗时取决于合同数量）",
        ):
            return
        if self.engine is not None and self.engine.running:
            for c in targets:
                store.request_flush(self.conn, company_id=c, trigger="manual", requested_by="gui")
            self.log(f"已提交手动记账请求（{len(targets)} 家）：监听线程会在下一轮处理")
            self.var_status.set("已排队等待记账")
        else:
            self.log("监听未运行：直接在后台线程执行一次手动记账")
            self.run_one_shot_flush(targets)

    def run_one_shot_flush(self, company_ids):
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo("正在记账", "上一次记账还没结束，请稍候")
            return
        db_path, books_dir, template = self.db_path, self.books_dir, self.book_template
        out_dir = self.out_dir

        def work():
            conn = None
            try:
                conn = store.open_db(db_path)
                registry = cw.load_handlers()
                for cid in company_ids:
                    result = bookkeeping.flush_company(
                        conn, cid, trigger="manual", books_dir=books_dir, template=template,
                        registry=registry, out_dir=out_dir, requested_by="gui",
                    )
                    self.log(f"手动记账 公司#{cid}：{result.status} "
                             f"{result.contract_count} 份 / {result.entry_count} 笔 {result.message}")
            except Exception as e:  # noqa: BLE001
                self.log(f"手动记账异常：{type(e).__name__}: {e}", level="ERROR")
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass

        self._worker = threading.Thread(target=work, name="watcher-gui-manual", daemon=True)
        self._worker.start()

    # ==================== 监听开关 ====================
    def build_session(self, conn, lock=None):
        state = {}
        if cw.STATE_FILE.exists():
            try:
                import json

                state = json.loads(cw.STATE_FILE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                state = {}
        cfg = self.collect_config()
        competition_id = cfg.get("competition_id")
        return cw.WatcherSession(
            backend=self.backend, state=state, registry=[cw.load_handlers()],
            out_dir=self.out_dir, competition_id=competition_id,
            catalog_interval=cw.DEFAULT_CATALOG_INTERVAL, heartbeat=None, conn=conn,
            threshold=self.threshold,
            auto_bookkeeping=cfg.get("auto_bookkeeping", True),
            fiscal_year_flush=cfg.get("fiscal_year_flush", True),
            fiscal_year_interval=float(
                self.overrides.get("fiscal_year_interval")
                or cfg.get("fiscal_year_interval")
                or cw.DEFAULT_FISCAL_YEAR_INTERVAL
            ),
            books_dir=self.books_dir, book_template=self.book_template,
            lock=lock,
        )

    def toggle_engine(self):
        if self.engine is not None and self.engine.running:
            stopped = self.engine.stop()
            if not stopped:
                msg = ("停止超时：监听线程仍卡在 Excel/COM 调用里。"
                       "为避免并发打开 Excel、并发写同一账本，不再允许启动新监听；"
                       "请关闭本窗口后重新打开（或结束进程）。")
                self.var_status.set("⚠ 监听线程卡住，请重启程序")
                self.log(msg, level="ERROR")
                messagebox.showwarning("监听线程卡住", msg)
                return
            self.release_lock()
            self.btn_engine.config(text="开始监听")
            self.var_status.set("监听已停止")
            self.log("监听线程已停止")
            return
        if self.backend is None or self.conn is None:
            messagebox.showwarning("未登录", "请先登录")
            return
        self.save_config()

        # 与命令行监听程序共用同一把单实例锁：不允许两个实例同时打开 Excel 写同一本账
        lock = cw.SharedLock(self.lock_file)
        acquired, why = lock.acquire()
        if not acquired:
            messagebox.showwarning(
                "已有实例在运行",
                f"{why}\n\n请先停止命令行监听程序（或另一个界面），再启动本界面的监听。",
            )
            self.log(f"启动监听被拒绝：{why}", level="ERROR")
            return
        self.instance_lock = lock
        self.log(why)

        conn = store.open_db(self.db_path)          # 监听线程自己的连接
        session = self.build_session(conn, lock=lock)
        self.engine = GuiEngine(session, interval=self.interval)
        self.engine.start()
        self.btn_engine.config(text="停止监听")
        self.var_status.set("监听中…")
        self.log(f"监听已启动（间隔 {self.interval}s，阈值 {self.threshold}）。"
                 "合同将先进入 SQLite，达到阈值/财年结束/手动请求时才写 xlsx。")

    def release_lock(self) -> None:
        if self.instance_lock is not None:
            try:
                self.instance_lock.release()
            except Exception:  # noqa: BLE001
                pass
            self.instance_lock = None

    # ==================== 杂项 ====================
    def open_books_dir(self):
        path = self.books_dir
        path.mkdir(parents=True, exist_ok=True)
        try:
            import os

            os.startfile(str(path))  # noqa: S606 - Windows 本地工具
        except Exception:  # noqa: BLE001
            messagebox.showinfo("账本目录", str(path))

    def log(self, message: str, level: str = "INFO") -> None:
        self.log_queue.put(f"{level} {message}")

    def _pump(self):
        """界面线程定时任务：刷日志、刷状态（不阻塞 Tk 主循环）。"""
        try:
            for _ in range(200):
                try:
                    line = self.log_queue.get_nowait()
                except queue.Empty:
                    break
                self.txt_log.configure(state="normal")
                self.txt_log.insert(END, line + "\n")
                self.txt_log.see(END)
                self.txt_log.configure(state="disabled")
            if self.conn is not None:
                for row in store.company_overview(self.conn):
                    iid = str(row["company_id"])
                    if self.tree_companies.exists(iid):
                        selected = "☑" if row["selected"] else "☐"
                        self.tree_companies.item(iid, values=(
                            selected, row["company_id"], row["name"], row["pending"],
                            row["booked"], store.to_local_text(row["last_batch_at"]) or "—",
                        ))
                if self.engine is not None:
                    if self.engine.running:
                        if self.engine.stalled():
                            self.var_status.set(
                                f"⚠ 监听线程已 {int(self.engine.stall_timeout)} 秒无进展"
                                "（可能卡在 Excel/COM；可停止监听并检查 Excel）"
                            )
                        else:
                            self.var_status.set(f"监听中…（已完成 {self.engine.rounds} 轮）")
                    elif self.engine.stopped_reason:
                        self.var_status.set("监听已停止（需处理）")
        except Exception:  # noqa: BLE001 - 界面刷新失败不能拖垮主循环
            pass
        self.root.after(1200, self._pump)

    def on_close(self):
        try:
            if self.engine is not None:
                self.engine.stop()
            self.release_lock()
        finally:
            if self._logging_handler is not None:
                logging.getLogger("contract_watcher").removeHandler(self._logging_handler)
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:  # noqa: BLE001
                    pass
        self.root.destroy()


def run_gui(cfg: dict | None = None, config_path: str | None = None,
            overrides: dict | None = None) -> int:
    """启动图形界面（阻塞直到窗口关闭）；返回进程退出码。"""
    cfg = dict(cfg or watcher_config.load_config(None, WATCHER_DIR))
    config_path = config_path or str(watcher_config.config_path(WATCHER_DIR))
    root = Tk()
    app = WatcherApp(root, cfg, config_path, overrides)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    if (overrides or {}).get("selftest"):
        # 冒烟自检：窗口起来后自动关闭（供自动化用例验证 UI 能构建）
        root.after(int((overrides or {}).get("selftest_ms", 700)), app.on_close)
    root.mainloop()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="contract_watcher 图形界面")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--selftest", action="store_true", help="构建窗口后自动关闭（冒烟用）")
    args = ap.parse_args(argv)
    cfg = watcher_config.load_config(args.config, WATCHER_DIR)
    return run_gui(
        cfg=cfg,
        config_path=args.config or str(watcher_config.config_path(WATCHER_DIR)),
        overrides={"selftest": args.selftest},
    )


if __name__ == "__main__":
    sys.exit(main())
