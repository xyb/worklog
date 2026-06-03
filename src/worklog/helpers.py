"""Pure utility helpers for worklog.

No I/O, no sqlite, no rich — these all take args / strings and return
args / strings (or types like Path/datetime). Tested in isolation and
safely importable from any other module without side effects.
"""
from __future__ import annotations


# generic-dimension tags (planning attributes / priority / type) -- excluded from focus --related, which links only on project/topic tags
GENERIC_TAGS = {
    "work", "personal", "planned", "unplanned",
    "P0", "P1", "P2", "habit", "meeting", "followup",
    "dev", "ai", "sync", "strategy", "reflection", "reading",
    "family", "health", "morning_check", "slack_scan",
}


def _status_marker(status):
    return {
        None: "[ ]",
        "TODO": "[ ]",
        "DOING": "[/]",
        "LATER": "[>]",
        "WAIT": "[?]",
        "DONE": "[x]",
        "DEFERRED": "[>]",
        "CANCELED": "[-]",
    }.get(status, "[ ]")


def _resolve_window(args):
    """Resolve a time window to (since, until) YYYY-MM-DD pair. Priority: week > month > since/until > this Monday to today."""
    from datetime import date, timedelta

    if getattr(args, "week", None):
        y, w = args.week.split("-W")
        monday = date.fromisocalendar(int(y), int(w), 1)
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    if getattr(args, "month", None):
        y, m = (int(x) for x in args.month.split("-"))
        first = date(y, m, 1)
        nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        return first.isoformat(), (nxt - timedelta(days=1)).isoformat()
    today = date.today()
    since = getattr(args, "since", None) or (today - timedelta(days=today.weekday())).isoformat()
    until = getattr(args, "until", None) or today.isoformat()
    return since, until


def _resolve_concrete_date(s):
    """Resolve today/yesterday/tomorrow/day-before-yesterday/day-after-tomorrow/YYYY-MM-DD (and Chinese aliases) to a concrete date string.
    English aliases are case-insensitive."""
    from datetime import date, timedelta

    s = s.strip()
    lower = s.lower()
    rel = {
        "today": 0, "今天": 0,
        "yesterday": -1, "昨天": -1,
        "day-before-yesterday": -2, "前天": -2,
        "tomorrow": 1, "明天": 1,
        "day-after-tomorrow": 2, "后天": 2,
    }
    if s in rel:
        return (date.today() + timedelta(days=rel[s])).isoformat()
    if lower in rel:
        return (date.today() + timedelta(days=rel[lower])).isoformat()
    date.fromisoformat(s)  # validate; raises ValueError on bad input
    return s

def _resolve_at_ts(at, default_now=True):
    """Parse --at: HH:MM (today + that time) / YYYY-MM-DD (that day, current time) /
    YYYY-MM-DD HH:MM[:SS] / ISO with 'T' separator. None -> now.
    Validates range (rejects 25:00 / month 13); raises ValueError on error.
    """
    from datetime import datetime as _dt
    import re as _re
    if not at:
        return _dt.now().strftime("%Y-%m-%d %H:%M:%S") if default_now else None
    at = at.strip()
    today = _dt.now().strftime("%Y-%m-%d")
    if _re.fullmatch(r"\d{2}:\d{2}", at):
        _dt.strptime(at, "%H:%M")
        return f"{today} {at}:00"
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", at):
        _dt.strptime(at, "%Y-%m-%d")
        return f"{at} {_dt.now().strftime('%H:%M:%S')}"
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", at):
        ts = at.replace("T", " ")
        if len(ts) == 16:
            ts += ":00"
        _dt.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return ts
    raise ValueError(f"invalid --at '{at}': supported formats: HH:MM / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")


def _term_width():
    """Terminal column count. No TTY (pipe/redirect) -> default 80."""
    import shutil
    try:
        return shutil.get_terminal_size().columns or 80
    except OSError:
        return 80

def _display_width(s):
    """Approximate terminal column width of a string: non-ASCII (CJK etc.) count as 2
    columns, others as 1 — same approximation _truncate_log_body uses internally. Use
    it to size indent_cols from a real prefix string instead of hard-coding a guess."""
    return sum(2 if ord(ch) > 0x7F else 1 for ch in s)

def _truncate_log_body(body, indent_cols, full=False):
    """Truncate log body to one line (terminal width - indent - safety margin), append … at end. full=True keeps body untouched.
    indent_cols is the column width already occupied before body (indent + marker).
    CJK characters may take 2 columns; this approximation by character count is acceptable.
    """
    if full:
        return body
    width = _term_width()
    # available columns = width - indent_cols - small safety margin (2 cols to avoid edge wrap)
    avail = max(20, width - indent_cols - 2)
    # CJK chars take 2 cols; estimate effective usage
    used = 0
    out_chars = []
    for ch in body:
        w = 2 if ord(ch) > 0x7F else 1
        if used + w > avail - 1:  # reserve 1 col for …
            out_chars.append("…")
            return "".join(out_chars)
        out_chars.append(ch)
        used += w
    return body


def _is_brief(args, *extras):
    """Brief mode: -q/--brief or any extra flag (no_logs/no_timeline/no_body etc.) triggers it.
    Usage: brief = _is_brief(args, "no_logs", "no_timeline")
    """
    if getattr(args, "brief", False):
        return True
    return any(getattr(args, e, False) for e in extras)

