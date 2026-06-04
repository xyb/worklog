-- worklog schema v7: store precise instants (*_at) as UTC; name literal dates *date.
--
-- Naming convention (see DESIGN / [[wl 打卡与时序数据建模设计]]):
--   *_at  = a precise instant, stored UTC ('YYYY-MM-DD HH:MM:SS'), rendered local.
--   *date = a literal local calendar date ('YYYY-MM-DD'), never timezone-converted.
--
-- Two parts:
--  (1) Convert existing *_at instants from local (+08:00, the zone every prior row
--      was written in — datetime('now','localtime') on a China host) to UTC, i.e.
--      subtract 8 hours. Only full-timestamp values are converted; bare 'YYYY-MM-DD'
--      values (date-only logged_at from `wl log --date`, and the checkin metric.at
--      backfilled by 0003) are degenerate dates, left verbatim — the LIKE guard skips
--      them (subtracting 8h would wrongly roll them to the previous day).
--  (2) Rename node.scheduled_at / node.deadline_at to *_date: they hold a planned /
--      deadline *day* (and scheduled_at also holds fuzzy '@2026-06' pins), not an
--      instant, so by the convention they must not carry the _at suffix. Values are
--      kept verbatim (local dates), not converted.
--
-- New rows are already written UTC by the application (datetime('now') / local_to_utc),
-- so this only fixes pre-v7 historical data. China has no DST, so the fixed -8h is exact.
--
-- NOT idempotent — apply ONLY through the migration runner, which gates on
-- PRAGMA user_version (so it runs exactly once) and wraps the whole file in one
-- transaction (a stray replay would double-subtract, but then the RENAME COLUMN
-- below fails because scheduled_at/deadline_at no longer exist, and the whole
-- transaction rolls back — leaving data intact). Do not hand-run it twice.

-- (1) local -> UTC for every full-timestamp instant column
UPDATE node  SET created_at = datetime(created_at, '-8 hours')
  WHERE created_at LIKE '____-__-__ __:__:__';
UPDATE node  SET closed_at  = datetime(closed_at,  '-8 hours')
  WHERE closed_at  LIKE '____-__-__ __:__:__';
UPDATE log   SET logged_at  = datetime(logged_at,  '-8 hours')
  WHERE logged_at  LIKE '____-__-__ __:__:__';
UPDATE metric SET at        = datetime(at,         '-8 hours')
  WHERE at         LIKE '____-__-__ __:__:__';
UPDATE clock SET start_at   = datetime(start_at,   '-8 hours')
  WHERE start_at   LIKE '____-__-__ __:__:__';
UPDATE clock SET end_at     = datetime(end_at,     '-8 hours')
  WHERE end_at     LIKE '____-__-__ __:__:__';
UPDATE sched SET created_at = datetime(created_at, '-8 hours')
  WHERE created_at LIKE '____-__-__ __:__:__';

-- (2) rename the date-typed columns (values kept verbatim — local calendar dates)
ALTER TABLE node RENAME COLUMN scheduled_at TO scheduled_date;
ALTER TABLE node RENAME COLUMN deadline_at  TO deadline_date;
