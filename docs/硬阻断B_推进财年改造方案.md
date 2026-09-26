# 硬阻断 B · 「推进财年」改造方案（实现交接文档）

> **用途**：本文档交给**另一个对话/另一位开发者**独立实施。它包含定位、机制、失效模式、改动点、约束、测试与验收标准，**无需回看原分析对话**。
> **基线**：分支 `bugfix-merged`，VERSION 1.4.0。**仓库当前为原始状态，本方案尚未落地任何代码。**
> **范围**：**仅「推进财年」这一个问题（硬阻断 B）**。不含时间锁/窗口（另一议题）、不含财务报表、不含权限模型。
> **所有行号均为当前 HEAD 实测**，改动后行号会漂移，请以函数名/代码片段定位。

---

## 0. 一句话问题

**「推进财年」在系统里不是一次操作，而是两个互不相关、无原子性、无幂等保护的手工动作；执行体（财年定时器）在请求线程内同步跑完全场公司，抢不到进程内锁就静默 return —— 于是会出现「界面显示财年已推进，实际部分或全部公司字段根本没被重置」，而且日志里只有一行 warning。**

赛程把财年当作**唯一硬时间盒**（Update 2.0：AI 财年 + 财年 1–6，每段 70–110 分钟），所以这条不确定链直接决定比赛能否按时推进。

---

## 1. 范围与不做什么

**要做**：让「推进财年」成为**服务端一次原子、可观测、可重试、可回滚**的操作。

**明确不做（避免范围蔓延）**：
- ❌ 不加财年时间窗口 / 倒计时 UI（属另一个议题）
- ❌ 不实现财务报表
- ❌ 不改权限模型（`competition:manage` 仍为超管专属）
- ❌ 不重构计算图引擎
- ✅ **可以做且建议做**：把"谁在什么时候推进了财年、结果如何"落库（这是"可观测"的前提）

---

## 2. 现状测绘（改前必读）

### 2.1 调用链：定时器只有两个触发点

```
前端「开始新财年」按钮
  → POST /api/competitions/:id/fiscal-years        FiscalYearCreateView.post
      → FiscalYear.objects.create(year=…)            写库（status 默认 ACTIVE）
      → FiscalYear.save() 覆写                       记录 _fy_prev_status
      → post_save 信号 → notify_fiscal_year_changed  ★只广播，不跑定时器
      → _broadcast_fiscal_year(fy)                   Socket.IO 广播
      → _apply_fiscal_year_timer(comp.id, "FY_START") ★同步阻塞，跑完全场
      → Response(...)                                ← 响应在定时器跑完才返回

前端「结束财年」按钮
  → PATCH /api/competitions/fiscal-years/:id       FiscalYearUpdateView.patch
      → fy.status = "CLOSED"; fy.save()              写库
      → post_save 信号 → 广播
      → 由 prev_status 推导 → _apply_fiscal_year_timer(comp.id, "FY_END")  ★同步阻塞
      → Response(...)                                ← 响应在定时器跑完才返回
```

**关键事实**：`models.py` 的 `FiscalYear.save()` 覆写会记录 `_fy_prev_status`，**信号层（含 admin / ORM / 归档导入 / 脚本）都能正确判定 `FY_START`/`FY_END` 迁移并广播**；但 **`_apply_fiscal_year_timer` 只在两个 API 视图里被调用**（`views.py:227` 与 `views.py:259/261`）。

> ⚠️ **由此产生一条隐藏不一致**：通过 Django admin、`manage.py shell`、归档导入改财年状态 → **信号会广播 FY_START/FY_END，但定时器不会执行**。字段没重置，前端却收到了"财年开始"。

### 2.2 文件清单（按职责）

| 层 | 文件 | 关键行 | 职责 |
| --- | --- | --- | --- |
| 模型 | `backend/apps/competitions/models.py` | `FiscalYear` 26–57；`save()` 44–57 | `(competition, year)` 唯一；`status ∈ {ACTIVE, CLOSED}`；`save()` 记录 `_fy_prev_status` |
| 视图 | `backend/apps/competitions/views.py` | `FiscalYearCreateView` 206–228；`FiscalYearUpdateView` 231–262 | **唯二**触发定时器处；`_broadcast_fiscal_year` 31–45；`_apply_fiscal_year_timer` 47–58 |
| 信号 | `backend/apps/competitions/signals.py` | `resolve_transition` 55–67；`notify_fiscal_year_changed` 69–116；钩子 120–163 | 广播 `fiscal_year_started/ended`；**不触发定时器** |
| **定时器** | `backend/apps/company_fields/timer.py` | `apply_fiscal_year_timer` **147–167**；`_run_fiscal_year_timer` **170–203**；`_apply_timer_to_company` **206–245**；锁 **60–62** | 改全场字段 + 逐公司重算 + 广播 |
| 字段写入口 | `backend/apps/company_fields/views.py` | `_write_field_value` 71–99；`_recompute_calc_fields` | 实际落库与级联重算 |
| 序列化 | `backend/apps/competitions/serializers.py` | `FiscalYearSerializer` 90–113 | `year`/`status`，**无范围/状态合法性校验** |
| 前端 | `frontend/src/views/competitions/CompetitionListView.vue` | 按钮 130–138 / 163–174；`hasActiveFiscalYear` 260–262；`startNextFiscalYear` 437–457；`endFiscalYear` 459–473 | 两次手工点击；`nextYear` 由前端推算 |
| 前端状态 | `frontend/src/stores/competition.ts` | `applyFiscalYearChange` / `find(f => f.status === "ACTIVE")` | 顶部栏"当前财年" |
| 部署 | `deploy/gipfel.service` | `ExecStart` 单进程 daphne；`Restart=always` | **单进程单线程** |
| 数据库 | `backend/backend/settings.py` | `DATABASES` 330–334 | SQLite，**无 `OPTIONS`/无 WAL/无 busy timeout** |

