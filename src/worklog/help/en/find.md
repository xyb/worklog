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

Use it when you remember a word but not the id. For a structured list with sorting/filters
use `wl ls`; for the raw log stream in a time window use `wl logs`.
