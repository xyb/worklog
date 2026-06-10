---
title: tree — the structure, top-down
category: command
see_also: ls, day, para, node
---
`wl tree` shows nodes as a tree. The default is an overview: the time timeline expanded to
today (year ▸ quarter ▸ month ▸ week ▸ today + today's tasks) plus your areas listed by
name — about 30 lines, so it never floods.

  wl tree                      # the overview
  wl tree --root <id>          # the subtree under one node (e.g. an area's projects+tasks)
  wl tree --root <week/month>  # a time node's per-day activity
  wl tree --depth N            # expand N levels from lifetime
  wl tree --by project/tag/direction   # regroup by a dimension
  wl tree -t work              # prune to matching nodes + their ancestor paths
  wl tree --root <id> -o json  # the subtree as nested JSON {…node, children:[…]}

`-o json` emits the structural subtree (`--root` → that subtree; else the top-level forest),
depth-bounded, each node carrying a `children` array — the text-view `--by` / tag filters don't
apply to it (for filtered flat data use `wl ls -o json`).

Use `wl tree` to see shape (how things nest — see `wl help para`); use `wl ls` for a flat
sorted list and `wl day` for one day's plan + activity.
