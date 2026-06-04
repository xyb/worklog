-- worklog schema v2: node → log → metric (log-centric structured datapoints)
--
-- Design rationale:
--   The tool is a *worklog*: `node` (tasks/hierarchy) and `log` (records) are
--   the two core objects; everything else grows off them. A `metric` is a
--   structured datapoint (check-in / number / measurement) that MUST hang off
--   one `log` — so every datapoint has a log as its carrier. Even a bare number
--   or check-in first gets a (possibly empty-body) log to carry it.
--
-- This migration is purely additive: a new `metric` table + a nullable `type`
-- column on `log`. No data is rewritten; scenario migrations (check-in, clock,
-- goal/summary → typed log, glucose, …) land incrementally in later work.
--
-- Shape decided after a cross-model review (GPT-5.5 + Kimi K2.5, 2026-06-04):
--   * metric's classification field is `tag` (open vocabulary, most varied);
--     the log-role field is `type`; the node-level `tag` table keeps its name
--     for now (a separate refactor will rename it — it's really work/personal
--     bucketing, not "tags"). Three distinct words for three distinct axes.
--   * metric.node_id is denormalized for join-free per-node queries but carries
--     NO foreign key (to avoid a double ON DELETE CASCADE with log_id) and is
--     kept consistent with its carrier log via triggers below — removing the
--     "node_id disagrees with log.node_id" footgun while keeping the fast path.
--   * a CHECK requires every metric to carry at least one value; pure markers
--     (check-in) store value_num = 1 rather than NULL/NULL, so no reserved tag
--     name is frozen into the schema (the reserved-tag namespace is still open).
--   * composite indexes match the real reads (per-node trend, cross-node trend,
--     latest typed log of a node) instead of low-selectivity single columns.
--   * timestamps stay localtime, matching the existing log.logged_at convention;
--     a dedicated migration will move everything to UTC + local rendering later.

-- metric: a structured datapoint attached to a log.
--   tag        — what this datapoint is (open vocabulary, may overlap/evolve):
--                glucose / weight / pullups / checkin / …  NOT the same namespace
--                as the node-level tag table (work/personal/…).
--   value_num  — numeric value (trends, sums); value_text — non-numeric value.
--                A pure marker (check-in) stores value_num = 1.
--   unit       — unit of value_num (mg/dL, kg, reps, …).
--   note       — inline short note for THIS datapoint; narrative goes in log.body.
--   node_id    — denormalized from log.node_id (no FK; trigger-maintained).
-- No UNIQUE constraint: high-frequency series (manual entry can be 1 log→1 metric;
-- bulk import is 1 log→N metrics) need many rows; check-in idempotency (one per
-- day) is enforced in the command layer while that rule (design Q5) is still open.
CREATE TABLE IF NOT EXISTS metric (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id     INTEGER NOT NULL REFERENCES log(id) ON DELETE CASCADE,
    node_id    INTEGER NOT NULL,
    tag        TEXT NOT NULL,
    value_num  REAL,
    value_text TEXT,
    unit       TEXT,
    note       TEXT,
    at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (value_num IS NOT NULL OR value_text IS NOT NULL)
);

-- log_id: fold a log's metrics beneath it.
CREATE INDEX IF NOT EXISTS idx_metric_log ON metric(log_id);
-- (node_id, tag, at): per-node series over time — check-in lookup, "node X glucose".
CREATE INDEX IF NOT EXISTS idx_metric_node_tag_at ON metric(node_id, tag, at);
-- (tag, at): cross-node trend for one datapoint type.
CREATE INDEX IF NOT EXISTS idx_metric_tag_at ON metric(tag, at);

-- Keep metric.node_id in lockstep with its carrier log (no FK; these triggers
-- are the integrity guarantee). Caller passes node_id; the trigger corrects it.
CREATE TRIGGER IF NOT EXISTS metric_node_id_after_insert
AFTER INSERT ON metric BEGIN
    UPDATE metric SET node_id = (SELECT node_id FROM log WHERE id = NEW.log_id)
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS metric_node_id_after_logid_update
AFTER UPDATE OF log_id ON metric BEGIN
    UPDATE metric SET node_id = (SELECT node_id FROM log WHERE id = NEW.log_id)
    WHERE id = NEW.id;
END;

-- If a log is re-parented to another node, move its metrics with it.
CREATE TRIGGER IF NOT EXISTS log_reparent_sync_metric
AFTER UPDATE OF node_id ON log BEGIN
    UPDATE metric SET node_id = NEW.node_id WHERE log_id = NEW.id;
END;

-- Guard against a direct `UPDATE metric SET node_id = ...` desyncing it from
-- the carrier log: snap it back. The WHEN clause makes this a no-op once in
-- sync (and recursive_triggers is OFF by default, so the inner UPDATE — and the
-- log_reparent trigger's UPDATE — won't re-fire this). This closes the last way
-- node_id could disagree with log.node_id.
CREATE TRIGGER IF NOT EXISTS metric_node_id_guard
AFTER UPDATE OF node_id ON metric
WHEN NEW.node_id IS NOT (SELECT node_id FROM log WHERE id = NEW.log_id)
BEGIN
    UPDATE metric SET node_id = (SELECT node_id FROM log WHERE id = NEW.log_id)
    WHERE id = NEW.id;
END;

-- log.type: the role of this log (open vocabulary):
--   note / goal / summary / overview / top5 / clock / state / …
-- NULL = plain note (back-compat: all existing logs read as untyped notes).
-- This lets day/week meta-info (goal/summary/overview/top5), currently stored as
-- single-value props, become typed logs — which keeps their edit history
-- instead of overwriting, and lets prop go back to truly-static attributes.
-- That prop → log migration is a later scenario; this only adds the column.
ALTER TABLE log ADD COLUMN type TEXT;

-- (node_id, type, logged_at): latest typed log of a node, e.g. a day's current goal.
CREATE INDEX IF NOT EXISTS idx_log_node_type ON log(node_id, type, logged_at);
