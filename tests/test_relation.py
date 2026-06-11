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
        assert "relations:" in out
        assert "split-from:" in out
        # the raw relation.* prop is NOT shown in the props block
        assert "relation.split_from" not in out
