"""worklog commands: query group."""
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


from .views import _print_tree, _render_day_group, _scheduled_node_ids

from .bulk import _VALID_FIND_FIELDS, _VALID_KINDS
from .state import _ids_list
from .views import _print_tree, _render_day_group, _scheduled_node_ids

def cmd_show(args, con):
    # multiple ids: show each in turn, blank-line separated; same rendering
    ids = _ids_list(args)
    for i, nid in enumerate(ids):
        if i > 0:
            print()
        args.id = nid
        _show_one(args, con)

def cmd_ls(args, con):
    """list nodes. Mirrors shell ls multi-dimensional query conventions (ls -t / -S / -r etc.):
    - default --sort pri (priority+id), like ls default-by-name
    - --sort created/closed/scheduled/updated/title/id similar to ls -t / -S
    - --reverse / -r reverses (like ls -r)
    - --recent N anything that changed in the last N days (created/log/closed)
    - --unscheduled tasks not in sched
    - --ids 1 2 3 list specific ids directly (like ls file1 file2)
    """
    inc_cancel = getattr(args, "show_canceled", False)

    # --ids mode: list specific ids directly (like ls file1 file2), skipping filters
    if getattr(args, "ids", None):
        rows = []
        for nid in args.ids:
            r = con.execute("SELECT * FROM node WHERE id = ?", (nid,)).fetchone()
            if r:
                rows.append(r)
        if not rows:
            print("(no nodes matched given ids)")
            return
        brief = getattr(args, "brief", False)
        for n in rows:
            out(_node_line(con, n, tags=not brief, sched=not brief))
        return

    where = []
    params = []
    if args.kind:
        where.append("kind = ?")
        params.append(args.kind)
    if args.status:
        where.append("status = ?")
        params.append(args.status)
    elif not args.all:
        # default: list non-DONE only (DONE hidden); --show-canceled decides CANCELED visibility separately
        frag, p = _status_filter_sql(inc_cancel, hide_done=True)
        if frag:
            where.append(frag)
            params.extend(p)
    if args.tag:
        tags_list = args.tag.split(",") if "," in args.tag else [args.tag]
        for t in tags_list:
            where.append("id IN (SELECT node_id FROM tag WHERE tag = ?)")
            params.append(t.strip())
    if args.parent is not None:
        where.append("parent_id = ?")
        params.append(args.parent)
    if getattr(args, "unscheduled", False):
        where.append("id NOT IN (SELECT node_id FROM sched)")
    if getattr(args, "recent", None):
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=args.recent)).isoformat()
        where.append("(date(created_at) >= ? OR date(closed_at) >= ? "
                     "OR id IN (SELECT node_id FROM log WHERE date(logged_at) >= ?))")
        params.extend([cutoff, cutoff, cutoff])

    sort_key = getattr(args, "sort", "pri") or "pri"
    if sort_key == "updated":
        # subquery: each node's latest log time; nodes with no log fall back to created_at
        sql = ("SELECT n.*, COALESCE((SELECT MAX(logged_at) FROM log WHERE node_id = n.id), n.created_at) "
               "AS _last FROM node n")
        order_by = "_last DESC, id DESC"
    else:
        sql = "SELECT * FROM node"
        order_by = _LS_SORT_SQL[sort_key]
    if where:
        sql += " WHERE " + " AND ".join(where)
    if getattr(args, "reverse", False):
        # simple ASC/DESC swap; columns without ASC/DESC get DESC appended
        flipped = []
        for piece in order_by.split(","):
            piece = piece.strip()
            if " DESC" in piece:
                flipped.append(piece.replace(" DESC", " ASC"))
            elif " ASC" in piece:
                flipped.append(piece.replace(" ASC", " DESC"))
            else:
                flipped.append(piece + " DESC")
        order_by = ", ".join(flipped)
    sql += f" ORDER BY {order_by}"

    rows = list(con.execute(sql, params))
    if not rows:
        print("(no nodes)")
        return

    # default limit 20 (avoids flooding on bare ls); --all / --limit 0 removes it; --limit N / --top N is explicit
    explicit_limit = getattr(args, "limit", None)
    explicit_top = getattr(args, "top", None)
    if explicit_limit is None and explicit_top is None and not args.all:
        args.limit = 20  # default 20 injected into args so _apply_top_limit sees it
    rows, total = _apply_top_limit(rows, args)
    if len(rows) < total:
        out(_c(f"(showing {len(rows)}/{total}; --limit N to adjust / --all to see all)", "meta"))

    brief = getattr(args, "brief", False)
    for n in rows:
        out(_node_line(con, n, tags=not brief, sched=not brief))

