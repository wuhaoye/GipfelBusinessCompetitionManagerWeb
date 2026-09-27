"""X-21 回归：migrate-server.sh 的退出码语义、--dry-run 零网络零副作用、--ssh-port 校验。

改前实测（真实 Git bash 5.3.15，全部连本机回环，不会连上任何真实服务器）：

    $ bash scripts/migrate-server.sh --help
      用法: scripts/migrate-server.sh [选项]        exit=0      ← 正确
    $ bash scripts/migrate-server.sh --install_dir /opt/gipfel
      [ERROR] 未知参数: --install_dir               exit=0      ← 参数拼错却报成功
    $ bash scripts/migrate-server.sh --target u@127.0.0.1
      [ERROR] 必须指定 --mode (push 或 pull)         exit=0      ← 必填缺失也报成功
    $ bash scripts/migrate-server.sh --mode push
      [ERROR] 推送模式必须指定 --target USER@HOST    exit=0
    $ bash scripts/migrate-server.sh --mode push --target u@127.0.0.1 --install-dir /tmp --ssh-port abc
      [STEP] Gipfel 服务器迁移工具 …                exit=1      ← 端口没校验，跑到建连才失败
    $ bash scripts/migrate-server.sh --dry-run --mode pull --source u@127.0.0.1 --install-dir /tmp
      [ERROR] 无法连接到源服务器: u@127.0.0.1       exit=1      ← dry-run 竟然真的建连
      /tmp/gipfel-migration-* 数量：运行前=0 运行后=1            ← 还留下临时目录

改后实测（同一探针）：
    --help → exit=0；拼错的参数 → exit=2；缺 --mode → exit=1；缺 --target → exit=1
    --ssh-port abc / 99999 / 0 → [ERROR] --ssh-port 必须是 1-65535 的整数（收到: …） exit=2
    --dry-run --mode pull → [INFO] [DRY-RUN] 将检查与 u@127.0.0.1 的 SSH 连通性 … exit=0
      /tmp/gipfel-migration-* 数量：运行前=0 运行后=0

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x21_migrate_dryrun.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MIG = REPO / "scripts" / "migrate-server.sh"
HARNESS = REPO / "code_audit" / "harness" / "_x21_harness.py"
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
    return found if found and "WindowsApps" not in found else None


class X21StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = MIG.read_bytes().decode("utf-8")
        # 去掉注释行，避免匹配到解释"改前怎么写"的说明
        cls.code = "\n".join(
            ln for ln in cls.text.splitlines() if not ln.lstrip().startswith("#")
        )

    def test_usage_takes_status_code(self):
        self.assertIn('local _usage_status="${1:-0}"', self.code)
        self.assertIn('exit "$_usage_status"', self.code)
        self.assertNotRegex(self.code, r"(?m)^\s*exit 0\s*$", "usage() 里仍有固定 exit 0")

    def test_exit_codes_by_scenario(self):
        self.assertIn("-h|--help)    usage 0 ;;", self.code)
        self.assertIn("未知参数: $1\"; usage 2 ;;", self.code)
        # 三处必填缺失都应是 1
        self.assertEqual(len(re.findall(r"usage 1$", self.code, re.M)), 3)

    def test_ssh_port_is_validated(self):
        self.assertIn('! [[ "$SSH_PORT" =~ ^[0-9]+$ ]]', self.code)
        self.assertIn("SSH_PORT < 1 || SSH_PORT > 65535", self.code)
        self.assertIn("--ssh-port 必须是 1-65535 的整数", self.code)

    def test_connectivity_probe_is_dry_run_aware(self):
        self.assertIn("check_remote_conn() {", self.code)
        self.assertIn("[DRY-RUN] 将检查与 $host 的 SSH 连通性", self.code)
        # 两处连接检查都必须走封装，不能再有裸 ssh "echo ok"
        self.assertNotIn('ssh "${SSH_OPTS[@]}" "$REMOTE" "echo ok"', self.code)
        self.assertEqual(self.code.count('check_remote_conn "$REMOTE"'), 2)

    def test_remote_command_probe_is_dry_run_aware(self):
        self.assertIn("check_remote_command() {", self.code)
        block = self.code.split("check_remote_command() {", 1)[1].split("}", 1)[0]
        self.assertIn('"$DRY_RUN" == true', block, "check_remote_command 没有 dry-run 分支")

    def test_pull_existence_probe_goes_through_remote_exec(self):
        self.assertNotIn('ssh "${SSH_OPTS[@]}" "$REMOTE" "test -e', self.code)
        self.assertIn('remote_exec "$REMOTE" "test -e $INSTALL_DIR/$item"', self.code)

    def test_dry_run_leaves_no_backup_dir(self):
        block = self.code.split("_cleanup_backup() {", 1)[1].split("}", 1)[0]
        self.assertNotIn('"$DRY_RUN" != true', block, "dry-run 仍然跳过清理，会留下临时目录")

    def test_line_endings_are_consistent(self):
        """只要求行尾一致（全 LF 或全 CRLF）——仓库里 `migrate-server.sh` 存的是 LF，
        检出时是否转成 CRLF 取决于 `core.autocrlf`，断言某一种就不稳。"""
        raw = MIG.read_bytes()
        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n")
        self.assertIn(crlf, (0, lf), f"行尾混杂：CRLF={crlf} 总LF={lf}")
        self.assertGreater(lf, 0)


class X21RuntimeTests(unittest.TestCase):
    def test_exit_codes_and_dry_run(self):
        if _find_bash() is None:
            self.skipTest("本机没有可执行的 bash，跳过真实执行（静态断言已覆盖）")
        if not HARNESS.is_file():
            self.skipTest("code_audit/harness/_x21_harness.py 不存在（应随仓库提供，请确认已检出）")

        proc = subprocess.run(
            [str(PY), str(HARNESS)], cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)

        def block(name: str, nxt: str) -> str:
            return out.split(name, 1)[1].split(nxt, 1)[0]

        help_block = block("-- --help --", "[1]")
        self.assertIn("exit=0", help_block)

        unknown = block("拼错的参数", "缺 --mode")
        self.assertIn("exit=2", unknown, f"未知参数未返回 2：\n{unknown}")

        no_mode = block("缺 --mode --", "push 缺")
        self.assertIn("exit=1", no_mode, f"缺 --mode 未返回 1：\n{no_mode}")

        no_target = block("push 缺 --target", "[2]")
        self.assertIn("exit=1", no_target, f"缺 --target 未返回 1：\n{no_target}")

        ports = block("[2] --ssh-port", "[3]")
        self.assertEqual(ports.count("exit=2"), 3, f"三个非法端口未全部返回 2：\n{ports}")
        self.assertIn("--ssh-port 必须是 1-65535 的整数", ports)

        dry = block("[3] --dry-run", "###")
        self.assertIn("exit=0", dry, f"dry-run 未成功预演：\n{dry}")
        self.assertIn("[DRY-RUN] 将检查与 u@127.0.0.1 的 SSH 连通性", dry)
        self.assertNotIn("无法连接到源服务器", dry, "dry-run 仍然发起了真实连接")
        m = re.search(r"运行前=(\d+) 运行后=(\d+)", out)
        self.assertIsNotNone(m, f"没有采到残留目录计数：\n{out}")
        self.assertEqual(m.group(1), m.group(2), "dry-run 留下了临时备份目录")


if __name__ == "__main__":
    unittest.main(verbosity=2)
