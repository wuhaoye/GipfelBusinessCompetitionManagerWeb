# Backend · Django 5

## 快速启动

```bash
# 1. 环境
cp .env.example .env        # 默认即可；生产务必改 JWT_SECRET
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 数据库 + 种子（首次 migrate 自动建 admin/admin23）
python manage.py migrate

# 3. 启动 daphne（HTTP + Socket.IO 同源同端口，端口来自 .env 的 PORT，默认 8000）
#    daphne 已在 INSTALLED_APPS 首位并接管 Django 自带的 runserver 命令，因此
#    `manage.py runserver` 实际即以 ASGI/daphne 运行，HTTP + Socket.IO WebSocket 同源同端口。
#    生产推荐用项目自带的 rundaphne 命令，端口自动取 .env 的 PORT（前端跳转按钮也会跟随该端口）：
python manage.py rundaphne
#    如需局域网/容器访问（绑定所有网卡）：
python manage.py rundaphne --bind 0.0.0.0
#    等价原生命令（端口需与 .env PORT 保持一致）：
#    daphne -b 127.0.0.1 -p 8000 backend.asgi:application
```

## 排错：开发期 `[vite] ws proxy error: read ECONNRESET`

前端经 Vite 开发代理（`/socket.io` → 后端）连接 Socket.IO 时，控制台偶发
`read ECONNRESET` 并触发反复重连。根因与修复：

- **根因**：Vite 代理目标曾写 `http://localhost:8000`，而 Windows 上 `localhost` 优先解析到
  IPv6 `::1`；daphne 默认只监听 IPv4，代理先对 `::1` 建连被 RST 再回退 IPv4，表现为一连串 RST。
  另外 Socket.IO 默认「先轮询再升级 WebSocket」，升级时废弃的轮询长连接被中断也会产生 RST 噪声。
- **已修复**：`frontend/vite.config.ts` 代理目标已改为 `http://127.0.0.1:8000`（显式 IPv4）；
  `frontend/src/realtime/socket.ts` 客户端已限定 `transports: ["websocket"]`（跳过轮询长连接）。
- **若仍出现**：确认启动 daphne 时绑定的是 IPv4（`-b 127.0.0.1` 或 `0.0.0.0`），不要只绑 `::1`；
  且后端进程未被重启/崩溃（重启后端会令在飞连接 RST，属正常，客户端会自动重连）。


## 健康检查

```bash
curl http://127.0.0.1:8000/api/health
# 期望: {"code":0,"message":"成功","data":{"status":"ok"}}
curl http://127.0.0.1:8000/api/version
# 期望: {"code":0,"message":"成功","data":{"version":"...","port":8000,
#        "log_viewer_port":8120,"log_viewer_url":"http://127.0.0.1:8120/"}}
```

## 登录并改密

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin23"}' | jq -r .data.token)

