# -*- coding: utf-8 -*-
"""
shang922.py —— shang.py 改写 + shang_fixed.py 的 bug 修复合并版。

【来自 shang922 的三项要求】
  1. 减少读取次数：所有「逐格读」改为「整块/整列一次批量读」，判断全部在内存里做。
     逐格 COM 往返累计约 80 次就会与 Excel 断开连接（RPC_E_DISCONNECTED），
     批量读把成百上千次往返压到个位数。
  2. 重连功能：文件名存在 self.file，掉线后自动 close → quit → 重新 open（必要时强杀进程），
     并把当前操作重试一次（reconnect / _retry_com / _batch_call）。
  3. 打开的文件名不再固定为 target.xlsx：构造函数第一参数即文件名，默认 'target.xlsx'，
     也可用 filename=... 或建好后改 x.file 再 x.reconnect()。
     若这个文件名**不存在**，就以模板（默认 target.xlsx，可用 template=... 换）复制一份、
     改名成该文件名后再打开，所以下面这样就直接得到一个新账套：
         x = xledit('2026年账.xlsx')      # 没有就复制 target.xlsx 出来
     模板也不存在时才报 FileNotFoundError；想强制新建空白簿可以传 template=None（用空簿）。

【来自 shang_fixed.py 的 bug 修复（保留方法名与签名）】
  - _is_blank / _is_free_row：统一「空」判据，不再用 .formula == ''（保存过的工作簿里
    空格 .formula 返回 '' 而不是 None），也不再用「左邻格有字」这类启发式。
  - check：按表名取资产负债表，不再用 sheets[7] 硬编码下标；同一格只读一次。
  - add_book_entries：
      * 加边界，原 while 在流水账写满后会一直往下读直到报错；
      * 序号：原代码先写 sht[i,2] 再拿刚写的那格判断，elif 分支永远走不到 → 改为
        首行写 1、其余行取上一行序号 +1；
      * 余额公式：原首行走的是引用表头 B3、并把本行金额算两次的分支 → 改为
        首行 =B{行}+D-E，其余行 =F{上一行}+D-E（上一行余额，不是下一行）；
      * 只记录行号，地址在使用时再生成，避免被插行顶掉。
  - add_book_item：
      * 原搜索是约 200 次逐格读的长循环（会掉线）→ 改为按底纹颜色定位绿色锚点行
        （常数次 COM）+ 批量读数据区；
      * 块布局按模板实测重排：锚点行 = 货品名行，其上两行是表头，数据行在锚点行与
        锚点行+1，图例行在锚点行+3，块内合计在 +4，全局总计在 +5；
      * 汇总公式不再写死 L199/L181/L182，改为动态拼所有块的「剩余总额」；
      * 出库金额汇总原来匹配的是模板里不存在的「本期出库金额：」（多一个全角冒号），
        结果恒为空 → 改为匹配「本期出库金额」；
      * 插行判据原来读落在合并区里的格子（读不到值，插行永不触发）→ 改读图例行的 B 列；
      * 商品的金额列是 F（输入值）而不是 E；
      * 跨表链接统一由 _link_account 写：原来 shtT.range(found_cell.Address) 得到的是
        绝对地址（如 $AT$1），再 +2/-1 会回落成 $AT$1 本身、目标列解析错；而且原代码把
        损益表的「主营业务成本-存货成本」写到了商品表自身上，这里跳过本表；
      * 追加新块时先删掉上一段的「结存/结转」合计行，保证全表只有一处总计；
      * 找不到空行时明确报错，不再静默写错行。
  - add_book_assets：
      * 找不到科目时明确报错（原来会拿 None 取属性，抛 AttributeError）；
      * 空行扫描原来没有终止条件（负债类 K4/L4、损益类 N5/O5 这种「首行是字面量 0 且
        左邻非空」的布局会一直 rowT += 1 走出表格、把 Excel 挂死）→ 改为有界扫描；
      * 负债/损益的借贷方向改为按类型显式写列，不再用变量交换；
      * 银行存款行改为引用「本期余额 - 期初余额」（=F{末}-F{首}），原公式把同一笔金额
        又减了一次、且 entries_to_assets_money 是类属性可能指向已被插行顶掉的行。

【本次新增的两条】
  - 提醒1 自动扩充块：某个货品所在块的数据行（模板块是「本期采购入库」图例行之前那一整段
    预置了公式的空白行，通常十几行）全部用满时，不再报错，而是在图例上方自动插入一行、
    整行沿用上一行的格式与公式，再写入本笔；控制台会打印 [扩充]。
  - 提醒2 自动重算：add_book_entries / add_book_item / add_book_assets 写完保存后自动调用
    recalc()（内部 xlapp.api.Calculate()，手动重算模式会临时切自动再切回），check() 前也会先
    重算，所以批量写入后读回的就是最新计算结果；也可手动调 x.recalc()。

【跨表公式的两个坑（本项目实测踩到）】
  - F 列属于「银行流水账」表，跨表引用必须带表名：不写表名的 =F164 会被当成本表自己的 F164
    （空），算出来恒为 0。
  - 汇总表科目块布局是 科目名/借贷名/期初/数据行…/SUM 行/期末行，写链接必须写「期末行」
    （= SUM 行的下一行），写到数据行会既进不了 SUM 区间、又和期末行重复计算。

每次操作后依旧调用 self.wb.save()。
"""

import datetime as dt
import shutil
from pathlib import Path

import xlwings as xw
from decimal import Decimal, getcontext
from enum import Enum

try:  # 掉线时抛的是 pywintypes.com_error（RPC_E_DISCONNECTED / 0x80010108 等）
    import pywintypes
    COM_ERROR = pywintypes.com_error
