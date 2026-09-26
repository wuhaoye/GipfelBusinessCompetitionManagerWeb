# Gipfel 商赛系统 · 现场快照系统设计方案（v1）

> **基准**：分支 `bugfix-merged` @ `5cb34682b7fb8bc9cf96aa6e4e3dab3a69c602ee`（工作区已核实）
> **硬约束**：**不修改任何现有代码**。本方案的新增物全部落在**仓库之外**（`/opt/gipfel-snapshot/`、`/etc/systemd/system/`、`/var/backups/gipfel/`），对 `backend/`、`frontend/`、`deploy/`、`scripts/` **零字节改动**。
> **目标场景**：中海国际中心七楼，~50–100 人，学生自带笔记本 + 共享 Wi-Fi，两天七个财年时段（AI 财年 + 第一~第六财年），由学生主席团自己运营。
> **对应缺陷**：直接解 **O-05**（无备份/无赛前快照/文档脚本缺失），并为 **O-02**（推进财年静默半成功）与 **O-13**（字段变更无审计）提供**外部可验证手段**；对 **O-04**（单线程）**必须做到不加重**。
> 相关文档：[`OPS_READINESS_REPORT.md`](OPS_READINESS_REPORT.md)、[`OPS_DEFECT_NOTES_O02_O04_O05_O09_O10_O11_O12_O13.md`](OPS_DEFECT_NOTES_O02_O04_O05_O09_O10_O11_O12_O13.md)

---

## 0. 一页摘要（TL;DR）

| 项 | 设计结论 |
| --- | --- |
| 架构 | **旁路快照服务**：一个 CLI（`snapshot.sh`）+ 一个财年事件侦听服务 + 一个兜底定时器 + 一个异地同步定时器，全部在应用之外，**对活库只读** |
| 核心原语 | 复用仓库已有的**一致性语义**：`VACUUM INTO` / `sqlite3_backup`（见 [`scripts/lib/deploy-common.sh:173-244`](../../../scripts/lib/deploy-common.sh)），**绝不用 `cp -a` 拷活库** |
| 触发 | ①财年 `FY_START`/`FY_END` 事件（锚定既有日志行）②赛程表定时 ③人工一键 ④部署/重启前 |
| 一致性 | SQLite：**强一致**（页级切片备份 + `integrity_check`）；`uploads/`：**有界不一致**并显式记录在 manifest |
| 内容 | `db.sqlite3` + `uploads/` + `.env` + `VERSION.json` + git commit + 活动 nginx/systemd 配置 + 日志尾段 + `manifest.json` + `SHA256SUMS` |
| 分层 | L0 稳态滚动（只含 DB）/ L1 财年边界（标记）/ L2 每日收尾（全量 bundle）/ L3 赛后归档（+ 记账账本 + 代码 bundle） |
| 落盘 | `/var/backups/gipfel/<时间戳>_<触发标签>/`，原子落盘（临时目录 → fsync → rename）+ 硬链接去重 |
| RPO | 财年边界 = **0**；稳态 ≤ 10 分钟；记账账本 = 每次 flush + 财年末 |
| RTO | ≤ 10 分钟恢复可用（含校验 + 健康检查）；≤ 20 分钟含影子核对 |
| 安全 | 快照含 `.env`（JWT_SECRET）与 bcrypt 哈希 → 目录 `0700`、**绝不能落在 `backend/uploads/`**（nginx `/uploads/` 是公开可下载的，见 [`deploy/nginx-gipfel.conf:80-81`](../../../deploy/nginx-gipfel.conf)） |
| 不做的 | 不改应用、不引入 PG/Redis/云、不做 WAL 归档、不做自动恢复生产、不做去重压缩算法自研 |

---

## 1. 背景与需求

### 1.1 为什么需要（O-05 的现场形态）

原报告 §2.5 + §4 的结论是：**生产环境没有任何定时备份、没有 systemd timer、没有 cron**；`scripts/backup.sh` **在仓库里不存在**（已实查 `scripts/`：只有 bootstrap/start/stop-dev、dev.py、deploy-linux.sh、update-from-github.sh、migrate-server.sh、quick-sync.sh、verify-migration.sh、gen_logviewer_key.py、make_favicon.py、`lib/`）；唯一存在的一致性快照函数 `snapshot_sqlite_consistent` **只被部署/升级脚本调用**（[`scripts/deploy-linux.sh:317-334`](../../../scripts/deploy-linux.sh)）。

后果：**两天七个财年时段的可恢复点实际上只有「上次部署」那一个**。`docs/MIGRATION.md:500-520` 让运维手写 `backup.sh`，示例还是 `cp -a` 热拷贝活库 + 凌晨 3 点的 crontab —— 对「上午 8:30 到晚上 18:30 的比赛日」而言，两个运行点之间隔着整个比赛日。

**快照系统的目标不是「备份」，而是「让现场在 30 秒内拿到一个可用的、被验证过的恢复点，并且这个动作不需要运维知识」。**

### 1.2 硬约束（决定了架构长什么样）

| # | 约束 | 出处 | 设计含义 |
| --- | --- | --- | --- |
| C1 | **不得修改现有代码** | 用户要求 | 触发、观测、校验全部走**外部进程 + 既有可观测面**（日志行、HTTP 探针、文件） |
| C2 | 服务**不停**、不重启 | 赛中不可能停服 | 必须在线一致性快照（`VACUUM INTO` / `sqlite3_backup`），不能用「停服 cp」 |
| C3 | 后端**所有同步视图共用唯一线程** | [`deploy/gipfel.service:26-31`](../../../deploy/gipfel.service) + Django `sync_to_async(thread_sensitive=True)` | 快照产生的任何锁等待都会**直接卡住全场** → 必须限速、切片、低优先级、可退避 |
| C4 | 数据库是 SQLite，**回滚日志模式、无 WAL、无 busy timeout 配置** | [`backend/backend/settings.py:305-310`](../../../backend/backend/settings.py) | 读事务与写事务**互斥**；长读事务会饿死写者 → 禁止「一次性长事务拷整个库」 |
| C5 | 应用以 `gipfel` 用户运行，systemd 沙箱 `ProtectSystem=full` + 显式 `ReadWritePaths` | [`deploy/gipfel.service:51-69`](../../../deploy/gipfel.service) | 快照服务必须**独立于该沙箱**，且自身也要收紧（见 §11） |
| C6 | 现场**无外网、无 UPS、可能没有第二台服务器** | v2.txt P10「需携带插线板」 | 异地副本只能是 **U 盘 / 第二台笔记本**，且「介质不在」必须**报警而不是静默跳过** |
| C7 | 运营者是学生主席团，不是运维 | 原报告 §2.10 | 一键、一行输出、纸质卡片、失败必须「吼」出来 |

### 1.3 需求清单

| ID | 需求 | 验收方式 |
| --- | --- | --- |
| **R1** | 在线生成**自洽**的 SQLite 快照，不依赖停服 | `PRAGMA integrity_check` = ok；`django_migrations` / 业务表计数与 manifest 一致 |
| **R2** | 每个财年**开始前**必须有一个快照 | 赛后核对：L1 快照数 = 财年时段数（7） |
| **R3** | 财年边界**前后**各有一个快照，可用于比对「财年定时器到底跑了没有」 | 两个快照的 `company_field_values` 行数与值指纹不同 |
| **R4** | 稳态滚动快照，RPO ≤ 10 分钟 | 稳态时段内相邻快照间隔 ≤ 10 分钟 |
| **R5** | 每份快照都有 manifest + 校验和 + 行数指纹，可独立验证 | `snapshot.sh verify <id>` 退出码 0 |
| **R6** | 至少两份物理副本（本机 + 异地介质） | `snapshot.sh status` 显示异地落后 ≤ 1 个快照 |
| **R7** | 单次快照对应用线程的阻塞**可测量且有上限** | 记录 `blocked_ms`；超阈值自动降级/退避 |
| **R8** | 恢复有**决策树 + 影子核对**，不做盲目覆盖 | T-1 演练通过 |
| **R9** | 快照与**记账账本**（`contract_watcher`）共用时间线键 | manifest 中的 `sid` 同时写在账本侧 |
| **R10** | 保留策略适配 U 盘容量，且**永不删当前比赛的关键点** | `snapshot.sh prune --dry-run` 输出可审 |
| **R11** | 失败**不静默**：写日志 + 退出码 + `LAST_FAILURE` + 纸质检查点变红 | 人为制造失败，现场能一眼看到 |
| **R12** | 全部新增物在仓库外，**可整目录删除** | `git status` 无变化 |

**非目标（明确不做）**：

