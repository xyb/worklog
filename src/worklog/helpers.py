"""Pure utility helpers for worklog.

No I/O, no sqlite, no rich — these all take args / strings and return
args / strings (or types like Path/datetime). Tested in isolation and
safely importable from any other module without side effects.
"""
from __future__ import annotations

from . import timeutil as _tu


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
    today = _tu.today_date()
    since = getattr(args, "since", None) or (today - timedelta(days=today.weekday())).isoformat()
    until = getattr(args, "until", None) or today.isoformat()
    return since, until


def _norm_word(s):
    """Lowercase + drop connector chars (hyphen / underscore / whitespace) so a relative-date
    *word* collapses to one lookup key: 'next-week', 'Next_Week', 'next week' and 'nextweek'
    all → 'nextweek'. Deliberately NOT applied to ISO dates or signed deltas — those keep
    their '-' sign / 'YYYY-MM-DD' structure and are matched on the original string. (A stripped
    delta like '-2w' → '2w' simply misses every word key, so the sign is never lost: the delta
    branch re-parses the original string.)"""
    import re as _re
    return _re.sub(r"[-_\s]+", "", s.strip().lower())


def _resolve_concrete_date(s):
    """Resolve a relative word / signed delta / YYYY-MM-DD to a concrete date string.
    Words: today/yesterday/day-before-yesterday/tomorrow/day-after-tomorrow (offset days) and
    next-week/next-month/next-quarter (→ that period's first day) + Chinese aliases. Matching is
    case-insensitive and connector-insensitive (so 'Day After Tomorrow' / 'next_week' resolve).
    `someday` has no anchor day and is rejected here (use `wl defer`)."""
    from datetime import date, timedelta

    s = s.strip()
    lower = s.lower()
    # word table keyed by the *normalized* form (see _norm_word): connectors stripped, lowercase.
    rel = {
        "today": 0, "今天": 0,
        "yesterday": -1, "昨天": -1,
        "daybeforeyesterday": -2, "前天": -2,
        "tomorrow": 1, "明天": 1,
        "dayaftertomorrow": 2, "后天": 2,
    }
    nw = _norm_word(s)
    if nw in rel:
        return (_tu.today_date() + timedelta(days=rel[nw])).isoformat()
    # fuzzy-period words resolve to that period's *anchor day* (its start), so concrete-date
    # commands (sched on_date / day / log --date) accept them too. `wl defer` keeps the coarser
    # granularity instead (next-week → a whole ISO week) — that is the one intentional split.
    today = _tu.today_date()
    if nw in ("nextweek", "下周", "下个星期"):
        y, w, _ = (today + timedelta(days=7)).isocalendar()
        return date.fromisocalendar(y, w, 1).isoformat()       # Monday of next ISO week
    if nw in ("nextmonth", "下月", "下个月"):
        y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return date(y, mo, 1).isoformat()
    if nw in ("nextquarter", "下季", "下个季度"):
        q = (today.month - 1) // 3 + 1
        ny, nq = (today.year + 1, 1) if q == 4 else (today.year, q + 1)
        return date(ny, (nq - 1) * 3 + 1, 1).isoformat()
    # signed relative delta from today: +1 / -2 / +1d / -2day / +3w / -2week / +1m / -1y.
    # signed number, optional unit (d/day[s] default · w/week[s] · m/month[s] · y/year[s]).
    import re as _re
    m = _re.fullmatch(r"([+-]\d+)\s*(d|day|days|w|week|weeks|m|month|months|y|year|years)?", lower)
    if m:
        n, unit = int(m.group(1)), (m.group(2) or "d")[0]
        base = _tu.today_date()
        if unit == "w":
            return (base + timedelta(weeks=n)).isoformat()
        if unit == "m":
            return _add_months(base, n).isoformat()
        if unit == "y":
            return _add_months(base, n * 12).isoformat()
        return (base + timedelta(days=n)).isoformat()
    date.fromisoformat(s)  # validate; raises ValueError on bad input
    return s


def _add_months(d, months):
    """Add `months` (may be negative) to a date, clamping the day to the target month's length
    (e.g. Jan 31 +1m → Feb 28). Stdlib only — no dateutil (DESIGN G3: few deps)."""
    from calendar import monthrange
    total = d.month - 1 + months
    y = d.year + total // 12
    mo = total % 12 + 1
    return d.replace(year=y, month=mo, day=min(d.day, monthrange(y, mo)[1]))

