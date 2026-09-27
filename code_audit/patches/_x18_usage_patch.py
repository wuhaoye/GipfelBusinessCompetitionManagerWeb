"""X-18 补充：在 bootstrap-dev.bat 顶部用法说明里补上 --no-keep-open（ASCII + CRLF）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-dev.bat"

OLD = "REM  Optional: pass --skip-frontend to skip Node/npm steps.\r\n"
NEW = (
    "REM  Optional: pass --skip-frontend to skip Node/npm steps.\r\n"
    "REM  Optional: pass --no-keep-open to run inline and return the real exit\r\n"
    "REM            code (use this from scripts/CI; it never spawns cmd /k).\r\n"
)


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("ascii")
    if b"--no-keep-open to run inline" in raw:
        print("[skip] 已存在")
        return 0
    if OLD not in text:
        print("[fail] 未找到锚点")
        return 1
    TARGET.write_bytes(text.replace(OLD, NEW, 1).encode("ascii"))
    b = TARGET.read_bytes()
    print(f"完成；ASCII={all(c < 128 for c in b)} CRLF={b.count(b'\\r\\n')} 裸LF={b.count(b'\\n') - b.count(b'\\r\\n')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
