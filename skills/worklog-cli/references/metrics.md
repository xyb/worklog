# Recording metrics the right way

A `metric` is a structured datapoint (a number, a measurement, or a `checkin`
marker) that hangs off a log. The command grammar lives in `features.md`; this
file is the *how to record it well* guidance — a worked example plus the two
mistakes that make metric data messy.

## A worked example: a habit that carries a number

Say you want a recurring habit and, each day, to record both that you did it and
a measured value. Run it as one connected flow.

**1. Create the habit node** (recurring, so it re-appears each day):

```bash
wl add "daily walk" --prop type.habit --parent 12 --recur daily
# → #42
```

**2. Pin the metric convention on the node** so every later record copies the
same tag/unit instead of re-guessing it:

```bash
wl set 42 metrics "steps(count) / distance(km)"
```

**3. Check in with the number — one command, same log.** The `checkin` marker and
the value land on a single log; `--keep-status` stops a plain log from promoting
the habit TODO→DOING:

```bash
wl log 42 "morning loop" --metric checkin --metric 'steps 5200 count' --keep-status
```

`--metric` is repeatable; each is `'tag [value] [unit]'` (`'steps 5200 count'`),
or a bare marker with no value (`--metric checkin`). `wl day` now renders `[x]`
for it today, and the node stays recurring.

**4. Look back:**

```bash
wl metric ls 42                    # this node's datapoints (each with its ⟶ #L owning log)
wl show 42                         # folds the day's datapoints under the node; shows last check-in
```

Repeat step 3 each day. Because the convention is pinned in step 2, you never
have to remember whether it was `steps` or `step_count`, `km` or `m`.

## Rule: reuse the node's convention, don't invent a new tag/unit

Before recording, check the two places that already hold the answer, and copy
what's there:

```bash
wl prop ls 42 | grep -i metric    # the node's metrics prop = the authoritative convention
wl metric ls 42 --all             # past datapoints — the tags/units actually used
```

Recording the same measurement under drifting names (`steps` one day,
`step_count` the next; with a unit sometimes, without it others) fragments the
series and breaks rollups. If nothing is pinned yet, do the `wl set 42 metrics
"..."` from step 2 first.

## Trap: retrofitting a number needs `--on-log`

If you already checked in with `wl tick` and only later want to attach a number,
hang it on **that check-in's log**. A bare `wl metric add` creates its own
carrier log, splitting the `checkin` and the number across two logs seconds
apart:

```bash
# grab the #L of today's checkin log, attach the number to it
wl metric add 42 steps 5200 --on-log $(wl metric ls 42 -q | grep checkin | tail -1 | grep -oE '#L[0-9]+')
```

Prefer one step over two: for a fresh check-in use the step-3 `wl log ... --metric`
form — same log by construction, nothing to split. `--on-log` is only for
after-the-fact fixups.

## `checkin` is the done-signal, not "a log exists"

A recurring item (habit or recurring task) counts as done-today from a
`tag=checkin` metric, not from any log that day:

- `wl log 42 "morning loop"` alone does **not** mark it done (no checkin) — use
  `wl tick`, or `wl log ... --metric checkin`.
- Recording only the number, without `checkin`, does not mark it done.
- `wl show 42` shows `last check-in: <date>`; `wl ls --not-checked-in N` lists
  recurring items not checked in within N days.

## A plain measurement (no check-in)

For a one-off or non-habit datapoint, no `checkin` is needed — just add the
number, either standalone or alongside a progress note:

```bash
wl metric add 42 distance 6.4 --unit km      # standalone datapoint on a fresh carrier log
wl log 42 "evening loop, felt slow" --metric 'distance 6.4 km'   # note + number on one log
```

## Edit / delete / cross-node

```bash
wl metric ls --tag steps --all     # find a tag across all nodes (before renaming / rollup)
wl metric edit #M711 --value 5400
wl metric rm #M711                 # also drops an emptied auto-carrier log
wl metrics                         # every metric tag in the DB + frequency
```
