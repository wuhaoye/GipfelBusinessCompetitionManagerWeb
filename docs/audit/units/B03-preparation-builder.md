# B03 比赛建包库（分支归属：feature/contract-type-code 引入，test / feature/contract-watcher 含）

## 概述

审计对象（提交 73e8e46 新增，master 没有）：

- `backend/apps/preparation/builder/__init__.py`（78 行，导出面）
- `backend/apps/preparation/builder/core.py`（1657 行，`CompetitionBuilder` + 35 类资源登记方法 + `validate()` / `resolve_field_ids()`）
- `backend/apps/preparation/builder/schema.py`（599 行，自描述列定义 + `verify_rows()`）
- `backend/apps/preparation/builder/types.py`（182 行，`BuilderError` / `Ref` / `resolve_ref` / `refs_of` / `positive_int` / `require_text` / 计算图助手）
- `backend/apps/preparation/management/commands/build_competition.py`（346 行，唯一落库入口）
- `backend/apps/preparation/tests/test_builder.py`（527 行，作为设计意图与"已钉住语义"的证据）
- `backend/examples/competitions/demo_competition.py`（294 行，官方模板；`auto_chain_*.py` 未看）

方法：只读审计（read/grep）+ 纯 Python 计算验证（`str(Ref)`、`str.strip()` 边界），**未运行 `build_competition`，未连接数据库**。为判定"产物落库后的真实后果"，只读引用了 `apps/preparation/archive.py`（B02 范围）与少量模型定义作为**证据**，缺陷结论一律落在本范围文件上。

架构事实（用来界定责任）：

- 本库只产出归档 JSON，落库全部复用 `archive.apply_import`（`core.py:1-49` 的设计声明）。
- `RESOURCE_ORDER` 与 `archive.IMPORT_ORDER` 在 `import` 时强校验（`core.py:205-245`），35 类资源顺序一致 —— 这一条做得很扎实，未发现问题。
- 资源主记录 id 由 `_next_id()` 从 **1** 开始单调分配（`core.py:327-329`），子记录共用同一计数器，不存在 0 号 id 与负数索引问题。
- 公司/区域/节点等名称以 `require_text()` 去空白后做**精确查重**，同名二次登记会立即抛 `BuilderError`（`core.py:337-342`）——"同名登记两次静默丢数据"在本库不成立（子资源除外，见 N-06/N-09/N-16）。

已核查但**未发现**问题的维度（避免与本报告其它条目混淆）：

1. 枚举与模型 `choices` 完全一致：`FIELD_TYPES` vs `IndustryField.TYPE_CHOICES`、`USER_ROLES` vs `User.ROLE_CHOICES`、`WAREHOUSE_TYPES`/`MATERIAL_TYPES`/`CONTRACT_STATUSES`/`COMPANY_STATUSES`/`COMPETITION_STATUSES`/`TIMER_TRIGGERS` 均逐一对齐，不存在"库内合法值被本库拒绝"或反之。
2. 列名自描述一致：`build()` 产出的每一个列名都能在 `schema.py` 找到（`verify_rows` 无漏报），`schema.py` 声明的列名也与 `archive._exp_*` 的 camelCase 口径一致（抽查 `mapNodes.region`、`materials.nodePricesByName`、`messages.targetUserIds`、`stockFundsAccounts.username`、`overviewCards.regionName/cards`、`contractInstances.contractTypeKey` 等）；`RESOURCE_SCHEMA` 键集合 == `RESOURCE_ORDER`。
3. `--dry-run` 确实不写库：命令把 `dry_run=True` 透传给 `apply_import`（`build_competition.py:280`），后者在 `transaction.atomic()` 内执行并 `set_rollback(True)`，无事务外副作用。
4. 命令不打印密码/密钥：`_print_summary` / `_print_result` 只打印资源标签、计数、`notes`/`problems`（账号相关 notes 只含用户名，归档本身不含密码），未发现日志泄漏敏感信息。
5. 无硬编码账号**字面量**（`user=None` 见 N-08 的账号代发问题；`settings.SEED_ADMIN_USERNAME` 默认 `admin` 属既有播种逻辑，不在本范围）。

缺陷清单按 P0→P3 排序（本次无 P0）。

## 缺陷清单

### [P1] `account(username=...)` 产出的 USER 资金账户永远解析不到归属，且全程零提示

