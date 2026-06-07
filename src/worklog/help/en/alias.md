---
title: alias — make your own command shortcuts
category: command
see_also: admin
---
`wl alias` maps a short name to a command so you can type less.

  wl alias add d day        # now `wl d` == `wl day`
  wl alias add c checkin
  wl alias ls               # list configured aliases
  wl alias rm d

Stored in `~/.config/worklog/aliases.ini`; the target must be a real command and an alias
can't shadow one. A change takes effect on the next `wl` run (aliases wire into the parser
at startup).
