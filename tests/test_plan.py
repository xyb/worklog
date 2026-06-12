"""Tests for the structured plan props (WL#691): writing a goal/top5 reserved-tag log
extracts the #ids it names into a `plan.<tag>` prop, so the plan↔node link is queryable."""
import json


def _day_id(cli):
    return json.loads(cli("day", "-o", "json")[1])["day_node_id"]


def _props(cli, nid):
    return json.loads(cli("show", str(nid), "-o", "json")[1])["props"]


class TestPlanProp:
    def test_goal_ids_extracted(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "b", "-k", "task")          # #2
        cli("goal", "deliver #1 and draft #2")
        assert _props(cli, _day_id(cli))["plan.goal"] == "1,2"

    def test_plan_goal_tracks_latest_goal(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "b", "-k", "task")          # #2
        cli("goal", "deliver #1")
        cli("goal", "changed: only #2")        # rewrite → prop overwrites to the latest text
        assert _props(cli, _day_id(cli))["plan.goal"] == "2"

    def test_goal_without_ids_has_no_plan_prop(self, cli):
        cli("goal", "just wrap things up, nothing specific")
        assert "plan.goal" not in _props(cli, _day_id(cli))

    def test_goal_clears_stale_plan_prop(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("goal", "do #1")                   # plan.goal = 1
        cli("goal", "never mind, free day")    # no ids → prop cleared
        assert "plan.goal" not in _props(cli, _day_id(cli))

    def test_pr_and_linear_refs_excluded(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("goal", "merge PR#1 + LUM-1 then ship #1")  # PR#1/LUM-1 not nodes; bare #1 is
        assert _props(cli, _day_id(cli))["plan.goal"] == "1"

    def test_only_live_nodes_extracted(self, cli):
        cli("goal", "target #999 which does not exist")
        assert "plan.goal" not in _props(cli, _day_id(cli))

    def test_reverse_query_by_plan_goal(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("goal", "deliver #1")
        _, out, _ = cli("ls", "--prop", "plan.goal", "--all")
        assert f"#{_day_id(cli)}" in out

    def test_manual_set_rejected(self, cli):
        cli("goal", "x")                       # ensure a day node exists
        code, _, err = cli("set", str(_day_id(cli)), "plan.goal", "1")
        assert code != 0 and "reserved" in err

    def test_top5_also_extracted(self, cli):
        cli("add", "a", "-k", "task")          # #1
        cli("add", "proj", "-k", "project")    # #2
        cli("set", "2", "top5", "top priority #1")   # top5 is a reserved tag → typed log + plan.top5
        assert _props(cli, 2)["plan.top5"] == "1"
