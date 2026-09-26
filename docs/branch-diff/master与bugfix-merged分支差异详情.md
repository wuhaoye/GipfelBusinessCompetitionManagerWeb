# master 与 bugfix-merged 分支差异详情

> **对比方向**：`git diff master bugfix-merged`（即「从 master 到 bugfix-merged 需要发生的变化」）
> **仓库**：`GipfelBusinessCompetitionManagerWeb`
> **生成时间**：2026-09-26 11:45:11
> **数据来源**：本地 git 对象库，已提交内容（不含工作区未提交改动）

## 0. 分支名确认与阅读说明

仓库中**不存在**名为 `bug-merge` 的引用（`git rev-parse bug-merge` 报 unknown revision）。与 `master` 存在可比关系、名称含 bug 的引用如下，本文件按名称最接近的 `bugfix-merged` 生成：

| 候选引用 | 提交 | 提交日期 | 说明 | 是否被本文采用 |
| --- | --- | --- | --- | --- |
| `bugfix-merged` | `5cb3468` | 2026-09-24 | 名称含 merge，且为当前 HEAD | **是（本文对象）** |
| `bugfix` | `a4aac46 2026-09-12` | - | 已完全包含于 `bugfix-merged` | 否，见附录 A |
| `backup/before-master-merge` | `e630443 2026-09-15` | - | 备份分支 | 否 |

若实际想对比的是 `bugfix`，请把本文件第 1–7 章的 `bugfix-merged` 全部替换为 `bugfix` 后重跑附录 C 的命令；两者的 master 侧完全一致（`master` 同为其祖先）。

---

## 1. 分支关系与可比性

| 项目 | 值 |
| --- | --- |
| 基准分支 | `master` = `97e117e`（2026-09-10，bing_de） |
| 目标分支 | `bugfix-merged` = `5cb3468`（2026-09-24，wuhaoye） |
| 共同祖先 merge-base | `97e117e` —— fix(frontend): 修复合约图自动布局分层错乱问题 |
| master 领先 | 0 个提交 |
| bugfix-merged 领先 | **148 个提交**（141 个非合并 + 7 个合并） |
| 祖先关系 | `master` 是 `bugfix-merged` 的**严格祖先**（`git merge-base --is-ancestor master bugfix-merged` 返回真） |
| 合并方式 | **可零冲突 fast-forward**；不需要解决任何冲突 |
| 变更时间跨度 | 2026-09-11 ～ 2026-09-24 |

提交链路（自旧到新）：

```
master  (97e117e)
  └── bugfix  (a4aac46)          +2 个提交（含 prep-export-import 合并）
        └── bugfix-merged  (5cb3468)   +148 个提交
```

> 结论：`master` 上没有任何独有提交（领先 0 个），因此本文列出的差异**全部是 bugfix-merged 新增或修改的内容**，不存在「被删除的 master 特性」需要额外甄别。

---

## 2. 变更规模总览

| 指标 | 数值 |
| --- | --- |
| 变更文件总数 | **357** |
| 新增行 | **+62,003** |
| 删除行 | **-801** |
| 状态分布 | 新增 A=270｜修改 M=75｜重命名 R=12｜删除 D=0 |
| 二进制/样例文件 | 9 个（xlsx / png / ico） |
| `git diff` 原始输出 | 67,072 行 / 3147.3 KB |

### 2.1 按顶层目录分布

| 顶层目录 | 文件数 | 新增行 | 删除行 | 变更性质 |
| --- | ---: | ---: | ---: | --- |
| `backend` | 134 | +28,782 | -102 | 比赛准备 app、合同类型 builder、权限/审计/撮合等核心域修复 |
| `tests` | 92 | +13,075 | -171 | 回归验证资产（fix_verify 前端 mjs 用例 + 顶层脚本），12 个伪测试改名 |
| `contract_watcher` | 63 | +8,263 | 0 | 全新的合同监听记账程序（独立进程，不改动现有服务） |
| `docs` | 10 | +4,746 | -5 | 建包/合同代码化文档（多为新增），少量运维文档同步更新 |
| `scripts` | 12 | +2,862 | -310 | 部署、升级、同步、开发启动脚本加固 |
| `frontend` | 35 | +2,269 | -160 | 前端请求层、权限、分页、时区、XSS 等修复与新对话框 |
| `(仓库根目录)` | 7 | +1,347 | -2 | README、.gitattributes、审计与验证报告 |
| `deploy` | 4 | +659 | -51 | nginx / systemd 单元与部署说明 |

> 对账：上表三列之和分别为 357 个文件、+62,003 行、-801 行，与 `git diff --shortstat master bugfix-merged`（357 files changed, 62,003 insertions(+), 801 deletions(-)）逐项一致。重命名文件按 git 报告的改名增量计入其**新**路径所在目录（12 个 `tests/test_* -> tests/explore_*` 共 +80 行，全部落在 `tests`）。

### 2.2 按提交类型 / 影响域分布

| 提交类型 | 数量 |
| --- | ---: |
| `fix` | 119 |
| `feat` | 12 |
| `test` | 3 |
| `docs` | 3 |
| `chore` | 2 |
| `refactor` | 2 |
| （合并提交） | 7 |

| 提交类型 | 影响域（scope） | 数量 |
| --- | --- | ---: |
| `fix` | `watcher` | 22 |
| `fix` | `scripts` | 17 |
| `fix` | `frontend` | 16 |
| `fix` | `excel` | 15 |
| `fix` | `import` | 9 |
| `fix` | `deploy` | 8 |
| `fix` | `contracts` | 4 |
| `fix` | `stock` | 4 |
| `feat` | `excel` | 3 |
| `fix` | `auth` | 3 |
| `fix` | `tests` | 3 |
| `feat` | `preparation` | 2 |
| `feat` | `watcher` | 2 |
| `fix` | `preparation` | 2 |
| `fix` | `repo` | 2 |
| `fix` | `users` | 2 |
| `refactor` | `excel` | 2 |
| `chore` | `gitignore` | 1 |
| `chore` | `watcher` | 1 |
| `docs` | `(未标注)` | 1 |
| `docs` | `contracts` | 1 |
| `docs` | `excel` | 1 |
| `feat` | `contracts` | 1 |
| `feat` | `contract-watcher` | 1 |
| `feat` | `deploy` | 1 |
| `feat` | `frontend` | 1 |
| `feat` | `nginx` | 1 |
| `fix` | `audit` | 1 |
| `fix` | `common` | 1 |
| `fix` | `contracts/builder` | 1 |
| `fix` | `docs` | 1 |
| `fix` | `examples` | 1 |
| `fix` | `logviewer` | 1 |
| `fix` | `maps,tech_tree` | 1 |
| `fix` | `money` | 1 |
| `fix` | `nginx` | 1 |
| `fix` | `permissions` | 1 |
| `fix` | `realtime` | 1 |
| `fix` | `部署` | 1 |
| `test` | `(未标注)` | 1 |
| `test` | `excel` | 1 |
| `test` | `preparation` | 1 |

### 2.3 提交人分布

| 提交人 | 提交数 |
| --- | ---: |
| wuhaoye | 132 |
| bing_de | 15 |
| Bingde | 1 |

---

## 3. 六条变更主线

本次差异不是零散修补，而是六条互相独立的工作线并行推进的结果，理解主线比逐文件看 diff 更重要。

### 3.1 主线一：比赛准备与 Excel/CSV 建包体系（全新能力）

新增 Django app `apps.preparation` 与配套的建包器、分组导出导入、开赛前体检，以及 5 份建包相关文档和 3 份 Excel 样例。该 app **没有 models.py / migrations**，全部逻辑通过既有 app 读写数据，因此合并后**不需要新增数据库迁移**（但 `backend/backend/settings.py` 已把 `apps.preparation` 加入 `INSTALLED_APPS`，`backend/backend/urls.py` 已挂载路由）。

| 关键新增路径 | 作用 |
| --- | --- |
| `backend/apps/preparation/builder/{core,schema,types}.py` | Excel/CSV 读表 → 建比赛的建包核心 |
| `backend/apps/preparation/{checklist,plan,archive}.py` | 准备清单、开赛前体检、分组归档导出导入 |
| `backend/apps/preparation/management/commands/build_competition.py` | `manage.py build_competition` 命令入口 |
| `backend/apps/preparation/{views,urls}.py` | 准备模块 API |
| `frontend/src/components/preparation/*.vue` | 导入/概览对话框 |
| `docs/比赛Excel建包规范.md`、`docs/比赛Excel建包教程.md`、`docs/BUILD_COMPETITION_*.md` | 表格规范与 API/代码化说明 |
| `backend/examples/excel/*.xlsx`、`backend/examples/*` | 最小示例、汽车产业链示例、建包模板 |

### 3.2 主线二：合同类型代码化（降低 effect / ENTITY 抽象）

把合同类型从「表格里手写四份 JSON」改为「只引用代码脚本」，新增 `backend/apps/contracts/builder/` 包与 `build_contract_types` 命令。

| 关键新增路径 | 作用 |
| --- | --- |
| `backend/apps/contracts/builder/{builder,effects,refs,values,validate,aggregates,errors}.py` | 合同类型构建器：引用解析、取值、效果、聚合、校验 |
| `backend/apps/contracts/management/commands/build_contract_types.py` | 类型建库命令 |
| `backend/apps/contracts/tests/{test_type_builder,test_complex_contracts,test_named_effect_semantics}.py` | 构建器回归用例 |
| `docs/CONTRACT_TYPE_BY_CODE.md`、`docs/合同可视化新建操作指南.md` | 使用说明 |

### 3.3 主线三：合同监听记账程序 contract_watcher（全新独立目录）

顶层新增 `contract_watcher/`，63 个文件、+8,263 行，是一个**独立运行、不改动现有服务**的旁路程序：轮询合同 → 生成记账 → 按公司分账写入 SQLite → 触发式批量写 xlsx，并带 Tkinter 界面与单实例锁。

| 关键文件 | 作用 |
| --- | --- |
| `contract_watcher/contract_watcher.py` | 主轮询循环（心跳、停滞看门狗、失败退避、增量拉取） |
| `contract_watcher/bookkeeping.py` | 记账金额与科目定位（显式校验，杜绝裸 float 静默漏账） |
| `contract_watcher/store.py` | SQLite 落地与原子写进度水位 |
| `contract_watcher/handlers.py`、`readable.py`、`watcher_config.py` | 自动生成 handler、可读记录、配置 |
| `contract_watcher/gui.py`、`start_gui.bat` | Tkinter 界面与启动脚本 |
| `contract_watcher/{README,DATA_GUIDE,TEST_FLOW}.md` | 使用/数据/测试说明 |
| `contract_watcher/bookkeeping_example/`（43 文件）、`test_run_recheck/`（8 文件） | 样例账本与实测产物（xlsx 已新增入库，注意业务数据脱敏） |

### 3.4 主线四：安全与正确性缺陷修复（75 个修改文件）

这是「对既有代码的改动」集中区，也是评审最需要关注的部分。75 个修改文件按顶层目录分布为：`backend` 33 个、`frontend` 21 个、`scripts` 8 个、`deploy` 4 个、`tests` 4 个、`docs` 3 个、仓库根 2 个。修复以缺陷编号登记，编号索引见第 6 章。

| 类别 | 代表缺陷编号 | 代表改动文件 |
| --- | --- | --- |
| 认证/会话/实时 | I-01、I-02、I-03、C-01、I-05、X-31 | `backend/apps/auth/authentication.py`、`backend/apps/auth/views.py`、`backend/apps/common/middleware.py`、`backend/apps/users/views.py` |
| 权限越权 | I-13、I-19、S-05、S-06、D-02 | `backend/apps/common/permissions.py`、`backend/apps/stock/views.py`、`backend/apps/contracts/views.py` |
| 竞赛隔离与脏数据 | D-07、D-08、W-02、W-03、V-05 | `backend/apps/{maps,tech_tree}/serializers.py`、`frontend/src/views/data-management/*.vue` |
| 数值/浮点/精度 | D-01、D-03、D-04、T-01、Z-02、Z-03 | `backend/apps/stock/{engine,models}.py`、`backend/apps/contracts/engine.py`、`frontend/src/views/stocks/estAmount.ts` |
| 审计与留痕 | C-02、C-04 | `backend/apps/common/audit.py`、`backend/apps/contracts/views.py` |
| 前端健壮性 | F-01、F-02、F-06、F-08、F-12、V-09、W-08、T-02、M-01 | `frontend/src/api/request.ts`、`frontend/src/utils/deleteConfirm.ts`、`frontend/src/utils/format.ts`、`frontend/src/components/dashboard/registerCustomWidgets.ts` |
| 请求层/信封协议 | — | `backend/apps/common/{response,renderers,exceptions}.py`、`frontend/src/api/envelope.ts` |

### 3.5 主线五：部署与运维脚本加固（本差异中单文件改动量最大的一类）

| 文件 | 增/删 | 关键改进 |
| --- | ---: | --- |
| `scripts/deploy-linux.sh` | +568 / -60 | 降级分支折算退出码（X-24）、一致性回滚副本与 migrate 后回滚路径（X-10）、SSH 选项数组化与端口/私钥校验（X-25）、不再把管理员初始口令写进 stdout（X-22） |
| `scripts/update-from-github.sh` | +482 / -59 | pull 失败默认中止、NodeSource 不再用 curl 管道执行（X-06/07/08） |
| `scripts/quick-sync.sh` | +244 / -11 | 不再直接同步活库，改一致性快照 + 双向校验（X-05）、INSTALL_DIR 绝对化（X-20） |
| `scripts/migrate-server.sh` | +221 / -53 | `--dry-run` 真正零副作用、密钥落盘与 root 注入修正（X-02/03/04、X-21） |
| `scripts/start-dev.bat` / `bootstrap-dev.bat` / `stop-dev.bat` / `scripts/dev.py` | +39/-94 等 | 传递真实启动结果（X-19）、重入标记改用参数（X-18）、stop-dev 不再盲杀端口（D-01/D-02）、Ctrl+C 卡死修复 |
| `deploy/nginx-gipfel.conf` | +173 / -18 | Cloudflare 源证书 HTTPS、logviewer TLS 端口校验时机与自定义端口、域名部署下 400/403 修复 |
| `deploy/{gipfel,logviewer}.service` | +26/-9、+18/-7 | 移除无消费方的 daphne Unix socket（X-23）、单元目录权限收紧（X-13/X-14） |
| `deploy/README.md`、`docs/OPS.md`、`docs/MIGRATION.md` | +442/-17 等 | 运维手册同步更新 |
| `scripts/lib/deploy-common.sh`、`scripts/gen_logviewer_key.py`、`scripts/make_favicon.py`、`scripts/verify-migration.sh` | 新增/修改 | 公共函数库、密钥编码容错（X-11/X-12）、迁移校验不再静默中止（X-01/X-09） |

