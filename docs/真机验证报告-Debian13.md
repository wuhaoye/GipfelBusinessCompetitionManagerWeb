# C1-a / C2 阶段1 / C3 · Debian 13 真机验证报告

> **目的**：把 [`架构性运维约束整改验收报告.md`](架构性运维约束整改验收报告.md) §7 中标为「需 Linux 真机验证」的
> T1–T5 项在真实 Linux 主机上跑掉，验证「结构正确」之外「在 Linux 上真的能跑」。
> **被测对象**：本仓库当前工作区（含本次全部整改）经 tarball 传到 VM 的 `~/vmtest/repo`。
> **执行方式**：本机 Windows → SSH（`wuhaoye@192.168.56.129`）非交互执行。
>
> ⚠️ **本文档已更新到第二轮**（用户给 `wuhaoye` 加了 sudo 之后）：第一轮（§0–§6）在独立目录 + 独立 venv 上完成；
> **第二轮（§7）**补齐了 systemd 真实装载、**完整 `deploy-linux.sh` 全流程部署**、redis 真机、100 客户端压测、财年推进计时、
> 前端构建，并因此**发现并修复了 4 个部署脚本缺陷**（其中 3 个只在真实的 C2-WAL + 真实全流程部署下才会暴露）。
> 现在 VM 上的 `/opt/gipfel` 部署**就是本次整改后的版本**（双进程 + WAL + 新 nginx 限流），并已通过部署后验收（§7.6）。

## 0. 结论摘要

| # | 验证项 | 对应风险项 | 结论 |
| --- | --- | --- | --- |
| 1 | `pip install -r requirements.txt`（Python 3.13 / Linux） | — | ✅ gunicorn 23.0.0 **在 Linux 装上**（`sys_platform != "win32"` 标记生效）、redis 5.0.8、Pillow 12.3.0 wheel |
| 2 | `manage.py check` | — | ✅ `System check identified no issues (1 silenced).` |
| 3 | **全量测试 584 项** | 基线回归 | ✅ **`Ran 584 tests in 65.497s` … `OK`** |
| 4 | WSGI 入口（`backend.wsgi:application`） | T1 | ✅ `WSGIHandler` 可加载（改造前此处 `ImportError`，现修复已被 Linux 验证） |
| 5 | C2：PRAGMA 落地（ext4 本地盘） | T2/T6 | ✅ `journal_mode=wal` / `busy_timeout=20000` / `synchronous=1` / `wal_autocheckpoint=1000`；启动日志「SQLite 调优生效：WAL 已打开」 |
| 6 | C2：并发写不锁 | T6 | ✅ 20 线程 × 10 条 = 200 写，**0 次 `database is locked`**，0.95s；反向对照（持锁 6s + `busy_timeout=5000`）✅ 如期报锁 → 证明参数真实生效 |
| 7 | C1-a：双进程真实启动 | T1 | ✅ gunicorn 23.0.0 `-w 4 -k gthread --threads 8` 起 4 个 worker；daphne hub 启动；`/api/health` 均 200 |
| 8 | C1-a：**跨进程实时广播端到端** | C1.5③ | ✅ edge 进程 emit → forward 总线 → hub → socket 客户端；✅ 真实写库的 `post_save` 信号跨进程送达；✅ hub 内投递（`seq=7`）；✅ `sync:replay` 从 hub 环形缓冲补发 |
| 9 | C1-a：**顶号跨进程强制断开**（I-01 真实路径） | 安全回退 | ✅ 5/5：另一处经 edge 登录后，旧连接收到 `auth:required` **并被 hub 真正断开** |
| 10 | C3：`/auth/me` 与批量端点契约 | T3 | ✅ 14/14（字段集不变、ETag、304 空体、light 四字段、两种 ETag 不互串、401 语义不变、批量端点/上限/非法参数、旧端点、开关回退） |
| 11 | nginx 配置真机语法 | T4 | ✅ **`nginx -t`：test is successful**（850 行完整展开）；zone/限流/429/双 upstream/`$request_time`/health 零限流/socket.io 只限连接/无 `/_internal` 代理 全部命中 |
| 12 | 部署脚本语法 | T3（bash -n） | ✅ `bash -n` **9/9 通过** |
| 13 | systemd unit | T1 | ✅ 渲染后 `systemd-analyze verify` **自身 0 报错**；`Environment` 在 `EnvironmentFile` 之前；ExecStart 等价命令实跑 4 worker + 探活 200 |
| 14 | 既有部署未被影响 | — | ✅ 测试期间/之后 `nginx`、`gipfel`、`gipfel-logviewer` 均 active，线上 `/api/health` 200 |
| 15 | 未覆盖（缺 root/环境） | — | ✅ **第二轮已全部补齐**：systemd 真实装载、完整 `deploy-linux.sh` 全流程、redis 真机、100 客户端压测、财年推进计时、前端构建 —— 见 §7；**并因此发现 4 个部署脚本缺陷（已修复 + 回归用例）** |

