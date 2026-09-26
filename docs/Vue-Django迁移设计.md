# Vue + Django 重构迁移设计

> 目标：将现有 **Electron + Vue 3 + NestJS + Prisma + SQLite** 桌面应用重构为 **Vue 3 + Django + DRF + SQLite** 纯 Web 网站，**保持全部功能与界面不变**。
>
> 本文档为迁移真源（source of truth），所有迁移工作以此为依据。原 NestJS/Prisma 实现作为功能参照基准，迁移完成后保留 `server/`（NestJS）与 `client/electron/` 作为历史参照，不再演进。

---

## 1. 背景与约束

### 1.1 现状速览

| 维度 | 现状 |
|------|------|
| 客户端 | Electron 33 + Vue 3.4 + TS + Element Plus + Pinia + Vue Router(Hash) + Vite 7 + Socket.IO Client + vue-konva + ECharts |
| 服务端 | NestJS 11 + Prisma 6 + SQLite + @nestjs/jwt + Passport-JWT + Socket.IO 4 + nest-winston + bcryptjs + mathjs + zod + class-validator |
| 数据模型 | 34 个 Prisma 模型（用户/权限/比赛/生产链/地图/产业/合同/消息/股票/审计） |
| 业务模块 | 15 个 REST 模块 + 5 个全局守卫 + 实时广播 + 合同引擎 + 股票引擎 + 产业计算引擎 + 表达式 DSL |
| 实时 | Socket.IO（与 REST 同源同端口），按比赛房间 `comp-{id}` 隔离 + 用户私有房间 `user-{id}` + 重连补发 |
| 权限 | 三角色 + 31 个细粒度权限 key（18 域）+ 动作等级蕴含 + 5 个范围约束字段 |

### 1.2 硬性约束（必须 1:1 保持）

迁移过程中以下行为必须**完全等价**，前端代码尽量零改动（或最小改动）：

1. **REST 路由路径不变**：全部保持 `/api/<resource>` 前缀（如 `/api/materials`、`/api/contracts/:id/execute`）。
2. **响应包装格式不变**：成功 `{ code:0, message:"成功", data }`；错误 `{ code:<http或业务码>, message:"中文提示", data:null }`。
3. **分页协议不变**：`{ items, total, page, pageSize }`，参数 `page`/`pageSize`/`updatedAfter`/`requireExistingIds`，`pageSize` 上限 200。
4. **增量同步协议不变**：`updatedAfter` 返回 `{ incremental:true, items, serverTime, deletedIds?/existingIds? }`。
5. **JWT 不变**：HS256，issuer=`gipfel-competition`，audience=`gipfel-competition-client`，payload `{ sub, username, role, tv, cid }`，24h；`tokenVersion` 顶号；`mustChangePassword` 拦截。
6. **Socket.IO 协议不变**：握手 `auth.token`；事件 `subscribe`/`unsubscribe`/`sync:replay`/`resource:changed`/`permissions:changed`/`auth:required`/`subscribe:denied`/`sync:replay:result`；房间 `comp-{id}`/`user-{id}`；微批处理 50ms；环形缓冲 1000；重连补发限流 5 次/分钟。
7. **多租户隔离不变**：`competitionId` 自动注入/校验；`@NoCompetitionScope` 等价物；BOLA 归属校验。
8. **RBAC 不变**：31 个权限 key、动作等级蕴含（`manage ⊇ execute ⊇ audit ⊇ edit ⊇ view`）、5 个范围字段语义、角色模板授予上限。
9. **乐观锁不变**：`CompanyFieldValue.version` 写时 `where:{id,version}` + 自增，冲突 409。
10. **合同引擎可撤销不变**：`ContractFieldEffect` 叶子记录 + 删除合同时按 `executedAt` 重放增量精确撤销。
11. **股票引擎不变**：撮合 + 定价公式（限幅 ±10%、PE 联动/随机、碳排/幸福度字段绑定）、K 线生成、推进轮次。
    > **后续演进注记（2026-09-04，迁移完成后）**：股票引擎在迁移基线上已增强，与 NestJS 原版行为不再完全一致——
    > ① 平盘推进：无成交轮次价格不动但 round 照常 +1 并生成平盘 K 线（round 全局同步，K 线无空洞）；
    > ② 防连板硬约束（S10）：上一轮封板时本轮同侧限幅收紧为 `min(limitPct×0.94, 9.4%)`，连续涨跌停在数学上不可能；
    > ③ 做市商增强（S11）：反向动量偏置（mmSkewPct）+ 波动自适应价差 + 分级回归锚干预；
    > ④ 全链路 Decimal 精算与逐对价格-时间优先结算。详见 `backend/README.md`「股票引擎要点」与 `backend/apps/stock/engine.py` 模块文档。
