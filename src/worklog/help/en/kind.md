---
title: kind — what a node is (-k)
category: concept
see_also: node, para, add
---
Every node has a **kind**, set with `-k` (default `task`):

  task      a concrete action
  project   an outcome with an end — lives under an area      (PARA → `wl help para`)
  area      an ongoing responsibility, no end — top level
  habit     something recurring; check in with `wl tick`
  meetlog   a meeting note
  day / week / month / quarter / year   the time skeleton (auto-built; you never create these)

Kind drives how a node renders and groups (e.g. `wl day` buckets, `wl tree` timeline). Change
it later with `wl node edit <id> -k <kind>`.
