---
name: worklog-cli
description: Use the `wl` command to read/write a SQLite-backed execution-system DB (XDG default `~/.local/share/worklog/worklog.db`; override per-invocation with `wl --db PATH ...` or globally with `$WORKLOG_DB`) — record/query plans + projects + tasks + logs + time hierarchy; a structured replacement for markdown worklogs. Use when the user wants to add a task/project/day entry, append a log to a task, mark done/defer, list a tree/a day's work/active projects, query time-range progress for a weekly report, bulk-import a day of data, or focus a node's parents/children. Trigger words: wl, worklog-cli, add worklog, log progress, list tree, show day, weekly report input, bulk import tasks, apply diff.
---

# worklog-cli (wl)

SQLite execution-system tool. A single `node` table carries lifetime/year/quarter/month/week/day/area/project/task/meetlog/habit; `parent_id` self-reference builds the tree. CLI mimics `todo.sh`. Global command name `wl` (installed via `pip install worklog`, or `~/bin/wl` → `<repo>/.venv/bin/wl` for editable dev installs).

**Full design conventions in repo `DESIGN.md`** (required reading before adding commands or changing formats). This skill covers how AI uses `wl`. Detail beyond the everyday path lives in `references/` (loaded on demand — see § More detail at the bottom).

## ⚠️ Check for duplicates before adding a task/project (hard rule)

Before `wl add`-ing any task or project, **first check whether a related entry already exists** — if so, reuse it (`wl sched` it onto the new day) instead of creating a duplicate. **Search across time ranges**, because an entry may be scheduled at `@2026-06` (month level) / `@someday` / earlier or later, so looking only at the current month misses it:

- `wl find <keyword>` — keyword/substring search (always run this first); for a concept you can't keyword (paraphrases), `wl query "<text>"` searches by meaning (needs `wl reindex` + the `semantic` extra)
- `wl agenda <start> <end> [--someday]` — everything scheduled in a date range across all granularities (day/week/month pins), the cross-range view that catches `@2026-06` / `@someday` items a per-month tree misses
- `wl ls --parent <proj> --all` — direct children of that project; `wl ls --root <proj> --all` — the **whole subtree** (all descendants, recursive — prefer this for dedup, entries may be nested deeper)

Likewise, before adding a dev todo under a project, check that project's existing entries first.

## When to use `wl` (scenario → command)

