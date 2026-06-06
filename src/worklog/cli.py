#!/usr/bin/env python3
"""worklog (wl): SQLite-backed worklog tool, todo.sh-style CLI.

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

from importlib.metadata import version as _pkg_version, PackageNotFoundError as _PackageNotFoundError
try:
    __version__ = _pkg_version("pyworklog")
except _PackageNotFoundError:  # pragma: no cover -- only hit when running source w/o `uv sync`
    __version__ = "0.0.0+unknown"

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .xdg import _xdg_data_home, _xdg_config_home, _resolve_db_path, _resolve_aliases_path
from . import render
from .render import (
    _RICH_AVAIL, _THEME_KEYS, THEMES, _STATUS_STYLE, _PRI_STYLE,
    _resolve_color, _detect_bg_is_dark, _resolve_theme, _init_console,
    out, _c, _hl, _node_line, _snippet, _print_truncation_hint,
)
# backward-compat: cli._CONSOLE is a property-like passthrough so existing
# `wl._CONSOLE` reads in tests/main() see the live render._CONSOLE.
# (For writes, use `_init_console()` — never bind cli._CONSOLE directly.)
from .completion import (
    cmd_print_completion,
    _generate_fish_completion,
    _generate_bash_completion,
    _generate_zsh_completion,
)
from .queries import (
    _insert_log,
    _node_tags,
    _check_ids_exist,
    _upsert_prop,
    _status_filter_sql,
    _project_members,
    _ancestors_chain,
    _node_bucket,
    _node_project,
    _node_plan,
    _sec_group,
    _collect_descendants,
    _has_tag,
    _node_clock_min,
    _node_exists,
)
from .helpers import GENERIC_TAGS  # noqa: F401
from .helpers import (
    _fmt_dur,
    _apply_top_limit,
    _log_full,
    _status_marker,
    _resolve_window,
    _resolve_concrete_date,
    _resolve_at_ts,
    _term_width,
    _truncate_log_body,
    _is_brief,
    _resolve_log_tail,
    _norm_sched,
    _sched_kind,
    _sched_anchor,
    _sched_sort_key,
    _sched_display,
)

DB_PATH = _resolve_db_path()
ALIASES_PATH = _resolve_aliases_path()
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# --- rich highlighting (optional dep, auto-detected; missing or non-TTY -> plain text) ---



# ─── DB helpers (thin wrappers; impl lives in db.py) ───
from . import db as _db


def db_connect() -> sqlite3.Connection:
    return _db.db_connect(DB_PATH)


def _migration_files() -> list[Path]:
    return _db.migration_files(MIGRATIONS_DIR)


def _db_version(con: sqlite3.Connection) -> int:
    return _db.db_version(con)


def _run_migrations(con: sqlite3.Connection, verbose: bool = False) -> list[Path]:
    return _db.run_migrations(con, MIGRATIONS_DIR, verbose=verbose)


def db_init(con: sqlite3.Connection) -> None:
    _db.run_migrations(con, MIGRATIONS_DIR)


def ensure_db():
    _db.ensure_db(DB_PATH, MIGRATIONS_DIR)


def _load_user_aliases():
    """Read ~/.config/worklog/aliases.ini and return {target_cmd: [alias1, alias2, ...]}.
    Format:
        [aliases]
        d = day
        c = checkin
    Multiple aliases pointing to the same target are merged. Returns {} on failure / missing file.
    """
    import configparser
    # Resolve at call-time so that tests monkeypatching HOME / XDG_CONFIG_HOME
    # see the new path (the module-level ALIASES_PATH is resolved at import).
    path = str(_resolve_aliases_path())
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


# --- shared argument sets (WL#486): a node operation is reachable both as a top-level
# shortcut (`wl add`) and under the entity group (`wl node add`); both call the same
# arg-adder so the two forms stay identical and there's one definition to maintain.
def _args_node_add(p):
    p.add_argument("title")
    p.add_argument("-k", "--kind", default="task", help="node kind (default: task)")
    p.add_argument("-p", "--priority", choices=["A", "B", "C"])
    p.add_argument("-t", "--tag", help="comma-separated tags")
    p.add_argument("--proj", help="project (stored as prop)")
    p.add_argument("--parent", type=int, help="parent node id")
    p.add_argument("--status")
    p.add_argument("--scheduled", help="(rough hint, writes node.scheduled_date) scheduled time: YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / next-week / next-month / next-quarter")
    p.add_argument("--sched", help="(precise, writes the sched table = visible as planned in `wl day` for that date) date: YYYY-MM-DD / today / yesterday / tomorrow / day-after-tomorrow")
    p.add_argument("--deadline", help="deadline date YYYY-MM-DD")
    p.add_argument("--body", help="optional body text")
    p.add_argument("--log", "-m", help="insert a log entry right after creation (result / output / numbers)")
    p.add_argument("--done", action="store_true", help="mark DONE + write closed_at immediately after creation (retrospective task in one shot)")
    p.add_argument("--at", help="timestamp for --log + (if --done) closed_at (HH:MM / YYYY-MM-DD [HH:MM[:SS]])")
    p.add_argument("--link", help="also attach a vault doc (no .md suffix, same semantics as `wl link`)")
    p.add_argument("--metric", action="append", metavar="'tag [value] [unit]'",
                   help="attach a structured datapoint (repeatable); reuses the --log carrier or makes one: "
                        "--metric 'glucose 5.4 mmol/L' / --metric checkin")
    return p


def _args_node_ls(p):
    p.add_argument("--parent", type=int, help="only direct children of this node")
    p.add_argument("--all", action="store_true", help="include DONE/CANCELED + remove the limit cap")
    p.add_argument("--limit", type=int, metavar="N", help="show only the first N (default 20; 0 = no cap)")
    p.add_argument("--top", type=int, metavar="N", help="take the top N under the current sort (often paired with --sort)")
    p.add_argument("--sort", choices=["pri", "created", "updated", "closed", "scheduled", "title", "id"],
                   default="pri", help="sort dimension (default pri = priority+id; updated = last log time, like shell ls -t)")
    p.add_argument("--reverse", "-r", action="store_true",
                   help="reverse sort (like shell ls -r); pairs with --sort; default pri reversed = lowest priority first")
    p.add_argument("--recent", type=int, metavar="N", default=None,
                   help="only items changed in the last N days (created / logged / closed)")
    p.add_argument("--unscheduled", action="store_true",
                   help="only items not in sched (use this for 'unscheduled', not --status)")
    p.add_argument("--ids", type=int, nargs="+", metavar="id",
                   help="list specific ids directly, skipping filters (like shell `ls file1 file2`)")
    return p


def _args_node_show(p):
    p.add_argument("ids", type=int, nargs="+", metavar="id", help="node id(s)")
    p.add_argument("--no-timeline", action="store_true",
                   help="skip the timeline; only show meta+tags+links (same as --brief)")
    p.add_argument("--timeline-tail", type=int, metavar="N",
                   help="only show the latest N timeline entries (default 5, with middle elided)")
    p.add_argument("--all-timelines", action="store_true", help="full timeline, no elision")
    return p


def build_parser():
    global _USER_ALIASES
    if _USER_ALIASES is None:
        _USER_ALIASES = _load_user_aliases()
    user_aliases = _USER_ALIASES

    p = argparse.ArgumentParser(prog="wl", description="worklog: SQLite-backed worklog tool")
    p.add_argument("--version", action="version", version=f"wl {__version__}")
    p.add_argument("--db", metavar="PATH",
                   help="override the DB path for this invocation (handy for testing / multiple worklogs); takes precedence over $WORKLOG_DB and the XDG default")
    p.add_argument("--color", choices=["auto", "always", "never"], default=None,
                   help="color switch (default auto: enabled on TTY + rich; also reads $WORKLOG_COLOR/$NO_COLOR)")
    p.add_argument("--theme", default=None, choices=["auto"] + list(THEMES),
                   metavar="{auto,%s}" % ",".join(THEMES),
                   help="color theme (default auto: probe terminal bg, pick dark/light; reads $WORKLOG_THEME; see `wl themes`)")
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

    # node-filter parent parser (reused by ls/tree/day/logs/agenda so every list/view
    # command takes the SAME --tag/--kind/--status, with the same meaning — see
    # make_node_filter). --tag is comma-separated AND.
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("-t", "--tag", help="comma-separated tags, AND filter (e.g. -t work)")
    filters.add_argument("--kind", help="filter by kind (task/habit/meetlog/project/area/...)")
    filters.add_argument("--status", help="filter by status (TODO/DOING/DONE/WAIT/LATER/CANCELED)")

    _real_sub = p.add_subparsers(dest="cmd", required=False)

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
            pp = self._sub.add_parser(name, **kw)
            # accept -q/--brief AFTER the subcommand too (wl day -q), not only globally
            # before it (wl -q day). default=SUPPRESS so an omitted flag here does not
            # clobber a value already set on the global parser.
            pp.add_argument("-q", "--brief", action="store_true", default=argparse.SUPPRESS,
                            help="brief output (same as the global -q; accepted after the subcommand too)")
            return pp
        def __getattr__(self, k):
            return getattr(self._sub, k)
    sub = _SubWrapper(_real_sub)

    sub.add_parser("migrate",
        help="apply pending SQL migrations from migrations/NNNN_*.sql (auto-run on every command; this is the explicit form)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The DB version is tracked via `PRAGMA user_version`. Every migration in
`migrations/` is named NNNN_*.sql (numeric prefix sorts the apply order);
files with number > current PRAGMA user_version run in order, each in its
own transaction, then user_version is bumped.

Migrations are auto-applied by `ensure_db()` on every command, so you
rarely need to invoke this explicitly. Use it to see what's pending or
to retry after a failed migration.""")

    sub.add_parser("config",
        help="print resolved configuration: DB path, aliases path, XDG dirs, env vars",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shows where worklog reads from and how the runtime is configured.
Useful when:
  - you're not sure which DB `wl` is using ($WORKLOG_DB env vs XDG default)
  - you need to point another tool at the same DB / aliases file
  - the rich highlighting isn't appearing and you want to check theme/env

Read-only and side-effect free — does not create the DB if missing.""")

    sub.add_parser("init",
        help="initialize SQLite DB (default ~/.local/share/worklog/worklog.db; skips if it exists)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Run once on a fresh machine before using wl.

DB path resolution:
  1. --db PATH flag (per-invocation override)
  2. $WORKLOG_DB env var
  3. $XDG_DATA_HOME/worklog/worklog.db (default ~/.local/share/worklog/worklog.db)

Config (aliases.ini) lives at $XDG_CONFIG_HOME/worklog/aliases.ini (default ~/.config/worklog/aliases.ini).""")

    a = sub.add_parser("add",
        help="create a new node (task/project/area/meetlog/habit/day...); compound flags let you do add + log + done + sched + link in one shot",
        description="Create a new node (task/project/area/meetlog/habit/day/...). Compound flags support add + log + done + sched + link in one step, replacing several separate commands. Canonical form: `wl node add` (this is the shortcut; see `wl node -h`).",
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
    _args_node_add(a)

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
    g.add_argument("--metric", action="append", metavar="'tag [value] [unit]'",
                   help="attach a structured datapoint to this log (repeatable): "
                        "--metric 'glucose 5.4 mmol/L' / --metric 'pullups 8' / --metric checkin")

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
        help="defer a task to a future point (LATER + scheduled_date; fuzzy times supported)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl defer 42 2026-06-15     # defer to a precise date
  wl defer 42 next-month     # fuzzy
  wl defer 42 2026-Q3        # quarter
  wl defer 42 someday        # no scheduled time

Differences from wl sched:
  - wl defer  -> status=LATER + scheduled_date field (rough hint, does NOT appear as "planned" in wl day on that day)
  - wl sched  -> writes to sched table (precise, appears as "planned" in wl day on that day)
To schedule it as planned for a specific day, use wl sched. defer is for "set aside, vaguely revisit later".""")
    df.add_argument("id", type=int)
    df.add_argument("date", help="scheduled time (precise or fuzzy): YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / next-week / next-month / next-quarter")

    s = sub.add_parser("start",
        help="clock-in to start timing (batch ids; --at to backfill past time)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl start 42                       # start timing now (opens a clock interval)
  wl start 42 43                    # multiple tasks at once (parallel timers)
  wl start 42 --at 09:00            # backfill 9am start (forgot to clock in)
  wl start 42 --at 2026-05-30 14:30 # full ts

Related: close with wl stop <id>; see what's running via wl active; wl spent records a clock interval from a duration.""")
    s.add_argument("ids", type=int, nargs="+", help="node id(s)")
    s.add_argument("--at", help="backfill start time: HH:MM (today) / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")

    st = sub.add_parser("stop",
        help="clock-out to stop timing + compute elapsed (multiple ids; --at to backfill past end)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl stop 42                            # stop now, close the interval (elapsed=Nmin)
  wl stop 42 43                         # batch stop
  wl stop 42 --at 11:30                 # backfill 11:30 end (must be later than the clock start)
  wl stop 42 --at 2026-05-30 16:00      # full ts

Difference from wl spent: stop closes a prior open clock; spent creates a closed interval directly from a duration.""")
    st.add_argument("ids", type=int, nargs="+", help="node id(s)")
    st.add_argument("--at", help="backfill end time (must be later than the clock start)")

    sp = sub.add_parser("spent",
        help="record a past time spent (build a clock interval from a duration, good for retrospective entries)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl spent 42 45               # 45 minutes (start = NOW - 45m, stop = NOW)
  wl spent 42 90m              # same, with m suffix
  wl spent 42 1h30m            # 1 hour 30 minutes
  wl spent 42 2h               # 2 hours
  wl spent 42 30m --at 14:30   # end at 14:30, backfill start at 14:00

Difference from wl start/stop: spent builds a closed clock interval from a duration in one step; no need to start first. Good for "forgot to clock, recording it after the fact".""")
    sp.add_argument("id", type=int, help="node id")
    sp.add_argument("duration", help="duration: 90 / 90m / 1h30m / 2h")
    sp.add_argument("--at", help="end timestamp (default NOW); start = at - duration")

    ac = sub.add_parser("active",
        help="tasks running right now (open clock) + today's elapsed + latest log",
        description="List tasks that are timing right now (an open clock interval). Shows current session elapsed, today's total, and the most recent log. Good for live focus check and finding tasks you forgot to stop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Use cases:
  - Before lunch / a meeting, see which task is still timing
  - Late in the day, find a task you forgot to stop and wrap it up with wl stop <id>
  - When juggling several tasks, confirm current focus

Difference from wl day:
  - wl day        = full progress for the day (includes done / not-yet-started planned items), for end-of-day review
  - wl active     = what's timing right now (open clock), for live focus check

Output includes: current session elapsed + today's total (to decide stop or continue) + latest log (context).
Brief mode -q: id + elapsed only. Full log body: --log-format full.""")
    # ac has no other flags but we keep the variable for future args (e.g. --since to look at past activity)

    wa = sub.add_parser("wait",
        help="mark WAIT (blocked on others / external input); auto-closes the clock; multiple ids",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl wait 42                            # mark WAIT (suspended)
  wl wait 42 --note "waiting on review" # add a log explaining what we're waiting on
  wl wait 42 43 --note "waiting on approval" # batch

Note: marking WAIT auto-closes any open clock (WAIT = suspended, no longer timing). Use wl reopen to revert to TODO.""")
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

    ul = sub.add_parser("unlink",
        help="remove one vault-doc link from a node (symmetric with link; multiple ids)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
  wl unlink 42 "Project hub doc"        # remove that one link from #42
  wl unlink 42 43 "shared topic"        # from multiple ids at once

Removes a single link; the rest of the node's links are untouched (unlike clearing
them all). No-op with a notice if that link wasn't present.""")
    ul.add_argument("ids", type=int, nargs="+", metavar="id", help="node id(s)")
    ul.add_argument("vault_doc")

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

    tg = sub.add_parser("tag",
        help="add/remove real tags on a node: wl tag <id> +work -planned (bare = add; no ops = list)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Edits the real tag field (the tag table), unlike `wl set <id> tags ...` which would
just create a shadow 'tags' prop. Tags drive bucketing (work/personal) and grouping.

  wl tag 42 +work +P0       # add tags
  wl tag 42 -planned        # remove a tag
  wl tag 42 +work -other    # add and remove in one call
  wl tag 42 work            # bare word = add (same as +work)
  wl tag 42                 # no ops = list current tags""")
    tg.add_argument("id", type=int)
    tg.add_argument("ops", nargs=argparse.REMAINDER,
                    help="+tag adds, -tag removes, bare word adds; empty = list current tags")

    sh = sub.add_parser("show",
        help="full detail + timeline for a node (accepts multiple ids)",
        description="All info on a node: metadata (status/priority/parents/tags/links/props) + timeline (created/scheduled/closed/log merged by time). Timeline defaults to the last 5; use --all-timelines for full expansion. Canonical form: `wl node show` (this is the shortcut; see `wl node -h`).",
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
    _args_node_show(sh)

    # node entity group (WL#486): the metric-style `wl node <verb>` primitive CRUD.
    # The top-level add/ls/show are the high-frequency shortcuts onto the same handlers;
    # edit/rm/reparent are the field-edit / soft-delete / move primitives.
    nd = sub.add_parser("node",
        help="node primitive CRUD: add / ls / show / edit / rm / reparent (add/ls/show also have top-level shortcuts)",
        description="Node CRUD primitives — the metric-style entity group. `wl add` / `wl ls` / `wl show` are the high-frequency shortcuts onto the same handlers; `node edit` / `node rm` / `node reparent` are the field-edit / soft-delete / move primitives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcuts (the high-frequency verbs also have a top-level shortcut — same handler):
  add  →  wl add        (= wl node add)
  ls   →  wl ls         (= wl node ls)
  show →  wl show       (= wl node show)
  edit / rm / reparent have no shortcut — call them under `node`.

Common examples:
  wl node add "task" -k task -p B     # = wl add (the top-level shortcut)
  wl node edit 42 --title "new" -p A  # edit a node's own fields (not status/parent/tags)
  wl node reparent 42 103             # move #42 under #103 (real parent_id); 'none' detaches
  wl node rm 42                       # soft-delete #42 + subtree (reversible tombstone)

Differences from related commands:
  - node edit       title/priority/kind/body/scheduled/deadline; status → done/cancel/…,
                    parent → node reparent, tags → wl tag
  - node rm         soft-delete (reversible); wl apply '- #id' is the diff-format equivalent
  - the metric group (wl metric add/ls/edit/rm) is the template this mirrors""")
    _ndsub = nd.add_subparsers(dest="node_sub")
    _args_node_add(_ndsub.add_parser("add", help="create a node (= wl add)",
        description="Create a node — the canonical primitive. Also: the top-level shortcut `wl add` (identical, same handler)."))
    _args_node_ls(_ndsub.add_parser("ls", parents=[filters], help="list nodes (= wl ls)",
        description="List nodes. Also: the top-level shortcut `wl ls` (identical, same handler)."))
    _args_node_show(_ndsub.add_parser("show", help="show a node + timeline (= wl show)",
        description="Show a node's detail + timeline. Also: the top-level shortcut `wl show` (identical, same handler)."))
    _nde = _ndsub.add_parser("edit", help="edit a node's own fields (title/priority/kind/body/scheduled/deadline)")
    _nde.add_argument("id", type=int)
    _nde.add_argument("--title")
    _nde.add_argument("-p", "--priority", choices=["A", "B", "C"])
    _nde.add_argument("-k", "--kind")
    _nde.add_argument("--body")
    _nde.add_argument("--scheduled", help="scheduled_date pin (YYYY-MM-DD / YYYY-MM / someday / …); pass '' to clear")
    _nde.add_argument("--deadline", help="deadline date YYYY-MM-DD; pass '' to clear")
    _ndr = _ndsub.add_parser("rm", help="soft-delete node(s) + their spoke rows (reversible tombstone, WL#501)")
    _ndr.add_argument("ids", type=int, nargs="+", metavar="id")
    _ndrp = _ndsub.add_parser("reparent", help="move a node under a new parent (changes the real parent_id, not a prop)")
    _ndrp.add_argument("id", type=int)
    _ndrp.add_argument("parent", help="new parent node id, or 'none'/'root' to detach to the top level")

    ls = sub.add_parser("ls", parents=[filters],
                        help="list nodes (default limit 20; see shell ls -t / -S / -r-style dimensions)",
                        description="List nodes (multi-dimensional, shell-ls style). Canonical form: `wl node ls` (this is the shortcut; see `wl node -h`).",
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
    _args_node_ls(ls)

    tr = sub.add_parser("tree", parents=[filters],
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

    ag = sub.add_parser("agenda", parents=[filters],
        help="cross-time-range scheduling overview: everything scheduled in [start, end]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Lists every node scheduled within the range, across all granularities (a task pinned
at @2026-06 month-level or @2026-W23 week-level shows up alongside exact days), ordered
by anchor date. Use it before planning to spot tasks already scheduled — a per-month
tree view misses month/week/someday-pinned items.

  wl agenda 2026-06-01 2026-06-30      # everything scheduled in June
  wl agenda today 2026-12-31 --someday # rest of year + the someday pile
  wl agenda 2026-06-01 2026-06-30 --all # include DONE/CANCELED too""")
    ag.add_argument("start", help="range start (YYYY-MM-DD / today / yesterday / 明天 ...)")
    ag.add_argument("end", help="range end (inclusive)")
    ag.add_argument("--someday", action="store_true", help="also list someday / fuzzy-scheduled nodes at the end")
    ag.add_argument("--all", action="store_true", help="include DONE/CANCELED (default hides terminal-status)")

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

    dy = sub.add_parser("day", parents=[filters],
        help="full view of a day (default today): bucket -> project/plan -> task -> log",
        description="Full view of one day: work/personal/other -> (planned/unplanned/project/priority) -> task -> indented logs. The header states the day's nature (workday / weekend, refined to holiday / leave / makeup by a `wl dateinfo` label). Top shows end-of-day summary + today's goal + Top5 (if set). Defaults to log-date-driven (works for past days too).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl day                              # today
  wl day 2026-05-30                   # historical day
  wl day yesterday                    # short form (yesterday / day-before-yesterday / tomorrow / day-after-tomorrow)
  wl day -t work                      # only work items (filter; -t/--tag, AND); -t personal for personal
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
  wl recap --date 2026-06-01 "..."    # write a past day's recap (back-fill)
  wl recap --date yesterday            # read yesterday's recap

wl day shows "Recap: ... (written at MM-DD HH:MM)" at the top;
if there are new logs after recap, wl day shows "⚠ N changes after recap, consider rewriting".
Using wl set <day_id> summary "..." directly does not stamp the timestamp; not recommended.""")
    rc.add_argument("text", nargs="?", help="no arg = read; with text = write")
    rc.add_argument("--date", help="target day (YYYY-MM-DD / today / yesterday / 昨天 ...); default today")

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
  wl unlog --node 39                  # delete the latest log for #39 today
  wl unlog --node 39 --date yesterday # latest log that day
  wl unlog --node 39 --all            # delete all logs for #39 that day

Find a log id: wl show <node_id> or wl logs --id <node_id> displays #L<id> in the timeline.
Edit a mistyped log with wl relog #L<id> instead. (Timing lives in the clock table, not logs — fix a clock with wl stop --at.)""")
    ul.add_argument("log_id", type=_log_id_arg, nargs="?",
                    help="log id (e.g. #L282 / L282 / 282; from wl show / wl logs timeline)")
    ul.add_argument("--node", type=int, help="delete by node id (default: latest log today)")
    ul.add_argument("--date", help="with --node: delete logs from that day (default today)")
    ul.add_argument("--all", action="store_true", help="with --node: delete all logs for that node that day")

    rl = sub.add_parser("relog",
        help="rewrite a log: new body / new time / editor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl relog #L282 "fixed content"     # change body
  wl relog #L282 -m "fixed content"  # -m mutually exclusive with positional
  wl relog #L282 --at 14:30          # only change time (keep date)
  wl relog #L282 --at 2026-05-30     # only change date (keep time)
  wl relog #L282                     # no body/--at -> open $EDITOR

Timing lives in the clock table, not logs — to fix a clock interval use wl stop --at.
Cannot move a log across nodes (that's unlog + log).""")
    rl.add_argument("log_id", type=_log_id_arg,
                    help="log id (#L282 / L282 / 282; from wl show / wl logs)")
    rl.add_argument("body", nargs="*", help="new body (positional; no arg -> -m / --at / EDITOR)")
    rl.add_argument("-m", "--message", help="new body (mutually exclusive with positional body; explicit)")
    rl.add_argument("--at", help="change time: HH:MM (keep date) / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")

    # ── metric: structured datapoints on a log (node → log → metric) ──
    mt = sub.add_parser("metric",
        help="structured datapoints (check-in / number / measurement): add/ls/edit/rm",
        description="CRUD for metrics — structured datapoints that hang off a log "
                    "(node → log → metric). A metric has a `tag` (what it is: glucose / "
                    "pullups / checkin …), an optional numeric or text value + unit, a note, "
                    "and a timestamp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl metric add 42 glucose 5.4 --unit mmol/L     # a numeric datapoint on node #42
  wl metric add 42 pullups 8 --unit reps         # reps
  wl metric add 42 checkin                        # a pure marker (stored as value 1)
  wl metric add 42 weight 70 --at yesterday       # backfill a timestamp
  wl metric add 42 glucose 6.1 --on-log #L99      # attach to an existing log instead of a new carrier
  wl metric ls 42                                 # list node #42's metrics
  wl metric ls 42 --tag glucose --since 2026-06-01
  wl metric edit #M7 --value 5.6 --note "post-meal"
  wl metric rm #M7 #M8                            # delete one or more

A metric must hang off a log; without --on-log a (possibly empty-body) carrier log is created.""")
    _msub = mt.add_subparsers(dest="metric_sub")

    _ma = _msub.add_parser("add", help="add a datapoint to a node")
    _ma.add_argument("node", type=int, help="node id the datapoint belongs to")
    _ma.add_argument("tag", help="what this datapoint is (glucose / pullups / checkin / …)")
    _ma.add_argument("value", nargs="?", help="value (numeric → value_num, else text); omit for a pure marker (=1)")
    _ma.add_argument("--text", action="store_true", help="treat value as text even if it looks numeric")
    _ma.add_argument("--unit", help="unit of a numeric value (mmol/L, kg, reps, …)")
    _ma.add_argument("--note", help="short inline note for this datapoint")
    _ma.add_argument("--at", help="datapoint time: YYYY-MM-DD / 'YYYY-MM-DD HH:MM' / today / yesterday (default: now)")
    _ma.add_argument("--on-log", dest="on_log", type=_log_id_arg,
                     help="attach to an existing log (#L<id>) instead of creating a carrier log")
    _ma.add_argument("--body", help="carrier log body (default empty); ignored with --on-log")

    _ml = _msub.add_parser("ls", help="list a node's metrics (default: this week; --all for everything)",
                           parents=[window])
    _ml.add_argument("node", type=int, help="node id")
    _ml.add_argument("--tag", help="filter by tag")
    _ml.add_argument("--all", action="store_true", help="all datapoints (ignore the default this-week window)")

    _me = _msub.add_parser("edit", help="edit a metric's fields")
    _me.add_argument("metric_id", type=_metric_id_arg, help="metric id (#M7 / M7 / 7; from wl metric ls)")
    _me.add_argument("--value", help="new value, autodetected numeric vs text (mutually exclusive with --num/--text)")
    _me.add_argument("--num", type=float, help="set numeric value (clears text value)")
    _me.add_argument("--text", help="set text value (clears numeric value)")
    _me.add_argument("--unit", help="set unit ('' clears)")
    _me.add_argument("--note", help="set note ('' clears)")
    _me.add_argument("--tag", help="change tag")
    _me.add_argument("--at", help="change timestamp")

    _mr = _msub.add_parser("rm", help="delete one or more metrics")
    _mr.add_argument("metric_ids", type=_metric_id_arg, nargs="+", help="metric id(s) (#M7 / M7 / 7)")

    ci = sub.add_parser("checkin",
        help="interactive check-in of today's habits (default multi-select arrows / space / Enter)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl checkin                          # default multi-select (arrows / space / Enter)
  wl checkin --per-item               # fallback: prompt y/n/note/q per item (allows per-item note)
  wl checkin --all-kinds              # not just habit; include all task/meetlog/... scheduled today

End-of-day: run wl checkin once to review every habit that's due today.
For single habit check-in, use wl tick <id>.""")
    ci.add_argument("--kind", help="filter by kind (default: habit; use --all-kinds to see anything scheduled)")
    ci.add_argument("--all-kinds", action="store_true",
                    help="no kind filter: habit/task/meetlog all listed (including everything scheduled today)")
    ci.add_argument("--per-item", action="store_true",
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

    lg = sub.add_parser("logs", parents=[window, filters],
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
        epilog="Switch theme: top-level --theme {auto,dark,light,mono} flag, or export WORKLOG_THEME=...; auto probes terminal background and picks dark/light.")

    pc = sub.add_parser("print-completion",
        help="dump shell completion script (argparse -> fish/bash/zsh; init-load model)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Usage (write once to your shell rc, then new shells auto-load; stays in sync with code changes):
  # fish: add to ~/.config/fish/config.fish
  wl print-completion fish | source

  # bash: add to ~/.bashrc
  eval "$(wl print-completion bash)"

  # zsh: add to ~/.zshrc
  eval "$(wl print-completion zsh)"

Same pattern as starship/direnv/zoxide.

User aliases: add [aliases] section to ~/.config/worklog/aliases.ini (e.g. d = day / c = checkin / ...); new shells pick them up (uniform across shells).""")
    pc.add_argument("shell", choices=["fish", "bash", "zsh"], help="target shell")

    return p


from . import commands
from .commands import (
    cmd_migrate,
    cmd_init,
    cmd_config,
    cmd_add,
    cmd_log,
    _ids_list,
    cmd_done,
    cmd_defer,
    cmd_start,
    cmd_stop,
    cmd_spent,
    cmd_link,
    cmd_unlink,
    cmd_set,
    cmd_tag,
    cmd_node_edit,
    cmd_node_rm,
    cmd_node_reparent,
    cmd_metric,
    _metric_id_arg,
    cmd_active,
    cmd_wait,
    cmd_reopen,
    cmd_cancel,
    cmd_show,
    _show_one,
    cmd_ls,
    cmd_projects,
    _tree_by,
    cmd_tree,
    cmd_focus,
    cmd_ancestors,
    cmd_descendants,
    cmd_agenda,
    _tree_children,
    _print_day_activity,
    _print_default_tree,
    _print_tree,
    _cn_weekday,
    _date_label,
    _sched_fires,
    _scheduled_node_ids,
    _sec_sort_key,
    _render_day_group,
    cmd_day,
    _ensure_today_day,
    cmd_goal,
    cmd_summary_prop,
    _checkin_collect,
    _is_interactive_tty,
    _multi_select_tty,
    _checkin_per_item,
    cmd_checkin,
    cmd_unlog,
    cmd_relog,
    _edit_in_editor,
    cmd_tick,
    _norm_rrule,
    cmd_sched,
    cmd_dateinfo,
    cmd_changes,
    _bulk_status_change,
    cmd_summary,
    _import_node,
    _import_update,
    cmd_import,
    _parse_node_line,
    _parse_fieldop,
    _parse_wld,
    _validate_fieldop,
    _exec_update,
    _fieldop_desc,
    cmd_apply,
    _apply_sub,
    cmd_find,
    cmd_logs,
    cmd_themes,
)

def cmd_node(args, con):
    """Dispatch `wl node <add|ls|show|edit|rm|reparent>` (the metric-style entity group;
    WL#486). The top-level add/ls/show route to the same handlers."""
    sub = getattr(args, "node_sub", None)
    if sub is None:
        sys.exit("✗ usage: wl node <add|ls|show|edit|rm|reparent> … (see `wl node --help`)")
    {"add": cmd_add, "ls": cmd_ls, "show": cmd_show,
     "edit": cmd_node_edit, "rm": cmd_node_rm, "reparent": cmd_node_reparent}[sub](args, con)


HANDLERS = {
    "config": cmd_config,
    "migrate": cmd_migrate,
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
    "unlink": cmd_unlink,
    "set": cmd_set,
    "tag": cmd_tag,
    "node": cmd_node,
    "metric": cmd_metric,
    "show": cmd_show,
    "ls": cmd_ls,
    "tree": cmd_tree,
    "projects": cmd_projects,
    "changes": cmd_changes,
    "summary": cmd_summary,
    "focus": cmd_focus,
    "ancestors": cmd_ancestors,
    "descendants": cmd_descendants,
    "agenda": cmd_agenda,
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


def _print_welcome():
    """Friendly banner shown when `wl` is run with no subcommand.
    Points users at the most common commands and `wl --help` for the full list."""
    print(f"wl {__version__} — SQLite-backed worklog")
    print("A todo.sh-style CLI for time hierarchy, projects, tasks, habits, meetlogs.")
    print()
    print("Getting started:")
    print('  wl init                          initialize the database')
    print('  wl add "task title" -k task      add a task')
    print('  wl log <id> "what happened"      append a log entry')
    print('  wl done <id>                     mark it done')
    print('  wl ls                            list open items')
    print('  wl tree                          full tree view')
    print('  wl day                           today\'s planned + activity')
    print()
    print("See `wl --help` for the full command list, or `wl <command> --help` for details.")


def main():  # pragma: no cover -- argparse entry; tests invoke HANDLERS[cmd] directly to bypass
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd is None:
        _print_welcome()
        return
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
    # config is read-only and side-effect free — don't create the DB just to print paths
    if args.cmd == "config":
        HANDLERS[args.cmd](args, None)
        return
    # --- per-invocation DB override (--db flag) ---
    # Re-evaluate DB_PATH with args so ensure_db / db_connect see the override.
    global DB_PATH
    DB_PATH = _resolve_db_path(args)
    # `wl migrate` is the explicit form of the auto-migration that ensure_db()
    # otherwise runs first. Calling ensure_db() here would apply the pending
    # migrations before the handler runs, leaving nothing to do — so for
    # `migrate` we just open the DB (creating the file if missing) and let
    # cmd_migrate decide what to apply.
    if args.cmd == "migrate":
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = db_connect()
        try:
            HANDLERS[args.cmd](args, con)
        finally:
            con.close()
        return
    ensure_db()
    con = db_connect()
    try:
        HANDLERS[args.cmd](args, con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
