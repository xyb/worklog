"""worklog commands: state group."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .. import render
from .. import timeutil as _tu
from .output import _is_json, _emit_json
from .. import db_table as _db
from .. import node_types as _nt
from ..helpers import (
    _apply_top_limit,
    _fmt_dur,
    _is_brief,
    _log_full,
    _norm_sched,
    _resolve_at_ts,
    _resolve_concrete_date,
    _resolve_log_tail,
    _resolve_window,
    _sched_anchor,
    _sched_display,
    _sched_level,
    _sched_sort_key,
    _status_marker,
    _term_width,
    _truncate_log_body,
    _display_width,
    GENERIC_TAGS,
)
from ..queries import (
    _ancestors_chain,
    _check_ids_exist,
    _collect_descendants,
    soft_delete_log,
    soft_delete_node,
    _has_tag,
    _insert_log,
    _node_bucket,
    _node_clock_min,
    _node_exists,
    _require_node,
    _node_plan,
    _node_project,
    _node_tags,
    _project_members,
    _sec_group,
    _status_filter_sql,
    _upsert_prop,
    node_type,
    node_type_from_props,
    sync_time_node_dates,
    _strip_wikilink,
    _upsert_link,
    _delete_link,
    _set_typed_log,
    _RESERVED_LOG_TAGS,
    _reserved_prop_hint,
    _RELATION_TYPES,
    _add_id_to_prop_list,
    _remove_id_from_prop_list,
    relation_view,
    _backrels,
)
from ..node import create_node
from .. import node_types as _nt
from .metric import attach_metric_specs, checkin_metric, _CARRIER_TYPE
from ..render import (
    _PRI_STYLE,
    _STATUS_STYLE,
    _RICH_AVAIL,
    _resolve_theme,
    THEMES,
    _c,
    _hl,
    _pri_marker,
    _node_line,
    _print_truncation_hint,
    _snippet,
    out,
)
from ..xdg import _resolve_db_path, _resolve_aliases_path, _xdg_data_home, _xdg_config_home, _xdg_state_home

# Lazy access to cli module (for DB wrappers + module state).
# Used at function call time (not at import) to avoid the cli ↔ commands
# import cycle.
from .. import cli as _cli  # noqa: E402


def _norm_title(s):
    """Normalize a title for fuzzy comparison: lowercase, drop spaces/punctuation."""
    import re as _re
    return _re.sub(r"[\s\W_]+", "", (s or "").lower())

def _find_similar_open(con, title, node_type):
    """Open (non-terminal) task/project nodes whose title looks like a duplicate of
    `title`: identical after normalization, or one normalized title contains the other
    (with the shorter ≥4 chars, to avoid trivial substring noise). Best-effort, used
    only to warn before creating a possible duplicate."""
    if node_type not in ("task", "project"):
        return []
    nt = _norm_title(title)
    if not nt:
        return []
    rows = con.execute(
        # derived type IN ('task','project'): an explicit type.para=task/project,
        # OR a bare node (no type.* classification at all → a plain task).
        "SELECT * FROM node n WHERE "
        "(EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.para' "
        f"        AND value IN ('task','project') AND {_db.ALIVE}) "
        f" OR NOT EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key LIKE 'type.%' AND {_db.ALIVE})) "
        # project status is NULL (DESIGN §40); NULL NOT IN (...) is NULL, not TRUE, so
        # guard explicitly or projects would never match.
        f"AND (n.status IS NULL OR n.status NOT IN ('DONE','CANCELED')) AND n.{_db.ALIVE} ORDER BY id"
    ).fetchall()
    hits = []
    for r in rows:
        rn = _norm_title(r["title"])
        if not rn:
            continue
        if rn == nt:
            hits.append(r)
        elif len(min(rn, nt, key=len)) >= 4 and (rn in nt or nt in rn):
            hits.append(r)
    return hits

def _add_sched(con, node_id, args):
    """`wl add --sched`: schedule the new node for a day (writes the sched table directly).
    Returns the ` @<date>` echo hint, or '' when no --sched."""
    if not getattr(args, "sched", None):
        return ""
    try:
        d = _resolve_concrete_date(args.sched)
    except ValueError:
        sys.exit(f"✗ invalid --sched date '{args.sched}' (use YYYY-MM-DD / today / tomorrow / day-after-tomorrow / yesterday)")
    _db.insert(con, "sched", {"node_id": node_id, "on_date": d, "created_at": _tu.utc_now()})
    return " " + _c(f"@{d}", "planned")


def _add_link(con, node_id, args):
    """`wl add --link`: attach a vault doc. Returns the ` → [[doc]]` hint, or '' when no/empty --link."""
    if not getattr(args, "link", None):
        return ""
    link_doc = _strip_wikilink(args.link)
    if not link_doc:
        return ""
    _upsert_link(con, node_id, link_doc)
    return " → " + _c(f"[[{link_doc}]]", "meta")


def _add_relations(con, node_id, args):
    """`wl add --relation` (repeatable): write task↔task relation(s) on both sides at creation.
    Returns the ` + N relation(s)` hint, or '' when none."""
    rel_specs = getattr(args, "relation", None)
    if not rel_specs:
        return ""
    rel_n = 0
    for spec in rel_specs:
        rtype, ids = _parse_relation_spec(spec)
        _check_ids_exist(con, ids)
        rel_n += len(_apply_relation(con, node_id, rtype, ids))
    return f" + {rel_n} relation(s)" if rel_n else ""


def _add_log_and_metrics(con, node_id, args, at_ts):
    """`wl add --log` / `--metric`: insert the log (sharing the resolved --at instant), then attach
    datapoint(s) onto that carrier — or a dedicated `metric` carrier log when there's no --log, so
    every datapoint still hangs off a log. Returns (log_hint, metric_hint)."""
    log_hint = ""
    created_log_id = None
    log_body = getattr(args, "log", None)
    if log_body and log_body.strip():
        if at_ts:
            # at_ts is already a UTC instant — insert directly (don't round-trip through
            # _insert_log's dict path, which would re-apply local→UTC)
            created_log_id = _db.insert(con, "log", {"node_id": node_id, "logged_at": at_ts, "body": log_body.strip()})
        else:
            created_log_id = _insert_log(con, node_id, log_body.strip())
        log_hint = " + log"
    metric_hint = ""
    specs = getattr(args, "metric", None)
    if specs:
        if created_log_id is not None:
            mlog_id = created_log_id
        else:
            mlog_id = _db.insert(con, "log", {
                "node_id": node_id, "logged_at": at_ts or _tu.utc_now(),
                "body": "", "tag": _CARRIER_TYPE,
            })
        nm = attach_metric_specs(con, mlog_id, node_id, specs, at=at_ts or None)
        metric_hint = f" + {nm} metric(s)"
    return log_hint, metric_hint


def cmd_add(args, con):
    if not args.title or not args.title.strip():
        sys.exit("✗ title cannot be empty")
    args.title = args.title.strip()
    # The node's classification is the type.* props written below. `para` is just a local token
    # for the duplicate-check + the echo line: --para names a PARA role; otherwise a bare add is a
    # loose `task` (any other classification comes via --prop and is derived afterwards from the
    # props actually written).
    para = getattr(args, "para", None)
    if args.sched and args.scheduled:
        sys.exit("✗ --sched (precise, writes sched table) and --scheduled (rough hint, writes node.scheduled_date) are mutually exclusive; use --sched day-to-day")
    tags = [t.strip() for t in (args.tag or "").split(",") if t.strip()]
    props = {}
    if args.proj:
        props["project"] = args.proj
    # gather --prop K=V now (before the status default) so the node's classification is known up front
    for spec in (getattr(args, "prop", None) or []):
        key, _, val = spec.partition("=")
        key = key.strip()
        val = val.strip()
        if key:
            if key in props and props[key] != val:
                # don't silently drop a conflicting earlier value (e.g. --proj X + --prop project=Y)
                out(_c(f"⚠ {key}={val} overrides earlier {key}={props[key]}", "later"))
            props[key] = val
    if args.deadline:
        deadline = args.deadline
    else:
        deadline = None

    # Derive the node's classification ONCE from para + the type.* props just gathered, and reuse
    # it for both the TODO-status default and the duplicate probe — so `wl add 2026-06-20 --prop
    # type.date=day` is a date node (not a TODO task) AND isn't dup-checked as a task.
    _eff = dict(props)
    if para:
        _eff[_nt.K_PARA] = para
    derived_type = node_type_from_props(_eff)
    # Default a work item (task / habit) to TODO — but NOT a time node / meetlog / area / project.
    status = args.status
    if not status and derived_type in ("task", "habit"):
        status = "TODO"
    # Duplicate check (warn only, never block): a related open task/project may already exist,
    # pinned at @month/@someday and easy to miss. Probe with the DERIVED type so a date/meetlog
    # add isn't searched as a task. Run before insert so the new node can't match itself.
    similar = _find_similar_open(con, args.title, derived_type)
    # --done overrides status directly (one-shot retrospective entry)
    if getattr(args, "done", False):
        status = "DONE"

    # --at affects --log timestamp + (if --done) closed_at
    at_ts = None
    if getattr(args, "at", None):
        try:
            at_ts = _resolve_at_ts(args.at)
        except ValueError as e:
            sys.exit(f"✗ {e}")
    closed_at = None
    if status == "DONE":
        closed_at = at_ts if at_ts else "__NOW__"  # placeholder, SQL below decides

    try:
        scheduled = _norm_sched(args.scheduled)
    except ValueError as e:
        sys.exit(f"✗ {e}")

    # created_at is always stamped (UTC); closed_at only when --done (now) or --done --at (a
    # resolved instant). One `now` read shared by both so a created-and-done task has
    # created_at == closed_at.
    now = _tu.utc_now()
    # The ONE create path: node.create_node is the only INSERT INTO node. Classification is
    # --para (type.para) + props (type.*), validated there.
    try:
        node_id = create_node(
            con, title=args.title, parent_id=args.parent, status=status,
            priority=args.priority, scheduled_date=scheduled, deadline_date=deadline,
            body=args.body, created_at=now,
            closed_at=(now if closed_at == "__NOW__" else (closed_at or None)),
            para=para, props=props)
    except ValueError as e:
        sys.exit(f"✗ {e}")
    for t in tags:
        _db.upsert(con, "tag", {"node_id": node_id, "tag": t}, key=("node_id", "tag"))
    # creation-time side effects, each returning its echo hint (order fixed by the output line below)
    sched_hint = _add_sched(con, node_id, args)
    link_hint = _add_link(con, node_id, args)
    rel_hint = _add_relations(con, node_id, args)
    log_hint, metric_hint = _add_log_and_metrics(con, node_id, args, at_ts)

    con.commit()
    if _is_json(args):
        from .query import _node_summary_dict
        _emit_json(_node_summary_dict(con, _db.get(con, "node", node_id)))
        return
    st = (" " + _c(f"[{status}]", _STATUS_STYLE.get(status, "todo"))) if status else ""
    # echo the node's DERIVED type (post --prop) so a `--prop type.habit` add reports "habit", not "task"
    echo_type = node_type(con, node_id)
    out(_c("✓", "done") + " " + _c(f"#{node_id}", "id") + " " + _c(f"{echo_type} '{args.title}'")
        + st + sched_hint + link_hint + rel_hint + log_hint + metric_hint
        + _c(f"  @{_tu.local_now()[:16]}", "meta"))   # stamp "now" so the caller (esp. an AI) sees the real current time
    if similar:
        out(_c(f"⚠ {len(similar)} similar open {echo_type}(s) already exist — reuse instead of duplicating?", "later"))
        for r in similar[:5]:
            out("  " + _node_line(con, r, sched=True))
        out(_c("  if it's the same thing: wl sched <id> <day> to reschedule, or wl link / wl log it", "meta"))
    # nudge (with a copy-paste fix on its own line) when a custom type.* facet was created empty
    for _k, _v in props.items():
        _h = _nt.existence_empty_hint(node_id, _k, _v)
        if _h:
            print(f"  tip: {_h[0]}", file=sys.stderr)
            print(f"  {_h[1]}", file=sys.stderr)

def cmd_log(args, con):
    _require_node(con, args.id)
    if not args.body or not args.body.strip():
        sys.exit("✗ log body cannot be empty")
    args.body = args.body.strip()
    date = getattr(args, "date", None)
    time_ = getattr(args, "time", None)
    if date or time_:
        entry = {"date": date, "time": time_, "body": args.body}
    else:
        entry = args.body
    try:
        log_id = _insert_log(con, args.id, entry)
    except ValueError as e:
        sys.exit(f"✗ invalid date: {e}")
    # auto TODO -> DOING (when no --date, "I logged something" implies "I'm working on it")
    # backfilling history (--date) does not change status; --keep-status explicitly disables
    auto_progress_hint = ""
    if not getattr(args, "keep_status", False) and not date:
        row = _db.query_one(con, "node", cols="status", id=args.id)
        if row and row["status"] == "TODO":
            _db.update(con, "node", args.id, {"status": "DOING"})
            auto_progress_hint = " (status: TODO → DOING)"
    # --metric: attach structured datapoint(s) to this log (inherit its timestamp)
    metric_hint = ""
    specs = getattr(args, "metric", None)
    if specs:
        log_at = _db.get(con, "log", log_id)["logged_at"]
        nm = attach_metric_specs(con, log_id, args.id, specs, at=log_at)
        metric_hint = f" + {nm} metric(s)"
    con.commit()
    if _is_json(args):
        row = _db.get(con, "log", log_id)
        _emit_json({"id": log_id, "node_id": args.id, "body": row["body"], "logged_at": row["logged_at"]})
        return
    print(f"✓ log added to #{args.id}{auto_progress_hint}{metric_hint}  @{_tu.local_now()[:16]}")

def cmd_done(args, con):
    _warn_recurring_done(con, _ids_list(args))
    _bulk_status_change(con, args, "DONE", close=True)


def _warn_recurring_done(con, ids):
    """`wl done` on a recurring task (has an rrule) sets global status DONE, which makes it
    show as completed on EVERY scheduled day and stop re-triggering. That is the "retire the
    whole recurring task" semantic. To mark just today's occurrence, `wl tick` is the right
    tool (adds a log, keeps status open). Warn so the two don't get confused."""
    for nid in ids:
        rule = _db.query_one(con, "sched", cols="rrule", node_id=nid, rrule__ne=None)
        if rule:
            out(_c(
                f"! #{nid} is recurring ({rule['rrule']}): `wl done` retires the whole task "
                f"(shows done on all scheduled days). For just today's occurrence use `wl tick {nid}`.",
                "planned"))

