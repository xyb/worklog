<sub><b>🌐 English</b> · <a href="README.zh.md">中文</a></sub>

# worklog

[![Test](https://github.com/xyb/worklog/actions/workflows/test.yml/badge.svg)](https://github.com/xyb/worklog/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/xyb/worklog/branch/main/graph/badge.svg)](https://codecov.io/gh/xyb/worklog)

> **Changelog**: see [CHANGELOG.md](CHANGELOG.md) for a curated highlight reel of every release.

SQLite-backed worklog tool with a `todo.sh`-style CLI. Models the full execution-system hierarchy in a single `node` table — lifetime / decade / year / quarter / month / week / day / project / task / habit / signal / meetlog — all sharing one id space, tree-linked via `parent_id` self-reference.

**Design conventions: see [DESIGN.md](DESIGN.md)** — required reading before adding commands, to keep everything consistent.
**AI collaboration: see [skills/worklog-cli/SKILL.md](skills/worklog-cli/SKILL.md)** — Claude Code skill (when / how to use `wl`, plus bulk import / apply).
Background: structured worklog tool, built as a self-built alternative after surveying 12 candidate products (Logseq / Tana / TaskWarrior / org-mode / Anytype / Capacities / Linear etc.) and finding no off-the-shelf tool that fits all three dimensions (time hierarchy, project hierarchy, vault wikilink) without compromise.

## Install

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

## Commands

```fish
wl add "research X" -k task -p A -t work,P0 --proj dev_tooling --parent 42
wl add "Dev tooling" -k project -p A --parent 4   # project hangs under month
wl log 42 "reviewed A's material, found..."
wl done 42
wl defer 42 2026-06-01
wl start 42 ; wl stop 42                            # CLOCK in/out
wl link 42 "Dev tooling"                       # vault wikilink
wl set 42 owner xyb                               # custom prop
wl show 42                                          # detail + log + tags + links
wl ls                                               # default: list open items
wl ls --kind project --tag work,P0
wl tree                                             # full tree
wl tree --kind year --depth 3
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
