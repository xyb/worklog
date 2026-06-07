---
title: start — clock in to time a task
category: command
see_also: stop, time, log
---
`wl start <id>` starts a live timer on a node and moves it to DOING. Accepts several ids.
  wl start 42
  wl start 42 --at 09:00     # backfill a past start
Stop it with `wl stop <id>` (computes elapsed); see what's running with `wl active`. For the
whole time model see `wl help time`.
