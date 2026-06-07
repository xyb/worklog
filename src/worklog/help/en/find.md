---
title: find — full-text search
category: command
see_also: ls, tree
---
`wl find "<text>"` searches across titles, log bodies, tags, props, and links, and lists the
nodes that match anywhere.

  wl find backup               # any node mentioning "backup"
  wl find "stale token"        # phrase
  wl find anthropic --limit 0  # no cap (default shows the first 20)
  wl find skill --in title,tag # restrict to certain fields (default: all)

Use it when you remember a word but not the id. For a structured list with sorting/filters
use `wl ls`; for the raw log stream in a time window use `wl logs`.

Tip: before creating a new task/log, `wl find` first — merge into an existing node instead
of duplicating it.
