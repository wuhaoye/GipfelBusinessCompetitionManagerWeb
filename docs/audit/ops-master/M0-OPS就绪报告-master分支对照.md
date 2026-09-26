# M0 · 《现场运营就绪度评估报告（OPS READINESS）》28 条缺陷在 master 分支的现状对照

> **对照对象**
> - 原件：`OPS_READINESS_REPORT.md`（附件，440 行 / 28 条）——编写基线 **`bugfix-merged @ 5cb3468`**
> - 目标：**`master @ 97e117e8781e0c02462a24269a165dde08ecef44`**
> - 证据：master 逐字节快照（`git archive -c core.autocrlf=false`，LF，CR=0）＋ `git show master:<path>`；所有行号均为 **master 行号**，原件行号一律标注「(原件)」。
> - 约束：**只读**，未修改任何代码/配置；`git status --porcelain` 与本次工作开始前一致。

## 0. 一句话结论

**28 条中没有一条在 master 上被修复。** 20 条原样存在（其中 7 条比原件描述**更重**）、3 条形态不同（2 条更重、1 条原件核心机制被**证伪**）、5 条因 master 缺少对应功能而「无法命中」——但其中 4 条是**能力比 bugfix-merged 更缺**（不是问题消失了），只有 1 条属于「因此不存在这个问题」。

原因很直接：这份报告是为 **bugfix-merged** 写的运营就绪度评估，而 master 比它少 357 个文件 / 62k 行，**原件赖以兜底的三个关键能力在 master 上整体不存在**——`backend/apps/preparation/`（39 项准备清单／自检／归档导出）、`contract_watcher/`（唯一的外部记账与取证底稿）、`scripts/lib/deploy-common.sh`（SQLite 一致性快照函数）。

**对 master 而言最要命的一条（比原件更重）**：原件给出的「赛前用 API 给主席团账号授 `company:manage`，让主席团替公司填字段」这个绕过方案，**在 master 上根本无法执行**——master 没有 `allowExtras` 通道，扩展权限集内的 5 个权限谁都授不出去；而唯一能填字段的 `SUPER_ADMIN` 又是**单会话**。两端叠加的结果是：master 上全场数据录入只能由**一台设备上的一个超管账号**完成（可行的替代是建多个 SUPER_ADMIN 账号，代价是零权限隔离）。

## 1. 结果总览（A/B/C/D）

| 分类 | 条数 | 编号 |
| --- | --- | --- |
| **A 仍存在**（机制一致，仅行号/位置不同） | **20** | O-02、O-03、O-05、O-06、O-07、O-08、O-09、O-11、O-12、O-13、O-14、O-15、O-17、O-18、O-19、O-23、O-24、O-25、O-26、O-28 |
| **B 形态/严重度不同** | **3** | O-01（更重）、O-04（机制被证伪）、O-10（部分不可达） |
| **C master 上无法命中** | **5** | O-16（「没有这个问题」）、O-20、O-21、O-22、O-27（均为「能力更缺」） |
| **D 已在 master 修复** | **0** | — |

按原件级别：P0 5 条 **全部成立**（O-01 更重、O-02 A、O-03 A、O-04 B、O-05 A 更重）；P1 10 条 **全部成立**（O-15 A 更重）；P2 9 条中 6 条成立、3 条能力缺失；P3 4 条中 3 条成立、1 条能力缺失。

## 2. 逐条对照表

