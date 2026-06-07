"""`wl help` — the info-style topic browser (DESIGN §25). Covers the index, single-topic
rendering + see-also, unknown-topic suggestions, language fallback, and a doc-integrity
check that every see_also link resolves (no dangling cross-references)."""
import argparse
import pytest

from worklog.commands.help import (
    HELP_DIR, FALLBACK_LANG, _parse_doc, _list_topics, _topic_path, cmd_help,
)


def _all_en_topics():
    return sorted(p.stem for p in (HELP_DIR / FALLBACK_LANG).glob("*.md"))


class TestHelpDocs:
    def test_en_dir_exists_with_index(self):
        assert (HELP_DIR / FALLBACK_LANG / "index.md").is_file()

    def test_every_topic_has_title_and_category(self):
        for topic in _all_en_topics():
            meta, body = _parse_doc((HELP_DIR / FALLBACK_LANG / f"{topic}.md").read_text("utf-8"))
            assert meta.get("title"), f"{topic}: missing title"
            assert body.strip(), f"{topic}: empty body"
            if topic != "index":
                assert meta.get("category") in ("guide", "concept", "command", "param"), topic

    def test_all_see_also_targets_resolve(self):
        # no dangling cross-links — every see_also points at a real topic file
        topics = set(_all_en_topics())
        for topic in topics:
            meta, _ = _parse_doc((HELP_DIR / FALLBACK_LANG / f"{topic}.md").read_text("utf-8"))
            for ref in meta.get("see_also", "").split(",") if isinstance(meta.get("see_also"), str) else meta.get("see_also", []):
                ref = ref.strip()
                if ref:
                    assert ref in topics, f"{topic}: see_also '{ref}' has no topic doc"


class TestHelpCommand:
    def test_index_lists_topics_by_category(self, cli):
        _, out, _ = cli("help")
        assert "All topics" in out
        assert "Concepts" in out and "Commands" in out and "Guides" in out
        assert "node" in out and "add" in out

    def test_topic_renders_with_see_also(self, cli):
        _, out, _ = cli("help", "node")
        assert "everything is a node" in out
        assert "See also:" in out and "status" in out

    def test_unknown_topic_suggests(self, cli):
        _, _, err = cli("help", "nodez")
        assert "no help topic" in err and "node" in err

    def test_lang_falls_back_to_en(self, cli, monkeypatch):
        monkeypatch.setenv("WORKLOG_LANG", "zz")   # no such dir → en
        _, out, _ = cli("help", "status")
        assert "TODO" in out and "DOING" in out

    def test_help_needs_no_con(self, tmp_db):
        # cmd_help must not touch the DB (it runs before `wl init` in main())
        p = tmp_db.build_parser()
        args = p.parse_args(["help", "para"])
        cmd_help(args, None)   # must not raise


class TestHelpIntegration:
    """--help ↔ wl help wiring: a command with a topic auto-gains a 'More: wl help <cmd>'
    pointer; `wl help <topic>` is tab-completable; the splash points to wl help."""

    def _sub(self, tmp_db, name):
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        return sa.choices[name]

    def test_command_with_topic_gets_auto_pointer(self, tmp_db):
        # log / tag / sched have topic docs → their --help points into wl help
        for cmd in ("log", "tag", "sched", "tree", "find"):
            h = self._sub(tmp_db, cmd).format_help()
            assert f"wl help {cmd}" in h, f"{cmd} -h missing wl help pointer"

    def test_help_command_itself_has_no_auto_pointer(self, tmp_db):
        # every other command now has a topic; `help` has neither a same-named topic nor a
        # family mapping, so it's the one command with no auto-pointer (no self-reference).
        assert "More: `wl help" not in self._sub(tmp_db, "help").format_help()

    def test_no_duplicate_pointer_when_handwritten(self, tmp_db):
        # add.md exists + add's epilog hand-references wl help add → exactly one mention
        assert self._sub(tmp_db, "add").format_help().count("wl help add") == 1

    def test_every_command_links_into_wl_help(self, tmp_db):
        # every command's --help points into wl help — its own topic or a family topic
        # (only `help` itself is exempt). Guards the auto-pointer + _HELP_FAMILY coverage.
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        seen, missing = set(), []
        for name, sub in sa.choices.items():
            if id(sub) in seen:
                continue
            seen.add(id(sub))
            if name != "help" and "More: `wl help" not in sub.format_help():
                missing.append(name)
        assert missing == [], f"commands with no wl help pointer: {missing}"

    def test_completion_offers_help_topics(self, cli):
        _, out, _ = cli("print-completion", "fish")
        # the help positional completes to topic ids
        assert "subcommand_from help" in out
        assert "para" in out and "planning" in out


