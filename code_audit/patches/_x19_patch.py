"""X-19：给 dev.py 加同步前置检查 `--check-only`，并让 start-dev.bat 先跑它（CRLF 保持）。"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEV_PY = REPO / "scripts" / "dev.py"
START_BAT = REPO / "scripts" / "start-dev.bat"

CHECK_FUNC = '''def check_command() -> int:
    """同步前置检查（审计 X-19）：依赖是否存在 + 三个开发端口是否空闲。

    背景：`start` 是**异步**启动，父批处理拿不到子进程的退出码，于是 `start-dev.bat`
    改前无论 dev.py 是否成功都 `exit /b 0` —— CI/IDE 把"启动失败"当成功，后续步骤
    在超时后才失败。这个入口把同一套 `_check_preconditions` 同步跑一遍，失败返回 1，
    供 start-dev.bat 在 `start` 之前调用。

    刻意**不**调用 `_pause_if_console()`：是否暂停由调用方（批处理的 :fail 分支）决定，
    避免双击用户被要求按两次回车。
    """
    logviewer_port = _read_logviewer_port()
    services = _build_services(logviewer_port)
    print("=" * 72)
    print(" Gipfel dev preconditions check")
    print("=" * 72)
    passed = _check_preconditions(logviewer_port, services)
    if passed:
        ok("Preconditions OK.")
    else:
        error("Preconditions FAILED; services were NOT started.")
    return 0 if passed else 1


'''

OLD_MAIN = '''def main() -> int:
    _prepare_console()

    # 审计 D-01 / D-02：`dev.py stop` 是安全的清理入口（逐条校验进程身份后才停，
    # 端口占用只报告不代杀）。`scripts\\stop-dev.bat` 现在只是它的包装。
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() in ("stop", "--stop"):
        return stop_command()
'''

NEW_MAIN = '''def main() -> int:
    _prepare_console()

    arg = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""

    # 审计 D-01 / D-02：`dev.py stop` 是安全的清理入口（逐条校验进程身份后才停，
    # 端口占用只报告不代杀）。`scripts\\stop-dev.bat` 现在只是它的包装。
    if arg in ("stop", "--stop"):
        return stop_command()

    # 审计 X-19：同步前置检查，供 start-dev.bat 在 `start` 之前调用，让"启动失败"
    # 能真正反映到批处理/调用方的退出码上。
    if arg in ("check", "--check-only"):
        return check_command()
'''

OLD_BAT_HEADER = (
    "REM  Gipfel - DEVELOPMENT - Start Django (8000) + Vite (5173) + LogViewer (8120)\r\n"
)
NEW_BAT_HEADER = (
    "REM  Gipfel - DEVELOPMENT - Start Django (8000) + Vite (5173) + LogViewer\r\n"
    "REM  (the LogViewer port comes from backend\\.env LOG_VIEWER_PORT, default 8120)\r\n"
)

OLD_BAT_START = (
    'echo [INFO]  Starting Gipfel dev services in a new window ("Gipfel Dev") ...\r\n'
    'start "Gipfel Dev" "%PY%" "%~dp0dev.py"\r\n'
    "exit /b 0\r\n"
)
NEW_BAT_START = (
    "REM  Audit X-19: `start` is asynchronous, so its exit code says nothing about\r\n"
    "REM  whether the services came up - the old script returned 0 unconditionally.\r\n"
    "REM  Run the same precondition checks synchronously first; only spawn the\r\n"
    "REM  window when they pass. (A service that dies AFTER a successful handover\r\n"
    "REM  is still reported only inside the new window - run\r\n"
    "REM  `python scripts\\dev.py stop` and check its output in that case.)\r\n"
    'echo [INFO]  Checking preconditions ...\r\n'
    '"%PY%" "%~dp0dev.py" --check-only\r\n'
    "if errorlevel 1 (\r\n"
    "  echo [ERROR] Preconditions not met; services were NOT started.\r\n"
    "  goto :fail\r\n"
    ")\r\n"
    "\r\n"
    'echo [INFO]  Starting Gipfel dev services in a new window ("Gipfel Dev") ...\r\n'
    'start "Gipfel Dev" "%PY%" "%~dp0dev.py"\r\n'
    "if errorlevel 1 (\r\n"
    "  echo [ERROR] Could not spawn the \\\"Gipfel Dev\\\" window.\r\n"
    "  goto :fail\r\n"
    ")\r\n"
    "exit /b 0\r\n"
)


def _patch(path: Path, pairs: list[tuple[str, str]], ascii_only: bool) -> int:
    raw = path.read_bytes()
    text = raw.decode("ascii" if ascii_only else "utf-8")
    changed = 0
    for old, new in pairs:
        if new.split("\r\n")[0] and new in text and old not in text:
            print(f"[skip] {path.name}: 已打过补丁")
            continue
        if old not in text:
            print(f"[fail] {path.name}: 未找到锚点 {old.splitlines()[0][:60]!r}")
            return 1
        text = text.replace(old, new, 1)
        changed += 1
    path.write_bytes(text.encode("ascii" if ascii_only else "utf-8"))
    b = path.read_bytes()
    print(
        f"[ok]   {path.name}: 替换 {changed} 处，CRLF={b.count(chr(13).encode() + chr(10).encode())}，"
        f"裸LF={b.count(chr(10).encode()) - b.count(chr(13).encode() + chr(10).encode())}"
    )
    return 0


def main() -> int:
    rc = _patch(
        DEV_PY,
        [(OLD_MAIN.replace("\n", "\r\n"), NEW_MAIN.replace("\n", "\r\n"))],
        ascii_only=False,
    )
    if rc:
        return rc
    # check_command 插到 main 之前
    raw = DEV_PY.read_bytes().decode("utf-8")
    if "def check_command() -> int:" not in raw:
        anchor = "def main() -> int:\r\n    _prepare_console()\r\n"
        if anchor not in raw:
            print("[fail] dev.py: 未找到 main 锚点")
            return 1
        raw = raw.replace(anchor, CHECK_FUNC.replace("\n", "\r\n") + anchor, 1)
        DEV_PY.write_bytes(raw.encode("utf-8"))
        print("[ok]   dev.py: 已插入 check_command()")
    else:
        print("[skip] dev.py: check_command() 已存在")

    return _patch(
        START_BAT,
        [(OLD_BAT_HEADER, NEW_BAT_HEADER), (OLD_BAT_START, NEW_BAT_START)],
        ascii_only=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
