"""Time-handling lint — `timeutil` is the ONE place that knows about clocks and zones.

An instant is stored UTC (`*_at`); a calendar date is stored as a literal local date
(`*date`). Every crossing between those worlds — read "now", parse a stored stamp, diff two
stamps, compute a "N days back" cutoff — has to go through `timeutil`, because each hand-rolled
copy is a fresh chance to compare a UTC value against a local one. That is not hypothetical: a
staleness check built its cutoff from the UTC date and compared it against a local activity date
(off by a day for the hours the local date leads UTC), and an age formatter hand-parsed
`strptime(ts, "%Y-%m-%d %H:%M:%S")` and blew up on the legacy date-only stamps older DBs carry.
Both were review catches, not test catches. So the rule gets a test.

Banned outside `timeutil.py`:
  1. reading the clock — `datetime.now()` / `datetime.utcnow()` / `date.today()`; use
     `utc_now()` / `local_now()` / `today()` / `today_date()` / `now_dt()`, which honor `$WORKLOG_TZ`;
  2. parsing a stored instant — `datetime.fromisoformat` / `datetime.strptime`; use `parse_ts()`
     (it also accepts the legacy date-only form), or `elapsed_sec()` / `age_min()` / `shift_ts()`.

Still allowed anywhere: `date.fromisoformat(s)` (validating a plain calendar date — no zone is
involved), and `timedelta` arithmetic on an already-local `date` from `today_date()` / `days_ago()`.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).parent.parent / "src" / "worklog"

SKIP_FILES = {"timeutil.py"}   # the single source itself — it's allowed to touch the clock
SKIP_DIRS = {"migrations"}     # already-applied migrations run against raw/legacy rows

CLOCK_CALLS = {("datetime", "now"), ("datetime", "utcnow"), ("date", "today")}
PARSE_CALLS = {("datetime", "fromisoformat"), ("datetime", "strptime")}

FIX = {
    "now": "timeutil.utc_now() / local_now() / now_dt()",
    "utcnow": "timeutil.utc_now()",
    "today": "timeutil.today() / today_date()",
    "fromisoformat": "timeutil.parse_ts() (or elapsed_sec / age_min / shift_ts)",
    "strptime": "timeutil.parse_ts() (or elapsed_sec / age_min / shift_ts)",
}


def _py_files():
    for f in sorted(SRC.rglob("*.py")):
        if f.name in SKIP_FILES or any(d in SKIP_DIRS for d in f.parts):
            continue
        yield f


def _base_name(node):
    """The receiver of an attribute call, tolerating the project's `datetime as _dt` /
    `date as _date` aliases: `_dt.fromisoformat` reports as `datetime`."""
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
        return None
    raw = node.value.id
    return {"_dt": "datetime", "_date": "date", "_datetime": "datetime"}.get(raw, raw)


def _offences(path, banned):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        base, attr = _base_name(node.func), getattr(node.func, "attr", None)
        if base and (base, attr) in banned:
            yield f"{path.relative_to(SRC)}:{node.lineno}  {base}.{attr}()  → use {FIX[attr]}"


def test_clock_is_read_only_through_timeutil():
    """Nobody outside timeutil asks the OS what time it is — that's how `$WORKLOG_TZ` stays
    authoritative and how a test can pin the zone."""
    bad = [o for f in _py_files() for o in _offences(f, CLOCK_CALLS)]
    assert not bad, "read the clock through timeutil:\n  " + "\n  ".join(bad)


def test_stored_stamps_are_parsed_only_through_timeutil():
    """Nobody outside timeutil hand-parses a stored `*_at` stamp — one parser means one answer
    about UTC-vs-local and one place that knows legacy date-only rows exist."""
    bad = [o for f in _py_files() for o in _offences(f, PARSE_CALLS)]
    assert not bad, "parse stored stamps through timeutil.parse_ts:\n  " + "\n  ".join(bad)
