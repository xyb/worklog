"""Tests for the shared --tag / --kind / --status filter (make_node_filter), wired via
the `filters` parent parser into ls / tree / day / logs / agenda. One definition, same
semantics everywhere: --tag is comma-separated AND, --kind/--status are equality."""
import pytest


class TestFilters:
    def _seed(self, cli, date="2026-05-28"):
        cli("add", "Lifetime", "-k", "lifetime")                                    # 1
        cli("add", "work area", "-k", "area", "-t", "work", "--parent", "1")            # 2
        cli("add", "personal area", "-k", "area", "-t", "personal", "--parent", "1")        # 3
        cli("add", "project A", "-k", "project", "-t", "work", "--parent", "2")          # 4
        cli("add", "write code", "-k", "task", "-t", "work", "--parent", "4", "--sched", date)      # 5
        cli("add", "buy groceries", "-k", "task", "-t", "personal", "--parent", "3", "--sched", date)    # 6
        # a node carrying BOTH tags (to exercise AND)
        cli("add", "retro", "-k", "task", "-t", "work,personal", "--parent", "4", "--sched", date)  # 7
        # logs dated to the viewed day (a bare --log on add lands at *now*, not `date`)
        cli("log", "5", "wrote filter", "--date", date)
        cli("log", "6", "bought food", "--date", date)
        cli("log", "7", "review", "--date", date)

    # ---- ls ----
    def test_ls_tag_filters_to_bucket(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "-t", "work")
        assert "write code" in out and "retro" in out
        assert "buy groceries" not in out

    def test_ls_tag_short_and_long_equivalent(self, cli):
        self._seed(cli)
        _, a, _ = cli("ls", "-t", "personal")
        _, b, _ = cli("ls", "--tag", "personal")
        assert a == b

    def test_ls_tag_comma_is_AND(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "-t", "work,personal")
        # only #7 carries BOTH tags
        assert "retro" in out
        assert "write code" not in out and "buy groceries" not in out

    def test_ls_kind_filter(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--kind", "project")
        assert "project A" in out
        assert "write code" not in out

    def test_ls_no_filter_unchanged(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls")
        assert "write code" in out and "buy groceries" in out and "retro" in out

    # ---- day ----
    def test_day_tag_work_hides_personal_bucket(self, cli):
        self._seed(cli)
        _, out, _ = cli("day", "2026-05-28", "-t", "work")
        assert "write code" in out
        assert "buy groceries" not in out
        assert "personal" not in out  # empty bucket not rendered

    def test_day_tag_personal(self, cli):
        self._seed(cli)
        _, out, _ = cli("day", "2026-05-28", "-t", "personal")
        assert "buy groceries" in out
        assert "write code" not in out

    def test_day_filter_no_match_message(self, cli):
        self._seed(cli)
        _, out, _ = cli("day", "2026-05-28", "-t", "nosuch")
        assert "nothing matches the filter" in out

    def test_day_stats_reflect_filter(self, cli):
        self._seed(cli)
        # work,personal AND → only #7 retro matches → exactly 1 task with progress
        _, out, _ = cli("day", "2026-05-28", "-t", "work,personal")
        assert "0/1 tasks with progress" in out

    # ---- tree ----
    def test_tree_tag_keeps_ancestor_path(self, cli):
        self._seed(cli)
        _, out, _ = cli("tree", "-t", "work")
        # work task + its structural ancestors kept; personal task pruned
        assert "write code" in out and "project A" in out and "work area" in out
        assert "buy groceries" not in out and "personal area" not in out

    def test_tree_root_filter_subtree(self, cli):
        self._seed(cli)
        _, out, _ = cli("tree", "--root", "4", "-t", "work")
        assert "write code" in out
        assert "buy groceries" not in out

    def test_tree_by_tag_respects_filter(self, cli):
        self._seed(cli)
        # work/personal are generic tags (excluded from --by tag); use a real semantic
        # tag shared by a work and a personal task, then filter by direction.
        cli("tag", "5", "alpha")   # write code (work) + alpha (a non-generic tag)
        cli("tag", "6", "alpha")   # buy groceries (personal) + alpha
        _, out, _ = cli("tree", "--by", "tag", "-t", "personal")
        assert "buy groceries" in out          # #alpha group, matches personal
        assert "write code" not in out    # filtered out (not personal)

    def test_tree_no_filter_unchanged(self, cli):
        self._seed(cli)
        _, plain, _ = cli("tree", "--depth", "9")
        assert "write code" in plain and "buy groceries" in plain

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
        assert "buy groceries" in out
        assert "write code" not in out


class TestFilterEdgeCases:
    """Regression cases from the cross-model review (GPT-5.5)."""

    def _seed(self, cli, date="2026-05-28"):
        cli("add", "Lifetime", "-k", "lifetime")                                    # 1
        cli("add", "work area", "-k", "area", "-t", "work", "--parent", "1")            # 2
        cli("add", "personal area", "-k", "area", "-t", "personal", "--parent", "1")        # 3
        cli("add", "project A", "-k", "project", "-t", "work", "--parent", "2")          # 4
        cli("add", "write code", "-k", "task", "-t", "work", "--parent", "4", "--sched", date)    # 5
        cli("add", "buy groceries", "-k", "task", "-t", "personal", "--parent", "3", "--sched", date)  # 6
        cli("log", "5", "wrote filter", "--date", date)
        cli("log", "6", "bought food", "--date", date)

    # finding 1: an effective-empty tag (",") must be a no-op, not an all-pass filter
    def test_empty_comma_tag_is_noop_ls(self, cli):
        self._seed(cli)
        _, plain, _ = cli("ls")
        _, comma, _ = cli("ls", "-t", ",")
        assert plain == comma

    def test_empty_comma_tag_is_noop_tree(self, cli):
        self._seed(cli)
        # bare tree shows the default overview (areas one level); ",", being a no-op,
        # must NOT route to the filtered (full structural) path.
        _, plain, _ = cli("tree")
        _, comma, _ = cli("tree", "-t", ",")
        assert plain == comma

    # finding 2: explicit --status CANCELED overrides the default terminal-status hide
    def test_status_canceled_honored_in_day(self, cli):
        self._seed(cli)
        cli("cancel", "6", "--at", "2026-05-28 09:00")
        _, out, _ = cli("day", "2026-05-28", "--status", "CANCELED")
        assert "buy groceries" in out          # the canceled task is shown, not pre-hidden
        assert "write code" not in out    # non-canceled filtered out

    def test_status_canceled_honored_in_agenda(self, cli):
        self._seed(cli)
        cli("cancel", "6", "--at", "2026-05-28 09:00")
        _, out, _ = cli("agenda", "2026-05-01", "2026-06-30", "--status", "CANCELED")
        assert "buy groceries" in out
        assert "write code" not in out

    def test_status_canceled_honored_in_tree(self, cli):
        self._seed(cli)
        cli("cancel", "5", "--at", "2026-05-28 09:00")
        _, out, _ = cli("tree", "--status", "CANCELED")
        assert "write code" in out  # filtered tree recurses into the canceled match

    # finding 3: the CLOCK total on `day` must be scoped to the filtered items
    def test_day_clock_total_respects_filter(self, cli):
        self._seed(cli)
        # 30 min of clock on the PERSONAL task only
        cli("spent", "6", "30m", "--at", "2026-05-28 10:00")
        _, allout, _ = cli("day", "2026-05-28")
        assert "CLOCK 30min" in allout
        _, workout, _ = cli("day", "2026-05-28", "-t", "work")
        # work view must not report the personal task's clock time
        assert "CLOCK" not in workout

    # finding 4: --by project + --kind project keeps the project headers
    def test_tree_by_project_kind_project(self, cli):
        self._seed(cli)
        _, out, _ = cli("tree", "--by", "project", "--kind", "project")
        assert "project A" in out


class TestPriorityFilter:
    """--priority / -p on the shared filter (A/B/C or P0/P1/P2; comma = any-of) + --status comma-OR."""

    def _seed(self, cli):
        cli("add", "alpha", "-k", "task", "-p", "A")   # 1
        cli("add", "bravo", "-k", "task", "-p", "B")   # 2
        cli("add", "charlie", "-k", "task", "-p", "C")  # 3
        cli("add", "delta", "-k", "task")               # 4 (no priority)

    def test_priority_exact(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--priority", "A")
        assert "alpha" in out
        assert "bravo" not in out and "charlie" not in out and "delta" not in out

    def test_priority_short_flag(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "-p", "B")
        assert "bravo" in out and "alpha" not in out

    def test_priority_comma_any_of(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "-p", "A,B")
        assert "alpha" in out and "bravo" in out
        assert "charlie" not in out and "delta" not in out

    def test_priority_p0_synonym(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "-p", "p0")   # P0 == A, case-insensitive
        assert "alpha" in out and "bravo" not in out

    def test_priority_invalid_value_errors(self, cli):
        self._seed(cli)
        code, _, err = cli("ls", "-p", "X")
        assert code != 0 and "invalid --priority" in err

    def test_priority_combines_with_tag(self, cli):
        cli("add", "work-a", "-k", "task", "-p", "A", "-t", "work")
        cli("add", "personal-a", "-k", "task", "-p", "A", "-t", "personal")
        _, out, _ = cli("ls", "-p", "A", "-t", "work")
        assert "work-a" in out and "personal-a" not in out

    def test_status_comma_or(self, cli):
        cli("add", "todo-task", "-k", "task")
        cli("add", "doing-task", "-k", "task")
        cli("start", "2")   # doing-task → DOING
        _, out, _ = cli("ls", "--status", "TODO,DOING")
        assert "todo-task" in out and "doing-task" in out

    def test_priority_filter_on_tree(self, cli):
        self._seed(cli)
        _, out, _ = cli("tree", "-p", "A", "--kind", "task")
        assert "alpha" in out and "bravo" not in out


class TestPropFilter:
    """--prop on the shared filter: exact K=V (comma-member aware) / K existence / GROUP. prefix; repeat = AND."""

    def _seed(self, cli):
        cli("add", "a", "-k", "task")                          # 1
        cli("set", "1", "github.repo", "xyb/worklog")
        cli("set", "1", "github.pr", "10,11")
        cli("add", "b", "-k", "task")                          # 2
        cli("set", "2", "github.repo", "xyb/worklog")
        cli("set", "2", "linear.id", "LUM-5")
        cli("add", "c", "-k", "task")                          # 3 (no props)

    def test_exact(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--prop", "github.repo=xyb/worklog", "--all")
        assert " #1 " in out and " #2 " in out and " #3 " not in out

    def test_exact_comma_member(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--prop", "github.pr=11", "--all")   # value stored as "10,11"
        assert " #1 " in out and " #2 " not in out

    def test_key_existence(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--prop", "linear.id", "--all")
        assert " #2 " in out and " #1 " not in out

    def test_namespace_prefix(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--prop", "github.", "--all")        # any github.* prop
        assert " #1 " in out and " #2 " in out and " #3 " not in out

    def test_namespace_prefix_star(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--prop", "github.*", "--all")
        assert " #1 " in out and " #2 " in out

    def test_repeat_is_and(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--prop", "github.repo=xyb/worklog", "--prop", "linear.id", "--all")
        assert " #2 " in out and " #1 " not in out                  # only #2 has both

    def test_no_match(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--prop", "github.repo=nope/nope", "--all")
        assert "(no nodes)" in out

    def test_parse_prop_cond_unit(self):
        from worklog.queries import _parse_prop_cond
        assert _parse_prop_cond("k=v") == ("exact", "k", "v")
        assert _parse_prop_cond("k") == ("exists", "k", None)
        assert _parse_prop_cond("g.") == ("prefix", "g.", None)
        assert _parse_prop_cond("g.*") == ("prefix", "g.", None)


class TestTreeByEmptiesGroup:
    """`wl tree --by <axis>` under a node-filter that empties a WHOLE group → that group is
    skipped (the `continue` branches), distinct from a filter that only partly trims a group."""

    def _two_directions(self, cli):
        cli("add", "work item", "-k", "task", "-t", "work")
        cli("add", "home item", "-k", "task", "-t", "personal")

    def test_by_tag_filter_drops_nonmatching_groups(self, cli):
        # non-generic tags (work/personal are excluded from the tag axis)
        cli("add", "alpha item", "-k", "task", "-t", "alpha")
        cli("add", "beta item", "-k", "task", "-t", "beta")
        # filter to `alpha` → the whole `beta` tag group empties → skipped
        code, out, _ = cli("tree", "--by", "tag", "-t", "alpha")
        assert code == 0
        assert "alpha item" in out and "beta item" not in out

    def test_by_direction_filter_drops_empty_direction(self, cli):
        self._two_directions(cli)
        code, out, _ = cli("tree", "--by", "direction", "-t", "work")
        assert code == 0
        assert "home item" not in out

    def test_by_project_filter(self, cli):
        cli("add", "Proj A", "-k", "project", "-t", "work")        # 1
        cli("add", "a task", "-k", "task", "--parent", "1", "-t", "work")  # 2
        code, out, _ = cli("tree", "--by", "project", "-t", "work")
        assert code == 0
