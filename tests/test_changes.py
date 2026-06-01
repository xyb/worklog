"""Tests for changes (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestChanges:
    def _seed(self, cli):
        cli("add", "projectX", "-k", "project", "-p", "A", "-t", "projX")  # 1
        cli("add", "completed task", "-k", "task", "-t", "projX")             # 2
        cli("add", "open task", "-k", "task", "-t", "projX")           # 3
        cli("add", "task with log", "-k", "task", "-t", "projX")              # 4
        cli("done", "2")
        cli("log", "4", "今天的进展")

    def test_changes_today_window(self, cli):
        from datetime import date
        self._seed(cli)
        today = date.today().isoformat()
        code, out, _ = cli("changes", "--since", today, "--until", today)
        assert code == 0
        assert "projectX" in out
        assert "done 1" in out and "completed task" in out
        assert "added open" in out and "open task" in out
        assert "node(s) with progress logs" in out

    def test_changes_empty_window(self, cli):
        self._seed(cli)
        code, out, _ = cli("changes", "--since", "2020-01-01", "--until", "2020-01-02")
        assert "no project changes" in out

    def test_changes_week_resolves(self, cli):
        from datetime import date
        self._seed(cli)
        iso = date.today().isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        code, out, _ = cli("changes", "--week", wk)
        assert code == 0
        assert "projectX" in out

    def test_changes_month_resolves(self, cli):
        from datetime import date
        self._seed(cli)
        mo = date.today().strftime("%Y-%m")
        code, out, _ = cli("changes", "--month", mo)
        assert code == 0
        assert "projectX" in out
