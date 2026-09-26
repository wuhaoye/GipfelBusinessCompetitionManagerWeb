"""创建/删除快照系统联调用的临时超管账号（仅本地验证使用）。

用法：
    cd backend
    .venv\\Scripts\\python.exe ..\\tests\\snapshot_tools\\make_e2e_admin.py create
    .venv\\Scripts\\python.exe ..\\tests\\snapshot_tools\\make_e2e_admin.py delete
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

from apps.users.models import User  # noqa: E402

USERNAME = os.environ.get("E2E_ADMIN_USER", "snap_e2e_admin")
PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "SnapE2E!2026")


def main() -> int:
    action = (sys.argv[1] if len(sys.argv) > 1 else "create").lower()
    if action == "delete":
        removed = User.objects.filter(username=USERNAME).delete()
        print(f"已删除临时账号 {USERNAME}：{removed}")
        return 0
    user = User.objects.filter(username=USERNAME).first()
    if user is None:
        user = User.objects.create_user(
            username=USERNAME,
            password=PASSWORD,
            role="SUPER_ADMIN",
            display_name="快照联调临时账号",
        )
        print(f"已创建临时超管 {USERNAME} / {PASSWORD}")
    else:
        user.set_password(PASSWORD)
        user.role = "SUPER_ADMIN"
        user.must_change_password = False
        user.is_active = True
        user.save(update_fields=["password_hash", "role", "must_change_password", "is_active", "updated_at"])
        print(f"已重置临时超管 {USERNAME} / {PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
