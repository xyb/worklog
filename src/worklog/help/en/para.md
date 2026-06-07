---
title: para — organizing with areas, projects, tasks
category: guide
see_also: node, add, planning
---
wl organizes work PARA-style. You build the tree by nesting nodes with `--parent`:

  area      an ongoing responsibility with no finish line (e.g. "Health", "Infra").
            Lives at the top level. Use it as a long-lived home for projects.
  project   an outcome with an end (e.g. "Website revamp", "Q3 report").
            Lives under an area. Done when the outcome is reached.
  task      a concrete action (e.g. "draft the homepage copy"). Lives under a
            project — or directly under an area for one-off work.

Example:
  wl add "Website" -k area                      # → #10
  wl add "Q3 revamp" -k project --parent 10     # → #11
  wl add "draft homepage copy" --parent 11      # a task under the project

Other kinds aren't PARA but share the tree: `habit` (recurring), `meetlog` (a meeting
note), and the auto-built time skeleton (year ▸ quarter ▸ month ▸ week ▸ day) that
scheduling and day-views hang off of — you never create those by hand.

This is a suggestion, not a requirement: tasks can live at any level. Use `wl tree` to
see the shape, `wl projects` to list active projects. (Resources/Archives from classic
PARA map loosely to tags and the hidden DONE/CANCELED states.)
