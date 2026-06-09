-- worklog schema v8: soft-delete tombstones.
--
-- Direction (xyb): (1) stop enforcing foreign keys so the tables decouple and can
-- be maintained / synced independently, and (2) avoid irreversible DELETE — a
-- removal becomes a tombstone (`deleted_at` set) instead of a row vanishing.
--
-- Why: even with ON DELETE CASCADE, some references can't be cleaned by cascade —
-- the denormalized `metric.node_id`, vault links, wikilinks inside a body, the
-- `parent_id` orphan-on-SET-NULL, and (the real driver) future multi-end / CRDT
-- sync (#57), where a hard CASCADE fights conflict-free merge. Not deleting
-- sidesteps that whole class of inconsistency, and a tombstone is the CRDT-native
-- "removed" signal.
--
-- Model after this migration:
--   * every table gets a nullable `deleted_at` (UTC instant; NULL = live).
--   * `db_table.delete()` is now a soft-delete (UPDATE deleted_at); reads filter
--     `deleted_at IS NULL` by default. Cascade is done in the app (soft-delete a
--     node tombstones its spoke rows; soft-delete a log tombstones its metrics),
--     replacing the FK CASCADE that's no longer enforced.
--   * `PRAGMA foreign_keys` is left OFF at connect time (see db.py). The existing
--     REFERENCES / ON DELETE clauses stay in the schema as documentation but are
--     inert; nothing is hard-deleted, so nothing orphans.
--
-- Additive and reversible-safe: ADD COLUMN with a NULL default touches no existing
-- data (all current rows read as live).

ALTER TABLE node      ADD COLUMN deleted_at TEXT;
ALTER TABLE log       ADD COLUMN deleted_at TEXT;
ALTER TABLE tag       ADD COLUMN deleted_at TEXT;
ALTER TABLE link      ADD COLUMN deleted_at TEXT;
ALTER TABLE prop      ADD COLUMN deleted_at TEXT;
ALTER TABLE sched     ADD COLUMN deleted_at TEXT;
ALTER TABLE metric    ADD COLUMN deleted_at TEXT;
ALTER TABLE clock     ADD COLUMN deleted_at TEXT;
ALTER TABLE date_meta ADD COLUMN deleted_at TEXT;

-- partial indexes: the common read is "live rows", so index the tombstone where
-- it matters most (node + log are the hot, large tables).
CREATE INDEX IF NOT EXISTS idx_node_live ON node(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_log_live  ON log(deleted_at)  WHERE deleted_at IS NULL;
