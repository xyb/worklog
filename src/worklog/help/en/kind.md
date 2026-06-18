---
title: type — what a node is
category: concept
see_also: node, para, add
---
Every node has a **type** — its classification. The type is *derived* from the node's orthogonal
`type.*` props (`type.para`, `type.date`, `type.habit`, `type.meetlog`, or a custom `type.<x>`). A
node with no classification prop is a plain `task`:

  task      a concrete action — a bare `wl add "..."` (no flag)
  project   an outcome with an end — lives under an area      (PARA → `wl help para`)
  area      an ongoing responsibility, no end — top level
  habit     something recurring; check in with `wl tick`
  meetlog   a meeting note
  day / week / month / quarter / year   the time levels (`type.date` + `date.*`)

Classification is orthogonal: each facet is an independent `type.*` prop, so a node can be a
`task` *and* a `habit` at once. When a single label is needed, one representative token is derived
by precedence (PARA role → time level → habit/meetlog → custom → bare `task`).

Set the classification at creation: `--para area|project|task` for a role, or `--prop type.habit` /
`--prop type.meetlog` / `--prop type.date=day` for the others. Type drives how a node renders and
groups (e.g. `wl day` buckets, `wl tree` timeline). Change a node's PARA role later with
`wl node edit <id> --para <role>`; set or clear other classifications with `wl set type.<x>` /
`wl prop rm <id> type.<x>`. Inspect the classification vocabulary in use with `wl types`.
