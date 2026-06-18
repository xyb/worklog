---
title: import — bulk-load a JSON document (the AI entry point)
category: command
see_also: apply, add, log
---
`wl import <file.json>` loads many nodes at once from a single JSON document — the main way
an AI (or a script) populates a day's worklog without dozens of `wl add` calls.

  wl import day.json              # load
  wl import day.json --dry-run    # preview without writing
  wl import - < day.json          # from stdin

Shape: `{"add": [ {ref, title, priority, tags, props:{…}, children:[…], logs:[…]}, … ],
"update": [ {id, status, add_tags, …}, … ]}`. Classification is set through `props` with the
`type.*` keys — `{"type.para":"project"}` (or `area`/`task`), `{"type.date":"day"}` (or week /
month / …), `{"type.habit":"true"}`, `{"type.meetlog":"true"}`; a bare node (no `type.*`) is a
plain task. `parent_ref` wires children within the batch; a log body like `"2026-05-06 did X"`
lands on that date, not today. The whole import is one transaction — any error rolls it all back
(so a bad `parent_ref` writes nothing).

For lighter, line-oriented edits that mirror wl's own output, see `wl help apply`.
