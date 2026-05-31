# Contributing to worklog

Thanks for considering a contribution! This guide covers the local setup, the development loop, and the two coding principles every change is held to: **TDD** and **DRY**.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — install via `brew install uv` or `pipx install uv`.
- Python ≥ 3.11. `uv` handles the interpreter — no system Python juggling.

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

# single test by node id
uv run pytest tests/test_wl.py::TestApply::test_apply_delete_cascades_subtree -v --no-cov

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

`DESIGN.md` is canonical for every shared convention — command style, state machine, marker symbols, time-window flags, render pipeline, schema, `import` / `apply` formats, theme keys, scheduled-time resolution, planned/unplanned derivation. Read the relevant section before adding a command or changing a format. If your change touches a convention, the same commit must update **DESIGN.md + `src/worklog/cli.py` + `tests/test_wl.py` + completion strings together** — drift between them is the failure mode this project guards against.

`AGENTS.md` is the operating guide for AI coding agents (Claude Code, Cursor, Aider). Skim it once even if you code by hand; it concentrates the hard-rules into one page.

## TDD: red → green → refactor

Every change touching behavior follows the [Red-Green-Refactor cycle](https://brennanbrown.github.io/notes/programming/python-tdd/):

1. **Red** — add a failing test in `tests/test_wl.py` first. Run it; confirm it fails for the reason you expect (not an import error). A test that never fails is not a test.
2. **Green** — write the minimum production code to make that test pass. Resist the urge to also fix neighboring code; one cycle, one concern.
3. **Refactor** — clean up. Extract helpers, remove duplication, rename. Tests still green at every step.

Bug fixes follow the same path: write a regression test that reproduces the bug first, then fix.

Tests don't ship without docstrings explaining what behavior they pin down (see existing `TestXDGPaths`, `TestConfig` for the style). One `Test<Command>` class per command. Cover happy path + boundaries (missing id, empty DB, illegal flag values) — the boundaries are where DESIGN drift surfaces first.

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

Version has a **single** in-repo source: `version = "X.Y.Z"` in `pyproject.toml`. `__version__` in `src/worklog/cli.py` reads it via `importlib.metadata.version("worklog")` — never edit it by hand. The git tag `vX.Y.Z` must match the pyproject version; the release workflow enforces that.

The `Release` workflow (`.github/workflows/release.yml`) is triggered by pushing a `v*` tag. It verifies tag == pyproject version, re-runs the test suite, then calls `softprops/action-gh-release@v2` to publish a GitHub Release with auto-generated notes (commits since the previous tag).

To cut a release:

```fish
# 1. bump version in pyproject.toml only
# 2. refresh the lock entry so uv.lock pins the new version
uv lock --upgrade-package worklog
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
- Run `make test` locally before pushing. CI runs the same parallel pytest + 95% gate across ubuntu + macos × Python 3.11/3.12/3.13.
- DESIGN.md / AGENTS.md drift is the most common review block — when in doubt, update them in the same PR.
