#!/usr/bin/env python3
"""worklog-cli (wl): SQLite-backed worklog tool, todo.sh-style CLI.

Usage examples:
  wl init                                  # init DB
  wl add "research X" -k task -p A -t work,P0 --proj dev_tooling
  wl add "Dev tooling" -k project --parent 12
  wl ls                                    # list open items
  wl ls --kind task --tag P0
  wl tree                                  # full tree
  wl tree --kind project
  wl show 42                               # detail + log + props + tags + links
  wl log 42 "reviewed A's notes, found..."
  wl done 42
  wl defer 42 2026-06-01
  wl start 42  /  wl stop 42               # CLOCK in/out (writes log)
  wl link 42 "Dev tooling"            # add vault wikilink
  wl set 42 owner xyb                    # add custom prop
"""
from __future__ import annotations

__version__ = "0.3.0"

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

def _xdg_data_home() -> Path:
    """XDG_DATA_HOME (default ~/.local/share). Spec: https://specifications.freedesktop.org/basedir-spec/"""
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def _xdg_config_home() -> Path:
    """XDG_CONFIG_HOME (default ~/.config)."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def _resolve_db_path() -> Path:
    """Resolve the SQLite DB path. Priority:
    1. $WL_DB env (testing / explicit override)
    2. legacy ~/.worklog/wl.db if it exists (back-compat for pre-XDG users)
    3. $XDG_DATA_HOME/wl/wl.db (default ~/.local/share/wl/wl.db)
    """
    env = os.environ.get("WL_DB")
    if env:
        return Path(env).resolve()
    legacy = Path.home() / ".worklog" / "wl.db"
    if legacy.exists():
        return legacy.resolve()
    return (_xdg_data_home() / "wl" / "wl.db").resolve()


def _resolve_aliases_path() -> Path:
    """$XDG_CONFIG_HOME/wl/aliases.ini (default ~/.config/wl/aliases.ini)."""
    return _xdg_config_home() / "wl" / "aliases.ini"


DB_PATH = _resolve_db_path()
ALIASES_PATH = _resolve_aliases_path()
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# --- rich highlighting (optional dep, auto-detected; missing or non-TTY -> plain text) ---
try:
    from rich.console import Console as _RichConsole
    from rich.theme import Theme as _RichTheme
    from rich.markup import escape as _rich_escape
    _RICH_AVAIL = True
except ImportError:
    _RICH_AVAIL = False

# theme = semantic element -> rich style. No "default" theme; default is auto, probes terminal bg and resolves to dark/light/mono.
_THEME_KEYS = "done doing later wait todo canceled pri_a pri_b pri_c id kind tag hit header meta planned clock".split()
THEMES = {
    # dark: dark background, use bright_* for contrast
    "dark": {
        "done": "bright_green", "doing": "bright_yellow", "later": "bright_cyan", "wait": "grey50",
        "todo": "default", "canceled": "strike grey50",
        "pri_a": "bold bright_red", "pri_b": "bright_yellow", "pri_c": "grey50",
        "id": "grey50", "kind": "bright_cyan", "tag": "bright_magenta", "hit": "bold black on bright_yellow",
        "header": "bold bright_white", "meta": "grey50", "planned": "bright_blue", "clock": "bright_green",
    },
    # light: light background, use deep saturated colors (avoid bright/white getting lost on white bg)
    "light": {
        "done": "green4", "doing": "dark_orange3", "later": "blue", "wait": "grey42",
        "todo": "default", "canceled": "strike grey42",
        "pri_a": "bold red3", "pri_b": "dark_orange3", "pri_c": "grey42",
        "id": "grey42", "kind": "dark_cyan", "tag": "purple", "hit": "bold black on yellow3",
        "header": "bold grey15", "meta": "grey42", "planned": "blue", "clock": "green4",
    },
    # mono: no color (want rich layout but no color)
    "mono": {k: "default" for k in _THEME_KEYS},
}
_STATUS_STYLE = {"DONE": "done", "DOING": "doing", "LATER": "later", "WAIT": "wait",
                 "TODO": "todo", "DEFERRED": "later", "CANCELED": "canceled", None: "todo"}
_PRI_STYLE = {"A": "pri_a", "B": "pri_b", "C": "pri_c"}

_CONSOLE = None  # initialized by main() based on --color/--theme; None = plain text


def _resolve_color(mode):
    if mode is None:
        mode = os.environ.get("WL_COLOR", "auto")
    if mode == "never":
        return False
    if mode == "always":
        return True
    return _RICH_AVAIL and sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _detect_bg_is_dark():  # pragma: no cover -- TTY/escape-seq probe, not unit-tested at integration layer
    """Detect terminal bg: True=dark / False=light / None=unknown.
    First check $COLORFGBG (no I/O), then query OSC 11 (requires TTY, short timeout)."""
    fgbg = os.environ.get("COLORFGBG")
    if fgbg and ";" in fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
            return bg not in (7, 15)  # 7/15 = light bg, others treated as dark
        except ValueError:
            pass
    if not (sys.stdout.isatty() and sys.stdin.isatty()):
        return None
    try:
        import termios, tty, select, re
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write("\033]11;?\033\\")
            sys.stdout.flush()
            resp = ""
            if select.select([fd], [], [], 0.15)[0]:
                resp = os.read(fd, 64).decode("latin-1", "ignore")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        m = re.search(r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", resp)
        if not m:
            return None
        r, g, b = (int(m.group(i)[:2], 16) for i in (1, 2, 3))  # take top 2 hex digits per channel
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.5  # perceived brightness < 0.5 = dark
    except (ValueError, AttributeError):
        # int(..., 16) parse failure / m.group out of range -> undetectable, treat as unknown
        return None


def _resolve_theme(name):
    """Resolve theme name to a real palette name. auto (default): probe bg -> dark/light, fallback dark if unknown."""
    if name in THEMES:
        return name  # explicit real theme
    # name is None / "auto" / unknown -> auto-detect
    dark = _detect_bg_is_dark()
    if dark is False:
        return "light"
    return "dark"  # dark or unknown -> use dark (most terminals have dark bg)


def _init_console(color_mode, theme_name):
    global _CONSOLE
    if not _resolve_color(color_mode) or not _RICH_AVAIL:
        _CONSOLE = None
        return
    name = _resolve_theme(theme_name or os.environ.get("WL_THEME"))
    force = True if color_mode == "always" else None
    _CONSOLE = _RichConsole(theme=_RichTheme(THEMES[name]), force_terminal=force, highlight=False, soft_wrap=True)
    # terminal without color support (TERM=dumb etc.) -> effectively mono, rich won't emit ANSI


def out(s):
    """Unified output: when highlighting is enabled, use rich (markup rendering); otherwise plain print."""
    if _CONSOLE is not None:
        _CONSOLE.print(s)
    else:
        print(s)


def _c(text, style=None):
    """Color a fragment: returns rich markup when enabled (content escaped to prevent injection), otherwise plain text."""
    t = str(text)
    if _CONSOLE is None:
        return t
    t = _rich_escape(t)
    return f"[{style}]{t}[/{style}]" if style else t


def _hl(text, q):
    """In a string, mark query matches (styled: hit style / plain: *…*). No match -> plain _c."""
    text = str(text)
    if not q:
        return _c(text)
    i = text.lower().find(q.lower())
    if i < 0:
        return _c(text)
    mid = text[i:i + len(q)]
    pre, post = text[:i], text[i + len(q):]
    if _CONSOLE is None:
        return pre + f"*{mid}*" + post
    return _c(pre) + _c(mid, "hit") + _c(post)


# generic-dimension tags (planning attributes / priority / type) -- excluded from focus --related, which links only on project/topic tags
GENERIC_TAGS = {
    "work", "personal", "planned", "unplanned",
    "P0", "P1", "P2", "habit", "meeting", "followup",
    "dev", "ai", "sync", "strategy", "reflection", "reading",
    "family", "health", "morning_check", "slack_scan",
}


# ─── DB helpers ───
def db_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def db_init(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    con.commit()


def ensure_db():
    if not DB_PATH.exists():
        con = db_connect()
        db_init(con)
        con.close()


# ─── command handlers ───
def cmd_init(args, con):
    db_init(con)
    print(f"✓ DB initialized: {DB_PATH}")


def cmd_add(args, con):
    if not args.title or not args.title.strip():
        sys.exit("✗ title cannot be empty")
    args.title = args.title.strip()
    if args.sched and args.scheduled:
        sys.exit("✗ --sched (precise, writes sched table) and --scheduled (rough hint, writes node.scheduled_at) are mutually exclusive; use --sched day-to-day")
    tags = [t.strip() for t in (args.tag or "").split(",") if t.strip()]
    props = {}
    if args.proj:
        props["project"] = args.proj
    if args.deadline:
        deadline = args.deadline
    else:
        deadline = None

    status = args.status
    if not status and args.kind in ("task", "habit"):
        status = "TODO"
    # --done overrides status directly (one-shot retrospective entry)
    if getattr(args, "done", False):
        status = "DONE"

    # --at affects --log timestamp + (if --done) closed_at
    at_ts = None
    if getattr(args, "at", None):
        try:
            at_ts = _resolve_at_ts(args.at)
        except ValueError as e:
            sys.exit(f"✗ {e}")
    closed_at = None
    if status == "DONE":
        closed_at = at_ts if at_ts else "__NOW__"  # placeholder, SQL below decides

    try:
        scheduled = _norm_sched(args.scheduled)
    except ValueError as e:
        sys.exit(f"✗ {e}")

    if closed_at == "__NOW__":
        cur = con.execute(
            """INSERT INTO node (parent_id, title, kind, status, priority, scheduled_at, deadline_at, body, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))""",
            (args.parent, args.title, args.kind, status, args.priority, scheduled, deadline, args.body),
        )
    elif closed_at:
        cur = con.execute(
            """INSERT INTO node (parent_id, title, kind, status, priority, scheduled_at, deadline_at, body, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (args.parent, args.title, args.kind, status, args.priority, scheduled, deadline, args.body, closed_at),
        )
    else:
        cur = con.execute(
            """INSERT INTO node (parent_id, title, kind, status, priority, scheduled_at, deadline_at, body)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (args.parent, args.title, args.kind, status, args.priority, scheduled, deadline, args.body),
        )
    node_id = cur.lastrowid
    for t in tags:
        con.execute("INSERT OR IGNORE INTO tag (node_id, tag) VALUES (?, ?)", (node_id, t))
    if args.proj:
        con.execute("INSERT OR IGNORE INTO prop (node_id, key, value) VALUES (?, ?, ?)", (node_id, "project", args.proj))
    # --sched: write directly to sched table (one command = "create task + schedule it as planned for a day")
    sched_hint = ""
    if getattr(args, "sched", None):
        try:
            d = _resolve_concrete_date(args.sched)
        except ValueError:
            sys.exit(f"✗ invalid --sched date '{args.sched}' (use YYYY-MM-DD / today / tomorrow / day-after-tomorrow / yesterday)")
        con.execute("INSERT INTO sched (node_id, on_date) VALUES (?, ?)", (node_id, d))
        sched_hint = " " + _c(f"@{d}", "planned")

    # --link: attach a vault doc
    link_hint = ""
    if getattr(args, "link", None):
        link_doc = args.link.strip()
        if link_doc:
            con.execute("INSERT OR IGNORE INTO link (node_id, vault_doc) VALUES (?, ?)", (node_id, link_doc))
            link_hint = " → " + _c(f"[[{link_doc}]]", "meta")

    # --log: insert a log (using at_ts if given, otherwise NOW)
    log_hint = ""
    log_body = getattr(args, "log", None)
    if log_body and log_body.strip():
        if at_ts:
            _insert_log(con, node_id, {"date": at_ts[:10], "time": at_ts[11:16], "body": log_body.strip()})
        else:
            _insert_log(con, node_id, log_body.strip())
        log_hint = " + log"

    con.commit()
    st = (" " + _c(f"[{status}]", _STATUS_STYLE.get(status, "todo"))) if status else ""
    out(_c("✓", "done") + " " + _c(f"#{node_id}", "id") + " " + _c(f"{args.kind} '{args.title}'")
        + st + sched_hint + link_hint + log_hint)


def _insert_log(con, nid, entry):
    """Insert a log. entry can carry a historical date + time:
    - dict{date, time, body}: date=YYYY-MM-DD / today / yesterday / day-before-yesterday; time=HH:MM optional
    - string prefixed with 'YYYY-MM-DD content': date only
    - plain body: use NOW (DB DEFAULT)
    """
    import re as _re
    date, time_part, body = None, None, entry
    if isinstance(entry, dict):
        date, time_part, body = entry.get("date"), entry.get("time"), entry["body"]
    else:
        m = _re.match(r"^(\d{4}-\d{2}-\d{2})[ T](.*)$", entry)
        if m:
            date, body = m.group(1), m.group(2)
    if date:
        # parse short form ("yesterday/today/day-before-yesterday/tomorrow/day-after-tomorrow" or YYYY-MM-DD)
        date = _resolve_concrete_date(date)
        if time_part:
            if not _re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", time_part):
                raise ValueError(f"invalid --time '{time_part}' (expected HH:MM or HH:MM:SS)")
            # pad seconds
            if time_part.count(":") == 1:
                time_part += ":00"
            logged_at = f"{date} {time_part}"
        else:
            logged_at = date
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, ?)", (nid, logged_at, body))
    elif time_part:
        # no date but time given -> today + that time
        from datetime import date as _date
        if not _re.match(r"^(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$", time_part):
            raise ValueError(f"invalid --time '{time_part}' (expected HH:MM or HH:MM:SS)")
        if time_part.count(":") == 1:
            time_part += ":00"
        logged_at = f"{_date.today().isoformat()} {time_part}"
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, ?)", (nid, logged_at, body))
    else:
        con.execute("INSERT INTO log (node_id, body) VALUES (?, ?)", (nid, body))


def cmd_log(args, con):
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    if not args.body or not args.body.strip():
        sys.exit("✗ log body cannot be empty")
    args.body = args.body.strip()
    date = getattr(args, "date", None)
    time_ = getattr(args, "time", None)
    if date or time_:
        entry = {"date": date, "time": time_, "body": args.body}
    else:
        entry = args.body
    try:
        _insert_log(con, args.id, entry)
    except ValueError as e:
        sys.exit(f"✗ invalid date: {e}")
    # auto TODO -> DOING (when no --date, "I logged something" implies "I'm working on it")
    # backfilling history (--date) does not change status; --keep-status explicitly disables
    auto_progress_hint = ""
    if not getattr(args, "keep_status", False) and not date:
        row = con.execute("SELECT status FROM node WHERE id = ?", (args.id,)).fetchone()
        if row and row["status"] == "TODO":
            con.execute("UPDATE node SET status = 'DOING' WHERE id = ?", (args.id,))
            auto_progress_hint = " (status: TODO → DOING)"
    con.commit()
    print(f"✓ log added to #{args.id}{auto_progress_hint}")


def _ids_list(args):
    """argparse compat: if args.ids (list, nargs='+') is set use it, else fall back to [args.id] (older type=int)."""
    if hasattr(args, "ids") and args.ids:
        return args.ids
    return [args.id]


def cmd_done(args, con):
    _bulk_status_change(con, args, "DONE", close=True)


def cmd_defer(args, con):
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    try:
        when = _norm_sched(args.date)
    except ValueError as e:
        sys.exit(f"✗ {e}")
    for nid in ids:
        con.execute(
            "UPDATE node SET status = 'LATER', scheduled_at = ? WHERE id = ?",
            (when, nid),
        )
    con.commit()
    for nid in ids:
        out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " → LATER, scheduled " + _c(_sched_display(when), "planned"))


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


def cmd_start(args, con):
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    # --at: backfill past start time. None -> NOW
    try:
        ts = _resolve_at_ts(getattr(args, "at", None))
    except ValueError as e:
        sys.exit(f"✗ {e}")
    for nid in ids:
        con.execute("UPDATE node SET status = 'DOING' WHERE id = ?", (nid,))
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, 'CLOCK_IN')", (nid, ts))
    con.commit()
    note = f" @{ts[11:16]}" if getattr(args, "at", None) else ""
    for nid in ids:
        print(f"✓ #{nid} → DOING, clocked in{note}")


def cmd_stop(args, con):
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    # --at: backfill past stop time (must be later than the matching CLOCK_IN)
    try:
        stop_ts = _resolve_at_ts(getattr(args, "at", None))
    except ValueError as e:
        sys.exit(f"✗ {e}")
    for nid in ids:
        row = con.execute(
            "SELECT logged_at FROM log WHERE node_id = ? AND body = 'CLOCK_IN' ORDER BY id DESC LIMIT 1",
            (nid,),
        ).fetchone()
        if not row:
            sys.exit(f"✗ no open CLOCK_IN for #{nid}")
        started = datetime.fromisoformat(row["logged_at"])
        stopped = datetime.fromisoformat(stop_ts)
        if stopped < started:
            sys.exit(f"✗ --at {stop_ts} is earlier than CLOCK_IN {row['logged_at']} (#{nid})")
        mins = max(1, int((stopped - started).total_seconds() / 60))
        con.execute(
            "INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, ?)",
            (nid, stop_ts, f"CLOCK_OUT elapsed={mins}min (from {row['logged_at']})"),
        )
        print(f"✓ #{nid} stopped, elapsed {mins} min")
    con.commit()


def cmd_spent(args, con):
    """Record a past time spent without opening a live CLOCK pair (retrospective entries).
    wl spent <id> 45            45 minutes (default: CLOCK_IN = NOW - 45m, CLOCK_OUT = NOW)
    wl spent <id> 45 --at 14:30  specify end time (CLOCK_IN = at - 45m, CLOCK_OUT = at)
    wl spent <id> 1h30m          supports 1h / 30m / 1h30m
    """
    import re as _re
    nid = args.id
    if not _node_exists(con, nid):
        sys.exit(f"✗ node #{nid} not found")
    # parse duration: 1h30m / 90m / 90 (bare number = minutes)
    s = args.duration.strip().lower()
    mins = 0
    m = _re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", s)
    if m and (m.group(1) or m.group(2)):
        mins = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    elif _re.fullmatch(r"\d+", s):
        mins = int(s)
    else:
        sys.exit(f"✗ invalid duration '{s}': supported formats: 90 / 90m / 1h30m / 2h")
    if mins <= 0:
        sys.exit("✗ duration must be > 0")
    try:
        end_ts = _resolve_at_ts(getattr(args, "at", None))
    except ValueError as e:
        sys.exit(f"✗ {e}")
    end_dt = datetime.fromisoformat(end_ts)
    from datetime import timedelta as _td
    start_dt = end_dt - _td(minutes=mins)
    start_ts = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, 'CLOCK_IN')", (nid, start_ts))
    con.execute(
        "INSERT INTO log (node_id, logged_at, body) VALUES (?, ?, ?)",
        (nid, end_ts, f"CLOCK_OUT elapsed={mins}min (from {start_ts})"),
    )
    con.commit()
    print(f"✓ #{nid} spent {mins}min ({start_ts[11:16]} → {end_ts[11:16]})")


def cmd_link(args, con):
    if not args.vault_doc or not args.vault_doc.strip():
        sys.exit("✗ vault_doc cannot be empty")
    args.vault_doc = args.vault_doc.strip()
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    for nid in ids:
        con.execute("INSERT OR IGNORE INTO link (node_id, vault_doc) VALUES (?, ?)", (nid, args.vault_doc))
    con.commit()
    for nid in ids:
        out(_c("✓", "done") + " " + _c(f"#{nid}", "id") + " " + _c(f"linked → [[{args.vault_doc}]]"))


def cmd_set(args, con):
    if not _node_exists(con, args.id):
        sys.exit(f"✗ node #{args.id} not found")
    if not args.key or not args.key.strip():
        sys.exit("✗ prop key cannot be empty")
    args.key = args.key.strip()
    _upsert_prop(con, args.id, args.key, args.value)
    con.commit()
    print(f"✓ #{args.id} {args.key}={args.value}")


def cmd_active(args, con):
    """List tasks running right now: tasks with an open CLOCK_IN (actually timing).
    Each task shows: id / title / current-session elapsed + today's total + latest log (context).

    Use cases:
    - Before lunch / a meeting, glance at which task is timing right now
    - Late in the day, find a task you forgot to stop and wrap up with wl stop <id>
    - When juggling several tasks, confirm current focus

    Difference from wl day: wl day = full single-day view (done / not-yet-started included); wl active = what's timing right now.
    `-q` skips total / log detail, listing only id + elapsed.
    """
    from datetime import datetime as _dt, date as _date

    rows = con.execute("""
        SELECT l.node_id, l.logged_at, n.title, n.status, n.priority
        FROM log l JOIN node n ON l.node_id = n.id
        WHERE l.body = 'CLOCK_IN'
          AND NOT EXISTS (
              SELECT 1 FROM log l2
              WHERE l2.node_id = l.node_id AND l2.id > l.id AND l2.body LIKE 'CLOCK_OUT%'
          )
        ORDER BY l.logged_at DESC
    """).fetchall()

    if not rows:
        out(_c("(no active task right now; use wl start <id> to start timing, wl day for today's progress)", "meta"))
        return

    brief = getattr(args, "brief", False)
    now = _dt.now()
    today = _date.today().isoformat()
    full = _log_full(args)
    for r in rows:
        started = _dt.fromisoformat(r["logged_at"])
        mins = int((now - started).total_seconds() / 60)
        pri = (_c(f"[#{r['priority']}]", _PRI_STYLE.get(r["priority"])) + " ") if r["priority"] else ""
        # head: id + priority + title + current session
        head_tail = "" if brief else " " + _c(f"({mins}min, since {r['logged_at'][11:16]})", "meta")
        out(_c("⏱", "clock") + " " + _c(f"#{r['node_id']}", "id") + " " + pri + _c(r["title"]) + head_tail)
        if brief:
            continue
        # today's CLOCK total + log progress section (helps decide "continue or stop")
        today_clock = con.execute(
            "SELECT body FROM log WHERE node_id = ? AND date(logged_at) = ? AND body LIKE 'CLOCK_OUT elapsed=%'",
            (r["node_id"], today),
        ).fetchall()
        total_min = mins  # includes current open session
        import re as _re
        for row in today_clock:
            m = _re.search(r"elapsed=(\d+)min", row["body"])
            if m:
                total_min += int(m.group(1))
        out("    " + _c(f"today's total {total_min}min ({total_min // 60}h{total_min % 60}m), includes current session", "meta"))
        # latest non-CLOCK log (oneline truncated)
        last = con.execute(
            "SELECT body FROM log WHERE node_id = ? AND body NOT LIKE 'CLOCK\\_%' ESCAPE '\\' "
            "ORDER BY id DESC LIMIT 1", (r["node_id"],),
        ).fetchone()
        if last:
            body_one = _truncate_log_body(last["body"], indent_cols=14, full=full)
            out("    " + _c(f"latest log: {body_one}", "meta"))


def cmd_wait(args, con):
    """Mark WAIT status (blocked on others / external input). Optional --note adds a log explaining what we're waiting on.
    If the task has an open CLOCK_IN, auto-emits CLOCK_OUT (WAIT = suspended, no longer timing)."""
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    for nid in ids:
        # if there's an open CLOCK_IN, close it
        row = con.execute(
            "SELECT id, logged_at FROM log WHERE node_id = ? AND body = 'CLOCK_IN' "
            "AND NOT EXISTS (SELECT 1 FROM log l2 WHERE l2.node_id = log.node_id AND l2.id > log.id AND l2.body LIKE 'CLOCK_OUT%') "
            "ORDER BY id DESC LIMIT 1",
            (nid,),
        ).fetchone()
        if row:
            started = datetime.fromisoformat(row["logged_at"])
            mins = max(1, int((datetime.now() - started).total_seconds() / 60))
            con.execute(
                "INSERT INTO log (node_id, body) VALUES (?, ?)",
                (nid, f"CLOCK_OUT elapsed={mins}min (from {row['logged_at']}) [auto by wait]"),
            )
        con.execute("UPDATE node SET status = 'WAIT' WHERE id = ?", (nid,))
        if args.note:
            _insert_log(con, nid, f"WAIT: {args.note}")
    con.commit()
    for nid in ids:
        msg = f"✓ #{nid} → WAIT"
        if args.note:
            msg += f" ({args.note})"
        print(msg)


def cmd_reopen(args, con):
    """Undo DONE/CANCELED: back to TODO, clear closed_at. Common when a task was mistakenly closed."""
    _bulk_status_change(con, args, "TODO", reopen=True)


def cmd_cancel(args, con):
    """Mark CANCELED + write closed_at. Parallel to done semantically but different status (dropped / not doing).
    Different from `wl set <id> status CANCELED`: set writes the prop table, cancel changes node.status."""
    _bulk_status_change(con, args, "CANCELED", close=True)


def cmd_show(args, con):
    # multiple ids: show each in turn, blank-line separated; same rendering
    ids = _ids_list(args)
    for i, nid in enumerate(ids):
        if i > 0:
            print()
        args.id = nid
        _show_one(args, con)


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


def _project_members(con, proj_id):
    """Set of task/meetlog/habit ids linked to a project: structural children (parent) + shared semantic tags"""
    ids = set()
    proj_tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (proj_id,))} - GENERIC_TAGS
    for r in con.execute(
        "SELECT id FROM node WHERE parent_id = ? AND kind IN ('task','meetlog','habit')", (proj_id,)
    ):
        ids.add(r["id"])
    if proj_tags:
        qm = ",".join("?" * len(proj_tags))
        for r in con.execute(
            f"SELECT DISTINCT n.id FROM node n JOIN tag t ON n.id = t.node_id "
            f"WHERE t.tag IN ({qm}) AND n.kind IN ('task','meetlog','habit')",
            list(proj_tags),
        ):
            ids.add(r["id"])
    return ids


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


def _node_line(con, n, *, indent="", done=False, show_kind=True, tags=False, planned=False, clock=True, sched=False, hl=None):
    """Unified node-line rendering (sole source per DESIGN.md §6).

    Format: <indent><marker> [#pri] #<id> [kind] <title>[ ·planned][ @sched][ [Xh Ym]][ :tags:]
    Everywhere that "lists tasks" goes through this; do not roll your own. hl=query highlights matches in title (used by find).
    clock defaults True: shows total duration [Xh Ym] when there's a CLOCK or log span; 0 hides it.
    """
    mk = "✓" if done else _status_marker(n["status"])
    marker = _c(mk, "done" if done else _STATUS_STYLE.get(n["status"], "todo"))
    if n["priority"]:
        pri = _c(f"[#{n['priority']}]", _PRI_STYLE.get(n["priority"]))
    else:
        pri = "   "  # no priority: spaces as placeholder to align with [#A], no collision with marker
    kind = (_c(f"[{n['kind']}]", "kind") + " ") if (show_kind and n["kind"] != "task") else ""
    nid = _c(f"#{n['id']}", "id")
    title = _hl(n["title"], hl) if hl else _c(n["title"])
    s = f"{indent}{marker} {pri} {nid} {kind}{title}"
    if planned and _has_tag(con, n["id"], "planned"):
        s += " " + _c("·planned", "planned")
    if sched and n["scheduled_at"]:
        s += " " + _c("@" + _sched_display(n["scheduled_at"]), "planned")
    if clock:
        cm = _node_clock_min(con, n["id"])
        d = _fmt_dur(cm)
        if d:
            s += " " + _c(d, "clock")
    if tags:
        tl = _node_tags(con, n["id"])
        if tl:
            s += "  " + _c(f":{':'.join(tl)}:", "tag")
    return s


def _tree_by(con, by):
    """Flat 2-level view, regrouped by dimension (avoids deep time-layered nesting)."""
    if by == "tag":
        tags = [r["tag"] for r in con.execute("SELECT DISTINCT tag FROM tag ORDER BY tag")]
        sem = [t for t in tags if t not in GENERIC_TAGS]
        if not sem:
            print("(no semantic tags)")
            return
        for tag in sem:
            rows = con.execute(
                "SELECT n.* FROM node n JOIN tag t ON n.id = t.node_id WHERE t.tag = ? "
                "ORDER BY n.priority NULLS LAST, n.id",
                (tag,),
            ).fetchall()
            out(_c(f"#{tag}", "tag") + "  " + _c(f"({len(rows)})", "meta"))
            for n in rows:
                out(_node_line(con, n))

    elif by == "project":
        projects = con.execute(
            f"SELECT * FROM node WHERE kind = 'project' {_ORDER_BY_PRI_ID}"
        ).fetchall()
        if not projects:
            print("(no project nodes)")
            return
        claimed = set()
        for proj in projects:
            proj_tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (proj["id"],))} - GENERIC_TAGS
            ids = set()
            # (a) structural children
            for r in con.execute("SELECT id FROM node WHERE parent_id = ?", (proj["id"],)):
                ids.add(r["id"])
            # (b) task/meetlog/habit sharing a semantic tag
            if proj_tags:
                qm = ",".join("?" * len(proj_tags))
                for r in con.execute(
                    f"SELECT DISTINCT n.id FROM node n JOIN tag t ON n.id = t.node_id "
                    f"WHERE t.tag IN ({qm}) AND n.kind IN ('task','meetlog','habit')",
                    list(proj_tags),
                ):
                    ids.add(r["id"])
            pri = (" " + _c(f"[#{proj['priority']}]", _PRI_STYLE.get(proj["priority"]))) if proj["priority"] else ""
            out("▸ " + _c(f"#{proj['id']}", "id") + pri + " " + _c(proj["title"], "header") + "  " + _c(f"({len(ids)})", "meta"))
            for nid in sorted(ids):
                n = con.execute("SELECT * FROM node WHERE id = ?", (nid,)).fetchone()
                claimed.add(nid)
                out(_node_line(con, n))
            if not ids:
                out("    " + _c("(no linked tasks)", "meta"))
        # orphans: task/meetlog/habit not attached to any project
        orphans = con.execute(
            f"SELECT * FROM node WHERE kind IN ('task','meetlog','habit') {_ORDER_BY_PRI_ID}"
        ).fetchall()
        orphans = [n for n in orphans if n["id"] not in claimed]
        if orphans:
            out("▸ " + _c("(unassigned)", "header") + "  " + _c(f"({len(orphans)})", "meta"))
            for n in orphans:
                out(_node_line(con, n))

    elif by == "direction":
        for direction in ("work", "personal"):
            rows = con.execute(
                "SELECT n.* FROM node n JOIN tag t ON n.id = t.node_id WHERE t.tag = ? "
                "AND n.kind IN ('task','meetlog','habit','project') "
                "ORDER BY n.priority NULLS LAST, n.id",
                (direction,),
            ).fetchall()
            out(_c(f"[{direction}]", "header") + " " + _c(f"({len(rows)})", "meta"))
            for n in rows:
                out(_node_line(con, n))


def cmd_tree(args, con):
    inc_cancel = getattr(args, "show_canceled", False)
    log_tail = _resolve_log_tail(args, _is_brief(args, "no_logs"), default_tail=3)
    if args.by:
        _tree_by(con, args.by)
        return
    full = _log_full(args)
    if args.root is None and args.kind is None and args.depth is None:
        # bare wl tree: areas one level + timeline up to today
        _print_default_tree(con, include_canceled=inc_cancel, log_tail=log_tail, full=full)
        return
    if args.root is not None:
        # expand subtree from a specified node as root (no longer requires parent_id IS NULL)
        root = con.execute("SELECT * FROM node WHERE id = ?", (args.root,)).fetchone()
        if not root:
            sys.exit(f"✗ node #{args.root} not found")
        roots = [root]
    else:
        root_sql = "SELECT * FROM node WHERE parent_id IS NULL"
        params_root = []
        if args.kind:
            root_sql += " AND kind = ?"
            params_root.append(args.kind)
        if not inc_cancel:
            frag, p = _status_filter_sql(include_canceled=False)
            if frag:
                root_sql += " AND " + frag
                params_root.extend(p)
        roots = list(con.execute(root_sql, params_root))

    if not roots:
        print("(no root nodes)")
        return

    # default depth limit to avoid flooding: full tree default 2 (area->project / year->quarter overview), --root default 3 (one extra level for drill-down)
    max_depth = args.depth if args.depth is not None else (3 if args.root is not None else 2)
    for root in roots:
        _print_tree(con, root, depth=0, max_depth=max_depth,
                    include_canceled=inc_cancel, log_tail=log_tail, full=full)


def _ancestors_chain(con, node_id):
    """Return the path list[Row] from the top-level root to node (inclusive)."""
    chain = []
    cur = con.execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()
    if not cur:
        return chain
    chain.append(cur)
    while cur["parent_id"]:
        cur = con.execute("SELECT * FROM node WHERE id = ?", (cur["parent_id"],)).fetchone()
        if not cur:
            break
        chain.append(cur)
    return list(reversed(chain))


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


_TIME_KINDS = {"lifetime", "decade", "year", "quarter", "month", "week", "day"}


def _tree_children(con, node, include_canceled=False):
    """Children ordering: time-kinds ascending by title (date); others by priority -> id. CANCELED excluded by default."""
    sql = "SELECT * FROM node WHERE parent_id = ?"
    sql_params = [node["id"]]
    frag, p = _status_filter_sql(include_canceled=include_canceled)
    if frag:
        sql += " AND " + frag
        sql_params.extend(p)
    rows = list(con.execute(sql, sql_params))

    def key(r):
        if r["kind"] in _TIME_KINDS:
            return (0, r["title"], 0)
        pr = {"A": 0, "B": 1, "C": 2}.get(r["priority"], 3)
        return (1, pr, r["id"])

    return sorted(rows, key=key)


def _print_day_activity(con, day_node, depth, max_depth, *, include_canceled=False, log_tail=3, full=False):
    """For a day node in the tree view, expand that day's activity: tasks with logs + that day's logs (today only, not others).
    log_tail: None = full expansion / 0 = no log expansion / N = latest N per task (default 3, middle elided to keep wl tree compact)."""
    from collections import OrderedDict

    target = day_node["title"][:10]
    cfrag, cparams = _status_filter_sql(include_canceled=include_canceled, col="node.status")
    cancel_sql = (" AND " + cfrag) if cfrag else ""
    rows = con.execute(
        rf"""SELECT log.node_id, log.body, node.title, node.status, node.priority, node.kind
            FROM log JOIN node ON log.node_id = node.id
            WHERE date(log.logged_at) = ?
              AND log.body NOT LIKE 'CLOCK\_%' ESCAPE '\'
              AND node.kind IN ('task', 'habit', 'meetlog')
              {cancel_sql}
            ORDER BY log.node_id""",
        [target] + cparams,
    ).fetchall()
    tasks = OrderedDict()
    for r in rows:
        tasks.setdefault(r["node_id"], {"r": r, "logs": []})["logs"].append(r["body"])
    ind = "  " * (depth + 1)
    for nid, t in tasks.items():
        n = t["r"]
        # habit with a log today = done today (render-layer smarts, same as _render_day_group)
        if n["kind"] == "habit" and t["logs"]:
            mk = _c("[x]", "done")
        else:
            mk = _c(_status_marker(n["status"]), _STATUS_STYLE.get(n["status"], "todo"))
        pri = (_c(f"[#{n['priority']}]", _PRI_STYLE.get(n["priority"])) + " ") if n["priority"] else ""
        out(ind + mk + " " + _c(f"#{nid}", "id") + " " + pri + _c(n["title"]))
        if log_tail != 0 and (max_depth is None or depth + 1 < max_depth):
            logs = t["logs"]
            shown = logs if log_tail is None else logs[-log_tail:]
            omitted = 0 if log_tail is None else max(0, len(logs) - log_tail)
            if omitted:
                out("  " * (depth + 2) + _c(f"· … ({omitted} earlier logs elided)", "meta"))
            for body in shown:
                indent = "  " * (depth + 2)
                shown_body = _truncate_log_body(body, indent_cols=len(indent) + 2, full=full)
                out(indent + _c("· " + shown_body, "meta"))


def _print_default_tree(con, *, include_canceled=False, log_tail=3, full=False):
    """Default wl tree: areas one level (area name only) + timeline expanded up to today (year -> quarter -> month -> week -> today + today's activity).
    To drill into an area's projects use --root <area>; for other days use --root <week/month>. CANCELED excluded by default."""
    from datetime import date

    life = con.execute("SELECT * FROM node WHERE kind = 'lifetime' ORDER BY id LIMIT 1").fetchone()
    has_day = con.execute("SELECT 1 FROM node WHERE kind = 'day' LIMIT 1").fetchone()
    has_month = con.execute("SELECT 1 FROM node WHERE kind = 'month' LIMIT 1").fetchone()
    if not life and not has_day and not has_month:
        print("(no root nodes)")
        return
    base = 0
    if life:
        out(_node_line(con, life))
        base = 1

    # timeline -> path to today (year -> quarter -> month -> week -> day) + today's activity; if no day node today, fall back to the latest month
    today = date.today().isoformat()
    dayn = con.execute(
        "SELECT * FROM node WHERE kind = 'day' AND title LIKE ? ORDER BY id LIMIT 1", (today + "%",)
    ).fetchone()
    if dayn:
        chain = [n for n in _ancestors_chain(con, dayn["id"]) if n["kind"] != "lifetime"]
        for d, n in enumerate(chain):
            out(_node_line(con, n, indent="  " * (base + d), sched=True))
        # today: only tasks, no log expansion (logs are for drill-down: wl day / wl tree --root <day> --depth big)
        day_depth = base + len(chain) - 1
        _print_day_activity(con, dayn, day_depth, max_depth=day_depth + 1, log_tail=log_tail, full=full)
    else:
        mon = con.execute("SELECT * FROM node WHERE kind = 'month' ORDER BY title DESC LIMIT 1").fetchone()
        if mon:
            out(_node_line(con, mon, indent="  " * base, sched=True))

    # areas one level only (no project expansion)
    if life:
        for a in _tree_children(con, life, include_canceled=include_canceled):
            if a["kind"] == "area":
                out(_node_line(con, a, indent="  " * base, sched=True))


def _print_tree(con, node, depth, max_depth, *, include_canceled=False, log_tail=3, full=False):
    out(_node_line(con, node, indent="  " * depth, sched=True))
    if max_depth is not None and depth >= max_depth:
        return
    if node["kind"] == "day":  # day has no real children (empty); expand today's log activity instead
        _print_day_activity(con, node, depth, max_depth,
                            include_canceled=include_canceled, log_tail=log_tail, full=full)
        return
    for c in _tree_children(con, node, include_canceled=include_canceled):
        _print_tree(con, c, depth + 1, max_depth,
                    include_canceled=include_canceled, log_tail=log_tail, full=full)


_BUCKET_ORDER = ["work", "personal", "other"]
_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _cn_weekday(date_str):
    """YYYY-MM-DD -> weekday name (computed, not stored)"""
    from datetime import date

    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        return _WEEKDAY_NAMES[date(y, m, d).weekday()]
    except (ValueError, IndexError):
        return ""


def _date_label(con, target):
    """Label (holiday/vacation/working-day-swap) for the date from date_meta, or None."""
    r = con.execute("SELECT label FROM date_meta WHERE date = ?", (target,)).fetchone()
    return r["label"] if r else None


def _node_bucket(con, nid):
    """Bucket a node into work / personal / other by work/personal tag."""
    tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (nid,))}
    if "work" in tags:
        return "work"
    if "personal" in tags:
        return "personal"
    return "other"


def _node_project(con, nid):
    """Return the project ancestor (id, title) of a node, or (None, '(unassigned)') if none."""
    for p in _ancestors_chain(con, nid):
        if p["kind"] == "project":
            return p["id"], p["title"]
    return None, "(unassigned)"


_PLAN_ORDER = ["planned", "unplanned", "unplanned (untagged)"]
_PRI_GROUP_ORDER = ["P0", "P1", "P2", "—"]
_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _sched_fires(on_date, rrule, target):
    """Whether this sched row fires on target (YYYY-MM-DD). Rules:
    - daily: every day
    - weekly:Mon,Wed,Fri | 1-7 | -1..-7: specific weekday(s) (number 1=Mon..7=Sun, -1=Sun..-7=Mon)
    - monthly:5 | 5,15,25 | -1: day of month; -N counts from month end (-1=last day)
    - quarterly:M-D | -1: M-th month in quarter (1-3), D-th day; -1 = quarter end (3/31, 6/30, 9/30, 12/31)
    - yearly:03-21 | -1: every year MM-DD; -1 = year end (12-31)
    """
    from datetime import date
    import calendar

    if on_date:
        return on_date == target
    if not rrule:
        return False
    rule = rrule.strip()
    if rule == "daily":
        return True
    y, m, d = (int(x) for x in target.split("-"))
    if rule.startswith("weekly:"):
        days = [x.strip() for x in rule[len("weekly:"):].split(",") if x.strip()]
        return _WEEKDAY_ABBR[date(y, m, d).weekday()] in days
    if rule.startswith("monthly:"):
        tokens = [x.strip() for x in rule[len("monthly:"):].split(",") if x.strip()]
        last = calendar.monthrange(y, m)[1]
        for tok in tokens:
            n = int(tok)
            target_day = n if n > 0 else last + n + 1   # -1 → last, -2 → last-1
            if 1 <= target_day <= last and target_day == d:
                return True
        return False
    if rule.startswith("quarterly:"):
        tokens = [x.strip() for x in rule[len("quarterly:"):].split(",") if x.strip()]
        quarter_month_idx = (m - 1) % 3 + 1   # month offset within the quarter: 1/2/3
        last = calendar.monthrange(y, m)[1]
        for tok in tokens:
            if tok == "-1":
                # quarter end: last day of the quarter's 3rd month (3/6/9/12)
                if quarter_month_idx == 3 and d == last:
                    return True
                continue
            mm, dd = (int(x) for x in tok.split("-"))
            if mm == quarter_month_idx and dd == d and 1 <= dd <= last:
                return True
        return False
    if rule.startswith("yearly:"):
        tokens = [x.strip() for x in rule[len("yearly:"):].split(",") if x.strip()]
        md = f"{m:02d}-{d:02d}"
        for tok in tokens:
            if tok == "-1" and md == "12-31":
                return True
            if tok == md:
                return True
        return False
    return False


def _scheduled_node_ids(con, target):
    """Set of node_ids hit by a schedule on target (forward planning -> planned bucket)."""
    ids = set()
    for r in con.execute("SELECT node_id, on_date, rrule FROM sched"):
        if _sched_fires(r["on_date"], r["rrule"], target):
            ids.add(r["node_id"])
    return ids


def _node_plan(con, nid, sched_ids):
    """Derive planned/unplanned: schedule-hit = planned; otherwise check transitional planned/unplanned tag; neither -> unplanned (untagged)."""
    if nid in sched_ids:
        return "planned"
    tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (nid,))}
    if "planned" in tags:
        return "planned"
    if "unplanned" in tags:
        return "unplanned"
    return "unplanned (untagged)"


