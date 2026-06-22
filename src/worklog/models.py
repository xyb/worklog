"""Domain model layer: dataclasses that mirror the worklog DB schema.

One class per table. Design rules:
- Field names match DB column names exactly.
- ``from_row(row)`` constructs from a sqlite3.Row or any mapping.
- CRUD lives on the shared bases (``_Model`` + the ``_IdPK`` / ``_Upsertable``
  mixins), with the table name baked into the ``_table`` class attribute.
  A concrete class only declares ``_table``, its fields, and ``from_row``.
  Reads: ``get`` / ``gets`` / ``query`` / ``query_one`` / ``count`` / ``exists``.
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
from typing import TYPE_CHECKING

from . import db_table as _db

if TYPE_CHECKING:  # py3.11+; only a type checker imports this. Runtime uses string annotations.
    from typing import Self


class _Model:
    """Active-Record-lite base shared by every table model.

    Concrete subclasses set ``_table`` (the table name), declare their dataclass
    fields, and define ``from_row``; the CRUD wrappers here build on those. The
    ``__getitem__`` shim gives dict-style read access so a model object is a
    drop-in replacement for a ``sqlite3.Row``.

    Reads filter soft-deleted rows unless ``include_deleted=True``. No method
    commits — the caller owns the transaction."""
    _table: str

    def __getitem__(self, key: str):
        return getattr(self, key)

    # ── reads ──────────────────────────────────────────────────────────────────
    @classmethod
    def query(cls, con, *, order=None, limit=None, include_deleted=False, **conds) -> list[Self]:
        rows = _db.query(con, cls._table, order=order, limit=limit, include_deleted=include_deleted, **conds)
        return [cls.from_row(r) for r in rows]

    @classmethod
    def query_one(cls, con, *, order=None, include_deleted=False, **conds) -> Self | None:
        row = _db.query_one(con, cls._table, order=order, include_deleted=include_deleted, **conds)
        return cls.from_row(row) if row else None

    @classmethod
    def count(cls, con, *, include_deleted=False, **conds) -> int:
        return _db.count(con, cls._table, include_deleted=include_deleted, **conds)

    @classmethod
    def exists(cls, con, *, include_deleted=False, **conds) -> bool:
        return _db.exists(con, cls._table, include_deleted=include_deleted, **conds)

    # ── writes ─────────────────────────────────────────────────────────────────
    @classmethod
    def insert(cls, con, row: dict, *, or_=None) -> int:
        return _db.insert(con, cls._table, row, or_=or_)

    @classmethod
    def delete(cls, con, **conds) -> int:
        return _db.delete(con, cls._table, **conds)

    @classmethod
    def purge(cls, con, **conds) -> int:
        return _db.purge(con, cls._table, **conds)


class _IdPK:
    """Mixin for tables with an autoincrement integer ``id`` PK: fetch-by-id
    (``get`` / ``gets``) and ``update`` by id."""
    @classmethod
    def get(cls, con, id: int, *, include_deleted: bool = False) -> Self | None:
        row = _db.get(con, cls._table, id, include_deleted=include_deleted)
        return cls.from_row(row) if row else None

    @classmethod
    def gets(cls, con, ids: list[int], *, include_deleted: bool = False) -> list[Self | None]:
        """Batch fetch by id, one query. Returns one slot per input id in order, ``None`` for misses."""
        if not ids:
            return []
        by_id = {r["id"]: cls.from_row(r) for r in _db.query(con, cls._table, include_deleted=include_deleted, id__in=ids)}
        return [by_id.get(i) for i in ids]

    @classmethod
    def update(cls, con, row_id: int, changes: dict) -> int:
        return _db.update(con, cls._table, row_id, changes)


class _Upsertable:
    """Mixin for natural-key tables (no surrogate ``id``): insert-or-update on the
    conflict key ``_upsert_key`` (used for idempotent prop/tag/link/date_meta writes)."""
    _upsert_key: tuple = ()

    @classmethod
    def upsert(cls, con, row: dict) -> bool:
        return _db.upsert(con, cls._table, row, key=cls._upsert_key)


@dataclass
class Node(_IdPK, _Model):
    """Mirrors the ``node`` table (task/project/time-node hierarchy)."""
    _table = "node"
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


@dataclass
class Log(_IdPK, _Model):
    """Mirrors the ``log`` table (append-only record on a node)."""
    _table = "log"
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


@dataclass
class Metric(_IdPK, _Model):
    """Mirrors the ``metric`` table (structured datapoint hanging off a log)."""
    _table = "metric"
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


@dataclass
class Clock(_IdPK, _Model):
    """Mirrors the ``clock`` table (time-tracking interval on a node)."""
    _table = "clock"
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


@dataclass
class Sched(_IdPK, _Model):
    """Mirrors the ``sched`` table (forward-planning entry: one-off date or rrule)."""
    _table = "sched"
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


@dataclass
class Prop(_Upsertable, _Model):
    """Mirrors the ``prop`` table (user-defined attribute on a node)."""
    _table = "prop"
    _upsert_key = ("node_id", "key")
    node_id: int
    key: str
    value: str

    @classmethod
    def from_row(cls, row) -> Prop:
        return cls(node_id=row["node_id"], key=row["key"], value=row["value"])


@dataclass
class Tag(_Upsertable, _Model):
    """Mirrors the ``tag`` table (work/personal/… classification on a node)."""
    _table = "tag"
    _upsert_key = ("node_id", "tag")
    node_id: int
    tag: str

    @classmethod
    def from_row(cls, row) -> Tag:
        return cls(node_id=row["node_id"], tag=row["tag"])


@dataclass
class Link(_Upsertable, _Model):
    """Mirrors the ``link`` table (vault wikilink attached to a node)."""
    _table = "link"
    _upsert_key = ("node_id", "vault_doc")
    node_id: int
    vault_doc: str  # vault document name without .md suffix

    @classmethod
    def from_row(cls, row) -> Link:
        return cls(node_id=row["node_id"], vault_doc=row["vault_doc"])


@dataclass
class DateMeta(_Upsertable, _Model):
    """Mirrors the ``date_meta`` table (calendar annotation: holiday, makeup day, …)."""
    _table = "date_meta"
    _upsert_key = ("date",)
    date: str    # YYYY-MM-DD
    label: str   # e.g. "Labor Day holiday" / "makeup workday"

    @classmethod
    def from_row(cls, row) -> DateMeta:
        return cls(date=row["date"], label=row["label"])

    @classmethod
    def get(cls, con, date: str) -> DateMeta | None:
        """Fetch by the natural ``date`` key (date_meta has no surrogate id)."""
        row = _db.query_one(con, cls._table, date=date)
        return cls.from_row(row) if row else None
