"""Tests for summary (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestSummary:
    def _seed(self, cli):
        cli("add", "projectX", "-k", "project", "-t", "projX,work")  # 1
        cli("add", "completed1", "-k", "task", "-t", "projX,work")      # 2
        cli("add", "completed2", "-k", "task", "-t", "work")            # 3
        cli("add", "personal completed", "-k", "task", "-t", "personal")      # 4
        cli("add", "open", "-k", "task", "-t", "work")             # 5
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
        cli("add", "projectY", "-k", "project", "-t", "projY")  # 1
        cli("add", "doing task", "-k", "task", "-t", "projY,planned", "--parent", "1")  # 2
        cli("add", "todo task", "-k", "task", "-t", "projY,planned", "--parent", "1")    # 3
        cli("start", "2")  # DOING
        code, out, _ = cli("summary")
        assert "doing (DOING)" in out
        assert "todo (TODO)" in out
        assert "·planned" in out

    def test_summary_orphan_bucket(self, cli):
        from datetime import date
        cli("add", "no-project task", "-k", "task", "-t", "planned")
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
        cli("add", "ongoing task", "-k", "task", "-t", "work")
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


class TestSummaryJson:
    def test_summary_json(self, cli):
        cli("add", "done task", "-k", "task", "-t", "work")
        cli("add", "open task", "-k", "task", "-t", "work")
        cli("done", "1")
        import json
        code, out, _ = cli("summary", "--since", "2026-01-01", "--until", "2099-12-31", "-o", "json")
        d = json.loads(out)
        assert code == 0
        assert d["totals"]["done"] == 1
        assert d["by_direction"]["work"] == 1
        assert [n["title"] for n in d["done"]] == ["done task"]
        assert "open task" in [n["title"] for n in d["pending"]]
