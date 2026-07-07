# Setup — install, shell completion, colors

Setup-time reference for `wl`. Read this when installing on a new machine or wiring up shell
completion / colors — not needed for day-to-day use.

## Install `wl` (new machine)

```fish
git clone <your-git-host>:<user>/worklog-cli.git ~/projects/worklog-cli
cd ~/projects/worklog-cli && make setup    # venv + ~/bin/wl
wl init
# see "Shell completion" below for the init-load line for your shell
```

## Install the skill (for the AI assistant)

The skill is **multi-file** — `SKILL.md` (always loaded when the skill triggers) plus
`references/` (loaded on demand). Source of truth is the repo at `skills/worklog-cli/`. Install
it by **symlinking the whole skill directory** into the assistant's skills dir, so `references/`
come along and SKILL.md's "More detail" pointers resolve:

```fish
ln -sfn ~/projects/worklog-cli/skills/worklog-cli ~/.claude/skills/worklog-cli
```

- `-n` so re-running doesn't nest a new link *inside* an already-linked dir.
- **Symlink the directory, not just `SKILL.md`** — a file-only symlink leaves `references/`
  missing and the § More-detail pointers dangling.
- Edit the skill in the repo; the symlink means the assistant always sees the live version (no
  copy to keep in sync). The `ai-skills` backup repo is only a mirror, not the source.

## Shell completion

```fish
# fish: add to ~/.config/fish/config.fish
wl print-completion fish | source
```

```bash
# bash: add to ~/.bashrc
eval "$(wl print-completion bash)"
```

```zsh
# zsh: add to ~/.zshrc
eval "$(wl print-completion zsh)"
```

Same loading model as starship / direnv / zoxide — new shells pick up changes to
`src/worklog/cli.py` automatically. Details in `DESIGN.md` §34.

**User aliases**:

```ini
# ~/.config/worklog/aliases.ini (optional)
[aliases]
d = day
c = checkin
ll = ls
w = day -t work       # a target may carry args
p = day -t personal
```

Cross-shell consistent — `wl d` resolves to `wl day` in fish/bash/zsh. A target may carry
arguments (`w = day -t work` → `wl w` == `wl day -t work`); args you type after the alias are
appended (`wl w 2026-06-08` → `wl day -t work 2026-06-08`). Edit the ini (or use `wl alias add w
"day -t work"`) and open a new shell to apply.

## Highlighting / colors

In a terminal, `wl` is colored by default (`rich`): status green/yellow, priority A red, search
hits (including title matches) background-highlighted. Global switches (before any subcommand):
`wl --color {auto,always,never}`, `wl --theme {auto,dark,light,mono}`; also reads
`$WORKLOG_COLOR` / `$WORKLOG_THEME` / `$NO_COLOR`. Theme default **auto**: probes terminal
background and picks dark/light (falls back to dark if undetectable); `wl themes` lists and
previews. `--color auto` (default) only colors a TTY — **AI capturing stdout gets plain text
automatically**, no need to explicitly disable. To pipe colored output (e.g. `| less -R`), use
`--color always`. Details in `DESIGN.md` §19.

**Output width** — by default output fills the terminal. Cap it with `wl --width help` (the
`--help` width, 100 cols) / `--width N` / `$WORKLOG_WIDTH=N`, e.g. to keep long lines readable on
a very wide terminal; `--width full` is the default (no cap).

**Long titles** — a node title too wide for the line wraps onto multiple lines by default,
continuation lines hang-indented under the title so the tree/list stays aligned. Use `wl --title
clip` / `$WORKLOG_TITLE=clip` to keep one line truncated with `…` instead; `--title wrap` is the
default.

## wl agent — status line + context hook (check & install)

`wl agent <id>` binds the current AI session to a task. Optional integrations make that binding
**visible** (status bar `📌WL#<id>`) and **injected** into the agent's context. Supported runtimes
live in `src/worklog/commands/agent_runtime.py` (`AgentRuntime` registry; `wl help agent`); each
has its own hook script under `integrations/`. All depend only on `wl` (no jq, no sqlite CLI):

- `integrations/wl-session-context.sh` — Claude Code `UserPromptSubmit` hook. Reads the binding
  via `wl agent context --hook claude`, caches it per session, injects only when it changes.
- `integrations/wl-cursor-session-start.sh` — Cursor `sessionStart` hook. Freezes
  `$WL_SESSION_ID` / `$WL_AGENT=cursor` and injects any existing binding (`--hook cursor`).
- `integrations/statusline-wl.sh` — a runtime-agnostic status-line segment printing ` 📌WL#<id>`.

**When the user asks to set this up (or asks why the binding isn't showing), check then install
the hook for their runtime:**

### Claude Code
1. **Check**: is `~/.claude/hooks/wl-session-context.sh` present AND registered under
   `hooks.UserPromptSubmit` in `~/.claude/settings.json`? (`grep -q wl-session-context
   ~/.claude/settings.json`.)
2. **Install** if missing:
   ```fish
   mkdir -p ~/.claude/hooks
   cp <skill-dir>/integrations/wl-session-context.sh ~/.claude/hooks/ && chmod +x ~/.claude/hooks/wl-session-context.sh
   ```
   then add a `hooks.UserPromptSubmit` entry running it to `~/.claude/settings.json` (a `command`
   hook with `"command": "$HOME/.claude/hooks/wl-session-context.sh"`). Edit the JSON with the
   user's confirmation.

### Cursor
Inside a Cursor agent shell `wl agent <id>` already works with no env setup (it reads
`$CURSOR_CONVERSATION_ID` / `$CURSOR_AGENT`). The hook only makes an *existing* binding known to a
fresh session.
1. **Check**: is `~/.cursor/hooks/wl-cursor-session-start.sh` present AND registered under
   `hooks.sessionStart` in `~/.cursor/hooks.json`?
2. **Install** if missing:
   ```fish
   mkdir -p ~/.cursor/hooks
   cp <skill-dir>/integrations/wl-cursor-session-start.sh ~/.cursor/hooks/ && chmod +x ~/.cursor/hooks/wl-cursor-session-start.sh
   ```
   then add to `~/.cursor/hooks.json`:
   ```json
   { "version": 1, "hooks": { "sessionStart": [{ "command": "./hooks/wl-cursor-session-start.sh" }] } }
   ```

### Status line (any runtime)
If the user wants the `📌WL#<id>` segment, copy `integrations/statusline-wl.sh` to their config
dir and have their status-line command pipe its stdin through it (it appends the segment). Works
for any runtime whose status-line JSON carries a `session_id`. If `wl` isn't on the
hook/status-line PATH, set `WL_BIN` to the wl binary.

`<skill-dir>` is wherever this skill is installed (e.g. `~/.claude/skills/worklog-cli/`). The
human-facing walkthrough + JSON snippets are in the shipped help topic too: `wl help agent`.
