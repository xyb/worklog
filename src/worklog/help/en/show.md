---
title: show — full detail + timeline for one node
category: command
see_also: focus, log, day, output
---
`wl show <id>` prints everything about a node: metadata (status / priority / parents / tags /
links / props) plus a merged timeline (created / scheduled / closed / logs). Accepts several ids.

  wl show 42
  wl show 42 -q                  # brief: skip the timeline
  wl show 42 --timeline-tail 20  # longer timeline (--all-timelines for full)
  wl show 42 -o json             # machine-readable; pipe to jq / a script

**`-o json`** prints the node + all its relations (tags, ancestors, props, links, schedule,
children, logs, metrics, clock) as one JSON object — an array when several ids are given — with
the full timeline (no elision). Field names mirror the DB columns and are stable enough to script
against; `*_at` values are **UTC** instants, `*_date` are local calendar days. Lets an AI or a
shell pull an exact field instead of parsing the text view.

This is where you read a log's `#L<id>` or a metric's `#M<id>` to edit them. For the up/down
tree context use `wl focus`; for a whole day across nodes use `wl day`; for just one node's
log stream use `wl logs --id <id>`.
