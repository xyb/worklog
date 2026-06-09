# Contributing to worklog

Thanks for considering a contribution! This guide covers the local setup, the development loop, and the two coding principles every change is held to: **TDD** and **DRY**.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — install via `brew install uv` or `pipx install uv`.
- Python ≥ 3.9 (tested on 3.9–3.14). `uv` handles the interpreter — no system Python juggling.

## Quick start

```fish
git clone https://github.com/xyb/worklog.git ~/projects/worklog
cd ~/projects/worklog
make setup       # uv sync (creates .venv) + installs ~/bin/wl wrapper
make test        # run the full suite (parallel, 95% cov gate)
```

`make setup` does two things: `uv sync --all-groups` materializes `.venv/` from `pyproject.toml` + `uv.lock`, and `make install` writes `~/bin/wl` pointing into that venv. From there, every command (`uv run pytest …`, `make test`, etc.) uses the same locked deps.

## Day-to-day development loop

```fish
make test          # parallel, cov, 95% gate  — pre-commit baseline
make test-fast     # parallel, no cov, no gate — quick iteration
make test-v        # sequential, verbose, no cov — debug a single failure

# single test by node id (tests are split by area: test_add.py / test_agent.py / …)
uv run pytest tests/test_add.py::TestAdd::test_add_task -v --no-cov

# coverage gate already lives in pytest.ini (--cov-fail-under=95); `make cov`
# just re-runs `make test` with term-missing output for inspection.
make cov
```

Run `wl` against a throwaway DB without touching `~/.local/share/worklog/worklog.db`:

```fish
wl --db /tmp/scratch.db init
wl --db /tmp/scratch.db add "experiment"
```

`wl config` shows which DB is currently resolved (helpful when an env / flag mix is confusing).

## Architecture

`DESIGN.md` is canonical for every shared convention — command style, state machine, marker symbols, time-window flags, render pipeline, schema, `import` / `apply` formats, theme keys, scheduled-time resolution, planned/unplanned derivation. Read the relevant section before adding a command or changing a format. If your change touches a convention, the same commit must update **DESIGN.md + `src/worklog/cli.py` + `tests/` + completion strings together** — drift between them is the failure mode this project guards against.

`AGENTS.md` is the operating guide for AI coding agents (Claude Code, Cursor, Aider). Skim it once even if you code by hand; it concentrates the hard-rules into one page.

## Writing `wl help` topics

`wl help <topic>` is an info-style browser over Markdown docs in `src/worklog/help/<lang>/<topic>.md` (architecture: DESIGN §25). `en` is the source language and the per-topic fallback; other languages mirror the same topic ids. To add or edit a topic, just add/edit a `.md` file — the loader, the index, shell completion, and each command's `--help` pointer pick it up automatically.

**File format** — minimal frontmatter between `---` fences, then a Markdown body:

```
---
title: tag — labels on a node          # "<name> — <one-line description>"; the index shows the part after the dash
category: command                       # one of: guide | concept | command | param
see_also: node, day, ls, add            # comma/space-separated topic ids
---
<body>
```

- The **topic id is the filename stem** (`tag.md` → `wl help tag`). For a command topic, name it after the command so its `--help` auto-gains a `More: wl help <cmd>` pointer (commands without a same-named topic fall back to a family topic via `_HELP_FAMILY` in `cli.py`).
- Every `see_also` id **must resolve to a real topic** — a test (`test_all_see_also_targets_resolve`) fails on a dangling link, since a "See also" is meant to be runnable as `wl help <x>`.

**Supported Markdown is a small, fixed subset** (rendered by a dependency-free renderer in `commands/help.py` — no Markdown/`rich.markdown` engine):

| Syntax | Renders as |
|---|---|
| `# Heading` … `###### ` | a styled heading (the `#`s are stripped) |
| ` ```fence ` … ` ``` ` | a code block (dimmed; the fence lines are dropped; no inline parsing inside) |
| `**bold**` | bold |
| `*italic*` | italic |
| `` `code` `` | inline code (cyan) |
| `[text](url)` | underlined text + the url |
| `http://…` / `https://…` (bare) | underlined link |

Everything else is printed verbatim, so **lay the body out as readable plain text** (it must read fine with color off, where the markers above are simply stripped). Notes:

- **`_italic_` is intentionally NOT supported** — bare underscores are too common in identifiers (`node_id`, `closed_at`, `$WORKLOG_LANG`). Use `*italic*`.
- Literal brackets (`[ ]`, `[x]`, `[#A]`, `#L42`) are safe — the renderer escapes them; don't avoid them.
- Preview both ways before committing: `wl help <topic>` and `wl help <topic> --color always`.

