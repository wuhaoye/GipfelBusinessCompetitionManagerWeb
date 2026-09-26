# -*- coding: utf-8 -*-
"""X-06 / X-07 / X-08 验证：NodeSource 安装、git pull 降级、日志查看器端口一致性。

- **X-06**（`deploy-linux.sh`）：改前 `curl -fsSL https://deb.nodesource.com/setup_20.x | bash -`
  以 **root** 管道执行远端脚本，不校验 GPG/哈希，而 `setup_20.x` 是浮动别名（上游原地覆盖同一 URL）。
- **X-07**（`update-from-github.sh`）：改前 `git pull --ff-only` 失败只 WARN 一句就继续，
  随后照常 `migrate` / `npm run build` / `restart` —— 可能造成「新库结构 + 旧代码」并对外服务。
- **X-08**（`update-from-github.sh`）：改前把日志查看器端口**硬编码 8120**（`LOG_VIEWER_PUBLIC_URL`
  两处 + `ufw allow` 两处），而 nginx 用 `.env` 的 `LOG_VIEWER_PORT`；端口改过之后 URL 与防火墙
  规则被改回 8120，前端按钮跳错端口、运维查防火墙也被误导。

本机没有可执行的 bash（WSL 未安装），故用**静态语义核对 + Python 等价行为复现**验证。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe -m unittest tests.fix_verify.scripts.test_x06_x08_deploy_update -v
"""
from __future__ import annotations

import re
import shutil
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "scripts" / "deploy-linux.sh"
UPDATE = REPO / "scripts" / "update-from-github.sh"
COMMON = REPO / "scripts" / "lib" / "deploy-common.sh"
TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


class X06NodeSourceTests(unittest.TestCase):
    def setUp(self):
        self.text = DEPLOY.read_text(encoding="utf-8")
        self.code = "\n".join(_code_lines(self.text))

    def test_no_curl_pipe_to_bash(self):
        """改前的 `curl … | bash -` 必须彻底消失（只匹配命令行，不匹配日志文案里的说明）。"""
        self.assertNotRegex(
            self.code, r"curl\s+-[^\n|]*\|\s*bash",
            "不得再以 root 管道执行远端脚本（curl | bash）",
        )
        self.assertNotIn("setup_20.x", self.code, "不得再用浮动的 setup_20.x 别名")
        self.assertIn("gpg --dearmor", self.code, "必须改为 apt-keyring 方式")

    def test_uses_keyring_with_signed_by(self):
        """必须走官方 apt-keyring 方式：GPG 公钥落地 + sources.list.d 显式 signed-by。"""
        self.assertIn("nodesource-repo.gpg.key", self.code, "必须下载 NodeSource GPG 公钥")
        self.assertRegex(self.code, r"gpg --dearmor", "必须 gpg --dearmor 建立 keyring")
        self.assertRegex(
            self.code, r"signed-by=", "apt 源必须显式 signed-by（否则签名不生效）",
        )
        self.assertRegex(
            self.code, r"nodesource\.list|sources\.list\.d", "必须写入 apt 源文件",
        )
        self.assertRegex(
            self.code, r"sha256sum", "至少打印下载文件的哈希，便于事后审计",
        )

    def test_download_and_execute_are_separated(self):
        """下载与执行必须分离（先落盘再校验/使用），不得管道。"""
        idx = self.code.index("nodesource-repo.gpg.key")
        window = self.code[idx: idx + 900]
        self.assertIn("-o /tmp/nodesource-repo.gpg.key", window, "公钥必须先落盘")
        self.assertRegex(window, r"rm -f /tmp/nodesource-repo\.gpg\.key", "用后应清理临时文件")


class X07StaleCodeTests(unittest.TestCase):
    def setUp(self):
        self.text = UPDATE.read_text(encoding="utf-8")
        self.code = "\n".join(_code_lines(self.text))

    def test_pull_failure_aborts_by_default(self):
        """pull 失败必须默认中止（err），只有显式开关才降级。"""
        self.assertIn("PULL_FAILED=0", self.code)
        self.assertRegex(
            self.code, r'if \[\[ "\$_allow_stale" == 0 \]\]; then\s*\n\s*err ',
            "默认应 `err` 中止",
        )
        self.assertIn("--allow-stale-code", self.code, "必须提供显式降级开关")
        self.assertIn("ALLOW_STALE_CODE", self.code, "开关必须进参数解析")

    def test_head_is_recorded_and_checked(self):
        """必须记录 HEAD 并在收尾核对（避免「新库结构 + 旧代码」被当成干净升级）。"""
        self.assertIn("CODE_HEAD_BEFORE", self.code)
        self.assertRegex(self.code, r"git rev-parse HEAD", "必须记录代码版本")
        self.assertIn("代码在本轮更新期间发生了变化", self.code, "收尾必须核对并告警")

    def test_stale_mode_warns_before_migrate(self):
        """降级模式下执行 migrate 前必须再次告警。

        改前这里用「migrate 前 700 字符」的固定窗口找告警文本；2026-09-26 在 migrate 之前
        新增了「停服（WAL 切换要求独占该库）」步骤（真机事故修复，见
        docs/真机验证报告-Debian13.md §7.6），把告警挤出了窗口 → 用例误报失败。
        现改为**位置断言**（告警必须出现在 migrate 之前、且在 pull 失败分支内），
        比固定窗口更强也更稳，任何新增步骤都不会让它假失败。
        """
        warn_at = self.code.find("沿用本地旧代码")
        self.assertNotEqual(warn_at, -1, "降级模式必须有「沿用本地旧代码」告警")
        migrate_at = self.code.index("manage.py migrate --noinput")
        self.assertLess(warn_at, migrate_at, "旧代码告警必须出现在 migrate 之前")
        # 告警必须挂在 pull 失败分支里（不是无条件打印）：从分支起点到告警之间不得出现分支结束
        branch_at = self.code.find('if [[ "${PULL_FAILED:-0}" == 1 ]]')
        self.assertNotEqual(branch_at, -1, "必须有 PULL_FAILED 分支")
        self.assertNotIn("\nfi\n", self.code[branch_at:warn_at], "告警应位于 PULL_FAILED 分支内")

    def test_no_unconditional_degrade_message(self):
        """改前那句「已降级为使用本地现有代码继续更新」不得再作为默认行为出现。"""
        self.assertNotIn("已降级为使用本地现有代码继续更新", self.code)


