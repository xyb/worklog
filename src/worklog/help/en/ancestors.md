---
title: ancestors — the path from root to a node
category: command
see_also: descendants, focus, tree
---
`wl ancestors <id>` prints the upstream chain — the path from the top-level root down to the
node (its lineage).
  wl ancestors 42
The downstream counterpart is `wl descendants`; both directions at once is `wl focus`.
