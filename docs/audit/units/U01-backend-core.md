# U01 backend core（分支归属：master 基线）

## 概述

本层是 Django/DRF 的公共底座：`backend/settings.py` 集中定义安全开关（JWT/弱密钥 fail-fast、ALLOWED_HOSTS、CSRF/CORS、MIDDLEWARE 顺序、DRF 全局认证与异常处理器），`apps/common/**` 提供 9 类基础数据的通用 CRUD 基类（分页、比赛域隔离、唯一冲突检测）、审计落库与写操作信号、登录限流中间件、后端管理后台防直连网关、大数安全渲染器与 RBAC 权限目录。
风险面集中在四处：**①中间件形式实现的限流/审计可被绕过或静默失效；②出站渲染层对非有限浮点无防护（响应渲染抛异常 → 500）；③审计完全依赖 post_save 信号，批量写与含 Decimal 字段的写入静默丢失；④会话/传输安全开关缺失与「全局兜底」权限类实际未挂载**。
本次未发现 P0（无未认证数据读取、无 SQL 注入、无越权写其他比赛的可达路径）；P1 两条、P2 五条、P3 六条。所有结论均以真实读码 + 解释器只读验证（未写库、未改任何代码）为依据。

关键运行环境事实（已实测）：
- DRF 3.15 默认 `STRICT_JSON=True` → `JSONRenderer` 用 `json.dumps(..., allow_nan=False)`；渲染发生在 `django/core/handlers/base.py:220`（`_get_response` 内、DRF try/except 之外），异常渲染失败 → Django 500 而非 `{code,message,data}` 信封。
- DRF `JSONParser` 的 `strict_constant` 拦住了 `NaN`/`Infinity` **字面量**，但 `1e400` / `-1e400` 这类数值字面量仍被解析成 `±inf`（实测：`{'a': 1e400} -> {'a': inf} (accepted)`）。
- Python 侧实测：`json.dumps(Decimal('12.34'), ensure_ascii=False)` → `TypeError: Object of type Decimal is not JSON serializable`。
- DRF `APIView.permission_classes = api_settings.DEFAULT_PERMISSION_CLASSES`，子类赋值是**整体替换**（`rest_framework/views.py:111`），不会与默认值合并。
- 全仓 89 处视图 `permission_classes` 均为显式声明 → `DEFAULT_PERMISSION_CLASSES` 中的 `CompetitionScopePermission` 实际零挂载（见 C-12）。

---

## 缺陷清单

### [P1] C-01 登录爆破限流可被完全绕过（三处独立触发）

- 位置：`backend/apps/common/middleware.py:255`
- 代码：
```python
    def process_request(self, request):
        if request.method == "POST" and request.path == "/api/auth/login":
            ip = _client_ip(request)
            username = ""
            try:
                import json

                body = json.loads(request.body or b"{}")
                username = body.get("username", "")
            except Exception:  # noqa: BLE001
                pass
            if is_login_locked(ip, username):
```
- 触发条件（三者任一即可，均可继续正常登录尝试）：
  1. **空白绕过**：视图侧取 `username = (request.data.get("username") or "").strip()`（`backend/apps/auth/views.py:126`）并以此记账 `record_login_failure(ip, username)`（同文件:139），而中间件用的是**未 strip 的原始值**做查询键（`is_login_locked` → `_locks.get((ip, username))`，`middleware.py:245-249`）。发 `{"username":"admin ","password":"x"}`：失败计数记在 `("1.2.3.4","admin")`，中间件查的是 `("1.2.3.4","admin ")` → 永远查不到锁定状态；视图 strip 后仍正常查库校验密码。
  2. **换 Content-Type**：DRF 默认启用 `FormParser`/`MultiPartParser`（`rest_framework/settings.py:10`，未在 settings 覆盖），视图能正常解析；但中间件只做 `json.loads(request.body)`，`username=admin&password=x` 直接抛异常被 `except` 吞掉 → `username=""` → 查 `(ip, "")` → 永不锁定。
  3. **body 非 JSON 对象**：`{"username":"admin"}` 之外任何 json.loads 失败形态（含空体、数组）都退化为 `username=""`，同样绕过。
