# -*- coding: utf-8 -*-
"""只读探针 3：用 XML 解析器（正确处理自闭合单元格）读出 target.xlsx 货品表前两块的量化布局。"""
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
P = Path("contract_watcher/bookkeeping_example/target.xlsx")
z = zipfile.ZipFile(P)
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

root = ET.fromstring(z.read("xl/worksheets/sheet2.xml"))
rowmap: dict[int, dict[str, tuple[str, int | None]]] = {}
for row in root.iter(f"{NS}row"):
    r = int(row.get("r"))
    for c in row.iter(f"{NS}c"):
        ref = c.get("r")
        col = re.match(r"([A-Z]+)", ref).group(1)
        t = c.get("t")
        v = c.find(f"{NS}v")
        isel = c.find(f"{NS}is")
        val = None
        if t == "s" and v is not None:
            val = strings[int(v.text)]
        elif t == "inlineStr" and isel is not None:
            val = "".join(x.text or "" for x in isel.iter(f"{NS}t"))
        elif v is not None:
            val = v.text
        s = c.get("s")
        rowmap.setdefault(r, {})[col] = (val, int(s) if s else None)


def fill_of(r, col):
    item = rowmap.get(r, {}).get(col)
    if not item or item[1] is None or item[1] >= len(xfs):
        return None
    fid = xfs[item[1]]
    return fill_colors[fid] if fid < len(fill_colors) else None


# 找出所有「报表标题」行（块首）
titles = [r for r in sorted(rowmap) if rowmap[r].get("A", (None,))[0] == "库存商品成本期末移动平均结转报告"]
print("块首行:", titles)
print("sheet dimension:", root.find(f"{NS}dimension").get("ref") if root.find(f"{NS}dimension") is not None else "?")
print()
for base in titles[:2]:
    print(f"### 块首 1-based 行 {base} → 0 基 base={base-1}，候选槽位 0 基行 {base+4}~{base+17}")
    for r in range(base, base + 20):
        d = rowmap.get(r, {}).get("D", (None, None))
        g = rowmap.get(r, {}).get("G", (None, None))
        h = rowmap.get(r, {}).get("H", (None, None))
        a = rowmap.get(r, {}).get("A", (None, None))
        print(
            f"  r{r} (0基{r-1}): A={a[0]!r:16s} D={d[0]!r:8s} G={g[0]!r:8s} H={h[0]!r:16s}"
            f" Afill={fill_of(r,'A')} Dfill={fill_of(r,'D')}"
        )
