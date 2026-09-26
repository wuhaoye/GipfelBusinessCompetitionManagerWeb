# 快照与回退系统（apps.snapshots）

> 一句话：**把整库数据原样拍成快照，需要时可以强制暂停全场、把数据整体回退到那一刻，并让所有在线客户端自动同步到回退后的数据。**

- 后端应用：`backend/apps/snapshots/`
- 前端页面：`快照与回退`（侧栏「系统」区，超管专属）→ `frontend/src/views/system/SnapshotManageView.vue`
- 权限域：`snapshot:view` / `snapshot:manage` / `snapshot:restore`（三者均为超管专属）
- 归档目录：`backend/snapshots/`（可用 `SNAPSHOT_DIR` 改）

---

## 1. 能力总览

| 能力 | 说明 |
| --- | --- |
| **记录所有数据** | 遍历 `apps.*` 下**全部 41 张业务表**，逐表流式写入 `tables/<表名>.jsonl.gz`；每份快照附 `manifest.json`（逐表行数 + sha256 + 回退策略）。可选一并归档 `uploads/` 上传文件。 |
| **及时回退** | 事务内「删 → 写回 → 校验」整表还原；保留主键与 `created_at/updated_at`；回退后逐表重算 sha256 与快照比对，不一致立即整体回滚（要么完全回退，要么数据一动不动）。 |
| **强制暂停** | 一键把全局门禁切到 `PAUSED`：服务端拒绝所有业务写入（HTTP 423），并通过 Socket.IO 广播让**所有在线客户端立刻弹出全屏遮罩**、停止操作。 |
| **保证同步性** | 回退在 `RESTORING` 模式下进行（读写全冻结 + 排空在途写请求）；完成后 `data_version` +1 并广播，所有客户端比对版本号后**清空本地缓存并整体重载**，全场看到同一代数据。 |

---

## 2. 数据模型

| 表 | 作用 |
| --- | --- |
| `snapshots` | 快照元数据：名称/备注/类型/状态/范围/统计（行数、字节数）/创建人/耗时/锁定/回退次数 |
| `snapshot_tables` | 每份快照内的逐表清单：模型、表名、回写策略、行数、字节数、sha256 |
| `system_gate` | **单例**：全局门禁（`RUNNING` / `PAUSED` / `RESTORING`）+ `data_version` |
| `snapshot_policy` | **单例**：自动快照开关/间隔/范围、保留份数与天数 |

> 快照系统自身的这 4 张表**不参与快照与回退**（否则回退会把「回退记录」自己抹掉）。

---

## 3. 快照的文件结构

```
backend/snapshots/
└── snap-000012/                     # 快照 #12
    ├── manifest.json                # 清单：schemaVersion / 逐表 sha256 / 统计 / 归档目录…
    ├── tables/
    │   ├── materials.jsonl.gz       # 一行一条记录（键为数据库列名，主键序）
    │   ├── companies.jsonl.gz
    │   └── …（共 41 张表）
    └── files/                       # includeFiles=true 时才有：uploads/ 的镜像
        └── map-background/…
```

- **行编码**：按模型 `concrete_fields` 的列名取值；`Decimal` 存字符串、时间存 ISO、`bytes` 存 base64。
  解码时按字段类型逆变换，因此可以原样写回（包括超过 15 位有效数字的十进制大数）。
- **校验和**：`sha256(按主键排序的规范化 JSON 行 + \n)`。快照时算一次、回退后重算比对。
- **原子落盘**：`manifest.json` 先写 `.tmp` 再 `os.replace`，避免半截文件被当作可用快照。

---

## 4. 回退作用域与回写策略

| 策略 | 含义 | 典型表 |
| --- | --- | --- |
| `full` | 先按作用域删除、再整表写回 | 原料、零件、产品、地图、合同、股票、订单、消息… |
| `upsert` | 只按主键回写（存在则更新、不存在则新建），**绝不删除** | `Competition`（删比赛会级联删账号）、`User`（见下） |
| `record` | 只记录、不回写 | `AuditLog`（追加型取证日志）、比赛维度快照下的全局表 |

**账号默认不回写（`User = record`）**，原因有二：

1. 回退 `token_version` 会让**已吊销的旧令牌复活**（安全风险）；
2. 删除账号会连带影响 `message_recipients` 等表。

需要回写账号时显式传 `includeUsers=true`，此时策略降级为 `upsert`：只更新、不删除，且
调用方明确知晓上述取舍。

**作用域**

- `scope=competition`：只纳入该比赛的数据；没有 `competition` 外键的子表（零件-原料配比、
  产品-零件配比、公司字段值、消息收件人…）通过父表递归收敛；全局表（产业类型/字段、合同类型、
  公告、控件包）只记录不回写，避免回退一个比赛时误伤其它比赛。
