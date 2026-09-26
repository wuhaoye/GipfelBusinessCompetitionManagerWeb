# B04 合同类型建库-内核（分支归属：feature/contract-type-code 引入）

## 概述

**审计范围（仅本批，已去重）**：提交 `408de7e`（`feature/contract-type-code`）引入、`test` / `feature/contract-watcher` 也包含、master 没有的
`backend/apps/contracts/builder/{__init__,builder,effects,values,refs,aggregates,errors}.py`。

**明确不在本报告范围**（已确认归属其它审计员）：`builder/validate.py`、`management/commands/build_contract_types.py`、`tests/**`、`backend/examples/contracts/**`（B05）；`apps/contracts/` 顶层 `models/engine/views/serializers`（U05，其报告已在 `code_audit/U05-backend-contracts-core.md` 中明确声明 `builder/**` 未审计）。

**方法**：全部结论来自 `read`/`grep` 实读代码。为了判定"什么输入会出错、后果是什么"，只读方式追了引擎侧的对端实现（`engine.py` 的 `eval_value_spec / apply_field_effect / apply_op / safe_evaluate / _call_fn / EXPR_HELPERS / resolve_party_location_node_id`）与 CLI 调用时序（`build_contract_types.py:88-137, 310-346`）作为**证据**；引擎自身的缺陷不在本报告内。数值/字符行为用 `python` 纯计算复算（不连库、不导入项目模块）：`str(int(1e23)) == '99999999999999991611392'`、`"钢材".isalnum() is True`、`bool("false") is True`、`float("nan").is_integer() is False`。

**总体印象**：本层的三处核心设计（11 个具名效果自带字段类型契约 / 实体属性白名单 / 值源具名构造）方向正确，且 `errors.py` 的报错质量（近似名提示、可用清单）明显高于仓库平均水平；`_materialize_mapping` 强制 CONST（避免 `to_number(dict)→0`）、`_concat` 的列表形状拍平、`remove_keys` 恒定数组形状这几处都是**刻意的正确设计**。但**"构建期直接报错"这一核心卖点并未落在 `build()` 上**——类型校验只在可选的 `check()` 里跑（E-05），而 `build()` 本身有一个会**静默产出错误合同类型**的状态缺陷（E-01，CLI 导入路径必然触发）。

缺陷共 17 条：P0 × 1、P1 × 3、P2 × 7、P3 × 6。

## 缺陷清单

### [P0] E-01 快照实体槽位是一次性队列：第二次 `build()` 必然丢槽位，CLI 导入路径 100% 触发

- 位置：`backend/apps/contracts/builder/refs.py:422`、`backend/apps/contracts/builder/builder.py:869`（触发点：`builder/__init__.py:261`、`management/commands/build_contract_types.py:101,315`）
- 代码：
```python
# refs.py:422  —— 清了 _pinned_inputs，但 _pinned(entity_id→slot) 仍然保留
    def drain_pinned_inputs(self) -> list[dict[str, Any]]:
        """取出并清空待注入的槽位输入项（构建器在编译前调用）。"""
        out = list(self._pinned_inputs)
        self._pinned_inputs.clear()
        return out
```
```python
# refs.py:404  —— 因此第二次调用 pin() 仍然返回同一个槽位名（但不是空）
        slot = self._pinned.get(ref.entity_id)
        if slot:
            return slot
```
```python
# builder.py:869  —— 槽位只从「待注入队列」取，队列已空则 inputSchema 里什么都没有
        if self._snapshot is not None:
            existing = {i["key"] for i in input_schema}
            for slot in self._snapshot.drain_pinned_inputs():
                if slot["key"] not in existing:
                    input_schema.append(dict(slot))
                    existing.add(slot["key"])
```
```python
# builder/__init__.py:261 —— check() 内部就会 build() 一次，即抽干一次
    payload = ct.build()
```
- 触发条件：任何用 `snap.material("铁矿石").carbon` 这类实体引用的合同类型，**只要 `build()` 被调用两次**即触发。真实路径有三条，第一条是命令行的默认流程：
  1. `manage.py build_contract_types <src> --competition 7 --import`：CLI 在写库前**无条件**对每个 `ct` 跑 `static_check(...)`（第 100-101 行）→ `check()` → `ct.build()`（抽干）→ 随后 `_write_all()` 里 `body = ct.payload()`（第 315 行）→ `build()` 第二次拿到空列表 → 写进库的 `inputSchema` **没有** `__ref_*` 项，而 `effects` 里照旧写 `"entityRef": "__ref_material_3"`。单文件单合同类型也会中招。
  2. 脚本里 `check(ct, snap=snap)`（或 `ct.payload()`、`ct.default_inputs()`、`ct.effects_json()`、`ct.conditions_json()`——它们都各自调 `build()`）之后再取 `ct.payload()`。
  3. 一个文件里返回多个 `ContractType` 共用一个 `snap`（CLI 文档第 25 行推荐的 `return ct | [ct, ct2]` 写法）：第一个 `ct` 的 `static_check` 会把**所有** `ct` 的槽位一次抽干并塞进自己的 payload，其余 `ct` 全部丢槽位。
