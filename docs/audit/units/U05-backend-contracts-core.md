# U05 backend contracts core（分支归属：master 基线）

## 概述

**审计范围（严格）**：`backend/apps/contracts/` 顶层文件：

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `engine.py` | 2419 | 合同 DSL 引擎（求值 / 效果落账 / 复原 / 预检 / 公式求值器） |
| `views.py` | 869 | 合同类型 + 合同实例 API（含执行、终止、试算、编号补全） |
| `serializers.py` | 242 | ContractType / Contract 序列化器 |
| `models.py` | 115 | ContractType / Contract / ContractFieldEffect |
| `urls.py` / `apps.py` / `admin.py` | 50 / 7 / 14 | 路由、AppConfig、admin 注册（无 `signals.py`，广播走 `apps/common/signals.py` + `apps/realtime/emit.py`） |

**明确未审计**：`builder/**`（B04/B05）、`tests/**`、`management/**`（B05）、`apps/preparation/**`。
`tests/` 仅作为生产代码缺陷的旁证引用（见 K-04）。
当前分支 `feature/contract-watcher` 与 master 相比未改动上述顶层文件（`git diff --stat master...feature/contract-watcher -- backend/apps/contracts` 只列出 builder/management/tests），故本报告结论归属 master 基线。

**方法与约束**：全程 `read`/`grep` 静态阅读，未运行任何 Django 命令、未连接数据库、未改动任何代码。
运行时依赖（`apps.companies.models.CompanyFieldValue`、`apps.common.guards`、`apps.common.sync`、`apps/realtime/emit`、权限目录 `apps/common/permissions.py`）作为交叉验证读取。
部署环境：`backend/backend/settings.py:305-310` 为 SQLite（未配置 WAL / `transaction_mode` / `select_for_update`），并发结论均以该前提表述。

**缺陷统计**：P0 ×1、P1 ×3、P2 ×9、P3 ×4，共 17 条（K-01 ~ K-17）。

**风险主线**：合同效果的落账目标是「合同自带的 `parties` JSON 中 role→companyId 的绑定」，而该绑定从创建到执行**没有任何服务端归属校验**（K-01）；配合创建时即可一次性代填全部会签编号自动进入待执行（K-02），以及执行时客户端可任意覆盖 `inputs` 绕过 `inputSchema` 约束（K-03），构成「以他人公司身份提交并执行合同」的完整链路。数值/字符串侧的主线是**静默取 0**（K-04/K-06/K-14）与**静默不写入**（K-09），均不报错、合同照样置为 EXECUTED。

---

## 缺陷清单

### [P0] K-01 合同参与方公司未做归属校验：落账目标公司由请求方任意指定（跨公司 / 跨比赛越权改写产业字段）

- 位置：`backend/apps/contracts/engine.py:2243`
- 代码：

```python
    def _resolve_party_company(self, role, party_map):
        p = party_map.get(role)
        if not p or p.get("isHost") or p.get("companyId") is None:
            return None
        return p
```

- 触发条件：
  1. `parties` 的公司绑定完全由客户端提供且不被校验。`serializers.py:211-223` 的 `validate_parties` 只检查「至少一个非主办方参与方」，`companyId` 既不做类型校验、也不校验是否属于 `competitionId`、更不校验创建者是否对该公司有管理范围；`views.py:222-225` 只校验合同归属比赛（`cid == user.competition_id`），完全不看参与方公司。
  2. 落账时引擎直接按该绑定取目标公司，无任何比赛/权限过滤：`engine.py:1889` → `engine.py:1897` `self._resolve_industry_field(party["companyId"], ...)` → `engine.py:2266` `Company.objects.filter(pk=company_id)`（无 `competition_id` 条件）→ `engine.py:1902-1911` 读改写 `CompanyFieldValue`。
  3. 执行门槛只看「**最后一个**非主办方参与方公司是否在执行者 `companyScopes` 内」：`views.py:793-800`（`_get_last_signatory_company_id` 取 `real[-1]`）+ `views.py:821-825`。攻击者把自己的公司放在 parties 末位即可通过，而被效果命中的 role 可以绑定到任意受害公司（`engine.py:1886-1897` 按 `eff["party"]` 定位，与末位无关）。
  4. 账号前提：`contract:manage`（rank 40）在 `has_permission` 同域蕴含下同时满足 `contract:audit`(20)/`contract:execute`(30)（`apps/common/permissions.py:151/253-289`，`CONTRACT_ACTION_RANKS`），即 `ROLE_TEMPLATES["COMPETITION_ADMIN"]` 默认权限组合（`apps/common/permissions.py:341-347`）即可。

  最小 PoC：`POST /api/contracts {"competitionId":<自己比赛>,"contractTypeId":<采购合同>,"parties":[{"role":"seller","companyId":<受害公司>},{"role":"buyer","companyId":<自己公司>}],"inputs":{...}}` → `POST /api/contracts/:id/execute`。

