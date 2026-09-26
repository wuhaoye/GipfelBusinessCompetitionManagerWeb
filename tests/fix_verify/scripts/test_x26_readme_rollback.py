"""X-26 回归：镜像建议不得再推荐 `--global`，回滚章节必须覆盖代码与数据两侧。

改前状态：
  * `deploy/README.md:42-43` 与 `scripts/update-from-github.sh:183` 都推荐
        git config --global url."https://ghproxy.net/https://github.com/".insteadOf "https://github.com/"
    它会把机器上**所有** `https://github.com/` 请求（含携带 `Authorization` 头的私有仓库请求）
    改写到第三方代理域名，代理可记录甚至篡改代码；文档只提醒"镜像可用性随时间变化"，
    既没说信任风险，也没给撤销方法。
  * `deploy/README.md:232-238` 的「回滚」只有两行：
        sudo -u gipfel cp /opt/gipfel/_backup/<时间戳>/db.sqlite3 /opt/gipfel/backend/
        sudo systemctl restart gipfel
    但 `_backup/*` **只含数据不含代码与 frontend-dist**：按文档"回滚"后仍在跑新版代码，
    得到"数据与代码版本不匹配"的更复杂故障；`cp` 失败也没有任何校验。

★ C1-a / C2 追加（2026-09，本用例随之加强，不放松）：
  · 写库的进程从 2 个变成 3 个（多了 gunicorn/WSGI），所以不再断言
    "systemctl stop gipfel gipfel-logviewer" 这一字面量，改为断言**同一条命令覆盖三个进程**；
  · C2 阶段 1 起 SQLite 为 WAL 模式，回滚前备份当前库不再能用 `cp -a` 活库，
    改为要求 `VACUUM INTO` 自洽导出 + 恢复前 `rm -f db.sqlite3-wal/-shm`——
    断言的是更强的性质（副本自洽、残留已清），比原来的字面量匹配更能防回归。

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x26_readme_rollback.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
README = REPO / "deploy" / "README.md"
UPDATE = REPO / "scripts" / "update-from-github.sh"


def _find_bash() -> str | None:
    for cand in (r"D:\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"):
        p = Path(cand)
        if not p.is_file() or "WindowsApps" in str(p):
            continue
        try:
            proc = subprocess.run(
                [str(p), "-c", "echo ok"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            if proc.returncode == 0 and "ok" in proc.stdout:
                return str(p)
        except Exception:  # noqa: BLE001
            continue
    found = shutil.which("bash")
    return found if found and "WindowsApps" not in found else None


def _extract_bash_function(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    lines = text[start:].split("\n")
    out = [lines[0]]
    for ln in lines[1:]:
        out.append(ln)
        if ln.strip() == "}":
            break
    return "\n".join(out)


class X26MirrorAdviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = README.read_bytes().decode("utf-8")

    def test_readme_no_longer_recommends_global_config(self):
        # 只允许出现在"不要用 --global"/"撤销全局配置"的语境里
        for m in re.finditer(r"git config --global url\.", self.readme):
            line = self.readme[: m.start()].split("\n")[-1]
            self.assertTrue(
                "--unset" in line or "不要用 `--global`" in line or "撤销" in line,
                f"仍在推荐全局 git 配置：{line.strip()}",
            )

    def test_readme_uses_repo_local_config_with_undo(self):
        self.assertIn('git config url."https://ghproxy.net/https://github.com/".insteadOf', self.readme)
        self.assertIn("--unset", self.readme)
        self.assertIn("不要用 `--global`", self.readme)
        self.assertIn("第三方代理", self.readme)

    def test_update_script_hint_is_repo_local(self):
        text = UPDATE.read_bytes().decode("utf-8").replace("\r\n", "\n")
        body = _extract_bash_function(text, "_pull_failed_hint")
        # bash 里引号是转义的（\"），这里只断言关键片段
        self.assertIn("git -C $INSTALL_DIR config url.", body)
        self.assertIn("insteadOf", body)
        self.assertIn("--unset", body)
        self.assertNotIn('warn "  git config --global url.', body)


class X26RollbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = README.read_bytes().decode("utf-8")
        # 「回滚」章节正文
        cls.section = cls.readme.split("### 回滚", 1)[1].split("\n---", 1)[0]

    def test_section_warns_backup_is_data_only(self):
        self.assertIn("只含**数据**", self.section)
        self.assertIn("不含代码", self.section)

    def test_section_covers_code_and_data(self):
        for needle in (
            "git -C /opt/GipfelBusinessCompetitionManagerWeb checkout",
            "rev-parse HEAD",
            "pip install -r",
            "npm ci",
            "manage.py migrate <app>",
            "api/health",
        ):
            self.assertIn(needle, self.section, f"回滚章节缺少：{needle}")

        # 回滚前必须先备份「当前」这份库。C2 阶段 1 起 SQLite 是 WAL 模式，
        # 对**活库** `cp` 会漏掉 -wal 里的事务、甚至拿到页不一致的文件，
        # 因此不再要求旧的 `cp -a .../db.sqlite3` 写法，而要求停服后用 VACUUM INTO 导出自洽副本
        # （用更强的方法满足同一意图：回滚前一定有一份可用的当前库副本）。
        self.assertIn("before-rollback", self.section, "必须先备份当前库（回滚本身也可能出错）")
        self.assertIn("VACUUM INTO", self.section, "活库副本必须用 VACUUM INTO 导出（WAL 安全）")

        # 停/起服：C1-a 之后写库的进程是三个（gipfel / gipfel-wsgi / gipfel-logviewer），
        # 命令必须都带上 —— 只停两个会出现"以为停了、其实 WSGI 还在写库"。
        stop_lines = [ln for ln in self.section.splitlines() if "systemctl stop" in ln]
        start_lines = [ln for ln in self.section.splitlines() if "systemctl start" in ln]
        self.assertTrue(stop_lines and start_lines, "回滚章节必须给出停服/起服命令")
        for lines, label in ((stop_lines, "停服"), (start_lines, "起服")):
            self.assertTrue(
                any(
                    all(s in ln for s in ("gipfel", "gipfel-wsgi", "gipfel-logviewer"))
                    for ln in lines
                ),
                f"{label}命令必须同时覆盖 daphne/WSGI/日志查看器三个进程：{lines}",
            )

        # WAL：恢复前先删残留
        self.assertIn("db.sqlite3-wal", self.section, "恢复前必须删 -wal 残留")
        self.assertIn("db.sqlite3-shm", self.section, "恢复前必须删 -shm 残留")

    def test_section_no_longer_is_two_liner(self):
        self.assertGreater(len(self.section.splitlines()), 20, "回滚章节仍然过短")
        self.assertNotRegex(
            self.section, r"当前代码目录改名",
            "仍保留旧的『只恢复数据库』说法",
        )

    def test_section_prefers_git_revert(self):
        self.assertIn("git revert", self.section)


class X26RuntimeTests(unittest.TestCase):
    def test_pull_failed_hint_output(self):
        bash = _find_bash()
        if bash is None:
            self.skipTest("本机没有可执行的 bash，跳过真实执行（静态断言已覆盖）")

        body = _extract_bash_function(UPDATE.read_bytes().decode("utf-8"), "_pull_failed_hint")
        script = (
            'warn() { echo "[WARN] $*"; }\n'
            'INSTALL_DIR=/opt/gipfel\n'
            f"{body}\n"
            "_pull_failed_hint\n"
        )
        proc = subprocess.run(
            [bash, "-c", script], cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn('git -C /opt/gipfel config url.', out)
        self.assertIn("--unset", out)
        self.assertIn("不要用 --global", out)
        self.assertIn("--allow-stale-code", out)
        # 不能出现"推荐全局配置"的那一行
        self.assertNotIn('  git config --global url."https://ghproxy.net', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
