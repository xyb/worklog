"""Domain model layer: dataclasses that mirror the worklog DB schema, plus
thin class-level read helpers.

One class per table. Design rules:
- Field names match DB column names exactly.
- ``from_row(row)`` constructs from a sqlite3.Row or any mapping.
- ``get()`` and ``query()`` are thin wrappers around db_table helpers that
  apply ``from_row()``; they own the table-name string so callers don't
  repeat it.
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

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Node | None:
        row = _db.get(con, "node", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Node]:
        rows = _db.query(con, "node", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


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

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Log | None:
        row = _db.get(con, "log", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Log]:
        rows = _db.query(con, "log", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


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

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Metric | None:
        row = _db.get(con, "metric", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Metric]:
        rows = _db.query(con, "metric", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


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

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Clock | None:
        row = _db.get(con, "clock", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Clock]:
        rows = _db.query(con, "clock", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


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

    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Sched | None:
        row = _db.get(con, "sched", id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Sched]:
        rows = _db.query(con, "sched", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


@dataclass
class Prop:
    """Mirrors the ``prop`` table (user-defined attribute on a node)."""
    node_id: int
    key: str
    value: str

    @classmethod
    def from_row(cls, row) -> Prop:
        return cls(node_id=row["node_id"], key=row["key"], value=row["value"])

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Prop]:
        rows = _db.query(con, "prop", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


@dataclass
class Tag:
    """Mirrors the ``tag`` table (work/personal/… classification on a node)."""
    node_id: int
    tag: str

    @classmethod
    def from_row(cls, row) -> Tag:
        return cls(node_id=row["node_id"], tag=row["tag"])

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Tag]:
        rows = _db.query(con, "tag", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


@dataclass
class Link:
    """Mirrors the ``link`` table (vault wikilink attached to a node)."""
    node_id: int
    vault_doc: str  # vault document name without .md suffix

    @classmethod
    def from_row(cls, row) -> Link:
        return cls(node_id=row["node_id"], vault_doc=row["vault_doc"])

    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Link]:
        rows = _db.query(con, "link", order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]


@dataclass
class DateMeta:
    """Mirrors the ``date_meta`` table (calendar annotation: holiday, makeup day, …)."""
    date: str    # YYYY-MM-DD
    label: str   # e.g. "Labor Day holiday" / "makeup workday"

    @classmethod
    def from_row(cls, row) -> DateMeta:
        return cls(date=row["date"], label=row["label"])

    @classmethod
    def get(cls, con, date: str) -> DateMeta | None:
        row = _db.query_one(con, "date_meta", date=date)
        return cls.from_row(row) if row else None

    @classmethod
    def query(cls, con, *, order=None, limit=None, **conds) -> list[DateMeta]:
        rows = _db.query(con, "date_meta", order=order, limit=limit, **conds)
        return [cls.from_row(r) for r in rows]