### 3.6 主线六：回归验证资产（新增 125 个验证用例文件）

| 目录 | 文件数 | 说明 |
| --- | ---: | --- |
| `backend/tests_fix_verify/` | 51 | 按缺陷编号命名的后端用例（`test_d01_decimal_exact.py`、`test_s06_stock_scope.py` …） |
| `tests/fix_verify/` | 74 | 前端 mjs 用例 + 打包/桩件（`test_f02_delete_confirm_xss.mjs` …） |
| `tests/*.py`、`tests/*.sh` | 修改 | 冒烟脚本退出码语义化：`big_number_smoke.py`、`sqlite_decimal_roundtrip.py`、`deploy_public_ip_test.sh`、`gipfel-logviewer-diag.sh` |
| `tests/explore_*.py`（12 个重命名） | 重命名 | 原 `tests/test_*.py` 实为「只打印、恒退出 0」的伪测试，改名为 `explore_*` 以避免被误当测试执行（X-16） |

---

## 4. 提交清单

### 4.1 非合并提交（141 个，自新到旧）

| # | 提交 | 日期 | 作者 | 说明 |
| ---: | --- | --- | --- | --- |
| 1 | `5cb3468` | 2026-09-24 | wuhaoye | fix(auth): 修复强制改密被会话心跳打断，并让种子口令在终端显示（X-31） |
| 2 | `2f39918` | 2026-09-23 | wuhaoye | fix(deploy): 修正运行用户创建路径 —— PATH 缺 sbin 静默失败、-U 与已存在组冲突（X-30） |
| 3 | `371cf57` | 2026-09-23 | wuhaoye | feat(watcher): 新增 GUI 启动脚本 start_gui.bat，并给图形界面补上单实例锁 |
| 4 | `bbba7b2` | 2026-09-23 | wuhaoye | feat(watcher): 记账改「先入 SQLite、按公司分账、触发式批量写 xlsx」，加角色门禁与 Tkinter 界面 |
| 5 | `b40fd25` | 2026-09-15 | wuhaoye | fix(repo): 重做被 master 合并覆盖的 X-11/X-14/X-22，并修正三处失真的回归用例（X-29） |
| 6 | `e630443` | 2026-09-15 | wuhaoye | fix(scripts): 修正 X-28 —— bootstrap-dev.bat 的裸 shift 会吃掉 %0，--no-keep-open 必然失败 |
| 7 | `34013bb` | 2026-09-15 | bing_de | fix(frontend): 放宽输入键端口类型限制 |
| 8 | `9d483fc` | 2026-09-15 | bing_de | fix(common): 按异常类型判断 401 提示避免中文泄露 |
| 9 | `26739a9` | 2026-09-15 | bing_de | feat(frontend): 优化站点图标为盾徽并新增多尺寸生成脚本 |
| 10 | `9d0702a` | 2026-09-15 | bing_de | fix(nginx): 修正 logviewer TLS 端口校验时机 |
| 11 | `f9f1230` | 2026-09-15 | bing_de | fix(deploy): 统一日志查看器公网地址并新增部署自检 |
| 12 | `cb72891` | 2026-09-15 | bing_de | fix(deploy): 清理域名部署下无域名遗留的日志查看器URL |
| 13 | `277db38` | 2026-09-15 | bing_de | fix(deploy): 日志查看器 TLS 端口在 --origin-cert 下默认 8443 |
| 14 | `4e8adf8` | 2026-09-15 | bing_de | feat(deploy): 新增日志查看器自定义 TLS 端口部署 |
| 15 | `a594151` | 2026-09-15 | bing_de | fix(deploy): 拦截 SSL 标记区域内误放的散文注释 |
| 16 | `5876d85` | 2026-09-15 | bing_de | test: 添加 HTTPS 443 端口诊断脚本 |
| 17 | `4b83781` | 2026-09-15 | bing_de | feat(nginx): 支持 Cloudflare 源证书启用 HTTPS |
| 18 | `04a4f1d` | 2026-09-15 | bing_de | fix(deploy): 容忍 grep 无匹配并加装 ERR trap |
| 19 | `3e0e5f9` | 2026-09-15 | bing_de | fix(logviewer): 修复域名部署下日志查看器 400/403 |
| 20 | `b08d062` | 2026-09-15 | bing_de | fix(部署): 放行 80/443 并支持反向代理 HTTPS |
| 21 | `5ec6d0a` | 2026-09-14 | wuhaoye | docs: 新增 WSL/生产环境验证手册与结果记录，并在审计报告中登记 X-27 |
| 22 | `179712f` | 2026-09-14 | wuhaoye | fix(repo): 新增 .gitattributes 强制 shell 脚本 LF —— Windows 检出会让全部部署脚本在 Linux 上无法执行 |
| 23 | `917e4de` | 2026-09-14 | wuhaoye | fix(scripts): 修正 X-25 误用的 warn（该脚本只有 log_warn），并适配两处受影响的回归用例 |
| 24 | `20982ce` | 2026-09-14 | wuhaoye | fix(scripts): 部署/升级脚本补一致性回滚副本与"migrate 后失败"的回滚路径（X-10） |
| 25 | `bea7ee7` | 2026-09-14 | wuhaoye | fix(docs): 镜像建议改为按仓库生效并补信任/撤销说明，回滚章节补全代码与数据两侧（X-26） |
| 26 | `be79c44` | 2026-09-14 | wuhaoye | fix(scripts): SSH 选项数组化与可配置端口/私钥/known_hosts，不再静默 accept-new（X-25） |
| 27 | `296ae6f` | 2026-09-14 | wuhaoye | fix(scripts): 部署脚本把降级分支折算成退出码，不再"nginx 失败也报部署完成"（X-24） |
| 28 | `cef251d` | 2026-09-14 | wuhaoye | fix(deploy): gipfel.service 移除没有消费方的 daphne Unix socket（X-23） |
| 29 | `9deeffb` | 2026-09-14 | wuhaoye | fix(scripts): 部署脚本不再把管理员初始口令明文写进 stdout，[DIAG] 不再枚举密钥名（X-22） |
| 30 | `fa86b5e` | 2026-09-14 | wuhaoye | fix(scripts): migrate-server 的 --dry-run 真正零副作用、退出码分场景、--ssh-port 校验（X-21） |
| 31 | `a0cf7ff` | 2026-09-14 | wuhaoye | fix(scripts): quick-sync 的 INSTALL_DIR 绝对化并在 push 前校验源目录（X-20） |
| 32 | `7f2a127` | 2026-09-14 | wuhaoye | fix(scripts): start-dev.bat 传递真实启动结果，dev.py 增加同步前置检查（X-19） |
| 33 | `84e72b7` | 2026-09-13 | wuhaoye | fix(scripts): bootstrap-dev.bat 重入标记改用参数，不再污染父环境也不再杀掉调用方（X-18） |
| 34 | `89a6f5b` | 2026-09-13 | wuhaoye | fix(tests): 连库脚本改用一次性测试库，导入零副作用，去掉静默 SKIP（X-17，含 M-2） |
| 35 | `6e14bf6` | 2026-09-13 | wuhaoye | fix(tests): tests/ 顶层 12 个"只打印、恒退出 0"的伪测试改名并显式声明非测试（X-16） |
| 36 | `7018403` | 2026-09-13 | wuhaoye | fix(tests): 公网 IP 单测与调用 cwd 解耦，删除已废弃的交互式 read 镜像分支（X-15） |
| 37 | `503a1fc` | 2026-09-13 | wuhaoye | fix(deploy): systemd 单元目录权限收紧与诊断脚本密钥掩码（X-13 + X-14） |
| 38 | `7659d04` | 2026-09-13 | wuhaoye | fix(scripts): .env 每个键保持唯一一行，gen_logviewer_key 编码容错（X-11 + X-12） |
| 39 | `cc6e04c` | 2026-09-13 | wuhaoye | fix(scripts): NodeSource 不再 curl\|bash、pull 失败默认中止、日志查看器端口统一（X-06 + X-07 + X-08） |
| 40 | `980c13d` | 2026-09-13 | wuhaoye | fix(scripts): quick-sync 不再直接同步活库，改一致性快照并双向校验（X-05） |
| 41 | `edd966b` | 2026-09-13 | wuhaoye | fix(scripts): 迁移脚本的密钥落盘、root 注入与静默丢库（X-02 + X-03 + X-04） |
| 42 | `39f1465` | 2026-09-13 | wuhaoye | fix(scripts): 迁移验证不再静默中止，并真正校验数据库与日志查看器（X-01 + X-09） |
| 43 | `e6153db` | 2026-09-13 | wuhaoye | fix(scripts): stop-dev 不再按端口/PID 盲杀，改用可校验身份（B01 D-01 + D-02） |
| 44 | `b02401c` | 2026-09-13 | wuhaoye | fix(preparation): 消费者需求按 (区域,产品) 去重、plan 明细真正应用上限（R-13 + R-14） |
| 45 | `46bacb5` | 2026-09-13 | wuhaoye | fix(preparation): 归档截断必须可见，非空比赛探针必须补全（R-11 + R-12） |
| 46 | `479a06b` | 2026-09-13 | wuhaoye | docs(excel): 统一建包文档的表数量口径，并说明 --sheets 过滤掉了哪些表（Z-17） |
| 47 | `71baf77` | 2026-09-13 | wuhaoye | test(excel): 锁死数字文本口径，防回归（Z-15） |
| 48 | `f824314` | 2026-09-13 | wuhaoye | fix(excel): 空表段与隐藏工作表/行都要有明确提示（Z-12 余项） |
| 49 | `516392c` | 2026-09-13 | wuhaoye | fix(examples): 运输合同补车次/碳税下界，冒烟真正跑到超重分支（Z-14） |
| 50 | `5c62f0f` | 2026-09-13 | wuhaoye | fix(watcher): 主循环心跳与停滞看门狗，Excel 挂起可被发现并自愈（CW-19） |
| 51 | `04e664b` | 2026-09-13 | wuhaoye | fix(watcher): 单实例互斥改到共享锁上，跨机器不再重复记账（CW-20） |
| 52 | `405f8cd` | 2026-09-13 | wuhaoye | fix(watcher): 增量拉取 + 失败退避 + 类型目录节流（CW-12） |
| 53 | `b2b2b54` | 2026-09-13 | wuhaoye | fix(watcher): 实测脚本的运行后哈希真算、Excel 回收不再误杀（CW-22） |
| 54 | `c2cc3af` | 2026-09-13 | wuhaoye | fix(watcher): 自测/浸泡脚本用退出码表达成败，失败不再伪装成成功（CW-21） |
| 55 | `08eaee5` | 2026-09-13 | wuhaoye | fix(watcher): 科目定位显式校验，Find 未命中不再静默漏账（CW-18） |
| 56 | `23e135b` | 2026-09-13 | wuhaoye | fix(watcher): 账期取合同执行时间，不再一律记成运行当天（CW-17） |
| 57 | `9a8e47d` | 2026-09-13 | wuhaoye | fix(watcher): 货品表槽位判定对齐模板，写不下时报错而不是覆写最后一行（CW-16） |
| 58 | `22c6bfb` | 2026-09-13 | wuhaoye | fix(watcher): 可读记录时间本地化带标注、数字展示不再改写（CW-15） |
| 59 | `ee7a690` | 2026-09-13 | wuhaoye | fix(watcher): 记账金额显式校验，不再用裸 float() 静默漏账/丢精度（CW-14） |
| 60 | `129b651` | 2026-09-13 | wuhaoye | fix(watcher): EXECUTED 但缺 executedAt 的合同必须告警，不再静默忽略（CW-27） |
| 61 | `bf46be1` | 2026-09-13 | wuhaoye | chore(watcher): 运行产物纳入 .gitignore，避免业务合同与日志入库（CW-26） |
| 62 | `1c0d0f6` | 2026-09-13 | wuhaoye | fix(watcher): 类型改名只改自己那个函数，不再子串替换误伤同前缀的 key（CW-25） |
| 63 | `6092d04` | 2026-09-13 | wuhaoye | fix(watcher): 输出子目录名不再能越出 out_dir（CW-24） |
| 64 | `22822db` | 2026-09-13 | wuhaoye | fix(watcher): 同秒内重复存档不再互相覆盖（CW-23） |
| 65 | `c792f8a` | 2026-09-13 | wuhaoye | fix(watcher): --competition 非正整数直接拒绝，不再静默跨比赛混记（CW-11） |
| 66 | `15ecd64` | 2026-09-13 | wuhaoye | fix(watcher): 进度文件原子写 + 损坏留证告警（CW-13） |
| 67 | `e6c1f21` | 2026-09-13 | wuhaoye | fix(watcher): 凭据失效不再无限静默失败，连续 401 达阈值即报错退出（CW-10） |
| 68 | `0fd056d` | 2026-09-13 | wuhaoye | fix(excel): 表格校验一次列出全部错误，不再遇到第一个就中断（Z-13） |
| 69 | `5a88ec2` | 2026-09-13 | wuhaoye | fix(excel): 显式拒绝 Excel 错误值，不再当普通文本写进比赛（Z-12） |
| 70 | `bfa4c3a` | 2026-09-13 | wuhaoye | fix(import): 覆盖模式更新地图节点与区域说明（R-10 余项） |
| 71 | `1562944` | 2026-09-13 | wuhaoye | fix(import): 覆盖模式真正更新股票行情参数与资金账户余额（R-10） |
| 72 | `6355c57` | 2026-09-13 | wuhaoye | fix(excel): CSV 目录导出前校验表名，不再写出半成品（Z-16） |
| 73 | `687094e` | 2026-09-13 | wuhaoye | fix(excel): 写 xlsx 前校验工作表名（长度/非法字符/重名）（Z-11） |
| 74 | `deb7383` | 2026-09-13 | wuhaoye | fix(import): 重名公司不再整类导入失败，按名兜底改为确定性绑定并提示（R-09） |
| 75 | `bfd9d24` | 2026-09-13 | wuhaoye | fix(import): 追加模式不再覆盖既有区域的概览卡片（R-08） |
| 76 | `101952f` | 2026-09-13 | wuhaoye | fix(import): 比赛状态不覆盖、账号先于消费方导入、消息写入收件人（R-05/R-06/R-07） |
| 77 | `0960c89` | 2026-09-13 | wuhaoye | fix(excel): 写 xlsx 时剔除 XML 非法控制字符（Z-10） |
| 78 | `0ba2f65` | 2026-09-13 | wuhaoye | fix(excel): --inspect 纯预览不再执行工作簿指定的脚本（Z-08） |
| 79 | `650118f` | 2026-09-13 | wuhaoye | fix(excel): --out 覆盖已有归档需显式 --force，并显示归档规模（Z-07） |
| 80 | `66a354d` | 2026-09-13 | wuhaoye | fix(excel): 复用同名比赛后 append 导入不再被占用保护挡住（Z-06） |
| 81 | `463001c` | 2026-09-13 | wuhaoye | fix(excel): --dry-run 不再先把比赛建进库（Z-09） |
| 82 | `d021f5d` | 2026-09-13 | wuhaoye | fix(excel): 文件级异常翻译成中文提示，不再抛 Python 堆栈（Z-05） |
| 83 | `517eaf0` | 2026-09-13 | wuhaoye | fix(excel): 股票参数只写一行不再绕过交叉校验（Z-04） |
| 84 | `58a7476` | 2026-09-13 | wuhaoye | fix(excel): 建包不再静默归一化全角/指数写法的数字（Z-03） |
| 85 | `97e58f4` | 2026-09-13 | wuhaoye | fix(excel): 建包数值列补下界校验，负距离/负单价/负配比不再进归档（Z-02） |
| 86 | `6465458` | 2026-09-13 | wuhaoye | fix(import): 同类型多份合同不再被合并成一条并改名（R-03） |
| 87 | `dee0db7` | 2026-09-13 | wuhaoye | fix(import): 追加模式不再因「范围里的公司已存在」丢掉整行账号（R-04） |
| 88 | `7c98de8` | 2026-09-13 | wuhaoye | fix(import): 追加模式不再因「合同类型已存在」丢掉全部预设合同实例（R-02） |
| 89 | `170f01a` | 2026-09-13 | wuhaoye | fix(excel): 建包不再把首列以 # 开头的真实数据行当注释丢弃（Z-01） |
| 90 | `5757039` | 2026-09-13 | wuhaoye | fix(import): 归档导入单资源失败只回滚该资源，不再「整批静默回滚却报成功」（R-01） |
| 91 | `9f53be5` | 2026-09-13 | wuhaoye | fix(watcher): 记账样例不再改全局 Decimal 精度，Excel 打开失败即回收进程（CW-08 + CW-09） |
| 92 | `e9f3082` | 2026-09-13 | wuhaoye | fix(watcher): 生成的 handlers.py 代码转义 + 语法校验 + 原子写（CW-07） |
| 93 | `e99d133` | 2026-09-13 | wuhaoye | fix(watcher): 自动生成不再屏蔽用户 handler，按命名约定的实现可被注册（CW-06） |
| 94 | `f593c14` | 2026-09-13 | wuhaoye | fix(watcher): 水位线按时间比较、合同列表按 id 去重（CW-05 + CW-04） |
| 95 | `cb01c80` | 2026-09-13 | wuhaoye | fix(watcher): 进度水位与失败重试，杜绝重复记账与永久静默漏账（CW-01 + CW-02） |
| 96 | `57b7b36` | 2026-09-13 | wuhaoye | fix(frontend): 四处列表加载接入请求代次守卫，旧响应不再覆盖新数据（V-09） |
| 97 | `a2b9927` | 2026-09-13 | wuhaoye | fix(frontend): 仓库列表并发请求加代次守卫，旧响应不再覆盖新数据（W-08） |
| 98 | `2a1478d` | 2026-09-13 | wuhaoye | fix(frontend): 合同页科技树清单下拉按比赛取数（W-03 同类缺陷） |
| 99 | `7f592cc` | 2026-09-13 | wuhaoye | fix(frontend): 切换比赛时实体下拉与地图/科技缓存一并失效（V-05） |
| 100 | `681bc43` | 2026-09-13 | wuhaoye | fix(frontend): 零件/产品页面的下拉选项按比赛取数，超管不再跨比赛选实体（W-03） |
| 101 | `ebdd812` | 2026-09-13 | wuhaoye | fix(frontend): 本地分页参数收敛到合法范围，非法 page/pageSize 不再返回错数据（F-12） |
| 102 | `1ed7e0c` | 2026-09-13 | wuhaoye | fix(frontend): 股票下单按钮防连点，双击不再产生两笔委托（T-02） |
| 103 | `39716cc` | 2026-09-13 | wuhaoye | fix(frontend): 股票预计金额整数相乘不再错成 0.xx（T-01） |
| 104 | `23305e4` | 2026-09-13 | wuhaoye | fix(frontend): 载具可通过路径类型改用后端契约字段，配置真正落库（W-02） |
| 105 | `2f5819f` | 2026-09-13 | wuhaoye | fix(frontend): 登录后重载控件包，仪表盘不再删除未注册控件的布局（M-01） |
| 106 | `ebd8f99` | 2026-09-13 | wuhaoye | fix(frontend): 登出清空请求层内存 memo，换账号不再串档（F-06） |
| 107 | `14d29b1` | 2026-09-13 | wuhaoye | fix(frontend): 时间显示改用本地时区，不再少 8 小时（F-08） |
| 108 | `ac4e857` | 2026-09-13 | wuhaoye | fix(frontend): 级联删除确认文案转义数据名称，消除存储型 XSS（F-02） |
| 109 | `c932448` | 2026-09-13 | wuhaoye | fix(frontend): blob 下载与非信封 2xx 不再被判为失败（F-01） |
| 110 | `dde6cbb` | 2026-09-13 | wuhaoye | fix(frontend): 列表不再被默认 pageSize=50 静默截断（A-01/V-04/W-05） |
| 111 | `c6b459e` | 2026-09-13 | wuhaoye | fix(contracts): 合同执行补审计留痕（QuerySet.update 绕过信号审计）（C-04） |
| 112 | `9e51fa2` | 2026-09-13 | wuhaoye | fix(audit): 含 Decimal 的写操作不再丢审计（C-02） |
| 113 | `ea42d99` | 2026-09-13 | wuhaoye | fix(users): permissions=null 真正「按角色继承」，并区分显式零权限（I-19） |
| 114 | `f240dc7` | 2026-09-13 | wuhaoye | fix(permissions): 扩展集权限支持「超管显式放开」，不再永远授不出去（I-13） |
| 115 | `5be9ed2` | 2026-09-13 | wuhaoye | fix(stock): 下单/撤单按比赛隔离，高级管理也不能跨比赛操作（S-06） |
| 116 | `62ada3b` | 2026-09-13 | wuhaoye | fix(stock): bindFieldId 改派补上「仅高级管理」校验，堵住任意充值（S-05） |
| 117 | `28edcbf` | 2026-09-13 | wuhaoye | fix(auth): 登录限流键口径统一、兼容表单提交、限流表加容量上限（C-01/I-05） |
| 118 | `9024ff5` | 2026-09-13 | wuhaoye | fix(realtime): 顶号真正断开旧 Socket.IO 连接，而不只是发通知（I-01） |
| 119 | `36b373d` | 2026-09-13 | wuhaoye | fix(users): 管理员重置密码/禁用账号时吊销 token 并断开 socket（I-03） |
| 120 | `5eff27b` | 2026-09-13 | wuhaoye | fix(auth): 禁用账号的已签发 token 与 WebSocket 连接立即失效（I-02） |
| 121 | `ddc86b4` | 2026-09-13 | wuhaoye | fix(contracts/builder): 重复 build() 不再丢实体槽位（D-09） |
| 122 | `9e7d44f` | 2026-09-13 | wuhaoye | fix(maps,tech_tree): 拒绝非有限浮点入库，渲染层兜底为 null（D-08） |
| 123 | `f9e39a1` | 2026-09-13 | wuhaoye | fix(contracts): 脏 companyId 不再打挂合同列表；创建时强制 companyId 为整数（D-07） |
| 124 | `49574cd` | 2026-09-13 | wuhaoye | fix(contracts): 公式/算子的数学域错误转业务错误，未定义变量不再静默取 0（D-04） |
| 125 | `194f9ee` | 2026-09-13 | wuhaoye | fix(stock): 撮合主循环在「量化归 0」时不再死循环（D-03） |
| 126 | `8f3da55` | 2026-09-13 | wuhaoye | fix(stock): 撮合「零成交轮次」的 K 线构建 TypeError（D-03 相邻缺陷，根因独立） |
| 127 | `da9ba47` | 2026-09-13 | wuhaoye | fix(contracts): 合同参与方公司按比赛隔离，禁止跨比赛读写他人公司字段（D-02） |
| 128 | `268bd64` | 2026-09-13 | wuhaoye | fix(money): 拒绝会被 SQLite 静默截断的高精度 Decimal 写入（D-01） |
| 129 | `0d38192` | 2026-09-13 | wuhaoye | docs(contracts): 新增《合同可视化新建操作指南》全流程文档 |
| 130 | `622a748` | 2026-09-13 | wuhaoye | chore(gitignore): 忽略 contract_watcher 测试输出目录中生成的 xlsx |
| 131 | `8b9dea0` | 2026-09-13 | wuhaoye | feat(contract-watcher): 新增合同通过监听程序（独立运行，不改动现有服务） |
| 132 | `a6feab9` | 2026-09-13 | wuhaoye | feat(excel): 按「框架进表格、绑公司/人/主键的不进」为股票系统加「股票参数」表 |
| 133 | `427b261` | 2026-09-13 | wuhaoye | refactor(excel): 合同类型改为「只引用代码脚本」，表格不再手写四份 JSON |
| 134 | `3011ef8` | 2026-09-13 | wuhaoye | refactor(excel): 表格规范收窄为「比赛框架」，运行数据不再进 Excel |
| 135 | `7fec2bd` | 2026-09-13 | wuhaoye | feat(excel): 表格表头中文化 + 新增最小示例与《比赛 Excel 建包教程》 |
| 136 | `03dd8f1` | 2026-09-13 | wuhaoye | feat(excel): 比赛建包表格规范（Excel/CSV 读表建比赛，一表一内容） |
| 137 | `3aed64e` | 2026-09-13 | wuhaoye | test(preparation): 汽车产业链测试赛建包与三大合同类型（开采/购销/运输） |
| 138 | `408de7e` | 2026-09-13 | wuhaoye | feat(contracts): 合同类型代码化建库（降低 effect 与 ENTITY 抽象） |
| 139 | `73e8e46` | 2026-09-13 | wuhaoye | feat(preparation): 比赛建包库 + build_competition 命令 |
| 140 | `602eab1` | 2026-09-11 | wuhaoye | feat(preparation): 比赛准备清单、分组导出导入与开赛前体检 |
| 141 | `3dfc731` | 2026-09-11 | wuhaoye | fix(scripts): 修复 Windows 开发启动 Ctrl+C 卡死、服务停不掉的问题 |

