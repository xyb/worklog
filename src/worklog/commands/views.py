"""worklog commands: views group."""
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
from ..helpers import _ORDER_BY_PRI_ID, _TIME_KINDS  # noqa: F401
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
    GENERIC_TAGS,
)
from ..queries import (
    _ancestors_chain,
    _check_ids_exist,
    _collect_descendants,
    _has_tag,
    _has_checkin,
    _latest_typed_log,
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
)
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



def cmd_tree(args, con):
    inc_cancel = getattr(args, "show_canceled", False)
    log_tail = _resolve_log_tail(args, _is_brief(args, "no_logs"), default_tail=3)
    if args.by:
        _tree_by(con, args.by)
        return
    full = _log_full(args)
    if args.root is None and args.kind is None and args.depth is None:
        # bare wl tree: areas one level + timeline up to today
        _print_default_tree(con, include_canceled=inc_cancel, log_tail=log_tail, full=full)
        return
    if args.root is not None:
        # expand subtree from a specified node as root (no longer requires parent_id IS NULL)
        root = con.execute("SELECT * FROM node WHERE id = ?", (args.root,)).fetchone()
        if not root:
            sys.exit(f"✗ node #{args.root} not found")
        roots = [root]
    else:
        root_sql = "SELECT * FROM node WHERE parent_id IS NULL"
        params_root = []
        if args.kind:
            root_sql += " AND kind = ?"
            params_root.append(args.kind)
        if not inc_cancel:
            frag, p = _status_filter_sql(include_canceled=False)
            if frag:
                root_sql += " AND " + frag
                params_root.extend(p)
        roots = list(con.execute(root_sql, params_root))

    if not roots:
        print("(no root nodes)")
        return

    # default depth limit to avoid flooding: full tree default 2 (area->project / year->quarter overview), --root default 3 (one extra level for drill-down)
    max_depth = args.depth if args.depth is not None else (3 if args.root is not None else 2)
    for root in roots:
        _print_tree(con, root, depth=0, max_depth=max_depth,
                    include_canceled=inc_cancel, log_tail=log_tail, full=full)