def cmd_defer(args, con):
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    try:
        when = _norm_sched(args.date)
    except ValueError as e:
        sys.exit(f"✗ {e}")
    for nid in ids:
        _db.update(con, "node", nid, {"status": "LATER", "scheduled_date": when})
    con.commit()
    if _is_json(args):
        from .query import _node_summary_dict
        _emit_json([_node_summary_dict(con, _db.get(con, "node", nid)) for nid in ids])
    else:
        for nid in ids:
            out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " → LATER, scheduled " + _c(_sched_display(when), "planned"))

def cmd_start(args, con):
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    # --at: backfill past start time. None -> NOW
    try:
        ts = _resolve_at_ts(getattr(args, "at", None))
    except ValueError as e:
        sys.exit(f"✗ {e}")
    note = f" @{_tu.utc_to_local(ts)[11:16]}" if getattr(args, "at", None) else ""
    started = []
    for nid in ids:
        # don't open a second interval on a node that's already running (would leave
        # a stale open clock + duplicate wl active rows); stop the current one first.
        if _db.exists(con, "clock", node_id=nid, end_at=None):
            out(_c(f"⚠ #{nid} already has a running clock — wl stop it first (skipped)", "later"))
            continue
        _db.update(con, "node", nid, {"status": "DOING"})
        _db.insert(con, "clock", {"node_id": nid, "start_at": ts})
        started.append(nid)
    con.commit()
    if _is_json(args) and started:
        from .query import _node_summary_dict
        _emit_json([_node_summary_dict(con, _db.get(con, "node", nid)) for nid in started])
    else:
        for nid in started:
            print(f"✓ #{nid} → DOING, clocked in{note}")

def cmd_stop(args, con):
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    # --at: backfill past stop time (must be later than the open clock's start)
    try:
        stop_ts = _resolve_at_ts(getattr(args, "at", None))
    except ValueError as e:
        sys.exit(f"✗ {e}")
    for nid in ids:
        row = _db.query_one(con, "clock", cols="id, start_at", node_id=nid, end_at=None, order="id DESC")
        if not row:
            sys.exit(f"✗ no open clock for #{nid}")
        started = datetime.fromisoformat(row["start_at"])
        stopped = datetime.fromisoformat(stop_ts)
        if stopped < started:
            sys.exit(f"✗ --at {stop_ts} is earlier than the clock start {row['start_at']} (#{nid})")
        secs = max(60, int((stopped - started).total_seconds()))  # floor at 1 min
        _db.update(con, "clock", row["id"], {"end_at": stop_ts, "elapsed_sec": secs})
        if not _is_json(args):
            print(f"✓ #{nid} stopped, elapsed {secs // 60} min")
    con.commit()
    if _is_json(args):
        from .query import _node_summary_dict
        _emit_json([_node_summary_dict(con, _db.get(con, "node", nid)) for nid in ids])

def cmd_spent(args, con):
    """Record a past time spent without opening a live CLOCK pair (retrospective entries).
    wl spent <id> 45            45 minutes (default: start = NOW - 45m, end = NOW)
    wl spent <id> 45 --at 14:30  specify end time (start = at - 45m, end = at)
    wl spent <id> 1h30m          supports 1h / 30m / 1h30m
    """
    import re as _re
    nid = args.id
    _require_node(con, nid)
    # parse duration: 1h30m / 90m / 90 (bare number = minutes)
    s = args.duration.strip().lower()
    mins = 0
    m = _re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", s)
    if m and (m.group(1) or m.group(2)):
        mins = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    elif _re.fullmatch(r"\d+", s):
        mins = int(s)
    else:
        sys.exit(f"✗ invalid duration '{s}': supported formats: 90 / 90m / 1h30m / 2h")
    if mins <= 0:
        sys.exit("✗ duration must be > 0")
    try:
        end_ts = _resolve_at_ts(getattr(args, "at", None))
    except ValueError as e:
        sys.exit(f"✗ {e}")
    end_dt = datetime.fromisoformat(end_ts)
    from datetime import timedelta as _td
    start_dt = end_dt - _td(minutes=mins)
    start_ts = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    cid = _db.insert(con, "clock", {"node_id": nid, "start_at": start_ts, "end_at": end_ts,
                                   "elapsed_sec": mins * 60})
    con.commit()
    if _is_json(args):
        r = _db.get(con, "clock", cid)
        _emit_json({"id": r["id"], "node_id": r["node_id"],
                    "start_at": r["start_at"], "end_at": r["end_at"],
                    "elapsed_sec": r["elapsed_sec"]})
        return
    print(f"✓ #{nid} spent {mins}min ({_tu.utc_to_local(start_ts)[11:16]} → {_tu.utc_to_local(end_ts)[11:16]})")

