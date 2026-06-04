-- worklog schema v4: move day/week/month meta fields from single-value props to
-- history-preserving typed logs.
--
-- goal / summary / overview / top5 were stored in the `prop` table (overwrite on
-- write, no history). They are really authored, edited, time-stamped records — i.e.
-- logs. They become tag(type)=<key> logs: each write appends a new log, and the
-- latest log of that type is the current value (so edit history is now kept, and
-- `prop` goes back to truly-static single-value attributes).
--
-- summary keeps its recorded write time (the old `summary_at` prop), so `wl day`'s
-- "written at" + stale-recap check still work; the others have no recorded write
-- time, so they use the node's created_at. summary_at is subsumed and dropped.

-- Scope strictly to time-hierarchy nodes (these meta fields only live there), so a
-- user UDA prop that happens to be named goal/summary/overview/top5/summary_at on a
-- task/project is left untouched.

-- summary → typed log, preserving its original write time
INSERT INTO log (node_id, type, logged_at, body)
SELECT p.node_id, 'summary', COALESCE(sa.value, n.created_at), p.value
FROM prop p
JOIN node n ON n.id = p.node_id
LEFT JOIN prop sa ON sa.node_id = p.node_id AND sa.key = 'summary_at'
WHERE p.key = 'summary' AND n.kind IN ('year', 'quarter', 'month', 'week', 'day');

-- goal / overview / top5 → typed logs (no recorded write time → node.created_at)
INSERT INTO log (node_id, type, logged_at, body)
SELECT p.node_id, p.key, n.created_at, p.value
FROM prop p
JOIN node n ON n.id = p.node_id
WHERE p.key IN ('goal', 'overview', 'top5') AND n.kind IN ('year', 'quarter', 'month', 'week', 'day');

-- drop the now-migrated props (only on time nodes — leave same-named UDA props elsewhere)
DELETE FROM prop WHERE key IN ('goal', 'summary', 'summary_at', 'overview', 'top5')
  AND node_id IN (SELECT id FROM node WHERE kind IN ('year', 'quarter', 'month', 'week', 'day'));
