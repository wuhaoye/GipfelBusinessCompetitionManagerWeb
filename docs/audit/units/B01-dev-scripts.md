# B01 dev 启动脚本（分支归属：bugfix / feature_wuhaoye 独有，提交 3dfc731）

> 审计对象（严格去重，仅此三个文件）：
> - `scripts/dev.py`（397 行，分支独有新增）
> - `scripts/start-dev.bat`（该提交重写，51 行）
> - `scripts/stop-dev.bat`（该提交新增，36 行）
>
> 分支归属已核实：`git ls-tree -r origin/prep-export-import -- scripts/` 与 `master` 的 `scripts/` 只有
> `bootstrap-dev.bat / deploy-linux.sh / gen_logviewer_key.py / migrate-server.sh / quick-sync.sh /
> start-dev.bat / update-from-github.sh / verify-migration.sh` —— **没有 `dev.py`、没有 `stop-dev.bat`**；
> 提交 `3dfc731 fix(scripts): 修复 Windows 开发启动 Ctrl+C 卡死、服务停不掉的问题` 引入 `dev.py`(397 行)、
> 重写 `start-dev.bat`(+/-119 行)、新增 `stop-dev.bat`(36 行)。当前 checkout = `feature/contract-watcher`。

## 概述

**审计方式**：全程只读（read/grep/git/进程与文件属性检查），**未执行** `dev.py` / `start-dev.bat` / `stop-dev.bat`，
未启动任何服务、未占用任何端口。为验证提交声称修复的 Ctrl+C / 子进程回收机制，另做了 4 组**隔离机制探针**
（仅临时拉起 1~2 个会自行退出的 `python.exe`/`node.exe`，与项目脚本无关，脚本与输出留在 `code_audit/.probe/`），
原始观测见 D-11 与"已验证无问题"一节。

**环境事实（本次实测）**
- `backend/.venv/Scripts/python.exe` = Python 3.14.6（255 KB → 是 venv redirector，真正解释器是其子进程）
- `frontend/node_modules/vite` = 5.4.21；`node` = v24.19.0（`D:\nodejs\node.exe`，在 PATH 上）
- `backend` 侧 `INSTALLED_APPS` 含 `daphne`（`backend/backend/settings.py:223`）→ `manage.py runserver` 走 ASGI；
  `backend/logviewer` 侧只有 `django.contrib.*`（**无 daphne**）→ 普通 WSGI `runserver`
- `backend/.env`：`PORT=8000`、`LOG_VIEWER_PORT=8120`（`backend/backend/settings.py:15` `load_dotenv()`；
  logviewer 侧 `backend/logviewer/logviewer/settings.py:26` 也 `load_dotenv(MAIN_DIR/".env")`）
- 本机系统代理已启用（`ProxyEnable=1`，`ProxyServer=https=http://127.0.0.1:31181`）；`HTTP_PROXY` 等环境变量为空

**未发现 P0**。共 11 条：P1×2、P2×5、P3×4。核心结论：**提交要修的"一次 Ctrl+C 全停"在真机按键路径上是成立的**
（见"已验证无问题"），但**兜底清理工具 `stop-dev.bat` 用"按端口无差别强杀 + 按 PID 无校验强杀"实现，
存在误杀用户其他服务的严重风险**；且启动期的 Ctrl+C、部分失败的上报、`.env` 端口解析三处仍有真实缺陷。

### 已验证无问题（审计提纲中显式点名、但检查后不构成缺陷的项）

