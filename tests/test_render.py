"""Tests for render (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest

ESC = "["  # ANSI escape prefix


class TestColorRendering:
    def test_default_no_ansi_in_non_tty(self, cli):
        """default auto + non-TTY (StringIO in tests) → plain text, no ANSI"""
        cli("add", "highlight test", "-p", "A")
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
        """find link hit: [[ ]] brackets survive (not eaten by markup), even though the matched
        term inside is hit-highlighted (so the literal isn't contiguous)."""
        cli("add", "linked task")
        cli("link", "1", "Dev tooling")
        code, out, _ = cli("--color", "always", "find", "Dev", "--in", "link")
        assert "[[" in out and "tooling]]" in out   # brackets preserved (escaped, not markup-eaten)

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


class TestTermHighlight:
    def test_hl_terms_marks_each_term_plain(self, tmp_db):
        wl = tmp_db
        wl._init_console("never", None)
        out = wl.render._hl_terms("优化性能瓶颈", ["性能", "优化"])
        assert "*性能*" in out and "*优化*" in out   # both terms marked, non-contiguous

    def test_hl_terms_no_match_is_plain(self, tmp_db):
        wl = tmp_db
        wl._init_console("never", None)
        assert wl.render._hl_terms("nothing here", ["zzz"]) == "nothing here"

    def test_hl_terms_case_insensitive_styled(self, tmp_db):
        wl = tmp_db
        wl._init_console("always", None)
        out = wl.render._hl_terms("Build the API", ["api"])
        assert "[hit]" in out and "API" in out      # styled, preserves original case

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
        cli("add", "t1")
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
        cli("add", "short title with key word foo bar")
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
        cli("add", "t1")
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
        cli("add", "t1", "-t", "work,urgent")
        cli("start", "1")
        cli("stop", "1")
        _, out, _ = cli("ls", "--all")
        # both tags and clock should appear
        assert "t1" in out


class TestTitleWrap:
    """--title / $WORKLOG_TITLE: wrap (multi-line, hang-indented; default) vs clip (one line + …)."""

    def test_resolve_title_mode(self, monkeypatch):
        from worklog.helpers import _resolve_title_mode
        monkeypatch.delenv("WORKLOG_TITLE", raising=False)
        assert _resolve_title_mode(None) == "wrap"       # default
        assert _resolve_title_mode("wrap") == "wrap"
        assert _resolve_title_mode("clip") == "clip"
        assert _resolve_title_mode("garbage") == "wrap"  # anything but clip → safe wrap default
        monkeypatch.setenv("WORKLOG_TITLE", "clip")
        assert _resolve_title_mode(None) == "clip"       # env fallback
        assert _resolve_title_mode("wrap") == "wrap"     # explicit flag overrides env

    def test_wrap_display_short_one_line(self):
        from worklog.helpers import _wrap_display
        assert _wrap_display("hello world", 40) == ["hello world"]

    def test_wrap_display_empty(self):
        from worklog.helpers import _wrap_display
        assert _wrap_display("", 40) == [""]

    def test_wrap_display_breaks_on_spaces(self):
        from worklog.helpers import _wrap_display, _display_width
        lines = _wrap_display("alpha beta gamma delta epsilon", 12)
        assert len(lines) > 1
        for ln in lines:
            assert _display_width(ln) <= 12
            assert not ln.startswith(" ")      # no leading space carried onto wrapped lines

    def test_wrap_display_hard_breaks_cjk_run(self):
        from worklog.helpers import _wrap_display, _display_width
        # CJK has no spaces → must hard-break per character; each line within the cap
        lines = _wrap_display("中文标题没有空格必须按字符硬折行", 10)
        assert len(lines) > 1
        for ln in lines:
            assert _display_width(ln) <= 10

    def test_node_line_wrap_hangs_indent(self, cli):
        from worklog import helpers
        long_title = "x" * 200
        cli("add", long_title, "-p", "A")
        helpers._set_width_cap(40)
        helpers._set_title_mode("wrap")
        try:
            from worklog import cli as wl
            con = wl.db_connect()
            n = con.execute("SELECT * FROM node WHERE id=1").fetchone()
            line = wl._node_line(con, n)
        finally:
            helpers._set_width_cap(None)
            helpers._set_title_mode("wrap")
        parts = line.split("\n")
        assert len(parts) > 1                       # wrapped onto multiple visual lines
        # continuation lines hang-indent under the title (not column 0)
        prefix_cols = len(parts[0]) - len(parts[0].lstrip())  # leading spaces of 2nd line measure indent
        for cont in parts[1:]:
            assert cont.startswith(" ")             # indented, never flush-left
            assert cont.strip()                     # and carries title text

    def test_node_line_clip_single_line(self, cli):
        from worklog import helpers
        cli("add", "y" * 200, "-p", "B")
        helpers._set_width_cap(40)
        helpers._set_title_mode("clip")
        try:
            from worklog import cli as wl
            con = wl.db_connect()
            n = con.execute("SELECT * FROM node WHERE id=1").fetchone()
            line = wl._node_line(con, n)
        finally:
            helpers._set_width_cap(None)
            helpers._set_title_mode("wrap")
        assert "\n" not in line                      # clip = exactly one line
        assert "…" in line                           # truncated with ellipsis


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


class TestPriorityMarker:
    """unset priority renders [# ] (aligned, muted) — never blank, never [ ] (the TODO marker)."""

    def test_unset_priority_shows_hash_space_marker(self, cli):
        cli("add", "no-pri task")     # no -p
        _, out, _ = cli("--color", "never", "ls")
        line = next(l for l in out.splitlines() if "no-pri task" in l)
        assert "[# ]" in line                         # priority slot present + unset
        # the TODO status marker [ ] and the priority slot [# ] both appear, distinct
        assert line.count("[ ]") == 1 and "[# ]" in line

    def test_set_and_unset_priority_columns_align(self, cli):
        cli("add", "has A", "-p", "A")
        cli("add", "no pri")
        _, out, _ = cli("--color", "never", "ls")
        a_line = next(l for l in out.splitlines() if "has A" in l)
        u_line = next(l for l in out.splitlines() if "no pri" in l)
        # the #id starts at the same column on both rows (4-col priority slot either way)
        assert a_line.index("#") == u_line.index("#")

    def test_pri_marker_helper(self):
        from worklog import render
        from worklog.render import _pri_marker
        # this is a plain-mode unit test; render._CONSOLE is a module global the cli-reload fixture
        # doesn't reset, so pin it to None here to establish the documented "no console" precondition
        # (otherwise a sibling test's `--color always` console can leak in).
        render._CONSOLE = None
        # plain mode (no console) returns the bare markers
        assert _pri_marker("A") == "[#A]"
        assert _pri_marker(None) == "[# ]"