def cmd_link(args, con):
    doc = _strip_wikilink(args.vault_doc)
    if not doc:
        sys.exit("✗ vault_doc cannot be empty")
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    for nid in ids:
        _upsert_link(con, nid, doc)
    con.commit()
    if _is_json(args):
        result = []
        for nid in ids:
            links = [r["vault_doc"] for r in _db.query(con, "link", cols="vault_doc", node_id=nid, order="vault_doc")]
            result.append({"node_id": nid, "links": links})
        _emit_json(result if len(result) > 1 else result[0])
    else:
        for nid in ids:
            out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " " + _c(f"linked → [[{doc}]]"))

def cmd_unlink(args, con):
    """Remove a single vault-doc link from a node. Symmetric with wl link;
    previously a mistaken link could only be cleared wholesale via `wl set links ''`."""
    doc = _strip_wikilink(args.vault_doc)
    if not doc:
        sys.exit("✗ vault_doc cannot be empty")
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    for nid in ids:
        _, n = _delete_link(con, nid, doc)
        if not _is_json(args):
            if n:
                out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " " + _c(f"unlinked [[{doc}]]"))
            else:
                out(_c(f"#{nid} had no link to [[{doc}]]", "meta"))
    con.commit()
    if _is_json(args):
        all_links = []
        for nid in ids:
            all_links.extend(r["vault_doc"] for r in _db.query(con, "link", cols="vault_doc",
                                                                node_id=nid, order="vault_doc"))
        _emit_json(all_links)
        return

def _print_relations(con, nid):
    """Render a node's resolved relations block (own + derived reverse). Shared by
    `wl relation <id>` (list mode) and `wl show`; one node per line, width-aware
    (see render._relations_lines)."""
    lines = render._relations_lines(con, relation_view(con, nid), backrels=_backrels(con, nid))
    if not lines:
        out(_c(f"#{nid} has no relations", "meta"))
        return
    for ln in lines:
        out(ln)


def _norm_relation_type(rtype):
    """Normalize a relation type token (`split_from` / `SPLIT-FROM` → `split-from`),
    exiting with a clear message on an unknown type. Single source for cmd_relation +
    the `wl add --relation` spec parser."""
    rt = (rtype or "").strip().lower().replace("_", "-")
    if rt not in _RELATION_TYPES:
        sys.exit(f"✗ unknown relation type '{rtype}' — use one of: " + ", ".join(_RELATION_TYPES))
    return rt


def _apply_relation(con, nid, rtype, others, *, rm=False):
    """Add (or with rm=True, remove) a relation between `nid` and each id in `others`,
    writing BOTH sides (split-from ↔ split-into; related is symmetric). Self-relations are
    skipped. No commit (caller owns the transaction). Returns the applied other-ids.
    The shared core behind `wl relation` and `wl add --relation`."""
    key, inv = _RELATION_TYPES[rtype]
    done = []
    for o in others:
        if o == nid:
            continue
        if rm:
            _remove_id_from_prop_list(con, nid, key, o)
            _remove_id_from_prop_list(con, o, inv, nid)
        else:
            _add_id_to_prop_list(con, nid, key, o)
            _add_id_to_prop_list(con, o, inv, nid)
        done.append(o)
    return done


def _parse_relation_spec(spec):
    """Parse a `wl add --relation` spec string '<type> <id> [<id>…]' → (rtype, [ids]).
    e.g. 'split-from 42' or 'related 42 43'."""
    parts = (spec or "").split()
    if len(parts) < 2:
        sys.exit(f"✗ --relation '{spec}': need '<type> <id>' (e.g. 'split-from 42' / 'related 42 43')")
    rtype = _norm_relation_type(parts[0])
    ids = []
    for tok in parts[1:]:
        try:
            ids.append(int(tok.lstrip("#")))
        except ValueError:
            sys.exit(f"✗ --relation '{spec}': '{tok}' is not a node id")
    return rtype, ids


def cmd_relation(args, con):
    """Record / list task↔task relations (relation.* props). `wl relation <id>` lists a
    node's relations; `wl relation <id> <type> <other…>` adds them — writing BOTH sides
    (split-from also sets the other node's split-into, related is symmetric); `--rm`
    removes from both sides. Types: split-from / split-into / related. Distinct from
    ancestors (parent/child hierarchy) — these express derivation / association (this task
    was split out of / into / relates to that one)."""
    _require_node(con, args.id)
    rtype = getattr(args, "rtype", None)
    others_raw = list(args.others or [])
    if rtype is None:
        if _is_json(args):
            from ..queries import relation_view, _backrels
            _emit_json(relation_view(con, args.id))
        else:
            _print_relations(con, args.id)
        return
    if rtype not in _RELATION_TYPES:
        # the type word was omitted: the first token is actually a node id → default to `related`
        others_raw.insert(0, rtype)
        rtype = "related"
    others = []
    for raw in others_raw:
        s = str(raw).lstrip("#").strip()
        try:
            others.append(int(s))
        except ValueError:
            sys.exit(f"✗ '{raw}' is not a node id")
    if not others:
        sys.exit(f"✗ give at least one related node id, e.g. `wl relation {args.id} {rtype} 42`")
    _check_ids_exist(con, others)
    rm = getattr(args, "rm", False)
    for o in others:
        if o == args.id:
            out(_c(f"(skip #{o}: a node can't relate to itself)", "meta"))
    done = _apply_relation(con, args.id, rtype, others, rm=rm)
    con.commit()
    if _is_json(args):
        from ..queries import relation_view
        _emit_json(relation_view(con, args.id))
        return
    if done:
        verb = "removed" if rm else "set"
        out(_c("✓", "done") + " " + _c(f"#{args.id}", "id") + f" {rtype} {verb}: "
            + _c(", ".join(f"#{o}" for o in done)))
    _print_relations(con, args.id)


def cmd_set(args, con):
    _require_node(con, args.id)
    if not args.key or not args.key.strip():
        sys.exit("✗ prop key cannot be empty")
    args.key = args.key.strip()
    hint = _reserved_prop_hint(args.key)
    if hint:
        # a reserved key: either a core node field (status/priority/title/…) that a prop would
        # shadow, or a system-managed prop (plan.*). Reject with a pointer to the right command.
        sys.exit(f"✗ '{args.key}' is reserved, not a free UDA prop — {hint}")
    if args.key in _RESERVED_LOG_TAGS:
        # goal/summary are history-preserving reserved-tag logs, stored in the log table (not
        # single-value props): each write appends a log, the latest is current. This is the
        # key-routed shortcut for `wl goal set` — keep the output identical (prose only, no ids).
        log_id = _set_typed_log(con, args.id, args.key, args.value)
        con.commit()
        log = _db.get(con, "log", log_id)
        if _is_json(args):
            _emit_json({"key": args.key, "body": log["body"], "logged_at": log["logged_at"]})
            return
        out(_c(f"✓ #{args.id} {args.key} (logged at {log['logged_at']}): {args.value}", "meta"))
        return
    try:
        _upsert_prop(con, args.id, args.key, args.value)
        # Setting type.date / date.period must keep the time node COHERENT: re-derive its full
        # date.* set (period + start/end) from the current level + period source, and CLEAR any
        # stale date.* a prior level/period left (e.g. week→day drops the week's date.start/end;
        # →lifetime drops the period+span). Otherwise the node carries a level but no period (or a
        # mismatched span) and is mis-placed by date-range queries.
        if args.key in (_nt.K_DATE, _nt.K_PERIOD):
            sync_time_node_dates(con, args.id)
    except ValueError as e:
        # the reserved type.*/date.* validator (or a shadow-field backstop) rejected the value
        sys.exit(f"✗ {e}")
    con.commit()
    if _is_json(args):
        _emit_json({"key": args.key, "value": args.value})
        return
    print(f"✓ #{args.id} {args.key}={args.value}")
    _h = _nt.existence_empty_hint(args.id, args.key, args.value)
    if _h:
        print(f"  tip: {_h[0]}", file=sys.stderr)
        print(f"  {_h[1]}", file=sys.stderr)

def cmd_tag(args, con):
    """Add/remove real tags on a node (the tag table): `wl tag <id> +work -planned`.
    A bare word adds (same as +word); no ops lists current tags. This is the direct
    editor for the real tag field — `wl set <id> tags ...` is rejected on purpose so
    it can't quietly create a shadow prop."""
    _require_node(con, args.id)
    ops = [o.strip() for o in (args.ops or []) if o.strip()]
    if not ops:
        tags = [r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=args.id, order="tag")]
        out(_c(f"#{args.id} tags: " + (":".join(tags) if tags else "(none)"), "meta"))
        return
    added, removed = [], []
    for op in ops:
        if op.startswith("-"):
            t = op[1:].strip()
            if t:
                _db.delete(con, "tag", node_id=args.id, tag=t)
                removed.append(t)
        else:
            t = op[1:].strip() if op.startswith("+") else op
            if t:
                _db.upsert(con, "tag", {"node_id": args.id, "tag": t}, key=("node_id", "tag"))
                added.append(t)
    con.commit()
    if _is_json(args):
        tags = [r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=args.id, order="tag")]
        _emit_json(tags)
        return
    parts = []
    if added:
        parts.append(_c("+" + ",".join(added), "planned"))
    if removed:
        parts.append(_c("-" + ",".join(removed), "later"))
    out(_c("✓", "done") + " " + _c(f"#{args.id}", "id") + " tags " + " ".join(parts))


