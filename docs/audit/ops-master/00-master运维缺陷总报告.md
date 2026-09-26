# master 分支运维层面缺陷审计 · 总报告

- **审计对象**：`master@97e117e8781e0c02462a24269a165dde08ecef44`（下称 master）
- **审计性质**：**只读**。全程未修改任何代码、脚本、配置或模板；未执行部署脚本、未改本机 systemd/nginx/防火墙/数据库。
- **组织方式**：Lead + 4 名专职分析员按域并行（发布流水线 / 服务编排 / 网络边界 / 数据容灾），Lead 另审配置密钥与可观测性域并逐条复核各域结论。
- **总计**：**37 条**（P0 × 0 ｜ P1 × 10 ｜ P2 × 17 ｜ P3 × 10）

---

## 0. 一句话结论

master 的运维风险**不在首次部署**（首次部署路径的工程质量明显高于既有审计的描述：参数清洗、探测超时包裹、masked 自愈、`.env` 权限收紧都做了），而集中在四个地方：**①「重跑部署/升级」会报告成功但发布并未生效**（服务不重启、失败仍 `exit 0`）；**② 默认部署产物是全站明文 HTTP，且主后端代码层面永不认 TLS**；**③ 唯一回滚点（`_backup` 活库 `cp` + 定时备份）在一致性与保留策略上都不可靠，`quick-sync.sh` 更是把 uploads 同步到了错误目录**；**④ 有域名形态的日志查看器入口 100% 返回 400，而恰好没有任何自动化能发现**（无 CI、健康检查是纯 liveness、诊断脚本绕过 nginx）。

---

## 1. 审计范围与方法

### 1.1 范围
`deploy/`（2 个 systemd 单元 + nginx 模板 + 部署手册）、`scripts/`（8 个部署/迁移/引导脚本）、`docs/OPS.md`、`docs/MIGRATION.md`、`backend/backend/settings.py`、`backend/logviewer/logviewer/settings.py`、`backend/.env.example`、`backend/apps/{auth,common,realtime,company_fields,competitions,audit,files,messages}` 中与运行时/部署强相关的实现、`tests/` 中 2 个运维诊断与部署测试脚本、`README.md`、`VERSION.json`、`frontend/package.json`、`.gitignore`。

### 1.2 证据基线（含一次方法学修正）
用 `git archive master` 在 Windows 上导出会因 `core.autocrlf` 把**所有文本文件转成 CRLF**，与 master blob 不符（同一文件 `deploy-linux.sh` 导出件含 652 个 CR 字节，`git cat-file blob` 实测为 0）。若直接据此审计，会得出「master 的 shell 脚本是 CRLF、在 Linux 上无法执行」这一**伪 P0**。
本报告全部证据改用 `git -c core.autocrlf=false -c core.eol=lf archive` 导出的**逐字节快照**（439 文件，CR=0），并以 `read`/`grep`/`git show master:<path>` 三方交叉核对每条 `file:line`。**结论：master 的 `.sh`/`.service`/`.conf` 全部是 LF，不存在换行符缺陷。**

### 1.3 复核标记
| 标记 | 含义 |
| --- | --- |
| ✅ | Lead 已用独立只读命令复跑并确认（本报告全部 P1 均为此类） |
| ◐ | 分析员报告 + 报告内附只读复核命令，Lead 未逐条复跑（机制性/文档性结论） |
| ⚠ | 依赖真实 Linux/systemd/nginx/压测环境，已标注「推断」，报告内给出目标机复核命令 |

### 1.4 去重与评级校准
- 既有审计 `code_audit/U14-scripts-deploy-tests.md`（X-01…X-29）已覆盖脚本代码层面的部分问题；本报告对重叠项**均独立复核后标注**（见第 8 节），不重复计数，只保留新增信息。
- 与子报告评级不一致处已校准并在条目内说明理由（共 1 处：财年定时器 P1→P2）。

### 1.5 无代码改动声明
`git status --porcelain` 在审计前后**完全一致**（既有 `M contract_watcher/bookkeeping_example/shang.py`、`M frontend/package-lock.json` 为审计开始前就存在的工作区改动，非本次产生）。本次新增文件仅 `code_audit/ops-master/*.md`（Markdown 报告，位于审计前就已 untracked 的 `code_audit/` 目录内）。

---

## 2. 结论总览

| 域 | 报告 | P1 | P2 | P3 | 合计 |
| --- | --- | --- | --- | --- | --- |
| O1 部署与发布流水线 | [O1-发布与部署流水线.md](O1-发布与部署流水线.md) | 5 | 7 | 2 | 14 |
| O2 服务编排与运行时 | [O2-服务编排与运行时.md](O2-服务编排与运行时.md) | 4 | 6 | 2 | 12 |
| O3 反向代理与网络边界 | [O3-反向代理与网络边界.md](O3-反向代理与网络边界.md) | 3 | 7 | 3 | 13 |
| O4 数据、备份与容灾 | [O4-数据备份与容灾.md](O4-数据备份与容灾.md) | 4 | 5 | 2 | 11 |
| O5 配置/密钥/可观测性 | [O5-配置密钥与可观测性.md](O5-配置密钥与可观测性.md) | 0 | 8 | 3 | 11 |
| **去重合并后** | **本报告** | **10** | **17** | **10** | **37** |

按**默认部署形态**（`sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx`，有域名/纯 IP 两种）的命中情况：

| 形态 | 必中 | 说明 |
| --- | --- | --- |
| 有域名 | P1-3、P1-4、P1-5、P1-6、P1-7、P1-8、P2-1… | 443 未启用 → 明文；日志查看器 400；Host 白名单缺子域 |
| 纯 IP | P1-3、P1-4、P1-6、P1-7、P1-8、P2-3、P2-4… | 明文；CORS 未配置 → Socket.IO 任意源；ufw 只放行 8120 |
| 重跑部署 / 升级 | P1-1、P1-2、P1-10、P2-7、P2-8、P2-9 | 发布不生效、备份不可靠、失败仍成功 |