def cmd_day(args, con):
    """Reproduce a single day's progress (default today): bucket by work/personal -> project -> task -> that day's logs.
    Driven by log dates (not the day node), so it works for historical days too."""
    from datetime import date as _date

    if args.date:
        try:
            target = _resolve_concrete_date(args.date)
        except ValueError:
            sys.exit(f"✗ invalid date '{args.date}' (use YYYY-MM-DD / today / yesterday / day-before-yesterday / tomorrow / day-after-tomorrow)")
    else:
        target = _date.today().isoformat()
    day = con.execute(
        "SELECT * FROM node WHERE kind = 'day' AND title LIKE ? ORDER BY id LIMIT 1",
        (target + "%",),
    ).fetchone()
    # date context: date + auto-computed weekday + date_meta label (holiday/vacation/working-day swap)
    wd = _cn_weekday(target)
    label = _date_label(con, target)
    head = target + (f" {wd}" if wd else "") + (f" · {label}" if label else "")
    out(_c(head, "header"))
    # meta (history-preserving typed logs on the day node): goal / recap(summary) / Top5;
    # plus the parent week node's overview. Each is the latest log of that type.
    if day:
        g = _latest_typed_log(con, day["id"], "goal")
        if g and g["body"]:
            out(_c("  > 🎯 " + g["body"], "meta"))
        s = _latest_typed_log(con, day["id"], "summary")
        if s and s["body"]:
            at = s["logged_at"]
            when = _c(f" (written at {at[5:16]})", "meta") if at else ""
            out(_c("  > Recap: " + s["body"], "meta") + when)
            # stale check: count plain-note logs (type IS NULL) added after the recap;
            # meta logs (goal/summary/…) and metric carriers (type='metric') don't count.
            if at:
                newer = con.execute(
                    "SELECT COUNT(*) FROM log WHERE logged_at > ? "
                    "AND substr(logged_at, 1, 10) = ? AND body NOT LIKE 'CLOCK_%' AND type IS NULL",
                    (at, target),
                ).fetchone()[0]
                if newer:
                    out(_c(f"  > ⚠ {newer} change(s) after recap; consider rewriting via wl recap", "doing"))
        t5 = _latest_typed_log(con, day["id"], "top5")
        if t5 and t5["body"]:
            out(_c("  > Top5: " + t5["body"], "meta"))
        wk = con.execute("SELECT id FROM node WHERE id = ? AND kind = 'week'", (day["parent_id"],)).fetchone()
        if wk:
            ov = _latest_typed_log(con, wk["id"], "overview")
            if ov and ov["body"]:
                out(_c("  > This week: " + ov["body"], "meta"))

    inc_cancel = getattr(args, "show_canceled", False)
    cfrag, cparams = _status_filter_sql(include_canceled=inc_cancel, col="node.status")
    cancel_sql = (" AND " + cfrag) if cfrag else ""
    rows = con.execute(
        rf"""SELECT log.node_id, log.logged_at, log.body,
                   node.title, node.status, node.priority, node.kind
            FROM log JOIN node ON log.node_id = node.id
            WHERE date(log.logged_at) = ?
              AND log.body NOT LIKE 'CLOCK\_%' ESCAPE '\'
              AND node.kind IN ('task', 'habit', 'meetlog')
              {cancel_sql}
            ORDER BY log.logged_at, log.node_id""",
        [target] + cparams,
    ).fetchall()

    # items: tasks with logs + tasks scheduled but with no log yet today (planned items visible ahead of time)
    items = {}
    for r in rows:
        items.setdefault(r["node_id"], {"node": r, "logs": []})["logs"].append(r["body"])
    sched_ids = _scheduled_node_ids(con, target)
    for nid in sched_ids:
        if nid not in items:
            nr = con.execute(
                "SELECT id AS node_id, title, status, priority, kind FROM node WHERE id = ?", (nid,)
            ).fetchone()
            if nr and nr["kind"] in ("task", "habit", "meetlog"):
                if not inc_cancel and nr["status"] == "CANCELED":
                    continue
                items[nid] = {"node": nr, "logs": []}

    if not items:
        out(_c(f"  (no log progress for {target}, and nothing planned)", "meta"))
        return

    # log_tail priority: --no-logs/--brief -> 0 / --all-logs -> None (full) /
    # --log-tail N -> N / default 3 (elide middle, only the end visible to keep wl day from blowing up on long logs)
    brief = _is_brief(args, "no_logs")
    log_tail = _resolve_log_tail(args, brief, default_tail=3)
    _render_day_group(con, items, by=getattr(args, "by", "plan"),
                      sched_ids=sched_ids, log_tail=log_tail,
                      full=_log_full(args), day=target)

    # bottom stats: per-status distribution + planned-not-done count + CLOCK time
    import re

    logged = {r["node_id"]: (r["status"] or "TODO") for r in rows}
    stats = {}
    for s in logged.values():
        stats[s] = stats.get(s, 0) + 1
    done = stats.get("DONE", 0)
    total = len(logged)
    # mirror the per-row hint: a terminal-status (DONE/CANCELED) task is not "not-done"
    planned_undone = sum(
        1 for nid in items
        if not items[nid]["logs"] and items[nid]["node"]["status"] not in ("DONE", "CANCELED")
    )
    parts = [f"{s} {stats[s]}" for s in ("DONE", "DOING", "TODO", "LATER", "WAIT", "DEFERRED", "CANCELED") if stats.get(s)]
    total_sec = con.execute(
        "SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE substr(end_at, 1, 10) = ?",
        (target,),
    ).fetchone()["s"]
    total_min = int((total_sec or 0) / 60)
    print()
    line = f"  ── {target}: {done}/{total} tasks with progress"
    if parts:
        line += " · " + " · ".join(parts)
    if planned_undone:
        line += f" · planned·not-done {planned_undone}"
    if total_min:
        line += f" · CLOCK {total_min}min ({total_min // 60}h{total_min % 60}m)"
    out(_c(line, "meta"))

