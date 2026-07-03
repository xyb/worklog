"""worklog commands: views group."""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .. import render
from .. import timeutil as _tu
from .. import db_table as _db
from ..models import DateMeta, Node
from ..node_schema import node_view as _node_view, type_facet as _type_facet, CORE as _CORE
from ..helpers import _ORDER_BY_PRI_ID, _TIME_LEVELS  # noqa: F401
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
    _wrap_display,
    _display_width,
    GENERIC_TAGS,
)
from ..queries import (
    _check_ids_exist,
    _has_tag,
    node_type,
    node_props,
    nodes_with_type,
    time_node_by_period,
    node_has_type,
    workitem_ids,
    filter_workitems,
    make_node_filter,
    nodes_with_tag,
    _has_checkin,
    _latest_typed_log,
    _log_goals,
    _insert_log,
    _node_clock_min,
    _node_exists,
    _node_tags,
    _status_filter_sql,
    _upsert_prop,
)
from ..graph import (
    _ancestors_chain,
    _collect_descendants,
    _node_bucket,
    _sec_group,
)
from .metric import metric_rows
from .output import output_format, TextRenderable, text_renderer
from ..render import (
    _PRI_STYLE,
    _STATUS_STYLE,
    _RICH_AVAIL,
    _resolve_theme,
    THEMES,
    _c,
    die,
    _hl,
    _group_header,
    _log_body_row,
    _hang_wrap,
    _node_line,
    _node_activity_prefix,
    _print_truncation_hint,
    _snippet,
    out,
)
from ..xdg import _resolve_db_path, _resolve_aliases_path, _xdg_data_home, _xdg_config_home

# Lazy access to cli module (for DB wrappers + module state).
# Used at function call time (not at import) to avoid the cli ↔ commands
# import cycle.
from .. import cli as _cli  # noqa: E402


def _tree_json_data(con, args):
    """`wl tree -o json`: the structural node subtree as nested `{...node, children:[...]}`.
    `--root` → that subtree (default depth 3); else the top-level forest (default depth 2).
    Honors `--depth` and `--show-canceled`; ignores the text-view `--by` / tag/status filters
    (for filtered flat data use `wl ls -o json`)."""
    inc_cancel = getattr(args, "show_canceled", False)
    if args.root is not None:
        root = Node.get(con, args.root)
        if not root:
            die(f"node #{args.root} not found")
        roots = [root]
        max_depth = args.depth if args.depth is not None else 3
    else:
        roots = list(Node.query(con, parent_id=None, order="priority NULLS LAST, id"))
        max_depth = args.depth if args.depth is not None else 2
    if not inc_cancel:
        roots = [r for r in roots if r["status"] != "CANCELED"]

    def node_json(n, depth):
        d = _node_view(con, n, _CORE).to_dict(_CORE)   # id, title, status, priority, type (NodeView contract)
        if depth < max_depth:
            kids = Node.query(con, parent_id=n["id"], order="priority NULLS LAST, id")
            kids = [c for c in kids if inc_cancel or c["status"] != "CANCELED"]
            d["children"] = [node_json(c, depth + 1) for c in kids]
        return d

    return [node_json(r, 0) for r in roots]


@text_renderer("tree_empty")
def _render_tree_empty(result):
    """Empty-root path: no top-level nodes matched (after filtering). The JSON data is
    already the empty list []; text just shows a hint."""
    out(_c('(empty — add a task with `wl add "..."`)', "meta"))


@output_format
def cmd_tree(args, con):
    # explicit --status overrides the default CANCELED hide so the filtered tree can
    # recurse into the matching terminal-status nodes (no-filter path: status is unset).
    inc_cancel = getattr(args, "show_canceled", False) or bool(getattr(args, "status", None))
    log_tail = _resolve_log_tail(args, _is_brief(args, "no_logs"), default_tail=3)
    nf = make_node_filter(con, args)  # shared --tag/--para/--status filter
    full = _log_full(args)

    if args.by:
        _by = args.by
        return TextRenderable(_tree_json_data(con, args),
                              lambda: _tree_by(con, _by, nf=nf))

    # a filter prunes the structural tree to matching nodes + their ancestor paths
    if nf is not None:
        root_node = None
        if args.root is not None:
            root_node = Node.get(con, args.root)
            if not root_node:
                die(f"node #{args.root} not found")
        return TextRenderable(
            _tree_json_data(con, args),
            lambda: _print_filtered_tree(con, nf, root_node=root_node,
                                         include_canceled=inc_cancel, log_tail=log_tail, full=full))

    if args.root is None and args.depth is None:
        return TextRenderable(
            _tree_json_data(con, args),
            lambda: _print_default_tree(con, include_canceled=inc_cancel, log_tail=log_tail, full=full))

    if args.root is not None:
        # expand subtree from a specified node as root (no longer requires parent_id IS NULL)
        root = Node.get(con, args.root)
        if not root:
            die(f"node #{args.root} not found")
        roots = [root]
    else:
        root_sql = f"SELECT * FROM node WHERE parent_id IS NULL AND {_db.ALIVE}"
        params_root = []
        if not inc_cancel:
            frag, p = _status_filter_sql(include_canceled=False)
            if frag:
                root_sql += " AND " + frag
                params_root.extend(p)
        roots = [Node.from_row(r) for r in con.execute(root_sql, params_root)]

    if not roots:
        return TextRenderable(_tree_json_data(con, args), cmd_name="tree_empty")

    # default depth limit to avoid flooding
    max_depth = args.depth if args.depth is not None else (3 if args.root is not None else 2)

    def _render():
        for root in roots:
            _print_tree(con, root, depth=0, max_depth=max_depth,
                        include_canceled=inc_cancel, log_tail=log_tail, full=full)

    return TextRenderable(_tree_json_data(con, args), _render)

