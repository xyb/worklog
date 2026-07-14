"""Tests for `wl relation ready` / `deps` / `unclaimed` — the query subcommands
built on the shared block-graph primitives in graph.py (`_block_graph`, `node_is_ready`,
`node_waiting_on`). `ready`/`deps` walk the BLOCK graph (relation.block edges), NOT the
task tree; `unclaimed`'s `<root>` scoping is the opposite — the TREE (parent_id
descendants), since a claim isn't a dependency-graph concept.
"""
import json

import pytest


def _mk(cli, n):
    for i in range(n):
        cli("add", f"task {i + 1}")


class TestReady:
    def test_isolated_node_is_ready_with_no_unlocked(self, cli):
        _mk(cli, 1)
        code, out, _ = cli("relation", "ready", "1")
        assert code == 0
        assert "ready" in out

    def test_ready_json_shape_for_isolated_node(self, cli):
        _mk(cli, 1)
        code, j, _ = cli("relation", "ready", "1", "-o", "json")
        assert code == 0
        d = json.loads(j)
        assert d["anchor"]["id"] == 1
        assert d["anchor"]["ready"] is True
        assert d["anchor"]["waiting_on"] == []
        assert d["unlocked"] == []

    def test_node_with_open_blocker_is_not_ready(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")   # #1 blocks #2
        code, j, _ = cli("relation", "ready", "2", "-o", "json")
        d = json.loads(j)
        assert d["anchor"]["ready"] is False
        assert d["anchor"]["waiting_on"] == [1]

    def test_ready_once_blocker_is_done(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        cli("done", "1")
        code, j, _ = cli("relation", "ready", "2", "-o", "json")
        d = json.loads(j)
        assert d["anchor"]["ready"] is True
        assert d["anchor"]["waiting_on"] == []

    def test_downstream_unlocked_after_finishing_anchor(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        cli("done", "1")
        code, j, _ = cli("relation", "ready", "1", "-o", "json")
        d = json.loads(j)
        assert [u["id"] for u in d["unlocked"]] == [2]

    def test_downstream_not_unlocked_while_anchor_still_open(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        code, j, _ = cli("relation", "ready", "1", "-o", "json")
        d = json.loads(j)
        assert d["unlocked"] == []

    def test_downstream_not_unlocked_if_another_blocker_still_open(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "block", "3")
        cli("relation", "2", "block", "3")
        cli("done", "1")   # only one of #3's two blockers is done
        code, j, _ = cli("relation", "ready", "1", "-o", "json")
        d = json.loads(j)
        assert d["unlocked"] == []   # #3 still waits on #2

    def test_transitive_downstream_unlocks_one_layer_at_a_time(self, cli):
        # A blocks B blocks C: finishing A only unlocks B (a done task isn't "ready to
        # work on" itself, and C still waits on the still-open B) — Kahn-style layered
        # unlock, not a cascade past an open node.
        _mk(cli, 3)
        cli("relation", "1", "block", "2")
        cli("relation", "2", "block", "3")
        cli("done", "1")
        code, j, _ = cli("relation", "ready", "1", "-o", "json")
        d = json.loads(j)
        assert [u["id"] for u in d["unlocked"]] == [2]
        # finishing B in turn unlocks C
        cli("done", "2")
        code, j, _ = cli("relation", "ready", "2", "-o", "json")
        d = json.loads(j)
        assert [u["id"] for u in d["unlocked"]] == [3]

    def test_chain_flag_includes_upstream(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "block", "2")
        cli("relation", "2", "block", "3")
        code, j, _ = cli("relation", "ready", "3", "--chain", "-o", "json")
        d = json.loads(j)
        assert sorted(c["id"] for c in d["chain"]) == [1, 2]

    def test_no_chain_key_without_flag(self, cli):
        _mk(cli, 1)
        code, j, _ = cli("relation", "ready", "1", "-o", "json")
        d = json.loads(j)
        assert "chain" not in d

    def test_ready_requires_existing_node(self, cli):
        code, _, err = cli("relation", "ready", "999")
        assert code != 0


class TestDeps:
    def test_deps_scoped_to_root_shows_transitive_downstream(self, cli):
        _mk(cli, 3)
        cli("relation", "1", "block", "2")
        cli("relation", "2", "block", "3")
        code, j, _ = cli("relation", "deps", "1", "-o", "json")
        d = json.loads(j)
        assert d["root"] == 1
        ids = {n["id"] for n in d["nodes"]}
        assert ids == {1, 2, 3}

    def test_deps_node_carries_its_own_block_list_and_ready_flag(self, cli):
        _mk(cli, 2)
        cli("relation", "1", "block", "2")
        code, j, _ = cli("relation", "deps", "1", "-o", "json")
        d = json.loads(j)
        by_id = {n["id"]: n for n in d["nodes"]}
        assert by_id[1]["blocks"] == [2]
        assert by_id[1]["ready"] is True     # #1 has no blocker of its own
        assert by_id[2]["ready"] is False    # #2 waits on #1

    def test_deps_global_only_includes_block_graph_participants(self, cli):
        _mk(cli, 3)   # #3 never touches block at all
        cli("relation", "1", "block", "2")
        code, j, _ = cli("relation", "deps", "-o", "json")
        d = json.loads(j)
        ids = {n["id"] for n in d["nodes"]}
        assert ids == {1, 2}
        assert 3 not in ids

    def test_deps_root_with_no_block_edges_shows_just_the_root(self, cli):
        _mk(cli, 1)
        code, out, _ = cli("relation", "deps", "1")
        assert code == 0
        assert "#1" in out and "ready" in out

    def test_deps_global_with_no_participants_reports_cleanly(self, cli):
        _mk(cli, 1)
        code, out, _ = cli("relation", "deps")
        assert code == 0
        assert "no block relations" in out

    def test_deps_requires_existing_root(self, cli):
        code, _, err = cli("relation", "deps", "999")
        assert code != 0


class TestUnclaimed:
    def test_lists_open_unclaimed_tickets(self, cli, monkeypatch):
        monkeypatch.setenv("WL_SESSION_ID", "s1")
        _mk(cli, 2)
        cli("relation", "claim", "1")
        code, j, _ = cli("relation", "unclaimed", "-o", "json")
        ids = {r["id"] for r in json.loads(j)}
        assert 1 not in ids
        assert 2 in ids

    def test_excludes_terminal_status_nodes(self, cli):
        _mk(cli, 1)
        cli("done", "1")
        code, j, _ = cli("relation", "unclaimed", "-o", "json")
        ids = {r["id"] for r in json.loads(j)}
        assert 1 not in ids

    def test_includes_stale_claim_as_unclaimed(self, cli, tmp_db, monkeypatch):
        from datetime import datetime, timedelta
        monkeypatch.setenv("WL_SESSION_ID", "s1")
        _mk(cli, 1)
        cli("relation", "claim", "1")
        con = tmp_db.db_connect()
        ts = (datetime.utcnow() - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
        con.execute("UPDATE prop SET value=? WHERE node_id=1 AND key='claimed_at'", (ts,))
        con.commit()
        code, j, _ = cli("relation", "unclaimed", "-o", "json")
        ids = {r["id"] for r in json.loads(j)}
        assert 1 in ids

    def test_root_scopes_to_tree_descendants(self, cli):
        cli("add", "project")            # #1
        cli("add", "child", "--parent", "1")   # #2
        cli("add", "unrelated")          # #3
        code, j, _ = cli("relation", "unclaimed", "1", "-o", "json")
        ids = {r["id"] for r in json.loads(j)}
        assert ids == {1, 2}
        assert 3 not in ids

    def test_root_requires_existing_node(self, cli):
        code, _, err = cli("relation", "unclaimed", "999")
        assert code != 0


class TestReadyChainRendering:
    """The TEXT rendering of `wl relation ready` — the unlocked list, the nothing-unlocked note,
    and the --chain upstream trail. The JSON contract is covered above; these are the lines a
    user actually reads, and they had no test."""

    def test_ready_lists_what_it_unlocked(self, cli):
        cli("add", "blocker")            # #1
        cli("add", "downstream")         # #2
        cli("relation", "1", "block", "2")
        cli("done", "1")                 # #1 settled → #2 becomes ready
        code, out, _ = cli("relation", "ready", "1")
        assert code == 0 and "unlocked:" in out and "#2" in out and "downstream" in out

    def test_ready_says_when_downstream_exists_but_none_are_ready(self, cli):
        cli("add", "blocker A")          # #1
        cli("add", "blocker B")          # #2
        cli("add", "downstream")         # #3, blocked by BOTH
        cli("relation", "1", "block", "3")
        cli("relation", "2", "block", "3")
        cli("done", "1")                 # only one blocker cleared → #3 still waiting
        code, out, _ = cli("relation", "ready", "1")
        assert code == 0 and "none are ready yet" in out

    def test_chain_shows_the_upstream_trail_with_done_marks(self, cli):
        cli("add", "first")              # #1
        cli("add", "second")             # #2
        cli("add", "third")              # #3
        cli("relation", "1", "block", "2")
        cli("relation", "2", "block", "3")
        cli("done", "1")
        code, out, _ = cli("relation", "ready", "3", "--chain")
        assert code == 0 and "upstream chain:" in out
        assert "✓" in out and "#1" in out        # settled upstream marked done
        assert "…" in out and "#2" in out        # still-open upstream marked pending
