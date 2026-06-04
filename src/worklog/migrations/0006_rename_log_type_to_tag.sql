-- worklog schema v6: rename log.type → log.tag.
--
-- The log-role column (note/goal/summary/overview/top5/clock/metric/…) was added
-- as `type` in 0002. Unify the naming so all three classification fields are `tag`:
-- node-level `tag` (a multi-purpose label set), `metric.tag` (a datapoint's kind),
-- and now `log.tag` (a log's role). One word, one mental model — "classified by tag".
-- (Earlier migrations 0002/0003/0004 still write `type`; they run before this rename,
-- which preserves their data.)
ALTER TABLE log RENAME COLUMN type TO tag;

-- rename the supporting index to match (the old one keeps working post-rename, but
-- the name should reflect the column).
DROP INDEX IF EXISTS idx_log_node_type;
CREATE INDEX IF NOT EXISTS idx_log_node_tag ON log(node_id, tag, logged_at);
