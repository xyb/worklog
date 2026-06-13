"""Tests for day (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestDay:
    """cmd_day driven by log dates: bucket(work/personal) → project/priority/plan-status → task → log"""

    def _seed(self, cli, date="2026-05-28"):
        cli("add", "2026", "-k", "year")                                          # 1
        cli("add", "2026-05", "-k", "month", "--parent", "1")                     # 2
        cli("add", "biz aggregation", "-k", "project", "-t", "work", "--parent", "2")     # 3
        cli("add", "czbooks", "-k", "project", "-t", "personal", "--parent", "2")  # 4
        cli("add", "agg taskA", "-k", "task", "-p", "A", "-t", "work", "--parent", "3")     # 5
        cli("add", "crawler taskB", "-k", "task", "-p", "C", "-t", "personal", "--parent", "4")  # 6
        cli("done", "5")
        cli("log", "5", "aggregated progress", "--date", date)
        cli("log", "6", "crawler progress", "--date", date)

    def test_day_groups_by_bucket_and_project(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28", "--by", "project")
        assert code == 0
        assert "work" in out and "personal" in out
        assert "biz aggregation" in out and "czbooks" in out
        assert "aggregated progress" in out and "crawler progress" in out
        assert "#5" in out and "#6" in out

    def test_day_default_by_plan(self, cli):
        # default --by plan: tasks not scheduled that day → unplanned (no separate
        # "(untagged)" bucket — that migration-era distinction was merged away)
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28")
        assert code == 0
        assert "unplanned" in out
        assert "untagged" not in out  # the misleading label is gone
        assert "aggregated progress" in out and "crawler progress" in out

    def test_day_stats_line(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28")
        assert "2 tasks with progress" in out  # 2/2
        assert "DONE 1" in out

    def test_day_default_today(self, cli):
        from datetime import date
        today = date.today().isoformat()
        self._seed(cli, date=today)
        code, out, _ = cli("day")  # no date arg = today
        assert code == 0
        assert today in out
        assert "aggregated progress" in out

    def test_day_header_shows_node_id(self, cli):
        import json
        cli("goal", "ship it")          # creates today's day node + a goal
        nid = json.loads(cli("day", "-o", "json")[1])["node_id"]
        assert nid
        _, out, _ = cli("day")
        assert f"#{nid}" in out.splitlines()[0]   # day node id leads the header

    def test_day_empty_no_fail(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2099-01-01")  # a date with no logs
        assert code == 0  # no longer fails
        assert "no log progress" in out

    def test_day_by_priority(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28", "--by", "priority")
        assert "P0" in out  # taskA priority A → P0
        assert "P2" in out  # taskB priority C → P2

    def test_day_by_plan(self, cli):
        cli("add", "2026", "-k", "year")
        cli("add", "proj", "-k", "project", "-t", "work", "--parent", "1")
        cli("add", "planned task", "-k", "task", "-t", "work,planned", "--parent", "2")    # 3
        cli("add", "temp task", "-k", "task", "-t", "work,unplanned", "--parent", "2")  # 4
        cli("log", "3", "planned progress", "--date", "2026-05-28")
        cli("log", "4", "temp progress", "--date", "2026-05-28")
        code, out, _ = cli("day", "2026-05-28", "--by", "plan")
        assert "planned" in out and "unplanned" in out

    def test_day_by_plan_unscheduled_is_unplanned(self, cli):
        # not scheduled that day + no planned tag → unplanned (no "(untagged)" suffix)
        cli("add", "2026", "-k", "year")
        cli("add", "proj", "-k", "project", "-t", "work", "--parent", "1")
        cli("add", "some task", "-k", "task", "-t", "work", "--parent", "2")  # 3
        cli("log", "3", "some progress", "--date", "2026-05-28")
        code, out, _ = cli("day", "2026-05-28", "--by", "plan")
        assert "unplanned" in out
        assert "untagged" not in out


class TestDayMeta:
    """reserved-tag logs: day recap / today's goal, shown at the top of wl day"""

    def _seed_day(self, cli):
        cli("add", "2026", "-k", "year")                                     # 1
        cli("add", "2026-05", "-k", "month", "--parent", "1")                # 2
        cli("add", "2026-05-20", "-k", "day", "--parent", "2")               # 3
        cli("add", "t", "-k", "task", "-t", "work")                          # 4
        cli("log", "4", "did something", "--date", "2026-05-20")

    def test_day_shows_goal_and_summary(self, cli):
        self._seed_day(cli)
        cli("set", "3", "goal", "deliver X today")
        cli("set", "3", "summary", "daily recap Y")
        code, out, _ = cli("day", "2026-05-20")
        assert "🎯 deliver X today" in out
        assert "Recap: daily recap Y" in out


