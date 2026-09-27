"""X-19 补丁修正：批处理里 `echo` 不该用反斜杠转义引号（会原样打印）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "start-dev.bat"
OLD = b'echo [ERROR] Could not spawn the \\"Gipfel Dev\\" window.'
NEW = b'echo [ERROR] Could not spawn the "Gipfel Dev" window.'


def main() -> int:
    raw = TARGET.read_bytes()
    if NEW in raw and OLD not in raw:
        print("[skip] 已修正")
        return 0
    if OLD not in raw:
        print("[fail] 未找到待修正文本")
        return 1
    TARGET.write_bytes(raw.replace(OLD, NEW, 1))
    b = TARGET.read_bytes()
    print("[ok]   已修正；CRLF =", b.count(b"\r\n"), "ascii =", all(c < 128 for c in b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
