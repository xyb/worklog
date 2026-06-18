"""Tests for tree (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestTree:
    def test_tree_renders_indented(self, cli):
        cli("add", "root")
        cli("add", "child", "--parent", "1")
        cli("add", "grandchild", "--parent", "2")
        code, out, _ = cli("tree", "--depth", "9")  # default tree is area+today; use --depth for full tree
        lines = [l for l in out.split("\n") if l.strip()]
        assert any("root" in l for l in lines)
        assert any("  " in l and "child" in l for l in lines)
        assert any("    " in l and "grandchild" in l for l in lines)

    def test_tree_filter_prop(self, cli):
        cli("add", "yr", "--prop", "type.date=year")
        cli("add", "task1")
        code, out, _ = cli("tree", "--prop", "type.date=year")
        assert "yr" in out
        assert "task1" not in out

    def test_tree_depth_limit(self, cli):
        cli("add", "root")
        cli("add", "L1", "--parent", "1")
        cli("add", "L2", "--parent", "2")
        cli("add", "L3", "--parent", "3")
        code, out, _ = cli("tree", "--depth", "1")
        assert "root" in out
        assert "L1" in out
        # L2 / L3 should not appear (depth=1 means root + 1 level)
        # implementation-dependent but L3 should at least be truncated
        assert "L3" not in out

    def test_tree_empty(self, cli):
        code, out, _ = cli("tree")
        assert "empty" in out.lower()


# ─── logs ───


class TestTreeRoot:
    def _seed(self, cli):
        cli("add", "month", "--prop", "type.date=month")            # 1
        cli("add", "projectX", "--para", "project", "--parent", "1")  # 2
        cli("add", "subtaskA", "--parent", "2")    # 3
        cli("add", "subtaskB", "--parent", "2")    # 4
        cli("add", "grandchild", "--parent", "3")      # 5

    def test_tree_root_starts_from_mid_node(self, cli):
        self._seed(cli)
        # project #2 as root (non-NULL parent_id is fine)
        code, out, _ = cli("tree", "--root", "2")
        assert code == 0
        assert "projectX" in out
        assert "subtaskA" in out
        assert "grandchild" in out
        assert "month" not in out  # upstream should not appear

    def test_tree_root_nonexistent_fails(self, cli):
        self._seed(cli)
        code, _, err = cli("tree", "--root", "99")
        assert code != 0


class TestTreeBy:
    def _seed(self, cli):
        cli("add", "2026-05", "--prop", "type.date=month")                                          # 1
        cli("add", "data-viz", "--para", "project", "-t", "gaming,work", "--parent", "1")  # 2
        cli("add", "investment", "--para", "project", "-t", "invest,personal", "--parent", "1")          # 3
        cli("add", "login fix", "-t", "gaming,work,P0", "--parent", "1")           # 4
        cli("add", "ingest pipeline", "-t", "gaming,work", "--parent", "1")        # 5
        cli("add", "reconcile", "-t", "invest,personal", "--parent", "1")           # 6
        cli("add", "structural child", "--parent", "2")                                 # 7 (under project #2)
        cli("add", "morning check", "-t", "work,P0", "--parent", "1")                      # 8 (no project tag = orphan)

    def test_by_tag_groups(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "tag")
        assert code == 0
        assert "#gaming" in out
        assert "#invest" in out
        # generic tags are not used as group headers
        assert "#work" not in out and "#P0" not in out

    def test_by_project_groups_by_shared_tag(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "project")
        # gaming-data-viz section should contain login fix + ingest pipeline (shared gaming tag)
        gaming_section = out.split("investment")[0]
        assert "login fix" in gaming_section
        assert "ingest pipeline" in gaming_section
        # structural child (parent=project) also counts
        assert "structural child" in gaming_section

    def test_by_project_orphans(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "project")
        # morning check has no project tag → falls into the unassigned bucket
        assert "unassigned" in out
        orphan_section = out.split("unassigned")[-1]
        assert "morning check" in orphan_section

    def test_by_direction(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "direction")
        assert "【work】" in out
        assert "【personal】" in out
        work_section = out.split("【personal】")[0]
        assert "login fix" in work_section
        personal_section = out.split("【personal】")[-1]
        assert "reconcile" in personal_section


class TestTreeDepthSortActivity:
    """wl tree: default depth-limited + time nodes sorted by date + day nodes expand activity for that day"""

    def test_tree_default_area_one_level(self, cli):
        # default tree: area lists only one level (area name); projects not expanded; use --root <area> to see them
        cli("add", "life", "--prop", "type.date=lifetime")           # 1
        cli("add", "data", "--para", "area", "--parent", "1")  # 2
        cli("add", "proj", "--para", "project", "--parent", "2")  # 3
        code, out, _ = cli("tree")
        assert "data" in out          # area name appears
        assert "proj" not in out      # project not expanded by default
        code, out2, _ = cli("tree", "--root", "2")  # drill into area to see projects
        assert "proj" in out2

    def test_tree_time_nodes_sorted_by_date(self, cli):
        cli("add", "2026-05", "--prop", "type.date=month")              # 1
        cli("add", "2026-W22", "--prop", "type.date=week", "--parent", "1")  # 2 added first (smaller id)
        cli("add", "2026-W18", "--prop", "type.date=week", "--parent", "1")  # 3 added second (larger id)
        code, out, _ = cli("tree", "--root", "1", "--depth", "1")
        assert out.index("2026-W18") < out.index("2026-W22")  # sorted by date not id

    def test_tree_day_shows_activity(self, cli):
        cli("add", "2026-05", "--prop", "type.date=month")                   # 1
        cli("add", "2026-05-18", "--prop", "type.date=day", "--parent", "1") # 2
        cli("add", "proj", "--para", "project")                    # 3
        cli("add", "did work", "--parent", "3")        # 4
        cli("log", "4", "today's progress", "--date", "2026-05-18")
        cli("log", "4", "other day's progress", "--date", "2026-05-20")
        code, out, _ = cli("tree", "--root", "2", "--depth", "3")
        assert "did work" in out                # task with log that day appears under day
        assert "today's progress" in out         # that day's log
        assert "other day's progress" not in out # other day's log does not appear


class TestTreeEmpty:
    def test_tree_empty_repo(self, cli):
        _, out, _ = cli("tree")
        assert "empty" in out.lower()

    def test_tree_nodes_outside_overview_not_called_empty(self, cli):
        # An orphan task (parent_id NULL, not a time/area node) is outside the default
        # overview's scope, but the DB is NOT empty — the message must say so and point at
        # how to see it, never imply "no nodes" (regression: it used to print "(no root nodes)").
        cli("add", "orphan task")
        _, out, _ = cli("tree")
        assert "empty" not in out.lower()
        assert "1 node" in out and "overview" in out
        assert "--depth" in out or "ls --all" in out
        # the node really is reachable via a deeper expansion
        _, deep, _ = cli("tree", "--depth", "9")
        assert "orphan task" in deep

    def test_tree_root_no_prop_filter_match(self, cli):
        cli("add", "t1")  # parent_id=NULL, a bare task
        # --prop flows through the shared filter (make_node_filter); an unmatched
        # filter prunes the tree to nothing.
        _, out, _ = cli("tree", "--prop", "type.nosuch")
        assert "nothing matches the filter" in out


class TestDefaultTreeAreaListing:
    """_print_default_tree: lifetime + area + today / month fallback branches."""

    def test_default_tree_lists_area_children(self, cli):
        cli("add", "Lifetime", "--prop", "type.date=lifetime")
        cli("add", "work", "--para", "area", "--parent", "1")
        cli("add", "personal", "--para", "area", "--parent", "1")
        _, out, _ = cli("tree")
        assert "work" in out and "personal" in out

    def test_default_tree_with_today_node(self, cli):
        """lifetime + today's day node → dayn branch (chain + _print_day_activity)"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "--prop", "type.date=lifetime")
        cli("add", "2026", "--prop", "type.date=year", "--parent", "1")
        cli("add", today, "--prop", "type.date=day", "--prop", f"date.period={today}", "--parent", "2")
        _, out, _ = cli("tree")
        assert today in out
        assert "2026" in out

    def test_default_tree_month_fallback(self, cli):
        """no day node today, has month → month fallback branch"""
        cli("add", "Lifetime", "--prop", "type.date=lifetime")
        cli("add", "2026-05", "--prop", "type.date=month", "--parent", "1")
        _, out, _ = cli("tree")
        assert "2026-05" in out