- 后果：落库的合同类型中，每条 ENTITY 值源在运行期取到 `inputs.get("__ref_material_3") is None`，引擎 `engine.py:1818-1820` 直接 `if not ent_id: return 0` —— **所有实体引用恒为 0**（原料碳排系数、载具载重、基建加成…全变 0），且不报错、审计表里记录的就是 0，属于静默数据损坏。第 3 条路径还会把 A 的隐藏输入项注入 B（B 的 inputSchema 里出现自己没引用的实体槽位，`entityType` 还是 A 的类型），造成跨合同的数据串位。
- 修复建议：`pin()` 的登记结果不应由"取一次就没了"的队列承载。最小改动：`drain_pinned_inputs()` 改成不清空（`return list(self._pinned_inputs)`），或在 `build()` 里从 `self._pinned`（entity_id→slot）重建全部槽位；更稳的做法是把待注入槽位按 `ContractType` 归属分组（`snap.pin(ref, owner=ct)`）。同时给 `build()` 加一条自检：`inputSchema` 的 key 集合必须覆盖 `effects` 里出现的所有 `entityRef`，否则抛 `SchemaError`（`_assert_contract_shape` 已经在做同类形状自检，加这条成本极低）。注意 `builder/validate.py:857` 只校验 `entityRef` 非空、不校验它是否真的存在于 `inputSchema`，所以 `--check` 也拦不住这条。

### [P1] E-02 实体槽位把「比赛内主键」烧进全局合同模板，跨比赛静默取到另一条实体

- 位置：`backend/apps/contracts/builder/refs.py:409`
- 代码：
```python
        self._pinned[ref.entity_id] = slot
        self._pinned_inputs.append(
            {
                "key": slot,
                "label": f"{ref.type_label}：{ref.name}",
                "type": "ENTITY",
                "entityType": ref.entity_type,
                "required": False,
                "default": ref.entity_id,          # ← 比赛局部的自增主键
                "hidden": True,
            }
        )
```
```python
# builder.py:917  —— 本库自己文档化了「default 会在创建合同时被填进 inputs」
    def default_inputs(self, overrides: dict | None = None) -> dict:
        """**为什么需要它**：引擎的 `ContractEngine.execute` **不会**自动套用
        `inputSchema` 里的 `default`……默认值是在**创建合同**那一步由调用方填进 `inputs` 的
```
- 触发条件：合同类型是**全局模板**（`POST /api/contract-types`，无 competition 外键），而 `refs.snapshot(7)` 的 `_index` 是 `filter(competition_id=7).values("id", name)` 的产物，`entity_id` 是 `Material/Part/...` 表里的全局自增 PK。用 `snapshot(7)` 建好类型并导入后，任何**其它比赛**引用该类型创建合同时，隐藏 ENTITY 输入项被填 `default=<比赛 7 的 PK>`；引擎 `engine.py:1818-1828` 用该 id 去 `filter(pk=ent_id)` 取行，**没有再按 competition 过滤**。
- 后果：比赛 8 里 id=1 的原料与比赛 7 里 id=1 的原料不是同一条（若该比赛删过记录/导入顺序不同），合同读到的 `carbonEmissionCoefficient`、`name`、`maxCargo` 等就是**另一条实体的数据**；若该 id 在目标比赛里不存在，则执行期抛 `BusinessError("实体不存在(MATERIAL#1)")` —— 合同当天直接执行失败。这是"跨比赛串数据"，且模板一旦落库就无法回溯是哪场比赛的 PK。
- 修复建议：至少把槽位与比赛绑定并校验——`pin()` 里带上 `competition_id`，`build()` 产出 payload 时若发现"同一 key 的槽位来自不同比赛"直接 `SchemaError`；更好的做法是槽位不存 PK 而存**实体名**（`default` 存 `ref.name`），由使用侧按名解析（引擎侧 ENTITY 只吃 id，需要引擎配合，属跨模块改动）。在 `assert_engine_parity()` 这一类导入期自检里加一条"槽位 default 必须在目标比赛内存在"的断言也能把错误提前到构建期。
- 备注：[待确认] 合同创建那条链路是否确实按 `inputSchema.default` 回填 `inputs`（`builder.py:917-926` 的注释如此声称；`contracts/views.py` 的创建流程属 U05 范围，本次未读）。若前端对 `hidden=True` 的 ENTITY 项**不**提交值，则后果退化为"恒为 0"（与 E-01 相同）。

### [P1] E-03 `concat_text()` 用错了引擎助手：3 段以上运行期 TypeError，2 段得到的是列表

