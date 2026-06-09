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

## wl agent — status line + context hook

`wl agent <id>` binds the current AI session to a task. Making that binding *visible* (status
bar `📌WL#<id>`) and *injected* into the agent's context is a one-time setup of a status-line
segment + a `UserPromptSubmit` hook — the exact snippets (sqlite reverse-lookup, hook,
settings.json registration) live in the shipped help topic: run `wl help agent`.
