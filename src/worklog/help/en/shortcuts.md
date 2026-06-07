---
title: shortcuts — short forms and how they map
category: concept
see_also: node, prop, meta, alias
---
Many everyday commands are short forms of a canonical `<entity> <verb>`:

  wl add   = wl node add        wl ls = wl node ls        wl show = wl node show
  wl set   = wl prop set  *or*  wl meta set  (key-routed: a meta key → meta, else a prop)
  wl unset = wl prop rm / wl meta rm          wl unlink = wl link rm
  wl relog = wl log edit        wl unlog = wl log rm      wl dateinfo ≈ the wl date group
  wl tag 42 +x  = wl tag add …  (the "default verb": `wl <entity> <id> …` ⇒ add)

Both forms do the same thing — the short one just saves keystrokes, and each `--help` names
its canonical partner. Make your own with `wl alias add d day` (see `wl help alias`).