- 位置：`backend/apps/contracts/builder/values.py:498`
- 代码：
```python
    pieces = [_spec_to_expr(_as_value(p).to_spec()) for p in parts]
    expr = f"concat({', '.join(pieces)})"
    return Value({"type": "FORMULA", "expr": expr}, label="拼接文本")
```
```python
# engine.py:962  —— 引擎的 concat 是「列表拼接」且只吃两个参数
    "concat": lambda a, b: (_a_unpack(a) if isinstance(a, list) else [a]) + (_a_unpack(b) if isinstance(b, list) else [b]),
```
```python
# engine.py:908  —— 调用时不校验参数个数，TypeError 直接冒泡
    def _call_fn(self, name: str, args: list):
        builtin = _BUILTIN_FUNCS.get(name)
        if builtin is not None:
            return builtin(*args)
        sc = self.scope.get(name)
        if callable(sc):
            return sc(*args)                      # ← concat(a,b,c) → TypeError
```
- 触发条件：`concat_text(route_start_node(route), " → ", route_end_node(route))` —— 就是该函数 docstring 自带的例子（3 段）→ `concat/3` → `TypeError: <lambda>() takes 2 positional arguments but 3 were given`；两段时（`concat_text(a, b)`）返回 `[a, b]` 这个**列表**，被写到 STRING 字段时经 `cast_scalar`（`engine.py:280-281`）变成 JSON 文本 `["A", " → "]`；一段时缺参数同样 TypeError。
- 后果：字符串拼接这条唯一被文档推荐的路径，要么让整张合同执行崩在未捕获的 `TypeError`（不是 `_SafeExpressionError`，`engine.py:1791` 的 `except` 接不住，直接 500），要么把 `["A", " → "]` 这种脏文本写进公司产业字段。`concat_text` 是 `__all__` 导出的公开 API。
- 修复建议：改用引擎真正做文本拼接的助手 `join`——生成 `join(LIST_CONCAT(...), '')` 或 `FORMULA: join([a, b, c], '')`（`engine.py:976` / `1025-1027`）；`_spec_to_expr` 里对 `CONST` 字符串也能走 `concat` 之外的路径。并在构建期校验助手 arity（可复用 `validate.py:187-210` 的 `_formula_name_pool` 思路，扩成"名字→签名"）。

### [P1] E-04 `_spec_to_expr()` 生成引擎沙箱里不存在的名字：`fieldValue(...)` 运行期 400、`inputs[...]`/`scope[...]` 静默 0

- 位置：`backend/apps/contracts/builder/values.py:378`
- 代码：
```python
    if t == "INPUT":
        if spec.get("aggregate"):
            raise BuildError(...)
        return f"inputs[{spec.get('key')!r}]"          # ← 引擎没有 inputs 对象
    if t == "VAR":
        return f"scope[{spec.get('name')!r}]"          # ← 引擎没有 scope 对象
    if t == "FIELD":
        return f"fieldValue({spec.get('party')!r}, {spec.get('fieldKey')!r})"   # ← 引擎没有这个函数
```
```python
# builder.py:754  —— 同一个仓库里已经写明正确的形状是什么
        编译成 `FORMULA: mats[row]`。**注意不能用 `inputs['mats']`**：引擎的
        `eval_value_spec` 把 FORMULA 的沙箱拼成 `{**inputs, **EXPR_HELPERS, **scope}`
        （`engine.py:1788`），也就是**输入项与循环变量是顶层名字**，并没有
        `inputs` / `scope` 这两个字典对象
```
- 触发条件：任何经 `Value.and_/or_/not_`（`values.py:266-273`）或 `FieldRef.and_/or_/not_`（`builder.py:187-194`）组合出来的条件，只要操作数里含输入项/字段，例如
  `with ct.when((buyer.field("cash") >= amount).and_(buyer.is_industry(1))):` → `FORMULA: AND((fieldValue('buyer', 'cash') >= inputs['amount']), ...)`。
  `fieldValue` 全仓只出现在 `values.py:388` 与前端 `graph-model.ts` 的**求值上下文**里，后端 `engine.py` 没有这个名字 → `_call_fn` 抛 `未知函数: fieldValue` → 被包成 `BusinessError(400)`，合同**整单执行失败**。`inputs`/`scope` 未定义 → 解析器 `engine.py:905 return 0  # 未定义变量回退为 0` → `0['amount']` → `_f_index` 返回 0 → 比较恒假，条件**静默**永远走 else。
- 后果：用文档推荐的逻辑组合写法时，轻则分支恒假（补贴/罚则永不生效），重则合同执行直接 400。
- 修复建议：`_spec_to_expr` 的 INPUT/VAR 分支改成顶层裸名（并对非标识符 key 报 `BuildError`，见 E-12），FIELD 分支在引擎支持之前直接 `raise BuildError("逻辑组合里暂不支持产业字段，请先 assign() 成变量")`；`validate.py:786-812` 已经能识别 `inputs[`/`scope[`/未知名字（跑 `--check` 时是 ERROR），把这段判定提到 `build()` 里即可。
- 备注：这条只在**跑了** `--check`/`check()` 时才被拦下；`build()`→`payload()`→`POST /api/contract-types` 的直连路径没有任何拦截。

### [P2] E-05 效果自带字段类型契约只是文档：`check_field_type()` 全仓无人调用，`build()` 不拦类型错配

