"""快照归档读写层：把表数据落成 gzip JSONL，并可原样读回。

设计要点
--------
- **逐表一个文件**：`<snapshot_dir>/tables/<table>.jsonl.gz`。流式读写，内存占用与
  表大小无关；单表损坏不会影响其它表。
- **原样保存**：按 `concrete_fields` 的 attname 取值（外键存 `xxx_id` 整数），
  Decimal 存字符串、时间存 ISO 字符串、bytes 存 base64 —— 解码时按模型字段类型
  逆变换，保证「读出来什么样，写回去还是什么样」。
- **内容校验和**：以「按主键排序的规范化 JSON 行」为哈希输入，回退后重算比对，
  任何一位不一致都会让回退事务整体回滚。
- **原子落盘**：manifest 先写临时文件再 `os.replace`，避免半截文件被当成可用快照。
"""
from __future__ import annotations

import base64
import datetime as _dt
import gzip
import hashlib
import json
import logging
import os
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from django.conf import settings
from django.db import models
from django.utils.dateparse import parse_date, parse_datetime, parse_time

from .registry import TableSpec

logger = logging.getLogger("gipfel")

MANIFEST_NAME = "manifest.json"
TABLES_DIR = "tables"
FILES_DIR = "files"
SCHEMA_VERSION = 1
GENERATOR = "gipfel-snapshot"

#: 单表读取/写入的分批大小
BATCH_SIZE = 500


# ====================================================================
# 路径
# ====================================================================
def snapshots_root() -> Path:
    root = Path(getattr(settings, "SNAPSHOT_DIR", "")) if getattr(
        settings, "SNAPSHOT_DIR", ""
    ) else Path(settings.BASE_DIR) / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def snapshot_dir(snapshot_id: int) -> Path:
    return snapshots_root() / f"snap-{int(snapshot_id):06d}"


def table_file(snapshot_id: int, table: str) -> Path:
    return snapshot_dir(snapshot_id) / TABLES_DIR / f"{table}.jsonl.gz"


def files_dir(snapshot_id: int) -> Path:
    return snapshot_dir(snapshot_id) / FILES_DIR


def uploads_root() -> Path:
    return Path(getattr(settings, "UPLOAD_DIR", Path(settings.BASE_DIR) / "uploads"))


