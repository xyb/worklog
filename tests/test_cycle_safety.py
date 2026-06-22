"""parent_id cycle safety. FK enforcement is OFF, so a bad parent_id graph
isn't DB-rejected. Two layers of defense: (1) the bulk update path refuses to create a
cycle (parity with cmd_node_reparent); (2) the ancestor/descendant walks are visited-set
guarded so a pre-existing/legacy cycle degrades gracefully instead of hanging the CLI."""
import sqlite3
import pytest

from worklog.graph import _collect_descendants, _ancestors_chain


def _make_cycle(tmp_db):
    """A → B → C → A, bypassing every guard via raw SQL (simulates legacy/corrupt data).
    Returns a live connection to the initialized DB."""
    tmp_db.ensure_db()
    con = tmp_db.db_connect()
    now = "2026-06-06 00:00:00"
    for i, parent in ((1, None), (2, 1), (3, 2)):
        con.execute("INSERT INTO node (id, title, parent_id, created_at) VALUES (?,?,?,?)",
                    (i, f"n{i}", parent, now))
    con.execute("UPDATE node SET parent_id = 3 WHERE id = 1")   # close the loop: 1's parent = 3
    con.commit()
    return con


class TestWalkerCycleSafety:
    def test_collect_descendants_terminates(self, tmp_db):
        con = _make_cycle(tmp_db)
        # must return a bounded set (every other node, each once), not loop forever
        assert set(_collect_descendants(con, 1)) == {2, 3}

    def test_ancestors_chain_terminates(self, tmp_db):
        con = _make_cycle(tmp_db)
        chain = _ancestors_chain(con, 3)            # walk up from 3 through the cycle
        ids = [r["id"] for r in chain]
        assert len(ids) == len(set(ids))            # no node repeats — the walk stopped
        assert 3 in ids


class TestBulkReparentCycleGuard:
    def test_apply_refuses_self_parent(self, cli, tmp_path):
        cli("add", "p", "--para", "project")
        f = tmp_path / "d.txt"; f.write_text("~ #1\n  parent 1\n", encoding="utf-8")
        _, _, err = cli("apply", str(f))
        assert "its own parent" in err or "cycle" in err

    def test_apply_refuses_descendant_parent(self, cli, tmp_path):
        cli("add", "p", "--para", "project")                 # #1
        cli("add", "c", "--parent", "1")   # #2 under #1
        f = tmp_path / "d.txt"; f.write_text("~ #1\n  parent 2\n", encoding="utf-8")
        _, _, err = cli("apply", str(f))                 # moving #1 under its descendant #2
        assert "cycle" in err or "descendant" in err

    def test_apply_allows_legit_reparent(self, cli, tmp_db, tmp_path):
        cli("add", "a", "--para", "project")                 # #1
        cli("add", "b", "--para", "project")                 # #2
        cli("add", "c", "--parent", "1")   # #3 under #1
        f = tmp_path / "d.txt"; f.write_text("~ #3\n  parent 2\n", encoding="utf-8")
        cli("apply", str(f))                             # move #3 under #2 — fine, no cycle
        con = tmp_db.db_connect()
        assert con.execute("SELECT parent_id FROM node WHERE id=3").fetchone()[0] == 2
