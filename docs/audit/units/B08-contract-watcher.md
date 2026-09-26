# B08 合同通过监听程序（分支归属：feature/contract-watcher 独有）

## 概述

**审计对象**（提交 `8b9dea0` 新增，当前 HEAD `0d38192`，分支 `feature/contract-watcher`）：

| 文件 | 本次结论密度 |
| --- | --- |
| `contract_watcher/contract_watcher.py` | 主战场：轮询/增量水位、去重、handler 自动生成与热加载 |
| `contract_watcher/handlers.py` | 当前**只有 11 行注释、没有任何处理函数**（见下） |
| `contract_watcher/readable.py` | 记录可读化：时间与时区、数字型字符串改写 |
| `contract_watcher/selftest.py` | 离线自测（覆盖生成/改名/翻译/分发，不覆盖轮询与网络） |
| `contract_watcher/bookkeeping_example/` | `shang.py`（xlwings 记账模板，真正的 xlsx 写入点）、`inspect_xlsx.py`、`run_branch_tests.py`、`soak_test.py` |
| `test_run_recheck/handlers.before.py`、`handlers.generated_after_test.py` | 仅用于对比：确认自动生成块形态（`# ===== [auto] ContractType.key = …` + `def handle_…_passed`），与当前 `handlers.py`（空模板）行为一致，无额外差异 |

**方法**：全部结论来自 `read`/`grep` 实读源码 + 纯函数级实测（未联网、未启动监听程序、未启动 Excel、未改动任何被审代码）。实测脚本保留在 `code_audit/_probe_cw.py`，原始输出见文末附录 A；`_probe_cw.py` 只在 `code_audit/_probe_tmp` 下写临时文件并自删（沙箱残留一个空目录 `code_audit/_probe_tmp/cwprobe_f8wo5x69`，无内容）。

**两条必须先讲清的结构性事实**：

1. **本分支交付的 `handlers.py` 里没有任何记账规则**（`contract_watcher/handlers.py:1-11` 全是注释），真正的记账代码是"示例" `bookkeeping_example/shang.py`，需要用户自己按 `README.md:143-173` 的片段粘进 handler。因此：**默认状态下本程序不会写任何 xlsx**，只会把合同 JSON 归档到 `records/<key>/`；而一旦按 README 接线，本文 CW-08/CW-14/CW-16/CW-17/CW-18 就是直接的钱账风险。
2. **幂等性完全押在 `data/state.json` 的一个 `executedAt` 字符串水位线上**：全链路没有任何"按合同 ID 已处理"的记录，也没有任何"该合同是否已入账"的判重列（xlsx 里也没有）。于是本文所有"重放"类缺陷（CW-01/03/04/20）都会直接变成重复分录，所有"吞异常/推进水位"类缺陷（CW-02/05/06/18/27）都会直接变成漏记，且两者都**不报警**。

缺陷清单：P0 × 2、P1 × 7、P2 × 11、P3 × 7，共 27 条（CW-01 … CW-27）。

---

## 缺陷清单

### [P0] CW-01 首次建立基线失败即把水位清空，导致历史合同全量重放（重复记账）
- 位置：`contract_watcher/contract_watcher.py:374`
- 代码：
```python
        state["lastExecutedAt"] = ""
        if not args.backfill:
            try:
                rows = backend.fetch_executed_ids(args.competition)
                state["lastExecutedAt"] = max((t for _, t in rows), default="")
            except Exception:  # noqa: BLE001
                state["lastExecutedAt"] = ""
```
（紧随其后的第 381 行无条件把这份 `state` 写进 `data/state.json`。）
- 触发条件：首次运行（无 `data/state.json`）时基线拉取**任一**异常即落入 `except`：后端还没起来（连接被拒）、401/403、15 秒超时、5xx、`http_json` 的 `code != 0`、"获取合同类型目录失败"之后的同轮抖动等。此时写入磁盘的是 `lastExecutedAt = ""`。
- 后果：主循环第 401 行 `t > last_seen` 对空串恒为真 ⇒ **服务器上全部历史 EXECUTED 合同一次性变成 fresh**，逐条 `dispatch` 记账（Excel 每条一个实例，见 CW-19）。因为账本里没有合同 ID 判重列，结果是历史金额整体翻倍；日志里只有正常的"已按类型处理"，没有任何"重放/重复"信号。这是"一次网络抖动 = 一次全量重复入账"。
- 修复建议：基线失败必须当作致命错误（不写 state、退避重试或直接退出并告警）；或改用后端现成的增量协议 `GET /api/contracts?status=EXECUTED&updatedAfter=<serverTime>`（`backend/apps/common/sync.py:29-45` 已提供 `serverTime`），并叠加"已处理合同 ID 集合"（见 CW-09）。

### [P0] CW-02 处理失败被吞 + 水位按列表最大值推进 → 合同被永久静默漏记、永不重试
- 位置：`contract_watcher/contract_watcher.py:402`、`contract_watcher/contract_watcher.py:284`
- 代码：
```python
            for cid, et in fresh:
                contract = backend.fetch_contract_detail(cid)
                if contract.get("status") != "EXECUTED":
                    continue
                dispatch(contract, registry[0], out_dir, args.competition)
                last_seen = max(last_seen, et)
```
```python
    except Exception:  # noqa: BLE001 - 单个合同处理失败不影响后续与网页端
        log.exception("处理合同 #%s(type=%s) 失败（已隔离）", contract["id"], key)
```
- 触发条件：handler 内任何异常——xlsx 正被 Excel 打开（COM 报错/只读）、合同缺 `inputs.amount` 导致 `float(None)`（CW-14）、账套科目名改过导致 `Find` 返回 None（CW-18）、Excel 进程被回收导致 RPC 不可用（`SOAK_REPORT.md:14-17` 实测过）——异常在 `dispatch` 内被吞；`status != "EXECUTED"` 的 `continue` 同理。
- 后果：第 409 行 `new_max = max((t for _, t in rows))` 是**按整份列表**取最大值，包含刚才失败/被跳过的那些合同 ⇒ 水位直接推过它们，下一轮不再 fresh，**永不重试**。账本少一笔钱，只有 `watcher.log` 里一行 exception；没有失败队列、没有重试次数、没有告警阈值。
- 修复建议：水位必须由"**处理成功**"驱动：以合同 ID 为主键记录 `processed` 集合（成功才落盘），失败进重试队列（带上限+退避）并在连续失败时告警；`status` 校验失败也应告警而非静默跳过。

