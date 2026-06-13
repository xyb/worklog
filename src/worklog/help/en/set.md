---
title: set — set a prop or a goal/summary (key-routed)
category: command
see_also: prop, goal, unset
---
`wl set <id> <key> <value>` is a key-routed shortcut: the key goal or summary writes that
reserved-tag log (= `wl goal set`), any other key writes a static prop (= `wl prop set`).
  wl set 42 owner xyb                 # a prop
  wl set <week_id> goal "..."         # a goal log on any node (prose only; for target ids use `wl goal set`)
`wl unset` is the delete counterpart. To edit real tags use `wl tag` (not set). See
`wl help prop` / `wl help goal`.
