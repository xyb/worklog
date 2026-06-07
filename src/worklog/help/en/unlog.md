---
title: unlog — delete a log entry
category: command
see_also: log, relog, tick
---
`wl unlog #L<id>` deletes a log (= `wl log rm`; soft-delete, reversible).
  wl unlog #L282                     # by log id
  wl unlog --node 39                 # delete #39's latest log today (undo a mistaken tick)
  wl unlog --node 39 --all           # all of #39's logs that day
Edit instead of delete with `wl relog`. See `wl help log`.