# wl day header reserved-tag logs, each with a distinct marker: one glance tells today's goal
# from recap from the week's goal from the month's goal. Week/month goals are the same `goal`
# tag on the ancestor week/month node — the level is the node's type.
_DAY_MARKERS = {
    "goal":    "🎯 ",
    "summary": "📝 Recap: ",
    "week":    "📅 This week: ",
    "month":   "⭐ This month: ",
}


def _header_blockquote(body, marker, indent="  "):
    """Render a day-header reserved-tag log: only the FIRST line carries the `<indent>> ` prefix; every
    continuation (soft-wrap or embedded newline) is indented with plain spaces to align under the
    text after `> ` — cleaner than repeating `> ` on every line. The marker (🎯 / Recap: / …) rides
    on the first line. Soft-wraps by display width (CJK-aware)."""
    qp = f"{indent}> "
    cont = " " * _display_width(qp)   # continuation lines align under the `> ` content column
    avail = max(8, _term_width() - _display_width(qp))
    paras = (marker + (body or "")).split("\n")
    lines = []
    first = True
    for para in paras:
        for chunk in _wrap_display(para, avail):
            lines.append((qp if first else cont) + chunk)
            first = False
    return "\n".join(lines)


def _goal_target_rows(con, node_id):
    """The resolved structured target nodes of a node's current goal — [{id,title,status}] in
    priority order (the `goal` metrics on its latest goal log). [] if the goal has no targets.
    The single source for showing a goal's targets across `wl day` / `wl goal` / `-o json`."""
    rows = []
    for n in Node.gets(con, _log_goals(con, node_id)):
        if n:
            rows.append({"id": n["id"], "title": n["title"], "status": n["status"] or "TODO"})
    return rows


def _goal_counts(con, node_id, body):
    """(done, total) for a goal's targets — the structured `goal` metrics if present, else the
    `#id`/`WL#id` refs named in the prose (legacy fallback). done = settled (DONE/CANCELED)."""
    rows = _goal_target_rows(con, node_id)
    if rows:
        done = sum(1 for r in rows if r["status"] in ("DONE", "CANCELED"))
        return done, len(rows)
    import re
    seen, total, done = set(), 0, 0
    for m in re.findall(r"(?:WL)?#(\d+)", body or ""):
        nid = int(m)
        if nid in seen:
            continue
        seen.add(nid)
        n = Node.get(con, nid)
        if not n:
            continue
        total += 1
        if (n["status"] or "TODO") in ("DONE", "CANCELED"):
            done += 1
    return done, total


def _goal_progress(con, node_id, body):
    """Goal achievement as ` [done/total] <emoji>` from its targets (structured metrics, else
    prose #ids). "" if no targets. ✅ all done · 🟡 partial · ⬜ none."""
    done, total = _goal_counts(con, node_id, body)
    if not total:
        return ""
    emoji = "✅" if done == total else ("⬜" if done == 0 else "🟡")
    return f" [{done}/{total}] {emoji}"


def _emit_goal_targets(con, node_id, indent="     "):
    """Print a goal's structured targets as a numbered, status-marked list (priority order):
    `<indent>1. [x] #630 title`. No-op if the goal has no structured targets. Shared by
    `wl day` and `wl goal` so the two render identically."""
    rows = _goal_target_rows(con, node_id)
    for n, r in enumerate(rows, 1):
        mk_plain = _status_marker(r["status"])
        # budget the title against the ACTUAL plain prefix width (indent + "N. " + marker + #id),
        # so the truncated line fits the terminal on ONE line (no soft-wrap to a flush-left tail)
        prefix_plain = f"{indent}{n}. {mk_plain} #{r['id']} "
        title = _truncate_log_body(r["title"], indent_cols=_display_width(prefix_plain), full=False)
        mk = _c(mk_plain, _STATUS_STYLE.get(r["status"], "todo"))
        out(f"{indent}{n}. " + mk + " " + _c(f"#{r['id']}", "id") + " " + title)


def _month_node_for_date(con, target):
    """The month time-node that calendar-owns ``target`` (``YYYY-MM-DD``), resolved by
    ``date.period`` — NOT by walking the day's week ancestry. A cross-month week (e.g. an ISO
    week hung under June) would otherwise surface June's Top5 for its July spill-over days."""
    return time_node_by_period(con, "month", target[:7])


_PERIOD_GOAL_MARKERS = {"week": "📅 ", "month": "⭐ ", "quarter": "🗓 ", "year": "🏆 "}


def _emit_goal_block(con, node, marker):
    """Render one goal-header row for a node: its latest goal (blockquoted with `marker`) + the
    `[done/total]` achievement + its structured target nodes. No-op when the node has no goal.
    The single source for every goal row — the day header's day/week/month rows and the summary
    period header all render through here, so a format tweak lands in one place."""
    g = _latest_typed_log(con, node["id"], "goal")
    if not (g and g.body):
        return
    out(_c(_header_blockquote(g.body, marker), "meta")
        + _c(_goal_progress(con, node["id"], g.body), "meta"))
    _emit_goal_targets(con, node["id"])


