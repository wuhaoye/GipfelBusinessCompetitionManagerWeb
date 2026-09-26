# U06 backend stock（分支归属：master 基线）

审计范围：`backend/apps/stock/**`（models / serializers / views / urls / admin / engine，共 8 个 py 文件 + 6 个 migration）、`tests/` 下 13 个股票相关脚本（仅作证据参考）。
审计方式：逐行阅读 `engine.py`(1934 行) / `views.py`(1065 行) / `serializers.py`(364 行) / `models.py`(215 行)，并核对 Django 5.0.14 / DRF 实际源码与 SQLite 行为（纯本地 `:memory:` 实验，未写任何业务库）。
**未做任何代码修改，未运行任何写库命令。**

## 概述

股票模块是一条完整的「集合竞价 → 逐笔撮合 → 定价 → 结算（现金/持仓）→ K 线 → 做市商干预」数值管线，撮合与结算全部在一个 `transaction.atomic()` 内完成，账目字段已从 Float 改造为 `DecimalField(max_digits=60)`，纯函数部分（`compute_match` / `compute_price` / `build_candle`）质量尚可，边界守卫（分母 +1、`industry_pe<=0`、`open_!=0`、`len(all_stocks)>2`）基本到位。

但本次审计在**撮合主循环、订单生命周期、权限边界、金额精度**四个方向发现 16 处真实缺陷，其中 1 处 P0 可让撮合循环永久死循环（事务与推进锁永不释放），5 处 P1（股票创建接口恒 500、玩家零成本自成交、做市商挂单永不过期、玩家可自选现金来源字段实现任意充值、跨比赛下单/撤单）。数值类缺陷的一个共性根因是：**撮合以 1e-6 为最小计量单位，但所有「是否为零」的判定用 `EPS=1e-9`，两者相差 1000 倍**；以及**账目「精确到分」的承诺在仓库配置的 SQLite 后端上并不成立（15 位有效数字以上静默丢精度）**。

已核对但**未发现**缺陷的边界点（避免误报）：`compute_pressure` 分母 +1 防除零、`compute_carbon_drift` 行业均值 ≤0 返回 0、`compute_relative_fundamental_score` 由 `len>2` 保证分母非 0、`build_candle` 在 `open_==0` 时跳过除零、`_advance_round_flat` 无除法、`compute_price` 对 `tradePrice` 做了 `isfinite` 校验、`_assert_account_operable` 无法被 `bindFieldId` 之外的改派绕过（`companyId/userId` 已收紧到 high）、`_release_advance_lock` 看似 double-release 但因 `_advance_locks_guard` 同时覆盖两次 `release()` 与 `acquire()`，实际不会误解锁（详见「存疑/待确认」）。所有 14 个视图均带 `IsAuthenticated` + `@require_permissions`，未发现未认证下单路径。

## 缺陷清单

### [P0] S-01 撮合主循环可永久死循环（qty 被量化成 0 后无任何状态推进），事务与推进锁永不释放

- 位置：`backend/apps/stock/engine.py:1596`（配合 `:1612`、`:1638`）
- 代码：
```python
            buy_cash = cash_map[buy.funds_account_id]
            if qty * pair_price > buy_cash + EPS:
                qty = buy_cash / pair_price
                if qty <= EPS:                     # EPS = 1e-9（engine.py:39）
                    buy_rem[buy.id] = Decimal("0")
                    bi += 1
                    continue
            ...
            # 6 位小数微调（保留买卖双方分摊精度，不入账）
            qty = (qty * Decimal("1000000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / Decimal("1000000")
            ...
            buy_rem[buy.id] = buy_rem[buy.id] - qty        # :1638
            sell_rem[sell.id] = sell_rem[sell.id] - qty    # :1639
            if buy_rem[buy.id] <= EPS:
                bi += 1
            if sell_rem[sell.id] <= EPS:
                si += 1
```
- 触发条件：现金夹紧（`qty = buy_cash / pair_price`）产生一个 **大于 `EPS=1e-9` 但小于 `5e-7`** 的商时，`qty <= EPS` 判零守卫不生效，而 `:1612` 的 6 位量化会把它变成 **0**；此时 `buy_rem`/`sell_rem` 都不减少，`bi/si` 都不前进，下一轮迭代状态与上一轮**完全一致**（现金、剩余量、成交价全部不变），`while` 无其他出口 → 无限循环。
  最小可复现账目（已用 Decimal 逐步复现循环状态）：账户现金 1,000,000，在 A、B 两只股票上各挂 `BUY 128700 股 @7.77`（各 999,999 元，均能通过 `views.py:897` 的校验，见 S-07），A 股成交后剩 1.00 元；轮到 B 股时 `qty = 1.00/7.77 = 0.1287…` → 6 位量化成交 0.128700 → 现金残留 `0.000001`；下一轮 `qty = 0.000001/7.77 = 1.287e-7 > 1e-9` → 通过守卫 → 量化为 `0` → 死循环。实测输出：
```text
 iter1 cash-clamp qty=78700.12870012870012870012870
 iter1 fill qty=78700.1287 cash_after=0.000001 buy_rem=49999.8713
 iter2 cash-clamp qty=1.287001287001287001287001287E-7
 iter2 fill qty=0 cash_after=0.000001 buy_rem=49999.8713
 NO-CONVERGENCE iters=3  ← 状态完全重复，永不退出
```
  做市商侧同样可触发：做市商买单不参与下单期现金校验，只要其现金在某一轮被建仓/历史成交耗到低于本轮买单名义总额（`base_quantity×(1+…+levels)×价格`，`mmMinQty` 允许配到 1,000,000，见 `serializers.py:348`），第一次现金夹紧就极易命中该窗口。