| ID | 原件级别 | master 状态 | master 关键证据 | 一句话差异 |
| --- | --- | --- | --- | --- |
| **O-01** | P0 | **B（更重）** | `permissions.py:342-347`（COMPETITION_ADMIN 默认无 `company:manage`）、`:361-363`（PLAYER 上限=17 个 view，零写权限）、`:388-401`（扩展集也算 violation，**全仓 `allowExtras` 0 命中**）、`auth/views.py:156-162`（登录即踢旧会话） | 原件「选手不能写」成立；但原件推荐的 API 授 `company:manage` 绕过方案在 master 上**必然 400**，只剩超管一条路 |
| **O-02** | P0 | **A** | `competitions/models.py:41`（唯一约束只有 `(competition, year)`）、`views.py:192-193`（创建不校验已存在 ACTIVE）、`:202/:234-236`（定时器在请求内同步调用）、`timer.py:159-163`（抢不到锁静默 return）、`:189-196`（单公司异常只 warning）、`:239-242`（逐公司小事务无回滚） | 完全同构；master 特有后果：同年份并发双击撞 DB UNIQUE → **500 + error 审计行**（而非 409） |
| **O-03** | P0 | **A（更彻底）** | 会计语义（报表/对账/资产负债表/利润表/现金流量/ledger/balance_sheet/income_statement）在 `backend/` 0 命中；`reportlab/weasyprint/xhtml2pdf/fpdf/application/pdf` 0 命中；`Content-Disposition` 全仓 0；40 个 model 类无账务模型；字段值只是 `TextField`（`companies/models.py:55`） | master 同样没有任何报表/对账/PDF；原件提到的 preparation 归档导出在 master **也没有** |
| **O-04** | P0 | **B（机制证伪）** | `gipfel.service:18-24`（单进程 daphne）、`settings.py:304-309`（无 OPTIONS/WAL/busy_timeout）、`signals.py:114-125`→`audit.py:62-75`（每次写多一条审计）、`exceptions.py:43-50`（4xx/5xx 也插）、`nginx-gipfel.conf:9-13`（无限流）、`auth.ts:155`（20s 心跳）、`request.ts:22`（15s 超时） | 7 项论据成立；**但「全部同步视图共用一条线程」不成立**：Django 5.0.14 每请求进 `ThreadSensitiveContext`，asgiref 按 context 各建一个 `ThreadPoolExecutor(max_workers=1)`（本机 asgiref 3.12.1：`sync.py:426/477`）→ 请求间并发。真实压力点改写为 **SQLite 单写者锁争用 + 线程数无上限** |
| **O-05** | P0 | **A（更重）** | `docs/MIGRATION.md:502/506/514-515`（仍教你手写 `scripts/backup.sh`、仍用 `cp -a` 热拷贝活库、`\| sudo crontab -`）、`scripts/` 仅 8 个文件无 backup.sh、全仓无 `OnCalendar`/`systemd.timer`、`docs/OPS.md:100-108`（§8 不说备份来源） | master **连 `snapshot_sqlite_consistent` 都没有**（该函数只在 bugfix-merged 的 `scripts/lib/deploy-common.sh:180-181`），而 master 自己的 `deploy-linux.sh:175` / `update-from-github.sh:192` 就是热拷贝活库且从不 `systemctl stop` |
| **O-06** | P1 | **A（范围更广）** | `auth/views.py:156-158`（登录即 `token_version + 1`）、`:162`（广播 `auth:required`）、`authentication.py:110-114`（比对 tv→401「账号已在其他设备登录」）、`gateway.py:125/139`、`README.md:217`（自述为有意设计） | 单会话对**所有角色**生效（非超管专属）。原件后半条「`contract_watcher` 与浏览器互踢」→ **C**（master 无该程序） |
| **O-07** | P1 | **A** | `大屏/投屏/排行榜/leaderboard/scoreboard` 全仓 0；`countdown` 仅命中第三方 bundle；`announcements/models.py:8-19` 仍是版本更新公告 + `seed_announcements.py` 默认 v1.4.0 内容；房间仅 `comp-{id}`/`user-{id}`，`gateway.py:189-204` 其它一律拒绝 | 完全同构；master 同样无任何计时/大屏/定向广播 |
| **O-08** | P1 | **A** | `users/models.py:30-34`（3 个角色）、`:49-52`（范围只有公司 id 列表，无产业维度）、`permissions.py:342-347`（审计与执行未分离）、`audit/views.py:25`（`/api/audit-logs` 需 `account:manage`）、`router/index.ts:225`（前端路由也收紧为超管） | 完全同构；master 在「审计看不到审计日志」上比原件更彻底（前端路由也堵了） |
| **O-09** | P1 | **A** | frontend 内 `serviceWorker/navigator.onLine/workbox/offline` 0 命中；`frontend/public/` 为空；`api/cache.ts` 只有 IndexedDB 读缓存，无写队列；断网写操作只有 15s 超时 + 普通 toast（`request.ts:21-23`） | 完全同构 |
| **O-10** | P1 | **B** | `AccountManagementView.vue:133-141`（角色下拉 3 项）、`:258-313`（`derivePermissions` 硬编码，无权限勾选）、`:457-485`（保存固定提交派生值） | 前 3 项 A 级成立；第 4 项「保存静默清掉手工授的扩展权限」在 master **不可达**（扩展权限本就授不出去，且 `grantCeiling` 恒等于派生集合）→ 风险位移为「根本授不出去」（计入 O-01） |
| **O-11** | P1 | **A（+master 特有差异）** | `stores/version.ts:31-32`（严格 `!==` 封锁）、`request.ts:27-33`（拒绝全部请求）、`App.vue:20-27`（启动 + 每 5 分钟复检）、`VersionUpdateDialog.vue:13-29`（不可关闭对话框）、`deploy/nginx-gipfel.conf:151-155`（SPA 无缓存头） | 封锁机制同构；**但触发条件需修正**（见 §4.2），且 master **特有**：`deploy-linux.sh:184-190` 的 rsync 只同步 `backend/ frontend/ deploy/`，根 `VERSION.json` **从不随部署落盘**，而 `views.py:32-41` 与 `vite.config.ts:8-14` 在文件缺失时都回退 `"0.0.0"` → 标准 Linux 部署下**两边都是 0.0.0，版本封锁恒不触发（保护静默失效）**；只有「安装目录本身是 git clone」的模式下封锁才真实生效 |
| **O-12** | P1 | **A（略重）** | `users/models.py:84`（bcrypt rounds=12）、`users/views.py:83-84`（新建默认 `mustChangePassword=True`）、`authentication.py:116-122`（未改密前拦截业务接口）、`auth/views.py:245`（`user.check_password()` → `users/models.py:93` `bcrypt.checkpw`）+ `:250`（`user.set_password()` → `users/models.py:84-85` `bcrypt.gensalt(rounds=12)`/`hashpw`） | 登录 1 次 bcrypt；**改密在 master 是 2 次**（校验 + 重哈希）→ 首次入场合计 **3 次 cost=12 bcrypt + 2 次 SQLite 写**，比原件描述更重；`stock:view` 下单能力同原件 |
| **O-13** | P1 | **A（合同侧更重）** | `emit.py:147/150`（映射 `None`）+ `signals.py:103-105/199-200`（跳过）+ `audit/views.py:20-56`（仅超管、4 个过滤项、无公司维度）+ `audit/urls.py:7-9`（无清理入口）+ `AuditLogView.vue:166`（20 条/页） | 字段值变更不进审计同构；**master 额外更重**：合同执行用 `.update()` 原子抢占（`contracts/views.py:333-345`，代码注释 `:342/:381-383` 自述「`.update()` 不触发 `post_save`」），全后端显式 `log_write` 只出现在 `signals.py:117/158` 与 `stock/engine.py:1916` → **一次成功执行合同产生 0 行审计**（原件称 bugfix-merged「执行完显式补写审计」） |
| **O-14** | P1 | **A** | `window.print/jsPDF/html2canvas/一式三份/counterpart` 应用代码 0 命中（`copies` 仅 LICENSE）；`ContractType` 无正文模板（`contracts/models.py:13-34`）；**越范围放行**在 master 位于 `contracts/views.py:844-859`（`_assert_edit_party_scope`：持 `contract:execute` **或** `contract:manage` 即 `return`，不做任何范围校验），调用点 `:417`，而 `COMPETITION_ADMIN` 默认正好同时持有这两个权限（`permissions.py:342-347`） | 同构；「任何主席团成员可替别家公司填合同编号」在 master 上照旧 |
| **O-15** | P1 | **A（更重）** | master `docs/` 只有 4 个文件（CUSTOM_WIDGET_GUIDE / MIGRATION / OPS / Vue-Django迁移设计）；**`WSL与生产环境验证操作手册.md` 与 `WSL与生产环境验证结果记录.md` 在 master 上都不存在**；docs+README 无「11 项/待验证/生产真机/压测」；无压测脚本 | 原件「真机从未验证」成立且**更严重**：master 连那份「11 项待验证」的手册/记录都没有（它们只存在于工作区/bugfix-merged）。原件的 DB 探针数据属 bugfix-merged 开发库，**不可作为 master 结论** |
| **O-16** | P2 | **C（「没有这个问题」+ 更缺建包）** | Excel/代码建包通道在 master 整体不存在（`contracts/management/`、`contracts/builder/`、`backend/examples/` 均无；全仓 management command 仅 2 个：`seed_announcements`、`rundaphne`） | master **没有第二套财年口径**（因为根本没有 Excel 建包）；但唯一那套仍是 0 基（`CompetitionListView.vue:444/447/148`、`store` 推导），命名问题仍在、更轻 |
| **O-17** | P2 | **A** | `competitions/views.py:212-227`（PATCH 无状态机）、`:233-234`（回切 ACTIVE 必再跑 FY_START）、`CompetitionListView.vue:461`（「此操作不可撤销」文案） | 完全同构（连行号都接近） |
| **O-18** | P2 | **A** | `request.ts:746-855`：`:757-758` 无上限 `Promise.all`、`:760-767` 按公司逐个 `/company-fields/{cid}`、`:817-818` 地图再来一轮；frontend 无 `p-limit/Semaphore/并发上限`；唯一调用点 `resource-changed.ts:173` | 完全同构；master 自己的版本公告还写着「修复断线重连后大量并发对账请求」（`seed_announcements.py:54`、`data/announcement.ts:70`）→ **公告与实现不符** |
| **O-19** | P2 | **A** | `common/middleware.py:203-205`（进程内 defaultdict）、`:206-208`（窗口/阈值/锁定时长常量）、`:230`（键 `(ip, username)`）、`:256`（只拦 `POST /api/auth/login`）；`README.md:200` 自述 | 完全同构；master 多一个 `_cleanup_locks`（`:209-225`）但不改变性质 |
| **O-20** | P2 | **C（能力更缺）** | `contract_watcher/xlwings/bookkeeping/记账` 全仓 0 命中 | master **根本没有**这条外部记账/取证兜底路径 → 与 O-03 叠加后「现场无账表可核」比 bugfix-merged 更严重 |
| **O-21** | P2 | **C（能力更缺）** | `preparation` 全仓 0；后端 `openpyxl/xlsxwriter/csv/Content-Disposition/FileResponse` 全 0；前端 `xlsx/exceljs/file-saver/download` 全 0；`requirements.txt` 无导出库 | master 连 bugfix-merged 那份 markdown/JSON 准备归档都没有 → 闭幕式颁奖数据只能手抄 |
| **O-22** | P2 | **C（能力更缺）** | `preparation`、`checklist`、`准备清单` 全仓 0 命中；`backend/apps/` 27 个应用无对应模块 | 不是「清单只读」，而是**整体没有准备清单这个功能** |
| **O-23** | P2 | **A** | `messages/views.py:64`（`_MAX_RECIPIENTS = 500`）、`:349-351`（上限校验）、`:323-324`（需 `message:manage`）、`permissions.py:326-332`（属扩展集，不在默认/上限内）、`AccountManagementView.vue:258-312`（硬编码派生、无勾选）、`:51-54`（只回收件人数）、无定时/重播字段 | 5 个子项全部同构；且因 master 无 `allowExtras`，`message:manage` **谁都授不出去**（比原件更死） |
| **O-24** | P2 | **A** | `common/pagination.py:11`（`DEFAULT_PAGE_SIZE = 50`）、`AuditLogView.vue:166`（20）、`contracts/views.py:204-215`（非超管整表进内存）+ `:733-752`/`:755-790`（范围过滤） | 同构；master 另有 `:178-193` 一处整表进内存路径 |
| **O-25** | P3 | **A（略重）** | `CompetitionListView.vue:466`（本端乐观置 null）+ `competition.ts:161-162`（广播置 null）+ `TopBar.vue:35`（渲染「未开启财年」）；`:19-26` 的 `fiscalYearLoading` 只抑制首次加载/切比赛 | 同构；原件说「短暂」，实际是**持续整个两次点击间隔**的真实状态 |
| **O-26** | P3 | **A** | `deploy/nginx-gipfel.conf:151-155`（SPA `try_files` 无任何缓存头）；缓存头只出现在 `/uploads/`、`/static/` 与日志查看器块 | 同构；与 O-11 联动即「刷了还是旧版本」 |
| **O-27** | P3 | **C（能力更缺）** | master 无 preparation/自检：`preparation|checklist|自检` 0 命中；未鉴权探针只有 `backend/urls.py:17-18` 的 `/api/health`、`/api/version`；`SettingsView.vue` 只有后端管理/日志查看器/公告/控件包 | 不是「要超管点三轮」，而是**根本没有赛前自检入口** |
| **O-28** | P3 | **A** | `TopBar.vue:35`（「未开启财年」无操作指引）、`Sidebar.vue:30`（多一处）、`request.ts:45-72`（固定文案表 401/504…）+ 统一 `ElMessage` | 同构（master 甚至多一处无指引文案） |

