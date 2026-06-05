"""Tests for the shared --tag / --kind / --status filter (make_node_filter), wired via
the `filters` parent parser into ls / tree / day / logs / agenda. One definition, same
semantics everywhere: --tag is comma-separated AND, --kind/--status are equality."""
import pytest


class TestFilters:
    def _seed(self, cli, date="2026-05-28"):
        cli("add", "Lifetime", "-k", "lifetime")                                    # 1
        cli("add", "工作域", "-k", "area", "-t", "work", "--parent", "1")            # 2
        cli("add", "个人域", "-k", "area", "-t", "personal", "--parent", "1")        # 3
        cli("add", "项目A", "-k", "project", "-t", "work", "--parent", "2")          # 4
        cli("add", "写代码", "-k", "task", "-t", "work", "--parent", "4", "--sched", date)      # 5
        cli("add", "买菜", "-k", "task", "-t", "personal", "--parent", "3", "--sched", date)    # 6
        # a node carrying BOTH tags (to exercise AND)
        cli("add", "复盘", "-k", "task", "-t", "work,personal", "--parent", "4", "--sched", date)  # 7
        # logs dated to the viewed day (a bare --log on add lands at *now*, not `date`)
        cli("log", "5", "wrote filter", "--date", date)
        cli("log", "6", "bought food", "--date", date)
        cli("log", "7", "review", "--date", date)

    # ---- ls ----
    def test_ls_tag_filters_to_bucket(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "-t", "work")
        assert "写代码" in out and "复盘" in out
        assert "买菜" not in out

    def test_ls_tag_short_and_long_equivalent(self, cli):
        self._seed(cli)
        _, a, _ = cli("ls", "-t", "personal")
        _, b, _ = cli("ls", "--tag", "personal")
        assert a == b

    def test_ls_tag_comma_is_AND(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "-t", "work,personal")
        # only #7 carries BOTH tags
        assert "复盘" in out
        assert "写代码" not in out and "买菜" not in out

    def test_ls_kind_filter(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--kind", "project")
        assert "项目A" in out
        assert "写代码" not in out

    def test_ls_no_filter_unchanged(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls")
        assert "写代码" in out and "买菜" in out and "复盘" in out

    # ---- day ----
    def test_day_tag_work_hides_personal_bucket(self, cli):
        self._seed(cli)
        _, out, _ = cli("day", "2026-05-28", "-t", "work")
        assert "写代码" in out
        assert "买菜" not in out
        assert "personal" not in out  # empty bucket not rendered

    def test_day_tag_personal(self, cli):
        self._seed(cli)
        _, out, _ = cli("day", "2026-05-28", "-t", "personal")
        assert "买菜" in out
        assert "写代码" not in out

    def test_day_filter_no_match_message(self, cli):
        self._seed(cli)
        _, out, _ = cli("day", "2026-05-28", "-t", "nosuch")
        assert "nothing matches the filter" in out

    def test_day_stats_reflect_filter(self, cli):
        self._seed(cli)
        # work,personal AND → only #7 复盘 matches → exactly 1 task with progress
        _, out, _ = cli("day", "2026-05-28", "-t", "work,personal")
        assert "0/1 tasks with progress" in out

    # ---- tree ----
    def test_tree_tag_keeps_ancestor_path(self, cli):
        self._seed(cli)
        _, out, _ = cli("tree", "-t", "work")
        # work task + its structural ancestors kept; personal task pruned
        assert "写代码" in out and "项目A" in out and "工作域" in out
        assert "买菜" not in out and "个人域" not in out

    def test_tree_root_filter_subtree(self, cli):
        self._seed(cli)
        _, out, _ = cli("tree", "--root", "4", "-t", "work")
        assert "写代码" in out
        assert "买菜" not in out

    def test_tree_by_tag_respects_filter(self, cli):
        self._seed(cli)
        # work/personal are generic tags (excluded from --by tag); use a real semantic
        # tag shared by a work and a personal task, then filter by direction.
        cli("tag", "5", "alpha")   # 写代码 (work) + alpha (a non-generic tag)
        cli("tag", "6", "alpha")   # 买菜 (personal) + alpha
        _, out, _ = cli("tree", "--by", "tag", "-t", "personal")
        assert "买菜" in out          # #dev group, matches personal
        assert "写代码" not in out    # filtered out (not personal)

    def test_tree_no_filter_unchanged(self, cli):
        self._seed(cli)
        _, plain, _ = cli("tree", "--depth", "9")
        assert "写代码" in plain and "买菜" in plain

    # ---- logs ----
    def test_logs_tag_filter(self, cli):
        self._seed(cli)
        _, out, _ = cli("logs", "--since", "2026-05-01", "-t", "work")
        assert "wrote filter" in out
        assert "bought food" not in out

    # ---- agenda ----
    def test_agenda_tag_filter(self, cli):
        self._seed(cli)
        _, out, _ = cli("agenda", "2026-05-01", "2026-06-30", "-t", "personal")
        assert "买菜" in out
        assert "写代码" not in out
