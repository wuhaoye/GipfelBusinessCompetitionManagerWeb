# Debian 13 真机测试报告：该程序在真实 Linux 下的问题

- 受测对象：**已推送到远端的 HEAD `2f39918`**（`bugfix-merged`；`git archive` 干净导出，不含工作区在途改动）
- 测试机：VMware `192.168.56.129`，Debian GNU/Linux 13 (trixie)，内核 6.12.107+deb13-amd64
- 运行时：`/opt/gipfel/backend/.venv`（Python 3.13.5）、Node v20.20.2、GNU bash 5.3.9、真 systemd
- 测试树：`/home/wuhaoye/gipfel-head`（713 文件，`.sh` 全 LF）；结果日志：`/home/wuhaoye/gipfel-test-results`
- 执行身份：普通用户 `wuhaoye`（Python/Node 用例）＋ root（systemd/诊断/迁移校验）
- 对比基线：同一 HEAD 在 **Windows** 上跑 `tests/fix_verify/scripts/*`（21/21 全绿）

---

## 1. 结论摘要

**程序本体在真实 Linux 上表现良好**：Django 两套 452 个用例、watcher 223 个用例、36×3 条部署断言、14 项迁移校验、systemd 单元校验、前端 16 个用例——要么全绿，要么差异全部落在**测试/开发工具链的 Windows 硬编码**上。

**发现 3 个只在 Linux 下暴露的真实缺陷，全部位于测试/开发工具，不影响生产部署**：

| # | 级别 | 位置 | 现象 | 影响 |
| --- | --- | --- | --- | --- |
| 1 | **P1** | 10 个测试文件 + `scripts/dev.py:51` | 硬编码 `backend/.venv/Scripts/python.exe`（Windows venv 布局） | Linux 上 **2 个测试文件直接报错**（`FileNotFoundError`），另 4 个静默降级 |
| 2 | **P1（含危险）** | `test_b01_stop_safety.py` + `scripts/dev.py:427-433` | 用例桩替换 `_force_kill_tree`，而 POSIX 分支走 `os.kill` | Linux 下用例必然失败；**以 root 运行测试会真的 SIGTERM PID 1（systemd）/ PID 2**（已拦截取证） |
| 3 | P2 | `test_x17_db_safety.py` | 依赖 `backend/db.sqlite3` 存在 | 全新 clone（无 dev 库）下必失败 |

另有三项"可移植性缺口"（前端验证脚本 `bundle.ps1`、`dev.py` 的 venv 路径、测试不自带 `.env`），见 §4。

---

## 2. 结果总表

| 套件 | 结果 | 说明 |
| --- | --- | --- |
| `manage.py test apps` | ✅ **Ran 153 tests OK** | 需先提供 `backend/.env`（见 §3.6） |
| `manage.py test tests_fix_verify` | ✅ **Ran 299 tests OK** | 同上 |
| `tests/fix_verify/scripts/test_*.py` | ⚠️ **19/21 文件通过**（191 用例，跳过 9） | 失败：`test_b01_stop_safety`、`test_x17_db_safety` |
| `tests/fix_verify/watcher/test_*.py` | ⚠️ **25/26 文件通过**（223 用例，跳过 11） | 失败：`test_cw21_exit_codes` |
| `tests/big_number_smoke.py` | ✅ PASS | rc=0 |
| `tests/sqlite_decimal_roundtrip.py` | ✅ PASS | rc=0 |
| `contract_watcher/selftest.py` | ✅ **TOTAL PASS=11 FAIL=0** | |
| `tests/deploy_public_ip_test.sh`（repo 根 / tests / scripts 三种 cwd） | ✅ **PASS=36 FAIL=0 ×3** | 真 Linux bash 下三种 cwd 口径一致 |
| `scripts/verify-migration.sh /opt/gipfel` | ✅ 通过 14 / 失败 0 / 告警 0 | 对**真实部署**校验 |
| `tests/gipfel-logviewer-diag.sh` / `tests/https-443-diag.sh` | ✅ rc=0 | 诊断输出正常 |
| `systemd-analyze verify gipfel.service gipfel-logviewer.service` | ✅ rc=0，无输出 | 指令级无错 |
| 全部 `.sh` 的 `bash -n` | ✅ **9/9 OK**，CRLF=0 | |
| 前端 `tests/fix_verify/frontend/*.mjs` | ✅ **16/16 rc=0**（换成 Linux esbuild 后） | 见 §4.5：原文档命令在 Linux 上跑不了 |
| `manage.py check --deploy` | ℹ️ 4 条 W 警告 | HSTS / SSL 重定向 / 安全 Cookie——纯 HTTP 部署的预期结果，非缺陷 |

**Windows ↔ Linux 差异只有 3 个文件**：`test_b01_stop_safety`、`test_x17_db_safety`、`test_cw21_exit_codes`（Windows 上均通过）。

---

## 3. 真实 Linux 下暴露的问题（取证）

### 3.1【P1】10 个测试文件硬编码 Windows venv 路径

```python
# tests/fix_verify/scripts/test_x17_db_safety.py:39 等多处
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
```