- 后果：
  - 以他人公司身份落账：受害公司的现金/库存/其它产业字段被 SUB/SET 改写，且 `ContractFieldEffect` 记在攻击者自己的合同上（`engine.py:1923-1933`），攻击者可通过终止/删除合同再次触发复原（`views.py:569-576`、`views.py:270-275`）反复拨动他人数据。
  - 跨比赛越权写：`Company.objects.filter(pk=company_id)` 与 `CompanyFieldValue` 均无比赛约束，`companyId` 为自增整数，A 比赛的管理员可写入 B 比赛公司的字段（租户隔离失效）。
  - 会签模型（`_assert_execute_scope` / `_assert_edit_party_scope` 存在的唯一目的）被完全绕过：执行者无需是效果所涉公司的管理者。

- 修复建议：
  1. `serializers.py:validate_parties` 中校验每个非主办方 `companyId` 非空、为整数、公司存在且 `company.competition_id == competitionId`，并与 `contractType.partyRoles` 的 role 白名单做集合相等校验（拒绝自造 role）。
  2. 创建时校验调用者对所绑定公司具备权限（至少 `company_scopes_list` 覆盖，或为超管）。
  3. 引擎落账前做纵深防御：`_resolve_party_company` 后断言 `Company.competition_id == contract["competition_id"]`；`_write_field_value`/`_read_current_field_value` 建议改为按 `(company_id, competition_id)` 双键查 `CompanyFieldValue`。

---

### [P1] K-02 会签编号可由创建者一次性代填，合同被自动置为待执行，绕过「各方各自签署」

- 位置：`backend/apps/contracts/views.py:231`
- 代码：

```python
        selectable = [p for p in parties if isinstance(p, dict) and not p.get("isHost")]
        all_filled = bool(selectable) and all(
            p.get("contractNumber") is not None and str(p["contractNumber"]).strip() != ""
            for p in selectable
        )
        if all_filled and contract.status == "DRAFT":
            contract.status = "PENDING_EXEC"
            contract.save(update_fields=["status", "updated_at"])
```

- 触发条件：
  1. 创建路径：`POST /api/contracts` 的 `parties` 里把所有参与方（含受害方）的 `contractNumber` 一并填好，服务端不校验「谁有权填哪一方的编号」，创建即 `PENDING_EXEC`（上方代码），随后直接执行。前端 UI 的意图恰恰相反——`frontend/src/views/data-management/ContractManageView.vue:1443-1444` 注释写明「发起方仅填自己管理的那一方；未填的编号留空（null），后续由各公司管理员在详情页补全」，即该规则只存在于前端。
  2. 补全路径：`PATCH /api/contracts/:id/party-numbers` 虽有 `_assert_edit_party_scope`（`views.py:417`），但该函数对持 `contract:execute` 或 `contract:manage` 的账号**直接 return**（`views.py:848-851`），于是同一批账号可一次性改完所有参与方编号。
  3. 执行门槛只检查编号非空（`views.py:308-317`），不检查编号由谁填写（`Contract`/`ContractFieldEffect` 均无「填写人」字段，`models.py:43-115`）。

- 后果：任何持 `contract:manage` 的账号可单方面让合同进入待执行并执行，另一方公司没有任何确认动作；「会签」在服务端不成立（与 K-01 组合即为完整攻击链）。合同编号同时失去作为签署凭据的证据价值。

- 修复建议：创建时只允许调用者为自己管理范围内的公司填写编号，其余强制置 `None`；`_assert_edit_party_scope` 去掉 `can_execute/can_manage` 的早退（或至少要求 `company_id in company_scopes_list`）；为编号补全增加「填写人/时间」审计（如新增 `ContractPartySign` 表）。

---

### [P1] K-03 执行接口接受客户端任意 `inputs` 覆盖，服务端完全不按 `inputSchema` 校验（required/min/max/enum/type）

- 位置：`backend/apps/contracts/views.py:319`
- 代码：

```python
        # 执行时可覆盖输入参数
        inputs_override = request.data.get("inputs") if isinstance(request.data, dict) else None
        inputs_raw = (
            json.dumps(inputs_override, ensure_ascii=False) if inputs_override is not None
            else contract.inputs
        )
```

- 触发条件：`POST /api/contracts/:id/execute {"inputs": {"quantity": -100, "price": -10}}`。引擎内对 `inputSchema` 的唯一校验是两张白名单（`engine.py:1856-1864`、`engine.py:2046-2053`）：

```python
        infra_err = self._validate_list_filters(input_schema, inputs, "infrastructureList", "allowedInfrastructures", "基建")
        veh_err = self._validate_list_filters(input_schema, inputs, "vehicleList", "allowedVehicles", "载具")
```

  `models.py:23` 声明的 `{key,label,type,required?,default?,enum?,min?,max?}` 中，除上述两个清单白名单外，`required/min/max/enum/type` 在 `backend/apps/contracts/*.py` 中**没有任何消费点**（全仓 grep 仅 builder/validate.py 在构建期使用，属 B04/B05 范围）；`precheck` 同样不校验。

