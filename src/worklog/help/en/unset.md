---
title: unset — remove a prop or clear a goal/summary
category: command
see_also: set, prop, goal
---
`wl unset <id> <key>` is the delete counterpart of `wl set`, key-routed the same way: the key
goal or summary clears that reserved-tag log (= `wl goal rm`), any other key removes a prop
(= `wl prop rm`).
  wl unset 42 owner
  wl unset <node_id> goal
See `wl help prop` / `wl help goal`.
