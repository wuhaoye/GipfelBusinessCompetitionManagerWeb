"""X-25 自检：确认补丁没有重复插入（一次性脚本，不属于交付物）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXPECT = {
    "scripts/quick-sync.sh": {
        "RSYNC_SSH=": 1,
        "SSH_OPTS=(": 1,
        "_pos=()": 1,
        "SYNC_ITEMS=(": 1,
        "非 22 端口 / 指定私钥": 1,
        "ACTION=\"\"": 1,
        "StrictHostKeyChecking=accept-new": 2,  # 1 处头部注释描述旧写法 + 1 处真实代码
        "--ssh-port": 6,                        # 注释 2 + 用法 1 + 解析 1 + 跳过取值 1 + 报错文案 1
    },
    "scripts/migrate-server.sh": {
        "SSH_KNOWN_HOSTS": 4,
        "StrictHostKeyChecking=accept-new": 1,
        "UserKnownHostsFile=": 1,
        "--ssh-known-hosts": 3,
    },
}

bad = 0
for rel, marks in EXPECT.items():
    text = (REPO / rel).read_text(encoding="utf-8")
    for mark, want in marks.items():
        got = text.count(mark)
        flag = "OK  " if got == want else "DIFF"
        if got != want:
            bad += 1
        print(f"  {flag} {rel:28} {mark!r:38} 出现 {got}（期望 {want}）")

for rel in EXPECT:
    p = REPO / rel
    b = p.read_bytes()
    crlf, lf = b.count(b"\r\n"), b.count(b"\n")
    status = "一致" if crlf in (0, lf) else f"混杂 CRLF={crlf} LF={lf}"
    print(f"  行尾 {rel:28} {status}")

print(f"结论：{'全部符合' if bad == 0 else f'{bad} 项不符'}")
raise SystemExit(0 if bad == 0 else 1)