- 位置：`backend/apps/contracts/builder/effects.py:182`、`backend/apps/contracts/builder/builder.py:634`
- 代码：
```python
# effects.py:182  —— 实现了完整的「效果 × 字段类型」判定……
    def check_field_type(self, actual: str | None, *, where: str = "") -> str | None:
        """校验本效果与真实字段类型是否匹配；不匹配返回问题描述，匹配返回 None。"""
        need = self.field_type
        if need == "ANY" or actual is None:
            return None
```
```python
# builder.py:634  —— ……但登记路径一个字都没校验，直接挂到效果树
    def add_number(self, target: FieldRef, amount: Any) -> Effect:
        """数值字段增加（只能作用于 NUMBER 字段）。"""
        return self._emit(add_number, target, amount)
```
- 触发条件：全仓 grep `check_field_type` 只有 `effects.py:182` 一处定义（`validate.py` 自己另写了一套判定）。因此 `ct.append_items(p.field("cash"), "X")`（cash 是 NUMBER）、`ct.add_number(p.field("tags"), number("5"))`（tags 是 LIST）、`ct.sub_number(p.field("stock_map"), number("1"))`（字典）都能 `build()` 成功并落库。真实字段类型只有 `snap.industry_field_types()` 才有，`ct.use_snapshot(snap)` 已经把快照交给构建器了，`build()` 却没用它。
- 后果：与模块头部承诺的"不匹配在构建期直接报错"相反，错配一路静默到运行期，且**行为各异**：NUMBER 字段收到列表值 → `to_number(["X"])` → `str(["X"])` 无法解析 → 0（`append_items` 变成"加 0"）；LIST 字段收到 `add_number` → 引擎走列表分支把 5 追加进列表（语义完全变了）；DICTIONARY 字段收到数组 → 走"删键"分支（`engine.py:413-416`）。这正是本模块声称要消灭的"看 op 猜字段类型"。
- 修复建议：`_emit` 里若 `self._snapshot` 非空，用 `snap.industry_field_types()` 对参与方所属产业查 `fieldKey` 的声明类型，调 `effect.check_field_type(...)`，不匹配即 `BuildError`（`use_snapshot` 的语义从"注入槽位"扩展为"顺带做类型契约校验"）；无快照时至少校验字面量值形态与效果种类（`EFFECT_KINDS[kind][2]` 已经声明了 single/sequence/mapping/keys）。

### [P2] E-06 `set_value` 在非 STRING 字段上语义随字段类型变形，"任意类型设定"名不副实

- 位置：`backend/apps/contracts/builder/effects.py:362`、`backend/apps/contracts/builder/effects.py:72`
- 代码：
```python
def set_value(target: Any, value: Any) -> Effect:
    """任意类型字段：整体设定（引擎 op=SET）。

    这是唯一不受字段类型限制的效果，用于 STRING / BOOLEAN 字段，
    或确知要整体覆盖的场合。
    """
    return _make("set_value", target, value)
```
```python
# engine.py:402  —— SET 对字典字段的实际语义
    elif is_dict:
        base = before if isinstance(before, dict) else {}
        if isinstance(new_value, dict):
            obj = new_value
        elif isinstance(new_value, str) and new_value:
            obj = {new_value: True}        # ← set_value(字典字段, "钢材") 得到 {"钢材": true}
```
- 触发条件：`ct.set_value(p.field("terms"), text("钢材"))`（terms 声明为 DICTIONARY）→ 写入 `{"钢材": true}`；`ct.set_value(p.field("tags"), text("X"))`（LIST）→ `["X"]`；`ct.set_value(p.field("cash"), text("abc"))`（NUMBER）→ `to_number("abc")` → 0（`engine.py:181-195`）。三种结果都与"整体设定为这个值"的直觉不符，且没有报错。
- 后果：迁移旧脚本时最容易被 `set_value` 这个"万能出口"坑到——以为在整体覆盖，实际在改字典的键集合或把数字清零；配合 E-01/E-05 会叠加成"看着正常、数字全错"。
- 修复建议：`set_value` 接受目标字段类型参数（或从 `use_snapshot` 推断），按类型分派到 `set_number/set_items/set_dict`，并在类型不匹配时报 `BuildError`；文档里把"任意类型"改为"仅 STRING / BOOLEAN 及确知类型时使用"。

### [P2] E-07 字典效果接受非数值字面量：首次写文本、再次参与运算被清零

- 位置：`backend/apps/contracts/builder/effects.py:264`（`_materialize_mapping`）、`backend/apps/contracts/builder/effects.py:391`（`_make_mapping`）
- 代码：
```python
    out: dict[str, Any] = {}
    for key, val in values.items():
        spec = val.to_spec()
        if spec.get("type") != "CONST":
            raise BuildError(...)          # ← 只要求「字面量」，不要求「数值」
        out[str(key)] = spec.get("value")
    return {"type": "CONST", "value": out}
```
```python
# engine.py:421  —— 逐键相减：键存在就用 to_number 双端转换
                for k, v in base.items():
                    after[k] = to_number(v) - to_number(new_value[k]) if k in new_value else v
```
- 触发条件：`ct.add_dict(p.field("stats"), {"备注": "钢材"})`、`ct.sub_dict(p.field("stats"), {"备注": "钢材"})`、`ct.add_dict(f, {"x": None})`（`_as_value(None)` 也是合法 CONST）。字典字段的 ADD 分支（`engine.py:426-431`）：键**不存在**时 `after[k] = v` 原样写入文本；键**已存在**时 `to_number("钢材") + to_number("钢材")` = 0 → 值被 0 覆盖。SUB 分支更直接：键存在且值为文本 → `0 - 0 = 0`，**原有文本被清零**。
- 后果：`add_dict`/`sub_dict` 的语义（"逐键累加/扣减"）只对数值成立，非数值字面量会让同一个键在不同执行次数下呈现文本/0/0 三种状态；`sub_dict` 会静默清掉既有数据。
- 修复建议：`_make_mapping` 对 `add_dict`/`sub_dict` 强制数值（`int`/`float`/数字字符串，复用 `values.number()` 的校验），非数值字面量只允许出现在 `set_dict`；`None`/空串直接报错。

