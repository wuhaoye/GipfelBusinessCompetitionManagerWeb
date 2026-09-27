"""X-21 补充：--dry-run 结束后也要清理临时备份目录（否则每次预演都留下一个空目录）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "migrate-server.sh"

OLD = (
    "    if [[ \"$KEEP_BACKUP\" != true && \"$DRY_RUN\" != true && -n \"$BACKUP_DIR\" && -d \"$BACKUP_DIR\" ]]; then"
    "\r\n"
)
NEW = (
    "    # 审计 X-21：改前 dry-run 也跳过清理，于是每次 `--dry-run` 都在 /tmp 留下一个\n"
    "    # `gipfel-migration-XXXXXX` 空目录（预演本应零副作用）。现在只有 --keep-backup 才保留。\n"
    "    if [[ \"$KEEP_BACKUP\" != true && -n \"$BACKUP_DIR\" && -d \"$BACKUP_DIR\" ]]; then\r\n"
)


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    if NEW.replace("\n", "\r\n") in text and OLD not in text:
        print("[skip] 已打过补丁")
        return 0
    if OLD not in text:
        print("[fail] 未找到锚点")
        return 1
    TARGET.write_bytes(text.replace(OLD, NEW.replace("\n", "\r\n"), 1).encode("utf-8"))
    b = TARGET.read_bytes()
    crlf = b.count(b"\r\n")
    print(f"[ok]   已修正；CRLF={crlf} 裸LF={b.count(chr(10).encode()) - crlf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