class TestPrintDayActivityEdges:
    """_print_day_activity: log_tail=0 branch + habit + omitted=0 branch."""

    def test_tree_log_tail_zero_no_logs(self, cli):
        """wl tree --no-logs → log_tail=0; logs not expanded but tasks still listed"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "--prop", "type.date=lifetime")
        cli("add", today, "--prop", "type.date=day", "--prop", f"date.period={today}", "--parent", "1")
        cli("add", "t1")
        cli("log", "3", "hidden-body")
        _, out, _ = cli("tree", "--no-logs")
        assert "t1" in out
        assert "hidden-body" not in out


class TestTreeBy:
    """cmd_tree --by tag/project/direction grouping dimensions."""

    def test_tree_by_tag_no_semantic(self, cli):
        cli("add", "t1", "-t", "work")  # work is a generic tag
        _, out, _ = cli("tree", "--by", "tag")
        assert "no semantic" in out or "(no " in out

    def test_tree_by_tag_with_semantic(self, cli):
        cli("add", "t1", "-t", "team-dev")
        cli("add", "t2", "-t", "team-dev")
        _, out, _ = cli("tree", "--by", "tag")
        assert "team-dev" in out

    def test_tree_by_project_no_projects(self, cli):
        cli("add", "t1")
        _, out, _ = cli("tree", "--by", "project")
        assert "no project" in out

    def test_tree_by_project_with_orphan(self, cli):
        cli("add", "P1", "--para", "project")  # id 1
        cli("add", "child", "--parent", "1")  # id 2 under P1
        cli("add", "orphan")  # id 3 orphan
        _, out, _ = cli("tree", "--by", "project")
        assert "P1" in out
        assert "unassigned" in out
        assert "orphan" in out

    def test_tree_by_direction(self, cli):
        cli("add", "w1", "-t", "work")
        cli("add", "p1", "-t", "personal")
        _, out, _ = cli("tree", "--by", "direction")
        assert "w1" in out and "p1" in out


class TestPrintDayActivityHabit:
    def test_habit_with_today_log_shows_x(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "--prop", "type.date=lifetime")
        cli("add", today, "--prop", "type.date=day", "--parent", "1")
        cli("add", "h1", "--prop", "type.habit=true")
        cli("tick", "3")  # check-in (writes a checkin metric) = done today
        _, out, _ = cli("tree", "--root", "2", "--depth", "2")
        # habit checked in that day should render [x]
        assert "[x]" in out


class TestTreeByProjectSharedTag:
    """tree --by project: tasks sharing a semantic tag with a project are listed under it."""

    def test_task_with_shared_tag_appears_under_project(self, cli):
        cli("add", "proj-x", "--para", "project", "-p", "A", "-t", "topic-x,work")
        # Task is NOT a child of proj-x, but shares 'topic-x' tag
        cli("add", "shared-task", "-t", "topic-x")
        _, out, _ = cli("tree", "--by", "project")
        # both project header and the task ID should appear
        assert "proj-x" in out
        assert "shared-task" in out


class TestTreeTimePins:
    """a task fuzzy-pinned at a time node (scheduled_date == its title, e.g.
    @2026-06) hangs under its project, not under the month node — tree/focus on the
    time node must still surface it, else a 'what's scheduled this month' check misses
    it and a duplicate gets created."""

    def _seed(self, cli):
        cli("add", "Lifetime", "--prop", "type.date=lifetime")                       # 1
        cli("add", "2026", "--prop", "type.date=year", "--parent", "1")              # 2
        cli("add", "2026-06", "--prop", "type.date=month", "--parent", "2")          # 3
        cli("add", "proj", "--para", "project", "--parent", "1")           # 4
        cli("add", "month task", "--parent", "4", "--scheduled", "2026-06")  # 5
        cli("add", "week task", "--parent", "4", "--scheduled", "2026-W23")  # 6

    def test_tree_root_month_shows_pinned(self, cli):
        self._seed(cli)
        _, out, _ = cli("tree", "--root", "3", "--depth", "9")
        assert "month task" in out      # the @2026-06 pin shows under the month node
        assert "week task" not in out   # the @2026-W23 pin is not this month's

    def test_focus_month_shows_pinned(self, cli):
        self._seed(cli)
        _, out, _ = cli("focus", "3")
        assert "month task" in out
        assert "(no children)" not in out

    def test_pinned_canceled_hidden_by_default(self, cli):
        self._seed(cli)
        cli("cancel", "5")
        _, out, _ = cli("tree", "--root", "3", "--depth", "9")
        assert "month task" not in out
        _, out2, _ = cli("--show-canceled", "tree", "--root", "3", "--depth", "9")  # global flag
        assert "month task" in out2

    def test_filtered_tree_root_month_shows_matching_pins(self, cli):
        # a filtered drill-down on a month must still surface its @-pins
        # that match the filter (they hang under their project, not the month subtree)
        cli("add", "Lifetime", "--prop", "type.date=lifetime")                       # 1
        cli("add", "2026", "--prop", "type.date=year", "--parent", "1")              # 2
        cli("add", "2026-06", "--prop", "type.date=month", "--parent", "2")          # 3
        cli("add", "proj", "--para", "project", "--parent", "1")           # 4
        cli("add", "work pin", "--parent", "4", "-t", "work", "--scheduled", "2026-06")      # 5
        cli("add", "personal pin", "--parent", "4", "-t", "personal", "--scheduled", "2026-06")  # 6
        _, out, _ = cli("tree", "--root", "3", "-t", "work")
        assert "work pin" in out
        assert "personal pin" not in out


class TestTreeJson:
    def test_tree_json_nested(self, cli):
        cli("add", "area", "--para", "area")                       # 1
        cli("add", "proj", "--para", "project", "--parent", "1")    # 2
        cli("add", "t", "--parent", "2")          # 3
        import json
        code, out, _ = cli("tree", "--depth", "5", "-o", "json")
        d = json.loads(out)
        assert code == 0 and isinstance(d, list)
        root = next(r for r in d if r["id"] == 1)
        assert root["children"][0]["id"] == 2
        assert root["children"][0]["children"][0]["id"] == 3

    def test_tree_json_root_subtree(self, cli):
        cli("add", "proj", "--para", "project")                     # 1
        cli("add", "t", "--parent", "1")          # 2
        import json
        _, out, _ = cli("tree", "--root", "1", "-o", "json")
        d = json.loads(out)
        assert d[0]["id"] == 1 and [c["id"] for c in d[0]["children"]] == [2]
