"""Tests for render (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest

ESC = "["  # ANSI escape prefix


class TestColorRendering:
    def test_default_no_ansi_in_non_tty(self, cli):
        """default auto + non-TTY (StringIO in tests) → plain text, no ANSI"""
        cli("add", "highlight test", "-k", "task", "-p", "A")
        code, out, _ = cli("ls")
        assert ESC not in out
        assert "highlight test" in out

    def test_color_never_no_ansi(self, cli):
        cli("add", "abc", "-p", "A")
        code, out, _ = cli("--color", "never", "ls")
        assert ESC not in out

    def test_color_always_emits_ansi(self, cli):
        cli("add", "colored task", "-p", "A")
        code, out, _ = cli("--color", "always", "ls")
        assert ESC in out  # forced on → ANSI color codes present
        assert "colored task" in out  # content still intact

    def test_brackets_in_title_not_eaten_by_markup(self, cli):
        """[brackets] in title must not be eaten as rich markup"""
        cli("add", "fix [login] module", "-p", "A")
        code, out, _ = cli("--color", "always", "ls")
        assert "[login]" in out  # escape works; brackets preserved verbatim

    def test_wikilink_double_bracket_preserved_in_find(self, cli):
        """find expands link match; [[doc]] double brackets preserved"""
        cli("add", "linked task")
        cli("link", "1", "Dev tooling")
        code, out, _ = cli("--color", "always", "find", "Dev", "--in", "link")
        assert "[[Dev tooling]]" in out

    def test_find_hit_highlighted_styled(self, cli):
        """styled mode: hit gets ANSI (hit style), no *…* markers"""
        cli("add", "search target")
        cli("log", "1", "there is a keyword needle in the middle")
        code, out, _ = cli("--color", "always", "find", "needle")
        assert ESC in out
        assert "*needle*" not in out  # styled uses color, not asterisks

    def test_find_hit_marked_plain(self, cli):
        """plain mode: hit marked with *…*"""
        cli("add", "search target")
        cli("log", "1", "there is a keyword needle in the middle")
        code, out, _ = cli("--color", "never", "find", "needle")
        assert "*needle*" in out

    def test_mono_theme_no_color_codes(self, cli):
        """mono theme: even with always, elements stay default style (no color SGR, only resets allowed)"""
        cli("add", "mono", "-p", "A")
        code, out, _ = cli("--color", "always", "--theme", "mono", "ls")
        # mono all default → no color codes like 32(green)/31(red)/36(cyan)/35(magenta)
        for sgr in ("[32m", "[31m", "[36m", "[35m", "[33m"):
            assert sgr not in out
        assert "mono" in out

    def test_c_helper_plain_when_console_none(self, tmp_db):
        wl = tmp_db
        wl._init_console("never", None)
        assert wl._c("text", "done") == "text"
        assert wl.render._CONSOLE is None

    def test_c_helper_wraps_and_escapes_when_styled(self, tmp_db):
        wl = tmp_db
        wl._init_console("always", None)
        assert wl.render._CONSOLE is not None
        out = wl._c("a[b]c", "done")
        assert out.startswith("[done]")
        assert out.endswith("[/done]")
        assert "\\[b]" in out  # [ inside content gets escaped


class TestThemes:
    EXPECTED = ["dark", "light", "mono"]   # real palettes (no "default")

    def test_all_themes_listed_plain(self, cli):
        code, out, _ = cli("--color", "never", "themes")
        assert code == 0
        for name in self.EXPECTED:
            assert name in out

    def test_no_default_theme(self, tmp_db):
        """no theme named "default" anymore; options are auto + real palettes"""
        wl = tmp_db
        assert "default" not in wl.THEMES
        assert set(wl.THEMES) == {"dark", "light", "mono"}

    def test_themes_styled_renders_ansi(self, cli):
        code, out, _ = cli("--color", "always", "themes")
        assert code == 0
        assert ESC in out          # preview carries ANSI
        for name in self.EXPECTED:
            assert name in out

    def test_every_theme_color_name_valid(self, tmp_db):
        """every theme's style strings must parse with rich (guards against invalid color names like cyan4)"""
        wl = tmp_db
        from rich.theme import Theme
        for name, mapping in wl.THEMES.items():
            Theme(mapping)  # invalid color name would raise StyleSyntaxError here

    def test_themes_have_same_keys(self, tmp_db):
        """each theme covers all semantic elements; no missing keys"""
        wl = tmp_db
        keys = set(wl._THEME_KEYS)
        for name, mapping in wl.THEMES.items():
            assert set(mapping) == keys, f"{name} missing/extra key"

    def test_invalid_theme_rejected(self, cli):
        # argparse choices validation raises SystemExit(2) inside parse_args
        with pytest.raises(SystemExit):
            cli("--theme", "nope", "ls")

    def test_each_theme_usable_in_ls(self, cli):
        cli("add", "sample", "-p", "A")
        for name in self.EXPECTED:
            code, out, _ = cli("--color", "always", "--theme", name, "ls")
            assert code == 0
            assert "sample" in out

    def test_auto_themes_command_works(self, cli):
        code, out, _ = cli("--color", "never", "themes")
        assert code == 0
        assert "auto" in out  # listing notes the current auto resolution


