# -*- coding: utf-8 -*-
"""contract_watcher 的 SQLite 存储层（按公司 companyId 分账 · 先入库后记账）。

职责
----
1. **先进入 SQLite**：合同通过后只把数据写进本库（按 company_id 分行），
   **不碰 Excel**。这样每份合同只产生一次本地写入，而不是一次 COM 会话。
2. **公司目录与选择**：记录后端的公司、是否在账号的「公司管理范围」（manageable）、
   是否被选为本机「记账目标」（selected）。
3. **记账批次**：每次真正写 xlsx 记为一个批次（threshold / fiscal_year_end /
   fiscal_year_start / manual），并记录该批次包含哪些合同，便于对账与失败重试。
4. **手动记账请求**：GUI 只往 `flush_requests` 插一行，由**同一线程**的监听循环消费，
   保证 Excel/COM 全程单线程、不并发。
5. **财年状态**：缓存后端财年，用本地前后两次状态推导 FY_START / FY_END。
6. **meta / events**：增量游标（合同、财年）与运行事件日志。

并发
----
默认 WAL 模式 + `busy_timeout`：GUI（主线程）与监听循环（工作线程）各持一个连接，
读读、读写可并发；写写由 SQLite 串行化并等待超时。**没有连接跨线程共享**。

时间
----
所有时间列存 UTC ISO 字符串（与后端契约一致），展示时再本地化。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# 未记账标记：booked_at IS NULL
_PENDING = "booked_at IS NULL"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    company_id     INTEGER PRIMARY KEY,
    competition_id INTEGER,
    name           TEXT,
    manageable     INTEGER NOT NULL DEFAULT 0,
    selected       INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS contracts (
    company_id      INTEGER NOT NULL,
    contract_id     INTEGER NOT NULL,
    competition_id  INTEGER,
    type_key        TEXT,
    type_name       TEXT,
    name            TEXT,
    contract_number TEXT,
    party_role      TEXT,
    status          TEXT,
    executed_at     TEXT,
    amount          TEXT,
    payload_json    TEXT,
    readable_json   TEXT,
    first_seen_at   TEXT,
    booked_at       TEXT,
    batch_id        INTEGER,
    book_note       TEXT,
    PRIMARY KEY (company_id, contract_id)
);
CREATE INDEX IF NOT EXISTS idx_contracts_pending  ON contracts (company_id, booked_at);
CREATE INDEX IF NOT EXISTS idx_contracts_executed ON contracts (executed_at, contract_id);
CREATE INDEX IF NOT EXISTS idx_contracts_company  ON contracts (company_id, executed_at);

CREATE TABLE IF NOT EXISTS batches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     INTEGER NOT NULL,
    competition_id INTEGER,
    trigger        TEXT    NOT NULL,
    requested_by   TEXT,
    contract_count INTEGER NOT NULL DEFAULT 0,
    entry_count    INTEGER NOT NULL DEFAULT 0,
    book_path      TEXT,
    status         TEXT    NOT NULL,
    message        TEXT,
    created_at     TEXT,
    started_at     TEXT,
    finished_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_batches_company ON batches (company_id, id);
CREATE INDEX IF NOT EXISTS idx_batches_status  ON batches (status, id);

CREATE TABLE IF NOT EXISTS batch_contracts (
    batch_id    INTEGER NOT NULL,
    company_id  INTEGER NOT NULL,
    contract_id INTEGER NOT NULL,
    PRIMARY KEY (batch_id, company_id, contract_id)
);

CREATE TABLE IF NOT EXISTS flush_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    competition_id INTEGER,
    company_id     INTEGER,
    trigger        TEXT NOT NULL DEFAULT 'manual',
    requested_by   TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT,
    handled_at     TEXT,
    message        TEXT
);
CREATE INDEX IF NOT EXISTS idx_flush_requests_status ON flush_requests (status, id);

CREATE TABLE IF NOT EXISTS fiscal_years (
    competition_id INTEGER NOT NULL,
    fiscal_year_id INTEGER NOT NULL,
    year           INTEGER,
    status         TEXT,
    updated_at     TEXT,
    synced_at      TEXT,
    PRIMARY KEY (competition_id, fiscal_year_id)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT,
    level       TEXT,
    kind        TEXT,
    company_id  INTEGER,
    contract_id INTEGER,
    message     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events (at, id);
"""