- 位置：`backend/apps/preparation/builder/core.py:1214`（登记侧）/ `backend/apps/preparation/archive.py:2242`（落库侧，仅作证据）
- 代码：
```python
        else:
            uname = require_text(username, "账户归属用户名")
            self._require("users", uname, "账户归属用户")
            row["ownerType"] = "USER"
            row["username"] = uname
        return self._register("stockFundsAccounts", aname, row)
```
- 触发条件：按官方文档与官方模板使用——`demo_competition.py:268` 就是 `b.account("甲钢铁操盘手备用金", username=p_a.name, cash_balance="200000")`；只要脚本走 `username=` 分支即可复现。
- 后果：本库只写 `ownerType=USER` + `username`，从不写 `userId`；落库侧 `user_id = ctx.ref.resolve("users", row.get("userId"), name=row.get("username"))` 中 `_REF_FALLBACKS`（`archive.py:333-346`）**没有 "users" 这一项**，`RefResolver.resolve` 在 `spec is None` 时直接 `return None`（`archive.py:382-384`），因此 `user_id` 恒为 `None`；而 `StockFundsAccount.user_id` 是 `IntegerField(null=True)`（`apps/stock/models.py:73`）无约束，于是**静默创建了一个"归属类型=USER、归属为空"的资金账户**。CLI 报告里没有 problem、退出码 0，玩家侧表现为"账户存在但看不到/用不了"。`validate()` 对这个已知的导入顺序陷阱（账号排在 `stockFundsAccounts` 之后，见 `RESOURCE_ORDER` 第 160/165 项）没有任何提醒，而同类问题的消息路径至少写了一行 note。
  根因在导入引擎（`_REF_FALLBACKS` 缺 `users` + 顺序），属 B02 范围；**本范围侧的缺陷是"文档承诺可用 + 强制先 `user()` 登记 + 零校验零告警"**，等于把用户主动送进这个坑。
- 修复建议：短期在 `validate()` 增加硬告警并同步文档（USER 类型账户必须"先单独导入账号、再单独导入资金账户"两次导入），或直接拒绝 `username=` 分支；根治需在 `archive._REF_FALLBACKS` 增加 `users`（按 username 查库兜底）或把 `users` 调到 `stockFundsAccounts` 之前。

### [P2] 子资源行缺少 `*Id` 列，追加模式的"父记录已保留则跳过子行"保护对建包产物完全失效（既有配比被静默改写）

- 位置：`backend/apps/preparation/builder/core.py:860`（以及 `895`/`928`/`970`/`666` 同类写法）
- 代码：
```python
        self._add_extra(
            "partMaterials",
            {
                "partName": p["name"],
                "materialName": m["name"],
                "ratio": self._num(ratio, "配比系数"),
            },
        )
```
- 触发条件：目标比赛里已存在同名零件（例如裁判在界面里调过配比、或上一次导入已经建过），再以默认 `--mode append` 导入本库产物。落库侧 `_CHILD_OF`（`archive.py:1525-1538`）用 `partId`/`productId`/`vehicleId`/`fromNodeId`/`toNodeId`/`contractTypeId` 判断"父行被 kept"，`_filter_rows_for_mode` 因此取到 `row.get("partId") is None` → `IdMap.kept()` 对 `None` 直接返回 False（`archive.py:317-324`）→ 子行**不被过滤**。
- 后果：
  1. `partMaterials` / `productParts` 走到 `get_or_create` 的"已存在"分支后执行 `obj.ratio = row.get("ratio") or 0; obj.save()`（`archive.py:1948-1949`），即**追加模式下把既有配比改成脚本里的系数**；CLI 的 append 契约是"已存在的同键记录原样保留（含其关联数据）"，且报告里零件显示"保留 N"，操作者会以为什么都没动。对比：本库对 `companyFieldValues`（`core.py:586`）与 `techPrerequisites`（`core.py:768`）是写了父 id 的，说明保护机制本可生效，只是这里漏了。
  2. 语义不一致：同一份数据走"导出产物"（`_exp_*` 全部产出 `partId`/`fromNodeId`/`contractTypeId`…）在 append 下会被保护，走"建包产物"则不会。`test_builder.py:266` 甚至把"append 下合同实例必被跳过"钉成引擎语义，但那只对导出产物成立——建包产物在 append 下**会**创建合同实例，测试与文档没覆盖这个差异。
  3. 测试里的 `IMPORTER_ONLY_COLUMNS`（`test_builder.py:42-51`）把这些 id 列注释为"可选、取不到时退化为按名解析"，这个假设是错的：它们同时是 `_CHILD_OF` 的输入，不是纯可选。
- 修复建议：在登记子记录时用已分配的父行 `_id` 补 `partId`/`productId`/`vehicleId`/`fromNodeId`/`toNodeId`/`pathTypeId`/`contractTypeId`（父行 `_id` 现成可用，例如 `add_material_ratio` 里的 `p["_id"]`），并收紧 `IMPORTER_ONLY_COLUMNS`；同时补一条"append 模式对既有父记录的子行不得改写"的回归测试。

### [P2] 数值列只做类型白名单，非数字字符串/空串一路通过构建期与 `--inspect`，到导入时才炸

