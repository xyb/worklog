---
title: priority — -p A/B/C (P0/P1/P2)
category: param
see_also: node, status, add
---
Priority is set with `-p` (or `--priority`) and takes one of three letters:

  -p A   P0 — highest; do-first / most important
  -p B   P1 — normal important work
  -p C   P2 — nice-to-have / low

It renders next to the id as `[#A]` / `[#B]` / `[#C]`. Lists sort by priority + id by
default (`wl ls --sort pri`), so high-priority items float to the top.

Priority is independent of scheduling and status: a `[#A]` task can be unscheduled, and a
`[#C]` task can be planned for today. Set it at creation (`wl add "…" -p B`) or change it
later with `wl node edit <id> -p A`. Leaving it off means "unprioritized" (sorts last).