- N1 不修改任何现有代码（含加 `signals` 钩子、加 management command、加 API 端点）。
- N2 不引入 PostgreSQL / Redis / 容器 / 云备份。
- N3 不做 WAL 归档或 PITR（SQLite 回滚日志模式下不适用）。
- N4 不做自动恢复到生产（恢复**必须人工确认**）。
- N5 不做自定义增量/去重算法（用硬链接 + 分层保留即可）。
- N6 不备份 `node_modules` / `.venv` / `frontend-dist`（可由代码重建）。

### 1.4 RPO / RTO 目标

| 时段 | RPO 目标 | 依据 |
| --- | --- | --- |
| 开赛前（T-0 07:00） | 0 | 手工"开赛基线"快照 |
| 每个财年开始前 | 0 | L1 边界快照（事件 + 定时双保险） |
| 比赛进行中 | ≤ 10 分钟 | L0 滚动 |
| 每日收尾（18:30 / 17:45） | 0 | L2 全量 bundle |
| 记账账本 | 每次 flush / 财年末 | 见 §5.4 |

| 场景 | RTO 目标 |
| --- | --- |
| 库文件损坏但服务器在 | ≤ 10 分钟（停服 → 恢复 → 健康检查） |
| 服务器整机不可用 | ≤ 30 分钟（换机 + 部署（已有脚本）+ 恢复最近快照） |
| 数据逻辑错误（要回到某个财年边界） | ≤ 20 分钟（含影子核对） |

---

## 2. 现状可复用的资产（不要重造）

| 资产 | 位置 | 复用方式 |
| --- | --- | --- |
| **一致性快照语义**（`VACUUM INTO` + 头校验 + `integrity_check` + python3 缺失时告警降级） | [`scripts/lib/deploy-common.sh:173-244`](../../../scripts/lib/deploy-common.sh) | **语义照抄**到独立脚本；不 `source` 该文件（避免快照系统依赖部署目录内容），但保留同样的校验步骤与告警口径 |
| 预迁移快照 + 回滚指引 | [`scripts/deploy-linux.sh:317-334`](../../../scripts/deploy-linux.sh)、[`deploy-common.sh:246-268`](../../../scripts/lib/deploy-common.sh) | 保留其原位职责；快照系统**不替代**它，只补齐「运行期」缺口 |
| **财年更迭日志锚点**（零代码改动的天然事件源） | [`backend/apps/competitions/signals.py:106-110`](../../../backend/apps/competitions/signals.py)：`logger.info("[fiscal-year] %s：比赛 #%s 财年 %s（id=%s）%s → %s", transition, …)`，`transition ∈ {FY_START, FY_END}`（[`:55-66`](../../../backend/apps/competitions/signals.py)） | 侦听 `backend/logs/gipfel.log` 的 `[fiscal-year]` 行 → 触发快照 |
| 日志落盘与轮转 | [`backend/backend/settings.py:410-446`](../../../backend/backend/settings.py)：`TimedRotatingFileHandler`，`LOG_DIR/gipfel.log`，`when=midnight`，`backupCount=14`，root `level=INFO`（[`:167`](../../../backend/backend/settings.py)） | 锚点行确实以 INFO 写入文件；侦听器必须容忍每日轮转（用 `tail -F` 语义） |
| 健康检查 | [`docs/OPS.md:64`](../../OPS.md)：`curl -sS http://127.0.0.1:8000/api/health` → `{"code":0,…,"status":"ok"}` | 恢复后的验收探针；也用于判断服务是否繁忙 |
| 版本真源 | [`backend/apps/auth/views.py:34-38`](../../../backend/apps/auth/views.py)：`/api/version` 读**项目根** `VERSION.json` | manifest 记录 `VERSION.json` + `git rev-parse HEAD`，保证「数据 ↔ 代码」可配对 |
| 部署/升级/迁移脚本 | `scripts/deploy-linux.sh`、`update-from-github.sh`、`migrate-server.sh`、`verify-migration.sh` | 换机恢复路径已有，快照只负责把「数据」交到它手里 |

**数据面清单（快照要覆盖的东西）**

| 数据 | 路径（生产） | 性质 | 能否从代码重建 |
| --- | --- | --- | --- |
| 业务库 | `/opt/gipfel/backend/db.sqlite3` | **唯一不可重建** | ❌ |
| 上传文件 | `/opt/gipfel/backend/uploads/` | 部分不可重建 | ⚠️ 部分 |
| 密钥与配置 | `/opt/gipfel/backend/.env` | 不可重建（`JWT_SECRET` 变了全员登出，[`settings.py:23-41`](../../../backend/backend/settings.py)） | ❌ |
| 版本号 | `/opt/gipfel/VERSION.json` | 可重建但必须配对 | ✅ |
| 活动配置 | `/etc/nginx/sites-available/gipfel.conf`、`/etc/systemd/system/gipfel*.service` | 可重建但耗时 | ✅ |
| 应用日志 | `/opt/gipfel/backend/logs/gipfel.log`（+ `/var/log/gipfel/access.log`） | 事后复盘唯一凭据（O-02/O-13） | ❌ |
| **记账账本**（在记账机上） | `contract_watcher/data/watcher.db`、`data/state.json`、`books/company_<id>.xlsx`、`records/` | 18:30 对账底稿（O-03 的兜底） | ❌ |

---

## 3. 总体架构

```
                    ┌──────────────────────────── 应用（只读接触） ─────────────────────────────┐
                    │  daphne(gipfel)  127.0.0.1:8000     daphne(logviewer) 127.0.0.1:8121        │
                    │  /opt/gipfel/backend/{db.sqlite3, uploads/, .env, logs/gipfel.log}        │
                    └───────▲──────────────────────────────────────────────────────────▲─────────┘
                            │ 只读：sqlite3_backup / VACUUM INTO                        │ tail -F
                            │ HTTP：/api/health（空闲探测、恢复后验收）                  │
┌───────────────────────────┴───────────────────────────────────────────────────────────┴─────────┐
│                        快照系统（/opt/gipfel-snapshot/，仓库外，独立 systemd 单元）                │
│                                                                                                 │
│  snapshot.sh  ── create|verify|list|status|restore|prune|sync      （唯一写入口，flock 串行）    │
│      ├─ 采集器   sqlite 切片备份 + uploads(硬链接) + .env/配置/日志尾 + manifest + SHA256SUMS     │
│      ├─ 校验器   integrity_check + 表行数指纹 + django_migrations 计数 + 头部魔数                │
│      └─ 落盘器   .tmp-<id>/ → fsync → 原子 rename → fsync 父目录 → 更新 LATEST 指针               │
│                                                                                                 │
│  gipfel-snapshot-watch.service   tail -F gipfel.log 抓 [fiscal-year] → 空闲探测 → 触发 create     │
│  gipfel-snapshot.timer           兜底：比赛日 07:00–20:00 每 10 分钟（L0）；每天 03:00（L2）       │
│  gipfel-snapshot-sync.timer      异地：每 5 分钟把新快照 rsync 到第二台机 / U 盘挂载点            │
└───────────────────────────────┬─────────────────────────────────────────────────────────────────┘
                                │
                    /var/backups/gipfel/   （0700 root:root，**绝不在 web 可达目录内**）
                    ├─ 20260729T070500+0800_baseline-t0/        L1/L2 全量 bundle
                    ├─ 20260729T101500+0800_fy-ai-start/        ↑ 每个财年边界一份
                    ├─ 20260729T140000+0800_l0/                 L0 仅 DB，滚动保留
                    ├─ LATEST -> 20260729T140000+0800_l0
                    ├─ LAST_FAILURE   （存在即代表最近一次失败，现场检查点看这个）
                    └─ snapshot.log   （append-only，含每次的耗时/阻塞毫秒/校验结果）
```

**组件清单**

| 组件 | 位置 | 职责 | 运行身份 | 触发 |
| --- | --- | --- | --- | --- |
| `snapshot.sh` | `/opt/gipfel-snapshot/snapshot.sh` | 唯一写入口（create/verify/list/status/restore/prune/sync），`flock` 串行 | root（单元内 `ReadOnlyPaths=/opt/gipfel`） | 人工 / watch / timer |
| `gipfel-snapshot-watch.service` | `/etc/systemd/system/` | `tail -F` 日志抓 `[fiscal-year]` → 空闲探测 → 触发 create | root | `Restart=always` |
| `gipfel-snapshot.timer` | `/etc/systemd/system/` | 兜底周期快照 + 每日全量 | — | `OnCalendar` |
| `gipfel-snapshot-sync.timer` | `/etc/systemd/system/` | 异地副本同步 + 介质缺席告警 | root | `OnUnitActiveSec=5min` |
| `snapshot-card.md` | `/opt/gipfel-snapshot/` | 打印的现场卡片（3 条命令 + 1 个检查点 + 恢复决策树） | — | 打印 |
| `restore-drill.sh` | `/opt/gipfel-snapshot/` | T-1 天的恢复演练（影子实例核对） | root | 人工 |

