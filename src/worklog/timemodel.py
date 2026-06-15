"""方案 C time model — time nodes as on-demand entities keyed by date, not by a tree.

The old time skeleton chained year→quarter→month→week→day through ``parent_id``
and maintained it eagerly. 方案 C drops that: a time node exists as a real row
*only when something hangs off it* (a goal / summary / a node parented to it),
and which year/month a node belongs to is computed from its ``date.period``
string — never from a ``parent_id`` link between time levels. So:

- identity is ``(type.date level, date.period value)``, enforced unique by
  find-or-create (you can't end up with two ``2026-06-14`` nodes);
- a time node carries **no** ``parent_id`` (belonging is derived from the date);
- explicit-span levels (week / quarter / decade — boundaries are ambiguous)
  pin ``date.start`` / ``date.end`` at creation, computed once so range rollups
  never re-run ISO/quarter math at query time;
- self-describing levels (year / month / day) store only ``date.period`` — their
  interval is unambiguous from the value;
- ``lifetime`` is a date-less global singleton.

This module owns the new mechanics; wiring the goal/recap/checkin commands onto
it (and the migration that back-fills type.date/date.period onto legacy
kind-based time nodes) is the cutover, tracked separately. During the transition
``kind`` is still written so legacy renderers keep showing these nodes."""
from __future__ import annotations

from . import db_table as _db
from . import node_types as _nt
from . import timeutil as _tu
from .queries import _upsert_prop


def write_time_props(con, nid, level, period):
    """Write a time node's type.date / date.* props (on creation). Shared by the new
    find-or-create path and the legacy skeleton builder so both populate the namespace
    identically: type.date always; date.period for a dated level; date.start/date.end for
    the explicit-span levels (week/quarter/decade). No commit (caller owns the transaction)."""
    _upsert_prop(con, nid, _nt.K_DATE, level)
    if level != "lifetime" and period and _nt.valid_period(level, period):
        _upsert_prop(con, nid, _nt.K_PERIOD, period)
        if level in _nt.EXPLICIT_SPAN_LEVELS:
            start, end = _nt.span_of(level, period)
            _upsert_prop(con, nid, _nt.K_START, start)
            _upsert_prop(con, nid, _nt.K_END, end)


def find_time_node(con, level, period):
    """The id of the existing time node for ``(level, period)``, or None. ``lifetime``
    matches on level alone (the singleton); every other level matches level + period.
    Tombstoned rows are skipped (db_table reads filter them)."""
    by_level = {r["node_id"] for r in _db.query(con, "prop", cols="node_id",
                                                key=_nt.K_DATE, value=level)}
    if level == "lifetime":
        candidates = by_level
    else:
        by_period = {r["node_id"] for r in _db.query(con, "prop", cols="node_id",
                                                      key=_nt.K_PERIOD, value=period)}
        candidates = by_level & by_period
    for nid in sorted(candidates):
        if _db.get(con, "node", nid) is not None:   # node itself still live
            return nid
    return None


def ensure_time_node(con, level, period, *, strict=False):
    """Find-or-create the time node for ``(level, period)`` and return its id. No commit
    (the caller owns the transaction).

    Idempotent: a second call for the same ``(level, period)`` returns the same id, so a
    duplicate time node can never come into existence. ``strict=True`` instead *refuses*
    when the node already exists (raises ``ValueError``) — the "explicitly creating an
    already-existing date node is a data-integrity conflict, hard-reject it" rule; the
    everyday lazy path uses ``strict=False`` (ensure / find-or-create).

    Raises ``ValueError`` for an unknown level or a period that doesn't match the level's
    canonical form (``lifetime`` takes ``None`` / empty period)."""
    if level not in _nt.DATE_LEVELS:
        raise ValueError(f"invalid time level: {level!r} (valid: {', '.join(_nt.DATE_LEVELS)})")
    if level != "lifetime" and not _nt.valid_period(level, period):
        raise ValueError(f"invalid {level} period: {period!r}")

    existing = find_time_node(con, level, period)
    if existing is not None:
        if strict:
            label = "lifetime" if level == "lifetime" else period
            raise ValueError(f"time node already exists for {level} {label} (#{existing})")
        return existing

    title = "lifetime" if level == "lifetime" else period
    nid = _db.insert(con, "node", {
        # parent_id intentionally NULL — 方案 C never links time levels by parent.
        # kind is dual-written during the transition so legacy renderers still work.
        "parent_id": None, "title": title, "kind": level, "created_at": _tu.utc_now(),
    })
    write_time_props(con, nid, level, period)
    return nid
