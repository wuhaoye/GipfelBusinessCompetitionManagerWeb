# 现场运营缺陷解释：O-02 / O-04 / O-05 / O-09 / O-10 / O-11 / O-12 / O-13

> 来源：[`OPS_READINESS_REPORT.md`](OPS_READINESS_REPORT.md) §4 缺陷清单（该报告共 28 条，本文只解释其中 8 条）。
> 写法：每条给出 **① 缺陷说的是什么 ② 代码里到底是什么机制（带引用行） ③ 现场怎么触发 ④ 后果 ⑤ 报告给的建议 ⑥ 我复核后的补充/校正**。
> 复核方式：只读代码，未起服务、未写库。凡本文新加的判断都标 **【复核补充】**，与报告原文区分开。
> 场景前提（来自原报告）：中海国际中心七楼，~50–100 人，学生自带笔记本 + 共享 Wi-Fi，两天六个财年，由学生主席团自己运营。

---

## 0. 速查表

| ID | 级别 | 一句话 | 什么时候会咬人 | 能不能靠流程绕过 |
| --- | --- | --- | --- | --- |
| **O-02** | P0 | 「推进财年」不是原子操作：两个独立端点、无服务端 ACTIVE 唯一性校验；定时器在请求线程里同步跑，抢不到锁就**静默跳过**，单公司失败静默吞掉 | 每次换财年（一天 6 次） | 部分可以：写成 SOP 卡 + 一人专责 + 看日志；但「静默半成功」无法靠流程消除 |
| **O-04** | P0 | 单进程 daphne + 全部同步视图**共用一个线程** + SQLite 无 WAL/无 busy timeout + 每写一条业务多插一条审计 + 无反代限流 | 财年边界、断线重连风暴、签到高峰 | 只能缓解（错峰、换库、加限流），不能靠话术绕 |
| **O-05** | P0 | 没有任何自动备份，赛前也无备份动作；文档让人建的 `scripts/backup.sh` **在仓库里不存在**，而且文档示例用的是**热拷贝活库** | 服务器/场地断电的那一刻 | 可以：赛前手写脚本 + 每财年前快照 + 拷到第二台机器/U 盘 |
| **O-09** | P1 | 客户端没有离线/降级能力（无 Service Worker / manifest / `navigator.onLine` / 写队列），断网就是所有写操作直接失败 | 共享 Wi-Fi 抖动、会场 AP 掉线 | 不能绕，只能提前告诉学生「断网就等，别反复点」+ 服务端侧减少重连风暴 |
| **O-10** | P1 | 账号界面只有 3 个角色、没有权限勾选框、权限由 `derivePermissions` 硬编码派生；**在界面里「编辑」一次账号会静默清掉手工用 API 授的扩展权限** | 赛前建账号、赛中改账号之后 | 可以绕：全部用 API 建账号，并在账号表上写明「禁止在界面编辑」 |
| **O-11** | P1 | 版本硬封锁：客户端版本 ≠ `/api/version` 就**拒绝发出任何业务请求**；后端一升级/热修，全场被锁，只能硬刷新 | 赛中有人「顺手修个小 bug」并重启服务 | 可以：立规矩「开赛后不升级、不重启」；并把硬刷新按钮教给学生 |
| **O-12** | P1 | 100 人签到集中在同一线程上做 bcrypt cost=12 登录 + 顶号写库 + **强制改密再哈希一次**；且同一账号只能一台设备在线 | 7:30–8:30 签到窗口 | 部分可以：赛前分批改密、共享账号拆开；但单线程串行是结构性的 |
| **O-13** | P1 | 现金/库存/配额/碳排等字段值的变动**不进审计**（模型被显式标记为不追踪），审计日志又只增不删、只有超管能看、无按公司/时间段查询 | 有人怀疑「数据造假」、要当场举证时 | 不能绕：只能靠 `contract_watcher` 的可读记录 + 纸质合同比对 |

**P0 三条（O-02/O-04/O-05）的共同点**：它们不是「功能缺失」，而是「**没有任何一条路径被设计过、也没有被演练过**」——原报告 §7 的总结即此意。

---

## 1. O-02（P0）·「推进财年」不是原子操作，且会静默半成功

### 1.1 缺陷说的是什么

系统里**根本不存在「推进财年」这个动作**。它被拆成两个互不相关的手工 API 调用：

1. `PATCH /api/competitions/fiscal-years/:id` 把当前财年 `status` 改成 `CLOSED`（界面上的「结束财年」）；
2. `POST /api/competitions/:id/fiscal-years` 新建下一年的财年，`status` 默认 `ACTIVE`（界面上的「开始新财年」）。

两步之间**没有事务、没有锁、没有状态机、没有幂等保护**；而且每一步都会在**HTTP 请求线程内同步执行**一个可能很慢的「财年定时器」，定时器**抢不到锁就静默返回**。

### 1.2 代码里到底是什么机制（证据链）

**（a）后端不校验「同比赛只能有一个 ACTIVE 财年」**

[`backend/apps/competitions/views.py:206-229`](../../../backend/apps/competitions/views.py)

```python
@require_permissions("competition:manage")
def post(self, request, cid):
    ...
    if FiscalYear.objects.filter(competition=comp, year=year).exists():
        raise BusinessError("该财年已存在", code=409, status_code=409)
    fy = FiscalYear.objects.create(
        competition=comp, year=year,
        status=serializer.validated_data.get("status", "ACTIVE"),   # ← 默认直接 ACTIVE
        ...
    )
    _broadcast_fiscal_year(fy)
    _apply_fiscal_year_timer(comp.id, "FY_START")                   # ← 在请求线程内同步跑
    return Response(FiscalYearSerializer(fy).data)
```

唯一的唯一性约束是 `(competition, year)`（`FiscalYear.unique_together`），**没有**「同比赛只能一个 ACTIVE」的数据库约束，视图里也没有这个检查。

**（b）前端唯一的守护取自本地缓存**

[`frontend/src/views/competitions/CompetitionListView.vue:130-138`](../../../frontend/src/views/competitions/CompetitionListView.vue) 只在按钮上写 `:disabled="hasActiveFiscalYear"`，而 `hasActiveFiscalYear` 来自**本地已加载的财年列表**（`fiscalYears.value.some(f => f.status === "ACTIVE")`）。
→ 两个超管标签页 / 两台设备同时点「开始新财年」，就会有**两个 ACTIVE 财年**；前端只显示年份较大的那个，旧的那个永远躺在列表里显示「进行中」。

**（c）结束财年确实防了重复点击**

[`backend/apps/competitions/views.py:257-261`](../../../backend/apps/competitions/views.py)

```python
if prev_status != "ACTIVE" and new_status == "ACTIVE":
    _apply_fiscal_year_timer(fy.competition_id, "FY_START")
elif prev_status != "CLOSED" and new_status == "CLOSED":
    _apply_fiscal_year_timer(fy.competition_id, "FY_END")
```