### 4.2 合并提交（7 个）

| 提交 | 日期 | 作者 | 说明 |
| --- | --- | --- | --- |
| `35af9be` | 2026-09-15 | wuhaoye | Merge origin/master（16 个提交）into bugfix-merged —— 冲突按「以 master 为主，未被改掉者保留」解决 |
| `61b1fd5` | 2026-09-15 | bing_de | Merge branch 'master' of https://github.com/bingdexzx/GipfelBusinessCompetitionManagerWeb |
| `15fb9e5` | 2026-09-14 | Bingde | Merge pull request #2 from wuhaoye/bugfix |
| `8bd1d76` | 2026-09-13 | wuhaoye | merge(bugfix): 补记 bugfix 分支已并入（内容与 feature_wuhaoye 等价，无文件变更） |
| `a4aac46` | 2026-09-12 | wuhaoye | Merge branch 'feature/prep-export-import' into bugfix |
| `2605e24` | 2026-09-12 | wuhaoye | Merge branch 'bugfix' into feature_wuhaoye |
| `d8d5085` | 2026-09-12 | wuhaoye | Merge branch 'feature/prep-export-import' into feature_wuhaoye |

---

## 5. 文件变更明细

### 5.1 修改的文件（75 个）

按改动量降序。这些是「既有代码被改动」的全部清单，建议逐项评审。

| 文件 | + | - |
| --- | ---: | ---: |
| `scripts/deploy-linux.sh` | 568 | 60 |
| `scripts/update-from-github.sh` | 482 | 59 |
| `deploy/README.md` | 442 | 17 |
| `scripts/migrate-server.sh` | 221 | 53 |
| `scripts/quick-sync.sh` | 244 | 11 |
| `deploy/nginx-gipfel.conf` | 173 | 18 |
| `tests/gipfel-logviewer-diag.sh` | 155 | 20 |
| `tests/deploy_public_ip_test.sh` | 112 | 58 |
| `tests/big_number_smoke.py` | 92 | 73 |
| `scripts/start-dev.bat` | 39 | 94 |
| `frontend/src/types/api.ts` | 132 | 0 |
| `tests/sqlite_decimal_roundtrip.py` | 106 | 20 |
| `frontend/src/components/dashboard/registerCustomWidgets.ts` | 77 | 45 |
| `docs/OPS.md` | 92 | 2 |
| `backend/apps/common/middleware.py` | 78 | 8 |
| `scripts/gen_logviewer_key.py` | 69 | 17 |
| `frontend/src/api/request.ts` | 45 | 38 |
| `backend/apps/contracts/views.py` | 67 | 15 |
| `backend/apps/contracts/engine.py` | 65 | 7 |
| `scripts/verify-migration.sh` | 60 | 3 |
| `backend/apps/contracts/serializers.py` | 63 | 0 |
| `frontend/src/api/index.ts` | 61 | 0 |
| `backend/apps/users/views.py` | 45 | 9 |
| `scripts/bootstrap-dev.bat` | 41 | 13 |
| `frontend/src/views/settings/SettingsView.vue` | 42 | 1 |
| `frontend/src/views/dashboard/DashboardView.vue` | 31 | 11 |
| `backend/apps/realtime/emit.py` | 37 | 0 |
| `deploy/gipfel.service` | 26 | 9 |
| `backend/apps/stock/models.py` | 18 | 16 |
| `frontend/src/views/stocks/StockMarketView.vue` | 12 | 22 |
| `frontend/src/stores/auth.ts` | 29 | 4 |
| `backend/apps/competitions/views.py` | 27 | 2 |
| `backend/apps/common/permissions.py` | 25 | 4 |
| `frontend/src/views/data-management/ContractManageView.vue` | 26 | 2 |
| `.gitignore` | 28 | 0 |
| `backend/apps/common/exceptions.py` | 23 | 5 |
| `deploy/logviewer.service` | 18 | 7 |
| `backend/apps/auth/authentication.py` | 19 | 4 |
| `backend/apps/stock/engine.py` | 21 | 1 |
| `frontend/src/views/data-management/VehiclesManager.vue` | 16 | 5 |
| `backend/logviewer/logviewer/settings.py` | 21 | 0 |
| `backend/apps/stock/views.py` | 15 | 4 |
| `frontend/src/views/data-management/PartsManager.vue` | 17 | 2 |
| `frontend/src/views/data-management/ProductsManager.vue` | 17 | 2 |
| `frontend/src/contracts/graph-model.ts` | 7 | 12 |
| `backend/backend/settings.py` | 16 | 1 |
| `backend/apps/auth/views.py` | 11 | 5 |
| `backend/apps/common/response.py` | 13 | 3 |
| `frontend/src/utils/format.ts` | 12 | 3 |
| `backend/apps/competitions/models.py` | 15 | 0 |
| `frontend/src/utils/deleteConfirm.ts` | 5 | 7 |
| `frontend/src/views/data-management/MapsManager.vue` | 10 | 1 |
| `frontend/src/views/data-management/WarehousesManager.vue` | 10 | 1 |
| `frontend/src/views/data-management/TechTreeManager.vue` | 10 | 1 |
| `frontend/index.html` | 10 | 1 |
| `backend/apps/users/models.py` | 10 | 1 |
| `README.md` | 8 | 2 |
| `frontend/src/views/data-management/FuelManager.vue` | 9 | 1 |
| `docs/MIGRATION.md` | 6 | 2 |
| `backend/apps/maps/serializers.py` | 5 | 3 |
| `backend/apps/competitions/urls.py` | 6 | 1 |
| `backend/apps/common/renderers.py` | 5 | 1 |
| `backend/apps/competitions/apps.py` | 6 | 0 |
| `backend/apps/production_lines/models.py` | 4 | 2 |
| `backend/apps/warehouses/models.py` | 4 | 2 |
| `backend/apps/infrastructures/models.py` | 4 | 2 |
| `backend/apps/tech_tree/serializers.py` | 4 | 2 |
| `frontend/src/views/login/LoginView.vue` | 4 | 1 |
| `backend/apps/realtime/gateway.py` | 4 | 0 |
| `backend/apps/vehicles/models.py` | 3 | 1 |
| `backend/apps/fuels/models.py` | 3 | 1 |
| `backend/apps/common/audit.py` | 2 | 1 |
| `docs/Vue-Django迁移设计.md` | 1 | 1 |
| `backend/backend/urls.py` | 2 | 0 |
| `backend/README.md` | 1 | 1 |