**环境**：Debian 13（kernel 6.12.107+deb13-amd64）、Python 3.13.5、2 vCPU / 1.9GB RAM / ext4（`/dev/sda1`）、nginx 1.26.3、Node 20.20.2。
第一轮时 `wuhaoye` 不在 sudoers、root SSH 被禁；**第二轮用户已授权 sudo**，因此 systemd/apt/nginx/全流程部署均已在真机执行。

---

## 1. 关键证据原文

### 1.1 全量回归（真机）
```
$ cd ~/vmtest/repo/backend && ../../venv/bin/python manage.py test apps tests_fix_verify
System check identified no issues (1 silenced).
...
Ran 584 tests in 65.497s
OK
Destroying test database for alias 'default'...
（Windows 本机同版本为 277s；Linux 快主要因为 2 核 + 内存态测试库）
```

### 1.2 C2：PRAGMA 与并发写（真机 ext4）
```
journal_mode       = wal
busy_timeout       = 20000
synchronous        = 1
wal_autocheckpoint = 1000
OPTIONS            = {'timeout': 20.0}
[INFO] SQLite 调优生效：WAL 已打开（db=…/repo/backend/db.sqlite3；journal_mode=WAL→wal; synchronous=NORMAL; busy_timeout=20000→20000; wal_autocheckpoint=1000→1000）

[B1] 20 线程并发写 200 条：无 database is locked   墙钟=0.95s 异常=0 锁错误=0
[C1] 对照：busy_timeout=5000 时持锁 6s 确实报锁（证明参数生效）
```

### 1.3 C1-a：双进程 + 跨进程广播（真机）
```
gunicorn: Listening at http://127.0.0.1:18002 → Booting worker 3545/3546/3547/3548（gthread）
daphne  : Listening on TCP address 127.0.0.1:18000（REALTIME_BUS=hub）
探活：:18002/api/health=200  :18000/api/health=200  :18000/socket.io 握手=200  :18002/socket.io=404（预期：WSGI 不服务 WebSocket）
内部端点：无令牌 POST=403；GET=405；带真实令牌 POST={"ok": true, "seq": 3}

C1A_E2E2: PASS=9 FAIL=1
  PASS B1 edge 进程 emit 经 forward 总线送达 hub 客户端
  PASS B2 edge 真实写库(post_save 信号) 跨进程送达
  PASS C1 hub 进程内投递（内部端点带令牌）送达客户端
  PASS D1 sync:replay 从 hub 环形缓冲补发（serverSeq=7 补发=1）
  FAIL E1 顶号(auth:required) 跨进程送达   ← 测试设计错误（见 §3.1），修正后 5/5 PASS

KICK_E2E（真实顶号路径，修正版）: PASS=5 FAIL=0
  [client] auth:required {'reason': 'token_version_mismatch'}
  [client] disconnect（被服务端踢出）
```

### 1.4 C3：API 契约（真机 gunicorn）
```
C3_PROBE: PASS=14 FAIL=0
  /auth/me 无参 200 + 字段集与改造前一致（缺字段=[]）；ETag=W/"f-666c8a30…"
  If-None-Match 命中 → 304 空体、ETag 一致
  ?light=1 → 仅 ['id','isActive','mustChangePassword','tokenVersion']，light-ETag 与 full-ETag 不同
  无效 token → 401；无效 token + If-None-Match → 仍 401（未变 304）
  批量端点 200 + {companies,companyIds,missing,serverTime}；51 家→400「单次最多查询 50 家公司（本次 51 家）」；非数字→400
  旧端点 /company-fields/1 → 404（仍在路由中，未被删除）
  AUTH_ME_CONDITIONAL_ENABLED=false → 不再返回 ETag
```