**为什么放在 `/var/backups/gipfel` 而不是 `/opt/gipfel/_backup`**

1. `_backup/<时间戳>` 是部署脚本的语义（「这一轮升级前的回滚点」），混入运行期快照会让人在升级时**误删/误选**。
2. `/opt/gipfel` 是 git 仓库根目录，`update-from-github.sh` 会在此执行 `git` 操作；快照不应与代码工作区共享生命周期。
3. FHS 语义正确，且便于在 U 盘/第二台机上沿用同一路径。

---

## 4. 快照定义

### 4.1 分层

| 层 | 名称 | 内容 | 触发 | 保留 | 体积估算* |
| --- | --- | --- | --- | --- | --- |
| **L0** | 稳态滚动 | 仅 `db.sqlite3`（切片备份）+ manifest | 定时器每 10 分钟 | 最近 6 份 + 每小时 1 份保留当天 | ~DB 大小 |
| **L1** | 财年边界 | `db.sqlite3` + 配置 + 日志尾 + **前后快照配对标记** | 事件（`[fiscal-year]`）+ 赛程表定时 | **全留**（共 ~14 份） | ~DB 大小 |
| **L2** | 每日收尾 | 全量 bundle：DB + `uploads/` + `.env` + `VERSION.json` + git 指纹 + 活动配置 + 日志 | 18:30 / 17:45 + 人工 | 全留 | DB + uploads |
| **L3** | 赛后归档 | L2 + 记账账本 + 代码 bundle（`git bundle --all`）+ 准备归档导出 | 闭幕式后人工 | 全留 | 最大 |

\* 本机实测口径：`db.sqlite3` ≈ 4.77 MB（含 7118 行 `audit_log`）。两天 100 客户端会把审计表推到几万行 → **生产按 20–80 MB/库估算**；L0 只保留 ~15 份，全层合计按 **≤ 2 GB/天** 规划（U 盘 32 GB 足够，且必须留 50% 余量）。

### 4.2 内容清单

```text
<sid>/                                   # sid = 20260729T140500+0800_fy-1-start
├── manifest.json                        # 见 §4.4（唯一权威元数据）
├── SHA256SUMS                           # 所有文件的 sha256
├── db/
│   ├── db.sqlite3                       # 一致性副本（切片备份 / VACUUM INTO）
│   └── db.sqlite3.info                  # page_size/page_count/schema_version/freelist
├── uploads/                             # 硬链接森林（--link-dest 到上一份 L2），仅 L2/L3
├── config/
│   ├── env.snapshot                     # .env 副本，0600，**含密钥**
│   ├── VERSION.json
│   ├── git.txt                          # rev-parse HEAD / describe / status --porcelain
│   ├── pip-freeze.txt                   # 后端 venv 依赖指纹（只读导出）
│   ├── nginx-gipfel.conf                # 从 /etc 取**活动**配置，而非仓库版本
│   └── gipfel.service / logviewer.service
├── logs/
│   ├── gipfel.log.tail                  # 最后 N MB（默认 8 MB）
│   └── access.log.tail                  # 仅 L1/L2
└── verify.txt                           # 校验过程的原始输出（integrity_check 等）
```

**排除项（明确不备份）**：`node_modules/`、`.venv/`、`frontend-dist/`、`staticfiles/`、`__pycache__/`、`*.pyc`、`_backup/`、`snapshot-tmp/`。
**绝不入包**：`/var/backups/gipfel` 自身、任何 U 盘挂载点内的循环目录。

### 4.3 目录布局与命名

```
/var/backups/gipfel/
├── <sid>/                     sid = <本地时间 YYYYMMDDTHHMMSS±HHMM>_<触发标签>
├── LATEST -> <sid>            最近一次**校验通过**的快照
├── LATEST-GOOD-FY -> <sid>    最近一次财年边界快照（恢复默认选它）
├── LAST_FAILURE               存在 = 最近一次失败（内容含时间/原因/日志尾）
├── snapshot.log
└── .tmp-<sid>/                采集中的临时目录（落盘后 rename，绝不留半个成品）
```

触发标签取值（固定枚举，便于人工识别与 prune）：

| 标签 | 含义 |
| --- | --- |
| `baseline-t0` | 开赛当天基线（T-0 手工） |
| `fy-<year>-pre` / `fy-<year>-post` | 财年 **前/后** 配对（由事件触发） |
| `daily-close` | 每日收尾全量 |
| `l0` | 稳态滚动 |
| `pre-deploy` / `pre-restart` | 部署/重启前 |
| `manual-<备注>` | 人工一键 |
| `drill` | 恢复演练产物（不影响正式指针） |

### 4.4 `manifest.json` 字段规范

```json
{
  "sid": "20260729T140500+0800_fy-1-pre",
  "schema": 1,
  "created_at_local": "2026-07-29T14:05:00+08:00",
  "created_at_utc": "2026-07-29T06:05:00Z",
  "host": "gipfel-prod",
  "trigger": { "kind": "fiscal-event", "anchor": "FY_END", "delay_s": 3, "source": "gipfel.log" },
  "level": "L1",
  "code": {
    "version_json": "1.4.0",
    "git_head": "5cb34682b7fb8bc9cf96aa6e4e3dab3a69c602ee",
    "git_dirty": false,
    "pip_freeze_sha256": "…"
  },
  "db": {
    "source": "/opt/gipfel/backend/db.sqlite3",
    "method": "sqlite3_backup_sliced",
    "pages_per_step": 256, "sleep_s": 0.05,
    "size_bytes": 51234567,
    "page_size": 4096, "page_count": 12508, "schema_version": 137,
    "integrity_check": "ok",
    "tables": {
      "django_migrations": 128, "competitions": 4, "users": 6, "companies": 12,
      "company_field_values": 104, "industry_fields": 55, "contracts": 3,
      "contract_field_effects": 0, "fiscal_years": 2, "audit_log": 7118
    },
    "field_value_fingerprint": "sha256:<对 (company_id, field_key, value) 排序后连接>"
  },
  "fiscal_state": [
    { "competition_id": 189, "year": 2026, "status": "ACTIVE", "phase": "ACTIVE" }
  ],
  "perf": { "duration_ms": 1840, "blocked_ms_est": 220, "io_priority": "idle", "nice": 19 },
  "uploads": { "mode": "hardlink", "link_dest": "20260728T183000+0800_daily-close", "files": 0, "bytes": 0 },
  "sources": { "env": true, "version_json": true, "nginx": true, "systemd": true, "log_tail_bytes": 8388608 },
  "verify": { "result": "pass", "checks": ["magic","integrity_check","table_counts","sha256","migrations_nonzero"] },
  "warnings": ["uploads 与 db 之间存在有界不一致：采集 db 后 120 ms 内 uploads 发生变化"]
}
```

**字段来源（全部只读 SQL / 只读命令）**

| 字段 | 来源 |
| --- | --- |
| `db.tables.*` | **对快照副本**执行 `SELECT count(*) FROM <table>`（不是对活库 —— 这样验证的是「恢复出来能不能用」） |
| `db.integrity_check` | 对副本 `PRAGMA integrity_check` |
| `db.schema_version` / `page_*` | 副本 `PRAGMA schema_version` / `page_size` / `page_count` |
| `field_value_fingerprint` | 副本 `SELECT company_id, industry_field_id, value FROM company_field_values ORDER BY 1,2,3` 流式哈希 —— **这是 R3「财年定时器到底跑了没有」的判定依据** |
| `fiscal_state` | 副本 `SELECT competition_id, year, status, phase FROM fiscal_years ORDER BY year` |
| `code.*` | `cat VERSION.json`、`git -C /opt/gipfel rev-parse HEAD`、`git status --porcelain`、`pip freeze`（只读） |
| `perf.blocked_ms_est` | 采集期间对活库做 `BEGIN IMMEDIATE; ROLLBACK` 探测的累计等待毫秒（见 §5.2） |

### 4.5 一致性语义（诚实地说明边界）

