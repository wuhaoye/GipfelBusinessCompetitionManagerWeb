# 合同数据获取指南（处理函数里有什么可用）

> 适用对象：编写 `handlers.py` 处理函数的使用者。
> 数据来源：监听程序调用现有接口 `GET /api/contracts/:id` 获得合同详情后，
> 原样作为 `contract` 参数传给 `handle_<key>_passed(contract, ctx)`。
> 本指南描述的字段与服务器实际返回逐一对齐（上游代码 `8c0b321`，无任何改动）。

---

## 1. 一句话总览

`contract` 是一份字典，包含 **4 组数据**：

```
合同头（是谁、什么状态、何时执行）
   + 合同类型模板（这个合同"定义"了什么：角色/输入项/效果/检查）
   + 参与方（谁参与、各自的公司与合同编号）
   + 执行内容与结果（填了什么、通过了什么检查、落账改了哪些字段值）
```

## 2. 顶层字段（contract 直接键）

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `contractId`* | int | 合同 ID（注：接口中键名为 `id`，见下） |
| `id` | int | 合同 ID（接口原样键名） |
| `competitionId` | int | 所属比赛 ID |
| `contractTypeId` | int | 合同类型模板 ID |
| `name` | str | 合同名称（创建时自动取自模板名称） |
| `status` | str | `DRAFT` / `PENDING_EXEC` / `EXECUTED` / `TERMINATED`；监听程序只会把 `EXECUTED` 交给你 |
| `signedAt` | str\|null | 会签时间（ISO 8601） |
| `executedAt` | str\|null | **执行（通过）时间**——`EXECUTED` 合同必有 |
| `createdAt` / `updatedAt` | str | 创建 / 最后更新时间 |
| `parties` | array | 参与方清单（§4） |
| `inputs` | object | 实际填写的输入参数（§5） |
| `executionLog` | array\|null | 引擎逐笔执行记录（§6） |
| `executionResult` | object\|null | 引擎执行结果：落账字段 + 前置检查（§6） |
| `contractType` | object | 嵌套的合同类型模板（§3） |

> 实际接口返回的是驼峰键 `id`；为便于你阅读，本指南示例沿用接口原样键名。

## 3. contractType（模板定义 —— 该合同的"规则"）

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `id` | int | 模板 ID |
| `key` | str | **类型标识**（你注册处理函数所用的 key；改名后此处即新 key） |
| `name` | str | 类型名称 |
| `description` | str\|null | 描述 |
| `partyCount` | int | 参与方角色数 |
| `partyRoles` | array | 角色定义：`[{role, label?, selectable?, isHost?}]` |
| `inputSchema` | array | 输入项定义：`[{key, label, type, required?, default?, enum?, min?, max?}]`，特殊类型 `infrastructureList`/`vehicleList` 还含 `allowedInfrastructures`/`allowedVehicles` 白名单 |
| `effects` | array | 效果 DSL（合同通过后会执行什么改写）：`{kind: FIELD, party, fieldKey, op: ADD/SUB/SET, value…}` 及控制流 `IF/FOREACH/ASSIGN` |
| `conditions` | array | 前置检查 DSL：`VALUE_COMPARE/DICT_COMPARE/LIST_COMPARE/FIELD_COMPARE/INDUSTRY_IS` + `branch` 分支 |
| `graph` | object\|null | 可视化图结构（编辑器布局，通常不需要） |
| `schemaVersion` | int | DSL 版本 |
| `enabled` | bool | 是否启用 |
| `createdAt` / `updatedAt` | str | 模板创建/更新时间 |

## 4. parties（参与方 —— 你最初关心的数据）

`parties` 是数组，每个元素对应一个角色：

```json
[
  {"role": "甲方", "label": "甲方", "isHost": false,
   "companyId": 3, "companyName": "晨光制造", "contractNumber": "G-2026-001"},
  {"role": "主办方", "label": "主办方", "isHost": true,
   "companyId": null, "companyName": null, "contractNumber": null}
]
```

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `role` | str | 角色标识（与模板 partyRoles.role 对应） |
| `label` | str\|缺省 | 角色显示名（存储时可能有） |
| `isHost` | bool | 是否主办方：主办方**无公司、无编号** |
| `companyId` | int\|null | 实际参与公司 ID（仅非主办方） |
| `companyName` | str\|null | 公司名（接口已按 companyId 反查补全，主办方为 null） |
| `contractNumber` | str\|null | 会签合同编号；**EXECUTED 合同必填非空**（执行前强制校验），未执行可能为 null |

要点：`EXECUTED` 合同里非主办方都有 `companyId + companyName + contractNumber`，可直接按参与方归档/对账。

## 5. inputs（填写内容）

`inputs` 是一份对象：键 = 模板 `inputSchema[].key`，值 = 用户/执行时填写的值。值的类型取决于 inputSchema 定义：

```json
{
  "amount": 1000000,
  "buyerNode": [2, 5, 7],
  "materials": {"1": 10, "2": 3},
  "startDate": "2026-10-01"
}
```

- 标量：数值或字符串；数组/对象：输入项本身定义的列表/字典内容；
- 不存在或未填写的输入项**不会出现**（用 `inputs.get(key)` 而非 `inputs[key]`）。

## 6. 执行结果（落账与检查）

### 6.1 executionResult —— 本次执行"改了什么、检查结果"

```json
{
  "logs": [...],                                 // 与 executionLog 同源（引擎 log）
  "fields": {"3:cash": 500000, "7:carbon": "123.45"},   // "<companyId>:<fieldKey>" → 落账后的值
  "checks": [
    {"kind": "VALUE_COMPARE", "party": "甲方", "label": "金额下限", "passed": true,
     "actual": 1000000, "expected": 100000, "detail": "值1 ≥ 值2：通过"}
  ]
}
```

