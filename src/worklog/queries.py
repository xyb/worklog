"""sqlite-backed query helpers for worklog.

These take a sqlite3.Connection as an argument and don't touch any
module-level state, so they're safe to import from any module that
already has a connection. They sit between the pure utilities in
helpers.py and the command handlers in cli.py.
"""
from __future__ import annotations

import contextlib
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
            # date given, no explicit time: stamp the CURRENT wall-clock time ON that date, so the
            # log keeps a real instant. A bare date loses intra-day ordering and renders no @HH:MM
            # (logged_at must be a full UTC instant — see the invariant in DESIGN §21). day-grouping
            # still lands on `date`: it's a complete local datetime round-tripped through UTC.
            logged_at = _tu.local_to_utc(f"{date} {_tu.local_now()[11:19]}")
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


def _has_tag(con, nid, tag):
    return Tag.exists(con, node_id=nid, tag=tag)


def _read_nodes_by_ids(con, ids, *, cols="*", order=None, limit=None):
    """Read live nodes for a precomputed id set — the shared tail of the prop/tag decompositions.
    Returns list[Node] for the default full-row read (ready for a renderer); a `cols=` projection
    returns raw list[Row], since a partial row can't satisfy Node.from_row. Empty ids → []."""
    if not ids:
        return []
    rows = _db.query(con, "node", cols=cols, order=order, limit=limit, id__in=ids)
    return [Node.from_row(r) for r in rows] if cols == "*" else rows


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
        # restrict by DERIVED type (column-free): batch-classify the candidate ids in one read, then filter
        want = set(types)
        type_map = classify_types(con, ids)
        ids = [i for i in ids if type_map[i] in want]
        if not ids:
            return []
    return _read_nodes_by_ids(con, ids, cols=cols, order=order)


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


def _has_checkin_between(con, node_id, start, end):
    """True if the node has a check-in metric on any day in ``[start, end]`` (YYYY-MM-DD,
    inclusive). The period-level 'done' signal — a day is just the start == end case."""
    return con.execute(
        f"SELECT 1 FROM metric WHERE node_id = ? AND tag = 'checkin' AND {_db.ALIVE} "
        f"AND {_tu.local_day_sql('at')} BETWEEN ? AND ? LIMIT 1",
        (node_id, start, end),
    ).fetchone() is not None


def _has_checkin(con, node_id, day):
    """True if the node has a check-in metric on the given day (YYYY-MM-DD).
    This is the structured 'done today' signal (G1) — replaces the old, too-loose
    'did any log exist that day' heuristic, so a stray note no longer counts as done."""
    return _has_checkin_between(con, node_id, day, day)

def _last_checkin(con, node_id):
    """The most recent check-in date (YYYY-MM-DD local) for a node, or None if never checked in.
    Read-time — the source of truth is the checkin metrics, never a cached prop, so deleting a
    log (`wl unlog`) can never leave a stale 'last done' behind."""
    row = con.execute(
        f"SELECT MAX({_tu.local_day_sql('at')}) AS d FROM metric WHERE node_id = ? AND tag = 'checkin' AND {_db.ALIVE}",
        (node_id,),
    ).fetchone()
    return row["d"] if row and row["d"] else None


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
        span = max(0, _tu.elapsed_sec(rows[0]["logged_at"], rows[-1]["logged_at"]) or 0) // 60

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


@contextlib.contextmanager
def _immediate_txn(con, *, busy_ms=5000):
    """Run a read-modify-write while holding SQLite's write lock, so a concurrent process can't
    interleave. `BEGIN IMMEDIATE` grabs the RESERVED lock UP FRONT (before the SELECT), so two
    callers racing the same check-then-set serialize: the loser blocks until the winner commits,
    then re-reads and sees the committed state. Without it, both SELECTs (deferred isolation takes
    no lock on a read) see the old state and the second write silently clobbers the first — the
    exact hole that let two sessions both `claim` one ticket. `busy_ms` bounds the wait so the loser
    reads-then-decides rather than erroring with 'database is locked'. Commits on success, rolls
    back on any exception (including a `die()`/SystemExit from inside the block). Mirrors the
    isolation_level dance the migration runner uses — Python's sqlite3 won't issue BEGIN under its
    default managed mode."""
    prev = con.isolation_level
    con.isolation_level = None
    con.execute(f"PRAGMA busy_timeout = {int(busy_ms)}")
    try:
        con.execute("BEGIN IMMEDIATE")
        yield
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.isolation_level = prev


def _insert_clock_span(con, nid, mins, end_ts):
    """Record a COMPLETED clock of `mins` minutes ending at `end_ts` (a UTC instant) — the start is
    derived, `elapsed_sec` stored. Returns the clock id. No commit. Single source for `wl spent` and
    apply's `spent` field-op, so the two entry points can't build the span differently."""
    start_ts = _tu.shift_ts(end_ts, minutes=-mins)
    return Clock.insert(con, {"node_id": nid, "start_at": start_ts, "end_at": end_ts,
                              "elapsed_sec": mins * 60})


