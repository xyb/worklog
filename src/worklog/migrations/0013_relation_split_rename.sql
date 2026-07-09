-- worklog schema v13: relation model redesign — split-into/split-from collapse into a
-- single `relation.split` (single-write, source-stores model: `wl relation A split B`
-- means "A splits out B", stored ONLY on A; the reverse `=split-from` is now derived at
-- read time, never stored). `relation.split_into` was exactly this edge under its old
-- name, so it's a straight rename; `relation.split_from` was the auto-written inverse,
-- now redundant (the view derives it), so it's dropped — the direction it encoded is not
-- lost, `split_into` (renamed to `split`) already has it. `related` needs no migration:
-- it was already dual-written, which is structurally identical to "two independent
-- single-write edges" under the new model (see DESIGN.md relation-model section).
UPDATE prop SET key = 'relation.split' WHERE key = 'relation.split_into';
DELETE FROM prop WHERE key = 'relation.split_from';
