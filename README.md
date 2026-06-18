<sub><b>🌐 English</b> · <a href="README.zh.md">中文</a></sub>

# worklog

[![PyPI version](https://img.shields.io/pypi/v/pyworklog.svg)](https://pypi.org/project/pyworklog/)
[![Python versions](https://img.shields.io/pypi/pyversions/pyworklog.svg)](https://pypi.org/project/pyworklog/)
[![Test](https://github.com/xyb/worklog/actions/workflows/test.yml/badge.svg)](https://github.com/xyb/worklog/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/xyb/worklog/branch/main/graph/badge.svg)](https://codecov.io/gh/xyb/worklog)
[![License: MIT](https://img.shields.io/pypi/l/pyworklog.svg)](https://github.com/xyb/worklog/blob/main/LICENSE)

> **Changelog**: see [CHANGELOG.md](CHANGELOG.md) for a curated highlight reel of every release.

**worklog (`wl`) is an AI-first, local-first execution-system CLI** — a structured replacement for a Markdown worklog. It models the full execution hierarchy in a single SQLite `node` table — lifetime / decade / year / quarter / month / week / day / project / task / habit / signal / meetlog — all sharing one id space, tree-linked via `parent_id` self-reference, behind a `todo.sh`-style command surface.

## Why worklog?

The origin: Markdown worklogs an AI kept for me grew ~50× and stopped scaling — concurrent writes clobbered, wikilinks drifted, summaries meant re-reading huge files. So I moved the structured part into a database built for an AI to drive.

**AI-first** — the AI is the real user; you just glance at the terminal to confirm:

- One-line commands, no interactive prompts — reliable to call from a shell.
- `-q` brief mode + width-clipped rows → token-cheap output.
- Plain-text output an AI reads directly; bundled [Claude Code skill](skills/worklog-cli/SKILL.md).

**Local-first** — one SQLite file, transparent schema, no daemon / GUI / lock-in:

- You and the AI read and write the same file — one source of truth.
- Concurrent-write-safe → parallel AI sessions don't clobber (Markdown can't).
- Pairs with your vault via `wl link` — structured execution in `wl`, long-form notes in Obsidian.

**Design conventions: see [DESIGN.md](DESIGN.md)** — required reading before adding commands, to keep everything consistent.
**AI collaboration: see [skills/worklog-cli/SKILL.md](skills/worklog-cli/SKILL.md)** — Claude Code skill (when / how to use `wl`, plus bulk import / apply).
Background: built after surveying 12 candidate products (Logseq / Tana / TaskWarrior / org-mode / Anytype / Capacities / Linear etc.) and finding no off-the-shelf tool that fits all three dimensions (time hierarchy, project hierarchy, vault wikilink) without compromise.

## Features

- **One `node` table for everything** — time line (year → day) + project line (area → task) + habit / meetlog, tree-linked.
- **Logs** — timestamped progress on any node, history-preserving.
- **Metrics** — structured datapoints (reps, glucose, check-ins) that trend.
- **Habits & recurrence** — check-ins + `--recur` (daily / weekly / monthly / …).
- **Scheduling** — sched a task to a day; fuzzy words (`tomorrow`, `next-week`, `+3w`).
- **Status machine** — TODO / DOING / LATER / WAIT / DONE / DEFERRED / CANCELED.
- **Day / week / month views** — `wl day` / `tree` / `summary` rebuild the picture + stats.
- **Full-text search** — `wl find`, hits highlighted.
- **Semantic + hybrid search** — `wl query`: rank by meaning (embeddings) fused with keyword match (RRF), so paraphrases *and* exact names both surface; via any OpenAI-compatible embedding server. Vectors live in LanceDB (optional `semantic` extra) or auto-fall-back to a pure-Python SQLite store where no LanceDB wheel exists — see [Semantic search backends](#semantic-search-backends).
- **Task relations** — `wl relation` links tasks (`split-from` / `split-into` / `related`), distinct from the parent/child tree.
- **Machine-readable output** — `-o json` on `show` / `ls` / `logs` / `day` / `tree` / `summary` / `projects` for scripts and AI.
- **Agent session binding** — `wl agent` ties an AI session to a task (status line / hooks can surface it).
- **Vault link** — `wl link` to Obsidian docs (`[[wikilink]]`).
- **Bulk import / apply** — load a whole day in one JSON or wl-diff.
- **AI-friendly output** — `-q` brief, plain-text on capture, colors on a TTY, shell completion.

## Install

### From PyPI (recommended for users)

Requires Python ≥ 3.9 (tested on 3.9–3.14).

```fish
pipx install pyworklog          # or: uv tool install pyworklog
wl init
```

The PyPI distribution name is `pyworklog` (the short names `worklog` and `worklog-cli` were already taken, and hyphenated names like `worklog-py` were avoided); the command stays `wl` and the import name stays `worklog`.

### From source (recommended for development)

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv` or `pipx install uv`).

```fish
git clone https://github.com/xyb/worklog.git ~/projects/worklog
cd ~/projects/worklog
make setup       # uv sync + install ~/bin/wl wrapper

# shell completion (init-load mode, pick your shell)
# fish: add to ~/.config/fish/config.fish
echo 'wl print-completion fish | source' >> ~/.config/fish/config.fish
# bash: add to ~/.bashrc       →  eval "$(wl print-completion bash)"
# zsh:  add to ~/.zshrc        →  eval "$(wl print-completion zsh)"

wl init
```

Behind the scenes `make setup` runs `uv sync` to create `.venv/` from `pyproject.toml` + `uv.lock`, then installs a `~/bin/wl` wrapper pointing into that `.venv`.

DB location follows the [XDG Base Directory spec](https://specifications.freedesktop.org/basedir-spec/): default `$XDG_DATA_HOME/worklog/worklog.db` (i.e. `~/.local/share/worklog/worklog.db`). Override per-invocation with `wl --db PATH ...`, or globally with the `$WORKLOG_DB` env var. User config (aliases.ini) lives at `$XDG_CONFIG_HOME/worklog/aliases.ini` (default `~/.config/worklog/aliases.ini`).

## Quickstart

The first 30 seconds — add a task, log progress, close it, replay the day:

```fish
wl init                                   # create the DB (once)
wl add "write the README" -p A            # → prints the new id, e.g. #1
wl log 1 "drafted the Features section"   # append progress
wl done 1                                 # close it
wl day                                    # today's work, regrouped + stats
```

## Commands

The fuller surface — every command also has `wl <cmd> --help`, and `wl help` browses topic docs:

```fish
wl add "research X" -p A -t work,P0 --proj dev_tooling --parent 42
wl add "Dev tooling" --para project -p A --parent 4   # project hangs under month
wl log 42 "reviewed A's material, found..."
wl done 42
wl defer 42 2026-06-01
wl start 42 ; wl stop 42                            # CLOCK in/out
wl link 42 "Dev tooling"                       # vault wikilink
wl set 42 owner xyb                               # custom prop
wl show 42                                          # detail + log + tags + links
wl ls                                               # default: list open items
wl ls --para project --tag work,P0
wl tree                                             # full tree
wl tree --prop type.date=year --depth 3
wl logs --since 2026-05-18                          # cross-task log range query
wl find needle                                      # full-text search, matches highlighted + indented
```

### Highlighting / colors

Terminal output is colored by default (via `rich`); global flags go before the subcommand:

```fish
wl themes                            # list dark/light/mono themes + previews + mark current
wl --color always tree | less -R     # force color (preserves ANSI through pipes)
wl --color never ls                  # no color (plain text)
wl --theme light summary --week ...  # manually pick the light-background theme
```

- `--color {auto,always,never}`, default `auto`: colors on if TTY + rich available; pipes / redirects / no-rich downgrade to plain text
- `--theme {auto,dark,light,mono}`, default **auto**: probes terminal background and picks dark (dark bg) / light (light bg); falls back to dark when undetectable. dark/light/mono can also be picked manually.
  - Background probe: first checks `$COLORFGBG`, then sends an OSC 11 query (needs an interactive terminal, short timeout, gracefully falls back if unsupported)
- Search hits (including matches in titles) highlight: styled mode uses background color; plain text wraps with `*…*`
- env fallback: `$WORKLOG_COLOR` / `$WORKLOG_THEME` / `$NO_COLOR`
- `rich` is an optional dependency — the tool still runs without it (plain text only)

## Semantic search backends

`wl query` / `wl reindex` embed text via any OpenAI-compatible server (stdlib HTTP, no
dependency) and store the vectors in a sidecar index. Two interchangeable backends, chosen
automatically — both segmentation and storage degrade gracefully, so semantic search works on
**every** supported Python with **zero** required extras:

| Component | Best (with `semantic` extra) | Fallback (no extra / no wheel) |
| --- | --- | --- |
| Vector store | **LanceDB** — memory-mapped, opens in ~1ms regardless of size | **SQLite** — pure-Python cosine, linear scan; fine at worklog scale, slower at very large stores |
| Word segmentation | **jieba** — multi-granularity CJK recall | **`\w+`** — a CJK run stays one token (coarser Chinese recall) |

```fish
pip install 'pyworklog[semantic]'   # the fast path: LanceDB + jieba
```

**Best experience:** install the `semantic` extra on any **Python 3.9–3.14** on **Linux** or
**Apple-Silicon macOS** — LanceDB ships forward-compatible (abi3) wheels there for all of those
versions, and jieba is pure-Python so it installs anywhere.

**When the fallback kicks in:** LanceDB ships no source distribution, so on platforms with no
prebuilt wheel — **Intel macOS**, **musl/Alpine**, **\*BSD**, 32-bit — `wl query`/`reindex`
automatically use the SQLite store instead (same results, just slower); `wl reindex` prints a
one-line note when it does. Nothing else changes, and the core CLI never needs either extra.

## Schema

Six tables; everything is a `node`.

```
node (id, parent_id→node, title, kind, status, priority,
      created_at, scheduled_at, deadline_at, closed_at, body)
tag  (node_id→node, tag)                    # many-to-many
log  (id, node_id→node, logged_at, body)    # one node, many log entries
prop (node_id→node, key, value)             # UDA
link (node_id→node, vault_doc)              # vault wikilink
v_node_path                                  # recursive CTE view, tree path
```

The `kind` field lets one table hold any execution-system entity. Cascade delete propagates to `tag/log/prop/link`; `parent_id` uses `ON DELETE SET NULL` so deleting a parent doesn't orphan-kill children.

## Status states

`TODO / DOING / LATER / WAIT / DONE / DEFERRED / CANCELED` — superset of the markdown `[ ]/[x]/[/]/[>]` four-state set, adds `LATER` / `WAIT` distinction (deferred to future vs. waiting on someone).

## Contributing

Development setup, the TDD/DRY conventions, local Makefile overrides, and the release process all live in [CONTRIBUTING.md](CONTRIBUTING.md). For agent-facing operating rules see [AGENTS.md](AGENTS.md); for canonical design conventions see [DESIGN.md](DESIGN.md).
