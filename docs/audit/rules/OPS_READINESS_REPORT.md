# Gipfel 商赛系统 · 现场运营就绪度评估报告（OPS READINESS）

> 审计对象：`GipfelBusinessCompetitionManagerWeb`（分支 `bugfix-merged` @ `5cb3468`）
> 审计视角：**这不是代码质量审计**，而是「明天就在中海国际中心七楼、~50–100 人、两天六个财年、学生自带笔记本+共享 Wi-Fi、由学生主席团自己运营」这个真实场景下，系统能不能撑住、哪些流程根本没被实现。
> 规则依据：[`v2.txt`](source/v2.txt)（Update 2.0 场地/时间/纪律）、[`v1.txt`](source/v1.txt)（Update 1.0 学术规则 34 页）、[`v3_full.txt`](source/v3_full.txt)（Update 3.0 参数全文；分析当期只有 `v3.txt` 目录 P1–P5，后由 `V3_PARAMS.md` 补齐）
> 去重声明：本报告**不重复** [`分支代码缺陷审计报告.md`](../分支代码缺陷审计报告.md) 的 366 条代码缺陷目录（已读其 §0.3 最该先修的 12 条）。凡是引用那里已列出的缺陷，只作为「现场后果」的放大系数，不重新定级。
> 证据等级：**【已读码】**（读过相关代码确认机制，未实跑）/ **【已复核】**（读码 + 实跑只读探针/命令并附输出）/ **【未见实现(搜索词: …)】**（全仓搜索无命中——负向结论，附搜索词）

---

## 0. 结论

**结论：以当前状态明天开赛，比赛会在第一个财年就瘫痪——但瘫痪的原因不是崩溃，而是「根本没人在系统里干活」。**

系统是一套**做得很扎实的「管理端」**：比赛准备清单（39 项）、合同 DSL 引擎、股票撮合、审计日志、Excel 建包、归档导入导出、外部记账监听程序——工程完成度远高于一般学生项目。但它被设计成**由主办方/管理员操作的教务系统**，而真实比赛要求**选手在自己的笔记本上完成经营动作**。这两者在权限模型上是冲突的（见 O-01）。

| 级别 | 条数 | 含义 |
| --- | --- | --- |
| **P0** | **5** | 会当场停赛 / 丢数据 |
| **P1** | **10** | 会在学生面前暴露混乱、只能人工兜底 |
| **P2** | **9** | 摩擦，消耗人力与时间 |
| **P3** | **4** | 体验打磨 |
| 合计 | 28 | |

### 0.1 「明天开赛」的硬阻断（任意一条不解决都不该开赛）

1. **选手账号只能看，不能做**（O-01）：全场比赛的字段填报、合同签订都要主席团亲自点；而唯一能填字段的 `SUPER_ADMIN` 全网同时只能登录一台设备。
2. **财年推进 = 两次点击、无原子操作、无锁、定时器还独占全站唯一的执行线程**（O-02）。
3. **系统不产生任何财务报表，也没有任何对账/导出工具**（O-03）——18:30「各代表与审计核对当日账表」与第 2 财年贷款（规则要求提交上一财年资产负债表/利润表/现金流量表）都无凭据。
4. **单进程 daphne + 全部同步视图共用一个线程 + SQLite（无 WAL / 无 busy timeout）**（O-04）——100 台设备在财年边界同时动作 = 大面积超时。
5. **没有任何自动化备份，赛前也没有备份动作；文档指向的 `scripts/backup.sh` 在仓库里不存在**（O-05）——服务器/场地断电即两天数据全丢。

---

## 1. 最严重的 8 个现场失败场景（每条一行）

| # | 场景 | 关联 |
| --- | --- | --- |
| 1 | **14:15 第一财年**：按「4~6 个产业 × 6~8 家公司 ≈ 30~40 家公司、每家约 11 个非计算字段」（参照 `auto_chain_competition.py` 的字段口径）估算，400+ 个产业字段（现金/库存/配额/碳排）必须由**一个**超管账号逐格填写，学生的笔记本打开系统只能看不能改，第一财年 105 分钟的窗口里填不完，后面的财年全部顺延 | O-01 |
| 2 | **16:40 第二财年末**：组委会点「结束财年」→ 全场 100 台设备的 `/auth/me` 心跳（20s 一次）和页面请求全部排在**同一个线程**后面，学生端 15s axios 超时同时弹出几十个「请求超时」；若定时器跑 >120s，nginx 直接 504，但财年已在库里改成 CLOSED——界面说失败、数据说成功 | O-02 / O-04 |
| 3 | **「结束财年」后立刻点「开始新财年」**：若上一个 FY_END 定时器还在跑（前端 15s 就放弃了，服务端还在继续），新财年的 FY_START 定时器**静默返回、永不执行**——所有公司「财年初」重置值一个都没写入，整个财年从错误初值开始，且日志里只有一行 warning | O-02 |
| 4 | **18:30 收尾对账**：审计要核当天的账表，系统里既没有资产负债表/利润表/现金流量表，也没有任何按公司的账目导出；只能打开 `contract_watcher` 的 Excel、或让学生口述——规则要求的一式多份纸质合同与系统记录**没有任何对应关系** | O-03 |
| 5 | **共享 Wi-Fi 抖动 5 秒后恢复**：100 个客户端同时执行「断线重连主动对账」，每个客户端对**每个已加载集合**并发补拉（产业字段还是按公司逐个请求）→ 单线程服务端在几十秒内收到数千请求，期间任何人无法操作 | O-04 |
| 6 | **15:50 第五财年，服务器/笔记本被碰掉电**：没有赛前快照、没有定时备份，可恢复点只有「上次部署时的 `_backup/<时间戳>`」，两天六个财年的数据全部丢失；文档让你照着 `docs/MIGRATION.md` 建 `scripts/backup.sh`，但那个脚本不在仓库里 | O-05 |
| 7 | **午休时运维「修个小 bug」跑了 `update-from-github.sh`**：服务重启 + 版本号变化 → `VERSION.json` 与 `/api/version` 不一致，**全部客户端被版本硬封锁**，学生只能硬刷新（Ctrl+Shift+R）才能继续 | O-11 |
| 8 | **7:30–8:30 签到**：100 名学生用初始口令首次登录，每次登录 = bcrypt cost=12 校验 + 顶号写库 + **强制改密再一次 bcrypt 哈希**，全部串行在同一个线程；同时**同一账号只能一台设备在线**，两人共用账号（或 CFO 与 CEO 各带一台电脑）会互相踢下线 | O-12 / O-06 |

---

## 2. 逐项评估

### 2.1 财年推进 / 时间盒（O-02，P0）

**现状：系统里根本没有「推进财年」这个操作。**

- 财年只是 `fiscal_years` 表里的 `(competition, year, status)` 三列，`ACTIVE`/`CLOSED` 两态 —— 【已读码】[`models.py:26-57`](../../../backend/apps/competitions/models.py)：`FiscalYear` 无「当前财年」唯一约束，`unique_together` 只约束 `(competition, year)`。
- 「推进财年」在前端是**两个互不相关的手工动作**：【已读码】[`CompetitionListView.vue:130-138`](../../../frontend/src/views/competitions/CompetitionListView.vue)（「开始新财年」按钮）+ [`163-174`](../../../frontend/src/views/competitions/CompetitionListView.vue)（行内「结束财年」按钮），对应后端 `POST /api/competitions/:id/fiscal-years` 与 `PATCH /api/competitions/fiscal-years/:id` 两个独立端点。
- 两者之间**没有任何事务、锁、状态机或幂等保护**：
  - 「开始新财年」唯一的守护是前端 `:disabled="hasActiveFiscalYear"`【已读码】[`:134`](../../../frontend/src/views/competitions/CompetitionListView.vue)，而 `hasActiveFiscalYear` 取自**本地缓存的列表**【已读码】[`CompetitionListView.vue:260-262`](../../../frontend/src/views/competitions/CompetitionListView.vue)（`fiscalYears.value.some(f => f.status === "ACTIVE")`）。后端 `FiscalYearCreateView.post` **不检查是否已存在 ACTIVE 财年**【已读码】[`views.py:206-228`](../../../backend/apps/competitions/views.py)。→ 两个超管标签页/两台设备同时点，就会出现**两个 ACTIVE 财年**；前端 `fys.find(f => f.status === "ACTIVE")` 只显示年份较大的那个【已读码】[`stores/competition.ts:127`](../../../frontend/src/stores/competition.ts)，旧的那个永远躺在列表里显示「进行中」。
  - 双击「结束财年」：`prev_status != "CLOSED"` 判定使第二次不再触发定时器【已读码】[`views.py:255-261`](../../../backend/apps/competitions/views.py)，这一条**是安全的**。
