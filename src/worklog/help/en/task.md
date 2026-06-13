---
title: task — a concrete action (PARA)
category: concept
see_also: para, project, area, status
---
A **task** is a concrete action — the leaf you actually do (e.g. "draft the homepage copy"). It
is the DEFAULT kind: `wl add "..."` with no `-k` creates a task.

  wl add "draft homepage copy" --parent 11     # a task under project #11
  wl add "pay the invoice"                     # a loose task (no parent)

Tasks usually live under a project, but can hang under an area (one-off work) or stand alone.
Track progress with `wl log <id> "..."`, time with `wl start` / `wl stop`, plan with
`wl sched <id> <day>` (shows "planned" in `wl day`); close with `wl done <id>`, or
`defer` / `wait` / `cancel`. Set priority with `-p A|B|C` and the work/personal bucket with
`-t work|personal`. A task is the usual target of a `goal` (`wl goal "..." <task-id>`).

See `wl help para` for the model, `wl help status` for the lifecycle, `wl help log` for logging.
