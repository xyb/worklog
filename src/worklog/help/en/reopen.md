---
title: reopen — undo a terminal status back to TODO
category: command
see_also: done, cancel, status
---
`wl reopen <id>` brings a DONE / CANCELED / WAIT / LATER node back to TODO and clears
closed_at — the undo for `done` / `cancel` / `wait` / `defer`. Accepts several ids.
  wl reopen 42
See `wl help status` for the full state machine.
