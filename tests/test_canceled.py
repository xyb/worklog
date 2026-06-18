"""CANCELED visibility: hidden by default, shown with --show-canceled / --all, across
`wl ls / projects / find / day / summary` (extracted from test_ux)."""
import sqlite3
import pytest


class TestCanceledFilter:
    """§28 unified status filtering: hides CANCELED by default; --show-canceled exposes it."""

    def test_cancel_command(self, cli):
        cli("add", "dropped work")
        _, out, _ = cli("cancel", "1")
        assert "→ CANCELED" in out
        _, show, _ = cli("show", "1")
        assert "CANCELED" in show

    def test_cancel_multiple_ids(self, cli):
        cli("add", "a")
        cli("add", "b")
        _, out, _ = cli("cancel", "1", "2")
        assert "#1 → CANCELED" in out
        assert "#2 → CANCELED" in out

    def test_ls_default_hides_canceled(self, cli):
        cli("add", "active")
        cli("add", "dropped")
        cli("cancel", "2")
        _, out, _ = cli("ls")
        assert "active" in out
        assert "dropped" not in out

    def test_ls_show_canceled(self, cli):
        cli("add", "active")
        cli("add", "dropped")
        cli("cancel", "2")
        _, out, _ = cli("--show-canceled", "ls")
        assert "dropped" in out

    def test_ls_all_includes_canceled(self, cli):
        # --all still includes DONE + CANCELED (semantics unchanged)
        cli("add", "a")
        cli("add", "b")
        cli("done", "1")
        cli("cancel", "2")
        _, out, _ = cli("ls", "--all")
        # --all includes DONE and CANCELED
        assert "#1" in out and "#2" in out

    def test_projects_default_hides_canceled(self, cli):
        cli("add", "active proj", "--para", "project")
        cli("add", "obsolete proj", "--para", "project")
        cli("cancel", "2")
        _, out, _ = cli("projects")
        assert "active proj" in out
        assert "obsolete proj" not in out

    def test_find_default_hides_canceled(self, cli):
        cli("add", "find-target alpha")
        cli("add", "find-target beta")
        cli("cancel", "2")
        _, out, _ = cli("find", "find-target")
        assert "alpha" in out
        assert "beta" not in out

    def test_find_show_canceled(self, cli):
        cli("add", "find-target alpha")
        cli("add", "find-target beta")
        cli("cancel", "2")
        _, out, _ = cli("--show-canceled", "find", "find-target")
        assert "alpha" in out
        assert "beta" in out

    def test_day_hides_canceled_task_log(self, cli):
        cli("add", "active")
        cli("add", "dropped")
        cli("log", "1", "did today")
        cli("log", "2", "today's obsolete log")
        cli("cancel", "2")
        _, out, _ = cli("day")
        assert "did today" in out
        assert "today's obsolete log" not in out

    def test_summary_hides_canceled(self, cli):
        cli("add", "active")
        cli("add", "dropped")
        cli("done", "1")
        cli("cancel", "2")
        _, out, _ = cli("summary", "--since", "1970-01-01")
        assert "active" in out
        assert "dropped" not in out
