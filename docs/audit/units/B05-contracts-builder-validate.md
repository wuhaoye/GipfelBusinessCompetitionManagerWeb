# B05 合同类型建库-体检/CLI/测试（分支归属：feature/contract-type-code 引入）

## 概述

审计对象（`git log` 确认：`408de7e` 由 feature/contract-type-code 引入，master 无此文件）：

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/apps/contracts/builder/validate.py` | 1054 | 静态体检，**60 个** distinct finding code（与「60 项检查」的说法一致，已逐个数过） |
| `backend/apps/contracts/management/commands/build_contract_types.py` | 470 | `--check/--trial/--dry-run/--import/--export/--all/--json/--strict-types/--verbose` |
| `backend/apps/contracts/tests/test_type_builder.py` | 958 | 编译契约 / 实体 / 聚合 / 体检 / 引擎端到端 |
| `backend/apps/contracts/tests/test_named_effect_semantics.py` | 294 | 11 种具名效果的真实引擎语义 |
| `backend/apps/contracts/tests/test_complex_contracts.py` | 721 | 6 个复杂案例的两层验收 |
| `backend/examples/contracts/demo_contracts.py` | 220 | 简单示例（可直接跑） |
| `backend/examples/contracts/complex_contracts.py` | 569 | 6 个复杂案例 |

验证方式：全部结论基于 `read`/`grep` 逐行读代码；对可静态判定的部分，用 `backend/.venv/Scripts/python.exe` **只读**跑了 `build()/check()/validate_payload()/apply_op()/safe_evaluate()` 的纯计算探针（未写库、未跑 `--import/--trial`），下文标 `【探针】` 的结论均有实测输出。

先行澄清三个「问题清单里问、但实际不存在」的点，避免后续重复投入：

1. **`--export` 没有文件写入路径**：它全程 `self.stdout.write(...)`（`build_contract_types.py:395-408`），既不落盘也不接受输出路径参数，因此**不存在路径穿越/覆盖已有文件**的风险。
2. **`--trial` 确实不落库**（除下述 L-01 类误报外）：引擎只在 `contract["id"] is not None` 时写效果审计行（`engine.py:1962`），字段改写被 `transaction.atomic() + set_rollback(True)` 丢弃（`build_contract_types.py:282-285`），且引擎内无 cache 写入、无 signal（grep 全库确认）。试算的 `"id": None` 保证了不写审计行。
3. **租户归属不是缺陷**：`ContractType` 是**全局模板**（`models.py:14-16`，无 competition 外键，`key` 全局唯一），所以 CLI 直写不会造成跨比赛越权写入；但也因此 `--competition` 只影响体检/试算，不影响写入目标（见 L-07）。

---

## 缺陷清单

### [P1] L-01 「字段引用」只认 FIELD 效果与 FIELD_COMPARE，值源里的 FIELD 与容器比较条件完全不校验

- 位置：`backend/apps/contracts/builder/validate.py:296`（并被 `:936` 复刻同一套 walk）
- 代码：
```python
    def walk(effects: Sequence[Any], where: str) -> None:
        for i, eff in enumerate(effects or []):
            if not isinstance(eff, dict):
                continue
            spot = f"{where}[{i}]"
            kind = eff.get("kind")
            if kind == "FIELD":
                note(str(eff.get("fieldKey") or ""), spot)
            elif kind == "IF":
                walk(eff.get("then") or [], f"{spot}.then")
                walk(eff.get("else") or [], f"{spot}.else")
            elif kind == "FOREACH":
                walk(eff.get("body") or [], f"{spot}.body")

    walk(payload.get("effects") or [], "效果")
    for i, cond in enumerate(payload.get("conditions") or []):
        if isinstance(cond, dict) and cond.get("kind") == "FIELD_COMPARE":
            note(str(cond.get("fieldKey") or ""), f"检查[{i}]")
```
```python
    elif t == "FIELD":                                     # validate.py:830
        if not str(spec.get("fieldKey") or "").strip():
            report.add(ERROR, "value.field_key", ...)
        role = str(spec.get("party") or "").strip()        # 只校验 party，不校验 fieldKey 是否存在
        if not role:
            report.add(ERROR, "value.field_party", ...)
        elif role not in parties:
            report.add(ERROR, "value.field_party_unknown", ...)
```
- 触发条件：字段引用以**值源**形态出现时，既不做「字段是否在该产业下」(`field.missing`) 也不做「字段是否在任何产业下」(`field.not_anywhere`) 检查。两条最常见路径都命中：
  1. **字段→字段效果**（本库一等公民写法）：`ct.add_number(buyer.field("cash"), seller.field("cash"))` 编译成 `{"type":"FIELD","party":"seller","fieldKey":"cash"}`【探针：确认 value 形态】。把 `cash` 拼成 `cassh` → **errors=[]**。
  2. **容器比较条件**：`ct.check_container("DICT_COMPARE", "GTE", p.field("stok"), number("1"))` / `LIST_COMPARE` 会把字段放进 `value1`（`{"kind":"DICT_COMPARE","value1":{"type":"FIELD",...,"fieldKey":"stok"}}`）【探针：确认 conditions 形状】，`_check_fields_exist_anywhere` 只看 `cond.get("fieldKey")`，容器条件没有这个键 → 拼错 `stok`/`tagz` → **errors=[]**（对照组：`ct.check(p.field("stok") >= ...)` 会正常报 `field.not_anywhere`）。
- 后果：体检「零阻断」通过 → `--import` 写入全局模板 → 签/执行合同时引擎直接抛错：`BusinessError("公司「X」所属产业下不存在字段「cassh」")`（`engine.py:1685-1686`）。即本模块头号卖点「E 引用可达」在最常见的两类引用上失效，失败点被推迟到运行期。
- 修复建议：把「字段引用的收集」从「只认 kind==FIELD」改为**遍历所有值源**（复用 `_walk_value` 的递归，或在 `_walk_value` 的 `t == "FIELD"` 分支里就地登记 `(party, fieldKey)`），并让 `_check_fields_against_industry`/`_check_fields_exist_anywhere` 共用同一份收集器；条件侧补 `DICT_COMPARE`/`LIST_COMPARE` 的 value1/value2 与 `branch.cond`。

---

### [P1] L-02 `--import` 不在事务内，且序列化器异常未归一化 → 半量导入且无回滚

- 位置：`backend/apps/contracts/management/commands/build_contract_types.py:310`
- 代码：
```python
    def _write_all(self, types: list[ContractType], *, dry_run: bool) -> None:
        from apps.contracts.serializers import ContractTypeSerializer

        created = updated = unchanged = 0
        for ct in types:
            body = ct.payload()
            body.pop("graph", None)  # graph 单独处理：不覆盖已有画布
            existing = ContractTypeModel.objects.filter(key=ct.key).first()
            if existing is None:
                if dry_run:
                    ...
                serializer = ContractTypeSerializer(data=body)
                serializer.is_valid(raise_exception=True)
                serializer.save()          # ← 逐条 autocommit，无外层 atomic