- 后果：
  - 负数量/负单价直接进入落账：`engine.py:441-448` 的 `SUB` 变成「减负数 = 加钱」，买方现金增加、卖方库存增加——金额方向反转，而所有前置检查（多为 `FIELD_COMPARE` 库存/现金 ≥ 阈值）仍可通过。
  - 超范围值（100 倍数量、超过 100% 的费率、负数单价）无上限拦截，合同一执行即造成不可逆的数值破坏（复原仅能回退本合同记录的增量，玩家已据此做出的其它操作无法追回）。
  - `required` 项缺失不会报错，退化为 K-04 的静默 0。

- 修复建议：在执行与预检入口按 `inputSchema` 做完整服务端校验（`type/required/enum/min/max` + 数值有限性与符号策略：金额/数量 ≥ 0、比例 ∈ [0,1]），校验失败返回 400 并中止事务；把 `default` 补齐逻辑从「仅试算接口」下沉到统一入口。

---

### [P1] K-04 缺失 / 拼错 INPUT 键与未定义公式变量静默取 0：`SET` 效果会把字段直接写成 0

- 位置：`backend/apps/contracts/engine.py:1774`
- 代码：

```python
        if aggregate == "TECH_RESEARCH_COST":
            return compute_tech_research_cost(raw, ctx.competition_id if ctx else None)
        return spec.get("default") if raw is None else raw
```

- 触发条件：
  1. 输入端：`eval_value_spec` 的 `INPUT` 分支（`engine.py:1712-1774`）在 `inputs` 缺键时返回 `spec.get("default")`，未配 `default` 即得到 `None`；引擎不会套用 `inputSchema` 的 `default`（前端因此把「仅提交可见输入项」当作规避手段：`frontend/src/views/data-management/ContractManageView.vue:1448-1452`，挂在未命中 IF 分支下的字段根本不入库）。
  2. 落账端：`None` 被 `to_number` 变成 0（`engine.py:441-448`）：

```python
            n_before = to_number(before)
            n_val = to_number(new_value)
            if op == "SET":
                after = n_val
            elif op == "SUB":
                after = n_before - n_val
            else:  # ADD
                after = n_before + n_val
```

  3. 公式端：`engine.py:905` `return 0  # 未定义变量回退为 0`（变量名拼错、大小写不一致、`scope` 未注入时静默 0）。
  4. 该行为在项目内已被记录为「已知静默错误」——`backend/apps/contracts/tests/test_complex_contracts.py:225-227`：「否则未提供的输入项会被 to_number(None) 变成 0，表现为「乘费率的效果恒为 0」这类静默错误」（引用测试作为旁证，未审计测试代码本身）。

- 后果：
  - `op=SET` 且值源解析失败 → 字段被**清零**（如公司现金、库存被写入 0），合同仍置 `EXECUTED`，`execution_log` 显示成功；这是不可逆的数据损坏，且用户完全没有察觉途径。
  - `op=ADD/SUB` → 效果静默无操作：看似成交，实际未扣款/未交货，双方账目与实际不符。
  - 公式里拼错变量名得到 0，会让「金额 = 单价 × 数量」这类公式推出 0 元合同。

- 修复建议：`INPUT` 缺键且无默认值时抛 `BusinessError`（不要返回 `None`）；`eval_value_spec` 顶层对 `None` 结果做显式拒绝（或要求效果声明 `allowEmpty`）；`_FormulaParser._parse_primary` 遇到不在 `scope`/内置常量中的标识符直接报错，而不是回退 0；`apply_field_effect` 对 `SET` 且 `new_value is None` 直接抛错。

---

### [P2] K-05 同一次执行内 `FIELD` 读取走预加载缓存，写入后不失效 → 读到执行前的旧值

- 位置：`backend/apps/contracts/engine.py:1670`
- 代码：

```python
    if ctx.field_cache is not None:
        hit = ctx.field_cache.get(f"{party['companyId']}:{field_key}")
        if hit:
            return parse_stored_field_value(hit.get("value") or hit.get("defaultValue"), hit.get("fieldType"))
```

