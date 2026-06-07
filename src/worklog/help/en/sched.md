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
  wl sched 42                       # list this task's schedule
  wl sched 42 --clear               # clear it (= wl sched rm 42)

Recurring rules (`--recur`; every cycle supports `-1` = its last day):
  daily                       every day
  weekly:Mon,Wed,Fri          named days (or numeric weekly:1,3,5; -1 = Sunday)
  monthly:5,15,-1             day 5 / 15 / last of each month
  quarterly:1-15              the 15th of each quarter's first month
  quarterly:-1                the last day of each quarter (3/31, 6/30, …)
  yearly:03-21                March 21 every year (yearly:-1 = 12-31)

Difference from `wl defer`: `sched` is a precise commitment to a day (appears "planned");
`wl defer <id> someday` is a loose backlog item (status LATER, no committed day). Create +
schedule in one step with `wl add "..." --sched today`. See `wl help planning` for the rhythm.