# 此时若直接访问受保护接口会 401 initial_password_must_be_changed
# 先改密（响应里会带回新 token + user，直接续接；详见 [apps/auth/views.py](apps/auth/views.py)）：
curl -s -X POST http://127.0.0.1:8000/api/auth/change-password \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"oldPassword":"admin23","newPassword":"Admin@2026"}'
# 改密成功后端递增 token_version 吊销所有旧 token（含当前 TOKEN），并在同一请求
# 内签发新 token 随响应返回（规避 SQLite 写后读竞态，避免二次 /login 触发偶发 401）。
# 脚本/SDK 调用方须用响应里的 .data.token 续接，不能继续用旧 $TOKEN。
```

## 环境变量

| 变量 | 默认 | 必填 | 说明 |
| --- | --- | --- | --- |
| `JWT_SECRET` | — | ✅ | HS256 签名密钥；空值进程直接退出（fail-fast） |
| `DJANGO_SECRET_KEY` | 未设置 → 回退 `JWT_SECRET` | | Django 自身 SECRET_KEY（session/CSRF 签名），生产建议配置独立值与 JWT 密钥分离；更换后现有 session/CSRF cookie 失效（用户需重新登录），择机轮换 |
| `TRUSTED_PROXIES` | 仅回环（127.0.0.1 / ::1） | | 可信代理 IP（逗号分隔）：仅当请求来自该集合时才信任其 `X-Real-IP`（取客户端真实 IP，用于登录限速等按 IP 防护）；标准 nginx 反代部署无需设置 |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost,::1` | | Django Host 头白名单（逗号分隔）。**公网部署必填**：缺失时对一切非回环 Host 的请求返回原生 400 Bad Request（登录/健康检查全部失败，前端表现为「请求参数错误」）。deploy-linux.sh / update-from-github.sh 会自动写入域名或公网 IP（幂等） |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `""` | | Django CSRF Origin 白名单（逗号分隔，`scheme://host[:port]` 显式格式）。`/admin` 登录表单等带 Origin 的 POST 必须命中，否则 403；deploy 脚本传 `--domain` 时自动写入，纯 IP 部署经 nginx 反代通常无需配置 |
| `DEBUG` | `false` | | `true` 启用 Django debug-toolbar（需额外安装）与详细错误页 |
| `PORT` | `8000` | | 后端监听端口（单一真源）：`python manage.py rundaphne` 按其绑定 daphne，并经 `/api/version` 下发给前端「系统设置 → 后端管理」区块的红色「后端管理界面」与黄色「日志查看器」跳转按钮 |
| `LOG_LEVEL` | `INFO` | | `DEBUG/INFO/WARNING/ERROR/CRITICAL` |
| `LOG_DIR` | `./logs` | | 日志目录；自动创建 |
| `UPLOAD_DIR` | `./uploads` | | 文件上传目录；自动创建；前端 `/uploads/*` 由此托管 |
| `CORS_ORIGIN` | `""` | | 公网部署必填，逗号分隔白名单（或含 `*` 则全部放行）；未配置仅本地/私网 |
| `SEED_ADMIN_USERNAME` | `admin` | | 首次 migrate 自动建的超级管理员用户名（业务超管 + 后台超管共用）；已存在则跳过 |
| `SEED_ADMIN_EMAIL` | `admin@example.com` | | 后台超管邮箱 |
| `SEED_ADMIN_PASSWORD` | `admin23` | | 首次 migrate 自动建的超级管理员密码；**`deploy-linux.sh` 首次部署会自动生成随机强密码并写入 `.env`**；手动部署时未配置则兜底 `admin23`；生产务必改强密码 |
| `JWT_ISSUER` | `gipfel-competition` | | JWT iss |
| `JWT_AUDIENCE` | `gipfel-competition-client` | | JWT aud |
| `JWT_EXPIRES_IN` | `24h` | | 支持 `Nh/Nm/Ns/Nd` |
| `DATABASE_URL` | 未设置 → SQLite `./db.sqlite3` | | `postgres://user:pw@host/dbname` |
| `LOG_VIEWER_PORT` | `8120` | | 日志查看器服务端口（Windows 开发由 `scripts/dev.py`（`start-dev.bat` 拉起）读取，Linux 由日志查看器启动脚本读取） |
| `LOGVIEWER_SECRET_KEY` | 未设置 → 回退 `JWT_SECRET` | | 日志查看器 / `/admin` 防直连一次性令牌的签名密钥；主后端签发、日志查看器校验，两者必须读到同一值；生产建议配置独立强随机值 |
| `LOG_VIEWER_PUBLIC_URL` | 未设置 → 按请求 Host 推导 | | 日志查看器公网地址（`/api/version` 下发的 `log_viewer_url` 用），如 `https://log.example.com/`。进程只取其中的 hostname 推导 ALLOWED_HOSTS（端口与 path 被忽略） |
| `LOGVIEWER_ALLOWED_HOSTS` | 未设置 → 仅回环 + `LOG_VIEWER_PUBLIC_URL` 主机 + `DJANGO_ALLOWED_HOSTS` | | 日志查看器服务自身 Host 头白名单的兜底追加项（逗号分隔 host，可含 `host:port` 或 `[IPv6]:port`，端口/方括号会被剥掉），如 `log.example.com,43.142.77.225`；一般无需配置（`DJANGO_ALLOWED_HOSTS` 已被 deploy 写入公网 IP/域名，**会自动纳入**） |
| `BACKEND_GATE_MAX_AGE` | `120` | | `/admin` 防直连一次性令牌有效期（秒） |

