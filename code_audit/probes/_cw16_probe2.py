# -*- coding: utf-8 -*-
"""只读探针 2：打印 target.xlsx「原材料(加工用)」表的块骨架（A/F 列 + 填充色 + 数量列的值）。"""
import re
import zipfile
from pathlib import Path

P = Path("contract_watcher/bookkeeping_example/target.xlsx")
z = zipfile.ZipFile(P)
sheet = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
shared = z.read("xl/sharedStrings.xml").decode("utf-8")
strings = ["".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)) for si in re.findall(r"<si>(.*?)</si>", shared, re.S)]
strings = [s.replace("&amp;", "&") for s in strings]

styles = z.read("xl/styles.xml").decode("utf-8")
fill_defs = re.findall(r"<fill>(.*?)</fill>", styles, re.S)
fill_colors = []
for f in fill_defs:
    m = re.search(r'<fgColor rgb="([0-9A-Fa-f]{8})"', f)
    fill_colors.append(m.group(1).upper()[2:] if m else None)
xfs = [int(x) for x in re.findall(r'<xf [^>]*fillId="(\d+)"', styles)]

cells: dict[tuple[int, str], str] = {}
cell_style: dict[tuple[int, str], int] = {}
for m in re.finditer(r'<c r="([A-Z]+)(\d+)"([^>]*)>(.*?)</c>', sheet, re.S):
    col, row, attrs, body = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    t = re.search(r't="(\w+)"', attrs)
    v = re.search(r"<v>(.*?)</v>", body, re.S)
    val = v.group(1) if v else None
    if t and t.group(1) == "s" and val is not None:
        val = strings[int(val)]
    if val is not None:
        cells[(row, col)] = val
    s = re.search(r's="(\d+)"', attrs)
    if s:
        cell_style[(row, col)] = int(s.group(1))


def fill_of(row, col):
    sid = cell_style.get((row, col))
    if sid is None or sid >= len(xfs):
        return None
    fid = xfs[sid]
    return fill_colors[fid] if fid < len(fill_colors) else None


rows_with_a = sorted(r for (r, c) in cells if c == "A")
print("总行数(有 A 值的行数):", len(rows_with_a), " 最大行:", max(rows_with_a))
print("块首（A 列 == 报表标题）行号:", [r for r in rows_with_a if cells[(r, "A")] == "库存商品成本期末移动平均结转报告"][:20])
print()
print("--- 前 3 块明细（1 基行号）---")
for base in sorted({r for r in rows_with_a if cells[(r, "A")] == "库存商品成本期末流动"}) or []:
    pass
starts = [r for r in rows_with_a if cells[(r, "A")] == "库存商品成本期末移动平均结转报告"]
for base in starts[:3]:
    print(f"### 块首 A{base}")
    for r in range(base, base + 22):
        parts = []
        for col in ("A", "B", "D", "E", "F", "G", "H", "K"):
            v = cells.get((r, col))
            if v is not None:
                parts.append(f"{col}={v!r}")
        fl = fill_of(r, "A")
        if fl:
            parts.append(f"A填充={fl}")
        if parts:
            print(f"  r{r}: " + "  ".join(parts))
