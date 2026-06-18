---
title: area — an ongoing responsibility (PARA)
category: concept
see_also: para, project, task
---
An **area** is an ongoing responsibility with no finish line — a long-lived domain you keep
tending (e.g. "Health", "Infra", "Finances"). The PARA "A": unlike a project, it's never "done".

  wl add "Infra" --para area             # a top-level area (→ #10)
  wl add "Health" --para area

Areas sit at the top of the responsibility tree (under the hidden `lifetime` root) and are the
long-lived home for projects (`wl add "..." --para project --parent <area>`). A one-off task with no
project can hang directly under an area. See the shape with `wl tree` (areas head the forest) or
list them with `wl ls --para area`.

Not "done"-able by nature — if an area winds down, `wl cancel <id>` retires it from the active
views. See `wl help para` for the model, `wl help project` / `wl help task` for what lives inside.
