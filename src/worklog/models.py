"""Domain model layer: dataclasses that mirror the worklog DB schema.

One class per table. Design rules:
- Field names match DB column names exactly.
- ``from_row(row)`` constructs from a sqlite3.Row or any mapping.
- No business logic, no rendering, no DB access.
- ``deleted_at`` is omitted — it is a storage implementation detail of the
  soft-delete system; models represent *live* domain objects.

View DTOs (what ``@text_renderer`` and ``JSONFormatter`` receive) live in the
command modules alongside the handlers that produce them. The split is:

    DB row (sqlite3.Row)
        ↓  Model.from_row()
    Model layer  ← this file
        ↓  handler assembles view DTO
    View DTO (XxxResult dataclass in commands/)
        ↓  @text_renderer / JSONFormatter
    terminal / JSON output
"""
from __future__ import annotations

from dataclasses import dataclass


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
            id=row["id"],
            parent_id=row["parent_id"],
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            created_at=row["created_at"],
            scheduled_date=row["scheduled_date"],
            deadline_date=row["deadline_date"],
            closed_at=row["closed_at"],
            body=row["body"],
        )


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
            id=row["id"],
            node_id=row["node_id"],
            logged_at=row["logged_at"],
            body=row["body"],
            tag=row["tag"],
        )


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
            id=row["id"],
            log_id=row["log_id"],
            node_id=row["node_id"],
            tag=row["tag"],
            value_num=row["value_num"],
            value_text=row["value_text"],
            unit=row["unit"],
            note=row["note"],
            at=row["at"],
        )


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
            id=row["id"],
            node_id=row["node_id"],
            start_at=row["start_at"],
            end_at=row["end_at"],
            elapsed_sec=row["elapsed_sec"],
        )


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
            id=row["id"],
            node_id=row["node_id"],
            on_date=row["on_date"],
            rrule=row["rrule"],
            created_at=row["created_at"],
        )


@dataclass
class Prop:
    """Mirrors the ``prop`` table (user-defined attribute on a node)."""
    node_id: int
    key: str
    value: str

    @classmethod
    def from_row(cls, row) -> Prop:
        return cls(node_id=row["node_id"], key=row["key"], value=row["value"])


@dataclass
class Tag:
    """Mirrors the ``tag`` table (work/personal/… classification on a node)."""
    node_id: int
    tag: str

    @classmethod
    def from_row(cls, row) -> Tag:
        return cls(node_id=row["node_id"], tag=row["tag"])


@dataclass
class Link:
    """Mirrors the ``link`` table (vault wikilink attached to a node)."""
    node_id: int
    vault_doc: str  # vault document name without .md suffix

    @classmethod
    def from_row(cls, row) -> Link:
        return cls(node_id=row["node_id"], vault_doc=row["vault_doc"])


@dataclass
class DateMeta:
    """Mirrors the ``date_meta`` table (calendar annotation: holiday, makeup day, …)."""
    date: str    # YYYY-MM-DD
    label: str   # e.g. "Labor Day holiday" / "makeup workday"

    @classmethod
    def from_row(cls, row) -> DateMeta:
        return cls(date=row["date"], label=row["label"])
