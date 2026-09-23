# -*- coding: utf-8 -*-
"""新增用例的公共小工具（文件名以 `_` 开头，不会被 unittest discover 收集）。

提供：
- `TempDir`：仓库内临时目录（系统 temp 在受限环境下不便清理，沿用既有用例的做法）
- `load_sibling(name)`：按路径加载 contract_watcher 下的模块（含 store/bookkeeping/...）
- `load_watcher()`：按路径加载 contract_watcher.py 并重定向落盘路径
- `FakeBook` / `make_contract`：批量记账用例的替身与造数
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WATCHER_DIR = REPO / "contract_watcher"
TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


class TempDir:
    """仓库内临时目录（用完即删）。"""

    def __enter__(self) -> Path:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.path = TMP_ROOT / f"cwnew_{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def load_sibling(name: str):
    """按 `contract_watcher_<name>` 的键加载同目录模块（与生产代码的加载键一致）。"""
    key = f"contract_watcher_{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, WATCHER_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(key, None)
        raise
    return mod


def load_watcher(tmpdir: Path | None = None):
    """加载真实的 contract_watcher.py，并把可写路径重定向到临时目录。"""
    key = "cwnew_watcher"
    spec = importlib.util.spec_from_file_location(key, WATCHER_DIR / "contract_watcher.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    if tmpdir is not None:
        mod.WATCHER_DIR = Path(tmpdir)
        mod.HANDLERS_FILE = Path(tmpdir) / "handlers.py"
        mod.STATE_FILE = Path(tmpdir) / "data" / "state.json"
        mod.LOG_FILE = Path(tmpdir) / "watcher.log"
        mod.RECORDS_DIR = Path(tmpdir) / "records"
        mod.HANDLERS_FILE.write_text("# 空模板\n", encoding="utf-8")
        (Path(tmpdir) / "data").mkdir(parents=True, exist_ok=True)
    return mod


def make_contract(contract_id=1, *, company_id=1, competition_id=189, type_key="demo",
                  name="测试合同", number=None, executed_at="2026-09-10T00:00:00Z",
                  amount="100", readable=False):
    """构造一份「已执行」合同详情（与后端 GET /api/contracts/:id 同形态）。"""
    return {
        "id": contract_id,
        "competitionId": competition_id,
        "name": name,
        "status": "EXECUTED",
        "executedAt": executed_at,
        "createdAt": "2026-09-01T00:00:00Z",
        "contractType": {"key": type_key, "name": f"{type_key}-类型",
                         "inputSchema": [{"key": "amount", "label": "金额"}]},
        "parties": [
            {"role": "甲方", "companyId": company_id, "companyName": f"公司{company_id}",
             "contractNumber": number or f"HT-{contract_id}"},
            {"role": "主办方", "isHost": True, "companyId": None},
        ],
        "inputs": {"amount": amount},
        "executionLog": [],
        "executionResult": {"fields": {}, "checks": []},
    }


class FakeBook:
    """假 xledit：记录 add_* 调用顺序与参数，可注入失败。"""

    def __init__(self, fail_on=None, fail_message="模拟 Excel 失败"):
        self.calls: list[tuple] = []
        self.saved = False
        self.discarded = False
        self.fail_on = fail_on          # 第几次 add 调用抛错（1 基）
        self.fail_message = fail_message
        self.xlapp = None
        self.wb = object()

    def _record(self, kind, args, kwargs):
        self.calls.append((kind, args, kwargs))
        if self.fail_on is not None and len(self.calls) >= self.fail_on:
            raise RuntimeError(self.fail_message)

    def add_book_entries(self, *args, **kwargs):
        self._record("entries", args, kwargs)

    def add_book_item(self, *args, **kwargs):
        self._record("item", args, kwargs)

    def add_book_assets(self, *args, **kwargs):
        self._record("assets", args, kwargs)

    def check(self):
        print("right")

    def save(self):
        self.saved = True
