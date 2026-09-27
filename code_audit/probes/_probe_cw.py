# -*- coding: utf-8 -*-
"""Pure-function verification (read-only w.r.t. repo code; writes only in code_audit/_probe_tmp)."""
import sys, os, json, tempfile, pathlib, shutil
from decimal import Decimal, getcontext

WS = r"C:\Users\wuhao\Desktop\shang\gipfel\GipfelBusinessCompetitionManagerWeb"
BASE = os.path.join(WS, "contract_watcher")
sys.path.insert(0, BASE)
TMPROOT = os.path.join(WS, "code_audit", "_probe_tmp")
os.makedirs(TMPROOT, exist_ok=True)

print("=== 1) slug/func name collisions ===")
import contract_watcher as cw
pairs = [("material-procurement", "material_procurement"), ("ABC", "abc"),
         ("a.b", "a-b"), ("---", "..."), ("e2e_watcher_recheck", "e2e-watcher-recheck"),
         ("x", "x_passed")]
for a, b in pairs:
    fa, fb = cw.func_name_of(a), cw.func_name_of(b)
    print(f"  {a!r} -> {fa!r} | {b!r} -> {fb!r} | collide={fa == fb} | substring={fa in fb}")

print("=== 2) upsert overwrites user's hand-written handler ===")
tmp = os.path.join(TMPROOT, "run1")
os.makedirs(tmp, exist_ok=True)
cw.HANDLERS_FILE = pathlib.Path(tmp) / "handlers.py"
cw.ensure_handlers_file()
cw.HANDLERS_FILE.write_text(
    "# template\n\n"
    "def handle_abc_passed(contract, ctx):\n"
    "    ctx['USER_CODE_RAN'] = True\n",
    encoding="utf-8")
made = cw.upsert_handler_for_key("ABC")
reg = cw.load_handlers()
text = cw.HANDLERS_FILE.read_text(encoding="utf-8")
print(f"  appended_new_block={made}; def-count={text.count('def handle_abc_passed')}")
fn = reg.get("ABC")
print(f"  registry['ABC'] src-line={fn.__code__.co_firstlineno if fn else None} of {len(text.splitlines())} lines")
hit = {}
if fn:
    try:
        fn({"id": 1}, hit)
    except Exception as e:
        hit["err"] = repr(e)
print(f"  call -> ctx={hit}  (USER_CODE_RAN present means user code won)")

print("=== 3) executedAt string comparison ===")
ref = "2026-09-10T15:20:46.731818Z"
for c in ["2026-09-10T15:20:46Z", "2026-09-10T15:20:46.731818Z",
          "2026-09-10T23:20:46+08:00", "2026-09-10T15:20:47Z", "2026-09-10T15:20:46.9Z"]:
    print(f"  {c!r:36} > ref = {c > ref}")
print("  max(mixed) =", max(["2026-09-10T15:20:46Z", "2026-09-10T15:20:46.731818Z"]))

print("=== 4) readable.pretty_value edges ===")
from readable import pretty_value, translate_contract
for v in [None, True, 0, -5, 1234567, 1234567.891, "1234", "+1234", "-0012345", "007",
          "1e5", "  88  ", "12345678901234567890", 1e21, "abc", "3.10", {"a": 1}, [1, 2]]:
    print(f"  {v!r:24} -> {pretty_value(v)!r}")

print("=== 5) Decimal prec=2 (shang.py global side effect) ===")
print("  default prec:", getcontext().prec)
getcontext().prec = 2
print("  after prec=2:")
print("   1234.56 + 0.44        =", Decimal("1234.56") + Decimal("0.44"))
print("   12345.67 * 3          =", Decimal("12345.67") * 3)
print("   1000000 * 0.13        =", Decimal("1000000") * Decimal("0.13"))
print("   999999.99 / 3         =", Decimal("999999.99") / 3)
for bad in [None, "", "1,000"]:
    try:
        float(bad)
        print(f"   float({bad!r}) OK")
    except Exception as e:
        print(f"   float({bad!r}) -> {type(e).__name__}: {e}")
print("   None != 0 ->", None != 0)

print("=== 6) safe_dirname traversal ===")
for k in ["..", "...", "../..", "a/b", "CON", "x" * 300]:
    sd = cw.safe_dirname(k)
    print(f"  {k[:16]!r:20} -> {sd[:32]!r} escape={sd in ('..', '../..', '.')}")

print("=== 7) generated function for hostile keys ===")
for k in ['bad"""key', "nl\nkey", "x = 1 #"]:
    sec = cw.build_default_section(k)
    try:
        compile(sec, "<gen>", "exec")
        print(f"  key={k!r} compile OK")
    except SyntaxError as e:
        print(f"  key={k!r} SyntaxError: {e}")

print("=== 8) readable output for edge contract ===")
c = {"id": 1, "competitionId": 2, "status": "EXECUTED",
     "executedAt": "2026-09-10T15:20:46.731818Z", "createdAt": "2026-09-10T15:20:46.731818Z",
     "contractType": {"key": "k", "name": "T"}, "inputs": {},
     "parties": [{"role": "A", "companyId": 3, "companyName": None, "isHost": False}],
     "executionLog": [{"kind": "FIELD", "companyId": 3, "fieldKey": "cash", "op": "SUB",
                       "value": "12345678901234567890", "before": "1e21", "after": None}],
     "executionResult": {"checks": [{"label": "L", "passed": False, "detail": None}]}}
rec = translate_contract(c)
print("  executedAt shown:", rec["执行时间"], "| party company:", rec["参与方"][0]["公司"],
      "| log company:", rec["落账明细"][0]["公司"])
print("  value:", rec["落账明细"][0]["数值"], "| before:", rec["落账明细"][0]["变动前"])

print("=== 9) JSON round trip precision ===")
raw = '{"amount": 12345678901234567890.9876, "v2": 0.1}'
obj = json.loads(raw)
print("  ", obj, "->", json.dumps(obj))

print("=== 10) MARKER_RE indentation ===")
print("  matches indented marker:", bool(cw.MARKER_RE.match('  # ===== [auto] ContractType.key = x =====')))

shutil.rmtree(TMPROOT, ignore_errors=True)
print("done")
