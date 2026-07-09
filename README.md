<sub><b>🌐 English</b> · <a href="README.zh.md">中文</a></sub>

# worklog

[![PyPI version](https://img.shields.io/pypi/v/pyworklog.svg)](https://pypi.org/project/pyworklog/)
[![Python versions](https://img.shields.io/pypi/pyversions/pyworklog.svg)](https://pypi.org/project/pyworklog/)
[![Test](https://github.com/xyb/worklog/actions/workflows/test.yml/badge.svg)](https://github.com/xyb/worklog/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/xyb/worklog/branch/main/graph/badge.svg)](https://codecov.io/gh/xyb/worklog)
[![License: MIT](https://img.shields.io/pypi/l/pyworklog.svg)](https://github.com/xyb/worklog/blob/main/LICENSE)

A local CLI worklog and task tracker backed by SQLite. Add tasks, log what you did, plan your day — all from the terminal, all in one file.

<p align="center">
  <img src="https://github.com/user-attachments/assets/c472b531-ec09-4488-ad73-eb03ac413383" alt="wl demo — a day in the terminal: plan, log, recap" width="720">
</p>

**Why `wl`:**

- **Everything in one place.** Time (year → month → week → day) and projects (area → task) share the same tree and the same id space. No switching between apps.
- **A daily rhythm that sticks.** Set a goal in the morning, log as you go, recap at night. `wl day` replays the whole day with stats.
- **Structured and queryable.** Every item carries status, priority, tags, props, and relations. Filter with `wl ls`, track dependencies/splits with `wl relation` (`block` / `split` / `related`), search by meaning with `wl query`.
- **AI-ready out of the box.** Every command outputs plain text or `-o json`. There's a bundled [Claude Code skill](skills/worklog-cli/SKILL.md) so an AI assistant can drive it directly.
- **Local and transparent.** One SQLite file, no daemon, no account. You own the data.

## Install

```fish
pipx install pyworklog   # or: uv tool install pyworklog
wl init
```

Requires Python ≥ 3.9. Shell completion:

```
wl print-completion fish | source        # fish — add to config.fish
eval "$(wl print-completion bash)"       # bash — add to .bashrc
eval "$(wl print-completion zsh)"        # zsh  — add to .zshrc
```

## Quickstart

```fish
wl add "write the report" -p A    # add a task, priority A
wl log 1 "drafted the intro"      # log progress → status auto-flips to DOING
wl done 1                         # mark done
wl day                            # replay today: plan + logs + stats
```

## Key workflows

### Daily planning rhythm

Start the day with a goal, log as you work, close with a recap. `wl day` shows the full picture.

```fish
wl goal "ship the landing page today"     # morning intent (bare = read back)
wl log 42 "finished the hero section"     # progress note mid-day
wl recap "shipped it, copy still TBD"     # evening summary (bare = read back)
wl day                                    # the whole day: plan, logs, what changed
```

### Schedule and track status

Assign tasks to dates and move them through a status machine.

```fish
wl add "review PR" --parent 7             # add under a project
wl sched 42 tomorrow                      # plan for tomorrow; also: next-week, +3w
wl start 42                               # clock in
wl stop 42                                # clock out — elapsed recorded
wl defer 42 next-month                    # push to backlog; also: someday
wl done 42                                # close
```

Statuses: **TODO → DOING → DONE** · also **LATER** (backlog) · **WAIT** (blocked) · **CANCELED**

### Search

```fish
wl find "deploy"                           # full-text, hits highlighted
wl query "follow up with the client"       # finds related even if exact words differ
```

`wl query` uses any OpenAI-compatible embedding server. For best results (LanceDB + CJK segmentation): `pip install 'pyworklog[semantic]'`.

### AI-readable output

Every command accepts `-o json` — place it before or after the verb.

```fish
wl -o json ls --para project              # array of open projects
wl show 42 -o json | jq '.logs'           # node detail, pipe to jq
wl -o json active                         # currently running tasks
```

## All commands

```fish
# capture
wl add "task" -p A -t work                # new task, priority, tags
wl log 42 "what I did"                    # log progress
wl done 42                                # close; also: defer / cancel / wait

# plan & review
wl goal "deliver X by EOD"                # today's goal  (bare = read)
wl recap "shipped, one blocker left"      # end-of-day summary (bare = read)
wl day                                    # today: plan + logs + stats
wl sched 42 2026-07-01                    # schedule; also: tomorrow / +3w / someday

# navigate
wl show 42                                # full detail + timeline
wl ls --para project -t work              # list open work projects
wl tree                                   # whole structure, top-down
wl summary --week 2026-W25                # weekly aggregate by project

# search
wl find "keyword"                         # full-text search
wl query "follow up with the client"      # semantic + keyword hybrid
```

Every command has `wl <cmd> --help`; `wl help <topic>` browses topic docs inline.

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) · [DESIGN.md](DESIGN.md) · [CHANGELOG.md](CHANGELOG.md)