- 后果：整个 `advance_one_stock` 的 `transaction.atomic()` 永不提交/回滚，SQLite 写锁被该连接长期持有；`advance_round` 的 `finally: _release_advance_lock()` 永不执行，该比赛的进程内推进锁永久占用 → 该比赛股票系统彻底僵死，后续下单/撤单/推进全部 409 或 `database is locked`，且因为死循环在 Python 层（不触发超时），只能重启进程。属于可被普通玩家（构造跨股票满额挂单）触发的拒绝服务。
- 修复建议：把零值守卫与量化口径统一——先量化再判零，且阈值用 1e-6（`qty = round6(...); if qty <= 0: buy_rem[...]=0; bi+=1; continue`），或无条件在循环末尾保证「本轮至少推进 bi 或 si 之一」；同时在 `while` 上加最大迭代次数兜底（`for _ in range(len(buys)+len(sells)+1)`）并记录告警。另建议下单/撮合全链路统一最小计量单位（见 S-16）。

### [P1] S-02 `POST /api/stocks` 恒返回 500：`StockSerializer.code` 被声明为 read_only，`create()` 却取 `validated_data["code"]`

- 位置：`backend/apps/stock/serializers.py:97`（配合 `:147-155`）、`backend/apps/stock/views.py:306-322`
- 代码：
```python
# serializers.py:97
    code = serializers.CharField(max_length=64, trim_whitespace=True, read_only=True)
...
# serializers.py:147
    def create(self, validated_data: dict) -> Stock:
        from .engine import compute_init_price

        cid = validated_data["competitionId"]
        # 唯一性校验
        if Stock.objects.filter(competition_id=cid, code=validated_data["code"]).exists():
```
- 触发条件：任何一次创建股票（`CollectionView.post` 是唯一调用 `StockSerializer.create` 的入口，`views.py:314`）。DRF 的 `Serializer._writable_fields` 会**跳过所有 read_only 字段**（已核对 `backend/.venv/Lib/site-packages/rest_framework/serializers.py:377-380`），因此 `validated_data` 中根本不存在 `code` 键，`validated_data["code"]` 抛 `KeyError`；`apps.common.exceptions.exception_handler` 对未被 DRF 接住的异常统一返回 500「服务器内部错误」（`exceptions.py:52-55`）。`validate_code`（`:122`）也永远不会被调用。
  附带问题：`views.py:312` 的 `data = dict(request.data)` 对表单编码请求会把每个值变成 list（`{"code": ["600001"]}`），非 JSON 调用方还会在字段校验阶段直接 400。
- 后果：股票创建功能整体不可用（100% 500），`stock:manage` 管理员无法新增股票；只能靠后台 admin 或预置数据绕过——而后台写入会绕过 `compute_init_price`/股本校验（`admin.py:1-3` 自己声明了这一点），因此实际路径上「初始价合法性检查」形同废止。
- 修复建议：把 `code` 改为可写（`serializers.CharField(max_length=64, trim_whitespace=True)`）并保留 `validate_code` + 唯一性校验；`create()` 用 `validated_data.get("code")` 并显式报业务错误；`views.py:312` 改为 `request.data.copy()`（或对 QueryDict 取首值）以兼容表单编码。

### [P1] S-03 撮合不做自成交检查：同一账户的买卖单互相对倒，零成本制造虚假成交量并参与成交价锚定

- 位置：`backend/apps/stock/engine.py:1582-1596`（配合 `backend/apps/stock/views.py:884-905` 的下单校验）
- 代码：
```python
        while bi < len(buys) and si < len(sells):
            buy = buys[bi]
            sell = sells[si]
            if buy.price < sell.price:
                break
            qty = min(buy_rem[buy.id], sell_rem[sell.id])
            ...
            pair_price = min(
                max(trade_price, Decimal(str(sell.price))),
                Decimal(str(buy.price)),
            )
            buy_cash = cash_map[buy.funds_account_id]
```
  下单侧只做「现金够不够 / 持仓够不够」检查，从不检查该账户是否已有反向挂单：
```python
# views.py:889-898
            for o in pending:
                if o.side == "BUY":
                    pending_buy_cost += Decimal(str(o.price)) * Decimal(str(o.quantity))
                else:
                    pending_sell_shares += Decimal(str(o.quantity))
            if data["side"] == "BUY":
                need = data["price"] * data["quantity"]
                if available_balance < need + pending_buy_cost - Decimal("0.000001"):
```
- 触发条件：同一资金账户（玩家自己的用户账户，或自己公司的账户）对同一只股票同时挂 BUY 与 SELL（`price_buy >= price_sell` 即可），撮合时 `buy.funds_account_id == sell.funds_account_id`。
- 后果：
  1. 现金净额恒为 0（`cash_map -= qty*pair_price` 后紧跟 `+= qty*pair_price`），持仓净额恒为 0，因此**对倒完全不花钱、不丢股**；但 `total_volume = Σ filled[buy]`（`engine.py:1708-1710`）会把它记成巨量成交量写进 K 线（`volume`），玩家可以任意伪造「放量」形态误导他人。
  2. 成交价 `trade_price = (最高买 + 最低卖)/2`（`engine.py:113`）在**全市场全部挂单**上计算，玩家自己的对倒单会直接参与这个锚点，再经 `tradePriceWeight=0.7` 的权重进入 `final`（`engine.py:206-209`），配合吃掉做市商低价卖档（先买光对手盘再对倒）可把收盘价推向涨停附近，而自身净成本仅剩手续费为 0 的价差。
  3. 因为对倒使 `matched` 恒为真，`advance_one_stock` 的「无对手盘 → 平盘」分支（`:1522`）会被玩家的对倒单长期绕过，价格完全脱离真实供需。