def _emit_time_goal_header(con, level, period):
    """The goal-dashboard header for a time node (its own goal + [done/total] + structured target
    nodes) — the `wl day` header generalized to week/month/quarter/year. No-op when the node or its
    goal is absent. Used by `wl summary --week/--month/--quarter/--year`."""
    node = time_node_by_period(con, level, period)
    if node:
        _emit_goal_block(con, node, _PERIOD_GOAL_MARKERS.get(level, "🎯 "))


def _day_goals_dict(con, target, day):
    """The day-header meta as a dict (for `wl day -o json`): goal (+ goal_progress {done,total}),
    summary (+ summary_at), week_goal, month_goal. Only present keys are included."""
    if not day:
        return {}
    d = {}
    g = _latest_typed_log(con, day["id"], "goal")
    if g and g.body:
        d["goal"] = g.body
        targets = _goal_target_rows(con, day["id"])
        if targets:
            d["goal_targets"] = targets          # [{id,title,status}], priority order
        dn, tt = _goal_counts(con, day["id"], g.body)
        if tt:
            d["goal_progress"] = {"done": dn, "total": tt}
    s = _latest_typed_log(con, day["id"], "summary")
    if s and s.body:
        d["summary"] = s.body
        d["summary_at"] = s.logged_at   # UTC instant
    wk = Node.get(con, day["parent_id"]) if day["parent_id"] else None
    if wk and node_type(con, wk) == "week":
        wg = _latest_typed_log(con, wk["id"], "goal")
        if wg and wg.body:
            d["week_goal"] = wg.body
    mo = _month_node_for_date(con, target)
    if mo:
        mg = _latest_typed_log(con, mo["id"], "goal")
        if mg and mg.body:
            d["month_goal"] = mg.body
    return d


def _day_json_data(con, target, day, items, sched_ids):
    """`wl day -o json`: the day's meta + the tasks active that day (each with its logs that day,
    planned flag, clock minutes) + the day's total clock. Machine view of `wl day`."""
    tasks = []
    for nid, it in items.items():
        n = it["node"]
        tasks.append({
            # day's node row is partial (id is the dict key), so build the facet from nid directly
            # rather than via node_view; the orthogonal type facet.
            "id": nid, "title": n["title"], "status": n["status"],
            "priority": n["priority"], "type": _type_facet(node_props(con, nid)),
            "planned": nid in sched_ids,
            "logs": list(it["logs"]),
            "clock_min": _node_clock_min(con, nid, target),
        })
    clock_sec = con.execute(
        f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock "
        f"WHERE {_tu.local_day_sql('end_at')} = ? AND {_db.ALIVE}", (target,)).fetchone()["s"]
    return {
        "date": target,
        "weekday": _cn_weekday(target),
        "nature": _day_nature(con, target),
        "node_id": day["id"] if day else None,
        # goal / goal_progress / summary / summary_at / week_goal / month_goal — flattened in
        # (only the present keys), no wrapper: these ARE the day node's reserved-tag logs.
        **_day_goals_dict(con, target, day),
        "tasks": tasks,
        "clock_min_total": int(clock_sec / 60),
    }


def _emit_day_header(con, day, target, capped=None):
    """Render the text-mode day header: `#<id> <date> <weekday> · <nature>`, then the day's goal +
    recap(summary, with a staleness warning) + the ancestor week's & month's goals — each the latest
    typed log, with its [done/total] progress and structured target nodes. No-op body when there's
    no day node (just the bare date line). `capped`: node ids whose forgotten clock was auto-closed
    this run — surfaced as a leading warning."""
    if capped:
        from .state import _capped_clock_warning
        for nid in capped:
            out(_c(_capped_clock_warning(nid), "later"))
    wd = _cn_weekday(target)
    nature = _day_nature(con, target)
    head = target + (f" {wd}" if wd else "") + (f" · {nature}" if nature else "")
    # lead with the day node id so `wl goal`/`wl show <id>` have it to hand
    nid = (_c(f"#{day['id']}", "id") + " ") if day else ""
    out(nid + _c(head, "header"))
    if not day:
        return
    # goal line carries its [done/total] achievement, then its structured target nodes
    _emit_goal_block(con, day, _DAY_MARKERS["goal"])
    s = _latest_typed_log(con, day["id"], "summary")
    if s and s.body:
        at = s.logged_at
        when = _c(f" (written at {_tu.utc_to_local(at)[5:16]})", "meta") if at else ""
        out(_c(_header_blockquote(s.body, _DAY_MARKERS["summary"]), "meta") + when)
        # stale check: count plain-note logs (tag IS NULL) added after the recap
        if at:
            newer = con.execute(
                f"SELECT COUNT(*) FROM log WHERE logged_at > ? "
                f"AND {_tu.local_day_sql('logged_at')} = ? AND tag IS NULL AND {_db.ALIVE}",
                (at, target),
            ).fetchone()[0]
            if newer:
                out(_c(f"  > ⚠ {newer} change(s) after recap; consider rewriting via wl recap", "doing"))
    # the week's goal — same `goal` tag on the ancestor week node
    wk = Node.get(con, day["parent_id"]) if day["parent_id"] else None
    if wk and node_type(con, wk) == "week":
        _emit_goal_block(con, wk, _DAY_MARKERS["week"])
    # the month's goal — resolved by the day's own date period, not the week's ancestry (a
    # cross-month week must not surface the previous month's goal for its spill-over days)
    mo = _month_node_for_date(con, target)
    if mo:
        _emit_goal_block(con, mo, _DAY_MARKERS["month"])


