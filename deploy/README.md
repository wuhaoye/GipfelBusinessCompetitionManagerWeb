# 部署手册

## 方案一：Linux 一键部署（推荐 · Ubuntu 22.04 / Debian 12）

```bash
cd GipfelBusinessCompetitionManagerWeb
sudo bash scripts/deploy-linux.sh \
  --domain comp.example.com \
  --install-dir /opt/gipfel \
  --with-nginx
```

> **依赖说明**：脚本默认会自动 `apt-get install` 所需系统包（`python3` / `nginx` / `nodejs` / **`rsync`** 等）。若使用 `--skip-install-deps` 跳过安装，需确保目标机**已装好 `rsync`**——代码同步阶段强依赖它，缺失会报 `rsync: command not found`。

> **⚠️ 必须用 root 登录环境执行**：`sudo -i` 或 **`su -`**（带 `-`）。**不要用 `su`（不带 `-`）或 `su -c`** —— 它的 PATH 是
> `/usr/local/bin:/usr/bin:/bin:/usr/games`，**不含 `/usr/sbin`**，于是 `useradd` 变成 `command not found`：创建运行用户的步骤会静默失败，
> 脚本一路跑到前端构建之后才以 `chown: invalid user: 'gipfel:gipfel'` 报错退出，现场留下「`.venv`/`db.sqlite3`/前端产物都在、却没有 `gipfel`
> 用户与 systemd 单元」的半成品部署，排查方向还会被误导到文件权限上。
> 若部署账号不在 `sudoers`（Debian 安装时设置了 root 密码，就不会把首个用户加入 `sudo` 组），可 `su -` 后用 root 执行，或先
> `usermod -aG sudo <用户名>`。脚本自身已启动时补齐 `/usr/sbin`（审计 X-30），但仍建议按规范使用登录 shell。

### 获取源码（clone 到服务器）

部署脚本必须在源码树内执行（`scripts/deploy-linux.sh` 的相对路径依赖它所在目录），所以**先 clone 到服务器，再进去跑脚本**。

```bash
# 0) 安装 git（若尚未安装）
sudo apt-get install -y git

# 1) 克隆仓库到 /opt（默认分支 master；克隆目录不要叫 /opt/gipfel，否则会与安装目录 rsync 自拷贝冲突）
git clone https://github.com/bingdexzx/GipfelBusinessCompetitionManagerWeb.git /opt/GipfelBusinessCompetitionManagerWeb
cd /opt/GipfelBusinessCompetitionManagerWeb/

git clone https://gitee.com/to-uphold-virtue/GipfelBusinessCompetitionManagerWeb.git /opt/GipfelBusinessCompetitionManagerWeb
cd /opt/GipfelBusinessCompetitionManagerWeb/

# 2) 一键部署：纯 IP 先用「无 --domain」，有域名加 --domain
sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx
#   有域名时：
#   sudo bash scripts/deploy-linux.sh --domain comp.example.com --install-dir /opt/gipfel --with-nginx
```

> **网络前提**：部署过程中服务器需能出网到 **apt 源 / PyPI（`pip install`）/ npm 源（`npm ci`）**。仅 GitHub 不通、但 apt/npm/PyPI 可达时，可用下方镜像绕过；若全部都不通，走「服务器完全连不上 GitHub」的 tar 包方案（前端 dist 也可在能联网的机器预构建后整体传入）。

> **GitHub 直连超时（`curl 28` / GnuTLS -110 / 443 连不上）？** 国内或受限网络常见，几种绕过方式：
> - **重试**：GnuTLS -110 常为瞬时抖动，直接重跑 `git pull` 一两次即可成功；
> - **一次性镜像 pull**（不改全局配置）：
>   `git pull https://ghproxy.net/https://github.com/bingdexzx/GipfelBusinessCompetitionManagerWeb.git master`
> - 或让**当前仓库**后续 git 操作走镜像（在 clone 目录里执行；之后普通 `git pull` 即可，
>   `update-from-github.sh` 的 pull 同样受益）：
>   ```bash
>   cd /opt/GipfelBusinessCompetitionManagerWeb     # 你的 clone 目录
>   git config url."https://ghproxy.net/https://github.com/".insteadOf "https://github.com/"
>   # 用毕撤销：git config --unset url."https://ghproxy.net/https://github.com/".insteadOf
>   ```
>   **不要用 `--global`**：那会把机器上**所有** `https://github.com/` 请求改写到第三方代理域名，
>   包括携带 `Authorization` 头/私有仓库凭据的请求，代理可以记录甚至篡改代码。若已加过全局配置，
>   用 `git config --global --unset url."https://ghproxy.net/https://github.com/".insteadOf` 撤销。
>   按仓库配置只影响这一个 clone，风险面小得多。
> - 镜像前缀可用性随时间变化，可依次尝试：`https://ghproxy.net/`、`https://ghfast.top/`、`https://gh-proxy.com/`、`https://mirror.ghproxy.com/`、`https://gitclone.com/github.com/`（挑能连的）。

> **镜像也不稳、想直接用 GitHub 地址拉取？** 可在服务器上安装 FastGitHub（本地代理加速/恢复 GitHub 连接），之后所有 git/curl 命令自动走 `127.0.0.1:38457` 即可正常访问 GitHub：
> ```bash
> # 1) 下载 FastGitHub（从 Gitee Release，适合 GitHub 连不上的环境）
sudo wget -c -O /opt/fastgithub_linux-x64.zip \
   https://gitee.com/chcrazy/FastGitHub/releases/download/latest/fastgithub_linux-x64.zip
> 
> # 2) 解压
sudo unzip -d /opt /opt/fastgithub_linux-x64.zip
 rm /opt/fastgithub_linux-x64.zip
>
 sudo apt-get install -y libicu-dev
> # 3) 启动 FastGitHub（后台代理，占用 38457 端口）
 sudo /opt/fastgithub_linux-x64/fastgithub start
> 
> # 4) 设置代理（当前 shell；如需永久生效，写入 /etc/profile 后重新登录）
 export http_proxy=http://127.0.0.1:38457
 export https_proxy=http://127.0.0.1:38457
> 
> # 5) 之后即可正常 clone GitHub 仓库到 /opt
 git clone https://github.com/bingdexzx/GipfelBusinessCompetitionManagerWeb.git /opt/GipfelBusinessCompetitionManagerWeb
 cd /opt/GipfelBusinessCompetitionManagerWeb
> ```
> 注意：FastGitHub 进程需保持运行；生产服务器建议用 `systemd` 或 `nohup/screen` 常驻。apt/pip/npm 等流量也会走该代理，通常无碍；若只想让 git 走代理，可用上方**按仓库**的 `git config url...insteadOf` 方案（不要用 `--global`，见该处说明）。

> **服务器完全连不上 GitHub（连镜像也不行）？** 在能访问 GitHub / 已含代码的机器上打包源码传上去，再在服务器本地跑脚本（不需要服务器联网到 GitHub）：
> ```bash
> # 在「源机器」打包（排除重型/生成目录）
> tar czf gipfel-deploy-src.tgz --exclude='.git' \
>   --exclude='backend/.venv' --exclude='backend/db.sqlite3' --exclude='backend/uploads' \
>   --exclude='backend/logs' --exclude='backend/staticfiles' --exclude='backend/logviewer/staticfiles' \
>   --exclude='frontend/node_modules' --exclude='frontend/dist' \
>   backend frontend deploy scripts docs README.md
> # 传到服务器
> scp gipfel-deploy-src.tgz root@<服务器IP>:/tmp/
> # 服务器上解包并部署（脚本从解包后的源码树内运行，无需 --source-dir）
> mkdir -p /opt/gipfel-src && tar xzf /tmp/gipfel-deploy-src.tgz -C /opt/gipfel-src
> cd /opt/gipfel-src && sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx
> ```

### 脚本执行完之后

| 产物 | 位置 |
| --- | --- |
| 代码 | `/opt/gipfel/`（整个项目复制过去） |
| 虚拟环境 | `/opt/gipfel/backend/.venv/` |
| 数据库 | `/opt/gipfel/backend/db.sqlite3` |
| 上传 | `/opt/gipfel/backend/uploads/` |
| 日志-Django | `/opt/gipfel/backend/logs/` + `/var/log/gipfel/` |
| 日志-nginx | `/var/log/nginx/gipfel.{access,error}.log` |
| systemd 服务 | `/etc/systemd/system/gipfel.service`（daphne/ASGI）、`gipfel-wsgi.service`（gunicorn/WSGI）、`gipfel-logviewer.service` |
| nginx vhost | `/etc/nginx/sites-available/gipfel.conf`（sites-enabled 软链）。**有域名**时含 `log.<DOMAIN>` 日志查看器子域块；**无域名（纯 IP）**时自动换成 `:8120` 端口块（`server_name _`）——deploy 脚本按是否传 `--domain` 保留对应一块、删除另一块 |
| 前端静态 | `/opt/gipfel/frontend-dist/`（由 nginx root 直接托管） |
| 日志查看器静态 | `/opt/gipfel/backend/logviewer/staticfiles/`（由 nginx 日志查看器块 alias 托管：有域名是 `log.<DOMAIN>` 块，无域名是 `:8120` 块） |

