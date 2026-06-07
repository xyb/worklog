---
title: show — full detail + timeline for one node
category: command
see_also: focus, log, day
---
`wl show <id>` prints everything about a node: metadata (status / priority / parents / tags /
links / props) plus a merged timeline (created / scheduled / closed / logs). Accepts several ids.

  wl show 42
  wl show 42 -q                  # brief: skip the timeline
  wl show 42 --timeline-tail 20  # longer timeline (--all-timelines for full)

This is where you read a log's `#L<id>` or a metric's `#M<id>` to edit them. For the up/down
tree context use `wl focus`; for a whole day across nodes use `wl day`.
