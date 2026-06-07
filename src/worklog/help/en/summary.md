---
title: summary — time-window aggregate (weekly-report input)
category: command
see_also: changes, day, projects
---
`wl summary` aggregates a time window into done / doing / added counts, grouped by project
or by day — the raw material for a weekly report.

  wl summary --week 2026-W22            # this ISO week
  wl summary --since 2026-06-01 --by day
  wl summary --month 2026-06 --by project
  wl summary --week 2026-W22 --top 5    # only the 5 most-progressed projects
  wl summary --week 2026-W22 --projects-only   # project rows only, no task expansion

Time-window flags (`--since` / `--until` / `--week` / `--month`) are shared with `wl changes`
and `wl logs` (see `wl help window`). For per-project added/done/log deltas use `wl changes`;
for one day's detail use `wl day`. A task spanning multiple projects is counted once by
default (`--no-dedup` repeats it per bucket).