`prev_status != "CLOSED"` 判定使第二次点「结束财年」不会再触发定时器——**这一条是安全的**（报告原话）。但反过来说：**CLOSED 改回 ACTIVE 会再触发一次 FY_START，把全场字段再重置一遍**（见 O-17）。

**（d）定时器在请求线程内同步执行，且抢不到锁就静默返回**

[`backend/apps/company_fields/timer.py:147-167`](../../../backend/apps/company_fields/timer.py)

```python
def apply_fiscal_year_timer(competition_id: int, trigger: str) -> None:
    # 并发守卫：同一比赛同一触发时机仅允许一个定时器在跑（非阻塞，冲突直接返回）
    with _fiscal_locks_guard:
        lock = _fiscal_locks.get(competition_id)
        if lock is None:
            lock = threading.Lock()
            _fiscal_locks[competition_id] = lock
    if not lock.acquire(blocking=False):
        logger.warning(
            "财年定时器：比赛 #%s 的 %s 已在执行中，跳过本次并发触发", competition_id, trigger
        )
        return                      # ← 静默跳过：不报错、不回滚、不影响 200
    try:
        _run_fiscal_year_timer(competition_id, trigger)
    finally:
        lock.release()
```

关键点：**锁是「按比赛」而不是「按触发时机」的一把锁**（`_fiscal_locks[competition_id]`）。所以 FY_END 和 FY_START 共用同一把锁 —— 只要上一个财年的 **结束**定时器还在跑，新财年的 **开始**定时器就**永远不会执行**（不是排队，是直接丢弃）。日志里只有一行 `warning`。

**（e）单公司失败也被吞掉，且没有「全场一个事务」**

[`backend/apps/company_fields/timer.py:189-196`](../../../backend/apps/company_fields/timer.py)

```python
for c in companies:
    try:
        _apply_timer_to_company(c, fields)
        affected_companies.append(c.id)
    except Exception as e:  # noqa: BLE001
        logger.warning("财年定时器：公司 #%s 处理失败：%s", c.id, getattr(e, "message", e))
```

【复核补充】事务边界在**公司**一级，不在**全场**一级 —— [`timer.py:239-243`](../../../backend/apps/company_fields/timer.py) 里 `with transaction.atomic():` 包的是**一家公司**的待写字段。所以「A 公司写成功、B 公司写失败」是允许的中间态，且只有 `backend/logs/gipfel.log` 能看出是哪一家。

**（f）额外限定：只有配置了定时器字段才会真的慢**

【复核补充】[`timer.py:171-175`](../../../backend/apps/company_fields/timer.py) 开头就是：

```python
timer_fields = list(IndustryField.objects.filter(timer_enabled=True, timer_trigger=trigger))
if not timer_fields:
    return
```

原报告 §5 的只读探针实测 **`timer_enabled=1` 的产业字段 = 0** → 在**当前这个库**里，点「开始/结束财年」实际是**瞬时返回**的，不会卡住全站。**O-02 的阻塞后果是「赛前把定时器字段配好之后才会出现」的条件性后果**，原报告 §5 E-5 也明确说「财年定时器的实际耗时未实测，是结构性推断」。这一点很重要：**不能因为"我试了一下很快"就认为没事**——那只是因为还没配。

*附带风险*：`threading.Lock` 是**进程内**锁。当前是单进程 daphne 所以有效；一旦哪天起多 worker，这把锁就完全不成立（结论方向不变，但前提要注意）。

### 1.3 现场怎么触发

- **正常路径**：16:40 第二财年末，主席团点「结束财年」→ 全场 100 台设备的 `/auth/me` 心跳（20s 一次）和页面请求全部排在同一个线程后面 → 学生端 15s axios 超时同时弹「请求超时」；若定时器跑 >120s，nginx `proxy_read_timeout 120s` 直接 504，**但财年已经在库里改成 CLOSED** —— 界面说失败，数据说成功。
- **最恶劣路径**：点「结束财年」后**立刻**点「开始新财年」。若上一个 FY_END 定时器还在跑（前端 15s 就放弃了，服务端还在继续），新财年的 FY_START 定时器**静默返回、永不执行** → 所有公司「财年初」重置值一个都没写入，整个财年从错误初值开始，而日志里只有一行 warning。
- **并发路径**：两个超管标签页同时点「开始新财年」→ 两个 ACTIVE 财年。
- **误操作路径**：把已 CLOSED 的财年改回 ACTIVE → 再触发一次 FY_START，全场字段被再重置一遍。

### 1.4 后果

时间是这场比赛唯一的硬约束（`v2.txt` P5：第三财年 8:30–10:15、第四财年 10:20–12:10…），而系统把「换财年」做成了**两个需要人手确认、可能静默半成功、且会冻结全站**的操作。最糟的不是慢，是**「界面说成功、数据说没做」且没人发现**。

### 1.5 报告给的建议（不改代码）

写一张「**财年推进 SOP 卡**」：固定一个人、固定一段串行操作、每次结束财年后**等界面出现「未开启财年」再点开始**、每次点完去 `设置→比赛准备总览` 看字段是否变化；并把 `backend/logs/gipfel.log` 打开在第二块屏上盯三类日志（`财年定时器` / `审计写入失败` / `database is locked`）。

### 1.6 复核补充

- 用流程绕不掉的部分是**「静默跳过」**：它返回 200，前端只能显示成功。**唯一可靠的确认手段是看日志**（搜索词：`已在执行中，跳过本次并发触发`）。
- 「等界面出现未开启财年」这个 SOP 并不是充分条件 —— 界面状态来自前端缓存的列表，不反映服务端定时器是否还在跑。真正的确认点是**日志有没有出现 FY_START 的执行记录 / 字段值有没有变**。

---

## 2. O-04（P0）·单线程 + SQLite + 无审计节流 + 无限流 → 大面积超时

这是原报告里**最容易被低估、现场后果最直接**的一条。

### 2.1 缺陷说的是什么

后端是**单进程 daphne**，**所有同步视图在 ASGI 下共用一个线程**，数据库是未调优的 SQLite（无 WAL、无 busy timeout），每一次写库还会**多插一行审计**（同一事务、同一线程），且从前端到 nginx 到后端**没有任何限流**。→ 财年边界与断线重连风暴下，100 台设备一起把唯一的线程堵死。

### 2.2 代码里到底是什么机制（证据链）