def _tree_by(con, by):
    """Flat 2-level view, regrouped by dimension (avoids deep time-layered nesting)."""
    if by == "tag":
        tags = [r["tag"] for r in con.execute("SELECT DISTINCT tag FROM tag ORDER BY tag")]
        sem = [t for t in tags if t not in GENERIC_TAGS]
        if not sem:
            print("(no semantic tags)")
            return
        for tag in sem:
            rows = con.execute(
                "SELECT n.* FROM node n JOIN tag t ON n.id = t.node_id WHERE t.tag = ? "
                "ORDER BY n.priority NULLS LAST, n.id",
                (tag,),
            ).fetchall()
            out(_c(f"#{tag}", "tag") + "  " + _c(f"({len(rows)})", "meta"))
            for n in rows:
                out(_node_line(con, n))

    elif by == "project":
        projects = con.execute(
            f"SELECT * FROM node WHERE kind = 'project' {_ORDER_BY_PRI_ID}"
        ).fetchall()
        if not projects:
            print("(no project nodes)")
            return
        claimed = set()
        for proj in projects:
            proj_tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (proj["id"],))} - GENERIC_TAGS
            ids = set()
            # (a) structural children
            for r in con.execute("SELECT id FROM node WHERE parent_id = ?", (proj["id"],)):
                ids.add(r["id"])
            # (b) task/meetlog/habit sharing a semantic tag
            if proj_tags:
                qm = ",".join("?" * len(proj_tags))
                for r in con.execute(
                    f"SELECT DISTINCT n.id FROM node n JOIN tag t ON n.id = t.node_id "
                    f"WHERE t.tag IN ({qm}) AND n.kind IN ('task','meetlog','habit')",
                    list(proj_tags),
                ):
                    ids.add(r["id"])
            pri = (" " + _c(f"[#{proj['priority']}]", _PRI_STYLE.get(proj["priority"]))) if proj["priority"] else ""
            out("▸ " + _c(f"#{proj['id']}", "id") + pri + " " + _c(proj["title"], "header") + "  " + _c(f"({len(ids)})", "meta"))
            for nid in sorted(ids):
                n = con.execute("SELECT * FROM node WHERE id = ?", (nid,)).fetchone()
                claimed.add(nid)
                out(_node_line(con, n))
            if not ids:
                out("    " + _c("(no linked tasks)", "meta"))
        # orphans: task/meetlog/habit not attached to any project
        orphans = con.execute(
            f"SELECT * FROM node WHERE kind IN ('task','meetlog','habit') {_ORDER_BY_PRI_ID}"
        ).fetchall()
        orphans = [n for n in orphans if n["id"] not in claimed]
        if orphans:
            out("▸ " + _c("(unassigned)", "header") + "  " + _c(f"({len(orphans)})", "meta"))
            for n in orphans:
                out(_node_line(con, n))

    elif by == "direction":
        for direction in ("work", "personal"):
            rows = con.execute(
                "SELECT n.* FROM node n JOIN tag t ON n.id = t.node_id WHERE t.tag = ? "
                "AND n.kind IN ('task','meetlog','habit','project') "
                "ORDER BY n.priority NULLS LAST, n.id",
                (direction,),
            ).fetchall()
            out(_c(f"[{direction}]", "header") + " " + _c(f"({len(rows)})", "meta"))
            for n in rows:
                out(_node_line(con, n))

def _tree_children(con, node, include_canceled=False):
    """Children ordering: time-kinds ascending by title (date); others by priority -> id. CANCELED excluded by default."""
    sql = "SELECT * FROM node WHERE parent_id = ?"
    sql_params = [node["id"]]
    frag, p = _status_filter_sql(include_canceled=include_canceled)
    if frag:
        sql += " AND " + frag
        sql_params.extend(p)
    rows = list(con.execute(sql, sql_params))

    def key(r):
        if r["kind"] in _TIME_KINDS:
            return (0, r["title"], 0)
        pr = {"A": 0, "B": 1, "C": 2}.get(r["priority"], 3)
        return (1, pr, r["id"])

    return sorted(rows, key=key)

