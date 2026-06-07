---
title: init — create the database
category: command
see_also: admin, config
---
`wl init` creates the SQLite database (default `~/.local/share/worklog/worklog.db`; override
with `--db` or `$WORKLOG_DB`). Safe to re-run — it skips if the DB already exists. Migrations
auto-run afterward. See `wl help admin`.
