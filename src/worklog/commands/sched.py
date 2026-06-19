"""worklog command: `wl sched` group (add/ls/rm) + recurrence-rule validation."""
from __future__ import annotations


from .. import timeutil as _tu
from .. import db_table as _db
from ..queries import _check_ids_exist
from ..helpers import _resolve_concrete_date
from ..render import _c, die, out
from .state import _ids_list
from .views import _WEEKDAY_ABBR
from .output import output_format


@output_format
def cmd_sched(args, con):
    """Forward planning: schedule a task to a specific day / recurrence. A scheduled task appears as 'planned' in wl day even with no log.
    Accepts multiple ids: wl sched 18 19 20 today (first N are ids; the trailing positional is the date)."""
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    if args.clear:
        total = 0
        for nid in ids:
            n = _db.delete(con, "sched", node_id=nid)
            total += n
            out(_c(f"✓ #{nid} cleared {n} schedule entries", "meta"))
        con.commit()
        return {"cleared": total}
    if not args.when and not args.recur:
        result = []
        for nid in ids:
            rows = _db.query(con, "sched", cols="on_date, rrule", node_id=nid, order="on_date NULLS LAST, rrule")
            if not rows:
                out(_c(f"#{nid} has no schedule", "meta"))
            for r in rows:
                out("  " + _c(f"#{nid} @" + (r["on_date"] or r["rrule"]), "planned"))
                result.append({"node_id": nid, "on_date": r["on_date"], "rrule": r["rrule"]})
        return result
    if args.recur:
        try:
            rule = _norm_rrule(args.recur)
        except ValueError as e:
            die(f"{e}")
        for nid in ids:
            # idempotent: don't insert a duplicate (node_id, rrule) row
            exists = _db.exists(con, "sched", node_id=nid, rrule=rule)
            if exists:
                out(_c(f"= #{nid} already on recurring schedule: {rule}", "meta"))
            else:
                out(_c(f"✓ #{nid} recurring schedule: {rule}", "meta"))
            if not exists:
                _db.insert(con, "sched", {"node_id": nid, "rrule": rule, "created_at": _tu.utc_now()})
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
            exists = _db.exists(con, "sched", node_id=nid, on_date=d)
            if exists:
                out(_c(f"= #{nid} already scheduled to {d}", "meta"))
            else:
                out(_c(f"✓ #{nid} scheduled to {d}", "meta"))
            if not exists:
                _db.insert(con, "sched", {"node_id": nid, "on_date": d, "created_at": _tu.utc_now()})
        con.commit()
    # return updated schedule list for the first (usually only) id
    nid = ids[0]
    rows = _db.query(con, "sched", cols="on_date, rrule", node_id=nid,
                     order="on_date NULLS LAST, rrule")
    return [{"on_date": r["on_date"], "rrule": r["rrule"]} for r in rows]


@output_format
def cmd_sched_ls(args, con):
    """List a node's schedule entries — the read verb of the sched group (= bare `wl sched
    <id>`). Each row is a one-off `on_date` or a recurring `rrule`."""
    _check_ids_exist(con, [args.id])
    rows = _db.query(con, "sched", cols="on_date, rrule", node_id=args.id,
                     order="on_date NULLS LAST, rrule")
    if not rows:
        out(_c(f"#{args.id} has no schedule", "meta"))
    else:
        for r in rows:
            out("  " + _c(f"#{args.id} @" + (r["on_date"] or r["rrule"]), "planned"))
    return [{"on_date": r["on_date"], "rrule": r["rrule"]} for r in rows]


@output_format
def cmd_sched_rm(args, con):
    """Clear a node's schedule entries — the delete verb of the sched group (= `wl sched
    <id> --clear`). Removes every one-off and recurring entry for the node."""
    _check_ids_exist(con, [args.id])
    n = _db.delete(con, "sched", node_id=args.id)
    con.commit()
    out(_c(f"✓ #{args.id} cleared {n} schedule entries", "meta"))
    return {"cleared": n}


def cmd_sched_group(args, con):
    """Dispatch `wl sched <add|ls|rm>` (the metric-style entity group).
    `add` is the default verb (`wl sched <id> <when>` == `wl sched add <id> <when>`) and
    keeps the full when / --recur / --clear / list-when-empty grammar (`cmd_sched`); `ls`
    lists, `rm` clears. `wl defer` (status=LATER + rough hint) stays its own command."""
    sub = getattr(args, "sched_sub", None)
    if sub is None:
        die("usage: wl sched <id> <when>  |  wl sched <add|ls|rm> … (see `wl sched --help`)")
    {"add": cmd_sched, "ls": cmd_sched_ls, "rm": cmd_sched_rm}[sub](args, con)


def _norm_rrule(s):
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
            if cap in _WEEKDAY_ABBR:
                norm_days.append(cap)
                continue
            try:
                n = int(tok)
            except ValueError:
                raise ValueError(f"invalid weekly day '{tok}' (use Mon..Sun or 1-7 / -1..-7)")
            if n > 0 and 1 <= n <= 7:
                norm_days.append(_WEEKDAY_ABBR[n - 1])
            elif n < 0 and -7 <= n <= -1:
                norm_days.append(_WEEKDAY_ABBR[7 + n])  # -1 -> 6 (Sun), -7 -> 0 (Mon)
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