## 3. master 比原件描述更严重的 7 处

1. **O-01（+O-10）扩展权限在 master 上无法授予**：`permissions.py:388-401` 对不在 `grantCeiling` 的权限一律计入 `violations`，扩展集也不例外（提示语是「需超管显式放开」，但**发起请求的正是超管**且依然失败）；全仓 `allowExtras` **0 命中**（bugfix-merged 有 4 处）。→ 原件「赛前用 API 授 `company:manage`」的绕过方案在 master 上不可执行。
   **master 可行的替代**：改建成 **多个 `SUPER_ADMIN` 账号**（`has_permission` 对超管恒真，`permissions.py:264-265`；`assert_grant_allowed` 允许建超管账号，`:379-382`），每个账号一台设备绕开 O-06 的单会话限制——代价是**完全放弃最小权限**：超管隐式全权、`companyScopes` 对它没有约束（无法按产业限定范围），且每个超管都能改账号与推进财年。
2. **O-05 备份链路更空**：master 连 `snapshot_sqlite_consistent`（bugfix-merged 有）都没有；文档仍要人手工创建 `backup.sh` 且用 `cp -a` 热拷贝活库；`docs/OPS.md` §8 只说「停服务→覆盖→重启」。
3. **O-13 合同执行零审计**：`.update()` 绕过 `post_save`（代码注释自述），contracts 内无任何显式 `log_write` → 成功执行一次合同在 `audit_log` 里不留痕。原件把「合同执行留痕」列为**亮点**，在 master 上该亮点不成立（原子防重放那部分仍成立）。
4. **O-15 验证资产缺失**：`WSL与生产环境验证操作手册.md` 与 `...结果记录.md` 在 master 上都不存在，「11 项待验证」这件事在 master 里连记录载体都没有。
5. **O-06 单会话覆盖所有角色**（不只是超管），原件把它当作超管专属约束。
6. **O-12 改密成本更高**：master 改密需要 2 次 bcrypt（checkpw + hashpw），首次入场共 3 次 cost=12 + 2 次写库。
7. **O-25 状态持续时间更长**：不是「短暂显示」，而是持续整个两次点击的间隔。

