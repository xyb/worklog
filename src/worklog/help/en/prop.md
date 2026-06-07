---
title: prop — a static key=value attribute
category: concept
see_also: meta, tag, node
---
A **prop** is a static, single-value key=value attribute on a node (e.g. `owner`,
`linear-id`). It overwrites in place — no history.

  wl set 42 owner xyb          # set/overwrite a prop (= wl prop set)
  wl prop ls 42                # list a node's props
  wl unset 42 owner            # remove one (= wl prop rm)

Prop vs meta: a prop is one static value; a **meta** field (goal/summary/overview/top5) is
history-preserving — each edit appends (see `wl help meta`). `wl set` / `wl unset` route by
key: a meta key goes to the meta store, any other key to a prop. To edit real **tags** use
`wl tag` — `wl set <id> tags ...` is refused on purpose (it would make a misleading shadow
prop, not touch the real tag field).
