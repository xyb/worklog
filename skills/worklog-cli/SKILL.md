---
name: worklog-cli
description: Use wl to read and write tasks, logs, a day, schedules, and metrics. Use when the user says wl, worklog, log progress, show today, list tasks, or apply a batch. Read with wl, not sqlite.
---

# worklog-cli (wl)

`wl` is the execution record: tasks, logs, a day, schedules, and metrics, in one local database. Use it instead of a markdown worklog or a direct SQL read. A markdown file cannot answer what was planned, what is still open, or what happened in a week. SQL drops columns, clips body text, and treats a stored UTC timestamp as a local date.

This skill says which command to run, and the rules that stop a wrong write. It does not copy the manual. The map of what to read is `references/help-index.md`: topic name, then `wl help <topic>`. `wl <cmd> -h` is that command's flags. `wl help --all` is the same topic list on the installed command. Changing `wl` itself: read repo `DESIGN.md`.

## Often used

| Run | |
|---|---|
| `wl -q show 42` | one node |
| `wl -q logs --id 42 --tail 5 --no-body` | last 5 logs, titles only |
| `wl -q logs --id 42 --tail 5 --log-format full` | same, full text, newlines kept |
| `wl log show L100` | one log |
| `wl log 42 "drafted the intro" --keep-status` | write a log; leave status |
| `wl query "release notes"` | search first |
| `wl find "release notes"` | only when `query` cannot run |
| `wl agent 42` | bind this session |
| `wl day` | today; `-q` is already on |
| `wl add "ship the report" -p A -t work --parent 7` | new task; priority A; tag work; parent 7 |
| `wl link 42 "release notes"` | attach a vault doc, no `.md` |

## Rules

- **Dedup before `wl add`.** `wl query "<text>"` first. If that command cannot run (a missing library), `wl find <keyword>`. An empty result is not that case. Then `wl agenda <start> <end>` and `wl ls --root <project> --all`. Reuse a hit with `wl sched`.
- **Confirm the id is a wl node** before a write. A bare `#NNN` may be a TaskList, session, Linear, or PR id. `wl show <id>` first.
- **Dates near now come from wl.** Bare `wl day` is today. Use `today` / `yesterday` / `tomorrow` / `+N` / `-N`. Type `YYYY-MM-DD` only for a fixed date. Text shown to a person still uses `YYYY-MM-DD`.
- **Read and write only through wl.** No `sqlite3` on the DB. A missing query means a new wl command. Oneline output clips body text. Full text: `wl log show L…`, or `wl logs --id … --match … --log-format full`. Node id, title, status, parent, and full `scheduled_date` stay on `wl show` and `wl ls --ids`, including under `-q`. `wl day <date>` says `(no day node)` when there is none. Soft-deleted schedule rows: `wl sched <id> --deleted`. Several nodes and a key prefix: `wl prop ls <id>… --prefix …`.
- **Default `-q` when reading into context.** In an agent session, `day` / `ls` / `projects` / `active` / `summary` already do this (`--no-brief` undoes it). Other commands need an explicit `-q`.
- **`-o json`, `-o jsonl`, and `-o toon` are global.** Same payload. `toon` is the smaller form for a prompt.
- **Several nodes go through `wl apply` or `wl import`.** `--dry-run` first. Do not loop `wl add`. Field list: `references/bulk.md`. A clock that is still running is the exception: `wl start` / `wl stop`.
- **Do not `wl reset`** unless asked. Confirm before a delete.

## Commands

| Need | Command |
|---|---|
| Today | `wl day`. Planned means a `sched` or `--recur` hits that day. Do not add a `:planned:` tag |
| One node | `wl show <id>` |
| Add | `wl add "…" -p A -t work --parent N`. Also `--para project`, `--prop type.habit`, `--sched <day>`, `--scheduled <fuzzy>`, `--relation 'related 7'`, `--log`, `--done`, `--link` |
| Progress | `wl log <id> "…"`. A TODO becomes DOING. `--keep-status` skips that. Backfill with `--date` / `--time` |
| Close | `wl done` / `wl wait` / `wl cancel` / `wl reopen`. `done` and `cancel` take `--log`. These close a running clock |
| Check in | `wl tick <id>` or `wl checkin`. A check-in marks that day `[x]` for a habit or a recurring task. The node stays recurring |
| A number | `wl log <id> "…" --metric checkin --metric 'tag value unit' --keep-status`. Later, `wl metric add … --on-log L<id>` (a bare add starts a second log). Reuse the node's unit. `references/metrics.md` |
| Schedule | `wl sched <id> <day>` or `--recur <rule>`. `wl sched stop <id>` ends future hits and keeps history. Fuzzy: `wl defer <id> next-month` |
| Clock | `wl start` / `wl stop` / `wl spent <id> 90m` |
| Goal / recap | Today: `wl goal "…" [ids]` and `wl recap "…"`. Another node: `wl goal set <node> "…" [ids]`. A past day: `wl recap --date <day>` (no text reads it). `wl goal` has no `--date`. Read with `wl goal` or `wl goal ls <node>`. `wl log` does not change a goal. `wl goal 408` writes the text `408` |
| Logs | `wl logs` is the last 7 local days. `--match` with no window searches all history. Full body: `--log-format full` or `wl log show L…`. Edit: `wl relog` / `wl retag` / `wl unlog` |
| Search | `wl query "<text>"`. If that command cannot run (a missing library), `wl find <kw>`. An empty result is not that case. Both hide CANCELED unless `--show-canceled` |
| Range | `wl ls` / `wl tree` / `wl projects` / `wl summary --week` / `wl changes --week` / `wl hours` |
| Link / tag / prop | `wl link <id> "doc"` (no `.md`). Remove one with `wl unlink <id> "doc"`. `wl tag <id> +work` — not `wl set <id> tags`. Before a new prop key or metric tag: `wl props` / `wl metrics`, then `references/props.md` |
| Relation | `wl relation <id> related\|block\|split <other>`. `wl relation ready <id>`. `wl relation claim <id>` (a claim older than 24h is free) |
| This session | `wl agent <id>` binds it. `wl agent gaps` lists important work with no session. `wl help agent` |

## Where a fact goes

- **prop** — one value to filter across nodes. Prefer `family.key`. Never name a flat key `type` or `relation`.
- **log** — what happened.
- **metric** — many values on one node. Not a cross-node filter.

A vault doc is the archive. The DB is the execution record. `wl link` stores the doc name.

## Where to read

Open `references/help-index.md` to find the topic, then `wl help <topic>` for the page. The index lists the topics that exist. It does not replace them.

Other files here are extra notes, not a second copy of those topics:

- `references/bulk.md` — assembling one `import` / `apply` diff (`wl help bulk`, `wl help apply`, `wl help import`)
- `references/props.md` / `references/metrics.md` — before inventing a prop key or metric tag (`wl help prop`, `wl help metric`)
- `references/features.md` — longer flag notes; the topic page is still `wl help <topic>`
- `references/setup.md` — install this skill, completion, agent hooks (`wl help admin`)
