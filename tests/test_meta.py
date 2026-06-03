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

    def test_auto_day_builds_full_time_ancestor_chain(self, cli, tmp_db):
        """Auto-created day must hang under week→month→quarter→year, not dangle (#410)."""
        from datetime import date
        cli("goal", "g")  # triggers _ensure_today_day on an empty DB
        con = tmp_db.db_connect()
        today = date.today()
        day = con.execute(
            "SELECT id, parent_id FROM node WHERE kind='day' AND title LIKE ?",
            (today.isoformat() + "%",)).fetchone()
        assert day["parent_id"] is not None, "day dangled with no parent"
        from worklog import cli as wl
        kinds = [n["kind"] for n in wl._ancestors_chain(con, day["id"])]
        for k in ("year", "quarter", "month", "week"):
            assert k in kinds, f"time ancestor '{k}' missing from chain {kinds}"
        # day's direct parent is the week
        wk = con.execute("SELECT kind FROM node WHERE id = ?", (day["parent_id"],)).fetchone()
        assert wk["kind"] == "week"

    def test_auto_day_reuses_existing_year_any_title_style(self, cli, tmp_db):
        """Lenient year lookup: an existing year titled 'YYYY 年' (or any 'YYYY…') is
        reused, not duplicated by a new ISO 'YYYY' node (#410, xyb's chosen behavior)."""
        from datetime import date
        y = date.today().year
        cli("add", f"{y} 年", "-k", "year")  # pre-existing Chinese-style year
        cli("goal", "g")
        con = tmp_db.db_connect()
        n_years = con.execute("SELECT COUNT(*) FROM node WHERE kind='year'").fetchone()[0]
        assert n_years == 1, "lenient lookup should reuse the existing year, not add an ISO duplicate"

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

    def test_day_warns_when_changes_after_summary(self, cli, tmp_db):
        # mock recap written long ago; later non-CLOCK log that day → wl day suggests rewriting recap
        from datetime import date
        cli("recap", "小结 v1")  # auto-creates today's day (+ its time-ancestor chain)
        con = tmp_db.db_connect()
        day = con.execute("SELECT id FROM node WHERE kind='day' AND title LIKE ?",
                          (date.today().isoformat() + "%",)).fetchone()
        cli("set", str(day["id"]), "summary_at", "2000-01-01 00:00:00")
        cli("add", "work item", "-k", "task")
        task = con.execute("SELECT id FROM node WHERE title='work item'").fetchone()
        cli("log", str(task["id"]), "小结后又干了活")
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
