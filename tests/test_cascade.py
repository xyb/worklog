"""Tests for cascade (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestCascade:
    def test_parent_delete_sets_children_parent_null(self, cli, tmp_db):
        """delete parent -> child parent_id becomes NULL (ON DELETE SET NULL)"""
        cli("add", "parent")
        cli("add", "child", "--parent", "1")
        con = tmp_db.db_connect()
        con.execute("DELETE FROM node WHERE id=1")
        con.commit()
        row = con.execute("SELECT parent_id FROM node WHERE id=2").fetchone()
        assert row["parent_id"] is None

    def test_node_delete_cascades_tags_logs_props_links(self, cli, tmp_db):
        cli("add", "doomed", "-t", "a,b")
        cli("log", "1", "log entry")
        cli("set", "1", "k", "v")
        cli("link", "1", "doc")
        con = tmp_db.db_connect()
        con.execute("DELETE FROM node WHERE id=1")
        con.commit()
        for table in ("tag", "log", "prop", "link"):
            count = con.execute(f"SELECT COUNT(*) FROM {table} WHERE node_id=1").fetchone()[0]
            assert count == 0, f"{table} not cascaded"