### 验证

```bash
systemctl status gipfel               # active (running) ← daphne(ASGI) :8000，承载 /socket.io/
systemctl status gipfel-wsgi          # active (running) ← gunicorn(WSGI) :8002，承载 /api/、/admin/
systemctl status nginx                # active (running)
systemctl status gipfel-logviewer     # active (running)  ← 日志查看器

# C1-a：两个后端进程要分别探活（只看一个会漏掉"另一个没起来"）
curl -sS http://127.0.0.1:8002/api/health                      # 直连 WSGI(gunicorn) → ok:true
curl -sS 'http://127.0.0.1:8000/socket.io/?EIO=4&transport=polling'   # 直连 daphne 的 Socket.IO 握手 → HTTP 200（响应体 0{"sid":...}）
curl -sS http://127.0.0.1/api/health                           # 经 nginx（应落在 WSGI 上）→ ok:true
curl -sS -I http://127.0.0.1/         # 200（nginx 托管 index.html）
# 浏览器打开 https://comp.example.com
```

> **端口速查**：`8000` = daphne(ASGI，只绑回环，只服务 `/socket.io/`)；`8002` = gunicorn(WSGI，只绑回环)；
> `8121` = 日志查看器 daphne；`80/443`（或纯 IP 形态的 `8120`）= nginx。两个后端端口都不对公网开放。
> WSGI 端口可用 `backend/.env` 的 `GIPFEL_WSGI_PORT` 改（改完脚本会同步 nginx upstream，见下节）。

### C1-a 双进程部署（HTTP 与 WebSocket 分离）

**为什么**：改造前只有一个 daphne 进程，Django 的同步视图全部排队给**同一个**线程执行器 ——
8 个并发请求 = 串行 8 次，全场 100 人时"点一下没反应、15 秒后集体超时"。
C1-a 把「短请求」与「长连接」拆成两组进程，让 `/api/` 真正并发。

| 进程 | unit | 监听 | 承载 | nginx upstream |
| --- | --- | --- | --- | --- |
| daphne（ASGI） | `gipfel.service` | `127.0.0.1:8000` | `/socket.io/*`（+ 回环上仍挂完整 Django 作兼容） | `gipfel_socketio` |
| gunicorn（WSGI） | `gipfel-wsgi.service`（新增） | `127.0.0.1:8002` | `/api/*`、`/admin/*` | `gipfel_django` |
| 日志查看器 daphne | `gipfel-logviewer.service` | `127.0.0.1:8121` | 日志查看器整站 | 直连（`log.<域名>` 块） |

- **gunicorn 参数**：`-w 4 -k gthread --threads 8` → 32 路并发（`backend/backend/wsgi.py`，无需新入口）。
  端口/worker/线程/超时都有 unit 默认值，可用 `backend/.env` 覆盖：
  `GIPFEL_WSGI_PORT=8002`、`GIPFEL_WSGI_WORKERS=4`、`GIPFEL_WSGI_THREADS=8`、`GIPFEL_WSGI_TIMEOUT=120`。
  ⚠️ unit 里 `Environment=`（默认值）写在 `EnvironmentFile=` **之前**：systemd 中 `.env` 优先级更高，
  所以改 `.env` 即可覆盖，不必改 unit（unit 在仓库里，升级会被覆盖）。
  改了 `GIPFEL_WSGI_PORT` 后重跑部署/升级脚本即可（脚本会把渲染产物里 upstream 的端口一起改掉）；
  手工改 `.env` 而不重跑脚本，nginx 会继续指向 8002 → 全站 502。
- **跨进程实时广播**：daphne 进程以 `REALTIME_BUS=hub` 运行（唯一持有 Socket.IO 连接与事件环），
  gunicorn 进程以 `REALTIME_BUS=forward` 运行，把事件 POST 给 `REALTIME_FORWARD_URL=http://127.0.0.1:8000`
  的内部端点（排队在独立后台线程，业务请求不等待）。**只启一个进程不会让实时功能"半死"**：
  单跑 `gipfel.service`（不带 WSGI）时 hub ≡ local，仍等价改造前行为。
  规模更大/多机时可装 `redis` 并在 `.env` 设 `REALTIME_BUS=redis` + `REALTIME_REDIS_URL`（两端一起覆盖）；
  **redis 缺失/连不上会降级为回环转发，不影响启动**。
- **限流（C3）** 现在由 nginx 兜底，按客户端 IP 聚合，取值都按「全场 100 客户端可能共用一个 NAT 出口 IP」估算：

  | 位置 | 指令 | 取值 | 依据 |
  | --- | --- | --- | --- |
  | `/api/` | `limit_req` + `limit_conn` | `120r/s, burst=240 nodelay` / `600` 连接 | **Debian 13 真机 100 客户端压测校准**：同一 NAT 出口 IP 下 60r/s 会拦掉 10%~32% 的正常重连（实测 64 / 21 个 429），`120r/s+burst240` 为 **0 个 429**，而最坏 1100 请求风暴仍拦掉 709（后端全程存活）。数据与复现脚本见 [`docs/真机验证报告-Debian13.md`](../docs/真机验证报告-Debian13.md) §T3 |
  | `= /api/auth/login` | `limit_req`（更严）+ `limit_conn` | `20r/s, burst=120 nodelay` / `600` | 开场 100 人共用出口 IP 同时登录：前 120 个瞬时请求全放行；防爆破由应用层「同 IP+用户名 10 次/5 分钟 → 锁 15 分钟」负责 |
  | `= /api/health` | **不限流** | — | 监控/部署探针必须永远能通过 |
  | `/socket.io/` | **只限连接数、不限速** | `200` 连接 | 心跳/polling 被限速会表现为"莫名断线→全场重连"；100 客户端 + 100 条重连余量 |

  超限统一返回 **429**；access log 使用 `gipfel_rt` 格式（含 `$request_time`），现场可直接定位长请求。
  现场换算口径（改数值前照它算一遍）：单客户端 ≤5 req/s；100 客户端稳态心跳 = 5 req/s（隐藏页 60s → 1.7 req/s）；
  100 客户端重连尖峰已被前端「并发上限 2 + 指数退避与抖动 + 批量端点」摊平到数秒；同一出口 IP 的在途请求 ≈100~200。
  > `limit_conn` 的口径（nginx 官方文档 + 作者在邮件列表的澄清，见 `deploy/nginx-gipfel.conf` 注释里的两个链接）：
  > **只统计"正在处理请求"的连接**，请求处理完进入空闲 keepalive 后不再计数；Socket.IO 这类长连接整条生命周期都被计数。
  > 所以 `600` 是刻意留了 3 倍余量的**安全上限**，不是常态限制 —— 真正挡洪水的是 `rate`。
  > ⚠️ **若现场真的出现 429**：先看 access log 的 `rt=`/`urt=`（`$request_time`）与实际速率，判断是真实超限还是阈值过严，
  > 再按 zone 调 `rate`/`burst`；**不要直接把限流删掉** —— 那等于把重连尖峰原样丢回后端。

  > ⚠️ **经 CDN（Cloudflare）时先配真实客户端 IP**：未配 `set_real_ip_from` / `real_ip_header CF-Connecting-IP`
  > （见本文档「经 CDN 后的客户端 IP」一节）时，`$remote_addr` 是 CDN 节点 IP，全场流量会落进同一个计数器。
  > 上面的阈值已按"最坏情况全网算一个 IP"留了余量，但**先配真实 IP 再收紧**才是正确顺序。

**排查（一眼定位）**：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `/api/*` 全 502，`/socket.io/` 正常 | gunicorn 没起来（或端口不对） | `systemctl status gipfel-wsgi`；`curl -sS http://127.0.0.1:8002/api/health` |
| 实时消息不动、页面却能用 | daphne 没起来 | `systemctl status gipfel`；`curl -sS 'http://127.0.0.1:8000/socket.io/?EIO=4&transport=polling'` |
| 登录/接口偶发 **429** | 触发了限流 | 看 `gipfel.error.log` 的 `limiting requests`/`limiting connections`；按上表核对是否配了真实客户端 IP；确属正常业务量再调高 `rate`/`burst`（**改完必须写清依据**） |
| `/admin/` 打不开 | 与 `/api/` 同因（上游是 gunicorn） | 同上，先看 `gipfel-wsgi` |
| WSGI 反复重启 | `GIPFEL_WSGI_PORT` 非法/被占用，或 `gunicorn` 未安装 | `journalctl -u gipfel-wsgi -n 50`；`pip install -r backend/requirements.txt` |

