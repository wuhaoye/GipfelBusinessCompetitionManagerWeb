"""部署脚本的 WAL 安全回归（真机事故驱动）。

真机事故（2026-09-26，Debian 13 全流程部署）：
  C2 阶段 1 把 SQLite 切到 WAL 后，`deploy-linux.sh` / `update-from-github.sh` 仍是
  「不停服 → pip → migrate → … → 最后才 restart」。于是 `migrate` 在**旧版本服务仍持有连接**
  （rollback journal 模式）时把库切到 WAL —— 同一库被两种日志模式并发访问，
  直接得到 `database disk image malformed`：一致性快照之后的所有 `VACUUM INTO` 全部失败、
  线上库各表不可读（真机首个部署就复现，见 docs/真机验证报告-Debian13.md §3.5）。

本用例锁定三条不变量：
  A. 两个部署脚本都必须在 `migrate --noinput` **之前**停掉 gipfel/gipfel-wsgi/gipfel-logviewer；
  B. 失败陷阱必须在「已停服」时尽力把服务拉回运行态（避免部署失败 + 站点长时间 502）；
  C. `deploy-common.sh::append_env_entry` 必须剥掉值两端包裹的引号，
     否则会写出 `"a,b,c",d` 这种 python-dotenv 无法解析的行（真机日志：
     `Python-dotenv could not parse statement starting at line 84`）。

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_deploy_wal_safety.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "scripts" / "deploy-linux.sh"
UPDATE = REPO / "scripts" / "update-from-github.sh"
COMMON = REPO / "scripts" / "lib" / "deploy-common.sh"


def _code(text: str) -> str:
    """去掉纯注释行（保留行号无关的顺序判断）。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


class MigrateNeedsServicesStoppedTests(unittest.TestCase):
    def _assert_stop_before_migrate(self, path: Path):
        code = _code(path.read_text(encoding="utf-8", errors="replace"))
        m = re.search(r'manage\.py"?\s+migrate\s+--noinput', code)
        self.assertIsNotNone(m, f"{path.name}: 未找到 migrate 调用")
        migrate_at = m.start()
        head = code[:migrate_at]
        self.assertRegex(
            head, r"systemctl\s+stop",
            f"{path.name}: migrate 之前没有 `systemctl stop`（WAL 切换必须独占该库）",
        )
        # 停的必须是全部三个写库进程
        stop_lines = [ln for ln in head.splitlines() if "systemctl stop" in ln or "SERVICES_TO_MANAGE" in ln]
        joined = "\n".join(stop_lines)
        self.assertTrue(
            "SERVICES_TO_MANAGE" in joined or
            all(s in joined for s in ("gipfel", "gipfel-wsgi", "gipfel-logviewer")),
            f"{path.name}: 停服未覆盖 gipfel/gipfel-wsgi/gipfel-logviewer",
        )

    def test_deploy_linux_stops_services_before_migrate(self):
        self._assert_stop_before_migrate(DEPLOY)

    def test_update_from_github_stops_services_before_migrate(self):
        self._assert_stop_before_migrate(UPDATE)

    def test_exit_trap_restores_services(self):
        for path in (DEPLOY, UPDATE):
            code = _code(path.read_text(encoding="utf-8", errors="replace"))
            self.assertIn("SERVICES_STOPPED", code, f"{path.name}: 未记录是否停过服务")
            self.assertRegex(
                code, r"SERVICES_STOPPED[\"']?\s*==\s*[\"']?1",
                f"{path.name}: 失败陷阱未按「已停服」分支处理",
            )
            self.assertRegex(
                code, r"systemctl start\s+\"?\$_?svc|\$_svc\b",
                f"{path.name}: 失败陷阱未尝试启动服务",
            )


class AppendEnvEntryQuoteHandlingTests(unittest.TestCase):
    def test_strips_surrounding_quotes(self):
        text = COMMON.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"append_env_entry\(\)\s*\{(?P<body>.*?)\n\}", text, re.S)
        self.assertIsNotNone(m, "未找到 append_env_entry 函数体")
        body = m.group("body")
        self.assertRegex(
            body, r"\$\{cur:1:\$\{#cur\}-2\}|\$\{cur#[\"']\}|\$\{cur%[\"']\}",
            "append_env_entry 未剥掉值两端包裹的引号（会写出 \"a,b\",c 触发 dotenv 解析失败）",
        )

    def test_malformed_allowed_hosts_shape_would_be_normalized(self):
        """模拟修复逻辑：'\"a,b,c\",d' 形式的旧值必须被规范化成 'a,b,c,d'。"""
        text = COMMON.read_text(encoding="utf-8", errors="replace")
        self.assertIn("item=\"${item//\\\"/}\"", text.replace('${item//\\"/}', '${item//\\"/}'),
                      "append_env_entry 未逐项去除引号")


class SqliteSidecarExcludedTests(unittest.TestCase):
    """源目录里的 `db.sqlite3-wal`/`-shm` 绝不能被 rsync 带到线上主库旁边。

    真机事故（2026-09-26）：C2 开启 WAL 后，源目录（含测试库）旁边有 `db.sqlite3-wal`，
    而 rsync 只排除 `db.sqlite3` —— 于是一个**属于另一个数据库的 WAL** 落到线上主库旁边，
    SQLite 尝试按它恢复线上库 → `database disk image is malformed`，此后一致性快照 / migrate /
    VACUUM INTO 全部失败（见 docs/真机验证报告-Debian13.md §3.6）。
    """

    def test_sidecars_are_excluded_with_wildcard(self):
        for path in (DEPLOY, UPDATE):
            code = _code(path.read_text(encoding="utf-8", errors="replace"))
            self.assertIn(
                "'db.sqlite3*'", code,
                f"{path.name}: 必须用 --exclude 'db.sqlite3*' 同时排除 -wal/-shm（只排除 db.sqlite3 不够）",
            )
            self.assertNotRegex(
                code, r"--exclude\s+db\.sqlite3(\s|$)",
                f"{path.name}: 仍存在只排除 db.sqlite3 的写法（会漏掉 -wal/-shm）",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
