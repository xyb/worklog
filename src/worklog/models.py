"""Domain model layer: dataclasses that mirror the worklog DB schema.

One class per table. Design rules:
- Field names match DB column names exactly.
- ``from_row(row)`` constructs from a sqlite3.Row or any mapping.
- CRUD class methods wrap db_table helpers with the table name baked in.
  Reads: ``get`` / ``gets`` / ``query``.
  Writes: ``insert`` / ``update`` / ``upsert`` / ``delete`` / ``purge``.
  None of these commit; the caller owns the transaction.
- No business logic, no rendering.
- ``deleted_at`` is omitted — it is a storage implementation detail of the
  soft-delete system; models represent *live* domain objects.

View DTOs (what ``@text_renderer`` and ``JSONFormatter`` receive) live in
``commands/dtos.py``, centralised and importable from any command module.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import db_table as _db


@dataclass
class Node:
    """Mirrors the ``node`` table (task/project/time-node hierarchy)."""
    id: int
    parent_id: int | None
    title: str
    status: str | None       # TODO/DOING/LATER/WAIT/DONE/CANCELED — task-like nodes only
    priority: str | None     # A/B/C
    created_at: str          # UTC instant
    scheduled_date: str | None  # literal local date or fuzzy pin (@2026-06 / someday)
    deadline_date: str | None   # literal local date
    closed_at: str | None    # UTC instant
    body: str | None

    @classmethod
    def from_row(cls, row) -> Node:
        return cls(
            id=row["id"], parent_id=row["parent_id"], title=row["title"],
            status=row["status"], priority=row["priority"], created_at=row["created_at"],
            scheduled_date=row["scheduled_date"], deadline_date=row["deadline_date"],
            closed_at=row["closed_at"], body=row["body"],
        )

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Node | None:
        row = _db.get(con, "node", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def gets(cls, con, ids: list[int], *, include_deleted: bool = False) -> list[Node | None]:
        if not ids:
            return []
        by_id = {r["id"]: cls.from_row(r) for r in _db.query(con, "node", include_deleted=include_deleted, id__in=ids)}
        return [by_id.get(i) for i in ids]

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Node]:
        rows = _db.query(con, "node", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Node | None:
        row = _db.query_one(con, "node", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "node", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "node", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "node", row, or_=or_)

    @classmethod
    def update(cls, con, row_id: int, changes: dict) -> int:
        return _db.update(con, "node", row_id, changes)

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "node", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "node", **conds)


@dataclass
class Log:
    """Mirrors the ``log`` table (append-only record on a node)."""
    id: int
    node_id: int
    logged_at: str    # UTC instant
    body: str
    tag: str | None   # log role: note / goal / summary / metric (carrier) / clock / …

    @classmethod
    def from_row(cls, row) -> Log:
        return cls(
            id=row["id"], node_id=row["node_id"], logged_at=row["logged_at"],
            body=row["body"], tag=row["tag"],
        )

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Log | None:
        row = _db.get(con, "log", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def gets(cls, con, ids: list[int], *, include_deleted: bool = False) -> list[Log | None]:
        if not ids:
            return []
        by_id = {r["id"]: cls.from_row(r) for r in _db.query(con, "log", include_deleted=include_deleted, id__in=ids)}
        return [by_id.get(i) for i in ids]

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Log]:
        rows = _db.query(con, "log", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Log | None:
        row = _db.query_one(con, "log", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "log", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "log", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "log", row, or_=or_)

    @classmethod
    def update(cls, con, row_id: int, changes: dict) -> int:
        return _db.update(con, "log", row_id, changes)

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "log", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "log", **conds)


@dataclass
class Metric:
    """Mirrors the ``metric`` table (structured datapoint hanging off a log)."""
    id: int
    log_id: int
    node_id: int      # denormalized from log.node_id; trigger-maintained
    tag: str          # datapoint classification: weight / glucose / checkin / …
    value_num: float | None
    value_text: str | None
    unit: str | None
    note: str | None
    at: str           # UTC instant or bare date (YYYY-MM-DD) for day-granularity entries

    @classmethod
    def from_row(cls, row) -> Metric:
        return cls(
            id=row["id"], log_id=row["log_id"], node_id=row["node_id"],
            tag=row["tag"], value_num=row["value_num"], value_text=row["value_text"],
            unit=row["unit"], note=row["note"], at=row["at"],
        )

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Metric | None:
        row = _db.get(con, "metric", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def gets(cls, con, ids: list[int], *, include_deleted: bool = False) -> list[Metric | None]:
        if not ids:
            return []
        by_id = {r["id"]: cls.from_row(r) for r in _db.query(con, "metric", include_deleted=include_deleted, id__in=ids)}
        return [by_id.get(i) for i in ids]

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Metric]:
        rows = _db.query(con, "metric", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Metric | None:
        row = _db.query_one(con, "metric", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "metric", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "metric", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "metric", row, or_=or_)

    @classmethod
    def update(cls, con, row_id: int, changes: dict) -> int:
        return _db.update(con, "metric", row_id, changes)

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "metric", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "metric", **conds)


@dataclass
class Clock:
    """Mirrors the ``clock`` table (time-tracking interval on a node)."""
    id: int
    node_id: int
    start_at: str        # UTC instant
    end_at: str | None   # NULL while running
    elapsed_sec: int | None  # NULL while running; set on stop

    @classmethod
    def from_row(cls, row) -> Clock:
        return cls(
            id=row["id"], node_id=row["node_id"], start_at=row["start_at"],
            end_at=row["end_at"], elapsed_sec=row["elapsed_sec"],
        )

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Clock | None:
        row = _db.get(con, "clock", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def gets(cls, con, ids: list[int], *, include_deleted: bool = False) -> list[Clock | None]:
        if not ids:
            return []
        by_id = {r["id"]: cls.from_row(r) for r in _db.query(con, "clock", include_deleted=include_deleted, id__in=ids)}
        return [by_id.get(i) for i in ids]

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Clock]:
        rows = _db.query(con, "clock", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Clock | None:
        row = _db.query_one(con, "clock", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "clock", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "clock", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "clock", row, or_=or_)

    @classmethod
    def update(cls, con, row_id: int, changes: dict) -> int:
        return _db.update(con, "clock", row_id, changes)

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "clock", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "clock", **conds)


@dataclass
class Sched:
    """Mirrors the ``sched`` table (forward-planning entry: one-off date or rrule)."""
    id: int
    node_id: int
    on_date: str | None  # YYYY-MM-DD one-off date; mutually exclusive with rrule
    rrule: str | None    # recurrence rule: daily / weekly:Mon,Wed / monthly:5 / …
    created_at: str

    @classmethod
    def from_row(cls, row) -> Sched:
        return cls(
            id=row["id"], node_id=row["node_id"], on_date=row["on_date"],
            rrule=row["rrule"], created_at=row["created_at"],
        )

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Sched | None:
        row = _db.get(con, "sched", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def gets(cls, con, ids: list[int], *, include_deleted: bool = False) -> list[Sched | None]:
        if not ids:
            return []
        by_id = {r["id"]: cls.from_row(r) for r in _db.query(con, "sched", include_deleted=include_deleted, id__in=ids)}
        return [by_id.get(i) for i in ids]

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Sched]:
        rows = _db.query(con, "sched", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Sched | None:
        row = _db.query_one(con, "sched", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "sched", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "sched", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "sched", row, or_=or_)

    @classmethod
    def update(cls, con, row_id: int, changes: dict) -> int:
        return _db.update(con, "sched", row_id, changes)

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "sched", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "sched", **conds)


@dataclass
class Prop:
    """Mirrors the ``prop`` table (user-defined attribute on a node)."""
    node_id: int
    key: str
    value: str

    @classmethod
    def from_row(cls, row) -> Prop:
        return cls(node_id=row["node_id"], key=row["key"], value=row["value"])

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Prop]:
        rows = _db.query(con, "prop", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Prop | None:
        row = _db.query_one(con, "prop", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "prop", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "prop", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "prop", row, or_=or_)

    @classmethod
    def upsert(cls, con, row: dict) -> bool:
        return _db.upsert(con, "prop", row, key=("node_id", "key"))

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "prop", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "prop", **conds)


@dataclass
class Tag:
    """Mirrors the ``tag`` table (work/personal/… classification on a node)."""
    node_id: int
    tag: str

    @classmethod
    def from_row(cls, row) -> Tag:
        return cls(node_id=row["node_id"], tag=row["tag"])

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Tag]:
        rows = _db.query(con, "tag", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Tag | None:
        row = _db.query_one(con, "tag", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "tag", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "tag", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "tag", row, or_=or_)

    @classmethod
    def upsert(cls, con, row: dict) -> bool:
        return _db.upsert(con, "tag", row, key=("node_id", "tag"))

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "tag", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "tag", **conds)


@dataclass
class Link:
    """Mirrors the ``link`` table (vault wikilink attached to a node)."""
    node_id: int
    vault_doc: str  # vault document name without .md suffix

    @classmethod
    def from_row(cls, row) -> Link:
        return cls(node_id=row["node_id"], vault_doc=row["vault_doc"])

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Link]:
        rows = _db.query(con, "link", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Link | None:
        row = _db.query_one(con, "link", order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, "link", include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, "link", include_deleted=include_deleted, **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, "link", row, or_=or_)

    @classmethod
    def upsert(cls, con, row: dict) -> bool:
        return _db.upsert(con, "link", row, key=("node_id", "vault_doc"))

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "link", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "link", **conds)


@dataclass
class DateMeta:
    """Mirrors the ``date_meta`` table (calendar annotation: holiday, makeup day, …)."""
    date: str    # YYYY-MM-DD
    label: str   # e.g. "Labor Day holiday" / "makeup workday"

    @classmethod
    def from_row(cls, row) -> DateMeta:
        return cls(date=row["date"], label=row["label"])

    # ── reads ────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, con, date: str) -> DateMeta | None:
        row = _db.query_one(con, "date_meta", date=date)
        return cls.from_row(row) if row else None

    @classmethod
    def query(cls, con, *, order=None, limit=None, **conds) -> list[DateMeta]:
        rows = _db.query(con, "date_meta", order=order, limit=limit, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def count(cls, con, **conds) -> int:
        return _db.count(con, "date_meta", **conds)

    @classmethod
    def exists(cls, con, **conds) -> bool:
        return _db.exists(con, "date_meta", **conds)

    # ── writes ───────────────────────────────────────────────────────────────

    @classmethod
    def upsert(cls, con, row: dict) -> bool:
        return _db.upsert(con, "date_meta", row, key=("date",))

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, "date_meta", **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, "date_meta", **conds)