### 2.3 定时器实现要点（决定「重试是否安全」）

`_run_fiscal_year_timer` 的行为：

1. 取 `IndustryField.objects.filter(timer_enabled=True, timer_trigger=trigger)` → **全局**查询（不带 competition 过滤），再按 `industry_type_id` 分组；
2. 每组取该比赛下**该产业类型的全部公司**；
3. 逐公司调用 `_apply_timer_to_company`：
   - 先取该公司全部字段值构建 `fieldKey -> (field, value)` **触发前快照**（保证字段间引用不受写入顺序影响）；
   - 解析每个定时器字段的目标值（字面量，或 `field:<key>` 引用），序列化后放进 `pending`；
   - `with transaction.atomic():` 内逐字段 `_write_field_value(company.id, field_id, value, version=None)`；
   - 然后 `_recompute_calc_fields(company.id)`（**在 atomic 块之外**）。
4. 广播：对 `affected_companies` 逐个 `emit_resource_changed("company-field", cid, competition_id, "updated")`。

> ✅ **重要**：定时器写入的是**配置好的常量/引用值**（`SET` 语义），不是增量。因此**重复执行是幂等的**（结果一致），只是白耗时间。这一点是"失败后重试即可修复"的依据。
>
> ⚠️ 但 `version=None` 走的是"乐观锁默认关闭"路径（`company_fields/views.py:91-99`），意味着**定时器写入会无条件覆盖**，包括覆盖玩家在同一瞬间手填的值。

### 2.4 并发守卫的实际语义（核心缺陷）

```python
# timer.py:60-62 —— 每比赛一把锁
_fiscal_locks: dict[int, threading.Lock] = {}
_fiscal_locks_guard = threading.Lock()

# timer.py:147-167
def apply_fiscal_year_timer(competition_id: int, trigger: str) -> None:
    ...
    if not lock.acquire(blocking=False):          # ← 非阻塞
        logger.warning("…已在执行中，跳过本次并发触发…")
        return                                    # ← 静默丢弃，无重试、无失败返回
    try:
        _run_fiscal_year_timer(competition_id, trigger)
    finally:
        lock.release()
```

**三个致命性质**：

| 性质 | 后果 |
| --- | --- |
| **锁按 `competition_id` 共用，不分 `trigger`** | `FY_END` 还在跑 → `FY_START` 被丢弃。反之亦然 |
| **非阻塞 + 静默 return** | 调用方（视图）**不知道被跳过了**，照样返回 200 |
| **进程内锁** | 单进程下有效，但**多 worker / 多实例部署时完全失效**（`deploy/gipfel.service` 当前是单进程，所以现在"恰好"有效——这是巧合而非保证） |

---

## 3. 失效模式全清单（B 的验收即逐条消除）

| # | 失效模式 | 触发条件 | 现象 | 代码位置 |
| --- | --- | --- | --- | --- |
| **B-1** | **出现两个 ACTIVE 财年** | 两台设备/两个标签页同时点「开始新财年」；或 admin/脚本/归档导入写入第二个 ACTIVE | 前端 `fys.find(status==='ACTIVE')` 只显示一个，另一个永远躺列表里显示「进行中」 | 后端 `views.py:206-228` **不校验是否已有 ACTIVE**；前端 `:disabled` 只据本地缓存 |
| **B-2** | **点击竞态产生重复财年** | 连点 / 网络重试 | 依赖 `(competition, year)` 唯一约束 → 第二次 **500 IntegrityError**（而非 409） | `views.py:217-223` 是"先查后插"TOCTOU |
| **B-3** | **FY_START 被静默跳过（最严重）** | 「结束财年」后立刻点「开始新财年」；`FY_END` 仍在跑 | **全场公司「财年初」重置值一个都没写入**，财年从错误初值开始；日志仅一行 warning | `timer.py:159-163` 共用锁 + 非阻塞 return |
| **B-4** | **FY_END 被静默跳过** | 反向场景：`FY_START` 仍在跑时点「结束财年」 | 财年末结转/计提未执行 | 同上 |
| **B-5** | **单公司失败被吞** | 某公司某字段写失败（引用缺失/类型错/DB 错） | 该公司部分字段未写 → **部分初始化**；`affected_companies` 仍包含它 → **广播"已更新"，前端看到的是混合状态** | `timer.py:189-196`（warning+continue）、`:199-203`（无条件广播） |
| **B-6** | **无回滚点** | 定时器跑到一半进程崩/断电 | 一半公司已重置、一半没有；**无事务包裹全场**，无补偿 | `timer.py` 仅**每公司**一个 `transaction.atomic()`（`:240`） |
| **B-7** | **请求线程被长时间占用** | 定时器在公司数多时耗时线性增长 | 该请求期间**全场所有 API 排队**（单进程 daphne + 全部同步视图共用线程）；超 120s → nginx 504，但**库里财年已 CLOSED** → "界面说失败、数据说成功" | `views.py:227/259/261` 同步调用；`deploy/nginx-gipfel.conf` `proxy_read_timeout 120s`；前端 axios 15s |
| **B-8** | **CLOSED 可被改回 ACTIVE，且会重跑 FY_START** | PATCH `status=ACTIVE`（前端无入口，但 API 允许） | **全场字段被再重置一遍**；而前端提示写的是「此操作不可撤销」 | `views.py:242-261`；`CompetitionListView.vue:461` |
| **B-9** | **ORM/admin 路径不跑定时器** | 通过 admin / shell / 归档导入改状态 | 信号广播了 `FY_START`，但字段没重置 → 前端与数据不一致 | `signals.py:120-126` 只广播；`timer` 仅在 `views.py` 调用 |
| **B-10** | **前端年份推算可产生 `year=0`** | 空列表首次推进 | `nextYear = 0`（`CompetitionListView.vue:444`）→ 与 Excel/建包路径的 `2026` 口径不一致（实测库里同时存在 `year=0` 与 `year=2026`） | `CompetitionListView.vue:440-444` |
| **B-11** | **`year`/`status` 无输入校验** | 直接调 API | 传 `year=99999`、负年份、无意义 status 全被接受 | `serializers.py:90-113` 仅 `IntegerField` + `ChoiceField`，无范围/业务校验 |
| **B-12** | **双触发无幂等键** | 同一年份被创建两次（不同设备） | B-1/B-2 的另一路径；且没有任何"推进正在执行/已执行"的记录可查 | 全无 |
| **B-13** | **无执行结果可观测** | 任何一次推进 | 界面只有成功/失败 toast；**哪家公司、哪个字段失败了，界面上看不到**（只在 `backend/logs/gipfel.log`） | `timer.py:189-196` |