| 事实 | 证据 |
| --- | --- |
| 单进程 daphne，systemd 单元里没有任何 worker/进程数参数 | [`deploy/gipfel.service:26-31`](../../../deploy/gipfel.service)：`ExecStart=… daphne -b 127.0.0.1 -p 8000 --access-log … backend.asgi:application` |
| **全部 API 视图都是同步视图**，Django 在 ASGI 下用 `sync_to_async(..., thread_sensitive=True)` 包裹 → **所有同步视图共用一个线程** | `backend/.venv/Lib/site-packages/django/core/handlers/base.py:129`（`return sync_to_async(method, thread_sensitive=True)`）与 `:250`；`django/core/handlers/asgi.py:249-252` 走 `get_response_async` |
| 数据库是默认 SQLite，**没有 `OPTIONS.timeout`、没有 WAL、没有 PRAGMA** | [`backend/backend/settings.py:305-310`](../../../backend/backend/settings.py)：`{"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}`；全后端搜 `journal_mode`/`PRAGMA`/`wal`/`busy_timeout` **0 命中** → 回滚日志模式 + sqlite3 默认 5s busy timeout |
| 每次业务写库都会**再插一行审计**（同一事务、同一线程） | [`backend/apps/common/signals.py:114-125`](../../../backend/apps/common/signals.py) → [`backend/apps/common/audit.py:63-76`](../../../backend/apps/common/audit.py)（`AuditLog.objects.create`） |
| **每一个 4xx/5xx 也插一行审计**（含 401/403/409） | [`backend/apps/common/exceptions.py:43-50`](../../../backend/apps/common/exceptions.py) → `log_exception` |
| 每个客户端每 **20 秒**打一次 `/auth/me` | [`frontend/src/stores/auth.ts:165`、`190`](../../../frontend/src/stores/auth.ts) |
| axios 全局 **15s 超时**；nginx `/api/` `proxy_read_timeout 120s` | [`frontend/src/api/request.ts:38-40`](../../../frontend/src/api/request.ts)（`axios.create({ timeout: 15000 })`）、[`deploy/nginx-gipfel.conf:55`](../../../deploy/nginx-gipfel.conf) |
| nginx 只有**一个** upstream、`max_fails=3 fail_timeout=3s`、**没有任何 `limit_req`/`limit_conn`** | [`deploy/nginx-gipfel.conf:9-13`](../../../deploy/nginx-gipfel.conf)；搜 `limit_req`/`limit_conn` 0 命中 |
| 断线重连后**对每个已加载集合并发补拉**，产业字段还**按公司逐个**发请求，无并发上限、无单飞 | [`frontend/src/api/request.ts:753-790`](../../../frontend/src/api/request.ts)（`await Promise.all(cols.map(...))`，`/company-fields/${cid}` 就在 map 内） |
| 后端无全局 HTTP 限流（README §6 自述） | [`README.md:206`](../../../README.md) |

**放大系数**：一次业务写入 = 1 次业务 INSERT + 1 次审计 INSERT，且都在同一条 SQLite 写锁上；一次错误响应也要写一行审计。SQLite 在回滚日志模式下**写操作互斥**，所以「唯一线程 + 单写锁 + 双写放大」三者叠加，任何一批并发写都会串成队列。

### 2.3 现场怎么触发（量化）

- **稳态**：100 客户端 / 20s 心跳 = **5 req/s** 打底，全部串在一个线程上。
- **断线重连风暴**：Wi-Fi 抖动 5 秒后恢复，若每人页面已加载 10 个集合、其中 5 人访问过 20 家公司的详情页 → 一次重连 ≈ `100×10 + 5×20 ≈ 1100` 个请求，全部涌向一个线程；期间任何人的操作都排队，15 秒后前端集体报「请求超时」。
- **叠加财年定时器**：定时器要跑「公司数 × 启用定时器的字段数」次写入 + 每家公司一次计算图重算，全部占用那唯一线程。现场表现：**主席团点「开始新财年」的那一刻，全场卡住**。
- 【复核补充】与 O-02(f) 同一个限定：当前库里 `timer_enabled=0`，所以**这条叠加效应现在还没生效**；一旦赛前按规则把财年初定时器字段配好，它就会生效。

### 2.4 后果

- 学生端 15s 超时 → 大量「请求超时」toast；nginx 120s 超时 → 504。
- 最危险的是**界面说失败、库里已成功**（写请求已经提交、响应还没回），学生会重复提交。
- `database is locked` 会随写锁争用出现，且每次失败又写一行审计，进一步加压。

### 2.5 报告给的建议（赛前必须做，不改代码）

1. 把财年推进安排在**茶歇/午休**（`v2.txt` 里恰好有 16:00–16:30、12:00–14:00、17:15–17:45 三段空档），而不是整点开赛时刻。
2. 赛前用真实数据做一次压测/演练：`ab`/`wrk` 或直接开 20 个浏览器标签 + 一次财年推进，记录耗时。
3. 若条件允许，**改用 PostgreSQL**（原报告称 `DATABASE_URL` 已支持切换），至少消除写锁争用；并把 nginx 加上 `limit_req` 兜底。

### 2.6 复核补充（重要校正）

> **【复核补充】「`DATABASE_URL` 已支持切换」这条在代码里不成立。**

我在 `backend/` 下搜 `DATABASE_URL` / `postgres` / `dj_database_url`：**只有 [`backend/README.md:89`](../../../backend/README.md) 的文档表格提到**，`settings.py` 里是**硬编码** SQLite（[`settings.py:305-310`](../../../backend/backend/settings.py)），**没有** `os.environ` 读取，也没有 `dj-database-url` 之类的依赖。
→ 结论：**「改用 PostgreSQL」不是配置项，而是一件需要改代码 + 装驱动 + 迁移数据的小工程**。赛前 T-1 天临时上 PostgreSQL 风险很高；这条只能作为「如果还有好几天时间」的选项，或者干脆放弃、改用「错峰 + 压测 + 降级预案」。

其他补充：
- `limit_req` 那条是真的没做，但即使加上，**财年定时器占线程**这件事也治不了（限流只挡并发请求，挡不住一个长请求占住唯一线程）。
- 减负的低成本做法（不改代码）：让学生端**少开标签页**、少访问公司详情页，可以直接降低重连风暴的请求数（`company-fields/{cid}` 是每人已访问公司数条请求）。

---

## 3. O-05（P0）·没有任何备份，文档指向的脚本不存在

### 3.1 缺陷说的是什么

生产环境**没有任何定时备份、没有 systemd timer、没有 cron 条目**；**也没有「每个财年前自动快照」**。两天六个财年，可恢复点实际上只有「上次部署时的 `_backup/<时间戳>`」。文档让你照着 `docs/MIGRATION.md` 手写一个 `scripts/backup.sh`，**但那个脚本在仓库里不存在**，而且文档示例用的是**热拷贝正在被写入的库**——正是本项目自己在部署脚本里点名批评并已修复的反模式。

### 3.2 代码/仓库里到底是什么机制（证据链）

**（a）有正确的快照函数，但只被部署/升级脚本调用**

[`scripts/lib/deploy-common.sh:180-181`](../../../scripts/lib/deploy-common.sh) 有 `snapshot_sqlite_consistent`（SQLite 一致性快照）。

【复核补充】实查 `scripts/` 目录内容：

```
scripts/bootstrap-dev.bat   scripts/deploy-linux.sh    scripts/dev.py
scripts/gen_logviewer_key.py  scripts/lib/  scripts/make_favicon.py
scripts/migrate-server.sh   scripts/quick-sync.sh      scripts/start-dev.bat
scripts/stop-dev.bat        scripts/update-from-github.sh  scripts/verify-migration.sh
```