## 应用注册顺序（依赖链）

```
common (无依赖)
users  →  common
auth   →  common + users
audit  →  common + users
realtime →  common + users
competitions → common
materials / parts / products / tech_tree / maps / infrastructures / fuels / vehicles / warehouses / production_lines → competitions
industry_types (全局)
companies + company_fields → competitions + industry_types
contracts → competitions + companies
regions + consumer_demands → competitions + maps
messages (label=gipfel_messages) → competitions + users
stock → competitions + companies + users
files → common
announcements → common
widget_packages → common
snapshots → common + realtime（快照/回退/全局门禁，注册表自动纳入上面全部业务模型）
```

> `apps.messages` label 显式取 `gipfel_messages`，避免与 `django.contrib.messages` 冲突。

## 通用 CRUD 契约

所有「资源列表/详情」类路由都遵循以下模式：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET    | `/api/{resource}?page=1&pageSize=10&competitionId=1` | 分页列表；比赛隔离自动套用 |
| POST   | `/api/{resource}` | 创建；业务校验通过后返回单条资源 |
| GET    | `/api/{resource}/:id` | 详情 |
| PUT    | `/api/{resource}/:id` | 整体更新（含嵌套关联会事务内全量替换） |
| PATCH  | `/api/{resource}/:id` | 部分更新；简单字段与 PUT 等价，嵌套关联推荐用 PUT |
| DELETE | `/api/{resource}/:id` | 删除；PROTECT 外键阻塞返回 409 |
| GET    | `/api/{resource}/:id/impact` | **删除影响预览**：返回受影响关联对象计数，前端两步删除 UI 用 |

响应统一 `ResponseEnvelope<T>`：

```json
{
  "ok": true,
  "message": "",
  "data": { "id": 1, "name": "..." },   // T
  "error": null
}
```

分页列表的 `data` 还带 `pagination` 字段。

## 大数支持（千万京 10^23 级）

系统分层支持大数金额，回归测试见 `tests/big_number_smoke.py`：

- **公司产业字段值（主战场）**：`CompanyFieldValue.value` 为 TEXT 列，存储无上限；计算链路（合同引擎 `apply_field_effect` / 表达式求值器 `safe_evaluate` / `apply_op` 算术 / 派生字段 `calc.py` / 财年定时器 `timer.py`）全部 **int（Python 任意精度整数）优先 + Decimal（50 位有效数字）**，10^23 量级 ADD/SUB/MUL 完全无损，不再产生中间 float。
- **出站 JSON 兜底**：统一渲染器（`apps/common/response.py` + `renderers.py`）把绝对值 > 2^53 的 int / 大 Decimal 递归转为**字符串**出站——前端 `JSON.parse` 不会丢精度；小整数（id/version 等）保持 number。
- **引擎 JSON 序列化**：`dumps_engine_json` 兼容 int/Decimal（Decimal 整数值 → number、小数 → 字符串）。
- **前端展示**：`utils/format.ts` 的 `formatMoney` 大数走 BigInt 千分位（字符串直通不经 Number）；`formatMoneyCN` 提供万/亿/兆/京/垓 中文单位紧凑显示。
- **数值列上限**：股票/仓储/载具等 `DecimalField(max_digits=30)`——校验层放宽，但注意 **SQLite 平台限制：DecimalField 实际只有 ~15 位有效数字**（NUMERIC affinity + Django prec=15 converter），超 10^15 的精确值请走公司字段值链路；切换 PostgreSQL 后数值列即获得真正任意精度。
- **已知边界**：合同 `EXP`/`LOG` 等超越函数仍为 float（结果量级小，无影响）；`DIV` 整除走 int、非整除走 Decimal（50 位）。

## 股票引擎要点（apps/stock/engine.py）

回合制**集合竞价**模型：每轮收集订单 → 统一撮合 → 轮末未成交订单作废。