---

## 4. 设计目标（实施者对号入座）

| 目标 | 可测判定 |
| --- | --- |
| **G1 单飞（互斥）** | 同一比赛**同一时刻只有一个**财年推进在执行；争抢者得到明确的 409/"进行中"，而非静默丢弃 |
| **G2 原子性（语义原子）** | 一次"推进"要么**完整生效**（旧财年 CLOSED + 其 FY_END 完成 + 新财年 ACTIVE + FY_START 完成），要么**明确标记失败并给出可重试点** |
| **G3 幂等 / 可重试** | 对同一次推进重复调用**不产生重复副作用**（定时器本身幂等，见 §2.3；重点是状态不重复推进） |
| **G4 可观测** | 每次推进在库里有记录：谁发起、起止时间、结果（成功/部分失败）、**逐公司逐字段的写入明细与失败明细**；前端可查 |
| **G5 不阻塞全场** | 推进执行期间，**其他比赛/其他读请求不被拖死到 504**；至少有进度反馈 |
| **G6 状态机收敛** | 只允许合法迁移；`CLOSED → ACTIVE` 默认被拒（需显式 `force` 且明确警告） |
| **G7 全路径一致** | 无论从 API、admin、脚本还是归档导入改财年，**执行语义一致**（要么都跑定时器，要么都走统一的服务层） |

---

## 5. 改动方案（分四层，可按序实施）

### 层 1 · 立即收敛（低风险，先做）

**1.1 服务端 ACTIVE 唯一性**

- 在 `FiscalYearCreateView.post` 插入前校验：同比赛是否已有 `status="ACTIVE"` 的财年 → 有则 **409**「该比赛已有进行中的财年（第 N 财年），请先结束」。
- 加**数据库层保险**：`UniqueConstraint(fields=["competition"], condition=Q(status="ACTIVE"), name="uniq_active_fiscal_year_per_competition")`。
  > ⚠️ SQLite 支持**部分索引**（`CREATE UNIQUE INDEX … WHERE …`），Django `UniqueConstraint(condition=…)` 可用。**但必须先清理历史脏数据**（若已存在两个 ACTIVE，迁移会失败）——见 §7.3。
- 把"先查后插"改为**捕获 `IntegrityError` → 409**，避免 TOCTOU 500。

**1.2 输入校验**（`FiscalYearSerializer`）

- `year`：`IntegerField(min_value=0, max_value=<合理上界，如 99>)`；并明确**年份口径**（建议：`0` = AI 财年，`1..6` = 第 N 财年；或统一用 2026 序列 —— **需先与组委会/现有建包数据对齐**，见 §7.2）。
- `status`：继续用 `ChoiceField`。
- 更新路径增加**状态机校验**（层 3）。

**1.3 锁按 trigger 分离**（`timer.py`）

把 `_fiscal_locks: dict[int, Lock]` 改为 `dict[tuple[int, str], Lock]`，键为 `(competition_id, trigger)`。
- 这**直接消除 B-3/B-4**（FY_END 不再阻塞 FY_START）。
- 保留非阻塞语义，但**必须把"跳过"变成可感知的失败**（见 1.4），否则只是把问题从"常发"变成"偶发"。

**1.4 让"被跳过"可感知**

`apply_fiscal_year_timer` **返回执行结果**而不是 `None`：

```python
class TimerResult(NamedTuple):
    ran: bool            # False = 因锁未获取而跳过
    trigger: str
    written_companies: int
    failed_companies: list[tuple[int, str]]   # (company_id, 错误摘要)
    skipped_fields: list[tuple[int, int, str]]  # (company_id, field_id, 原因)
    elapsed_ms: int
```

