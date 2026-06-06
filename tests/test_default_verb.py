"""The default-verb mechanism (WL#486) + the link entity group, the first collision
entity (group name == old leaf command). `wl link 42 doc` keeps working as the add
default verb; `wl link ls/rm` reach the group; `wl unlink` is the rm shortcut."""
import sqlite3
import pytest

from worklog.cli import _expand_default_verb


class TestExpandDefaultVerb:
    def test_leaf_form_gets_default_verb(self):
        assert _expand_default_verb(["link", "42", "doc"]) == ["link", "add", "42", "doc"]

    def test_explicit_verb_untouched(self):
        for v in ("add", "ls", "rm"):
            assert _expand_default_verb(["link", v, "42"]) == ["link", v, "42"]

    def test_help_flag_untouched(self):
        assert _expand_default_verb(["link", "-h"]) == ["link", "-h"]
        assert _expand_default_verb(["link", "--help"]) == ["link", "--help"]

    def test_bare_entity_untouched(self):
        assert _expand_default_verb(["link"]) == ["link"]

    def test_global_value_flag_skipped(self):
        assert _expand_default_verb(["--db", "x.db", "link", "42", "doc"]) == ["--db", "x.db", "link", "add", "42", "doc"]

    def test_global_bool_flag_skipped(self):
        assert _expand_default_verb(["-q", "link", "42", "doc"]) == ["-q", "link", "add", "42", "doc"]

    def test_eq_form_global_flag(self):
        assert _expand_default_verb(["--db=x.db", "link", "42", "d"]) == ["--db=x.db", "link", "add", "42", "d"]

    def test_non_collision_entity_untouched(self):
        # node is a clean group (no default verb); a bogus form is left for argparse to reject
        assert _expand_default_verb(["node", "42"]) == ["node", "42"]


class TestLinkGroup:
    def test_legacy_leaf_still_adds(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("link", "1", "Doc A")               # legacy leaf = default verb add
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE node_id=1 AND vault_doc='Doc A' AND deleted_at IS NULL").fetchone()[0] == 1

    def test_link_ls(self, cli):
        cli("add", "t", "-k", "task")
        cli("link", "1", "Doc A")
        _, out, _ = cli("link", "ls", "1")
        assert "Doc A" in out

    def test_link_rm_and_unlink_equivalent(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("link", "1", "A")
        cli("link", "1", "B")
        cli("link", "rm", "1", "A")    # group rm
        cli("unlink", "1", "B")         # shortcut
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE node_id=1 AND deleted_at IS NULL").fetchone()[0] == 0

    def test_link_add_explicit(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("link", "add", "1", "X")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE node_id=1 AND vault_doc='X' AND deleted_at IS NULL").fetchone()[0] == 1

    def test_doc_named_like_verb_via_id_first(self, cli, tmp_db):
        # `wl link 1 ls` — first token after link is an int → default add → doc named 'ls'
        cli("add", "t", "-k", "task")
        cli("link", "1", "ls")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE node_id=1 AND vault_doc='ls'").fetchone()[0] == 1

    def test_bare_link_usage(self, cli):
        _, _, err = cli("link")
        assert "usage: wl link" in err

    def test_link_help_lists_shortcuts(self, tmp_db):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        h = sa.choices["link"].format_help()
        assert "default verb" in h and "wl unlink" in h
