# Contributing to worklog

Thanks for considering a contribution! This guide covers the local setup, the development loop, and the two coding principles every change is held to: **TDD** and **DRY**.

## Schema

Six tables; everything is a `node`.

```
node (id, parent_id→node, title, status, priority,
      created_at, scheduled_at, deadline_at, closed_at, body)
tag  (node_id→node, tag)                    # many-to-many
log  (id, node_id→node, logged_at, body)    # one node, many log entries
prop (node_id→node, key, value)             # UDA
link (node_id→node, vault_doc)              # vault wikilink
v_node_path                                  # recursive CTE view, tree path
```

One `node` table holds every execution-system entity; classification is the orthogonal `type.*` prop namespace (no dedicated column). Cascade delete propagates to `tag/log/prop/link`; `parent_id` uses `ON DELETE SET NULL` so deleting a parent doesn't orphan-kill children. Full schema: `src/worklog/schema.sql`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — install via `brew install uv` or `pipx install uv`.
- Python ≥ 3.9 (tested on 3.9–3.14). `uv` handles the interpreter — no system Python juggling.

## Quick start

```fish
git clone https://github.com/xyb/worklog.git ~/projects/worklog
cd ~/projects/worklog
make sync        # uv sync --all-groups → creates .venv/ for dev/test
make test        # run the full suite (parallel, 95% cov gate)
```

`make sync` materializes `.venv/` from `pyproject.toml` + `uv.lock`. From there, every command (`uv run pytest …`, `make test`, etc.) uses the same locked deps. To install `wl` for daily use, run `make install` separately — it builds a frozen snapshot from a clean committed ref, isolated from this dev `.venv`.

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

# more/fewer workers than the default (cores - 2)
WORKERS=4 make test
```

### Why the test workers are called `wl-pytest`

`make test` runs the suite through `scripts/run-tests.sh`, which launches pytest via a hardlink
to the interpreter named `wl-pytest`, and uses `cores - 2` workers. Two reasons: a screenful of
anonymous `Python` processes pinning every core is indistinguishable from a runaway program, and
leaving two cores free keeps the machine usable while the suite runs.

macOS names a process after the *filename of the binary it exec'd* (`p_comm`), not after
`argv[0]`, so `exec -a` and argv rewrites are invisible to Activity Monitor, and a *symlink*
resolves back to the original name. A hardlink is what works — which is also why `make sync`
pins the venv to a **uv-managed** Python: a framework build (python.org / Homebrew /
CommandLineTools) re-execs itself into `Python.app` and clobbers the name. On a framework venv
the script says so and falls back to running unnamed; nothing breaks.

CI is untouched by any of this — it calls `uv run pytest` directly and reads `pytest.ini`.

Run `wl` against a throwaway DB without touching `~/.local/share/worklog/worklog.db`:

```fish
wl --db /tmp/scratch.db init
wl --db /tmp/scratch.db add "experiment"
```

`wl config` shows which DB is currently resolved (helpful when an env / flag mix is confusing).

## Architecture

`DESIGN.md` is canonical for every shared convention — command style, state machine, marker symbols, time-window flags, render pipeline, schema, `import` / `apply` formats, theme keys, scheduled-time resolution, planned/unplanned derivation. Read the relevant section before adding a command or changing a format.

`AGENTS.md` is the operating guide for AI coding agents (Claude Code, Cursor, Aider). Skim it once even if you code by hand; it concentrates the hard-rules into one page.

### Keep every surface in sync (same commit)

A command/flag/behavior is described in many places; a change isn't done until they all agree. Drift between them is the failure mode this project guards against. When you touch one, update in the **same commit**:

- **`src/worklog/cli.py`** — the implementation + its argparse `help=` / `epilog` / `description`.
- **Shell completion** — a new command/flag must appear; if it takes a node id or tag, also wire `_FISH_POSITIONAL_NODE` / the dynamic helpers (it regenerates from the parser, but id/tag completion is hand-registered).
- **`wl help <topic>`** — the matching topic doc under `src/worklog/help/<lang>/`.
- **`CHANGELOG.md`** — a one-line `[Unreleased]` entry; a **breaking** change (env/path/schema migration, renamed or removed command/flag, changed contract) goes in a `### Breaking` section, listed first in the release.
- **`tests/test_<area>.py`** — the test that pins it.
- **`DESIGN.md`** (+ `DESIGN.zh.md`) if a convention changed; **`skills/worklog-cli/SKILL.md`** if everyday usage changed.

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
- `_resolve_window(args)` — time-window flag resolution (`--since` / `--until` / `--week` / `--month` / `--quarter` / `--year`). Commands must not parse these themselves; gate "was a window given" on `_window_period(args)`, not a hand-listed flag tuple.
- `_resolve_db_path(args)` — DB path resolution (`--db` flag > `$WORKLOG_DB` > XDG default). Mirrored by `__wl_db_path[_bash|_zsh]` helpers in the generated shell completion — keep them in sync.