- **定时器在请求线程内同步执行**：【已读码】[`views.py:227`](../../../backend/apps/competitions/views.py)（创建财年后）、[`259/261`](../../../backend/apps/competitions/views.py)（状态迁移后）→ `company_fields/timer.py` 会遍历该比赛**全部公司**、逐字段写值、再逐公司级联重算计算字段【已读码】[`timer.py:170-245`](../../../backend/apps/company_fields/timer.py)。HTTP 响应必须等它全部跑完。
- **静默跳过**：`apply_fiscal_year_timer` 用比赛级非阻塞锁，抢不到锁就 **warning + return**，不报错、不回滚、不影响 200 响应【已读码】[`timer.py:153-167`](../../../backend/apps/company_fields/timer.py)。由于 FY_END 与 FY_START **共用同一把锁**（同一个 `_fiscal_locks[competition_id]`），只要上一个财年的结束定时器还在跑，新财年的开始定时器就**永远不会执行**。
- **单公司失败也被吞掉**：`_apply_timer_to_company` 异常只记 warning，继续下一家【已读码】[`timer.py:189-196`](../../../backend/apps/company_fields/timer.py)。→ 界面显示「财年已开始」，实际上部分公司/部分字段没被改写，且**没有任何地方能看到「哪家公司失败」**（只有 `backend/logs/gipfel.log`）。
- **无倒计时/无时间盒 UI、无服务端计时**：【未见实现(搜索词: `countdown` / `倒计时` / `剩余时间` / `celery` / `APScheduler`)] 全仓无任何定时/调度设施（唯一的 `setInterval` 是前端 20s 会话心跳与 5min 版本复核）。时间盒只能靠主持人看表喊。
- **「结束财年」文案与行为不符**：前端提示「此操作不可撤销」【已读码】[`CompetitionListView.vue:461`](../../../frontend/src/views/competitions/CompetitionListView.vue)，但 API 允许把 CLOSED 再 PATCH 成 ACTIVE【已读码】[`views.py:242-261`](../../../backend/apps/competitions/views.py)——而这一次回切**会再触发一次 FY_START 定时器**，把全场字段再重置一遍。

**现场后果**：时间盒是这场比赛的唯一硬约束（`v2.txt` P5：第三财年 8:30–10:15、第四财年 10:20–12:10…），而系统把「换财年」做成了两个需要人手确认、可能静默半成功、且会冻结全站的操作。

**建议（不改代码也能先做）**：赛前写一张「财年推进 SOP 卡」——固定一个人、固定一段串行操作、每次结束财年后**等界面出现「未开启财年」再点开始**、每次点完去 `设置→比赛准备总览` 看字段是否变化；并把 `backend/logs/gipfel.log` 打开在第二块屏上。

---

### 2.2 对账与报表（O-03，P0）

**现状：系统不产生任何会计意义上的报表，一行都没有。**

- 【未见实现(搜索词: `报表` / `对账` / `资产负债表` / `利润表` / `现金流量` / `试算平衡` / `ledger` / `balance_sheet` / `income_statement` / `trial_balance`)]：`backend/apps/` 下**零命中**。命中的全部在 `contract_watcher/`（外部程序）与测试文件里。
- 【未见实现(搜索词: `reportlab` / `weasyprint` / `xhtml2pdf` / `fpdf` / `application/pdf`)]：后端没有任何 PDF/打印能力；唯一的 `Content-Disposition` 出现在 `preparation/views.py:116-122`，导出格式只有 `markdown` / `json` 两种【已读码】[`preparation/views.py:74-78`](../../../backend/apps/preparation/views.py)。
- 有的只是**离散的字段值**：`company_field_values` 把每个产业字段存成 `TEXT` 字符串（`CompanyFieldValue.value`），104 行 / 12 家公司（本库实测）。它没有科目、没有借贷方向、没有期初期末、没有折旧。
- 规则要求的东西全都落在系统之外：
  - `v2.txt` P5 18:30「各代表与审计核对当日账表」
  - `v1.txt` P8「需提供上一财年财务报表（含资产负债表、利润表、现金流量表）」（第 2 财年起贷款的前置条件）
  - `v1.txt` P15-P18 固定资产「直线法按财年计提折旧」、`v1.txt` P28「第三方资产评估机构核算」
  - 【未见实现(搜索词: `评估` / `固定资产` / `折旧` / `appraisal` / `valuation` / `depreciation`)]：`apps/` 下只有「评估前置检查」这一个语义无关的命中（`contracts/engine.py:11`）。

**唯一存在的「账」是外部的、单机的、Windows 专用的**：`contract_watcher/bookkeeping_example/target.xlsx` 是一套 12 张表的账套（银行流水账 / 明细账汇总-资产类 / 负债类 / 损益类 / 资产负债表 / 利润表 / 计算 / 现金流量表 / 常用财务分析表），由 `shang.py` 通过 **xlwings 驱动本机 Excel** 写入【已读码】[`contract_watcher/README.md:257-290`](../../../contract_watcher/README.md)。它有平衡校验 `check()`（资产−负债−所有者权益）。这是一条真实可用的对账路径，但：

- 每家公司**一本** `books/company_<id>.xlsx`，由**一个** watcher 进程**串行**写（单实例锁 `data/watcher.lock`）【已读码】[`contract_watcher/README.md:133-140`](../../../contract_watcher/README.md)；
- 记账只在「达阈值（默认 10 条）/ 财年更迭 / 手动点『立即记账』」时才落 Excel，其余时间只在 `data/watcher.db`【已读码】[`contract_watcher/README.md:59-82`](../../../contract_watcher/README.md)；
- 记账规则要**人手写 Python**（`handlers.py` 里 `handle_<key>_passed`，删掉 `# [auto-default]` 再自己实现）【已读码】[`contract_watcher/README.md:84-108`](../../../contract_watcher/README.md)；
- 它**只覆盖账号 `companyScopes` 范围内的公司**【已读码】[`contract_watcher/README.md:35-57`](../../../contract_watcher/README.md)——一场 4 个产业 + 金融 + 经管的比赛，若用 COMPETITION_ADMIN 账号跑，需要按产业开多个账号 + 多份 watcher 数据目录；若用超管账号跑，会和组委会的浏览器**互相顶号**（见 O-06）。

**现场后果**：审计组拿到的是「学生自己记的 Excel + 一个学生自己写的记账脚本」。系统里的字段值与他们手上的账表没有任何自动比对——规则里「与审计核对账表」这件事，系统帮不上任何忙。

---

### 2.3 赛前准备（P1/P2，无 P0）

**做得好的部分（应当表扬并写进 SOP）**：