### 1.5 nginx（真机 1.26.3）
```
$ /usr/sbin/nginx -t -c <渲染后的最小 http{} 包装> -p <测试目录>
nginx: configuration file … syntax is ok
nginx: configuration file … test is successful       ← exit 0
展开 850 行，逐条命中：
  limit_req_zone … zone=gipfel_api_rl:10m rate=60r/s       命中=1
  limit_req_zone … zone=gipfel_login_rl:10m rate=20r/s     命中=1
  limit_conn_zone … gipfel_api_conn / gipfel_socket_conn   命中各 1
  log_format gipfel_rt（含 rt=$request_time）              命中=1
  upstream gipfel_django / gipfel_socketio                 命中各 1
  server 127.0.0.1:8002 / 127.0.0.1:8000                   命中各 1
  limit_req_status 429 / limit_conn_status 429             命中各 1
  归属核对：= /api/auth/login → 仅 login zone + conn 600
            /api/           → api zone(60r/s,burst120) + conn 600
            /socket.io/     → 仅 conn 200（无限速）
            = /api/health    → 无任何 limit 指令
  /_internal/ 被代理次数 = 0
```

### 1.6 脚本与 systemd
```
bash -n：共 9 个脚本，失败 0（deploy-linux.sh / update-from-github.sh / migrate-server.sh / verify-migration.sh / quick-sync.sh / lib/deploy-common.sh / tests 3 个）
systemd-analyze verify（渲染 gipfel.service / gipfel-wsgi.service）：与自身相关报错 0 条
渲染后：Environment="PATH=…/venv/bin"（31 行）→ EnvironmentFile（39 行）：顺序正确
        ExecStart=…/venv/bin/gunicorn --bind 127.0.0.1:${GIPFEL_WSGI_PORT} --workers ${GIPFEL_WSGI_WORKERS} …
ExecStart 等价命令实跑：Booting worker=4，:18006/api/health=200
```

---

## 2. 与主验收报告的对应关系（T1–T5 更新）

| 主报告风险项 | 真机验证结果 |
| --- | --- |
| **T1** gunicorn/systemd 双进程未验 | ✅ gunicorn 4 worker + daphne hub 真实启动、双探活 200、unit 语法与 ExecStart 实跑通过。**仅 systemd 装载（`systemctl start`）因无 root 未做** |
| **T2** 财年推进耗时未压测 | ⚠️ 仍未压测（需真实业务数据与 root 部署）；但 C2 并发写（20×10）已证明写路径不再锁失败 |
| **T3** 100 客户端重连压测 | ⚠️ 仍未压测（本机无压测工具/负载生成环境）；nginx 阈值已在真机语法层面确认按预期归属 |
| **T4** 真实 `nginx -t` | ✅ **已跑通**（`test is successful`），限流/上游/`$request_time`/health 豁免/无 `/_internal` 代理逐条命中 |
| **T5** Redis 模式真机 | ⚠️ 未验（VM 无 redis-server；`redis` 包已随 requirements 装上，`REALTIME_BUS=redis` 分支仍为假客户端单测覆盖）；**单机默认的 forward 模式已真机验通** |
| （新）bash -n | ✅ 9/9 |
| （新）Python 3.13 + requirements 真实安装 | ✅ 含 gunicorn/redis 的平台标记 |

---

## 3. 真机测试发现的三个事实（含 1 个设计性观察）

### 3.1 观察：登录顶号的「延迟 kick」可能与新连接赛跑（测试设计教训 + 产品提示）
- 现象：第一次 E2E 里，客户端在 `connect()` 后 7ms 被 `auth:required` 踢掉——原因是**同一账号稍早那次登录**发出的 kick
  经 forward 总线异步到达 hub，恰好落在新连接建立之后（hub 侧 kick 语义 = 断开该 user 房间内的**所有** 会话）。
- 复现条件：kick 的投递延迟（forward ≈ 1–5ms）与「登录 → 新建 socket」的间隔同一量级。
  真机实测：客户端在同一进程内**紧接**登录后连接会命中；加入「历史 kick 排空」后在真实路径下稳定 5/5 PASS。
- 影响评估：浏览器流程里 socket 连接发生在登录响应之后（JS 事件循环 + 握手 ≥10ms），而 kick 通常 1–5ms 内到达 hub，
  因此**正常前端流程不会命中**；但属真实存在的窄窗口。
- 建议（未改实现）：若不放心，可让 hub 侧的 session 记录 `token_version`，kick 只断开 `tv` 不比请求时新的会话；
  或前端收到登录后 N ms 内的 `auth:required` 时先重连一次再登出。已作为残余项登记。

### 3.2 VM 上的既有部署是**改造前**的旧版本（本次未改动它）
```
/opt/gipfel：只有 gipfel.service + gipfel-logviewer.service（无 gipfel-wsgi.service）
nginx 已启用站点：gipfel.conf（/api/ 仍指 127.0.0.1:8000）
DB：/opt/gipfel/backend/db.sqlite3 → journal_mode=delete（52 表 / 1 用户 / 9-24 13:17）
venv：无 gunicorn、无 redis
```
→ 本次真机验证是**在独立目录 + 独立 venv 上进行**（`~/vmtest/repo`、`~/vmtest/venv`），
线上部署保持原状（验证前后 `nginx/gipfel/active`、`/api/health=200`）。
**要让 VM 用上本次整改**，需以 root 执行仓库的部署脚本（见 §5）。