### [P1] CW-03 轮内任一步异常 ⇒ 本轮已记账合同下一轮重复记账（进度写在循环之后）
- 位置：`contract_watcher/contract_watcher.py:402`
- 代码：
```python
            for cid, et in fresh:                          # 402  ← 已成功记账的合同从这里开始
                contract = backend.fetch_contract_detail(cid)   # 403 单条失败即抛出整个 for
                if contract.get("status") != "EXECUTED":
                    continue
                dispatch(contract, registry[0], out_dir, args.competition)
                last_seen = max(last_seen, et)             # 407  只改内存变量
            # 4) 持久化进度                                  # 408  进度写在循环之后
            new_max = max((t for _, t in rows), default="")     # 409
```
（第 410-412 行才把 `state["lastExecutedAt"] = new_max` 写入 `STATE_FILE`；上面的 `except Exception` 兜底在第 418 行。）
- 触发条件：同一轮有 ≥2 条 fresh，其中**第 2 条**的 `fetch_contract_detail` 抛错（5xx/超时/403），或第 412 行的 `STATE_FILE.write_text` 自身失败（文件被占用、磁盘满、备份进程锁）。两者都跳到第 418 行的兜底 `except`。
- 后果：本轮已经成功记账的第 1 条合同，其进度从未落盘 ⇒ 下一轮重新 fresh ⇒ **再记一次**（金额翻倍）。触发只需一次单条接口抖动，与 CW-01 组合后甚至不需要重启。
- 修复建议：每条处理成功后立刻原子落盘一次进度（或先记"处理中"再记"完成"），使进度具备"逐条提交"语义；并让 `state` 写入使用临时文件 + `os.replace`。

### [P1] CW-04 分页是 offset 快照且 fresh 未按合同 ID 去重 ⇒ 同一合同同轮被分发两次（并发下还会永久漏）
- 位置：`contract_watcher/contract_watcher.py:129`、`contract_watcher/contract_watcher.py:401`
- 代码：
```python
            params = f"status=EXECUTED&page={page}&pageSize=200"     # 129
            if competition_id:
                params += f"&competitionId={competition_id}"
            data = self.api(f"/api/contracts?{params}")
            ...
            if not batch or len(batch) >= total:                     # 138
                return out
            page += 1
```
```python
            fresh = sorted([(cid, t) for cid, t in rows if t > last_seen], key=lambda x: x[1])
```
- 触发条件：后端列表固定 `qs.order_by("-created_at")`（`backend/apps/contracts/views.py:200`、`:208`），翻页期间只要有新合同被创建（比赛进行中很常见），`skip` 偏移就会整体错位：**第 N 页的最后一条会在第 N+1 页再出现一次**（经典 offset 漂移）。`rows` 里于是出现重复 id，而 `fresh` 只是排序，没有 `set` 去重。
- 后果：同一合同在同一轮被 `dispatch` 两次 ⇒ 两笔分录；同时被"错位盖过"的另一条合同如果时间戳 ≤ 水位（水位随后按最大值推进）则**永久漏记**。这是唯一不需要任何故障、仅靠正常并发就能复现的重复记账路径。
- 修复建议：`fresh` 前先按 `cid` 去重（`dict`/`set`）并记录已分发集合；根治手段是改用后端 `updatedAfter` 增量（无分页、服务端游标）或按 id 游标翻页。

### [P1] CW-05 以 `executedAt` 字符串水位线做增量（严格大于）：同秒/格式差异即永久漏账
- 位置：`contract_watcher/contract_watcher.py:400`、`contract_watcher/contract_watcher.py:137`
- 代码：
```python
            rows = backend.fetch_executed_ids(args.competition)
            last_seen = state.get("lastExecutedAt") or ""
            fresh = sorted([(cid, t) for cid, t in rows if t > last_seen], key=lambda x: x[1])
```
```python
            for it in batch:
                if it.get("executedAt"):
                    out.append((int(it["id"]), str(it["executedAt"])))
```
- 触发条件（实测见附录 A-3）：(a) 两条合同的 `executedAt` 完全相同（整秒精度数据，例如导入/脚本/直接写库的合同；Django 的 MySQL 后端列是 `datetime(6)`，但任何外部写入方都可能给整秒）⇒ 严格 `>` 使时间戳等于水位的**第二条永远不会 fresh**；(b) DRF 编码 UTC 时间时 microsecond 为 0 会省略小数位，`"…15:20:46Z"` 的字典序**大于** `"…15:20:46.731818Z"`（实测 `True`），`max()` 取到整秒值后会把同一秒内所有毫秒级记录一起盖掉；(c) 任何"补写/回填"老时间戳的合同〈水位〉永不处理；(d) 一旦出现 `+08:00` 之类的偏移格式，字典序彻底失去时间语义。
- 后果：漏记合同且无任何日志（它连"未注册处理函数"的 info 都不会有）；`baselineAt` 字段存了却从不参与比较，说明作者本意也不是纯字符串游标。
- 修复建议：解析为 `datetime` 比较（`datetime.fromisoformat` 已能处理 `Z`/偏移），并把"水位"换成"已处理 ID 集合 + `serverTime` 游标"；同一秒并列的处理必须靠 ID 而不是时间。

### [P1] CW-06 自动生成的默认函数会静默覆盖用户自定义 handler（同名 def 后者生效）
- 位置：`contract_watcher/contract_watcher.py:183`、`contract_watcher/contract_watcher.py:231`
- 代码：
```python
def upsert_handler_for_key(key: str) -> bool:
    """handlers.py 中为该 key 生成默认函数（已存在则跳过）。返回是否新增。"""
    text = HANDLERS_FILE.read_text(encoding="utf-8")
    if MARKER_PREFIX + key + MARKER_SUFFIX in text:
        return False
    text = text.rstrip() + build_default_section(key) + "\n"
    HANDLERS_FILE.write_text(text, encoding="utf-8")
    return True
```
```python
    for key in keys:
        fn = getattr(mod, func_name_of(key), None)
        if callable(fn):
            if key in registry:
                log.warning("key=%s 的函数名冲突（%s），后者未生效", key, func_name_of(key))
                continue
            registry[key] = fn
```
- 触发条件（三条均已实测，附录 A-2）：(1) 用户按 `handlers.py:11`「删除某个自动生成块并重启后，程序不会自动补回」的说明删掉标注块、自己写 `handle_<key>_passed` ⇒ 标记行消失 ⇒ 下一轮 `sync_catalog` 重新追加默认块，**追加在文件末尾 ⇒ Python 后者生效**，用户函数被屏蔽（实测 registry 指向自动生成的默认函数、用户代码一次都没执行）；(2) 两个 key 的 slug 相同（实测 `material-procurement`≡`material_procurement`、`ABC`≡`abc`、`a.b`≡`a-b`、`---`≡`...`→`handle_key_passed`），新 key 自动生成时正是同名覆盖；(3) 第 233 行的冲突告警只按 **key** 判重（同一 key 两个标注行），不检测**函数名**冲突，所以上述情况一条告警都没有。
- 后果：该类型的记账逻辑静默失效（退化为把 JSON 归档），合同通过后"什么都没发生"，日志仍然是正常的 `已按类型处理`。
- 修复建议：`upsert` 前用 AST/正则检查 `func_name_of(key)` 是否已存在于文件中，冲突则报错跳过并告警；自动块用不会被用户复用的独立命名（如 `_auto_handle_<slug>_passed`）再由注册表按 key 显式映射；不要用"同名 def 后定义覆盖前定义"作为幂等手段。

