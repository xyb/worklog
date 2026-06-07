---
title: day — a single day's plan + activity
category: command
see_also: planning, para, status
---
`wl day [date]` reproduces one day (default today): the day's goal + recap at the top,
then work / personal / other buckets → planned/unplanned → task → its logs that day.

  wl day                     today
  wl day 2026-05-30          a past day (works for history)
  wl day yesterday           today / yesterday / tomorrow / day-after-tomorrow
  wl day -t work             only the work bucket (-t/--tag, AND)
  wl day --by project        regroup (default --by plan = planned/unplanned)

Planned vs unplanned: a task you `wl sched` to that day is "planned"; a task you only
logged on that day (no schedule) is "unplanned". Scheduling, not tags, drives this.

The time levels behind the view (lifetime ▸ year ▸ quarter ▸ month ▸ week ▸ day) build
themselves as you log and schedule — you never create them by hand. A suggested (optional)
planning cadence per level lives in `wl help planning`.

End-of-day flow: `wl day` to review → `wl recap "..."` to write the summary (`wl day`
then shows it, and warns if you log more afterward).
