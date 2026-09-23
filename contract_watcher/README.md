# 合同通过监听程序（独立 · 静默 · 自动）

不改动网页端与后端任何代码：本程序是**独立运行的本地监听器**，对服务器只表现为
一个只读账号（登录 + 查询接口），文件全部落在启用者自己的电脑上。

> **2026-09 改版（重要）**：记账改成 **「先入 SQLite → 触发式批量写 xlsx」**，
> 并新增 **Tkinter 图形界面**；同时加上 **角色门禁**（只允许 COMPETITION_ADMIN /
> SUPER_ADMIN，PLAYER 默认关闭）与 **公司管理范围（companyScopes）** 校验。
> 详见下方「[新工作流](#新工作流2026-09登录门禁--sqlite-分账--触发式批量记账)」。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `contract_watcher.py` | 主程序：轮询监听、类型目录同步、按类型分发、角色门禁、SQLite 入库、触发式批量记账 |
| `store.py` | **SQLite 存储层**：按 companyId 分账的合同库、公司目录与记账目标、批次、手动请求、财年缓存、游标 |
| `bookkeeping.py` | **批量记账**：某公司积压合同 → 一次 Excel 会话调用 `shang.py` 的 add_* |
| `gui.py` | **Tkinter 窗口**：登录、选择公司、手动记账、按公司查看 SQLite 里的合同 |
| `start_gui.bat` | **图形界面启动脚本**（Windows）：自动挑选带 tkinter/xlwings 的 Python、缺 xlwings 时告警、失败时暂停显示报错 |
| `watcher_config.py` | 本地配置（默认 `config.json`）：服务器/账号、阈值、记账目标、账本路径等 |
| `handlers.py` | **处理函数文件**（自动维护：新类型自动追加默认函数、类型改名自动改名） |
| `readable.py` | **翻译模块**：合同 payload → 可理解的中文记录；支持按类型注册自定义翻译器 |
| `DATA_GUIDE.md` | **合同数据指南**：处理函数里可获得哪些数据、如何取值 |
| `TEST_FLOW.md` | **端到端测试流程**：文字版分步操作与验收标准 |
| `bookkeeping_example/shang.py` | **Excel 记账联动示例**：用户记账处理函数（xledit 类，三个 add 模板；默认静默运行、含平衡校验 `check()`） |
| `bookkeeping_example/target.xlsx` | 上述脚本配套的 12 表记账账套模板（每家公司账本由它复制而来） |
| `data/watcher.db` | **按公司分账的 SQLite 台账**（合同/批次/手动请求/财年/游标） |
| `books/company_<id>.xlsx` | **每家公司一本账本**（首次记账时按 `target.xlsx` 复制生成） |
| `records/` | 通过合同的可读存档（`contract_<id>_<时间>.json` + `_readable.json`） |
| `watcher.log` | 运行日志（平时静默，全部记录在此） |
| `data/state.json` | 进度与类型目录状态（重启不重复处理） |

## 新工作流（2026-09）：登录门禁 · SQLite 分账 · 触发式批量记账

### 1. 谁能用：只允许 COMPETITION_ADMIN / SUPER_ADMIN

启动后先登录，再按登录响应里的 `user.role` 做门禁：

| 角色 | 默认 | 说明 |
| --- | --- | --- |
| `SUPER_ADMIN` | ✅ 允许 | 公司管理范围视为全部公司 |
| `COMPETITION_ADMIN` | ✅ 允许 | 只记录自己 `companyScopes`（公司管理范围）内的公司 |
| `PLAYER` | ❌ **默认关闭** | 需要时显式放开：`--allow-player` 或配置 `allow_player: true` |
| 其它角色 | ❌ 拒绝 | 退出码 3 |

被拒绝时不会写入任何数据、也不会启动监听（退出码 3，stderr 有中文提示）。

### 2. 记谁：公司管理范围 + 记账目标选择

- **只能记录有权限管理的公司**：按账号的 `companyScopes`（公司管理范围）过滤；
  范围为空 ⇒ 没有任何可记录公司（与后端「执行合同」的校验同口径）；
  超管不受限。
- **在此基础上选择记账目标**（勾选的公司才会写 xlsx）：
  - GUI：公司列表第 1 列点击即勾选/取消（`☑`/`☐`），或「全选 / 全不选」；
  - 命令行：`--companies 172,173`（`all` = 全部可管理公司）；
  - 配置：`record_company_ids`（`null` = 沿用上次选择，`[]` = 一个都不记）。
  - 首次同步默认勾选全部「可管理」公司；非管理范围的公司即使被勾选也不会参与记账。

### 3. 数据流：先 SQLite，再（必要时）Excel

```
合同通过 ──► SQLite（data/watcher.db，按 company_id 分行；零 COM）
                 │  未记账合同积压
                 ├─ ① 达阈值（默认 10 条，可改）
                 ├─ ② 财年结束 / 开始（后端财年更迭信号）
                 └─ ③ 手动请求（GUI「立即记账」）
                          │
                          ▼
             一次 Excel 会话写入该公司的 books/company_<id>.xlsx
             （逐份合同调用 handlers.py 的处理函数 → add_book_entries /
               add_book_item / add_book_assets → check() 平衡校验 → save()）
```

关键点：**其余时候一次都不会调用 `shang.py`**（`book_factory` 根本不会被调用），
所以 COM 会话次数 = 记账次数，而不是合同数。

- 合同入库幂等：主键 `(company_id, contract_id)`，重复处理不产生第二行、不覆盖记账标记；
- 金额等大数按字符串存（不走 float）；
- 记账失败（Excel 打不开 / 科目找不到 / 处理函数报错）时：账本**不保存**（放弃会话）、
  合同保持未记账、批次记为 `failed`，并进入 **60 秒退避**（避免每轮都开 Excel）；
- 进程在「Excel 已保存、SQLite 尚未更新」之间被杀时，残留 `running` 批次**不自动重放**
  （宁可少记也不重复记账），标记为「需人工核对」。

### 4. 记账规则写在哪：`handlers.py`（默认不写分录）

处理函数会被调用**两个阶段**，用 `ctx["phase"]` 区分：

| 阶段 | 何时 | 能做什么 |
| --- | --- | --- |
| `collect` | 合同通过、写入 SQLite 时 | 只做与 Excel 无关的事（默认：存档 JSON）；`ctx["book"] is None` |
| `book` | 批量记账时 | 用 `ctx["add_entries"] / add_item / add_assets` 记账；`ctx["book"]` 是本批次共用的 xledit 实例 |

```python
def handle_material_procurement_passed(contract: dict, ctx: dict) -> None:
    if ctx.get("phase") != "book":
        return                                   # collect 阶段什么都不做
    from decimal import Decimal
    amount = Decimal(str((contract.get("inputs") or {}).get("amount") or 0))
    number = (contract.get("parties") or [{}])[0].get("contractNumber") or f"#{contract['id']}"
    ctx["add_entries"](add=amount, minus=Decimal(0), number=number, about=contract["name"])
```

- **自动生成的默认函数**（带 `# [auto-default]` 注释、函数体是存档）不算记账规则，
  不会触发 Excel；按文档删掉那行注释、写自己的实现即可。
- 没有可用记账规则的合同会被标记为「已记账（无分录）」，避免反复触发；
  需要补记时用 GUI/脚本撤销标记后重新触发（`store.unbook_contracts`）。
- `shang.py` 会在模块级把 Decimal 精度改成 2（脚本自身行为）；
  `bookkeeping.import_shang()` 在导入前后恢复进程精度，不会污染本程序其它金额运算。

### 5. 图形界面（Tkinter）

```powershell
# Windows：双击 / 命令行启动脚本（推荐，自动挑选带 tkinter+xlwings 的 Python）
start_gui.bat
start_gui.bat -w            # 无控制台窗口（pythonw）
start_gui.bat --config my.json

# 直接调用
python contract_watcher.py --gui        # 或
python gui.py
```

`start_gui.bat` 做的事：切到本目录 → 按 `py -3` → `python` 顺序挑一个**带 tkinter**
的解释器（优先同时有 xlwings 的，缺 xlwings 只告警不拦截）→ 运行 `gui.py`，
失败时 `pause` 保留报错。文件本身是**纯 ASCII + CRLF**（cmd 按 OEM 代码页读字节，
中文注释会乱码；与本仓库 `scripts/*.bat` 同一约定）。

界面分四块：**① 登录**（服务器/账号/密码/比赛 id）；**② 公司**（只列有管理权限的公司，
勾选=记账目标，显示待记账/已记账/最近记账）；**③ 合同**（选中公司在 SQLite 里的合同，
可只看待记账）；**④ 操作**（阈值、自动记账开关、财年结账开关、「立即记账」、
「开始/停止监听」、打开账本目录、日志窗口）。

- 「立即记账」= 手动请求：监听在跑时排队给监听线程处理（保证 Excel 单线程），
  没在跑时用后台线程直接执行一次；
- **单实例**：启动监听前会取 `data/watcher.lock`（与命令行 `--lock-file` 同一把锁），
  命令行监听程序或另一个界面在跑时会被明确拒绝——避免两个进程同时打开 Excel 写同一本账；
  停止监听/关闭窗口会释放；长批次（几分钟的 Excel 会话）期间由记账回调持续刷新锁心跳；
- 监听线程会显示「已完成 N 轮」；若超过 120 秒无进展，状态栏提示
  「可能卡在 Excel/COM」（Excel 卡死无法从 Python 安全强杀，请结束后重启监听）；
- 公司勾选与阈值会写回 `config.json`（密码只有勾选「记住密码」才落盘）。

### 6. 财年更迭信号（主项目侧）

- 后端 `apps/competitions/signals.py`：财年写入会发出 `fiscal_year_changed`
  （另有 `fiscal_year_started` / `fiscal_year_ended`），迁移语义为
  `ACTIVE→CLOSED` = `FY_END`、`非ACTIVE→ACTIVE` = `FY_START`；
- 后端 `GET /api/competitions/:id/fiscal-years?updatedAfter=<ISO>` 支持增量轮询
  （返回 `items` / `existingIds` / `deletedIds` / `serverTime`）——**这是给外部进程的
  可轮询信号**，本程序每 `--fiscal-year-interval`（默认 60s）拉一次，
  与本地缓存比对推导 `FY_END` / `FY_START`，再对已选择的公司结账。

### 7. 新增命令行参数（均有默认值，老命令照旧可用）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--gui` | 关 | 启动图形界面（此时 `--server/--username/--password` 可省，界面里填） |
| `--config` | `config.json` | 本地配置路径 |
| `--db` | `data/watcher.db` | SQLite 台账路径 |
| `--threshold` | `10` | 同一公司未记账合同达到该条数即写 xlsx |
| `--companies` | 全部可管理 | 记账目标公司 id（逗号分隔；`all` = 全部） |
| `--books-dir` | `books/` | 公司账本目录 |
| `--book-template` | `bookkeeping_example/target.xlsx` | 账本模板 |
| `--allow-player` | 关 | 允许 PLAYER 角色使用 |
| `--no-auto-book` | 关 | 关闭阈值/财年自动记账（只保留手动请求） |
| `--no-fiscal-year-flush` | 关 | 关闭财年结束/开始自动结账 |
| `--fiscal-year-interval` | `60` | 财年轮询最小间隔（秒） |

## 快速开始

```powershell
# 前提：账号具备 contract:view（自动生成/改名另需 contractType:view）
python contract_watcher.py --server http://127.0.0.1:8000 `
    --username admin --password "你的密码" [--competition 1]

# 前台观察一次运行效果（明细打到控制台）：
python contract_watcher.py --server ... --username ... --password ... --verbose

# 存量合同也要补处理（仅首次）：
python contract_watcher.py --server ... --username ... --password ... --backfill

# 图形界面（登录 / 选公司 / 手动记账 / 查看合同）：
python contract_watcher.py --gui
# Windows 上更省事：双击或执行启动脚本（自动挑 Python、缺 xlwings 会告警）
start_gui.bat
```

- 默认每 3 秒检查一次（`--interval 1` 可更实时）；
- 首次运行建立进度基线：之后**新通过**的合同才会被处理；
- 单实例：重复启动会被端口锁拒绝（`--port` 可换）；**图形界面启动监听**与命令行
  监听程序共用 `data/watcher.lock`，两者不会同时打开 Excel 写同一本账；
- 记账不再随合同执行：合同先进 `data/watcher.db`，达阈值（默认 10 条）/财年结束/
  手动请求时才写 `books/company_<id>.xlsx`（详见上面的「新工作流」）。

## 自动化行为（无需人工干预）

1. **合同通过 → 按类型执行**：轮询发现 `EXECUTED` 且 `executedAt` 更新的合同 →
   拉详情 → **先按参与公司写入 SQLite**（`companyScopes` 范围外的公司跳过）→
   按 `contractType.key` 找 `handle_<key>_passed(contract, ctx)` 执行（`phase="collect"`）；
   找不到则执行默认存档（全量 JSON + 可读翻译版 → `records/<key>/`）。
2. **批量记账**：同一公司未记账合同达阈值、财年更迭、或收到手动请求时，
   对该公司的全部积压合同**只开一次 Excel**，在 `phase="book"` 下调用处理函数记账。
3. **新类型 → 实时生成默认函数**：轮询合同类型目录，出现新 key 即在
   `handlers.py` 末尾自动追加默认函数并热加载（默认函数只存档、不记账、不触发 Excel）。
4. **类型改名 → 自动改名**：同一 typeId 的 key 变化时，自动更新该函数的
   「标注行 + 函数名」为新 key（函数体原样保留），并热加载生效。
5. **修改即生效**：你编辑 `handlers.py` 保存后，下一轮自动热加载，无需重启。
6. **健壮性**：登录态自动续期；断网/接口错误/处理异常只写日志并继续下一轮；
   进度落盘，重启不重复；记账失败有 60 秒退避，不会每轮都开 Excel。

## 定制某个类型的处理

打开 `handlers.py`，找到对应自动生成块（标注 `ContractType.key = <你的key>`），
删除函数体内 `# [auto-default]` 一行并写自己的逻辑，例如按需拆分文件、发送到
本机其它目录等：

```python
def handle_material_procurement_passed(contract: dict, ctx: dict) -> None:
    # 例子：只保存参与方名单（去掉默认存档逻辑）
    import json, time
    from pathlib import Path
    sub = Path(ctx["out_dir"]) / "parties"
    sub.mkdir(parents=True, exist_ok=True)
    f = sub / f"parties_{contract['id']}.json"
    f.write_text(json.dumps(contract.get("parties", []), ensure_ascii=False, indent=2), encoding="utf-8")
```

## 翻译模块（readable.py · 默认自动产出可读版）

默认存档时，每个合同自动产出两个文件：
`contract_<id>_<时间>.json`（原始全量）与 `contract_<id>_<时间>_readable.json`
（翻译后的中文可读记录：参与方/填写内容/前置检查/落账明细，公司名、字段中文名、
操作中文（增加/扣减/设定）、金额千分位均已转换）。翻译失败不影响原始存档。

**用户接口**（处理函数里直接用）：

```python
from readable import translate_contract, build_readable, register_translator, pretty_value

# 1) 翻译任意合同
rec = translate_contract(contract)                 # dict，可直接 json.dumps 落盘

# 2) 为某合同类型注册专属翻译器（在默认结构上增删字段）
@register_translator("material-procurement")
def translate_procure(payload: dict) -> dict:
    rec = build_readable(payload)                  # 先取默认翻译
    rec["采购注意"] = "需要人工复核单价上限"            # 按类型补充内容
    return rec

# 3) 全局标签表可直接覆盖（影响所有类型）
import readable
readable.OP_LABELS["SET"] = "设定为"               # 修改操作词
```

在你的处理函数里：不调用 `ctx["default_archive"]` 的自定义处理，
可自行用 `translate_contract(contract)` 生成可读记录后按需落盘。

## Excel 记账联动（bookkeeping_example/ · 内容理解）

用户提供的记账处理函数 `shang.py` + 账套 `target.xlsx`，用于把合同通过后的业务
变动**记入 Excel 账本**。已复制到 `bookkeeping_example/` 供参考与测试
（当前为 **2026-09-10 新版**，13,392 字节；旧版 12,612 字节）。

### 依赖与性质

- `shang.py` 使用 **xlwings** 驱动本机 **Excel 应用**（非标准库、非网页端）：需
  Windows + 已安装 Excel，首次使用 `pip install xlwings`；会真实打开并保存
  `target.xlsx`，请勿同时被多人/多实例打开（测试建议用副本）。
- **默认静默运行**：`xledit(file, debug=False)` 不显示 Excel 窗口
  （`visible=False` + `ScreenUpdating=False`），适合监听程序后台调用；
  需要肉眼观察时传 `debug=True` 才显示 Excel 界面。
- 内置 **会计平衡校验**：`xledit.check()` 读取资产负债表 `sheets[7]` 的 `H80`
  单元格（资产 − 负债 − 所有者权益）：为 0 打印 `right`；>0 打印
  「资产>负债+所有者权益」；<0 打印「资产<负债+所有者权益」
  （已修正原先两种失衡使用同一文案的问题）。
- `target.xlsx` 是一个 **12 张表的记账账套**：银行流水账、原材料(加工用)、
  零件（加工用）、商品（各实体用）、明细账汇总-资产类科目、明细账汇总-负债类
  科目、明细账汇总-损益类科目、资产负债表、利润表、计算、现金流量表、
  常用财务分析表。

### `xledit` 类与三个 add 模板函数（语义对照）

| 函数 | 写哪张表 | 作用 | 典型对应合同事件 |
| --- | --- | --- | --- |
| `add_book_entries(add, minus, number, about)` | `sheets[0]` 银行流水账 | 追加一行银行日记账：日期自动、序号自增、`add` 写入「存款增加」、`minus` 写入「存款减少」、自动余额公式，摘要 = `number + " " + about`；余额单元格地址存 `entries_to_assets_money` 供资产表联动 | 合同款项收付（货款/定金/还款） |
| `add_book_item(thing: things, name, number, price, add, minus)` | `sheets[1-3]` 原材料/零件/商品 | 按存货名定位货品行（新版按 `things.RAWMETRIAL/COMPENT/PORDUCT` 枚举分支）：`add=True` → 采购入库（数量+单价），`minus=True` → 耗用出库（数量，加权单价由公式算）；新物料自动建移动平均结转报告表头，插入行用 `autofill` 复制格式与公式；净额自动同步到资产/损益汇总科目 | 原料/零件/商品类合同的收发存 |
| `add_book_assets(type: BOOOKTYPE, name, add, minus)` | `sheets[4]` 资产 / `sheets[5]` 负债 / `sheets[6]` 损益科目 | 按科目中文名定位行写入发生额、自动向下扩行并维护「银行存款」余额联动公式；**新版负债类与损益类分支均自动借贷互换**（按账套方向约定） | 合同对资产负债/损益类科目的影响 |

配套枚举：`things`（原材料/零件/产品）、`ASSET/LIABILITIES/EQUITY`（科目中文名
常量，如 `银行存款/应收账款/应付账款/主营业务成本-存货成本/…`）、`BOOOKTYPE`。

### 本版（2026-09-10）相对旧版的实际改动

| # | 改动 | 影响 |
| --- | --- | --- |
| 1 | `__init__` 增加 `debug=False`，默认**无界面**运行 | 可在监听程序后台静默记账 |
| 2 | 新增 `check()` 资产负债表平衡校验 | 记账后可自检是否平账 |
| 3 | `add_book_item` 参数 `unitprice` → `price`；分支判断改 `things` 枚举 | 调用方关键字传参需同步改名（位置传参不受影响） |
| 4 | 货品行定位 `i+3` → `i+4`、写数据行偏移 +1、插入行改 `autofill`、移除重复条件 | 修复旧版可能定位错行/条件不生效的问题 |
| 5 | `add_book_assets` 损益类分支新增借/贷互换 | 损益科目方向与资产相反，由脚本自动处理 |

### 与监听程序的结合方式（在你的 handlers.py 里）

**新工作流（推荐）**：不要在处理函数里自己开关 Excel。批量记账时监听程序已经把
本批次共用的 Excel 会话准备好了，用 `ctx` 提供的三个方法记账即可（见上文
「记账规则写在哪」与 `handlers.py` 头部注释）：

```python
def handle_material_procurement_passed(contract: dict, ctx: dict) -> None:
    if ctx.get("phase") != "book":      # collect 阶段：只入库/存档，不碰 Excel
        return
    from decimal import Decimal
    amount = Decimal(str((contract.get("inputs") or {}).get("amount") or 0))
    number = (contract.get("parties") or [{}])[0].get("contractNumber") or f"#{contract['id']}"
    ctx["add_entries"](add=amount, minus=Decimal(0), number=number, about=contract["name"])
    # ctx["add_item"](things.RAWMETRIAL, "原料A", 100, Decimal("50"), True, False)
    # ctx["add_assets"](BOOOKTYPE.ASSETS, ASSET.BANK_DEPOSITS, add=amount, minus=Decimal(0))
```

需要 `things / ASSET / BOOOKTYPE` 等枚举时：

```python
import bookkeeping
shang = bookkeeping.import_shang()      # 自动定位 bookkeeping_example/shang.py
things, ASSET, BOOOKTYPE = shang.things, shang.ASSET, shang.BOOOKTYPE
```

> 参数取值按你的账套口径校验后再启用（金额单位、科目映射、借贷方向）。
> Excel 自动化建议只在单机、非并发场景使用；监听程序已做异常隔离与单线程串行，
> 记账失败只写日志与批次记录，不影响合同执行与网页端。

**旧写法（每份合同自己开 Excel）**：仍然可用，但每份合同一次 COM 会话，
容易遇到 `RPC 服务器不可用`；建议按上面的新写法迁移。

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "bookkeeping_example"))
from shang import xledit

