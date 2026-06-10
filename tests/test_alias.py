"""`wl alias add/ls/rm` — manage command aliases in aliases.ini. XDG_CONFIG_HOME is
redirected to a tmp dir so tests never touch the real ~/.config/worklog/aliases.ini.
(Aliases take effect on the NEXT invocation — wired into the parser at startup — so these
verify the file round-trip + validation rather than in-process re-dispatch.)"""
import configparser
import pytest


def _alias_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg" / "worklog" / "aliases.ini"


def _read(path):
    cfg = configparser.ConfigParser(); cfg.optionxform = str
    cfg.read(path, encoding="utf-8")
    return dict(cfg["aliases"]) if "aliases" in cfg else {}


class TestAlias:
    def test_add_writes_file(self, cli, monkeypatch, tmp_path):
        f = _alias_file(monkeypatch, tmp_path)
        cli("alias", "add", "d", "day")
        assert _read(f) == {"d": "day"}

    def test_add_then_ls(self, cli, monkeypatch, tmp_path):
        _alias_file(monkeypatch, tmp_path)
        cli("alias", "add", "c", "checkin")
        _, out, _ = cli("alias", "ls")
        assert "c" in out and "checkin" in out

    def test_add_multi_token_target(self, cli, monkeypatch, tmp_path):
        """an alias may carry args: `wl alias add w "day -t work"` stores the full target."""
        f = _alias_file(monkeypatch, tmp_path)
        cli("alias", "add", "w", "day -t work")
        assert _read(f) == {"w": "day -t work"}

    def test_add_multi_token_rejects_bad_first_word(self, cli, monkeypatch, tmp_path):
        f = _alias_file(monkeypatch, tmp_path)
        _, _, err = cli("alias", "add", "x", "notacommand -t work")
        assert "unknown command" in err
        assert not f.exists() or _read(f) == {}

    def test_add_rejects_unknown_target(self, cli, monkeypatch, tmp_path):
        f = _alias_file(monkeypatch, tmp_path)
        _, _, err = cli("alias", "add", "x", "notacommand")
        assert "unknown command" in err
        assert not f.exists() or _read(f) == {}

    def test_add_rejects_shadowing_command(self, cli, monkeypatch, tmp_path):
        f = _alias_file(monkeypatch, tmp_path)
        # name 'day' is itself a command; target 'log' is valid → shadow guard must fire
        _, _, err = cli("alias", "add", "day", "log")
        assert "shadow" in err or "already a wl command" in err
        assert not f.exists() or _read(f) == {}

    def test_rm(self, cli, monkeypatch, tmp_path):
        f = _alias_file(monkeypatch, tmp_path)
        cli("alias", "add", "d", "day")
        cli("alias", "rm", "d")
        assert _read(f) == {}

    def test_rm_absent(self, cli, monkeypatch, tmp_path):
        _alias_file(monkeypatch, tmp_path)
        _, out, _ = cli("alias", "rm", "nope")
        assert "no alias" in out

    def test_ls_empty(self, cli, monkeypatch, tmp_path):
        _alias_file(monkeypatch, tmp_path)
        _, out, _ = cli("alias", "ls")
        assert "no aliases" in out

    def test_bare_alias_usage(self, cli, monkeypatch, tmp_path):
        _alias_file(monkeypatch, tmp_path)
        _, _, err = cli("alias")
        assert "usage: wl alias" in err
