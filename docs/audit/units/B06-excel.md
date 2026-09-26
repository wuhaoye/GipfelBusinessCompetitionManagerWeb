# B06 Excel 建包工具链（分支归属：test 分支独有）

## 概述

**审计范围**（`git diff --name-status feature/contract-type-code test` 逐一核对，均为 test 分支独有）：

| 文件 | 行数 | 角色 |
| --- | --- | --- |
| `backend/examples/excel/sheet_spec.py` | 1094 | 表格规范本体：21 张表的列定义、中文表头、单元格语法、各表 handler |
| `backend/examples/excel/xlsx_io.py` | 304 | 极简 xlsx/CSV 读写（纯标准库） |
| `backend/examples/excel/build_from_sheets.py` | 432 | 读表 → 建包 → 写归档 → `archive.apply_import` 落库 |
| `backend/examples/excel/make_template.py` | 143 | 生成空白模板 / xlsx ↔ CSV 目录互转 |
| `backend/examples/excel/make_sample_auto_chain.py` | 330 | 生成「汽车产业链示例 / 最小示例」两份表格 |
| `backend/examples/competitions/auto_chain_competition.py` | 501 | 汽车产业链代码建包脚本 |
| `backend/examples/competitions/auto_chain_setup.py` | 220 | 建比赛 + 重算 + 去重 + 口令 + 体检摘要 |
| `backend/examples/competitions/auto_chain_smoke.py` | 186 | 三份预置合同端到端冒烟（事务回滚） |
| `backend/examples/contracts/auto_chain_contracts.py` | 291 | 开采 / 购销 / 运输三大合同类型 |
| `backend/examples/contracts/mini_contracts.py` | 78 | 最小购销合同类型（教程示例引用） |
| `docs/比赛Excel建包教程.md` / `比赛Excel建包规范.md` / `汽车产业链测试赛准备.md` | 386 / 626 / 166 | 文档（只审「文档承诺了但代码没做/做错」） |

**审计方法（全部只读，无任何写库操作）**：

1. 通读上表全部代码 + 三份文档，并用 `git diff --name-status feature/contract-type-code test` 核对分支归属；
2. 用只读脚本真实加载仓库自带的三个 xlsx（`最小示例.xlsx` / `汽车产业链示例.xlsx` / `比赛建包模板.xlsx`），并手工构造 xlsx 部件验证公式 / 错误值 / 富文本 / 合并单元格 / 日期序列号 / 数字格式 / 超链接 / 控制字符 / 重名工作表等单元格边界；
3. 直接调用 `build_from_tables()`（内存建包，**不调用** `apply_import`）构造 40+ 组边界表，观察归档 JSON 的实际内容与 `validate()` 结果；
4. 只读方式（`sqlite3.connect("file:db.sqlite3?mode=ro", uri=True)`）核对库内比赛占用情况（#189 已有 9 家公司 / 2 燃料 / 6 载具等），用于验证「重复执行」相关结论；
5. 以只读方式跑 `build_from_sheets.py <仓库自带表格> --inspect` 复核文档给出的示例输出（最小示例实测输出与教程第 1 节逐行一致）。

**结论摘要**：工具链的架构是干净的（只把表格翻译成 `CompetitionBuilder` 调用、复用既有 `apply_import`、`--dry-run` 确实走事务回滚），但**单元格边界层是最薄弱的一环**：以 `#` 开头的真实数据行会被整行静默丢弃、负数与超限数值没有任何下界/上界校验、文本被无条件 `strip()`、重名工作表静默覆盖、非法控制字符与非法工作表名会产出打不开的 xlsx；错误处理只覆盖自家异常，损坏/被占用的文件直接抛 Python 堆栈。文档在「幂等」「只读」「空表/覆盖」四处承诺与代码不一致。

---

## 缺陷清单

### [P0] Z-01 首列以 `#` 开头的**真实数据行**会被当注释整行静默丢弃

- 位置：`backend/examples/excel/build_from_sheets.py:326-336`
- 代码：

```python
    for offset, raw in enumerate(table[header_index + 1:], start=header_index + 2):
        cells = [str(c or "").strip() for c in raw]
        if not any(cells):
            continue
        if cells[0].startswith("#") or cells[0].startswith("//"):
            continue
        row = {
            headers[i]: cells[i]
            for i in range(min(len(headers), len(cells)))
            if headers[i] and cells[i] != ""
        }
```

- 触发条件与实测（均为内存建包，未落库）：

```
A. 首列是公式错误值：
   区域 = [["区域名称","说明"], ["甲",""], ["#N/A","公式没匹配到的区域"], ["乙",""]]
   stats = {'区域': 2}       regions = [{'_id':1,'name':'甲'}, {'_id':2,'name':'乙'}]
   → '#N/A' 那一行整行消失，连 stats 计数都不含它

B. 首列是合法名称（货号/编号风格）：
   区域 = [["区域名称","说明"], ["#1 号矿区","首列以 # 开头的正式名称"], ["乙区",""]]
   stats = {'区域': 1}       regions = [{'_id':1,'name':'乙区'}]
   → 「#1 号矿区」被当成注释丢弃
```

- 后果：
  1. **静默丢数据**——这是整条工具链里唯一"用户完全看不到"的失效方式：`--inspect` 的"已处理的工作表 N 行"、`--out` 的归档、导入结果都会一致地少一行，没有任何 warning。用户会以为建全了。
  2. 首列是公式列时**极易命中**：`产品名称`/`区域名称`/`原料名称` 用 `VLOOKUP` 或 `CONCAT` 拼出来，只要有一个值算出 `#N/A`、`#REF!`、`#DIV/0!`，那一行就没了；而 `#N/A` 恰恰是最常见的"没匹配上"信号，也就是最需要被看见的那一行。
  3. 以 `#` 开头的正式名称（`#1 号矿区`、`#A 类客户`）无法录入，且不会有任何提示。
