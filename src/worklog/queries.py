"""sqlite-backed query helpers for worklog.

These take a sqlite3.Connection as an argument and don't touch any
module-level state, so they're safe to import from any module that
already has a connection. They sit between the pure utilities in
helpers.py and the command handlers in cli.py.
"""
from __future__ import annotations

import sqlite3
import sys
from . import timeutil as _tu
from . import db_table as _db
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
    from . import timeutil as _tu
    if date:
        # parse short form ("yesterday/today/day-before-yesterday/tomorrow/day-after-tomorrow" or YYYY-MM-DD)
        date = _resolve_concrete_date(date)
        if time_part:
            if not _re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", time_part):
                raise ValueError(f"invalid --time '{time_part}' (expected HH:MM or HH:MM:SS)")
            # pad seconds
            if time_part.count(":") == 1:
                time_part += ":00"
            # a date+time the user typed is local wall-clock -> store UTC
            logged_at = _tu.local_to_utc(f"{date} {time_part}")
        else:
            # date only, no time: a degenerate "logged on this day" — keep the
            # bare local date verbatim (no instant to convert; day-grouping reads it as-is)
            logged_at = date
        _db.insert(con, "log", {"node_id": nid, "logged_at": logged_at, "body": body})
    elif time_part:
        # no date but time given -> today + that time (local) -> store UTC
        if not _re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", time_part):
            raise ValueError(f"invalid --time '{time_part}' (expected HH:MM or HH:MM:SS)")
        if time_part.count(":") == 1:
            time_part += ":00"
        logged_at = _tu.local_to_utc(f"{_tu.today()} {time_part}")
        _db.insert(con, "log", {"node_id": nid, "logged_at": logged_at, "body": body})
    else:
        _db.insert(con, "log", {"node_id": nid, "logged_at": _tu.utc_now(), "body": body})

def _project_members(con, proj_id):
    """Set of task/meetlog/habit ids linked to a project: structural children (parent) + shared semantic tags"""
    ids = set()
    proj_tags = {r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=proj_id)} - GENERIC_TAGS
    for r in _db.query(con, "node", cols="id", parent_id=proj_id, kind__in=("task", "meetlog", "habit")):
        ids.add(r["id"])
    if proj_tags:
        for r in nodes_with_tag(con, proj_tags, kinds=("task", "meetlog", "habit"), cols="id"):
            ids.add(r["id"])
    return ids

def _ancestors_chain(con, node_id):
    """Return the path list[Row] from the top-level root to node (inclusive)."""
    chain = []
    cur = _db.get(con, "node", node_id)
    if not cur:
        return chain
    chain.append(cur)
    while cur["parent_id"]:
        cur = _db.get(con, "node", cur["parent_id"])
        if not cur:
            break
        chain.append(cur)
    return list(reversed(chain))

def _node_bucket(con, nid):
    """Bucket a node into work / personal / other by work/personal tag."""
    tags = {r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=nid)}
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
    tags = {r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=nid)}
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
        children = _db.query(con, "node", cols="id", parent_id=pid)
        for c in children:
            acc.append(c["id"])
            stack.append(c["id"])
    return acc

def _has_tag(con, nid, tag):
    return _db.exists(con, "tag", node_id=nid, tag=tag)


def nodes_with_tag(con, tags, *, kinds=None, cols="*", order=None):
    """Nodes carrying ANY of `tags` (a str or an iterable) — the single-table
    decomposition of `node JOIN tag`: collect node ids from the tag table, then
    read those nodes. `kinds` further restricts node.kind; `cols` / `order` pass
    through to the node read. Returns list[Row] (deduped by node; empty tags → [])."""
    tag_list = [tags] if isinstance(tags, str) else list(tags)
    if not tag_list:
        return []
    ids = sorted({r["node_id"] for r in _db.query(con, "tag", cols="node_id", tag__in=tag_list)})
    if not ids:
        return []
    conds = {"id__in": ids}
    if kinds is not None:
        conds["kind__in"] = list(kinds)
    return _db.query(con, "node", cols=cols, order=order, **conds)


# every spoke table references node_id; soft-deleting a node tombstones these too —
# the app-level stand-in for the old FK ON DELETE CASCADE (foreign_keys is now OFF, WL#501).
_NODE_SPOKES = ("log", "tag", "link", "prop", "sched", "clock", "metric")


def soft_delete_node(con, nid):
    """Soft-delete a node and everything hanging off it (log / tag / link / prop /
    sched / clock / metric, all keyed by node_id) — the app-level replacement for the
    old `ON DELETE CASCADE` now that FK enforcement is off. Tombstones, never removes;
    reversible by clearing `deleted_at`. No commit. Returns the node rowcount."""
    n = _db.delete(con, "node", id=nid)
    for spoke in _NODE_SPOKES:
        _db.delete(con, spoke, node_id=nid)
    return n