def _print_tree(con, node, depth, max_depth, *, include_canceled=False, log_tail=3, full=False):
    out(_node_line(con, node, indent="  " * depth, sched=True))
    if max_depth is not None and depth >= max_depth:
        return
    if node["kind"] == "day":  # day has no real children (empty); expand today's log activity instead
        _print_day_activity(con, node, depth, max_depth,
                            include_canceled=include_canceled, log_tail=log_tail, full=full)
        return
    for c in _tree_children(con, node, include_canceled=include_canceled):
        _print_tree(con, c, depth + 1, max_depth,
                    include_canceled=include_canceled, log_tail=log_tail, full=full)


_BUCKET_ORDER = ["work", "personal", "other"]
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def _print_day_activity(con, day_node, depth, max_depth, *, include_canceled=False, log_tail=3, full=False):
    """For a day node in the tree view, expand that day's activity: tasks with logs + that day's logs (today only, not others).
    log_tail: None = full expansion / 0 = no log expansion / N = latest N per task (default 3, middle elided to keep wl tree compact)."""
    from collections import OrderedDict

    target = day_node["title"][:10]
    cfrag, cparams = _status_filter_sql(include_canceled=include_canceled, col="node.status")
    cancel_sql = (" AND " + cfrag) if cfrag else ""
    rows = con.execute(
        rf"""SELECT log.node_id, log.body, node.title, node.status, node.priority, node.kind
            FROM log JOIN node ON log.node_id = node.id
            WHERE date(log.logged_at) = ?
              AND log.body NOT LIKE 'CLOCK\_%' ESCAPE '\'
              AND node.kind IN ('task', 'habit', 'meetlog')
              {cancel_sql}
            ORDER BY log.node_id""",
        [target] + cparams,
    ).fetchall()
    tasks = OrderedDict()
    for r in rows:
        tasks.setdefault(r["node_id"], {"r": r, "logs": []})["logs"].append(r["body"])
    ind = "  " * (depth + 1)
    for nid, t in tasks.items():
        n = t["r"]
        # habit done today = has a structured check-in metric that day (not "any log")
        if n["kind"] == "habit" and _has_checkin(con, nid, target):
            mk = _c("[x]", "done")
        else:
            mk = _c(_status_marker(n["status"]), _STATUS_STYLE.get(n["status"], "todo"))
        pri = (_c(f"[#{n['priority']}]", _PRI_STYLE.get(n["priority"])) + " ") if n["priority"] else ""
        out(ind + mk + " " + _c(f"#{nid}", "id") + " " + pri + _c(n["title"]))
        if log_tail != 0 and (max_depth is None or depth + 1 < max_depth):
            logs = t["logs"]
            shown = logs if log_tail is None else logs[-log_tail:]
            omitted = 0 if log_tail is None else max(0, len(logs) - log_tail)
            if omitted:
                out("  " * (depth + 2) + _c(f"· … ({omitted} earlier logs elided)", "meta"))
            for body in shown:
                indent = "  " * (depth + 2)
                shown_body = _truncate_log_body(body, indent_cols=len(indent) + 2, full=full)
                out(indent + _c("· " + shown_body, "meta"))