def cmd_tag_ls(args, con):
    """List a node's real tags — the read verb of the tag group (= bare `wl tag <id>`)."""
    _require_node(con, args.id)
    tags = [r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=args.id, order="tag")]
    if _is_json(args):
        _emit_json(tags)
        return
    out(_c(f"#{args.id} tags: " + (":".join(tags) if tags else "(none)"), "meta"))


def cmd_tag_rm(args, con):
    """Remove tag(s) from a node — the delete verb of the tag group (= `wl tag <id> -tag`).
    Each argument is a plain tag name (a leading + is stripped; to pass a `-tag` use the
    inline form `wl tag <id> -tag`, since argparse would read a leading - as a flag)."""
    _require_node(con, args.id)
    removed = []
    for raw in args.tags:
        t = raw.lstrip("+-").strip()
        if t:
            _db.delete(con, "tag", node_id=args.id, tag=t)
            removed.append(t)
    con.commit()
    if _is_json(args):
        tags = [r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=args.id, order="tag")]
        _emit_json(tags)
        return
    if removed:
        out(_c("✓", "done") + " " + _c(f"#{args.id}", "id") + " tags "
            + _c("-" + ",".join(removed), "later"))
    else:
        out(_c(f"#{args.id} no tags removed", "meta"))


def cmd_tag_group(args, con):
    """Dispatch `wl tag <add|ls|rm>` (the metric-style entity group).
    `add` is the default verb (`wl tag <id> +x -y` == `wl tag add <id> +x -y`) and keeps the
    full +add / -remove / bare-add / empty-list grammar; `ls` / `rm` are single-purpose."""
    sub = getattr(args, "tag_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl tag <id> +x -y  |  wl tag <add|ls|rm> … (see `wl tag --help`)")
    {"add": cmd_tag, "ls": cmd_tag_ls, "rm": cmd_tag_rm}[sub](args, con)

def cmd_tick(args, con):
    """Quick check-in: add a log for today to one or more nodes (default body='✓ done', overridable with --note).
    --done also marks the node DONE. Bulk habit check-in: `wl tick 39 40 41 --note "..."`."""
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    # empty note (--note '') falls back to default; we don't allow inserting a truly empty log
    note = (args.note or "").strip()
    body = note if note else "✓ done"
    today = _tu.today()
    for nid in ids:
        log_id = _insert_log(con, nid, body)
        # structured "done today" signal (one per node per day) — not "a log exists"
        checkin_metric(con, log_id, nid, today)
        if args.done:
            _db.update(con, "node", nid, {"status": "DONE", "closed_at": _tu.utc_now()})
    con.commit()
    for nid in ids:
        out(_c(f"✓ #{nid} checked in", "meta") + (_c(" + DONE", "done") if args.done else ""))

def cmd_wait(args, con):
    """Mark WAIT status (blocked on others / external input). Optional --note adds a log explaining what we're waiting on.
    If the task has an open clock, closes it (WAIT = suspended, no longer timing)."""
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    for nid in ids:
        # if there's an open clock, close it (WAIT = suspended, no longer timing)
        row = _db.query_one(con, "clock", cols="id, start_at", node_id=nid, end_at=None, order="id DESC")
        if row:
            now_s = _tu.utc_now()
            secs = max(60, int((datetime.fromisoformat(now_s) - datetime.fromisoformat(row["start_at"])).total_seconds()))
            _db.update(con, "clock", row["id"], {"end_at": now_s, "elapsed_sec": secs})
        _db.update(con, "node", nid, {"status": "WAIT"})
        if args.note:
            _insert_log(con, nid, f"WAIT: {args.note}")
    con.commit()
    if _is_json(args):
        from .query import _node_summary_dict
        _emit_json([_node_summary_dict(con, _db.get(con, "node", nid)) for nid in ids])
    else:
        for nid in ids:
            msg = f"✓ #{nid} → WAIT"
            if args.note:
                msg += f" ({args.note})"
            print(msg)

def cmd_reopen(args, con):
    """Undo DONE/CANCELED: back to TODO, clear closed_at. Common when a task was mistakenly closed."""
    _bulk_status_change(con, args, "TODO", reopen=True)

def cmd_cancel(args, con):
    """Mark CANCELED + write closed_at. Parallel to done semantically but different status (dropped / not doing).
    Different from `wl set <id> status CANCELED`: set writes the prop table, cancel changes node.status."""
    _bulk_status_change(con, args, "CANCELED", close=True)

def cmd_unlog(args, con):
    """Delete log entries. Two usages:
       wl unlog 282                       delete by exact log.id (find id from wl show timeline)
       wl unlog --node 39                 delete the latest non-CLOCK log for that node today (undo a mistaken tick)
       wl unlog --node 39 --date yesterday delete the latest log for that node on that day
       wl unlog --node 39 --all           delete all non-CLOCK logs for that node that day
    """
    import re as _re

    log_id = getattr(args, "log_id", None)
    nid = getattr(args, "node", None)
    if (log_id is None) == (nid is None):
        sys.exit("✗ provide either positional <log_id> or --node <id>; pick one")

    _json = _is_json(args)
    if log_id is not None:
        row = _db.get(con, "log", log_id)
        if not row:
            sys.exit(f"✗ log #{log_id} not found")
        nmetric = _db.count(con, "metric", log_id=log_id)
        soft_delete_log(con, log_id)
        con.commit()
        if _json:
            _emit_json({"deleted": [log_id], "node_id": row["node_id"], "metrics_deleted": nmetric})
        else:
            body_preview = row["body"][:60] + ("…" if len(row["body"]) > 60 else "")
            extra = f" + {nmetric} metric(s)" if nmetric else ""
            out(_c(f"✓ deleted log #{log_id}{extra} (node #{row['node_id']}, {_tu.utc_to_local(row['logged_at'])}): {body_preview}", "meta"))
        return

    # --node <id>: delete latest log for that day
    _require_node(con, nid)
    date = getattr(args, "date", None)
    if date:
        try:
            date = _resolve_concrete_date(date)
        except ValueError:
            sys.exit(f"✗ invalid --date '{date}'")
    else:
        date = _tu.today()

    sql = (f"SELECT id, logged_at, body FROM log WHERE node_id = ? AND {_tu.local_day_sql('logged_at')} = ? "
           f"AND {_db.ALIVE} ORDER BY id DESC")
    if not args.all:
        sql += " LIMIT 1"
    rows = list(con.execute(sql, (nid, date)))
    if not rows:
        if not _json:
            out(_c(f"(node #{nid} has no non-CLOCK logs on {date})", "meta"))
        else:
            _emit_json({"deleted": []})
        return
    deleted_ids = []
    for r in rows:
        nmetric = _db.count(con, "metric", log_id=r["id"])
        soft_delete_log(con, r["id"])
        deleted_ids.append(r["id"])
        if not _json:
            body_preview = r["body"][:60] + ("…" if len(r["body"]) > 60 else "")
            extra = f" + {nmetric} metric(s)" if nmetric else ""
            out(_c(f"✓ deleted log #{r['id']}{extra} (node #{nid}, {_tu.utc_to_local(r['logged_at'])}): {body_preview}", "meta"))
    con.commit()
    if _json:
        _emit_json({"deleted": deleted_ids})

def cmd_relog(args, con):
    """Rewrite an existing log: body or timestamp.

       wl relog #L282 "fixed content"      positional = new body
       wl relog #L282 -m "fixed content"   -m explicit
       wl relog #L282 --at 14:30           only change time (same day HH:MM, date auto-prepended)
       wl relog #L282 --at 2026-05-30 14:30  full ts (YYYY-MM-DD or YYYY-MM-DD HH:MM)
       wl relog #L282                       no body/--at -> open $EDITOR to edit body

    Constraints:
    - Timing lives in the clock table, not logs (use wl stop --at to fix a clock interval)
    - Cannot move across nodes (that's unlog + log, not relog)
    """
    import re as _re

    log_id = args.log_id
    row = _db.get(con, "log", log_id)
    if not row:
        sys.exit(f"✗ log #{log_id} not found")

    # body: positional or -m, mutually exclusive; both empty -> EDITOR (only when --at also missing)
    new_body = None
    if args.body and args.message:
        sys.exit("✗ positional body and -m/--message are mutually exclusive; pick one")
    if args.body:
        new_body = " ".join(args.body).strip()
    elif args.message:
        new_body = args.message.strip()

    # --at: accepts HH:MM (keep original date) / YYYY-MM-DD / YYYY-MM-DD HH:MM
    new_ts = None
    at = args.at
    if at:
        from datetime import datetime as _dt
        at = at.strip()
        # the user enters --at in LOCAL time; derive the original's local wall-clock so
        # an HH:MM-only edit keeps the same local day, then convert the result back to UTC
        orig_local = _tu.utc_to_local(row["logged_at"])
        orig_date = orig_local[:10]
        try:
            if _re.fullmatch(r"\d{2}:\d{2}", at):
                _dt.strptime(at, "%H:%M")  # validate HH/MM range
                local_ts = f"{orig_date} {at}:00"
            elif _re.fullmatch(r"\d{4}-\d{2}-\d{2}", at):
                _dt.strptime(at, "%Y-%m-%d")
                orig_time = orig_local[11:] or "00:00:00"
                local_ts = f"{at} {orig_time}"
            elif _re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", at):
                ts = at.replace("T", " ")
                if len(ts) == 16:
                    ts += ":00"
                _dt.strptime(ts, "%Y-%m-%d %H:%M:%S")
                local_ts = ts
            else:
                raise ValueError("format")
            new_ts = _tu.local_to_utc(local_ts)
        except ValueError:
            sys.exit(f"✗ invalid --at '{at}': supported formats: HH:MM / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")

    if new_body is None and new_ts is None:
        # nothing given -> open EDITOR to edit body
        new_body = _edit_in_editor(row["body"], suffix=".log.txt")
        if new_body is None or new_body.strip() == row["body"]:
            out(_c("(no change; relog canceled)", "meta"))
            return
        new_body = new_body.strip()

    changes = {}
    if new_body is not None:
        changes["body"] = new_body
    if new_ts is not None:
        changes["logged_at"] = new_ts
    _db.update(con, "log", log_id, changes)
    con.commit()

    new_row = _db.get(con, "log", log_id)
    if _is_json(args):
        _emit_json({"id": new_row["id"], "node_id": new_row["node_id"], "tag": new_row["tag"],
                    "body": new_row["body"], "logged_at": new_row["logged_at"]})
    else:
        body_preview = new_row["body"][:60] + ("…" if len(new_row["body"]) > 60 else "")
        out(_c(f"✓ relog #{log_id} (node #{row['node_id']}, {_tu.utc_to_local(new_row['logged_at'])}): {body_preview}", "meta"))


