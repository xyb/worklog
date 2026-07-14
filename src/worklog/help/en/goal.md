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

## Achievement tracking: when does a target count as done?

`wl day` / `wl goal` resolve a goal's targets, mark each one, and append ` [done/total]` with a
status emoji — `✅` all done, `🟡` partial, `⬜` none:

    $ wl day
      > 🎯 ship the report, write the plan  [2/2] ✅
         1. [x] #12 send the Q3 report
         2. [x] #42 weekly plan   ← recurring: a check-in settled it;
                                    its status is still TODO

**A one-off target** settles on its status: DONE, or CANCELED (a decision not to do it is still a
disposal, so it counts — a goal isn't unachieved because you consciously dropped an item).

**A recurring target never reaches DONE.** `wl done` on a recurrence would retire the whole thing
— every future occurrence would show as already finished and stop prompting you. So you close out
a recurrence with `wl tick`, which records a check-in and leaves the status at TODO. Reading the
status alone would therefore pin a recurring target at `[ ]` forever, no matter how faithfully you
did it. Instead it settles on **a check-in inside the goal's own period**:

- a goal on a **day** node (the everyday `wl goal "…" 42`) → a check-in **that day**: `wl tick 42`
- a goal on a **week** node (`wl goal set <week> …`) → a check-in anywhere in **that ISO week**
- a goal on a **month** node → a check-in anywhere in **that month**

That's the same check-in signal `wl day`'s task list already renders as `[x]`, so the header and
the task bucket can never disagree about whether you did it.

Two consequences worth knowing:

- Retiring a recurrence later (`wl sched stop`) does **not** un-settle a period you already
  checked in for — the past is a fact and doesn't change when the future does.
- The rule is deliberately coarse: **one** check-in anywhere in the period settles it. So a
  *daily* habit set as a *week* goal reads done after a single day. For a day goal — the common
  case — there's no difference. (A habit's own `(this month 12/14)` progress still tells the
  honest story if you want the rate.)

A goal on a node that isn't a time node has no period, so its targets can only settle on status.

The morning counterpart to `wl recap` (evening). `wl day` shows the day's goal + recap, plus the
week's and month's goal, each header line with its own marker (🎯 goal · 📝 recap · 📅 this week ·
⭐ this month). `wl set <node> goal "..."` / `wl unset` route here by key. See `wl help planning`
for the cadence.
