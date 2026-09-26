"""快照系统的 REST 接口（挂在 /api/snapshots 下）。

路由一览
--------
    GET    /api/snapshots                       快照列表（分页 + 过滤）
    POST   /api/snapshots                       创建快照
    GET    /api/snapshots/status                概览（门禁 / 策略 / 数量 / 占用）
    GET    /api/snapshots/gate                  当前全局门禁状态
    POST   /api/snapshots/gate/pause            强制暂停（冻结所有人的写入）
    POST   /api/snapshots/gate/resume           恢复运行
    GET    /api/snapshots/policy                自动快照与保留策略
    PUT    /api/snapshots/policy                更新策略
    POST   /api/snapshots/cleanup               按策略清理（支持 dryRun）
    GET    /api/snapshots/<id>                  详情（含每张表清单）
    DELETE /api/snapshots/<id>                  删除（?force=true 可删锁定快照）
    GET    /api/snapshots/<id>/diff             回退预览（将删除/写回多少行）
    GET    /api/snapshots/<id>/verify           归档完整性校验
    GET    /api/snapshots/<id>/download         下载归档（tar.gz）
    POST   /api/snapshots/<id>/restore          强制暂停 + 回退（核心接口）
    POST   /api/snapshots/<id>/lock             锁定/解锁（锁定后不被保留策略清理）

权限：`snapshot:view` / `snapshot:manage`（创建、删除、策略）/ `snapshot:restore`
（暂停、恢复、回退）。三者均为超管专属（见 apps/common/permissions.py）。
"""
from __future__ import annotations

import io
import logging
import re
import tarfile
import urllib.parse

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.audit import log_write
from apps.common.exceptions import BusinessError
from apps.common.guards import PermissionsPermission, require_permissions
from apps.common.pagination import paginated_response, parse_pagination

from . import archive as archive_mod
from . import engine, gate
from .models import Snapshot, SnapshotPolicy, SnapshotTable

logger = logging.getLogger("gipfel")

_PERM_CLASSES = (IsAuthenticated, PermissionsPermission)
PERM_VIEW = "snapshot:view"
PERM_MANAGE = "snapshot:manage"
PERM_RESTORE = "snapshot:restore"

#: 回退/暂停时的默认 TTL（秒）：0 表示不自动恢复。回退用有限 TTL 兜底，
#: 避免进程崩溃后系统永久停在「回退中」。
DEFAULT_RESTORE_TTL = int(getattr(settings, "SNAPSHOT_RESTORE_TTL_SECONDS", 900))
DEFAULT_PAUSE_TTL = int(getattr(settings, "SNAPSHOT_PAUSE_TTL_SECONDS", 0))
#: 下载打包上限（字节）
DOWNLOAD_MAX_BYTES = int(getattr(settings, "SNAPSHOT_DOWNLOAD_MAX_BYTES", 256 * 1024 * 1024))

_UNSAFE_FILENAME = re.compile(r"[^\w\u4e00-\u9fff\-]+", re.UNICODE)


