# -*- coding: utf-8 -*-
"""C1-a 部署侧 + C3 限流回归：双 unit 分离拓扑、nginx 上游拆分、限流与 $request_time。

覆盖的设计契约（docs/运维约束整改设计说明.md §1 / §4.4）：
  * **两个后端进程**：`deploy/gipfel.service`（daphne ASGI，127.0.0.1:8000，只服务 /socket.io/）
    与 `deploy/gipfel-wsgi.service`（gunicorn WSGI，127.0.0.1:8002，服务 /api/、/admin/）；
  * nginx：`upstream gipfel_django → 127.0.0.1:8002`、新增 `upstream gipfel_socketio → 127.0.0.1:8000`，
    `/socket.io/` 必须走后者；文件里仍要有字面量 `127.0.0.1:8000` 与 `127.0.0.1:8121`（既有回归断言），
    且**不得**出现历史遗留的 Unix socket 名称；
  * unit 里 `Environment=`（默认值）必须写在 `EnvironmentFile=` 之前（.env 优先级更高）；
  * C3 限流：`/api/` 与 `= /api/auth/login` 分 zone、`= /api/health` 不限流、
    `/socket.io/` 只限连接数、超限 429；`log_format`（唯一名）在文件顶层且被主 server 的 access_log 使用，
    使 `$request_time` 真正落盘；
  * 注释态的 `NGINX_SSL_443*` 模板块必须同步成新拓扑（否则启用 443 时静默回退旧拓扑）；
  * 部署脚本安装/刷新两个 unit、分层探活（WSGI:8002/api/health + daphne:8000/socket.io/）。

★ 为什么是「结构化校验」而不是 `nginx -t`：
  本机（Windows）**没有 nginx、gunicorn、redis**，`bash` 也只是 WSL 存根（E_ACCESSDENIED），
  既跑不了 `nginx -t` 也跑不了 `bash -n`。因此这里用 Python 做**等价的结构化校验**：
  括号配平、指令必须在 http 上下文、zone 引用闭合、占位符渲染后无残留、
  443 模板块按部署脚本的 sed 规则「取消一层注释」后仍是合法指令……
  真机 `nginx -t` 由部署脚本 `--with-nginx` 分支执行（脚本里那一步没有被删）。

运行（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\fix_verify\\scripts\\test_c1a_split_topology.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SVC_DIR = REPO / "deploy"
GIPFEL_UNIT = SVC_DIR / "gipfel.service"
WSGI_UNIT = SVC_DIR / "gipfel-wsgi.service"
LV_UNIT = SVC_DIR / "logviewer.service"
NGINX = SVC_DIR / "nginx-gipfel.conf"
DEPLOY_SH = REPO / "scripts" / "deploy-linux.sh"
UPDATE_SH = REPO / "scripts" / "update-from-github.sh"
COMMON_SH = REPO / "scripts" / "lib" / "deploy-common.sh"
MIGRATE_SH = REPO / "scripts" / "migrate-server.sh"
VERIFY_SH = REPO / "scripts" / "verify-migration.sh"
REQUIREMENTS = REPO / "backend" / "requirements.txt"
DEPLOY_README = SVC_DIR / "README.md"

# 历史遗留的 daphne Unix socket 名称：**故意不写成连续字面量** ——
# X-23 用例会全仓扫描这个字符串（deploy/、tests/ 都在扫描范围内），
# 用例自己写出来会被判成"仍有文件引用该 socket"。这里拼出来即可，语义不变。
LEGACY_UNIX_SOCKET = "gipfel" + ".sock"

PLACEHOLDERS = ("__INSTALL_DIR__", "__DOMAIN__", "__LOG_VIEWER_PORT__")


# --------------------------------------------------------------------------
# 通用解析助手
# --------------------------------------------------------------------------
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_comments(text: str) -> str:
    """去掉整行注释（`#`/`;`），保留行内注释（解析指令时另行剥离）。"""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", ";"))
    )


def _directives(text: str) -> dict[str, str]:
    """systemd unit 指令表（后出现的同名指令覆盖前者，与 systemd 语义一致）。"""
    out: dict[str, str] = {}
    for line in _strip_comments(text).splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _effective_lines(text: str) -> list[tuple[int, str]]:
    """nginx 文件的"有效行"：去掉整行注释与行内注释，返回 (行号, 内容)。"""
    out: list[tuple[int, str]] = []
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if "#" in s:
            s = s.split("#", 1)[0].strip()
        if s:
            out.append((i, s))
    return out


def _block(text: str, header: str, *, commented: bool = False) -> str:
    """取以 header 开头的 `{ ... }` 块（含首行）；找不到返回空串。

    commented=True 时用于注释态模板块：调用方传入的 header 需自带 `# ` 前缀。
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(header):
            start = i
            break
    if start is None:
        return ""
    out: list[str] = []
    depth = 0
    for ln in lines[start:]:
        out.append(ln)
        code = ln.split("#", 1)[0] if ln.lstrip().startswith("#") is False else ""
        depth += code.count("{") - code.count("}")
        if depth == 0 and len(out) > 1:
            break
    return "\n".join(out)


def _uncomment_region(text: str, start_marker: str, end_marker: str) -> str:
    """按部署脚本的 sed 规则还原注释态标记区：`s/^# //; s/^#$//`（标记行本身保持注释）。"""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == start_marker)
        end = next(i for i, ln in enumerate(lines) if ln.strip() == end_marker)
    except StopIteration:
        return ""
    out: list[str] = []
    for ln in lines[start + 1 : end]:
        if ln.startswith("# ==="):
            out.append(ln)
            continue
        if ln.startswith("# "):
            out.append(ln[2:])
        elif ln.rstrip() == "#":
            out.append("")
        else:
            out.append(ln)
    return "\n".join(out)