- 修复建议：注释行只认"整行第一格以 `#`/`//` 开头**且该行没有别的非空单元格**"（更严格的做法：只认第一格以 `# ` 或 `//` 开头、并且该行不含任何会被解析的数据列），或者干脆要求注释使用专门的语法/表；对"被跳过的行"累计计数并在 `--inspect` 里打印（例如"跳过 3 行注释、跳过 1 行疑似注释的首列 #N/A"）。

### [P0] Z-02 数值列完全没有下界校验：负数距离 / 负单价 / 负配比会一路写进归档

- 位置：`backend/examples/excel/sheet_spec.py:530`、`:543`、`:549`、`:575`、`:583`、`:595`、`:603`、`:610`、`:617`、`:633`
- 代码（`sheet_spec.py:530-540`，同类写法遍布 10 个 handler）：

```python
def _h_edge(ctx: SheetContext, row: dict) -> None:
    src, dst = row.get("from_node"), row.get("to_node")
    if not src or not dst:
        raise SheetFormatError("地图连线表的 from_node 与 to_node 必填")
    distance = row.get("distance")
    if not distance:
        raise SheetFormatError("地图连线表的 distance 必填")
    path_type = row.get("path_type")
    if not path_type:
        raise SheetFormatError("地图连线表的 path_type 必填（载具按它判断能否通行）")
    ctx.builder.edge(src, dst, _as_scalar(distance), path_type)
```

- 触发条件：把 `距离(公里)` 填 `-3`、`每升单价` 填 `-7.6`、`容量/单价` 填负数、`原料配比` 填 `矿石*-4`、`每公里油耗` 填 `-0.35`、`碳排系数` 填 `-0.5`、`研发费用` 填 `-80000`。表里只需一个负号，工具全流程不报错。
- 实测（内存建包）产出归档：

```
mapEdges         [{'fromNodeName': 'N1', 'toNodeName': 'N2', 'distance': -3.0, ...}]
fuels            [{'pricePerLiter': '-7.6', ...}]
vehicles         [{'fuelConsumptionPerKm': -0.35, 'maxCargo': -30.0, 'price': '-260000', 'carbonEmission': -0.9, ...}]
partMaterials    [{'partName': 'P1', 'materialName': '矿石', 'ratio': -4, ...}]
materials        [{'carbonEmissionCoefficient': -0.5, ..., 'nodePricesByName': {'N1': '-180'}}]
validate(): []                      # 一条提醒都没有
```

- 后果：这些数值直接进 `archive`、由既有导入引擎落库（导入侧 `_imp_*` 也按原值写）。下游按它们算钱：运输合同 `freight = distance * rate_per_km * trips + fuel_cost`（`auto_chain_contracts.py:235`），`distance=-3` 时运费为负，`sub_number(client.cash, 负运费)` 会让**委托方加钱、承运方扣钱**——整条产业链的经济模型被写反，而表格工具毫无提示。负 `maxCargo` 让「超重加价」永远不触发或永远触发；负 `ratio` 让配比聚合出负数产量。规范里 `distance` 用 `if not distance` 判空，于是 0 被拒、`-3` 放行，恰好把唯一能解释的边界反过来了。
- 修复建议：在 `sheet_spec` 的数值 handler 里补显式下界（`distance > 0`、`price_per_liter >= 0`、`capacity >= 0`、`max_cargo >= 0`、`ratio > 0`、`fuel_consumption_per_km >= 0`、`carbon_emission_coefficient >= 0`、`research_cost >= 0`、`footprint >= 0`；坐标允许负数要显式声明），错误信息带表名 + 行号；并在 `build_from_sheets.py` 产出归档前做一次「数值列非负」的兜底体检。
- 说明：调用方 `CompetitionBuilder` 只做类型校验（`builder/types.py::positive_int` 仅用于 x/y/tier/sortOrder/laborCount），数值范围不在它的职责内，因此这是本工具的缺陷。

### [P0] Z-03 单元格文本被无条件 `strip()` 并数字文本化：全角数字被转成半角键、文本精度保证不成立

- 位置：`backend/examples/excel/build_from_sheets.py:297`、`:327`、`:332-336`；`backend/examples/excel/sheet_spec.py:131-141`
- 代码（`build_from_sheets.py:296-300` 取表头、`:325-336` 取数据行）：

```python
    for index, raw in enumerate(table):
        cells = [str(c or "").strip() for c in raw]
        if any(cells):
            header_index, raw_headers = index, cells
            break
...
    for offset, raw in enumerate(table[header_index + 1:], start=header_index + 2):
        cells = [str(c or "").strip() for c in raw]
        if not any(cells):
            continue
        if cells[0].startswith("#") or cells[0].startswith("//"):
            continue
        row = {
            headers[i]: cells[i]
            for i in range(min(len(headers), len(cells)))
            if headers[i] and cells[i] != ""
        }
```

- 代码（`sheet_spec.py:131-141`，一切「数字列」的公共入口）：

```python
def _as_scalar(text: str) -> Any:
    """标量：能当数字就当数字（配比/价格），否则保留文本。"""
    s = str(text).strip()
    if s == "":
        return ""
    try:
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
        return float(s)
    except ValueError:
        return s
```

- 触发条件：在任意单元格写全角数字（`１００`、`２００１`）或带前后空格的名称/键（`" 白云鄂博矿区 "`）。
- 实测：

```
_as_scalar('007')   -> 7      (int)
_as_scalar('１００') -> 100    (int)
_as_scalar('１')     -> 1      (int)
_as_scalar('1e3')   -> 1000.0 (float)
_as_scalar('1_000') -> 1000.0 (float)
```

