"""Tests for aliases (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestUserAliasesIni:
    """~/.config/worklog/aliases.ini → argparse aliases (cross-shell: wl d = wl day)"""

    def _setup_aliases(self, tmp_path, monkeypatch, content):
        config_dir = tmp_path / ".config" / "worklog"
        config_dir.mkdir(parents=True)
        (config_dir / "aliases.ini").write_text(content)
        monkeypatch.setenv("HOME", str(tmp_path))
        # CI runners may preset XDG_CONFIG_HOME / XDG_DATA_HOME — clear them
        # so _xdg_config_home() / _xdg_data_home() fall back to $HOME (tmp_path).
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        import importlib; from worklog import cli as wl
        wl._USER_ALIASES = None  # force reload
        importlib.reload(wl)
        return wl

    def test_load_aliases_basic(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nc = checkin\n")
        loaded = wl._load_user_aliases()
        assert loaded == {"day": ["d"], "checkin": ["c"]}

    def test_load_aliases_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        import importlib; from worklog import cli as wl
        wl._USER_ALIASES = None
        importlib.reload(wl)
        assert wl._load_user_aliases() == {}

    def test_load_aliases_no_section(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch, "# empty file with no [aliases]\n")
        assert wl._load_user_aliases() == {}

    def test_load_aliases_multi_to_same_target(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nda = day\n")
        loaded = wl._load_user_aliases()
        assert sorted(loaded["day"]) == ["d", "da"]

    def test_parser_recognizes_alias(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nll = ls\n")
        parser = wl.build_parser()
        # alias should be in subparsers choices
        sa = next(a for a in parser._actions if isinstance(a, __import__("argparse")._SubParsersAction))
        assert "d" in sa.choices
        assert "ll" in sa.choices
        # main name and alias point to the same parser
        assert sa.choices["d"] is sa.choices["day"]
        assert sa.choices["ll"] is sa.choices["ls"]

    def test_alias_in_fish_completion(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nc = checkin\n")
        out = wl._generate_fish_completion(wl.build_parser())
        # main name + alias should both appear
        assert '"day"' in out and '"d"' in out
        assert '"checkin"' in out and '"c"' in out
        # alias entries marked with "= day"
        assert "(= day)" in out
        # subcommand argument condition should include main name + alias
        assert "__fish_seen_subcommand_from day d" in out

    def test_alias_in_bash_completion(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\n")
        out = wl._generate_bash_completion(wl.build_parser())
        # subcmds list includes d
        assert " d " in out or " d\"" in out
        # case pattern day|d)
        assert "day|d)" in out

    def test_alias_in_zsh_completion(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\n")
        out = wl._generate_zsh_completion(wl.build_parser())
        assert "'d:" in out
        # case includes day|d
        assert "day|d)" in out

    def test_no_aliases_clean_output(self, tmp_path, monkeypatch):
        """without an ini file, output should have no alias traces"""
        monkeypatch.setenv("HOME", str(tmp_path))
        import importlib; from worklog import cli as wl
        wl._USER_ALIASES = None
        importlib.reload(wl)
        out = wl._generate_fish_completion(wl.build_parser())
        assert "(= day)" not in out  # not shown when no aliases