- `scope=system`：全库所有行。此时全局表也参与回写（除非显式关闭 `restoreGlobal`）。

**依赖顺序**：注册表按外键依赖做拓扑排序（`registry.py`），回退时正序插入、倒序删除，
因此在开启外键约束的情况下也能整表还原（无需关约束）。

---

## 5. 强制暂停（门禁）工作原理

```
管理员点击「强制暂停」
        │
        ├─① SystemGate.mode = PAUSED（落库，跨进程可见）+ 广播 system:paused
        │
        ├─② 等待在途写请求归零（gate.drain，默认最多 10 秒）
        │     中间件对每个放行的写请求 +1/-1；模式已翻转 → 新写请求一律 423
        │
        └─③ 排空成功才继续（失败则自动恢复 RUNNING 并报错，数据不变）
```

- **服务端**：`SnapshotGateMiddleware` 对非白名单路径拦截——
  `PAUSED` 拒绝写请求（423），`RESTORING` 连读也拒绝；白名单为
  静态资源 / Socket.IO / `/api/health` / `/api/version` / `/api/auth/*` / `/api/snapshots/*`。
- **客户端**：`system:paused` 后铺全屏遮罩（`SystemGateOverlay.vue`），
  同时 `api/request.ts` 在请求拦截器里先一步拒绝写请求，避免产生无意义的网络与错误提示。
- **新上线/断线重连的客户端**：Socket.IO 握手时服务端主动下发 `system:state`，
  因此**不会有人漏掉暂停**；HTTP 层也仍然拦得住（423 兜底）。
- **没有实时通道的客户端**（未选比赛 / 断网）：`App.vue` 每 20 秒轮询一次门禁状态
  （仅在 socket 未连接时），并在页面重新可见（`visibilitychange`）时立刻复核。

### 门禁状态机

```
        pause/restore 开始                    操作完成 / 手动恢复 / TTL 到期
RUNNING ─────────────────► PAUSED / RESTORING ──────────────────────────► RUNNING
   ▲                                                    （data_version 仅回退成功时 +1）
   └──────────────── 排空失败自动回退 ──────────────────┘
```

- `PAUSED`：写冻结、读放行（用于人工修数、核对账目）。
- `RESTORING`：读写全冻结（库内容正在被整表替换）。
- `SNAPSHOT_PAUSE_TTL_SECONDS` / `SNAPSHOT_RESTORE_TTL_SECONDS` 为兜底 TTL：
  进程异常退出时不至于永久停在暂停态；**独占操作进行中不会触发 TTL 自动恢复**。

---

## 6. 回退流程（一次完整时序）

```
POST /api/snapshots/{id}/restore  { confirmText, reason, includeUsers, ... }
  │
  ├─ 校验：快照状态可用 + 归档 sha256 全部通过 + 无其它独占操作 + 当前非暂停态
  │
  ├─ gate → RESTORING，广播 system:restoring（全场弹出「回退中」遮罩）
  ├─ 排空在途写请求（静止点）
  ├─ 默认先建「回退前安全快照」（kind=pre-restore，可再回退回来）
  │
  ├─ 事务开始
  │   ① 按倒序删除作用域内数据（full 表）
  │   ② 按正序写回（bulk_create + 回写 auto_now 字段原值）
  │   ③ upsert 表按主键回写（不删除）
  │   ④ 重置自增序列（避免回退后新行与历史主键冲突）
  │   ⑤ 逐表重算 sha256 比对 → 不一致就抛错整体回滚
  │   ⑥ SystemGate.data_version += 1
  │  事务提交
  │
  ├─ 广播 system:restored { snapshotId, deletedRows, insertedRows, dataVersion, safetySnapshotId }
  ├─ gate → RUNNING，广播 system:resumed { dataVersion }
  │
  └─ 客户端：发现 dataVersion 变化 → 清空 IndexedDB 缓存 + 内存 memo → 整体重载页面
```

**为什么客户端要整体重载而不是增量刷新**：回退是「整库级」变更，主键复用、行可能被删除又被
还原，逐资源增量同步无法表达这种变化；直接作废本地缓存并重载是与服务端保持一致最简单、
也最可靠的做法。

---

