"""X-25 修正：migrate-server.sh 里该用 log_warn（该脚本没有 warn 函数）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "migrate-server.sh"

OLD = '''    warn "未指定 --ssh-known-hosts：首次连接将自动信任目标主机密钥（只防后续变更，不防首次中间人）。"'''
NEW = '''    log_warn "未指定 --ssh-known-hosts：首次连接将自动信任目标主机密钥（只防后续变更，不防首次中间人）。"'''

OLD2 = '''    warn "已用 -i 指定私钥；该路径会出现在同机 ps 中，生产环境建议改用 ~/.ssh/config 的 IdentityFile。"'''
NEW2 = '''    log_warn "已用 -i 指定私钥；该路径会出现在同机 ps 中，生产环境建议改用 ~/.ssh/config 的 IdentityFile。"'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    changed = 0
    for old, new in ((OLD, NEW), (OLD2, NEW2)):
        if old in text:
            text = text.replace(old, new, 1)
            changed += 1
        elif new in text:
            print("[skip] 已修正")
    TARGET.write_text(text, encoding="utf-8")
    remaining = [ln for ln in text.splitlines() if ln.strip().startswith("warn ")]
    print(f"[ok] 替换 {changed} 处；仍以 warn 开头的行: {len(remaining)}")
    for ln in remaining:
        print("     " + ln.strip())
    return 0 if not remaining else 1


if __name__ == "__main__":
    raise SystemExit(main())
