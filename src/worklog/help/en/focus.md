---
title: focus — one node with its context (up + down)
category: command
see_also: show, tree, day
---
`wl focus <id>` shows a node together with its upstream path (ancestors) and downstream
subtree — the "where does this sit, and what's under it?" view.

  wl focus 42
  wl focus 42 --depth 2 --related

Neighbors: `wl show <id>` is one node's detail + timeline (no tree); `wl tree --root <id>`
is just the subtree; `wl ancestors` / `wl descendants` walk one direction only.