def cmd_log_ls(args, con):
    """List a node's log entries — the read verb of the log group. A simple node-scoped
    stream (`#L<id> [time] body`); for the full filterable / windowed view use `wl logs
    --id <id>` (presets, --since/--until, --by-task, --group, …)."""
    _require_node(con, args.id)
    rows = _db.query(con, "log", cols="id, logged_at, body, tag", node_id=args.id, order="logged_at")
    if _is_json(args):
        _emit_json([{"id": r["id"], "logged_at": r["logged_at"], "tag": r["tag"], "body": r["body"]} for r in rows])
        return
    if not rows:
        out(_c(f"#{args.id} has no logs", "meta"))
        return
    full = _log_full(args)
    for r in rows:
        prefix = f"#L{r['id']} [{_tu.utc_to_local(r['logged_at'])}] "
        body = _truncate_log_body(r["body"], len(prefix), full=full)
        out(_c(f"#L{r['id']}", "id") + " "
            + _c(f"[{_tu.utc_to_local(r['logged_at'])}]", "meta") + " " + body)


def cmd_log_show(args, con):
    """Show one log entry's full (untruncated) content by its log id (`wl log show #L282`).
    The list views (`wl logs`, `wl log ls`, `wl show` timeline) truncate each log to one
    line; this prints the whole body. Accepts #L282 / L282 / 282."""
    row = _db.get(con, "log", args.log_id)
    if row is None:
        sys.exit(f"✗ no log #L{args.log_id}")
    if _is_json(args):
        _emit_json({"id": row["id"], "node_id": row["node_id"], "tag": row["tag"],
                    "body": row["body"], "logged_at": row["logged_at"]})
        return
    node = _db.get(con, "node", row["node_id"])
    title = node["title"] if node else "?"
    label = row["tag"] or "note"
    out(_c(f"#L{row['id']}", "id") + " "
        + _c(f"[{_tu.utc_to_local(row['logged_at'])}]", "meta") + " "
        + _c(label, "meta") + " "
        + _c(f"#{row['node_id']}", "id") + " " + _c(f"'{title}'", "meta"))
    out(row["body"])


def cmd_retag(args, con):
    """Change one log's `tag` directly (`wl retag #L282 goal`). The tag classifies a log's
    role (goal / summary / a custom marker); a plain note has no tag. Passing `note` / `none`
    / `-` / empty clears it back to a plain note (NULL). Accepts #L282 / L282 / 282."""
    row = _db.get(con, "log", args.log_id)
    if row is None:
        sys.exit(f"✗ no log #L{args.log_id}")
    raw = (args.tag or "").strip()
    new = None if raw.lower() in ("", "-", "note", "none") else raw
    _db.update(con, "log", args.log_id, {"tag": new})
    con.commit()
    out(_c("✓", "done") + " " + _c(f"#L{args.log_id}", "id") + f" tag → {new or 'note'}")


def cmd_log_group(args, con):
    """Dispatch `wl log <add|ls|edit|rm|show>` (the metric-style entity group).
    `add` is the default verb (`wl log <id> "body"` == `wl log add <id> "body"`); `edit`
    is `wl relog` and `rm` is `wl unlog` (both keep their top-level shortcuts); `show`
    prints one log's full content."""
    sub = getattr(args, "log_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl log <id> \"body\"  |  wl log <add|ls|edit|rm|show> … (see `wl log --help`)")
    {"add": cmd_log, "ls": cmd_log_ls, "edit": cmd_relog, "rm": cmd_unlog, "show": cmd_log_show}[sub](args, con)


def cmd_active(args, con):
    """List tasks running right now: tasks with an open clock interval (actually timing).
    Each task shows: id / title / current-session elapsed + today's total + latest log (context).

    Use cases:
    - Before lunch / a meeting, glance at which task is timing right now
    - Late in the day, find a task you forgot to stop and wrap up with wl stop <id>
    - When juggling several tasks, confirm current focus

    Difference from wl day: wl day = full single-day view (done / not-yet-started included); wl active = what's timing right now.
    `-q` skips total / log detail, listing only id + elapsed.
    """
    from datetime import datetime as _dt, date as _date

    rows = con.execute(f"""
        SELECT c.node_id, c.start_at, n.title, n.status, n.priority
        FROM clock c JOIN node n ON c.node_id = n.id
        WHERE c.end_at IS NULL AND c.{_db.ALIVE} AND n.{_db.ALIVE}
        ORDER BY c.start_at DESC
    """).fetchall()

    if not rows:
        if _is_json(args):
            _emit_json([])
        else:
            out(_c("(no active task right now; use wl start <id> to start timing, wl day for today's progress)", "meta"))
        return

    brief = getattr(args, "brief", False)
    now = _dt.fromisoformat(_tu.utc_now())  # UTC, to match the UTC-stored start_at
    today = _tu.today()
    full = _log_full(args)
    if _is_json(args):
        result = []
        for r in rows:
            started = _dt.fromisoformat(r["start_at"])
            mins = int((now - started).total_seconds() / 60)
            result.append({"node_id": r["node_id"], "title": r["title"],
                           "status": r["status"], "priority": r["priority"],
                           "start_at": r["start_at"], "elapsed_min": mins})
        _emit_json(result)
        return
    for r in rows:
        started = _dt.fromisoformat(r["start_at"])
        mins = int((now - started).total_seconds() / 60)
        pri = _pri_marker(r["priority"]) + " "
        # head: id + priority + title + current session
        head_tail = "" if brief else " " + _c(f"({mins}min, since {_tu.utc_to_local(r['start_at'])[11:16]})", "meta")
        out(_c("⏱", "clock") + " " + _c(f"#{r['node_id']}", "id") + " " + pri + _c(r["title"]) + head_tail)
        if brief:
            continue
        # today's completed clock total + the current open session (helps decide "continue or stop")
        done_sec = con.execute(
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ? AND {_tu.local_day_sql('end_at')} = ? AND {_db.ALIVE}",
            (r["node_id"], today),
        ).fetchone()["s"]
        total_min = mins + int((done_sec or 0) / 60)  # includes current open session
        out("    " + _c(f"today's total {total_min}min ({total_min // 60}h{total_min % 60}m), includes current session", "meta"))
        # latest plain-note log (oneline truncated)
        last = _db.query_one(con, "log", cols="body", node_id=r["node_id"], tag=None, order="id DESC")
        if last:
            body_one = _truncate_log_body(last["body"], indent_cols=_display_width("    latest log: "), full=full)
            out("    " + _c(f"latest log: {body_one}", "meta"))

def _ids_list(args):
    """argparse compat: if args.ids (list, nargs='+') is set use it, else fall back to [args.id] (older type=int)."""
    if hasattr(args, "ids") and args.ids:
        return args.ids
    return [args.id]

def _bulk_status_change(con, args, new_status, *, close=False, reopen=False, msg=None):
    """Unified batch status change: done/cancel/reopen all go through this path.
    - close=True: write closed_at = NOW (or args.at if given)
    - reopen=True: clear closed_at = NULL
    - otherwise: only change status

    If args has a .log field, insert a log per id first (via _insert_log, supporting args.at).
    """
    ids = _ids_list(args)
    _check_ids_exist(con, ids)

    # --at parse (reuses _resolve_at_ts; affects closed_at + log time)
    at_ts = None
    if close and getattr(args, "at", None):
        try:
            at_ts = _resolve_at_ts(args.at)
        except ValueError as e:
            sys.exit(f"✗ {e}")

    # --log: insert log first (use at_ts; default to NOW if no at)
    log_body = getattr(args, "log", None)
    if log_body:
        log_body = log_body.strip()
    if log_body:
        for nid in ids:
            if at_ts:
                # at_ts is already UTC — insert directly (avoid _insert_log re-localizing)
                _db.insert(con, "log", {"node_id": nid, "logged_at": at_ts, "body": log_body})
            else:
                _insert_log(con, nid, log_body)

    changes = {"status": new_status}
    if close:
        changes["closed_at"] = at_ts if at_ts else _tu.utc_now()
    elif reopen:
        changes["closed_at"] = None   # -> SET closed_at = NULL
    for nid in ids:
        _db.update(con, "node", nid, changes)
    con.commit()
    label = msg or ("reopened → " + new_status if reopen else "→ " + new_status)
    note = f" @{_tu.utc_to_local(at_ts)[11:16]}" if at_ts else ""
    log_hint = " + log" if log_body else ""
    if _is_json(args):
        from .query import _node_summary_dict
        _emit_json([_node_summary_dict(con, _db.get(con, "node", nid)) for nid in ids])
    else:
        for nid in ids:
            print(f"✓ #{nid} {label}{note}{log_hint}")