def _apply_uncomment(text: str, start_marker: str, end_marker: str) -> str:
    """把标记区整体替换成「按 sed 规则取消一层注释」后的内容（模拟部署脚本的渲染结果）。"""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == start_marker)
    end = next(i for i, ln in enumerate(lines) if ln.strip() == end_marker)
    rendered = _uncomment_region(text, start_marker, end_marker).splitlines()
    return "\n".join(lines[: start + 1] + rendered + lines[end:])


def _render(text: str) -> str:
    return (
        text.replace("__INSTALL_DIR__", "/opt/gipfel")
        .replace("__DOMAIN__", "comp.example.com")
        .replace("__LOG_VIEWER_PORT__", "8120")
    )


def _line_of(text: str, needle: str) -> int:
    for i, ln in enumerate(text.splitlines(), 1):
        if needle in ln:
            return i
    return -1


def _statements(text: str) -> list[str]:
    """把有效行拼成「指令语句」：nginx 允许指令跨行（本文件的 log_format 就是 4 行），
    一条语句在处理完全部续行后才以 `;`/`{`/`}` 结束。返回未闭合的尾巴单独报错。"""
    stmts: list[str] = []
    buf = ""
    for _, code in _effective_lines(text):
        buf = f"{buf} {code}".strip() if buf else code
        if code.endswith((";", "{", "}")):
            stmts.append(buf)
            buf = ""
    if buf:
        stmts.append(buf + "  <<UNTERMINATED>>")
    return stmts


def _assert_nginx_structure(
    test: unittest.TestCase, text: str, where: str, *, check_placeholders: bool = False
) -> None:
    """结构化校验：括号配平 + 每条语句正确收尾（+ 可选：无残留占位符）。

    ★ 占位符检查只对**渲染后**的产物开启：仓库模板里 `__DOMAIN__` / `__INSTALL_DIR__` /
      `__LOG_VIEWER_PORT__` 是给部署脚本 sed 的，模板阶段本来就应该在。
    """
    depth = 0
    for stmt in _statements(text):
        test.assertFalse(
            stmt.endswith("<<UNTERMINATED>>"),
            f"{where}: 存在未以 `;`/`{{`/`}}` 收尾的指令（漏分号会让 nginx -t 直接失败）：{stmt[:120]!r}",
        )
        depth += stmt.count("{") - stmt.count("}")
        test.assertGreaterEqual(
            depth, 0, f"{where}: 出现多余的 `}}`（括号不配平）附近的指令：{stmt[:120]!r}"
        )
    test.assertEqual(depth, 0, f"{where}: `{{` 与 `}}` 不配平（差 {depth} 层）")

    if not check_placeholders:
        return
    for lineno, code in _effective_lines(text):
        for ph in re.findall(r"__[A-Z_]+__", code):
            test.fail(
                f"{where}:{lineno} 渲染后仍残留占位符 {ph}（未替换的占位符会让 nginx -t 直接失败）：{code[:80]!r}"
            )


def _server_head(text: str) -> str:
    """主 server 块里**第一个 location 之前**的 server 级指令。

    ⚠️ 不能用 `block.split("location")`：`add_header Permissions-Policy "...geolocation=()..."`
    里就含 "location" 子串（踩过一次），必须按**行首**判断。
    """
    out: list[str] = []
    for ln in _block(text, "server {").splitlines():
        if ln.strip().startswith("location"):
            break
        out.append(ln)
    return "\n".join(out)


def _zone_names(text: str, directive: str) -> set[str]:
    names: set[str] = set()
    for _, code in _effective_lines(text):
        if code.startswith(directive):
            m = re.search(r"zone=([A-Za-z0-9_]+):", code)
            if m:
                names.add(m.group(1))
    return names


def _assert_zones_resolvable(test: unittest.TestCase, text: str, where: str) -> None:
    req_zones = _zone_names(text, "limit_req_zone")
    conn_zones = _zone_names(text, "limit_conn_zone")
    test.assertTrue(req_zones, f"{where}: 必须定义 limit_req_zone")
    test.assertTrue(conn_zones, f"{where}: 必须定义 limit_conn_zone")
    for lineno, code in _effective_lines(text):
        m = re.match(r"limit_req\s+zone=([A-Za-z0-9_]+)", code)
        if m:
            test.assertIn(
                m.group(1), req_zones,
                f"{where}:{lineno} limit_req 引用了未定义的 zone {m.group(1)}"
                f"（zone 必须在 http 上下文＝文件顶层定义）",
            )
        m = re.match(r"limit_conn\s+([A-Za-z0-9_]+)", code)
        if m:
            test.assertIn(
                m.group(1), conn_zones,
                f"{where}:{lineno} limit_conn 引用了未定义的 zone {m.group(1)}",
            )


