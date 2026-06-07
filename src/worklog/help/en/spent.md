---
title: spent — record a past duration (no live timer)
category: command
see_also: time, start, clock
---
`wl spent <id> <duration>` adds a clock interval from a duration — for logging time after the
fact, without having run start/stop.
  wl spent 42 90m
  wl spent 42 1h30m
Use `wl start`/`wl stop` for live timing instead. See `wl help time`.