def handle_material_procurement_passed(contract: dict, ctx: dict) -> None:
    if ctx.get("phase") == "book":
        return                           # 交给批量记账；这里只处理通过瞬间的其它逻辑
    book = xledit(r"D:\账套\target.xlsx")
    try:
        book.add_book_entries(add=contract["inputs"].get("amount"), minus=0,
                              number=contract["parties"][0].get("contractNumber"),
                              about=contract["name"])
        book.save()
    finally:
        try:
            book.xlapp.quit()
        except Exception:
            pass
```

## 开机自启（可选）

- Windows：任务计划程序 → 创建任务 → 触发器「登录时」→ 操作填上面的 python 命令；
- Linux：`crontab -e` 加 `@reboot ...`，或用 systemd user service；
- 无需界面常驻：程序没有窗口输出（去 `--verbose` 即全静默，仅写 `watcher.log`）。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 启动报「登录失败」 | 账号密码错，或 admin 首次登录需先改密 |
| 启动报「角色 … 不允许使用」 | 只有 COMPETITION_ADMIN / SUPER_ADMIN 可用；PLAYER 需 `--allow-player` |
| 合同处理了但没进账本 | 正常：记账只在①达阈值（默认 10 条）②财年结束/开始 ③手动请求时发生；也可在 GUI 点「立即记账」 |
| 公司列表是空的 / 什么都没记 | 账号的 `companyScopes`（公司管理范围）为空 ⇒ 没有任何可记录公司；请让超管配置范围并授予 `company:view` |
| 公司没进账本 | 该公司未被勾选为「记账目标」（GUI 第 1 列 `☐`，或 `--companies`/`record_company_ids` 未包含它） |
| 账本没写进去、批次 failed | 看批次 `message` 与 `watcher.log`：常见是 Excel 被占用、科目名在账套里找不到、处理函数报错；修好后 60 秒退避结束或点「立即记账」强制重试 |
| 批次状态 `unknown`（需人工核对） | 上次记账时进程被中断，无法判定账本是否已写入；**不自动重放**（防重复入账），请人工核对账本后决定是否 `store.unbook_contracts()` 重记 |
| 日志出现「获取合同类型目录失败」 | 账号缺 contractType:view：自动生成/改名不可用，合同通过处理不受影响 |
| 改了 handlers.py 没生效 | 等一个轮询周期即热加载；语法错误会在 watcher.log 记录并回退默认行为 |
| 换电脑/重装 | 整个文件夹复制过去即可（SQLite、进度、账本一并带走，不会重复处理） |
| 想手工查数据库 | 用任意 SQLite 工具打开 `data/watcher.db`：`contracts`（按公司分账）/`batches`（记账批次）/`fiscal_years`/`flush_requests` |

## 运行与验证记录（本机实测）

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 静态自测（不依赖后端/Excel） | `python selftest.py` | 11 项全通过 |
| 新增工作流用例 | `backend\.venv\Scripts\python.exe -m unittest tests.fix_verify.watcher.test_cw3*` | 61 项全通过（store / 批量记账 / 角色门禁 / 公司范围 / 三类触发 / GUI 冒烟） |
| 端到端（真实后端 + SQLite） | `code_audit/_e2e/run_e2e.py`（需先起后端） | 19 项全通过：PLAYER 拒绝、公司范围、只入库不记账、阈值触发、手动请求、财年 FY_END 结账 |
| 真实 Excel 写入 | `python code_audit/_e2e/run_excel_flush.py` | 9 项全通过：3 份合同 1 次会话、3 笔分录、平衡校验 right、读回账本金额正确、无残留 EXCEL.EXE |