12. **产业计算引擎不变**：`IndustryField.calcGraph`（GGraph）写入时级联重算；财年定时器 FY_START/FY_END。
13. **安全头/限流/CORS 不变**：CSP `default-src 'none'`、XFO DENY、XCTO nosniff、Referrer no-referrer、CORP（/uploads cross-origin 其余 same-origin）；登录限流 10 次/5 分钟锁 15 分钟；CORS 本地/私网反射+凭据，公网须白名单。
14. **审计日志不变**：写操作 + 异常上下文落 `AuditLog`，含 operatorId/ip/requestId/changes 脱敏。
15. **静态资源不变**：`/uploads/*`（地图背景图、消息图片）由服务端托管，CORP cross-origin。
16. **版本硬封锁语义调整**：桌面端原比对 `app.getVersion()`，Web 化后改为前端 `version.ts` 与 `/api/version` 比对（仍保留封锁逻辑，但来源从 Electron 包改为前端常量）。

### 1.3 项目记忆中的硬约束（必须延续）

- JWT_SECRET 缺失则服务启动失败
- WebSocket 握手必须校验 tokenVersion
- 分页必须 `parsePagination()`，pageSize 上限 200
- 合同执行/回滚必须事务包裹
- 删除 Company 必须两步确认（提示 + 名称校验）
- 股票推进轮次的多笔事件合并为单次 bulk WebSocket 事件
- 列表查询必须带 skip/take 分页
- Vue 动态组件须缓存引用防重初始化循环
- 渲染期计算属性须 `Array.isArray` + 可选链兜底

---

## 2. 技术栈映射

| 层 | 原（NestJS/TS） | 新（Django/Python） | 说明 |
|----|-----------------|---------------------|------|
| 语言 | TypeScript 5.9 | Python 3.11+ | |
| Web 框架 | NestJS 11 | Django 5.0 + DRF 3.15 | DRF 提供 ViewSet/Serializer |
| ORM | Prisma 6 | Django ORM | 1:1 迁移 schema.prisma → models.py |
| DB | SQLite | SQLite（保持） | 单文件 `db.sqlite3`，迁移期可独立文件 |
| JWT | @nestjs/jwt + passport-jwt | djangorestframework-simplejwt（自定义 payload） | 自定义 issuer/audience 校验 |
| 密码哈希 | bcryptjs cost=12 | Django `make_password`(bcrypt) | 哈希格式兼容 |
| 输入校验 | class-validator + zod | DRF Serializer + Pydantic（settings） | |
| 表达式求值 | mathjs（沙箱） | `simpleeval` / `RestrictedPython` | 安全沙箱，DSL 端口 |
| WebSocket | @nestjs/websockets + Socket.IO 4 | `python-socketio`（ASGI）+ `daphne` | 保持 Socket.IO 协议，前端零改动 |
| 日志 | nest-winston + daily-rotate-file | Python `logging` + `logging.handlers.TimedRotatingFileHandler` | |
| 异步上下文 | AsyncLocalStorage（operator） | `contextvars.ContextVar` | 操作员上下文注入日志 |
| 审计 | Prisma `$allOperations` 中间件 | Django `signals`（post_save/post_delete） + 自定义 queryset | 写操作审计 + 实时广播 |
| 进程管理 | PM2 | gunicorn/daphne + systemd | 生产部署 |
| 实时广播触发 | PrismaService 中间件 → RealtimeService | Django signals → `socketio.AsyncServer` 单例 | |
| 共享 DSL | `shared/engine-dsl`（TS） | `backend/engine_dsl`（Python 包） | 前端仍吃 TS 源码（不迁移前端 DSL） |

### 2.1 ASGI 部署架构

```
Django (ASGI) ── asgi.py
  ├── Django HTTP (DRF views)        → /api/*
  ├── Django static/media             → /uploads/*, /static/*
  └── python-socketio ASGIApp         → /socket.io/* (与 HTTP 同端口同源)
       └── AsyncServer(async_mode='asgi')
           ├── 握手鉴权（JWT）
           ├── 房间 comp-{id} / user-{id}
           ├── 微批处理 50ms
           └── 环形缓冲 + sync:replay
```

> 关键：python-socketio 提供原生 Socket.IO 协议服务端，前端 `socket.io-client` 无需任何改动，仅需把连接地址指向 Web 服务端。

---

## 3. 目录结构（目标）

