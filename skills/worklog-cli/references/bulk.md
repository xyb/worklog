# Bulk operations — `wl import` / `wl apply` (the AI's data-loading main path)

When loading a day's worklog or multiple nodes, **use the bulk entry, not dozens of `wl add`
calls.** `--dry-run` first, then apply/import once. Two formats:

## `wl import <file|->` (JSON, complex bulk / deep nesting)

```fish
echo '{
  "add": [
    {"ref":"p","title":"dashboard","props":{"type.para":"project"},"priority":"A","tags":["work","viz"],
     "children":[{"title":"login fix","priority":"A","status":"DONE","tags":["P0"],"logs":["root cause..."]}]},
    {"title":"digestive system","parent_ref":"p","tags":["viz"]}
  ],
  "update": [{"id":14,"status":"DONE","parent":6,"add_tags":["urgent"],"remove_tags":["old"]}]
}' | wl import -
```

- **classification = `props` with `type.*` keys**: `{"type.para":"project"}` (or `area`/`task`), `{"type.date":"day"}` (week/month/…), `{"type.habit":"true"}`, `{"type.meetlog":"true"}`; a bare node (no `type.*`) is a plain task
- `children` nesting (parent id auto-propagates) + `ref`/`parent_ref` (in-batch reference)
- a log entry may carry `"metrics":[{"tag":"glucose","value":5.4,"unit":"mmol/L"}]`; a node may
  carry node-level `"metrics":[...]` (one carrier log → N datapoints)
- `--dry-run` to preview first

## `wl apply <file|->` (wl-diff, same format as `wl` output — lightweight edits for humans/AI)

```
  #6 2026-05-29 Friday            ← anchor: locate existing node as parent, don't modify
+   [x] [#A] morning-check          ← add (indent = child), [x]=DONE; for planned use `wl sched`, not a :planned: tag
+     @log check key points
+     @prop type.para=project       ← classification = @prop type.* (no token on the node line)
~ [x] #14                         ← change #14 status (single-line shorthand)
~ #20                             ← complex update: lock + field operations
  priority A
  +tag urgent
  -tag old
```

Prefixes: `+` add / `~` update / `-` delete / ` ` anchor. `--dry-run` validates + previews.

## ⚠️ Update (~) safety rule

**Only modify the fields that appear or are declared; leave everything else alone.** This is the
guard against "I gave id + name and other fields got wiped."

- Single-line shorthand: `~ [x] #14` only changes status; `~ [#A] #14` only changes priority
  (without a marker, status is untouched); `~ #14 new name` only changes title
- Field operations: `status DONE` / `priority -` (clear) / `parent 6` (move) / `+tag` / `-tag` /
  `prop k=v` / `-prop k` / `+log`
- ⚠️ Each field op **must be indented** (2 spaces) under its `~ #id` lock line. A flush-left
  `parent 6` is a separate top-level line, not part of the update — the parser will reject it and
  tell you to indent.
- Illegal values (priority∉ABC, illegal status, parent missing) caught by validator — **bad data
  never lands**

## What one diff can write

Create: `+ [marker] [#P] title :tags:` (2-space indent nests under the line above). On the following indented lines: `@prop k=v` / `@link doc` / `@log text` / `@metric tag [value] [unit]`.

Update: `~ #id`, then indented ops only. Only declared fields change.

`status` / `priority` / `title` / `parent N` / `scheduled` / `deadline` / `+tag` / `-tag` / `+link` / `-link` / `prop k=v` / `-prop` / `goal <text>` / `summary <text>` / `+metric tag [value] [unit]` / `sched <date>` / `recur <rule>` / `-sched` / `spent <duration>` / `body <text>`

`scheduled` is the rough date column. `sched` / `recur` write a precise schedule row. `goal` / `summary` write the reserved-tag log. `prop relation.block=N` (also `relation.split` / `relation.related`) writes one side; the reverse is derived. `spent 1h30m` records a finished clock.

Other lines: `@date <date> <label>` (a `-` label clears it). `~ #L<id>` edits one log (`body` / `retag` / `at`). `- #L<id>` deletes that log. `- #id` soft-deletes the node and its subtree.

The diff is validated first, then applied in one transaction. A clock that is still running is the exception: `wl start` / `wl stop`.

## Typical workflow: AI handling a day's worklog

1. Parse the day's discussion / notes → assemble a `wl apply` diff or `wl import` JSON
2. `--dry-run` first, verify correctness
3. apply/import once (replaces dozens of commands)
4. `wl day` to review the full day + stats
5. For a weekly report, run `wl changes` / `wl summary --week` to extract material