| 对象 | 一致性 | 说明 |
| --- | --- | --- |
| `db.sqlite3` | **强一致** | 单库事务快照：副本反映备份开始时（或各切片时刻之间的某个一致点）的完整事务状态，不会出现「半条事务」 |
| `db` ↔ `uploads/` | **有界不一致** | 两者无法在同一事务内采集。设计上**先采 DB、后采 uploads**，并把时间差写进 `warnings`。理由：DB 引用 uploads 记录，宁可有「DB 存在但文件缺失」（可发现、可补），不要「文件存在但 DB 无记录」（不可发现） |
| `db` ↔ 记账账本 | **弱一致（按事件对齐）** | 账本在另一台机器，用 `sid` 时间线键对齐；账本 flush 是事件驱动的 |
| 活动配置 ↔ 快照 | **弱一致** | 配置在比赛期不会变；若变了，manifest 的 sha256 会让差异可见 |

---

## 5. 采集实现

### 5.1 SQLite 一致性复制：两级策略

> **核心矛盾**：本项目 DB 是**回滚日志模式**（无 WAL，[`settings.py:305-310`](../../../backend/backend/settings.py)）。在该模式下，**一个读事务会阻塞所有写事务**；而应用全部同步视图共用**唯一线程**（C3）。所以「一次性长事务拷完整个库」= 在最坏时刻把全场卡住。

| 模式 | 原语 | 锁行为 | 用在哪 |
| --- | --- | --- | --- |
| **默认：切片备份** | Python `sqlite3.Connection.backup(dst, pages=256, sleep=0.05, progress=…)` | 每步只持有极短的页级锁，步间释放并 sleep → 把长事务切成 ~50 个短事务 | **所有自动触发**（L0/L1） |
| **快速：`VACUUM INTO`** | 仓库同款语义（[`deploy-common.sh:194-211`](../../../scripts/lib/deploy-common.sh)） | 单事务，最短总时长但**最长的连续阻塞** | 只在**确定无人在线**时用：`--fast`（赛前、午休、收尾后、换机演练） |

两者都**只读源库**（`file:<path>?mode=ro`，`timeout=30`），并都在副本上执行魔数校验 + `integrity_check` + 表计数（R1/R5）。

```
# 已实测的正确形态（本文档不落盘为可执行文件）
def copy_db(src, dst, pages=256, sleep=0.05, on_progress=None):
    """单次 backup() 调用即完成整库拷贝；用 progress 回调判定是否完成。"""
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    dst_con = sqlite3.connect(dst)
    seen = {"last": None}
    def cb(status, remaining, total):
        seen["last"] = (remaining, total)
        if on_progress:
            on_progress(remaining, total)
    try:
        src_con.execute("PRAGMA busy_timeout=30000")
        # 内部按 pages 分块、块间 sleep；调用返回时备份已完成（返回值恒为 None）
        src_con.backup(dst_con, pages=pages, sleep=sleep, progress=cb)
        assert seen["last"] is not None and seen["last"][0] == 0, "backup 未报告完成"
    finally:
        dst_con.close(); src_con.close()
    os.fsync(...)
```

> **实测结论（本次设计期间在本机实跑，Python 内置 SQLite 3.50.4）** —— 这一条修正了本方案 v1 初稿的错误写法：
> - `Connection.backup(target, *, pages=N, progress=cb, sleep=s)` **单次调用就完成整个备份**，内部按 `pages` 分块、块间 `sleep`；
> - 它的**返回值恒为 `None`**（不是 `True`）→ 初稿写的 `while True: if backup(...): break` **会死循环**，已修正为上面的形态；
> - 完成判定必须靠 `progress` 回调的 `remaining == 0`（实测：79 页库 + `pages=64` → 2 次回调，末次 `remaining=0`，总耗时 11 ms）；
> - `pages` / `sleep` 控制的是**分块粒度与块间让出频率**，不是「每次调用拷多少页」。
> - 实跑命令与输出留档于本方案的验收记录；落地时**必须在本机 4.77 MB 库上复跑一遍**（AC6）。

**为什么不用 `cp -a`**：与 [`deploy-common.sh:175-177`](../../../scripts/lib/deploy-common.sh) 的原话一致 —— 它会拿到「看起来正常、打开却可能损坏或缺最近事务」的文件，而这将是**唯一的回滚副本**。

### 5.2 对唯一线程的影响控制（R7）

四道闸门，任何一道不满足就**退避重试而不是硬上**：

1. **优先级**：`nice -n 19` + `ionice -c3`（idle 级 IO）。CPU/磁盘争用时让位给 daphne。
2. **切片**：`pages=256, sleep=0.05` → 每次持锁 ~1 个页组的时间，步间主动让出。
3. **占用探测（触发前）**：对活库尝试 `BEGIN IMMEDIATE` + `ROLLBACK`，`busy_timeout=0`：
   - 立刻成功 → 说明当前**没有写者在事务中**（财年定时器大概率跑完了）→ 可以采；
   - `SQLITE_BUSY` → 说明有写事务在跑（很可能就是 FY_START 定时器）→ 退避 2s/4s/8s…（上限 5 分钟）后重试；
   - 该探测自身只持锁微秒级，且**不写任何数据**；即使与应用写撞车，应用侧有默认 5s busy timeout 会自行重试。
4. **预算与熔断**：单次采集 `blocked_ms_est` 超过阈值（默认 **800 ms**）或总时长超过 **90 s** → 中止本次、删除临时目录、写 `LAST_FAILURE`、等下一个触发点。**绝不允许为了备份把比赛卡住。**

> **待实测（T-7 天）**：闸门 3 的写锁探测在**真实并发**下的判定准确性（特别是 `SQLITE_BUSY` 与 `SQLITE_LOCKED` 的区分）、以及 `blocked_ms_est` 的口径，都需要用真实负载标定后再定阈值。本方案的默认值是可调的初始值，不是实测值。

**为什么需要探测**：`FY_START` 事件发生在**定时器开始之前**（[`competitions/views.py:227`](../../../backend/apps/competitions/views.py) 在同一个请求里同步调用 [`timer.py:147`](../../../backend/apps/company_fields/timer.py)），而定时器会在该比赛**全部公司 × 全部启用字段**上写值。事件一到就快照 = 正好撞上写高峰。

### 5.3 uploads 与配置文件

| 内容 | 方法 | 注意 |
| --- | --- | --- |
| `uploads/` | `rsync -a --link-dest=<上一份 L2> /opt/gipfel/backend/uploads/ <sid>/uploads/` | 硬链接共享未变文件；**先 DB 后 uploads**；空目录也建（恢复时要求存在） |
| `.env` | `cp` + `chmod 600` + `chown root:root` | 含 `JWT_SECRET`/`LOGVIEWER_SECRET_KEY`（[`settings.py:23-64`](../../../backend/backend/settings.py)）→ 副本权限必须比原件更严 |
| `VERSION.json` / git 指纹 | `cat` / `git rev-parse` / `git status --porcelain` | `git_dirty=true` 必须显著告警（意味着线上代码与 commit 不符） |
| 活动 nginx / systemd | 从 `/etc/...` 取**实际生效**的文件，而不是仓库里的 `deploy/*` | 现场排查时「生效的那份」才有用 |
| 日志尾段 | `tail -c 8M` + 记录 inode/偏移 | 首行可能被截断 → manifest 记录 `truncated_at_head: true` |

### 5.4 跨机器：记账账本快照（R9）

`contract_watcher` 跑在**记账机（Windows）**上，数据不在服务器：`data/watcher.db`（SQLite 台账）、`data/state.json`、`books/company_<id>.xlsx`、`records/<key>/…`（原报告 §2.8 称其为「唯一真正好用的赛后取证/对账底稿」）。

设计：**账本侧独立快照 + 共用 sid**

1. 服务器每次 create 后，把 `sid` 写到 `/var/backups/gipfel/LATEST.sid`，并（可选）通过局域网共享/HTTP 暴露一个只读文本。
2. 记账机上一个 `.cmd`/PowerShell 脚本（卡片驱动，人工双击或计划任务每 15 分钟）：
   - 读 `LATEST.sid`（读不到就用本地时间戳，并在 manifest 标 `ledger_unaligned: true`）；
   - **先 flush**（`contract_watcher --flush` 或界面「立即记账」），确保 `books/*.xlsx` 是最新的（原报告 §2.8：记账默认只在达阈值/财年末/手动时落 Excel）；
   - 用 `sqlite3.exe ".backup"`（或 Python 一行）复制 `data/watcher.db`，再 `robocopy /MIR` 复制 `books/`、`records/`、`data/state.json`；
   - 目标目录名带同一个 `sid`，并写一份 `ledger-manifest.json`（文件数、xlsx 的 mtime、`check()` 平衡校验结果）。
3. **现场纪律**：18:30 收尾前必须做一次「账本快照 + `check()` 平衡校验」，否则「当天账表底稿」不成立。

