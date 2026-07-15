---
title: hours — where time went, reconstructed from the log stream
category: command
see_also: summary, changes, day
---
`wl hours` reconstructs where your time went from the log stream and groups it by project,
task, or day. The interval between two adjacent logs is credited to the earlier log's node —
as long as they're close enough to be one work session. A gap over 60 minutes counts as a
break (lunch / meeting / overnight) and is dropped whole, so a real break never inflates a task.

  wl hours                              # today, by project
  wl hours 2026-07-14 --by task         # one day, by task
  wl hours --week 2026-W28              # a week, by project
  wl hours --since 2026-07-08 --until 2026-07-14 --by day
  wl hours -o toon                      # compact, LLM-friendly

Window: a positional day (`YYYY-MM-DD` / `today` / `yesterday`), or `--since/--until` /
`--week/--month/--quarter/--year`. With none, it's today.

`--by`:
  project (default)  roll each task up to its project ancestor; no project → `(unassigned)`
  task               per node that carries logs
  day                per calendar day (use with a multi-day window)

Output: one bar per group (time + share), sorted by time, plus the window total. `-o json`
gives `{since, until, by, total_min, groups:[{id,title,min,pct}]}`; `-o toon` turns the
uniform `groups` into a compact table.

## What it measures (and doesn't)

`hours` measures **log-activity time** — when work was happening on a node — from wl's own
data. A log's timestamp is when the log was written, so the number counts both your work and
any AI agent's: with several agent sessions running in parallel, a day's total can exceed the
hours you were actually at the desk. That's the honest limit of a single-source, wl-only view.

It is **not** a measure of human presence. Knowing how long *you* were actually present or
working needs signals wl doesn't have (keyboard input, voice, calendar, chat), cross-checked
across sources — a separate concern from this command. Use `hours` for "which projects and
tasks saw work this week", not for "was I overloaded".

Why not `clock`: a node's `clock` measures how long a task sat open (dwell time), which under
parallel agent sessions detaches from real effort (one day can accumulate far more than 24 h).
`hours` derives from log cadence instead, so it reflects when work actually happened.
