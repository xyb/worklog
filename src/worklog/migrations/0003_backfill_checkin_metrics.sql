-- worklog schema v3: backfill check-in metrics for historical habit completions.
--
-- Habit "done today" detection moves from the loose "did any log exist that day"
-- heuristic to the structured signal "is there a tag=checkin metric that day"
-- (G1). To preserve historical completion display, synthesize one check-in metric
-- per (habit node, day) that already has a non-CLOCK log — using the earliest such
-- log that day as its carrier. The metric's `at` is the day (date-only = day-start),
-- matching how detection compares substr(at,1,10) to the day.
--
-- Going forward, `wl tick` / `wl checkin` write check-ins explicitly; a stray note
-- added to a habit no longer counts as done. This migration only restructures the
-- past as-recorded; it does not change which historical days showed as done.
INSERT INTO metric (log_id, node_id, tag, value_num, at)
SELECT MIN(l.id), l.node_id, 'checkin', 1, substr(l.logged_at, 1, 10)
FROM log l
JOIN node n ON n.id = l.node_id
WHERE n.kind = 'habit'
  AND l.type IS NULL                       -- only plain note logs; skip metric carriers / typed logs
  AND l.body NOT LIKE 'CLOCK\_%' ESCAPE '\'
GROUP BY l.node_id, substr(l.logged_at, 1, 10);