```
- 触发条件：脚本产出 ≥2 个合同类型，第 N 个写入失败。失败源不止一种：
  - `serializer.is_valid(raise_exception=True)` 抛 DRF `ValidationError`（例如 `create()` 里 key 已存在的竞态检查 `serializers.py:106-109`、`name` 为空）——不是 `CommandError`，Django 直接打 traceback；
  - `body["graph"] = json.loads(existing.graph)`（`:341`）遇到库里 `graph` 是非法 JSON 时抛 `JSONDecodeError`；
  - `_load_types` 之后的任何 DB 异常。
  整个 `handle()` 内只有 `_trial_all` 用了 `transaction.atomic()`（grep 确认：`transaction` 除 `:41` 的 import 外只出现在 `:282`/`:285`）。
- 后果：前 N-1 个合同类型已经**提交**（`ContractType` 是全局模板，所有比赛共用），命令以退出码 1 + traceback 结束；调用方（CI/部署脚本）只看到「失败」，无法知道库里已经是半新半旧状态。极端情况：被跳过的那个类型才是关键修复，而失败的脚本重跑前无人察觉。
- 修复建议：`_write_all` 整体包 `with transaction.atomic():`；把 `serializer.is_valid(raise_exception=True)` 的 `ValidationError` 转成 `CommandError`（附 key 与字段级 errors）；`json.loads(existing.graph)` 用 `_parse_json(existing.graph, None)` 兜底。

---

### [P1] L-03 示例 `owned` 输入用字符串 `"[]"` 当 `list` 类型默认值 → 前置校验恒不通过，文档里的 `--trial` 流程必然失败

- 位置：`backend/examples/contracts/complex_contracts.py:308`
- 代码：
```python
    tech = ct.input("tech", "引进的科技", "techNode", required=True)
    owned = ct.input("owned", "已解锁科技清单", "list", default="[]")
    fee = ct.input("license_fee", "授权费", "number", required=True, default="200000")
    discount = ct.input("discount", "老客户折扣", "number", default="0")
    ...
    ct.check_container(
        "LIST_COMPARE", "CONTAINS",
        tech_prerequisites(tech), owned,
        label="科技前置校验",
        error="所选科技的前置节点尚未全部解锁，不能越级引进",
    )
```
- 触发条件：任何用默认值补齐输入项的调用方。CLI 的 `--trial` 正是这样做的（`build_contract_types.py:267` `inputs = ct.default_inputs()`），而该示例的文档头把 `--trial` 列为标准用法（`complex_contracts.py:34`）。此时 `owned` 取到字符串 `"[]"`，引擎的列表比较要求两个操作数都是 `list`：
```python
            elif kind == "LIST_COMPARE":            # engine.py:2148
                v1 = resolve_value(c.get("value1"))
                v2 = resolve_value(c.get("value2"))
                ...
                elif not (isinstance(v1, list) and isinstance(v2, list)):
                    passed = False
                    detail = f"LIST_COMPARE 要求两个操作数均为列表，实际：值1={...}，值2={...}"
```
  （`eval_value_spec` 的 `INPUT` 分支只对 `CONST` 做 `json.loads` 字符串还原：`engine.py:1701-1710`，`INPUT` 原样返回 `raw`，`:1774`。）
- 后果：两层。
  1. `--trial` 对 `technology-license × 每家公司` 都报「检查未通过 —— 科技前置校验：LIST_COMPARE 要求两个操作数均为列表…」，随后 `CommandError("1 个合同类型的试算未全部通过")` 退出码 1——文档里给出的标准命令**开箱即失败**；
  2. 若跳过 `--trial` 直接 `--import`（该示例文档也给了 `--import`），模板落库后**任何**按 inputSchema 默认值创建的技术引进合同都会被前置校验拦死，用户看到的是「前置节点尚未全部解锁」这个**与真实原因无关**的提示。
  测试完全掩盖了这一点：`test_complex_contracts.py:431/442/451/458` 全部显式传 `owned=[...]` 真列表，从不走默认值路径；`test_default_inputs_fills_schema_defaults` 也只查 `penalty_rate`。
- 修复建议：`default=[]`（真列表）；并在 `validate.py` 增加「输入项 default 与 type 不匹配」检查（`list` 默认值必须是数组、`number` 默认值必须可转数值、`boolean` 默认值必须是布尔），CLI 的 `--trial` 才会被提前拦住。

---

### [P2] L-04 「效果 × 字段类型」判定形同虚设：`--strict-types` 也救不回来

- 位置：`backend/apps/contracts/builder/validate.py:982`、`:1010`
- 代码：
```python
        # 效果 × 字段类型
        for op in ops:
            kinds = _kinds_for_op(op)
            if not kinds:
                continue
            need = {EFFECT_KINDS[k][0] for k in kinds}
            if "ANY" in need or actual.upper() in need:
                continue
            if strict or _definitely_wrong(op, actual):
                report.add(ERROR, "effect.type_mismatch", ...)