- 位置：`backend/apps/preparation/builder/core.py:311`
- 代码：
```python
    @staticmethod
    def _num(value: Any, field: str) -> Any:
        """数值列：原样透传（int / float / str 均可，字符串可保 Decimal 精度）。"""
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise BuilderError(
                f"{field} 必须是数字或数字字符串，收到 {type(value).__name__}: {value!r}"
            )
        return value
```
- 触发条件：任何"看起来像数字"的手滑输入，例如 `b.stock("600001", "甲钢铁", total_shares="1.2万股")`、`b.warehouse("原料仓", "MATERIAL", capacity="1万")`、`b.line("一号线", price="")`、`b.infrastructure("电厂", price="120万元")`。`_num()` 只检查 `isinstance(..., str)`，不解析、不判空。
- 后果：`--inspect` / `--out` / `build()` **全部通过**（产物 JSON 合法），错误被推迟到导入：`Stock.total_shares` 是 `DecimalField(max_digits=60, decimal_places=4)`（`apps/stock/models.py:18`），Django 在 `get_db_prep_save` 阶段抛 `ValidationError`，被 `apply_import` 的"单资源失败不中断整批"逐资源 `except` 捕获（`archive.py:2670-2674`），于是该资源**整批 skipped**、problem +1、CLI 退出码 1；但事务不回滚，前面已导入的公司/区域/科技等照常提交 → **半成品比赛需要人工清理**。这正好违反本库自己写的"写错立即报错而不是导入时才失败"。注意同一份脚本里 `float(...)` 站点（`node(x=)`、`vehicle(max_cargo=)`）反而会在构建期抛 `ValueError`，错误类型与时机都不统一。
- 修复建议：`_num()` 增加"字符串必须能被 `Decimal` 解析且有限"的校验（`Decimal(value)` 失败 → `BuilderError`，含定位信息如资源名+行名），并把 `float(...)` 站点统一改为 `_num` 语义（非 BuilderError 的 `ValueError`/`TypeError` 一律包装成 `BuilderError`）。

### [P2] CLI 全链路丢弃 `validate()` 的体检提醒，`--inspect` 的文档承诺未实现

- 位置：`backend/apps/preparation/management/commands/build_competition.py:142`（`--inspect` 帮助文案在同文件 `105-107`）
- 代码：
```python
        if options["inspect"]:
            self._print_summary(archive)
            if not options["out"]:
                return

        competition_id = options["competition"]
        if competition_id is None:
            if options["inspect"] or options["out"]:
                return  # 只产出不导入
```
- 触发条件：`python manage.py build_competition examples/competitions/demo_competition.py --inspect`（官方模板本身就带 4 张 `industryFieldId=0` 的占位卡片，`demo_competition.py:169-172`）。
- 后果：`_print_summary()` 只打印资源数量（`build_competition.py:236-248`），全文件没有任何地方调用 `builder.validate()`；`build()` 内部虽然调了 `self.validate()`，却把返回的提醒**直接丢弃**（`core.py:1374`）。因此本库刻意设计的六类"软陷阱"提醒——卡片 `industryFieldId` 仍是 0、地图孤立节点、产业类型缺 `location`、科技前置成环、载具无可通行路径、消息指定收件人——在任何 CLI 路径（`--inspect` / `--out` / 真实导入）都不会显示给使用者；只有单元测试和 `auto_chain_*.py`、`build_from_sheets.py` 这类脚本自己调 `validate()` 才看得到（grep 全仓仅此几处）。用 `--inspect` 按文档做"体检"会得到"一切正常"的假象，然后导入一批取不到值的总览卡片。
- 修复建议：在 `handle()` 里对脚本来源调用一次 `validate()` 并把提醒打印到 stdout（`--inspect` 至少打印；真实导入建议打印到 stderr 或加 `--strict` 让提醒可升级为失败），或在 `build()` 里把 `warnings` 一并放进归档元数据。

### [P2] `add_tech_requirement()` 用字符串名称时，同名零件/产品会静默挂到零件上（产品的科技门槛消失）

- 位置：`backend/apps/preparation/builder/core.py:909`
- 代码：
```python
        resource: str | None = None
        name = ""
        for candidate in ("parts", "products"):
            try:
                candidate_name = resolve_ref(item, candidate, "零件或产品")
            except BuilderError:
                continue
            if candidate_name in self._by_name[candidate]:
                resource, name = candidate, candidate_name
                break
        if resource is None:
```
- 触发条件：包内同时存在同名零件与同名产品（供应链比赛里很常见，例如零件"粗钢坯"与产品"粗钢坯"），并且按 `schema.py:326`/`355` 宣传的 `add_tech_requirement(part, tech_node)` 语义以**名称**调用：`b.add_tech_requirement("粗钢坯", t_roll)`。
- 后果：循环固定按 `("parts", "products")` 顺序探测，**先命中 parts 就 break**，于是"给产品加科技需求"被静默登记成 `partTechRequirements`；产物的 `productTechRequirements` 缺这一条，导入后该产品**不需要研发对应科技即可生产**（游戏规则被绕过），而本库既不报错也不提醒（传 `Ref` 时能正确区分，所以 `product(..., tech=[...])` 内部路径是安全的，问题只在文档同样允许的名称写法上）。
- 修复建议：探测到"两个候选都存在"时直接抛 `BuilderError`（要求调用方传 `Ref` 消歧），或在签名上显式区分 `add_part_tech_requirement` / `add_product_tech_requirement`。