def _sec_group(con, nid, n, by, sched_ids):
    """(key, display title) for the secondary group. by in project/priority/plan."""
    if by == "priority":
        label = {"A": "P0", "B": "P1", "C": "P2"}.get(n["priority"], "—")
        return label, label
    if by == "plan":
        label = _node_plan(con, nid, sched_ids)
        return label, label
    pid, ptitle = _node_project(con, nid)
    return (pid if pid is not None else ptitle), ptitle


def _sec_sort_key(by):
    if by == "priority":
        return lambda lbl: _PRI_GROUP_ORDER.index(lbl) if lbl in _PRI_GROUP_ORDER else 99
    if by == "plan":
        return lambda lbl: _PLAN_ORDER.index(lbl) if lbl in _PLAN_ORDER else 99
    return None


def _render_day_group(con, items, by="plan", sched_ids=frozenset(), log_tail=None, full=False):
    """Render a day: items = {nid: {"node": row(title/status/priority), "logs": [body...]}}.
    Layout: bucket -> (plan/project/priority) -> task -> logs (indented). A task with no log but on a schedule is marked "planned·not-done".
    log_tail: None = full / 0 = no expansion / N = latest N per task.
    full: True keeps body untruncated (default truncates to one line by terminal width)."""
    from collections import OrderedDict

    buckets = OrderedDict()
    for nid, it in items.items():
        bucket = _node_bucket(con, nid)
        key, title = _sec_group(con, nid, it["node"], by, sched_ids)
        b = buckets.setdefault(bucket, OrderedDict())
        g = b.setdefault(key, {"title": title, "tasks": OrderedDict()})
        g["tasks"][nid] = it

    sortk = _sec_sort_key(by)
    for bucket in sorted(buckets, key=lambda x: _BUCKET_ORDER.index(x) if x in _BUCKET_ORDER else 99):
        out("  " + _c(bucket, "header"))
        groups = buckets[bucket].items()
        if sortk:
            groups = sorted(groups, key=lambda kv: sortk(kv[1]["title"]))
        for _, g in groups:
            out("    ▸ " + _c(g["title"], "kind"))
            for nid, it in g["tasks"].items():
                n = it["node"]
                logs = it["logs"]
                # habit with a log today = done today (render-layer smarts; resets next day; DB untouched)
                if n["kind"] == "habit" and logs:
                    mk = _c("[x]", "done")
                else:
                    mk = _c(_status_marker(n["status"]), _STATUS_STYLE.get(n["status"], "todo"))
                pri = (_c(f"[#{n['priority']}]", _PRI_STYLE.get(n["priority"])) + " ") if n["priority"] else ""
                hint = ""
                if not logs:
                    hint = _c("  «planned·not-done»", "planned")
                elif log_tail == 0:
                    # compact mode: don't expand body, attach a count hint after the title line
                    hint = _c(f"  ({len(logs)} log)", "meta")
                # total duration (CLOCK union log span); see _node_clock_min docstring
                dur = _fmt_dur(_node_clock_min(con, nid))
                dur_str = (" " + _c(dur, "clock")) if dur else ""
                out("      " + mk + " " + _c(f"#{nid}", "id") + " " + pri + _c(n["title"]) + dur_str + hint)
                if log_tail == 0:
                    continue
                bodies = logs if log_tail is None else logs[-log_tail:]
                if log_tail is not None and len(logs) > log_tail:
                    out("        " + _c(f"· … ({len(logs) - log_tail} earlier logs elided)", "meta"))
                # log body rendering: indent 8 + "· " 2 = 10 cols, remaining width-10-2 for one line
                for body in bodies:
                    shown = _truncate_log_body(body, indent_cols=10, full=full)
                    out("        " + _c("· " + shown, "meta"))


