"""X-17/X-19 等用例的输出解码工具（预先存在的夹具缺陷修复）。

问题：这些用例用 `subprocess.run(..., text=True, encoding="utf-8", errors="replace")`
读取「我方脚本（输出 UTF-8）」与「经 cmd.exe / Windows 控制台（输出系统代码页）」
混合产生的字节流。中文 Windows 上后者是 GBK，按 UTF-8 硬解会把中文变成 `\\ufffd`，
于是断言里的中文串（如「超精度值必须显式报错」）永远匹配不上 —— 表现为"脚本坏了"，
实际是被测脚本完全正常。

对策：读 bytes 自行解码 —— 先按 UTF-8 严格解；失败则按系统首选编码（Windows 用 ANSI
代码页）宽松解。这样无论夹具走哪条路径都能得到可断言的中文。
"""
from __future__ import annotations

import locale
import subprocess


def decode_output(raw: bytes) -> str:
    """bytes → str：UTF-8 优先，回退系统首选编码。"""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def run_captured(argv, *, cwd=None, timeout: int | None = None) -> subprocess.CompletedProcess:
    """跑子进程并返回已解码的输出（stdout/stderr 拼在一起，与旧用例行为一致）。"""
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout)
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        decode_output(proc.stdout),
        decode_output(proc.stderr),
    )
