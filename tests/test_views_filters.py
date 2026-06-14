"""View-layer branches the happy-path suites skip: `wl tree --root` guard, the
`wl summary --by <axis>` groupers under an active node-filter that empties some
groups, and `wl day` with a filter that excludes everything."""


class TestTreeRoot:
    def test_tree_root_missing_errors(self, cli):
        code, _, err = cli("tree", "--root", "99999")
        assert code != 0 and "not found" in err

    def test_tree_root_renders_subtree(self, cli):
        cli("add", "parent proj", "-k", "project")        # 1
        cli("add", "child task", "-k", "task", "--parent", "1")  # 2
        code, out, _ = cli("tree", "--root", "1")
        assert code == 0 and "child task" in out


class TestTreeByWithFilter:
    """`wl tree --by <axis>` regroups flat; an active node-filter skips groups it empties."""

    def _two_directions(self, cli):
        cli("add", "work item", "-k", "task", "-t", "work")
        cli("add", "home item", "-k", "task", "-t", "personal")

    def test_by_tag_filter_drops_nonmatching_groups(self, cli):
        # use non-generic tags (work/personal are GENERIC_TAGS, excluded from the tag axis)
        cli("add", "alpha item", "-k", "task", "-t", "alpha")
        cli("add", "beta item", "-k", "task", "-t", "beta")
        # node-filter to `alpha` → the `beta` tag group has no surviving rows → skipped
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


class TestDayFilterEmpties:
    def test_day_filter_excludes_everything(self, cli):
        cli("add", "logged task", "-k", "task")
        cli("log", "1", "did some work today")
        # filter to a tag nothing has → the day view renders with no items, still exits clean
        code, out, _ = cli("day", "-t", "nonexistent-tag")
        assert code == 0