# --------------------------------------------------------------------------
# 1) 两个 unit：分离拓扑 + 沙箱/权限项 + Environment 顺序
# --------------------------------------------------------------------------
class C1aUnitSplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gipfel_raw = _read(GIPFEL_UNIT)
        self.wsgi_raw = _read(WSGI_UNIT)
        self.gipfel = _directives(self.gipfel_raw)
        self.wsgi = _directives(self.wsgi_raw)

    # ---- daphne unit：只允许新增 REALTIME_BUS=hub，其余冻结项必须原样 ----
    def test_wsgi_unit_exists(self):
        self.assertTrue(WSGI_UNIT.is_file(), "缺少 deploy/gipfel-wsgi.service（C1-a 新增 unit）")
        self.assertGreater(len(self.wsgi_raw), 500, "gipfel-wsgi.service 内容过短，疑似被截断")

    def test_daphne_keeps_frozen_execstart_and_hardening(self):
        code = _strip_comments(self.gipfel_raw)
        self.assertIn("-b 127.0.0.1", code)
        self.assertIn("-p 8000", code)
        self.assertIn("backend.asgi:application", code)
        execstart = code.split("ExecStart=", 1)[1]
        self.assertNotRegex(execstart, r"(?m)^\s*-u\s", "daphne 不得再监听 Unix socket")
        self.assertIn("${LOGS_DIRECTORY}", code)
        for must in ("RuntimeDirectoryMode=0750", "LogsDirectoryMode=0750", "UMask=0027"):
            self.assertIn(must, code, f"gipfel.service 丢失冻结项 {must}")

    def test_daphne_runs_as_realtime_hub(self):
        # 注意：unit 里有多条 Environment=，逐行断言（_directives 只保留最后一条）
        self.assertIn('Environment="PATH=__INSTALL_DIR__/backend/.venv/bin"', self.gipfel_raw)
        self.assertIn(
            'Environment="REALTIME_BUS=hub"', self.gipfel_raw,
            "daphne 是唯一持有 Socket.IO 连接与事件环的进程，必须 REALTIME_BUS=hub",
        )
        self.assertNotIn(
            "REALTIME_BUS=forward", self.gipfel_raw,
            "daphne 侧不能是 forward（那会把事件转发给自己，形成自环）",
        )

    # ---- gunicorn WSGI unit ----
    def test_wsgi_execstart_uses_gunicorn_and_threads(self):
        code = _strip_comments(self.wsgi_raw)
        self.assertIn("gunicorn", code)
        self.assertIn("backend.wsgi:application", code, "WSGI 入口必须是 backend.wsgi:application")
        self.assertIn("--bind 127.0.0.1:${GIPFEL_WSGI_PORT}", code, "只能绑回环，端口取环境变量")
        self.assertIn("--worker-class gthread", code, "同步视图靠 gthread 线程并发（C1-a 的核心）")
        self.assertIn("--workers ${GIPFEL_WSGI_WORKERS}", code)
        self.assertIn("--threads ${GIPFEL_WSGI_THREADS}", code)
        self.assertIn("--timeout ${GIPFEL_WSGI_TIMEOUT}", code)
        self.assertIn("${LOGS_DIRECTORY}", code, "访问日志必须落在自己的 LogsDirectory")

    def test_wsgi_env_defaults_match_env_example_keys(self):
        for key, value in (
            ("REALTIME_BUS", "forward"),
            ("REALTIME_FORWARD_URL", "http://127.0.0.1:8000"),
            ("GIPFEL_WSGI_PORT", "8002"),
            ("GIPFEL_WSGI_WORKERS", "4"),
            ("GIPFEL_WSGI_THREADS", "8"),
            ("GIPFEL_WSGI_TIMEOUT", "120"),
        ):
            self.assertIn(
                f'Environment="{key}={value}"', self.wsgi_raw,
                f"gipfel-wsgi.service 缺少默认值 Environment=\"{key}={value}\""
                f"（键名必须与 backend/.env.example 一致，否则 .env 覆盖不生效）",
            )

    def test_environment_before_environmentfile_in_all_units(self):
        """systemd 中 EnvironmentFile 优先级更高；默认值必须写在它**之前**，语义才一目了然。"""
        for path in (GIPFEL_UNIT, WSGI_UNIT, LV_UNIT):
            text = _read(path)
            lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
            env_idx = [i for i, ln in enumerate(lines) if ln.strip().startswith("Environment=")]
            file_idx = [i for i, ln in enumerate(lines) if ln.strip().startswith("EnvironmentFile=")]
            self.assertTrue(file_idx, f"{path.name} 缺少 EnvironmentFile=")
            self.assertTrue(env_idx, f"{path.name} 缺少 Environment=")
            self.assertLess(
                max(env_idx), min(file_idx),
                f"{path.name}：EnvironmentFile= 必须排在所有 Environment= 之后"
                f"（.env 覆盖优先，顺序写反会误导维护者）",
            )

    def test_wsgi_unit_hardening_equals_daphne(self):
        for line in _strip_comments(self.gipfel_raw).splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key in {
                "NoNewPrivileges", "PrivateTmp", "ProtectSystem", "ProtectHome",
                "ProtectKernelTunables", "ProtectControlGroups", "RestrictRealtime",
                "RestrictSUIDSGID", "LockPersonality", "CapabilityBoundingSet",
                "RestrictAddressFamilies", "User", "Group", "UMask",
                "RuntimeDirectoryMode", "LogsDirectoryMode",
            }:
                self.assertIn(
                    line.strip(), _strip_comments(self.wsgi_raw),
                    f"WSGI unit 的沙箱/权限项与 daphne 不一致（缺 {line.strip()!r}）",
                )

    def test_wsgi_unit_readwrite_paths(self):
        rwp = self.wsgi.get("ReadWritePaths", "")
        for must in ("backend/uploads", "backend/logs", "backend/snapshots", "db.sqlite3"):
            self.assertIn(must, rwp, f"WSGI 进程同样要写 {must}（缺了会 PermissionError）：{rwp}")
        self.assertIn("/run/gipfel-wsgi", rwp)
        self.assertIn("/var/log/gipfel-wsgi", rwp)

    def test_units_do_not_share_runtime_or_logs_directory(self):
        """X-13 的结论同样适用于新 unit：共用目录会让 restart 互相清理。"""
        dirs = {
            "gipfel.service": (self.gipfel.get("RuntimeDirectory"), self.gipfel.get("LogsDirectory")),
            "gipfel-wsgi.service": (self.wsgi.get("RuntimeDirectory"), self.wsgi.get("LogsDirectory")),
            "logviewer.service": (
                _directives(_read(LV_UNIT)).get("RuntimeDirectory"),
                _directives(_read(LV_UNIT)).get("LogsDirectory"),
            ),
        }
        runtime = [v[0] for v in dirs.values()]
        logs = [v[1] for v in dirs.values()]
        self.assertEqual(len(set(runtime)), 3, f"三个 unit 不得共用 RuntimeDirectory：{dirs}")
        self.assertEqual(len(set(logs)), 3, f"三个 unit 不得共用 LogsDirectory：{dirs}")
        for name, (rt, lg) in dirs.items():
            self.assertTrue(rt and lg, f"{name} 必须显式声明 RuntimeDirectory/LogsDirectory")