def _print_default_tree(con, *, include_canceled=False, log_tail=3, full=False):
    """Default wl tree: areas one level (area name only) + timeline expanded up to today (year -> quarter -> month -> week -> today + today's activity).
    To drill into an area's projects use --root <area>; for other days use --root <week/month>. CANCELED excluded by default."""
    from datetime import date

    life = con.execute("SELECT * FROM node WHERE kind = 'lifetime' ORDER BY id LIMIT 1").fetchone()
    has_day = con.execute("SELECT 1 FROM node WHERE kind = 'day' LIMIT 1").fetchone()
    has_month = con.execute("SELECT 1 FROM node WHERE kind = 'month' LIMIT 1").fetchone()
    if not life and not has_day and not has_month:
        print("(no root nodes)")
        return
    base = 0
    if life:
        out(_node_line(con, life))
        base = 1

    # timeline -> path to today (year -> quarter -> month -> week -> day) + today's activity; if no day node today, fall back to the latest month
    today = date.today().isoformat()
    dayn = con.execute(
        "SELECT * FROM node WHERE kind = 'day' AND title LIKE ? ORDER BY id LIMIT 1", (today + "%",)
    ).fetchone()
    if dayn:
        chain = [n for n in _ancestors_chain(con, dayn["id"]) if n["kind"] != "lifetime"]
        for d, n in enumerate(chain):
            out(_node_line(con, n, indent="  " * (base + d), sched=True))
        # today: only tasks, no log expansion (logs are for drill-down: wl day / wl tree --root <day> --depth big)
        day_depth = base + len(chain) - 1
        _print_day_activity(con, dayn, day_depth, max_depth=day_depth + 1, log_tail=log_tail, full=full)
    else:
        mon = con.execute("SELECT * FROM node WHERE kind = 'month' ORDER BY title DESC LIMIT 1").fetchone()
        if mon:
            out(_node_line(con, mon, indent="  " * base, sched=True))

    # areas one level only (no project expansion)
    if life:
        for a in _tree_children(con, life, include_canceled=include_canceled):
            if a["kind"] == "area":
                out(_node_line(con, a, indent="  " * base, sched=True))

def _render_day_group(con, items, by="plan", sched_ids=frozenset(), log_tail=None, full=False, day=None):
    """Render a day: items = {nid: {"node": row(title/status/priority), "logs": [body...]}}.
    Layout: bucket -> (plan/project/priority) -> task -> logs (indented). A task with no log but on a schedule is marked "planned·not-done".
    log_tail: None = full / 0 = no expansion / N = latest N per task.
    full: True keeps body untruncated (default truncates to one line by terminal width)."""
    from collections import OrderedDict

    buckets = OrderedDict()
    for nid, it in items.items():
        bucket = _node_bucket(con, nid)
        key, title = _sec_group(con, nid, it["node"], by, sched_ids)
        b = buckets.setdefault(bucket, OrderedDict())
        g = b.setdefault(key, {"title": title, "tasks": OrderedDict()})
        g["tasks"][nid] = it

    sortk = _sec_sort_key(by)
    for bucket in sorted(buckets, key=lambda x: _BUCKET_ORDER.index(x) if x in _BUCKET_ORDER else 99):
        out("  " + _c(bucket, "header"))
        groups = buckets[bucket].items()
        if sortk:
            groups = sorted(groups, key=lambda kv: sortk(kv[1]["title"]))
        for _, g in groups:
            out("    ▸ " + _c(g["title"], "kind"))
            for nid, it in g["tasks"].items():
                n = it["node"]
                logs = it["logs"]
                # habit done today = has a structured check-in metric that day (not "any log")
                if n["kind"] == "habit" and day and _has_checkin(con, nid, day):
                    mk = _c("[x]", "done")
                else:
                    mk = _c(_status_marker(n["status"]), _STATUS_STYLE.get(n["status"], "todo"))
                pri = (_c(f"[#{n['priority']}]", _PRI_STYLE.get(n["priority"])) + " ") if n["priority"] else ""
                hint = ""
                if not logs and n["status"] not in ("DONE", "CANCELED"):
                    # only "not-done" if the task is still open; a terminal-status task
                    # scheduled on a day with no logs is done, not pending (avoids the
                    # contradictory "[x] … «planned·not-done»").
                    hint = _c("  «planned·not-done»", "planned")
                elif logs and log_tail == 0:
                    # compact mode: don't expand body, attach a count hint after the title line
                    hint = _c(f"  ({len(logs)} log)", "meta")
                # total duration (CLOCK union log span); see _node_clock_min docstring
                dur = _fmt_dur(_node_clock_min(con, nid))
                dur_str = (" " + _c(dur, "clock")) if dur else ""
                out("      " + mk + " " + _c(f"#{nid}", "id") + " " + pri + _c(n["title"]) + dur_str + hint)
                if log_tail == 0:
                    continue
                bodies = logs if log_tail is None else logs[-log_tail:]
                if log_tail is not None and len(logs) > log_tail:
                    out("        " + _c(f"· … ({len(logs) - log_tail} earlier logs elided)", "meta"))
                # log body rendering: indent 8 + "· " 2 = 10 cols, remaining width-10-2 for one line
                for body in bodies:
                    shown = _truncate_log_body(body, indent_cols=10, full=full)
                    out("        " + _c("· " + shown, "meta"))

