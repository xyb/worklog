"""The ``node`` entity — creation and node-level data-layer operations, in its own module so
every node-creating path (wl add, import, migrations) routes through ONE place. Core concepts —
node / log / metric / tag — each get their own module; this is node's. Built on the ``db_table``
dict→INSERT helper: no raw SQL, no hand-written column lists.
"""
from __future__ import annotations

from . import node_types as _nt
from . import timeutil as _tu
from .models import Node
from .queries import _upsert_prop


def create_node(con, *, title, parent_id=None, status=None, priority=None,
                scheduled_date=None, deadline_date=None, body=None,
                created_at=None, closed_at=None, para=None, props=None):
    """The SINGLE entry point for creating a node — there is no other ``INSERT INTO node``
    (wl add / import / any future caller route here). A node's classification lives in the
    type.* prop namespace, set explicitly — ``para`` → ``type.para``,
    and ``props`` (a dict, or an iterable of ``(key, value)``) of reserved ``type.*`` / ``date.*``
    or user props, each validated by ``_upsert_prop``. A bare create (no para, no props) is a
    plain task. ``created_at`` defaults to now (UTC). Returns the new node id. No commit — the
    caller owns the transaction."""
    row = {"title": title, "created_at": created_at or _tu.utc_now()}
    for col, val in (("parent_id", parent_id), ("status", status), ("priority", priority),
                     ("scheduled_date", scheduled_date), ("deadline_date", deadline_date),
                     ("body", body), ("closed_at", closed_at)):
        if val is not None:
            row[col] = val
    nid = Node.insert(con, row)
    if para:
        _upsert_prop(con, nid, _nt.K_PARA, para)
    written = {}
    for key, val in (props.items() if isinstance(props, dict) else (props or [])):
        _upsert_prop(con, nid, key, val)
        written[key] = val
    # A time node must be COMPLETE: derive its date.* (period + explicit span for week/quarter/
    # decade) via the shared node_types.date_props_for helper, so it's findable by date-range
    # queries (time_node_by_period needs BOTH type.date and date.period). The period source is an
    # explicitly-passed date.period if given, else the title — so `wl add 2026-06-20 --prop
    # type.date=day` works like the old `-k day` did, AND `--prop type.date=week --prop
    # date.period=2026-W25` still gets its date.start/date.end. An explicitly-given date.* value is
    # never overwritten (only the missing ones are filled in).
    level = written.get(_nt.K_DATE)
    if level:
        period = written.get(_nt.K_PERIOD) or title
        for key, val in _nt.date_props_for(level, period).items():
            if key not in written:
                _upsert_prop(con, nid, key, val)
    return nid