- 修复建议：撮合循环遇到 `buy.funds_account_id == sell.funds_account_id` 时跳过（`bi += 1` 或标记该单为自成交不可撮合）；下单时禁止同账户同股票的反向挂单；成交量统计排除自成交对。

### [P1] S-04 做市商旧轮挂单永远不会被撤销（轮次被改写为 new_round），挂单表逐轮累积、历史报价长期污染撮合与成交价

- 位置：`backend/apps/stock/engine.py:1016-1024`（撤销）与 `backend/apps/stock/engine.py:1753-1756`（改写轮次）
- 代码：
```python
# engine.py:1016（做市商每轮开头想撤销自己的旧轮挂单）
    # 问题4: 只取消做市商的旧轮订单（本轮订单保留，旧轮价格仍在范围内的也保留）
    StockOrder.objects.filter(
        stock_id=stock.id,
        competition_id=competition_id,
        status="PENDING",
        funds_account_id=mm_account.id,
        round__lt=current_round,
    ).update(status="CANCELLED")
...
# engine.py:1753（同一轮结尾：所有仍在价格带内的挂单被“续命”到新一轮）
                else:
                    # 价格仍在范围内，更新订单轮次保持有效
                    order.round = new_round
                    order.save(update_fields=["round"])
```
- 触发条件：任何一次正常推进。做市商在 R 轮以 `round=R` 创建挂单，R 轮结尾这些未成交单被 `order.round = R+1` 续命；R+1 轮开头做市商的撤销条件是 `round__lt=R+1`，已经匹配不到它们 → 撤不掉。平盘分支 `_advance_round_flat` 的 `:1276` 同样续命。
- 后果：
  1. 做市商每轮新增 6~8 笔挂单（`levels=3` 时 3 买 +3 卖 ± 自成交 2 笔），全部留在 `PENDING`；只要价格没有走出旧的 ±10% 价格带就永不清理，N 轮后单只股票积压 O(N) 笔僵尸报价，撮合循环、`compute_match` 的极值统计、`/stocks/orders/list` 的 `[:500]` 截断（`views.py:796`）全部被做市商自己的历史报价灌满。
  2. `trade_price = (最高买 + 最低卖)/2` 在**含僵尸报价的全量挂单**上计算：历史轮次的高买价与低卖价会长期把中位价锚在过去的极值附近，再经 0.7 权重决定收盘价 → 价格被人为钉住，玩家真实成交难以改变定价。
  3. 做市商每轮按「当前持仓」重新放卖单（`:1139`），却不扣除自己尚未成交的历史卖单，挂单簿上的可卖量长期远超其真实持仓，前端深度展示与实际可成交量严重不一致。
  4. 与 `_advance_round_flat` 文档（`:1218-1224`「委托仅在本轮有效，轮次结束未成交即作废」）和 `:1016` 注释自相矛盾，说明该生命周期语义未被实现。
- 修复建议：明确并实现单一订单生命周期语义。若保留「跨轮有效」，做市商撤销必须用 `round__lte=current_round` 或按 `created_at`/`is_market_maker` 标记清理，且续命时不得把做市商单的 `round` 直接改写为 `new_round`（应只对玩家限价单续命）；同时在撮合前把做市商超期未成交单批量作废，避免僵尸深度累积。

### [P1] S-05 `bindFieldId` 改派未纳入「高级管理」校验：普通 `stock:edit` 玩家可把账户现金来源改成任意数值字段，实现任意金额注资

- 位置：`backend/apps/stock/views.py:684-691`（配合 `:646-676` 的权限门）
- 代码：
```python
        if "cashBalance" in data:
            if not high:
                raise BusinessError("仅高级管理可调整资金账户余额", code=403, status_code=403)
            account.cash_balance = data["cashBalance"]
            update_fields.append("cash_balance")
        ...
        if "bindFieldId" in data:
            account.bind_field_id = data["bindFieldId"]
            update_fields.append("bind_field_id")
            if data["bindFieldId"] and account.company_id:
                v = _resolve_field_value_or_default(account.company_id, data["bindFieldId"])
                if v is not None:
                    account.cash_balance = v
                    update_fields.append("cash_balance")
```
- 触发条件：持有 `stock:edit` 的玩家（`AccountItemView.patch` 只要求 `_EDIT_PERM`）对自己作用域内（`stock_company_scopes_list`）的**公司账户**发 `PATCH /stocks/accounts/:id {"bindFieldId": <任意 industryFieldId>}`。上文 `:653-655` 的注释明确写着「资金与归属改派为高危写：仅高级管理可执行」，但该门（`high`）只加在 `cashBalance/companyId/userId` 上，`bindFieldId` 分支直接改 `cash_balance`。`resolve_field_value_or_default` 只按 `(company_id, industry_field_id)` 取值，**不校验该字段是否属于现金字段、也不校验产业类型匹配**（对比 `serializers.py:66` 的 PE 联动校验）。
- 后果：账户的「可用现金」由 `locked_account.bind_field_id` 对应的公司字段决定（下单时 `views.py:878-881`、结算时 `engine.py:1548-1552` 都读该字段）。玩家把绑定改到自己公司任意一个大额字段（如总资产/营业收入，通常远大于现金），即可让该账户获得远超 100 万初始额度的购买力；若该字段本身由玩家/计算图可写，则等价于无限资金——而显式写 `cashBalance` 的路径是被拒绝的，两条路径的权限口径不一致。此外玩家可借此把公司账上的真实现金字段「切走」，使后续结算不再回写原现金字段。
- 修复建议：`bindFieldId` 的变更必须与 `cashBalance/companyId/userId` 同级别（`high` 校验），并校验目标字段必须是该比赛/公司产业类型下的合法现金字段（白名单或字段 key 约束）；重建绑定时应报错而不是静默把字段值抄成 `cash_balance`。