→ **确实没有 `scripts/backup.sh`**。

**（b）文档给的示例是热拷贝，且要你手写**

[`docs/MIGRATION.md:500-520`](../../MIGRATION.md)

```bash
cat > /opt/gipfel/scripts/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/gipfel/_backup/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -a /opt/gipfel/backend/db.sqlite3 "$BACKUP_DIR/"     # ← 对正在被写入的活库做 cp -a
cp -a /opt/gipfel/backend/.env "$BACKUP_DIR/"
cp -a /opt/gipfel/backend/uploads "$BACKUP_DIR/"
echo "备份完成: $BACKUP_DIR"
EOF
chmod +x /opt/gipfel/scripts/backup.sh
echo "0 3 * * * /opt/gipfel/scripts/backup.sh" | sudo crontab -
```

- 这是**热拷贝**：SQLite 在回滚日志模式下，`cp -a` 一个正在被写的库可能拷到「主库已更新、日志未合并」的中间态，恢复出来可能损坏或丢最近事务。正确做法是 `sqlite3 db.sqlite3 ".backup '<目标>'"`（或用仓库里已有的 `snapshot_sqlite_consistent`）。
- 而且它是**文档示例**，不是仓库文件 → 部署时不会自动存在，**没有任何人被执行过这一步**。
- 另外这条 crontab 是**凌晨 3 点**跑，对「两天上午 8:30 到晚上 18:30 的比赛」而言，两次运行之间隔着整个比赛日 → 即使真的挂上了，**赛中的可恢复点也几乎为零**。

**（c）运维文档只有一句话**

[`docs/OPS.md:103-111`](../../OPS.md) §8「备份与恢复」只有「停服务 → 用备份覆盖目录 → 重启」，**没有说明备份从哪来、谁在什么时候做**。

**（d）现场电源风险被规则本身承认**

`v2.txt` P10 明确「需携带插线板」，说明组织方已预期插座不足。→ 服务器那台跑 `/opt/gipfel` 的机器若放在会场，接线板被踢掉即全场比赛终止，**且没有 UPS、没有第二台热备、没有现场恢复脚本**。

### 3.3 现场怎么触发

15:50 第五财年，服务器/笔记本被碰掉电，或有人拔了插线板、或 systemd 之外的意外重启导致 db 损坏。

### 3.4 后果

**两天六个财年的数据全部丢失**，只能回到上次部署时的快照（如果部署时那步 `_backup` 做过）。比赛无法继续，也没有第二个数据源（`contract_watcher` 的 Excel 只覆盖部分公司、且依赖它一直在跑）。

### 3.5 报告给的建议

1. 赛前手写一个 `backup.sh`（用 `sqlite3 db.sqlite3 ".backup"` 或直接复用 `snapshot_sqlite_consistent`），**每财年开始前 + 每天 18:30 收尾后**各跑一次，拷到 U 盘 + 另一台机器。
2. 把 `db.sqlite3` 与 `uploads/` 的备份目录打印成一张卡片贴在服务器旁。
3. 原报告 §6 T-0 补充：开赛前立刻做一次 DB 快照；**每个财年开始前再做一次（6 次，每次 30 秒）**；超管设备固定一台、接 UPS/固定插座。

### 3.6 复核补充

- 需要注意 `uploads/` 也要一起备份，且 systemd 单元里 `ReadWritePaths` 白名单包含 `__INSTALL_DIR__/backend/uploads`、`logs`、`db.sqlite3`（[`deploy/gipfel.service:69`](../../../deploy/gipfel.service)）→ 备份脚本如果要写到 `/opt/gipfel/_backup/`，**不受该单元约束**（它是在 systemd 之外以你的 shell 身份跑的），但要注意目录属主与 `UMask=0027`/运行目录权限，别让备份目录对 `gipfel` 用户不可读或反过来破坏权限边界。
- **备份必须演练一次恢复**（原报告 T-7 天第 1 条：「验证能恢复」）。没有验证过的备份等于没有备份。

---

## 4. O-09（P1）·客户端无离线/降级能力

### 4.1 缺陷说的是什么

前端**没有 Service Worker / PWA / manifest，也没有任何 `navigator.onLine` 处理**；IndexedDB 只是**读缓存**（按 realm + 账号分库），**没有写队列**。断网时页面上的旧数据还在，但任何写操作直接失败并 toast，学生无事可做，也没有「离线模式，请稍后重试」的明确提示。

### 4.2 代码里到底是什么机制（证据链）

- 【复核补充】在 `frontend/` 下搜 `navigator.onLine` / `serviceWorker` / `ServiceWorker` / `manifest.json`：**0 命中**。
- 版本巡检与心跳是其仅有的周期行为：每 20s `/auth/me`、每 5min 版本复核（[`frontend/src/App.vue:20-27`](../../../frontend/src/App.vue)）。
- 断网时 axios 请求走到 15s 超时（[`frontend/src/api/request.ts:38-40`](../../../frontend/src/api/request.ts)），凭据失效时走 401 分支 → 统一 toast。

### 4.3 现场怎么触发

共享 Wi-Fi 抖动、会场 AP 被踢、学生笔记本休眠唤醒、笔记本切到手机热点。

### 4.4 后果

- 表面后果：学生点「保存」没反应、弹 toast，不知道是自己的网还是系统的问题。
- **真正的后果是它和 O-04 耦合**：网络恢复的那一刻，100 个客户端同时执行「断线重连主动对账」，每个客户端对每个已加载集合并发补拉 → 单线程服务端在几十秒内收到数千请求，**期间任何人无法操作**。也就是说：**断网本身不是最大问题，恢复的那一秒才是**。
- 没有写队列 → 断网期间的填报动作全部丢失，学生得自己记住重做。

### 4.5 报告给的建议

原报告在 O-09 条目下没有单独给建议，但 §2.5 与 §6 的精神是：**赛前明确告知「断网就等，不要反复点」**，并把「减少重连风暴」当作 O-04 的缓解手段一起处理（错峰、少开标签页），因为客户端侧短期无法改代码。

### 4.6 复核补充

- 值得强调的对比：**读缓存是有的**（IndexedDB 按 realm+账号分库），所以断网时页面**看起来一切正常**（旧数据在），这会让学生以为系统可用而继续点 → 更容易触发恢复瞬间的请求风暴。这是「半可用」状态，比直接白屏更危险。
- 如果赛前有时间做一点前端改动，**最小改动收益最大的是**：重连对账加并发上限（甚至改成串行 + 随机抖动），这直接砍掉 O-04 的峰值。

---

## 5. O-10（P1）·账号界面三个硬限制，且会静默清掉手工授的权限

### 5.1 缺陷说的是什么

账号管理界面：**角色下拉只有三项**（超级管理员 / 管理员 / 参赛选手）、**没有权限勾选框**（权限由 `derivePermissions(role, companies)` **硬编码派生**）、而且**编辑保存时会固定提交派生结果**，于是之前用 API 手工授的扩展权限（如 `data:region:edit`）会被**静默覆盖删除**，没有任何提示。

