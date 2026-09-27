"""X-24 回归：部署脚本必须把"降级继续"的分支折算成退出码，而不是永远报成功。

改前实测（真实 Git bash；把 deploy-linux.sh 的「问题计数定义块」与「收尾块」原样抽出来，
注入模拟的问题数）：

    ### X-24 探针（定义块=无-无，收尾块=653-656）
      defs.sh 行数=0（0 表示改前没有该定义块）
      问题数=0 --allow-partial=0 -> exit=0  最后一行: [OK] 部署完成！
      问题数=0 --allow-partial=1 -> exit=0  最后一行: [OK] 部署完成！
      问题数=3 --allow-partial=0 -> exit=0  最后一行: [OK] 部署完成！
      问题数=3 --allow-partial=1 -> exit=0  最后一行: [OK] 部署完成！
      reload 失败分支用 problem: 0 / start 失败: 0 / nginx 非 active: 0
      日志查看器探针失败用 problem: 0 / /api/health 探针失败用 problem: 0
      收尾处仍无条件 ok "部署完成！": 1（应为 0）

    即：`nginx -t` 只校验语法不检测端口冲突，reload/start 失败（或站点仍是旧配置）时脚本
    只打一行 warn，结尾照样 `[OK] 部署完成！` 且退出码 0 —— CI 会把坏掉的部署判定为成功。

改后实测（同一探针）：
    ### X-24 探针（定义块=34-36，收尾块=703-717）
      问题数=0 --allow-partial=0 -> exit=0  最后一行: [OK] 部署完成！
      问题数=0 --allow-partial=1 -> exit=0  最后一行: [OK] 部署完成！
      问题数=3 --allow-partial=0 -> exit=1  最后一行: [ERROR] 部署未完全成功：…
      问题数=3 --allow-partial=1 -> exit=0  最后一行: [OK] 部署完成（有 3 项待人工确认）！
      各 problem 分支计数 1/1/1/2/2；收尾处无条件 ok 行: 0

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x24_deploy_exit_code.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "scripts" / "deploy-linux.sh"
HARNESS = REPO / "code_audit" / "harness" / "_x24_harness.sh"
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
    return found and "WindowsApps" not in found and found or None


class X24StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = DEPLOY.read_bytes().decode("utf-8")
        cls.code = "\n".join(
            ln for ln in cls.text.splitlines() if not ln.lstrip().startswith("#")
        )

    def test_problem_counter_exists(self):
        self.assertIn("DEPLOY_PROBLEMS=0", self.code)
        self.assertRegex(self.code, r"problem\(\)\s*\{\s*DEPLOY_PROBLEMS=")

    def test_degraded_branches_use_problem(self):
        for pattern in (
            r'problem "nginx reload 失败',
            r'problem "nginx 启动失败',
            r'problem "nginx 未处于 active',
            r'problem "日志查看器站点探针',
            r'problem "后端 /api/health 探针',
            r'problem "80 端口仍返回 nginx 默认欢迎页',
        ):
            self.assertRegex(self.code, pattern, f"缺少计入问题数的分支：{pattern}")

    def test_footer_no_longer_always_succeeds(self):
        self.assertNotRegex(
            self.code, r'(?m)^ok "部署完成！"$',
            "收尾仍然无条件报成功",
        )
        self.assertIn('if [[ "$DEPLOY_PROBLEMS" -gt 0 ]]; then', self.code)

    def test_allow_partial_flag_is_documented_and_parsed(self):
        self.assertIn("--allow-partial)       ALLOW_PARTIAL=1; shift ;;", self.code)
        self.assertIn("--allow-partial", self.text)

    def test_functional_probes_exist(self):
        self.assertIn("curl -s -o /dev/null -w '%{http_code}' --max-time 5", self.code)
        self.assertIn("http://127.0.0.1:${LV_PORT}/", self.code)
        self.assertIn("http://127.0.0.1/api/health", self.code)

    def test_port_precheck_before_reload(self):
        pre = self.code.find("sport = :${LV_PORT}")
        reload_at = self.code.find("systemctl reload nginx")
        self.assertNotEqual(pre, -1, "缺少 ${LV_PORT} 占用预检")
        self.assertNotEqual(reload_at, -1)
        self.assertLess(pre, reload_at, "端口预检必须在 reload/start 之前")

    def test_line_endings_consistent(self):
        raw = DEPLOY.read_bytes()
        self.assertIn(raw.count(b"\r\n"), (0, raw.count(b"\n")))


class X24RuntimeTests(unittest.TestCase):
    def test_exit_code_decision_table(self):
        if _find_bash() is None:
            self.skipTest("本机没有可执行的 bash，跳过真实执行（静态断言已覆盖）")
        if not HARNESS.is_file():
            self.skipTest("code_audit/harness/_x24_harness.sh 不存在（应随仓库提供，请确认已检出）")

        proc = subprocess.run(
            [_find_bash(), str(HARNESS)], cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)

        table = {
            (p, a): rc
            for p, a, rc in re.findall(
                r"问题数=(\d) --allow-partial=(\d) -> exit=(\d+)", out
            )
        }
        self.assertEqual(len(table), 4, f"没有采到四条组合：\n{out}")
        self.assertEqual(table[("0", "0")], "0", "没有问题时应成功")
        self.assertEqual(table[("0", "1")], "0")
        self.assertNotEqual(table[("3", "0")], "0", "有问题时默认必须非 0 退出")
        self.assertEqual(table[("3", "1")], "0", "--allow-partial 应显式接受当前状态")

        self.assertIn("定义块=34-", out, f"没有抽到问题计数定义块：\n{out}")
        self.assertIn('收尾处仍无条件 ok "部署完成！": 0（应为 0）', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
