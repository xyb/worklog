---
title: tick — one-key habit check-in
category: command
see_also: checkin, tracking, sched
---
`wl tick <id>` records "done today" for a habit: a log plus a structured `checkin` metric
(what `wl day` counts as done). Accepts several ids for batch check-in.
  wl tick 39
  wl tick 39 --note "6 pull-ups"     # -n is the short flag
  wl tick 39 40 41                   # batch
Interactive review of today's habits: `wl checkin`. See `wl help tracking`.
