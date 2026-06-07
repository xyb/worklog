---
title: done — mark a node finished
category: command
see_also: status, log, defer
---
`wl done <id>` marks a node DONE and stamps `closed_at`. Accepts several ids at once.

  wl done 42                         # done
  wl done 42 43 44                   # batch
  wl done 42 --log "PR#13 merged"    # close + log the result in one shot (--at for a past time)

Related transitions (see `wl help status`):
  wl cancel <id>    → CANCELED (dropped / no longer doing; also takes --log/--at)
  wl reopen <id>    undo DONE/CANCELED/WAIT/LATER back to TODO (clears closed_at)
  wl wait <id>      → WAIT (blocked); auto-closes any open clock

DONE nodes are hidden from lists by default — show them with `--all`. Be honest: use `[/]`
(keep logging) for "mostly done" rather than claiming `[x]`.