def cmd_day(args, con):
    """Reproduce a single day's progress (default today): bucket by work/personal -> project -> task -> that day's logs.
    Driven by log dates (not the day node), so it works for historical days too."""
    from datetime import date as _date

    if args.date:
        try:
            target = _resolve_concrete_date(args.date)
        except ValueError:
            sys.exit(f"✗ invalid date '{args.date}' (use YYYY-MM-DD / today / yesterday / day-before-yesterday / tomorrow / day-after-tomorrow)")
    else:
        target = _date.today().isoformat()
    day = con.execute(
        "SELECT * FROM node WHERE kind = 'day' AND title LIKE ? ORDER BY id LIMIT 1",
        (target + "%",),
    ).fetchone()
    # date context: date + auto-computed weekday + date_meta label (holiday/vacation/working-day swap)
    wd = _cn_weekday(target)
    label = _date_label(con, target)
    head = target + (f" {wd}" if wd else "") + (f" · {label}" if label else "")
    out(_c(head, "header"))
    # meta (stored as props on the day node): goal / recap / Top5; plus parent week node's overview
    if day:
        meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM prop WHERE node_id = ?", (day["id"],))}
        if meta.get("goal"):
            out(_c("  > 🎯 " + meta["goal"], "meta"))
        if meta.get("summary"):
            at = meta.get("summary_at")
            when = _c(f" (written at {at[5:16]})", "meta") if at else ""
            out(_c("  > Recap: " + meta["summary"], "meta") + when)
            # stale check: after recap, if there are new non-CLOCK logs today, prompt to rewrite
            if at:
                newer = con.execute(
                    "SELECT COUNT(*) FROM log WHERE logged_at > ? "
                    "AND substr(logged_at, 1, 10) = ? AND body NOT LIKE 'CLOCK_%'",
                    (at, target),
                ).fetchone()[0]
                if newer:
                    out(_c(f"  > ⚠ {newer} change(s) after recap; consider rewriting via wl recap", "doing"))
        if meta.get("top5"):
            out(_c("  > Top5: " + meta["top5"], "meta"))
        wk = con.execute("SELECT id FROM node WHERE id = ? AND kind = 'week'", (day["parent_id"],)).fetchone()
        if wk:
            ov = con.execute("SELECT value FROM prop WHERE node_id = ? AND key = 'overview'", (wk["id"],)).fetchone()
            if ov:
                out(_c("  > This week: " + ov["value"], "meta"))

    inc_cancel = getattr(args, "show_canceled", False)
    cfrag, cparams = _status_filter_sql(include_canceled=inc_cancel, col="node.status")
    cancel_sql = (" AND " + cfrag) if cfrag else ""
    rows = con.execute(
        rf"""SELECT log.node_id, log.logged_at, log.body,
                   node.title, node.status, node.priority, node.kind
            FROM log JOIN node ON log.node_id = node.id
            WHERE date(log.logged_at) = ?
              AND log.body NOT LIKE 'CLOCK\_%' ESCAPE '\'
              AND node.kind IN ('task', 'habit', 'meetlog')
              {cancel_sql}
            ORDER BY log.logged_at, log.node_id""",
        [target] + cparams,
    ).fetchall()

    # items: tasks with logs + tasks scheduled but with no log yet today (planned items visible ahead of time)
    items = {}
    for r in rows:
        items.setdefault(r["node_id"], {"node": r, "logs": []})["logs"].append(r["body"])
    sched_ids = _scheduled_node_ids(con, target)
    for nid in sched_ids:
        if nid not in items:
            nr = con.execute(
                "SELECT id AS node_id, title, status, priority, kind FROM node WHERE id = ?", (nid,)
            ).fetchone()
            if nr and nr["kind"] in ("task", "habit", "meetlog"):
                if not inc_cancel and nr["status"] == "CANCELED":
                    continue
                items[nid] = {"node": nr, "logs": []}

    if not items:
        out(_c(f"  (no log progress for {target}, and nothing planned)", "meta"))
        return

    # log_tail priority: --no-logs/--brief -> 0 / --all-logs -> None (full) /
    # --log-tail N -> N / default 3 (elide middle, only the end visible to keep wl day from blowing up on long logs)
    brief = _is_brief(args, "no_logs")
    log_tail = _resolve_log_tail(args, brief, default_tail=3)
    _render_day_group(con, items, by=getattr(args, "by", "plan"),
                      sched_ids=sched_ids, log_tail=log_tail,
                      full=_log_full(args))

    # bottom stats: per-status distribution + planned-not-done count + CLOCK time
    import re

    logged = {r["node_id"]: (r["status"] or "TODO") for r in rows}
    stats = {}
    for s in logged.values():
        stats[s] = stats.get(s, 0) + 1
    done = stats.get("DONE", 0)
    total = len(logged)
    planned_undone = sum(1 for nid in items if not items[nid]["logs"])
    parts = [f"{s} {stats[s]}" for s in ("DONE", "DOING", "TODO", "LATER", "WAIT", "DEFERRED", "CANCELED") if stats.get(s)]
    clock = con.execute(
        "SELECT body FROM log WHERE date(logged_at) = ? AND body LIKE 'CLOCK_OUT%'",
        (target,),
    ).fetchall()
    total_min = sum(int(m.group(1)) for r in clock if (m := re.search(r"elapsed=(\d+)min", r["body"])))
    print()
    line = f"  ── {target}: {done}/{total} tasks with progress"
    if parts:
        line += " · " + " · ".join(parts)
    if planned_undone:
        line += f" · planned·not-done {planned_undone}"
    if total_min:
        line += f" · CLOCK {total_min}min ({total_min // 60}h{total_min % 60}m)"
    out(_c(line, "meta"))


def _ensure_today_day(con):
    """Return today's day-node id; create one if missing (attach to current ISO week; unparented if week absent)."""
    from datetime import date

    today = date.today().isoformat()
    r = con.execute(
        "SELECT id FROM node WHERE kind='day' AND title LIKE ? ORDER BY id LIMIT 1", (today + "%",)
    ).fetchone()
    if r:
        return r["id"]
    iso = date.today().isocalendar()
    week_title = f"{iso[0]}-W{iso[1]:02d}"
    w = con.execute("SELECT id FROM node WHERE kind='week' AND title = ? LIMIT 1", (week_title,)).fetchone()
    cur = con.execute(
        "INSERT INTO node (parent_id, title, kind) VALUES (?, ?, 'day')",
        (w["id"] if w else None, today),
    )
    con.commit()
    return cur.lastrowid


def _set_prop(con, nid, key, value):
    _upsert_prop(con, nid, key, value)
    con.commit()


def _get_prop(con, nid, key):
    r = con.execute("SELECT value FROM prop WHERE node_id = ? AND key = ?", (nid, key)).fetchone()
    return r["value"] if r else None


def cmd_goal(args, con):
    """Shortcut to read/write today's goal: `wl goal` reads; `wl goal 'text'` writes. Today's day-node is auto-created if missing."""
    nid = _ensure_today_day(con)
    if not args.text:
        v = _get_prop(con, nid, "goal")
        out(v if v else _c("(no goal set for today)", "meta"))
        return
    _set_prop(con, nid, "goal", args.text)
    out(_c(f"✓ today's goal: {args.text}", "meta"))


def cmd_summary_prop(args, con):
    """Shortcut to read/write today's end-of-day recap. On write, stamps summary_at (YYYY-MM-DD HH:MM:SS),
    so we can later detect changes added after the recap (wl day shows a hint to rewrite)."""
    nid = _ensure_today_day(con)
    if not args.text:
        v = _get_prop(con, nid, "summary")
        if not v:
            out(_c("(no summary set for today)", "meta"))
            return
        at = _get_prop(con, nid, "summary_at")
        out(v + (_c(f"  (written at {at})", "meta") if at else ""))
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _upsert_prop(con, nid, "summary", args.text)
    _upsert_prop(con, nid, "summary_at", now)
    con.commit()
    out(_c(f"✓ today's summary (written at {now}): {args.text}", "meta"))


def _checkin_collect(con, args):
    """Collect today's habits to check in. Returns [{id, title, priority, kind, already}]."""
    from datetime import date as _d

    today = _d.today().isoformat()
    sched_ids = _scheduled_node_ids(con, today)
    kinds = {args.kind} if args.kind else {"habit"}
    if args.all_kinds:
        kinds = {"habit", "task", "meetlog"}

    rows = []
    for nid in sorted(sched_ids):
        n = con.execute("SELECT * FROM node WHERE id = ?", (nid,)).fetchone()
        if not n or n["kind"] not in kinds:
            continue
        if n["status"] == "CANCELED" and not getattr(args, "show_canceled", False):
            continue
        already = con.execute(
            "SELECT 1 FROM log WHERE node_id = ? AND date(logged_at) = ? "
            "AND body NOT LIKE 'CLOCK\\_%' ESCAPE '\\' LIMIT 1",
            (nid, today),
        ).fetchone()
        rows.append({
            "id": n["id"], "title": n["title"], "priority": n["priority"],
            "kind": n["kind"], "already": bool(already),
        })
    return rows, today, kinds


def _is_interactive_tty():
    """Whether we can run a raw-mode TUI: both stdin and stdout are TTYs. Used by tests via monkeypatch."""
    import sys
    return sys.stdin.isatty() and sys.stdout.isatty()


def _multi_select_tty(options, header):  # pragma: no cover -- TTY interactive, needs termios+os.read, manual smoke only
    """Terminal multi-select widget (rich.Live render, no misalignment): up/down moves cursor; space toggles; Enter confirms; q/Esc cancels.
    options: [(label, default_selected)]
    Returns: list of selected indices, or None (canceled).
    Requires rich available + both stdin/stdout are TTYs; otherwise returns None so caller can fall back."""
    import sys
    if not _RICH_AVAIL or not _is_interactive_tty():
        return None
    import os, termios, tty, select
    from rich.console import Console as _LiveConsole
    from rich.live import Live
    from rich.text import Text

    selected = [d for _, d in options]
    cursor = 0
    n = len(options)

    def make_view():
        # header may contain [style]..[/style] markup; from_markup parses; rich output handles \r\n
        t = Text.from_markup(header)
        t.append("\n")
        t.append("(up/down or j/k to move · space to toggle · Enter to confirm · q/Esc to cancel)\n\n",
                 style="dim")
        for i, (label, _) in enumerate(options):
            mark = "[x] " if selected[i] else "[ ] "
            pointer = "▸ " if i == cursor else "  "
            line = f"  {pointer}{mark}{label}\n"
            t.append(line, style="bold reverse" if i == cursor else None)
        return t

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    canceled = False
    # use a separate Console to avoid collision with wl's global _CONSOLE theme/highlight
    live_console = _LiveConsole(file=sys.stderr, force_terminal=True)
    try:
        # cbreak (not raw): disable echo + line buffer but keep ONLCR (\n auto-adds \r);
        # otherwise rich's \n won't return to col 0 and each line drifts right
        tty.setcbreak(fd)
        # important: use os.read(fd, ...) to bypass Python's sys.stdin buffer.
        # sys.stdin.read(1) would swallow the entire ESC[A 3-byte sequence; select would then
        # see no more data and misinterpret ESC as a single keypress -> exit (root cause of prior bug)
        def read_byte():
            return os.read(fd, 1).decode("utf-8", errors="replace")

        def peek_more(timeout):
            # check whether fd has more bytes ready (terminals emit ESC[A as 3 bytes nearly instantly)
            return bool(select.select([fd], [], [], timeout)[0])

        with Live(make_view(), console=live_console, refresh_per_second=30,
                  screen=False, transient=True) as live:
            while True:
                ch = read_byte()
                if ch == "\x1b":  # ESC or arrow sequence
                    if peek_more(0.05):  # more bytes already there = escape sequence
                        seq = os.read(fd, 2).decode("utf-8", errors="replace")
                        if seq == "[A":
                            cursor = (cursor - 1) % n
                        elif seq == "[B":
                            cursor = (cursor + 1) % n
                        # other arrows / Home / End: ignore
                    else:
                        canceled = True
                        break
                elif ch == " ":
                    selected[cursor] = not selected[cursor]
                elif ch in ("\r", "\n"):
                    break
                elif ch in ("q", "Q", "\x03", "\x04"):  # q / Ctrl-C / Ctrl-D
                    canceled = True
                    break
                elif ch in ("j", "J"):
                    cursor = (cursor + 1) % n
                elif ch in ("k", "K"):
                    cursor = (cursor - 1) % n
                live.update(make_view())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if canceled:
        return None
    return [i for i, s in enumerate(selected) if s]


