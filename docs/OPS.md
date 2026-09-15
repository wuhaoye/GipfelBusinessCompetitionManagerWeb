# Gipfel 商赛系统 · 运维文档（OPS）

面向运维人员的日常操作手册。架构、目录结构、API 契约见 [`README.md`](README.md)；部署步骤见 [`deploy/README.md`](deploy/README.md)。

---

## 1. 服务与端口

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| 前端（Vite / 生产 nginx 静态） | `:5173`（开发）/ 80·443（生产） | 浏览器访问入口 |
| 后端（Django 5 + daphne ASGI） | `:8000` | HTTP REST + Socket.IO WebSocket 同源同端口；`/admin` 管理后台仅前端按钮携带一次性令牌可进，直连 302 回前端 |
| 日志查看器（独立 Django 站点） | `:8121`（daphne 内部，仅绑 127.0.0.1，service 模板硬编码）/ `.env` 的 `LOG_VIEWER_PORT` 决定 nginx 公网监听端口（默认 `:8120`） | 在线查看 `backend/logs/`，**共享主后端 `db.sqlite3`**；公网整站代理到 `127.0.0.1:8121`，且**仅前端按钮点击（携带一次性令牌）可进入**，直接输入网址被 403 拒绝。有域名经 nginx 子域 `log.<DOMAIN>`（端口 80）；无域名（纯 IP）经 nginx `LOG_VIEWER_PORT` 端口（`server_name _`，默认 8120）访问 `http://<IP>:8120/`（详见 deploy/README.md「无域名纯 IP 部署」） |

> 后端 `:8000` 由 `start-dev.bat` 固定，不读 `.env` 的 `PORT`；`PORT` 仅被 `manage.py rundaphne` 与 `/api/version` 下发的跳转按钮使用。`.env` 的 `LOG_VIEWER_PORT`（默认 8120）**真正控制** nginx 公网监听端口（deploy 脚本渲染 vhost 时替换 `__LOG_VIEWER_PORT__` 占位符），并随 `/api/version` 下发给前端按钮拼 `log_viewer_url`。**daphne 实际绑定的内部端口是 127.0.0.1:8121**（`deploy/logviewer.service` 模板硬编码）——与 `LOG_VIEWER_PORT` **故意解耦**，避免 nginx 与 daphne 同机抢端口。改 `LOG_VIEWER_PORT` 改的是公网端口，8121 内部端口不变；防火墙 ufw 规则随新值自动清理/重建。

## 2. 环境要求

- **Python** 3.10+（需 Pillow≥12：12.0 起官方支持 Python 3.14）
- **Node.js** 18+（推荐 20 LTS，22 已验证）
- 操作系统：Linux（Ubuntu 22.04 / Debian 12，生产推荐）、Windows 10/11（仅开发）

## 3. 启动与停止

### 3.1 Linux（生产，systemd）

```bash
systemctl start gipfel        # 后端 daphne
systemctl stop gipfel         # 停止
systemctl restart gipfel      # 重启（改代码/配置后）
systemctl status gipfel       # 状态
systemctl status gipfel-logviewer   # 日志查看器（独立站点）
systemctl status nginx        # 反向代理 + 前端静态
journalctl -u gipfel -f       # 实时日志
```

### 3.2 Windows（开发）

```bat
scripts\bootstrap-dev.bat     :: 首次：虚拟环境 + pip + npm + migrate + 建默认超管（可加 --skip-frontend）
scripts\start-dev.bat         :: 校验前置条件后切到 "Gipfel Dev" 监管窗口，拉起 Django(:8000) + Vite(:5173) + 日志查看器(:8120)
scripts\stop-dev.bat          :: 兜底强停：监管窗口被强关（X / 任务管理器）导致服务残留时执行
```

停止：在 **Gipfel Dev** 监管窗口按一次 `Ctrl+C`，Django / Vite / 日志查看器会一起优雅退出，窗口随之关闭。

> **为什么不再用批处理的 `start /B` 直接拉服务**：`start` 会把子进程放进**新的进程组**，控制台 Ctrl+C 不会投递给它们（服务照常运行、8000/5173/8120 只增不减），而 cmd.exe 自己会停在「终止批处理操作吗(Y/N)?」——窗口看起来就是卡死，按 Y 之后服务仍在跑。监管逻辑因此移入 [scripts/dev.py](scripts/dev.py)：子进程依旧以 `CREATE_NEW_PROCESS_GROUP` 启动，退出时由监管进程对每个子进程组**定向**发送 `CTRL_BREAK_EVENT` 优雅停止（daphne 收到 SIGBREAK 会正常关闭 reactor），8s 内没退再 `taskkill /PID <pid> /T /F` 兜底；子进程 PID 写入 `%TEMP%\gipfel-dev.pids`，供 `scripts\stop-dev.bat` 兜底强杀。

> `start-dev.bat` 用的是 `manage.py runserver`，但 **daphne 已在 `INSTALLED_APPS` 首位并接管了 runserver 命令**，因此实际就是以 ASGI/daphne 运行，HTTP + WebSocket 同源同端口，Socket.IO 正常。

## 4. 默认账号

| 用户名 | 密码 | 角色 | 说明 |
| --- | --- | --- | --- |
| `admin` | `admin23` | SUPER_ADMIN | 首次登录强制改密 |