> 该脚本属于**记账机上的新增文件**，不改 `contract_watcher` 任何代码。

### 5.5 校验（verify）

`snapshot.sh verify <sid>` 按顺序执行，任一失败即整体失败：

| # | 检查 | 判据 |
| --- | --- | --- |
| V1 | 文件与 `SHA256SUMS` 全部匹配 | 无 missing/mismatch |
| V2 | SQLite 魔数 `SQLite format 3` | 与 [`deploy-common.sh:218-221`](../../../scripts/lib/deploy-common.sh) 同口径 |
| V3 | `PRAGMA integrity_check` = `ok`，**且不抛异常** | 同 [`deploy-common.sh:222-241`](../../../scripts/lib/deploy-common.sh)。⚠ 实测：截断 8 KB 的库在 `sqlite3` 侧直接抛 `DatabaseError: database disk image is malformed`（发生在打开/首次查询时），**不是**返回非 `ok` 字符串 → verify 必须把「异常」与「非 ok」**同等当作失败** |
| V4 | 表行数与 manifest 一致 | 逐表比对 |
| V5 | `django_migrations` 行数 > 0 且与 manifest 一致 | 防止「空库/半初始化的库」被当成好快照 |
| V6 | `fiscal_years` 至少 1 行，且 `status` 分布与 `fiscal_state` 一致 | 防止「财年表被清空」的坏快照 |
| V7 | `manifest.json` 可解析且 `schema` 已知 | 前向兼容闸门 |
| V8 | 若 `level ≥ L2`：`uploads/` 目录存在 | 恢复脚本依赖它 |

校验结果同时写 `<sid>/verify.txt`（原始输出）与 `snapshot.log`（一行摘要）。**只有 V1–V8 全过，才会把 `LATEST` 指针指向它。**

---

## 6. 触发设计

### 6.1 触发矩阵

| 触发 | 类型 | 层 | 时机 | 幂等/去重 |
| --- | --- | --- | --- | --- |
| 开赛基线 | 人工 | L2 | T-0 07:00 | 同标签已存在且 <10 分钟则跳过 |
| `FY_END` 锚点 | 事件 | L1 | 日志出现后 **+3 s**，且空闲探测通过 | 同 `(competition,year,anchor)` 只做一次（状态文件） |
| `FY_START` 锚点 | 事件 | L1 | 日志出现后**等空闲探测通过**（定时器跑完） | 同上 |
| 赛程表定时 | 定时 | L1 | 每个财年时段**开始前 5 分钟**（见 §6.2） | 同分钟去重 |
| 稳态滚动 | 定时 | L0 | 比赛日 07:00–20:00 每 10 分钟 | 全局 `flock` |
| 每日收尾 | 定时 | L2 | 19:30（Day1）/ 17:50（Day2） | 每天一次 |
| 部署/重启前 | 人工（写进部署 SOP） | L1 | `systemctl restart` 之前 | 每次执行 |
| 人工一键 | 人工 | L1 | 任意时刻 | `--tag` 需唯一或自动加序号 |
| 赛后归档 | 人工 | L3 | 闭幕式后 | 一次 |

**两个"事件 + 定时"双保险的理由**：事件源是**日志行**，它可能因为日志级别被改、文件被轮转、服务被重启而漏掉；定时器是**时间**，它可能因为比赛整体顺延而错过。两者互不依赖。

### 6.2 与赛程表对齐（v2.txt）

规则文件 [`v2.txt:11`](source/v2.txt) 的原始安排：

| 日期 | 时段 | 活动 | 快照动作 |
| --- | --- | --- | --- |
| 7/29 | 7:30–8:30 | 签到 | 07:00 `baseline-t0`（L2，手工，**开赛前必做**） |
| 7/29 | 8:40–9:20 | 开幕式/办公室拍卖 | L0 滚动（每 10 分钟） |
| 7/29 | 9:20–10:50 | CFO培训/产业培训/BP评级 | L0 滚动 |
| 7/29 | **10:50–12:00** | **AI 财年** | 10:45 `fy-ai-pre`（L1）→ 结束时 `fy-ai-post` |
| 7/29 | 12:00–14:00 | 午休 | 12:00 `daily-close` 前哨（L2，含 uploads） |
| 7/29 | **14:15–16:00** | **第一财年** | 14:10 `fy-1-pre` → 16:00 `fy-1-post` |
| 7/29 | 16:00–16:30 | 下午茶 | L0 滚动 |
| 7/29 | **16:40–18:30** | **第二财年** | 16:35 `fy-2-pre` → 18:30 `fy-2-post` |
| 7/29 | 18:30–19:40 | 收尾核对账表 | 19:30 `daily-close`（L2 + 账本快照） |
| 7/30 | 7:30–8:30 | 签到 | 07:15 `baseline-day2`（L2） |
| 7/30 | **8:30–10:15** | **第三财年** | 08:25 `fy-3-pre` → 10:15 `fy-3-post` |
| 7/30 | **10:20–12:10** | **第四财年** | 10:20 `fy-4-pre` → 12:10 `fy-4-post` |
| 7/30 | 12:10–14:10 | 午休 | 12:10 `daily-close` 前哨 |
| 7/30 | **14:10–15:40** | **第五财年** | 14:05 `fy-5-pre` → 15:40 `fy-5-post` |
| 7/30 | **15:50–17:15** | **第六财年** | 15:45 `fy-6-pre` → 17:15 `fy-6-post` |
| 7/30 | 17:15–17:45 | 下午茶 | 17:50 `daily-close`（L2）+ 赛后 L3 |

> 快照**不占用**这些时段：L1 只含 DB（切片备份，50 MB 库约 1–3 s），安排在**边界前后 5 分钟的窗口**，且自带熔断（§5.2）。**绝不在财年推进的那一秒去抢写锁。**

### 6.3 财年事件侦听（零代码改动的关键）

事件源就是既有日志行（[`signals.py:107-110`](../../../backend/apps/competitions/signals.py)）：

```
[fiscal-year] FY_END：比赛 #189 财年 1（id=12）ACTIVE → CLOSED
[fiscal-year] FY_START：比赛 #189 财年 2（id=13）CLOSED → ACTIVE
```

侦听器草案：

```
tail -F --follow=name --retry /opt/gipfel/backend/logs/gipfel.log |
  while IFS= read -r line; do
    case "$line" in
      *"[fiscal-year]"*"FY_END"*)   on_event FY_END   ;;
      *"[fiscal-year]"*"FY_START"*) on_event FY_START ;;
    esac
  done
```

`on_event` 的语义差异（**这是本设计的要点**）：

- `FY_END`：**先做**。财年末状态是「已结束但还没重置」——这是最干净、最该留住的状态。等 3 s 让请求收尾，不需要空闲探测（此时定时器正在写值…若探测显示繁忙，就按退避策略等，最多 5 分钟）。
- `FY_START`：**等**。它的价值是「财年初值已写入」，所以必须等定时器写完。用 §5.2 的写锁探测判定「写者已退出」，再采一份，标签 `fy-<year>-post`。
- **配对价值（R3）**：把 `fy-N-pre` 与 `fy-N-post` 的 `field_value_fingerprint` 相比 —— **不同 = 定时器确实跑了；相同 = 定时器被 `lock.acquire(blocking=False)` 静默跳过或全部字段写失败**（[`timer.py:159-163`](../../../backend/apps/company_fields/timer.py)）。这给 O-02 的「静默半成功」提供了**与日志无关的第二证据链**。

**健壮性要求**：`tail -F` 语义（容忍每日轮转，[`settings.py:436`](../../../backend/backend/settings.py) `when=midnight`）；服务单元 `Restart=always` + 启动时 `tail -n 200` 回放（防止重启期间漏事件）；事件状态文件 `/var/backups/gipfel/.state/events` 做幂等。

### 6.4 兜底定时器

```ini
# 草案
[Unit] Description=Gipfel snapshot fallback timer
[Timer]
OnCalendar=*-*-* 07:00..20:00/10:00     # 比赛日白天每 10 分钟
OnCalendar=*-*-* 03:00                   # 每天凌晨全量
Persistent=true
RandomizedDelaySec=20                     # 避免与整点动作撞车
AccuracySec=5s
[Install] WantedBy=timers.target
```

> 非比赛日也跑（无害，L0 体积小、保留环会自然回收）。`skew`/延迟是刻意的：**让快照避开整点**（整点往往是主席团点财年的时刻）。

### 6.5 人工一键（现场卡片上的 3 条命令）

