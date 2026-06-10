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

    def test_fish_completion_descr_with_quote_is_single_quoted(self, tmp_path, monkeypatch):
        """a help string with a literal `"` (the alias help: `wl alias add w "day -t work"`)
        must not break fish `complete`. Descriptions are wrapped in SINGLE quotes (where `"` is
        literal), so the generated script has no `-d "` and the embedded quote survives verbatim.
        Regression: double-quoted `-d` made fish read `-t` inside the help as an option."""
        wl = self._setup_aliases(tmp_path, monkeypatch, "[aliases]\n")
        out = wl._generate_fish_completion(wl.build_parser())
        assert ' -d "' not in out                       # every description is single-quoted
        assert 'wl alias add w "day -t work"' in out      # the quote-bearing help is preserved

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

    def test_load_aliases_multi_token_keyed_by_first_word(self, tmp_path, monkeypatch):
        """an arg-carrying target (`w = day -t work`) registers the alias under its first word."""
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nw = day -t work\np = day -t personal\n")
        loaded = wl._load_user_aliases()
        assert loaded == {"day": ["w", "p"]} or loaded == {"day": ["p", "w"]}

    def test_parser_registers_multi_token_alias_under_target(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch, "[aliases]\nw = day -t work\n")
        parser = wl.build_parser()
        import argparse
        sa = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        assert "w" in sa.choices and sa.choices["w"] is sa.choices["day"]


class TestExpandUserAlias:
    """`_expand_user_alias` — the argv splice that makes `wl w` == `wl day -t work`."""

    def test_single_token(self):
        from worklog.cli import _expand_user_alias
        assert _expand_user_alias(["d"], {"d": "day"}) == ["day"]

    def test_multi_token(self):
        from worklog.cli import _expand_user_alias
        assert _expand_user_alias(["w"], {"w": "day -t work"}) == ["day", "-t", "work"]

    def test_user_args_appended_after_expansion(self):
        from worklog.cli import _expand_user_alias
        assert _expand_user_alias(["w", "2026-06-10"], {"w": "day -t work"}) == \
            ["day", "-t", "work", "2026-06-10"]

    def test_global_flags_before_alias_are_skipped(self):
        from worklog.cli import _expand_user_alias
        # --color consumes its value; the alias after it still expands
        assert _expand_user_alias(["--color", "never", "w"], {"w": "day -t work"}) == \
            ["--color", "never", "day", "-t", "work"]

    def test_non_alias_subcommand_untouched(self):
        from worklog.cli import _expand_user_alias
        assert _expand_user_alias(["day", "-t", "work"], {"w": "day -t work"}) == \
            ["day", "-t", "work"]

    def test_alias_name_as_argument_not_expanded(self):
        from worklog.cli import _expand_user_alias
        # `w` here is find's query, not the subcommand → must NOT expand
        assert _expand_user_alias(["find", "w"], {"w": "day -t work"}) == ["find", "w"]

    def test_chain_resolves(self):
        from worklog.cli import _expand_user_alias
        assert _expand_user_alias(["ww"], {"ww": "w", "w": "day -t work"}) == \
            ["day", "-t", "work"]

    def test_cycle_does_not_hang(self):
        from worklog.cli import _resolve_alias_tokens
        # a → b → a: bounded, returns something without infinite recursion
        out = _resolve_alias_tokens("a", {"a": "b", "b": "a"})
        assert out is not None

    def test_empty_map_passthrough(self):
        from worklog.cli import _expand_user_alias
        assert _expand_user_alias(["w"], {}) == ["w"]


class TestUserAliasesIniExtra:
    def test_no_aliases_clean_output(self, tmp_path, monkeypatch):
        """without an ini file, output should have no alias traces"""
        monkeypatch.setenv("HOME", str(tmp_path))
        import importlib; from worklog import cli as wl
        wl._USER_ALIASES = None
        importlib.reload(wl)
        out = wl._generate_fish_completion(wl.build_parser())
        assert "(= day)" not in out  # not shown when no aliases