### 5.2 代码里到底是什么机制（证据链）

**（a）三个角色 + 无权限勾选**

[`frontend/src/views/account-management/AccountManagementView.vue:133-142`](../../../frontend/src/views/account-management/AccountManagementView.vue)（角色下拉只有三项）；
[`AccountManagementView.vue:245-313`](../../../frontend/src/views/account-management/AccountManagementView.vue) 的 `derivePermissions(role, companies)` 是**纯函数硬编码派生**：

```ts
// 简化权限：身份(role) + 所选公司 → 自动派生权限与四个范围
// - SUPER_ADMIN：空（隐式全权限）。
// - COMPETITION_ADMIN（管理员）：… contract:execute 为比赛级执行，不受公司范围限制 …
// - PLAYER（选手）：… 下单/撤单仅需 stock:view …
```

→ 想在界面里给「消费者」账号加 `data:region:edit`，**界面根本做不到**，只能直接调 API。

**（b）保存时固定提交派生值 → 覆盖手工授权**

[`AccountManagementView.vue:456-467`](../../../frontend/src/views/account-management/AccountManagementView.vue)

```ts
const derived = derivePermissions(form.role, managedCompanies.value);
if (isEdit.value && editingId.value) {
  await usersApi.update(editingId.value, {
    role: form.role,
    displayName: form.displayName,
    permissions: derived.permissions,          // ← 固定提交硬编码派生结果
    companyScopes: derived.companyScopes,
    viewCompanyScopes: derived.viewCompanyScopes,
    contractViewCompanyScopes: derived.contractViewCompanyScopes,
    stockCompanyScopes: derived.stockCompanyScopes,
  });
}
```

后端[`backend/apps/users/views.py:124-136`](../../../backend/apps/users/views.py) 的上限校验是「**提交值或既有值**」：

```python
# 授予上限校验：以新角色为准，权限取「提交值或既有值」
effective_role = serializer.validated_data.get("role", user.role)
effective_perms = serializer.validated_data.get("permissions", user.permissions_list)
_assert_grant(request, effective_role, effective_perms)
```

提交值在允许范围内即通过 → **之前用 API 授的扩展权限被覆盖删除**。
→ **只要有人事后在界面上「编辑」过一次这个账号，消费者就再也发不了需求，而且没有任何提示。**

### 5.3 现场怎么触发

- 赛前用 API 给各产业主席团授 `company:manage`、给消费者账号授 `data:region:edit`（这是唯一能绕过 O-01 的办法）。
- 赛中有人发现「某个账号名字写错了」，在界面上编辑并保存 → 权限被清空，**当场表现为「这个人突然什么都做不了」**，且没有任何错误提示，排查成本极高。

### 5.4 后果

- 真实角色（主席团按产业、审计、消费者、资产评估机构）在这套 RBAC 里**没有对应账号类型**：只有 `COMPETITION_ADMIN`，且**公司范围只能按公司勾选、没有「产业」维度**，所有比赛管理员权限完全相同、无产业隔离。
- 审计与执行没有职责分离：`COMPETITION_ADMIN` 同时持有 `contract:manage` + `contract:execute`，而 `GET /api/audit-logs` 需要 `account:manage`（超管专属，[`backend/apps/audit/views.py:25`](../../../backend/apps/audit/views.py)）→ **审计看不到审计日志**。
- 这些都让「手工 API 授扩展权限」成为唯一的应急路径，而 O-10 让这条路径**随时可能被一次界面操作毁掉**。

### 5.5 报告给的建议（绕过方案）

- 赛前用 API（`POST /api/users` 带 `"allowExtras": true`）为每个产业建一个 `COMPETITION_ADMIN`，并把 `company:manage` 授出去，让主席团能替本产业公司填字段；**但必须立下规矩：不要再用界面「编辑」这些账号**（否则权限被清空）。
- 明确「谁持有超管」：超管只保留 1 台设备，且这台设备只做财年推进 / 账号 / 数据管理，其余操作交给 `COMPETITION_ADMIN`。
- 原报告 §6 第 5 条：写角色映射表（真实角色 → 具体账号名 → 角色 → `companyScopes` → 权限集合），并**在表上打印「不要在界面上编辑这些账号」**。

### 5.6 复核补充

- `company:manage`、`data:region:edit`、`message:manage` 这些都在 `_COMPETITION_ADMIN_EXTRAS` 里，**必须由超管在请求体里显式写 `allowExtras=true` 才能授予**；界面不提供这个开关（原报告 §2.6 已说明）。
- 实操建议：把「授了哪些扩展权限」和「账号名」写进那张纸质映射表，**并且记录授权的 API 调用命令**——因为一旦被界面清掉，你需要能原样重放。

---

## 6. O-11（P1）·版本硬封锁：一次热修锁死全场

### 6.1 缺陷说的是什么

客户端版本号与服务端 `/api/version` **不一致时，前端会拒绝发出任何业务请求**（除了版本校验请求自己）。所以任何一次后端升级/热修，只要版本号对不上，**全部客户端被硬封锁**，学生只能硬刷新（Ctrl+Shift+R）才能继续。而 `index.html` 也没有显式 `no-cache`。

### 6.2 代码里到底是什么机制（证据链）

**（a）不一致 = 硬封锁**

[`frontend/src/stores/version.ts:30-38`](../../../frontend/src/stores/version.ts)

```ts
const client = getClientVersion();
const blocked = !!serverVersion.value && serverVersion.value !== client;
versionBlocked.value = blocked;
if (blocked) {
  showDialog.value = true;
  startPolling();          // ← 每 5 分钟再校验一次
} else {
  stopPolling();
}
```

[`frontend/src/api/request.ts:42-50`](../../../frontend/src/api/request.ts)

```ts
// 版本硬封锁：客户端版本与服务端不一致时，除显式 bypassVersionBlock 的版本校验请求外，
// 一律拒绝并阻断网络，实现「无法使用任何功能、发出任何请求」。
if (versionBlocked.value && !config.bypassVersionBlock) {
  return Promise.reject(
    new Error("客户端版本与服务端不一致，已禁用全部请求，请联系管理员获取最新版本"),
  );
}
```

注意：这**不是**网络层失败，是**拦截器主动拒绝** —— 也就是说服务是好的、网是通的，但前端自己不发请求。Socket.IO 侧同样被拦（[`frontend/src/realtime/socket.ts:28`](../../../frontend/src/realtime/socket.ts)）。

**（b）为什么硬刷新能救**

客户端的「版本号常量」来自打包进 bundle 的代码（`getClientVersion()`，注释见 [`version.ts:7`](../../../frontend/src/stores/version.ts)：「Web 化后客户端版本号来自 version.ts 常量」）。硬刷新会重新拉 `index.html` 与新的 JS bundle，新 bundle 里的常量就与服务端一致了 → 解锁。**普通刷新（F5）可能命中缓存拿到旧 bundle，所以救不了**——这就是必须 Ctrl+Shift+R 的原因。