```bash
# ① 立刻做一份（最常用）
sudo /opt/gipfel-snapshot/snapshot.sh create --tag manual

# ② 看现在有没有可用的恢复点、异地副本跟没跟上
sudo /opt/gipfel-snapshot/snapshot.sh status

# ③ 校验某一份到底能不能用
sudo /opt/gipfel-snapshot/snapshot.sh verify LATEST-GOOD-FY
```

`status` 输出必须**一眼可读**（这是 C7 的核心）：

```
[快照状态] 2026-07-29 16:00:12 +0800
  最新可用     20260729T160000+0800_fy-1-post   校验 pass  大小 51 MB
  最近财年点   同上
  滚动快照     6 份（最近 16:00，最老 15:10）
  异地副本     已同步至 16:00（延迟 0 份）  ✅
  磁盘         已用 1.2 GB / 可用 41 GB
  最近失败     无
  结论：可用 ✅
```

---

## 7. 保留与空间

| 层 | 保留规则 | 依据 |
| --- | --- | --- |
| L0 | 环形保留最近 **6** 份；每小时的第 1 份保留至当天结束 | RPO 10 分钟 + 一天内可回看 |
| L1 | **全留**（本场比赛 ~14 份） | 每个财年边界都是关键判决点 |
| L2 | 全留至赛后 **90 天** | 对账/申诉窗口 |
| L3 | 永久（人工管理） | 赛后归档 |

**保护规则（prune 永不删）**：`baseline-*`、`fy-*-pre/post`、`daily-close`、`LATEST`、`LATEST-GOOD-FY`、任何 `level ≥ L2` 的 `drill`。
**安全阀**：`prune` 默认 `--dry-run`；要真删必须显式 `--yes`；先 `verify` 通过才允许删旧的；**若可用空间 < 20%，先告警、由人工决定是否放宽 L0 保留数，绝不自动删 L1/L2。**

空间估算（生产按 50 MB/库）：L0 6 份 ≈ 300 MB；L1 14 份 + uploads 硬链接 ≈ 800 MB；L2 2 份含 uploads ≈ 视 uploads 而定；**全天 ≤ 2 GB**。

---

## 8. 异地副本与介质

| 目标 | 方式 | 介质缺席时 |
| --- | --- | --- |
| 第二台笔记本/服务器 | `rsync -a --delete-after <sid>/ user@peer:/var/backups/gipfel/`（走局域网、不依赖外网） | **写 `LAST_FAILURE` + `status` 标红**；不静默跳过 |
| U 盘 | `rsync` 到挂载点 `/media/<label>`，完成后 `sync` 再 `umount` | 同上 + 卡片注明「插上 U 盘后手动跑 `snapshot.sh sync`」 |
| 第三份（可选） | 赛场第二块 SSD / 主席团笔记本 | — |

**纪律**：*任何时刻至少有一份副本在与服务器不同的物理设备上*。这一点在 T-0 清单里作为硬性检查项（赛事期间服务器若放在会场，插线板被踢掉即全场终止 —— 快照是唯一能救回半天数据的东西）。

**断点续传**：rsync 天然支持；同步只传新增快照目录，且用 `--link-dest` 在远端复用。

---

## 9. 恢复设计

### 9.1 决策树

| 症状 | 动作 | 用哪份快照 |
| --- | --- | --- |
| 页面大面积超时 / `database is locked` | **先别恢复**：停写 60 s，看是否有财年推进在跑（原报告应急卡） | — |
| 服务起不来、库打不开 | 恢复 | `LATEST`（L0 即可，若 uploads 无关） |
| 库损坏（`integrity_check` 失败） | 换上一份 `LATEST-GOOD-FY` | L1/L2 |
| 数据逻辑错（某财年字段被重置/串财年） | 回到该财年**边界前**快照 | `fy-N-pre` |
| 怀疑有人改数（O-13） | **不恢复**，改为「快照 diff 举证」（见 §12） | 两份相邻快照 |
| 断电/重启后库异常 | 先 `verify LATEST`；坏了再用 `LATEST-GOOD-FY` | L1/L2 |
| 整机不可用 | 换机 → 跑 `deploy-linux.sh` → 恢复 | 最近 L2 |

### 9.2 in-place 恢复（标准流程）

> 原则：**先留证据、再动手；先影子、后生产。**

```bash
# 0) 声明与确认（人工，必须看到这一行再继续）
sudo /opt/gipfel-snapshot/snapshot.sh status

# 1) 冻结"现状"作为证据（坏库也要留！）
sudo /opt/gipfel-snapshot/snapshot.sh create --tag forensics-broken --force

# 2) 校验目标快照
sudo /opt/gipfel-snapshot/snapshot.sh verify 20260729T160000+0800_fy-1-post

# 3) 停服务（两个）
sudo systemctl stop gipfel gipfel-logviewer

# 4) 清理 SQLite 残留（**极易漏**）
#    回滚日志模式下，残留的 db.sqlite3-journal 会在启动时被"回滚"，
#    可能把刚恢复的库改回旧状态。必须删干净 sidecar 文件。
sudo rm -f /opt/gipfel/backend/db.sqlite3-journal \
           /opt/gipfel/backend/db.sqlite3-wal \
           /opt/gipfel/backend/db.sqlite3-shm

# 5) 覆盖数据库
sudo install -o gipfel -g gipfel -m 0640 \
     /var/backups/gipfel/<sid>/db/db.sqlite3 /opt/gipfel/backend/db.sqlite3

# 6) 覆盖 uploads（若快照含 uploads）
sudo rsync -a --delete /var/backups/gipfel/<sid>/uploads/ /opt/gipfel/backend/uploads/
sudo chown -R gipfel:gipfel /opt/gipfel/backend/uploads

# 7) .env：**默认不动**。只有确实丢失/损坏时才恢复 ——
#    换回旧 .env 会换掉 JWT_SECRET/SECRET_KEY，导致全员登出（settings.py:23-64）。
#    sudo install -o gipfel -g gipfel -m 0600 /var/backups/gipfel/<sid>/config/env.snapshot /opt/gipfel/backend/.env

# 8) 迁移对齐（快照可能来自较早的代码版本）
sudo -u gipfel /opt/gipfel/backend/.venv/bin/python /opt/gipfel/backend/manage.py migrate --noinput

# 9) 起服务并验收
sudo systemctl start gipfel gipfel-logviewer
curl -sS http://127.0.0.1:8000/api/health      # 期望 {"code":0,…,"status":"ok"}
curl -sS http://127.0.0.1:8000/api/version     # 期望 version 与当前前端一致（否则触发 O-11 封锁！）

# 10) 记录
sudo /opt/gipfel-snapshot/snapshot.sh create --tag manual-post-restore
```

**步骤 4 与步骤 7 是这份流程里最容易出事的两步**，卡片上要单独标红。

### 9.3 影子核对（强烈推荐，尤其"数据逻辑错"场景）

不直接覆盖生产，而是**在同一台机器上用不同端口跑一个只读影子实例**，先用眼睛确认数据对不对：

```bash
# ① 校验并复制到影子目录
sudo mkdir -p /var/tmp/gipfel-shadow && cd /var/tmp/gipfel-shadow
sudo install -o gipfel -g gipfel -m 0640 \
     /var/backups/gipfel/<sid>/db/db.sqlite3 /var/tmp/gipfel-shadow/db.sqlite3

# ② 用一份独立 .env（改 PORT=8001、PORT 与 LOG_VIEWER_PORT 错开）
sudo -u gipfel cp /opt/gipfel/backend/.env /var/tmp/gipfel-shadow/.env
sudo -u gipfel sed -i 's/^PORT=.*/PORT=8001/' /var/tmp/gipfel-shadow/.env

# ③ 起影子实例（显式指定 DB 与环境文件；**用环回地址，绝不改 nginx**）
cd /opt/gipfel/backend
sudo -u gipfel env $(cat /var/tmp/gipfel-shadow/.env | xargs) \
  /opt/gipfel/backend/.venv/bin/daphne -b 127.0.0.1 -p 8001 backend.asgi:application

# ④ 核对（主席团用 curl 或浏览器 127.0.0.1:8001 看关键数字）
curl -sS http://127.0.0.1:8001/api/health
sqlite3 /var/tmp/gipfel-shadow/db.sqlite3 \
  "SELECT year,status FROM fiscal_years ORDER BY year;
   SELECT count(*) FROM company_field_values;
   SELECT count(*) FROM contract_field_effects;"

# ⑤ 确认无误后再执行 §9.2 的 in-place 恢复
```

> 影子实例是**只读使用**（只查不改）。核对完 `kill` 掉即可，不留痕迹。**注意**：`.env` 里若含 `ALLOWED_HOSTS`/端口相关配置，务必确认不会与生产抢端口或抢运行目录。