def _resolve_log_tail(args, brief, default_tail=3):
    """Unified log tail resolution. Shared by all commands that show log lists.

    Priority (high to low):
    - brief / --no-logs / --no-timeline / --no-body -> 0 (none shown)
    - --all-logs (or --all-timeline) -> None (full expansion)
    - --log-tail N (or --timeline-tail N / --tail N) -> N
    - otherwise -> default_tail (usually 3)
    """
    if brief:
        return 0
    if (getattr(args, "all_logs", False) or getattr(args, "all_timelines", False)
            or getattr(args, "all_timeline", False)):
        return None
    for attr in ("log_tail", "timeline_tail", "tail"):
        v = getattr(args, attr, None)
        if v is not None:
            return v
    return default_tail


def _norm_sched(s):
    """Normalize user-input scheduled time. Returns normalized string; raises ValueError on bad input; empty returns None.
    Accepts YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday
    + relative words today|tomorrow|next-week|next-month|next-quarter."""
    import re as _re
    import datetime as _dt
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    today = _dt.date.today()
    if s in ("today", "今天"):
        return today.isoformat()
    if s in ("tomorrow", "明天"):
        return (today + _dt.timedelta(days=1)).isoformat()
    if s in ("next-week", "下周", "下个星期"):
        y, w, _ = (today + _dt.timedelta(days=7)).isocalendar()
        return f"{y}-W{w:02d}"
    if s in ("next-month", "下月", "下个月"):
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return f"{y}-{m:02d}"
    if s in ("next-quarter", "下季", "下个季度"):
        q = (today.month - 1) // 3 + 1
        ny, nq = (today.year + 1, 1) if q == 4 else (today.year, q + 1)
        return f"{ny}-Q{nq}"
    if s in ("someday", "以后", "有空", "总有一天"):
        return "someday"
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        _dt.date.fromisoformat(s)  # validate; raises ValueError on bad input
        return s
    if _re.fullmatch(r"\d{4}-\d{2}", s):
        if not 1 <= int(s[5:7]) <= 12:
            raise ValueError(f"invalid month: {s}")
        return s
    if _re.fullmatch(r"\d{4}-W\d{2}", s):
        if not 1 <= int(s[6:]) <= 53:
            raise ValueError(f"invalid week: {s}")
        return s
    if _re.fullmatch(r"\d{4}-Q[1-4]", s):
        return s
    if _re.fullmatch(r"\d{4}", s):
        return s
    raise ValueError(
        f"unrecognized scheduled time '{s}' (use YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / next-week / next-month / next-quarter)")

def _sched_kind(s):
    """Normalized value -> granularity: day/week/month/quarter/year/someday/fuzzy"""
    import re as _re
    if not s:
        return None
    if s == "someday":
        return "someday"
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return "day"
    if _re.fullmatch(r"\d{4}-W\d{2}", s):
        return "week"
    if _re.fullmatch(r"\d{4}-\d{2}", s):
        return "month"
    if _re.fullmatch(r"\d{4}-Q[1-4]", s):
        return "quarter"
    if _re.fullmatch(r"\d{4}", s):
        return "year"
    return "fuzzy"

def _sched_anchor(s):
    """Normalized value -> anchor date (for sorting). someday/fuzzy -> far-future, sorts last."""
    import datetime as _dt
    k = _sched_kind(s)
    try:
        if k == "day":
            return s
        if k == "month":
            return s + "-01"
        if k == "year":
            return s + "-01-01"
        if k == "quarter":
            y, q = int(s[:4]), int(s[6])
            return f"{y}-{(q - 1) * 3 + 1:02d}-01"
        if k == "week":
            return _dt.date.fromisocalendar(int(s[:4]), int(s[6:]), 1).isoformat()
    except (ValueError, IndexError):
        # fromisoformat / fromisocalendar / int() / slice out of range -> treat as unparseable, use sentinel
        pass
    return "9999-12-31"

def _sched_sort_key(s):
    """Sort key (anchor date, granularity rank). Coarser granularities sort later; someday/fuzzy last. Callers compose final keys."""
    rank = {"day": 0, "week": 1, "month": 2, "quarter": 3, "year": 4, "someday": 9, "fuzzy": 9}
    return (_sched_anchor(s), rank.get(_sched_kind(s), 9))

def _sched_display(s):
    """Display: precise dates show MM-DD only (current-year context); fuzzy values shown as-is (month/week/quarter/year/someday)."""
    if not s:
        return ""
    return s[5:] if _sched_kind(s) == "day" else s


def _fmt_dur(minutes):
    """Compact duration format: [2h30m] / [45m] / [0] hidden. ASCII-safe, no reliance on emoji widths."""
    if not minutes or minutes <= 0:
        return ""
    h, m = divmod(int(minutes), 60)
    if h:
        return f"[{h}h{m}m]" if m else f"[{h}h]"
    return f"[{m}m]"


def _apply_top_limit(rows, args):
    """Truncate the list by args.top / args.limit; return (rows, total_before).
    `--top` takes the first N (rows are already in target order); `--limit` further truncates the display.
    """
    total = len(rows)
    top = getattr(args, "top", None)
    if top is not None and top > 0:
        rows = rows[:top]
    limit = getattr(args, "limit", None)
    if limit is not None and limit > 0:
        rows = rows[:limit]
    return rows, total


def _log_full(args):
    """args.log_format == 'full' -> True; otherwise (including None / 'oneline') -> False."""
    return getattr(args, "log_format", "oneline") == "full"


# SQL / kind constants shared between command modules
_ORDER_BY_PRI_ID = "ORDER BY priority NULLS LAST, id"
_TIME_KINDS = {"lifetime", "decade", "year", "quarter", "month", "week", "day"}