def _day_workitem_log_rows(con, target, inc_cancel, *, extra_cols="", order="log.node_id"):
    """The day's log rows whose node is a work item: `log JOIN node` for `target`'s local day (the
    time-window JOIN stays SQL), then a batched work-item filter (workitem_ids — one type-prop read
    over the day's logged nodes). Shared by _collect_day_items / _print_day_activity. `extra_cols`
    appends log columns (e.g. `log.logged_at`); `order` is the SQL ORDER BY."""
    cfrag, cparams = _status_filter_sql(include_canceled=inc_cancel, col="node.status")
    cancel_sql = (" AND " + cfrag) if cfrag else ""
    sel = "log.node_id, log.body, node.title, node.status, node.priority" + (", " + extra_cols if extra_cols else "")
    rows = con.execute(
        rf"""SELECT {sel}
            FROM log JOIN node ON log.node_id = node.id
            WHERE {_tu.local_day_sql('log.logged_at')} = ?
              AND log.{_db.ALIVE} AND node.{_db.ALIVE}
              {cancel_sql}
            ORDER BY {order}""",
        [target] + cparams,
    ).fetchall()
    wi = workitem_ids(con, {r["node_id"] for r in rows})
    return [r for r in rows if r["node_id"] in wi]


def _collect_day_items(con, target, inc_cancel):
    """The day's work items: every task/habit/meetlog with a log on that local day, plus any
    scheduled for that day with no log yet (planned-ahead, visible before being worked). Returns
    (items, sched_ids), items mapping node_id -> {node, logs:[...]}. CANCELED nodes are dropped
    unless inc_cancel."""
    rows = _day_workitem_log_rows(con, target, inc_cancel,
                                  extra_cols="log.logged_at", order="log.logged_at, log.node_id")
    items = {}
    for r in rows:
        items.setdefault(r["node_id"], {"node": r, "logs": []})["logs"].append(r["body"])
    sched_ids = _scheduled_node_ids(con, target)
    sched_wi = workitem_ids(con, sched_ids)   # batch-classify the scheduled-ahead set (same primitive as the log side)
    for nid in sched_ids:
        if nid in items or nid not in sched_wi:
            continue
        nr = _db.query_one(con, "node", cols="id AS node_id, title, status, priority", id=nid)
        if nr is None or (not inc_cancel and nr["status"] == "CANCELED"):
            continue
        items[nid] = {"node": nr, "logs": []}
    return items, sched_ids


@output_format
def cmd_day(args, con):
    """Reproduce a single day's progress (default today): bucket by work/personal -> project -> task -> that day's logs.
    Driven by log dates (not the day node), so it works for historical days too."""
    if args.date:
        try:
            target = _resolve_concrete_date(args.date)
        except ValueError:
            die(f"invalid date '{args.date}' (use YYYY-MM-DD / today / yesterday / day-before-yesterday / tomorrow / day-after-tomorrow)")
    else:
        target = _tu.today()
    # backstop: close any forgotten clock running >12h (wl day is the most-used view, so it's a
    # natural place to catch a zombie timer) — capped, reported in the header. Only when viewing
    # TODAY: looking at a past/future day is a pure historical read and must not mutate the live
    # clock.
    capped = []
    if target == _tu.today():
        from .state import _autocap_stale_clocks
        capped = _autocap_stale_clocks(con)
        if capped:
            con.commit()
    day = time_node_by_period(con, "day", target)

    # an explicit --status filter (applied below via make_node_filter) must override the
    # default CANCELED hide, else `day --status CANCELED` would drop its own matches.
    inc_cancel = getattr(args, "show_canceled", False) or bool(getattr(args, "status", None))
    items, sched_ids = _collect_day_items(con, target, inc_cancel)

    # shared --tag/--para/--status filter: keep only matching nodes.
    nf = make_node_filter(con, args)
    if nf:
        items = {nid: it for nid, it in items.items() if nf(nid)}
        if not items:
            def _render_empty_filter():
                _emit_day_header(con, day, target, capped)
                out(_c(f"  (nothing matches the filter on {target})", "meta"))
            return TextRenderable(_day_json_data(con, target, day, {}, sched_ids), _render_empty_filter)

    if not items:
        # clock-only day: time was tracked (wl spent / start-stop) but nothing logged/planned
        clock_sec = con.execute(
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE {_tu.local_day_sql('end_at')} = ? AND {_db.ALIVE}",
            (target,),
        ).fetchone()["s"]
        if clock_sec:
            cm = int(clock_sec / 60)
            _empty_msg = _c(f"  (no logged task progress for {target}) · CLOCK {cm}min ({cm // 60}h{cm % 60}m)", "meta")
        else:
            _empty_msg = _c(f"  (no log progress for {target}, and nothing planned)", "meta")

        def _render_empty():
            _emit_day_header(con, day, target, capped)
            out(_empty_msg)

        return TextRenderable(_day_json_data(con, target, day, {}, sched_ids), _render_empty)

    brief = _is_brief(args, "no_logs")
    log_tail = _resolve_log_tail(args, brief, default_tail=3)
    _by = getattr(args, "by", "plan")
    _full = _log_full(args)

    # compute bottom stats now (computation, not render)
    logged = {nid: (it["node"]["status"] or "TODO") for nid, it in items.items() if it["logs"]}
    stats = {}
    for s in logged.values():
        stats[s] = stats.get(s, 0) + 1
    done_count = stats.get("DONE", 0)
    total = len(logged)
    planned_undone = sum(
        1 for nid in items
        if not items[nid]["logs"] and items[nid]["node"]["status"] not in ("DONE", "CANCELED")
    )
    parts = [f"{s} {stats[s]}" for s in ("DONE", "DOING", "TODO", "LATER", "WAIT", "DEFERRED", "CANCELED") if stats.get(s)]
    # CLOCK total: unfiltered = all clock for the day; filtered = only the shown items'
    if nf:
        ids = list(items)
        if ids:
            qm = ",".join("?" * len(ids))
            total_sec = con.execute(
                f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock "
                f"WHERE {_tu.local_day_sql('end_at')} = ? AND node_id IN ({qm}) AND {_db.ALIVE}",
                [target] + ids,
            ).fetchone()["s"]
        else:
            total_sec = 0
    else:
        total_sec = con.execute(
            f"SELECT COALESCE(SUM(elapsed_sec), 0) AS s FROM clock WHERE {_tu.local_day_sql('end_at')} = ? AND {_db.ALIVE}",
            (target,),
        ).fetchone()["s"]
    total_min = int((total_sec or 0) / 60)
    line = f"  ── {target}: {done_count}/{total} tasks with progress"
    if parts:
        line += " · " + " · ".join(parts)
    if planned_undone:
        line += f" · planned·not-done {planned_undone}"
    if total_min:
        line += f" · CLOCK {total_min}min ({total_min // 60}h{total_min % 60}m)"

    def _render():
        _emit_day_header(con, day, target, capped)
        _render_day_group(con, items, by=_by, sched_ids=sched_ids, log_tail=log_tail, full=_full, day=target)
        out("")
        out(_c(line, "meta"))

    return TextRenderable(_day_json_data(con, target, day, items, sched_ids), _render)

