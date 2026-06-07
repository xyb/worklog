---
title: migrate — apply pending schema migrations
category: command
see_also: admin
---
`wl migrate` applies any pending SQL migrations explicitly. You rarely need it — migrations
auto-run on every command; this is the manual form (and what to run if a migration failed and
you've fixed it). See `wl help admin`.
