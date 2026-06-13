"""Tests for structured goal targets: a goal log can carry, explicitly at write time,
the node ids it aims to deliver — stored as `goal` metrics (value_num = node id, metric order =
priority). No text parsing; it degrades to text-only when no ids are given. Week/month/year goals
are the same `goal` tag on the ancestor node (the level is the node's kind)."""
from datetime import date


def _day_id(con):
    today = date.today().isoformat()
    return con.execute("SELECT id FROM node WHERE kind='day' AND title LIKE ?",
                       (today + "%",)).fetchone()["id"]


def _goal_targets(con, node_id):
    """The goal-target node ids on a node's latest goal log, in stored (priority) order."""
    row = con.execute("SELECT id FROM log WHERE node_id=? AND tag='goal' AND deleted_at IS NULL "
                      "ORDER BY id DESC LIMIT 1", (node_id,)).fetchone()
    if not row:
        return []
    return [int(r["value_num"]) for r in con.execute(
        "SELECT value_num FROM metric WHERE log_id=? AND tag='goal' AND deleted_at IS NULL ORDER BY id",
        (row["id"],))]


class TestGoalTargets:
    def test_today_goal_stores_targets(self, cli, tmp_db):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "b", "-k", "task")          # #2
        cli("goal", "ship A and B", "1", "2")
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == [1, 2]

    def test_targets_are_priority_order(self, cli, tmp_db):
        cli("add", "a", "-k", "task")
        cli("add", "b", "-k", "task")
        cli("goal", "B first", "2", "1")
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == [2, 1]

    def test_no_targets_degrades_to_text(self, cli, tmp_db):
        cli("goal", "just wrap up, nothing specific")
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == []

    def test_targets_track_latest_goal(self, cli, tmp_db):
        cli("add", "a", "-k", "task")
        cli("add", "b", "-k", "task")
        cli("goal", "do A", "1")
        cli("goal", "changed: B", "2")         # new goal log → its own metrics; latest wins
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == [2]

    def test_goal_set_on_any_node_stores_targets(self, cli, tmp_db):
        cli("add", "m", "-k", "month")         # #1
        cli("add", "a", "-k", "task")          # #2
        cli("add", "b", "-k", "task")          # #3
        cli("goal", "set", "1", "month plan", "3", "2")   # month goal → targets [3,2]
        con = tmp_db.db_connect()
        assert _goal_targets(con, 1) == [3, 2]

    def test_nonexistent_target_rejected(self, cli):
        code, _, err = cli("goal", "ship it", "999")
        assert code != 0 and "not found" in err

    def test_summary_rejects_targets(self, cli):
        cli("add", "d", "-k", "day")
        code, _, err = cli("goal", "set", "1", "recap", "1", "--summary")
        assert code != 0      # target ids don't apply to a summary

    def test_set_shortcut_stores_prose_only(self, cli, tmp_db):
        # `wl set <node> goal "..."` is the prose-only key-routed path (no structured targets)
        cli("add", "a", "-k", "task")          # #1
        cli("add", "d", "-k", "day")           # #2
        cli("set", "2", "goal", "deliver A")
        con = tmp_db.db_connect()
        assert _goal_targets(con, 2) == []

    def test_reverse_query_by_metric(self, cli, tmp_db):
        # which goals target node #1? scan the metric table directly (tag=goal, value_num=id)
        cli("add", "a", "-k", "task")          # #1
        cli("goal", "deliver A", "1")
        con = tmp_db.db_connect()
        rows = con.execute("SELECT node_id FROM metric WHERE tag='goal' AND value_num=1 "
                           "AND deleted_at IS NULL").fetchall()
        assert _day_id(con) in [r["node_id"] for r in rows]