- 视图层据此决定：**`ran=False` → 向调用方返回 409/202 并说明"上一轮仍在执行，请稍后重试"**，而不是 200。
- 这是 G1/G4 的最小实现。

### 层 2 · 统一服务层（消除 B-9）

新增 `backend/apps/competitions/fiscal_year_service.py`（或 `services.py`），提供唯一入口：

```
advance_fiscal_year(competition_id, *, from_fy_id, to_year, actor, mode) -> FiscalYearAdvanceResult
end_fiscal_year(fy_id, actor) -> FiscalYearAdvanceResult
```

- **两个 API 视图改为调用它**；`FiscalYear.save()` 覆写保留（信号语义不变）。
- 在 `signals.py` 里**不要**再触发定时器（保持"信号只广播"的既有契约，避免 admin 保存时跑重活）；改为**在 admin 的 save_model / 需要一致性的管理命令里显式调用服务层**，从而 G7 达成且不引入隐式重活。
- 服务层内部：先落"推进记录"（层 3），再执行，最后回写结果。

### 层 3 · 状态机 + 推进记录（G2/G4/G6）

**3.1 `FiscalYear` 增 `phase`**

```
PREPARING → ACTIVE → SETTLING → CLOSED
```

- `status` **保持不变**（`signals.py` 的 ACTIVE↔CLOSED 迁移判定依赖它，`tests_fix_verify/test_fy01_signals.py` 19 例必须继续通过）。
- `phase` 只是叠加的阶段门禁：`SETTLING` 期间拒绝玩家写入（可选，属另一议题的收益，但字段现在加上成本极低）。
- 合法迁移集中在一处校验函数里（如 `assert_transition_allowed(prev_phase, next_phase)`）。

**3.2 新增推进记录模型**（建议名 `FiscalYearRun`）

| 字段 | 说明 |
| --- | --- |
| `competition` / `from_fiscal_year` / `to_fiscal_year` | 关联（可空，首财年无 from） |
| `action` | `ADVANCE` / `END` / `START` |
| `status` | `RUNNING` / `SUCCEEDED` / `PARTIAL` / `FAILED` |
| `actor` | 发起人（`User` FK 或 id + 用户名快照） |
| `started_at` / `finished_at` / `elapsed_ms` | 时间与耗时 |
| `trigger_fy_end_ok` / `trigger_fy_start_ok` | 两段结果 |
| `written_count` / `failed_count` | 汇总 |
| **`details`（JSON）** | 逐公司：`{company_id, company_name, ok, fields:[{field_id, field_key, before, after}], error}` |
| `error` | 顶层错误摘要 |

- **写入明细就是 §11 需要的"不可否认账"**，同时解决"哪家公司失败看不到"（B-13）。
- 注意 JSON 体积：公司 × 字段可能上百项，建议**截断策略**（如失败明细全存，成功明细只存计数）+ 单独文件/表存全量（可选）。

**3.3 一个"推进"= 一次编排**

```
advance_fiscal_year:
  ① 前置校验：当前有且仅有一个 ACTIVE 财年（否则 409）
  ② 抢占：创建 FiscalYearRun(status=RUNNING) —— 用 DB 唯一约束保证同比赛同时只有一个 RUNNING
  ③ 结束旧财年：phase → SETTLING → 跑 FY_END 定时器 → phase → CLOSED；status → CLOSED 并 save()（触发既有广播）
  ④ 若 ③ 失败 → run.status=FAILED，保留 RUNNING 记录供重试；**不创建新财年**
  ⑤ 创建新财年（phase=PREPARING），跑 FY_START 定时器，成功后 phase → ACTIVE、status=ACTIVE
  ⑥ 若 ⑤ 的定时器部分失败 → run.status=PARTIAL 并开放"重试 FY_START"入口（幂等，安全）
  ⑦ 回写 run 结果，广播 fiscal-year:changed
```

> **跨"关闭旧年"与"开启新年"的事务边界**：**不要**把两次定时器包进一个长事务（SQLite 会长时间持有写锁，放大 B-7）。采用**编排 + 明确状态 + 幂等重试**（saga 风格），并把每步结果写进 `FiscalYearRun`。

**3.4 `CLOSED → ACTIVE` 拦截**

- 默认拒绝并返回 409，提示「已结束的财年不能重新开启；如需重置请使用显式 `force=true` 并知晓会重跑 FY_START 重置全场字段」。
- 若保留 `force` 路径，**必须**同时写 `FiscalYearRun`（action=`RESTART`）留下痕迹。

### 层 4 · 不阻塞全场（G5，可选但现场价值高）

问题根源：定时器在**请求线程内**跑完，而单进程 daphne 下所有同步视图共用该线程。

**方案 A（推荐，成本低）**：把"推进"拆成**两段式**
- `POST /advance` → 立刻返回 `202 + run_id`，后台线程执行（`threading.Thread` 或 `concurrent.futures`）；
- `GET /advance/{run_id}` → 前端轮询进度与结果。
- ⚠️ 后台线程必须**自己管理 DB 连接**（`django.db.connection.close()`），且**进程重启即丢失**——因此 `FiscalYearRun` 的 `RUNNING` 记录**必须**能被识别为"孤儿"并在启动时/下次推进时清理（否则会永久卡住 G1）。

**方案 B（更稳，成本高）**：引入 Celery/RQ + broker。当前仓库**本项目自身**无调度设施（搜 `celery`/`APScheduler` 零命中）；但注意并行工作流已通过 `manage.py snapshot_auto` + systemd timer/cron 引入**外部周期任务**模式，可参考同一套路（见 §11）。**赛前不建议为推进单独引入 broker。**