def _checkin_linear(con, rows):
    """Per-item prompt fallback mode: y/n/note/q (works on non-TTY / piped input; also supports per-item note)."""
    pre_done = sum(1 for r in rows if r["already"])
    out(_c(f"{len(rows)} items to check in, {pre_done} already done:", "header"))
    out(_c("Input: [Enter]/y = check in · n = skip · q = quit · any other text = check in with that as note", "meta"))
    print()

    done_now = skipped = 0
    for r in rows:
        nid = r["id"]
        pri = f"[#{r['priority']}]" if r["priority"] else ""
        head = f"#{nid} {pri} {r['title']}".strip()
        if r["already"]:
            out(_c(f"  ✓ {head} (already done today)", "done"))
            continue
        try:
            ans = input(_c(f"  ▸ {head}\n    [y/n/note/q] > ", "header")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            out(_c("(interrupted; remaining tasks skipped)", "meta"))
            break
        if ans in ("q", "Q", "exit", "quit"):
            out(_c("(quit)", "meta"))
            break
        if ans in ("n", "N", "no", "skip"):
            skipped += 1
            out(_c(f"    ⏭ #{nid} skipped", "meta"))
            continue
        body = "✓ done" if ans in ("", "y", "Y", "yes") else ans
        _insert_log(con, nid, body)
        con.commit()
        done_now += 1
        marker = _c("    ✓", "done")
        if body == "✓ done":
            out(f"{marker} #{nid} checked in")
        else:
            out(f"{marker} #{nid} checked in: {_c(body, 'meta')}")
    print()
    out(_c(
        f"done {pre_done + done_now}/{len(rows)} · new this run {done_now}" +
        (f" · skipped {skipped}" if skipped else ""),
        "header"))


def cmd_checkin(args, con):
    """Interactive check-in for today's habits.
    Default: multi-select (up/down + space + Enter), pick all at once and check in.
    --linear: per-item prompt mode (allows per-item note; also the fallback for non-TTY / piped input)."""
    import sys

    rows, today, kinds = _checkin_collect(con, args)
    if not rows:
        out(_c(f"(no {'/'.join(kinds)} scheduled to check in for {today})", "meta"))
        return

    pending = [r for r in rows if not r["already"]]
    pre_done = len(rows) - len(pending)

    if not pending:
        out(_c(f"all {len(rows)}/{len(rows)} already checked in for {today} ✓", "done"))
        return

    if getattr(args, "linear", False) or not _is_interactive_tty():
        _checkin_linear(con, rows)
        return

    header = _c(f"{today} · pick habits done today (already checked in {pre_done}/{len(rows)})", "header")
    # default unselected for all (use space to toggle on what you did); intuitive: 'mark what I did' not 'unmark what I missed'
    options = [(f"#{r['id']} {r['title']}", False) for r in pending]
    chosen = _multi_select_tty(options, header)
    if chosen is None:
        out(_c("(canceled, no changes made)", "meta"))
        return

    for i in chosen:
        _insert_log(con, pending[i]["id"], "✓ done")
    con.commit()
    done_now = len(chosen)
    skipped = len(pending) - done_now
    out(_c(
        f"done {pre_done + done_now}/{len(rows)} · new this run {done_now}" +
        (f" · skipped {skipped}" if skipped else "") +
        " · for detailed notes use `wl tick <id> --note ...` or `wl checkin --linear`",
        "header"))


def cmd_unlog(args, con):
    """Delete log entries. Two usages:
       wl unlog 282                       delete by exact log.id (find id from wl show timeline)
       wl unlog --node 39                 delete the latest non-CLOCK log for that node today (undo a mistaken tick)
       wl unlog --node 39 --date yesterday delete the latest log for that node on that day
       wl unlog --node 39 --all           delete all non-CLOCK logs for that node that day
    """
    import re as _re

    log_id = getattr(args, "log_id", None)
    nid = getattr(args, "node", None)
    if (log_id is None) == (nid is None):
        sys.exit("✗ provide either positional <log_id> or --node <id>; pick one")

    if log_id is not None:
        row = con.execute("SELECT node_id, logged_at, body FROM log WHERE id = ?", (log_id,)).fetchone()
        if not row:
            sys.exit(f"✗ log #{log_id} not found")
        if _re.match(r"^CLOCK_(IN|OUT)", row["body"]):
            sys.exit(f"✗ log #{log_id} is a CLOCK event; use wl stop instead of unlog (to avoid breaking timing pairs)")
        con.execute("DELETE FROM log WHERE id = ?", (log_id,))
        con.commit()
        body_preview = row["body"][:60] + ("…" if len(row["body"]) > 60 else "")
        out(_c(f"✓ deleted log #{log_id} (node #{row['node_id']}, {row['logged_at']}): {body_preview}", "meta"))
        return

    # --node <id>: delete latest log for that day
    if not _node_exists(con, nid):
        sys.exit(f"✗ node #{nid} not found")
    date = getattr(args, "date", None)
    if date:
        try:
            date = _resolve_concrete_date(date)
        except ValueError:
            sys.exit(f"✗ invalid --date '{date}'")
    else:
        from datetime import date as _d
        date = _d.today().isoformat()

    sql = ("SELECT id, logged_at, body FROM log WHERE node_id = ? AND date(logged_at) = ? "
           "AND body NOT LIKE 'CLOCK\\_%' ESCAPE '\\' ORDER BY id DESC")
    if not args.all:
        sql += " LIMIT 1"
    rows = list(con.execute(sql, (nid, date)))
    if not rows:
        out(_c(f"(node #{nid} has no non-CLOCK logs on {date})", "meta"))
        return
    for r in rows:
        con.execute("DELETE FROM log WHERE id = ?", (r["id"],))
        body_preview = r["body"][:60] + ("…" if len(r["body"]) > 60 else "")
        out(_c(f"✓ deleted log #{r['id']} (node #{nid}, {r['logged_at']}): {body_preview}", "meta"))
    con.commit()


def cmd_relog(args, con):
    """Rewrite an existing log: body or timestamp.

       wl relog #L282 "fixed content"      positional = new body
       wl relog #L282 -m "fixed content"   -m explicit
       wl relog #L282 --at 14:30           only change time (same day HH:MM, date auto-prepended)
       wl relog #L282 --at 2026-05-30 14:30  full ts (YYYY-MM-DD or YYYY-MM-DD HH:MM)
       wl relog #L282                       no body/--at -> open $EDITOR to edit body

    Constraints:
    - Cannot edit CLOCK_IN/CLOCK_OUT logs (breaks timing stats; use wl stop --at to fix time)
    - Cannot move across nodes (that's unlog + log, not relog)
    """
    import re as _re

    log_id = args.log_id
    row = con.execute("SELECT id, node_id, logged_at, body FROM log WHERE id = ?", (log_id,)).fetchone()
    if not row:
        sys.exit(f"✗ log #{log_id} not found")
    if _re.match(r"^CLOCK_(IN|OUT)", row["body"]):
        sys.exit(f"✗ log #{log_id} is a CLOCK event; relog not allowed (use wl stop --at to fix time, or the source command to delete)")

    # body: positional or -m, mutually exclusive; both empty -> EDITOR (only when --at also missing)
    new_body = None
    if args.body and args.message:
        sys.exit("✗ positional body and -m/--message are mutually exclusive; pick one")
    if args.body:
        new_body = " ".join(args.body).strip()
    elif args.message:
        new_body = args.message.strip()

    # --at: accepts HH:MM (keep original date) / YYYY-MM-DD / YYYY-MM-DD HH:MM
    new_ts = None
    at = args.at
    if at:
        from datetime import datetime as _dt
        at = at.strip()
        orig_date = row["logged_at"][:10]
        try:
            if _re.fullmatch(r"\d{2}:\d{2}", at):
                _dt.strptime(at, "%H:%M")  # validate HH/MM range
                new_ts = f"{orig_date} {at}:00"
            elif _re.fullmatch(r"\d{4}-\d{2}-\d{2}", at):
                _dt.strptime(at, "%Y-%m-%d")
                orig_time = row["logged_at"][11:] or "00:00:00"
                new_ts = f"{at} {orig_time}"
            elif _re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", at):
                ts = at.replace("T", " ")
                if len(ts) == 16:
                    ts += ":00"
                _dt.strptime(ts, "%Y-%m-%d %H:%M:%S")
                new_ts = ts
            else:
                raise ValueError("format")
        except ValueError:
            sys.exit(f"✗ invalid --at '{at}': supported formats: HH:MM / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")

    if new_body is None and new_ts is None:
        # nothing given -> open EDITOR to edit body
        new_body = _edit_in_editor(row["body"], suffix=".log.txt")
        if new_body is None or new_body.strip() == row["body"]:
            out(_c("(no change; relog canceled)", "meta"))
            return
        new_body = new_body.strip()

    # prevent changing body to a CLOCK_* prefix (type collision)
    if new_body is not None and _re.match(r"^CLOCK_(IN|OUT)", new_body):
        sys.exit("✗ relog body cannot start with CLOCK_IN/CLOCK_OUT (to prevent forging timing events)")

    sets, params = [], []
    if new_body is not None:
        sets.append("body = ?")
        params.append(new_body)
    if new_ts is not None:
        sets.append("logged_at = ?")
        params.append(new_ts)
    params.append(log_id)
    con.execute(f"UPDATE log SET {', '.join(sets)} WHERE id = ?", params)
    con.commit()

    new_row = con.execute("SELECT logged_at, body FROM log WHERE id = ?", (log_id,)).fetchone()
    body_preview = new_row["body"][:60] + ("…" if len(new_row["body"]) > 60 else "")
    out(_c(f"✓ relog #{log_id} (node #{row['node_id']}, {new_row['logged_at']}): {body_preview}", "meta"))


def _edit_in_editor(initial_text, suffix=".txt"):
    """Open $EDITOR to edit a piece of text; return the new content, or None if canceled or unchanged."""
    import os
    import subprocess
    import tempfile

    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile("w+", suffix=suffix, delete=False) as f:
        f.write(initial_text)
        path = f.name
    try:
        rc = subprocess.call([editor, path])
        if rc != 0:
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def cmd_tick(args, con):
    """Quick check-in: add a log for today to one or more nodes (default body='✓ done', overridable with --note).
    --done also marks the node DONE. Bulk habit check-in: `wl tick 39 40 41 --note "..."`."""
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    # empty note (--note '') falls back to default; we don't allow inserting a truly empty log
    note = (args.note or "").strip()
    body = note if note else "✓ done"
    for nid in ids:
        _insert_log(con, nid, body)
        if args.done:
            con.execute(
                "UPDATE node SET status = 'DONE', closed_at = datetime('now','localtime') WHERE id = ?", (nid,)
            )
    con.commit()
    for nid in ids:
        out(_c(f"✓ #{nid} checked in", "meta") + (_c(" + DONE", "done") if args.done else ""))


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


def cmd_sched(args, con):
    """Forward planning: schedule a task to a specific day / recurrence. A scheduled task appears as 'planned' in wl day even with no log.
    Accepts multiple ids: wl sched 18 19 20 today (first N are ids; the trailing positional is the date)."""
    ids = _ids_list(args)
    _check_ids_exist(con, ids)
    if args.clear:
        for nid in ids:
            cur = con.execute("DELETE FROM sched WHERE node_id = ?", (nid,))
            out(_c(f"✓ #{nid} cleared {cur.rowcount} schedule entries", "meta"))
        con.commit()
        return
    if not args.when and not args.recur:
        # if multiple ids, show schedule for each (caters to single-id scenario)
        for nid in ids:
            rows = con.execute(
                "SELECT on_date, rrule FROM sched WHERE node_id = ? ORDER BY on_date NULLS LAST, rrule", (nid,)
            ).fetchall()
            if not rows:
                out(_c(f"#{nid} has no schedule", "meta"))
            for r in rows:
                out("  " + _c(f"#{nid} @" + (r["on_date"] or r["rrule"]), "planned"))
        return
    if args.recur:
        try:
            rule = _norm_rrule(args.recur)
        except ValueError as e:
            sys.exit(f"✗ {e}")
        for nid in ids:
            con.execute("INSERT INTO sched (node_id, rrule) VALUES (?, ?)", (nid, rule))
        con.commit()
        for nid in ids:
            out(_c(f"✓ #{nid} recurring schedule: {rule}", "meta"))
    if args.when:
        try:
            d = _resolve_concrete_date(args.when)
        except ValueError:
            sys.exit(f"✗ invalid date '{args.when}' (use YYYY-MM-DD / today / yesterday / tomorrow / day-after-tomorrow)")
        for nid in ids:
            con.execute("INSERT INTO sched (node_id, on_date) VALUES (?, ?)", (nid, d))
        con.commit()
        for nid in ids:
            out(_c(f"✓ #{nid} scheduled to {d}", "meta"))


def cmd_dateinfo(args, con):
    """Date metadata: holiday / vacation / working-day-swap label. Set one / batch-import a yearly holiday table / list."""
    if args.import_file:
        import json

        raw = sys.stdin.read() if args.import_file == "-" else Path(args.import_file).read_text(encoding="utf-8")
        data = json.loads(raw)  # {"2026-05-01": "Labor Day", ...}
        n = 0
        for d, label in data.items():
            con.execute("INSERT OR REPLACE INTO date_meta (date, label) VALUES (?, ?)", (d, label))
            n += 1
        con.commit()
        out(_c(f"✓ imported {n} date metadata entries", "meta"))
        return
    if args.date and args.label:
        con.execute("INSERT OR REPLACE INTO date_meta (date, label) VALUES (?, ?)", (args.date, args.label))
        con.commit()
        out(_c(f"✓ {args.date} {_cn_weekday(args.date)} · {args.label}", "meta"))
        return
    if args.date and args.clear:
        con.execute("DELETE FROM date_meta WHERE date = ?", (args.date,))
        con.commit()
        out(_c(f"✓ cleared metadata for {args.date}", "meta"))
        return
    # no args / only date: list
    if args.date:
        lbl = _date_label(con, args.date)
        out(_c(f"{args.date} {_cn_weekday(args.date)}" + (f" · {lbl}" if lbl else " (no label)"), "meta"))
    else:
        for r in con.execute("SELECT date, label FROM date_meta ORDER BY date"):
            out(_c(f"{r['date']} {_cn_weekday(r['date'])} · {r['label']}", "meta"))


def _collect_descendants(con, root_id):
    """Recursively collect all descendant ids of a node (excluding self)."""
    acc = []
    stack = [root_id]
    while stack:
        pid = stack.pop()
        children = con.execute("SELECT id FROM node WHERE parent_id = ?", (pid,)).fetchall()
        for c in children:
            acc.append(c["id"])
            stack.append(c["id"])
    return acc


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


def _has_tag(con, nid, tag):
    return con.execute("SELECT 1 FROM tag WHERE node_id = ? AND tag = ? LIMIT 1", (nid, tag)).fetchone() is not None


def _node_clock_min(con, nid):
    """Total minutes spent on this node, auto-combined: CLOCK_OUT elapsed sum union log timestamp span.
    Takes the greater so "no wl start/stop, only wl log" workflows still get a rough duration.
    Design choice: auto-compute, no explicit --duration field. Auto-calc surfaces drift; an explicit field
    rarely gets updated and pollutes upper-level aggregations.
    """
    import re as _re

    # 1. CLOCK_OUT elapsed total (precise, from wl start/stop)
    clock = 0
    for r in con.execute("SELECT body FROM log WHERE node_id = ? AND body LIKE 'CLOCK_OUT%'", (nid,)):
        m = _re.search(r"elapsed=(\d+)min", r["body"])
        if m:
            clock += int(m.group(1))

    # 2. ordinary log timestamp span (rough, max - min, excluding CLOCK_*)
    #    multiple logs at the same timestamp count as one "batch-backfilled instant", not duplicated
    rows = list(con.execute(
        "SELECT DISTINCT logged_at FROM log WHERE node_id = ? AND body NOT LIKE 'CLOCK_%' ESCAPE '\\' ORDER BY logged_at",
        (nid,),
    ))
    span = 0
    if len(rows) >= 2:
        try:
            from datetime import datetime
            first = datetime.fromisoformat(rows[0]["logged_at"])
            last = datetime.fromisoformat(rows[-1]["logged_at"])
            span = max(0, int((last - first).total_seconds() / 60))
        except (ValueError, TypeError):
            pass

    return max(clock, span)


def _fmt_dur(minutes):
    """Compact duration format: [2h30m] / [45m] / [0] hidden. ASCII-safe, no reliance on emoji widths."""
    if not minutes or minutes <= 0:
        return ""
    h, m = divmod(int(minutes), 60)
    if h:
        return f"[{h}h{m}m]" if m else f"[{h}h]"
    return f"[{m}m]"


# --- DRY helpers: filter / truncate / bulk status change, reused across commands ---

def _status_filter_sql(include_canceled=False, hide_done=False, col="status"):
    """Build a `status` column filter SQL fragment + params. Used uniformly across cmds, avoids scattered string-concat.
    Returns (where_fragment, params_list); when nothing is filtered returns ("", []).

    Usage:
        frag, params = _status_filter_sql(inc_cancel, hide_done=not args.all)
        if frag: where.append(frag); sql_params.extend(params)
    """
    excluded = []
    if hide_done:
        excluded.append("DONE")
    if not include_canceled:
        excluded.append("CANCELED")
    if not excluded:
        return "", []
    ph = ",".join("?" * len(excluded))
    return f"({col} IS NULL OR {col} NOT IN ({ph}))", excluded


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


def _print_truncation_hint(shown, total, extra=""):
    """Print `(showing N/total[, extra])` hint when truncated; print nothing otherwise."""
    if shown < total:
        msg = f"(showing {shown}/{total}"
        if extra:
            msg += f", {extra}"
        msg += ")"
        out(_c(msg, "meta"))


def _check_ids_exist(con, ids):
    """Batch existence check; sys.exit if any id is missing. Used by multi-id commands."""
    for nid in ids:
        if not _node_exists(con, nid):
            sys.exit(f"✗ node #{nid} not found")


def _upsert_prop(con, nid, key, value):
    """Unified prop UPSERT (no commit; caller controls the transaction). Batch-friendly.
    `_set_prop` is the commit version for single daily operations."""
    con.execute("INSERT OR REPLACE INTO prop (node_id, key, value) VALUES (?, ?, ?)", (nid, key, value))


# generic ORDER BY fragment: priority A/B/C first, NULL last; same priority by id ascending.
# Usage: f"SELECT * FROM node WHERE ... {_ORDER_BY_PRI_ID}"
# Note: when joining, write the qualified column "n.priority"; that case stays inline.
_ORDER_BY_PRI_ID = "ORDER BY priority NULLS LAST, id"


def _node_tags(con, nid):
    """Return the tag list for a node (insertion order)."""
    return [r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id = ?", (nid,))]


def _term_width():
    """Terminal column count. No TTY (pipe/redirect) -> default 80."""
    import shutil
    try:
        return shutil.get_terminal_size().columns or 80
    except OSError:
        return 80


def _log_full(args):
    """args.log_format == 'full' -> True; otherwise (including None / 'oneline') -> False."""
    return getattr(args, "log_format", "oneline") == "full"


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



def _bulk_status_change(con, args, new_status, *, close=False, reopen=False, msg=None):
    """Unified batch status change: done/cancel/reopen all go through this path.
    - close=True: write closed_at = NOW (or args.at if given)
    - reopen=True: clear closed_at = NULL
    - otherwise: only change status

    If args has a .log field, insert a log per id first (via _insert_log, supporting args.at).
    """
    ids = _ids_list(args)
    _check_ids_exist(con, ids)

    # --at parse (reuses _resolve_at_ts; affects closed_at + log time)
    at_ts = None
    if close and getattr(args, "at", None):
        try:
            at_ts = _resolve_at_ts(args.at)
        except ValueError as e:
            sys.exit(f"✗ {e}")

    # --log: insert log first (use at_ts; default to NOW if no at)
    log_body = getattr(args, "log", None)
    if log_body:
        log_body = log_body.strip()
    if log_body:
        for nid in ids:
            if at_ts:
                _insert_log(con, nid, {"date": at_ts[:10], "time": at_ts[11:16], "body": log_body})
            else:
                _insert_log(con, nid, log_body)

    parts = ["status = ?"]
    sql_params_extra = [new_status]
    if close:
        if at_ts:
            parts.append("closed_at = ?")
            sql_params_extra.append(at_ts)
        else:
            parts.append("closed_at = datetime('now', 'localtime')")
    elif reopen:
        parts.append("closed_at = NULL")
    sql = f"UPDATE node SET {', '.join(parts)} WHERE id = ?"
    for nid in ids:
        con.execute(sql, sql_params_extra + [nid])
    con.commit()
    label = msg or ("reopened → " + new_status if reopen else "→ " + new_status)
    note = f" @{at_ts[11:16]}" if at_ts else ""
    log_hint = " + log" if log_body else ""
    for nid in ids:
        print(f"✓ #{nid} {label}{note}{log_hint}")


# --- scheduled time: precise dates + fuzzy granularity (month/week/quarter/year/someday) ---
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


def _import_node(con, spec, parent_id, ref_map, dry, counters):
    """Recursively insert a node (with tags/props/links/logs/children). Returns new id (placeholder None in dry mode)."""
    title = spec.get("title")
    if not title:
        raise ValueError(f"node missing title: {spec}")
    kind = spec.get("kind", "task")
    status = spec.get("status")
    if not status and kind in ("task", "habit"):
        status = "TODO"
    sched = _norm_sched(spec.get("scheduled"))  # normalize + validate (raises in dry-run)
    # parent: explicit parent_id > parent_ref (same batch) > recursively-passed parent_id
    pid = spec.get("parent", parent_id)
    if spec.get("parent_ref"):
        if spec["parent_ref"] not in ref_map:
            raise ValueError(f"parent_ref '{spec['parent_ref']}' undefined (must appear before reference)")
        pid = ref_map[spec["parent_ref"]]
    closed_at = None
    if status == "DONE":
        closed_at = "datetime_now"  # placeholder; the SQL below uses datetime('now','localtime')

    if dry:
        nid = f"<ref:{spec.get('ref', '?')}>"
        counters["add"] += 1
    else:
        if closed_at:
            cur = con.execute(
                "INSERT INTO node (parent_id,title,kind,status,priority,scheduled_at,deadline_at,body,closed_at) "
                "VALUES (?,?,?,?,?,?,?,?, datetime('now','localtime'))",
                (pid, title, kind, status, spec.get("priority"), sched,
                 spec.get("deadline"), spec.get("body")),
            )
        else:
            cur = con.execute(
                "INSERT INTO node (parent_id,title,kind,status,priority,scheduled_at,deadline_at,body) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (pid, title, kind, status, spec.get("priority"), sched,
                 spec.get("deadline"), spec.get("body")),
            )
        nid = cur.lastrowid
        counters["add"] += 1
        for t in spec.get("tags", []):
            con.execute("INSERT OR IGNORE INTO tag (node_id,tag) VALUES (?,?)", (nid, t))
        for k, v in (spec.get("props") or {}).items():
            _upsert_prop(con, nid, k, str(v))
        for d in spec.get("links", []):
            con.execute("INSERT OR IGNORE INTO link (node_id,vault_doc) VALUES (?,?)", (nid, d))
        for entry in spec.get("logs", []):
            _insert_log(con, nid, entry)

    if spec.get("ref"):
        ref_map[spec["ref"]] = nid
    for child in spec.get("children", []):
        _import_node(con, child, nid, ref_map, dry, counters)
    return nid


def _import_update(con, spec, dry, counters):
    nid = spec.get("id")
    if not nid or not _node_exists(con, nid):
        raise ValueError(f"update target #{nid} does not exist")
    if dry:
        counters["update"] += 1
        return
    if "parent" in spec and spec["parent"] is not None and not _node_exists(con, spec["parent"]):
        raise ValueError(f"update #{nid}: parent #{spec['parent']} does not exist")
    fields, vals = [], []
    for col in ("status", "priority", "title", "scheduled_at", "deadline_at", "body"):
        if col in spec:
            fields.append(f"{col} = ?")
            vals.append(spec[col])
    if "parent" in spec:  # move; parent_id column name differs from spec key, handled separately
        fields.append("parent_id = ?")
        vals.append(spec["parent"])
    if spec.get("status") == "DONE" and "closed_at" not in spec:
        fields.append("closed_at = datetime('now','localtime')")
    if fields:
        con.execute(f"UPDATE node SET {', '.join(fields)} WHERE id = ?", (*vals, nid))
    for t in spec.get("add_tags", []):
        con.execute("INSERT OR IGNORE INTO tag (node_id,tag) VALUES (?,?)", (nid, t))
    for t in spec.get("remove_tags", []):
        con.execute("DELETE FROM tag WHERE node_id = ? AND tag = ?", (nid, t))
    for d in spec.get("add_links", []):
        con.execute("INSERT OR IGNORE INTO link (node_id,vault_doc) VALUES (?,?)", (nid, d))
    for entry in spec.get("add_logs", []):
        _insert_log(con, nid, entry)
    counters["update"] += 1


def cmd_import(args, con):
    """Batch add/update: JSON {add:[...], update:[...]}; single transaction; supports nested children + ref/parent_ref."""
    import json

    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"✗ JSON parse error: {e}")
    if not isinstance(data, dict):
        sys.exit("✗ top level must be an object {add:[...], update:[...]}")

    ref_map = {}
    counters = {"add": 0, "update": 0}
    dry = args.dry_run
    try:
        for spec in data.get("add", []):
            _import_node(con, spec, None, ref_map, dry, counters)
        for spec in data.get("update", []):
            _import_update(con, spec, dry, counters)
    except (ValueError, KeyError) as e:
        con.rollback()
        sys.exit(f"✗ import failed (rolled back): {e}")

    if dry:
        out(_c("[dry-run]", "meta") + _c(f" would add {counters['add']} · update {counters['update']} (not written)"))
        if ref_map:
            out("  " + _c("ref: " + ", ".join(ref_map.keys()), "meta"))
    else:
        con.commit()
        out(_c("✓", "done") + _c(f" added {counters['add']} · updated {counters['update']}"))
        if ref_map:
            print("  ref->id: " + ", ".join(f"{k}=#{v}" for k, v in ref_map.items()))