- 后果：
  1. **全角/前导零的标识被静默改写，跨表引用随之错位**：地点价的键是地图节点名，`１００:195` 会被归一成 `100`；全角中文输入法下写出全角数字非常常见。更严重的是全局资源 `产业类型 code`：表格里写 `００１０` 会被导入成 `10`，与库里既有 code 撞车 → 导入侧按 code **复用并覆盖**该产业类型的名称/描述（`archive.py::_imp_industry_types`），静默改掉另一套行业口径。
  2. **文本精度保证不成立**：教程第 2.2 节写「金额、单价、数量**建议直接写数字文本**（`600000`、`0.35`），程序按文本保精度」。实际上只有"非数字文本"保留原文；`0.1` 这类小数会被 `float()` 变成二进制浮点，`600000` 变成 `int`。`keep_text=True` 只用在 `原料` 的地点价路径，且只对"非 JSON 单元格"生效；JSON 单元格（`{"锂矿石": 180}`）里值写成数字仍然变成 `int`。
  3. **前后空格差异被抹平**：`" 甲 "` 与 `"甲 "` 都被当成"甲"，构建器按名判重会报"重复登记"，与用户"这两行名字不一样"的直觉不符；反过来 `" 甲 "` 与 `"甲"` 又被合并成一条。
- 修复建议：单元格值保留原文（只在判定"是否为空"时 `strip()`，写库/匹配时按列类型决定是否 trim）；`_as_scalar` 增加「仅 ASCII 数字」判定（`re.fullmatch(r"[+-]?[0-9]+", s)`）并对全角输入给出明确中文提示，而不是静默归一；把"文本保精度"改成金额列统一走字符串，并在文档里写清哪些列会被 `float` 介入。

### [P0] Z-04 股票参数只写一个键就绕过全部交叉校验：`limitPct` 可设成 1000

- 位置：`backend/examples/excel/sheet_spec.py:725-729`、`:757-769`、`:732-754`
- 代码（`sheet_spec.py:725-729` 与 `:757-766`）：

```python
    # 逐行并入：与已登记的配置合并后再交给建包库（stock_config_set 是整份替换语义）
    merged = dict(getattr(ctx.builder, "stock_config", None) or {})
    merged[param] = value
    _assert_stock_config_sane(merged, ctx)
    ctx.builder.stock_config_set(merged)


def _assert_stock_config_sane(config: dict, ctx: SheetContext) -> None:
    """建包库只校验「非空字典」，这里补上引擎口径的两条硬约束与一条风险提示。"""
    limit, move = config.get("limitPct"), config.get("maxMovePct")
    if limit is not None and move is not None and float(limit) < float(move):
        raise SheetFormatError(
            f"limitPct（单轮限幅 {limit}）不能小于 maxMovePct（单轮最大波动 {move}）"
        )
    low, high = config.get("mmMinQty"), config.get("mmMaxQty")
    if low is not None and high is not None and float(low) > float(high):
        raise SheetFormatError(f"mmMinQty（做市商最小数量 {low}）不能大于 mmMaxQty（{high}）")
    if limit is not None and float(limit) > 0.1:
        ctx.note(f"股票参数：limitPct={limit} 已高于 10%，玩家更容易把价格拉到涨停"
                 f"（引擎的防连板机制仍会兜底，但建议不超过 0.10）")
```

- 触发条件：表格里只写一行 `limitPct`（不写 `maxMovePct`），值填任意大的数字。
- 实测（内存建包）：

```
只写 limitPct=0.5   -> 通过，stock_config = {'limitPct': 0.5}
   运行期合并（apps/stock/engine.py::resolve_stock_config）= {'limitPct': 0.5, 'maxMovePct': 0.05, ...}
只写 limitPct=100   -> 通过，stock_config = {'limitPct': 100.0}（只给一条提示）
只写 limitPct='1e3' -> 通过，stock_config = {'limitPct': 1000.0}
两行都写但填反   -> 被拦下：SheetFormatError: [股票参数] 第 3 行：limitPct（单轮限幅 0.02）不能小于 maxMovePct（0.05）
```

- 后果：文档（规范第 4 节末、教程 3.7 节）明确承诺「表格层会替你挡住三类问题：未知参数名、`limitPct < maxMovePct`、`mmMinQty > mmMaxQty`」，而教程 3.7 节给出的示例表正好**只写了 `limitPct` 与 `maxMovePct` 两行**。实际行为是：只要 `maxMovePct` 不在表格里，运行期会被补成默认 0.05（`engine.py:382-388`），于是 0.5/0.05 这种「限幅小于最大波动」的非法组合被写进 `Competition.stockConfig`。`limitPct` 还没有上界：填 `1e3` 只得到一句"建议不超过 0.10"的提示，落库后单轮涨跌幅上限是 100000%。规范第 7 节 6 还说明导入**不受 `append/overwrite` 影响、无条件覆盖**目标比赛的参数，因此一次手滑会直接改掉既有比赛的股票规则。
- 修复建议：`_assert_stock_config_sane` 改为「与 `DEFAULT_STOCK_CONFIG` 合并后再校验」（缺省键按运行期默认补齐参与比较），把 `limitPct > 0.10` 从 `note` 提升为拒绝或要求显式确认，并给 `_stock_config_value` 加有限性/上界校验（`math.isfinite`）。

### [P1] Z-05 文件级异常未被捕获：损坏文件 / 被 Excel 占用 / 无权限 → 直接抛 Python 堆栈

- 位置：`backend/examples/excel/build_from_sheets.py:114-124`、`:130-134`、`:149-153`；源头 `backend/examples/excel/xlsx_io.py:118`
- 代码（`build_from_sheets.py:114-124`）：

```python
    try:
        builder, stats, notes = build_from_tables(
            Path(args.source), only_sheets=args.sheets, fallback_name=args.name,
            name_override=args.name,
        )
        if args.competition is not None:
            _try_resolve_card_fields(builder, args.competition, notes)
        archive = builder.build()
    except (SheetFormatError, SheetBuildError, BuilderError, ValueError) as exc:
        print(f"✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
```

- 代码（`xlsx_io.py:115-118`，异常源头）：

```python
def read_xlsx(path: str | Path) -> dict[str, list[list[str]]]:
    """读 xlsx：{工作表名: [[单元格文本, ...], ...]}（空尾列已裁剪，整行空行保留）。"""
    tables: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(Path(path)) as zf:
```

