"""快照系统的回归测试。

覆盖：
1. 注册表完备性（业务模型全纳入、快照系统自身排除、依赖拓扑序正确）
2. 全系统快照 → 任意破坏 → 回退，逐字段还原（含主键与时间戳）
3. 比赛维度快照的隔离性（不影响其它比赛、不回写全局表）
4. 快照之后新建的行会被回退删除；已删除的行会被还原
5. 归档被篡改时校验失败、回退中止且数据零变化
6. 门禁：暂停期间写请求 423、读放行；回退中读写全拒；恢复后放行
7. dataVersion 回退后递增
8. 保留策略清理（锁定快照不被清理）
9. 账号数据默认只记录不回写

运行：cd backend && .venv\\Scripts\\python.exe manage.py test apps.snapshots -v 2
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase, override_settings

from apps.common.audit import log_write  # noqa: F401  （确保审计模块可用）
from apps.companies.models import Company
from apps.competitions.models import Competition
from apps.contracts.models import ContractType
from apps.industry_types.models import IndustryField, IndustryType
from apps.materials.models import Material
from apps.parts.models import Part, PartMaterial
from apps.users.models import User

from . import archive, engine, gate
from .models import Snapshot, SnapshotTable, SystemGate
from .registry import build_registry


def _reset_gate():
    SystemGate.objects.all().delete()


class SnapshotTestBase(TestCase):
    """公共基类：把归档目录指向仓库内的临时目录（Windows 下系统临时目录可能不可写）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmpdir = Path(settings.BASE_DIR) / "snapshots_test_tmp" / uuid.uuid4().hex[:8]
        cls._tmpdir.mkdir(parents=True, exist_ok=True)
        cls._override = override_settings(SNAPSHOT_DIR=cls._tmpdir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        parent = Path(settings.BASE_DIR) / "snapshots_test_tmp"
        if parent.exists() and not any(parent.iterdir()):
            shutil.rmtree(parent, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        _reset_gate()
        gate.load_state(force=True)
        self.comp_a = Competition.objects.create(name="比赛A")
        self.comp_b = Competition.objects.create(name="比赛B")
        self.super_user = User.objects.create_user(
            username="tester", password="x", role="SUPER_ADMIN"
        )

    def _seed(self, comp: Competition, tag: str):
        """在某比赛下造一套互相引用的数据。"""
        material = Material.objects.create(
            name=f"原料-{tag}",
            origin="产地",
            carbon_emission_coefficient=1.5,
            node_prices='{"1": 12.5}',
            competition=comp,
        )
        part = Part.objects.create(name=f"零件-{tag}", competition=comp)
        PartMaterial.objects.create(part=part, material=material, ratio=0.25)
        company = Company.objects.create(name=f"公司-{tag}", competition=comp)
        return material, part, company

    def _snapshot(self, *, competition=None, label="测试快照", include_files=False):
        return engine.create_snapshot(
            label=label,
            competition_id=getattr(competition, "id", None) if competition else None,
            include_files=include_files,
            operator=self.super_user,
        )


class RegistryTests(SnapshotTestBase):
    def test_registry_covers_all_business_models_and_excludes_self(self):
        registry = build_registry()
        names = {s.name for s in registry.specs}
        for expected in (
            "Competition",
            "FiscalYear",
            "Material",
            "Part",
            "PartMaterial",
            "Product",
            "Company",
            "CompanyFieldValue",
            "Contract",
            "ContractType",
            "Stock",
            "StockOrder",
            "MapNode",
            "MapEdge",
            "Message",
            "MessageRecipient",
            "User",
            "IndustryType",
            "AuditLog",
        ):
            self.assertIn(expected, names, f"注册表缺少模型 {expected}")
        for excluded in ("Snapshot", "SnapshotTable", "SystemGate", "SnapshotPolicy"):
            self.assertNotIn(excluded, names, f"快照系统自身表不应纳入快照：{excluded}")

    def test_topology_puts_parents_before_children(self):
        registry = build_registry()
        order = {s.label: s.order for s in registry.specs}
        self.assertLess(order["materials.Material"], order["parts.PartMaterial"])
        self.assertLess(order["parts.Part"], order["parts.PartMaterial"])
        self.assertLess(order["competitions.Competition"], order["materials.Material"])
        self.assertLess(order["companies.Company"], order["companies.CompanyFieldValue"])
        self.assertLess(order["industry_types.IndustryType"], order["industry_types.IndustryField"])
        self.assertLess(order["gipfel_messages.Message"], order["gipfel_messages.MessageRecipient"])

    def test_child_tables_resolve_competition_scope(self):
        registry = build_registry()
        part_material = registry.by_label("parts.PartMaterial")
        self.assertEqual(part_material.scope, "child")
        self.assertIn("part", part_material.scope_fks)
        industry_field = registry.by_label("industry_types.IndustryField")
        self.assertEqual(industry_field.scope, "global")


class SnapshotRoundTripTests(SnapshotTestBase):
    def test_full_system_roundtrip_restores_every_field(self):
        material, part, company = self._seed(self.comp_a, "A")
        self._seed(self.comp_b, "B")
        before = {
            "material": Material.objects.values().order_by("id").first(),
            "part": Part.objects.values().order_by("id").first(),
            "part_material": PartMaterial.objects.values().order_by("id").first(),
            "companies": list(Company.objects.values().order_by("id")),
        }
        snapshot = self._snapshot(label="全系统基线")
        self.assertEqual(snapshot.status, "ready")
        self.assertGreater(snapshot.row_count, 0)
        self.assertEqual(snapshot.table_count, len(build_registry().specs))

        # 破坏数据：改字段、删行、新增行
        Material.objects.filter(pk=material.id).update(
            name="被改坏的原料", carbon_emission_coefficient=999.0, node_prices="{}"
        )
        PartMaterial.objects.all().delete()
        Part.objects.filter(pk=part.id).delete()
        Company.objects.filter(pk=company.id).update(name="被改坏的公司")
        Material.objects.create(
            name="快照后新增", origin="x", carbon_emission_coefficient=0.0,
            competition=self.comp_a,
        )
        self.assertEqual(PartMaterial.objects.count(), 0)

        result = engine.restore_snapshot(snapshot, operator=self.super_user)
        self.assertGreater(result["insertedRows"], 0)

        self.assertEqual(
            Material.objects.values().order_by("id").first(), before["material"]
        )
        self.assertEqual(Part.objects.values().order_by("id").first(), before["part"])
        self.assertEqual(
            PartMaterial.objects.values().order_by("id").first(), before["part_material"]
        )
        self.assertEqual(
            list(Company.objects.values().order_by("id")), before["companies"]
        )
        # 快照之后新增的行必须被回退掉
        self.assertFalse(Material.objects.filter(name="快照后新增").exists())

    def test_restore_preserves_created_at_and_primary_keys(self):
        material, _part, _company = self._seed(self.comp_a, "A")
        original = Material.objects.values().get(pk=material.id)
        snapshot = self._snapshot()
        Material.objects.filter(pk=material.id).update(name="改名")
        Material.objects.create(
            name="新增", origin="o", carbon_emission_coefficient=0.1, competition=self.comp_a
        )
        engine.restore_snapshot(snapshot, operator=self.super_user)
        restored = Material.objects.values().get(pk=material.id)
        self.assertEqual(restored["name"], original["name"])
        self.assertEqual(restored["created_at"], original["created_at"])
        self.assertEqual(restored["updated_at"], original["updated_at"])
        self.assertEqual(Material.objects.count(), 1)

    def test_competition_snapshot_does_not_touch_other_competition(self):
        self._seed(self.comp_a, "A")
        self._seed(self.comp_b, "B")
        snapshot = self._snapshot(competition=self.comp_a)
        # 快照只包含 A 的数据
        self.assertEqual(snapshot.competition_id, self.comp_a.id)
        material_b = Material.objects.get(name="原料-B")
        Material.objects.filter(name="原料-A").update(name="A-改")
        Material.objects.create(
            name="A-新增", origin="o", carbon_emission_coefficient=0.2,
            competition=self.comp_a,
        )
        Material.objects.filter(pk=material_b.id).update(name="B-改")

        engine.restore_snapshot(snapshot, operator=self.super_user)

        self.assertTrue(Material.objects.filter(name="原料-A").exists())
        self.assertFalse(Material.objects.filter(name="A-新增").exists())
        # B 比赛的数据不受任何影响
        self.assertEqual(Material.objects.get(pk=material_b.id).name, "B-改")

    def test_competition_snapshot_records_but_skips_global_tables(self):
        industry_type = IndustryType.objects.create(name="产业X", code=9001)
        IndustryField.objects.create(
            industry_type=industry_type, name="字段1", field_key="f1"
        )
        snapshot = self._snapshot(competition=self.comp_a)
        policy = dict(
            SnapshotTable.objects.filter(snapshot=snapshot).values_list(
                "model_name", "policy"
            )
        )
        self.assertEqual(policy.get("IndustryType"), "record")
        self.assertEqual(policy.get("ContractType"), "record")
        self.assertEqual(policy.get("AuditLog"), "record")
        self.assertEqual(policy.get("User"), "record")
        self.assertEqual(policy.get("Competition"), "upsert")
        self.assertEqual(policy.get("Material"), "full")

        # 全局表在回退时保持不变
        IndustryType.objects.filter(pk=industry_type.pk).update(name="产业X-改后")
        engine.restore_snapshot(snapshot, operator=self.super_user)
        self.assertEqual(
            IndustryType.objects.get(pk=industry_type.pk).name, "产业X-改后"
        )

    def test_users_are_not_restored_by_default(self):
        user = User.objects.create_user(
            username="player1", password="x", role="PLAYER", competition=self.comp_a
        )
        snapshot = self._snapshot(competition=self.comp_a)
        User.objects.filter(pk=user.pk).update(role="SUPER_ADMIN", token_version=99)
        engine.restore_snapshot(snapshot, operator=self.super_user)
        refreshed = User.objects.get(pk=user.pk)
        self.assertEqual(refreshed.role, "SUPER_ADMIN")
        self.assertEqual(refreshed.token_version, 99)

    def test_restore_when_include_users_rewrites_without_deleting(self):
        user = User.objects.create_user(
            username="player2", password="x", role="PLAYER", competition=self.comp_a
        )
        snapshot = self._snapshot(competition=self.comp_a)
        User.objects.filter(pk=user.pk).update(role="SUPER_ADMIN")
        User.objects.create_user(
            username="player3", password="x", role="PLAYER", competition=self.comp_a
        )
        engine.restore_snapshot(
            snapshot, operator=self.super_user, include_users=True
        )
        self.assertEqual(User.objects.get(pk=user.pk).role, "PLAYER")
        # 不删除：快照之后新建的账号被保留
        self.assertTrue(User.objects.filter(username="player3").exists())

    def test_data_version_increments_on_restore(self):
        self._seed(self.comp_a, "A")
        snapshot = self._snapshot()
        before = gate.load_state(force=True)["dataVersion"]
        engine.restore_snapshot(snapshot, operator=self.super_user)
        after = gate.load_state(force=True)["dataVersion"]
        self.assertEqual(after, before + 1)

    def test_verify_detects_tampered_archive_and_restore_aborts(self):
        material, _p, _c = self._seed(self.comp_a, "A")
        snapshot = self._snapshot()
        self.assertTrue(engine.verify_snapshot(snapshot)["ok"])

        # 用「内容被改写」的方式篡改：gzip 仍然合法，但 sha256 必然对不上
        path = archive.table_file(snapshot.id, "materials")
        import gzip
        import json

        with gzip.open(path, "rt", encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        rows[0]["name"] = "被篡改的名字"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

        check = engine.verify_snapshot(snapshot)
        self.assertFalse(check["ok"])
        self.assertTrue(any("materials" in p for p in check["problems"]))

        Material.objects.filter(pk=material.pk).update(name="解压前的名字")
        with self.assertRaises(engine.SnapshotError):
            engine.restore_snapshot(snapshot, operator=self.super_user)
        # 校验失败 → 数据零变化
        self.assertEqual(Material.objects.get(pk=material.pk).name, "解压前的名字")

    def test_verify_reports_corrupted_file_without_crashing(self):
        """归档被截断/非 gzip 时应报告问题而不是抛异常。"""
        self._seed(self.comp_a, "A")
        snapshot = self._snapshot()
        path = archive.table_file(snapshot.id, "materials")
        path.write_bytes(b"not-a-gzip-stream")
        check = engine.verify_snapshot(snapshot)
        self.assertFalse(check["ok"])
        self.assertTrue(any("无法读取" in p for p in check["problems"]))

    def test_failed_restore_rolls_back_everything(self):
        """归档中被删掉一列（模拟模型演进）时应整体回滚，不留半截状态。"""
        material, _p, _c = self._seed(self.comp_a, "A")
        snapshot = self._snapshot()
        path = archive.table_file(snapshot.id, "materials")
        raw = archive.read_manifest(archive.snapshot_dir(snapshot.id))
        self.assertIn("tables", raw)
        path.write_bytes(b"not-a-gzip-stream")
        Material.objects.filter(pk=material.pk).update(name="保持不动")
        with self.assertRaises(Exception):
            engine.restore_snapshot(snapshot, operator=self.super_user)
        self.assertEqual(Material.objects.get(pk=material.pk).name, "保持不动")


class GateTests(SnapshotTestBase):
    def test_pause_blocks_writes_allows_reads(self):
        client = Client()
        gate.set_mode(gate.MODE_PAUSED, reason="测试暂停", operator_name="tester")

        blocked = client.post(
            "/api/materials",
            data={"name": "x"},
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 423)
        body = blocked.json()
        self.assertEqual(body["errorCode"], "system_paused")
        self.assertIn("强制暂停", body["message"])

        # 读请求放行（未登录 → 401，说明没有被门禁拦下）
        allowed = client.get("/api/materials")
        self.assertNotEqual(allowed.status_code, 423)

        gate.set_mode(gate.MODE_RUNNING)
        after = client.post(
            "/api/materials", data={"name": "x"}, content_type="application/json"
        )
        self.assertNotEqual(after.status_code, 423)

    def test_restoring_blocks_reads_too(self):
        client = Client()
        gate.set_mode(gate.MODE_RESTORING, reason="回退中")
        self.assertEqual(client.get("/api/materials").status_code, 423)
        self.assertEqual(client.get("/api/competitions").status_code, 423)
        # 白名单：门禁状态自身与健康检查必须可达
        self.assertNotEqual(client.get("/api/snapshots/gate").status_code, 423)
        self.assertNotEqual(client.get("/api/health").status_code, 423)

    def test_gate_state_reports_pause_metadata(self):
        gate.set_mode(
            gate.MODE_PAUSED,
            reason="数据校验",
            message="请勿操作",
            ttl_seconds=120,
            operator_id=7,
            operator_name="admin",
        )
        state = gate.load_state(force=True)
        self.assertEqual(state["mode"], "PAUSED")
        self.assertEqual(state["reason"], "数据校验")
        self.assertEqual(state["operatorName"], "admin")
        self.assertIsNotNone(state["expiresAt"])

    def test_exclusive_window_drains_and_resumes(self):
        gate.begin_write()
        with self.assertRaises(gate.GateBusy):
            with gate.exclusive_window(mode=gate.MODE_PAUSED, drain_timeout=0.05):
                pass
        gate.end_write()
        # 排空失败后门禁必须已经被恢复，不能把系统卡在暂停态
        self.assertEqual(gate.load_state(force=True)["mode"], gate.MODE_RUNNING)

        with gate.exclusive_window(mode=gate.MODE_RESTORING, drain_timeout=0.5):
            self.assertEqual(gate.load_state(force=True)["mode"], gate.MODE_RESTORING)
        self.assertEqual(gate.load_state(force=True)["mode"], gate.MODE_RUNNING)


class SnapshotApiTests(SnapshotTestBase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.force_authenticate(user=self.super_user)
        self._seed(self.comp_a, "A")

    def test_create_list_and_detail(self):
        res = self.api.post(
            "/api/snapshots",
            {"label": "接口快照", "competitionId": self.comp_a.id},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        payload = res.json()["data"]
        snapshot_id = payload["id"]
        self.assertEqual(payload["status"], "ready")

        listing = self.api.get("/api/snapshots").json()["data"]
        self.assertGreaterEqual(listing["total"], 1)

        detail = self.api.get(f"/api/snapshots/{snapshot_id}").json()["data"]
        self.assertTrue(any(t["modelName"] == "Material" for t in detail["tables"]))

        diff = self.api.get(f"/api/snapshots/{snapshot_id}/diff").json()["data"]
        self.assertEqual(diff["totals"]["affectedTables"] > 0, True)

        verify = self.api.get(f"/api/snapshots/{snapshot_id}/verify").json()["data"]
        self.assertTrue(verify["ok"])

    def test_restore_requires_confirmation_text(self):
        snapshot = self._snapshot(competition=self.comp_a, label="确认用")
        bad = self.api.post(
            f"/api/snapshots/{snapshot.id}/restore", {"confirmText": "随便写"}, format="json"
        )
        self.assertNotEqual(bad.status_code, 200)
        self.assertEqual(gate.load_state(force=True)["mode"], gate.MODE_RUNNING)

        ok = self.api.post(
            f"/api/snapshots/{snapshot.id}/restore",
            {"confirmText": str(snapshot.id), "skipSafetySnapshot": True},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        data = ok.json()["data"]
        self.assertEqual(data["snapshotId"], snapshot.id)
        self.assertGreaterEqual(data["dataVersion"], 1)
        # 回退完成后系统必须已恢复运行
        self.assertEqual(gate.load_state(force=True)["mode"], gate.MODE_RUNNING)

    def test_restore_creates_safety_snapshot_by_default(self):
        snapshot = self._snapshot(competition=self.comp_a, label="带安全快照")
        res = self.api.post(
            f"/api/snapshots/{snapshot.id}/restore",
            {"confirmText": "回退"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        safety_id = res.json()["data"]["safetySnapshotId"]
        self.assertIsNotNone(safety_id)
        safety = Snapshot.objects.get(pk=safety_id)
        self.assertEqual(safety.kind, "pre-restore")

    def test_status_endpoint_exposes_gate_and_counts(self):
        data = self.api.get("/api/snapshots/status").json()["data"]
        self.assertIn("gate", data)
        self.assertIn("policy", data)
        self.assertGreater(data["registryTables"], 30)

    def test_gate_endpoint_is_readable_by_any_logged_in_user(self):
        """强制暂停必须能触达每一个普通选手：门禁端点只要求登录，不要求超管权限。"""
        from rest_framework.test import APIClient

        player = User.objects.create_user(
            username="player-gate", password="x", role="PLAYER", competition=self.comp_a
        )
        client = APIClient()
        client.force_authenticate(user=player)

        gate.set_mode(gate.MODE_PAUSED, reason="全体暂停")
        res = client.get("/api/snapshots/gate")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["data"]["mode"], "PAUSED")
        self.assertEqual(res.json()["data"]["reason"], "全体暂停")

        # 但业务管理端点仍然只对超管开放
        self.assertIn(client.get("/api/snapshots").status_code, (401, 403))
        self.assertIn(client.post("/api/snapshots/gate/resume", {}, format="json").status_code, (401, 403))
        gate.set_mode(gate.MODE_RUNNING)

    def test_anonymous_cannot_read_gate(self):
        self.assertIn(self.client.get("/api/snapshots/gate").status_code, (401, 403))

    def test_cleanup_respects_lock(self):
        snap_old = self._snapshot(competition=self.comp_a, label="旧快照")
        snap_new = self._snapshot(competition=self.comp_a, label="新快照")
        Snapshot.objects.filter(pk=snap_old.pk).update(locked=True)
        dry = engine.cleanup_snapshots(keep_last=1, keep_days=0, dry_run=True)
        self.assertEqual(dry["removedCount"], 0, "锁定快照不应被清理")
        result = engine.cleanup_snapshots(keep_last=0, keep_days=0)
        self.assertEqual(result["removedCount"], 0)
        self.assertFalse(dry["dryRun"] is None)
        engine.cleanup_snapshots(keep_last=0, keep_days=0)
        # 解锁后可按份数清理
        Snapshot.objects.filter(pk=snap_old.pk).update(locked=False)
        result = engine.cleanup_snapshots(keep_last=1, keep_days=0)
        self.assertEqual(result["removedCount"], 1)
        self.assertEqual(Snapshot.objects.count(), 1)
        self.assertEqual(Snapshot.objects.first().pk, snap_new.pk)

    def test_download_returns_tarball(self):
        snapshot = self._snapshot(competition=self.comp_a, label="下载用")
        res = self.client.get(f"/api/snapshots/{snapshot.id}/download")
        # 未登录 → 401；此处只验证路由存在（鉴权由 DRF 负责）
        self.assertIn(res.status_code, (401, 403))
        res = self.api.get(f"/api/snapshots/{snapshot.id}/download")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/gzip")


class PolicyTests(SnapshotTestBase):
    def test_default_policy_values(self):
        policy = engine.get_policy()
        self.assertEqual(policy.keep_last, 20)
        self.assertEqual(policy.keep_days, 7)
        self.assertFalse(policy.auto_enabled)

    def test_auto_snapshot_respects_interval(self):
        policy = engine.get_policy()
        from apps.snapshots.models import SnapshotPolicy

        SnapshotPolicy.objects.filter(pk=policy.pk).update(
            auto_enabled=True, auto_interval_minutes=60, auto_scope="system"
        )
        first = engine.auto_snapshot_if_due()
        self.assertIsNotNone(first)
        second = engine.auto_snapshot_if_due()
        self.assertIsNone(second, "同一间隔内不应重复创建自动快照")
        forced = engine.auto_snapshot_if_due(force=True)
        self.assertIsNotNone(forced)


class ContractTypeHelpTests(SnapshotTestBase):
    """契约性检查：回退不得把全局合同类型删掉（Contract 对其为 RESTRICT）。"""

    def test_global_contract_type_survives_restore(self):
        ctype = ContractType.objects.create(name="标准合同", key="STD")
        self._seed(self.comp_a, "A")
        snapshot = self._snapshot(competition=self.comp_a)
        engine.restore_snapshot(snapshot, operator=self.super_user)
        self.assertTrue(ContractType.objects.filter(pk=ctype.pk).exists())