### [P1] CW-07 自动生成的代码把 key 直接内插进 docstring：含 `"""` 或换行的 key 会把 handlers.py 写成语法错误，导致全部 handler 失效
- 位置：`contract_watcher/contract_watcher.py:148`
- 代码：
```python
def build_default_section(key: str) -> str:
    func = func_name_of(key)
    return (
        "\n\n" + MARKER_PREFIX + key + MARKER_SUFFIX + "\n"
        f"def {func}(contract: dict, ctx: dict) -> None:\n"
        f'    """{key} 类型合同通过后的处理（自动生成的默认函数）。\n'
        "    定制：删除下面这行 [auto-default] 注释后，替换为你自己的实现。\n"
        "    可用：ctx['out_dir']（输出根目录）、ctx['typeKey']（当前合同类型 key）、\n"
```
- 触发条件：后端对 key **没有字符集校验**（`backend/apps/contracts/serializers.py:88-92` 只校验非空 + `max_length=128`），管理端可创建 `key = 'x"""y'` 或含换行的 key；`sync_catalog`（第 305 行）在下一轮自动追加该块。实测 `compile(build_default_section('bad"""key'))` → `SyntaxError`；含 `\n` 的 key 同样 `SyntaxError`。
- 后果：用户文件被写成坏文件且**不会自愈**；`load_handlers` 捕获异常后返回 `{}`（第 226-228 行）⇒ 所有合同类型一起退化为默认存档，记账全线停止，仅 `watcher.log` 一行"handlers.py 加载失败"。用户自己写的代码还在文件里但永远不可达。
- 修复建议：用 `key.replace('"', '').replace("\n", " ")`（或 `repr()`）后再内插；生成后先 `compile()` 校验，失败即回滚（临时文件 + `os.replace`，保留 `.bak`）。

### [P1] CW-08 `shang.py` 在模块级把 Decimal 精度改成 2：按文档接线后金额静默算错
- 位置：`contract_watcher/bookkeeping_example/shang.py:48`
- 代码：
```python
getcontext().prec = 2

class xledit:
    xlapp:xw.App = None
    wb:xw.Book = None
    entries_to_assets_money = ''
```
- 触发条件：handler 里 `from shang import xledit, ...`（`README.md:152` 就是这么教的），随后按 `DATA_GUIDE.md:158` 的建议用 Decimal 做金额运算——`getcontext()` 是**线程/进程级全局上下文**，import 即生效。
- 后果（实测，附录 A-5）：`Decimal('1234.56')+Decimal('0.44')` → `1.2E+3`（1200）；`Decimal('12345.67')*3` → `3.7E+4`（37000，正确值 37037.01）；`Decimal('999999.99')/3` → `3.3E+5`。任何"单价×数量、含税/折扣、汇总合计"都会静默错到不可用，而且污染同进程内所有 Decimal 运算（不只是记账）。这比 float 误差严重得多，是量级错误。
- 修复建议：删除该行；确需限精度请用 `with localcontext() as ctx:` 局部设置。

### [P1] CW-09 记账侧完全没有"按合同 ID"的幂等键；`xledit` 打开失败会泄漏隐藏 Excel 进程（失败即静默漏账）
- 位置：`contract_watcher/bookkeeping_example/shang.py:54`、`contract_watcher/bookkeeping_example/shang.py:76`
- 代码：
```python
    def __init__(self,file:str,debug:bool = False):
        p = Path(file)
        if(p.is_file() != True):
            raise
        if debug == False:
            self.xlapp = xw.App(visible=False,add_book=False)
            self.wb = self.xlapp.books.open(file)
            self.xlapp.api.ScreenUpdating = False
            self.xlapp.api.DisplayAlerts = False
```
```python
    def save(self):
        self.wb.save()
        self.wb.close()
        self.xlapp.quit()
```
- 触发条件：任一条重放路径（CW-01/03/04/20，或人工 `--backfill`）；以及 xlsx 被人工 Excel 打开/只读/损坏/网络盘瞬时不可用导致 `books.open` 抛错。
- 后果：**(1)** 账本里没有任何"合同号/合同ID → 已入账"的判重列，也没有独立台账文件，重复处理 = 重复金额，事后只能靠人工比对合同编号才发现；**(2)** `books.open` 抛错时 `xw.App()` 已经启动，`__init__` 没有 `try/except + quit()`，每次失败泄漏一个**隐藏 EXCEL.EXE**（冷启动约 60 秒，`SOAK_REPORT.md:7`），泄漏进程会继续占用该 xlsx，使后续尝试更容易失败，而 `contract_watcher.py:284` 又把异常吞掉 ⇒ 持续性静默漏账；`README.md:164-168` 建议的 `finally: book.xlapp.quit()` 也保护不到构造函数内部的这次启动。
- 修复建议：账本首列写入合同 ID/编号并在写入前查重（存在即跳过并记日志）；`__init__` 内部对 `App()/books.open()` 加 `try/except`，失败时 `quit()` 后再抛出；把"记账失败"上报为可见错误（不要只写日志）。

### [P2] CW-10 凭据失效后无限静默失败：进程不退、不告警、状态不变
- 位置：`contract_watcher/contract_watcher.py:107`、`contract_watcher/contract_watcher.py:413`
- 代码：
```python
        try:
            env = http_json("GET", f"{self.server}{path}", self.token)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.login()
                env = http_json("GET", f"{self.server}{path}", self.token)
            else:
                raise
```
```python
        except urllib.error.HTTPError as e:
            if e.code == 401:
                log.warning("登录态失效，下一轮自动重登")
            else:
                log.warning("后端请求失败 HTTP %s", e.code)
```
- 触发条件：账号被改密、被禁用、`contract:view` 被回收、JWT_SECRET 轮换后 token 与密码都不再有效（改密场景下 `login()` 必然失败并抛出，落入第 413 行的 HTTPError 分支）。
- 后果：程序每轮固定发 2 个请求 + 一行 warning，永远循环（无"连续 N 次失败退出"、无健康检查端点、无告警），运维看起来"进程在跑"。好消息是水位不前进，恢复后能补记；但补记期间的所有合同都会用"运行当天"入账（CW-17）。附带凭据问题：密码是 `--password` **明文命令行参数**（`README.md:26`、`TEST_FLOW.md:27`），会出现在进程表、任务计划配置、shell 历史里；且文档全部用 `http://` 明文登录（局域网/远程部署可被抓包）。
- 修复建议：连续认证失败 N 轮即退出并明显告警；支持环境变量/凭据文件；默认只允许 `http://127.0.0.1`，远程强制 https；日志与输出中永远不要出现密码/token（现状 OK，未见泄漏）。

