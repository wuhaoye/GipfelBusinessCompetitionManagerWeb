# -*- coding: utf-8 -*-
"""X-13 / X-14 验证：systemd 单元权限边界与诊断脚本泄密。

- **X-13**（`deploy/gipfel.service` / `deploy/logviewer.service`）：
  ① 两个 unit 都声明 `RuntimeDirectory=gipfel` + `RuntimeDirectoryMode=0755` ——
     `/run/gipfel` 对**同机任意用户可遍历**，而该目录里放着 daphne 的 Unix socket
     `gipfel.sock`（daphne 不校验对端 UID），可绕过 nginx 直连后端；共用运行目录还意味着
     `systemctl restart gipfel` 会连带清理/重建该目录；
  ② `logviewer.service` 的 `ReadWritePaths` 授予了主应用的 `backend/uploads` 与
     **`backend/db.sqlite3`** 写权限 —— 一个"只读看日志"的站点却能改写主业务数据库。
- **X-14**（`tests/gipfel-logviewer-diag.sh`）：
  ① 第 6 行的 grep 把 **`LOGVIEWER_SECRET_KEY` 原文**打到 stdout；
  ② 所有 curl 没有超时（网络异常会永久挂住）；
  ③ 端口硬编码 8120（与 nginx/ufw 的实际端口可能不一致）；
  ④ `sudo -n` 失败时静默无输出。

★ C1-a 追加（2026-09）：新增了第三个 unit `deploy/gipfel-wsgi.service`（gunicorn WSGI）。
  X-13 的结论（运行/日志目录不共用、模式 0750、UMask、最小可写路径、Environment 顺序）
  必须**同样适用**于它 —— 新 unit 最容易成为"加固漏网"。故本用例增加对它的断言，
  并把 nginx 断言扩成"分离上游"（/api/→8002、/socket.io/→8000；这两处端口/上游变更属有意）。

本机没有可执行的 bash，故用**静态语义核对 + Python 等价行为复现**验证。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe -m unittest tests.fix_verify.scripts.test_x13_x14_units_diag -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GIPFEL_UNIT = REPO / "deploy" / "gipfel.service"
WSGI_UNIT = REPO / "deploy" / "gipfel-wsgi.service"
LV_UNIT = REPO / "deploy" / "logviewer.service"
DIAG = REPO / "tests" / "gipfel-logviewer-diag.sh"


def _directives(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class X13UnitHardeningTests(unittest.TestCase):
    def setUp(self):
        self.gipfel = _directives(GIPFEL_UNIT.read_text(encoding="utf-8"))
        self.lv_text = LV_UNIT.read_text(encoding="utf-8")
        self.lv = _directives(self.lv_text)

    def test_runtime_directory_mode_is_not_world_traversable(self):
        """0755 会让同机任意用户遍历到 daphne 的 Unix socket。"""
        for directives, name in ((self.gipfel, "gipfel.service"), (self.lv, "logviewer.service")):
            mode = directives.get("RuntimeDirectoryMode", "")
            self.assertIn(mode, ("0750", "0700", "0751"), f"{name} 的 RuntimeDirectoryMode={mode!r} 过宽")
        self.assertEqual(self.gipfel.get("RuntimeDirectoryMode"), "0750")
        self.assertEqual(self.lv.get("RuntimeDirectoryMode"), "0750")

    def test_logs_directory_mode_tightened(self):
        self.assertEqual(self.gipfel.get("LogsDirectoryMode"), "0750")
        self.assertEqual(self.lv.get("LogsDirectoryMode"), "0750")

    def test_units_do_not_share_runtime_directory(self):
        """共用运行目录：restart 一个服务会清掉另一个服务的目录。"""
        self.assertEqual(self.gipfel.get("RuntimeDirectory"), "gipfel")
        self.assertEqual(self.lv.get("RuntimeDirectory"), "gipfel-logviewer")
        self.assertNotEqual(
            self.gipfel.get("RuntimeDirectory"), self.lv.get("RuntimeDirectory"),
            "两个 unit 不得共用 RuntimeDirectory",
        )

    def test_units_do_not_share_logs_directory(self):
        self.assertEqual(self.gipfel.get("LogsDirectory"), "gipfel")
        self.assertEqual(self.lv.get("LogsDirectory"), "gipfel-logviewer")

    def test_umask_is_set(self):
        self.assertEqual(self.gipfel.get("UMask"), "0027")
        self.assertEqual(self.lv.get("UMask"), "0027")

    def test_logviewer_cannot_write_main_database_or_uploads(self):
        """日志查看器绝不能有主库/上传目录的写权限（改前的核心问题）。"""
        rwp = self.lv.get("ReadWritePaths", "")
        self.assertNotIn("db.sqlite3", rwp, f"日志查看器不得可写主数据库：{rwp}")
        self.assertNotIn("backend/uploads", rwp, f"日志查看器不得可写上传目录：{rwp}")
        self.assertNotIn("/run/gipfel", rwp.replace("/run/gipfel-logviewer", ""),
                         f"不得引用主服务的运行目录：{rwp}")
        self.assertIn("/run/gipfel-logviewer", rwp)
        self.assertIn("/var/log/gipfel-logviewer", rwp)

    def test_main_service_keeps_its_own_paths(self):
        """回归：主服务仍需可写自己的 uploads/logs/db 与运行目录。"""
        rwp = self.gipfel.get("ReadWritePaths", "")
        for must in ("backend/uploads", "backend/logs", "db.sqlite3", "/run/gipfel", "/var/log/gipfel"):
            self.assertIn(must, rwp, f"主服务应保留 {must}：{rwp}")

    def test_access_log_uses_logs_directory_variable(self):
        """日志路径改用 systemd 的 ${LOGS_DIRECTORY}，避免两服务写同一个文件。"""
        for text, name in ((GIPFEL_UNIT.read_text(encoding="utf-8"), "gipfel.service"),
                           (self.lv_text, "logviewer.service")):
            self.assertIn("${LOGS_DIRECTORY}", text, f"{name} 的 --access-log 应用 ${{LOGS_DIRECTORY}}")
            self.assertNotIn("--access-log /var/log/gipfel/logviewer.access.log", text,
                             f"{name} 不应写死主服务的日志目录")

    def test_nginx_does_not_use_the_unix_socket(self):
        """收紧运行目录权限的前提：nginx 走的是 TCP 回环（不是 unix socket）。

        C1-a：`/api/` 改走 gunicorn(127.0.0.1:8002)，`/socket.io/` 走 daphne(127.0.0.1:8000)。
        两个字面量都必须在（8000 仍被 socket.io 上游使用），且都不依赖 Unix socket。
        """
        nginx = (REPO / "deploy" / "nginx-gipfel.conf").read_text(encoding="utf-8")
        self.assertNotIn("gipfel.sock", nginx, "nginx 若依赖 unix socket，收权会打断它")
        self.assertIn("127.0.0.1:8000", nginx)
        self.assertIn("127.0.0.1:8121", nginx)
        # C1-a 追加：双上游必须各就各位，否则"收权前提"在拆分后就不成立了
        self.assertRegex(
            nginx, r"upstream gipfel_django \{[\s\S]*?server 127\.0\.0\.1:8002",
            "gipfel_django 必须指向 WSGI(8002)（有意变更：C1-a 之前是 8000）",
        )
        self.assertRegex(
            nginx, r"upstream gipfel_socketio \{[\s\S]*?server 127\.0\.0\.1:8000",
            "gipfel_socketio 必须指向 daphne(8000)",
        )

    # ---- C1-a 追加：新 unit 必须继承 X-13 的全部加固结论 ----
    def test_wsgi_unit_directories_are_tightened_and_private(self):
        wsgi = _directives(WSGI_UNIT.read_text(encoding="utf-8"))
        self.assertEqual(wsgi.get("RuntimeDirectoryMode"), "0750")
        self.assertEqual(wsgi.get("LogsDirectoryMode"), "0750")
        self.assertEqual(wsgi.get("UMask"), "0027")
        self.assertEqual(wsgi.get("RuntimeDirectory"), "gipfel-wsgi")
        self.assertEqual(wsgi.get("LogsDirectory"), "gipfel-wsgi")
        # 不得与其它两个 unit 共用（共用会让 restart 互相清理目录）
        for other in (_directives(GIPFEL_UNIT.read_text(encoding="utf-8")),
                      _directives(LV_UNIT.read_text(encoding="utf-8"))):
            self.assertNotEqual(wsgi.get("RuntimeDirectory"), other.get("RuntimeDirectory"))
            self.assertNotEqual(wsgi.get("LogsDirectory"), other.get("LogsDirectory"))

    def test_wsgi_unit_keeps_sandbox_and_write_paths(self):
        text = WSGI_UNIT.read_text(encoding="utf-8")
        code = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", ";"))
        )
        for must in ("NoNewPrivileges=true", "ProtectSystem=full", "ProtectHome=true",
                     "CapabilityBoundingSet=", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
                     "${LOGS_DIRECTORY}"):
            self.assertIn(must, code, f"gipfel-wsgi.service 缺少加固项 {must}")
        rwp = _directives(text).get("ReadWritePaths", "")
        for must in ("backend/uploads", "backend/logs", "backend/snapshots", "db.sqlite3",
                     "/run/gipfel-wsgi", "/var/log/gipfel-wsgi"):
            self.assertIn(must, rwp, f"gipfel-wsgi.service 的 ReadWritePaths 缺少 {must}：{rwp}")

    def test_all_units_declare_defaults_before_environment_file(self):
        """systemd 中 EnvironmentFile 优先级更高；默认值必须排在它之前（顺序即文档）。"""
        for path in (GIPFEL_UNIT, WSGI_UNIT, LV_UNIT):
            lines = [
                ln for ln in path.read_text(encoding="utf-8").splitlines()
                if not ln.lstrip().startswith("#")
            ]
            env = [i for i, ln in enumerate(lines) if ln.strip().startswith("Environment=")]
            env_file = [i for i, ln in enumerate(lines) if ln.strip().startswith("EnvironmentFile=")]
            self.assertTrue(env and env_file, f"{path.name} 缺少 Environment=/EnvironmentFile=")
            self.assertLess(max(env), min(env_file), f"{path.name} 的 Environment 顺序不对")


class X14DiagScriptTests(unittest.TestCase):
    def setUp(self):
        self.text = DIAG.read_text(encoding="utf-8")
        self.code = "\n".join(
            ln for ln in self.text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )

    def test_secret_key_is_masked(self):
        """改前直接把 LOGVIEWER_SECRET_KEY 原文打到 stdout。"""
        self.assertIn("mask", self.code, "必须提供掩码函数")
        # 不得存在「把该键的值原样 echo/print」的语句
        bad = [ln for ln in self.code.splitlines()
               if "LOGVIEWER_SECRET_KEY" in ln and re.search(r'echo\s+"?\$\{?(val|line)\b', ln)
               and "mask" not in ln]
        self.assertEqual(bad, [], f"仍有按键原文输出的语句：{bad}")
        self.assertIn('mask "$val"', self.code)

    def test_all_curls_have_timeouts(self):
        curls = [ln for ln in self.code.splitlines() if ln.strip().startswith("curl ")]
        self.assertTrue(curls, "脚本应至少有一条 curl 探测")
        for ln in curls:
            self.assertTrue(
                "--max-time" in ln or "CURL_OPTS" in ln,
                f"curl 必须带超时（或使用统一的 CURL_OPTS）：{ln.strip()}",
            )
        self.assertRegex(self.code, r"CURL_OPTS=\(.*--connect-timeout.*--max-time")

    def test_port_is_not_hardcoded(self):
        """改前硬编码 8120 —— 应从 .env 的 LOG_VIEWER_PORT 读取。"""
        self.assertIn("LOG_VIEWER_PORT", self.code)
        self.assertRegex(self.code, r'LV_PORT="\$\{LV_PORT:-8120\}"', "缺省兜底应为变量而非写死")
        self.assertNotIn(":8120/", self.code.replace("${LV_PORT}", ""))

    def test_sudo_failure_is_reported(self):
        self.assertIn("have_sudo_n", self.code, "必须检测 sudo -n 是否可用")
        self.assertRegex(self.code, r"\[warn\] 无 NOPASSWD sudo", "sudo 不可用时要明确提示")

    def test_install_dir_is_configurable(self):
        self.assertIn('INSTALL_DIR="${1:-/opt/gipfel}"', self.code, "安装目录应可传入")
        self.assertNotIn("/opt/gipfel/backend/.env", self.code, "不得把安装目录写死在各处")

    def test_masking_behaviour_reproduced(self):
        """等价行为复现：掩码后不得包含原文，且能看出长度。"""
        def mask(v: str) -> str:
            n = len(v)
            if not v:
                return "<空>"
            return f"****(len={n})" if n <= 8 else f"{v[:4]}****(len={n})"

        secret = "s3cr3t-value-that-must-not-leak-0123456789"
        masked = mask(secret)
        self.assertNotIn(secret, masked)
        self.assertNotIn(secret[4:], masked, "除前 4 位外都不得出现")
        self.assertIn("len=42", masked)
        self.assertEqual(mask(""), "<空>")
        short = mask("abc")
        self.assertNotIn("abc", short, "短值整段掩码")


if __name__ == "__main__":
    unittest.main(verbosity=2)