def _norm_log_tag(raw):
    """Normalise a log `tag` token: `-` / `note` / `none` / empty all mean "a plain note" (NULL).
    Single source for `wl retag` and apply's `retag` field-op."""
    raw = (raw or "").strip()
    return None if raw.lower() in ("", "-", "note", "none") else raw


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
# Relations between tasks (block / split / related) are stored as `relation.<type>` UDA
# props whose value is a comma-separated id list. Unlike ancestors (the parent/child
# hierarchy) they express derivation / association / dependency *across* the tree: this
# task blocks that one / was split out of that one / relates to that one. Each type is
# SINGLE-WRITE — `wl relation A <type> B` writes ONLY on A — the reverse is NEVER
# written, only derived at read time by `graph.relation_view`, so it can never go stale.
# `block` and `split` each get their own derived-reverse label (`=blocked-by` /
# `=split-from`); `related`'s does not — a one-sided `related` edge folds into
# `=backrels` (`graph._backrels`) instead, since "A related me" and "text mentions me"
# are the same kind of inbound, machine-derived fact. `wl relation A related B` and a
# separate `wl relation B related A` are two independent edges (neither implies or
# dedupes the other) — that is what makes `related` "symmetric in meaning" despite
# single-write. `block` is the only type checked for cycles at write time
# (`graph._block_cycle_check`) — it is a real dependency DAG (a cycle would mean two
# tasks each waiting on the other, forever unready); `split` is a loose lineage marker
# with no logic riding on it, and `related` is symmetric, so neither is worth the check
# (see graph.py's cycle-check docstring for the full reasoning).
_RELATION_TYPES = {
    # type arg -> own prop key (the only key ever written)
    "block":   "relation.block",
    "split":   "relation.split",
    "related": "relation.related",
}
# own prop key -> type name (display order + the "is this a relation.* key" test used to
# exclude relation props from the generic props block)
_RELATION_KEY_LABEL = {
    "relation.block":   "block",
    "relation.split":   "split",
    "relation.related": "related",
}
# own prop key -> the DERIVED label shown on the node(s) it points at (computed by
# graph.relation_view, never itself stored). No entry for relation.related: its reverse
# is folded into `=backrels` instead (graph._backrels), not a dedicated label.
_RELATION_DERIVED_LABEL = {
    "relation.block": "blocked_by",
    "relation.split": "split_from",
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
    Decomposed into two simple single-table reads (prop lookup for the carrying node ids, then a
    node read) instead of an EXISTS subquery. Returns list[Node] for the default full-row read; a
    ``cols=`` projection returns raw list[Row]. Tombstone-filtered on both node and prop (a deleted
    node has its props tombstoned too, so the prop-side filter already excludes it)."""
    conds = {"cols": "node_id", "key": key}
    if value is not None:
        conds["value"] = value
    ids = sorted({r["node_id"] for r in _db.query(con, "prop", **conds)})
    return _read_nodes_by_ids(con, ids, cols=cols, order=order)


def time_node_by_period(con, level, period, *, cols="*"):
    """The live time node of ``level`` whose ``date.period`` == ``period`` (e.g. day
    ``2026-06-14``, month ``2026-06``) — the column-free time-node lookup, matching on
    ``type.date`` + ``date.period``. Decomposed into two prop lookups intersected to the node id,
    then a node read — no EXISTS subquery. Returns a Node, or None."""
    by_level = {r["node_id"] for r in _db.query(con, "prop", cols="node_id", key="type.date", value=level)}
    by_period = {r["node_id"] for r in _db.query(con, "prop", cols="node_id", key="date.period", value=period)}
    ids = sorted(by_level & by_period)
    rows = _read_nodes_by_ids(con, ids, cols=cols, order="id", limit=1)
    return rows[0] if rows else None


def node_has_type(con, nid, key, value=None):
    """Whether node ``nid`` has the (live) prop ``key`` (optionally ``=value``)."""
    if value is None:
        return Prop.exists(con, node_id=nid, key=key)
    return Prop.exists(con, node_id=nid, key=key, value=value)


_WORKITEM_TYPES = ("task", "habit", "meetlog")


def classify_types(con, node_ids):
    """Map each node id to its derived type token — the on-demand batch classification primitive
    (replaces the `workitem_sql` EXISTS predicate and per-node `node_type` in batch paths). ONE
    read of *just these ids'* `type.*` props (`node_id__in`) + the pure `node_type_from_props`: no
    per-node query (not N+1), no full-table scan (only the ids handed in). The caller picks the
    batch by need — a project's children, a day's logged nodes, a status-filtered set — so the read
    scales to what's used. (A deleted node's props are tombstoned too, so a missing/empty entry
    classifies as a bare `task`; callers pre-filter to live nodes, so that case doesn't arise.)"""
    node_ids = list(node_ids)
    if not node_ids:
        return {}
    type_props = {}
    for r in _db.query(con, "prop", cols="node_id, key, value", key__like="type.%", node_id__in=node_ids):
        type_props.setdefault(r["node_id"], {})[r["key"]] = r["value"]
    return {nid: node_type_from_props(type_props.get(nid, {})) for nid in node_ids}


def filter_workitems(con, nodes):
    """Keep only the work items (derived type task/habit/meetlog) from a batch of node rows, by
    classifying that batch (see classify_types). Input order preserved."""
    nodes = list(nodes)
    types = classify_types(con, [n["id"] for n in nodes])
    return [n for n in nodes if types.get(n["id"]) in _WORKITEM_TYPES]


def workitem_ids(con, node_ids):
    """The subset of node_ids whose derived type is a work item — for filtering rows that only carry
    a node_id (e.g. a `log JOIN node` result) without rebuilding node objects."""
    return {nid for nid, t in classify_types(con, node_ids).items() if t in _WORKITEM_TYPES}


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
