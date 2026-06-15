"""One-off backfill of the type.*/date.* namespace onto existing nodes.

Derives each node's reserved props from its legacy ``kind`` and writes them through
the validated write API (``_upsert_prop``) — deliberately NOT a raw-SQL migration,
because reserved keys may not bypass the validator (DESIGN hard-constraint) and a
``.sql`` file would. Additive and idempotent: ``kind`` is left intact, so this can run
alongside the column during the transition and re-running just re-asserts the same
props. This is the data half of the kind→type.* move; dropping the ``kind`` column is a
separate schema-cutover step.

Mapping (mirrors what `wl add` dual-writes for new nodes):
- ``area`` / ``project`` → ``type.para`` (a plain ``task`` stays bare — the loose default)
- a time level → ``type.date`` (+ ``date.period`` parsed from the title, + ``date.start`` /
  ``date.end`` for explicit-span levels)
- ``habit`` / ``meetlog`` → the existence prop
- ``signal`` (retired) and any custom kind → nothing"""
from __future__ import annotations

import re

from . import db_table as _db
from . import node_types as _nt
from .queries import _upsert_prop

# pull a canonical period token out of a (possibly decorated, e.g. "2026 年") legacy title
_PERIOD_EXTRACT = {
    "day": re.compile(r"(\d{4}-\d{2}-\d{2})"),
    "week": re.compile(r"(\d{4}-W\d{2})"),
    "quarter": re.compile(r"(\d{4}-Q[1-4])"),
    "month": re.compile(r"(\d{4}-\d{2})"),
    "year": re.compile(r"(\d{4})"),
}


def _period_from_title(level, title):
    """Best-effort canonical ``date.period`` for a legacy time node's title, or None if the
    title carries no recognizable period (then we leave date.period unset — kind still has it)."""
    rx = _PERIOD_EXTRACT.get(level)
    if not rx or not title:
        return None
    m = rx.search(title)
    if not m:
        return None
    period = m.group(1)
    return period if _nt.valid_period(level, period) else None


def backfill_node_types(con):
    """Backfill type.*/date.* for every live node from its kind. No-op-safe to re-run.
    Returns a per-category count dict. Commits."""
    counts = {"para": 0, "date": 0, "habit": 0, "meetlog": 0, "bare": 0}
    for n in _db.query(con, "node", cols="id, kind, title"):
        nid, kind, title = n["id"], n["kind"], n["title"]
        if kind in ("area", "project"):
            _upsert_prop(con, nid, _nt.K_PARA, kind)
            counts["para"] += 1
        elif kind in _nt.DATE_LEVELS:
            _upsert_prop(con, nid, _nt.K_DATE, kind)
            counts["date"] += 1
            if kind != "lifetime":
                period = _period_from_title(kind, title)
                if period:
                    _upsert_prop(con, nid, _nt.K_PERIOD, period)
                    if kind in _nt.EXPLICIT_SPAN_LEVELS:
                        start, end = _nt.span_of(kind, period)
                        _upsert_prop(con, nid, _nt.K_START, start)
                        _upsert_prop(con, nid, _nt.K_END, end)
        elif kind == "habit":
            _upsert_prop(con, nid, _nt.K_HABIT, "")
            counts["habit"] += 1
        elif kind == "meetlog":
            _upsert_prop(con, nid, _nt.K_MEETLOG, "")
            counts["meetlog"] += 1
        else:
            counts["bare"] += 1   # plain task (loose default), signal (retired), custom kinds
    con.commit()
    return counts