**回退到改造前的单进程形态**（C1-a 出问题时的最短路径）：

```bash
# 1) 停用并移除 WSGI 单元（脚本不会自动删 unit，必须手工做这一步）
sudo systemctl disable --now gipfel-wsgi
sudo rm -f /etc/systemd/system/gipfel-wsgi.service && sudo systemctl daemon-reload

# 2) 代码/vhost 回退到改造前的 commit（模板里的 upstream 会自己回到 127.0.0.1:8000）
sudo git -C /opt/GipfelBusinessCompetitionManagerWeb checkout <改造前的 commit>
sudo bash scripts/update-from-github.sh \
  --source-dir /opt/GipfelBusinessCompetitionManagerWeb \
  --install-dir /opt/gipfel --with-nginx [--domain <域名>]      # 参数与本轮部署保持一致

# 3) 确认 daphne 单进程重新承载全部流量
curl -sS http://127.0.0.1/api/health && systemctl is-active gipfel gipfel-wsgi
#    （gipfel-wsgi 显示 inactive/not-found 属预期）
```

> 不想回退代码时，也可以只把渲染后的 vhost 里 `upstream gipfel_django` 的
> `server 127.0.0.1:8002;` 改回 `127.0.0.1:8000;` 再 `nginx -t && systemctl reload nginx`
> （限流指令可保留，也可一并删掉）。这样 `/api/` 又回到 daphne 单线程执行器上 ——
> 并发问题会回来，但功能正常。


### 日志查看器公网访问（防直连）

> ## ⚠️ 先看这条：域名挂了 Cloudflare 时，`:8120` 永远打不开
>
> **Cloudflare 只代理固定端口**（[官方 Network ports 文档](https://developers.cloudflare.com/fundamentals/reference/network-ports/)）：
>
> | | 端口 |
> | --- | --- |
> | HTTP | `80` `8080` `8880` `2052` `2082` `2086` `2095` |
> | HTTPS | `443` `2053` `2083` `2087` `2096` **`8443`** |
>
> **`8120` 不在其中。** 橙云时 DNS 返回的是 Cloudflare 的 IP，而 CF 边缘不服务 8120 → 请求到不了源站。所以
> **`http://<域名>:8120/` 这个形态只在「无域名、直连 IP」的部署下成立**，一旦有了走 CF 的域名就必然失败（现象：连接被拒/超时，而源站其实一切正常）。
>
> 而且 `LOG_VIEWER_PUBLIC_URL` 若还是早期无域名部署留下的 `http://<IP>:8120/`，前端按钮会一直生成那个不可用的地址——请删掉该行或按下面的方式重设。

日志查看器作为独立 Django 站点，经 nginx **整站代理**到 `127.0.0.1:8121`（日志查看器 daphne 仅绑内网 8121，公网端口由 nginx 监听并反代；若 daphne 也用同一端口会与 nginx 抢端口导致 nginx 起不来）。可选形态有三种：

| 形态 | 地址 | 适用 |
| --- | --- | --- |
| **A. 子域** | `https://log.<域名>/` | 域名**能加 `log.` 三级记录**时（最干净） |
| **B. 非标准 TLS 端口** ★ | `https://<域名>:8443/` | 域名走 CF 但**加不了 `log.` 记录**时（如域名是别人给的子域） |
| **C. 纯 IP + 端口** | `http://<IP>:8120/` | **没有域名**、直连源站时 |

**形态 B 的启用方式**（无需任何代码改动；★ **8443 是默认值，不必手传端口**）：

```bash
# 首次部署
sudo bash scripts/deploy-linux.sh --domain <域名> --install-dir /opt/gipfel \
     --with-nginx --origin-cert

# 日常升级（★ 每次都带 --domain --origin-cert，否则 vhost 重渲染后端口块会消失）
sudo bash scripts/update-from-github.sh --source-dir <clone 目录> \
     --install-dir /opt/gipfel --with-nginx --domain <域名> \
     --origin-cert
```

- **`--origin-cert` 时日志查看器 TLS 端口默认就是 `8443`**（因为 Origin Certificate 意味着域名走 Cloudflare，而 CF 只代理固定端口）。要换端口用 `--logviewer-tls-port <端口>`；若你走的是 `log.<域名>` 子域形态，用 `--no-logviewer-tls` 关掉它。
- 脚本会：渲染 `listen 8443 ssl` 的日志查看器 server 块（复用主站证书，**不需要 `log.` 域名**）→ 把 `.env` 的 `LOG_VIEWER_PUBLIC_URL` 写成 `https://<域名>:8443/`（前端按钮即指向它）→ `ufw allow 8443/tcp` → `nginx -t` → reload。

> ⚠️ **还要在云控制台安全组入方向放行 TCP 8443**——脚本只能放行本机 ufw，管不到云侧。
> ⚠️ **Origin Certificate 的 Hostnames 必须含 `<域名>`**（形态 B 不需要 `log.` 前缀）。若用了 `*.域名` 通配则都覆盖。
> ℹ️ **`LOG_VIEWER_PORT`（默认 8120）是另一个东西**：它是**纯 IP 明文形态（C）** 的端口，与形态 B 的 TLS 端口互不影响，保持 8120 不变。


不论哪种形态，均为**仅按钮跳转**：前端「系统设置 → 日志查看器」按钮在点击时向后端 `POST /api/auth/logviewer-token` 获取一次性（默认 120s）签名令牌（仅 `SUPER_ADMIN` 可获取），拼入跳转地址打开（有域名 `https://log.<DOMAIN>/?token=...`，无域名 `http://<IP>:8120/?token=...`，地址由 `/api/version` 下发的 `log_viewer_url` 决定，可用 `.env` 的 `LOG_VIEWER_PUBLIC_URL` 显式覆盖）。日志查看器 `index` 视图校验令牌，缺失/无效/过期均 **403 拒绝**——因此直接输入网址、书签、复制链接都无法进入。

- **前置条件（仅「有域名」形态需要，运维侧）**：
  1. DNS：`log.<DOMAIN>` 的 A 记录指向本服务器；
  2. 证书：`certbot --nginx -d <DOMAIN> -d log.<DOMAIN>`（deploy 脚本的 certbot 提示已纳入该子域）。未启用 HTTPS 时以 HTTP(80) 提供，功能正常（cookie 非 Secure）。
  3. 启用 HTTPS 后，建议在 `.env` 设 `LOGVIEWER_SECURE_COOKIES=true`，使网关会话/ CSRF cookie 标记 Secure。
  4. ★ **`log.<DOMAIN>` 必须在 `DJANGO_ALLOWED_HOSTS` 里**（见下）。
- **无域名防火墙**：8120 端口必须对外可达；云服务器还需在安全组/防火墙放行 TCP 8120（deploy 脚本已尽力 `ufw allow 8120/tcp`，但仍需确认云侧安全组）。
- **共享密钥**：主后端与日志查看器共用 `.env` 的 `LOGVIEWER_SECRET_KEY` 签发/校验令牌。deploy 脚本首次部署自动生成随机值；已部署实例升级时 `.env` 保留不变，两端始终一致。
- **双重认证**：令牌只放行「进入日志查看器站点的网关」，进入后仍需用 Django 后台超级管理员凭据登录才能真正读取日志。

#### ★ 域名形态：日志查看器打不开的三个独立原因

**「域名部署后主站正常、日志查看器打不开」有三种各自独立的成因，症状与修法都不同。** 按下面顺序查，一步一个：

| # | 现象 | 成因 | 修法 |
| --- | --- | --- | --- |
| ① | **400 Bad Request**（`Invalid HTTP_HOST header: 'log.<域名>'`） | `log.<域名>` 不在日志查看器的 `ALLOWED_HOSTS` 里。日志查看器是**独立 Django 服务**，它自己的 `ALLOWED_HOSTS` 由 `DJANGO_ALLOWED_HOSTS` 兜底纳入（`logviewer/settings.py`）——而 deploy 脚本过去**只**往里写主域，从不写 `log.<域名>` | ★ 已修：脚本现在域名模式下会**同时**追加 `<DOMAIN>` 与 `log.<DOMAIN>`。手工补救：`DJANGO_ALLOWED_HOSTS=<域名>,log.<域名>,localhost,127.0.0.1` 后 `sudo systemctl restart gipfel gipfel-logviewer` |
| ② | **403 Forbidden**（登录 POST 失败） | 日志查看器的 `CSRF_TRUSTED_ORIGINS` 里没有 `log.<域名>`。根因是端口写法：nginx 用 `proxy_set_header Host $host:$server_port` 透传，**默认端口**下 `get_host()` 得到 `log.<域名>:80`，而浏览器发出的 `Origin` 会**省略默认端口**（`http://log.<域名>`）；Django 的 `_origin_verified` 做的是**字符串相等**比较（`django/middleware/csrf.py`），于是对不上，只能落到 `CSRF_TRUSTED_ORIGINS`。而该项过去**只**从 `LOG_VIEWER_PUBLIC_URL` 推导——域名模式下脚本不写这一项，等于没有兜底 | ★ 已修：`logviewer/settings.py` 现在按 `ALLOWED_HOSTS` 里的每个主机统一补上 `http://` 与 `https://` 两种来源。（注意 `:8120` 那类**非默认端口**浏览器会带端口、两边恰好一致——为 8120 加的修复没问题，是默认端口引入了新差异） |
| ③ | **连接被拒 / 证书错误 / 显示的是主站** | 按钮地址派生为 `https://log.<域名>/`（`backend/apps/auth/views.py` 按请求 Host 派生），但 **nginx 模板里日志查看器子域只有 80 块，没有 443 块**——SSL 模板只给了主站，注释里写的是「日志查看器子域同理，复制并改」 | 用 certbot 带上子域（推荐）：`sudo certbot --nginx -d <域名> -d log.<域名> --non-interactive --redirect`。**手工路线**见下方片段 |

**手工补日志查看器 443 块**（仅在不用 certbot、自己管证书时需要；在主站 443 块之后追加）：

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name log.<DOMAIN>;

    ssl_certificate     /etc/letsencrypt/live/<DOMAIN>/fullchain.pem;   # 证书需覆盖该子域
    ssl_certificate_key /etc/letsencrypt/live/<DOMAIN>/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    location /static/ {
        alias <INSTALL_DIR>/backend/logviewer/staticfiles/;
        try_files $uri =404;
    }
    location / {
        proxy_pass         http://127.0.0.1:8121;
        proxy_http_version 1.1;
        proxy_set_header Host              $host:$server_port;   # 必须带端口，见上表 ②
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout  120s;
    }
    location ~ /\. { deny all; access_log off; log_not_found off; }
}
```

> ⚠️ **不要同时**启用这个手工块**和** certbot 的 `-d log.<域名>`——两者会为同一 `server_name` 生成两个 443 块，nginx 会报 `conflicting server name` 并只用一个，排查起来很费时间。二选一。

**分层排查命令**（哪一层断了一眼可见）：

```bash
# 1) 日志查看器服务本身是否在跑（daphne 绑内网 8121）
systemctl is-active gipfel-logviewer && ss -lntp | grep 8121

