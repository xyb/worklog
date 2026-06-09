---
name: worklog-cli
description: Use the `wl` command to read/write a SQLite-backed execution-system DB (XDG default `~/.local/share/worklog/worklog.db`; override per-invocation with `wl --db PATH ...` or globally with `$WORKLOG_DB`) — record/query plans + projects + tasks + logs + time hierarchy; a structured replacement for markdown worklogs. Use when the user wants to add a task/project/day entry, append a log to a task, mark done/defer, list a tree/a day's work/active projects, query time-range progress for a weekly report, bulk-import a day of data, or focus a node's parents/children. Trigger words: wl, worklog-cli, add worklog, log progress, list tree, show day, weekly report input, bulk import tasks, apply diff.
---

# worklog-cli (wl)

SQLite execution-system tool. A single `node` table carries lifetime/year/quarter/month/week/day/area/project/task/meetlog/habit; `parent_id` self-reference builds the tree. CLI mimics `todo.sh`. Global command name `wl` (installed via `pip install worklog`, or `~/bin/wl` → `<repo>/.venv/bin/wl` for editable dev installs).

**Full design conventions in repo `DESIGN.md`** (required reading before adding commands or changing formats). This skill covers how AI uses `wl`. Detail beyond the everyday path lives in `references/` (loaded on demand — see § More detail at the bottom).

## ⚠️ Check for duplicates before adding a task/project (hard rule)

Before `wl add`-ing any task or project, **first check whether a related entry already exists** — if so, reuse it (`wl sched` it onto the new day) instead of creating a duplicate. **Search across time ranges**, because an entry may be scheduled at `@2026-06` (month level) / `@someday` / earlier or later, so looking only at the current month misses it:

- `wl find <keyword>` — full-text search (always run this first)
- `wl agenda <start> <end> [--someday]` — everything scheduled in a date range across all granularities (day/week/month pins), the cross-range view that catches `@2026-06` / `@someday` items a per-month tree misses
- `wl ls --parent <proj> --all` — entries already under that project

Likewise, before adding a dev todo under a project, check that project's existing entries first.

## When to use `wl` (scenario → command)