---

## 3. P1 缺陷（10 条 · 全部经 Lead 独立复核）

### P1-1 ✅ 重跑 `deploy-linux.sh` 不会重启服务：新代码、新库结构、新前端全部落盘，线上进程仍是旧代码，脚本仍打印「运行中 / 部署完成」

- **位置**：`scripts/deploy-linux.sh:472`、`:483`（`systemctl enable --now gipfel` / `gipfel-logviewer`）
- **证据**：全文 `systemctl` 只出现 `unmask`(:451)、`daemon-reload`(:470)、`enable --now`(:472/:483)、`is-active`(:474/:478/:485/:489)、`enable nginx`(:561)、`reload nginx`(:563)、`start nginx`(:566)——**没有任何 `systemctl stop|restart`**。而 `deploy/README.md:230` 明确承诺「`deploy-linux.sh` 同样会备份/恢复数据并 restart……两种路径效果一致」（真正 restart 的是 `update-from-github.sh:441`/`:457`）。
- **运维影响**：`enable --now` 对已 active 的单元是 no-op。按文档给出的「等价做法：重跑部署脚本」（`deploy/README.md:225-230`）操作时：代码已换、`migrate` 已把库结构前滚、`collectstatic`/`npm run build` 已替换 `frontend-dist`，**但 daphne 仍跑旧 Python 代码** → 新前端 × 新库结构 × 旧后端的三方错配，表现为随机 500/接口契约不符；脚本随后打印 `gipfel.service 运行中` 与 `部署完成！`(:606)，运维没有任何信号需要重启。修复动作（手工 `systemctl restart`）不在任何文档步骤里。
- **命中形态**：重跑部署（升级语义）**必中**；首次部署不中。
- **复核命令**：
  ```bash
  git show master:scripts/deploy-linux.sh | grep -n "systemctl"
  git show master:deploy/README.md | sed -n '225,230p'
  ```

### P1-2 ✅ 唯一回滚点不可靠：运行中活库 `cp -a` 备份 + `_backup` 无保留策略 + 回滚只恢复数据库

- **位置**：`scripts/deploy-linux.sh:175`（`cp -a db.sqlite3 "$BACKUP_DIR/" 2>/dev/null || true`）、`scripts/update-from-github.sh:192`、`docs/MIGRATION.md:500-515`（推荐「每天 3 点」的定时备份同样是活库裸 `cp`）、`deploy/README.md:234-238`（回滚）、`scripts/deploy-linux.sh:80` / `update-from-github.sh:189`（`_backup/<时间戳>`）
- **证据**：两脚本**全文无 `systemctl stop`**（服务 `Restart=always`，始终运行）；备份不取 `db.sqlite3-journal`/`-wal`，不使用 SQLite 一致性备份接口（`.backup`/`VACUUM INTO`），无完整性校验；`_backup` 目录无 `prune`/`find -mtime`/容量上限（全仓 `grep prune|df -h|du -sh` 0 命中，`.gitignore` 也未忽略 `_backup/`）；回滚章节只 `cp` 回 `db.sqlite3` 再 `restart gipfel`，不恢复 `uploads/`/`.env`/代码，也不重启共用该库的 `gipfel-logviewer`。
- **运维影响**：① 备份的**可恢复性没有任何保证**（SQLite 在并发写下的裸文件复制可能页撕裂或缺少未提交日志），而它正是文档给出的唯一回滚依据——「回滚成功」可能只是把损坏的库覆盖回生产；② 每次重跑部署都新增一份 `_backup` 且永不清理，长期把**生产与全部备份放在同一块盘**（磁盘故障时一起消失），盘满后 `cp` 失败被 `|| true` 吞掉、`migrate` 报 `SQLITE_FULL`、业务写 500，而备份目录仍然「看起来存在」；③ 回滚后 `uploads/` 与 `.env` 仍是被替换后的新版本，`gipfel-logviewer` 继续持有旧库句柄——单文件回滚不构成一次可用回滚。
- **命中形态**：每次部署/升级/迁移都命中；事故恢复时后果最大。
- **复核命令**：
  ```bash
  git show master:scripts/deploy-linux.sh | sed -n '80p;175,177p;202p'
  git show master:docs/MIGRATION.md | sed -n '500,516p'
  git show master:deploy/README.md | sed -n '234,238p'
  git grep -n "prune\|df -h\|VACUUM INTO" master -- scripts/    # 无输出
  ```

### P1-3 ✅ 默认 `--with-nginx` 产物是全站明文 HTTP：登录口令、JWT、后台与日志查看器凭据全程可嗅探

- **位置**：`deploy/nginx-gipfel.conf:16-17`（仅 `listen 80` / `listen [::]:80`）、`:293`（`# HTTPS / HSTS 模板（H3）：默认【注释态】`）、`:303-392`（整段 443 配置被注释）、`scripts/deploy-linux.sh:583-588`（certbot 只 `warn` 提示，不安装不执行）
- **证据**：唯一的 443 server 块与 HTTP→HTTPS 跳转都处于注释态；部署脚本从不安装/执行 certbot，无域名形态连提示都没有。而 `deploy/README.md:108` 的验证步骤写「浏览器打开 https://comp.example.com」，同段第 107 行却是 `curl -sS -I http://127.0.0.1/`。
- **运维影响**：按文档完成部署后（certbot 被列为「可选/推荐」而非必需），明文链路上传输：登录 POST 的明文口令、前端 `localStorage` 中的 JWT（每个请求的 `Authorization` 头）、Django `/admin` 会话 cookie、日志查看器的超级管理员凭据。同 WiFi、同云内网/VPC 流量镜像即可直接取得后台凭据——而 `docs/OPS.md:232` 明确警告「后台直写 SQLite 会绕过业务校验」。同时按 `deploy/README.md:108` 打开 https 会直接失败，首次验收即被误导为「部署失败」。
- **命中形态**：有域名（未跑 certbot = 默认）+ 纯 IP（无法申请证书）**两种默认形态全中**。
- **复核命令**：
  ```bash
  git show master:deploy/nginx-gipfel.conf | grep -n "listen 80\|listen \[::\]:80\|NGINX_SSL_443_START\|listen 443"
  git show master:scripts/deploy-linux.sh | sed -n '583,590p'
  ```

