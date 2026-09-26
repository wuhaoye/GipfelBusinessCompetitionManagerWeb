"""真实库端到端验证：快照 → 破坏 → 回退 → 逐表比对。

在**开发库**上跑一遍完整闭环，验证：
1. 全系统快照能覆盖所有业务表；
2. 回退后每张表的 sha256 与快照一致（逐位还原，含主键与时间戳）；
3. 快照之后新增的行会被回退清除；
4. 回退会把 SystemGate.data_version +1。

用法（务必先备份 db.sqlite3）：
    cd backend
    copy db.sqlite3 db.sqlite3.bak
    .venv\\Scripts\\python.exe ..\\tests\\snapshot_tools\\roundtrip_real_db.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django  # noqa: E402

django.setup()

from apps.snapshots import archive, engine, gate  # noqa: E402
from apps.snapshots.registry import build_registry, scope_queryset  # noqa: E402


def fingerprint() -> dict:
    """全库逐表指纹（行数 + 内容 sha256）。"""
    out = {}
    for spec in build_registry().specs:
        rows, digest = archive.db_table_checksum(spec, scope_queryset(spec, None))
        out[spec.label] = (rows, digest)
    return out


def main() -> int:
    before = fingerprint()
    total_rows = sum(r for r, _ in before.values())
    print(f"当前库：{len(before)} 张表 / {total_rows} 行")

    version_before = gate.load_state(force=True)["dataVersion"]
    print("创建全系统快照…")
    snapshot = engine.create_snapshot(
        label="真实库验证快照",
        note="roundtrip_real_db.py 自动创建",
        kind="manual",
        competition_id=None,
        include_files=True,
    )
    print(
        f"  快照 #{snapshot.id}：{snapshot.row_count} 行 / {snapshot.table_count} 张表 / "
        f"{snapshot.byte_size} 字节 / 上传文件 {snapshot.file_count} 个"
    )

    check = engine.verify_snapshot(snapshot)
    assert check["ok"], f"归档自检失败：{check['problems'][:3]}"
    print("  归档 sha256 自检：通过")

    # ---------- 破坏数据（可逆实验，只动 3 张表） ----------
    from apps.competitions.models import Competition
    from apps.materials.models import Material
    from apps.parts.models import Part

    touched = []
    comp = Competition.objects.order_by("id").first()
    if comp:
        Competition.objects.filter(pk=comp.pk).update(name=comp.name + "[被改坏]")
        touched.append(f"competitions#{comp.pk}.name")
    material = Material.objects.order_by("id").first()
    if material:
        Material.objects.filter(pk=material.pk).update(name="被改坏的原料", node_prices="{}")
        touched.append(f"materials#{material.pk}.name")
    deleted = 0
    if material:
        deleted, _ = Material.objects.filter(pk=material.pk).delete()
        touched.append(f"materials#{material.pk} 整行删除")
    new_id = None
    if comp:
        row = Part.objects.create(name="快照后新增的零件", competition=comp)
        new_id = row.pk
        touched.append(f"parts#{new_id} 新增行")
    print("已破坏：" + "；".join(touched))

    # ---------- 回退 ----------
    print("执行回退…")
    with gate.exclusive_window(
        mode=gate.MODE_RESTORING,
        reason="真实库验证",
        operator_name="roundtrip_real_db",
        ttl_seconds=600,
        snapshot_id=snapshot.id,
    ):
        result = engine.restore_snapshot(snapshot, verify=True, progress=lambda m: None)
    print(
        f"  删除 {result['deletedRows']} 行 / 写回 {result['insertedRows']} 行 / "
        f"{result['durationMs']}ms / dataVersion={result['dataVersion']}"
    )

    # ---------- 比对 ----------
    after = fingerprint()
    # 审计日志按设计「只记录不回写」（回退/快照操作本身也会追加审计），单独统计不参与严格比对
    append_only = {"audit.AuditLog"}
    diffs = [
        label
        for label in before
        if label not in append_only and before[label] != after.get(label)
    ]
    audit_before = before.get("audit.AuditLog")
    audit_after = after.get("audit.AuditLog")
    version_after = gate.load_state(force=True)["dataVersion"]

    ok = True
    if diffs:
        ok = False
        print(f"[FAIL] 以下表未完全还原（{len(diffs)} 张）：")
        for label in diffs[:20]:
            print(f"    {label}: 快照前 {before[label]} -> 回退后 {after.get(label)}")
    else:
        print(
            f"[OK] 除只追加的审计日志外，全部 {len(before) - len(append_only)} 张表逐位还原"
            "（sha256 完全一致）"
        )
    if audit_before and audit_after:
        print(
            f"     审计日志按设计不回写：{audit_before[0]} 行 -> {audit_after[0]} 行"
            "（快照/回退操作本身会追加审计）"
        )
    if new_id and Part.objects.filter(pk=new_id).exists():
        ok = False
        print(f"[FAIL] 快照之后新增的 parts#{new_id} 未被清除")
    else:
        print("[OK] 快照之后新增的行已被回退清除")
    if version_after != version_before + 1:
        ok = False
        print(f"[FAIL] dataVersion 未按预期递增：{version_before} -> {version_after}")
    else:
        print(f"[OK] dataVersion {version_before} -> {version_after}")
    print(f"快照归档目录：{archive.snapshot_dir(snapshot.id)}")
    print(f"（可用 manage.py snapshot_restore --id {snapshot.id} --yes 再次回退）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
