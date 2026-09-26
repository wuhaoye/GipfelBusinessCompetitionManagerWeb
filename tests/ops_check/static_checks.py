# -*- coding: utf-8 -*-
"""B/H/I/J 组：测试未削弱、部署配置静态核查、红线、零配置回退。

- B：`git diff` 里被改的既有用例是否放宽/删除断言；x23 把 docs 排除出「socket 消费方扫描」
     之后，守卫用例是否真的锁死排除集（并用**人为放宽**反证守卫有效）；排除 docs 后
     全仓重扫一遍，人工分类命中文件。
- H：nginx 配置的**结构性**核查（本机没有 nginx，无法 `nginx -t`，见 FINDING）；
     两个 systemd unit 的关键项。
- I：简报 §C1.6/§C2.4「不该做」红线。
- J：`.env` 不带任何新键时的逐项行为差；三个开关是否足以回到改造前。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\static_checks.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

REPO, BACKEND, ART = pc.REPO, pc.BACKEND, pc.ARTIFACTS
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
NGINX = REPO / "deploy" / "nginx-gipfel.conf"
UNIT_DAPHNE = REPO / "deploy" / "gipfel.service"
UNIT_WSGI = REPO / "deploy" / "gipfel-wsgi.service"

#: 审计 X-23 断言「仓库里没有别的东西引用这个已被移除的 Unix socket 名」，
#: 扫描范围包含 tests/。因此**本探针自己也不能把这个字面量写进任何文件**
#: （否则会制造假阳性）。这里拼接构造，并在落盘前统一脱敏。
SOCKET_NAME = "gipfel" + ".sock"


def _sanitize(text: str) -> str:
    return text.replace(SOCKET_NAME, "<SOCKET-NAME-REDACTED>")


# ==========================================================================
# B：测试是否被削弱
# ==========================================================================
def check_tests_not_weakened() -> None:
    diff_stat = subprocess.run(["git", "diff", "--stat"], cwd=str(REPO),
                               capture_output=True, text=True, encoding="utf-8").stdout
    (ART / "B_git_diff_stat.txt").write_text(_sanitize(diff_stat), encoding="utf-8")
    diff_tests = subprocess.run(["git", "diff", "--", "tests/fix_verify/scripts/"],
                                cwd=str(REPO), capture_output=True, text=True,
                                encoding="utf-8").stdout
    (ART / "B_git_diff_tests.txt").write_text(_sanitize(diff_tests), encoding="utf-8")

    changed = [ln.split("|")[0].strip() for ln in diff_stat.splitlines()
               if "|" in ln and ln.split("|")[0].strip().endswith(".py")]
    pc.info(f"本次 git diff 中 .py 文件：{changed}")

    # 逐个被改的既有用例文件：断言条数不得减少
    for rel in ("tests/fix_verify/scripts/test_x10_rollback_path.py",
                "tests/fix_verify/scripts/test_x13_x14_units_diag.py",
                "tests/fix_verify/scripts/test_x23_daphne_socket.py",
                "tests/fix_verify/scripts/test_x26_readme_rollback.py"):
        old = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=str(REPO),
                             capture_output=True, text=True, encoding="utf-8").stdout
        new = (REPO / rel).read_text(encoding="utf-8")
        n_old = len(re.findall(r"self\.assert|assert ", old))
        n_new = len(re.findall(r"self\.assert|assert ", new))
        pc.check(f"B-assert-count-not-reduced:{Path(rel).name}", n_new >= n_old,
                 f"{Path(rel).name}：断言数 {n_old} → {n_new}")

    # 被删掉的断言行逐条列出（供报告人工复核）
    removed = [ln[1:].strip() for ln in diff_tests.splitlines()
               if ln.startswith("-") and not ln.startswith("---") and ("assert" in ln)]
    (ART / "B_removed_assertions.txt").write_text(_sanitize("\n".join(removed)), encoding="utf-8")
    pc.info(f"被删除的断言行 {len(removed)} 条，已存 B_removed_assertions.txt")
    # 其中「字面量被删」的必须由更强断言替换：逐一验证替代物确实存在
    new_x10 = (REPO / "tests/fix_verify/scripts/test_x10_rollback_path.py").read_text(encoding="utf-8")
    new_x26 = (REPO / "tests/fix_verify/scripts/test_x26_readme_rollback.py").read_text(encoding="utf-8")
    pc.check("B-x10-three-writer-stop", "gipfel-wsgi" in new_x10 and "stop_lines" in new_x10,
             "x10：把「停服字面量」换成「同一条命令必须覆盖 gipfel/gipfel-wsgi/gipfel-logviewer」")
    pc.check("B-x10-wal-shm-guard", "db.sqlite3-wal" in new_x10 and "db.sqlite3-shm" in new_x10,
             "x10：新增「恢复前必须删 -wal/-shm」断言")
    pc.check("B-x26-vacuum-into", "VACUUM INTO" in new_x26 and "before-rollback" in new_x26,
             "x26：把 `cp -a 活库` 换成「VACUUM INTO 自洽副本 + before-rollback 命名」")


def check_x23_docs_exclusion() -> None:
    """x23 把 docs 排除出消费方扫描：排除集是否被锁死？全仓重扫有无遗漏？"""
    spec = importlib.util.spec_from_file_location(
        "x23_mod", REPO / "tests" / "fix_verify" / "scripts" / "test_x23_daphne_socket.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # ① 守卫用例本身必须通过
    case = mod.X23NoConsumerTests("test_consumers_are_really_scanned")
    try:
        case.test_consumers_are_really_scanned()
        pc.ok("B-x23-guard-passes", "排除集守卫用例通过（SCAN_DIRS 含 5 个消费方目录、排除集恰为 {docs}）")
    except AssertionError as exc:
        pc.fail("B-x23-guard-passes", f"守卫用例失败：{exc}")

    # ② 人为把排除范围写大 → 守卫必须失败（反证它不是空断言）
    original = mod._NON_CONSUMER_DIRS
    try:
        mod._NON_CONSUMER_DIRS = {"docs", "backend"}
        widened_failed = False
        try:
            mod.X23NoConsumerTests("test_consumers_are_really_scanned").test_consumers_are_really_scanned()
        except AssertionError:
            widened_failed = True
        pc.check("B-x23-guard-catches-widening", widened_failed,
                 "把排除集人为改成 {docs, backend} 后守卫用例**失败** → 守卫确实锁死了排除范围")
    finally:
        mod._NON_CONSUMER_DIRS = original

    # ③ 原扫描断言本身仍通过
    try:
        mod.X23NoConsumerTests("test_no_other_reference").test_no_other_reference()
        pc.ok("B-x23-scan-passes-with-docs-excluded", "排除 docs 后仓库内无消费方引用该 Unix socket 名")
    except AssertionError as exc:
        pc.fail("B-x23-scan-passes-with-docs-excluded", f"扫描失败：{exc}")

    # ④ 独立重扫：把 docs 重新纳入，看看多出哪些命中，人工分类
    hits: list[str] = []
    for d in mod.X23NoConsumerTests.SCAN_DIRS:
        root = REPO / d
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() in mod.X23NoConsumerTests.SKIP_SUFFIX:
                continue
            if p.name in mod._SELF or p == mod.GIPFEL_UNIT:
                continue
            if mod._SKIP_PARTS & set(p.parts):
                continue
            try:
                if SOCKET_NAME in p.read_text(encoding="utf-8", errors="replace"):
                    hits.append(str(p.relative_to(REPO)))
            except OSError:
                continue
    non_docs = [h for h in hits if not h.replace("\\", "/").startswith("docs/")]
    pc.check("B-x23-extra-hits-are-docs-only", not non_docs,
             f"把 docs 重新纳入扫描后新增命中={hits}；其中非 docs 的={non_docs}")
    pc.info(f"含该 socket 名的文件（含 docs）={hits}")
    pc.finding(
        "F4-x23-scan-scope-is-6-topdirs",
        "x23 的消费方扫描范围是固定的 6 个顶层目录（scripts/tests/backend/frontend/deploy/docs），"
        "**不含** `code_audit/`、根目录 `*.md`（它们确实含该 socket 名字样，属审计/文档产物，非消费方）。"
        "排除 docs 后真正的消费方（脚本/配置/代码）仍然全量扫描，故该排除不构成放水。",
    )


# ==========================================================================
# H：nginx / systemd 静态结构
# ==========================================================================
def _strip_nginx_comments(text: str) -> str:
    out = []
    for ln in text.splitlines():
        idx = ln.find("#")
        out.append(ln[:idx] if idx >= 0 else ln)
    return "\n".join(out)


def _parse_nginx(text: str):
    """极简 nginx 结构解析：返回 (深度→行列表, 括号是否平衡, 顶层指令集合)。"""
    depth = 0
    by_depth: dict[int, list[str]] = {}
    balanced = True
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        opens = line.count("{")
        closes = line.count("}")
        if closes > opens and depth - (closes - opens) < 0:
            balanced = False
        by_depth.setdefault(depth, []).append(line)
        depth += opens - closes
        if depth < 0:
            balanced = False
            depth = 0
    if depth != 0:
        balanced = False
    return by_depth, balanced


def check_nginx_structure() -> None:
    raw = NGINX.read_text(encoding="utf-8")
    code = _strip_nginx_comments(raw)
    by_depth, balanced = _parse_nginx(code)
    pc.check("H1-braces-balanced", balanced, "花括号平衡（结构性替代 nginx -t 的第一层）")

    top = by_depth.get(0, [])
    bad_ctx = [ln for ln in top if ln.startswith(("limit_req ", "limit_conn "))]
    pc.check("H2-zone-directives-at-http-level",
             not bad_ctx and any("limit_req_zone" in ln for ln in top)
             and any("limit_conn_zone" in ln for ln in top)
             and any(ln.startswith("log_format") for ln in top),
             "limit_req_zone / limit_conn_zone / log_format 都在 http 上下文（文件顶层），"
             f"且 server 级没有 limit_req/limit_conn（server 级会连 health 一起限掉）：{bad_ctx}")
    pc.check("H2-log-format-unique", code.count("log_format gipfel_rt") == 1,
             "`log_format gipfel_rt` 只定义一次（重名会让 nginx -t 报 duplicate log format）")

    upstreams = [ln for ln in top if ln.startswith("upstream ")]
    pc.check("H3-two-upstreams", len(upstreams) == 2, f"顶层 upstream={upstreams}")
    m_dj = re.search(r"upstream gipfel_django \{([\s\S]*?)\}", code)
    m_sio = re.search(r"upstream gipfel_socketio \{([\s\S]*?)\}", code)
    pc.check("H3-upstream-django-8002",
             bool(m_dj) and "server 127.0.0.1:8002" in m_dj.group(1),
             f"gipfel_django → {m_dj.group(1).strip().splitlines()[0] if m_dj else '缺失'}")
    pc.check("H3-upstream-socketio-8000",
             bool(m_sio) and "server 127.0.0.1:8000" in m_sio.group(1),
             f"gipfel_socketio → {m_sio.group(1).strip().splitlines()[0] if m_sio else '缺失'}")

    # 主 server（监听 80 的那个）
    main = re.search(r"server \{\s*\n\s*listen 80;[\s\S]*?\n\}", code)
    ms = main.group(0) if main else ""
    pc.check("H4-main-server-access-log-uses-format",
             bool(re.search(r"access_log\s+\S+\s+gipfel_rt;", ms)),
             "主 server 的 access_log 引用 gipfel_rt（否则 $request_time 不落盘）")
    pc.check("H5-health-location-unlimited",
             bool(re.search(r"location = /api/health \{[\s\S]*?\}", ms))
             and "limit_" not in re.search(r"location = /api/health \{[\s\S]*?\}", ms).group(0),
             "`location = /api/health` 内**没有**任何 limit_req/limit_conn")
    login = re.search(r"location = /api/auth/login \{[\s\S]*?\}", ms)
    api = re.search(r"location /api/ \{[\s\S]*?\}", ms)
    sio = re.search(r"location /socket\.io/ \{[\s\S]*?\}", ms)
    pc.check("H6-login-zone",
             bool(login) and "zone=gipfel_login_rl" in login.group(0) and "burst=120" in login.group(0)
             and "nodelay" in login.group(0),
             "= /api/auth/login 用独立更严 zone（gipfel_login_rl, burst=120 nodelay）")
    pc.check("H7-api-zone",
             bool(api) and "zone=gipfel_api_rl" in api.group(0) and "burst=120" in api.group(0)
             and "limit_conn gipfel_api_conn 600" in api.group(0),
             "/api/ 用 gipfel_api_rl(burst=120) + limit_conn 600")
    pc.check("H8-socketio-conn-only",
             bool(sio) and "limit_conn gipfel_socket_conn 200" in sio.group(0)
             and "limit_req" not in sio.group(0),
             "/socket.io/ 只限连接数（200），不限速（限速会把长连接心跳打成断线重连）")
    pc.check("H9-429-status", "limit_req_status 429;" in code and "limit_conn_status 429;" in code,
             "超限统一返回 429（不是 503）")
    pc.check("H10-literals",
             "127.0.0.1:8000" in code and "127.0.0.1:8121" in code and SOCKET_NAME not in code,
             "字面量 127.0.0.1:8000 / 127.0.0.1:8121 都在，且不含历史 Unix socket 名")
    pc.check("H12-no-internal-proxy", "/_internal" not in code,
             "nginx 不代理 /_internal/（内部转发端点仅回环可达，公网不可达）")

    # 443 注释模板：取消一层注释后必须与 80 块同步
    m = re.search(r"# === NGINX_SSL_443_START ===([\s\S]*?)# === NGINX_SSL_443_END ===", raw)
    block = m.group(1) if m else ""
    uncommented = "\n".join(re.sub(r"^\s*#\s?", "", ln) for ln in block.splitlines())
    pc.check("H11-443-template-synced",
             "http://gipfel_django" in uncommented and "http://gipfel_socketio" in uncommented
             and "limit_req  zone=gipfel_api_rl" in uncommented
             and "limit_conn gipfel_socket_conn 200" in uncommented
             and "access_log /var/log/nginx/gipfel.access.log gipfel_rt;" in uncommented,
             "443 注释模板取消一层注释后：/api/→gipfel_django、/socket.io/→gipfel_socketio、"
             "限流与 access_log 格式都与 80 块一致")
    health443 = re.search(r"location = /api/health \{[\s\S]*?\}", uncommented)
    pc.check("H11-443-health-unlimited",
             bool(health443) and "limit_" not in health443.group(0),
             "443 模板里的 = /api/health 同样不限流")

    pc.finding(
        "F5-nginx-t-not-run",
        "本机是 Windows，没有 nginx 可执行文件，`nginx -t` **未运行**："
        "上面的括号平衡/上下文/指令位置检查只是结构性替代，"
        "真正的语法与 include 上下文需在 Linux 目标机上跑 `nginx -t` 确认（需真机验证）。",
    )


def _unit_directives(path: Path) -> dict[str, str]:
    """解析 unit 指令；**支持续行**（systemd 用行尾 `\\` 续行，ExecStart 是多行的）。"""
    out: dict[str, str] = {}
    pending = ""
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not pending and (not s or s.startswith(("#", ";")) or s.startswith("[")):
            continue
        continued = s.endswith("\\")
        if continued:
            s = s[:-1].strip()
        pending = f"{pending} {s}".strip() if pending else s
        if continued:
            continue
        if "=" in pending:
            k, v = pending.split("=", 1)
            out[k.strip()] = re.sub(r"\s+", " ", v.strip())
        pending = ""
    return out


def check_units() -> None:
    d = _unit_directives(UNIT_DAPHNE)
    w = _unit_directives(UNIT_WSGI)
    exec_d = d.get("ExecStart", "")
    exec_w = w.get("ExecStart", "")
    pc.check("H13-daphne-execstart",
             "-b 127.0.0.1" in exec_d and "-p 8000" in exec_d
             and "backend.asgi:application" in exec_d and "-u " not in exec_d,
             "gipfel.service：daphne 仍绑 127.0.0.1:8000 / backend.asgi:application，无 -u unix socket")
    pc.check("H13-wsgi-execstart",
             "gunicorn" in exec_w and "--worker-class gthread" in exec_w and "--threads" in exec_w
             and "backend.wsgi:application" in exec_w and "--bind 127.0.0.1:" in exec_w,
             "gipfel-wsgi.service：gunicorn gthread + 多线程 + backend.wsgi:application + 仅回环")
    for name, u in (("daphne", d), ("wsgi", w)):
        env = u.get("Environment", "")
        ef = u.get("EnvironmentFile", "")
        text = (UNIT_DAPHNE if name == "daphne" else UNIT_WSGI).read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        i_env = [i for i, ln in enumerate(lines) if ln.strip().startswith("Environment=")]
        i_file = [i for i, ln in enumerate(lines) if ln.strip().startswith("EnvironmentFile=")]
        pc.check(f"H13-{name}-env-order", bool(i_env) and bool(i_file) and max(i_env) < min(i_file),
                 f"{name}：Environment=（默认值）在 EnvironmentFile=（.env）之前")
        pc.check(f"H13-{name}-sandbox",
                 u.get("RuntimeDirectoryMode") == "0750" and u.get("LogsDirectoryMode") == "0750"
                 and u.get("UMask") == "0027" and u.get("NoNewPrivileges") == "true"
                 and "AF_INET" in u.get("RestrictAddressFamilies", "")
                 and "AF_INET6" in u.get("RestrictAddressFamilies", ""),
                 f"{name}：0750/UMask=0027/NoNewPrivileges/RestrictAddressFamilies(AF_INET6) 齐全")
        rwp = u.get("ReadWritePaths", "")
        pc.check(f"H13-{name}-writepaths",
                 all(x in rwp for x in ("backend/uploads", "backend/logs", "backend/snapshots",
                                        "db.sqlite3")),
                 f"{name}：ReadWritePaths 含 uploads/logs/snapshots/db.sqlite3 → {rwp}")
    pc.check("H13-dirs-not-shared",
             d.get("RuntimeDirectory") != w.get("RuntimeDirectory")
             and d.get("LogsDirectory") != w.get("LogsDirectory"),
             f"两个 unit 的运行/日志目录不共用（{d.get('RuntimeDirectory')} vs {w.get('RuntimeDirectory')}）")


# ==========================================================================
# I：红线
# ==========================================================================
def check_redlines() -> None:
    units = list((REPO / "deploy").glob("*.service"))
    app_units = [u.name for u in units
                 if "backend.asgi:application" in _unit_directives(u).get("ExecStart", "")]
    pc.check("I1-no-multi-daphne", app_units == ["gipfel.service"],
             f"只有 gipfel.service 起本应用的 daphne（{app_units}）；"
             "logviewer.service 是另一个站点（:8121），不算抢端口")
    d = _unit_directives(UNIT_DAPHNE)
    pc.check("I1-daphne-no-workers", not re.search(r"\s-w\s|--workers", d.get("ExecStart", "")),
             "daphne 未加 -w/--workers")

    py_src = "".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (BACKEND / "apps").rglob("*.py")
        if "__pycache__" not in p.parts
    )
    pc.check("I2-no-async-to-sync-wrapper", "async_to_sync" not in py_src,
             "backend/apps/** 没有给同步视图加 async_to_sync 包装")
    pc.check("I3-no-thread-sensitive-false", "thread_sensitive=False" not in py_src,
             "backend/apps/** 没有 thread_sensitive=False 混用")
    bad_vacuum = [p.relative_to(REPO) for p in list((BACKEND / "apps").rglob("*.py"))
                  if "__pycache__" not in p.parts
                  and re.search(r"VACUUM\s*(;|\"|'|\s*$)", p.read_text(encoding="utf-8", errors="replace"),
                                re.IGNORECASE | re.MULTILINE)]
    pc.check("I4-no-live-vacuum", not bad_vacuum,
             f"backend/apps/** 没有对活库跑裸 VACUUM（只允许 VACUUM INTO）：{bad_vacuum}")

    # 旧端点未被删除（用 Django 解析器实证）
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.ops_check.probe_settings")
    os.environ.setdefault("OPS_CHECK_DB", str(pc.DB_COPY))
    for p in (str(REPO), str(BACKEND)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import django

    django.setup()
    from django.urls import resolve

    legacy = ["/api/company-fields/1", "/api/company-fields/1/2"]
    resolved = []
    for url in legacy:
        try:
            m = resolve(url)
            resolved.append(f"{url} → {m.view_name}")
        except Exception as exc:  # noqa: BLE001
            resolved.append(f"{url} → 解析失败 {exc}")
    pc.check("I5-legacy-routes-alive", all("解析失败" not in r for r in resolved),
             f"既有旧端点仍可解析：{resolved}")
    from django.urls import reverse

    pc.check("I5-batch-route-added",
             reverse("company_fields:company-fields-batch") == "/api/company-fields",
             f"新增批量端点：{reverse('company_fields:company-fields-batch')}")

    # 没有多开 daphne 抢端口：gipfel.service 与 wsgi unit 端口不同
    wsgi_text = UNIT_WSGI.read_text(encoding="utf-8")
    wsgi_unit = _unit_directives(UNIT_WSGI)
    pc.check("I1-ports-distinct",
             "-p 8000" in d.get("ExecStart", "")
             and 'Environment="GIPFEL_WSGI_PORT=8002"' in wsgi_text
             and "--bind 127.0.0.1:${GIPFEL_WSGI_PORT}" in wsgi_unit.get("ExecStart", ""),
             "daphne=8000、gunicorn=8002（ExecStart 用 ${GIPFEL_WSGI_PORT}，默认值 8002，端口不冲突）")


# ==========================================================================
# J：零配置回退
# ==========================================================================
def _report(extra_env: dict[str, str]) -> dict:
    env = dict(os.environ)
    env.update(extra_env)
    env["PYTHONIOENCODING"] = "utf-8"
    env["DJANGO_SETTINGS_MODULE"] = "tests.ops_check.probe_settings"
    env["OPS_CHECK_DB"] = str(ART / "db_j.sqlite3")
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), str(BACKEND), env.get("PYTHONPATH", "")])
    proc = subprocess.run([str(PYTHON), str(HERE / "_pragma_report.py")], cwd=str(BACKEND),
                          env=env, capture_output=True, text=True, encoding="utf-8")
    line = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
    return json.loads(line[-1]) if line else {}


def check_zero_config() -> None:
    import sqlite3

    if not (ART / "db_j.sqlite3").exists():
        src = sqlite3.connect(f"file:{pc.DB_COPY.as_posix()}?mode=ro", uri=True)
        src.execute("VACUUM INTO ?", (str(ART / "db_j.sqlite3"),))
        src.close()
    rep = _report({})
    pc.info(f"零配置生效值：{json.dumps(rep, ensure_ascii=False)}")
    pc.check("J1-zero-config-defaults",
             rep.get("sqlite_tuning_enabled") is True
             and rep.get("audit_http_error_mode") == "sampled"
             and rep.get("realtime_bus") == "auto"
             and rep.get("auth_me_conditional") is True,
             "零配置（.env 不带任何新键）时的默认：SQLITE_TUNING_ENABLED=true、"
             "AUDIT_HTTP_ERROR_MODE=sampled、REALTIME_BUS=auto、AUTH_ME_CONDITIONAL_ENABLED=true")
    pc.finding(
        "F6-zero-config-not-identical-to-before",
        "设计说明 §0 说「零配置即等价于改造前」，实测只对 C1-a/C3-新增端点成立："
        "C2 的 WAL/busy_timeout=20000/synchronous=NORMAL 与审计 4xx 采样（5%）**默认就生效**，"
        "属于有意变更；/auth/me 也多出 ETag 响应头。逐项差异见报告 §J 表。",
    )
    # 三个开关是否足以回到改造前？AUTH_ME_CONDITIONAL_ENABLED 未列入 → /auth/me 仍带 ETag
    import django  # noqa: F401  （bootstrap 已在 check_redlines 完成）
    os.environ["OPS_CHECK_DB"] = str(ART / "db_j.sqlite3")
    from django.test import Client
    from django.test.utils import override_settings

    from apps.auth.authentication import create_jwt
    from apps.users.models import User

    u = User.objects.filter(pk=2).first() or User.objects.first()
    client = Client()
    with override_settings(ALLOWED_HOSTS=["testserver"], SQLITE_TUNING_ENABLED=False,
                           AUDIT_HTTP_ERROR_MODE="all", REALTIME_BUS="local"):
        resp = client.get("/api/auth/me", HTTP_AUTHORIZATION=f"Bearer {create_jwt(u)}")
    pc.check("J2-three-switches-still-emit-etag", bool(resp.headers.get("ETag")),
             f"只设 SQLITE_TUNING_ENABLED=false + AUDIT_HTTP_ERROR_MODE=all + REALTIME_BUS=local 时，"
             f"/auth/me 仍返回 ETag={resp.headers.get('ETag')!r} → 这三个开关**不足以**做到逐字节回退，"
             "需要第 4 个开关 AUTH_ME_CONDITIONAL_ENABLED=false（见报告 §J）")
    with override_settings(ALLOWED_HOSTS=["testserver"], AUTH_ME_CONDITIONAL_ENABLED=False):
        resp2 = client.get("/api/auth/me", HTTP_AUTHORIZATION=f"Bearer {create_jwt(u)}")
    pc.check("J2-fourth-switch-restores", not resp2.headers.get("ETag"),
             "加上 AUTH_ME_CONDITIONAL_ENABLED=false 后不再返回 ETag（完整回退口径）")


def main() -> int:
    pc.bootstrap(need_django=False)
    check_tests_not_weakened()
    check_x23_docs_exclusion()
    check_nginx_structure()
    check_units()
    check_redlines()
    check_zero_config()
    return pc.finish()


if __name__ == "__main__":
    raise SystemExit(main())
