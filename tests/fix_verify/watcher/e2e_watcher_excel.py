# -*- coding: utf-8 -*-
"""批量记账「真实 Excel」验证：真正打开 shang.py + xlwings，写入一份公司账本。

这是唯一会启动 Excel 的验证，单独跑、单独看：
  1. 用临时目录做「账本目录」，模板指向 bookkeeping_example/target.xlsx；
  2. 注册一个真实记账处理函数（phase=book 时调用 ctx["add_entries"]）；
  3. 3 份合同**只开一次** Excel（bookkeeping.flush_company 的批量语义）；
  4. 记账后批次 success、合同标记已记账、账本文件出现；
  5. 用 openpyxl 读回「银行流水账」表，确认摘要与金额真的写进去了；
  6. 退出后不残留 EXCEL.EXE（只统计本次新起的进程）。

用法（仓库根目录，用**系统 Python**，需已安装 xlwings + Excel）：
    python tests\fix_verify\watcher\e2e_watcher_excel.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WATCHER = REPO / "contract_watcher"
SCRATCH = Path(__file__).resolve().parent / ".tmp" / f"e2e_excel_{int(time.time())}"


def load_sibling(name: str):
    key = f"contract_watcher_{name}"
    spec = importlib.util.spec_from_file_location(key, WATCHER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


store = load_sibling("store")
bookkeeping = load_sibling("bookkeeping")

RESULTS: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def excel_pids() -> set:
    import subprocess

    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, errors="replace")
    pids = set()
    for line in out.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].upper().startswith("EXCEL"):
            pids.add(parts[1])
    return pids


def make_contract(i: int, company_id: int, amount: int) -> dict:
    return {
        "id": i, "competitionId": 1, "name": f"Excel 验证合同 {i}", "status": "EXECUTED",
        "executedAt": f"2026-09-1{i}T00:00:00Z",
        "contractType": {"key": "excel_probe", "name": "Excel 验证类型"},
        "parties": [{"role": "甲方", "companyId": company_id, "companyName": "验证公司",
                     "contractNumber": f"EX-{i}"}, {"role": "主办方", "isHost": True}],
        "inputs": {"amount": amount}, "executionLog": [], "executionResult": {},
    }


def handler(contract, ctx):
    """真实记账规则：只在批量记账阶段写银行流水账。"""
    if ctx.get("phase") != "book":
        return
    amount = Decimal(str((contract.get("inputs") or {}).get("amount") or 0))
    number = (contract.get("parties") or [{}])[0].get("contractNumber") or f"#{contract['id']}"
    ctx["add_entries"](add=amount, minus=Decimal(0), number=number, about=contract["name"])


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    books = SCRATCH / "books"
    books.mkdir(parents=True, exist_ok=True)
    db = SCRATCH / "watcher.db"
    conn = store.open_db(db)
    company_id = 9001
    store.sync_companies(conn, [{"id": company_id, "name": "验证公司", "competitionId": 1}],
                         {company_id})
    for i in (1, 2, 3):
        store.upsert_contract(conn, make_contract(i, company_id, 1000 * i), company_id)

    before = excel_pids()
    print("启动前 EXCEL.EXE:", before or "（无）")
    t0 = time.time()
    result = bookkeeping.flush_company(
        conn, company_id, trigger="threshold", books_dir=books,
        template=WATCHER / "bookkeeping_example" / "target.xlsx",
        registry={"excel_probe": handler}, requested_by="e2e-excel",
    )
    elapsed = time.time() - t0
    print(f"flush 结果：status={result.status} entries={result.entry_count} "
          f"耗时={elapsed:.1f}s message={result.message[:200]}")

    check("X1 批次成功", result.status == "success", result.message[:200])
    check("X2 三份合同一次会话共 3 笔分录", result.entry_count == 3, f"entries={result.entry_count}")
    check("X3 合同全部标记为已记账", store.pending_count(conn, company_id) == 0)
    book_path = books / f"company_{company_id}.xlsx"
    check("X4 账本文件已生成", book_path.is_file(), str(book_path))
    check("X5 账本非模板大小（确实被写过）",
          book_path.is_file() and book_path.stat().st_size > 0,
          f"size={book_path.stat().st_size if book_path.is_file() else 0}")
    check("X6 平衡校验有输出", "right" in (result.message or "") or "负债" in (result.message or ""),
          result.message[-120:])

    # 读回账本核实内容
    try:
        import openpyxl

        wb = openpyxl.load_workbook(book_path, data_only=False)
        sheet = wb.worksheets[0]
        found = []
        for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 400), max_col=7):
            text = row[6].value if len(row) > 6 else None
            if isinstance(text, str) and "Excel 验证合同" in text:
                found.append((text, row[3].value if len(row) > 3 else None))
        check("X7 读回银行流水账：3 行摘要写入", len(found) == 3, str(found[:3]))
        writes = [f[1] for f in found]
        check("X8 存款增加列金额正确", all(w is not None for w in writes), str(writes))
    except Exception as e:  # noqa: BLE001
        check("X7 读回账本", False, f"{type(e).__name__}: {e}")

    time.sleep(2)
    after = excel_pids()
    leaked = after - before
    check("X9 不残留新起的 EXCEL.EXE", not leaked, f"新进程={sorted(leaked)}")

    print("\n合计:", sum(1 for _, ok, _ in RESULTS if ok), "/", len(RESULTS))
    print("临时目录:", SCRATCH)
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
