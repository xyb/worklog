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
        assert "⏱ clock-in" in show

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
        # start 14:00, end 14:30
        assert "2025-01-02 14:30:00" in show
        assert "2025-01-02 14:00:00" in show

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
        cli("add", "在跑的活", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "在跑的活" in out
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
        assert "完成了 A 部分" in out or "progress" in out

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