| 键 | 说明 |
| --- | --- |
| `fields` | 本次被执行合同改写的**公司字段最终值**（键为 `公司ID:字段key`） |
| `checks` | 每条前置检查的结果：`kind/party/label/passed/actual/expected/detail`，`passed=false` 的合同不会通过执行（能被监听程序收到说明全部通过） |

### 6.2 executionLog —— 逐笔效果明细（更细的 before/after）

```json
[
  {"kind": "FIELD", "companyId": 3, "fieldKey": "cash", "fieldName": "现金",
   "op": "SUB", "value": 500000, "before": 1000000, "after": 500000}
]
```

每个元素对应合同类型 `effects` 里的一笔 FIELD 效果：改写了**哪家公司（companyId）的哪个产业字段（fieldKey）**，从 `before` 到 `after`，本次变动量为 `value`。

## 7. 处理函数里怎么取值（handlers.py 示例）

```python
def handle_material_procurement_passed(contract: dict, ctx: dict) -> None:
    # 合同头
    cid, status, et = contract["id"], contract["status"], contract["executedAt"]
    # 类型（注册 key 与其一致）
    tkey = contract["contractType"]["key"]
    # 参与方：非主办方公司列表
    companies = [(p["role"], p["companyId"], p["companyName"], p["contractNumber"])
                 for p in contract["parties"] if not p.get("isHost")]
    # 填写内容
    amount = (contract["inputs"] or {}).get("amount")
    # 执行影响
    changed = contract["executionResult"]["fields"]       # {"3:cash": 500000}
    ops = contract["executionLog"]                        # 每笔 before/after
    # 落盘示例（或按你的逻辑处理）
    ctx["default_archive"](contract, ctx)
```

## 8. 注意：这些数据**拿不到**（请用替代方案）

| 数据 | 原因 | 替代 |
| --- | --- | --- |
| 每笔落账的服务端审计行 `ContractFieldEffect`（含 raw 文本与 created_at） | 无只读接口 | 用 `executionLog`（内容等价：companyId/fieldKey/op/value/before/after） |
| 公司当前**其它**字段现值（如余额除了被本次改动后的值） | 合同接口不含 | 另行调用 `GET /api/company-fields/<companyId>`（需对应权限） |
| "谁"执行的合同（操作者账号） | 合同对象不含操作者 | 以监听程序所在环境另行记录；或按需另查审计接口 |
| 金额精度 | 大额 Decimal 以字符串下发 | 字符串可直接原样归档；做算术请先用 `decimal.Decimal(str(v))` |

## 9. 常见误解速查

1. `parties` 是**数组**（每角色一项），不是对象；
2. 主办方（`isHost: true`）没有公司、没有编号——统计参与公司时记得过滤；
3. `inputs` 只含**实际填写过**的键；
4. `executionResult.fields` 的键是 `"公司ID:字段key"` 字符串，先 `split(":")` 再取 ID；
5. 未执行的合同 `executedAt` 为 null——监听程序只处理 `EXECUTED`，无需判断；
6. 金额类数值可能是字符串（如 `"123.45"`），归档可直接用，运算前转 Decimal。

---

## 10. 批量记账阶段的 ctx（`phase == "book"`，2026-09 新增）

处理函数会被调用两次：合同通过入库时 `phase == "collect"`（只有存档等非 Excel 行为），
真正写账本时 `phase == "book"`。`book` 阶段除 §7 的字段外，额外提供：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `phase` | str | `"book"`（批量记账）/ `"collect"`（合同入库） |
| `book` | xledit | **本批次共用的** Excel 会话；勿自行 `save()`/`quit()` |
| `add_entries` | callable | `add_entries(add=, minus=, number=, about=)` → 银行流水账 |
| `add_item` | callable | `add_item(thing, name, number, price, add, minus)` → 原材料/零件/商品 |
| `add_assets` | callable | `add_assets(type, name, add, minus)` → 资产/负债/损益科目 |
| `record` | dict | 该合同在本地 SQLite 里的行：`company_id / contract_id / competition_id / type_key / name / contract_number / amount / executed_at / payload_json / readable_json …` |
| `companyId` / `companyName` | int / str | 本次记账所属公司（**一本账本对应一家公司**） |
| `trigger` | str | `threshold`（达阈值）/ `fiscal_year_end` / `fiscal_year_start` / `manual` |
| `batchId` | int | 本次记账批次号（对应 `data/watcher.db` 的 `batches.id`） |
| `out_dir` / `typeKey` / `default_archive` | 同 collect 阶段 | 输出目录 / 类型 key / 通用存档函数 |

示例（按金额记一笔银行存款，同时更新资产科目）：

```python
def handle_material_procurement_passed(contract: dict, ctx: dict) -> None:
    if ctx.get("phase") != "book":
        return
    from decimal import Decimal
    amount = Decimal(str((contract.get("inputs") or {}).get("amount") or 0))
    number = ctx["record"].get("contract_number") or f"#{contract['id']}"
    ctx["add_entries"](add=amount, minus=Decimal(0), number=number, about=contract["name"])
```

要点：
- **同一批次只开一次 Excel**：不要在处理函数里 `xledit(...)`；
- 计数与金额都用 `Decimal(str(v))`，别用 `float`（`shang.py` 内部落 Excel 时才转 double）；
- 处理函数抛异常 ⇒ 本批次不保存、合同保持未记账、60 秒内不自动重试（GUI 可强制重试）；
- 没有可用记账规则的合同会被标记为「已记账（无分录）」，不会反复触发 Excel。