- 触发条件：一次执行包含 ≥2 个效果，且后一个效果的**值源**用 `{"type":"FIELD","party":..,"fieldKey":..}` 读取前一个效果已改写的同一字段。缓存由 `engine.py:1873` 的 `_preload_field_cache(parties, ctx)` 在执行前一次性构建，而写入路径 `engine.py:1905-1911` → `_write_field_value`（`engine.py:2281-2326`）只更新数据库，**从不回写 `ctx.field_cache`**；`read_company_field_value` 优先命中缓存，因此返回执行前的快照。注意同一字段的连续 ADD/SUB 不受影响（`apply_leaf` 走 `_read_current_field_value` 实时读库，`engine.py:1902`），受影响的是「读某字段算另一个效果」的组合。
- 后果：例如 effect1「卖方库存 SUB 数量」→ effect2「违约金 = FIELD(卖方, 库存) × 0.1」拿到**扣减前**库存，金额偏大（或偏小），落账无任何提示，审计日志里也看不出取的是旧值。
- 修复建议：`_write_field_value` 成功后同步更新 `ctx.field_cache[f"{cid}:{field_key}"]`（值 + fieldType），或让 `FIELD` 求值绕过缓存直读数据库（同一事务内可见自己的写入）。

---

### [P2] K-06 聚合端点对不存在的业务对象名静默按 0 计算（碳排 / 价格 / 配比）

- 位置：`backend/apps/contracts/engine.py:1246`
- 代码：

```python
    total = 0
    for name, q in entries:
        if location_node_id is not None and name in loc_prices:
            price = loc_prices[name]
        else:
            price = avg_prices.get(name, 0)
        total += price * to_number(q)
    return total
```

- 触发条件：清单输入的 key（原料/零件/产品/载具/仓库/基建名称）在本比赛数据中不存在——拼写差异、前后空格、繁体/emoji/全角字符、或该数据已被删除。同一模式遍布聚合端点：`engine.py:1213`（`coeff.get(name, 0)` 碳排系数）、`engine.py:1242`（无任何地点价则不进 `avg_prices`）、`engine.py:1280-1286`（`if not part: continue`）、`engine.py:1316-1322`、`engine.py:1341-1342`、`_compute_named_field_aggregate`（`engine.py:1375` `val.get(name, 0)`）、`engine.py:1419-1426`。
- 后果：「原料清单碳排放 = 0」「总价 = 0」被当作真实结果参与前置检查与落账：`碳排 ≤ 上限`、`总金额 ≤ 现金`之类条件误判通过；以 0 金额成交或按 0 碳排通过环保检查，且 `execution_result` 中只有数字、没有「名称未匹配」的痕迹。
- 修复建议：`_entries` 解析出的名称在批量查询后做差集，未匹配名称收集为失败检查（或至少进 `result["logs"]` 的 warnings），让 `precheck` 显式提示「清单中的 X 不存在」；价格缺失时按业务要求报错而非按 0。

---

### [P2] K-07 DSL 结构缺字段 / 类型不符 → 未捕获的 KeyError / IndexError / AttributeError → 500

- 位置：`backend/apps/contracts/engine.py:1897`
- 代码：

```python
                field = self._resolve_industry_field(party["companyId"], eff["fieldKey"], ctx)
                new_value = resolve_value(eff.get("value"), sc)
                if eff.get("value2"):
                    v2 = resolve_value(eff.get("value2"), sc)
                    new_value = combine_values(new_value, v2, eff.get("valueOp") or "ADD", field["field_type"])
                current = self._read_current_field_value(party["companyId"], field["id"])
                config = parse_field_config(field.get("config"))
                applied = apply_field_effect(current, field["field_type"], config, eff["op"], new_value)
```

- 触发条件：`effects`/`conditions`/`inputSchema` 由 `ContractTypeSerializer` 以裸 `JSONField` 接收后原样入库（`serializers.py:61-64`、`serializers.py:118-120`），无结构校验。因此可写入：
  - effect 缺 `fieldKey`（→ `KeyError`）或缺 `op`（→ 第 1904 行 `eff["op"]` `KeyError`）；
  - conditions 元素为 `null` 或缺 `kind`（`engine.py:2070-2071` `c.get("label") or cond_kind_label(c["kind"])`、`engine.py:2086` `c["kind"]` → `AttributeError`/`KeyError`）；
  - `OP` 参数个数不足（`engine.py:989-991` `a[0]`/`a[1]` → `IndexError`，如 `{"type":"OP","op":"ADD","args":[...]}` 只给 1 个参数）。
- 后果：该合同类型下**所有**合同的执行与预检稳定 500（事务回滚，无脏数据，但功能整体不可用）；生产 `DEBUG=False` 时前端只看到「服务器内部错误」，无法定位到是哪条 effect 写错。属于配置面 DoS。
- 修复建议：`ContractTypeSerializer.validate_effects/validate_conditions/validate_inputSchema` 做递归结构校验（可直接复用 `builder/validate.py` 的校验器）；引擎内所有 `eff[...]`/`c[...]`/`a[...]` 改为 `.get()` + 明确 `BusinessError("effect[i] 缺少 op")`。

---

### [P2] K-08 `avg` / `^` / `**` / `pow` 产生 float，破坏「引擎内绝不产生 float、支持 10^23」的约定

- 位置：`backend/apps/contracts/engine.py:835`
- 代码：

