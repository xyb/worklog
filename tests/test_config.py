"""Tests for config (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestConfig:
    """wl config: read-only printer for paths/env/runtime; side-effect free."""

    def test_config_runs(self, cli):
        code, out, _ = cli("config")
        assert code == 0
        assert "worklog" in out

    def test_config_shows_db_path_and_aliases(self, cli):
        _, out, _ = cli("config")
        assert "database" in out and ".db" in out
        assert "aliases" in out and "aliases.ini" in out

    def test_config_shows_xdg_sections(self, cli):
        _, out, _ = cli("config")
        assert "XDG_DATA_HOME" in out
        assert "XDG_CONFIG_HOME" in out

    def test_config_marks_wl_db_source(self, tmp_path, monkeypatch):
        """When $WORKLOG_DB is set, config reports it as the DB source."""
        db = tmp_path / "test.db"
        monkeypatch.setenv("WORKLOG_DB", str(db))
        import importlib; from worklog import cli as wl
        importlib.reload(wl)
        # cmd_config writes to out() which prints via stdout
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wl.cmd_config(type("A", (), {})(), None)
        text = buf.getvalue()
        assert "$WORKLOG_DB" in text
        assert str(db) in text

    def test_config_does_not_create_db(self, tmp_path, monkeypatch):
        """`wl config` must not create the DB file (side-effect free)."""
        db = tmp_path / "fresh.db"
        monkeypatch.setenv("WORKLOG_DB", str(db))
        import importlib; from worklog import cli as wl
        importlib.reload(wl)
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            wl.cmd_config(type("A", (), {})(), None)
        assert not db.exists()