Linux 上的 venv 布局是 `.venv/bin/python`，于是：

```
FileNotFoundError: [Errno 2] No such file or directory:
  '/home/wuhaoye/gipfel-head/backend/.venv/Scripts/python.exe'
```

实测影响面（静态统计）：

| 文件 | PY 出现次数 | 是否真用于启动子进程 | Linux 结果 |
| --- | --- | --- | --- |
| `test_x17_db_safety.py` | 3 | 是（2） | **3 errors + 1 failure** |
| `test_cw21_exit_codes.py` | 3 | 是（2） | **2 errors** |
| `test_x18/x19/x20/x21` | 3/3/2/2 | 是（1） | 通过——因 `code_audit/_xNN_harness.py` 不在仓库而 `skipTest` |
| `test_x10/x22/x24/x25` | 1 | 否 | 通过（路径未被使用） |

> 仓库里 `code_audit/` 是非交付目录（未跟踪），因此那 4 个文件的"真实执行"断言在**任何干净 clone** 上都恒被跳过——属于覆盖面静默丢失，建议一并处理。

### 3.2【P1，含危险】`test_b01_stop_safety` 与 POSIX 分支耦合

`scripts/dev.py`：

```python
427:        if IS_WINDOWS:
428:            _force_kill_tree(int(pid))     # ← 测试桩替换的就是它
429:        else:
430:            try:
431:                os.kill(int(pid), signal.SIGTERM)   # ← POSIX 走这里，桩完全失效
```

用例 `test_stop_kills_only_verifiable_processes` 只桩了 `_force_kill_tree`，并断言 `killed == [1, 2]`。Linux 上实测（拦截 `os.kill`，**不发真信号**）：

```
IS_WINDOWS            = False
测试桩 _force_kill_tree 被调用 = []          ← 用例断言的就是它
实际 os.kill 目标      = [(1, 15), (2, 15)]   (SIGTERM=15)
返回码                = 0
```

两个后果：

1. **用例必然失败**（Windows 上通过），断言信息"只应停止身份可校验的进程，实际 []"会误导排查方向；
2. **危险**：桩数据用的是 PID 1/2/3，Linux 下真的发 SIGTERM。**若以 root 运行该套件，PID 1 就是 systemd**——`os.kill(1, SIGTERM)` 可能触发 init 的关机/重启流程（本次刻意以普通用户运行并拦截 `os.kill`，未造成影响）。测试套件不应依赖"以非 root 运行"这一隐含前提来保证安全。

### 3.3【P2】`test_x17_db_safety` 依赖开发库存在

```
AssertionError: unexpectedly None : 找不到 backend/db.sqlite3，无法验证
```

`db.sqlite3` 被 `.gitignore` 排除，`git archive HEAD` 出的干净树里没有它 → 全新 clone 上该用例必失败（与 Windows/Linux 无关，但会污染"真机测试"结论）。

### 3.4【P2】`scripts/dev.py` 硬编码 Windows venv → Linux 上开发启动器不可用

```python
40: IS_WINDOWS = os.name == "nt"                     # 文件本身是平台感知的
51: VENV_PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"   # ← 却写死 Windows
223/243: argv=[str(VENV_PYTHON), "manage.py", "runserver", ...]  # Django / 日志查看器
257-258: if not VENV_PYTHON.exists():
             error(f"{VENV_PYTHON} not found. Run scripts\\bootstrap-dev.bat first.")
```

Linux 上 `dev.py` 会以"找不到 `...\Scripts\python.exe`"失败，且补救提示指向 Windows 的 `bootstrap-dev.bat`。**生产部署不受影响**（systemd 单元用绝对路径 `.venv/bin/python`），受影响的是 Linux 开发者。

### 3.5【P2】前端验证工具链锁死 Windows

- `tests/fix_verify/frontend/bundle.ps1:26` 硬编码 `node_modules\@esbuild\win32-x64\esbuild.exe`，且是 PowerShell 脚本；
- 多个 `.mjs` 用法注释同样写死 `frontend/node_modules/@esbuild/win32-x64/esbuild.exe`。

**但代码本身没问题**：我在真机上把二进制换成 `node_modules/.bin/esbuild`、原样保留 `bundle.ps1` 的 alias/stub 参数后，**16/16 个用例全部通过**：

```
[OK] test_delete_confirm_message.mjs   PASS=8  FAIL=0
[OK] test_format_time.mjs              PASS=9  FAIL=0
[OK] test_response_envelope.mjs        PASS=11 FAIL=0
[OK] test_list_paging.mjs              PASS=9  FAIL=0
[OK] test_t01_est_amount / f12 / w02 / m01_layout / v09 / w08   rc=0
[OK] test_f06_logout_clears_memo（bundle+stub）  rc=0
[OK] test_m01_widget_packages_reload（bundle+stub）rc=0
[OK] test_t02 / w03 / v05 / contract_tech_nodes（自包含）rc=0
```

即：**只需把 `bundle.ps1` 里的 esbuild 路径改为按平台探测（或补一个 `bundle.sh`），这 16 个用例就能在 Linux 上跑**。

