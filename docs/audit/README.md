# 代码缺陷审计归档（audit）

> 本目录是「代码缺陷审计 + 运维就绪度评估」这条线的**结论性文档归档**。
> 全部条目都带 `路径:行号` 定位、代码摘录、触发条件、后果与修复建议；**取证过程**（逐条 before/after 代码快照、patch/harness 脚本、运行日志）属中间产物，已移出仓库，不在此目录内。
> 上位索引入口见 [../README.md](../README.md)。

---

## 1. 汇总与索引

| 文档 | 内容 |
| --- | --- |
| [分支代码缺陷审计报告.md](分支代码缺陷审计报告.md) | **总报告**。跨 `master` / `bugfix` / `feature/*` / `test` 等分支的缺陷汇总与复核结论：已修复项、重做项（被 master 覆盖后重新做回的 X-11/X-14/X-22）、未修复项与分级统计；含真机实战新增的 X-30 / X-31 立案段 |
| [00-缺陷总索引.md](00-缺陷总索引.md) | **全量索引**：21 个审计单元共 366 条的逐条清单（编号 / 级别 / 位置 / 一句话），另含「跨单元同源 / 重复项」聚类表。⚠️ 本文由 `_build_index.py` 从 21 份单元报告**机械抽取**，级别为**单元自评**，与总报告的重排口径可能相差一级；文内自报 368 条、总报告为 366 条，**以总报告口径为准**。生成器已随中间产物移出仓库，本文为冻结快照（见文末「归档说明」） |
| [浮点精度检测报告.md](浮点精度检测报告.md) | 浮点精度专项检测（v3 · 更新仓库复测）：金额/数量在前后端与合同引擎中的精度口径问题。基于较早基线，其后 D-01 金额精度已列入修复台账并完成 |
| [WSL与生产环境验证结果记录-详版.md](WSL与生产环境验证结果记录-详版.md) | [../WSL与生产环境验证结果记录.md](../WSL与生产环境验证结果记录.md) 的**同日详版**（384 行 vs 164 行）。**不是早期草稿**：它独有「第二层（真机）」的公开探针证据（`43.142.77.225` → `/api/health`=200、`/`=200、`:8120`=302）与 7 条给操作手册的修订建议；正式记录里真机部分当时仍标「尚未执行」 |
| [Debian13工具链可移植性报告.md](Debian13工具链可移植性报告.md) | Debian 13 上**测试/开发工具链**的可移植性问题（受测 `2f39918`）：3 个只在 Linux 暴露的缺陷，**均不影响生产部署，且至今未修**；另有 3 项可移植性缺口（`bundle.ps1`、`dev.py` 的 venv 路径等）。与生产部署无关，故与 [../真机验证报告-Debian13.md](../真机验证报告-Debian13.md) 互补而非重复 |
| [Debian13部署重测报告-gipfel用户组.md](Debian13部署重测报告-gipfel用户组.md) | `gipfel` 用户/组与部署报错的**修复前取证**（其 P0 三条即 X-30，已在 `2f39918` 修复；§5 建议已落地为公共库 `ensure_runtime_user`）。留作问题复现与修复对照 |

## 2. 审计单元（units/）

21 份单元报告，每份覆盖一个技术域，格式统一为「代码摘录 + 触发条件 + 后果 + 修复建议 + 存疑项」。

| 前缀 | 数量 | 基线 | 范围 |
| --- | --- | --- | --- |
| `U01`–`U14` | 14 | `master` | 后端核心 / 认证身份 / 组织 / 生产 / 合同核心 / 股票 / 前端核心 / 前端视图（数据 1–2、业务 1–2）/ 前端组件（1–2）/ 脚本部署测试 |
| `B01`–`B08` | 7 | 其他分支 | `B01` `bugfix`·`feature_wuhaoye` 脚本；`B02` 建包 IO；`B03`–`B05` 合同类型代码化；`B06` Excel（`test` 分支）；`B08` 合同监视器（`feature/contract-watcher`） |

> `B07` 不存在（编号留空）。`B02` 对应 `origin/prep-export-import`，`B03`–`B05` 对应 `feature/contract-type-code`。

## 3. master 分支运维层面缺陷审计（ops-master/）

| 文档 | 内容 |
| --- | --- |
| [ops-master/00-master运维缺陷总报告.md](ops-master/00-master运维缺陷总报告.md) | 总报告：master 分支运维层面缺陷审计的结论与分级 |
| [ops-master/O1-发布与部署流水线.md](ops-master/O1-发布与部署流水线.md) | 部署与发布流水线 |
| [ops-master/O2-服务编排与运行时.md](ops-master/O2-服务编排与运行时.md) | 服务编排与运行时 |
| [ops-master/O3-反向代理与网络边界.md](ops-master/O3-反向代理与网络边界.md) | 反向代理与网络边界 |
| [ops-master/O4-数据备份与容灾.md](ops-master/O4-数据备份与容灾.md) | 数据、备份与容灾 |
| [ops-master/O5-配置密钥与可观测性.md](ops-master/O5-配置密钥与可观测性.md) | 配置 / 密钥 / 可观测性 / 文档一致性 |
| [ops-master/M0-OPS就绪报告-master分支对照.md](ops-master/M0-OPS就绪报告-master分支对照.md) | 《OPS_READINESS_REPORT》28 条缺陷在 master 分支的现状对照 |
| [ops-master/M1-部署备份版本类对照.md](ops-master/M1-部署备份版本类对照.md) | 部署/备份/版本类逐条对照（原件 `bugfix-merged@5cb3468` → 目标 `master@97e117e`） |
| [ops-master/M2-运行时并发财年类对照.md](ops-master/M2-运行时并发财年类对照.md) | O-02 / O-04 / O-13 / O-17 / O-19 / O-24 / O-25 |
| [ops-master/M3-权限角色账号会话类对照.md](ops-master/M3-权限角色账号会话类对照.md) | O-01 / O-06 / O-08 / O-10 / O-12 / O-27 / O-28 |
| [ops-master/M4-业务能力缺失类对照.md](ops-master/M4-业务能力缺失类对照.md) | 业务能力缺失类对照 |

