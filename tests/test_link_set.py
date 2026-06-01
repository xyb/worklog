"""Tests for link_set (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestLinkAndSet:
    def test_link_to_vault_doc(self, cli, tmp_db):
        cli("add", "task")
        cli("link", "1", "Dev tooling")
        cli("link", "1", "Q2 metric rollup")
        con = tmp_db.db_connect()
        docs = {r[0] for r in con.execute("SELECT vault_doc FROM link WHERE node_id=1")}
        assert docs == {"Dev tooling", "Q2 metric rollup"}

    def test_link_idempotent(self, cli, tmp_db):
        cli("add", "task")
        cli("link", "1", "doc")
        cli("link", "1", "doc")
        con = tmp_db.db_connect()
        count = con.execute("SELECT COUNT(*) FROM link WHERE node_id=1").fetchone()[0]
        assert count == 1

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