### 3.3 环境限制：本会话无法取得 root
`wuhaoye` 不在 sudoers；`ssh root@…` 被拒；`su` 需要交互式 TTY。因此以下只能由用户在 VM 终端（有 root/TTY）执行：
`systemctl daemon-reload/start`、`apt install`、写 `/etc/nginx`、`deploy-linux.sh` 全流程。

---

## 4. 未覆盖清单（明确边界）

| # | 项 | 原因 | 建议 |
| --- | --- | --- | --- |
| 1 | `systemctl start gipfel-wsgi`（真实 unit 装载/沙箱） | 无 root | 部署时由 `deploy-linux.sh` 完成；或 root 下 `systemctl start gipfel-wsgi && systemctl status gipfel-wsgi` |
| 2 | 完整 `deploy-linux.sh`（apt + systemd + nginx reload + 前端构建） | 无 root、GitHub 403（`npm ci` 需联网装依赖） | root 下按 `deploy/README.md` 执行；注意 `node_modules` 需可达 npm 源 |
| 3 | redis 总线真机 | VM 无 redis-server | `apt install redis-server` 后设 `REALTIME_BUS=redis`，重跑 `c1a_realtime_e2e2.py` |
| 4 | 100 客户端稳态/断网重连压测（C3.4） | 无压测工具与负载环境 | 现场用 k6/ab 或真实客户端；同时看 access log 的 `rt=$request_time` 与 429 计数 |
| 5 | 财年推进 <3s | 需真实业务数据 | 真机部署后按 C1.5 验收 |
| 6 | 前端构建产物（`npm ci && npm run build`） | GitHub 403 / 依赖需 npm 源 | 在可达 npm 源的网络下构建 |

---

## 5. 在 VM 上启用本次整改（需 root，供用户执行）

已在 VM 留下可直接复用的源码与工具：
```
~/vmtest/repo        ← 本次整改后的完整源码（937 文件，含 deploy/scripts/tests）
~/vmtest/venv        ← 已按 requirements.txt 装好的独立 venv（含 gunicorn/redis）
~/vmtest/scripts/    ← 本次使用的全部验证脚本（可重跑：c1a_split.sh、c1a_realtime_e2e2.py、kick_e2e.py、
                        c3_api_probe.py、c2_sqlite_probe.py、nginx_full.sh、units_final.sh 等）
~/vmtest/logs/       ← 原始输出
```
推荐做法（root 终端）：
```bash
# 1) 把整改后的源码放到部署源目录（VM 上原源码在 ~/桌面/…-bugfix-merged，是旧版）
sudo rsync -a --delete --exclude '.venv' --exclude 'db.sqlite3' --exclude 'uploads' --exclude 'snapshots' \
     /home/wuhaoye/vmtest/repo/ /opt/GipfelBusinessCompetitionManagerWeb/

# 2) 用仓库自带脚本部署/升级（会装 gipfel-wsgi.service、渲染新 nginx、迁移、构建前端、重启两个服务）
cd /opt/GipfelBusinessCompetitionManagerWeb
sudo bash scripts/update-from-github.sh --source-dir /opt/GipfelBusinessCompetitionManagerWeb \
     --install-dir /opt/gipfel --with-nginx          # 纯 IP 形态；有域名再加 --domain

# 3) 部署后健康检查（新增 WSGI 进程）
curl -sS http://127.0.0.1:8002/api/health      # 期望 {"code":0,...}
curl -sS http://127.0.0.1/api/health           # 经 nginx 命中 WSGI 上游
curl -sS -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8000/socket.io/?EIO=4&transport=polling'   # 期望 200
systemctl status gipfel gipfel-wsgi
```
> 注意：`update-from-github.sh` 会走 `git pull`（VM 未装 git）；请用上面的 `--source-dir` 形式或先 `apt install git`。
> 部署前请确认 `backend/db.sqlite3` 位于**本地磁盘**（本机 `/dev/sda1 ext4` ✓，WAL 前提满足）。

---