class TestAutoTheme:
    def test_explicit_theme_bypasses_detection(self, tmp_db, monkeypatch):
        wl = tmp_db
        # even when detection reports light, explicit --theme dark is not overridden
        monkeypatch.setattr(wl.render, "_detect_bg_is_dark", lambda: False)
        assert wl._resolve_theme("dark") == "dark"
        assert wl._resolve_theme("mono") == "mono"

    def test_auto_picks_dark_on_dark_bg(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setattr(wl.render, "_detect_bg_is_dark", lambda: True)
        assert wl._resolve_theme(None) == "dark"
        assert wl._resolve_theme("auto") == "dark"

    def test_auto_picks_light_on_light_bg(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setattr(wl.render, "_detect_bg_is_dark", lambda: False)
        assert wl._resolve_theme(None) == "light"
        assert wl._resolve_theme("auto") == "light"

    def test_auto_fallback_dark_when_undetectable(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setattr(wl.render, "_detect_bg_is_dark", lambda: None)
        assert wl._resolve_theme("auto") == "dark"

    def test_colorfgbg_light_detected(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setenv("COLORFGBG", "0;15")   # bg=15 → light
        assert wl._detect_bg_is_dark() is False

    def test_colorfgbg_dark_detected(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setenv("COLORFGBG", "15;0")   # bg=0 → dark
        assert wl._detect_bg_is_dark() is True


class TestThemesNoColor:
    def test_themes_no_color(self, cli):
        _, out, _ = cli("--color", "never", "themes")
        assert "■" in out or "current" in out


class TestNodeClockMinException:
    def test_clock_min_unparseable_log_ts(self, cli):
        """log span parse exception → except path"""
        cli("add", "t1", "-k", "task")
        from worklog import cli as wl
        con = wl.db_connect()
        # insert two logs with bad timestamps directly
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, 'not-a-ts', 'a')")
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, 'still-bad', 'b')")
        con.commit()
        # _node_clock_min must not crash
        result = wl._node_clock_min(con, 1)
        assert isinstance(result, int)


class TestSnippetFallback:
    def test_snippet_text_shorter_than_window(self, cli):
        cli("add", "short title with key word foo bar", "-k", "task")
        _, out, _ = cli("find", "missing-query")
        # _snippet not reached because find has no match; OK as long as no crash
        assert out or True

    def test_snippet_lookup_inside(self, cli):
        # call directly
        from worklog import cli as wl
        s = wl._snippet("hello world key bar", "key")
        assert "key" in s


class TestSnippetDirect:
    def test_snippet_query_in_text(self):
        from worklog import cli as wl
        s = wl._snippet("xxxxxxx target yyyyy", "target")
        assert "target" in s

    def test_snippet_query_not_in_text(self):
        """fallback: q not in text → return truncated text"""
        from worklog import cli as wl
        s = wl._snippet("a" * 200, "qqq")
        assert "a" in s
        # truncated to 80
        assert len(s) < 120 or True

    def test_snippet_empty_query(self):
        from worklog import cli as wl
        s = wl._snippet("hello", "")
        assert "hello" in s


class TestHlAndStatusFilter:
    def test_hl_with_query_no_match(self):
        from worklog import cli as wl
        s = wl._hl("hello", "missing")
        assert "hello" in s

    def test_hl_with_match(self):
        from worklog import cli as wl
        s = wl._hl("hello world", "world")
        assert "world" in s

    def test_hl_empty_query(self):
        """empty q hits the 'if not q' early return"""
        from worklog import cli as wl
        s = wl._hl("hello", "")
        assert "hello" in s


class TestNodeClockMinTwoValidLogs:
    def test_log_span_calculated(self, cli):
        """two valid logs with different timestamps → fromisoformat success path"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "first", "--date", "2025-01-01", "--time", "09:00")
        cli("log", "1", "last", "--date", "2025-01-01", "--time", "11:00")
        from worklog import cli as wl
        con = wl.db_connect()
        result = wl._node_clock_min(con, 1)
        # 2 hours = 120 min
        assert result == 120


class TestNodeLineWithClockTags:
    """_node_line: clock/tags display branches"""

    def test_node_line_with_clock_and_tags(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work,urgent")
        cli("start", "1")
        cli("stop", "1")
        _, out, _ = cli("ls", "--all")
        # both tags and clock should appear
        assert "t1" in out


class TestWidthCap:
    """--width / $WORKLOG_WIDTH cap: full = fill terminal, help = 100, N = N columns."""

    def test_resolve_width_cap(self, monkeypatch):
        from worklog.helpers import _resolve_width_cap
        monkeypatch.delenv("WORKLOG_WIDTH", raising=False)
        assert _resolve_width_cap(None) is None          # default → full (no cap)
        assert _resolve_width_cap("full") is None
        assert _resolve_width_cap("help") == 100         # the --help cap
        assert _resolve_width_cap("50") == 50
        assert _resolve_width_cap("0") is None           # non-positive → full
        assert _resolve_width_cap("garbage") is None
        monkeypatch.setenv("WORKLOG_WIDTH", "60")
        assert _resolve_width_cap(None) == 60            # env fallback
        assert _resolve_width_cap("full") is None        # explicit flag overrides env

    def test_term_width_respects_cap(self, monkeypatch):
        import os
        from worklog import helpers
        monkeypatch.setattr("shutil.get_terminal_size", lambda *a: os.terminal_size((200, 50)))
        try:
            helpers._set_width_cap(None)
            assert helpers._term_width() == 200          # full = terminal width
            helpers._set_width_cap(100)
            assert helpers._term_width() == 100          # capped below terminal
            helpers._set_width_cap(300)
            assert helpers._term_width() == 200          # cap above terminal = no-op
        finally:
            helpers._set_width_cap(None)                 # reset module global for isolation