# --- scheduled time: precise dates + fuzzy granularity (month/week/quarter/year/someday) ---

def _edit_in_editor(initial_text, suffix=".txt"):
    """Open $EDITOR to edit a piece of text; return the new content, or None if canceled or unchanged."""
    import os
    import subprocess
    import tempfile

    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=suffix, delete=False) as f:
        f.write(initial_text)
        path = f.name
    try:
        rc = subprocess.call([editor, path])
        if rc != 0:
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass



def cmd_node_reparent(args, con):
    """Move a node under a new parent — changes the real `parent_id`,
    not a UDA prop. 'none'/'root'/0 detaches to the top level. Refuses a cycle (the new
    parent must not be the node itself or one of its descendants)."""
    nid = args.id
    _require_node(con, nid)
    p = (args.parent or "").strip().lower()
    if p in ("none", "root", "0", ""):
        new_parent = None
    else:
        try:
            new_parent = int(args.parent)
        except ValueError:
            sys.exit(f"✗ parent must be a node id or 'none'/'root'/'0' (detach), got {args.parent!r}")
        if not _node_exists(con, new_parent):
            sys.exit(f"✗ parent node #{new_parent} not found")
        if new_parent == nid:
            sys.exit("✗ a node cannot be its own parent")
        # include_deleted: catch a live descendant reachable through a tombstoned intermediate
        if new_parent in _collect_descendants(con, nid, include_deleted=True):
            sys.exit(f"✗ #{new_parent} is a descendant of #{nid} — reparenting there would make a cycle")
    _db.update(con, "node", nid, {"parent_id": new_parent})
    con.commit()
    where = "the top level" if new_parent is None else f"#{new_parent}"
    out(_c(f"✓ #{nid} moved under {where}", "meta"))


def cmd_node_rm(args, con):
    """Soft-delete node(s) and their subtree (reversible tombstone) — the
    primitive single-node form of `wl apply - #id`. Clearing `deleted_at` restores."""
    for nid in args.ids:
        _require_node(con, nid)
    total = 0
    for nid in args.ids:
        # include_deleted: tombstone the FULL structural subtree, so a live node hanging
        # under an already-tombstoned intermediate doesn't get orphaned.
        for did in [nid] + _collect_descendants(con, nid, include_deleted=True):
            soft_delete_node(con, did)
        total += 1
    con.commit()
    if _is_json(args):
        _emit_json({"deleted": list(args.ids)})
    else:
        out(_c(f"✓ soft-deleted {len(args.ids)} node(s) + subtree (reversible; clear deleted_at to restore)", "meta"))


def cmd_node_edit(args, con):
    """Edit a node's own fields: title / priority / body / scheduled / deadline, plus --para to
    set the PARA role (writes type.para). (Status has its own verbs done/cancel/…; parent →
    `node reparent`; tags → `wl tag`; other classifications → `wl set type.<x>` / `wl prop rm`.)"""
    nid = args.id
    _require_node(con, nid)
    changes = {}
    if args.title is not None:
        if not args.title.strip():
            sys.exit("✗ title cannot be empty")
        changes["title"] = args.title.strip()
    if args.priority is not None:
        changes["priority"] = args.priority
    if args.body is not None:
        changes["body"] = args.body
    if args.scheduled is not None:
        try:
            changes["scheduled_date"] = _norm_sched(args.scheduled) if args.scheduled else None
        except ValueError as e:
            sys.exit(f"✗ {e}")
    if args.deadline is not None:
        changes["deadline_date"] = args.deadline or None
    para = getattr(args, "para", None)
    if not changes and para is None:
        sys.exit("✗ nothing to edit (give --title / --priority / --para / --body / --scheduled / --deadline)")
    if changes:
        _db.update(con, "node", nid, changes)
    conflicts = []
    if para is not None:
        # set the PARA role via the type.* namespace (the column is gone). Replace any prior role.
        _db.delete(con, "prop", node_id=nid, key=_nt.K_PARA)
        _upsert_prop(con, nid, _nt.K_PARA, para)
        changes["para"] = para
        # The type.* model is orthogonal, so --para does NOT auto-clear a node's OTHER
        # classification (type.date / type.habit / type.meetlog) — but a node that is e.g. a
        # meetlog AND now type.para=project reads inconsistently (node_type_from_props shows the role by
        # precedence, yet `--prop type.meetlog` / checkin still match it). Surface it so the user
        # can clear the stale one with `wl prop rm` rather than silently leaving a hybrid.
        _LABELS = {_nt.K_DATE: "type.date", _nt.K_HABIT: "type.habit", _nt.K_MEETLOG: "type.meetlog"}
        have = {r["key"] for r in _db.query(con, "prop", cols="key", node_id=nid)}
        conflicts = [_LABELS[k] for k in (_nt.K_DATE, _nt.K_HABIT, _nt.K_MEETLOG) if k in have]
    con.commit()
    if _is_json(args):
        row = _db.get(con, "node", nid)
        _emit_json({"id": row["id"], "title": row["title"], "status": row["status"],
                    "priority": row["priority"],
                    "scheduled_date": row["scheduled_date"], "deadline_date": row["deadline_date"]})
    else:
        out(_c(f"✓ #{nid} updated: " + ", ".join(f"{k}={v}" if k == "para" else k for k, v in changes.items()), "meta"))
        if conflicts:
            out(_c(f"⚠ #{nid} still carries {', '.join(conflicts)} alongside type.para={para} — "
                   f"clear the stale one with `wl prop rm {nid} <key>` if this should be a plain {para}", "later"))


# --- prop entity group: set / ls / rm ---
def cmd_prop_ls(args, con):
    """List a node's UDA props (key=value). The read primitive for prop (props are also
    shown inline by `wl show`)."""
    _require_node(con, args.id)
    rows = _db.query(con, "prop", cols="key, value", node_id=args.id, order="key")
    if _is_json(args):
        _emit_json([{"key": r["key"], "value": r["value"]} for r in rows])
        return
    if not rows:
        out(_c(f"(#{args.id} has no props)", "meta"))
        return
    for r in rows:
        out(_c(f"#{args.id} ", "id") + _c(f"{r['key']}={r['value']}"))


def cmd_prop_rm(args, con):
    """Remove a UDA prop from a node (soft-delete the row). Also the `wl unset`
    shortcut. The delete counterpart of `wl set`."""
    _require_node(con, args.id)
    key = (args.key or "").strip()
    if not key:
        sys.exit("✗ prop key cannot be empty")
    if key in _RESERVED_LOG_TAGS:
        # key-routed shortcut, symmetric with `wl set`: goal/summary live in the log table
        # as reserved-tag logs, not props — clear it there (= wl goal rm).
        n = _db.delete(con, "log", node_id=args.id, tag=key)
        con.commit()
        if _is_json(args):
            _emit_json({"key": key, "removed": n})
            return
        out(_c(f"✓ #{args.id} {key} cleared ({n} log(s))" if n
               else f"(#{args.id} has no {key})", "meta"))
        return
    n = _db.delete(con, "prop", node_id=args.id, key=key)
    if n and (key.startswith("type.") or key.startswith("date.")):
        # a structural classification key just changed what this node IS — surface it (non-blocking),
        # since removing e.g. type.para demotes a project to a bare task, or type.date un-places a
        # time node. Hint to stderr so stdout/JSON stays clean.
        new_type = node_type(con, args.id)
        print(f"⚠ #{args.id} is now '{new_type}' (removing '{key}' changed its classification)",
              file=sys.stderr)
    con.commit()
    if _is_json(args):
        _emit_json({"key": key, "removed": n})
        return
    out(_c(f"✓ #{args.id} prop '{key}' removed" if n else f"(#{args.id} has no prop '{key}')", "meta"))


def cmd_prop(args, con):
    """Dispatch `wl prop <set|ls|rm>` (the metric-style entity group)."""
    sub = getattr(args, "prop_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl prop <set|ls|rm> … (see `wl prop --help`)")
    {"set": cmd_set, "ls": cmd_prop_ls, "rm": cmd_prop_rm}[sub](args, con)


# --- agent entity group: bind the current agent session to a node, stored as an
# `agent_session.<app>` prop on that node (no new table). The prefix `agent_session.` finds a
# node's bindings across apps; the suffix is the app (claude / cursor / …). CRUD:
#   wl agent <id> (set) · wl agent (show current) · wl agent ls (list all) · wl agent rm (unbind).
_AGENT_APP = "claude"                       # default agent runtime this CLI ships for
_AGENT_PREFIX = "agent_session."            # cross-app prefix: prop key is agent_session.<agent>
_AGENT_KEY = _AGENT_PREFIX + _AGENT_APP       # default key (agent_session.claude)
_SESSION_METRIC_TAG = "agent_session"        # bind-history metric: the session id (value_text = sid); tag string mirrors the prop namespace
_AGENT_LS_CAP = 12                            # `wl agent ls` shows the N most-recently-active bindings by default; --all (or plain/piped) shows every one
_AGENT_METRIC_TAG = "agent"                  # bind-history metric: the runtime name (value_text = prop-key suffix, e.g. claude)