**方案 C（保守，零新设施）**：保持同步，但
- 把推进安排到**茶歇/午休**执行（现场 SOP 层面）；
- 把 nginx `proxy_read_timeout` 与前端 axios 超时对推进接口**单独放宽**（避免"界面失败、数据成功"）；
- 前端加**全屏遮罩 + loading 锁**，禁止二次点击。

> ⚠️ **无论选哪个方案，都必须先读 §11**：并行工作流的**快照写闸门**会拒绝 `PAUSED`/`RESTORING` 期间的推进请求，且同步长耗时的推进会**拖垮自动快照的排空等待**。

> **赛前最小组合建议：层 1 + 层 3.1/3.2 + 方案 A 或 C，并与快照系统对齐。** 层 2 视人力决定。

---

## 6. 详细改动点（逐文件）

### 6.1 `backend/apps/company_fields/timer.py`

| 改动 | 说明 |
| --- | --- |
| 锁键 `(competition_id, trigger)` | 见 §5 1.3 |
| `apply_fiscal_year_timer` 返回 `TimerResult` | 见 §5 1.4；**保留**函数签名向后兼容（新增返回值，不删参数） |
| 失败明细收集 | `_run_fiscal_year_timer` 把 `failed`/`skipped` 收集并返回；不再只 `logger.warning` |
| 广播修正 | **只对真正成功的公司**（或在明细里标注 ok=false）发 `emit_resource_changed`，避免"广播已更新但其实是混合状态"（B-5） |
| 事务边界 | 保持"每公司一个 `atomic()`"；**不要**提升为全场一个事务（SQLite 长写锁） |
| `_recompute_calc_fields` 位置 | 目前在 atomic 之外（`:245`）。若失败会留下"基础字段已写、计算字段未重算"的不一致 → **建议纳入同一 per-company 事务**，或至少在 `TimerResult` 里记录重算失败 |

### 6.2 `backend/apps/competitions/views.py`

| 改动 | 说明 |
| --- | --- |
| `FiscalYearCreateView.post` | ① 校验已有 ACTIVE → 409；② 捕获 `IntegrityError` → 409；③ **改为调用服务层**（或至少处理 `TimerResult.ran == False` → 409/202 而非 200） |
| `FiscalYearUpdateView.patch` | ① 状态机校验（拒 `CLOSED→ACTIVE`，除显式 force）；② 处理 `TimerResult`；③ 保留 `_broadcast_fiscal_year` |
| 新增 `FiscalYearAdvanceView`（可选） | `POST /api/competitions/:id/fiscal-years/advance` 一次点击完成"关旧+开新" |
| 新增 run 查询接口 | `GET /api/competitions/fiscal-years/runs/:id`（或列表）供前端展示进度/明细 |

### 6.3 `backend/apps/competitions/models.py`

- `FiscalYear`：加 `phase`；加 `UniqueConstraint(competition, condition=status="ACTIVE")`。
- 新模型 `FiscalYearRun`（见 §5 3.2）。
- **不要动** `save()` 里的 `_fy_prev_status` 逻辑与 `status` 字段。

### 6.4 `backend/apps/competitions/serializers.py`

- `year` 范围校验 + 年份口径注释。
- 输出 `phase`、`lastRun`（可选：最近一次推进的摘要）。

### 6.5 `backend/apps/realtime/emit.py`

- 新模型 `FiscalYearRun` 需在 `MODEL_TO_RESOURCE` 注册（本仓约定：新增业务模型必须注册；不需要广播则映射为 `None`）。

### 6.6 `frontend/src/views/competitions/CompetitionListView.vue`

| 改动 | 说明 |
| --- | --- |
| 合并为**一个**「推进财年」按钮（或保留两个但加串行锁） | 消除"两次手工点击"这一根因 |
| 加 `submitting` 锁 + 全屏 loading | 防连点（B-2） |
| 推进后**轮询 run 结果** | 展示"已重置 N 家公司 / M 家失败"（B-13） |
| `nextYear` 改为**由服务端决定** | 不再前端推算（B-10）；前端只发 `advance`，不回传 year |
| 文案修正 | 「不可撤销」与"实际可改回 ACTIVE"的矛盾（B-8）；推进期间的提示语 |

### 6.7 `frontend/src/stores/competition.ts`

- 顶部栏"当前财年"改为**优先取 `phase`/`lastRun` 状态**，避免列表中残留第二个 ACTIVE 造成误显示。

---

## 7. 实施注意事项（踩坑预警）

### 7.1 SQLite 与事务

- **`select_for_update()` 在 SQLite 上静默失效**（Django 特性守卫降级）→ 不能只靠行锁做互斥。**互斥必须落库**（`FiscalYearRun` 的 `RUNNING` 唯一约束）或依赖进程内锁 + 单进程假设。
- 长事务会长时间持有写锁 → **不要把两次定时器包进一个大事务**（否则全场写入排队，直接放大 B-7）。
- 建议同时给 SQLite 加 `OPTIONS: {"timeout": 20}`，缓解 `database is locked`（属相邻议题，但改 `DATABASES` 一行成本极低）。

### 7.2 年份口径必须先定

实测库里**同时存在** `year=0`（界面建的首个财年）与 `year=2026`（Excel/建包路径）。改造前必须与组委会确认：

