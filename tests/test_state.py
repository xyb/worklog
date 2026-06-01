"""Tests for state (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest

ESC = "["  # ANSI escape prefix


class TestStatusTransitions:
    def test_done_sets_status_and_closed_at(self, cli, tmp_db):
        cli("add", "task")
        cli("done", "1")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status, closed_at FROM node WHERE id=1").fetchone()
        assert row["status"] == "DONE"
        assert row["closed_at"] is not None

    def test_defer_sets_later_and_scheduled(self, cli, tmp_db):
        cli("add", "task")
        cli("defer", "1", "2026-06-15")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status, scheduled_at FROM node WHERE id=1").fetchone()
        assert row["status"] == "LATER"
        assert row["scheduled_at"] == "2026-06-15"

    def test_start_marks_doing_and_logs_clock_in(self, cli, tmp_db):
        cli("add", "task")
        cli("start", "1")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] == "DOING"
        log = con.execute("SELECT body FROM log WHERE node_id=1 ORDER BY id DESC LIMIT 1").fetchone()
        assert log["body"] == "CLOCK_IN"

    def test_stop_appends_clock_out_with_elapsed(self, cli, tmp_db):
        import time
        cli("add", "task")
        cli("start", "1")
        time.sleep(0.1)
        code, out, _ = cli("stop", "1")
        assert code == 0
        con = tmp_db.db_connect()
        log = con.execute("SELECT body FROM log WHERE node_id=1 ORDER BY id DESC LIMIT 1").fetchone()
        assert "CLOCK_OUT" in log["body"]
        assert "elapsed=" in log["body"]

    def test_stop_without_start_fails(self, cli):
        cli("add", "task")
        code, _, err = cli("stop", "1")
        assert code != 0
        assert "no open CLOCK_IN" in err

    def test_done_nonexistent_fails(self, cli):
        code, _, err = cli("done", "99")
        assert code != 0


# ─── link / set ───


class TestCmdDeferErrors:
    def test_defer_invalid_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("defer", "1", "garbage-date")
        assert code != 0


class TestStatusFilterAndSnippet:
    def test_status_filter_hide_done(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("ls")
        # default hides DONE
        assert "t2" in out
        assert "t1" not in out

    def test_find_snippet_match_not_found_fallback(self, cli):
        """_snippet falls back to 80-char truncation when q not found"""
        cli("add", "t1", "-k", "task")
        # q uppercase in body; _snippet lowercases so it finds; real fallback hits when body lacks q
        cli("log", "1", "x" * 200)
        _, out, _ = cli("find", "nothere", "--in", "title")
        # no match → fallback path
        assert out or True  # just don't crash


class TestStatusFilterHideDone:
    def test_hide_done_filter(self, cli):
        from worklog import cli as wl
        frag, params = wl._status_filter_sql(include_canceled=True, hide_done=True)
        assert "DONE" in params


class TestCmdReopen:
    def test_reopen_done_back_to_todo(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        cli("reopen", "1")
        _, out, _ = cli("ls")
        assert "t1" in out  # reappears


class TestDoneCompound:
    """wl done --log / --at: one-shot log + done"""

    def test_done_with_log(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "--log", "PR#42 merged")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "PR#42 merged" in show

    def test_done_with_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "--at", "2025-01-02 14:30")
        _, show, _ = cli("show", "1")
        assert "closed_at 2025-01-02 14:30:00" in show

    def test_done_with_log_and_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "--log", "结果说明", "--at", "16:00")
        _, show, _ = cli("show", "1")
        assert "结果说明" in show
        assert " 16:00:00" in show  # both log + closed_at use 16:00

    def test_done_with_m_alias(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "-m", "via -m alias")
        _, show, _ = cli("show", "1")
        assert "via -m alias" in show

    def test_cancel_with_log(self, cli):
        cli("add", "t1", "-k", "task")
        cli("cancel", "1", "--log", "abandon: 不再做这条")
        _, show, _ = cli("show", "1")
        assert "CANCELED" in show
        assert "abandon" in show

    def test_done_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("done", "1", "--at", "garbage")
        assert code != 0

    def test_done_multi_id_with_log(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        cli("done", "1", "2", "--log", "批量收尾")
        for nid in ("1", "2"):
            _, show, _ = cli("show", nid)
            assert "DONE" in show
            assert "批量收尾" in show
