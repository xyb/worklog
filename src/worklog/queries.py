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
from . import node_types as _nt
from .helpers import GENERIC_TAGS  # noqa: F401
from .helpers import _resolve_concrete_date
from .models import Clock, Link, Log, Metric, Node, Prop, Sched, Tag
from .render import die


def _insert_log(con, nid, entry):
    """Insert a log; return the new log id. entry can carry a historical date + time:
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
        return Log.insert(con, {"node_id": nid, "logged_at": logged_at, "body": body})
    elif time_part:
        # no date but time given -> today + that time (local) -> store UTC
        if not _re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", time_part):
            raise ValueError(f"invalid --time '{time_part}' (expected HH:MM or HH:MM:SS)")
        if time_part.count(":") == 1:
            time_part += ":00"
        logged_at = _tu.local_to_utc(f"{_tu.today()} {time_part}")
        return Log.insert(con, {"node_id": nid, "logged_at": logged_at, "body": body})
    else:
        return Log.insert(con, {"node_id": nid, "logged_at": _tu.utc_now(), "body": body})

def _project_members(con, proj_id):
    """Set of task/meetlog/habit ids linked to a project: structural children (parent) + shared semantic tags"""
    ids = set()
    proj_tags = {r.tag for r in Tag.query(con, node_id=proj_id)} - GENERIC_TAGS
    for r in con.execute(
            f"SELECT id FROM node n WHERE n.parent_id=? AND n.{_db.ALIVE} AND ({workitem_sql('n')})",
            (proj_id,)):
        ids.add(r["id"])
    if proj_tags:
        for r in nodes_with_tag(con, proj_tags, types=("task", "meetlog", "habit"), cols="id"):
            ids.add(r["id"])
    return ids

def _ancestors_chain(con, node_id):
    """Return the path list[Row] from the top-level root to node (inclusive). Cycle-safe:
    FK enforcement is off so `parent_id` integrity isn't DB-guaranteed and a
    bad/legacy graph could contain a cycle — a visited set stops the walk re-entering it
    rather than looping forever."""
    chain = []
    cur = Node.get(con, node_id)
    if not cur:
        return chain
    chain.append(cur)
    seen = {node_id}
    while cur["parent_id"] and cur["parent_id"] not in seen:
        seen.add(cur["parent_id"])
        cur = Node.get(con, cur["parent_id"])
        if not cur:
            break
        chain.append(cur)
    return list(reversed(chain))

def _node_bucket(con, nid):
    """Bucket a node into work / personal / other by work/personal tag."""
    tags = {r.tag for r in Tag.query(con, node_id=nid)}
    if "work" in tags:
        return "work"
    if "personal" in tags:
        return "personal"
    return "other"

def _node_project(con, nid):
    """Return the project ancestor (id, title) of a node, or (None, '(unassigned)') if none."""
    for p in _ancestors_chain(con, nid):
        if node_type(con, p) == "project":
            return p["id"], p["title"]
    return None, "(unassigned)"

def _node_plan(con, nid, sched_ids):
    """Derive planned vs unplanned: scheduled that day (or carrying the transitional
    'planned' tag) = planned; everything else = unplanned. The old separate
    'unplanned (untagged)' bucket was a migration-era distinction — now that
    planned/unplanned is derived from sched, anything not scheduled is just unplanned."""
    if nid in sched_ids:
        return "planned"
    tags = {r.tag for r in Tag.query(con, node_id=nid)}
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

def _collect_descendants(con, root_id, *, include_deleted=False):
    """Recursively collect all descendant ids of a node (excluding self). By default only
    live nodes; `include_deleted=True` walks through tombstoned nodes too, so a structural
    cascade (soft-delete subtree / cycle check) reaches live nodes hanging under an already-
    tombstoned intermediate."""
    acc = []
    stack = [root_id]
    seen = {root_id}  # cycle-safe: FK is off, so a bad parent_id graph could loop
    while stack:
        pid = stack.pop()
        children = _db.query(con, "node", cols="id", parent_id=pid, include_deleted=include_deleted)
        for c in children:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            acc.append(c["id"])
            stack.append(c["id"])
    return acc

def _has_tag(con, nid, tag):
    return Tag.exists(con, node_id=nid, tag=tag)


def nodes_with_tag(con, tags, *, types=None, cols="*", order=None):
    """Nodes carrying ANY of `tags` (a str or an iterable) — the single-table
    decomposition of `node JOIN tag`: collect node ids from the tag table, then
    read those nodes. `types` further restricts by derived type; `cols` / `order` pass
    through to the node read. Returns list[Node] for the default full-row read (ready to
    hand to a renderer); a `cols=` projection (e.g. cols="id") returns raw list[Row]
    instead, since a partial row can't satisfy Node.from_row. Deduped by node; empty
    tags → []."""
    tag_list = [tags] if isinstance(tags, str) else list(tags)
    if not tag_list:
        return []
    ids = sorted({r.node_id for r in Tag.query(con, tag__in=tag_list)})
    if not ids:
        return []
    if types is not None:
        # restrict by DERIVED type (column-free): filter the id list, then read
        want = set(types)
        ids = [i for i in ids if node_type(con, i) in want]
        if not ids:
            return []
    rows = _db.query(con, "node", cols=cols, order=order, id__in=ids)
    # convert at this boundary so every full-row caller gets Node objects without
    # repeating the wrap (and a future caller can't forget it); projections stay raw.
    return [Node.from_row(r) for r in rows] if cols == "*" else rows


# every spoke table references node_id; soft-deleting a node tombstones these too —
# the app-level stand-in for the old FK ON DELETE CASCADE (foreign_keys is now OFF).
_NODE_SPOKES = ("log", "tag", "link", "prop", "sched", "clock", "metric")


def soft_delete_node(con, nid):
    """Soft-delete a node and everything hanging off it (log / tag / link / prop /
    sched / clock / metric, all keyed by node_id) — the app-level replacement for the
    old `ON DELETE CASCADE` now that FK enforcement is off. Tombstones, never removes;
    reversible by clearing `deleted_at`. No commit. Returns the node rowcount."""
    n = Node.delete(con, id=nid)
    Log.delete(con, node_id=nid)
    Tag.delete(con, node_id=nid)
    Link.delete(con, node_id=nid)
    Prop.delete(con, node_id=nid)
    Sched.delete(con, node_id=nid)
    Clock.delete(con, node_id=nid)
    Metric.delete(con, node_id=nid)
    return n


def soft_delete_log(con, log_id):
    """Soft-delete a log and its metrics (the old `metric.log_id` CASCADE, now
    app-level). Tombstones, never removes. No commit. Returns the log rowcount."""
    n = Log.delete(con, id=log_id)
    Metric.delete(con, log_id=log_id)
    return n


_PRI_FILTER_ALIASES = {"P0": "A", "P1": "B", "P2": "C", "A": "A", "B": "B", "C": "C"}


def _parse_priority_filter(spec):
    """Parse a `--priority` value into a set of canonical letters. Accepts A/B/C and the
    P0/P1/P2 synonyms, case-insensitive, comma = any-of (`--priority A,B` → {A, B}). Exits with
    a hint on an unrecognized token (a silent no-match would hide a typo)."""
    out = set()
    for tok in spec.split(","):
        t = tok.strip().upper()
        if not t:
            continue
        if t not in _PRI_FILTER_ALIASES:
            die(f"invalid --priority '{tok}' (use A/B/C or P0/P1/P2; comma for any-of)")
        out.add(_PRI_FILTER_ALIASES[t])
    return out


def _parse_prop_cond(spec):
    """Parse one `--prop` spec into `(mode, key, value)`:
    - `key=value`           → ("exact",  key, value)  — node's prop key equals value (or value is
                              one of its comma-joined members, so `github.pr=164` hits `164,165`).
    - `group.` / `group.*`  → ("prefix", "group.", None) — node has any prop key in that namespace.
    - `key`                 → ("exists", key, None)   — node has that prop key (any value).
    """
    spec = spec.strip()
    if spec.endswith(".*"):
        return ("prefix", spec[:-1], None)        # group.* → prefix "group."
    if spec.endswith("."):
        return ("prefix", spec, None)             # group.  → prefix "group."
    if "=" in spec:
        k, _, v = spec.partition("=")
        return ("exact", k.strip(), v.strip())
    return ("exists", spec, None)


def make_node_filter(con, args):
    """Shared --tag / --para / --status / --priority / --prop filter, used by ls/tree/day/logs/agenda
    so every list/view command filters the same way (one definition, DESIGN §12 single entry point).
    Returns a memoized predicate `node_id -> bool`, or **None** when no filter flag is set —
    callers treat None as "no filtering", keeping unfiltered behavior byte-identical.
    `--tag` is comma-separated AND (node must carry every tag); `--status` / `--priority` are
    comma-separated OR; `--para` is sugar for an exact `type.para` prop condition (the
    responsibility role); `--prop` is repeatable, AND across conditions (exact key=value / key
    existence / `group.` namespace prefix). Classification beyond the PARA role (meetlog / habit /
    time level) filters through `--prop type.*`."""
    tag = getattr(args, "tag", None)
    status = getattr(args, "status", None)
    priority = getattr(args, "priority", None)
    # parse tags first so an effective-empty tag (--tag "" / "," / ",,") collapses to
    # "no tag filter" rather than an all-pass predicate (which would still route tree to
    # the filtered path); if nothing real is left to filter on, return None.
    wanted = {t.strip() for t in tag.split(",") if t.strip()} if tag else set()
    statuses = {s.strip().upper() for s in status.split(",") if s.strip()} if status else set()
    pris = _parse_priority_filter(priority) if priority else set()
    prop_conds = [_parse_prop_cond(s) for s in (getattr(args, "prop", None) or []) if s and s.strip()]
    # --para is sugar for an exact type.para prop condition, so it filters on the
    # same role that create writes.
    para = getattr(args, "para", None)
    if para:
        prop_conds.append(("exact", _nt.K_PARA, para))
    if not wanted and not statuses and not pris and not prop_conds:
        return None

    cache = {}

    def ok(nid):
        if nid in cache:
            return cache[nid]
        n = Node.get(con, nid)
        res = n is not None
        if res and statuses and n["status"] not in statuses:
            res = False
        if res and pris and n["priority"] not in pris:
            res = False
        if res and wanted:
            have = {r.tag for r in Tag.query(con, node_id=nid)}
            res = wanted <= have
        if res and prop_conds:
            props = {r.key: r.value for r in Prop.query(con, node_id=nid)}
            for mode, k, v in prop_conds:
                if mode == "exists":
                    if k not in props:
                        res = False; break
                elif mode == "prefix":
                    if not any(key.startswith(k) for key in props):
                        res = False; break
                else:  # exact: equals, or a member of the comma-joined value
                    pv = props.get(k)
                    if pv is None or (pv != v and v not in [x.strip() for x in pv.split(",")]):
                        res = False; break
        cache[nid] = res
        return res

    return ok


_RESERVED_LOG_TAGS = ("goal", "summary")  # reserved-tag logs: forward goal / backward summary


def _latest_typed_log(con, node_id, log_type):
    """The most recent log row of a given `tag` on a node — the 'current' value of a
    history-preserving reserved-tag log (goal / summary). Returns the Row
    (body, logged_at) or None. Each edit appends a new log, so history is kept and the
    latest one is the current value."""
    return Log.query_one(con, node_id=node_id, tag=log_type, order="logged_at DESC, id DESC")


# A `goal` log can carry a STRUCTURED, priority-ordered list of the node(s) it aims to deliver —
# stored as `goal` metrics on the log itself (value_num = node id, metric insertion order =
# priority). The caller supplies the ids explicitly; wl never parses them from the prose. Any
# number is allowed (we suggest ~5 for a month). The metric tag is the bare word `goal`, the same
# as the carrier log's own tag; the node's type (day / week / month / year) distinguishes the level
# at query time. `summary` logs are prose only. The latest goal log's `goal` metrics are current.
_GOAL_METRIC = "goal"


def _set_typed_log(con, node_id, log_type, body, goals=None):
    """Append a reserved-tag log (`tag` in goal/summary; history-preserving — each write appends,
    the latest is current). No commit; caller owns the transaction. For a `goal` log, `goals` (an
    ordered node-id list, priority first) is stored as one `goal` metric per id, in order. The
    caller supplies them — wl never parses the prose. Returns the log id."""
    log_id = Log.insert(con, {
        "node_id": node_id, "tag": log_type, "body": body, "logged_at": _tu.utc_now(),
    })
    if goals and log_type == "goal":
        for i in goals:   # insertion order = priority
            Metric.insert(con, {
                "log_id": log_id, "node_id": node_id, "tag": _GOAL_METRIC,
                "value_num": i, "at": _tu.utc_now(),
            })
    return log_id


def _log_goals(con, node_id):
    """The goal node ids of a node's CURRENT goal log — the `goal` metrics on its latest goal log,
    in priority order (metric insertion order). [] if there's no goal log or it carries none. The
    display numbers these to show priority; reverse queries hit the metric table directly
    (tag=goal), narrowing by node type for the level."""
    row = Log.query_one(con, node_id=node_id, tag="goal", order="id DESC")
    if not row:
        return []
    return [int(r.value_num) for r in Metric.query(con, log_id=row.id, tag=_GOAL_METRIC, order="id") if r.value_num is not None]


def _has_checkin(con, node_id, day):
    """True if the node has a check-in metric on the given day (YYYY-MM-DD).
    This is the structured 'done today' signal (G1) — replaces the old, too-loose
    'did any log exist that day' heuristic, so a stray note no longer counts as done."""
    return con.execute(
        f"SELECT 1 FROM metric WHERE node_id = ? AND tag = 'checkin' AND {_tu.local_day_sql('at')} = ? AND {_db.ALIVE} LIMIT 1",
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
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ? AND {_tu.local_day_sql('end_at')} = ? AND {_db.ALIVE}",
            (nid, day),
        ).fetchone()["s"]
    else:
        secs = _db.query(con, "clock", cols="COALESCE(SUM(elapsed_sec), 0) AS s", node_id=nid)[0]["s"]
    clock = int((secs or 0) / 60)

    # 2. plain-note log timestamp span (rough, max - min); exclude typed logs (metric
    #    carriers / goal / summary / …) so they don't inflate the span. Same-timestamp
    #    rows collapse (a batch backfill is one instant, not a span). Scoped to `day`
    #    when given so a multi-day task's span isn't counted on a single day's row.
    if day:
        rows = list(con.execute(
            f"SELECT DISTINCT logged_at FROM log WHERE node_id = ? AND tag IS NULL "
            f"AND {_tu.local_day_sql('logged_at')} = ? AND {_db.ALIVE} ORDER BY logged_at",
            (nid, day),
        ))
    else:
        rows = _db.query(con, "log", cols="DISTINCT logged_at", node_id=nid, tag=None, order="logged_at")
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
    return Node.exists(con, id=node_id)


def _require_node(con, node_id):
    """The canonical single-id existence guard: calls die() with '✗ node #N not found' if the
    node is missing. The single-id twin of :func:`_check_ids_exist` — every command that takes one
    node id routes its not-found check here instead of re-inlining the message."""
    if not _node_exists(con, node_id):
        die(f"node #{node_id} not found")


def _node_tags(con, nid):
    """Return the tag list for a node (insertion order)."""
    return [r.tag for r in Tag.query(con, node_id=nid)]


def _check_ids_exist(con, ids):
    """Batch existence check; calls die() if any id is missing. Used by multi-id commands."""
    for nid in ids:
        _require_node(con, nid)


# Core node fields (real columns / hierarchy / tags) that must NEVER become UDA props.
# Storing one as a prop (via `wl set` / `wl prop set` / an import `props:` block) used to
# silently create a *shadow* prop next to the real field — e.g. `wl set 574 status LATER` left
# the real status TODO while a misleading `status=LATER` prop showed up in `wl show`.
# Generalizes the original 'tags'-only guard. Keyed by lowercased name → the command
# that actually edits that field.
_RESERVED_PROP_KEYS = {
    "tag": "real tags — use `wl tag <id> +x -y`",
    "tags": "real tags — use `wl tag <id> +x -y`",
    "status": "node status — use `wl done` / `defer` / `reopen` / `start` / `wait` / `cancel`",
    "priority": "node field — use `wl node edit <id> -p A|B|C`",
    "title": "node field — use `wl node edit <id> --title …`",
    "body": "node field — use `wl node edit <id> --body …`",
    "scheduled": "node field — use `wl sched` / `wl defer` / `wl node edit <id> --scheduled …`",
    "scheduled_date": "node field — use `wl sched` / `wl defer` / `wl node edit <id> --scheduled …`",
    "deadline": "node field — use `wl node edit <id> --deadline …`",
    "deadline_date": "node field — use `wl node edit <id> --deadline …`",
    "parent": "node hierarchy — use `wl node reparent <id> <parent>`",
    "parent_id": "node hierarchy — use `wl node reparent <id> <parent>`",
    "closed_at": "managed automatically by `wl done` / `reopen`",
    "created_at": "immutable node field",
    "id": "immutable node id",
    "deleted_at": "managed by `wl node rm` / restore",
}


def _reserved_prop_hint(key):
    """If `key` collides with a core node field (so storing it as a UDA prop would shadow the
    real field), return the corrective hint; else None. Single source of truth for the guard
    shared by cmd_set (wl set / wl prop set) and the importers."""
    return _RESERVED_PROP_KEYS.get((key or "").strip().lower())


def _upsert_prop(con, nid, key, value):
    """Unified prop UPSERT (no commit; caller controls the transaction). Batch-friendly.
    `_set_prop` is the commit version for single daily operations. Rejects reserved node-field
    names so a prop can't shadow a real column — the universal backstop behind the
    nicer cmd_set / importer pre-checks."""
    hint = _reserved_prop_hint(key)
    if hint:
        raise ValueError(
            f"'{key}' is reserved, not a free UDA prop — {hint} "
            f"(storing it as a prop would create a misleading shadow of the real field)")
    # The one chokepoint for the type.* / date.* reserved namespace: validate + normalize
    # the value here so NO write path can poison a reserved key with an out-of-domain value
    # (a bad type.para / type.date would silently break tree-building + views). Non-reserved
    # user props pass straight through (free values).
    value = _nt.validate_prop(key, value)
    Prop.upsert(con, {"node_id": nid, "key": key, "value": value})


# --- task↔task relations (relation.* props) ---------------------------------
# Relations between tasks (split-from / split-into / related) are stored as
# `relation.<type>` UDA props whose value is a comma-separated id list. Unlike
# ancestors (the parent/child hierarchy) they express derivation / association
# *across* the tree: this task was split out of / split into / relates to that one.
# `split-from` ↔ `split-into` are inverses; `related` is symmetric. `wl relation`
# writes BOTH sides; the view below ALSO derives the reverse from other nodes' props,
# so even a hand-set one-sided prop still renders bidirectionally.
_RELATION_TYPES = {
    # type arg -> (own key on this node, inverse key written on the other node)
    "split-from": ("relation.split_from", "relation.split_into"),
    "split-into": ("relation.split_into", "relation.split_from"),
    "related":    ("relation.related",    "relation.related"),
}
# own prop key -> display label (also fixes display order)
_RELATION_KEY_LABEL = {
    "relation.split_from": "split-from",
    "relation.split_into": "split-into",
    "relation.related":    "related",
}
# the other node's key -> the label it implies FOR nid (A.split_from ∋ nid ⇒ nid.split-into ∋ A)
_RELATION_REVERSE_LABEL = {
    "relation.split_from": "split-into",
    "relation.split_into": "split-from",
    "relation.related":    "related",
}


def _parse_id_list(value):
    """Parse a comma-separated id-list prop value into list[int] — order-preserving,
    deduped, skipping blanks / non-ints."""
    out, seen = [], set()
    for tok in (value or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            i = int(tok)
        except ValueError:
            continue
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _prop_value(con, nid, key):
    """The live value of prop (nid, key), or None."""
    r = Prop.query_one(con, node_id=nid, key=key)
    return r.value if r else None


def node_props(con, nid, *, include_deleted=False):
    """A node's props as a ``{key: value}`` dict — the read primitive behind the type.*
    accessors (node_type / role / time-level derivation). Live props only by default;
    ``include_deleted=True`` also returns tombstoned prop rows (used when classifying a
    tombstoned node, whose props are tombstoned to match it)."""
    return {r.key: r.value for r in Prop.query(con, node_id=nid, include_deleted=include_deleted)}




#: the reserved date keys whose write must trigger a time-node re-sync (see sync_time_node_dates)
DATE_SYNC_KEYS = frozenset({_nt.K_DATE, _nt.K_PERIOD})


def sync_time_node_dates(con, nid):
    """Re-derive a node's ``date.*`` (period / start / end) to EXACTLY match its current
    ``type.date`` level + period source, clearing any stale ``date.*`` a prior level/period left
    behind. Period source is the existing ``date.period`` IF it's valid for the (possibly just-
    changed) level, else the node title (mirrors ``create_node``). No-op for a node with no
    ``type.date`` level.

    This is the SINGLE place that keeps a time node's span coherent after a *generic prop write*
    of ``type.date`` / ``date.period`` — call it from every such path (``wl set``, bulk import/
    apply), so setting a time level via ``--prop`` produces a findable time node, not a half one.
    (``create_node`` does its own inline completion for the create path.)"""
    props = node_props(con, nid)
    level = props.get(_nt.K_DATE)
    if not level:
        return
    period = props.get(_nt.K_PERIOD)
    if not (period and _nt.valid_period(level, period)):
        period = Node.get(con, nid).title
    want = _nt.date_props_for(level, period)
    for key in (_nt.K_PERIOD, _nt.K_START, _nt.K_END):
        if key in want:
            _upsert_prop(con, nid, key, want[key])
        elif key in props:
            Prop.delete(con, node_id=nid, key=key)


def node_type_from_props(props) -> str:
    """Derive a node's single representative type token from its orthogonal ``type.*`` props — the
    bridge for the (few) internal paths + the display tag that still want one label while the
    orthogonal model is the source of truth. Precedence: a PARA role, else a time level, else a
    soft type (habit/meetlog), else a custom ``type.<x>``, else a bare ``task``. Pure (props in,
    token out); the data-layer home for the single-token type derivation that used to live in node_types (kept out of the pure
    types module so that module stays purely orthogonal accessors)."""
    if _nt.K_PARA in props:
        return props[_nt.K_PARA]
    if _nt.K_DATE in props:
        return props[_nt.K_DATE]
    if _nt.K_HABIT in props:
        return "habit"
    if _nt.K_MEETLOG in props:
        return "meetlog"
    # a custom classification carried as a generic type.<x> existence prop (e.g. `--prop type.recipe`)
    for k in sorted(props):
        if k.startswith(_nt.TYPE_NS) and k not in _nt.RESERVED_KEYS and len(k) > len(_nt.TYPE_NS):
            return k[len(_nt.TYPE_NS):]   # non-empty suffix only (a bare "type." is not a type)
    return "task"


def node_type(con, n):
    """The node's single representative type token, derived from its ``type.*`` props.
    ``n`` is a node row or an id."""
    nid = n if isinstance(n, int) else n["id"]
    return node_type_from_props(node_props(con, nid))


def nodes_with_type(con, key, value=None, *, cols="*", order=None):
    """Live nodes carrying the prop ``key`` (optionally ``=value``) — the column-free single-value
    classification lookup (``type.para=project``, ``type.date=day``, ``type.habit`` existence, …).
    Tombstone-filtered on both node and prop."""
    sql = (f"SELECT {cols} FROM node n WHERE n.{_db.ALIVE} AND EXISTS("
           f"SELECT 1 FROM prop WHERE node_id=n.id AND key=? AND {_db.ALIVE}"
           + (" AND value=?" if value is not None else "") + ")")
    params = [key] + ([value] if value is not None else [])
    if order:
        sql += f" ORDER BY {order}"
    return con.execute(sql, params).fetchall()


def time_node_by_period(con, level, period, *, cols="*"):
    """The live time node of ``level`` whose ``date.period`` == ``period`` (e.g. day
    ``2026-06-14``, month ``2026-06``) — the column-free time-node lookup, matching on
    ``type.date`` + ``date.period``. Returns a Row, or None."""
    return con.execute(
        f"SELECT {cols} FROM node n WHERE n.{_db.ALIVE} "
        f"AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.date' AND value=? AND {_db.ALIVE}) "
        f"AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='date.period' AND value=? AND {_db.ALIVE}) "
        "ORDER BY n.id LIMIT 1", (level, period)).fetchone()


def node_has_type(con, nid, key, value=None):
    """Whether node ``nid`` has the (live) prop ``key`` (optionally ``=value``)."""
    if value is None:
        return Prop.exists(con, node_id=nid, key=key)
    return Prop.exists(con, node_id=nid, key=key, value=value)


def workitem_sql(alias="n"):
    """SQL predicate: the node (table aliased ``alias``) has DERIVED type task/habit/meetlog — an
    actionable work item — expressed purely from type.* props (column-free). Must match
    node_types.node_type_from_props's precedence (para > date > habit > meetlog > custom > task) exactly,
    so it equals ``node_type_from_props(props) IN ('task','habit','meetlog')``: a node is a work item iff
    its type.para is 'task' (the role wins regardless of any other dim), OR it has no para AND no
    time level AND no custom type.<x> (a bare node, or a pure habit/meetlog — those reserved keys
    aren't the 'custom' the last clause excludes)."""
    def _ex(cond):  # EXISTS a live prop on this node matching cond
        return "EXISTS(SELECT 1 FROM prop WHERE node_id=" + alias + ".id AND " + cond + " AND " + _db.ALIVE + ")"
    para_task = _ex("key='type.para' AND value='task'")
    has_para = _ex("key='type.para'")
    has_date = _ex("key='type.date'")
    has_habit = _ex("key='type.habit'")
    has_meetlog = _ex("key='type.meetlog'")
    # length(key) > 5 mirrors node_type_from_props's `len(k) > len(TYPE_NS)` guard: a bare 'type.' key
    # (empty suffix) is NOT a custom type, so it must not exclude the node from the work-item set.
    has_custom = _ex("key LIKE 'type.%' AND length(key) > 5 "
                     "AND key NOT IN ('type.para','type.date','type.habit','type.meetlog')")
    # node_type_from_props precedence para > date > habit > meetlog > custom > task, restricted to the
    # task/habit/meetlog set: type.para='task' (role wins outright), OR — with no para and no time
    # level — a habit, a meetlog, or a bare node (no custom type.<x>). habit/meetlog outrank a
    # co-present custom type.<x>, so they stay in the set even when a custom prop also exists.
    return (f"({para_task} OR (NOT {has_para} AND NOT {has_date} "
            f"AND ({has_habit} OR {has_meetlog} OR NOT {has_custom})))")


def _add_id_to_prop_list(con, nid, key, add_id):
    """Add `add_id` to the comma-list prop (nid, key); no-op if already present.
    No commit. Returns True if it was added."""
    ids = _parse_id_list(_prop_value(con, nid, key))
    if add_id in ids:
        return False
    ids.append(add_id)
    _upsert_prop(con, nid, key, ",".join(str(i) for i in ids))
    return True


def _remove_id_from_prop_list(con, nid, key, rm_id):
    """Remove `rm_id` from the comma-list prop (nid, key); soft-delete the prop when it
    becomes empty (rather than leave an empty-string value). No commit. Returns True if
    something was removed."""
    ids = _parse_id_list(_prop_value(con, nid, key))
    if rm_id not in ids:
        return False
    ids = [i for i in ids if i != rm_id]
    if ids:
        _upsert_prop(con, nid, key, ",".join(str(i) for i in ids))
    else:
        Prop.delete(con, node_id=nid, key=key)
    return True


def relation_view(con, nid):
    """Resolved bidirectional relations for a node: an ordered dict
    {'split-from': [ids], 'split-into': [ids], 'related': [ids]}. Unions the node's own
    relation.* props with the reverse derived from every other node's props
    (A.split_into ∋ nid ⇒ nid.split-from ∋ A), so one-sided data still shows both ways.
    Each list is order-preserving + deduped, excludes nid itself, and only includes live
    nodes (a relation to a soft-deleted node is dropped from the view)."""
    merged = {t: [] for t in _RELATION_TYPES}
    seen = {t: set() for t in _RELATION_TYPES}

    def _add(label, i):
        if i == nid or i in seen[label] or not _node_exists(con, i):
            return
        seen[label].add(i)
        merged[label].append(i)

    # own props
    for r in Prop.query(con, node_id=nid):
        lbl = _RELATION_KEY_LABEL.get(r.key)
        if lbl:
            for i in _parse_id_list(r.value):
                _add(lbl, i)
    # reverse: any other node whose relation.* list points at nid
    for r in Prop.query(con, key__like="relation.%"):
        lbl = _RELATION_REVERSE_LABEL.get(r.key)
        if lbl and nid in _parse_id_list(r.value):
            _add(lbl, r.node_id)
    return merged


def _backrels(con, nid):
    """Back-relations ('what links here' / 维基百科链入): other nodes whose TEXT mentions this
    node's id — a `#<nid>` or `WL#<nid>` reference in a log body or a node body. Returns sorted
    distinct node ids, excluding self. A bare `#` or a `WL#` prefix counts; a letter run like
    `PR#`/`LUM-` does NOT (so a GitHub PR / Linear ref isn't mistaken for a node ref). Unlike the
    stored relation.* props, these are MACHINE-DERIVED (computed by scanning text), so the show
    view marks the row with a leading `=` + italic to set it apart from the real relations."""
    import re
    # candidates via a cheap LIKE, then a word-boundary regex confirms it's a real node ref
    pat = re.compile(rf"(?<![A-Za-z0-9])(?:WL)?#0*{nid}(?!\d)")
    found = set()
    like = f"%#{nid}%"
    for src_id, body in con.execute(
        f"SELECT DISTINCT node_id, body FROM log WHERE {_db.ALIVE} AND body LIKE ?", (like,)):
        if src_id != nid and pat.search(body or ""):
            found.add(src_id)
    for src_id, body in con.execute(
        f"SELECT id, body FROM node WHERE {_db.ALIVE} AND body LIKE ?", (like,)):
        if src_id != nid and pat.search(body or ""):
            found.add(src_id)
    return sorted(found)


def _strip_wikilink(doc):
    """Strip an outer ``[[ ... ]]`` wrapper (repeatedly) plus surrounding whitespace from a
    vault-doc name, so ``[[X]]`` and ``X`` store identically and an already-wrapped value
    can't become ``[[[[X]]]]``."""
    s = (doc or "").strip()
    while len(s) >= 4 and s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2].strip()
    return s


def _upsert_link(con, nid, doc):
    """Add a vault-doc link to a node (idempotent revive-or-insert), normalizing the doc
    name via `_strip_wikilink` first. The single chokepoint for link writes. No commit.
    Returns the stripped doc name (callers echo it)."""
    name = _strip_wikilink(doc)
    Link.upsert(con, {"node_id": nid, "vault_doc": name})
    return name


def _delete_link(con, nid, doc):
    """Soft-delete a vault-doc link, matching by the normalized (stripped) doc name so a
    link added as `X` is removable whether the caller passes `X` or `[[X]]`. No
    commit. Returns (stripped_name, rowcount)."""
    name = _strip_wikilink(doc)
    return name, Link.delete(con, node_id=nid, vault_doc=name)


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
