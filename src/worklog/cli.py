#!/usr/bin/env python3
"""worklog (wl): SQLite-backed worklog tool, todo.sh-style CLI.

Usage examples:
  wl init                                  # init DB
  wl add "research X" -p A -t work,P0 --proj dev_tooling
  wl add "Dev tooling" --para project --parent 12
  wl ls                                    # list open items
  wl ls --para project --tag work
  wl tree                                  # full tree
  wl tree --para project
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
    _RESERVED_LOG_TAGS,
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
    _set_width_cap,
    _resolve_width_cap,
    _set_title_mode,
    _resolve_title_mode,
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
    _sched_level,
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
from . import node_types as _node_types


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


def _user_alias_map():
    """Read ~/.config/worklog/aliases.ini → {alias_name: target_str}. The target may carry
    arguments — `w = day -t work` — which are spliced onto argv at the subcommand position
    before parsing (the git-alias model). Returns {} on failure / missing file.
    Format:
        [aliases]
        d = day
        w = day -t work
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
        alias, target = alias.strip(), target.strip()
        if alias and target:
            out[alias] = target
    return out


def _resolve_alias_tokens(name, amap, _depth=0):
    """Resolve an alias name to its expansion token list (`w` → ['day','-t','work']), following
    a chain if the target's own first token is another alias (`ww = w`). Cycle-/depth-guarded
    (cap 8). Returns None if `name` isn't an alias."""
    if name not in amap or _depth > 8:
        return None
    import shlex
    try:
        toks = shlex.split(amap[name])
    except ValueError:
        return None
    if toks and toks[0] != name and toks[0] in amap:
        deeper = _resolve_alias_tokens(toks[0], amap, _depth + 1)
        if deeper:
            toks = deeper + toks[1:]
    return toks


def _load_user_aliases(amap=None):
    """{resolved_first_token: [alias1, ...]} — feeds argparse's `aliases=` (so `wl <alias> -h`
    and completion know the alias) and is keyed by the target command, e.g. `w = day -t work`
    registers `w` under `day`. The actual argument splice is done by `_expand_user_alias`."""
    if amap is None:
        amap = _user_alias_map()
    out = {}
    for name in amap:
        toks = _resolve_alias_tokens(name, amap)
        if toks:
            out.setdefault(toks[0], []).append(name)
    return out


def _expand_user_alias(argv, amap=None):
    """Splice a user alias at the subcommand position into its (possibly multi-token) target:
    `wl w --since X` with `w = day -t work` → `wl day -t work --since X`. Scans past leading
    global flags (same rule as `_expand_default_verb`). Only the subcommand token is considered,
    so an alias name appearing as an argument (`wl find w`) is left untouched."""
    global _USER_ALIAS_MAP
    if amap is None:
        if _USER_ALIAS_MAP is None:
            _USER_ALIAS_MAP = _user_alias_map()
        amap = _USER_ALIAS_MAP
    if not amap:
        return argv
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        toks = _resolve_alias_tokens(tok, amap)
        if toks is not None:
            argv[i:i + 1] = toks
        return argv
    return argv


_USER_ALIASES = None     # lazy cache: {first_token: [alias, ...]} for argparse registration
_USER_ALIAS_MAP = None   # lazy cache: {alias: target_str} for the argv splice


# --- shared argument sets: a node operation is reachable both as a top-level
# shortcut (`wl add`) and under the entity group (`wl node add`); both call the same
# arg-adder so the two forms stay identical and there's one definition to maintain.
def _args_node_add(p):
    p.add_argument("title", help="the node's title, e.g. \"ship the Q3 report\" (quote if it has spaces)")
    p.add_argument("--para", choices=["area", "project", "task"],
                   help="responsibility-line role — writes type.para (the same flag as `wl ls --para`). "
                        "A bare add (no --para) is a loose task with no role; for a time node / soft "
                        "type / custom type use --prop (e.g. --prop type.date=day, --prop type.habit).")
    p.add_argument("-p", "--priority", choices=["A", "B", "C"],
                   help="priority: A = P0 (highest) / B = P1 / C = P2")
    p.add_argument("-t", "--tag", help="comma-separated tags, e.g. -t work or -t work,urgent (work/personal drive bucketing)")
    p.add_argument("--proj", help="project name (stored as a prop)")
    p.add_argument("--prop", action="append", metavar="K=V|K", default=None,
                   help="set a prop at creation (repeatable), e.g. --prop type.meetlog=dating; "
                        "bare K (no =) sets an existence prop")
    p.add_argument("--parent", type=int, help="parent node id (nest under a project/area), e.g. --parent 103")
    p.add_argument("--status", help="initial status (default TODO); rarely needed at creation")
    p.add_argument("--scheduled", help="(rough hint, writes node.scheduled_date) scheduled time: YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / next-week / next-month / next-quarter")
    p.add_argument("--sched", help="(precise, writes the sched table = visible as planned in wl day for that date) date: YYYY-MM-DD / today / yesterday / tomorrow / a signed delta +1 / -2d / +3w / -1y")
    p.add_argument("--deadline", help="deadline date YYYY-MM-DD")
    p.add_argument("--body", help="optional body text")
    p.add_argument("--log", "-m", help="insert a log entry right after creation (result / output / numbers)")
    p.add_argument("--done", action="store_true", help="mark DONE + write closed_at immediately after creation (retrospective task in one shot)")
    p.add_argument("--at", help="timestamp for --log + (if --done) closed_at (HH:MM / YYYY-MM-DD [HH:MM[:SS]])")
    p.add_argument("--link", help="also attach a vault doc (no .md suffix, same semantics as wl link)")
    p.add_argument("--relation", action="append", metavar="'<type> <id>…'",
                   help="relate the new node to existing one(s), writing both sides (repeatable); "
                        "type = split-from / split-into / related: --relation 'split-from 42' / --relation 'related 42 43'")
    p.add_argument("--metric", action="append", metavar="'tag [value] [unit]'",
                   help="attach a structured datapoint (repeatable); reuses the --log carrier or makes one: "
                        "--metric 'glucose 5.4 mmol/L' / --metric checkin")
    return p


def _args_node_ls(p):
    p.add_argument("--parent", type=int, help="only direct children of this node")
    p.add_argument("--root", type=int, help="all descendants of this node (recursive subtree, flat) — vs --parent (direct children only)")
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
                   help="list specific ids directly, skipping filters (like shell ls file1 file2)")
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
    p.add_argument("key", help="prop key (or goal/summary → routes to wl goal, history-preserving)")
    p.add_argument("value", help="the value to store")
    return p


def _args_prop_rm(p):
    p.add_argument("id", type=int, help="node id")
    p.add_argument("key", help="prop key to remove (or goal/summary → clears it)")
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
    p.add_argument("-d", "--date", help="log date: YYYY-MM-DD / today / yesterday / tomorrow / a signed delta +1 / -2d / +3w (default: today; for backfilling history)")
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


# --- default-verb dispatch ---
# Some entity groups share a name with the old leaf command (link / sched / log / tag).
# To keep the legacy leaf form working (`wl link 42 doc`) while adding `wl link add/ls/rm`,
# we insert the group's *default verb* when the token after the entity isn't a known verb.
# entity -> (default_verb, {known sub-verbs})
_DEFAULT_VERB_ENTITIES = {
    "link": ("add", frozenset(("add", "ls", "rm"))),
    "tag": ("add", frozenset(("add", "ls", "rm"))),
    "log": ("add", frozenset(("add", "ls", "edit", "rm", "show"))),
    "sched": ("add", frozenset(("add", "ls", "rm"))),
    "agent": ("set", frozenset(("set", "ls", "rm", "context"))),
    # goal's default form writes/reads TODAY's goal (text, not an int id); set/ls/rm reach any node
    "goal": ("today", frozenset(("today", "set", "ls", "rm"))),
}
# global flags that consume the next token as their value (skip it when locating the subcommand)
_GLOBAL_VALUE_FLAGS = frozenset(("--db", "--color", "--theme", "--log-format", "--width", "--title", "-o", "--output"))

# commands without a same-named help topic → the topic that covers them (a family guide or
# a sibling command's topic), so every command's --help can auto-link into `wl help` (§25).
_HELP_FAMILY = {
    "start": "time", "stop": "time", "spent": "time", "active": "time", "wait": "time",
    "tick": "tracking",
    "set": "prop", "unset": "prop", "unlink": "link", "relog": "log", "unlog": "log", "retag": "log",
    "cancel": "done", "reopen": "done", "ancestors": "focus", "descendants": "focus",
    "date": "dateinfo",
    "themes": "admin", "init": "admin", "config": "admin", "migrate": "admin",
    "print-completion": "admin",
    "tags": "tag", "props": "prop", "metrics": "metric",   # the cross-node "list all" lists
}


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


