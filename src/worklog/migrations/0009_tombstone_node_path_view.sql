-- worklog schema v9: make the v_node_path helper view tombstone-aware.
--
-- v_node_path (a recursive node → "A / B / C" path view, from 0001) predates soft-delete
-- and would include soft-deleted nodes if queried manually. The CLI doesn't read it today,
-- but recreate it filtered so it can't resurface tombstoned rows for anyone who does.

DROP VIEW IF EXISTS v_node_path;

CREATE VIEW v_node_path AS
WITH RECURSIVE path(id, depth, label) AS (
    SELECT id, 0, title FROM node WHERE parent_id IS NULL AND deleted_at IS NULL
    UNION ALL
    SELECT n.id, p.depth + 1, p.label || ' / ' || n.title
    FROM node n
    JOIN path p ON n.parent_id = p.id
    WHERE n.deleted_at IS NULL
)
SELECT * FROM path;