| User says | Command |
|---|---|
| **Daily three** (the daily flow) | `wl goal "deliver X today"` (read = `wl goal`) / `wl recap "end-of-day summary..."` (read = `wl recap`) / `wl tick <id> [--note "..."] [--done]` to check in. **Auto-creates today's day node** (hung under the current ISO week), no need to manually `wl add ... --prop type.date=day`. `wl recap`/`wl goal` write a history-preserving `log.tag` log (latest = current); `wl day` shows "(written MM-DD HH:MM)" from the recap log's own time, and if more plain-note logs land after it, warns "⚠ N changes after recap, consider rewriting". Back-fill a past day with `wl recap --date YYYY-MM-DD "..."` (`goal --date` is not available — recap only) |
| Add a task / project / habit | `wl add "..." -p A -t work,P0 --parent N` (bare = task; `--para project` for a project; `--prop type.habit` for a habit) |
| Add a task with scheduled time (precise or fuzzy) | `wl add "..." --scheduled 2026-06-15` / `--scheduled 2026-06` / `next-week` / `next-month` / `someday` |
| Log progress on a task | `wl log <id> "..."` (backfill old logs with `--date 2026-05-06`, or use `import` with body `"2026-05-06 content"` so logs land on the original day, not today). **Logging a TODO auto-promotes it to DOING**; `--keep-status` logs without changing status |
| Record a number / measurement / check-in | `wl metric add <id> glucose 5.4 --unit mmol/L` / `wl metric ls <id>` (omit the node + `--tag X` → find that tag across **all** nodes, e.g. before renaming) / `wl metric edit #M7` / `wl metric rm #M7`; inline: `wl log <id> "..." --metric 'pullups 8'`. Habit done-today = a `checkin` metric (`wl tick`/`wl checkin`). Detail → `references/features.md` |
| Mark done / defer (fuzzy time ok) / start clock | `wl done <id>` / `wl defer <id> next-month` (also accepts `2026-Q3` / `someday` / precise date) / `wl start <id>` `wl stop <id>` |
| Schedule task to a date / repeat (drives "planned") | `wl sched <id> 2026-06-15` (also accepts `tomorrow` / `day-after-tomorrow`) / `--clear`. `--recur` supports period start / end (`daily` / `weekly:Mon,Fri` / `monthly:-1` / `quarterly:1-15` / `yearly:-1`; `-1` = period end) — full grammar in `references/features.md`. A task scheduled to a day shows up in `wl day` as "planned · not yet logged" even with no log |
| Goal / end-of-day summary (any time level) | History-preserving reserved-tag logs (`log.tag` ∈ goal/summary), **a distinct store from props** — group `wl goal set/ls/rm <node>` (`--summary` targets the summary). A goal is the **same `goal` tag at every level** — the node's type (day/week/month/year) is the level (no more `overview`/`top5`). Shortcuts: bare `wl goal "..."` / `wl recap "..."` (auto-target today's day; preferred — read-back + stale-warning), and the key-routed `wl set <node> goal\|summary` (= `wl goal set`; `wl unset` = `wl goal rm`). e.g. `wl goal set <week> "this week: ..."` / `wl goal set <month> "..." 7 9 3`. **Structured targets**: trailing node ids (priority order) are stored as `goal` metrics — `wl goal "ship X" 12 34`, or `wl goal set <node> --ids 12 34` to set them on an existing goal; named-but-unstructured ids in the text trigger a copy-paste hint. `wl day` / `wl goal` show the goal + its numbered, status-marked targets + `[done/total]` |
| Command aliases | `wl alias add d day` (→ `wl d` == `wl day`); a target may carry args — `wl alias add w "day -t work"` → `wl w` == `wl day -t work` (typed args append) / `wl alias ls` / `wl alias rm d`. Stored in `~/.config/worklog/aliases.ini`; target's first word must be a real command, can't shadow one; takes effect next run |
| Date context (holidays / vacation / makeup days) | `wl dateinfo 2026-05-01 Labor-Day-holiday` / `wl dateinfo --import holidays.json` (`{"YYYY-MM-DD":"label"}`) / `wl dateinfo <date> --clear`. Weekday auto-computed; `wl day` header shows "date weekday · label" |
| Reproduce a day's progress (like markdown worklog) | `wl day [YYYY-MM-DD]` (log-date based: work/personal split → secondary group → task → indented logs + stats). **Default `--by plan`** (planned / unplanned). **Planned = has a `sched`/recur entry firing that day (source of truth, set via `wl sched`); everything else = unplanned.** The legacy `:planned:` tag is honored only as a transitional fallback — do NOT add it to new tasks, use `wl sched`. Switch dimensions with `--by project` / `--by priority` (P0/P1/P2) |
| List all active projects | `wl projects` |
| Tree view | `wl tree` (**default = overview: timeline expanded to today [year→quarter→month→week→today + today's tasks] + areas only listed as names, ~30 lines to avoid flooding**) / `wl tree --root <area>` (projects + tasks under that area) / `wl tree --root <week/month>` (per-day activity) / `wl tree --depth N` (fully expanded from lifetime) / `wl tree --by project/tag/direction` (switch dimension). Time nodes sorted by date; **day node expansion = tasks that have logs that day + only their logs from that day** |
| Time-range progress (weekly report input) | `wl changes --week 2026-W22` / `wl summary --week ... --by project/day` |
| Full info + timeline for a single task | `wl show <id>` |
| Search by keyword vs by meaning | `wl find <kw>` (exact substring across title/body/log/tag — primary, no setup) · `wl query "<text>"` (**hybrid**: fuses semantic meaning + keyword match via RRF, so paraphrases *and* exact names both surface; jieba-segmented, `config.ini [synonyms]` aware; `--limit N` / `--threshold T` / `-o json`). `query` needs a one-time `wl reindex` (embeds all nodes via the configured OpenAI-compatible server; backend in `wl config`); the **`semantic` extra** (`pip install 'pyworklog[semantic]'`) is the fast path (LanceDB + jieba) but optional — without it `query` auto-falls-back to a pure-Python SQLite store + `\w+` segmentation. `find` is the zero-setup primary; `query` adds meaning + ranking |
| Focus a node's parents/children | `wl focus <id>` / `wl ancestors <id>` / `wl descendants <id>` |
| Link a vault doc | `wl link <id> "doc name"` (no `.md` suffix); remove one with `wl unlink <id> "doc name"` |
| Relate two tasks (split / related) | `wl relation <id> split-from <other…>` (this task was split out of another) · `split-into` (inverse) · `related` (symmetric, the **default type** — `wl relation 42 7 9` == `… related 7 9`). `wl relation <id>` lists; `--rm` removes. Writes **both sides** + `wl show` shows a `relations:` block. Distinct from ancestors (parent/child hierarchy) — this is derivation/association across the tree. Stored as `relation.*` props (comma-sep id list), so `wl ls --prop relation.split_from` works. **At creation:** `wl add "…" --relation 'split-from 42' --relation 'related 7 9'` establishes the relation in one shot (repeatable, both sides). **When a new node is split from / related to another, attach the relation with `--relation`** so the derivation isn't left implicit in the title |
| Edit a node's tags | `wl tag <id> +work -planned` (bare word = add; no ops = list). **Edits the real tag field** — do NOT `wl set <id> tags ...` (rejected; it would create a misleading shadow prop). For bulk, `apply ~ #id / +tag` or `import add_tags/remove_tags` |
| Vocabulary overview (cross-node) | `wl tags` / `wl props` / `wl metrics` / `wl types` — each lists that vocabulary in use across all nodes, sorted by frequency. All support `-o json`: `wl tags -o json` → `[{"tag": "work", "count": 2}, …]`; `wl props -o json` → `[{"key": "owner", "count": 1}, …]`; `wl metrics -o json` → `[{"tag": "pullups", "count": 3}, …]`; `wl types -o json` → `[{"key": "type.para", "value": "project", "count": 1}, …]`. Use these to discover what's in the DB before filtering with `wl ls --tag` / `--prop` / `wl metric ls --tag`. |
| Bind / show which task **this agent session** is on | `wl agent <id>` binds this session to a node (id from `$WL_SESSION_ID` / `$CLAUDE_CODE_SESSION_ID`); `wl agent` shows · `ls` lists all · `rm` unbinds. Stored as an `agent_session.<agent>` prop (live pointer, one session→one node; agent = claude / cursor / codex from `$WL_AGENT` / `--agent`, default claude). Each bind records history by default (`--no-record` to skip) — two metrics on one carrier log: `agent_session` (value = session id) + `agent` (value = runtime name); past sessions via `wl metric ls <id> --tag agent_session --all`. Status-line + hook scripts ship under `integrations/` (jq-free; `wl agent context [--hook]`); install steps in `references/setup.md`, walkthrough in `wl help agent` |
| List log stream | `wl logs` (**default: last 7 days only**, to avoid flooding); `--since/--until/--date` for explicit range; `--group day [--by project/priority/plan]` for daily replay. **All list views truncate each log to one line** |
| Read one log in **full** | `wl log show #L282` — one log's complete body (lists/timeline truncate each to one line). Accepts `#L282`/`L282`/`282`. A whole node's logs in full: `wl --log-format full show <id> --all-timelines` |
| Change one log's tag | `wl retag #L282 <tag>` — set a log's role (goal / summary / a custom marker); `note` / `none` / `-` / empty clears back to a plain note |

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
wl -q show 356                     # #356 goal / summary, no timeline
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

- **⚠️ Verify an id is a `wl` node before any mutating command.** A bare `#NNN` collides with the harness TaskList / session / Linear / PR id spaces — `wl show <id>` first, or you mutate the wrong node.
- **⚠️ For dates near now, let `wl` read the clock — don't hand-type them** (an AI's "today" drifts to its session start). Today = bare `wl day`, set with `today`; nearby days = `yesterday`/`tomorrow` or signed deltas (`wl day -1`, `wl sched 42 +2`, `wl log 42 "..." --date -1`). Use `YYYY-MM-DD` only for a fixed/far date (run `date` first). Absolute dates in human-facing **text** still stay `YYYY-MM-DD`.
- **No silent bulk delete / modification** — confirm with the user via `wl show <id>` before destructive changes
- **Never bypass `wl` to touch the DB directly — neither `sqlite3` reads nor writes.** `wl` is the only supported interface; for anything missing, add/extend a `wl` command rather than querying the file. The DB (default `~/.local/share/worklog/worklog.db`, or wherever `$WORKLOG_DB` / `--db PATH` points) is the source of truth; its schema is an internal detail, not a query surface.
- **Don't run `wl reset`** (drops the DB) unless explicitly requested
- Before any bulk write, **`--dry-run` first**, especially for update/delete

<!-- Developing wl itself (TDD, the keep-surfaces-in-sync rule, test seams) is NOT documented here —
this skill is for USING wl. Contributor discipline lives in the repo's AGENTS.md + CONTRIBUTING.md. -->

## Where a fact goes: prop vs metric vs log

Ask *"will I filter/stat over it across nodes?"* + cardinality (full rule: `DESIGN.md` §2 / `wl help prop`):

- **`prop`** — single-value **query dimension** (`owner`, `project`, the one ref a task maps to, the `release` it shipped → "which tasks shipped in v0.7.0" = one query). Don't flood with process noise.
- **`log`** — the process record (a dev task's many commits).
- **`metric`** — those records made queryable per node: `wl metric add <id> commit <hash>`, then `wl metric ls <id> --tag commit`. Many-per-node, not a cross-node filter.
- commit/PR/release: many commits → log (+ a `commit` metric for structure); a single identifying PR/commit or the release → prop.
- **Namespaced prop keys** — dot-group related single-value props under a prefix (`agent_session.claude`, `ext.linear`); each full key stays single-value, but `key LIKE 'group.%'` finds the whole namespace, so prop filters/stats can target a namespace, not just an exact key. Flat keys (`owner`/`release`) stay flat.

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
