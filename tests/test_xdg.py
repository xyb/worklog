"""Tests for xdg (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestXDGPaths:
    """Path resolution follows the XDG Base Directory spec."""

    def test_worklog_db_env_wins(self, tmp_path, monkeypatch):
        """$WORKLOG_DB env var has top priority"""
        target = tmp_path / "custom.db"
        monkeypatch.setenv("WORKLOG_DB", str(target))
        import importlib; from worklog import cli as wl
        importlib.reload(wl)
        assert wl.DB_PATH == target.resolve()

    def test_xdg_default_db_path(self, tmp_path, monkeypatch):
        """No $WORKLOG_DB → $XDG_DATA_HOME/worklog/worklog.db"""
        monkeypatch.delenv("WORKLOG_DB", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
        import importlib; from worklog import cli as wl
        importlib.reload(wl)
        assert wl.DB_PATH == (tmp_path / "xdg-data" / "worklog" / "worklog.db").resolve()

    def test_db_flag_wins_over_env(self, tmp_path, monkeypatch):
        """`--db PATH` flag has top priority over $WORKLOG_DB env."""
        monkeypatch.setenv("WORKLOG_DB", str(tmp_path / "from-env.db"))
        from worklog import cli as wl
        flag_path = tmp_path / "from-flag.db"
        args = type("A", (), {"db": str(flag_path)})()
        resolved = wl._resolve_db_path(args)
        assert resolved == flag_path.resolve()

    def test_db_flag_absent_falls_back_to_env(self, tmp_path, monkeypatch):
        """No --db flag (or flag is None) → fall back to $WORKLOG_DB."""
        env_path = tmp_path / "from-env.db"
        monkeypatch.setenv("WORKLOG_DB", str(env_path))
        from worklog import cli as wl
        args = type("A", (), {"db": None})()
        assert wl._resolve_db_path(args) == env_path.resolve()

    def test_xdg_config_home_aliases(self, tmp_path, monkeypatch):
        """$XDG_CONFIG_HOME/worklog/aliases.ini is the aliases path"""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-cfg"))
        import importlib; from worklog import cli as wl
        importlib.reload(wl)
        assert wl.ALIASES_PATH == tmp_path / "xdg-cfg" / "worklog" / "aliases.ini"
