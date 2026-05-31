-- worklog-cli schema v0
-- main table: node (one id space spans lifetime / decade / year / quarter / month / week / day / area / project / task / signal / habit / any custom kind)
-- tree: parent_id self-reference
-- multi-log: log table hangs off node
-- multi-tag: tag table (many-to-many)
-- multi-field: prop table (UDA-style key/value)
-- vault wikilink: link table

CREATE TABLE IF NOT EXISTS node (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER REFERENCES node(id) ON DELETE SET NULL,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- lifetime/decade/year/quarter/month/week/day/area/project/task/signal/habit/meetlog/...
    status       TEXT,                 -- TODO/DOING/LATER/WAIT/DONE/DEFERRED/CANCELED (task-like kinds only)
    priority     TEXT,                 -- A/B/C (task-like kinds only)
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    scheduled_at TEXT,                 -- planned start / appearance date (task-like kinds)
    deadline_at  TEXT,                 -- hard deadline
    closed_at    TEXT,                 -- completion / cancel time
    body         TEXT                  -- optional long content
);

CREATE INDEX IF NOT EXISTS idx_node_parent ON node(parent_id);
CREATE INDEX IF NOT EXISTS idx_node_kind ON node(kind);
CREATE INDEX IF NOT EXISTS idx_node_status ON node(status);

CREATE TABLE IF NOT EXISTS tag (
    node_id INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY (node_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_tag_tag ON tag(tag);

CREATE TABLE IF NOT EXISTS log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    logged_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    body      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_log_node ON log(node_id);
CREATE INDEX IF NOT EXISTS idx_log_time ON log(logged_at);

-- forward planning (calendar-like, decoupled from log): pin tasks to a specific day / recurrence rule
-- on_date and rrule are mutually exclusive; a task can have multiple rows (multi-day / one-off + recurring together)
-- `wl day` derives planned (hit by sched) vs unplanned (logged with no sched) from this table
CREATE TABLE IF NOT EXISTS sched (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    on_date    TEXT,                 -- a specific day YYYY-MM-DD (one-off)
    rrule      TEXT,                 -- recurrence rule: daily / weekly:Mon,Wed,Fri
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sched_node ON sched(node_id);
CREATE INDEX IF NOT EXISTS idx_sched_date ON sched(on_date);

-- date metadata (calendar-like): holiday / vacation / makeup-workday / solar-term context; weekday is computed, not stored
-- can be bulk-imported (e.g. national holidays) plus user customization (personal vacation / makeup days); independent of day-node existence
CREATE TABLE IF NOT EXISTS date_meta (
    date  TEXT PRIMARY KEY,        -- YYYY-MM-DD
    label TEXT NOT NULL            -- e.g. "Labor Day holiday" / "makeup workday" / "solar term: Lesser Fullness" / "annual leave"
);

CREATE TABLE IF NOT EXISTS prop (
    node_id INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (node_id, key)
);

CREATE TABLE IF NOT EXISTS link (
    node_id   INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    vault_doc TEXT NOT NULL,     -- vault document name (no .md suffix)
    PRIMARY KEY (node_id, vault_doc)
);

CREATE INDEX IF NOT EXISTS idx_link_doc ON link(vault_doc);

-- view: tree path (used by `wl tree` for display)
CREATE VIEW IF NOT EXISTS v_node_path AS
WITH RECURSIVE path(id, depth, label) AS (
    SELECT id, 0, title FROM node WHERE parent_id IS NULL
    UNION ALL
    SELECT n.id, p.depth + 1, p.label || ' / ' || n.title
    FROM node n
    JOIN path p ON n.parent_id = p.id
)
SELECT * FROM path;
