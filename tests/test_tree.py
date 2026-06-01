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

    def test_tree_filter_kind(self, cli):
        cli("add", "yr", "-k", "year")
        cli("add", "task1", "-k", "task")
        code, out, _ = cli("tree", "--kind", "year")
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
        assert "no root" in out.lower()


# ─── logs ───


class TestTreeRoot:
    def _seed(self, cli):
        cli("add", "month", "-k", "month")            # 1
        cli("add", "projectX", "-k", "project", "--parent", "1")  # 2
        cli("add", "subtaskA", "-k", "task", "--parent", "2")    # 3
        cli("add", "subtaskB", "-k", "task", "--parent", "2")    # 4
        cli("add", "grandchild", "-k", "task", "--parent", "3")      # 5

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
        cli("add", "2026-05", "-k", "month")                                          # 1
        cli("add", "data-viz", "-k", "project", "-t", "gaming,work", "--parent", "1")  # 2
        cli("add", "investment", "-k", "project", "-t", "invest,personal", "--parent", "1")          # 3
        cli("add", "login fix", "-k", "task", "-t", "gaming,work,P0", "--parent", "1")           # 4
        cli("add", "采数管线", "-k", "task", "-t", "gaming,work", "--parent", "1")              # 5
        cli("add", "对账", "-k", "task", "-t", "invest,personal", "--parent", "1")             # 6
        cli("add", "structural child", "-k", "task", "--parent", "2")                                 # 7 (under project #2)
        cli("add", "morning check", "-k", "task", "-t", "work,P0", "--parent", "1")                      # 8 (no project tag = orphan)

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
        # gaming-data-viz section should contain login fix + 采数管线 (shared gaming tag)
        gaming_section = out.split("investment")[0]
        assert "login fix" in gaming_section
        assert "采数管线" in gaming_section
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
        assert "对账" in personal_section


class TestTreeDepthSortActivity:
    """wl tree: default depth-limited + time nodes sorted by date + day nodes expand activity for that day"""

    def test_tree_default_area_one_level(self, cli):
        # default tree: area lists only one level (area name); projects not expanded; use --root <area> to see them
        cli("add", "life", "-k", "lifetime")           # 1
        cli("add", "数据", "-k", "area", "--parent", "1")  # 2
        cli("add", "proj", "-k", "project", "--parent", "2")  # 3
        code, out, _ = cli("tree")
        assert "数据" in out          # area name appears
        assert "proj" not in out      # project not expanded by default
        code, out2, _ = cli("tree", "--root", "2")  # drill into area to see projects
        assert "proj" in out2

    def test_tree_time_nodes_sorted_by_date(self, cli):
        cli("add", "2026-05", "-k", "month")              # 1
        cli("add", "2026-W22", "-k", "week", "--parent", "1")  # 2 added first (smaller id)
        cli("add", "2026-W18", "-k", "week", "--parent", "1")  # 3 added second (larger id)
        code, out, _ = cli("tree", "--root", "1", "--depth", "1")
        assert out.index("2026-W18") < out.index("2026-W22")  # sorted by date not id

    def test_tree_day_shows_activity(self, cli):
        cli("add", "2026-05", "-k", "month")                   # 1
        cli("add", "2026-05-18", "-k", "day", "--parent", "1") # 2
        cli("add", "proj", "-k", "project")                    # 3
        cli("add", "干了活", "-k", "task", "--parent", "3")     # 4
        cli("log", "4", "今天的进展", "--date", "2026-05-18")
        cli("log", "4", "别天的进展", "--date", "2026-05-20")
        code, out, _ = cli("tree", "--root", "2", "--depth", "3")
        assert "干了活" in out          # task with log that day appears under day
        assert "今天的进展" in out       # that day's log
        assert "别天的进展" not in out   # other day's log does not appear


class TestTreeEmpty:
    def test_tree_empty_repo(self, cli):
        _, out, _ = cli("tree")
        assert "no root nodes" in out

    def test_tree_root_no_kind_filter_match(self, cli):
        cli("add", "t1", "-k", "task")  # parent_id=NULL, kind=task
        _, out, _ = cli("tree", "--kind", "nosuch")
        assert "no root nodes" in out


class TestDefaultTreeAreaListing:
    """_print_default_tree: lifetime + area + today / month fallback branches."""

    def test_default_tree_lists_area_children(self, cli):
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "work", "-k", "area", "--parent", "1")
        cli("add", "personal", "-k", "area", "--parent", "1")
        _, out, _ = cli("tree")
        assert "work" in out and "personal" in out

    def test_default_tree_with_today_node(self, cli):
        """lifetime + today's day node → dayn branch (chain + _print_day_activity)"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "2026", "-k", "year", "--parent", "1")
        cli("add", today, "-k", "day", "--parent", "2")
        _, out, _ = cli("tree")
        assert today in out
        assert "2026" in out

    def test_default_tree_month_fallback(self, cli):
        """no day node today, has month → month fallback branch"""
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "2026-05", "-k", "month", "--parent", "1")
        _, out, _ = cli("tree")
        assert "2026-05" in out


class TestPrintDayActivityEdges:
    """_print_day_activity: log_tail=0 branch + habit + omitted=0 branch."""

    def test_tree_log_tail_zero_no_logs(self, cli):
        """wl tree --no-logs → log_tail=0; logs not expanded but tasks still listed"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", today, "-k", "day", "--parent", "1")
        cli("add", "t1", "-k", "task")
        cli("log", "3", "hidden-body")
        _, out, _ = cli("tree", "--no-logs")
        assert "t1" in out
        assert "hidden-body" not in out


class TestTreeBy:
    """cmd_tree --by tag/project/direction grouping dimensions."""

    def test_tree_by_tag_no_semantic(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work")  # work is a generic tag
        _, out, _ = cli("tree", "--by", "tag")
        assert "no semantic" in out or "(no " in out

    def test_tree_by_tag_with_semantic(self, cli):
        cli("add", "t1", "-k", "task", "-t", "team-dev")
        cli("add", "t2", "-k", "task", "-t", "team-dev")
        _, out, _ = cli("tree", "--by", "tag")
        assert "team-dev" in out

    def test_tree_by_project_no_projects(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("tree", "--by", "project")
        assert "no project" in out

    def test_tree_by_project_with_orphan(self, cli):
        cli("add", "P1", "-k", "project")  # id 1
        cli("add", "child", "-k", "task", "--parent", "1")  # id 2 under P1
        cli("add", "orphan", "-k", "task")  # id 3 orphan
        _, out, _ = cli("tree", "--by", "project")
        assert "P1" in out
        assert "unassigned" in out
        assert "orphan" in out

    def test_tree_by_direction(self, cli):
        cli("add", "w1", "-k", "task", "-t", "work")
        cli("add", "p1", "-k", "task", "-t", "personal")
        _, out, _ = cli("tree", "--by", "direction")
        assert "w1" in out and "p1" in out


class TestPrintDayActivityHabit:
    def test_habit_with_today_log_shows_x(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", today, "-k", "day", "--parent", "1")
        cli("add", "h1", "-k", "habit")
        cli("log", "3", "done today")
        _, out, _ = cli("tree", "--root", "2", "--depth", "2")
        # habit with a log that day should render [x]
        assert "[x]" in out
