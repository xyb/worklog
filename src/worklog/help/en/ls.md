---
title: ls — a flat, sorted list of nodes
category: command
see_also: tree, find, status, priority
---
`wl ls` lists nodes as a flat, sorted list (DONE/CANCELED hidden, capped at 20 by default) —
the `ls -t`/`-S`/`-r`-style view of your work.

  wl ls                        # open items, by priority then id
  wl ls --parent 45            # children of #45 (like ls dir/)
  wl ls --ids 39 41 270        # specific ids, directly (like ls f1 f2)
  wl ls --sort updated --limit 10   # 10 most-recently-logged (like ls -t)
  wl ls --sort created -r --limit 5 # 5 newest
  wl ls --all                  # include DONE/CANCELED, no cap
  wl ls -t work --kind task    # filter by tag / kind / status / priority (shared across views)
  wl ls -p A                   # only P0 (A); -p A,B = A or B; -p P0 == -p A
  wl ls --status TODO,DOING    # status, comma = any-of
  wl ls --recent 7             # only items touched in the last 7 days
  wl ls --unscheduled          # open items with no schedule

Sort dimensions: pri (default) / created / updated (last log) / closed / scheduled / title /
id. For tree structure use `wl tree`; to search text use `wl find`.