## 6. 原始输出存档（VM 侧）
```
~/vmtest/fullsuite.log          # 584 tests OK 全文
~/vmtest/fullsuite.pid
~/vmtest/logs/hub.log           # daphne hub 日志（含内部端点 403/405 与 seq）
~/vmtest/logs/edge-error.log    # gunicorn 4 worker
~/vmtest/logs/unit-exec.log     # ExecStart 等价命令实跑
~/vmtest/nginx/dump.txt         # nginx -T 850 行展开
~/vmtest/nginx/vhost*.conf      # 渲染后的 vhost
~/vmtest/units/*.service        # 渲染后的 unit
```

---
---

# 第二轮：sudo 可用后的补测（T1/T2/T3/T5 + 全流程部署）

> 触发：用户给 `wuhaoye` 加了 sudo（`sudo` 可用、以 root 执行），于是第一轮标为「未覆盖」的项目全部可测。
> 所有操作都在真机执行；**结尾把 VM 上的 `/opt/gipfel` 实际升级成了本次整改后的版本**，并完成部署后验收。

## 7.1 systemd 真实装载（T1）✅

用**生产 unit 原文**（仅把 `__INSTALL_DIR__` 换成测试树）在 systemd 下真实装载运行：

| 检查 | 结果 |
| --- | --- |
| 运行身份 | `User=gipfel / Group=gipfel`（与生产一致） |
| unit 状态 | `active (running)`，`ExecMainStatus=0`，监听 `127.0.0.1:8002` |
| worker | gunicorn 4 worker（Tasks=5，~224MB） |
| 探活 | `/api/health` **200**、`/api/version` **200** |
| 沙箱（`systemctl show` 实测） | `UMask=0027`、`ProtectSystem=full`、`ProtectHome=yes`、`PrivateTmp=yes`、`NoNewPrivileges=yes`、`RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`、`RuntimeDirectory/LogsDirectory=gipfel-wsgi`(0750) |
| ReadWritePaths 实测 | db.sqlite3 / logs / uploads / snapshots **均可写**；`/etc`、`/home/wuhaoye`、`/srv` 根**不可写** ✅ |
| LogsDirectory 产物 | `wsgi.access.log` 权限 `640 gipfel:gipfel`（UMask 生效） |
| `EnvironmentFile` 覆盖 unit 默认值 | `/proc/<MainPID>/environ` 显示 `REALTIME_FORWARD_URL=http://127.0.0.1:18000`（unit 默认是 `:8000`）→ **`Environment` 在前、`EnvironmentFile` 在后的覆盖语义被真机证实** |
| 崩溃自愈 | `kill -9` 主进程 → 服务回到 `active` 且 `/api/health` 200（`Restart=always`） |
| C2 在 systemd 下生效 | 服务自己的库：`journal_mode=wal / busy_timeout=20000 / synchronous=1`，日志出现「SQLite 调优生效：WAL 已打开」 |

> 过程记录：第一次尝试把 venv 放在 `/root`→`/home` 下，unit 的 `ProtectHome=yes` 使 `ExecStart` 无法执行（`226/NAMESPACE`）。
> 这恰好反向证明了生产布局（venv 在 `/opt`，不在 `/home`）是必须的。

## 7.2 Redis 总线真机（T5）✅ 9/9

`apt install redis-server`（8.0.2）→ 两端 `REALTIME_BUS=redis`：

| # | 断言 | 结果 |
| --- | --- | --- |
| — | `sio.manager` 类型 | **`AsyncRedisManager`**（日志：「实时总线：Socket.IO 已启用 AsyncRedisManager」） |
| B1 | edge 进程 emit → **经 Redis** → hub 客户端收到 | ✅ |
| B2 | edge 真实写库（`post_save` 信号）→ Redis → 客户端 | ✅ |
| B3 | Redis 承担序号与环形缓冲 | ✅ `gipfel:realtime:seq=6`、`gipfel:realtime:ring` zcard=4，pubsub channel `socketio` |
| D1 | `sync:replay` 从 Redis ZSET 补发 | ✅ `serverSeq=6`，补发 1 条 |
| E1 | 顶号（redis + FORWARD_URL）由 hub 强制断开 | ✅ 收到 `auth:required` 并断开 |
| 降级 | 配 `REALTIME_BUS=redis` 但 Redis 不可达 | ✅ 只 warning，`resolve_mode()` 回落 `local`，**不抛异常、不影响启动** |

## 7.3 100 客户端压测 + **限流阈值数据驱动调优**（T3）✅

经**改造后的 nginx vhost**（测试实例 18080，上游=真机 gunicorn/daphne；全部流量来自 127.0.0.1 = 模拟「全场共用一个 NAT 出口 IP」）：