```
.
├── backend/                           # 新 Django 服务端（替代 server/）
│   ├── manage.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── backend/                       # Django 项目包
│   │   ├── settings.py                # 环境校验（JWT_SECRET fail-fast）
│   │   ├── urls.py                    # /api 路由聚合 + 静态/uploads 挂载
│   │   ├── asgi.py                    # Django + socketio ASGI 装配
│   │   └── wsgi.py
│   ├── apps/
│   │   ├── common/                    # 横切：分页/权限装饰器/守卫/审计/日志/安全头/CORS/限流/上下文
│   │   │   ├── pagination.py          # parsePagination() 等价
│   │   │   ├── response.py            # ResponseInterceptor 等价包装
│   │   │   ├── exceptions.py          # HttpExceptionFilter 等价
│   │   │   ├── permissions.py         # catalog + hasPermission + 装饰器 + guard
│   │   │   ├── scope.py               # CompetitionScope/Ownership guard
│   │   │   ├── throttle.py            # 登录限流
│   │   │   ├── security.py            # securityHeaders 中间件
│   │   │   ├── audit.py               # 审计写入 + signals
│   │   │   ├── operator_context.py    # ContextVar 操作员上下文
│   │   │   ├── logging_config.py      # TimedRotatingFileHandler
│   │   │   └── expression.py           # safe-expression 沙箱
│   │   ├── users/                     # 用户 + 权限授予
│   │   ├── auth/                      # 登录/me/change-password/JWT
│   │   ├── competitions/             # 比赛 + 财年
│   │   ├── materials/
│   │   ├── parts/
│   │   ├── products/
│   │   ├── tech_tree/
│   │   ├── maps/
│   │   ├── infrastructures/
│   │   ├── fuels/
│   │   ├── vehicles/
│   │   ├── warehouses/
│   │   ├── production_lines/
│   │   ├── industry_types/           # 产业类型 + 字段 + 计算引擎
│   │   ├── companies/                 # 公司 + 字段值（乐观锁）
│   │   ├── contracts/                # 合同类型 + 合同 + 引擎 + 可撤销
│   │   ├── regions/
│   │   ├── consumer_demands/
│   │   ├── messages/
│   │   ├── stock/                     # 股票系统 + 引擎
│   │   ├── files/                     # 上传 + 地图背景
│   │   ├── realtime/                  # socketio gateway + service
│   │   └── audit/                     # AuditLog 模型 + 查询
│   ├── engine_dsl/                    # 表达式 DSL（Python 端口，对应 shared/engine-dsl）
│   │   ├── __init__.py
│   │   └── schema.py
│   ├── tests/
│   └── uploads/                       # /uploads 落盘根
│
├── client/                            # 前端（剥离 Electron，保留 Vue 全部）
│   ├── src/                           # 几乎不变
│   ├── index.html
│   ├── vite.config.ts                 # 移除 electron 插件
│   └── package.json                   # 移除 electron 依赖
│
├── shared/engine-dsl/                 # 保留（前端仍用 TS 源码）
├── server/                            # 保留作历史参照（不再演进）
├── docs/
└── tools/                             # 部署脚本改为 Django+gunicorn
```

---

## 4. 数据模型迁移（Prisma → Django ORM）

34 个模型逐一映射。要点：

- **枚举**：Prisma enum（SQLite 存字符串）→ Django `CharField(choices=...)`。
- **JSON 字段**（Prisma `String?` 存 JSON）→ Django `JSONField`（SQLite 支持）。保留默认值 `"{}"`/`"[]"`。
- **自关联多对多**（`TechPrerequisite`、`MapEdge`）→ 显式中间模型（与 Prisma 一致，保留复合主键）。
- **乐观锁**：`CompanyFieldValue.version` → 整数字段 + `update()` 时 `F('version') + 1` + `where version=old`，冲突抛 `FieldWriteConflict`（409）。
- **级联**：`onDelete: Cascade` → `on_delete=models.CASCADE`；`SetNull` → `SET_NULL`；`Restrict` → `PROTECT`。
- **唯一约束**：`@@unique` → `unique_together` / `UniqueConstraint`。
- **索引**：`@@index` → `indexes` in Meta。
- **时间戳**：`createdAt`/`updatedAt` → `auto_now_add`/`auto_now`。

### 4.1 模型清单（按迁移优先级分组）

| 优先级 | 分组 | 模型 |
|--------|------|------|
| P0 | 基础 | User, AuditLog, Competition, FiscalYear |
| P1 | 生产链 | Material, Part, PartMaterial, PartTechRequirement, Product, ProductPart, ProductTechRequirement, TechNode, TechPrerequisite |
| P1 | 地图物流 | MapNodeType, PathType, MapNode, MapEdge, Fuel, Vehicle, VehiclePathType, Infrastructure, Warehouse, ProductionLine |
| P2 | 产业合同 | IndustryType, IndustryField, Region, Company, CompanyFieldValue, ConsumerDemand, ContractType, Contract, ContractFieldEffect |
| P3 | 消息股票 | Message, MessageRecipient, Stock, StockFundsAccount, StockHolding, StockOrder, StockCandle |

> 完整字段定义见原 `server/prisma/schema.prisma`，迁移时逐字段对照，不增不减。

---

## 5. 认证与权限迁移

### 5.1 JWT

- 使用 `djangorestframework-simplejwt`，但**自定义 Token 类**以注入 `tv`(tokenVersion)/`cid`(competitionId) 并绑定 issuer/audience。
- 自定义 `JWTAuthentication`：校验 issuer/audience + `tokenVersion` 比对（不一致 401「账号已在其他设备登录」）+ `mustChangePassword` 拦截。
- 登录成功 `tokenVersion += 1`，签发新 token。
- 改密成功同样 `tokenVersion += 1` 吊销所有旧 token（含当前会话，防旧凭据残留）；前端改密后自动用新密码重新登录换发新 token，本设备会话无感续接。
- bcrypt 哈希与原 `bcryptjs` 兼容（`$2a$`/`$2b$` 前缀，cost 12）。

### 5.2 守卫链（Django 中间件/DRF permission 顺序）

原 NestJS 守卫顺序必须保留：

```
OperatorMiddleware (ContextVar 注入操作员)
→ securityHeaders
→ loginRateLimiter
→ CORS
→ JwtAuthGuard (JWT + tokenVersion)
→ MustChangePasswordGuard
→ CompetitionScopeGuard
→ OwnershipGuard (BOLA)
→ PermissionsGuard (RBAC)
→ View → Service → ORM (signals 审计 + 实时广播)
→ ResponseInterceptor (统一包装)
→ LoggingInterceptor
→ HttpExceptionFilter
```