### [P2] CW-11 跨比赛 / 跨租户混记：`--competition` 可选且为假值时静默失效
- 位置：`contract_watcher/contract_watcher.py:323`、`contract_watcher/contract_watcher.py:130`
- 代码：
```python
    ap.add_argument("--competition", type=int, default=None)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--out-dir", default=str(RECORDS_DIR))
    ap.add_argument("--port", type=int, default=47653, help="单实例互斥端口")
```
```python
            params = f"status=EXECUTED&page={page}&pageSize=200"
            if competition_id:
                params += f"&competitionId={competition_id}"
```
- 触发条件：不传 `--competition`（`README.md:25` 与 `TEST_FLOW.md:26` 都把它写成可选；仓库里提交的证据日志 `test_run_recheck/watcher.log:1` 就是 `competition=None`），且账号是 `admin`（文档推荐的账号）或"纯 view 且未配公司范围"的账号——后者按 `backend/apps/contracts/views.py:781-784`（空范围 = 不限制、可见全部合同）同样全量可见；`--competition 0` 也会因为 `if competition_id:` 而静默不过滤。
- 后果：不同比赛、不同公司的合同全部进入同一本 xlsx 与同一个 `records/<key>/` 目录：金额跨比赛串账（`ctx['competitionId']` 虽被传入但 `default_archive` 完全没用于分区），本机归档目录里同时落有其他比赛公司的合同原文（公司名、合同编号、金额）。在"多比赛共用一套后端"的场景里就是跨租户数据混装 + 错账。
- 修复建议：把 `--competition` 改为必填（或至少在多比赛可见时强制要求并告警）；`records` 路径与账本按 `competitionId` 分区；文档中的示例命令补上 `--competition`；不要推荐用超管账号跑只读监听（用最小权限 + 公司范围账号）。

### [P2] CW-12 每轮全量重拉（含完整 DSL/graph）＋固定 3 秒无退避：随历史线性增长的自伤流量
- 位置：`contract_watcher/contract_watcher.py:129`、`contract_watcher/contract_watcher.py:399`、`contract_watcher/contract_watcher.py:420`
- 代码：
```python
            params = f"status=EXECUTED&page={page}&pageSize=200"
```
```python
            sync_catalog(backend, state, registry)
            # 3) 检测合同通过
            rows = backend.fetch_executed_ids(args.competition)
            last_seen = state.get("lastExecutedAt") or ""
```
```python
        time.sleep(max(0.5, args.interval))
```
- 触发条件：常态运行（默认 `--interval 3`）。
- 后果：列表接口对每行都返回**完整合同**（`backend/apps/contracts/serializers.py:175-197`：`contractType` 的 `effects/conditions/graph` + `executionLog/executionResult` + 逐行 `_enrich_party_companies` 公司反查），而程序每轮把所有历史 EXECUTED 合同全部分页拉一遍：合同数 N ⇒ 每轮 `ceil(N/200)+1` 个重请求（另加 1 个 `/api/contract-types` 全量目录），间隔 3 秒、永不衰减即 `(N/200+2)/3` 请求/秒，开销只增不减；后端不可用时也保持同一节奏（`time.sleep` 固定、无指数退避、无抖动），叠加 `--interval 0.5` 时可到 2~4 请求/秒。
- 修复建议：改用 `updatedAfter=<serverTime>` 增量拉取（只在有新数据时返回）、失败的退避 + 抖动、把"目录同步"降到分钟级；只在需要时才对单个合同拉详情。

### [P2] CW-13 `state.json`、`handlers.py` 都是非原子写；损坏后静默改变行为
- 位置：`contract_watcher/contract_watcher.py:354`、`contract_watcher/contract_watcher.py:412`
- 代码：
```python
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}
```
```python
                state["lastExecutedAt"] = new_max
                STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
```
- 触发条件：进程被杀/断电正好落在 `write_text` 的"先截断再写"窗口内 ⇒ `state.json` 为空或半截 JSON（Windows 上被杀进程、任务计划重启、同步盘/杀软都很常见）；`handlers.py` 同理（第 189、210 行也是整文件覆写）。
- 后果：解析失败 ⇒ `state = {}` ⇒ 走"首次基线"分支 ⇒ **静默**把当前最大值当基线，重启前未处理的合同全部漏掉（且与 CW-01 相反方向的错：这次是丢账而不是重账）；`handlers.py` 被写坏时 `load_handlers` 返回 `{}` ⇒ 全部退化为默认存档（不记账）。两种情况都只有一行日志，且 `state` 里的 `catalog`（typeId→key）一丢，改名检测也会错乱。
- 修复建议：两者都用"写临时文件 + `os.replace`"原子替换并保留 `.bak`；解析失败时明确告警并要求人工确认，而不是静默重建基线；`handlers.py` 改写前先备份并保证可回滚。

### [P2] CW-14 金额一律 `float()`：None/空串/千分位字符串抛错被吞、大额精度丢失、0 值不写
- 位置：`contract_watcher/bookkeeping_example/shang.py:92`
- 代码：
```python
        if (add != 0):
            sht[i,3].value = float(add)
        if (minus != 0):
            sht[i,4].value = float(minus)
```
- 触发条件：按 `README.md:157-160` 的推荐写法取数 `amount = contract["inputs"].get("amount")`；而 `DATA_GUIDE.md:98` 明确"未填写的输入项不会出现"、`:158` 又说金额可能是字符串（如 `"123.45"`）。
- 后果（实测，附录 A-5）：`amount is None` ⇒ `None != 0` 为真 ⇒ `float(None)` **TypeError**（`""`、`"1,000"` 同理 ValueError）⇒ 异常被 `contract_watcher.py:284` 吞掉 ⇒ 合同标记为已处理但**一条分录都没有**；`>15` 位有效数字经 float→Excel double 静默丢低位（实测 `12345678901234567890` → `1.23456789012346E+19`）；`add=minus=0` 时 D/E 两列都不写，只留下摘要和余额公式（形成"空金额行"）。
- 修复建议：入口统一 `Decimal(str(v))` 并显式校验 `None/空串/非数字`（校验失败要抛出让上层重试，而不是被吞）；0 也应写入 `0` 以保持借贷两列对齐；超大金额可按 `DATA_GUIDE.md:158` 的建议以文本原样保留。

