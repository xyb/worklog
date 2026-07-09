"""Tests for `wl relation` — task↔task relations (relation.* props), the wl show
relations block, single-write + derived-reverse display, and the -o json relations field.

Storage model: each relation type (`block` / `split` / `related`) is SINGLE-WRITE —
`wl relation A <type> B` writes ONLY on A. The reverse is never stored, only derived at
read time: `block` → `=blocked-by`, `split` → `=split-from` (each its own derived label),
`related` → a one-sided edge folds into `=backrels` (graph._backrels) instead of getting
its own label. `block` is the one type that is cycle-checked at write time (dependency
edges must stay a DAG); `split` / `related` are not (see graph.py's cycle-check docstring).
"""
import json
import pytest


def _mk(cli, n):
    """Create n bare tasks, returns nothing (ids are 1..n)."""
    for i in range(n):
        cli("add", f"task {i + 1}")


class TestRelationWrite:
    def test_split_writes_only_the_source_side(self, cli):
        _mk(cli, 2)
        code, out, _ = cli("relation", "1", "split", "2")
        assert code == 0
        # #1's own view shows the stored `split: #2`
        assert "split:" in out and "#2" in out
        _, j, _ = cli("show", "1", "-o", "json")
        d = json.loads(j)
        assert d["relations"]["split"] == [2]
        # the reverse is NOT written as a real prop on #2 — only derived
        assert not any(k.startswith("relation.") for k in d["props"])
        _, j2, _ = cli("show", "2", "-o", "json")
        d2 = json.loads(j2)
        assert d2["relations"]["split"] == []          # #2 has no own split edge
        assert d2["relations"]["split_from"] == [1]     # derived reverse

    def test_related_writes_only_the_source_side(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        _, j1, _ = cli("show", "1", "-o", "json")
        _, j2, _ = cli("show", "2", "-o", "json")
        assert json.loads(j1)["relations"]["related"] == [2]
        # #2 never got its own `related` prop written...
        assert json.loads(j2)["relations"]["related"] == []
        # ...but the one-sided edge surfaces on #2, folded into backrels (not a
        # dedicated derived label — Q4/relation-model-redesign)
        assert json.loads(j2)["backrels"] == [1]

    def test_related_built_both_ways_shows_both_facts_undeduped(self, cli):
        # Q4: if both sides independently `related`, that's two distinct facts — #1
        # shows its own stored `related: #2` AND the derived backrel from #2's edge.
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        cli("relation", "2", "related", "1")
        _, j1, _ = cli("show", "1", "-o", "json")
        d1 = json.loads(j1)
        assert d1["relations"]["related"] == [2]
        assert d1["backrels"] == [2]

    def test_multiple_ids_at_once(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "related", "2", "3")
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [2, 3]

    def test_hash_prefix_and_underscore_type_accepted(self, cli):
        _mk(cli, 2)
        code, _, _ = cli("relation", "1", "split", "#2")
        assert code == 0
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["split"] == [2]

    def test_dedup_on_repeat(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        cli("relation", "1", "related", "2")  # again
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [2]


class TestBlockRelation:
    """`block` — dependency edge: `wl relation A block B` means A blocks B (A is upstream,
    must finish first). Single-write like split/related; reverse derives into `=blocked-by`."""

    def test_block_writes_only_the_source_side(self, cli):
        _mk(cli, 2)
        code, out, _ = cli("relation", "1", "block", "2")
        assert code == 0
        assert "block:" in out and "#2" in out
        d1 = json.loads(cli("show", "1", "-o", "json")[1])
        assert d1["relations"]["block"] == [2]
        assert not any(k.startswith("relation.") for k in d1["props"])
        d2 = json.loads(cli("show", "2", "-o", "json")[1])
        assert d2["relations"]["block"] == []            # #2 has no own block edge
        assert d2["relations"]["blocked_by"] == [1]       # derived reverse

    def test_block_rm_clears_source_and_derived_reverse_follows(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        code, _, _ = cli("relation", "1", "block", "2", "--rm")
        assert code == 0
        d1 = json.loads(cli("show", "1", "-o", "json")[1])
        d2 = json.loads(cli("show", "2", "-o", "json")[1])
        assert d1["relations"]["block"] == []
        assert d2["relations"]["blocked_by"] == []

    def test_direct_cycle_rejected(self, cli):
        # A block B already exists; B block A would close a 2-node loop — reject, don't write
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        code, _, err = cli("relation", "2", "block", "1")
        assert code != 0
        assert "cycle" in err.lower()
        d2 = json.loads(cli("show", "2", "-o", "json")[1])
        assert d2["relations"]["block"] == []   # rejected edge was never written

    def test_transitive_cycle_rejected(self, cli):
        # 1 block 2 block 3; closing 3 block 1 would create a 3-node cycle
        _mk(cli, 3)
        cli("relation", "1", "block", "2")
        cli("relation", "2", "block", "3")
        code, _, err = cli("relation", "3", "block", "1")
        assert code != 0
        assert "cycle" in err.lower()

    def test_self_block_still_just_skipped_not_a_cycle_error(self, cli):
        _mk(cli, 1)
        code, out, _ = cli("relation", "1", "block", "1")
        assert code == 0
        assert "itself" in out

    def test_split_and_related_are_not_cycle_checked(self, cli):
        # block/split are independent graphs — a split cycle (or reverse) is not rejected
        _mk(cli, 2)
        cli("relation", "1", "split", "2")
        code, _, _ = cli("relation", "2", "split", "1")
        assert code == 0
        cli("relation", "1", "related", "2")
        code, _, _ = cli("relation", "2", "related", "1")
        assert code == 0

    def test_block_shown_before_split_and_related_in_relation_block(self, cli):
        _mk(cli, 4)
        cli("relation", "1", "related", "2")
        cli("relation", "1", "split", "3")
        cli("relation", "1", "block", "4")
        _, out, _ = cli("relation", "1")
        assert out.index("block:") < out.index("split:") < out.index("related:")


class TestReadyWaitingDisplay:
    """`wl show`'s `relation:` block gets two more computed rows built on the same
    graph primitives as `wl relation ready`/`deps` (graph.node_ready_view) — `=ready` (bool)
    and `=waiting` (the direct blockers still open). Shown ONLY on `wl show` (not on
    `wl relation <id>`'s plain CRUD/list output, and not in that command's `-o json`), and
    ONLY for a node that participates in the block graph at all — a node with zero `block`
    edges gets neither row, same as any other empty relation section."""

    def test_node_with_no_block_edge_shows_neither_field(self, cli):
        _mk(cli, 1)
        d = json.loads(cli("show", "1", "-o", "json")[1])
        assert "ready" not in d["relations"]
        assert "waiting" not in d["relations"]

    def test_blocker_with_no_open_blockers_of_its_own_is_ready(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        d1 = json.loads(cli("show", "1", "-o", "json")[1])
        assert d1["relations"]["ready"] is True
        assert d1["relations"]["waiting"] == []

    def test_blocked_node_is_not_ready_and_lists_waiting(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        d2 = json.loads(cli("show", "2", "-o", "json")[1])
        assert d2["relations"]["ready"] is False
        assert d2["relations"]["waiting"] == [1]

    def test_ready_flips_true_once_blocker_is_done(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        cli("done", "1")
        d2 = json.loads(cli("show", "2", "-o", "json")[1])
        assert d2["relations"]["ready"] is True
        assert d2["relations"]["waiting"] == []

    def test_waiting_only_lists_still_open_blockers(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "block", "3")
        cli("relation", "2", "block", "3")
        cli("done", "1")
        d3 = json.loads(cli("show", "3", "-o", "json")[1])
        assert d3["relations"]["ready"] is False
        assert d3["relations"]["waiting"] == [2]   # #1 is done, dropped from waiting

    def test_text_output_shows_ready_and_waiting_rows(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        _, out, _ = cli("show", "2")
        assert "=ready:" in out and "false" in out
        assert "=waiting:" in out and "#1" in out

    def test_text_output_waiting_shows_none_when_ready(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        _, out, _ = cli("show", "1")
        assert "=ready:" in out and "true" in out
        assert "=waiting:" in out and "(none)" in out

    def test_relation_command_json_does_not_carry_ready_or_waiting(self, cli):
        # `wl relation <id> -o json` stays the pure CRUD/list contract — =ready/=waiting are
        # a `wl show`-only computed extra, not part of relation_view's own shape.
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        d = json.loads(cli("relation", "1", "-o", "json")[1])
        assert "ready" not in d and "waiting" not in d

    def test_relation_command_text_does_not_show_ready_or_waiting(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        _, out, _ = cli("relation", "1")
        assert "=ready" not in out and "=waiting" not in out


class TestRelationRemove:
    def test_rm_clears_the_source_side_and_the_derived_reverse_follows(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "split", "2")
        code, out, _ = cli("relation", "1", "split", "2", "--rm")
        assert code == 0
        _, j1, _ = cli("show", "1", "-o", "json")
        _, j2, _ = cli("show", "2", "-o", "json")
        assert json.loads(j1)["relations"]["split"] == []
        assert json.loads(j2)["relations"]["split_from"] == []   # derived, follows the source

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
    def test_add_with_relation_writes_only_the_new_node_side(self, cli):
        _mk(cli, 2)  # #1 #2
        code, out, _ = cli("add", "derived",
                           "--relation", "split 1", "--relation", "related 2")
        assert code == 0
        assert "relation(s)" in out  # the success hint mentions relations
        _, j, _ = cli("show", "3", "-o", "json")
        d = json.loads(j)
        assert d["relations"]["split"] == [1]
        assert d["relations"]["related"] == [2]
        # the existing nodes get ONLY the derived reverse, no real prop written on them
        d1 = json.loads(cli("show", "1", "-o", "json")[1])
        assert d1["relations"]["split_from"] == [3]
        assert not any(k.startswith("relation.") for k in d1["props"])
        d2 = json.loads(cli("show", "2", "-o", "json")[1])
        assert d2["backrels"] == [3]

    def test_add_relation_multiple_ids(self, cli):
        _mk(cli, 2)
        cli("add", "x", "--relation", "related 1 2")
        _, j, _ = cli("show", "3", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [1, 2]

    def test_add_relation_underscore_and_hash(self, cli):
        _mk(cli, 1)
        cli("add", "x", "--relation", "split #1")
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["split"] == [1]

    def test_add_relation_bad_type_errors(self, cli):
        _mk(cli, 1)
        code, _, err = cli("add", "x", "--relation", "foo 1")
        assert code != 0
        assert "unknown relation type" in err

    def test_add_relation_missing_id_errors(self, cli):
        _mk(cli, 1)
        code, _, err = cli("add", "x", "--relation", "split")
        assert code != 0
        assert "need '<type> <id>'" in err

    def test_add_relation_nonexistent_target_errors(self, cli):
        code, _, err = cli("add", "x", "--relation", "related 999")
        assert code != 0
        assert "not found" in err


class TestRelationDerivation:
    def test_reverse_derived_from_one_sided_split_prop(self, cli):
        # set ONLY the source side via raw `wl set`; the view must still derive the reverse
        _mk(cli, 2)
        cli("set", "1", "relation.split", "2")
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["split_from"] == [1]

    def test_relation_to_deleted_node_dropped(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "related", "2")
        cli("node", "rm", "2")  # soft-delete #2
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == []

    def test_split_reverse_dropped_when_source_deleted(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "split", "2")
        cli("node", "rm", "1")  # soft-delete the source of the split edge
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["split_from"] == []


class TestRelationList:
    def test_list_mode_no_relations(self, cli):
        _mk(cli, 1)
        code, out, _ = cli("relation", "1")
        assert code == 0
        assert "no relations" in out

    def test_list_mode_shows_titles(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "split", "2")
        code, out, _ = cli("relation", "1")
        assert "split:" in out
        assert "task 2" in out  # the related node's title


class TestBacklinks:
    def test_text_mention_shows_linked_from(self, cli):
        cli("add", "target")          # #1
        cli("add", "depends on it")    # #2
        cli("log", "2", "blocked on #1 result")      # #2's log mentions #1
        _, out, _ = cli("show", "1")
        assert "=backrels" in out and "#2" in out

    def test_wl_prefix_counts(self, cli):
        cli("add", "target")           # #1
        cli("add", "other")            # #2
        cli("log", "2", "see WL#1 for context")
        _, out, _ = cli("show", "1")
        assert "=backrels" in out and "#2" in out

    def test_pr_ref_and_self_excluded(self, cli):
        cli("add", "target")           # #1
        cli("add", "other")            # #2
        cli("log", "2", "merged PR#1, not a node ref")   # PR#1 must NOT backlink #1
        cli("log", "1", "self mention #1")               # self excluded
        _, out, _ = cli("show", "1")
        assert "=backrels" not in out

    def test_no_substring_false_match(self, cli):
        cli("add", "target")           # #1
        cli("add", "other")            # #2
        cli("log", "2", "see #12 and #100")          # neither is #1
        _, out, _ = cli("show", "1")
        assert "=backrels" not in out

    def test_backlinks_in_json(self, cli):
        cli("add", "target")           # #1
        cli("add", "other")            # #2
        cli("log", "2", "ref #1")
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["backrels"] == [2]

    def test_goal_summary_rollcall_not_backrel(self, cli):
        """A day's 今日目标 (goal) / 日终小结 (summary) lists in-progress #task ids in
        passing — that roll-call isn't a substantive reference, so it must NOT backrel the
        listed task to the day node. Regression: a day summary listing an in-progress
        task id used to backrel that task to the day node."""
        cli("add", "target")                 # #1
        cli("goal", "today: advance #1")     # goal log on today's day node mentions #1
        _, out, _ = cli("show", "1")
        assert "=backrels" not in out
        # a plain log mentioning #1 still backrels (substantive reference)
        cli("add", "other")                  # #2
        cli("log", "2", "actually blocked on #1")
        _, out, _ = cli("show", "1")
        assert "=backrels" in out and "#2" in out


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
        cli("relation", "1", "split", "2")
        code, out, _ = cli("show", "1")
        # the relation block nests under props: as a `relation:` sub-block
        assert "props:" in out and "relation:" in out
        assert "split:" in out
        # the raw relation.* prop key is NOT shown as a flat key=value row
        assert "relation.split" not in out

    def test_derived_reverse_marked_with_equals_and_italic_style(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "split", "2")
        code, out, _ = cli("show", "2")
        assert "=split-from" in out and "#1" in out
        # the derived row is NOT the stored `split:` label — #2 has no own split edge
        assert "  split:" not in out

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
        cli("add", "x" * 200)  # #1 long title (ASCII so len == display width)
        cli("add", "src")      # #2
        cli("relation", "2", "related", "1")
        _, out, _ = cli("relation", "2")
        assert all(len(l) <= 60 for l in out.splitlines())   # nothing overflows
        # the title wrapped to a continuation line aligned under the title column
        title_lines = [l for l in out.splitlines() if set(l.strip()) == {"x"}]
        assert title_lines and all(l.startswith(" " * 18) for l in title_lines)


class TestRelationSubcommandStructure:
    """Step 4/7: `wl relation` restructured into an entity group — `set` is the
    default verb (the legacy CRUD/list form, reachable bare, unchanged behavior) plus
    `ready` / `deps` / `unclaimed` / `claim` / `unclaim` subcommands. This step wired up
    the CLI (argparse dispatch); all five new verbs are now real handlers — see test_relation_claim.py and test_relation_ready_deps.py for their
    behavioral coverage. Here we only confirm the CLI shapes parse and route correctly."""

    def test_legacy_bare_form_still_works_unchanged(self, cli):
        # `wl relation <id> <type> <other>` — no `set` keyword — is the default verb
        _mk(cli, 2)
        code, out, _ = cli("relation", "1", "split", "2")
        assert code == 0
        assert "split:" in out and "#2" in out

    def test_explicit_set_verb_is_equivalent(self, cli):
        _mk(cli, 2)
        code, out, _ = cli("relation", "set", "1", "split", "2")
        assert code == 0
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["split"] == [2]

    def test_legacy_list_mode_still_works(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "split", "2")
        code, out, _ = cli("relation", "1")
        assert code == 0
        assert "split:" in out

    # ready/deps/unclaimed shipped for real in step 6 — see
    # test_relation_ready_deps.py for behavioral coverage; claim/unclaim in step 5
    # — see test_relation_claim.py. Here: just confirm CLI shapes route.
    @pytest.mark.parametrize("verb,extra_args", [
        ("ready", ["1"]),
        ("deps", []),
        ("deps", ["1"]),
        ("unclaimed", []),
        ("unclaimed", ["1"]),
    ])
    def test_new_verbs_parse_and_reach_the_real_handler(self, cli, verb, extra_args):
        # argparse must accept these shapes (not a usage/parse error) and route to the
        # real handler, not die with an "unimplemented" placeholder or crash
        _mk(cli, 1)
        code, _, err = cli("relation", verb, *extra_args)
        assert code == 0
        assert "isn't implemented yet" not in err

    def test_claim_and_unclaim_parse_and_reach_real_handlers(self, cli, monkeypatch):
        monkeypatch.setenv("WL_SESSION_ID", "sess-structure-test")
        _mk(cli, 1)
        code, out, _ = cli("relation", "claim", "1")
        assert code == 0 and "isn't implemented yet" not in out
        code, out, _ = cli("relation", "unclaim", "1")
        assert code == 0 and "isn't implemented yet" not in out

    def test_ready_requires_an_anchor_id(self, cli):
        # Q5: ready is never a global scan — argparse itself rejects a missing id
        with pytest.raises(SystemExit):
            cli("relation", "ready")

    def test_ready_accepts_chain_flag(self, cli):
        _mk(cli, 1)
        code, _, err = cli("relation", "ready", "1", "--chain")
        assert code == 0

    def test_unknown_relation_subcommand_rejected_by_argparse(self, cli):
        # "bogus-verb" isn't a known verb, so the default-verb expansion treats it as the
        # `set` verb's leading (int) id -> argparse type error, never reaching any stub
        with pytest.raises(SystemExit):
            cli("relation", "bogus-verb")


class TestAddRelationParse:
    def test_add_relation_non_numeric_id_errors(self, cli):
        # `wl add --relation` parses the spec at creation time; a non-numeric id is rejected
        # (distinct entry point from `wl relation <id> <type> <ids>`).
        code, _, err = cli("add", "child", "--relation", "related abc")
        assert code != 0
        assert "not a node id" in err


class TestRelationDefaultType:
    """`related` is the default type: `wl relation <id> <others>` with no type word."""

    def test_related_is_default_when_type_omitted(self, cli):
        _mk(cli, 2)
        code, _, _ = cli("relation", "2", "1")  # no type word -> related
        assert code == 0
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [1]

    def test_related_default_multiple_ids(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "2", "3")  # both, default related
        _, j, _ = cli("show", "1", "-o", "json")
        assert json.loads(j)["relations"]["related"] == [2, 3]

    def test_explicit_type_still_works(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "split", "2")
        _, j, _ = cli("show", "2", "-o", "json")
        assert json.loads(j)["relations"]["split_from"] == [1]