class _WlHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Cap the wrap width on wide terminals so `--help` stays readable, and wrap the raw
    epilog/description with a hanging indent (preserving its hand-aligned two-column layout).
    Option/choice help is still wrapped by argparse itself (via `_split_lines`) — we never
    touch that. The width cap (render.help_width) is shared so everything lines up."""

    def __init__(self, prog):
        super().__init__(prog)
        from .render import help_width
        self._width = help_width()
        # recompute the help column against the (possibly capped) width, mirroring argparse
        self._max_help_position = min(self._max_help_position,
                                      max(self._width - 20, self._indent_increment * 2))

    def _fill_text(self, text, width, indent):
        # argparse calls this ONLY for the description + epilog (option/choice help goes through
        # _split_lines, left to argparse). RawDescription keeps our layout but never wraps;
        # instead wrap here with a hanging indent (DESIGN §25 / commands.help.wrap_help_text).
        from .commands.help import wrap_help_text
        wrapped = wrap_help_text(text, width)
        if indent:
            wrapped = "\n".join(indent + line for line in wrapped.split("\n"))
        return wrapped


class _WlParser(argparse.ArgumentParser):
    """ArgumentParser that applies the default-verb expansion before parsing, so both
    `main()` (sys.argv) and the tests (which call parse_args directly) get it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import re as _re
        # Treat any `-<digit>…` token as a negative-number-LIKE positional, so a signed date delta
        # (`-2`, `-2d`, `-2week`) is consumed by a date positional instead of mis-read as an unknown
        # option. Python 3.14's argparse already uses this loose `-\.?\d` matcher; 3.11–3.13 use a
        # strict `^-\d+$` that rejects `-2week`. We unify on the loose form (wl has no
        # negative-number options, so nothing legitimate is mis-detected). Subparsers are _WlParser
        # too (parser_class defaults to type(self)), so they inherit this.
        self._negative_number_matcher = _re.compile(r"-\.?\d")

    def parse_known_args(self, args=None, namespace=None):
        if args is None:
            args = sys.argv[1:]
        args = list(args)
        # User-alias splice only at the top level (prog "wl"): an alias name reaching a subparser
        # as an argument (`wl find w`) must not be expanded. Default-verb runs at every level.
        if self.prog == "wl":
            args = _expand_user_alias(args)
        return super().parse_known_args(_expand_default_verb(args), namespace)

    def format_help(self):
        # colorize `wl -h` / `wl <cmd> -h` to match the wl help 3-tier scheme (DESIGN §25).
        # Post-processes the fully-aligned text, so the zero-width ANSI doesn't shift columns;
        # a no-op when color is off (non-TTY / --color never / $NO_COLOR / mono).
        return colorize_help(super().format_help())