- **两套账号体系**：业务 `users` 表（前端 JWT 登录，自定义 User 用 bcrypt 校验 `password_hash`）；后台 `auth_user` 表（Django `/admin` 与日志查看器登录，用 pbkdf2_sha256）。
- 日志查看器与 `/admin` **共用 `auth_user` 凭据**（均校验 `is_superuser`）。前端改密**不会**同步到后台/日志查看器。
- 密码由 `.env` 的 `SEED_ADMIN_*` 驱动；首次 `migrate` 自动创建，已存在则跳过（不覆盖）。

## 5. 健康检查

```bash
curl -sS http://127.0.0.1:8000/api/health     # 期望 {"code":0,"message":"成功","data":{"status":"ok"}}
curl -sS http://127.0.0.1:8000/api/version    # 期望 data 内含 version / port / log_viewer_url 字段（日志查看器公网地址）
curl -sS http://127.0.0.1:8120/api/health     # 日志查看器健康检查
curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:8120/   # 直连应为 403（防直连网关）
curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/admin/   # 直连应为 302（防直连网关，Location 指向 /）
```

## 6. 常用运维命令

后端（在 `backend/` 下，激活虚拟环境 `.\.venv\Scripts\Activate.ps1` 或 `source .venv/bin/activate`）：

```bash
python manage.py check                  # Django 系统检查
python manage.py migrate                # 应用迁移（幂等，自动 seed 默认超管）
python manage.py createsuperuser        # 新建/重置后台超管（交互）
python manage.py shell                  # ORM shell
python manage.py rundaphne                 # 生产启动：默认绑定 127.0.0.1（端口取 .env 的 PORT），由 nginx 反代对外
# 仅局域网/容器内联调临时需要时才 --bind 0.0.0.0（切勿将 0.0.0.0 直接暴露于公网）
```

前端（在 `frontend/` 下）：

```bash
npm run build        # 生产构建 → dist/
npm run preview      # 预览构建产物
npm run typecheck    # 类型检查（CI 必跑）
```

## 7. 日志

- **后端运行日志**：`backend/logs/gipfel.log`（按天滚动，保留 14 天）；生产亦可用 `journalctl -u gipfel -f`。
- **日志查看器**：公网经 `https://log.<DOMAIN>/`（有域名）或 `http://<IP>:8120/`（无域名纯 IP 部署，见 deploy/README.md「无域名纯 IP 部署」）整站代理；开发环境 `http://127.0.0.1:8120/`。**仅能从「系统设置 → 日志查看器」按钮（携带一次性令牌）进入**；直接输入网址会被 403 拒绝。进入后仍需用 Django 后台超级管理员凭据登录。
- **cookie 隔离**：两者同在 `localhost`，cookie 名必须不同，否则主后端 `HttpOnly` 的会盖掉前端要读的那份：

  | 站点 | CSRF cookie | Session cookie | HttpOnly |
  | --- | --- | --- | --- |
  | 主后端 `:8000` | `csrftoken` | `sessionid` | 是 |
  | 日志查看器 `:8120` | `lv_csrftoken` | `lv_sessionid` | 否（前端 JS 需读取回传 `X-CSRFToken`） |

## 8. 备份与恢复

需备份的核心数据（SQLite 默认）：

- `backend/db.sqlite3`（数据库）
- `backend/uploads/`（上传文件）
- `backend/logs/`（日志，可选）

恢复：停止服务 → 用备份覆盖上述目录 → 重启。详细回滚流程见 [`deploy/README.md`](deploy/README.md) 的「更新部署 / 回滚」一节（`deploy-linux.sh --skip-install-deps` 会自动先备份到 `/opt/gipfel/_backup/`）。

### 服务器迁移

需要将服务迁移到另一台服务器？详见 **[服务器迁移指南](MIGRATION.md)**，包含：

- 一键迁移脚本 `scripts/migrate-server.sh`（推送/拉取两种模式）
- 快速数据同步脚本 `scripts/quick-sync.sh`
- 迁移验证脚本 `scripts/verify-migration.sh`
- 手动迁移步骤
- 常见问题解答

## 9. 故障排查（FAQ）

### Q1. Django 后台 `/admin` 或日志查看器登录失败，密码明明正确

- **根因（曾真实发生）**：`backend/backend/settings.py` 的 `PASSWORD_HASHERS` 若只保留 bcrypt，而 `auth_user` 账号密码是 `pbkdf2_sha256`，`identify_hasher()` 找不到 pbkdf2 算法会使 `check_password` 恒返回 `False`，表现为「密码正确却永远登录失败」。业务 `users` 表自行用 bcrypt 校验，不受影响（前端登录正常）。
- **处置**：确认 `PASSWORD_HASHERS` 含 `PBKDF2PasswordHasher` 等默认算法（当前已配置，bcrypt 排首位）。改完后用 `python manage.py shell` 验证：
  ```python
  from django.contrib.auth.models import User
  u = User.objects.get(username="admin")
  u.check_password("admin23")   # 应为 True
  ```

### Q2. 日志查看器登录报 403 `CSRF token ... has incorrect length`