def soft_delete_log(con, log_id):
    """Soft-delete a log and its metrics (the old `metric.log_id` CASCADE, now
    app-level). Tombstones, never removes. No commit. Returns the log rowcount."""
    n = _db.delete(con, "log", id=log_id)
    _db.delete(con, "metric", log_id=log_id)
    return n


def make_node_filter(con, args):
    """Shared --tag / --kind / --status filter, used by ls/tree/day/logs/agenda so every
    list/view command filters the same way (one definition, DESIGN §12 single entry point).
    Returns a memoized predicate `node_id -> bool`, or **None** when no filter flag is set —
    callers treat None as "no filtering", keeping unfiltered behavior byte-identical.
    `--tag` is comma-separated AND: the node must carry every listed tag."""
    tag = getattr(args, "tag", None)
    kind = getattr(args, "kind", None)
    status = getattr(args, "status", None)
    # parse tags first so an effective-empty tag (--tag "" / "," / ",,") collapses to
    # "no tag filter" rather than an all-pass predicate (which would still route tree to
    # the filtered path); if nothing real is left to filter on, return None.
    wanted = {t.strip() for t in tag.split(",") if t.strip()} if tag else set()
    if not wanted and not kind and not status:
        return None

    cache = {}

    def ok(nid):
        if nid in cache:
            return cache[nid]
        n = _db.get(con, "node", nid)
        res = n is not None
        if res and kind and n["kind"] != kind:
            res = False
        if res and status and n["status"] != status:
            res = False
        if res and wanted:
            have = {r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=nid)}
            res = wanted <= have
        cache[nid] = res
        return res

    return ok


_META_LOG_TYPES = ("goal", "summary", "overview", "top5")  # meta fields stored as typed logs


def _latest_typed_log(con, node_id, log_type):
    """The most recent log row of a given `type` on a node — the 'current' value of a
    history-preserving meta field (goal / summary / overview / top5). Returns the Row
    (body, logged_at) or None. Each edit appends a new log, so history is kept and the
    latest one is the current value."""
    return _db.query_one(con, "log", cols="body, logged_at", node_id=node_id, tag=log_type,
                        order="logged_at DESC, id DESC")


def _set_typed_log(con, node_id, log_type, body):
    """Append a new typed log (history-preserving write of a meta field). No commit;
    caller controls the transaction. Returns the new log id."""
    return _db.insert(con, "log", {
        "node_id": node_id, "tag": log_type, "body": body, "logged_at": _tu.utc_now(),
    })


def _has_checkin(con, node_id, day):
    """True if the node has a check-in metric on the given day (YYYY-MM-DD).
    This is the structured 'done today' signal (G1) — replaces the old, too-loose
    'did any log exist that day' heuristic, so a stray note no longer counts as done."""
    return con.execute(
        f"SELECT 1 FROM metric WHERE node_id = ? AND tag = 'checkin' AND {_tu.local_day_sql('at')} = ? AND deleted_at IS NULL LIMIT 1",
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
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ? AND {_tu.local_day_sql('end_at')} = ? AND deleted_at IS NULL",
            (nid, day),
        ).fetchone()["s"]
    else:
        secs = con.execute(
            "SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ? AND deleted_at IS NULL", (nid,)
        ).fetchone()["s"]
    clock = int((secs or 0) / 60)

    # 2. plain-note log timestamp span (rough, max - min); exclude typed logs (metric
    #    carriers / goal / summary / …) so they don't inflate the span. Same-timestamp
    #    rows collapse (a batch backfill is one instant, not a span). Scoped to `day`
    #    when given so a multi-day task's span isn't counted on a single day's row.
    if day:
        rows = list(con.execute(
            f"SELECT DISTINCT logged_at FROM log WHERE node_id = ? AND tag IS NULL "
            f"AND {_tu.local_day_sql('logged_at')} = ? AND deleted_at IS NULL ORDER BY logged_at",
            (nid, day),
        ))
    else:
        rows = list(con.execute(
            "SELECT DISTINCT logged_at FROM log WHERE node_id = ? AND tag IS NULL AND deleted_at IS NULL ORDER BY logged_at",
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
    return _db.exists(con, "node", id=node_id)


def _node_tags(con, nid):
    """Return the tag list for a node (insertion order)."""
    return [r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=nid)]


def _check_ids_exist(con, ids):
    """Batch existence check; sys.exit if any id is missing. Used by multi-id commands."""
    for nid in ids:
        if not _node_exists(con, nid):
            sys.exit(f"✗ node #{nid} not found")


def _upsert_prop(con, nid, key, value):
    """Unified prop UPSERT (no commit; caller controls the transaction). Batch-friendly.
    `_set_prop` is the commit version for single daily operations."""
    _db.insert(con, "prop", {"node_id": nid, "key": key, "value": value}, or_="replace")


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