class TestHelpRendering:
    """The restricted-Markdown renderer (DESIGN §25): strips markers in plain mode, and
    (with color) escapes literal brackets so bodies with [ ] / [x] / [/] don't crash rich."""

    def test_strip_md_removes_inline_markers(self):
        from worklog.commands.help import _strip_md
        assert _strip_md("**bold** *it* `code` and [text](http://x)") == \
            "bold it code and text (http://x)"

    def test_md_inline_plain_passthrough(self, monkeypatch):
        import worklog.render as render
        from worklog.commands import help as H
        monkeypatch.setattr(render, "_CONSOLE", None)   # color off
        assert "**" not in H._md_inline("a **b** c") and "b" in H._md_inline("a **b** c")
        # literal brackets are untouched in plain mode (no escaping, no crash)
        assert H._md_inline("[/] done") == "[/] done"

    @pytest.mark.skipif(not __import__("worklog.render", fromlist=["_RICH_AVAIL"])._RICH_AVAIL,
                        reason="rich not installed")
    def test_md_inline_escapes_brackets_with_color(self, monkeypatch):
        import worklog.render as render
        from worklog.commands import help as H
        monkeypatch.setattr(render, "_CONSOLE", object())   # color on (truthy console)
        r = H._md_inline("status [/] and **go** and `wl ls`")
        assert "[header]go[/header]" in r        # bold → strong "header" style (visible)
        assert "[kind]wl ls[/kind]" in r         # inline code → bright cyan ("kind")
        assert r"\[/]" in r                      # the literal [/] is escaped, not a stray tag

    @pytest.mark.skipif(not __import__("worklog.render", fromlist=["_RICH_AVAIL"])._RICH_AVAIL,
                        reason="rich not installed")
    def test_status_markers_and_commands_colored(self, monkeypatch):
        import worklog.render as render
        from worklog.commands import help as H
        monkeypatch.setattr(render, "_CONSOLE", object())
        # a `code` span that is a status marker → its real status style (not generic cyan)
        assert "[done]" in H._md_inline("finish with `[x]`")
        assert "[doing]" in H._md_inline("in progress `[/]`")
        # a bare `wl <subcommand>` → command color when it's a real command…
        assert "[kind]wl day[/kind]" in H._md_inline("run wl day now")
        # …but prose like "wl maps" (not a command) stays body, not mis-colored
        assert "[kind]wl maps" not in H._md_inline("wl maps the tree")

    @pytest.mark.skipif(not __import__("worklog.render", fromlist=["_RICH_AVAIL"])._RICH_AVAIL,
                        reason="rich not installed")
    def test_help_status_renders_with_color_no_crash(self, cli):
        # regression: bodies with [ ] / [/] / [x] used to crash rich markup parsing
        code, _, _ = cli("--color", "always", "help", "status")
        assert code == 0