### P1-4 ✅ 主后端代码层面永不认 TLS：缺 `SECURE_PROXY_SSL_HEADER` 与全部 `*_COOKIE_SECURE`，即使跑完 certbot 后台会话 cookie 仍无 `Secure`

- **位置**：`backend/backend/settings.py:461-463`（该文件最后三行仅 `SESSION_COOKIE_HTTPONLY=True`、`CSRF_COOKIE_HTTPONLY=True`）；对照 `backend/logviewer/logviewer/settings.py:53`（`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO","https")`）、`:64`（`SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = …`）；`deploy/nginx-gipfel.conf:53` 已传 `X-Forwarded-Proto $scheme`
- **证据**：`git grep -n "SECURE_" master -- backend/backend/settings.py` **无输出**；同仓的日志查看器却完整具备。nginx 已把真实 scheme 传给后端，主后端把它当普通 META 丢弃。
- **运维影响**：nginx 终止 TLS 后主后端内 `request.is_secure()` 恒 `False`、`request.scheme` 恒 `http`：① `/admin` 的 `sessionid`/`csrftoken` 永不带 `Secure`——即运维按文档完成 HTTPS 加固后，用户任何一次 `http://` 访问（旧书签、外链、地址栏补全）浏览器都会**先**把后台会话 cookie 明文发出，服务器才 301；局域网嗅探即可重放该 cookie 登录后台；② Django 侧无 HSTS、无 SSL 重定向、无 `SECURE_CONTENT_TYPE_NOSNIFF`；③ 任何以 scheme 为条件的逻辑（当前是 `apps/auth/views.py:101-113` 的地址推导，见 P2-3）在 HTTPS 下判断错误；④ 这类缺失无法用 nginx 配置弥补，必须改代码，而仓库没有任何 `manage.py check --deploy` 执行点（见 P2-6）。
- **命中形态**：启用 HTTPS 后**仍命中**（这才是要害：加固动作做完，安全边界依然不成立）；纯 HTTP 形态叠加 P1-3。
- **复核命令**：
  ```bash
  git grep -n "SECURE_" master -- backend/backend/settings.py      # 无输出
  git show master:backend/logviewer/logviewer/settings.py | sed -n '53p;64p'
  ```

### P1-5 ✅ 有域名形态下日志查看器入口 100% 返回 400：`log.<DOMAIN>` 从未进入任何 Host 白名单

- **位置**：`backend/logviewer/logviewer/settings.py:78-106`（白名单三来源：回环 / `LOG_VIEWER_PUBLIC_URL` 的 hostname / `DJANGO_ALLOWED_HOSTS`，且 `:67` 明确「不再通配 `"*"`」）、`scripts/deploy-linux.sh:309`（`if [[ -z "$DOMAIN" && -f … ]]` —— `LOG_VIEWER_PUBLIC_URL` 的维护被 `-z "$DOMAIN"` 挡住）、`:365-366`（`AH_ENTRY="$DOMAIN"` —— 只写主域）、`deploy/nginx-gipfel.conf:220-223`（子域块透传 `Host: $host:$server_port`）
- **证据**：全仓库**没有任何一处**把 `log.$DOMAIN` 写进 `LOG_VIEWER_PUBLIC_URL` / `DJANGO_ALLOWED_HOSTS` / `LOGVIEWER_ALLOWED_HOSTS`（`git grep -n "log\.\$DOMAIN"` 只命中 certbot/DNS 提示文本）。因此有域名部署时白名单 = `{127.0.0.1, localhost, ::1, <DOMAIN>}`，访问 `log.<DOMAIN>` 时 Django `get_host()` 得到 `log.<DOMAIN>` → `DisallowedHost` → 原生 400。`deploy/README.md:120-123` 的「有域名前置条件」只列了 DNS + certbot + `LOGVIEWER_SECURE_COOKIES`，完全没有这一条；`docs/OPS.md:171-191`（Q7c）的三来源兜底叙述只覆盖纯 IP 场景。
- **运维影响**：文档主推的「系统设置 → 日志查看器」入口在有域名形态下**整体不可用**（400 HTML，前端表现为异常），而运维按 FAQ 排查会被引向「8120 端口块」（域名形态根本不用 8120）与「令牌/密钥不一致」，方向全错。即便运维为它补 DNS + certbot，仍然 400。
- **命中形态**：`--domain` 形态**必中**（与是否启用 HTTPS 无关）；纯 IP 形态不中。
- **复核命令**：
  ```bash
  git show master:backend/logviewer/logviewer/settings.py | sed -n '66,106p'
  git show master:scripts/deploy-linux.sh | sed -n '309p;365,366p'
  git grep -n "logviewer.*ALLOWED\|LOGVIEWER_ALLOWED_HOSTS" master -- scripts/   # 脚本从不写
  ```

### P1-6 ✅ systemd「文件系统只读」不成立：`ProtectSystem=full` 不覆盖 `/opt`，且部署脚本把整棵安装树（含源码/`.venv`/`.env`/备份）交给运行账户

