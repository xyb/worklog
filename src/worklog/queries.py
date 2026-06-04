"""sqlite-backed query helpers for worklog.

These take a sqlite3.Connection as an argument and don't touch any
module-level state, so they're safe to import from any module that
already has a connection. They sit between the pure utilities in
helpers.py and the command handlers in cli.py.
"""
from __future__ import annotations

import sqlite3
import sys
from .helpers import GENERIC_TAGS  # noqa: F401
from .helpers import _resolve_concrete_date


def _insert_log(con, nid, entry):
    """Insert a log. entry can carry a historical date + time:
    - dict{date, time, body}: date=YYYY-MM-DD / today / yesterday / day-before-yesterday; time=HH:MM optional
    - string prefixed with 'YYYY-MM-DD content': date only
    - plain body: use NOW (DB DEFAULT)
    """
    import re as _re
    date, time_part, body = None, None, entry
    if isinstance(entry, dict):
        date, time_part, body = entry.get("date"), entry.get("time"), entry["body"]
    else:
        m = _re.match(r"^(\d{4}-\d{2}-\d{2})[ T](.*)$", entry)
        if m:
            date, body = m.group(1), m.group(2)
    if date:
        # parse short form ("yesterday/today/day-before-yesterday/tomorrow/day-after-tomorrow" or YYYY-MM-DD)
        date = _resolve_concrete_date(date)
        if time_part:
            if not _re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", time_part):
                raise ValueError(f"invalid --time '{time_part}' (expected HH:MM or HH:MM:SS)")
            # pad seconds
            if time_part.count(":") == 1:
                time_part += ":00"
            logged_at = f"{date} {time_part}"
        else:
            logged_at = date
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, ?)", (nid, logged_at, body))
    elif time_part:
        # no date but time given -> today + that time
        from datetime import date as _date
        if not _re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", time_part):
            raise ValueError(f"invalid --time '{time_part}' (expected HH:MM or HH:MM:SS)")
        if time_part.count(":") == 1:
            time_part += ":00"
        logged_at = f"{_date.today().isoformat()} {time_part}"
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, ?)", (nid, logged_at, body))
    else:
        con.execute("INSERT INTO log (node_id, body) VALUES (?, ?)", (nid, body))

def _project_members(con, proj_id):
    """Set of task/meetlog/habit ids linked to a project: structural children (parent) + shared semantic tags"""
    ids = set()
    proj_tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (proj_id,))} - GENERIC_TAGS
    for r in con.execute(
        "SELECT id FROM node WHERE parent_id = ? AND kind IN ('task','meetlog','habit')", (proj_id,)
    ):
        ids.add(r["id"])
    if proj_tags:
        qm = ",".join("?" * len(proj_tags))
        for r in con.execute(
            f"SELECT DISTINCT n.id FROM node n JOIN tag t ON n.id = t.node_id "
            f"WHERE t.tag IN ({qm}) AND n.kind IN ('task','meetlog','habit')",
            list(proj_tags),
        ):
            ids.add(r["id"])
    return ids

def _ancestors_chain(con, node_id):
    """Return the path list[Row] from the top-level root to node (inclusive)."""
    chain = []
    cur = con.execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()
    if not cur:
        return chain
    chain.append(cur)
    while cur["parent_id"]:
        cur = con.execute("SELECT * FROM node WHERE id = ?", (cur["parent_id"],)).fetchone()
        if not cur:
            break
        chain.append(cur)
    return list(reversed(chain))

def _node_bucket(con, nid):
    """Bucket a node into work / personal / other by work/personal tag."""
    tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (nid,))}
    if "work" in tags:
        return "work"
    if "personal" in tags:
        return "personal"
    return "other"

def _node_project(con, nid):
    """Return the project ancestor (id, title) of a node, or (None, '(unassigned)') if none."""
    for p in _ancestors_chain(con, nid):
        if p["kind"] == "project":
            return p["id"], p["title"]
    return None, "(unassigned)"

def _node_plan(con, nid, sched_ids):
    """Derive planned vs unplanned: scheduled that day (or carrying the transitional
    'planned' tag) = planned; everything else = unplanned. The old separate
    'unplanned (untagged)' bucket was a migration-era distinction — now that
    planned/unplanned is derived from sched, anything not scheduled is just unplanned."""
    if nid in sched_ids:
        return "planned"
    tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (nid,))}
    if "planned" in tags:
        return "planned"
    return "unplanned"

def _sec_group(con, nid, n, by, sched_ids):
    """(key, display title) for the secondary group. by in project/priority/plan."""
    if by == "priority":
        label = {"A": "P0", "B": "P1", "C": "P2"}.get(n["priority"], "—")
        return label, label
    if by == "plan":
        label = _node_plan(con, nid, sched_ids)
        return label, label
    pid, ptitle = _node_project(con, nid)
    return (pid if pid is not None else ptitle), ptitle

def _collect_descendants(con, root_id):
    """Recursively collect all descendant ids of a node (excluding self)."""
    acc = []
    stack = [root_id]
    while stack:
        pid = stack.pop()
        children = con.execute("SELECT id FROM node WHERE parent_id = ?", (pid,)).fetchall()
        for c in children:
            acc.append(c["id"])
            stack.append(c["id"])
    return acc

def _has_tag(con, nid, tag):
    return con.execute("SELECT 1 FROM tag WHERE node_id = ? AND tag = ? LIMIT 1", (nid, tag)).fetchone() is not None


