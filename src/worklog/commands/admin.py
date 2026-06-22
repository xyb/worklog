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
from .output import output_format, TextRenderable, text_renderer

# Lazy access to the cli module (db_init / migration helpers / __version__) — at call time.
from .. import cli as _cli  # noqa: E402

# Bundled config.ini template (shipped in the wheel, like help/*.md), copied by `wl config init`.
_CONFIG_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "config.ini"


@text_renderer("config_init")
def _render_config_init(result):
    _dest = result["path"]
    if not result["created"]:
        out(_c(f"config already exists — not overwriting: {_dest}", "meta"))
    else:
        out(_c(f"✓ wrote config template: {_dest}", "done"))
        out(_c("  edit it (everything starts commented = defaults), then `wl config` shows resolved values.", "meta"))


@text_renderer("config")
def _render_config(result):
    def _row(label, value, hint=""):
        hint_part = "  " + _c(hint, "meta") if hint else ""
        out(f"  {label:<18} {value}{hint_part}")

    db = result["database"]
    aliases_path = result["aliases"]["path"]
    aliases_exists = result["aliases"]["exists"]
    xdg = result["xdg"]
    env = result["env"]
    ec = result["embedding"]
    ec_api_key_set = result["embedding_api_key_set"]
    vec_backend = result["vector_backend"]

    out(_c(f"worklog {result['version']}", "header"))
    out("")
    out(_c("paths:", "header"))
    _row("database", db["path"], f"[{db['source']}] {db['size']}")
    _row("aliases", aliases_path, "(exists)" if aliases_exists else "(not configured)")
    out("")
    out(_c("XDG directories:", "header"))
    _row("XDG_DATA_HOME", xdg["data_home"], "(env set)" if xdg["data_home_set"] else "(default)")
    _row("XDG_CONFIG_HOME", xdg["config_home"], "(env set)" if xdg["config_home_set"] else "(default)")
    out("")
    out(_c("environment:", "header"))
    for var in ("WORKLOG_DB", "WORKLOG_COLOR", "WORKLOG_THEME", "NO_COLOR"):
        val = env.get(var)
        _row(var, val if val else _c("(not set)", "meta"))
    out("")
    out(_c("embedding (wl query / reindex):", "header"))
    _row("endpoint", ec["endpoint"], f"[{ec['source']['endpoint']}]")
    _row("model", ec["model"], f"[{ec['source']['model']}]")
    _row("dimensions", ec["dimensions"] if ec["dimensions"] is not None else _c("(model native)", "meta"),
         f"[{ec['source']['dimensions']}]")
    _row("api_key", _c("(set)", "meta") if ec_api_key_set else _c("(none)", "meta"),
         f"[{ec['source']['api_key']}]")
    qp = ec.get("query_prompt")
    qp_show = _c("(disabled)", "meta") if not qp else (qp[:54].replace("\n", "\\n") + ("…" if len(qp) > 54 else ""))
    _row("query_prompt", qp_show, f"[{ec['source']['query_prompt']}]")
    if vec_backend == "lancedb":
        _row("vector store", "LanceDB", "the 'semantic' extra (fast)")
    else:
        _row("vector store", "SQLite (pure-Python fallback)",
             "no LanceDB wheel — `pip install 'pyworklog[semantic]'` for the fast store")
    out("")
    out(_c("runtime:", "header"))
    _row("python", result["python"], f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    _row("rich", "available" if result["rich"] else "not installed (plain-text mode)")


@text_renderer("migrate")
def _render_migrate(result):
    if result["pending"] == 0:
        out(_c(f"✓ DB at version {result['version']}, no pending migrations ({result['total']} total).", "done"))
    else:
        applied = result["applied"]
        out(_c(f"✓ DB now at version {result['version']} ({len(applied)} migration(s) applied).", "done"))


def cmd_init(args, con):
    _cli.db_init(con)
    out(_c(f"✓ DB initialized: {_resolve_db_path(args)}", "done"))


@output_format
def cmd_config_init(args, con):
    """Write a commented config.ini template from the bundled template; never overwrite."""
    dest = _resolve_config_path()
    _dest = str(dest)
    if dest.exists():
        return TextRenderable({"created": False, "path": _dest}, cmd_name="config_init")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_CONFIG_TEMPLATE, dest)
    return TextRenderable({"created": True, "path": _dest}, cmd_name="config_init")