### [P3] 同一合同类型登记多份合同，导入后只落一份，`name` 列被完全忽略

- 位置：`backend/apps/preparation/builder/core.py:1073`
- 代码：
```python
        ct_key = self._contract_type_key(contract_type)
        ct_row = self._by_name["contractTypes"][ct_key]
        cname = require_text(ct_row.get("name") or ct_key, "合同名称") if name is None else require_text(name, "合同名称")
        dedup = f"{ct_key}\u0000{cname}"
        if dedup in self._by_name["contractInstances"]:
            raise BuilderError(
                f"合同「{cname}」（类型 {ct_key}）重复登记；同一合同类型下名称必须唯一"
            )
```
- 触发条件：`b.contract(name="钢材销售合同-A", contract_type=ct)` 与 `b.contract(name="...-B", contract_type=ct)` 各登记一次（本库按 `(类型, 名称)` 查重，允许通过）。
- 后果：落库侧查重与建名都用**合同类型名**而不是归档里的 `name`（`archive.py:2168-2176`：`name=ct.name if ct else (row.get("name") or "合同")`），第二份走"已存在"分支只 `bump skipped`、不报 problem、退出码 0 → 操作者以为预置了两份合同，实际只有一份。更麻烦的是 `schema.py:431-437` 给出的规避建议"确需多份时请给 contract(name=...) 传不同名字"**是无效的**（同一类型永远只会有一行），照文档做仍然丢数据。
- 修复建议：`contract()` 对"同一 `contract_type` 的第二份实例"直接抛 `BuilderError`（或在 `validate()` 里出提醒），并删除/改写 `schema.py` 那条无效建议。

### [P3] 消息"指定收件人"的告警是死代码（检查了错误的键），校验永远不触发

- 位置：`backend/apps/preparation/builder/core.py:1521`
- 代码：
```python
        # 7) 消息指定收件人的导入顺序限制
        if _USERS_IMPORTED_AFTER_MESSAGES:
            targeted = [r["title"] for r in self._extra["messages"] if r.get("targetUserIds")]
            if targeted:
                warnings.append(
                    f"有 {len(targeted)} 条消息使用了指定收件人，但账号在导入顺序中排在消息之后，"
                    "单次导入无法落地收件人（导入侧会提示）；如需保留，请先导入账号再单独导入消息"
                )
```
- 触发条件：`b.message("通知", to_users=["player_a"])`（`message()` 把收件人存成 `targetUsernames`，`core.py:1300`），然后调用 `validate()` 或 `build()`。
- 后果：`self._extra["messages"]` 里的行**从来没有** `targetUserIds` 键——该键只在 `_rows_of("messages")` 输出阶段临时生成（`core.py:1419-1425`）。因此 `targeted` 恒为空列表，这条提醒永远不出现：用户以为"指定收件人"没问题，导入后收件人被清空、消息只对全体/无人可见，唯一的安全网是导入引擎的一行 note。叠加 N-04，这条提醒即便修好也不会被 CLI 打印。
- 修复建议：改判 `r.get("targetUsernames")`，并加一条单测（`validate()` 必须对 `to_users=` 产出提醒）——现有测试只覆盖了环、孤立节点、载具路径、卡片占位四类提醒。

### [P3] 消息发布者不做校验，且导入时账号尚未建立 → 发布者被静默改写成"任一超管"

- 位置：`backend/apps/preparation/builder/core.py:1301`（落库侧 `archive.py:2309-2332`、CLI `user=None` 见 `build_competition.py:284`）
- 代码：
```python
        if unames:
            row["targetUsernames"] = unames
        if sender:
            row["senderUsername"] = require_text(sender, "消息发布者用户名")
        self._add_extra("messages", row)
        return self
```
- 触发条件：`b.user("referee", ...)` + `b.message("开局公告", sender="referee")`（最自然的写法：账号在本包里新建），或 `sender` 写成不存在的用户名（本库不校验，与 `to_users` 的 `_require()` 形成反差）。
- 后果：`users` 在 `IMPORT_ORDER` 里排在 `messages` 之后，落库侧 `User.objects.filter(username=sender_name).first()` 必然查不到（同一次导入里账号还没建），于是只记一条 note 并把发布者换成"当前操作账号"；而命令行导入固定传 `user=None`，落库侧回退到 `User.objects.filter(role="SUPER_ADMIN").first()`——在播种过 `admin` 的库上就是那个不知名的超管账号（`apps/auth/bootstrap.py:25`）。玩家看到的公告作者是 admin，审计上也无法追溯到底是谁导的包；全程只有 note、没有 problem、退出码 0。
- 修复建议：`message(sender=...)` 与 `to_users` 一样要求账号已在本包登记，并在 `validate()` 里提醒"本包新建账号不能在单次导入中作为发布者/收件人"；CLI 侧建议显式提供 `--as <username>`（否则打印"消息将以 <x> 名义发布"的提示）。