### [P2] E-08 `total_price(at=...)` 接受任意角色字符串，拼错静默回退市场均价

- 位置：`backend/apps/contracts/builder/aggregates.py:260`（配合 `backend/apps/contracts/builder/values.py:585`）
- 代码：
```python
    where = "市场均价"
    if at is not None:
        role = _party_role(at)                 # ← 只要求「有 role 属性或是非空字符串」
        spec["party"] = role
        where = f"{role} 所在地"
    return Value(spec, label=f"{key} 总价格（{where}）")
```
```python
# values.py:585  —— 字符串分支不做任何注册校验
def _party_role(party: Any) -> str:
    role = getattr(party, "role", None)
    if role:
        return str(role)
    if isinstance(party, str) and party.strip():
        return party.strip()
```
- 触发条件：`total_price(plate, at="byuer")`、`total_price(plate, at="buyer ")`（尾空格已 strip 但大小写不同）、或把常量字符串当参与方传入。引擎侧 `resolve_party_location_node_id`（`engine.py:1628-1631`）对未知 role 直接 `return None`，`compute_material_list_price(raw, cid, None)` 走**市场均价**。
- 后果：同一份合同在"按买方所在地价结算"与"按市场均价结算"之间静默切换，金额差可能很大且无任何报错。`validate.py:771` 只对"完全没传 party"给 INFO，不校验 role 是否存在（`_walk_value` 的 INPUT 分支没有调 `_check_party`），所以 `--check` 也不会报。
- 修复建议：`ContractType` 侧收口——`build()` 时遍历 payload，对所有 `{"type":"INPUT","aggregate":"PRICE"}` 的 `party` 校验收 `_party_by_role`（构建器本来就知道合法角色集合）；或在 `total_price` 增加 `party_ref` 参数只接受 `PartyRef`。同时把 `validate.py` 的 PRICE 检查升级为"role 必须存在，否则 ERROR"。

### [P2] E-09 `effects_from_specs()` 是只读反解，却污染全局游离效果表 → 之后任何 `build()` 都误报

- 位置：`backend/apps/contracts/builder/effects.py:146`、`backend/apps/contracts/builder/effects.py:424`
- 代码：
```python
        self.via_builder = Effect.builder_active
        if not self.via_builder:
            Effect.loose.append(self)          # ← 构造器无条件登记全局表
```
```python
# builder.py:807  —— 构建器把「本构建器水位线之后的所有游离效果」都算成自己的
        orphans = [e for e in self._created if id(e) not in self._attached_ids]
        orphans += [e for e in Effect.loose[self._loose_mark :] if id(e) not in self._attached_ids]
        del Effect.loose[self._loose_mark :]
```
- 触发条件：同一个进程里先做只读巡检、再建库。`effects_from_specs()` 内部 `Effect(...)` 构造时 `builder_active` 为 False（`_emit` 之外没人置位），于是**每一条反解出来的效果都进 `Effect.loose`**。调用点真实存在：`management/commands/build_contract_types.py:378`（`--export`）、`tests/test_type_builder.py:887,911,918,930`；随后任意 `ct.build()`（`_loose_mark` 是 ct **构造时**的水位线，反解出的效果落在水位线之后且不在 `_attached_ids`）→ `BuildError("有 N 条效果创建后没有登记到合同类型上…")`。
- 后果：一个明确只读、导出用的 API 让后续建库失败；更糟的是 `del Effect.loose[self._loose_mark:]` 是**截断共享全局列表**——它会把其它构建器/其它线程尚未上报的游离效果一并吞掉（真正的漏挂载反而漏报），归属也只看"谁先 build"而不看效果是谁创建的。
- 修复建议：`effects_from_specs` 显式关闭登记（构造前 `Effect.builder_active = True` 或加 `record_loose=False` 参数）；`Effect.loose` 改成"由 ContractType 持有的收集器"（`_emit` 之外的模块级误用在同一个进程里本就是全局问题，交给 `builder_active` + 显式上下文管理器更稳）。另外 `builder_active` 是**类属性**，两个线程/两个构建器交错时 `_emit` 的置位窗口会把别的线程创建的游离效果误标成"已登记"（漏报），需要加锁或改成 `contextvars`。

### [P2] E-10 `number()` 不校验数值性（拼写错误静默 0）、`flag("false")` 得到 True

- 位置：`backend/apps/contracts/builder/values.py:406`、`backend/apps/contracts/builder/values.py:423`
- 代码：
```python
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise BuildError(f"number() 只接受数字或数字字符串，收到 {type(raw).__name__}: {raw!r}")
    text = _num_text(raw) if isinstance(raw, (int, float, bool)) else str(raw).strip()
    if text == "":
        raise BuildError("number() 不能为空字符串")
    return Value({"type": "CONST", "value": text}, label=f"数值 {text}")
```
```python
def flag(raw: Any) -> Value:
    """布尔常量。"""
    return Value({"type": "CONST", "value": bool(raw)}, label=f"布尔 {bool(raw)}")
```
- 触发条件：`number("10O")`（把 0 敲成字母 O）、`number("1,000")`、`number("3%")`、`number("一千")` 全部通过校验 → CONST 字符串 → 引擎 `to_number`（`engine.py:183-195`）`int()` 失败、Decimal 解析失败 → **fallback 0**。`number(1e23)` 走 `_num_text` 的 `is_integer()` 分支（`values.py:326-329`）→ 实测 `str(int(1e23)) == "99999999999999991611392"`，与字面量不是同一个数；`number(float("nan"))`/`number(float("inf"))` → CONST `"nan"`/`"inf"` → 0。`flag("false")`、`flag("0")`、`flag("no")` → `bool("false") is True`（实测），只有 `flag(0)`/`flag("")` 才为 False。
- 后果：数值拼写错误不报错、直接变成 0 —— 这正是 `errors.py` 开头声明"本次落地要消灭的主要对象"；`flag("false")` 会让配置驱动的开关**反向**。
- 修复建议：`number()` 用 `decimal.Decimal(str(raw))`（或 `^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$` 正则）+ `is_finite()` 校验，非法即 `BuildError`；大整数不要走 float 分支（`int` 原样 `str()`）；`flag()` 只接受 `bool`，或显式映射 `{"true":True,"1":True,"false":False,"0":False}`，其它类型报错。

