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
    _set_typed_log,
    _META_LOG_TYPES,
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
from ..xdg import _resolve_db_path, _resolve_aliases_path, _xdg_data_home, _xdg_config_home

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
    only to warn before creating a possible duplicate (#435)."""
    if kind not in ("task", "project"):
        return []
    nt = _norm_title(title)
    if not nt:
        return []
    rows = con.execute(
        "SELECT * FROM node WHERE kind IN ('task','project') "
        # project status is NULL (DESIGN §40); NULL NOT IN (...) is NULL, not TRUE, so
        # guard explicitly or projects would never match.
        "AND (status IS NULL OR status NOT IN ('DONE','CANCELED')) ORDER BY id"
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
    # exist, possibly pinned at @month/@someday and easy to miss (#435). Computed before
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
        con.execute("INSERT OR IGNORE INTO tag (node_id, tag) VALUES (?, ?)", (node_id, t))
    if args.proj:
        con.execute("INSERT OR IGNORE INTO prop (node_id, key, value) VALUES (?, ?, ?)", (node_id, "project", args.proj))
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
        link_doc = args.link.strip()
        if link_doc:
            con.execute("INSERT OR IGNORE INTO link (node_id, vault_doc) VALUES (?, ?)", (node_id, link_doc))
            link_hint = " → " + _c(f"[[{link_doc}]]", "meta")

    # --log: insert a log (using at_ts if given, otherwise NOW)
    log_hint = ""
    created_log_id = None
    log_body = getattr(args, "log", None)
    if log_body and log_body.strip():
        if at_ts:
            # at_ts is already a UTC instant — insert it directly (don't round-trip
            # through _insert_log's dict path, which would re-apply local→UTC)
            _db.insert(con, "log", {"node_id": node_id, "logged_at": at_ts, "body": log_body.strip()})
        else:
            _insert_log(con, node_id, log_body.strip())
        created_log_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
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
        _insert_log(con, args.id, entry)
    except ValueError as e:
        sys.exit(f"✗ invalid date: {e}")
    log_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    # auto TODO -> DOING (when no --date, "I logged something" implies "I'm working on it")
    # backfilling history (--date) does not change status; --keep-status explicitly disables
    auto_progress_hint = ""
    if not getattr(args, "keep_status", False) and not date:
        row = con.execute("SELECT status FROM node WHERE id = ?", (args.id,)).fetchone()
        if row and row["status"] == "TODO":
            con.execute("UPDATE node SET status = 'DOING' WHERE id = ?", (args.id,))
            auto_progress_hint = " (status: TODO → DOING)"
    # --metric: attach structured datapoint(s) to this log (inherit its timestamp)
    metric_hint = ""
    specs = getattr(args, "metric", None)
    if specs:
        log_at = con.execute("SELECT logged_at FROM log WHERE id = ?", (log_id,)).fetchone()["logged_at"]
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
        rule = con.execute(
            "SELECT rrule FROM sched WHERE node_id = ? AND rrule IS NOT NULL LIMIT 1", (nid,)
        ).fetchone()
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
        con.execute(
            "UPDATE node SET status = 'LATER', scheduled_date = ? WHERE id = ?",
            (when, nid),
        )
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
        if con.execute("SELECT 1 FROM clock WHERE node_id = ? AND end_at IS NULL LIMIT 1", (nid,)).fetchone():
            out(_c(f"⚠ #{nid} already has a running clock — wl stop it first (skipped)", "later"))
            continue
        con.execute("UPDATE node SET status = 'DOING' WHERE id = ?", (nid,))
        con.execute("INSERT INTO clock (node_id, start_at) VALUES (?, ?)", (nid, ts))
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
        row = con.execute(
            "SELECT id, start_at FROM clock WHERE node_id = ? AND end_at IS NULL ORDER BY id DESC LIMIT 1",
            (nid,),
        ).fetchone()
        if not row:
            sys.exit(f"✗ no open clock for #{nid}")
        started = datetime.fromisoformat(row["start_at"])
        stopped = datetime.fromisoformat(stop_ts)
        if stopped < started:
            sys.exit(f"✗ --at {stop_ts} is earlier than the clock start {row['start_at']} (#{nid})")
        secs = max(60, int((stopped - started).total_seconds()))  # floor at 1 min
        con.execute("UPDATE clock SET end_at = ?, elapsed_sec = ? WHERE id = ?", (stop_ts, secs, row["id"]))
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
    con.execute(
        "INSERT INTO clock (node_id, start_at, end_at, elapsed_sec) VALUES (?, ?, ?, ?)",
        (nid, start_ts, end_ts, mins * 60),
    )
    con.commit()
    print(f"✓ #{nid} spent {mins}min ({_tu.utc_to_local(start_ts)[11:16]} → {_tu.utc_to_local(end_ts)[11:16]})")

def cmd_link(args, con):
    if not args.vault_doc or not args.vault_doc.strip():
        sys.exit("✗ vault_doc cannot be empty")
    args.vault_doc = args.vault_doc.strip()
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    for nid in ids:
        con.execute("INSERT OR IGNORE INTO link (node_id, vault_doc) VALUES (?, ?)", (nid, args.vault_doc))
    con.commit()
    for nid in ids:
        out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " " + _c(f"linked → [[{args.vault_doc}]]"))

def cmd_unlink(args, con):
    """Remove a single vault-doc link from a node (#426). Symmetric with wl link;
    previously a mistaken link could only be cleared wholesale via `wl set links ''`."""
    if not args.vault_doc or not args.vault_doc.strip():
        sys.exit("✗ vault_doc cannot be empty")
    args.vault_doc = args.vault_doc.strip()
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    for nid in ids:
        cur = con.execute("DELETE FROM link WHERE node_id = ? AND vault_doc = ?", (nid, args.vault_doc))
        if cur.rowcount:
            out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " " + _c(f"unlinked [[{args.vault_doc}]]"))
        else:
            out(_c(f"#{nid} had no link to [[{args.vault_doc}]]", "meta"))
    con.commit()

