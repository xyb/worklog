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
from .queries import _upsert_prop, node_props

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
    """Backfill type.*/date.* for EVERY node from its kind — including soft-deleted (tombstoned)
    ones, so a node restored after the kind column is dropped is still classified correctly
    (skipping them would silently misclassify a restored project/meetlog as a bare task). No-op-
    safe to re-run. Returns a per-category count dict. Commits."""
    counts = {"para": 0, "date": 0, "habit": 0, "meetlog": 0, "bare": 0}
    for n in _db.query(con, "node", cols="id, kind, title", include_deleted=True):
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
    # Consistency: a tombstoned node's spoke rows are tombstoned too (soft_delete_node tombstones
    # node + props together). _upsert_prop above wrote the type.* props LIVE, which would leave
    # live prop rows hanging off a dead node — an internal inconsistency (and a phantom candidate
    # for prop-based lookups). Re-stamp the just-written reserved props on tombstoned nodes back to
    # the node's own deleted_at, so prop state always matches node state.
    # NOTE (restore requirement): this leaves a tombstoned node's reserved props tombstoned. A
    # future `wl node restore` MUST revive the spoke rows too (the inverse of soft_delete_node),
    # or a restored project/meetlog would render as a bare task. Tracked as a follow-up.
    # Scope the UPDATE to the EXACT reserved keys (not a LIKE 'type.%'/'date.%' prefix) so a user
    # prop merely named like `date.foo` / `type.custom` is never touched.
    reserved = sorted(_nt.RESERVED_KEYS)
    placeholders = ",".join("?" * len(reserved))
    con.execute(
        "UPDATE prop SET deleted_at = (SELECT deleted_at FROM node WHERE node.id = prop.node_id) "
        f"WHERE key IN ({placeholders}) AND deleted_at IS NULL "
        "AND node_id IN (SELECT id FROM node WHERE deleted_at IS NOT NULL)", reserved)
    con.commit()
    return counts


def verify_roundtrip(con):
    """Integrity check for the kind→type.* migration: for every node (live AND tombstoned)
    whose original kind is a *preserved* one (node_types.KNOWN_KINDS), the kind derived from its
    type.\\* props must equal the column value. Returns ``(ok, mismatches, retired)``:

    - ``mismatches``: ``(id, column_kind, derived_kind)`` for a KNOWN kind that failed to
      round-trip — a real data-loss bug; ``ok`` is False if any exist.
    - ``retired``: ``(id, column_kind)`` for ``signal`` / custom kinds that deliberately
      collapse to a bare node (not data loss — ``signal`` is retired by design).

    An empty ``mismatches`` means the type.* namespace losslessly represents every classified
    node — the precondition for safely dropping the column. ``period_lost`` is a non-gating
    warning list ``(id, level)`` of non-lifetime time nodes whose title yielded no canonical
    ``date.period`` (the level + title survive, but date-range queries can't place them).
    Read-only."""
    mismatches, retired, period_lost = [], [], []
    for n in _db.query(con, "node", cols="id, kind, deleted_at", include_deleted=True):  # tombstoned too
        # Read props in the SAME tombstone state as the node: a live node is classified by its
        # LIVE props (exactly what the renderers see — a tombstoned prop on a live node must NOT
        # count, or verify would give false safety); a tombstoned node by its tombstoned props.
        props = node_props(con, n["id"], include_deleted=n["deleted_at"] is not None)
        derived = _nt.legacy_kind(props)
        if derived != n["kind"]:
            (mismatches if n["kind"] in _nt.KNOWN_KINDS else retired).append(
                (n["id"], n["kind"], derived) if n["kind"] in _nt.KNOWN_KINDS else (n["id"], n["kind"]))
        lvl = props.get(_nt.K_DATE)
        if lvl and lvl != "lifetime" and _nt.K_PERIOD not in props:
            period_lost.append((n["id"], lvl))
    return (not mismatches, mismatches, retired, period_lost)


def migrate_and_verify(con):
    """The complete one-shot kind→type.* data migration: backfill the namespace, then verify
    every classified node round-trips. Returns ``(counts, ok, mismatches, retired, period_lost)``.
    Does NOT drop the kind column — that structural step only happens once the code no longer
    reads it; this proves the data is faithfully represented first."""
    counts = backfill_node_types(con)
    ok, mismatches, retired, period_lost = verify_roundtrip(con)
    return counts, ok, mismatches, retired, period_lost