def _tree_by(con, by, nf=None):
    """Flat 2-level view, regrouped by dimension (avoids deep time-layered nesting).
    `nf` (from make_node_filter) further restricts the listed nodes; empty groups are
    skipped when a filter is active."""
    if by == "tag":
        tags = [r["tag"] for r in _db.query(con, "tag", cols="DISTINCT tag", order="tag")]
        sem = [t for t in tags if t not in GENERIC_TAGS]
        if not sem:
            out("(no semantic tags)")
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
        projects = nodes_with_type(con, "type.para", "project", order="priority NULLS LAST, id")
        if not projects:
            out("(no project nodes)")
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
                for r in nodes_with_tag(con, proj_tags, types=("task", "meetlog", "habit"), cols="id"):
                    ids.add(r["id"])
            if nf:
                ids = {i for i in ids if nf(i)}
                # keep the project if it has matching members OR the project node itself
                # matches (e.g. --para project) — else `--by project --para project` would
                # drop every project because no member is itself a project.
                if not ids and not nf(proj["id"]):
                    continue
            _group_header(proj["title"], node_id=proj["id"], pri=proj["priority"],
                          suffix=f"({len(ids)})", lead="")
            for nid, n in zip(sorted(ids), Node.gets(con, sorted(ids))):
                claimed.add(nid)
                out(_node_line(con, n))
            if not ids:
                out("    " + _c("(no linked tasks)", "meta"))
        # orphans: task/meetlog/habit not attached to any project
        orphans = filter_workitems(con, Node.query(con, order="priority NULLS LAST, id"))
        orphans = [n for n in orphans if n["id"] not in claimed]
        if nf:
            orphans = [n for n in orphans if nf(n["id"])]
        if orphans:
            _group_header("(unassigned)", suffix=f"({len(orphans)})", lead="")
            for n in orphans:
                out(_node_line(con, n))

    elif by == "direction":
        for direction in ("work", "personal"):
            rows = nodes_with_tag(con, direction, types=("task", "meetlog", "habit", "project"),
                                  order="priority NULLS LAST, id")
            if nf:
                rows = [n for n in rows if nf(n["id"])]
                if not rows:
                    continue
            out(_c(f"[{direction}]", "header") + " " + _c(f"({len(rows)})", "meta"))
            for n in rows:
                out(_node_line(con, n))

def _tree_children(con, node, include_canceled=False):
    """Children ordering: time nodes ascending by title (date); others by priority -> id. CANCELED excluded by default."""
    sql = f"SELECT * FROM node WHERE parent_id = ? AND {_db.ALIVE}"
    sql_params = [node["id"]]
    frag, p = _status_filter_sql(include_canceled=include_canceled)
    if frag:
        sql += " AND " + frag
        sql_params.extend(p)
    rows = [Node.from_row(r) for r in con.execute(sql, sql_params)]

    def key(r):
        if node_type(con, r) in _TIME_LEVELS:
            return (0, r["title"], 0)
        pr = {"A": 0, "B": 1, "C": 2}.get(r["priority"], 3)
        return (1, pr, r["id"])

    return sorted(rows, key=key)

