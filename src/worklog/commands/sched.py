"""worklog command: `wl sched` group (add/ls/rm) + recurrence-rule validation."""
from __future__ import annotations

from dataclasses import dataclass

from .. import timeutil as _tu
from .. import db_table as _db
from ..models import Sched
from ..queries import _check_ids_exist
from ..helpers import _resolve_concrete_date
from ..render import _c, die, dispatch_group, out
from .state import _ids_list
from .views import _WEEKDAY_NAMES, _split_until, _format_recurrence
from .output import output_format, TextRenderable, text_renderer


@dataclass
class SchedEntry:
    on_date: str | None
    recurrence: str | None


@dataclass
class SchedLsResult:
    node_id: int
    rows: list  # list[SchedEntry]


@text_renderer("sched_clear")
def _render_sched_clear(result):
    for item in result["nodes"]:
        out(_c(f"✓ #{item['node_id']} cleared {item['count']} schedule entries", "meta"))


@text_renderer("sched_query")
def _render_sched_query(result):
    for item in result:
        nid = item["node_id"]
        if item["on_date"] is None and item["recurrence"] is None:
            out(_c(f"#{nid} has no schedule", "meta"))
        else:
            disp = item["on_date"] or _format_recurrence(item["recurrence"])
            out("  " + _c(f"#{nid} @" + disp, "planned"))


@text_renderer("sched_write")
def _render_sched_write(result):
    for op in result["ops"]:
        if op["type"] == "recurrence":
            if op["exists"]:
                out(_c(f"= #{op['node_id']} already on recurring schedule: {op['rule']}", "meta"))
            else:
                out(_c(f"✓ #{op['node_id']} recurring schedule: {op['rule']}", "meta"))
        else:
            if op["exists"]:
                out(_c(f"= #{op['node_id']} already scheduled to {op['date']}", "meta"))
            else:
                out(_c(f"✓ #{op['node_id']} scheduled to {op['date']}", "meta"))


def _upsert_recurrence(con, nid, rule):
    """Idempotent recurrence write, keyed by BASE rule: any row whose base == `rule` already covers it —
    including a stopped one (`base;until=DATE`). Consolidate ALL such rows to one clean live rule:
    reactivate the first (clear its until) and drop duplicates, so re-adding a recurrence you'd
    stopped resumes it without leaving a stale/duplicate row behind. Returns True if it already
    existed unchanged. Single source for `wl sched --recur` and apply's `recur` field-op. No commit."""
    matches = [r for r in Sched.query(con, node_id=nid) if r.recurrence and _split_until(r.recurrence)[0] == rule]
    if not matches:
        Sched.insert(con, {"node_id": nid, "recurrence": rule, "created_at": _tu.utc_now()})
        return False
    already = len(matches) == 1 and matches[0].recurrence == rule
    if matches[0].recurrence != rule:
        Sched.update(con, matches[0].id, {"recurrence": rule})   # reactivate
    for extra in matches[1:]:
        Sched.delete(con, id=extra.id)                      # drop duplicate base rows
    return already


@output_format
def cmd_sched(args, con):
    """Forward planning: schedule a task to a specific day / recurrence. A scheduled task appears as 'planned' in wl day even with no log.
    Accepts multiple ids: wl sched 18 19 20 today (first N are ids; the trailing positional is the date)."""
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    if args.clear:
        nodes = []
        total = 0
        for nid in ids:
            n = Sched.delete(con, node_id=nid)
            total += n
            nodes.append({"node_id": nid, "count": n})
        con.commit()
        return TextRenderable({"cleared": total, "nodes": nodes}, cmd_name="sched_clear")
    if not args.when and not args.recur:
        result = []
        for nid in ids:
            rows = Sched.query(con, node_id=nid, order="on_date NULLS LAST, recurrence")
            if not rows:
                result.append({"node_id": nid, "on_date": None, "recurrence": None})
            for r in rows:
                result.append({"node_id": nid, "on_date": r.on_date, "recurrence": r.recurrence})
        return TextRenderable(result, cmd_name="sched_query")
    ops = []
    if args.recur:
        try:
            rule = _normalize_recurrence(args.recur)
        except ValueError as e:
            die(f"{e}")
        for nid in ids:
            already = _upsert_recurrence(con, nid, rule)
            ops.append({"type": "recurrence", "node_id": nid, "rule": rule, "exists": already})
        con.commit()
    if args.when:
        try:
            d = _resolve_concrete_date(args.when)
        except ValueError:
            die(f"invalid date '{args.when}' — sched takes a concrete day: YYYY-MM-DD / "
                     f"today / tomorrow / day-after-tomorrow / +1 / -2w / next-week / next-month / "
                     f"next-quarter (resolved to the period's first day). For 'someday' use `wl defer`.")
        for nid in ids:
            # idempotent: don't insert a duplicate (node_id, on_date) row
            exists = Sched.exists(con, node_id=nid, on_date=d)
            ops.append({"type": "on_date", "node_id": nid, "date": d, "exists": exists})
            if not exists:
                Sched.insert(con, {"node_id": nid, "on_date": d, "created_at": _tu.utc_now()})
        con.commit()
    # return updated schedule list for the first (usually only) id
    nid = ids[0]
    rows = _db.query(con, "sched", cols="on_date, recurrence", node_id=nid,
                     order="on_date NULLS LAST, recurrence")
    schedule = [{"on_date": r["on_date"], "recurrence": r["recurrence"]} for r in rows]
    return TextRenderable({"ops": ops, "schedule": schedule}, cmd_name="sched_write")


