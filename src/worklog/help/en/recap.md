---
title: recap — a day's end-of-day summary
category: command
see_also: goal, planning, day
---
`wl recap "..."` writes a day's end-of-day summary (what actually happened); `wl recap` reads
it. Stored as the day node's `summary` meta field; the write time is recorded so `wl day`
warns if you log more after recapping.

  wl recap "shipped the draft; blocked on review"
  wl recap --date yesterday          # read / back-fill a past day (also -d)

Evening counterpart to `wl goal`. Weekly/monthly summaries are `wl meta set <week> overview`
/ `<month> top5` — see `wl help planning`.