def cmd_find(args, con):
    """Full-text search nodes: title/body/log/tag/prop/link; mark hits + show hit content not in the title line in indented expansion."""
    q = args.query
    if not q or not q.strip():
        sys.exit("✗ search term cannot be empty (empty query would dump all nodes)")
    q = q.strip()
    like = f"%{q}%"
    if args.in_:
        fields = set(args.in_.split(","))
        bad = fields - _VALID_FIND_FIELDS
        if bad:
            sys.exit(f"✗ invalid --in fields: {sorted(bad)} (valid: {sorted(_VALID_FIND_FIELDS)})")
    else:
        fields = _VALID_FIND_FIELDS
    if args.kind and args.kind not in _VALID_KINDS:
        sys.exit(f"✗ invalid --kind: '{args.kind}' (valid: {sorted(_VALID_KINDS)})")
    hits = {}  # node_id -> set of fields with hits

    def mark(rows, where):
        for r in rows:
            hits.setdefault(r[0], set()).add(where)

    if "title" in fields:
        mark(con.execute("SELECT id FROM node WHERE title LIKE ?", (like,)), "title")
    if "body" in fields:
        mark(con.execute("SELECT id FROM node WHERE body LIKE ?", (like,)), "body")
    if "log" in fields:
        mark(con.execute("SELECT DISTINCT node_id FROM log WHERE body LIKE ? AND body NOT LIKE 'CLOCK_%'", (like,)), "log")
    if "tag" in fields:
        mark(con.execute("SELECT DISTINCT node_id FROM tag WHERE tag LIKE ?", (like,)), "tag")
    if "prop" in fields:
        mark(con.execute("SELECT DISTINCT node_id FROM prop WHERE key LIKE ? OR value LIKE ?", (like, like)), "prop")
    if "link" in fields:
        mark(con.execute("SELECT DISTINCT node_id FROM link WHERE vault_doc LIKE ?", (like,)), "link")

    inc_cancel = getattr(args, "show_canceled", False)
    rows = []
    for nid in hits:
        n = con.execute("SELECT * FROM node WHERE id = ?", (nid,)).fetchone()
        if args.kind and n["kind"] != args.kind:
            continue
        if not inc_cancel and n["status"] == "CANCELED":
            continue
        rows.append(n)
    if not rows:
        print(f"(no matches for '{q}')")
        return
    rows.sort(key=lambda n: (n["priority"] or "Z", n["id"]))
    total = len(rows)
    # --limit: default 20 to avoid flooding; --limit 0 / --all shows all
    limit = getattr(args, "limit", None)
    show_all = getattr(args, "all", False)
    if limit is None and not show_all:
        limit = 20
    if limit and limit > 0 and total > limit:
        rows = rows[:limit]
        out(_c(f"'{q}' {total} hits (showing first {limit}; use --all or --limit 0 to see all):", "header"))
    else:
        out(_c(f"'{q}' {total} hits:", "header"))
    for n in rows:
        nid = n["id"]
        where = hits[nid]
        out(_node_line(con, n, hl=q) + "  " + _c(f"«{'/'.join(sorted(where))}»", "meta"))
        # show hit contents not in the title line (title already highlighted, no expansion needed)
        if "body" in where and n["body"]:
            out("    " + _c("body:", "meta") + " " + _snippet(n["body"], q))
        if "log" in where:
            for r in con.execute("SELECT body FROM log WHERE node_id=? AND body LIKE ? AND body NOT LIKE 'CLOCK_%' ORDER BY id", (nid, like)):
                out("    " + _c("log:", "meta") + " " + _snippet(r["body"], q))
        if "tag" in where:
            tg = [r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=? AND tag LIKE ?", (nid, like))]
            out("    " + _c("tag:", "meta") + " " + _c(", ".join(tg), "tag"))
        if "prop" in where:
            for r in con.execute("SELECT key,value FROM prop WHERE node_id=? AND (key LIKE ? OR value LIKE ?)", (nid, like, like)):
                out("    " + _c("prop:", "meta") + " " + _c(f"{r['key']}={r['value']}"))
        if "link" in where:
            for r in con.execute("SELECT vault_doc FROM link WHERE node_id=? AND vault_doc LIKE ?", (nid, like)):
                out("    " + _c("link:", "meta") + " " + _c(f"[[{r['vault_doc']}]]"))

def cmd_focus(args, con):
    """Focus on a node: upstream path + self + downstream subtree."""
    n = con.execute("SELECT * FROM node WHERE id = ?", (args.id,)).fetchone()
    if not n:
        sys.exit(f"✗ node #{args.id} not found")

    chain = _ancestors_chain(con, args.id)
    # upstream path (excludes self)
    upstream = chain[:-1]
    if upstream:
        out(_c("upstream:", "meta") + " " + _c(" / ".join(f"#{p['id']} {p['title']}" for p in upstream)))

    # self
    mk = _c(_status_marker(n["status"]), _STATUS_STYLE.get(n["status"], "todo"))
    pri = (_c(f"[#{n['priority']}]", _PRI_STYLE.get(n["priority"])) + " ") if n["priority"] else ""
    out("▶ focus " + mk + " " + _c(f"#{n['id']}", "id") + " " + pri + _c(f"[{n['kind']}]", "kind") + " " + _c(n["title"], "header"))

    # downstream subtree
    children = con.execute(
        f"SELECT * FROM node WHERE parent_id = ? {_ORDER_BY_PRI_ID}", (args.id,)
    ).fetchall()
    if children:
        out(_c("downstream:", "meta"))
        for c in children:
            _print_tree(con, c, depth=1, max_depth=args.depth)
    else:
        out(_c("downstream: (no children)", "meta"))

    # related: other nodes sharing semantic tags (excluding upstream/downstream/self + generic tags to avoid flooding)
    if args.related:
        own_tags = _node_tags(con, args.id)
        sem_tags = [t for t in own_tags if t not in GENERIC_TAGS]
        if not sem_tags:
            out(_c("related: (only generic-dimension tags; no project/topic tag to link by)", "meta"))
        else:
            qmarks = ",".join("?" * len(sem_tags))
            exclude = set(c["id"] for c in children) | {p["id"] for p in chain}
            rel = con.execute(
                f"SELECT DISTINCT n.* FROM node n "
                f"JOIN tag t ON n.id = t.node_id WHERE t.tag IN ({qmarks}) "
                f"ORDER BY n.id",
                sem_tags,
            ).fetchall()
            rel = [r for r in rel if r["id"] not in exclude]
            if rel:
                out(_c(f"related (shared tag {'/'.join(sem_tags)}):", "header"))
                for r in rel:
                    out(_node_line(con, r, indent="  "))
            else:
                out(_c(f"related (tag {'/'.join(sem_tags)}): (no other nodes)", "meta"))

def cmd_ancestors(args, con):
    """Show only the upstream path (root -> node)."""
    chain = _ancestors_chain(con, args.id)
    if not chain:
        sys.exit(f"✗ node #{args.id} not found")
    for depth, node in enumerate(chain):
        indent = "  " * depth
        arrow = "▶ " if node["id"] == args.id else ""
        out(f"{indent}{arrow}" + _c(f"#{node['id']}", "id") + " " + _c(f"[{node['kind']}]", "kind") + " " + _c(node["title"], "header" if node["id"] == args.id else None))

def cmd_descendants(args, con):
    """Show only the downstream subtree (node -> all descendants)."""
    n = con.execute("SELECT * FROM node WHERE id = ?", (args.id,)).fetchone()
    if not n:
        sys.exit(f"✗ node #{args.id} not found")
    _print_tree(con, n, depth=0, max_depth=args.depth)



def cmd_projects(args, con):
    """List active projects + per-project todo/done counts + recent activity.
    brief or no --since: skip the "recent YYYY-MM-DD" segment. --since: only list projects with logs after that day."""
    inc_cancel = getattr(args, "show_canceled", False)
    brief = getattr(args, "brief", False)
    # if any of --since/--week/--month is set, use _resolve_window to get since as cutoff;
    # otherwise since=None (no filter). until has no meaning here (we only check "active after that day").
    if any(getattr(args, k, None) for k in ("since", "until", "week", "month")):
        resolved_since, _ = _resolve_window(args)
        since = resolved_since
    else:
        since = None
    where = "WHERE kind = 'project'"
    proj_params = []
    if not args.all:
        frag, p = _status_filter_sql(inc_cancel, hide_done=True)
        if frag:
            where += " AND " + frag
            proj_params.extend(p)
    projects = con.execute(
        f"SELECT * FROM node {where} {_ORDER_BY_PRI_ID}",
        proj_params,
    ).fetchall()
    if not projects:
        print("(no active projects)")
        return

    # collect lines -> apply --since/--top/--limit truncation -> print
    lines = []
    for proj in projects:
        ids = _project_members(con, proj["id"])
        done = doing = pending = total = 0
        recent = None
        if ids:
            qm = ",".join("?" * len(ids))
            for r in con.execute(
                f"SELECT status, COUNT(*) c FROM node WHERE id IN ({qm}) GROUP BY status",
                list(ids),
            ):
                c = r["c"]
                total += c
                s = r["status"]
                if s == "DONE":
                    done += c
                elif s == "DOING":
                    doing += c
                elif s in ("CANCELED",):
                    pass
                else:
                    pending += c
            r1 = con.execute(f"SELECT MAX(logged_at) m FROM log WHERE node_id IN ({qm})", list(ids)).fetchone()["m"]
            r2 = con.execute(
                f"SELECT MAX(COALESCE(closed_at, created_at)) m FROM node WHERE id IN ({qm})", list(ids)
            ).fetchone()["m"]
            cands = [x for x in (r1, r2) if x]
            recent = max(cands) if cands else None

        # --since filter: based on real activity signals (log time / closed_at), not created_at
        # (a newly-created task doesn't count as "active"; if it sits idle a few days it still gets filtered out)
        if since:
            r_log = con.execute(f"SELECT MAX(logged_at) m FROM log WHERE node_id IN ({qm})", list(ids)).fetchone()["m"] if ids else None
            r_closed = con.execute(f"SELECT MAX(closed_at) m FROM node WHERE id IN ({qm})", list(ids)).fetchone()["m"] if ids else None
            activity = max([x for x in (r_log, r_closed) if x], default=None)
            if not activity or activity[:10] < since:
                continue

        pri = _c(f"[#{proj['priority']}]", _PRI_STYLE.get(proj["priority"])) if proj["priority"] else _c("[ ]", "todo")
        parts = [f"done {done}/{total}"]
        if doing:
            parts.append(f"doing {doing}")
        if pending:
            parts.append(f"todo {pending}")
        stat = " · ".join(parts)
        if recent and not brief:
            stat += f" · latest {recent[:16]}"
        lines.append(_c(f"#{proj['id']:<3d}", "id") + " " + pri + " " + _c(proj["title"], "header") + " — " + _c(stat, "meta"))

    lines, total_lines = _apply_top_limit(lines, args)
    _print_truncation_hint(len(lines), total_lines)
    for line in lines:
        out(line)

def cmd_changes(args, con):
    """Per-project changes in a time window: closed / added / log activity (input for weekly reports / Linear update)."""
    since, until = _resolve_window(args)

    def in_win(ts):
        return bool(ts) and since <= ts[:10] <= until

    out(_c(f"📅 {since} ~ {until} change summary", "header"))
    projects = con.execute(
        f"SELECT * FROM node WHERE kind = 'project' {_ORDER_BY_PRI_ID}"
    ).fetchall()

    any_output = False
    for proj in projects:
        members = _project_members(con, proj["id"])
        done, added_open, logged = [], [], 0
        for mid in members:
            n = con.execute("SELECT * FROM node WHERE id = ?", (mid,)).fetchone()
            d = in_win(n["closed_at"])
            if d:
                done.append(n)
            elif in_win(n["created_at"]):
                added_open.append(n)
            has_log = con.execute(
                "SELECT 1 FROM log WHERE node_id = ? AND substr(logged_at,1,10) BETWEEN ? AND ? "
                "AND body NOT LIKE 'CLOCK_%' LIMIT 1",
                (mid, since, until),
            ).fetchone()
            if has_log:
                logged += 1
        if not (done or added_open or logged):
            continue
        any_output = True
        pri = (_c(f"[#{proj['priority']}]", _PRI_STYLE.get(proj["priority"])) + " ") if proj["priority"] else ""
        out("\n▸ " + pri + _c(proj["title"], "header"))
        if done:
            out("  " + _c("✓ done", "done") + f" {len(done)}: " + _c(", ".join(f"#{n['id']} {n['title']}" for n in done)))
        if added_open:
            out(f"  + added open {len(added_open)}: " + _c(", ".join(f"#{n['id']} {n['title']}" for n in added_open)))
        if logged:
            out("  " + _c(f"· {logged} node(s) with progress logs", "meta"))

    if not any_output:
        out(_c("(no project changes in window)", "meta"))





# --- DRY helpers: filter / truncate / bulk status change, reused across commands ---






def cmd_summary(args, con):
    """Time-window summary: aggregate counts + sliced by direction/project + completion list (input for weekly / monthly reports)."""
    import re as _re

    since, until = _resolve_window(args)
    inc_cancel = getattr(args, "show_canceled", False)

    def inw(ts):
        return bool(ts) and since <= ts[:10] <= until

    sql = "SELECT * FROM node WHERE kind IN ('task','meetlog','habit')"
    sm_params = []
    frag, p = _status_filter_sql(include_canceled=inc_cancel)
    if frag:
        sql += " AND " + frag
        sm_params.extend(p)
    nodes = con.execute(sql, sm_params).fetchall()
    done = [n for n in nodes if inw(n["closed_at"])]
    added_open = [n for n in nodes if inw(n["created_at"]) and not inw(n["closed_at"])]
    doing = [n for n in nodes if n["status"] == "DOING"]

    clock_min = 0
    for r in con.execute("SELECT body, logged_at FROM log WHERE body LIKE 'CLOCK_OUT%'"):
        if inw(r["logged_at"]):
            m = _re.search(r"elapsed=(\d+)min", r["body"])
            if m:
                clock_min += int(m.group(1))

    out(_c(f"📊 {since} ~ {until} summary", "header"))
    line = f"done {len(done)} · doing {len(doing)} · added-open {len(added_open)}"
    if clock_min:
        line += f" · clock {clock_min // 60}h{clock_min % 60}m"
    out(_c(line))

    # by direction
    dir_lines = []
    for d in ("work", "personal"):
        dd = [n for n in done if _has_tag(con, n["id"], d)]
        if dd:
            dir_lines.append(f"  {d}: done {len(dd)}")
    if dir_lines:
        out(_c("\nby direction:", "header"))
        out(_c("\n".join(dir_lines)))

    # pending (window-relevant): planned / doing / added-in-window and not done
    pending = [
        n for n in nodes
        if (n["status"] or "TODO") not in ("DONE", "CANCELED")
        and (_has_tag(con, n["id"], "planned") or n["status"] == "DOING" or inw(n["created_at"]))
    ]

    # === by project: per-project done + pending (grouped by status), each with priority + clock ===
    done_map = {n["id"]: n for n in done}
    pend_map = {n["id"]: n for n in pending}

    def _print_block(p_done, p_pending, indent="    "):
        if p_done:
            for n in sorted(p_done, key=lambda n: (n["priority"] or "Z", n["id"])):
                out(_node_line(con, n, indent=indent, done=True, planned=True, clock=True, sched=True))
        if p_pending:
            by_status = {}
            for n in p_pending:
                by_status.setdefault(n["status"] or "TODO", []).append(n)
            for st, label in (("DOING", "doing"), ("TODO", "todo"), ("LATER", "later"), ("WAIT", "waiting")):
                grp = by_status.get(st, [])
                if not grp:
                    continue
                out(_c(f"{indent}· {label} ({st}):", "meta"))
                for n in sorted(grp, key=lambda n: (n["priority"] or "Z", n["id"])):
                    out(_node_line(con, n, indent=indent + "  ", planned=True, clock=True, sched=True))

    if args.by == "day":
        from collections import defaultdict

        day_done = defaultdict(list)
        for n in done:
            day_done[n["closed_at"][:10]].append(n)
        day_pend = defaultdict(list)
        for n in pending:
            d = (n["scheduled_at"] or n["created_at"] or "")[:10] or "unscheduled"
            day_pend[d].append(n)
        if day_done or day_pend:
            out(_c("\n=== by day ===", "header"))
            for d in sorted(set(day_done) | set(day_pend)):
                pd = day_done.get(d, [])
                pp = day_pend.get(d, [])
                out("\n▸ " + _c(d, "header") + _c(f"  (done {len(pd)} / pending {len(pp)})", "meta"))
                _print_block(pd, pp)
    elif done_map or pend_map:
        out(_c("\n=== by project ===", "header"))
        projects = con.execute(
            f"SELECT * FROM node WHERE kind = 'project' {_ORDER_BY_PRI_ID}"
        ).fetchall()
        # by default dedup by task id: a task appearing in multiple projects is listed only in the first match;
        # --no-dedup restores the old behavior (task repeated in each project bucket).
        dedup = not getattr(args, "no_dedup", False)
        projects_only = getattr(args, "brief", False) or getattr(args, "projects_only", False)
        top_n = getattr(args, "top", None)
        claimed = set()
        # compute pd/pp per project first, used for --top sort; dedup happens here
        plan = []  # [(proj, pd, pp)]
        for proj in projects:
            members = _project_members(con, proj["id"])
            if dedup:
                pd = [done_map[i] for i in members if i in done_map and i not in claimed]
                pp = [pend_map[i] for i in members if i in pend_map and i not in claimed]
            else:
                pd = [done_map[i] for i in members if i in done_map]
                pp = [pend_map[i] for i in members if i in pend_map]
            if not (pd or pp):
                continue
            # claimed is always tracked (dedup uses it to exclude in later projects; no-dedup only affects the "unassigned" segment)
            claimed |= {n["id"] for n in pd} | {n["id"] for n in pp}
            plan.append((proj, pd, pp))
        if top_n is not None:
            plan.sort(key=lambda x: (len(x[1]) + len(x[2])), reverse=True)
            plan = plan[:top_n]
        for proj, pd, pp in plan:
            pri = (_c(f"[#{proj['priority']}]", _PRI_STYLE.get(proj["priority"])) + " ") if proj["priority"] else ""
            out("\n▸ " + pri + _c(proj["title"], "header") + _c(f"  (done {len(pd)} / pending {len(pp)})", "meta"))
            if not projects_only:
                _print_block(pd, pp)
        # window nodes not in any project
        od = [done_map[i] for i in done_map if i not in claimed]
        op = [pend_map[i] for i in pend_map if i not in claimed]
        if (od or op) and top_n is None:
            out("\n▸ " + _c("(unassigned)", "header") + _c(f"  (done {len(od)} / pending {len(op)})", "meta"))
            if not projects_only:
                _print_block(od, op)

def cmd_logs(args, con):
    """List all log entries in a time range. Default: last N days only, to avoid full-history flooding."""
    from datetime import date, timedelta

    # presets: wl logs today / yesterday / week / recent
    preset = getattr(args, "preset", None)
    if preset == "today":
        args.date = date.today().isoformat()
    elif preset == "yesterday":
        args.date = (date.today() - timedelta(days=1)).isoformat()
    elif preset == "week":
        # this Monday
        today = date.today()
        args.since = (today - timedelta(days=today.weekday())).isoformat()
    elif preset == "recent":
        args.days = 1
        args.brief = True  # explicit brief

    where = []
    params = []
    if args.id:
        where.append("node_id = ?")
        params.append(args.id)
    if args.date:
        try:
            args.date = _resolve_concrete_date(args.date)
        except ValueError:
            sys.exit(f"✗ invalid --date '{args.date}' (use YYYY-MM-DD / today / yesterday)")
        where.append("date(logged_at) = ?")
        params.append(args.date)
    # default time window: when no id/date/since given, only the last N days (default 7)
    since = args.since
    if not args.id and not args.date and not since:
        since = (date.today() - timedelta(days=getattr(args, "days", 7) or 7)).isoformat()
    if since:
        where.append("date(logged_at) >= ?")
        params.append(since)
    if getattr(args, "until", None):
        where.append("date(logged_at) <= ?")
        params.append(args.until)
    grouped = getattr(args, "group", "none") == "day"
    cols = "log.id, log.node_id, log.logged_at, log.body, node.title"
    if grouped:
        cols += ", node.status, node.priority, node.kind"
    sql = f"SELECT {cols} FROM log JOIN node ON log.node_id = node.id"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY log.logged_at"
    rows = con.execute(sql, params).fetchall()

    if not rows:
        # provide a useful hint explaining why empty
        if args.id and not _node_exists(con, args.id):
            out(_c(f"(node #{args.id} does not exist)", "meta"))
        elif args.id:
            out(_c(f"(node #{args.id} has no logs in this window)", "meta"))
        else:
            hint = []
            if args.date:
                hint.append(f"on {args.date}")
            elif since:
                hint.append(f"since {since}")
                if args.until:
                    hint.append(f"until {args.until}")
            out(_c(f"(no logs {' '.join(hint)})", "meta"))
        return

    brief = _is_brief(args, "no_body")
    by_task = getattr(args, "by_task", False)
    # tail default 3 (aligns with wl day / wl tree); 0 = no expansion; --all-logs / large number = full expansion
    tail = _resolve_log_tail(args, brief, default_tail=3)

    if grouped:
        # date header -> bucket -> project -> task -> log (reuse day-view grouping)
        from collections import OrderedDict

        by_date = OrderedDict()
        for r in rows:
            by_date.setdefault(str(r["logged_at"])[:10], []).append(r)
        log_tail = tail  # reuse (default 3 / 0 when brief / None when --all-logs)
        for d, drows in by_date.items():
            out(_c(d, "header"))
            items = {}
            for r in drows:
                items.setdefault(r["node_id"], {"node": r, "logs": []})["logs"].append(r["body"])
            _render_day_group(con, items, by=getattr(args, "by", "project"),
                              sched_ids=_scheduled_node_ids(con, d), log_tail=log_tail,
                              full=_log_full(args))
            print()
        return

    if by_task:
        # aggregate by task: last N per task (default all)
        from collections import OrderedDict

        groups = OrderedDict()
        for r in rows:
            groups.setdefault(r["node_id"], {"title": r["title"], "rows": []})["rows"].append(r)
        for nid, g in groups.items():
            if tail is None:
                picks = g["rows"]
            elif tail <= 0:
                picks = []  # tail 0 = no expansion (same as brief 'header only')
            else:
                picks = g["rows"][-tail:]
            head = _c(f"#{nid}", "id") + " " + _c(f"'{g['title'][:60]}'")
            if tail is not None and len(g["rows"]) > tail:
                head += " " + _c(f"({len(g['rows'])} total, showing last {tail})", "meta")
            else:
                head += " " + _c(f"({len(g['rows'])} entries)", "meta")
            out(head)
            if brief:
                # brief + by_task: list all dates, no body (bypassing tail=0-truncated picks)
                dates = ", ".join(r["logged_at"][:10] for r in g["rows"])
                out("    " + _c(dates, "meta"))
                continue
            for r in picks:
                # --by-task indent "    [YYYY-MM-DD HH:MM:SS] " ~ 26 cols
                body = _truncate_log_body(r["body"], indent_cols=26, full=_log_full(args))
                out("    " + _c(f"[{r['logged_at']}]", "meta") + " " + _c(body))
        return

    # --tail N also works in --id single-task mode (consistent with --by-task tail)
    # without --by-task, tail directly slices the flat list tail, coordinating with _apply_top_limit
    raw_tail = getattr(args, "tail", None)
    if raw_tail is not None and raw_tail > 0 and len(rows) > raw_tail:
        omitted = len(rows) - raw_tail
        rows = rows[-raw_tail:]
        out(_c(f"… ({omitted} earlier elided, showing last {raw_tail}); use --all-logs or --limit 0 to see all", "meta"))
    elif raw_tail is None:
        rows, total = _apply_top_limit(rows, args)
        _print_truncation_hint(len(rows), total, extra="--limit 0 for all")
    for r in rows:
        lid = _c(f"#L{r['id']}", "id")
        if brief:
            out(_c(f"[{r['logged_at'][:10]}]", "meta") + " " + lid + " " + _c(f"#{r['node_id']}", "id") + " " + _c(f"{r['title'][:50]}"))
        else:
            # flat logs row "[YYYY-MM-DD HH:MM:SS] #L<id> #<node> 'title': <body>" prefix ~ 60 cols
            body = _truncate_log_body(r["body"], indent_cols=60, full=_log_full(args))
            out(_c(f"[{r['logged_at']}]", "meta") + " " + lid + " " + _c(f"#{r['node_id']}", "id") + " " + _c(f"'{r['title'][:30]}': {body}"))


# --- completion generator (argparse -> fish/bash/zsh) ---
# loaded via ~/.config/<shell>/<config> | source pattern; does not write a persistent file

# action attribute name -> fish helper function (dynamic completion)





# --- bash backend ---

# bash does not show descriptions, only completes tokens. helper is a bash function that emits a token list.



# --- zsh backend ---

def _show_one(args, con):
    n = con.execute("SELECT * FROM node WHERE id = ?", (args.id,)).fetchone()
    if not n:
        sys.exit(f"✗ node #{args.id} not found")
    out(_c(f"#{n['id']}", "id") + " " + _c(f"[{n['kind']}]", "kind") + " " + _c(n["title"], "header"))
    if n["status"]:
        st = _c(n["status"], _STATUS_STYLE.get(n["status"], "todo"))
        pr = (" " + _c(f"[#{n['priority']}]", _PRI_STYLE.get(n["priority"]))) if n["priority"] else ""
        out("  " + _c("status:", "meta") + "   " + st + pr)
    chain = _ancestors_chain(con, args.id)
    if len(chain) > 1:
        out("  " + _c("ancestors:", "meta") + " " + _c(" / ".join(f"#{p['id']} {p['title']}" for p in chain[:-1])))
    for k in ("created_at", "scheduled_at", "deadline_at", "closed_at"):
        if n[k]:
            out("  " + _c(f"{k:9s}", "meta") + " " + _c(n[k]))
    if n["body"]:
        out("  " + _c("body:", "meta") + "     " + _c(n["body"]))
    tags = _node_tags(con, args.id)
    if tags:
        out("  " + _c("tags:", "meta") + "     " + _c(f":{':'.join(tags)}:", "tag"))
    props = list(con.execute("SELECT key, value FROM prop WHERE node_id = ?", (args.id,)))
    if props:
        out("  " + _c("props:", "meta"))
        for r in props:
            out("    " + _c(f"{r['key']:12s} = {r['value']}"))
    links = [r["vault_doc"] for r in con.execute("SELECT vault_doc FROM link WHERE node_id = ?", (args.id,))]
    if links:
        out("  " + _c("links:", "meta") + "    " + _c(", ".join(f"[[{d}]]" for d in links)))
    # schedule (sched table): one-off dates + recurring rules. First-hand info for debugging
    # recurring tasks (e.g. why a task shows on multiple days); previously only visible via raw SQL.
    sched_rows = con.execute(
        "SELECT on_date, rrule FROM sched WHERE node_id = ? ORDER BY on_date NULLS LAST, rrule",
        (args.id,),
    ).fetchall()
    if sched_rows:
        dates = [r["on_date"] for r in sched_rows if r["on_date"]]
        rules = [r["rrule"] for r in sched_rows if r["rrule"]]
        parts = []
        if rules:
            parts.append("recur " + ", ".join(rules))
        if dates:
            parts.append("on " + ", ".join(dates))
        out("  " + _c("schedule:", "meta") + " " + _c("; ".join(parts), "planned"))
    # children (direct only)
    children = con.execute(
        f"SELECT * FROM node WHERE parent_id = ? {_ORDER_BY_PRI_ID}", (args.id,)
    ).fetchall()
    if children:
        out("  " + _c(f"children ({len(children)}):", "header"))
        for c in children:
            out(_node_line(con, c, indent="    "))

    # timeline / changes: created / scheduled / closed / each log (including CLOCK events), merged by time
    # brief / --no-timeline -> skip entire section; --timeline-tail N -> only the latest N
    brief = _is_brief(args, "no_timeline")
    if brief:
        return
    logs = list(con.execute("SELECT id, logged_at, body FROM log WHERE node_id = ? ORDER BY id", (args.id,)))
    # event tuple: (ts, kind_label, extra, log_id) -- log_id only for log events, meta events None
    events = []
    if n["created_at"]:
        events.append((n["created_at"], "● created", "", None))
    if n["scheduled_at"]:
        events.append((n["scheduled_at"], "◷ scheduled", "", None))
    if n["closed_at"]:
        events.append((n["closed_at"], f"✓ {n['status'] or 'closed'}", "", None))
    for r in logs:
        body = r["body"]
        if body.startswith("CLOCK_IN"):
            events.append((r["logged_at"], "⏱ clock-in", "", r["id"]))
        elif body.startswith("CLOCK_OUT"):
            import re as _re
            m = _re.search(r"elapsed=(\d+)min", body)
            events.append((r["logged_at"], "⏱ clock-out", f"({m.group(1)}min)" if m else "", r["id"]))
        else:
            # timeline log row: "    YYYY-MM-DD HH:MM:SS  #L<id>  ✎ log  <body>" indented ~ 32 cols
            head = _truncate_log_body(body, indent_cols=32, full=_log_full(args))
            events.append((r["logged_at"], "✎ log", head, r["id"]))
    if events:
        events.sort(key=lambda e: e[0])
        # tail: --no-timeline/brief=0 / --all-timelines=None full expansion / --timeline-tail N / default 5
        # default 5 (slightly more than wl day, since wl show is a detail command, but still elides middle)
        tail = _resolve_log_tail(args, brief=False, default_tail=5)
        shown = events if tail is None else (events[-tail:] if tail else [])
        title = f"timeline / changes ({len(events)})"
        if tail is not None and len(events) > tail:
            title += f", showing last {tail}"
        out("  " + _c(title + ":", "header"))
        if tail is not None and len(events) > tail:
            out("    " + _c(f"… ({len(events) - tail} earlier elided; use --all-timelines for full)", "meta"))
        # log.id used for operations (wl unlog #L<id>); meta events have no id, just a placeholder for alignment
        # prefix #L<id> mirrors node #123 with '#'; 'L' distinguishes (letter prefix = log, plain digits = node)
        for ts, kind, extra, lid in shown:
            lid_str = _c(f"#L{lid}", "id") if lid is not None else _c("     ", "meta")
            out("    " + _c(ts, "meta") + "  " + lid_str + "  " + _c(kind) + (f"  {_c(extra)}" if extra else ""))


_LS_SORT_SQL = {
    "pri": "priority NULLS LAST, id",
    "created": "created_at DESC, id DESC",     # like shell ls -t (newest first)
    "closed": "closed_at DESC NULLS LAST, id DESC",
    "scheduled": "scheduled_at DESC NULLS LAST, id DESC",
    "title": "title COLLATE NOCASE, id",
    "id": "id",
    # updated goes through a subquery, not here
}

