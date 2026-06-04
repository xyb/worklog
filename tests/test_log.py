"""Tests for log (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestLog:
    def test_add_log_to_task(self, cli, tmp_db):
        cli("add", "task")
        cli("log", "1", "read materials about A today")
        con = tmp_db.db_connect()
        rows = list(con.execute("SELECT body FROM log WHERE node_id=1"))
        assert len(rows) == 1
        assert rows[0]["body"] == "read materials about A today"

    def test_multiple_logs_in_order(self, cli, tmp_db):
        cli("add", "task")
        cli("log", "1", "5/18 first entry")
        cli("log", "1", "5/19 second entry")
        cli("log", "1", "5/20 third entry")
        con = tmp_db.db_connect()
        bodies = [r["body"] for r in con.execute("SELECT body FROM log WHERE node_id=1 ORDER BY id")]
        assert bodies == ["5/18 first entry", "5/19 second entry", "5/20 third entry"]

    def test_log_nonexistent_node_fails(self, cli):
        code, _, err = cli("log", "99", "no such node")
        assert code != 0
        assert "not found" in err

    def test_log_long_body(self, cli, tmp_db):
        long_body = "retrospective: " + "x" * 500
        cli("add", "task")
        cli("log", "1", long_body)
        con = tmp_db.db_connect()
        row = con.execute("SELECT body FROM log WHERE node_id=1").fetchone()
        assert len(row["body"]) > 500


# ─── done / defer / start / stop ───


class TestLogHistoricalDate:
    """when migrating historical data, a log's logged_at must land on the original day, not import day"""

    def test_import_log_iso_prefix_sets_date(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"历史","logs":["2026-05-06 起头","2026-05-08 跑通"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert "2026-05-06" in out and "起头" in out
        assert "2026-05-08" in out

    def test_import_log_no_date_uses_today(self, cli, tmp_path):
        import datetime as dt
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"x","logs":["无日期一条"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert dt.date.today().isoformat() in out  # no date → today

    def test_log_cmd_date_flag(self, cli):
        cli("add", "task")
        cli("log", "1", "历史进展", "--date", "2026-05-10")
        code, out, _ = cli("show", "1")
        assert "2026-05-10" in out and "历史进展" in out

    def test_log_invalid_date_rejected(self, cli):
        cli("add", "task")
        code, out, err = cli("log", "1", "x", "--date", "2026-13-40")
        assert code != 0  # invalid date rejected

    def test_import_log_dict_form(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"y","logs":[{"date":"2026-05-01","body":"dict 形式"}]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert "2026-05-01" in out and "dict 形式" in out

    def test_log_timeline_sorted_by_real_date(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"z","logs":["2026-05-20 晚","2026-05-05 早"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert out.index("2026-05-05") < out.index("2026-05-20")  # sorted by real date


class TestRelog:
    def test_relog_body_positional(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "wrong")
        _, out, _ = cli("relog", "1", "fixed")
        assert "relog #1" in out
        _, show, _ = cli("show", "1")
        assert "fixed" in show
        assert "wrong" not in show

    def test_relog_body_via_m(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "wrong")
        cli("relog", "L1", "-m", "fixed via m")
        _, show, _ = cli("show", "1")
        assert "fixed via m" in show

    def test_relog_at_hhmm(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        cli("relog", "#L1", "--at", "14:30")
        _, show, _ = cli("show", "1")
        assert "14:30:00" in show

    def test_relog_at_full_ts(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        cli("relog", "1", "--at", "2025-01-02 09:15")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 09:15:00" in show

    def test_relog_at_only_date(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        cli("relog", "1", "--at", "2025-01-02")
        _, show, _ = cli("show", "1")
        assert "2025-01-02" in show

    def test_relog_body_and_at_together(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "old")
        cli("relog", "1", "new", "--at", "10:00")
        _, show, _ = cli("show", "1")
        assert "new" in show
        assert "10:00:00" in show
        assert "old" not in show


    def test_relog_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "--at", "25:00")
        assert code != 0
        code2, _, _ = cli("relog", "1", "--at", "2026-13-01")
        assert code2 != 0

    def test_relog_not_found(self, cli):
        code, _, err = cli("relog", "999999", "x")
        assert code != 0
        assert "not found" in err or "not found" in _

    def test_relog_body_and_m_conflict(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "pos", "-m", "msg")
        assert code != 0

    def test_relog_at_total_garbage(self, cli):
        """--at doesn't look like a time (not HH:MM / date / full ts) → ValueError branch"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "--at", "totally-not-a-time")
        assert code != 0

    def test_relog_editor_modify(self, cli, monkeypatch):
        """no body / no --at → EDITOR path; monkeypatch subprocess.call writes new text"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "old")
        def fake_call(argv):
            with open(argv[-1], "w") as f:
                f.write("edited body")
            return 0
        import subprocess as _sp
        monkeypatch.setattr(_sp, "call", fake_call)
        _, out, _ = cli("relog", "1")
        assert "relog #1" in out
        _, show, _ = cli("show", "1")
        assert "edited body" in show

    def test_relog_editor_unchanged_cancels(self, cli, monkeypatch):
        """EDITOR exits with text unchanged → canceled; body preserved"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "keep me")
        import subprocess as _sp
        monkeypatch.setattr(_sp, "call", lambda argv: 0)  # don't touch the file
        _, out, _ = cli("relog", "1")
        assert "cancel" in out or "unchanged" in out
        _, show, _ = cli("show", "1")
        assert "keep me" in show

    def test_relog_editor_rc_nonzero_cancels(self, cli, monkeypatch):
        """EDITOR exit code != 0 → treated as abort"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "keep me")
        import subprocess as _sp
        monkeypatch.setattr(_sp, "call", lambda argv: 1)
        _, out, _ = cli("relog", "1")
        assert "cancel" in out or "unchanged" in out


class TestUnlogErrorPaths:
    """cmd_unlog error paths: log_id missing / --node missing / bad --date / no log that day."""

    def test_unlog_log_id_not_found(self, cli):
        code, _, err = cli("unlog", "9999")
        assert code != 0
        assert "not found" in err or "not found" in _

    def test_unlog_node_not_found(self, cli):
        code, _, err = cli("unlog", "--node", "999")
        assert code != 0
        assert "not found" in err or "not found" in _

    def test_unlog_invalid_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("unlog", "--node", "1", "--date", "junk-date")
        assert code != 0

    def test_unlog_node_no_log_that_day(self, cli):
        cli("add", "t1", "-k", "task")
        # no log today
        _, out, _ = cli("unlog", "--node", "1")
        assert "no non-CLOCK logs" in out or "no logs" in out


class TestInsertLogAutoStatus:
    def test_log_to_todo_promotes_to_doing(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "first progress")
        _, out, _ = cli("ls", "--all")
        # marker [/] may be followed by align spaces before #id
        assert "[/]" in out and "#1" in out


class TestLogTimeAndDate:
    """cmd_log --date + --time combinations"""

    def test_log_with_date_only(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--date", "2025-01-02")
        _, show, _ = cli("show", "1")
        assert "2025-01-02" in show

    def test_log_with_date_and_time(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--date", "2025-01-02", "--time", "14:30")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 14:30:00" in show

    def test_log_with_time_only(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--time", "09:15")
        _, show, _ = cli("show", "1")
        from datetime import date
        assert f"{date.today().isoformat()} 09:15:00" in show

    def test_log_with_time_seconds(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--time", "09:15:30")
        _, show, _ = cli("show", "1")
        assert "09:15:30" in show

    def test_log_invalid_time(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("log", "1", "x", "--time", "25:00")
        assert code != 0

    def test_log_invalid_time_with_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("log", "1", "x", "--date", "2025-01-02", "--time", "99:99")
        assert code != 0


class TestInsertLogClockNotPromote:
    def test_clock_in_does_not_promote(self, cli):
        """CLOCK_IN log must not auto-advance TODO to DOING (start command sets status itself)"""
        cli("add", "t1", "-k", "task")
        # don't call start; insert CLOCK_IN log via internal API
        from worklog import cli as wl
        con = wl.db_connect()
        wl._insert_log(con, 1, "CLOCK_IN")
        con.commit()
        # status must not be changed by _insert_log (CLOCK is not a progress log)
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] in (None, "TODO")


class TestResolveAtTsEdges:
    """_resolve_at_ts handles HH:MM, YYYY-MM-DD, full ISO; rejects garbage."""

    def test_garbage_at_raises_value_error(self):
        from worklog.helpers import _resolve_at_ts
        with pytest.raises(ValueError, match="invalid --at"):
            _resolve_at_ts("not a real timestamp")

    def test_date_only_uses_current_time(self):
        from worklog.helpers import _resolve_at_ts
        out = _resolve_at_ts("2026-06-15")
        assert out.startswith("2026-06-15 ")
        assert len(out) == 19  # YYYY-MM-DD HH:MM:SS

    def test_full_iso_with_T_separator(self):
        from worklog.helpers import _resolve_at_ts
        out = _resolve_at_ts("2026-06-15T09:30")
        assert out == "2026-06-15 09:30:00"

    def test_term_width_oserror_falls_back_to_80(self, monkeypatch):
        """shutil.get_terminal_size raising OSError → default 80."""
        import shutil
        from worklog import helpers
        def boom(*a, **k):
            raise OSError("no tty")
        monkeypatch.setattr(shutil, "get_terminal_size", boom)
        assert helpers._term_width() == 80
