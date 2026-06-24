"""Migration 0012: promote bare-date log timestamps to full instants.

Older `wl log --date` (before it learned to keep the time) stored a date-only logged_at like
"2026-06-20" — losing intra-day ordering and rendering no @HH:MM. Backfill each such log to that
day's LOCAL midnight stored as a UTC instant (``local_to_utc("<date> 00:00:00")``): the local
calendar day is preserved (a full local datetime round-tripped through UTC never rolls) and the log
now shows @00:00. Only the ``log`` table — the day-granular checkin ``metric.at`` is intentionally
left bare. Runs in one transaction (``db._apply_py_migration``); any failure rolls the whole thing
back, leaving the DB at v11. Idempotent in effect: a full instant is length 19, so re-running finds
nothing length-10 to promote.
"""
from worklog import timeutil as _tu


def migrate(con):
    # length 10 == "YYYY-MM-DD" (a full instant is 19); logged_at only ever holds one of those two.
    rows = con.execute("SELECT id, logged_at FROM log WHERE length(logged_at) = 10").fetchall()
    for row in rows:
        log_id, logged_at = row[0], row[1]
        con.execute("UPDATE log SET logged_at = ? WHERE id = ?",
                    (_tu.local_to_utc(f"{logged_at} 00:00:00"), log_id))