# ====================================================================
# 工具
# ====================================================================
def _truthy(raw, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _as_int(raw, default=None):
    if raw in (None, "", "null", "undefined"):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BusinessError(f"参数必须是整数：{raw}", code=400, status_code=400) from exc


def _is_super_admin(request) -> bool:
    return getattr(request.user, "role", None) == "SUPER_ADMIN"


def _get_snapshot(pk: int) -> Snapshot:
    snapshot = Snapshot.objects.filter(pk=pk).first()
    if snapshot is None:
        raise BusinessError("请求的资源不存在", code=404, status_code=404)
    return snapshot


def _assert_visible(request, snapshot: Snapshot) -> None:
    """比赛域隔离：非超管只能看到自己比赛（或全系统）的快照。"""
    if _is_super_admin(request):
        return
    own = getattr(request.user, "competition_id", None)
    if snapshot.competition_id not in (None, own):
        raise BusinessError("请求的资源不存在", code=404, status_code=404)


def _safe_filename(name: str, fallback: str = "snapshot") -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", (name or "").strip()).strip("_")
    return cleaned or fallback


def _audit(request, action: str, *, snapshot_id=None, competition_id=None, changes=None) -> None:
    try:
        log_write(
            model="Snapshot",
            action=action,
            record_id=snapshot_id,
            changes=changes,
            competition_id=competition_id,
        )
    except Exception:  # noqa: BLE001
        logger.debug("快照审计写入失败", exc_info=True)


def _operator_name(request) -> str:
    return getattr(request.user, "username", "") or ""


# ====================================================================
# 状态 / 门禁
# ====================================================================
class SnapshotStatusAPIView(APIView):
    """GET /api/snapshots/status —— 概览（含门禁状态）。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_VIEW)
    def get(self, request):
        return Response(engine.snapshot_status())


class SnapshotGateAPIView(APIView):
    """GET /api/snapshots/gate —— 当前全局门禁状态（暂停遮罩与数据版本同步用）。

    权限：**仅要求已登录**（不要求 `snapshot:view`）。这是有意为之 ——
    「强制暂停」必须能触达每一个普通选手：客户端在 WebSocket 握手与定期轮询时都会读该端点，
    若要求超管权限，非超管会拿到 403 而永远看不到暂停遮罩。该端点只返回门禁状态
    （模式 / 原因 / 提示文案 / 操作人 / 数据版本），不含任何业务数据，故对登录用户开放是安全的。
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(gate.load_state(force=True))


class SnapshotGatePauseAPIView(APIView):
    """POST /api/snapshots/gate/pause —— 强制暂停所有写入。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_RESTORE)
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        reason = str(body.get("reason") or "管理员手动暂停").strip()[:255]
        message = str(body.get("message") or "系统已暂停，请暂停一切操作，等待管理员恢复。")
        ttl = _as_int(body.get("ttlSeconds"), DEFAULT_PAUSE_TTL) or 0
        if gate.is_paused():
            state = gate.load_state(force=True)
            return Response({**state, "alreadyPaused": True})
        state = gate.set_mode(
            gate.MODE_PAUSED,
            reason=reason,
            message=message,
            ttl_seconds=max(0, int(ttl)),
            operator_id=getattr(request.user, "id", None),
            operator_name=_operator_name(request),
        )
        # 等待在途写请求结束（拿静止点），失败则恢复运行并报错
        if not gate.drain():
            gate.set_mode(
                gate.MODE_RUNNING,
                reason="暂停失败：仍有写入在途",
                operator_name=_operator_name(request),
            )
            raise BusinessError(
                f"仍有 {gate.active_writers()} 个写请求未结束，暂停未生效，请稍后重试",
                code=409,
                status_code=409,
            )
        _audit(request, "snapshot:gate-pause", changes={"reason": reason, "ttlSeconds": ttl})
        return Response(state)


class SnapshotGateResumeAPIView(APIView):
    """POST /api/snapshots/gate/resume —— 恢复运行。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_RESTORE)
    def post(self, request):
        if gate.is_exclusive_busy():
            raise BusinessError("系统正在执行快照/回退操作，无法手动恢复", code=409, status_code=409)
        body = request.data if isinstance(request.data, dict) else {}
        reason = str(body.get("reason") or "管理员手动恢复").strip()[:255]
        state = gate.set_mode(
            gate.MODE_RUNNING,
            reason=reason,
            operator_id=getattr(request.user, "id", None),
            operator_name=_operator_name(request),
        )
        _audit(request, "snapshot:gate-resume", changes={"reason": reason})
        return Response(state)