```
```python
    def _definitely_wrong(op: str, field_type: str) -> bool:
        ft = str(field_type or "").upper()
        o = str(op or "").upper()
        if ft == "NUMBER":
            return o not in ("ADD", "SUB", "SET")   # 数值字段三种 op 都有意义
        if ft == "LIST":
            return False
        if ft == "DICTIONARY":
            return False
        if ft in ("STRING", "BOOLEAN"):
            return o in ("ADD", "SUB")
        return False
```
- 触发条件：`op` 已被 `_check_leaf_effect` 保证是 ADD/SUB/SET 之一，所以 `_definitely_wrong` 的 NUMBER 分支恒为 `False`、LIST/DICTIONARY 直接 `False`；`_kinds_for_op` 又把 11 个具名效果塌缩成 3 个 op（`ADD → add_number/append_items/add_dict`），于是「同一 op 内的类型错配」永远不会被看出来；`SET` 的候选里含 `set_value`（字段类型 `ANY`），`"ANY" in need` 直接 `continue`，**任何 SET 都不检查**。
- 后果（【探针】实测，declared 里 `cash=NUMBER / tags=LIST / stock=DICTIONARY`）：
  - `ct.add_dict(p.field("cash"), {"a": 1})` → `errors=[]`（`--strict-types` 也是 `[]`）
  - `ct.set_dict(p.field("cash"), {"a": 1})` → `errors=[]`
  - `ct.append_items(p.field("cash"), text("x"))` → `errors=[]`
  - `ct.add_number(p.field("tags"), number("1"))` → `errors=[]`
  - 只有 `add_number` 打到 STRING 字段才报 `effect.type_mismatch`（对照组）。
  即「把字典写进数值字段」「把列表写进数值字段」这类必然写坏字段的效果一路绿灯导入，运行期 `apply_leaf` 会把结构值写进 NUMBER 字段，之后该字段参与数值运算恒为 0。模块头把这类检查列为四大类之一的「T 类型匹配」，但实际只有 STRING/BOOLEAN×ADD/SUB 这一格生效。
- 修复建议：判定时不要只看 `op`，用**值形态**（`CONST` 的 value 是 dict/list/scalar、`OP.LIST_CONCAT` 是列表、`OP` 算术是标量——`effects.py:_value_shape` 已有现成实现）与目标字段类型交叉判定；把 `set_value` 的 `ANY` 收窄为「仅当值形态与字段类型一致时才放行（dict→DICTIONARY、list→LIST、标量→NUMBER/STRING/BOOLEAN）」。

---

### [P2] L-05 `--json` 的 stdout 不是合法 JSON（CI 直接解析失败）

- 位置：`backend/apps/contracts/management/commands/build_contract_types.py:96`
- 代码：
```python
        snap = snapshot(options["competition"]) if options["competition"] else None
        self.stdout.write(f"共 {len(types)} 个合同类型" + (f"，比赛 #{options['competition']}" if snap else ""))

        reports: list[Report] = []
        for ct in types:
            reports.append(static_check(ct, snap=snap, strict_effect_types=bool(options["strict_types"])))

        if options["json"]:
            self.stdout.write(json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=2))
```
- 触发条件：`--json`（帮助文本明说「便于接入 CI」）。前缀行 `共 N 个合同类型，比赛 #7` 无条件下发到 stdout；`--json --verbose` 还会在数组之后再追加每个类型的 `ct.to_json()`（`:111-115`），`--json --dry-run/--import` 追加导入摘要（`:348-352`）。
- 后果：`python manage.py build_contract_types src/ --json | jq` / CI 的 JSON 解析全部失败；若 CI 用 `json.load(stdout)` 会因为「Extra data」报错，而非零退出的场景（体检通过时退出 0）会让 CI 步骤**报错但原因不明**。
- 修复建议：`--json` 时把所有人类可读输出改走 `self.stderr`（或加 `--json` 就抑制 banner/detail 行），并在测试里加一条 `json.loads(stdout)` 的断言。

---

### [P2] L-06 文档承诺的「S 静默回退」检查（缺 location / 缺默认值）没有实现，测试用被禁用的断言假装覆盖

- 位置：`backend/apps/contracts/builder/validate.py:22` + `backend/apps/contracts/tests/test_type_builder.py:830`
- 代码：
```python
- **S 静默回退**：会触发引擎「静默降级」的配置（地点价、缺 location、缺默认值）；
```
```python
        ct = ContractType("e-loc", "地点回退")
        p = ct.party("p", "方")
        mats = ct.input("mats", "原料清单", "materialList")
        ct.add_number(p.field("cash"), total_price(mats, at=p))
        report = static_check(ct, snap=snap)
        # 均价口径提示 + 参与方未限定产业
        codes = [f.code for f in report.findings]
        self.assertIn("value.price_avg", codes) if False else None      # ← 被禁用的断言
        # at=party 时不会给 price_avg 提示（那是显式指定口径），但 location 缺失由快照暴露
        self.assertTrue(any(f.code in ("industry.party_unbound", "value.price_avg") for f in report.findings))
```
- 触发条件：grep `validate.py` 全文，**没有任何** `location`/`default` 相关的检查（`location` 只出现在头部文档与注释里，`default` 只出现在 `dataclasses.field(default_factory=...)`）。与「缺 location」沾边的唯一实现是 `value.price_avg`，而它只在**完全没指定参与方**时报 INFO（`:771-778`）；`total_price(mats, at=p)` 这种**指定了参与方但公司没填 location** 的场景——也就是文档表格第 11 行描述的静默回退——恰恰不报。「缺默认值」同样没有任何检查（`_check_inputs` 只查 type/entity_type/allowed/party/route/key 标识符，`validate.py:407-458`）。
- 后果：使用者按文档相信「地点价回退 / 缺默认值」已被体检覆盖，实际这两类静默降级会一路带到运行期（回退均价、乘费率恒 0）；而 `test_party_location_fallback_is_flagged` 这条**名字就宣称覆盖 location 回退**的测试，第一行断言被 `if False` 关掉、第二行 `any(A or B)` 里 `industry.party_unbound` 与 location 毫无关系 → 恒真，等于没测。
- 修复建议：二选一——(a) 真实现：给 `validate_payload` 增加「参与方 → 是否有 location」的数据（`snapshot.company_location()` 已有），对 `PRICE` + `party` 的 spec 报 warning；对「参加公式/聚合的输入项没有 default」报 warning；(b) 若暂不实现，删掉文档里的承诺并让测试如实断言（把 `if False` 那行改成对 `industry.party_unbound` 的显式断言，或直接删掉该测试）。