- `0` 是否表示 **AI 财年**？
- 第 1–6 财年是 `1..6` 还是 `2027..2032`？
- **改口径会影响 `unique_together(competition, year)`、归档导入、Excel 建包与公告文案** → 建议**本议题只做"不回归"**（即保留既有口径、只禁止非法值），口径统一**另开一项**。

### 7.3 历史脏数据

加 `UniqueConstraint(competition, status="ACTIVE")` 前，必须先查并清理：

```sql
SELECT competition_id, COUNT(*) FROM fiscal_years WHERE status='ACTIVE' GROUP BY competition_id HAVING COUNT(*) > 1;
```

若存在多 ACTIVE，需人工决定保留哪一个（**保留年份最大者**与前端显示口径一致），其余置 CLOSED。**迁移脚本要做好回滚说明**。

### 7.4 不要破坏既有契约（回归红线）

| 契约 | 位置 | 要求 |
| --- | --- | --- |
| 财年信号语义 | `signals.py`、`tests_fix_verify/test_fy01_signals.py`（19 例） | `ACTIVE↔CLOSED` 迁移仍触发 `fiscal_year_started/ended`；**必须全绿** |
| Socket.IO 事件名 | `fiscal-year:changed` | 事件名与载荷（含 `competitionId`）不变，前端 `onRealtime` 依赖 |
| 增量轮询协议 | `GET /fiscal-years?updatedAfter=` | 不变（`contract_watcher` 与前端断线重连依赖） |
| 响应字段 | `id/competitionId/year/status/createdAt/updatedAt` | 只增不减 |
| 权限 | `competition:manage` | 不变（仍超管专属） |
| 定时器幂等性 | `timer.py` | 保持"SET 常量"语义，勿改成增量 |

### 7.5 关于"重试修复"

**可以**用"重试 FY_START"修复 B-3，因为定时器是 SET 常量、幂等。但要注意：
- 重试会**覆盖**期间玩家手填的同名字段（`version=None` 无条件写）；
- 因此重试入口应限量（仅当 `phase=PREPARING` 或 `run.status=PARTIAL` 时开放），并在前端明确提示"将重置这些字段"。

---

## 8. 测试计划（新增回归）

新建 `backend/tests_fix_verify/test_b_fiscal_year_advance.py`（沿用本仓 `tests_fix_verify` 约定：文件头 docstring 写「改前缺陷 / 改后行为 / 运行命令」）。

| # | 用例 | 断言 |
| --- | --- | --- |
| T1 | 已有 ACTIVE 时再 POST 创建财年 | **409**，且库中 ACTIVE 数量仍为 1（改前：200 或 500） |
| T2 | 并发/竞态创建（同比赛同 year 两次） | 第二次 **409**（不是 500 IntegrityError） |
| T3 | 「结束财年」后**立刻**「开始新财年」，且模拟 FY_END 仍在执行 | 新财年 FY_START **不丢**：`TimerResult.ran is True`，字段被正确重置（改前：静默跳过） |
| T4 | 定时器执行中再次触发同一 trigger | 返回 `ran=False`，且**调用方得到 409/202**（不是 200） |
| T5 | 某公司字段写入失败 | `run.status=PARTIAL`，**失败明细可查**（公司/字段/原因），且**不广播该公司"已更新"** |
| T6 | 一次推进的完整编排 | 旧财年 `CLOSED` + 新财年 `ACTIVE` + 两条 run 记录齐全；`FiscalYearRun.details` 可复原"哪些字段从 X 变成 Y" |
| T7 | `PATCH CLOSED → ACTIVE` | 默认 **409**；带 `force` 时成功且留下 `RESTART` 记录 |
| T8 | 非法输入 | `year=-1` / `year=99999` → 400 |
| T9 | **回归** | `tests_fix_verify/test_fy01_signals.py` 19 例全绿 |
| T10 | 幂等 | 连续两次执行同一 trigger 的定时器 → 字段终值一致，无重复副作用 |

**执行命令**：

