"""Tests for projects (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestProjects:
    def _seed(self, cli):
        cli("add", "month", "-k", "month")                                              # 1
        cli("add", "projectA", "-k", "project", "-p", "A", "-t", "projA", "--parent", "1")  # 2
        cli("add", "projectB done", "-k", "project", "-p", "B", "-t", "projB", "--parent", "1")  # 3
        cli("add", "A task1", "-k", "task", "-t", "projA", "--parent", "1")             # 4
        cli("add", "A task2", "-k", "task", "-t", "projA", "--parent", "1")             # 5
        cli("add", "A subtask", "-k", "task", "--parent", "2")                           # 6 (structural subtask)
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
        cli("add", "P1", "-k", "project")
        cli("add", "t1", "-k", "task", "--parent", "1")
        cli("log", "2", "p")
        _, out, _ = cli("projects", "--since", "2020-01-01")
        assert "P1" in out


class TestProjectsJson:
    def test_projects_json_array_with_counts(self, cli):
        cli("add", "proj", "-k", "project", "-p", "A")          # 1
        cli("add", "t1", "-k", "task", "--parent", "1")          # 2
        cli("add", "t2", "-k", "task", "--parent", "1")          # 3
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


class TestKinds:
    def test_kinds_lists_with_counts_in_canonical_order(self, cli):
        cli("add", "t1", "-k", "task"); cli("add", "t2", "-k", "task")
        cli("add", "p", "-k", "project"); cli("add", "a", "-k", "area")
        code, out, _ = cli("kinds")
        assert code == 0
        assert "task" in out and "2" in out and "project" in out and "area" in out
        # canonical order: area (container) before task (leaf)
        assert out.index("area") < out.index("task")

    def test_kinds_json(self, cli):
        cli("add", "t", "-k", "task")
        import json
        _, out, _ = cli("kinds", "-o", "json")
        d = json.loads(out)
        assert {"kind": "task", "count": 1} in d