---

### [P2] L-07 `--import` 不要求 `--competition`：跳过全部字段类检查却照样写全局模板

- 位置：`backend/apps/contracts/management/commands/build_contract_types.py:96`、`:133`
- 代码：
```python
        snap = snapshot(options["competition"]) if options["competition"] else None
        ...
        if do_trial:
            if snap is None:
                raise CommandError("--trial 需要 --competition")
            ...
        if blocked:
            raise CommandError(f"{len(blocked)} 个合同类型存在阻断项，已中止写入")

        if do_dry or do_import:
            self._write_all(types, dry_run=do_dry)
```
- 触发条件：`manage.py build_contract_types src/ --import`（不带 `--competition`）。此时 `snap is None` → `check(ct, snap=None)` → `declared_field_types=None`、`all_field_keys=None`，于是 `field.missing`、`field.not_anywhere`、`effect.type_mismatch`、`industry.party_unbound`、`industry.unknown` 全部**不执行**，只在报告里留一条 INFO（`validate.py:914-922`「未提供产业字段信息，跳过…」）。而 `--trial`（会真跑引擎）反倒强制要求 `--competition`。
- 后果：唯一能拦住「字段根本不存在」的检查被静默跳过（INFO 不是阻断），一个字段名拼错的模板照样写进全局库并影响所有比赛；对比 `--trial` 的门槛，`--import` 是**更弱**的准入。另外 `--competition 0`（手滑或 ID 占位）走同一分支：`if options["competition"]` 对 0 为假 → 静默降级，不报参数错误。
- 修复建议：`--import`/`--dry-run` 也要求 `--competition`（或至少要求显式 `--no-competition` 之类的豁免开关），并把「跳过字段检查」从 INFO 提升为写入前的 WARNING/确认；`--competition` 用 `is not None` 判断以拒绝 0/负数。

---

### [P2] L-08 变量作用域过宽：先用后赋值、只在未命中分支里赋值都能通过体检

- 位置：`backend/apps/contracts/builder/validate.py:370`
- 代码：
```python
    def walk(effects: Sequence[Any]) -> None:
        for eff in effects or []:
            if not isinstance(eff, dict):
                continue
            kind = eff.get("kind")
            if kind == "FOREACH":
                ...
            elif kind == "IF":
                walk(eff.get("then") or [])
                walk(eff.get("else") or [])
            elif kind == "ASSIGN" and eff.get("name"):
                out.add(str(eff["name"]))          # ← 不分层、不看顺序，全部并进全局作用域
```
- 触发条件：`_declared_vars` 把所有 `ASSIGN` 名字（含 IF 分支内、FOREACH 体内）无差别收进一个集合，`_walk_value` 的 `VAR` 分支（`:820-829`）只做 `name not in var_scope` 判定。【探针】以下 payload 零报错：
```python
{"kind": "FIELD", "party": "p", "fieldKey": "cash", "op": "ADD",
 "value": {"type": "VAR", "name": "later"}},                       # 先用
{"kind": "IF", "cond": {"type": "CONST", "value": 1},
 "then": [{"kind": "ASSIGN", "name": "later", "value": {...}}], "else": []}   # 后赋值，且在分支里
```
  引擎侧：`scope` 是**边执行边填**的（`engine.py:1952-1958` 顺序执行 `apply_effect`），`VAR` 求值 `scope` 未命中就回退 `inputs.get(name)` → `None`（`engine.py:1776-1781`），随后 `to_number(None)` → 0。
- 后果：`ct.add_number(p.field("cash"), var("discount"))` 写在 `with ct.when(...): ct.assign("discount", ...)` **之前**（或该分支未命中）时，静默加 0，账面金额少算且无任何信号——正是本模块要消灭的静默错误类型，却恰好放过。`FOREACH` 体内 `ASSIGN` 只写进 `child_scope` 副本（`engine.py:1949`），循环结束后变量消失，同样不被体检发现。
- 修复建议：`VAR` 校验改为「顺序 + 作用域」双维度：遍历效果时维护一个"到当前点为止已赋值"的集合（顶层生效、进入 IF 分支时按 copy 处理、FOREACH 体用 `{**outer, var, body_assigns}`），对未赋值引用报 `value.var_scope`（可加"赋值在其后/仅在某分支内"的 hint 精确定位）。

---

### [P2] L-09 `ROUTE` 值源的 `routeRef` 只查存在性、不查类型（同义的 INPUT+ROUTE_* 路径却查）