### 5.2 新增的文件（270 个）

按目录汇总：

| 目录前缀 | 新增文件数 |
| --- | ---: |
| `tests/fix_verify` | 74 |
| `backend/tests_fix_verify` | 51 |
| `contract_watcher/bookkeeping_example` | 43 |
| `backend/apps` | 34 |
| `backend/examples` | 16 |
| `contract_watcher` | 12 |
| `frontend/src` | 10 |
| `contract_watcher/test_run_recheck` | 8 |
| `docs` | 7 |
| `(仓库根目录)` | 5 |
| `frontend/public` | 4 |
| `scripts` | 3 |
| `tests` | 2 |
| `scripts/lib` | 1 |

<details>
<summary>展开查看 270 个新增文件的完整路径</summary>

- `.gitattributes`
- `backend/apps/common/drf_fields.py`
- `backend/apps/common/fields.py`
- `backend/apps/competitions/signals.py`
- `backend/apps/contracts/builder/__init__.py`
- `backend/apps/contracts/builder/aggregates.py`
- `backend/apps/contracts/builder/builder.py`
- `backend/apps/contracts/builder/effects.py`
- `backend/apps/contracts/builder/errors.py`
- `backend/apps/contracts/builder/refs.py`
- `backend/apps/contracts/builder/validate.py`
- `backend/apps/contracts/builder/values.py`
- `backend/apps/contracts/management/__init__.py`
- `backend/apps/contracts/management/commands/__init__.py`
- `backend/apps/contracts/management/commands/build_contract_types.py`
- `backend/apps/contracts/tests/__init__.py`
- `backend/apps/contracts/tests/test_complex_contracts.py`
- `backend/apps/contracts/tests/test_named_effect_semantics.py`
- `backend/apps/contracts/tests/test_type_builder.py`
- `backend/apps/preparation/__init__.py`
- `backend/apps/preparation/apps.py`
- `backend/apps/preparation/archive.py`
- `backend/apps/preparation/builder/__init__.py`
- `backend/apps/preparation/builder/core.py`
- `backend/apps/preparation/builder/schema.py`
- `backend/apps/preparation/builder/types.py`
- `backend/apps/preparation/checklist.py`
- `backend/apps/preparation/management/__init__.py`
- `backend/apps/preparation/management/commands/__init__.py`
- `backend/apps/preparation/management/commands/build_competition.py`
- `backend/apps/preparation/plan.py`
- `backend/apps/preparation/tests/__init__.py`
- `backend/apps/preparation/tests/test_builder.py`
- `backend/apps/preparation/urls.py`
- `backend/apps/preparation/views.py`
- `backend/examples/competitions/auto_chain_competition.py`
- `backend/examples/competitions/auto_chain_setup.py`
- `backend/examples/competitions/auto_chain_smoke.py`
- `backend/examples/competitions/demo_competition.py`
- `backend/examples/contracts/auto_chain_contracts.py`
- `backend/examples/contracts/complex_contracts.py`
- `backend/examples/contracts/demo_contracts.py`
- `backend/examples/contracts/mini_contracts.py`
- `backend/examples/excel/build_from_sheets.py`
- `backend/examples/excel/make_sample_auto_chain.py`
- `backend/examples/excel/make_template.py`
- `backend/examples/excel/sheet_spec.py`
- `backend/examples/excel/xlsx_io.py`
- `backend/examples/excel/比赛建包模板.xlsx`
- `backend/examples/excel/汽车产业链示例.xlsx`
- `backend/examples/excel/最小示例.xlsx`
- `backend/tests_fix_verify/__init__.py`
- `backend/tests_fix_verify/test_c01_login_throttle.py`
- `backend/tests_fix_verify/test_c02_audit_decimal.py`
- `backend/tests_fix_verify/test_c04_contract_execute_audit.py`
- `backend/tests_fix_verify/test_d01_decimal_exact.py`
- `backend/tests_fix_verify/test_d02_party_competition_scope.py`
- `backend/tests_fix_verify/test_d03_stock_match_terminates.py`
- `backend/tests_fix_verify/test_d03b_candle_zero_fill.py`
- `backend/tests_fix_verify/test_d04_formula_domain_errors.py`
- `backend/tests_fix_verify/test_d07_party_company_id.py`
- `backend/tests_fix_verify/test_d08_non_finite_floats.py`
- `backend/tests_fix_verify/test_d09_builder_entity_slots.py`
- `backend/tests_fix_verify/test_fy01_signals.py`
- `backend/tests_fix_verify/test_i01_kick_on_relogin.py`
- `backend/tests_fix_verify/test_i02_inactive_account.py`
- `backend/tests_fix_verify/test_i03_admin_action_revokes.py`
- `backend/tests_fix_verify/test_i04_must_change_password_gate.py`
- `backend/tests_fix_verify/test_i13_grant_extras.py`
- `backend/tests_fix_verify/test_i19_null_permissions.py`
- `backend/tests_fix_verify/test_r01_archive_import_savepoint.py`
- `backend/tests_fix_verify/test_r02_contract_instances_append.py`
- `backend/tests_fix_verify/test_r03_contract_instances_natural_key.py`
- `backend/tests_fix_verify/test_r04_users_append.py`
- `backend/tests_fix_verify/test_r05_r06_r07_import_meta_users_messages.py`
- `backend/tests_fix_verify/test_r08_overview_cards_mode.py`
- `backend/tests_fix_verify/test_r09_duplicate_company_names.py`
- `backend/tests_fix_verify/test_r10_overwrite_stock_accounts.py`
- `backend/tests_fix_verify/test_r10b_overwrite_maps_regions.py`
- `backend/tests_fix_verify/test_r11_r12_archive_limits.py`
- `backend/tests_fix_verify/test_r13_r14_demands_plan.py`
- `backend/tests_fix_verify/test_s05_bind_field_permission.py`
- `backend/tests_fix_verify/test_s06_order_competition_scope.py`
- `backend/tests_fix_verify/test_w02_vehicle_path_types.py`
- `backend/tests_fix_verify/test_z01_excel_comment_rows.py`
- `backend/tests_fix_verify/test_z02_excel_numeric_bounds.py`
- `backend/tests_fix_verify/test_z03_excel_fullwidth_digits.py`
- `backend/tests_fix_verify/test_z04_excel_stock_config.py`
- `backend/tests_fix_verify/test_z05_excel_file_errors.py`
- `backend/tests_fix_verify/test_z06_excel_create_competition_reuse.py`
- `backend/tests_fix_verify/test_z07_excel_out_backup.py`
- `backend/tests_fix_verify/test_z08_excel_inspect_scripts.py`
- `backend/tests_fix_verify/test_z09_excel_dry_run.py`
- `backend/tests_fix_verify/test_z10_excel_xml_control_chars.py`
- `backend/tests_fix_verify/test_z11_excel_sheet_names.py`
- `backend/tests_fix_verify/test_z12_empty_and_hidden.py`
- `backend/tests_fix_verify/test_z12_excel_error_values.py`
- `backend/tests_fix_verify/test_z13_excel_error_aggregation.py`
- `backend/tests_fix_verify/test_z14_transport_checks.py`
- `backend/tests_fix_verify/test_z15_numeric_text.py`
- `backend/tests_fix_verify/test_z16_excel_csv_names.py`
- `backend/tests_fix_verify/test_z17_doc_sheet_counts.py`
- `contract_watcher/bookkeeping.py`
- `contract_watcher/bookkeeping_example/inspect_xlsx.py`
- `contract_watcher/bookkeeping_example/run_branch_tests.py`
- `contract_watcher/bookkeeping_example/shang.py`
- `contract_watcher/bookkeeping_example/soak_test.py`
- `contract_watcher/bookkeeping_example/target.old-20260910.xlsx`
- `contract_watcher/bookkeeping_example/target.xlsx`
- `contract_watcher/bookkeeping_example/test_output/logs/00_probe.json`
- `contract_watcher/bookkeeping_example/test_output/logs/00_probe.log`
- `contract_watcher/bookkeeping_example/test_output/logs/01_error_path.json`
- `contract_watcher/bookkeeping_example/test_output/logs/01_error_path.log`
- `contract_watcher/bookkeeping_example/test_output/logs/05_usedrange.json`
- `contract_watcher/bookkeeping_example/test_output/logs/05_usedrange.log`
- `contract_watcher/bookkeeping_example/test_output/logs/10_bank_entries.json`
- `contract_watcher/bookkeeping_example/test_output/logs/10_bank_entries.log`
- `contract_watcher/bookkeeping_example/test_output/logs/20_item_raw.json`
- `contract_watcher/bookkeeping_example/test_output/logs/20_item_raw.log`
- `contract_watcher/bookkeeping_example/test_output/logs/40_assets.json`
- `contract_watcher/bookkeeping_example/test_output/logs/40_assets.log`
- `contract_watcher/bookkeeping_example/test_output/logs/50_check_branches.json`
- `contract_watcher/bookkeeping_example/test_output/logs/50_check_branches.log`
- `contract_watcher/bookkeeping_example/test_output/logs/soak_20260910_224745.jsonl`
- `contract_watcher/bookkeeping_example/test_output/logs/soak_idle_20260910_230107.jsonl`
- `contract_watcher/bookkeeping_example/test_output/logs/soak_idle_debug_20260910_225230.jsonl`
- `contract_watcher/bookkeeping_example/test_output/logs/soak_scratch_20260910_224916.jsonl`
- `contract_watcher/bookkeeping_example/test_output/logs/soak_scratch_debug_20260910_225101.jsonl`
- `contract_watcher/bookkeeping_example/test_output/logs/soak_scratch_debug_20260910_225951.jsonl`
- `contract_watcher/bookkeeping_example/test_output/SOAK_REPORT.md`
- `contract_watcher/bookkeeping_example/test_output/TEST_REPORT_v2.md`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/00_probe.json`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/00_probe.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/01_error_path.json`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/01_error_path.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/05_usedrange.json`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/05_usedrange.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/10_bank_entries.json`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/10_bank_entries.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/20_item_raw.json`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/20_item_raw.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/50_check_branches.json`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/50_check_branches.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/probe_find_attribution.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/logs/probe_item_steps.log`
- `contract_watcher/bookkeeping_example/test_output_oldtarget_EEF6E4/TEST_REPORT.md`
- `contract_watcher/contract_watcher.py`
- `contract_watcher/DATA_GUIDE.md`
- `contract_watcher/gui.py`
- `contract_watcher/handlers.py`
- `contract_watcher/readable.py`
- `contract_watcher/README.md`
- `contract_watcher/selftest.py`
- `contract_watcher/start_gui.bat`
- `contract_watcher/store.py`
- `contract_watcher/TEST_FLOW.md`
- `contract_watcher/test_run_recheck/handlers.before.py`
- `contract_watcher/test_run_recheck/handlers.generated_after_test.py`
- `contract_watcher/test_run_recheck/records/e2e_watcher_recheck/contract_3_20260910_232021.json`
- `contract_watcher/test_run_recheck/records/e2e_watcher_recheck/contract_3_20260910_232021_readable.json`
- `contract_watcher/test_run_recheck/records/e2e_watcher_recheck_v2/contract_4_20260910_232048.json`
- `contract_watcher/test_run_recheck/records/e2e_watcher_recheck_v2/contract_4_20260910_232048_readable.json`
- `contract_watcher/test_run_recheck/REPORT.md`
- `contract_watcher/test_run_recheck/watcher.log`
- `contract_watcher/watcher_config.py`
- `docs/BUILD_COMPETITION_API_REFERENCE.md`
- `docs/BUILD_COMPETITION_BY_CODE.md`
- `docs/CONTRACT_TYPE_BY_CODE.md`
- `docs/比赛Excel建包规范.md`
- `docs/比赛Excel建包教程.md`
- `docs/合同可视化新建操作指南.md`
- `docs/汽车产业链测试赛准备.md`
- `frontend/public/apple-touch-icon.png`
- `frontend/public/favicon.ico`
- `frontend/public/favicon-16.png`
- `frontend/public/favicon-32.png`
- `frontend/src/api/envelope.ts`
- `frontend/src/api/localPaging.ts`
- `frontend/src/api/responseMemo.ts`
- `frontend/src/components/dashboard/layoutStorage.ts`
- `frontend/src/components/preparation/PreparationImportDialog.vue`
- `frontend/src/components/preparation/PreparationOverviewDialog.vue`
- `frontend/src/composables/useLatestRequest.ts`
- `frontend/src/utils/deleteConfirmMessage.ts`
- `frontend/src/views/data-management/vehiclePathTypes.ts`
- `frontend/src/views/stocks/estAmount.ts`
- `scripts/dev.py`
- `scripts/lib/deploy-common.sh`
- `scripts/make_favicon.py`
- `scripts/stop-dev.bat`
- `tests/_bootstrap.py`
- `tests/fix_verify/frontend/bundle.ps1`
- `tests/fix_verify/frontend/entries/f06_entry.mjs`
- `tests/fix_verify/frontend/entries/m01_boot.mjs`
- `tests/fix_verify/frontend/entries/m01_entry.mjs`
- `tests/fix_verify/frontend/stubs/browser.mjs`
- `tests/fix_verify/frontend/stubs/element-plus.mjs`
- `tests/fix_verify/frontend/test_contract_tech_nodes_scope.mjs`
- `tests/fix_verify/frontend/test_delete_confirm_message.mjs`
- `tests/fix_verify/frontend/test_f06_logout_clears_memo.mjs`
- `tests/fix_verify/frontend/test_f12_page_bounds.mjs`
- `tests/fix_verify/frontend/test_format_time.mjs`
- `tests/fix_verify/frontend/test_list_paging.mjs`
- `tests/fix_verify/frontend/test_m01_widget_layout_preserved.mjs`
- `tests/fix_verify/frontend/test_m01_widget_packages_reload.mjs`
- `tests/fix_verify/frontend/test_response_envelope.mjs`
- `tests/fix_verify/frontend/test_t01_est_amount.mjs`
- `tests/fix_verify/frontend/test_t02_double_submit.mjs`
- `tests/fix_verify/frontend/test_v05_entity_cache_scope.mjs`
- `tests/fix_verify/frontend/test_v09_list_race_guards.mjs`
- `tests/fix_verify/frontend/test_w02_vehicle_path_types.mjs`
- `tests/fix_verify/frontend/test_w03_option_competition_scope.mjs`
- `tests/fix_verify/frontend/test_w08_warehouses_race.mjs`
- `tests/fix_verify/frontend/test_x31_forced_password_change_flow.mjs`
- `tests/fix_verify/scripts/_decoding.py`
- `tests/fix_verify/scripts/test_b01_stop_safety.py`
- `tests/fix_verify/scripts/test_x01_x09_verify_migration.py`
- `tests/fix_verify/scripts/test_x02_x04_migrate_server.py`
- `tests/fix_verify/scripts/test_x05_quick_sync.py`
- `tests/fix_verify/scripts/test_x06_x08_deploy_update.py`
- `tests/fix_verify/scripts/test_x10_rollback_path.py`
- `tests/fix_verify/scripts/test_x11_x12_env_integrity.py`
- `tests/fix_verify/scripts/test_x13_x14_units_diag.py`
- `tests/fix_verify/scripts/test_x15_public_ip_test.py`
- `tests/fix_verify/scripts/test_x16_no_pseudo_tests.py`
- `tests/fix_verify/scripts/test_x17_db_safety.py`
- `tests/fix_verify/scripts/test_x18_bootstrap_guard.py`
- `tests/fix_verify/scripts/test_x19_start_dev_exit.py`
- `tests/fix_verify/scripts/test_x20_install_dir.py`
- `tests/fix_verify/scripts/test_x21_migrate_dryrun.py`
- `tests/fix_verify/scripts/test_x22_secret_logging.py`
- `tests/fix_verify/scripts/test_x23_daphne_socket.py`
- `tests/fix_verify/scripts/test_x24_deploy_exit_code.py`
- `tests/fix_verify/scripts/test_x25_ssh_options.py`
- `tests/fix_verify/scripts/test_x26_readme_rollback.py`
- `tests/fix_verify/scripts/test_x30_runtime_user.py`
- `tests/fix_verify/watcher/_cw_helpers.py`
- `tests/fix_verify/watcher/e2e_watcher_backend.py`
- `tests/fix_verify/watcher/e2e_watcher_excel.py`
- `tests/fix_verify/watcher/test_cw01_cw02.py`
- `tests/fix_verify/watcher/test_cw04_cw05_watermark.py`
- `tests/fix_verify/watcher/test_cw06_handler_collision.py`
- `tests/fix_verify/watcher/test_cw07_generated_code_safety.py`
- `tests/fix_verify/watcher/test_cw08_cw09_bookkeeping.py`
- `tests/fix_verify/watcher/test_cw10_auth_failure_exit.py`
- `tests/fix_verify/watcher/test_cw11_competition_arg.py`
- `tests/fix_verify/watcher/test_cw12_incremental_polling.py`
- `tests/fix_verify/watcher/test_cw13_state_atomic.py`
- `tests/fix_verify/watcher/test_cw14_amount_conversion.py`
- `tests/fix_verify/watcher/test_cw15_readable_format.py`
- `tests/fix_verify/watcher/test_cw16_slot_detection.py`
- `tests/fix_verify/watcher/test_cw17_business_date.py`
- `tests/fix_verify/watcher/test_cw18_subject_lookup.py`
- `tests/fix_verify/watcher/test_cw19_heartbeat_watchdog.py`
- `tests/fix_verify/watcher/test_cw20_shared_lock.py`
- `tests/fix_verify/watcher/test_cw21_exit_codes.py`
- `tests/fix_verify/watcher/test_cw22_branch_tests_safety.py`
- `tests/fix_verify/watcher/test_cw23_archive_name_collision.py`
- `tests/fix_verify/watcher/test_cw24_safe_dirname.py`
- `tests/fix_verify/watcher/test_cw25_rename_substring.py`
- `tests/fix_verify/watcher/test_cw27_missing_executed_at.py`
- `tests/fix_verify/watcher/test_cw30_store.py`
- `tests/fix_verify/watcher/test_cw31_bookkeeping_batch.py`
- `tests/fix_verify/watcher/test_cw32_watcher_workflow.py`
- `tests/fix_verify/watcher/test_cw33_gui_smoke.py`
- `tests/https-443-diag.sh`
- `WSL与生产环境验证操作手册.md`
- `WSL与生产环境验证结果记录.md`
- `分支代码缺陷审计报告.md`
- `浮点精度检测报告.md`

</details>

### 5.3 重命名的文件（12 个，相似度均为 75%+，无内容丢失）

| 相似度 | 原路径 | 新路径 |
| --- | --- | --- |
| R087 | `tests/test_callback_mechanism.py` | `tests/explore_callback_mechanism.py` |
| R075 | `tests/test_fix.py` | `tests/explore_fix.py` |
| R087 | `tests/test_improved_mm.py` | `tests/explore_improved_mm.py` |
| R089 | `tests/test_kline.py` | `tests/explore_kline.py` |
| R085 | `tests/test_market_maker.py` | `tests/explore_market_maker.py` |
| R089 | `tests/test_natural_fluctuation.py` | `tests/explore_natural_fluctuation.py` |
| R089 | `tests/test_parameter_fluctuation.py` | `tests/explore_parameter_fluctuation.py` |
| R092 | `tests/test_reduced_intervention.py` | `tests/explore_reduced_intervention.py` |
| R089 | `tests/test_relative_ranking.py` | `tests/explore_relative_ranking.py` |
| R091 | `tests/test_solution2.py` | `tests/explore_solution2.py` |
| R077 | `tests/test_stock_ui.js` | `tests/explore_stock_ui.js` |
| R087 | `tests/test_user_volume.py` | `tests/explore_user_volume.py` |

### 5.4 删除的文件

**无**（0 个删除）。master 上的任何文件都没有被移除。

### 5.5 二进制 / 样例数据文件（9 个，均为新增）

| 文件 | 说明 |
| --- | --- |
| `backend/examples/excel/比赛建包模板.xlsx` | Excel 建包样例或模板 |
| `backend/examples/excel/汽车产业链示例.xlsx` | Excel 建包样例或模板 |
| `backend/examples/excel/最小示例.xlsx` | Excel 建包样例或模板 |
| `contract_watcher/bookkeeping_example/target.old-20260910.xlsx` | 样例账本 / 实测产物，入库前请确认不含真实业务数据 |
| `contract_watcher/bookkeeping_example/target.xlsx` | 样例账本 / 实测产物，入库前请确认不含真实业务数据 |
| `frontend/public/apple-touch-icon.png` | 站点图标（配合 scripts/make_favicon.py 生成） |
| `frontend/public/favicon.ico` | 站点图标（配合 scripts/make_favicon.py 生成） |
| `frontend/public/favicon-16.png` | 站点图标（配合 scripts/make_favicon.py 生成） |
| `frontend/public/favicon-32.png` | 站点图标（配合 scripts/make_favicon.py 生成） |

---

## 6. 缺陷编号索引

提交主题中登记了缺陷编号，共 122 个编号、132 处登记。前缀含义：`X`=仓库/脚本综合审计项，`B01`=后端高危，`Z`=Excel 建包，`R`=导入/导出，`CW`=contract_watcher，`D`=数据/领域正确性，`I`=身份与权限，`S`=股票，`C`=审计/认证，`F/V/W/T/M/A`=前端各模块。

| 编号 | 主题（截断） | 提交 |
| --- | --- | --- |
| `X-01` | fix(scripts): 迁移验证不再静默中止，并真正校验数据库与日志查看器（X-01 + X-09） | `39f1465` |
| `X-02` | fix(scripts): 迁移脚本的密钥落盘、root 注入与静默丢库（X-02 + X-03 + X-04） | `edd966b` |
| `X-03` | fix(scripts): 迁移脚本的密钥落盘、root 注入与静默丢库（X-02 + X-03 + X-04） | `edd966b` |
| `X-04` | fix(scripts): 迁移脚本的密钥落盘、root 注入与静默丢库（X-02 + X-03 + X-04） | `edd966b` |
| `X-05` | fix(scripts): quick-sync 不再直接同步活库，改一致性快照并双向校验（X-05） | `980c13d` |
| `X-06` | fix(scripts): NodeSource 不再 curl\|bash、pull 失败默认中止、日志查看器端口统一（… | `cc6e04c` |
| `X-07` | fix(scripts): NodeSource 不再 curl\|bash、pull 失败默认中止、日志查看器端口统一（… | `cc6e04c` |
| `X-08` | fix(scripts): NodeSource 不再 curl\|bash、pull 失败默认中止、日志查看器端口统一（… | `cc6e04c` |
| `X-09` | fix(scripts): 迁移验证不再静默中止，并真正校验数据库与日志查看器（X-01 + X-09） | `39f1465` |
| `X-10` | fix(scripts): 部署/升级脚本补一致性回滚副本与"migrate 后失败"的回滚路径（X-10） | `20982ce` |
| `X-11` | fix(repo): 重做被 master 合并覆盖的 X-11/X-14/X-22，并修正三处失真的回归用例（X-29… | `b40fd25` `7659d04` |
| `X-12` | fix(scripts): .env 每个键保持唯一一行，gen_logviewer_key 编码容错（X-11 + X… | `7659d04` |
| `X-13` | fix(deploy): systemd 单元目录权限收紧与诊断脚本密钥掩码（X-13 + X-14） | `503a1fc` |
| `X-14` | fix(repo): 重做被 master 合并覆盖的 X-11/X-14/X-22，并修正三处失真的回归用例（X-29… | `b40fd25` `503a1fc` |
| `X-15` | fix(tests): 公网 IP 单测与调用 cwd 解耦，删除已废弃的交互式 read 镜像分支（X-15） | `7018403` |
| `X-16` | fix(tests): tests/ 顶层 12 个"只打印、恒退出 0"的伪测试改名并显式声明非测试（X-16） | `6e14bf6` |
| `X-17` | fix(tests): 连库脚本改用一次性测试库，导入零副作用，去掉静默 SKIP（X-17，含 M-2） | `89a6f5b` |
| `X-18` | fix(scripts): bootstrap-dev.bat 重入标记改用参数，不再污染父环境也不再杀掉调用方（X-1… | `84e72b7` |
| `X-19` | fix(scripts): start-dev.bat 传递真实启动结果，dev.py 增加同步前置检查（X-19） | `7f2a127` |
| `X-20` | fix(scripts): quick-sync 的 INSTALL_DIR 绝对化并在 push 前校验源目录（X-2… | `a0cf7ff` |
| `X-21` | fix(scripts): migrate-server 的 --dry-run 真正零副作用、退出码分场景、--ssh… | `fa86b5e` |
| `X-22` | fix(repo): 重做被 master 合并覆盖的 X-11/X-14/X-22，并修正三处失真的回归用例（X-29… | `b40fd25` `9deeffb` |
| `X-23` | fix(deploy): gipfel.service 移除没有消费方的 daphne Unix socket（X-23… | `cef251d` |
| `X-24` | fix(scripts): 部署脚本把降级分支折算成退出码，不再"nginx 失败也报部署完成"（X-24） | `296ae6f` |
| `X-25` | fix(scripts): 修正 X-25 误用的 warn（该脚本只有 log_warn），并适配两处受影响的回归用例 | `917e4de` `be79c44` |
| `X-26` | fix(docs): 镜像建议改为按仓库生效并补信任/撤销说明，回滚章节补全代码与数据两侧（X-26） | `bea7ee7` |
| `X-27` | docs: 新增 WSL/生产环境验证手册与结果记录，并在审计报告中登记 X-27 | `5ec6d0a` |
| `X-28` | fix(scripts): 修正 X-28 —— bootstrap-dev.bat 的裸 shift 会吃掉 %0，-… | `e630443` |
| `X-29` | fix(repo): 重做被 master 合并覆盖的 X-11/X-14/X-22，并修正三处失真的回归用例（X-29… | `b40fd25` |
| `X-30` | fix(deploy): 修正运行用户创建路径 —— PATH 缺 sbin 静默失败、-U 与已存在组冲突（X-30） | `2f39918` |
| `X-31` | fix(auth): 修复强制改密被会话心跳打断，并让种子口令在终端显示（X-31） | `5cb3468` |
| `Z-01` | fix(excel): 建包不再把首列以 # 开头的真实数据行当注释丢弃（Z-01） | `170f01a` |
| `Z-02` | fix(excel): 建包数值列补下界校验，负距离/负单价/负配比不再进归档（Z-02） | `97e58f4` |
| `Z-03` | fix(excel): 建包不再静默归一化全角/指数写法的数字（Z-03） | `58a7476` |
| `Z-04` | fix(excel): 股票参数只写一行不再绕过交叉校验（Z-04） | `517eaf0` |
| `Z-05` | fix(excel): 文件级异常翻译成中文提示，不再抛 Python 堆栈（Z-05） | `d021f5d` |
| `Z-06` | fix(excel): 复用同名比赛后 append 导入不再被占用保护挡住（Z-06） | `66a354d` |
| `Z-07` | fix(excel): --out 覆盖已有归档需显式 --force，并显示归档规模（Z-07） | `650118f` |
| `Z-08` | fix(excel): --inspect 纯预览不再执行工作簿指定的脚本（Z-08） | `0ba2f65` |
| `Z-09` | fix(excel): --dry-run 不再先把比赛建进库（Z-09） | `463001c` |
| `Z-10` | fix(excel): 写 xlsx 时剔除 XML 非法控制字符（Z-10） | `0960c89` |
| `Z-11` | fix(excel): 写 xlsx 前校验工作表名（长度/非法字符/重名）（Z-11） | `687094e` |
| `Z-12` | fix(excel): 空表段与隐藏工作表/行都要有明确提示（Z-12 余项） | `f824314` `5a88ec2` |
| `Z-13` | fix(excel): 表格校验一次列出全部错误，不再遇到第一个就中断（Z-13） | `0fd056d` |
| `Z-14` | fix(examples): 运输合同补车次/碳税下界，冒烟真正跑到超重分支（Z-14） | `516392c` |
| `Z-15` | test(excel): 锁死数字文本口径，防回归（Z-15） | `71baf77` |
| `Z-16` | fix(excel): CSV 目录导出前校验表名，不再写出半成品（Z-16） | `6355c57` |
| `Z-17` | docs(excel): 统一建包文档的表数量口径，并说明 --sheets 过滤掉了哪些表（Z-17） | `479a06b` |
| `R-01` | fix(import): 归档导入单资源失败只回滚该资源，不再「整批静默回滚却报成功」（R-01） | `5757039` |
| `R-02` | fix(import): 追加模式不再因「合同类型已存在」丢掉全部预设合同实例（R-02） | `7c98de8` |
| `R-03` | fix(import): 同类型多份合同不再被合并成一条并改名（R-03） | `6465458` |
| `R-04` | fix(import): 追加模式不再因「范围里的公司已存在」丢掉整行账号（R-04） | `dee0db7` |
| `R-05` | fix(import): 比赛状态不覆盖、账号先于消费方导入、消息写入收件人（R-05/R-06/R-07） | `101952f` |
| `R-06` | fix(import): 比赛状态不覆盖、账号先于消费方导入、消息写入收件人（R-05/R-06/R-07） | `101952f` |
| `R-07` | fix(import): 比赛状态不覆盖、账号先于消费方导入、消息写入收件人（R-05/R-06/R-07） | `101952f` |
| `R-08` | fix(import): 追加模式不再覆盖既有区域的概览卡片（R-08） | `bfd9d24` |
| `R-09` | fix(import): 重名公司不再整类导入失败，按名兜底改为确定性绑定并提示（R-09） | `deb7383` |
| `R-10` | fix(import): 覆盖模式更新地图节点与区域说明（R-10 余项） | `bfa4c3a` `1562944` |
| `R-11` | fix(preparation): 归档截断必须可见，非空比赛探针必须补全（R-11 + R-12） | `46bacb5` |
| `R-12` | fix(preparation): 归档截断必须可见，非空比赛探针必须补全（R-11 + R-12） | `46bacb5` |
| `R-13` | fix(preparation): 消费者需求按 (区域,产品) 去重、plan 明细真正应用上限（R-13 + R-1… | `b02401c` |
| `R-14` | fix(preparation): 消费者需求按 (区域,产品) 去重、plan 明细真正应用上限（R-13 + R-1… | `b02401c` |
| `CW-01` | fix(watcher): 进度水位与失败重试，杜绝重复记账与永久静默漏账（CW-01 + CW-02） | `cb01c80` |
| `CW-02` | fix(watcher): 进度水位与失败重试，杜绝重复记账与永久静默漏账（CW-01 + CW-02） | `cb01c80` |
| `CW-04` | fix(watcher): 水位线按时间比较、合同列表按 id 去重（CW-05 + CW-04） | `f593c14` |
| `CW-05` | fix(watcher): 水位线按时间比较、合同列表按 id 去重（CW-05 + CW-04） | `f593c14` |
| `CW-06` | fix(watcher): 自动生成不再屏蔽用户 handler，按命名约定的实现可被注册（CW-06） | `e99d133` |
| `CW-07` | fix(watcher): 生成的 handlers.py 代码转义 + 语法校验 + 原子写（CW-07） | `e9f3082` |
| `CW-08` | fix(watcher): 记账样例不再改全局 Decimal 精度，Excel 打开失败即回收进程（CW-08 + C… | `9f53be5` |
| `CW-09` | fix(watcher): 记账样例不再改全局 Decimal 精度，Excel 打开失败即回收进程（CW-08 + C… | `9f53be5` |
| `CW-10` | fix(watcher): 凭据失效不再无限静默失败，连续 401 达阈值即报错退出（CW-10） | `e6c1f21` |
| `CW-11` | fix(watcher): --competition 非正整数直接拒绝，不再静默跨比赛混记（CW-11） | `c792f8a` |
| `CW-12` | fix(watcher): 增量拉取 + 失败退避 + 类型目录节流（CW-12） | `405f8cd` |
| `CW-13` | fix(watcher): 进度文件原子写 + 损坏留证告警（CW-13） | `15ecd64` |
| `CW-14` | fix(watcher): 记账金额显式校验，不再用裸 float() 静默漏账/丢精度（CW-14） | `ee7a690` |
| `CW-15` | fix(watcher): 可读记录时间本地化带标注、数字展示不再改写（CW-15） | `22c6bfb` |
| `CW-16` | fix(watcher): 货品表槽位判定对齐模板，写不下时报错而不是覆写最后一行（CW-16） | `9a8e47d` |
| `CW-17` | fix(watcher): 账期取合同执行时间，不再一律记成运行当天（CW-17） | `23e135b` |
| `CW-18` | fix(watcher): 科目定位显式校验，Find 未命中不再静默漏账（CW-18） | `08eaee5` |
| `CW-19` | fix(watcher): 主循环心跳与停滞看门狗，Excel 挂起可被发现并自愈（CW-19） | `5c62f0f` |
| `CW-20` | fix(watcher): 单实例互斥改到共享锁上，跨机器不再重复记账（CW-20） | `04e664b` |
| `CW-21` | fix(watcher): 自测/浸泡脚本用退出码表达成败，失败不再伪装成成功（CW-21） | `c2cc3af` |
| `CW-22` | fix(watcher): 实测脚本的运行后哈希真算、Excel 回收不再误杀（CW-22） | `b2b2b54` |
| `CW-23` | fix(watcher): 同秒内重复存档不再互相覆盖（CW-23） | `22822db` |
| `CW-24` | fix(watcher): 输出子目录名不再能越出 out_dir（CW-24） | `6092d04` |
| `CW-25` | fix(watcher): 类型改名只改自己那个函数，不再子串替换误伤同前缀的 key（CW-25） | `1c0d0f6` |
| `CW-26` | chore(watcher): 运行产物纳入 .gitignore，避免业务合同与日志入库（CW-26） | `bf46be1` |
| `CW-27` | fix(watcher): EXECUTED 但缺 executedAt 的合同必须告警，不再静默忽略（CW-27） | `129b651` |
| `D-01` | fix(scripts): stop-dev 不再按端口/PID 盲杀，改用可校验身份（B01 D-01 + D-02） | `e6153db` `268bd64` |
| `D-02` | fix(scripts): stop-dev 不再按端口/PID 盲杀，改用可校验身份（B01 D-01 + D-02） | `e6153db` `da9ba47` |
| `D-03` | fix(stock): 撮合主循环在「量化归 0」时不再死循环（D-03） | `194f9ee` `8f3da55` |
| `D-04` | fix(contracts): 公式/算子的数学域错误转业务错误，未定义变量不再静默取 0（D-04） | `49574cd` |
| `D-07` | fix(contracts): 脏 companyId 不再打挂合同列表；创建时强制 companyId 为整数（D-0… | `f9e39a1` |
| `D-08` | fix(maps,tech_tree): 拒绝非有限浮点入库，渲染层兜底为 null（D-08） | `9e7d44f` |
| `D-09` | fix(contracts/builder): 重复 build() 不再丢实体槽位（D-09） | `ddc86b4` |
| `I-01` | fix(realtime): 顶号真正断开旧 Socket.IO 连接，而不只是发通知（I-01） | `9024ff5` |
| `I-02` | fix(auth): 禁用账号的已签发 token 与 WebSocket 连接立即失效（I-02） | `5eff27b` |
| `I-03` | fix(users): 管理员重置密码/禁用账号时吊销 token 并断开 socket（I-03） | `36b373d` |
| `I-05` | fix(auth): 登录限流键口径统一、兼容表单提交、限流表加容量上限（C-01/I-05） | `28edcbf` |
| `I-13` | fix(permissions): 扩展集权限支持「超管显式放开」，不再永远授不出去（I-13） | `f240dc7` |
| `I-19` | fix(users): permissions=null 真正「按角色继承」，并区分显式零权限（I-19） | `ea42d99` |
| `S-05` | fix(stock): bindFieldId 改派补上「仅高级管理」校验，堵住任意充值（S-05） | `62ada3b` |
| `S-06` | fix(stock): 下单/撤单按比赛隔离，高级管理也不能跨比赛操作（S-06） | `5be9ed2` |
| `C-01` | fix(auth): 登录限流键口径统一、兼容表单提交、限流表加容量上限（C-01/I-05） | `28edcbf` |
| `C-02` | fix(audit): 含 Decimal 的写操作不再丢审计（C-02） | `9e51fa2` |
| `C-04` | fix(contracts): 合同执行补审计留痕（QuerySet.update 绕过信号审计）（C-04） | `c6b459e` |
| `F-01` | fix(frontend): blob 下载与非信封 2xx 不再被判为失败（F-01） | `c932448` |
| `F-02` | fix(frontend): 级联删除确认文案转义数据名称，消除存储型 XSS（F-02） | `ac4e857` |
| `F-06` | fix(frontend): 登出清空请求层内存 memo，换账号不再串档（F-06） | `ebd8f99` |
| `F-08` | fix(frontend): 时间显示改用本地时区，不再少 8 小时（F-08） | `14d29b1` |
| `F-12` | fix(frontend): 本地分页参数收敛到合法范围，非法 page/pageSize 不再返回错数据（F-12） | `ebdd812` |
| `V-04` | fix(frontend): 列表不再被默认 pageSize=50 静默截断（A-01/V-04/W-05） | `dde6cbb` |
| `V-05` | fix(frontend): 切换比赛时实体下拉与地图/科技缓存一并失效（V-05） | `7f592cc` |
| `V-09` | fix(frontend): 四处列表加载接入请求代次守卫，旧响应不再覆盖新数据（V-09） | `57b7b36` |
| `W-02` | fix(frontend): 载具可通过路径类型改用后端契约字段，配置真正落库（W-02） | `23305e4` |
| `W-03` | fix(frontend): 合同页科技树清单下拉按比赛取数（W-03 同类缺陷） | `2a1478d` `681bc43` |
| `W-05` | fix(frontend): 列表不再被默认 pageSize=50 静默截断（A-01/V-04/W-05） | `dde6cbb` |
| `W-08` | fix(frontend): 仓库列表并发请求加代次守卫，旧响应不再覆盖新数据（W-08） | `a2b9927` |
| `T-01` | fix(frontend): 股票预计金额整数相乘不再错成 0.xx（T-01） | `39716cc` |
| `T-02` | fix(frontend): 股票下单按钮防连点，双击不再产生两笔委托（T-02） | `1ed7e0c` |
| `M-01` | fix(frontend): 登录后重载控件包，仪表盘不再删除未注册控件的布局（M-01） | `2f5819f` |
| `A-01` | fix(frontend): 列表不再被默认 pageSize=50 静默截断（A-01/V-04/W-05） | `dde6cbb` |

---

## 7. 关键代码差异摘录

以下为「既有代码被修改」中语义最关键的若干处真实 diff（完整内容见伴随文件 `master-vs-bugfix-merged.modified.diff`）。其余修改文件的 diff 已按同样口径收录在伴随文件中，这里只做抽样展示。

### 7.1 `backend/apps/auth/authentication.py`

```diff
diff --git a/backend/apps/auth/authentication.py b/backend/apps/auth/authentication.py
index d6da15e..ece640b 100644
--- a/backend/apps/auth/authentication.py
+++ b/backend/apps/auth/authentication.py
@@ -2,7 +2,7 @@
 
 - HS256 签名 + issuer/audience 校验（与原 jwt.module 配置一致）
 - 校验 tokenVersion（顶号下线：payload.tv ≠ user.token_version → 401）
