"""Tests for focus (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestFocus:
    def _seed(self, cli):
        cli("add", "2026-05", "-k", "month")                     # 1
        cli("add", "data-viz", "-k", "project", "-t", "gaming", "--parent", "1")  # 2
        cli("add", "login fix", "-k", "task", "-t", "gaming,work,P0", "--parent", "1")     # 3
        cli("add", "decision meeting", "-k", "meetlog", "-t", "gaming,work,P0,strategy", "--parent", "1")  # 4
        cli("add", "digest system", "-k", "task", "-t", "gaming,followup", "--parent", "4")    # 5 (meeting subtask)
        cli("add", "unrelated task", "-k", "task", "-t", "biz_agg,work", "--parent", "1")       # 6

    def test_focus_shows_upstream_self_downstream(self, cli):
        self._seed(cli)
        code, out, _ = cli("focus", "4")
        assert code == 0
        assert "upstream" in out and "2026-05" in out
        assert "focus" in out and "decision meeting" in out
        assert "downstream" in out and "digest system" in out

    def test_focus_related_excludes_generic_tags(self, cli):
        """#4 tag = gaming/work/P0/strategy → related matches only on gaming, not flooded by work/P0"""
        self._seed(cli)
        code, out, _ = cli("focus", "4", "--related")
        # gaming-related #2 #3 should appear
        assert "data-viz" in out or "login fix" in out
        # unrelated task #6 (biz_agg/work) should not mix in — it only shares work(generic), not gaming
        rel_section = out.split("related")[-1] if "related" in out else ""
        assert "unrelated task" not in rel_section

    def test_focus_related_only_generic_tags(self, cli):
        cli("add", "isolated", "-k", "task", "-t", "work,P0,planned")  # all generic tags
        code, out, _ = cli("focus", "1", "--related")
        assert "only generic-dimension tags" in out

    def test_focus_nonexistent_fails(self, cli):
        code, _, err = cli("focus", "99")
        assert code != 0


class TestFocusDayNode:
    """focus on a day node should expand that day's activity (logs/sched), not just
    parent_id children — same semantics as `wl tree` / `wl day` for day nodes."""

    def _seed(self, cli, date="2026-05-28"):
        cli("add", "2026-05", "-k", "month")                                      # 1
        cli("add", date, "-k", "day", "--parent", "1")                            # 2 (day node)
        cli("add", "project X", "-k", "project", "-t", "work", "--parent", "1")   # 3
        cli("add", "task with log today", "-k", "task", "-t", "work", "--parent", "3")  # 4
        cli("add", "today's meetlog", "-k", "meetlog", "-t", "work", "--parent", "3")   # 5
        # neither task's parent is the day node; they belong to the day only via log date
        cli("log", "4", "did work today", "--date", date)
        cli("log", "5", "had a meeting today", "--date", date)

    def test_focus_day_expands_activity(self, cli):
        """focus on a day node = wl day for that date: every node with activity that
        day shows up, regardless of its parent_id (was: only parent_id children)."""
        self._seed(cli)
        code, out, _ = cli("focus", "2")
        assert code == 0
        assert "task with log today" in out, "focus on a day node dropped the day's log activity"
        assert "today's meetlog" in out


class TestAncestorsDescendants:
    def _seed(self, cli):
        cli("add", "year", "-k", "year")                  # 1
        cli("add", "month", "-k", "month", "--parent", "1")  # 2
        cli("add", "task", "-k", "task", "--parent", "2")  # 3
        cli("add", "children", "-k", "task", "--parent", "3")  # 4

    def test_ancestors_chain(self, cli):
        self._seed(cli)
        code, out, _ = cli("ancestors", "3")
        assert "year" in out and "month" in out and "task" in out
        assert "▶" in out  # self has an arrow marker

    def test_descendants_subtree(self, cli):
        self._seed(cli)
        code, out, _ = cli("descendants", "2")
        assert "month" in out and "task" in out and "children" in out

    def test_ancestors_nonexistent_fails(self, cli):
        code, _, err = cli("ancestors", "99")
        assert code != 0


class TestAncestorsChainBreak:
    def test_dangling_parent_id_breaks_loop(self, cli):
        """parent_id points to missing node → mid-chain break (with FK off)"""
        cli("add", "p1", "-k", "task")
        cli("add", "c1", "-k", "task", "--parent", "1")
        from worklog import cli as wl
        con = wl.db_connect()
        # temporarily disable FK to perform the edit, then re-enable
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("UPDATE node SET parent_id = 999 WHERE id = 2")
        con.commit()
        chain = wl._ancestors_chain(con, 2)
        # start at c1 → parent=999 missing → break; chain = [c1]
        assert len(chain) == 1
        assert chain[0]["title"] == "c1"


class TestDescendantsMissing:
    def test_descendants_node_not_found(self, cli):
        code, _, _ = cli("descendants", "999")
        assert code != 0


class TestFocusRelatedEmpty:
    """cmd_focus on a node with semantic tags but no peers sharing them: (no other nodes)."""

    def test_focus_related_no_peers(self, cli):
        cli("add", "lonely-task", "-k", "task", "-t", "unique-topic-xyz")
        _, out, _ = cli("focus", "1", "--related")
        assert "no other nodes" in out