- **位置**：`deploy/gipfel.service:37`（注释声称「最小权限 + 文件系统只读 + 仅开放必要可写路径」）、`:40`（`ProtectSystem=full`）、`:52`（`ReadWritePaths=…`）；`deploy/logviewer.service:39/42/53` 同构；`scripts/deploy-linux.sh:437`（`chown -R gipfel:gipfel "$INSTALL_DIR"`）、`scripts/update-from-github.sh:261`
- **证据**：`ProtectSystem=full` 只把 `/usr`、`/boot`、`/efi`、`/etc` 置为只读；**`/opt` 不在其中**（只有 `ProtectSystem=strict` 才是「整个文件系统只读」）。因此 `ReadWritePaths` 里列出的 4 条路径并非「仅开放的必要可写路径」，其余 `/opt/gipfel` 下的**源码、`.venv`、`.env`、`frontend-dist`、`_backup`** 对服务用户 `gipfel` 全部可写；两个服务共用同一 `User=gipfel`。而 `deploy/nginx-gipfel.conf:37` 的 `root __INSTALL_DIR__/frontend-dist` 说明 `frontend-dist` 正是 nginx 直接托管给全体浏览器的目录；`deploy-linux.sh:404/410` 表明 root 下次部署会执行安装树内的文件（`.venv/bin/pip`、`manage.py`）。
- **运维影响**：① 应用一旦被攻破（该应用有文件上传与公式/图编辑器），攻击者可直接改写 `frontend-dist` 中的 JS → 对所有浏览器生效的持久化 XSS，且不影响任何服务状态、无痕；② 可读写或删除 `_backup`（唯一回滚点）与 `.env`（全部密钥）；③ `chown -R` 把 `.venv`、`manage.py`、`deploy/*.service` 模板交给运行账户，而 root 后续会执行/安装这些文件 → 本地提权链。加固注释与实际隔离能力相反，会让运维误以为已满足最小权限。
- **命中形态**：所有部署形态（属单元文件固有配置）。
- **复核命令**：
  ```bash
  git show master:deploy/gipfel.service | sed -n '37,52p'
  git show master:scripts/deploy-linux.sh | sed -n '437,439p'
  # 目标机只读验证：systemd-analyze security gipfel.service
  ```

### P1-7 ◐ 崩溃循环永远不会变成 `failed`：`Restart=always` + `RestartSec=3s` 使 systemd 默认启动限流不可达，无 `OnFailure` 告警

- **位置**：`deploy/gipfel.service:32-33`、`deploy/logviewer.service:34-35`（`Restart=always` / `RestartSec=3s`；两个单元均未设 `StartLimitIntervalSec`/`StartLimitBurst`）
- **证据**：systemd 默认 `DefaultStartLimitIntervalSec=10s`、`DefaultStartLimitBurst=5`。以 `RestartSec=3s` 重启，5 次启动需约 12s > 10s，滑动窗口每次都已滑出 → **限流永不触发**。触发条件真实存在：`EnvironmentFile=-…`（`gipfel.service:15`，前置 `-` 表示可选）缺失或 `.env` 被改坏时，`settings.py:23-28` 的 `JWT_SECRET` 校验会 `raise RuntimeError`；`logviewer/settings.py:29-36` 的 `LOGVIEWER_SECRET_KEY` 校验同理。更常见的是 `.env` 中 `PORT`/`LOG_VIEWER_PORT`/`BACKEND_GATE_MAX_AGE` 的非数字值（`settings.py:51/140/143` 裸 `int()`）。
- **运维影响**：配置写错后服务进入**无限 3 秒重启循环**：单元始终显示 `activating`/`active`（永不 `failed`），`systemctl is-failed` 与基于它的巡检/告警全部静默，`deploy-linux.sh:474-480` 的 `is-active` 活性检查也会打印「gipfel.service 运行中」；外网表现只是持续的 502；`journalctl -u gipfel` 每天新增约数万行。故障发现完全依赖用户报障。
- **复核命令**：
  ```bash
  git show master:deploy/gipfel.service | sed -n '15p;32,33p'
  git grep -n "StartLimit" master -- deploy/          # 无输出
  # 目标机只读验证：systemctl show -p DefaultStartLimitIntervalUSec,DefaultStartLimitBurst
  ```
- **标注**：仓库侧事实（无 StartLimit 覆盖、`RestartSec=3s`）✅ 已验证；限流不触发的运行时行为属 ⚠ 推断（依赖 systemd 上游默认值语义）。

### P1-8 ✅ 两条 daphne access log 零轮转，且 daphne 持有 fd 使事后 `logrotate` rename 也无效 → 磁盘写满

- **位置**：`deploy/gipfel.service:22`（`--access-log /var/log/gipfel/access.log`）、`deploy/logviewer.service:24`（`--access-log /var/log/gipfel/logviewer.access.log`）、`deploy/gipfel.service:29-30`（`LogsDirectory=gipfel`）、`docs/OPS.md:91`（只承诺 `backend/logs/gipfel.log` 按天滚动保留 14 天）
- **证据**：仓库内**不存在任何 logrotate 配置**（`git ls-tree -r master` 中无 `logrotate*`，`git grep -n logrotate` 0 命中）；Python 侧的 `TimedRotatingFileHandler`（`settings.py:432-440`，`backupCount=14`）只覆盖 `gipfel.log`，与 daphne 的 access log 是两个文件。daphne 在启动时 open 一次 access log 并长期持有 fd。
- **运维影响**：`/var/log/gipfel/access.log` 与 `logviewer.access.log` 无上限增长（每次 API 调用、Socket.IO 轮询、静态请求都记一条），最终写满根分区；SQLite 写失败后业务 500、`_backup` 的 `cp` 也失败（见 P1-2），而系统没有任何磁盘监控或告警。事后补 `logrotate` 若只做 rename 不 `copytruncate`，daphne 会继续写已改名的旧 inode，新文件恒为空、旧文件继续涨——即「加了轮转也没生效」。附带：access log 记录请求完整 URI（含 query），而 `/admin/?token=…` 与 `log.<DOMAIN>/?token=…` 的**一次性网关令牌在 URL 里**；配合 P2-10（无 `UMask`、目录 0755）该文件默认可被同机其他用户读取。
- **命中形态**：所有部署形态，随时间必然发生。
- **复核命令**：
  ```bash
  git show master:deploy/gipfel.service | sed -n '22p;29,30p'
  git ls-tree -r master --name-only | grep -i logrotate     # 无输出
  git show master:backend/backend/settings.py | sed -n '432,440p'
  ```