| 提纲关注点 | 实测/核对结论 |
|---|---|
| 端口检测方式 | `dev.py:120-128` 用 connect 探测（非 bind），规避了 TIME_WAIT/SO_REUSEADDR 误判；副作用见 D-04 |
| 就绪轮询是否有上限 | 有：`wait_ready` 用 `deadline = time.time() + probe_seconds` 墙钟上限（24/30/24s），不会死循环 |
| 健康检查 URL | 三个都正确：`/api/health`（`backend/backend/urls.py:17`）、`/`（Vite）、`/api/health`（`logviewer/logviewer/urls.py:17`，无尾斜杠一致） |
| `CREATE_NEW_PROCESS_GROUP` + `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)` 是否真能打到子进程组 | 能。实测：以 venv redirector 的 PID 为 group id 发 CTRL_BREAK，**redirector 后面的真解释器（孙进程）**收到事件、Python 层 handler 执行、`finally`+`atexit` 均运行、退出码 0 |
| Vite 的 readline 会不会把控制台改成 raw 模式、导致整个控制台 Ctrl+C 不再产生 CTRL_C_EVENT | **不会**。实测（真实新控制台 + `CONIN$`，完全复刻 `vite/dist/node/chunks/dep-*.js:54321` 的 `readline.createInterface({ input })` 调用）：`isTTY=true / isRaw_before=false / isRaw_after=false` → 未调用 `setRawMode(true)`，`ENABLE_PROCESSED_INPUT` 保持置位，Ctrl+C 仍会变成 CTRL_C_EVENT 投给监管进程 |
| 是否会按进程名误杀 | `dev.py` 自身不会（只对自己 `Popen` 的 PID 操作，`svc.pid` 来自活着的 `Popen`）；**`stop-dev.bat` 会**，见 D-01/D-02 |
| venv 解释器路径解析 | `backend/.venv/Scripts/python.exe` 存在性已校验（`dev.py:251`），Windows 路径写法正确；带空格/中文的路径在三个文件的引用处都加了引号（`start-dev.bat:27,29,33,37,43`、`dev.py` 全部走 `list[str]` argv 不走 shell） |
| 中文控制台编码 | `dev.py:77-88` 同时做了 `SetConsoleOutputCP(65001)` + `sys.stdout/stderr.reconfigure("utf-8", errors="replace")`，并给子进程注入 `PYTHONUNBUFFERED/PYTHONIOENCODING`；无 emoji、无 UnicodeEncodeError 风险（反证：本次一个未做 reconfigure 的临时脚本在 GBK 控制台打印 `²` 直接 `UnicodeEncodeError` 崩了） |
| 重复启动 | `dev.py:263-266` 先探端口并给出明确指引后退出（码 1），不会两个实例同时拉服务 |
| 关日志文件句柄 | 三个子进程不落日志文件（输出直连控制台，文档与实现一致），不存在句柄泄漏面 |

## 缺陷清单

### [P1] D-01 `stop-dev.bat` 的"端口兜底"会无差别强杀任何监听 8000/5173/8120 的进程，且 `dev.py` 的错误提示正好引导用户去跑它

- 位置：`scripts/stop-dev.bat:24`（配合 `scripts/dev.py:264`）
- 代码：
```bat
for %%P in (8000 5173 8120) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r ":%%P .*LISTENING"') do (
    taskkill /PID %%a /T /F >nul 2>nul
    if not errorlevel 1 set "KILLED=1"
  )
)
```
```python
# scripts/dev.py:263-265
    if busy:
        error("Port already in use: %s" % ", ".join(busy))
        error("Another dev instance is probably still alive. Run scripts\\stop-dev.bat, then retry.")
```
- 触发条件：本机上**任何**进程监听 `8000 / 5173 / 8120` 中任一个端口，然后用户执行 `scripts\stop-dev.bat`
  （README/`stop-dev.bat:4-5` 明确教用户在"窗口被 X 关掉"时用它；`dev.py` 在端口占用时也直接建议它）。典型场景：
  - Docker Desktop 发布端口（`docker run -p 8000:8000` → `com.docker.backend` 监听 `0.0.0.0:8000`）；
  - 用户另一个前端项目的 Vite（5173 是 Vite 默认端口）或另一个 Django/Flask/uvicorn（8000）；
  - 任何占用 8120 的本地小工具。
  这里是"按端口找进程"，且**没有任何身份校验**（不比对进程名、不比对命令行是否属于本项目、不比对 PID 文件）。
- 后果：`taskkill /T /F` 把命中 PID 的**整棵进程树**强杀。用户其他服务（可能是有数据的服务）被瞬间杀死，未落盘的写/未提交事务丢失；
  杀掉 Docker Desktop 的后端进程还会连带影响其它容器/端口转发。更糟的是这条路径由本仓库的报错文案主动推荐，用户会认为"这是标准清理动作"。
- 修复建议：
  1. 端口兜底只在"PID 文件缺失且端口占用"时启用，并且**先输出候选**（PID + 映像名 + 命令行）让用户确认，不要默认 `/F` 直杀；
  2. 命中后加身份白名单：映像名必须是 `python.exe`/`node.exe`，且命令行包含本项目根路径（`wmic process where processid=<pid> get commandline` 或 PowerShell `Get-CimInstance Win32_Process`），不满足则只打印提示；
  3. 保留 `--yes`/`/FORCE` 开关给脚本自动化调用，默认交互确认；
  4. `dev.py:265` 的提示改为"先确认 8000/5173/8120 的占用者是不是本项目再清理"，避免误导。

