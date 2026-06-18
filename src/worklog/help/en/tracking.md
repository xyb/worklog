---
title: tracking — habits, check-ins & datapoints
category: guide
see_also: checkin, metric, sched, tag
---
Two related ideas:

  • habits   — a node with `type.habit` you do regularly. `wl tick <id>` records "done today"
    (a structured `checkin` metric — not merely "a log exists"); `wl checkin` reviews today's
    not-yet-done habits interactively. Make it recur with `wl sched <id> --recur daily`.
  • metrics  — structured datapoints hanging off a log: a number or a marker.
    `wl metric add <id> glucose 5.4 --unit mmol/L`, or inline `wl log <id> "…" --metric 'pullups 8'`.

"Done today" for a habit = a check-in metric that day, so a stray note never counts as done.
Metrics are queryable series (unlike free-text logs). Details: `wl help checkin` / `wl help metric`.