| 场景 | 60r/s + burst120（初版） | **120r/s + burst240（已改入仓库）** |
| --- | --- | --- |
| S1 稳态（100 客户端心跳 300 请求/3 轮） | 300×200，0×429 | 300×200，0×429 |
| S2 改造前形态尖峰（100×11=1100 请求瞬时） | 151×200 / 949×429，后端存活 | 343×200 / 757×429，后端存活 |
| S3 改造后形态（100×2=200 请求，无抖动） | 136×200 / **64×429** ❌ 误伤 | **200×200 / 0×429** ✅ |
| S3b 改造后形态 + 前端启动抖动 0–1s | 179×200 / **21×429** ❌ 仍误伤 | **200×200 / 0×429** ✅ |
| S4 `/api/health` 200 瞬时请求 | 200×200，0×429 | 200×200，0×429 |
| S5 access log 含 `$request_time` | — | ✅ 含 `rt=/uct=/uht=/urt=`（1800 行） |

**结论（已落地为配置修改）**：C3 前端把重连请求从 ~1100 降到 ~200，但 `60r/s` 在同一出口 IP 下仍会拦掉
10%~32% 的**正常**重连；`120r/s + burst240` 在真实场景 0 个 429，而最坏 1100 请求风暴仍拦掉 757（后端全程存活）。
→ `deploy/nginx-gipfel.conf`（含注释态 443 模板）、`deploy/README.md`、`docs/OPS.md` 已同步，并新增/更新了
`tests/fix_verify/scripts/test_c1a_split_topology.py` 的数值断言与依据注释。

**压测后线上服务未受影响**：nginx / gipfel / gipfel-wsgi 全部 active，`/api/health` 200。
**真机复核（调优后）**：`nginx -t` → `test is successful`（生产形态渲染，850 行展开）。

## 7.4 财年推进真机计时（T2）✅

先造出**真的会让定时器干活**的数据（定时器只处理 `timer_enabled=True 且 timer_trigger=FY_START` 的字段）：

| 规模 | 工作量 | 经 nginx 的 `POST /api/competitions/{id}/fiscal-years` 墙钟 | DB 副作用校验 |
| --- | --- | --- | --- |
| 50 家公司 × 20 个 timer 字段 | 1000 次字段写入 + 逐公司重算 | **0.571s** | 值 `42` 计数 0 → 1000 ✅ |
| 200 家公司 × 20 个 timer 字段 | 4000 次字段写入 | **2.277s** | 0 → 4000 ✅ |

**结论**：简报里「财年推进 >120s / 全场冻结」的估算在本规模下**不成立**；200 公司档 2.28s < 3s 验收线。
（首版探针 12ms 是因为造的字段 `timer_enabled=False` → 定时器空跑；这条也说明"计时必须校验副作用"，已写进探针。）

## 7.5 前端构建链路 ✅

`npm ci`（272 包，**12 分钟**，2 vCPU）+ `npm run build`（`vue-tsc --noEmit && vite build`）→ **`BUILD_EXIT=0`**，
`dist/index.html` 1166 B + 76 个 assets；日志中 `error TS` 计数 = 0。
（`npm ci` 12 分钟是本 VM 的硬件下限，不是脚本问题；`deploy-linux.sh` 在第 5 步会做同样的事。）

## 7.6 完整 `deploy-linux.sh` 全流程部署 + 部署后验收 ✅

**这是本轮最有价值的部分**：真实跑全流程，暴露出 4 个部署脚本缺陷（3 个只在 C2-WAL 生效后才会出现）。

### 7.6.1 发现的缺陷（全部已在仓库修复 + 新增回归用例）

| # | 缺陷 | 现场表现 | 修复 |
| --- | --- | --- | --- |
| **D1** | rsync `--exclude snapshots` 未锚定 | 该模式匹配**任意层级**同名目录 → 把 Django app `backend/apps/snapshots/` 也排除，叠加 `--delete` **删掉线上该 app** → `manage.py check` 报 `ModuleNotFoundError: No module named 'apps.snapshots'`，部署中止、服务重启后起不来 | 改 `--exclude=/snapshots/`（前导斜杠锚定到传输根），`deploy-linux.sh` + `update-from-github.sh` |
| **D2** | migrate 前**不停服** | C2 开启 WAL 后，migrate 要把库从 rollback journal 切到 WAL，而旧版本服务仍持有连接 → 同一库两种日志模式并发访问 → **`database disk image is malformed`**（线上库各表不可读、VACUUM INTO 全部失败） | 两个脚本都新增「migrate 前停 gipfel/gipfel-wsgi/gipfel-logviewer」，并让失败 trap 尽力把服务拉回运行态 |
| **D3** | rsync 只排除 `db.sqlite3`，**没排除 `-wal`/`-shm`** | 源目录（含测试库）旁边的 `db.sqlite3-wal` 被拷到线上主库旁 → SQLite 拿「属于另一个数据库的 WAL」去恢复 → **`malformed`**（且因 rsync -a 保留 mtime，边车时间戳看起来"与主库无关"，极易误判为磁盘损坏） | 改 `--exclude 'db.sqlite3*'`（两个脚本） |
| **D4** | `append_env_entry` 不剥引号 | `.env` 原值 `"a,b,c"` 被逐项拆分后，首尾引号被当成条目的一部分保留，追加新条目后写成 `"a,b,c",d` → `Python-dotenv could not parse statement starting at line 84`，该变量可能整条失效（现场 `DJANGO_ALLOWED_HOSTS` 即此形态） | 读取时剥掉值两端引号、逐项再去引号后写回唯一一行 |

