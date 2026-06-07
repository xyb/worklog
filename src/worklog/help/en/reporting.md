---
title: reporting — reviewing what happened
category: guide
see_also: day, summary, changes, logs, projects
---
Pick the lens by scope:

  one day     `wl day [date]`            plan + activity, bucketed
  one node    `wl show <id>`             full detail + timeline  ·  `wl focus <id>` up+down context
  a list      `wl ls`                    sorted/filtered open items  ·  `wl tree`  the structure
  a period    `wl summary --week/--month`  counts (done / doing / added) by project or day
              `wl changes --week …`         per-project deltas
              `wl logs --since … --by-task` the raw log stream
  projects    `wl projects`              active projects + recent activity

Weekly-report recipe: `wl summary --week` for the skeleton, `wl changes` for per-project
deltas, `wl logs --by-task` to recall specifics. The time-range flags are shared across the
period commands — see `wl help window`.
