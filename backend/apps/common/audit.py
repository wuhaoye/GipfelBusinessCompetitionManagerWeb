"""审计日志。

- 写操作审计：通过 Django signals（post_save/post_delete）触发
- 异常上下文审计：exception_handler 调用 log_exception
- changes 脱敏（密码/令牌等字段）

C2 阶段 1「审计噪声治理」：
- 只对 `log_exception`（HTTP 4xx/5xx）降噪：按 settings.AUDIT_HTTP_ERROR_MODE 分流
  （all=改造前全部落库 / sampled=5xx 全量 + 4xx 采样 / off=只写日志）；
- `log_write`（业务写审计）**永远落库**，不读任何开关——这是红线：比赛过程的
  业务写留痕不能因为降噪而丢失（见 settings.py「审计噪声治理」段注释）。
"""
from __future__ import annotations

import json
import logging
import random
from typing import Any

from django.conf import settings

from apps.common.helpers import client_ip as _client_ip
from apps.common.json_util import dumps_json_safe

logger = logging.getLogger("gipfel")


# ==================== 脱敏 ====================
_SENSITIVE_KEYS = {
    "password",
    "passwordhash",
    "password_hash",  # User 模型真实字段名（bcrypt 哈希），不可明文入审计库
    "token",
    "authorization",
    "secret",
    "jwt",
    "accesstoken",
    "refreshtoken",
}


def sanitize_changes(data: Any) -> Any:
    """递归脱敏敏感字段（值替换为 ***REDACTED***）。"""
    if isinstance(data, dict):
        return {
            k: (
                "***REDACTED***"
                if k.lower() in _SENSITIVE_KEYS
                else sanitize_changes(v)
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [sanitize_changes(x) for x in data]
    return data


# ==================== 审计写入 ====================
def log_write(
    *,
    model: str,
    action: str,
    record_id: str | None = None,
    changes: Any = None,
    competition_id: int | None = None,
) -> None:
    """写操作审计落库。

    **不受 AUDIT_HTTP_ERROR_MODE / 任何降噪开关影响：业务写审计永远落库。**
    （降噪只作用于 log_exception 的 HTTP 异常上下文。）
    """
    operator = get_current_operator_safe()
    try:
        from apps.audit.models import AuditLog
        from apps.common.logfilter import get_request_device, get_request_ip

        AuditLog.objects.create(
            kind="write",
            operator_id=operator.get("id") if operator else None,
            operator_name=operator.get("username") if operator else None,
            action=action,  # 如 "Material:create"
            model=model,
            record_id=str(record_id) if record_id is not None else None,
            competition_id=competition_id,
            changes=dumps_json_safe(sanitize_changes(changes), ensure_ascii=False)
            if changes is not None
            else None,
            ip=get_request_ip(),
            device=get_request_device(),
        )
    except Exception:  # noqa: BLE001 - 审计失败不阻断主流程
        logger.debug("写操作审计写入失败", exc_info=True)


def get_http_error_mode() -> str:
    """当前 HTTP 异常审计模式：all / sampled / off。

    缺键时回落 `all`：这是改造前的语义，保证「零配置 = 等价于改造前」。
    """
    raw = str(getattr(settings, "AUDIT_HTTP_ERROR_MODE", "all") or "all").strip().lower()
    return raw if raw in {"all", "sampled", "off"} else "all"


def get_http_error_sample_rate() -> float:
    """4xx 采样率（0.0–1.0），非法值回落 0.05（与 settings 默认一致）。"""
    try:
        rate = float(getattr(settings, "AUDIT_HTTP_ERROR_SAMPLE_RATE", 0.05))
    except (TypeError, ValueError):
        return 0.05
    return min(1.0, max(0.0, rate))


def should_persist_http_error(status_code: int) -> bool:
    """HTTP 异常是否落库（C2 降噪判据）。

    - off     ：一律不落库（只写 logger）
    - all     ：一律落库（改造前行为）
    - sampled ：5xx **全量**落库（服务端缺陷必须留痕）；4xx 按采样率落库
    - 其余（<400 的异常本不该走到这里）：保守落库

    线程安全：`random.random()` 在 CPython 内部自带锁，信号处理器/多线程视图共用
    模块级 Random 实例是安全的，无需再自建锁。
    """
    mode = get_http_error_mode()
    if mode == "off":
        return False
    if mode == "all":
        return True
    if status_code >= 500:
        return True
    if 400 <= status_code < 500:
        rate = get_http_error_sample_rate()
        if rate >= 1.0:
            return True
        if rate <= 0.0:
            return False
        return random.random() < rate
    return True


def log_exception(request, exc, response) -> None:
    """异常上下文审计（HTTP 4xx/5xx）：按 AUDIT_HTTP_ERROR_MODE 分流 + 始终写 logger。

    三种模式下都**始终**写一条 logger（method/path/status/operator + 是否落库），
    保证降噪后仍能靠日志排查（现场没有 ELK，日志即兜底证据）。
    日志失败绝不吞掉落库（反之亦然）：两部分各自独立兜异常。
    """
    # ① 判定（无论走哪条分支都要先算出上下文，供日志与落库共用）
    try:
        operator = get_current_operator_safe()
        status_code = getattr(response, "status_code", 500) if response else 500
        method = (getattr(request, "method", None) or "UNKNOWN") if request else "UNKNOWN"
        path = (getattr(request, "path", None) or "-") if request else "-"
        operator_name = (operator or {}).get("username") or "-"
        mode = get_http_error_mode()
        persist = should_persist_http_error(status_code)
    except Exception:  # noqa: BLE001 - 判据异常时按改造前语义保守落库
        operator, status_code, method, path, operator_name, mode, persist = (
            None,
            500,
            "UNKNOWN",
            "-",
            "-",
            "all",
            True,
        )

    # ② 始终写日志：5xx 记 error，4xx 记 warning（含 method/path/status/operator/mode）
    try:
        logger.log(
            logging.ERROR if status_code >= 500 else logging.WARNING,
            "HTTP 异常审计：%s %s → %s operator=%s mode=%s %s",
            method,
            path,
            status_code,
            operator_name,
            mode,
            "已落库" if persist else "仅日志（未落库）",
        )
    except Exception:  # noqa: BLE001 - 日志失败不影响落库
        pass

    if not persist:
        return

    # ③ 落库（采样命中 / all / 5xx）
    try:
        from apps.audit.models import AuditLog
        from apps.common.logfilter import get_request_device

        AuditLog.objects.create(
            kind="error",
            operator_id=operator.get("id") if operator else None,
            operator_name=operator.get("username") if operator else None,
            action=method,
            model=None,
            record_id=None,
            competition_id=operator.get("competitionId") if operator else None,
            changes=None,
            status_code=status_code,
            error_summary=str(exc)[:500],
            ip=_client_ip(request),
            device=get_request_device(),
            request_id=request.headers.get("X-Request-Id") if request else None,
        )
    except Exception:  # noqa: BLE001
        logger.debug("异常审计写入失败", exc_info=True)


def get_current_operator_safe() -> dict | None:
    try:
        from .middleware import get_current_operator

        return get_current_operator()
    except Exception:  # noqa: BLE001
        return None


