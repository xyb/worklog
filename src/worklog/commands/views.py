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
from .. import timeutil as _tu
from .. import db_table as _db
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
    make_node_filter,
    nodes_with_tag,
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
from .metric import _fmt_value
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
    # explicit --status overrides the default CANCELED hide so the filtered tree can
    # recurse into the matching terminal-status nodes (no-filter path: status is unset).
    inc_cancel = getattr(args, "show_canceled", False) or bool(getattr(args, "status", None))
    log_tail = _resolve_log_tail(args, _is_brief(args, "no_logs"), default_tail=3)
    nf = make_node_filter(con, args)  # shared --tag/--kind/--status filter
    if args.by:
        _tree_by(con, args.by, nf=nf)
        return
    full = _log_full(args)
    # a filter prunes the structural tree to matching nodes + their ancestor paths
    # (separate code path; the bare/unfiltered tree below stays byte-identical).
    if nf is not None:
        root_node = None
        if args.root is not None:
            root_node = _db.get(con, "node", args.root)
            if not root_node:
                sys.exit(f"✗ node #{args.root} not found")
        _print_filtered_tree(con, nf, root_node=root_node,
                             include_canceled=inc_cancel, log_tail=log_tail, full=full)
        return
    if args.root is None and args.depth is None:
        # bare wl tree: areas one level + timeline up to today
        _print_default_tree(con, include_canceled=inc_cancel, log_tail=log_tail, full=full)
        return
    if args.root is not None:
        # expand subtree from a specified node as root (no longer requires parent_id IS NULL)
        root = _db.get(con, "node", args.root)
        if not root:
            sys.exit(f"✗ node #{args.root} not found")
        roots = [root]
    else:
        root_sql = "SELECT * FROM node WHERE parent_id IS NULL"
        params_root = []
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
        target = _tu.today()
    day = _db.query_one(con, "node", kind="day", title__like=target + "%", order="id")
    # date context: date + weekday + the day's nature (workday/weekend/holiday/leave,
    # refined by any date_meta label) so the header conveys "what kind of day" at a glance
    wd = _cn_weekday(target)
    nature = _day_nature(con, target)
    head = target + (f" {wd}" if wd else "") + (f" · {nature}" if nature else "")
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
            when = _c(f" (written at {_tu.utc_to_local(at)[5:16]})", "meta") if at else ""
            out(_c("  > Recap: " + s["body"], "meta") + when)
            # stale check: count plain-note logs (tag IS NULL) added after the recap;
            # meta logs (goal/summary/…) and metric carriers (type='metric') don't count.
            if at:
                newer = con.execute(
                    f"SELECT COUNT(*) FROM log WHERE logged_at > ? "
                    f"AND {_tu.local_day_sql('logged_at')} = ? AND tag IS NULL",
                    (at, target),
                ).fetchone()[0]
                if newer:
                    out(_c(f"  > ⚠ {newer} change(s) after recap; consider rewriting via wl recap", "doing"))
        t5 = _latest_typed_log(con, day["id"], "top5")
        if t5 and t5["body"]:
            out(_c("  > Top5: " + t5["body"], "meta"))
        wk = _db.query_one(con, "node", cols="id", id=day["parent_id"], kind="week")
        if wk:
            ov = _latest_typed_log(con, wk["id"], "overview")
            if ov and ov["body"]:
                out(_c("  > This week: " + ov["body"], "meta"))

    # an explicit --status filter (applied below via make_node_filter) must override the
    # default CANCELED hide, else `day --status CANCELED` would drop its own matches.
    inc_cancel = getattr(args, "show_canceled", False) or bool(getattr(args, "status", None))
    cfrag, cparams = _status_filter_sql(include_canceled=inc_cancel, col="node.status")
    cancel_sql = (" AND " + cfrag) if cfrag else ""
    rows = con.execute(
        rf"""SELECT log.node_id, log.logged_at, log.body,
                   node.title, node.status, node.priority, node.kind
            FROM log JOIN node ON log.node_id = node.id
            WHERE {_tu.local_day_sql('log.logged_at')} = ?
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

    # shared --tag/--kind/--status filter: keep only matching nodes. Empty buckets /
    # groups then simply don't get rendered (_render_day_group builds them from items).
    nf = make_node_filter(con, args)
    if nf:
        items = {nid: it for nid, it in items.items() if nf(nid)}
        if not items:
            out(_c(f"  (nothing matches the filter on {target})", "meta"))
            return

    if not items:
        # clock-only day: time was tracked (wl spent / start-stop) but nothing logged/planned
        clock_sec = con.execute(
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE {_tu.local_day_sql('end_at')} = ?",
            (target,),
        ).fetchone()["s"]
        if clock_sec:
            cm = int(clock_sec / 60)
            out(_c(f"  (no logged task progress for {target}) · CLOCK {cm}min ({cm // 60}h{cm % 60}m)", "meta"))
        else:
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

    # tasks "with progress" = items that have logs today; derive from the (possibly
    # filtered) items so the summary line matches what was actually rendered.
    logged = {nid: (it["node"]["status"] or "TODO") for nid, it in items.items() if it["logs"]}
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
    # CLOCK total: unfiltered = all clock for the day; filtered = only the shown items'
    # clock, so `day -t work` doesn't report personal tasks' time on a work-only view.
    if nf:
        ids = list(items)
        if ids:
            qm = ",".join("?" * len(ids))
            total_sec = con.execute(
                f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock "
                f"WHERE {_tu.local_day_sql('end_at')} = ? AND node_id IN ({qm})",
                [target] + ids,
            ).fetchone()["s"]
        else:
            total_sec = 0
    else:
        total_sec = con.execute(
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE {_tu.local_day_sql('end_at')} = ?",
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

def _tree_by(con, by, nf=None):
    """Flat 2-level view, regrouped by dimension (avoids deep time-layered nesting).
    `nf` (from make_node_filter) further restricts the listed nodes; empty groups are
    skipped when a filter is active."""
    if by == "tag":
        tags = [r["tag"] for r in _db.query(con, "tag", cols="DISTINCT tag", order="tag")]
        sem = [t for t in tags if t not in GENERIC_TAGS]
        if not sem:
            print("(no semantic tags)")
            return
        for tag in sem:
            rows = nodes_with_tag(con, tag, order="priority NULLS LAST, id")
            if nf:
                rows = [n for n in rows if nf(n["id"])]
                if not rows:
                    continue
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
            proj_tags = {r["tag"] for r in _db.query(con, "tag", cols="tag", node_id=proj["id"])} - GENERIC_TAGS
            ids = set()
            # (a) structural children
            for r in _db.query(con, "node", cols="id", parent_id=proj["id"]):
                ids.add(r["id"])
            # (b) task/meetlog/habit sharing a semantic tag
            if proj_tags:
                for r in nodes_with_tag(con, proj_tags, kinds=("task", "meetlog", "habit"), cols="id"):
                    ids.add(r["id"])
            if nf:
                ids = {i for i in ids if nf(i)}
                # keep the project if it has matching members OR the project node itself
                # matches (e.g. --kind project) — else `--by project --kind project` would
                # drop every project because no member is itself a project.
                if not ids and not nf(proj["id"]):
                    continue
            pri = (" " + _c(f"[#{proj['priority']}]", _PRI_STYLE.get(proj["priority"]))) if proj["priority"] else ""
            out("▸ " + _c(f"#{proj['id']}", "id") + pri + " " + _c(proj["title"], "header") + "  " + _c(f"({len(ids)})", "meta"))
            for nid in sorted(ids):
                n = _db.get(con, "node", nid)
                claimed.add(nid)
                out(_node_line(con, n))
            if not ids:
                out("    " + _c("(no linked tasks)", "meta"))
        # orphans: task/meetlog/habit not attached to any project
        orphans = con.execute(
            f"SELECT * FROM node WHERE kind IN ('task','meetlog','habit') {_ORDER_BY_PRI_ID}"
        ).fetchall()
        orphans = [n for n in orphans if n["id"] not in claimed]
        if nf:
            orphans = [n for n in orphans if nf(n["id"])]
        if orphans:
            out("▸ " + _c("(unassigned)", "header") + "  " + _c(f"({len(orphans)})", "meta"))
            for n in orphans:
                out(_node_line(con, n))

    elif by == "direction":
        for direction in ("work", "personal"):
            rows = nodes_with_tag(con, direction, kinds=("task", "meetlog", "habit", "project"),
                                  order="priority NULLS LAST, id")
            if nf:
                rows = [n for n in rows if nf(n["id"])]
                if not rows:
                    continue
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


def _print_filtered_tree(con, nf, *, root_node=None, include_canceled=False, log_tail=3, full=False):
    """Render the structural tree pruned to nodes matching `nf` plus the ancestor paths
    that lead to them (so matches keep their context instead of being orphaned). Used by
    `wl tree` whenever a --tag/--kind/--status filter is active. `root_node` restricts the
    search to that subtree. Note: this is the structural (area→project→task) tree — the
    log-derived day-activity expansion isn't shown here; use `wl day --tag …` for that."""
    if root_node is not None:
        universe = [root_node["id"]] + _collect_descendants(con, root_node["id"])
        subtree = set(universe)
    else:
        universe = [r["id"] for r in con.execute("SELECT id FROM node")]
        subtree = None
    keep = set()
    for nid in universe:
        if nf(nid):
            for a in _ancestors_chain(con, nid):  # node itself + chain up to the top
                keep.add(a["id"])
    if subtree is not None:
        keep &= subtree  # drop ancestors above the requested root
    if not keep:
        where = "" if root_node is None else f" under #{root_node['id']}"
        print(f"(nothing{where} matches the filter)")
        return
    if root_node is not None:
        roots = [root_node]
    else:
        roots = [r for r in con.execute("SELECT * FROM node WHERE parent_id IS NULL")
                 if r["id"] in keep]
    for root in roots:
        _print_kept_subtree(con, root, 0, keep, include_canceled=include_canceled,
                            log_tail=log_tail, full=full)


def _print_kept_subtree(con, node, depth, keep, *, include_canceled=False, log_tail=3, full=False):
    """Print `node` and recurse only into children in the `keep` set (the filtered-tree
    companion to _print_tree; no depth cap — `keep` is already the pruned node set)."""
    out(_node_line(con, node, indent="  " * depth, sched=True))
    for c in _tree_children(con, node, include_canceled=include_canceled):
        if c["id"] in keep:
            _print_kept_subtree(con, c, depth + 1, keep,
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
            WHERE {_tu.local_day_sql('log.logged_at')} = ?
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
        mh = ""
        if n["kind"] == "habit":
            prog = _habit_month_progress(con, nid, target)
            if prog:
                mh = _c(f"  (this month {prog[0]}/{prog[1]})", "meta")
        out(ind + mk + " " + _c(f"#{nid}", "id") + " " + pri + _c(n["title"]) + mh)
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
            # fold that day's datapoints (skip checkin marker — reflected by [x]); elide >5
            indent = "  " * (depth + 2)
            mrows = [m for m in con.execute(
                "SELECT tag, value_num, value_text, unit FROM metric WHERE node_id = ? "
                f"AND {_tu.local_day_sql('at')} = ? ORDER BY id", (nid, target)) if m["tag"] != "checkin"]
            for m in mrows[:5]:
                out(indent + _c(f"↳ [{m['tag']}] {_fmt_value(m)}".rstrip(), "meta"))
            if len(mrows) > 5:
                out(indent + _c(f"↳ … {len(mrows) - 5} more datapoints", "meta"))

def _print_default_tree(con, *, include_canceled=False, log_tail=3, full=False):
    """Default wl tree: areas one level (area name only) + timeline expanded up to today (year -> quarter -> month -> week -> today + today's activity).
    To drill into an area's projects use --root <area>; for other days use --root <week/month>. CANCELED excluded by default."""
    from datetime import date

    life = _db.query_one(con, "node", kind="lifetime", order="id")
    has_day = _db.exists(con, "node", kind="day")
    has_month = _db.exists(con, "node", kind="month")
    if not life and not has_day and not has_month:
        print("(no root nodes)")
        return
    base = 0
    if life:
        out(_node_line(con, life))
        base = 1

    # timeline -> path to today (year -> quarter -> month -> week -> day) + today's activity; if no day node today, fall back to the latest month
    today = _tu.today()
    dayn = _db.query_one(con, "node", kind="day", title__like=today + "%", order="id")
    if dayn:
        chain = [n for n in _ancestors_chain(con, dayn["id"]) if n["kind"] != "lifetime"]
        for d, n in enumerate(chain):
            out(_node_line(con, n, indent="  " * (base + d), sched=True))
        # today: only tasks, no log expansion (logs are for drill-down: wl day / wl tree --root <day> --depth big)
        day_depth = base + len(chain) - 1
        _print_day_activity(con, dayn, day_depth, max_depth=day_depth + 1, log_tail=log_tail, full=full)
    else:
        mon = _db.query_one(con, "node", kind="month", order="title DESC")
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
                # THIS DAY's duration (clock intervals + plain-note span scoped to the day),
                # not the node's all-time total; see _node_clock_min docstring
                dur = _fmt_dur(_node_clock_min(con, nid, day=day))
                dur_str = (" " + _c(dur, "clock")) if dur else ""
                # habit month-to-date completion rate (this month N/M); skip if no schedule
                mh = ""
                if n["kind"] == "habit" and day:
                    prog = _habit_month_progress(con, nid, day)
                    if prog:
                        mh = _c(f"  (this month {prog[0]}/{prog[1]})", "meta")
                out("      " + mk + " " + _c(f"#{nid}", "id") + " " + pri + _c(n["title"]) + dur_str + hint + mh)
                if log_tail == 0:
                    continue
                bodies = logs if log_tail is None else logs[-log_tail:]
                if log_tail is not None and len(logs) > log_tail:
                    out("        " + _c(f"· … ({len(logs) - log_tail} earlier logs elided)", "meta"))
                # log body rendering: indent 8 + "· " 2 = 10 cols, remaining width-10-2 for one line
                for body in bodies:
                    shown = _truncate_log_body(body, indent_cols=10, full=full)
                    out("        " + _c("· " + shown, "meta"))
                # fold this node's datapoints that day beneath it (skip the checkin marker —
                # it's already reflected by the [x]); over-count elided
                if day:
                    mrows = [m for m in con.execute(
                        f"SELECT tag, value_num, value_text, unit FROM metric WHERE node_id = ? "
                        f"AND {_tu.local_day_sql('at')} = ? ORDER BY id", (nid, day)) if m["tag"] != "checkin"]
                    for m in mrows[:5]:
                        out("        " + _c(f"↳ [{m['tag']}] {_fmt_value(m)}".rstrip(), "meta"))
                    if len(mrows) > 5:
                        out("        " + _c(f"↳ … {len(mrows) - 5} more datapoints", "meta"))

def _sec_sort_key(by):
    if by == "priority":
        return lambda lbl: _PRI_GROUP_ORDER.index(lbl) if lbl in _PRI_GROUP_ORDER else 99
    if by == "plan":
        return lambda lbl: _PLAN_ORDER.index(lbl) if lbl in _PLAN_ORDER else 99
    return None

def _habit_month_progress(con, nid, day):
    """For a habit, (done, expected) this month up to `day` (YYYY-MM-DD): done =
    distinct days this month ≤ day with a checkin metric; expected = days this month
    ≤ day on which the habit's schedule fires. Returns None when the habit has no
    schedule (no meaningful rate)."""
    from datetime import date, timedelta
    scheds = _db.query(con, "sched", cols="on_date, rrule", node_id=nid)
    if not scheds:
        return None
    y, m, d = (int(x) for x in day.split("-"))
    month = day[:7]
    done = con.execute(
        f"SELECT COUNT(DISTINCT {_tu.local_day_sql('at')}) FROM metric WHERE node_id = ? AND tag = 'checkin' "
        f"AND {_tu.local_month_sql('at')} = ? AND {_tu.local_day_sql('at')} <= ?", (nid, month, day),
    ).fetchone()[0]
    expected = 0
    cur, end = date(y, m, 1), date(y, m, d)
    while cur <= end:
        ds = cur.isoformat()
        if any(_sched_fires(s["on_date"], s["rrule"], ds) for s in scheds):
            expected += 1
        cur += timedelta(days=1)
    return done, expected


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
    for r in _db.query(con, "sched", cols="node_id, on_date, rrule"):
        if _sched_fires(r["on_date"], r["rrule"], target):
            ids.add(r["node_id"])
    return ids

def _date_label(con, target):
    """Label (holiday/vacation/working-day-swap) for the date from date_meta, or None."""
    r = _db.query_one(con, "date_meta", cols="label", date=target)
    return r["label"] if r else None


# date_meta labels are free text; these hints classify one into a work/rest word so the
# wl day header reads at a glance. Matched case-insensitively (Chinese is unaffected by
# lower()). Hints are deliberately specific multi-char terms — a loose hint like "off"
# wrongly fired on "office"/"kickoff", and a bare "休" fired inside "调休" (a makeup
# workday). WORK is checked before OFF so an explicit working-day signal wins when both
# appear (e.g. "swap to workday for the holiday" -> workday, "调休上班" -> workday).
# note: no bare "swap" — it fired on "swap meet" / "swap space"; a real swapped workday
# already matches "makeup"/"workday"/"调休"/"补班".
_WORK_HINTS = ("makeup", "working", "workday", "调休", "补班", "上班", "值班")
_OFF_HINTS = ("holiday", "vacation", "leave", "day off", "假期", "节假日", "放假", "休假", "请假", "年假")
_LEAVE_HINTS = ("leave", "vacation", "休假", "请假", "年假")


def _day_nature(con, target):
    """A short 'what kind of day is this' note for the wl day header, so every day reads
    at a glance — workday / weekend by default, refined to holiday / leave / workday when
    a date_meta label (set via `wl dateinfo`) says so. The baseline comes from the weekday;
    an explicit label overrides the work/rest word and is appended for context (e.g.
    `workday (Grain Buds solar term)`, `holiday (Labor Day)`). Returns None on a bad date."""
    from datetime import date
    try:
        y, m, d = (int(x) for x in target.split("-"))
        wd = date(y, m, d).weekday()  # 0=Mon .. 6=Sun
    except (ValueError, IndexError):
        return None
    base = "workday" if wd < 5 else "weekend"
    label = _date_label(con, target)
    if not label:
        return base
    low = label.lower()
    if any(h in low for h in _WORK_HINTS):
        status = "workday"  # explicit working-day signal wins over a co-occurring holiday word
    elif any(h in low for h in _OFF_HINTS):
        status = "leave" if any(h in low for h in _LEAVE_HINTS) else "holiday"
    else:
        status = base  # a neutral annotation (e.g. a solar term): keep the weekday baseline
    # append the label unless the status word is already in it (avoid "holiday (… holiday)")
    return f"{status} ({label})" if status not in low else label




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

