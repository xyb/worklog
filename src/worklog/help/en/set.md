---
title: set — set a prop or meta field (key-routed)
category: command
see_also: prop, meta, unset
---
`wl set <id> <key> <value>` is a key-routed shortcut: a meta key (goal/summary/overview/top5)
writes the meta field (= `wl meta set`), any other key writes a static prop (= `wl prop set`).
  wl set 42 owner xyb                 # a prop
  wl set <week_id> overview "..."     # a meta field
`wl unset` is the delete counterpart. To edit real tags use `wl tag` (not set). See
`wl help prop` / `wl help meta`.