### P1-9 ✅ `quick-sync.sh` 把 uploads/logs 同步到了错误目录：打印「同步完成」，目标机 `uploads/` 零更新

- **位置**：`scripts/quick-sync.sh:44-49`（`SYNC_ITEMS` 中 `"backend/uploads/"`、`"backend/logs/"` **带尾斜杠**）、`:59`（`dst="$INSTALL_DIR/$(dirname "$item")/"`）、`:64`（push）、`:71`（pull）
- **证据**：`dirname "backend/uploads/"` 求值为 `backend`（尾斜杠被剥离），于是 `dst=$INSTALL_DIR/backend/`；而 rsync 源 `"$INSTALL_DIR/backend/uploads/"` 带尾斜杠表示「复制目录内容」→ **uploads 内的文件被摊平写进远端 `backend/`，目标机的 `backend/uploads/` 一个字节都没更新**。pull 方向同理：`rsync "$REMOTE:$src" "$(dirname "$src")/"` → `dirname` 再次剥离尾斜杠，远端 uploads 内容落进本地 `backend/`。`db.sqlite3`/`.env`（无尾斜杠）不受影响，所以脚本表面上「同步成功」。
- **运维影响**：文档把它宣传为「快速数据同步（仅同步数据，不含代码）」（`deploy/README.md:301-305`）。真实结果是**数据库更新而上传文件不更新**：迁移到新机后所有 `/uploads/` 资源 404（地图背景图、消息图片、导入附件），公司/比赛数据却看起来正常，极易误判为前端或 nginx 配置问题；一旦源机下线/重装，上传件**永久丢失**（`_backup` 里虽有一份活库 `cp` 的 uploads，但同样受 P1-2 的保留与一致性影响）。同时被污染的远端 `backend/` 目录里会混入用户上传件。
- **命中形态**：任何使用 `quick-sync.sh push|pull` 的迁移/应急路径必中。
- **复核命令**：
  ```bash
  git show master:scripts/quick-sync.sh | sed -n '43,49p;57,71p'
  ```

### P1-10 ✅ `update-from-github.sh` 在服务单元刷新失败时跳过全部重启，脚本仍以 `exit 0` 结束（CI/自动化判成功，升级实际未生效）

- **位置**：`scripts/update-from-github.sh:436-438`（`warn "服务单元未能正常启用（masked 等问题未解除），本次跳过全部重启…"`）、末尾 `exit 0`
- **证据**：unit 刷新（`:409-424`）两次尝试均失败后，脚本只打印 warn 并跳过 `:441`/`:457` 的 `restart`，随后继续执行收尾并显式 `exit 0`（末尾注释「明确退出，避免依赖上一条命令的偶然退出码」）。而同一脚本在别处对失败是 `err`（非 0 退出），退出码语义不一致。
- **运维影响**：CI/自动化/运维脚本无法据退出码判断升级结果——「升级成功」退出 0，但新代码未生效（服务仍在跑旧进程），与 P1-1 叠加后成为同一类事故的第二个入口。同时 `masked` 自愈失败本身是需要人工介入的状态，被降级为一行 warn 后极易被日志洪流淹没。
- **命中形态**：升级路径在 unit 被 mask/软链异常时命中。
- **复核命令**：
  ```bash
  git show master:scripts/update-from-github.sh | sed -n '430,460p'
  git show master:scripts/update-from-github.sh | tail -n 4      # 显式 exit 0
  ```

---

## 4. P2 缺陷（17 条 · 合并去重后）