## 4. 原件中被证伪 / 需改写的论据

### 4.1 O-04「所有同步视图共用一条线程」——在 master 上不成立
- 原件依据 Django `handlers/base.py` 的 `sync_to_async(..., thread_sensitive=True)` 推断「全部同步视图共用一个线程」。
- master 侧事实：Django 5.0.14 的 ASGI handler 每个请求进入 `ThreadSensitiveContext`；asgiref 为**每个 context** 新建 `ThreadPoolExecutor(max_workers=1)`（本机 asgiref 3.12.1：`sync.py:426` 的类型声明、`:477` 的创建点）。→ **不同请求各自成线程，彼此并发**。
- 因此真正的压力点是：**SQLite 单写者锁争用**（回滚日志模式 + 默认 5s busy timeout，无 WAL/`PRAGMA`）＋ **线程数无上限**（无 `LimitNOFILE`/`TasksMax`）＋ nginx 无限流。原件「1100 个请求涌向一条线程」的量化偏重，但**结论方向（财年边界与重连风暴下大面积超时/`database is locked`）不变**。
- 附加风险：master `requirements.txt` **未锁 asgiref**（Django 5.0 只要求 `>=3.7`），并发行为随安装时的 asgiref 版本漂移——这本身是运维缺陷（可与 O1-O5 审计的「无依赖锁/无 CI」合并）。