Django 映射：

| NestJS 组件 | Django 实现 |
|-------------|-------------|
| OperatorMiddleware | `contextvars.ContextVar` 中间件，从 JWT 解析操作员注入 |
| securityHeaders | 中间件 set headers |
| loginRateLimiter | 中间件（仅 POST /api/auth/login） |
| CORS | `django-cors-headers` + 自定义 origin 校验（本地/私网反射+凭据，公网白名单） |
| JwtAuthGuard | DRF `IsAuthenticated` + 自定义 `tokenVersion` 校验 |
| MustChangePasswordGuard | DRF permission 类（除改密接口外拦截） |
| CompetitionScopeGuard | DRF permission 类 + queryset 过滤（自动 AND competitionId；非 SUPER_ADMIN 校验归属） |
| OwnershipGuard | DRF `get_object` 覆写 + competitionId 路径匹配 |
| PermissionsGuard | DRF permission 类 `@require_permissions()` |
| ResponseInterceptor | 自定义 DRF `render` 或中间件包装为 `{code,message,data}` |
| LoggingInterceptor | 中间件访问日志 |
| HttpExceptionFilter | DRF 自定义 `exception_handler` 脱敏 + 审计 |

### 5.3 权限目录端口

`apps/common/permissions.py` 直接端口 `server/src/permissions/catalog.ts`：

- `PERMISSION_CATALOG` 列表（18 域 31 key，与原完全一致）
- `DEFAULT_ACTION_RANKS` + 合同域自定义 `actionRank`
- `has_permission(role, permissions, required)` 动作等级蕴含
- `require_permissions(*keys)` 装饰器（等价 `@RequirePermissions`）
- 角色模板 `grant_ceiling`/`grant_extras`/`assert_grant_allowed`
- 5 个范围字段校验函数（`can_read_company_all_fields` 等）

---

## 6. 实时广播迁移（Socket.IO）

### 6.1 协议保持

前端 `client/src/realtime/socket.ts` 与 `resource-changed.ts` **零改动**。后端用 `python-socketio` 实现等价网关：

```python
# apps/realtime/gateway.py
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=...)
# 握手鉴权：connect 事件校验 auth.token (JWT + tokenVersion + mustChangePassword)
# join comp-{id} / user-{id} 房间（非 SUPER_ADMIN 校验比赛归属）
# sync:replay 处理 + 限流 5/min
# resource:changed 微批 50ms + 环形缓冲 1000
```

### 6.2 广播触发

原 `PrismaService.$allOperations` 中间件 → Django **signals**（`post_save`/`post_delete`）触发：

- 写操作审计落 `AuditLog`
- 调用 `RealtimeService.emit_resource_changed(resource, id, competitionId, action)`
- `MODEL_TO_RESOURCE` 映射表端口
- `GLOBAL_ENTITIES`（industry-types/contract-types/competitions）广播给全体

### 6.3 关键事件

| 事件 | 触发 | 数据 |
|------|------|------|
| `resource:changed` | 任何 REST 写操作 | `{resource, ids, action, competitionId, seq, ts}` |
| `permissions:changed` | 用户权限/角色/范围变更 | `{userId, version, seq, ts}` → user-{id} 房间 |
| `sync:replay:result` | 重连补发 | `{events, serverSeq}` |
| `auth:required` | tokenVersion 不一致/需改密 | `{reason}` |
| `subscribe:denied` | 跨比赛订阅 | `{competitionId, reason}` |

---

## 7. 引擎迁移

### 7.1 表达式 DSL（shared/engine-dsl → backend/engine_dsl）

端口 `schema.ts`（zod schema → Pydantic）+ 求值器（mathjs → simpleeval 沙箱）。前端继续用 TS 源码（`vite.config.ts` alias 不变）。

### 7.2 合同引擎（contract-engine.service → contracts/engine.py）

- `compute.ts`/`conditions.ts`/`effects.ts`/`values.ts` 端口
- 仅操作 `CompanyFieldValue`（FIELD 效果：ADD/SUB/SET）
- 写入 `ContractFieldEffect` 叶子记录（before/after/value/op）
- 删除合同时按 `executedAt` 重放其余合同增量精确撤销
- 全程 `transaction.atomic()`

### 7.3 股票引擎（stock/engine.ts → stock/engine.py）

- 撮合（BUY/SELL 按 price×quantity）
- 定价公式：限幅 ±10%、PE 联动（取公司产业字段实时值）/ 随机（±2 游走）
- 碳排/幸福度绑定区域总览字段实时值
- 生成 `StockCandle`（open/high/low/close/changePct）
- 推进轮次：多笔事件合并为单次 bulk `resource:changed`

### 7.4 产业计算引擎（industry-calc-engine.service → industry_types/calc_engine.py）

- `IndustryField.calcGraph`（GGraph）写入时级联重算本字段
- 财年定时器：`FiscalYear.status` 切换驱动 FY_START/FY_END → 写 `timerValue`

---

## 8. 前端 Web 化迁移