# 2) 绕开 DNS/证书，直接让 nginx 用正确 Host 走一遍（80 端口）
curl -sS -o /dev/null -w 'Host=log.<域名> → %{http_code}\n' \
     -H 'Host: log.<域名>' http://127.0.0.1/
#    400 → 上表 ①     403 → 上表 ②     200/302 → nginx 与 Host 链路正常

# 3) 443 上是否真有该子域的 vhost
sudo nginx -T | grep -A2 'server_name log\.'

# 4) 证书是否覆盖该子域
sudo certbot certificates | grep -A3 'Domains'
```
- **CSRF（登录 403 排查）**：日志查看器登录 `/api/auth/login` 受 `CsrfViewMiddleware` 保护（前端 `app.js` 读 `lv_csrftoken` cookie 写 `X-CSRFToken` 头，正确）。Django 4.0+ 对同源 POST 有「`Origin == scheme://get_host()` 即放行」逻辑，但前提是 `get_host()` 与浏览器 `Origin` **字符串完全相等**（`django/middleware/csrf.py` 的 `_origin_verified` 是直接 `==` 比较，不做端口归一化）。
  **曾现 403 的真因（非默认端口）**：nginx 反代用的是 `proxy_set_header Host $host`，而 nginx 的 `$host` **不含端口**，导致 `get_host()`=`43.142.77.225`、`Origin=`http://43.142.77.225:8120` → 不一致 → 403。已修复：nginx 模板日志查看器块改为 `proxy_set_header Host $host:$server_port`（透传带端口的原始 Host），Django 同源判定即命中。
  ★ **同一写法在默认端口（80/443）上会反向出问题**：浏览器对默认端口**省略不写**（`Origin: http://log.<域名>`），而 `$host:$server_port` 会得到 `log.<域名>:80`，字符串仍不相等。所以真正的兜底必须是 `CSRF_TRUSTED_ORIGINS`。
  **双保险（已修正）**：`logviewer/settings.py` 在加载时写入 `CSRF_TRUSTED_ORIGINS`，来源有两处——① `.env` 的 `LOG_VIEWER_PUBLIC_URL`（精确指定 scheme://host:port）；② ★ **`ALLOWED_HOSTS` 里的每个主机各补 `http://` 与 `https://` 两种来源**。第 ② 条是必需的：域名模式下脚本**不写** `LOG_VIEWER_PUBLIC_URL`，只靠第 ① 条等于没有兜底——这正是「域名部署后日志查看器登录 403」的根因（详见上文「域名形态：日志查看器打不开的三个独立原因」）。
  若仍报 `403 Forbidden` 且非凭证错误：① 确认 nginx 已 reload（`sudo nginx -t && sudo systemctl reload nginx`）；② 重启 `gipfel-logviewer` 服务；③ 检查 `.env` 的 `LOG_VIEWER_PUBLIC_URL` 是否为 `http://<公网IP>:8120/` 形态。

### 无域名纯 IP 部署（适合还没买域名）

直接**省略 `--domain`** 即可：

```bash
cd GipfelBusinessCompetitionManagerWeb
sudo bash scripts/deploy-linux.sh \
  --install-dir /opt/gipfel \
  --with-nginx
# 不传 --domain → nginx 主站点 server_name 为 _（IP 可访问）；
#                 日志查看器改为 :8120 端口块，访问 http://<IP>:8120/；
#                 .env 自动写入 LOG_VIEWER_PUBLIC_URL=http://<IP>:8120/；
#                 .env 的 DJANGO_ALLOWED_HOSTS 自动追加公网 IP（缺失该条目时 Django
#                 对非回环 Host 一律返回 400，登录/健康检查全挂，前端表现为「请求参数错误」）；
#                 ufw 自动放行 8120（若无 ufw 则提示手动放行云安全组）。

# 受限网络自动探测不到公网 IP、或非交互环境（CI/管道）想避免卡在手动输入时，
# 直接显式传 --public-ip（脚本会跳过探测与交互，绝不卡住）：
sudo bash scripts/deploy-linux.sh \
  --install-dir /opt/gipfel \
  --with-nginx \
  --public-ip 43.142.77.225
```

**`LOG_VIEWER_PUBLIC_URL` 取值优先级**（deploy 与 update 脚本一致）：
1. `--public-ip <IP>` 显式指定 → 直接采用（最优先，非交互）；
2. 多服务探测兜底（ipify → ifconfig.me → icanhazip，`timeout` 硬包裹防 DNS 卡死）成功 → 采用探测到的公网 IP；
3. 探测全失败 → 不写该行（已部署实例上误写内网 IP 的旧行会被移除），由后端按请求 Host（nginx 透传 `$host`=公网 IP）推导为 `http://<公网IP>:8120/`。

> 脚本以非交互方式运行（`exec 0</dev/null`），**没有交互式手动填写环节**——受限网络探测不到公网 IP 时请显式传 `--public-ip`，脚本会跳过探测，绝不卡住。

> ⚠️ 切勿用 `hostname -I` 首地址或 Docker 网桥 IP（`172.16–31.x.x`、`10.x`、`192.168.x`、`169.254.x`）充当日志查看器公网地址——这些会暴露内网入口或导致前端跳转打不开。脚本已对 `.env` 中误写的内网 IP 做「自愈」纠正（重部署/升级时自动替换为公网 IP；探测仍失败则移除该行交后端推导）。

- **HTTP 非 HTTPS**：无域名时全站走 HTTP，cookie 非 Secure，功能正常；后续买了域名重跑 `deploy-linux.sh --domain 你的域名 --with-nginx` 即可平滑切换到子域 + HTTPS。
- **访问入口**：浏览器 `http://<IP>/`；日志查看器 `http://<IP>:8120/`（前端「系统设置 → 日志查看器」按钮，需超级管理员登录）。
- **改域名后**：重跑部署脚本传 `--domain`，vhost 会自动把日志查看器切回 `log.<DOMAIN>` 子域块（8120 端口块被删除），并移除 `.env` 里旧的 `LOG_VIEWER_PUBLIC_URL`（需手动删或重跑首次部署）——注意切换后记得跑 certbot 覆盖子域。

### 后端管理后台公网访问（防直连）

后端 `/admin` 管理后台经 nginx 主站点（同域）代理到 `127.0.0.1:8002`（gunicorn/WSGI，C1-a），并由 `BackendGateMiddleware` 网关保护：