def _sec_sort_key(by):
    if by == "priority":
        return lambda lbl: _PRI_GROUP_ORDER.index(lbl) if lbl in _PRI_GROUP_ORDER else 99
    if by == "plan":
        return lambda lbl: _PLAN_ORDER.index(lbl) if lbl in _PLAN_ORDER else 99
    return None

def _sched_fires(on_date, rrule, target):
    """Whether this sched row fires on target (YYYY-MM-DD). Rules:
    - daily: every day
    - weekly:Mon,Wed,Fri | 1-7 | -1..-7: specific weekday(s) (number 1=Mon..7=Sun, -1=Sun..-7=Mon)
    - monthly:5 | 5,15,25 | -1: day of month; -N counts from month end (-1=last day)
    - quarterly:M-D | -1: M-th month in quarter (1-3), D-th day; -1 = quarter end (3/31, 6/30, 9/30, 12/31)
    - yearly:03-21 | -1: every year MM-DD; -1 = year end (12-31)
    """
    from datetime import date
    import calendar

    if on_date:
        return on_date == target
    if not rrule:
        return False
    rule = rrule.strip()
    if rule == "daily":
        return True
    y, m, d = (int(x) for x in target.split("-"))
    if rule.startswith("weekly:"):
        days = [x.strip() for x in rule[len("weekly:"):].split(",") if x.strip()]
        return _WEEKDAY_ABBR[date(y, m, d).weekday()] in days
    if rule.startswith("monthly:"):
        tokens = [x.strip() for x in rule[len("monthly:"):].split(",") if x.strip()]
        last = calendar.monthrange(y, m)[1]
        for tok in tokens:
            n = int(tok)
            target_day = n if n > 0 else last + n + 1   # -1 → last, -2 → last-1
            if 1 <= target_day <= last and target_day == d:
                return True
        return False
    if rule.startswith("quarterly:"):
        tokens = [x.strip() for x in rule[len("quarterly:"):].split(",") if x.strip()]
        quarter_month_idx = (m - 1) % 3 + 1   # month offset within the quarter: 1/2/3
        last = calendar.monthrange(y, m)[1]
        for tok in tokens:
            if tok == "-1":
                # quarter end: last day of the quarter's 3rd month (3/6/9/12)
                if quarter_month_idx == 3 and d == last:
                    return True
                continue
            mm, dd = (int(x) for x in tok.split("-"))
            if mm == quarter_month_idx and dd == d and 1 <= dd <= last:
                return True
        return False
    if rule.startswith("yearly:"):
        tokens = [x.strip() for x in rule[len("yearly:"):].split(",") if x.strip()]
        md = f"{m:02d}-{d:02d}"
        for tok in tokens:
            if tok == "-1" and md == "12-31":
                return True
            if tok == md:
                return True
        return False
    return False

def _scheduled_node_ids(con, target):
    """Set of node_ids hit by a schedule on target (forward planning -> planned bucket)."""
    ids = set()
    for r in con.execute("SELECT node_id, on_date, rrule FROM sched"):
        if _sched_fires(r["on_date"], r["rrule"], target):
            ids.add(r["node_id"])
    return ids

def _date_label(con, target):
    """Label (holiday/vacation/working-day-swap) for the date from date_meta, or None."""
    r = con.execute("SELECT label FROM date_meta WHERE date = ?", (target,)).fetchone()
    return r["label"] if r else None




_PLAN_ORDER = ["planned", "unplanned"]
_PRI_GROUP_ORDER = ["P0", "P1", "P2", "—"]
_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def _cn_weekday(date_str):
    """YYYY-MM-DD -> weekday name (computed, not stored)"""
    from datetime import date

    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        return _WEEKDAY_NAMES[date(y, m, d).weekday()]
    except (ValueError, IndexError):
        return ""