### 8.1 移除 Electron

- `client/electron/` 目录：保留作历史，不再构建
- `client/package.json`：移除 `electron`/`electron-builder`/`vite-plugin-electron`/`vite-plugin-electron-renderer` 依赖与脚本；`main` 字段删除
- `client/vite.config.ts`：移除 electron 插件配置，改为纯 Vite web 配置
- `client/src/main.ts`：移除 `ensureStorageMigration`/`deleteOldAccountDbs` 中 Electron 专属逻辑（保留账号命名空间隔离）
- `client/src/utils/accountStorage.ts`：保留 localStorage 命名空间隔离，移除 Electron store 依赖
- `client/src/config/index.ts`：默认服务端地址仍为 `http://localhost:8000`（Django 默认端口），开发代理到后端

### 8.2 版本硬封锁调整

- 原：`app.getVersion()`（Electron）vs `/api/version`
- 新：前端 `client/src/data/version.ts` 常量 vs `/api/version`，比对逻辑保留
- 不一致仍弹不可关闭提示 + 5 分钟轮询

### 8.3 跨域与凭据

- 开发：Vite dev server (5173) → Django (8000)，需 CORS 允许 localhost:5173
- 生产：Django 同时托管前端静态文件（`collectstatic` + 前端 `dist`），同源，无需 CORS

### 8.4 路由

- 保持 Hash 模式（`createWebHashHistory`），Web 部署无需服务器 rewrite 配置

### 8.5 保留不变的前端模块

以下**完全不动**（确保界面与功能不变）：
- 所有 `views/`、`components/`、`stores/`、`router/`、`composables/`、`api/cache.ts`、`realtime/`、`permissions/catalog.ts`
- Element Plus / ECharts / vue-konva / pinyin-pro 全部依赖
- 全局样式、主题

---

## 9. REST 路由清单（必须 1:1）

保持原 NestJS 所有 Controller 前缀与子路由。核心路由（节选，完整见原代码）：

| 模块 | 路由前缀 | 关键子路由 |
|------|----------|-----------|
| auth | /api/auth | login, me, change-password |
| users | /api/users | CRUD + grant-permissions |
| competitions | /api/competitions, /api/fiscal-years | CRUD + 财年切换 |
| materials/parts/products/... | /api/<resource> | CRUD + 增量 |
| maps | /api/maps/full, /map-nodes, /map-edges, /map-node-types, /path-types | 复合地图 + 子资源 |
| industry-types | /api/industry-types, /api/industry-fields | 字段配置 |
| companies | /api/companies, /api/company-fields/:id | 字段读写（乐观锁） |
| contracts | /api/contract-types, /api/contracts, /api/contracts/:id/execute | 引擎 |
| regions | /api/regions | |
| consumer-demands | /api/consumer-demands | |
| messages | /api/messages, /api/message-attachments | 发布/收件箱/未读 |
| stock | /api/stocks, /api/stock-accounts, /api/stock-orders, /api/stock-holdings, /api/stock-candles, /api/stocks/:id/advance | 引擎 |
| files | /api/files/upload, /api/files/map-background | |
| health | /api/health | 无鉴权 |
| version | /api/version | 无鉴权 |

### 9.1 前端 API 契约（权威，基于 frontend/src/api/index.ts）

以下为前端实际调用的全部端点，后端必须 1:1 实现。响应统一经 JSONRenderer 包装为 `{code,message,data}`。

**简单 CRUD（createCrud 工厂，GET/POST/PUT/PATCH/DELETE）**：
| 资源 | 路径 | 权限(view/edit) |
|------|------|-----------------|
| materials | /materials, /materials/:id | data:material:view/edit |
| parts | /parts, /parts/:id | data:part:view/edit（含 partMaterials/techRequirements 嵌套） |
| products | /products, /products/:id | data:product:view/edit（含 productParts/techRequirements 嵌套） |
| tech-nodes | /tech-nodes, /tech-nodes/:id | data:tech:view/edit（含 prerequisites） |
| infrastructures | /infrastructures, /infrastructures/:id | data:infrastructure:view/edit |
| fuels | /fuels, /fuels/:id | data:fuel:view/edit |
| vehicles | /vehicles, /vehicles/:id | data:vehicle:view/edit（含 vehiclePathTypes） |
| warehouses | /warehouses, /warehouses/:id | data:warehouse:view/edit |
| production-lines | /production-lines, /production-lines/:id | data:productionLine:view/edit |

**地图模块**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /maps/full | GET | data:map:view（返回 nodes/edges/nodeTypes/pathTypes） |
| /map-node-types | GET/POST | data:map:view/edit |
| /map-node-types/:id | PATCH/DELETE | data:map:edit |
| /path-types | GET/POST | data:map:view/edit |
| /path-types/:id | PATCH/DELETE | data:map:edit |
| /map-nodes | GET/POST | data:map:view/edit |
| /map-nodes/:id | PATCH/DELETE | data:map:edit |
| /map-edges | GET/POST | data:map:view/edit |
| /map-edges/:id | PATCH/DELETE | data:map:edit |