- 触发条件与实测：
  1. 把 Excel 2003 的 `.xls`（OLE 复合文档）改名成 `.xlsx` → `zipfile.BadZipFile: File is not a zip file`（实测复现：打印完整 traceback，退出码 1）；
  2. 损坏的 xlsx（内容被截断/写坏）→ 同上；
  3. 文件正在 Excel 里打开（Windows 上会短暂独占）→ `PermissionError`；
  4. 表格路径是目录但不可读、有软链接环 → `OSError`；
  5. `--out` 写盘失败（`:149-153`）同样在 try 之外。
- 后果：规范第 1 节写明「退出码：`0` 成功；`1` 表格格式错误或导入出现 problem；`2` 用法错误」，教程第 8 节《常见错误速查》也承诺给出中文可读提示。这几种情况全部落空：用户看到的是 `zipfile.BadZipFile` 堆栈，无法判断是"文件格式不对"还是"工具坏了"。注意 `.xls`（保留原扩展名）会被 `load_tables` 的中文 `ValueError` 正确挡住，踩坑的是"另存为时只改了扩展名"这一最常见操作。
- 修复建议：把 `zipfile.BadZipFile`、`OSError`（含 `PermissionError`、`FileNotFoundError`）翻译成 `SheetBuildError`，提示"该文件不是 .xlsx/.xlsm；若是 Excel 2003 的 .xls，请先用 Excel 另存为 .xlsx；若文件正在 Excel 中打开请先关闭"；把 `--out` 写盘与 `--create-competition` 一并纳入 try 范围。

### [P1] Z-06 重复执行同一行命令会直接失败：`--create-competition` 复用同名比赛后撞上"非空保护"

- 位置：`backend/examples/excel/build_from_sheets.py:229-247`（复用已有比赛）、`:130-134` 与 `:388-410`（导入）
- 代码（`build_from_sheets.py:241-247`）：

```python
    competition = Competition.objects.filter(name=name).first()
    if competition is None:
        competition = Competition.objects.create(name=name, status=status)
        print(f"已新建比赛：#{competition.id}「{competition.name}」（状态 {competition.status}）")
    else:
        print(f"复用已有比赛：#{competition.id}「{competition.name}」（状态 {competition.status}）")
    return competition.id
```

- 代码（`build_from_sheets.py:400-410`，`allow_non_empty` 默认关闭）：

```python
        result = archive_builder.apply_import(
            archive, competition_id,
            dry_run=bool(args.dry_run),
            allow_non_empty=bool(args.allow_non_empty),
            mode=args.mode,
            only_resources=only_resources,
            user=None,
        )
    except archive_builder.ArchiveError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
```

- 触发条件：按教程第 1 节 / 第 5 节的原样命令跑第二遍：

```powershell
.\.venv\Scripts\python.exe examples/excel/build_from_sheets.py examples/excel/最小示例.xlsx --create-competition
```

  第一次导入后比赛内已有 `区域 / 燃料 / 基建 / 载具 / 仓库 / 生产线 / 消费者需求` 等比赛级数据；`archive.py:2621-2644` 的占用检查命中、`allow_non_empty=False` → 抛 `ArchiveError` → 退出码 2，提示「目标比赛已有业务数据（…），默认拒绝导入以避免污染既有数据」。
- 证据（只读查库）：`db.sqlite3` 里 #189「2026 汽车产业链测试赛」已有 `fuels=2, vehicles=6, regions=3, consumer_demands=4, warehouses=4, production_lines=4, infrastructures=3, companies=9`，占用检查必然命中。
- 后果：三处承诺与实现不符，且都是用户会照着抄的命令：
  1. 规范第 1 节「`--create-competition` 按「比赛」表的名称新建比赛（同名复用），再导入」+ 教程 3.1 节「同名比赛会复用而不是新建」——复用是事实，但复用后立刻失败；
  2. 教程第 1 节「建比赛并导入框架（**一条命令**；同名比赛已存在会复用）」；
  3. `backend/examples/competitions/auto_chain_setup.py:205-206` 打印的「第二遍（覆盖模式）」命令 `manage.py build_competition ... --mode overwrite` **缺 `--allow-non-empty`**（`docs/汽车产业链测试赛准备.md:82` 的版本有），说明这是真实遗漏而非设计。
- 修复建议：走到"复用已有比赛"分支且目标非空时自动带上 `allow_non_empty=True`（或明确提示下一步并返回可读错误）；文档把「可反复执行」限定为"空比赛"或"带 `--allow-non-empty`"；修掉 `auto_chain_setup.py:205` 漏掉的 `--allow-non-empty`。

### [P1] Z-07 `--out` 无备份覆盖已有归档，且 `--sheets` 子集会把完整归档覆盖成残缺包

- 位置：`backend/examples/excel/build_from_sheets.py:149-153`
- 代码：

```python
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"已写出归档：{out_path}")
```

- 触发条件：
  1. 对同一路径跑两遍（教程 10.1 节把 `--out a.json` 列为常规命令）；
  2. `--sheets 产业类型,产业字段 --out 框架.json`（规范第 1 节 ⑥ 与教程第 5 节把 `--sheets` 与 `--out` 并列介绍）：本次归档**只含这两类资源**，却写到同一路径，整体替换上一次的完整归档。
- 后果：归档 JSON 是"可拿去前端「导入归档」上传"的交付物（教程 10.1），一旦被只含两张表的残缺包替换，导入得到的是一场只有行业口径、没有区域/地图/物资的比赛，而用户手里没有任何旧副本。当前输出只说"已写出归档"，不显示资源类数与条数，用户无法察觉内容缩水（`_print_summary` 明明已有这个能力，只走 `--inspect` 分支）。
- 修复建议：`--out` 目标已存在时报错或要求显式 `--force`，默认改名成 `<name>.<timestamp>.json`；写盘后打印本次资源类数与条数；`--sheets` 与 `--out` 同时出现时给出显式警告。

### [P1] Z-08 `--inspect` 会执行工作簿指定的 Python 脚本，与"纯只读、不写任何东西"的文档承诺不符

