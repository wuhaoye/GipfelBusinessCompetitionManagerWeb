# Gipfel Business Competition Manager Web

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (Vue 3 + TS + Element Plus + Pinia + Vite)         │
│  · HTTP REST  →  /api/*  →  Django REST Framework           │
│  · WebSocket  →  /socket.io/*  →  python-socketio (ASGI)    │
│  · 静态资源   →  /assets/*  ←  nginx 或 Vite dev proxy      │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Django 5 后端                                              │
│  · daphne ASGI server（HTTP + WebSocket 同源同端口 :8000）  │
│  · DRF：40 张业务表 / 27 个 app / 统一 CRUD 基类            │
│  · JWT + RBAC：42 个权限键（20 个域）、5 级动作等级         │
│  · 实时广播：Socket.IO Rooms（comp-{id} + user-{id}）       │
│  · 合同引擎 / 股票引擎 / 产业计算图                         │
│  · 快照与回退：全库快照 + 强制暂停(423) + 整体回退 + 版本同步│
│  · SQLite（当前唯一实现，已开 WAL + busy_timeout=20s；PostgreSQL 属 C2 阶段 2）│
└─────────────────────────────────────────────────────────────┘
```

- **前端端口**：开发 `:5173`（Vite，自动代理 `/api` `/socket.io` `/uploads` 到 `:8000`），生产由 nginx 托管 `frontend-dist/`（或开发态 Django `STATIC_ROOT` 兜底）
- **后端端口**：Daphne 默认 `:8000`（开发态与回环直连）；生产 C1-a 整改后由 nginx 分流 —— `/api/`、`/admin/` → **gunicorn WSGI `127.0.0.1:8002`（多 worker）**，`/socket.io/` → daphne `127.0.0.1:8000`
- **上传目录**：`backend/uploads/`（环境变量 `UPLOAD_DIR`）
- **数据库**：`backend/db.sqlite3`（**当前硬编码 sqlite3**；调优开关见 `backend/.env.example` 的 C2 阶段 1 段）。
  ⚠️ **没有** `DATABASE_URL` 支持：切 PostgreSQL 属于《架构性运维约束整改简报.md》的 **C2 阶段 2**，需要「加 `psycopg` 依赖 + 改 `settings.DATABASES` + 建库迁移数据」三件事，不是改一个环境变量就能生效（改造前的 README 此处声明与代码不符，已更正）
- **日志**：`backend/logs/`（环境变量 `LOG_DIR`）

---

## 2. 三行启动（开发者本地 · Windows）

```bat
cd GipfelBusinessCompetitionManagerWeb
REM 1. 首次初始化（虚拟环境、pip 依赖、npm 依赖、迁移、建默认超管）
scripts\bootstrap-dev.bat

REM 2. 一键开发启动：同时拉起 Django (:8000) + Vite (:5173) + 日志查看器 (:8120)
REM    前置校验通过后切到独立的 "Gipfel Dev" 监管窗口
scripts\start-dev.bat

REM 3. 停止：在 "Gipfel Dev" 窗口按一次 Ctrl+C（三个服务一起优雅退出）
REM    窗口被强关（X / 任务管理器）导致服务残留时：scripts\stop-dev.bat
```

> **端口来源**：Django 的 `127.0.0.1:8000` 由 `scripts/dev.py`（`start-dev.bat` 拉起的监管进程）内部固定，**不读** `.env` 的 `PORT`（`PORT` 是 `manage.py rundaphne` 与 `/api/version` 下发的真源）；日志查看器端口才读 `.env` 的 `LOG_VIEWER_PORT`（默认 8120）。若改了 `PORT`，请改用 `manage.py rundaphne` 启动，或同步修改 `scripts/dev.py` 里的 `BACKEND_HOST` / `BACKEND_PORT`。

浏览器访问 `http://localhost:5173`，登录默认账号：

| 用户名 | 初始密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin23` | SUPER_ADMIN（首次登录强制改密） |

> 若想分别启动前后端，见 [backend/README.md](backend/README.md) 与 [frontend/README.md](frontend/README.md)。

---

## 3. 目录结构

```
GipfelBusinessCompetitionManagerWeb/
├── backend/                         Django 5 后端
│   ├── apps/                        27 个业务 + 基础设施 app
│   │   ├── auth/                    JWT 登录/改密/顶号/默认超管种子
│   │   ├── users/                   用户与权限版本
│   │   ├── competitions/            比赛 / 财年
│   │   ├── common/                  CRUD 基类、审计、分页、中间件、signals
│   │   ├── realtime/                Socket.IO 网关、emit 服务、seq/重放
│   │   ├── materials/parts/products 生产链
│   │   ├── maps/tech_tree/          地图与科技树
│   │   ├── companies/company_fields 公司字段（乐观锁 + 级联重算）
│   │   ├── industry_types/          产业类型 + 计算图字段
│   │   ├── contracts/               合同 + 引擎 engine.py
│   │   ├── stock/                   股票引擎（集合竞价撮合 + AI 做市商 + 防连板）
│   │   ├── messages/                消息中心
│   │   ├── announcements/           公告管理
│   │   ├── widget_packages/         自定义控件包管理
│   │   ├── snapshots/               快照与回退（全库快照 / 强制暂停 / 整体回退 / 版本同步）
│   │   └── files/                   上传
│   ├── snapshots/                   快照归档目录（SNAPSHOT_DIR，gitignore，升级不清除）
│   ├── logviewer/                   独立日志查看器站点（默认 :8120，见下）
│   ├── manage.py
│   ├── requirements.txt
│   ├── .env.example
│   └── README.md                    ← 后端专属说明
│
├── frontend/                        Vue 3 前端
│   ├── src/
│   │   ├── router/                  视图路由（按角色 lazy-load）
│   │   ├── stores/                  Pinia：auth / competition / config
│   │   ├── views/                   全部业务视图：数据管理、赛事、公司、股票等
│   │   ├── components/DataManager   通用 CRUD 组件（约 10 个简单资源直接复用）
│   │   ├── realtime/                socket 客户端 + resourceChanged 响应式广播
│   │   ├── contracts/graph-model    合同条件/效果表达式引擎
│   │   └── utils/permissions        can() / canAny() / 角色动作等级表
│   ├── vite.config.ts               dev 代理到 8000
│   ├── package.json
│   └── README.md                    ← 前端专属说明
│
├── scripts/
│   ├── bootstrap-dev.bat            Windows 开发环境首次初始化
│   ├── start-dev.bat                Windows 开发启动（校验前置条件后交给 dev.py）
│   ├── dev.py                       Windows 开发监管进程（三服务 + Ctrl+C 一次停全部）
│   ├── stop-dev.bat                 残留服务强停兜底（监管窗口被强关时用）
│   ├── deploy-linux.sh              Linux 一键部署（daphne + systemd + nginx + 前端静态）
│   └── update-from-github.sh        Linux 增量升级（拉取最新 + 备份 + 迁移 + 构建 + 重启，保留数据）
│
├── deploy/
│   ├── gipfel.service               systemd unit 模板（daphne ASGI，承载 /socket.io/，回环直连兼容）
│   ├── gipfel-wsgi.service          systemd unit 模板（C1-a：gunicorn WSGI，承载 /api/、/admin/）
│   ├── logviewer.service            systemd unit 模板（日志查看器）
│   └── nginx-gipfel.conf            nginx 虚拟主机模板（限流 + $request_time 日志格式）
│
├── widget-package-examples/         自定义控件包示例（progress-bar、simple-card）
├── architecture_diagram/            架构图资源
├── tests/                           验证与探针脚本（fix_verify 回归套件 / ops_check 独立验收 / snapshot_tools）
├── logs/                            开发期日志（gitignore；生产由 backend/logs/ 托管）
├── uploads/                         开发期上传文件（生产由 backend/uploads/ 托管）
├── docs/                            **全部文档的唯一入口**
│   ├── README.md                       ← 文档索引：按主题分组导航下面全部文档
│   ├── OPS.md · MIGRATION.md · SNAPSHOT_SYSTEM.md · CUSTOM_WIDGET_GUIDE.md · Vue-Django迁移设计.md
│   ├── 架构性运维约束整改简报.md · 运维约束整改设计说明.md · 架构性运维约束整改验收报告.md
│   ├── 真机验证报告-Debian13.md · WSL与生产环境验证操作手册.md · WSL与生产环境验证结果记录.md
│   ├── BUILD_COMPETITION_API_REFERENCE.md · BUILD_COMPETITION_BY_CODE.md · CONTRACT_TYPE_BY_CODE.md
│   ├── 比赛Excel建包教程.md · 比赛Excel建包规范.md · 合同可视化新建操作指南.md · 汽车产业链测试赛准备.md
│   ├── 赛务运维分析.md · 比赛系统缺陷与改进方案.md · 硬阻断B_推进财年改造方案.md
│   ├── audit/                          代码缺陷审计归档（总报告 + 366 条索引 + 21 个审计单元 + 规则转录）
│   └── branch-diff/                    master 与 bugfix-merged 分支差异报告与生成脚本
├── VERSION.json                     全局版本号（前端 prebuild 读取）
└── README.md                        ← 你现在正在看的
```

> **文档去哪了**：仓库根目录**只保留本 README**。其余文档（含整改、审计、赛务分析、验证记录）全部在 [`docs/`](docs/README.md) 下，入口是 [docs/README.md](docs/README.md)。

---

## 4. 环境要求

| 组件 | 最低版本 | 建议版本 |
| --- | --- | --- |
| Python | 3.10 | **3.12**（3.13/3.14 可用，需 Pillow≥12） |
| Node.js | 18 | **20 LTS**（22 也已验证） |
| npm | 9 | 10 |
| 操作系统 | — | Windows 10/11、Linux（Ubuntu 22.04+ / Debian 12） |
| 浏览器 | — | Chrome 120+、Edge 120+、Firefox 120+ |

依赖亮点（详见 `requirements.txt` 与 `package.json`）：

- 后端（`backend/requirements.txt`）：`Django 5.0.14`、`djangorestframework 3.15`、`djangorestframework-simplejwt 5.3`（JWT，内含 PyJWT）、`daphne 4.1`（ASGI）、`python-socketio 5.11`、`bcrypt`、`django-cors-headers`、`Pillow 12`、`python-dotenv`（合同引擎的表达式求值为自研受限求值器 `apps/contracts/engine.py: safe_evaluate`，不依赖 eval/simpleeval 等第三方执行库）
- 前端（`frontend/package.json`）：`Vue 3.4`、`Vue Router 4.4`、`Pinia 4.0`、`Element Plus 2.7`、`Vite 5.3`、`axios 1.20`、`echarts 6.1`、`socket.io-client 4.7`、`konva 10.3` + `vue-konva 3.4`（地图画布）、`pinyin-pro 3.29`、`typescript 5.4`、`vue-tsc 2.0`
- 状态持久化由各 store 手写 `localStorage` 完成，**未使用** `pinia-plugin-persistedstate`

---

## 5. 生产部署

### 5.1 Linux（推荐 · Ubuntu 22.04 / Debian 12）

```bash
# 在目标服务器上执行，须有 sudo 权限
cd GipfelBusinessCompetitionManagerWeb

# 有域名：
sudo bash scripts/deploy-linux.sh \
  --domain comp.example.com \
  --install-dir /opt/gipfel \
  --with-nginx

# 纯 IP（无域名）：省略 --domain；受限网络探测不到公网 IP 时显式传 --public-ip
sudo bash scripts/deploy-linux.sh \
  --install-dir /opt/gipfel \
  --with-nginx \
  --public-ip 43.142.77.225

# 完成后：
#   systemctl status gipfel      # 后端 daphne
#   systemctl status nginx       # 反向代理 + 前端静态
#   http://<IP>/                 # 纯 IP 访问（或 https://comp.example.com）
```

> **日常升级**用 [update-from-github.sh](scripts/update-from-github.sh)：自动「拉取最新 + 备份 + 迁移 + 前端构建 + 重启」，保留数据；纯 IP 部署同样支持（自动自愈 `LOG_VIEWER_PUBLIC_URL` 与 `DJANGO_ALLOWED_HOSTS`）。详见 [deploy/README.md「更新部署」](deploy/README.md) 与 [OPS.md 第 11 节](docs/OPS.md)。

脚本自动完成：
1. 系统依赖安装（python3-venv、python3-dev、nodejs、npm、nginx、openssl）
2. 虚拟环境 + `pip install -r requirements.txt`
3. `python manage.py migrate`（自动幂等建默认 admin）
4. `npm ci && npm run build` 并把产物放到 `$INSTALL_DIR/frontend-dist`
5. 写入 `deploy/gipfel.service` → `/etc/systemd/system/gipfel.service` 并 `enable --now`
6. 写入 `deploy/nginx-gipfel.conf` → `/etc/nginx/sites-available/` 并 `ln -s` `sites-enabled`，`nginx -t && systemctl reload`
7. （可选）`certbot --nginx -d comp.example.com` 一键 HTTPS

### 5.2 Windows（仅开发；生产部署请使用 Linux）

Windows 不提供独立生产部署脚本，仅提供开发启动器：

```bat
scripts\start-dev.bat
```

并行拉起 Django(:8000) + Vite(:5173) + 日志查看器(:8120，端口取 `backend/.env` 的 `LOG_VIEWER_PORT`)。
生产环境部署请以 5.1 的 `deploy-linux.sh` 为准。

详细手动部署步骤、systemd/nginx 模板、回滚流程见 [deploy/README.md](deploy/README.md)。

---

## 6. 安全与合规

- **JWT**：HS256，`JWT_SECRET`（必填，未配置进程 fail-fast 拒绝启动），默认 24h，`tokenVersion` 顶号立即失效（改密同样递增吊销所有旧 token）；**改密时后端直接签发新 token 返回**，前端拿到后**替换旧 token 即可**（[ChangePasswordView](backend/apps/auth/views.py) 改密、递增 `token_version` 吊销旧 token、签发新 token 三步在同一 ORM 实例上原子完成，避免二次 `/login` 触发的 SQLite 写后读竞态）；Django 自身 `SECRET_KEY` 支持经 `DJANGO_SECRET_KEY` 独立配置（未配置回退 `JWT_SECRET`，生产建议分离）
- **RBAC**：39 个权限键、19 个权限域；5 级动作等级蕴含（`view(10) < edit(20) < manage(30) < execute(40) < audit(50)`），合同域自定义为 `view(10) < audit(20) < execute(30) < manage(40)`；`can(action, resource)` 前后端一致
- **比赛隔离**：读查询自动按 `competition_id` 域过滤（`apply_competition_scope`）；写操作由 `create_competition_id` 强制归属（非超管忽略请求体的 competitionId，杜绝跨比赛写入）；`CompetitionScopePermission` 挂载在 DRF 全局默认权限做兜底（非超管写操作必须有比赛上下文）
- **客户端 IP 信任链**：`client_ip()` 仅当请求来自可信代理（默认回环，可经 `TRUSTED_PROXIES` 扩展）才信任 `X-Real-IP`，绕过 nginx 直连后端无法伪造 IP 使登录限速失效
- **CORS**：未配置 `CORS_ORIGIN` 时仅本地/私网反射并带凭据；公网必须显式白名单
- **安全头**：自定义中间件写入 CSP、X-Frame-Options=DENY、X-Content-Type-Options=nosniff、Strict-Transport-Security、Referrer-Policy
- **登录限流**：`LoginRateLimitMiddleware` **只拦截** `POST /api/auth/login`——同一 IP + 用户名在 5 分钟窗口内累计失败 10 次即锁定 15 分钟并返回 429。阈值是 `apps/common/middleware.py` 里的常量（`_FAIL_WINDOW` / `_FAIL_THRESHOLD` / `_LOCK_DURATION`），非环境变量；锁定状态存进程内存，**重启后端即清空**。项目目前**没有**全局 HTTP 限流中间件
- **乐观锁**：公司字段写操作携带 `version`，冲突 409 提示前端重试
- **删公司两步确认**：`DELETE /api/companies/:id` 先返回「删除影响预览」，前端二次确认带 `confirmName` 才执行
- **快照与强制暂停**：`apps/snapshots` 提供全库快照与整体回退；「强制暂停」期间中间件拒绝全部业务写入（HTTP **423**）并向所有在线客户端广播全屏遮罩，回退完成后 `data_version` +1，各客户端比对版本号后清空本地缓存并整体重载 —— 详见 [docs/SNAPSHOT_SYSTEM.md](docs/SNAPSHOT_SYSTEM.md)
- **审计日志**：`apps/common/signals.py` 对**已在 `MODEL_TO_RESOURCE` 注册**的模型统一挂 `post_save`/`post_delete` → `AuditLog` 表落库，含 operator、IP、changes JSON 快照；映射值为 `None` 的子表（仅列名映射、不广播）不落审计

---

## 7. 默认账号与权限

首次 `migrate` 完成后自动写入默认超管（凭据由 `.env` 的 `SEED_ADMIN_*` 驱动，可覆盖；库里已有该用户名则跳过）：

| 用户名 | 初始密码 | 角色 | 权限 |
| --- | --- | --- | --- |
| `admin` | `admin23` | SUPER_ADMIN | 全部 39 项 + 所有比赛域 |

> 同一套凭据也会在 `migrate` 时一并创建 Django 后台（`/admin`）超级管理员（见下方「Django 管理后台」一节）。

**强制改密**：首次登录成功后返回的 JWT 仍能通过鉴权，但调用受 `must_change_password` 守卫的接口（如 `/auth/me`、全部业务接口）会返回 **401 `initial_password_must_be_changed`**，前端立即跳转到改密页。改密成功后 `must_change_password` 置为 False，同时**递增 `token_version` 吊销所有旧 token**（安全设计，防旧凭据残留）；**后端直接签发新 token 与最新 user 资料返回**（[ChangePasswordView](backend/apps/auth/views.py)），前端替换内存与 localStorage 里的旧 token、重启心跳即可完成本设备会话无感续接，其他设备被正确踢下线。脚本/SDK 调用方需从 `change-password` 响应里取 `token` 字段续接。

### 新建比赛管理员

```powershell
# 命令行创建（或前端「账号管理」里手动建）
.\.venv\Scripts\python.exe manage.py create_competition_admin `
  --username comp01admin --password "S3cret!" --competition-id 1
```

### Django 管理后台（/admin）

除前端 Vue 界面外，后端还完整启用了 Django 自带管理后台，可直接在网页上查看 / 增删改查全部业务数据。仅建议运维临时排查与修数，详细说明（账号、警示、命令、技术改动）见 [backend/README.md](backend/README.md) 的「Django 管理后台（Admin）」一节：

- 访问：`http://<host>:8000/admin/`
- 账号：`admin` / 密码默认 `admin23`（来自 `apps/auth/bootstrap.py` 的兜底默认值；`.env` 中 `SEED_ADMIN_PASSWORD` 默认被注释，取消注释并赋值可自定义）；首次 `migrate` 自动创建，已存在则跳过（建议立即改密）
- **⚠️ 警示**：后台直接写库会绕过业务校验（合同引擎 / 股票计算 / 权限派生等），可能导致账实不符；常规管理请走前端界面。

### 日志查看器（:8120）

独立 Django 站点（`backend/logviewer/`，有自己的 `manage.py`），**共享主后端的 `db.sqlite3`**，用于在线查看 `backend/logs/` 的运行日志。

- 访问：`http://127.0.0.1:8120/`（端口取 `backend/.env` 的 `LOG_VIEWER_PORT`，默认 8120）
- 登录：**与 `/admin` 共用同一套 `auth_user` 超管凭据**（校验 `is_superuser`），默认 `admin` / `admin23`。它与前端登录用的业务 `users` 表是**两套账号体系**，前端改密**不会**同步到这里
- **cookie 必须隔离**：两者同在 `localhost`，若 cookie 同名，主后端那份 `HttpOnly` 的会盖掉前端要读的那份，登录报 403 `CSRF token ... has incorrect length`：

  | 站点 | CSRF cookie | Session cookie | HttpOnly |
  | --- | --- | --- | --- |
  | 主后端 `:8000` | `csrftoken` | `sessionid` | 是 |
  | 日志查看器 `:8120` | `lv_csrftoken` | `lv_sessionid` | 否（前端 JS 需读取后回传 `X-CSRFToken`） |

- 排错：升级后若登录仍 403，多半是浏览器缓存了旧版 `app.js`（旧版读 `csrftoken`），硬刷新 `Ctrl+Shift+R` 或开无痕窗口

---

## 8. 常用命令速查

### 后端

```powershell
cd GipfelBusinessCompetitionManagerWeb\backend
.\.venv\Scripts\Activate.ps1            # 激活虚拟环境
python manage.py check                  # Django 系统检查
python manage.py makemigrations         # 生成模型迁移
python manage.py migrate                # 应用迁移（+ 自动 seed 默认 admin）
python manage.py createsuperuser        # 另一种建超管方式
python manage.py shell                  # ORM shell
python manage.py snapshot_auto          # 自动快照（按 SnapshotPolicy，可配 cron）
python manage.py snapshot_restore --list    # 列出快照 / 应急命令行回退（--id N --yes）
python manage.py test apps.snapshots    # 快照与回退系统回归测试
python manage.py runserver 127.0.0.1:8000 # 开发用；daphne 已在 INSTALLED_APPS 接管 runserver，实际跑的是 ASGI
python manage.py rundaphne                 # 生产推荐：默认绑定 127.0.0.1（端口取 .env 的 PORT），由 nginx 反代对外
# 仅局域网/容器内联调临时需要时再 --bind 0.0.0.0：daphne -b 127.0.0.1 -p 8000 backend.asgi:application
```

### 前端

```powershell
cd GipfelBusinessCompetitionManagerWeb\frontend
npm install      # 依赖
npm run dev      # Vite 开发 :5173（代理 /api /socket.io /uploads 到 :8000）
npm run build    # 生产构建 → dist/
npm run preview  # 预览构建产物
npm run typecheck # vue-tsc 类型检查（CI 必跑）
```

### 端到端冒烟（快速验证迁移后功能）

```powershell
# 1. 起服务
cd backend; .\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000

# 2. 另开终端：health → login → 改密 → me → competitions CRUD → maps/full → stocks/industry-types
#    全部应为 200，参考 design doc 的 9.1 API 契约
```

---

## 9. 开发与贡献规范

- 任何新增业务模型必须在 `apps/realtime/emit.py` 的 `MODEL_TO_RESOURCE` 注册（值为 `None` 表示「列名映射但不广播」，如子表）
- 批量 ORM 操作（如股票推进轮次）用 `with suppress_signals():` 包裹，随后发 `bulk` 广播
- 前端所有 REST API 走 `src/api/index.ts` 的统一 axios 实例（带 JWT 拦截、错误 toast、Unauthorized 401 跳登录）
- 前端实时广播使用 `useResourceChanged()` composable，**不要**直接写 `socket.on`，避免重复订阅、重复调用
- 权限检查：后端装饰器 `@require_permissions("data:part:edit")` + 前端按钮 `v-if="can('edit', 'data:part')"` 成对出现
- CI：`npm run typecheck` 与 `python manage.py check` 必须全绿，无 migrations 未应用

---