- 后果：`10 次/5 分钟 → 锁定 15 分钟`（`middleware.py:206-208`）这一唯一的在线口令爆破防护**完全失效**；攻击者可对任意账号（含 SUPER_ADMIN）无限次尝试，仅受 bcrypt cost=12 的单次耗时限制（可并发摊薄）。审计面上也不会留下「已锁定」痕迹。
- 修复建议：限流判定与记账使用同一份规范化输入——把提取逻辑收敛成一个函数（例如 `normalize_login_key(request)`：用 `request.data`/DRF 解析器取用户名 + `.strip()` + `str()` 兜底），中间件与 `LoginView` 共用；更稳妥是直接在 `LoginView` 内做锁定判定（视图能拿到解析后的 `username`），中间件只保留兜底。并补一条测试：`{"username":"admin "}` 连续 10 次失败后第 11 次必须 429。

---

### [P1] C-02 含 DecimalField 的模型写审计永久静默丢失（审计等于不存在）

- 位置：`backend/apps/common/audit.py:62`
- 代码：
```python
        AuditLog.objects.create(
            kind="write",
            ...
            changes=json.dumps(sanitize_changes(changes), ensure_ascii=False)
            if changes is not None
            else None,
            ip=get_request_ip(),
            device=get_request_device(),
        )
    except Exception:  # noqa: BLE001 - 审计失败不阻断主流程
        logger.debug("写操作审计写入失败", exc_info=True)
```
- 触发条件：任何一次「被 `MODEL_TO_RESOURCE` 跟踪且含 `DecimalField`」的模型 save/delete。信号侧 `_summarize_instance` 用 `data[f.name] = getattr(instance, f.name)` 原样取字段值（`backend/apps/common/signals.py:74-84`），`sanitize_changes` 只按 key 名做脱敏、不转换类型（`audit.py:31-44`），于是 `changes` 里带 `Decimal`；`json.dumps` 抛 `TypeError: Object of type Decimal is not JSON serializable`（已实测），被 `except Exception` 吞掉，只在 **debug** 级留一行（生产 `LOG_LEVEL=INFO` 不可见）。
  受影响模型（均非空 Decimal 列，每次写必然命中）：`Infrastructure.price/activation_price`、`Warehouse.capacity/price`、`ProductionLine.price/max_per_year`、`Fuel.price_per_liter`、`Vehicle.price`（`backend/apps/*/models.py` 对应行），以及 `Stock/StockFundsAccount/StockHolding/StockOrder/StockCandle` 的全部金额列（`backend/apps/stock/models.py:18-200`）。而 `MODEL_TO_RESOURCE` 明确跟踪这些模型（`backend/apps/realtime/emit.py:137-160`）→ 信号确实连接了，只是写入必然失败。
- 后果：基础设施、仓库、生产线、燃料、载具、股票（含下单/持仓/资金账户/轮次推进）等**全部写操作在 `audit_logs` 里没有任何记录**，且失败无告警；配合 C-04，审计覆盖出现大面积空洞，事故取证与责任追溯失效。这正是 `apps/common/json_util.py:36` 专门提供 `dumps_json_safe` 要解决的问题（该函数注释已写明「直接 json.dumps 会抛 Object of type Decimal is not JSON serializable」），但 `log_write` 没有使用它。
- 修复建议：`log_write` 改用 `apps.common.json_util.dumps_json_safe`；`except` 分支把日志级别提到 `warning`（或计数报警），让审计失败可见；建议加一条测试：对 `Fuel` 做一次 create 后断言 `AuditLog.objects.filter(model="fuels").exists()`。

---

### [P2] C-03 非有限浮点穿透渲染层：响应渲染抛 ValueError → 500，且落库后该模块持续 500

