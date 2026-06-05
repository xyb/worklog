"""Tests for log_format (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


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
