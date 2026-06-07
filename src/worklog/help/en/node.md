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
  log (progress entries) · tag (labels) · prop (static fields) · meta (goal/summary/…)
  metric (datapoints) · link (vault docs) · sched (planned days) · clock (time intervals)

Create one with `wl add "title" -k <kind>`; move it with `wl node reparent`; soft-delete
with `wl node rm` (reversible). Nest with `--parent` to build area ▸ project ▸ task —
see `wl help para`.