**产业系统**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /industry-types | GET/POST | industryType:view/manage |
| /industry-types/:id | GET/PATCH/DELETE | industryType:view/manage |
| /industry-types/:id/fields | GET/POST | industryType:view/manage |
| /industry-types/fields/:fieldId | PATCH/DELETE | industryType:manage |
| /industry-fields | CRUD | industryType:view/manage（兼容别名） |

**公司与字段**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /companies | GET/POST | company:view/manage |
| /companies/:id | GET/PATCH/DELETE | company:view/manage |
| /company-fields/:companyId | GET/PUT | company:view/manage（乐观锁 version） |
| /company-fields/:companyId/:fieldId | PUT | company:manage |

**区域与消费者需求**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /regions | GET/POST | data:region:view/edit |
| /regions/:id | DELETE | data:region:edit |
| /regions/map-overview | GET | data:region:view |
| /regions/by-name/:name/overview-cards | PUT | data:region:edit |
| /consumer-demands | GET/POST | consumer-demand:view/edit |
| /consumer-demands/:id | PATCH/DELETE | consumer-demand:edit |

**合同**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /contract-types | GET/POST | contractType:view/manage |
| /contract-types/:id | GET/PATCH/DELETE | contractType:view/manage |
| /contracts | GET/POST | contract:view/manage |
| /contracts/:id | GET/PATCH/DELETE | contract:view/manage |
| /contracts/:id/execute | POST | contract:execute/audit |
| /contracts/:id/trial | POST | contract:view（试算） |
| /contracts/:id/party-numbers | PATCH | contract:manage/execute/audit |
| /contracts/:id/precheck | POST | contract:view |
| /contracts/:id/status | PATCH | contract:manage |

**消息**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /messages | GET/POST | message:view/manage |
| /messages/inbox | GET | message:view |
| /messages/sent | GET | message:view |
| /messages/:id | GET/DELETE | message:view/manage |
| /messages/:id/read | PATCH | message:view |
| /messages/read-all | POST | message:view |
| /messages/unread-count | GET | message:view |
| /messages/selectable-users | GET | message:manage |
| /messages/upload-image | POST | message:manage |

**股票**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /stocks | GET/POST | stock:view/manage |
| /stocks/:id | GET/PATCH/DELETE | stock:view/manage |
| /stocks/:id/candles | GET | stock:view |
| /stocks/pb-sources | GET | stock:view |
| /stocks/advance-round?competitionId= | POST | stock:manage |
| /stocks/accounts/list | GET | stock:view/edit/manage |
| /stocks/accounts/overview | GET | stock:view/edit/manage |
| /stocks/accounts/:id | GET/PATCH/DELETE | stock:edit/manage |
| /stocks/accounts/:id/holdings | GET | stock:view/edit/manage |
| /stocks/accounts | POST | stock:manage |
| /stocks/orders/list | GET | stock:view/edit/manage |
| /stocks/orders | POST | stock:view |
| /stocks/orders/:id | DELETE | stock:view |
| /stocks/holdings/list | GET | stock:view/edit/manage |
| /stock-accounts, /stock-orders, /stock-holdings | CRUD | 兼容别名 |
| /stock-candles?stockId= | GET | stock:view |

**文件**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /files/upload | POST | 登录 |
| /files/map-background | GET/POST/DELETE | 登录 |
| /files/map-background/transform | PATCH | 登录 |

**审计**：
| 端点 | 方法 | 权限 |
|------|------|------|
| /audit-logs | GET | account:manage |

---

## 10. 分阶段实施计划

### 阶段 0：文档与脚手架
- [x] 本设计文档
- [x] Django 项目脚手架（settings/asgi/urls/env 校验）
- [x] requirements.txt / manage.py / .env.example
- [x] 前端重写完成（剥离 Electron，全部置于 GipfelBusinessCompetitionManagerWeb/frontend）