class TestDayMetaRendering:
    """cmd_day: today's goal / day recap / week-goal / month-goal rendering branches."""

    def _setup_day_with_props(self, cli, **props):
        from datetime import date
        today = date.today().isoformat()
        # create the day node and write props via wl goal/recap/set
        cli("add", today, "-k", "day")
        for k, v in props.items():
            cli("set", "1", k, v)
        return today

    def test_day_renders_goal(self, cli):
        today = self._setup_day_with_props(cli, goal="today goal")
        _, out, _ = cli("day", today)
        assert "🎯" in out and "today goal" in out

    def test_day_renders_summary(self, cli):
        today = self._setup_day_with_props(cli, summary="end-of-day review content")
        _, out, _ = cli("day", today)
        assert "Recap" in out and "end-of-day review content" in out

    def test_day_renders_month_goal(self, cli):
        from datetime import date
        today = date.today().isoformat()
        # month → week → day chain; the month's goal shows under "This month"
        cli("add", "2026-05", "-k", "month")                      # 1
        cli("add", "2026-W22", "-k", "week", "--parent", "1")     # 2
        cli("add", today, "-k", "day", "--parent", "2")           # 3
        cli("set", "1", "goal", "month goal content")
        _, out, _ = cli("day", today)
        assert "This month" in out and "month goal content" in out

    def test_day_renders_week_goal(self, cli):
        from datetime import date
        today = date.today().isoformat()
        # week → day chain, write the goal on the week (was the old "overview")
        cli("add", "2026-W22", "-k", "week")  # id 1
        cli("add", today, "-k", "day", "--parent", "1")  # id 2
        cli("set", "1", "goal", "this week's focus")
        _, out, _ = cli("day", today)
        assert "This week" in out and "this week's focus" in out

    def test_day_renders_clock_total(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("start", "1")
        cli("stop", "1")
        _, out, _ = cli("day", today)
        assert "CLOCK" in out


class TestDayPlannedNotDoneSuppression:
    """A DONE task scheduled on a day with no logs must not be tagged «planned·not-done»."""

    def test_done_task_no_planned_not_done(self, cli):
        cli("add", "done task", "-k", "task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        cli("done", "1")
        _, out, _ = cli("day", "2026-06-15")
        assert "#1" in out
        assert "planned·not-done" not in out

    def test_open_task_still_planned_not_done(self, cli):
        cli("add", "todo task", "-k", "task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        _, out, _ = cli("day", "2026-06-15")
        assert "planned·not-done" in out          # footer stat count is still present

    def test_row_hint_suppressed_under_by_plan(self, cli):
        # under the default --by plan the `▸ planned` group header + the `[ ]` marker
        # already convey it, so the per-row «planned·not-done» hint is redundant → hidden.
        cli("add", "todo task", "-k", "task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        _, plan, _ = cli("day", "2026-06-15")                 # default by=plan
        assert "«planned·not-done»" not in plan               # row hint hidden
        assert "· planned·not-done" in plan                   # footer stat still shown
        _, proj, _ = cli("day", "2026-06-15", "--by", "project")
        assert "«planned·not-done»" in proj                   # kept under --by project


class TestDayNature:
    """The wl day header states what kind of day it is — workday / weekend, refined to
    holiday / leave / workday by a date_meta label (set via wl dateinfo)."""

    def test_weekday_is_workday(self, cli):
        # 2026-06-05 is a Friday, no date_meta -> workday baseline
        _, out, _ = cli("day", "2026-06-05")
        assert "2026-06-05 Fri · workday" in out

    def test_weekend_is_weekend(self, cli):
        # 2026-06-06 is a Saturday
        _, out, _ = cli("day", "2026-06-06")
        assert "Sat · weekend" in out

    def test_holiday_label_classified(self, cli):
        # a weekday holiday shows the label, not a contradictory "workday"
        cli("dateinfo", "2026-06-01", "Children's Day holiday")  # a Monday
        _, out, _ = cli("day", "2026-06-01")
        assert "Children's Day holiday" in out
        assert "workday" not in out

    def test_leave_label_classified(self, cli):
        cli("dateinfo", "2026-06-08", "annual leave")  # a Monday
        _, out, _ = cli("day", "2026-06-08")
        assert "annual leave" in out
        assert "workday" not in out

    def test_makeup_workday_on_weekend(self, cli):
        # a Saturday makeup workday: the label says working, so no "weekend"
        cli("dateinfo", "2026-06-13", "makeup workday (swap)")  # a Saturday
        _, out, _ = cli("day", "2026-06-13")
        assert "makeup workday" in out
        assert "weekend" not in out

    def test_neutral_label_keeps_weekday_baseline(self, cli):
        # a solar term is neither work nor rest: keep the weekday baseline + append the label
        cli("dateinfo", "2026-05-21", "Grain Buds solar term")  # a Thursday
        _, out, _ = cli("day", "2026-05-21")
        assert "workday (Grain Buds solar term)" in out

    # regression: cross-model review (GPT-5.5) found the keyword classifier too loose
    def test_office_label_not_misclassified_as_off(self, cli):
        # "office" contains "off" but must NOT be read as a day off
        cli("dateinfo", "2026-06-05", "office maintenance")  # a Friday
        _, out, _ = cli("day", "2026-06-05")
        assert "workday (office maintenance)" in out

    def test_makeup_work_chinese_not_leave(self, cli):
        # 调休上班 = working a makeup day; the bare 休 inside must not flip it to leave
        cli("dateinfo", "2026-06-13", "调休上班")  # a Saturday
        _, out, _ = cli("day", "2026-06-13")
        assert "leave" not in out and "weekend" not in out
        assert "workday" in out

    def test_swap_to_workday_beats_holiday_word(self, cli):
        # an explicit working-day signal wins over a co-occurring "holiday" word
        cli("dateinfo", "2026-06-13", "swap to workday for the holiday")  # a Saturday
        _, out, _ = cli("day", "2026-06-13")
        assert "workday" in out

    def test_swap_meet_not_workday(self, cli):
        # regression (Kimi): bare "swap" was too loose; "swap meet" on a Saturday is an
        # event, not a workday — stays weekend
        cli("dateinfo", "2026-06-13", "swap meet")  # a Saturday
        _, out, _ = cli("day", "2026-06-13")
        assert "weekend (swap meet)" in out


class TestDayMetaMarkersAndGoalProgress:
    """distinct meta markers · continued blockquote · goal achievement [done/total]."""

    def _day(self, cli, **props):
        from datetime import date
        today = date.today().isoformat()
        cli("add", today, "-k", "day")        # id 1
        for k, v in props.items():
            cli("set", "1", k, v)
        return today

    def test_distinct_markers(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "2026-05", "-k", "month")                   # 1
        cli("add", "2026-W99", "-k", "week", "--parent", "1")  # 2
        cli("add", today, "-k", "day", "--parent", "2")        # 3
        cli("set", "3", "goal", "G")
        cli("set", "3", "summary", "S")
        cli("set", "1", "goal", "T")     # month-level goal
        cli("set", "2", "goal", "O")     # week-level goal
        _, out, _ = cli("day", today)
        assert "🎯 G" in out          # today's goal
        assert "📝 Recap: S" in out   # summary distinct from goal
        assert "⭐ This month: T" in out
        assert "📅 This week: O" in out

    def test_recap_blockquote_continuation(self, cli):
        """only the FIRST line carries `> `; continuation lines align (spaces) under the `> `
        content column — not flush-left, not a repeated `> ` on every line."""
        today = self._day(cli, summary="line one\nline two\nline three")
        _, out, _ = cli("day", today)
        l1 = next(ln for ln in out.splitlines() if "line one" in ln)
        l2 = next(ln for ln in out.splitlines() if "line two" in ln)
        l3 = next(ln for ln in out.splitlines() if "line three" in ln)
        assert "> " in l1                              # first line quoted
        assert ">" not in l2 and ">" not in l3         # continuations carry no `>`
        # continuations align under the `> ` content (same column as line 1's text start)
        col = l1.index(">") + 2                         # where text after "> " begins
        assert l2.startswith(" " * col) and l2[col] != " "
        assert l3.startswith(" " * col) and l3[col] != " "

    def test_goal_progress_partial(self, cli):
        cli("add", "a", "-k", "task")   # 1
        cli("add", "b", "-k", "task")   # 2
        cli("add", "c", "-k", "task")   # 3
        cli("done", "1")
        today = self._day_after(cli)
        cli("set", str(self._day_id), "goal", "ship #1 #2 #3")
        _, out, _ = cli("day", today)
        assert "[1/3]" in out and "🟡" in out

    def test_goal_progress_all_done(self, cli):
        cli("add", "a", "-k", "task")   # 1
        cli("add", "b", "-k", "task")   # 2
        cli("done", "1"); cli("done", "2")
        today = self._day_after(cli)
        cli("set", str(self._day_id), "goal", "do #1 and #2")
        _, out, _ = cli("day", today)
        assert "[2/2]" in out and "✅" in out

    def test_goal_no_ids_no_indicator(self, cli):
        today = self._day(cli, goal="just a free-text goal, no ids")
        _, out, _ = cli("day", today)
        assert "🎯" in out
        assert "[" not in out.split("🎯")[1].split("\n")[0]   # no [n/m] on the goal line

    # helper: create the day node AFTER some tasks exist, tracking its id
    def _day_after(self, cli):
        from datetime import date
        today = date.today().isoformat()
        code, out, _ = cli("add", today, "-k", "day")
        import re
        self._day_id = int(re.search(r"#(\d+)", out).group(1))
        return today


class TestDayJson:
    def test_day_json_structure(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", today, "-k", "day")                      # 1
        cli("add", "task a", "-k", "task", "-t", "work")     # 2
        cli("sched", "2", "today")
        cli("log", "2", "did it")
        cli("set", "1", "goal", "do #2")
        import json
        code, out, _ = cli("day", today, "-o", "json")
        d = json.loads(out)
        assert code == 0 and d["date"] == today
        assert d["goal"] == "do #2"
        assert d["goal_progress"] == {"done": 0, "total": 1}
        t = next(t for t in d["tasks"] if t["id"] == 2)
        assert t["planned"] is True and t["logs"] == ["did it"]

    def test_day_json_empty_day(self, cli):
        import json
        _, out, _ = cli("day", "2099-01-01", "-o", "json")
        d = json.loads(out)
        assert d["date"] == "2099-01-01" and d["tasks"] == []


class TestDayTitleWrap:
    """wl day's custom task-line renderer wraps long titles with hang-indent (shared _hang_wrap)."""

    def test_day_long_title_wraps_hang_indented(self, cli):
        from worklog import helpers
        long = "x" * 200
        cli("add", long, "-k", "task", "-t", "work")
        cli("sched", "1", "today")
        cli("log", "1", "did")
        from datetime import date
        helpers._set_width_cap(50)
        try:
            _, out, _ = cli("--color", "never", "day", date.today().isoformat())
        finally:
            helpers._set_width_cap(None)
        body = [ln for ln in out.splitlines() if "x" in ln and "did" not in ln]
        assert len(body) > 1                       # the title wrapped onto multiple lines
        first = body[0]
        col = first.index("xxx")                    # where the title text starts
        for cont in body[1:]:
            assert cont.startswith(" " * col)       # continuations hang-indent to the title column
            assert cont.strip().startswith("x")