def _resolve_at_ts(at, default_now=True):
    """Parse --at into a UTC storage string. The user always enters *local* time;
    this returns the corresponding UTC instant (see timeutil). Accepts:
    HH:MM (today + that time) / YYYY-MM-DD (that day, current time) /
    YYYY-MM-DD HH:MM[:SS] / ISO with 'T' separator. None -> now (UTC).
    Validates range (rejects 25:00 / month 13); raises ValueError on error.
    """
    from datetime import datetime as _dt
    import re as _re
    from . import timeutil as _tu
    if not at:
        return _tu.utc_now() if default_now else None
    at = at.strip()
    local_now = _tu.local_now()  # 'YYYY-MM-DD HH:MM:SS' in the configured zone
    today = local_now[:10]
    if _re.fullmatch(r"\d{2}:\d{2}", at):
        _dt.strptime(at, "%H:%M")
        return _tu.local_to_utc(f"{today} {at}:00")
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", at):
        _dt.strptime(at, "%Y-%m-%d")
        return _tu.local_to_utc(f"{at} {local_now[11:]}")
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", at):
        ts = at.replace("T", " ")
        if len(ts) == 16:
            ts += ":00"
        _dt.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return _tu.local_to_utc(ts)
    raise ValueError(f"invalid --at '{at}': supported formats: HH:MM / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")


_WIDTH_CAP = None   # None = fill the terminal; int = cap rendered width to that many columns

def _set_width_cap(cap):
    """Set the output width cap (None = full terminal width). Called once from main()."""
    global _WIDTH_CAP
    _WIDTH_CAP = cap

def _resolve_width_cap(flag):
    """Resolve the cap: `--width` flag > `$WORKLOG_WIDTH` > default (None = full). Values:
    `full` → None, `help` → the `--help` cap (100 cols), a positive int → that many columns."""
    import os
    val = flag if flag is not None else os.environ.get("WORKLOG_WIDTH")
    if not val or val == "full":
        return None
    if val == "help":
        from .render import HELP_MAX_WIDTH
        return HELP_MAX_WIDTH
    try:
        n = int(val)
    except ValueError:
        return None
    return n if n > 0 else None

def _term_width():
    """Rendered column count: the terminal width, capped to `--width` / `$WORKLOG_WIDTH` if set
    (so wide terminals can hold output to a fixed, readable width). No TTY (pipe/redirect) -> 80."""
    import shutil
    try:
        w = shutil.get_terminal_size().columns or 80
    except OSError:
        w = 80
    return min(w, _WIDTH_CAP) if _WIDTH_CAP else w

def _cw(ch):
    """Display columns for one char: East-Asian Wide/Fullwidth = 2, else 1. Uses
    `unicodedata.east_asian_width`, which keeps CJK at 2 but the narrow punctuation we use
    (· → … — ▸, all Ambiguous/Narrow) at 1 — a crude non-ASCII→2 rule would wrongly double those."""
    import unicodedata
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _display_width(s):
    """Terminal column width of a string (sum of per-char `_cw`). The single width measure used
    across the codebase — log truncation budgets, help wrapping, and hanging indents all share it."""
    return sum(_cw(ch) for ch in s)


_TITLE_MODE = "wrap"   # "wrap" = multi-line with hanging indent (default); "clip" = one line, truncate with …

def _set_title_mode(mode):
    """Set how node titles render when too wide: 'wrap' (default) or 'clip'. Called once from main()."""
    global _TITLE_MODE
    _TITLE_MODE = mode

def _resolve_title_mode(flag):
    """Resolve title mode: `--title` flag > `$WORKLOG_TITLE` > default 'wrap'. Anything other than
    'clip' (incl. unset / 'wrap' / garbage) → 'wrap', so the safe multi-line default always wins."""
    import os
    val = flag if flag is not None else os.environ.get("WORKLOG_TITLE")
    return "clip" if val == "clip" else "wrap"

def _title_mode():
    return _TITLE_MODE