### [P1] D-02 PID 文件无身份校验 + 非优雅退出不清理 → PID 被系统回收后误杀无关进程树

- 位置：`scripts/dev.py:273`、`scripts/dev.py:391`（清理只在 `finally`）、`scripts/stop-dev.bat:16`
- 代码：
```python
# scripts/dev.py:273-278
def _write_pid_file(supervisor_pid: int, services: list[Service]) -> None:
    try:
        lines = [str(supervisor_pid)] + [str(s.pid) for s in services if s.pid]
        PID_FILE.write_text("\n".join(lines) + "\n", encoding="ascii")
    except OSError as exc:
        warn(f"Cannot write {PID_FILE}: {exc}")
```
```bat
REM scripts/stop-dev.bat:16-22
if exist "%PIDFILE%" (
  for /f "usebackq tokens=*" %%p in ("%PIDFILE%") do (
    taskkill /PID %%p /T /F >nul 2>nul
    if not errorlevel 1 set "KILLED=1"
  )
  del /q "%PIDFILE%" >nul 2>nul
)
```
- 触发条件：`%TEMP%\gipfel-dev.pids` 里只有 4 个裸 PID（监管进程自身 + 三个子进程），**没有映像名、没有创建时间、
  没有项目标识**。任何"非优雅退出"都不会删这个文件（`_remove_pid_file()` 只在 `finally`，`dev.py:391-393`）：
  关窗 X、任务管理器结束任务、`taskkill`、断电/崩溃 —— 而这正是脚本注释与 README 主动承认的用法场景
  （`stop-dev.bat:4-5`"Use this when the Gipfel Dev window was closed with the X button"）。此后机器继续运行数小时，
  Windows 会把这些 PID 分配给新进程（同一登录会话内 PID 复用很常见），用户再执行 `stop-dev.bat`。
- 后果：`taskkill /PID <已被复用的 PID> /T /F` 强杀**完全无关**的进程树（例如用户自己的另一个 Python 服务、
  编辑器的 node 进程、构建工具），且失败时脚本仍打印 `[OK] Dev services stopped.`（见 D-10），故障无法归因。
- 修复建议：
  1. 落盘时写入可校验身份信息：`pid, image_name, creation_time(FILETIME), executable_path/命令行片段`，再加一个随机 nonce；
  2. `stop-dev.bat` 杀之前逐条校验（PowerShell: `Get-Process -Id` / `Get-CimInstance Win32_Process` 的 `Name`+`CommandLine`+`CreationDate`），身份不符**只告警不杀**；
  3. `dev.py` 启动时先加载旧 PID 文件，逐条校验：属于本项目且已死 → 清理；身份不符 → 删除并警告，绝不 kill；
  4. PID 文件路径带上项目标识（如项目根路径哈希），避免同一 `%TEMP%` 下多个 checkout 共用一份记录；
  5. `del /q "%PIDFILE%"` 只在确认清理完成后执行，否则会丢掉唯一线索。

### [P2] D-03 启动探针期间（最长 ~78s）Ctrl+C 不产生任何反应，用户只能关窗

- 位置：`scripts/dev.py:361`（顺序探针）、`scripts/dev.py:195`（探针循环不检查停止标志）、`scripts/dev.py:325`（handler 只置位）
- 代码：
```python
# scripts/dev.py:361-365
        for svc in services:
            svc.wait_ready()

        print()
        ok("All services started.")
```
```python
# scripts/dev.py:195-208（节选）
    def wait_ready(self) -> bool:
        info(f"Probing {self.name} {self.url} ...")
        deadline = time.time() + self.probe_seconds
        while time.time() < deadline:
            if _probe(self.url):
                ok(f"{self.name} ready")
                return True
            if not self.alive():
```
```python
# scripts/dev.py:325-327
def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _stop_requested
    _stop_requested = True
```
- 触发条件：三个服务在 `time.sleep(1)` 后逐个探测，`probe_seconds` 分别为 24 / 30 / 24（`dev.py:225,232,245`），
  最坏情况是 3s + 78s 的启动窗口；典型触发是后端 DB 未就绪/迁移未跑（`/api/health` 一直 500）或端口被 Hyper-V 排除范围占住
  导致子进程起不来但也不退出。用户在这个窗口里按 Ctrl+C。
