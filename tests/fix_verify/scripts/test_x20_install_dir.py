"""X-20 回归：quick-sync.sh 的 INSTALL_DIR 必须绝对化，push 前必须校验源目录。

改前实测（真实 Git bash，工作目录 `.tmp/x20`）：

    $ bash ../../scripts/quick-sync.sh push user@nonexistent ./no-such-dir
      [INFO] 目录: ./no-such-dir                     ← 原样透传的相对路径
      [WARN] 跳过（不存在）: backend/db.sqlite3
      ...（4 项全部跳过）
      [INFO] 同步完成！                              ← 什么都没做却报"完成"
      exit=0

    $ bash ../../scripts/quick-sync.sh push user@nonexistent empty-install
      [INFO] 目录: empty-install                     ← 仍然是相对路径
      exit=0

    $ source ../../scripts/lib/deploy-common.sh; absolutize_dir .
      bash: absolutize_dir: command not found        ← 当时没有这个helper

即：相对 INSTALL_DIR 按 `$PWD` 解析，rsync 可能把另一个目录里的同名文件当成数据源推给
生产机（覆盖目标机的 db.sqlite3 / .env），而"源目录根本不存在"时也只静默跳过并报成功。

改后实测（同一命令）：
    [1] → [WARN] 安装目录已规范化为绝对路径: …/.tmp/x20/no-such-dir（原值: ./no-such-dir…）
          [ERROR] 推送源不可用：…/no-such-dir/backend 不存在
          exit=1
    [2] → 打印绝对路径 + "与本脚本所在仓库不是同一份"提醒，然后正常跳过（无 rsync），exit=0
    [3] → absolutize_dir: "." → <cwd>；"./a/b/../c" → <cwd>/a/c；"a/" → <cwd>/a；
          "/opt/gipfel//x/.." → /opt/gipfel；"/tmp/../opt" → /opt
    [4] → migrate-server.sh 的相对路径与含 `..` 路径仍被拒绝（X-03 已覆盖），exit=1

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x20_install_dir.py
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
QUICK = REPO / "scripts" / "quick-sync.sh"
LIB = REPO / "scripts" / "lib" / "deploy-common.sh"
MIG = REPO / "scripts" / "migrate-server.sh"
HARNESS = REPO / "code_audit" / "_x20_harness.py"
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"


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
    if found and "WindowsApps" not in found:
        return found
    return None


class X20StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.quick = QUICK.read_bytes().decode("utf-8")
        cls.lib = LIB.read_bytes().decode("utf-8")
        cls.mig = MIG.read_bytes().decode("utf-8")

    def test_shared_helper_exists(self):
        self.assertIn("absolutize_dir() {", self.lib)
        self.assertIn("realpath -m", self.lib, "应优先用 realpath -m")
        self.assertIn('[[ "$p" == /* ]] || p="$PWD/$p"', self.lib, "缺少纯 bash 兜底")

    def test_quick_sync_uses_helper_and_has_fallback(self):
        self.assertIn('_DEPLOY_COMMON="$SCRIPT_DIR/lib/deploy-common.sh"', self.quick)
        self.assertIn('source "$_DEPLOY_COMMON"', self.quick)
        self.assertIn("command -v absolutize_dir", self.quick, "缺少 lib 缺失时的兜底实现")
        self.assertIn('INSTALL_DIR="$(absolutize_dir "$_RAW_INSTALL_DIR")"', self.quick)

    def test_quick_sync_fails_fast_on_bad_push_source(self):
        self.assertIn('if [[ ! -d "$INSTALL_DIR/backend" ]]', self.quick)
        self.assertIn("推送源不可用", self.quick)
        # 未知动作要在循环之前就被拦下（改前藏在循环里）
        head = self.quick.split("for item in")[0]
        self.assertIn("未知操作", head, "未知 ACTION 应在循环之前 fail fast")

    def test_quick_sync_warns_when_source_is_another_checkout(self):
        self.assertIn("REPO_ROOT", self.quick)
        self.assertIn("不是同一份", self.quick)

    def test_migrate_server_still_requires_absolute_install_dir(self):
        self.assertIn('if [[ "$INSTALL_DIR" != /* || ! "$INSTALL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]', self.mig)
        self.assertIn('".."', self.mig)

    def test_quick_sync_is_lf_only(self):
        """X-27 之后 shell 脚本必须**全 LF**（`.gitattributes: *.sh text eol=lf`）。

        本用例原先断言的是 X-27 之前的坏状态（`assertEqual(CRLF, LF)` = "必须是 CRLF"），
        而提交 `179712f` 只新增了 `.gitattributes`、**没同步改这里** —— 于是文件改对之后
        这个断言反而**恒失败**（文件已是 LF=315/CRLF=0，用例却在要求 CRLF）。
        改为断言真正要保证的不变量：不得出现任何 CRLF（Linux 的 bash 不接受）。
        """
        raw = QUICK.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 0, "quick-sync.sh 出现 CRLF —— Linux 的 bash 不接受")
        self.assertGreater(raw.count(b"\n"), 0, "quick-sync.sh 不该是空文件")


class X20RuntimeTests(unittest.TestCase):
    def test_relative_install_dir_is_absolutized_and_validated(self):
        bash = _find_bash()
        if bash is None:
            self.skipTest("本机没有可执行的 bash，跳过真实执行（静态断言已覆盖）")
        if not HARNESS.is_file():
            self.skipTest("code_audit/_x20_harness.py 不存在（非交付物，允许缺失）")

        proc = subprocess.run(
            [str(PY), str(HARNESS)], cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)

        # [1] 相对且不存在的目录 → 规范化 + 中止
        self.assertIn("安装目录已规范化为绝对路径", out)
        self.assertIn("推送源不可用", out)
        block1 = out.split("[1]")[1].split("[2]")[0]
        self.assertIn("exit=1", block1, f"不存在的推送源没有被拒绝：\n{block1}")
        self.assertNotIn("同步完成", block1, "不存在的推送源仍然报『同步完成』")

        # [2] 相对且存在 → 打印绝对路径 + 仓库不一致提醒
        block2 = out.split("[2]")[1].split("[3]")[0]
        self.assertIn("不是同一份", block2)
        self.assertRegex(block2, r"\[INFO\] 目录: /\S+/empty-install")

        # [3] helper 语义
        block3 = out.split("[3]")[1].split("[4]")[0]
        self.assertNotIn("absolutize_dir: command not found", block3)
        self.assertIn("-> /opt/gipfel", block3)
        self.assertIn("-> /opt", block3)

        # [4] migrate-server.sh 仍然拒绝非法路径
        block4 = out.split("[4]")[1]
        self.assertEqual(block4.count("exit=1"), 2, f"migrate-server.sh 未拒绝非法路径：\n{block4}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
