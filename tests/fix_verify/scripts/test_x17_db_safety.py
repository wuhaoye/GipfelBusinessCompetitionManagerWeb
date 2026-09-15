"""X-17 回归：tests/ 的连库脚本只能跑一次性测试库，且导入时零副作用。

改前实测（本机 backend/.venv python，真实执行）：

    $ python tests/sqlite_decimal_roundtrip.py
      exit=1
      apps.common.exceptions.BusinessError: Fuel.price_per_liter 需要 27 位有效数字，超过 SQLite
      能精确保存的 15 位…（未捕获异常，靠"raise SystemExit 在事务块内"回滚的写法根本没走到）
      backend.settings 的 DATABASES['default']['NAME'] = <repo>\\backend\\db.sqlite3   ← 真实开发库

    $ python tests/big_number_smoke.py
      结果：PASS=20 FAIL=0   exit=0
      [SKIP] timer._to_num 不存在（符号名不同，跳过——由 T2 专项验证）   ← 第 6 组从未执行

    单纯 import 这两个模块（pytest collection 场景）：
      tests/big_number_smoke.py      → import 期间触发 SystemExit(0)
      tests/sqlite_decimal_roundtrip.py → import 期间 BusinessError（真实库上发起过写事务）

    模块顶层 `django.setup()` / `setdefault("DJANGO_SETTINGS_MODULE")` 调用计数：2 / 2

运行：backend/.venv/Scripts/python.exe tests/fix_verify/scripts/test_x17_db_safety.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

from _decoding import run_captured

REPO = Path(__file__).resolve().parents[3]
TESTS = REPO / "tests"
REAL_DB = REPO / "backend" / "db.sqlite3"
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
SCRIPTS = ["big_number_smoke.py", "sqlite_decimal_roundtrip.py"]

TOP_LEVEL_RE = re.compile(
    r"(?m)^(?!\s)(?:django\.setup\(\)|os\.environ\.setdefault\(\"DJANGO_SETTINGS_MODULE\")"
)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(script: str) -> subprocess.CompletedProcess:
    # 解码走 tests/fix_verify/scripts/_decoding.py：被测脚本输出 UTF-8，但经 Windows
    # 控制台/代码页时会变成 GBK；硬按 UTF-8 解会把中文断言串变成替换符（预先存在的夹具缺陷）。
    return run_captured(
        [str(PY), str(TESTS / script)],
        cwd=str(REPO),
        timeout=600,
    )


class X17StaticTests(unittest.TestCase):
    def test_no_module_level_django_setup(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                text = (TESTS / name).read_text(encoding="utf-8", errors="replace")
                self.assertIsNone(
                    TOP_LEVEL_RE.search(text),
                    f"{name} 仍在模块顶层执行 django.setup()/setdefault(settings)",
                )
                self.assertIn("_bootstrap", text, f"{name} 未走 tests/_bootstrap.py 引导")

    def test_main_guard_present(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                text = (TESTS / name).read_text(encoding="utf-8", errors="replace")
                self.assertIn('if __name__ == "__main__":', text)

    def test_no_silent_skip(self):
        text = (TESTS / "big_number_smoke.py").read_text(encoding="utf-8", errors="replace")
        # 去掉模块/函数 docstring 与注释——它们会引用"改前的坏写法"作为说明
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        code = "\n".join(
            ln for ln in code.splitlines() if not ln.lstrip().startswith("#")
        )
        self.assertNotIn("[SKIP]", code, "big_number_smoke.py 仍存在静默 SKIP 分支")
        self.assertIn("_parse_number_raw", code, "应使用真实存在的函数名 _parse_number_raw")
        self.assertNotIn("_to_num", code, "不得再引用不存在的 _to_num")


class X17RuntimeTests(unittest.TestCase):
    def test_import_has_no_side_effects(self):
        """模块导入（pytest collection）不得触发 SystemExit 或任何异常。"""
        for name in SCRIPTS:
            with self.subTest(script=name):
                probe = (
                    "import importlib.util, sys, pathlib;"
                    f"p = pathlib.Path(r'{TESTS / name}');"
                    "spec = importlib.util.spec_from_file_location('probe_mod', p);"
                    "m = importlib.util.module_from_spec(spec);"
                    "spec.loader.exec_module(m);"
                    "print('IMPORT_OK')"
                )
                proc = run_captured(
                    [str(PY), "-c", probe],
                    cwd=str(REPO),
                    timeout=300,
                )
                self.assertEqual(
                    proc.returncode, 0,
                    f"{name} 导入即产生副作用：\n{proc.stdout}\n{proc.stderr}",
                )
                self.assertIn("IMPORT_OK", proc.stdout)

    def test_scripts_do_not_touch_real_dev_db(self):
        before = _sha256(REAL_DB)
        self.assertIsNotNone(before, "找不到 backend/db.sqlite3，无法验证")
        results = {}
        for name in SCRIPTS:
            proc = _run(name)
            results[name] = proc
        after = _sha256(REAL_DB)
        self.assertEqual(before, after, "运行这两个脚本改动了真实开发库 backend/db.sqlite3")
        for name, proc in results.items():
            with self.subTest(script=name):
                out = (proc.stdout or "") + (proc.stderr or "")
                self.assertEqual(proc.returncode, 0, f"{name} 退出码非 0：\n{out}")
                active = [
                    ln for ln in out.splitlines() if "活动数据库" in ln and "不是真实开发库" not in ln
                ]
                for ln in active:
                    self.assertNotIn(
                        "db.sqlite3", ln, f"{name} 实际连的是真实开发库：{ln}"
                    )
                m = re.search(r"结果：PASS=(\d+) FAIL=(\d+)", out)
                self.assertIsNotNone(m, f"{name} 未输出结果行：\n{out}")
                self.assertEqual(m.group(2), "0", f"{name} 存在 FAIL：\n{out}")

    def test_roundtrip_uses_in_memory_test_db(self):
        proc = _run("sqlite_decimal_roundtrip.py")
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("memory", out, f"未在内存测试库上运行：\n{out}")
        self.assertIn("超精度值必须显式报错", out)
        self.assertIn("15 位有效数字以内必须精确往返", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