- 后果：SIGINT 被 `_handle_signal` 吃掉（只置 `_stop_requested=True`，不抛 KeyboardInterrupt），而 `wait_ready` 与主循环都不看这个标志，
  所以**屏幕上不打印任何东西、进程也不退出**，看起来正是"Ctrl+C 又没用了"（本提交要修的现象）。用户随后关窗（X）→ `finally` 不执行 →
  三个已启动的子进程残留、PID 文件残留，接下来只能走 D-01/D-02 那两个危险兜底。
- 修复建议：`wait_ready` 每轮检查 `_stop_requested` 并立即 `return False`；主循环入口先判断标志；收到信号后立刻打印
  `Ctrl+C received. Stopping ...` 再进入清理（给用户即时反馈）；三个服务可并行探测（`threading` 或交错轮询）把最坏启动窗口从 78s 降到 ~30s。

### [P2] D-04 部分服务启动失败被无条件上报为"全部启动成功"，退出码也看不出失败

- 位置：`scripts/dev.py:362`（返回值被丢弃）、`scripts/dev.py:364`（无条件成功文案）、`scripts/dev.py:307`（停止侧同类问题）
- 代码：
```python
# scripts/dev.py:361-365
        for svc in services:
            svc.wait_ready()          # 返回值被丢弃：False 也无所谓

        print()
        ok("All services started.")
```
```python
# scripts/dev.py:307-319（节选）
    for svc in services:
        if not svc.alive():
            continue
        warn(f"{svc.name} did not exit in {SHUTDOWN_GRACE_SECONDS:.0f}s, force killing ...")
        if IS_WINDOWS:
            _force_kill_tree(svc.pid)
        else:
            svc.proc.kill()
        try:
            svc.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    ok("All services stopped.")
```
- 触发条件（任一即可）：
  1. `backend/.env` 里把 `LOG_VIEWER_PORT` 写成 `8000`（与 Django 同端口）：`_check_preconditions` 只在启动前对
     同一个 `(host, port)` 检查一次且不查重（`dev.py:261-262`），此时两个端口都"空闲"→ 通过；Django 先绑 8000，
     LogViewer 的 `runserver` 随即报 "That port is already in use" 退出；
  2. 8000 落在 Hyper-V/WSL 的 `netsh int ipv4 show excludedportrange` 排除区间：connect 探测失败（判定"空闲"）但 bind 报 WSAEACCES；
  3. `/api/health` 因 DB/迁移问题返回 500：`wait_ready` 打印 "not responding after 24s (continuing anyway)" 后继续；
  4. Vite 静默换端口（见 D-07）；
  5. 停止侧：`taskkill` 因权限/杀进程失败、或端口仍在 TIME_WAIT/未被释放，也照样打印 `All services stopped.`。
- 后果：用户看到 `All services started.` 和三个 URL，实际只有 2/3 个服务在跑（或某个服务其实在别的端口），
  排障方向被这句话带偏；`main()` 最终 `return 0`（`dev.py:390`），包装脚本/CI 无法据退出码判定降级启动。
- 修复建议：`wait_ready` 的结果收集起来（`ready = all(svc.wait_ready() for svc in services)`），有失败时打印明确失败清单，
  且停止侧在 `taskkill` 之后**复验**（进程已死 + 端口已释放，用 `_port_in_use` 复查）才打印 `All services stopped.`；
  失败时 `return 2`（区分正常 Ctrl+C 退出 0）；`_check_preconditions` 对端口列表去重并检测重复端口。

### [P2] D-05 启动中途失败时，"清理"被推迟到用户按 Enter 之后；此时 Django 已占着 8000 且 PID 文件还没写

- 位置：`scripts/dev.py:354`（`_pause_if_console()` 在 `finally` 之前）、`scripts/dev.py:359`（PID 文件在此才写）
- 代码：
```python
# scripts/dev.py:349-359
    try:
        for svc in services:
            info(f"Starting {svc.name} -> {' '.join(svc.argv[1:])}")
            try:
                svc.start()
            except OSError as exc:
                error(f"Cannot start {svc.name}: {exc}")
                _pause_if_console()
                return 1
            time.sleep(1)
        _write_pid_file(os.getpid(), services)
```
- 触发条件：中途 `OSError`（典型：`node` 不在 PATH 上 → Vite 的 `Popen` 抛 `FileNotFoundError`；或磁盘/权限问题）
  → 第 356 行进入 `input("\nPress Enter to exit ...")` **阻塞**，第 357 行的 `return 1` 要等用户回车才触发 `finally`
  （`dev.py:391`）里的 `_stop_services`。