### 4.2 O-11 触发条件需修正 + master 特有的「封锁静默失效」
- 原件表述「任何后端升级/热修都会让 `VERSION.json ≠ /api/version` → 全客户端封锁」偏宽：实际需 **`VERSION.json` 变化而浏览器仍跑旧 bundle**（或旧 bundle 的注入值 ≠ 后端读到的值）。
- master 特有：`deploy-linux.sh:184-190` 的 rsync 只同步 `backend/`、`frontend/`、`deploy/` 三棵子树，**根目录 `VERSION.json` 从不落盘**；而 `auth/views.py:32-41` 与 `vite.config.ts:8-14` 在文件缺失时都回退 `"0.0.0"` → 标准 Linux 部署下两边恒等于 `0.0.0`，**版本硬封锁永远不触发（防护静默失效）**。反之在「安装目录即 git clone」模式下封锁真实生效，午休升级即触发原件的场景。

### 4.3 原件若干「亮点」在 master 上部分不成立
| 原件表扬项 | master 现状 |
| --- | --- |
| 合同执行「原子且留痕」 | 原子防重放成立（`transaction.atomic` + 条件 `update`，`:333-350`）；**留痕不成立**（见 §3.3） |
| 「准备清单 39 项／dry-run 建包／归档导出」 | master 无 `preparation` 应用，**全部不存在** |
| 「`contract_watcher` 是唯一好用的取证底稿」 | master 无 `contract_watcher`，**不存在** |
| 「有可执行的端到端准备 runbook 与建包教程」 | `docs/汽车产业链测试赛准备.md`、`BUILD_COMPETITION_BY_CODE.md`、`比赛Excel建包教程/规范.md`、`合同可视化新建操作指南.md` **均不在 master** |