- 位置：`backend/examples/excel/sheet_spec.py:640-691`（`_h_contract_type`）与 `:772-802`（`_contract_types_from_script`）；触发路径 `backend/examples/excel/build_from_sheets.py:203-221`
- 代码（`sheet_spec.py:651-661`）：

```python
    script = (row.get("script") or "").strip()
    if not script:
        raise SheetFormatError(
            "合同类型表的「脚本路径」必填：合同类型必须由代码脚本产出"
            "（写法见 docs/CONTRACT_TYPE_BY_CODE.md 与 backend/examples/contracts/）"
        )
    wanted = (row.get("key") or "").strip()
    enabled = bool(as_bool(row.get("enabled"), default=True))

    types = _contract_types_from_script(ctx, script)
```

- 代码（`sheet_spec.py:782-789`，真正执行）：

```python
    spec = importlib.util.spec_from_file_location(f"_sheet_ct_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise SheetFormatError(f"无法加载合同类型脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - 脚本自身的错误直接暴露给使用者
        raise SheetFormatError(f"合同类型脚本 {path.name} 执行失败：{type(exc).__name__}: {exc}") from None
```

- 触发条件：工作簿「合同类型」表里写任意脚本路径（相对 backend 或表格目录），然后跑**任何**一条命令——包括 `--inspect`、`--out`；`build_from_tables()` 无条件处理「合同类型」表。
- 实测：构造一个在被 import 时写标记文件的脚本，跑 `build_from_tables()`（`--inspect` 的同一条路径）：

```
建包成功；副作用文件存在？ True → 只读的 --inspect/--out 路径也会执行脚本
```

  仓库自带的 `比赛建包模板.xlsx` 也被实测确认会执行 `examples/contracts/auto_chain_contracts.py`。
- 后果：
  1. 文档三处承诺被打破：规范第 1 节 ③「只读表格、看看会建出什么（**不连库、不写任何东西**）」、教程第 1 节 ①「看一眼最小示例框架会建出什么（**不连库、不写任何东西**）」、教程 5.1 节把 `--inspect` 定位为"改完表都跑一遍，10 秒"的安全检查；
  2. 工作簿是可转发文件（策划之间传表很常见），「合同类型」表里的一格路径就能让打开者的机器执行任意本地 Python 文件（`exec_module`，无沙箱、无白名单）。用户的主观意图是"检查这张表"；
  3. 同一路径写两行「合同类型」会**执行两次**，脚本里的顶层副作用（打印、写文件、连库）重复发生；脚本里 `sys.exit()` 抛的是 `SystemExit`，不被 `except Exception` 捕获，会让 `build_from_sheets.py` 静默退出且无提示。
- 修复建议：`--inspect` 默认不执行脚本（只打印"将引入脚本 X 的类型 Y"），新增 `--run-scripts` 或 `--out/--competition` 时才执行；把脚本解析结果按 `(path, mtime)` 在本次运行内缓存；显式捕获并拒绝 `SystemExit` 之外的 `BaseException`。

### [P1] Z-09 `--dry-run` 会先把比赛建进库：预演不是"一行都不落库"

- 位置：`backend/examples/excel/build_from_sheets.py:130-134`
- 代码：

```python
    competition_id = args.competition
    if args.create_competition and competition_id is None:
        competition_id = _create_competition(archive["sourceCompetition"]["name"],
                                             archive["resources"].get("competitionMeta", {}))
        args.competition = competition_id
```

- 触发条件：`build_from_sheets.py 我的比赛.xlsx --create-competition --dry-run`（教程第 5 节与规范第 1 节 ⑤ 的标准姿势），且比赛名此前不存在。
- 后果：`_create_competition` 走 `Competition.objects.create(...)`（`build_from_sheets.py:243`），发生在 `apply_import`（及其 `transaction.set_rollback`）**之前**、没有任何外层事务包裹 → 这一行会真实提交，库里多出一场空比赛。又因为比赛名全局唯一，第二次真导时会走"复用已有比赛"分支并落进 Z-06（占用检查拒绝）。文档两处承诺与此不符：规范第 1 节「`--dry-run`：预演导入（事务回滚，一行都不落库）」、教程第 5 节「建比赛 + 预演（事务回滚，一行都不落库）」。
- 修复建议：`--dry-run` 时只打印"将新建比赛「X」（状态 ACTIVE）"并跳过 `create`；或把 `_create_competition` 与 `apply_import` 放进同一个 `transaction.atomic()` 并统一回滚。

### [P1] Z-10 写出的 xlsx 含 XML 非法控制字符：工具自己的读取器与 Excel 都打不开

- 位置：`backend/examples/excel/xlsx_io.py:172-181`、`:209-210`
- 代码：

```python
def _xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        # XML 1.0 不允许的控制字符（Excel 会直接报文件损坏）
        .replace("\x00", "")
    )
```

- 触发条件：任何要写进表格的文本里带 `\x01`–`\x08`、`\x0b`、`\x0c`、`\x0e`–`\x1f` 之一。现实来源常见：从别的系统粘贴的文本、`make_template.py --from-sheets <目录>` 读取的 CSV（`csv` 模块不剔除控制字符）、日志/扫描结果里带 `\x0b` 的字段。
- 实测：

```
写入的 XML: <c r="B1" t="inlineStr"><is><t xml:space="preserve">行1\x0b行2\x01...   ← 裸控制字符
读回失败: ParseError: not well-formed (invalid token): line 1, column 275
```

- 后果：注释写着"XML 1.0 不允许的控制字符（Excel 会直接报文件损坏）"，但代码只删了 `\x00`。产出的 xlsx 有三个后果：本工具再读会 `xml.etree.ElementTree.ParseError`（属 Z-05 那一类未捕获异常）、Excel/WPS 打开报"文件已损坏"、`make_template.py --from-sheets A.xlsx --out B.xlsx` 这类"转一手"会把原本能读的文件变成打不开的文件。
- 修复建议：按 XML 1.0 合法字符集过滤（允许 `\t`/`\n`/`\r`，其余 `< 0x20` 丢弃或替换为 `\uFFFD`），并累计提示"某单元格含非法控制字符，已清理"。

