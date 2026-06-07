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

    def test_command_without_topic_has_no_pointer(self, tmp_db):
        # `projects` has no topic doc → no auto-pointer
        assert "wl help projects" not in self._sub(tmp_db, "projects").format_help()

    def test_no_duplicate_pointer_when_handwritten(self, tmp_db):
        # add.md exists + add's epilog hand-references wl help add → exactly one mention
        assert self._sub(tmp_db, "add").format_help().count("wl help add") == 1

    def test_completion_offers_help_topics(self, cli):
        _, out, _ = cli("print-completion", "fish")
        # the help positional completes to topic ids
        assert "subcommand_from help" in out
        assert "para" in out and "planning" in out
