---
title: logs — the flat log stream across nodes
category: command
see_also: log, day, find, window, output
---
`wl logs` lists log entries as a flat, time-ordered stream (task title + one line per log) —
the cross-cutting view of progress, vs `wl day`'s single structured day or `wl log`'s
single-node history.

  wl logs today / yesterday / week / recent   # presets
  wl logs --id 42 43 --tail 5                  # last 5 for one or more tasks
  wl logs --match "shipped"                    # body substring; all history unless a window is set
  wl logs --id 42 --log-format full            # full title, tag, and body; newlines kept
  wl logs --since 2026-06-01 --by-task         # aggregate by task
  wl logs --group day --by project             # group by day → project → task
  wl logs -d 2026-06-01                         # a single day (also --date)
  wl logs --id 42 -o json                       # machine-readable array of log rows
  wl log show L282 L283                         # one or more logs, full body, newlines kept

Defaults to the last 7 days to avoid flooding; widen with the window flags (`wl help window`).
`--id` or `--match` with no window searches all history. The window is the **local** calendar
day of each log, not the stored UTC date.
`-o json` emits the matching log rows (id / node_id / logged_at / tag / body / node_title) as a
JSON array, ignoring the text grouping/tail flags — for scripts and `jq`. `logged_at` is local
time to the second, the same instant the text view prints. `--log-format full` does not clip
the title, prints the tag (`note` when the log has none), and keeps newlines in the body.
