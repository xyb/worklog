---
title: kind — what a node is
category: concept
see_also: node, para, add
---
Every node has a **kind** — its classification. It is no longer a stored column; it is *derived*
from the node's `type.*` props (`type.para`, `type.date`, `type.habit`, `type.meetlog`). A node
with no classification prop is a plain `task`:

  task      a concrete action — a bare `wl add "..."` (no flag)
  project   an outcome with an end — lives under an area      (PARA → `wl help para`)
  area      an ongoing responsibility, no end — top level
  habit     something recurring; check in with `wl tick`
  meetlog   a meeting note
  day / week / month / quarter / year   the time skeleton (auto-built; you never create these)

Set the classification at creation: `--para area|project` for a role, or `--prop type.habit` /
`--prop type.meetlog` / `--prop type.date=day` for the others. Kind drives how a node renders and
groups (e.g. `wl day` buckets, `wl tree` timeline). Change a node's PARA role later with
`wl node edit <id> --para <role>`; set or clear other classifications with `wl set type.<x>` /
`wl prop rm <id> type.<x>`.
