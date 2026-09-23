# -*- coding: utf-8 -*-
"""contract_watcher 端到端验证（真实后端 + 真实 SQLite；不启动 Excel）。

覆盖：
  A. PLAYER 账号启动 CLI → 退出码 3（角色门禁）
  B. COMPETITION_ADMIN 启动 → 公司目录只含 companyScopes 内的公司
  C. 合同通过 → 只进入 SQLite（不写 xlsx）；达阈值 → 一次批量记账（无记账规则 ⇒ 不开 Excel）
  D. 手动请求 → 立即记账（trigger=manual）
  E. 财年更迭信号（PATCH 财年为 CLOSED）→ 增量轮询检测 FY_END → 批量结账
运行前需先启动后端；运行后自动清理测试数据（比赛/公司/合同类型/合同/账号/财年）。

用法（仓库根目录，或 backend 目录均可；须用 backend 的 venv Python）：
    cd backend
    .\\.venv\\Scripts\\python.exe ..\\tests\\fix_verify\\watcher\\e2e_watcher_backend.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WATCHER_DIR = REPO / "contract_watcher"
SCRATCH = Path(__file__).resolve().parent / ".tmp" / "e2e"
SERVER = os.environ.get("E2E_SERVER", "http://127.0.0.1:8231")
PASSWORD = "E2ePw!2026"
MARK = "__e2e_cw__"
USERS = ["e2e_cw_admin", "e2e_cw_referee", "e2e_cw_player"]

sys.path.insert(0, str(REPO / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
import django  # noqa: E402

django.setup()

from django.utils import timezone  # noqa: E402

from apps.companies.models import Company  # noqa: E402
from apps.competitions.models import Competition, FiscalYear  # noqa: E402
from apps.contracts.models import Contract, ContractType  # noqa: E402
from apps.users.models import User  # noqa: E402

RESULTS: list[dict] = []


def load_sibling(name: str):
    key = f"contract_watcher_{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, WATCHER_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


cw = load_sibling("contract_watcher")
store = load_sibling("store")


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        raise AssertionError(f"{name} {detail}")


def http_json(method: str, url: str, token=None, body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ==================== 测试数据 ====================
def cleanup() -> None:
    Contract.objects.filter(competition__name=MARK).delete()
    ContractType.objects.filter(key__in=["e2e_cw_probe"]).delete()
    User.objects.filter(username__in=USERS).delete()
    Competition.objects.filter(name=MARK).delete()
    FiscalYear.objects.filter(competition__name=MARK).delete()


def setup_data() -> dict:
    cleanup()
    comp = Competition.objects.create(name=MARK, status="ACTIVE")
    a = Company.objects.create(name="E2E-A", competition=comp)
    b = Company.objects.create(name="E2E-B", competition=comp)
    ct = ContractType.objects.create(
        key="e2e_cw_probe",
        name="E2E 探测合同",
        party_roles=json.dumps(
            [{"role": "甲方", "isHost": False, "label": "甲方"},
             {"role": "主办方", "isHost": True, "label": "主办方"}],
            ensure_ascii=False,
        ),
        input_schema=json.dumps([{"key": "amount", "label": "金额", "type": "NUMBER"}],
                                ensure_ascii=False),
    )

    def make_contract(company, idx):
        return Contract.objects.create(
            competition=comp, contract_type=ct, name=f"E2E-{company.name}-{idx}",
            status="EXECUTED",
            parties=json.dumps(
                [{"role": "甲方", "companyId": company.id, "companyName": company.name,
                  "contractNumber": f"E2E-{company.id}-{idx}"},
                 {"role": "主办方", "isHost": True, "companyId": None}],
                ensure_ascii=False,
            ),
            inputs=json.dumps({"amount": 1000 * idx}, ensure_ascii=False),
            execution_log="[]",
            execution_result=json.dumps({"fields": {}, "checks": []}, ensure_ascii=False),
            executed_at=timezone.now(),
        )

    contracts_a = [make_contract(a, i) for i in (1, 2, 3)]
    contract_b = make_contract(b, 1)
    fy = FiscalYear.objects.create(competition=comp, year=2026, status="ACTIVE")
    User.objects.create_user(username="e2e_cw_admin", password=PASSWORD, role="SUPER_ADMIN")
    User.objects.create_user(
        username="e2e_cw_referee", password=PASSWORD, role="COMPETITION_ADMIN",
        competition=comp, company_scopes=json.dumps([a.id]),
    )
    User.objects.create_user(
        username="e2e_cw_player", password=PASSWORD, role="PLAYER",
        competition=comp, company_scopes=json.dumps([a.id]),
    )
    return {
        "competition": comp.id, "company_a": a.id, "company_b": b.id,
        "contracts_a": [c.id for c in contracts_a], "contract_b": contract_b.id,
        "fiscal_year": fy.id,
    }


# ==================== A. 角色门禁（CLI 子进程） ====================
def test_player_cli_rejected(tmp: Path, data: dict) -> None:
    out, err = tmp / "player.out", tmp / "player.err"
    with out.open("w", encoding="utf-8") as fo, err.open("w", encoding="utf-8") as fe:
        proc = subprocess.run(
            [sys.executable, str(WATCHER_DIR / "contract_watcher.py"),
             "--server", SERVER, "--username", "e2e_cw_player", "--password", PASSWORD,
             "--port", "47698", "--out-dir", str(tmp / "player_out"),
             "--db", str(tmp / "player.db")],
            stdout=fo, stderr=fe, cwd=str(REPO),
        )
    stderr = err.read_text(encoding="utf-8", errors="replace")
    check("A1 PLAYER 启动被拒绝（退出码 3）", proc.returncode == 3,
          f"returncode={proc.returncode} stderr={stderr.strip()[:200]}")
    check("A2 提示信息包含 PLAYER 与放开方式", "PLAYER" in stderr and "allow-player" in stderr)


# ==================== B~E. 主流程（进程内会话） ====================
def test_main_flow(tmp: Path, data: dict) -> None:
    cw.WATCHER_DIR = tmp
    cw.HANDLERS_FILE = tmp / "handlers.py"
    cw.HANDLERS_FILE.write_text("# 空模板（无处理函数 ⇒ 不写分录、不开 Excel）\n", encoding="utf-8")
    cw.STATE_FILE = tmp / "data" / "state.json"
    cw.RECORDS_DIR = tmp / "records"
    (tmp / "data").mkdir(parents=True, exist_ok=True)

    backend = cw.Backend(SERVER, "e2e_cw_referee", PASSWORD)
    backend.login()
    check("B1 登录成功且角色为 COMPETITION_ADMIN", backend.role == "COMPETITION_ADMIN",
          f"role={backend.role}")
    check("B2 角色门禁放行", cw.find_role_denial(backend.role) is None)
    check("B3 登录响应带公司管理范围", backend.company_scopes == [data["company_a"]],
          f"scopes={backend.company_scopes}")

    conn = store.open_db(tmp / "watcher.db")
    companies = backend.fetch_companies(backend.competition_id)
    manageable = cw.resolve_manageable_ids(backend.role, backend.company_scopes,
                                           [c["id"] for c in companies])
    store.sync_companies(conn, companies, manageable)
    cw.apply_company_selection(conn, None, manageable)
    check("B4 只把 companyScopes 内的公司标为可管理",
          store.manageable_ids(conn) == [data["company_a"]],
          f"manageable={store.manageable_ids(conn)}")
    check("B5 记账目标默认勾选可管理公司",
          store.selected_ids(conn) == [data["company_a"]])

    session = cw.WatcherSession(
        backend=backend, state={"lastExecutedAt": "", "baselineAt": "e2e"},
        registry=[{}], out_dir=tmp / "records", competition_id=data["competition"],
        conn=conn, threshold=10, books_dir=tmp / "books",
        book_template=WATCHER_DIR / "bookkeeping_example" / "target.xlsx",
        fiscal_year_interval=0,
    )
    session.run_round()
    pending_a = store.pending_count(conn, data["company_a"])
    check("C1 合同通过后只进入 SQLite（未达阈值不记账）",
          pending_a == 3 and store.last_batch(conn, data["company_a"]) is None,
          f"pending={pending_a}")
    check("C2 范围外公司的合同不入库",
          store.pending_count(conn, data["company_b"]) == 0)
    check("C3 合同按公司分账（companyId 主键）",
          len(store.company_contracts(conn, data["company_a"])) == 3)

    # 达阈值 → 一次批量记账（无处理函数 ⇒ 不打开 Excel）
    session.threshold = 3
    session.check_thresholds()
    batch = store.last_batch(conn, data["company_a"])
    check("C4 达到阈值触发一次批量记账",
          batch is not None and batch["trigger"] == "threshold" and batch["status"] == "success",
          f"batch={dict(batch) if batch else None}")
    check("C5 无处理函数时不打开 Excel（COM 次数为 0）",
          "未打开 Excel" in (batch["message"] or ""), f"message={batch['message']}")
    check("C6 记账后待记账数清零", store.pending_count(conn, data["company_a"]) == 0)

    # 再来一份合同（未达阈值）→ 手动请求 → trigger=manual
    ct = ContractType.objects.get(key="e2e_cw_probe")
    comp = Competition.objects.get(pk=data["competition"])
    company_a = Company.objects.get(pk=data["company_a"])
    Contract.objects.create(
        competition=comp, contract_type=ct, name="E2E-A-4", status="EXECUTED",
        parties=json.dumps(
            [{"role": "甲方", "companyId": company_a.id, "companyName": company_a.name,
              "contractNumber": "E2E-A-4"}, {"role": "主办方", "isHost": True}],
            ensure_ascii=False),
        inputs=json.dumps({"amount": 4000}, ensure_ascii=False),
        execution_log="[]", execution_result='{"fields": {}, "checks": []}',
        executed_at=timezone.now(),
    )
    session.run_round()
    check("D1 新合同入库但仍未达阈值", store.pending_count(conn, data["company_a"]) == 1)
    request_id = store.request_flush(conn, company_id=data["company_a"], trigger="manual",
                                     requested_by="e2e")
    session.consume_flush_requests()
    batch = store.last_batch(conn, data["company_a"])
    req = conn.execute("SELECT * FROM flush_requests WHERE id = ?", (request_id,)).fetchone()
    check("D2 手动请求立即触发记账",
          batch["trigger"] == "manual" and store.pending_count(conn, data["company_a"]) == 0,
          f"trigger={batch['trigger']}")
    check("D3 请求被标记为已完成", req["status"] == "done", f"status={req['status']}")

    # 财年更迭：先用 PATCH 由超管把财年置为 CLOSED，再看监听端能否从增量轮询里推出来
    session.poll_fiscal_years()          # 首次同步建立基线
    admin = cw.Backend(SERVER, "e2e_cw_admin", PASSWORD)
    admin.login()
    env = http_json("PATCH", f"{SERVER}/api/competitions/fiscal-years/{data['fiscal_year']}",
                    admin.token, {"status": "CLOSED"})
    check("E1 超管关闭财年成功", env.get("code") == 0 and env["data"]["status"] == "CLOSED",
          str(env)[:160])

    Contract.objects.create(
        competition=comp, contract_type=ct, name="E2E-A-5", status="EXECUTED",
        parties=json.dumps(
            [{"role": "甲方", "companyId": company_a.id, "companyName": company_a.name,
              "contractNumber": "E2E-A-5"}, {"role": "主办方", "isHost": True}],
            ensure_ascii=False),
        inputs=json.dumps({"amount": 5000}, ensure_ascii=False),
        execution_log="[]", execution_result='{"fields": {}, "checks": []}',
        executed_at=timezone.now(),
    )
    # 本轮：合同先入库，随后财年更迭（FY_END）触发结账 → 该合同在**同轮**被记账
    session.run_round()
    batch = store.last_batch(conn, data["company_a"])
    check("E2 财年结束触发的记账包含本轮新入库的合同",
          batch["trigger"] == "fiscal_year_end" and batch["contract_count"] == 1
          and store.pending_count(conn, data["company_a"]) == 0,
          f"trigger={batch['trigger']} count={batch['contract_count']}")
    check("E3 更迭被记录为 FY_END",
          any(t["transition"] == "FY_END" for t in session.fiscal_transitions),
          str(session.fiscal_transitions))

    conn.close()


def write_report(tmp: Path) -> None:
    lines = ["# contract_watcher 端到端验证报告（真实后端 + SQLite）", ""]
    lines.append(f"- 后端：{SERVER}")
    lines.append(f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("| 用例 | 结果 | 说明 |")
    lines.append("| --- | --- | --- |")
    for r in RESULTS:
        lines.append(f"| {r['name']} | {'✅' if r['ok'] else '❌'} | {r['detail'] or ''} |")
    lines.append("")
    (SCRATCH / "E2E_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {SCRATCH / 'E2E_REPORT.md'}")


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    tmp = SCRATCH / f"run_{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    data = setup_data()
    print("测试数据：", json.dumps(data, ensure_ascii=False))
    try:
        test_player_cli_rejected(tmp, data)
        test_main_flow(tmp, data)
    finally:
        cleanup()
        print("测试数据已清理")
    write_report(tmp)
    ok = all(r["ok"] for r in RESULTS)
    print(f"\n合计 {len(RESULTS)} 项，{'全部通过' if ok else '存在失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
