---
title: sched — schedule a node to a day (drives "planned")
category: command
see_also: defer, day, planning, add
---
`wl sched <id> <day>` schedules a task to a day. A scheduled task shows up as **planned**
in `wl day` for that day — this is how "what did I plan for today?" is answered (planned vs
unplanned is derived from the schedule, not from a tag).

  wl sched 42 2026-06-15            # a specific day
  wl sched 42 tomorrow              # today / yesterday / tomorrow / day-after-tomorrow
  wl sched 42 --recur weekly:Mon,Fri  # recurring (daily / monthly:-1 / quarterly:1-1 / …; -1 = period end)
  wl sched 42                       # list this task's schedule
  wl sched 42 --clear               # clear it (= wl sched rm 42)

Difference from `wl defer`: `sched` is a precise commitment to a day (appears "planned");
`wl defer <id> someday` is a loose backlog item (status LATER, no committed day). Create +
schedule in one step with `wl add "..." --sched today`. See `wl help planning` for the rhythm.