## 4. 规则 / 数据 / 就绪度专项（rules/）

| 文档 | 内容 |
| --- | --- |
| [rules/RULE_GAP_REPORT.md](rules/RULE_GAP_REPORT.md) | 规则覆盖度差距分析：赛事规则书（Update 1.0/2.0/3.0）与系统实现之间的差距 |
| [rules/DATA_FIT_REPORT.md](rules/DATA_FIT_REPORT.md) | 真实数据可导入性差距：赛事实际数据文件与系统入库能力的差距 |
| [rules/OPS_READINESS_REPORT.md](rules/OPS_READINESS_REPORT.md) | 现场运营就绪度评估：28 条就绪度缺陷 |
| [rules/OPS_DEFECT_NOTES_O02_O04_O05_O09_O10_O11_O12_O13.md](rules/OPS_DEFECT_NOTES_O02_O04_O05_O09_O10_O11_O12_O13.md) | 上述就绪度缺陷中 8 条的逐条解释与修法 |
| [rules/OPS_SNAPSHOT_SYSTEM_DESIGN.md](rules/OPS_SNAPSHOT_SYSTEM_DESIGN.md) | 现场快照系统设计方案（v1）：备份/恢复/影子实例演练的完整操作与实测形态 |
| [rules/V3_PARAMS.md](rules/V3_PARAMS.md) | Update 3.0 参数表完整转录（评分与财务口径的原始依据） |

## 5. 单条缺陷立案（issues/）

| 文档 | 内容 |
| --- | --- |
| [issues/issue-master-01-deploy-runtime-user-path.md](issues/issue-master-01-deploy-runtime-user-path.md) | master：部署脚本运行用户创建路径（PATH 缺 `sbin` 静默失败） |
| [issues/issue-master-02-forced-password-change.md](issues/issue-master-02-forced-password-change.md) | master：强制改密被会话心跳打断（X-31 的 master 侧立案） |
| [issues/issue-master-03-seed-password-stdout-leak.md](issues/issue-master-03-seed-password-stdout-leak.md) | master：种子口令 stdout 泄露面 |
| [issues/issue-master-04-dev-launcher-linux.md](issues/issue-master-04-dev-launcher-linux.md) | master：Linux 下开发启动器不可用 |
| [issues/upstream-issue-01-deploy-useradd-path-silent-fail.md](issues/upstream-issue-01-deploy-useradd-path-silent-fail.md) | 上游：`useradd` PATH 静默失败（X-30 ①） |
| [issues/upstream-issue-02-deploy-group-exists-useradd-U.md](issues/upstream-issue-02-deploy-group-exists-useradd-U.md) | 上游：组已存在导致 `useradd -U` 退出 9（X-30 ②） |
| [issues/upstream-issue-03-migrate-server-useradd-chown-order.md](issues/upstream-issue-03-migrate-server-useradd-chown-order.md) | 上游：`migrate-server.sh` 建用户缺 `-U` 且 chown 早于建用户（X-30 ③） |

---

## 归档说明

- 本目录下的报告**全部入库**；与之配套的取证中间产物（`_before`/`_after` 代码快照、`_*_patch.py`、`_*_harness.*`、`_merge/`、`_probe_tmp/`、`_rules/*.txt`、运行日志、索引生成器 `_build_index.py`）**不入库**，已移出到仓库外的本地归档目录。
- 因此，本目录文档里出现的 `code_audit/...` 路径指**当时的取证工作目录**，不是本仓库的现有路径。其中两类需要特别说明：
  - **已被本目录取代的**：`code_audit/U01…U14-*.md`、`B01…B08-*.md` → [units/](units)；`code_audit/00-缺陷总索引.md` → [00-缺陷总索引.md](00-缺陷总索引.md)；`code_audit/ops-master/**` → [ops-master/](ops-master)；`code_audit/_rules/*.md` → [rules/](rules)。
  - **原本就已缺失的**（指向取证期更早的临时文件）：[rules/DATA_FIT_REPORT.md](rules/DATA_FIT_REPORT.md) 指向的 `_rules/_probe/*.txt`、[rules/OPS_READINESS_REPORT.md](rules/OPS_READINESS_REPORT.md) 引用的 `_rules/v3.txt`（现存的是 `v3_full.txt`）、以及 `_rules/xlsx_dump.txt`。这些**在归档前就不存在**，文档内嵌的结论与代码摘录仍可独立阅读。
- 少数回归用例（如 `tests/fix_verify/scripts/test_x18_bootstrap_guard.py`）会引用当时的 harness；harness 缺失时用例**自动 `skipTest`**（用例内已注明「非交付物，允许缺失」），不影响套件通过。
- [issues/](issues) 下 7 份是**可直接粘贴成 GitHub Issue 的立案草案**，尚未提交到 issue tracker。其中 `issue-master-04-dev-launcher-linux.md` 对应的「Linux 开发启动器不可用」**至今仍未修复**，请先落地跟踪再考虑清理。