def _wrap_display(text, width):
    """Word-wrap `text` to lines no wider than `width` display columns (East-Asian aware).

    Breaks preferentially at spaces; a token longer than `width` (e.g. a long CJK run, which has
    no spaces) is hard-broken at the character boundary. Returns a list of plain-text lines
    (always at least one, even for empty input). `width` is floored at 1."""
    width = max(1, width)
    lines, line, used = [], [], 0
    # split keeping spaces as their own tokens, so we can break at them and drop the trailing space
    import re as _re
    tokens = _re.findall(r"\s+|\S+", text)
    for tok in tokens:
        is_space = tok.isspace()
        tw = _display_width(tok)
        if not is_space and tw > width:
            # token wider than the line: flush, then hard-break it per character
            if line:
                lines.append("".join(line)); line, used = [], 0
            for ch in tok:
                cw = _cw(ch)
                if used + cw > width:
                    lines.append("".join(line)); line, used = [], 0
                line.append(ch); used += cw
            continue
        if used + tw > width:
            lines.append("".join(line)); line, used = [], 0
            if is_space:
                continue  # don't carry a leading space onto the new line
        line.append(tok); used += tw
    lines.append("".join(line))
    # strip trailing spaces left on each line by the space-token handling
    return [ln.rstrip() if ln.strip() else ln for ln in lines] or [""]

def _truncate_log_body(body, indent_cols, full=False):
    """Truncate log body to one line (terminal width - indent - safety margin), append … at end. full=True keeps body untouched.
    indent_cols is the column width already occupied before body (indent + marker).
    CJK characters may take 2 columns; this approximation by character count is acceptable.
    """
    if full:
        return body
    # collapse embedded newlines / whitespace runs into single spaces FIRST, so a multi-line body
    # renders as one truncated line (…) instead of spilling continuation lines to column 0 and
    # letting the width budget accumulate across lines. Every truncated-body view (day / tree /
    # timeline / logs) wants one line per log; the full multi-line body is still there via full=True.
    body = " ".join((body or "").split())
    width = _term_width()
    # available columns = width - indent_cols - small safety margin (2 cols to avoid edge wrap).
    # Floor at 1, not 20: a 20-col floor *forces* overflow when indent_cols is large (e.g. the
    # ~39-col `wl show` timeline prefix made any terminal < ~61 cols overflow). With a tall prefix
    # and a narrow terminal the body degrades to just "…" — correct, vs. spilling onto a 2nd line.
    avail = max(1, width - indent_cols - 2)
    # CJK chars take 2 cols; estimate effective usage
    used = 0
    out_chars = []
    for ch in body:
        w = _cw(ch)
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
    # relative words match by normalized key (connector-insensitive, lowercase); ISO/delta forms
    # below keep matching the original `s` (their '-' is structural, not a connector).
    nw = _norm_word(s)
    today = _tu.today_date()
    if nw in ("today", "今天"):
        return today.isoformat()
    if nw in ("tomorrow", "明天"):
        return (today + _dt.timedelta(days=1)).isoformat()
    if nw in ("nextweek", "下周", "下个星期"):
        y, w, _ = (today + _dt.timedelta(days=7)).isocalendar()
        return f"{y}-W{w:02d}"
    if nw in ("nextmonth", "下月", "下个月"):
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return f"{y}-{m:02d}"
    if nw in ("nextquarter", "下季", "下个季度"):
        q = (today.month - 1) // 3 + 1
        ny, nq = (today.year + 1, 1) if q == 4 else (today.year, q + 1)
        return f"{ny}-Q{nq}"
    if nw in ("someday", "以后", "有空", "总有一天"):
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
    # fall through to the concrete-date resolver: signed deltas (+1 / -2d / +3w / -1y) and the
    # full yesterday / day-before-yesterday / day-after-tomorrow word set resolve to a precise
    # day hint, which defer stores as a YYYY-MM-DD scheduled_date.
    try:
        return _resolve_concrete_date(s)
    except ValueError:
        pass
    raise ValueError(
        f"unrecognized scheduled time '{s}' (use YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / +N / -2w / next-week / next-month / next-quarter)")

def _sched_level(s):
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
    k = _sched_level(s)
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
    return (_sched_anchor(s), rank.get(_sched_level(s), 9))

def _sched_display(s):
    """Display: precise dates show MM-DD only (current-year context); fuzzy values shown as-is (month/week/quarter/year/someday)."""
    if not s:
        return ""
    return s[5:] if _sched_level(s) == "day" else s


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


# SQL / time-level constants shared between command modules
_ORDER_BY_PRI_ID = "ORDER BY priority NULLS LAST, id"
_TIME_LEVELS = {"lifetime", "decade", "year", "quarter", "month", "week", "day"}