- 位置：`backend/apps/contracts/builder/validate.py:865`
- 代码：
```python
    elif t == "ROUTE":
        ref = str(spec.get("routeRef") or "")
        if ref not in inputs:
            report.add(
                ERROR,
                "value.route_ref",
                f"{spot} 的路程引用了不存在的输入项「{ref}」",
                where="值源",
            )
```
- 触发条件：`route_distance()` 编译成 `{"type":"ROUTE","routeRef":<key>}`（`values.py:533-535`），而 `route_path_types/route_start_node/route_end_node` 编译成 `{"type":"INPUT","key":...,"aggregate":"ROUTE_*"}`（`aggregates.py:408-420`）。后者走 `AGGREGATE_INPUT_TYPES` 白名单会被拦，前者不会。【探针】输入项 `veh` 类型是 `vehicleList`：
  - `route_distance(veh)` → `errors=[]`
  - `route_start_node(veh)` → `errors=['value.aggregate_mismatch']`
- 后果：`route_distance(载具清单)` 通过体检，运行期 `to_number_array({名称:数量})` 对 dict 返回 `[]`（`engine.py:223-241` 末尾 `return []`）→ 距离 0 → 运费/碳税/路程全为 0，静默算错；且 `ct.check(distance > 0, error="运输路线不能为空")` 会在每次签合同时拦下，用户以为是自己没选路线。
- 修复建议：`ROUTE` 分支补 `inputs[ref].get("type") == "nodeRoute"` 判定，复用 `value.aggregate_mismatch` 或新增 `value.route_ref_type`。

---

### [P2] L-10 IF 分支条件写成 `kind` 形状或 `INDUSTRY_IS` 缺字段时零报错 → 分支永远走 else

- 位置：`backend/apps/contracts/builder/validate.py:726`
- 代码：
```python
    t = spec.get("type")
    # IF 的 cond 是「值源」；而条件对象本身（含 kind）也可能被传进来，这里做一次分流，
    # 避免把 {"kind": "INDUSTRY_IS", ...} 当成未知值源误报。
    cond_kind = spec.get("kind")
    if t is None and cond_kind in ("INDUSTRY_IS", "FIELD_COMPARE", "VALUE_COMPARE", "DICT_COMPARE", "LIST_COMPARE"):
        for slot in ("value", "value1", "value2"):
            if slot in spec:
                _walk_value(spec[slot], inputs, var_scope, parties, report, f"{spot}.{slot}", context=context)
        return
```
```python
    elif t in ("CONST", "INDUSTRY_IS", "PARTY_COMPANY_NAME"):     # validate.py:874
        return
```
- 触发条件：【探针】`{"kind":"IF","cond":{"kind":"INDUSTRY_IS","party":"nope","industryTypeId":1},...}` → `errors=[]`；`{"type":"INDUSTRY_IS","party":"nope"}`（未知参与方）→ `errors=[]`；`{"type":"INDUSTRY_IS","party":"p"}`（缺 `industryTypeId`）→ `errors=[]`。引擎侧 `eval_value_spec` 只按 `type` 分派（`engine.py:1699`），`type` 缺失/不识别的 spec 落到末尾 `return 0`（`engine.py:1834`）；`INDUSTRY_IS` 时 `ctx.parties.get("nope") is None` 也返回 `False`（`engine.py:1801-1807`）。
- 后果：`is_truthy(0)/False` → `apply_effect` 永远走 else 分支（`engine.py:1939-1943`），合同「另一条腿」静默不执行；而同一个 `kind` 形状的**顶层检查**（`conditions`）是被校验的（`cond.kind`/`cond.industry`/`_check_party`），说明体检对同一个错误形状给出了不一致的结论。测试 `test_if_condition_uses_value_spec_shape` 明确记录了这个陷阱（「写成 kind 会落到默认分支返回 0」），却只在 builder 侧断言，体检侧没接住。
- 修复建议：`_walk_effects` 处理 IF 时先判定 cond 是否是值源（有 `type`）；把 `{"kind": ...}` 形状直接报 ERROR（提示「IF 的 cond 必须是值源形状，请写 type」）；`t == "INDUSTRY_IS"` 分支补 `_check_party(...)` 与 `industryTypeId` 非空校验，`PARTY_COMPANY_NAME` 补 `party` 非空校验。

---

### [P2] L-11 退出码 2 从不发生：参数/脚本错误与体检失败同为 1

- 位置：`backend/apps/contracts/management/commands/build_contract_types.py:31`（文档）、`:85`（实现）
- 代码：
```python
退出码：0 成功；1 体检有阻断项 / 试算失败；2 参数或脚本错误。
```
```python
        if not source:
            raise CommandError(
                "请给出脚本路径；或使用 --export <key> 导出已有合同类型"
            )
        path = Path(source).expanduser()
        if not path.exists():
            raise CommandError(f"路径不存在：{path}")
```
- 触发条件：全文 20 处 `raise CommandError(...)` 均未传 `returncode=`（Django 的 `CommandError` 默认 `returncode=1`），也没有 `sys.exit(2)`。反过来，`snapshot(options["competition"])`（`:96`）与 `_export` 内的 `snapshot(...)`（`:372`）都没有 try/except：比赛 id 不存在时抛的是 `DataError`（`errors.py:38`，`ContractBuilderError` 子类）→ 用户看到的是完整 traceback，而不是「比赛 #999999 不存在」。
- 后果：CI/部署脚本无法用退出码区分「脚本写错了（不该重试）」与「合同有阻断项（该修配置）」，只能靠解析输出文本；比赛 id 打错时输出是 traceback，日志里混入框架栈。
- 修复建议：参数/脚本/数据错误一律 `raise CommandError(msg, returncode=2)`；把 `snapshot()`、`json.loads(existing.graph)`、`serializer.is_valid()` 的异常统一包成 `CommandError`。

---

### [P2] L-12 动作开关无互斥校验：`--export` 静默吞掉 `--import`，`--dry-run` 静默压过 `--import`

