"""Tests for ls (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestLs:
    def _seed(self, cli):
        cli("add", "task1", "-k", "task", "-p", "A", "-t", "work,P0")
        cli("add", "task2", "-k", "task", "-p", "B", "-t", "personal")
        cli("add", "proj1", "-k", "project", "-p", "A", "-t", "work")
        cli("add", "doneTask", "-k", "task", "-t", "work")
        cli("done", "4")

    def test_ls_default_excludes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls")
        assert "task1" in out
        assert "task2" in out
        assert "proj1" in out
        assert "doneTask" not in out

    def test_ls_all_includes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--all")
        assert "doneTask" in out

    def test_ls_filter_kind(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--kind", "project")
        assert "proj1" in out
        assert "task1" not in out

    def test_ls_filter_tag(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--tag", "personal")
        assert "task2" in out
        assert "task1" not in out

    def test_ls_filter_multi_tag_and(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--tag", "work,P0")
        assert "task1" in out
        assert "proj1" not in out  # has work but no P0
        assert "task2" not in out

    def test_ls_filter_parent(self, cli, tmp_db):
        cli("add", "parent")
        cli("add", "child1", "--parent", "1")
        cli("add", "child2", "--parent", "1")
        cli("add", "orphan")
        code, out, _ = cli("ls", "--parent", "1")
        assert "child1" in out
        assert "child2" in out
        assert "orphan" not in out

    def test_ls_empty_db(self, cli):
        code, out, _ = cli("ls")
        assert "(no nodes)" in out


# ─── tree ───


class TestLsTagFilter:
    def test_ls_multi_tag_and(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work,foo")
        cli("add", "t2", "-k", "task", "-t", "work")
        _, out, _ = cli("ls", "--tag", "work,foo")
        # AND filter: only t1 has both work + foo
        assert "t1" in out
        assert "t2" not in out

    def test_ls_all_includes_done(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        _, out, _ = cli("ls", "--all")
        assert "t1" in out


class TestLsAdvanced:
    """wl ls multi-dimension sort / filter / default limit (modeled on shell ls -t/-S/-r)"""

    def test_default_limit_20(self, cli):
        for i in range(25):
            cli("add", f"t{i}", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task")
        # default limit 20
        assert "showing 20/25" in out
        # t0..t19 present (priority+id ascending)
        assert "t0" in out and "t19" in out
        assert "t20" not in out and "t24" not in out

    def test_all_lifts_default_limit(self, cli):
        for i in range(25):
            cli("add", f"t{i}", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--all")
        assert "t24" in out

    def test_limit_0_lifts(self, cli):
        for i in range(25):
            cli("add", f"t{i}", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--limit", "0")
        assert "t24" in out

    def test_sort_created_desc(self, cli):
        """--sort created: newest first (like shell ls -t)"""
        cli("add", "first", "-k", "task")
        cli("add", "second", "-k", "task")
        cli("add", "third", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--sort", "created")
        # third should be first
        idx_first = out.find("first")
        idx_third = out.find("third")
        assert idx_third < idx_first

    def test_sort_title(self, cli):
        cli("add", "zebra", "-k", "task")
        cli("add", "apple", "-k", "task")
        cli("add", "mango", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--sort", "title")
        idx_a = out.find("apple")
        idx_z = out.find("zebra")
        assert idx_a < idx_z

    def test_reverse_flag(self, cli):
        cli("add", "first", "-k", "task")
        cli("add", "second", "-k", "task")
        _, out_normal, _ = cli("ls", "--kind", "task", "--sort", "id")
        _, out_rev, _ = cli("ls", "--kind", "task", "--sort", "id", "--reverse")
        # forward: first first; reverse: second first
        assert out_normal.find("first") < out_normal.find("second")
        assert out_rev.find("second") < out_rev.find("first")

    def test_unscheduled_filter(self, cli):
        from datetime import date
        cli("add", "planned-alpha", "-k", "task")
        cli("add", "open-beta", "-k", "task")
        cli("sched", "1", date.today().isoformat())
        _, out, _ = cli("ls", "--kind", "task", "--unscheduled")
        assert "open-beta" in out
        assert "planned-alpha" not in out

    def test_recent_n_days(self, cli):
        """--recent N: changed within the last N days (including created)"""
        cli("add", "new-task", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--recent", "1")
        assert "new-task" in out

    def test_ids_direct(self, cli):
        """--ids 1 3 5: like shell ls file1 file3 — bypass filters, list directly"""
        cli("add", "a", "-k", "task")
        cli("add", "b", "-k", "task")
        cli("add", "c", "-k", "task")
        _, out, _ = cli("ls", "--ids", "1", "3")
        assert "a" in out
        assert "c" in out
        assert "b" not in out

    def test_ids_unknown_skipped(self, cli):
        cli("add", "a", "-k", "task")
        _, out, _ = cli("ls", "--ids", "999")
        assert "no nodes matched" in out

    def test_short_r_flag_for_reverse(self, cli):
        cli("add", "first", "-k", "task")
        cli("add", "second", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--sort", "id", "-r")
        assert out.find("second") < out.find("first")

    def test_bare_ls_no_hint_pollution(self, cli):
        """bare ls does not pollute stdout (hints moved to --help epilog)"""
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("ls")
        # should list only t1, no "(bare ls...)" hint
        assert "t1" in out
        assert "bare ls" not in out
        assert "narrowing" not in out


class TestLsSortUpdated:
    """ls --sort updated paths: uses latest log timestamp; --reverse flips order."""

    def test_sort_updated(self, cli, tmp_db):
        cli("add", "old-task", "-k", "task")
        cli("add", "new-task", "-k", "task")
        cli("log", "1", "old entry")
        cli("log", "2", "later entry")
        _, out, _ = cli("ls", "--sort", "updated", "--limit", "5")
        # new-task (id=2) logged later → appears first under DESC
        idx_new = out.find("new-task")
        idx_old = out.find("old-task")
        assert 0 <= idx_new < idx_old

    def test_sort_updated_reverse(self, cli, tmp_db):
        cli("add", "old-task", "-k", "task")
        cli("add", "new-task", "-k", "task")
        cli("log", "1", "old entry")
        cli("log", "2", "later entry")
        _, out, _ = cli("ls", "--sort", "updated", "--reverse", "--limit", "5")
        # ASC after reverse → old-task first
        idx_new = out.find("new-task")
        idx_old = out.find("old-task")
        assert 0 <= idx_old < idx_new
