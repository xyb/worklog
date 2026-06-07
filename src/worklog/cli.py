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
    _META_LOG_TYPES,
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
    cfg.optionxform = str  # preserve alias-name case (match `wl alias` writer)
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
    p.add_argument("title", help="the node's title, e.g. \"ship the Q3 report\" (quote if it has spaces)")
    p.add_argument("-k", "--kind", default="task",
                   help="what it is: task (default) / project / area / habit / meetlog / day")
    p.add_argument("-p", "--priority", choices=["A", "B", "C"],
                   help="priority: A = P0 (highest) / B = P1 / C = P2")
    p.add_argument("-t", "--tag", help="comma-separated tags, e.g. -t work or -t work,urgent (work/personal drive bucketing)")
    p.add_argument("--proj", help="project name (stored as a prop)")
    p.add_argument("--parent", type=int, help="parent node id (nest under a project/area), e.g. --parent 103")
    p.add_argument("--status", help="initial status (default TODO); rarely needed at creation")
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


def _args_prop_set(p):
    p.add_argument("id", type=int, help="node id")
    p.add_argument("key", help="prop key (or a meta field: goal/summary/overview/top5 → routes to meta)")
    p.add_argument("value", help="the value to store")
    return p


def _args_prop_rm(p):
    p.add_argument("id", type=int, help="node id")
    p.add_argument("key", help="prop key to remove (or a meta field → clears it)")
    return p


def _args_link(p):
    p.add_argument("ids", type=int, nargs="+", metavar="id", help="node id(s)")
    p.add_argument("vault_doc", help="vault doc name (no .md; an outer [[ ]] is stripped)")
    return p


def _log_id_arg(s):
    # accepts '#L282' / 'L282' / '282' (wl show / wl logs displays as #L<id>)
    t = s.lstrip("#")
    return int(t[1:] if t.lower().startswith("l") else t)


def _args_log_add(p):
    p.add_argument("id", type=int)
    p.add_argument("body")
    p.add_argument("-d", "--date", help="log date: YYYY-MM-DD / today / yesterday / day-before-yesterday / tomorrow / day-after-tomorrow (default: today; for backfilling history)")
    p.add_argument("--time", help="log time HH:MM or HH:MM:SS (with --date, or alone for today)")
    p.add_argument("--keep-status", action="store_true",
                   help="do not auto-promote TODO to DOING (default: logging implies 'working on it'; DONE etc. unchanged)")
    p.add_argument("--metric", action="append", metavar="'tag [value] [unit]'",
                   help="attach a structured datapoint to this log (repeatable): "
                        "--metric 'glucose 5.4 mmol/L' / --metric 'pullups 8' / --metric checkin")
    return p


def _args_relog(p):
    p.add_argument("log_id", type=_log_id_arg,
                   help="log id (#L282 / L282 / 282; from wl show / wl logs)")
    p.add_argument("body", nargs="*", help="new body (positional; no arg -> -m / --at / EDITOR)")
    p.add_argument("-m", "--message", help="new body (mutually exclusive with positional body; explicit)")
    p.add_argument("--at", help="change time: HH:MM (keep date) / YYYY-MM-DD / YYYY-MM-DD HH:MM[:SS]")
    return p


def _args_unlog(p):
    p.add_argument("log_id", type=_log_id_arg, nargs="?",
                   help="log id (e.g. #L282 / L282 / 282; from wl show / wl logs timeline)")
    p.add_argument("--node", type=int, help="delete by node id (default: latest log today)")
    p.add_argument("-d", "--date", help="with --node: delete logs from that day (default today)")
    p.add_argument("--all", action="store_true", help="with --node: delete all logs for that node that day")
    return p


# --- default-verb dispatch (WL#486) ---
# Some entity groups share a name with the old leaf command (link / sched / log / tag).
# To keep the legacy leaf form working (`wl link 42 doc`) while adding `wl link add/ls/rm`,
# we insert the group's *default verb* when the token after the entity isn't a known verb.
# entity -> (default_verb, {known sub-verbs})
_DEFAULT_VERB_ENTITIES = {
    "link": ("add", frozenset(("add", "ls", "rm"))),
    "tag": ("add", frozenset(("add", "ls", "rm"))),
    "log": ("add", frozenset(("add", "ls", "edit", "rm"))),
    "sched": ("add", frozenset(("add", "ls", "rm"))),
}
# global flags that consume the next token as their value (skip it when locating the subcommand)
_GLOBAL_VALUE_FLAGS = frozenset(("--db", "--color", "--theme", "--log-format"))