- `apps/preparation/` 提供了 9 大类 **39 项**准备清单，每项都带「为什么需要它 / 去哪个页面 / 分几步做」【已读码】[`checklist.py:115-540`](../../../backend/apps/preparation/checklist.py)，并且**明确写出了最容易漏配的地方**（定时器字段、计算图、孤立节点、科技成环、需求指向空产品、账号范围为空导致「登录后什么都看不到」）【已读码】[`checklist.py:181-193`、`488-500`](../../../backend/apps/preparation/checklist.py)。
- 有 dry-run 建包 + 幂等导入 + 归档导出/导入（默认 `dryRun=true`，事务回滚不留痕）【已读码】[`preparation/views.py:20-21`](../../../backend/apps/preparation/views.py)。
- 有 Excel 建包通道（`backend/examples/excel/比赛建包模板.xlsx`、`汽车产业链示例.xlsx`），覆盖 26 类资源，能让「改口径不碰代码」【已读码】[`docs/汽车产业链测试赛准备.md:197-226`](../../汽车产业链测试赛准备.md)。
- 有**可执行的端到端准备 runbook**：`docs/汽车产业链测试赛准备.md:67-93` 是 6 步命令序列（建比赛 → dry-run → 导入 → 覆盖模式回填卡片 id → 合同类型 `--check --trial` → 冒烟），并且记录了导入期的 5 个真实坑【已读码】[`docs/汽车产业链测试赛准备.md:155-177`](../../汽车产业链测试赛准备.md)。

**缺的部分**：

- **`/api/preparations/*` 全部要求 `competition:manage`（超管专属）**【已读码】[`preparation/views.py:16-21`、`42`、`150`、`203`、`233`](../../../backend/apps/preparation/views.py)。清单里 39 项**每一项的落点页面也都是超管专属**（`/competitions` 需 `competition:manage`、`/accounts` 需 `account:manage`、`/industry-types` 需 `industryType:manage`、`/contract-types` 需 `contractType:manage`、`/companies` 写入需 `company:manage`）【已读码】[`frontend/src/router/index.ts:36-258`](../../../frontend/src/router/index.ts)。→ 赛前准备**只能由超管一个人做**，无法把 ①比赛基础 ②行业口径 ③参赛主体 … 八组工作分给八个人并行。
- 清单条目是**静态文案，没有负责人 / 状态 / 时间点 / 勾选留痕**——它是一份可读的检查表，不是可协作的看板【已读码】[`checklist.py:16-41`](../../../backend/apps/preparation/checklist.py)（`PrepItem` 只有 key/分组/标题/是否必需/描述/路由/steps）。
- **真实比赛没有现成的建包数据**：仓库里只有 `demo_competition.py`（通用演示）与 `auto_chain_competition.py`（汽车产业链：原料开采/零部件加工/整车进销），与本次「原料 / 加工 / 经销 / 物流 + 金融公司 / 经济管理中心」并不对应。→ 赛前必须**新写一个 Python 建包脚本或填一张 ~20 个工作表的 Excel**，这件事的负责人、工期、验收标准目前没有任何文档。
- 【未见实现(搜索词: `拍卖` / `竞价` / `价高者得` / `流拍` / `auction`)]：0 命中 → 8:40「开幕式/办公室拍卖」完全在线下（这本身可以接受，但**拍卖结果没有任何录入入口**：`v1.txt` P6 要求「办公室拍卖交易合同一式三份」，系统没有办公室/席位/流拍/候选公司这类模型）。
- 本库实测：`timer_enabled=1` 的产业字段 = **0**、`contract_field_effects` = **0**、`fiscal_years` 只有 `comp=4 year=0` 与 `comp=189 year=2026` 两条【已复核】（探针输出见 §5）。→ **财年定时器改写字段**与**合同执行落账**这两条最关键的运行期路径，在当前库里从未真正跑过。

---

### 2.4 并发与多端（O-04，P0）

**这是最容易被低估、现场后果最直接的一块。**

| 事实 | 证据 |
| --- | --- |
| 后端是**单进程 daphne**，systemd 单元没有任何 worker/进程数参数 | 【已读码】[`deploy/gipfel.service:26-31`](../../../deploy/gipfel.service)（`ExecStart=… daphne -b 127.0.0.1 -p 8000 … backend.asgi:application`） |
| **全部 API 视图都是同步视图**，Django 在 ASGI 下用 `sync_to_async(..., thread_sensitive=True)` 包裹 → **所有同步视图共用一个线程** | 【已读码】`backend/.venv/Lib/site-packages/django/core/handlers/base.py:129`（`return sync_to_async(method, thread_sensitive=True)`）与 `:250`；`django/core/handlers/asgi.py:249-252` 走 `get_response_async` |
| 数据库是默认 SQLite，**没有 `OPTIONS.timeout`、没有 WAL、没有 PRAGMA** | 【已读码】[`settings.py:305-310`](../../../backend/backend/settings.py)；【未见实现(搜索词: `journal_mode` / `PRAGMA` / `wal` / `busy_timeout`)] 后端 0 命中 → 回滚日志模式 + sqlite3 默认 5s busy timeout |
| 每次业务写库都会**再插一行审计**（同一事务、同一线程） | 【已读码】[`common/signals.py:114-125`](../../../backend/apps/common/signals.py) → [`common/audit.py:63-76`](../../../backend/apps/common/audit.py)（`AuditLog.objects.create`） |
| **每一个 4xx/5xx 也插一行审计**（含 401/403/409） | 【已读码】[`common/exceptions.py:43-50`](../../../backend/apps/common/exceptions.py) → `log_exception` |
| 每个客户端每 **20 秒**打一次 `/auth/me` | 【已读码】[`frontend/src/stores/auth.ts:165`、`190`](../../../frontend/src/stores/auth.ts) |
| axios 全局 **15s 超时**；nginx `/api/` `proxy_read_timeout 120s` | 【已读码】[`frontend/src/api/request.ts:39`](../../../frontend/src/api/request.ts)、[`deploy/nginx-gipfel.conf:55`](../../../deploy/nginx-gipfel.conf) |
| nginx 只有**一个** upstream、`max_fails=3 fail_timeout=3s`、**没有任何 `limit_req`/`limit_conn`** | 【已读码】[`deploy/nginx-gipfel.conf:9-13`](../../../deploy/nginx-gipfel.conf)；【未见实现(搜索词: `limit_req` / `limit_conn`)] 0 命中 |
| 断线重连后**对每个已加载集合并发补拉**，产业字段还**按公司逐个**发请求，无并发上限、无单飞 | 【已读码】[`frontend/src/api/request.ts:753-788`](../../../frontend/src/api/request.ts)（`await Promise.all(cols.map(...))`，`/company-fields/${cid}` 在 map 内） |
| 后端无全局 HTTP 限流（README §6 自述） | 【已读码】[`README.md:206`](../../../README.md) |

**量化**：100 个客户端 → 稳态 **5 req/s**（仅心跳）。Wi-Fi 抖动 5 秒后恢复，若每人页面已加载 10 个集合、其中 5 人访问过 20 家公司的详情页 → 一次重连产生 `100×10 + 5×20 ≈ 1100` 个请求，全部涌向**一个线程**；期间任何人的操作都排队，15 秒后前端集体报「请求超时」。

**叠加上 §2.1 的财年定时器**：定时器本身要跑「公司数 × 启用定时器的字段数」次写入 + 每家公司一次计算图重算，全部占用那唯一线程。现场表现是：**主席团点「开始新财年」的那一刻，全场卡住**。

**建议（赛前必须做，不改代码）**：
1. 把财年推进安排在**茶歇/午休**（`v2.txt` 里恰好有 16:00–16:30、12:00–14:00、17:15–17:45 三段空档），而不是整点开赛时刻。
2. 赛前用真实数据做一次压测/演练：`ab`/`wrk` 或直接开 20 个浏览器标签 + 一次财年推进，记录耗时。
3. 若条件允许，**改用 PostgreSQL**（`DATABASE_URL` 已支持切换，README §1 自述 SQLite 默认/PostgreSQL 生产），至少消除写锁争用；并把 nginx 加上 `limit_req` 兜底。

---

### 2.5 断网 / 断线 / 断电与降级（O-05 P0，O-09 P1）

**服务端**：
- 服务自愈依赖 systemd `Restart=always` + `RestartSec=3s`【已读码】[`deploy/gipfel.service:46-49`](../../../deploy/gipfel.service)——进程崩了会自动起，但**没有健康检查/告警**，现场没人会知道它崩过。
- 有正确的 SQLite 一致性快照函数 `snapshot_sqlite_consistent`【已读码】[`scripts/lib/deploy-common.sh:180-181`](../../../scripts/lib/deploy-common.sh)，但它**只被部署/升级脚本调用**：

