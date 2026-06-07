---
title: descendants — everything under a node
category: command
see_also: ancestors, focus, tree
---
`wl descendants <id>` lists all nodes beneath a node (its whole subtree, flattened).
  wl descendants 42
The upstream counterpart is `wl ancestors`; for a rendered subtree use `wl tree --root <id>`,
and for up+down context use `wl focus`.
