---
title: kinds — what node kinds are in use
category: command
see_also: ls, tree, projects, node
---
`wl kinds` lists the node kinds that exist in the DB, each with a count of its live nodes — a
quick "what's in here" overview (the `wl projects`-style list, but for kinds).

  wl kinds            # kinds in use + counts, in canonical order
  wl kinds -o json    # machine-readable [{kind, count}, …]

Kinds run from the time skeleton (`lifetime → year → quarter → month → week → day`) through
containers (`area` / `project`) to leaves (`task` / `meetlog` / `habit`); `wl kinds`
shows them in that order, custom kinds last. To list or drill into one classification, filter by
role with `wl ls --para <role>` (area/project/task), or by the classification prop with
`wl ls --prop type.<x>` (e.g. `--prop type.meetlog` / `--prop type.habit` / `--prop type.date=day`).
