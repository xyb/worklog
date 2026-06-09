"""Soft-delete (tombstone) cascade — the app-level replacement for the old FK
`ON DELETE CASCADE`. FK enforcement is now off; a node "delete" is a
soft-delete that tombstones the node and its spoke rows, reversible and hidden
from reads, instead of physically removing anything."""
import sqlite3
import pytest

from worklog import queries as q


class TestSoftDeleteCascade:
    def test_soft_delete_node_tombstones_node_and_spokes(self, cli, tmp_db):
        cli("add", "doomed", "-t", "a,b")
        cli("log", "1", "log entry")
        cli("set", "1", "k", "v")
        cli("link", "1", "doc")
        con = tmp_db.db_connect()
        q.soft_delete_node(con, 1)
        con.commit()
        for table, col in (("node", "id"), ("tag", "node_id"), ("log", "node_id"),
                           ("prop", "node_id"), ("link", "node_id")):
            live = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col}=1 AND deleted_at IS NULL").fetchone()[0]
            total = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=1").fetchone()[0]
            assert live == 0, f"{table} still has live rows after soft-delete"
            assert total >= 1, f"{table} was physically deleted (should be a tombstone)"

    def test_soft_deleted_node_hidden_from_reads(self, cli):
        import tempfile, os
        cli("add", "keep")
        cli("add", "doomed")
        # `wl apply` with a `- #id` line soft-deletes node 2
        f = tempfile.NamedTemporaryFile("w", suffix=".wld", delete=False, encoding="utf-8")
        f.write("- #2 doomed\n")
        f.close()
        cli("apply", f.name)
        os.unlink(f.name)
        _, out, _ = cli("ls")
        assert "keep" in out and "doomed" not in out  # gone from the list
        _, out, err = cli("show", "2")
        assert "not found" in (out + err)             # gone from show

    def test_soft_delete_is_reversible(self, cli, tmp_db):
        cli("add", "doomed")
        con = tmp_db.db_connect()
        q.soft_delete_node(con, 1)
        con.commit()
        assert con.execute("SELECT deleted_at FROM node WHERE id=1").fetchone()["deleted_at"] is not None
        # clearing the tombstone restores it
        con.execute("UPDATE node SET deleted_at=NULL WHERE id=1")
        con.commit()
        _, out, _ = cli("show", "1")
        assert "doomed" in out

    def test_soft_delete_log_tombstones_its_metrics(self, cli, tmp_db):
        cli("add", "h", "-k", "habit")
        cli("metric", "add", "1", "glucose", "5.4")  # creates a carrier log + metric
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT log_id FROM metric WHERE id=1").fetchone()["log_id"]
        q.soft_delete_log(con, log_id)
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric WHERE deleted_at IS NULL").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM log WHERE id=? AND deleted_at IS NULL", (log_id,)).fetchone()[0] == 0


class TestTombstoneHiddenEverywhere:
    """A soft-deleted node must not leak into any read command (the raw-SQL read paths
    all filter deleted_at IS NULL). Covers the full read surface as a regression net."""

    def _seed_and_delete(self, cli):
        import tempfile, os
        cli("add", "2026", "-k", "year")                                      # 1
        cli("add", "keepproj", "-k", "project", "-t", "work", "--parent", "1")  # 2
        cli("add", "keepme", "-k", "task", "-t", "work", "--parent", "2",
            "--log", "kept", "--sched", "2026-06-06")                          # 3
        cli("add", "doomed", "-k", "task", "-t", "work", "--parent", "2",
            "--log", "gone", "--sched", "2026-06-06")                          # 4
        f = tempfile.NamedTemporaryFile("w", suffix=".wld", delete=False, encoding="utf-8")
        f.write("- #4 doomed\n")
        f.close()
        cli("apply", f.name)
        os.unlink(f.name)

    def test_hidden_from_all_reads(self, cli):
        self._seed_and_delete(cli)
        checks = [
            ("ls",), ("ls", "--all"), ("tree", "--depth", "9"), ("tree", "--by", "project"),
            ("tree", "-t", "work"), ("day", "2026-06-06"), ("logs", "--since", "2026-06-01"),
            ("agenda", "2026-06-01", "2026-06-30"), ("projects",), ("summary", "--since", "2026-06-01"),
        ]
        for cmd in checks:
            _, out, _ = cli(*cmd)
            assert "doomed" not in out and "gone" not in out, f"leaked in: wl {' '.join(cmd)}\n{out}"
        # find echoes the query in its "(no matches…)" message, so check it gives no hit
        for term in ("doomed", "gone"):
            _, out, _ = cli("find", term)
            assert "no matches" in out, f"find leaked: {out}"
        # sanity: the filter isn't nuking everything — the kept sibling still shows
        _, out, _ = cli("ls")
        assert "keepme" in out


class TestTombstoneReAdd:
    """Cross-model review (GPT-5.5) findings: a removed natural-key row (tag/link/prop/
    date_meta) must be re-addable — the tombstone is revived, not swallowed by OR IGNORE
    or hard-replaced by OR REPLACE."""

    def test_readd_removed_tag(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-t", "work")
        cli("tag", "1", "-work")          # soft-delete the tag
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM tag WHERE node_id=1 AND deleted_at IS NULL").fetchone()[0] == 0
        cli("tag", "1", "+work")          # re-add — must come back (revive the tombstone)
        _, out, _ = cli("show", "1")
        assert "work" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM tag WHERE node_id=1 AND tag='work' AND deleted_at IS NULL").fetchone()[0] == 1

    def test_readd_removed_link(self, cli, tmp_db):
        cli("add", "t")
        cli("link", "1", "Doc A")
        cli("unlink", "1", "Doc A")        # soft-delete the link
        cli("link", "1", "Doc A")          # re-add
        con = tmp_db.db_connect()
        assert con.execute(
            "SELECT COUNT(*) FROM link WHERE node_id=1 AND vault_doc='Doc A' AND deleted_at IS NULL").fetchone()[0] == 1

    def test_reset_dateinfo_after_clear_revives(self, cli, tmp_db):
        cli("dateinfo", "2026-06-01", "holiday")
        con = tmp_db.db_connect()
        # tombstone it directly (simulating a removal)
        con.execute("UPDATE date_meta SET deleted_at = '2026-06-01 00:00:00' WHERE date='2026-06-01'")
        con.commit()
        cli("dateinfo", "2026-06-01", "vacation")  # re-set → revive + new label, not hard-replace
        con = tmp_db.db_connect()
        rows = con.execute("SELECT label, deleted_at FROM date_meta WHERE date='2026-06-01'").fetchall()
        assert len(rows) == 1 and rows[0]["label"] == "vacation" and rows[0]["deleted_at"] is None

    def test_find_tolerates_orphan_spoke(self, cli, tmp_db):
        # a live log whose node is tombstoned must not crash find (n is None) nor be returned
        cli("add", "doomed", "-k", "task")
        cli("log", "1", "uniqueneedle")
        con = tmp_db.db_connect()
        con.execute("UPDATE node SET deleted_at='2026-06-06 00:00:00' WHERE id=1")  # tombstone node, leave log live
        con.commit()
        code, out, _ = cli("find", "uniqueneedle")
        assert code == 0 and "no matches" in out  # tolerated, not surfaced

    def test_completion_sql_filters_tombstones(self, cli):
        _, out, _ = cli("print-completion", "fish")
        # the generated node + tag completion SQL must filter tombstones
        assert "deleted_at IS NULL" in out