### 3.6【P3】干净 clone 缺 `backend/.env` → Django 套件整体起不来

```
RuntimeError: 环境变量校验失败:
  JWT_SECRET: JWT_SECRET is required
```

`settings.py` 的 fail-fast 是**正确设计**（生产必须有强密钥），但仓库不含 `.env`（gitignore），因此"clone → 跑测试"缺少一步。本次补一个仅测试用的 `.env`（`openssl rand -hex 32`）后，`apps` 与 `tests_fix_verify` 共 **452 个用例全绿**。建议在 `backend/README.md` 或测试入口显式说明，或让测试在缺失时自provision（如 `DJANGO_SECRET_KEY`/`JWT_SECRET` 的测试默认值 + 环境变量注入）。

### 3.7【P3】`check --deploy` 4 条警告

```
security.W004 SECURE_HSTS_SECONDS 未设置
security.W008 SECURE_SSL_REDIRECT 未设 True
security.W012 SESSION_COOKIE_SECURE 未设 True
security.W016 CSRF_COOKIE_SECURE 未设 True
```

本次是**纯 HTTP + 无域名**部署（`--public-ip`），未启用 HTTPS，四条警告属预期。启用 `--origin-cert`/HTTPS 后应复核是否需要对反向代理场景显式配置。

---

## 4. 建议修复（按性价比排序）

1. **测试解释器路径按平台解析**（一处 helper，10 个文件复用）：
   ```python
   # tests/fix_verify/_python.py（新增）
   from pathlib import Path
   def venv_python(repo: Path) -> Path:
       win = repo / "backend" / ".venv" / "Scripts" / "python.exe"
       posix = repo / "backend" / ".venv" / "bin" / "python"
       return win if win.exists() else posix
   ```
   并把 `test_x10/x17/x18/x19/x20/x21/x22/x24/x25/cw21` 的 `PY = ...` 改为调用它；找不到时给出明确 skip 理由（而不是让 6 处启动点抛 `FileNotFoundError`）。
2. **`test_b01` 与平台解耦**：或把断言改为"通过 `_force_kill_tree` **或** `os.kill` 之一停止"（mock 两者），并在 POSIX 下**绝不使用 1/2/3 这类系统 PID**（改用 999999+ 或 mock `IS_WINDOWS`）。这条最值得先做——它同时消除 root 下 SIGTERM PID 1 的风险。
3. **`test_x17` 去掉对 `backend/db.sqlite3` 的硬依赖**：缺失时构造一次性临时库，或 `skipTest("无开发库，跳过真实库校验")` 并保留其余断言。
4. **`scripts/dev.py`**：`VENV_PYTHON` 改为按平台探测（`Scripts/python.exe` → `bin/python`），错误提示同步按平台输出（Linux 指向 `bootstrap-dev` 的对应说明）。
5. **前端验证**：`bundle.ps1` 的 esbuild 改为平台探测（`@esbuild/<platform>-<arch>/esbuild[.exe]`，或直接 `node_modules/.bin/esbuild`），或新增等价的 `bundle.sh`；`.mjs` 注释里的 win32 路径同步更新。
6. **文档**：`backend/README.md` 增加"跑测试前先 `cp .env.example .env` 并填 `JWT_SECRET=$(openssl rand -hex 32)`"。

---

## 5. 复现方法

```bash
# 1) Windows 侧（本机）导出干净 HEAD 树
git archive --format=tar -o .tmp-test/head.tar HEAD
scp .tmp-test/head.tar wuhaoye@192.168.56.129:/tmp/          # 密码 123456
# 2) VM 侧解包
mkdir -p ~/gipfel-head && tar xf /tmp/head.tar -C ~/gipfel-head
# 3) 测试环境（干净 clone 缺 .env）
printf 'JWT_SECRET=%s\nDJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver\n' "$(openssl rand -hex 32)" > ~/gipfel-head/backend/.env
# 4) 跑套件（示例）
PY=/opt/gipfel/backend/.venv/bin/python
cd ~/gipfel-head/tests/fix_verify/scripts && $PY -m unittest test_b01_stop_safety -v   # Linux 失败
cd ~/gipfel-head/tests/fix_verify/scripts && $PY -m unittest test_x17_db_safety -v    # Linux 报错
cd ~/gipfel-head/tests/fix_verify/watcher && $PY -m unittest test_cw21_exit_codes -v  # Linux 报错
```

VM 上保留的证据：测试树 `~/gipfel-head`、逐用例日志 `~/gipfel-test-results/`（`scripts_*.log`、`watcher_*.log`、`django_*.log`）。

---

## 6. 本次未覆盖

- `tests/fix_verify/watcher/e2e_watcher_excel.py`、`e2e_watcher_backend.py`：前者需真实 Excel + xlwings（Windows），后者需可登录的后端与账号，本轮未跑。
- `test_cw33_gui_smoke`：11 个用例在无 DISPLAY 的 SSH 会话下全部 skip（Tkinter 无法建窗），GUI 冒烟未在真机执行。
- 生产 HTTPS/域名形态（`--origin-cert`、Cloudflare 回源）未在本轮范围。
