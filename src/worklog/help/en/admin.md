---
title: admin — setup, config & maintenance
category: guide
see_also: alias, para
---
First run: `wl init` creates the database (default `~/.local/share/worklog/worklog.db`).

Kick the tyres on a populated db without hand-building one: `WORKLOG_DB=/tmp/wl-demo.db wl demo`
seeds a sample "a day with wl" dataset (refuses on any non-empty db, so it never touches real data).

Where things live & current settings: `wl config` (DB path, aliases file, XDG dirs, env).
Point at a different worklog with `--db PATH` or `$WORKLOG_DB`; otherwise the XDG default.

  • schema      migrations auto-run on every command; `wl migrate` is the explicit form.
  • appearance  `wl themes` lists color themes; pick with `--theme` or `$WORKLOG_THEME`.
  • completion  `wl print-completion fish|bash|zsh` (write to your shell rc once).
  • shortcuts   `wl alias add d day` → `wl d` (see `wl help alias`).
  • language    `wl help` topics follow `$WORKLOG_LANG` (falling back to en).
