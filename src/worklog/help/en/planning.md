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

The narrative rhythm (history-preserving meta fields — each edit is kept):
  day      `wl goal "..."`                       what you aim to deliver today
           `wl recap "..."`                       what actually happened (evening)
  week     `wl meta set <week_id> overview "..."` this week's focus / P0-P1
  month    `wl meta set <month_id> top5 "..."`    the month's Top 5
  quarter  `wl meta set <quarter_id> goal "..."`  the quarter's objective (if you plan that far)

`wl day` shows the day's goal + recap (and Top5/overview if set) at the top, and warns if
you log more after recapping. Find a level's node id with `wl tree`. None of this is
enforced; the time skeleton builds itself as you log and schedule.