### [P2] Z-11 工作表名不校验（长度/非法字符）+ 重名工作表静默丢数据

- 位置：`backend/examples/excel/xlsx_io.py:226-229`（写表名）、`:138-140`（重名覆盖）、`:264-271`（CSV 目录 → 表名）
- 代码（`xlsx_io.py:226-229` 与 `:138-140`）：

```python
    sheets_xml = "\n".join(
        f'<sheet name="{_xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, name in enumerate(names, start=1)
    )
...
                width = max(cells) + 1 if cells else 0
                rows.append([cells.get(i, "") for i in range(width)])
            tables[name] = rows          # ← 表名做 dict 键：同名后写覆盖先写
```

- 触发条件与实测：
  1. 工作簿里出现两个同名工作表（跨工具导出、宏改名、另存为都会发生）→ 读回 `{'区域': [['年份'], ['2026']]}`，**第一张表的行整段消失，无任何警告**；
  2. `write_xlsx`/`save_tables` 写 40 个字的表名或含 `a/b` 的表名 → 写出成功、工具自己也能读回，但 Excel 打开会拒绝或要求修复；`make_template.py --from-sheets <CSV 目录> --out x.xlsx` 用文件名当表名，文件名完全可能超 31 字符或含 `[`、`]`、`*`、`?`；
  3. 表名带尾空格的 CSV 目录（`"名称 .csv"`）会生成 `名称 ` 这样"看不出区别"的重复表名。
- 后果：`read_*` 的返回值是 dict，重名无法表达，一旦命中**前半张表被静默丢弃**（用户会以为都建进去了）；表名非法只影响"转一手"场景，但会直接产出 Excel 打不开的文件。
- 修复建议：`read_xlsx` 遇到重名工作表时报错（或改名加后缀并提示）；`write_xlsx`/`save_tables` 写前把表名裁到 31 字符、替换 `: \ / ? * [ ]`、去首尾空白与引号，冲突时改名并提示。

### [P2] Z-12 表格工具独有的空值/类型丢失：错误值 `#DIV/0!` 被当普通字符串、空表被整个吞掉

- 位置：`backend/examples/excel/xlsx_io.py:99-112`（`t="e"` 与数字格式被忽略）、`backend/examples/excel/build_from_sheets.py:194-221`
- 代码（`xlsx_io.py:103-112`）：

```python
    value_node = next((n for n in cell if _strip_ns(n.tag) == "v"), None)
    raw = "" if value_node is None or value_node.text is None else value_node.text
    if ctype == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if ctype == "b":
        return "TRUE" if raw.strip() in ("1", "true", "TRUE") else "FALSE"
    return raw
```

- 触发条件与实测（手工构造 xlsx 部件）：

```
['3',      '数字公式(有缓存值)']            # 缓存值可读，符合预期
['ab',     '文本公式']                     # t="str" 走兜底分支读缓存值，符合预期
['',       '无缓存值']                     # 无 <v> 时返回 ""
['#DIV/0!','错误值']                       # t="e" 当普通字符串返回
['45306',  '日期(序列号, s=1 是日期格式)']   # 数字格式 s= 被忽略，日期以序列号出现
['1234567','千分位格式数字']
['0.5',    '百分比格式 0.5 → 50%']
['富文本', '共享字符串(富文本)']            # <si><r><t> 拼接正确
```

- 后果：
  1. **错误值静默变文本**：某列公式临时算出 `#DIV/0!`（例如"单价"列引用了空单元格），错误码被当普通文本返回。落在文本列上会**原样写进比赛**——库里出现一个叫 `#DIV/0!` 的原料名（若它在首列则触发 Z-01 直接被丢行）。数值列会因 `float()` 失败报 `ValueError`（能拦住，但信息不友好）。
  2. **日期/格式类输入没有护栏**：`年份` 列若被 Excel 识别成日期，单元格值可能是序列号 `45306`；`单价`/`层级` 列若套了百分比或千分位格式，读到的分别是 `0.5` 和 `1234567`（用户屏幕上看到的是 `50%` 和 `1,234,567`）。文档对此没有任何提示（教程 2.2 节只讲"建议写数字文本"），属于会静默建错的输入。
  3. **空表被整表吞掉**：实测 `{"比赛": [["比赛名称"],["甲"]], "区域": [["区域名称"]], "燃料": [["燃料名称","每升单价"]]}` → `stats={'区域': 0, '燃料': 0}`，产出只有 `{'competitionMeta': 1}`，归档里**根本没有 `regions` / `fuels` 段**。用户特意放一张"稍后再填"的空表，等价于没放；对消费归档的前端，缺段与空段语义不同。
  4. **隐藏工作表/行不受控制**：`_sheet_files`（`xlsx_io.py:83-95`）只读 `name`/`r:id`，不读 `state="hidden"`；`read_xlsx` 也不看 `row/@hidden`、`col/@hidden`。藏在隐藏表里的旧数据、被隐藏的草稿行都会照常被建进比赛。
- 修复建议：`_cell_text` 对 `t="e"` 返回可识别标记并让 handler 拒绝写入；对 `t="d"` 与数字格式给出提示；空表在归档里产出"存在但 0 行"的段并在 `--inspect` 里单独提示；`--inspect` 里列出"本表在工作簿中被隐藏，但仍被读取"。

### [P2] Z-13 校验遇到第一个错误就中断，无法一次列出全部错误

- 位置：`backend/examples/excel/build_from_sheets.py:210-221`
- 代码：

```python
        rows = _table_rows(spec, tables[spec.name])
        for row_no, row in rows:
            ctx.row_no = row_no
            try:
                spec.handler(ctx, row)
            except SheetFormatError as exc:
                raise SheetFormatError(f"[{spec.name}] 第 {row_no} 行：{exc}") from None
            except BuilderError as exc:
                raise SheetBuildError(
                    f"[{spec.name}] 第 {row_no} 行：{exc}{_reference_hint(str(exc), tables, selected)}"
                ) from None
```

