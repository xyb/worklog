---
name: worklog-cli
description: Use the `wl` command to read/write a SQLite-backed execution-system DB (XDG default `~/.local/share/worklog/worklog.db`; override per-invocation with `wl --db PATH ...` or globally with `$WORKLOG_DB`) — record/query plans + projects + tasks + logs + time hierarchy; a structured replacement for markdown worklogs. Use when the user wants to add a task/project/day entry, append a log to a task, mark done/defer, list a tree/a day's work/active projects, query time-range progress for a weekly report, bulk-import a day of data, or focus a node's parents/children. Trigger words: wl, worklog-cli, add worklog, log progress, list tree, show day, weekly report input, bulk import tasks, apply diff.
---

# worklog-cli (wl)

SQLite execution-system tool. A single `node` table carries lifetime/year/quarter/month/week/day/area/project/task/meetlog/habit; `parent_id` self-reference builds the tree. CLI mimics `todo.sh`. Global command name `wl` (installed via `pip install worklog`, or `~/bin/wl` → `<repo>/.venv/bin/wl` for editable dev installs).

**Full design conventions in repo `DESIGN.md`** (18 sections; required reading before adding commands or changing formats). This skill only covers how AI uses `wl`.

## ⚠️ Check for duplicates before adding a task/project (hard rule)

Before `wl add`-ing any task or project, **first check whether a related entry already exists** — if so, reuse it (`wl sched` it onto the new day) instead of creating a duplicate. **Search across time ranges**, because an entry may be scheduled at `@2026-06` (month level) / `@someday` / earlier or later, so looking only at the current month misses it:

- `wl find <keyword>` — full-text search (always run this first)
- `wl ls --sort scheduled --all` — see everything that's scheduled
- `wl ls --parent <proj> --all` — entries already under that project (note: `wl tree --root` may miss tasks hung on a month node, see #436)

Likewise, before adding a dev todo under a project, check that project's existing entries first. If a convenient cross-range query command is missing, flag it and file a dev todo (#45 / see #434 for the planned `agenda` command).

## When to use `wl` (scenario → command)

| User says | Command |
|---|---|
| **Daily three** (the daily flow) | `wl goal "deliver X today"` (read = `wl goal`) / `wl recap "end-of-day summary..."` (read = `wl recap`) / `wl tick <id> [--note "..."] [--done]` to check in. **Auto-creates today's day node** (hung under the current ISO week), no need to manually `wl add ... -k day`. `wl recap` write **auto-stamps `summary_at`**, and `wl day` shows "(written MM-DD HH:MM)" at the top; if more non-CLOCK changes happen after the summary, `wl day` warns "⚠ N changes after summary, consider rewriting recap". Back-fill a past day with `wl recap --date YYYY-MM-DD "..."` (also `goal --date` is not available — recap only) |
| Add a task / project / habit | `wl add "..." -k task -p A -t work,P0 --parent N` |
| Add a task with scheduled time (precise or fuzzy) | `wl add "..." --scheduled 2026-06-15` / `--scheduled 2026-06` / `next-week` / `next-month` / `someday` |
| Log progress on a task | `wl log <id> "..."` (backfill old logs with `--date 2026-05-06`, or use `import` with body `"2026-05-06 content"` so logs land on the original day, not today) |
| Mark done / defer (fuzzy time ok) / start clock | `wl done <id>` / `wl defer <id> next-month` (also accepts `2026-Q3` / `someday` / precise date) / `wl start <id>` `wl stop <id>` |
| Schedule task to a date / repeat (drives "planned") | `wl sched <id> 2026-06-15` (also accepts `tomorrow` / `day-after-tomorrow`) / `--clear`. `--recur` supports period start / end: `daily` / `weekly:Mon,Fri` (also 1-7 / -1..-7) / `monthly:1` (month start) · `monthly:-1` (month end) / `quarterly:1-1` (quarter start) · `quarterly:-1` (quarter end) / `yearly:01-01` (year start) · `yearly:-1` (year end); `-1` always means period end. A task scheduled to a day shows up in `wl day` as "planned · not yet logged" even with no log |
| Meta info (end-of-day summary / Top5 / today's goal) | Stored as day/week node prop: `wl set <day_id> summary "..."` / `goal "..."` / `top5 "..."` / on a week node `overview "..."`. `wl day` shows them at the top as a blockquote. Prefer `wl recap` over `wl set summary` (auto-stamps `summary_at` + stale-warning) |
| Date context (holidays / vacation / makeup days) | `wl dateinfo 2026-05-01 Labor-Day-holiday` / `wl dateinfo --import holidays.json` (`{"YYYY-MM-DD":"label"}`) / `wl dateinfo <date> --clear`. Weekday auto-computed; `wl day` header shows "date weekday · label" |
| Reproduce a day's progress (like markdown worklog) | `wl day [YYYY-MM-DD]` (log-date based: work/personal split → secondary group → task → indented logs + stats). **Default `--by plan`** (planned / unplanned / unplanned-unmarked — anything without `planned`/`unplanned` tag is treated as unplanned); switch dimensions with `--by project` / `--by priority` (P0/P1/P2) |
| List all active projects | `wl projects` |
| Tree view | `wl tree` (**default = overview: timeline expanded to today [year→quarter→month→week→today + today's tasks] + areas only listed as names, ~30 lines to avoid flooding**) / `wl tree --root <area>` (projects + tasks under that area) / `wl tree --root <week/month>` (per-day activity) / `wl tree --depth N` (fully expanded from lifetime) / `wl tree --by project/tag/direction` (switch dimension). Time nodes sorted by date; **day node expansion = tasks that have logs that day + only their logs from that day** |
| Time-range progress (weekly report input) | `wl changes --week 2026-W22` / `wl summary --week ... --by project/day` |
| Full info + timeline for a single task | `wl show <id>` |
| Focus a node's parents/children | `wl focus <id>` / `wl ancestors <id>` / `wl descendants <id>` |
| Link a vault doc | `wl link <id> "doc name"` (no `.md` suffix) |
| List log stream | `wl logs` (**default: last 7 days only**, to avoid flooding); `--since/--until/--date` for explicit range; `--group day [--by project/priority/plan]` for daily replay |

## UX v2 (iteratively converged)

New ergonomic commands (`done/start/stop/link/wait/reopen` all accept **multiple ids**):

```fish
wl add "new task" -k task -p A --parent 7 --sched today  # add + direct-to-sched in one shot
wl done 18 19 20                                          # batch done
wl start 18 19; wl stop 18 19                             # batch clock
wl active                                                 # show tasks currently on CLOCK (with elapsed)
wl wait 18 --note "waiting on review"                     # WAIT state + auto-stops CLOCK
wl reopen 18                                              # undo DONE back to TODO
wl log 44 "breakfast" --time 11:09 --date yesterday       # precise time + short date
wl day yesterday / wl day day-before-yesterday            # date shortcuts
wl find skill --limit 5 / --all                           # search default 20 to avoid blast
```

⚠️ `--sched <day>` (precise, writes the sched table, visible in `wl day <that-day>`) vs. `--scheduled <fuzzy>` (rough hint, only sets `node.scheduled_at`). **Use `--sched` daily.** Passing both conflicts.

New: `wl active` / `wl wait` / `wl reopen`, `wl logs today/yesterday/week/recent` presets, `wl show` multi-id, `wl find --limit`, `wl --version`. All empty-string inputs (title / body / vault_doc / prop key / find query) are rejected uniformly; illegal field names (`--in bogus` / `--kind bogus`) rejected; illegal times (`--time 25:99`) rejected.

## UX v3 (compound params / log editing / time backfill / query precision)

### One-shot completion: compound params on `add` / `done` / `cancel`

```fish
# create + log + done + closed_at + link + sched, all in one (retrospective entry)
wl add "got something done" -k task -p B --log "result: PR#42 fixed 3 bugs" \
  --done --at 14:30 --link "vault doc name" --sched today

# existing task: close + log in one
wl done <id> --log "result note" --at HH:MM
wl cancel <id> --log "abandoned: priority dropped"
# -m is short for --log (matches git commit habit)
```

### log editing: `unlog` / `relog`

`wl show` / `wl logs` timelines show log id as `#L<id>`:

```fish
wl unlog #L282                       # delete log
wl unlog --node 39 --date yesterday  # delete most-recent 1 by node + date
wl relog #L282 "corrected content"   # edit body
wl relog #L282 --at 14:30            # edit time only
wl relog #L282                       # no body/--at → opens $EDITOR
# CLOCK_IN/OUT logs are immutable here (use `wl stop --at` to fix times)
```

### Time backfill: `start --at` / `stop --at` / `spent`

```fish
wl start <id> --at 09:00              # backfill CLOCK_IN
wl stop <id> --at 11:30               # backfill CLOCK_OUT (must be after IN)
wl spent <id> 90m                     # given duration, write a CLOCK pair directly
wl spent <id> 1h30m --at 14:00        # 14:00 as end, backs out 12:30 as start
```

### Multi-habit interactive check-in: `wl checkin`

```fish
wl checkin                            # default multi-select (↑↓ space enter)
wl checkin --per-item                 # alt: one-by-one y/n/note/q prompt
wl checkin --all-kinds                # not limited to habit kind
```

### Recurrence rules (`--recur`), every variant supports `-1` for "last day of period"

```fish
wl sched <id> --recur weekly:Mon,Wed,Fri    # or weekly:1,3,5 / weekly:-1=Sun
wl sched <id> --recur monthly:5,15,-1       # each month 5th / 15th / last
wl sched <id> --recur quarterly:1-15        # quarter's 1st month 15th → 1/15, 4/15, 7/15, 10/15
wl sched <id> --recur quarterly:-1          # quarter end (3/31, 6/30, 9/30, 12/31)
wl sched <id> --recur yearly:03-21          # every year on a date
wl sched <id> --recur yearly:-1             # year end 12-31
```

### `wl ls` multi-dimensional query (inspired by shell `ls -t/-S/-r`)

Default limit 20 + truncation hint; `wl ls --help` includes 10 examples:

```fish
wl ls --kind project                  # projects only
wl ls --parent 45                     # children of #45
wl ls --tag work,dev                # multi-tag AND
wl ls --unscheduled --kind task       # backlog needing schedule
wl ls --sort created -r --limit 5     # last-5 created (like ls -tr -5)
wl ls --sort updated --limit 10       # last-10 with new logs (like ls -t)
wl ls --recent 7                      # touched in last 7 days
wl ls --ids 39 41 270                 # specific ids (like ls f1 f2)
wl ls --all                           # remove limit + include DONE/CANCELED
```

### log/timeline default-tail to N (so long tasks don't blast screen)

| Command | Default tail | Disable | Full expand | Custom |
|---|---|---|---|---|
| `wl day` | 3 logs per task | `--no-logs` | `--all-logs` | `--log-tail N` |
| `wl logs --by-task` | 3 | `--no-body` | `--all-logs` | `--tail N` |
| `wl logs --id N` | all | `-q` | (default all) | `--tail N` for tail slice |
| `wl show <id>` | timeline 5 | `--no-timeline` | `--all-timelines` | `--timeline-tail N` |
| `wl tree` (day activity) | 3 per task | `--no-logs` | `--all-logs` | `--log-tail N` |

### Query-precision design principle

Core: **the command should make "goal → command → output" precise at all three layers**; don't rely on `ls --all` and eyeball-search. Anti-patterns: AI uses `wl ls` to list 100 entries to find 1 / `wl logs --id N` lists 17 to find latest. Correct: use specialty entry points (`wl find` / `wl active` / `wl day` / `wl ls --recent/--ids/--sort` / each subcommand's `--help` for examples) or add one.

## ⭐ Bulk operations (the AI's data-loading main path — DO NOT loop `wl add`)

When loading a day's worklog or multiple nodes, **use the bulk entry, not dozens of `wl add` calls**:

### `wl import <file|->` (JSON, complex bulk / deep nesting)

```fish
echo '{
  "add": [
    {"ref":"p","title":"data-viz","kind":"project","priority":"A","tags":["work","viz"],
     "children":[{"title":"login fix","kind":"task","priority":"A","status":"DONE","tags":["P0"],"logs":["root cause..."]}]},
    {"title":"digestive system","kind":"task","parent_ref":"p","tags":["viz"]}
  ],
  "update": [{"id":14,"status":"DONE","parent":6,"add_tags":["urgent"],"remove_tags":["old"]}]
}' | wl import -
```

- `children` nesting (parent id auto-propagates) + `ref`/`parent_ref` (in-batch reference)
- `--dry-run` to preview first

### `wl apply <file|->` (wl-diff, same format as `wl` output — lightweight edits for humans/AI)

```
  #6 [day] 2026-05-29 Friday      ← anchor: locate existing node as parent, don't modify
+   [x] [#A] morning-check :planned:P0:   ← add (indent = child), [x]=DONE
+     @log check key points
~ [x] #14                         ← change #14 status (single-line shorthand)
~ #20                             ← complex update: lock + field operations
  priority A
  +tag urgent
  -tag old
```

Prefixes: `+` add / `~` update / `-` delete / ` ` anchor. `--dry-run` validates + previews.

### ⚠️ Update (~) safety rule

**Only modify the fields that appear or are declared; leave everything else alone.** This is the guard against "I gave id + name and other fields got wiped."

- Single-line shorthand: `~ [x] #14` only changes status; `~ [#A] #14` only changes priority (without a marker, status is untouched); `~ #14 new name` only changes title
- Field operations: `status DONE` / `priority -` (clear) / `parent 6` (move) / `+tag` / `-tag` / `prop k=v` / `-prop k` / `+log`
- ⚠️ Each field op **must be indented** (2 spaces) under its `~ #id` lock line. A flush-left `parent 6` is a separate top-level line, not part of the update — the parser will reject it and tell you to indent.
- Illegal values (priority∉ABC, illegal status, parent missing) caught by validator — **bad data never lands**

## ⭐ Brief / token-saving mode (REQUIRED reading for AI usage)

Every command supports top-level **`-q` / `--brief`**: skips log body, timeline, detail sections, and keeps structured key info. AI should default to `-q` when fetching output; drop down to `wl show <id>` for full detail on a specific one.

| Command | `-q` behavior | Line count (measured) | Specialty params |
|---|---|---|---|
| `wl day` | task line + `(N logs)` hint, no body expansion | 34→18 (-47%) | `--no-logs` / `--log-tail N` |
| `wl summary --week` | one line per project `done N / open M`, no task expand | 329→36 (-89%) | `--projects-only` / `--top N` / `--no-dedup` |
| `wl logs --since` | only `[date] #id title`, no body | large char drop | `--no-body` / `--by-task --tail N` |
| `wl show <id>` | skip timeline, only meta info | 19→6 (-68%) | `--no-timeline` / `--timeline-tail N` |
| `wl projects` | drops "last YYYY-MM-DD" column | 18 chars/row saved | `--since DATE` only those active since |

**`summary` deduplicates by default**: when one task lives under multiple projects (parent + shared tag), it now appears once under the primary project; old behavior (one entry per project) under `--no-dedup`.

**Time-window flags are global**: `--since` / `--until` / `--week` / `--month` are hoisted to the parent parser; `changes` / `summary` / `logs` all use them with consistent naming + behavior.

Typical AI calling pattern:

```fish
wl -q day                          # today's tasks: todo / in-progress / done
wl -q summary --week 2026-W22      # weekly per-project done count (weekly-report skeleton)
wl summary --week 2026-W22 --top 5 --projects-only  # Top 5 priorities at a glance
wl -q logs --since 2026-05-25 --by-task --tail 1     # last 1 log per task
wl -q show 356                     # #356 meta info, no timeline
wl projects --since 2026-05-25     # projects actually active this week
```

Humans (TTY) keep the original verbose output; no switching needed.

## Relationship to a vault (knowledge ⇄ execution decoupling)

- vault markdown (e.g. `YYYY-MM worklog`) = human-written human-readable archive; `wl` DB = machine-written machine-queryable execution truth
- `wl link <id> "Dev tooling"` associates a task with a vault doc (stores doc name, no `.md` suffix), matching the `[[...]]` syntax used in worklog markdown
- New task handling: prefer `wl` first (structured); attach a vault link if a vault doc is involved

## Typical workflow: AI handling a day's worklog

1. Parse the day's discussion / notes → assemble a `wl apply` diff or `wl import` JSON
2. `--dry-run` first, verify correctness
3. apply/import once (replaces dozens of commands)
4. `wl day` to review the full day + stats
5. For a weekly report, run `wl changes` / `wl summary --week` to extract material

## What NOT to do

- **No silent bulk delete / modification** — confirm with the user via `wl show <id>` before destructive changes
- **Don't bypass `wl` and `sqlite3` the DB directly** — the DB (default `~/.local/share/worklog/worklog.db`, or wherever `$WORKLOG_DB` / `--db PATH` points) is the source of truth; schema lives in `DESIGN.md`
- **Don't run `wl reset`** (drops the DB) unless explicitly requested
- Before any bulk write, **`--dry-run` first**, especially for update/delete
- When adding a command to `src/worklog/cli.py`: change implementation + tests + completion + `DESIGN.md` (if convention touched) + this `SKILL.md` (if usage touched) together; `make ship` (push only if tests pass)

## Highlighting / colors

In a terminal, `wl` is colored by default (`rich`): status green/yellow, priority A red, search hits (including title matches) background-highlighted. Global switches (before any subcommand): `wl --color {auto,always,never}`, `wl --theme {auto,dark,light,mono}`; also reads `$WORKLOG_COLOR` / `$WORKLOG_THEME` / `$NO_COLOR`. Theme default **auto**: probes terminal background and picks dark/light (falls back to dark if undetectable); `wl themes` lists and previews. `--color auto` (default) only colors a TTY — **AI capturing stdout gets plain text automatically**, no need to explicitly disable. To pipe colored output (e.g. `| less -R`), use `--color always`. Details in `DESIGN.md` §19.

## Related sub-skills

By frequency and trigger scenario:

| skill | trigger / scenario | main `wl` commands |
|---|---|---|
| `worklog` | top-level entry / navigation, which sub to pick | (router) |
| `log` | single entry: got one thing done today / mark done / backfill an unplanned item | `wl add` / `wl log` / `wl done` / `wl link` |
| `worklog-daily-planning` | morning / weekend / period boundary planning | `wl day` / `wl show <month/week>` / `wl goal "..."` / `wl sched <id> today` |
| `worklog-end-of-day-planning` | end-of-day wrap-up + next-day prep | `wl day` / `wl recap "..."` / `wl set <week> overview` (Fri) / hand off to `habits` |
| `habits` | habit check-in (standalone or end-of-day) | `wl tick <id> --note "..."` |
| `worklog-day-summary` | end-of-day summary paragraph rules | `wl recap "..."` (data: `wl day`) |
| `worklog-weekly-plan` | Mon (or week start) plan this week | `wl set <week> overview` + week tasks via `wl add` + `wl sched` to each day |
| `worklog-weekly-summary` | Fri / week-end summary vault doc | `wl summary --week` + `wl set <week> overview` + `wl link <week> "<vault doc>"` |
| `meetlog-plan-sync` | meeting follow-up syncs into concrete tasks | `wl add ... -k task` + `wl sched <id> <date>` + `wl link <id> "<meetlog>"` |
| `progress-snapshot` | dashboard view (today/this week/this month) | `wl day --by plan/priority` / `wl summary --week/--month` |
| `pick-next-task` | pick next thing | `wl day --by plan` / `wl projects` / `wl show <month> top5` |

## Install (new machine)

```fish
git clone <your-git-host>:<user>/worklog-cli.git ~/projects/worklog-cli
cd ~/projects/worklog-cli && make setup    # venv + ~/bin/wl
ln -sf ~/projects/worklog-cli/skills/worklog-cli/SKILL.md ~/.claude/skills/worklog-cli/SKILL.md
wl init
# see "Shell completion" below for the init-load line for your shell
```

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

Same loading model as starship / direnv / zoxide — new shells pick up changes to `src/worklog/cli.py` automatically. Details in `DESIGN.md` §34.

**User aliases**:

```ini
# ~/.config/worklog/aliases.ini (optional)
[aliases]
d = day
c = checkin
ll = ls
```

Cross-shell consistent — `wl d` resolves to `wl day` in fish/bash/zsh; edit the ini and open a new shell to apply.