-- 强制改密拦截：must_change_password=true 时除改密接口外全部拒绝
+- 强制改密拦截：must_change_password=true 时除「改密」与「读自身资料」外全部拒绝
 - 暴露 decode_jwt_payload（供 OperatorContextMiddleware 注入上下文，失败不阻断）
 - 暴露 create_jwt（供 LoginView 签发）
 """
@@ -19,8 +19,16 @@ logger = logging.getLogger("gipfel")
 
 _ALGORITHM = "HS256"
 
-# 强制改密放行路径（仅改密接口允许在 must_change_password=true 时通过）
-_CHANGE_PASSWORD_PATHS = ("/api/auth/change-password",)
+# 强制改密放行路径：must_change_password=true 时仅以下接口允许通过。
+# · change-password：改密本身。
+# · me：前端「修改初始密码」弹窗与 20s 会话心跳都要读自己的资料（只读自身信息，
+#   不放大任何业务权限）。改前未放行 → 心跳打 /api/auth/me 收到 401，被前端全局 401
+#   拦截器当成「会话过期」清掉 token，用户随后提交改密必然报「登录已过期」
+#   （真机事故：新部署的超管永远改不了初始密码，等于无法登录）。
+_CHANGE_PASSWORD_PATHS = (
+    "/api/auth/change-password",
+    "/api/auth/me",
+)
 
 
 # ==================== Token 编解码 ====================
@@ -107,6 +115,13 @@ class JWTAuthentication(authentication.BaseAuthentication):
                 "登录已过期，请重新登录", code="invalid_user"
             )
 
+        # 账号已被禁用：立即失效。禁用是管理动作（PATCH /api/users/:id {"isActive": false}），
+        # 若只在登录时校验，被禁用账号仍可用旧 token 访问全部接口直至 token 过期（审计 I-02）。
+        if not getattr(user, "is_active", True):
+            raise exceptions.AuthenticationFailed(
+                "该账号已被禁用，请联系管理员", code="inactive"
+            )
+
         # 顶号下线：token 中 tv 与用户当前 token_version 不一致
         if payload.get("tv") != user.token_version:
             raise exceptions.AuthenticationFailed(
@@ -151,6 +166,6 @@ class JWTAuthentication(authentication.BaseAuthentication):
 
 
 def _is_change_password_endpoint(request) -> bool:
-    """判断当前请求是否指向改密接口（路径尾匹配，兼容 include 前缀）。"""
+    """判断当前请求是否落在「强制改密期间仍需放行」的路径上（尾匹配，兼容 include 前缀）。"""
     path = request.path or ""
     return path.endswith(_CHANGE_PASSWORD_PATHS)
```

### 7.2 `backend/apps/common/middleware.py`

```diff
diff --git a/backend/apps/common/middleware.py b/backend/apps/common/middleware.py
index 8a89641..ee141ba 100644
--- a/backend/apps/common/middleware.py
+++ b/backend/apps/common/middleware.py
@@ -208,6 +208,46 @@ _FAIL_THRESHOLD = 10
 _LOCK_DURATION = 15 * 60  # 15 分钟
 _CLEANUP_INTERVAL = 10 * 60  # 10 分钟清理一次过期条目
 _last_cleanup = 0.0
+#: 限流表容量上限：键里含攻击者可控的用户名，无上限可被用于内存耗尽（审计 C-01/I-05）
+_MAX_LOCK_ENTRIES = 10_000
+#: 与 User.username 的 max_length 对齐，避免超长用户名撑大限流表
+_MAX_USERNAME_LEN = 128
+
+
+def normalize_login_username(raw) -> str:
+    """登录用户名归一化——**登录视图与限流中间件必须共用同一口径**。
+
+    改前中间件用未 strip 的原始 body 值查锁定，而视图用 strip() 后的值记账，
+    于是 `"admin "` 这类尾随空格变体查不到锁定记录，限流形同虚设（审计 C-01/I-05）。
+    非字符串入参（列表/数字/对象）统一归一为 ""，避免 `.strip()` 抛 TypeError → 500。
+    """
+    if not isinstance(raw, str):
+        return ""
+    return raw.strip()[:_MAX_USERNAME_LEN]
+
+
+def _login_username_from_request(request) -> str:
+    """从登录请求体提取用户名（JSON 与表单编码都解析），返回归一化后的值。
+
+    改前只解析 `application/json`，把 Content-Type 改成
+    `application/x-www-form-urlencoded`（DRF 同样接受）即可完全绕过限流。
+    """
+    content_type = (getattr(request, "content_type", "") or "").split(";")[0].strip().lower()
+    raw = None
+    if content_type == "application/json":
+        try:
+            import json
+
+            body = json.loads(request.body or b"{}")
+            raw = body.get("username") if isinstance(body, dict) else None
+        except Exception:  # noqa: BLE001 - 解析失败按无用户名处理
+            raw = None
+    else:
+        try:
+            raw = request.POST.get("username")
+        except Exception:  # noqa: BLE001
+            raw = None
+    return normalize_login_username(raw)
 
 
 def _cleanup_locks() -> None:
@@ -225,6 +265,40 @@ def _cleanup_locks() -> None:
         _locks.pop(k, None)
 
 
+def _evict_overflow() -> None:
+    """限流表超过容量上限时淘汰：先清过期，再按最早失败时间淘汰最旧条目。
+
+    只删「已过期」与「最旧」的条目，且不动仍在锁定中的条目，避免稀释正在生效的防护。
+    """
+    if len(_locks) <= _MAX_LOCK_ENTRIES:
+        return
+    now = time.time()
+    expired = [
+        k for k, v in _locks.items()
+        if now - v["first_at"] > _FAIL_WINDOW and now > v["locked_until"]
+    ]
+    for k in expired:
+        _locks.pop(k, None)
+    overflow = len(_locks) - _MAX_LOCK_ENTRIES
+    if overflow <= 0:
+        return
+    # 第一轮：只淘汰未锁定的条目（按最早失败时间，最旧优先）——
+    # 锁定中的条目是正在生效的防护，不能被"刷海量唯一用户名"挤掉。
+    unlocked = sorted(
+        (kv for kv in _locks.items() if now >= kv[1]["locked_until"]),
+        key=lambda kv: kv[1]["first_at"],
+    )
+    for k, _v in unlocked[:overflow]:
+        _locks.pop(k, None)
+    overflow = len(_locks) - _MAX_LOCK_ENTRIES
+    if overflow <= 0:
+        return
+    # 第二轮：未锁定条目已清空仍超限（说明锁定条目自身就超容量）→ 内存保护优先
+    oldest = sorted(_locks.items(), key=lambda kv: kv[1]["first_at"])[:overflow]
+    for k, _v in oldest:
+        _locks.pop(k, None)
+
+
 def record_login_failure(ip: str, username: str) -> None:
     _cleanup_locks()
     key = (ip, username)
@@ -236,6 +310,7 @@ def record_login_failure(ip: str, username: str) -> None:
     state["fails"] += 1
     if state["fails"] >= _FAIL_THRESHOLD:
         state["locked_until"] = now + _LOCK_DURATION
+    _evict_overflow()
 
 
 def record_login_success(ip: str, username: str) -> None:
@@ -255,14 +330,9 @@ class LoginRateLimitMiddleware(MiddlewareMixin):
     def process_request(self, request):
         if request.method == "POST" and request.path == "/api/auth/login":
             ip = _client_ip(request)
-            username = ""
-            try:
-                import json
-
-                body = json.loads(request.body or b"{}")
-                username = body.get("username", "")
-            except Exception:  # noqa: BLE001
-                pass
+            # 用户名必须与登录视图同口径归一化（strip + 类型与长度约束），
+            # 并兼容 JSON / 表单两种提交方式，否则限流可被绕过（审计 C-01/I-05）。
+            username = _login_username_from_request(request)
             if is_login_locked(ip, username):
                 from django.http import JsonResponse
 
```

### 7.3 `backend/apps/stock/engine.py`

```diff
diff --git a/backend/apps/stock/engine.py b/backend/apps/stock/engine.py
index 1758b93..483be2b 100644
--- a/backend/apps/stock/engine.py
+++ b/backend/apps/stock/engine.py
@@ -276,7 +276,10 @@ def build_candle(
             base_wick = trade_range * (0.3 + 0.4 * candle_noise(round_, open_))
         elif theoretical is not None and math.isfinite(theoretical):
             # 无成交但有理论价：影线基于理论价与收盘价的差异
-            theory_diff = abs(float(theoretical) - close)
+            # close 可能是 Decimal（price["final"] 来自 round2()），必须先转 float
+            # 再与 float(theoretical) 相减，否则 float - Decimal 抛 TypeError，
+            # 「判定可成交但本轮零成交」的轮次会直接 500、无法推进。
+            theory_diff = abs(float(theoretical) - float(close))
             base_wick = theory_diff * (0.5 + 0.5 * candle_noise(round_, open_))
         else:
             # 无成交无理论价：使用股价百分比
@@ -1594,22 +1597,39 @@ def advance_one_stock(
                 Decimal(str(buy.price)),
             )
             buy_cash = cash_map[buy.funds_account_id]
+            cash_limited = False
             if qty * pair_price > buy_cash + EPS:
                 qty = buy_cash / pair_price
+                cash_limited = True
                 if qty <= EPS:
                     buy_rem[buy.id] = Decimal("0")
                     bi += 1
                     continue
             sell_hold = holding_map.get(sell.funds_account_id)
             sell_shares = sell_hold["shares"] if sell_hold else Decimal("0")
+            shares_limited = False
             if qty > sell_shares + EPS:
                 qty = sell_shares
+                shares_limited = True
                 if qty <= EPS:
                     sell_rem[sell.id] = Decimal("0")
                     si += 1
                     continue
             # 6 位小数微调（保留买卖双方分摊精度，不入账）
             qty = (qty * Decimal("1000000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / Decimal("1000000")
+            if qty <= 0:
+                # 量化归 0：资金/持仓夹紧后的余量小于最小成交单位（5e-7），本对委托
+                # 一股都成交不了。此时若不推进任何一侧，后面的现金/持仓/剩余量更新
+                # 全是 0、bi/si 也不动，外层 while 会永远停在同两个索引上（请求不返回、
+                # 事务与推进锁长期占用 → 该比赛股票系统僵死）。按「受限的一方」作废
+                # 剩余量后继续撮合。
+                if cash_limited or not shares_limited:
+                    buy_rem[buy.id] = Decimal("0")
+                    bi += 1
+                if shares_limited or not cash_limited:
+                    sell_rem[sell.id] = Decimal("0")
+                    si += 1
+                continue
             trade_prices.append(pair_price)
 
             # 买入方：现金减少，持仓增加（加权成本）
```

### 7.4 `backend/apps/common/permissions.py`

```diff
diff --git a/backend/apps/common/permissions.py b/backend/apps/common/permissions.py
index 0cf6022..a94356d 100644
--- a/backend/apps/common/permissions.py
+++ b/backend/apps/common/permissions.py
@@ -366,9 +366,15 @@ ROLE_TEMPLATES = {
 }
 
 
-def assert_grant_allowed(actor_role, target_role, permissions):
+def assert_grant_allowed(actor_role, target_role, permissions, allow_extras: bool = False):
     """校验权限授予是否在上限范围内。
 