- 位置：`backend/apps/common/renderers.py:34`
- 代码：
```python
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            return None
        if obj == obj.to_integral_value():
            return _convert_big_numbers(int(obj))
        return format(obj, "f")
    if isinstance(obj, float):
        return obj
```
- 触发条件：任何出站数据里含 `inf`/`-inf`/`nan` 的 float。入口可达（已实测）：DRF 的 `parse_constant=strict_constant` 只拦 `NaN`/`Infinity` **字面量**，`1e400` 仍解析为 `inf`；`serializers.FloatField.to_internal_value` 直接 `float(data)`（`rest_framework/fields.py:954-959`），**无 `is_finite` 校验**，且 min/max 校验器对 `inf` 一律通过（`inf > max` 为 False 时才拒绝，未设 max 的字段如 `Material.carbonEmissionCoefficient`（`backend/apps/materials/serializers.py:17`）完全放行；`nan` 连 min/max 都能通过）。示例：`POST /api/materials` body `{"name":"x","origin":"y","carbonEmissionCoefficient":1e400,"competitionId":<id>}`。
  Decimal 分支有 `is_finite()` 防护，float 分支没有——渲染层是**唯一的**统一出站收口（`DEFAULT_RENDERER_CLASSES` 只挂 `apps.common.response.JSONRenderer`），所以任何模块写入的 inf 都会在这里炸。
- 后果：`_convert_big_numbers` 放行 `inf` → DRF `JSONRenderer` 用 `allow_nan=False` 抛 `ValueError: Out of range float values are not JSON compliant`。渲染发生在 `django/core/handlers/base.py:220`，异常经中间件 `process_exception`（无人处理）重新抛出，最终由 Django 兜底成 **500 且响应体不是 `{code,message,data}` 信封**（`DEBUG=true` 时直接返回 HTML 技术错误页，暴露配置与堆栈）；更严重的是该值已随 `Material.objects.create` 提交（`ATOMIC_REQUESTS` 未开，设置项见 `backend/settings.py:305-310`），此后**该模型列表/详情接口对该比赛永久 500**（inf 以 REAL 形式持久化；`nan` 会被 SQLite 存成 NULL，故只有当次响应失败）[待确认：不同 SQLite 版本对 ±Inf 的持久化差异]。
- 修复建议：在 `_convert_big_numbers` 的 float 分支加 `math.isfinite` 守卫（非有限值统一转 `None` 或字符串 `"Infinity"`，与 Decimal 分支一致），并给 `response.JSONRenderer.render` 的 `super().render()` 也套一层兜底（避免渲染期异常直接变成 500）；根治仍需在序列化层对 FloatField 统一补 `is_finite` 校验（各 app 序列化器，建议做成公共 `FiniteFloatField` 放 `apps/common`）。
- 备注：U03 报告 O-04/O-08 已在 companies/industry_types 侧发现同类现象，本条定位到公共层根因（`renderers.py` 是唯一收口），不重复计算其业务侧条目。

---

### [P2] C-04 `QuerySet.update()`/`bulk_create` 完全绕过审计与实时广播

- 位置：`backend/apps/common/signals.py:201`
- 代码：
```python
            post_save.connect(_on_post_save, sender=model, weak=False)
            post_delete.connect(_on_post_delete, sender=model, weak=False)
            logger.debug("signals connected for model=%s", name)
```
- 触发条件：任何用 `Model.objects.filter(...).update(...)` / `bulk_create` / `bulk_update` 完成的写入——Django 不会为这类语句发 `post_save`，而审计与广播**只**挂在 `post_save`/`post_delete` 上（`log_write` 在全仓仅被 `signals.py:117/158` 与 `stock/engine.py:1916` 调用）。现存真实调用点：`backend/apps/contracts/views.py:337`（合同执行：`status="EXECUTED"` + `inputs`/`signed_at`/`executed_at` 原子抢占）、同文件:354（写入引擎账目结果）、`backend/apps/messages/views.py:251`（一键已读）、:372（`bulk_create` 收件人）、`backend/apps/regions/views.py:410`（批量摘除公司归属）、`backend/apps/industry_types/views.py:347`。
- 后果：**合同执行（改变资金/库存/字段等业务后果最重的动作）在 `audit_logs` 中没有任何留痕**，只有后续显式补的 `emit_resource_changed` 广播（`contracts/views.py:384`）；审批/审核类批量变更同样无审计。审计覆盖依赖于「业务代码记得手写 log_write」，已出现遗漏。
- 修复建议：在 `apps/common/audit.py` 暴露一个显式 `log_action(model, action, record_id, changes, competition_id)` 供 `.update()` 路径调用（`contracts` execute 处必须补）；或在 `apps/common` 提供受控的 `safe_update()` 包装，内部先取旧值、执行 update、再写审计+广播，禁止业务层直接调 `.update()`。

