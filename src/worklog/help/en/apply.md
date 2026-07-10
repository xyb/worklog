---
title: apply — bulk edits in the lightweight wl-diff format
category: command
see_also: import, add, done
---
`wl apply <file>` applies a **wl-diff**: a compact, line-oriented format that mirrors what
wl prints, so you can edit a listing and feed it back.

  + new task              # add (indent with two spaces to nest under the line above)
  ~ #42                   # update node #42 …
    status DONE           #   … its fields (indented field-ops)
    +tag urgent           #   add/remove a tag, +link / -link, prop k=v, parent N
    goal ship it today    #   set the node's goal / summary (reserved-tag log; wl goal / wl recap)
    +metric steps 5000 count  #  a metric datapoint (tag [value] [unit]); bare tag = a marker
    sched tomorrow        #   plan it on a day (sched row = "planned"); recur weekly:Mon,Fri
    -sched                #   clear every schedule row
    spent 1h30m           #   record a completed clock (90 / 90m / 1h30m)
    body free text        #   set the node's body
  - #43                   # delete (soft) node #43

  ~ #L282                 # a LOG target (note the L): rewrite one existing log
    body fixed content    #   … its body (wl relog), retag goal (wl retag), at 14:30
  - #L282                 # delete (soft) that one log (wl unlog)

  + [ ] new health task   # add, with a datapoint at creation:
    @metric glucose 5.4 mmol/L

  wl apply changes.wld            # apply  ·  --dry-run to preview
  wl apply - < changes.wld        # from stdin

Validated as a whole first (bad refs / cycles / unknown fields stop everything before any
write), then applied in one transaction. Use `apply` for quick hand-or-AI edits; use
`wl import` for loading a whole structured day from JSON.
