> 以下整段可直接粘贴为 GitHub Issue（标题为第一行 `title:` 后的内容）。
> **类型：功能请求 / 可移植性改进**（master 上 `dev.py` 明确写着"Windows 开发启动监管进程"，因此这不是"实现有 bug"，而是"Linux 开发环境缺一条可用路径"）。

---

**title:** `[dev] 新增 Linux 开发启动路径（scripts/dev.py 硬编码 Windows venv 与 taskkill，Linux 无法启动本地环境）`

## 环境

| 项 | 值 |
| --- | --- |
| 分支 / commit | `master` / `34013bb` |
| 受影响文件 | `scripts/dev.py:1`、`:41`、`:48`、`:148`、`:157`、`:217`、`:237`、`:251-255`、`:289-295` |
| 相关文件 | `scripts/bootstrap-dev.bat`（仓库内无 `.sh` 版本）、`deploy/README.md`（方案二明确写"Windows（仅开发）"） |

## 现状

`scripts/dev.py` 的定位就是 Windows 开发启动器：

```python
# scripts/dev.py:1
"""Windows 开发启动监管进程：Django(:8000) + Vite(:5173) + 日志查看器(:8120)。"""

# :48  ← venv 解释器路径写死 Windows 布局
VENV_PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"

# :217 / :237  ← Django 与日志查看器都用它拉起
argv=[str(VENV_PYTHON), "manage.py", "runserver", …]

# :251-252  ← 前置检查的补救提示也只指向 Windows 脚本
if not VENV_PYTHON.exists():
    error(f"{VENV_PYTHON} not found. Run scripts\\bootstrap-dev.bat first.")
```

停止进程的路径同样只在 Windows 成立：`:148` 用 `ctypes.windll.kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)`，`:157` 兜底 `taskkill /PID <pid> /T /F`。

## 问题

在 Linux/macOS 上（venv 布局是 `.venv/bin/python`）：

```
<repo>/backend/.venv/Scripts/python.exe not found. Run scripts\bootstrap-dev.bat first.
```

- Linux 开发者无法用仓库自带入口启动"后端 + 前端 + 日志查看器"三件套；
- 仓库也没有 `bootstrap-dev.sh`，README 只给 Windows 方案；
- **生产部署不受影响**（systemd 单元用的是 `.venv/bin/python` 绝对路径）。

## 期望结果

1. `VENV_PYTHON` 按平台解析（或直接用 `sys.executable` / `shutil.which("python")` 兜底）：

```python
def _venv_python(backend: Path) -> Path:
    win = backend / ".venv" / "Scripts" / "python.exe"
    posix = backend / ".venv" / "bin" / "python"
    return win if win.exists() else posix
```

2. 前置检查的补救提示按平台输出（Linux 指向 `bootstrap-dev.sh` 或 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`）；
3. 停止路径补 POSIX 分支（`os.killpg(pgid, SIGTERM)` 或按已记录的 PID + 身份校验逐个停止），或至少在非 Windows 下明确拒绝并给出替代命令；
4. 可选：新增 `scripts/bootstrap-dev.sh`（`python3 -m venv` + `pip install -r requirements.txt` + `npm ci`），与 `.bat` 对齐。

## 说明

`bugfix-merged` 分支上的 `dev.py` 已经带了 `IS_WINDOWS` 与 POSIX 的 `/proc` 分支，但 `VENV_PYTHON` 仍写死 `Scripts/python.exe`（同样的可移植性缺口）。若 master 采纳本项，建议两条分支口径一致。