# fuzzy time nodes a task can be pinned at via scheduled_date (day is handled separately
# by _print_day_activity, which also folds in that day's logged activity).
_FUZZY_TIME_LEVELS = ("year", "quarter", "month", "week")


def _pinned_at(con, node):
    """Tasks fuzzy-pinned at a time node (scheduled_date == its title, e.g. '2026-06'
    month / '2026-W23' week / '2026' year). They hang under their project in the
    parent_id tree, not under the time node, so tree / focus on the time node would
    otherwise miss them — which led to creating duplicates when checking 'is anything
    already scheduled this month'. Returns [] for non-time / day nodes."""
    if node_type(con, node) not in _FUZZY_TIME_LEVELS:
        return []
    return Node.query(con, scheduled_date=node["title"], order="priority NULLS LAST, id")


def _print_tree(con, node, depth, max_depth, *, include_canceled=False, log_tail=3, full=False):
    out(_node_line(con, node, indent="  " * depth, sched=True))
    if max_depth is not None and depth >= max_depth:
        return
    if node_type(con, node) == "day":  # day has no real children (empty); expand today's log activity instead
        _print_day_activity(con, node, depth, max_depth,
                            include_canceled=include_canceled, log_tail=log_tail, full=full)
        return
    # fuzzy time pins: a month/week/year node's @-pinned tasks live under their
    # project, not here — surface them so a "what's scheduled this month" view sees them.
    children = _tree_children(con, node, include_canceled=include_canceled)
    child_ids = {c["id"] for c in children}
    for p in _pinned_at(con, node):
        if p["id"] not in child_ids and (include_canceled or p["status"] != "CANCELED"):
            out(_node_line(con, p, indent="  " * (depth + 1), sched=True))
    for c in children:
        _print_tree(con, c, depth + 1, max_depth,
                    include_canceled=include_canceled, log_tail=log_tail, full=full)


def _print_filtered_tree(con, nf, *, root_node=None, include_canceled=False, log_tail=3, full=False):
    """Render the structural tree pruned to nodes matching `nf` plus the ancestor paths
    that lead to them (so matches keep their context instead of being orphaned). Used by
    `wl tree` whenever a --tag/--para/--status filter is active. `root_node` restricts the
    search to that subtree. Note: this is the structural (area→project→task) tree — the
    log-derived day-activity expansion isn't shown here; use `wl day --tag …` for that."""
    if root_node is not None:
        universe = [root_node["id"]] + _collect_descendants(con, root_node["id"])
        subtree = set(universe)
    else:
        universe = [r["id"] for r in _db.query(con, "node", cols="id")]
        subtree = None
    keep = set()
    for nid in universe:
        if nf(nid):
            for a in _ancestors_chain(con, nid):  # node itself + chain up to the top
                keep.add(a["id"])
    if subtree is not None:
        keep &= subtree  # drop ancestors above the requested root
    # a time node in the subtree can have @-pins — tasks pinned at it via
    # scheduled_date, which hang under their project (NOT in this subtree) — that match
    # the filter. They aren't found by the universe scan, so collect them per time node
    # and force-keep that node so it renders as their container. Only on a --root
    # drill-down: in a full filtered tree the pin already shows under its own project.
    pin_parent = {}  # time_node_id -> [matching pin rows]
    if root_node is not None:
        for tnid, tn in zip(universe, Node.gets(con, universe)):
            if not tn or node_type(con, tn) not in _FUZZY_TIME_LEVELS:
                continue
            matched = [p for p in _pinned_at(con, tn)
                       if (include_canceled or p["status"] != "CANCELED") and nf(p["id"])]
            if matched:
                pin_parent[tnid] = matched
                for a in _ancestors_chain(con, tn["id"]):
                    if a["id"] in subtree:
                        keep.add(a["id"])
    if not keep:
        where = "" if root_node is None else f" under #{root_node['id']}"
        out(f"(nothing{where} matches the filter)")
        return
    if root_node is not None:
        roots = [root_node]
    else:
        roots = [r for r in Node.query(con, parent_id=None) if r["id"] in keep]
    for root in roots:
        _print_kept_subtree(con, root, 0, keep, pin_parent=pin_parent,
                            include_canceled=include_canceled, log_tail=log_tail, full=full)


def _print_kept_subtree(con, node, depth, keep, *, pin_parent=None,
                        include_canceled=False, log_tail=3, full=False):
    """Print `node` and recurse only into children in the `keep` set (the filtered-tree
    companion to _print_tree; no depth cap — `keep` is already the pruned node set).
    `pin_parent[node_id]` (if given) is the precomputed list of filter-matching @-pins to
    list under a time node (under a filter)."""
    out(_node_line(con, node, indent="  " * depth, sched=True))
    for p in (pin_parent or {}).get(node["id"], []):
        out(_node_line(con, p, indent="  " * (depth + 1), sched=True))
    for c in _tree_children(con, node, include_canceled=include_canceled):
        if c["id"] in keep:
            _print_kept_subtree(con, c, depth + 1, keep, pin_parent=pin_parent,
                                include_canceled=include_canceled, log_tail=log_tail, full=full)