# ==================== 基础 ====================

def utc_now() -> str:
    """当前 UTC 时间（ISO，秒精度），与后端契约同形态。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_local_text(value: str | None) -> str:
    """UTC ISO → 本地时间文本（GUI 展示用）；无法解析时原样返回。"""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path) -> sqlite3.Connection:
    """打开（必要时创建）数据库；返回已设好 WAL / busy_timeout 的连接。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError:
        # 只读介质 / 文件被锁等：仍然返回连接，由调用方决定是否降级
        pass
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    conn.executescript(SCHEMA)
    meta_set(conn, "schemaVersion", str(SCHEMA_VERSION))


def open_db(db_path) -> sqlite3.Connection:
    conn = connect(db_path)
    init_db(conn)
    return conn


# ==================== meta ====================

def meta_get(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, "" if value is None else str(value), utc_now()),
    )
    conn.commit()


def meta_get_json(conn: sqlite3.Connection, key: str, default=None):
    raw = meta_get(conn, key)
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def meta_set_json(conn: sqlite3.Connection, key: str, value) -> None:
    meta_set(conn, key, json.dumps(value, ensure_ascii=False))


# ==================== 事件日志 ====================

def log_event(conn, kind: str, message: str, *, level: str = "INFO",
              company_id=None, contract_id=None) -> None:
    try:
        conn.execute(
            "INSERT INTO events (at, level, kind, company_id, contract_id, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (utc_now(), level, kind, company_id, contract_id, message),
        )
        conn.commit()
    except sqlite3.DatabaseError:
        pass


def recent_events(conn, limit: int = 200) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    )


# ==================== 公司 ====================

def upsert_company(conn, company_id: int, *, competition_id=None, name=None,
                   manageable=None, selected=None) -> None:
    now = utc_now()
    row = conn.execute(
        "SELECT company_id FROM companies WHERE company_id = ?", (int(company_id),)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO companies (company_id, competition_id, name, manageable, selected, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(company_id), competition_id, name,
                1 if manageable else 0,
                1 if selected else 0,
                now,
            ),
        )
    else:
        sets, params = ["updated_at = ?"], [now]
        if competition_id is not None:
            sets.append("competition_id = ?")
            params.append(competition_id)
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if manageable is not None:
            sets.append("manageable = ?")
            params.append(1 if manageable else 0)
        if selected is not None:
            sets.append("selected = ?")
            params.append(1 if selected else 0)
        params.append(int(company_id))
        conn.execute(f"UPDATE companies SET {', '.join(sets)} WHERE company_id = ?", params)
    conn.commit()


def sync_companies(conn, rows, manageable_ids=None, *, default_selected=True) -> dict:
    """用后端公司列表刷新本地目录。

    rows: [{'id': 1, 'name': '甲', 'competitionId': 189}, ...]（接口 camelCase）
    manageable_ids: 账号可管理的公司 id 集合（None = 全部可管理）

    返回 {'added': n, 'manageable': n}。
    `selected` 只在**新增公司**时按 default_selected 初始化，已存在的公司保留用户选择。
    """
    added = 0
    manageable = 0
    now = utc_now()
    for row in rows:
        try:
            cid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        name = row.get("name")
        comp = row.get("competitionId", row.get("competition_id"))
        is_manageable = True if manageable_ids is None else (cid in manageable_ids)
        exists = conn.execute(
            "SELECT company_id FROM companies WHERE company_id = ?", (cid,)
        ).fetchone()
        if exists is None:
            conn.execute(
                "INSERT INTO companies (company_id, competition_id, name, manageable, selected, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, comp, name, 1 if is_manageable else 0,
                 1 if (is_manageable and default_selected) else 0, now),
            )
            added += 1
        else:
            conn.execute(
                "UPDATE companies SET competition_id = ?, name = ?, manageable = ?, updated_at = ? "
                "WHERE company_id = ?",
                (comp, name, 1 if is_manageable else 0, now, cid),
            )
        if is_manageable:
            manageable += 1
    conn.commit()
    return {"added": added, "manageable": manageable}