@output_format
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
    from ..config import resolve_embedding_config
    ec = resolve_embedding_config(args)
    try:
        import lancedb  # noqa: F401
        vec_backend = "lancedb"
    except ImportError:
        vec_backend = "sqlite"
    result = {
        "version": _cli.__version__,
        "database": {"path": str(db), "source": db_src, "size": db_size, "exists": db_exists},
        "aliases": {"path": str(aliases), "exists": aliases.exists()},
        "xdg": {"data_home": str(_xdg_data_home()), "config_home": str(_xdg_config_home()),
                "data_home_set": bool(os.environ.get("XDG_DATA_HOME")),
                "config_home_set": bool(os.environ.get("XDG_CONFIG_HOME"))},
        "env": {v: os.environ.get(v) for v in ("WORKLOG_DB", "WORKLOG_COLOR", "WORKLOG_THEME", "NO_COLOR")},
        "embedding": {k: v for k, v in ec.items() if k != "api_key"},
        "embedding_api_key_set": bool(ec.get("api_key")),
        "vector_backend": vec_backend,
        "python": sys.executable,
        "rich": render._RICH_AVAIL,
    }
    return TextRenderable(result, cmd_name="config")


@output_format
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
        return TextRenderable(
            {"version": current, "pending": 0, "total": len(files), "applied": []},
            cmd_name="migrate",
        )
    out(_c(f"applying {len(pending)} migration(s) (DB at version {current}):", "header"))
    applied = _cli._run_migrations(con, verbose=True)
    new_version = _cli._db_version(con)
    return TextRenderable(
        {"version": new_version, "pending": len(applied), "applied": [p.name for p in applied]},
        cmd_name="migrate",
    )


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
            print(f"■ {name}{mark}")  # noqa: cmd_themes has no @output_format, no suppression in effect
        print(f"current: {req}{auto_note}")  # noqa
        if not render._RICH_AVAIL:
            print("(rich not installed; no color preview; pip install rich)")  # noqa
        return
    # render the sample with each theme's own palette (force_terminal: keeps colors when piped to less -R)
    for name in THEMES:
        prev = render._RichConsole(theme=render._RichTheme(THEMES[name]), force_terminal=True, highlight=False, soft_wrap=True)
        mark = f"  [done]<- current {auto_note}[/done]" if name == cur else ""
        prev.print(f"[header]■ {name}[/header]{mark}")
        prev.print("  [done]\\[x][/done] [pri_a]\\[#A][/pri_a] [id]#42[/id] [type]\\[project][/type] "
                   "sample task with [hit]match[/hit] [planned]·planned[/planned]  [clock]⏱30min[/clock]  [tag]:work:[/tag]")
        prev.print("  [doing]\\[/][/doing] [pri_b]\\[#B][/pri_b] [id]#43[/id] doing sample    "
                   "[later]\\[>][/later] [pri_c]\\[#C][/pri_c] [id]#44[/id] later sample  [meta]«meta»[/meta]")
        prev.print()


@output_format
def cmd_doctor(args, con):
    """Scan the node graph for the inconsistencies no foreign key prevents (FK is off):
    dangling parent_id, parent cycles, orphaned spoke rows, relation.* refs to dead nodes,
    one-sided relations. Read-only — reports, never fixes. A clean exit means the graph is
    consistent."""
    from ..graph import check_integrity
    issues = check_integrity(con)
    return TextRenderable(
        {"issue_count": len(issues),
         "issues": [{"kind": i.kind, "node_id": i.node_id, "detail": i.detail} for i in issues]},
        cmd_name="doctor",
    )


@text_renderer("doctor")
def _render_doctor(result):
    issues = result["issues"]
    if not issues:
        out(_c("✓ graph consistent — no integrity issues found", "done"))
        return
    out(_c(f"⚠ {len(issues)} graph integrity issue(s) found:", "doing"))
    by_kind = {}
    for i in issues:
        by_kind.setdefault(i["kind"], []).append(i)
    for kind in sorted(by_kind):
        group = by_kind[kind]
        out(_c(f"  {kind} ({len(group)}):", "header"))
        for i in group:
            out("    " + _c(f"#{i['node_id']}", "id") + "  " + _c(i["detail"], "meta"))