```python
    def _parse_power(self):
        base = self._parse_unary()
        if self._peek()[0] == "op" and self._peek()[1] in ("^", "**"):
            self._next()
            exp = self._parse_power()  # 右结合
            return math.pow(_f_to_num(base), _f_to_num(exp))
        return base
```

- 触发条件：
  - 公式中的幂运算：`10^23`、`pow(2,60)`、`1.05^80` 等一律经 `math.pow` → C double；`engine.py:722` 的 `pow` 内建同理。
  - `avg([...])` 不是超越函数却用 Python 真除：`engine.py:743` `(sum(_f_to_num(x) for x in arr) / len(arr))`，`int/int` → float。
  - 该模块开头 `engine.py:31-42` 明确承诺「引擎内部数值 = int | Decimal」「float 不再在引擎内产生/传播（仅 EXP/LOG 等超越函数局部使用）」，「10^23 量级」是设计目标。
- 后果：一旦进入 float，2^53 以上精度立即丢失（10^23 的 ULP ≈ 10^7）：`avg([10**17+1, 10**17+3])` → `1.0000000000000002e+17`；`10^23` → `9.999999999999999e+22`。误差随 `SET/ADD` 直接写进公司字段与审计行（`dumps_engine_json` 对 float 走 `json.dumps` 原样输出），后续所有复算基于错误基数，且不再是 Decimal，无法无损回读。
- 修复建议：`avg` 改用 `_f_div(sum, len)`；`_parse_power` 在指数为整数（`isinstance(exp,int)` 或 Decimal 整数值）且底数为 int/Decimal 时用 Decimal 幂（`Decimal(base) ** int(exp)`），仅对非整数指数保留 `math.pow`，并在文档中标注精度边界。

---

### [P2] K-09 乐观锁二次冲突后「跳过写入」但仍写审计行并把合同置 EXECUTED → 审计与真实值永久不一致

- 位置：`backend/apps/contracts/engine.py:2319`
- 代码：

```python
        updated = CompanyFieldValue.objects.filter(pk=fv.pk, version=fv.version).update(
            value=store_value, version=fv.version + 1
        )
        if not updated:
            # 极端情况下仍冲突：记录告警并跳过本次写入，避免静默覆盖也不阻断整批落账
            logger.warning(
                "[contracts] _write_field_value 乐观锁冲突重试失败 company=%s field=%s",
                company_id, industry_field_id,
            )
        return fv
```

- 触发条件：目标 `CompanyFieldValue` 行的 `version` 在「首次读取 → 首次 UPDATE」以及「重读 → 重试 UPDATE」之间各被推进一次（并发的手动编辑、另一份合同落账）。重试失败分支**不抛错、不回滚**：`engine.py:1905-1933` 仍会 `effect_rows.append(...)`（用旧快照算出的 `before/after`）、写 `execution_log`，`views.py:333-352` 仍把合同置为 `EXECUTED`。
- 后果：数据库里字段值**没变**，但 `ContractFieldEffect`/`execution_log` 记录「已改写」，`result["fields"]` 也返回改写后的值；随后终止/删除该合同时按这些错误记录重放（`engine.py:2033-2037`），把字段推到一个从未真实存在过的值。观测口径差异：SQLite（当前部署，`settings.py:305-310`，未开 WAL）下并发写通常先在锁层面失败为 `database is locked`（500，事务回滚，不会静默）；MySQL REPEATABLE READ 下重读仍返回快照，该静默分支可达 → **[待确认]** 与生产库选型相关。
- 修复建议：重试仍失败时抛 `BusinessError` 让整个执行事务回滚（宁可让用户重试，也不要产生「已执行」的假账）；若确实要降级，则必须同时回滚审计行与合同状态，并在 `execution_result` 里显式标记「字段 X 未落账」。

---

### [P2] K-10 合同「终止」缺少执行方范围校验，且回滚后直接删除效果行

- 位置：`backend/apps/contracts/views.py:569`
- 代码：

```python
        effect_count = ContractFieldEffect.objects.filter(contract_id=contract.id).count()
        with transaction.atomic():
            if effect_count > 0:
                engine_dict = _contract_to_engine_dict(contract)
                _engine.revert_contract(engine_dict)
                contract.status = status
                contract.save(update_fields=["status", "updated_at"])
                ContractFieldEffect.objects.filter(contract_id=contract.id).delete()
```

- 触发条件：`PATCH /api/contracts/:id/status {"status":"TERMINATED"}` 只需 `contract:manage`（`views.py:556`），**没有** `_assert_execute_scope`（对比执行路径 `views.py:305` / `views.py:803-825`），也不做任何公司范围判定（`_get_contract` 只校验比赛域，`views.py:621-630`）。
- 后果：持 `contract:manage` 且 `companyScopes` 与合同参与公司**无交集**的账号（例如只分管其他公司的比赛管理员）可以单方面终止并**回滚**他人公司已落账的字段效果（现金/库存被改回），同时第 576 行删掉全部 `ContractFieldEffect`——复原依据随之消失，被终止方无法再自查或恢复。写权限口径不一致：能执行需「末位公司在范围内」，能终止却不需要。
- 修复建议：终止复用 `_assert_execute_scope`（或要求操作者对合同参与公司具备管理范围）；终止原因/操作者写入审计；删除效果行前保留只读归档（如置 `reverted_at` 而不是删行）。