def _expand_default_verb(argv):
    """Insert an entity group's default verb so the legacy leaf form keeps working:
    `wl link 42 doc` → `wl link add 42 doc`, while `wl link ls 42` / `wl link -h` are left
    alone (the next token is a known verb / a flag). Scans past leading global flags to
    find the subcommand. The leaf's first positional is always an int id, never a verb
    word, so the verb-vs-leaf test is unambiguous."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        # tok is at the subcommand position
        if tok in _DEFAULT_VERB_ENTITIES:
            default, verbs = _DEFAULT_VERB_ENTITIES[tok]
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not nxt.startswith("-") and nxt not in verbs:
                argv.insert(i + 1, default)
        return argv
    return argv


class _WlParser(argparse.ArgumentParser):
    """ArgumentParser that applies the default-verb expansion before parsing, so both
    `main()` (sys.argv) and the tests (which call parse_args directly) get it."""

    def parse_known_args(self, args=None, namespace=None):
        if args is None:
            args = sys.argv[1:]
        return super().parse_known_args(_expand_default_verb(list(args)), namespace)


def build_parser():
    global _USER_ALIASES
    if _USER_ALIASES is None:
        _USER_ALIASES = _load_user_aliases()
    user_aliases = _USER_ALIASES

    p = _WlParser(
        prog="wl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "worklog (wl) — a fast, local, SQLite-backed worklog & planner.\n"
            "Track tasks/projects, log progress, plan your day, and review — all from the shell.\n\n"
            "New here? Try:  wl init  →  wl add \"my first task\" -k task  →  wl log 1 \"made progress\"  →  wl day\n"
            "Run `wl <command> -h` for any command's options + examples. "
            "Commands are grouped by purpose at the bottom of this help."
        ),
        epilog="""\