def set_company_selected(conn, company_id: int, selected: bool) -> None:
    conn.execute(
        "UPDATE companies SET selected = ?, updated_at = ? WHERE company_id = ?",
        (1 if selected else 0, utc_now(), int(company_id)),
    )
    conn.commit()


def set_selected_companies(conn, company_ids, selected: bool = True) -> int:
    ids = [int(i) for i in (company_ids or [])]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE companies SET selected = ?, updated_at = ? WHERE company_id IN ({marks})",
        [1 if selected else 0, utc_now()] + ids,
    )
    conn.commit()
    return cur.rowcount


def list_companies(conn, *, manageable_only: bool = False,
                   selected_only: bool = False) -> list[sqlite3.Row]:
    where = []
    if manageable_only:
        where.append("manageable = 1")
    if selected_only:
        where.append("selected = 1")
    sql = "SELECT * FROM companies"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY competition_id, company_id"
    return list(conn.execute(sql).fetchall())


def get_company(conn, company_id: int):
    return conn.execute(
        "SELECT * FROM companies WHERE company_id = ?", (int(company_id),)
    ).fetchone()


def manageable_ids(conn) -> list[int]:
    return [
        r["company_id"]
        for r in conn.execute(
            "SELECT company_id FROM companies WHERE manageable = 1 ORDER BY company_id"
        ).fetchall()
    ]


def selected_ids(conn) -> list[int]:
    return [
        r["company_id"]
        for r in conn.execute(
            "SELECT company_id FROM companies WHERE manageable = 1 AND selected = 1 "
            "ORDER BY company_id"
        ).fetchall()
    ]


# ==================== 合同 ====================

def extract_contract_meta(contract: dict, company_id: int) -> dict:
    """从合同 payload 里取出与「该公司」相关的一行元数据（金额/编号/角色）。"""
    parties = contract.get("parties") or []
    number, role = None, None
    for p in parties:
        if not isinstance(p, dict):
            continue
        try:
            pid = int(p.get("companyId"))
        except (TypeError, ValueError):
            continue
        if pid == int(company_id):
            number = p.get("contractNumber")
            role = p.get("role")
            break
    inputs = contract.get("inputs") or {}
    amount = inputs.get("amount") if isinstance(inputs, dict) else None
    ct = contract.get("contractType") or {}
    return {
        "competition_id": contract.get("competitionId"),
        "type_key": ct.get("key"),
        "type_name": ct.get("name"),
        "name": contract.get("name"),
        "contract_number": number,
        "party_role": role,
        "status": contract.get("status"),
        "executed_at": contract.get("executedAt"),
        "amount": None if amount is None else str(amount),
    }


def upsert_contract(conn, contract: dict, company_id: int, *, readable=None) -> bool:
    """按 (company_id, contract_id) 幂等入库；返回是否**新增**了一行。

    - 重复入库不覆盖已记账标记（booked_at / batch_id）；
    - payload_json 每次刷新（合同详情可能在执行后仍有更新）。
    """
    try:
        contract_id = int(contract.get("id"))
    except (TypeError, ValueError):
        raise ValueError("合同缺少有效 id，无法入库")
    company_id = int(company_id)
    meta = extract_contract_meta(contract, company_id)
    payload = json.dumps(contract, ensure_ascii=False)
    if readable is None:
        readable_text = None
    elif isinstance(readable, str):
        readable_text = readable
    else:
        readable_text = json.dumps(readable, ensure_ascii=False)

    exists = conn.execute(
        "SELECT 1 FROM contracts WHERE company_id = ? AND contract_id = ?",
        (company_id, contract_id),
    ).fetchone()
    if exists:
        conn.execute(
            "UPDATE contracts SET competition_id = ?, type_key = ?, type_name = ?, name = ?, "
            "contract_number = ?, party_role = ?, status = ?, executed_at = ?, amount = ?, "
            "payload_json = ?, readable_json = COALESCE(?, readable_json) "
            "WHERE company_id = ? AND contract_id = ?",
            (
                meta["competition_id"], meta["type_key"], meta["type_name"], meta["name"],
                meta["contract_number"], meta["party_role"], meta["status"],
                meta["executed_at"], meta["amount"], payload, readable_text,
                company_id, contract_id,
            ),
        )
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO contracts (company_id, contract_id, competition_id, type_key, type_name, "
        "name, contract_number, party_role, status, executed_at, amount, payload_json, "
        "readable_json, first_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            company_id, contract_id, meta["competition_id"], meta["type_key"], meta["type_name"],
            meta["name"], meta["contract_number"], meta["party_role"], meta["status"],
            meta["executed_at"], meta["amount"], payload, readable_text, utc_now(),
        ),
    )
    conn.commit()
    return True


