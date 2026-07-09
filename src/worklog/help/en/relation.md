---
title: relation — link one task to another (block / split / related)
category: command
see_also: prop, ancestors, node, show
---
A **relation** connects two tasks *across* the tree — "this task blocks that one", "this
task split out that one", "these are related". It's different from **ancestors**
(`wl ancestors`), which is the parent/child *hierarchy*: a relation expresses derivation,
association, or dependency between peers, not containment.

  wl relation 42                     # list #42's relations
  wl relation 5 block 8              # #5 blocks #8 (#8 can't start until #5 is done)
  wl relation 42 split 17 18         # #42 split out #17 and #18
  wl relation 42 related 7 9         # #42 relates to #7 and #9
  wl relation 42 7 9                  # same — `related` is the default type
  wl relation 5 block 8 --rm         # remove that relation

At creation, `wl add` takes a compound `--relation` so a new node links up in one shot
(repeatable): `wl add "epic: revamp billing" --relation 'split 17' --relation 'related 7 9'`.

Three types:

  block        this task blocks the given task(s) — directed, upstream → downstream,
               a real dependency; cycles are rejected (see below)
  split        this task split out the given task(s) — directed, upstream → downstream
  related      generally related (no direction)

**Single-write.** `wl relation 42 split 17` writes ONLY on #42 — the reverse is never
stored, it's derived at read time: #17's `wl show` computes and displays `=split-from
#42` on the fly, so it can never go stale. There's no command to write `=split-from` (or
`=blocked-by`) directly — they're views, not fields. `related` works the same way
(single-write per invocation): `wl relation 42 related 7` and a separate `wl relation 7
related 42` are two independent edges, neither implied by the other.

**`block` is cycle-checked.** Adding a `block` edge that would close a dependency loop
(e.g. #5 blocks #8, then trying `wl relation 8 block 5`, directly or through a longer
chain) is rejected with a clear error instead of being written — a cycle would leave two
tasks each permanently waiting on the other. `split` and `related` are NOT cycle-checked:
`split` is a loose lineage marker with no logic riding on it, and `related` is symmetric
by nature.

Under the hood these are `relation.*` props (`relation.block` / `relation.split` /
`relation.related`), each a comma-separated id list — so `wl ls --prop relation.block`
and the `relation.` namespace prefix work like any other prop (see `wl help prop`).
Prefer `wl relation` over editing the props by hand: it validates the ids, keeps the
direction consistent, and enforces the `block` cycle check. The block shows in
`wl show`, and `wl show -o json` carries a resolved `relations` object.

## How the relation block displays (nested under props)