```powershell
# 在 backend/ 目录下执行（注意：见 §9 的 cwd 说明）
.\.venv\Scripts\python.exe manage.py test tests_fix_verify.test_b_fiscal_year_advance tests_fix_verify.test_fy01_signals
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

---

## 9. 验收标准（Definition of Done）

- [ ] **B-1/B-2** 服务端拒绝第二个 ACTIVE；DB 层有唯一约束；竞态返回 409 而非 500
- [ ] **B-3/B-4** 锁按 trigger 分离；被跳过时**调用方可知**（非 200）
- [ ] **B-5** 失败明细可见；不再对失败公司广播"已更新"
- [ ] **B-6** 每次推进有 `FiscalYearRun` 记录，含逐公司明细与结果状态
- [ ] **B-7** 推进期间有其他请求可用（层 4）**或**至少前端不再出现"界面失败、数据成功"（超时对齐 + loading 锁）
- [ ] **B-8** `CLOSED→ACTIVE` 默认被拒
- [ ] **B-9** admin/脚本路径与 API 语义一致（统一服务层）
- [ ] **B-10** 前端不再自行推算 year
- [ ] **B-11** 非法 `year`/`status` 被拒
- [ ] **B-13** 界面能展示"重置了 N 家 / M 家失败"
- [ ] `manage.py check` 无问题；`makemigrations --check` 无差异；**`test_fy01_signals` 19 例全绿**；新增用例全绿
- [ ] 迁移在**含有历史脏数据**的库上可执行（或迁移前给出清理脚本）

---

## 10. 现场缓解措施（**代码未改之前的过渡方案**）

在改造落地前，现场必须按下列 SOP 操作，把 B 的影响压到最低：

```
【财年推进 SOP】固定 1 人、固定 1 台设备
① 主持人喊停，确认全场停止操作，等 30 秒
② 跑一次 DB 快照（记下文件名）
③ 点【结束财年】→ 等界面刷新（不要连点）
④ 看第二屏 gipfel.log：确认出现 FY_END，且**没有** "已在执行中，跳过本次并发触发"
⑤ 等 30 秒（等 FY_END 真正结束）
⑥ 点【开始新财年】
⑦ 校验：打开「设置→比赛准备总览」，抽 2 家公司 × 3 个字段确认已变化
⑧ 若未变化 → 查日志是否"跳过"；确认上一轮结束后，再 PATCH 一次 status（先 CLOSED 再 ACTIVE 可重触发，但会重置全场字段）
⑨ 主持人宣布新财年开始
```

- **时序**：安排在 **12:00–14:00 午休** 或 **16:00–16:30 / 17:15–17:45 茶歇**，不要在整点开赛时刻。
- **若出现两个「进行中」财年**：立刻把年份较小的那个 PATCH 成 CLOSED（**不要**点两次「结束财年」）。
- **第二块屏常开** `backend/logs/gipfel.log`，重点盯三类：`财年定时器`、`审计写入失败`、`database is locked`。

---

## 11. ⚠️ 与并行工作流的协调（**实施前必读**）

本文档撰写期间，仓库中已出现**另一条并行工作流：快照与回退系统**（`backend/apps/snapshots/`、`docs/SNAPSHOT_SYSTEM.md`、`frontend/src/system/`、`views/system/SnapshotManageView.vue`、`tests/snapshot_tools/`，并修改了 `settings.py` / `gipfel.service` / `gateway.py` / `socket.ts` 等）。它**与 B 的改造有直接交叉**，必须协调，否则会互相破坏。

### 11.1 已确认不影响本文档结论的部分

- `deploy/gipfel.service` **仍是单进程 daphne**（`ExecStart` 无 worker 参数）→ §2.1「单线程」结论成立；它只加了 `ReadWritePaths=…/backend/snapshots`。
- `settings.py` 的 `DATABASES` **仍是裸 SQLite**（无 `OPTIONS`、无 WAL、无 busy timeout）→ §7.1 的建议依然有效且**尚未被实现**。

### 11.2 三个必须协调的点

**(1) 全局写闸门会直接拒绝财年推进请求**

`apps/snapshots/middleware.py` 注册为全局中间件，在 `mode != RUNNING` 时：
- `mode == RESTORING` → **所有请求**都返回锁定；
- `mode == PAUSED` 且 **非安全方法** → 返回锁定（含 423）。

其 `EXEMPT_PREFIXES` 只放行 `/api/snapshots`、`/api/auth/`、`/api/version`、`/api/health`、`/socket.io/`、`/static/`、`/uploads/`、`/favicon.ico` —— **财年推进走 `/api/competitions/...`，不在豁免名单内。**

后果：联网的客户端在 `PAUSED`/`RESTORING` 期间点「推进财年」会被锁定拒绝。这是**期望行为**（快照/回退期间本来就不该写），但**实施者必须**：
- 在推进的前端流程里识别 `system_paused` / `system_restoring` 错误码，提示"系统正在快照，请稍候"而不是"推进失败"；
- 明确**"快照进行中不允许推进财年"**这条运维纪律（避免现场在自动快照触发时点推进）。

**(2) 推进耗时会拖住快照的"排空在途写请求"**

`snapshot_auto` 默认会把系统切到 `PAUSED` 并**等待在途写请求排空**（`SNAPSHOT_DRAIN_TIMEOUT`，默认 10 秒）以获得静止点。

而 B 的改造**目标之一就是让推进不再长时间占用请求线程**（§5 层 4）。若推进长时间同步运行：
- 自动快照（建议每 5–10 分钟）可能在排空阶段**超时失败**（数据不变，但快照做不出来）；
- 反复失败会导致**整场没有自动快照** —— 这恰好是赛务最需要的兜底。

**结论**：**层 4（把推进移出请求线程）的优先级因快照系统的引入而上升**。若暂不做层 4，则必须：
- 把 `snapshot_auto` 的调度**避开财年推进时点**（或推进期间暂停自动快照）；
- 或调大 `SNAPSHOT_DRAIN_TIMEOUT` 并接受快照失败风险。

**(3) 新模型要进快照/回退范围**

- `FiscalYearRun`（§5 层 3）与 `FiscalYear.phase` 都应纳入快照注册，否则**回退后推进记录与阶段状态会与数据不一致**。
  > 实测：`backend/apps/snapshots/registry.py` 中**未见 `FiscalYear`**（只在 `tests.py:101` 出现过），因此**实施者需自行确认财年表与新增的推进记录是否在快照范围内**。
- **回退后的"孤儿 RUNNING"**：若一次推进在 `RUNNING` 状态时系统回退，回退后的库里可能留下永久 `RUNNING` 记录 → **永久锁死 G1（单飞）**。因此**必须**实现 `RUNNING` 记录的失效判定（超时 TTL 或启动时清理），这与快照系统 `gate.py` 里对「回退中状态兜底 TTL」的处理思路一致，建议对齐同一套做法。
- `FiscalYearRun` 需在 `realtime/emit.py` 的 `MODEL_TO_RESOURCE` 注册（本仓约定）。

### 11.3 建议的协调动作

| 动作 | 负责 |
| --- | --- |
| 读 `docs/SNAPSHOT_SYSTEM.md`，确认 `PAUSED`/`RESTORING` 期间的完整锁定语义与错误码 | B 的实施者 |
| 决定"快照进行中禁止推进财年"的纪律，并写进现场 SOP | 赛务 |
| 确认 `FiscalYear` / `FiscalYearRun` 是否在快照注册表内，不在则加入 | B 的实施者 |
| 对齐"孤儿 RUNNING 的 TTL 失效"与 `gate.py` 的兜底 TTL 实现 | B 的实施者 |
| 若层 4 采用后台线程：确认它不与快照的 `write_permit()` 计数冲突（后台线程的写不会被中间件计入，可能破坏"静止点"保证） | B 的实施者 |

> **一句话**：B 的层 4 与快照闸门都在动"写请求"，**两者必须在同一份设计里对齐**，否则会出现"快照永远做不出来"或"回退后财年卡死"。

---

## 12. 建议实施顺序

| 阶段 | 内容 | 预估 |
| --- | --- | --- |
| **第一批（必做）** | 层 1（ACTIVE 唯一性 + 输入校验 + 锁分离 + TimerResult） | 0.5 人日 |
| **第二批（必做）** | 层 3.2（`FiscalYearRun` 记录 + 明细）+ 视图回写 | 1 人日 |
| **第三批（建议）** | 层 2（统一服务层）+ 层 3.1（`phase` 状态机）+ `CLOSED→ACTIVE` 拦截 | 1 人日 |
| **第四批（可选）** | 层 4 方案 A（后台执行 + 轮询）或方案 C（保守） | 0.5–1 人日 |
| **配套** | 前端合并按钮 + loading + 进度展示 | 0.5 人日 |

---

## 附 A · 关键代码片段（改前原文）

```python
# backend/apps/company_fields/timer.py:60-62, 147-167
_fiscal_locks: dict[int, threading.Lock] = {}
_fiscal_locks_guard = threading.Lock()