# --- wl-diff format (apply) ---
# line format: <prefix><indent><node line>  prefix: '+' add, '~' update, '-' delete, ' ' context anchor
# node line: [marker] [#pri] #id [kind] title :tags:   (marker required, others optional)
# rich-field sub-lines: <indent>@log/@link/@prop <value>  (attached to the previous node)
_MARKER_STATUS = {" ": "TODO", "x": "DONE", "/": "DOING", ">": "LATER", "?": "WAIT", "-": "CANCELED"}


def _parse_node_line(body):
    import re

    f = {}
    m = re.match(r"^\[([ x/>?\-])\]\s*", body)
    if m:
        f["marker"] = m.group(1)
        body = body[m.end():]
    m = re.match(r"^\[#([A-C])\]\s*", body)
    if m:
        f["priority"] = m.group(1)
        body = body[m.end():]
    m = re.match(r"^#(\d+)\s*", body)
    if m:
        f["id"] = int(m.group(1))
        body = body[m.end():]
    m = re.match(r"^\[([a-z_]+)\]\s*", body)
    if m:
        f["kind"] = m.group(1)
        body = body[m.end():]
    m = re.search(r"\s*:([\w:]+):\s*$", body)
    if m:
        f["tags"] = [t for t in m.group(1).split(":") if t]
        body = body[: m.start()]
    f["title"] = body.strip()
    return f


_SETTABLE = ("status", "priority", "title", "parent", "scheduled", "deadline")


def _parse_fieldop(s):
    """Parse a field-operation line under a ~ block. Returns (action, field, value) or None.

    set:   `status DONE` / `priority A` / `title x` / `parent 6` / `scheduled 2026-06-01`
    clear: `priority -` (value '-' clears)
    tag:   `+tag x` / `-tag x`
    log:   `+log text` (add only; log is append-only)
    link:  `+link doc` / `-link doc`
    prop:  `prop k=v` / `-prop k`
    """
    import re

    m = re.match(r"^([+-])(tag|link)\s+(.+)$", s)
    if m:
        return ("add" if m.group(1) == "+" else "remove", m.group(2), m.group(3).strip())
    m = re.match(r"^\+log\s+(.+)$", s)
    if m:
        return ("add", "log", m.group(1).strip())
    m = re.match(r"^-prop\s+(\S+)$", s)
    if m:
        return ("remove", "prop", m.group(1))
    m = re.match(r"^prop\s+(\S+?)=(.*)$", s)
    if m:
        return ("set", "prop", (m.group(1), m.group(2).strip()))
    m = re.match(r"^(" + "|".join(_SETTABLE) + r")\s+(.+)$", s)
    if m:
        val = m.group(2).strip()
        if val == "-":
            return ("clear", m.group(1), None)
        return ("set", m.group(1), val)
    return None


def _parse_wld(text):
    """Parse wl-diff -> ops list.

    +/-/anchor op: {op,depth,fields,subs,lineno}
    ~  op:         {op:'~',id,fieldops:[(lineno,(action,field,value))],lineno}
    raises ValueError
    """
    import re

    ops = []
    cur_update = None  # most recent ~ op; collects subsequent indented field-op lines
    for lineno, raw in enumerate(text.splitlines(), 1):
        s = raw.lstrip()
        # blank / comment ('#' followed by space or non-digit) -> skip; but #<digit> is a node id, not a comment
        if not s or (s.startswith("#") and (len(s) == 1 or not s[1].isdigit())):
            continue
        indented = raw[:1] in (" ", "\t")
        # indented line under a ~ context: try field-op first (+tag/-tag/-prop start with +/-, so first-char heuristic isn't enough)
        if cur_update is not None and indented:
            fop = _parse_fieldop(s)
            if fop is not None:
                cur_update["fieldops"].append((lineno, fop))
                continue
            # indented but not a valid field-op: if it looks like a node line (has marker), drop through as new node (ending ~); else error
            if not re.match(r"^[+\- ]?\s*\[", s):
                raise ValueError(f"line {lineno}: unparseable field-op '{s}' under '~' (allowed: status/priority/title/parent/scheduled/deadline/±tag/+log/±link/prop/-prop)")
        # @ sub-line (rich fields of a +/-/anchor node)
        m = re.match(r"^[+\- ]?\s*@(log|link|prop)\s+(.*)$", raw)
        if m:
            if not ops or ops[-1]["op"] == "~":
                raise ValueError(f"line {lineno}: @{m.group(1)} has no preceding +/anchor node to attach to")
            ops[-1]["subs"].append((m.group(1), m.group(2).strip()))
            continue
        m = re.match(r"^([+~\- ])(\s*)(.*)$", raw)
        if not m:
            raise ValueError(f"line {lineno}: cannot parse '{raw}'")
        prefix, spaces, body = m.group(1), m.group(2), m.group(3)
        if not body.strip():
            continue
        if prefix == "~":
            idm = re.search(r"#(\d+)", body)
            if not idm:
                raise ValueError(f"line {lineno}: '~' requires #id (e.g. '~ #14' or single-line '~ [x] #14')")
            nid = int(idm.group(1))
            # single-line shorthand: parse marker/priority/title if present -> a set op for each (untouched if absent)
            f = _parse_node_line(body)
            inline = []
            if "marker" in f:
                inline.append((lineno, ("set", "status", _MARKER_STATUS.get(f["marker"], "TODO"))))
            if "priority" in f:
                inline.append((lineno, ("set", "priority", f["priority"])))
            if f.get("title"):
                inline.append((lineno, ("set", "title", f["title"])))
            op = {"op": "~", "id": nid, "fieldops": inline, "lineno": lineno}
            ops.append(op)
            cur_update = op  # may still accept subsequent indented field-ops (mix of single-line shorthand + complex ops)
            continue
        cur_update = None  # +/-/anchor line ends ~ context
        depth = len(spaces) // 2
        fields = _parse_node_line(body)
        if not fields["title"] and prefix != "-":
            raise ValueError(f"line {lineno}: missing title")
        ops.append({"op": prefix, "depth": depth, "fields": fields, "subs": [], "lineno": lineno})
    return ops


_STATUSES = {"TODO", "DOING", "LATER", "WAIT", "DONE", "DEFERRED", "CANCELED"}
_SET_COL = {"status": "status", "priority": "priority", "title": "title",
            "parent": "parent_id", "scheduled": "scheduled_at", "deadline": "deadline_at"}


def _validate_fieldop(con, lineno, action, field, value, errs):
    if field == "status" and action == "set" and value not in _STATUSES:
        errs.append(f"line {lineno}: invalid status '{value}' (valid: {'/'.join(sorted(_STATUSES))})")
    elif field == "priority" and action == "set" and value not in ("A", "B", "C"):
        errs.append(f"line {lineno}: invalid priority '{value}' (A/B/C)")
    elif field == "title" and action == "clear":
        errs.append(f"line {lineno}: title cannot be cleared")
    elif field == "parent" and action == "set":
        if not value.isdigit() or not _node_exists(con, int(value)):
            errs.append(f"line {lineno}: parent #{value} does not exist")
    elif field == "scheduled" and action == "set":
        try:
            _norm_sched(value)
        except ValueError as e:
            errs.append(f"line {lineno}: {e}")


def _exec_update(con, o):
    """Execute ~ field operations: only touches explicitly-declared fields; never touches anything not declared."""
    nid = o["id"]
    for _, (action, field, value) in o["fieldops"]:
        if field in _SET_COL:
            col = _SET_COL[field]
            if action == "clear":
                con.execute(f"UPDATE node SET {col} = NULL WHERE id = ?", (nid,))
            else:
                if field == "parent":
                    v = int(value)
                elif field == "scheduled":
                    v = _norm_sched(value)
                else:
                    v = value
                con.execute(f"UPDATE node SET {col} = ? WHERE id = ?", (v, nid))
                if field == "status" and value == "DONE":
                    con.execute("UPDATE node SET closed_at = datetime('now','localtime') WHERE id = ? AND closed_at IS NULL", (nid,))
        elif field == "tag":
            if action == "add":
                con.execute("INSERT OR IGNORE INTO tag (node_id,tag) VALUES (?,?)", (nid, value))
            else:
                con.execute("DELETE FROM tag WHERE node_id = ? AND tag = ?", (nid, value))
        elif field == "log":
            _insert_log(con, nid, value)
        elif field == "link":
            if action == "add":
                con.execute("INSERT OR IGNORE INTO link (node_id,vault_doc) VALUES (?,?)", (nid, value))
            else:
                con.execute("DELETE FROM link WHERE node_id = ? AND vault_doc = ?", (nid, value))
        elif field == "prop":
            if action == "set":
                k, v = value
                _upsert_prop(con, nid, k, v)
            else:
                con.execute("DELETE FROM prop WHERE node_id = ? AND key = ?", (nid, value))


def _fieldop_desc(action, field, value):
    if action == "clear":
        return f"{field}=cleared"
    if action == "add":
        return f"+{field} {value}"
    if action == "remove":
        return f"-{field} {value}"
    if field == "prop":
        return f"prop {value[0]}={value[1]}"
    return f"{field}->{value}"


def cmd_apply(args, con):
    """Apply wl-diff: + add (node line) / ~ update (lock-line + field-ops, only declared fields) / - delete / anchor. Single transaction + dry-run validation."""
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        ops = _parse_wld(raw)
    except ValueError as e:
        sys.exit(f"✗ parse failed: {e}")

    # --- validation phase (validate all, stop on errors, no write) ---
    errors = []
    for o in ops:
        pfx, ln = o["op"], o["lineno"]
        if pfx == "~":
            if not _node_exists(con, o["id"]):
                errors.append(f"line {ln}: #{o['id']} does not exist")
            if not o["fieldops"]:
                errors.append(f"line {ln}: ~ #{o['id']} has no field operations")
            for floln, (action, field, value) in o["fieldops"]:
                _validate_fieldop(con, floln, action, field, value, errors)
        else:
            f = o["fields"]
            has_id = "id" in f
            if pfx in ("-", " ") and not has_id:
                errors.append(f"line {ln}: '{pfx}' requires #id")
            if pfx == "+" and has_id:
                errors.append(f"line {ln}: '+' add should not carry #id")
            if has_id and pfx != "+" and not _node_exists(con, f["id"]):
                errors.append(f"line {ln}: #{f['id']} does not exist")
            if "marker" in f and f["marker"] not in _MARKER_STATUS:
                errors.append(f"line {ln}: unknown marker [{f['marker']}]")
    if errors:
        sys.exit("✗ validation failed (not written):\n  " + "\n  ".join(errors))

    # --- plan (guaranteed valid at this point) ---
    plan = []
    for o in ops:
        pfx = o["op"]
        if pfx == "~":
            ch = ", ".join(_fieldop_desc(a, fld, v) for _, (a, fld, v) in o["fieldops"])
            plan.append(f"~ #{o['id']}: {ch}")
        elif pfx == "+":
            f = o["fields"]
            sub = f" (+{len(o['subs'])} sub-items)" if o["subs"] else ""
            plan.append(f"+ {f['title']}" + (f" @depth{o['depth']}" if o["depth"] else "") + sub)
        elif pfx == "-":
            plan.append(f"- #{o['fields']['id']} (cascades subtree)")

    if args.dry_run:
        out(_c("[dry-run]", "meta") + _c(f" {len(plan)} operations (not written):", "header"))
        for desc in plan:
            out("  " + _c(desc))
        return

    # --- execute (single transaction) ---
    stack = {}
    counts = {"add": 0, "update": 0, "delete": 0}
    try:
        for o in ops:
            pfx = o["op"]
            if pfx == "~":
                _exec_update(con, o)
                counts["update"] += 1
                continue
            f, depth = o["fields"], o["depth"]
            if pfx == " ":
                stack[depth] = f["id"]
                continue
            if pfx == "-":
                # recursive subtree delete: node self-ref is ON DELETE SET NULL, only deleting the parent would orphan children, so we must explicitly collect descendants
                ids = [f["id"]] + _collect_descendants(con, f["id"])
                for did in ids:
                    con.execute("DELETE FROM node WHERE id = ?", (did,))
                counts["delete"] += len(ids)
                continue
            # pfx == "+": add new node
            parent_id = stack.get(depth - 1) if depth > 0 else None
            status = _MARKER_STATUS.get(f.get("marker", " "), "TODO")
            kind = f.get("kind", "task")
            if status == "DONE":
                cur = con.execute(
                    "INSERT INTO node (parent_id,title,kind,status,priority,closed_at) "
                    "VALUES (?,?,?,?,?, datetime('now','localtime'))",
                    (parent_id, f["title"], kind, status, f.get("priority")),
                )
            else:
                cur = con.execute(
                    "INSERT INTO node (parent_id,title,kind,status,priority) VALUES (?,?,?,?,?)",
                    (parent_id, f["title"], kind, status, f.get("priority")),
                )
            nid = cur.lastrowid
            for t in f.get("tags", []):
                con.execute("INSERT OR IGNORE INTO tag (node_id,tag) VALUES (?,?)", (nid, t))
            for kind_, val in o["subs"]:
                _apply_sub(con, nid, kind_, val)
            counts["add"] += 1
            stack[depth] = nid
    except Exception as e:
        con.rollback()
        sys.exit(f"✗ apply failed (rolled back): {e}")

    con.commit()
    out(_c("✓", "done") + _c(f" added {counts['add']} · updated {counts['update']} · deleted {counts['delete']}"))


def _apply_sub(con, nid, kind, val):
    if kind == "log":
        _insert_log(con, nid, val)
    elif kind == "link":
        con.execute("INSERT OR IGNORE INTO link (node_id,vault_doc) VALUES (?,?)", (nid, val))
    elif kind == "prop":
        if "=" in val:
            k, v = val.split("=", 1)
            _upsert_prop(con, nid, k.strip(), v.strip())


def _snippet(text, q, ctx=30):
    """Extract a snippet around the query, with the match highlighted (styled) / *…* marked (plain)."""
    i = text.lower().find(q.lower())
    if i < 0:
        return _c(text[:80] + ("…" if len(text) > 80 else ""))
    a, b = max(0, i - ctx), min(len(text), i + len(q) + ctx)
    mid = text[i:i + len(q)]
    pre = ("…" if a > 0 else "") + text[a:i]
    post = text[i + len(q):b] + ("…" if b < len(text) else "")
    if _CONSOLE is None:
        return pre + f"*{mid}*" + post
    return _c(pre) + _c(mid, "hit") + _c(post)


_VALID_FIND_FIELDS = {"title", "body", "log", "tag", "prop", "link"}
_VALID_KINDS = {"task", "project", "area", "year", "quarter", "month", "week", "day",
                "lifetime", "decade", "habit", "signal", "meetlog"}


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
_FISH_HELPERS = {
    # (sub_cmd, opt_name) → fish completion source
    # opt_name None = positional argument
    ("__any__", "--parent"): "(__wl_list_nodes)",
    ("__any__", "--root"): "(__wl_list_nodes)",
    ("__any__", "--id"): "(__wl_list_nodes)",
    ("__any__", "--node"): "(__wl_list_nodes)",
    ("__any__", "--ids"): "(__wl_list_nodes)",
    ("__any__", "--tag"): "(__wl_list_tags)",
    ("sched", "--recur"): "(__wl_recur_suggestions)",
    # time / date related -> date suggestions
    ("log", "--date"): "(__wl_date_suggestions)",
    ("logs", "--date"): "(__wl_date_suggestions)",
    ("unlog", "--date"): "(__wl_date_suggestions)",
    ("dateinfo", "date"): "(__wl_date_suggestions)",
    ("dateinfo", None): "(__wl_date_suggestions)",
    ("day", "date"): "(__wl_date_suggestions)",
    ("sched", "when"): "(__wl_date_suggestions)",
    ("defer", "date"): "(__wl_date_suggestions)",
}
# subcommands whose default positional argument takes a node id (when not explicitly specified)
_FISH_POSITIONAL_NODE = {"log", "done", "defer", "start", "stop", "wait", "reopen",
                        "cancel", "tick", "link", "set", "show", "focus", "ancestors",
                        "descendants", "spent", "unlog", "relog"}

_FISH_HELPER_FUNCTIONS = r"""# --- helper functions (dynamic queries against wl.db; no Python startup, fast) ---
function __wl_list_nodes
    set -l db (test -n "$WL_DB"; and echo $WL_DB; or echo ~/.worklog/wl.db)
    test -f $db; or return
    # SQLite char(9) = tab (fish completion uses \t to separate token + desc)
    sqlite3 $db "SELECT id || char(9) || title FROM node WHERE status IS NULL OR status NOT IN ('DONE', 'CANCELED') ORDER BY id DESC LIMIT 80" 2>/dev/null
end

function __wl_list_tags
    set -l db (test -n "$WL_DB"; and echo $WL_DB; or echo ~/.worklog/wl.db)
    test -f $db; or return
    sqlite3 $db "SELECT DISTINCT tag FROM tag ORDER BY tag" 2>/dev/null
end

function __wl_date_suggestions
    printf 'today\ttoday\nyesterday\tyesterday\nday-before-yesterday\tday before yesterday\ntomorrow\ttomorrow\nday-after-tomorrow\tday after tomorrow\n'
    printf 'someday\tno specific time\n'
    set -l today (date +%Y-%m-%d)
    printf '%s\ttoday YYYY-MM-DD\n' $today
end

function __wl_recur_suggestions
    printf 'daily\tevery day\n'
    printf 'weekly:Mon,Wed,Fri\tMon/Wed/Fri\n'
    printf 'weekly:Sat,Sun\tweekends\n'
    printf 'weekly:-1\tevery Sunday (last day)\n'
    printf 'monthly:1\t1st of every month\n'
    printf 'monthly:15\t15th of every month\n'
    printf 'monthly:-1\tlast day of every month\n'
    printf 'quarterly:1-1\tfirst day of every quarter\n'
    printf 'quarterly:-1\tlast day of every quarter\n'
    printf 'yearly:01-01\tJan 1 every year\n'
    printf 'yearly:-1\tlast day of year (12-31)\n'
end
"""


def _completion_iter_actions(parser):
    """yield action; skip help / version / dest=cmd / subparsers"""
    for a in parser._actions:
        if isinstance(a, (argparse._HelpAction, argparse._VersionAction)):
            continue
        if isinstance(a, argparse._SubParsersAction):
            continue
        yield a


def _fish_escape(s):
    """fish string escape: wrap in single quotes; inner single quote becomes \\'"""
    if s is None:
        return ""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _fish_one_complete(prefix, action, sub_cmd=None):
    """Emit one fish complete line for a single action. prefix is the leading text of the complete line (with -c wl -n ...)."""
    lines = []
    descr = (action.help or "").split("\n")[0].strip()
    # short / long options
    short = []
    long_ = []
    for o in action.option_strings:
        (long_ if o.startswith("--") else short).append(o.lstrip("-"))
    opt_parts = []
    for s in short:
        opt_parts.append(f"-s {s}")
    for l in long_:
        opt_parts.append(f"-l {l}")
    opt_str = " ".join(opt_parts)
    if not opt_str:
        return []  # no short/long = positional; handled by caller

    # value-taking options disable filename completion (-x); store_true / store_false take no value
    takes_value = not isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                                          argparse._StoreConstAction, argparse._CountAction))
    if takes_value:
        opt_str += " -x"

    line = f"{prefix} {opt_str}"
    if descr:
        line += f' -d "{_fish_escape(descr)}"'

    # value-completion source: choices > helper map > default (none)
    val_src = None
    if action.choices:
        val_src = " ".join(str(c) for c in action.choices)
    else:
        # find helper: first (sub_cmd, --long), then (__any__, --long)
        for opt in action.option_strings:
            for key in [(sub_cmd, opt), ("__any__", opt)]:
                if key in _FISH_HELPERS:
                    val_src = _FISH_HELPERS[key]
                    break
            if val_src:
                break
    if val_src:
        line += f' -a "{val_src}"'

    lines.append(line)
    return lines