### [P3] `messages` 既无包内查重也无幂等键，按官方模板的"两次导入"流程会重复生成公告

- 位置：`backend/apps/preparation/builder/core.py:1294`
- 代码：
```python
        row: dict[str, Any] = {
            "title": t,
            "content": str(content or ""),
            "targetsAll": bool(to_all),
        }
```
- 触发条件：`demo_competition.py:282-294` 推荐的流程——先导入一次建出全局字段，导出回填 `industryFieldId` 后再导入一次（`--allow-non-empty`，任何 mode 均可）；或同一脚本重复导入同一比赛。
- 后果：`_add_extra("messages", ...)` 不做任何去重，落库侧 `Message.objects.create(...)` 无条件新建（`archive.py:2346`），`Message` 也没有唯一约束 → 每次导入都新增一批标题/正文完全相同的公告，玩家端出现重复消息；而 CLI 的 append 文案承诺"已存在的保留不动"。本库对 `partMaterials`/`mapEdges`/`techPrerequisites` 都写了查重，唯独 `messages`（以及 `companyFieldValues`、`consumerDemands`）没有，标准不统一。
- 修复建议：`message()` 增加"同标题+同收件人重复登记"的包内查重，并在 `validate()` 提示"消息无幂等键，重复导入会重复创建"（或在文档里改为"消息单独分组、只导一次"）。

### [P3] `--resources ""` / `--resources ","` 被静默当作"未指定"，降级为全量导入

- 位置：`backend/apps/preparation/management/commands/build_competition.py:298`
- 代码：
```python
    def _parse_resources(self, raw) -> set[str] | None:
        if not raw:
            return None
        items = {x.strip() for x in str(raw).split(",") if x.strip()}
        unknown = sorted(r for r in items if r not in archive_builder.IMPORT_ORDER)
        if unknown:
            raise CommandError(f"未知资源名：{', '.join(unknown)}")
        return items or None
```
- 触发条件：`--resources=""`、`--resources ","`，或 `--resources "$LIST"` 而 shell 变量为空/只含逗号（CI 里很常见）。
- 后果：`return items or None` → `None` → `apply_import(only_resources=None)` 表示"归档里有什么就导什么"，即**一次空值输入变成对目标比赛的全量写入**（若配合 `--allow-non-empty` 还会覆盖/追加既有数据），用户本意是"什么都不导"或"少导一点"。这是"参数解析边界"里唯一会放大写库范围的路径。
- 修复建议：区分"未提供"与"提供了但解析为空"（用 `options.get("resources") is not None` 判断），空值直接 `CommandError("--resources 不能为空")`。

### [P3] 文档承诺的退出码 2（导入被拒绝）从不出现，2 实际只来自 argparse

- 位置：`backend/apps/preparation/management/commands/build_competition.py:39`（文档行）/ `:286`（实现）
- 代码：
```python
        except archive_builder.ArchiveError as e:
            raise CommandError(str(e))

        if not options["dry_run"]:
            self._print_summary(archive)
        self._print_result(result)

        problems = result.get("problems") or []
        if problems:
            # 有问题意味着部分数据没落地，用非零退出码让脚本/CI 能发现
            sys.exit(1)
```
（docstring 第 39 行原文：`退出码：0 成功；1 脚本/构建错误；2 导入被拒绝（如目标比赛非空且未加 --allow-non-empty）。`）
- 触发条件：对已有业务数据的比赛执行不带 `--allow-non-empty` 的导入（CI 里按约定判断 `$? -eq 2` → "被拒绝，需要加 flag"）。
- 后果：非空拒绝走 `raise ArchiveError → CommandError`，Django 以 **1** 退出；dry-run 被 blocked 时走到 `sys.exit(1)` 也是 1。真正的 2 只可能来自 argparse 的用法错误（未知参数/`--competition abc`）。于是 CI 会把"用法写错"误判为"比赛非空被拒"，反之亦然，自动化流程会做出错误决策。
- 修复建议：要么实现 `sys.exit(2)`（在 `_import` 的 blocked 分支），要么把文档改成"1 = 脚本/构建/导入被拒绝"，并让 CI 依据 stderr 文本或 `--json` 输出判断。

### [P3] 四套范围列把**公司名**写进声明为"公司旧 id 数组"的列，只靠导入侧按名兜底才生效

