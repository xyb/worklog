---
title: date — date-metadata CRUD (= the wl dateinfo store)
category: command
see_also: dateinfo, day
---
`wl date set / ls / rm / import` is the explicit CRUD group for date labels (holiday /
vacation / makeup), shown in the `wl day` header. `wl dateinfo` is the polymorphic shortcut
over the same store.
  wl date set 2026-05-01 "Labor Day"
  wl date ls / rm 2026-05-01 / import holidays.json
See `wl help dateinfo`.