- **撮合**：成交判定 = 最高买价 ≥ 最低卖价；信号价 = 最优两档中点；结算为完整的价格-时间优先逐对撮合，成交价夹入 `[卖价, 买价]` 不违反任何一方限价；买方受现金约束、卖方受持仓约束，金额守恒；全链路 Decimal。
- **定价**：`final = 限幅(tradePriceWeight × 成交价 + (1-w) × 理论价, 上轮收盘 × (1±limitPct))`；理论价 = 上轮收盘 × (1 + 净买压力×maxMovePct + 幸福度/碳排趋势偏置×maxMovePct)。
- **平盘推进**：无成交（或无任何订单）时价格不动，但 round 照常 +1 并生成平盘 K 线——各股票 round 全局同步、K 线序列无空洞。
- **防连板硬约束（S10）**：上一轮封板（|涨跌| ≥ 9.9%）时本轮同侧限幅收紧为 `min(limitPct×0.94, 9.4%)`——无论玩家如何挂单、无论 limitPct 配置多大，连续涨停/跌停在数学上不可能；定价与 K 线上下界同步生效。
- **做市商（S11）**：每轮撮合前自动生成买卖挂单提供流动性（深度按总股本 × `mmDepthPct` 自动计算，钳制在 `mmMinQty`~`mmMaxQty`）；报价含**反向动量偏置**（`mmSkewPct`，上轮涨 → 挂单整体下移逢高派发、跌 → 上移逢低承接，封板次轮加倍）、**波动自适应价差**（|上轮涨跌| 越大价差越宽，封顶 2 倍）、**分级回归锚干预**（连续封板 ≥2 轮挂大单对冲，量随连续轮数放大）。
- **推进入口**：管理端「推进一轮」按钮为全自动（无需填参，做市商与引擎参数取比赛 `stockConfig` 或默认值）；「自定义参数推进」弹窗可临时覆盖。

## 快照与回退（apps.snapshots）

全库快照 + 强制暂停 + 整体回退 + 全客户端版本同步。要点：

- **注册表**（`apps/snapshots/registry.py`）自动纳入 `apps.*` 下全部业务模型并按外键依赖做拓扑排序，
  新增业务模型**无需改动快照代码**；快照系统自身的 4 张表不参与快照/回退。
- **归档**：`backend/snapshots/snap-XXXXXX/`（`manifest.json` + `tables/*.jsonl.gz` + 可选 `files/`），
  逐表 sha256，回退后重算比对，不一致即整体回滚。
- **强制暂停**：`SnapshotGateMiddleware` 在 `PAUSED` 拒绝写请求（423）、`RESTORING` 拒绝全部请求；
  放行的写请求计入「在途写请求」计数器，暂停时先翻转状态再等计数器归零（静止点）。
- **同步**：回退成功 `SystemGate.data_version` +1，广播 `system:restored` / `system:resumed`，
  前端清空 IndexedDB 缓存并整体重载。
- **命令行**：`manage.py snapshot_auto`（定时快照 + 保留清理）、`manage.py snapshot_restore`（应急回退）。

完整设计、接口契约与运维手册见 **[docs/SNAPSHOT_SYSTEM.md](../docs/SNAPSHOT_SYSTEM.md)**。

## 数据库迁移

```bash
python manage.py makemigrations        # 全应用扫描生成
python manage.py makemigrations users  # 指定 app
python manage.py migrate               # 应用 + seed 默认超管
python manage.py migrate --fake-initial  # 首次从生产库导入表结构时用
```

循环依赖（`companies ↔ regions` 外键，`parts/products` 自引用 tech_node）已自动拆为 `0001_initial.py` + `0002_*.py`，无需人工干预。

## Django Shell 常用片段

```python
# 列出所有比赛及其公司数
from apps.competitions.models import Competition
[(c.name, c.companies.count()) for c in Competition.objects.all()]

# 赋权 + 触发权限缓存刷新广播
from apps.users.models import User
u = User.objects.get(username="staff01")
u.permissions = '{"data:part:view":1,"data:material:edit":1}'
u.permission_version = (u.permission_version or 0) + 1
u.save()
from apps.realtime.emit import emit_permissions_changed
emit_permissions_changed(u.id, u.permission_version)
```

## Django 管理后台（Admin）

项目已完整启用 Django 自带管理后台，可直接在网页上查看 / 增删改查全部业务数据，便于运维临时排查与修数。

### 访问与账号