def apply_fiscal_year_timer(competition_id: int, trigger: str) -> None:
    with _fiscal_locks_guard:
        lock = _fiscal_locks.get(competition_id)
        if lock is None:
            lock = threading.Lock()
            _fiscal_locks[competition_id] = lock
    if not lock.acquire(blocking=False):
        logger.warning(
            "财年定时器：比赛 #%s 的 %s 已在执行中，跳过本次并发触发", competition_id, trigger
        )
        return                                   # ← 静默丢弃
    try:
        _run_fiscal_year_timer(competition_id, trigger)
    finally:
        lock.release()
```

```python
# backend/apps/competitions/views.py:206-228（创建财年，无 ACTIVE 唯一性校验）
if FiscalYear.objects.filter(competition=comp, year=year).exists():
    raise BusinessError("该财年已存在", code=409, status_code=409)
fy = FiscalYear.objects.create(competition=comp, year=year,
                              status=serializer.validated_data.get("status", "ACTIVE"))
_broadcast_fiscal_year(fy)
_apply_fiscal_year_timer(comp.id, "FY_START")     # ← 同步阻塞请求线程
return Response(FiscalYearSerializer(fy).data)
```

```python
# backend/apps/competitions/views.py:255-261（状态迁移推导 trigger）
new_status = fy.status
if prev_status != "ACTIVE" and new_status == "ACTIVE":
    _apply_fiscal_year_timer(fy.competition_id, "FY_START")
elif prev_status != "CLOSED" and new_status == "CLOSED":
    _apply_fiscal_year_timer(fy.competition_id, "FY_END")
```

## 附 B · 可复现的现状证据（供实施者自查）

```powershell
# 1) 单进程部署（无线程池参数）
Select-String -Path deploy\gipfel.service -Pattern 'ExecStart|Restart='

# 2) SQLite 无 OPTIONS（无 WAL / 无 busy timeout）
Select-String -Path backend\backend\settings.py -Pattern 'DATABASES' -Context 0,5

# 3) 定时器只在两个视图被调用
Select-String -Path backend\apps\competitions\views.py -Pattern '_apply_fiscal_year_timer'
Select-String -Path backend\apps\competitions\signals.py -Pattern '_apply_fiscal_year_timer'   # 期望：无命中

# 4) 定时器字段与合同落账在真实库里从未跑过（只读 ORM）
#    实测：timer_enabled 字段 = 0，contract_field_effects = 0

# 5) 年份口径两套并存（只读 ORM）
#    实测：comp=4 year=0；comp=189 year=2026
```

---

## 附 C · 本文档的边界

- **未实测**：财年定时器的**实际耗时未做压测**（需写库）。"阻塞全场"的强度是**结构性推断**（同步调用 + 单进程 + 全部同步视图共用线程 + 全公司×全字段写入 + 逐公司计算图重算），**实施前/彩排时应在真实数据上实测一次并记录公司数与耗时**。
- **未验证**：本文档中"层 4 方案 A 的后台线程 + 孤儿 RUNNING 清理"的具体实现未做原型，实施者需自行处理 Django 连接生命周期与进程重启后的状态恢复。
- **依赖外部裁定**：§7.2 的年份口径需与组委会/现有建包数据对齐后才能确定 `year` 的校验范围。
