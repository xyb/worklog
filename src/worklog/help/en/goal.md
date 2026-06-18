---
title: goal — a node's intended deliverable (any time level)
category: command
see_also: recap, planning, day
---
`wl goal` reads/writes a goal — a short statement of what you aim to deliver. Bare `wl goal` is
the shortcut for TODAY (auto-creates today's day node); `wl goal set/ls/rm <node>` reach any node
(day / week / month / year — the level is just the node's type). Stored as a `goal` log
(history-preserving: each write appends, the latest is current).

  wl goal "ship the Q3 report draft"         # today's goal
  wl goal "ship X" 12 34                      # today's goal + its target nodes #12,#34
  wl goal                                     # read today's goal
  wl goal set <week_id> "this week: ship X"   # a week-level goal
  wl goal set <month_id> "deliver A, B" 7 9 3 # a month's goal + ~5 target nodes (priority order)
  wl goal ls <node_id>                        # show a node's current goal / summary
  wl goal rm <node_id>                        # clear a node's goal (--summary clears the summary)

**Structured targets**: a goal can name the node ids it aims to deliver — supplied explicitly,
trailing the text, **order = priority**. They're stored as `goal` metrics on the log (queryable;
wl never parses them from the prose). Any number is allowed (we suggest ~5 for a month). Omit
them and the goal is plain free text. Already wrote a goal and forgot the ids?
`wl goal set <node> --ids 7 9` sets that node's current goal targets to exactly those ids
(wholesale — the text is the complete goal — no new log, no re-typing).

If a goal's text *names* live node ids (`#42`) that aren't structured targets yet, the success
line prints a copy-paste hint with both forms: `--ids` them onto the current goal now, or the
one-shot `wl goal "..." 42` for next time. If a goal ends up with **no** target nodes at all, it
nudges you to link some — `wl goal set <node> --ids <id…>` — so every goal points at its tasks.

**Achievement tracking**: if the goal text names task ids (`#12`, or `WL#12`), `wl day` resolves
them and appends ` [done/total]` with a status emoji — `✅` all done, `🟡` partial, `⬜` none.

The morning counterpart to `wl recap` (evening). `wl day` shows the day's goal + recap, plus the
week's and month's goal, each header line with its own marker (🎯 goal · 📝 recap · 📅 this week ·
⭐ this month). `wl set <node> goal "..."` / `wl unset` route here by key. See `wl help planning`
for the cadence.
