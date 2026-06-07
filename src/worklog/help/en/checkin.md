---
title: checkin — daily habit check-ins
category: command
see_also: metric, tag, day
---
For habits (kind `habit`), a **check-in** records "done today" as a structured `checkin`
metric — not merely "a log exists", so a stray note never counts as completion.

  wl tick 39                     # one-key check-in (log "✓ done" + a checkin metric)
  wl tick 39 --note "6 pull-ups" # with a note   ·  -n is the short flag
  wl tick 39 40 41               # batch several habits
  wl checkin                     # interactive: pick today's not-yet-done habits (↑↓ space enter)
  wl checkin --per-item          # one-by-one y/n/note prompts

`wl day` marks a habit `[x]` only when it has a check-in metric that day. Schedule a habit
to recur with `wl sched 39 --recur daily` so it shows up planned each day.
