---
title: meta — history-preserving fields (goal/summary/overview/top5)
category: command
see_also: planning, prop, day
---
**Meta fields** are the four history-preserving fields that hang off time nodes:
`goal` and `summary` (a day), `overview` (a week), `top5` (a month). Each is stored as a
typed log — every edit appends, the latest is current — so you keep the history of how a
plan evolved. They're a separate store from props (which are static single-value).

  wl meta set <week_id> overview "this week: ship X, unblock Y"
  wl meta set <month_id> top5 "1. … 2. …"
  wl meta ls <id>                 # current value of each meta field on a node
  wl meta rm <id> goal            # clear one

Shortcuts: `wl goal "..."` / `wl recap "..."` target today's day node automatically;
`wl set <node> <field>` / `wl unset` route here by key (a meta key → meta, any other key →
a prop). `wl day` shows the day's goal/summary (and Top5/overview) at the top. See
`wl help planning` for the suggested cadence.