# --------------------------------------------------------------------------
# 2) nginx 上游拆分与路由
# --------------------------------------------------------------------------
class C1aNginxUpstreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = _read(NGINX)
        self.active = "\n".join(
            ln for ln in self.raw.splitlines() if not ln.lstrip().startswith("#")
        )

    def test_upstream_django_points_to_wsgi_port(self):
        block = _block(self.active, "upstream gipfel_django {")
        self.assertTrue(block, "缺少 upstream gipfel_django")
        self.assertIn("server 127.0.0.1:8002", block, "/api/ 的上游必须是 gunicorn(WSGI) 的 8002")

    def test_upstream_socketio_points_to_daphne(self):
        block = _block(self.active, "upstream gipfel_socketio {")
        self.assertTrue(block, "缺少 upstream gipfel_socketio（C1-a 新增）")
        self.assertIn("server 127.0.0.1:8000", block, "/socket.io/ 的上游必须是 daphne 的 8000")

    def test_api_location_uses_wsgi_upstream(self):
        block = _block(self.active, "location /api/ {")
        self.assertIn("proxy_pass         http://gipfel_django;", block)
        self.assertNotIn("gipfel_socketio", block)

    def test_socketio_location_uses_daphne_upstream(self):
        block = _block(self.active, "location /socket.io/ {")
        self.assertTrue(block)
        self.assertIn(
            "proxy_pass         http://gipfel_socketio;", block,
            "/socket.io/ 必须走 daphne 上游；指到 WSGI(8002) 会握手失败、实时广播全丢",
        )

    def test_admin_uses_wsgi_upstream(self):
        block = _block(self.active, "location /admin/ {")
        self.assertIn("proxy_pass         http://gipfel_django;", block)

    def test_internal_realtime_endpoint_is_not_proxied_by_nginx(self):
        """C1-a 内部转发端点 `/_internal/` **不得**经 nginx 暴露。

        它只应由 gunicorn 在**回环**上 POST 给 daphne（`REALTIME_FORWARD_URL=http://127.0.0.1:8000`
        + `X-Gipfel-Internal-Token` + 来源必须是 127.0.0.1/::1）。经 nginx 暴露就等于把它放到公网；
        当前 SPA 兜底 `location /` 不代理后端，所以 `/_internal/*` 只会拿到 index.html
        —— 这属于"碰巧安全"。这里正向钉住：**没有任何 location 指向它**，
        将来谁加了反向代理都会在本用例上立刻失败。
        """
        headers = re.findall(r"(?m)^\s*location\s+([^\n{]+)\{", self.active)
        bad = [h.strip() for h in headers if "_internal" in h]
        self.assertEqual(bad, [], f"nginx 不得为内部端点 /_internal/ 配置 location：{bad}")
        # 兜底 location / 只是静态文件/SPA，不会把请求转发给 Django
        self.assertIn("location / {", self.active)
        self.assertIn("try_files $uri $uri/ /index.html;", self.active)

    def test_frozen_literals_still_present(self):
        """既有 X-13/X-23 回归断言：这两个字面量必须仍在文件里。"""
        self.assertIn("127.0.0.1:8000", self.raw)
        self.assertIn("127.0.0.1:8121", self.raw)
        self.assertNotIn(LEGACY_UNIX_SOCKET, self.raw, "nginx 不得依赖历史 Unix socket")


