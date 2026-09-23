# 合同类型处理函数文件（由 contract_watcher.py 自动维护）。
# 自动生成的默认函数会按 ContractType.key 追加/改名；在函数体内修改即可定制，
# 程序只会调整『标注行与函数名』，不会覆盖你的函数体。
#
# 函数签名固定：
#     def handle_<key>_passed(contract: dict, ctx: dict) -> None:
#         # contract: 合同详情（含 contractType.key/parties/inputs/execution…）
#         # ctx: 见下面的「两个阶段」
#
# 也可手动为某类型添加专属处理（函数名按 key 的拼音形态，如 key="A-B" → handle_a_b_passed）。
# 删除某个自动生成块并重启后，程序不会自动补回——若不想处理该类型，把函数体改为 pass。
#
# ============================================================================
# 处理函数会被调用**两个阶段**，用 ctx["phase"] 区分（2026-09 新工作流）
# ----------------------------------------------------------------------------
# ① phase == "collect"：合同通过、写入本地 SQLite 时调用。
#    此时 ctx["book"] is None —— 只做与 Excel 无关的事（默认行为：存档 JSON）。
#    **不要**在这里打开 xledit：每份合同一次 Excel 会话正是 COM 崩溃的根源。
#
# ② phase == "book"：批量记账时调用（同一公司未记账合同达阈值 / 财年结束 / 手动请求）。
#    此时 ctx 提供**本批次共用的** Excel 会话，调用下面这些方法记账即可：
#
#        ctx["add_entries"](add=Decimal("100"), minus=Decimal(0),
#                           number="HT-001", about="原材料采购")
#            → shang.xledit.add_book_entries（银行流水账）
#        ctx["add_item"](thing, name, number, price, add, minus)
#            → add_book_item（原材料/零件/商品明细）
#        ctx["add_assets"](type, name, add, minus)
#            → add_book_assets（资产/负债/损益科目）
#        ctx["book"]      本批次的 xledit 实例（需要别的 shang 方法时用；
#                         **不要**自己 save()/quit()，批次结束会统一保存）
#        ctx["record"]    该合同在本地库里的行（company_id/contract_id/contract_number/
#                         amount/executed_at/payload_json…）
#        ctx["companyId"] / ctx["companyName"] / ctx["trigger"] / ctx["batchId"]
#        ctx["out_dir"] / ctx["typeKey"] / ctx["default_archive"]（同 collect 阶段）
#
# 示例（按合同金额记一笔银行流水；把下面函数体粘到对应类型的自动生成块里，
#       并删掉那行 "# [auto-default]" 注释 —— 带该注释的函数会被视为「无记账规则」，
#       不会触发 Excel 会话）：
#
#     def handle_material_procurement_passed(contract: dict, ctx: dict) -> None:
#         if ctx.get("phase") != "book":
#             return                      # collect 阶段什么都不做
#         from decimal import Decimal
#         import bookkeeping
#         shang = bookkeeping.import_shang()          # 已自动恢复 Decimal 精度
#         amount = Decimal(str((contract.get("inputs") or {}).get("amount") or 0))
#         number = (contract.get("parties") or [{}])[0].get("contractNumber") or f"#{contract['id']}"
#         ctx["add_entries"](add=amount, minus=Decimal(0), number=number,
#                            about=contract["name"])
#
# 记账失败（Excel 打不开 / 科目找不到）时：本批次不保存、合同保持未记账，
# 60 秒内不会自动重试（GUI「立即记账」可强制重试）。
# ============================================================================
