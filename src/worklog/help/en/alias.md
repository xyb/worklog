---
title: alias — make your own command shortcuts
category: command
see_also: admin
---
`wl alias` maps a short name to a command — optionally with arguments — so you can type less.

  wl alias add d day              # now `wl d` == `wl day`
  wl alias add w "day -t work"    # `wl w` == `wl day -t work` (quote a target with args)
  wl alias add p "day -t personal"
  wl alias ls                     # list configured aliases
  wl alias rm d

A target may carry arguments (the git-alias model): `w = day -t work` expands `wl w` to
`wl day -t work`, and any args you type after the alias are appended — `wl w 2026-06-08` runs
`wl day -t work 2026-06-08`. The target's first word must be a real command and an alias can't
shadow one.

Stored in `~/.config/worklog/aliases.ini` (edit it directly or use `wl alias add`). A change
takes effect on the next `wl` run (aliases wire into the parser at startup).