- **仅按钮跳转**：前端「系统设置 → 后端管理界面」按钮在点击时向后端 `POST /api/auth/backend-token` 获取一次性（默认 120s）签名令牌（仅 `SUPER_ADMIN` 可获取），拼入 `/admin/?token=...` 打开。后端 `BackendGateMiddleware` 校验令牌，缺失/无效/过期均 302 重定向回前端 SPA——因此直接输入网址、书签、复制链接都会被跳回前端。
- **nginx 路由前提**：`deploy/nginx-gipfel.conf` 中 `location /admin/` 必须显式代理到后端；若缺失，该路径会被 SPA 兜底 `location /` 吞掉返回 `index.html`，管理后台在公网不可达（该 `location` 已在部署模板中内置）。
- **共享密钥**：网关令牌与主后端/日志查看器共用 `.env` 的 `LOGVIEWER_SECRET_KEY` 签发与校验（salt 为 `backend-gate` 以与日志查看器令牌隔离）。deploy 脚本首次部署自动生成随机值；已部署实例升级时 `.env` 保留不变，密钥始终一致。
- **双重认证**：令牌只放行「进入管理后台的网关」，进入后仍需用 Django 后台超级管理员凭据登录才能真正操作。
- **令牌有效期**：`.env` 的 `BACKEND_GATE_MAX_AGE`（秒，默认 120）可调。

### 安全响应头与 HTTPS（E 组加固）

`deploy/nginx-gipfel.conf` 已为所有 `server` 块（主站点、日志查看器子域与 8120 块）统一注入安全响应头：

- `X-Content-Type-Options: nosniff` —— 禁止浏览器 MIME 嗅探；
- `X-Frame-Options: SAMEORIGIN` —— 防点击劫持（管理后台/日志查看器不被恶意站点 iframe 嵌套）；
- `Referrer-Policy: strict-origin-when-cross-origin` —— 限制 Referer 泄露；
- `Permissions-Policy` —— 禁用地理位置/麦克风/摄像头/支付等敏感特性；
- `X-XSS-Protection: 1; mode=block` —— 旧浏览器 XSS 兜底层。

> 注意：`add_header` 在 `location` 自带 `add_header` 时不会继承，故 `/uploads/`、`/static/` 两个 location 已各自显式补回上述响应头。

**上传文件加固（L5）**：`location /uploads/` 已限制 MIME（仅放行图片与 PDF，其余一律 `application/octet-stream`），并 `~* \.(php|pl|py|...)$` 拒绝执行任何脚本类文件，防上传文件 RCE。

**启用 HTTPS / HSTS（H3）**：模板中 `# === NGINX_SSL_443_START/END ===`（主站）与 `# === NGINX_SSL_443_LOGVIEWER_START/END ===`（日志查看器子域）两段 443 server 块，**默认注释态**（不影响 `nginx -t`）。三条启用路线**只能选一条**：

| 路线 | 适用 | 做法 |
| --- | --- | --- |
| **A. Cloudflare Origin Certificate** ★ | 域名挂在 Cloudflare 后面 | 见下方独立小节，由脚本自动完成 |
| **B. Let's Encrypt** | 无 CDN 或不想用 CF 证书 | `certbot --nginx -d <DOMAIN> --non-interactive --redirect`（**先只签主域**，理由见下） |
| **C. 手工** | 已有其它来源的证书 | 自己取消注释、改证书路径，`nginx -t && systemctl reload nginx` |

> ⚠️ **路线 B 的坑（真实 521 事故根因）**：`-d log.<DOMAIN>` 要求该子域做 HTTP-01 校验。**只要 `log.<DOMAIN>` 没有 DNS 记录，整条 certbot 命令就会中止，主域证书也拿不到、443 块一个字节都写不进去**，而报错夹在长输出里很容易被忽略。所以：**先只签主域**；确实需要两个名字时，改用路线 A（Origin Certificate 不校验 DNS）。

> ⚠️ **不要混用 A 与 B**：certbot 会自行写入它自己的 443 块，与模板渲染出的块会让同一 `server_name` 出现两个 443，nginx 报 `conflicting server name` 并只取一个。

---

### ★ 路线 A：Cloudflare Origin Certificate（完整步骤）

**为什么推荐**：① **不依赖任何 DNS 校验**（避开上面那个坑）；② 有效期最长 **15 年**，无需续期；③ 证书只被 Cloudflare 信任，即使源站 IP 泄露也无法被第三方利用。

#### 1. 在 Cloudflare 面板签发

`SSL/TLS` → `源服务器`（Origin Server）→ `创建证书`（Create Certificate）

| 项 | 填什么 |
| --- | --- |
| 私钥类型 | `RSA`（兼容性最好） |
| Hostnames | **`<域名>`、`log.<域名>` 各占一行**；或直接 `*.de5.net` 之类的通配（**一份证书覆盖主站与日志查看器，两者共用**） |
| 证书有效期 | 15 年（默认最长） |

点「创建」后**只显示一次**私钥——立刻把 `cert.pem`（源证书）与 `key.pem`（私钥）两个文件保存下来。中途关掉页面就得重新签发。

#### 2. 把证书放到服务器

```bash
# 目录名与脚本默认值一致（也可用 --origin-cert-dir 指定别的目录）
sudo install -d -m 755 /etc/ssl/cloudflare
sudo install -m 644 cert.pem /etc/ssl/cloudflare/<域名>.pem
sudo install -m 600 key.pem  /etc/ssl/cloudflare/<域名>.key
```

> 文件名必须是 `<域名>.pem` / `<域名>.key`（脚本按 `--domain` 推导）；用别的名字就配 `--ssl-cert` / `--ssl-key` 显式指定。
> 私钥 `600` 即可——nginx 由 master（root）进程读取证书，worker 不需要直接读。

#### 3. 启用（首次部署 / 升级，各一条命令）

```bash
# 首次部署
sudo bash scripts/deploy-linux.sh \
  --domain <域名> --install-dir /opt/gipfel --with-nginx --origin-cert

# 日常升级（★ 记得每次都带 --domain 与 --origin-cert）
sudo bash scripts/update-from-github.sh \
  --source-dir /opt/GipfelBusinessCompetitionManagerWeb \
  --install-dir /opt/gipfel --with-nginx --domain <域名> --origin-cert
```

脚本会：取消两段 443 块的注释 → 把 `__SSL_CERT__` / `__SSL_KEY__` 替换成实际路径 → 自检产物里确实有 `listen 443 ssl` → `nginx -t` → reload。任一环失败都会**显式报错并非零退出**，不会留下半配置状态。

> **为什么升级也必须带 `--origin-cert`**：升级会用模板产物**整体覆盖** vhost，而模板里的 443 块是注释态。不带这个开关重跑，等于把 443 块抹掉——HTTPS 静默消失。脚本已为此加了防护：若检测到现有 vhost 里**已有生效的 443** 而本次又没传 `--origin-cert`，会**直接中止并备份旧文件**，绝不静默摧毁。

#### 4. Cloudflare 面板确认 SSL/TLS 模式

`SSL/TLS` → `概述` → 加密模式选 **Full (strict)**。

| 模式 | CDN→源站 | 用 Origin Certificate 时 |
| --- | --- | --- |
| **Full (strict)** | 443 + TLS，**校验**源站证书 | ✅ **必须选这个**（Origin Certificate 正是为它设计） |
| Full | 443 + TLS，不校验 | ⚠️ 能用但浪费了证书的校验意义 |
| **Flexible** | **80 + 明文** | ❌ **绝对不要**：源站 443 白配，且与任何 80→443 跳转叠加成**重定向循环** |

#### 5. 验证

```bash
# 源站自身（绕开 CDN）
ss -lntp | grep 443
curl -skI --resolve '<域名>:443:127.0.0.1' https://<域名>/ | head -3

# 经 Cloudflare（应 200/302，不再是 521）
curl -sSI https://<域名>/ | head -3

# 日志查看器子域（应 403「拒绝直接访问」= 已到达；521/无法解析 = 未到达）
curl -sSI https://log.<域名>/ | head -3
```

用 `openssl` 看证书主体与有效期，确认是 Origin Certificate：

```bash
echo | openssl s_client -connect 127.0.0.1:443 -servername <域名> 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
# issuer 应含 "CloudFlare Origin SSL Certificate Authority"
```

#### 6. 续期 / 轮换

Origin Certificate 最长 15 年，但**若源站 IP 变更、或怀疑私钥泄露**就要换：在 CF 面板重新签发 → 覆盖两个文件 → 重跑上面第 3 步的命令（脚本会重新渲染并 reload）。**不需要 certbot，也不要 `systemctl reload` 之外的额外操作。**