def _current_agent():
    """Which agent runtime drives this `wl` — recorded with the session so the bind history
    shows *what* worked the node (claude / cursor / codex / …), not just an opaque sid.
    `$WL_AGENT` (a per-agent SessionStart hook can set it) wins; otherwise `claude`, the runtime
    this CLI ships for. Lowercased + trimmed so the prop key / metric note stay tidy."""
    import os
    return (os.environ.get("WL_AGENT") or "").strip().lower() or _AGENT_APP

def _agent_key(agent):
    """Prop key for an agent's live binding: `agent_session.<agent>` (the cross-app convention)."""
    return _AGENT_PREFIX + agent

def _agent_cache_dir():
    """Where integrations cache a session's binding: `$XDG_STATE_HOME/worklog/agent/`."""
    return _xdg_state_home() / "worklog" / "agent"

def _invalidate_agent_cache(sid):
    """Drop a session's cached binding so an integration (the UserPromptSubmit context hook, a
    status line) re-fetches via `wl agent context` next time. Called on every bind /
    rebind / unbind — the binding changed, so the cache is stale. Best-effort."""
    d = _agent_cache_dir()
    try:
        (d / sid).unlink()
    except OSError:
        pass

def _agent_context_line(con, sid):
    """The current binding for `sid` as a machine line `<node_id>\\t<title>` (empty if unbound).
    The single query an integration calls (via `wl agent context`) — so a hook never hand-writes
    SQL and never reads the DB on its hot path; it caches this and invalidates on rebind."""
    if not sid:
        return ""
    # by sid alone (unique) across any agent's key — the live pointer regardless of runtime
    row = _db.query_one(con, "prop", cols="node_id", key__like=_AGENT_PREFIX + "%", value=sid)
    if not row:
        return ""
    node = _db.get(con, "node", row["node_id"])
    if not node:
        return ""
    return f"{node['id']}\t{node['title']}"

def _agent_hook_json(con, sid):
    """The current binding as a Claude Code `UserPromptSubmit` hook payload (JSON), or "" if
    unbound. Lets the shipped context hook emit valid JSON without `jq` — wl owns the escaping.
    The message is intentionally short (the agent re-reads it); titles are escaped by json.dumps."""
    line = _agent_context_line(con, sid)
    if not line:
        return ""
    nid, _, title = line.partition("\t")
    msg = (f"📌 This session is bound to WL#{nid}: {title}. "
           f"Keep this session's work on it; prefer logging progress to WL#{nid}.")
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": msg}}, ensure_ascii=False)

def _has_agent_history(con, nid, sid):
    """Whether the (node, session) bind is already in the history trail — dedup by the
    `session` metric, so re-binding the same pair never duplicates it, yet a pair that was
    bound without a history record still gets one on a later bind."""
    return _db.query_one(con, "metric", cols="id", node_id=nid,
                         tag=_SESSION_METRIC_TAG, value_text=sid) is not None

def _record_bind_history(con, nid, sid, agent):
    """Append-only record that session `sid` (run by `agent`) was bound to node `nid`.

    Two stores with different jobs:
      * the prop `agent_session.<agent>` is the *live* pointer — one session → one node, and it
        MOVES on rebind, so it always names the node a session is currently on;
      * this log + its two metrics are the *history* — they stay on the node forever, so
        `wl show <id>` / `wl metric ls <id> --tag session` recover every session a node was worked
        under. The bind carries **two separate metrics** on one carrier log: `session` (value =
        the full session id) and `agent` (value = the runtime name — claude / cursor / …, the
        `agent_session.<agent>` prop-key suffix), so each is its own queryable datapoint.

    Written once per (node, session) bind, NOT stamped onto every later log — one row per
    association instead of tagging every write, which is the whole point of the light design."""
    now = _tu.utc_now()
    log_id = _db.insert(con, "log", {
        "node_id": nid, "logged_at": now,
        "body": f"agent session bound · {agent}:{sid[:8]}…",
        "tag": "metric",   # auto metric-carrier log (same convention as `wl metric add`)
    })
    # the session id, as its own metric
    _db.insert(con, "metric", {
        "log_id": log_id, "node_id": nid, "tag": _SESSION_METRIC_TAG,
        "value_num": None, "value_text": sid, "unit": None,
        "note": None, "at": now,
    })
    # the agent runtime name, as its own metric (value = the prop-key suffix)
    _db.insert(con, "metric", {
        "log_id": log_id, "node_id": nid, "tag": _AGENT_METRIC_TAG,
        "value_num": None, "value_text": agent, "unit": None,
        "note": None, "at": now,
    })

def _short(s, n=50):
    """Truncate a title for one-line bind output (plain char count is fine here)."""
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _agent_ls_row(it, idw, agw, sidlen, plain, indent=""):
    """Format one `wl agent ls` row. plain → full sid + full title (no truncation); else the full
    sid when it fits (shrunk to `sidlen` with … when tight) + title truncated to one line. `indent`
    prefixes the row (e.g. under a day-group header) and is counted in the title's width budget."""
    idstr = f"#{it['id']}".ljust(idw)
    ag = (it["agent"] + ":").ljust(agw + 1)
    if plain:
        return f"{indent}{idstr} ← {ag}{it['sid']} · {it['title']}"
    sid = it["sid"]
    sid_show = sid if len(sid) <= sidlen else sid[: sidlen - 1] + "…"
    seg = f"{ag}{sid_show}"
    prefix_plain = f"{indent}{idstr} ← {seg} · "
    title = _truncate_log_body(it["title"], indent_cols=_display_width(prefix_plain), full=False)
    return indent + _c(idstr, "id") + " ← " + _c(seg, "meta") + " · " + title

def _current_session_id():
    """Session id of the running agent shell. Prefer $WL_SESSION_ID (a SessionStart hook can
    freeze the official session_id under this stable name), fall back to the (undocumented)
    $CLAUDE_CODE_SESSION_ID. None if neither — callers fail closed (GPT review)."""
    import os
    return os.environ.get("WL_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID") or None

def _agent_need_sid():
    sid = _current_session_id()
    if not sid:
        sys.exit("✗ no session id ($WL_SESSION_ID / $CLAUDE_CODE_SESSION_ID) — run inside a Claude Code session")
    return sid

def _agent_set(args, con):
    sid = _agent_need_sid()
    agent = (getattr(args, "agent", None) or _current_agent())
    key = _agent_key(agent)
    nid = args.id
    _require_node(con, nid)
    cur = _db.query_one(con, "prop", cols="value", node_id=nid, key=key)
    if cur and cur["value"] != sid:
        out(_c(f"⚠ #{nid} 已被 session {cur['value'][:8]}… 绑定,将被覆盖", "later"))
    # Record the history trail unless told not to AND it isn't already recorded — dedup by the
    # actual metric (not "is the prop already set"), so a pair bound before history existed
    # (an early auto-bind, or a --no-record bind) still gets recorded on a later bind.
    do_record = getattr(args, "record", True) and not _has_agent_history(con, nid, sid)
    # one session → one node: drop this sid's live pointer under ANY agent key before re-binding
    _db.delete(con, "prop", key__like=_AGENT_PREFIX + "%", value=sid)
    _upsert_prop(con, nid, key, sid)
    if do_record:
        _record_bind_history(con, nid, sid, agent)   # append-only history trail
    con.commit()
    _invalidate_agent_cache(sid)   # binding changed → integrations re-fetch via `wl agent context`
    title = (_db.get(con, "node", nid) or {})["title"]
    line = _c("✓", "done") + " " + _c(f"#{nid}", "id") + " ← " + _c(f"{agent}:{sid[:8]}…", "meta") + " · " + _short(title)
    if do_record:
        line += _c("  +history", "meta")
    out(line)
    return

def _agent_ls(args, con):
    rows = _db.query(con, "prop", cols="node_id, key, value", key__like=_AGENT_PREFIX + "%")
    if not rows:
        if _is_json(args):
            _emit_json([])
        else:
            out(_c("(no session bindings)", "meta"))
        return
    # two time axes per binding: `act` = node's latest log/update time (most-recently-worked);
    # `bound` = when this session was bound to the node (latest agent_session bind-history log).
    items = []
    for r in rows:
        nid, sid = r["node_id"], r["value"]
        node = _db.get(con, "node", nid)
        title = node["title"] if node else "(deleted)"
        last = _db.query_one(con, "log", cols="MAX(logged_at) AS m", node_id=nid)
        act = (last["m"] if last else None) or (node["created_at"] if node else "") or ""
        bl = con.execute(
            "SELECT MAX(l.logged_at) AS m FROM log l JOIN metric mt ON mt.log_id = l.id "
            f"WHERE l.node_id = ? AND mt.tag = ? AND mt.value_text = ? AND l.{_db.ALIVE}",
            (nid, _SESSION_METRIC_TAG, sid)).fetchone()
        bound = (bl["m"] if bl else None) or ""
        items.append({"id": nid, "agent": r["key"][len(_AGENT_PREFIX):],
                      "sid": sid, "title": title, "act": act, "bound": bound})
    by = getattr(args, "by", "active")
    keyf = (lambda it: it["bound"] or it["act"]) if by == "bound" else (lambda it: it["act"])
    items.sort(key=keyf, reverse=True)                 # most-recent (by chosen axis) first
    if _is_json(args):
        _emit_json(items)
        return
    plain = render.is_plain()
    # plain/piped or --all → show all (a script needs the lot); else elide older to avoid flood
    show_all = plain or getattr(args, "all", False)
    shown = items if show_all else items[:_AGENT_LS_CAP]
    hidden = len(items) - len(shown)
    idw = max(len(f"#{it['id']}") for it in shown)
    agw = max(len(it["agent"]) for it in shown)
    # full sid when it fits leaving the title room; shrink uniformly (≥8) when tight (TTY only)
    W = _term_width()
    MIN_TITLE = 16
    sidlen = max(len(it["sid"]) for it in shown)
    if idw + 3 + (agw + 1) + 3 + sidlen + MIN_TITLE > W:
        sidlen = max(8, W - (idw + 3 + (agw + 1) + 3) - MIN_TITLE)
    group = getattr(args, "group", False)
    if group:
        today = _tu.today()
        yday = (_tu.today_date() - timedelta(days=1)).isoformat()
        cur = object()
        for it in shown:
            # bucket by LOCAL day (keyf returns a UTC timestamp) so the day header matches the
            # local today/yesterday labels — otherwise a binding made "now" lands under the UTC
            # date and mislabels as yesterday during the UTC-evening / local-next-day window.
            key = keyf(it)
            day = _tu.utc_to_local(key)[:10] if key else "(no date)"
            if day != cur:
                cur = day
                lbl = day + (" (today)" if day == today else " (yesterday)" if day == yday else "")
                out(_c(lbl, "planned"))
            out(_agent_ls_row(it, idw, agw, sidlen, plain, indent="  "))
    else:
        for it in shown:
            out(_agent_ls_row(it, idw, agw, sidlen, plain))
    if hidden > 0:
        out(_c(f"  … +{hidden} older — wl agent ls --all", "meta"))
    return

