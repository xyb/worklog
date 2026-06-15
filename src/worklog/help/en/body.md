---
title: body — node.body vs log.body (two separate TEXT fields)
category: concept
see_also: log, add, node
---
"Body" names two different TEXT fields — easy to confuse because they share the word:

  node.body   a node's long description (optional, may be empty) — definition-of-done,
              motivation, context. One per node, edited in place.
  log.body    one log entry's text (required) — the running history. Many per node;
              each `wl log` appends one, never edited away.

Which command writes which:

  node.body   wl add "title" --body "..."   ·   wl node edit <id> --body "..."
  log.body    wl log <id> "..."   ·   wl relog / wl log edit   ·   wl tick -n "..."
              ·   wl metric add <id> <tag> --body "..."

So `wl add "ship v2" --body "done = deployed + smoke-tested"` sets the node's
description, while `wl log 42 "deployed to prod"` appends a progress entry. They are
independent: a node can have a rich `--body` and zero logs, or many logs and no body.

`wl show` prints node.body in the meta block (its own `body:` line) and each log.body
in the timeline (one line each, truncated — `wl log show #L<id>` reads one in full).