_BUCKET_ORDER = ["work", "personal", "other"]
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def _print_day_activity(con, day_node, depth, max_depth, *, include_canceled=False, log_tail=3, full=False):
    """For a day node in the tree view, expand that day's activity: tasks with logs + that day's logs (today only, not others).
    log_tail: None = full expansion / 0 = no log expansion / N = latest N per task (default 3, middle elided to keep wl tree compact)."""
    from collections import OrderedDict

    target = day_node["title"][:10]
    rows = _day_workitem_log_rows(con, target, include_canceled)
    tasks = OrderedDict()
    for r in rows:
        tasks.setdefault(r["node_id"], {"r": r, "logs": []})["logs"].append(r["body"])
    ind = "  " * (depth + 1)
    for nid, t in tasks.items():
        n = t["r"]
        is_habit = node_type(con, nid) == "habit"
        # done today = a check-in metric that day (not "any log"), for a habit or a recurrence
        # ACTIVE on `target`. Gating on active-recurring means a one-off tick (no --done) never
        # renders [x], and a recurrence stopped before this day doesn't either.
        done = (is_habit or _has_active_rrule(con, nid, target)) and _has_checkin(con, nid, target)
        mh = mh_plain = ""
        if is_habit:
            prog = _habit_month_progress(con, nid, target)
            if prog:
                mh_plain = f"  (this month {prog[0]}/{prog[1]})"; mh = _c(mh_plain, "meta")
        prefix, prefix_cols = _node_activity_prefix(n, nid, ind, done=done)
        out(_hang_wrap(prefix, prefix_cols, n["title"], tail=mh, tail_cols=_display_width(mh_plain)))
        if log_tail != 0 and (max_depth is None or depth + 1 < max_depth):
            # drop empty metric-carrier bodies (their data shows in the ↳ metric lines below), so
            # they don't render as blank `· ` rows or consume the log-tail budget
            logs = [b for b in t["logs"] if (b or "").strip()]
            shown = logs if log_tail is None else logs[-log_tail:]
            omitted = 0 if log_tail is None else max(0, len(logs) - log_tail)
            if omitted:
                out("  " * (depth + 2) + _c(f"· … ({omitted} earlier logs elided)", "meta"))
            for body in shown:
                _log_body_row(body, "  " * (depth + 2), full=full)
            # fold that day's datapoints (skip checkin marker — reflected by [x]); elide >5
            indent = "  " * (depth + 2)
            mrows = [m for m in con.execute(
                f"SELECT tag, value_num, value_text, unit FROM metric WHERE node_id = ? AND {_db.ALIVE} "
                f"AND {_tu.local_day_sql('at')} = ? ORDER BY id", (nid, target)) if m["tag"] != "checkin"]
            for line in metric_rows(mrows, indent):
                out(line)