- **根因**：主后端与日志查看器同在 `localhost`，CSRF/Session cookie 同名会冲突。已用 `lv_csrftoken` / `lv_sessionid` 独立前缀隔离。
- **处置（多为浏览器缓存）**：升级后若仍 403，通常是浏览器缓存了重启前的旧 `app.js`（旧版读 `csrftoken`）。**硬刷新 `Ctrl+Shift+R` 或开无痕窗口**即可。

### Q3. 双击 `bootstrap-dev.bat` 一闪而过（闪退）

- **根因（曾真实发生）**：`.bat` 块内 `echo` 文本若含圆括号，cmd 会当成命令分组，`)` 之后的内容被当作新命令而报 `or was unexpected at this time`；且该错误在**解析期**就中止脚本，跳过所有 `pause`，窗口一闪而没。
- **处置**：当前脚本已在顶部加 `cmd /k` 兜底包装——即便将来出现语法错误，窗口也会**保留并显示错误与命令提示符**，不再静默闪退。仍闪退请确认双击的是 `scripts\bootstrap-dev.bat`（而非 `start-dev.bat`），或在文件所在目录按住 Shift 右键「在此处打开命令窗口」后手动运行看输出。

### Q4. 默认密码到底是多少

`admin / admin23`（**不是 admin123**）。文档、`.env`、种子默认值、当前数据库已统一为 `admin23`。

### Q5. 改端口后前端跳转按钮还指向旧端口

前端「系统设置 → 后端管理」的红色「后端管理界面」按钮走同源相对路径 `/admin/`（不拼端口，随 nginx 域名自适应），点击时向后端 `POST /api/auth/backend-token` 取一次性令牌（仅 `SUPER_ADMIN`）拼入 `/admin/?token=...` 打开；后端 `BackendGateMiddleware` 校验，直连 `/admin/` 无令牌会被 302 重定向回前端 SPA。黄色「日志查看器」按钮地址由 `/api/version` 下发的 `log_viewer_url` 拼接（默认由请求 Host 派生 `https://log.<域名>/`，可用 `.env` 的 `LOG_VIEWER_PUBLIC_URL` 显式覆盖）。改 `.env` 的 `LOG_VIEWER_PORT` 或域名后**重启后端**即可，无需改前端代码。

### Q6. Socket.IO / 实时数据不刷新

- `start-dev.bat` 的 `runserver` 已被 daphne 接管为 ASGI，WebSocket 正常；若异常，确认 `daphne` 在 `INSTALLED_APPS` 首位。
- 前端经 Vite 代理 `/socket.io` → `http://127.0.0.1:8000`（**显式 IPv4**，避免 Windows 上 `localhost` 解析到 IPv6 `::1` 导致 `ECONNRESET`）。

### Q7. 登录接口返回 429

`LoginRateLimitMiddleware` **只拦截 `POST /api/auth/login`**：同一 IP + 用户名在 5 分钟窗口内累计失败 10 次即锁定 15 分钟并返回 429。锁定状态存**进程内存**，重启后端即清空。阈值由 `apps/common/middleware.py` 常量 `_FAIL_WINDOW` / `_FAIL_THRESHOLD` / `_LOCK_DURATION` 控制，非环境变量。

### Q7b. 纯 IP 部署登录/健康检查返回 400（「请求参数错误」）

- **现象**：浏览器访问 `http://<IP>/api/...` 得到 Django 原生 400 Bad Request HTML 页面，前端统一提示「请求参数错误」；`curl http://127.0.0.1:8000/api/health`（回环）却正常。
- **根因（曾真实发生）**：`backend/.env` 的 `DJANGO_ALLOWED_HOSTS` 不含公网 IP——Django 的 Host 头白名单没放行 `http://<IP>` 请求，对一切非回环 Host 一律返回 400。
- **自动修复**：`deploy-linux.sh` / `update-from-github.sh` 均会在部署/升级时自动把域名或公网 IP 幂等追加进 `DJANGO_ALLOWED_HOSTS`（优先级 `--domain > --public-ip > 自动探测`；受限网络探测不到时显式传 `--public-ip <IP>`）。手动补救：
  ```bash
  # /opt/gipfel/backend/.env 追加公网 IP 后重启
  echo 'DJANGO_ALLOWED_HOSTS=<公网IP>,localhost,127.0.0.1' | sudo tee -a /opt/gipfel/backend/.env
  sudo systemctl restart gipfel
  ```
- **区分**：若是 401/429/403 走对应 Q7/安全章节；**400 + HTML** 几乎必然是 ALLOWED_HOSTS。

### Q7c. 日志查看器（:8120）返回 400 / Host header 报错