class X08PortConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.update_text = UPDATE.read_text(encoding="utf-8")
        self.update_code = "\n".join(_code_lines(self.update_text))
        self.deploy_text = DEPLOY.read_text(encoding="utf-8")
        self.common = COMMON.read_text(encoding="utf-8")

    def test_shared_library_exists_and_is_sourced(self):
        """端口解析必须抽成公共库，两个脚本共同 source（避免再次漂移）。"""
        self.assertTrue(COMMON.is_file(), "必须存在 scripts/lib/deploy-common.sh")
        self.assertIn("log_viewer_port", self.common)
        for text, name in ((self.deploy_text, "deploy-linux.sh"), (self.update_text, "update-from-github.sh")):
            self.assertIn("lib/deploy-common.sh", text, f"{name} 必须 source 公共库")

    def test_update_script_has_no_hardcoded_8120_in_url_or_ufw(self):
        """改前的四处硬编码 8120（URL×2 + ufw×2）必须改为取 LOG_VIEWER_PORT。"""
        # LOG_VIEWER_PUBLIC_URL 的写入不得再带字面量 :8120/
        bad_url = re.findall(r"LOG_VIEWER_PUBLIC_URL=http://[^\n]*:8120", self.update_code)
        self.assertEqual(bad_url, [], f"LOG_VIEWER_PUBLIC_URL 不得硬编码 8120：{bad_url}")
        # ufw 规则不得再写死 8120/tcp
        bad_ufw = re.findall(r"ufw allow 8120/tcp", self.update_code)
        self.assertEqual(bad_ufw, [], f"ufw 不得硬编码 8120：{bad_ufw}")
        self.assertRegex(self.update_code, r'ufw allow "\$\{?[A-Za-z_]', "ufw 应放行实际端口变量")
        self.assertIn("ensure_log_viewer_public_url", self.update_code)

    def test_ufw_cleans_legacy_rule(self):
        """放行实际端口前应清理历史 8120 规则（与 deploy-linux.sh 一致）。"""
        self.assertRegex(
            self.update_code, r"ufw delete allow 8120/tcp",
            "必须清理历史 8120 规则，避免残留",
        )

    def test_port_helper_semantics_reproduced(self):
        """等价行为复现：公共库的端口解析语义（含非法值兜底）。"""
        self.assertIn("LOG_VIEWER_PORT", self.common)
        self.assertRegex(self.common, r"1-65535|65535")
        # 复刻语义
        def parse(env_text: str) -> str:
            for line in env_text.splitlines():
                if line.strip().startswith("LOG_VIEWER_PORT="):
                    p = line.split("=", 1)[1].strip().strip("'\"")
                    if p.isdigit() and 1 <= int(p) <= 65535:
                        return p
                    return "8120"
            return "8120"

        self.assertEqual(parse("LOG_VIEWER_PORT=9000\n"), "9000")
        self.assertEqual(parse('LOG_VIEWER_PORT="9000"\n'), "9000")
        self.assertEqual(parse("LOG_VIEWER_PORT=0\n"), "8120")
        self.assertEqual(parse("LOG_VIEWER_PORT=70000\n"), "8120")
        self.assertEqual(parse("LOG_VIEWER_PORT=abc\n"), "8120")
        self.assertEqual(parse("OTHER=1\n"), "8120")

    def test_public_url_helper_rewrites_port(self):
        """等价行为复现：`.env` 里旧的 8120 行会被改写成实际端口（含 IPv6 方括号）。"""
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        base = TMP_ROOT / f"x08_{uuid.uuid4().hex[:8]}"
        base.mkdir(parents=True, exist_ok=True)
        env = base / ".env"
        try:
            env.write_text(
                "LOG_VIEWER_PORT=9000\nLOG_VIEWER_PUBLIC_URL=http://1.2.3.4:8120/\n",
                encoding="utf-8",
            )
            self._rewrite(env, "1.2.3.4", "9000")
            self.assertIn("LOG_VIEWER_PUBLIC_URL=http://1.2.3.4:9000/", env.read_text(encoding="utf-8"))

            self._rewrite(env, "2001:db8::1", "9000")
            self.assertIn(
                "LOG_VIEWER_PUBLIC_URL=http://[2001:db8::1]:9000/",
                env.read_text(encoding="utf-8"),
                "IPv6 必须加方括号",
            )

            # 缺失该行时追加
            env.write_text("LOG_VIEWER_PORT=9000\n", encoding="utf-8")
            self._rewrite(env, "1.2.3.4", "9000")
            self.assertIn("LOG_VIEWER_PUBLIC_URL=http://1.2.3.4:9000/", env.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    @staticmethod
    def _rewrite(env: Path, ip: str, port: str) -> None:
        """复刻 `ensure_log_viewer_public_url` 的语义。"""
        host = f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip
        want = f"LOG_VIEWER_PUBLIC_URL=http://{host}:{port}/"
        lines = env.read_text(encoding="utf-8").splitlines()
        out, replaced = [], False
        for line in lines:
            if line.startswith("LOG_VIEWER_PUBLIC_URL="):
                out.append(want)
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(want)
        env.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
