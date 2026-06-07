---
title: dateinfo — label a date (holiday / vacation / makeup)
category: command
see_also: day, planning
---
`wl dateinfo` tags a calendar date with a label (holiday / vacation / working-day swap),
shown in the `wl day` header. The weekday is computed automatically; dateinfo only stores
the extra label.

  wl dateinfo 2026-05-01 "Labor Day"
  wl dateinfo --import holidays.json   # batch {"YYYY-MM-DD":"label"}
  wl dateinfo 2026-05-01 --clear

The explicit CRUD form is the `wl date set / ls / rm / import` group (same store).
