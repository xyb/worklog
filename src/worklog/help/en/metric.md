---
title: metric — structured datapoints (numbers, check-ins)
category: command
see_also: log, checkin, day
---
A **metric** is a structured datapoint hanging off a log: a number (`glucose 5.4 mmol/L`,
`pullups 8`) or a marker (`checkin`). Unlike free-text logs, metrics are queryable series.

  wl metric add 42 glucose 5.4 --unit mmol/L   # a measurement (creates a carrier log)
  wl metric add 42 pullups 8                    # a number
  wl metric ls 42                               # list · metric edit #M7 · metric rm #M7
  wl log 42 "did set" --metric 'pullups 8'      # attach inline while logging

Every metric must hang off a log (the carrier). A "done today" check-in is a `tag=checkin`
metric written by `wl tick` / `wl checkin` (not just "a log exists") — see `wl help checkin`.
Metrics are referenced as `#M<id>`.