---

### [P2] C-05 未认证请求可无限写审计库与日志（无全局节流、审计表无保留策略）

- 位置：`backend/apps/common/exceptions.py:44`
- 代码：
```python
    # 写审计日志（异常上下文），脱敏由 audit 模块负责
    try:
        from .audit import log_exception

        request = context.get("request")
        log_exception(request, exc, response)
    except Exception:  # noqa: BLE001 - 审计失败不影响主流程
        logger.debug("异常审计写入失败", exc_info=True)
```
- 触发条件：任何被 DRF 接住的异常都会落一条 `AuditLog(kind="error")`，**包括未认证请求的 401 与登录失败**（`LoginView` 抛 `BusinessError(401)`，`backend/apps/auth/views.py:138-150`）。触发无需任何凭据：`for i in $(seq 1 100000); do curl -s -X POST $H/api/auth/login -H 'Content-Type: application/json' -d "{\"username\":\"u$i\",\"password\":\"x\"}"; done`（用不存在的用户名时不会跑 bcrypt，速度极快）。REST_FRAMEWORK 未配置任何 `DEFAULT_THROTTLE_CLASSES`（`backend/settings.py:332-351`，DRF 默认空表），`LoginRateLimitMiddleware` 只覆盖 `/api/auth/login` 且键为 `(ip, username)`（换用户名即不受限），`apps/audit` 无任何清理/保留期代码（全仓 `AuditLog` 只有写入与查询）。
- 后果：未认证攻击者可让 **SQLite（单写者，`settings.py:305-310`）持续承接审计写入**，与业务写互相抢锁（合法用户可能拿到 `database is locked`）；`audit_logs` 无上限增长；每个请求同时写一行 `gipfel.log`（`TimedRotatingFileHandler` 按天轮转、`backupCount=14`，无单文件大小上限，`settings.py:433-441`）→ 磁盘可被打满。
- 修复建议：对未认证/认证失败类异常不再落 `kind="error"` 审计（或只做采样/计数聚合），仅保留 4xx 业务异常；补全局节流 `DEFAULT_THROTTLE_CLASSES`（AnonRateThrottle）并对 `/api/auth/login` 按 IP 限速（含 C-01 修复后的规范化键）；为 `audit_logs` 加保留期管理命令（如 `prune_audit_logs --days=90`）并在部署脚本挂 cron；日志 handler 增加 `maxBytes`。

---

### [P2] C-06 管理后台会话 Cookie 无 Secure 标记 + 无 HSTS/SSL 重定向（会话可被嗅探）

- 位置：`backend/backend/settings.py:462`
- 代码：
```python
# ==================== Session/Cookie（API 项目基本不用，保留默认） ====================
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
```
- 触发条件：settings 中**只有**这两个项，全文件没有 `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` / `SECURE_SSL_REDIRECT` / `SECURE_HSTS_SECONDS` / `SECURE_PROXY_SSL_HEADER`（实测 grep 仅命中上述两行）。因此经 nginx 终止 TLS 部署时（本仓 deploy 脚本形态），Django 认为请求是 http，`sessionid` 与 `csrftoken` 均不带 `Secure` 标志。攻击场景：管理员已登录 /admin，攻击者诱导其浏览器发起任一 `http://<host>/...` 子资源请求（如 `<img src="http://host/uploads/x.png">`，被动混合内容默认不被拦截；或直接给一个 http 链接），同网络/链路上的攻击者即可拿到明文 `sessionid`，进而以 `is_superuser` 身份进入 Django 后台。
- 后果：Django 后台（可改业务用户、权限、任意表数据）的会话被劫持，等于系统最高权限旁路；同时缺少 HSTS 使降级攻击可持续生效。
- 修复建议：设 `SESSION_COOKIE_SECURE = True`、`CSRF_COOKIE_SECURE = True`、`SECURE_SSL_REDIRECT = True`、`SECURE_HSTS_SECONDS = 31536000`（含 `SECURE_HSTS_INCLUDE_SUBDOMAINS`/`PRELOAD` 视域名而定）、`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO","https")`，并统一改为环境变量开关，避免「只有 HTTPONLY 一半」的配置形态；同时确认 nginx 侧强制 http→https。