- **现象**：浏览器打开「系统设置 → 日志查看器」按钮跳转后的页面，得到 Django 原生 400 Bad Request HTML；或日志查看器自身 `Invalid HTTP_HOST header: '<公网IP:8120>': You may need to add '<domain>' to ALLOWED_HOSTS` 错误。主后端 `/api/...` 同时正常返回。
- **根因**：日志查看器是**独立 Django 服务**（`backend/logviewer/`，绑 127.0.0.1:8121，nginx 8120 反代），拥有**自己**的 `ALLOWED_HOSTS`，与主后端的 `DJANGO_ALLOWED_HOSTS` **不共享**。`LOGVIEWER_ALLOWED_HOSTS` 默认只含回环 + `LOG_VIEWER_PUBLIC_URL` 推导出的 host；若公网 IP 探测失败或未写该项，公网访问一律 400。
- **自动修复**：`deploy-linux.sh` / `update-from-github.sh` 在纯 IP 部署场景会自动探测公网 IP 写入 `LOG_VIEWER_PUBLIC_URL`（同时由其 hostname 推导日志查看器自身的 ALLOWED_HOSTS）；`update-from-github.sh` 自愈已具备「内网 IP / 缺失行 / 与当前公网 IP 不一致」三类纠正。
- **三来源兜底（任一生效即可）**：当前版本日志查看器 ALLOWED_HOSTS 自动从下列来源取并集去重——
  1. 回环地址（127.0.0.1 / localhost / ::1）
  2. `LOG_VIEWER_PUBLIC_URL` 推导的 hostname（去端口、IPv6 去方括号）
  3. `DJANGO_ALLOWED_HOSTS`（与主后端共用 .env，deploy 总是把公网 IP/域名写进去；`host:port` 与 `[IPv6]:port` 会被剥端口/方括号）
  4. `LOGVIEWER_ALLOWED_HOSTS`（手动追加）
  即便 `LOG_VIEWER_PUBLIC_URL` 因受限网络探测失败被 deploy 自愈删掉，只要 `DJANGO_ALLOWED_HOSTS` 已被写入公网 IP，日志查看器仍能正确放行。
- **手动补救**（极端情况：三来源都缺失）：
  ```bash
  # /opt/gipfel/backend/.env 写入/纠正公网地址（注意 hostname 部分必须与访问 Host 头一致）
  sudo sed -i -E "s|^LOG_VIEWER_PUBLIC_URL=.*|LOG_VIEWER_PUBLIC_URL=http://<公网IP>:8120/|" /opt/gipfel/backend/.env
  # 或追加兜底白名单（不含端口）
  echo 'LOGVIEWER_ALLOWED_HOSTS=<公网IP或域名>' | sudo tee -a /opt/gipfel/backend/.env
  sudo systemctl restart gipfel-logviewer
  ```
- **为什么不能只改 `DJANGO_ALLOWED_HOSTS`**：日志查看器进程读的是它自己 `settings.py` 的 `ALLOWED_HOSTS`，主后端的同名变量对它**无效**。改完主后端 .env 一定要 `restart gipfel-logviewer`，不是 `restart gipfel`。
- **进程校验**：`curl -H "Host: <公网IP>" http://127.0.0.1:8121/` 不再返回 400 即视为生效。

### Q8. 日志查看器直接输入网址打不开 / 提示「拒绝直接访问」

- **这是预期的安全行为（防直连）**：日志查看器 `index` 视图要求携带主后端签发的一次性令牌（`POST /api/auth/logviewer-token`，仅 `SUPER_ADMIN` 可获取，默认 120s 有效）。直接输入网址、书签、复制链接都无令牌 → 403 拒绝。
- **正确入口**：主系统「系统设置 → 日志查看器」按钮（仅超级管理员可见）。点击时前端自动取令牌并拼入跳转 URL。
- **仍打不开**：① 确认已用超级管理员账号登录；② 令牌 120s 内有效，超时重开按钮即可；③ 若 403 持续，检查主后端与日志查看器 `.env` 的 `LOGVIEWER_SECRET_KEY` 是否**一致**（不一致会导致验签失败）；④ 经 nginx 子域 `log.<DOMAIN>` 访问需 DNS A 记录 + certbot 覆盖该子域（见 deploy/README.md）；⑤ **无域名纯 IP 部署**经 `http://<IP>:8120/` 访问，需确认 `deploy/nginx-gipfel.conf` 的 8120 端口块已生效（deploy 脚本无 `--domain` 时自动保留），且云/系统防火墙放行 TCP 8120（deploy 脚本无域名时自动 `ufw allow 8120/tcp`，否则需手动放行）。
- **底层**：`LOGVIEWER_GATE_MAX_AGE`（秒）/ `LOGVIEWER_GATE_SALT` 在 `backend/logviewer/logviewer/settings.py` 可调；`LOGVIEWER_SECRET_KEY` 在主后端 `settings.py` 读取，与日志查看器共用同一 `.env`。

### Q12. 域名部署后主站正常，但日志查看器打不开

