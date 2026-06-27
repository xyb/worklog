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


class TestConfigInit:
    def test_init_creates_template(self, cli, tmp_db):
        from worklog.xdg import _resolve_config_path
        p = _resolve_config_path()
        assert not p.exists()
        code, out, _ = cli("config", "init")
        assert code == 0 and p.exists()
        text = p.read_text(encoding="utf-8")
        assert "[embedding]" in text and "[synonyms]" in text   # documented template
        assert "wrote" in out.lower()

    def test_init_does_not_overwrite(self, cli, tmp_db):
        from worklog.xdg import _resolve_config_path
        p = _resolve_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[embedding]\nmodel = mine\n", encoding="utf-8")
        code, out, _ = cli("config", "init")
        assert code == 0
        assert "model = mine" in p.read_text(encoding="utf-8")   # untouched
        assert "already exists" in out.lower()


class TestConfigSources:
    """The DB-source label + vector-store backend line in `wl config`."""

    def test_marks_db_flag_source(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKLOG_DB", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        import importlib, io, contextlib, types
        from worklog import cli as wl
        importlib.reload(wl)
        args = types.SimpleNamespace(db=str(tmp_path / "x.db"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wl.cmd_config(args, None)
        assert "--db flag" in buf.getvalue()

    def test_marks_xdg_default_source(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKLOG_DB", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        import importlib, io, contextlib, types
        from worklog import cli as wl
        importlib.reload(wl)
        args = types.SimpleNamespace()   # neither --db flag nor $WORKLOG_DB
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wl.cmd_config(args, None)
        assert "XDG default" in buf.getvalue()

    def test_shows_lancedb_backend_when_present(self, cli):
        pytest.importorskip("lancedb")
        _, out, _ = cli("config")
        assert "LanceDB" in out

    def test_shows_sqlite_fallback_without_lancedb(self, cli, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "lancedb", None)
        code, out, _ = cli("config")
        assert code == 0
        assert "SQLite (pure-Python fallback)" in out


class TestAutoReindexEnabled:
    """`auto_reindex_enabled()`: env var wins, else config.ini `[index] auto_reindex`, else default ON."""

    def _cfg(self, tmp_path, monkeypatch, body=None):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        if body is not None:
            d = tmp_path / "worklog"
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.ini").write_text(body, encoding="utf-8")
        from worklog import config
        return config

    def test_default_on_when_no_env_no_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKLOG_AUTO_REINDEX", raising=False)
        cfg = self._cfg(tmp_path, monkeypatch)
        assert cfg.auto_reindex_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "  FALSE  "])
    def test_env_falsy_disables(self, tmp_path, monkeypatch, val):
        monkeypatch.setenv("WORKLOG_AUTO_REINDEX", val)
        cfg = self._cfg(tmp_path, monkeypatch)
        assert cfg.auto_reindex_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "anything"])
    def test_env_truthy_enables(self, tmp_path, monkeypatch, val):
        monkeypatch.setenv("WORKLOG_AUTO_REINDEX", val)
        cfg = self._cfg(tmp_path, monkeypatch)
        assert cfg.auto_reindex_enabled() is True

    def test_env_wins_over_config(self, tmp_path, monkeypatch):
        # config says false, env says on → env wins
        monkeypatch.setenv("WORKLOG_AUTO_REINDEX", "1")
        cfg = self._cfg(tmp_path, monkeypatch, "[index]\nauto_reindex = false\n")
        assert cfg.auto_reindex_enabled() is True

    def test_config_false_disables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKLOG_AUTO_REINDEX", raising=False)
        cfg = self._cfg(tmp_path, monkeypatch, "[index]\nauto_reindex = false\n")
        assert cfg.auto_reindex_enabled() is False

    def test_config_true_enables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKLOG_AUTO_REINDEX", raising=False)
        cfg = self._cfg(tmp_path, monkeypatch, "[index]\nauto_reindex = true\n")
        assert cfg.auto_reindex_enabled() is True

    def test_config_without_index_section_defaults_on(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKLOG_AUTO_REINDEX", raising=False)
        cfg = self._cfg(tmp_path, monkeypatch, "[embedding]\nbackend = sqlite\n")
        assert cfg.auto_reindex_enabled() is True

    def test_malformed_config_defaults_on(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKLOG_AUTO_REINDEX", raising=False)
        cfg = self._cfg(tmp_path, monkeypatch, "this is not = valid ini [[[\n")
        assert cfg.auto_reindex_enabled() is True
