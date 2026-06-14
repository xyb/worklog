"""Tests for logs (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


def _disp_width(s):
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


class TestLogsLineWidth:
    """flat logs row must fit the terminal width — a wide CJK title used to push
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


class TestLogsJson:
    def test_logs_json_array(self, cli):
        cli("add", "t", "-k", "task")
        cli("log", "1", "first"); cli("log", "1", "second")
        import json
        code, out, _ = cli("logs", "--id", "1", "-o", "json")
        d = json.loads(out)
        assert code == 0 and [l["body"] for l in d] == ["first", "second"]
        assert set(d[0].keys()) >= {"id", "node_id", "logged_at", "tag", "body", "node_title"}

    def test_logs_json_empty_is_array(self, cli):
        cli("add", "t", "-k", "task")
        import json
        _, out, _ = cli("logs", "--id", "1", "-o", "json")
        assert json.loads(out) == []


# --- log-rendering format (merged from test_log_format.py): tail elision + --log-format ---

class TestLogTailDefault:
    """wl day / wl logs --by-task / wl tree day-activity / wl show timeline:
    logs default to last 3 only (timeline last 5), middle elided; --all-logs/--all-timelines expands all.
    """

    def test_day_default_tail_3(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        for i in range(6):
            cli("log", "1", f"log-{i}")
        _, out, _ = cli("day", today)
        assert "log-5" in out and "log-4" in out and "log-3" in out
        assert "log-0" not in out and "log-1" not in out
        assert "3 earlier logs elided" in out

    def test_day_all_logs_full(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        for i in range(6):
            cli("log", "1", f"log-{i}")
        _, out, _ = cli("day", today, "--all-logs")
        for i in range(6):
            assert f"log-{i}" in out
        assert "elided" not in out

    def test_day_log_tail_n_override(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        for i in range(6):
            cli("log", "1", f"log-{i}")
        _, out, _ = cli("day", today, "--log-tail", "1")
        assert "log-5" in out
        assert "log-4" not in out
        assert "5 earlier logs elided" in out

    def test_logs_by_task_default_tail_3(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        for i in range(5):
            cli("log", "1", f"x{i}")
        _, out, _ = cli("logs", "--by-task", "--date", today)
        assert "showing last 3" in out
        assert "x4" in out and "x3" in out and "x2" in out
        assert "x0" not in out

    def test_logs_all_logs(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        for i in range(5):
            cli("log", "1", f"x{i}")
        _, out, _ = cli("logs", "--by-task", "--date", today, "--all-logs")
        for i in range(5):
            assert f"x{i}" in out
        assert "showing last" not in out

    def test_show_timeline_default_tail_5(self, cli):
        cli("add", "t1", "-k", "task")
        for i in range(10):
            cli("log", "1", f"e{i}")
        _, out, _ = cli("show", "1")
        # 10 logs + 1 created = 11 events; default tail=5, 6 elided
        assert "showing last 5" in out
        assert "e9" in out and "e5" in out
        assert "e0" not in out

    def test_show_all_timelines(self, cli):
        cli("add", "t1", "-k", "task")
        for i in range(10):
            cli("log", "1", f"e{i}")
        _, out, _ = cli("show", "1", "--all-timelines")
        for i in range(10):
            assert f"e{i}" in out
        assert "elided" not in out


class TestLogFormatOneline:
    """global --log-format {oneline,full}: default oneline truncates log body by terminal width; full expands.
    Unified across wl day / wl tree / wl logs / wl show."""

    LONG_BODY = "x" * 200  # 200 chars exceeds any reasonable terminal width

    def test_truncate_helper_oneline_default(self):
        from worklog import cli as wl
        out = wl._truncate_log_body(self.LONG_BODY, indent_cols=10, full=False)
        assert out.endswith("…")
        assert len(out) < 200

    def test_truncate_helper_full(self):
        from worklog import cli as wl
        out = wl._truncate_log_body(self.LONG_BODY, indent_cols=10, full=True)
        assert out == self.LONG_BODY

    def test_truncate_helper_short_body_unchanged(self):
        from worklog import cli as wl
        assert wl._truncate_log_body("short", 10, full=False) == "short"

    def test_log_full_helper(self):
        from worklog import cli as wl
        from types import SimpleNamespace
        assert wl._log_full(SimpleNamespace(log_format="full")) is True
        assert wl._log_full(SimpleNamespace(log_format="oneline")) is False
        assert wl._log_full(SimpleNamespace()) is False  # missing attr defaults to False

    def test_day_oneline_truncates(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("day", today)
        # after body truncation there should be no run of 200 x's
        assert self.LONG_BODY not in out
        assert "…" in out

    def test_day_full_keeps_body(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("--log-format", "full", "day", today)
        assert self.LONG_BODY in out

    def test_logs_by_task_oneline(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("logs", "--by-task", "today")
        assert self.LONG_BODY not in out
        assert "…" in out

    def test_logs_by_task_full(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("--log-format", "full", "logs", "--by-task", "today")
        assert self.LONG_BODY in out

    def test_show_timeline_oneline(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("show", "1")
        assert self.LONG_BODY not in out
        assert "…" in out

    def test_show_timeline_full(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("--log-format", "full", "show", "1")
        assert self.LONG_BODY in out

    def test_invalid_log_format_rejected(self, cli):
        import pytest
        with pytest.raises(SystemExit):
            cli("--log-format", "garbage", "day")
