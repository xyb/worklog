# Props: modelling and querying custom attributes

A prop is a **single-value** key/value attached to a node. Primary key is `(node_id, key)` — one value per key per node. Props are how a node says *what it is* and *what it points at*, without adding columns.

## Discover before you invent

Run this first, every time, before adding a prop key that doesn't obviously exist yet:

```bash
wl props                  # every prop key in use, by frequency
wl types                  # the type.* classification keys + their values
wl props -o json          # [{"key": "owner", "count": 1}, …]
```

Reusing an existing key is almost always right. Inventing a near-duplicate (`owner` vs `owned_by`, `repo` vs `repository`) silently splits the data: every later query by one key misses the rows filed under the other, and nothing errors. Same failure mode as metric tags (see `metrics.md` § reuse the node's convention).

## Namespaced keys

Dot-group related props under a shared prefix. Built-in examples:

```
agent_session.claude    agent_session.cursor      # written by wl agent
relation.block          relation.related          # written by wl relation
type.para               type.date                 # classification
```

The same applies to your own families — if you end up with `deploy.host`, `deploy.user` and `deploy.port`, namespace them rather than flattening to `deployhost` / `deployuser`.

**Prefer a dotted key. Default to a namespace, even for the first key of a family.**

Ask "what family does this belong to?" and prefix it — `repo.github`, not `github_repo`; `invoice.number` and `invoice.due`, not `invoicenumber` and `invoicedue`. Reach for a flat key only when the concept is genuinely standalone (see below).

Why the default leans this way, when "it's only one key, keep it simple" sounds reasonable:

- **A flat key can collide with an existing namespace.** A prop named `type` sits next to `type.para` / `type.date` and reads like their parent — it isn't, and every reader has to relearn that. Same for a `relation` prop next to `relation.block`.
- **A key you think is lone often isn't.** `visibility` grows a sibling and someone adds `repo.visibility`; now both exist and every query by one silently misses the other.
- **Retrofitting is expensive, so it doesn't happen.** Renaming means rewriting history and there is no bulk rename, so "namespace it later" reliably means "never".

The cost of the default is real but small: a longer key to type and a little more noise in `wl prop ls`. The cost of getting it wrong is a silent data split you find months later.

Rules:

- **Each full key is still a single-value prop.** `deploy.host` and `deploy.port` are two independent props. The dot is a naming convention, not nesting — there is no sub-object.
- **The namespace buys you prefix lookup**: `wl ls --prop group.` finds the whole family in one query (it matches the prefix, not an exact key). That payoff is what the three reasons above are protecting.
- **Never introduce a flat key whose name is an existing namespace prefix** (`type`, `relation`, `agent_session`, …). That reads as the family's parent and is guaranteed confusion.

### When a flat key is still right

The test is mechanical, not a judgement call: **would a prefix query ever be useful here?** If no sibling could share the prefix, the dot buys nothing and only adds noise. `owner`, `url`, `started` are fine flat. So are wl's own `claimed_by` / `claimed_at`: a claim is one concept with two fields, and nothing else would ever live under a `claim.` prefix. The moment you can name a plausible sibling, prefix it.

### This rule does NOT apply to metric tags

Metric tags are a different vocabulary with a different query surface. `wl metric ls --tag X` matches the tag **exactly** — there is no prefix lookup — so a dot buys nothing and just makes tags harder to type. Keep metric tags flat and readable (`pullups`, `weight`); for a family, use a shared word (`bp_systolic` / `bp_diastolic`) and let `wl metrics` group them by eye. Recording rules live in `metrics.md`.

## Three matching modes

Every prop-based feature (filter, summary, stats) must support all three:

```bash
wl ls --prop type.date=day       # exact:   key = value
wl ls --prop type.meetlog        # exists:  key present, any value
wl ls --prop relation.           # prefix:  whole namespace (trailing dot)
wl ls --prop type.para=project --prop relation.block    # repeat = AND
```

If you add a feature that reads props, exact-only is a bug.

## prop vs log vs metric

Pick by the shape of the data, not by convenience:

| Shape | Store as | Example |
|---|---|---|
| Single value describing the node | **prop** | `repo.visibility=public`, `owner=alice` |
| Something that happened at a moment | **log** | "reviewed the design, blocked on X" |
| A number in a series you want to trend | **metric** | weight, reps, check-ins |

Prefer a metric over widening a log with new fields. A value you will want to *filter or group by* wants to be a prop; a value you will want to *plot over time* wants to be a metric.

## Reserved fields are not props

`status` / `priority` / `title` / `parent` / `tags` / `scheduled` / `deadline` are real columns. `wl set` and `wl prop set` reject them, and import does too. This is deliberate: a shadow prop with the same name as a real column makes `wl show` display two conflicting values and every query ambiguous. Use the real commands instead — `wl done` / `wl tag` / `wl node reparent` / `wl sched`.

## Reading and writing

```bash
wl prop set <id> <key> <value>     # set / overwrite
wl set <id> <key> <value>          # same thing, shorter (what metrics.md uses for the `metrics` prop)
wl prop ls <id>                    # this node's props
wl prop rm <id> <key>              # remove
wl show <id>                       # props shown in context
wl show <id> -o json               # machine-readable
```

Both setters route to the same store and enforce the same reserved-field guard below.

## Built-in namespaces worth knowing

| Namespace | Written by | Meaning |
|---|---|---|
| `type.*` | `wl add --para/--prop` | Classification. `type.para` (area/project/task), `type.date` (day/week/month/quarter/year/lifetime), plus any user-defined `type.<x>` |
| `relation.*` | `wl relation` | Task↔task edges (`relation.block` / `.split` / `.related`), stored as comma-separated id lists, so `wl ls --prop relation.block` works |
| `agent_session.*` | `wl agent` | Live pointer from an agent runtime's session to the node it is working (`agent_session.claude`, `agent_session.cursor`) |

`claimed_by` / `claimed_at` are deliberately **flat**, not `relation.*` — a claim is not a task↔task edge.

## Retrofitting a namespace

Renaming a prop key means rewriting history, and there is no bulk rename — which is exactly why the namespace has to be decided when the key is *born*, not when the family shows up. If you do find yourself with a flat key and a namespaced sibling for the same concept, fix both in one pass (`wl prop set` the new key, `wl prop rm` the old, per node) and write down which key won, so the loser doesn't get re-introduced later.