def cmd_set(args, con):
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    if not args.key or not args.key.strip():
        sys.exit("✗ prop key cannot be empty")
    args.key = args.key.strip()
    if args.key.lower() in ("tag", "tags"):
        # Guard the #441 footgun: 'tags' is not a UDA prop. Setting it here used to
        # silently create a shadow 'tags' prop while the real tag field went unchanged.
        sys.exit("✗ 'tags' is not a prop — use `wl tag <id> +x -y` to edit real tags "
                 "(plain `wl set` here would silently create a misleading shadow prop)")
    if args.key in _META_LOG_TYPES:
        # goal/summary/overview/top5 are history-preserving meta fields, stored as typed
        # logs (not single-value props): each write appends a log, the latest is current.
        _set_typed_log(con, args.id, args.key, args.value)
        con.commit()
        print(f"✓ #{args.id} {args.key} (logged): {args.value}")
        return
    _upsert_prop(con, args.id, args.key, args.value)
    con.commit()
    print(f"✓ #{args.id} {args.key}={args.value}")

def cmd_tag(args, con):
    """Add/remove real tags on a node (the tag table): `wl tag <id> +work -planned`.
    A bare word adds (same as +word); no ops lists current tags. This is the direct
    editor for the real tag field — `wl set <id> tags ...` is rejected on purpose so
    it can't quietly create a shadow prop (#440/#441)."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    ops = [o.strip() for o in (args.ops or []) if o.strip()]
    if not ops:
        tags = [r["tag"] for r in con.execute(
            "SELECT tag FROM tag WHERE node_id = ? ORDER BY tag", (args.id,))]
        out(_c(f"#{args.id} tags: " + (":".join(tags) if tags else "(none)"), "meta"))
        return
    added, removed = [], []
    for op in ops:
        if op.startswith("-"):
            t = op[1:].strip()
            if t:
                con.execute("DELETE FROM tag WHERE node_id = ? AND tag = ?", (args.id, t))
                removed.append(t)
        else:
            t = op[1:].strip() if op.startswith("+") else op
            if t:
                con.execute("INSERT OR IGNORE INTO tag (node_id, tag) VALUES (?, ?)", (args.id, t))
                added.append(t)
    con.commit()
    parts = []
    if added:
        parts.append(_c("+" + ",".join(added), "planned"))
    if removed:
        parts.append(_c("-" + ",".join(removed), "later"))
    out(_c("✓", "done") + " " + _c(f"#{args.id}", "id") + " tags " + " ".join(parts))

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
        _insert_log(con, nid, body)
        log_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        # structured "done today" signal (one per node per day) — not "a log exists"
        checkin_metric(con, log_id, nid, today)
        if args.done:
            con.execute(
                "UPDATE node SET status = 'DONE', closed_at = datetime('now') WHERE id = ?", (nid,)
            )
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
        row = con.execute(
            "SELECT id, start_at FROM clock WHERE node_id = ? AND end_at IS NULL ORDER BY id DESC LIMIT 1",
            (nid,),
        ).fetchone()
        if row:
            now_s = _tu.utc_now()
            secs = max(60, int((datetime.fromisoformat(now_s) - datetime.fromisoformat(row["start_at"])).total_seconds()))
            con.execute(
                "UPDATE clock SET end_at = ?, elapsed_sec = ? WHERE id = ?",
                (now_s, secs, row["id"]),
            )
        con.execute("UPDATE node SET status = 'WAIT' WHERE id = ?", (nid,))
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
        row = con.execute("SELECT node_id, logged_at, body FROM log WHERE id = ?", (log_id,)).fetchone()
        if not row:
            sys.exit(f"✗ log #{log_id} not found")
        nmetric = con.execute("SELECT COUNT(*) FROM metric WHERE log_id = ?", (log_id,)).fetchone()[0]
        con.execute("DELETE FROM log WHERE id = ?", (log_id,))
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
           "ORDER BY id DESC")
    if not args.all:
        sql += " LIMIT 1"
    rows = list(con.execute(sql, (nid, date)))
    if not rows:
        out(_c(f"(node #{nid} has no non-CLOCK logs on {date})", "meta"))
        return
    for r in rows:
        nmetric = con.execute("SELECT COUNT(*) FROM metric WHERE log_id = ?", (r["id"],)).fetchone()[0]
        con.execute("DELETE FROM log WHERE id = ?", (r["id"],))
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
    row = con.execute("SELECT id, node_id, logged_at, body FROM log WHERE id = ?", (log_id,)).fetchone()
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

    sets, params = [], []
    if new_body is not None:
        sets.append("body = ?")
        params.append(new_body)
    if new_ts is not None:
        sets.append("logged_at = ?")
        params.append(new_ts)
    params.append(log_id)
    con.execute(f"UPDATE log SET {', '.join(sets)} WHERE id = ?", params)
    con.commit()

    new_row = con.execute("SELECT logged_at, body FROM log WHERE id = ?", (log_id,)).fetchone()
    body_preview = new_row["body"][:60] + ("…" if len(new_row["body"]) > 60 else "")
    out(_c(f"✓ relog #{log_id} (node #{row['node_id']}, {_tu.utc_to_local(new_row['logged_at'])}): {body_preview}", "meta"))

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
        WHERE c.end_at IS NULL
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
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE node_id = ? AND {_tu.local_day_sql('end_at')} = ?",
            (r["node_id"], today),
        ).fetchone()["s"]
        total_min = mins + int((done_sec or 0) / 60)  # includes current open session
        out("    " + _c(f"today's total {total_min}min ({total_min // 60}h{total_min % 60}m), includes current session", "meta"))
        # latest plain-note log (oneline truncated)
        last = con.execute(
            "SELECT body FROM log WHERE node_id = ? AND tag IS NULL ORDER BY id DESC LIMIT 1", (r["node_id"],),
        ).fetchone()
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

    parts = ["status = ?"]
    sql_params_extra = [new_status]
    if close:
        if at_ts:
            parts.append("closed_at = ?")
            sql_params_extra.append(at_ts)
        else:
            parts.append("closed_at = datetime('now')")
    elif reopen:
        parts.append("closed_at = NULL")
    sql = f"UPDATE node SET {', '.join(parts)} WHERE id = ?"
    for nid in ids:
        con.execute(sql, sql_params_extra + [nid])
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

