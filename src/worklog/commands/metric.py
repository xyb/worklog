"""worklog commands: metric group — CRUD for structured datapoints.

A `metric` is a structured datapoint (check-in / number / measurement) that
hangs off a `log` (node → log → metric). These commands are the primary,
single-purpose CRUD surface: `wl metric add / ls / edit / rm`. A full CRUD on
purpose — the project's lesson is to never ship "create but no delete".

`wl metric add` without --on-log creates a carrier log on the node, tagged
`log.tag='metric'` so it's distinguishable from a user-written note (used for
folding and for cleaning it up on `rm`). A pure marker (no value, e.g. a
check-in) is stored as value_num=1 to satisfy the table CHECK without freezing
any reserved tag name into the schema.

`wl metric add` does NOT promote a node TODO→DOING the way `wl log` does: a
measurement is data capture, not "working on the task". (Revisit if a real
workflow wants it; a --keep-status-style escape would go here.)
"""
from __future__ import annotations

import math
import re
import sys

from .. import timeutil as _tu
from .. import db_table as _db
from ..helpers import _resolve_concrete_date, _resolve_window
from ..queries import _node_exists, _has_checkin
from ..render import _c, out

_CARRIER_TYPE = "metric"  # log.tag marking an auto-created metric carrier log
CHECKIN_TAG = "checkin"   # reserved metric tag: the structured "done today" signal


def _metric_id_arg(s):
    """argparse type: accept '#M12' / 'M12' / '12' (wl metric ls displays #M<id>)."""
    t = s.lstrip("#")
    return int(t[1:] if t.lower().startswith("m") else t)


def _resolve_at(s):
    """Resolve --at into a stored value: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'.
    The date token accepts keywords (today / yesterday / …) via _resolve_concrete_date."""
    s = s.strip()
    parts = s.split(None, 1)
    try:
        d = _resolve_concrete_date(parts[0])
    except ValueError:
        sys.exit(f"✗ invalid --at date '{parts[0]}'")
    if len(parts) == 2:
        t = parts[1].strip()
        if not re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", t):
            sys.exit(f"✗ invalid --at time '{t}' (expected HH:MM or HH:MM:SS)")
        if t.count(":") == 1:
            t += ":00"
        # a date+time is a local instant -> store UTC; a bare date stays literal
        return _tu.local_to_utc(f"{d} {t}")
    return d


def _parse_value(value, force_text):
    """(value_num, value_text) from a positional value string. force_text keeps it
    textual. Non-finite floats (inf/nan) are NOT treated as numeric (they would
    crash numeric rendering) — they fall through to text."""
    if value is None:
        return None, None
    if not force_text:
        try:
            f = float(value)
            if math.isfinite(f):
                return f, None
        except ValueError:
            pass
    return None, value


def _clean_text(value_text):
    """Trim a text value; reject empty/whitespace-only (a semantically empty datapoint)."""
    if value_text is None:
        return None
    t = value_text.strip()
    if not t:
        sys.exit("✗ value cannot be empty (omit the value entirely for a marker)")
    return t


def _fmt_num(n):
    """Drop a trailing .0 so 1.0 → '1', 5.4 → '5.4'."""
    return str(int(n)) if float(n) == int(n) else f"{n:g}"


def _fmt_value(row):
    """Human display of a metric's value + unit."""
    if row["value_num"] is not None:
        v = _fmt_num(row["value_num"])
        return f"{v} {row['unit']}".rstrip() if row["unit"] else v
    if row["value_text"] is not None:
        return row["value_text"]
    return ""


def _line(row):
    line = (_c(f"#M{row['id']}", "id") + " " + _c(f"[{row['tag']}]", "planned")
            + f" {_fmt_value(row)}".rstrip() + _c(f"  @{_tu.utc_to_local(row['at'])}", "meta"))
    if row["note"]:
        line += _c(f"  — {row['note']}", "meta")
    line += _c(f"  ⟶ #L{row['log_id']}", "meta")
    return line


def _insert_metric_on_log(con, log_id, node_id, tag, value, *,
                          force_text=False, unit=None, note=None, at=None):
    """Insert ONE metric onto an existing log (no commit; caller controls the txn).
    Shared by `wl metric add` and the `--metric` helper on `wl log`/`wl add`.
    Value parsing, the marker (value_num=1) rule, empty-value rejection, and
    unit-only-for-numeric all live here so every entry point behaves the same.
    Returns the new metric id."""
    tag = (tag or "").strip()
    if not tag:
        sys.exit("✗ metric tag cannot be empty")
    vnum, vtext = _parse_value(value, force_text)
    vtext = _clean_text(vtext)
    if vnum is None and vtext is None:
        vnum = 1.0  # pure marker — satisfies CHECK, no reserved tag frozen into schema
    u = unit if vnum is not None else None  # unit only meaningful on a numeric value
    # explicit UTC "now" when no time given, so we never fall back to the localtime
    # column DEFAULT; a caller-supplied `at` is already UTC (or a bare date)
    return _db.insert(con, "metric", {
        "log_id": log_id, "node_id": node_id, "tag": tag,
        "value_num": vnum, "value_text": vtext, "unit": u, "note": note,
        "at": at if at else _tu.utc_now(),
    })


