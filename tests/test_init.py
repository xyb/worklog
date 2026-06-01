"""Tests for init (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestInit:
    def test_init_creates_tables(self, cli, tmp_db):
        code, out, _ = cli("init")
        assert code == 0
        # tables present
        con = tmp_db.db_connect()
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables >= {"node", "tag", "log", "prop", "link"}
        # view present
        views = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        assert "v_node_path" in views

    def test_idempotent_init(self, cli):
        """init is idempotent"""
        assert cli("init")[0] == 0
        assert cli("init")[0] == 0
