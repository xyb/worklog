"""Tests for structured goal targets: a goal log can carry, explicitly at write time,
the node ids it aims to deliver — stored as `goal` metrics (value_num = node id, metric order =
priority). No text parsing; it degrades to text-only when no ids are given. Week/month/year goals
are the same `goal` tag on the ancestor node (the level is the node's type)."""
from datetime import date


def _day_id(con):
    today = date.today().isoformat()
    return con.execute(
        "SELECT n.id FROM node n WHERE n.deleted_at IS NULL AND n.title LIKE ? "
        "AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.date' "
        "AND value='day' AND deleted_at IS NULL) ORDER BY n.id LIMIT 1",
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
        cli("add", "a")          # #1
        cli("add", "b")          # #2
        cli("goal", "ship A and B", "1", "2")
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == [1, 2]

    def test_targets_are_priority_order(self, cli, tmp_db):
        cli("add", "a")
        cli("add", "b")
        cli("goal", "B first", "2", "1")
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == [2, 1]

    def test_no_targets_degrades_to_text(self, cli, tmp_db):
        cli("goal", "just wrap up, nothing specific")
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == []

    def test_targets_track_latest_goal(self, cli, tmp_db):
        cli("add", "a")
        cli("add", "b")
        cli("goal", "do A", "1")
        cli("goal", "changed: B", "2")         # new goal log → its own metrics; latest wins
        con = tmp_db.db_connect()
        assert _goal_targets(con, _day_id(con)) == [2]

    def test_goal_set_on_any_node_stores_targets(self, cli, tmp_db):
        cli("add", "m", "--prop", "type.date=month")         # #1
        cli("add", "a")          # #2
        cli("add", "b")          # #3
        cli("goal", "set", "1", "month plan", "3", "2")   # month goal → targets [3,2]
        con = tmp_db.db_connect()
        assert _goal_targets(con, 1) == [3, 2]

    def test_nonexistent_target_rejected(self, cli):
        code, _, err = cli("goal", "ship it", "999")
        assert code != 0 and "not found" in err

    def test_summary_rejects_targets(self, cli):
        cli("add", "d", "--prop", "type.date=day")
        code, _, err = cli("goal", "set", "1", "recap", "1", "--summary")
        assert code != 0      # target ids don't apply to a summary

    def test_set_shortcut_stores_prose_only(self, cli, tmp_db):
        # `wl set <node> goal "..."` is the prose-only key-routed path (no structured targets)
        cli("add", "a")          # #1
        cli("add", "d", "--prop", "type.date=day")           # #2
        cli("set", "2", "goal", "deliver A")
        con = tmp_db.db_connect()
        assert _goal_targets(con, 2) == []

    def test_reverse_query_by_metric(self, cli, tmp_db):
        # which goals target node #1? scan the metric table directly (tag=goal, value_num=id)
        cli("add", "a")          # #1
        cli("goal", "deliver A", "1")
        con = tmp_db.db_connect()
        rows = con.execute("SELECT node_id FROM metric WHERE tag='goal' AND value_num=1 "
                           "AND deleted_at IS NULL").fetchall()
        assert _day_id(con) in [r["node_id"] for r in rows]

    def test_targets_surface_consistently(self, cli):
        # the structured targets must show in wl goal, wl day (text), and -o json — aligned
        import json
        cli("add", "a")          # #1
        cli("add", "b")          # #2
        cli("done", "1")                        # #1 settled
        cli("goal", "ship A and B", "1", "2")
        _, g, _ = cli("goal")                   # wl goal read
        assert "[1/2]" in g and "[x] " in g and "#1" in g and "#2" in g
        _, d, _ = cli("day")                    # wl day text
        assert "1. " in d and "2. " in d and "#1" in d and "#2" in d
        _, j, _ = cli("day", "-o", "json")      # json
        dd = json.loads(j)
        assert [t["id"] for t in dd["goal_targets"]] == [1, 2]
        assert dd["goal_targets"][0]["status"] == "DONE"
        assert dd["goal_progress"] == {"done": 1, "total": 2}


class TestGoalIdHint:
    """We never parse #ids from the prose into storage, but if the goal text NAMES live nodes that
    aren't structured targets yet, we print TWO ready-to-run commands, each alone on its own line
    (copy-paste-able): set them on the goal now, or the one-shot form for next time."""

    def test_hint_offers_two_commands(self, cli, tmp_db):
        cli("add", "a")          # #1
        cli("add", "b")          # #2
        _, out, _ = cli("goal", "ship #1 and draft #2")
        assert "💡" in out
        # each command on its own line (no label prefix) — copy-paste-runnable
        assert '\n  wl goal "ship #1 and draft #2" 1 2' in out
        con = tmp_db.db_connect()
        assert f'\n  wl goal set {_day_id(con)} --ids 1 2' in out

    def test_hint_only_for_unstructured_ids(self, cli):
        cli("add", "a")          # #1
        cli("add", "b")          # #2
        _, out, _ = cli("goal", "ship #1 and draft #2", "1")  # #1 already structured
        assert '\n  wl goal "ship #1 and draft #2" 2' in out  # only #2 suggested

    def test_no_hint_when_all_structured(self, cli):
        cli("add", "a")          # #1
        _, out, _ = cli("goal", "ship #1", "1")
        assert "💡" not in out                 # goal already has its target → silent

    def test_nudge_when_no_targets(self, cli, tmp_db):
        # a prose-only goal with no ids at all still nudges toward linking target nodes
        _, out, _ = cli("goal", "wrap up loose ends")
        con = tmp_db.db_connect()
        assert "no target nodes" in out
        assert f"wl goal set {_day_id(con)} --ids <id" in out

    def test_pr_and_dead_ids_get_only_the_generic_nudge(self, cli):
        # PR#9 (glued) and #999 (dead) aren't real targets → no concrete set-ids command,
        # but the goal has no targets so the generic nudge still shows
        _, out, _ = cli("goal", "merge PR#9 and ship #999")
        assert "set them on this goal" not in out   # no concrete id command for PR#9/#999
        assert "no target nodes" in out             # generic nudge instead

    def test_hint_on_goal_set(self, cli):
        cli("add", "m", "--prop", "type.date=month")         # #1
        cli("add", "a")          # #2
        _, out, _ = cli("goal", "set", "1", "deliver #2")
        # each command alone on its own line (copy-paste-able)
        assert '\n  wl goal set 1 "deliver #2" 2' in out
        assert "\n  wl goal set 1 --ids 2" in out

    def test_no_hint_for_summary(self, cli):
        cli("add", "a")          # #1
        cli("add", "d")          # #2  (a node that can take a summary)
        _, out, _ = cli("goal", "set", "2", "shipped #1", "--summary")
        assert "💡" not in out


class TestGoalSetTargets:
    """`wl goal set <node> --ids <ids>` SETS the node's existing goal targets wholesale — no new
    log, no re-typing the text (the text is the complete goal, so the ids are the complete set)."""

    def test_ids_sets_targets_on_existing_goal(self, cli, tmp_db):
        cli("add", "a")          # #1
        cli("add", "b")          # #2
        cli("goal", "ship #1 and draft #2")    # text-only goal on today's day node
        con = tmp_db.db_connect()
        day = _day_id(con)
        cli("goal", "set", str(day), "--ids", "1", "2")
        con = tmp_db.db_connect()
        # one goal log (no duplicate), both targets set in order
        assert con.execute("SELECT COUNT(*) FROM log WHERE node_id=? AND tag='goal'", (day,)).fetchone()[0] == 1
        assert _goal_targets(con, day) == [1, 2]

    def test_ids_replaces_not_appends(self, cli, tmp_db):
        cli("add", "a")          # #1
        cli("add", "b")          # #2
        cli("add", "c")          # #3
        cli("goal", "first plan")
        con = tmp_db.db_connect()
        day = _day_id(con)
        cli("goal", "set", str(day), "--ids", "1", "2")
        cli("goal", "set", str(day), "--ids", "3")             # wholesale replace, not append
        con = tmp_db.db_connect()
        assert _goal_targets(con, day) == [3]

    def test_ids_idempotent(self, cli, tmp_db):
        cli("add", "a")          # #1
        cli("goal", "ship #1")
        con = tmp_db.db_connect()
        day = _day_id(con)
        cli("goal", "set", str(day), "--ids", "1")
        _, out, _ = cli("goal", "set", str(day), "--ids", "1")   # again
        assert "already" in out
        con = tmp_db.db_connect()
        assert _goal_targets(con, day) == [1]

    def test_ids_without_goal_errors(self, cli):
        cli("add", "d", "--prop", "type.date=day")           # #1, no goal written
        code, _, err = cli("goal", "set", "1", "--ids", "1")
        assert code != 0 and "no goal yet" in err

    def test_ids_rejects_value_and_ids_together(self, cli):
        cli("add", "d", "--prop", "type.date=day")           # #1
        code, _, err = cli("goal", "set", "1", "some text", "--ids", "1")
        assert code != 0 and "not both" in err


class TestRecurringGoalTargets:
    """A recurring target settles by CHECK-IN, not status: `wl tick` leaves the status at TODO
    (`wl done` would retire the whole recurrence), so reading status alone left every recurring
    target stuck at [ ] forever. The goal period bounds which check-ins count."""

    def test_ticked_recurring_target_counts_as_done(self, cli, tmp_db):
        cli("add", "weekly plan")                            # #1
        cli("sched", "1", "--recur", "daily")
        cli("goal", "run the weekly plan", "1")
        cli("tick", "1")                                     # check-in, status stays TODO
        con = tmp_db.db_connect()
        assert con.execute("SELECT status FROM node WHERE id=1").fetchone()["status"] == "TODO"
        _, g, _ = cli("goal")
        assert "[1/1] ✅" in g and "[x] " in g
        _, d, _ = cli("day")
        assert "1. [x] #1" in d

    def test_unticked_recurring_target_stays_open(self, cli):
        cli("add", "weekly plan")                            # #1
        cli("sched", "1", "--recur", "daily")
        cli("goal", "run the weekly plan", "1")
        _, g, _ = cli("goal")
        assert "[0/1] ⬜" in g and "1. [ ] #1" in g

    def test_checkin_outside_the_goal_period_does_not_count(self, cli):
        # a check-in on another day is not today's delivery
        cli("add", "weekly plan")                            # #1
        cli("sched", "1", "--recur", "daily")
        cli("goal", "run the weekly plan", "1")
        cli("metric", "add", "1", "checkin", "--at", "-7d")  # last week's check-in
        _, g, _ = cli("goal")
        assert "[0/1] ⬜" in g

    def test_week_goal_counts_a_checkin_anywhere_in_the_week(self, cli):
        cli("add", "weekly plan")                            # #1
        cli("sched", "1", "--recur", "weekly:Mon")
        cli("tick", "1")
        week = date.today().strftime("%G-W%V")
        cli("add", week, "--prop", "type.date=week")         # #2, the week time node
        cli("goal", "set", "2", "keep the weekly plan going")
        cli("goal", "set", "2", "--ids", "1")
        _, g, _ = cli("goal", "ls", "2")
        assert "[x] #1" in g                                 # the Mon tick settles the week goal

    def test_one_off_target_still_reads_status(self, cli):
        # a plain task has no recurrence: a check-in must NOT settle it — only DONE/CANCELED do
        cli("add", "a")          # #1
        cli("add", "b")          # #2
        cli("goal", "ship A and B", "1", "2")
        cli("tick", "1")                                     # log + check-in, but still TODO
        _, g, _ = cli("goal")
        assert "[0/2] ⬜" in g
        cli("done", "1")
        cli("cancel", "2")
        _, g, _ = cli("goal")
        assert "[2/2] ✅" in g

    def test_recurring_target_done_in_json(self, cli):
        import json
        cli("add", "weekly plan")                            # #1
        cli("sched", "1", "--recur", "daily")
        cli("goal", "run it", "1")
        cli("tick", "1")
        dd = json.loads(cli("day", "-o", "json")[1])
        assert dd["goal_progress"] == {"done": 1, "total": 1}
        assert dd["goal_targets"][0]["status"] == "TODO" and dd["goal_targets"][0]["done"] is True

    def test_stopping_a_recurrence_does_not_unsettle_a_past_checkin(self, cli):
        # `wl sched stop` writes `;until=<today>`. Recurrence-liveness is judged at the period's
        # START, so retiring a finished drive must NOT retroactively erase an achievement the
        # period already recorded — the past is a fact, it can't change when the future does.
        cli("add", "standup drive")                          # #1
        cli("sched", "1", "--recur", "daily")
        cli("tick", "1")
        month = date.today().strftime("%Y-%m")
        cli("add", month, "--prop", "type.date=month")       # #2, the month time node
        cli("goal", "set", "2", "keep the drive going")
        cli("goal", "set", "2", "--ids", "1")
        assert "[x] #1" in cli("goal", "ls", "2")[1]         # settled by the check-in
        cli("sched", "stop", "1")                            # drive retired today
        assert "[x] #1" in cli("goal", "ls", "2")[1]         # still settled — the tick happened
