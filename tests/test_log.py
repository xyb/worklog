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


class TestLogShow:
    """`wl log show #L<id>` — view a single log entry's full (untruncated) content.

    The list views (`wl logs`, `wl log ls`, `wl show` timeline) truncate each log to
    one line; this verb prints the whole body for one log."""

    def test_show_prints_full_untruncated_body(self, cli):
        long_body = "retrospective: " + "x" * 400
        cli("add", "task")
        cli("log", "1", long_body)
        code, out, _ = cli("log", "show", "#L1")
        assert code == 0
        assert long_body in out
        assert "…" not in out  # not the oneline-truncated form

    def test_show_accepts_hash_L_and_bare_forms(self, cli):
        cli("add", "task")
        cli("log", "1", "hello full body world")
        for form in ("#L1", "L1", "1"):
            code, out, _ = cli("log", "show", form)
            assert code == 0, f"form {form!r} failed"
            assert "hello full body world" in out

    def test_show_header_has_log_id_and_owning_node(self, cli):
        cli("add", "my distinctive task title")
        cli("log", "1", "some body content")
        code, out, _ = cli("log", "show", "#L1")
        assert "#L1" in out
        assert "my distinctive task title" in out  # owning node title
        assert "#1" in out  # owning node id

    def test_show_nonexistent_log_fails(self, cli):
        cli("add", "task")
        code, _, err = cli("log", "show", "#L999")
        assert code != 0
        assert "999" in err or "no log" in err.lower()


# ─── done / defer / start / stop ───


