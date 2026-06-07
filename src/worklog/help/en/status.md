---
title: status — a node's state and its marker
category: concept
see_also: node, priority, day
---
Every node has a status, shown as a one-character marker in `wl ls` / `tree` / `day`:

  [ ]  TODO       not started
  [/]  DOING      in progress (logging on a TODO auto-moves it here)
  [x]  DONE       finished (sets closed_at)
  [>]  LATER      deferred / set aside (also DEFERRED)
  [?]  WAIT       blocked on someone or something external
  [-]  CANCELED   dropped / no longer doing

How status changes:
  • `wl log <id> "..."`     TODO → DOING ("logging means working"); suppress with --keep-status
  • `wl done <id>`          → DONE              `wl cancel <id>`  → CANCELED
  • `wl wait <id>`          → WAIT (auto-closes any open clock)
  • `wl defer <id> <when>`  → LATER + a rough scheduled hint
  • `wl reopen <id>`        undo DONE/CANCELED/WAIT/LATER back to TODO

DONE and CANCELED are hidden by default in lists; show them with `--all` (or
`--show-canceled` for just canceled). Prefer `[/]` over claiming `[x]` when something
is only "mostly done" — it's the honest marker.
