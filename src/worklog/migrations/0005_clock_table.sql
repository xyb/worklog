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

-- Backfill, using the authoritative record old `wl stop` already wrote: every
-- CLOCK_OUT body carries "(from <start_ts>)" — the exact CLOCK_IN it closed
-- (computed by the old LIFO stop). So we reconstruct each CLOSED interval
-- directly from the OUT (start = its "from", end = its own logged_at), which is
-- correct for any sequence (IN,IN,OUT / IN,IN,OUT,OUT / nested) with no pairing
-- ambiguity and no change to the recorded totals. An OPEN interval is a CLOCK_IN
-- whose timestamp is not the "from" of any OUT (a `wl start` never stopped).
-- "(from " + 19-char "YYYY-MM-DD HH:MM:SS" (the format old stop/spent/wait wrote).

-- closed intervals: one per CLOCK_OUT, from its own recorded start + end
INSERT INTO clock (node_id, start_at, end_at, elapsed_sec)
SELECT node_id,
       substr(body, instr(body, '(from ') + 6, 19) AS start_at,
       logged_at,
       CAST(round((julianday(logged_at)
                   - julianday(substr(body, instr(body, '(from ') + 6, 19))) * 86400) AS INTEGER)
FROM log
WHERE body LIKE 'CLOCK_OUT elapsed=%(from %)%' AND instr(body, '(from ') > 0;

-- open intervals: a CLOCK_IN whose start was never closed by any OUT
INSERT INTO clock (node_id, start_at)
SELECT i.node_id, i.logged_at
FROM log i
WHERE i.body = 'CLOCK_IN'
  AND NOT EXISTS (
      SELECT 1 FROM log o
      WHERE o.node_id = i.node_id AND o.body LIKE 'CLOCK_OUT elapsed=%(from %)%'
        AND substr(o.body, instr(o.body, '(from ') + 6, 19) = i.logged_at
  );

-- The migrated CLOCK logs are now redundant — their timing lives in the clock
-- table. Narrow predicate (exact CLOCK_IN / command-format CLOCK_OUT) so a
-- user-written log that merely starts with "CLOCK_OUT" is not deleted.
DELETE FROM log WHERE body = 'CLOCK_IN' OR body LIKE 'CLOCK_OUT elapsed=%';
