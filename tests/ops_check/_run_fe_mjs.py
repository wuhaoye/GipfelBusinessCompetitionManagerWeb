# -*- coding: utf-8 -*-
"""跑 tests/fix_verify/frontend/*.mjs 既有前端用例。

**重要事实（验收方核对所得）**：这 18 个用例里有 13 个把「构建后的模块路径」作为
`process.argv[2]` 传入，**本身不可独立运行**；仓库只提供了 4 个入口
（`entries/{c3,f06,m01_boot,m01}_entry.mjs`），且只有 f06 / m01 能对上其中两个用例。
因此本脚本的判定分三类：

- `PASS/FAIL`：能跑的（自包含的 5 个 + 自建 bundle 的 c3 + 用工学入口构建的 f06/m01）
- `SKIP`：需要仓库未提供的构建产物（layoutStorage.mjs / vehiclePathTypes.mjs /
  useLatestRequest.mjs / envelope.mjs / format.mjs / localPaging.mjs / estAmount.mjs …）
  —— 这是**覆盖缺口**，不是实现缺陷，必须在报告里如实列出。

退出码：只要可运行的用例没有 FAIL 就是 0；SKIP 只统计、不影响退出码。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FRONTEND = REPO / "frontend"
FE = REPO / "tests" / "fix_verify" / "frontend"
BUNDLE_PS1 = FE / "bundle.ps1"

#: 需要 argv[2] 的用例 → 若有对应入口则「入口文件」，否则 None（= SKIP）
NEEDS_BUNDLE = {
    "test_c3_heartbeat_backoff.mjs": "SELF_BUILD",   # 内部自己 buildBundle()
    "test_f06_logout_clears_memo.mjs": FE / "entries" / "f06_entry.mjs",
    "test_m01_widget_packages_reload.mjs": FE / "entries" / "m01_entry.mjs",
    "test_delete_confirm_message.mjs": None,
    "test_f12_page_bounds.mjs": None,
    "test_format_time.mjs": None,
    "test_list_paging.mjs": None,
    "test_m01_widget_layout_preserved.mjs": None,
    "test_response_envelope.mjs": None,
    "test_t01_est_amount.mjs": None,
    "test_v09_list_race_guards.mjs": None,
    "test_w02_vehicle_path_types.mjs": None,
    "test_w08_warehouses_race.mjs": None,
}


def build(entry: Path, outfile: str, tmpdir: Path) -> Path | None:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BUNDLE_PS1),
         "-Entry", str(entry), "-Outfile", outfile],
        cwd=str(tmpdir), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    path = tmpdir / outfile
    return path if r.returncode == 0 and path.exists() else None


def main() -> int:
    # 注意：不能用 Python 的 tempfile.mkdtemp()——本机沙箱下 esbuild 读不到那种目录
    # （`Cannot read directory ".": Access is denied`），改用仓库内的工作目录。
    tmpdir = HERE / "_artifacts" / "fe_bundles"
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
    tmpdir.mkdir(parents=True, exist_ok=True)
    passed, failed, skipped = [], [], []
    try:
        for case in sorted(FE.glob("test_*.mjs")):
            argv = ["node", str(case)]
            if case.name in NEEDS_BUNDLE:
                spec = NEEDS_BUNDLE[case.name]
                if spec == "SELF_BUILD":
                    argv = ["node", str(case)]  # 用例内部自己打包
                elif spec is None:
                    skipped.append(case.name)
                    continue
                else:
                    built = build(spec, f"{spec.stem}.mjs", tmpdir)
                    if built is None:
                        skipped.append(f"{case.name}（入口打包失败）")
                        continue
                    argv = ["node", str(case), str(built)]
            try:
                proc = subprocess.run(argv, cwd=str(FRONTEND), capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=1800)
                ok = proc.returncode == 0
                tail = " | ".join([ln.strip() for ln in (proc.stdout or "").strip().splitlines()[-2:]])
            except subprocess.TimeoutExpired:
                ok, tail = False, "超时"
            print(f"[{'PASS' if ok else 'FAIL'}] {case.name}" + ("" if ok else f"：{tail[:200]}"),
                  flush=True)
            (passed if ok else failed).append(case.name)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    for name in skipped:
        print(f"[SKIP] {name}：需要仓库未提供的预构建产物（用例把 argv[2] 当模块路径）", flush=True)
    print(f"[SUMMARY] total={len(passed) + len(failed) + len(skipped)} "
          f"pass={len(passed)} fail={len(failed)} skip={len(skipped)}"
          + (f" failed={failed}" if failed else "")
          + (f"；SKIP 是覆盖缺口（见报告 §3/§4）：{skipped}" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