- 位置：`backend/apps/contracts/management/commands/build_contract_types.py:78`、`:117`
- 代码：
```python
    def handle(self, *args, **options) -> None:
        if options["export"] is not None:
            self._export(options)
            return                       # ← source/--check/--trial/--dry-run/--import 全部被静默忽略
```
```python
        do_trial = options["trial"]
        do_dry = options["dry_run"]
        do_import = options["do_import"]
        if not (do_trial or do_dry or do_import):
            ...
        ...
        if do_dry or do_import:
            self._write_all(types, dry_run=do_dry)     # ← 同时给两个时 dry_run 获胜
```
- 触发条件：`manage.py build_contract_types src/ --competition 7 --import --export steel-sale` → 只导出、**不导入**、退出码 0；`--import --dry-run` → 只预演、不导入（输出里有一句「（未写库）」，但退出码 0、无任何警告）；`--export KEY --all` → KEY 被忽略，导出全部（`:361` `if key and not options["all"]`）。
- 后果：把命令写进部署脚本的人会以为导入成功（退出 0、无报错），实际什么都没写；`--dry-run --import` 这种"先看一眼再真跑"的组合尤其容易被误用为"既预演又导入"。
- 修复建议：`add_arguments` 后做互斥校验（`--export` 与 `--check/--trial/--dry-run/--import` 互斥；`--dry-run` 与 `--import` 互斥），冲突时 `CommandError(..., returncode=2)`。

---

### [P3] L-13 公式名字池的兜底集合缺数学函数，且退化结果被永久缓存 → 误报「未知名字」

- 位置：`backend/apps/contracts/builder/validate.py:194`、`:216`
- 代码：
```python
    names: set[str] = {
        # Python 字面量与语法关键字
        "True", "False", "None", "and", "or", "not", "in", "if", "else",
    }
    try:
        from apps.contracts.engine import EXPR_HELPERS, _BUILTIN_CONSTS, _BUILTIN_FUNCS
        names.update(str(k) for k in EXPR_HELPERS)
        names.update(str(k) for k in _BUILTIN_FUNCS)
        names.update(str(k) for k in _BUILTIN_CONSTS)
    except Exception:  # noqa: BLE001 - 引擎不可用时退化为最小集合
        names.update(
            {"IF", "AND", "OR", "NOT", "len", "get", "keys", "values", "has",
             "hasKey", "merge", "unique", "flatten", "join", "sumOf", "contains",
             "push", "concat", "indexIn"}
        )
    return frozenset(names)
```
- 触发条件：兜底集合里**没有** `abs/sqrt/cbrt/floor/ceil/round/pow/exp/log/min/max/sum/avg/pi/e`（引擎的 `_BUILTIN_FUNCS` 有 30 个，`engine.py:712-745`），而 `:816-817` 的报错提示却向用户承诺「数学函数 abs/sqrt/exp/log/pow/min/max/sum/avg/round/floor/ceil…」。`_FORMULA_NAME_POOL_CACHE` 一旦被填就**不再重算**（`:219-223`），所以只要进程里有一次体检发生在 `django.setup()` 之前（或 `apps.contracts.engine` 当时不可导入），该进程后续所有体检都会把 `exp(...)` 判成 ERROR `value.formula_name`。[待确认] 现有调用方（CLI `handle`、示例 `__main__`、测试运行器）都在 setup 之后才体检，因此暂未观察到实际误报。
- 后果：false positive 会直接阻断 `--check` 退出码 0/1 的判定（把好合同判成坏合同），且提示信息自相矛盾（说 exp 可用，却报它未知）。
- 修复建议：兜底集合改成与 `_BUILTIN_FUNCS` 同步的静态常量，或缓存时记录「是否已成功派生」，失败时每次重试。

---

### [P3] L-14 `field.missing` 的提示把整个产业名字典插进消息，定位信息不可读

- 位置：`backend/apps/contracts/builder/validate.py:931`、`:975`
- 代码：
```python
    industry_type_name = {int(k): str(v) for k, v in (industry_names or {}).items()}
        ...
            report.add(
                ERROR,
                "field.missing",
                f"参与方「{role}」所属产业类型「{industry_type_name}」下没有字段「{field_key}」",
                where="类型检查",
                hint=f"该产业可用字段：{'、'.join(sorted(fields)) or '（无）'}；"
                "引擎执行时会直接报「所属产业下不存在字段」",
            )
```
- 触发条件：任何一次「字段不在该产业下」的报错。【探针】实测输出：`参与方「p」所属产业类型「{9001: '测试产业'}」下没有字段「nope」`。
- 后果：这是最常见的阻断项，消息里本该是产业名，实际是 Python dict 的 repr；多产业时该行会变得很长且无法阅读（`industry_names` 是全量映射）。
- 修复建议：`industry_type_name.get(tid, f"#{tid}")`。

---

### [P3] L-15 `inputSchema` 缺 key / 空 key / 重复 key 一律不报

- 位置：`backend/apps/contracts/builder/validate.py:272`、`:415`
- 代码：
```python
    inputs = {i.get("key"): i for i in payload.get("inputSchema") or [] if isinstance(i, dict)}
    _check_inputs(payload, inputs, report)
```
```python
    for key, item in inputs.items():
        itype = item.get("type")
        if itype not in INPUT_TYPES:
            report.add(ERROR, "input.type", f"输入项「{key}」的类型非法：{itype!r}", where="输入项")
            continue
```
- 触发条件：【探针】三种 payload 全部 `errors=[]`：`[{"type":"number"}]`（无 key）、`[{"key":"","type":"number"}]`（空 key）、`[{"key":"a","type":"number"},{"key":"a","type":"materialList"}]`（重复 key）。原因是 `inputs` 用字典推导：`key=None` 时 `str(None)=="None"` 恰好匹配 `_VALID_IDENT_RE`（`:184`、`:448`）所以连标识符告警都不报；重复 key 后者静默覆盖前者，后续所有以 `inputs[key]` 为依据的判定（聚合白名单、`value.input_unknown`）都会对着**被覆盖的那一项**给结论。builder 自身会拦重复 key（`BuildError("输入项 key「a」重复")`，【探针】），所以本条只在手写/前端 JSON 经 `validate_payload` 时可达——但 `validate_payload` 是 `builder.__all__` 导出的公开入口（`__init__.py:232`）。[待确认] 是否有外部调用方直接传非 builder 产物。
- 后果：非法/重复输入项定义通过体检，运行期取到错误的项或取不到值。
- 修复建议：遍历 `payload["inputSchema"]` 的原数组（保留索引用于定位）而不是先转字典；补 `input.key_missing` / `input.key_dup` 两个检查。