# ====================================================================
# 取值 / 还原
# ====================================================================
def encode_value(field: models.Field, value: Any) -> Any:
    """把字段值转成 JSON 可序列化形态。"""
    if value is None:
        return None
    if isinstance(field, models.DecimalField):
        return str(value)
    if isinstance(field, models.DateTimeField):
        return value.isoformat() if isinstance(value, _dt.datetime) else str(value)
    if isinstance(field, models.DateField) and not isinstance(field, models.DateTimeField):
        return value.isoformat() if isinstance(value, _dt.date) else str(value)
    if isinstance(field, models.TimeField):
        return value.isoformat() if isinstance(value, _dt.time) else str(value)
    if isinstance(field, models.BinaryField):
        raw = bytes(value)
        return base64.b64encode(raw).decode("ascii")
    if isinstance(field, models.UUIDField):
        return str(value)
    if isinstance(field, models.BooleanField):
        return bool(value)
    if isinstance(value, (int, float, str, bool, list, dict)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    # 兜底：未知类型按字符串保存（解码时字段类型会尽力还原）
    logger.debug("快照：字段 %s 的值类型 %s 未显式支持，按字符串保存", field.name, type(value))
    return str(value)


def decode_value(field: models.Field, raw: Any) -> Any:
    """把快照里的值还原成可写库的 Python 值（与 encode_value 逆运算）。"""
    if raw is None:
        return None
    if isinstance(field, models.DecimalField):
        return Decimal(str(raw))
    if isinstance(field, models.DateTimeField):
        if isinstance(raw, str):
            parsed = parse_datetime(raw)
            if parsed is None:
                raise ValueError(f"{field.name}: 非法时间值 {raw!r}")
            return parsed
        return raw
    if isinstance(field, models.DateField):
        if isinstance(raw, str):
            parsed = parse_date(raw)
            if parsed is None:
                raise ValueError(f"{field.name}: 非法日期值 {raw!r}")
            return parsed
        return raw
    if isinstance(field, models.TimeField):
        if isinstance(raw, str):
            parsed = parse_time(raw)
            if parsed is None:
                raise ValueError(f"{field.name}: 非法时间值 {raw!r}")
            return parsed
        return raw
    if isinstance(field, models.BinaryField):
        if isinstance(raw, str):
            return base64.b64decode(raw.encode("ascii"))
        return raw
    if isinstance(field, models.BooleanField):
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if isinstance(field, models.IntegerField) and isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return raw
    if isinstance(field, models.FloatField) and isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def encode_row(spec: TableSpec, row: dict) -> dict:
    """把 `.values()` 出来的行（键为 attname）转成快照行。"""
    fields = {f.attname: f for f in spec.model._meta.concrete_fields}
    out: dict[str, Any] = {}
    for key, value in row.items():
        f = fields.get(key)
        out[key] = encode_value(f, value) if f is not None else value
    return out


def decode_row(spec: TableSpec, raw: dict) -> dict:
    """把快照行还原为 `bulk_create` 可用的 kwargs（键为 attname）。"""
    fields = {f.attname: f for f in spec.model._meta.concrete_fields}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        f = fields.get(key)
        if f is None:
            # 归档里存在、当前模型已删除的列：丢弃（前向兼容）
            logger.debug("快照：模型 %s 已无字段 %s，忽略该列", spec.label, key)
            continue
        out[key] = decode_value(f, value)
    return out


def row_json(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ====================================================================
# 写表
# ====================================================================
def write_table(
    spec: TableSpec,
    queryset,
    path: Path,
    *,
    on_batch: Callable[[int], None] | None = None,
) -> tuple[int, int, str]:
    """把 queryset 的每一行写成 gzip JSONL。返回 (行数, 文件字节数, sha256)。

    sha256 针对**未压缩的规范化 JSON 内容**计算，回退后可用同样的算法重算比对。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    digest = hashlib.sha256()
    rows = 0
    pk = spec.pk_attname
    qs = queryset.values().order_by(pk)
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6, newline="\n") as fh:
        for raw in qs.iterator(chunk_size=BATCH_SIZE):
            line = row_json(encode_row(spec, raw))
            fh.write(line)
            fh.write("\n")
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
            rows += 1
            if on_batch is not None and rows % BATCH_SIZE == 0:
                on_batch(rows)
    os.replace(tmp, path)
    return rows, path.stat().st_size, digest.hexdigest()


# ====================================================================
# 读表
# ====================================================================
def iter_rows(path: Path) -> Iterator[dict]:
    """流式读取快照表文件，逐行 yield dict。"""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:  # pragma: no cover - 归档损坏
                raise ValueError(f"快照文件 {path.name} 第 {lineno} 行不是合法 JSON：{exc}") from exc


def read_table(path: Path, spec: TableSpec) -> list[dict]:
    """读取整表为 `bulk_create` kwargs 列表（仅在需要一次性载入时使用）。"""
    return [decode_row(spec, raw) for raw in iter_rows(path)]


def table_checksum(path: Path) -> tuple[int, str]:
    """重算某表文件的 (行数, sha256)，用于回退后的一致性校验。"""
    digest = hashlib.sha256()
    rows = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
            rows += 1
    return rows, digest.hexdigest()


def db_table_checksum(spec: TableSpec, queryset) -> tuple[int, str]:
    """按与 write_table 完全相同的规范化算法，计算**库中当前行**的 (行数, sha256)。"""
    digest = hashlib.sha256()
    rows = 0
    qs = queryset.values().order_by(spec.pk_attname)
    for raw in qs.iterator(chunk_size=BATCH_SIZE):
        line = row_json(encode_row(spec, raw))
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
        rows += 1
    return rows, digest.hexdigest()


# ====================================================================
# manifest
# ====================================================================
def write_manifest(directory: Path, payload: dict) -> str:
    """原子写入 manifest.json，返回其 sha256。"""
    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = directory / (MANIFEST_NAME + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    final = directory / MANIFEST_NAME
    os.replace(tmp, final)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_manifest(directory: Path) -> dict:
    path = directory / MANIFEST_NAME
    if not path.exists():
        raise FileNotFoundError(f"快照归档缺少 {MANIFEST_NAME}：{directory}")
    return json.loads(path.read_text(encoding="utf-8"))


# ====================================================================
# 上传文件归档
# ====================================================================
def archive_files(dest: Path) -> tuple[int, int, list[str]]:
    """把 uploads 目录整体归档到快照目录（优先硬链接，跨盘时退回复制）。

    返回 (文件数, 总字节, 问题列表)。
    """
    src = uploads_root()
    problems: list[str] = []
    count = 0
    total = 0
    if not src.exists():
        return 0, 0, problems
    max_bytes = int(getattr(settings, "SNAPSHOT_MAX_FILE_BYTES", 2 * 1024 * 1024 * 1024))
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        size = path.stat().st_size
        if total + size > max_bytes:
            problems.append(
                f"上传文件总量超过上限 {max_bytes} 字节，剩余文件未归档（{rel} 起）"
            )
            break
        try:
            if target.exists():
                target.unlink()
            try:
                os.link(path, target)
            except OSError:
                shutil.copy2(path, target)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rel}: {exc}")
            continue
        count += 1
        total += size
    return count, total, problems


def restore_files(src: Path) -> tuple[int, int, list[str]]:
    """把快照里的上传文件写回 uploads 目录（覆盖同名文件，不删除新增文件）。"""
    dest = uploads_root()
    problems: list[str] = []
    count = 0
    total = 0
    if not src.exists():
        return 0, 0, problems
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rel}: {exc}")
            continue
        count += 1
        total += target.stat().st_size
    return count, total, problems


def remove_snapshot_dir(snapshot_id: int) -> None:
    directory = snapshot_dir(snapshot_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)


def dir_size(directory: Path) -> int:
    if not directory.exists():
        return 0
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:  # pragma: no cover
            continue
    return total


def chunks(seq: Iterable, size: int = BATCH_SIZE) -> Iterator[list]:
    buf: list = []
    for item in seq:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