## 7. 接口一览

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/snapshots` | `snapshot:view` | 列表（分页，可按范围/类型/状态/关键字过滤） |
| POST | `/api/snapshots` | `snapshot:manage` | 创建快照（`scope`、`includeFiles`、`pauseFirst`） |
| GET | `/api/snapshots/status` | `snapshot:view` | 概览（门禁 + 策略 + 数量 + 占用 + 在途写请求数） |
| GET | `/api/snapshots/gate` | **仅需登录** | 当前门禁状态（暂停遮罩/版本同步用）。刻意不要求超管权限，否则普通选手永远看不到暂停遮罩；返回体不含业务数据 |
| POST | `/api/snapshots/gate/pause` | `snapshot:restore` | **强制暂停全体**（`reason` / `message` / `ttlSeconds`） |
| POST | `/api/snapshots/gate/resume` | `snapshot:restore` | 恢复运行 |
| GET/PUT | `/api/snapshots/policy` | `snapshot:view` / `snapshot:manage` | 自动快照与保留策略 |
| POST | `/api/snapshots/cleanup` | `snapshot:manage` | 按保留策略清理（`dryRun` 预览） |
| GET | `/api/snapshots/{id}` | `snapshot:view` | 详情（逐表清单 + manifest） |
| DELETE | `/api/snapshots/{id}?force=` | `snapshot:manage` | 删除（锁定快照需 `force=true`） |
| GET | `/api/snapshots/{id}/diff` | `snapshot:view` | 回退预览（将删除/写回多少行） |
| GET | `/api/snapshots/{id}/verify` | `snapshot:view` | 归档完整性校验 |
| GET | `/api/snapshots/{id}/download` | `snapshot:view` | 下载归档（tar.gz） |
| POST | `/api/snapshots/{id}/restore` | `snapshot:restore` | **强制暂停 + 回退**（需 `confirmText`） |
| POST | `/api/snapshots/{id}/lock` | `snapshot:manage` | 锁定/解锁（锁定的快照不会被保留策略清理） |

响应仍是项目统一的 `{code, message, data}` 信封。`423 Locked` 表示系统处于暂停/回退中，
响应体含 `errorCode`（`system_paused` / `system_restoring`）与完整门禁状态 `gate`。

---

## 8. 实时事件契约

| 事件 | 触发时机 | 负载（关键字段） |
| --- | --- | --- |
| `system:state` | 门禁状态变化、Socket 握手 | `mode, reason, message, since, expiresAt, activeSnapshotId, progress, dataVersion, operatorName` |
| `system:paused` | 进入 `PAUSED` | 同上 |
| `system:restoring` | 进入 `RESTORING` | 同上（`activeSnapshotId` = 目标快照） |
| `system:progress` | 快照/回退进度 | `progress` |
| `system:restored` | 回退事务提交成功 | `snapshotId, label, deletedRows, insertedRows, durationMs, dataVersion, safetySnapshotId` |
| `system:resumed` | 恢复 `RUNNING` | `mode, dataVersion, reason` |

这些事件进入 `apps/realtime/emit.py` 的环形缓冲（全局房间），因此**断线重连后
`sync:replay` 仍能补到关键事件**。前端在 `realtime/socket.ts` 中于 socket 创建时立即注册
（与 `auth:required` 同理），避免初始化窗口期丢事件。

---

## 9. 运维手册

### 9.1 日常：手动快照 / 回退

浏览器 → **快照与回退** 页面：

1. 「创建快照」填名称/备注、选范围（当前比赛 / 全系统），必要时勾选「上传文件」与
   「先强制暂停再快照」；
2. 回退时点某行的「回退」→ 看影响预览 → 输入快照编号确认 → 执行。

### 9.2 定时自动快照

```bash
cd /opt/gipfel/backend
# 按策略（SnapshotPolicy）判断是否到期，到期则创建 + 清理过期
.venv/bin/python manage.py snapshot_auto

# 忽略间隔立即创建一份（常用：财年推进前）
.venv/bin/python manage.py snapshot_auto --force --scope system --label "第3财年基线"

