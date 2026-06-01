"""Tests for meta (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestGoalRecapTick:
    """shortcuts: wl goal / wl recap (today) + wl tick (check-in)"""

    def test_goal_set_and_read(self, cli):
        cli("goal", "今天交付 X")
        code, out, _ = cli("goal")
        assert "今天交付 X" in out

    def test_goal_auto_creates_day(self, cli):
        # wl goal on an empty DB should auto-create today's day node
        cli("goal", "测试目标")
        from datetime import date
        today = date.today().isoformat()
        code, out, _ = cli("ls", "--kind", "day")
        assert today in out  # day node exists

    def test_recap_set_and_read(self, cli):
        cli("recap", "今天小结 Y")
        code, out, _ = cli("recap")
        assert "今天小结 Y" in out

    def test_recap_empty_default(self, cli):
        code, out, _ = cli("recap")
        assert "no summary set for today" in out

    def test_recap_stamps_summary_at(self, cli):
        # recap writes a stamp; read and wl day both show "written at"; no new changes → no rewrite prompt
        cli("recap", "小结 X")
        _, rout, _ = cli("recap")
        assert "written at" in rout
        _, dout, _ = cli("day")
        assert "written at" in dout
        assert "consider rewriting" not in dout

    def test_day_warns_when_changes_after_summary(self, cli):
        # mock recap written long ago; later non-CLOCK log that day → wl day suggests rewriting recap
        cli("recap", "小结 v1")  # recap on empty DB auto-creates today's day, id=1
        cli("set", "1", "summary_at", "2000-01-01 00:00:00")
        cli("add", "work item", "-k", "task")
        cli("log", "2", "小结后又干了活")
        _, out, _ = cli("day")
        assert "consider rewriting" in out

    def test_tick_adds_log(self, cli):
        cli("add", "workout", "-k", "habit")
        cli("tick", "1", "--note", "引体 6 个")
        code, out, _ = cli("show", "1")
        assert "引体 6 个" in out

    def test_tick_done_flag(self, cli):
        cli("add", "一次性活", "-k", "task")
        cli("tick", "1", "--done")
        code, out, _ = cli("show", "1")
        assert "DONE" in out