**（c）index.html 没有显式 no-cache**

[`deploy/nginx-gipfel.conf:154-156`](../../../deploy/nginx-gipfel.conf)

```nginx
location / {
    try_files $uri $uri/ /index.html;
}
```

没有 `add_header Cache-Control "no-cache"`，也没有对 `index.html` 单独设 `expires -1`。这就是 O-26 说的「配合版本硬封锁有『刷了还是旧版本』的排查成本」。

### 6.3 现场怎么触发

午休时运维「修个小 bug」跑了 `scripts/update-from-github.sh` → 服务重启 + 版本号变化（或前端 bundle 仍是旧的）→ `VERSION.json` 与 `/api/version` 不一致 → **全部客户端被版本硬封锁**。

### 6.4 后果

- 全场 100 台设备同时弹「版本不一致」，业务请求全部被本地拒绝 → **比赛暂停**。
- 恢复方式是**每台设备手动硬刷新**，没有批量手段，且学生不会知道要按 Ctrl+Shift+R。
- 更糟的是它和 O-10/O-06 叠加：如果此时超管还想后台操作，5min 轮询会继续封锁。

### 6.5 报告给的建议

原报告 §6 T-0 第 11 条：**午休不升级、不重启**（→ 避免 O-11）。以及 T-1 天打印现场卡片（含应急命令）。

### 6.6 复核补充

- 这条**完全可以用纪律消除**：开赛那一刻起，服务端与前端 bundle 一律冻结，任何人不得部署。把这条写进超管卡片，比任何代码修复都可靠。
- 万一真发生：正确的口头指令是「**Ctrl+Shift+R 强刷**」，不是「刷新一下」。建议把这个动作**写进学生端的纸质须知/开场播报**。
- 【复核补充】5 分钟轮询意味着**恢复也会滞后最多 5 分钟**（如果只是服务端版本回退而不是前端更新）——所以别指望它自愈。

---

## 7. O-12（P1）·签到高峰：100 人 × bcrypt × 强制改密，全部串行

### 7.1 缺陷说的是什么

7:30–8:30 签到，100 名学生用初始口令首次登录。每次登录 = **bcrypt cost=12 校验** + **顶号写库** + **强制改密再一次 bcrypt 哈希**，全部串行在同一个线程上（O-04 的单线程）；同时**同一账号只能一台设备在线**，两人共用账号（或 CFO 与 CEO 各带一台电脑）会互相踢下线。

### 7.2 代码里到底是什么机制（证据链）

**（a）bcrypt cost=12**

[`backend/apps/users/models.py:80-95`](../../../backend/apps/users/models.py)

```python
def set_password(self, raw_password):
    """用 bcrypt cost=12 哈希，与原 bcryptjs 兼容。"""
    import bcrypt
    salt = bcrypt.gensalt(rounds=12)
    self.password_hash = bcrypt.hashpw(raw_password.encode("utf-8"), salt).decode("utf-8")

def check_password(self, raw_password):
    import bcrypt
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), self.password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        ...
```

cost=12 意味着**单次哈希/校验是几十到几百毫秒量级的 CPU 密集操作**（原报告按 ~250–300ms 估算；具体取决于服务器 CPU），而它**跑在 Python 进程内、占的就是那个唯一的同步线程**。

**（b）登录时顶号 + 写库 + 踢人**

[`backend/apps/auth/views.py:161-173`](../../../backend/apps/auth/views.py)

```python
# 顶号下线：递增 token_version，旧 token 立即失效
user.token_version = (user.token_version or 0) + 1
user.save(update_fields=["token_version", "updated_at"])

# 立即踢掉旧设备：通知 + 真正断开旧连接
from apps.realtime.emit import kick_user_sessions
kick_user_sessions(user.id, reason="token_version_mismatch")

record_login_success(ip, username)
token = create_jwt(user)
```

**（c）新账号默认强制改密，改密期间业务接口全部 401**

- 建号默认 `mustChangePassword=True`：[`backend/apps/users/views.py:86-95`](../../../backend/apps/users/views.py)（「创建的账号默认 mustChangePassword=true：管理员设置的初始密码仅作交付凭据，首次登录强制修改」）。
- 认证层刚性拦截：[`backend/apps/auth/authentication.py:131-137`](../../../backend/apps/auth/authentication.py)

```python
# 强制改密：除改密接口外全部拦截
if getattr(user, "must_change_password", False) and not _is_change_password_endpoint(request):
    raise exceptions.AuthenticationFailed("账号需先修改初始密码", code="must_change_password")
```

→ 学生登录成功后，**在改完密码之前点任何业务页面都是 401**；而「改密」本身又是**再一次 `set_password`（第二次 bcrypt cost=12）**。

**（d）同账号单设备**

[`backend/apps/auth/authentication.py:125-129`](../../../backend/apps/auth/authentication.py)

```python
# 顶号下线：token 中 tv 与用户当前 token_version 不一致
if payload.get("tv") != user.token_version:
    raise exceptions.AuthenticationFailed("账号已在其他设备登录", code="token_version_mismatch")
```

→ 每次登录都递增 `token_version`，所以**后登录的那台设备必然把先登录的踢掉**（`kick_user_sessions` 还会真正断开旧 socket）。

### 7.3 现场怎么触发

7:30–8:30 签到窗口，100 人几乎同时首次登录；其中一部分人还会两人共用一个账号，或者一家公司的 CEO/CFO 各带一台电脑同时用同一账号。

### 7.4 后果

- 签到时间被拉长：100 人 ×（校验 + 写库 + 改密哈希）全部串在唯一线程上，**估算在分钟到十分钟量级**（原报告 §1 第 8 条列了这条场景）。若叠加 O-04 的稳态心跳，队列更长。
- 「登录了但什么都点不了」：强制改密期间业务接口 401，学生会以为系统坏了，去排队找主席团 → **主席团也被占用**。
- 「互相踢下线」：共享账号/多设备会被反复踢，每次都触发一次 `token_version` 写入 + socket 断开，形成放大循环（原报告 O-06 描述 `contract_watcher` 自动重登 + 浏览器抢号会**无限循环**互踢）。

### 7.5 报告给的建议

原报告没有给 O-12 单独建议，§2.6 的绕过方案与 §6 的 T-1 天第 9 条相关：**打印「账号口令交接表」**、T-7 天写角色映射表（每个真实角色 → 具体账号名 → 设备）。

### 7.6 复核补充（可执行的减负办法）

- **赛前批量改密**：在赛前（不是签到当天）用管理员 API 为每个账号设好最终口令并把 `mustChangePassword=false`（`users/views.py:95` 说明请求体显式传 `mustChangePassword: false` 可豁免）→ 签到当天就只剩**一次** bcrypt，而不是两次，且不会再出现「登录后 401 干等」。**这是这条缺陷最有效的赛前护城河，代价只是多印一张口令表。**
- **明确规定「一个账号一台设备」**：写进角色映射表，避免 CFO/CEO 抢号。若确实要让两人各自操作，就该开两个 `COMPETITION_ADMIN` 账号（配合 O-10 的 API 授权）。
- **错峰签到**：按产业/公司分批（例如 8:00 / 8:15 / 8:30 三批），把峰值压到 1/3。

