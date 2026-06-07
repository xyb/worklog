---
title: unlink — remove a vault-doc link
category: command
see_also: link, node
---
`wl unlink <id> "<doc>"` removes one vault-doc link from a node (= `wl link rm`). An outer
`[[ ]]` is tolerated, matching either form.
  wl unlink 42 "old doc"
Add links with `wl link`. See `wl help link`.