| User says | Command |
|---|---|
| **Daily three** (the daily flow) | `wl goal "deliver X today"` (read = `wl goal`) / `wl recap "end-of-day summary..."` (read = `wl recap`) / `wl tick <id> [--note "..."] [--done]` to check in. **Auto-creates today's day node** (hung under the current ISO week), no need to manually `wl add ... -k day`. `wl recap`/`wl goal` write a history-preserving `log.tag` log (latest = current); `wl day` shows "(written MM-DD HH:MM)" from the recap log's own time, and if more plain-note logs land after it, warns "⚠ N changes after recap, consider rewriting". Back-fill a past day with `wl recap --date YYYY-MM-DD "..."` (`goal --date` is not available — recap only) |
| Add a task / project / habit | `wl add "..." -k task -p A -t work,P0 --parent N` |
| Add a task with scheduled time (precise or fuzzy) | `wl add "..." --scheduled 2026-06-15` / `--scheduled 2026-06` / `next-week` / `next-month` / `someday` |
| Log progress on a task | `wl log <id> "..."` (backfill old logs with `--date 2026-05-06`, or use `import` with body `"2026-05-06 content"` so logs land on the original day, not today). **Logging a TODO auto-promotes it to DOING** ("I logged → I'm working on it"); add **`--keep-status`** to just record a note without changing status (e.g. a side observation on a not-yet-started task) |
| Record a number / measurement / check-in | `wl metric add <id> glucose 5.4 --unit mmol/L` / `wl metric ls <id>` / `wl metric edit #M7` / `wl metric rm #M7`; inline: `wl log <id> "..." --metric 'pullups 8'`. Habit done-today = a `checkin` metric (`wl tick`/`wl checkin`). Detail → `references/features.md` |
| Mark done / defer (fuzzy time ok) / start clock | `wl done <id>` / `wl defer <id> next-month` (also accepts `2026-Q3` / `someday` / precise date) / `wl start <id>` `wl stop <id>` |
| Schedule task to a date / repeat (drives "planned") | `wl sched <id> 2026-06-15` (also accepts `tomorrow` / `day-after-tomorrow`) / `--clear`. `--recur` supports period start / end (`daily` / `weekly:Mon,Fri` / `monthly:-1` / `quarterly:1-15` / `yearly:-1`; `-1` = period end) — full grammar in `references/features.md`. A task scheduled to a day shows up in `wl day` as "planned · not yet logged" even with no log |
| Meta info (end-of-day summary / Top5 / today's goal) | History-preserving typed logs (`log.tag`), **a distinct store from props** — own group `wl meta set/ls/rm <node> <field>` (`field` ∈ goal/summary/overview/top5; each write appends, latest = current). Shortcuts onto it: `wl goal "..."` / `wl recap "..."` (auto-target today's day; preferred — read-back + stale-warning), and the key-routed `wl set <node> <field>` (= `wl meta set`; `wl unset <node> <field>` = `wl meta rm`). e.g. `wl meta set <week> overview "..."` / `wl meta set <month> top5 "..."`. `wl day` shows them at the top as a blockquote |
| Command aliases | `wl alias add d day` (→ `wl d` == `wl day`) / `wl alias ls` / `wl alias rm d`. Stored in `~/.config/worklog/aliases.ini`; target must be a real command, can't shadow one; takes effect next run |
| Date context (holidays / vacation / makeup days) | `wl dateinfo 2026-05-01 Labor-Day-holiday` / `wl dateinfo --import holidays.json` (`{"YYYY-MM-DD":"label"}`) / `wl dateinfo <date> --clear`. Weekday auto-computed; `wl day` header shows "date weekday · label" |
| Reproduce a day's progress (like markdown worklog) | `wl day [YYYY-MM-DD]` (log-date based: work/personal split → secondary group → task → indented logs + stats). **Default `--by plan`** (planned / unplanned). **Planned = has a `sched`/recur entry firing that day (source of truth, set via `wl sched`); everything else = unplanned.** The legacy `:planned:` tag is honored only as a transitional fallback — do NOT add it to new tasks, use `wl sched`. Switch dimensions with `--by project` / `--by priority` (P0/P1/P2) |
| List all active projects | `wl projects` |
| Tree view | `wl tree` (**default = overview: timeline expanded to today [year→quarter→month→week→today + today's tasks] + areas only listed as names, ~30 lines to avoid flooding**) / `wl tree --root <area>` (projects + tasks under that area) / `wl tree --root <week/month>` (per-day activity) / `wl tree --depth N` (fully expanded from lifetime) / `wl tree --by project/tag/direction` (switch dimension). Time nodes sorted by date; **day node expansion = tasks that have logs that day + only their logs from that day** |
| Time-range progress (weekly report input) | `wl changes --week 2026-W22` / `wl summary --week ... --by project/day` |
| Full info + timeline for a single task | `wl show <id>` |
| Focus a node's parents/children | `wl focus <id>` / `wl ancestors <id>` / `wl descendants <id>` |
| Link a vault doc | `wl link <id> "doc name"` (no `.md` suffix); remove one with `wl unlink <id> "doc name"` |
| Edit a node's tags | `wl tag <id> +work -planned` (bare word = add; no ops = list). **Edits the real tag field** — do NOT `wl set <id> tags ...` (rejected; it would create a misleading shadow prop). For bulk, `apply ~ #id / +tag` or `import add_tags/remove_tags` |
| Bind / show which task **this agent session** is on | `wl agent <id>` binds the current Claude Code session to a node (session id from `$WL_SESSION_ID` / `$CLAUDE_CODE_SESSION_ID`, fails closed if neither); `wl agent` shows it · `wl agent ls` lists all · `wl agent rm` unbinds. Stored as the `agent_session.claude` prop = **live pointer** (one session → one node, no new table). Each bind **also records history by default** (one log + an `agent_session` metric with the full sid; `--no-record` opts out) — recover past sessions a node was worked under with `wl metric ls <id> --tag agent_session --all`. A status line + a `UserPromptSubmit` hook can surface the live binding — the scripts ship with this skill under `integrations/` (jq-free; use `wl agent context [--hook]`), and **`references/setup.md` has the check-&-install steps** the assistant runs on request (`wl help agent` is the human walkthrough) |
| List log stream | `wl logs` (**default: last 7 days only**, to avoid flooding); `--since/--until/--date` for explicit range; `--group day [--by project/priority/plan]` for daily replay |

Each command's `wl <cmd> --help` is the per-command quick reference; full grammar for the
compound/batch params, clock, `wl metric`, recurrence, and `wl ls` queries → `references/features.md`.

## ⭐ Brief / token-saving mode (REQUIRED for AI usage)

Every command supports top-level **`-q` / `--brief`** — skips log body / timeline / detail
sections, keeps structured key info. **AI should default to `-q`** when fetching output (big
savings: `wl -q day` −47%, `wl -q summary --week` −89%, `wl -q show` −68%); drop to a full
`wl show <id>` only for detail on a specific node. Typical pattern:

```fish
wl -q day                          # today's tasks: todo / in-progress / done
wl -q summary --week 2026-W22      # weekly per-project done count (weekly-report skeleton)
wl summary --week 2026-W22 --top 5 --projects-only  # Top 5 priorities at a glance
wl -q logs --since 2026-05-25 --by-task --tail 1     # last 1 log per task
wl -q show 356                     # #356 meta info, no timeline
```

Time-window flags `--since` / `--until` / `--week` / `--month` are global (parent parser);
`summary` deduplicates a multi-project task by default (`--no-dedup` to disable). Humans (TTY)
keep verbose output — `--color auto` gives AI plain text automatically.

## ⭐ Bulk loading — DO NOT loop `wl add`

When loading a day's worklog or multiple nodes, **use the bulk entry, not dozens of `wl add`
calls**: `wl import <file|->` (JSON, deep nesting) or `wl apply <file|->` (wl-diff, same shape as
`wl` output). Always `--dry-run` first. Full format, the update (`~`) safety rule, and the
typical day-handling workflow → `references/bulk.md`.

## What NOT to do

- **⚠️ Confirm an id is a `wl` node id before any mutating command** (`done`/`log`/`link`/`rm`/`reopen`/`tick`/`cancel`/`defer`/`unlog`). A bare `#NNN` is ambiguous — the harness TaskList, a Claude Code session/task, Linear, a PR/issue all have their own `#NNN` spaces that collide numerically. `wl show <id>` first and check the title is the node you mean; using another space's id mutates the wrong node and corrupts real data.
- **No silent bulk delete / modification** — confirm with the user via `wl show <id>` before destructive changes
- **Don't bypass `wl` and `sqlite3` the DB directly for writes** — the DB (default `~/.local/share/worklog/worklog.db`, or wherever `$WORKLOG_DB` / `--db PATH` points) is the source of truth; schema lives in `DESIGN.md`
- **Don't run `wl reset`** (drops the DB) unless explicitly requested
- Before any bulk write, **`--dry-run` first**, especially for update/delete
- When adding a command to `src/worklog/cli.py`: change implementation + tests + completion + `DESIGN.md` (if convention touched) + this `SKILL.md` (if usage touched) together; `make ship` (push only if tests pass)

## Where a fact goes: prop vs metric vs log

Sort by *"will I filter / group / stat over it across nodes?"* + cardinality (full rule in
`DESIGN.md` §2 / `wl help prop` / `wl help metric`):

- **`prop`** — a single-value **query dimension** you slice the tree by (`owner`, `project`, the
  one identifying ref a task maps to, the `release` it shipped in → "which tasks shipped in
  v0.7.0" is one prop query). Scarce, high-value; **don't flood it with process noise**.
- **`log`** — the human **process record** (a dev task's many intermediate commits go in the body).
- **`metric`** — those process records made structured/queryable per node: `wl metric add <id>
  commit <hash>` (or `--metric 'commit <hash>'`), then `wl metric ls <id> --tag commit`. Many-per-
  node, append-only — *not* a cross-node filter dimension.
- Worked example (commit / PR / release ↔ task): **many** commits → log (+ a `commit` metric for
  structure); a **single** identifying PR/commit, or the **release** → prop.

## Vault link (knowledge ⇄ execution decoupling)

vault markdown = human-written archive; the `wl` DB = machine-queryable execution truth. `wl link
<id> "Dev tooling"` associates a task with a vault doc (stores the doc name, no `.md` suffix,
matching `[[...]]`). Prefer `wl` first for new tasks; attach a vault link if a vault doc is involved.

## More detail (load on demand)

- **`references/features.md`** — full command grammar: compound & batch params, `unlog`/`relog`,
  clock backfill, `wl metric`, `wl checkin`, recurrence rules, `wl ls` queries, timeline-tail
  defaults, query-precision principle.
- **`references/bulk.md`** — `wl import` / `wl apply` format, the update (`~`) safety rule, the
  typical AI day-handling workflow.
- **`references/setup.md`** — install `wl`, install this skill (whole-dir symlink), shell
  completion, colors / themes, `wl agent` status-line + hook setup.
