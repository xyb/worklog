---
title: link — connect a node to a vault doc
category: command
see_also: node, add
---
A **link** points a node at an Obsidian vault document (by name) — the bridge between the
execution layer (wl) and the knowledge layer (your notes). wl only stores the doc name; it
doesn't sync content.

  wl link 42 "Project hub doc"   # link #42 to a vault doc
  wl link 42 43 "shared topic"   # several nodes at once
  wl link ls 42                  # list #42's links
  wl unlink 42 "old doc"         # remove one (= wl link rm)

An outer `[[ ]]` is stripped automatically, so `[[X]]` and `X` store the same; re-linking
the same doc is idempotent. Attach at creation with `wl add "..." --link "doc name"`.
Keep the link name identical to the vault note's `[[wikilink]]` so the two stay aligned.