---

### [P3] L-16 值源形状校验的漏项：空公式 / 无值 CONST / 无 op 的 OP 全部放行

- 位置：`backend/apps/contracts/builder/validate.py:874`、`:781`
- 代码：
```python
    elif t in ("CONST", "INDUSTRY_IS", "PARTY_COMPANY_NAME"):
        return
    else:
        report.add(ERROR, "value.type", f"{spot} 的值源类型未知：{t!r}", where="值源")
```
```python
    elif t == "OP":
        for j, arg in enumerate(spec.get("args") or []):
            _walk_value(arg, inputs, var_scope, parties, report, f"{spot}.args[{j}]", context=context)
```
- 触发条件：`CONST`/`INDUSTRY_IS`/`PARTY_COMPANY_NAME` 直接 `return`，`FORMULA` 分支不校验 `expr` 非空（`:785` `expr = str(spec.get("expr") or "")` 后只做正则与名字检查，空串不产生任何 finding），`OP` 分支不校验 `op` 名称与 `args` 个数。【探针】6 个畸形 spec 一起提交，只报出 1 条（`value.var_scope`，且消息是「作用域外的变量「」」）：
  | spec | 体检 | 引擎实际行为 |
  | --- | --- | --- |
  | `{"type":"FORMULA","expr":""}` | 无报错 | `safe_evaluate("")` → **0**（【探针】实测） |
  | `{"type":"CONST"}` | 无报错 | `value=None` → `to_number` → **0** |
  | `{"type":"PARTY_COMPANY_NAME"}` | 无报错 | `parties.get(None)` → `""` |
  | `{"type":"VAR"}` | `value.var_scope`（消息含空名字，误导） | 0 |
  | `{"type":"OP"}` | 无报错 | `apply_op(None, [])` → `BusinessError("未知运算: None")`（【探针】实测） |
  | `{"type":"OP","op":"NOT_AN_OP","args":[]}` | 无报错 | 同上，运行期才报错 |
  | `{"type":"OP","op":"ADD","args":[]}` | 无报错 | `IndexError`（`engine.py:1130-1131`） |
- 后果：前三行是静默 0（体检放行、账目少算），后三行把运行期错误伪装成「体检通过」，其中 `IndexError` 不是业务异常、可能表现为 500。
- 修复建议：`FORMULA` 补 `expr` 非空；`CONST` 补 `"value" in spec`；`OP` 校验 `op` 属于已知运算集合（可从 `engine.apply_op` 的 op 表派生）与最小参数个数；`VAR` 的 `name` 为空时报「缺少变量名」而不是「作用域外」。

---

### [P3] L-17 测试盲区：CLI 零测试；60 个检查码只有 15 个在测试中出现过

- 位置：`backend/apps/contracts/tests/test_type_builder.py:507`（StaticCheckTests 全组）、`backend/apps/contracts/tests/test_complex_contracts.py:284`
- 代码：
```python
    def test_all_pass_static_check(self):
        """六个案例在完整夹具下必须零阻断（字段都在、口径都对）。"""
        bad = []
        for key in CONTRACT_KEYS:
            report = static_check(self._contract(key), snap=self.snap)
            if not report.ok:
                bad.append(f"{key}: " + "；".join(f.message for f in report.errors))
        self.assertEqual(bad, [], "\n".join(bad))
```
- 触发条件（覆盖面实测）：
  - 三个测试文件里 **没有任何** `call_command`/`build_contract_types` 出现（grep 全目录）→ CLI 的 9 个开关、退出码、`_write_all`/`_trial_all`/`_export`、脚本加载器（`_script_files`/`_run_script`/`_coerce_types`）全部无测试；
  - 把 `validate.py` 的 60 个 finding code 逐个在测试全文里检索：**15 个出现过，45 个从未出现**；其中 `field.missing`/`field.not_anywhere`/`value.formula_name` 只被 `assertNotIn`（反向断言）使用，真正「构造坏输入 → 断言报出该 code」的只有约 13 个。从未被任何测试触碰的包括：`effect.op`、`effect.value`、`effect.dict_spec_leak`、`effect.value_shape`、`effect.party_missing`、`cond.op`、`value.route_ref`、`value.entity_ref`、`value.aggregate_unknown`、`input.allowed`、`party.dup`…（完整 45 项见 grep 结果）。
  - 6 个复杂案例的测试夹具把所有产业字段都建齐（`test_complex_contracts.py:64-89` + `_make_fields`），于是 examples 里 `if _has(snap, ...)` 的**跳过分支**（约 20 处）在测试里永远不执行——而这条分支会**改变最终编译产物**，是示例的主要设计之一。
- 后果：体检的绝大多数检查项没有回归保护，改动 `_kinds_for_op`/`_definitely_wrong` 之类的判定不会让任何测试变红（L-04 能长期存在就是证据）；CLI 作为「导入正式数据」的入口完全没有测试防线。
- 修复建议：为 60 个 code 建参数化用例（`(payload, expected_code)` 表驱动，一条一例）；补 `call_command("build_contract_types", ...)` 的 CLI 用例：`--check` 阻断时 exit 1、`--json` 可 `json.loads`、`--dry-run` 不写库 `assertEqual(ContractType.objects.count(), 0)`、`--import` 幂等（二次运行 `[相同]`）、以及「第 3 个类型失败时前 2 个必须不存在」的事务用例。