### [P1] S-06 跨比赛下单/撤单：`_is_high_manager` 直接跳过账户可操作性校验，且股票视图不启用比赛域兜底

- 位置：`backend/apps/stock/views.py:852-853`、`backend/apps/stock/views.py:958-959`（配合 `:68`、`backend/backend/settings.py:336-343`）
- 代码：
```python
# views.py:849-853（下单）
        if account.competition_id != competition_id:
            raise BusinessError("资金账户不存在", code=404, status_code=404)

        if not _is_high_manager(request.user):
            _assert_account_operable(account, request.user)
...
# views.py:955-959（撤单）
            account = StockFundsAccount.objects.get(pk=order.funds_account_id)
            ...
            if not _is_high_manager(request.user):
                _assert_account_operable(account, request.user)
```
```python
# views.py:68
_PERM_CLASSES = (IsAuthenticated, PermissionsPermission)
# settings.py:336-343 声称的「比赛域全局兜底 CompetitionScopePermission」在显式设置
# permission_classes 的视图上不生效（DRF 用类属性完全覆盖 DEFAULT_PERMISSION_CLASSES）
```
- 触发条件：某比赛 A 的用户持有 `stock:manage`（`_is_high_manager` = `SUPER_ADMIN or stock:manage`，`views.py:83-85`），提交 `POST /stocks/orders`（或 `DELETE /stocks/orders/:id`）指向比赛 B 的 `stockId` + 比赛 B 的 `fundsAccountId`/订单 id。该路径只要求「账户与股票的 `competition_id` 相同」，**完全不校验该比赛是不是调用者所属比赛**；而同类资源在 `ItemView`/`AccountItemView` 中都有 `_get_stock_scoped` / `_get_account` 的比赛域 404 保护（`:97-99`、`:634-637`），说明这是遗漏而非设计。ID 为连续整数，可枚举。
- 后果：比赛 A 的管理员可对比赛 B 的任何资金账户下单/撤单：用 B 的账户 A 高价买入 B 的账户 B 的挂单（搬运资金、破坏 B 的账户余额与排名）、或强行给 B 的账户建立/清空持仓、撤掉 B 选手尚未成交的委托、并通过做市商干预路径改变 B 的股价与 K 线。跨租户写，且会在两个比赛的审计里留下「合法用户操作」的痕迹，赛后无法申诉复原。
- 修复建议：`_assert_account_operable` 不应被 high 完全跳过，而应改为「本比赛内的全部账户才放行」：即对非 SUPER_ADMIN，无论权限高低都先做 `account.competition_id == user.competition_id` 与 `stock.competition_id == user.competition_id` 校验；或给两个视图显式加上 `CompetitionScopePermission`。

### [P2] S-07 下单「已挂未成交占用」按 `stock_id` 过滤：同一账户可在多只股票上重复用满余额，直接被接受后无法成交并触发 S-01 死循环

- 位置：`backend/apps/stock/views.py:884-898`
- 代码：
```python
            pending = StockOrder.objects.filter(
                funds_account_id=locked_account.id, stock_id=stock.id, status="PENDING"
            )
            pending_buy_cost = Decimal("0")
            ...
            if data["side"] == "BUY":
                need = data["price"] * data["quantity"]
                if available_balance < need + pending_buy_cost - Decimal("0.000001"):
                    raise BusinessError("现金余额不足", code=400, status_code=400)
```
- 触发条件：账户余额 1,000,000 时，在 A 股挂 `BUY 128700@7.77`（999,999 元）后在 B 股再挂同样一笔——第二次检查里的 `pending` 只统计 B 股的挂单，两次都通过（`-0.000001` 还额外放宽了 1e-6）。撮合时 `cash_map` 是真实余额，A 股吃满后 B 股只剩 1.00 元。
- 后果：一是「假成交能力」——账户挂单总额可以远超现金，玩家可用同一笔钱在多只股票上排队，撮合时先到的吃掉现金、后到的订单永远无法成交（若不触发死循环，则表现为订单长期 PENDING 而后被价格带作废）；二是这条路径正是 S-01 的实用触发入口（现金残留 `0.000001` 级尾数 + 量化为 0 → 死循环）。
- 修复建议：占用统计去掉 `stock_id` 过滤（按 `competition_id + funds_account_id + status=PENDING` 聚合），并把 `- 0.000001` 这类容差改为严格比较或与最小计量单位一致；卖出侧同理应按账户聚合全部 `PENDING` 卖单。