def contract_exists(conn, company_id: int, contract_id: int) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM contracts WHERE company_id = ? AND contract_id = ?",
            (int(company_id), int(contract_id)),
        ).fetchone()
        is not None
    )


def pending_contracts(conn, company_id: int, limit: int | None = None) -> list[sqlite3.Row]:
    """该公司尚未记账的合同（按执行时间、合同 id 升序）。"""
    sql = (
        "SELECT * FROM contracts WHERE company_id = ? AND " + _PENDING +
        " ORDER BY COALESCE(executed_at, first_seen_at), contract_id"
    )
    params: list = [int(company_id)]
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return list(conn.execute(sql, params).fetchall())


def pending_count(conn, company_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE company_id = ? AND " + _PENDING,
        (int(company_id),),
    ).fetchone()
    return int(row["n"]) if row else 0


def pending_counts(conn) -> dict[int, int]:
    rows = conn.execute(
        "SELECT company_id, COUNT(*) AS n FROM contracts WHERE " + _PENDING +
        " GROUP BY company_id"
    ).fetchall()
    return {int(r["company_id"]): int(r["n"]) for r in rows}


def booked_count(conn, company_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE company_id = ? AND booked_at IS NOT NULL",
        (int(company_id),),
    ).fetchone()
    return int(row["n"]) if row else 0


def company_contracts(conn, company_id: int, *, limit: int = 500, offset: int = 0,
                      only_pending: bool = False, keyword: str | None = None,
                      order: str = "desc") -> list[sqlite3.Row]:
    """某公司的合同（GUI 查看用）；默认新→旧。"""
    where = ["company_id = ?"]
    params: list = [int(company_id)]
    if only_pending:
        where.append(_PENDING)
    if keyword:
        where.append("(name LIKE ? OR contract_number LIKE ? OR type_key LIKE ? OR type_name LIKE ?)")
        like = f"%{keyword}%"
        params += [like, like, like, like]
    direction = "DESC" if str(order).lower() != "asc" else "ASC"
    sql = (
        f"SELECT * FROM contracts WHERE {' AND '.join(where)} "
        f"ORDER BY COALESCE(executed_at, first_seen_at) {direction}, contract_id {direction} "
        "LIMIT ? OFFSET ?"
    )
    params += [int(limit), int(offset)]
    return list(conn.execute(sql, params).fetchall())


def contract_rows_by_ids(conn, company_id: int, contract_ids) -> list[sqlite3.Row]:
    ids = [int(i) for i in (contract_ids or [])]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    return list(
        conn.execute(
            f"SELECT * FROM contracts WHERE company_id = ? AND contract_id IN ({marks})",
            [int(company_id)] + ids,
        ).fetchall()
    )


# ==================== 记账批次 ====================

def create_batch(conn, company_id: int, *, competition_id=None, trigger="manual",
                 requested_by=None, contract_ids=(), book_path=None) -> int:
    """建批次并登记合同（status=pending）。返回 batch id。"""
    ids = [int(i) for i in (contract_ids or [])]
    cur = conn.execute(
        "INSERT INTO batches (company_id, competition_id, trigger, requested_by, "
        "contract_count, entry_count, book_path, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, 'pending', ?)",
        (int(company_id), competition_id, trigger, requested_by, len(ids), book_path, utc_now()),
    )
    batch_id = int(cur.lastrowid)
    for cid in ids:
        conn.execute(
            "INSERT OR IGNORE INTO batch_contracts (batch_id, company_id, contract_id) VALUES (?, ?, ?)",
            (batch_id, int(company_id), cid),
        )
    conn.commit()
    return batch_id


