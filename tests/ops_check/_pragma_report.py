# -*- coding: utf-8 -*-
"""子进程助手：打印「当前环境变量下」Django 新建连接实际生效的 PRAGMA（JSON 一行）。

为什么要子进程：`DATABASES["default"]["OPTIONS"]` 与 `SQLITE_TUNING_ENABLED` 的联动
发生在 **settings 导入时**，用 override_settings 在当前进程里改容易被连接缓存干扰；
开新进程 + 只改环境变量，等价于「现场改 .env 后重启服务」的真实路径。

用法：OPS_CHECK_DB=<副本> SQLITE_TUNING_ENABLED=false python _pragma_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _probe_common as pc  # noqa: E402

pc.bootstrap()

from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402


def main() -> int:
    conn = connections["default"]
    conn.ensure_connection()

    def scalar(sql: str):
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
        return row[0] if row else None

    out = {
        "db": str(settings.DATABASES["default"]["NAME"]),
        "engine": settings.DATABASES["default"]["ENGINE"],
        "options": settings.DATABASES["default"].get("OPTIONS"),
        "sqlite_tuning_enabled": bool(getattr(settings, "SQLITE_TUNING_ENABLED", None)),
        "pragma_statements": list(getattr(settings, "SQLITE_PRAGMA_STATEMENTS", ())),
        "driver_timeout": conn.settings_dict.get("OPTIONS", {}).get("timeout"),
        "journal_mode": scalar("PRAGMA journal_mode"),
        "busy_timeout": scalar("PRAGMA busy_timeout"),
        "synchronous": scalar("PRAGMA synchronous"),
        "wal_autocheckpoint": scalar("PRAGMA wal_autocheckpoint"),
        "audit_http_error_mode": getattr(settings, "AUDIT_HTTP_ERROR_MODE", None),
        "audit_sample_rate": getattr(settings, "AUDIT_HTTP_ERROR_SAMPLE_RATE", None),
        "realtime_bus": getattr(settings, "REALTIME_BUS", None),
        "auth_me_conditional": getattr(settings, "AUTH_ME_CONDITIONAL_ENABLED", None),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