回归用例（真机事故驱动，Windows 上即可跑）：
`tests/fix_verify/scripts/test_deploy_rsync_excludes.py`（3 例）、
`tests/fix_verify/scripts/test_deploy_wal_safety.py`（6 例）——
覆盖「裸名排除不得与 `backend/apps/<name>` 同名」「归档目录必须用锚定形式」「migrate 前必须停服」「失败 trap 必须拉回服务」「必须排除 `db.sqlite3*`」「必须剥引号」。

### 7.6.2 事故处置（数据无损）

D2/D3 导致线上主库被判为 malformed。处置过程（全部留证）：

1. 取证：主库头 `journal_mode=delete`（字节 18/19 = 01 01），旁边却有一个 **4.16 MB、mtime 更早**的 `-wal`（rsync 保留了源库边车的 mtime）→ 确认为「外来 WAL」；
2. 备份快照完整：`_backup/<ts>/db.sqlite3`（`VACUUM INTO`）`integrity=ok`、users=1、audit_log=64；
3. 恢复：停三个服务 → 删除外来 `-wal`/`-shm` → 用该快照落位 → `integrity_check=ok`、`journal_mode=delete`、**行数与事故前完全一致**（无损）；
4. 用修复后的脚本重跑部署 → **全流程通过**。

> 值得记录的两点：① 部署脚本「快照失败即中止」的设计在事故中救了场（它拒绝在一个不可读的库上继续迁移）；
> ② 事故根因不是磁盘/SQLite，而是**部署脚本把源目录的边车文件带到了线上库旁边** —— 这正是 C2 引入 WAL 后新增的部署风险面，
> 已在 `docs/OPS.md` 与脚本注释里写明。

### 7.6.3 全流程部署结果

```
[OK] 数据库一致性快照已就绪：/opt/gipfel/_backup/.../db.sqlite3
[OK] DJANGO_ALLOWED_HOSTS 已写入（去重合并、唯一一行）：127.0.0.1,localhost,::1,192.168.56.129,124.126.98.153   ← D4 修复生效
[OK] 代码同步完成
[OK] Python 依赖安装完成（gunicorn 23.0.0 / redis 5.0.8 装进部署 venv）
[OK] 数据库迁移完成，静态资源收集完成（migrate 在**停服**状态下完成 WAL 切换）
[OK] 前端构建完成 → /opt/gipfel/frontend-dist（vite built in 15.40s）
[OK] 文件归属已切换为 gipfel，.env 权限 600
[OK] gipfel.service 运行中（daphne :8000，/socket.io/）
[OK] gipfel-wsgi.service 运行中（gunicorn :8002）
[OK] gipfel-logviewer.service 运行中
[OK] 已启用 gipfel nginx 虚拟主机；nginx 配置已 reload
```

### 7.6.4 部署后验收（`deployed_acceptance.py`，经 nginx:80）**15/16 PASS**

| # | 项 | 结果 |
| --- | --- | --- |
| A1–A4 | nginx `/api/health` 200（命中 **WSGI:8002**）；WSGI 直连 200；daphne `:8000` socket.io 握手 200；**WSGI 不服务 socket.io（404，预期）** | ✅ |
| A5/B0 | 造超管 + 比赛；**经 nginx 登录**取得 JWT（REST 走 WSGI 进程） | ✅ |
| B1–B3 | socket 客户端连 `:8000` 并订阅 `comp-1`；**经 nginx 的真实写库（PATCH `/api/users/{id}`）→ WSGI 进程 emit → forward 总线 → daphne hub → 客户端收到 `resource:changed`** ← C1.5③ 在**真实部署**上成立 | ✅ |
| C1 | ⚠️ 探针用**系统 python3 裸 sqlite3** 读 `busy_timeout` 得 5000（= sqlite3 默认值，不经过 Django 的 `OPTIONS`/信号层）→ 探针口径问题，非实现问题；**权威口径**见下 | 探针 FAIL |
| C2 | 线上库 `integrity=ok`；users=2、audit_log=70（含本次验收写入） | ✅ |
| D1–D4 | `/auth/me` 200+ETag；`If-None-Match` → **304 空体**；`?light=1` 仅 4 字段；无效 token 401；批量端点 200 | ✅ |
| E1 | 480 请求瞬时并发经 nginx → `{200: 299, 429: 181}`：**限流保护生效且后端未被拖垮** | ✅ |

