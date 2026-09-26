# -*- coding: utf-8 -*-
"""C2 阶段 1 探针：SQLite PRAGMA 联动、busy_timeout 真实性、并发写、审计降噪、审计归档。

全部在**副本**上做（本探针还会再复制一份 `_artifacts/db_e.sqlite3` 供破坏性用例使用），
绝不触碰 `backend/db.sqlite3`。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\probe_sqlite.py
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

REPO = pc.REPO
BACKEND = pc.BACKEND
ART = pc.ARTIFACTS
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
DB_E = ART / "db_e.sqlite3"
DB_SCRATCH = ART / "db_scratch.sqlite3"

# 本探针独占一份副本，破坏性用例（归档删除）不影响其它探针
if pc.DB_COPY.exists():
    _src = sqlite3.connect(f"file:{pc.DB_COPY.as_posix()}?mode=ro", uri=True)
    if DB_E.exists():
        DB_E.unlink()
    _src.execute("VACUUM INTO ?", (str(DB_E),))
    _src.close()
os.environ["OPS_CHECK_DB"] = str(DB_E)

pc.bootstrap()

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import override_settings  # noqa: E402

from apps.common import audit  # noqa: E402


# --------------------------------------------------------------------------
def _fresh_copy(name: str) -> Path:
    """从共享副本再 VACUUM INTO 一份**全新**库（journal_mode 回到 SQLite 默认 delete）。"""
    dst = ART / name
    if dst.exists():
        dst.unlink()
    src = sqlite3.connect(f"file:{pc.DB_COPY.as_posix()}?mode=ro", uri=True)
    src.execute("VACUUM INTO ?", (str(dst),))
    src.close()
    return dst


def run_pragma_report(extra_env: dict[str, str], db: Path | None = None) -> dict:
    env = dict(os.environ)
    env.update(extra_env)
    env["PYTHONIOENCODING"] = "utf-8"
    env["OPS_CHECK_DB"] = str(db or DB_E)
    env["DJANGO_SETTINGS_MODULE"] = "tests.ops_check.probe_settings"
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), str(BACKEND), env.get("PYTHONPATH", "")])
    proc = subprocess.run([str(PYTHON), str(HERE / "_pragma_report.py")], cwd=str(BACKEND),
                          env=env, capture_output=True, text=True, encoding="utf-8")
    line = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
    if proc.returncode != 0 or not line:
        pc.fail("E-report", f"子进程失败 rc={proc.returncode} err={(proc.stderr or '')[-400:]}")
        return {}
    return json.loads(line[-1])


def check_pragma_default() -> dict:
    """E1：默认（零配置）新连接 = wal / 20000 / 1；OPTIONS.timeout 联动为 20s。

    用**全新副本**（journal_mode 尚未被改成 WAL）测，避免「库文件已是 WAL」这一持久属性
    污染结论（见 check_pragma_disabled 的说明）。
    """
    db = _fresh_copy("db_e1_fresh.sqlite3")
    before = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    initial = before.execute("PRAGMA journal_mode").fetchone()[0]
    before.close()
    rep = run_pragma_report({}, db=db)
    pc.info(f"全新副本初始 journal_mode={initial}；默认 PRAGMA 报告：{json.dumps(rep, ensure_ascii=False)}")
    pc.check("E1-journal-wal", str(rep.get("journal_mode", "")).lower() == "wal",
             f"journal_mode={rep.get('journal_mode')}（期望 wal）")
    pc.check("E1-busy-timeout-20000", rep.get("busy_timeout") == 20000,
             f"busy_timeout={rep.get('busy_timeout')}（期望 20000）")
    pc.check("E1-synchronous-normal", rep.get("synchronous") == 1,
             f"synchronous={rep.get('synchronous')}（期望 1=NORMAL）")
    pc.check("E1-options-timeout-linked", rep.get("driver_timeout") == 20.0,
             f"DATABASES.OPTIONS.timeout={rep.get('driver_timeout')}（期望 20.0，与开关联动）")
    pc.check("E1-wal-autocheckpoint", rep.get("wal_autocheckpoint") == 1000,
             f"wal_autocheckpoint={rep.get('wal_autocheckpoint')}（期望 1000）")
    return rep


def check_pragma_disabled() -> dict:
    """E2：SQLITE_TUNING_ENABLED=false → busy_timeout/synchronous/OPTIONS 回到改造前。

    注意（**重要发现**）：`journal_mode` 是 **SQLite 库文件的持久属性**，一旦被改成 WAL，
    后续任何连接（包括完全不发 PRAGMA 的连接）看到的都还是 WAL。因此
    「关掉开关」只能恢复 busy_timeout/synchronous/OPTIONS，**不能**把已经 WAL 化的
    库文件变回 delete；要把 journal_mode 也变回去，必须显式执行
    `PRAGMA journal_mode=DELETE`（或换回改造前的库文件）。本探针分两个副本分别证明这两点。
    """
    # ① 全新库 + 开关关闭 → 三者都回到改造前
    fresh = _fresh_copy("db_e2_fresh.sqlite3")
    con = sqlite3.connect(f"file:{fresh.as_posix()}?mode=ro", uri=True)
    initial = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()
    rep = run_pragma_report({"SQLITE_TUNING_ENABLED": "false"}, db=fresh)
    pc.info(f"全新副本初始 journal_mode={initial}；关闭开关后：{json.dumps(rep, ensure_ascii=False)}")
    pc.check("E2-journal-delete-on-fresh-db", str(rep.get("journal_mode", "")).lower() == "delete",
             f"全新库 + 开关关闭：journal_mode={rep.get('journal_mode')}（期望 delete）")
    pc.check("E2-busy-timeout-5000", rep.get("busy_timeout") == 5000,
             f"busy_timeout={rep.get('busy_timeout')}（期望 5000 = sqlite3 默认）")
    pc.check("E2-synchronous-full", rep.get("synchronous") == 2,
             f"synchronous={rep.get('synchronous')}（期望 2=FULL）")
    pc.check("E2-options-removed", rep.get("options") in ({}, None),
             f"DATABASES.OPTIONS={rep.get('options')}（期望 {{}}：连 OPTIONS 一起撤掉）")
    pc.check("E2-driver-timeout-default", rep.get("driver_timeout") in (None, 5.0),
             f"连接 timeout={rep.get('driver_timeout')}（期望 5.0 = sqlite3 默认）")

    # ② 已被 WAL 化的库 + 开关关闭 → journal_mode 仍是 wal（持久属性，无法靠开关回退）
    already = _fresh_copy("db_e2_already_wal.sqlite3")
    w = sqlite3.connect(str(already))
    w.execute("PRAGMA journal_mode=WAL")
    w.close()
    rep2 = run_pragma_report({"SQLITE_TUNING_ENABLED": "false"}, db=already)
    pc.info(f"已 WAL 化的库 + 开关关闭：journal_mode={rep2.get('journal_mode')}，"
            f"busy_timeout={rep2.get('busy_timeout')}")
    pc.check("E2-wal-persists-in-file", str(rep2.get("journal_mode", "")).lower() == "wal",
             f"已 WAL 化的库在开关关闭后仍是 {rep2.get('journal_mode')}"
             "（journal_mode 是库文件持久属性 → 单靠开关**不能**回退 WAL）")
    pc.check("E2-timeouts-still-revert", rep2.get("busy_timeout") == 5000
             and rep2.get("synchronous") == 2 and rep2.get("options") in ({}, None),
             f"但 busy_timeout/synchronous/OPTIONS 仍回退到改造前："
             f"{rep2.get('busy_timeout')}/{rep2.get('synchronous')}/{rep2.get('options')}")
    return rep2


def _scratch_db(pragmas: tuple[str, ...]) -> sqlite3.Connection:
    if DB_SCRATCH.exists():
        DB_SCRATCH.unlink()
    con = sqlite3.connect(str(DB_SCRATCH), timeout=30, isolation_level=None)
    for stmt in pragmas:
        con.execute(stmt)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    con.close()
    return sqlite3.connect(str(DB_SCRATCH), timeout=30, isolation_level=None)


def check_concurrent_writes() -> None:
    """E3：20 个并发写者（每个 10 次提交）在 WAL + busy_timeout=20000 下不得出现 locked。"""
    pragmas = tuple(settings.SQLITE_PRAGMA_STATEMENTS)
    pc.info(f"使用 settings.SQLITE_PRAGMA_STATEMENTS={pragmas}")
    _scratch_db(pragmas)  # 建库

    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def writer(tag: int) -> None:
        con = sqlite3.connect(str(DB_SCRATCH), timeout=30, isolation_level=None)
        for stmt in pragmas:
            con.execute(stmt)
        try:
            barrier.wait()
            for i in range(10):
                try:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("INSERT INTO t (v) VALUES (?)", (f"{tag}-{i}",))
                    con.execute("COMMIT")
                except Exception as exc:  # noqa: BLE001
                    try:
                        con.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001
                        pass
                    with lock:
                        errors.append(f"{type(exc).__name__}: {exc}")
                    return
        finally:
            con.close()

    t0 = time.perf_counter()
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0
    con = sqlite3.connect(str(DB_SCRATCH))
    rows = con.execute("SELECT count(*) FROM t").fetchone()[0]
    con.close()
    locked = [e for e in errors if "locked" in e.lower()]
    pc.check("E3-no-database-locked", not locked,
             f"20 并发写 × 10 次 = {rows}/200 行成功，locked 错误={len(locked)}；其它错误={errors[:3]}")
    pc.check("E3-all-rows-written", rows == 200, f"落库行数={rows}（期望 200）")
    pc.info(f"20 并发写总耗时 {elapsed:.2f}s")


def check_busy_timeout_is_real() -> None:
    """E3b：证明 busy_timeout=20000 真实生效。

    线程 A 用 `BEGIN IMMEDIATE` 持有写锁 6 秒（> 旧默认 5000ms，< 新值 20000ms）：
    - busy_timeout=20000 的连接必须**等锁成功后写入**（耗时 ≈ 6s）；
    - busy_timeout=5000 的连接必须**抛 database is locked**（在 5s 处放弃）。
    这同时证明「5000 → 20000 不是纸面改动」。
    """
    pragmas = tuple(settings.SQLITE_PRAGMA_STATEMENTS)
    hold_seconds = 6.0
    results: dict[str, object] = {}

    def hold_lock() -> None:
        con = sqlite3.connect(str(DB_SCRATCH), timeout=30, isolation_level=None)
        for stmt in pragmas:
            con.execute(stmt)
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO t (v) VALUES ('holder')")
        time.sleep(hold_seconds)
        con.execute("COMMIT")
        con.close()

    def waiter(label: str, pragma_timeout: int) -> None:
        con = sqlite3.connect(str(DB_SCRATCH), timeout=30, isolation_level=None)
        con.execute(f"PRAGMA busy_timeout={pragma_timeout}")
        con.execute("PRAGMA journal_mode=WAL")
        t0 = time.perf_counter()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO t (v) VALUES (?)", (label,))
            con.execute("COMMIT")
            results[label] = ("ok", round(time.perf_counter() - t0, 2))
        except Exception as exc:  # noqa: BLE001
            results[label] = (f"{type(exc).__name__}: {exc}", round(time.perf_counter() - t0, 2))
        finally:
            con.close()

    h = threading.Thread(target=hold_lock)
    h.start()
    time.sleep(0.5)
    w20 = threading.Thread(target=waiter, args=("t20000", 20000))
    w5 = threading.Thread(target=waiter, args=("t5000", 5000))
    w20.start()
    time.sleep(0.2)
    w5.start()
    h.join()
    w20.join()
    w5.join()

    r20 = results.get("t20000")
    r5 = results.get("t5000")
    pc.check("E3b-20000-waits-and-succeeds",
             isinstance(r20, tuple) and r20[0] == "ok" and r20[1] >= 4.0,
             f"busy_timeout=20000 等锁后成功：{r20}（应 ≈{hold_seconds - 0.7:.1f}s，说明真的在等）")
    pc.check("E3b-5000-fails-locked",
             isinstance(r5, tuple) and "locked" in str(r5[0]).lower(),
             f"busy_timeout=5000 在锁释放前放弃：{r5}（应报 database is locked）")


def _mk_request(path: str = "/api/x", method: str = "get"):
    from django.test import RequestFactory

    rf = RequestFactory()
    return getattr(rf, method)(path)


def _capture_logs():
    import logging

    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):  # noqa: D102
            records.append(record.getMessage())

    handler = _H()
    lg = logging.getLogger("gipfel")
    lg.addHandler(handler)
    old = lg.level
    lg.setLevel(logging.DEBUG)
    return records, handler, lg, old


def check_audit_noise() -> None:
    """E4：审计降噪不误伤（log_write 恒落库 / 5xx 恒落库 / 4xx 可采样可配 / 始终写日志）。"""
    from apps.audit.models import AuditLog

    def count(**filters) -> int:
        return AuditLog.objects.filter(**filters).count()

    req = _mk_request()
    exc = RuntimeError("probe-boom")

    class _Resp:
        def __init__(self, code: int):
            self.status_code = code

    # --- log_write 不受任何开关影响 ---
    for mode in ("all", "sampled", "off"):
        before = count(kind="write")
        with override_settings(AUDIT_HTTP_ERROR_MODE=mode, AUDIT_HTTP_ERROR_SAMPLE_RATE=0.0):
            audit.log_write(model="ProbeModel", action="Probe:create", record_id="1",
                            changes={"a": 1, "password": "secret"}, competition_id=None)
        after = count(kind="write")
        pc.check(f"E4-log-write-always-{mode}", after == before + 1,
                 f"AUDIT_HTTP_ERROR_MODE={mode} 时 log_write 仍落库（{before}→{after}）")
    sanitized = AuditLog.objects.filter(kind="write", model="ProbeModel").order_by("-id").first()
    pc.check("E4-log-write-sanitized", sanitized is not None and "***REDACTED***" in (sanitized.changes or ""),
             f"changes 脱敏：{(sanitized.changes if sanitized else None)}")

    # --- 5xx 在 sampled 下恒落库（采样率 0 也必须落） ---
    for rate in (0.0, 0.05, 1.0):
        before = count(kind="error", status_code=500)
        with override_settings(AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=rate):
            audit.log_exception(req, exc, _Resp(500))
        after = count(kind="error", status_code=500)
        pc.check(f"E4-5xx-always-rate{rate}", after == before + 1,
                 f"sampled + 采样率 {rate}：500 仍落库（{before}→{after}）")

    # --- 4xx 采样可配 ---
    before_all = count(kind="error", status_code=401)
    with override_settings(AUDIT_HTTP_ERROR_MODE="all"):
        audit.log_exception(req, exc, _Resp(401))
    after_all = count(kind="error", status_code=401)
    pc.check("E4-4xx-mode-all-persists", after_all == before_all + 1, f"mode=all：401 落库（{before_all}→{after_all}）")

    before_r0 = count(kind="error", status_code=403)
    with override_settings(AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=0.0):
        for _ in range(5):
            audit.log_exception(req, exc, _Resp(403))
    after_r0 = count(kind="error", status_code=403)
    pc.check("E4-4xx-rate0-never", after_r0 == before_r0,
             f"sampled + 采样率 0：5 次 403 一条都不落库（{before_r0}→{after_r0}）")

    before_r1 = count(kind="error", status_code=409)
    with override_settings(AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=1.0):
        for _ in range(5):
            audit.log_exception(req, exc, _Resp(409))
    after_r1 = count(kind="error", status_code=409)
    pc.check("E4-4xx-rate1-always", after_r1 == before_r1 + 5,
             f"sampled + 采样率 1.0：5 次 409 全落库（{before_r1}→{after_r1}）")

    before_off = count(kind="error", status_code=404)
    with override_settings(AUDIT_HTTP_ERROR_MODE="off"):
        for _ in range(5):
            audit.log_exception(req, exc, _Resp(404))
    after_off = count(kind="error", status_code=404)
    pc.check("E4-mode-off-no-persist", after_off == before_off,
             f"mode=off：5 次 404 不落库（{before_off}→{after_off}）")

    # --- 三种模式下都必须写 logger（含 method/path/status/operator） ---
    for mode in ("all", "sampled", "off"):
        records, handler, lg, old = _capture_logs()
        try:
            with override_settings(AUDIT_HTTP_ERROR_MODE=mode, AUDIT_HTTP_ERROR_SAMPLE_RATE=0.0):
                audit.log_exception(req, exc, _Resp(404))
        finally:
            lg.removeHandler(handler)
            lg.setLevel(old)
        hit = [m for m in records if "HTTP 异常审计" in m and "/api/x" in m and "404" in m]
        pc.check(f"E4-logger-always-{mode}", bool(hit),
                 f"mode={mode} 仍写日志：{hit[:1]}")

    # --- 端到端：登录失败（401）在 mode=all 下落库、在 sampled+rate0 下不落库 ---
    client = Client()
    hosts = override_settings(ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost"])
    payload = json.dumps({"username": "ops_check_no_such_user", "password": "wrong"})
    with hosts:
        before = count(kind="error", status_code=401)
        with override_settings(AUDIT_HTTP_ERROR_MODE="all"):
            r1 = client.post("/api/auth/login", data=payload, content_type="application/json")
        mid = count(kind="error", status_code=401)
        with override_settings(AUDIT_HTTP_ERROR_MODE="sampled", AUDIT_HTTP_ERROR_SAMPLE_RATE=0.0):
            r2 = client.post("/api/auth/login", data=payload, content_type="application/json")
        after = count(kind="error", status_code=401)
    pc.check("E4-login-fail-401-status", r1.status_code == 401 and r2.status_code == 401,
             f"登录失败状态码={r1.status_code}/{r2.status_code}")
    pc.check("E4-login-fail-audited-in-all", mid == before + 1,
             f"mode=all：登录失败落库（{before}→{mid}）")
    pc.check("E4-login-fail-sampled-out", after == mid,
             f"sampled + 采样率 0：登录失败不落库（{mid}→{after}）——降噪的**有意**取舍"
             "（赛场排查仍可用日志 + AUDIT_HTTP_ERROR_SAMPLE_RATE=1 恢复全量）")


def _run_archive(args: list[str], db: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({
        "DJANGO_SETTINGS_MODULE": "tests.ops_check.probe_settings",
        "OPS_CHECK_DB": str(db),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join([str(REPO), str(BACKEND), env.get("PYTHONPATH", "")]),
    })
    return subprocess.run([str(PYTHON), "manage.py", "audit_archive", *args],
                          cwd=str(BACKEND), env=env, capture_output=True, text=True, encoding="utf-8")


def _audit_count(db: Path) -> int:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    finally:
        con.close()


def check_audit_archive() -> None:
    """E5：audit_archive —— dry-run 不删、导出失败不删、正常路径先导出后删除。"""
    db_arch = ART / "db_archive.sqlite3"
    if db_arch.exists():
        db_arch.unlink()
    con = sqlite3.connect(f"file:{DB_E.as_posix()}?mode=ro", uri=True)
    con.execute("VACUUM INTO ?", (str(db_arch),))
    # 保证有「超期」行：把最早 5 行的 created_at 改成 30 天前
    con.close()
    w = sqlite3.connect(str(db_arch))
    w.execute("UPDATE audit_log SET created_at = datetime('now', '-30 days') "
              "WHERE id IN (SELECT id FROM audit_log ORDER BY id LIMIT 5)")
    w.commit()
    w.close()

    before = _audit_count(db_arch)
    dry_dir = ART / "audit_archive_dryrun"
    if dry_dir.exists():
        for f in dry_dir.glob("*"):
            f.unlink()
    dry = _run_archive(["--dry-run", "--days", "7", "--output-dir", str(dry_dir)], db_arch)
    after_dry = _audit_count(db_arch)
    m = re.search(r"超期\s+(\d+)\s+行", dry.stdout or "")
    expired = int(m.group(1)) if m else None
    pc.check("E5-dry-run-exit0", dry.returncode == 0, f"--dry-run 退出码={dry.returncode}")
    pc.check("E5-dry-run-no-delete", after_dry == before,
             f"--dry-run 前后行数 {before}→{after_dry}（必须不变）")
    pc.check("E5-dry-run-no-file", not list(dry_dir.glob("*.gz")),
             f"--dry-run 不得产出归档文件：{list(dry_dir.glob('*'))}")
    pc.check("E5-dry-run-plans-count", expired is not None and expired > 0,
             f"命令自报超期行数={expired}（解析自：{(dry.stdout or '').strip().splitlines()[:1]}）")

    # 导出目录不可用（父路径是一个文件）→ 必须报错且不删
    blocker = ART / "_blocker_file"
    blocker.write_text("not a dir", encoding="utf-8")
    before_fail = _audit_count(db_arch)
    bad = _run_archive(["--days", "7", "--output-dir", str(blocker / "sub")], db_arch)
    after_fail = _audit_count(db_arch)
    pc.check("E5-export-failure-nonzero", bad.returncode != 0,
             f"导出失败退出码={bad.returncode}（期望非 0）")
    pc.check("E5-export-failure-no-delete", after_fail == before_fail,
             f"导出失败前后行数 {before_fail}→{after_fail}（必须不变）")
    pc.check("E5-export-failure-message", "未删除" in (bad.stdout or "") + (bad.stderr or ""),
             f"错误提示片段：{((bad.stdout or '') + (bad.stderr or '')).strip().splitlines()[-1:]}")

    # --days 0 必须拒绝
    zero = _run_archive(["--days", "0"], db_arch)
    pc.check("E5-days-zero-rejected", zero.returncode != 0 and _audit_count(db_arch) == before_fail,
             f"--days 0 退出码={zero.returncode}，行数不变={_audit_count(db_arch) == before_fail}")

    # 正常路径：导出 + 删除
    out_dir = ART / "audit_archive_probe"
    before_ok = _audit_count(db_arch)
    good = _run_archive(["--days", "7", "--output-dir", str(out_dir), "--batch-size", "2"], db_arch)
    after_ok = _audit_count(db_arch)
    gz_files = sorted(out_dir.glob("audit-*.jsonl.gz"))
    lines = 0
    if gz_files:
        with gzip.open(gz_files[-1], "rt", encoding="utf-8") as fh:
            lines = sum(1 for _ in fh)
    pc.check("E5-normal-exit0", good.returncode == 0, f"正常归档退出码={good.returncode}")
    pc.check("E5-normal-exported", bool(gz_files), f"导出文件={[f.name for f in gz_files]}")
    pc.check("E5-normal-deleted", expired is not None and after_ok == before_ok - expired,
             f"删除行数={before_ok - after_ok}（期望 {expired} = 命令自报的超期行数）")
    pc.check("E5-normal-export-lines", expired is not None and lines == expired,
             f"归档文件行数={lines}（期望 {expired}：先导出 N 行再删 N 行，逐行一致）")
    pc.info(f"正常归档输出：{(good.stdout or '').strip().splitlines()[-1:]}")


def main() -> int:
    pc.info(f"DB_E={os.environ.get('OPS_CHECK_DB')}")
    check_pragma_default()
    check_pragma_disabled()
    check_concurrent_writes()
    check_busy_timeout_is_real()
    check_audit_noise()
    check_audit_archive()
    return pc.finish()


if __name__ == "__main__":
    raise SystemExit(main())
