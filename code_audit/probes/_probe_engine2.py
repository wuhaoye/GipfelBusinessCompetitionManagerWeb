# -*- coding: utf-8 -*-
"""只读探针 2：inf / NaN 在引擎里的传播（不写库）。"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django  # noqa: E402

django.setup()

from apps.contracts import engine  # noqa: E402


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:46s} -> OK   {r!r}")
    except Exception as e:  # noqa: BLE001
        print(f"{label:46s} -> RAISE {type(e).__name__}: {e}")


inf = float("inf")
nan = float("nan")

print("== inf / NaN 传播 ==")
probe("to_number(float('inf'))", lambda: engine.to_number(inf))
probe("to_number(float('nan'))", lambda: engine.to_number(nan))
probe("to_number(Decimal('Infinity'))", lambda: engine.to_number(Decimal("Infinity")))
probe("to_number(Decimal('NaN'))", lambda: engine.to_number(Decimal("NaN")))
probe("to_number_int(float('inf'))", lambda: engine.to_number_int(inf))
probe("apply_op('EXP',[1000]) 之外的 inf 源", lambda: engine.apply_op("EXP", ["1e400"]))
probe("apply_op('MIN',[inf,5])", lambda: engine.apply_op("MIN", [inf, 5]))
probe("apply_op('SUM_OF',[[1,inf]])", lambda: engine.apply_op("SUM_OF", [[1, inf]]))
probe("dumps_engine_json(float('inf'))", lambda: engine.dumps_engine_json(inf))
probe("dumps_engine_json(float('nan'))", lambda: engine.dumps_engine_json(nan))
probe("dumps_engine_json(Decimal('Infinity'))", lambda: engine.dumps_engine_json(Decimal("Infinity")))

print()
print("== 深拷贝 / 变量作用域 ==")
probe("safe_evaluate('log(0)') 是否被 BusinessError 包装", lambda: engine.safe_evaluate("log(0)"))
probe("safe_evaluate('(1+2)*(3',)", lambda: engine.safe_evaluate("(1+2)*(3"))
probe("safe_evaluate('1++2')", lambda: engine.safe_evaluate("1++2"))
probe("safe_evaluate('--1')", lambda: engine.safe_evaluate("--1"))
probe("safe_evaluate('1 2')", lambda: engine.safe_evaluate("1 2"))
probe("safe_evaluate('\"abc\"')", lambda: engine.safe_evaluate('"abc"'))