Since relations ARE `relation.*` props, `wl show` nests them under `props:` as a
`relation:` sub-block (named for the namespace) — with their own richer display, not flat
key=value rows. STORED edges (`block:` / `split:` / `related:`) render first, plain. Then
DERIVED rows: `=blocked-by` (the reverse of another node's `block`), `=split-from` (the
reverse of another node's `split`), and `=backrels` — the ids of OTHER nodes whose text
mentions this one (a `#<id>` or `WL#<id>` reference in their log/body), OR a one-sided
`related` edge from another node that never got reciprocated:

    props:
      github.repo = owner/repo
      relation:
        related:      #7 some related task
        =blocked-by:  #5 must finish before this can start
        =split-from:  #3 the epic this was carved out of
        =backrels:    #12 #34          # inbound references / one-sided `related` edges
        =ready:       false            # every direct block-blocker done/terminal?
        =waiting:     #5               # the direct blockers still open (empty = ready)

Every row after the stored ones is **machine-derived**, not a stored property: computed
on the fly from the live graph (or by scanning text for `=backrels`; a `PR#`/`LUM-` run
does NOT count, so a GitHub PR / Linear ref isn't mistaken for a node ref). To set it
apart from the real stored relations above, derived rows are marked with a leading `=` and
rendered italic + dim. (General wl convention: a leading `=` + italic means "computed /
read-only / not a stored field".) In `wl show -o json` these are the `blocked_by` /
`split_from` fields (nested under `relations`) and the top-level `backrels` array.
There's no command to edit them directly — write the relation (or a `#id` reference) on
the OTHER side and it appears.

**`=ready` / `=waiting` are `wl show`-only, and only for nodes touching `block`.** They're
the exact same computation `wl relation ready` uses (`graph.node_ready_view`), surfaced
inline on the node itself so a plain `wl show` already answers "can I start this?" without
a separate query — but they only appear when the node blocks something or is blocked by
something; a node with no `block` edge at all shows neither row (nothing to compute). They
show up in `wl show -o json`'s `relations.ready` / `relations.waiting`, but NOT in `wl
relation <id>`'s own output (text or `-o json`) — that command stays the plain stored +
derived-reverse contract; use `wl relation ready <id>` when readiness itself is the ask.

## ready / deps / unclaimed — dependency queries (built on `block`)

  wl relation ready 8                   # is #8 ready? + what it unlocks downstream
  wl relation ready 8 --chain           # ...plus the full upstream chain
  wl relation deps 8                    # #8's full cascaded block-dependency graph
  wl relation deps                      # every node participating in any block edge
  wl relation unclaimed 1               # open, unclaimed tickets under #1's subtree
  wl relation unclaimed                 # ...globally

`ready` and `deps` walk the **block graph** (`relation.block` edges), never the tree.
`ready <id>` answers two questions at once: is `<id>` itself ready (all its direct
blockers done/terminal), and — since finishing a task is usually why you're asking —
which of `<id>`'s downstream tasks (what it transitively blocks) just became unblocked.
Unlocking is checked layer by layer: a downstream task only shows as unlocked once ALL
of its own direct blockers are done, so a task two hops away whose immediate blocker is
still open does NOT show up yet (it will, once that blocker clears). `--chain` adds the
full upstream chain (everything that transitively blocks `<id>`) for lineage context.
`ready` always needs an anchor id — most tasks carry no `block` relation at all, so a
global scan would drown the few that matter in noise.

`deps [<root>]` is the cascading counterpart to `wl show`'s `=blocked-by`, which only
ever shows the direct layer above a node: give it a root and it lists that root plus
everything it transitively blocks, each with its own direct `blocks` list and live
ready/blocked state; omit the root and it lists every node that participates in any
`block` edge, globally. A root with zero `block` edges of its own still shows up alone
(ready, blocking nothing) — it isn't filtered out.

`unclaimed [<root>]` is different: it's a backlog query, scoped by the **tree** (parent/
child descendants of `<root>`), not the block graph — a claim isn't a dependency-graph
concept. It lists every open (non-terminal) node under `<root>` (or globally) that has
no active claim (never claimed, or a claim gone stale — see below). Combine with `ready`
for a wayfinder-style frontier: tasks that are both workable right now AND unclaimed.

## claim / unclaim — who's working a ticket (orthogonal to block)

  wl relation claim 42                  # claim #42 (as <agent>:<session id>, like wl agent)
  wl relation claim 42 --as alice       # claim under a custom free-string identity
  wl relation unclaim 42                # release your own claim
  wl relation unclaim 42 --force        # release someone else's claim too

`claim`/`unclaim` are about **who's currently working a ticket**, not whether it CAN be
worked (that's `block`/`ready`) — the two are independent axes; a wayfinder-style
frontier is `ready ∩ unclaimed`. Stored as plain `claimed_by` (free-string identity) /
`claimed_at` (timestamp) props — not `relation.*`, since a claim isn't a task↔task edge —
so they show up in `wl show`'s ordinary `props:` block, not the `relation:` section.

Identity defaults to `<agent>:<session id>` (the same derivation `wl agent` uses); pass
`--as IDENTITY` for a free-form override (a human name, a custom label — wl doesn't lock
the format). `claim` fails if the ticket is already claimed by a DIFFERENT identity and
that claim is still fresh; claiming again under your OWN identity is a no-op that just
refreshes `claimed_at` (a heartbeat). `unclaim` on an already-unclaimed ticket is a
friendly no-op, not an error; releasing someone else's fresh claim needs `--force`.

**Stale claims (>24h) are free for anyone** — a claim that old is treated as abandoned:
`claim` can take it over and `unclaim` can release it, neither needs `--force` nor even
an identity for `unclaim` (nothing left to compare against). wl doesn't verify whether the
session behind a claim is still alive — this fixed timeout is the whole staleness story;
if a session needs to signal "still working", it re-claims periodically.
