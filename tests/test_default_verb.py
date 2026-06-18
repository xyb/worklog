"""The default-verb mechanism + the link entity group, the first collision
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

    def test_tag_leaf_form_gets_default_verb(self):
        assert _expand_default_verb(["tag", "42", "+work"]) == ["tag", "add", "42", "+work"]
        # a removal op after the id still triggers expansion (id is the trigger, not the op)
        assert _expand_default_verb(["tag", "42", "-planned"]) == ["tag", "add", "42", "-planned"]
        # bare list form
        assert _expand_default_verb(["tag", "42"]) == ["tag", "add", "42"]

    def test_tag_explicit_verb_untouched(self):
        for v in ("add", "ls", "rm"):
            assert _expand_default_verb(["tag", v, "42"]) == ["tag", v, "42"]

    def test_tag_help_and_bare_untouched(self):
        assert _expand_default_verb(["tag", "-h"]) == ["tag", "-h"]
        assert _expand_default_verb(["tag"]) == ["tag"]

    def test_log_leaf_form_gets_default_verb(self):
        assert _expand_default_verb(["log", "42", "body"]) == ["log", "add", "42", "body"]

    def test_log_explicit_verbs_untouched(self):
        for v in ("add", "ls", "edit", "rm"):
            assert _expand_default_verb(["log", v, "1"]) == ["log", v, "1"]

    def test_log_help_and_bare_untouched(self):
        assert _expand_default_verb(["log", "-h"]) == ["log", "-h"]
        assert _expand_default_verb(["log"]) == ["log"]

    def test_sched_leaf_form_gets_default_verb(self):
        assert _expand_default_verb(["sched", "42", "2026-06-15"]) == ["sched", "add", "42", "2026-06-15"]
        # --clear after the id still triggers expansion (id is the trigger)
        assert _expand_default_verb(["sched", "42", "--clear"]) == ["sched", "add", "42", "--clear"]
        assert _expand_default_verb(["sched", "42"]) == ["sched", "add", "42"]

    def test_sched_explicit_verbs_untouched(self):
        for v in ("add", "ls", "rm"):
            assert _expand_default_verb(["sched", v, "1"]) == ["sched", v, "1"]

    def test_sched_help_and_bare_untouched(self):
        assert _expand_default_verb(["sched", "-h"]) == ["sched", "-h"]
        assert _expand_default_verb(["sched"]) == ["sched"]


def _tags(con, nid):
    return [r[0] for r in con.execute(
        "SELECT tag FROM tag WHERE node_id=? AND deleted_at IS NULL ORDER BY tag", (nid,))]


class TestTagGroup:
    def test_legacy_leaf_still_adds(self, cli, tmp_db):
        cli("add", "t")
        cli("tag", "1", "+work", "+P0")          # legacy leaf = default verb add
        assert _tags(tmp_db.db_connect(), 1) == ["P0", "work"]

    def test_leaf_mixed_add_remove(self, cli, tmp_db):
        cli("add", "t")
        cli("tag", "1", "+work", "+P0")
        cli("tag", "1", "+urgent", "-P0")        # add & remove in one call (default verb)
        assert _tags(tmp_db.db_connect(), 1) == ["urgent", "work"]

    def test_leaf_bare_word_adds(self, cli, tmp_db):
        cli("add", "t")
        cli("tag", "1", "work")                   # bare word = add
        assert _tags(tmp_db.db_connect(), 1) == ["work"]

    def test_tag_ls(self, cli):
        cli("add", "t")
        cli("tag", "1", "+work")
        _, out, _ = cli("tag", "ls", "1")
        assert "work" in out
        # bare `wl tag <id>` lists the same way (default verb, empty ops)
        _, out2, _ = cli("tag", "1")
        assert "work" in out2

    def test_tag_rm_explicit(self, cli, tmp_db):
        cli("add", "t")
        cli("tag", "1", "+work", "+P0")
        cli("tag", "rm", "1", "P0")               # group rm (plain name)
        assert _tags(tmp_db.db_connect(), 1) == ["work"]

    def test_tag_rm_strips_plus_prefix(self, cli, tmp_db):
        cli("add", "t")
        cli("tag", "1", "+work")
        cli("tag", "rm", "1", "+work")            # leading + tolerated
        assert _tags(tmp_db.db_connect(), 1) == []

    def test_tag_add_explicit(self, cli, tmp_db):
        cli("add", "t")
        cli("tag", "add", "1", "+work")
        assert _tags(tmp_db.db_connect(), 1) == ["work"]

    def test_readd_revives_tombstone(self, cli, tmp_db):
        cli("add", "t")
        cli("tag", "1", "+work")
        cli("tag", "rm", "1", "work")
        cli("tag", "1", "+work")                  # re-add must revive, not duplicate
        con = tmp_db.db_connect()
        assert _tags(con, 1) == ["work"]
        assert con.execute("SELECT COUNT(*) FROM tag WHERE node_id=1 AND tag='work'").fetchone()[0] == 1

    def test_leaf_pure_removal_only_op(self, cli, tmp_db):
        # `wl tag 1 -drop` → expand → `wl tag add 1 -drop`; REMAINDER must capture a
        # first op that starts with '-' so the removal still lands.
        cli("add", "t")
        cli("tag", "1", "+keep", "+drop")
        cli("tag", "1", "-drop")
        assert _tags(tmp_db.db_connect(), 1) == ["keep"]

    def test_id_first_token_is_data_not_verb(self, cli, tmp_db):
        # `wl tag 1 ls` — first token after tag is an int id → default add → 'ls' is a
        # bare tag name (data), NOT the ls verb. (mirrors the link id-first rule)
        cli("add", "t")
        cli("tag", "1", "ls")
        assert _tags(tmp_db.db_connect(), 1) == ["ls"]
        # whereas verb-first `wl tag ls 1` lists and adds nothing
        _, out, _ = cli("tag", "ls", "1")
        assert "ls" in out
        assert _tags(tmp_db.db_connect(), 1) == ["ls"]

    def test_group_verbs_reject_missing_node(self, cli):
        for argv in (("tag", "ls", "999"), ("tag", "rm", "999", "x"), ("tag", "999", "+y")):
            _, _, err = cli(*argv)
            assert "not found" in err

    def test_bare_tag_usage(self, cli):
        _, _, err = cli("tag")
        assert "usage: wl tag" in err

    def test_tag_help_lists_shortcuts(self, tmp_db):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        h = sa.choices["tag"].format_help()
        assert "default verb" in h and "wl tag ls" in h

    def test_tag_add_help_names_shortcut(self, tmp_db):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        tag = sa.choices["tag"]
        tsa = next(a for a in tag._actions if isinstance(a, argparse._SubParsersAction))
        h = tsa.choices["add"].format_help()
        assert "omit" in h or "wl tag" in h


class TestLinkGroup:
    def test_legacy_leaf_still_adds(self, cli, tmp_db):
        cli("add", "t")
        cli("link", "1", "Doc A")               # legacy leaf = default verb add
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE node_id=1 AND vault_doc='Doc A' AND deleted_at IS NULL").fetchone()[0] == 1

    def test_link_ls(self, cli):
        cli("add", "t")
        cli("link", "1", "Doc A")
        _, out, _ = cli("link", "ls", "1")
        assert "Doc A" in out

    def test_link_rm_and_unlink_equivalent(self, cli, tmp_db):
        cli("add", "t")
        cli("link", "1", "A")
        cli("link", "1", "B")
        cli("link", "rm", "1", "A")    # group rm
        cli("unlink", "1", "B")         # shortcut
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE node_id=1 AND deleted_at IS NULL").fetchone()[0] == 0

    def test_link_add_explicit(self, cli, tmp_db):
        cli("add", "t")
        cli("link", "add", "1", "X")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM link WHERE node_id=1 AND vault_doc='X' AND deleted_at IS NULL").fetchone()[0] == 1

    def test_doc_named_like_verb_via_id_first(self, cli, tmp_db):
        # `wl link 1 ls` — first token after link is an int → default add → doc named 'ls'
        cli("add", "t")
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


def _logs(con, nid):
    return [(r[0], r[1]) for r in con.execute(
        "SELECT id, body FROM log WHERE node_id=? AND deleted_at IS NULL ORDER BY id", (nid,))]


class TestLogGroup:
    def test_legacy_leaf_still_adds(self, cli, tmp_db):
        cli("add", "t")
        cli("log", "1", "first progress")          # legacy leaf = default verb add
        assert [b for _, b in _logs(tmp_db.db_connect(), 1)] == ["first progress"]

    def test_log_add_explicit(self, cli, tmp_db):
        cli("add", "t")
        cli("log", "add", "1", "explicit")
        assert [b for _, b in _logs(tmp_db.db_connect(), 1)] == ["explicit"]

    def test_log_ls(self, cli):
        cli("add", "t")
        cli("log", "1", "alpha")
        _, out, _ = cli("log", "ls", "1")
        assert "alpha" in out and "#L" in out

    def test_log_ls_empty(self, cli):
        cli("add", "t")
        _, out, _ = cli("log", "ls", "1")
        assert "no logs" in out

    def test_log_edit_equals_relog(self, cli, tmp_db):
        cli("add", "t")
        cli("log", "1", "typo")
        cli("log", "edit", "L1", "fixed")          # group edit == relog
        assert [b for _, b in _logs(tmp_db.db_connect(), 1)] == ["fixed"]
        # the relog shortcut is equivalent
        cli("log", "1", "typo2")
        cli("relog", "L2", "fixed2")
        assert ("fixed2" in [b for _, b in _logs(tmp_db.db_connect(), 1)])

    def test_log_rm_equals_unlog(self, cli, tmp_db):
        cli("add", "t")
        cli("log", "1", "a")
        cli("log", "1", "b")
        cli("log", "rm", "L1")                      # group rm
        cli("unlog", "L2")                          # shortcut
        assert _logs(tmp_db.db_connect(), 1) == []

    def test_id_first_token_is_body_not_verb(self, cli, tmp_db):
        # `wl log 1 ls` — int id first → default add → body is the literal word 'ls'
        cli("add", "t")
        cli("log", "1", "ls")
        assert [b for _, b in _logs(tmp_db.db_connect(), 1)] == ["ls"]
        # `wl log 1 edit` likewise adds a log bodied 'edit', NOT an edit verb
        cli("log", "1", "edit")
        assert "edit" in [b for _, b in _logs(tmp_db.db_connect(), 1)]

    def test_log_ls_missing_node(self, cli):
        _, _, err = cli("log", "ls", "999")
        assert "not found" in err

    def test_bare_log_usage(self, cli):
        _, _, err = cli("log")
        assert "usage: wl log" in err

    def test_log_help_lists_shortcuts(self, tmp_db):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        h = sa.choices["log"].format_help()
        assert "default verb" in h and "wl relog" in h and "wl unlog" in h

    def test_relog_unlog_help_name_canonical(self, tmp_db):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        assert "wl log edit" in sa.choices["relog"].format_help()
        assert "wl log rm" in sa.choices["unlog"].format_help()


def _sched(con, nid):
    return [(r[0], r[1]) for r in con.execute(
        "SELECT on_date, rrule FROM sched WHERE node_id=? AND deleted_at IS NULL ORDER BY on_date NULLS LAST, rrule", (nid,))]


class TestSchedGroup:
    def test_legacy_leaf_still_schedules(self, cli, tmp_db):
        cli("add", "t")
        cli("sched", "1", "2026-06-15")              # legacy leaf = default verb add
        assert _sched(tmp_db.db_connect(), 1) == [("2026-06-15", None)]

    def test_recur_via_default_verb(self, cli, tmp_db):
        cli("add", "t")
        cli("sched", "1", "--recur", "daily")
        assert ("daily" in [rr for _, rr in _sched(tmp_db.db_connect(), 1)])

    def test_sched_ls(self, cli):
        cli("add", "t")
        cli("sched", "1", "2026-06-15")
        _, out, _ = cli("sched", "ls", "1")
        assert "2026-06-15" in out
        _, out2, _ = cli("sched", "1")               # bare = list (default verb, empty)
        assert "2026-06-15" in out2

    def test_clear_via_default_verb_and_rm_equivalent(self, cli, tmp_db):
        cli("add", "t")
        cli("sched", "1", "2026-06-15")
        cli("sched", "1", "--clear")                 # default-verb form clears
        assert _sched(tmp_db.db_connect(), 1) == []
        cli("sched", "1", "tomorrow")
        cli("sched", "rm", "1")                       # group rm clears
        assert _sched(tmp_db.db_connect(), 1) == []

    def test_sched_add_explicit(self, cli, tmp_db):
        cli("add", "t")
        cli("sched", "add", "1", "2026-07-01")
        assert _sched(tmp_db.db_connect(), 1) == [("2026-07-01", None)]

    def test_defer_still_separate(self, cli, tmp_db):
        # defer is NOT a sched verb — it stays its own composite command
        cli("add", "t")
        _, out, _ = cli("defer", "1", "tomorrow")
        assert "LATER" in out

    def test_sched_ls_missing_node(self, cli):
        _, _, err = cli("sched", "ls", "999")
        assert "not found" in err

    def test_bare_sched_usage(self, cli):
        _, _, err = cli("sched")
        assert "usage: wl sched" in err

    def test_sched_help_lists_shortcuts(self, tmp_db):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        h = sa.choices["sched"].format_help()
        assert "default verb" in h and "wl defer" in h


class TestGroupLeafCompletions:
    """Regression: when a top-level command becomes a default-verb group, its leaf args
    (e.g. sched's --recur / when, log's --date) live under the `add` subparser; the
    completion generators must still surface them under the bare group condition."""

    def test_fish_sched_recur_and_when(self, cli):
        _, out, _ = cli("print-completion", "fish")
        assert "(__wl_recur_suggestions)" in out
        # sched's `when` takes a concrete day (someday rejected) → date suggestions, not defer's
        assert 'subcommand_from sched" -f -a "(__wl_date_suggestions)"' in out
        assert "(__wl_defer_suggestions)" in out   # defer keeps the someday-inclusive list

    def test_fish_log_date_present(self, cli):
        _, out, _ = cli("print-completion", "fish")
        # log --date completion (silently lost when log became a group) restored
        assert 'seen_subcommand_from log' in out and "(__wl_date_suggestions)" in out

    def test_bash_sched_recur(self, cli):
        _, out, _ = cli("print-completion", "bash")
        assert "__wl_recur_suggestions_bash" in out

    def test_zsh_generates(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        assert "compdef _wl wl" in out
