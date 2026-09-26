# -*- coding: utf-8 -*-
"""C1-a 回归：WSGI 入口可用（gunicorn `backend.wsgi:application` / `manage.py runserver`）。

背景（实施期发现的改造前既有缺陷）：`backend/backend/wsgi.py` 曾写
`from django.core.wsgi import get_asgi_application` —— `django.core.wsgi` 里**没有**
这个名字（ASGI 的同名函数在 `django.core.asgi`），于是 `WSGI_APPLICATION` 一导入就
ImportError：C1-a 的 gunicorn REST 进程根本起不来，`manage.py runserver` 也起不来。
基线测试（`manage.py check` / `manage.py test`）都不导入 wsgi.py，所以长期未暴露。

本文件把「WSGI 入口可加载且是 WSGI callable」「ASGI 入口未被误伤」「WSGI 入口不得把
ASGI/Socket.IO 栈拖起来」钉成回归。
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: backend/backend/wsgi.py（本文件位于 backend/tests_fix_verify/）
WSGI_ENTRY = Path(__file__).resolve().parents[1] / "backend" / "wsgi.py"


class WsgiEntryTests(SimpleTestCase):
    def test_wsgi_application_can_be_loaded(self):
        """runserver / gunicorn 解析 `WSGI_APPLICATION` 的路径必须不再 ImportError。"""
        from django.core.servers.basehttp import get_internal_wsgi_application

        application = get_internal_wsgi_application()
        self.assertTrue(callable(application), "WSGI_APPLICATION 必须可加载且可调用")

    def test_backend_wsgi_application_is_callable_and_not_asgi_app(self):
        from backend.asgi import ASGIApp
        from backend.wsgi import application

        self.assertEqual(settings.WSGI_APPLICATION, "backend.wsgi.application")
        self.assertTrue(callable(application), "backend.wsgi.application 必须是 WSGI callable")
        self.assertNotIsInstance(
            application, ASGIApp, "WSGI 入口不得是 ASGI 应用（进程分离的前提）"
        )

    def test_asgi_entry_still_serves_socketio(self):
        """ASGI 路径未被误伤：仍是带 /socket.io 分发的 ASGIApp。"""
        from backend.asgi import ASGIApp, application

        self.assertIsInstance(application, ASGIApp)
        self.assertTrue(callable(application))
        self.assertTrue(callable(application.socketio))
        self.assertTrue(callable(application.django))

    def test_wsgi_entry_does_not_pull_in_asgi_stack(self):
        """结构性守卫：WSGI 入口不得 import apps.realtime / socketio（避免 WSGI worker 被拖进 ASGI 栈）。"""
        import ast

        tree = ast.parse(WSGI_ENTRY.read_text(encoding="utf-8"))
        modules: list[str] = []
        symbols: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
                symbols += [alias.name for alias in node.names]
        self.assertNotIn("apps.realtime", modules, "WSGI 入口不得 import apps.realtime")
        self.assertFalse(
            any("socketio" in module for module in modules),
            f"WSGI 入口不得 import socketio：{modules}",
        )
        self.assertIn("django.core.wsgi", modules)
        self.assertIn("get_wsgi_application", symbols, "必须用 WSGI 版本入口")
        self.assertNotIn(
            "get_asgi_application", symbols, "不得把 ASGI 入口函数搬进 WSGI 入口"
        )

    def test_django_core_wsgi_has_no_get_asgi_application(self):
        """把「改前为什么必炸」钉住：`django.core.wsgi` 里没有 `get_asgi_application`。"""
        import django.core.wsgi as django_core_wsgi

        self.assertFalse(
            hasattr(django_core_wsgi, "get_asgi_application"),
            "django.core.wsgi 不应有 get_asgi_application（ASGI 版本在 django.core.asgi）",
        )
        import django.core.asgi as django_core_asgi

        self.assertTrue(hasattr(django_core_asgi, "get_asgi_application"))
