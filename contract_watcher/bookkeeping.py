# -*- coding: utf-8 -*-
"""批量记账：把某公司积压的合同**一次性**写入该公司自己的 xlsx 账本。

为什么要有这一层
----------------
合同通过时若直接调用 `shang.py` 的 `add_*`，每份合同都要启动一次 Excel（COM），
累计几十次 COM 往返就容易 `RPC 服务器不可用` / Excel 假死（监听程序卡住）。
因此本模块把「记账」从「合同入库」里彻底拆开：

    合同通过 ──► SQLite（store.py，零 COM）
                    │
                    ├─ 同一公司未记账合同数 ≥ 阈值
                    ├─ 财年结束 / 开始（财年更迭信号）
                    └─ 手动请求（GUI「立即记账」）
                            │
                            ▼
                    flush_company()：**一次** Excel 会话
                        ├─ 逐份合同调用 handlers.py 的处理函数（ctx 提供 book / add_*）
                        ├─ shang.xledit.check() 平衡校验（输出进日志）
                        ├─ save()：保存 → 关闭 → 退出 Excel
                        └─ 标记合同已记账 + 落批次记录（失败则保持未记账，下个触发点重试）

处理函数约定（`handlers.py`）
-----------------------------
处理函数会被调用**两次**，用 `ctx["phase"]` 区分：

- `phase == "collect"`（合同入库时）：只做与 Excel 无关的事（默认行为：什么都不做）；
- `phase == "book"`（批量记账时）：此时 `ctx["book"]` 是**本批次共用的** xledit 实例，
  调用 `ctx["add_entries"](...)` / `ctx["add_item"](...)` / `ctx["add_assets"](...)`
  记账即可；**不要**自己 `save()` / `quit()`（批次结束时统一保存）。

没有注册处理函数的合同不会产生分录，但会被标记为已记账（避免同一批合同反复触发
Excel 会话）；需要补记时用 `store.unbook_contracts()` 撤销标记后再次触发。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import sys
from dataclasses import dataclass, field
from decimal import getcontext
from pathlib import Path


def _load_sibling(name: str):
    """按文件路径加载同目录模块（不依赖 sys.path，也不污染它）。

    与 contract_watcher.py 里的同名小工具一致：本模块既可能被 `import store` 正常导入，
    也可能在测试里从任意 cwd 以路径加载；统一走路径加载最稳。
    """
    key = f"contract_watcher_{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载同目录模块 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(key, None)
        raise
    return mod


store = _load_sibling("store")

log = logging.getLogger("contract_watcher.bookkeeping")

# 批次状态
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_UNKNOWN = "unknown"
# 会话级状态（不属于批次）：上次记账失败后的退避期内暂不重试
STATUS_DEFERRED = "deferred"

# 无处理函数时的记账备注
NOTE_NO_HANDLER = "无处理函数：未产生分录"
NOTE_UNKNOWN = "上次保存结果未知（进程中断），需人工核对"
NOTE_BATCH_FAILED = "本批次记账失败，未写入（保持未记账，稍后重试）"


@dataclass
class FlushResult:
    """一次批量记账的结果（供日志 / GUI / 测试断言）。"""

    company_id: int
    status: str
    contract_count: int = 0
    entry_count: int = 0
    batch_id: int | None = None
    book_path: str | None = None
    message: str = ""
    handled_contracts: list[int] = field(default_factory=list)
    skipped_contracts: list[int] = field(default_factory=list)
    balance: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_SUCCESS, STATUS_SKIPPED)


# ==================== shang.py 的导入（Decimal 精度自保） ====================

_SHANG_CACHE: dict[str, object] = {}


def import_shang(shang_dir=None, module_name: str = "shang"):
    """导入 `shang.py`，并**恢复**进程级 Decimal 精度。

    记账脚本在模块级执行 `getcontext().prec = 2`（脚本自身的选择，本模块不改它），
    而 `Decimal` 上下文是**进程级**的：一旦被改成 2 位有效数字，本进程里所有金额
    汇总/比较都会静默失真（金额类缺陷里最危险的一类）。因此这里在导入前后包一层
    精度恢复，把这个副作用限制在 `shang` 模块自己的代码里。

    优先按文件路径加载 `shang_dir/<module_name>.py`（不往 sys.path 塞目录）；
    找不到文件时才退回按模块名导入。返回模块对象。
    """
    key = f"{module_name}@{Path(shang_dir).resolve() if shang_dir else ''}"
    cached = _SHANG_CACHE.get(key)
    if cached is not None:
        return cached
    existing = sys.modules.get(module_name)
    if existing is not None and shang_dir is None:
        _SHANG_CACHE[key] = existing
        return existing

    path = Path(shang_dir) / f"{module_name}.py" if shang_dir else None
    prev_prec = getcontext().prec
    try:
        if path is not None and path.is_file():
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法加载记账脚本 {path}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(module_name, None)
                raise
        else:
            if shang_dir:
                directory = str(Path(shang_dir).resolve())
                if directory not in sys.path:
                    sys.path.insert(0, directory)
            mod = __import__(module_name)
    finally:
        # 无论成功失败都恢复：失败路径同样不能把进程精度留在 2
        getcontext().prec = prev_prec
    _SHANG_CACHE[key] = mod
    return mod


def default_shang_dir() -> Path:
    """记账脚本所在目录（`bookkeeping_example/`）。

    **不能依赖 sys.path**：监听程序由 `python contract_watcher.py` 启动时，sys.path 上只有
    contract_watcher/ 而没有它的子目录，`import shang` 必然 ModuleNotFoundError。
    这里按文件位置定位，再用 `import_shang` 以路径加载。
    """
    here = Path(__file__).resolve().parent
    candidate = here / "bookkeeping_example"
    return candidate if (candidate / "shang.py").is_file() else here


def book_path_for(books_dir, company_id: int) -> Path:
    """每家公司一个账本：`books/company_<companyId>.xlsx`。

    只用 companyId 命名（不掺公司名）：公司改名不会导致「换一本新账」，历史账目不会分叉。
    """
    return Path(books_dir) / f"company_{int(company_id)}.xlsx"


def open_book(book_path, template=None, *, debug: bool = False, shang_dir=None):
    """打开（必要时按模板新建）公司账本，返回 xledit 实例。"""
    shang = import_shang(shang_dir or default_shang_dir())
    kwargs = {"debug": debug}
    if template is not None:
        kwargs["template"] = str(template)
    return shang.xledit(str(book_path), **kwargs)


def _discard_book(book) -> None:
    """放弃当前 Excel 会话（**不保存**）。

    shang.xledit.__del__ → _release() 会先 `wb.save()` 再退出；批次中途失败时保存
    半成品会把「未完成的批次」固化进账本，下次重试就会重复入账。这里先把 wb/xlapp
    置空（让 __del__ 无对象可保存），再直接退出 Excel 进程。
    """
    try:
        app = getattr(book, "xlapp", None)
        try:
            book.wb = None
            book.xlapp = None
        except Exception:  # noqa: BLE001 - 替身对象可能只读
            pass
        if app is not None:
            try:
                app.quit()
            except Exception:  # noqa: BLE001
                try:
                    app.kill()
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        log.debug("放弃 Excel 会话时出现异常（已忽略）", exc_info=True)


# ==================== 处理函数调度 ====================

class BookSession:
    """本批次共用的 Excel 会话 + 分录计数。"""

    def __init__(self, book):
        self.book = book
        self.entries = 0

    def add_entries(self, *args, **kwargs):
        self.entries += 1
        return self.book.add_book_entries(*args, **kwargs)

    def add_item(self, *args, **kwargs):
        self.entries += 1
        return self.book.add_book_item(*args, **kwargs)

    def add_assets(self, *args, **kwargs):
        self.entries += 1
        return self.book.add_book_assets(*args, **kwargs)


def _default_registry():
    """没显式传 registry 时，用监听程序的 handlers.py 注册表（延迟导入避免循环依赖）。"""
    try:
        import contract_watcher as cw

        return cw.load_handlers()
    except Exception:  # noqa: BLE001 - 单测/独立使用时允许没有 handlers
        log.debug("加载 handlers.py 失败，按「无处理函数」处理", exc_info=True)
        return {}


def _slug_of(key: str) -> str:
    import re

    return re.sub(r"\W+", "_", str(key)).strip("_").lower() or "key"


def resolve_handler(registry: dict, type_key: str):
    """按 key 取处理函数；未注册时退回 slug 命名（与监听程序 dispatch 同口径）。"""
    if not registry:
        return None
    fn = registry.get(type_key)
    if fn is None and type_key:
        fn = registry.get(_slug_of(type_key))
    return fn if callable(fn) else None


AUTO_DEFAULT_MARK = "[auto-default]"


def is_auto_default(fn) -> bool:
    """判断处理函数是不是「自动生成的默认函数」（只做存档、不产生分录）。

    监听程序会为每个新合同类型自动追加默认函数，函数体是
    `ctx["default_archive"](contract, ctx)` 并带一行 `# [auto-default]` 注释；
    文档要求「定制时删除该注释行」。批量记账阶段若把这种函数当成真实记账规则，
    就会为一个「什么都不记」的函数启动一次 Excel（纯浪费 COM，还容易崩），
    因此这里认得它：默认函数**不触发** Excel 会话。

    判定依据是函数源码里的标记注释（用 inspect 取源码）。取不到源码时返回 False
    （宁可多开一次 Excel，也不漏记用户自己写的规则）。
    """
    try:
        import inspect

        src = inspect.getsource(fn)
    except Exception:  # noqa: BLE001 - 动态/内建函数取不到源码
        return False
    return AUTO_DEFAULT_MARK in src


def _contract_payload(row) -> dict:
    raw = row["payload_json"]
    if not raw:
        return {}
    import json

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


# ==================== 主流程 ====================

def flush_company(
    conn,
    company_id: int,
    *,
    trigger: str = "manual",
    books_dir,
    template=None,
    registry: dict | None = None,
    out_dir=None,
    requested_by: str | None = None,
    book_factory=None,
    shang_dir=None,
    debug: bool = False,
    log_event: bool = True,
    on_progress=None,
) -> FlushResult:
    """把 `company_id` 名下所有未记账合同一次性写入该公司账本。

    - 没有未记账合同 → `status=skipped`（**不打开 Excel**）；
    - 没有任何合同注册了处理函数 → 同样不打开 Excel，只把合同标记为已记账（无分录）；
    - 打开 Excel 后逐份调用处理函数；成功则 `save()`（内部保存+关闭+退出）；
    - 任一步失败：放弃会话（不保存）、合同保持未记账、批次标记失败，下个触发点重试。

    `on_progress`：批次推进时的回调（每个合同、保存前各调一次）。单实例锁用它刷新心跳 ——
    一个批次的 Excel 会话可能跑好几分钟，不刷心跳会被别的实例判成「锁过期」而接管。
    """
    company_id = int(company_id)
    registry = _default_registry() if registry is None else registry
    company = store.get_company(conn, company_id)
    company_name = company["name"] if company is not None else f"公司#{company_id}"

    def beat() -> None:
        if on_progress is None:
            return
        try:
            on_progress()
        except Exception:  # noqa: BLE001 - 心跳失败不该影响记账
            log.debug("flush 进度回调失败", exc_info=True)

    book_path = book_path_for(books_dir, company_id)
    # 崩溃残留必须先处理：它会把「结果未知」的合同标记掉，随后取待记账集合时就不会再重放
    _resolve_stale_batches(conn, company_id)
    pending = store.pending_contracts(conn, company_id)
    if not pending:
        return FlushResult(
            company_id=company_id, status=STATUS_SKIPPED, book_path=str(book_path),
            message="没有待记账合同",
        )


    tasks: list[tuple] = []       # (row, handler)
    no_handler: list = []
    for row in pending:
        fn = resolve_handler(registry, row["type_key"] or "")
        # 自动生成的默认函数只负责「存档」，不是记账规则：不触发 Excel（见 is_auto_default）
        if fn is None or is_auto_default(fn):
            no_handler.append(row)
        else:
            tasks.append((row, fn))

    contract_ids = [int(r["contract_id"]) for r in pending]
    competition_id = pending[0]["competition_id"] if pending else None

    if not tasks:
        # 全都没有可用的记账规则（未注册，或只有自动生成的默认函数）：不打开 Excel，
        # 直接把它们标记为「已记账（无分录）」，避免同一批合同反复触发 Excel 会话
        beat()
        batch_id = store.create_batch(
            conn, company_id, competition_id=competition_id, trigger=trigger,
            requested_by=requested_by, contract_ids=contract_ids, book_path=str(book_path),
        )
        store.mark_batch_running(conn, batch_id)
        store.mark_contracts_booked(conn, company_id, contract_ids, batch_id, NOTE_NO_HANDLER)
        store.finish_batch(
            conn, batch_id, status=STATUS_SUCCESS, entry_count=0,
            message=f"{len(contract_ids)} 份合同没有可用的记账规则，未产生分录（未打开 Excel）",
        )
        if log_event:
            store.log_event(
                conn, "bookkeeping",
                f"{company_name}：{len(contract_ids)} 份合同无记账规则，未打开 Excel",
                company_id=company_id,
            )
        return FlushResult(
            company_id=company_id, status=STATUS_SUCCESS, batch_id=batch_id,
            contract_count=len(contract_ids), entry_count=0, book_path=str(book_path),
            message="没有可用的记账规则：未产生分录，未打开 Excel",
            skipped_contracts=[int(r["contract_id"]) for r in no_handler],
        )

    batch_id = store.create_batch(
        conn, company_id, competition_id=competition_id, trigger=trigger,
        requested_by=requested_by, contract_ids=contract_ids, book_path=str(book_path),
    )
    store.mark_batch_running(conn, batch_id)

    factory = book_factory or (
        lambda path: open_book(path, template=template, debug=debug, shang_dir=shang_dir)
    )
    book = None
    session = None
    captured = io.StringIO()
    balance = ""
    try:
        beat()
        book = factory(book_path)
        session = BookSession(book)
        for row, fn in tasks:
            beat()                      # 每份合同刷一次心跳（Excel 会话可能很久）
            record = dict(row)
            ctx = {
                "phase": "book",
                "book": session.book,
                "add_entries": session.add_entries,
                "add_item": session.add_item,
                "add_assets": session.add_assets,
                "record": record,
                "companyId": company_id,
                "companyName": company_name,
                "competitionId": row["competition_id"],
                "typeKey": row["type_key"],
                "trigger": trigger,
                "batchId": batch_id,
                "out_dir": out_dir,
                "log": log,
            }
            # shang.add_* 与 check() 会 print 到 stdout：统一捕获进日志/GUI
            with contextlib.redirect_stdout(captured):
                fn(_contract_payload(row), ctx)

        with contextlib.redirect_stdout(captured):
            beat()                      # 保存前再刷一次：这一步可能最慢
            checker = getattr(session.book, "check", None)
            if callable(checker):
                try:
                    checker()
                except Exception as e:  # noqa: BLE001 - 平衡校验失败不影响已写入的数据
                    log.warning("资产负债表平衡校验执行失败：%s", e)
            saver = getattr(session.book, "save", None)
            if callable(saver):
                saver()

        out_text = captured.getvalue().strip()
        balance = " ".join(line.strip() for line in out_text.splitlines() if line.strip())
        store.mark_contracts_booked(
            conn, company_id, contract_ids, batch_id,
        )
        # 无处理函数的合同：本批一并标记（不产生分录，但不再重复触发 Excel）
        if no_handler:
            store.mark_contracts_booked(
                conn, company_id, [int(r["contract_id"]) for r in no_handler], batch_id,
                NOTE_NO_HANDLER,
            )
        message = f"{len(tasks)} 份合同已记账，{session.entries} 笔分录"
        if no_handler:
            message += f"；{len(no_handler)} 份无处理函数（未产生分录）"
        if balance:
            message += f"；平衡校验：{balance}"
        store.finish_batch(
            conn, batch_id, status=STATUS_SUCCESS, entry_count=session.entries,
            message=message, book_path=str(book_path),
        )
        if log_event:
            store.log_event(
                conn, "bookkeeping", f"{company_name}：{message}", company_id=company_id,
            )
        return FlushResult(
            company_id=company_id, status=STATUS_SUCCESS, batch_id=batch_id,
            contract_count=len(contract_ids), entry_count=session.entries,
            book_path=str(book_path), message=message, balance=balance,
            handled_contracts=[int(r["contract_id"]) for r, _ in tasks],
            skipped_contracts=[int(r["contract_id"]) for r in no_handler],
        )
    except Exception as e:  # noqa: BLE001 - 记账失败不丢合同：保持未记账，稍后重试
        if book is not None:
            _discard_book(book)
        err = f"{type(e).__name__}: {e}"
        log.exception("批量记账失败 company=%s trigger=%s：%s", company_id, trigger, err)
        store.finish_batch(
            conn, batch_id, status=STATUS_FAILED, entry_count=0,
            message=f"{err}（未保存，合同保持未记账）", book_path=str(book_path),
        )
        if log_event:
            store.log_event(
                conn, "bookkeeping", f"{company_name}：批量记账失败 {err}",
                level="ERROR", company_id=company_id,
            )
        return FlushResult(
            company_id=company_id, status=STATUS_FAILED, batch_id=batch_id,
            contract_count=len(contract_ids), entry_count=0, book_path=str(book_path),
            message=err,
        )


def _resolve_stale_batches(conn, company_id: int) -> None:
    """处理上次崩溃留下的 running 批次。

    进程在「Excel 已保存、SQLite 尚未更新」之间被杀时无法判定账本是否已写入。
    这里选择**不自动重放**（宁可少记也不重复记账），把合同标记为已记账并注明
    「结果未知，需人工核对」，同时写 ERROR 事件让 GUI/日志能看见。
    """
    for batch in store.running_batches(conn):
        if int(batch["company_id"]) != int(company_id):
            continue
        ids = store.batch_contract_ids(conn, int(batch["id"]))
        store.mark_contracts_booked(conn, company_id, ids, int(batch["id"]), NOTE_UNKNOWN)
        store.finish_batch(
            conn, int(batch["id"]), status=STATUS_UNKNOWN,
            message="进程中断，账本是否已写入未知；已标记为需人工核对，未自动重放",
        )
        store.log_event(
            conn, "bookkeeping",
            f"批次 #{batch['id']} 处于 running（进程中断）：已标记 {len(ids)} 份合同需人工核对",
            level="ERROR", company_id=company_id,
        )
        log.error(
            "批次 #%s（公司 #%s）上次运行中断，结果未知：已标记 %s 份合同需人工核对，未自动重放",
            batch["id"], company_id, len(ids),
        )


def flush_companies(conn, company_ids, **kwargs) -> list[FlushResult]:
    """按顺序逐公司记账（序列化，绝不并发打开 Excel）。"""
    results = []
    for cid in company_ids:
        results.append(flush_company(conn, cid, **kwargs))
    return results