### [P2] E-11 输入项 key 与引擎公式助手/循环变量同名时静默取错对象

- 位置：`backend/apps/contracts/builder/builder.py:104`、`backend/apps/contracts/builder/builder.py:780`
- 代码：
```python
        if itype not in _LIST_VALUED_INPUT_TYPES:
            name = _NAME_RE.sub("_", key)
            return {"type": "FORMULA", "expr": f"keys({name})"}   # ← 直接当顶层名字用
```
```python
        return Value(
            {"type": "FORMULA", "expr": f"{_NAME_RE.sub('_', k)}[{_NAME_RE.sub('_', v)}]"},
            label=f"{k}[{v}]",
        )
```
```python
# engine.py:1788  —— 沙箱里 EXPR_HELPERS / scope 都排在 inputs 之后（覆盖同名输入项）
        sandbox = {**inputs, **EXPR_HELPERS, **(scope or {})}
```
- 触发条件：`ct.input("sum", "总额", "materialList")` 或 `keys`/`min`/`max`/`len`/`has`/`get`/`join`/`concat`/`IF`/`AND`/`pi` 等任一引擎助手名（`engine.py:953-978`、`709`、`712-745`）作为输入项 key，再对它 `for_each`/`item` → `keys(sum)` 里的 `sum` 是内置求和函数，循环拿不到清单（空循环或求值异常）。另一条：`ct.assign("mats", ...)` 或 `for_each(var="mats")` 与同名输入项冲突 → FORMULA 里 `mats` 解析到 scope 值（`engine.py:1788` scope 覆盖 inputs），VAR 求值也是 scope 优先（`engine.py:1776-1781`），而 `input_value()` 恰好编译成 VAR（`values.py:445-457`）→ 读到的是变量而不是输入项。
- 后果：循环体不执行（合同"什么都没发生"）或对另一个对象做运算，全静默。`input()`/`assign()`/`for_each(var=)` 三处都能在构建期发现（`self._input_keys` 与 `_var_scopes` 都在手上），现在一处都没查。
- 修复建议：`input()` 校验 key 不在"引擎助手/常量名"集合内（该集合 `validate.py:187-210` 已经会从引擎动态派生，直接复用）；`assign()`/`for_each(var=)` 校验变量名不与 `_input_keys` 重名（或反过来：重名时报错并要求显式改名）。

### [P2] E-12 输入项 key 归一化后可能撞车：`for_each`/`item` 静默遍历到另一个清单

- 位置：`backend/apps/contracts/builder/builder.py:85`
- 代码：
```python
#: 公式里可用作标识符的名字（输入项 key 需要是合法标识符才能直接写进表达式）
_NAME_RE = re.compile(r"\W")
```
```python
# builder.py:104 / 780 两处都这样把 key 归一化后直接拼进公式
            name = _NAME_RE.sub("_", key)
            return {"type": "FORMULA", "expr": f"keys({name})"}
```
- 触发条件：同一合同类型里同时定义 `ct.input("a-b", ...)` 与 `ct.input("a_b", ...)`（或 `"钢 材"` / `"钢_材"`）。`input()` 的去重检查（`builder.py:476-477`）只比较原始字符串，两者都能登记；随后 `for_each(x)` 生成 `keys(a_b)`，引擎按顶层名字解析到**字面名为 `a_b` 的那个输入项**。
- 后果：循环遍历的是另一个清单/字典（数量与键完全不同），运行期无任何报错；反向同理，`item("a-b", var="a_b")` 会取错对象。`validate.py` 也拦不住：`a_b` 确实是一个已存在的输入项 key（`validate.py:806` 判定为已知名字）。
- 修复建议：`input()` 里维护 `{sanitize(key): 原始 key}` 并检测冲突；`for_each`/`item` 生成表达式时若 `sanitize(key) != key`，同时校验归一化后的名字唯一（或干脆拒绝非标识符 key，提示改用 `var` 别名）。
- 备注：`_NAME_RE` 不处理"以数字开头"（`ct.input("1mats", ...)` → `keys(1mats)`）与 Python 关键字（`ct.input("if", ...)` → `keys(if)`）→ 引擎词法/语法错误；`validate.py:448` 已有 `_VALID_IDENT_RE` 判定，可直接前移到 `input()`。

### [P3] E-13 `allowed=[]` 静默变成"不限制"，紧随其后的报错是死代码