- 后果：阻塞期间已经启动的 Django（可能还有 LogViewer）继续占着 8000/8120，用户若此时按提示的直觉直接关窗（X），
  `finally` 永不执行 → 孤儿 Django 残留；而 PID 文件只在三个服务全部启动成功后才写（`dev.py:359`），
  此时根本没写过 → `stop-dev.bat` 只能落到 D-01 的按端口无差别强杀路径（可能误杀别人的 8000）。同时用户若另开一个窗口再跑 `start-dev.bat`，
  会收到"Port already in use"，被再次引导去执行 `stop-dev.bat`。
- 修复建议：先做清理再暂停：`error(...)` → `_stop_services(services)`（或直接把 `_pause_if_console()` 移进 `finally` 末尾），
  让"按 Enter"只影响窗口是否保留；把 PID 文件改成"每启动一个服务就追加"而不是最后一次性写，保证任何失败路径都有记录可清理。

### [P2] D-06 `LOG_VIEWER_PORT` 在同一提交里存在三套解析：`dev.py` 手写解析 / 应用侧 python-dotenv / `stop-dev.bat` 硬编码 8120

- 位置：`scripts/dev.py:108`（手写解析）、`scripts/stop-dev.bat:24`（硬编码 8120）
- 代码：
```python
# scripts/dev.py:108-114
        for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line.startswith("LOG_VIEWER_PORT="):
                continue
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value.isdigit():
                port = int(value)
```
```bat
REM scripts/stop-dev.bat:24-28
for %%P in (8000 5173 8120) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r ":%%P .*LISTENING"') do (
    taskkill /PID %%a /T /F >nul 2>nul
    if not errorlevel 1 set "KILLED=1"
  )
)
```
- 触发条件（本次用同一份输入对两个解析器做了对照，`dev.py` 侧为其 108-114 行的逐行复刻）：

  | `.env` 内容（同为合法 dotenv 写法） | `dev.py` 结果 | `python-dotenv` 结果 |
  |---|---|---|
  | `LOG_VIEWER_PORT=8120` | 8120 | 8120 |
  | `LOG_VIEWER_PORT = 8121`（等号两侧空格） | **8120（静默回退默认值）** | 8121 |
  | `LOG_VIEWER_PORT=8121  # 注释`（行尾注释） | **8120（静默回退默认值）** | 8121 |

  应用侧真源是 dotenv：`backend/backend/settings.py:15 load_dotenv()`、`backend/logviewer/logviewer/settings.py:26`
  `load_dotenv(MAIN_DIR/".env")` → `settings.LOG_VIEWER_PORT`（`backend/backend/settings.py:143`），
  并据此生成前端"日志查看器"跳转地址（`backend/apps/auth/views.py:96,109,113`）。
- 后果：用户按注释习惯把 `.env` 写成 `LOG_VIEWER_PORT = 8121` 或加行尾注释后：`dev.py` 绑 **8120**（并只探测 8120），
  而 `/api/version` 下发的 `log_viewer_url` 指向 **8121** → 前端"日志查看器"按钮打开的是没人监听的端口；
  同时 `stop-dev.bat` 的兜底只认 8120，一旦自定义端口，D-01/D-02 的兜底就漏掉 LogViewer（漏杀本身无害，但会留下占端口的残留进程，
  让 `start-dev.bat` 反复报 "Port already in use"，用户于是继续重跑兜底脚本，风险落在 D-01）。
- 修复建议：不要手写 `.env` 解析 —— 直接 `from dotenv import dotenv_values; int(dotenv_values(BACKEND/".env")["LOG_VIEWER_PORT"])`
  （venv 里已装 python-dotenv，与应用侧完全同源）；解析失败/非法时**显式报错退出**而不是静默回退默认值；
  `stop-dev.bat` 不要硬编码 8120，改为从 `.env` 读取或从 PID 文件记录里取端口。

### [P2] D-07 Vite 未加 `--strictPort`，端口被抢时静默换到 5174，脚本却继续按 5173 报成功

