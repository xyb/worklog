---
title: types — what classification props are in use
category: command
see_also: ls, tree, projects, node, para
---
`wl types` lists the `type.*` / `date.*` classification props in use + a count of each value — the
RAW classification vocabulary, grouped by key. Unlike the retired `wl kinds`, it does NOT collapse
a node into one derived `kind`; it exposes the underlying namespaced props directly, so a node
appears under every facet it carries (a habit task counts under both `type.para=task` AND
`type.habit` — the orthogonal model is visible).

  wl types            # type.*/date.* keys + value counts, grouped by key
  wl types -o json    # machine-readable [{key, value, count}, …]

`type.*` facets (para / date / habit / meetlog / custom) come first with their value breakdown;
the high-cardinality `date.*` time values (period / start / end) follow as a count. To list or
drill into one classification, filter by role with `wl ls --para <role>` (area/project/task), or
by the classification prop with `wl ls --prop type.<x>` (e.g. `--prop type.meetlog` /
`--prop type.habit` / `--prop type.date=day`).