---

### [P2] C-07 非字符串 username 触发中间件 TypeError → 未认证 500（同一段限流代码）

- 位置：`backend/apps/common/middleware.py:258`
- 代码：
```python
            username = ""
            try:
                import json

                body = json.loads(request.body or b"{}")
                username = body.get("username", "")
            except Exception:  # noqa: BLE001
                pass
            if is_login_locked(ip, username):
```
- 触发条件：`POST /api/auth/login` body `{"username": ["a"], "password": "x"}`（或 `{"username": {"a": 1}}`）。`json.loads` 成功，`username` 为 list/dict；`is_login_locked` 执行 `_locks.get((ip, username))`（`middleware.py:246`）需要对元组取哈希 → `TypeError: unhashable type: 'list'`，且该异常不在 `try` 内、`process_request` 无捕获。
- 后果：未认证请求直接把请求打成 500（Django 兜底页，DEBUG 下含堆栈），同时在 `AuditLog` 写一条 error、日志写一行 traceback；配合 C-05（无节流）可被用于低成本日志/审计污染。
- 修复建议：`username = body.get("username", "")` 后强制 `isinstance(username, str) and username.strip() or ""`；`is_login_locked/record_login_failure` 内部对 key 做 `str()` 归一（与 C-01 的规范化函数合并即可一次性解决两条）。

---

### [P3] C-08 唯一性冲突检测与写入非原子（TOCTOU）→ 并发同名创建返回 500 而非 409

- 位置：`backend/apps/common/base_crud.py:146`
- 代码：
```python
        data["competitionId"] = create_competition_id(request.user, data)
        self._check_conflict(data)
        instance = serializer.create(data)
        return Response(self.serialize(instance))
```
- 触发条件：两个并发 POST `/api/materials`（以及 fuels/infrastructures/warehouses/production-lines/vehicles/parts/products/regions/tech-nodes/map-* 等同构 CRUD）提交同一 `name`；`_check_conflict`（`base_crud.py:100-116`）只做一次 `qs.exists()` 查询，随后才 INSERT，且未包 `transaction.atomic()`/未用「唯一约束 + catch IntegrityError」。前端按钮双击即可复现窗口。
- 后果：两次请求都通过冲突检测，第二条 `Material.objects.create` 撞上 `unique_together(competition,name)`（`backend/apps/materials/models.py:26`）抛 `IntegrityError` → 统一异常处理器返回 **500「服务器内部错误」**（而非 409「名称已存在」），前端与用户无法区分「重名」与「服务异常」，自动重试也不会变成幂等成功；同时多写一条 error 审计。
- 修复建议：把 `_check_conflict` + `create/update` 包进 `transaction.atomic()`，并 `try/except IntegrityError` 收敛为 409（保留 DB 唯一约束作为最终防线）；客户端侧为创建按钮加防抖/幂等键。

---

### [P3] C-09 ASGI 路由不校验 `scope["type"]`：任意路径 WebSocket 握手触发未处理 ValueError

- 位置：`backend/backend/asgi.py:32`
- 代码：
```python
    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path.startswith("/socket.io"):
            await self.socketio(scope, receive, send)
        else:
            await self.django(scope, receive, send)
```
- 触发条件：向任意非 `/socket.io` 路径发起 WebSocket 握手（如 `ws://host/api/auth/me`、`ws://host/`）。Django 的 `ASGIHandler.__call__` 对非 http scope 直接 `raise ValueError("Django can only handle ASGI/HTTP connections, not %s.")`（`django/core/handlers/asgi.py:164-167`），该异常在本路由函数内无捕获。另：`startswith("/socket.io")` 是前缀匹配，`/socket.ioabc`、`/socket.io/../api/...` 也会被交给 socket.io 应用，Django 侧路由被静默遮蔽。
- 后果：每次握手尝试产生一个未处理异常 + 完整 traceback（daphne 记录），未认证且无限速 → 可低成本刷日志（配合 C-05/C-06 的磁盘与轮转策略形成资源消耗）；同时也说明该路由对「非 HTTP、非 socket.io」的流量没有明确拒绝语义（应干净地 403/关闭而非抛异常）。
- 修复建议：在路由里先判断 `scope["type"]`——`websocket`/`lifespan` 且路径不匹配时直接 `await send({"type": "websocket.close", "code": 1008})` 或返回 403；把前缀判断改成 `path == "/socket.io" or path.startswith("/socket.io/")`，避免遮蔽同前缀的其它路径。