**备份现状（P0）**：
- 生产环境**没有任何定时备份、没有 systemd timer、没有 cron 条目**。【未见实现(搜索词: `0 3 * * *` / `systemd.timer` / `OnCalendar`)] 仓库中唯一出现「定时备份」的地方是文档示例：【已读码】[`docs/MIGRATION.md:500-520`](../../MIGRATION.md) 教你 `cat > /opt/gipfel/scripts/backup.sh` 手写一个脚本再挂 crontab——但 **`scripts/backup.sh` 在仓库里不存在**（`scripts/` 下只有 bootstrap/start/stop-dev、dev.py、deploy-linux.sh、update-from-github.sh、migrate-server.sh、quick-sync.sh、verify-migration.sh、gen_logviewer_key.py、make_favicon.py、lib/）。
- 而且那段示例用的是 `cp -a` **热拷贝正在被写入的 db.sqlite3**——正是本项目自己在 `deploy-linux.sh:321-330` 里点名批评并已修复的 X-10 反模式。
- `docs/OPS.md` §8 的「备份与恢复」只有一句「停服务 → 用备份覆盖目录 → 重启」，**没有说明备份从哪来、谁在什么时候做**【已读码】[`docs/OPS.md:103-111`](../../OPS.md)。
- **没有「每个财年前自动快照」**。两天六个财年，可恢复点实际上只有「上次部署」。

**客户端（P1）**：
- 前端**没有 Service Worker / PWA / manifest，没有任何 `navigator.onLine` 处理**【未见实现(搜索词: `serviceWorker` / `manifest` / `offline` / `navigator.onLine`)]：0 命中。IndexedDB 只是**读缓存**（按 realm+账号分库），**没有写队列**。
- 断网时：页面上的旧数据还在，任何写操作直接失败并 toast；学生无事可做，也没有「离线模式，请稍后重试」的明确提示。

**场地电源**：`v2.txt` P10 明确「需携带插线板」，说明组织方已预期插座不足。→ 服务器（那台跑 `/opt/gipfel` 的机器）如果放在会场，接线板被踢掉即全场比赛终止，**且没有 UPS、没有第二台热备、没有现场恢复脚本**。

**建议**：
1. 赛前手写一个 `backup.sh`（用 `sqlite3 db.sqlite3 ".backup"` 或直接复用 `snapshot_sqlite_consistent`），**每财年开始前 + 每天 18:30 收尾后**各跑一次，拷到 U 盘 + 另一台机器。
2. 把 `db.sqlite3` 与 `uploads/` 的备份目录打印成一张卡片贴在服务器旁。

---

### 2.6 权限与角色映射（O-01 P0，O-06/O-08/O-10 P1）

**系统只有 3 个角色、39 个权限键**【已复核】（实跑探针，输出见 §5）：
`SUPER_ADMIN`（隐式全权）/ `COMPETITION_ADMIN`（17 个 `*:view` + `contract:manage/audit/execute` + `stock:edit`）/ `PLAYER`（17 个 `*:view`，其中 `stock:view` 允许下单撤单）。

真实比赛的角色在这套 RBAC 里**没有对应账号类型**：

| 真实角色 | 规则要求 | 系统现状 | 严重度 |
| --- | --- | --- | --- |
| **主席团（按产业：原料/加工/经销/物流/金融/经管）** | 每个产业一个主席团，各自管自己产业的合同与公司 | 只有 `COMPETITION_ADMIN`，且**公司范围（companyScopes）只能按公司勾选、没有「产业」维度**；所有比赛管理员权限完全相同，无产业隔离 | P1 |
| **审计** | 与各代表核对当日账表；规则要求审计独立 | 只有 `contract:audit` 这一个动作键（公司范围内改合同编号/审核）。**没有「只读审计」账号**：`COMPETITION_ADMIN` 默认同时持有 `contract:manage` + `contract:execute`（能新建、能落账），**审计与执行没有职责分离**；`GET /api/audit-logs` 要求 `account:manage`（超管专属）【已读码】[`audit/views.py:25`](../../../backend/apps/audit/views.py) → **审计看不到审计日志** | P1 |
| **消费者（市场）** | 「会计年度正式启动前 15 分钟召开市场需求发布会」，由消费者发布当年度各区域需求与价格（`v1.txt` P21） | 需求表 `ConsumerDemand` 是简单 CRUD；读写走 `data:region:view/edit`【已读码】[`consumer_demands/views.py:37-38`](../../../backend/apps/consumer_demands/views.py)，前端编辑按钮 `can("data:region:edit")`【已读码】[`RegionOverviewView.vue:183`](../../../frontend/src/views/regions/RegionOverviewView.vue)。而 `data:region:edit` 属于 `_COMPETITION_ADMIN_EXTRAS`，**必须由超管在请求体里显式写 `allowExtras=true` 才能授予，账号管理界面根本不提供这个开关** | P1 |
| **第三方资产评估机构** | 贷款抵押前对固定资产估值，贷款额 ≤ 评估值 70%（`v1.txt` P8） | 【未见实现(搜索词: `评估` / `固定资产` / `折旧` / `appraisal`)] → **完全没有模型、没有页面、没有角色** | P1 |
| **组委会 / 秘书长 / 会务** | 全场协调、纪律、公示 | 无角色；公告只能发「版本更新公告」，消息中心可群发但不能发公文 | P2 |

**账号管理界面的三个硬限制**（P1，O-10）：
1. 角色下拉**只有三项**：超级管理员 / 管理员 / 参赛选手【已读码】[`AccountManagementView.vue:133-142`](../../../frontend/src/views/account-management/AccountManagementView.vue)。
2. **没有权限勾选框**：权限由 `derivePermissions(role, companies)` **硬编码派生**【已读码】[`AccountManagementView.vue:245-313`](../../../frontend/src/views/account-management/AccountManagementView.vue)。→ 想在界面里给「消费者」账号加 `data:region:edit` 是**做不到的**，只能直接调 API。
3. **界面会静默清掉手工授的权限**：编辑账号保存时固定提交 `permissions: derived.permissions`【已读码】[`AccountManagementView.vue:457-467`](../../../frontend/src/views/account-management/AccountManagementView.vue)；后端 `UserUpdateView.patch` 用「提交值或既有值」做上限校验【已读码】[`users/views.py:129-134`](../../../backend/apps/users/views.py)，提交值在范围内即通过 → 之前用 API 授的扩展权限被覆盖删除。**只要有人事后在界面上「编辑」过一次这个账号，消费者就再也发不了需求，而且没有任何提示。**

**O-01（P0）：选手账号实际上什么都不能做。**
- 写公司产业字段需要 `company:manage`【已读码】[`company_fields/views.py:25-27`、`141`、`167`](../../../backend/apps/company_fields/views.py)，它属于扩展集，**超管之外默认无人持有**；
- 建合同需要 `contract:manage`、执行需要至少 `contract:audit`【已读码】[`contracts/views.py:218`、`297`](../../../backend/apps/contracts/views.py)；
- 所以 `PLAYER` 能做的只有：看数据、看自己公司的合同与字段、`stock:view` 下单撤单、收消息。
- 【已复核】探针实测：`PLAYER` 对 `data:material:edit` / `company:manage` / `contract:manage` / `contract:audit` / `contract:execute` / `message:manage` / `data:region:edit` / `industryType:manage` / `contractType:manage` / `account:manage` / `competition:manage` 全部 **False**（唯一 True 是 `stock:view`）。
- 而 `SUPER_ADMIN` **每次登录都会递增 `token_version` 并强制断开该账号所有旧会话**【已读码】[`auth/views.py:161-168`](../../../backend/apps/auth/views.py) → 组委会再多人也只能有**一台**设备持有超管会话。