## 5. master 上「无法命中」的 5 条：4 条是能力更缺，1 条是没这个问题

| ID | 判定 | 对 master 的真实含义 |
| --- | --- | --- |
| O-20 | 能力更缺 | 没有外部记账/取证程序 → 现场「当天账表」连兜底路径都没有（与 O-03 叠加） |
| O-21 | 能力更缺 | 没有任何业务数据导出（连 markdown/JSON 归档也没有）→ 颁奖/归档只能手抄 |
| O-22 | 能力更缺 | 没有准备清单功能本身（不是「只读」） |
| O-27 | 能力更缺 | 没有赛前自检入口（只有 `/api/health`、`/api/version` 两个探针） |
| O-16 | **没这个问题** | master 无 Excel/代码建包通道 → 不存在「两套财年口径」；但唯一口径仍是 0 基，命名问题以更轻形态保留 |

## 6. 对原件 §6「赛前最小可执行清单」的 master 版修订

原件清单里**在 master 上不可执行**的条目（必须改写或放弃）：

| 原件条目 | 在 master 上 | 建议替代 |
| --- | --- | --- |
| T-7 第 3 项：写本次比赛建包数据（`manage.py build_competition` / `build_contract_types`） | ❌ 不可执行：master 无 `contracts/management/`、无 `contracts/builder/`、无 `backend/examples/` | 只能走**前端界面 + 数据管理页**逐项手工建（行业类型/字段/合同类型/公司/财年），需预留远超 T-7 的工时；或先把建包能力从 bugfix-merged 移植过来 |
| T-7 第 5 项：用 API `allowExtras: true` 授 `company:manage` | ❌ 不可执行：master 无该通道，必然 400 | 改建**多个 SUPER_ADMIN 账号**（每账号一台设备），并接受零权限隔离；`companyScopes` 对超管无效，无法按产业限范围 |
| T-7 第 1 项：写 `scripts/backup.sh` 并复用 `snapshot_sqlite_consistent` | ⚠️ 半可执行：脚本仍要自己写，但**函数不存在** | 改用 `sqlite3 db.sqlite3 ".backup <目标>"`（或 `VACUUM INTO`），停服或用 `.backup` 保证一致性；备份到第二台机器 + U 盘 |
| T-1 第 7 项：起 `contract_watcher` 跑记账与 `check()` 平衡校验 | ❌ 不可执行：master 无该程序 | 账表只能完全离线（学生 Excel）+ 人工核对；若必须用，需先把 `contract_watcher/` 从 bugfix-merged 移植并在现场机器上验证 |
| T-1 第 6 项：全流程彩排（建合同→执行→推进财年→确认定时器→导出归档） | ⚠️ 部分可执行：**导出归档步骤不存在** | 彩排到「确认财年初定时器生效」为止；归档改为手工导出 DB + 截图 |
| T-0 第 12 项：盯 `backend/logs/gipfel.log` 的「财年定时器 / 审计写入失败 / `database is locked`」 | ✅ 可执行 | 注意 `deploy-linux.sh:652` 横幅给的是 `app.log`（实际文件是 `gipfel.log`，见 O5 审计 P3-1） |

