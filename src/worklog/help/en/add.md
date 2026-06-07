---
title: add — create a node (task/project/area/…)
category: command
see_also: node, para, priority, day
---
`wl add "<title>"` creates a node. It's the shortcut for `wl node add`. The kind defaults
to `task`; use `-k` for others (see `wl help para` for area/project/task).

Common forms:
  wl add "ship the Q3 report"                 # simplest — a task
  wl add "review the PR" -p B -t work --sched today
  wl add "Website revamp" -k project -p A -t work       # → e.g. #42
  wl add "draft the homepage copy" --parent 42          # a task nested under it
  wl add "[meetlog] 09:30 tech sync" -k meetlog -p A -t work,meeting --parent <day_id>  # a meeting note

Compound flags do several steps in one shot (a retrospective entry):
  wl add "fixed the login bug" -p B \
    --log "root cause: stale token (PR#42)" --done --at 14:30

Key options (full list: `wl add -h`):
  -k kind · -p A/B/C priority · -t tags (comma, AND) · --parent <id> nest
  --sched <day> plan it (shows "planned" in wl day) · --deadline <date>
  --log "..." add a log now · --done (+ --at <ts>) close it now · --link "<doc>"
  --metric 'tag value unit' attach a datapoint

Related: `wl log <id>` adds to an existing node (doesn't create one); `wl tick <id>`
is a one-key check-in for a habit. See `wl help planning` to schedule the work.
