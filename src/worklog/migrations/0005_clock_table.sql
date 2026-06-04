-- worklog schema v5: structured clock (time tracking) — replaces the CLOCK_IN /
-- CLOCK_OUT log-body convention with a real interval table (G1).
--
-- Old model: `wl start` wrote a log body 'CLOCK_IN', `wl stop` wrote
-- 'CLOCK_OUT elapsed=Nmin (from ...)'; durations were parsed back out of body
-- text (a string convention — fragile, unaggregatable). New model: a `clock`
-- row per interval (start_at, end_at, elapsed_sec) hanging off the node.
--
-- clock is a standalone interval table on the node (NOT a metric and NOT a
-- carrier log): an interval has a different shape from a point-metric, and tying
-- it to a log added churn with no benefit. wl start/stop/spent/wait now read and
-- write this table; wl show / wl day / wl summary render time from it.

CREATE TABLE IF NOT EXISTS clock (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    start_at    TEXT NOT NULL,
    end_at      TEXT,                 -- NULL = still running
    elapsed_sec INTEGER               -- NULL while running; set on stop
);

CREATE INDEX IF NOT EXISTS idx_clock_node ON clock(node_id);
CREATE INDEX IF NOT EXISTS idx_clock_start ON clock(start_at);
CREATE INDEX IF NOT EXISTS idx_clock_end ON clock(end_at);

-- Backfill: pair each CLOCK_IN log with the next CLOCK_OUT on the same node.
-- A CLOCK_IN with no following CLOCK_OUT becomes an open interval (end_at NULL).
-- elapsed_sec is the wall-clock span (matches the old elapsed=Nmin, to the second).
INSERT INTO clock (node_id, start_at, end_at, elapsed_sec)
SELECT i.node_id, i.logged_at, o.logged_at,
       CASE WHEN o.logged_at IS NOT NULL
            THEN CAST(round((julianday(o.logged_at) - julianday(i.logged_at)) * 86400) AS INTEGER)
            END
FROM log i
LEFT JOIN log o ON o.id = (
    SELECT MIN(o2.id) FROM log o2
    WHERE o2.node_id = i.node_id AND o2.id > i.id AND o2.body LIKE 'CLOCK_OUT%'
)
WHERE i.body = 'CLOCK_IN';

-- The CLOCK logs are now redundant — their timing lives in the clock table.
DELETE FROM log WHERE body = 'CLOCK_IN' OR body LIKE 'CLOCK_OUT%';
