"""Tests for projects (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestProjects:
    def _seed(self, cli):
        cli("add", "month", "--prop", "type.date=month")                                              # 1
        cli("add", "projectA", "--para", "project", "-p", "A", "-t", "projA", "--parent", "1")  # 2
        cli("add", "projectB done", "--para", "project", "-p", "B", "-t", "projB", "--parent", "1")  # 3
        cli("add", "A task1", "-t", "projA", "--parent", "1")             # 4
        cli("add", "A task2", "-t", "projA", "--parent", "1")             # 5
        cli("add", "A subtask", "--parent", "2")                           # 6 (structural subtask)
        cli("done", "4")
        cli("done", "3")  # mark projectB done

    def test_projects_lists_active(self, cli):
        self._seed(cli)
        code, out, _ = cli("projects")
        assert code == 0
        assert "projectA" in out
        # projectB is DONE, not listed by default
        assert "projectB" not in out

    def test_projects_all_includes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("projects", "--all")
        assert "projectB" in out

    def test_projects_stats(self, cli):
        self._seed(cli)
        code, out, _ = cli("projects")
        # projectA: A task1(done) + A task2(todo) + A subtask(todo, structural) = 3, done 1
        line = [l for l in out.split("\n") if "projectA" in l][0]
        assert "done 1/3" in line
        assert "todo 2" in line

    def test_projects_empty(self, cli):
        code, out, _ = cli("projects")
        assert "no active projects" in out


class TestProjectFilters:
    def test_projects_since(self, cli):
        cli("add", "P1", "--para", "project")
        cli("add", "t1", "--parent", "1")
        cli("log", "2", "p")
        _, out, _ = cli("projects", "--since", "2020-01-01")
        assert "P1" in out


class TestProjectsJson:
    def test_projects_json_array_with_counts(self, cli):
        cli("add", "proj", "--para", "project", "-p", "A")          # 1
        cli("add", "t1", "--parent", "1")          # 2
        cli("add", "t2", "--parent", "1")          # 3
        cli("done", "2")
        import json
        code, out, _ = cli("projects", "-o", "json")
        d = json.loads(out)
        assert code == 0 and isinstance(d, list) and len(d) == 1
        p = d[0]
        assert p["id"] == 1 and p["title"] == "proj"
        assert p["counts"]["done"] == 1 and p["counts"]["total"] == 2
        assert "latest_activity" in p

    def test_projects_json_empty_is_array(self, cli):
        import json
        _, out, _ = cli("projects", "-o", "json")
        assert json.loads(out) == []


class TestTypes:
    def test_types_groups_raw_facets_by_key(self, cli):
        cli("add", "t1"); cli("add", "t2")   # bare tasks carry NO type.* prop → absent from output
        cli("add", "p", "--para", "project"); cli("add", "a", "--para", "area")
        code, out, _ = cli("types")
        assert code == 0
        # raw type.para facet with per-value counts; no collapse, no auto-mapped "task" for bares
        assert "type.para" in out and "project" in out and "area" in out

    def test_types_json(self, cli):
        cli("add", "p", "--para", "project")   # a bare task would have no facet → []
        import json
        _, out, _ = cli("types", "-o", "json")
        d = json.loads(out)
        assert {"key": "type.para", "value": "project", "count": 1} in d

    def test_types_date_ordered_by_level_lifetime_last(self, cli):
        # type.date values render in time-level order (finest first → lifetime last), not by count
        cli("add", "L", "--prop", "type.date=lifetime")
        cli("add", "d1", "--prop", "type.date=day"); cli("add", "d2", "--prop", "type.date=day")
        cli("add", "w", "--prop", "type.date=week")
        line = next(l for l in cli("types")[1].splitlines() if l.startswith("type.date"))
        assert line.index("day") < line.index("week") < line.index("lifetime")

    def test_types_empty_value_facet_shows_count_only(self, cli):
        # a custom existence facet stored with no sub-value ("") shows just its count, no leading
        # space — so the column lines up with valued facets (e.g. type.habit "true N")
        cli("add", "c", "--prop", "type.pet")
        line = next(l for l in cli("types")[1].splitlines() if l.startswith("type.pet"))
        # body is just "1" (count) at the same column as valued facets — not " 1" (leading space)
        assert line == f"{'type.pet':13} 1"


class TestVocabLists:
    """wl tags / props / metrics — cross-node 'list the vocabulary in use' commands."""

    def test_tags(self, cli):
        cli("add", "a", "-t", "work")
        cli("add", "b", "-t", "work,urgent")
        code, out, _ = cli("tags")
        assert code == 0 and "work" in out and "urgent" in out
        assert out.index("work") < out.index("urgent")   # most-used first (work=2, urgent=1)

    def test_props_alpha_grouped(self, cli):
        cli("add", "a")
        cli("set", "1", "owner", "xyb"); cli("set", "1", "github.pr", "5")
        _, out, _ = cli("props")
        assert "github.pr" in out and "owner" in out
        assert out.index("github.pr") < out.index("owner")   # alphabetical (namespaces group)

    def test_metrics(self, cli):
        cli("add", "a")
        cli("log", "1", "x", "--metric", "pullups 8")
        _, out, _ = cli("metrics")
        assert "pullups" in out

    def test_vocab_json(self, cli):
        cli("add", "a", "-t", "work")
        import json
        _, out, _ = cli("tags", "-o", "json")
        assert {"tag": "work", "count": 1} in json.loads(out)

    def test_props_json(self, cli):
        cli("add", "a")
        cli("set", "1", "owner", "xyb")
        import json
        _, out, _ = cli("props", "-o", "json")
        assert {"key": "owner", "count": 1} in json.loads(out)

    def test_metrics_json(self, cli):
        cli("add", "a")
        cli("log", "1", "x", "--metric", "pullups 8")
        import json
        _, out, _ = cli("metrics", "-o", "json")
        assert {"tag": "pullups", "count": 1} in json.loads(out)

    def test_empty(self, cli):
        _, out, _ = cli("tags")
        assert "(none)" in out


class TestTypesEmpty:
    def test_types_on_empty_db(self, cli):
        code, out, _ = cli("types")
        assert code == 0 and "no type.*/date.* props yet" in out


class TestProjectsLimitWindow:
    """--limit + --week window (from test_ux)"""
    def test_projects_limit(self, cli):
        for i in range(5):
            cli("add", f"p{i}", "--para", "project")
        _, out, _ = cli("projects", "--limit", "2")
        assert "(showing 2/5)" in out

    def test_projects_window_week(self, cli):
        """projects uses the window parent parser; --week resolves to a since cutoff"""
        cli("add", "old", "--para", "project")
        cli("add", "t-old", "--parent", "1")
        cli("log", "2", "old", "--date", "2020-01-01")
        cli("add", "new", "--para", "project")
        cli("add", "t-new", "--parent", "3")
        cli("log", "4", "today")
        # use the current week for wl
        from datetime import date
        today = date.today()
        iso_week = today.isocalendar()
        wk = f"{iso_week[0]}-W{iso_week[1]:02d}"
        _, out, _ = cli("projects", "--week", wk)
        assert "new" in out
        assert "old" not in out