### 阶段 1：基础与认证 ✅
- [x] P0 数据模型迁移（User/AuditLog/Competition/FiscalYear）
- [x] JWT + 守卫链（JwtAuth/MustChangePassword/CompetitionScope/Ownership/Permissions）
- [x] auth/users 模块（登录/me/change-password/CRUD/权限授予）
- [x] 权限目录 + 角色模板（39 key + has_permission + assert_grant_allowed）
- [x] 安全头/CORS/限流/审计/日志/操作员上下文（apps/common/*）
- [x] 默认超管自举（apps/auth/bootstrap.py + post_migrate 信号）
- [x] competitions 模块（CRUD + 财年）
- [x] audit 模块（只读列表）

### 阶段 2：实时广播 ✅
- [x] python-socketio ASGI 装配（backend/asgi.py 按 path 前缀分发）
- [x] 网关（connect JWT 鉴权、tokenVersion 顶号、user-{id}/comp-{id} 自动入房、subscribe 兼容 { room / competitionId / userId } 三键）
- [x] sync:replay 按 lastSeq 过滤补发（环形缓冲保留最近 5000 条，按 user/comp 权限过滤）
- [x] RealtimeService：全局 seq 计数器 + MOEL_TO_RESOURCE 映射 + 契约对齐 ResourceChangedEvent（resource/ids/action/competitionId/seq/ts）
- [x] 跨线程安全 emit：gateway.connect 注册 ASGI loop，同步侧用 asyncio.run_coroutine_threadsafe 把 await sio.emit 投递到 daphne loop（解决 Django HTTP 同步视图里 AsyncServer.emit 静默失败的问题）
- [x] permissions:changed / auth:required 事件发布器；用户赋权接口 bump permission_version 并实时通知前端刷新缓存
- [x] signals 触发审计 + 广播：apps/common/signals.py 统一 connect 所有 tracked 模型 post_save/post_delete → log_write + emit_resource_changed；`suppress_signals` 上下文管理器供批量 ORM 操作临时屏蔽 per-row 事件
- [x] 股票推进轮次合并广播：advance_round 内 `with suppress_signals():` 包裹 ORM 循环，结束后统一 `emit_resource_changed("stocks", None, comp_id, "bulk")`

### 阶段 3：生产链与地图（P1）✅
- [x] 通用 CRUD 基类（apps/common/base_crud.py：分页/权限/冲突检测/删除影响/路由生成）
- [x] materials
- [x] parts（Part + PartMaterial/PartTechRequirement 嵌套 include + 事务全量替换 + 删除影响）
- [x] products（Product + ProductPart/ProductTechRequirement 嵌套 include + 事务全量替换 + 删除影响）
- [x] tech_tree（TechNode + TechPrerequisite 自引用前置依赖）
- [x] maps（复合 /maps/full + nodeTypes/pathTypes/nodes/edges 子资源）
- [x] infrastructures
- [x] fuels
- [x] vehicles（Vehicle + VehiclePathType 嵌套 include + 事务全量替换；fuel PROTECT）
- [x] warehouses
- [x] production_lines

> 注：parts/products/vehicles 的外键指向兄弟应用（materials/tech_tree/fuels/maps），
> 采用字符串外键引用（如 `"materials.Material"`、`"maps.PathType"`），`makemigrations` 时解析；
> 循环依赖（companies↔regions、parts/products 自引用 tech_node）拆为 0001+0002 两个迁移解决；
> 删除影响中对跨应用关联表（ProductPart/ConsumerDemand）的统计采用请求期延迟导入。

### 阶段 4：产业与合同（P2）✅
- [x] industry_types（字段 + 计算图校验 + 财年定时器；全局资源返回数组；默认「所在地」字段）
- [x] companies（字段读写 + 乐观锁 version + 级联重算）
- [x] company_fields（公司字段值独立端点）
- [x] contracts（ContractType + Contract + ContractFieldEffect + 引擎 engine.py）
- [x] regions/consumer_demands（地图总览聚合 + 消费需求管理）

### 阶段 5：消息与股票（P3）✅
- [x] messages（label=gipfel_messages，规避与 django.contrib.messages 冲突；发布/收件箱/未读/图片）
- [x] stock（引擎 + 推进轮次 + 账户/持仓/订单/K线 + bulk 广播）
- [x] files（上传 + 地图背景）

### 阶段 6：联调与部署 ✅
- [x] 迁移生成与应用：`makemigrations` + `migrate` 全部 24 个应用通过（含 0001/0002 拆分）
- [x] Django `check` 无错误（静默 DRF W001——分页为自定义 parsePagination）
- [x] 启动验证：daphne ASGI 启动正常
- [x] 端到端联调：登录/JWT/改密拦截/me/competitions CRUD/materials/maps/full/stocks/industry-types/version 全部 200
- [x] 默认超管自举修复：post_migrate 信号去掉 sender 限定，`migrate` 即自动建 admin（幂等）
- [x] 前端验证：`vue-tsc --noEmit` 零错误；`vite build` 成功（剥离 Electron，移除无用 engine-dsl 别名）
- [x] README：[README.md](../README.md)（架构总览/三行启动/环境要求/安全合规/常用命令速查）；[backend/README.md](../backend/README.md) 与 [frontend/README.md](../frontend/README.md) 子项目说明
- [x] Linux 部署：[deploy-linux.sh](../scripts/deploy-linux.sh)（apt/node20/venv/migrate/npm build/systemd gipfel.service/nginx vhost/回滚策略）；配套模板 [gipfel.service](../deploy/gipfel.service)、[nginx-gipfel.conf](../deploy/nginx-gipfel.conf)
- [x] 部署：生产部署统一走 Linux [deploy-linux.sh](../scripts/deploy-linux.sh)（Windows 不再单独提供生产部署脚本；Windows 仅保留开发启动脚本）
- [x] Windows 开发启动：[bootstrap-dev.bat](../scripts/bootstrap-dev.bat) 首次初始化 + [start-dev.bat](../scripts/start-dev.bat) 校验后拉起 [dev.py](../scripts/dev.py) 监管进程（Django+Vite+日志查看器，Ctrl+C 一次优雅停止全部，[stop-dev.bat](../scripts/stop-dev.bat) 兜底强停）
- [x] 部署手册：[deploy/README.md](../deploy/README.md)（方案 A Linux / B Windows / C Docker 示例 + 升级与回滚）

---

## 11. 验证标准

迁移完成的判定标准（逐项确认）：

1. 前端 `client/` 除 Electron 相关外**零改动**，`npm run dev` 启动后连接 Django 后端功能正常
2. 34 个数据模型字段与约束 1:1 对应
3. 所有 REST 路由可访问，响应格式一致
4. Socket.IO 实时事件（resource:changed/permissions:changed/sync:replay）行为一致
5. 登录/JWT/顶号/改密拦截/权限/范围 全部生效
6. 合同执行/撤销、股票推进轮次、产业字段级联重算 结果与原系统一致
7. 审计日志、安全头、CORS、限流 行为一致
8. 界面完全不变（所有 views/components 样式与交互保留）

---

## 12. 风险与回退

| 风险 | 缓解 |
|------|------|
| python-socketio 与 Django ASGI 集成复杂 | 先做最小 PoC 验证握手/房间/广播 |
| 表达式沙箱语义差异（mathjs vs simpleeval） | 保留原测试用例，迁移后跑通 |
| 合同/股票引擎业务逻辑复杂 | 逐函数端口 + 原单测验证 |
| 增量同步协议细节多 | 保留原 e2e 测试场景 |
| 迁移期数据迁移 | 提供 Prisma→Django 数据导入脚本（如需保留历史数据） |

> 原 NestJS 服务端保留在 `server/`，迁移验证完成前不删除，可随时回退对照。

---

## 13. 迁移期缺陷修复记录

### 1. 顶部栏财年恒显「未开启财年」（严重，2026-08-31）

- **文件**：`frontend/src/stores/competition.ts`（主因）、`backend/apps/competitions/serializers.py`、`backend/apps/competitions/views.py`、`backend/apps/realtime/emit.py`
- **现象**：比赛管理页开始/结束财年后，左上角财年标签会刷新一次，但最终**恒定**显示「未开启财年」，即使库中确实存在 `status=ACTIVE` 的财年（实测 `fiscal_years` 表有 `year=1, status=ACTIVE`）。
- **根因（主因，前端）**：Django 端 `GET /api/competitions/:id/fiscal-years` 遵循分页契约返回 `{items,total,page,pageSize}`，再经 `JSONRenderer` 包装成 `{code,message,data}`；而 store 的 `loadFiscalYear` 使用**裸 `fetch`**（为避开缓存层而绕过 `api/request.ts`，因此也绕过了 `extractItems` 的分页解包），直接执行 `json.data.find(...)`。在对象上调用数组方法抛 `TypeError` → 被 `catch` 捕获 → 走 `currentFiscalYear.value = null` 兜底分支，于是**每次回源都稳定退化**为「未开启财年」。这也解释了「乐观更新已生效（会刷新）但最终值仍错」的表象：乐观值随后被失败的回源覆盖。
- **修复**：`loadFiscalYear` 自行做响应形态归一，兼容裸数组与分页对象两种形态：

```ts
const payload = json?.data ?? json;
const fys = Array.isArray(payload) ? payload : (payload?.items ?? []);
```

- **配套修复（与 NestJS 契约对齐）**：
  1. `CompetitionSerializer.to_representation` 补 `fiscalYears`（NestJS 侧 `findAll/findOne` 用 `include: { fiscalYears: true }`），修复比赛列表「当前财年」列恒为空（`getCurrentFiscalYear(comp)` 读 `comp.fiscalYears`）；列表查询同步加 `prefetch_related("fiscal_years")` 避免 N+1。
  2. 财年 create/update 补 `fiscal-year:changed` 房间广播（新增 `emit_to_competition`，房间 `comp-<id>`，载荷 `{competitionId, fiscalYear}`）。前端 `handleFiscalYearChanged` 早已监听该事件，但 Django 侧此前从未发出，导致多端/多标签页无法实时同步。
- **约束提醒**：财年年号从 **0** 起（首个财年 `year = 0`），判空一律用 `!== null`，禁止真值判断。
- **财年定时器已补齐（2026-08-31）**：新增 `backend/apps/company_fields/timer.py` 的 `apply_fiscal_year_timer(competition_id, trigger)`，对齐 NestJS `CompanyFieldsService.applyFiscalYearTimer`：
  - 捞出 `timer_enabled && timer_trigger==trigger` 的 `IndustryField`，按 `industry_type_id` 分组，批量写入该比赛对应产业类型公司（避免 N+1）；
  - 写入值经 `_serialize` 按字段类型序列化（DICTIONARY/LIST 为 JSON 文本）；`timer_value` 形如 `field:<fieldKey>` 时取被引用字段当前值（基于触发前快照，避免相互引用顺序依赖）；引用源缺失则 warn 跳过不中断；
  - 每公司写入后调 `_recompute_calc_fields`（calcGraph 引擎接入后真正生效），并广播 `company-field:changed` 让同比赛前端刷新。
  - 触发点：`FiscalYearCreateView`（POST 新建 → FY_START）、`FiscalYearUpdateView`（PATCH 非 ACTIVE→ACTIVE → FY_START；非 CLOSED→CLOSED → FY_END），与 NestJS `competition.service.ts` 的 trigger 推导一致。
  - 验证：构造测试产业/公司/字段，`apply_fiscal_year_timer(comp.id,"FY_START")` 实测 —— 常量字段 `cash_base` 写入 `100.0`、`field:cash_base` 引用字段 `cash_disp` 解析为被引用字段当前值 `55.0`，符合预期。
