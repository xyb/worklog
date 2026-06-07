---
title: clock — track time on a node (start / stop / spent / active)
category: command
see_also: day, status, log
---
A **clock** is a time interval on a node. The time-tracking family:

  wl start 42            # clock in (status → DOING); several ids ok
  wl stop 42             # clock out + compute elapsed (--at to backfill a past end)
  wl spent 42 90m        # record a past duration without a live timer (retrospective)
  wl active              # what's timing right now + today's elapsed + latest log
  wl clock ls 42         # list #42's intervals · clock edit <cid> · clock rm <cid>

`wl day` and `wl active` total a node's time. If you only `wl log` (never start/stop), wl
still derives a rough duration from the span of that day's log timestamps — so time shows
up either way; an explicit clock is just more precise.

`wl wait <id>` (blocked) auto-closes an open clock, so a forgotten timer doesn't run
overnight. Fix a mistimed interval with `wl clock edit <clock_id> --start … --end …`.
