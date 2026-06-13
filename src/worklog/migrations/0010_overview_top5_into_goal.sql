-- worklog schema v10: collapse the `overview` and `top5` reserved-tag logs into `goal`.
--
-- The reserved-tag logs used to be four (goal/summary/overview/top5): `overview` held a
-- week's forward-looking focus and `top5` a month's priorities. Both are just a GOAL at a
-- different time level — the node's kind (week / month) already says the level — so they
-- collapse into a single `goal` tag. After this the reserved tags are only `goal` (forward,
-- any level) and `summary` (backward recap). Each was a history-preserving log; retag in place
-- so the history is preserved, just under `goal`.
UPDATE log SET tag = 'goal' WHERE tag IN ('overview', 'top5') AND deleted_at IS NULL;