#### 7. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 仍 521 | CF 模式是 Full/Full(strict) 但源站 443 没监听 | 确认第 3 步命令跑成功；`ss -lntp \| grep 443` 应有输出 |
| **525** SSL handshake failed | 源站 443 在说**明文 HTTP**（`listen 443;` 少了 `ssl`） | 检查渲染产物：`grep 'listen 443' /etc/nginx/sites-available/gipfel.conf` |
| **526** Invalid SSL certificate | CF 校验证书失败：过期 / Hostnames 不含该域名 / 只放了叶子证书 | 用第 5 步的 `openssl` 命令核对 issuer 与 dates |
| 主站正常但 `log.<域名>` 打不开 | ① 该子域**没有 DNS 记录**（Origin Certificate 不校验 DNS，所以证书装得上、但访客解析不到）；② `log.<域名>` 不在 `DJANGO_ALLOWED_HOSTS` | 补 DNS 记录；升级命令**带上 `--domain`** 让脚本自动追加（详见下文「域名形态：日志查看器打不开的三个独立原因」） |
| `nginx -t` 报证书文件不存在 | 路径/文件名与推导不符 | 配 `--ssl-cert` / `--ssl-key`，或按第 2 步的命名放好 |
| 想换回 Let's Encrypt | — | 先把两个 443 块连同证书路径改回 LE 的（模板注释态里给的就是 LE 路径写法），或删掉 vhost 里手工加的块后跑 certbot |

**Socket.IO CORS（L2）**：`backend/apps/realtime/gateway.py` 的 `cors_allowed_origins` 不再硬编码 `*`，改为复用主后端同一份 `CORS_ORIGIN` 白名单（未配置时退化为 `*`，仅限开发/私网反射），避免任意站点跨域连 WebSocket（CSWSH）。

### Cloudflare / CDN 前置（H4）

域名前面挂了 Cloudflare（或其它 CDN / 反代）时，**TLS 是两段**：

```
访客 ──TLS(CDN 的证书)──► Cloudflare ──TLS(你的证书 + nginx 443)──► nginx ──┬─http──► gunicorn:8002（/api/、/admin/）
                                                                          └─http──► daphne:8000（/socket.io/）
```

所以 HTTPS 不通时，**第一步是判断断在哪一段**。CDN 的错误码已经告诉你了——这时候去翻 nginx 日志通常什么都没有，因为请求根本没到源站。

#### 错误码对照（先看这个）

| 错误码 | 归属 | 含义 | 头号嫌疑 |
| --- | --- | --- | --- |
| **521** | Cloudflare | Web server is down —— **源站拒绝连接** | ★ **源站 443 没有监听**（最常见）；其次防火墙 / fail2ban / 安全软件**挡掉了 Cloudflare 的 IP 段** |
| 522 | Cloudflare | 连接超时 | 防火墙把包**丢掉**（DROP）；源站过载 |
| 523 | Cloudflare | 源站不可达 | 源站 IP 配错 |
| 524 | Cloudflare | 连上了但响应超时 | 后端慢或挂了（看 `systemctl status gipfel`） |
| 525 | Cloudflare | SSL 握手失败 | 源站 443 说的是**明文 HTTP**：`listen 443;` 少了 `ssl` |
| 526 | Cloudflare | 源站证书无效（仅 Full-strict） | 证书过期 / 域名不匹配 / 只装了叶子证书没带链 |

> Cloudflare 官方把 521 定义为「**源站拒绝来自 Cloudflare 的连接**」，并明确给出两条主因：
> ① **源站 Web 应用离线**；② **源站防火墙挡掉了 Cloudflare**。官方同时点名了与 TLS 模式对应的端口要求：
> **Flexible 用 80，Full / Full (strict) 用 443**——源站必须真的在这个端口上监听
> （[Error 521 官方文档](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-521/)）。
>
> ★ **一个经验性判据（非官方规范，但很好用）**：521 是「**拒绝**」，522 是「**丢包**」。
> ufw 默认策略是 DROP → 多表现为 522；而端口上没有任何进程监听时，内核回 RST/REJECT → 多表现为 521。
> 所以看到 **521，第一嫌疑就是「nginx 根本没在 443 上监听」**——这与官方「源站应用离线」的表述一致。

#### 一条命令定性

```bash
ss -lntp | grep -E ':(80|443)\b'
grep -n 'listen' /etc/nginx/sites-available/gipfel.conf
sudo certbot certificates
curl -vk https://127.0.0.1/ -H 'Host: <DOMAIN>' | head -5
```

**443 无输出 = 就是它。** 陷阱在于：`deploy/nginx-gipfel.conf` 里的 443 块**默认是注释态模板**，而 `deploy-linux.sh` **不申请证书**。所以「证书已签发」≠「nginx 已用上证书」——`certbot certonly`、或 `certbot --nginx` 中途失败，都会留下「证书在、443 没监听」这个状态。

#### 修复路线 A：让脚本启用 443（★ 挂在 Cloudflare 后面时首选）

**用 Cloudflare Origin Certificate，脚本一条命令搞定**——不依赖 DNS 校验，因此不会出现「因为 `log.<域名>` 没有记录而整条签发失败」：

```bash
sudo bash scripts/deploy-linux.sh \
  --domain <域名> --install-dir /opt/gipfel --with-nginx --origin-cert
```

完整步骤（签发、放置文件、验证、轮换）见上文 **「路线 A：Cloudflare Origin Certificate（完整步骤）」**。

#### 修复路线 A′：用 Let's Encrypt / certbot

```bash
# ★ 只签主域（最稳，不依赖任何子域 DNS）
sudo certbot --nginx -d <DOMAIN> --non-interactive --redirect

# ⚠️ 需要日志查看器子域时再加一个 -d，但该子域必须有 DNS 记录：
#    log.<DOMAIN> 解析不到会让【整条命令中止】、主域证书也拿不到、443 块写不进去
#    ——这正是「证书申请了但 HTTPS 起不来」最常见的原因
sudo certbot --nginx -d <DOMAIN> -d log.<DOMAIN> --non-interactive --redirect
```

**签发前置（经 Cloudflare 时最容易踩）**：

- 源站 **80 端口公网可达**（HTTP-01 校验走 80）；
- ★ **临时关闭 Cloudflare 的 Always Use HTTPS / 边缘跳转规则**——否则校验请求会跟着跳到尚不可用的 443，签发失败。注意这与源站自己的 `--redirect` 无关（那是签发**之后**才该生效的）。

