---
title: ids — referencing nodes, logs, and metrics
category: concept
see_also: node, log, metric
---
Three id forms appear in wl output:

  42  (or #42)   a **node** — what most commands take (and several at once)
  #L42           a **log** entry — view full with `wl log show`; edit/delete with `wl relog` / `wl unlog` (= `wl log edit` / `rm`)
  #M7            a **metric** — use with `wl metric edit` / `wl metric rm`

Find them in `wl show <id>` (the timeline) and `wl logs`. The letter prefix (L / M)
distinguishes a log/metric id from a plain node id.
