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

At creation, `wl add` takes a compound `--relation` so a new node links up in one shot
(repeatable, both sides): `wl add "split-out work" --relation 'split-from 17' --relation 'related 7 9'`.

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

## How the relation block displays (nested under props)

Since relations ARE `relation.*` props, `wl show` nests them under `props:` as a `relation:`
sub-block (named for the namespace) — with their own richer display, not flat key=value rows.
The block ends with a `=backrels` row — the ids of OTHER nodes whose text mentions this one
(a `#<id>` or `WL#<id>` reference in their log/body), i.e. "what links here" / backlinks:

    props:
      github.repo = owner/repo
      relation:
        related:   #7 some related task
        =backrels  #12 #34          # nodes whose text references this one

This row is **machine-derived**, not a stored property: it is computed on the fly by
scanning text (a `PR#`/`LUM-` run does NOT count, so a GitHub PR / Linear ref isn't
mistaken for a node ref). To set it apart from the real stored relations above, derived
rows are marked with a leading `=` and rendered italic + dim. (General wl convention: a
leading `=` + italic means "computed / read-only / not a stored field".) In `wl show -o
json` it is the `backrels` array. There's no command to edit it — write a `#id` in a log
and the backlink appears.
