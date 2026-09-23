# -*- coding: utf-8 -*-
"""X-30 验证：运行用户（gipfel）创建路径 —— PATH 依赖、`|| true` 吞错、`-U` 与已存在组冲突。

真机事故（Debian 13 / VMware，`su` 不带 `-` 部署）：
- `useradd` 位于 `/usr/sbin`，而 `su` 的 PATH 是 `/usr/local/bin:/usr/bin:/bin:/usr/games`；
  `apt-get`/`chown`/`systemctl` 都在 `/usr/bin`，所以环境"看起来完全正常"。
- 改前建用户写成 `useradd … || true` → `command not found`(127) 被吞掉，脚本跑完 migrate +
  前端构建后，在 `chown -R gipfel:gipfel` 处以 `chown: invalid user: 'gipfel:gipfel'` 终止：
  现场留下「`.venv`/`db.sqlite3`/`frontend-dist` 都在、却没有 `gipfel` 用户与 systemd 单元」
  的半成品部署，排查方向被引向文件权限。
- 守卫 `id gipfel` 只查用户，而 `-U` 要求同名组未被占用：「组在、用户不在」时 `useradd -U`
  退出 9（`group gipfel exists … use -g`），同样被吞掉；该状态真实可达 ——
  组内还有其它成员时 `userdel gipfel` 不会删组。
- 同一 PATH 依赖还会让 `nginx -t`（`/usr/sbin/nginx`）二次引爆。

本机无 Linux/bash 保证，故沿用本目录既有约定：**静态语义核对 + Python 等价行为复现**
（真机端到端另由 `code_audit/_repro_gipfel_user_group.sh` 与 Debian 13 实测覆盖）。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe -m unittest tests.fix_verify.scripts.test_x30_runtime_user -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "scripts" / "deploy-linux.sh"
UPDATE = REPO / "scripts" / "update-from-github.sh"
MIGRATE = REPO / "scripts" / "migrate-server.sh"
COMMON = REPO / "scripts" / "lib" / "deploy-common.sh"
README = REPO / "deploy" / "README.md"
MIGRATION_DOC = REPO / "docs" / "MIGRATION.md"

PATH_EXPORT = 'export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _code(text: str) -> str:
    return "\n".join(_code_lines(text))


def _function_body(text: str, name: str) -> str:
    """取出 shell 函数体（`name() {` 到顶格 `}`），找不到返回空串。"""
    m = re.search(rf"^{re.escape(name)}\(\)\s*\{{", text, re.M)
    if not m:
        return ""
    start = m.end()
    end = text.find("\n}", start)
    return text[start: end if end != -1 else len(text)]


def _swallowed_useradd(text: str) -> list[str]:
    """找出「useradd 建用户的结果被 `|| true` 吞掉」的行。

    放行 `command -v useradd … || true`（那是探测命令是否存在，不是建用户）。
    """
    bad = []
    for ln in _code_lines(text):
        if "useradd" not in ln or "||" not in ln:
            continue
        if re.search(r"command\s+-v\s+useradd", ln):
            continue
        if re.search(r"\|\|\s*true\s*\)?\s*$", ln):
            bad.append(ln.strip())
    return bad


class X30CommonHelperTests(unittest.TestCase):
    """公共库里的 ensure_runtime_user 必须幂等、可诊断、兼容「组在、用户不在」。"""

    @classmethod
    def setUpClass(cls):
        cls.text = COMMON.read_text(encoding="utf-8")
        cls.body = _function_body(cls.text, "ensure_runtime_user")

    def test_helper_exists(self):
        self.assertTrue(self.body, "scripts/lib/deploy-common.sh 必须定义 ensure_runtime_user")

    def test_checks_user_and_group_together(self):
        """守卫必须同时校验用户与组（改前只查 `id gipfel`）。"""
        self.assertIn('id "$user"', self.body, "必须校验用户存在")
        self.assertIn('getent group "$group"', self.body, "必须校验组存在")

    def test_reuses_existing_group_with_g(self):
        """组已存在时必须用 `-g` 复用（用 `-U` 会以 “group … exists” 退出 9）。"""
        self.assertIn('-g "$group" -d "$dir" "$user"', self.body, "组已存在 → useradd -g")
        idx_g = self.body.index('-g "$group"')
        self.assertIn('if getent group "$group"', self.body[:idx_g],
                      "`-g` 分支必须由「组已存在」判定守卫")

    def test_creates_group_when_absent(self):
        """用户与组都不存在时用 `-U` 一次建出。"""
        self.assertIn('-U -d "$dir" "$user"', self.body, "两者都不存在 → useradd -U")
        idx_u = self.body.index('-U -d "$dir"')
        self.assertIn("else", self.body[:idx_u], "`-U` 分支必须是「组不存在」的另一支")

    def test_backfills_missing_group(self):
        """用户已在而组缺失时，useradd 会因「用户已存在」失败，必须走 groupadd 补组。"""
        self.assertIn("groupadd", self.body, "必须能补建缺失的组")
        self.assertIn('-r "$group"', self.body, "补组用 groupadd -r")

    def test_never_swallows_useradd_failure(self):
        self.assertNotRegex(self.body, r'"\$bin"\s+-r[^\n]*\|\|\s*true',
                            "建用户的结果不得被 `|| true` 吞掉")

    def test_fails_loudly_with_actionable_hint(self):
        """命令缺失与创建失败都必须 err 中止，且提示里给出 PATH 与 `su -` 的成因。"""
        self.assertGreaterEqual(self.body.count("err "), 2, "至少两处 err：命令缺失 + 创建失败")
        self.assertIn("PATH=$PATH", self.body, "错误信息必须回显 PATH 便于定位")
        self.assertIn("su -", self.body, "错误信息必须点明 `su` 非登录 shell 的 PATH 不含 /usr/sbin")


class X30PathNormalizationTests(unittest.TestCase):
    """三个脚本都必须自己补齐 /usr/sbin，不再假设调用者的 PATH 完整。"""

    # 各脚本里「真正依赖 sbin 或运行用户」的可执行锚点（帮助文本/注释里出现的同名串不在此列：
    # `_code_lines` 已剥注释，锚点取命令行而非 echo/heredoc 文案）
    ANCHORS = {
        "deploy-linux.sh": ("apt-get install", "ensure_runtime_user gipfel"),
        "update-from-github.sh": ("ensure_runtime_user gipfel",),
        "migrate-server.sh": ("_ensure_runtime_user_cmd()",),
    }

    def test_all_scripts_export_sbin_path(self):
        for path in (DEPLOY, UPDATE, MIGRATE):
            text = path.read_text(encoding="utf-8")
            self.assertIn(PATH_EXPORT, text, f"{path.name} 必须显式导出含 /usr/sbin 的 PATH")

    def test_path_export_precedes_sbin_dependent_steps(self):
        for path in (DEPLOY, UPDATE, MIGRATE):
            code = _code(path.read_text(encoding="utf-8"))
            idx_path = code.index(PATH_EXPORT)
            for anchor in self.ANCHORS[path.name]:
                self.assertLess(idx_path, code.index(anchor),
                                f"{path.name}: PATH 补齐必须早于 `{anchor}`")
            # 且必须靠近文件头部（参数解析/帮助文本之后即可，不能拖到主体流程里）
            self.assertLessEqual(code[:idx_path].count("\n"), 200,
                                 f"{path.name}: PATH 补齐必须位于文件头部")

    def test_readme_warns_about_su_without_dash(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("su -", text, "deploy/README.md 必须写明用 `su -`（登录 shell）")
        self.assertIn("/usr/sbin", text, "必须解释 `su` 的 PATH 不含 /usr/sbin 这一成因")

    def test_migration_doc_creates_group(self):
        text = MIGRATION_DOC.read_text(encoding="utf-8")
        self.assertIn("useradd -r -s /usr/sbin/nologin -U gipfel", text,
                      "docs/MIGRATION.md 的手工建用户步骤必须补 -U（否则不建同名组）")
        self.assertNotIn("useradd -r -s /usr/sbin/nologin gipfel 2>/dev/null || true", text,
                         "不得再用 `2>/dev/null || true` 掩盖建用户失败")


class X30DeployScriptTests(unittest.TestCase):
    """deploy-linux.sh / update-from-github.sh 必须走 helper，且不在 chown 处才暴露失败。"""

    def setUp(self):
        self.texts = {p.name: p.read_text(encoding="utf-8") for p in (DEPLOY, UPDATE)}

    def test_no_swallowed_useradd(self):
        for name, text in self.texts.items():
            self.assertEqual([], _swallowed_useradd(text),
                             f"{name} 不得再把建用户的失败吞进 `|| true`")

    def test_calls_common_helper(self):
        for name, text in self.texts.items():
            self.assertIn('ensure_runtime_user gipfel "$INSTALL_DIR"', _code(text),
                          f"{name} 必须调用公共库的 ensure_runtime_user")

    def test_guards_against_missing_lib(self):
        """lib 缺失时 ensure_runtime_user 未定义 —— 必须显式报错而不是 command not found。"""
        for name, text in self.texts.items():
            self.assertIn("command -v ensure_runtime_user", _code(text),
                          f"{name} 必须显式检查 helper 是否可用")

    def test_chown_preceded_by_user_group_assertion(self):
        """chown 之前必须再确认用户/组存在（避免再次以 `chown: invalid user` 误导排障）。"""
        for name, text in self.texts.items():
            code = _code(text)                      # 只看命令行，排除注释里对 chown 的引用
            idx = code.index("chown -R gipfel:gipfel")
            self.assertIn("getent group gipfel", code[:idx],
                          f"{name}: `chown -R gipfel:gipfel` 之前必须有用户/组断言")


class X30MigrateServerTests(unittest.TestCase):
    """migrate-server.sh：建用户补 -U、移到 chown 之前、不再静默吞错。"""

    @classmethod
    def setUpClass(cls):
        cls.text = MIGRATE.read_text(encoding="utf-8")
        cls.code = _code(cls.text)

    def test_useradd_always_pins_group(self):
        """每一处 useradd 都必须显式定组（-U 建同名组 或 -g 复用），不能依赖 USERGROUPS_ENAB。"""
        calls = [ln.strip() for ln in _code_lines(self.text) if "useradd" in ln
                 and not re.search(r"command\s+-v\s+useradd", ln)]
        self.assertTrue(calls, "migrate-server.sh 应有建用户调用")
        for ln in calls:
            self.assertRegex(ln, r"-(U|g)\b", f"useradd 必须带 -U 或 -g：{ln}")

    def test_remote_helper_snippet_exists(self):
        body = _function_body(self.text, "_ensure_runtime_user_cmd")
        self.assertTrue(body, "必须定义 _ensure_runtime_user_cmd")
        self.assertIn("-U -d", body)
        self.assertIn("-g gipfel", body)
        self.assertIn("getent group gipfel", body)

    def test_push_chown_after_user_creation_and_not_swallowed(self):
        self.assertNotIn("chown -R gipfel:gipfel '$INSTALL_DIR/backend' 2>/dev/null || true",
                         self.code, "push 模式的 chown 不得再吞错")
        idx_ensure = self.code.index('remote_exec "$REMOTE" "$(_ensure_runtime_user_cmd)"')
        idx_chown = self.code.index("chown -R gipfel:gipfel '$INSTALL_DIR/backend'")
        self.assertLess(idx_ensure, idx_chown, "push 模式必须先建用户再 chown")

    def test_pull_chown_after_user_creation(self):
        idx_ensure = self.code.index('eval "$(_ensure_runtime_user_cmd)"')
        idx_chown = self.code.index('sudo chown -R gipfel:gipfel "$INSTALL_DIR/backend"')
        self.assertLess(idx_ensure, idx_chown, "pull 模式必须先建用户再 chown")


class X30DecisionTableModelTests(unittest.TestCase):
    """等价行为复现：helper 的判定表（与脚本分支结构逐条对应）。"""

    @staticmethod
    def _expected(user_exists: bool, group_exists: bool) -> str:
        if user_exists and group_exists:
            return "noop"
        if not user_exists:
            return "useradd -g" if group_exists else "useradd -U"
        return "groupadd"

    def test_decision_table(self):
        cases = {
            (True, True): "noop",            # 幂等：什么都不做
            (True, False): "groupadd",       # 用户已在、组缺失 → 补组
            (False, True): "useradd -g",     # 组在、用户不在 → 复用组（-U 会退出 9）
            (False, False): "useradd -U",    # 全新机器 → 一次建出用户与组
        }
        for (user_exists, group_exists), want in cases.items():
            self.assertEqual(want, self._expected(user_exists, group_exists),
                             f"user={user_exists} group={group_exists}")

    def test_model_matches_shell_structure(self):
        """模型中的三条分支必须都能在 shell 实现里找到对应写法。"""
        body = _function_body(COMMON.read_text(encoding="utf-8"), "ensure_runtime_user")
        self.assertIn('if ! id "$user"', body)                                  # 用户缺失分支
        self.assertIn('if getent group "$group"', body)                         # 组存在分支
        self.assertIn('-g "$group" -d "$dir" "$user"', body)                    # → reuse
        self.assertIn('-U -d "$dir" "$user"', body)                             # → create both
        self.assertIn('groupadd', body)                                         # → backfill


if __name__ == "__main__":
    unittest.main(verbosity=2)