# ====================================================================
# 策略 / 清理
# ====================================================================
class SnapshotPolicyAPIView(APIView):
    """GET/PUT /api/snapshots/policy —— 自动快照与保留策略。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_VIEW)
    def get(self, request):
        return Response(engine.get_policy().to_dict())

    @require_permissions(PERM_MANAGE)
    def put(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        policy = engine.get_policy()
        updates = {}
        if "autoEnabled" in body:
            updates["auto_enabled"] = _truthy(body.get("autoEnabled"))
        if "autoIntervalMinutes" in body:
            updates["auto_interval_minutes"] = max(1, _as_int(body.get("autoIntervalMinutes"), 30))
        if "autoScope" in body:
            scope = str(body.get("autoScope") or "system").strip()
            if scope not in ("system", "competition"):
                raise BusinessError("autoScope 只能是 system 或 competition", code=400, status_code=400)
            updates["auto_scope"] = scope
        if "keepLast" in body:
            updates["keep_last"] = max(0, _as_int(body.get("keepLast"), 20))
        if "keepDays" in body:
            updates["keep_days"] = max(0, _as_int(body.get("keepDays"), 7))
        if "autoIncludeFiles" in body:
            updates["auto_include_files"] = _truthy(body.get("autoIncludeFiles"))
        if updates:
            SnapshotPolicy.objects.filter(pk=policy.pk).update(**updates)
            policy.refresh_from_db()
        _audit(request, "snapshot:policy-update", changes=updates)
        return Response(policy.to_dict())


class SnapshotCleanupAPIView(APIView):
    """POST /api/snapshots/cleanup —— 按保留策略清理（dryRun 预览）。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_MANAGE)
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        dry_run = _truthy(body.get("dryRun"), False)
        result = engine.cleanup_snapshots(
            keep_last=_as_int(body.get("keepLast"), None),
            keep_days=_as_int(body.get("keepDays"), None),
            dry_run=dry_run,
        )
        if not dry_run:
            _audit(request, "snapshot:cleanup", changes=result)
        return Response(result)