### 9.4 恢复演练（T-1 必做，R8）

`restore-drill.sh` 在**演练模式**下做一遍完整恢复，但走影子路径、不动生产：

| 步 | 演练内容 | 通过判据 |
| --- | --- | --- |
| D1 | 取 `LATEST-GOOD-FY`，`verify` | pass |
| D2 | 影子实例起在 8001 | `/api/health` = ok |
| D3 | 数据核对：`fiscal_years` 状态、`company_field_values` 计数、`contracts` 计数 | 与 manifest 一致 |
| D4 | 用**一个真账号**登录影子实例，打开公司详情页 | 页面有数据 |
| D5 | 计时 | 从「决定恢复」到「影子可用」 ≤ 10 分钟 |
| D6 | 人为破坏演练：把一份快照的 DB 尾部截断，跑 `verify` | 必须**失败**（证明校验有效，不是橡皮图章） |
| D7 | 写成卡片并打印 | 卡片在手 |

### 9.5 恢复时的已知坑

| 坑 | 原因 | 对策 |
| --- | --- | --- |
| 残留 `-journal` 把库回滚 | SQLite 回滚日志模式 | 恢复前 `rm -f *.sqlite3-journal/-wal/-shm`（§9.2 步 4） |
| 恢复后全场被版本封锁（O-11） | 快照的 `VERSION.json`/代码与当前前端不一致 | 恢复后立刻 `curl /api/version` 比对；不一致就先修版本再放人进来 |
| 恢复后全员登出/被踢 | `.env` 的 `JWT_SECRET` 变了，或库里的 `token_version` 比当前 token 旧 | 默认不覆盖 `.env`；提前广播「可能要重新登录」 |
| 恢复后部分新数据消失 | 快照就是那个时刻 | 恢复前一定先做 `forensics-broken` 快照；必要时事后从坏库里 `sqlite3 .recover` 捞差量 |
| 权限/属主不对，服务起不来 | `install` 未指定属主 或 `chown` 漏了 uploads | 步骤里固定 `-o gipfel -g gipfel -m 0640`；结束后 `ls -l` 核对 |
| 迁移未跑 → 500 | 快照来自较早代码 | 步 8 `manage.py migrate --noinput` |

---

## 10. 观测、告警与现场可用性

| 面 | 设计 |
| --- | --- |
| 日志 | `/var/backups/gipfel/snapshot.log`，append-only，每行含 `sid/trigger/level/duration_ms/blocked_ms_est/result` |
| 失败信号 | `LAST_FAILURE` 文件（存在即异常），`status` 子命令首行标红；systemd 单元退出码非 0 → `journalctl -u gipfel-snapshot*` |
| 现场检查点（纸质卡） | 每个财年开始**前**看一眼 `snapshot.sh status` 的「结论」行：`可用 ✅` 才推进财年 |
| 与第二块屏 | 原报告 T-0 第 12 条要求第二块屏 `tail -f gipfel.log` —— 建议同一块屏用 `tmux` 分两格：一格 `tail -f backend/logs/gipfel.log`，一格 `watch -n 30 snapshot.sh status` |
| 不污染应用日志 | 快照系统**只写自己的日志**，绝不写 `backend/logs/`（避免被日志查看器/审计误读） |
| 不静默 | **任何**失败路径都必须至少落一行日志 + 置 `LAST_FAILURE`；禁止 `|| true` 式吞错（这正是 `deploy-linux.sh:322` 修过的老毛病） |

---

## 11. 安全与合规

| 风险 | 说明 | 对策 |
| --- | --- | --- |
| **快照落在 web 可达目录** | nginx [`deploy/nginx-gipfel.conf:80-81`](../../../deploy/nginx-gipfel.conf)：`location /uploads/ { alias /opt/gipfel/backend/uploads/; }` —— 放进 `uploads/` 的一切都能被公网下载；快照含 `.env`（`JWT_SECRET`）与全部 bcrypt 哈希 | 快照根固定 `/var/backups/gipfel`（0700 root:root），**代码里硬校验**：目标路径不得位于 `/opt/gipfel/backend/uploads`、`/opt/gipfel/frontend-dist`、`/opt/gipfel/backend/staticfiles` 之下 |
| 快照工具越权改库 | 工具以 root 跑，一旦有 bug 可能写坏活库 | 双保险：① 单元内 `ProtectSystem=strict` + `ReadOnlyPaths=/opt/gipfel` + `ReadWritePaths=/var/backups/gipfel`（与应用单元同款加固思路，[`gipfel.service:51-69`](../../../deploy/gipfel.service)）；② 工具内所有源库连接强制 `file:…?mode=ro` |
| `.env` 副本泄露 | 含 `JWT_SECRET` 与 `SECRET_KEY`（[`settings.py:23-64`](../../../backend/backend/settings.py)） | 副本 `0600 root:root`；U 盘必须 LUKS/BitLocker 或物理保管；`status` 不打印任何密钥内容 |
| U 盘丢失 | 含全量业务数据与哈希 | 登记编号与保管人；赛后擦除 |
| 日志查看器沙箱边界 | 其单元已收窄到 `/run/gipfel-logviewer` + `/var/log/gipfel-logviewer`（[`logviewer.service:64`](../../../deploy/logviewer.service)） | 快照目录**不要**加进它的可写/可读路径，保持零接触 |
| 快照工具成为新攻击面 | 新增了一个 root 服务 | 不监听端口、不接网络；`tail -F` 只读本地文件；脚本 `0700 root:root` |

---

## 12. 与既有缺陷的关系（收益矩阵）

| 缺陷 | 快照系统的贡献 | 机制 |
| --- | --- | --- |
| **O-05（P0，无备份）** | **直接解** | L1/L2 快照 + 异地副本 + 校验 + 恢复演练；不再依赖不存在的 `scripts/backup.sh` |
| **O-02（P0，财年静默半成功）** | **提供外部第二证据链** | `fy-N-pre` 与 `fy-N-post` 的 `field_value_fingerprint` 对比：相同 = 定时器没跑成。不再只依赖 `gipfel.log` 的一行 warning |
| **O-13（P1，字段变更无审计）** | **部分补位（唯一可行的外部手段）** | 两个相邻快照的 `company_field_values` 可做**逐字段 diff**（谁从 X 变成 Y 差不出「谁」，但能差出「什么时候变的」）→ 把举证从「完全查不到」提升到「可定位到 10 分钟窗口」 |
| **O-03（P0，无报表）** | 不解决，但保证账本底稿不丢 | §5.4 的账本快照 + `check()` 平衡校验 |
| **O-04（P0，单线程）** | **必须不加重** | 切片备份 + nice/ionice + 写锁探测 + 熔断（§5.2）；**反面教材就是把 `VACUUM INTO` 放在整点财年推进的同一秒** |
| **O-11（P1，版本硬封锁）** | 降低恢复风险 | manifest 记录 `VERSION.json` + git commit → 恢复后能立刻判断会不会触发封锁 |
| **O-15（P1，从未真机验证）** | 提供可重复的验证手段 | `verify` + `restore-drill.sh` 本身就是可执行的验证脚本 |

---

## 13. 落地步骤

### T-7 天（准备，约 60 分钟）
1. 建目录与脚本：`/opt/gipfel-snapshot/{snapshot.sh,snapshot-watch.sh,restore-drill.sh,snapshot-card.md}`，`0700 root:root`。
2. 写 `/etc/systemd/system/gipfel-snapshot-watch.service`、`gipfel-snapshot.timer`、`gipfel-snapshot-sync.timer`；`systemctl daemon-reload && enable --now`。
3. 在本机 4.77 MB 库上**实测**切片参数（`pages`/`sleep`）与 `blocked_ms_est`，确定 `budget` 阈值（AC6）。
4. 人为制造 3 种失败（磁盘满、校验失败、介质缺席），确认 `LAST_FAILURE` + 日志 + 退出码都正确（R11）。
5. **跑一次完整恢复演练**（§9.4）。

### T-1 天
6. 打印现场卡片（3 条命令 + status 输出示例 + 恢复决策树 + 两步标红：`rm -f *-journal`、`.env` 默认不动）。
7. 准备 U 盘（格式化 + 打标签 + 写命令卡）与第二台机器。
8. 与记账员对齐 §5.4 的账本快照流程，确认 `check()` 平衡校验能跑通。
9. 确认 systemd timer 在重启后仍生效：`systemctl list-timers | grep snapshot`。

