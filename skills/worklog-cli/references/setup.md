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
```

Cross-shell consistent — `wl d` resolves to `wl day` in fish/bash/zsh; edit the ini and open a
new shell to apply.

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

`wl agent <id>` binds the current AI session to a task. Two optional integrations make that
binding **visible** (status bar `📌WL#<id>`) and **injected** into the agent's context each time
it changes. Both ship with this skill under `integrations/` and depend only on `wl` (no jq, no
sqlite CLI):

- `integrations/wl-session-context.sh` — a `UserPromptSubmit` hook. Reads the binding via
  `wl agent context --hook`, caches it per session, injects only when it changes.
- `integrations/statusline-wl.sh` — a status-line segment printing ` 📌WL#<id>`.

**When the user asks to set this up (or asks why the binding isn't showing), check then install:**

1. **Check the hook**: is `~/.claude/hooks/wl-session-context.sh` present AND is it registered
   under `hooks.UserPromptSubmit` in `~/.claude/settings.json`? (`grep -q wl-session-context
   ~/.claude/settings.json`.)
2. **Install the hook** if missing:
   ```fish
   mkdir -p ~/.claude/hooks
   cp <skill-dir>/integrations/wl-session-context.sh ~/.claude/hooks/ && chmod +x ~/.claude/hooks/wl-session-context.sh
   ```
   then add a `hooks.UserPromptSubmit` entry running it to `~/.claude/settings.json` (a `command`
   hook with `"command": "$HOME/.claude/hooks/wl-session-context.sh"`). Edit the JSON with the
   user's confirmation.
3. **Status line**: if the user wants the `📌WL#<id>` segment, copy `integrations/statusline-wl.sh`
   to `~/.claude/` and have their status-line command pipe its stdin through it (it appends the
   segment). If `wl` isn't on the hook/status-line PATH, set `WL_BIN` to the wl binary.

`<skill-dir>` is wherever this skill is installed (e.g. `~/.claude/skills/worklog-cli/`). The
human-facing walkthrough + the JSON snippet are in the shipped help topic too: `wl help agent`.