+    allow_extras：是否放开「扩展集」（角色模板 `grantExtras` 里的权限）。
+    扩展集默认不开放，必须由超管在请求体里显式声明 `allowExtras=true` 才可授予 ——
+    改前该分支恒记违规，导致扩展集**永远授不出去**（`company:manage` /
+    `message:manage` / `industryType:manage` / `contractType:manage` /
+    `data:region:edit` 实际只有超管可用），与文档承诺的「超管可按需放开」不符（审计 I-13）。
+
     返回 (allowed: bool, violations: list[str])。
     """
     # 非超管不能写权限
@@ -385,18 +391,33 @@ def assert_grant_allowed(actor_role, target_role, permissions):
     if not template:
         return False, [f"未知角色: {target_role}"]
 
-    # 检查是否在授予上限范围内（扩展集不在默认上限内）
+    # 检查是否在授予上限范围内（扩展集需显式放开）
     ceiling = set(template["grantCeiling"])
     extras = template["grantExtras"]
     violations = []
     for perm in permissions:
-        # 超管专属权限检查
+        # 超管专属权限检查（无论是否放开扩展集都不可授予）
         if perm in SUPER_ADMIN_ONLY_PERMISSIONS:
             violations.append(f"{perm} 为超管专属权限，不可授予 {target_role}")
             continue
         if perm not in ceiling:
             if perm in extras:
-                violations.append(f"{perm} 在扩展集中，需超管显式放开")
+                if not allow_extras:
+                    violations.append(f"{perm} 在扩展集中，需显式 allowExtras=true 放开")
             else:
                 violations.append(f"{perm} 超出 {target_role} 的授予上限")
     return len(violations) == 0, violations
+
+
+def role_default_permissions(role: str | None) -> list[str]:
+    """角色模板的默认权限（供 `permissions=null` 的「按角色继承」语义使用，审计 I-19）。
+
+    - SUPER_ADMIN：隐式全权，不落库，返回空列表（判定由 `has_permission` 的 role 短路负责）
+    - 其它角色：返回模板 defaultPermissions 的副本（调用方可能就地修改）
+    """
+    if role == "SUPER_ADMIN":
+        return []
+    template = ROLE_TEMPLATES.get(role or "")
+    if not template:
+        return []
+    return list(template["defaultPermissions"])
```

### 7.5 `frontend/src/utils/deleteConfirm.ts`

```diff
diff --git a/frontend/src/utils/deleteConfirm.ts b/frontend/src/utils/deleteConfirm.ts
index 3569c28..9e97ccb 100644
--- a/frontend/src/utils/deleteConfirm.ts
+++ b/frontend/src/utils/deleteConfirm.ts
@@ -1,5 +1,7 @@
 import { ElMessageBox } from "element-plus";
 
+import { buildDeleteConfirmMessage } from "./deleteConfirmMessage";
+
 export interface DeleteImpactItem {
   label: string;
   count: number;
@@ -33,13 +35,9 @@ export async function confirmDeleteWithImpact(
     return;
   }
 
-  const lines = children
-    .filter((c) => (c.count || 0) > 0)
-    .map((c) => `• ${c.label}：${c.count} 条`)
-    .join("<br/>");
-  const msg =
-    `删除「<b>${name}</b>」将<b>级联删除</b>以下关联数据，且不可恢复：<br/><br/>` +
-    `${lines}<br/><br/>确定继续删除吗？`;
+  // 文案由纯函数生成（动态值已 HTML 转义）：本确认框以 dangerouslyUseHTMLString 渲染，
+  // 未转义的数据名称即存储型 XSS（审计 F-02）。
+  const msg = buildDeleteConfirmMessage(name, children);
   await ElMessageBox.confirm(msg, "级联删除警告", {
     type: "warning",
     dangerouslyUseHTMLString: true,
```

### 7.6 `frontend/src/utils/format.ts`

```diff
diff --git a/frontend/src/utils/format.ts b/frontend/src/utils/format.ts
index 8aebd74..2741ca1 100644
--- a/frontend/src/utils/format.ts
+++ b/frontend/src/utils/format.ts
@@ -111,12 +111,21 @@ export function isValidNumberString(s: string | number | null | undefined): bool
 }
 
 /**
- * ISO 时间去秒截断（与全局 $formatTime 完全一致）。
- * 空值或非法日期返回 "-"；否则按 UTC 截断到秒（YYYY-MM-DD HH:mm:ss）。
+ * 时间显示（**本地时区**，截断到秒，与全局 $formatTime 完全一致）。
+ *
+ * 空值或非法日期返回 "-"；否则输出 `YYYY-MM-DD HH:mm:ss`。
+ *
+ * 为什么不用 toISOString()：它把时刻转成 UTC 再截断，而后端 TIME_ZONE=Asia/Shanghai
+ * （USE_TZ=True）—— 前端按 UTC 显示会让全站创建/更新/执行时间**少 8 小时**，且与
+ * AuditLogView 用 toLocaleString 渲染的本地时间口径矛盾（审计 F-08）。
  */
 export function formatTime(val: string | Date | null | undefined): string {
   if (!val) return "-";
   const d = typeof val === "string" ? new Date(val) : val;
   if (isNaN(d.getTime())) return "-";
-  return d.toISOString().replace("T", " ").substring(0, 19);
+  const p = (n: number) => String(n).padStart(2, "0");
+  return (
+    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
+    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
+  );
 }
```

### 7.7 `frontend/src/api/request.ts`

```diff
diff --git a/frontend/src/api/request.ts b/frontend/src/api/request.ts
index e3e3c91..4bbc0ca 100644
--- a/frontend/src/api/request.ts
+++ b/frontend/src/api/request.ts
@@ -2,6 +2,23 @@ import axios, { AxiosInstance, AxiosRequestConfig } from "axios";
 import { ElMessage } from "element-plus";
 import { getApiBaseUrl, versionBlocked } from "@/config";
 import { getAccountItem, removeAccountItem } from "@/utils/accountStorage";
+// 本地全量副本 → 响应形态（纯函数，独立成模块以便单测）：
+// 未显式传 pageSize 时返回本地全量，不再按 50 条静默截断（审计 A-01/V-04/W-05）。
+import { applyLocalPaging as reconstruct } from "./localPaging";
+// 响应体归类（纯函数）：blob 下载与非信封 2xx 不再被判为失败（审计 F-01）。
+import { interpretResponse } from "./envelope";
+// 请求层内存 memo（登出/换账号清空 + epoch 防旧响应回填，见模块注释；审计 F-06）。
+import {
+  bumpResourceEvent,
+  getMemoEntry,
+  isMemoFresh,
+  memoEpoch,
+  resetResponseMemo,
+  writeMemo,
+} from "./responseMemo";
+
+// 实时事件到达时标记资源「最近有变更」的实现来自 ./responseMemo，这里保持对外导出不变。
+export { bumpResourceEvent };
 
 // axios 自定义请求配置字段类型增强（request.ts 与 stores/version.ts 均使用这些字段）。
 declare module "axios" {
@@ -86,12 +103,16 @@ export function isSessionRefreshing(): boolean {
 
 api.interceptors.response.use(
   (response) => {
-    const res = response.data;
-    if (res.code !== 0) {
-      ElMessage.error(res.message || "请求失败");
-      return Promise.reject(new Error(res.message));
+    // 响应体归类（纯函数，见 ./envelope.ts）：二进制下载原样返回、带 code 的按信封语义、
+    // 其余非信封 2xx 也原样返回 —— 改前直接读 res.code 判定，导致 blob 下载与
+    // 非信封响应被一律判为失败（审计 F-01）。
+    const decision = interpretResponse(response.data);
+    if (decision.kind === "error") {
+      ElMessage.error(decision.message);
+      return Promise.reject(new Error(decision.message));
     }
-    return res.data;
+    // axios 拦截器按「已解包的业务数据」返回（本文件既有约定，故此处断言为 any）
+    return decision.value as any;
   },
   (error) => {
     // 安全加固：立即剥离 error 对象上的 Authorization 头——下游各视图的
@@ -106,6 +127,15 @@ api.interceptors.response.use(
       return Promise.reject(error);
     }
     if (error.response?.status === 401) {
+      // 后端「强制改密」门禁（errorCode=must_change_password）**不是**会话过期：绝不能清 token、
+      // 不能跳登录页、不能派发 auth:kicked —— 否则用户正在填写的「修改初始密码」表单会因为
+      // 登录态被清空而提交成匿名请求，后端返回「登录已过期，请重新登录」（真机事故：
+      // 新部署的超管永远改不了初始密码，等于被挡在门外）。
+      // 注：必须用 errorCode（后端 401 的机器码）而不是 data.code —— 后者是 HTTP 状态码 401。
+      if (error.response?.data?.errorCode === "must_change_password") {
+        if (!error.config?.silent) ElMessage.warning(getErrorMessage(error));
+        return Promise.reject(error);
+      }
       // 会话刷新窗口（改密自动重登中）：旧会话的 401 一律静默丢弃，不踢出、不弹提示
       if (isSessionRefreshing()) {
         return Promise.reject(error);
@@ -214,10 +244,7 @@ function _deriveResourceKey(url: string): string {
 // ---------- O3：跨挂载新鲜度窗口（stale-while-revalidate）----------
 // 内存 memo：按「请求键」缓存最近一次成功响应；窗口内（且无该资源实时事件）直接返回，
 // 避免同一资源在多个组件/多次挂载被重复发往服务端（仍是后台增量请求，但能省则省）。
-const STALE_WINDOW_MS = 15 * 1000;
-const _memo = new Map<string, { time: number; value: unknown }>();
-// 各资源最近一次实时事件时间；事件后该资源的 memo 立即失效（绕过新鲜度窗口），保证及时刷新。
-const _lastEventAt = new Map<string, number>();
+// 状态与读写器在 ./responseMemo.ts（含「登出/换账号清空」与 epoch 防旧响应回填）。
 
 function _resourceOf(url: string): string {
   const path = (url || "").split("?")[0];
@@ -225,23 +252,9 @@ function _resourceOf(url: string): string {
   return SEG_TO_RESOURCE[seg] || seg;
 }
 
-/** 实时事件到达时调用：标记该资源「最近有变更」，使 O3 memo 立即失效并触发刷新。 */
-export function bumpResourceEvent(resource: string): void {
-  if (!resource) return;
-  _lastEventAt.set(resource, Date.now());
-  // 防止无界增长：条目超过 100 时清理 1 小时前的旧条目
-  if (_lastEventAt.size > 100) {
-    const cutoff = Date.now() - 3600_000;
-    for (const [k, v] of _lastEventAt) {
-      if (v < cutoff) _lastEventAt.delete(k);
-    }
-  }
-}
-
 /** 写操作 / 登录失效后清空内存 memo，避免返回被写失效前的陈旧数据。 */
 function _resetMemo(): void {
-  _memo.clear();
-  _lastEventAt.clear();
+  resetResponseMemo();
 }
 
 /** 对外暴露：清空内存 memo（设置页「清空本地缓存」等场景调用，配合清空 IndexedDB 后重载页面）。 */
@@ -332,15 +345,8 @@ function mapSubKey(resource: string, competitionId: string | number | undefined)
   return `${resource}|competitionId=${competitionId ?? ""}`;
 }
 
-/** 按集合的 shape 与请求的分页参数，把本地全量副本「还原」成组件期望的响应形态。 */
-function reconstruct(items: unknown[], shape: "array" | "paged", params: Record<string, unknown>): unknown {
-  if (shape === "array") return items;
-  const page = params.page != null ? parseInt(String(params.page), 10) : 1;
-  const pageSize = params.pageSize != null ? parseInt(String(params.pageSize), 10) : 50;
-  const total = items.length;
-  const start = (page - 1) * pageSize;
-  return { items: items.slice(start, start + pageSize), total, page, pageSize };
-}
+/** 按集合的 shape 与请求的分页参数，把本地全量副本「还原」成组件期望的响应形态。
+ *  实现见 ./localPaging.ts 的 applyLocalPaging（顶部已别名导入为 reconstruct）。 */
 
 /** 全量同步：循环分页拉取，直到取满 total，避免单集合超过 LARGE_PAGE_SIZE 时本地副本被截断。
  *  返回合并后的响应（items 为全量，total 为真实总数），供 storeAndReturn 写入本地全量副本。 */
@@ -688,19 +694,20 @@ async function _cachedGet<T = unknown>(url: string, config?: AxiosRequestConfig)
 
   // O3：窗口内且无该资源实时事件 → 直接返回内存副本，不打网络（含后台增量请求）。
   const resource = _resourceOf(url);
-  const m = _memo.get(key);
-  const lastEvt = _lastEventAt.get(resource) ?? -Infinity;
-  if (m && Date.now() - m.time < STALE_WINDOW_MS && lastEvt <= m.time) {
-    return unwrap(m.value) as Promise<T>;
+  const m = getMemoEntry(key);
+  if (isMemoFresh(m, resource)) {
+    return unwrap(m!.value) as Promise<T>;
   }
 
   // F2 修复：记录请求发起时刻（而非完成时刻），确保事件晚于发起时刻时 memo 失效
   const startedAt = Date.now();
+  // 记录发起时的会话 epoch：登出/换账号会递增它，旧响应返回后不得再写回 memo（审计 F-06）
+  const epoch = memoEpoch();
   const p = cachedGetImpl(url, silentConfig).finally(() => _getInflight.delete(key));
   _getInflight.set(key, p);
   const result = await p;
   // 使用 startedAt 而非 Date.now()，消除时序竞态窗口；memo 存原始结构（保留分页 total 等）
-  _memo.set(key, { time: startedAt, value: result });
+  writeMemo(key, { time: startedAt, value: result }, epoch);
   // 对外返回：列表统一降维为裸数组（normalize=false 时保留分页对象），兼容下游 `Array.isArray(res)` 写法
   return unwrap(result);
 }
```

---

## 8. 风险清单与合并建议

### 8.1 风险清单

| # | 风险 | 依据 | 等级 | 缓解动作 |
| --- | --- | --- | --- | --- |
| R1 | 148 个提交一次性进入 master，回滚粒度粗 | `master` 落后 148 个提交，且为 fast-forward 关系 | 中 | 合并前在 `bugfix-merged` 打 tag（如 `pre-merge-20260924`），必要时可整体回退 |
| R2 | 部署脚本大改，且正确性最终取决于真实 Linux/SSH 主机 | `deploy-linux.sh` +568/-60、`update-from-github.sh` +482/-59、`quick-sync.sh`、`migrate-server.sh`；对应 `tests/fix_verify/scripts/` 22 个用例只能静态/桩件验证，不触达真实主机 | **高** | 合并后先在 WSL/预发按 `docs/OPS.md` 全流程演练；不要直接上生产 |
| R3 | 新增 `.gitattributes` 强制 shell 脚本 LF，会改变既有工作副本的检出行为 | `.gitattributes` 为新增文件；`tests/deploy_public_ip_test.sh` 等 mtime 可能被判定为修改 | 中 | 合并后在开发机执行 `git add --renormalize .` 并复核 `git status`，避免把行尾噪声混进业务提交 |
| R4 | 样例与运行产物入库，可能携带真实业务数据 | 9 个二进制文件（xlsx/png/ico）＋ `contract_watcher/bookkeeping_example/`（43 文件）与 `contract_watcher/test_run_recheck/`（8 文件） | 中 | 逐个人工确认脱敏；必要时改为生成脚本 + `.gitignore`（已有 CW-26 的同类处理） |
| R5 | 权限与审计语义变化，行为可能与既有运维习惯不符 | I-13「超管显式放开」、I-19「permissions=null 按角色继承」、C-04 合同执行补审计 | 中 | 合并后按角色矩阵回归：超管、高级管理、普通管理员、公司账号各跑一遍 |
| R6 | `settings.py` 新增 `SECURE_PROXY_SSL_HEADER`，依赖 nginx 正确转发 `X-Forwarded-Proto` | `backend/backend/settings.py` +16/-1，且 `deploy/nginx-gipfel.conf` 同步改动 | 中 | 合并后同时更新 nginx 配置，否则 admin 登录/CSRF 可能出现 Secure cookie 异常 |
| R7 | 新增 `apps.preparation` app 需重启后端进程并确认路由 | `settings.py` 加入 `INSTALLED_APPS`、`backend/backend/urls.py` 挂载路由；该 app 无 models，**不需要 migrate** | 低 | 部署时执行 `manage.py check` 与 `manage.py migrate --plan` 确认无待执行迁移 |
| R8 | 依赖足迹未变（正面结论） | `package.json` / `requirements.txt` / 锁文件在本次对比中**均未变更** | 低 | 常规 `npm ci` / `pip install -r requirements.txt` 即可 |

### 8.2 推荐合并步骤

```bash
# 0) 备份与取回
git fetch --all --prune
git log --oneline master..bugfix-merged | wc -l   # 应输出 148

# 1) 在目标分支打预合并 tag（回滚锚点）
git tag -a pre-merge-20260924 bugfix-merged -m 'merge anchor before master fast-forward'

# 2) master 是 bugfix-merged 的祖先 => 直接快进，零冲突
git checkout master
git merge --ff-only bugfix-merged

# 3) 若必须保留显式合并点，改用：
# git merge --no-ff bugfix-merged -m 'chore(release): 合并 bugfix-merged（148 提交）'

# 4) 让 .gitattributes 的行尾规则统一生效
git add --renormalize .
git status --short

# 5) 推送
git push origin master
```

### 8.3 合并后验证清单

| # | 验证项 | 命令 / 判据 |
| --- | --- | --- |
| 1 | 后端静态检查 | `cd backend && python manage.py check` |
| 2 | 无待执行迁移意外新增 | `python manage.py migrate --plan`（preparation 无 models） |
| 3 | 缺陷回归用例 | `python manage.py test tests_fix_verify`（51 个用例） |
| 4 | 前端用例 | 按 `tests/fix_verify/frontend/bundle.ps1` 打包后运行 mjs 用例（74 个文件） |
| 5 | 数值/精度专项 | `python tests/sqlite_decimal_roundtrip.py`、`python tests/big_number_smoke.py` |
| 6 | 部署链路 | `bash tests/deploy_public_ip_test.sh`、`bash tests/gipfel-logviewer-diag.sh` |
| 7 | 合同监听程序自检 | `python contract_watcher/selftest.py` |
| 8 | 前端可构建 | `cd frontend && npm ci && npm run build` |

---

## 附录 A：master 与 bugfix 的对比（备选口径）

若实际想看的「bug 分支」是 `bugfix` 而非 `bugfix-merged`，其与 `master` 的差异如下，可视为本文的子集：

| 指标 | 值 |
| --- | --- |
| bugfix tip | `a4aac46 2026-09-12` |
| 领先 master | 3 个提交 |
| 变更规模 | 22 files changed, 7100 insertions(+), 103 deletions(-) |
| 包含内容 | `fix(scripts)` Windows Ctrl+C 卡死修复、`feat(preparation)` 准备清单与分组导入导出、`Merge branch 'feature/prep-export-import'` |

`master ⊂ bugfix ⊂ bugfix-merged`，因此只需要本文的完整口径即可覆盖 bugfix 的全部内容。

## 附录 B：伴随差异文件

| 文件 | 内容 | 规模 |
| --- | --- | --- |
| `docs/branch-diff/master-vs-bugfix-merged.full.diff` | 全部 357 个文件的完整 unified diff（含新增文件全文） | 67,072 行 / 3147.3 KB |
| `docs/branch-diff/master-vs-bugfix-merged.modified.diff` | 仅 75 个**被修改**文件的 diff（评审重点，不含 270 个新增文件的全文） | 7,534 行 |
| `master与bugfix-merged分支差异详情.md` | 本文件：统计、提交清单、文件清单、缺陷索引、风险与建议 | — |

## 附录 C：复现命令

```bash
# 分支关系
git merge-base master bugfix-merged
git merge-base --is-ancestor master bugfix-merged && echo 'master 是祖先，可快进'
git rev-list --left-right --count master...bugfix-merged

# 规模统计
git diff --shortstat master bugfix-merged
git diff --name-status -M master bugfix-merged | cut -f1 | sort | uniq -c
git diff --numstat -M master bugfix-merged

# 提交清单
git log master..bugfix-merged --no-merges --format='%h|%ad|%an|%s' --date=short

# 完整差异
git diff master bugfix-merged > master-vs-bugfix-merged.full.diff
git diff --diff-filter=M -M master bugfix-merged > master-vs-bugfix-merged.modified.diff

# 原始 git 输出为 UTF-8；Windows 下若中文文件名显示为转义，加 -c core.quotepath=false
git -c core.quotepath=false diff --stat master bugfix-merged
```

---

_本文件由 `git diff`/`git log` 的机器可读输出生成，统计数字与提交/文件清单均为实测值，未做人工估算。_
