---
title: stop — clock out and record elapsed
category: command
see_also: start, time, spent
---
`wl stop <id>` ends the live timer started by `wl start` and records the elapsed interval.
  wl stop 42
  wl stop 42 --at 10:30      # backfill a past end
No live timer to stop? Record a past duration directly with `wl spent`. See `wl help time`.