### [P2] CW-15 `readable.py`：时间 UTC 不本地化（差 8 小时且无标注）＋ 数字型字符串被千分位化破坏
- 位置：`contract_watcher/readable.py:113`、`contract_watcher/readable.py:60`
- 代码：
```python
def _fmt_dt(v: Any) -> str:
    return (str(v)[:19].replace("T", " ") if v else "—")
```
```python
    if isinstance(v, str):
        s = v.strip()
        if _NUM_RE.match(s):
            return _group_int(s)
        return s
```
- 触发条件：常态。后端返回 UTC（`test_run_recheck/REPORT.md:46` 实测 `2026-09-10T15:20:46.731818Z`），且合同里的"纯数字字符串"字段（合同编号、账号、编号类输入项）都会命中 `_NUM_RE`。
- 后果（实测，附录 A-4/A-8）：`执行时间` 显示 `2026-09-10 15:20:46`，比北京时间早 8 小时且无时区标注 ⇒ 跨天/跨财年归属判读错误，且与 `default_archive` 文件名用的**本地时间戳**（第 251 行 `time.strftime`）互相对不上；`'+1234'` → `'1,234'`（**加号被吞**，`_group_int` 只认 `-`）、`'-0012345'` → `'-0,012,345'`（前导零导致错误分组）、int 加千分位而 float 不加（`1234567.891` 原样），可读记录里的人工对账依据被静默改写。
- 修复建议：用 `datetime.fromisoformat` 解析后按本地时区输出并保留时区标注（或统一输出 UTC+标注）；只对 `inputSchema.type` 为金额/数量的项做千分位，其余原样；`_group_int` 保留 `+`、禁止对含前导零的串分组。

### [P2] CW-16 `shang.py` 槽位匹配过苛；找不到空槽时覆盖并合并账表最后一行（破坏已有数据）
- 位置：`contract_watcher/bookkeeping_example/shang.py:113`、`contract_watcher/bookkeeping_example/shang.py:122`
- 代码（定位循环的终止条件与"走到最后一行"的处置）：
```python
        i = 0
        while (i != sht.used_range.last_cell.row):
            if (sht[i,0].value != None):
                if(sht[i+4,0].value == name):
                    break
                if((sht[i+4,0].color == (0,255,0))and(sht[i+4,0].value == None)):
                    break
                i += 1
                continue
            i += 1
        if(i == sht.used_range.last_cell.row):
```
```python
            i -= 1
            sht[i,0].value = '库存商品成本期末移动平均结转报告'
            sht.range(f'A{i+1}:L{i+1}').merge()
```
```python
            if (sht[i+6,3].value == 0) and (sht[i+6,6].value == 0):   # 236 槽位判定
```
- 触发条件：(a) 新物料名超过模板预置的绿色空槽数量（`TEST_REPORT_v2.md:51` 实测模板每 19~20 行一块，"原材料(加工用)"共 199 行 ⇒ 约 10 个槽），循环走到底 ⇒ 进入覆盖分支；绿色判定是精确 RGB 元组 `(0,255,0)`，主题/版本差异或槽位被着色都会让它更早触发。(b) 写数据循环（233-253 行）的槽位条件是 `sht[i+6,3].value == 0 and sht[i+6,6].value == 0`，**空单元格是 None ≠ 0** ⇒ 空槽被跳过，一直到第 234-235 行 `i == last_cell.row` 时直接 `break` ⇒ 数量既不写入也不报错。
- 后果：(a) 用 0 基索引 `i=last_cell.row-1` 正好落在 Excel 最后一行上，把该行（通常是上一块物料的合计行）覆盖成报表标题，并 `A:L` 合并 —— 合并只保留左上角值，**原数据不可恢复**；(b) 采购/出库数量静默丢失（旧账套就是因残值 1/2/3 出现过定位错乱，见 `test_output/TEST_REPORT_v2.md:38` 的"上一轮结论"），账实不符且无异常。
- 修复建议：空槽判定统一为 `value in (None, 0, "")`；找不到槽位必须抛业务异常或走显式扩表逻辑（插入既有行块并复制格式），绝不能覆写最后一行；颜色判定改为读取模板命名区域/单元格批注而不是硬编码 RGB。

### [P2] CW-17 账期取"运行当天"而不是合同执行时间（重放/补记即跨期错账）
- 位置：`contract_watcher/bookkeeping_example/shang.py:82`
- 代码：
```python
        sht = self.wb.sheets[0]
        cdt = dt.date.today()
        dtstr = cdt.strftime("%Y/%m/%d")
        i = 2
        while (sht[i,6].value != None):
            i += 1
        sht[i,0].value = dtstr
```
- 触发条件：任何延迟处理——`--backfill` 补存量、CW-01 的全量重放、后端/凭据故障恢复后补记（CW-10）、监听程序停机几天后重启。
- 后果：昨天甚至上季度通过的合同被记成"今天"发生的业务 ⇒ 跨天/跨月/跨财年错期，与合同 `executedAt` 无法对账；函数签名（`add_book_entries(add, minus, number, about)`）里根本没有日期参数，说明这是设计缺口而不是调用方疏忽。
- 修复建议：`add_book_entries` 增加业务日期参数，由 handler 传合同 `executedAt` 换算出的当地业务日（与 CW-15 的时区修正一起做）；补记时保留原始执行时间并在摘要里标注。

### [P2] CW-18 科目按中文名 `Find` 且不校验未命中：改名/换账套即静默漏账或写错行
- 位置：`contract_watcher/bookkeeping_example/shang.py:269`
- 代码：
```python
        search_range = sht.api.UsedRange
        found_cell = search_range.Find(What=name.value,LookIn=xw.constants.FindLookIn.xlValues)
        colunmT = 0
        rowT = 0
        rowT = found_cell.Row-1
        colunmT = found_cell.Column-1
        rowT += 3
```
- 触发条件：账套换版/科目改名/名称含多余空格（枚举里 13 个中文名必须与模板**逐字**一致）；`Find` 在 `UsedRange` 内任意列搜索，标题、说明、合计行里的同名文本都可能先命中。作者自己的对比测试已经暴露这种脆弱性：同一脚本在旧/新两版账套上行为不同（`TEST_REPORT_v2.md:9`、`:38`、`:83`⑤）。
- 后果：`Find` 未命中返回 `None` ⇒ `found_cell.Row` AttributeError ⇒ 被 `contract_watcher.py:284` 吞掉 ⇒ 该合同静默漏账（且按 CW-02 永不重试）；命中错误单元格时，代码继续按 `+3` 行向下找"两个空 formula 单元格"写入 ⇒ 金额落进无关区域，账表被污染且没有任何报错。
- 修复建议：显式判 `found_cell is None` 并抛出可重试的业务异常（要告警）；把科目定位改为固定行列映射或给模板加隐藏的"科目 key"列，避免靠显示名匹配。

