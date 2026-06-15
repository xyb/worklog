"""Time-skeleton node helpers: ensure the year→quarter→month→week→day chain exists.

Shared by the goal / recap / checkin commands (any command that needs today's — or a past —
day node). Kept in its own module so those commands don't depend on each other just to reach
`_ensure_day`."""
from __future__ import annotations

from .. import timeutil as _tu
from .. import db_table as _db
from .. import timemodel as _tm


def _ensure_time_ancestors(con, d):
    """Ensure the time skeleton year→quarter→month→week exists for date `d`, creating
    any missing level, and return the week node id (the day node's parent).

    Lookup is lenient so we reuse an existing node regardless of title style — a year
    written `2026` or `2026 年` both match the `2026%` probe. New nodes are created in
    plain ISO form (year `YYYY`, quarter `YYYY-Qn`, month `YYYY-MM`, week ISO `YYYY-Www`).
    Year hangs under an existing `lifetime` node if there is one, else stays top-level.
    Without this, a day created on the first of a month/week (when month/week don't yet
    exist) dangled directly under lifetime/NULL and broke per-month aggregation.
    """
    y, m = d.year, d.month
    iso = d.isocalendar()
    q = (m - 1) // 3 + 1

    def _get_or_make(kind, match, new_title, parent_id, *, like=False):
        # lenient reuse: year matches a `2026%` LIKE probe (any title style); the rest match
        # the exact ISO title. Single-table read via db_table (tombstone filter automatic).
        cond = {"title__like": match} if like else {"title": match}
        row = _db.query_one(con, "node", cols="id", kind=kind, order=("id" if like else None), **cond)
        if row:
            return row["id"]
        nid = _db.insert(con, "node", {
            "parent_id": parent_id, "title": new_title, "kind": kind, "created_at": _tu.utc_now(),
        })
        _tm.write_time_props(con, nid, kind, new_title)   # dual-write the type.date/date.* namespace
        return nid

    lt = _db.query_one(con, "node", cols="id", kind="lifetime", order="id")
    lt_id = lt["id"] if lt else None
    yr_id = _get_or_make("year", f"{y}%", str(y), lt_id, like=True)
    qr_id = _get_or_make("quarter", f"{y}-Q{q}", f"{y}-Q{q}", yr_id)
    mo_id = _get_or_make("month", f"{y}-{m:02d}", f"{y}-{m:02d}", qr_id)
    wk_title = f"{iso[0]}-W{iso[1]:02d}"
    wk_id = _get_or_make("week", wk_title, wk_title, mo_id)
    return wk_id


def _ensure_day(con, d):
    """Return the day-node id for date `d` (a datetime.date); create it if missing,
    building the full time skeleton (year→quarter→month→week) above it so it never
    dangles. Works for any date, not just today — back-fills past days too."""
    iso = d.isoformat()
    r = _db.query_one(con, "node", cols="id", kind="day", title__like=iso + "%", order="id")
    if r:
        return r["id"]
    wk_id = _ensure_time_ancestors(con, d)
    nid = _db.insert(con, "node", {
        "parent_id": wk_id, "title": iso, "kind": "day", "created_at": _tu.utc_now(),
    })
    _tm.write_time_props(con, nid, "day", iso)            # dual-write the type.date/date.* namespace
    con.commit()
    return nid


def _ensure_today_day(con):
    """Today's day-node id (thin wrapper over _ensure_day)."""
    return _ensure_day(con, _tu.today_date())
