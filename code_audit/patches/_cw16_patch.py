# -*- coding: utf-8 -*-
"""一次性补丁脚本：把 add_book_item 的槽位查找改成「先按物料名找块，找不到再用空块」。

审计 CW-16 的槽位语义：
  1. 物料名命中某个块的绿色槽位行（或该槽位数量列已被写上物料名）→ 用**该块**；
     若该块数量列已被占用，说明同一物料本期已记过 → 抛业务异常（改前会静默丢弃）。
  2. 否则用第一个「数量列全空」的块。
  3. 都没有 → 抛业务异常（改前会覆写 Excel 最后一行并合并，不可恢复）。
"""
from pathlib import Path
import ast

P = Path("contract_watcher/bookkeeping_example/shang.py")
src = P.read_text(encoding="utf-8")

old = '''        last_row = sht.used_range.last_cell.row
        block_rows = [r for r in range(last_row) if sht[r,0].value != None]
        # 各块的「可写槽位行」= 块首 + 6；并要求数量列（进/出）都是真空
        slot_rows = [r + 6 for r in block_rows]
        item_row = None
        for r in slot_rows:
            if sht[r-2,0].value == name:
                item_row = r
                break
        if item_row is None:
            item_row = find_free_row(sht, slot_rows, quantity_cols=(3, 6))
        if item_row is None:
            raise ValueError(
                f"货品表没有可用的空槽位（物料 {name!r} 未登记且模板已写满，共扫描 "
                f"{len(slot_rows)} 个槽位）：改前会覆写最后一行（A{last_row}:L{last_row}）"
                "造成不可恢复的数据损坏。请在 target.xlsx 里为该物料扩充预置槽位块后重试"
            )
        # 已登记物料：必须落到它自己那个空闲的槽位行
        if not (is_free_slot(sht[item_row,3].value) and is_free_slot(sht[item_row,6].value)):
            raise ValueError(
                f"物料 {name!r} 在本期已有记录（槽位行 {item_row+1} 的数量列已被占用）："
                "改前会静默丢弃本次数量。请先确认是否重复登记，或为该物料扩充新的槽位块"
            )
        i = item_row - 6
'''
new = '''        last_row = sht.used_range.last_cell.row
        # 块首行（A 列非空）；各块的可写槽位行 = 块首 + 6，块首 + 4 是绿色物料名行
        block_rows = [r for r in range(last_row) if sht[r,0].value != None]
        i = None
        for r in block_rows:
            if sht[r+4,0].value == name or sht[r+6,3].value == name:
                if not (is_free_slot(sht[r+6,3].value) and is_free_slot(sht[r+6,6].value)):
                    raise ValueError(
                        f"物料 {name!r} 在本期已有记录（槽位行 {r+7} 的数量列已被占用）："
                        "改前会静默丢弃本次数量，现在直接报错以免账实不符。"
                        "请先确认是否重复登记，或为该物料扩充新的槽位块"
                    )
                i = r
                break
        if i is None:
            slot_row = find_free_row(sht, [r + 6 for r in block_rows], quantity_cols=(3, 6))
            if slot_row is None:
                raise ValueError(
                    f"货品表没有可用的空槽位（物料 {name!r} 未登记且模板已写满，共扫描 "
                    f"{len(block_rows)} 个块）：改前会覆写最后一行（A{last_row}:L{last_row}）"
                    "造成不可恢复的数据损坏。请在 target.xlsx 里为该物料扩充预置槽位块后重试"
                )
            i = slot_row - 6
'''
assert old in src, "locate block not found"
src = src.replace(old, new)
ast.parse(src)
P.write_text(src, encoding="utf-8")
print("patched OK, lines =", len(src.splitlines()))