### [P2] CW-19 单线程同步轮询 + Excel 自动化无超时/看门狗：一次卡住即整体停摆且无痕迹
- 位置：`contract_watcher/contract_watcher.py:403`、`contract_watcher/contract_watcher.py:420`
- 代码：
```python
            for cid, et in fresh:
                contract = backend.fetch_contract_detail(cid)
                if contract.get("status") != "EXECUTED":
                    continue
                dispatch(contract, registry[0], out_dir, args.competition)
                last_seen = max(last_seen, et)          # ← 处理在轮询线程内同步执行
```
```python
        time.sleep(max(0.5, args.interval))
```
- 触发条件：handler 内驱动 Excel —— 冷启动约 60 秒/次（`SOAK_REPORT.md:7`）、每条合同一个新 `xw.App()` 实例、模态弹窗（文件被占用、恢复窗格）、COM 瞬时错误或进程死亡（`SOAK_REPORT.md:14-17` 实测 `RPC 服务器不可用`、`OLE error 0xe0000002`）。
- 后果：轮询与处理在**同一线程串行**：处理期间完全不轮询（`--interval 3` 形同虚设），一旦 COM 调用永久挂起，监听程序整体停摆——不写日志、不退出、没有心跳，运维只能靠"账本没更新"发现；`--backfill` 时每个历史合同一个 Excel 实例，耗时线性放大。另外 `shang.check()` 只 `print()` 不返回也不记日志（第 68-75 行），后台运行时 stdout 通常无处可去 ⇒ 文档宣称的"内置平衡校验"实际不可见。
- 修复建议：轮询与处理解耦（内存/文件队列 + 独立工作进程），Excel 操作加超时与"失败重启 Excel"策略，加心跳/进度日志；`check()` 改为返回值/抛异常并写日志。

### [P2] CW-20 单实例互斥只覆盖本机端口，跨机器仍会重复记账
- 位置：`contract_watcher/contract_watcher.py:340`
- 代码：
```python
    try:
        lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lock_sock.bind(("127.0.0.1", args.port))
        lock_sock.listen(1)
    except OSError:
        print(f"另一个监听实例已在运行（端口 {args.port} 被占用），本实例退出。"
              f"如需更换端口请用 --port。", file=sys.stderr)
        return 1
```
- 触发条件：两台机器（或多台测试机）用同一后端跑同一个监听程序（`README.md:175-179` 鼓励用任务计划/`@reboot` 自启，多机部署很常见）；或者本机换 `--port` 起第二个实例。
- 后果：端口锁只在当前主机有效 ⇒ 两个实例各自拉取同一批合同、各自记账：若账本路径相同则两个 Excel 实例交错写同一文件（后保存者覆盖前者，损失另一实例的分录）；若各用一份副本则会话账本分叉。文档"单实例：重复启动会被端口锁拒绝"给出了过强的安全感。
- 修复建议：把互斥点放到共享资源上——账本文件加锁（或被 Excel 独占打开即为锁）+ 已处理合同 ID 集合放在共享位置；`--port` 应视为调试开关而非隔离手段。

### [P3] CW-21 自测/浸泡脚本恒返回退出码 0，失败会被自动化当成成功
- 位置：`contract_watcher/selftest.py:132`、`contract_watcher/bookkeeping_example/soak_test.py:89`
- 代码：
```python
    print(f"TOTAL PASS={PASS} FAIL={FAIL}")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
```
```python
                if not recoverable:
                    raise SystemExit(0)
```
- 触发条件：任一断言失败 / 浸泡出现不可恢复错误。
- 后果：`selftest.py` 只打印 `TOTAL PASS/FAIL` 而不 `sys.exit(1)`；`soak_test.py` 失败时 `SystemExit(0)`。两者在 CI、任务计划、或"脚本跑完了没报错"的人工判断下都表现为成功；`test_run_recheck/REPORT.md:8` 的"11/11 通过"也只能靠人读输出。
- 修复建议：`FAIL > 0` 时 `sys.exit(1)`；浸泡失败 `SystemExit(1)` 并保留失败现场（不删临时文件）。

### [P3] CW-22 `run_branch_tests.py` 的"运行后哈希"实为运行前计算；`taskkill` 会误杀用户 Excel
- 位置：`contract_watcher/bookkeeping_example/run_branch_tests.py:306`、`contract_watcher/bookkeeping_example/run_branch_tests.py:329`、`contract_watcher/bookkeeping_example/run_branch_tests.py:77`
- 代码：
```python
    names = sys.argv[1:] or list(CASES)
    src_sha = sha(SRC)
    for name in names:
```
```python
    log(f"原文件 SHA256（运行后）: {src_sha}")
```
```python
def reap(new_pids: set[int]) -> None:
    for pid in new_pids:
        os.system(f"taskkill /F /PID {pid} > nul 2>&1")
```
- 触发条件：每次跑用例。
- 后果：(1) 日志里标着"运行后"的哈希其实是循环**之前**算的，脚本从未验证 `target.xlsx` 未被改动 —— `TEST_REPORT_v2.md:8` 声称的"运行前后哈希一致"并非由脚本保障（这次是靠人工 `sha256`）；(2) `reap` 对"用例期间新出现的所有 EXCEL.EXE" 执行 `taskkill /F`，会连用户自己打开、正在编辑的 Excel 一起强杀（未保存数据丢失），而 `SOAK_REPORT.md:24` 又证明外部 `taskkill` 与该环境下的 COM 崩溃高度相关，误杀会反过来污染后续用例结论；`excel_pids()` 还用固定共享临时文件 `dsh_tasklist_out.txt`，并发跑两个用例会互相覆盖解析结果。
- 修复建议：结束时重新计算哈希并 `assert` 相等；只回收本脚本自己启动的 PID（xlwings 记录 PID）；临时文件用 `tempfile.mkstemp` 唯一化。