def _parse_metric_spec(s):
    """A `--metric` spec is 'tag [value] [unit]' (whitespace-separated):
    'glucose 5.4 mmol/L' / 'pullups 8' / 'checkin'. → (tag, value, unit)."""
    parts = (s or "").split()
    if not parts:
        sys.exit("✗ --metric spec is empty (expected 'tag [value] [unit]')")
    return parts[0], (parts[1] if len(parts) > 1 else None), (parts[2] if len(parts) > 2 else None)


def import_metric(con, log_id, node_id, mspec, *, default_at=None):
    """Insert one metric from a bulk-import/apply spec dict on an existing log.
    Spec: {tag (required), value?, unit?, note?, at?}. value may be a JSON number
    or string (autodetected numeric vs text); omit it for a marker. `at` falls back
    to default_at (typically the carrier log's timestamp). No commit."""
    if not isinstance(mspec, dict) or not str(mspec.get("tag") or "").strip():
        sys.exit("✗ metric spec must be an object with a non-empty 'tag'")
    return _insert_metric_on_log(
        con, log_id, node_id, str(mspec["tag"]), mspec.get("value"),
        unit=mspec.get("unit"), note=mspec.get("note"),
        at=mspec.get("at") or default_at,
    )


def checkin_metric(con, log_id, node_id, day):
    """Attach a check-in marker metric to a log, idempotent per (node, day): if the
    node already has a check-in that day, do nothing. Returns True if one was added.
    Used by `wl tick` / `wl checkin` so 'done today' is a structured signal, not
    'some log exists'. No commit — caller controls the transaction."""
    if _has_checkin(con, node_id, day):
        return False
    _insert_metric_on_log(con, log_id, node_id, CHECKIN_TAG, None)  # marker → value_num=1
    return True


def attach_metric_specs(con, log_id, node_id, specs, *, at=None):
    """Attach each `--metric` spec to a log (no commit; caller commits). Returns count."""
    n = 0
    for spec in specs or []:
        tag, value, unit = _parse_metric_spec(spec)
        _insert_metric_on_log(con, log_id, node_id, tag, value, unit=unit, at=at)
        n += 1
    return n


def cmd_metric_add(args, con):
    """Attach a structured datapoint to a node (via a carrier log)."""
    node = args.node
    if not _node_exists(con, node):
        sys.exit(f"✗ node #{node} not found")
    if not (args.tag or "").strip():
        sys.exit("✗ metric tag cannot be empty")
    at = _resolve_at(args.at) if args.at else None

    # carrier log: an existing one (--on-log, must belong to the node, not CLOCK) or a new one.
    if args.on_log is not None:
        log = _db.get(con, "log", args.on_log)
        if not log:
            sys.exit(f"✗ log #L{args.on_log} not found")
        if log["node_id"] != node:
            sys.exit(f"✗ log #L{args.on_log} belongs to node #{log['node_id']}, not #{node}")
        log_id = args.on_log
        if at is None:
            at = log["logged_at"]  # inherit the existing log's time, not "now"
    else:
        log_id = _db.insert(con, "log", {
            "node_id": node, "logged_at": at or _tu.utc_now(),
            "body": args.body or "", "tag": _CARRIER_TYPE,
        })

    # carrier-log INSERT + metric INSERT are one unit of work; keep them atomic so
    # a failed metric INSERT can't leave an orphan carrier log.
    try:
        mid = _insert_metric_on_log(con, log_id, node, args.tag, args.value,
                                    force_text=args.text, unit=args.unit, note=args.note, at=at)
        con.commit()
    except Exception:
        con.rollback()
        raise
    out(_c("✓", "done") + " " + _line(_db.get(con, "metric", mid)))