class TestGoalIdHint:
    """We never parse #ids from the prose into storage, but if the goal text NAMES live nodes that
    aren't structured targets yet, we print TWO ready-to-run lines: append to the goal now, or the
    one-shot form for next time."""

    def test_hint_offers_two_commands(self, cli, tmp_db):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "b", "-k", "task")          # #2
        _, out, _ = cli("goal", "ship #1 and draft #2")
        assert "💡" in out
        # the one-shot form (next time) — stable, copy-paste-runnable
        assert 'next time: wl goal "ship #1 and draft #2" 1 2' in out
        # the set-ids form references the real day node id + --ids
        con = tmp_db.db_connect()
        assert f"set ids:   wl goal set {_day_id(con)} --ids 1 2" in out

    def test_hint_only_for_unstructured_ids(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "b", "-k", "task")          # #2
        _, out, _ = cli("goal", "ship #1 and draft #2", "1")  # #1 already structured
        assert 'next time: wl goal "ship #1 and draft #2" 2' in out  # only #2 suggested

    def test_no_hint_when_all_structured(self, cli):
        cli("add", "a", "-k", "task")          # #1
        _, out, _ = cli("goal", "ship #1", "1")
        assert "💡" not in out

    def test_hint_skips_pr_refs_and_dead_ids(self, cli):
        _, out, _ = cli("goal", "merge PR#9 and ship #999")   # PR#9 glued; #999 doesn't exist
        assert "💡" not in out

    def test_hint_on_goal_set(self, cli):
        cli("add", "m", "-k", "month")         # #1
        cli("add", "a", "-k", "task")          # #2
        _, out, _ = cli("goal", "set", "1", "deliver #2")
        assert 'next time: wl goal set 1 "deliver #2" 2' in out
        assert "set ids:   wl goal set 1 --ids 2" in out

    def test_no_hint_for_summary(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "d", "-k", "task")          # #2  (a node that can take a summary)
        _, out, _ = cli("goal", "set", "2", "shipped #1", "--summary")
        assert "💡" not in out


class TestGoalSetTargets:
    """`wl goal set <node> --ids <ids>` SETS the node's existing goal targets wholesale — no new
    log, no re-typing the text (the text is the complete goal, so the ids are the complete set)."""

    def test_ids_sets_targets_on_existing_goal(self, cli, tmp_db):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "b", "-k", "task")          # #2
        cli("goal", "ship #1 and draft #2")    # text-only goal on today's day node
        con = tmp_db.db_connect()
        day = _day_id(con)
        cli("goal", "set", str(day), "--ids", "1", "2")
        con = tmp_db.db_connect()
        # one goal log (no duplicate), both targets set in order
        assert con.execute("SELECT COUNT(*) FROM log WHERE node_id=? AND tag='goal'", (day,)).fetchone()[0] == 1
        assert _goal_targets(con, day) == [1, 2]

    def test_ids_replaces_not_appends(self, cli, tmp_db):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "b", "-k", "task")          # #2
        cli("add", "c", "-k", "task")          # #3
        cli("goal", "first plan")
        con = tmp_db.db_connect()
        day = _day_id(con)
        cli("goal", "set", str(day), "--ids", "1", "2")
        cli("goal", "set", str(day), "--ids", "3")             # wholesale replace, not append
        con = tmp_db.db_connect()
        assert _goal_targets(con, day) == [3]

    def test_ids_idempotent(self, cli, tmp_db):
        cli("add", "a", "-k", "task")          # #1
        cli("goal", "ship #1")
        con = tmp_db.db_connect()
        day = _day_id(con)
        cli("goal", "set", str(day), "--ids", "1")
        _, out, _ = cli("goal", "set", str(day), "--ids", "1")   # again
        assert "already" in out
        con = tmp_db.db_connect()
        assert _goal_targets(con, day) == [1]

    def test_ids_without_goal_errors(self, cli):
        cli("add", "d", "-k", "day")           # #1, no goal written
        code, _, err = cli("goal", "set", "1", "--ids", "1")
        assert code != 0 and "no goal yet" in err

    def test_ids_rejects_value_and_ids_together(self, cli):
        cli("add", "d", "-k", "day")           # #1
        code, _, err = cli("goal", "set", "1", "some text", "--ids", "1")
        assert code != 0 and "not both" in err
