# Feature detail

Detailed grammar for the commands summarized in SKILL.md's scenario table, organized by topic.
Read on demand for the exact flags / behavior of a feature; `wl <cmd> --help` is the per-command
quick reference.

## Compound & batch operations

Fold several writes into one command (a whole retrospective entry in a single call), and act on
many ids at once:

```fish
# create + log + done + closed_at + link + sched, all in one
wl add "got something done" -p B --log "result: PR#42 fixed 3 bugs" \
  --done --at 14:30 --link "vault doc name" --sched today
wl done <id> --log "result note" --at HH:MM        # existing task: close + log in one
wl cancel <id> --log "abandoned: priority dropped"
# -m is short for --log (matches git commit habit)

wl add "new task" -p A --parent 7 --sched today   # add + direct-to-sched
wl done 18 19 20                                           # batch (done/start/stop/wait/reopen/link all take multiple ids)
wl start 18 19; wl stop 18 19                              # batch clock
wl wait 18 --note "waiting on review"                     # WAIT state + auto-stops CLOCK
wl reopen 18                                              # undo DONE back to TODO
```

⚠️ `--sched <day>` (precise, writes the sched table, visible in `wl day <that-day>`) vs.
`--scheduled <fuzzy>` (rough hint, only sets `node.scheduled_date`). **Use `--sched` daily.**
Passing both conflicts.

## log editing: `unlog` / `relog`

`wl show` / `wl logs` timelines show log id as `#L<id>`:

```fish
wl unlog #L282                       # delete log
wl unlog --node 39 --date yesterday  # delete most-recent 1 by node + date
wl relog #L282 "corrected content"   # edit body
wl relog #L282 --at 14:30            # edit time only
wl relog #L282                       # no body/--at → opens $EDITOR
```

## Time tracking: `start` / `stop` / `spent` (structured `clock` table)

Time is a structured interval in the `clock` table, not log text. `wl start` opens an interval;
`wl stop`/`wl wait` close it; `wl spent` writes a closed one. `wl active` lists open intervals;
`wl show` renders them as `start→end (Nmin)`; `wl day`/`wl summary` totals sum elapsed.

```fish
wl start <id> --at 09:00              # open an interval (backfill start)
wl stop <id> --at 11:30               # close it (must be after start)
wl spent <id> 90m                     # given duration, write a closed interval directly
wl spent <id> 1h30m --at 14:00        # 14:00 as end, backs out 12:30 as start
wl active                             # tasks currently on CLOCK (with elapsed)
# wl start refuses a 2nd open interval on an already-running node (wl stop first)
```

## Structured datapoints: `wl metric` (node → log → metric)

A `metric` is a structured datapoint (number / measurement / check-in) that hangs off a log. Use
it for anything you'll want to query/trend later (glucose, weight, reps, …) instead of stuffing
numbers into log text.

```fish
wl metric add 42 glucose 5.4 --unit mmol/L   # numeric datapoint (auto creates a carrier log)
wl metric add 42 pullups 8                    # numeric, no unit
wl metric add 42 mood good --text             # text value
wl metric add 42 checkin                       # a pure marker (stored as value 1)
wl metric add 42 glucose 6.1 --on-log #L99     # attach to an existing log instead of a new carrier
wl metric ls 42                                # list (default this week; --all / --tag / --since/--until/--week/--month)
wl metric edit #M7 --value 5.6 --note "post-meal"
wl metric rm #M7 #M8                           # delete (also removes an emptied auto-carrier log)
```

Inline shortcut — attach datapoints in the same command as a log/task (repeatable):

```fish
wl log 42 "morning reading" --metric 'glucose 5.4 mmol/L' --metric checkin
wl add "weigh-in" --metric 'weight 70 kg'
```

`wl import` too: a log entry can carry `"metrics":[{tag,value,unit}]`, and a node can carry
node-level `"metrics":[...]` on ONE carrier log (1 carrier → N points, e.g. a day of CGM readings
without 288 separate logs).

`wl show` folds a log's metrics beneath it (`↳ [glucose] 5.4 mmol/L`); an empty metric-carrier
log shows its datapoint directly as a `📊 metric` line. `wl day` / `wl tree` day-expansion fold a
node's that-day datapoints under it (the `checkin` marker is skipped — it's reflected by `[x]`);
>5 are elided. A scheduled habit also shows `(本月 N/M)` = month-to-date check-ins / scheduled days.

## Habit check-in: `wl tick` / `wl checkin`

Habit "done today" is a structured `tag=checkin` metric (idempotent per day), NOT "any log exists
that day" — so a stray note no longer marks a habit done. `wl tick` and `wl checkin` write it.

