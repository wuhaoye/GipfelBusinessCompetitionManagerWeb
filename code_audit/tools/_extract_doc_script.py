"""从手册里抽取内嵌的验证脚本，验证手册的自包含性（一次性脚本）。"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "WSL与生产环境验证操作手册.md"
OUT = REPO / "code_audit" / "_wsl_verify_from_doc.sh"

MARK = "生产相关 bash 内容的 WSL(Ubuntu) 复验脚本"
FENCE = "```"


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    parts = text.split(FENCE + "bash")
    script = None
    for part in parts[1:]:
        body = part.split(FENCE, 1)[0]
        if MARK in body:
            script = body.lstrip("\n")
            break
    if script is None:
        print("[fail] 手册里没找到内嵌脚本")
        return 1
    OUT.write_text(script, encoding="utf-8", newline="\n")   # 关键：Windows 下默认会把 \n 翻成 CRLF
    print(f"[ok] 已抽取内嵌脚本 → {OUT.name}（{len(script.splitlines())} 行）")
    print("     前面 200 字符：", script[:200].replace("\n", " | "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
