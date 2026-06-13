---
title: project — an outcome with an end (PARA)
category: concept
see_also: para, area, task, projects
---
A **project** is an outcome with a finish line (e.g. "Q3 report", "Website revamp") — the PARA
"P". It's done when the outcome is reached, which is what distinguishes it from an `area`
(ongoing, no end).

  wl add "Q3 revamp" -k project --parent 10    # under area #10 (→ #11)
  wl add "draft copy" --parent 11              # tasks hang under the project

A project lives under an area and holds tasks. Close it with `wl done <id>` when the outcome
lands. `wl projects` lists active projects with their task counts + latest activity;
`wl tree --root <project>` shows its tasks; a project can carry its own `goal`
(`wl goal set <project> "..."`).

See `wl help para` for the model, `wl help area` (its home) / `wl help task` (its contents).
