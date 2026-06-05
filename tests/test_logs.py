"""Tests for logs (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


def _disp_width(s):
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


class TestLogsLineWidth:
    """#415: flat logs row must fit the terminal width — a wide CJK title used to push
    the body past the edge and wrap to a second line (fixed indent_cols was too small)."""

    def test_flat_logs_row_fits_width_with_cjk_title(self, cli, monkeypatch):
        from worklog import helpers
        monkeypatch.setattr(helpers, "_term_width", lambda: 80)
        cli("add", "标" * 30, "-k", "task")          # 30 CJK chars = 60 display cols
        cli("log", "1", "a very long log body to test wrapping" * 4)
        _, out, _ = cli("logs", "today")
        body_lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("(")]
        assert body_lines
        for ln in body_lines:
            assert _disp_width(ln) <= 80, f"line overflowed 80 cols ({_disp_width(ln)}): {ln!r}"


class TestLogs:
    def test_logs_list_all(self, cli):
        cli("add", "a")
        cli("add", "b")
        cli("log", "1", "log A1")
        cli("log", "1", "log A2")
        cli("log", "2", "log B1")
        code, out, _ = cli("logs")
        assert "log A1" in out
        assert "log A2" in out
        assert "log B1" in out

    def test_logs_filter_by_id(self, cli):
        cli("add", "a")
        cli("add", "b")
        cli("log", "1", "log A1")
        cli("log", "2", "log B1")
        code, out, _ = cli("logs", "--id", "1")
        assert "log A1" in out
        assert "log B1" not in out

    def test_logs_group_day(self, cli):
        cli("add", "2026", "-k", "year")
        cli("add", "proj", "-k", "project", "-t", "work", "--parent", "1")
        cli("add", "t", "-k", "task", "-t", "work", "--parent", "2")
        cli("log", "3", "progressX", "--date", "2026-05-28")
        code, out, _ = cli("logs", "--group", "day", "--since", "2026-05-01")
        assert "2026-05-28" in out  # date header
        assert "work" in out         # bucket
        assert "proj" in out         # project
        assert "progressX" in out

    def test_logs_default_recent_window(self, cli):
        cli("add", "a")
        cli("log", "1", "today log", )                       # default today
        cli("log", "1", "long ago", "--date", "2020-01-01")
        code, out, _ = cli("logs")  # no args = last 7 days
        assert "today log" in out
        assert "long ago" not in out  # outside default window

    def test_logs_since_overrides_window(self, cli):
        cli("add", "a")
        cli("log", "1", "long ago", "--date", "2020-01-01")
        code, out, _ = cli("logs", "--since", "2020-01-01")
        assert "long ago" in out


# --- edge cases / cascade ---


class TestLogsCoverageGaps:
    """cmd_logs gaps: yesterday preset / bad --date / missing id hint / empty window hint /
    brief + by_task date-listing branch."""

    def test_logs_yesterday_preset(self, cli):
        from datetime import date, timedelta
        cli("add", "t1", "-k", "task")
        yday = (date.today() - timedelta(days=1)).isoformat()
        cli("log", "1", "y-log", "--date", yday)
        _, out, _ = cli("logs", "yesterday")
        assert "y-log" in out

    def test_logs_invalid_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("logs", "--date", "garbage")
        assert code != 0

    def test_logs_unknown_id_hint(self, cli):
        _, out, _ = cli("logs", "--id", "9999")
        assert "does not exist" in out

    def test_logs_id_exists_but_empty_window(self, cli):
        cli("add", "t1", "-k", "task")  # no log
        _, out, _ = cli("logs", "--id", "1")
        assert "no logs" in out

    def test_logs_empty_window_hint(self, cli):
        from datetime import date, timedelta
        # window from a year ago; nothing there
        old = (date.today() - timedelta(days=400)).isoformat()
        _, out, _ = cli("logs", "--since", old, "--until", old)
        assert "no logs" in out

    def test_logs_by_task_brief_no_body(self, cli):
        """cmd_logs brief + by_task: list each log's date without expanding the body."""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "aaaa-body")
        cli("log", "1", "bbbb-body")
        _, out, _ = cli("logs", "--by-task", "--no-body")
        from datetime import date
        today = date.today().isoformat()
        assert "t1" in out
        assert today in out  # date string should appear
        assert "aaaa-body" not in out and "bbbb-body" not in out