# 只按保留策略清理
.venv/bin/python manage.py snapshot_auto --cleanup-only
```

crontab 示例（每 10 分钟判断一次是否到期）：

```cron
*/10 * * * * cd /opt/gipfel/backend && .venv/bin/python manage.py snapshot_auto >> /var/log/gipfel/snapshot_auto.log 2>&1
```

> `snapshot_auto --force` 默认会短暂进入 `PAUSED`（拿到静止点），可用 `--no-pause` 改为
> 不打断用户的弱一致快照。

### 9.3 应急：Web 不可用时的命令行回退

```bash
cd /opt/gipfel/backend
.venv/bin/python manage.py snapshot_restore --list          # 列出可用快照
.venv/bin/python manage.py snapshot_restore --id 12         # 预览影响（不写库）
.venv/bin/python manage.py snapshot_restore --id 12 --yes   # 真实执行
```

命令行回退与页面回退行为完全一致：会暂停全场、默认先建安全快照、回退后校验，
并在结束时广播 `system:restored` / `system:resumed` 让前端自动重载。

### 9.4 备份与保留

- 快照归档本身就是「可回退的备份」，**但仍不能替代异地备份**：它与库在同一台机器上。
- 建议：`backend/snapshots/` 一并纳入服务器备份；用「保留份数/天数」策略控制磁盘占用。
- 升级脚本已把 `snapshots/` 加入 `rsync --exclude`（`deploy-linux.sh` /
  `update-from-github.sh`），升级**不会**清掉归档；`deploy/gipfel.service` 的
  `ReadWritePaths` 也已包含该目录（否则 systemd 沙箱下写入会 PermissionError）。

### 9.5 关键环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SNAPSHOT_DIR` | `backend/snapshots` | 归档目录 |
| `SNAPSHOT_DRAIN_TIMEOUT` | `10` | 强制暂停时等待在途写请求的上限（秒） |
| `SNAPSHOT_PAUSE_TTL_SECONDS` | `0` | 手动暂停的默认 TTL（0 = 需手动恢复） |
| `SNAPSHOT_RESTORE_TTL_SECONDS` | `900` | 「回退中」兜底 TTL |
| `SNAPSHOT_GATE_CACHE_SECONDS` | `1` | 门禁状态进程内缓存秒数 |
| `SNAPSHOT_MAX_FILE_BYTES` | `2GB` | 上传文件归档总量上限 |
| `SNAPSHOT_DOWNLOAD_MAX_BYTES` | `256MB` | 打包下载上限 |

---

## 10. 边界与取舍（务必知悉）

1. **账号默认不回写**（见 §4）；`includeUsers=true` 也是「只更新不删除」。
2. **审计日志永不回写**：回退是「恢复数据」，不该抹掉「谁在什么时候做了什么」的取证记录；
   回退/快照操作本身也会追加审计。
3. **比赛与账号行不会被删除**：避免级联删除历史账号。若快照之后新建了比赛/账号，
   回退后它们仍然存在（数据表内容回退，主体行保留）。
4. **回退 ≠ 恢复代码**：只回退数据。若缺陷来自代码，请先按 `deploy/README.md` 回滚版本。
5. **多进程部署（C1-a 后为生产默认，口径已修正）**：门禁状态落库、跨进程可见，但**每个进程各有一份
   1 秒的进程内缓存**（`SNAPSHOT_GATE_CACHE_SECONDS`，默认 1s），且**「在途写请求」计数器是进程内的**
   （`gate.active_writers()` / `drain()` 只见本进程）。因此 C1-a（REST 拆到 `gipfel-wsgi` 多 worker）后：
   - 翻转模式后，其它 worker 最多再放行 ~1s 的写请求（缓存未过期）；
   - `drain()` 只能等到**本 worker** 的在途写结束，别的 worker 上正在执行的写会照常跑完。
   结果是**「强制暂停」不再是全场严格静止点**，残余窗口 ≈ 缓存 1s + 单个在途写请求的耗时。
   事务本身仍是原子的，暂停后不会出现「半截写入」；但若现场要求绝对静止（例如回退前的取证），
   - 可把 `SNAPSHOT_GATE_CACHE_SECONDS=0`（每次请求都查库，跨进程即时可见，代价是每请求一次查询），或
   - 先让全场停止操作（主持人口令）再执行暂停/回退。
   回退期间**不要**重启服务（见第 6 条）；重启两个进程用 `systemctl restart gipfel gipfel-wsgi`。
6. **回退期间不要重启服务**：事务未提交会被回滚，数据仍是回退前状态（安全），
   但门禁会因 TTL 到期自动恢复，需要管理员确认后再操作。
7. `SQLite` 下大库回退耗时随行数线性增长；实测本仓库开发库（约 7.5k 行、41 张表）
   完整回退约 **0.15~1.7 秒**。

---

## 11. 相关测试

| 位置 | 内容 |
| --- | --- |
| `backend/apps/snapshots/tests.py` | 26 条单测：注册表完备性、全系统/比赛维度往返、被删行还原、新增行清除、归档篡改中止、门禁 423、dataVersion 递增、保留策略、API 契约 |
| `tests/snapshot_tools/roundtrip_real_db.py` | 真实库端到端：快照 → 破坏 → 回退 → 逐表 sha256 比对 |
| `tests/snapshot_tools/live_gate_e2e.mjs` | 真实 ASGI + Socket.IO 联调：握手状态、暂停 423、实时事件、回退后 dataVersion 同步（17 项断言） |
| `tests/snapshot_tools/inspect_registry.py` | 打印注册表（作用域/策略/依赖顺序），新增模型后自检用 |

```powershell
cd backend
.\.venv\Scripts\python.exe manage.py test apps.snapshots -v 2
.\.venv\Scripts\python.exe ..\tests\snapshot_tools\roundtrip_real_db.py
```