class TestLogHistoricalDate:
    """when migrating historical data, a log's logged_at must land on the original day, not import day"""

    def test_import_log_iso_prefix_sets_date(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"history","logs":["2026-05-06 start","2026-05-08 works"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert "2026-05-06" in out and "start" in out
        assert "2026-05-08" in out

    def test_import_log_no_date_uses_today(self, cli, tmp_path):
        import datetime as dt
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"x","logs":["one without date"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert dt.date.today().isoformat() in out  # no date → today

    def test_log_cmd_date_flag(self, cli):
        cli("add", "task")
        cli("log", "1", "historical progress", "--date", "2026-05-10")
        code, out, _ = cli("show", "1")
        assert "2026-05-10" in out and "historical progress" in out

    def test_log_invalid_date_rejected(self, cli):
        cli("add", "task")
        code, out, err = cli("log", "1", "x", "--date", "2026-13-40")
        assert code != 0  # invalid date rejected

    def test_import_log_dict_form(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"y","logs":[{"date":"2026-05-01","body":"dict form"}]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert "2026-05-01" in out and "dict form" in out

    def test_log_timeline_sorted_by_real_date(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"z","logs":["2026-05-20 evening","2026-05-05 morning"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert out.index("2026-05-05") < out.index("2026-05-20")  # sorted by real date


class TestRelog:
    def test_relog_body_positional(self, cli):
        cli("add", "t1")
        cli("log", "1", "wrong")
        _, out, _ = cli("relog", "1", "fixed")
        assert "relog #1" in out
        _, show, _ = cli("show", "1")
        assert "fixed" in show
        assert "wrong" not in show

    def test_relog_body_via_m(self, cli):
        cli("add", "t1")
        cli("log", "1", "wrong")
        cli("relog", "L1", "-m", "fixed via m")
        _, show, _ = cli("show", "1")
        assert "fixed via m" in show

    def test_relog_at_hhmm(self, cli):
        cli("add", "t1")
        cli("log", "1", "x")
        cli("relog", "#L1", "--at", "14:30")
        _, show, _ = cli("show", "1")
        assert "14:30:00" in show

    def test_relog_at_full_ts(self, cli):
        cli("add", "t1")
        cli("log", "1", "x")
        cli("relog", "1", "--at", "2025-01-02 09:15")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 09:15:00" in show

    def test_relog_at_only_date(self, cli):
        cli("add", "t1")
        cli("log", "1", "x")
        cli("relog", "1", "--at", "2025-01-02")
        _, show, _ = cli("show", "1")
        assert "2025-01-02" in show

    def test_relog_body_and_at_together(self, cli):
        cli("add", "t1")
        cli("log", "1", "old")
        cli("relog", "1", "new", "--at", "10:00")
        _, show, _ = cli("show", "1")
        assert "new" in show
        assert "10:00:00" in show
        assert "old" not in show


    def test_relog_invalid_at(self, cli):
        cli("add", "t1")
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
        cli("add", "t1")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "pos", "-m", "msg")
        assert code != 0

    def test_relog_at_total_garbage(self, cli):
        """--at doesn't look like a time (not HH:MM / date / full ts) → ValueError branch"""
        cli("add", "t1")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "--at", "totally-not-a-time")
        assert code != 0

    def test_relog_editor_modify(self, cli, monkeypatch):
        """no body / no --at → EDITOR path; monkeypatch subprocess.call writes new text"""
        cli("add", "t1")
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
        cli("add", "t1")
        cli("log", "1", "keep me")
        import subprocess as _sp
        monkeypatch.setattr(_sp, "call", lambda argv: 0)  # don't touch the file
        _, out, _ = cli("relog", "1")
        assert "cancel" in out or "unchanged" in out
        _, show, _ = cli("show", "1")
        assert "keep me" in show

    def test_relog_editor_rc_nonzero_cancels(self, cli, monkeypatch):
        """EDITOR exit code != 0 → treated as abort"""
        cli("add", "t1")
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
        cli("add", "t1")
        code, _, _ = cli("unlog", "--node", "1", "--date", "junk-date")
        assert code != 0

    def test_unlog_node_no_log_that_day(self, cli):
        cli("add", "t1")
        # no log today
        _, out, _ = cli("unlog", "--node", "1")
        assert "no non-CLOCK logs" in out or "no logs" in out


class TestInsertLogAutoStatus:
    def test_log_to_todo_promotes_to_doing(self, cli):
        cli("add", "t1")
        cli("log", "1", "first progress")
        _, out, _ = cli("ls", "--all")
        # marker [/] may be followed by align spaces before #id
        assert "[/]" in out and "#1" in out


class TestLogTimeAndDate:
    """cmd_log --date + --time combinations"""

    def test_log_with_date_only(self, cli):
        cli("add", "t1")
        cli("log", "1", "x", "--date", "2025-01-02")
        _, show, _ = cli("show", "1")
        assert "2025-01-02" in show

    def test_log_with_date_and_time(self, cli):
        cli("add", "t1")
        cli("log", "1", "x", "--date", "2025-01-02", "--time", "14:30")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 14:30:00" in show

    def test_log_with_time_only(self, cli):
        cli("add", "t1")
        cli("log", "1", "x", "--time", "09:15")
        _, show, _ = cli("show", "1")
        from datetime import date
        assert f"{date.today().isoformat()} 09:15:00" in show

    def test_log_with_time_seconds(self, cli):
        cli("add", "t1")
        cli("log", "1", "x", "--time", "09:15:30")
        _, show, _ = cli("show", "1")
        assert "09:15:30" in show

    def test_log_invalid_time(self, cli):
        cli("add", "t1")
        code, _, _ = cli("log", "1", "x", "--time", "25:00")
        assert code != 0

    def test_log_invalid_time_with_date(self, cli):
        cli("add", "t1")
        code, _, _ = cli("log", "1", "x", "--date", "2025-01-02", "--time", "99:99")
        assert code != 0


class TestInsertLogClockNotPromote:
    def test_clock_in_does_not_promote(self, cli):
        """CLOCK_IN log must not auto-advance TODO to DOING (start command sets status itself)"""
        cli("add", "t1")
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
        from worklog import timeutil as tu
        out = _resolve_at_ts("2026-06-15")  # date only → that local day, current time, stored UTC
        assert len(out) == 19  # YYYY-MM-DD HH:MM:SS
        assert tu.local_day_of(out) == "2026-06-15"  # local calendar day is the one asked for

    def test_full_iso_with_T_separator(self):
        from worklog.helpers import _resolve_at_ts
        # 09:30 local (+08:00) is stored as 01:30 UTC
        out = _resolve_at_ts("2026-06-15T09:30")
        assert out == "2026-06-15 01:30:00"

    def test_term_width_oserror_falls_back_to_80(self, monkeypatch):
        """shutil.get_terminal_size raising OSError → default 80."""
        import shutil
        from worklog import helpers
        def boom(*a, **k):
            raise OSError("no tty")
        monkeypatch.setattr(shutil, "get_terminal_size", boom)
        assert helpers._term_width() == 80


# --- log auto-status promotion + duration display (from test_ux) ---

class TestDurationAndAutoProgress:
    """§26 duration summary + §27 auto status advancement."""

    def test_log_keep_status_disables_auto(self, cli):
        cli("add", "t1")
        _, out, _ = cli("log", "1", "progress", "--keep-status")
        assert "TODO → DOING" not in out
        _, show, _ = cli("show", "1")
        # still TODO
        assert "TODO" in show

    def test_log_with_date_keeps_status(self, cli):
        """backfilling a historical log does not change status"""
        cli("add", "t1")
        cli("log", "1", "history", "--date", "2020-01-01")
        _, show, _ = cli("show", "1")
        assert "TODO" in show

    def test_log_done_not_reverted(self, cli):
        """logging after DONE does not auto-revert status"""
        cli("add", "t1")
        cli("done", "1")
        cli("log", "1", "addendum")
        _, show, _ = cli("show", "1")
        assert "DONE" in show

    def test_duration_format(self, cli):
        """log span duration shown as [Xh Ym]"""
        cli("add", "t1")
        cli("log", "1", "a", "--time", "10:00")
        cli("log", "1", "b", "--time", "12:30")
        _, out, _ = cli("ls")
        assert "[2h30m]" in out

    def test_duration_under_hour(self, cli):
        cli("add", "t1")
        cli("log", "1", "a", "--time", "10:00")
        cli("log", "1", "b", "--time", "10:45")
        _, out, _ = cli("ls")
        assert "[45m]" in out

    def test_duration_zero_hidden(self, cli):
        """single log has no span; no duration shown"""
        cli("add", "t1")
        cli("log", "1", "single")
        _, out, _ = cli("ls")
        assert "[" not in out.split("single")[1] if "single" in out else True


class TestLogDateWords:
    """log --date relative words + empty-body guard (from test_ux)"""
    def test_log_date_accepts_yesterday(self, cli):
        cli("add", "work item")
        cli("log", "1", "yesterday thing", "--date", "yesterday")
        _, show, _ = cli("show", "1")
        # "yesterday" is resolved to a concrete date and stored in logged_at
        from datetime import date, timedelta
        yday = (date.today() - timedelta(days=1)).isoformat()
        assert yday in show

    def test_logs_empty_body_rejected(self, cli):
        cli("add", "t1")
        code, _, err = cli("log", "1", "")
        assert code != 0
        code2, _, _ = cli("log", "1", "   ")
        assert code2 != 0


class TestLogTimestamp:
    """logged_at must ALWAYS be a full UTC instant (`YYYY-MM-DD HH:MM:SS`), never a bare date — a
    bare date loses intra-day ordering and renders no `@HH:MM`. Regression guard for every log path,
    especially `--date` (which used to store a bare date and drop the time)."""

    def _last_logged_at(self, cli, tmp_db, *log_args):
        cli("add", "t")
        cli("log", "1", "body", *log_args)
        con = tmp_db.db_connect()
        return con.execute("SELECT logged_at FROM log ORDER BY id DESC LIMIT 1").fetchone()[0]

    @staticmethod
    def _is_full_instant(ts):
        return len(ts) >= 19 and ts[10] == " " and ts[13] == ":"   # "YYYY-MM-DD HH:MM:SS"

    def test_plain_log_keeps_time(self, cli, tmp_db):
        assert self._is_full_instant(self._last_logged_at(cli, tmp_db))

    def test_date_today_keeps_time(self, cli, tmp_db):
        assert self._is_full_instant(self._last_logged_at(cli, tmp_db, "--date", "today"))

    def test_date_relative_keeps_time(self, cli, tmp_db):
        assert self._is_full_instant(self._last_logged_at(cli, tmp_db, "--date", "-1"))

    def test_date_historical_keeps_time_and_lands_on_day(self, cli, tmp_db):
        from worklog import timeutil as _tu
        ts = self._last_logged_at(cli, tmp_db, "--date", "2026-06-20")
        # tz-safe: logged_at is a UTC instant; its LOCAL day must be the requested date
        assert self._is_full_instant(ts) and _tu.local_day_of(ts) == "2026-06-20"

    def test_date_with_explicit_time(self, cli, tmp_db):
        from worklog import timeutil as _tu
        ts = self._last_logged_at(cli, tmp_db, "--date", "2026-06-20", "--time", "14:30")
        assert self._is_full_instant(ts) and _tu.local_day_of(ts) == "2026-06-20"


class TestRetag:
    """`wl retag #L<id> <tag>` — change a single log's tag (note/goal/summary/custom)."""

    def test_retag_sets_tag(self, cli, tmp_db):
        cli("add", "task")
        cli("log", "1", "progress")
        code, _, _ = cli("retag", "#L1", "goal")
        assert code == 0
        con = tmp_db.db_connect()
        assert con.execute("SELECT tag FROM log WHERE id=1").fetchone()["tag"] == "goal"

    def test_retag_note_clears_to_null(self, cli, tmp_db):
        cli("add", "task")
        cli("log", "1", "progress")
        cli("retag", "#L1", "summary")
        cli("retag", "#L1", "note")  # note -> plain note (NULL)
        con = tmp_db.db_connect()
        assert con.execute("SELECT tag FROM log WHERE id=1").fetchone()["tag"] is None

    def test_retag_nonexistent_fails(self, cli):
        cli("add", "task")
        code, _, err = cli("retag", "#L999", "goal")
        assert code != 0
