"""worklog bootstrap commands: `wl init` / `config` / `migrate` / `themes`.

These set up or report on the DB / config / theming rather than touch task data; `init` and
`config` are routed in main() to bypass `ensure_db()`."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .. import render
from ..render import _c, out, _resolve_theme, THEMES
from ..xdg import (_resolve_db_path, _resolve_aliases_path, _resolve_config_path,
                   _xdg_data_home, _xdg_config_home)

# Lazy access to the cli module (db_init / migration helpers / __version__) — at call time.
from .. import cli as _cli  # noqa: E402

# Bundled config.ini template (shipped in the wheel, like help/*.md), copied by `wl config init`.
_CONFIG_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "config.ini"


def cmd_init(args, con):
    _cli.db_init(con)
    print(f"✓ DB initialized: {_resolve_db_path(args)}")


def cmd_config_init(args, con):
    """Write a commented config.ini template from the bundled template; never overwrite."""
    dest = _resolve_config_path()
    if dest.exists():
        out(_c(f"config already exists — not overwriting: {dest}", "meta"))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_CONFIG_TEMPLATE, dest)
    out(_c(f"✓ wrote config template: {dest}", "done"))
    out(_c("  edit it (everything starts commented = defaults), then `wl config` shows resolved values.", "meta"))


def cmd_config(args, con):
    """Print resolved configuration; `wl config init` writes a template instead."""
    if getattr(args, "config_sub", None) == "init":
        return cmd_config_init(args, con)
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
    out(_c("embedding (wl query / reindex):", "header"))
    from ..config import resolve_embedding_config
    ec = resolve_embedding_config(args)
    _row("endpoint", ec["endpoint"], f"[{ec['source']['endpoint']}]")
    _row("model", ec["model"], f"[{ec['source']['model']}]")
    _row("dimensions", ec["dimensions"] if ec["dimensions"] is not None else _c("(model native)", "meta"),
         f"[{ec['source']['dimensions']}]")
    _row("api_key", _c("(set)", "meta") if ec["api_key"] else _c("(none)", "meta"),
         f"[{ec['source']['api_key']}]")
    qp = ec["query_prompt"]
    qp_show = _c("(disabled)", "meta") if not qp else (qp[:54].replace("\n", "\\n") + ("…" if len(qp) > 54 else ""))
    _row("query_prompt", qp_show, f"[{ec['source']['query_prompt']}]")
    try:
        import lancedb  # noqa: F401
        vec_status, vec_note = "LanceDB", "the 'semantic' extra (fast)"
    except ImportError:
        vec_status = "SQLite (pure-Python fallback)"
        vec_note = "no LanceDB wheel — `pip install 'pyworklog[semantic]'` for the fast store"
    _row("vector store", vec_status, vec_note)

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


def cmd_migrate_types(args, con):
    """Backfill the type.*/date.* namespace onto existing nodes from their legacy kind, then
    verify every node round-trips (kind left intact; idempotent). The data half of the kind→
    type.* transition — run it once so `wl ls --para` and friends see pre-existing nodes too."""
    from ..node_type_backfill import migrate_and_verify
    c, ok, mismatches, retired = migrate_and_verify(con)
    out(_c(f"✓ type.* backfill: {c['para']} para, {c['date']} date, {c['habit']} habit, "
           f"{c['meetlog']} meetlog ({c['bare']} left bare)", "done"))
    if retired:
        out(_c(f"  {len(retired)} node(s) with a retired/custom kind collapsed to a bare node "
               "(signal is removed by design)", "meta"))
    if ok:
        out(_c("✓ verified: every classified node's type.* round-trips to its original kind", "done"))
    else:
        out(_c(f"✗ {len(mismatches)} node(s) did NOT round-trip — type.* would lose their "
               "classification; do NOT drop the kind column:", "later"))
        for nid, col, der in mismatches[:20]:
            out(_c(f"    #{nid}: kind={col!r} but derived {der!r}", "later"))
        sys.exit(1)


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