### [P3] CW-23 秒级时间戳命名导致同名覆盖：重复处理不留痕、浸泡证据互毁
- 位置：`contract_watcher/contract_watcher.py:251`、`contract_watcher/bookkeeping_example/soak_test.py:36`
- 代码：
```python
    sub.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    f = sub / f"contract_{contract['id']}_{stamp}.json"
    f.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
```
```python
STAMP = time.strftime("%Y%m%d_%H%M%S")
TAG = f"{MODE}{'_debug' if DEBUG else ''}"
XLSX = OUT / f"soak_{TAG}_{STAMP}.xlsx"
JSONL = LOGS / f"soak_{TAG}_{STAMP}.jsonl"
```
- 触发条件：同一合同在同一秒内被处理两次（正是 CW-03/04 的重复路径）；或同一秒内启动两次浸泡测试。
- 后果：归档文件被静默覆盖，**重复处理不留任何痕迹**（恰好掩盖了 CW-03/CW-04 的现场，这也是它们难以被发现的原因）；`soak_test.py` 用 `JSONL.open("w")` 打开同名日志会直接截断上一轮的记录。
- 修复建议：文件名加入毫秒/微秒与随机短后缀；同名已存在时改为追加或改名而不是覆盖。

### [P3] CW-24 `safe_dirname` 放过 `..`：输出目录可越出 `out_dir`
- 位置：`contract_watcher/contract_watcher.py:64`
- 代码：
```python
def safe_dirname(key: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(key))
```
- 触发条件：合同类型 key 由管理端创建且后端**不校验字符集**（`backend/apps/contracts/serializers.py:88-92`）：实测 `safe_dirname("..") == ".."`，于是 `out_dir / ".."`（第 249-250 行 `mkdir` + 写文件）落到 `records` 的**父目录**；Windows 保留名（`CON`/`NUL`/`AUX`…）与超长 key 会 `OSError`，异常同样被第 284 行吞掉。
- 后果：合同 JSON（含公司名、合同编号、金额、字段变动）被写到预期目录之外；保留名/超长 key 则静默不存档（`records` 里少文件，日志只有一行 exception）。
- 修复建议：目录名白名单化（`[A-Za-z0-9_.-]`，并显式替换 `.`/`..` 与 Windows 保留名）、限制长度，写入前校验 `resolved_path.is_relative_to(out_dir)`。

### [P3] CW-25 `MARKER_RE` 对缩进敏感；改名用子串替换会连带改掉别的类型
- 位置：`contract_watcher/contract_watcher.py:50`、`contract_watcher/contract_watcher.py:206`
- 代码：
```python
MARKER_RE = re.compile(r"^# ===== \[auto\] ContractType\.key = (.*?) =====$", re.MULTILINE)
```
```python
        if line.strip() == MARKER_PREFIX + old_key + MARKER_SUFFIX:
            lines[i] = MARKER_PREFIX + new_key + MARKER_SUFFIX
            changed = True
        elif line.lstrip().startswith("def ") and old_func in line:
            lines[i] = line.replace(old_func, new_func)
            changed = True
```
- 触发条件：(a) 用户把标注行缩进/加空格（`load_handlers` 用 `finditer` 直接匹配**不做 strip**，而 `section_span` 用 `lines[i].strip()`，同一文件两套判定；实测缩进后不匹配）；(b) 两个 key 的 slug 互为前缀，如 key `x` 与 `x_passed`（实测 `handle_x_passed` 是 `handle_x_passed_passed` 的子串）。
- 后果：(a) 该类型的处理函数不再被注册 ⇒ 静默退化为默认存档；(b) 改名 `x → x2` 时会把另一类型的 `def handle_x_passed_passed` 一起改成 `handle_x2_passed_passed`，而它的标注行仍是旧 key ⇒ 那个类型静默退化。
- 修复建议：匹配前统一 `strip()`；改名改为"定位标注行后第一条 `def` 行，用 `^\s*def\s+<old_func>\s*\(` 精确匹配整行替换"，避免子串替换。

### [P3] CW-26 运行产物未纳入 `.gitignore`：业务合同 JSON 与日志有入库风险
- 位置：`.gitignore:54`（`contract_watcher` 只忽略了测试 xlsx）
- 代码：
```gitignore
# ===== contract_watcher 测试产物 =====
contract_watcher/bookkeeping_example/test_output*/*.xlsx
contract_watcher/bookkeeping_example/test_output*/**/*.xlsx
```
- 触发条件：按 `README.md:25` 的示例在 `contract_watcher/` 目录内直接运行监听程序 ⇒ 生成 `contract_watcher/records/**`（合同全量 JSON + 中文可读版）、`contract_watcher/watcher.log`、`contract_watcher/data/state.json`，这三类都**不在忽略列表**里。
- 后果：`git add -A` 会把参与公司名、合同编号、金额、字段变动等业务数据连同含本机绝对路径与服务器地址的日志一起提交；仓库里已有同类先例——`test_run_recheck/records/*.json`、`test_run_recheck/watcher.log` 已被提交，其中 `watcher.log:1` 就记录了本机绝对路径与后端地址（文件本身是测试数据，风险在于这套路径没有防护）。
- 修复建议：忽略 `contract_watcher/watcher.log`、`contract_watcher/data/`、`contract_watcher/records/`；日志不写绝对路径；提交证据文件前脱敏。

### [P3] CW-27 `executedAt` 为空的 EXECUTED 合同被静默忽略（文档断言其不存在）
- 位置：`contract_watcher/contract_watcher.py:136`
- 代码：
```python
            total = data.get("total") or len(batch)
            for it in batch:
                if it.get("executedAt"):
                    out.append((int(it["id"]), str(it["executedAt"])))
            if not batch or len(batch) >= total:
                return out
```
- 触发条件：任何并非经由 `POST /api/contracts/:id/execute` 产生的 EXECUTED 合同（数据导入、脚本建单、直接改库、迁移），`status='EXECUTED'` 但 `executed_at` 为 NULL——模型允许（`backend/apps/contracts/models.py:73` `executed_at = models.DateTimeField(null=True, blank=True)`），而 `DATA_GUIDE.md:32` 断言"EXECUTED 合同必有 executedAt"。
- 后果：该合同既不会被处理，也不会推进水位，**连一行日志都不会有** ⇒ 永久漏记且无痕迹（排查时甚至会怀疑是"合同没过"）。
- 修复建议：对"status=EXECUTED 但缺 executedAt"的记录显式告警，或回退使用 `updatedAt/signedAt` 作为游标并按 ID 去重处理。

---

## 存疑/待确认