---

### [P3] C-10 分页 `page` 无上限，与「防全表扫描」的硬约束声明不符

- 位置：`backend/apps/common/pagination.py:21`
- 代码：
```python
    try:
        page = int(query_params.get("page", DEFAULT_PAGE))
    except (TypeError, ValueError):
        page = DEFAULT_PAGE
    try:
        page_size = int(query_params.get("pageSize", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    ...
    skip = (page - 1) * page_size
    return page, page_size, skip
```
- 触发条件：`GET /api/materials?page=99999999999999999999&pageSize=200`（模块 docstring 明示「pageSize 上限 200（硬约束，防止 DoS 全表扫描）」，但 `page` 只做了 `< 1` 回退，没有上界）。`skip` 被拼进 `LIMIT/OFFSET`（`base_crud.py:132` 切片），SQLite 需先定位到该偏移。
- 后果：以「合法 pageSize」即可绕过该防护，迫使 SQLite 做全表扫描/大偏移定位（表越大越慢），单请求即可长期占用 worker；`qs.count()` 也每请求一次全表计数，放大成本。[待确认：超大 OFFSET（超出 int64）在不同 SQLite 版本上是钳制到最大值还是直接报错，需在目标环境实测]
- 修复建议：`MAX_PAGE`（如 1_000_000）或统一改成 `skip` 上界校验；超大 `page` 直接返回空集 `items: []` 而不查库；列表接口可对 `count()` 做缓存或改为 `exists()`+`limit` 策略。

---

### [P3] C-11 `/uploads/**` 由 Django 静态托管且无任何鉴权

- 位置：`backend/backend/urls.py:54`
- 代码：
```python
urlpatterns += [
    re_path(r"^uploads/(?P<path>.*)$", static_serve, {"document_root": settings.MEDIA_ROOT}),
]
```
- 触发条件：`GET /uploads/<subdir>/<filename>`（无 Authorization 即可）。上传文件名形如 `upload-<毫秒时间戳>-<原名>`、`comp-<比赛id>-<毫秒时间戳><ext>`（`backend/apps/files/views.py:197/256`），URL 会随消息/比赛详情接口下发给客户端，且不参与比赛域隔离、无失效机制。目录穿越已被 `django.views.static.serve` 的 `safe_join` 阻断（`../` 只会 400），且响应带 `nosniff` + CSP `default-src 'none'`，故不构成存储型 XSS。
- 后果：任何人拿到 URL（浏览器历史、聊天里转发的图片链接、代理/访问日志）即可**在未登录状态下永久读取**他人上传的图片（消息图片、地图背景等），属于未认证的横向数据暴露（capability URL 强度依赖时间戳不可枚举，故定级 P3）。
- 修复建议：改为需鉴权的受控视图（校验 JWT + 文件归属比赛/公司后再返回 `FileResponse`），或使用不可猜测的随机文件名（UUID）并配合短期签名 URL；确实需要公开的资源（如地图背景）单独放到公开前缀下，与消息附件分离。
- 备注：`MEDIA_ROOT`/`LOG_DIR` 取 `./uploads`、`./logs` 相对路径（`settings.py:168-173`），实际解析依赖进程 CWD，换工作目录启动会「上传成功但访问 404/日志写别处」，建议改为 `BASE_DIR / ...`（属健壮性问题，不单独编号）。

---

### [P3] C-12 `CompetitionScopePermission` 的「全局兜底」实际从未生效，且实现对非 dict body 会抛 500

