"""worklog commands: meta group."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .. import render
from .. import timeutil as _tu
from .. import db_table as _db
from .metric import checkin_metric
from ..queries import _has_checkin, _latest_typed_log, _set_typed_log
from ..helpers import (
    _apply_top_limit,
    _fmt_dur,
    _is_brief,
    _log_full,
    _norm_sched,
    _resolve_at_ts,
    _resolve_concrete_date,
    _resolve_log_tail,
    _resolve_window,
    _sched_anchor,
    _sched_display,
    _sched_kind,
    _sched_sort_key,
    _status_marker,
    _term_width,
    _truncate_log_body,
    GENERIC_TAGS,
)
from ..queries import (
    _ancestors_chain,
    _check_ids_exist,
    _collect_descendants,
    _has_tag,
    _insert_log,
    _node_bucket,
    _node_clock_min,
    _node_exists,
    _node_plan,
    _node_project,
    _node_tags,
    _project_members,
    _sec_group,
    _status_filter_sql,
    _upsert_prop,
)
from ..render import (
    _PRI_STYLE,
    _STATUS_STYLE,
    _RICH_AVAIL,
    _resolve_theme,
    THEMES,
    _c,
    _hl,
    _node_line,
    _print_truncation_hint,
    _snippet,
    out,
)
from ..xdg import _resolve_db_path, _resolve_aliases_path, _xdg_data_home, _xdg_config_home

# Lazy access to cli module (for DB wrappers + module state).
# Used at function call time (not at import) to avoid the cli ↔ commands
# import cycle.
from .. import cli as _cli  # noqa: E402


from .views import _cn_weekday, _date_label, _scheduled_node_ids

from .state import _ids_list
from .views import _WEEKDAY_ABBR, _cn_weekday, _date_label, _scheduled_node_ids

def cmd_init(args, con):
    _cli.db_init(con)
    print(f"✓ DB initialized: {_resolve_db_path(args)}")

def cmd_config(args, con):
    """Print resolved configuration: where the DB and config files live + env."""
    db = _resolve_db_path(args)
    if getattr(args, "db", None):
        db_src = "--db flag"
    elif os.environ.get("WORKLOG_DB"):
        db_src = "$WORKLOG_DB"
    else:
        db_src = "XDG default"
    db_exists = db.exists()
    db_size = f"{db.stat().st_size:,} bytes" if db_exists else "missing — run `wl init`"

    aliases = _resolve_aliases_path()

    def _row(label, value, hint=""):
        hint_part = "  " + _c(hint, "meta") if hint else ""
        out(f"  {label:<18} {value}{hint_part}")

    out(_c(f"worklog {_cli.__version__}", "header"))
    out("")
    out(_c("paths:", "header"))
    _row("database", db, f"[{db_src}] {db_size}")
    _row("aliases", aliases, "(exists)" if aliases.exists() else "(not configured)")
    out("")
    out(_c("XDG directories:", "header"))
    _row("XDG_DATA_HOME", _xdg_data_home(), "(env set)" if os.environ.get("XDG_DATA_HOME") else "(default)")
    _row("XDG_CONFIG_HOME", _xdg_config_home(), "(env set)" if os.environ.get("XDG_CONFIG_HOME") else "(default)")
    out("")
    out(_c("environment:", "header"))
    for var in ("WORKLOG_DB", "WORKLOG_COLOR", "WORKLOG_THEME", "NO_COLOR"):
        val = os.environ.get(var)
        _row(var, val if val else _c("(not set)", "meta"))
    out("")
    out(_c("runtime:", "header"))
    _row("python", sys.executable, f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    _row("rich", "available" if render._RICH_AVAIL else "not installed (plain-text mode)")

def cmd_migrate(args, con):
    """List + apply pending SQL migrations (`migrations/NNNN_*.sql`).

    Idempotent: re-running after everything is applied prints "up to date".
    Failure mid-sequence rolls back the offending migration and leaves the DB
    at the last successfully-applied number — re-run after fixing.
    """
    files = _cli._migration_files()
    current = _cli._db_version(con)
    pending = [p for p in files if int(p.stem.split("_", 1)[0]) > current]
    if not pending:
        out(_c(f"✓ DB at version {current}, no pending migrations ({len(files)} total).", "done"))
        return
    out(_c(f"applying {len(pending)} migration(s) (DB at version {current}):", "header"))
    applied = _cli._run_migrations(con, verbose=True)
    new_version = _cli._db_version(con)
    out(_c(f"✓ DB now at version {new_version} ({len(applied)} migration(s) applied).", "done"))


# ─── command handlers ───

def cmd_themes(args, con):
    """List all color themes, each rendering a one-line sample in its own palette for comparison."""
    req = args.theme or os.environ.get("WORKLOG_THEME") or "auto"
    cur = _resolve_theme(req)  # resolve auto to a real theme
    auto_note = f" (auto -> {cur})" if req in (None, "auto") else ""
    no_color = args.color == "never" or os.environ.get("NO_COLOR")
    if not render._RICH_AVAIL or no_color:
        # no rich or color explicitly off: plain text listing
        for name in THEMES:
            mark = "  <- current" if name == cur else ""
            print(f"■ {name}{mark}")
        print(f"current: {req}{auto_note}")
        if not render._RICH_AVAIL:
            print("(rich not installed; no color preview; pip install rich)")
        return
    # render the sample with each theme's own palette (force_terminal: keeps colors when piped to less -R)
    for name in THEMES:
        prev = render._RichConsole(theme=render._RichTheme(THEMES[name]), force_terminal=True, highlight=False, soft_wrap=True)
        mark = f"  [done]<- current {auto_note}[/done]" if name == cur else ""
        prev.print(f"[header]■ {name}[/header]{mark}")
        prev.print("  [done]\\[x][/done] [pri_a]\\[#A][/pri_a] [id]#42[/id] [kind]\\[project][/kind] "
                   "sample task with [hit]match[/hit] [planned]·planned[/planned]  [clock]⏱30min[/clock]  [tag]:work:[/tag]")
        prev.print("  [doing]\\[/][/doing] [pri_b]\\[#B][/pri_b] [id]#43[/id] doing sample    "
                   "[later]\\[>][/later] [pri_c]\\[#C][/pri_c] [id]#44[/id] later sample  [meta]«meta»[/meta]")
        prev.print()


# --- helpers ---


# --- argparse ---

def cmd_dateinfo(args, con):
    """Date metadata: holiday / vacation / working-day-swap label. Set one / batch-import a yearly holiday table / list."""
    if args.import_file:
        import json

        raw = sys.stdin.read() if args.import_file == "-" else Path(args.import_file).read_text(encoding="utf-8")
        data = json.loads(raw)  # {"2026-05-01": "Labor Day", ...}
        n = 0
        for d, label in data.items():
            _db.insert(con, "date_meta", {"date": d, "label": label}, or_="replace")
            n += 1
        con.commit()
        out(_c(f"✓ imported {n} date metadata entries", "meta"))
        return
    if args.date and args.label:
        _db.insert(con, "date_meta", {"date": args.date, "label": args.label}, or_="replace")
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

def cmd_goal(args, con):
    """Shortcut to read/write today's goal: `wl goal` reads; `wl goal 'text'` writes. Today's day-node is auto-created if missing.
    Stored as a tag=goal log (history-preserving): each write appends a new log; the latest is the current goal."""
    nid = _ensure_today_day(con)
    if not args.text:
        row = _latest_typed_log(con, nid, "goal")
        out(row["body"] if row and row["body"] else _c("(no goal set for today)", "meta"))
        return
    _set_typed_log(con, nid, "goal", args.text)
    con.commit()
    out(_c(f"✓ today's goal: {args.text}", "meta"))

def cmd_summary_prop(args, con):
    """Shortcut to read/write a day's end-of-day recap (default today; --date for a past day).
    Stored as a tag=summary log; each write appends a new log (history kept), and the log's
    own logged_at is the 'written at' time used to detect changes added after the recap."""
    from datetime import date, datetime as _dt
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
    at = con.execute("SELECT logged_at FROM log WHERE id = ?", (log_id,)).fetchone()["logged_at"]
    out(_c(f"✓ {label}'s summary (written at {at}): {args.text}", "meta"))

def cmd_checkin(args, con):
    """Interactive check-in for today's habits.
    Default: multi-select (up/down + space + Enter), pick all at once and check in.
    --per-item: per-item prompt mode (allows per-item note; also the fallback for non-TTY / piped input)."""
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

    if getattr(args, "per_item", False) or not _is_interactive_tty():
        _checkin_per_item(con, rows)
        return

    header = _c(f"{today} · pick habits done today (already checked in {pre_done}/{len(rows)})", "header")
    # default unselected for all (use space to toggle on what you did); intuitive: 'mark what I did' not 'unmark what I missed'
    options = [(f"#{r['id']} {r['title']}", False) for r in pending]
    chosen = _multi_select_tty(options, header)
    if chosen is None:
        out(_c("(canceled, no changes made)", "meta"))
        return

    for i in chosen:
        nid = pending[i]["id"]
        _insert_log(con, nid, "✓ done")
        log_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        checkin_metric(con, log_id, nid, today)
    con.commit()
    done_now = len(chosen)
    skipped = len(pending) - done_now
    out(_c(
        f"done {pre_done + done_now}/{len(rows)} · new this run {done_now}" +
        (f" · skipped {skipped}" if skipped else "") +
        " · for detailed notes use `wl tick <id> --note ...` or `wl checkin --per-item`",
        "header"))

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
            # idempotent: don't insert a duplicate (node_id, rrule) row
            exists = con.execute(
                "SELECT 1 FROM sched WHERE node_id = ? AND rrule = ?", (nid, rule)
            ).fetchone()
            if exists:
                out(_c(f"= #{nid} already on recurring schedule: {rule}", "meta"))
            else:
                _db.insert(con, "sched", {"node_id": nid, "rrule": rule, "created_at": _tu.utc_now()})
                out(_c(f"✓ #{nid} recurring schedule: {rule}", "meta"))
        con.commit()
    if args.when:
        try:
            d = _resolve_concrete_date(args.when)
        except ValueError:
            sys.exit(f"✗ invalid date '{args.when}' (use YYYY-MM-DD / today / yesterday / tomorrow / day-after-tomorrow)")
        for nid in ids:
            # idempotent: don't insert a duplicate (node_id, on_date) row
            exists = con.execute(
                "SELECT 1 FROM sched WHERE node_id = ? AND on_date = ?", (nid, d)
            ).fetchone()
            if exists:
                out(_c(f"= #{nid} already scheduled to {d}", "meta"))
            else:
                _db.insert(con, "sched", {"node_id": nid, "on_date": d, "created_at": _tu.utc_now()})
                out(_c(f"✓ #{nid} scheduled to {d}", "meta"))
        con.commit()

def _ensure_time_ancestors(con, d):
    """Ensure the time skeleton year→quarter→month→week exists for date `d`, creating
    any missing level, and return the week node id (the day node's parent).

    Lookup is lenient so we reuse an existing node regardless of title style — a year
    written `2026` or `2026 年` both match the `2026%` probe. New nodes are created in
    plain ISO form (year `YYYY`, quarter `YYYY-Qn`, month `YYYY-MM`, week ISO `YYYY-Www`).
    Year hangs under an existing `lifetime` node if there is one, else stays top-level.
    Without this, a day created on the first of a month/week (when month/week don't yet
    exist) dangled directly under lifetime/NULL and broke per-month aggregation (#410).
    """
    y, m = d.year, d.month
    iso = d.isocalendar()
    q = (m - 1) // 3 + 1

    def _get_or_make(kind, lookup_sql, lookup_param, new_title, parent_id):
        row = con.execute(lookup_sql, lookup_param).fetchone()
        if row:
            return row["id"]
        return _db.insert(con, "node", {
            "parent_id": parent_id, "title": new_title, "kind": kind, "created_at": _tu.utc_now(),
        })

    lt = con.execute("SELECT id FROM node WHERE kind='lifetime' ORDER BY id LIMIT 1").fetchone()
    lt_id = lt["id"] if lt else None
    yr_id = _get_or_make(
        "year", "SELECT id FROM node WHERE kind='year' AND title LIKE ? ORDER BY id LIMIT 1",
        (f"{y}%",), str(y), lt_id)
    qr_id = _get_or_make(
        "quarter", "SELECT id FROM node WHERE kind='quarter' AND title = ? LIMIT 1",
        (f"{y}-Q{q}",), f"{y}-Q{q}", yr_id)
    mo_id = _get_or_make(
        "month", "SELECT id FROM node WHERE kind='month' AND title = ? LIMIT 1",
        (f"{y}-{m:02d}",), f"{y}-{m:02d}", qr_id)
    wk_title = f"{iso[0]}-W{iso[1]:02d}"
    wk_id = _get_or_make(
        "week", "SELECT id FROM node WHERE kind='week' AND title = ? LIMIT 1",
        (wk_title,), wk_title, mo_id)
    return wk_id

def _ensure_day(con, d):
    """Return the day-node id for date `d` (a datetime.date); create it if missing,
    building the full time skeleton (year→quarter→month→week) above it so it never
    dangles (#410). Works for any date, not just today — back-fills past days too."""
    iso = d.isoformat()
    r = con.execute(
        "SELECT id FROM node WHERE kind='day' AND title LIKE ? ORDER BY id LIMIT 1", (iso + "%",)
    ).fetchone()
    if r:
        return r["id"]
    wk_id = _ensure_time_ancestors(con, d)
    nid = _db.insert(con, "node", {
        "parent_id": wk_id, "title": iso, "kind": "day", "created_at": _tu.utc_now(),
    })
    con.commit()
    return nid

def _ensure_today_day(con):
    """Today's day-node id (thin wrapper over _ensure_day)."""
    from datetime import date
    return _ensure_day(con, _tu.today_date())

def _checkin_collect(con, args):
    """Collect today's habits to check in. Returns [{id, title, priority, kind, already}]."""
    from datetime import date as _d

    today = _tu.today()
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
        # "already done today" = structured check-in metric (not "any log that day")
        already = _has_checkin(con, nid, today)
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
    if not render._RICH_AVAIL or not _is_interactive_tty():
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
    # use a separate Console to avoid collision with wl's global render._CONSOLE theme/highlight
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

def _checkin_per_item(con, rows):
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
        from datetime import date as _d
        log_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        checkin_metric(con, log_id, nid, _tu.today())
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

