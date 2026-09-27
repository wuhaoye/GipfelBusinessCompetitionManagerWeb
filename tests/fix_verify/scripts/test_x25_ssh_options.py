"""X-25 回归：SSH 选项数组化、端口/私钥可配置、首次连接不再静默 accept-new。

改前实测（真实 Git bash；A 段把脚本里的 SSH_OPTS 构建块原样抽出来求值，B 段用
`.tmp/x25/empty-install`（只有空 backend/，无任何同步项）跑真实脚本，不会触发 rsync）：

    ### X-25 探针
    [A] SSH_OPTS 构建块
      （改前：脚本里没有 SSH_OPTS 构建块，A 段跳过）
    [B] 真实脚本的前置路径
      -- push empty-install（默认） -> exit=0
         …（没有任何关于主机密钥的提示）
      -- push empty-install --ssh-port 2222 --ssh-key /tmp/k --ssh-known-hosts /tmp/kh -> exit=0
         …（三个开关被**静默忽略**）
      -- push empty-install --ssh-port abc -> exit=0
         …（非法端口也不报错）

    即：quick-sync.sh 完全不支持自定义端口/私钥（非 22 端口上根本不能用），只用硬编码的
    `-e "ssh -o StrictHostKeyChecking=accept-new"`（首次连接自动信任任意主机密钥），
    没有 `-o BatchMode`（密钥不可用时会回退到交互式口令提示把脚本挂住），端口也不校验。

改后实测（同一探针）：
      RSYNC_SSH=[ssh -p 22 -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new]
      RSYNC_SSH=[ssh -p 2222 … -i /home/First\\ Last/.ssh/id_ed25519]      ← 带空格的私钥路径不被拆开
      --ssh-port abc -> exit=2  [ERROR] --ssh-port 必须是 1-65535 的整数（收到: abc）
      指定 known_hosts → RSYNC_SSH=[ssh -p 2222 … -o UserKnownHostsFile=/etc/ssh/known_hosts -o StrictHostKeyChecking=yes]

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x25_ssh_options.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
QUICK = REPO / "scripts" / "quick-sync.sh"
MIG = REPO / "scripts" / "migrate-server.sh"
HARNESS = REPO / "code_audit" / "harness" / "_x25_harness.sh"
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


def _code_of(path: Path) -> str:
    return "\n".join(
        ln
        for ln in path.read_bytes().decode("utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )


class X25StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.quick_code = _code_of(QUICK)
        cls.mig_code = _code_of(MIG)

    def test_quick_sync_builds_ssh_opts_array(self):
        self.assertIn("SSH_OPTS=(-p \"$SSH_PORT\" -o BatchMode=yes -o ConnectTimeout=10)", self.quick_code)
        self.assertIn('RSYNC_SSH="ssh ${_ssh_opts_q% }"', self.quick_code)
        self.assertIn("printf -v _ssh_opts_q '%q '", self.quick_code)

    def test_quick_sync_validates_port(self):
        self.assertIn('! [[ "$SSH_PORT" =~ ^[0-9]+$ ]]', self.quick_code)
        self.assertIn("SSH_PORT < 1 || SSH_PORT > 65535", self.quick_code)

    def test_quick_sync_parses_new_flags(self):
        for flag in ("--ssh-port", "--ssh-key", "--ssh-known-hosts"):
            self.assertIn(flag, self.quick_code, f"未解析 {flag}")
        # 位置参数兼容：push host [install-dir] 仍可用
        self.assertIn('ACTION="${_pos[0]:-}"', self.quick_code)
        self.assertIn('INSTALL_DIR="${_pos[2]:-/opt/gipfel}"', self.quick_code)

    def test_quick_sync_known_hosts_switches_to_strict(self):
        self.assertIn('-o "UserKnownHostsFile=$SSH_KNOWN_HOSTS" -o StrictHostKeyChecking=yes',
                      self.quick_code)

    def test_quick_sync_all_rsync_use_shared_ssh_string(self):
        self.assertNotIn('ssh -o StrictHostKeyChecking=accept-new', self.quick_code,
                         "仍有硬编码的 rsync -e ssh 选项")
        self.assertEqual(self.quick_code.count('-e "$RSYNC_SSH"'), 4, "四处 rsync 未统一")

    def test_no_duplicate_insertions(self):
        self.assertEqual(self.quick_code.count("SSH_OPTS=("), 1)
        self.assertEqual(self.quick_code.count("RSYNC_SSH="), 1)
        self.assertEqual(self.quick_code.count("_pos=()"), 1)
        self.assertEqual(self.quick_code.count("SYNC_ITEMS=("), 1)

    def test_migrate_server_known_hosts_flag(self):
        self.assertIn('--ssh-known-hosts) SSH_KNOWN_HOSTS="$2"; shift 2 ;;', self.mig_code)
        self.assertIn('-o "UserKnownHostsFile=$SSH_KNOWN_HOSTS" -o StrictHostKeyChecking=yes',
                      self.mig_code)
        self.assertIn('-o BatchMode=yes', self.mig_code)
        # accept-new 只在显式给值的那一支之外出现一次
        self.assertEqual(self.mig_code.count("StrictHostKeyChecking=accept-new"), 1)

    def test_key_path_caveat_is_documented(self):
        for path in (QUICK, MIG):
            text = path.read_bytes().decode("utf-8")
            self.assertIn("ps", text, f"{path.name} 未提示 -i 会把私钥路径暴露在 ps 中")


class X25RuntimeTests(unittest.TestCase):
    def test_ssh_opts_and_flags(self):
        if _find_bash() is None:
            self.skipTest("本机没有可执行的 bash，跳过真实执行（静态断言已覆盖）")
        if not HARNESS.is_file():
            self.skipTest("code_audit/harness/_x25_harness.sh 不存在（应随仓库提供，请确认已检出）")

        proc = subprocess.run(
            [_find_bash(), str(HARNESS)], cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)

        # A 段：默认 / 自定义端口 / known_hosts 三条 RSYNC_SSH
        self.assertIn("-p 22 -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new", out)
        # %q 必须把带空格的私钥路径转义成单个 argv（反斜杠 + 空格）
        self.assertIn("-i /home/First\\ Last/.ssh/id_ed25519", out)
        self.assertIn("-p 2222", out)
        self.assertIn("-o UserKnownHostsFile=/etc/ssh/known_hosts -o StrictHostKeyChecking=yes", out)
        self.assertIn("--ssh-port 必须是 1-65535 的整数（收到: abc）", out)
        self.assertIn("已用 -i 指定私钥；该路径会出现在同机 ps 中", out)

        # B 段：真实脚本的退出码
        self.assertRegex(out, r"push empty-install（默认） -> exit=0")
        self.assertRegex(out, r"--ssh-key /tmp/k --ssh-known-hosts /tmp/kh -> exit=0")
        self.assertRegex(out, r"--ssh-port abc -> exit=2")
        self.assertRegex(out, r"缺参时打印用法 -> exit=1")

        # 指定了 known_hosts 就不该再出现 accept-new 警告
        block = out.split("--ssh-key /tmp/k")[1].split("--")[0]
        self.assertNotIn("未指定 --ssh-known-hosts", block)

        # C 段：migrate-server.sh
        self.assertIn("--ssh-known-hosts 出现次数: 3", out)
        self.assertIn("仅给 --ssh-known-hosts 不给 --mode -> exit=1（应 1）", out)

        self.assertNotIn("A 段跳过", out, f"脚本里没有 SSH_OPTS 构建块：\n{out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