**The same subset also styles `--help`** — `argparse` epilogs and `description=` strings are colorized by `colorize_help` (in `commands/help.py`) into the identical scheme, so you may use `` `code` `` / `**bold**` / `*italic*` / `[text](url)` there too. Two rules:

- **Markdown only in `epilog=` / `description=`** (which argparse prints raw/un-wrapped). **Never in a per-command `help=` one-line summary** — argparse line-wraps those, which splits a Markdown span across lines (the marker then leaks instead of styling). Keep `help=` plain prose; `wl <command>` references in it still get colored by the heuristic.
- A `wl <command>` reference colors itself (the heuristic recognizes real subcommand names) — reserve backticks for the cases the heuristic can't catch: bare command names that collide with English (`` `set` `` / `` `log` `` / `` `show` ``), key=value bits, flags-in-prose.

## TDD: red → green → refactor

Every change touching behavior follows the [Red-Green-Refactor cycle](https://brennanbrown.github.io/notes/programming/python-tdd/):

1. **Red** — add a failing test in the matching `tests/test_<area>.py` first. Run it; confirm it fails for the reason you expect (not an import error). A test that never fails is not a test.
2. **Green** — write the minimum production code to make that test pass. Resist the urge to also fix neighboring code; one cycle, one concern.
3. **Refactor** — clean up. Extract helpers, remove duplication, rename. Tests still green at every step.

Bug fixes follow the same path: write a regression test that reproduces the bug first, then fix.

Tests need a docstring saying what behavior they pin down. One `Test<Command>` class per command. Cover happy path + boundaries (missing id, empty DB, illegal flag values) — boundaries are where DESIGN drift surfaces first.

## DRY: there is exactly one of each

The codebase has a small set of single-source helpers; new code must reuse them, never re-implement. The full list lives in DESIGN §12, but the load-bearing ones:

- `_status_marker(status)` — status → `[ ]/[x]/[/]/...` marker. Never hard-code the marker elsewhere.
- `_node_line(con, n, ...)` — the **only** node renderer. Any place listing nodes reuses it and inherits highlighting + search emphasis for free. Hand-rolling a node line is rejected in review.
- `out()` / `_c(text, style)` — the single output pipe. Bare `print()` for renderable content is rejected; any fragment that might contain `[ ]` must go through `_c` so `rich.markup` doesn't eat it.
- `_resolve_window(args)` — time-window flag resolution. Commands must not parse `--since` / `--until` / `--week` / `--month` themselves.
- `_resolve_db_path(args)` — DB path resolution (`--db` flag > `$WORKLOG_DB` > XDG default). Mirrored by `__wl_db_path[_bash|_zsh]` helpers in the generated shell completion — keep them in sync.

Same rule for documentation: install / dev / release info lives here, not duplicated in README. Sections in README that need this content link to it instead of restating it.

## Local Makefile overrides

The Makefile loads any `local/*.mk` files at the end via `-include local/*.mk`. The `local/` directory is gitignored, so site-specific variables, private remotes, or extra targets go there without touching the shipped Makefile. Missing is fine — make won't complain.

Example `local/private.mk`:

```makefile
GITEA_REMOTE := git@your-private-host:user/worklog.git

push-gitea:        ## push current branch to private remote
	@$(GIT) -c commit.gpgsign=false push $(GITEA_REMOTE) $$($(GIT) branch --show-current)
```

After saving, `make help` lists `push-gitea` alongside the built-in targets.

## Release process

Version has a **single** in-repo source: `version = "X.Y.Z"` in `pyproject.toml`. `__version__` in `src/worklog/cli.py` reads it via `importlib.metadata.version("pyworklog")` — never edit it by hand. The git tag `vX.Y.Z` must match the pyproject version; the release workflow enforces that.

The `Release` workflow (`.github/workflows/release.yml`) is triggered by pushing a `v*` tag. It verifies tag == pyproject version, re-runs the test suite, then calls `softprops/action-gh-release@v2` to publish a GitHub Release with auto-generated notes (commits since the previous tag).

To cut a release:

```fish
# 1. bump version in pyproject.toml only
# 2. refresh the lock entry so uv.lock pins the new version
uv lock --upgrade-package pyworklog
# 3. commit + tag + push
git commit -am "chore: bump version to X.Y.Z"
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

The workflow fails the release if any of the three version anchors disagree, so a typo can't ship.

## Pull requests

- Branch from `main`. PRs target `main`.
- Each PR moves the test count up: a refactor adds no tests but keeps coverage ≥95%; a feature/fix adds the test that exercises it.
- Run `make test` locally before pushing. CI runs the same parallel pytest + 95% gate across ubuntu + macos × Python 3.9–3.14.
- DESIGN.md / AGENTS.md drift is the most common review block — when in doubt, update them in the same PR.