### [P2] S-08 仓库配置的 SQLite 后端下 `DecimalField(60,4)` 只能保存 15 位有效数字：账目「精确到分」不成立，读回即静默失真

- 位置：`backend/apps/stock/models.py:76`、`:111`、`:160-162`、`:194-200`（配合 `backend/backend/settings.py:305-310`）
- 代码：
```python
# models.py:76
    cash_balance = models.DecimalField(max_digits=60, decimal_places=4, default=1_000_000)
# models.py:160-162
    price = models.DecimalField(max_digits=60, decimal_places=4)
    quantity = models.DecimalField(max_digits=60, decimal_places=4)
    amount = models.DecimalField(max_digits=60, decimal_places=4)
# settings.py:305-310
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
```
- 触发条件：任何绝对值超过 15 位有效数字的账目写入。SQLite 对 `decimal(60,4)` 走 NUMERIC 亲和性，文本会被强制转 INTEGER/REAL；Django 读回时用 `decimal.Context(prec=15).create_decimal_from_float`（已核对 `backend/.venv/Lib/site-packages/django/db/backends/sqlite3/operations.py:331-344`）。本地 `:memory:` 实测：
```text
insert '12345678901234567.8900' → typeof=integer, raw=12345678901234568   ← 分位直接消失
insert '999999999999999999.99'  → raw=1000000000000000000
insert '1000000000000.25'       → typeof=real（13 位有效数字恰好可回转）
```
- 后果：`cash_balance` / `shares` / `price` / `amount` / K 线 OHLC 在超过 15 位有效数字后**静默**改变数值（无异常、无日志）。撮合读到的余额与上一轮写入的不是同一个数，差价被直接算进买卖双方的现金与持仓 —— 这正是模块注释反复提到的「玩家投诉账户少了钱」的残余根因，且与仓库「支持千万京（10^23）级金额」的目标（`appendix`《浮点精度检测报告》E1、`max_digits=60` 改造）直接冲突。触发需要单账户金额 ≥10^15（约千万亿），但绑定字段模式下 `cash_map` 会直接采用公司字段值（`engine.py:1548-1552`），而公司字段是无位数校验的 CharField，达到阈值并不困难。
- 修复建议：为账目 DecimalField 提供 TEXT 亲和性存储（自定义 `db_type` 返回 text 的子类字段 + 迁移重建列），或在写入侧拦截超 15 位有效数字（宁可报错不可静默丢值）；短期至少把 `engine` 侧读回值与该轮写入值做一致性断言并告警。

### [P2] S-09 `round2` 在精度不足时静默返回 0：绑定字段值超过 70 位（或写成 `1e100` 形式）会在结算时把账户现金清零

- 位置：`backend/apps/stock/engine.py:58-63`（配合 `:1657-1665` 的结算写入）
- 代码：
```python
    with localcontext() as ctx:
        ctx.prec = 70
        try:
            return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return Decimal("0")
```
```python
# engine.py:1657-1665
        for acc_id, cash in cash_map.items():
            acc = account_objs.get(acc_id)
            rounded_cash = round2(cash)
            if acc is not None and acc.bind_field_id and acc.company_id:
                write_field_value_in_tx(acc.company_id, acc.bind_field_id, str(rounded_cash))
```
- 触发条件：`cash_map` 对绑定账户直接取公司字段值（`engine.py:1549-1551`，`_parse_exact_number` 接受任意精度整数与 `Decimal`，含 `"1e100"` 这类指数写法），该值经 `Decimal(str(v))` 进入 `round2` 时若有效位数 >70（例如字段值写成 `1e100` 或 71 位以上数字串），`quantize(0.01)` 抛 `InvalidOperation` 被吞掉，返回 `Decimal("0")`，随后**写回公司字段与账户余额**。本地实测 `round2(Decimal('9'*75)) → 0`、`round2(Decimal('9'*80)) → 0`。
- 后果：一次结算即可把该账户现金永久清零（字段值、`cash_balance` 同时归零），且无任何异常与告警；因为绑定字段可由玩家/公式写入而字段类型无位数校验（《浮点精度检测报告》B4 亦指出写入端无位数校验），这是模块注释中所述「现金清零事故」在 prec=70 之外的残余通道。
- 修复建议：`round2` 不应把异常降级为 0 —— 抛出业务异常或返回原值并 `logger.error`；结算写入前对 `cash` 做 `is_finite` 与位数上限校验，超限则该轮跳过该账户并告警，绝不写回 0。

### [P2] S-10 `select_for_update` 在当前后端静默失效，撤单与撮合的订单状态写入互相覆盖

- 位置：`backend/apps/stock/views.py:874`、`backend/apps/stock/views.py:951`、`backend/apps/stock/engine.py:1699`（配合 `engine.py:1867-1873`）
- 代码：
```python
# views.py:874
            locked_account = StockFundsAccount.objects.select_for_update().get(pk=account.id)
# views.py:948-951
        with db_transaction.atomic():
            # P0-#1: select_for_update 锁定订单行，防止撤单与撮合引擎并发竞态
            try:
                order = StockOrder.objects.select_for_update().get(pk=pk)
# engine.py:1694-1704（撮合侧完全不锁订单行，直接按 pk 覆盖 status）
        for o in orders:
            f = filled.get(o.id, Decimal("0"))
            if f > EPS:
                remaining = Decimal(str(o.quantity)) - f
                if remaining <= EPS:
                    StockOrder.objects.filter(pk=o.id).update(status="FILLED")
```
  引擎自身知道 SQLite 不支持行锁（`engine.py:1871 if connection.vendor != "sqlite": qs = qs.select_for_update()`），但视图层没做同样的降级。