| 编号 | 域 | 位置（master） | 一句话影响 | 复核 |
| --- | --- | --- | --- | --- |
| P2-1 | O3 | `nginx-gipfel.conf:32`、`:71-72`；`files/views.py:55`、`messages/views.py:67` | 反向代理零 `limit_req`/`limit_conn`，`client_max_body_size 128m` 而后端实际只收 10MB（非文件体 2.5MB）→ 任意未认证入口可并发慢速大 body 刷满磁盘/连接，单进程 daphne 无横向余地 | ✅ |
| P2-2 | O3 | `nginx-gipfel.conf:12`、`49-50`、`:66` | `upstream keepalive 32` 完全不生效（8 处 location 无一清空 `Connection`），`/socket.io/` 又把 `upgrade` 写死 → 每请求新建 TCP、TIME_WAIT 堆积，注释误导为已优化 | ◐ |
| P2-3 | O3+O5 | `apps/auth/views.py:108-111`、`apps/common/backend_gate.py:50` | 两处**硬编码 `https://`** 跳转（日志查看器按钮、`/admin` 防直连弹回），而默认产物无 443 → 按钮必然打不开、直连 `/admin` 被 302 到不可达地址（与 `docs/OPS.md:202`、`deploy/README.md:173` 的承诺相反） | ✅ |
| P2-4 | O3 | `deploy-linux.sh:590-601`、`update-from-github.sh:564-566`；`docs/OPS.md:15` | 防火墙只管 8120：从不放行 80/443，`--domain` 分支完全不碰 ufw；清理只删固定的 8120，改过端口后旧端口公网规则永久残留；文档承诺「随新值自动清理/重建」不成立 | ✅ |
| P2-5 | O3 | `nginx-gipfel.conf:154-156` | SPA 兜底把一切未命中路径变成 200 `index.html` → 监控/拨测/CDN 无法识别 404，静态资源 404 变成 HTML 200（`Unexpected token '<'`） | ◐ |
| P2-6 | O5 | 仓库无 `.github/`/CI；`README.md:277,299`、`docs/OPS.md:86`；`git tag` 为空；`VERSION.json` | 无任何 CI/发布门禁（文档三处宣称「CI 必跑」），`master` 即生产源，全仓无 tag → 未验证提交直达生产，且事故时没有「上一个已知良好版本」可锚定 | ✅ |
| P2-7 | O1 | `deploy-linux.sh:431`（`cp -a dist/. frontend-dist/`） | 前端发布非原子：rsync/cp 覆盖过程中 nginx 直接托管该目录 → 白屏/500 窗口；无版本化目录 + 原子软链切换 | ◐ |
| P2-8 | O1 | 两脚本全文无 `flock`/锁文件/trap | 并发部署（两个终端、CI 与人工同时）互相删改安装树与 `_backup`，无互斥、无中断清理 | ◐ |
| P2-9 | O1+O5 | `deploy-linux.sh:309`（`-z "$DOMAIN"`）、`update-from-github.sh:265`；`deploy/README.md:167` | 由纯 IP 切到域名后旧 `LOG_VIEWER_PUBLIC_URL` 无人清理/改写 → 按钮长期指向已废弃的明文地址，日志查看器 cookie 在 HTTPS 下仍非 Secure；文档自相矛盾地写「会自动移除（需手动删）」 | ✅ |
| P2-10 | O2 | `gipfel.service:27-30`、`logviewer.service:29-32`（`0755`、无 `UMask`） | 日志目录与文件对同机其他用户可读（含带一次性令牌的 access log 行）；`db.sqlite3`/`uploads` 同权限 | ✅ |
| P2-11 | O2 | `gipfel.service:18-24`（`-u /run/gipfel/gipfel.sock` 同时 `-b 127.0.0.1 -p 8000`）、两单元共用 `RuntimeDirectory=gipfel` | Unix socket 无任何消费方（nginx 走 127.0.0.1:8000）、却落在 0755 运行目录；两服务共用运行目录，停其一可能影响另一（X-13/X-23 同源，新信息：X-13 的「ReadWritePaths 授权」前提不成立） | ◐ |
| P2-12 | O2 | `gipfel.service` 无 `LimitNOFILE`/`MemoryMax`/`CPUQuota`/`TasksMax` | 上传 128MB × 并发、实时/股票引擎线程无上限 → 可拖垮整机（与 nginx 无限流叠加） | ◐ |
| P2-13 | O2 | `company_fields/timer.py:147-167`、`competitions/views.py:45-55`、`:202`、`:234-236` | 财年定时器在**请求内同步执行**且并发触发**静默跳过**（返回 None，接口仍 200）；nginx `/api/` 超时 120s、SIGTERM 直接 cancel 在途请求 → 财年已 CLOSED 而部分公司字段仍是旧值，且**无手动重跑入口**，只能反向切状态（会先跑 FY_START 二次覆盖） | ✅ |
| P2-14 | O4 | `backend/backend/settings.py:304-309` | `DATABASES` 无 `OPTIONS`（`busy_timeout` 仅 Python 默认 5s）、未启用 WAL；**两个 daphne 进程共用同一 SQLite 库**（日志查看器登录会写 `auth_user.last_login`）→ 并发写 `database is locked` → 5xx | ◐ |
| P2-15 | O4 | `scripts/verify-migration.sh`（uploads 丢失只 `check_warn`）、与 X-01 叠加 | 迁移验证对上传件整体丢失只告警不失败，退出码语义与「验证」不符 | ◐ |
| P2-16 | O4 | `scripts/migrate-server.sh`（pull 模式） | pull 在本机服务**运行中**直接覆盖 `db.sqlite3`/`uploads`/`.env`，不停服、无版本对齐 → 覆盖后进程持有旧 inode，数据与进程状态不一致 | ◐ |
| P2-17 | O5 | `deploy-linux.sh:234-238`（`if [[ -n "$DOMAIN" ]]`）、`update-from-github.sh`（0 处 `CORS_ORIGIN`）、`realtime/gateway.py:39-51` | 纯 IP 部署与「由升级脚本首次引导的实例」从不写 `CORS_ORIGIN` → Socket.IO `cors_allowed_origins="*"`（**任意源**），而 `deploy/README.md:201` 宣称未配置时「仅限开发/私网反射」（该限制只存在于 HTTP 层） | ✅ |

> 另有多条 P2 级文档/工具可信度问题（`tests/gipfel-logviewer-diag.sh` 绕过 nginx 且永远 `exit 0`；`tests/deploy_public_ip_test.sh` 断言固化 8120 错误契约 + 重复用例）已在 O3 报告 O3-09/O3-10 记录，且分别与既有 X-14/X-15 同源，本表按去重原则不重复计数。

---

## 5. P3 缺陷（10 条）

