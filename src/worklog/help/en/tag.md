---
title: tag — labels on a node (work / personal / …)
category: command
see_also: node, day, ls, add
---
A **tag** is a free-form label on a node. The two that matter most are `work` and
`personal` — they drive the buckets in `wl day` and keep work/personal cleanly separated.

  wl tag 42 +work +urgent      # add tags
  wl tag 42 -urgent            # remove one
  wl tag 42 work               # bare word = add
  wl tag 42                    # no ops = list current tags
  wl tag ls 42 / rm 42 work    # the explicit group verbs

Set tags at creation with `-t`: `wl add "..." -t work,urgent` (comma = AND). Filter any
list/view by tag: `wl day -t work`, `wl ls -t work`, `wl tree -t personal` (all AND).

Tags are the real tag field — distinct from props (`wl set` would only make a shadow
"tags" prop, which is why `wl set <id> tags ...` is refused). For one-of-a-kind static
attributes use a prop instead; for a node's history-preserving goal / summary use `wl goal`.
