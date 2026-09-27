# -*- coding: utf-8 -*-
"""只读探针 4：DRF FloatField 的 inf/nan 接受度 + 出站渲染（不写库）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django  # noqa: E402

django.setup()

from apps.maps.serializers import MapNodeSerializer  # noqa: E402
from apps.common.renderers import _convert_big_numbers  # noqa: E402
from apps.common.response import JSONRenderer  # noqa: E402


def try_serializer(label, payload):
    s = MapNodeSerializer(data=payload)
    ok = s.is_valid()
    print(f"{label:34s} valid={ok} errors={dict(s.errors) if not ok else ''} "
          f"x={s.validated_data.get('x') if ok else '-'!r}")


print("== MapNodeSerializer(FloatField x/y) 边界输入 ==")
base = {"name": "n", "nodeTypeId": 1, "competitionId": 1}
for raw in ["inf", "-inf", "Infinity", "nan", "NaN", "1e400", float("inf"), 1e308 * 10]:
    p = dict(base, x=raw)
    try:
        try_serializer(f"x={raw!r}", p)
    except Exception as e:  # noqa: BLE001
        print(f"x={raw!r:12s} -> RAISE {type(e).__name__}: {e}")

print()
print("== 出站渲染 inf / nan ==")
r = JSONRenderer()
for value in [float("inf"), float("nan"), 1e400]:
    data = {"code": 0, "message": "成功", "data": {"x": value}}
    try:
        out = r.render(data)
        print(f"render({value!r}) -> OK {out[:80]!r}")
    except Exception as e:  # noqa: BLE001
        print(f"render({value!r}) -> RAISE {type(e).__name__}: {e}")

print()
print("== _convert_big_numbers 对 inf 的处理 ==")
print(" inf ->", _convert_big_numbers(float("inf")))
print(" nan ->", _convert_big_numbers(float("nan")))
print(" 1e400 ->", _convert_big_numbers(1e400))
