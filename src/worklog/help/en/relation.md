---
title: relation — link one task to another (split / related)
category: command
see_also: prop, ancestors, node, show
---
A **relation** connects two tasks *across* the tree — "this task was split out of that
one", "this split into those", "these are related". It's different from **ancestors**
(`wl ancestors`), which is the parent/child *hierarchy*: a relation expresses derivation or
association between peers, not containment.

  wl relation 42                     # list #42's relations
  wl relation 42 split-from 17       # #42 was split out of #17
  wl relation 17 split-into 42 43    # #17 split into #42 and #43
  wl relation 42 related 7 9         # #42 relates to #7 and #9
  wl relation 42 split-from 17 --rm  # remove (from both sides)

Three types. `split-from` and `split-into` are inverses; `related` is symmetric:

  split-from   this task was split out of the given task(s)
  split-into   this task was split into the given task(s)
  related      generally related (no direction)

**Both sides are written.** `wl relation 42 split-from 17` also records `split-into 42`
on #17, so either node's `wl show` displays the connection. The view additionally derives
the reverse from other nodes' data, so even a hand-set one-sided value still reads
bidirectionally.

Under the hood these are `relation.*` props (`relation.split_from` / `relation.split_into` /
`relation.related`), each a comma-separated id list — so `wl ls --prop relation.split_from`
and the `relation.` namespace prefix work like any other prop (see `wl help prop`). Prefer
`wl relation` over editing the props by hand: it keeps both sides consistent and validates
the ids. The block shows in `wl show`, and `wl show -o json` carries a resolved `relations`
object.
