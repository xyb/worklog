---
title: planning — goals, summaries, and scheduling rhythm
category: guide
see_also: day, para, add
---
wl separates **planning the work** (which tasks, which day) from **planning the
narrative** (goals and summaries at each time level). Both are optional — use what fits.

Planning the work:
  wl sched <id> <day>     schedule a task to a day → it shows "planned" in `wl day`
  wl sched <id> --recur   a recurring rule (daily / weekly:Mon,Fri / monthly:-1 / …)
  wl defer <id> someday   a loose backlog item (status LATER, no committed day)

The narrative rhythm (history-preserving reserved-tag logs — each edit is kept). The goal is
the same `goal` tag at every level; the node's type (day / week / month / …) is the level:
  day      `wl goal "..."`                       what you aim to deliver today
           `wl recap "..."`                       what actually happened (evening)
  week     `wl goal set <week_id> "..."`          this week's focus / P0-P1
  month    `wl goal set <month_id> "..." …ids`    the month's goals (we suggest ~5, by priority)
  quarter  `wl goal set <quarter_id> "..."`       the quarter's objective (if you plan that far)

`wl day` shows the day's goal + recap, plus the week's and month's goal at the top, and warns
if you log more after recapping. Find a level's node id with `wl tree`. None of this is
enforced; the time skeleton builds itself as you log and schedule.
