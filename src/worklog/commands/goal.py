"""worklog commands: `wl goal` group (goal/set/ls/rm) + `wl recap`.

`goal` (forward) and `summary` (backward recap) are the two reserved-tag logs (`log.tag`):
history-preserving (each write appends, latest = current), a distinct store from props. A goal
is the same `goal` tag at every time level — the node's kind (day/week/month/year) is the level.
A goal can carry structured target node ids (priority order), stored as `goal` metrics."""
from __future__ import annotations

import re
import sys

from .. import timeutil as _tu
from .. import db_table as _db
from ..queries import (
    _check_ids_exist,
    _node_exists,
    _latest_typed_log,
    _set_typed_log,
    _RESERVED_LOG_TAGS,
)
from ..helpers import _resolve_concrete_date
from ..render import _c, out
from .timenodes import _ensure_day, _ensure_today_day
from .views import _goal_progress, _emit_goal_targets


_GOAL_ID_MENTION = re.compile(r"(?:WL)?#(\d+)")


def _mentioned_goal_ids(con, body, already):
    """Live node ids NAMED in a goal's prose (`#42` / `WL#42`) that aren't already supplied as
    structured targets — for a *hint only* (we never parse them into storage; that's too fragile).
    Skips `PR#42` / `ABC#42` (a `#` glued to an alnum char) and dead ids. Order-preserving, deduped."""
    found, seen = [], set()
    excl = set(already or ())
    for m in _GOAL_ID_MENTION.finditer(body or ""):
        if m.start() > 0 and body[m.start() - 1].isalnum():
            continue   # PR#42 / ABC#42 — not a node ref
        i = int(m.group(1))
        if i in seen or i in excl:
            continue
        seen.add(i)
        if _node_exists(con, i):
            found.append(i)
    return found


def _set_goal_targets(con, node_id, ids):
    """SET a node's CURRENT (latest) goal's structured targets to exactly `ids` (priority order) —
    the 'I wrote the whole goal as text, now attach its ids' shortcut: no new log, no re-typing.
    REPLACES any existing targets on that goal log (the text is the complete goal, so the ids are
    the complete set, not an append). Errors if the node has no goal yet."""
    row = _db.query_one(con, "log", cols="id", node_id=node_id, tag="goal", order="id DESC")
    if not row:
        sys.exit(f'✗ #{node_id} has no goal yet — write one first (wl goal "...")')
    _check_ids_exist(con, ids)
    want = []
    for i in ids:        # dedupe, preserve priority order
        if i not in want:
            want.append(i)
    current = [int(r["value_num"]) for r in _db.query(con, "metric", cols="value_num",
               log_id=row["id"], tag="goal") if r["value_num"] is not None]
    shown = " ".join("#" + str(i) for i in want)
    if current == want:
        out(_c(f"= #{node_id} goal targets already {shown}", "meta"))
        return
    for r in _db.query(con, "metric", cols="id", log_id=row["id"], tag="goal"):
        _db.delete(con, "metric", id=r["id"])   # replace: clear the old set first
    for i in want:
        _db.insert(con, "metric", {"log_id": row["id"], "node_id": node_id, "tag": "goal",
                   "value_num": i, "at": _tu.utc_now()})
    con.commit()
    out(_c(f"✓ #{node_id} goal targets: {shown}", "meta"))


def _goal_id_hint(con, body, already, set_stem, full_stem):
    """After writing a goal, nudge toward structured target nodes (so every goal links to the
    tasks it delivers). `already` is the structured ids set THIS write (empty if none).
      • prose NAMES live ids not yet structured → offer two ready-to-run lines: `set_stem` sets
        them on the goal now (no re-typing), `full_stem` is the one-shot form for next time.
      • goal ended up with NO targets at all → a one-line nudge with the `--ids` command template.
    No-op only when the goal already has structured targets."""
    ids = _mentioned_goal_ids(con, body, already)
    if ids:
        shown = " ".join(f"#{i}" for i in ids)
        tail = " ".join(str(i) for i in ids)
        out(_c(f"  💡 {shown} in the text aren't structured targets yet:", "meta"))
        out(f"     set ids:   {set_stem} {tail}")
        out(f"     next time: {full_stem} {tail}")
    elif not already:
        out(_c(f"  💡 no target nodes — link the goal to the tasks it delivers "
               f"(priority order): {set_stem} <id…>", "meta"))


def cmd_goal(args, con):
    """The default (bare) form of `wl goal`: read/write TODAY's goal. `wl goal` reads; `wl goal
    'text' [ids]` writes (today's day-node auto-created). Stored as a tag=goal log (history-
    preserving): each write appends, latest is current. Trailing ids (priority order) are the
    goal's target nodes, stored as `goal` metrics. `wl goal set/ls/rm` reach any node."""
    nid = _ensure_today_day(con)
    text = getattr(args, "text", None)
    if not text:
        row = _latest_typed_log(con, nid, "goal")
        if not (row and row["body"]):
            out(_c("(no goal set for today)", "meta"))
            return
        # read view: goal text + [done/total] + the structured targets (same render as wl day)
        out(row["body"] + _c(_goal_progress(con, nid, row["body"]), "meta"))
        _emit_goal_targets(con, nid)
        return
    goals = getattr(args, "goals", None) or []
    if goals:
        _check_ids_exist(con, goals)
    _set_typed_log(con, nid, "goal", text, goals=goals)
    con.commit()
    extra = ("  [" + ", ".join(f"#{i}" for i in goals) + "]") if goals else ""
    out(_c(f"✓ today's goal: {text}{extra}", "meta"))
    _goal_id_hint(con, text, goals, f"wl goal set {nid} --ids", f'wl goal "{text}"')


