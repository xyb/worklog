---
title: changes — per-project deltas in a time window
category: command
see_also: summary, window, projects
---
`wl changes` shows what moved per project in a time window — added / done / log counts —
a quick "where did the work go this week?".

  wl changes --week 2026-W22
  wl changes --since 2026-06-01 --until 2026-06-07
  wl changes --month 2026-06

Shares the time-window flags with `wl summary` / `wl logs` (see `wl help window`). Companion
to `wl summary` (done/doing/added counts by project or day) and `wl logs` (the raw stream).