def mark_batch_running(conn, batch_id: int) -> None:
    conn.execute(
        "UPDATE batches SET status = 'running', started_at = ? WHERE id = ?",
        (utc_now(), int(batch_id)),
    )
    conn.commit()


def finish_batch(conn, batch_id: int, *, status: str, entry_count: int | None = None,
                 message: str | None = None, book_path: str | None = None) -> None:
    sets = ["status = ?", "finished_at = ?"]
    params: list = [status, utc_now()]
    if entry_count is not None:
        sets.append("entry_count = ?")
        params.append(int(entry_count))
    if message is not None:
        sets.append("message = ?")
        params.append(str(message)[:2000])
    if book_path is not None:
        sets.append("book_path = ?")
        params.append(str(book_path))
    params.append(int(batch_id))
    conn.execute(f"UPDATE batches SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def mark_contracts_booked(conn, company_id: int, contract_ids, batch_id: int,
                          note: str | None = None) -> int:
    ids = [int(i) for i in (contract_ids or [])]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE contracts SET booked_at = ?, batch_id = ?, book_note = ? "
        f"WHERE company_id = ? AND contract_id IN ({marks})",
        [utc_now(), int(batch_id), note, int(company_id)] + ids,
    )
    conn.commit()
    return cur.rowcount


def unbook_contracts(conn, company_id: int, contract_ids) -> int:
    """撤销记账标记（人工复核后重试用）。"""
    ids = [int(i) for i in (contract_ids or [])]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE contracts SET booked_at = NULL, batch_id = NULL, book_note = NULL "
        f"WHERE company_id = ? AND contract_id IN ({marks})",
        [int(company_id)] + ids,
    )
    conn.commit()
    return cur.rowcount


def batch_contract_ids(conn, batch_id: int) -> list[int]:
    return [
        int(r["contract_id"])
        for r in conn.execute(
            "SELECT contract_id FROM batch_contracts WHERE batch_id = ? ORDER BY contract_id",
            (int(batch_id),),
        ).fetchall()
    ]


def running_batches(conn) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM batches WHERE status = 'running' ORDER BY id").fetchall()
    )


def recent_batches(conn, company_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if company_id is None:
        return list(
            conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        )
    return list(
        conn.execute(
            "SELECT * FROM batches WHERE company_id = ? ORDER BY id DESC LIMIT ?",
            (int(company_id), int(limit)),
        ).fetchall()
    )


def last_batch(conn, company_id: int):
    return conn.execute(
        "SELECT * FROM batches WHERE company_id = ? ORDER BY id DESC LIMIT 1",
        (int(company_id),),
    ).fetchone()


# ==================== 手动记账请求 ====================

def request_flush(conn, *, company_id=None, competition_id=None, trigger="manual",
                  requested_by="gui") -> int:
    cur = conn.execute(
        "INSERT INTO flush_requests (competition_id, company_id, trigger, requested_by, "
        "status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (competition_id, company_id, trigger, requested_by, utc_now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def pending_flush_requests(conn) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM flush_requests WHERE status = 'pending' ORDER BY id"
        ).fetchall()
    )


def finish_flush_request(conn, request_id: int, status: str, message: str = "") -> None:
    conn.execute(
        "UPDATE flush_requests SET status = ?, handled_at = ?, message = ? WHERE id = ?",
        (status, utc_now(), str(message)[:2000], int(request_id)),
    )
    conn.commit()


# ==================== 财年 ====================

def _fy_rows(conn, competition_id: int) -> dict[int, sqlite3.Row]:
    return {
        int(r["fiscal_year_id"]): r
        for r in conn.execute(
            "SELECT * FROM fiscal_years WHERE competition_id = ?", (int(competition_id),)
        ).fetchall()
    }


def fy_baseline_done(conn, competition_id: int) -> bool:
    return meta_get(conn, f"fyBaseline:{int(competition_id)}") == "1"


