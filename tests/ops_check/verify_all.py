# -*- coding: utf-8 -*-
"""一键验收：跑完全部可本机执行的检查，打印 PASS/FAIL 汇总表，退出码反映是否有 FAIL。

用法（仓库根目录或任意位置，脚本自己按路径定位仓库）：
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\verify_all.py            # 全量（含 584 项全量测试）
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\verify_all.py --fast     # 跳过最慢的全量测试
    backend\\.venv\\Scripts\\python.exe tests\\ops_check\\verify_all.py --keep-db  # 保留临时数据库副本
    .\\tests\\ops_check\\run_all.ps1                                              # 等价的 PowerShell 包装

产物：`tests/ops_check/_artifacts/*.log`（每一步的原始输出）+ 本脚本 stdout 的汇总表。
所有需要数据库的探针都只操作 `_artifacts/db_*.sqlite3` 副本，绝不写 backend/db.sqlite3。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "backend"
ART = HERE / "_artifacts"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
FRONTEND = REPO / "frontend"

BASE_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONPATH": os.pathsep.join([str(REPO), str(BACKEND), os.environ.get("PYTHONPATH", "")]),
}


class Step:
    def __init__(self, sid: str, title: str, argv: list[str], cwd: Path = BACKEND,
                 slow: bool = False, env: dict | None = None, expect: str = "exit0"):
        self.sid = sid
        self.title = title
        self.argv = argv
        self.cwd = cwd
        self.slow = slow
        self.env = env or {}
        self.expect = expect
        self.status = "SKIP"
        self.detail = ""
        self.seconds = 0.0


def build_steps(include_slow: bool) -> list[Step]:
    py = str(PYTHON)
    steps: list[Step] = []

    if include_slow:
        steps.append(Step("A1", "基线回归：manage.py check",
                          [py, "manage.py", "check"], expect="check-ok"))
        steps.append(Step("A2", "基线回归：manage.py test apps tests_fix_verify（≥584 项）",
                          [py, "manage.py", "test", "apps", "tests_fix_verify"], slow=True,
                          env={"OPS_CHECK_DB": ""}, expect="django-test"))
    steps += [
        Step("BHIJ", "测试未削弱 / nginx+unit 静态 / 红线 / 零配置（static_checks.py）",
             [py, str(HERE / "static_checks.py")], expect="probe"),
        Step("C", "C1-a 总线：签名/转发/降级/内部端点/_after_commit/seq 单调（probe_bus.py）",
             [py, str(HERE / "probe_bus.py")], expect="probe"),
        Step("D", "C1 并发对照：WSGI 多线程 vs 单线程 vs daphne（probe_concurrency.py）",
             [py, str(HERE / "probe_concurrency.py")], expect="probe"),
        Step("E", "C2：PRAGMA 联动 / busy_timeout 真实性 / 并发写 / 审计降噪 / 归档（probe_sqlite.py）",
             [py, str(HERE / "probe_sqlite.py")], expect="probe"),
        Step("F1", "C3 后端：/auth/me 契约与 401、批量端点权限一致（probe_c3_api.py）",
             [py, str(HERE / "probe_c3_api.py")], expect="probe"),
        Step("F2", "C3 前端：心跳稳态/304/退避抖动/批量端点 独立复算（node probe_frontend_heartbeat.mjs）",
             ["node", str(HERE / "probe_frontend_heartbeat.mjs")], cwd=FRONTEND, expect="exit0"),
        Step("G", "备份/恢复：VACUUM INTO、apps.snapshots、副本闭环、回滚指引（probe_snapshot.py）",
             [py, str(HERE / "probe_snapshot.py")], expect="probe"),
        Step("F3", "既有 X 系列回归脚本 tests/fix_verify/scripts/*.py",
             [py, str(HERE / "_run_x_series.py")], expect="exit0"),
        Step("F4", "既有前端用例 tests/fix_verify/frontend/*.mjs（可运行子集；需预构建的标 SKIP）",
             [py, str(HERE / "_run_fe_mjs.py")], expect="exit0"),
        Step("F5", "前端类型检查 vue-tsc --noEmit（等价 npm run typecheck）",
             ["node", str(FRONTEND / "node_modules" / "vue-tsc" / "bin" / "vue-tsc.js"),
              "--noEmit"], cwd=FRONTEND, expect="exit0"),
    ]
    return steps


def run_step(step: Step) -> None:
    log = ART / f"{step.sid}.log"
    env = {**BASE_ENV, **step.env}
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(step.argv, cwd=str(step.cwd), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=3600)
        out = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except Exception as exc:  # noqa: BLE001
        out = f"启动失败：{type(exc).__name__}: {exc}"
        code = -1
    step.seconds = time.perf_counter() - t0
    log.write_text(out, encoding="utf-8")

    if step.expect == "probe":
        summary = [ln for ln in out.splitlines() if ln.startswith("[SUMMARY]")]
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        findings = [ln for ln in out.splitlines() if ln.startswith("[FINDING]")]
        step.status = "PASS" if code == 0 and not fails else "FAIL"
        step.detail = (summary[-1] if summary else f"exit={code}") + \
                      (f"；findings={len(findings)}" if findings else "")
        if fails:
            step.detail += f"；首条 FAIL：{fails[0][:160]}"
    elif step.expect == "django-test":
        import re

        m = re.search(r"Ran (\d+) test", out)
        ok = re.search(r"^OK", out, re.M)
        n = int(m.group(1)) if m else 0
        step.status = "PASS" if code == 0 and ok and n >= 584 else "FAIL"
        step.detail = f"Ran {n} tests；{'OK' if ok else 'FAILED'}；exit={code}"
    elif step.expect == "check-ok":
        ok = "System check identified no issues" in out
        step.status = "PASS" if code == 0 and ok else "FAIL"
        step.detail = ("System check identified no issues" if ok else "输出中未见 no issues") + f"；exit={code}"
    else:
        step.status = "PASS" if code == 0 else "FAIL"
        step.detail = f"exit={code}"


def cleanup(keep_db: bool) -> None:
    if keep_db:
        print("[INFO] --keep-db：保留 _artifacts 下的数据库副本与快照目录")
        return
    freed = 0
    for pat in ("db_*.sqlite3*", "wal_consistency_*", "*.sqlite3-wal", "*.sqlite3-shm"):
        for p in ART.glob(pat):
            try:
                freed += p.stat().st_size
                p.unlink()
            except OSError:
                pass
    for d in ("snapshots_roundtrip", "audit_archive_probe", "audit_archive_dryrun"):
        target = ART / d
        if target.is_dir():
            freed += sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
            shutil.rmtree(target, ignore_errors=True)
    print(f"[INFO] 已清理临时数据库/快照副本（约 {freed / 1024 / 1024:.1f} MB）；日志保留在 {ART}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过最慢的全量测试（A2）")
    ap.add_argument("--keep-db", action="store_true", help="保留临时数据库副本")
    ap.add_argument("--only", default="", help="只跑指定步骤 id（逗号分隔）")
    args = ap.parse_args()

    ART.mkdir(parents=True, exist_ok=True)
    print("=" * 100)
    print("Gipfel 整改验收 · 一键脚本（tests/ops_check/verify_all.py）")
    print(f"仓库={REPO}")
    print(f"python={PYTHON}")
    print("=" * 100, flush=True)

    # 刷新共享副本（探针全部只读/写副本）
    subprocess.run([str(PYTHON), str(HERE / "make_db_copy.py")], cwd=str(BACKEND),
                   env=BASE_ENV, capture_output=True, text=True)
    if not (ART / "db_copy.sqlite3").exists():
        print("[FATAL] 无法生成数据库副本 _artifacts/db_copy.sqlite3（探针全部依赖它）")
        return 2

    steps = build_steps(include_slow=not args.fast)
    if args.only:
        wanted = {s.strip().upper() for s in args.only.split(",") if s.strip()}
        steps = [s for s in steps if s.sid.upper() in wanted]

    for step in steps:
        print(f"\n>>> [{step.sid}] {step.title}", flush=True)
        if step.slow:
            print("    （慢：全量测试约 5 分钟，安静机器上更短）", flush=True)
        run_step(step)
        print(f"    [{step.status}] {step.detail}（{step.seconds:.1f}s，日志 {step.sid}.log）", flush=True)

    print("\n" + "=" * 100)
    print("汇总表")
    print("=" * 100)
    print(f"{'步骤':<6}{'结论':<7}{'耗时':>8}  说明")
    for step in steps:
        print(f"{step.sid:<6}{step.status:<7}{step.seconds:>7.1f}s  {step.title}")
        print(f"{'':<13}         → {step.detail}")
    fails = [s.sid for s in steps if s.status == "FAIL"]
    skips = [s.sid for s in steps if s.status == "SKIP"]
    print("-" * 100)
    print(f"PASS={sum(1 for s in steps if s.status == 'PASS')} FAIL={len(fails)} SKIP={len(skips)}"
          + (f" 失败步骤={fails}" if fails else ""))
    print("需 Linux 真机验证（本机无法执行）：nginx -t；gunicorn 多进程/多 worker 实跑；"
          "redis 总线；systemd unit 实际启动；daphne+WSGI 双进程下实时广播端到端。")
    cleanup(args.keep_db)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