- 位置：`scripts/dev.py:229`（未传 `--strictPort`）、`scripts/dev.py:262`（只在启动前查一次端口）
- 代码：
```python
# scripts/dev.py:227-233
        Service(
            name="Vite",
            argv=[node, str(VITE_JS), "--host", VITE_HOST, "--port", str(VITE_PORT)],
            cwd=FRONTEND,
            url=f"http://{VITE_HOST}:{VITE_PORT}/",
            probe_seconds=30,
        ),
```
```python
# scripts/dev.py:261-262
    ports = [(BACKEND_HOST, BACKEND_PORT), (VITE_HOST, VITE_PORT), (LOGVIEWER_HOST, logviewer_port)]
    busy = [f"{host}:{port}" for host, port in ports if _port_in_use(host, port)]
```
- 触发条件：`frontend/vite.config.ts:41` 是 `strictPort: false`，Vite 的语义是"端口被占就自动 +1"。
  `dev.py` 只在启动前（`_check_preconditions`）connect 探一次 5173，随后还要启动 Django、`sleep(1)` 才轮到 Vite ——
  这 2~3 秒窗口内如果别的进程（另一个 Vite/Electron/预览工具/另一个 checkout）占了 5173，Vite 会安静地绑 5174。
- 后果：`wait_ready` 对 5173 探测 30s 后打印 "not responding after 30s (continuing anyway)"，随后按 D-04 仍然打印
  `All services started.` 和 `Frontend (Vite): http://127.0.0.1:5173`。用户按提示打开 5173 时访问到的是**别人的服务**，
  在极端情况下会把本项目的账号密码输进陌生页面；真正的前端在 5174 没人知道。
- 修复建议：给 Vite 传 `--strictPort`（端口被占直接失败，配合 D-04 的失败上报给出可诊断错误）；
  启动后再复验一次"5173 是不是我们拉起的那个 Vite"（例如探测 Vite 特有的响应头/`/@vite/client`），失败则明确报错。

### [P3] D-08 端口字符串只做了 `isdigit()` 判断，缺类型与范围校验（0 / >65535 / Unicode 数字）

- 位置：`scripts/dev.py:112`
- 代码：
```python
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value.isdigit():
                port = int(value)
```
- 触发条件与后果（均在 3.14.6 实测）：
  1. `LOG_VIEWER_PORT=0` → `"0".isdigit()==True`，端口 0 通过全部检查：`_port_in_use(host,0)` 永远 False（"空闲"），
     `runserver 127.0.0.1:0` 绑随机端口，`_probe("http://127.0.0.1:0/api/health")` 永远失败 → 探针白等 24s，
     打印的 LogViewer URL 是错的，随后按 D-04 报"全部成功"；
  2. `LOG_VIEWER_PORT=99999`（>65535）→ 同样通过校验，子进程在 `bind` 时抛 `OverflowError` 崩溃 → 又是"启动失败但报成功"；
  3. `LOG_VIEWER_PORT=²`（上标数字）→ `"²".isdigit()` 为 **True** 但 `int("²")` 抛 `ValueError: invalid literal for int()
     with base 10: '²'` → **该异常在 `main()` 的 `try` 之外（`dev.py:333`）抛出，dev.py 直接带 traceback 退出，
     既没有 `_pause_if_console` 也没有任何提示**；双击 `start-dev.bat` 时表现为窗口一闪而过（用户看不到原因）。
     （`"٨١٢٠"` 这类阿拉伯-印度数字 `isdigit()` 也为 True 且 `int()` 能解析为 8120 —— 是另一个静默取奇怪值的路径。）
- 修复建议：用 `str.isascii() and value.isdecimal()` 或 `re.fullmatch(r"\d{1,5}", value)` + `1 <= port <= 65535` 校验；
  非法值直接 `error(...)` 并让 `main()` 返回非 0（同时保证该解析在可 pause 的路径内），不要静默回退默认值。

### [P3] D-09 健康检查走 `urllib`，会读取系统/环境代理；旧脚本用的 `curl` 不读注册表（[待确认]）

- 位置：`scripts/dev.py:131`
- 代码：
```python
def _probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 400
    except Exception:  # noqa: BLE001
        return False
```
- 触发条件：`urllib.request.getproxies()` 在 Windows 上 = 环境变量优先、否则读 IE/系统代理注册表。
  本机实测 `getproxies() == {'https': 'http://127.0.0.1:31181'}` 且 `proxy_bypass('127.0.0.1:5173') == True`
  （注册表代理只配了 `https=`，且 `ProxyOverride` 含 `127.*.*.*`/`<local>`）→ **当前 http:// 探测直连，未受影响**。
  但两种常见输入会让探测走代理：① 开发者在终端里设了 `HTTP_PROXY`/`ALL_PROXY`（`getproxies_environment()` 优先于注册表）；
  ② 系统代理是 `ProxyServer=host:port`（无协议前缀）且 `ProxyOverride` 未包含回环。