**在 master 上仍然成立的现场结论**（与原件一致，无需改写）：明天以 master 直接开赛，同一组 P0 依然全部命中——选手不能写、财年推进非原子且定时器可静默跳过、无任何报表对账、SQLite 单写者 + 无限流、无备份。**且其中三条比原件描述更重**（§3）。

## 7. 与本次 master 运维缺陷审计（O1–O5）的交叉

两份报告在 master 上互相印证的高危项（同一根因、两处独立发现）：

| 交叉点 | 本对照 | O1–O5 审计 |
| --- | --- | --- |
| 无任何备份/快照，且现有备份是活库 `cp` | O-05 | P1-2（唯一回滚点不可靠） |
| 文档让手工创建 `scripts/backup.sh` + `crontab -` 覆盖 root crontab | O-05 | P3-4（`\| sudo crontab -` 整体替换） |
| 单进程 daphne + SQLite 无 WAL/无 busy_timeout | O-04 | P2-14（`DATABASES` 无 OPTIONS、两 daphne 共库） |
| nginx 零限流 + 无限流兜底 | O-04 | P2-1（`limit_req/limit_conn` 全缺、128m vs 10MB 错配） |
| 无 CI/发布门禁、无依赖锁（含 asgiref 未 pin） | O-15、§4.1 | P2-6（无 CI、无 tag、master 即生产源） |
| 健康检查是纯 liveness，无法发现上述任何一条 | O-15 | P2 / O5-06 |
| `index.html` 无缓存头 + 版本硬封锁 | O-11、O-26 | P2（SPA 兜底吞 404、无缓存策略） |

## 8. 方法与局限

- **前提校正（含对 Lead 自身的更正）**：① 原任务书称 master 存在 `docs/WSL与生产环境验证操作手册.md`——**不成立**，master `docs/` 只有 4 个文件（分析员纠正了 Lead）；② master `backend/apps/contracts/views.py` 是 **869 行**（非 738）——根因是 PowerShell `Measure-Object -Line`/`Get-Content` **漏计空行与超长行**（实测 `competitions/views.py` 真 268、`request.ts` 真 867）。**本对照所有行号均由 `read`/`grep`/`git show` 得出，并以 LF 字节数交叉验证**。
- **不得沿用的原件证据**：原件 §5 的角色权限矩阵（E-1）与数据库规模探针（E-2）跑在 `bugfix-merged` 的库上，**不是 master 的结论**；master 侧权限结论一律由 `permissions.py` 读码推导。
- **未复核/需实测**：O-04 的现场量化（并发请求数、财年定时器耗时、`database is locked` 频率）、O-12 的 100 人签到耗时、O-18 重连风暴强度、asgiref 版本漂移带来的并发行为差异——均需在目标环境实测，本对照为静态审计。
- **未执行任何写操作**：未起服务、未跑负载、未改任何文件（除本报告与 4 份对照分册）。

## 9. 交付物

| 文件 | 内容 |
| --- | --- |
| 本文件 | 28 条总对照（A/B/C/D）＋更重点＋证伪点＋清单修订 |
| [M1-部署备份版本类对照.md](M1-部署备份版本类对照.md) | O-05 / O-09 / O-11 / O-15 / O-21 / O-22 / O-26（7 条：A=5、C=2） |
| [M2-运行时并发财年类对照.md](M2-运行时并发财年类对照.md) | O-02 / O-04 / O-13 / O-17 / O-19 / O-24 / O-25（7 条：A=6、B=1） |
| [M3-权限角色账号会话类对照.md](M3-权限角色账号会话类对照.md) | O-01 / O-06 / O-08 / O-10 / O-12 / O-27 / O-28（7 条：A=4、B=2、C=1） |
| [M4-业务能力缺失类对照.md](M4-业务能力缺失类对照.md) | O-03 / O-07 / O-14 / O-16 / O-18 / O-20 / O-23（7 条：A=5、C=2） |
| [00-master运维缺陷总报告.md](00-master运维缺陷总报告.md) | 前序 master 运维层面缺陷审计（O1–O5 域，37 条） |

**声明**：本次对照未修改 master 分支或工作区的任何代码、配置、模板与脚本；`git status --porcelain` 与工作开始前一致。
