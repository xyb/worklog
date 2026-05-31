# AGENTS.md

Primary operating guide for AI coding agents (Claude Code, Cursor, Aider, etc.) working in this repository. Keep it under ~200 lines — anything longer stops getting read in full.

## Authoritative docs (read these first)

- **`DESIGN.md`** is canonical for every shared convention (command style, state machine, marker symbols, time-window flags, rendering, schema, import/apply formats, color theming, scheduled time, planned/unplanned derivation, etc.). Read the relevant section before adding a command or changing a format — keeping `wl.py`, the tests, the completion strings, and DESIGN in sync is the project's hardest rule.
- **`skills/worklog-cli/SKILL.md`** is the Claude Code skill (AI-facing usage guide: when to use `wl`, scenario→command table, bulk `import` / `apply` patterns, `-q` brief mode for token savings). Update this when usage patterns change.
- `README.md` is install + outline only; `tests/test_wl.py` is the de-facto contract for every command.

## Common commands

```fish
make setup          # first-time: uv sync + install ~/bin/wl wrapper into project .venv
make test           # pytest in parallel (-n auto) with the 95% cov gate from pytest.ini
make test-v         # sequential, no cov — debug noisy output
make test-fast      # parallel, no cov gate — quick dev loop
make cov            # detailed term-missing coverage report
make demo           # reset DB + populate sample tree (irreversible)
make reset          # interactive: drop current DB + re-init
make ship           # test then push (only pushes if green)
```

Single-test invocation:

```fish
uv run pytest tests/test_wl.py::TestApply::test_apply_delete_cascades_subtree -v --no-cov
```

Ad-hoc DB for scratch work without touching `~/.local/share/worklog/worklog.db`:

```fish
wl --db /tmp/scratch.db init
wl --db /tmp/scratch.db add "..."
```

`wl config` prints the resolved DB path + source (`--db flag` / `$WORKLOG_DB` / XDG default) plus all env vars — start there when debugging a path/env confusion.

## Architecture (the big picture)

**Single-file CLI.** Everything lives in `wl.py` (~5000 lines) plus `schema.sql`. No `src/` package layout, no submodules — kept this way intentionally; reconsider only if a feature truly cannot be co-located.

**One polymorphic `node` table.** The `kind` column distinguishes `lifetime / decade / year / quarter / month / week / day / area / project / task / meetlog / habit / signal`; `parent_id` self-reference builds the tree. Two parallel sub-trees hang under `lifetime`: a **responsibility line** (`area → project → task`, PARA-style) and a **time line** (`year → quarter → month → week → day`, time skeleton + metadata props). Day/week/month views are **log-driven** (`logged_at` + ancestor chain + tags), not via fixed parent-of-task — moving projects between areas does not break per-day views. Tables: `node / tag / log / prop / link / sched / date_meta` + recursive CTE view `v_node_path`.

**Path resolution.** Priority: `wl --db PATH` flag → `$WORKLOG_DB` env → `$XDG_DATA_HOME/worklog/worklog.db` (default `~/.local/share/worklog/worklog.db`). Config is `$XDG_CONFIG_HOME/worklog/aliases.ini`. `_resolve_db_path(args)` is the only resolver; `cmd_config` displays which branch fired.

**Subcommand dispatch.** `build_parser()` constructs the argparse tree (one `sub.add_parser(...)` block per command, with `help` + `description` + `epilog`). `HANDLERS = {"cmd": cmd_handler}` maps the parsed name to `cmd_<name>(args, con) -> None`. `main()` resolves user aliases (`~/.config/worklog/aliases.ini`), routes meta-commands (`print-completion`, `config`) that bypass `ensure_db()`, applies `--db` override, then calls the handler. Tests invoke `HANDLERS[cmd](args, con)` directly through the `cli` fixture to bypass `main()`.

**Rendering pipeline.** All output goes through `out()` (rich-aware) — never bare `print()` for user-visible rows. `_c(text, style)` is the single coloring entry: when `_CONSOLE` is set, it escapes content via `rich.markup.escape` (so `[x]`, `[[doc]]`, `[#A]` aren't eaten as rich markup) and wraps with `[style]…[/style]`; when console is plain, it returns the string. **Any fragment that may contain `[ ]` must go through `_c`** — direct insertion triggers `rich.MarkupError` or silent eating. `_node_line(con, n, ...)` is the **only node renderer**; reuse it everywhere listing nodes, including new commands, to inherit highlighting + search-hit emphasis for free. `_init_console` runs once in `main()`; tests run in plain mode (no TTY).

**Shell completion is generated, not hand-written.** `wl print-completion {fish,bash,zsh}` walks the argparse tree at runtime and emits a script tailored to that shell. Each shell template contains a `__wl_db_path[_bash|_zsh]` helper that queries SQLite directly for dynamic node-id / tag completions — **no Python startup**, so Tab is <50ms. When you add a subcommand or flag, completion is automatic; **but** if it takes a node id or tag, register it in `_FISH_POSITIONAL_NODE` / `_FISH_HELPERS` / `_BASH_DYN_HELPERS` so dynamic completion fires.

**Coverage gate is hard (95%).** `pytest.ini` enforces `--cov-fail-under=95`; the CI runs the same. The two pragma-no-cover regions are TTY/escape-sequence probes in `_detect_bg_is_dark` and the bare `main()` entrypoint (tests bypass it).

## Hard rules

- **DESIGN.md is the source of truth.** If a convention changes (statuses, markers, time-window flag set, project↔task linkage rule, `--by` aggregation dim, theme key set, schema), update DESIGN + `wl.py` + tests + completion strings in the same commit. Drift between them is the failure mode this project guards against.
- **Status / priority / theme key names** are enumerated in DESIGN §3/§5/§19; add to `_THEME_KEYS` when introducing a new palette entry — `test_themes_have_same_keys` catches misses.
- **No bare `print()` for renderable content**; all rows go through `out()` + `_c()`.
- **Bulk writes default to `--dry-run`.** `wl apply` and `wl import` validate first, then execute as a single transaction (`_collect_descendants` recurses for the `-` delete prefix because the FK is `ON DELETE SET NULL`, not cascade). Update flags strictly: only fields that appear in the diff are touched — see DESIGN §18.2 "anti-wipe" rule.
- **i18n layout.** README + DESIGN are bilingual (`*.zh.md`), `wl.py` strings + tests + SKILL.md are English. CJK fixtures in tests are intentional (they exercise unicode width / sort / title handling).
- **`tmp_path_home` test fixture pattern.** When a test changes `$HOME` to assert path resolution, it must also `monkeypatch.delenv("XDG_CONFIG_HOME")` + `delenv("XDG_DATA_HOME")` — CI runners preset XDG vars and otherwise leak through. See `TestUserAliasesIni._setup_aliases` for the working pattern.

## Local overrides

`local/*.mk` is gitignored and pulled in via the Makefile's `-include` line — drop private remotes, custom targets, or environment-specific variables there without touching the shipped Makefile. See README "Local Makefile overrides".

## Release

Project name is `worklog`; CLI binary is `wl`. Version lives in two places that must move together: `__version__` in `wl.py` (`wl --version` displays it) and the git tag (`v0.1.0`). After bumping, `gh release create vX.Y.Z` documents the change. CI uploads coverage to Codecov from the `ubuntu-latest × Python 3.12` job only.