---

### [P2] K-11 合同详情 / 影响接口对操作类账号不做范围过滤，与列表口径不一致（跨公司读）

- 位置：`backend/apps/contracts/views.py:832`
- 代码：

```python
    can_execute = has_permission(user.role, user.permissions_list, "contract:execute")
    can_audit = has_permission(user.role, user.permissions_list, _CONTRACT_AUDIT_PERM)
    can_manage = has_permission(user.role, user.permissions_list, _CONTRACT_MANAGE_PERM)
    if can_execute or can_audit or can_manage:
        return
    scopes = user.contract_view_company_scopes_list
    if not scopes:
        return  # 未配置范围 = 不限制
```

- 触发条件：`GET /api/contracts/:id`（`views.py:249-254`）与 `GET /api/contracts/:id/impact`（`views.py:600-603`）对持 `contract:audit/execute/manage` 的账号直接放行；而列表接口对同一批账号是按 `companyScopes` 过滤的（`views.py:772-777`，`_filter_by_scope`：空范围 = 看不到任何合同）。于是「列表里看不见」的合同，只要猜/枚举 id 就能读到详情。
- 后果：跨公司越权读——`inputs`（成交价、数量、成本）、`executionLog`/`executionResult`（含各公司落账前后值）、`parties` 公司名等经营数据对分管范围外的管理员全部可见；`/impact` 还泄露效果行数量。
- 修复建议：详情/影响接口对操作类账号也按 `companyScopes` 判定合同可见性（复用 `_filter_by_scope` 或 `_parties_list_in_scopes`），使读口径与写口径一致。

---

### [P2] K-12 参与方 `companyId` 无类型校验 → 一条脏合同即让整比赛合同列表 500（含 contract-watcher 轮询）

- 位置：`backend/apps/contracts/views.py:668`
- 代码：

```python
    for it in items:
        parties = it.get("parties") if isinstance(it.get("parties"), list) else []
        for p in parties:
            if isinstance(p, dict) and p.get("companyId") is not None:
                company_ids.add(int(p["companyId"]))
```

- 触发条件：`POST /api/contracts` 时 `parties:[{"role":"x","companyId":"abc","contractNumber":"1"}]`（`validate_parties` 不校验类型，`serializers.py:211-223`）。此后任意用户请求 `GET /api/contracts`（非增量分支或内存过滤后分页，`views.py:201` / `views.py:214`）会执行 `int("abc")` → `ValueError` → 未捕获 → 500。执行该合同时另有一处：`engine.py:2340-2343` `Company.objects.filter(id__in=["abc"])` → `ValueError` → 500（`_preload_field_cache` 的 `try` 只包住两处 import，`engine.py:2334-2339`）。
- 后果：**该比赛所有账号的合同列表持续 500**（不是只坏那一条数据），前端合同页整体不可用；`contract_watcher` 依赖 `GET /api/contracts?status=EXECUTED` 轮询（`contract_watcher/contract_watcher.py:129-140`），会同步失效——即监听程序静默不工作。另注：float 型 `companyId`（如 `3.5`）会被 `int()` 静默截断为 `3`，用于范围判定（`views.py:699`、`views.py:727`）后会指向另一家公司。
- 修复建议：`validate_parties` 强制 `companyId` 为 `int` 或 `null`；`_enrich_party_companies`/`_get_party_company_ids` 用容错转换并跳过非法值；配合 K-01 的存在性/归属校验从根上杜绝脏数据。

---

### [P2] K-13 执行与终止/删除之间无行锁（全仓无 `select_for_update`），存在状态错乱竞态

- 位置：`backend/apps/contracts/views.py:346`
- 代码：

```python
            if rows == 0:
                latest = Contract.objects.get(pk=pk)
                if latest.status == "EXECUTED":
                    raise BusinessError("合同已执行，不可重复执行", code=400, status_code=400)
                raise BusinessError("合同已终止，不可再次执行", code=400, status_code=400)
            # 抢占成功：仅此一处执行引擎副作用，保证落账副作用只发生一次
            engine_result = _engine.execute(engine_dict)
```

