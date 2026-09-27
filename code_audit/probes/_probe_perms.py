# -*- coding: utf-8 -*-
"""只读探针 3：权限授予上限 / 角色可达性（纯函数，不连库）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django  # noqa: E402

django.setup()

from apps.common.permissions import (  # noqa: E402
    BASE_VIEW_PERMISSIONS,
    ROLE_TEMPLATES,
    SUPER_ADMIN_ONLY_PERMISSIONS,
    assert_grant_allowed,
    has_permission,
)

print("== 授予校验（actor=SUPER_ADMIN） ==")
cases = [
    ("COMPETITION_ADMIN", ["contract:manage"]),
    ("COMPETITION_ADMIN", ["company:manage"]),        # 扩展集
    ("COMPETITION_ADMIN", ["message:manage"]),        # 扩展集
    ("COMPETITION_ADMIN", ["industryType:manage"]),   # 扩展集
    ("COMPETITION_ADMIN", ["data:region:edit"]),      # 扩展集
    ("COMPETITION_ADMIN", ["contractType:manage"]),   # 扩展集
    ("COMPETITION_ADMIN", ["data:material:edit"]),
    ("PLAYER", ["stock:view"]),
    ("PLAYER", ["data:material:edit"]),
    ("PLAYER", ["stock:edit"]),
    ("SUPER_ADMIN", []),
]
for role, perms in cases:
    ok, v = assert_grant_allowed("SUPER_ADMIN", role, perms)
    print(f"  {role:18s} {str(perms):34s} -> allowed={ok}  {v}")

print()
print("== 默认角色实际能力 ==")
for role, tpl in ROLE_TEMPLATES.items():
    if role == "SUPER_ADMIN":
        continue
    print(f"  {role}: 默认权限 {len(tpl['defaultPermissions'])} 项")
    for key in ["data:material:edit", "contract:manage", "contract:execute", "contract:audit",
                "stock:edit", "stock:view", "company:manage", "account:manage"]:
        print(f"     {key:22s} -> {has_permission(role, tpl['defaultPermissions'], key)}")

print()
print("== 未登记 action 的 fail-open 检查（模拟拼错权限键） ==")
for key in ["contract:vieww", "contract:managex", "contract:"]:
    print(f"  PLAYER 请求 {key!r} -> {has_permission('PLAYER', BASE_VIEW_PERMISSIONS, key)}")

print()
print("== 超管专属清单 ==")
print(" ", SUPER_ADMIN_ONLY_PERMISSIONS)
