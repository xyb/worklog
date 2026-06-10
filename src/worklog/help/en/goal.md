---
title: goal — today's intended deliverable
category: command
see_also: recap, planning, meta
---
`wl goal "..."` sets today's goal — a short statement of what you aim to deliver today;
`wl goal` (no text) reads it. Stored as the day node's `goal` meta field (history-preserving:
each write appends, the latest is current).

  wl goal "ship the Q3 report draft"
  wl goal "finish #12 and #13"   # naming task #ids makes `wl day` show progress
  wl goal

**Achievement tracking**: if the goal names task ids (`#12`, or `WL#12`), `wl day` resolves them
and appends ` [done/total]` with a status emoji — `✅` all done, `🟡` partial, `⬜` none — so the
goal line shows its own progress. No ids → no indicator (plain free-text goal).

The morning counterpart to `wl recap` (evening). `wl day` shows it at the top, each header field
with its own marker (🎯 goal · 📝 recap · ⭐ top5 · 📅 week). For the full rhythm (week `overview`,
month `top5`) see `wl help planning`.
