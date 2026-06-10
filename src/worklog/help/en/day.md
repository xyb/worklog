---
title: day — a single day's plan + activity
category: command
see_also: planning, para, status
---
`wl day [date]` reproduces one day (default today): the day's header meta at the top — each
field with its own marker (🎯 goal · 📝 recap · ⭐ top5 · 📅 week overview), a goal that names
task `#ids` showing ` [done/total]` progress — then work / personal / other buckets →
planned/unplanned → task → its logs that day. A long/multi-line recap carries `> ` only on its
first line; continuation lines align (spaces) under the `> ` content column.

  wl day                     today
  wl day 2026-05-30          a past day (works for history)
  wl day yesterday           today / yesterday / tomorrow / day-after-tomorrow
  wl day -2                  signed delta from today: +1 / -2 / -2d / +3w / +1m (default unit = days)
  wl day -t work             only the work bucket (-t/--tag, AND)
  wl day --by project        regroup (default --by plan = planned/unplanned; --by priority too)
  wl day --log-tail 1        logs default to the last 3 per task; --all-logs / --no-logs / --log-format full also adjust
  wl day -o json             machine-readable: meta (+ goal progress) + tasks-with-logs + clock

Planned vs unplanned: a task you `wl sched` to that day is "planned"; a task you only
logged on that day (no schedule) is "unplanned". Scheduling, not tags, drives this.

The time levels behind the view (lifetime ▸ year ▸ quarter ▸ month ▸ week ▸ day) build
themselves as you log and schedule — you never create them by hand. A suggested (optional)
planning cadence per level lives in `wl help planning`.

End-of-day flow: `wl day` to review → `wl recap "..."` to write the summary (`wl day`
then shows it, and warns if you log more afterward).

Related: `wl active` = only what's running right now (no history); `wl logs -d <date>` = that
day's flat log stream (no task tree); `wl tree --root <day_id>` = the day node's subtree.
