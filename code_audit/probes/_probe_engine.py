# -*- coding: utf-8 -*-
"""只读探针：验证 contracts.engine 的公式/运算数值边界行为（不写库）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django  # noqa: E402

django.setup()

from apps.contracts import engine  # noqa: E402


def probe(label, fn):
    try:
        r = fn()
        print(f"{label:38s} -> OK   {r!r}")
    except Exception as e:  # noqa: BLE001
        print(f"{label:38s} -> RAISE {type(e).__name__}: {e}")


print("== FORMULA 路径（safe_evaluate） ==")
probe("safe_evaluate('1+1')", lambda: engine.safe_evaluate("1+1"))
probe("safe_evaluate('log(0)')", lambda: engine.safe_evaluate("log(0)"))
probe("safe_evaluate('sqrt(0-1)')", lambda: engine.safe_evaluate("sqrt(0-1)"))
probe("safe_evaluate('pow(10,1000)')", lambda: engine.safe_evaluate("pow(10,1000)"))
probe("safe_evaluate('asin(2)')", lambda: engine.safe_evaluate("asin(2)"))
probe("safe_evaluate('1/0')", lambda: engine.safe_evaluate("1/0"))
probe("safe_evaluate('0/0')", lambda: engine.safe_evaluate("0/0"))
probe("safe_evaluate('unknownvar+1')", lambda: engine.safe_evaluate("unknownvar+1"))
probe("safe_evaluate('')", lambda: engine.safe_evaluate(""))
probe("safe_evaluate('   ')", lambda: engine.safe_evaluate("   "))
probe("safe_evaluate('=1+2')", lambda: engine.safe_evaluate("=1+2"))
probe("safe_evaluate('（1+2）')", lambda: engine.safe_evaluate("（1+2）"))
probe("safe_evaluate('1e400')", lambda: engine.safe_evaluate("1e400"))
probe("safe_evaluate('9'*400)", lambda: engine.safe_evaluate("9" * 400))

print()
print("== OP 路径（apply_op） ==")
probe("apply_op LOG [0,10]", lambda: engine.apply_op("LOG", [0, 10]))
probe("apply_op LOG [10,1]", lambda: engine.apply_op("LOG", [10, 1]))
probe("apply_op LOG [-5,10]", lambda: engine.apply_op("LOG", [-5, 10]))
probe("apply_op LOG [10,0]", lambda: engine.apply_op("LOG", [10, 0]))
probe("apply_op LOG [10,-2]", lambda: engine.apply_op("LOG", [10, -2]))
probe("apply_op EXP [1000]", lambda: engine.apply_op("EXP", [1000]))
probe("apply_op EXP ['1e400']", lambda: engine.apply_op("EXP", ["1e400"]))
probe("apply_op DIV [1,0]", lambda: engine.apply_op("DIV", [1, 0]))
probe("apply_op DIV [0,0]", lambda: engine.apply_op("DIV", [0, 0]))
probe("apply_op LEN [None]", lambda: engine.apply_op("LEN", [None]))
probe("apply_op MIN [None,None]", lambda: engine.apply_op("MIN", [None, None]))
probe("apply_op AVG [[]]", lambda: engine.apply_op("AVG", [[]]))

print()
print("== to_number 边界 ==")
for v in [None, "", "   ", "abc", "nan", "inf", "-inf", "1e400", 10**30, 0.1, True, False, [], {}, "１２３"]:
    probe(f"to_number({v!r})", lambda v=v: engine.to_number(v))
