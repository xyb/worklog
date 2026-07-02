"""Tests for summary (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestSummary:
    def _seed(self, cli):
        cli("add", "projectX", "--para", "project", "-t", "projX,work")  # 1
        cli("add", "completed1", "-t", "projX,work")      # 2
        cli("add", "completed2", "-t", "work")            # 3
        cli("add", "personal completed", "-t", "personal")      # 4
        cli("add", "open", "-t", "work")             # 5
        cli("done", "2")
        cli("done", "3")
        cli("done", "4")

    def test_summary_totals(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert code == 0
        assert "done 3" in out
        assert "added-open 1" in out

    def test_summary_by_direction(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert "work: done 2" in out
        assert "personal: done 1" in out

    def test_summary_by_project(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert "=== by project ===" in out
        assert "projectX" in out and "done 1" in out

    def test_summary_done_list(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        # done items show ✓ + priority, aggregated under the project
        assert "completed1" in out and "personal completed" in out
        assert "✓" in out

    def test_summary_pending_grouped_by_status(self, cli):
        """open items group by status; DOING clearly distinct from TODO"""
        cli("add", "projectY", "--para", "project", "-t", "projY")  # 1
        cli("add", "doing task", "-t", "projY,planned", "--parent", "1")  # 2
        cli("add", "todo task", "-t", "projY,planned", "--parent", "1")    # 3
        cli("start", "2")  # DOING
        code, out, _ = cli("summary")
        assert "doing (DOING)" in out
        assert "todo (TODO)" in out
        assert "·planned" in out

    def test_summary_orphan_bucket(self, cli):
        from datetime import date
        cli("add", "no-project task", "-t", "planned")
        cli("done", "1")
        code, out, _ = cli("summary")
        assert "unassigned" in out

    def test_summary_by_day(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t, "--by", "day")
        assert code == 0
        assert "=== by day ===" in out
        assert t in out  # today's date acts as the group header
        assert "completed1" in out

    def test_summary_by_day_worked_group(self, cli):
        """a task logged (progress) on a day shows under that day's `worked` group, once —
        not double-listed in pending; this is the log-centric piece node buckets alone miss."""
        from datetime import date
        cli("add", "ongoing task", "-t", "work")
        cli("log", "1", "made progress today", "--keep-status")
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t, "--by", "day")
        assert code == 0
        assert "worked (有进展)" in out
        assert "worked 1" in out          # the logged task counts as worked
        assert out.count("ongoing task") == 1   # shown once (worked), not also in pending

    def test_summary_clock_hours(self, cli):
        import time
        from datetime import date
        cli("add", "timing task", "-t", "work")
        cli("start", "1")
        time.sleep(0.05)
        cli("stop", "1")
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert "clock" in out


class TestSummaryWindow:
    """--week/--month/--quarter/--year resolution + malformed-flag guard (no traceback)."""

    def test_bad_week_format_dies_cleanly(self, cli):
        # regression: `--week 2026-07` (a month string) used to crash with a raw ValueError
        code, _, err = cli("summary", "--week", "2026-07")
        assert code != 0 and "invalid --week" in err

    def test_bad_quarter_format_dies_cleanly(self, cli):
        code, _, err = cli("summary", "--quarter", "2026-Q9")
        assert code != 0 and "invalid --quarter" in err

    def test_quarter_window_resolves(self, cli):
        _, out, _ = cli("summary", "--quarter", "2026-Q3")
        assert "2026-07-01 ~ 2026-09-30" in out

    def test_year_window_resolves(self, cli):
        _, out, _ = cli("summary", "--year", "2026")
        assert "2026-01-01 ~ 2026-12-31" in out


class TestSummaryGoalHeader:
    """#1194: summary shows the window's time-node goal as a wl-day-style dashboard header."""

    def test_month_goal_header(self, cli):
        cli("add", "2026-07", "--prop", "type.date=month", "--prop", "date.period=2026-07")  # 1
        cli("set", "1", "goal", "JULY top5")
        _, out, _ = cli("summary", "--month", "2026-07")
        assert "⭐" in out and "JULY top5" in out

    def test_quarter_goal_header(self, cli):
        cli("add", "2026-Q3", "--prop", "type.date=quarter", "--prop", "date.period=2026-Q3")  # 1
        cli("set", "1", "goal", "Q3 okr")
        _, out, _ = cli("summary", "--quarter", "2026-Q3")
        assert "🗓" in out and "Q3 okr" in out

    def test_no_goal_no_header(self, cli):
        cli("add", "2026-07", "--prop", "type.date=month", "--prop", "date.period=2026-07")
        _, out, _ = cli("summary", "--month", "2026-07")
        assert "⭐" not in out

    def test_bare_since_until_window_has_no_header(self, cli):
        # a --since/--until window names no single time node → no goal header even if the month has one
        cli("add", "2026-07", "--prop", "type.date=month", "--prop", "date.period=2026-07")
        cli("set", "1", "goal", "JULY top5")
        _, out, _ = cli("summary", "--since", "2026-07-01", "--until", "2026-07-31")
        assert "JULY top5" not in out


class TestSummaryJson:
    def test_summary_json(self, cli):
        cli("add", "done task", "-t", "work")
        cli("add", "open task", "-t", "work")
        cli("done", "1")
        import json
        code, out, _ = cli("summary", "--since", "2026-01-01", "--until", "2099-12-31", "-o", "json")
        d = json.loads(out)
        assert code == 0
        assert d["totals"]["done"] == 1
        assert d["by_direction"]["work"] == 1
        assert [n["title"] for n in d["done"]] == ["done task"]
        assert "open task" in [n["title"] for n in d["pending"]]