- 位置：`backend/backend/settings.py:336`
- 代码：
```python
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
        # 比赛域全局兜底：非超管的写操作必须有比赛上下文（body 的 competitionId
        # 或自身归属比赛）。显式设置 permission_classes 的视图不受影响；
        "apps.common.guards.CompetitionScopePermission",
    ),
```
- 触发条件：DRF 中 `APIView.permission_classes = api_settings.DEFAULT_PERMISSION_CLASSES`，子类只要赋值就**整体替换**（`rest_framework/views.py:111`，无合并语义）。而本仓所有视图都显式声明了 `permission_classes`：`apps/common/base_crud.py:46`（`(IsAuthenticated, CrudPermission)`）、`messages/views.py:144` 等 9 处 `(IsAuthenticated, PermissionsPermission)`、以及各 app 的 `_PERM_CLASSES`（共 89 处匹配）。`CompetitionScopePermission` 在全仓**仅被 settings.py:342 引用一次**，没有任何视图把它放进权限链。
- 后果：`guards.py:72-76` 注释所称「比赛域隔离由 CompetitionScopePermission 全局兜底」在运行时不存在——隔离完全依赖各视图自行 `apply_competition_scope`/`_get_object`/手写过滤；任何新视图或既有视图一旦漏写过滤，不会有兜底拦截（U03 的结论也基于「存在兜底」这一前提，需据此复核）。另外该类实现对请求体类型有隐含假设：`cid = request.data.get("competitionId") if request.data else None`（`guards.py:124`），body 为 JSON 数组/字符串时 `list.get` → `AttributeError` → 500，一旦将来真的挂到某视图上，恶意 body 即可打 500。
- 修复建议：要么把该类显式加入各视图权限链（`(IsAuthenticated, CompetitionScopePermission, PermissionsPermission)` 或写进各 `_PERM_CLASSES`），要么删掉这段误导性注释并把「每视图自行过滤」定为契约（配 lint/测试）；同时把 `request.data.get(...)` 改成 `isinstance(request.data, dict)` 守卫。

---

### [P3] C-13 后台网关「一次性令牌」可重放，且 `/admin/login/` 无任何限流

- 位置：`backend/apps/common/backend_gate.py:110`
- 代码：
```python
        # 携带一次性令牌：校验通过 → 写入带时间戳的会话标记并 302 重定向到干净地址（去除 token 防泄漏）
        token = request.GET.get("token")
        if _verify_token(token):
            request.session[BACKEND_GATE_SESSION_KEY] = {"ts": time.time()}
            request.session.modified = True
            return HttpResponseRedirect(request.path)  # request.path 不含查询串
```
- 触发条件：令牌由 `TimestampSigner` 签发 `f"bk:{user.id}"`（`apps/auth/views.py:211-212`），校验侧只验签名与 `max_age`（`backend_gate.py:64-73`），**不绑定会话、不记录已用、不含随机 nonce** → 在 `BACKEND_GATE_MAX_AGE`（默认 120s，`settings.py:51`）内可被任意人重复使用（URL 出现在浏览器历史、截图、代理/审计日志、Referer 等场景即可泄露）。网关通过后进入 `/admin/login/`，而 `LoginRateLimitMiddleware` 硬编码只拦 `POST /api/auth/login`（`middleware.py:256`），Django 自带登录页无锁定机制。
- 后果：网关的「仅按钮点击可进入」保证被削弱为「2 分钟内的 URL 保密性」；若运维用 `SEED_ADMIN_PASSWORD` 指定了弱口令（`settings.py:153-164`，仅在未配置时才自动生成强随机），攻击者可在无次数限制下爆破 Django 后台超级管理员（`is_superuser`）。
- 修复建议：令牌携带一次性 nonce 并在服务端（缓存/表）标记已用；或直接改成「校验令牌后写入短 TTL 会话标记 + 绑定当前 session key」；对 `/admin/login/` 增加失败计数与锁定（可复用 `record_login_failure`）；默认不提供 `SEED_ADMIN_PASSWORD` 弱值并在部署脚本强制随机化。

---

## 存疑/待确认