# ====================================================================
# 列表 / 创建 / 详情
# ====================================================================
class SnapshotListAPIView(APIView):
    """GET /api/snapshots（列表），POST /api/snapshots（创建）。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_VIEW)
    def get(self, request):
        qs = Snapshot.objects.all()
        if not _is_super_admin(request):
            own = getattr(request.user, "competition_id", None)
            qs = qs.filter(competition_id=own) if own else qs.filter(pk__in=[])
        raw_cid = request.query_params.get("competitionId")
        if raw_cid in ("system", "null", "none"):
            qs = qs.filter(competition_id__isnull=True)
        elif raw_cid not in (None, ""):
            qs = qs.filter(competition_id=_as_int(raw_cid))
        kind = (request.query_params.get("kind") or "").strip()
        if kind:
            qs = qs.filter(kind=kind)
        status = (request.query_params.get("status") or "").strip()
        if status == "ready":
            qs = qs.filter(status__in=("ready", "restored"))
        elif status:
            qs = qs.filter(status=status)
        keyword = (request.query_params.get("q") or "").strip()
        if keyword:
            from django.db.models import Q

            qs = qs.filter(Q(label__icontains=keyword) | Q(note__icontains=keyword))
        page, page_size, skip = parse_pagination(request.query_params)
        total = qs.count()
        items = [s.to_summary() for s in qs[skip : skip + page_size]]
        return Response(paginated_response(items, total, page, page_size))

    @require_permissions(PERM_MANAGE)
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        if gate.is_paused():
            raise BusinessError(
                "系统当前处于暂停/回退状态，请先恢复后再创建快照",
                code=409,
                status_code=409,
            )
        scope = str(body.get("scope") or "").strip().lower()
        competition_id = _as_int(body.get("competitionId"), None)
        if scope == "system":
            competition_id = None
        elif competition_id is None and scope != "system":
            # 缺省取当前登录账号所属比赛；超管未指定比赛时默认全系统
            competition_id = getattr(request.user, "competition_id", None)
        if not _is_super_admin(request):
            competition_id = getattr(request.user, "competition_id", None)
            if not competition_id:
                raise BusinessError("当前账号未归属任何比赛", code=400, status_code=400)

        label = str(body.get("label") or "").strip()
        note = str(body.get("note") or "").strip()
        include_files = _truthy(body.get("includeFiles"), False)
        pause_first = _truthy(body.get("pauseFirst"), False)
        ttl = max(30, _as_int(body.get("ttlSeconds"), 300))

        if pause_first:
            with gate.exclusive_window(
                mode=gate.MODE_PAUSED,
                reason="正在创建数据快照",
                message="系统正在创建数据快照，请稍候…",
                ttl_seconds=ttl,
                operator_id=getattr(request.user, "id", None),
                operator_name=_operator_name(request),
                resume_reason="快照创建完成，系统已恢复",
            ):
                snapshot = engine.create_snapshot(
                    label=label,
                    note=note,
                    kind="manual",
                    competition_id=competition_id,
                    include_files=include_files,
                    operator=request.user,
                    progress=gate.set_progress,
                )
        else:
            snapshot = engine.create_snapshot(
                label=label,
                note=note,
                kind="manual",
                competition_id=competition_id,
                include_files=include_files,
                operator=request.user,
                progress=gate.set_progress,
            )
        _audit(
            request,
            "snapshot:create",
            snapshot_id=snapshot.id,
            competition_id=competition_id,
            changes={
                "label": snapshot.label,
                "scope": snapshot.scope,
                "rows": snapshot.row_count,
                "tables": snapshot.table_count,
                "includeFiles": include_files,
                "pauseFirst": pause_first,
            },
        )
        return Response(snapshot.to_summary())


class SnapshotDetailAPIView(APIView):
    """GET /api/snapshots/<id>（详情），DELETE /api/snapshots/<id>（删除）。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_VIEW)
    def get(self, request, pk):
        snapshot = _get_snapshot(pk)
        _assert_visible(request, snapshot)
        tables = [t.to_dict() for t in SnapshotTable.objects.filter(snapshot=snapshot)]
        manifest = None
        try:
            manifest = archive_mod.read_manifest(archive_mod.snapshot_dir(snapshot.id))
        except Exception:  # noqa: BLE001
            manifest = None
        return Response(
            {
                **snapshot.to_summary(),
                "tables": tables,
                "manifest": manifest,
                "storagePath": snapshot.storage_path,
            }
        )

    @require_permissions(PERM_MANAGE)
    def delete(self, request, pk):
        snapshot = _get_snapshot(pk)
        _assert_visible(request, snapshot)
        force = _truthy(request.query_params.get("force"), False)
        if gate.load_state()["activeSnapshotId"] == snapshot.id:
            raise BusinessError("该快照正在被回退，无法删除", code=409, status_code=409)
        info = {"id": snapshot.id, "label": snapshot.label, "locked": snapshot.locked}
        engine.delete_snapshot(snapshot, force=force)
        _audit(request, "snapshot:delete", snapshot_id=info["id"], changes=info)
        return Response({"ok": True, **info})


class SnapshotLockAPIView(APIView):
    """POST /api/snapshots/<id>/lock —— 锁定/解锁。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_MANAGE)
    def post(self, request, pk):
        snapshot = _get_snapshot(pk)
        _assert_visible(request, snapshot)
        body = request.data if isinstance(request.data, dict) else {}
        locked = _truthy(body.get("locked"), not snapshot.locked)
        Snapshot.objects.filter(pk=snapshot.id).update(locked=locked)
        _audit(request, "snapshot:lock", snapshot_id=snapshot.id, changes={"locked": locked})
        return Response({"id": snapshot.id, "locked": locked})


# ====================================================================
# 校验 / 预览 / 下载
# ====================================================================
class SnapshotVerifyAPIView(APIView):
    """GET|POST /api/snapshots/<id>/verify —— 归档完整性校验。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_VIEW)
    def get(self, request, pk):
        snapshot = _get_snapshot(pk)
        _assert_visible(request, snapshot)
        include_current = _truthy(request.query_params.get("withCurrent"), False)
        return Response(engine.verify_snapshot(snapshot, recompute_current=include_current))

    post = get


