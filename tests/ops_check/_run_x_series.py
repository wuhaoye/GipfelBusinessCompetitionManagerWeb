# -*- coding: utf-8 -*-
"""跑 tests/fix_verify/scripts/*.py 全部既有回归脚本（每个独立进程），汇总退出码。

这些脚本原是「本机无 bash，用 Python 等价复现」的独立用例；一键脚本需要把它们纳入
统一 PASS/FAIL 表，故这里逐个 subprocess 执行并打印结果。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
SCRIPTS = sorted((REPO / "tests" / "fix_verify" / "scripts").glob("test_*.py"))

fails = []
for script in SCRIPTS:
    proc = subprocess.run([str(PYTHON), str(script)], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=900)
    ok = proc.returncode == 0
    tail = " | ".join([ln.strip() for ln in (proc.stderr or "").strip().splitlines()[-1:]])
    print(f"[{'PASS' if ok else 'FAIL'}] {script.name}"
          + ("" if ok else f"  exit={proc.returncode}：{tail[:200]}"), flush=True)
    if not ok:
        fails.append(script.name)

print(f"[SUMMARY] total={len(SCRIPTS)} fail={len(fails)}" + (f" failed={fails}" if fails else ""))
sys.exit(1 if fails else 0)