---

## 8. O-13（P1）·字段变更与落账明细不进审计 → 无法举证「数据造假」

### 8.1 缺陷说的是什么

`CompanyFieldValue`（公司产业字段值）**被显式标记为不追踪**，所以**现金、库存、配额、碳排这些真正会被「数据造假」的数字，谁在什么时候从 X 改成 Y，系统里查不到**。`ContractFieldEffect`（合同落账明细）同样不追踪（另有表存证）。同时审计日志**只增不删、只有超管能看、没有按公司/记录/时间段的查询**。

### 8.2 代码里到底是什么机制（证据链）

**（a）None = 不广播，同时也不连审计信号**

[`backend/apps/realtime/emit.py:145-155`](../../../backend/apps/realtime/emit.py)

```python
"Company": "companies",
"CompanyFieldValue": None,          # ← 显式标记为「不追踪」
"ContractType": "contract-types",
"Contract": "contracts",
"ContractFieldEffect": None,        # ← 同上
```

[`backend/apps/common/signals.py:194-202`](../../../backend/apps/common/signals.py)

```python
for model in app_conf.get_models():
    name = model.__name__
    if name not in tracked:
        continue
    # None 表示该模型只列在映射里但显式标记为「不广播」（如 PartMaterial/子表）
    if MODEL_TO_RESOURCE.get(name) is None:
        continue                                    # ← 直接跳过，post_save 不连接
    post_save.connect(_on_post_save, sender=model, weak=False)
    post_delete.connect(_on_post_delete, sender=model, weak=False)
```

这意味着**字段值的每一次写入（含超管手填、合同落账、财年定时器批量写）都不产生审计行**。README §6 也自述了这一点。

**（b）审计日志只有超管能看，且过滤维度极少**

[`backend/apps/audit/views.py:20-56`](../../../backend/apps/audit/views.py) 只支持 `kind` / `model` / `operatorId` / `competitionId` 四个过滤；`GET /api/audit-logs` 要求 `account:manage`（超管专属，[`audit/views.py:25`](../../../backend/apps/audit/views.py)）→ **审计看不到审计日志**。前端页面 `pageSize=20`（[`frontend/src/views/system/AuditLogView.vue:166`](../../../frontend/src/views/system/AuditLogView.vue)）。
→ 「查一下 A 公司今天的现金变动」这件事**做不到**。

**（c）审计表只增不删**

原报告 §5 E-2 实测：本库已积累 **7118 行**（`write 7057 / error 61`）。两天 100 客户端会把这张表推到几万行，没有清理/归档命令；而 `AuditLogListView` 每次都要 `qs.count()`。

**（d）纸质合同与系统记录只有一个连接点，且可被越范围填写**

- 唯一连接点是各参与方手填的 `contractNumber`（[`backend/apps/contracts/views.py:411-460`](../../../backend/apps/contracts/views.py)）——**没有校验、没有唯一性约束、没有纸质模板**。
- 编号补全的范围校验对比赛管理员是失效的：[`contracts/views.py:896-911`](../../../backend/apps/contracts/views.py) 的 `_assert_edit_party_scope` 在持有 `contract:execute` 或 `contract:manage` 时**直接放行**。而每个主席团都是这样的账号 → **任何一个主席团成员都可以替别家公司的合同填编号**（本应「各公司自填自己那份」）。

### 8.3 现场怎么触发

`v2.txt` P10：「参赛人员应秉持诚信原则…不得有任何作弊行为（如数据造假、恶意竞争等），一经发现，立即取消参会资格并进行公示。」→ 只要出现争议（某公司声称现金不对、怀疑对手改数），审计组就要举证。

### 8.4 后果

- **无法事后举证**：系统里查不到字段变更历史，只能靠 `contract_watcher` 输出的 `records/<key>/contract_<id>_..._readable.json`（含参与方/填写内容/前置检查/落账明细）与**纸质合同**比对。原报告称这是「本系统里唯一真正好用的赛后取证/对账底稿工具」。
- **当场只能以纸质合同为准**（原报告应急卡原话）。如果纸质合同本身没签、编号没填，就彻底无解。
- 审计日志膨胀还会让「翻页查证据」变得很慢（配合 O-24）。

### 8.5 报告给的建议

- 原报告 §2.8 明确：赛前必须把 `contract_watcher` 跑起来（**独立账号**），把每份通过合同翻译成中文可读记录，作为「当天账表底稿」与「赛后取证」。
- 应急卡：怀疑选手改数 → 系统查不到字段变更（O-13）→ 只能靠 `contract_watcher` 的 `records/*_readable.json` 与纸质合同比对；**当场以纸质合同为准**。
- §2.9：赛前必须准备各合同类型的**纸质模板（份数不同）+ 编号规则 + 「谁在系统里录入编号」的分工**——这三件东西目前一件都没有。

### 8.6 复核补充

- 注意 `CompanyFieldValue` 既不广播也不审计，意味着**财年定时器的批量写入同样无痕迹**。所以 O-02 的「静默半成功」在事后也**无法从审计日志复盘**，只能看 `gipfel.log`（日志会滚动/可能被覆盖）→ 建议赛前就把 `backend/logs/gipfel.log` 做一份独立留档（原报告 T-0 第 12 条要求第二块屏 `tail -f`，可以同时改成 `tail -f | tee`）。
- 合同执行本身**是好的**：先原子抢占 `EXECUTED` 再落账、重复执行返回 400、执行完显式补写审计（含落账字段摘要，最多 50 项），且每笔落账写一行不可变的 `ContractFieldEffect`（`op / value_raw / before_raw / after_raw`），可事件溯源复原（[`contracts/views.py:332-406`](../../../backend/apps/contracts/views.py)、[`contracts/models.py:85-115`](../../../backend/apps/contracts/models.py)）。→ **「通过合同产生的变更」可追溯，「绕过合同直接改字段」不可追溯**。这条区别很重要：它决定了现场举证的抓手是**合同编号**，所以纸质合同与编号纪律必须严格。

---

## 9. 这 8 条的相互关系（为什么不能一条一条孤立地修）

```
O-04（单线程 + SQLite + 无限流 + 审计双写）
  ├─ 放大 O-02：财年定时器占住唯一线程 → 全场卡住 → 前端 15s 超时 → 用户重复点 → 更多请求
  ├─ 放大 O-09：断网恢复瞬间 1100 个请求涌向唯一线程 → 几十秒内无人能操作
  └─ 放大 O-12：100 次 bcrypt 登录排在同一线程上 → 签到变慢

O-02（静默跳过）
  └─ 后果无法从审计复盘（因为 O-13：字段变更不入审计）→ 只剩 gipfel.log

O-10（界面清权限）
  └─ 是绕开 O-01（选手无写权限）的唯一路径，一旦在界面编辑就失效 → 全场业务再次退化为「一个超管录数据」

O-11（版本硬封锁）
  └─ 与 O-02/O-04 叠加：赛中重启 → 全场被锁 + 正在跑的定时器状态不明

O-05（无备份）
  └─ 是 O-04「重复提交/半成功」与 O-13「查不到变更」的最终保险；没有它，任何数据层面的错误都无法回退

O-13（无变更审计）
  └─ 使 O-02 的「静默半成功」和 O-04 的「界面说失败、库说成功」都变成不可复盘事件
```