- 位置：`backend/apps/preparation/builder/core.py:1343`
- 代码：
```python
    def _set_scope(self, user_ref: Ref, field: str, names_field: str, scopes: Any) -> None:
        names = refs_of(scopes, "companies", f"账号「{user_ref.name}」的 {field}")
        for name in names:
            self._require("companies", name, f"账号「{user_ref.name}」的 {field}")
        if not names:
            return
        row = self._require("users", user_ref.name, "账号")
        row[field] = names
        row[names_field] = names
```
- 触发条件：任何 `b.user(..., company_scopes=[a_steel])` 用法（官方模板每行账号都这么写）。
- 后果：`companyScopes` 按 `schema.py:519-527` 的声明是"公司管理范围（公司旧 id 数组）"，`companyScopeNames` 才是"对应的公司名"；但实现把**同一个 list 对象**塞进两列。当前落库侧 `_map_company_scope_ids` 是"先按 id、再按同下标名称兜底"（`archive.py:2357-2372`），所以碰巧能解析成功——即该格式错误被导入侧的容错掩盖。任何按 schema 语义消费归档的第三方（导入预览、审计/对拍工具、将来收紧解析的导入引擎）都会把公司名当旧 id 解析失败并丢弃范围（玩家"登录后什么都看不到"）；且两列共享同一对象，调用方后续 `list.append` 会同时污染两列。
- 修复建议：`row[field] = [c["_id"] for c in 公司行]`、`row[names_field] = names`（分别用 `list(...)` 拷贝），并在 `_set_scope` 里顺手取回公司行以拿 `_id`。

### [P3] `region`/`origin` 文本列不对已登记区域做一致性校验，传 `Ref` 会被静默字符串化成 `"Ref(regions:东区)"`

- 位置：`backend/apps/preparation/builder/core.py:630`（同类：`704` 的 `origin`、`989` 的 `demand(region=...)`）
- 代码：
```python
        return self._register(
            "mapNodes",
            name,
            {
                "nodeTypeName": type_name,
                "region": str(region or ""),
                "x": float(x or 0),
                "y": float(y or 0),
            },
        )
```
- 触发条件：`east = b.region("东区")` 之后写 `b.node("东区港", port, region=east)`、`b.material("铁矿石", origin=n_port)`、`b.demand(east, prod, 100)`——本库其它方法（`company(region=...)`、`edge()`、`set_node_prices()`）都接受 `Ref`，用户很容易顺手把 `Ref` 传进这些文本列。
- 后果：`Ref` 只定义了 `__repr__`（`types.py:38-39`），`str(ref)` 得到调试串 `"Ref(regions:东区)"`（已用纯 Python 验证），于是数据库里落一个**看起来像对象转储的区域名**：区域总览按该文本聚合（`schema.py:197`）→ 节点/需求不属于任何区域，运费/总览/需求静默失联；本库既不报错也不提醒（`region` 是文本列，且 `node()`/`demand()` 都只校验"非空"）。此外区域名打错（"东区港"写成节点名）同样无人发现——而 `node()` 对 `node_type()` 是做了存在性校验的，标准不一致。
- 修复建议：对文本区域列接受 `Ref`/名称两种写法并统一 `resolve_ref(..., "regions", ...)`，再 `_require("regions", ...)`（或至少在 `validate()` 里对"不在本次登记区域集合内"的 region/origin 出提醒）。

### [P3] `build()` 返回的是构建器内部行对象（浅拷贝），后续登记会"回改"已拿到的归档

- 位置：`backend/apps/preparation/builder/core.py:1256`（根因 `1436` 的 `return list(self._primary[resource]) + list(self._extra[resource])`）
- 代码：
```python
        if zone is not None:
            card_row["zone"] = zone
        owner = self._extra_row_by("overviewCards", "regionName", r["name"])
        if owner is None:
            owner = self._add_extra("overviewCards", {"regionName": r["name"], "cards": []})
        owner["cards"].append(card_row)
        self._cards.append((r["name"], card_row, it_name, key))
```
- 触发条件：`a1 = b.build()`（或 `b.rows("overviewCards")`）之后继续 `b.card(...)` / `b.add_field_value(...)` / 修改 `a1` 里的行。
- 后果：`_rows_of()` 只做 `list()` 外层拷贝，元素仍是内部 dict；`_register`/`_add_extra` 也只做 `dict(row)` 浅拷贝，嵌套结构（`cards` 列表、`config`、`calcGraph`、`parties`）与调用方/内部状态共享同一对象。因此 `a1["resources"]["overviewCards"]["rows"][0]["cards"]` 会在后续登记后**凭空变长**；`rows()` 的文档写的是"取某资源的全部行（副本）"，实际只是浅拷贝。若脚本把 `build()` 结果缓存起来做 diff、或先 `save()` 再继续登记后复用同一个 dict 内存对象，会出现"内存里的归档与磁盘文件不一致"的诡异现象。
- 修复建议：`_rows_of()` 返回 `copy.deepcopy`（或至少对 `cards`/`parties`/`config` 等嵌套结构做深拷贝），并把 `rows()` 的注释改准；`__init__` 的 `map_background`/`stock_config` 也应深拷贝一份，避免外部对象后续被改。