class TestArgparseHelpColor:
    """colorize_help post-processes argparse `--help` into the wl help 3-tier scheme. It must be
    a no-op when color is off (so piped `-h` and the format_help() tests stay plain), and when on
    must style headings / options / commands / markers without dropping any visible text."""

    _SAMPLE = (
        "usage: wl log [-h]\n"
        "\n"
        "positional arguments:\n"
        "    add             add a log entry\n"
        "\n"
        "options:\n"
        "  -h, --help  show this help message and exit\n"
        "\n"
        "Concepts:\n"
        "  status  marker [/] doing [x] done; priority [#A]; run `wl day` / wl bogus\n"
    )

    def test_color_off_is_identity(self, monkeypatch):
        from worklog.commands.help import colorize_help
        monkeypatch.setattr("sys.argv", ["wl", "-h"])
        monkeypatch.delenv("WORKLOG_COLOR", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")          # force off regardless of TTY
        assert colorize_help(self._SAMPLE) == self._SAMPLE

    def _force_on(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["wl", "-h"])
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("WORKLOG_COLOR", "always")
        monkeypatch.setenv("WORKLOG_THEME", "dark")

    @pytest.mark.skipif(not __import__("worklog.render", fromlist=["_RICH_AVAIL"])._RICH_AVAIL,
                        reason="rich not installed")
    def test_three_tier_scheme_applied(self, monkeypatch):
        self._force_on(monkeypatch)
        from rich.style import Style
        import worklog.render as render
        from worklog.commands.help import colorize_help
        out = colorize_help(self._SAMPLE)
        th = render.THEMES["dark"]
        S = lambda txt, key: Style.parse(th[key]).render(txt)
        # no visible text lost
        for s in ("usage:", "wl log", "add", "--help", "Concepts:", "[/]", "[x]", "[#A]", "wl day"):
            assert s in out, f"{s!r} missing from colorized help"
        assert S("usage:", "header") in out          # usage label → bold bright-white
        assert S("options:", "header") in out         # section header → bold bright-white
        assert S("--help", "kind") in out             # option flag → cyan
        assert S("add", "kind") in out                # subcommand-choice name → cyan
        assert S("[/]", "doing") in out and S("[x]", "done") in out   # markers → real status color
        assert S("[#A]", "pri_a") in out              # priority marker → its color
        assert S("wl day", "kind") in out             # `wl day` (real command) → cyan
        assert S("wl bogus", "body") in out           # `wl bogus` (not a command) → stays body

    @pytest.mark.skipif(not __import__("worklog.render", fromlist=["_RICH_AVAIL"])._RICH_AVAIL,
                        reason="rich not installed")
    def test_alignment_preserved_when_ansi_stripped(self, monkeypatch):
        # the colorized text, with ANSI removed, must equal the original (zero-width injection)
        self._force_on(monkeypatch)
        import re
        from worklog.commands.help import colorize_help
        out = colorize_help(self._SAMPLE)
        assert out != self._SAMPLE                                   # color actually applied
        # stripping ANSI (and the markdown backticks, which render as zero-width code spans)
        # restores the original byte-for-byte — i.e. coloring never shifts a column.
        bare = re.sub(r"\x1b\[[0-9;]*m", "", out).replace("`", "")
        assert bare == self._SAMPLE.replace("`", "")

    @pytest.mark.skipif(not __import__("worklog.render", fromlist=["_RICH_AVAIL"])._RICH_AVAIL,
                        reason="rich not installed")
    def test_explicit_color_never_in_argv_wins(self, monkeypatch):
        # `wl --color never -h` stays plain even on a (would-be) color TTY
        from worklog.commands.help import colorize_help
        monkeypatch.setattr("sys.argv", ["wl", "--color", "never", "-h"])
        monkeypatch.setenv("WORKLOG_COLOR", "always")
        assert colorize_help(self._SAMPLE) == self._SAMPLE

    def test_style_ansi_and_palette_helpers(self, monkeypatch):
        import worklog.render as render
        assert render.style_ansi("hi", "default") == "hi"   # mono/default → no codes
        assert render.style_ansi("", "bright_cyan") == ""   # empty → untouched
        # palette: off when NO_COLOR, a real theme dict when forced on
        monkeypatch.setattr("sys.argv", ["wl"])
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.delenv("WORKLOG_COLOR", raising=False)
        assert render.help_palette() is None
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("WORKLOG_COLOR", "always")
        if render._RICH_AVAIL:
            assert render.help_palette(theme_name="dark")["kind"] == "bright_cyan"

    def test_argv_color_theme_scan(self, monkeypatch):
        from worklog.commands.help import _argv_color_theme
        monkeypatch.setattr("sys.argv", ["wl", "--color", "always", "--theme=light", "log", "-h"])
        assert _argv_color_theme() == ("always", "light")