- 位置：`backend/apps/contracts/builder/builder.py:515`
- 代码：
```python
        if allowed:
            names = [str(x).strip() for x in allowed if str(x).strip()]
            if not names:
                raise BuildError(f"输入项「{k}」的 allowed 不能为空列表")   # ← 永远不可达
            if itype == "infrastructureList":
                item["allowedInfrastructures"] = names
```
- 触发条件：`ct.input("infra", "基建", "infrastructureList", allowed=[])`（`[]` 为假 → 整段跳过）；`allowed=[None]` 之类会被 `names` 过滤成空列表 → 才走报错分支。
- 后果：本想表达"本清单一个都不许选"的模板变成"什么都能选"，引擎的 `_validate_list_filters`（`engine.py:1859-1864`）因 schema 里没有白名单而完全放行——一笔本应被拦住的合同顺利执行。
- 修复建议：`if allowed is not None:`，并保留空列表报错。

### [P3] E-14 `_branch_of()` 不校验布尔且 if/else 完全等价，`when=` 与 `when()` 的校验强度不一致

- 位置：`backend/apps/contracts/builder/builder.py:1001`
- 代码：
```python
def _branch_of(condition: Value) -> dict:
    """输入项的条件显隐：把布尔值源转成 `{when, cond}`。"""
    spec = _as_value(condition).to_spec()
    if spec.get("type") == "OP" and str(spec.get("op") or "").startswith("CMP_"):
        return {"when": "then", "cond": spec}
    return {"when": "then", "cond": spec}      # ← 两个分支一模一样
```
- 触发条件：`ct.input("x", "X", "number", when=number("1"))` 或 `when=buyer.field("cash")` → `branch.cond` 是数值/字段值源，而不是 `ct.when()` 要求的布尔值源（`_branch_condition_spec`，`builder.py:1063-1078`，会直接 `BuildError`）。
- 后果：非布尔 cond 交给引擎/前端各自解释真值（`engine.py:209-220 is_truthy` 之类），输入项的显隐行为不确定、且构建期毫无提示；if/else 等价是这段代码质量问题的直接证据。
- 修复建议：`_branch_of` 直接复用 `_branch_condition_spec`（两者产出的形状本来就一致），删掉死分支。
- 备注：[待确认] 引擎对 `branch.cond` 的具体判定路径未追（属 U05），故后果只写"行为不确定"。

### [P3] E-15 `build()` 产物浅拷贝：与构建器内部状态、调用方原始 JSON 共享嵌套对象

- 位置：`backend/apps/contracts/builder/builder.py:864`（另见 `effects.py:350`、`builder.py:255`）
- 代码：
```python
        effects = [dict(e) if isinstance(e, dict) else e for e in self._preserved_effects]
        effects.extend(e.to_spec() for e in self._root)
```
```python
# effects.py:350  —— _RawEffect.to_spec() 也是浅拷贝
    def to_spec(self) -> dict:
        return dict(self._spec)
```
- 触发条件：`p1 = ct.payload()`；对返回值做原地加工（导入前统一打补丁、脱敏、给 IF 分支补字段）：`p1["effects"][0]["cond"]["op"] = "CMP_LT"` 或 `p1["effects"][0]["then"].append(...)` → 共享的是同一个嵌套 `cond`/`then` 对象（`_RawEffect._spec` 与 `_node`），第二次 `ct.payload()` / `ct.build()` 会**带上这次修改**；`keep_effects(rows)` 的 `dict(e)` 同理，之后改 `rows[i]["then"]` 会改到产物里。
- 后果：同一构建器多次编译结果不一致、脚本里的原始 JSON 被库悄悄改动，排查时表现为"同样的代码两次导入结果不同"。
- 修复建议：`_RawEffect.to_spec()` 与 `build()` 里的 `dict(...)` 换成 `copy.deepcopy`（`values.Value.to_spec()` 已经用 `_deep_copy` 做对了，这里对齐即可）。

### [P3] E-16 `_require_key()` 的 `isalnum()` 放行中日韩/全角字符，与提示不符

- 位置：`backend/apps/contracts/builder/builder.py:977`
- 代码：
```python
    if not all(c.isalnum() or c in "-_" for c in key):
        raise BuildError(
            f"合同类型 key「{key}」含非法字符",
            hint="只允许字母、数字、连字符与下划线（它会出现在 URL 与文件名里）",
        )
```
- 触发条件：`ContractType("钢材销售", "…")` —— `"钢材".isalnum()` 实测为 `True`，`"１２３"`（全角数字）同样通过；而提示明确说"只允许字母、数字、连字符与下划线（它会出现在 URL 与文件名里）"。
- 后果：非 ASCII key 落库后可能在 URL 路由/静态文件名/日志检索处表现异常（具体后端校验规则在 `serializers.py`，属 U05，本次未读，故后果标不确定）。
- 修复建议：`re.fullmatch(r"[A-Za-z0-9_-]+", key)`。
- 备注：[待确认] 需要 B05/U05 交叉确认后端是否另有 key 校验；若后端也放行，本条应升级为"URL/文件名兼容性缺陷"。

### [P3] E-17 `Value`/`FieldRef` 覆写比较运算符返回非布尔对象，`if a >= b:` 恒为真