def _agent_rm(args, con):
    sid = _agent_need_sid()
    n = _db.delete(con, "prop", key__like=_AGENT_PREFIX + "%", value=sid)   # any agent's key
    con.commit()
    _invalidate_agent_cache(sid)   # drop cached binding so the hook stops injecting
    out(_c(f"✓ unbound (session {sid[:8]}…)" if n else f"(session {sid[:8]}… 本来就没绑定)", "meta"))
    return

def _agent_context(args, con):
    # Machine output for integrations (the context hook): a `<node_id>\t<title>` line, or
    # with --hook the ready-to-emit UserPromptSubmit JSON (so the hook needs no `jq`). Empty
    # when unbound. Plain print (not `out`) — consumed by scripts, not rendered.
    sid = _current_session_id()
    print(_agent_hook_json(con, sid) if getattr(args, "hook", False) else _agent_context_line(con, sid))
    return

def _agent_show(args, con):
    # bare `wl agent` → show the current session's binding (under whichever agent key it lives)
    sid = _agent_need_sid()
    row = _db.query_one(con, "prop", cols="node_id, key", key__like=_AGENT_PREFIX + "%", value=sid)
    if not row:
        if _is_json(args):
            _emit_json(None)
        else:
            out(_c(f"(session {sid[:8]}… 未绑定任何任务)", "meta"))
        return
    agent = row["key"][len(_AGENT_PREFIX):]
    title = (_db.get(con, "node", row["node_id"]) or {})["title"]
    if _is_json(args):
        _emit_json({"node_id": row["node_id"], "title": title, "agent": agent, "session_id": sid})
        return
    idstr = f"#{row['node_id']}"
    if render.is_plain():
        out(f"{idstr} ← {agent}:{sid} · {title}")    # plain: full sid + full title, no truncation
        return
    # interactive: full sid when it fits leaving the title room; shrink uniformly when tight
    base = len(idstr) + len(" ← ") + len(agent) + 1 + len(" · ")
    MIN_TITLE = 16
    sidlen = len(sid)
    if base + sidlen + MIN_TITLE > _term_width():
        sidlen = max(8, _term_width() - base - MIN_TITLE)
    sid_show = sid if len(sid) <= sidlen else sid[:sidlen - 1] + "…"
    seg = f"{agent}:{sid_show}"
    prefix_plain = f"{idstr} ← {seg} · "
    shown = _truncate_log_body(title, indent_cols=_display_width(prefix_plain), full=False)
    out(_c(idstr, "id") + " ← " + _c(seg, "meta") + " · " + shown)

def cmd_agent(args, con):
    """`wl agent` — bind the current agent session to a node.
    wl agent <id> = set · wl agent = show current · wl agent ls = list all · wl agent rm = unbind."""
    sub = getattr(args, "agent_sub", None)
    # set/ls/rm/context dispatch like every other entity group; bare `wl agent` → show
    {"set": _agent_set, "ls": _agent_ls, "rm": _agent_rm,
     "context": _agent_context}.get(sub, _agent_show)(args, con)

# --- clock entity group: ls / edit / rm (create = start/stop/spent) ---
def cmd_clock_ls(args, con):
    """List a node's clock intervals (start → end, duration). Read primitive for clock."""
    _require_node(con, args.id)
    rows = _db.query(con, "clock", cols="id, start_at, end_at, elapsed_sec", node_id=args.id, order="id")
    if _is_json(args):
        _emit_json([{"id": r["id"], "start_at": r["start_at"], "end_at": r["end_at"],
                     "elapsed_sec": r["elapsed_sec"]} for r in rows])
        return
    if not rows:
        out(_c(f"(#{args.id} has no clock intervals)", "meta"))
        return
    for r in rows:
        st = _tu.utc_to_local(r["start_at"])
        en = _tu.utc_to_local(r["end_at"]) if r["end_at"] else "(running)"
        dur = _fmt_dur(int((r["elapsed_sec"] or 0) / 60)) if r["elapsed_sec"] else ""
        out(_c(f"#C{r['id']}", "id") + " " + _c(f"{st} → {en}", "meta") + (" " + _c(dur, "clock") if dur else ""))


def cmd_clock_edit(args, con):
    """Edit a clock interval's start / end (recomputes elapsed_sec). Fix a mistimed
    `wl start/stop/spent` entry."""
    row = _db.get(con, "clock", args.clock_id)
    if not row:
        sys.exit(f"✗ clock interval #C{args.clock_id} not found")
    changes = {}
    start_at = row["start_at"]
    end_at = row["end_at"]
    if args.start is not None:
        try:
            start_at = _resolve_at_ts(args.start)
        except ValueError as e:
            sys.exit(f"✗ --start: {e}")
        changes["start_at"] = start_at
    if args.end is not None:
        try:
            end_at = _resolve_at_ts(args.end) if args.end else None
        except ValueError as e:
            sys.exit(f"✗ --end: {e}")
        changes["end_at"] = end_at
    if not changes:
        sys.exit("✗ nothing to edit (give --start / --end)")
    # recompute elapsed from the resulting start/end when both are present
    if start_at and end_at:
        from datetime import datetime
        try:
            secs = int((datetime.fromisoformat(end_at) - datetime.fromisoformat(start_at)).total_seconds())
        except (ValueError, TypeError):
            secs = None
        if secs is not None and secs < 0:
            sys.exit(f"✗ end {end_at} is before start {start_at}")
        if secs is not None:
            changes["elapsed_sec"] = secs
    elif "end_at" in changes and end_at is None:
        changes["elapsed_sec"] = None  # --end '' cleared the end → back to running
    _db.update(con, "clock", args.clock_id, changes)
    con.commit()
    if _is_json(args):
        r = _db.get(con, "clock", args.clock_id)
        _emit_json({"id": r["id"], "node_id": r["node_id"],
                    "start_at": r["start_at"], "end_at": r["end_at"],
                    "elapsed_sec": r["elapsed_sec"]})
        return
    out(_c(f"✓ clock #C{args.clock_id} updated: " + ", ".join(changes), "meta"))


def cmd_clock_rm(args, con):
    """Soft-delete a clock interval — remove a wrong `wl spent`/start-stop entry."""
    for cid in args.clock_ids:
        if not _db.exists(con, "clock", id=cid):
            sys.exit(f"✗ clock interval #C{cid} not found")
    for cid in args.clock_ids:
        _db.delete(con, "clock", id=cid)
    con.commit()
    if _is_json(args):
        _emit_json({"deleted": list(args.clock_ids)})
        return
    out(_c(f"✓ removed {len(args.clock_ids)} clock interval(s)", "meta"))


def cmd_clock(args, con):
    """Dispatch `wl clock <ls|edit|rm>` (the metric-style entity group).
    Creating intervals stays with the `start` / `stop` / `spent` composite helpers."""
    sub = getattr(args, "clock_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl clock <ls|edit|rm> … (create with start/stop/spent; see `wl clock --help`)")
    {"ls": cmd_clock_ls, "edit": cmd_clock_edit, "rm": cmd_clock_rm}[sub](args, con)


# --- link entity group: add / ls / rm, default verb `add` ---
def cmd_link_ls(args, con):
    """List a node's vault-doc links. Read primitive for link (also shown by `wl show`)."""
    _require_node(con, args.id)
    rows = _db.query(con, "link", cols="vault_doc", node_id=args.id, order="vault_doc")
    if _is_json(args):
        _emit_json([r["vault_doc"] for r in rows])
        return
    if not rows:
        out(_c(f"(#{args.id} has no links)", "meta"))
        return
    for r in rows:
        out(_c(f"#{args.id} ", "id") + _c(f"→ [[{r['vault_doc']}]]", "meta"))


def cmd_link_group(args, con):
    """Dispatch `wl link <add|ls|rm>`. `add` is the default verb, so the legacy
    `wl link 42 doc` still works (the parser expands it to `wl link add 42 doc`). `rm` also
    has the top-level shortcut `wl unlink`."""
    sub = getattr(args, "link_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl link <id…> <doc>  |  wl link <add|ls|rm> … (see `wl link --help`)")
    {"add": cmd_link, "ls": cmd_link_ls, "rm": cmd_unlink}[sub](args, con)
