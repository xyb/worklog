# Related sub-skills (routing)

`wl` is the data layer; these higher-level skills drive specific worklog workflows on top of it.
Each self-triggers on its own description, so you rarely need to consult this table — read it when
a request clearly matches a workflow (morning planning, weekly summary, …) and you want the
dedicated skill rather than raw `wl` commands.

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