Concepts (the data model):
  node      everything is one node in a single tree; its `kind` is task · project · area
            (PARA, below) · habit (recurring) · meetlog (meeting note) · day/week/month/… (time)
  log       a timestamped progress entry on a node (each append is kept = history)
  tag       labels on a node; work / personal drive the `wl day` buckets
  prop      a static key=value attribute (owner, linear-id, …) — single value, overwritten
  meta      history-preserving fields: goal / summary / overview / top5 (a separate store)
  metric    a structured datapoint (a number or a check-in) attached to a log
  link      a pointer to an Obsidian vault doc
  sched     schedules a node to a day → it shows up "planned" in `wl day`
  clock     a time interval (`wl start`/`stop`/`spent`); `wl day` / `active` total it
  status    a node's state, shown as a marker: [ ] todo · [/] doing · [x] done ·
            [>] later/deferred · [?] wait (blocked) · [-] canceled
  priority  [#A] = P0 (highest) · [#B] = P1 · [#C] = P2 — shown by the id; sorts lists by default

Commands by purpose (run `wl <command> -h` for options + examples):
  track work    add · log · done · defer · cancel · reopen · wait · tick
  time          start · stop · spent · active · clock
  see it        day · tree · ls · show · logs · find · projects · agenda · summary · changes · focus
  organize      tag · link · sched · set · prop · meta · node · relog · unlog
  plan/reflect  goal · recap · checkin · metric · dateinfo
  bulk & setup  import · apply · init · config · alias · themes · print-completion

Good to know:
  • Organize PARA-style: areas (ongoing responsibilities, no end) ▸ projects (outcomes with
    a finish line) ▸ tasks — create with `-k area/project/task` and nest with `--parent <id>`.
  • Ids: a node is 42 or #42 (most write-commands take several at once); a log is #L42 and a
    metric is #M7 (as shown by `wl show` / `wl logs`) — use those forms with relog/unlog/metric.
  • Dates accept today / yesterday / tomorrow / YYYY-MM-DD (and fuzzy next-week / 2026-Q3 for defer/sched).
  • A task you `wl sched` to a day shows up "planned" in `wl day`; logging auto-moves TODO → DOING.
  • Tab-completion: `wl print-completion fish|bash|zsh` (the command prints setup instructions).
  • Shortcuts: `wl add` = `wl node add`, `wl set` = prop/meta by key; make your own with `wl alias add d day`.
  • `-q` brief output · `--db PATH` use a different worklog file.
  • Deeper docs: `wl help` is an info-style topic browser — `wl help para` (organizing),
    `wl help planning` (goals/summaries), `wl help status`, `wl help <command>`, …""",
    )
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

    _real_sub = p.add_subparsers(dest="cmd", required=False, metavar="<command>")

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
            # auto-link --help to the wl help topic when one exists (DESIGN §25 slimming
            # policy): write a topic doc and the command's --help gains the pointer for free.
            # Skipped if the epilog already names `wl help <name>` (a hand-written richer one).
            if topic_exists(name) and f"wl help {name}" not in (pp.epilog or ""):
                pp.epilog = ((pp.epilog + "\n\n") if pp.epilog else "") + f"More: `wl help {name}`"
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
  # Simplest — a task (kind defaults to task)
  wl add "ship the Q3 report"

  # With priority, tag, and scheduled for today
  wl add "review the PR" -p B -t work --sched today

  # A project, then a task nested under it (use the project's id as --parent)
  wl add "Website revamp" -k project -p A -t work
  wl add "draft the homepage copy" -p B --parent 42

  # Retrospective entry — create + log + mark done + timestamp, in one shot
  wl add "fixed the login bug" -p B \\
    --log "root cause: stale token (PR#42)" --done --at 14:30

  # A meeting note for today
  wl add "[meetlog] 09:30 tech sync" -k meetlog -p A -t work,meeting --parent <day_id>

Organizing (PARA): nest with --parent to build area ▸ project ▸ task —
  - area     an ongoing responsibility with no end (e.g. "Health", "Infra"); top-level
  - project  an outcome with a finish line (e.g. "Website revamp"); lives under an area
  - task     a concrete action; lives under a project (or area)
  (habit / meetlog / day are other kinds; the year→…→day time skeleton is auto-built.)

Differences from related commands:
  - wl add ... --log + --done       one-shot create + log + close. Same as add -> log -> done in three steps.
  - wl tick <id>                    add a check-in log to an existing habit/task, does not create a new one
  - wl log <id>                     add a log to an existing task, does not create a new one

More: `wl help add` (fuller intro) · `wl help para` (areas / projects / tasks).""")
    _args_node_add(a)

    # log entity group (WL#486): add / ls / edit / rm. `add` is the default verb so the
    # everyday `wl log <id> "body"` keeps working; `edit` = wl relog, `rm` = wl unlog
    # (both keep their top-level shortcuts). The rich cross-cutting view stays at `wl logs`.
    g = sub.add_parser("log",
        help="log CRUD: add / ls / edit / rm — `wl log 42 \"body\"` adds (default verb); edit=relog, rm=unlog",
        description="Log-entry CRUD on a node (progress / event stream) — the metric-style entity group. `wl log <id> \"body\"` is the add shortcut (the default verb; auto-progresses TODO->DOING unless --keep-status). `wl relog` = `log edit`, `wl unlog` = `log rm`. The full filterable stream view is `wl logs`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcuts / default verb (same handler):
  wl log 42 "body"      add a log (the default verb — = wl log add 42 "body")
  wl relog #L282 "..."  rewrite a log (= wl log edit #L282 "...")
  wl unlog #L282        delete a log (= wl log rm #L282)
  wl log ls 42          list a node's logs (full view: wl logs --id 42)

Common examples:
  wl log 42 "result: PR#13 merged"               # current progress (add, default verb)
  wl log 42 "..." --date yesterday --time 14:30  # backfill a precise timestamp
  wl log ls 42                                   # node-scoped log list
  wl log edit #L282 "fixed"                      # rewrite (= wl relog)
  wl log rm #L282                                # delete (= wl unlog)

Differences from related commands:
  - wl tick <id> --note "..."   habit check-in, default body = "✓ done"
  - wl add ... --log "..."      create a new task + insert a log in one step
  - wl logs --id 42             full filterable / windowed log view (presets, --since, --by-task)""")
    _lgsub = g.add_subparsers(dest="log_sub")
    _args_log_add(_lgsub.add_parser("add",
        help="add a log entry (= the default `wl log 42 \"body\"`)",
        description="Add a log entry to a node (auto TODO->DOING unless --keep-status; --date/--time backfill history). Also reachable as the default `wl log <id> \"body\"` (omit `add`; see `wl log -h`)."))
    _lgsub.add_parser("ls", help="list a node's logs (full view: wl logs --id <id>)").add_argument("id", type=int)
    _args_relog(_lgsub.add_parser("edit",
        help="rewrite a log's body / time (= wl relog)",
        description="Rewrite an existing log's body or timestamp. Also: the top-level shortcut `wl relog`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
  wl log edit #L282 "fixed content"   # change body
  wl log edit #L282 --at 14:30        # only change time
  wl log edit #L282                   # no body/--at -> open $EDITOR"""))
    _args_unlog(_lgsub.add_parser("rm",
        help="delete a log entry (= wl unlog)",
        description="Delete a log entry (soft-delete). Also: the top-level shortcut `wl unlog`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
  wl log rm #L282                     # exact delete by log id
  wl log rm --node 39                 # delete the latest log for #39 today
  wl log rm --node 39 --all           # delete all of #39's logs that day"""))

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
    df.add_argument("id", type=int, help="node id to defer")
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
    wa.add_argument("-n", "--note", help="add a log explaining what you're waiting on")

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

    # link entity group (WL#486): add / ls / rm with a default verb of `add`, so the
    # legacy `wl link 42 doc` still works (the parser expands it to `wl link add 42 doc`).
    ln = sub.add_parser("link",
        help="link CRUD: add / ls / rm — `wl link 42 doc` adds (default verb), `wl unlink` = rm",
        description="Vault-doc link CRUD — the metric-style entity group. `wl link <id…> <doc>` is the add shortcut (the default verb); `wl unlink` is the rm shortcut.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcuts / default verb (same handler):
  wl link 42 "doc"      add (the default verb — = wl link add 42 "doc")
  wl unlink 42 "doc"    remove (= wl link rm 42 "doc")
  wl link ls 42         list a node's links (no shortcut)

Common examples:
  wl link 42 "Project hub doc"          # link (add — the default verb)
  wl link 42 43 "shared topic"          # link multiple ids at once
  wl link ls 42                         # list #42's links
  wl link rm 42 "old doc"               # remove (= wl unlink)

Design: the knowledge layer (vault) and execution layer (wl) stay decoupled; wl only knows the linked doc name and does not sync content back.""")
    _lnsub = ln.add_subparsers(dest="link_sub")
    _args_link(_lnsub.add_parser("add", help="link a node to a vault doc (= the default `wl link 42 doc`)"))
    _lnsub.add_parser("ls", help="list a node's vault-doc links").add_argument("id", type=int)
    _args_link(_lnsub.add_parser("rm", help="remove a vault-doc link (= wl unlink)"))

    ul = sub.add_parser("unlink",
        help="remove one vault-doc link from a node (= wl link rm)",
        description="Remove one vault-doc link from a node (symmetric with link add). Canonical form: `wl link rm` (this is the shortcut; see `wl link -h`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
  wl unlink 42 "Project hub doc"        # remove that one link from #42
  wl unlink 42 43 "shared topic"        # from multiple ids at once

Removes a single link; the rest of the node's links are untouched (unlike clearing
them all). No-op with a notice if that link wasn't present.""")
    _args_link(ul)

    se = sub.add_parser("set",
        help="set a value on a node — key-routed shortcut: a prop (= wl prop set) or a meta field (= wl meta set)",
        description="Set a value on a node — a key-routed shortcut. A meta key (goal/summary/overview/top5) routes to `wl meta set` (history-preserving typed log); any other key routes to `wl prop set` (static single-value UDA prop). So `wl set` is to `prop set` / `meta set` what `wl add` is to `node add`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Props (static single-value — = wl prop set):
  wl set 42 owner xyb                  # add owner to a task
  wl set 42 linear ABC-449             # backfill Linear ID

Meta fields (history-preserving typed log — = wl meta set; see `wl meta -h`):
  wl set <day_id> goal "deliver X"     # (or wl goal — auto-targets today + stamps time)
  wl set <day_id> summary "..."        # (or wl recap — auto-targets today)
  wl set <week_id> overview "..."      # week overview
  wl set <month_id> top5 "..."         # monthly Top5

`wl recap`/`wl goal` target today's day node automatically; `wl set` needs the explicit node id.""")
    _args_prop_set(se)

    # prop entity group (WL#486 / #527): set / ls / rm. `set` → wl set shortcut; `rm` → wl unset.
    pr = sub.add_parser("prop",
        help="prop (UDA) CRUD: set / ls / rm (set has the top-level shortcut `wl set`; rm = `wl unset`)",
        description="Custom key=value prop (UDA) CRUD — the metric-style entity group. Meta fields (goal/summary/overview/top5) and real tags are NOT props (use wl goal/recap and wl tag).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcuts (same handler):
  set →  wl set         (= wl prop set)
  rm  →  wl unset       (= wl prop rm)
  ls has no shortcut — call it under `prop` (props also show inline in `wl show`).""")
    _prsub = pr.add_subparsers(dest="prop_sub")
    _args_prop_set(_prsub.add_parser("set", help="set/update a prop (= wl set)",
        description="Set/update a UDA prop. Also: the top-level shortcut `wl set` (identical, same handler)."))
    _prsub.add_parser("ls", help="list a node's props").add_argument("id", type=int)
    _args_prop_rm(_prsub.add_parser("rm", help="remove a prop (= wl unset)",
        description="Remove a UDA prop (soft-delete the row). Also: the top-level shortcut `wl unset`."))

    us = sub.add_parser("unset",
        help="remove a value from a node — key-routed: a prop (= wl prop rm) or a meta field (= wl meta rm)",
        description="Remove a value from a node — the delete counterpart of `wl set`, key-routed the same way: a meta key (goal/summary/overview/top5) clears that meta field (= `wl meta rm`); any other key removes a UDA prop (= `wl prop rm`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl unset 42 owner                   # remove the 'owner' prop (= wl prop rm)
  wl unset <day_id> goal              # clear the goal meta field (= wl meta rm; see `wl meta -h`)

Differences from related commands:
  - wl unset 42 owner   delete a UDA prop (= wl prop rm)
  - wl set 42 owner x   set/overwrite it (= wl prop set)""")
    _args_prop_rm(us)

    # clock entity group (WL#486 / #528): ls / edit / rm. Create stays start/stop/spent.
    ck = sub.add_parser("clock",
        help="clock-interval CRUD: ls / edit / rm (create with start / stop / spent)",
        description="Time-tracking interval CRUD — the metric-style entity group. Intervals are CREATED by the composite helpers `wl start` / `wl stop` / `wl spent`; this group lists, edits and removes them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl clock ls 42                      # list #42's time intervals
  wl clock edit 7 --start 09:00 --end 10:30   # fix a mistimed interval (recomputes duration)
  wl clock rm 7                       # remove a wrong interval

Create intervals with the composite helpers:
  - wl start 42 / wl stop 42          live timing
  - wl spent 42 90m                   record a past duration""")
    _cksub = ck.add_subparsers(dest="clock_sub")
    _cksub.add_parser("ls", help="list a node's clock intervals").add_argument("id", type=int)
    _cke = _cksub.add_parser("edit", help="edit an interval's start/end (recomputes duration)")
    _cke.add_argument("clock_id", type=int, metavar="clock_id")
    _cke.add_argument("--start", help="new start (HH:MM / YYYY-MM-DD [HH:MM[:SS]])")
    _cke.add_argument("--end", help="new end (same formats); '' sets it back to running")
    _ckr = _cksub.add_parser("rm", help="remove clock interval(s)")
    _ckr.add_argument("clock_ids", type=int, nargs="+", metavar="clock_id")

    # tag entity group (WL#486): add / ls / rm. `add` is the default verb so the everyday
    # `wl tag <id> +x -y` keeps working (full +add / -remove / bare-add / empty-list grammar);
    # `ls` lists, `rm` removes. Update is atomic (add/remove), so there is no `edit` verb.
    tg = sub.add_parser("tag",
        help="tag CRUD: add / ls / rm — `wl tag 42 +work -planned` adds/removes (default verb add)",
        description="Real-tag CRUD on a node (the tag table; drives work/personal bucketing & grouping) — the metric-style entity group. `wl tag <id> …` is the add shortcut (the default verb) and keeps the full +add / -remove / bare-add / empty-list grammar. Distinct from `wl set <id> tags …`, which would create a shadow 'tags' prop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcuts / default verb (same handler):
  wl tag 42 +work -planned   add & remove in one call (the default verb — = wl tag add 42 +work -planned)
  wl tag 42                  list current tags (= wl tag ls 42)
  wl tag ls 42               list a node's tags
  wl tag rm 42 planned       remove (= wl tag 42 -planned)

Common examples:
  wl tag 42 +work +P0        # add tags (default verb)
  wl tag 42 work             # bare word = add (same as +work)
  wl tag 42 +work -other     # add and remove in one call
  wl tag ls 42               # list current tags
  wl tag rm 42 other         # remove

Edits the real tag field (the tag table), unlike `wl set <id> tags ...` which would
just create a shadow 'tags' prop.""")
    _tgsub = tg.add_subparsers(dest="tag_sub")
    _tga = _tgsub.add_parser("add",
        help="add (and, with -tag, remove) tags (= the default `wl tag 42 +work`)",
        description="Add tags to a node. Power form: +tag adds, -tag removes, a bare word adds, no ops lists. Also reachable as the default `wl tag <id> …` (omit `add`; see `wl tag -h`).")
    _tga.add_argument("id", type=int)
    _tga.add_argument("ops", nargs=argparse.REMAINDER,
                      help="+tag adds, -tag removes, bare word adds; empty = list current tags")
    _tgsub.add_parser("ls", help="list a node's real tags (= bare `wl tag <id>`)").add_argument("id", type=int)
    _tgr = _tgsub.add_parser("rm", help="remove tag(s) from a node (= `wl tag <id> -tag`)")
    _tgr.add_argument("id", type=int)
    _tgr.add_argument("tags", nargs="+", metavar="tag",
                      help="tag name(s) to remove (plain name; to use a - prefix use `wl tag <id> -tag`)")

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
    tr.add_argument("--proj", help="filter to a project by name (prop match)")
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
    fo.add_argument("id", type=int, help="node id to focus on")
    fo.add_argument("--depth", type=int, help="max downstream depth")
    fo.add_argument("--related", action="store_true", help="also show tag-related nodes")

    an = sub.add_parser("ancestors",
        help="upstream path: ancestor chain from root to the node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: wl ancestors 42 -> Lifetime / Area / Project / Task. Inverse: wl descendants for the downstream subtree.")
    an.add_argument("id", type=int, help="node id")

    de = sub.add_parser("descendants",
        help="downstream subtree: all descendants of a node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: wl descendants 7 --depth 2 -> two levels of children under #7. wl tree --root 7 is equivalent but rendered as a tree.")
    de.add_argument("id", type=int, help="node id")
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

Time levels — a *suggested* rhythm (entirely optional; nothing here is enforced):
  The skeleton lifetime ▸ year ▸ quarter ▸ month ▸ week ▸ day builds itself as you log / sched,
  so you never create those nodes by hand. If you want a planning cadence, a common one is:
    day      `wl goal "..."` today's target · `wl recap "..."` end-of-day summary · tasks via `wl sched <id> today`
    week     `wl meta set <week_id> overview "..."`   this week's focus / P0-P1
    month    `wl meta set <month_id> top5 "..."`      the month's Top 5
    quarter  `wl meta set <quarter_id> goal "..."`    the quarter's objective (if you plan that far)
    year     same idea — a yearly direction, optional
  Find a level's node id with `wl tree` (the timeline is shown by default). Tasks/projects can
  hang at any level; use whichever levels fit how you actually plan — skip the rest.

Differences from related commands:
  - wl day        single-day overview (plan + actual + status mix, including not-done items)
  - wl active     tasks running right now (live focus, no history)
  - wl logs --date YYYY-MM-DD    flat log stream for that day (no task structure)
  - wl tree --root <day_id>       subtree of that day node (uses tree structure)

End-of-day workflow: wl day -> review the day -> wl recap "..." to write the summary.

More: `wl help day` · `wl help planning` (the time-level cadence).""")
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
        help="read/write today's goal — what you aim to deliver today (history-preserving)",
        description="Read or write today's goal — a short statement of what you aim to deliver today. Stored as the day node's `goal` meta field (a history-preserving typed log: each write appends, the latest is current); auto-creates today's day node. `wl day` shows it at the top.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl goal "ship the Q3 report draft"     # write today's goal
  wl goal                                # read (no text)

Planning rhythm (all optional, all history-preserving meta fields):
  morning   `wl goal "..."`                       today's intended deliverable
  evening   `wl recap "..."`                       what actually happened (the counterpart)
  weekly    `wl meta set <week_id> overview "..."` this week's focus / P0-P1
  monthly   `wl meta set <month_id> top5 "..."`    the month's Top 5
  Plan the *work* itself with `wl sched <id> <day>` (a task shows "planned" in `wl day`)
  or `wl defer <id> someday` (a loose backlog item). See `wl day -h` for the level cadence.""")
    g.add_argument("text", nargs="?", help="no arg = read today's goal; with text = write it")

    rc = sub.add_parser("recap",
        help="read/write a day's end-of-day summary — what actually happened (history-preserving)",
        description="Read or write a day's end-of-day summary — a short reflection on what actually happened. Stored as the day node's `summary` meta field (a history-preserving typed log); the write time is recorded so `wl day` can warn if you log more after recapping. The evening counterpart to `wl goal`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl recap "shipped the draft; blocked on review"   # write today's summary
  wl recap                                           # read
  wl recap --date 2026-06-01 "..."                  # write a past day's recap (back-fill)
  wl recap --date yesterday                          # read yesterday's recap

`wl day` shows "Recap: ... (written at MM-DD HH:MM)" at the top; if you log more after
recapping it warns "⚠ N changes after recap, consider rewriting". (`wl goal` is the morning
counterpart; weekly/monthly summaries are `wl meta set <week> overview` / `<month> top5`.)""")
    rc.add_argument("text", nargs="?", help="no arg = read; with text = write the summary")
    rc.add_argument("-d", "--date", help="target day (YYYY-MM-DD / today / yesterday / 昨天 ...); default today")

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
    tk.add_argument("-n", "--note", help="custom log body (default '✓ done')")
    tk.add_argument("--done", action="store_true", help="also mark DONE")

    ul = sub.add_parser("unlog",
        help="delete a log entry: #L<id> exact / --node delete latest that day (undo tick)",
        description="Delete a log entry (soft-delete). Canonical form: `wl log rm` (this is the shortcut; see `wl log -h`).",
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
    _args_unlog(ul)

    rl = sub.add_parser("relog",
        help="rewrite a log: new body / new time / editor",
        description="Rewrite an existing log's body or timestamp. Canonical form: `wl log edit` (this is the shortcut; see `wl log -h`).",
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
    _args_relog(rl)

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
    _ma.add_argument("-n", "--note", help="short inline note for this datapoint")
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
    _me.add_argument("-n", "--note", help="set note ('' clears)")
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

    # sched entity group (WL#486): add / ls / rm. `add` is the default verb so the everyday
    # `wl sched <id> <when>` / `wl sched <id> --clear` keep working (full when / --recur /
    # --clear / list-when-empty grammar via cmd_sched); `ls` lists, `rm` clears. `wl defer`
    # (status=LATER + rough hint) stays its own composite command.
    sc = sub.add_parser("sched",
        help="sched CRUD: add / ls / rm — `wl sched 42 2026-06-15` schedules (default verb add); drives wl day 'planned'",
        description="Forward-planning CRUD — schedule a task to a day / recurring rule (drives wl day 'planned') — the metric-style entity group. `wl sched <id> <when>` is the add shortcut (the default verb) and keeps the full when / --recur / --clear / list-when-empty grammar. Distinct from `wl defer` (status=LATER + rough hint).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcuts / default verb (same handler):
  wl sched 42 2026-06-15     schedule to a day (the default verb — = wl sched add 42 2026-06-15)
  wl sched 42                list this task's schedule (= wl sched ls 42)
  wl sched 42 --clear        clear all entries (= wl sched rm 42)

Schedule to a specific day:
  wl sched 42 2026-06-15              # exact date (add, the default verb)
  wl sched 42 tomorrow                # short form (today / yesterday / tomorrow / day-after-tomorrow)

Recurring rules (--recur); each supports -1 = last day of the cycle:
  wl sched 42 --recur daily                       # every day
  wl sched 42 --recur weekly:Mon,Wed,Fri          # also numeric weekly:1,3,5 / -1=Sun
  wl sched 42 --recur monthly:5,15,-1             # day 5/15/last each month
  wl sched 42 --recur quarterly:1-15              # 15th of the first month in each quarter
  wl sched 42 --recur quarterly:-1                # last day of each quarter (3/31, 6/30, ...)
  wl sched 42 --recur yearly:03-21                # March 21 every year
  wl sched 42 --recur yearly:-1                   # last day of year (12-31)

  wl sched ls 42             # list this task's schedule
  wl sched rm 42             # clear all entries (= wl sched 42 --clear)

Difference from wl defer: sched writes to the sched table (precise; appears as "planned" in wl day); defer = status=LATER + rough hint.
Create + schedule in one line: wl add "..." --sched today""")
    _scsub = sc.add_subparsers(dest="sched_sub")
    _sca = _scsub.add_parser("add",
        help="schedule to a day / recurring rule (= the default `wl sched 42 <when>`)",
        description="Schedule a task to a one-off day or a recurring rule (--recur); no when/--recur lists; --clear clears. Also reachable as the default `wl sched <id> <when>` (omit `add`; see `wl sched -h`).")
    _sca.add_argument("id", type=int)
    _sca.add_argument("when", nargs="?", help="YYYY-MM-DD / today / yesterday / tomorrow / day-after-tomorrow (one-off date)")
    _sca.add_argument("--recur",
                      help="recurring rule (all support -1 = last day): daily / weekly:Mon|1-7|-1 / monthly:5|-1 / quarterly:M-D|-1 / yearly:MM-DD|-1")
    _sca.add_argument("--clear", action="store_true", help="clear all schedule entries for this task")
    _scsub.add_parser("ls", help="list a node's schedule entries (= bare `wl sched <id>`)").add_argument("id", type=int)
    _scsub.add_parser("rm", help="clear a node's schedule entries (= `wl sched <id> --clear`)").add_argument("id", type=int)

    di = sub.add_parser("dateinfo",
        help="date metadata (holiday/vacation/working-day swap; shown in wl day header)",
        description="Date metadata (holiday / vacation / working-day-swap label; shown in the wl day header) — the polymorphic everyday shortcut over the date_meta table. The explicit metric-style form is the `wl date set / ls / rm / import` group (see `wl date -h`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Common examples:
  wl dateinfo 2026-05-01 "Labor Day"        # single entry  (= wl date set)
  wl dateinfo 2026-05-03 "swap working day" # working day swap
  wl dateinfo --import holidays.json        # batch {"YYYY-MM-DD":"label"}  (= wl date import)
  wl dateinfo 2026-05-01 --clear            # clear  (= wl date rm)
  wl dateinfo                               # list all  (= wl date ls)

wl day shows "<date> <weekday> · <label>" at the top. Weekday comes from the date; dateinfo only stores the extra label.
Explicit verbs: wl date set/ls/rm/import (same date_meta table; see `wl date -h`).""")
    di.add_argument("date", nargs="?", help="YYYY-MM-DD")
    di.add_argument("label", nargs="?", help="label, e.g. Labor Day / swap working day / vacation")
    di.add_argument("--import", dest="import_file", metavar="FILE", help='batch import {"YYYY-MM-DD":"label"} JSON, - reads stdin')
    di.add_argument("--clear", action="store_true", help="clear the label for this date")

    # date entity group (WL#486): set / ls / rm / import. A clean group — `date` doesn't
    # collide with any leaf, so no default verb. `wl dateinfo` is the polymorphic shortcut.
    dt = sub.add_parser("date",
        help="date-metadata CRUD: set / ls / rm / import (polymorphic shortcut: `wl dateinfo`)",
        description="Date-metadata CRUD (holiday / vacation / working-day-swap label, shown in the wl day header) — the metric-style entity group. The everyday polymorphic shortcut is `wl dateinfo` (set when a label is given, list when not, --clear / --import variants).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcut (same date_meta table):
  wl dateinfo …            polymorphic everyday form (= these verbs auto-dispatched)

Common examples:
  wl date set 2026-05-01 "Labor Day"     # set/update a label (= wl dateinfo 2026-05-01 "...")
  wl date ls                             # list all (= bare wl dateinfo)
  wl date ls 2026-05-01                  # show one date
  wl date rm 2026-05-01                  # clear (= wl dateinfo 2026-05-01 --clear)
  wl date import holidays.json           # batch {"YYYY-MM-DD":"label"} (= wl dateinfo --import)""")
    _dtsub = dt.add_subparsers(dest="date_sub")
    _dtset = _dtsub.add_parser("set", help="set/update a date's label (= wl dateinfo <date> <label>)")
    _dtset.add_argument("date", help="YYYY-MM-DD")
    _dtset.add_argument("label", help="label, e.g. Labor Day / swap working day / vacation")
    _dtls = _dtsub.add_parser("ls", help="list date metadata, or show one date (= bare wl dateinfo)")
    _dtls.add_argument("date", nargs="?", help="YYYY-MM-DD (omit to list all)")
    _dtsub.add_parser("rm", help="clear a date's label (= wl dateinfo <date> --clear)").add_argument("date", help="YYYY-MM-DD")
    _dtsub.add_parser("import", help='batch import {"YYYY-MM-DD":"label"} JSON (= wl dateinfo --import)').add_argument("file", help="JSON file path, or - for stdin")

    # meta entity group (WL#486): set / ls / rm for the history-preserving typed-log meta
    # fields (goal/summary/overview/top5). Distinct from props (prop = static single-value).
    me = sub.add_parser("meta",
        help="meta-field CRUD: set / ls / rm — history-preserving typed logs (goal/summary/overview/top5)",
        description="Meta-field CRUD — the metric-style entity group for the four history-preserving typed-log fields (goal/summary/overview/top5; each edit appends, latest = current). Distinct from props (prop = static single-value, overwrite). Shortcuts onto this group: `wl set <node> <field>` (= meta set), `wl goal` / `wl recap` (today's goal/summary).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shortcuts (same typed-log store):
  wl set <node> goal "..."     = wl meta set <node> goal "..."  (key-routed; props go to prop set)
  wl unset <node> goal         = wl meta rm <node> goal
  wl goal "..."  / wl recap "..."   today's day-node goal / summary (auto-target today)

Common examples:
  wl meta set 9 overview "this week: ship X"   # week node's overview
  wl meta set 12 top5 "1. … 2. …"              # month node's Top5
  wl meta ls 3                                  # show #3's current meta fields
  wl meta rm 3 goal                             # clear a meta field (reversible)""")
    _mesub = me.add_subparsers(dest="meta_sub")
    _mset = _mesub.add_parser("set", help="set/append a meta field (= the `wl set <node> <field>` shortcut)")
    _mset.add_argument("id", type=int)
    _mset.add_argument("field", choices=list(_META_LOG_TYPES))
    _mset.add_argument("value")
    _mesub.add_parser("ls", help="list a node's current meta fields").add_argument("id", type=int)
    _mrm = _mesub.add_parser("rm", help="clear a meta field (= wl unset <node> <field>)")
    _mrm.add_argument("id", type=int)
    _mrm.add_argument("field", choices=list(_META_LOG_TYPES))

    # alias command: manage ~/.config/worklog/aliases.ini (wired into the parser at startup)
    al = sub.add_parser("alias",
        help="manage command aliases: add / ls / rm (e.g. `wl alias add d day` → `wl d`)",
        description="Manage command aliases stored in ~/.config/worklog/aliases.ini. An alias maps a short name to a wl command (`wl alias add d day` makes `wl d` == `wl day`). Aliases are wired into the parser at startup, so a change takes effect on the NEXT wl invocation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
  wl alias add d day        # wl d == wl day (next run)
  wl alias add c checkin    # wl c == wl checkin
  wl alias ls               # list configured aliases
  wl alias rm d             # remove an alias

The target must be a real wl command, and an alias can't shadow an existing command.""")
    _alsub = al.add_subparsers(dest="alias_sub")
    _aadd = _alsub.add_parser("add", help="add/update an alias (name → command)")
    _aadd.add_argument("name", help="the short alias to type")
    _aadd.add_argument("target", help="the wl command it expands to")
    _alsub.add_parser("ls", help="list configured aliases")
    _alsub.add_parser("rm", help="remove an alias").add_argument("name", help="alias name to remove")

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
    fd.add_argument("query", help="text to search for (matches title/body/log/tag/prop/link)")
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
    lg.add_argument("--id", type=int, help="only this node's logs")
    lg.add_argument("-d", "--date", help="YYYY-MM-DD / today / yesterday / day-before-yesterday (only this day)")
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

    hp = sub.add_parser("help",
        help="info-style topic browser: `wl help` lists topics, `wl help <topic>` reads one",
        description="Browse the bundled help topics — fuller explanations of commands, concepts, parameters, and workflows than `<command> -h` gives, with 'See also' links. `wl help` shows the index; `wl help <topic>` reads one topic.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  wl help                 # the index + all topics by category
  wl help para            # how to organize (areas / projects / tasks)
  wl help planning        # goals / summaries / scheduling rhythm
  wl help status          # what the [ ] / [/] / [x] markers mean

Topics live as Markdown docs in the repo (i18n-ready); `wl <command> -h` stays the quick
per-command reference, `wl help <topic>` is the fuller teaching layer.""")
    hp.add_argument("topic", nargs="?", help="topic name (omit to list all; e.g. node / add / planning)")
    hp.add_argument("--lang", help="help language (default: $WORKLOG_LANG / $LANG, falling back to en)")

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
    cmd_tag_group,
    cmd_log_group,
    cmd_node_edit,
    cmd_node_rm,
    cmd_node_reparent,
    cmd_prop,
    cmd_prop_rm,
    cmd_clock,
    cmd_link_group,
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
    cmd_sched_group,
    cmd_dateinfo,
    cmd_date_group,
    cmd_meta,
    cmd_alias,
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
    cmd_help,
    topic_exists,
    topic_names,
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
    "log": cmd_log_group,
    "done": cmd_done,
    "defer": cmd_defer,
    "start": cmd_start,
    "stop": cmd_stop,
    "spent": cmd_spent,
    "active": cmd_active,
    "wait": cmd_wait,
    "reopen": cmd_reopen,
    "cancel": cmd_cancel,
    "link": cmd_link_group,
    "unlink": cmd_unlink,
    "set": cmd_set,
    "unset": cmd_prop_rm,
    "tag": cmd_tag_group,
    "node": cmd_node,
    "prop": cmd_prop,
    "clock": cmd_clock,
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
    "sched": cmd_sched_group,
    "dateinfo": cmd_dateinfo,
    "date": cmd_date_group,
    "meta": cmd_meta,
    "alias": cmd_alias,
    "import": cmd_import,
    "apply": cmd_apply,
    "find": cmd_find,
    "logs": cmd_logs,
    "themes": cmd_themes,
    "help": cmd_help,
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
    print("Learn more:")
    print("  wl --help                        full command list + concepts overview")
    print("  wl <command> --help              one command's options + examples")
    print("  wl help                          guided topics (concepts / workflows); e.g. wl help para")


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
    # config / help are read-only and side-effect free — don't create the DB just to
    # print paths or render a help topic (a newcomer may run `wl help` before `wl init`).
    if args.cmd in ("config", "help"):
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