- 触发条件：并发「撤单」+「推进轮次」。Django 的 `has_select_for_update` 默认为 False（`django/db/backends/base/features.py:49`），`compiler.py:809` 用 `and features.has_select_for_update` 守卫，因此 SQLite 上两个 `select_for_update()` 都是 **no-op**（已核对源码）；撮合侧读订单（`engine.py:1341`）与回写状态（`:1699`）都不带任何锁。撤单事务提交后，撮合事务的 `update(status="FILLED")` 会无条件覆盖 `CANCELLED`；反之撮合已按 PENDING 计算完资金/持仓后，撤单仍可能成功返回「已撤销」。
- 后果：玩家收到「撤单成功」但订单随后变成 FILLED（钱货已按撮合结果变动），或订单显示 CANCELLED 却已被扣款/过户；部分成交路径 `:1701-1704` 还会把已撤销订单的 `quantity` 改小。SQLite 单写者下更常见的表现是长事务（整轮推进持写锁）期间的下单/撤单直接 `database is locked` 500。所有「行锁防并发」的注释在本部署下不成立。
- 修复建议：视图层与引擎统一并发策略——把订单状态的回写改为带条件的 `UPDATE ... WHERE status='PENDING'`（乐观锁，`updated`=0 则跳过该单的结算），撤单也在同一条件语义下进行；或改用 PostgreSQL 并确保两侧都用 `select_for_update()`；SQLite 下缩短事务（按股票提交）并设置 `timeout`/WAL。

### [P2] S-11 做市商建仓路径凭空创造股票、销毁现金，且不受 `total_shares` 约束

- 位置：`backend/apps/stock/engine.py:1054-1076`
- 代码：
```python
    if need_shares > 0:
        # P0-#4: 检查做市商现金是否足够，不够则缩减建仓数量
        cost_per_share = round2(Decimal(str(base_price)))
        total_cost = cost_per_share * Decimal(str(need_shares))
        available_cash = Decimal(str(mm_account.cash_balance))
        ...
            if mm_holding is None:
                StockHolding.objects.create(
                    funds_account_id=mm_account.id, stock_id=stock.id,
                    shares=round2(total_sell_qty), cost_price=round2(base_price),
                    competition_id=competition_id,
                )
            else:
                mm_holding.shares = Decimal(str(mm_holding.shares)) + Decimal(str(need_shares))
                mm_holding.save(update_fields=["shares"])
            actual_cost = round2(Decimal(str(base_price)) * Decimal(str(need_shares)))
            mm_account.cash_balance = Decimal(str(mm_account.cash_balance)) - actual_cost
            mm_account.save(update_fields=["cash_balance"])
```
- 触发条件：每轮推进做市商挂单前，只要做市商持仓少于 `total_sell_qty = base_quantity×(1+…+levels) + intervention_qty` 就执行（正常比赛的每只股票每轮都会走）。
- 后果：增持的股票没有任何卖方、也没有任何账户收到对应现金——股票被凭空创造、现金被凭空销毁（做市商是唯一无对手方的记账主体）。累积起来，市场流通股数可以远超 `Stock.total_shares`（`total_shares` 只在计算 `base_quantity` 时被参考一次，从不作为持仓上限），使「市值 = 总股本 × 股价」这一基础口径与玩家总资产统计不再自洽；做市商现金则单向流出（建仓只扣现金、卖单成交才回款），长期运行后资金/库存严重失衡，也更容易把做市商推到现金不足 → 触发 S-01 的现金夹紧路径。
- 修复建议：把做市商建仓也纳入资金守恒口径（例如从比赛「央行/发行账户」划转，或在 `Stock` 上新增 `mm_shares`/流通股上限并校验 `mm_holding.shares ≤ total_shares`），并对做市商库存设置上下限；至少应在建仓时记录一条审计流水以便对账。

### [P2] S-12 `compute_init_price` 的 `quantize` 无精度保护：合法入参即可抛 `InvalidOperation` 导致 500

- 位置：`backend/apps/stock/engine.py:333-339`（入参校验见 `serializers.py:102-103`、调用点 `serializers.py:172`、`:254`）
- 代码：
```python
    price = (
        Decimal(str(init_net_profit))
        * 10000
        / Decimal(str(total_shares))
        / Decimal(str(industry_pe))
    )
    return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
```
- 触发条件：`totalShares` / `initNetProfit` 是 `DecimalField(max_digits=60, decimal_places=4, min_value=0)`，`create/update` 只校验「>0」。取 `initNetProfit = 9…9(56 位)`、`totalShares = 0.0001`、PE = 0.01 时，乘积/商会得到 28 位有效数字但指数为 39+ 的 Decimal，`quantize(Decimal("0.01"))` 需要 ~66 位精度，超出默认 context（prec=28）→ `InvalidOperation`。此处在 `round2` 里有 try/except，`compute_init_price` 里**没有**。本地实测：`div ok, exp=39 → EXC InvalidOperation`。
- 后果：`PATCH /stocks/:id`（改股本/净利润）或创建股票时抛未捕获异常 → 统一被 `exception_handler` 转成 500「服务器内部错误」，并写入未处理异常审计；管理员无法从报错中知道是量纲/精度问题（`create` 路径本来就有 S-02 的 500，`update` 路径则会在处理极值时暴露）。
- 修复建议：与 `round2` 一致地使用 `localcontext(prec=70)`，或先做量纲/量级预检（例如要求 `init_price ∈ (0, 10000]` 的参数区间前置校验后再计算），并把 `InvalidOperation` 转成明确的 400 业务错误。