def _fish_positional_complete(parser, sub_cmd):
    """Positional argument completion for a subcommand (mostly node id / date)."""
    lines = []
    for a in parser._actions:
        if a.option_strings or isinstance(a, (argparse._SubParsersAction,
                                              argparse._HelpAction, argparse._VersionAction)):
            continue
        # positional. Look up dest -> helper
        prefix = f'complete -c wl -n "__fish_seen_subcommand_from {sub_cmd}"'
        val_src = None
        # explicit helper
        for key in [(sub_cmd, a.dest), (sub_cmd, None)]:
            if key in _FISH_HELPERS:
                val_src = _FISH_HELPERS[key]
                break
        # default: subcommand in node-id operation set -> __wl_list_nodes
        if val_src is None and sub_cmd in _FISH_POSITIONAL_NODE:
            val_src = "(__wl_list_nodes)"
        if val_src is None and a.choices:
            val_src = " ".join(str(c) for c in a.choices)
        if val_src:
            descr = (a.help or "").split("\n")[0].strip()
            line = f"{prefix} -f -a \"{val_src}\""
            if descr:
                line += f' -d "{_fish_escape(descr)}"'
            lines.append(line)
    return lines


def _generate_fish_completion(parser):
    """Walk build_parser() to produce full fish completion. argparse is the source of truth."""
    lines = [
        "# wl fish completion (auto-generated by `wl --print-completion fish`)",
        "# Load: add `wl --print-completion fish | source` to ~/.config/fish/config.fish",
        "",
        "complete -c wl -f   # disable filename completion by default",
        "",
        _FISH_HELPER_FUNCTIONS,
        "# --- global args ---",
    ]
    # global (top-level parser) actions
    for a in _completion_iter_actions(parser):
        lines += _fish_one_complete('complete -c wl', a, sub_cmd=None)

    # subcommands
    subparsers_action = next((x for x in parser._actions
                              if isinstance(x, argparse._SubParsersAction)), None)
    if subparsers_action is None:
        return "\n".join(lines) + "\n"

    # use _collect_sub_meta to get (name, help, sub, aliases)
    sub_metas = _collect_sub_meta(parser)
    lines.append("")
    lines.append("# --- subcommand names (+ aliases) ---")
    for name, descr, _sub, aliases in sub_metas:
        descr_part = f' -d "{_fish_escape(descr)}"' if descr else ""
        lines.append(f'complete -c wl -n "__fish_use_subcommand" -a "{name}"{descr_part}')
        for alias in aliases:
            alias_descr = f"{descr} (= {name})" if descr else f"alias of {name}"
            lines.append(f'complete -c wl -n "__fish_use_subcommand" -a "{alias}"'
                         f' -d "{_fish_escape(alias_descr)}"')

    # per-subcommand arguments -- condition includes the primary name + all aliases
    lines.append("")
    lines.append("# --- per-subcommand arguments ---")
    for name, _descr, sub, aliases in sub_metas:
        all_names = " ".join([name] + aliases)
        cond = f'__fish_seen_subcommand_from {all_names}'
        prefix = f'complete -c wl -n "{cond}"'
        section = [f"\n# {name}"]
        for a in _completion_iter_actions(sub):
            section += _fish_one_complete(prefix, a, sub_cmd=name)
        section += _fish_positional_complete(sub, name)
        if len(section) > 1:
            lines += section

    return "\n".join(lines) + "\n"


# --- bash backend ---

# bash does not show descriptions, only completes tokens. helper is a bash function that emits a token list.
_BASH_HELPER_FUNCTIONS = r"""# helper functions (local SQLite query against wl.db; no Python startup)
__wl_list_nodes_bash() {
    local db="${WL_DB:-$HOME/.worklog/wl.db}"
    [ -f "$db" ] || return
    sqlite3 "$db" "SELECT id FROM node WHERE status IS NULL OR status NOT IN ('DONE', 'CANCELED') ORDER BY id DESC LIMIT 80" 2>/dev/null
}

__wl_list_tags_bash() {
    local db="${WL_DB:-$HOME/.worklog/wl.db}"
    [ -f "$db" ] || return
    sqlite3 "$db" "SELECT DISTINCT tag FROM tag ORDER BY tag" 2>/dev/null
}

__wl_date_suggestions_bash() {
    echo "today yesterday day-before-yesterday tomorrow day-after-tomorrow someday $(date +%Y-%m-%d)"
}

__wl_recur_suggestions_bash() {
    echo "daily weekly:Mon,Wed,Fri weekly:Sat,Sun weekly:-1 monthly:1 monthly:15 monthly:-1 quarterly:1-1 quarterly:-1 yearly:01-01 yearly:-1"
}
"""

# subcommand / argument -> bash helper function name (outputs token list, consumed by compgen -W)
_BASH_DYN_HELPERS = {
    ("__any__", "--parent"): "__wl_list_nodes_bash",
    ("__any__", "--root"): "__wl_list_nodes_bash",
    ("__any__", "--id"): "__wl_list_nodes_bash",
    ("__any__", "--node"): "__wl_list_nodes_bash",
    ("__any__", "--ids"): "__wl_list_nodes_bash",
    ("__any__", "--tag"): "__wl_list_tags_bash",
    ("sched", "--recur"): "__wl_recur_suggestions_bash",
    ("log", "--date"): "__wl_date_suggestions_bash",
    ("logs", "--date"): "__wl_date_suggestions_bash",
    ("unlog", "--date"): "__wl_date_suggestions_bash",
}


def _collect_sub_meta(parser):
    """Return [(sub_name, sub_help, sub_parser, [aliases])].
    aliases are all alias names of the sub (excluding the primary name); the primary sub matches via choices key against _choices_actions; aliases point to the same parser object."""
    subparsers_action = next((x for x in parser._actions
                              if isinstance(x, argparse._SubParsersAction)), None)
    if not subparsers_action:
        return []
    # reverse map: parser obj id -> list of names
    parser_to_names = {}
    for name, sub_p in subparsers_action.choices.items():
        parser_to_names.setdefault(id(sub_p), []).append(name)
    # primary names: those in _choices_actions with help text
    primary_names = set()
    if subparsers_action._choices_actions:
        for c in subparsers_action._choices_actions:
            primary_names.add(c.dest)
    result = []
    seen = set()
    for name, sub in subparsers_action.choices.items():
        if id(sub) in seen:
            continue
        if name not in primary_names:
            # this is an alias; skip for now, collected together when the primary name appears
            continue
        seen.add(id(sub))
        help_text = ""
        if subparsers_action._choices_actions:
            for c in subparsers_action._choices_actions:
                if c.dest == name:
                    help_text = c.help or ""
                    break
        aliases = [n for n in parser_to_names[id(sub)] if n != name]
        result.append((name, (help_text or "").split("\n")[0].strip(), sub, aliases))
    return result


def _sub_options(sub_parser):
    """List of all --long / -short options for a subcommand."""
    opts = []
    for a in _completion_iter_actions(sub_parser):
        for o in a.option_strings:
            opts.append(o)
    return opts


def _generate_bash_completion(parser):
    """argparse → bash _wl() function + complete -F _wl wl."""
    sub_metas = _collect_sub_meta(parser)
    # subcmds list includes primary names + aliases
    all_sub_names = []
    for name, _, _, aliases in sub_metas:
        all_sub_names.append(name)
        all_sub_names.extend(aliases)
    sub_names = " ".join(all_sub_names)

    # global flags (top-level parser)
    global_opts = []
    for a in _completion_iter_actions(parser):
        global_opts.extend(a.option_strings)
    global_opts_str = " ".join(global_opts)

    lines = [
        "# wl bash completion (auto-generated by `wl print-completion bash`)",
        "# Load: add `eval \"$(wl print-completion bash)\"` to ~/.bashrc",
        "",
        _BASH_HELPER_FUNCTIONS,
        "_wl() {",
        '    local cur="${COMP_WORDS[COMP_CWORD]}"',
        '    local prev="${COMP_WORDS[COMP_CWORD-1]}"',
        "",
        "    # find current sub: first word not starting with -",
        '    local sub=""',
        "    local i",
        "    for ((i=1; i<COMP_CWORD; i++)); do",
        '        case "${COMP_WORDS[i]}" in',
        "            -*) ;;",
        '            *) sub="${COMP_WORDS[i]}"; break ;;',
        "        esac",
        "    done",
        "",
        f'    local global_opts="{global_opts_str}"',
        f'    local subcmds="{sub_names}"',
        "",
        '    if [ -z "$sub" ]; then',
        '        if [[ "$cur" == -* ]]; then',
        '            COMPREPLY=( $(compgen -W "$global_opts" -- "$cur") )',
        "        else",
        '            COMPREPLY=( $(compgen -W "$subcmds" -- "$cur") )',
        "        fi",
        "        return",
        "    fi",
        "",
        '    case "$sub" in',
    ]

    for name, _, sub, aliases in sub_metas:
        opts = _sub_options(sub)
        opts_str = " ".join(opts)
        # bash case pattern: name|alias1|alias2)
        case_pattern = "|".join([name] + aliases)
        case_lines = [f'        {case_pattern})']
        # when prev is a long option, look up its helper / choices
        prev_cases = []
        for a in _completion_iter_actions(sub):
            if not a.option_strings:
                continue
            long_opts = [o for o in a.option_strings if o.startswith("--")]
            if not long_opts:
                continue
            for opt in long_opts:
                src = None
                if a.choices:
                    src = " ".join(str(c) for c in a.choices)
                else:
                    for key in [(name, opt), ("__any__", opt)]:
                        if key in _BASH_DYN_HELPERS:
                            src = f'$({_BASH_DYN_HELPERS[key]})'
                            break
                if src:
                    prev_cases.append((opt, src))
        if prev_cases:
            case_lines.append('            case "$prev" in')
            for opt, src in prev_cases:
                if src.startswith("$("):
                    case_lines.append(f'                {opt}) COMPREPLY=( $(compgen -W "{src}" -- "$cur") ); return ;;')
                else:
                    case_lines.append(f'                {opt}) COMPREPLY=( $(compgen -W "{src}" -- "$cur") ); return ;;')
            case_lines.append('            esac')

        case_lines.append('            if [[ "$cur" == -* ]]; then')
        case_lines.append(f'                COMPREPLY=( $(compgen -W "{opts_str} $global_opts" -- "$cur") )')
        case_lines.append('            else')
        # positional: when the subcommand operates on node ids -> __wl_list_nodes_bash
        if name in _FISH_POSITIONAL_NODE:
            case_lines.append(f'                COMPREPLY=( $(compgen -W "$(__wl_list_nodes_bash)" -- "$cur") )')
        else:
            case_lines.append('                :')
        case_lines.append('            fi')
        case_lines.append('            ;;')
        lines.extend(case_lines)

    lines.append('    esac')
    lines.append('}')
    lines.append('complete -F _wl wl')
    return "\n".join(lines) + "\n"


# --- zsh backend ---

_ZSH_HELPER_FUNCTIONS = r"""# helper functions (local SQLite query against wl.db; no Python startup)
__wl_list_nodes_zsh() {
    local db="${WL_DB:-$HOME/.worklog/wl.db}"
    [ -f "$db" ] || return
    local -a nodes
    nodes=( "${(@f)$(sqlite3 "$db" "SELECT id || ':' || replace(title, ':', '\\:') FROM node WHERE status IS NULL OR status NOT IN ('DONE', 'CANCELED') ORDER BY id DESC LIMIT 80" 2>/dev/null)}" )
    _describe 'node' nodes
}

__wl_list_tags_zsh() {
    local db="${WL_DB:-$HOME/.worklog/wl.db}"
    [ -f "$db" ] || return
    local -a tags
    tags=( "${(@f)$(sqlite3 "$db" "SELECT DISTINCT tag FROM tag ORDER BY tag" 2>/dev/null)}" )
    _values 'tag' $tags
}

__wl_date_suggestions_zsh() {
    local today=$(date +%Y-%m-%d)
    _describe 'date' \
        "today:today" "yesterday:yesterday" "day-before-yesterday:day before yesterday" "tomorrow:tomorrow" "day-after-tomorrow:day after tomorrow" \
        "someday:no specific time" "$today:today YYYY-MM-DD"
}

__wl_recur_suggestions_zsh() {
    _describe 'recur' \
        "daily:every day" \
        "weekly\\:Mon,Wed,Fri:Mon/Wed/Fri" \
        "weekly\\:Sat,Sun:weekends" \
        "weekly\\:-1:every Sunday (last day)" \
        "monthly\\:1:1st of every month" \
        "monthly\\:15:15th of every month" \
        "monthly\\:-1:last day of every month" \
        "quarterly\\:1-1:first day of every quarter" \
        "quarterly\\:-1:last day of every quarter" \
        "yearly\\:01-01:Jan 1 every year" \
        "yearly\\:-1:last day of year (12-31)"
}
"""

_ZSH_DYN_HELPERS = {
    ("__any__", "--parent"): "__wl_list_nodes_zsh",
    ("__any__", "--root"): "__wl_list_nodes_zsh",
    ("__any__", "--id"): "__wl_list_nodes_zsh",
    ("__any__", "--node"): "__wl_list_nodes_zsh",
    ("__any__", "--ids"): "__wl_list_nodes_zsh",
    ("__any__", "--tag"): "__wl_list_tags_zsh",
    ("sched", "--recur"): "__wl_recur_suggestions_zsh",
    ("log", "--date"): "__wl_date_suggestions_zsh",
    ("logs", "--date"): "__wl_date_suggestions_zsh",
    ("unlog", "--date"): "__wl_date_suggestions_zsh",
}


def _zsh_escape(s):
    """zsh string escape: backticks / square brackets / single + double quotes"""
    if s is None:
        return ""
    return s.replace("\\", "\\\\").replace("'", "''").replace("[", "\\[").replace("]", "\\]").replace(":", "\\:")


def _zsh_arg_spec(action, sub_cmd):
    """For a single action, produce the _arguments spec string. None means positional (handled separately)."""
    if not action.option_strings:
        return None  # positional
    descr = (action.help or "").split("\n")[0].strip()
    descr_part = f"[{_zsh_escape(descr)}]" if descr else ""

    takes_value = not isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                                          argparse._StoreConstAction, argparse._CountAction))

    val_part = ""
    if takes_value:
        # value-completion source
        val_src = None
        if action.choices:
            val_src = "(" + " ".join(str(c) for c in action.choices) + ")"
        else:
            for opt in action.option_strings:
                for key in [(sub_cmd, opt), ("__any__", opt)]:
                    if key in _ZSH_DYN_HELPERS:
                        val_src = _ZSH_DYN_HELPERS[key]
                        break
                if val_src:
                    break
        if val_src:
            val_part = f": :{val_src}" if val_src.startswith("__") else f": :{val_src}"
        else:
            val_part = ": :"

    # multiple option strings (e.g. -q --brief): zsh uses {-q,--brief} form
    opts = action.option_strings
    if len(opts) == 1:
        return f"'{opts[0]}{descr_part}{val_part}'"
    elif len(opts) == 2:
        return "'(" + " ".join(opts) + ")'{" + ",".join(opts) + "}'" + descr_part + val_part + "'"
    else:
        # > 2 options: one entry per option
        return " ".join(f"'{o}{descr_part}{val_part}'" for o in opts)


def _generate_zsh_completion(parser):
    """argparse → zsh _wl() function + compdef _wl wl."""
    sub_metas = _collect_sub_meta(parser)

    lines = [
        "#compdef wl",
        "# wl zsh completion (auto-generated by `wl print-completion zsh`)",
        "# Load: add `eval \"$(wl print-completion zsh)\"` to ~/.zshrc",
        "",
        _ZSH_HELPER_FUNCTIONS,
        "_wl() {",
        "    local context state line",
        "    typeset -A opt_args",
        "",
        "    _arguments -C \\",
    ]

    # global args
    global_specs = []
    for a in _completion_iter_actions(parser):
        spec = _zsh_arg_spec(a, sub_cmd=None)
        if spec:
            global_specs.append(spec)
    for spec in global_specs:
        lines.append(f"        {spec} \\")
    lines.append("        '1: :->cmds' \\")
    lines.append("        '*::arg:->args'")
    lines.append("")
    lines.append('    case "$state" in')
    lines.append('        cmds)')
    lines.append('            local -a subcmds')
    lines.append('            subcmds=(')
    for name, descr, _, aliases in sub_metas:
        descr_safe = _zsh_escape(descr)
        lines.append(f"                '{name}:{descr_safe}'")
        for alias in aliases:
            alias_descr = _zsh_escape(f"{descr} (= {name})" if descr else f"alias of {name}")
            lines.append(f"                '{alias}:{alias_descr}'")
    lines.append('            )')
    lines.append("            _describe 'subcommand' subcmds")
    lines.append('            ;;')
    lines.append('        args)')
    lines.append('            case $line[1] in')

    for name, _, sub, aliases in sub_metas:
        # zsh case pattern: name|alias1|alias2)
        case_pattern = "|".join([name] + aliases)
        lines.append(f'                {case_pattern})')
        lines.append('                    _arguments \\')
        sub_specs = []
        for a in _completion_iter_actions(sub):
            spec = _zsh_arg_spec(a, sub_cmd=name)
            if spec:
                sub_specs.append(spec)
        # positional (single positional taking a node id)
        positional_helper = None
        if name in _FISH_POSITIONAL_NODE:
            positional_helper = "__wl_list_nodes_zsh"
        for i, spec in enumerate(sub_specs):
            suffix = " \\" if (i < len(sub_specs) - 1 or positional_helper) else ""
            lines.append(f"                        {spec}{suffix}")
        if positional_helper:
            lines.append(f"                        '*: :{positional_helper}'")
        lines.append('                    ;;')

    lines.append('            esac')
    lines.append('            ;;')
    lines.append('    esac')
    lines.append('}')
    lines.append('compdef _wl wl')
    return "\n".join(lines) + "\n"


def cmd_print_completion(args, con=None):
    """Dump shell completion script. See per-shell header for how to load.

    fish: add `wl print-completion fish | source` to ~/.config/fish/config.fish
    bash: add `eval "$(wl print-completion bash)"` to ~/.bashrc
    zsh:  add `eval "$(wl print-completion zsh)"`  to ~/.zshrc
    """
    shell = args.shell
    parser = build_parser()
    if shell == "fish":
        sys.stdout.write(_generate_fish_completion(parser))
    elif shell == "bash":
        sys.stdout.write(_generate_bash_completion(parser))
    elif shell == "zsh":
        sys.stdout.write(_generate_zsh_completion(parser))
    else:
        sys.exit(f"✗ shell '{shell}' not supported (fish / bash / zsh)")


def cmd_themes(args, con):
    """List all color themes, each rendering a one-line sample in its own palette for comparison."""
    req = args.theme or os.environ.get("WL_THEME") or "auto"
    cur = _resolve_theme(req)  # resolve auto to a real theme
    auto_note = f" (auto -> {cur})" if req in (None, "auto") else ""
    no_color = args.color == "never" or os.environ.get("NO_COLOR")
    if not _RICH_AVAIL or no_color:
        # no rich or color explicitly off: plain text listing
        for name in THEMES:
            mark = "  <- current" if name == cur else ""
            print(f"■ {name}{mark}")
        print(f"current: {req}{auto_note}")
        if not _RICH_AVAIL:
            print("(rich not installed; no color preview; pip install rich)")
        return
    # render the sample with each theme's own palette (force_terminal: keeps colors when piped to less -R)
    for name in THEMES:
        prev = _RichConsole(theme=_RichTheme(THEMES[name]), force_terminal=True, highlight=False, soft_wrap=True)
        mark = f"  [done]<- current {auto_note}[/done]" if name == cur else ""
        prev.print(f"[header]■ {name}[/header]{mark}")
        prev.print("  [done]\\[x][/done] [pri_a]\\[#A][/pri_a] [id]#42[/id] [kind]\\[project][/kind] "
                   "sample task with [hit]match[/hit] [planned]·planned[/planned]  [clock]⏱30min[/clock]  [tag]:work:[/tag]")
        prev.print("  [doing]\\[/][/doing] [pri_b]\\[#B][/pri_b] [id]#43[/id] doing sample    "
                   "[later]\\[>][/later] [pri_c]\\[#C][/pri_c] [id]#44[/id] later sample  [meta]«meta»[/meta]")
        prev.print()


# --- helpers ---
def _node_exists(con, node_id):
    return con.execute("SELECT 1 FROM node WHERE id = ?", (node_id,)).fetchone() is not None


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