**权威 C2 口径**（用**部署实例的 venv + Django** 读，即应用真正使用的连接）：

```
[INFO] SQLite 调优生效：WAL 已打开（db=/opt/gipfel/backend/db.sqlite3；journal_mode=WAL→wal; synchronous=NORMAL; busy_timeout=20000→20000; …）
journal_mode = wal    busy_timeout = 20000    synchronous = 1    OPTIONS = {'timeout': 20.0}
```

### 7.6.5 部署后的线上状态（最终）

| 服务/端口 | 状态 |
| --- | --- |
| nginx | active；`:80` → `/api/` 反代 **127.0.0.1:8002**（WSGI），`/socket.io/` 反代 **127.0.0.1:8000**（daphne） |
| gipfel.service | active，daphne `:8000`，`REALTIME_BUS=hub` |
| gipfel-wsgi.service | active，gunicorn `:8002`（4×gthread8），`REALTIME_BUS=forward` + `REALTIME_FORWARD_URL=http://127.0.0.1:8000` |
| gipfel-logviewer.service | active，daphne `:8121`（公网 `:8120` 经 nginx，直连 302/403） |
| redis-server | active（本轮为验证 redis 总线安装；**部署本身不依赖它**） |
| 限流 | `zone=gipfel_api_rl:10m rate=120r/s` + `burst=240 nodelay` + `limit_conn 600`；登录 `20r/s+burst120`；health 零限流 |
| 可观测性 | `log_format gipfel_rt`（含 `rt/uct/uht/urt`）已被主 server 引用 |
| 数据 | `db.sqlite3` 为 WAL、`integrity_check=ok`、原有用户/审计行完好 |

## 7.7 第二轮后仍属「需现场/真机确认」的项

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | 真实 100 客户端 + 真实 Wi-Fi 抖动下的体验 | 本轮是单机回环模拟（同一出口 IP、真实 nginx/gunicorn/daphne），**未使用真实客户端与无线网络**；建议赛前用 k6/ab 或真机预演校准 |
| 2 | CDN（Cloudflare）场景的真实客户端 IP | 未配 `set_real_ip_from`/`real_ip_header` 时全场会被算作一个 IP；阈值已按最坏情况校准，但应在启用 CDN 的现场确认 |
| 3 | C2 阶段 2（PostgreSQL） | 本次未做（属简报后续阶段）；`select_for_update` 在 Postgres 上会真正加锁，需另行复核 |
| 4 | 负载下的长时稳定性 | 本轮压测为秒级突发；未做小时级 SLO/内存观测 |
| 5 | `update-from-github.sh` 全流程 | 本轮跑的是 `deploy-linux.sh`（同一套 rsync/停服/迁移语义已同步修复）；`update-from-github.sh` 还含 `git pull`/模式 A–C 分支，VM 未装 git 未实跑 |

## 7.8 第二轮原始输出存档（VM 侧）

```
~/vmtest/deploy5.log                     # 最终成功的全流程部署日志（含各阶段 [OK]）
~/vmtest/deploy.log / deploy2/3/4.log    # 失败与诊断过程（D1–D4 的现场证据）
~/vmtest/scripts/sd_test2.sh / sd_test3.sh   # systemd 真实装载验证
~/vmtest/scripts/redis_setup2.sh / redis_e2e.py  # redis 真机 9/9
~/vmtest/scripts/load_final.sh / load_test.py / load_compare.sh  # 压测与阈值对比
~/vmtest/scripts/fiscal_timer_probe2.py  # 财年推进计时
~/vmtest/scripts/fe_build.sh / frontend-build.log  # 前端构建
~/vmtest/scripts/deployed_acceptance.py  # 部署后验收 15/16
/root/db-forensics-*/                    # 事故取证（外来 -wal 与主库副本）
/opt/gipfel/_backup/2026-09-26_222628/db.sqlite3  # 成功部署前的一致性快照
```