- 触发条件：表格里有多处问题（多张表、同一张表多行）。实测：`区域` 表第 2 行缺区域名 + `燃料` 表第 2 行缺燃料名，命令只报第一条 `[区域] 第 2 行的必填列「区域名称」为空`，修好再跑才报第二条。
- 后果：策划修表要"改一处、跑一次"，几十行的表可能来回十几轮；教程第 5.1 节把 `--inspect` 定位为"10 秒一次的安全检查"，但没有说明是"首错即停"。行号本身是准的（1-based、表头行 +1、空行/注释行不计入编号，实测 `区域` 表第 4 行报错对应 Excel 第 4 行），这点没问题。
- 修复建议：收集所有错误后一次性输出（`--inspect` 模式尤其重要），或在首错后加一句"已停止，修好后请重跑"；`_table_rows` 的必填校验同样改为收集本表全部行的问题。

### [P2] Z-14 合同层的输入校验缺口：`trips` 可为负、超重加价永不触发（文档「已验证」不可复现）

- 位置：`backend/examples/contracts/auto_chain_contracts.py:226-245`
- 代码：

```python
    cargo_weight = ct.input("cargo_weight", "货物总重（吨）", "number", default="0")
    rate_per_km = ct.input("rate_per_km", "单公里运价（元/公里）", "number", required=True, default="12")
    trips = ct.input("trips", "车次", "number", default="1")
    carbon_tax_rate = ct.input("carbon_tax_rate", "碳税税率", "number", default="0.05")
...
    ct.check(rate_per_km >= 0, label="运价校验", error="单公里运价不能为负")
    ct.check(cargo_weight >= 0, label="货重校验", error="货物总重不能为负")
```

- 触发条件：玩家在「合同管理」新建运输合同时把「车次」填成 `-100`（该输入项没有任何下界检查；在 `apps/contracts/builder/validate.py` 里搜索「负数/非负/不能为负」无任何规则）。此时 `freight = distance * rate_per_km * trips + fuel_cost` 为负 → `ct.sub_number(client.cash, 负值)` 给委托方加钱、`ct.add_number(carrier.cash, 负值)` 给承运方扣钱；`ct.check(client.field(cash) >= freight + carbon_tax)` 也拦不住（负数恒小于现金）。
- 触发条件（第二处）：预置实例 `cargo_weight: 20`（`auto_chain_competition.py:290`），冒烟补的载具是 `{"重型卡车": 2}`（`auto_chain_smoke.py:117`，单车 `max_cargo=30`）→ 合计 `maxCargo=60`，「超重 +10%」分支 `cargo_weight > capacity` **永远不触发**。文档 `docs/汽车产业链测试赛准备.md:55` 把"超重 +10%"列为该合同主要效果，`:126` 的冒烟参考金额 `540×12 + 0.35×2×540 = 6,858` 也正是"未超重"的结果——即文档展示的效果在当前预置数据下无法观察到。
- 后果：这是合同模板自身的输入校验缺口，但它是本分支交付物的一部分，直接决定经济模型能否被玩家套利；第二处让文档的"已验证事实"（准备文档第 4 节 ②）变成不可复现的描述。
- 修复建议：给 `trips`、`carbon_tax_rate` 及其余数值输入项补 `>= 0` 的 `ct.check`（或给 `number` 型输入项加统一下界声明）；把冒烟/文档的载具数量或载货量调到能触发超重（如 `{"重型卡车": 1}` 或 `cargo_weight=70`），或在文档里明确"本场不触发超重"。

### [P3] Z-15 部分「数字文本」不满足数值正则，静默变字符串；`1_000` 这类意外写法被接受

- 位置：`backend/examples/excel/sheet_spec.py:136-141`
- 代码：

```python
    try:
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
        return float(s)
    except ValueError:
        return s
```

- 触发条件与实测：单元格填 `1,000`、`1,000.50`、`50%`、`100元`、`(100)`、`--`、`1_000`：

```
'1,000'   -> '1,000'  (str)      '1,000.50' -> '1,000.50' (str)
'50%'     -> '50%'    (str)      '100元'    -> '100元'    (str)
'1_000'   -> 1000.0   (float)    '1e3'      -> 1000.0     (float)
```

- 后果：前几种落在数值列上会被 `float()` 兜底失败挡住（但是英文异常，属 Z-05 同类）；落在文本列（`产地`、`备注`、`载具名称`）就会**原样入库**，`1,000` 这种"看起来是数字"的值会成为匹配不到任何节点的引用，且错误信息只会说"引用的 X 未登记"，用户看不出是格式问题。`1_000` 被接受则是纯意外行为（没人会这么写，但 PowerPoint/网页复制粘贴会带来下划线）。
- 修复建议：改用严格正则 `re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", s)` 并显式拒绝下划线/全角；对无法解析的字符串在数值列给出中文提示，例如"该列需要数字，收到「50%」（若想表示百分比请写 0.5）"。

### [P3] Z-16 CSV 目录写出时表名未做文件名安全化，写到一半会抛异常

- 位置：`backend/examples/excel/xlsx_io.py:274-281`
- 代码：

```python
def write_csv_dir(path: str | Path, tables: dict[str, list[list]]) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (path / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerows(rows or [])
    return path
```

- 触发条件：表名含 Windows 文件名非法字符（`/`、`:`、`*`、`?`、`"`、`<`、`>`、`|`）或超长（>255 字符）——表名来自 xlsx 的 sheet 名（见 Z-11）或用户手工命名的 CSV。
- 后果：`save_tables` / `make_template.py --csv-out` / `make_sample_auto_chain.py --csv-out` 只写到一半就 `OSError`，前几张表已落盘、后几张没有，异常不被捕获（与 Z-05 同类），用户看到的是 `OSError: [Errno 22]` 之类的堆栈。
- 修复建议：写 CSV 前对表名做文件名安全化并把改名结果打印出来；对 `OSError` 统一包装成可读错误。

### [P3] Z-17 文档与代码的其余事实性不一致