class SnapshotDiffAPIView(APIView):
    """GET /api/snapshots/<id>/diff —— 回退预览。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_VIEW)
    def get(self, request, pk):
        snapshot = _get_snapshot(pk)
        _assert_visible(request, snapshot)
        include_users = _truthy(request.query_params.get("includeUsers"), False)
        restore_global = _truthy(request.query_params.get("restoreGlobal"), False)
        return Response(
            engine.diff_snapshot(
                snapshot, include_users=include_users, restore_global=restore_global
            )
        )


class SnapshotDownloadAPIView(APIView):
    """GET /api/snapshots/<id>/download —— 打包下载快照归档（tar.gz）。"""

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_VIEW)
    def get(self, request, pk):
        snapshot = _get_snapshot(pk)
        _assert_visible(request, snapshot)
        directory = archive_mod.snapshot_dir(snapshot.id)
        if not directory.exists():
            raise BusinessError("快照归档不存在", code=404, status_code=404)
        total = archive_mod.dir_size(directory)
        if total > DOWNLOAD_MAX_BYTES:
            raise BusinessError(
                f"归档体积 {total} 字节超过下载上限 {DOWNLOAD_MAX_BYTES}，"
                "请直接在服务器上取用归档目录",
                code=400,
                status_code=400,
            )
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            tar.add(directory, arcname=f"snap-{snapshot.id:06d}")
        body = buffer.getvalue()
        ts = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        filename = f"快照_{_safe_filename(snapshot.label)}_{snapshot.id}_{ts}.tar.gz"
        quoted = urllib.parse.quote(filename)
        response = HttpResponse(body, content_type="application/gzip")
        response["Content-Disposition"] = (
            f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"
        )
        response["Cache-Control"] = "no-store"
        _audit(request, "snapshot:download", snapshot_id=snapshot.id)
        return response


# ====================================================================
# 回退（核心）
# ====================================================================
class SnapshotRestoreAPIView(APIView):
    """POST /api/snapshots/<id>/restore —— 强制暂停全体 + 回退数据。

    请求体：
        confirmText     必填。快照编号、"回退" 或快照名称之一，防误触
        reason          可选，展示给全体用户的暂停原因
        includeUsers    可选，是否回写账号数据（默认 false）
        restoreGlobal   可选，是否回写全局表（默认 false，仅全系统快照有意义）
        restoreFiles    可选，是否还原上传文件（默认 false）
        skipSafetySnapshot 可选，是否跳过「回退前自动安全快照」（默认 false，强烈建议保留）
        verify          可选，回退后是否做内容校验（默认 true）
        ttlSeconds      可选，回退中状态的最长持续时间（默认 900 秒兜底自愈）
    """

    permission_classes = _PERM_CLASSES

    @require_permissions(PERM_RESTORE)
    def post(self, request, pk):
        snapshot = _get_snapshot(pk)
        _assert_visible(request, snapshot)
        body = request.data if isinstance(request.data, dict) else {}

        confirm = str(body.get("confirmText") or "").strip()
        allowed = {str(snapshot.id), "回退", "restore", snapshot.label}
        if confirm not in allowed:
            raise BusinessError(
                "请在下方的确认框中输入快照编号（或「回退」）以确认本次回退",
                code=400,
                status_code=400,
            )
        if snapshot.status == "building":
            raise BusinessError("该快照仍在创建中，暂不可回退", code=409, status_code=409)
        if snapshot.status == "failed":
            raise BusinessError("该快照创建失败，不可用于回退", code=409, status_code=409)
        if gate.is_paused():
            mode = gate.load_state()["mode"]
            raise BusinessError(
                f"系统当前处于{('回退中' if mode == gate.MODE_RESTORING else '暂停')}状态，"
                "请先恢复运行或等待当前操作结束",
                code=409,
                status_code=409,
            )

        include_users = _truthy(body.get("includeUsers"), False)
        restore_global = _truthy(body.get("restoreGlobal"), False)
        restore_files = _truthy(body.get("restoreFiles"), False)
        skip_safety = _truthy(body.get("skipSafetySnapshot"), False)
        verify = _truthy(body.get("verify"), True)
        ttl = max(60, _as_int(body.get("ttlSeconds"), DEFAULT_RESTORE_TTL))
        reason = str(
            body.get("reason")
            or f"管理员正在执行数据回退（快照 #{snapshot.id} {snapshot.label}）"
        ).strip()[:255]

        operator = request.user
        result: dict = {}
        try:
            self._run_restore(
                request=request,
                snapshot=snapshot,
                operator=operator,
                reason=reason,
                ttl=ttl,
                include_users=include_users,
                restore_global=restore_global,
                restore_files=restore_files,
                skip_safety=skip_safety,
                verify=verify,
                result=result,
            )
        except gate.GateBusy as exc:
            # 并发独占操作 / 排空超时：明确 409，且数据未被修改
            raise BusinessError(str(exc), code=409, status_code=409) from exc

        _audit(
            request,
            "snapshot:restore",
            snapshot_id=snapshot.id,
            competition_id=snapshot.competition_id,
            changes={
                "label": snapshot.label,
                "deletedRows": result.get("deletedRows"),
                "insertedRows": result.get("insertedRows"),
                "dataVersion": result.get("dataVersion"),
                "includeUsers": include_users,
                "restoreGlobal": restore_global,
                "restoreFiles": restore_files,
                "safetySnapshotId": result.get("safetySnapshotId"),
            },
        )
        return Response(result)

    def _run_restore(
        self,
        *,
        request,
        snapshot: Snapshot,
        operator,
        reason: str,
        ttl: int,
        include_users: bool,
        restore_global: bool,
        restore_files: bool,
        skip_safety: bool,
        verify: bool,
        result: dict,
    ) -> None:
        """在「强制暂停 + 排空」的独占窗口内完成回退。"""
        with gate.exclusive_window(
            mode=gate.MODE_RESTORING,
            reason=reason,
            message="系统正在回退数据，请稍候；回退完成后页面会自动同步。",
            ttl_seconds=ttl,
            snapshot_id=snapshot.id,
            operator_id=getattr(operator, "id", None),
            operator_name=_operator_name(request),
            drain_timeout=float(getattr(settings, "SNAPSHOT_DRAIN_TIMEOUT", 10.0)),
            resume_reason="数据回退完成，系统已恢复",
        ):
            safety = None
            if not skip_safety:
                gate.set_progress("正在创建回退前安全快照…", snapshot_id=snapshot.id)
                try:
                    safety = engine.create_snapshot(
                        label=f"回退前自动快照（目标 #{snapshot.id}）",
                        note=f"回退到快照 #{snapshot.id}「{snapshot.label}」之前的数据状态",
                        kind="pre-restore",
                        competition_id=snapshot.competition_id,
                        include_files=False,
                        operator=operator,
                        progress=gate.set_progress,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("回退前安全快照创建失败：%s", exc, exc_info=True)
                    raise BusinessError(
                        f"回退前安全快照创建失败，已中止回退（数据未变化）：{exc}",
                        code=500,
                        status_code=500,
                    ) from exc

            gate.set_progress("正在回退数据…", snapshot_id=snapshot.id)
            outcome = engine.restore_snapshot(
                snapshot,
                operator=operator,
                include_users=include_users,
                restore_global=restore_global,
                restore_files=restore_files,
                verify=verify,
                progress=gate.set_progress,
            )
            outcome["safetySnapshotId"] = safety.id if safety else None
            outcome["operatorName"] = _operator_name(request)
            result.update(outcome)
            # 先播「回退完成」（客户端此时仍处于遮罩态，切到「正在同步」），
            # 再由 exclusive_window 退出时播 system:resumed 携带新的 dataVersion。
            from apps.realtime.emit import emit_system_restored

            emit_system_restored(outcome)