except Exception:  # pragma: no cover - 没有 pywin32 的环境
    COM_ERROR = ()


class things(Enum):
    RAWMETRIAL = 1
    COMPENT = 2
    PORDUCT = 3


class ASSET(Enum):
    BANK_DEPOSITS = "银行存款"
    LAND_USE_RIGHTS = "土地使用权"
    RAW_MATERIALS = "原材料"
    SEMI_FINISHED_PARTS = "半成品（零件）"
    FIXED_ASSETS = "固定资产"
    ACCOUNTS_RECEIVABLE = "应收账款"
    ACCOUNTS_PAYABLE = "应付账款"
    INVENTORY_GOODS = "库存商品"
    INDUSTRIAL_PROPERTY = "工业产权及专有技术"
    ACCUMULATED_DEPRECIATION = "累计折旧"
    TRADING_FINANCIAL_ASSETS = "交易性金融资产"


class LIABILITIES(Enum):
    ADVANCE_RECEIVABLES = "预收账款"
    SHORT_TERM_LOANS = "短期借款"
    ACCOUNTS_PAYABLE = "应付账款"
    EMPLOYEE_BENEFITS_PAYABLE = "应付职工薪酬"


class EQUITY(Enum):
    PRODUCT_SALES_REVENUE = "产品（商品）销售收入"
    INVESTMENT_INCOME = "投资收益"
    OTHER_OPERATING_INCOME = "其他业务收入"
    MAIN_OPERATING_COST_INVENTORY = "主营业务成本-存货成本"
    MAIN_OPERATING_COST_LABOR = "主营业务成本-人工"
    MAIN_OPERATING_TAXES = "主营业务税金及附加"
    SELLING_EXPENSES = "销售费用"
    ADMINISTRATIVE_EXPENSES = "管理费用"
    FINANCIAL_EXPENSES_INTEREST = "财务费用（利息费用）"
    PAID_IN_CAPITAL = "实收资本"
    CAPITAL_RESERVE = "资本公积"
    UNDISTRIBUTED_PROFIT = "未分配利润"
    NON_OPERATING_INCOME = "营业外收入"


class BOOOKTYPE(Enum):
    ASSETS = 1
    LIABILITIES = 2
    EQUITY = 3


getcontext().prec = 2

DEFAULT_FILE = 'target.xlsx'
TEMPLATE_FILE = DEFAULT_FILE       # 目标文件不存在时，按这个样板复制一份新的出来
GREEN = '#00FF00'
GREEN_BGR = 65280                  # RGB(0,255,0)，FindFormat 用的 BGR 值
SCAN_PAD = 8                       # 找空行时在表尾之后多扫几行，避免边界行被重复使用
BLOCK_SCAN = 200                   # 块内数据区的扫描上限（正常会在「本期采购入库」图例行停住）
ENTRIES_OPENING_ROW = 3            # 银行流水账里「账户初始资金」的值所在行（1 基，表头行下面一行）


def _retry_com(function, self, *args, **kwargs):
    """掉线重试：只有连接类异常（com_error）才重连，重连后把原操作重做一次。"""
    last = None
    for attempt in range(2):
        try:
            return function(self, *args, **kwargs)
        except COM_ERROR as error:
            last = error
            if attempt == 1:
                break
            print(f"[重连] {function.__name__} 执行中连接异常，正在重新打开 "
                  f"{getattr(self, 'file', DEFAULT_FILE)} …（{error}）")
            self.reconnect()
    raise last


def _batch_call(function):
    """方法装饰器：批量读 + 自动重连重试。"""
    def wrapper(self, *args, **kwargs):
        return _retry_com(function, self, *args, **kwargs)
    wrapper.__name__ = function.__name__
    wrapper.__doc__ = function.__doc__
    wrapper.__wrapped__ = function        # 便于 inspect / 调试时看到真正的方法体
    return wrapper