### [P3] `stock_config` 只校验"非空字典"，键名拼错/取值非法被静默忽略；构造器还绕过了这一层校验

- 位置：`backend/apps/preparation/builder/core.py:411`（构造器侧 `284-289`）
- 代码：
```python
        if not isinstance(config, dict) or not config:
            raise BuilderError("stock_config 需要非空字典（不设置即沿用系统默认）")
        self.stock_config = dict(config)
        return self
```
- 触发条件：`b.stock_config_set({"limit_pct": 0.1, "max_move_pct": 0.08})`（键名与 `DEFAULT_STOCK_CONFIG` 不一致）、`{"limitPct": "10%"}`，或 `CompetitionBuilder("X", stock_config={})` / `stock_config=["limitPct"]`（`__init__` 完全不做校验，空 dict 与非 dict 都能通过）。
- 后果：落库侧只做 `isinstance(cfg, dict) and cfg` 判断（`archive.py:1294-1297`），引擎侧 `resolve_stock_config()` 用 `merged.update(input_)` 合并默认值（`apps/stock/engine.py:382-388`），**未知键被无声丢弃、缺失键回退默认**。于是"我想把单轮限幅改成 5%"这类规则调整可能完全没有生效，比赛按默认参数开跑，而构建期、导入期、退出码全部正常（构造器传入非法类型时更彻底：`stockConfig` 资源显示"跳过 1"，无 problem）。对商赛来说"规则与脚本意图不符且无人知晓"比直接报错更危险。
- 修复建议：用 `apps.stock.engine.DEFAULT_STOCK_CONFIG` 的键集合做白名单校验（未知键 → `BuilderError`），数值做类型/范围校验（`limitPct`/`maxMovePct` ∈ (0,1]、`mmMinQty ≤ mmMaxQty`），并让 `__init__` 复用 `stock_config_set()` 的校验逻辑。

### [P3] `card()` 不查重：同一区域+公司+字段重复登记会产生同 id 的重复卡片

- 位置：`backend/apps/preparation/builder/core.py:1250`
- 代码：
```python
        card_row: dict[str, Any] = {
            "id": card_id or f"{r['name']}-{c['name']}-{key}",
            "displayName": display_name or key,
            "companyName": c["name"],
            "industryFieldId": int(self._field_ids.get((it_name, key), 0)),
        }
```
- 触发条件：`b.card(east, a_steel, "cash")` 调用两次（例如写在循环里、或脚本合并时重复），或显式传相同的 `card_id=`。
- 后果：`owner["cards"].append(...)` 无条件追加，同一 `regionName` 行里会出现 `id` 完全相同的两张卡片；`Region.overview_cards` 是前端按数组渲染的 JSON，重复 id 会造成渲染/绑定歧义，而股票的 `carbonFieldRef`/`happinessFieldRef` 正是按 `{region, cardId}` 引用卡片（`schema.py:457-459`），重复 id 让"这张卡"失去唯一指向。本库对节点、连线、配比都做了查重，唯独卡片没有。
- 修复建议：登记前按 `(regionName, card_id)` 查重（重复即 `BuilderError`，或同 id 时覆盖并提示），并在 `validate()` 里检查 `_cards` 内 id 唯一。

### [P3] 重名检测只做 `strip()`，零宽字符/BOM 造成的"视觉同名"可以重复登记

- 位置：`backend/apps/preparation/builder/types.py:108`
- 代码：
```python
def require_text(value: Any, field: str) -> str:
    """必填文本（去空白后不能为空）。"""
    if value is None:
        raise BuilderError(f"{field} 为必填项")
    text = str(value).strip()
    if not text:
        raise BuilderError(f"{field} 不能为空")
    return text
```
- 触发条件：名字里带从网页/Excel 复制来的不可见字符，例如 `b.company("甲钢铁")` 与 `b.company("甲钢铁\u200b")`、`"甲钢铁\ufeff"`（纯 Python 验证：`str.strip()` 能去掉普通空格和全角空格 U+3000，但**不会**去掉 U+200B 与 U+FEFF，两者判定仍不相等）。
- 后果：查重键（`core.py:337-342` 的 `self._by_name[resource][text]`）认为这是两个不同名字，登记全部通过；落库后（SQLite 的 TEXT 默认 BINARY 比较）也确实建出两家在界面上**看起来一模一样**的公司/两个同名地图节点。之后所有"按名兜底解析"（公司字段值、卡片、合同参与方、载具、账号范围、股票关联）都可能在两个视觉同名实体之间解析到不确定的那个，玩家的字段/股票/合同挂错主体。
- 修复建议：`require_text()` 里剔除 `Cf` 类不可见字符（或做 `unicodedata.normalize("NFKC", text).strip()`）后再登记，并在查重时按归一化后的键比较（错误信息里回显原始输入以便定位）。

