"""Tests for link_set (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestLinkAndSet:
    def test_link_to_vault_doc(self, cli, tmp_db):
        cli("add", "task")
        cli("link", "1", "Dev tooling")
        cli("link", "1", "Q2 metric rollup")
        con = tmp_db.db_connect()
        docs = {r[0] for r in con.execute("SELECT vault_doc FROM link WHERE node_id=1 AND deleted_at IS NULL")}
        assert docs == {"Dev tooling", "Q2 metric rollup"}

    def test_link_idempotent(self, cli, tmp_db):
        cli("add", "task")
        cli("link", "1", "doc")
        cli("link", "1", "doc")
        con = tmp_db.db_connect()
        count = con.execute("SELECT COUNT(*) FROM link WHERE node_id=1").fetchone()[0]
        assert count == 1

    def test_unlink_removes_one_keeps_others(self, cli, tmp_db):
        cli("add", "task")
        cli("link", "1", "Doc A")
        cli("link", "1", "Doc B")
        cli("unlink", "1", "Doc A")
        con = tmp_db.db_connect()
        docs = {r[0] for r in con.execute("SELECT vault_doc FROM link WHERE node_id=1 AND deleted_at IS NULL")}
        assert docs == {"Doc B"}  # only Doc A removed

    def test_unlink_absent_link_is_noop_notice(self, cli):
        cli("add", "task")
        cli("link", "1", "Doc A")
        _, out, _ = cli("unlink", "1", "Doc X")
        assert "had no link" in out

    def test_unlink_multiple_ids(self, cli, tmp_db):
        cli("add", "t1")
        cli("add", "t2")
        cli("link", "1", "shared")
        cli("link", "2", "shared")
        cli("unlink", "1", "2", "shared")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE vault_doc='shared' AND deleted_at IS NULL").fetchone()[0] == 0

    def test_set_property(self, cli, tmp_db):
        cli("add", "task")
        cli("set", "1", "owner", "xyb")
        cli("set", "1", "estimate", "30min")
        con = tmp_db.db_connect()
        props = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM prop WHERE node_id=1")}
        assert props == {"owner": "xyb", "estimate": "30min"}

    def test_set_overrides_existing(self, cli, tmp_db):
        cli("add", "task")
        cli("set", "1", "owner", "xyb")
        cli("set", "1", "owner", "yanbo")
        con = tmp_db.db_connect()
        row = con.execute("SELECT value FROM prop WHERE node_id=1 AND key='owner'").fetchone()
        assert row["value"] == "yanbo"


# ─── show ───


class TestCmdSetErrors:
    def test_set_empty_key(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("set", "1", " ", "v")
        assert code != 0

    def test_set_node_not_found(self, cli):
        code, _, _ = cli("set", "999", "k", "v")
        assert code != 0

    def test_set_tags_rejected_points_at_tag_cmd(self, cli):
        """`wl set <id> tags X` must be rejected — it used to silently create a shadow
        'tags' prop while the real tag field stayed unchanged."""
        cli("add", "t1", "-k", "task")
        for key in ("tags", "tag", "Tags"):
            code, out, err = cli("set", "1", key, "work")
            assert code != 0, f"set {key} should be rejected"
            assert "wl tag" in (out + err)
        # no shadow prop got created
        _, show, _ = cli("show", "1")
        assert "tags=" not in show


class TestCmdTag:
    """wl tag <id> +x -y edits the real tag field (tag table), not a shadow prop."""

    def test_tag_add_and_remove(self, cli, tmp_db):
        cli("add", "t1", "-k", "task", "-t", "work,planned")
        cli("tag", "1", "+urgent", "-planned")
        con = tmp_db.db_connect()
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1 AND deleted_at IS NULL")}
        assert tags == {"work", "urgent"}  # planned removed, urgent added, work kept

    def test_tag_bare_word_adds(self, cli, tmp_db):
        cli("add", "t1", "-k", "task")
        cli("tag", "1", "personal")  # bare = add
        con = tmp_db.db_connect()
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1 AND deleted_at IS NULL")}
        assert "personal" in tags

    def test_tag_no_ops_lists(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work,P0")
        _, out, _ = cli("tag", "1")
        assert "work" in out and "P0" in out

    def test_tag_add_is_idempotent(self, cli, tmp_db):
        cli("add", "t1", "-k", "task", "-t", "work")
        cli("tag", "1", "+work")  # already present
        con = tmp_db.db_connect()
        n = con.execute("SELECT COUNT(*) FROM tag WHERE node_id=1 AND tag='work'").fetchone()[0]
        assert n == 1  # INSERT OR IGNORE, no duplicate

    def test_tag_empty_ops_are_noops(self, cli, tmp_db):
        # a bare '+' / '-' has nothing after stripping the sign → skipped, not a blank tag row
        cli("add", "t1", "-k", "task", "-t", "work")
        cli("tag", "1", "+", "-")
        con = tmp_db.db_connect()
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1 AND deleted_at IS NULL")}
        assert tags == {"work"} and "" not in tags

    def test_tag_node_not_found(self, cli):
        code, _, _ = cli("tag", "999", "+x")
        assert code != 0


def _links(con, nid):
    return [r[0] for r in con.execute(
        "SELECT vault_doc FROM link WHERE node_id=? AND deleted_at IS NULL ORDER BY vault_doc", (nid,))]


class TestLinkWikilinkStrip:
    """an outer [[ ]] wrapper is stripped on input so [[X]] and X store identically
    (no [[[[X]]]] double-wrap), dedup via the natural key, and unlink matches either form."""

    def test_wrapper_stripped(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("link", "1", "[[My Doc]]")
        assert _links(tmp_db.db_connect(), 1) == ["My Doc"]

    def test_double_wrap_stripped(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("link", "1", "[[[[Deep]]]]")
        assert _links(tmp_db.db_connect(), 1) == ["Deep"]

    def test_plain_and_wrapped_dedup(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("link", "1", "My Doc")
        cli("link", "1", "[[My Doc]]")           # same after strip → no duplicate
        assert _links(tmp_db.db_connect(), 1) == ["My Doc"]

    def test_unlink_matches_wrapped(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("link", "1", "My Doc")
        cli("unlink", "1", "[[My Doc]]")          # wrapped form must match the stored plain
        assert _links(tmp_db.db_connect(), 1) == []

    def test_link_on_add_strips(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "--link", "[[On Add]]")
        assert _links(tmp_db.db_connect(), 1) == ["On Add"]

    def test_apply_add_link_strips(self, cli, tmp_db, tmp_path):
        cli("add", "t", "-k", "task")
        f = tmp_path / "d.txt"; f.write_text("~ #1\n  +link [[Via Apply]]\n", encoding="utf-8")
        cli("apply", str(f))
        assert _links(tmp_db.db_connect(), 1) == ["Via Apply"]
