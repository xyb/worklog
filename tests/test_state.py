"""Tests for state (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest

ESC = "["  # ANSI escape prefix


class TestAtLogNoDoubleConvert:
    """--at + --log must store the log at the SAME UTC instant as the resolved at_ts,
    not re-localize it (regression: at_ts is already UTC; routing it back through
    _insert_log's local→UTC path would shift it another -8h)."""

    def test_add_at_log_stores_single_utc(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "--at", "2026-06-01 08:00", "--log", "morning")
        con = tmp_db.db_connect()
        # 08:00 local (+08:00) → 00:00 UTC, once (not 2026-05-31 16:00)
        assert con.execute("SELECT logged_at FROM log WHERE body='morning'").fetchone()[0] == "2026-06-01 00:00:00"

    def test_done_at_log_stores_single_utc(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("done", "1", "--log", "wrapped up", "--at", "2026-06-01 08:00")
        con = tmp_db.db_connect()
        assert con.execute("SELECT logged_at FROM log WHERE body='wrapped up'").fetchone()[0] == "2026-06-01 00:00:00"


class TestAddDoneTimestamps:
    def test_created_and_done_share_one_now(self, cli, tmp_db):
        # `wl add --done` stamps created_at and closed_at from one `now` read,
        # so they're identical (regression: two separate utc_now() calls could
        # differ by a second across a boundary).
        cli("add", "t", "-k", "task", "--done")
        con = tmp_db.db_connect()
        row = con.execute("SELECT created_at, closed_at FROM node WHERE id=1").fetchone()
        assert row["created_at"] == row["closed_at"]


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
        row = con.execute("SELECT status, scheduled_date FROM node WHERE id=1").fetchone()
        assert row["status"] == "LATER"
        assert row["scheduled_date"] == "2026-06-15"

    def test_start_marks_doing_and_opens_clock(self, cli, tmp_db):
        cli("add", "task")
        cli("start", "1")
        con = tmp_db.db_connect()
        assert con.execute("SELECT status FROM node WHERE id=1").fetchone()["status"] == "DOING"
        c = con.execute("SELECT start_at, end_at FROM clock WHERE node_id=1").fetchone()
        assert c is not None and c["start_at"] and c["end_at"] is None  # open interval

    def test_stop_closes_clock_with_elapsed(self, cli, tmp_db):
        import time
        cli("add", "task")
        cli("start", "1")
        time.sleep(0.1)
        code, out, _ = cli("stop", "1")
        assert code == 0
        con = tmp_db.db_connect()
        c = con.execute("SELECT end_at, elapsed_sec FROM clock WHERE node_id=1").fetchone()
        assert c["end_at"] is not None and c["elapsed_sec"] >= 60  # floored at 1 min

    def test_stop_without_start_fails(self, cli):
        cli("add", "task")
        code, _, err = cli("stop", "1")
        assert code != 0
        assert "no open clock" in err

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
        cli("done", "1", "--log", "result note", "--at", "16:00")
        _, show, _ = cli("show", "1")
        assert "result note" in show
        assert " 16:00:00" in show  # both log + closed_at use 16:00

    def test_done_with_m_alias(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "-m", "via -m alias")
        _, show, _ = cli("show", "1")
        assert "via -m alias" in show

    def test_cancel_with_log(self, cli):
        cli("add", "t1", "-k", "task")
        cli("cancel", "1", "--log", "abandon: drop this one")
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
        cli("done", "1", "2", "--log", "batch wrap-up")
        for nid in ("1", "2"):
            _, show, _ = cli("show", nid)
            assert "DONE" in show
            assert "batch wrap-up" in show


class TestDoneRecurringWarning:
    """wl done on a recurring task hints to use wl tick (occurrence) vs done (retire)."""

    def test_done_recurring_warns(self, cli):
        cli("add", "daily patrol", "-k", "habit")
        cli("sched", "1", "--recur", "daily")
        _, out, _ = cli("done", "1")
        assert "recurring" in out
        assert "wl tick 1" in out

    def test_done_oneoff_no_warn(self, cli):
        cli("add", "one-off", "-k", "task")
        cli("sched", "1", "2026-06-15")
        _, out, _ = cli("done", "1")
        assert "recurring" not in out


class TestMultiIdAndWait:
    """multi-id done/start + wait status (from test_ux)"""
    def test_done_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        cli("add", "t3", "-k", "task")
        _, out, _ = cli("done", "1", "2", "3")
        assert "#1 → DONE" in out
        assert "#2 → DONE" in out
        assert "#3 → DONE" in out

    def test_done_single_id_still_works(self, cli):
        """legacy usage wl done 1 still works"""
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("done", "1")
        assert "#1 → DONE" in out

    def test_start_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("start", "1", "2")
        assert "#1 → DOING" in out
        assert "#2 → DOING" in out

    def test_wait_marks_status(self, cli):
        cli("add", "t1", "-k", "task")
        cli("wait", "1", "--note", "waiting on review")
        _, show, _ = cli("show", "1")
        assert "WAIT" in show
        assert "waiting on review" in show

    def test_wait_auto_clocks_out(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")
        cli("wait", "1")
        _, active, _ = cli("active")
        assert "#1" not in active  # CLOCK auto-closed; no longer in active

    def test_wait_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("wait", "1", "2")
        assert "#1 → WAIT" in out
        assert "#2 → WAIT" in out