### [P3] S-13 部分成交后订单剩余量被 `round2` 到 2 位小数，与 6 位成交精度不匹配：订单最多可超发 0.005 股/轮

- 位置：`backend/apps/stock/engine.py:1694-1704`
- 代码：
```python
        for o in orders:
            f = filled.get(o.id, Decimal("0"))
            if f > EPS:
                remaining = Decimal(str(o.quantity)) - f
                if remaining <= EPS:
                    StockOrder.objects.filter(pk=o.id).update(status="FILLED")
                else:
                    StockOrder.objects.filter(pk=o.id).update(
                        quantity=round2(remaining),
                        amount=round2(Decimal(str(o.price)) * remaining),
                    )
```
- 触发条件：任何带 6 位小数的部分成交（撮合在 `:1612` 按 1e-6 量化成交量，例如成交 33.333333 股），`remaining = 100 - 33.333333 = 66.666667` 被 `round2` 成 `66.67`。
- 后果：订单声明的数量比实际可成交量多出最多 0.005 股/轮，多轮部分成交会持续放大（订单展示的 `quantity`/`amount` 与真实资金流水不一致，前端「剩余可成交」显示偏大）；也说明订单数量本就没有与之匹配的最小单位（见 S-16）。
- 修复建议：订单剩余量与 `amount` 保留与撮合一致的小数位（4 位或 6 位，别再降到 2 位），或统一把最小交易单位定义为 1 股/100 股并对齐全部数量字段。

### [P3] S-14 「AI做市商」账户名可被玩家抢占：做市商资金/持仓落在玩家可操作账户上，且该账户在所有管理端列表中被隐藏

- 位置：`backend/apps/stock/views.py:576-580`（创建账户时不禁止保留名）、`backend/apps/stock/engine.py:1008-1012`（做市商账户按名字 `get_or_create`）
- 代码：
```python
# views.py:576-580
        name = (request.data.get("name") or "").strip()
        if not name:
            raise BusinessError("账户名不能为空", code=400, status_code=400)
        if StockFundsAccount.objects.filter(competition_id=cid, name=name).exists():
            raise BusinessError("资金账户名已存在", code=409, status_code=409)
```
```python
# engine.py:1008-1012
    mm_account, _ = StockFundsAccount.objects.get_or_create(
        competition_id=competition_id,
        name="AI做市商",
        defaults={"owner_type": "COMPANY", "cash_balance": 1_000_000_000},
    )
```
- 触发条件：持 `stock:edit` 的玩家在**首次推进轮次之前**创建名为「AI做市商」的用户账户（该比赛尚无做市商账户，唯一性检查通过；`owner_type=USER` 被强制为自己、`cash_balance` 固定 100 万，`views.py:590-596`）。之后做市商的 `get_or_create` 直接命中该账户，`defaults` 不再生效。
- 后果：做市商的建仓持仓、全部双向报价都会挂在该玩家自己名下且可由该玩家交易的账户上（`_assert_account_operable` 通过），而所有账户列表接口都用 `.exclude(name="AI做市商")` 过滤（`views.py:467`、`:487`），导致该账户在管理端完全不可见——玩家既拿到了做市商的股票库存/报价流，也让自己的账户从裁判视野里消失。审核/对账无法发现。
- 修复建议：创建账户时保留名（`AI做市商` 等）一律 403；做市商账户改为按固定标记（新增 `is_market_maker` 布尔字段或 system 名 + `owner_type` 校验）识别，而不是按可被用户占用的 `name` 字符串匹配。

### [P3] S-15 `cashBalance` / `bindFieldId` / 账户归属字段直写缺乏类型与取值校验

- 位置：`backend/apps/stock/views.py:669-691`、`backend/apps/stock/views.py:607-610`
- 代码：
```python
        if "cashBalance" in data:
            if not high:
                raise BusinessError("仅高级管理可调整资金账户余额", code=403, status_code=403)
            account.cash_balance = data["cashBalance"]
            update_fields.append("cash_balance")
        if "companyId" in data:
            if not high:
                raise BusinessError("仅高级管理可变更账户归属公司", code=403, status_code=403)
            account.company_id = data["companyId"]
            update_fields.append("company_id")
```
- 触发条件：任何拥有 `stock:manage` 的调用方直接发 `{"cashBalance": -1}`、`{"cashBalance": "abc"}`、`{"cashBalance": 1e400}` 或省略字段类型。此处**完全绕过了** `StockFundsAccountSerializer`（其 `cashBalance` 带 `min_value=0`、`max_digits=60`，且 `_serialize_account` 只用于输出）。`AccountCollectionView` 的 `bindFieldId` 分支（`:607-610`）同样不校验目标字段是否属于该公司产业类型（对比 `serializers.py:66` 的 PE 联动校验）。
- 后果：负数余额会被引擎当作「可买 0 股」静默吞掉买单；非数值/超长值在 `save()` 时抛 `decimal.InvalidOperation`/`ValueError` → 500；绑定到异产业字段会让账户现金被无关指标驱动（与 S-05 同类）。
- 修复建议：写路径复用序列化器（或至少 `Decimal(str(v))` + `is_finite` + 非负 + 位数上限校验），`bindFieldId` 增加产业类型一致性校验。

