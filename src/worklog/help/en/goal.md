---
title: goal — today's intended deliverable
category: command
see_also: recap, planning, meta
---
`wl goal "..."` sets today's goal — a short statement of what you aim to deliver today;
`wl goal` (no text) reads it. Stored as the day node's `goal` meta field (history-preserving:
each write appends, the latest is current).

  wl goal "ship the Q3 report draft"
  wl goal

The morning counterpart to `wl recap` (evening). `wl day` shows it at the top. For the full
rhythm (week `overview`, month `top5`) see `wl help planning`.