**现场结论**：这场比赛实际上会退化成「**一个超管账号在主席台上一家公司一家公司地录数据**」，而学生自己的笔记本只能当看板。**这是本报告发现的最严重、也最难用流程绕过的缺口。**

**绕过方案（不改代码）**：
- 赛前用 API（`POST /api/users` 带 `"allowExtras": true`）为每个产业建一个 `COMPETITION_ADMIN`，并把 `company:manage` 授出去，让主席团能替本产业公司填字段；**但必须立下规矩：不要再用界面「编辑」这些账号**（否则权限被清空）。
- 明确「谁持有超管」：超管只保留 1 台设备，且这台设备只做财年推进 / 账号 / 数据管理，其余操作交给 COMPETITION_ADMIN。

---

### 2.7 现场计时与流程提示（O-07，P1）

- 【未见实现(搜索词: `倒计时` / `countdown` / `剩余时间` / `大屏` / `投屏` / `排行榜` / `leaderboard`)]：**前端 0 命中**。唯一的周期行为是 20s 会话心跳与 5min 版本复核。
- `apps/announcements/` 名字看着像「公告」，实际是**软件版本更新公告**（字段：`version` / `title` / `date` / `content`，默认内容是 v1.4.0 的功能列表）【已读码】[`announcements/models.py:8-19`](../../../backend/apps/announcements/models.py)、[`management/commands/seed_announcements.py:9-31`](../../../backend/apps/announcements/management/commands/seed_announcements.py)。且只有超管能增删改【已读码】[`announcements/views.py:44-46`](../../../backend/apps/announcements/views.py)。
- 唯一能当「广播」用的是消息中心：`POST /api/messages` 支持「全体（可选范围内用户）」+ 在线实时弹窗 `message:new`【已读码】[`messages/views.py:323-403`](../../../backend/apps/messages/views.py)，但需要 `message:manage`（同样是扩展集，**界面授不出去**），收件人上限 500，**没有「已读确认/回执」**，也没有定时重播。
- 实时房间只有 `comp-{id}` 与 `user-{id}` 两种【已读码】[`realtime/gateway.py:156-159`、`193-208`](../../../backend/apps/realtime/gateway.py)：**没有「主席团房间」「审计房间」「大屏房间」**，无法定向广播，也没有任何「全体静默/统一看表」的机制。
- 没有大屏模式：`DashboardView` 是可配置的小组件看板，不是投影用的排行榜/倒计时；`v2.txt` P6 的「闭幕式颁发各产业奖项」在系统里没有任何评分/排名功能支撑（【未见实现(搜索词: `评分准则` / `实体企业评分` / `leaderboard`)] 0 命中，`code_audit/_rules/_gapkw.txt:161-171` 同结论）。

**现场后果**：6 个财年 × 每财年 1.5–2 小时的时间盒，完全靠主持人拿话筒喊 + 学生自己看表；「市场需求发布会」「二次选址顺序」「公示」都没有载体。

**建议**：另配一块投影屏跑一个最简单的倒计时网页（甚至是手机秒表投屏），并规定「每个财年由主持人用消息中心群发 3 条提示（开始 / 剩余 30 分钟 / 结束）」。

---

### 2.8 数据核验与防作弊（P1）

`v2.txt` P10：「参赛人员应秉持诚信原则…不得有任何作弊行为（如数据造假、恶意竞争等），一经发现，立即取消参会资格并进行公示。」

**能用的（要点赞）**：
- 合同执行**是原子且留痕的**：先原子抢占 `EXECUTED` 再落账，重复执行返回 400；执行完显式补写审计（含落账字段摘要，最多 50 项）【已读码】[`contracts/views.py:332-406`](../../../backend/apps/contracts/views.py)。
- 合同每次落账会写一行不可变的 `ContractFieldEffect`（`op / value_raw / before_raw / after_raw`）【已读码】[`contracts/models.py:85-115`](../../../backend/apps/contracts/models.py)，可事件溯源复原。
- 股票推进轮次有进程内锁 + 409 + 整轮事务【已读码】[`stock/engine.py:1805-1826`、`1861-1863`](../../../backend/apps/stock/engine.py)。
- `contract_watcher` 能对服务器只表现为一个只读账号，把**每一份通过合同**翻译成中文可读记录（`records/<key>/contract_<id>_..._readable.json`，含参与方/填写内容/前置检查/落账明细）【已读码】[`contract_watcher/README.md:227-232`](../../../contract_watcher/README.md)——**这是本系统里唯一真正好用的「赛后取证 / 对账底稿」工具**。

**不能用的（缺口）**：

1. **公司产业字段值的改动完全不落审计**（P1，O-13）。`MODEL_TO_RESOURCE["CompanyFieldValue"] = None`【已读码】[`realtime/emit.py:149`](../../../backend/apps/realtime/emit.py)，而信号连接会**跳过映射值为 `None` 的模型**【已读码】[`common/signals.py:198-202`](../../../backend/apps/common/signals.py)、README §6 同样自述。→ 也就是**现金、库存、配额、碳排这些真正会被「数据造假」的数字，谁在什么时候从 X 改成 Y，系统里查不到**。`ContractFieldEffect` 同样为 `None`，虽然它另有表存证。
2. **审计日志只有超管能看，且没有按公司/记录/时间段的查询**【已读码】[`audit/views.py:20-56`](../../../backend/apps/audit/views.py)（只支持 `kind/model/operatorId/competitionId` 四个过滤）；前端页面 `pageSize=20`【已读码】[`AuditLogView.vue:166`](../../../frontend/src/views/system/AuditLogView.vue)。→ 「查一下 A 公司今天的现金变动」这件事**做不到**。
3. **审计日志只增不删**：本库已积累 `7118` 行（`write 7057 / error 61`）【已复核】。两天 100 客户端会把这张表推到几万行，没有清理/归档命令；`AuditLogListView` 每次都要 `qs.count()`。
4. **「数据造假」的检测只能靠人对纸质合同**：纸质合同与系统记录唯一的连接点是各参与方手填的 `contractNumber`【已读码】[`contracts/views.py:411-460`](../../../backend/apps/contracts/views.py)——而没有任何校验、没有唯一性约束、没有纸质模板。
5. **编号补全的范围校验对比赛管理员是失效的**：`_assert_edit_party_scope` 在持有 `contract:execute` 或 `contract:manage` 时**直接放行**【已读码】[`contracts/views.py:896-911`](../../../backend/apps/contracts/views.py)——每个主席团都是这样的账号，于是**任何一个主席团成员都可以替别家公司的合同填编号**（本应「各公司自填自己那份」）。

**`contract_watcher` 能否现场用？**【已读码】结论：**能用，但有前提，且有一个致命冲突**。
- 前提：Windows + 已安装 Excel + `pip install xlwings`；每个被记账公司一本 xlsx；单实例锁保证只有一个进程写 Excel【已读码】[`contract_watcher/README.md:133-140`](../../../contract_watcher/README.md)。
- 冲突：它启动时会**登录**，而**登录即递增 `token_version` 并踢掉同账号的所有旧会话**【已读码】[`auth/views.py:161-168`](../../../backend/apps/auth/views.py)；`contract_watcher.py:607-614` 在 401 时会**自动重新登录**【已读码】[`contract_watcher/contract_watcher.py:607-614`](../../../contract_watcher/contract_watcher.py)。→ 如果 watcher 和组委会浏览器用同一个账号，双方会**互相顶号、无限循环**（每轮一次 bcrypt + 一次写库）。**必须给 watcher 一个专属账号，且该账号不与任何人的浏览器共用。**

---

### 2.9 线下交付物：合同 / 打印 / U 盘（O-14，P1）

规则反复要求纸质合同：办公室拍卖「一式三份」、生产资料购买「一式三份」、原料购销「一式四份」、三方物流「一式六份」、借款合同「一式两份」、股权投资协议「一式四份」、定向拨款协议「一式四份」（`v1.txt` P6/P8/P15/P16/P20/P24/P26）。

