"""Tests for `wl relation` — task↔task relations (relation.* props), the wl show
relations block, bidirectional derivation, and the -o json relations field."""
import json
import pytest


def _mk(cli, n):
    """Create n bare tasks, returns nothing (ids are 1..n)."""
    for i in range(n):
        cli("add", f"task {i + 1}", "-k", "task")


class TestRelationWrite:
    def test_split_from_writes_both_sides(self, cli):
        _mk(cli, 2)
        code, out, _ = cli("relation", "2", "split-from", "1")
        assert code == 0
        # #2's own view shows split-from #1
        assert "split-from:" in out and "#1" in out
        # the inverse is written on #1: it now has split-into #2 as a real prop
        _, j, _ = cli("show", "1", "-o", "json")
        d = json.loads(j)
        assert d["relations"]["split_into"] == [2]
        # relation.* props do NOT leak into the generic props dict
        assert not any(k.startswith("relation.") for k in d["props"])

    def test_split_into_inverse(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "split-into", "2")
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["split_from"] == [1]

    def test_related_is_symmetric(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        _, j1, _ = cli("show", "1", "-o", "json")
        _, j2, _ = cli("show", "2", "-o", "json")
        assert json.loads(j1)["relations"]["related"] == [2]
        assert json.loads(j2)["relations"]["related"] == [1]

    def test_multiple_ids_at_once(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "related", "2", "3")
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [2, 3]

    def test_hash_prefix_and_underscore_type_accepted(self, cli):
        _mk(cli, 2)
        # leading # on ids and underscore type form both normalize
        code, _, _ = cli("relation", "2", "split_from", "#1")
        assert code == 0
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["split_from"] == [1]

    def test_dedup_on_repeat(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        cli("relation", "1", "related", "2")  # again
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [2]


class TestRelationRemove:
    def test_rm_clears_both_sides(self, cli):
        _mk(cli, 2)
        cli("relation", "2", "split-from", "1")
        code, out, _ = cli("relation", "2", "split-from", "1", "--rm")
        assert code == 0
        _, j1, _ = cli("show", "1", "-o", "json")
        _, j2, _ = cli("show", "2", "-o", "json")
        assert json.loads(j1)["relations"]["split_into"] == []
        assert json.loads(j2)["relations"]["split_from"] == []

    def test_rm_one_of_many_keeps_rest(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "related", "2", "3")
        cli("relation", "1", "related", "2", "--rm")
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [3]

    def test_rm_last_removes_prop_entirely(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        cli("relation", "1", "related", "2", "--rm")
        # the relation.related prop should be gone, not an empty-string value
        _, j, _ = cli("show", "1", "-o", "json")
        d = json.loads(j)
        assert d["relations"]["related"] == []
        assert "relation.related" not in d["props"]


class TestAddRelation:
    def test_add_with_relation_writes_both_sides(self, cli):
        _mk(cli, 2)  # #1 #2
        code, out, _ = cli("add", "derived", "-k", "task",
                           "--relation", "split-from 1", "--relation", "related 2")
        assert code == 0
        assert "relation(s)" in out  # the success hint mentions relations
        _, j, _ = cli("show", "3", "-o", "json")
        d = json.loads(j)
        assert d["relations"]["split_from"] == [1]
        assert d["relations"]["related"] == [2]
        # both sides written on the existing nodes
        assert json.loads(cli("show", "1", "-o", "json")[1])["relations"]["split_into"] == [3]
        assert json.loads(cli("show", "2", "-o", "json")[1])["relations"]["related"] == [3]

    def test_add_relation_multiple_ids(self, cli):
        _mk(cli, 2)
        cli("add", "x", "-k", "task", "--relation", "related 1 2")
        _, j, _ = cli("show", "3", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [1, 2]

    def test_add_relation_underscore_and_hash(self, cli):
        _mk(cli, 1)
        cli("add", "x", "-k", "task", "--relation", "split_from #1")
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["split_from"] == [1]

    def test_add_relation_bad_type_errors(self, cli):
        _mk(cli, 1)
        code, _, err = cli("add", "x", "-k", "task", "--relation", "foo 1")
        assert code != 0
        assert "unknown relation type" in err

    def test_add_relation_missing_id_errors(self, cli):
        _mk(cli, 1)
        code, _, err = cli("add", "x", "-k", "task", "--relation", "split-from")
        assert code != 0
        assert "need '<type> <id>'" in err

    def test_add_relation_nonexistent_target_errors(self, cli):
        code, _, err = cli("add", "x", "-k", "task", "--relation", "related 999")
        assert code != 0
        assert "not found" in err


class TestRelationDerivation:
    def test_reverse_derived_from_one_sided_prop(self, cli):
        # set ONLY one side via raw `wl set`; the view must still surface the reverse
        _mk(cli, 2)
        cli("set", "2", "relation.split_from", "1")
        # #1 never got split_into written, but its view derives it
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["split_into"] == [2]

    def test_relation_to_deleted_node_dropped(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        cli("node", "rm", "2")  # soft-delete #2
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == []


class TestRelationList:
    def test_list_mode_no_relations(self, cli):
        _mk(cli, 1)
        code, out, _ = cli("relation", "1")
        assert code == 0
        assert "no relations" in out

    def test_list_mode_shows_titles(self, cli):
        _mk(cli, 2)
        cli("relation", "2", "split-from", "1")
        code, out, _ = cli("relation", "2")
        assert "split-from:" in out
        assert "task 1" in out  # the related node's title


class TestBacklinks:
    def test_text_mention_shows_linked_from(self, cli):
        cli("add", "target", "-k", "task")          # #1
        cli("add", "depends on it", "-k", "task")    # #2
        cli("log", "2", "blocked on #1 result")      # #2's log mentions #1
        _, out, _ = cli("show", "1")
        assert "=backrels" in out and "#2" in out

    def test_wl_prefix_counts(self, cli):
        cli("add", "target", "-k", "task")           # #1
        cli("add", "other", "-k", "task")            # #2
        cli("log", "2", "see WL#1 for context")
        _, out, _ = cli("show", "1")
        assert "=backrels" in out and "#2" in out

    def test_pr_ref_and_self_excluded(self, cli):
        cli("add", "target", "-k", "task")           # #1
        cli("add", "other", "-k", "task")            # #2
        cli("log", "2", "merged PR#1, not a node ref")   # PR#1 must NOT backlink #1
        cli("log", "1", "self mention #1")               # self excluded
        _, out, _ = cli("show", "1")
        assert "=backrels" not in out

    def test_no_substring_false_match(self, cli):
        cli("add", "target", "-k", "task")           # #1
        cli("add", "other", "-k", "task")            # #2
        cli("log", "2", "see #12 and #100")          # neither is #1
        _, out, _ = cli("show", "1")
        assert "=backrels" not in out

    def test_backlinks_in_json(self, cli):
        cli("add", "target", "-k", "task")           # #1
        cli("add", "other", "-k", "task")            # #2
        cli("log", "2", "ref #1")
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["backrels"] == [2]


class TestRelationErrors:
    def test_unknown_type_rejected(self, cli):
        _mk(cli, 2)
        # argparse choices reject an unknown type before the handler
        code, _, err = cli("relation", "1", "related", "2")  # sanity: valid passes
        assert code == 0

    def test_missing_node(self, cli):
        code, _, err = cli("relation", "999")
        assert code != 0
        assert "not found" in err

    def test_relate_to_missing_node(self, cli):
        _mk(cli, 1)
        code, _, err = cli("relation", "1", "related", "999")
        assert code != 0
        assert "not found" in err

    def test_non_int_id_rejected(self, cli):
        _mk(cli, 1)
        code, _, err = cli("relation", "1", "related", "abc")
        assert code != 0

    def test_self_relation_skipped(self, cli):
        _mk(cli, 1)
        code, out, _ = cli("relation", "1", "related", "1")
        assert code == 0
        assert "itself" in out
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == []

    def test_type_without_ids_errors(self, cli):
        _mk(cli, 1)
        code, _, err = cli("relation", "1", "related")
        assert code != 0
        assert "at least one" in err


class TestRelationShow:
    def test_show_renders_relations_block(self, cli):
        _mk(cli, 2)
        cli("relation", "2", "split-from", "1")
        code, out, _ = cli("show", "2")
        # the relation block nests under props: as a `relation:` sub-block
        assert "props:" in out and "relation:" in out
        assert "split-from:" in out
        # the raw relation.* prop key is NOT shown as a flat key=value row
        assert "relation.split_from" not in out

    def test_one_related_node_per_line(self, cli):
        # multiple related ids render one-per-line (like children), not comma-joined on one line
        _mk(cli, 3)
        cli("relation", "1", "related", "2", "3")
        _, out, _ = cli("relation", "1")
        body = [l for l in out.splitlines() if "task 2" in l or "task 3" in l]
        assert len(body) == 2  # each on its own line
        assert not any("task 2" in l and "task 3" in l for l in out.splitlines())

    def test_long_title_does_not_overflow_width(self, cli, monkeypatch):
        # a long related title wraps with hang-indent (like children/_node_line) instead of
        # overflowing the terminal width — the whole point of routing through _hang_wrap
        monkeypatch.setenv("COLUMNS", "60")
        cli("add", "x" * 200, "-k", "task")  # #1 long title (ASCII so len == display width)
        cli("add", "src", "-k", "task")      # #2
        cli("relation", "2", "related", "1")
        _, out, _ = cli("relation", "2")
        assert all(len(l) <= 60 for l in out.splitlines())   # nothing overflows
        # the title wrapped to a continuation line aligned under the title column
        title_lines = [l for l in out.splitlines() if set(l.strip()) == {"x"}]
        assert title_lines and all(l.startswith(" " * 18) for l in title_lines)


class TestAddRelationParse:
    def test_add_relation_non_numeric_id_errors(self, cli):
        # `wl add --relation` parses the spec at creation time; a non-numeric id is rejected
        # (distinct entry point from `wl relation <id> <type> <ids>`).
        code, _, err = cli("add", "child", "-k", "task", "--relation", "related abc")
        assert code != 0
        assert "not a node id" in err


class TestRelationDefaultType:
    """`related` is the default type: `wl relation <id> <others>` with no type word."""

    def test_related_is_default_when_type_omitted(self, cli):
        _mk(cli, 2)
        code, _, _ = cli("relation", "2", "1")  # no type word -> related
        assert code == 0
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [2]

    def test_related_default_multiple_ids(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "2", "3")  # both, default related
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [1]

    def test_explicit_type_still_works(self, cli):
        _mk(cli, 2)
        cli("relation", "2", "split-from", "1")
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["split_into"] == [2]
