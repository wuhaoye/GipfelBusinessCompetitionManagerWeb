# -*- coding: utf-8 -*-
"""contract_watcher 的本地配置（JSON 文件 · GUI 与命令行共用）。

位置：默认 `contract_watcher/config.json`（`--config` 可换）。首次运行时若不存在，
按 `default_config()` 生成一份（不含密码）。

安全说明
--------
`password` 字段**默认不写盘**：只有 GUI 勾选「记住密码」时才把它明文存进本文件
（本地单机工具的便利性取舍）。不勾选时，GUI 每次启动都要重新输入密码。
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_NAME = "config.json"

DEFAULTS: dict = {
    # ---- 后端连接 ----
    "server": "http://127.0.0.1:8000",
    "username": "",
    "password": "",
    "remember_password": False,
    "competition_id": None,          # None = 不筛选（超管跨比赛时由合同自带 competitionId）
    # ---- 记录范围 ----
    # null = 沿用本地已保存的选择（首次同步时默认全选「有管理权限」的公司）
    # []   = 一个都不选（只有手动重新勾选后才会写 xlsx）
    # [id] = 只记这些公司（会自动与公司管理范围 companyScopes 求交）
    "record_company_ids": None,
    # ---- 记账触发 ----
    "flush_threshold": 10,           # 同一公司未记账合同达到该条数即写入 xlsx
    "auto_bookkeeping": True,        # 关掉后只有手动请求才会写 xlsx
    "fiscal_year_flush": True,       # 财年结束/开始时自动结账
    "fiscal_year_interval": 60.0,    # 财年轮询最小间隔（秒）
    # ---- 角色门禁 ----
    "allow_player": False,           # PLAYER 默认关闭；显式打开才允许其使用本工具
    # ---- 账本 ----
    "books_dir": "",                 # 空 = contract_watcher/books
    "book_template": "",             # 空 = contract_watcher/bookkeeping_example/target.xlsx
    # ---- 其它 ----
    "interval": 3.0,                 # 合同轮询间隔（秒）
    "db_path": "",                   # 空 = contract_watcher/data/watcher.db
}


def config_path(watcher_dir) -> Path:
    return Path(watcher_dir) / CONFIG_NAME


def default_config() -> dict:
    return json.loads(json.dumps(DEFAULTS))  # 深拷贝，避免调用方改动全局默认


def load_config(path=None, watcher_dir=None) -> dict:
    """读配置；缺失/损坏时返回默认值（不抛错，避免 GUI 起不来）。"""
    cfg = default_config()
    if path is None:
        if watcher_dir is None:
            return cfg
        path = config_path(watcher_dir)
    path = Path(path)
    if not path.exists():
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS or k.startswith("_")})
    return cfg


def save_config(cfg: dict, path=None, watcher_dir=None, *, include_password=None) -> Path:
    """写配置；默认按 `remember_password` 决定是否落盘密码。"""
    if path is None:
        if watcher_dir is None:
            raise ValueError("save_config 需要 path 或 watcher_dir")
        path = config_path(watcher_dir)
    path = Path(path)
    data = dict(cfg)
    keep_password = data.get("remember_password") if include_password is None else include_password
    if not keep_password:
        data["password"] = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    import os

    os.replace(tmp, path)
    return path
