# -*- coding: utf-8 -*-
"""从 docs/audit/units/*.md 单元报告机械抽取全部缺陷条目，生成 docs/audit/00-缺陷总索引.md。

只做搬运：标题原文、位置原文，不新增结论。

用法（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe code_audit/tools/_build_index.py
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]        # code_audit/tools/ -> 仓库根
ROOT = REPO / "docs" / "audit" / "units"          # 21 份单元报告所在目录
OUT = REPO / "docs" / "audit" / "00-缺陷总索引.md"  # 生成物
REPORT_RE = re.compile(r"^(U\d\d|B\d\d)-.*\.md$")

# 单元 → 分支归属
BRANCH = {
    "U": "master",
    "B01": "bugfix / feature_wuhaoye",
    "B02": "origin/prep-export-import",
    "B03": "feature/contract-type-code",
    "B04": "feature/contract-type-code",
    "B05": "feature/contract-type-code",
    "B06": "test",
    "B08": "feature/contract-watcher",
}
TITLE = {
    "U01": "backend core（settings/urls/asgi + apps/common）",
    "U02": "backend identity（auth/users/audit/realtime/files/widget_packages/announcements/messages/logviewer）",
    "U03": "backend org（competitions/companies/company_fields/industry_types/regions/maps/tech_tree）",
    "U04": "backend production chain（materials/parts/products/fuels/warehouses/vehicles/infrastructures/production_lines/consumer_demands）",
    "U05": "backend contracts core（models/serializers/views/engine）",
    "U06": "backend stock（引擎/视图 + tests 股票脚本）",
    "U07": "frontend core（api/stores/router/realtime/utils/permissions/config/types）",
    "U08": "frontend views data1（合同/地图/科技树/合同类型/燃料）",
    "U09": "frontend views data2（产业类型/原料/零件/产品/载具/仓库/基建/产线）",
    "U10": "frontend views biz1（stocks/dashboard/regions）",
    "U11": "frontend views biz2（账号/公司/比赛/消息/登录/审计/设置）",
    "U12": "frontend components1（合同图编辑器/简单编辑器/公式输入/产业字段图编辑器）",
    "U13": "frontend components2（layout/common/dashboard + 全局弹窗）",
    "U14": "scripts / deploy / tests / 示例控件包",
    "B01": "dev 启动脚本（scripts/dev.py + start-dev.bat + stop-dev.bat）",
    "B02": "比赛准备 导出/导入/体检",
    "B03": "比赛建包库（preparation/builder + build_competition）",
    "B04": "合同类型建库-内核（contracts/builder）",
    "B05": "合同类型建库-体检/CLI/测试",
    "B06": "Excel 建包工具链（examples/excel + auto_chain）",
    "B08": "合同通过监听程序（contract_watcher）",
}

SEV_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "?": 4}


def parse_report(path: Path):
    """返回 (findings, susp_count)。findings: list[dict]"""
    findings: list[dict] = []
    susp = 0
    cur = None
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            section = line[3:].strip()
        m = re.match(r"^###\s*\[(P[0-3])\]\s*(.+?)\s*$", line)
        if not m:
            m2 = re.match(r"^###\s+(.+?)\s*$", line)
            # 只在"缺陷清单"章节内、且标题像缺陷编号时才当成条目
            if m2 and "缺陷" in section and re.match(r"^`?[A-Z]{1,3}-?\d+", m2.group(1)):
                cur = {"sev": "?", "title": m2.group(1), "loc": []}
                findings.append(cur)
                continue
            if m2 and "缺陷" in section:
                cur = None
            continue
        cur = {"sev": m.group(1), "title": m.group(2), "loc": []}
        findings.append(cur)

    # 逐行补位置
    cur = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        m = re.match(r"^###\s*\[(P[0-3])\]\s*(.+?)\s*$", line)
        if m:
            cur = findings.pop(0) if False else cur  # placeholder
    # 简化：重新扫描，按顺序把「位置：」归属到最近的条目
    idx = -1
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if re.match(r"^###\s*\[(P[0-3])\]", line) or (
            re.match(r"^###\s+`?[A-Z]{1,3}-?\d+", line) and "缺陷" in section
        ):
            idx += 1
            continue
        if idx >= 0 and idx < len(findings):
            m = re.match(r"^-\s*位置：(.*)$", line)
            if m and not findings[idx]["loc"]:
                findings[idx]["loc"].append(m.group(1).strip())

    text = path.read_text(encoding="utf-8")
    m = re.search(r"存疑[^\n]*?(\d+)\s*条", text)
    if m:
        susp = int(m.group(1))
    return findings, susp


def clean_loc(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s*（.*?）\s*$", "", s) if s.endswith("）") and s.count("（") == s.count("）") else s
    return s


def short(title: str, n: int = 90) -> str:
    t = re.sub(r"\s+", " ", title).strip()
    t = t.replace("|", "\\|")
    return t if len(t) <= n else t[: n - 1] + "…"


def main() -> None:
    reports = sorted(p for p in ROOT.iterdir() if REPORT_RE.match(p.name))
    per_unit = {}
    for p in reports:
        unit = p.name.split("-")[0]
        findings, susp = parse_report(p)
        per_unit[unit] = (findings, susp, p.name)

    total = sum(len(v[0]) for v in per_unit.values())
    bysev: dict[str, int] = {}
    for findings, _, _ in per_unit.values():
        for f in findings:
            bysev[f["sev"]] = bysev.get(f["sev"], 0) + 1

    out: list[str] = []
    out.append("# 缺陷总索引（21 个审计单元）")
    out.append("")
    out.append(
        "> 本文件由 `code_audit/tools/_build_index.py` 从 `docs/audit/units/` 的 21 份单元报告**机械抽取**"
        "（标题与位置原文照搬，未改写结论）。级别为**单元自评**，"
        "与《分支代码缺陷审计报告.md》的重排口径可能相差一级。"
    )
    out.append(
        f"> 统计：合计 **{total}**"
        f"（P0={bysev.get('P0', 0)}、P1={bysev.get('P1', 0)}、"
        f"P2={bysev.get('P2', 0)}、P3={bysev.get('P3', 0)}"
        + (f"、未标注级别={bysev.get('?', 0)}" if bysev.get("?") else "")
        + "）。详细取证（代码摘录/触发条件/后果/修复建议）见各单元报告。"
    )
    out.append("")

    # 一、按单元
    out.append("## 一、按单元（含分支归属）")
    out.append("")
    for unit in sorted(per_unit):
        findings, susp, name = per_unit[unit]
        if not findings:
            continue
        cnt: dict[str, int] = {}
        for f in findings:
            cnt[f["sev"]] = cnt.get(f["sev"], 0) + 1
        stat = "、".join(f"{k}×{cnt[k]}" for k in ("P0", "P1", "P2", "P3", "?") if cnt.get(k))
        out.append(
            f"### {unit} {TITLE.get(unit, '')}｜分支：{BRANCH.get(unit, 'master')}｜"
            f"{len(findings)} 条（{stat}）｜`code_audit/{name}`"
        )
        out.append("")
        out.append("| 编号 | 级别 | 位置 | 一句话 |")
        out.append("| --- | --- | --- | --- |")
        for f in sorted(findings, key=lambda x: SEV_ORDER.get(x["sev"], 9)):
            num = f["title"].split(" ")[0].strip("`")
            title = f["title"]
            if title.startswith(num):
                title = title[len(num):].strip()
            loc = clean_loc("；".join(f["loc"])) if f["loc"] else "—"
            loc = loc.replace("`", "")
            if len(loc) > 160:
                loc = loc[:159] + "…"
            out.append(f"| {num} | {f['sev']} | {loc} | {short(title)} |")
        if susp:
            out.append("")
            out.append(f"（另有存疑/待确认 {susp} 条，见原报告）")
        out.append("")

    # 二、按级别
    out.append("## 二、按级别汇总（跨单元）")
    out.append("")
    for sev in ("P0", "P1", "P2", "P3", "?"):
        rows = [
            (unit, f)
            for unit in sorted(per_unit)
            for f in per_unit[unit][0]
            if f["sev"] == sev
        ]
        if not rows:
            continue
        out.append(f"### {sev}（{len(rows)} 条）")
        out.append("")
        out.append("| 单元 | 编号 | 位置 | 一句话 |")
        out.append("| --- | --- | --- | --- |")
        for unit, f in rows:
            num = f["title"].split(" ")[0].strip("`")
            title = f["title"]
            if title.startswith(num):
                title = title[len(num):].strip()
            loc = clean_loc("；".join(f["loc"])) if f["loc"] else "—"
            loc = loc.replace("`", "")
            if len(loc) > 160:
                loc = loc[:159] + "…"
            out.append(f"| {unit} | {num} | {loc} | {short(title)} |")
        out.append("")

    # 三、跨单元同源聚类（依据各报告正文对同一 文件:行号 的重复指控）
    out.append("## 三、跨单元同源/重复项（同一根因，避免重复修）")
    out.append("")
    out.append("| # | 主题 | 涉及条目 | 共同根因位置 |")
    out.append("| --- | --- | --- | --- |")
    clusters = [
        ("非有限浮点落库 → 出站渲染 500", "U01 C-03；U03 O-04；U04 P-02",
         "`apps/common/renderers.py:34`（float 分支无 is_finite）+ `maps/serializers.py:95`/`materials/serializers.py:17`（FloatField 无界）"),
        ("金额精度：SQLite 上 Decimal(60) 只保 15 位有效数字", "U06 S-08；U14 X-17（同一回环测试写真实开发库）；仓库根《浮点精度检测报告.md》E1",
         "各 app `models.py` 的 `DecimalField(max_digits=60, decimal_places=4)` + SQLite NUMERIC 亲和性（`tests/sqlite_decimal_roundtrip.py` 实跑 FAIL）"),
        ("删除确认弹窗未转义 HTML（存储型 XSS）", "U07 F-02；U08 V-01；U09（说明并入 V-01）",
         "`frontend/src/utils/deleteConfirm.ts:36-48`（dangerouslyUseHTMLString + 未转义 name）"),
        ("列表被缓存层静默截断为前 50/100 条", "U07（reconstruct 根因）；U08 V-04；U09 W-05；U11 A-01；U13 备注②",
         "`frontend/src/api/request.ts:336-343`（params.pageSize 缺省 50）"),
        ("切比赛/并发时旧响应覆盖新数据", "U07 F-05；U08 V-09；U09 W-08；U10 T-03/T-04；U11 A-05",
         "各视图 loadData 无请求代次/取消（如 `WarehousesManager.vue:213-220`）"),
        ("BigNumberInput 边界（min 只提示、指数记法、无位数上限）", "U09 W-06/W-12；U13 M-05/M-07；U10（去重说明）",
         "`frontend/src/components/common/BigNumberInput.vue:68-95`"),
        ("乐观锁不可达 / 丢失更新", "U03 O-08；U05 K-07/K-10；U06 S-10",
         "`company_fields/views.py:91-99`（version 缺省=当前）+ 全仓无 `select_for_update`"),
        ("审计留痕缺失（Decimal 序列化失败 / .update() 绕过 / 无节流）", "U01 C-02/C-04/C-05；U02 I-12/I-14",
         "`apps/common/audit.py:70`、`apps/common/signals.py:201`、`apps/common/exceptions.py:44`"),
        ("权限模型：扩展集不可授予 + 前端 fail-open", "U02 I-13；U04（可达性校准）；U07 F-04",
         "`apps/common/permissions.py:388-401`、`frontend/src/permissions/catalog.ts:74-83`"),
        ("未定义标识符静默取 0（引擎/构建器/前端编辑器）", "U05 K-04；U12 G-04；B04 E-04/E-10；B05 L-08",
         "`contracts/engine.py:905`（未定义变量 return 0）+ 值源编译路径"),
        ("唯一性/冲突检测先查后插（TOCTOU）", "U01 C-08；U03 O-11；U04 P-08",
         "`apps/common/base_crud.py:146`、各 serializer 的 `filter().exists()` + `create()`"),
        ("负值/缺下界进入业务数据", "U04 P-01；U09 W-06；B06 Z-02",
         "`fuels|warehouses|production_lines|infrastructures|consumer_demands/serializers.py` 无 min_value；`sheet_spec.py:530+` 数值列无下界"),
        ("单请求内存/磁盘耗尽（大对象无上限）", "U02 I-06/I-07；U03 O-05；U05 K-16；B02 R-15~R-20（归档无大小上限）",
         "`apps/files/views.py:238`、`widget_packages/views.py:86-95`、`company_fields/timer.py:76`"),
        ("时区：UTC 与 Asia/Shanghai 混用", "U07 F-08；B08（readable 输出 UTC）",
         "`frontend/src/utils/format.ts:117-122` 的 toISOString；`contract_watcher/readable.py`"),
        ("账号切换后残留（内存 memo / 消息锁 / 房间订阅 / 视图缓存）", "U07 F-06/F-13；U10 T-05；U13 M-02",
         "`frontend/src/stores/auth.ts:183-197`（logout 不清 memo）+ message store 幂等锁 + competition store 直写"),
        ("导入/建包幂等与事务缺失", "B02 R-01~R-04；B03 N-02/N-09；B04 E-09；B05 L-02",
         "`preparation/archive.py:2657-2676`（吞异常无 savepoint）、`build_contract_types.py:310-346`（--import 无事务）"),
        ("顶号/禁用账号未真正断开或吊销", "U02 I-01/I-02/I-03；U05 K-10（并发执行无行锁，同属会话/并发一致性）",
         "`realtime/gateway.py:119-127`（只 emit 不 disconnect）、`auth/authentication.py:104-114`（不查 is_active）、`users/views.py:159-165`（不递增 token_version）"),
    ]
    for i, (topic, items, root) in enumerate(clusters, 1):
        out.append(f"| {i} | {topic} | {items} | {root} |")
    out.append("")
    out.append("> 说明：本节的聚类依据是各单元报告对**同一 `文件:行号`** 的独立指控；"
               "若某条在单元报告里已被标注为\"与其它单元同因\"，此处不再重复展开。")
    out.append("")

    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("wrote docs/audit/00-缺陷总索引.md")
    print("units:", len(per_unit), "total:", total, "by sev:", bysev)


if __name__ == "__main__":
    main()