class xledit:
    xlapp: xw.App = None
    wb: xw.Book = None
    entries_to_assets_money = ''

    def __init__(self, file: str = DEFAULT_FILE, debug: bool = False, filename: str = None,
                 template: str = TEMPLATE_FILE):
        # file 就是本次打开的文件名（默认 target.xlsx，不再写死）
        if filename is not None:
            file = filename
        p = Path(file).expanduser()
        self.template = None if template is None else str(Path(template).expanduser())
        self._blank = False
        self.file = str(p.resolve())      # 重连要用，绝对化后不受 cwd 变化影响
        if p.is_file() != True:
            # 要打开的文件不存在：以模板（默认 target.xlsx）为样板复制一份出来再打开
            self._copy_template(p)
        self.debug = debug
        self._reopening = False
        self._account_rows = {}           # 各科目在汇总表里用的数据行，保证链接与写入同一行
        self.read_calls = 0               # 统计：COM 读取次数（批量读一次算一次）
        self.write_calls = 0              # 统计：COM 写入次数
        self._open()

    def _copy_template(self, target: Path):
        """目标文件不存在时，从模板复制一份并改名成目标文件名（模板默认 target.xlsx）。
        template=None 表示不复制样板，直接新建一个空白工作簿。"""
        if target.is_dir():
            raise IsADirectoryError(f"{target} 是目录，不是文件名")
        if self.template is None:
            self._blank = True            # 空簿：由 _open 直接新建，不复制
            print(f"[新建] {target} 不存在，且指定 template=None，将新建空白工作簿")
            return
        source = Path(self.template)
        if source.is_file() != True:
            # 退一步：按「和本模块放在一起」找模板，这样从别的目录运行也找得到
            beside = Path(__file__).resolve().parent / Path(self.template).name
            if beside.is_file():
                source = beside
        if source.is_file() != True:
            raise FileNotFoundError(
                f"要打开的文件 {target} 不存在，模板 {self.template} 也不存在，无法复制")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass                          # 目录已存在或无权创建，交给复制去报错
        shutil.copy2(source, target)      # 连同格式/公式/底纹一起复制
        print(f"[新建] {target} 不存在，已从模板 {source} 复制一份")


    # ------------------------------------------------------------------
    # 连接 / 重连
    # ------------------------------------------------------------------
    def _open(self):
        self.xlapp = xw.App(visible=bool(self.debug), add_book=False)
        if self._blank:                   # 目标文件是空白新建的，先开空簿再存成目标名
            self.wb = self.xlapp.books.add()
            self._blank = False
            self.wb.save(self.file)
        else:
            self.wb = self.xlapp.books.open(self.file)
        self.xlapp.api.ScreenUpdating = bool(self.debug)
        self.xlapp.api.DisplayAlerts = False

    def _release(self):
        """断开旧连接。连接已经死掉时这些调用本身也会报错，全部吞掉。"""
        wb, xlapp = self.wb, self.xlapp
        self.wb, self.xlapp = None, None
        for action in (
            lambda: wb.save(),            # 重连前尽量保住已经改好的内容
            lambda: wb.close(),
            lambda: xlapp.quit(),
            lambda: xlapp.kill(),
        ):
            try:
                action()
            except Exception:
                pass

    def reconnect(self) -> bool:
        """掉线重连：释放旧进程（必要时强杀），按 self.file 重新打开。"""
        if self._reopening:               # 防重入：重连过程中又掉线
            return False
        self._reopening = True
        try:
            self._release()
            self._open()
            return True
        finally:
            self._reopening = False

    def _alive(self) -> bool:
        """连接是否还能用。探测失败就当作掉线。"""
        if (self.xlapp is None) or (self.wb is None):
            return False
        try:
            self.xlapp.books.count
            self.wb.sheets.count
            return True
        except Exception:
            return False

    def ensure_connection(self) -> bool:
        if not self._alive():
            print(f"[重连] 连接已断开，重新打开 {self.file}")
            self.reconnect()
        return True

    @property
    def book(self) -> xw.Book:
        """self.wb 的保险版：先确认连接可用再返回。"""
        self.ensure_connection()
        return self.wb

    def stats(self) -> dict:
        return {'read_calls': self.read_calls, 'write_calls': self.write_calls}

    def recalc(self):
        """提醒2：强制重算。批量写入后立刻重算，公式不再是缓存值，随后读回的就是最新结果。
        若工作簿被设成手动重算，这里临时切成自动、算完再切回去。"""
        try:
            self.ensure_connection()
            current = self.xlapp.api.Calculation
            manual = getattr(xw.constants.Calculation, 'xlCalculationManual', -4135)
            if current == manual:
                self.xlapp.api.Calculation = getattr(xw.constants.Calculation,
                                                     'xlCalculationAutomatic', -4105)
            self.xlapp.api.Calculate()
            if current == manual:
                self.xlapp.api.Calculation = current
        except Exception as error:
            print(f"[重算] 跳过：{error}")
        return self

    def __del__(self):
        try:
            if getattr(self, 'wb', None) is not None:
                self._release()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 批量读 / 批量写（一次调用只做一次 COM 往返）
    # ------------------------------------------------------------------
    def _read(self, sheet, first_row: int, first_column: int, last_row: int, last_column: int):
        """一次读回一片区域，统一成二维列表（行主序）。行列都是 1 基。"""
        self.ensure_connection()
        sht = self.wb.sheets[sheet]
        first_row = max(int(first_row), 1)
        first_column = max(int(first_column), 1)
        last_row = max(int(last_row), first_row)
        last_column = max(int(last_column), first_column)
        self.read_calls += 1
        target = sht.range((first_row, first_column), (last_row, last_column))
        values = target.value
        # 注意 xlwings 的取值形状：区域内全是空格时返回标量 None；单列多行返回一维列表；
        # 单行多列也返回一维列表；多行多列才是二维列表。这里一律按区域形状归一成二维。
        shape = getattr(target, 'shape', None)
        if not isinstance(shape, (list, tuple)) or len(shape) != 2:
            shape = (last_row - first_row + 1, last_column - first_column + 1)
        rows_count, columns_count = int(shape[0]), int(shape[1])
        if not isinstance(values, (list, tuple)):
            table = [[values] * columns_count for _ in range(rows_count)]
        elif rows_count == 1:
            table = [list(values)]
        elif columns_count == 1:
            table = [[value] for value in values]
        else:
            table = [list(row) for row in values]
        width = last_column - first_column + 1
        rows = []
        for row in table[:rows_count]:
            row = list(row) if isinstance(row, (list, tuple)) else [row]
            if len(row) < width:          # 合并单元格可能少给几列，补齐防越界
                row = row + [None] * (width - len(row))
            rows.append(row)
        while len(rows) < rows_count:     # 理论上不会发生，兜底保证行数
            rows.append([None] * width)
        return rows

    def _column(self, sheet, column0: int, last_row: int = None) -> list:
        """整列批量读回（0 基列号），返回 0 基列表：index = Excel 行号 - 1。"""
        self.ensure_connection()
        if last_row == None:
            last_row = self._last_row(sheet)
        return [row[0] for row in self._read(sheet, 1, column0 + 1, last_row, column0 + 1)]

    def _write_range(self, sheet, first_row: int, first_column: int, table):
        """一次写回一小片区域（1 基），用于「同一行多格」的批量写。"""
        self.ensure_connection()
        sht = self.wb.sheets[sheet]
        rows = len(table)
        columns = len(table[0]) if rows else 0
        if (rows == 0) or (columns == 0):
            return
        self.write_calls += 1
        last_row = first_row + rows - 1
        last_column = first_column + columns - 1
        if rows == 1:
            sht.range((first_row, first_column), (first_row, last_column)).value = list(table[0])
        elif columns == 1:
            sht.range((first_row, first_column), (last_row, first_column)).value = [[v[0]] for v in table]
        else:
            sht.range((first_row, first_column), (last_row, last_column)).value = table

    def _last_row(self, sheet) -> int:
        """表尾行号（1 基）。原来每次读 used_range 都是一次 COM 往返。"""
        self.ensure_connection()
        return self.wb.sheets[sheet].used_range.last_cell.row

    def _scan_row(self, sheet, pad: int = None) -> int:
        """扫描用的下界行号 = 表尾 + 余量。
        必须留余量：找空行的循环若正好卡在表尾，刚写满的那一行会被当成「空行」再写一次，
        连续两次记录就叠在同一行上。余量只是多读几行空白，代价很小。"""
        if pad == None:
            pad = SCAN_PAD
        return max(self._last_row(sheet), 1) + pad

    @staticmethod
    def _is_blank(value) -> bool:
        """统一「空」判据。工作簿保存过之后，空单元格的 .formula 会返回 '' 而不是 None。"""
        return (value == None) or (isinstance(value, str) and value.strip() == '')

    def _is_free_row(self, sheet, row0: int, column0: int) -> bool:
        """该行是否可写：借贷两列的值与公式都为空。
        模板里各科目「期初」文字的行号并不统一，用左邻格判断会误判，故只看这两列。"""
        for column in (column0, column0 + 1):
            if (not self._is_blank(self.wb.sheets[sheet][row0, column].value)) or \
               (not self._is_blank(self.wb.sheets[sheet][row0, column].formula)):
                return False
        return True

    def _find_header(self, sheet, text: str):
        """在前两行里找科目表头，返回 (0 基行, 1 基列)。
        用「一次批量读一行 + 内存比对」代替 Excel 的 Find：用过颜色 FindFormat 之后，
        后续文字 Find 会带着残留格式状态，直接让 xlwings 与 Excel 断开连接。"""
        for row0 in (0, 1):
            table = self._read(sheet, row0 + 1, 1, row0 + 1, 48)
            if not table:
                continue
            for column, value in enumerate(table[0], start=1):
                if (value != None) and (str(value).strip() == text):
                    return row0, column
        return None, None

    def _find_green_rows(self, sheet) -> list:
        """按底纹颜色定位绿色锚点行（常数次 COM，替代逐格扫描）。"""
        sht = self.wb.sheets[sheet]
        rows = []
        rng = sht.used_range.api
        self.xlapp.api.FindFormat.Clear()
        self.xlapp.api.FindFormat.Interior.Color = GREEN_BGR
        cell = rng.Find(What="", After=rng.Cells(rng.Cells.Count),
                        LookIn=xw.constants.FindLookIn.xlValues,
                        LookAt=xw.constants.LookAt.xlWhole,
                        SearchOrder=xw.constants.SearchOrder.xlByRows,
                        SearchDirection=xw.constants.SearchDirection.xlNext,
                        MatchCase=False, SearchFormat=True)
        seen = set()
        while cell is not None and cell.Address not in seen:
            seen.add(cell.Address)
            rows.append(cell.Row)
            cell = rng.Find(What="", After=cell,
                            LookIn=xw.constants.FindLookIn.xlValues,
                            LookAt=xw.constants.LookAt.xlWhole,
                            SearchOrder=xw.constants.SearchOrder.xlByRows,
                            SearchDirection=xw.constants.SearchDirection.xlNext,
                            MatchCase=False, SearchFormat=True)
        self.xlapp.api.FindFormat.Clear()
        rows.sort()
        return rows

    # ------------------------------------------------------------------
    # 资产负债平衡检查（原来同一格读了三次）
    # ------------------------------------------------------------------
    @_batch_call
    def check(self):
        self.recalc()                             # 先重算，保证读到的是最新结果
        sht = self.book.sheets['资产负债表']      # 不再用 sheets[7] 硬编码下标
        value = sht.range('H80').value            # 一次读，三处复用
        if value == 0:
            print("right")
        if value != None and value > 0:
            print("资产>负债+所有者权益")
        if value != None and value < 0:
            print("资产<负债+所有者权益")

    # ------------------------------------------------------------------
    # 保存 / 收尾
    # ------------------------------------------------------------------
    def save(self):
        self.wb.save()
        self.wb.close()
        self.xlapp.quit()
        self.wb = None
        self.xlapp = None

    # ------------------------------------------------------------------
    # 银行流水账
    # ------------------------------------------------------------------
    @_batch_call
    def add_book_entries(self, add: Decimal, minus: Decimal, number: str, about: str):
        sht = self.book.sheets[0]
        cdt = dt.date.today()
        dtstr = cdt.strftime("%Y/%m/%d")
        used = self._scan_row(0)
        # 批量读 A~F 列一次，在内存里定位最后一笔数据行，写到它下面一行。
        # 不能找「第一个空行」：模板中间可能有空行（本工作簿第 163 行就是空的），
        # 那样就会把记录写在空行上，下一次又被当成空行再写一遍，连续两笔叠在同一行。
        # 判空也必须用 _is_blank：空格读回来可能是 ''，用 != None 会把空行当成已占用。
        table = self._read(0, 1, 1, used, 6)
        last0 = 0                        # 0 基：最后一笔有数据的行
        # 从「账户初始资金」行的下一行开始扫：初始资金那一行的 F 只是期初 0，不是一笔记录。
        for index in range(ENTRIES_OPENING_ROW + 1, used):
            if not self._is_blank(table[index][5]):
                last0 = index
        i = last0 + 1                    # 0 基索引按 1 基 Excel 行号算，正好是下一行
        if i + 1 > used:
            raise RuntimeError("银行流水账已无空行可写")
        target = i + 1                    # 1 基 Excel 行号
        if i == 2:
            sequence = 1
        else:
            previous = table[i - 2][2]
            sequence = (int(previous) + 1) if isinstance(previous, (int, float)) else 1
        # 同一行的值一次写完，余额那一格再单独写公式
        self._write_range(0, target, 1, [[
            dtstr,
            None,
            sequence,
            (float(add) if add != 0 else None),
            (float(minus) if minus != 0 else None),
            None,
            str(number) + " " + str(about),
        ]])
        r = target
        row0 = r - 1                                  # 0 基行号
        if i == 2:
            # 首行没有上一行余额，用 B 列（账户初始资金，为空则按 0 计）
            sht[row0, 5].formula = f'=B{r}+D{r}-E{r}'
        else:
            sht[row0, 5].formula = f'=F{r-1}+D{r}-E{r}'     # 上一行余额 + 本行增 - 本行减
        # 只记录行号，地址在使用时再生成，避免被 add_book_item 插行顶掉
        if getattr(self, 'entries_to_assets_first_row', None) == None:
            self.entries_to_assets_first_row = row0
        self.entries_to_assets_row = row0
        self.entries_to_assets_money = f"'{sht.name}'!$F${r}"
        self.wb.save()
        self.recalc()                                 # 提醒2：写入后自动重算

    # ------------------------------------------------------------------
    # 原材料 / 零件 / 商品 明细块
    # ------------------------------------------------------------------
    @_batch_call
    def add_book_item(self, thing: things, name: str, number: int, price: Decimal, add: bool, minus: bool):
        #
        if thing == things.RAWMETRIAL:
            index = 1
        elif thing == things.COMPENT:
            index = 2
        elif thing == things.PORDUCT:
            index = 3
        sht = self.book.sheets[index]
        used = max(self._last_row(index), 1)
        # 找块用绿色锚点行（常数次 COM），所以这里的数据只作为「本块数据行是否已用」的参考；
        # 至少读到 anchor0+7 行，保证块内 7 行都在窗口里。
        scan = max(used, 12) + SCAN_PAD
        # 批量读：A 列（货品名）+ D~G 列（数量/单价/金额/出库数量），各一次
        col_a = self._column(index, 0, scan)
        data = self._read(index, 1, 4, scan, 7)

        def a_at(row0):
            if 0 <= row0 < len(col_a):
                return col_a[row0]
            return None

        def data_at(row0, n):
            if 0 <= row0 < len(data):
                return data[row0][n]
            return None

        # 用绿色锚点行定位块：锚点行即货品名行。名字相同或该块还没用（货品名为空）就复用。
        # 这样每个货品都有自己的块，不会被写到别的货品的块里。
        green = self._find_green_rows(index)
        anchor0 = None
        for row in green:
            row0 = row - 1
            if (a_at(row0) == name) or (a_at(row0) == None):
                anchor0 = row0
                break
        # 这个货品的块是不是已经用过（货品名非空）
        used_block = (anchor0 != None) and (a_at(anchor0) != None)
        new_block = False
        if anchor0 == None:
            new_block = True
            # 先删掉上一段的「结存/结转」合计行，保证全表只有一处总计
            col_j = self._column(index, 9, used)
            col_k = self._column(index, 10, used)
            if thing.value == 3:
                total_label, total_next = '结转', '总商品净额'
            else:
                total_label, total_next = '结存', '总库存净额'
            grand0 = None
            for m in range(min(len(col_j), len(col_k)) - 1, -1, -1):
                if (col_j[m] == total_label) and (col_k[m] == total_next):
                    grand0 = m
                    break
            if grand0 == None:
                # 连总计行都没有（空模板）：把新块摆在表尾
                grand0 = max(used, 5)
            else:
                for m in range(min(len(col_j), len(col_k))):
                    if (col_j[m] == total_label) and (col_k[m] == total_next):
                        sht[m, 9].value = None
                        sht[m, 10].value = None
                        sht[m, 11].value = None
            # 新块摆在表尾时先腾位置，保证块内行不越出已有版面
            if grand0 + 1 > used:
                sht.range(f'{used + 1}:{grand0 + 8}').insert(shift='down')
                grand0 = grand0 + 8
            anchor0 = grand0 - 5               # 锚点行 = 总计行 - 5
        if new_block:
            title0 = anchor0 - 4
            unit0 = anchor0 - 3
            head0 = anchor0 - 2
            sub0 = anchor0 - 1
            name0 = anchor0                      # 绿色锚点行 = 货品名行
            a0 = anchor0 + 1                     # 块内第一个数据行
            a1 = anchor0 + 2                     # 块内第二个数据行
            g0 = anchor0 + 3                     # 图例行
            t0 = anchor0 + 4                     # 块内合计行
            grand0 = anchor0 + 5                 # 全局总计行
            cost0 = anchor0 + 6                  # 商品：主营业务成本行
            row = title0 + 1
            sht[title0, 0].value = '库存商品成本期末移动平均结转报告'
            sht.range(f'A{row}:L{row}').merge()
            sht[unit0, 0].value = '编制单位：XXX公司'          # 可以处理
            cdt = dt.date.today()
            dtstr = cdt.strftime("%Y/%m/%d")
            sht[unit0, 8].value = f'报告日期：{dtstr}'
            sht.range(f'I{row+1}:K{row+1}').merge()
            sht[head0, 0].value = '库存货品名称及批次'
            sht.range(f'A{row+2}:A{row+3}').merge()
            sht[head0, 1].value = '期初数'
            sht.range(f'B{row+2}:C{row+2}').merge()
            sht[sub0, 1].value = '数量'
            sht[sub0, 2].value = '总计金额'
            sht[head0, 3].value = '采购入库'
            sht.range(f'D{row+2}:F{row+2}').merge()
            sht[head0, 3].color = '#FFFF00'
            sht[sub0, 3].value = '数量'
            sht[a0, 3].value = 0
            sht[a1, 3].value = 0
            sht[sub0, 4].value = '单价'
            sht[sub0, 5].value = '采购金额'
            sht[head0, 6].value = '耗用出库'
            sht.range(f'G{row+2}:I{row+2}').merge()
            sht[head0, 6].color = '#FF3399'
            sht[sub0, 6].value = '数量'
            sht[a0, 6].value = 0
            sht[a1, 6].value = 0
            sht[sub0, 7].value = '加权平均单价'
            sht[sub0, 8].value = '出库金额'
            sht[head0, 9].value = '结存'
            sht.range(f'J{row+2}:L{row+2}').merge()
            sht[head0, 9].color = '#FF8000'
            sht[sub0, 9].value = '剩余数量'
            sht[sub0, 10].value = '加权平均单价'
            sht[sub0, 11].value = '剩余总额'
            # 数据行公式（1 基行号）
            rn, r0, r1 = name0 + 1, a0 + 1, a1 + 1
            sht[name0, 0].value = name
            sht[name0, 0].color = GREEN
            sht[a0, 2].formula = f'=20*B{rn}'
            if (thing.value != 3):
                sht[a0, 5].value = 0
                sht[a1, 5].value = 0
                sht[a0, 5].formula = f'=D{r0}*E{r0}'
                sht[a1, 5].formula = f'=D{r1}*E{r1}'
            elif thing.value == 3:
                sht[a0, 4].formula = f'=IF(D{r0}=0,0,F{r0}/D{r0})'
                sht[a1, 4].formula = f'=IF(D{r1}=0,0,F{r1}/D{r1})'
            sht[a1, 7].formula = f'=K{r0}'
            sht[a0, 8].formula = f'=IF(F{r0},G{r0}*G{r0},0)'
            sht[a1, 8].formula = f'=IF(F{r1},G{r1}*G{r1},0)'
            sht[a0, 9].formula = f'=D{r0}-G{r0}+J{r0-1}'
            sht[a1, 9].formula = f'=D{r1}-G{r1}+J{r1-1}'
            sht[a0, 10].formula = f'=IF(J{r0}=0,0,L{r0}/J{r0})'
            sht[a1, 10].formula = f'=IF(J{r1}=0,0,L{r1}/J{r1})'
            sht[a0, 11].formula = f'=IF(F{r0},L{r0-1}-I{r0},L{r0-1}+F{r0})'
            sht[a1, 11].formula = f'=IF(F{r1},L{r1-1}-I{r1},L{r1-1}+F{r1})'
            # 图例行
            sht[g0, 5].value = '本期采购入库'
            sht[g0, 6].formula = f'=SUM(F{r0}:F{r1})'
            sht[g0, 7].value = '本期出库金额'
            sht[g0, 8].formula = f'=SUM(I{r0}:I{r1})'
            sht[g0, 10].value = '剩余总额'
            sht[g0, 11].formula = f'=L{r1}'
            # 块内合计行只在 L 列放本块小计
            sht[t0, 11].formula = f'=L{g0+1}'
            # 全局总计行（全表只保留这一处，上面已删掉上一段的同类行）
            sht[grand0, 9].value = ('结转' if thing.value == 3 else '结存')
            sht[grand0, 10].value = ('总商品净额' if thing.value == 3 else '总库存净额')
            if (thing.value == 3):
                # 商品才多一行「主营业务成本」；材料/零件这一行要保持空白
                sht[cost0, 9].value = None
                sht[cost0, 10].value = '主营业务成本'
            sht.grand_row0 = grand0
            sht.cost_row0 = cost0
            self.wb.save()
        # 复用到还没用过的块时，把货品名与绿色锚点补上（新建块在上面已写好）
        if (not used_block) and (a_at(anchor0) == None):
            sht[anchor0, 0].value = name
            sht[anchor0, 0].color = GREEN
        # 写入数据：数据行从锚点行+2 起（锚点行+1 是「数量/总计金额」表头行），
        # 一直延伸到「本期采购入库」图例行之前；整段满员时 _free_item_row 会自动扩充一行。
        # add 与 minus 各按自己那一列找空位，互不抢行（两者可以写在同一行的不同列）。
        if add == True:
            i = self._free_item_row(sht, data_at, anchor0, add=True,
                                    blank_only=new_block, name=name)
            sht[i, 3].value = number
            if thing.value == 3:
                sht[i, 5].value = float(price)          # 商品的金额列是 F（输入值）
            else:
                sht[i, 4].value = float(price)
        if minus == True:
            i = self._free_item_row(sht, data_at, anchor0, add=False,
                                    blank_only=new_block, name=name)
            sht[i, 6].value = number
        grand0 = getattr(sht, 'grand_row0', None)
        if grand0 == None:
            # 复用已有块（本次没新建）时，总计行从表尾标签里找
            col_j = self._column(index, 9, used)
            col_k = self._column(index, 10, used)
            want_j, want_k = (('结转', '总商品净额') if thing.value == 3 else ('结存', '总库存净额'))
            for m in range(len(col_j) - 1, -1, -1):
                if (m < len(col_k)) and (col_j[m] == want_j) and (col_k[m] == want_k):
                    grand0 = m
                    break
        if grand0 != None:
            sht[grand0, 11].formula = self._sum_blocks(sht)
            address = f"'{sht.name}'!$L${grand0 + 1}"
            if thing.value == 1:
                self._link_account('原材料', sht.name, address)
            if thing.value == 2:
                self._link_account('半成品（零件）', sht.name, address)
            if thing.value == 3:
                self._link_account('库存商品', sht.name, address)
                cost0 = getattr(sht, 'cost_row0', None)
                if cost0 == None:
                    cost0 = grand0 + 1
                cost = self._sum_out(sht)
                sht[cost0, 11].formula = (f"{cost[1:]}+'明细账汇总-损益类科目'!N25"
                                          if cost != '=0' else "='明细账汇总-损益类科目'!N25")
                self._link_account('主营业务成本-存货成本', sht.name,
                                   f"'{sht.name}'!$L${cost0 + 1}")
        self.wb.save()
        self.recalc()                                 # 提醒2：写入后自动重算

    def _free_item_row(self, sht, data_at, anchor0: int, add: bool, blank_only: bool, name: str) -> int:
        """在块内找一个能写这笔记账的行，返回 0 基行号。

        模板块的数据区是 锚点行+2 起（锚点行+1 是「数量/总计金额」表头行）一直延伸到
        「本期采购入库」图例行之前，模板里是一整段预先写好公式的空白数据行，
        所以这里按整段扫描，而不是只看紧挨着的两行。
        模板里 add 写「数量」列(D)/「单价」列(E 或 F)，minus 写「出库数量」列(G)，两者不冲突，
        所以按各自那一列找空位；整段都用满了才在图例上方插一行自动扩充。
        """
        if blank_only:
            free_add = lambda row0: self._is_blank(data_at(row0, 0))
            free_minus = lambda row0: self._is_blank(data_at(row0, 3))
        else:
            free_add = lambda row0: data_at(row0, 0) in (None, '', 0)
            free_minus = lambda row0: data_at(row0, 3) in (None, '', 0)
        is_free = free_add if add else free_minus
        target = None
        row0 = anchor0 + 2
        for _ in range(BLOCK_SCAN):              # 上限只作防御，正常会在图例行处停住
            if data_at(row0, 2) == '本期采购入库':   # F 列图例行：数据区到此为止
                break
            if is_free(row0):
                target = row0
                break
            row0 += 1
        if target == None:
            # 提醒1：整个数据区都用满了 → 自动扩充一行（插在图例上方，沿用上一行格式与公式）
            insert0 = anchor0 + 3
            donor0 = insert0 - 1
            print(f"[扩充] {sht.name} 的货品 {name} 所在块数据行已用满，自动插入一行")
            sht.range(f'{insert0 + 1}:{insert0 + 1}').insert(shift='down')
            sht.range(f'A{donor0 + 1}:L{donor0 + 1}').api.Copy()
            sht.range(f'A{insert0 + 1}:L{insert0 + 1}').api.PasteSpecial(-4104)
            try:
                self.xlapp.api.CutCopyMode = False
            except Exception:
                pass
            target = insert0
        return target

    def _sum_blocks(self, sht) -> str:
        """汇总全表所有块的「剩余总额」（L 列），替代原来写死 L199/L181/L182 的补丁。
        这里重新取一次表尾行号：新建块时上面插过行，用旧的 used 会漏掉新块。"""
        labels = self._column(sht.index, 10)
        return self._sum_of(labels, '剩余总额', 'L')

    def _sum_out(self, sht) -> str:
        """出库金额汇总。原代码匹配的是模板里不存在的「本期出库金额：」（多一个全角冒号），
        结果恒为空串，这里匹配真实的标签「本期出库金额」。"""
        labels = self._column(sht.index, 7)
        return self._sum_of(labels, '本期出库金额', 'I')

    @staticmethod
    def _sum_of(labels, text: str, column_letter: str) -> str:
        parts = ''
        for index, label in enumerate(labels):
            if label == text:
                parts += f'{column_letter}{index + 1}+'
        parts = parts[:-1]
        return f'={parts}' if parts else '=0'

    def _closing_row_by_sum(self, sheet, column1: int) -> int:
        """按「SUM 行的下一行」找期末行（0 基）。科目块的固定结构就是数据区、SUM 行、期末行。"""
        self.ensure_connection()
        sht = self.wb.sheets[sheet]
        used = max(self._last_row(sheet), 1)
        self.read_calls += 1
        formulas = self._flatten(sht.range((1, column1), (used, column1)).formula)
        for row0, value in enumerate(formulas):
            if value != None and 'SUM(' in str(value):
                return row0 + 1
        rowT, _ = self._find_header(sheet, '银行存款')
        return (rowT if rowT != None else 0) + 4

    def _link_account(self, header: str, sheet_name: str, address: str):
        """把明细表汇总格的地址写到汇总表（资产表/损益表）的科目行。
        汇总表科目块布局：科目名(表头行) / 借贷名 / 期初 / 数据行… / SUM 行 / 期末行；
        必须写到期末行上（= SUM 行的下一行），写到数据行会既进不了 SUM 区间、
        又和期末行重复计算。
        原代码 shtT.range(found_cell.Address) 得到的是绝对地址（如 $AT$1），再 +2/-1
        会回落成 $AT$1 本身、目标列解析错；而且把损益表的「主营业务成本-存货成本」
        写到了商品表自身上，这里跳过本表。"""
        for index in (4, 6):
            if self.wb.sheets[index].name == sheet_name:
                continue
            rowT, columnT = self._find_header(index, header)
            if columnT == None:
                continue
            column0 = columnT - 1                  # 0 基：科目名所在列
            row0 = self._closing_row_by_sum(index, column0 + 1)
            self.wb.sheets[index][row0, column0].value = f"={address}"

    @staticmethod
    def _flatten(values) -> list:
        """把 xlwings 读回来的值摊平成一维（整列 .formula 可能返回嵌套元组）。"""
        out = []
        stack = [values]
        while stack:
            item = stack.pop(0)
            if isinstance(item, (list, tuple)):
                stack = list(item) + stack
            else:
                out.append(item)
        return out

    def _closing_row(self, sheet, columns1, marker: str) -> int:
        """找科目块里的「期末行」（0 基）。columns1 是要一起看的列号（1 基）。
        模板里这一行的公式引用了银行流水账，所以按「整列读回公式、找哪一行带流水账表名」
        来定位（marker 就是那个表名）；找不到就退回表头行 +4。"""
        self.ensure_connection()
        sht = self.wb.sheets[sheet]
        marker = str(marker)
        used = max(self._last_row(sheet), 1)
        found = None
        for column1 in columns1:
            self.read_calls += 1
            formulas = self._flatten(sht.range((1, column1), (used, column1)).formula)
            for row0, value in enumerate(formulas):
                if value != None and marker in str(value):
                    found = row0
        if found == None:
            rowT, _ = self._find_header(sheet, '银行存款')
            found = (rowT if rowT != None else 0) + 4
        return found

    def _entries_sheet_ref(self) -> str:
        """银行流水账的表名引用前缀，如 "'银行流水账'!"。"""
        self.ensure_connection()
        sheet_name = self.wb.sheets[0].name
        if not isinstance(sheet_name, str):
            sheet_name = str(sheet_name)
        return "'" + sheet_name.replace("'", "''") + "'!"

    def _entries_opening_ref(self) -> str:
        """账户期初余额在流水账 B 列（表头行下面是期初行）。"""
        return f'{self._entries_sheet_ref()}B{ENTRIES_OPENING_ROW}'

    def _entries_net_formula(self, last_row1: int, first_row1: int) -> str:
        """本期净变动 = 银行流水账 F{末} - F{首}（带表名，避免被当成本表的单元格）。"""
        prefix = self._entries_sheet_ref()
        if last_row1 == first_row1:
            return f'{prefix}F{last_row1}'
        return f'{prefix}F{last_row1}-{prefix}F{first_row1}'

    @staticmethod
    def _account_key(header: str, sheet_name: str) -> tuple:
        return (sheet_name, header)

    # ------------------------------------------------------------------
    # 明细账汇总表写入（原：逐格 while 读两列找空行）
    # ------------------------------------------------------------------
    @_batch_call
    def add_book_assets(self, type: BOOOKTYPE, name: ASSET, add: Decimal, minus: Decimal):
        #
        if type == BOOOKTYPE.ASSETS:
            index = 4
        elif type == BOOOKTYPE.LIABILITIES:
            index = 5
            n = minus
            minus = add
            add = n
        elif type == BOOOKTYPE.EQUITY:
            index = 6
            n = minus
            minus = add
            add = n
        sht = self.book.sheets[index]
        rowT, columnT = self._find_header(index, name.value)
        if columnT == None:
            raise KeyError(f"{sht.name} 中没有科目：{name.value}")   # 原来会 AttributeError
        column0 = columnT - 1
        scan = self._scan_row(index)
        # 借贷两列各批量读一次，在内存里有界扫描第一个空行（原 while 没有终止条件）
        debit_col = self._column(index, column0, scan)
        credit_col = self._column(index, column0 + 1, scan)
        row0 = None
        for m in range(max(rowT + 3, 3), min(len(debit_col), len(credit_col))):
            if self._is_blank(debit_col[m]) and self._is_blank(credit_col[m]):
                row0 = m
                break
        if row0 == None:
            raise RuntimeError(f"{sht.name} 的 {name.value} 已无空行可写")
        # 记住这个科目用的数据行：add_book_item 的跨表链接要写到同一行，不能另起一行
        self._account_rows[self._account_key(name.value, sht.name)] = row0
        # 同一行的借贷两格一次写完（只写这两格，不动其它行的公式）
        self._write_range(index, row0 + 1, column0 + 1, [[float(add), float(minus)]])
        if name.value == '银行存款':
            # 模板的科目块布局：期初行 / 数据行… / SUM 行 / 期末行。
            # 期末行原来是「本期净变动 - 账户期初资金」，期初资金应当相加而不是相减，
            # 所以这里重写成「账户期初余额(B 列) + 本期净变动」。
            # 期末行的位置不写死行号，直接按「模板里引用了银行流水账的那一行」找出来。
            # 注意 F 列在「银行流水账」表上，必须带表名：不写表名的 =F164 会被当成本表的 F164
            #（空），算出来恒为 0。原代码还把「本行地址 - entries_to_assets_money」拼成字符串，
            # 而那个类属性只存最后一笔的地址（可能指向已被插行顶掉的行），并重复减了一次。
            first_row = getattr(self, 'entries_to_assets_first_row', None)
            last_row = getattr(self, 'entries_to_assets_row', None)
            # 期末行：模板里引用「银行流水账」的那一行（B/C 两列一起看）
            closing0 = self._closing_row(index, (column0 + 1, column0 + 2),
                                         self.wb.sheets[0].name)
            if last_row == None:
                sht[closing0, column0].value = float(minus)
            else:
                if first_row == None:
                    first_row = last_row
                # 期末余额 = 账户期初余额（在流水账的 B 列）+ 本期净变动，
                # 写在期末行的「借方」格里——资产负债表读的就是这一格。
                opening = self._entries_opening_ref()
                net = self._entries_net_formula(last_row + 1, first_row + 1)
                sht[closing0, column0].formula = f'={opening}+{net}'
        self.wb.save()
        self.recalc()                                 # 提醒2：写入后自动重算
