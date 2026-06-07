---
title: log — record progress on a node
category: command
see_also: add, done, day, node
---
A **log** is a timestamped progress entry on a node — the running history of what
happened. `wl log <id> "<what happened>"` appends one (and auto-moves a TODO to DOING,
since "logging means you're working on it"; suppress with --keep-status).

  wl log 42 "drafted the intro section"        # progress now
  wl log 42 "..." --date yesterday --time 14:30 # backfill a past entry (also -d)
  wl log 42 "..." --keep-status                 # don't change status (e.g. while WAIT)
  wl log ls 42                                  # list #42's logs (full view: wl logs --id 42)

Each write is kept (history), so a node accumulates its story; `wl day` / `wl show` /
`wl tree` show recent logs. Edit one with `wl relog #L<id>` (= `wl log edit`), delete with
`wl unlog #L<id>` (= `wl log rm`) — logs are referenced as `#L<id>`.

Related: `wl tick` is a one-key check-in log for habits; `wl add ... --log "..."` creates a
node and logs in one shot; attach a number with `--metric 'pullups 8'`.
