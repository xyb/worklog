"""The node entity group: `wl node add/ls/show/edit/rm/reparent`, the metric-
style primitive CRUD. The top-level add/ls/show are shortcuts onto the same handlers."""
import sqlite3
import pytest


class TestNodeGroupShortcutEquivalence:
    def test_node_add_equals_add(self, cli, tmp_db):
        cli("node", "add", "via group", "-k", "task")
        cli("add", "via shortcut", "-k", "task")
        con = tmp_db.db_connect()
        titles = {r["title"] for r in con.execute("SELECT title FROM node WHERE deleted_at IS NULL")}
        assert {"via group", "via shortcut"} <= titles

    def test_node_ls_and_show(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("node", "ls")
        assert "t1" in out
        _, out2, _ = cli("node", "show", "1")
        assert "t1" in out2

    def test_node_group_needs_subcommand(self, cli):
        _, _, err = cli("node")
        assert "usage: wl node" in err


class TestNodeEdit:
    def test_edit_fields(self, cli, tmp_db):
        cli("add", "old", "-k", "task")
        cli("node", "edit", "1", "--title", "new", "-p", "A", "--body", "details")
        con = tmp_db.db_connect()
        r = con.execute("SELECT title, priority, body FROM node WHERE id=1").fetchone()
        assert r["title"] == "new" and r["priority"] == "A" and r["body"] == "details"

    def test_edit_scheduled_and_clear(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("node", "edit", "1", "--scheduled", "2026-06")
        con = tmp_db.db_connect()
        assert con.execute("SELECT scheduled_date FROM node WHERE id=1").fetchone()[0] == "2026-06"
        cli("node", "edit", "1", "--scheduled", "")  # clear
        con = tmp_db.db_connect()
        assert con.execute("SELECT scheduled_date FROM node WHERE id=1").fetchone()[0] is None

    def test_edit_nothing_errors(self, cli):
        cli("add", "t", "-k", "task")
        code, _, err = cli("node", "edit", "1")
        assert code != 0 and "nothing to edit" in err

    def test_edit_missing_node(self, cli):
        code, _, err = cli("node", "edit", "99", "--title", "x")
        assert code != 0 and "not found" in err

    def test_edit_kind_body_deadline(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("node", "edit", "1", "-k", "habit", "--body", "b", "--deadline", "2026-06-30")
        con = tmp_db.db_connect()
        r = con.execute("SELECT kind, body, deadline_date FROM node WHERE id=1").fetchone()
        assert (r["kind"], r["body"], r["deadline_date"]) == ("habit", "b", "2026-06-30")
        cli("node", "edit", "1", "--deadline", "")  # clear deadline
        con = tmp_db.db_connect()
        assert con.execute("SELECT deadline_date FROM node WHERE id=1").fetchone()[0] is None


class TestNodeReparent:
    def test_reparent_moves(self, cli, tmp_db):
        cli("add", "parent", "-k", "project")   # 1
        cli("add", "child", "-k", "task")        # 2 (top-level)
        cli("node", "reparent", "2", "1")
        con = tmp_db.db_connect()
        assert con.execute("SELECT parent_id FROM node WHERE id=2").fetchone()[0] == 1

    def test_reparent_to_none_detaches(self, cli, tmp_db):
        cli("add", "p", "-k", "project")            # 1
        cli("add", "c", "-k", "task", "--parent", "1")  # 2
        cli("node", "reparent", "2", "none")
        con = tmp_db.db_connect()
        assert con.execute("SELECT parent_id FROM node WHERE id=2").fetchone()[0] is None

    def test_reparent_cycle_refused(self, cli):
        cli("add", "a", "-k", "project")            # 1
        cli("add", "b", "-k", "task", "--parent", "1")  # 2 (child of 1)
        code, _, err = cli("node", "reparent", "1", "2")  # make 1 child of its own child
        assert code != 0 and "cycle" in err

    def test_reparent_self_refused(self, cli):
        cli("add", "a", "-k", "task")
        code, _, err = cli("node", "reparent", "1", "1")
        assert code != 0 and "own parent" in err

    def test_reparent_invalid_parent_string(self, cli):
        cli("add", "a", "-k", "task")
        code, _, err = cli("node", "reparent", "1", "abc")  # not an id, not none/root/0
        assert code != 0 and "parent must be a node id" in err

    def test_reparent_parent_not_found(self, cli):
        cli("add", "a", "-k", "task")
        code, _, err = cli("node", "reparent", "1", "9999")
        assert code != 0 and "not found" in err


class TestNodeRm:
    def test_rm_soft_deletes_subtree(self, cli, tmp_db):
        cli("add", "p", "-k", "project")            # 1
        cli("add", "c", "-k", "task", "--parent", "1")  # 2
        cli("node", "rm", "1")
        con = tmp_db.db_connect()
        live = con.execute("SELECT COUNT(*) FROM node WHERE deleted_at IS NULL").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM node").fetchone()[0]
        assert live == 0 and total == 2  # both tombstoned (subtree), not removed
        _, out, _ = cli("ls")
        assert "p" not in out and "c" not in out


class TestNodeReviewFixes:
    """Cross-model review (Kimi K2.5) findings."""

    def test_edit_empty_title_rejected(self, cli):
        cli("add", "t", "-k", "task")
        code, _, err = cli("node", "edit", "1", "--title", "  ")
        assert code != 0 and "title cannot be empty" in err

    def test_rm_cascades_through_tombstoned_intermediate(self, cli, tmp_db):
        # A → B → C ; tombstone B directly (leaving C live, FK off), then rm A:
        # the full structural subtree (incl. C under tombstoned B) must be tombstoned.
        cli("add", "A", "-k", "project")                  # 1
        cli("add", "B", "-k", "project", "--parent", "1")  # 2
        cli("add", "C", "-k", "task", "--parent", "2")     # 3
        con = tmp_db.db_connect()
        con.execute("UPDATE node SET deleted_at='2026-06-06 00:00:00' WHERE id=2")  # tombstone B only
        con.commit()
        cli("node", "rm", "1")
        con = tmp_db.db_connect()
        live_c = con.execute("SELECT COUNT(*) FROM node WHERE id=3 AND deleted_at IS NULL").fetchone()[0]
        assert live_c == 0  # C no longer orphaned-but-live

    def test_reparent_detach_with_zero(self, cli, tmp_db):
        cli("add", "p", "-k", "project")
        cli("add", "c", "-k", "task", "--parent", "1")
        cli("node", "reparent", "2", "0")  # 0 = detach
        con = tmp_db.db_connect()
        assert con.execute("SELECT parent_id FROM node WHERE id=2").fetchone()[0] is None


class TestNodeGuards:
    """Missing-node / bad-input guards on the node entity group."""
    def test_reparent_missing_node_errors(self, cli):
        code, _, err = cli("node", "reparent", "99", "1")   # missing CHILD (vs missing-parent elsewhere)
        assert code != 0 and "not found" in err

    def test_rm_missing_node_errors(self, cli):
        code, _, err = cli("node", "rm", "99")
        assert code != 0 and "not found" in err

    def test_edit_bad_scheduled_errors(self, cli):
        cli("add", "task one", "-k", "task")
        code, _, err = cli("node", "edit", "1", "--scheduled", "not-a-date")
        assert code != 0