| 编号 | 域 | 位置 | 一句话 |
| --- | --- | --- | --- |
| P3-1 | O5 | `deploy-linux.sh:652` vs `settings.py:434` | 部署完成横幅指引 `tail -F backend/logs/app.log`，真实文件是 `gipfel.log`（排障第一步即扑空） |
| P3-2 | O5 | `deploy/README.md:274,282`、`settings.py:31-39` | Docker 示例 `-e JWT_SECRET=CHANGE-ME` 命中弱密钥黑名单 → 按示例必然启动失败；`-b 0.0.0.0` 与全仓加固口径矛盾；`collectstatic \|\| true` 吞失败 |
| P3-3 | O5 | `settings.py:51,140,143`、`logviewer/settings.py:109` | 环境变量无 schema/范围校验，非数字值使服务在 import 阶段崩溃（与 P1-7 叠加成重启循环） |
| P3-4 | O5 | `docs/MIGRATION.md:515` | `echo "0 3 * * * …" \| sudo crontab -` 会**整体替换** root 的 crontab（且该备份脚本本身是活库裸 `cp`，见 P1-2） |
| P3-5 | O5 | `docs/MIGRATION.md:160` vs `users/models.py:66` | 文档给的库校验 SQL 查 `users_user`，实际表名是 `users` → 必然 `no such table` |
| P3-6 | O4 | `.gitignore` 无 `_backup/` | 模式 A（安装目录即 clone）下 `git add -A` 会把用户上传件纳入版本库、`git clean -fdx` 会删掉唯一备份 |
| P3-7 | O3 | `nginx-gipfel.conf:50` 等 | 主站点 `Host $host` 丢端口：经非标端口/端口映射访问时 `Origin` 与 `get_host()` 不一致 → 全部 POST（含登录）403（日志查看器块已用 `$host:$server_port` 修过同类问题） |
| P3-8 | O3 | `deploy/README.md:108`、`docs/OPS.md:63-64`、`nginx-gipfel.conf:364-369/386-392` | 文档/模板不一致：验证步骤让开 https（无 443）；有域名形态下 `127.0.0.1:8120` 健康检查必失败（`deploy-linux.sh:519` 已删除 8120 块）；443 注释模板的 `/static/` 未补安全头；手动启用步骤漏了 80→443 跳转块 |
| P3-9 | O2 | 两个单元未设 `TZ`；`PATH` 仅 `.venv/bin`；`.env PORT` 非真源 | 环境契约与文档声明不一致（子进程缺 `/usr/bin`、时区依赖系统默认） |
| P3-10 | O3 | `nginx-gipfel.conf:17,243`；`deploy-linux.sh:559` | `listen [::]` 无 IPv6 可用性探测，内核禁用 IPv6 的主机 `nginx -t` 失败并在部署最后一步中止（⚠ 推断） |

---

## 6. 跨域共性根因（比单条缺陷更值得处理）

1. **「成功」的定义过窄**：脚本以「命令退出码 0」= 成功，而部署真正的成功条件是「新代码正在被服务执行」。由此派生 P1-1（不重启仍报完成）、P1-10（跳过重启仍 `exit 0`）、P1-2（`cp` 失败被 `|| true` 吞）、`docs/OPS.md` 的多处「重跑即可自动解决」承诺。
2. **没有任何「发布门禁 / 生产形态校验」的执行点**：仓库无 CI（P2-6），无 `manage.py check --deploy`，无 tag，健康检查是纯 liveness（`apps/auth/views.py:67-73` 恒返回 `{"status":"ok"}`，不碰库/盘/迁移），诊断脚本又绕过 nginx（O3-09）——上述所有 P1 都**无法被任何自动化发现**，只能靠用户报障或人工比对。
3. **安全配置在两个 Django 站点之间不对称**：同一份 `.env`、同一个 nginx，日志查看器有 `SECURE_PROXY_SSL_HEADER`、Secure cookie、Host 白名单三来源兜底；主后端全都没有（P1-4），而 Host 白名单在域名形态下又漏掉子域（P1-5）。加固做过一半，比完全没做更危险（会让人以为已达标）。
4. **数据保护链路的每一段都「看起来有」但都不可靠**：活库 `cp` 备份 → 无保留策略、与生产同盘 → 无校验/无恢复演练 → 回滚只恢复 db → `quick-sync.sh` 同步错目录（P1-2、P1-9、P2-14/16）。RPO/RTO 无任何承诺。
5. **文档与默认产物系统性脱节**：文档描述的是「理想形态」（https、域名子域、云端防火墙、按钮可用），脚本产出的是「最小可跑形态」（80 明文、子域无白名单、只放行 8120、`app.log` 不存在、Docker 示例起不来）。逐条见 P1-3/P1-5、P2-3/P2-9、P3-1/2/5/8。
6. **`/opt` 的权限模型与 systemd 沙箱注释自相矛盾**：单元声称「文件系统只读 + 仅开放必要可写路径」，实际运行账户拥有整棵安装树（含 nginx 托管的静态资源与唯一备份），且 root 会执行其中文件（P1-6）。

---

## 7. 建议的处置优先级（不改代码，仅排序）

**上线/交付前必须先处理（阻塞项）**
1. P1-1 让部署脚本对已存在服务执行 `restart`（或在收尾强制重启），并把「代码已换但服务未重启」消除到不可能。
2. P1-3 + P1-4：把「是否 HTTPS」变成部署脚本的一等状态——启用则同时打开后端 Secure cookie / `SECURE_PROXY_SSL_HEADER`，未启用则在收尾用醒目结论声明「当前为明文，凭据可被嗅探」。
3. P1-5：两种形态都把日志查看器的外网入口 host 写入白名单，并在收尾用 `curl -H "Host: <入口>" http://127.0.0.1:8121/` 自检非 400。
4. P1-9：修正 `quick-sync.sh` 的目标目录计算（或直接废弃该脚本）。
5. P1-2：备份改用 SQLite 一致性备份（`.backup`/`VACUUM INTO`）+ 加保留策略 + 备份后校验可打开，回滚文档补齐 `uploads`/`.env`/代码与 `gipfel-logviewer` 重启。

**两周内（可用性与可观测性）**
6. P1-8 加 logrotate（`copytruncate` 或改为 journald 收集）+ 磁盘水位告警；P1-7 显式设 `StartLimitBurst`/`OnFailure` 或改为告警式重启。
7. P1-6 把 `ProtectSystem=strict` 与 `UMask` 落地，并把 `_backup` 移出运行账户可写范围。
8. P1-10 退出码语义统一（跳过重启不得退出 0）。
9. P2-13 财年定时器改为可观测的异步任务 + 提供重跑入口。
10. P2-6 加最小 CI（`vue-tsc`、`manage.py check`、`makemigrations --check`、`bash tests/*.sh`）与 tag 化发布。