- 位置：`backend/apps/contracts/builder/values.py:245`（另见 `builder.py:169-179`）
- 代码：
```python
    def __ge__(self, other: Any) -> "Value":  # type: ignore[override]
        return self._cmp("GTE", other)
```
```python
# values.py:239  —— 返回的是 Value（没有 __bool__）
    def _cmp(self, comp_type: str, other: Any) -> "Value":
        return Value({"type": "OP", "op": _normalize_cmp_type(comp_type), "args": [...]}, ...)
```
- 触发条件：脚本作者把比较写进 Python 流程控制而不是 `ct.check(...)`：`if buyer.field("cash") >= amount: ct.sub_number(...)`。`Value` 未定义 `__bool__`，对象恒真 → 条件永远成立（`else` 永不执行）；`sorted()`/`max()`/`min()` 也会拿到 Value 参与比较。
- 后果：本应"资金不足则不扣款"的逻辑变成无条件扣款，且没有任何报错。
- 修复建议：给 `Value` 加 `def __bool__(self): raise BuildError("比较结果不能当 Python 布尔用，请用 ct.check(...) / with ct.when(...)")`（前提是库内部没有把 Value 用在真值判断上——检查后 `_as_value`/`_spec_to_expr` 都只做 `is None` 判断，安全）。
- 备注：同一类问题的另一半是纯风格（`__hash__ = object.__hash__`、`equals()` 命名），不单独列。

## 存疑/待确认

1. **[已核实为"非缺陷"，供 B05/U05 交叉引用]** `_TOTAL_QTY_OF[""] = "PART_TOTAL_QTY"`（`aggregates.py:124`）这类"类型信息缺失时的兜底"看起来危险，但 `engine.py:1742` 对 `MATERIAL_TOTAL_QTY / PART_TOTAL_QTY / PRODUCT_TOTAL_QTY / FUEL_TOTAL_QTY` 四个枚举**走同一段算法**，所以兜底不会算错；`expand()` 的类型缺失兜底（`aggregates.py:295`）同理只在清单名字查库口径上一致时才成立，本次未逐一验证 `compute_part_materials` 与 `compute_product_parts` 的差异，建议 B05 顺带确认。
2. **[引擎侧，非本批范围]** 任务清单里的这些维度，实现全部落在 `engine.py`（U05/B05 范围），本层只做枚举映射，未发现构建期可修的问题：空集合求和 / `total_qty` 无项（`compute_total_qty`，`engine.py:1256`）、分母为 0（`apply_op DIV` 在 `engine.py:1136-1139` 直接返回 0，不抛 ZeroDivisionError，但**构建期无法发现"除数可能为 0"**——建议本层加一条"x / y 且 y 可能是 0"的静态提示）、`warehouse_storage` 多仓库合并与 None（`engine.py:1408`）、`route_*` 无路径（`engine.py:1717-1724`）、`tech_prerequisites` 成环（`engine.py:1435` 的递归，本层 `tech_prerequisites()` 只是发枚举，构建期无环检测能力）、`expand` 笛卡尔积爆炸（同上）。
3. **[待确认]** `set_dict(target, {...})` 与 `add_dict(target, {...})` 的区分：本层文档说 `sub_dict` 保留键、`remove_keys` 删键（`effects.py:329-343`，与 `engine.py:410-425` 的数组/字典分支一致，**这部分实现是正确的**）。但 `set_dict` 在 DICTIONARY 字段上会**整体覆盖**（引擎 `op=SET → after = obj`），本层文档只在括号里写了一句"整体设定"，未提示"调用顺序在 `add_dict` 之后会抹掉之前的累加"——是否算缺陷取决于 `builder/validate.py` 是否对同字段多效果排序给出警告（B05 范围），本次未定性。
4. **[待确认]** 引擎的 `VALUE_COMPARE` 是否对 `value2` 为 `FIELD` 值源做求值：`_condition_spec`（`builder.py:1056`）在"左操作数非 FIELD、右操作数是 FIELD"时生成 `FIELD` 值源作 `value2`（例如 `ct.check(amount >= buyer.field("cash"))`）。若引擎只对 `value2` 做 `to_number(eval_value_spec(...))` 则语义正确；若走别的分支可能静默 0。引擎侧属 U05，本次未追。
5. **[不报为缺陷，但记录]** 输入项参与运算时 `None`/空串的行为：`const(None)`、`text("")` 都是合法 CONST，引擎 `to_number` 对 `None`/`""` 一律 fallback 0（`engine.py:181-188`），即"空值 = 0"是引擎既定口径；本层未提供任何"必填/非空"构建期校验（`required=True` 只写进 schema）。若 B05 的 `validate.py` 也未覆盖，建议补一条"参与算术的输入项应 `required=True`"的 WARNING。
6. **[不报为缺陷]** `EFFECT_KINDS`/`KINDS_BY_FIELD_TYPE` 里 `"ANY": tuple(EFFECT_KINDS)`（`effects.py:96`）会让 `KINDS_BY_FIELD_TYPE["ANY"]` 报错提示里列出全部 11 个效果名，属于提示噪音；`remove_keys` 单键时走 `LIST_CONCAT` 单参数路径（`effects.py:213-248`）经引擎 `as_list` 包装后形状正确，已核实无误。`_VarBlock`（`builder.py:956-971`）是 `_ForeachBlock` 的未使用副本（`for_each` 只返回 `_ForeachBlock`），属死代码，不单列。
```
