"""Tests for clock (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestCmdActive:
    def test_active_shows_running_clocks(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "t1" in out

    def test_active_empty(self, cli):
        _, out, _ = cli("active")
        # nothing running → friendly hint
        assert "no" in out or "(" in out or out == ""


class TestStartStopAt:
    """wl start --at / wl stop --at backfill past timestamps"""

    def test_start_at_hhmm(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "09:00")
        _, show, _ = cli("show", "1")
        assert " 09:00:00" in show
        assert "⏱ clock" in show

    def test_start_at_full_ts(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "2025-01-02 10:00")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 10:00:00" in show

    def test_start_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("start", "1", "--at", "25:99")
        assert code != 0

    def test_stop_at_after_start(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "2025-01-02 09:00")
        _, stop_out, _ = cli("stop", "1", "--at", "2025-01-02 09:30")
        assert "elapsed 30 min" in stop_out

    def test_stop_at_before_start_rejected(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "2025-01-02 10:00")
        code, _, _ = cli("stop", "1", "--at", "2025-01-02 09:00")
        assert code != 0

    def test_stop_without_start(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("stop", "1")
        assert code != 0

    def test_stop_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")
        code, _, _ = cli("stop", "1", "--at", "garbage")
        assert code != 0


class TestSpent:
    """wl spent <id> <duration> backfill"""

    def test_spent_minutes(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "45")
        assert "45min" in out

    def test_spent_hour_minute(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "1h30m")
        assert "90min" in out

    def test_spent_hour_only(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "2h")
        assert "120min" in out

    def test_spent_minute_suffix(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "30m")
        assert "30min" in out

    def test_spent_with_end_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("spent", "1", "30m", "--at", "2025-01-02 14:30")
        _, show, _ = cli("show", "1")
        # clock interval: start 14:00 (the timeline event time) → end 14:30 (in the range label)
        assert "2025-01-02 14:00:00" in show
        assert "14:00→14:30" in show and "(30min)" in show

    def test_spent_invalid_duration(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("spent", "1", "garbage")
        assert code != 0

    def test_spent_zero_duration(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("spent", "1", "0")
        assert code != 0

    def test_spent_node_not_found(self, cli):
        code, _, _ = cli("spent", "999", "30m")
        assert code != 0

    def test_spent_clock_total_recorded(self, cli):
        """CLOCK pair written by spent should be counted by _node_clock_min"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("spent", "1", "45m")
        _, out, _ = cli("day", today)
        assert "[45m]" in out or "45min" in out


