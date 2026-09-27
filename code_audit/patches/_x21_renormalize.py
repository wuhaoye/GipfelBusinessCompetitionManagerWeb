"""X-21 收尾：把 migrate-server.sh 在**对象库里**恢复成 LF（历史一直是 LF）。

原因：该路径在 index 里的属性是 `-text`（i/-text w/-text），`core.autocrlf=true` 对它不生效，
X-21 那次提交把工作区的 CRLF 原样写进了 blob，导致 `git show HEAD:...` 与 HEAD~1 逐行都不同
（519 插入 / 489 删除）。这里把工作区文件改写成 LF 后重新 add，使仓库里恢复成 LF。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "scripts" / "migrate-server.sh"


def main() -> int:
    raw = TARGET.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    if lf == raw:
        print("[skip] 工作区文件已经是 LF")
    else:
        TARGET.write_bytes(lf)
        print(f"[ok]   工作区已改写为 LF（原 CRLF {raw.count(b'\\r\\n'.replace(b'\\\\', b'')) or raw.count(bytes([13, 10]))} 处）")

    subprocess.run(["git", "add", "scripts/migrate-server.sh"], cwd=str(REPO), check=True)
    staged = subprocess.run(
        ["git", "show", ":scripts/migrate-server.sh"], cwd=str(REPO), capture_output=True, check=True
    ).stdout
    crlf = staged.count(bytes([13, 10]))
    print(f"[check] 暂存区 blob：CRLF={crlf} 总LF={staged.count(bytes([10]))}")
    numstat = subprocess.run(
        ["git", "diff", "--cached", "--numstat", "HEAD", "--", "scripts/migrate-server.sh"],
        cwd=str(REPO), capture_output=True, text=True,
    ).stdout.strip()
    print(f"[check] 相对 HEAD 的改动量（应只有真实改动）: {numstat}")
    return 0 if crlf == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
