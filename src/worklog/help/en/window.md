---
title: window — the shared time-range flags
category: param
see_also: summary, changes, logs
---
Several read commands (`summary`, `changes`, `logs`) take the same time-window flags, with
the same meaning everywhere:

  --since YYYY-MM-DD     start (inclusive)
  --until YYYY-MM-DD     end (inclusive)
  --week  YYYY-Www       a whole ISO week (overrides since/until)
  --month YYYY-MM        a whole month (overrides since/until)

  wl summary --week 2026-W22
  wl logs --since 2026-06-01 --until 2026-06-07
  wl changes --month 2026-06

Omitted, most windowed commands default to a recent span (e.g. `wl logs` = last 7 days) to
avoid flooding. Day-grouping inside these is computed in your local timezone (see
`$WORKLOG_TZ`), so an instant near midnight lands on the right local day.