1. **Excel 公式注入（未实测）**：`shang.py:101` `sht[i,6].value = str(number) + " " + str(about)` 把用户可控的合同编号/名称写进单元格；xlwings 的取值链路是 `self.xl.Value = data`（`site-packages/xlwings/_xlwindows.py:1206-1209`），Excel COM 对以 `=` 开头的字符串会按公式解析。若某合同的 `contractNumber`/`name` 以 `=` 开头（后端未见对 `contractNumber` 的字符集过滤，`[待确认]`），摘要格会变成公式（DDE/超链接类载荷在打开账本时触发提示）。本环境不允许启动 Excel，未做实测，故未列为正式缺陷；建议写入前显式加文本前缀或把该列设为文本格式。
2. **`add_book_item` 的插行/autofill 分支完全未被任何测试覆盖**：`shang.py:238-240`、`:248-250`（`sht.range(f'{i+8}:{i+8}').insert(shift='down')` + `autofill`）在所有报告里都因 COM 崩溃而未执行（`TEST_REPORT_v2.md:21`、`:63-73`）。插入位置与目标行（`sht[i+7,5]` 判定的"本期采购入库"）的偏移关系未经实测；若位置错，账表结构会被破坏且不会报错。建议在正常桌面 Excel 上先行验证这条路径再用于正式记账。
3. **整秒 `executedAt` 的出现概率未实测**：CW-05 的字符串比较反例已实测成立，但"生产库里会有多少条同秒/整秒记录"取决于数据库列精度与实际写入方（Django 的 MySQL 后端建 `datetime(6)`；`_make_test_data.py` 用 `timezone.now()`）。要判定实际影响面，需要统计一次 `SELECT executed_at, COUNT(*) FROM contracts WHERE status='EXECUTED' GROUP BY executed_at HAVING COUNT(*)>1`。
4. **绿色槽位判定的稳定性**：`shang.py:117` `sht[i+4,0].color == (0,255,0)` 依赖 xlwings 返回的颜色元组格式；主题色/条件格式/不同 Excel 版本下是否恒等于 `(0,255,0)` 未验证（影响 CW-16 的触发面）。
5. **`fetch_executed_ids` 假定列表 `data` 是 dict**：若后端某天把 `/api/contracts` 的 `data` 改成数组（`/api/contract-types` 就是数组），`data.get("items")` 会 AttributeError 并被轮级兜底吞掉 ⇒ 每轮失败、永不处理任何合同。当前后端返回 `{items,total,page,pageSize}`（`backend/apps/common/pagination.py:41-48`），属兼容性风险，非现存缺陷。
6. **`.old-DATE.xlsx` 轮转逻辑在本分支不存在**：`bookkeeping_example/target.old-20260910.xlsx` 是人工归档的旧账套（`TEST_REPORT_v2.md:9`），代码里没有任何轮转/备份逻辑，故无"时间戳冲突、并发轮转"缺陷可报；但这也意味着 `target.xlsx` 被 Excel 写坏时**没有自动备份**（唯一防线是 CW-09/13 提到的原子写建议）。
7. **`handlers.py` 当前为空模板**：这意味着"轮询 → 记账"链路在仓库现状下不会写任何 xlsx，只有默认 JSON 归档；`README.md:41-43` 把"按 handlers.py 里的规则做记账"描述成既有行为，与仓库内容不符（记账需用户自行接线，见 `README.md:143-173`）。按"不报纯文档/风格问题"的要求未单列，但验收时需注意。

---

## 附录 A：实测记录（`code_audit/_probe_cw.py`，Python 3.14.6 / xlwings 0.36.5；未联网、未启动 Excel）

A-1 函数名 slug 冲突（对应 CW-06）：
```
'material-procurement' -> 'handle_material_procurement_passed' | 'material_procurement' -> 同 | collide=True
'ABC' -> 'handle_abc_passed' | 'abc' -> 同 | collide=True
'a.b' -> 'handle_a_b_passed' | 'a-b' -> 同 | collide=True
'---' -> 'handle_key_passed' | '...' -> 'handle_key_passed' | collide=True
'x' -> 'handle_x_passed' | 'x_passed' -> 'handle_x_passed_passed' | collide=False | substring=True
```
A-2 自动生成覆盖用户 handler（对应 CW-06）：
```
appended_new_block=True; def-count=2
registry['ABC'] src-line=7 of 14 lines      # 用户手写 def 在第 3 行 → 注册到的是第 7 行的自动默认函数
call -> ctx={'err': "KeyError('default_archive')"}   # 执行的是自动默认函数（用户代码未运行）
```
A-3 时间戳字符串比较（对应 CW-05）：
```
'2026-09-10T15:20:46Z'          > '2026-09-10T15:20:46.731818Z' = True
'2026-09-10T23:20:46+08:00'     > '2026-09-10T15:20:46.731818Z' = True
max(['...46Z', '...46.731818Z']) = 2026-09-10T15:20:46Z
```
A-4/5 数值与 Decimal（对应 CW-14/15、CW-08）：
```
pretty_value: '+1234' -> '1,234'（丢 +）；'-0012345' -> '-0,012,345'；1234567.891 -> '1234567.891'（float 不加千分位）；1e+21 -> '1e+21'
getcontext().prec = 2 后：1234.56+0.44 = 1.2E+3；12345.67*3 = 3.7E+4；999999.99/3 = 3.3E+5
float(None) -> TypeError；float('') -> ValueError；float('1,000') -> ValueError
json 往返：12345678901234567890.9876 -> 1.2345678901234567e+19
```
A-6 `safe_dirname`（对应 CW-24）：`'..' -> '..' escape=True`；`'CON'` 原样；300 字符 key 原样。
A-7 生成代码的语法校验（对应 CW-07）：`key='bad"""key'` → SyntaxError；`key='nl\nkey'` → SyntaxError；`key='x = 1 #'` → 可编译。
A-8 readable 输出（对应 CW-15）：`executedAt` 显示 `2026-09-10 15:20:46`（UTC 未标注）；`参与方[0].公司 = None` 而 `落账明细[0].公司 = 公司#3`（兜底不一致，与 `test_run_recheck/REPORT.md:47`② 独立吻合）。
A-9 索引语义核对：`xlwings 0.36.5` 的 `Sheet.__getitem__` → `self.cells[(row, col)]` → `Range.__getitem__` 一律 `self.row + row`（0 基）⇒ `sht[i,6]` 即 G 列、Excel 行号 `i+1`，与 `shang.py` 的公式写法自洽（`add_book_entries` 的余额链公式经复核**正确**，与 `TEST_REPORT_v2.md:32-34` 实测一致），CW-16 中"最后一行"的定位也据此推算。
A-10 行尾兼容性核对：`Path.write_text` 在 Windows 会写 CRLF，但 `read_text` 是 universal newlines（CRLF→LF），`MARKER_RE` 仍能匹配；本机实测 `upsert_handler_for_key` + `load_handlers` 往返正常（该方向无缺陷）。
