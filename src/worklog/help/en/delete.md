---
title: delete — removal is reversible (soft-delete)
category: concept
see_also: node, log, status
---
Removing things in wl is a **soft-delete**: `wl node rm` / `wl unlog` / `wl unlink` /
`wl tag rm` set a tombstone instead of erasing, and reads skip tombstoned rows — so an
accidental removal is recoverable (clear the tombstone), and re-adding a removed tag or link
revives it.

  wl node rm 42      soft-delete a node + its whole subtree (reversible)
  wl unlog #L9       soft-delete a log entry

Separately, DONE / CANCELED nodes are merely **hidden** (not deleted) — show them with
`--all`. There's no everyday hard-delete; that's reserved for migrations / tests.