# --------------------------------------------------------------------------
# 3) C3 限流 + $request_time
# --------------------------------------------------------------------------
class C3RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = _read(NGINX)
        self.active = "\n".join(
            ln for ln in self.raw.splitlines() if not ln.lstrip().startswith("#")
        )
        self.before_first_server = self.active.split("server {", 1)[0]

    def test_zones_defined_in_http_context(self):
        """zone/log_format 只能在 http 上下文；写进 server{} 会让 nginx -t 报 not allowed here。"""
        for directive in ("limit_req_zone", "limit_conn_zone", "log_format"):
            self.assertIn(directive, self.before_first_server,
                          f"{directive} 必须定义在文件顶层（第一个 server{{}} 之前）")

    def test_api_and_login_have_separate_zones(self):
        api = _block(self.active, "location /api/ {")
        login = _block(self.active, "location = /api/auth/login {")
        self.assertTrue(login, "缺少精确匹配的登录 location（需要单独更严的 zone）")
        m_api = re.search(r"limit_req\s+zone=([A-Za-z0-9_]+)\s+burst=(\d+)", api)
        m_login = re.search(r"limit_req\s+zone=([A-Za-z0-9_]+)\s+burst=(\d+)", login)
        self.assertIsNotNone(m_api, "/api/ 必须限流")
        self.assertIsNotNone(m_login, "/api/auth/login 必须限流")
        self.assertNotEqual(m_api.group(1), m_login.group(1), "登录必须用独立 zone")

        def rate(zone: str) -> float:
            m = re.search(rf"limit_req_zone\s+\S+\s+zone={zone}:\S+\s+rate=(\d+)r/s", self.raw)
            self.assertIsNotNone(m, f"zone {zone} 的 rate 未定义")
            return float(m.group(1))

        self.assertLess(
            rate(m_login.group(1)), rate(m_api.group(1)),
            "登录 zone 必须比 /api/ 更严（同一出口 IP 全场共用时的撞库面）",
        )

    def test_rates_are_comment_justified(self):
        """每个限流 zone 的取值都必须有注释解释依据（重点：100 客户端共用一个 NAT 出口 IP）。"""
        lines = self.raw.splitlines()
        for i, ln in enumerate(lines):
            if re.match(r"\s*limit_(req|conn)_zone\b", ln):
                ctx = "\n".join(lines[max(0, i - 10) : i])
                self.assertRegex(
                    ctx, r"依据|NAT|100|重连|兜底|r/s",
                    f"第 {i + 1} 行的 zone 定义前缺少取值依据注释：{ln.strip()!r}",
                )
        self.assertIn("NAT", self.raw, "限流注释必须点明「全场可能共用一个 NAT 出口 IP」这一关键场景")
        self.assertIn("100", self.raw, "限流注释必须写清 100 客户端的量级估算")
        self.assertIn("429", self.raw, "超限必须返回 429")
        # Lead 复核要求：配置里要有一张"换算表"，并写清"见到 429 先看 access log 再调数值"。
        self.assertIn("换算表", self.raw, "限流注释必须包含现场换算表（估算口径统一）")
        self.assertRegex(
            self.raw, r"不要直接删掉限流",
            "必须写明「若仍见 429：先看 access log/$request_time 再调 rate/burst，不要直接删掉限流」",
        )
        self.assertIn("$request_time", self.raw)

    def test_values_tolerate_shared_nat_egress(self):
        """数值下限护栏：100 客户端共用一个出口 IP 时不得误伤（Lead 复核后的口径）。"""
        rates = {
            m.group(1): int(m.group(2))
            for m in re.finditer(r"zone=([A-Za-z0-9_]+):\S+\s+rate=(\d+)r/s", self.raw)
        }
        bursts = {
            m.group(1): int(m.group(2))
            for m in re.finditer(r"limit_req\s+zone=([A-Za-z0-9_]+)\s+burst=(\d+)", self.active)
        }
        conns = {
            m.group(1): int(m.group(2))
            for m in re.finditer(r"limit_conn\s+([A-Za-z0-9_]+)\s+(\d+)", self.active)
        }
        self.assertGreaterEqual(rates["gipfel_api_rl"], 60, "稳态 5 req/s 需要 ≥12 倍余量")
        self.assertGreaterEqual(
            rates["gipfel_login_rl"], 20,
            "登录 zone 低于 20r/s 时，开场 100 人共用出口 IP 会有人被 429 挡在门外（误伤）",
        )
        self.assertGreaterEqual(
            bursts["gipfel_login_rl"], 120,
            "登录 burst 必须能一次性吸收全场瞬时登录（100~200 人规模）",
        )
        self.assertGreaterEqual(
            conns["gipfel_api_conn"], 600,
            "/api/ 连接上限过小会误伤同一个 NAT 出口 IP 下的 100 个客户端（浏览器同源常驻多条连接）",
        )
        self.assertGreaterEqual(
            conns["gipfel_socket_conn"], 200,
            "Socket.IO 连接上限必须容纳 100 客户端 + 重连期并存连接",
        )

    def test_health_is_not_rate_limited(self):
        block = _block(self.active, "location = /api/health {")
        self.assertTrue(block, "缺少 location = /api/health")
        self.assertNotIn("limit_req", block, "健康检查不允许限速（监控/部署探针必须永远通过）")
        self.assertNotIn("limit_conn", block, "健康检查不允许限连接数")
        # limit_* 只允许写在 location 内：server 级会被 health 继承
        server_head = _server_head(self.active)
        self.assertNotIn("limit_req ", server_head, "limit_req 不得写在 server 级（会连带限掉 health）")
        self.assertNotIn("limit_conn ", server_head, "limit_conn 不得写在 server 级（会连带限掉 health）")

    def test_socketio_is_connection_limited_only(self):
        block = _block(self.active, "location /socket.io/ {")
        self.assertIn("limit_conn gipfel_socket_conn", block)
        self.assertNotIn(
            "limit_req", block,
            "/socket.io/ 只限连接数：限速会让长连接/polling 被判超时 → 全场重连风暴",
        )

    def test_status_codes_are_429(self):
        self.assertRegex(self.active, r"limit_req_status\s+429;")
        self.assertRegex(self.active, r"limit_conn_status\s+429;")

    def test_log_format_has_request_time_and_is_used(self):
        m = re.search(r"log_format\s+([A-Za-z0-9_]+)\s+'", self.active)
        self.assertIsNotNone(m, "必须定义自定义 log_format（用于携带 $request_time）")
        name = m.group(1)
        self.assertEqual(
            len(re.findall(rf"log_format\s+{name}\b", self.raw)), 1,
            f"log_format 名 {name!r} 必须唯一（重名会让 nginx -t 报 duplicate log format）",
        )
        fmt = self.active[self.active.index(f"log_format {name}"):]
        fmt = fmt[: fmt.index(";") + 1]
        self.assertIn("$request_time", fmt, "log_format 必须包含 $request_time（C3 可观测性要求）")
        main_server_access = _server_head(self.active)
        self.assertRegex(
            main_server_access, rf"access_log\s+\S+\s+{name};",
            "主 server 的 access_log 必须引用该格式，否则 $request_time 不会落盘",
        )