- 触发条件：`backend/apps/contracts/` 全目录 grep `select_for_update` 零命中；终止/删除路径的判定条件也取自事务外的 `count()`（`views.py:569`、`views.py:270`）。时序：
  1. T1 执行：抢占式 `UPDATE ... status='EXECUTED'`（`views.py:334-345`）成功但尚未提交，引擎正在写 `CompanyFieldValue`/效果行；
  2. T2 终止：`ContractFieldEffect.objects.filter(...).count()` 读到 0（读不到 T1 未提交的插入）→ 走 `else` 分支仅改状态为 `TERMINATED` 并提交；
  3. T1 继续写完效果行并提交。

  最终：合同 `status=EXECUTED`、效果已落账，「终止」请求返回 200 却完全无效。反向交错则得到 `status=TERMINATED` 且效果行仍在且未被清理（要等超管删除合同时才回滚，`views.py:270-275`）。
- 后果：状态与账目不一致——显示已终止但资金/库存已变动，或显示已执行但实际被终止；审计上无法解释，且用户看到的是成功响应。SQLite 单写者会使上述交错多表现为 `database is locked` 500（长事务同时在跑碳排/价格/Dijkstra 等多条查询），但语义缺陷与库无关。
- 修复建议：`execute`/`status`/`delete` 三处对 `Contract` 行使用 `select_for_update()`（SQLite 下依赖写事务串行，其它库下靠行锁）串行化；把 `effect_count` 判定移入同一事务并锁行；必要时对 `(company_id, industry_field_id)` 的 `CompanyFieldValue` 也用 `select_for_update` + 自增 version 替代「重试一次」策略。

---

### [P3] K-14 条件算子非法时静默按 ≥（GTE）处理

- 位置：`backend/apps/contracts/engine.py:290`
- 代码：

```python
    if op == "EQ":
        return actual == expected
    if op == "LTE":
        return actual <= expected
    return actual >= expected  # GTE / default
```

- 触发条件：`FIELD_COMPARE` 数值分支 `engine.py:2211` `passed = compare_op(actual, c.get("op") or "GTE", expected)`；`op` 写成 `"LEN_GTE"`、`"gte "`（带空格）、`"CONTAINS"` 或任意拼写错误时落入默认分支。对照：`DICT_COMPARE`/`LIST_COMPARE` 都做了算子白名单校验（`engine.py:2124-2126`、`engine.py:2153-2155`），只有数值比较没有。
- 后果：前置检查按 ≥ 判定，与配置意图（如「库存 ≤ 上限」写错算子后变成「库存 ≥ 上限」）相反，可能放行本应拦截的合同并把错误条件写进 `execution_result` 的「通过」结论。
- 修复建议：`compare_op` 对未知算子抛 `BusinessError`；`FIELD_COMPARE` 数值分支与结构化分支统一走白名单校验。

---

### [P3] K-15 `INDUSTRY_IS` 检查的 `detail` 硬编码 `actual = "未设置"`，失败原因恒为误导信息

- 位置：`backend/apps/contracts/engine.py:2218`
- 代码：

```python
                company = Company.objects.filter(pk=company_id).values("industry_type_id").first()
                actual = "未设置"
                it = IndustryType.objects.filter(pk=to_number(c.get("industryTypeId")) or 0).values("name").first()
                expected = it["name"] if it else f"#{c.get('industryTypeId')}"
                passed = bool(company and company.get("industry_type_id") and company["industry_type_id"] == to_number(c.get("industryTypeId")))
                detail = f"产业类型={actual}，要求={expected}"
```

- 触发条件：任何 `INDUSTRY_IS` 条件（无论公司是否真的设置了产业类型）——`actual` 从未被赋成公司真实产业类型名，而 `engine.py:2228-2230` 的失败摘要会把 `detail` 直接展示给用户并写入 `execution_log`/`executionResult`。
- 后果：合同执行失败时用户看到「产业类型=未设置，要求=X」，而实际原因是公司产业是 Y；排查方向被误导（这条检查还带 `errorMessage` 定制文案，问题会被进一步掩盖），审计记录失真。
- 修复建议：查 `IndustryType.objects.filter(pk=company["industry_type_id"]).values("name")` 填入 `actual`（未设置时才用「未设置」）。

---

### [P3] K-16 `LIST_RANGE` 无长度上限，可用合同类型 DSL 构造超大列表耗尽内存/CPU

- 位置：`backend/apps/contracts/engine.py:1037`
- 代码：

```python
    if op == "LIST_RANGE":
        start = num(a[0])
        stop = start if a[1] is None else num(a[1])
        step = (1 if start <= stop else -1) if a[2] is None else num(a[2])
        out = []
        if step == 0:
            return out
        i = start
```

- 触发条件：合同类型 `effects` 中使用 `{"type":"OP","op":"LIST_RANGE","args":[0, 1000000000]}`（或 `step` 极小，如 `0.0001`），在 `FOREACH/body` 或 `SET` 值源中被求值。该列表随后进入 `dumps_engine_json`（`engine.py:1930-1932`）与 `execution_log`。
- 后果：单次执行/试算请求消耗数 GB 内存并长时间持有 SQLite 写事务（可放大 K-13 的 `database is locked` 影响面），进程可能被 OOM kill；需要 `contractType:manage`（比赛管理员）权限，属配置面资源耗尽。
- 修复建议：`LIST_RANGE` 增加长度上限（如 10^6）与元素类型约束，超限抛 `BusinessError`；对 `LIST_*` 全族的输出长度做统一上限。

