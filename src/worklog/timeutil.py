"""Timezone handling for worklog.

Naming convention (see DESIGN): a column holding a **precise instant** is named
`*_at` (or bare `at`) and is stored as a UTC string `YYYY-MM-DD HH:MM:SS`; a
column holding a **literal calendar date** is named `*date` (e.g. `on_date`,
`scheduled_date`, `deadline_date`) and is stored verbatim as a local-calendar
`YYYY-MM-DD`, never timezone-converted.

So instants round-trip through here:
- on write: local wall-clock (or "now") -> UTC, via `utc_now()` / `local_to_utc()`
- on read:  UTC -> local, via `utc_to_local()` / `local_day_of()`

"Local" is the machine timezone by default (DST-correct via the OS). Set
`$WORKLOG_TZ` to a fixed offset (e.g. `+08:00`, `+8`, `-0500`) to override —
useful for reproducible tests and for pinning a zone regardless of the host.
China has no DST, so a fixed `+08:00` is exact year-round.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

FMT = "%Y-%m-%d %H:%M:%S"
_UTC = timezone.utc


def _parse_offset(tz: str) -> timezone | None:
    """Parse a fixed-offset string (`+08:00` / `+8` / `+0800` / `-5` / `8`) to a
    timezone. Returns None if it doesn't look like an offset."""
    m = re.fullmatch(r"([+-]?)(\d{1,2})(?::?(\d{2}))?", tz.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    hours = int(m.group(2))
    minutes = int(m.group(3) or 0)
    if hours > 14 or minutes >= 60:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _fixed_offset() -> timezone | None:
    """The fixed-offset timezone from `$WORKLOG_TZ` if set and parseable, else None
    — None means "use the machine's local zone", which we convert through the OS so
    it stays DST-correct per-date (capturing a single fixed offset would mis-convert
    timestamps from the other half of a DST year)."""
    tz = os.environ.get("WORKLOG_TZ", "").strip()
    if tz:
        return _parse_offset(tz)  # None if unparseable -> fall back to machine local
    return None


def utc_now() -> str:
    """Current instant as a UTC storage string."""
    return datetime.now(_UTC).strftime(FMT)


def local_now() -> str:
    """Current wall-clock in the configured local zone (`YYYY-MM-DD HH:MM:SS`).
    Use this to fill in "now" when a user supplies a partial local time (`--at
    14:30` / `--at 2026-06-05`) — the pieces are local, then `local_to_utc`
    stores UTC."""
    off = _fixed_offset()
    if off is not None:
        return datetime.now(off).strftime(FMT)
    return datetime.now().strftime(FMT)  # naive system-local wall clock


def today() -> str:
    """Today's date (`YYYY-MM-DD`) in the configured local zone — the calendar
    "today" used for day views and default schedules. Follows `$WORKLOG_TZ`
    when set, else the machine zone (so it agrees with `tz_sql_modifier()`)."""
    return local_now()[:10]


def today_date():
    """`today()` as a `datetime.date`, for relative-date arithmetic (week ranges,
    `--recent N`) that must also follow the configured zone."""
    from datetime import date as _date
    return _date.fromisoformat(today())


def local_to_utc(local_str: str) -> str:
    """A local wall-clock string (`YYYY-MM-DD HH:MM:SS`, optionally without
    seconds) -> UTC storage string. Used when the user supplies a time (`--at`)
    which is always entered in local time."""
    s = local_str.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", s):
        s += ":00"
    naive = datetime.strptime(s, FMT)
    off = _fixed_offset()
    # fixed offset: attach it; machine-local: a naive datetime's .astimezone(UTC)
    # interprets it in the OS zone (DST-correct for the date in question)
    aware = naive.replace(tzinfo=off) if off is not None else naive
    return aware.astimezone(_UTC).strftime(FMT)


def utc_to_local(utc_str: str) -> str:
    """A UTC storage string -> local wall-clock string for display. Pass-through
    for falsy / unparseable input (defensive: don't crash rendering on a stray
    value)."""
    if not utc_str:
        return utc_str
    try:
        dt = datetime.strptime(utc_str.strip(), FMT).replace(tzinfo=_UTC)
    except ValueError:
        return utc_str
    off = _fixed_offset()
    # machine-local: .astimezone() (no arg) converts to the OS zone, DST-correct
    return dt.astimezone(off).strftime(FMT) if off is not None else dt.astimezone().strftime(FMT)


def local_day_of(utc_str: str) -> str:
    """The local calendar date (`YYYY-MM-DD`) an instant falls on. This is the
    Python-side counterpart to the `tz_sql_modifier()` day-grouping in SQL —
    `substr(utc_str, 1, 10)` would give the UTC date, which is off by a day for
    the ±offset window around local midnight."""
    return utc_to_local(utc_str)[:10]


# ── parsing + arithmetic on stored instants ──────────────────────────────────
# Everything that reads a stored `*_at` stamp back into a datetime, or asks "how long
# ago / how long between", goes through these. Hand-rolling `datetime.strptime` at the
# call site is what produced a UTC-cutoff-vs-local-date comparison and a crash on legacy
# date-only stamps; `test_time_lint.py` now fails the build if a module reintroduces one.

def parse_ts(ts):
    """A stored instant -> naive UTC `datetime`, or None when it can't be read.

    Accepts the canonical `YYYY-MM-DD HH:MM:SS`, an ISO `T` separator, and the **legacy
    date-only** `YYYY-MM-DD` that older DBs still carry (pre-`wl log --date` logs, the
    0003-backfilled checkin `metric.at`). A date-only value is a literal *local* date, so
    it reads as that day's local midnight — the same rule `local_day_sql`'s CASE applies,
    so SQL and Python can't disagree about which day an old row belongs to."""
    if not ts:
        return None
    s = str(ts).strip().replace("T", " ")
    if len(s) >= 19:
        try:
            return datetime.strptime(s[:19], FMT)
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.strptime(local_to_utc(s + " 00:00:00"), FMT)
    return None


def now_dt() -> datetime:
    """"Now" as a naive UTC `datetime` — the counterpart of `parse_ts`, so a difference
    between them is always UTC-vs-UTC."""
    return datetime.strptime(utc_now(), FMT)


def elapsed_sec(start_ts, end_ts=None):
    """SIGNED whole seconds from `start_ts` to `end_ts` (default: now). None when either stamp
    can't be read.

    Deliberately not clamped: a negative span is meaningful — it means an end lands before its
    start, which is a user error (`wl clock edit --end` before `--start`) that the caller must
    REJECT, not silently round up to zero. Flooring is a policy the call site owns (`max(60, …)`
    for a clock's minimum billable minute); a primitive that clamps would hide the error."""
    a, b = parse_ts(start_ts), (parse_ts(end_ts) if end_ts else now_dt())
    if a is None or b is None:
        return None
    return int((b - a).total_seconds())


def age_min(ts) -> int | None:
    """Whole minutes since `ts`, or None when it can't be read (callers render a placeholder).
    Clamped at 0: a stamp in the future is a clock skew, not negative age."""
    secs = elapsed_sec(ts)
    return None if secs is None else max(0, secs) // 60


def shift_ts(ts, *, minutes: int = 0) -> str:
    """`ts` moved by `minutes` (may be negative), back as a UTC storage string."""
    base = parse_ts(ts) or now_dt()
    return (base + timedelta(minutes=minutes)).strftime(FMT)


_AT_HHMM = re.compile(r"\d{2}:\d{2}")
_AT_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AT_FULL = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")


def local_input_to_utc(at: str, *, anchor_local: str, label: str = "at") -> str:
    """Resolve a user-typed `--at` token -> a UTC storage string. The user always types LOCAL time,
    and may type only part of it; `anchor_local` (a local `YYYY-MM-DD HH:MM:SS`) supplies the rest:

      `HH:MM`                  -> keeps the anchor's DAY, replaces the time
      `YYYY-MM-DD`             -> keeps the anchor's TIME, replaces the day
      `YYYY-MM-DD HH:MM[:SS]`  -> replaces both

    The anchor is what distinguishes the two uses: an existing log's own timestamp (so a bare
    `HH:MM` on a backdated log can't silently yank it to today) versus `local_now()` for a fresh
    entry. Validates ranges (rejects `25:00`, month 13); raises ValueError on a bad token —
    `label` names the offending input in that message (`at` for a field-op, `--at` for the flag)."""
    at = (at or "").strip()
    if _AT_HHMM.fullmatch(at):
        datetime.strptime(at, "%H:%M")                        # range-check HH/MM
        return local_to_utc(f"{anchor_local[:10]} {at}:00")
    if _AT_DATE.fullmatch(at):
        datetime.strptime(at, "%Y-%m-%d")                     # range-check the calendar date
        return local_to_utc(f"{at} {anchor_local[11:] or '00:00:00'}")
    if _AT_FULL.fullmatch(at):
        ts = at.replace("T", " ")
        if len(ts) == 16:
            ts += ":00"
        datetime.strptime(ts, FMT)                            # range-check both halves
        return local_to_utc(ts)
    raise ValueError(f"invalid {label} '{at}': supported formats: HH:MM / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")


def days_ago(n: int) -> str:
    """The local calendar date `n` days before today (`YYYY-MM-DD`). The single source for
    every "cutoff N days back" — both sides of such a comparison must be LOCAL days, which
    is exactly what a hand-rolled UTC-derived cutoff got wrong."""
    return (today_date() - timedelta(days=n)).isoformat()


def days_ahead(n: int) -> str:
    """The local calendar date `n` days after today (`YYYY-MM-DD`)."""
    return (today_date() + timedelta(days=n)).isoformat()


def tz_sql_modifier() -> str:
    """The modifier to pass to SQLite `datetime(col, ?)` so a UTC-stored column
    renders in local time for day-grouping: `'localtime'` when following the
    machine zone, or a fixed `'±HH:MM'` offset when `$WORKLOG_TZ` is set.

    Usage: `substr(datetime(at, ?), 1, 10)` with this as the bound parameter,
    instead of the old `substr(at, 1, 10)` (which now yields the UTC date)."""
    tz = os.environ.get("WORKLOG_TZ", "").strip()
    if tz:
        off = _parse_offset(tz)
        if off is not None:
            total = int(off.utcoffset(None).total_seconds())
            sign = "+" if total >= 0 else "-"
            total = abs(total)
            return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"
    return "localtime"


def local_day_sql(col: str) -> str:
    """SQL fragment giving the **local calendar date** (`YYYY-MM-DD`) of a UTC
    instant column — the day-grouping replacement for the old `substr(col,1,10)`
    (which now yields the UTC date, off by a day near local midnight). The
    modifier is a controlled value (`localtime` or a fixed `±HH:MM`), never user
    input, so inlining it is safe. Usage: `f"... WHERE {local_day_sql('at')} = ?"`.

    Bare `YYYY-MM-DD` values (legacy date-only logged_at from logs written before
    `wl log --date` started keeping the time, and the 0003-backfilled checkin
    metric.at) are literal local dates — the CASE leaves them untouched, because
    offsetting a midnight (e.g. a negative `$WORKLOG_TZ`) would wrongly roll them
    to the previous day."""
    mod = tz_sql_modifier()
    return (f"CASE WHEN length({col}) >= 19 THEN substr(datetime({col}, '{mod}'), 1, 10) "
            f"ELSE substr({col}, 1, 10) END")


def local_month_sql(col: str) -> str:
    """Like `local_day_sql` but the local `YYYY-MM` month (for month-to-date views)."""
    mod = tz_sql_modifier()
    return (f"CASE WHEN length({col}) >= 19 THEN substr(datetime({col}, '{mod}'), 1, 7) "
            f"ELSE substr({col}, 1, 7) END")