# --- argparse ---
def _load_user_aliases():
    """Read ~/.config/wl/aliases.ini and return {target_cmd: [alias1, alias2, ...]}.
    Format:
        [aliases]
        d = day
        c = checkin
    Multiple aliases pointing to the same target are merged. Returns {} on failure / missing file.
    """
    import configparser
    path = str(ALIASES_PATH)
    if not os.path.exists(path):
        return {}
    cfg = configparser.ConfigParser()
    try:
        cfg.read(path, encoding="utf-8")
    except (configparser.Error, OSError):
        return {}
    if "aliases" not in cfg:
        return {}
    out = {}
    for alias, target in cfg["aliases"].items():
        target = target.strip()
        if not target:
            continue
        out.setdefault(target, []).append(alias.strip())
    return out


_USER_ALIASES = None  # lazy cache, populated on first build_parser call


def build_parser():
    global _USER_ALIASES
    if _USER_ALIASES is None:
        _USER_ALIASES = _load_user_aliases()
    user_aliases = _USER_ALIASES

    p = argparse.ArgumentParser(prog="wl", description="worklog-cli: SQLite-backed worklog tool")
    p.add_argument("--version", action="version", version=f"wl {__version__}")
    p.add_argument("--color", choices=["auto", "always", "never"], default=None,
                   help="color switch (default auto: enabled on TTY + rich; also reads $WL_COLOR/$NO_COLOR)")
    p.add_argument("--theme", default=None, choices=["auto"] + list(THEMES),
                   metavar="{auto,%s}" % ",".join(THEMES),
                   help="color theme (default auto: probe terminal bg, pick dark/light; reads $WL_THEME; see `wl themes`)")
    p.add_argument("-q", "--brief", action="store_true",
                   help="brief output: skip log body/timeline/detail in every command, token-saving for AI")
    p.add_argument("--log-format", choices=["oneline", "full"], default="oneline",
                   help="log body render style (default oneline = truncate to terminal width with …; full = expand; applies across wl day/tree/logs/show)")
    p.add_argument("--show-canceled", action="store_true",
                   help="show CANCELED nodes (hidden by default; --all also includes them)")

    # time-window parent parser (reused by changes/summary/logs etc.)
    window = argparse.ArgumentParser(add_help=False)
    window.add_argument("--since", help="YYYY-MM-DD (start)")
    window.add_argument("--until", help="YYYY-MM-DD (end)")
    window.add_argument("--week", help="YYYY-Www (ISO week, overrides since/until)")
    window.add_argument("--month", help="YYYY-MM (overrides since/until)")

    _real_sub = p.add_subparsers(dest="cmd", required=True)

    # wrap add_parser to inject user aliases (cross-shell uniform: wl d == wl day)
    class _SubWrapper:
        def __init__(self, sub):
            self._sub = sub
        def add_parser(self, name, **kw):
            aliases = list(kw.pop("aliases", []))
            for a in user_aliases.get(name, []):
                if a not in aliases and a != name:
                    aliases.append(a)
            if aliases:
                kw["aliases"] = aliases
            # battery-included (DESIGN §35): if no explicit description,
            # use help as the description so `wl <cmd> --help` always
            # has an intro line right after the usage line.
            if "description" not in kw and "help" in kw:
                kw["description"] = kw["help"]
            return self._sub.add_parser(name, **kw)
        def __getattr__(self, k):
            return getattr(self._sub, k)
    sub = _SubWrapper(_real_sub)

    sub.add_parser("init",
        help="initialize SQLite DB (default ~/.local/share/wl/wl.db; skips if it exists)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Run once on a fresh machine before using wl.

DB path resolution (priority order):
  1. $WL_DB env var (testing / explicit override)
  2. legacy ~/.worklog/wl.db (back-compat: used if it already exists)
  3. $XDG_DATA_HOME/wl/wl.db (default ~/.local/share/wl/wl.db)

Config (aliases.ini) lives at $XDG_CONFIG_HOME/wl/aliases.ini (default ~/.config/wl/aliases.ini).""")

    a = sub.add_parser("add",
        help="create a new node (task/project/area/meetlog/habit/day...); compound flags let you do add + log + done + sched + link in one shot",
        description="Create a new node (task/project/area/meetlog/habit/day/...). Compound flags support add + log + done + sched + link in one step, replacing several separate commands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  # New task (work-task-start preferred path)
  wl add "PoC-3 S3 permissions" -k task -p B -t work,iac --parent 103 --sched today

  # New project under an area
  wl add "new project" -k project -p A -t work --parent <area_id>

  # Retrospective entry (create + log + done + closed_at + link, one shot)
  wl add "got something done" -k task -p B \\
    --log "result note (PR#42)" --done --at 14:30 --link "vault doc name" --sched today

  # meetlog placeholder
  wl add "[meetlog] 09:30 tech sync" -k meetlog -p A -t work,meeting --parent <day_id>

Differences from related commands:
  - wl add ... --log + --done       one-shot create + log + close. Same as add -> log -> done in three steps.
  - wl tick <id>                    add a check-in log to an existing habit/task, does not create a new one
  - wl log <id>                     add a log to an existing task, does not create a new one""")
    a.add_argument("title")
    a.add_argument("-k", "--kind", default="task", help="node kind (default: task)")
    a.add_argument("-p", "--priority", choices=["A", "B", "C"])
    a.add_argument("-t", "--tag", help="comma-separated tags")
    a.add_argument("--proj", help="project (stored as prop)")
    a.add_argument("--parent", type=int, help="parent node id")
    a.add_argument("--status")
    a.add_argument("--scheduled", help="(rough hint, writes node.scheduled_at) scheduled time: YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / next-week / next-month / next-quarter")
    a.add_argument("--sched", help="(precise, writes the sched table = visible as planned in `wl day` for that date) date: YYYY-MM-DD / today / yesterday / tomorrow / day-after-tomorrow")
    a.add_argument("--deadline", help="deadline date YYYY-MM-DD")
    a.add_argument("--body", help="optional body text")
    # compound flags: create + log + status + association
    a.add_argument("--log", "-m", help="insert a log entry right after creation (result / output / numbers)")
    a.add_argument("--done", action="store_true", help="mark DONE + write closed_at immediately after creation (retrospective task in one shot)")
    a.add_argument("--at", help="timestamp for --log + (if --done) closed_at (HH:MM / YYYY-MM-DD [HH:MM[:SS]])")
    a.add_argument("--link", help="also attach a vault doc (no .md suffix, same semantics as `wl link`)")

    g = sub.add_parser("log",
        help="add a log entry to a node (auto TODO -> DOING)",
        description="Add a log entry to a node (progress / event stream). By default auto-progresses TODO to DOING ('logging means working'); suppress with --keep-status. Backfill historical data with --date/--time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl log 42 "result: PR#13 merged"               # current progress
  wl log 42 "..." --date 2026-05-20              # backfill to that day
  wl log 42 "..." --date yesterday --time 14:30  # precise timestamp
  wl log 42 "..." --keep-status                  # don't change status (e.g. log while WAIT)

Differences from related commands:
  - wl tick <id> --note "..."   habit check-in, default body = "✓ done"
  - wl add ... --log "..."      create a new task + insert a log in one step
  - wl relog #L<id> "new body"  rewrite an existing log body / time
  - wl unlog #L<id>             delete a log""")
    g.add_argument("id", type=int)
    g.add_argument("body")
    g.add_argument("--date", help="log date: YYYY-MM-DD / today / yesterday / day-before-yesterday / tomorrow / day-after-tomorrow (default: today; for backfilling history)")
    g.add_argument("--time", help="log time HH:MM or HH:MM:SS (with --date, or alone for today)")
    g.add_argument("--keep-status", action="store_true",
                   help="do not auto-promote TODO to DOING (default: logging implies 'working on it'; DONE etc. unchanged)")

    d = sub.add_parser("done",
        help="mark node DONE + closed_at (multiple ids; --log/--at for one-shot log+done)",
        description="Mark node as DONE and write closed_at. Accepts multiple ids. --log/--at combines log + close + timestamp in one step (replaces wl log -> wl done two-step).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl done 42                                  # mark done
  wl done 42 43 44                            # batch
  wl done 42 --log "PR#13 merged"             # close + log in one shot
  wl done 42 -m "..." --at 2026-05-30 16:00   # use past timestamp (closed_at + log together)
  wl cancel 42 --log "deprioritized, dropping" # cancel also takes --log/--at

Note: running done on an already-DONE node overwrites closed_at (matches cancel behavior).
Inverse of wl reopen (undo DONE back to TODO).""")
    d.add_argument("ids", type=int, nargs="+", help="node id(s)")
    d.add_argument("--log", "-m", help="add a log (result / output / numbers) right before closing")
    d.add_argument("--at", help="closed_at + log use this timestamp (HH:MM / YYYY-MM-DD [HH:MM[:SS]])")

    df = sub.add_parser("defer",
        help="defer a task to a future point (LATER + scheduled_at; fuzzy times supported)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl defer 42 2026-06-15     # defer to a precise date
  wl defer 42 next-month     # fuzzy
  wl defer 42 2026-Q3        # quarter
  wl defer 42 someday        # no scheduled time

Differences from wl sched:
  - wl defer  -> status=LATER + scheduled_at field (rough hint, does NOT appear as "planned" in wl day on that day)
  - wl sched  -> writes to sched table (precise, appears as "planned" in wl day on that day)
To schedule it as planned for a specific day, use wl sched. defer is for "set aside, vaguely revisit later".""")
    df.add_argument("id", type=int)
    df.add_argument("date", help="scheduled time (precise or fuzzy): YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / next-week / next-month / next-quarter")

    s = sub.add_parser("start",
        help="clock-in to start timing (batch ids; --at to backfill past time)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl start 42                       # start timing now (inserts CLOCK_IN log)
  wl start 42 43                    # multiple tasks at once (parallel timers)
  wl start 42 --at 09:00            # backfill 9am start (forgot to clock in)
  wl start 42 --at 2026-05-30 14:30 # full ts

Related: close with wl stop <id>; see what's running via wl active; wl spent records a CLOCK pair from a duration.""")
    s.add_argument("ids", type=int, nargs="+", help="node id(s)")
    s.add_argument("--at", help="backfill start time: HH:MM (today) / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")

    st = sub.add_parser("stop",
        help="clock-out to stop timing + compute elapsed (multiple ids; --at to backfill past end)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl stop 42                            # stop now, write CLOCK_OUT elapsed=Nmin
  wl stop 42 43                         # batch stop
  wl stop 42 --at 11:30                 # backfill 11:30 end (must be later than CLOCK_IN)
  wl stop 42 --at 2026-05-30 16:00      # full ts

Difference from wl spent: stop pairs with a prior CLOCK_IN; spent creates a pair directly from a duration.""")
    st.add_argument("ids", type=int, nargs="+", help="node id(s)")
    st.add_argument("--at", help="backfill end time (must be later than CLOCK_IN)")

    sp = sub.add_parser("spent",
        help="record a past time spent (build CLOCK pair from duration, good for retrospective entries)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl spent 42 45               # 45 minutes (start = NOW - 45m, stop = NOW)
  wl spent 42 90m              # same, with m suffix
  wl spent 42 1h30m            # 1 hour 30 minutes
  wl spent 42 2h               # 2 hours
  wl spent 42 30m --at 14:30   # end at 14:30, backfill start at 14:00

Difference from wl start/stop: spent builds CLOCK_IN+OUT pair from a duration in one step; no need to start first. Good for "forgot to clock, recording it after the fact".""")
    sp.add_argument("id", type=int, help="node id")
    sp.add_argument("duration", help="duration: 90 / 90m / 1h30m / 2h")
    sp.add_argument("--at", help="end timestamp (default NOW); start = at - duration")

    ac = sub.add_parser("active",
        help="tasks running right now (open CLOCK_IN) + today's elapsed + latest log",
        description="List tasks that are timing right now (open CLOCK_IN). Shows current session elapsed, today's total, and the most recent log. Good for live focus check and finding tasks you forgot to stop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Use cases:
  - Before lunch / a meeting, see which task is still timing
  - Late in the day, find a task you forgot to stop and wrap it up with wl stop <id>
  - When juggling several tasks, confirm current focus

Difference from wl day:
  - wl day        = full progress for the day (includes done / not-yet-started planned items), for end-of-day review
  - wl active     = what's timing right now (open CLOCK_IN), for live focus check

Output includes: current session elapsed + today's total (to decide stop or continue) + latest log (context).
Brief mode -q: id + elapsed only. Full log body: --log-format full.""")
    # ac has no other flags but we keep the variable for future args (e.g. --since to look at past activity)

    wa = sub.add_parser("wait",
        help="mark WAIT (blocked on others / external input); auto-closes CLOCK; multiple ids",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl wait 42                            # mark WAIT (suspended)
  wl wait 42 --note "waiting on review" # add a log explaining what we're waiting on
  wl wait 42 43 --note "waiting on approval" # batch

Note: marking WAIT auto-closes any open CLOCK_IN (WAIT = suspended, no longer timing). Use wl reopen to revert to TODO.""")
    wa.add_argument("ids", type=int, nargs="+", help="node id(s)")
    wa.add_argument("--note", help="add a log explaining what you're waiting on")

    ro = sub.add_parser("reopen",
        help="undo DONE/CANCELED/WAIT/LATER back to TODO + clear closed_at (multiple ids)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl reopen 42         # single id
  wl reopen 42 43      # batch

Inverse of wl done/cancel. Use when you change your mind and want to restart a task.""")
    ro.add_argument("ids", type=int, nargs="+", help="node id(s)")

    cx = sub.add_parser("cancel",
        help="mark CANCELED + closed_at (drop / no-longer-doing; parallel to done); --log/--at supported",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl cancel 42                        # drop it
  wl cancel 42 -m "deprioritized"     # close + log reason in one step
  wl cancel 42 --at 2026-05-30 16:00  # closed_at + log use past timestamp

Difference from wl done: done = delivered; cancel = dropped, not doing. Both write closed_at and accept --log/--at.
Difference from wl wait: wait = paused (still planning to do); cancel = not doing it.""")
    cx.add_argument("ids", type=int, nargs="+", help="node id(s)")
    cx.add_argument("--log", "-m", help="add a log explaining why you're canceling")
    cx.add_argument("--at", help="use this timestamp for closed_at + log")

    ln = sub.add_parser("link",
        help="link a node to a vault doc name (no .md suffix; multiple ids)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl link 42 "Project hub doc"          # link
  wl link 42 43 "shared topic"          # link multiple ids at once
  # vault doc name matches the [[wikilink]] title (no .md suffix)

After linking, wl show <id> displays links: [[doc name]] at the top.
Design: the knowledge layer (vault) and execution layer (wl) stay decoupled; wl only knows the linked doc name and does not sync content back.""")
    ln.add_argument("ids", type=int, nargs="+", metavar="id", help="node id(s)")
    ln.add_argument("vault_doc")

    se = sub.add_parser("set",
        help="set/update a custom key=value prop (UDA-style)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl set 42 owner xyb                # add owner to a task
  wl set 42 linear ABC-449             # backfill Linear ID
  wl set <day_id> summary "..."        # meta prop (but prefer wl recap for end-of-day)
  wl set <day_id> goal "deliver X"     # (prefer wl goal: stamps a timestamp)
  wl set <week_id> overview "..."      # week overview
  wl set <month_id> top5 "..."         # monthly Top5

Difference from wl recap/goal: those target the day node and stamp a timestamp; they are convenience aliases for wl set summary/goal.""")
    se.add_argument("id", type=int)
    se.add_argument("key")
    se.add_argument("value")

    sh = sub.add_parser("show",
        help="full detail + timeline for a node (accepts multiple ids)",
        description="All info on a node: metadata (status/priority/parents/tags/links/props) + timeline (created/scheduled/closed/log merged by time). Timeline defaults to the last 5; use --all-timelines for full expansion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl show 42                          # full detail + last 5 timeline entries
  wl show 42 -q                       # brief: skip timeline
  wl show 42 --timeline-tail 20       # show a longer timeline
  wl show 42 --all-timelines          # full expansion
  wl show 42 --log-format full        # do not truncate log body in timeline

Differences from related commands:
  - wl show <id>      single-node detail + timeline (deep dive on one node)
  - wl focus <id>     single node + upstream path + downstream subtree (context view)
  - wl logs --id <id> only log stream for that node (no metadata)""")
    sh.add_argument("ids", type=int, nargs="+", metavar="id", help="node id(s)")
    sh.add_argument("--no-timeline", action="store_true",
                    help="skip the timeline; only show meta+tags+links (same as --brief)")
    sh.add_argument("--timeline-tail", type=int, metavar="N",
                    help="only show the latest N timeline entries (default 5, with middle elided)")
    sh.add_argument("--all-timelines", action="store_true",
                    help="full timeline, no elision")

    ls = sub.add_parser("ls", help="list nodes (default limit 20; see shell ls -t / -S / -r-style dimensions)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog="""\
Common examples (precise queries, shell-ls multi-dimensional):
  wl ls --parent 45                     children of #45 (like ls dir/)
  wl ls --kind project                  only projects
  wl ls --tag work,dev                multi-tag AND filter
  wl ls --unscheduled --kind task       unscheduled tasks (inbox)
  wl ls --sort created -r --limit 5     5 most-recently-created (like ls -tr -5)
  wl ls --sort updated --limit 10       10 most-recently-logged (like ls -t)
  wl ls --recent 7                      anything that changed in the last 7 days
  wl ls --ids 39 41 270                 look at specific ids directly (like ls f1 f2)
  wl ls --status WAIT                   blocked / waiting on others
  wl ls --all                           remove the 20-row limit + include DONE/CANCELED

See also: wl find <q> / wl day / wl active / wl projects (each has a dedicated entry point sharper than ls)""")
    ls.add_argument("--kind", help="filter by kind (task/habit/meetlog/project/area/...)")
    ls.add_argument("--status", help="filter by status (TODO/DOING/DONE/WAIT/LATER/CANCELED)")
    ls.add_argument("--tag", help="comma-separated tags, AND filter")
    ls.add_argument("--parent", type=int, help="only direct children of this node")
    ls.add_argument("--all", action="store_true", help="include DONE/CANCELED + remove the limit cap")
    ls.add_argument("--limit", type=int, metavar="N",
                    help="show only the first N (default 20; 0 = no cap)")
    ls.add_argument("--top", type=int, metavar="N",
                    help="take the top N under the current sort (often paired with --sort)")
    ls.add_argument("--sort", choices=["pri", "created", "updated", "closed", "scheduled", "title", "id"],
                    default="pri",
                    help="sort dimension (default pri = priority+id; updated = last log time, like shell ls -t)")
    ls.add_argument("--reverse", "-r", action="store_true",
                    help="reverse sort (like shell ls -r); pairs with --sort; default pri reversed = lowest priority first")
    ls.add_argument("--recent", type=int, metavar="N", default=None,
                    help="only items changed in the last N days (created / logged / closed)")
    ls.add_argument("--unscheduled", action="store_true",
                    help="only items not in sched (use this for 'unscheduled', not --status)")
    ls.add_argument("--ids", type=int, nargs="+", metavar="id",
                    help="list specific ids directly, skipping filters (like shell `ls file1 file2`)")

    tr = sub.add_parser("tree",
        help="tree view of nodes (default: timeline up to today + areas one level, ~30 rows)",
        description="Tree view of nodes. Default: timeline expanded up to today (year -> quarter -> month -> week -> today + today's tasks) + areas one level, ~30 rows to avoid scrolling. Use --root <id> to drill into a node.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl tree                             # default overview (today + areas one level)
  wl tree --root <area_id>            # area -> projects + tasks (default depth 3)
  wl tree --root <project_id>         # project subtree (tasks)
  wl tree --root <day_id> --depth 9   # full per-log expansion for a day
  wl tree --depth 9                   # full tree (from lifetime; can be large)
  wl tree --by project                # flat 2-level: project -> task
  wl tree --by tag                    # flat 2-level: semantic tag -> node

Differences from related commands:
  - wl tree            hierarchical browse (default overview; --root to drill)
  - wl day             log-date-driven view of a single day (not tied to tree)
  - wl projects        list projects as cards (subtask counts, no tree expansion)
  - wl ls --parent <N> flat list of direct children, no recursion""")
    tr.add_argument("--kind")
    tr.add_argument("--proj")
    tr.add_argument("--root", type=int, help="start tree from this node id")
    tr.add_argument("--by", choices=["project", "tag", "direction"], help="regroup by dimension (flat 2-level)")
    tr.add_argument("--depth", type=int, help="max depth")
    tr.add_argument("--no-logs", action="store_true",
                    help="don't expand logs under day-node activities (same as --brief)")
    tr.add_argument("--log-tail", type=int, metavar="N",
                    help="latest N logs per task in day-node activities (default 3, middle elided)")
    tr.add_argument("--all-logs", action="store_true",
                    help="full log expansion in day-node activities, no elision")

    fo = sub.add_parser("focus",
        help="focus on a node: upstream path + self + downstream subtree",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl focus 42                    # upstream path + self + direct children
  wl focus 42 --depth 3          # expand 3 levels downstream
  wl focus 42 --related          # also include tag-related nodes

Related: wl show is self + timeline only; wl ancestors/descendants only go one direction; wl focus combines them.""")
    fo.add_argument("id", type=int)
    fo.add_argument("--depth", type=int, help="max downstream depth")
    fo.add_argument("--related", action="store_true", help="also show tag-related nodes")

    an = sub.add_parser("ancestors",
        help="upstream path: ancestor chain from root to the node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: wl ancestors 42 -> Lifetime / Area / Project / Task. Inverse: wl descendants for the downstream subtree.")
    an.add_argument("id", type=int)

    de = sub.add_parser("descendants",
        help="downstream subtree: all descendants of a node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: wl descendants 7 --depth 2 -> two levels of children under #7. wl tree --root 7 is equivalent but rendered as a tree.")
    de.add_argument("id", type=int)
    de.add_argument("--depth", type=int, help="max depth")

    pj = sub.add_parser("projects", parents=[window],
        help="list active projects + subtask counts + recent activity",
        description="List all active projects (kind=project, status not DONE/CANCELED) with subtask counts + last log time. --since filters to projects with activity after that date.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl projects                         # all active projects
  wl projects --since 2026-05-01      # active since May 1
  wl projects --week 2026-W22         # active this week
  wl projects --top 5                 # top 5 by priority
  wl projects --all                   # include DONE/CANCELED projects

Differences from related commands:
  - wl projects        card view (subtask counts + recent activity)
  - wl tree --by project flat 2-level: project -> linked tasks (includes tag links)
  - wl ls --kind project plain list of project nodes (no card, no subtask stats)""")
    pj.add_argument("--all", action="store_true", help="include DONE/CANCELED projects")
    pj.add_argument("--limit", type=int, metavar="N", help="show only the first N")
    pj.add_argument("--top", type=int, metavar="N",
                    help="top N by priority+id (semantics: high-priority active projects)")

    sub.add_parser("changes", parents=[window],
        help="per-project changes in a time window (added / done / log counts)",
        description="What happened to each project in a time window: tasks added, tasks closed, new log count. Good input for weekly reports and Linear updates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl changes --week 2026-W22          # this week's changes (per-project)
  wl changes --since 2026-05-01       # changes since May 1
  wl changes --month 2026-05          # whole month

Differences from related commands:
  - wl changes        change-focused: what was added / closed / how many logs
  - wl summary        state-distribution snapshot: counts of done/doing/todo
  - wl projects       project card view (subtask counts + last activity)

Weekly / Linear-update workflow: wl changes --week -> look at changes -> draft the report""")

    sm = sub.add_parser("summary", parents=[window],
        help="time-window aggregate: done/doing/added counts + grouped by project or day",
        description="Snapshot of current state distribution in a time window: counts of done / doing / added, grouped by project (default) or day. First-pass material for weekly / monthly reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl summary --week 2026-W22                 # this week (by project)
  wl summary --month 2026-05                 # full month
  wl summary --since 2026-05-01 --by day     # group by day
  wl summary --week 2026-W22 --top 5         # only the 5 most-progressed projects
  wl summary --week 2026-W22 --projects-only # project rows only, no task expansion
  wl summary --week ... -q                   # AI context-grab default brief (large token savings)

Differences from related commands:
  - wl summary    state snapshot (counts of done / doing / todo)
  - wl changes    change view (added / closed / log count)
  - wl day        full single-day view (with log body)

Dedup: by default a task appearing in multiple projects (via parent + shared tag) is listed only once. --no-dedup restores the old behavior.""")
    sm.add_argument("--by", choices=["project", "day"], default="project", help="aggregate dimension (default: project)")
    sm.add_argument("--projects-only", action="store_true",
                    help="project rows only, no task expansion (same as --brief but explicit)")
    sm.add_argument("--top", type=int, metavar="N",
                    help="only the top N most-progressed projects")
    sm.add_argument("--no-dedup", action="store_true",
                    help="no dedup: a task across multiple projects is repeated in each bucket (old behavior)")

    dy = sub.add_parser("day",
        help="full view of a day (default today): bucket -> project/plan -> task -> log",
        description="Full view of one day: work/personal/other -> (planned/unplanned/project/priority) -> task -> indented logs. Top shows end-of-day summary + today's goal + Top5 (if set). Defaults to log-date-driven (works for past days too).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl day                              # today
  wl day 2026-05-30                   # historical day
  wl day yesterday                    # short form (yesterday / day-before-yesterday / tomorrow / day-after-tomorrow)
  wl day --by project                 # change grouping (default plan: planned/unplanned)
  wl day --by priority                # group by P0/P1/P2
  wl day --log-tail 1                 # logs default to last 3, narrow to 1
  wl day --all-logs                   # full log expansion (default is last 3)
  wl day --no-logs                    # don't expand logs, just tasks
  wl day --log-format full            # don't truncate body

Differences from related commands:
  - wl day        single-day overview (plan + actual + status mix, including not-done items)
  - wl active     tasks running right now (live focus, no history)
  - wl logs --date YYYY-MM-DD    flat log stream for that day (no task structure)
  - wl tree --root <day_id>       subtree of that day node (uses tree structure)

End-of-day workflow: wl day -> review the day -> wl recap "..." to write the summary.""")
    dy.add_argument("date", nargs="?", help="YYYY-MM-DD (default: today)")
    dy.add_argument("--by", choices=["plan", "project", "priority"], default="plan",
                    help="secondary grouping dimension (default: plan = planned/unplanned)")
    dy.add_argument("--depth", type=int, help="(reserved, currently unused)")
    dy.add_argument("--no-logs", action="store_true",
                    help="don't expand any log body (same as --brief)")
    dy.add_argument("--log-tail", type=int, metavar="N",
                    help="expand at most the latest N logs per task (default 3, middle elided)")
    dy.add_argument("--all-logs", action="store_true",
                    help="full log expansion, no elision (overrides default tail=3)")

    g = sub.add_parser("goal",
        help="read/write today's goal (auto-creates day node + prop 'goal')",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl goal "deliver X today"     # write
  wl goal                       # read (no text)

wl day shows a top blockquote with the goal. Use at the end of morning planning. Pair with wl recap for end-of-day summary.""")
    g.add_argument("text", nargs="?", help="no arg = read today's goal; with text = write")

    rc = sub.add_parser("recap",
        help="read/write today's end-of-day summary (auto-stamps summary_at)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl recap "three things today: ..."  # write + auto-stamp summary_at
  wl recap                             # read

wl day shows "Recap: ... (written at MM-DD HH:MM)" at the top;
if there are new non-CLOCK logs after recap, wl day shows "⚠ N changes after recap, consider rewriting".
Using wl set <day_id> summary "..." directly does not stamp the timestamp; not recommended.""")
    rc.add_argument("text", nargs="?", help="no arg = read; with text = write")

    tk = sub.add_parser("tick",
        help="quick check-in: add a log to each node today (batch habit check-in)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl tick 39                          # default body "✓ done"
  wl tick 39 --note "6 pull-ups"      # custom note
  wl tick 39 40 41                    # batch check-in multiple habits
  wl tick 218 --done                  # also mark DONE (one-off task)

Difference from wl log: tick defaults to '✓ done' body, great for one-key habit check-in; log needs explicit content.
For interactive habit batch review, use wl checkin (interactive multi-select).""")
    tk.add_argument("ids", type=int, nargs="+", help="node id(s)")
    tk.add_argument("--note", help="custom log body (default '✓ done')")
    tk.add_argument("--done", action="store_true", help="also mark DONE")

    def _log_id_arg(s):
        # accepts '#L282' / 'L282' / '282' (wl show / wl logs displays as #L<id>)
        t = s.lstrip("#")
        return int(t[1:] if t.lower().startswith("l") else t)

    ul = sub.add_parser("unlog",
        help="delete a log entry: #L<id> exact / --node delete latest that day (undo tick)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl unlog #L282                      # exact delete by log id
  wl unlog L282                       # same (# optional)
  wl unlog 282                        # same (plain number)
  wl unlog --node 39                  # delete the latest non-CLOCK log for #39 today
  wl unlog --node 39 --date yesterday # latest log that day
  wl unlog --node 39 --all            # delete all non-CLOCK logs for #39 that day

Find a log id: wl show <node_id> or wl logs --id <node_id> displays #L<id> in the timeline.
CLOCK_IN/OUT logs cannot be deleted (would break timing pairs). Edit a mistyped log with wl relog #L<id> instead.""")
    ul.add_argument("log_id", type=_log_id_arg, nargs="?",
                    help="log id (e.g. #L282 / L282 / 282; from wl show / wl logs timeline)")
    ul.add_argument("--node", type=int, help="delete by node id (default: latest non-CLOCK log today)")
    ul.add_argument("--date", help="with --node: delete logs from that day (default today)")
    ul.add_argument("--all", action="store_true", help="with --node: delete all non-CLOCK logs for that node that day")

    rl = sub.add_parser("relog",
        help="rewrite a log: new body / new time / editor (CLOCK not accepted)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl relog #L282 "fixed content"     # change body
  wl relog #L282 -m "fixed content"  # -m mutually exclusive with positional
  wl relog #L282 --at 14:30          # only change time (keep date)
  wl relog #L282 --at 2026-05-30     # only change date (keep time)
  wl relog #L282                     # no body/--at -> open $EDITOR

CLOCK_IN/OUT logs cannot be edited (would break timing pairs); use wl stop --at to fix CLOCK times.
Cannot move a log across nodes (that's unlog + log).""")
    rl.add_argument("log_id", type=_log_id_arg,
                    help="log id (#L282 / L282 / 282; from wl show / wl logs)")
    rl.add_argument("body", nargs="*", help="new body (positional; no arg -> -m / --at / EDITOR)")
    rl.add_argument("-m", "--message", help="new body (mutually exclusive with positional body; explicit)")
    rl.add_argument("--at", help="change time: HH:MM (keep date) / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")

    ci = sub.add_parser("checkin",
        help="interactive check-in of today's habits (default multi-select arrows / space / Enter)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl checkin                          # default multi-select (arrows / space / Enter)
  wl checkin --linear                 # fallback: prompt y/n/note/q per item (allows per-item note)
  wl checkin --all-kinds              # not just habit; include all task/meetlog/... scheduled today

End-of-day: run wl checkin once to review every habit that's due today.
For single habit check-in, use wl tick <id>.""")
    ci.add_argument("--kind", help="filter by kind (default: habit; use --all-kinds to see anything scheduled)")
    ci.add_argument("--all-kinds", action="store_true",
                    help="no kind filter: habit/task/meetlog all listed (including everything scheduled today)")
    ci.add_argument("--linear", action="store_true",
                    help="fallback mode: prompt y/n/note/q per item (allows per-item note; auto-used when not on a TTY)")

    sc = sub.add_parser("sched",
        help="forward planning: schedule a task to a day / recurring rule (drives wl day 'planned')",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Schedule to a specific day:
  wl sched 42 2026-06-15              # exact date
  wl sched 42 tomorrow                # short form (today / yesterday / tomorrow / day-after-tomorrow)

Recurring rules (--recur); each supports -1 = last day of the cycle:
  wl sched 42 --recur daily                       # every day
  wl sched 42 --recur weekly:Mon,Wed,Fri          # also numeric weekly:1,3,5 / -1=Sun
  wl sched 42 --recur monthly:5,15,-1             # day 5/15/last each month
  wl sched 42 --recur quarterly:1-15              # 15th of the first month in each quarter
  wl sched 42 --recur quarterly:-1                # last day of each quarter (3/31, 6/30, ...)
  wl sched 42 --recur yearly:03-21                # March 21 every year
  wl sched 42 --recur yearly:-1                   # last day of year (12-31)

Clear:
  wl sched 42 --clear                 # clear all schedule entries for this task

Difference from wl defer: sched writes to the sched table (precise; appears as "planned" in wl day); defer = status=LATER + rough hint.
Create + schedule in one line: wl add "..." --sched today""")
    sc.add_argument("id", type=int)
    sc.add_argument("when", nargs="?", help="YYYY-MM-DD / today / yesterday / tomorrow / day-after-tomorrow (one-off date)")
    sc.add_argument("--recur",
                    help="recurring rule (all support -1 = last day): daily / weekly:Mon|1-7|-1 / monthly:5|-1 / quarterly:M-D|-1 / yearly:MM-DD|-1")
    sc.add_argument("--clear", action="store_true", help="clear all schedule entries for this task")

    di = sub.add_parser("dateinfo",
        help="date metadata (holiday/vacation/working-day swap; shown in wl day header)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl dateinfo 2026-05-01 "Labor Day"        # single entry
  wl dateinfo 2026-05-03 "swap working day" # working day swap
  wl dateinfo --import holidays.json        # batch {"YYYY-MM-DD":"label"}
  wl dateinfo 2026-05-01 --clear            # clear

wl day shows "<date> <weekday> · <label>" at the top. Weekday comes from the date; dateinfo only stores the extra label.""")
    di.add_argument("date", nargs="?", help="YYYY-MM-DD")
    di.add_argument("label", nargs="?", help="label, e.g. Labor Day / swap working day / vacation")
    di.add_argument("--import", dest="import_file", metavar="FILE", help='batch import {"YYYY-MM-DD":"label"} JSON, - reads stdin')
    di.add_argument("--clear", action="store_true", help="clear the label for this date")

    im = sub.add_parser("import",
        help="bulk load from JSON ({add:[...],update:[...]}; main AI integration path)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
JSON format (single document):
  {
    "add": [
      {"ref":"p","title":"project name","kind":"project","priority":"A","tags":["work"],
       "children":[{"title":"subtask","kind":"task","priority":"A","status":"DONE","logs":["..."]}]},
      {"title":"another task","kind":"task","parent_ref":"p"}
    ],
    "update": [{"id":42,"status":"DONE","add_tags":["urgent"]}]
  }

Common:
  wl import data.json             # load
  wl import data.json --dry-run   # preview without writing

For AI to load a day's worklog / multiple nodes, use this rather than many wl add calls. wl apply is the other option (lightweight wl-diff format).""")
    im.add_argument("file", help="JSON file path, or - for stdin")
    im.add_argument("--dry-run", action="store_true", help="preview without writing")

    ap = sub.add_parser("apply",
        help="apply wl-diff lightweight bulk changes (+add/~update/-delete/ anchor; same format as wl output)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
wl-diff format:
    #6 [day] 2026-05-29            <- anchor: identifies an existing node as parent, not modified
  +   [x] [#A] morning check :planned:  <- add (indent = child), [x]=DONE
  +     @log monitoring note            <- log child
  ~ [x] #14                         <- change status of #14 (single-line shorthand)
  ~ #20                             <- complex update: lock + field operations
      +tag urgent                    <- sub-op: add tag
      -log unwanted log              <- sub-op: remove
  - #99                             <- delete (with subtree)

Common:
  wl apply diff.txt              # apply
  wl apply diff.txt --dry-run    # preview

Difference from wl import: import = JSON rich format (good for scripted generation); apply = wl-diff text format (good for small hand-written edits / AI deltas).""")
    ap.add_argument("file", help="wl-diff file path, or - for stdin")
    ap.add_argument("--dry-run", action="store_true", help="validate + preview without writing")

    fd = sub.add_parser("find",
        help="full-text search nodes (title/body/log/tag/prop/link, any match)",
        description="Full-text search across fields: title/body/log/tag/prop/link, any match returns. Default limit 20; --all removes it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl find skill                       # default limit 20
  wl find skill --limit 5             # only the first 5
  wl find skill --all                 # no limit
  wl find skill --kind project        # only projects
  wl find skill --in title,tag        # only in title/tag (default: all fields)

Differences from related commands:
  - wl find <q>           content search (across fields; common 'I remember mentioning X')
  - wl ls --tag X         precise tag filter (when you know the dimension)
  - wl ls --recent N      by time (recently active)

Before writing a new task / log, run wl find to check if there's an existing node to merge into, to avoid duplicates.""")
    fd.add_argument("query")
    fd.add_argument("--in", dest="in_", help="comma-separated fields to search (default: all)")
    fd.add_argument("--kind", help="filter by kind")
    fd.add_argument("--limit", type=int, metavar="N", help="show only the first N (default 20; use 0 or --all for no cap)")
    fd.add_argument("--all", action="store_true", help="no row limit")

    lg = sub.add_parser("logs", parents=[window],
        help="list log entries (default last 7 days; preset today/yesterday/week/recent)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl logs today                       # preset: today
  wl logs yesterday                   # yesterday
  wl logs week                        # since Monday this week
  wl logs recent                      # --days 1 + -q
  wl logs --id 42                     # all logs for a task
  wl logs --id 42 --tail 5            # last 5 logs for a task
  wl logs --since 2026-05-01          # time window
  wl logs --by-task --tail 3          # aggregate by task, last 3 per task
  wl logs --group day --by project    # group by day -> project -> task

Differences from related commands:
  - wl logs       flat log stream (with task title, one line per log)
  - wl day        structured single-day view (plan + task tree + logs)
  - wl show <id>  single-node detail + timeline

Default window of 7 days avoids full-history flooding. Use --since/--until/--week/--month for precise windows.""")
    lg.add_argument("preset", nargs="?",
                    choices=["today", "yesterday", "week", "recent"],
                    help="quick preset: today/yesterday (= --date short form) / week (since Monday) / recent (--days 1 -q)")
    lg.add_argument("--id", type=int)
    lg.add_argument("--date", help="YYYY-MM-DD / today / yesterday / day-before-yesterday (only this day)")
    lg.add_argument("--days", type=int, default=7, help="default window in days when no since/date (default: 7)")
    lg.add_argument("--group", choices=["none", "day"], default="none",
                    help="day = group by date -> bucket -> task -> log (indented)")
    lg.add_argument("--by", choices=["project", "priority", "plan"], default="project",
                    help="secondary grouping dimension under --group day (default: project)")
    lg.add_argument("--no-body", action="store_true",
                    help="only [date] #id title, no body (same as --brief)")
    lg.add_argument("--by-task", action="store_true",
                    help="aggregate by task (pairs with --tail to get last N per task)")
    lg.add_argument("--tail", type=int, metavar="N",
                    help="last N logs per task (pairs with --by-task / --group day; default 3, middle elided)")
    lg.add_argument("--all-logs", action="store_true",
                    help="full log expansion, no tail truncation (overrides default tail=3)")
    lg.add_argument("--limit", type=int, metavar="N",
                    help="show only the first N logs (for non --by-task cases, to prevent flooding)")

    sub.add_parser("themes",
        help="list all color themes (one-line preview per theme)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Switch theme: top-level --theme {auto,dark,light,mono} flag, or export WL_THEME=...; auto probes terminal background and picks dark/light.")

    pc = sub.add_parser("print-completion",
        help="dump shell completion script (argparse -> fish/bash/zsh; init-load model)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Usage (write once to your shell rc, then new shells auto-load; stays in sync with wl.py changes):
  # fish: add to ~/.config/fish/config.fish
  wl print-completion fish | source

  # bash: add to ~/.bashrc
  eval "$(wl print-completion bash)"

  # zsh: add to ~/.zshrc
  eval "$(wl print-completion zsh)"

Same pattern as starship/direnv/zoxide.

User aliases: add [aliases] section to ~/.config/wl/aliases.ini (e.g. d = day / c = checkin / ...); new shells pick them up (uniform across shells).""")
    pc.add_argument("shell", choices=["fish", "bash", "zsh"], help="target shell")

    return p


HANDLERS = {
    "init": cmd_init,
    "add": cmd_add,
    "log": cmd_log,
    "done": cmd_done,
    "defer": cmd_defer,
    "start": cmd_start,
    "stop": cmd_stop,
    "spent": cmd_spent,
    "active": cmd_active,
    "wait": cmd_wait,
    "reopen": cmd_reopen,
    "cancel": cmd_cancel,
    "link": cmd_link,
    "set": cmd_set,
    "show": cmd_show,
    "ls": cmd_ls,
    "tree": cmd_tree,
    "projects": cmd_projects,
    "changes": cmd_changes,
    "summary": cmd_summary,
    "focus": cmd_focus,
    "ancestors": cmd_ancestors,
    "descendants": cmd_descendants,
    "day": cmd_day,
    "goal": cmd_goal,
    "recap": cmd_summary_prop,
    "tick": cmd_tick,
    "unlog": cmd_unlog,
    "relog": cmd_relog,
    "checkin": cmd_checkin,
    "sched": cmd_sched,
    "dateinfo": cmd_dateinfo,
    "import": cmd_import,
    "apply": cmd_apply,
    "find": cmd_find,
    "logs": cmd_logs,
    "themes": cmd_themes,
    "print-completion": cmd_print_completion,
}


def main():  # pragma: no cover -- argparse entry; tests invoke HANDLERS[cmd] directly to bypass
    parser = build_parser()
    args = parser.parse_args()
    # resolve alias back to its primary name (e.g. wl d -> day)
    if args.cmd not in HANDLERS:
        for target, alist in (_USER_ALIASES or {}).items():
            if args.cmd in alist:
                args.cmd = target
                break
    # print-completion is a meta command; needs no DB / console
    if args.cmd == "print-completion":
        HANDLERS[args.cmd](args, None)
        return
    _init_console(args.color, args.theme)
    ensure_db()
    con = db_connect()
    try:
        HANDLERS[args.cmd](args, con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