def cmd_metric_ls(args, con):
    """List a node's metrics (default = this week; --all for everything; --tag filter)."""
    node = args.node
    if not _node_exists(con, node):
        sys.exit(f"✗ node #{node} not found")
    simple = {"node_id": node}
    if args.tag:
        simple["tag"] = args.tag.strip()
    where, params = _db.clause(**simple)
    if not args.all:
        since, until = _resolve_window(args)  # shared window: --since/--until/--week/--month
        where.append(f"{_tu.local_day_sql('at')} BETWEEN ? AND ?")
        params += [since, until]
    # ORDER BY at: a date-only `at` (no time given) sorts at the start of its day
    # — "no time" treated as day-start, consistent with the log.logged_at convention.
    rows = list(con.execute(
        f"SELECT * FROM metric WHERE {' AND '.join(where)} ORDER BY at, id", params))
    if not rows:
        filt = f" tag={args.tag}" if args.tag else ""
        scope = "" if args.all else " in window (use --all / --week / --month)"
        out(_c(f"(node #{node} has no metrics{filt}{scope})", "meta"))
        return
    for r in rows:
        out(_line(r))


def cmd_metric_edit(args, con):
    """Edit fields of a single metric."""
    mid = args.metric_id
    row = _db.get(con, "metric", mid)
    if not row:
        sys.exit(f"✗ metric #M{mid} not found")

    if sum(x is not None for x in (args.value, args.num, args.text)) > 1:
        sys.exit("✗ --value / --num / --text are mutually exclusive; pick one")

    # Resolve the resulting value (and whether it's numeric) first, so unit —
    # which only applies to a numeric value — can be validated consistently.
    new_num = new_text = None          # the value to write, if changed
    becomes_num = None                 # True/False = changes type; None = value unchanged
    if args.num is not None:
        if not math.isfinite(args.num):
            sys.exit("✗ --num must be a finite number")
        new_num, becomes_num = args.num, True
    elif args.text is not None:
        new_text, becomes_num = _clean_text(args.text), False
    elif args.value is not None:
        vnum, vtext = _parse_value(args.value, False)
        if vtext is not None:
            new_text, becomes_num = _clean_text(vtext), False
        else:
            new_num, becomes_num = vnum, True
    is_num = becomes_num if becomes_num is not None else (row["value_num"] is not None)

    if args.unit is not None and args.unit and not is_num:
        sys.exit("✗ unit only applies to a numeric value (this metric's value is text)")

    changes = {}
    if args.tag is not None:
        t = args.tag.strip()
        if not t:
            sys.exit("✗ metric tag cannot be empty")
        changes["tag"] = t
    if becomes_num is not None:
        changes["value_num"], changes["value_text"] = new_num, new_text
    if becomes_num is False:
        # a text value has no unit — clear any stale one (no --unit can survive: it was rejected above)
        changes["unit"] = None
    elif args.unit is not None:
        changes["unit"] = args.unit or None  # --unit '' clears
    if args.note is not None:
        changes["note"] = args.note or None  # --note '' clears
    if args.at is not None:
        changes["at"] = _resolve_at(args.at)

    if not changes:
        sys.exit("✗ nothing to change (give --value/--num/--text/--unit/--note/--tag/--at)")

    _db.update(con, "metric", mid, changes)
    con.commit()
    out(_c("✓", "done") + " " + _line(_db.get(con, "metric", mid)))


def cmd_metric_rm(args, con):
    """Delete one or more metrics. If a metric's carrier was an auto-created
    (type='metric') empty-body log with no other metrics left, remove it too —
    otherwise a stale empty log would linger as fake activity."""
    # Buffer messages and emit them only after the commit succeeds, so we never
    # print "✓ deleted" for work that a later failure rolls back.
    msgs = []
    for mid in args.metric_ids:
        row = _db.find_one(con, "metric", cols="log_id", id=mid)
        if not row:
            msgs.append(f"(metric #M{mid} not found)")
            continue
        log_id = row["log_id"]
        _db.delete(con, "metric", id=mid)
        msg = f"✓ deleted metric #M{mid}"
        log = _db.find_one(con, "log", cols="body, tag", id=log_id)
        remaining = _db.count(con, "metric", log_id=log_id)
        if log and log["tag"] == _CARRIER_TYPE and not (log["body"] or "").strip() and remaining == 0:
            _db.delete(con, "log", id=log_id)
            msg += f" + its empty carrier log #L{log_id}"
        msgs.append(msg)
    con.commit()
    for m in msgs:
        out(_c(m, "meta"))


_SUBS = {
    "add": cmd_metric_add,
    "ls": cmd_metric_ls,
    "edit": cmd_metric_edit,
    "rm": cmd_metric_rm,
}


def cmd_metric(args, con):
    """Dispatch `wl metric <add|ls|edit|rm>`."""
    sub = getattr(args, "metric_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl metric <add|ls|edit|rm> … (see `wl metric --help`)")
    _SUBS[sub](args, con)
