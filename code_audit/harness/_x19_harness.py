"""X-19 运行时探针：start-dev.bat 的退出码 / dev.py --check-only 的同步前置检查。

安全性：探针只做两件事 ——
  * 在 cmd.exe 里演示 `start` 不传递子进程退出码的语言语义（用 `start /B`，
    不新开窗口、不启动任何真实服务）；
  * 在**自己临时占用 5173 端口**的前提下运行 dev.py / start-dev.bat，
    让前置检查必然失败，从而既拿到非 0 退出码、又不会真的拉起任何服务。

用法：backend/.venv/Scripts/python.exe code_audit/harness/_x19_harness.py before|after
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # code_audit/harness/ -> 仓库根
CMD_EXE = r"C:\Windows\System32\cmd.exe"
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
BUSY_PORT = 5173


class _Hold:
    """临时占用一个端口，让前置检查必然失败。

    必须**持续 accept**：`listen(1)` 且从不 accept 时，第一次 connect 占满 backlog，
    之后新的 connect 会被 Windows 直接拒绝 —— `_port_in_use()` 就会误判成"端口空闲"，
    于是 start-dev.bat 真的会去拉起服务（本探针第一次就是这么踩到的）。
    """

    def __init__(self, port: int) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(64)
        self.port = port
        self._closing = False
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        while not self._closing:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.close()
            except OSError:
                pass

    def __enter__(self) -> "_Hold":
        return self

    def __exit__(self, *exc: object) -> None:
        self._closing = True
        try:
            self.sock.close()
        except OSError:
            pass


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=str(REPO),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        **kw,
    )


def _show(title: str, proc: subprocess.CompletedProcess, keep: int = 8) -> None:
    out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    print(f"  -- {title} --")
    for ln in out[:keep]:
        print("     " + ln.strip())
    if len(out) > keep:
        print(f"     ...（共 {len(out)} 行）")
    print(f"     exit={proc.returncode}")


def _port_free(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
    except OSError:
        return True
    finally:
        s.close()
    return False


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "after").lower()

    print(f"### X-19 探针（{which}）")
    print("[1] cmd 语言语义：`start` 是否传递子进程退出码")
    probe = 'start /B cmd /c "exit 7" & timeout /t 1 >nul & echo START_RC=%ERRORLEVEL%'
    proc = _run([CMD_EXE, "/c", probe])
    for ln in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
        if "START_RC" in ln:
            print("     " + ln.strip())
    print("     （START_RC=0 说明 `start` 丢弃子进程退出码 —— 这正是 start-dev.bat 改前恒返回 0 的原因）")

    print("[2] 前置检查（5173 已被本探针占用，任何真实服务都不会被拉起）")
    with _Hold(BUSY_PORT):
        time.sleep(0.2)
        proc = _run([str(PY), "scripts/dev.py", "--check-only"])
        _show("dev.py --check-only（端口被占）", proc, keep=10)

        if which == "after":
            # 诊断：确认此刻 5173 确实被占用（否则 bat 会真的拉起服务）
            print(f"     [诊断] 调用 bat 前 5173 是否空闲 = {_port_free(BUSY_PORT)}")
            # 用 CREATE_NO_WINDOW + 文件重定向（不用管道：cmd 的孙进程会继承管道句柄，
            # 一旦它没有退出，capture_output 就会一直等下去）。
            out_path = REPO / ".tmp" / "x19_bat.out"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", encoding="utf-8", errors="replace") as fh:
                child = subprocess.Popen(
                    [CMD_EXE, "/c", "scripts\\start-dev.bat"],
                    cwd=str(REPO),
                    stdin=subprocess.DEVNULL,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
                try:
                    rc = child.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    rc = None
                    subprocess.run(
                        ["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True
                    )
            text = out_path.read_text(encoding="utf-8", errors="replace")
            if "Starting Gipfel dev services" in text:
                print("     [安全网] bat 竟然走到了 start：立即用 dev.py stop 收尾")
                _run([str(PY), "scripts/dev.py", "stop"])
            print("  -- start-dev.bat（端口被占） --")
            for ln in [x for x in text.splitlines() if x.strip()][:12]:
                print("     " + ln.strip())
            print(f"     exit={rc}" + ("" if rc is not None else "（TIMEOUT，改后不应如此）"))
    time.sleep(0.3)

    if which == "after":
        print("[3] 端口空闲时的同步检查必须放行、且不得留下任何服务")
        proc = _run([str(PY), "scripts/dev.py", "--check-only"])
        _show("dev.py --check-only（端口空闲）", proc, keep=10)
        time.sleep(0.5)
        for p in (8000, BUSY_PORT, 8120):
            print(f"     端口 {p} 仍空闲: {_port_free(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
