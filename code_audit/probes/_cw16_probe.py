# -*- coding: utf-8 -*-
"""只读探针：读出 target.xlsx 的「原材料(加工用)」表结构，确认绿色槽位/本期采购入库的真实布局。

审计 CW-16 要按模板真实几何来判断「空槽」，所以先用只读方式把这个表的骨架打出来
（A 列非空的行、F 列标签、填充色），不启动 Excel、不修改文件。
"""
import re
import zipfile
from pathlib import Path

P = Path("contract_watcher/bookkeeping_example/target.xlsx")
z = zipfile.ZipFile(P)
sheet = z.read("xl/worksheets/sheet2.xml").decode("utf-8")

shared = z.read("xl/sharedStrings.xml").decode("utf-8")
strings = [
    "".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))
    for si in re.findall(r"<si>(.*?)</si>", shared, re.S)
]
strings = [
    s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    for s in strings
]

cells: dict[tuple[int, int], tuple[str, str]] = {}
for m in re.finditer(r'<c r="([A-Z]+)(\d+)"([^>]*)>(.*?)</c>', sheet, re.S):
    col, row, attrs, body = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    t = re.search(r't="(\w+)"', attrs)
    v = re.search(r"<v>(.*?)</v>", body, re.S)
    val = v.group(1) if v else None
    if val is None:
        continue
    if t and t.group(1) == "s":
        val = strings[int(val)]
    cells[(row, col)] = (val, attrs)

fills = z.read("xl/styles.xml").decode("utf-8")
# 取 cellXfs 里每个 xf 的 fillId，再取 fills 的 fgColor
fill_defs = re.findall(r'<fill>(.*?)</fill>', fills, re.S)
fill_colors = []
for f in fill_defs:
    m = re.search(r'<fgColor rgb="([0-9A-Fa-f]{8})"', f)
    fill_colors.append(m.group(1).upper()[2:] if m else None)
xfs = re.findall(r'<xf [^>]*fillId="(\d+)"', fills)
print("fill palette:", fill_colors)

print(f"used cells: {len(cells)}")
print("--- A 列非空的行 ---")
for (row, col), (val, attrs) in sorted(cells.items()):
    if col != "A":
        continue
    s = re.search(r's="(\d+)"', attrs)
    color = fill_colors[int(xfs[int(s.group(1))])] if s and int(s.group(1)) < len(xfs) else None
    print(f"  A{row}: {val!r:40s} fill={color}")
