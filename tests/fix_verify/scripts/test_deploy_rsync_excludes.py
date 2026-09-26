"""部署 rsync 排除模式回归：**不得误伤同名 Django app 目录**。

真机事故（2026-09-26，Debian 13 全流程部署复现）：
  `deploy-linux.sh` / `update-from-github.sh` 为保护历史快照归档目录，写了
      --exclude snapshots
  但 rsync 的排除模式**不带前导 `/` 时匹配任意层级同名目录** ——
  `backend/apps/snapshots/` 是 **Django app**（在 INSTALLED_APPS 中），于是：
      1) 该 app 不被同步；2) 叠加 `--delete` 后线上已有的 `apps/snapshots/` 被**删除**；
      3) 服务启动即 `ModuleNotFoundError: No module named 'apps.snapshots'`，
         部署脚本第 644 行 `manage.py check` 直接失败（现场表现为"部署到一半失败"）。
  正确写法：`--exclude=/snapshots/`（前导斜杠 = 锚定到 rsync 传输根，只匹配 `backend/snapshots/`）。

本用例做两件事：
  A. 静态扫描三个部署脚本里所有「`--exclude <裸名>`」模式，若 `backend/apps/<裸名>` 真实存在 → 失败；
  B. 反向验证：`--exclude=/snapshots/` 形式必须存在（证明归档目录仍被保护），且
     `--exclude snapshots` 这种会误伤的形式必须不存在。

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_deploy_rsync_excludes.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = [
    REPO / "scripts" / "deploy-linux.sh",
    REPO / "scripts" / "update-from-github.sh",
    REPO / "scripts" / "migrate-server.sh",
]
APPS_DIR = REPO / "backend" / "apps"

# 允许的「裸名」排除：这些名字不会与 app 目录冲突（断言 A 会再兜一层）
_KNOWN_SAFE = {
    "node_modules", "dist", "db.sqlite3", "uploads", "logs", ".env", "frontend-dist",
    "staticfiles", ".venv", "__pycache__", "*.pyc", "*.sqlite3", "*.bak", "snapshots_test_tmp",
}


def _rsync_excludes(text: str) -> list[tuple[int, str]]:
    """返回 [(行号, 排除模式)]，涵盖 `--exclude X` 与 `--exclude=X` 两种写法。"""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for m in re.finditer(r"--exclude[= ]'?([^\s'\\]+)'?", line):
            out.append((lineno, m.group(1)))
    return out


class RsyncExcludeSafetyTests(unittest.TestCase):
    def test_bare_exclude_never_collides_with_a_django_app(self):
        """裸名排除若与 backend/apps/<name> 同名 → rsync --delete 会删掉该 app。"""
        app_names = {p.name for p in APPS_DIR.iterdir() if p.is_dir() and not p.name.startswith("__")}
        self.assertIn("snapshots", app_names, "apps/snapshots 应当存在（本用例的前提）")

        problems: list[str] = []
        for path in SCRIPTS:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, pat in _rsync_excludes(text):
                bare = pat.strip("/")
                if bare.startswith("$") or "*" in bare:
                    continue
                # 带前导斜杠 = 锚定到传输根 → 安全（不会匹配 apps/<name>）
                if pat.startswith("/"):
                    continue
                if bare in app_names:
                    problems.append(f"{path.name}:{lineno} `--exclude {pat}` 会误伤 Django app `apps/{bare}/`")
        self.assertEqual(problems, [], "存在会误伤同名 app 目录的 rsync 排除模式：\n" + "\n".join(problems))

    def test_snapshot_archive_is_still_protected_and_anchored(self):
        """归档目录必须继续被排除，但只能用锚定形式。"""
        for name in ("deploy-linux.sh", "update-from-github.sh"):
            path = REPO / "scripts" / name
            text = path.read_text(encoding="utf-8", errors="replace")
            code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
            self.assertIn("--exclude=/snapshots/", code, f"{name}: 必须用锚定的 --exclude=/snapshots/ 保护归档目录")
            self.assertNotRegex(
                code, r"--exclude[= ]'?snapshots'?(\s|$)",
                f"{name}: 不得使用非锚定的 `--exclude snapshots`（会连带删掉 apps/snapshots）",
            )

    def test_apps_snapshots_is_importable_package(self):
        """apps/snapshots 必须是一个完整可导入的 Django app（部署脚本不会再把它删掉）。"""
        app_dir = APPS_DIR / "snapshots"
        for f in ("__init__.py", "apps.py", "middleware.py", "gate.py", "engine.py", "views.py", "urls.py"):
            self.assertTrue((app_dir / f).is_file(), f"apps/snapshots/{f} 缺失")


if __name__ == "__main__":
    unittest.main(verbosity=2)