### [P3] `edge()` 的距离不做范围校验：0/负数/NaN/Inf 直接落库，负数会破坏 Dijkstra

- 位置：`backend/apps/preparation/builder/core.py:659`
- 代码：
```python
        for row in self._extra["mapEdges"]:
            if row["fromNodeName"] == a and row["toNodeName"] == b:
                raise BuilderError(f"地图连线「{a} → {b}」重复登记（同一对节点只允许一条连线）")
        self._add_extra(
            "mapEdges",
            {
                "fromNodeName": a,
                "toNodeName": b,
                "distance": float(distance),
```
（紧邻的 661-662 行只挡了"未给距离"：`if distance is None: raise BuilderError(...)`；`float()` 只拒绝非数字字符串）
- 触发条件：手滑写成 `b.edge(a, b, -420, road)`、`b.edge(a, b, 0, road)`，或 `distance=float("nan")`（`float()` 只拦非数字字符串，不拦负号与 NaN/Inf）。
- 后果：`MapEdge.distance` 是 `FloatField(default=0)`（`apps/maps/models.py:88`），落库无任何阻挡；距离是合同/运费路程计算里 `_dijkstra`（`apps/contracts/engine.py:1551`）的边权，**负权边使 Dijkstra 的最短路结果无意义**（可产出比实际更短甚至绕着环"越走越近"的线路），运费/里程随之错误；而比赛体检只检查 `if not e.distance`（`apps/preparation/plan.py:774`），**负数不会被任何环节提示**（NaN 也会让排序/求和比较全部失效）。本库既然已对 `a == b`、`distance is None` 做了拦截，漏掉正数校验属于同一层的校验缺口。
- 修复建议：`edge()` 用 `Decimal(str(distance))` 校验可解析、`is_finite()` 且 `> 0`（或按产品口径允许 0 但必须显式 `allow_zero=True`）；同时把 `-0`/NaN 写进错误信息。

## 存疑/待确认

1. **[待确认] 并发导入与事务边界**：`build_competition` 自身没有任何互斥/锁，且"目标比赛非空"的判定发生在写入事务之外（`archive.py:2622` 早于 `archive.py:2657` 的 `transaction.atomic()`）。理论上两个并发进程可以对同一个空比赛同时通过检查并开始导入，第二个进程的 `get_or_create` 会撞唯一约束，被"单资源失败不中断整批"（`archive.py:2670-2674`）记成 problem，最终得到一个半成品比赛（退出码 1）。该结论未实测（不许连库、不许跑命令）：SQLite 默认写锁会把第二个进程阻塞/报 `database is locked`，具体表现取决于 `timeout` 与是否 WAL，**待确认**。根因在导入引擎（B02）；若确认要修，CLI 侧可加"导入前占位/导入中标记"的应用级互斥。
2. **[待确认] 大小写与排序规则**：本库的名称查重是大小写敏感的精确匹配，与项目的 `settings.py:307`（`django.db.backends.sqlite3`，TEXT 默认 BINARY 比较）一致，因此当前环境不存在"库内唯一约束与建包查重口径不一致"的问题；但生产若换成 MySQL（`utf8mb4_*_ci`）等大小写不敏感排序规则，"ABC"/"abc" 会被库判为重名并在导入时抛唯一约束错误（且会退化成 N-03 描述的"整批 skipped"）。**待确认生产库类型**。
3. **[待确认] 归档 `exportedAt` 无时区**：`_now_text()` 用 `datetime.datetime.now()`（`core.py:1607-1610`），naive 本地时间；导入侧不消费该字段，仅在多机/跨时区比对归档时有歧义，影响很低，未列为缺陷。
4. **[待确认] `to_json(default=str)` 的静默降级**：`core.py:1440` 给 `json.dumps` 传了 `default=str`，任何非 JSON 原生对象（`Decimal`、`Path`、自定义对象）都会被静默字符串化而不是报错；本次未找到能造成实际数据错误的传入路径（`_num` 已限制为 int/float/str），仅在用户直接塞入 `map_background` 等原始 dict 时存在风险，需要产品确认是否要收紧。
5. **[待确认] `_find_cycle` 递归深度**：`core.py:1638-1650` 用递归 DFS，科技前置链长度超过 Python 默认递归上限（约 1000）时 `validate()`/`build()` 会抛 `RecursionError`（不是 `BuilderError`）。需要确认实际科技树规模；若可能上千节点，应改成显式栈或迭代式拓扑检测。
6. **[待确认] `verify_rows()` 只查"多出来的列"，不查"缺必填列"**：`schema.py:548-564` 只用 `set(row) - allowed` 判未知列，`Column.required=True` 从未被消费。因此将来某个登记方法漏写必填列（例如 `mapEdges` 丢掉 `pathTypeName`）不会被本库拦住，只会在导入侧被记 problem。是否要补"必填列缺失"校验，取决于维护者对本表定位（列字典 or 强校验）的取舍。