- **[待确认] `guards._get_object` 对 `competition_id IS NULL` 的记录不做隔离**：`base_crud.py:78-82` 用 `if cid is not None and cid != user_cid` 判定，意味着任何「全局/无比赛归属」的行对所有比赛账号可读可改可删。当前 9 类 CRUD 模型的 `competition` 外键**全部非空**（`materials/regions/companies/consumer_demands/...` 已逐一核对），故不可利用；属潜在风险（将来加一列 `null=True` 即成跨比赛写）。
- **[待确认] 多进程/多 worker 部署下限流与进程内状态失效**：`_locks`（`middleware.py:203-210`）与 `signals._signals_enabled_local`（`signals.py:28`）都是进程内状态；当前部署以 `rundaphne` 单进程（`management/commands/rundaphne.py:50-60`）运行为前提，若改多 worker/多实例，锁定与 `suppress_signals` 的作用域都会失真。另 `_locks` 只按 `(ip, username)` 计数，用户名可任意指定 → 未认证请求可在 10 分钟窗口内堆积大量条目（内存放大），建议改为 LRU/带上限。
- **[待确认] `client_ip` 依赖反代注入 `X-Real-IP`**：`helpers.py:121-136` 仅在 `REMOTE_ADDR` ∈ 可信代理（默认回环）时才读 `X-Real-IP`，设计正确；但若 nginx 未设置该头，`REMOTE_ADDR=127.0.0.1` 会被当作可信代理而回退成 `127.0.0.1`，此时所有用户共享同一个限流键 `("127.0.0.1", username)` —— 任何人对任意用户名的 10 次失败即可让该账号对全体用户锁定 15 分钟（拒绝服务）。需核对 deploy 模板里的 nginx 配置。
- **[待确认] Decimal 出站类型翻转（同字段 number/string 不稳定）**：`renderers.py:28-33` 把整数值 Decimal 转 int（JSON number）、非整数值转字符串（`format(obj,"f")`），因此同一字段（如 `Fuel.pricePerLiter`）可能时而 `12`、时而 `"12.3456"`。若前端对该字段做 `+`/`-` 或未 `Number()` 转换，会出现字符串拼接或按字典序比较的错误结果。本次未审前端，标待确认，建议统一为「金额类一律字符串」并在前端统一转换，或统一为定点小数。
- **[待确认] `log_write` 的 `json.dumps` 未禁用 `allow_nan`**：即使修掉 C-02 的 Decimal 问题，若实例里含 `inf`/`nan`（见 C-03），审计 `changes` 文本会写入非标准 JSON token `Infinity`/`NaN`，审计页做 `JSON.parse` 时会失败；建议改用 `dumps_json_safe` + `allow_nan=False` 并同样做非有限值兜底。
- **[待确认] `MIDDLEWARE` 顺序中 `SecurityMiddleware` 排在自定义中间件之后**：`settings.py:264-280` 把 `django.middleware.security.SecurityMiddleware` 放在 `OperatorContext/LoginRateLimit` 之后，而 `SecurityHeadersMiddleware`（自定义）在其之前。当前 `SECURE_*` 全为默认关闭，行为无差异；若将来开启 `SECURE_SSL_REDIRECT`，重定向会晚于限流/审计中间件执行，出现「http 请求先被记账/计数再 301」的次序问题，建议把 `SecurityMiddleware` 提到最前。
- **[待确认] `parse_pagination` 的 key 名与 DRF 惯例不一致**：使用 `pageSize` 而非 `page_size`（`pagination.py:26`），且 `DEFAULT_PAGINATION_CLASS=None`（`settings.py:344`）；任何忘记走 `parse_pagination` 的视图会静默返回未分页全表（`PAGE_SIZE=50` 不生效），建议在公共层提供强制包装或在 code review 检查表中固化。
- **[待确认] `settings.py:305-310` 仅配置 SQLite 且无 `OPTIONS.timeout`**：并发写（信号审计 + 业务写 + 异常审计）在锁竞争下会抛 `database is locked`，与 C-05 叠加时更容易触发；生产若仍用 SQLite 建议显式设置 `timeout`/WAL，或迁移到 PostgreSQL。