### [P3] S-16 无最小交易单位约束：`quantity` 可为 0.0001 股，与撮合 1e-6 计量、K 线整数股语义不匹配

- 位置：`backend/apps/stock/serializers.py:326-329`
- 代码：
```python
    side = serializers.ChoiceField(choices=["BUY", "SELL"])
    # 委托价格/数量：撮合输入必须 Decimal，否则撮合误差会累积。
    price = serializers.DecimalField(max_digits=60, decimal_places=4, min_value=Decimal("0.0001"))
    quantity = serializers.DecimalField(max_digits=60, decimal_places=4, min_value=Decimal("0.0001"))
```
- 触发条件：任意下单请求传 `quantity: 0.0001`（或 0.0001 的倍数），系统没有 100 股整手校验、也没有「必须为整数股」校验（`min_value` 只挡住 0 与负数）。
- 后果：撮合内部按 1e-6 量化、订单展示按 2 位舍入（S-13），而业务语义（股）应当是整数，三者口径并存；碎股订单使 S-01 的「现金尾数 → 量化为 0」窗口更容易命中（非整除价格 + 极小剩余量），也让 `volume`、持仓、`total_shares` 的口径出现非整数股，前端展示与统计口径漂移。
- 修复建议：明确并统一最小交易单位（建议整手 100 股 + `quantity % 100 == 0`，或允许碎股但全链路统一到 1e-4 并补齐 S-13、S-01 的量化）。

## 存疑/待确认

1. **`_release_advance_lock` 的双重 `release()` 经核实不是缺陷**（`engine.py:1805-1817`）：两次 `release()` 都包在 `with _advance_locks_guard:` 内，而 `_try_acquire_advance_lock` 同样必须在持有该 guard 时才能 `acquire`，因此第二个 `release()` 不可能释放别人刚拿到的锁（只会抛 `RuntimeError` 被吞）。仍建议删除多余的那次调用，避免后续维护者把 acquire 移到 guard 之外后立刻退化为真实互斥破坏。
2. **`StockConfigSerializer` 允许 `limitPct` 上限 0.5**（`serializers.py:343`），而委托价硬编码限制在 ±10%（`views.py:856-868`）、封板判定线固定 9.9%（`engine.py:1889-1893`）。配置成 30%~50% 时单轮理论价最大变动可达 (maxMovePct+drift×maxMovePct)，K 线涨跌幅会远超 10% 语义、封板判定与「防连板 9.4% 上限」的注释不再自洽。是否有意支持「扩板」模式 [待确认]；若有意，建议同步公开委托价带与封板线，或把 `upperLimitPct/lowerLimitPct` 与封板判定绑定为配置派生值。
3. **相对排名并列时的偏空倾向**（`engine.py:839-855`）：`percentile = (rank + 0.5)/len`，当 n 只股票原始分完全相同（rank 全为 0）时 percentile = 0.5/n ≤ 0.3，全体落入「后 30%」得负分（n=3 时约 -0.22），与注释「避免所有股票基本面相同时全部得最低分」的意图只部分吻合。影响幅度有限（再由 `clamp(±0.5)` 与基本面 20% 权重衰减），是否可接受 [待确认]。
4. **`tests/` 下 13 个股票脚本不 import `apps.stock.*`，全部是重新抄写的独立模拟函数**（例：`test_kline.py` 自带 `candle_noise`/影线公式拷贝、`test_user_volume.py` 自带 `mm_intensity` 阈值拷贝），因此**无法对生产代码形成任何回归保护**。且其中至少两处已与生产实现漂移：`test_market_maker.py:9-49` 的 `calculate_fundamental_score` 是 -1~1 加权归一化算法，生产是 `calculate_raw_fundamental_score` 的 0~100 阈值制（`engine.py:760-819`）；`test_callback_mechanism.py:11-17` 使用的 `mmCallbackMinRounds / mmCallbackMaxProb / mmCallbackMinPct / mmCallbackMaxPct` 等键在生产配置中不存在（引擎只读 `mmCallbackEnabled`，概率与幅度硬编码在 `engine.py:1473-1510`）。`big_number_smoke.py` 与 `sqlite_decimal_roundtrip.py` 确实是真实链路测试，但只覆盖 contracts/公司字段，不覆盖 stock。建议把这些脚本改为直接调用 `apps.stock.engine` 并纳入 CI。
5. **`mmLevels` / `mmSkewPct` / `mmSelfTrade*` / `mmRegressionThreshold` / `mmRegressionStrength` / `mmBadNews*` / `mmGoodNews*` 均不在 `StockConfigSerializer` 字段表中**（`serializers.py:342-357`），只能靠 `Competition.stock_config` 里的原始 JSON 生效；`StockConfigSerializer` 也无范围以外的类型清洗。若运营确实需要调这些参数 [待确认]，建议纳入序列化器（含上下界），否则应把它们从 `DEFAULT_STOCK_CONFIG` 里标注为「仅代码内常量」以免误以为可通过接口配置。
6. **`_parse_exact_number` 接受指数形式**（`engine.py:392-410`，`int(s)` 失败后回退 `Decimal(s)`，`"1e100"` 可通过），与 S-09 组合可让绑定字段值变成不可量化的极大数；是否需要在数值入口统一拒绝指数写法 [待确认]。