现状：
- **系统不打印、不导出、不生成任何合同文档**。【未见实现(搜索词: `window.print` / `打印` / `jsPDF` / `html2canvas` / `exceljs` / `xlsx`)]：前端 0 命中（唯一的 `MapsManager.vue:1926` 是地图画布导出图片）。后端唯一的附件下载是准备归档（markdown/JSON）【已读码】[`preparation/views.py:74-78`](../../../backend/apps/preparation/views.py)。
- 系统里只有「合同实例」（参与方 + 输入项 + 效果），没有可签署的**合同正文**；`signed_at` 是点击执行时由服务端自动打的时间戳【已读码】[`contracts/views.py:330`](../../../backend/apps/contracts/views.py)，与纸上签字无关。
- 【未见实现(搜索词: `一式三份` / `一式四份` / `一式六份` / `copies` / `counterpart`)]：0 命中（`code_audit/_rules/_gapkw.txt:183-190` 同结论）。

**现场后果**：「各部门签署一式多份纸质合同」这条纪律要求与系统之间**只有手抄编号这一条细线**。赛前必须准备：各合同类型的纸质模板（份数不同）、编号规则、以及「谁在系统里录入编号」的分工。**这三件东西目前一件都没有。**

**U 盘**：系统没有任何「把系统数据带到 U 盘」的能力（无 Excel/CSV 导出）；学生只能用 U 盘互相拷自己的 Excel 账本。组委会无法在闭幕式前用系统导出一份「各公司最终经营数据」用于颁奖——`preparation` 的 markdown 归档勉强可以，但它只含**准备数据**与明细，且需要超管权限。

---

### 2.10 人力与培训（P1/P2）

**管理面复杂度（决定培训成本）**：
- 前端 **24 条业务路由**（数据管理 10 个资源页 + 区域总览 + 产业类型 + 合同类型 + 合同 + 比赛 + 账号 + 公司 + 公司详情 + 股票行情 + 股票管理 + 消息 + 审计日志 + 设置 + 仪表盘）【已读码】[`frontend/src/router/index.ts:30-258`](../../../frontend/src/router/index.ts)。
- 产业类型管理页一个页面就要处理：字段 CRUD、字段类型（含 DICTIONARY/LIST）、计算字段与**计算图编辑器**、财年定时器（触发时机 + 常量/引用字段两种取值模式）【已读码】[`IndustryTypeManageView.vue:360-440`](../../../frontend/src/views/data-management/IndustryTypeManageView.vue)。
- 合同类型要用**可视化节点编辑器**搭 DSL（参与方 / 输入项 / 条件 / 效果 / 变量 / 聚合端点），文件 `ContractTypeGraphEditor.vue` + `graph-model.ts` + 后端 `builder/` 三套概念【已读码】[`docs/合同可视化新建操作指南.md`](../../合同可视化新建操作指南.md)。
- 比赛准备清单 39 项，其中 8 项标 `required=True`。

**结论**：
- **不可能现场培训**。至少要提前一周做一次**全流程彩排**（建包 → 导入 → 合同试算 → 冒烟 → 走完一个财年 → 推进财年 → 对账），并留下操作卡片。
- **最小人员配置（保守）**：1 名「超管操作员」（唯一持有超管会话，负责财年推进/账号/数据管理，**不能中途换人换设备**）+ 每产业 1 名 `COMPETITION_ADMIN`（填字段、建合同、执行落账，需赛前通过 API 授 `company:manage`）+ 1 名记账/审计（跑 `contract_watcher`，**必须用独立账号**）+ 1 名主持人（时间盒与广播）+ 1 名网络/电源保障。**≈ 9–12 人**（4 个产业 + 金融 + 经管 ≈ 6 个主席团）。
- **任何一人缺席的后果**：
  - 超管缺席 = **看不到「比赛管理」页，没人能推进财年**（按钮 `v-if="isSuperAdmin"`），比赛直接停摆，且没有 B 角可以接手（换设备登录会把原设备踢下线，需要重新走一遍强制改密/交接口令）。
  - 记账员缺席 = 当天的账表没有底稿，18:30 收尾无法核对。
  - 产业主席团缺席 = 该产业的字段/合同无人能写（该账号的 `companyScopes` 只覆盖本产业公司）。

---

## 3. 文档化流程：有 vs 没有

| 流程 | 文档 | 状态 |
| --- | --- | --- |
| 首次部署（Linux + nginx + systemd） | `scripts/deploy-linux.sh`、`deploy/README.md` | ✅ 很完整（含 521/403/400 等真实事故复盘） |
| 增量升级 / 回滚 | `scripts/update-from-github.sh`、`docs/OPS.md` §11、`deploy/README.md` | ✅ 完整，含失败陷阱说明 |
| 健康检查 | `docs/OPS.md` §5 | ✅ 有 5 条 curl |
| 日常运维命令 / 故障 FAQ | `docs/OPS.md` §3/§6/§9（Q1–Q12） | ✅ 详尽 |
| 日志查看器 / 后台防直连 | `docs/OPS.md` §7/§10、README §7 | ✅ |
| 比赛准备清单（39 项） | `apps/preparation/checklist.py` + 前端准备总览 | ✅ 内容质量高，但**只读、只对超管、无责任人/勾选** |
| 建包（代码 / Excel） | `docs/BUILD_COMPETITION_BY_CODE.md`、`docs/比赛Excel建包教程.md`、`docs/比赛Excel建包规范.md`、`docs/汽车产业链测试赛准备.md` | ✅ 有教程与可跑示例；❌ **没有本次比赛（原料/加工/经销/物流+金融+经管）的建包数据与负责人** |
| 合同类型可视化搭建 | `docs/合同可视化新建操作指南.md`、`docs/CONTRACT_TYPE_BY_CODE.md` | ✅ |
| 服务器迁移 | `docs/MIGRATION.md` + 3 个脚本 | ✅ |
| **赛前备份 / 每财年快照** | `docs/OPS.md` §8（一句话）、`docs/MIGRATION.md` §7.3（要你手写 `scripts/backup.sh`，而该脚本不存在） | ❌ **无自动化、无赛前动作、示例脚本缺失且用热拷贝** |
| **现场应急预案（断电/断网/服务挂/数据错）** | — | ❌ **完全没有** |
| **财年推进 SOP** | `checklist.py:142-155` 只有「开始新财年/结束财年」的按钮说明 | ❌ 无时序、无责任人、无校验点 |
| **对账 / 报表流程** | — | ❌ 系统无能力，文档也未说明「用外部 Excel 兜底」的正式流程 |
| **角色到账号的映射表（谁用哪个账号）** | `checklist.py:474-500` 只有「三类角色」说明 | ❌ 无「主席团/审计/消费者/评估机构 → 具体账号名」的对照表 |
| **计时/广播/大屏** | — | ❌ |
| **纸质合同模板与编号规则** | — | ❌ |
| **赛后归档与颁奖数据导出** | `preparation` 导出 markdown/JSON（超管） | ⚠️ 勉强可用，但不是奖项数据 |
| **真机（生产服务器）验证** | `WSL与生产环境验证结果记录.md` §4 | ❌ **明确写着「尚未执行」**，11 项待验证 |
| **负载/压力测试记录** | — | ❌ 无 |

---

## 4. 缺陷清单（按级别，共 28 条）

### P0（5）

