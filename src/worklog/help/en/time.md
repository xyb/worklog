---
title: time — tracking how long things take
category: guide
see_also: clock, day
---
wl records time as **clock intervals** on a node. Two ways:
  • live          `wl start <id>` (clock in → DOING) … `wl stop <id>` (clock out, computes elapsed)
  • retrospective `wl spent <id> 90m` (log a past duration, no live timer)

See what's running now with `wl active`; list / fix intervals with `wl clock ls / edit / rm`.

Roll-up: `wl day` and `wl active` total a node's time for the day. Even with no start/stop,
wl derives a rough duration from the span of that day's log timestamps — so logging alone
gives an estimate, and clocks just make it precise (no separate "duration" field to maintain).

Gotchas: `wl wait <id>` (blocked) auto-closes an open clock so a forgotten timer doesn't run
overnight; fix a mistimed one with `wl clock edit <cid> --start … --end …`. Per-command
details: `wl help clock`.
