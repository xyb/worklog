---
title: unset — remove a prop or clear a meta field
category: command
see_also: set, prop, meta
---
`wl unset <id> <key>` is the delete counterpart of `wl set`, key-routed the same way: a meta
key clears that meta field (= `wl meta rm`), any other key removes a prop (= `wl prop rm`).
  wl unset 42 owner
  wl unset <day_id> goal
See `wl help prop` / `wl help meta`.