def _print_default_tree(con, *, include_canceled=False, log_tail=3, full=False):
    """Default wl tree: areas one level (area name only) + timeline expanded up to today (year -> quarter -> month -> week -> today + today's activity).
    To drill into an area's projects use --root <area>; for other days use --root <week/month>. CANCELED excluded by default."""
    from datetime import date

    _life = nodes_with_type(con, "type.date", "lifetime", order="id")
    life = _life[0] if _life else None
    has_day = bool(nodes_with_type(con, "type.date", "day", cols="id"))
    has_month = bool(nodes_with_type(con, "type.date", "month", cols="id"))
    if not life and not has_day and not has_month:
        # Nothing anchors the timeline/areas overview. Be honest about *why* it's empty: a
        # brand-new DB vs. nodes that exist but aren't in the overview's scope yet — never
        # imply "the DB is empty" when nodes actually exist (the old bare "(no root nodes)"
        # printed identically in both cases and misread as empty).
        total = Node.count(con)
        if total == 0:
            out(_c('(empty — add a task with `wl add "..."`, or `wl day` to start today)', "meta"))
        else:
            noun = "node" if total == 1 else "nodes"
            out(_c(f"({total} {noun} exist but none are in the timeline/areas overview yet "
                   "— try `wl tree --depth 9` or `wl ls --all`)", "meta"))
        return
    base = 0
    if life:
        out(_node_line(con, life))
        base = 1

    # timeline -> path to today (year -> quarter -> month -> week -> day) + today's activity; if no day node today, fall back to the latest month
    today = _tu.today()
    dayn = time_node_by_period(con, "day", today)
    if dayn:
        chain = [n for n in _ancestors_chain(con, dayn["id"]) if node_type(con, n) != "lifetime"]
        for d, n in enumerate(chain):
            out(_node_line(con, n, indent="  " * (base + d), sched=True))
        # today: only tasks, no log expansion (logs are for drill-down: wl day / wl tree --root <day> --depth big)
        day_depth = base + len(chain) - 1
        _print_day_activity(con, dayn, day_depth, max_depth=day_depth + 1, log_tail=log_tail, full=full)
    else:
        _mon = nodes_with_type(con, "type.date", "month", order="title DESC")
        mon = _mon[0] if _mon else None
        if mon:
            out(_node_line(con, mon, indent="  " * base, sched=True))

    # areas one level only (no project expansion)
    if life:
        for a in _tree_children(con, life, include_canceled=include_canceled):
            if node_type(con, a) == "area":
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
            _group_header(g["title"], style="type", lead="    ")
            for nid, it in g["tasks"].items():
                n = it["node"]
                logs = it["logs"]
                nk_habit = node_type(con, nid) == "habit"
                # done today = a check-in metric that day, for a habit or a recurrence active that
                # day; a one-off tick or a since-stopped recurrence never renders [x].
                done = bool(day) and (nk_habit or _has_active_rrule(con, nid, day)) and _has_checkin(con, nid, day)
                hint = hint_plain = ""
                if not logs and n["status"] not in ("DONE", "CANCELED") and by != "plan":
                    # only "not-done" if the task is still open; a terminal-status task
                    # scheduled on a day with no logs is done, not pending (avoids the
                    # contradictory "[x] … «planned·not-done»"). Suppressed under `--by plan`
                    # the `▸ planned` group header + the `[ ]` marker already say it.
                    hint_plain = "  «planned·not-done»"; hint = _c(hint_plain, "planned")
                elif logs and log_tail == 0:
                    # compact mode: don't expand body, attach a count hint after the title line
                    hint_plain = f"  ({len(logs)} log)"; hint = _c(hint_plain, "meta")
                # THIS DAY's duration (clock intervals + plain-note span scoped to the day),
                # not the node's all-time total; see _node_clock_min docstring
                dur = _fmt_dur(_node_clock_min(con, nid, day=day))
                dur_str = (" " + _c(dur, "clock")) if dur else ""
                dur_plain = (" " + dur) if dur else ""
                # habit month-to-date completion rate (this month N/M); skip if no schedule
                mh = mh_plain = ""
                if nk_habit and day:
                    prog = _habit_month_progress(con, nid, day)
                    if prog:
                        mh_plain = f"  (this month {prog[0]}/{prog[1]})"; mh = _c(mh_plain, "meta")
                prefix, prefix_cols = _node_activity_prefix(n, nid, "      ", done=done)
                # suffixes ride the last title line via _hang_wrap (so they wrap correctly, not
                # spill to column 0); pass their combined PLAIN width as the tail budget
                tail = dur_str + hint + mh
                out(_hang_wrap(prefix, prefix_cols, n["title"], tail=tail,
                               tail_cols=_display_width(dur_plain + hint_plain + mh_plain)))
                if log_tail == 0:
                    continue
                # metric-carrier logs have empty bodies (their data renders in the ↳ metric lines
                # below); drop them so they don't show as blank `· ` rows or eat the log-tail budget.
                # `logs` itself stays unfiltered above — a metric-only node was still touched today,
                # so it must NOT read as «planned·not-done».
                shown_logs = [b for b in logs if (b or "").strip()]
                bodies = shown_logs if log_tail is None else shown_logs[-log_tail:]
                if log_tail is not None and len(shown_logs) > log_tail:
                    out("        " + _c(f"· … ({len(shown_logs) - log_tail} earlier logs elided)", "meta"))
                for body in bodies:
                    _log_body_row(body, "        ", full=full)
                # fold this node's datapoints that day beneath it (skip the checkin marker —
                # it's already reflected by the [x]); over-count elided
                if day:
                    mrows = [m for m in con.execute(
                        f"SELECT tag, value_num, value_text, unit FROM metric WHERE node_id = ? AND {_db.ALIVE} "
                        f"AND {_tu.local_day_sql('at')} = ? ORDER BY id", (nid, day)) if m["tag"] != "checkin"]
                    for line in metric_rows(mrows, "        "):
                        out(line)

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
        f"SELECT COUNT(DISTINCT {_tu.local_day_sql('at')}) FROM metric WHERE node_id = ? AND tag = 'checkin' AND {_db.ALIVE} "
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


def _split_until(rrule):
    """Split a recurrence rule into (base_rule, until_date_or_None). A stopped recurrence encodes
    its end as a `;until=YYYY-MM-DD` suffix on the rrule string (set by `wl sched stop`), so every
    existing rrule consumer keeps working by stripping the suffix first. `until` is inclusive."""
    if rrule and ";until=" in rrule:
        base, _, until = rrule.partition(";until=")
        return base, (until or None)
    return rrule, None


def _rrule_display(rrule):
    """User-facing form of a (possibly stopped) rrule — the single source so every sched surface
    (`wl show` / `wl sched ls` / sched write) renders a stopped rule the same clean way instead of
    leaking the internal `;until=` suffix: `weekly:Wed` or `weekly:Wed (stopped 2026-06-30)`."""
    base, until = _split_until(rrule)
    return f"{base} (stopped {until})" if until else base


def _rrule_active(rrule, today):
    """Whether a recurrence is still live on `today` (YYYY-MM-DD): no `;until=`, or its inclusive
    until >= today. Single source for 'active recurrence vs one stopped in the past', shared by the
    done-warning / `wl show` last-check-in / `wl ls --not-checked-in` surfaces so they agree."""
    _, until = _split_until(rrule)
    return until is None or until >= today


def _has_active_rrule(con, nid, on_date):
    """True if the node has a recurrence live ON `on_date` — the 'actively recurring that day' test
    for the `wl day` completion marker, so a recurrence stopped before that day doesn't render [x]
    on it (keeps the day gate consistent with every other `_rrule_active` consumer)."""
    return any(r["rrule"] and _rrule_active(r["rrule"], on_date)
               for r in _db.query(con, "sched", cols="rrule", node_id=nid))


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
    rrule, until = _split_until(rrule)
    if until and target > until:   # stopped recurrence: no fire after the (inclusive) end date
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
    r = DateMeta.get(con, target)
    return r.label if r else None


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

