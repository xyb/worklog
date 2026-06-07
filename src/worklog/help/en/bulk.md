---
title: bulk — loading & editing many nodes (AI integration)
category: guide
see_also: import, apply, add
---
Two ways to make many changes at once — the main path for an AI or script to populate or
edit a worklog without dozens of single commands:

  • `wl import <file.json>`  load a structured document (`{add:[…], update:[…]}`) with nested
    children and per-node logs. Best for creating a whole day / project from scratch.
  • `wl apply <file>`        a line-oriented **wl-diff** (`+ add` / `~ update` / `- delete`)
    that mirrors wl's own output. Best for quick edits to existing nodes.

Both take `--dry-run` (preview) and are atomic: validate everything first (bad refs, cycles,
unknown fields), then one transaction — a single error writes nothing. Details:
`wl help import` / `wl help apply`.