---

### [P3] L-18 `complex_contracts.py` 有两处「注释/文档与实现不符」的坏示例

- 位置：`backend/examples/contracts/complex_contracts.py:273`、`:398`
- 代码：
```python
    # 出库：按品种扣减库存（字典逐键相减）
    if _has(snap, F["stock"]):
        ct.sub_dict(seller.field(F["stock"]), {"钢材": 0})
```
```python
    # 逐期还款计划：键是期数（运行期才知道）
    if _has(snap, F["terms"]):
        with ct.for_each(range_list(number("1"), months + number("1")), var="period"):
            ct.add_dict(borrower.field(F["terms"]), {"待还期数": 1})
```
- 触发条件：`sub_dict` 要求非空字典（`effects.py:391-395` `_make_mapping` 里 `if not delta: raise BuildError("...的字典不能为空")`），示例为了绕过校验写了 `{"钢材": 0}` → 引擎的字典减数是逐键相减：
```python
            elif isinstance(new_value, dict):        # engine.py:417
                # 逐键相减：共有键的值相减（保留键，不删键）；
                after = {}
                for k, v in base.items():
                    after[k] = to_number(v) - to_number(new_value[k]) if k in new_value else v
```
  减 0 即**什么都不做**：整条效果是死代码（字典 SUB 语义另有 `test_named_effect_semantics.py:192-197`「共有键相减、键仍在」佐证）。
  `syndicated-loan` 的循环体写的是常量键 `{"待还期数": 1}`，`period` 变量从未使用 → 结果是一个计数器，而不是「逐期还款计划」；测试 `test_complex_contracts.py:522-526` 断言的 `{"待还期数": 6}` 恰好证明它没有生成任何按期的键（文档 `:11`「`range_list` + `FOREACH` 动态键」的说法不成立）。
- 后果：示例是「照抄即可」的模板（两处文档都强调可以照抄），照抄者会得到一条恒空转的效果和一个假的动态键；`--export` 的 `namedCoverage` 也会把这 11 条都算成"已反解"，掩盖信息缺失。
- 修复建议：`sub_dict` 改用真实扣减量（若本意就是"不动"，就不要生成这条效果）；`{"待还期数": 1}` 改成 `{str(period): ...}` 之类真正用到循环变量的写法（若引擎要求字典键为字符串，用 `ct.assign` + 变量拼接），或把文档里的「动态键」说法删掉。

---

### [P3] L-19 `demo_contracts.py` 硬编码产业 id，与「换成你自己的比赛 id」的自述不匹配

- 位置：`backend/examples/contracts/demo_contracts.py:37`、`:144`
- 代码：
```python
#: 换成你自己的比赛 id
COMPETITION_ID = 4
...
    # 差异化利率：同一份模板被不同产业的公司使用时走不同分支
    if _has(snap, FIELD["interest"]):
        with ct.when(borrower.is_industry(1)):
            ct.add_number(borrower.field(FIELD["interest"]), principal * number("0.045"))
        with ct.otherwise():
            ct.add_number(borrower.field(FIELD["interest"]), principal * number("0.060"))
```
- 触发条件：文档只说「把 COMPETITION_ID 换成你自己的比赛 id」，但产业 id `1` 是硬编码的；`borrower` 也没声明 `industry_type_id`，因此体检不会（也无法）提示这个常量与参与方所属产业是否一致——`complex_contracts.py:382-384` 的注释明确承认这类不一致「体检无法发现，但运行期会走错分支」。
- 后果：换到任何产业 id ≠ 1 的比赛，所有借款方都静默按 6% 计息（4.5% 那档永不生效），账目金额错误且没有任何提示。
- 修复建议：与 `complex_contracts.py` 一致，把优惠产业提成顶部常量并给 `party(..., industry_type_id=...)`；`validate.py` 可增加一致性检查：条件里的字面量产业 id 若与参与方声明的 `industryTypeId` 明显不符（且该 id 在库中不存在），报 INFO 提示核对。

---

## 存疑/待确认

1. **[待确认] L-13 的真实触发路径**：我无法在现有调用方（CLI/示例/测试）中找到「体检发生在 `django.setup()` 之前」的实际序列，因此把兜底集合缺数学函数记为潜在误报（P3）。如果 `apps/contracts/builder` 被当作库直接 import 并在 setup 前调 `check()`（例如某个自定义脚本或未来的 Web 后台），误报会立刻出现。
2. **[待确认] `_run_script` 的模块名冲突**：`f"_ct_script_{path.stem}"`（`build_contract_types.py:169`）在不同目录存在同名脚本时会复用同一个 `sys.modules` key。本次只做静态判断，未构造「a/ct.py + b/ct.py 互相 import」的用例验证是否有实际影响；含空格/中文的文件名作为 module name 是可用的（未实测）。
3. **[待确认] L-15 的影响面**：重复/缺失 key 只有绕过 builder 手写 payload 才可达（builder 自己会拦），是否有外部调用方直接调 `validate_payload`（它是 `__all__` 导出项）未确认。
4. **[待确认] `--trial` 的非事务残留**：已确认引擎只在 `contract["id"] is not None` 时写审计行、字段改写随事务回滚、引擎内无 cache/signal；但 `_write_field_value` 的乐观锁重试分支（`engine.py:2317-2325`）在回滚后是否留下 `version` 递增等痕迹、以及是否有数据库外的副作用（上传目录、日志）未逐一验证。未运行 `--trial`。
5. **`--export` 无文件写入**：若后续版本给 `--export` 增加 `--out <path>`，需要重新评估路径校验（当前实现无此参数，不构成缺陷）。
6. **第三方依赖行为**：`transaction.set_rollback(True)` 在 `--trial` 中的语义依赖 Django 5.0.14 的 atomic 实现（内层 savepoint 回滚），未做版本无关性验证。