class TestActiveBatteryIncluded:
    """wl active enhancements: current session + today's total + latest log + epilog"""

    def test_active_empty_hint(self, cli):
        _, out, _ = cli("active")
        assert "no active task right now" in out or "wl start" in out

    def test_active_shows_running_task(self, cli):
        cli("add", "running task", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "running task" in out
        assert "#1" in out

    def test_active_shows_today_total(self, cli):
        """today's total should appear (with "X min" text)"""
        cli("add", "work item", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "today's total" in out

    def test_active_shows_recent_log(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "progress: finished part A; next part B")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "latest log" in out
        # body should appear (truncated oneline or full)
        assert "finished part A" in out or "progress" in out

    def test_active_brief_skips_detail(self, cli):
        """-q compact mode: skips total / latest log expansion"""
        cli("add", "work item", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("-q", "active")
        assert "work item" in out
        assert "today's total" not in out
        assert "latest log" not in out

    def test_active_help_epilog(self, cli):
        """--help should include use cases and diff from wl day"""
        from worklog import cli as wl
        p = wl.build_parser()
        sa = next(a for a in p._actions if hasattr(a, "choices") and "active" in (a.choices or {}))
        active_p = sa.choices["active"]
        epilog = active_p.epilog or ""
        assert "Use cases:" in epilog
        assert "wl day" in epilog and "Difference from" in epilog


class TestActiveTodayTotal:
    """cmd_active aggregates today's CLOCK time across closed sessions."""

    def test_active_includes_today_completed_sessions(self, cli, tmp_db):
        cli("add", "long-running", "-k", "task")
        # close one session of 30 minutes
        cli("spent", "1", "30m")
        # start a current session
        cli("start", "1")
        _, out, _ = cli("active")
        assert "today's total" in out
        # at least 30 min from the closed session
        assert "30min" in out or "0h30m" in out


class TestClockTable:
    """Structured clock table (migration 0005): start/stop/spent/wait + backfill."""

    def test_start_opens_interval_stop_closes(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("start", "1", "--at", "2026-06-01 09:00")
        con = tmp_db.db_connect()
        assert con.execute("SELECT end_at FROM clock WHERE node_id=1").fetchone()["end_at"] is None
        cli("stop", "1", "--at", "2026-06-01 10:00")
        c = con.execute("SELECT end_at, elapsed_sec FROM clock WHERE node_id=1").fetchone()
        # --at is local (+08:00) → stored UTC (10:00 local = 02:00 UTC); duration unchanged
        assert c["end_at"] == "2026-06-01 02:00:00" and c["elapsed_sec"] == 3600

    def test_wait_closes_open_clock(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("start", "1")
        cli("wait", "1")
        con = tmp_db.db_connect()
        c = con.execute("SELECT end_at, elapsed_sec FROM clock WHERE node_id=1").fetchone()
        assert c["end_at"] is not None and c["elapsed_sec"] >= 60
        assert con.execute("SELECT status FROM node WHERE id=1").fetchone()["status"] == "WAIT"

    def test_no_clock_logs_written(self, cli, tmp_db):
        """start/stop no longer write CLOCK_IN/OUT logs (timing lives in the clock table)."""
        cli("add", "t", "-k", "task")
        cli("start", "1")
        cli("stop", "1")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM log WHERE body LIKE 'CLOCK%'").fetchone()[0] == 0

    def test_clock_backfill_migration_sql(self, cli, tmp_db):
        """0005 pairs legacy CLOCK_IN/CLOCK_OUT logs into clock rows and deletes the logs."""
        import pathlib
        cli("add", "t", "-k", "task")
        con = tmp_db.db_connect()
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-01 09:00:00', 'CLOCK_IN')")
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-01 10:30:00', 'CLOCK_OUT elapsed=90min (from 2026-06-01 09:00:00)')")
        con.commit()
        mig = pathlib.Path(tmp_db.__file__).resolve().parent / "migrations" / "0005_clock_table.sql"
        con.executescript(mig.read_text())
        con.commit()
        c = con.execute("SELECT start_at, end_at, elapsed_sec FROM clock WHERE node_id=1").fetchone()
        assert c["start_at"] == "2026-06-01 09:00:00" and c["end_at"] == "2026-06-01 10:30:00"
        assert c["elapsed_sec"] == 5400  # 90 min
        assert con.execute("SELECT COUNT(*) FROM log WHERE body LIKE 'CLOCK%'").fetchone()[0] == 0

    def test_node_clock_min_sums_intervals(self, cli, tmp_db):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t", "-k", "task")
        cli("sched", "1", today)
        cli("spent", "1", "30m")
        cli("spent", "1", "45m")
        _, out, _ = cli("day", today)
        assert "75m" in out or "75min" in out or "1h15m" in out


class TestClockReviewFixes:
    """Fixes from the cross-model review of the clock increment."""

    def test_double_start_skipped(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("start", "1")  # already running
        assert "already has a running clock" in out
        con = tmp_db.db_connect()
        # only one open interval, not two
        assert con.execute("SELECT COUNT(*) FROM clock WHERE node_id=1 AND end_at IS NULL").fetchone()[0] == 1

    def test_clock_only_day_shows_total(self, cli):
        cli("add", "t", "-k", "task")  # not scheduled, no logs
        cli("spent", "1", "30m", "--at", "2026-06-01 14:00")
        _, out, _ = cli("day", "2026-06-01")
        assert "CLOCK 30min" in out  # clock total shown even with no logged progress

    def test_backfill_double_in_no_double_count(self, cli, tmp_db):
        """0005: IN1, IN2, OUT1 → IN2 closes at OUT1, IN1 stays open (not two intervals to OUT1)."""
        import pathlib
        cli("add", "t", "-k", "task")
        con = tmp_db.db_connect()
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-01 09:00:00', 'CLOCK_IN')")
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-01 09:30:00', 'CLOCK_IN')")
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-01 10:00:00', 'CLOCK_OUT elapsed=30min (from 2026-06-01 09:30:00)')")
        con.commit()
        mig = pathlib.Path(tmp_db.__file__).resolve().parent / "migrations" / "0005_clock_table.sql"
        con.executescript(mig.read_text())
        con.commit()
        rows = con.execute("SELECT start_at, end_at, elapsed_sec FROM clock ORDER BY start_at").fetchall()
        assert len(rows) == 2
        # IN1 (09:00) left open; IN2 (09:30) closed at 10:00
        assert rows[0]["start_at"] == "2026-06-01 09:00:00" and rows[0]["end_at"] is None
        assert rows[1]["start_at"] == "2026-06-01 09:30:00" and rows[1]["end_at"] == "2026-06-01 10:00:00"
        # total counted = 30min only (no double-count)
        total = con.execute("SELECT COALESCE(SUM(elapsed_sec),0) AS s FROM clock").fetchone()["s"]
        assert total == 1800

    def test_backfill_nested_in_in_out_out(self, cli, tmp_db):
        """0005: IN,IN,OUT,OUT (LIFO) reconstructs BOTH intervals from each OUT's
        recorded "(from ...)" — no lost interval, total preserved."""
        import pathlib
        cli("add", "t", "-k", "task")
        con = tmp_db.db_connect()
        con.executescript("""
            INSERT INTO log(node_id,logged_at,body) VALUES
            (1,'2026-06-01 09:00:00','CLOCK_IN'),
            (1,'2026-06-01 09:30:00','CLOCK_IN'),
            (1,'2026-06-01 10:00:00','CLOCK_OUT elapsed=30min (from 2026-06-01 09:30:00)'),
            (1,'2026-06-01 11:00:00','CLOCK_OUT elapsed=120min (from 2026-06-01 09:00:00)');
        """)
        con.commit()
        mig = pathlib.Path(tmp_db.__file__).resolve().parent / "migrations" / "0005_clock_table.sql"
        con.executescript(mig.read_text())
        con.commit()
        rows = con.execute("SELECT start_at, end_at, elapsed_sec FROM clock ORDER BY start_at").fetchall()
        assert len(rows) == 2  # both closed, none lost, none open
        assert all(r["end_at"] is not None for r in rows)
        assert con.execute("SELECT SUM(elapsed_sec) FROM clock").fetchone()[0] == 9000  # 150 min

    def test_day_duration_is_per_day_not_all_time(self, cli):
        """_node_clock_min(day=) scopes to the day — a task clocked on two days shows
        only that day's duration on each day's row (no cross-day inflation)."""
        cli("add", "t", "-k", "task")
        cli("sched", "1", "2026-06-01")
        cli("sched", "1", "2026-06-05")
        cli("spent", "1", "30m", "--at", "2026-06-01 10:00")
        cli("spent", "1", "120m", "--at", "2026-06-05 10:00")
        _, d1, _ = cli("day", "2026-06-01")
        _, d5, _ = cli("day", "2026-06-05")
        # day 06-01 shows ~30m, NOT the 4-day span or the 150min total
        assert "30m" in d1 and "2h" not in d1 and "96h" not in d1
        assert "2h" in d5 or "120m" in d5


class TestSpentBadAt:
    """`wl spent` with garbage --at should error cleanly."""

    def test_spent_bad_at_exits_cleanly(self, cli):
        cli("add", "task1", "-k", "task")
        code, _, err = cli("spent", "1", "30m", "--at", "garbage-timestamp")
        assert code != 0
