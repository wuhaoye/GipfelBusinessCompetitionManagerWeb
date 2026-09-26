"""WSGI 入口（仅同步 HTTP，WebSocket 由 ASGI 处理）。

【C1-a 整改】生产部署改为「WSGI 承载 REST + daphne 承载 Socket.IO」双进程：
    · gunicorn（`deploy/gipfel-wsgi.service`）→ 本模块 `backend.wsgi:application`
    · daphne（`deploy/gipfel.service`）      → `backend.asgi:application`（仅 /socket.io/）
本文件**只做** P01 中「多 worker WSGI 入口」这一件事，不要在这里 import apps.realtime
或 asgi 模块（WSGI 进程不需要 Socket.IO 服务端实例；import 会把 ASGI 栈也拉起来）。

⚠️ 修复记录（C1-a 实施期发现，改造前既有缺陷）：改前本文件第 4 行写的是
`from django.core.wsgi import get_asgi_application` —— `django.core.wsgi` 里**没有**这个
名字（ASGI 的同名函数在 `django.core.asgi`），于是 `WSGI_APPLICATION` 导入即 ImportError：
    · gunicorn `backend.wsgi:application` 启动失败（C1-a 的 REST 进程根本起不来）；
    · `manage.py runserver` 走 `get_internal_wsgi_application()` 同样起不来。
基线测试（`manage.py check` / `manage.py test`）不会导入 wsgi.py，所以一直没暴露。
回归保护见 `backend/tests_fix_verify/test_c1_wsgi_entry.py`（断言本入口可导入且是 WSGI callable）。
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
application = get_wsgi_application()
