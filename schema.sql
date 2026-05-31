-- worklog-cli schema v0
-- 主表: node (统一 id 涵盖 lifetime / decade / year / quarter / month / week / day / area / project / task / signal / habit / 任意自定义 kind)
-- 树状: parent_id 自引
-- 多 log: log 表挂 node
-- 多标签: tag 表 (多对多)
-- 多字段: prop 表 (UDA 风格 key/value)
-- vault 双链: link 表

CREATE TABLE IF NOT EXISTS node (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER REFERENCES node(id) ON DELETE SET NULL,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- lifetime/decade/year/quarter/month/week/day/area/project/task/signal/habit/meetlog/...
    status       TEXT,                 -- TODO/DOING/LATER/WAIT/DONE/DEFERRED/CANCELED (任务类才用)
    priority     TEXT,                 -- A/B/C (任务类才用)
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    scheduled_at TEXT,                 -- 计划开始/出现日 (任务类)
    deadline_at  TEXT,                 -- 硬截止
    closed_at    TEXT,                 -- 完成/取消时间
    body         TEXT                  -- 可选长内容
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

-- 前瞻计划 (类日历, 独立于 log): 把任务排到具体某天 / 重复规则
-- on_date 与 rrule 二选一; 一个任务可有多条 (多天 / 一次性+重复并存)
-- wl day 据此推导计划内(被 sched 命中) vs 计划外(有 log 无 sched)
CREATE TABLE IF NOT EXISTS sched (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    on_date    TEXT,                 -- 具体某天 YYYY-MM-DD (一次性)
    rrule      TEXT,                 -- 重复规则: daily / weekly:Mon,Wed,Fri
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sched_node ON sched(node_id);
CREATE INDEX IF NOT EXISTS idx_sched_date ON sched(on_date);

-- 日期元数据 (类日历): 节日/休假/调休/节气等上下文; 周X 由日期自动算不存这
-- 可全年预导入(假期表) + 自定义(自己哪天休假/调休); 不依赖 day 节点存在
CREATE TABLE IF NOT EXISTS date_meta (
    date  TEXT PRIMARY KEY,        -- YYYY-MM-DD
    label TEXT NOT NULL            -- 劳动节假期 / 调休上班 / 小满 / 年假 ...
);

CREATE TABLE IF NOT EXISTS prop (
    node_id INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (node_id, key)
);

CREATE TABLE IF NOT EXISTS link (
    node_id   INTEGER NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    vault_doc TEXT NOT NULL,     -- vault 内文档名 (无 .md 后缀)
    PRIMARY KEY (node_id, vault_doc)
);

CREATE INDEX IF NOT EXISTS idx_link_doc ON link(vault_doc);

-- view: 树状路径（用于 wl tree 展示）
CREATE VIEW IF NOT EXISTS v_node_path AS
WITH RECURSIVE path(id, depth, label) AS (
    SELECT id, 0, title FROM node WHERE parent_id IS NULL
    UNION ALL
    SELECT n.id, p.depth + 1, p.label || ' / ' || n.title
    FROM node n
    JOIN path p ON n.parent_id = p.id
)
SELECT * FROM path;