Same rule for documentation: install / dev / release info lives here, not duplicated in README. Sections in README that need this content link to it instead of restating it.

## Naming: spell it out

Identifiers use whole words. The cost of a longer name is paid once, by the person
typing it; the cost of a short one is paid every time anybody reads the line.

| Write this | Not this |
|---|---|
| `token` | `tok` |
| `cursor` | `cur` |
| `config` | `cfg` |
| `value` | `val` |
| `result` | `res` |
| `normalize_*` | `norm_*` |

Short forms are fine when the short form is itself the word readers know: `id`, `url`,
`db`, `ok`, `args`, `kwargs`. A loop index may be `i` when its entire scope fits on one
screen.

**Two established exceptions, deliberately not in the table above: `con` and `nid`.** The
DB handle is `con` everywhere — fixed by the `cmd_<name>(args, con)` handler contract
(AGENTS.md) and by single-source helpers like `_node_line(con, …)`. A node id argument is
`nid`. `con` occurs ~2700 times across `src/` + `tests/` and `nid` ~700; renaming them one
file at a time would leave the codebase *less* consistent than it is now, which is the
opposite of the point. They change in a dedicated whole-repo sweep or not at all. In new
code, match the surrounding file — writing `connection` in a new handler is the drift, not
the fix.

**Two rules that come from real confusion in this code:**

**1. No near-identical names in one scope.** Names that differ by a letter or two force
the reader to hold both in their head at once and re-derive which is which. The
recurrence matcher used to read:

```python
# before — d is the target date's day, dd is the day the rule asks for
mm, dd = (int(x) for x in tok.split("-"))
if mm == quarter_month_idx and dd == d and 1 <= dd <= last:

# after
rule_month, rule_day = (int(part) for part in token.split("-"))
if rule_month == month_within_quarter and rule_day == day and 1 <= rule_day <= days_in_month:
```

**2. Don't name a thing after a standard it doesn't implement.** The recurrence column
was called `rrule` because the plan was to grow it into RFC 5545 RRULE. It grew a
different way instead — `quarterly:` is not an RFC frequency at all, and `weekly:-1`
means "Sunday" here but "the last one" in the standard. The name kept promising
compliance the code never delivered, which is worse than no name at all: a reader who
knows the standard is actively misled. It is now `recurrence`, which claims only what
it is. If a name describes an intention rather than the current behavior, it will be
wrong for however long the intention takes — assume that is forever.

**A sweeping rename lands as its own commit.** Renaming a symbol across many files, or
renaming a stored field, must not ride along with a behavior change — a diff that both
moves logic and renames symbols across the tree is one nobody can review properly. This
does not restrict the refactor step of the TDD loop: tidying the names inside the code
you just changed is exactly what that step is for, and it stays in that commit.

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
- Doc/completion/help/changelog drift is the most common review block — run the "Keep every surface in sync" checklist above before pushing.