- 位置与实测：
  1. **表数量文档内部矛盾**：`docs/比赛Excel建包规范.md:26`「本规范覆盖，21 张表」（实测 `len(SHEETS) == 21` ✔）、`:103`「最小示例.xlsx（最小框架，**17 张表**）」、`:619`「最小示例.xlsx（**16 张表**）」、`docs/比赛Excel建包教程.md:505`「最小示例.xlsx 最小框架（**16 张表**）」；实测最小示例 17 张 = 16 张业务表 + 1 张「说明」表，`比赛Excel建包规范.md:618`「汽车产业链示例.xlsx（20 张表）」实测 21 张 = 20 + 说明。同一文件在不同段落给出 16/17 两个数。
  2. **`--sheets` 的统计口径**：`build_from_sheets.py:221` 的 `stats[spec.name] = len(rows)` 在过滤后与 `:224` 的"共处理 N 张表"一起输出，实测 `only_sheets="产业类型"` 时打印「共处理 0 张表、0 行数据」，而 `产业类型` 表实际被处理、归档里有 `industryTypes`；`ctx.notes` 也一并丢失。用户按教程 5.1 节"逐张核对你这次想建的表都在，且行数对得上"核对时会被误导。
  3. **「比赛」表写多行只取第一行**：规范第 5 节写「整表一行」，实测多写一行（`第一场/第二场`）静默只取第一行、无提示；`stats` 里也不含「比赛」表。属于"文档说了、代码也这么做、但缺少提示"的弱提示缺口。
  4. **文档的"验证结论"与当前数据不符**：`汽车产业链测试赛准备.md:95`「全部命令退出码均为 `0` … 重复执行是安全的（幂等）」与同页 `:171-173` 第 6.4 条「重复导入的两个既有行为：① 比赛内消息不判重，第二次导入会多出一整套」自相矛盾；`:82` 的第二遍导入命令带 `--allow-non-empty` 而 `auto_chain_setup.py:205` 打印的同一命令漏了（Z-06 已述）。
- 修复建议：文档统一按「业务表数 + 说明表」口径重写表数量；`stats` 只统计本次实际处理的表并单独说明"因 `--sheets` 跳过的表"；「比赛」表多行时给出提示；把准备文档第 3 节的"幂等"限定到具体命令。

---

## 存疑/待确认

1. **[待确认] `.xlsm` 后缀被当作 xlsx 写出是否会生成无效文件**
   `xlsx_io.py:294`（读）与 `:302`（写）都把 `.xlsm` 当 xlsx 处理，但 `_CONTENT_TYPES`（`:148-155`）只有 `sheet.main+xml` 内容类型，没有 `macroEnabled.main+xml`。用 `save_tables("x.xlsm", ...)` 会写出"扩展名 xlsm、内容类型 xlsx"的文件，Excel 可能报内容类型不匹配（读 `.xlsm` 本身没问题，它是同一个 zip+XML 家族）。仓库示例里没有 `.xlsm` 用法，故标为待确认。

2. **[待确认] `read_xlsx` 的兜底工作表路径 `xl/worksheets/sheet{n}.xml` 在真实 Excel 产物上的命中率**
   `xlsx_io.py:92-94`：当 `workbook.xml.rels` 的 Target 与 zip 内路径对不上时按序号猜。我只验证了仓库自带文件（`rId` 正常），没有构造 Target 带 `../`、sheets 与 chartsheets 混排的工作簿，未确认兜底是否会在真实 Excel 产物上误命中（误命中会把 A 表的数据读成 B 表的）。

3. **[待确认] `--sheets` 过滤后 `ctx.notes` 丢失是否会影响"合同类型脚本来自哪个脚本"这类必需提示**
   `build_from_sheets.py:206-208` 的 `continue` 使未选中的表连"行数 0"都不计入 `stats`，`ctx.notes` 因为 `notes.extend(ctx.notes)`（`:223`）只在函数末尾执行而永远拿不到被跳过表的提示。若 `--sheets` 选中了某些表但跳过了引用表，用户拿不到任何关于跳过的说明；这一条影响面取决于 `--sheets` 的实际使用频率，标为待确认。

4. **[待确认] `_sheet_files` 对"工作表没有 r:id 或 rels 缺失"时用序号兜底是否会读错表**
   与第 2 条同源：`xlsx_io.py:86-95` 在 `rid` 缺失时 `target=""`，然后按 `sheet{len(out)+1}.xml` 猜。若工作簿里删过工作表导致序号与真实文件名错位，可能把整张表读成另一张表的内容（表名与实际数据不符）。我没有构造该场景验证，标为待确认。

5. **[待确认] 行数/文件体积没有上限，超大表格会整表载入内存**
   `read_xlsx` 把全部行读进 `list[list[str]]`（`xlsx_io.py:125-140`），`build_from_tables` 再把整表转成 dict 列表，`--out` 时 `json.dumps` 整个归档一次性进内存。规范/教程都没有给出行数上限（`docs/BUILD_COMPETITION_API_REFERENCE.md:1394` 的"2000 行/资源"只是**导出**明细上限，`archive.py:39` 的注释明确"导入不受此限制"）。我没有测量实际的阈值，且这属于"资源占用"类问题，标为待确认是否值得单列缺陷。

---

### 附：本次为验证而生成的只读脚本与临时文件（均可删除，未触碰仓库业务代码与数据库）

- `code_audit/_b06_probe1.py` ~ `_b06_probe11.py`：读 xlsx → 内存建包 → 打印，以及手工构造 xlsx 部件验证单元格边界。**没有调用 `apply_import`**。
- `code_audit/_b06_out/`：探针构造的临时 xlsx / CSV / 标记文件。
- 查库仅使用只读连接：`sqlite3.connect("file:db.sqlite3?mode=ro", uri=True)`。
- 另外以只读方式跑过三次官方命令：`build_from_sheets.py examples/excel/{汽车产业链示例,最小示例,比赛建包模板}.xlsx --inspect`（退出码均为 0，未写库）。
