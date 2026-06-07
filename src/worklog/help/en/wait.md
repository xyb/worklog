---
title: wait — mark blocked (waiting on someone/something)
category: command
see_also: status, defer, reopen
---
`wl wait <id>` sets status WAIT — blocked on others or external input — and auto-closes any
open clock (so a blocked task doesn't keep timing). Accepts several ids.
  wl wait 42 --note "waiting on review"
Resume with `wl reopen <id>` (→ TODO) or just `wl start`/`wl log`. WAIT differs from `wl
defer` (LATER = your own "later", not externally blocked). See `wl help status`.
