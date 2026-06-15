---
title: recap — a day's end-of-day summary
category: command
see_also: goal, planning, day
---
`wl recap "..."` writes a day's end-of-day summary (what actually happened); `wl recap` reads
it. Stored as the day node's `summary` log (history-preserving); the write time is recorded so
`wl day` warns if you log more after recapping.

  wl recap "shipped the draft; blocked on review"
  wl recap --date yesterday          # read / back-fill a past day (also -d)
  wl recap --diff                    # the notes logged AFTER this recap (judge if it's stale)

`wl day` warns "⚠ N change(s) after recap" when you log more after recapping; `wl recap --diff`
lists those N notes (excluding checkin/metric noise) so you can tell real missed content from
cross-day churn.

Evening counterpart to `wl goal`. Week/month goals are `wl goal set <week>` / `<month> "..." …ids`
— see `wl help planning`.