- 后果：健康检查实际请求打给代理 → 代理回 403/502 或超时 → `_probe` 恒 False → 24s/30s 后打印
  "not responding (continuing anyway)"，服务其实完全正常；叠加 D-04 就变成"脚本说没就绪、却又说全部启动成功"。
  **标记 [待确认]**：具体后果取决于用户的代理配置，本机当前配置下复现不了。
- 修复建议：探测显式禁用代理 —— `urllib.request.build_opener(urllib.request.ProxyHandler({}))`（或改回 `curl --noproxy '*'`），
  并让 `_probe` 在异常时把失败原因（超时/连接被拒/HTTP 状态）带上，便于区分"服务没好"与"网络路径不对"。

### [P3] D-10 两个 .bat 的退出码恒为 0，真失败无法被自动化/包装脚本识别

- 位置：`scripts/start-dev.bat:43`、`scripts/stop-dev.bat:31`
- 代码：
```bat
REM scripts/start-dev.bat:42-44
echo [INFO]  Starting Gipfel dev services in a new window ("Gipfel Dev") ...
start "Gipfel Dev" "%PY%" "%~dp0dev.py"
exit /b 0
```
```bat
REM scripts/stop-dev.bat:31-36
if "%KILLED%"=="1" (
  echo [OK]    Dev services stopped.
) else (
  echo [INFO]  No dev service was running.
)
exit /b 0
```
- 触发条件：`start` 只是拉起新窗口，`dev.py` 随后可能因端口占用/依赖缺失立即 `return 1`，但 `start-dev.bat` 已经
  `exit /b 0`；`stop-dev.bat` 无论是否真的杀掉进程、`taskkill` 是否全部失败，最后都是 `exit /b 0`。
- 后果：任何包装脚本/CI/一键初始化流程（仓库里已有多个 `.sh`/`.bat` 编排）都无法区分"启动成功"和"启动失败"；
  与 D-04 叠加后，"失败但退出码 0"成为系统性问题（`dev.py` 的 1 也被 bat 吞掉）。
- 修复建议：`start-dev.bat` 可用"等待新窗口写就绪标记"的方式（`dev.py` 成功启动后写 `%TEMP%\gipfel-dev.ready`，bat 轮询数秒）
  再决定 `exit /b`；`stop-dev.bat` 记录并复验结果（进程数/端口占用）后返回 0/1。

### [P3] D-11 注释与提交信息里的"优雅停止"与实测不符：无 SIGBREAK 处理器的子进程是被**立即硬终止**；阻塞在单次长 C 级等待时事件会被吞掉

- 位置：`scripts/dev.py:7`（设计声明）、`scripts/dev.py:296`（发送处）、`scripts/dev.py:310`（8s 兜底）
- 代码：
```python
# scripts/dev.py:7-9
    本进程自己当监管者：子进程依旧以 CREATE_NEW_PROCESS_GROUP 启动，退出时由
    我们**定向**向每个子进程组发送 CTRL_BREAK_EVENT（Windows 上唯一可定向到
    进程组的控制台事件，实测子进程能收到），超时再用 `taskkill /T /F` 兜底。
```
```python
# scripts/dev.py:295-301
        delivered = _send_ctrl_break(svc.pid) if IS_WINDOWS else False
        if delivered:
            continue
        try:
            svc.proc.send_signal(signal.SIGTERM if IS_WINDOWS else signal.SIGINT)
        except OSError:
            pass
```
- 实测（Python 3.14.6 / Windows，隔离探针，未执行被审脚本）：
  1. `signal.getsignal(signal.SIGBREAK)` → `<Handlers.SIG_DFL: 0>`（venv 里没有默认的 Python 级处理器；SIGINT 才有 `default_int_handler`）；
  2. 子进程无自装处理器时，CTRL_BREAK 由 CRT/OS 默认动作**立即终止**：实测 `rc=0xC000013A`(3221225786)，
     且 `finally`/`atexit` 均未执行，等价于 `taskkill /F`；
  3. 子进程自装 `signal.signal(SIGBREAK, h)` 且主线程在跑 Python 代码（Twisted reactor / 轮询循环就是这种）→ handler 正常执行、
     `finally`+`atexit` 运行、退出码 0（**这也是 daphne 那条"Killed 0 pending application instances"日志成立的原因**）；
  4. 子进程自装处理器但主线程阻塞在**单次长时间 C 级等待**（如 `time.sleep(60)`）→ 3s 内既不退出也不执行 handler，
     事件被静默吞掉，只能等 `SHUTDOWN_GRACE_SECONDS=8` 后 `taskkill /T /F`。
  本项目对应关系：主后端含 `daphne`（`backend/backend/settings.py:223`）→ 走第 3 种，优雅退出成立；
  **LogViewer 侧 `INSTALLED_APPS` 只有 `django.contrib.*`（无 daphne）→ 普通 WSGI `runserver`，无任何 SIGBREAK 处理器 → 走第 2 种（硬终止）**。
