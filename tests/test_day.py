"""Tests for day (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestDay:
    """cmd_day driven by log dates: bucket(work/personal) → project/priority/plan-status → task → log"""

    def _seed(self, cli, date="2026-05-28"):
        cli("add", "2026", "-k", "year")                                          # 1
        cli("add", "2026-05", "-k", "month", "--parent", "1")                     # 2
        cli("add", "业务聚合", "-k", "project", "-t", "work", "--parent", "2")     # 3
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
        assert "业务聚合" in out and "czbooks" in out
        assert "aggregated progress" in out and "crawler progress" in out
        assert "#5" in out and "#6" in out

    def test_day_default_by_plan(self, cli):
        # default --by plan: untagged tasks → unplanned (untagged), no grouping by project
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28")
        assert code == 0
        assert "unplanned (untagged)" in out
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
        cli("add", "临时任务", "-k", "task", "-t", "work,unplanned", "--parent", "2")  # 4
        cli("log", "3", "计划进展", "--date", "2026-05-28")
        cli("log", "4", "临时进展", "--date", "2026-05-28")
        code, out, _ = cli("day", "2026-05-28", "--by", "plan")
        assert "planned" in out and "unplanned" in out

    def test_day_by_plan_untagged_as_unplanned(self, cli):
        # no planned/unplanned tag → treated as unplanned, marked (untagged)
        cli("add", "2026", "-k", "year")
        cli("add", "proj", "-k", "project", "-t", "work", "--parent", "1")
        cli("add", "untagged task", "-k", "task", "-t", "work", "--parent", "2")  # 3
        cli("log", "3", "untagged progress", "--date", "2026-05-28")
        code, out, _ = cli("day", "2026-05-28", "--by", "plan")
        assert "unplanned (untagged)" in out


class TestDayMeta:
    """meta: day recap / Top5 / today's goal stored as day-node props, shown at the top of wl day"""

    def _seed_day(self, cli):
        cli("add", "2026", "-k", "year")                                     # 1
        cli("add", "2026-05", "-k", "month", "--parent", "1")                # 2
        cli("add", "2026-05-20", "-k", "day", "--parent", "2")               # 3
        cli("add", "t", "-k", "task", "-t", "work")                          # 4
        cli("log", "4", "做了点事", "--date", "2026-05-20")

    def test_day_shows_goal_and_summary(self, cli):
        self._seed_day(cli)
        cli("set", "3", "goal", "今天交付 X")
        cli("set", "3", "summary", "今天小结 Y")
        code, out, _ = cli("day", "2026-05-20")
        assert "🎯 今天交付 X" in out
        assert "Recap: 今天小结 Y" in out


class TestDayMetaRendering:
    """cmd_day: goal / day recap / Top5 / week prop rendering branches."""

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
        today = self._setup_day_with_props(cli, summary="日终复盘内容")
        _, out, _ = cli("day", today)
        assert "Recap" in out and "日终复盘内容" in out

    def test_day_renders_top5(self, cli):
        today = self._setup_day_with_props(cli, top5="Top5 内容")
        _, out, _ = cli("day", today)
        assert "Top5" in out

    def test_day_renders_week_overview(self, cli):
        from datetime import date
        today = date.today().isoformat()
        # build a week → day parent-child chain, write overview on the week
        cli("add", "2026-W22", "-k", "week")  # id 1
        cli("add", today, "-k", "day", "--parent", "1")  # id 2
        cli("set", "1", "overview", "本周主线")
        _, out, _ = cli("day", today)
        assert "This week" in out and "本周主线" in out

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
        cli("add", "完成任务", "-k", "task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        cli("done", "1")
        _, out, _ = cli("day", "2026-06-15")
        assert "#1" in out
        assert "planned·not-done" not in out

    def test_open_task_still_planned_not_done(self, cli):
        cli("add", "待办任务", "-k", "task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        _, out, _ = cli("day", "2026-06-15")
        assert "planned·not-done" in out
