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


def _local_tz() -> timezone:
    """The timezone used for rendering / day-grouping: `$WORKLOG_TZ` fixed offset
    if set and parseable, else the machine's current local timezone."""
    tz = os.environ.get("WORKLOG_TZ", "").strip()
    if tz:
        off = _parse_offset(tz)
        if off is not None:
            return off
    # machine local — naive now carries the OS tzinfo once we astimezone()
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def utc_now() -> str:
    """Current instant as a UTC storage string."""
    return datetime.now(_UTC).strftime(FMT)


def local_now() -> str:
    """Current wall-clock in the configured local zone (`YYYY-MM-DD HH:MM:SS`).
    Use this to fill in "now" when a user supplies a partial local time (`--at
    14:30` / `--at 2026-06-05`) — the pieces are local, then `local_to_utc`
    stores UTC."""
    return datetime.now(_local_tz()).strftime(FMT)


def today() -> str:
    """Today's date (`YYYY-MM-DD`) in the configured local zone — the calendar
    "today" used for day views and default schedules. Follows `$WORKLOG_TZ`
    when set, else the machine zone (so it agrees with `tz_sql_modifier()`)."""
    return local_now()[:10]


def local_to_utc(local_str: str) -> str:
    """A local wall-clock string (`YYYY-MM-DD HH:MM:SS`, optionally without
    seconds) -> UTC storage string. Used when the user supplies a time (`--at`)
    which is always entered in local time."""
    s = local_str.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", s):
        s += ":00"
    dt = datetime.strptime(s, FMT).replace(tzinfo=_local_tz())
    return dt.astimezone(_UTC).strftime(FMT)


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
    return dt.astimezone(_local_tz()).strftime(FMT)


def local_day_of(utc_str: str) -> str:
    """The local calendar date (`YYYY-MM-DD`) an instant falls on. This is the
    Python-side counterpart to the `tz_sql_modifier()` day-grouping in SQL —
    `substr(utc_str, 1, 10)` would give the UTC date, which is off by a day for
    the ±offset window around local midnight."""
    return utc_to_local(utc_str)[:10]


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
    input, so inlining it is safe. Usage: `f"... WHERE {local_day_sql('at')} = ?"`."""
    return f"substr(datetime({col}, '{tz_sql_modifier()}'), 1, 10)"


def local_month_sql(col: str) -> str:
    """Like `local_day_sql` but the local `YYYY-MM` month (for month-to-date views)."""
    return f"substr(datetime({col}, '{tz_sql_modifier()}'), 1, 7)"