def sync_fiscal_years(conn, competition_id: int, rows, *, existing_ids=None) -> list[dict]:
    """用后端财年列表刷新本地缓存，并返回**本次检测到的更迭**。

    rows: [{'id':.., 'year':.., 'status':.., 'updatedAt':..}, ...]（接口 camelCase）
    existing_ids: 增量响应里的 existingIds（用于识别「被删除的财年」）；None 表示本次是全量列表。

    返回 [{'transition': 'FY_START'|'FY_END', 'reason': ..., 'year': .., 'fiscal_year_id': ..}]

    首次同步（无基线）只落状态、不报更迭 —— 否则刚启动就会把「当前进行中的财年」
    误判成 FY_START 而触发一次空的 Excel 会话。
    """
    competition_id = int(competition_id)
    first_time = not fy_baseline_done(conn, competition_id)
    local = _fy_rows(conn, competition_id)
    transitions: list[dict] = []
    now = utc_now()
    seen: set[int] = set()

    for row in rows or []:
        try:
            fy_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        year = row.get("year")
        status = row.get("status")
        updated = row.get("updatedAt") or row.get("updated_at")
        old = local.get(fy_id)
        if not first_time:
            prev_status = old["status"] if old is not None else None
            if status == "CLOSED" and prev_status != "CLOSED":
                transitions.append({
                    "transition": "FY_END",
                    "reason": "财年结束" if old is not None else "发现已结束的财年",
                    "year": year,
                    "fiscal_year_id": fy_id,
                })
            elif status == "ACTIVE" and prev_status != "ACTIVE":
                transitions.append({
                    "transition": "FY_START",
                    "reason": "财年开始",
                    "year": year,
                    "fiscal_year_id": fy_id,
                })
        conn.execute(
            "INSERT INTO fiscal_years (competition_id, fiscal_year_id, year, status, updated_at, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(competition_id, fiscal_year_id) DO UPDATE SET "
            "year = excluded.year, status = excluded.status, "
            "updated_at = excluded.updated_at, synced_at = excluded.synced_at",
            (competition_id, fy_id, year, status, updated, now),
        )
        seen.add(fy_id)

    removed = [fid for fid in local if fid not in seen]
    if existing_ids is not None:
        current = {int(i) for i in existing_ids if str(i).strip().lstrip("-").isdigit()}
        removed = [fid for fid in removed if fid not in current]
    elif rows is not None and len(rows) == 0 and local:
        # 全量列表为空 ⇒ 本地全部已删除
        removed = list(local)

    for fid in removed:
        old = local[fid]
        if not first_time and old["status"] == "ACTIVE":
            transitions.append({
                "transition": "FY_END",
                "reason": "进行中的财年被删除",
                "year": old["year"],
                "fiscal_year_id": fid,
            })
        conn.execute(
            "DELETE FROM fiscal_years WHERE competition_id = ? AND fiscal_year_id = ?",
            (competition_id, fid),
        )

    conn.commit()
    if first_time:
        meta_set(conn, f"fyBaseline:{competition_id}", "1")
    return transitions


def fiscal_years(conn, competition_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM fiscal_years WHERE competition_id = ? ORDER BY year",
            (int(competition_id),),
        ).fetchall()
    )


def current_fiscal_year(conn, competition_id: int) -> int | None:
    """当前进行中（ACTIVE）的财年年份；没有则返回 None（供界面显示）。"""
    row = conn.execute(
        "SELECT year FROM fiscal_years WHERE competition_id = ? AND status = 'ACTIVE' "
        "ORDER BY year DESC LIMIT 1",
        (int(competition_id),),
    ).fetchone()
    return int(row["year"]) if row and row["year"] is not None else None


# ==================== 统计概览（GUI 用） ====================

def company_overview(conn) -> list[dict]:
    """公司 + 待记账数 + 最近批次，供 GUI 表格直接渲染。"""
    counts = pending_counts(conn)
    out: list[dict] = []
    for row in list_companies(conn):
        cid = int(row["company_id"])
        last = last_batch(conn, cid)
        out.append({
            "company_id": cid,
            "competition_id": row["competition_id"],
            "name": row["name"],
            "manageable": bool(row["manageable"]),
            "selected": bool(row["selected"]),
            "pending": counts.get(cid, 0),
            "booked": booked_count(conn, cid),
            "last_batch_at": (last["finished_at"] or last["created_at"]) if last else None,
            "last_batch_status": last["status"] if last else None,
        })
    return out
