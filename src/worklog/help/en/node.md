---
title: node — the single unit of everything
category: concept
see_also: status, priority, para, add
---
In wl, **everything is a node**, and all nodes live in one tree. Tasks, projects,
areas, habits, meeting notes (meetlog), and even the time skeleton
(year / quarter / month / week / day) are all the same underlying thing — a node.

A node has:
  • a title and a `kind` (-k): task · project · area · habit · meetlog · day/week/…
  • a priority (-p A/B/C — see `wl help priority`) and a status (see `wl help status`)
  • a parent (--parent), which is how the tree is built
  • created/closed timestamps

Around a node hang the other entities (each its own topic):
  log (progress entries) · tag (labels) · prop (static fields) · goal (goal/summary logs)
  metric (datapoints) · link (vault docs) · sched (planned days) · clock (time intervals)

CRUD (the metric-style `wl node <verb>` group; add/ls/show also have top-level shortcuts):
  wl node add "title" -k task    # = wl add
  wl node edit 42 --title "…" -p A   # own fields: title / priority / kind / body / scheduled / deadline
  wl node reparent 42 103        # change the real parent (103, or none/root to detach)
  wl node rm 42                  # soft-delete #42 + subtree (reversible tombstone)

`node edit` touches only a node's *own* fields — not status, parent, or tags: use
`wl done`/`wl cancel`/… for status, `wl node reparent` for the parent, and `wl tag` for tags.
Nest with `--parent` to build area ▸ project ▸ task — see `wl help para`.