@text_renderer("sched_ls")
def _render_sched_ls(result: SchedLsResult):
    if not result.rows:
        out(_c(f"#{result.node_id} has no schedule", "meta"))
    else:
        for item in result.rows:
            disp = item.on_date or _format_recurrence(item.recurrence)
            out("  " + _c(f"#{result.node_id} @" + disp, "planned"))


@output_format
def cmd_sched_ls(args, con):
    """List a node's schedule entries — the read verb of the sched group (= bare `wl sched
    <id>`). Each row is a one-off `on_date` or a recurring `recurrence`."""
    _check_ids_exist(con, [args.id])
    rows = _db.query(con, "sched", cols="on_date, recurrence", node_id=args.id,
                     order="on_date NULLS LAST, recurrence")
    nid = args.id
    result = SchedLsResult(
        node_id=nid,
        rows=[SchedEntry(on_date=r["on_date"], recurrence=r["recurrence"]) for r in rows],
    )
    return TextRenderable(result, cmd_name="sched_ls")


@text_renderer("sched_rm")
def _render_sched_rm(result):
    out(_c(f"✓ #{result['node_id']} cleared {result['cleared']} schedule entries", "meta"))


@output_format
def cmd_sched_rm(args, con):
    """Clear a node's schedule entries — the delete verb of the sched group (= `wl sched
    <id> --clear`). Removes every one-off and recurring entry for the node."""
    _check_ids_exist(con, [args.id])
    n = Sched.delete(con, node_id=args.id)
    con.commit()
    return TextRenderable({"node_id": args.id, "cleared": n}, cmd_name="sched_rm")


@text_renderer("sched_stop")
def _render_sched_stop(result):
    for base in result["stopped"]:
        out(_c(f"✓ #{result['node_id']} recurrence stopped: {base} (fires through {result['date']} inclusive, then no more)", "meta"))


@output_format
def cmd_sched_stop(args, con):
    """Stop a recurrence: it fires up to and INCLUDING <date> (default today), then no more.
    Past occurrences stay intact (unlike --clear/rm, which erase the rule from history). Encodes
    an inclusive `;until=<date>` suffix on the recurrence. Multiple rules → all, unless --rule names one."""
    _check_ids_exist(con, [args.id])
    try:
        end = _resolve_concrete_date(args.date) if args.date else _tu.today()
    except ValueError:
        die(f"invalid date '{args.date}' — stop takes a concrete day (YYYY-MM-DD / today / +1 / -2w / …)")
    # --rule may be pasted from output in either the internal `base;until=DATE` form or the display
    # form `base (stopped DATE)` — normalize both down to the base before comparing.
    want = _split_until((args.rule or "").split(" (stopped ")[0])[0] if args.rule else None
    rows = [r for r in Sched.query(con, node_id=args.id) if r.recurrence]
    if not rows:
        die(f"#{args.id} has no recurring schedule to stop")
    stopped = []
    for r in rows:
        base, _ = _split_until(r.recurrence)
        if want and base != want:
            continue
        Sched.update(con, r.id, {"recurrence": f"{base};until={end}"})
        stopped.append(base)
    if not stopped:
        die(f"#{args.id} has no recurrence matching --rule {args.rule!r}")
    con.commit()
    return TextRenderable({"node_id": args.id, "date": end, "stopped": stopped}, cmd_name="sched_stop")


def cmd_sched_group(args, con):
    """Dispatch `wl sched <add|ls|rm|stop>` (the metric-style entity group).
    `add` is the default verb (`wl sched <id> <when>` == `wl sched add <id> <when>`) and
    keeps the full when / --recur / --clear / list-when-empty grammar (`cmd_sched`); `ls`
    lists, `rm` clears, `stop` ends a recurrence keeping history. `wl defer` stays its own command."""
    return dispatch_group(args, con, "sched_sub",
        {"add": cmd_sched, "ls": cmd_sched_ls, "rm": cmd_sched_rm, "stop": cmd_sched_stop},
        usage="usage: wl sched <id> <when>  |  wl sched <add|ls|rm|stop> … (see `wl sched --help`)")


