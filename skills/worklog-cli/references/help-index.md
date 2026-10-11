# Help index

This is the map. It does not repeat the pages. Find the topic name here, then read it with `wl help <topic>`. The full list on the installed command is `wl help --all`. A command's flags are `wl <cmd> -h`; that page names a topic when a longer one exists.

The commands called most often. Run the example. Open the topic when the flags are not enough.

| Run | | Topic |
|---|---|---|
| `wl -q show 42` | one node | `wl help show` |
| `wl -q logs --id 42 --tail 5 --no-body` | last 5 logs, titles only | `wl help logs` |
| `wl -q logs --id 42 --tail 5 --log-format full` | same, full text, newlines kept | `wl help logs` |
| `wl log show L100` | one log | `wl help log` |
| `wl log 42 "drafted the intro" --keep-status` | write a log; leave status | `wl help log` |
| `wl query "release notes"` | search first | `wl help query` |
| `wl find "release notes"` | only when `query` cannot run | `wl help find` |
| `wl agent 42` | bind this session | `wl help agent` |
| `wl day` | today; `-q` is already on | `wl help day` |
| `wl add "ship the report" -p A -t work --parent 7` | new task; priority A; tag work; parent 7 | `wl help add` |
| `wl link 42 "release notes"` | attach a vault doc, no `.md` | `wl help link` |

## Guides

| Topic | Read when |
|---|---|
| `wl help para` | How work is organized: area, project, task |
| `wl help planning` | Goals, recaps, and the scheduling rhythm |
| `wl help status` | What `[ ]` `/` `[x]` mean |
| `wl help planned` | Why a task is planned or unplanned on a day |
| `wl help reporting` | Reviewing a day, week, or month |
| `wl help tracking` | Habits, check-ins, and numbers |
| `wl help time` | Clocks versus reconstructed hours |
| `wl help bulk` | Loading or editing many nodes |
| `wl help admin` | Setup, config, maintenance |

## Concepts

| Topic | Read when |
|---|---|
| `wl help node` | The one record everything hangs on |
| `wl help kind` | What a node is (`type.para`, `type.date`, habit, meetlog) |
| `wl help ids` | Node id, `#L` log id, `#M` metric id |
| `wl help prop` | A single-value attribute, and namespaces |
| `wl help body` | Node body versus log body |
| `wl help priority` | `-p A/B/C` |
| `wl help window` | `--since` / `--until` / `--date` / `--week` / `--month` |
| `wl help output` | `-o json` / `jsonl` / `toon` |
| `wl help shortcuts` | Short forms (`wl add` = `wl node add`, and the rest) |
| `wl help delete` | Removal is reversible |
| `wl help area` / `project` / `task` | The three PARA roles |

## Commands

Run `wl help <command>` for the longer page. Everyday ones:

`add` `log` `logs` `show` `ls` `day` `tree` `query` `find` `reindex` `sched` `agenda` `defer` `goal` `recap` `done` `cancel` `wait` `reopen` `tick` `checkin` `metric` `dateinfo` `link` `unlink` `tag` `relation` `agent` `import` `apply` `focus` `ancestors` `descendants` `hours` `clock` `summary` `changes` `projects` `active`

Also a topic: `alias` `config` `set` `unset` `relog` `unlog` `start` `stop` `spent` `types`.