```fish
wl tick 39                            # check in one habit today (writes a checkin metric)
wl tick 39 40 41 --note "…"           # bulk check-in
wl checkin                            # default multi-select (↑↓ space enter)
wl checkin --per-item                 # alt: one-by-one y/n/note/q prompt
wl checkin --all-kinds                # not limited to the habit type (task/meetlog too)
```

## Goal / summary keep history (reserved-tag logs, not props)

Two reserved-tag logs — `goal` (forward, any time level) and `summary` (backward recap) — have
their own group `wl goal set/ls/rm <node>` (`--summary` targets the summary), stored as `log.tag`
logs: each write appends, the latest is current, so edit history is kept (`prop` is a separate
store, only for truly-static single-value attributes). A goal is the **same `goal` tag at every
level** — the node's type (day/week/month/year) is the level (the former `overview`/`top5` are
gone, folded into `goal`; migration 0010). Bare `wl goal` / `wl recap` are today-auto shortcuts;
`wl set <node> goal|summary` / `wl unset` are key-routed onto `wl goal set` / `wl goal rm` (so
`wl set` fronts both `prop set` and `goal set` by key, like `wl add` is `node add`).

**Structured goal targets**: a goal can name the node ids it delivers — supplied explicitly,
trailing the text, order = priority — stored as `goal` metrics (`wl goal "ship X" 12 34`). Set
them on an existing goal with `wl goal set <node> --ids 12 34`. `wl day` / `wl goal` / `wl goal ls`
render the goal + its numbered, status-marked targets + a `[done/total]` tag computed from them.

## Scheduling & recurrence rules (`--recur`)

Every recurrence variant supports `-1` for "last day of period":

```fish
wl sched <id> --recur weekly:Mon,Wed,Fri    # or weekly:1,3,5 / weekly:-1=Sun
wl sched <id> --recur monthly:5,15,-1       # each month 5th / 15th / last
wl sched <id> --recur quarterly:1-15        # quarter's 1st month 15th → 1/15, 4/15, 7/15, 10/15
wl sched <id> --recur quarterly:-1          # quarter end (3/31, 6/30, 9/30, 12/31)
wl sched <id> --recur yearly:03-21          # every year on a date
wl sched <id> --recur yearly:-1             # year end 12-31
```

## Querying: `wl ls`, timeline tails, precision

### `wl ls` multi-dimensional query (inspired by shell `ls -t/-S/-r`)

Default limit 20 + truncation hint; `wl ls --help` includes 10 examples:

```fish
wl ls --para project                  # projects only
wl ls --parent 45                     # children of #45
wl ls --tag work,dev                  # multi-tag AND
wl ls -p A                            # priority A (P0); -p A,B = any-of; -p P0 == -p A
wl ls --status TODO,DOING             # status, comma = any-of
wl ls --prop github.pr                # reverse-query by prop: K=V / K (exists) / GROUP. (prefix); repeat=AND
wl ls --unscheduled                   # backlog: open items with no schedule
wl ls --sort created -r --limit 5     # last-5 created (like ls -tr -5)
wl ls --sort updated --limit 10       # last-10 with new logs (like ls -t)
wl ls --recent 7                      # touched in last 7 days
wl ls --ids 39 41 270                 # specific ids (like ls f1 f2)
wl ls --all                           # remove limit + include DONE/CANCELED
```

### `-o json` — machine-readable output (show / ls / logs / projects / day / tree / summary)

`-o json` emits structured JSON instead of the text view, on `wl show` / `wl ls` / `wl logs` /
`wl projects` / `wl day` / `wl tree` / `wl summary` (other commands reject `-o`). Field names =
DB columns (stable); `*_at` UTC, `*_date` local; empty → `[]`. Pull an exact field instead of
parsing text:

```fish
wl show 42 -o json | jq .status       # full node + relations (one object; array for several ids)
wl ls -p A -o json | jq '.[].title'   # array of node summaries (filters apply, no 20-cap)
wl logs --id 42 -o json               # array of that node's log rows
wl projects -o json | jq '.[].counts' # projects + per-project done/doing/pending/total
wl day -o json | jq '.goal_progress, .goal_targets'  # goal+targets flattened; + tasks-with-logs + clock
wl tree --root 45 -o json             # nested structural subtree
wl summary --week 2026-W22 -o json | jq .totals   # window totals + done/pending
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

**The command should make "goal → command → output" precise at all three layers** — don't rely on
`ls --all` and eyeball-search. Anti-patterns: AI uses `wl ls` to list 100 entries to find 1 / `wl
logs --id N` lists 17 to find latest. Correct: use specialty entry points (`wl find` / `wl active`
/ `wl day` / `wl ls --recent/--ids/--sort` / each subcommand's `--help` for examples) or add one.

### Input validation

All empty-string inputs (title / body / vault_doc / prop key / find query) are rejected
uniformly; illegal field names (`--in bogus` / `--para bogus`) and illegal times (`--time 25:99`)
are rejected too — bad data never lands.