- 访问地址：`http://<host>:8000/admin/`（与 API、Socket.IO 同源同端口）
  - 前端「系统设置 → 后端管理」区块提供两个超管专属按钮（均需 `isSuperAdmin` 才可见）：红色「后端管理界面」一键新标签页打开后端 `http://127.0.0.1:8000/admin/`；黄色「日志查看器」打开独立日志查看器 `http://127.0.0.1:8120/`（端口取 `LOG_VIEWER_PORT`）。
  - 注意：Vite 开发代理**不再转发** `/admin` 与 `/static`（已由该跳转按钮直连后端取代）；开发期需进后台请直连后端端口或用按钮，**不要**走 `http://localhost:5173/admin/`。
- 超级管理员账号（首次 `migrate` 自动从 `.env` 的 `SEED_ADMIN_*` 创建；业务超管与后台超管共用同一凭据，**已存在则跳过、不覆盖**）：

  | 用户名 | 密码 | 说明 |
  | --- | --- | --- |
  | `admin` | **一键部署**：脚本自动生成随机强密码，部署完成时直接显示在终端；**手动部署**：默认 `admin23`（`SEED_ADMIN_PASSWORD` 在 `.env` 中默认被注释，由 `bootstrap.py` 兜底；取消注释可自定义） | 生产务必改用强密码；**建议首次登录后立即改密** |

- 登录后可见 40 个业务模型（公司 / 比赛 / 合同 / 股票 / 原料 / 零件 / 产品 / 地图节点 / 基础设施 / 燃料 / 车辆 / 仓库 / 生产线 / 行业类型 / 区域 / 消费需求 / 消息 / 公告 / 控件包 / 技术树 / 审计等）+ Django 内置的 用户 / 组。

### ⚠️ 重要警示：后台仅用于临时排查 / 修数

> Django 后台直接写 SQLite，**会绕过所有后端业务校验**：
> - 合同执行引擎（DRAFT 直接改 EXECUTED 会导致账实不符、字段效果不触发）
> - 股票 K 线计算、行情推进轮次
> - 权限派生（前端简化界面自动生成的 permissions 与四个 Scopes）
> - 公司字段乐观锁级联重算、删除影响预览（两步确认）等
>
> 因此，**常规管理请一律走前端 Vue 界面**；后台只适合运维临时修数、补救脏数据，改完务必回到前端核对数据一致性。

### 创建 / 重置超级管理员

```bash
cd backend
# 交互式创建（按提示输入用户名 / 邮箱 / 密码）
.\.venv\Scripts\python.exe manage.py createsuperuser

# 无交互式（CI / 脚本）
set DJANGO_SUPERUSER_USERNAME=admin
set DJANGO_SUPERUSER_EMAIL=admin@example.com
set DJANGO_SUPERUSER_PASSWORD=YourNewPass!
.\.venv\Scripts\python.exe manage.py createsuperuser --no-input
```

> 注意：超级管理员只存在于当前 `db.sqlite3`。**重新 `migrate`（换库 / 换机器 / 清空数据库）后需再次执行 `createsuperuser`**。

### 后台能正常工作的两个关键改动（技术说明）

1. **`backend/urls.py`** 挂载了 `path("admin/", admin.site.urls)`，并额外用 `re_path` 无条件托管 `STATIC_ROOT`，使 `DEBUG=False` 时后台样式与脚本也能加载（`django.conf.urls.static.static` 仅在 `DEBUG=True` 时挂载）。
2. **`apps/common/middleware.py`** 的 `SecurityHeadersMiddleware` 对 `/admin` 路径跳过 CSP 等安全头——否则全局 `default-src 'none'; form-action 'none'` 会直接拦截登录与所有表单提交。该豁免仅作用于 `/admin`，`/api` 等接口的安全头保持不变。
3. **`apps/auth/bootstrap.py`** 的 `seed_default_admin` 在 `post_migrate` 信号中自动建超管：写入业务 `users` 表（`role=SUPER_ADMIN`，前端 JWT 登录用）+ 后台 `auth_user` 表（`is_staff/is_superuser=True`，`/admin` 登录用），凭据均来自 `.env` 的 `SEED_ADMIN_*`，幂等（已存在则跳过）。