- 后果：`dev.py:7-9` 与提交信息宣称的"优雅停止"只对 daphne 成立；LogViewer（以及未来任何 SIG_DFL 子进程）实际是被
  OS 默认动作硬杀，in-flight 请求被直接切断、`atexit` 清理不执行（本项目里影响有限，因为子进程不写日志文件、SQLite 也具备崩溃一致性）；
  第 4 种情形若出现在某个子进程上，则每次停止都必然是"等满 8s + taskkill /F"，与"优雅"相反。
- 修复建议：把注释/文档改为如实描述（"CTRL_BREAK 对自装处理器的子进程优雅；其余为立即终止"）；
  子服务需要真正优雅时，由子进程显式处理 SIGBREAK（例如给 LogViewer 加一段 `signal.signal(SIGBREAK, ...)` 包装 `runserver` 的关闭）；
  或者把停止策略改为"先 CTRL_BREAK，再短超时后按需 `/T /F`"，并把实际走了哪条路径打印出来（可诊断性）。

## 存疑/待确认

1. **[待确认] 非 Windows 路径只是"看起来能跑"**：`dev.py:187` 在 POSIX 上 `creationflags=0`（不建进程组、不 `start_new_session`），
   `_send_ctrl_break` 直接返回 False，于是 `dev.py:299` 只对**直接子进程**发 SIGINT/SIGTERM —— 后代进程（node 拉起的 esbuild 服务、
   venv redirector 后面的真解释器）收不到信号。本脚本注释与用法均面向 Windows，故未单列缺陷，但若有人在 Linux/macOS 上按
   `python scripts/dev.py` 使用，会出现"监管退出但 esbuild/子进程残留"。建议在非 Windows 上显式 `start_new_session=True` + `os.killpg`。
2. **[待确认] 子进程与监管进程共用同一个控制台 stdin**：三个子进程未做 `stdin=DEVNULL`，Vite 的
   `readline.createInterface({input: process.stdin})` 与 `dev.py:96` 的 `input("Press Enter to exit ...")` 会抢同一个控制台输入。
   D-05 那条路径（中途启动失败 → pause）下如果 Vite 已经起来了，用户按 Enter 可能被 Vite 的行读取器吞掉，导致 pause 永不返回、
   清理永不执行。复现与概率未实测（正常的 Ctrl+C 不受影响，因为已实测 Vite 不会把控制台切到 raw 模式）。
3. **[待确认] 关窗（X）后子进程是否残留，取决于 Windows 控制台关闭语义**：三个子进程与监管进程共享同一个控制台，
   控制台被销毁时系统通常会给它们发 CTRL_CLOSE_EVENT 并终止。提交信息与脚本注释都断言"X 关窗会残留"，本次未做真机验证
   （不做破坏性验证）。这不改变 D-01/D-02 的判定：只要存在任何"PID 文件残留且进程已换人"的路径，按端口/按 PID 的无校验强杀就是风险。
4. **[待确认] `PID_FILE` 与 `%TEMP%` 是否总是同一个目录**：`dev.py:56` 用 `tempfile.gettempdir()`（受 `TMPDIR/TEMP/TMP` 影响），
   `stop-dev.bat:13` 用 `%TEMP%`。常规 cmd 启动下两者一致；若用户在设置了 `TMPDIR` 的 shell 里启动 dev.py，
   而之后在普通 cmd 里跑 stop-dev.bat，可能读不到 PID 文件（退化为 D-01 的端口兜底）。
5. **[待确认] 2s 探针超时对冷启动服务是否偏紧**：Vite 首次启动会做依赖预构建（esbuild），首个请求可能 >2s；
   当前失败会重试（1s 间隔、最长 30s），所以只是噪声，但配合 D-04 的无条件成功文案会放大误导。
6. `start-dev.bat:37` 校验的是 `node_modules\.bin\vite.cmd`，而 `dev.py:254` 校验的是 `node_modules\vite\bin\vite.js`，
   两者不是同一路径；本次检查两者都存在，且 `npm` 安装时同源生成，故未单列为缺陷，仅记录不一致。
