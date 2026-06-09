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
from .. import db_table as _db
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
    _sched_kind,
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
    _node_plan,
    _node_project,
    _node_tags,
    _project_members,
    _sec_group,
    _status_filter_sql,
    _upsert_prop,
    _strip_wikilink,
    _upsert_link,
    _delete_link,
    _set_typed_log,
    _META_LOG_TYPES,
    _reserved_prop_hint,
)
from .metric import attach_metric_specs, checkin_metric, _CARRIER_TYPE
from ..render import (
    _PRI_STYLE,
    _STATUS_STYLE,
    _RICH_AVAIL,
    _resolve_theme,
    THEMES,
    _c,
    _hl,
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

def _find_similar_open(con, title, kind):
    """Open (non-terminal) task/project nodes whose title looks like a duplicate of
    `title`: identical after normalization, or one normalized title contains the other
    (with the shorter ≥4 chars, to avoid trivial substring noise). Best-effort, used
    only to warn before creating a possible duplicate."""
    if kind not in ("task", "project"):
        return []
    nt = _norm_title(title)
    if not nt:
        return []
    rows = con.execute(
        "SELECT * FROM node WHERE kind IN ('task','project') "
        # project status is NULL (DESIGN §40); NULL NOT IN (...) is NULL, not TRUE, so
        # guard explicitly or projects would never match.
        "AND (status IS NULL OR status NOT IN ('DONE','CANCELED')) AND deleted_at IS NULL ORDER BY id"
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

def cmd_add(args, con):
    if not args.title or not args.title.strip():
        sys.exit("✗ title cannot be empty")
    args.title = args.title.strip()
    # Duplicate check (warn only, never block): a related open task/project may already
    # exist, possibly pinned at @month/@someday and easy to miss. Computed before
    # insert so the new node doesn't match itself.
    similar = _find_similar_open(con, args.title, args.kind)
    if args.sched and args.scheduled:
        sys.exit("✗ --sched (precise, writes sched table) and --scheduled (rough hint, writes node.scheduled_date) are mutually exclusive; use --sched day-to-day")
    tags = [t.strip() for t in (args.tag or "").split(",") if t.strip()]
    props = {}
    if args.proj:
        props["project"] = args.proj
    if args.deadline:
        deadline = args.deadline
    else:
        deadline = None

    status = args.status
    if not status and args.kind in ("task", "habit"):
        status = "TODO"
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

    # one dict, three SQL variants collapsed — created_at is always stamped (UTC),
    # closed_at only when --done (now) or --done --at (a resolved UTC instant).
    # One `now` read shared by both fields so a created-and-done task has
    # created_at == closed_at (the old single-INSERT datetime('now') did this).
    now = _tu.utc_now()
    row = {
        "parent_id": args.parent, "title": args.title, "kind": args.kind,
        "status": status, "priority": args.priority,
        "scheduled_date": scheduled, "deadline_date": deadline,
        "body": args.body, "created_at": now,
    }
    if closed_at == "__NOW__":
        row["closed_at"] = now
    elif closed_at:
        row["closed_at"] = closed_at
    node_id = _db.insert(con, "node", row)
    for t in tags:
        _db.upsert(con, "tag", {"node_id": node_id, "tag": t}, key=("node_id", "tag"))
    if args.proj:
        _db.upsert(con, "prop", {"node_id": node_id, "key": "project", "value": args.proj}, key=("node_id", "key"))
    # --sched: write directly to sched table (one command = "create task + schedule it as planned for a day")
    sched_hint = ""
    if getattr(args, "sched", None):
        try:
            d = _resolve_concrete_date(args.sched)
        except ValueError:
            sys.exit(f"✗ invalid --sched date '{args.sched}' (use YYYY-MM-DD / today / tomorrow / day-after-tomorrow / yesterday)")
        _db.insert(con, "sched", {"node_id": node_id, "on_date": d, "created_at": _tu.utc_now()})
        sched_hint = " " + _c(f"@{d}", "planned")

    # --link: attach a vault doc
    link_hint = ""
    if getattr(args, "link", None):
        link_doc = _strip_wikilink(args.link)
        if link_doc:
            _upsert_link(con, node_id, link_doc)
            link_hint = " → " + _c(f"[[{link_doc}]]", "meta")

    # --log: insert a log (using at_ts if given, otherwise NOW)
    log_hint = ""
    created_log_id = None
    log_body = getattr(args, "log", None)
    if log_body and log_body.strip():
        if at_ts:
            # at_ts is already a UTC instant — insert it directly (don't round-trip
            # through _insert_log's dict path, which would re-apply local→UTC)
            created_log_id = _db.insert(con, "log", {"node_id": node_id, "logged_at": at_ts, "body": log_body.strip()})
        else:
            created_log_id = _insert_log(con, node_id, log_body.strip())
        log_hint = " + log"

    # --metric: attach datapoint(s); reuse the --log carrier if present, else make a
    # dedicated (type='metric') carrier log so every datapoint still has a log.
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

    con.commit()
    st = (" " + _c(f"[{status}]", _STATUS_STYLE.get(status, "todo"))) if status else ""
    out(_c("✓", "done") + " " + _c(f"#{node_id}", "id") + " " + _c(f"{args.kind} '{args.title}'")
        + st + sched_hint + link_hint + log_hint + metric_hint)
    if similar:
        out(_c(f"⚠ {len(similar)} similar open {args.kind}(s) already exist — reuse instead of duplicating?", "later"))
        for r in similar[:5]:
            out("  " + _node_line(con, r, sched=True))
        out(_c("  if it's the same thing: wl sched <id> <day> to reschedule, or wl link / wl log it", "meta"))

def cmd_log(args, con):
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
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
    print(f"✓ log added to #{args.id}{auto_progress_hint}{metric_hint}")

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
        print(f"✓ #{nid} stopped, elapsed {secs // 60} min")
    con.commit()

def cmd_spent(args, con):
    """Record a past time spent without opening a live CLOCK pair (retrospective entries).
    wl spent <id> 45            45 minutes (default: start = NOW - 45m, end = NOW)
    wl spent <id> 45 --at 14:30  specify end time (start = at - 45m, end = at)
    wl spent <id> 1h30m          supports 1h / 30m / 1h30m
    """
    import re as _re
    nid = args.id
    if not _node_exists(con, nid):
        sys.exit(f"✗ node #{nid} not found")
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
    _db.insert(con, "clock", {"node_id": nid, "start_at": start_ts, "end_at": end_ts,
                              "elapsed_sec": mins * 60})
    con.commit()
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
        if n:
            out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " " + _c(f"unlinked [[{doc}]]"))
        else:
            out(_c(f"#{nid} had no link to [[{doc}]]", "meta"))
    con.commit()

def cmd_set(args, con):
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    if not args.key or not args.key.strip():
        sys.exit("✗ prop key cannot be empty")
    args.key = args.key.strip()
    hint = _reserved_prop_hint(args.key)
    if hint:
        # a core node field (status/priority/tags/title/…) must not become a UDA
        # prop — that would shadow the real field. Reject with a pointer to the right command.
        sys.exit(f"✗ '{args.key}' is a reserved node field, not a UDA prop — {hint}\n"
                 f"  (plain `wl set` / `wl prop set` would silently create a misleading shadow prop)")
    if args.key in _META_LOG_TYPES:
        # goal/summary/overview/top5 are history-preserving meta fields, stored as typed
        # logs (not single-value props): each write appends a log, the latest is current.
        # This is the key-routed shortcut for `wl meta set` — keep the output identical.
        log_id = _set_typed_log(con, args.id, args.key, args.value)
        con.commit()
        at = _db.get(con, "log", log_id)["logged_at"]
        out(_c(f"✓ #{args.id} {args.key} (logged at {at}): {args.value}", "meta"))
        return
    _upsert_prop(con, args.id, args.key, args.value)
    con.commit()
    print(f"✓ #{args.id} {args.key}={args.value}")

def cmd_tag(args, con):
    """Add/remove real tags on a node (the tag table): `wl tag <id> +work -planned`.
    A bare word adds (same as +word); no ops lists current tags. This is the direct
    editor for the real tag field — `wl set <id> tags ...` is rejected on purpose so
    it can't quietly create a shadow prop."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
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
    parts = []
    if added:
        parts.append(_c("+" + ",".join(added), "planned"))
    if removed:
        parts.append(_c("-" + ",".join(removed), "later"))
    out(_c("✓", "done") + " " + _c(f"#{args.id}", "id") + " tags " + " ".join(parts))


def cmd_tag_ls(args, con):
    """List a node's real tags — the read verb of the tag group (= bare `wl tag <id>`)."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    tags = [r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=args.id, order="tag")]
    out(_c(f"#{args.id} tags: " + (":".join(tags) if tags else "(none)"), "meta"))


def cmd_tag_rm(args, con):
    """Remove tag(s) from a node — the delete verb of the tag group (= `wl tag <id> -tag`).
    Each argument is a plain tag name (a leading + is stripped; to pass a `-tag` use the
    inline form `wl tag <id> -tag`, since argparse would read a leading - as a flag)."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    removed = []
    for raw in args.tags:
        t = raw.lstrip("+-").strip()
        if t:
            _db.delete(con, "tag", node_id=args.id, tag=t)
            removed.append(t)
    con.commit()
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

    if log_id is not None:
        row = _db.get(con, "log", log_id)
        if not row:
            sys.exit(f"✗ log #{log_id} not found")
        nmetric = _db.count(con, "metric", log_id=log_id)
        soft_delete_log(con, log_id)
        con.commit()
        body_preview = row["body"][:60] + ("…" if len(row["body"]) > 60 else "")
        extra = f" + {nmetric} metric(s)" if nmetric else ""
        out(_c(f"✓ deleted log #{log_id}{extra} (node #{row['node_id']}, {_tu.utc_to_local(row['logged_at'])}): {body_preview}", "meta"))
        return

    # --node <id>: delete latest log for that day
    if not _node_exists(con, nid):
        sys.exit(f"✗ node #{nid} not found")
    date = getattr(args, "date", None)
    if date:
        try:
            date = _resolve_concrete_date(date)
        except ValueError:
            sys.exit(f"✗ invalid --date '{date}'")
    else:
        date = _tu.today()

    sql = (f"SELECT id, logged_at, body FROM log WHERE node_id = ? AND {_tu.local_day_sql('logged_at')} = ? "
           "AND deleted_at IS NULL ORDER BY id DESC")
    if not args.all:
        sql += " LIMIT 1"
    rows = list(con.execute(sql, (nid, date)))
    if not rows:
        out(_c(f"(node #{nid} has no non-CLOCK logs on {date})", "meta"))
        return
    for r in rows:
        nmetric = _db.count(con, "metric", log_id=r["id"])
        soft_delete_log(con, r["id"])
        body_preview = r["body"][:60] + ("…" if len(r["body"]) > 60 else "")
        extra = f" + {nmetric} metric(s)" if nmetric else ""
        out(_c(f"✓ deleted log #{r['id']}{extra} (node #{nid}, {_tu.utc_to_local(r['logged_at'])}): {body_preview}", "meta"))
    con.commit()

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
    body_preview = new_row["body"][:60] + ("…" if len(new_row["body"]) > 60 else "")
    out(_c(f"✓ relog #{log_id} (node #{row['node_id']}, {_tu.utc_to_local(new_row['logged_at'])}): {body_preview}", "meta"))


def cmd_log_ls(args, con):
    """List a node's log entries — the read verb of the log group. A simple node-scoped
    stream (`#L<id> [time] body`); for the full filterable / windowed view use `wl logs
    --id <id>` (presets, --since/--until, --by-task, --group, …)."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    rows = _db.query(con, "log", cols="id, logged_at, body", node_id=args.id, order="logged_at")
    if not rows:
        out(_c(f"#{args.id} has no logs", "meta"))
        return
    full = _log_full(args)
    for r in rows:
        prefix = f"#L{r['id']} [{_tu.utc_to_local(r['logged_at'])}] "
        body = _truncate_log_body(r["body"], len(prefix), full=full)
        out(_c(f"#L{r['id']}", "id") + " "
            + _c(f"[{_tu.utc_to_local(r['logged_at'])}]", "meta") + " " + body)


def cmd_log_group(args, con):
    """Dispatch `wl log <add|ls|edit|rm>` (the metric-style entity group).
    `add` is the default verb (`wl log <id> "body"` == `wl log add <id> "body"`); `edit`
    is `wl relog` and `rm` is `wl unlog` (both keep their top-level shortcuts)."""
    sub = getattr(args, "log_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl log <id> \"body\"  |  wl log <add|ls|edit|rm> … (see `wl log --help`)")
    {"add": cmd_log, "ls": cmd_log_ls, "edit": cmd_relog, "rm": cmd_unlog}[sub](args, con)


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

    rows = con.execute("""
        SELECT c.node_id, c.start_at, n.title, n.status, n.priority
        FROM clock c JOIN node n ON c.node_id = n.id
        WHERE c.end_at IS NULL AND c.deleted_at IS NULL AND n.deleted_at IS NULL
        ORDER BY c.start_at DESC
    """).fetchall()

    if not rows:
        out(_c("(no active task right now; use wl start <id> to start timing, wl day for today's progress)", "meta"))
        return

    brief = getattr(args, "brief", False)
    now = _dt.fromisoformat(_tu.utc_now())  # UTC, to match the UTC-stored start_at
    today = _tu.today()
    full = _log_full(args)
    for r in rows:
        started = _dt.fromisoformat(r["start_at"])
        mins = int((now - started).total_seconds() / 60)
        pri = (_c(f"[#{r['priority']}]", _PRI_STYLE.get(r["priority"])) + " ") if r["priority"] else ""
        # head: id + priority + title + current session
        head_tail = "" if brief else " " + _c(f"({mins}min, since {_tu.utc_to_local(r['start_at'])[11:16]})", "meta")
        out(_c("⏱", "clock") + " " + _c(f"#{r['node_id']}", "id") + " " + pri + _c(r["title"]) + head_tail)
        if brief:
            continue
        # today's completed clock total + the current open session (helps decide "continue or stop")
        done_sec = con.execute(
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ? AND {_tu.local_day_sql('end_at')} = ? AND deleted_at IS NULL",
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
    if not _node_exists(con, nid):
        sys.exit(f"✗ node #{nid} not found")
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
        if not _node_exists(con, nid):
            sys.exit(f"✗ node #{nid} not found")
    total = 0
    for nid in args.ids:
        # include_deleted: tombstone the FULL structural subtree, so a live node hanging
        # under an already-tombstoned intermediate doesn't get orphaned.
        for did in [nid] + _collect_descendants(con, nid, include_deleted=True):
            soft_delete_node(con, did)
        total += 1
    con.commit()
    out(_c(f"✓ soft-deleted {len(args.ids)} node(s) + subtree (reversible; clear deleted_at to restore)", "meta"))


def cmd_node_edit(args, con):
    """Edit a node's own fields: title / priority / kind / body / scheduled / deadline.
    (Status has its own verbs done/cancel/…; parent → `node reparent`; tags → `wl tag`.)"""
    nid = args.id
    if not _node_exists(con, nid):
        sys.exit(f"✗ node #{nid} not found")
    changes = {}
    if args.title is not None:
        if not args.title.strip():
            sys.exit("✗ title cannot be empty")
        changes["title"] = args.title.strip()
    if args.priority is not None:
        changes["priority"] = args.priority
    if args.kind is not None:
        changes["kind"] = args.kind
    if args.body is not None:
        changes["body"] = args.body
    if args.scheduled is not None:
        try:
            changes["scheduled_date"] = _norm_sched(args.scheduled) if args.scheduled else None
        except ValueError as e:
            sys.exit(f"✗ {e}")
    if args.deadline is not None:
        changes["deadline_date"] = args.deadline or None
    if not changes:
        sys.exit("✗ nothing to edit (give --title / --priority / --kind / --body / --scheduled / --deadline)")
    _db.update(con, "node", nid, changes)
    con.commit()
    out(_c(f"✓ #{nid} updated: " + ", ".join(changes), "meta"))


# --- prop entity group: set / ls / rm ---
def cmd_prop_ls(args, con):
    """List a node's UDA props (key=value). The read primitive for prop (props are also
    shown inline by `wl show`)."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    rows = _db.query(con, "prop", cols="key, value", node_id=args.id, order="key")
    if not rows:
        out(_c(f"(#{args.id} has no props)", "meta"))
        return
    for r in rows:
        out(_c(f"#{args.id} ", "id") + _c(f"{r['key']}={r['value']}"))


def cmd_prop_rm(args, con):
    """Remove a UDA prop from a node (soft-delete the row). Also the `wl unset`
    shortcut. The delete counterpart of `wl set`."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    key = (args.key or "").strip()
    if not key:
        sys.exit("✗ prop key cannot be empty")
    if key in _META_LOG_TYPES:
        # key-routed shortcut, symmetric with `wl set`: a meta field lives in the log table
        # as a typed log, not a prop — clear it there (= wl meta rm).
        n = _db.delete(con, "log", node_id=args.id, tag=key)
        con.commit()
        out(_c(f"✓ #{args.id} {key} cleared ({n} log(s))" if n
               else f"(#{args.id} has no {key})", "meta"))
        return
    n = _db.delete(con, "prop", node_id=args.id, key=key)
    con.commit()
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
_AGENT_APP = "claude"                       # this CLI binds the Claude Code session
_AGENT_PREFIX = "agent_session."            # cross-app prefix
_AGENT_KEY = _AGENT_PREFIX + _AGENT_APP       # agent_session.claude
_AGENT_METRIC_TAG = "agent_session"          # metric tag for the bind-history trail (mirrors the prop)

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
    row = _db.query_one(con, "prop", cols="node_id", key=_AGENT_KEY, value=sid)
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
    """Whether the (node, session) bind is already in the history trail — dedup by the actual
    `agent_session` metric, so re-binding the same pair never duplicates it, yet a pair that
    was bound without a history record still gets one on a later bind."""
    return _db.query_one(con, "metric", cols="id", node_id=nid,
                         tag=_AGENT_METRIC_TAG, value_text=sid) is not None

def _record_bind_history(con, nid, sid):
    """Append-only record that session `sid` was bound to node `nid` (light design).

    Two stores with different jobs:
      * the prop `agent_session.claude` is the *live* pointer — one session → one node, and it
        MOVES on rebind, so it always names the node a session is currently on;
      * this log + metric is the *history* — it stays on the node forever, so
        `wl metric ls <id> --tag agent_session --all` / `wl show <id>` recover every session a
        node was ever worked under.

    Written once per (node, session) bind, NOT stamped onto every later log — one row per
    association instead of tagging every write, which is the whole point of the light design."""
    log_id = _db.insert(con, "log", {
        "node_id": nid, "logged_at": _tu.utc_now(),
        "body": f"agent session bound · {_AGENT_APP}:{sid[:8]}…",
        "tag": "metric",   # auto metric-carrier log (same convention as `wl metric add`)
    })
    _db.insert(con, "metric", {
        "log_id": log_id, "node_id": nid, "tag": _AGENT_METRIC_TAG,
        "value_num": None, "value_text": sid, "unit": None,
        "note": _AGENT_APP, "at": _tu.utc_now(),
    })

def _short(s, n=50):
    """Truncate a title for one-line bind output (plain char count is fine here)."""
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"

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

def cmd_agent(args, con):
    """`wl agent` — bind the current agent session to a node.
    wl agent <id> = set · wl agent = show current · wl agent ls = list all · wl agent rm = unbind."""
    sub = getattr(args, "agent_sub", None)
    if sub == "set":
        sid = _agent_need_sid()
        nid = args.id
        if not _node_exists(con, nid):
            sys.exit(f"✗ node #{nid} not found")
        cur = _db.query_one(con, "prop", cols="value", node_id=nid, key=_AGENT_KEY)
        if cur and cur["value"] != sid:
            out(_c(f"⚠ #{nid} 已被 session {cur['value'][:8]}… 绑定,将被覆盖", "later"))
        # Record the history trail unless told not to AND it isn't already recorded — dedup by the
        # actual metric (not "is the prop already set"), so a pair bound before history existed
        # (an early auto-bind, or a --no-record bind) still gets recorded on a later bind.
        do_record = getattr(args, "record", True) and not _has_agent_history(con, nid, sid)
        _db.delete(con, "prop", key=_AGENT_KEY, value=sid)   # one session → one node (live pointer)
        _upsert_prop(con, nid, _AGENT_KEY, sid)
        if do_record:
            _record_bind_history(con, nid, sid)   # append-only history trail
        con.commit()
        _invalidate_agent_cache(sid)   # binding changed → integrations re-fetch via `wl agent context`
        title = (_db.get(con, "node", nid) or {})["title"]
        line = _c("✓", "done") + " " + _c(f"#{nid}", "id") + " ← " + _c(f"{_AGENT_APP}:{sid[:8]}…", "meta") + " · " + _short(title)
        if do_record:
            line += _c("  +history", "meta")
        out(line)
        return
    if sub == "ls":
        rows = _db.query(con, "prop", cols="node_id, key, value", key__like=_AGENT_PREFIX + "%", order="key, value")
        if not rows:
            out(_c("(no session bindings)", "meta"))
            return
        for r in rows:
            node = _db.get(con, "node", r["node_id"])
            title = node["title"] if node else "(deleted)"
            out(_c(f"#{r['node_id']}", "id") + " ← " + _c(f"{r['key'][len(_AGENT_PREFIX):]}:{r['value'][:8]}…", "meta") + " · " + _short(title))
        return
    if sub == "rm":
        sid = _agent_need_sid()
        n = _db.delete(con, "prop", key=_AGENT_KEY, value=sid)
        con.commit()
        _invalidate_agent_cache(sid)   # drop cached binding so the hook stops injecting
        out(_c(f"✓ unbound (session {sid[:8]}…)" if n else f"(session {sid[:8]}… 本来就没绑定)", "meta"))
        return
    if sub == "context":
        # Machine output for integrations (the context hook): a `<node_id>\t<title>` line, or
        # with --hook the ready-to-emit UserPromptSubmit JSON (so the hook needs no `jq`). Empty
        # when unbound. Plain print (not `out`) — consumed by scripts, not rendered.
        sid = _current_session_id()
        print(_agent_hook_json(con, sid) if getattr(args, "hook", False) else _agent_context_line(con, sid))
        return
    # bare `wl agent` → show the current session's binding
    sid = _agent_need_sid()
    row = _db.query_one(con, "prop", cols="node_id", key=_AGENT_KEY, value=sid)
    if not row:
        out(_c(f"(session {sid[:8]}… 未绑定任何任务)", "meta"))
        return
    title = (_db.get(con, "node", row["node_id"]) or {})["title"]
    out(_c(f"#{row['node_id']}", "id") + " ← " + _c(f"{_AGENT_APP}:{sid[:8]}…", "meta") + " · " + _short(title))


# --- clock entity group: ls / edit / rm (create = start/stop/spent) ---
def cmd_clock_ls(args, con):
    """List a node's clock intervals (start → end, duration). Read primitive for clock."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    rows = _db.query(con, "clock", cols="id, start_at, end_at, elapsed_sec", node_id=args.id, order="id")
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
    out(_c(f"✓ clock #C{args.clock_id} updated: " + ", ".join(changes), "meta"))


def cmd_clock_rm(args, con):
    """Soft-delete a clock interval — remove a wrong `wl spent`/start-stop entry."""
    for cid in args.clock_ids:
        if not _db.exists(con, "clock", id=cid):
            sys.exit(f"✗ clock interval #C{cid} not found")
    for cid in args.clock_ids:
        _db.delete(con, "clock", id=cid)
    con.commit()
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
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    rows = _db.query(con, "link", cols="vault_doc", node_id=args.id, order="vault_doc")
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
