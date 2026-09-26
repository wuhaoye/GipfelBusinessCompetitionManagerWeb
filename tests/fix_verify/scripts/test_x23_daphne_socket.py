"""X-23 回归：gipfel.service 不再监听没人用的 daphne Unix socket，且 X-13 的收权仍在。

改前状态（`deploy/gipfel.service`）：
    ExecStart=…/daphne \\
        -u /run/gipfel/gipfel.sock \\      ← 全仓库没有任何消费方
        -b 127.0.0.1 \\
        -p 8000 \\
    RuntimeDirectoryMode=0755             ← 运行目录对同机任意用户可遍历
    （无 UMask）

daphne 对 Unix socket **不校验对端 UID**：同机任意用户只要能进入 `/run/gipfel` 就能绕过
nginx 直连后端，而且因为来源被判为回环，还能自造 `X-Real-IP` 绕过按 IP 的登录限速。
`deploy/nginx-gipfel.conf` 走的是 `127.0.0.1:8000`，脚本/测试/文档里也没有引用该 socket。

改后：
  * `-u /run/gipfel/gipfel.sock` 已从 ExecStart 移除（nginx 反代路径不变）；
  * X-13 的纵深防御保留：`RuntimeDirectoryMode=0750`、`LogsDirectoryMode=0750`、`UMask=0027`。

★ C1-a 追加（2026-09，端口/上游变更属**有意**，不是回归）：
  `/api/*` 改由新增的 `deploy/gipfel-wsgi.service`（gunicorn WSGI，127.0.0.1:8002）承载，
  daphne 的 :8000 只再承载 `/socket.io/*`（本用例原先断言的字面量 `127.0.0.1:8000`
  仍在，只是从 `upstream gipfel_django` 换到了新增的 `upstream gipfel_socketio`）。
  因此这里**只增不改**：保留原有全部断言，另加"分离拓扑"的断言，
  确认 daphne 的 :8000 仍然存在、仍然对 Unix socket 无依赖。

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x23_daphne_socket.py
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GIPFEL_UNIT = REPO / "deploy" / "gipfel.service"
NGINX = REPO / "deploy" / "nginx-gipfel.conf"

# 允许出现 "gipfel.sock" 字样的地方：说明性注释 + 审计报告 +
# 回归用例自身（X-13 的用例会断言 nginx 不用这个 socket，用例里必须写出这个名字）
_SELF = {Path(__file__).name, "test_x13_x14_units_diag.py"}
_SKIP_PARTS = {"node_modules", ".tmp", ".venv", "dist", ".git", "__pycache__"}

# ★ 本用例的断言意图是「**没有消费方**依赖这个 socket」——被 systemd / nginx / bash / python
#   加载的东西才算消费方。`docs/` 下是**文本产物**，永远不会被加载，而且：
#     · `docs/运维约束整改设计说明.md` 的接口冻结条款**必须**写出这个名字
#       （"…且不含 gipfel.sock"），否则契约本身无法表达；
#     · `docs/branch-diff/*.diff` 是分支差异归档（3 MB+ 的 diff 全文，含改前的单位文件）。
#   把 docs 纳入扫描会让"设计文档按契约写了这句话"被判成"仍有文件引用 socket"（假阳性，
#   且无法通过改代码消除）。因此这里**把 docs 从消费方扫描中排除**，
#   而 scripts / backend / frontend / deploy / tests 仍然全量扫描 —— 真正的消费方跑不掉。
_NON_CONSUMER_DIRS = {"docs"}


def _strip_comments(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", ";", "REM", "//"))
    )


class X23SocketRemovedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = GIPFEL_UNIT.read_bytes().decode("utf-8")
        cls.code = _strip_comments(cls.raw)

    def test_execstart_no_longer_listens_on_unix_socket(self):
        execstart = self.code.split("ExecStart=", 1)[1].split("\n\n", 1)[0]
        self.assertNotRegex(execstart, r"(?m)^\s*-u\s", "ExecStart 仍在监听 Unix socket")
        self.assertNotIn("gipfel.sock", execstart)

    def test_still_binds_loopback_http(self):
        self.assertIn("-b 127.0.0.1", self.code)
        self.assertIn("-p 8000", self.code)
        self.assertIn("backend.asgi:application", self.code)

    def test_x13_hardening_still_present(self):
        self.assertIn("RuntimeDirectory=gipfel", self.code)
        self.assertIn("RuntimeDirectoryMode=0750", self.code)
        self.assertIn("LogsDirectoryMode=0750", self.code)
        self.assertIn("UMask=0027", self.code)

    def test_address_families_keep_ip(self):
        m = re.search(r"RestrictAddressFamilies=(.*)", self.code)
        self.assertIsNotNone(m)
        fams = m.group(1).split()
        for need in ("AF_INET", "AF_INET6"):
            self.assertIn(need, fams, f"RestrictAddressFamilies 缺少 {need}")

    def test_nginx_does_not_use_the_socket(self):
        nginx = NGINX.read_bytes().decode("utf-8", errors="replace")
        self.assertNotIn("gipfel.sock", nginx)
        self.assertIn("127.0.0.1:8000", nginx)


class X23NoConsumerTests(unittest.TestCase):
    """确认整个仓库里没有别的东西依赖这个 socket（否则删掉会打断它）。"""

    SCAN_DIRS = ("scripts", "tests", "backend", "frontend", "deploy", "docs")
    SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".xlsx", ".ico", ".woff", ".woff2"}

    def test_no_other_reference(self):
        hits: list[str] = []
        for d in self.SCAN_DIRS:
            root = REPO / d
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() in self.SKIP_SUFFIX:
                    continue
                if p.name in _SELF or p == GIPFEL_UNIT:
                    continue
                if _SKIP_PARTS & set(p.parts):
                    continue
                if _NON_CONSUMER_DIRS & set(p.relative_to(REPO).parts):
                    continue  # 见 _NON_CONSUMER_DIRS 的说明：文档不是消费方
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "gipfel.sock" in text:
                    hits.append(str(p.relative_to(REPO)))
        self.assertEqual(hits, [], f"仍有文件引用 gipfel.sock：{hits}")

    def test_consumers_are_really_scanned(self):
        """反向守卫：排除 docs 之后，消费方目录仍必须真的被扫到（防止排除范围被写大）。

        这里用「人为把 socket 名放进一个临时文件名的探针」成本太高，
        改为断言扫描范围常量本身：消费方目录一个都不能少，且排除项只有 docs。
        """
        for must in ("scripts", "backend", "frontend", "deploy", "tests"):
            self.assertIn(must, self.SCAN_DIRS, f"扫描范围不得漏掉消费方目录 {must}")
        self.assertEqual(
            _NON_CONSUMER_DIRS, {"docs"},
            "非消费方排除项只允许是 docs（文档是文本产物，不会被 systemd/nginx/bash 加载）；"
            "若要把配置/脚本目录加进来，等于放掉真实消费方，必须先论证",
        )


class X23C1aSplitTopologyTests(unittest.TestCase):
    """C1-a 追加断言：拆分上游后，daphne 的 :8000 仍只被 /socket.io/ 使用，且新 unit 也不碰 socket。

    为什么加在这里而不是新文件：本用例原本就断言「nginx 走 127.0.0.1:8000 而不是 Unix socket」，
    是**收权前提**。C1-a 把 /api/ 挪到 WSGI(8002) 之后，这个前提必须重新表述为
    「8000 仍然存在、且只服务 Socket.IO」——否则将来有人把 8000 删掉，
    本用例会以"找不到 127.0.0.1:8000"失败，但失败原因（Socket.IO 断了）看不出来。
    """

    def setUp(self) -> None:
        self.nginx = NGINX.read_bytes().decode("utf-8", errors="replace")
        self.wsgi_unit = (REPO / "deploy" / "gipfel-wsgi.service").read_bytes().decode("utf-8")

    def test_socketio_still_uses_daphne_8000(self):
        start = self.nginx.index("upstream gipfel_socketio {")
        block = self.nginx[start : self.nginx.index("}", start)]
        self.assertIn("server 127.0.0.1:8000", block, "daphne 的 8000 必须仍在（Socket.IO 靠它）")
        self.assertIn("proxy_pass         http://gipfel_socketio;", self.nginx)

    def test_api_no_longer_points_at_daphne(self):
        """有意变更：/api/ 的上游从 8000 改为 8002（gunicorn WSGI）。"""
        start = self.nginx.index("upstream gipfel_django {")
        block = self.nginx[start : self.nginx.index("}", start)]
        self.assertIn("server 127.0.0.1:8002", block)
        self.assertNotIn("127.0.0.1:8000", block, "gipfel_django 不应再指向 daphne（那是 C1-a 之前的拓扑）")

    def test_new_wsgi_unit_does_not_use_unix_socket(self):
        code = "\n".join(
            ln for ln in self.wsgi_unit.splitlines()
            if not ln.lstrip().startswith(("#", ";"))
        )
        self.assertNotIn("gipfel.sock", code)
        self.assertNotRegex(code, r"(?m)^\s*-u\s|--unix-socket", "gunicorn 不得监听 Unix socket")
        self.assertIn("127.0.0.1", code, "WSGI 同样只绑回环")


if __name__ == "__main__":
    unittest.main(verbosity=2)