> ## ⚠️ 若你用的是 `http://<域名>:8120/...` —— 这个地址**永远打不开**，不是配置问题
>
> **Cloudflare 只代理固定端口**（[官方 Network ports 文档](https://developers.cloudflare.com/fundamentals/reference/network-ports/)）：HTTP `80/8080/8880/2052/2082/2086/2095`，HTTPS `443/2053/2083/2087/2096/`**`8443`**。**8120 不在其中。**
> 橙云时 DNS 返回 Cloudflare 的 IP，而 CF 边缘不服务 8120 → 请求到不了源站。`:8120` 形态**只适用于「无域名、直连 IP」的部署**。
>
> **正确入口取决于能否给域名加 `log.` 三级记录**：能加 → `https://log.<域名>/`；**不能加**（例如域名是别人给的子域）→ `https://<域名>:8443/`（形态 B，见下）。

- **现象**：`https://<域名>/` 一切正常，点「系统设置 → 日志查看器」却打不开（连接被拒 / 400 / 403 / 显示成主站）。
- **根因**：这是**几个各自独立**的问题，症状不同、修法也不同。日志查看器是**独立 Django 服务**（`backend/logviewer/`），它有**自己**的 `ALLOWED_HOSTS` 与 `CSRF_TRUSTED_ORIGINS`——主站正常不代表它也正常。

| 现象 | 成因 | 修法 |
| --- | --- | --- |
| **连接被拒/超时**，且地址带 `:8120` | CF 不代理 8120（见上框） | 改用子域形态，或形态 B（`--origin-cert`，端口默认 8443） |
| **400** `Invalid HTTP_HOST header` | 主机名不在日志查看器 `ALLOWED_HOSTS` 里。它由 `DJANGO_ALLOWED_HOSTS` 兜底纳入，而 deploy 脚本过去**只**写主域、从不写 `log.<域名>` | ★ 已修（脚本现在同时追加 `<域名>` 与 `log.<域名>`）。手工：`DJANGO_ALLOWED_HOSTS=<域名>,log.<域名>,localhost,127.0.0.1` → `sudo systemctl restart gipfel gipfel-logviewer` |
| **403**（登录 POST 失败） | `CSRF_TRUSTED_ORIGINS` 里没有该来源。nginx 以 `Host $host:$server_port` 透传，**默认端口**下 `get_host()` = `<域名>:80`，而浏览器 `Origin` 会**省略默认端口**；Django 的 `_origin_verified` 是**字符串相等**比较 → 对不上。该项过去只从 `LOG_VIEWER_PUBLIC_URL` 推导，而域名模式下脚本不写这一项 | ★ 已修（settings 现按 `ALLOWED_HOSTS` 统一补 `http://` 与 `https://` 两种来源） |
| **证书错误 / 显示成主站** | 按钮地址派生为 `https://log.<域名>/`（`backend/apps/auth/views.py`），但 nginx 没有该子域的 443 块 | 用子域形态（certbot 带 `-d log.<域名>`），或改走形态 B |

#### ★ 形态 B：域名走 CF 但加不了 `log.` 记录 → 用 8443 端口

```bash
sudo bash scripts/update-from-github.sh --source-dir <clone 目录> \
     --install-dir /opt/gipfel --with-nginx --domain <域名> \
     --origin-cert
```

- ★ **`--origin-cert` 时日志查看器 TLS 端口默认就是 `8443`**，不必手传（要换用 `--logviewer-tls-port <端口>`；走 `log.<域名>` 子域形态则用 `--no-logviewer-tls` 关闭）
- 脚本渲染 `listen 8443 ssl` 的日志查看器块（**复用主站 Origin Certificate，不需要 `log.` 域名**），并把 `LOG_VIEWER_PUBLIC_URL` 写成 `https://<域名>:8443/`——前端按钮即指向它
- ★ **必须在云控制台安全组入方向放行 TCP 8443**（脚本只能放行本机 ufw）
- ℹ️ **`LOG_VIEWER_PORT`（默认 8120）是另一回事**：它是**纯 IP 明文形态**的端口，与这里的 TLS 端口互不影响
- Origin Certificate 的 Hostnames 需含 `<域名>`（形态 B 不需要 `log.` 前缀）
- 认证不变：仍走主系统「系统设置 → 日志查看器」按钮签发的一次性令牌，进入后仍需超管登录

- **分层定位（一条命令看断在哪层）**：
  ```bash
  systemctl is-active gipfel-logviewer && ss -lntp | grep 8121        # 服务在跑吗
  ss -lntp | grep -E ':(443|8443)\b'                                  # 公网监听起来了吗
  curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: <域名>' http://127.0.0.1:8121/
  #   400 → 白名单；403 → CSRF；200/302 → nginx 与 Host 链路正常
  sudo nginx -T | grep -E 'server_name|listen 443|listen 8443' | head
  ```
- **注意**：① 单独改 `DJANGO_ALLOWED_HOSTS` 后必须重启 **`gipfel-logviewer`**（它读自己的 settings），只重启 `gipfel` 无效；② 不要**同时**用 certbot `-d log.<域名>` 和手工 443 块——同一 `server_name` 两个 443 块会让 nginx 报 `conflicting server name` 并只用一个；③ 令牌默认 **120 秒**有效，过期表现为**被 302 弹回前端首页**（不是报错页），所以请从系统里点按钮、别手拼 URL。

### Q9. 直接输入网址打开 /admin 被跳回前端首页

- **这是预期的安全行为（防直连）**：后端 `/admin` 管理后台由 `BackendGateMiddleware` 网关保护，要求携带主后端签发的一次性令牌（`POST /api/auth/backend-token`，仅 `SUPER_ADMIN` 可获取，默认 120s 有效）。直接输入网址、书签、复制链接都无令牌 → 302 重定向回前端 SPA 根路径 `/`。
- **正确入口**：主系统「系统设置 → 后端管理界面」按钮（仅超级管理员可见）。点击时前端自动取令牌并拼入跳转 URL。
- **仍打不开**：① 确认已用超级管理员账号登录；② 令牌 120s 内有效，超时重开按钮即可；③ 若持续重定向，检查 `.env` 的 `LOGVIEWER_SECRET_KEY` 是否与后端一致（网关验签依赖它）。

### Q10. 打开网站显示「Welcome to nginx!」默认页

- **现象**：访问 `http://<服务器IP>/` 看到 nginx 默认欢迎页，而不是商赛系统登录页。
- **根因**：nginx 自带默认站点（`sites-enabled/default`，旧脚本可能改名成 `default.disabled`）仍然被加载，且其 `server_name _` 与 gipfel 主站点在 80 端口撞名，把 80 端口抢走了。gipfel 配置没失效，只是没拿到 80 端口。
- **自动修复**：`deploy-linux.sh` / `update-from-github.sh` 的 `--with-nginx` 步骤现在会**自动删除**所有默认站点变体（`sites-enabled/default`、`sites-enabled/default.disabled`、`sites-enabled/default.conf`、`conf.d/default.conf`），始终刷新 `gipfel.conf` 软链，reload 后还会 `curl 127.0.0.1` 校验：若仍返回欢迎页会告警（提示检查 `nginx.conf` 是否内联了默认 server 块），若后端 `gipfel` 未起会提示 502 排查。**重跑部署脚本（加 `--with-nginx`）即可自动解决**。
- **手动补救**（脚本跑过仍异常时）：
  ```bash
  sudo rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/default.disabled \
            /etc/nginx/sites-enabled/default.conf /etc/nginx/conf.d/default.conf
  sudo ln -sf /etc/nginx/sites-available/gipfel.conf /etc/nginx/sites-enabled/gipfel.conf
  sudo nginx -t && sudo systemctl reload nginx
  ```
- **仍显示欢迎页**：`cat /etc/nginx/nginx.conf` 看是否内联了 `server { ... }` 默认块（去掉或注释它），或 `ls /etc/nginx/sites-enabled/` 是否还有其它非 gipfel 配置冲突。
- **变为 502 Bad Gateway**：nginx 已正确接管，但后端 `gipfel` 服务没跑：`sudo systemctl restart gipfel`。

### Q11. 配了域名和证书，HTTPS 打不开（CDN 报 521）

- **现象**：`http://<域名>/` 正常，但 `https://<域名>/` 打不开；前面挂了 Cloudflare 时页面显示 **521 Web server is down**。
- **根因**：**521 是 CDN 的错误码**（不是 nginx 也不是浏览器的），官方定义是「**源站拒绝来自 Cloudflare 的连接**」，所以此时翻 nginx 日志通常什么都没有——请求根本没到源站。两条主因：① **源站没有在 TLS 模式要求的端口上监听**（Flexible 要求 80，Full / Full (strict) 要求 443）；② **源站防火墙 / fail2ban / 安全软件挡掉了 Cloudflare 的 IP 段**。绝大多数是第 ① 条，且具体形态是：`deploy/nginx-gipfel.conf` 里的 443 块**默认是注释态模板**，而 `deploy-linux.sh` **不申请证书**。于是「证书已签发」和「nginx 用上证书」是**两件独立的事**——`certbot certonly`、或 `certbot --nginx` 中途失败，都会留下「证书在、443 没监听」这个状态。
- **一条命令定性**：
  ```bash
  ss -lntp | grep -E ':(80|443)\b'                       # 443 无输出 → 就是它
  grep -n 'listen' /etc/nginx/sites-available/gipfel.conf  # 只有 listen 80 → 模板未启用
  sudo certbot certificates                              # 证书是否存在、路径为何
  ```
  > **521 与 522 的区别**：521 = 「拒绝」（端口上没有任何进程监听，内核回 REJECT）；522 = 「丢包」（ufw 默认 DROP 策略把包丢了）。所以看到 521，第一嫌疑是「nginx 没在 443 上监听」。
- **修复（推荐，挂 Cloudflare 时首选）**：用 **Cloudflare Origin Certificate** + 脚本一条命令——**不依赖 DNS 校验**，因此不会出现「因为 `log.<域名>` 没有记录而整条签发失败」：
  ```bash
  # 1) CF 面板：SSL/TLS → 源服务器 → 创建证书（Hostnames 填 <域名> 与 log.<域名>，有效期可选 15 年）
  # 2) 放好两个文件（文件名必须是 <域名>.pem / <域名>.key）
  sudo install -d -m 755 /etc/ssl/cloudflare
  sudo install -m 644 cert.pem /etc/ssl/cloudflare/<域名>.pem
  sudo install -m 600 key.pem  /etc/ssl/cloudflare/<域名>.key
  # 3) 由脚本渲染 443 块并 reload（首次部署用 deploy-linux.sh，升级用 update-from-github.sh）
  sudo bash scripts/update-from-github.sh --source-dir <clone 目录> \
       --install-dir /opt/gipfel --with-nginx --domain <域名> --origin-cert
  # 4) CF 面板 SSL/TLS 模式设为 Full (strict)
  ```
  ★ **升级时也必须带 `--origin-cert`**：升级会用模板产物整体覆盖 vhost，而模板里 443 块是注释态，不带这个开关重跑等于把 443 抹掉。脚本已加防护——检测到现有 vhost 已有生效的 443 而本次未传该开关时**会中止并备份**，不会静默摧毁。完整步骤见 [deploy/README.md](../deploy/README.md) 的「路线 A：Cloudflare Origin Certificate（完整步骤）」。
- **修复（备选）**：让 certbot 写入 443 块。
  ```bash
  sudo certbot --nginx -d <DOMAIN> --non-interactive --redirect   # 只签主域，最稳
  ```
  ⚠️ 若加上 `-d log.<DOMAIN>`，**该子域必须有 DNS 记录**——解析不到会让**整条命令中止**、主域证书也拿不到、443 块写不进去。这正是「证书申请了但 HTTPS 起不来」最常见的原因。
  ⚠️ **不要**与 `--origin-cert` 混用：certbot 会自行写入 443 块，两者会让同一 `server_name` 出现两个 443，nginx 只取一个。
- **签发前置（仅 certbot 路线需要）**：源站 80 端口公网可达；**经 Cloudflare 时须临时关闭 Always Use HTTPS / 边缘跳转**，否则 HTTP-01 校验会跟着跳到尚不可用的 443。自检：`curl -sS -o /dev/null -w '%{http_code}\n' http://<DOMAIN>/.well-known/acme-challenge/probe` 期望 **404**（请求到达了 nginx），而不是 301/522。
- **防火墙**：`deploy-linux.sh` / `update-from-github.sh` 现已自动 `ufw allow 80/tcp`、`443/tcp`（此前脚本**只**处理日志查看器端口，从未放行 80/443）；**云控制台的安全组**脚本管不到，需手动放行 TCP 80/443。另需确认源站没有把 [Cloudflare 的 IP 段](https://www.cloudflare.com/ips/) 拉黑（fail2ban / 云 WAF 误封是官方点名的 521 第二大成因）：`sudo fail2ban-client status`、`sudo iptables -S | grep -i drop`。
- **CDN 的 SSL/TLS 模式**：必须 **Full (strict)**。**Flexible 与 certbot `--redirect` 叠加会变成重定向循环**（CDN 回源 80 → 源站 301 到 443 → CDN 再回 80）。
- **仍有问题**：完整错误码对照（521/522/523/524/525/526）与源站加固（Origin Certificate、只允许 Cloudflare 回源、经 CDN 后的真实客户端 IP）见 [deploy/README.md 的「Cloudflare / CDN 前置（H4）」](../deploy/README.md)。

## 10. 安全与合规速览

- **JWT**：HS256，`JWT_SECRET` 必填（未配置进程 fail-fast 拒绝启动），默认 24h，`tokenVersion` 顶号立即失效。Django 自身 `SECRET_KEY` 支持经 `DJANGO_SECRET_KEY` 独立配置（未配置回退 `JWT_SECRET`；更换会使 session/CSRF cookie 失效，择机轮换）。
- **改密吊销会话**：改密成功后端递增 `token_version` 吊销**所有**旧 token（含当前会话，防止旧凭据残留）；**后端在同一次请求内直接签发新 token 并随响应返回**（[ChangePasswordView](backend/apps/auth/views.py) 改密、递增 token_version、签发新 token 三步在同一 ORM 实例上原子完成，规避 SQLite 写后读竞态），前端用响应里的 `token` 字段直接替换旧 token 即可，本设备会话无感续接，其他设备被正确踢下线。脚本/SDK 调用方须从 `change-password` 响应里取 `token` 续接。
- **RBAC**：39 个权限键、19 个权限域；5 级动作等级蕴含（`view<edit<manage<execute<audit`，合同域自定义）。`can(action, resource)` 前后端一致。
- **比赛隔离**：读查询按 `competition_id` 自动域过滤（`apply_competition_scope`）；写操作 `create_competition_id` 强制归属（非超管忽略请求体 competitionId）；`CompetitionScopePermission` 挂载 DRF 全局默认兜底。
- **客户端 IP 信任链**：`client_ip()` 仅对可信代理（默认回环，`TRUSTED_PROXIES` 可扩展）信任 `X-Real-IP`，直连后端无法伪造该头绕过登录限速。
- **CORS**：未配置 `CORS_ORIGIN` 时仅本地/私网反射并带凭据；公网必须显式白名单。
- **登录限流**：见 Q7。
- **日志查看器防直连**：公网整站代理（nginx 监听公网 `:8120` → 反代内网 `127.0.0.1:8121`，日志查看器 daphne 仅绑内网 8121）；有域名经 nginx 子域 `log.<DOMAIN>`（端口 80），无域名纯 IP 经 nginx `:8120` 端口（`server_name _`）。`index` 视图校验主后端签发的一次性签名令牌（`LOGVIEWER_SECRET_KEY` 共享密钥，默认 120s 有效），缺失/无效/过期即 403，实现「仅按钮点击可跳转、直连网址无法跳转」。进入后仍需后台超级管理员登录。
- **后端管理后台 `/admin` 防直连**：`BackendGateMiddleware` 网关保护 `/admin/*`，要求携带主后端签发的一次性签名令牌（`LOGVIEWER_SECRET_KEY` 共享密钥 + salt `backend-gate`，默认 120s 有效），缺失/无效/过期即 302 重定向回前端 SPA，实现「仅按钮点击可跳转、直连网址自动跳回前端」。进入后仍需 Django 后台超级管理员登录。令牌有效期由 `.env` 的 `BACKEND_GATE_MAX_AGE`（秒）控制。
- **⚠️ 后台写库警示**：Django `/admin` 直接写 SQLite 会**绕过业务校验**（合同引擎、股票计算、权限派生、乐观锁级联重算等），常规管理请走前端界面；后台仅用于运维临时修数，改完回前端核对一致性。

## 11. 升级流程

### 首次部署（Linux 一键部署）

完整步骤见 [`deploy/README.md`](deploy/README.md) 的「获取源码」一节。最简流程（默认分支 `master`，纯 IP 省略 `--domain`）：

```bash
# 克隆到 /opt（不要叫 /opt/gipfel，会与安装目录 rsync 自拷贝冲突）
git clone https://github.com/bingdexzx/GipfelBusinessCompetitionManagerWeb.git /opt/GipfelBusinessCompetitionManagerWeb
cd /opt/GipfelBusinessCompetitionManagerWeb
sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx
# 有域名时加 --domain：sudo bash scripts/deploy-linux.sh --domain 你的域名 --install-dir /opt/gipfel --with-nginx
```

> GitHub 直连超时（`curl 28` / 443 连不上）时，可用镜像前缀 clone：`git clone https://ghproxy.com/https://github.com/bingdexzx/GipfelBusinessCompetitionManagerWeb.git`；或按 `deploy/README.md` 安装 FastGitHub 本地代理后走 `http://127.0.0.1:38457`。服务器完全连不上 GitHub 时，在能联网的机器上打包源码 `tar czf ...` 再 `scp` 传到服务器解包部署（详见 deploy/README.md）。

### 增量升级（保留数据）

**推荐：使用专用升级脚本 [`update-from-github.sh`](../scripts/update-from-github.sh)**——自动完成「拉取最新 + 备份 db/uploads/.env + 迁移 + 收集静态 + 前端构建 + 权限归属 + 重启」，与首次部署一致且保留数据，无需手敲多步：

```bash
# 情况一：部署目录 /opt/gipfel 本身就是 git clone（首次直接用 git clone 拉起）→ 模式 A 原地 pull
sudo bash scripts/update-from-github.sh --install-dir /opt/gipfel --with-nginx
#   有域名时加 --domain：sudo bash scripts/update-from-github.sh --install-dir /opt/gipfel --domain 你的域名 --with-nginx

# 情况二：按本文档流程（clone 到独立目录 /opt/GipfelBusinessCompetitionManagerWeb，再用 deploy-linux.sh rsync 到 /opt/gipfel）→ 模式 B
cd /opt/GipfelBusinessCompetitionManagerWeb
sudo bash scripts/update-from-github.sh --source-dir /opt/GipfelBusinessCompetitionManagerWeb --install-dir /opt/gipfel --with-nginx

# 纯 IP（无域名）部署：省略 --domain 即可，脚本自动自愈 LOG_VIEWER_PUBLIC_URL 与
# DJANGO_ALLOWED_HOSTS（幂等追加公网 IP）；受限网络探测不到公网 IP 时显式指定：
sudo bash scripts/update-from-github.sh \
  --source-dir /opt/GipfelBusinessCompetitionManagerWeb \
  --install-dir /opt/gipfel --with-nginx --public-ip 43.142.77.225
```

> 脚本自动：① 拉取最新代码 ② 备份 `db.sqlite3`+`uploads`+`.env` 到 `_backup/<时间戳>` ③ 更新代码（排除数据文件）④ `pip install`+`migrate`+`collectstatic` ⑤ `npm ci`+`npm run build`→`frontend-dist/` ⑥ chown 归属 gipfel、`.env` 权限 600 ⑦ 纯 IP 自愈（`LOG_VIEWER_PUBLIC_URL` + `DJANGO_ALLOWED_HOSTS`）⑧ 刷新 systemd 单元并 restart `gipfel`(+`gipfel-logviewer`) ⑨ [--with-nginx] 刷新 vhost 并 reload。完整细节见 [`deploy/README.md`](deploy/README.md) 的「更新部署」一节。

> ## ⚠️ 域名部署升级时**必须**带 `--domain`（上面示例默认是纯 IP 写法）
>
> 漏传会在两处出错，且都不显眼：
> 1. ★ **脚本中途静默终止**：走进「无域名」分支去读 `.env` 的 `LOG_VIEWER_PUBLIC_URL`，而域名部署从不写该项 → `grep` 无匹配 → `pipefail` 下管道失败 → 变量赋值失败 → `set -e` 终止且**无任何输出**。现象是「跑到『文件归属已切换』就没了」（已在脚本中修复并加装 ERR trap 报出终止行号）。
> 2. ★★ **`--with-nginx` 会把域名 vhost 改写回纯 IP 形态**：`server_name` 变 `_`、日志查看器子域块被删除、改回 8120 端口块 —— 域名与 `log.<域名>` 随即失效且**没有报错**。
>
> 正确写法：`sudo bash scripts/update-from-github.sh --source-dir <clone 目录> --install-dir /opt/gipfel --with-nginx --domain <你的域名>`

<details>
<summary>手动升级步骤（不依赖脚本时，需自行处理备份 / 静态 / 权限，否则易踩坑）</summary>

```bash
git pull                         # 拉取代码
# 后端
cd backend && .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput   # 生产环境 /admin 等静态资源必需，漏了会 404
# 前端
cd ../frontend && npm ci && npm run build
# 重启（Linux 需先 chown -R gipfel:gipfel /opt/gipfel 并 chmod 600 .env，否则权限拒绝）
systemctl restart gipfel        # Linux
# Windows 开发：Ctrl+C 停 start-dev.bat 后重跑
```
</details>

升级后务必跑一次第 5 节的健康检查。