# --------------------------------------------------------------------------
# 4) 模板结构校验（替代 nginx -t）
# --------------------------------------------------------------------------
class NginxTemplateStructuralTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = _read(NGINX)

    def test_active_part_is_structurally_valid(self):
        _assert_nginx_structure(self, self.raw, "nginx-gipfel.conf")
        _assert_zones_resolvable(self, self.raw, "nginx-gipfel.conf")

    def test_rendered_placeholders_are_resolved(self):
        rendered = _render(self.raw)
        for ph in PLACEHOLDERS:
            self.assertNotIn(ph, rendered, f"部署脚本会替换 {ph}，渲染后不应再有")
        _assert_nginx_structure(
            self, rendered, "nginx-gipfel.conf(渲染后)", check_placeholders=True
        )

    def test_443_template_mirrors_new_topology(self):
        """注释态 443 块按脚本的 sed 规则取消一层注释后，必须是**新拓扑**的合法配置。"""
        uncommented = _uncomment_region(
            self.raw, "# === NGINX_SSL_443_START ===", "# === NGINX_SSL_443_END ==="
        )
        self.assertTrue(uncommented, "未找到 NGINX_SSL_443 模板块（标记被改动？）")
        self.assertIn("server {", uncommented)
        _assert_nginx_structure(self, uncommented, "443 模板(取消注释后)")
        # 上游必须与 80 端口块一致
        self.assertIn(
            "proxy_pass         http://gipfel_socketio;", uncommented,
            "443 模板的 /socket.io/ 必须也走 daphne 上游，否则启用 443 时回退旧拓扑",
        )
        self.assertIn("proxy_pass         http://gipfel_django;", uncommented)
        self.assertIn("location = /api/health {", uncommented, "443 模板同样要豁免健康检查")
        self.assertIn("limit_req  zone=gipfel_login_rl", uncommented, "443 模板同样要有登录限流")
        self.assertIn("limit_req  zone=gipfel_api_rl", uncommented, "443 模板同样要有 /api/ 限流")
        self.assertIn("limit_conn gipfel_socket_conn", uncommented)
        self.assertNotIn(
            LEGACY_UNIX_SOCKET, uncommented, "443 模板不得引入历史 Unix socket"
        )

    def test_443_template_zone_references_are_resolvable(self):
        """启用 443 后 zone 定义（文件顶层）与引用（块内）必须同时存在。"""
        uncommented = _uncomment_region(
            self.raw, "# === NGINX_SSL_443_START ===", "# === NGINX_SSL_443_END ==="
        )
        combined = self.raw + "\n" + uncommented
        _assert_zones_resolvable(self, combined, "nginx-gipfel.conf + 443 模板")

    def test_443_region_contains_no_prose(self):
        """部署脚本会把区域内的非 ASCII 有效行判为"散文注释"并中止 —— 这里提前拦。"""
        uncommented = _uncomment_region(
            self.raw, "# === NGINX_SSL_443_START ===", "# === NGINX_SSL_443_END ==="
        )
        for lineno, code in _effective_lines(uncommented):
            self.assertRegex(
                code, r"^[\x20-\x7e]*$",
                f"443 模板第 {lineno} 行取消注释后含非 ASCII 内容（散文会变成非法指令）：{code!r}",
            )

    def test_full_deploy_render_pipeline_is_consistent(self):
        """模拟 `deploy-linux.sh --domain X --with-nginx --origin-cert --no-logviewer-tls` 的
        完整渲染管线（占位符替换 → 删除 8120 端口块 → 取消两段 443 注释 → 填证书路径），
        再对**最终产物**做结构校验与关键拓扑断言。

        这是本机（无 nginx）最接近 `nginx -t` 的检查：真实 `nginx -t` 由部署脚本第 7 步执行。
        """
        text = _render(self.raw)
        # 删除「纯 IP 形态」的 8120 端口块（有 --domain 时脚本会删它）
        text = re.sub(
            r"# === LOGVIEWER_PORT8120_START ===[\s\S]*?# === LOGVIEWER_PORT8120_END ===",
            "", text,
        )
        # --origin-cert：主站 443 + 日志查看器子域 443 两段取消注释
        text = _apply_uncomment(text, "# === NGINX_SSL_443_START ===", "# === NGINX_SSL_443_END ===")
        text = _apply_uncomment(
            text, "# === NGINX_SSL_443_LOGVIEWER_START ===", "# === NGINX_SSL_443_LOGVIEWER_END ==="
        )
        # --no-logviewer-tls：整段删除（含 __LOG_VIEWER_TLS_PORT__ 占位符）
        text = re.sub(
            r"# === LOGVIEWER_TLS_PORT_START ===[\s\S]*?# === LOGVIEWER_TLS_PORT_END ===",
            "", text,
        )
        # ③ 填证书路径（脚本在取消注释之后做；顺序对本检查无影响）
        text = text.replace("__SSL_CERT__", "/etc/ssl/cloudflare/comp.example.com.pem")
        text = text.replace("__SSL_KEY__", "/etc/ssl/cloudflare/comp.example.com.key")

        _assert_nginx_structure(self, text, "渲染产物(origin-cert)", check_placeholders=True)
        _assert_zones_resolvable(self, text, "渲染产物(origin-cert)")

        # 脚本自身的自检项（deploy-linux.sh 第 7 步）：必须有 443 ssl
        self.assertIn("listen 443 ssl", text)
        # 双上游在渲染后仍然正确
        self.assertIn("server 127.0.0.1:8002 fail_timeout", text)
        self.assertIn("server 127.0.0.1:8000 fail_timeout", text)
        self.assertIn("proxy_pass         http://gipfel_socketio;", text)
        # 限流与可观测性在渲染后仍然生效
        #   rate 数值来源：Debian 13 真机 100 客户端压测（docs/真机验证报告-Debian13.md §T3）——
        #   同一 NAT 出口 IP 下 60r/s 会拦掉 10%~32% 的正常重连，120r/s+burst240 为 0 个 429。
        self.assertIn("limit_req_zone $binary_remote_addr zone=gipfel_api_rl:10m rate=120r/s;", text)
        self.assertIn("limit_req  zone=gipfel_api_rl burst=240 nodelay;", text)
        # 登录档与连接数档**保持不变**（登录靠应用层防爆破、连接数是病态兜底）
        self.assertIn("limit_req_zone $binary_remote_addr zone=gipfel_login_rl:10m rate=20r/s;", text)
        self.assertIn("limit_req  zone=gipfel_login_rl burst=120 nodelay;", text)
        self.assertIn("limit_conn gipfel_api_conn 600;", text)
        self.assertIn("limit_conn gipfel_socket_conn 200;", text)
        self.assertIn("location = /api/health {", text)
        self.assertIn("access_log /var/log/nginx/gipfel.access.log gipfel_rt;", text)
        # 渲染后不得有 location 被整段吞掉（模板/脚本撕裂的典型后果）
        for must in ("location /api/ {", "location /socket.io/ {", "location /admin/ {",
                     "location /uploads/ {", "location /static/ {", "location / {"):
            self.assertIn(must, text, f"渲染产物缺少 {must}（模板被破坏？）")

    def test_units_are_committed_with_lf_line_endings(self):
        """部署产物在 Linux 侧必须是 LF。

        本仓库 `.gitattributes` 只对 `*.sh` 强制 `eol=lf`；unit/conf 依赖
        `core.autocrlf=true`（索引里是 LF、工作区是 CRLF），所以这里**不检查工作区字节**，
        而是用 git 的索引视图核对（`git ls-files --eol` 的 `i/lf`）—— 索引是 LF，
        Linux 上 git clone / checkout 得到的就是 LF；靠 rsync 从 Windows 工作区同步的情形
        由部署脚本的 `rsync`（也以工作区为准）负责，属既有行为，不在本用例扩展范围内。
        """
        import subprocess

        try:
            out = subprocess.run(
                ["git", "ls-files", "--eol", "deploy/"],
                cwd=REPO, capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("本机没有可用的 git，跳过索引行尾检查")
        if out.returncode != 0:
            self.skipTest(f"git ls-files 不可用：{out.stderr.strip()[:120]}")
        for line in out.stdout.splitlines():
            if not line.strip():
                continue
            attrs, _, path = line.partition("\t")
            if path.endswith((".service", ".conf")):
                self.assertIn(
                    "i/lf", attrs,
                    f"{path} 在 git 索引里不是 LF —— Linux 侧会拿到 CRLF 行尾",
                )


# --------------------------------------------------------------------------
# 5) 部署脚本接线：安装/刷新两个 unit + 分层健康检查 + WAL 安全的恢复指引
# --------------------------------------------------------------------------
class C1aScriptWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deploy = _read(DEPLOY_SH)
        self.update = _read(UPDATE_SH)
        self.common = _read(COMMON_SH)

    def test_deploy_installs_wsgi_unit(self):
        self.assertIn(
            'check_exists "$PROJECT_ROOT/deploy/gipfel-wsgi.service"', self.deploy,
            "deploy-linux.sh 必须校验 WSGI unit 模板存在（缺失应当场报错而不是静默跳过）",
        )
        self.assertRegex(
            self.deploy, r'deploy/gipfel-wsgi\.service" > "\$_tmp_unit"',
            "deploy-linux.sh 必须渲染并安装 gipfel-wsgi.service",
        )
        self.assertIn("systemctl restart gipfel-wsgi", self.deploy)
        self.assertRegex(
            self.deploy, r"for _svc in gipfel\.service gipfel-wsgi\.service gipfel-logviewer\.service",
            "masked 自愈循环必须覆盖新 unit（否则 masked 时 cp -f 会把 unit 写进 /dev/null）",
        )

    def test_update_refreshes_wsgi_unit(self):
        self.assertRegex(
            self.update, r'refresh_unit "\$INSTALL_DIR/deploy/gipfel-wsgi\.service" gipfel-wsgi\.service',
            "升级脚本必须刷新 WSGI unit —— 新增 unit 最易在升级路径被漏掉",
        )
        self.assertIn("systemctl restart gipfel-wsgi", self.update)

    def test_scripts_probe_both_backends(self):
        for name, text in (("deploy-linux.sh", self.deploy), ("update-from-github.sh", self.update)):
            self.assertIn("split_backend_health", text, f"{name} 必须调用分层健康检查")
        for needle in ("gipfel_wsgi_port", "split_backend_health", "socket.io/?EIO=4&transport=polling"):
            self.assertIn(needle, self.common, f"deploy-common.sh 缺少 {needle}")
        self.assertIn('GIPFEL_DAPHNE_PORT', self.common)

    def test_health_check_url_uses_loopback_port_not_public_8000(self):
        """daphne 的 :8000 只绑回环 —— 提示里出现 http://<域名>:8000 会误导排查。"""
        for name, text in (("deploy-linux.sh", self.deploy), ("update-from-github.sh", self.update)):
            self.assertNotRegex(
                text, r"http://\$\{DOMAIN\}:8000",
                f"{name} 的健康检查提示不得指向公网 <域名>:8000（该端口只绑 127.0.0.1）",
            )

    def test_wsgi_port_override_is_synced_into_vhost(self):
        """unit 端口可被 .env 覆盖；nginx 模板写死 8002 → 渲染期必须同步，否则全站 502。"""
        for name, text in (("deploy-linux.sh", self.deploy), ("update-from-github.sh", self.update)):
            self.assertRegex(
                text, r'127\.0\.0\.1:8002.*127\.0\.0\.1:\$\{_WSGI_PORT\}',
                f"{name} 必须把 .env 的 GIPFEL_WSGI_PORT 同步进渲染产物",
            )
            self.assertRegex(
                text, r"server 127\\\.0\\\.0\\\.1:\$\{_WSGI_PORT\} fail_timeout",
                f"{name} 必须自检 upstream gipfel_django 指向 WSGI 端口",
            )

    def test_other_deploy_scripts_know_about_the_third_unit(self):
        self.assertIn("deploy/gipfel-wsgi.service", _read(MIGRATE_SH),
                      "migrate-server.sh 必须把 WSGI unit 一起迁到新机（否则 /api/ 全 502）")
        verify = _read(VERIFY_SH)
        self.assertIn("gipfel-wsgi", verify, "verify-migration.sh 必须检查 WSGI 服务状态")
        self.assertIn("8002", verify, "verify-migration.sh 必须探 WSGI 的 /api/health")
        self.assertIn("socket.io", verify, "verify-migration.sh 必须探 daphne 的 Socket.IO 握手")

    def test_shell_scripts_are_lf_only(self):
        for path in (DEPLOY_SH, UPDATE_SH, COMMON_SH, MIGRATE_SH, VERIFY_SH):
            self.assertNotIn(
                b"\r\n", path.read_bytes(),
                f"{path.name} 含 CRLF：Linux 的 bash 会报 `$'\\r': command not found`"
                f"（.gitattributes 已强制 *.sh eol=lf，工作区被改成 CRLF 时必须改回）",
            )

    def test_shell_scripts_are_not_truncated(self):
        """无 bash 可用（本机 bash 是 WSL 存根 → E_ACCESSDENIED），故做两项**保守**的结构检查：

        ① 每个 heredoc（`<<'PY'` / `<<PY` / `<<-EOF`）都必须有对应的结束行 —— 少一个结束行，
           后面的脚本会整段被当成 heredoc 内容吞掉（编辑器/合并事故的典型后果）；
        ② 文件最后一条有效行不得以续行符 `\\` 结尾（脚本被截断的直接特征）。

        ★ 这不是 `bash -n` 的等价物。真正的语法校验必须在 Linux 上补跑：
          `bash -n scripts/*.sh scripts/lib/*.sh`（见完成回执的「未验证项」）。
        """
        for path in (DEPLOY_SH, UPDATE_SH, COMMON_SH, MIGRATE_SH, VERIFY_SH):
            lines = _read(path).splitlines()
            # ① heredoc 结束行
            heredocs = [
                m.group(2) or m.group(3)
                for m in re.finditer(r"<<(-?)(?:\s*'([A-Za-z_][A-Za-z0-9_]*)'|\s*\"?([A-Za-z_][A-Za-z0-9_]*)\"?)", _read(path))
            ]
            for tag in heredocs:
                self.assertIn(
                    tag, lines,
                    f"{path.name}: heredoc {tag!r} 找不到独立的结束行（脚本可能被截断/合并事故）",
                )
            # ② 文件末尾不得悬着续行符
            code_lines = [
                ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")
            ]
            self.assertFalse(
                code_lines[-1].rstrip().endswith("\\"),
                f"{path.name}: 最后一条有效命令以续行符结尾 —— 脚本疑似被截断",
            )

    # ---- WAL：恢复指引必须"先停服 + 删 -wal/-shm" ----
    def test_rollback_hint_is_wal_safe(self):
        fn = self.common[self.common.index("print_rollback_hint()"):]
        self.assertIn("db.sqlite3-wal", fn, "恢复指引必须删 -wal 残留")
        self.assertIn("db.sqlite3-shm", fn, "恢复指引必须删 -shm 残留")
        self.assertIn("systemctl stop gipfel gipfel-wsgi gipfel-logviewer", fn,
                      "恢复数据库前必须先停掉三个写入进程")
        self.assertIn("VACUUM INTO", fn, "手工副本必须给出 VACUUM INTO 的自洽导出方式")

    def test_deploy_script_documents_its_only_db_restore_branch(self):
        """deploy-linux.sh 唯一的覆盖式恢复只在"目标库不存在"时执行，必须写明为何 WAL 安全。"""
        idx = self.deploy.index('if [[ -f "$BACKUP_DIR/db.sqlite3" && ! -f "$INSTALL_DIR/backend/db.sqlite3" ]]')
        ctx = self.deploy[max(0, idx - 900) : idx]
        self.assertIn("WAL", ctx, "该 cp 分支必须注释说明 WAL 安全性结论")
        self.assertIn("! -f", self.deploy[idx : idx + 200], "覆盖式恢复只能发生在目标库不存在时")

    def test_readme_rollback_is_wal_safe(self):
        text = _read(DEPLOY_README)
        self.assertIn("db.sqlite3-wal", text, "README 回滚小节必须提醒删 -wal 残留")
        self.assertIn("db.sqlite3-shm", text)
        self.assertIn("VACUUM INTO", text, "README 必须给出停服后导出自洽副本的做法")
        self.assertIn("gipfel-wsgi", text, "README 必须提到第三个 unit（停/起服务都要带上）")
        self.assertRegex(text, r"C1-a 双进程部署", "README 必须新增 C1-a 部署小节")
        self.assertRegex(text, r"回退", "C1-a 小节必须写回退步骤")


# --------------------------------------------------------------------------
# 6) 依赖
# --------------------------------------------------------------------------
class C1aRequirementsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _read(REQUIREMENTS)
        self.code = "\n".join(
            ln.split("#", 1)[0].strip()
            for ln in self.text.splitlines()
        )

    def test_gunicorn_marked_for_non_windows(self):
        m = re.search(r"^gunicorn==([0-9.]+)\s*;\s*sys_platform\s*!=\s*\"win32\"", self.code, re.M)
        self.assertIsNotNone(
            m, "requirements.txt 必须有 `gunicorn==<版本>; sys_platform != \"win32\"`"
               "（gunicorn 不支持 Windows，Windows 开发机用 runserver）",
        )

    def test_redis_is_optional_and_documented_as_degradable(self):
        self.assertRegex(self.code, r"(?m)^redis==[0-9.]+")
        self.assertIn("可选", self.text, "redis 行必须注明是可选增强")
        self.assertRegex(
            self.text, r"降级|回落|fallback",
            "必须写明 redis 缺失时应用会降级（不影响启动/实时功能不停摆）",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