| ID | 缺陷 | 证据 |
| --- | --- | --- |
| **O-01** | 选手账号无任何写权限（字段/合同/需求/消息全部不可写），全场业务动作必须由 staff 代做；唯一能填字段的 `SUPER_ADMIN` 又是单会话 | 【已复核】权限探针见 §5；【已读码】`company_fields/views.py:141,167`、`contracts/views.py:218,297`、`auth/views.py:161-168`、`AccountManagementView.vue:296-312` |
| **O-02** | 「推进财年」不是原子操作（两次点击/两个端点），无服务端 ACTIVE 唯一性校验，定时器在请求线程内同步执行且**抢不到锁就静默跳过**、单公司失败静默吞掉、无回滚 | 【已读码】`competitions/views.py:206-262`、`company_fields/timer.py:147-196,239-245`、`CompetitionListView.vue:130-174,437-473` |
| **O-03** | 无任何财务报表/对账/打印/Excel 导出能力；规则要求的资产负债表/利润表/现金流量表在系统中不存在 | 【未见实现(搜索词: `报表`/`对账`/`资产负债表`/`income_statement`/`reportlab`/`application/pdf`)] |
| **O-04** | 单进程 daphne + 全部同步视图共用一个线程 + SQLite（无 WAL/无 busy timeout）+ 每次写多一条审计 INSERT + 无任何限流 → 财年边界与断线重连风暴下大面积超时/锁等待 | 【已读码】`deploy/gipfel.service:26-31`、`django/core/handlers/base.py:129,250`、`backend/settings.py:305-310`、`common/signals.py:114-125`、`deploy/nginx-gipfel.conf:9-13`、`frontend/src/api/request.ts:39,753-788` |
| **O-05** | 无定时备份、无赛前/每财年快照；文档指向的 `scripts/backup.sh` 不存在，且示例用热拷贝活库 | 【已读码】`docs/MIGRATION.md:500-520`、【未见实现(搜索词: `OnCalendar`/`crontab` 定时备份)]、`docs/OPS.md:103-111` |

### P1（10）

| ID | 缺陷 | 证据 |
| --- | --- | --- |
| **O-06** | 超管单会话（每次登录顶号并断开旧连接）→ 两人无法同时操作；`contract_watcher` 与浏览器抢同一账号会互踢循环 | 【已读码】`auth/views.py:161-168`、`contract_watcher/contract_watcher.py:607-614` |
| **O-07** | 无倒计时/时间盒 UI、无服务端计时、无大屏/排行榜、无定向广播房间；`announcements` 只是版本更新公告 | 【未见实现(搜索词: `倒计时`/`countdown`/`大屏`/`leaderboard`)]、`announcements/models.py:8-19`、`realtime/gateway.py:156-159` |
| **O-08** | 真实角色无账号类型：无产业维度隔离、无「只读审计」、审计看不到审计日志（`/audit-logs` 需超管）、无资产评估机构模型 | 【已读码】`common/permissions.py:298-366`、`audit/views.py:25`、`RegionOverviewView.vue:183`、`consumer_demands/views.py:37-38` |
| **O-09** | 客户端无离线/降级（无 SW/manifest/写队列），断网即全部写操作失败，学生只能干等 | 【未见实现(搜索词: `serviceWorker`/`manifest`/`offline`/`navigator.onLine`)] |
| **O-10** | 账号界面只有 3 个角色、无权限勾选、`derivePermissions` 硬编码；且界面保存会**静默清掉**手工授予的扩展权限（`data:region:edit` 等） | 【已读码】`AccountManagementView.vue:133-142,245-313,457-467`、`users/views.py:36-53,129-134` |
| **O-11** | 版本硬封锁：任何后端升级/热修都会让 `VERSION.json ≠ /api/version` → 全客户端封锁；`index.html` 也没有显式 no-cache | 【已读码】`frontend/src/stores/version.ts:30-38`、`frontend/src/App.vue:20-27`、`deploy/nginx-gipfel.conf:154-156` |
| **O-12** | 签到高峰：100 人 × (bcrypt cost=12 登录 + 强制改密再一次哈希 + 顶号写库) 全部串行在单线程；强制改密期间业务接口 401，学生进不了系统 | 【已读码】`users/models.py:81-85`、`auth/views.py:161-168`、`README.md:223` |
| **O-13** | 公司字段值变更与合同落账明细**不进审计**（`MODEL_TO_RESOURCE=None` 被信号跳过）；审计日志只增不删、无按公司/时间过滤、仅超管可见 → 无法事后举证「数据造假」 | 【已读码】`realtime/emit.py:149,152`、`common/signals.py:198-202`、`audit/views.py:20-56`、`AuditLogView.vue:166` |
| **O-14** | 纸质合同与系统记录脱节：无打印/PDF/导出文档，无份数（一式 N 份）概念，编号可被任意比赛管理员越范围填写 | 【未见实现(搜索词: `window.print`/`jsPDF`/`一式四份`/`copies`)]、`contracts/views.py:411-460,896-911` |
| **O-15** | 生产真机从未验证（文档自述 11 项待验证）；两条最关键的运行期路径在本库从未跑过（`timer_enabled` 字段=0、`contract_field_effects`=0）；无压测记录 | 【已复核】探针输出见 §5；【已读码】`WSL与生产环境验证结果记录.md:118-142` |

### P2（9）

| ID | 缺陷 | 证据 |
| --- | --- | --- |
| **O-16** | 财年编号两套口径：界面建的首个财年是「第 0 财年」，Excel/建包建的是 2026 → 与公告的「AI 财年 / 第一~第六财年」对不上 | 【已复核】DB：`comp=4 year=0`、`comp=189 year=2026`；【已读码】`CompetitionListView.vue:440-444`、`stores/competition.ts:129` |
| **O-17** | 「结束财年」提示「不可撤销」，但 API 允许改回 ACTIVE，且回切会再触发一次 FY_START 定时器（全场字段再被重置） | 【已读码】`CompetitionListView.vue:461`、`competitions/views.py:242-261` |
| **O-18** | 断线重连对账无并发上限、无单飞、产业字段按公司逐个请求（每人最多 = 已访问公司数 个请求） | 【已读码】`frontend/src/api/request.ts:753-788` |
| **O-19** | 登录限流只覆盖 `/api/auth/login`，键为 (IP+用户名)，共享 Wi-Fi 下同一学生失败 10 次锁 15 分钟；锁定存进程内存，重启即清空 | 【已读码】`common/middleware.py`、`README.md:206`、`docs/OPS.md:158-160` |
| **O-20** | `contract_watcher` 现场门槛高：Windows + Excel + xlwings，每家一本 xlsx，单实例，只能覆盖一个账号的公司范围，记账规则要手写 Python | 【已读码】`contract_watcher/README.md:35-57,110-140,257-290` |
| **O-21** | 无任何业务数据的 Excel/CSV 导出（只有准备归档 markdown/JSON，且需超管）→ 闭幕式颁奖数据、赛后归档都要手抄 | 【已读码】`preparation/views.py:74-78,167-214` |
| **O-22** | 比赛准备清单只读且全部要超管权限，无负责人/状态/时间点，无法把 39 项分派给多人并行 | 【已读码】`preparation/views.py:150,203,233`、`preparation/checklist.py:16-41` |
| **O-23** | 消息中心可当广播但需 `message:manage`（扩展集，界面授不出去），且无「已读确认」、无定时重播、收件人上限 500 | 【已读码】`messages/views.py:323-403`、`common/permissions.py:326-332` |
| **O-24** | 列表默认 `pageSize=50`、审计页 20 条/页；合同列表对非超管还要把整表拉进内存做范围过滤（`views.py:205-216`）→ 大比赛翻页慢 | 【已读码】`common/pagination.py:9-11`、`AuditLogView.vue:166`、`contracts/views.py:205-216` |

### P3（4）

| ID | 缺陷 | 证据 |
| --- | --- | --- |
| **O-25** | 两次点击之间全场顶部栏短暂显示「未开启财年」 | 【已读码】`CompetitionListView.vue:466` |
| **O-26** | `index.html` 无 `Cache-Control: no-cache`，配合版本硬封锁有「刷了还是旧版本」的排查成本 | 【已读码】`deploy/nginx-gipfel.conf:154-156` |
| **O-27** | 无赛前「一键自检」入口（准备总览要超管进设置页点三轮：`SettingsView.vue:31`） | 【已读码】`SettingsView.vue:29-31` |
| **O-28** | 「未开启财年」等文案无「请等待主席团推进」的操作指引；学生面对 401/超时只有统一 toast | 【已读码】`TopBar.vue:33-35` |

---

## 5. 复核证据（本次实跑的只读探针与命令）

> 全部为只读：不写业务库、不起服务、不改任何文件（除本报告）。