def build_parser():
    global _USER_ALIASES, _USER_ALIAS_MAP
    if _USER_ALIASES is None:
        _USER_ALIAS_MAP = _user_alias_map()
        _USER_ALIASES = _load_user_aliases(_USER_ALIAS_MAP)
    user_aliases = _USER_ALIASES

    p = _WlParser(
        prog="wl",
        formatter_class=_WlHelpFormatter,
        description=(
            "worklog (wl) — a fast, local, SQLite-backed worklog & planner.\n"
            "Track tasks/projects, log progress, plan your day, and review — all from the shell.\n\n"
            "New here? Try:  wl init  →  wl add \"my first task\"  →  wl log 1 \"made progress\"  →  wl day\n"
            "Run `wl <command> -h` for any command's options + examples. "
            "Commands are grouped by purpose at the bottom of this help."
        ),
        epilog="""\
Concepts (the data model):
  `node`     everything is one node in a single tree; its `type` is task · project · area (PARA, below) · habit (recurring) · meetlog (meeting note) · day/week/month/… (time)
  `log`      a timestamped progress entry on a node (each append is kept = history)
  `tag`      labels on a node; work / personal drive the `wl day` buckets
  `prop`     a static key=value attribute (owner, linear-id, …) — single value, overwritten
  `goal`     a node's goal / summary — history-preserving reserved-tag logs (`wl goal`)
  `metric`   a structured datapoint (a number or a check-in) attached to a log
  `link`     a pointer to an Obsidian vault doc
  `sched`    schedules a node to a day → it shows up "planned" in `wl day`
  `clock`    a time interval (`wl start`/`stop`/`spent`); `wl day` / `active` total it
  `status`   a node's state, shown as a marker: [ ] todo · [/] doing · [x] done · [>] later/deferred · [?] wait (blocked) · [-] canceled
  `priority` [#A] = P0 (highest) · [#B] = P1 · [#C] = P2 — shown by the id; sorts lists by default

Commands by purpose (run `wl <command> -h` for options + examples):
  track work    `add` · `log` · `done` · `defer` · `cancel` · `reopen` · `wait` · `tick`
  time          `start` · `stop` · `spent` · `active` · `clock`
  see it        `day` · `tree` · `ls` · `show` · `logs` · `find` · `projects` · `agenda` · `summary` · `changes` · `focus`
  organize      `tag` · `link` · `sched` · `set` · `prop` · `meta` · `node` · `relog` · `unlog`
  plan/reflect  `goal` · `recap` · `checkin` · `metric` · `dateinfo`
  bulk & setup  `import` · `apply` · `init` · `config` · `alias` · `themes` · `print-completion`

Good to know:
  • Organize PARA-style: areas (ongoing responsibilities, no end) ▸ projects (outcomes with
    a finish line) ▸ tasks — set the role with `--para area/project` (a bare task needs none) and
    nest with `--parent <id>`.
  • Ids: a node is 42 or #42 (most write-commands take several at once); a log is #L42 and a
    metric is #M7 (as shown by `wl show` / `wl logs`) — use those forms with relog/unlog/metric.
  • Dates accept today / yesterday / tomorrow / YYYY-MM-DD, signed deltas +1 / -2d / +3w / -1y
    (number + d/w/m/y, default days), and fuzzy next-week / 2026-Q3 (defer/sched).
  • A task you `wl sched` to a day shows up "planned" in `wl day`; logging auto-moves TODO → DOING.
  • Tab-completion: `wl print-completion fish|bash|zsh` (the command prints setup instructions).
  • Shortcuts: `wl add` = `wl node add`, `wl set` = prop/meta by key; make your own with `wl alias add w "day -t work"`.
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
                   help="color theme (default auto: probe terminal bg, pick dark/light; reads $WORKLOG_THEME; see wl themes)")
    p.add_argument("--width", default=None, metavar="{full,help,N}",
                   help="output width: full (fill terminal, default) / help (cap to the --help width, 100) / N columns; also reads $WORKLOG_WIDTH")
    p.add_argument("--title", choices=["wrap", "clip"], default=None,
                   help="long node title: wrap (multi-line, hang-indented under the title; default) / clip (one line, truncate with …); also reads $WORKLOG_TITLE")
    p.add_argument("-q", "--brief", action="store_true",
                   help="brief output: skip log body/timeline/detail in every command, token-saving for AI")
    p.add_argument("-o", "--output", choices=["text", "json"], default="text",
                   help="output format: text (default) or json (machine-readable); place before or after the verb — wl -o json ls / wl ls -o json. See: wl help output")
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
    # command takes the SAME --tag/--para/--status, with the same meaning — see
    # make_node_filter). --tag is comma-separated AND.
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("-t", "--tag", help="comma-separated tags, AND filter (e.g. -t work)")
    filters.add_argument("--para", choices=["area", "project", "task"],
                         help="filter by responsibility role (the type.para prop; same flag as `wl add --para`). "
                              "For non-PARA classifications filter the prop directly: "
                              "--prop type.meetlog / --prop type.habit / --prop type.date=day")
    filters.add_argument("--status", help="filter by status, comma = any-of (TODO/DOING/DONE/WAIT/LATER/CANCELED)")
    filters.add_argument("-p", "--priority", help="filter by priority, comma = any-of (A/B/C or P0/P1/P2)")
    filters.add_argument("--prop", action="append", metavar="K=V|K|GROUP.", default=None,
                         help="filter by prop: K=V (exact; matches a member of a comma-joined value) / K (key exists) / GROUP. or GROUP.* (namespace prefix). Repeat for AND, e.g. --prop github.repo=xyb/worklog --prop linear.id")

    # structured-output parent (reused by show/ls/logs so `-o json` means the same everywhere):
    # text = the rich rendering (default), json = machine-readable. A command gains `-o` only
    # when it serializes to json; others don't accept the flag (no silent text fallback).
    output_parent = argparse.ArgumentParser(add_help=False)
    output_parent.add_argument("-o", "--output", choices=["text", "json"], default=argparse.SUPPRESS,
                               help="output format: text (default) or json (machine-readable); place before or after the verb. See: wl help output")

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
            # auto-link --help to the wl help topic (DESIGN §25 slimming policy): prefer a
            # same-named topic, else the family topic this command belongs to (_HELP_FAMILY),
            # so every command's --help points somewhere useful. Skipped if the epilog already
            # names that `wl help <x>` (a hand-written richer pointer).
            tgt = name if topic_exists(name) else _HELP_FAMILY.get(name)
            if tgt and topic_exists(tgt) and f"wl help {tgt}" not in (pp.epilog or ""):
                pp.epilog = ((pp.epilog + "\n\n") if pp.epilog else "") + f"More: `wl help {tgt}`"
            return pp
        def __getattr__(self, k):
            return getattr(self._sub, k)
    sub = _SubWrapper(_real_sub)

    sub.add_parser("migrate",
        help="apply pending SQL migrations from migrations/NNNN_*.sql (auto-run on every command; this is the explicit form)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
The DB version is tracked via `PRAGMA user_version`. Every migration in
`migrations/` is named NNNN_*.sql (numeric prefix sorts the apply order);
files with number > current PRAGMA user_version run in order, each in its
own transaction, then user_version is bumped.

Migrations are auto-applied by `ensure_db()` on every command, so you
rarely need to invoke this explicitly. Use it to see what's pending or
to retry after a failed migration.

Before applying to an existing DB, the runner snapshots it to a same-dir
`<db>.pre-v<N>.bak` (N = the version before migrating), so a bad migration
is recoverable. A fresh init (no data yet) is not backed up.""")


    cfgp = sub.add_parser("config",
        help="print resolved configuration: DB path, aliases path, XDG dirs, env vars, embedding",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Shows where worklog reads from and how the runtime is configured (paths, env, embedding backend).
`wl config init` writes a commented config.ini template you can edit (won't overwrite an existing one).

Read-only by default — bare `wl config` does not create anything.""")
    _cfgsub = cfgp.add_subparsers(dest="config_sub")
    _cfgsub.add_parser("init", help="write a commented config.ini template (skips if it exists)",
        formatter_class=_WlHelpFormatter,
        epilog="Creates $XDG_CONFIG_HOME/worklog/config.ini from a template with the embedding "
               "defaults + [synonyms] example, all commented. Edit it, then `wl config` shows the "
               "resolved values. Won't overwrite an existing file.")

    sub.add_parser("init",
        help="initialize SQLite DB (default ~/.local/share/worklog/worklog.db; skips if it exists)",
        formatter_class=_WlHelpFormatter,
        epilog="""Run once on a fresh machine before using wl.

DB path resolution:
  1. --db PATH flag (per-invocation override)
  2. $WORKLOG_DB env var
  3. $XDG_DATA_HOME/worklog/worklog.db (default ~/.local/share/worklog/worklog.db)

Config (aliases.ini) lives at $XDG_CONFIG_HOME/worklog/aliases.ini (default ~/.config/worklog/aliases.ini).""")

    a = sub.add_parser("add",
        parents=[output_parent],
        help="create a new node (task/project/area/meetlog/habit/day...); compound flags let you do add + log + done + sched + link + relation in one shot",
        description="Create a new node (task/project/area/meetlog/habit/day/...). Compound flags support add + log + done + sched + link + relation in one step, replacing several separate commands. Canonical form: `wl node add` (this is the shortcut; see `wl node -h`).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl add "ship the Q3 report"                          # simplest — a task
  wl add "review the PR" -p B -t work --sched today    # priority, tag, plan for today
  wl add "Website revamp" --para project -p A -t work  # a project (→ e.g. #42); --para = the type.para role
  wl add "draft the homepage copy" --parent 42         # nest a task under it
  wl add "split out of the big task" --relation 'split-from 42'   # relate to an existing node at creation (both sides)
  wl add "fixed the login bug" -p B --log "root cause: …" --done --at 14:30   # create + log + close
  wl add "[meetlog] 09:30 tech sync" --prop type.meetlog --parent <day_id>    # a meeting note

More: `wl help add` (fuller intro + key options) · `wl help para` (areas / projects / tasks).""")
    _args_node_add(a)

    # log entity group: add / ls / edit / rm. `add` is the default verb so the
    # everyday `wl log <id> "body"` keeps working; `edit` = wl relog, `rm` = wl unlog
    # (both keep their top-level shortcuts). The rich cross-cutting view stays at `wl logs`.
    g = sub.add_parser("log",
        help="log CRUD: add / ls / edit / rm — wl log 42 \"body\" adds (default verb); edit=relog, rm=unlog",
        description="Log-entry CRUD on a node (progress / event stream) — the metric-style entity group. `wl log <id> \"body\"` is the add shortcut (the default verb; auto-progresses TODO->DOING unless --keep-status). `wl relog` = `log edit`, `wl unlog` = `log rm`. The full filterable stream view is `wl logs`.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl log 42 "result: PR#13 merged"               # progress now (add, the default verb)
  wl log 42 "..." --date yesterday --time 14:30  # backfill a precise timestamp
  wl log ls 42                                   # this node's logs (= wl logs --id 42)
  wl log edit #L282 "fixed"                      # rewrite (= wl relog); wl log rm = wl unlog

More: `wl help log` (vs `wl tick` habit check-in / `wl logs` filterable stream).""")
    _lgsub = g.add_subparsers(dest="log_sub")
    _args_log_add(_lgsub.add_parser("add", parents=[output_parent],
        help="add a log entry (= the default wl log 42 \"body\")",
        description="Add a log entry to a node (auto TODO->DOING unless --keep-status; --date/--time backfill history). Also reachable as the default `wl log <id> \"body\"` (omit `add`; see `wl log -h`)."))
    _lgls = _lgsub.add_parser("ls", parents=[output_parent], help="list a node's logs (full view: wl logs --id <id>)")
    _lgls.add_argument("id", type=int)
    _lgshow = _lgsub.add_parser("show",
        parents=[output_parent],
        help="show one log entry's full (untruncated) content by log id (#L282)",
        description="Print one log entry's complete, untruncated body by its log id (the list views truncate each log to one line). Accepts #L282 / L282 / 282.")
    _lgshow.add_argument("log_id", type=_log_id_arg, metavar="log_id", help="log id (#L282 / L282 / 282)")
    _args_relog(_lgsub.add_parser("edit", parents=[output_parent],
        help="rewrite a log's body / time (= wl relog)",
        description="Rewrite an existing log's body or timestamp. Also: the top-level shortcut `wl relog`.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
  wl log edit #L282 "fixed content"   # change body
  wl log edit #L282 --at 14:30        # only change time
  wl log edit #L282                   # no body/--at -> open $EDITOR"""))
    _args_unlog(_lgsub.add_parser("rm", parents=[output_parent],
        help="delete a log entry (= wl unlog)",
        description="Delete a log entry (soft-delete). Also: the top-level shortcut `wl unlog`.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
  wl log rm #L282                     # exact delete by log id
  wl log rm --node 39                 # delete the latest log for #39 today
  wl log rm --node 39 --all           # delete all of #39's logs that day"""))

    d = sub.add_parser("done",
        parents=[output_parent],
        help="mark node DONE + closed_at (multiple ids; --log/--at for one-shot log+done)",
        description="Mark node as DONE and write closed_at. Accepts multiple ids. --log/--at combines log + close + timestamp in one step (replaces wl log -> wl done two-step).",
        formatter_class=_WlHelpFormatter,
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
        parents=[output_parent],
        help="defer a task to a future point (LATER + scheduled_date; fuzzy times supported)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl defer 42 someday        # no committed time, just "later"
  wl defer 42 next-month     # fuzzy (also 2026-Q3 / a precise date)
  wl defer 42 2026-06-15     # still LATER, a soft hint — not "planned"

Distinct from `wl sched` (firm day, shows "planned"); defer is the someday/backlog pile.

More: `wl help defer`.""")
    df.add_argument("id", type=int, help="node id to defer")
    df.add_argument("date", help="scheduled time (precise or fuzzy): YYYY-MM-DD / YYYY-MM / YYYY-Www / YYYY-Qn / YYYY / someday / tomorrow / next-week / next-month / next-quarter")

    s = sub.add_parser("start",
        parents=[output_parent],
        help="clock-in to start timing (batch ids; --at to backfill past time)",
        formatter_class=_WlHelpFormatter,
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
        parents=[output_parent],
        help="clock-out to stop timing + compute elapsed (multiple ids; --at to backfill past end)",
        formatter_class=_WlHelpFormatter,
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
        parents=[output_parent],
        help="record a past time spent (build a clock interval from a duration, good for retrospective entries)",
        formatter_class=_WlHelpFormatter,
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
        parents=[output_parent],
        help="tasks running right now (open clock) + today's elapsed + latest log",
        description="List tasks that are timing right now (an open clock interval). Shows current session elapsed, today's total, and the most recent log. Good for live focus check and finding tasks you forgot to stop.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl active           # tasks timing now (session elapsed + today's total + latest log)
  wl active -q        # just id + elapsed

Close a forgotten timer with `wl stop <id>`.

More: `wl help active` (vs `wl day` full-day review).""")
    # ac has no other flags but we keep the variable for future args (e.g. --since to look at past activity)

    wa = sub.add_parser("wait",
        parents=[output_parent],
        help="mark WAIT (blocked on others / external input); auto-closes the clock; multiple ids",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl wait 42                            # mark WAIT (suspended)
  wl wait 42 --note "waiting on review" # add a log explaining what we're waiting on
  wl wait 42 43 --note "waiting on approval" # batch

Note: marking WAIT auto-closes any open clock (WAIT = suspended, no longer timing). Use wl reopen to revert to TODO.""")
    wa.add_argument("ids", type=int, nargs="+", help="node id(s)")
    wa.add_argument("-n", "--note", help="add a log explaining what you're waiting on")

    ro = sub.add_parser("reopen",
        parents=[output_parent],
        help="undo DONE/CANCELED/WAIT/LATER back to TODO + clear closed_at (multiple ids)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl reopen 42         # single id
  wl reopen 42 43      # batch

Inverse of wl done/cancel. Use when you change your mind and want to restart a task.""")
    ro.add_argument("ids", type=int, nargs="+", help="node id(s)")

    cx = sub.add_parser("cancel",
        parents=[output_parent],
        help="mark CANCELED + closed_at (drop / no-longer-doing; parallel to done); --log/--at supported",
        formatter_class=_WlHelpFormatter,
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

    # link entity group: add / ls / rm with a default verb of `add`, so the
    # legacy `wl link 42 doc` still works (the parser expands it to `wl link add 42 doc`).
    ln = sub.add_parser("link",
        help="link CRUD: add / ls / rm — wl link 42 doc adds (default verb), wl unlink = rm",
        description="Vault-doc link CRUD — the metric-style entity group. `wl link <id…> <doc>` is the add shortcut (the default verb); `wl unlink` is the rm shortcut.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl link 42 "Project hub doc"          # link (add — the default verb)
  wl link 42 43 "shared topic"          # link multiple ids at once
  wl link ls 42                         # list #42's links
  wl link rm 42 "old doc"               # remove (= wl unlink)

More: `wl help link`.""")
    _lnsub = ln.add_subparsers(dest="link_sub")
    _args_link(_lnsub.add_parser("add", parents=[output_parent], help="link a node to a vault doc (= the default wl link 42 doc)"))
    _lnls = _lnsub.add_parser("ls", parents=[output_parent], help="list a node's vault-doc links")
    _lnls.add_argument("id", type=int)
    _args_link(_lnsub.add_parser("rm", parents=[output_parent], help="remove a vault-doc link (= wl unlink)"))

    ul = sub.add_parser("unlink",
        parents=[output_parent],
        help="remove one vault-doc link from a node (= wl link rm)",
        description="Remove one vault-doc link from a node (symmetric with link add). Canonical form: `wl link rm` (this is the shortcut; see `wl link -h`).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
  wl unlink 42 "Project hub doc"        # remove that one link from #42
  wl unlink 42 43 "shared topic"        # from multiple ids at once

Removes a single link; the rest of the node's links are untouched (unlike clearing
them all). No-op with a notice if that link wasn't present.""")
    _args_link(ul)

    rel = sub.add_parser("relation",
        parents=[output_parent],
        help="task↔task relations: split-from / split-into / related (writes both sides)",
        description="Record or list relations between tasks — split-from / split-into / related — stored as relation.* props (comma-separated id lists). Distinct from ancestors (the parent/child hierarchy): relations express derivation / association across the tree. Adding writes BOTH sides (split-from on A also sets split-into on B; related is symmetric); the view also derives the reverse from other nodes, so it always reads bidirectionally.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl relation 42                        # list #42's relations
  wl relation 42 split-from 17          # #42 was split out of #17 (sets #17 split-into 42 too)
  wl relation 17 split-into 42 43       # #17 split into #42 and #43
  wl relation 42 related 7 9            # #42 relates to #7 and #9 (symmetric)
  wl relation 42 7 9                    # same — `related` is the default type
  wl relation 42 split-from 17 --rm     # remove that relation (both sides)

Types: split-from / split-into (inverses) · related (symmetric, the default).

More: `wl help relation`.""")
    rel.add_argument("id", type=int, help="the node whose relations to set / list")
    rel.add_argument("rtype", nargs="?",
        type=lambda s: s.replace("_", "-").lower(),   # accept split_from / SPLIT-FROM too
        help="relation type (split-from / split-into / related); omit the type word and `related` is assumed")
    rel.add_argument("others", nargs="*", metavar="other_id",
        help="the related node id(s)")
    rel.add_argument("--rm", action="store_true", help="remove the relation (from both sides)")

    se = sub.add_parser("set",
        parents=[output_parent],
        help="set a value on a node — key-routed shortcut: a prop (= wl prop set) or goal/summary (= wl goal set)",
        description="Set a value on a node — a key-routed shortcut. The key goal or summary routes to `wl goal set` (history-preserving reserved-tag log); any other key routes to `wl prop set` (static single-value UDA prop). So `wl set` is to `prop set` / `goal set` what `wl add` is to `node add`.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl set 42 owner xyb                  # a prop (static single-value; = wl prop set)
  wl set 42 linear ABC-449             # backfill a Linear id
  wl set <node_id> goal "deliver X"    # a goal log on any node (= wl goal set; `wl goal` for today)

Key-routed: goal/summary → wl goal (history-preserving); any other key → a prop.

More: `wl help set`.""")
    _args_prop_set(se)

    # prop entity group: set / ls / rm. `set` → wl set shortcut; `rm` → wl unset.
    pr = sub.add_parser("prop",
        help="prop (UDA) CRUD: set / ls / rm (set has the top-level shortcut wl set; rm = wl unset)",
        description="Custom key=value prop (UDA) CRUD — the metric-style entity group. goal/summary (reserved-tag logs) and real tags are NOT props (use wl goal/recap and wl tag).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Shortcuts: set → `wl set`, rm → `wl unset` (same handlers); `ls` has none (props also show
inline in `wl show`).

A key may be namespaced with a dot — `agent_session.claude`, `ext.linear` — to group related
single-value props under a shared prefix; each full key is still one value, but `key LIKE
'group.%'` finds the whole namespace (prop filters / stats honor the prefix). See `wl help prop`.

More: `wl help prop`.""")
    _prsub = pr.add_subparsers(dest="prop_sub")
    _args_prop_set(_prsub.add_parser("set", parents=[output_parent], help="set/update a prop (= wl set)",
        description="Set/update a UDA prop. Also: the top-level shortcut `wl set` (identical, same handler)."))
    _prls = _prsub.add_parser("ls", parents=[output_parent], help="list a node's props")
    _prls.add_argument("id", type=int)
    _args_prop_rm(_prsub.add_parser("rm", parents=[output_parent], help="remove a prop (= wl unset)",
        description="Remove a UDA prop (soft-delete the row). Also: the top-level shortcut `wl unset`."))

    ag = sub.add_parser("agent",
        parents=[output_parent],
        help="bind the current AI agent session to a task: wl agent <id> (set) / wl agent (show) / wl agent ls / wl agent rm",
        description="Bind the current AI agent session to a node so the agent knows which task it's on and the status line / hook context can surface it. Stored as an `agent_session.<agent>` prop on the node (agent = claude / cursor / codex / …, from $WL_AGENT or --agent, default claude) — no new table. `wl agent <id>` is the set shortcut (default verb).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl agent 42             # bind this session to task #42 (default verb: set) — records history
  wl agent 42 --no-record # bind without leaving a history mark (pointer only)
  wl agent 42 --agent codex   # record a non-default runtime (else $WL_AGENT, else claude)
  wl agent                # show what this session is bound to
  wl agent ls             # list all session→task bindings
  wl agent rm             # unbind this session

Two stores, two jobs:
  * the `agent_session.<agent>` prop is the LIVE pointer — one session → one node, it MOVES on
    rebind, so it always names the node a session is currently on;
  * a HISTORY trail (one log carrying TWO metrics — `agent_session` = the full session id, and
    `agent` = the runtime name) stays on the node forever. `wl agent <id>` writes it BY DEFAULT
    (so auto-binds capture lineage with no flag to remember); `--no-record` skips it. Recover it
    with `wl metric ls <id> --tag agent_session --all` (or read `wl show <id>`'s timeline).

Only the bind event is recorded — later logs don't each carry the session, so it costs one row
per binding, not per write; rebinding the same pair doesn't duplicate it.

Reads $WL_SESSION_ID / $CLAUDE_CODE_SESSION_ID for the session, $WL_AGENT for the runtime name.

More: `wl help agent`.""")
    _agsub = ag.add_subparsers(dest="agent_sub")
    _ags = _agsub.add_parser("set", help="bind current session to <id>")
    _ags.add_argument("id", type=int)
    _ags.add_argument("--record", action=argparse.BooleanOptionalAction, default=True,
        help="append a one-off history log carrying an `agent_session` metric (session id) + an `agent` metric (runtime name), so `wl metric ls <id> --tag agent_session --all` recovers every session it was worked under (default on; `--no-record` for a pointer-only bind)")
    _ags.add_argument("--agent", default=None, metavar="NAME",
        help="which agent runtime this is (claude / cursor / codex / …); recorded with the session so the history shows what worked the node. Default: $WL_AGENT, else 'claude'")
    _agls = _agsub.add_parser("ls", parents=[output_parent], help="list session→task bindings, most-recently-active first")
    _agls.add_argument("--all", action="store_true", help="show every binding (default elides older ones to avoid a screen-flood)")
    _agls.add_argument("--by", choices=["active", "bound"], default="active",
                       help="sort axis: active = node's latest log/update time (default); bound = session bind time")
    _agls.add_argument("-g", "--group", dest="group", action="store_true", default=True,
                       help="group into per-day sections by the --by axis (DEFAULT on)")
    _agls.add_argument("--flat", "--no-group", dest="group", action="store_false",
                       help="flat list, no per-day grouping")
    _agsub.add_parser("rm", help="unbind the current session")
    _agctx = _agsub.add_parser("context", help="machine line `<id>\\t<title>` of the current session's binding (for hooks; empty if unbound)")
    _agctx.add_argument("--hook", action="store_true",
        help="emit a ready-to-print Claude Code UserPromptSubmit JSON payload instead (so a hook needs no jq)")

    us = sub.add_parser("unset",
        parents=[output_parent],
        help="remove a value from a node — key-routed: a prop (= wl prop rm) or goal/summary (= wl goal rm)",
        description="Remove a value from a node — the delete counterpart of `wl set`, key-routed the same way: the key goal or summary clears that reserved-tag log (= `wl goal rm`); any other key removes a UDA prop (= `wl prop rm`).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl unset 42 owner                   # remove the 'owner' prop (= wl prop rm)
  wl unset <node_id> goal             # clear the goal log (= wl goal rm)

Key-routed like `wl set`: goal/summary → that reserved-tag log, any other key → a prop.""")
    _args_prop_rm(us)

    # clock entity group: ls / edit / rm. Create stays start/stop/spent.
    ck = sub.add_parser("clock",
        help="clock-interval CRUD: ls / edit / rm (create with start / stop / spent)",
        description="Time-tracking interval CRUD — the metric-style entity group. Intervals are CREATED by the composite helpers `wl start` / `wl stop` / `wl spent`; this group lists, edits and removes them.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl clock ls 42                      # list #42's time intervals
  wl clock edit 7 --start 09:00 --end 10:30   # fix a mistimed interval (recomputes duration)
  wl clock rm 7                       # remove a wrong interval

Create intervals with the composite helpers:
  - wl start 42 / wl stop 42          live timing
  - wl spent 42 90m                   record a past duration""")
    _cksub = ck.add_subparsers(dest="clock_sub")
    _ckls = _cksub.add_parser("ls", parents=[output_parent], help="list a node's clock intervals")
    _ckls.add_argument("id", type=int)
    _cke = _cksub.add_parser("edit", parents=[output_parent], help="edit an interval's start/end (recomputes duration)")
    _cke.add_argument("clock_id", type=int, metavar="clock_id")
    _cke.add_argument("--start", help="new start (HH:MM / YYYY-MM-DD [HH:MM[:SS]])")
    _cke.add_argument("--end", help="new end (same formats); '' sets it back to running")
    _ckr = _cksub.add_parser("rm", parents=[output_parent], help="remove clock interval(s)")
    _ckr.add_argument("clock_ids", type=int, nargs="+", metavar="clock_id")

    # tag entity group: add / ls / rm. `add` is the default verb so the everyday
    # `wl tag <id> +x -y` keeps working (full +add / -remove / bare-add / empty-list grammar);
    # `ls` lists, `rm` removes. Update is atomic (add/remove), so there is no `edit` verb.
    tg = sub.add_parser("tag",
        help="tag CRUD: add / ls / rm — wl tag 42 +work -planned adds/removes (default verb add)",
        description="Real-tag CRUD on a node (the tag table; drives work/personal bucketing & grouping) — the metric-style entity group. `wl tag <id> …` is the add shortcut (the default verb) and keeps the full +add / -remove / bare-add / empty-list grammar. Distinct from `wl set <id> tags …`, which would create a shadow 'tags' prop.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl tag 42 +work +P0        # add tags (default verb; bare word also adds)
  wl tag 42 +work -other     # add and remove in one call
  wl tag 42                  # list current tags (= wl tag ls 42)
  wl tag rm 42 other         # remove

Edits the real tag field, unlike `wl set <id> tags ...` (which would make a shadow prop).

More: `wl help tag`.""")
    _tgsub = tg.add_subparsers(dest="tag_sub")
    _tga = _tgsub.add_parser("add", parents=[output_parent],
        help="add (and, with -tag, remove) tags (= the default wl tag 42 +work)",
        description="Add tags to a node. Power form: +tag adds, -tag removes, a bare word adds, no ops lists. Also reachable as the default `wl tag <id> …` (omit `add`; see `wl tag -h`).")
    _tga.add_argument("id", type=int)
    _tga.add_argument("ops", nargs=argparse.REMAINDER,
                      help="+tag adds, -tag removes, bare word adds; empty = list current tags")
    _tgls = _tgsub.add_parser("ls", parents=[output_parent], help="list a node's real tags (= bare wl tag <id>)")
    _tgls.add_argument("id", type=int)
    _tgr = _tgsub.add_parser("rm", parents=[output_parent], help="remove tag(s) from a node (= wl tag <id> -tag)")
    _tgr.add_argument("id", type=int)
    _tgr.add_argument("tags", nargs="+", metavar="tag",
                      help="tag name(s) to remove (plain name; to use a - prefix use wl tag <id> -tag)")

    sh = sub.add_parser("show", parents=[output_parent],
        help="full detail + timeline for a node (accepts multiple ids)",
        description="All info on a node: metadata (status/priority/parents/tags/links/props) + timeline (created/scheduled/closed/log merged by time). Timeline defaults to the last 5; use --all-timelines for full expansion. `-o json` for the full machine-readable node. Canonical form: `wl node show` (this is the shortcut; see `wl node -h`).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl show 42                   # full detail + last 5 timeline entries
  wl show 42 -q                # brief: skip timeline
  wl show 42 --all-timelines   # full timeline (--timeline-tail N for a set length)
  wl show 42 -o json           # machine-readable node + relations (pipe to jq)

More: `wl help show` (vs `wl focus` up/down context / `wl logs --id` log stream only).""")
    _args_node_show(sh)

    # node entity group: the metric-style `wl node <verb>` primitive CRUD.
    # The top-level add/ls/show are the high-frequency shortcuts onto the same handlers;
    # edit/rm/reparent are the field-edit / soft-delete / move primitives.
    nd = sub.add_parser("node",
        help="node primitive CRUD: add / ls / show / edit / rm / reparent (add/ls/show also have top-level shortcuts)",
        description="Node CRUD primitives — the metric-style entity group. `wl add` / `wl ls` / `wl show` are the high-frequency shortcuts onto the same handlers; `node edit` / `node rm` / `node reparent` are the field-edit / soft-delete / move primitives.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Shortcuts: add → `wl add`, ls → `wl ls`, show → `wl show` (same handlers); edit / rm /
reparent have no top-level shortcut — call them under `node`.

Common examples:
  wl node edit 42 --title "new" -p A  # edit own fields (not status/parent/tags)
  wl node reparent 42 103             # move #42 under #103 ('none' detaches)
  wl node rm 42                       # soft-delete #42 + subtree (reversible)

More: `wl help node`.""")
    _ndsub = nd.add_subparsers(dest="node_sub")
    _args_node_add(_ndsub.add_parser("add", parents=[output_parent], help="create a node (= wl add)",
        description="Create a node — the canonical primitive. Also: the top-level shortcut `wl add` (identical, same handler)."))
    _args_node_ls(_ndsub.add_parser("ls", parents=[filters, output_parent], help="list nodes (= wl ls)",
        description="List nodes. Also: the top-level shortcut `wl ls` (identical, same handler)."))
    _args_node_show(_ndsub.add_parser("show", parents=[output_parent], help="show a node + timeline (= wl show)",
        description="Show a node's detail + timeline. Also: the top-level shortcut `wl show` (identical, same handler)."))
    _nde = _ndsub.add_parser("edit", parents=[output_parent], help="edit a node's own fields (title/priority/--para role/body/scheduled/deadline)")
    _nde.add_argument("id", type=int)
    _nde.add_argument("--title")
    _nde.add_argument("-p", "--priority", choices=["A", "B", "C"])
    _nde.add_argument("--para", choices=["area", "project", "task"],
                      help="set the responsibility-line role (writes type.para); to change other "
                           "classifications use `wl set type.<x>` / `wl prop rm`")
    _nde.add_argument("--body")
    _nde.add_argument("--scheduled", help="scheduled_date pin (YYYY-MM-DD / YYYY-MM / someday / …); pass '' to clear")
    _nde.add_argument("--deadline", help="deadline date YYYY-MM-DD; pass '' to clear")
    _ndr = _ndsub.add_parser("rm", parents=[output_parent], help="soft-delete node(s) + their spoke rows (reversible tombstone)")
    _ndr.add_argument("ids", type=int, nargs="+", metavar="id")
    _ndrp = _ndsub.add_parser("reparent", help="move a node under a new parent (changes the real parent_id, not a prop)")
    _ndrp.add_argument("id", type=int)
    _ndrp.add_argument("parent", help="new parent node id, or 'none'/'root' to detach to the top level")

    ls = sub.add_parser("ls", parents=[filters, output_parent],
                        help="list nodes (default limit 20; see shell ls -t / -S / -r-style dimensions)",
                        description="List nodes (multi-dimensional, shell-ls style). Canonical form: `wl node ls` (this is the shortcut; see `wl node -h`).",
                        formatter_class=_WlHelpFormatter,
                        epilog="""\
Common examples (shell-ls multi-dimensional):
  wl ls --parent 45                  direct children of #45 (one level, like ls dir/)
  wl ls --root 45                    whole subtree under #45 (all descendants, recursive)
  wl ls --para project               only projects · --tag work,dev (AND) · --status WAIT,LATER (any-of)
  wl ls -p A                         only P0 (A); -p A,B = any-of; -p P0 == -p A
  wl ls --unscheduled                open items with no schedule (inbox)
  wl ls --sort updated --limit 10    10 most-recently-logged (like ls -t; --sort created -r for newest)
  wl ls --ids 39 41 270              specific ids directly (like ls f1 f2)
  wl ls --all                        remove the 20-row cap + include DONE/CANCELED

More: `wl help ls` · sharper entry points: wl find <q> / wl day / wl active / wl projects.""")
    _args_node_ls(ls)

    tr = sub.add_parser("tree", parents=[filters, output_parent],
        help="tree view of nodes (default: timeline up to today + areas one level, ~30 rows)",
        description="Tree view of nodes. Default: timeline expanded up to today (year -> quarter -> month -> week -> today + today's tasks) + areas one level, ~30 rows to avoid scrolling. Use --root <id> to drill into a node.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl tree                      # default overview (timeline to today + areas)
  wl tree --root <id>          # the subtree under one node (area / project / day)
  wl tree --root <day> --depth 9   # full per-log expansion for a day
  wl tree --by project         # flat 2-level regroup (also --by tag / direction)
  wl tree -t work              # prune to matching nodes + their ancestors

More: `wl help tree` (vs `wl ls` flat list / `wl day` single day / `wl projects` cards).""")
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
        parents=[output_parent],
        help="focus on a node: upstream path + self + downstream subtree",
        formatter_class=_WlHelpFormatter,
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
        parents=[output_parent],
        help="upstream path: ancestor chain from root to the node",
        formatter_class=_WlHelpFormatter,
        epilog="Example: wl ancestors 42 -> Lifetime / Area / Project / Task. Inverse: wl descendants for the downstream subtree.")
    an.add_argument("id", type=int, help="node id")

    de = sub.add_parser("descendants",
        parents=[output_parent],
        help="downstream subtree: all descendants of a node",
        formatter_class=_WlHelpFormatter,
        epilog="Example: wl descendants 7 --depth 2 -> two levels of children under #7. wl tree --root 7 is equivalent but rendered as a tree.")
    de.add_argument("id", type=int, help="node id")
    de.add_argument("--depth", type=int, help="max depth")

    ag = sub.add_parser("agenda", parents=[filters, output_parent],
        help="cross-time-range scheduling overview: everything scheduled in [start, end]",
        formatter_class=_WlHelpFormatter,
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

    pj = sub.add_parser("projects", parents=[window, output_parent],
        help="list active projects + subtask counts + recent activity",
        description="List all active projects (type.para=project, status not DONE/CANCELED) with subtask counts + last log time. --since filters to projects with activity after that date.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl projects                         # all active projects (cards: subtask counts + activity)
  wl projects --since 2026-05-01      # active since a date (--week 2026-W22 too)
  wl projects --top 5                 # top 5 by priority
  wl projects --all                   # include DONE/CANCELED projects

More: `wl help projects` (vs `wl tree --by project` / `wl ls --para project`).""")
    pj.add_argument("--all", action="store_true", help="include DONE/CANCELED projects")
    pj.add_argument("--limit", type=int, metavar="N", help="show only the first N")
    pj.add_argument("--top", type=int, metavar="N",
                    help="top N by priority+id (semantics: high-priority active projects)")

    sub.add_parser("changes", parents=[window, output_parent],
        help="per-project changes in a time window (added / done / log counts)",
        description="What happened to each project in a time window: tasks added, tasks closed, new log count. Good input for weekly reports and Linear updates.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl changes --week 2026-W22          # this week's changes (per-project)
  wl changes --since 2026-05-01       # changes since a date
  wl changes --month 2026-05          # whole month

Weekly/Linear-update input: added / closed / log counts per project.

More: `wl help changes` (vs `wl summary` state snapshot / `wl projects` cards).""")

    sm = sub.add_parser("summary", parents=[window, output_parent],
        help="time-window aggregate: done/doing/added counts + grouped by project or day",
        description="Snapshot of current state distribution in a time window: counts of done / doing / added, grouped by project (default) or day. First-pass material for weekly / monthly reports.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl summary --week 2026-W22             # this week (by project)
  wl summary --since 2026-05-01 --by day # group by day
  wl summary --week 2026-W22 --top 5     # top 5 most-progressed projects (--projects-only too)
  wl summary --week ... -q               # brief (AI context-grab, large token savings)

More: `wl help summary` (vs `wl changes` deltas / `wl day` single day).""")
    sm.add_argument("--by", choices=["project", "day"], default="project", help="aggregate dimension (default: project)")
    sm.add_argument("--projects-only", action="store_true",
                    help="project rows only, no task expansion (same as --brief but explicit)")
    sm.add_argument("--top", type=int, metavar="N",
                    help="only the top N most-progressed projects")
    sm.add_argument("--no-dedup", action="store_true",
                    help="no dedup: a task across multiple projects is repeated in each bucket (old behavior)")

    dy = sub.add_parser("day", parents=[filters, output_parent],
        help="full view of a day (default today): bucket -> project/plan -> task -> log",
        description="Full view of one day: work/personal/other -> (planned/unplanned/project/priority) -> task -> indented logs. The header states the day's nature (workday / weekend, refined to holiday / leave / makeup by a `wl dateinfo` label). Top shows end-of-day summary + today's goal + the week's & month's goal (if set). Defaults to log-date-driven (works for past days too).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl day                       # today
  wl day 2026-05-30            # a past day (yesterday / tomorrow short forms too)
  wl day -t work               # only the work bucket (-t/--tag, AND; -t personal too)
  wl day --by project          # regroup (default --by plan = planned/unplanned; --by priority)
  wl day --log-tail 1          # logs default to last 3/task (--all-logs / --no-logs / --log-format full)

End-of-day flow: wl day → review → wl recap "..." to write the summary.

More: `wl help day` · `wl help planning` (the optional per-level planning cadence).""")
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
        parents=[output_parent],
        help="goal CRUD: read/write a node's goal — what you aim to deliver (history-preserving)",
        description="Read or write a goal — a short statement of what you aim to deliver. Bare `wl goal` reads/writes TODAY's goal (the default form, auto-creates today's day node); `wl goal set/ls/rm <node>` reach any node (day / week / month / year — the level is the node's type). Stored as a `goal` log (history-preserving: each write appends, the latest is current). `wl day` shows today's at the top. A goal can carry trailing node ids — its target nodes, in priority order — stored as `goal` metrics so the link is structured, not parsed from the prose.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl goal "ship the Q3 report draft"     # write today's goal
  wl goal "ship X" 12 34                 # today's goal + its target nodes #12,#34 (priority order)
  wl goal                                # read (no text)
  wl goal set <month_id> "deliver A, B" 7 9 3   # a month's goal + ~5 target nodes
  wl goal ls <node_id>                   # show a node's current goal / summary
  wl goal rm <node_id>                   # clear a node's goal (--summary clears the summary)

Planning rhythm (all optional, all history-preserving):
  morning   `wl goal "..."`                       today's intended deliverable
  evening   `wl recap "..."`                       what actually happened (the counterpart)
  weekly    `wl goal set <week_id> "..."`          this week's focus / P0-P1
  monthly   `wl goal set <month_id> "..." …ids`    the month's goals (we suggest ~5)
  Plan the *work* itself with `wl sched <id> <day>` (a task shows "planned" in `wl day`)
  or `wl defer <id> someday` (a loose backlog item). See `wl day -h` for the level cadence.""")
    _gsub = g.add_subparsers(dest="goal_sub")
    # default verb: bare `wl goal` / `wl goal "text" [ids]` → today's goal
    _gtoday = _gsub.add_parser("today", parents=[output_parent], help="read/write today's goal (the default bare form)")
    _gtoday.add_argument("text", nargs="?", help="no arg = read today's goal; with text = write it")
    _gtoday.add_argument("goals", nargs="*", type=int, metavar="ID",
                   help="target node ids after the text, in priority order (`wl goal \"...\" 12 34`) — stored as `goal` metrics so the link is structured, not parsed from the prose")
    _gset = _gsub.add_parser("set", help="set a goal on any node (--summary for the summary; --ids sets target ids on the current goal)")
    _gset.add_argument("id", type=int)
    _gset.add_argument("value", nargs="?", help="goal/summary text (omit only with --ids)")
    _gset.add_argument("goals", nargs="*", type=int, metavar="ID",
                       help="target node ids after the value, in priority order — stored as `goal` metrics so the link is structured")
    _gset.add_argument("--summary", action="store_true",
                       help="set the node's summary (backward-looking recap) instead of its goal — prose only, no target ids")
    _gset.add_argument("--ids", nargs="+", type=int, metavar="ID",
                       help="set the node's CURRENT goal targets to exactly these node ids (priority order; replaces, no new log, no text) — the 'I wrote the goal, now attach the ids' fix")
    _gls = _gsub.add_parser("ls", parents=[output_parent], help="list a node's current goal / summary")
    _gls.add_argument("id", type=int)
    _grm = _gsub.add_parser("rm", help="clear a node's goal (--summary for the summary)")
    _grm.add_argument("id", type=int)
    _grm.add_argument("--summary", action="store_true", help="clear the summary instead of the goal")

    rc = sub.add_parser("recap",
        parents=[output_parent],
        help="read/write a day's end-of-day summary — what actually happened (history-preserving)",
        description="Read or write a day's end-of-day summary — a short reflection on what actually happened. Stored as the day node's `summary` reserved-tag log (history-preserving); the write time is recorded so `wl day` can warn if you log more after recapping. The evening counterpart to `wl goal`.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl recap "shipped the draft; blocked on review"   # write today's summary
  wl recap                                           # read
  wl recap --date 2026-06-01 "..."                  # write a past day's recap (back-fill)
  wl recap --date yesterday                          # read yesterday's recap

`wl day` shows "Recap: ... (written at MM-DD HH:MM)" at the top; if you log more after
recapping it warns "⚠ N changes after recap, consider rewriting". (`wl goal` is the morning
counterpart; week/month goals are `wl goal set <week>` / `<month> "..." …ids`.)""")
    rc.add_argument("text", nargs="?", help="no arg = read; with text = write the summary")
    rc.add_argument("-d", "--date", help="target day (YYYY-MM-DD / today / yesterday / 昨天 ...); default today")
    rc.add_argument("--diff", action="store_true",
                    help="list the plain-note logs added that day AFTER the recap was written (what `wl day`'s '⚠ N change(s) after recap' counts; excludes checkin/metric noise) — judge if a rewrite is warranted")

    tk = sub.add_parser("tick",
        help="quick check-in: add a log to each node today (batch habit check-in)",
        formatter_class=_WlHelpFormatter,
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
        formatter_class=_WlHelpFormatter,
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
        formatter_class=_WlHelpFormatter,
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

    rt = sub.add_parser("retag",
        help="change one log's tag (goal / summary / custom; `note` clears to a plain note)",
        description="Change a single log's `tag` directly — the tag classifies a log's role (goal / summary / a custom marker); a plain note has no tag. `note` / `none` / `-` / empty clears it back to a plain note.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
  wl retag #L282 goal      # mark this log as a goal
  wl retag #L282 summary   # ... or a summary
  wl retag #L282 note      # clear back to a plain note (note / none / - / "" all clear)""")
    rt.add_argument("log_id", type=_log_id_arg, metavar="log_id", help="log id (#L282 / L282 / 282)")
    rt.add_argument("tag", help="new tag (goal / summary / a custom marker; `note` clears it)")

    # ── metric: structured datapoints on a log (node → log → metric) ──
    mt = sub.add_parser("metric",
        help="structured datapoints (check-in / number / measurement): add/ls/edit/rm",
        description="CRUD for metrics — structured datapoints that hang off a log "
                    "(node → log → metric). A metric has a `tag` (what it is: glucose / "
                    "pullups / checkin …), an optional numeric or text value + unit, a note, "
                    "and a timestamp.",
        formatter_class=_WlHelpFormatter,
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

    _ma = _msub.add_parser("add", parents=[output_parent], help="add a datapoint to a node")
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

    _ml = _msub.add_parser("ls", help="list metrics: a node's (default this week), OR omit the node to find a tag across ALL nodes",
                           parents=[window, output_parent])
    _ml.add_argument("node", type=int, nargs="?", help="node id; omit to search every node (pair with --tag to locate all uses of a tag)")
    _ml.add_argument("--tag", help="filter by tag")
    _ml.add_argument("--all", action="store_true", help="all datapoints (ignore the default this-week window)")

    _me = _msub.add_parser("edit", parents=[output_parent], help="edit a metric's fields")
    _me.add_argument("metric_id", type=_metric_id_arg, help="metric id (#M7 / M7 / 7; from wl metric ls)")
    _me.add_argument("--value", help="new value, autodetected numeric vs text (mutually exclusive with --num/--text)")
    _me.add_argument("--num", type=float, help="set numeric value (clears text value)")
    _me.add_argument("--text", help="set text value (clears numeric value)")
    _me.add_argument("--unit", help="set unit ('' clears)")
    _me.add_argument("-n", "--note", help="set note ('' clears)")
    _me.add_argument("--tag", help="change tag")
    _me.add_argument("--at", help="change timestamp")

    _mr = _msub.add_parser("rm", parents=[output_parent], help="delete one or more metrics")
    _mr.add_argument("metric_ids", type=_metric_id_arg, nargs="+", help="metric id(s) (#M7 / M7 / 7)")

    ci = sub.add_parser("checkin",
        help="interactive check-in of today's habits (default multi-select arrows / space / Enter)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl checkin                          # default multi-select (arrows / space / Enter)
  wl checkin --per-item               # fallback: prompt y/n/note/q per item (allows per-item note)
  wl checkin --all-types              # not just habit; include all task/meetlog/... scheduled today

End-of-day: run wl checkin once to review every habit that's due today.
For single habit check-in, use wl tick <id>.""")
    ci.add_argument("--all-types", action="store_true",
                    help="review all scheduled items (habit + task + meetlog), not just habits (the default)")
    ci.add_argument("--per-item", action="store_true",
                    help="fallback mode: prompt y/n/note/q per item (allows per-item note; auto-used when not on a TTY)")

    # sched entity group: add / ls / rm. `add` is the default verb so the everyday
    # `wl sched <id> <when>` / `wl sched <id> --clear` keep working (full when / --recur /
    # --clear / list-when-empty grammar via cmd_sched); `ls` lists, `rm` clears. `wl defer`
    # (status=LATER + rough hint) stays its own composite command.
    sc = sub.add_parser("sched",
        help="sched CRUD: add / ls / rm — wl sched 42 2026-06-15 schedules (default verb add); drives wl day 'planned'",
        description="Forward-planning CRUD — schedule a task to a day / recurring rule (drives wl day 'planned') — the metric-style entity group. `wl sched <id> <when>` is the add shortcut (the default verb) and keeps the full when / --recur / --clear / list-when-empty grammar. Distinct from `wl defer` (status=LATER + rough hint).",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl sched 42 2026-06-15       # a specific day (today / tomorrow short forms too)
  wl sched 42 --recur weekly:Mon,Fri   # recurring (daily / monthly:-1 / quarterly:1-15 / yearly:-1)
  wl sched 42                  # list this task's schedule
  wl sched 42 --clear          # clear all entries (= wl sched rm 42)

Distinct from `wl defer` (status LATER + rough hint). Create + schedule: wl add "..." --sched today.

More: `wl help sched` (the full recurring-rule grammar).""")
    _scsub = sc.add_subparsers(dest="sched_sub")
    _sca = _scsub.add_parser("add", parents=[output_parent],
        help="schedule to a day / recurring rule (= the default wl sched 42 <when>)",
        description="Schedule a task to a one-off day or a recurring rule (--recur); no when/--recur lists; --clear clears. Also reachable as the default `wl sched <id> <when>` (omit `add`; see `wl sched -h`).")
    _sca.add_argument("id", type=int)
    _sca.add_argument("when", nargs="?", help="YYYY-MM-DD / today / yesterday / tomorrow / a signed delta +1 / -2d / +3w / -1y (one-off date)")
    _sca.add_argument("--recur",
                      help="recurring rule (all support -1 = last day): daily / weekly:Mon|1-7|-1 / monthly:5|-1 / quarterly:M-D|-1 / yearly:MM-DD|-1")
    _sca.add_argument("--clear", action="store_true", help="clear all schedule entries for this task")
    _scls = _scsub.add_parser("ls", parents=[output_parent], help="list a node's schedule entries (= bare wl sched <id>)")
    _scls.add_argument("id", type=int)
    _scr = _scsub.add_parser("rm", parents=[output_parent], help="clear a node's schedule entries (= wl sched <id> --clear)")
    _scr.add_argument("id", type=int)

    di = sub.add_parser("dateinfo",
        help="date metadata (holiday/vacation/working-day swap; shown in wl day header)",
        description="Date metadata (holiday / vacation / working-day-swap label; shown in the wl day header) — the polymorphic everyday shortcut over the date_meta table. The explicit metric-style form is the `wl date set / ls / rm / import` group (see `wl date -h`).",
        formatter_class=_WlHelpFormatter,
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

    # date entity group: set / ls / rm / import. A clean group — `date` doesn't
    # collide with any leaf, so no default verb. `wl dateinfo` is the polymorphic shortcut.
    dt = sub.add_parser("date",
        help="date-metadata CRUD: set / ls / rm / import (polymorphic shortcut: wl dateinfo)",
        description="Date-metadata CRUD (holiday / vacation / working-day-swap label, shown in the wl day header) — the metric-style entity group. The everyday polymorphic shortcut is `wl dateinfo` (set when a label is given, list when not, --clear / --import variants).",
        formatter_class=_WlHelpFormatter,
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
    _dtset = _dtsub.add_parser("set", parents=[output_parent], help="set/update a date's label (= wl dateinfo <date> <label>)")
    _dtset.add_argument("date", help="YYYY-MM-DD")
    _dtset.add_argument("label", help="label, e.g. Labor Day / swap working day / vacation")
    _dtls = _dtsub.add_parser("ls", parents=[output_parent], help="list date metadata, or show one date (= bare wl dateinfo)")
    _dtls.add_argument("date", nargs="?", help="YYYY-MM-DD (omit to list all)")
    _dtsub.add_parser("rm", parents=[output_parent], help="clear a date's label (= wl dateinfo <date> --clear)").add_argument("date", help="YYYY-MM-DD")
    _dtsub.add_parser("import", help='batch import {"YYYY-MM-DD":"label"} JSON (= wl dateinfo --import)').add_argument("file", help="JSON file path, or - for stdin")

    # (the former `wl meta` group is merged into `wl goal` — goal/summary reserved-tag logs
    # are managed via `wl goal set/ls/rm` and the bare `wl goal` / `wl recap` shortcuts.)

    # alias command: manage ~/.config/worklog/aliases.ini (wired into the parser at startup)
    al = sub.add_parser("alias",
        help="manage command aliases: add / ls / rm (e.g. wl alias add w \"day -t work\" → wl w)",
        description="Manage command aliases stored in ~/.config/worklog/aliases.ini. An alias maps a short name to a wl command, optionally WITH arguments — `wl alias add w \"day -t work\"` makes `wl w` == `wl day -t work` (extra args you type are appended); `wl alias add d day` makes `wl d` == `wl day`. Aliases are wired in at startup, so a change takes effect on the NEXT wl invocation.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
  wl alias add d day            # wl d == wl day (next run)
  wl alias add w "day -t work"  # wl w == wl day -t work (quote a target with args)
  wl alias add p "day -t personal"
  wl alias ls                   # list configured aliases
  wl alias rm d                 # remove an alias

The target's first word must be a real wl command; an alias can't shadow an existing command.
Args you type after the alias are appended (`wl w 2026-06-08` → `wl day -t work 2026-06-08`).""")
    _alsub = al.add_subparsers(dest="alias_sub")
    _aadd = _alsub.add_parser("add", help="add/update an alias (name → command [+ args])")
    _aadd.add_argument("name", help="the short alias to type")
    _aadd.add_argument("target", help="the wl command it expands to (quote if it carries args: \"day -t work\")")
    _alsub.add_parser("ls", help="list configured aliases")
    _alsub.add_parser("rm", help="remove an alias").add_argument("name", help="alias name to remove")

    im = sub.add_parser("import",
        help="bulk load from JSON ({add:[...],update:[...]}; main AI integration path)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl import data.json             # load a {"add":[...],"update":[...]} document
  wl import data.json --dry-run   # preview without writing
  wl import - < data.json         # from stdin

The main AI/scripted path for loading many nodes at once (vs `wl apply`'s lightweight diff).

More: `wl help import` (the full JSON shape).""")
    im.add_argument("file", help="JSON file path, or - for stdin")
    im.add_argument("--dry-run", action="store_true", help="preview without writing")

    ap = sub.add_parser("apply",
        help="apply wl-diff lightweight bulk changes (+add/~update/-delete/ anchor; same format as wl output)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl apply diff.txt              # apply a wl-diff (+add / ~update / -delete; mirrors wl output)
  wl apply diff.txt --dry-run    # validate + preview without writing
  wl apply - < diff.txt          # from stdin

Lightweight hand/AI edits (vs `wl import`'s rich JSON document).

More: `wl help apply` (the full wl-diff format).""")
    ap.add_argument("file", help="wl-diff file path, or - for stdin")
    ap.add_argument("--dry-run", action="store_true", help="validate + preview without writing")

    fd = sub.add_parser("find",
        parents=[output_parent],
        help="full-text search nodes (title/body/log/tag/prop/link, any match)",
        description="Full-text search across fields: title/body/log/tag/prop/link, any match returns. Default limit 20; --all removes it.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl find skill                       # default limit 20 (--all / --limit N to adjust)
  wl find skill --para project        # only projects
  wl find skill --in title,tag        # only in title/tag (default: all fields)

Before writing a new task/log, run `wl find` to merge into an existing node, not duplicate it.

More: `wl help find` (vs `wl ls --tag` precise filter / `wl ls --recent` by time).""")
    fd.add_argument("query", help="text to search for (matches title/body/log/tag/prop/link)")
    fd.add_argument("--in", dest="in_", help="comma-separated fields to search (default: all)")
    fd.add_argument("--para", choices=["area", "project", "task"],
                    help="filter by responsibility role (the type.para prop)")
    fd.add_argument("--limit", type=int, metavar="N", help="show only the first N (default 20; use 0 or --all for no cap)")
    fd.add_argument("--all", action="store_true", help="no row limit")

    # --- semantic search (the 'semantic' extra): wl reindex / wl query ---
    # Shared embedding-backend override flags (default<config.ini<env<flag, see worklog.config).
    embed_parent = argparse.ArgumentParser(add_help=False)
    embed_parent.add_argument("--endpoint", help="OpenAI-compatible /v1/embeddings URL (overrides config/env)")
    embed_parent.add_argument("--model", help="embedding model name (overrides config/env)")
    embed_parent.add_argument("--dimensions", type=int, help="truncate embeddings to N dims (if the model supports it)")
    embed_parent.add_argument("--api-key", dest="api_key", help="bearer token for the embedding server, if it needs one")
    embed_parent.add_argument("--query-prompt", dest="query_prompt",
        help="query template ({query} placeholder; pass '' to disable) — overrides config/env")

    rx = sub.add_parser("reindex", parents=[embed_parent],
        help="build/update the semantic search index (incremental by default)",
        formatter_class=_WlHelpFormatter,
        description="Embed live nodes (title+body+logs+tags) via the configured embedding server "
                    "and store the vectors in a sidecar store. Default = incremental: only "
                    "new/changed nodes are re-embedded and deleted ones are evicted. On first run "
                    "(no index yet) it automatically does a full pass. `wl query` reads what this "
                    "builds. Uses LanceDB if the 'semantic' extra is installed, else a slower "
                    "pure-Python SQLite fallback (no extra needed).",
        epilog="""\
Common examples:
  wl reindex                          # incremental top-up (full on first run)
  wl reindex --full                   # full rebuild — use after a model change
  wl reindex --endpoint http://host:11434/v1/embeddings --model qwen3-embedding

Vector backend: LanceDB (fast, the optional 'semantic' extra) on any Python 3.9–3.14 on Linux
or Apple-Silicon macOS; falls back automatically to a pure-Python SQLite store where no LanceDB
wheel exists (Intel macOS, musl/Alpine, …) — same results, slower at large scale.
More: `wl query` to search; configure the backend in config.ini [embedding] / $WORKLOG_EMBED_*.""")
    rx.add_argument("--full", action="store_true",
                    help="force a full rebuild (drop the index and re-embed every node) — use after a model change or to repair a corrupt index")
    rx.add_argument("--auto", action="store_true",
                    help="single-flight background worker: hold a lock (skip if one's running) and loop incremental passes until the index is clean. Spawned automatically after a write when [index] auto_reindex is on")

    qy = sub.add_parser("query", parents=[embed_parent, output_parent],
        help="semantic search: nearest nodes by meaning (vs `find`'s keyword match)",
        formatter_class=_WlHelpFormatter,
        description="Embed the query and return the nodes whose meaning is closest (cosine), "
                    "finding paraphrases that keyword `find` misses. Needs `wl reindex` first. "
                    "Uses the LanceDB store if installed, else the pure-Python SQLite fallback.",
        epilog="""\
Common examples:
  wl query "how to avoid duplicate work"   # concept search, not substring
  wl query "open-source release" --limit 5
  wl query "performance" --threshold 0.4    # drop weak matches

More: `wl find` for exact keyword/substring; `wl reindex` to (re)build the index.""")
    qy.add_argument("query", help="natural-language text to search for by meaning")
    qy.add_argument("--limit", type=int, metavar="N", default=10, help="max results (default 10)")
    qy.add_argument("--threshold", type=float, metavar="T", help="drop matches with cosine score below T")

    lg = sub.add_parser("logs", parents=[window, filters, output_parent],
        help="list log entries (default last 7 days; preset today/yesterday/week/recent)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Common examples:
  wl logs today                       # presets: today / yesterday / week / recent
  wl logs --id 42 --tail 5            # last 5 logs for one task
  wl logs --since 2026-05-01          # a time window (--since/--until/--week/--month)
  wl logs --by-task --tail 3          # aggregate by task, last 3 per task
  wl logs --group day --by project    # group by day -> project -> task

Defaults to the last 7 days to avoid flooding.

More: `wl help logs` (vs `wl day` structured view / `wl show <id>` single-node timeline).""")
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

    sub.add_parser("types", parents=[output_parent],
        help="list the type.*/date.* classification props in use + counts (raw, grouped by key)",
        formatter_class=_WlHelpFormatter,
        epilog="The raw classification vocabulary grouped by `type.*`/`date.*` key — each facet shown on its own, so a node shows under every facet it carries. `-o json` for the machine list. Filter by role with `wl ls --para <role>`, or by classification prop with `wl ls --prop type.<x>`.")

    sub.add_parser("tags", parents=[output_parent],
        help="list every tag in use + a count of nodes carrying it",
        formatter_class=_WlHelpFormatter,
        epilog="Cross-node companion to `wl tag <id>`. Most-used first; `-o json` too. List a tag's nodes: `wl ls -t <tag>`.")
    sub.add_parser("props", parents=[output_parent],
        help="list every prop key in use + a count (namespaces like github.*/linear.* group)",
        formatter_class=_WlHelpFormatter,
        epilog="Cross-node companion to `wl prop ls <id>`. Alphabetical; `-o json` too. Reverse-query a key: `wl ls --prop <key>`.")
    sub.add_parser("metrics", parents=[output_parent],
        help="list every metric tag in use + a count of datapoints",
        formatter_class=_WlHelpFormatter,
        epilog="Cross-node companion to `wl metric ls <id>`. Most-used first; `-o json` too.")

    sub.add_parser("themes",
        help="list all color themes (one-line preview per theme)",
        formatter_class=_WlHelpFormatter,
        epilog="Switch theme: top-level --theme {auto,dark,light,mono} flag, or export WORKLOG_THEME=...; auto probes terminal background and picks dark/light.")

    hp = sub.add_parser("help",
        help="info-style topic browser: wl help lists topics, wl help <topic> reads one",
        description="Browse the bundled help topics — fuller explanations of commands, concepts, parameters, and workflows than `<command> -h` gives, with 'See also' links. `wl help` shows a short overview; `wl help --all` lists every topic by category; `wl help <topic>` reads one topic.",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Examples:
  wl help                 # short overview + pointer to the full list
  wl help --all           # every topic, grouped by category
  wl help para            # how to organize (areas / projects / tasks)
  wl help planning        # goals / summaries / scheduling rhythm
  wl help status          # what the [ ] / [/] / [x] markers mean

Topics live as Markdown docs in the repo (i18n-ready); `wl <command> -h` stays the quick
per-command reference, `wl help <topic>` is the fuller teaching layer.""")
    hp.add_argument("topic", nargs="?", help="topic name (omit for the overview; e.g. node / add / planning)")
    hp.add_argument("--all", action="store_true", help="list every help topic by category (the full index)")
    hp.add_argument("--lang", help="help language (default: $WORKLOG_LANG / $LANG, falling back to en)")

    pc = sub.add_parser("print-completion",
        help="dump shell completion script (argparse -> fish/bash/zsh; init-load model)",
        formatter_class=_WlHelpFormatter,
        epilog="""\
Usage (write once to your shell rc, then new shells auto-load; stays in sync with code changes):
  # fish: add to ~/.config/fish/config.fish
  wl print-completion fish | source

  # bash: add to ~/.bashrc
  eval "$(wl print-completion bash)"

  # zsh: add to ~/.zshrc
  eval "$(wl print-completion zsh)"

Same pattern as starship/direnv/zoxide.

User aliases: add [aliases] section to ~/.config/worklog/aliases.ini (e.g. d = day / w = day -t work / ...); a target may carry args; new shells pick them up (uniform across shells).""")
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
    cmd_relation,
    cmd_set,
    cmd_tag,
    cmd_tag_group,
    cmd_log_group,
    cmd_node_edit,
    cmd_node_rm,
    cmd_node_reparent,
    cmd_agent,
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
    cmd_types,
    cmd_tags,
    cmd_props,
    cmd_metrics,
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
    cmd_goal_group,
    cmd_summary_prop,
    _checkin_collect,
    _is_interactive_tty,
    _multi_select_tty,
    _checkin_per_item,
    cmd_checkin,
    cmd_unlog,
    cmd_relog,
    cmd_retag,
    _edit_in_editor,
    cmd_tick,
    _norm_rrule,
    cmd_sched,
    cmd_sched_group,
    cmd_dateinfo,
    cmd_date_group,
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
    colorize_help,
    topic_exists,
    topic_names,
    cmd_query,
    cmd_reindex,
)

def cmd_node(args, con):
    """Dispatch `wl node <add|ls|show|edit|rm|reparent>` (the metric-style entity group). The top-level add/ls/show route to the same handlers."""
    sub = getattr(args, "node_sub", None)
    if sub is None:
        render.die("usage: wl node <add|ls|show|edit|rm|reparent> … (see `wl node --help`)")
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
    "relation": cmd_relation,
    "set": cmd_set,
    "unset": cmd_prop_rm,
    "tag": cmd_tag_group,
    "node": cmd_node,
    "agent": cmd_agent,
    "prop": cmd_prop,
    "clock": cmd_clock,
    "metric": cmd_metric,
    "show": cmd_show,
    "ls": cmd_ls,
    "tree": cmd_tree,
    "projects": cmd_projects,
    "types": cmd_types,
    "tags": cmd_tags,
    "props": cmd_props,
    "metrics": cmd_metrics,
    "changes": cmd_changes,
    "summary": cmd_summary,
    "focus": cmd_focus,
    "ancestors": cmd_ancestors,
    "descendants": cmd_descendants,
    "agenda": cmd_agenda,
    "day": cmd_day,
    "goal": cmd_goal_group,
    "recap": cmd_summary_prop,
    "tick": cmd_tick,
    "unlog": cmd_unlog,
    "relog": cmd_relog,
    "retag": cmd_retag,
    "checkin": cmd_checkin,
    "sched": cmd_sched_group,
    "dateinfo": cmd_dateinfo,
    "date": cmd_date_group,
    "alias": cmd_alias,
    "import": cmd_import,
    "apply": cmd_apply,
    "find": cmd_find,
    "query": cmd_query,
    "reindex": cmd_reindex,
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
    print('  wl add "task title"              add a task')
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
    from .commands.output import set_json_error_mode
    set_json_error_mode(getattr(args, "output", "text") == "json")
    _init_console(args.color, args.theme)
    _set_width_cap(_resolve_width_cap(getattr(args, "width", None)))
    _set_title_mode(_resolve_title_mode(getattr(args, "title", None)))
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
        _maybe_kick_reindex(args, con)
    finally:
        con.close()


def _maybe_kick_reindex(args, con):  # pragma: no cover -- spawns a detached process; not unit-tested
    """After a command that wrote to the DB, spawn a detached single-flight background incremental
    reindex (`wl reindex --auto`), so new/changed nodes get embedded without a manual `wl reindex`.
    No-op for read commands (nothing changed), for `reindex` itself, and when disabled by config/env.
    Best-effort: any failure is swallowed — a stale index must never break the foreground command."""
    try:
        if args.cmd == "reindex" or con.total_changes <= 0:
            return
        from . import config as _config
        if not _config.auto_reindex_enabled():
            return
        import subprocess
        db = getattr(args, "db", None)
        # --db is a GLOBAL flag → it must precede the subcommand, not follow it
        cmd = [sys.argv[0]] + (["--db", str(db)] if db else []) + ["reindex", "--auto"]
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
