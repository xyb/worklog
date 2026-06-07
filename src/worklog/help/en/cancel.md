---
title: cancel — drop a task (no longer doing)
category: command
see_also: done, reopen, status
---
`wl cancel <id>` marks a node CANCELED and stamps closed_at — dropped / no longer doing,
the parallel of `wl done`. Accepts several ids and `--log`/`--at`.
  wl cancel 42 --log "deprioritized"
CANCELED is hidden from lists by default (`--all` or `--show-canceled` to see). Undo with
`wl reopen`. See `wl help status`.