### T-0（开赛当天）
10. 07:00 `sudo snapshot.sh create --tag baseline-t0`（+ 异地同步 + 校验），在卡片上打勾。
11. 每个财年开始**前**：看 `snapshot.sh status` 的结论行；结束时确认 `fy-N-post` 已生成。
12. 18:30 / 17:45 收尾：`create --tag daily-close` + 账本快照 + 异地同步。
13. 闭幕式后：`create --tag final`（L3，含 `git bundle` + 准备归档导出）。

---

## 14. 验收标准（AC）

| ID | 验收项 | 判定命令/动作 |
| --- | --- | --- |
| AC1 | **零代码改动** | `git status --porcelain` 中不出现 `backend/`、`frontend/`、`deploy/`、`scripts/` 的改动 |
| AC2 | 在线快照自洽 | `snapshot.sh create && snapshot.sh verify LATEST` → 退出码 0 |
| AC3 | 校验能抓错 | 截断 DB 尾部后 `verify` **必须失败**（负向测试）。本次已实测基线：截断 8 KB → `DatabaseError: database disk image is malformed` |
| AC4 | 事件触发有效 | 手工 PATCH 一个财年状态，watch 在 60 s 内产出 `fy-*-post` |
| AC5 | 幂等 | 同一事件重复触发只产出一份（事件状态文件生效） |
| AC6 | 阻塞可控 | ① 在本机 4.77 MB 库上复跑 `backup()` 语义与 `pages`/`sleep` 取值探针（§5.1）；② 在**满负载**（20 浏览器标签 + 一次财年推进）下采集，`blocked_ms_est` ≤ 800 ms，且 `/api/health` 无明显抖动 |
| AC7 | 失败不静默 | 拔掉 U 盘/写只读目录 → `LAST_FAILURE` 出现 + `snapshot.log` 有行 + 退出码非 0 |
| AC8 | 恢复可用 | 演练：从 `LATEST-GOOD-FY` 恢复到影子实例，`/api/health` = ok 且数据核对一致，≤ 10 分钟 |
| AC9 | 异地副本 | `status` 显示异地延迟 ≤ 1 份 |
| AC10 | 保留策略可审 | `prune --dry-run` 输出不包含任何 `fy-*-pre/post`、`baseline-*`、`daily-close` |

---

## 15. 风险与权衡

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 切片备份在 Python 版本间语义差异 | 采集可能死循环或不足量 | AC6 前先在本机实测；实现里加"页数不推进即中止"的保护 |
| `VACUUM INTO` 在无人在线时的长阻塞 | 若误在高峰用 `--fast` → 卡全场 | `--fast` 仅用于人工场景，并在输出里提示「仅限无人在线」；默认不用 |
| 日志锚点被改级别/被静默 | 事件触发失效 | 定时兜底 + 启动回放 + `verify` 事后核对 `fiscal_state` 是否与预期一致 |
| 记账机流程需要人做 | 账本快照可能漏 | 卡片 + 18:30 检查点 + `ledger_unaligned` 标记 |
| 磁盘满 | 采集失败=没有恢复点 | `status` 显示余量；< 20% 告警；L0 环形保留自动回收 |
| 快照系统本身成为依赖 | 它挂了会让人误以为没备份 | `LAST_FAILURE` + 纸质检查点；同时保留 `docs/MIGRATION.md` 的手工路径作为最终兜底（但**改成 `.backup` 语义**） |
| 恢复流程被误用于"修小问题" | 越恢复越糟 | 决策树（§9.1）+ 影子核对（§9.3）+ 恢复必须两人确认 |

### 被否决的备选方案

| 方案 | 否决理由 |
| --- | --- |
| `cp -a` 定时拷库 | 就是 O-05 批评的反模式；回滚日志模式下得不到自洽副本 |
| 改成 WAL + 归档 WAL 文件 | 需要改配置并重启（C2），且单线程下 WAL 的收益有限；与「不改代码」冲突 |
| 在 Django 里加 `post_save` 钩子/management command/API 端点做快照 | 违反 C1；且把备份逻辑放进那个**唯一线程**里，正好违反 C3 |
| 用 `django-dbbackup` 之类的库 | 需要安装依赖 + 可能改 settings；引入新依赖 = 新风险 |
| 云对象存储 | 现场无外网（C6） |
| 容器卷快照 / LVM 快照 | 部署形态是裸机 + systemd（`deploy/gipfel.service`），现场没有 LVM 与容器；改造成本远超收益 |
| 只靠 `contract_watcher` 的 Excel 当备份 | 只覆盖部分公司（账号范围），且依赖外部 Windows 程序持续运行 —— 不能作为灾备 |

---

## 附录 A：待实施的脚本草案（本文档**不落盘**为可执行文件）

> 以下仅为设计示意，落地时放在 `/opt/gipfel-snapshot/`，**不进仓库**。

**`snapshot.sh` 子命令与退出码**

```
用法: snapshot.sh <command> [args]
  create  [--tag <label>] [--level L0|L1|L2|L3] [--fast] [--force]
  verify  <sid|LATEST|LATEST-GOOD-FY>
  list    [--limit N]
  status
  prune   [--dry-run|--yes] [--keep-l0 N]
  sync    [--target /media/<label>|peer:/var/backups/gipfel/]
  restore <sid> [--dry-run]        # 只打印 §9.2 的步骤，不执行（执行由人做）
退出码: 0 成功 / 10 校验失败 / 11 采集被熔断 / 12 资源(磁盘/介质)不足 / 13 参数错 / 14 锁被占用
```

**关键约束（写进脚本头部注释）**

```
- set -euo pipefail；所有外部命令显式检查返回值，禁止 || true 吞错
- flock /var/backups/gipfel/.lock（非阻塞；拿不到直接退出 14）
- 源库一律 file:<path>?mode=ro；**永不**对 /opt/gipfel 下任何路径写入
- 目标路径白名单校验：必须是 /var/backups/gipfel 的子路径
- 落盘：.tmp-<sid>/ → sha256 → fsync(file+dir) → rename → 更新 LATEST
- 每步耗时与 blocked_ms 写入 manifest.perf 与 snapshot.log
```

**`gipfel-snapshot-watch.service` 草案**

```ini
[Unit]
Description=Gipfel fiscal-year snapshot watcher
After=network.target

[Service]
Type=simple
User=root
ExecStart=/opt/gipfel-snapshot/snapshot-watch.sh
Restart=always
RestartSec=3s
# 与 deploy/gipfel.service 同款加固思路：对应用目录只读
ProtectSystem=strict
ReadOnlyPaths=/opt/gipfel
ReadWritePaths=/var/backups/gipfel
PrivateTmp=true
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=multi-user.target
```

**现场卡片（`snapshot-card.md` 的要点）**

```
【每个财年开始前，看这一行】
  sudo /opt/gipfel-snapshot/snapshot.sh status   →  结论：可用 ✅

【三条命令】
  1. 立刻快照：sudo snapshot.sh create --tag manual
  2. 看状态：  sudo snapshot.sh status
  3. 校验：    sudo snapshot.sh verify LATEST-GOOD-FY

【恢复（要两个人确认）】
  见 /opt/gipfel-snapshot/restore-card.txt
  ⚠ 第一步永远是：snapshot.sh create --tag forensics-broken
  ⚠ 覆盖前必须：rm -f /opt/gipfel/backend/db.sqlite3-journal /  -wal / -shm
  ⚠ .env 默认不要覆盖（会全员登出）
```

---

## 附录 B：manifest 与 status 的关系

`status` 不接受任何猜测：它只读 `LATEST`、`LATEST-GOOD-FY`、各 `<sid>/manifest.json`、`LAST_FAILURE` 与 `snapshot.log`。**因此 manifest 是所有判断的唯一权威**；任何"我觉得刚才跑过"都不作数。这条设计直接对应 O-05 报告的教训：*文档说让你建 `backup.sh`，但脚本不存在* —— 现场不能靠"应该做过"。

---

## 附录 C：命令速查（打印版）

```bash
sudo /opt/gipfel-snapshot/snapshot.sh create --tag manual     # 立即快照
sudo /opt/gipfel-snapshot/snapshot.sh status                  # 我有没有可用恢复点
sudo /opt/gipfel-snapshot/snapshot.sh list --limit 20         # 都有哪些
sudo /opt/gipfel-snapshot/snapshot.sh verify LATEST           # 能不能用
sudo /opt/gipfel-snapshot/snapshot.sh sync                    # 拷到 U 盘/第二台机
sudo /opt/gipfel-snapshot/snapshot.sh restore <sid>           # 只看恢复步骤，不动手
sudo journalctl -u gipfel-snapshot-watch -n 50                # 事件侦听在不在干活
systemctl list-timers | grep snapshot                         # 定时器状态
```
