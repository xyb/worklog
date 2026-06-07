---
title: defer — set a task aside (backlog / someday)
category: command
see_also: sched, status, planning
---
`wl defer <id> <when>` moves a task to LATER and records a rough scheduled hint. Use it for
"not now, revisit later" — the someday/backlog pile — as opposed to `wl sched`, which is a
firm commitment to a specific day.

  wl defer 42 someday          # no committed time, just "later"
  wl defer 42 next-month       # fuzzy
  wl defer 42 2026-Q3          # a quarter
  wl defer 42 2026-06-15       # a date (still LATER, a soft hint — not "planned")
  wl defer 42 +2w              # signed delta from today: +1 / -2d / +3w / +1m / -1y

Unlike `wl sched`, a deferred task does NOT show as "planned" in `wl day` for the hinted
date — it's a loose intention. When you're ready to commit it to a day, `wl sched` it.
