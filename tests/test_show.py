"""Tests for show (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestShow:
    def test_show_full_node(self, cli, tmp_db):
        cli("add", "strategy pivot", "-k", "task", "-p", "A", "-t", "work,P0")
        cli("log", "1", "5/18 decision", "--keep-status")  # do not auto-progress to DOING; keep TODO for the test
        cli("log", "1", "5/19 breakdown", "--keep-status")
        cli("link", "1", "Dev tooling")
        cli("set", "1", "issue", "76")

        code, out, _ = cli("show", "1")
        assert code == 0
        assert "strategy pivot" in out
        assert "TODO" in out
        assert "#A" in out
        assert ":work:P0:" in out or ":P0:work:" in out
        assert "[[Dev tooling]]" in out
        assert "issue" in out and "76" in out
        assert "5/18 decision" in out
        assert "5/19 breakdown" in out
        assert "timeline / changes" in out  # logs upgraded to timeline

    def test_show_nonexistent_fails(self, cli):
        code, _, err = cli("show", "99")
        assert code != 0

    def test_show_upstream_path(self, cli):
        cli("add", "month", "-k", "month")
        cli("add", "day", "-k", "day", "--parent", "1")
        cli("add", "task", "-k", "task", "--parent", "2")
        code, out, _ = cli("show", "3")
        assert "ancestors" in out and "month" in out and "day" in out

    def test_show_subtasks(self, cli):
        cli("add", "parent", "-k", "task")
        cli("add", "child1", "-k", "task", "--parent", "1")
        cli("add", "child2", "-k", "task", "--parent", "1")
        code, out, _ = cli("show", "1")
        assert "children (2)" in out
        assert "child1" in out and "child2" in out

    def test_show_timeline_changes(self, cli):
        import time
        cli("add", "task")
        cli("log", "1", "progress one")
        cli("start", "1")
        time.sleep(0.05)
        cli("stop", "1")
        cli("done", "1")
        code, out, _ = cli("show", "1")
        assert "timeline / changes" in out
        assert "● created" in out
        assert "✎ log" in out and "progress one" in out
        assert "⏱ clock" in out  # structured clock interval (start→end)
        assert "✓ DONE" in out


# ─── ls ───


class TestShowSchedule:
    """wl show surfaces the sched table (one-off dates + recurring rules)."""

    def test_show_displays_oneoff_and_recur(self, cli):
        cli("add", "巡检", "-k", "habit")
        cli("sched", "1", "2026-06-15")
        cli("sched", "1", "--recur", "daily")
        _, out, _ = cli("show", "1")
        assert "schedule:" in out
        assert "daily" in out
        assert "2026-06-15" in out

    def test_show_no_schedule_section_when_unscheduled(self, cli):
        cli("add", "无排期", "-k", "task")
        _, out, _ = cli("show", "1")
        assert "schedule:" not in out