**E-1 角色能力矩阵**（`has_permission` 纯函数探针，实跑）
```
== SUPER_ADMIN ==     全部 True（含 competition:manage / account:manage / stock:manage）
== COMPETITION_ADMIN ==  contract:manage/audit/execute=True, stock:view/edit=True
                        company:manage=False, data:*:edit=False, message:manage=False,
                        data:region:edit=False, industryType:manage=False,
                        contractType:manage=False, account:manage=False, competition:manage=False
== PLAYER ==           仅 stock:view=True（其余全部 False）
有效权限键总数: 39
```
支撑 O-01 / O-08 / O-10。

**E-2 数据库规模与状态**（Django 只读 ORM 查询，`backend/db.sqlite3`）
```
competitions: 4        （test / 分支排查 / 路线排查 / 2026 汽车产业链测试赛）
users: 6               （SUPER_ADMIN 1 / COMPETITION_ADMIN 1 / PLAYER 4）
companies: 12          company_field_values: 104
fiscal_years: 2        （comp=4 year=0 ACTIVE；comp=189 year=2026 ACTIVE）
industry_fields: 55    timer_enabled 字段: 0
contracts: 3           contract_field_effects: 0
audit_log: 7118        （write 7057 / error 61）
```
支撑 O-15（关键运行路径从未真正落库）、O-13（审计增长）、O-16（两套财年编号）。

**E-3 负向搜索（全仓）**
| 搜索词 | 命中 | 结论 |
| --- | --- | --- |
| `报表` / `对账` / `资产负债表` / `利润表` / `现金流量` / `试算平衡` / `ledger` / `balance_sheet` / `income_statement` / `trial_balance` | `apps/` 0 | 无会计能力（O-03） |
| `reportlab` / `weasyprint` / `xhtml2pdf` / `fpdf` / `application/pdf` | 0 | 无 PDF（O-03/O-14） |
| `window.print` / `打印` / `jsPDF` / `html2canvas` / `exceljs` / `xlsx` | 前端 0 | 无打印/导出（O-14/O-21） |
| `倒计时` / `countdown` / `剩余时间` | 0 | 无计时器（O-07） |
| `大屏` / `投屏` / `排行榜` / `leaderboard` / `scoreboard` / `kiosk` | 0 | 无大屏/排名（O-07） |
| `celery` / `APScheduler` / `OnCalendar` | 0 | 无调度设施（O-02/O-05） |
| `serviceWorker` / `manifest` / `offline` / `navigator.onLine` | 前端 0 | 无离线降级（O-09） |
| `journal_mode` / `PRAGMA` / `wal` / `busy_timeout` | 后端 0 | SQLite 未调优（O-04） |
| `limit_req` / `limit_conn`（nginx） | 0 | 无反代限流（O-04） |
| `评估机构` / `appraisal` / `valuation` / `固定资产` / `折旧` | `apps/` 0 | 无资产评估（O-03/O-08） |
| `一式三份` / `一式四份` / `一式六份` / `copies` / `counterpart` | 0 | 无份数概念（O-14） |

**E-4 框架层证据**
- `django/core/handlers/base.py:129` → `return sync_to_async(method, thread_sensitive=True)`；`:250` 同样。→ ASGI 下全部同步视图共用**一个**线程（O-04）。
- `django/core/handlers/asgi.py:249-252` → `run_get_response` 走 `get_response_async`。

**E-5 未执行（本报告的能力边界）**
- 没有跑任何负载测试、没有起 dev/prod 服务、没有执行任何写操作（含财年定时器与合同执行）。
- **财年定时器的实际耗时未实测**（需要写库）。本报告对「阻塞时长」的判断是**结构性推断**（同步调用 + 单线程 + 全公司×全字段写入 + 逐公司计算图重算），不是实测值。**赛前必须在真实数据上实测一次**，这本身就是 O-15 的一部分。

---

## 6. 赛前最小可执行清单（不改代码）

### T-7 天
1. **写一个真的 `scripts/backup.sh`**（用 `sqlite3 db.sqlite3 ".backup '<目标>'"` 或复用 `scripts/lib/deploy-common.sh:180` 的 `snapshot_sqlite_consistent`），验证能恢复；把备份拷到第二台机器 + U 盘。→ 解 O-05。
2. **真机部署一次并走完 `WSL与生产环境验证结果手册` §4 的 11 项**；记录 HTTPS/端口/ALLOWED_HOSTS 的实际结果。→ 解 O-15。
3. **写本次比赛的建包数据**（原料/加工/经销/物流 + 金融公司 + 经济管理中心），用 `manage.py build_competition --dry-run` + `build_contract_types --check --trial` + 冒烟脚本验收。→ 解 §2.3 的「没有建包数据」。
4. **在真实数据上实测一次财年推进**：记录 `timer_enabled` 字段数与耗时；若超过 10 秒，把推进安排在茶歇段。→ 解 O-02/O-04 的未知量。
5. **写角色映射表**：每个真实角色 → 具体账号名 → 角色 → `companyScopes` → 权限集合。用 API 建账号（`allowExtras: true` 授 `company:manage` / `data:region:edit`），并**在表上打印「不要在界面上编辑这些账号」**。→ 解 O-01/O-08/O-10。

### T-1 天
6. 全流程彩排（**至少一次完整财年**）：建合同 → 填编号 → 执行落账 → 看字段变化 → 推进财年 → 确认「财年初」定时器生效 → 导出准备归档。
7. 起 `contract_watcher`（**独立账号**），跑通一家公司的记账 + `check()` 平衡校验 → 确认现场"当天账表"有底稿。→ 解 O-03 的兜底路径。
8. 准备纸质合同模板（按合同类型分份数）+ 编号规则 + 录入分工。
9. 打印现场卡片：**财年推进 SOP**、**备份命令**、**应急联系/回滚命令**、**账号口令交接表**。

### T-0（开赛当天）
10. 开赛前立刻做一次 DB 快照；**每个财年开始前再做一次**（6 次，每次 30 秒）。
11. 超管设备固定一台、接 UPS/固定插座；**午休不升级、不重启**（→ 避免 O-11）。
12. 第二个屏幕打开 `backend/logs/gipfel.log`（`tail -f`），盯 `财年定时器` / `审计写入失败` / `database is locked` 三类日志。
13. 每个财年用消息中心发 3 条提示（开始 / 剩余 30 分钟 / 结束），并人工投影一个倒计时。

### 应急卡（一页纸）
| 症状 | 现场动作 |
| --- | --- |
| 全场页面转圈 / 大量「请求超时」 | 别点第二次！等 1–2 分钟；确认不是有人正在推进财年；看 access log 是否有长请求 |
| `database is locked` | 停止一切写操作 60 秒；关掉多余标签页；必要时 `systemctl restart gipfel`（3 秒自愈） |
| 财年推进后字段没变 | 看日志是否有「财年定时器：…已在执行中，跳过本次并发触发」→ 等上一轮结束后**重新 PATCH 一次状态**（先 CLOSED 再 ACTIVE 可重触发，但会重置全场字段，慎用） |
| 服务起不来 | 恢复最近一次 DB 快照 → `systemctl restart gipfel` → 健康检查 `curl /api/health` |
| 出现两个「进行中」财年 | 立刻把年份小的那个 PATCH 成 CLOSED（**不要**点「结束财年」两次） |
| 怀疑选手改数 | 系统查不到字段变更（O-13）→ 只能靠 `contract_watcher` 的 `records/*_readable.json` 与纸质合同比对；当场以纸质合同为准 |

---

## 7. 一句话总结

这套系统的**工程完成度远高于运营就绪度**：建包、合同引擎、股票、审计、准备清单、外部记账程序都是真材实料，但**没有任何一条「让 100 个学生在共享 Wi-Fi 下、由学生主席团自己推进六个财年、并在每天收尾时对上账」的路径被设计过，也没有被演练过**。明天开赛，最先崩的不是代码，是「谁来点、点什么、点完怎么确认成功」。