**一个月内（纵深与加固）**
11. P2-1/P2-2/P2-4/P2-10/P2-11/P2-12（限流、keepalive、防火墙端口集合、UMask/日志权限、socket 清理、资源上限）。
12. P2-14/P2-15/P2-16（WAL + `busy_timeout`、迁移验证断言化、pull 停服）。
13. P2-3/P2-5/P2-17 及全部 P3（scheme 推导、404 语义、CORS 白名单、文档与模板对齐）。

---

## 8. 与既有审计（`code_audit/U14-scripts-deploy-tests.md` X-01…X-29）的关系

**独立复核后确认在 master 上「仍存在」**（本报告已合并/吸收，不重复计数）：X-01（`verify-migration.sh` 计数器与退出码语义）、X-04（恢复失败被吞）、X-05（运行中同步活库，机制需更正为「未启用 WAL，缺 `-journal` 与一致性保证」）、X-08（`update-from-github.sh` 硬编码 8120）、X-09（验证只看文件大小/uploads 丢失仅 WARN）、X-13（共用 `RuntimeDirectory`；**更正**：X-13 的「ReadWritePaths 授予写权限」前提不成立，越权面是整棵 `/opt`，见 P1-6）、X-14（diag 脚本泄密钥/无超时）、X-15（部署测试 `$PWD` 假阴性）、X-22（口令进 stdout）、X-23（socket 权限/无 `UMask`）、X-26（README 回滚不完整）。

**本报告对既有审计的更正/补充**
- X-24「nginx 失败仍退出 0」在 master 上**已不成立**（`nginx -t`/`reload`/`start` 失败经 `set -e` 会非 0 退出）；真正「失败仍退出 0」的是 `update-from-github.sh` 收尾的显式 `exit 0` 与其「跳过全部重启」分支（P1-10），以及 `deploy-linux.sh:574-580` 的校验性 warn。
- X-28（`shift` 吃 `%0`）与 X-19 引用的 `dev.py`/`stop-dev.bat` 在 master 上不存在（属分支差异）。
- X-05/X-01 的位置应从 `tests/` 更正为 `scripts/`（master 无 `tests/` 版本）。

**未被既有审计覆盖、本次新增的高价值项**：P1-1、P1-2 的保留策略/回滚完整性部分、P1-3、P1-4、P1-5、P1-6 的 `/opt` 越权面、P1-7、P1-8、P1-9、P2-13、P2-17、P3-4、P3-5，以及全部 O5 域条目。

---

## 9. 局限与未确认项

1. **环境限制**：本机为 Windows，无 Linux/systemd/nginx/daphne 运行环境。凡涉及「运行时才可观测」的链路均已标注 ⚠ 推断，并在域报告内给出目标机上的只读复核命令（`systemd-analyze security`、`systemctl show -p DefaultStartLimit*`、`ss -ltnp`、`ufw status numbered`、`nginx -T`、`curl -H "Host: …" http://127.0.0.1:8121/`）。
2. **未做端到端复现**：`database is locked` → 5xx 的并发压测、活库 `cp` 备份的损坏概率、财年定时器超 120s 的实际耗时，均需测试机压测。
3. **已排除的假阳性**（避免误报，供复核）：CRLF 类（master 实际 LF）；`frontend/package.json` 的 `prebuild` 污染工作区（blob 为 LF 且 `version` 与 `VERSION.json` 一致，往返输出逐字节相同，`IDENTICAL=true`）；`.env.example` 引号导致 `DJANGO_ALLOWED_HOSTS` 追加失效（systemd env-file 状态机允许在闭引号后继续累积）；`X-Forwarded-Host` Host 投毒（后端未设 `USE_X_FORWARDED_HOST`）；`/uploads/` 目录穿越与 dotfile 拦截（规则顺序正确）；Socket.IO 升级链路本身（可用）；Django 源码层面的「请求全站串行」（Django 的 `ThreadSensitiveContext` 使并发请求各得线程）。
4. **本次审计不包含**：业务逻辑正确性、权限/越权、前端功能与性能（属既有 `code_audit/U01-U14` 与 `B01-B08` 系列范围），以及 `contract_watcher`/`backend/examples` 等 master 上不存在的目录。

---

## 10. 交付物与复核方式

| 文件 | 内容 |
| --- | --- |
| 本报告 | 去重、校准、Lead 复核后的总报告（37 条） |
| [O1-发布与部署流水线.md](O1-发布与部署流水线.md) | 14 条 + 已核对无问题 12 条 + 移交线索 |
| [O2-服务编排与运行时.md](O2-服务编排与运行时.md) | 12 条 + 已核对无问题 12 条 + 移交线索 4 条 |
| [O3-反向代理与网络边界.md](O3-反向代理与网络边界.md) | 13 条 + 已核对无问题 10 条 + 移交线索 6 条 |
| [O4-数据备份与容灾.md](O4-数据备份与容灾.md) | 11 条 + 已核对无问题 + X-01/04/05/09 复核表 |
| [O5-配置密钥与可观测性.md](O5-配置密钥与可观测性.md) | 11 条 + 已核对无问题 8 条 + 移交线索 |

**复核方式**：所有「验证命令」均为只读命令，在仓库根目录直接执行即可复现同一证据（`git show master:<path>` 读对象库，不受当前工作区所在分支影响）。若需按行号比对，请用
```bash
git -c core.autocrlf=false -c core.eol=lf archive master --format=tar -o /tmp/m.tar && tar -xf /tmp/m.tar -C /tmp/m
```
导出逐字节快照后再读——直接用 `git archive`（不覆盖 `core.autocrlf`）在 Windows 上产出的是 CRLF 版本，行号相同但内容不同。

**声明**：本次审计未修改 master 分支或工作区的任何代码、配置、模板与脚本；`git status --porcelain` 与审计前完全一致。