def cmd_summary_prop(args, con):
    """`wl recap` — read/write a day's end-of-day summary (default today; --date for a past day).
    Stored as a tag=summary log; each write appends a new log (history kept), and the log's
    own logged_at is the 'written at' time used to detect changes added after the recap."""
    from datetime import datetime as _dt
    target = getattr(args, "date", None)
    if target:
        try:
            iso = _resolve_concrete_date(target)
        except ValueError as e:
            sys.exit(f"✗ bad --date '{target}': {e}")
        d = _dt.strptime(iso, "%Y-%m-%d").date()
        label = iso
    else:
        d = _tu.today_date()
        label = "today"
    nid = _ensure_day(con, d)
    if not args.text:
        row = _latest_typed_log(con, nid, "summary")
        if not row or not row["body"]:
            out(_c(f"(no summary set for {label})", "meta"))
            return
        at = row["logged_at"]
        out(row["body"] + (_c(f"  (written at {at})", "meta") if at else ""))
        return
    log_id = _set_typed_log(con, nid, "summary", args.text)
    con.commit()
    at = _db.get(con, "log", log_id)["logged_at"]
    out(_c(f"✓ {label}'s summary (written at {at}): {args.text}", "meta"))


# --- goal group: set / ls / rm for the reserved-tag logs (goal / summary) on ANY node.
# `wl set <node> <key>` routes here as a documented shortcut (parallel to `wl set` → `wl prop
# set`); bare `wl goal` / `wl recap` are the today-auto shortcuts. ---
def cmd_goal_set(args, con):
    """Set a goal (or summary, via --summary) on a node — the create/update verb of the goal
    group. Each write appends a reserved-tag log (history kept; latest is current). `--ids`
    instead sets the existing goal's structured targets. Also reachable as `wl set <node> <key>`."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    set_ids = getattr(args, "ids", None)
    if set_ids:                   # set the node's existing goal targets (no new log, no text)
        if args.value:
            sys.exit("✗ give a goal text OR --ids <ids>, not both")
        _set_goal_targets(con, args.id, set_ids)
        return
    if not args.value:
        sys.exit('✗ need a goal/summary text (or --ids <ids> to set targets on the current goal)')
    field = "summary" if getattr(args, "summary", False) else "goal"
    goals = getattr(args, "goals", None) or []
    if goals:
        if field != "goal":
            sys.exit("✗ target node ids only apply to a goal, not --summary")
        _check_ids_exist(con, goals)
    log_id = _set_typed_log(con, args.id, field, args.value, goals=goals)
    con.commit()
    at = _db.get(con, "log", log_id)["logged_at"]
    extra = ("  [" + ", ".join(f"#{i}" for i in goals) + "]") if goals else ""
    out(_c(f"✓ #{args.id} {field} (logged at {at}): {args.value}{extra}", "meta"))
    if field == "goal":
        _goal_id_hint(con, args.value, goals,
                      f"wl goal set {args.id} --ids", f'wl goal set {args.id} "{args.value}"')


def cmd_goal_ls(args, con):
    """List a node's reserved-tag logs (current goal / summary) — the read verb of the goal group.
    Each shows its latest typed log (the current value)."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    shown = False
    for field in _RESERVED_LOG_TAGS:
        row = _latest_typed_log(con, args.id, field)
        if row and row["body"]:
            shown = True
            out(_c(f"  #{args.id} {field}", "id") + _c(": ", "meta") + row["body"])
            if field == "goal":          # list the goal's structured targets, same as wl day
                _emit_goal_targets(con, args.id, indent="       ")
    if not shown:
        out(_c(f"#{args.id} has no goal / summary", "meta"))


def cmd_goal_rm(args, con):
    """Clear a node's goal (or summary, via --summary) — the delete verb of the goal group.
    Soft-deletes the field's typed logs (reversible). Also reachable as `wl unset <node> <key>`."""
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    field = "summary" if getattr(args, "summary", False) else "goal"
    n = _db.delete(con, "log", node_id=args.id, tag=field)
    con.commit()
    out(_c(f"✓ #{args.id} {field} cleared ({n} log(s))" if n
           else f"(#{args.id} has no {field})", "meta"))


def cmd_goal_group(args, con):
    """Dispatch `wl goal` — bare (or the `today` default verb) reads/writes today's goal via
    cmd_goal; `set`/`ls`/`rm` reach any node's reserved-tag logs (goal / summary)."""
    sub = getattr(args, "goal_sub", None)
    if sub in (None, "today"):
        return cmd_goal(args, con)
    {"set": cmd_goal_set, "ls": cmd_goal_ls, "rm": cmd_goal_rm}[sub](args, con)