def _normalize_recurrence(s):
    """Validate / normalize a recurrence rule:
    - daily
    - weekly:Mon,Wed,Fri | 1-7 | -1..-7 (1=Mon..7=Sun, -1=Sun..-7=Mon)
    - monthly:5 / 5,15 / -1 (last day of month)
    - quarterly:M-D / -1 (last day of quarter): M in 1-3 = month offset within the quarter
    - yearly:03-21 / -1 (last day of year): MM-DD
    """
    import re as _re
    rule = s.strip()
    if rule == "daily":
        return "daily"
    if rule.startswith("weekly:"):
        raw = [x.strip() for x in rule[len("weekly:"):].split(",") if x.strip()]
        if not raw:
            raise ValueError("weekly rule needs at least 1 weekday (Mon/Tue/.../Sun or 1-7 / -1..-7)")
        norm_days = []
        for tok in raw:
            cap = tok.capitalize()
            if cap in _WEEKDAY_NAMES:
                norm_days.append(cap)
                continue
            try:
                n = int(tok)
            except ValueError:
                raise ValueError(f"invalid weekly day '{tok}' (use Mon..Sun or 1-7 / -1..-7)")
            if n > 0 and 1 <= n <= 7:
                norm_days.append(_WEEKDAY_NAMES[n - 1])
            elif n < 0 and -7 <= n <= -1:
                norm_days.append(_WEEKDAY_NAMES[7 + n])  # -1 -> 6 (Sun), -7 -> 0 (Mon)
            else:
                raise ValueError(f"weekly number '{n}' out of range (allowed 1-7 or -1..-7)")
        # dedup preserve order
        seen = set()
        deduped = [d for d in norm_days if not (d in seen or seen.add(d))]
        return "weekly:" + ",".join(deduped)
    if rule.startswith("monthly:"):
        tokens = [x.strip() for x in rule[len("monthly:"):].split(",") if x.strip()]
        if not tokens:
            raise ValueError("monthly rule needs at least 1 day (e.g. monthly:5 / monthly:1,15 / monthly:-1)")
        norm = []
        for tok in tokens:
            try:
                n = int(tok)
            except ValueError:
                raise ValueError(f"monthly day must be an integer: '{tok}' (positive 1-31 / negative -1..-28 from month-end)")
            if n == 0 or not (-28 <= n <= 31):
                raise ValueError(f"monthly day '{n}' out of range (allowed 1-31 or -1..-28)")
            norm.append(str(n))
        return "monthly:" + ",".join(norm)
    if rule.startswith("quarterly:"):
        tokens = [x.strip() for x in rule[len("quarterly:"):].split(",") if x.strip()]
        if not tokens:
            raise ValueError("quarterly rule needs at least 1 M-D or -1 (e.g. quarterly:1-15 / quarterly:-1)")
        for tok in tokens:
            if tok == "-1":
                continue
            if not _re.fullmatch(r"\d{1,2}-\d{1,2}", tok):
                raise ValueError(f"invalid quarterly '{tok}' (expected M-D, M in 1-3 month-in-quarter; or -1 quarter end)")
            mm, dd = (int(x) for x in tok.split("-"))
            if not (1 <= mm <= 3):
                raise ValueError(f"quarterly '{tok}' month offset out of range (1=Q-start / 2=mid / 3=Q-end)")
            if not (1 <= dd <= 31):
                raise ValueError(f"quarterly '{tok}' day out of range (1-31)")
        return "quarterly:" + ",".join(tokens)
    if rule.startswith("yearly:"):
        tokens = [x.strip() for x in rule[len("yearly:"):].split(",") if x.strip()]
        if not tokens:
            raise ValueError("yearly rule needs at least 1 MM-DD or -1 (e.g. yearly:03-21 / yearly:-1)")
        for tok in tokens:
            if tok == "-1":
                continue
            if not _re.fullmatch(r"\d{2}-\d{2}", tok):
                raise ValueError(f"invalid yearly '{tok}' (expected MM-DD like 03-21; or -1 year end)")
            mm, dd = int(tok[:2]), int(tok[3:])
            if not (1 <= mm <= 12 and 1 <= dd <= 31):
                raise ValueError(f"yearly '{tok}' out of range (month 1-12 / day 1-31)")
        return "yearly:" + ",".join(tokens)
    raise ValueError(f"unknown recurrence rule '{s}' (supports daily / weekly / monthly / quarterly / yearly, each accepting -1 = end of cycle)")