_META_LOG_TYPES = ("goal", "summary", "overview", "top5")  # meta fields stored as typed logs


def _latest_typed_log(con, node_id, log_type):
    """The most recent log row of a given `type` on a node — the 'current' value of a
    history-preserving meta field (goal / summary / overview / top5). Returns the Row
    (body, logged_at) or None. Each edit appends a new log, so history is kept and the
    latest one is the current value."""
    return con.execute(
        "SELECT body, logged_at FROM log WHERE node_id = ? AND type = ? "
        "ORDER BY logged_at DESC, id DESC LIMIT 1",
        (node_id, log_type),
    ).fetchone()


def _set_typed_log(con, node_id, log_type, body):
    """Append a new typed log (history-preserving write of a meta field). No commit;
    caller controls the transaction. Returns the new log id."""
    return con.execute(
        "INSERT INTO log (node_id, type, body) VALUES (?, ?, ?)", (node_id, log_type, body)
    ).lastrowid


def _has_checkin(con, node_id, day):
    """True if the node has a check-in metric on the given day (YYYY-MM-DD).
    This is the structured 'done today' signal (G1) — replaces the old, too-loose
    'did any log exist that day' heuristic, so a stray note no longer counts as done."""
    return con.execute(
        "SELECT 1 FROM metric WHERE node_id = ? AND tag = 'checkin' AND substr(at, 1, 10) = ? LIMIT 1",
        (node_id, day),
    ).fetchone() is not None

def _node_clock_min(con, nid, day=None):
    """Minutes spent on this node, auto-combined: structured clock intervals
    (sum elapsed_sec) union the ordinary-log timestamp span; takes the greater so a
    "no wl start/stop, only wl log" workflow still gets a rough duration.
    `day` (YYYY-MM-DD) scopes BOTH parts to that day — pass it in a per-day view so a
    multi-day task doesn't show its all-time span on one day's row; omit it (None) for
    the node's all-time total.
    Design choice: auto-compute, no explicit --duration field. Auto-calc surfaces drift;
    an explicit field rarely gets updated and pollutes upper-level aggregations.
    """
    # 1. structured clock intervals (precise, from wl start/stop/spent)
    if day:
        secs = con.execute(
            "SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ? AND substr(end_at, 1, 10) = ?",
            (nid, day),
        ).fetchone()["s"]
    else:
        secs = con.execute(
            "SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ?", (nid,)
        ).fetchone()["s"]
    clock = int((secs or 0) / 60)

    # 2. plain-note log timestamp span (rough, max - min); exclude typed logs (metric
    #    carriers / goal / summary / …) so they don't inflate the span. Same-timestamp
    #    rows collapse (a batch backfill is one instant, not a span). Scoped to `day`
    #    when given so a multi-day task's span isn't counted on a single day's row.
    if day:
        rows = list(con.execute(
            "SELECT DISTINCT logged_at FROM log WHERE node_id = ? AND type IS NULL "
            "AND substr(logged_at, 1, 10) = ? ORDER BY logged_at",
            (nid, day),
        ))
    else:
        rows = list(con.execute(
            "SELECT DISTINCT logged_at FROM log WHERE node_id = ? AND type IS NULL ORDER BY logged_at",
            (nid,),
        ))
    span = 0
    if len(rows) >= 2:
        try:
            from datetime import datetime
            first = datetime.fromisoformat(rows[0]["logged_at"])
            last = datetime.fromisoformat(rows[-1]["logged_at"])
            span = max(0, int((last - first).total_seconds() / 60))
        except (ValueError, TypeError):
            pass

    return max(clock, span)

def _node_exists(con, node_id):
    return con.execute("SELECT 1 FROM node WHERE id = ?", (node_id,)).fetchone() is not None


def _node_tags(con, nid):
    """Return the tag list for a node (insertion order)."""
    return [r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (nid,))]


def _check_ids_exist(con, ids):
    """Batch existence check; sys.exit if any id is missing. Used by multi-id commands."""
    for nid in ids:
        if not _node_exists(con, nid):
            sys.exit(f"✗ node #{nid} not found")


def _upsert_prop(con, nid, key, value):
    """Unified prop UPSERT (no commit; caller controls the transaction). Batch-friendly.
    `_set_prop` is the commit version for single daily operations."""
    con.execute("INSERT OR REPLACE INTO prop (node_id, key, value) VALUES (?, ?, ?)", (nid, key, value))


# generic ORDER BY fragment: priority A/B/C first, NULL last; same priority by id ascending.
# Usage: f"SELECT * FROM node WHERE ... {_ORDER_BY_PRI_ID}"
# Note: when joining, write the qualified column "n.priority"; that case stays inline.


def _status_filter_sql(include_canceled=False, hide_done=False, col="status"):
    """Build a `status` column filter SQL fragment + params. Used uniformly across cmds, avoids scattered string-concat.
    Returns (where_fragment, params_list); when nothing is filtered returns ("", []).

    Usage:
        frag, params = _status_filter_sql(inc_cancel, hide_done=not args.all)
        if frag: where.append(frag); sql_params.extend(params)
    """
    excluded = []
    if hide_done:
        excluded.append("DONE")
    if not include_canceled:
        excluded.append("CANCELED")
    if not excluded:
        return "", []
    ph = ",".join("?" * len(excluded))
    return f"({col} IS NULL OR {col} NOT IN ({ph}))", excluded
