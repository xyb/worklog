---
title: planned vs unplanned — how wl day splits your work
category: concept
see_also: sched, defer, day
---
In `wl day`, a task is **planned** or **unplanned** — and this is *derived*, not a tag:

  planned     it has a `sched` entry (or recurrence) firing that day — you committed to it
              via `wl sched <id> <day>`.
  unplanned   it has logs that day but no schedule — you worked on it without planning to.

So the split answers two honest questions at once: what did I intend to do today (planned),
and what did I actually touch (both). A task you only `wl defer`-red to "someday" is NOT
planned for any day (it's a loose backlog item).

`wl day --by plan` (the default) groups by this; a planned task with no log yet appears so
you can see what's still pending. The legacy `:planned:` tag is honored only as a migration
fallback — don't add it; use `wl sched`.