自检（签发前跑一次）：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://<DOMAIN>/.well-known/acme-challenge/probe
# 期望 404（说明请求确实到达了 nginx），而不是 301 / 522
```

#### 修复路线 B：手工启用模板

在**渲染后的** vhost 上改，不是仓库模板：

```bash
sudo vim /etc/nginx/sites-available/gipfel.conf
#   取消 # === NGINX_SSL_443_START/END === 整段注释（主站）
#   取消 # === NGINX_SSL_443_LOGVIEWER_START/END === 整段注释（日志查看器子域）
#   把 __SSL_CERT__ / __SSL_KEY__ 换成实际证书路径
#     · Origin Certificate：/etc/ssl/cloudflare/<域名>.pem 与 .key
#     · Let's Encrypt：/etc/letsencrypt/live/<域名>/fullchain.pem 与 privkey.pem
#   需要 HSTS 再取消 Strict-Transport-Security 那行
sudo nginx -t && sudo systemctl reload nginx
```

> ★ 手工改过之后，**再跑 `--with-nginx` 的部署/升级脚本会覆盖掉这些改动**。脚本已加防护：检测到此时会**中止并备份**，不会静默抹掉。想长期稳定，建议改用 `--origin-cert` 交给脚本管理（见上文「路线 A」）。

> ⚠️ 改 `deploy/nginx-gipfel.conf` 再 reload 是**无效**的：它不参与 nginx 加载，且里面仍是 `__DOMAIN__` / `__INSTALL_DIR__` 占位符，直接使用会让 `nginx -t` 报证书文件不存在。

#### Cloudflare 的 SSL/TLS 模式必须与源站匹配

| SSL/TLS 模式 | CDN→源站 | 要求 | 结论 |
| --- | --- | --- | --- |
| **Full (strict)** | 443 + TLS，**校验**源站证书 | 源站 443 有有效证书 | ✅ **推荐**（Let's Encrypt 正好满足） |
| Full | 443 + TLS，不校验 | 源站 443 提供 TLS | ⚠️ 可用，但会掩盖源站证书错误 |
| **Flexible** | **80 + 明文** | 源站 80 提供明文 HTTP | ❌ **不要用**：与 `--redirect` 叠加 → CDN 回源 80、源站 301 到 443、CDN 再回 80 → **重定向循环** |
| Off | 仅 HTTP | — | 访客侧根本没有 HTTPS |

**更省事的替代**：不想维护 Let's Encrypt 的 90 天续期，就改用 **Cloudflare Origin Certificate**（有效期最长 15 年、只被 Cloudflare 信任），配 Full (strict)。本仓库 nginx 配置**无需改动**，只换证书路径即可。

#### 防火墙：回源 443 必须放行

`deploy-linux.sh` 与 `update-from-github.sh` 现已自动执行 `ufw allow 80/tcp` 与 `ufw allow 443/tcp`（此前脚本**只**处理日志查看器端口，从没放行过 80/443）。但**云控制台的安全组**脚本管不到，仍需手动确认入方向放行 TCP 80/443：

```bash
sudo ufw status verbose
sudo iptables -L INPUT -n --line-numbers | head -20
```

可选加固：只允许 Cloudflare 回源（防火墙白名单 Cloudflare IP 段，或启用 Authenticated Origin Pulls）。**本仓库脚本刻意不硬编码 Cloudflare IP 段**——那份列表会变，硬编码迟早过期。

> ⚠️ **反过来说**：如果源站装了 fail2ban、云 WAF 或其它安全软件，**它可能把 Cloudflare 的回源 IP 当成攻击者封掉**——
> 这是官方点名的 521 第二大成因。排查时确认没有把 [Cloudflare 的 IP 段](https://www.cloudflare.com/ips/) 拉黑：

```bash
# 若装了 fail2ban，看是否有 Cloudflare 网段被封
sudo fail2ban-client status 2>/dev/null || echo "fail2ban 未安装"
sudo iptables -S | grep -i drop | head
```

#### ★ 经 CDN 后的客户端 IP（会污染审计日志）

`deploy/nginx-gipfel.conf` 目前是 `proxy_set_header X-Real-IP $remote_addr;`。**经 Cloudflare 后 `$remote_addr` 是 Cloudflare 的 IP**，于是 `audit_log` / `http_requests` 里记录的「客户端 IP」全是 CDN 节点地址，排障与审计都会失真。

需要真实访客 IP 时，在 nginx `http` 或 `server` 块加（Cloudflare 会覆盖 `CF-Connecting-IP`，故该头可信）：

```nginx
# 只信任 Cloudflare 回源；IP 段见 https://www.cloudflare.com/ips/（会变，需定期同步）
set_real_ip_from 173.245.48.0/20;
set_real_ip_from 103.21.244.0/22;
# … 其余段按官方列表补全 …
real_ip_header CF-Connecting-IP;
real_ip_recursive on;
```

配好后 `$remote_addr` 即为真实访客 IP，现有 `proxy_set_header X-Real-IP $remote_addr;` 无需改动。**若将来撤掉 Cloudflare，这段必须一并删除**，否则任何人都能伪造 `CF-Connecting-IP` 冒充他人 IP。

#### 本仓库在这条链路上的其他注意点

| 项 | 说明 |
| --- | --- |
| `DJANGO_ALLOWED_HOSTS` | 传 `--domain` 时脚本已幂等追加域名。缺它 Django 会对公网 Host 直接返回 400（表现是「页面打不开」，但**不是** 521） |
| `X-Forwarded-Proto` | nginx 各 `proxy_set_header` 用 `$scheme` **覆盖** CDN 传来的同名头；Django 侧由 `SECURE_PROXY_SSL_HEADER` 消费（主后端与日志查看器现已一致） |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 传 `--domain` 时自动写为 `https://<DOMAIN>,http://<DOMAIN>` |
| Socket.IO / WebSocket | Cloudflare 默认支持 WebSocket 升级，无需额外开关；`location /socket.io/` 已透传 `Upgrade` / `Connection` |
| 日志查看器 `log.<DOMAIN>` | 同样需要 DNS 记录（可代理）。启用 HTTPS 后建议 `.env` 设 `LOGVIEWER_SECURE_COOKIES=true` |
| 上传体上限 | nginx 侧是 `client_max_body_size 128m`；**Cloudflare 侧另有一道更低的限制**（官方 413 文档：Free **100 MB** / Pro **100 MB** / Business 200 MB / Enterprise 最高 5 GB，且可在 zone 的 **Network → Maximum Upload Size** 调整）。所以 100–128 MB 之间的包会被 CDN 先拒（报 **413**），与 nginx 无关 |

### 更新部署（升级版本，保留数据）

**推荐：使用专用升级脚本 [`update-from-github.sh`](../scripts/update-from-github.sh)**（自动「拉取最新 + 备份 + 迁移 + 收集静态 + 前端构建 + 重启」，保留数据，语义最贴合升级）：

```bash
# 情况一：按本文档流程（clone 到独立目录 /opt/GipfelBusinessCompetitionManagerWeb，再 rsync 到 /opt/gipfel）→ 模式 B
cd /opt/GipfelBusinessCompetitionManagerWeb
sudo bash scripts/update-from-github.sh --source-dir /opt/GipfelBusinessCompetitionManagerWeb --install-dir /opt/gipfel --with-nginx
# 情况二：部署目录 /opt/gipfel 本身就是 git clone → 模式 A：sudo bash scripts/update-from-github.sh --install-dir /opt/gipfel --with-nginx
```

**纯 IP（无域名）部署更新**：省略 `--domain` 即可，脚本会自动完成与首次部署一致的纯 IP 自愈——`LOG_VIEWER_PUBLIC_URL` 纠正/补全、`DJANGO_ALLOWED_HOSTS` 幂等追加公网 IP（此前升级脚本缺这项，纯 IP 实例升级后可能复发登录 400，现已补齐）。受限网络探测不到公网 IP 时显式传 `--public-ip`：

```bash
sudo bash scripts/update-from-github.sh \
  --source-dir /opt/GipfelBusinessCompetitionManagerWeb \
  --install-dir /opt/gipfel --with-nginx --public-ip 43.142.77.225
```

脚本自动：
1) 拉取最新代码 2) 备份 `db.sqlite3`+`uploads`+`.env` 到 `/opt/gipfel/_backup/$(date +%F_%H%M%S)`（数据库用 `VACUUM INTO` 一致性快照，WAL 安全）3) 更新代码（排除数据文件）4) `pip install -r requirements.txt`（如有新依赖）5) `migrate`（种子幂等）+ `collectstatic`（主后端 + 日志查看器静态资源）6) `npm ci && npm run build` → `frontend-dist/` 7) 纯 IP 自愈（`LOG_VIEWER_PUBLIC_URL` + `DJANGO_ALLOWED_HOSTS`，改写后恢复 `.env` 属主 gipfel 与 600 权限）8) 刷新 systemd 单元（最新 `deploy/*.service` 重新落地，含 C1-a 的 `gipfel-wsgi.service`）+ `systemctl restart gipfel` `gipfel-wsgi`（+ `gipfel-logviewer`）9) [--with-nginx] 刷新 vhost 并 reload（含默认站点清理、80 端口校验、**80/443 与 8120 防火墙放行**）。

> ## ⚠️ 域名部署升级时**必须**带上 `--domain`
>
> 上面「情况一/二」的示例**没有** `--domain`，那是**纯 IP 部署**的写法。域名部署照抄会在两处出错：
>
> | 漏传 `--domain` 的后果 | 说明 |
> | --- | --- |
> | ★ **脚本中途静默终止** | 脚本会走进「无域名」分支去读 `.env` 的 `LOG_VIEWER_PUBLIC_URL`；而域名部署（`deploy-linux.sh --domain`）**从不写这一项** → `grep` 无匹配 → `set -o pipefail` 下整条管道失败 → 该行是变量赋值 → `set -e` 直接终止，**且不打印任何东西**。现象是「脚本跑到『文件归属已切换』那一步就没了」。已在脚本内修掉（容忍无匹配）并加装 ERR trap 报出终止行号 |
> | ★★ **把域名 vhost 改写回纯 IP 形态** | 若同时传了 `--with-nginx`：脚本按「无域名」重新渲染 vhost —— `server_name` 变 `_`、**日志查看器子域块被删除**、改回 8120 端口块。域名与 `log.<域名>` 随即失效，且**没有报错**。这比崩溃更危险 |
>
> 正确写法（**每次升级都带**）：
>
> ```bash
> sudo bash scripts/update-from-github.sh \
>   --source-dir /opt/GipfelBusinessCompetitionManagerWeb \
>   --install-dir /opt/gipfel --with-nginx --domain <你的域名>
> ```
>
> `--domain` 不只影响 nginx：它还决定 ① `.env` 里 `DJANGO_ALLOWED_HOSTS` 追加 `<域名>` 与 `log.<域名>`（日志查看器能通过 Host 白名单的前提）；② 日志查看器走 `log.<域名>` 子域而不是 `<IP>:8120`。详见 `--help`。

> **等价做法（仍可用）**：重跑部署脚本（需先从 clone 目录 pull 代码）：
> ```bash
> cd /opt/GipfelBusinessCompetitionManagerWeb
> sudo scripts/deploy-linux.sh --domain comp.example.com --install-dir /opt/gipfel --with-nginx --skip-install-deps
> ```
> `deploy-linux.sh` 同样会备份/恢复数据并 restart；`--skip-install-deps` 跳过 apt 装包。两种路径效果一致，`update-from-github.sh` 更契合「拉取最新 + 升级」语义，建议优先使用。