---

### [P3] K-17 `contract:execute` 的权限语义自相矛盾：目录/前端称「比赛级不限公司」，后端按公司范围拒绝，且空范围=零可见

- 位置：`backend/apps/contracts/views.py:821`
- 代码：

```python
    last_cid = _get_last_signatory_company_id(contract)
    if last_cid is None or last_cid not in user.company_scopes_list:
        raise BusinessError(
            "仅具有合同参与公司管理权的账号可执行", code=403, status_code=403
        )
```

- 触发条件：账号持有 `contract:execute`（后端权限目录标注为「执行（比赛级，不限公司）」，`apps/common/permissions.py:155`；前端目录同文案 `frontend/src/permissions/catalog.ts:50`；`frontend/src/stores/auth.ts:55` 直接据此放宽 UI；`frontend/src/views/account-management/AccountManagementView.vue:254` 亦声明「合同执行与列表可见性不受 companyScopes 限制」），但 `companyScopes` 未覆盖合同末位参与公司——后端一律 403。若 `companyScopes` 为空（如创建 COMPETITION_ADMIN 时未勾选公司，`AccountManagementView.vue:296-303`），列表侧 `_filter_by_scope` 对操作类账号「空范围 = 看不到任何合同」（`views.py:772-777`），合同列表恒为空、任何执行恒 403。
- 后果：合法管理员在 UI 上能看到执行按钮却必然 403（`_assert_execute_scope` 与 `auth.ts:canAuditCompany` 判定相反）；若用这类账号运行 `contract_watcher`，`GET /api/contracts?status=EXECUTED` 恒空，监听程序静默不工作。反之若以目录语义为准，则 `_assert_execute_scope` 是过严的拦截。两侧代码都自洽，缺陷在口径未统一。
- 修复建议：确定单一权威语义——若「比赛级执行」成立，则 `_assert_execute_scope` 只校验 `contract:execute`，列表对 execute 持有者不按 `companyScopes` 过滤（或把空范围语义改为「不限制」，与 `contract_view_company_scopes_list` 的既有约定对齐）；同时修正前端 `auth.ts:52-59` 与权限目录文案。

---

## 存疑 / 待确认

1. **[待确认] `_write_field_value` 二次冲突分支的真实可达性**（K-09）：当前部署为 SQLite（`settings.py:305-310`，无 WAL/`transaction_mode` 配置），读锁使并发写更可能先失败为 `database is locked`；该静默分支应主要在 MySQL REPEATABLE READ（重读仍见快照）下成立。若生产库为 SQLite，K-09 优先级可下调为 P3，但代码语义缺陷仍在。
2. **[待确认] 手动编辑与合同落账的复原口径**：`revert_contract`（`engine.py:2008-2037`）用「最早 `before_raw` 基线 + 按 `executed_at` 重放存量增量」复原，模型上不包含区间内由 `apps/company_fields` 手动编辑或计算字段重算写入的值（那些路径是否写 `ContractFieldEffect` 未在本次范围内确认）。若会引入外部写入，终止/删除合同后的字段值可能与预期有偏差。
3. **[待确认] contract-watcher 账号的实际权限配置**：README/TEST_FLOW 推荐 `admin` 或持 `contract:view`/`contractType:view` 的账号——纯 view 账号不受 `_filter_by_scope` 的操作类分支影响（空范围=不限制）可正常工作；若运维改用持 `contract:audit/manage/execute` 且 `companyScopes` 为空的账号，则轮询恒空（见 K-17）。
4. **[待确认] `ContractFieldEffect` 缺少幂等/唯一键**：当前重复执行已由 `views.py:334-345` 的原子抢占防住，该表无 `(contract, company, field)` 唯一约束或执行序号；若将来引入补偿任务、管理命令重放或人工修数，没有幂等保护。是否需要取决于后续设计。
5. **[待确认] 试算接口的回滚依赖最内层事务**：`ContractTrialAPIView` 以 `transaction.set_rollback(True)`（`views.py:530`、`views.py:547`）实现「整体回滚」，当前 `settings.py` 未开启 `ATOMIC_REQUESTS`，其 `with transaction.atomic()` 为最外层，语义正确；若日后开启 `ATOMIC_REQUESTS` 或在外层再包事务，需复核回滚范围与 `on_commit` 广播被取消的假设。
6. **[待确认] `ContractType.key` 并发创建**：`serializers.py:106-109` 为「先查后建」，无数据库层兜底重试，并发同 key 创建会抛 `IntegrityError`（500）。发生概率低，未列入正式缺陷清单。
