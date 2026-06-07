---
title: relog — rewrite a log's body or time
category: command
see_also: log, unlog, show
---
`wl relog #L<id>` rewrites an existing log (= `wl log edit`) — fix the body or the timestamp.
  wl relog #L282 "fixed content"
  wl relog #L282 --at 14:30          # change only the time
  wl relog #L282                     # no args → open $EDITOR
Find a log's `#L<id>` in `wl show <id>` / `wl logs`. Delete one with `wl unlog`. See `wl help log`.