**一句话**：O-04 是**放大器**，O-02 是**触发器**，O-05 是**唯一保险**，O-13 是**复盘能力的缺失**，O-09/O-11/O-12 是**三个高频现场触点**，O-10 是**唯一能绕过 O-01 的操作路径**。

---

## 10. 复核校正与不确定项（读这份解释时必须知道的边界）

1. **【校正】「`DATABASE_URL` 已支持切换」在代码中不成立。** `settings.py:305-310` 硬编码 SQLite，全后端无 `DATABASE_URL`/`postgres`/`dj-database-url` 引用，只有 `backend/README.md:89` 的文档表格提到。→ 「赛前改用 PostgreSQL」不是改配置，而是改代码 + 装驱动 + 迁数据的工程。
2. **【限定】O-02 / O-04 的「卡住全场」是条件性后果。** 当前库 `timer_enabled` 字段 = 0，`timer.py:174-175` 会直接 `return`，所以**现在**推进财年是瞬时的。只有赛前按规则把财年初定时器字段配好之后，阻塞才会真实出现。**不要因为"我试了一下很快"就判定无风险。**
3. **【限定】定时器锁是进程内 `threading.Lock`。** 单进程 daphne 下有效；若起多 worker，这把锁完全失效（FY_END 与 FY_START 会真并发）。
4. **【限定】事务边界在「公司」一级**，不是全场一个事务（`timer.py:239-243`）→ 「部分公司成功、部分失败」是允许的中间态。
5. **【未实测】所有耗时数字都是结构性推断**（原报告 §5 E-5 自述）：没有跑负载测试、没有跑财年定时器、没有压测。原报告明确要求「赛前必须在真实数据上实测一次，这本身就是 O-15 的一部分」。本文沿用其数字时都加了「估算/量级」限定。
6. **【未验证】本文没有做任何写操作**：未起服务、未跑定时器、未执行合同、未改任何业务库。所有结论均来自只读代码复核 + 原报告的实跑探针（§5 E-1/E-2）。

---

## 附：本文引用到的文件与关键行

| 文件 | 关键行 | 关联缺陷 |
| --- | --- | --- |
| [`backend/apps/competitions/views.py`](../../../backend/apps/competitions/views.py) | 206-229（创建，无 ACTIVE 唯一校验，同步跑 FY_START）、257-261（状态推导 trigger） | O-02 |
| [`backend/apps/company_fields/timer.py`](../../../backend/apps/company_fields/timer.py) | 147-167（非阻塞锁 + 静默跳过）、171-175（无字段直接 return）、189-196（吞单公司异常）、239-243（公司级事务） | O-02 / O-04 |
| [`frontend/src/views/competitions/CompetitionListView.vue`](../../../frontend/src/views/competitions/CompetitionListView.vue) | 130-138（开始新财年按钮 + 本地缓存守卫）、163-174（结束财年行内按钮） | O-02 |
| [`deploy/gipfel.service`](../../../deploy/gipfel.service) | 26-31（单进程 daphne）、46-49（Restart）、69（ReadWritePaths） | O-04 / O-05 |
| [`backend/backend/settings.py`](../../../backend/backend/settings.py) | 305-310（硬编码 SQLite，无 OPTIONS/PRAGMA） | O-04 |
| [`deploy/nginx-gipfel.conf`](../../../deploy/nginx-gipfel.conf) | 9-13（单 upstream、无限流）、55（proxy_read_timeout 120s）、154-156（index.html 无 no-cache） | O-04 / O-11 |
| [`frontend/src/api/request.ts`](../../../frontend/src/api/request.ts) | 38-40（15s 超时）、42-50（版本硬封锁拦截）、753-790（重连对账无并发上限） | O-04 / O-09 / O-11 |
| [`frontend/src/stores/version.ts`](../../../frontend/src/stores/version.ts) | 7、30-46（版本比对 → 硬封锁 + 5min 轮询） | O-11 |
| [`frontend/src/App.vue`](../../../frontend/src/App.vue) | 20-27（启动校验 + 5 分钟复核） | O-09 / O-11 |
| [`scripts/lib/deploy-common.sh`](../../../scripts/lib/deploy-common.sh) | 180-181（`snapshot_sqlite_consistent`） | O-05 |
| [`docs/MIGRATION.md`](../../MIGRATION.md) | 500-520（要你手写 backup.sh + 热拷贝 `cp -a`） | O-05 |
| [`docs/OPS.md`](../../OPS.md) | 103-111（§8 备份与恢复只有一句话） | O-05 |
| [`frontend/src/views/account-management/AccountManagementView.vue`](../../../frontend/src/views/account-management/AccountManagementView.vue) | 133-142（三角色）、245-313（`derivePermissions` 硬编码）、456-467（固定提交派生值） | O-10 |
| [`backend/apps/users/views.py`](../../../backend/apps/users/views.py) | 86-95（建号默认强制改密）、124-136（提交值或既有值校验） | O-10 / O-12 |
| [`backend/apps/users/models.py`](../../../backend/apps/users/models.py) | 80-95（bcrypt cost=12） | O-12 |
| [`backend/apps/auth/views.py`](../../../backend/apps/auth/views.py) | 161-173（顶号 + 写库 + 踢会话） | O-12 |
| [`backend/apps/auth/authentication.py`](../../../backend/apps/auth/authentication.py) | 125-137（tv 校验 + 强制改密全拦截） | O-12 |
| [`backend/apps/realtime/emit.py`](../../../backend/apps/realtime/emit.py) | 149（`CompanyFieldValue: None`）、152（`ContractFieldEffect: None`） | O-13 |
| [`backend/apps/common/signals.py`](../../../backend/apps/common/signals.py) | 194-203（`None` → 不连信号 → 不写审计） | O-13 |
| [`backend/apps/audit/views.py`](../../../backend/apps/audit/views.py) | 20-25、20-56（仅超管、仅 4 个过滤） | O-13 |
| [`backend/apps/contracts/views.py`](../../../backend/apps/contracts/views.py) | 332-406（原子执行 + 补审计）、411-460（编号唯一连接点）、896-911（范围校验直接放行） | O-13 |

> 证据等级沿用原报告约定：**【已读码】**=读过相关代码确认机制但未实跑；**【已复核】**=读码 + 实跑只读探针并附输出。本文所有内容均为 **【已读码】** + 原报告 §5 的 **【已复核】** 探针数据。