### 回滚

> **先看清楚**：`/opt/gipfel/_backup/<时间戳>/` 只含**数据**（`db.sqlite3`、`uploads/`、`.env`），
> **不含代码**。只恢复数据库不恢复代码，会得到"新代码 + 旧库"或"旧库 + 新代码"的版本错配 ——
> 尤其是已经跑过 `migrate` 的库，旧代码不一定认它的表结构。所以回滚要**数据与代码一起**做。
>
> ⚠️ **WAL 前提（C2 阶段 1 起 SQLite 默认 `journal_mode=WAL`）**：WAL 模式下最近的事务可能还躺在
> `db.sqlite3-wal` 里，而 `shm` 是它的索引。所以 ①**备份活库不能用 `cp`**（会漏掉 -wal 里的事务，
> 甚至拿到页不一致的文件）—— 必须是停服后拷贝，或用 `VACUUM INTO` 导出；②**恢复前必须先删掉目标机上
> 残留的 `-wal`/`-shm`** —— 否则旧库的 WAL 会被 SQLite 当成新库的未提交事务**重放**，得到一个
> "表结构是旧的、数据却混着新事务"的库（文件头校验、非空校验都查不出来）。

```bash
set -e
BK=/opt/gipfel/_backup/2025-08-31_1200          # 换成实际要回滚到的时间戳目录（ls /opt/gipfel/_backup/）
cd /opt/GipfelBusinessCompetitionManagerWeb      # 你的 clone 目录

# 0) 先记录当前版本，并把"现在"再备份一份（回滚本身也可能出错）
sudo git -C /opt/GipfelBusinessCompetitionManagerWeb rev-parse HEAD | tee /tmp/gipfel_rollback_from.txt
#    ★ WAL 前提：对**运行中**的库直接 `cp` 会漏掉还在 -wal 里的事务，甚至拿到页不一致的文件。
#      正确顺序是「先停服 → 再用 SQLite 自己导出自洽副本（VACUUM INTO，等价脚本里的
#      snapshot_sqlite_consistent）」。所以这里把"停服"和"备份"合并成一步：
sudo systemctl stop gipfel gipfel-wsgi gipfel-logviewer
sudo rm -f /opt/gipfel/_backup/db.sqlite3.before-rollback.sqlite3     # VACUUM INTO 要求目标不存在
sudo -u gipfel /opt/gipfel/backend/.venv/bin/python - <<'PY'
import sqlite3
con = sqlite3.connect("file:/opt/gipfel/backend/db.sqlite3?mode=ro", uri=True)
con.execute("VACUUM INTO '/opt/gipfel/_backup/db.sqlite3.before-rollback.sqlite3'")
con.close()
print("已导出自洽副本（不含 -wal，可直接落位）: /opt/gipfel/_backup/db.sqlite3.before-rollback.sqlite3")
PY

# 1) 服务已在第 0 步停掉（gipfel / gipfel-wsgi / gipfel-logviewer 三个进程都在写这个库）
#    确认一下再继续：
systemctl is-active gipfel gipfel-wsgi gipfel-logviewer || true

# 2) 代码回退到上一个可用 tag/commit（不知道退到哪就用 git log --oneline 挑）
sudo git -C /opt/GipfelBusinessCompetitionManagerWeb checkout <上一个 tag 或 commit>

# 3) 恢复数据
#    ★★ 必须先删掉 WAL/SHM 残留：旧库留下的 db.sqlite3-wal 会被当成"新库的未提交事务"重放，
#       把恢复出来的库带偏（症状是数据对不上/库损坏，而文件头与非空校验全都正常）。
sudo rm -f /opt/gipfel/backend/db.sqlite3-wal /opt/gipfel/backend/db.sqlite3-shm
#    注：$BK/db.sqlite3 是脚本用 snapshot_sqlite_consistent（VACUUM INTO）产出的自洽副本，
#        不带 -wal，停服后直接落位即可；若这份副本是手工 cp 的活库，先按第 0 步的方式重导一次。
sudo -u gipfel cp "$BK/db.sqlite3" /opt/gipfel/backend/db.sqlite3
[ -d "$BK/uploads" ] && sudo -u gipfel cp -a "$BK/uploads/." /opt/gipfel/backend/uploads/
[ -f "$BK/.env" ]    && sudo -u gipfel cp "$BK/.env" /opt/gipfel/backend/.env
sudo chown gipfel:gipfel /opt/gipfel/backend/db.sqlite3 /opt/gipfel/backend/.env
sudo chmod 600 /opt/gipfel/backend/.env

# 4) 按回退后的代码重装依赖、重建前端、回退数据库结构
sudo -u gipfel /opt/gipfel/backend/.venv/bin/pip install -r /opt/gipfel/backend/requirements.txt
cd /opt/GipfelBusinessCompetitionManagerWeb/frontend && sudo npm ci && sudo npm run build
# 若本次升级引入过新迁移，需要退回到旧迁移点（<app> 与迁移名取自 git show <旧commit>:backend/apps/<app>/migrations/）：
#   sudo -u gipfel /opt/gipfel/backend/.venv/bin/python /opt/gipfel/backend/manage.py migrate <app> <上一个迁移名>

# 5) 起服务并确认（回退到 C1-a 之前时，gipfel-wsgi 不存在是正常的，见「C1-a 双进程部署 → 回退」）
sudo systemctl start gipfel gipfel-wsgi gipfel-logviewer
curl -fsS --max-time 5 http://127.0.0.1/api/health && echo " 后端 OK"
curl -fsS --max-time 5 http://127.0.0.1:8002/api/health && echo " WSGI OK"
systemctl is-active gipfel gipfel-wsgi gipfel-logviewer
```

> 更稳的做法：不要在服务器上手工挑文件回滚，而是 `git revert` 出问题的那次改动并**重新跑一遍**
> `scripts/update-from-github.sh`（数据仍由 `_backup` 兜底）。`_backup/*` 只作最后手段。

---

## 方案二：Windows（仅开发，无生产部署脚本）

Windows 不提供独立生产部署脚本；生产部署请使用方案一的 Linux 脚本 [deploy-linux.sh](../scripts/deploy-linux.sh)。

开发启动器 [scripts/start-dev.bat](../scripts/start-dev.bat)（校验前置条件后交给监管进程 [scripts/dev.py](../scripts/dev.py)）会并行拉起：

- Django `:8000`（runserver）
- Vite `:5173`（前端开发服务器）
- 日志查看器：开发态 Windows `http://127.0.0.1:8120/`（daphne 绑 8120）、生产 `http://<IP>:8120/` 或 `https://log.<DOMAIN>/`（daphne 绑内网 8121，nginx 公网监听 `.env` 的 `LOG_VIEWER_PORT` 默认 8120 → 反代 8121；改 `LOG_VIEWER_PORT` 改的是 nginx 公网端口与防火墙放行，daphne 内部 8121 不变），登录账号使用 Django 后台超级管理员凭据

---

## 方案三：Docker（快速上手）

```dockerfile
# 示例 Dockerfile 片段：多阶段 build 前端 + 单镜像跑 daphne
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./
RUN python manage.py collectstatic --noinput 2>/dev/null || true
COPY --from=frontend /app/frontend/dist /app/frontend-dist
EXPOSE 8000
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "--proxy-headers", "backend.asgi:application"]
```

挂载：
```
-v ./data/db.sqlite3:/app/backend/db.sqlite3
-v ./data/uploads:/app/backend/uploads
-v ./data/logs:/app/backend/logs
-e JWT_SECRET=CHANGE-ME
```

---

## 服务器迁移

需要将服务从一台服务器迁移到另一台？项目提供了专用迁移脚本：

```bash
# 在旧服务器执行（推送到新服务器）
sudo bash scripts/migrate-server.sh --mode push --target root@新服务器IP --install-dir /opt/gipfel

# 在新服务器执行（从旧服务器拉取）
sudo bash scripts/migrate-server.sh --mode pull --source root@旧服务器IP --install-dir /opt/gipfel
```

**迁移内容**：数据库 (`db.sqlite3`)、用户上传 (`uploads/`)、环境配置 (`.env`)、日志、静态资源、前端构建产物。

**快速数据同步**（仅同步数据，不含代码）：
```bash
bash scripts/quick-sync.sh push root@新服务器IP    # 推送
bash scripts/quick-sync.sh pull root@旧服务器IP    # 拉取
```

**迁移验证**：
```bash
bash scripts/verify-migration.sh /opt/gipfel
```

详细文档见 [**服务器迁移指南**](../docs/MIGRATION.md)。
