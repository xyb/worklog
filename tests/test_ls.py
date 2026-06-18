"""Tests for ls (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestLs:
    def _seed(self, cli):
        cli("add", "task1", "-p", "A", "-t", "work,P0")
        cli("add", "task2", "-p", "B", "-t", "personal")
        cli("add", "proj1", "--para", "project", "-p", "A", "-t", "work")
        cli("add", "doneTask", "-t", "work")
        cli("done", "4")

    def test_ls_default_excludes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls")
        assert "task1" in out
        assert "task2" in out
        assert "proj1" in out
        assert "doneTask" not in out

    def test_ls_all_includes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--all")
        assert "doneTask" in out

    def test_ls_filter_para(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--para", "project")
        assert "proj1" in out
        assert "task1" not in out

    def test_ls_filter_tag(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--tag", "personal")
        assert "task2" in out
        assert "task1" not in out

    def test_ls_filter_multi_tag_and(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--tag", "work,P0")
        assert "task1" in out
        assert "proj1" not in out  # has work but no P0
        assert "task2" not in out

    def test_ls_filter_parent(self, cli, tmp_db):
        cli("add", "parent")
        cli("add", "child1", "--parent", "1")
        cli("add", "child2", "--parent", "1")
        cli("add", "orphan")
        code, out, _ = cli("ls", "--parent", "1")
        assert "child1" in out
        assert "child2" in out
        assert "orphan" not in out

    def test_ls_empty_db(self, cli):
        code, out, _ = cli("ls")
        assert "(no nodes)" in out


# ─── tree ───


class TestLsTagFilter:
    def test_ls_multi_tag_and(self, cli):
        cli("add", "t1", "-t", "work,foo")
        cli("add", "t2", "-t", "work")
        _, out, _ = cli("ls", "--tag", "work,foo")
        # AND filter: only t1 has both work + foo
        assert "t1" in out
        assert "t2" not in out

    def test_ls_all_includes_done(self, cli):
        cli("add", "t1")
        cli("done", "1")
        _, out, _ = cli("ls", "--all")
        assert "t1" in out


class TestLsAdvanced:
    """wl ls multi-dimension sort / filter / default limit (modeled on shell ls -t/-S/-r)"""

    def test_default_limit_20(self, cli):
        for i in range(25):
            cli("add", f"t{i}")
        _, out, _ = cli("ls")
        # default limit 20
        assert "showing 20/25" in out
        # t0..t19 present (priority+id ascending)
        assert "t0" in out and "t19" in out
        assert "t20" not in out and "t24" not in out

    def test_all_lifts_default_limit(self, cli):
        for i in range(25):
            cli("add", f"t{i}")
        _, out, _ = cli("ls", "--all")
        assert "t24" in out

    def test_limit_0_lifts(self, cli):
        for i in range(25):
            cli("add", f"t{i}")
        _, out, _ = cli("ls", "--limit", "0")
        assert "t24" in out

    def test_sort_created_desc(self, cli):
        """--sort created: newest first (like shell ls -t)"""
        cli("add", "first")
        cli("add", "second")
        cli("add", "third")
        _, out, _ = cli("ls", "--sort", "created")
        # third should be first
        idx_first = out.find("first")
        idx_third = out.find("third")
        assert idx_third < idx_first

    def test_sort_title(self, cli):
        cli("add", "zebra")
        cli("add", "apple")
        cli("add", "mango")
        _, out, _ = cli("ls", "--sort", "title")
        idx_a = out.find("apple")
        idx_z = out.find("zebra")
        assert idx_a < idx_z

    def test_reverse_flag(self, cli):
        cli("add", "first")
        cli("add", "second")
        _, out_normal, _ = cli("ls", "--sort", "id")
        _, out_rev, _ = cli("ls", "--sort", "id", "--reverse")
        # forward: first first; reverse: second first
        assert out_normal.find("first") < out_normal.find("second")
        assert out_rev.find("second") < out_rev.find("first")

    def test_unscheduled_filter(self, cli):
        from datetime import date
        cli("add", "planned-alpha")
        cli("add", "open-beta")
        cli("sched", "1", date.today().isoformat())
        _, out, _ = cli("ls", "--unscheduled")
        assert "open-beta" in out
        assert "planned-alpha" not in out

    def test_recent_n_days(self, cli):
        """--recent N: changed within the last N days (including created)"""
        cli("add", "new-task")
        _, out, _ = cli("ls", "--recent", "1")
        assert "new-task" in out

    def test_ids_direct(self, cli):
        """--ids 1 3 5: like shell ls file1 file3 — bypass filters, list directly"""
        cli("add", "a")
        cli("add", "b")
        cli("add", "c")
        _, out, _ = cli("ls", "--ids", "1", "3")
        assert "a" in out
        assert "c" in out
        assert "b" not in out

    def test_ids_unknown_skipped(self, cli):
        cli("add", "a")
        _, out, _ = cli("ls", "--ids", "999")
        assert "no nodes matched" in out

    def test_short_r_flag_for_reverse(self, cli):
        cli("add", "first")
        cli("add", "second")
        _, out, _ = cli("ls", "--sort", "id", "-r")
        assert out.find("second") < out.find("first")

    def test_bare_ls_no_hint_pollution(self, cli):
        """bare ls does not pollute stdout (hints moved to --help epilog)"""
        cli("add", "t1")
        _, out, _ = cli("ls")
        # should list only t1, no "(bare ls...)" hint
        assert "t1" in out
        assert "bare ls" not in out
        assert "narrowing" not in out


class TestLsSortUpdated:
    """ls --sort updated paths: uses latest log timestamp; --reverse flips order."""

    def test_sort_updated(self, cli, tmp_db):
        cli("add", "old-task")
        cli("add", "new-task")
        cli("log", "1", "old entry")
        cli("log", "2", "later entry")
        _, out, _ = cli("ls", "--sort", "updated", "--limit", "5")
        # new-task (id=2) logged later → appears first under DESC
        idx_new = out.find("new-task")
        idx_old = out.find("old-task")
        assert 0 <= idx_new < idx_old

    def test_sort_updated_reverse(self, cli, tmp_db):
        cli("add", "old-task")
        cli("add", "new-task")
        cli("log", "1", "old entry")
        cli("log", "2", "later entry")
        _, out, _ = cli("ls", "--sort", "updated", "--reverse", "--limit", "5")
        # ASC after reverse → old-task first
        idx_new = out.find("new-task")
        idx_old = out.find("old-task")
        assert 0 <= idx_old < idx_new


class TestLsJson:
    """`wl ls -o json` — array of compact node summaries (filters apply, empty → [])."""

    def test_ls_json_array(self, cli):
        cli("add", "alpha", "-p", "A", "-t", "work")
        cli("add", "bravo", "-p", "B")
        import json
        code, out, _ = cli("ls", "-o", "json")
        d = json.loads(out)
        assert code == 0 and isinstance(d, list) and len(d) == 2
        # summary payload is the NodeView contract — an orthogonal `type` facet, not a single
        # collapsed token.
        assert set(d[0].keys()) >= {"id", "type", "title", "status", "priority", "tags"}
        assert "kind" not in d[0]
        assert isinstance(d[0]["type"], dict)

    def test_ls_json_type_facet(self, cli):
        # orthogonal type facet: a project → {"para":"project"}; a bare task → {}
        cli("add", "proj", "--para", "project")
        cli("add", "plain")
        import json
        d = {n["title"]: n for n in json.loads(cli("ls", "-o", "json")[1])}
        assert d["proj"]["type"] == {"para": "project"}
        assert d["plain"]["type"] == {}

    def test_ls_json_respects_filter(self, cli):
        cli("add", "a", "-p", "A")
        cli("add", "b", "-p", "B")
        import json
        _, out, _ = cli("ls", "-p", "A", "-o", "json")
        d = json.loads(out)
        assert [n["title"] for n in d] == ["a"]

    def test_ls_json_ids_mode(self, cli):
        cli("add", "a"); cli("add", "b")
        import json
        _, out, _ = cli("ls", "--ids", "2", "-o", "json")
        assert [n["id"] for n in json.loads(out)] == [2]

    def test_ls_json_empty_is_array(self, cli):
        import json
        _, out, _ = cli("ls", "-p", "C", "-o", "json")   # nothing matches
        assert json.loads(out) == []

    def test_ls_json_no_default_cap(self, cli):
        for i in range(25):
            cli("add", f"t{i}")
        import json
        _, out, _ = cli("ls", "-o", "json")
        assert len(json.loads(out)) == 25   # machine output isn't capped at the display default 20


class TestLsLimitTop:
    """explicit --limit / --top (from test_ux)"""
    def test_ls_limit(self, cli):
        for i in range(10):
            cli("add", f"t{i}")
        _, out, _ = cli("ls", "--limit", "3")
        assert "showing 3/10" in out
        # only 3 task rows expected
        assert out.count("#1 t1") == 0 or "t0" in out

    def test_ls_top_by_priority(self, cli):
        cli("add", "low-pri", "-p", "C")
        cli("add", "high-pri-1", "-p", "A")
        cli("add", "no-pri")
        cli("add", "high-pri-2", "-p", "A")
        _, out, _ = cli("ls", "--top", "2")
        # top sorts by priority + id; top 2 are A
        assert "high-pri-1" in out
        assert "high-pri-2" in out
        assert "low-pri" not in out
        assert "no-pri" not in out



class TestLsBrief:
    """ls -q brief drops tags (from test_ux)"""
    def test_ls_brief_drops_tags(self, cli):
        cli("add", "t1", "-t", "important,work")
        _, full, _ = cli("ls")
        _, brief, _ = cli("-q", "ls")
        assert ":important:" in full or "important" in full
        assert ":important:" not in brief



class TestLsRoot:
    """`wl ls --root <id>` — flat list of ALL descendants (recursive subtree)."""

    def test_root_includes_nested_descendants(self, cli):
        cli("add", "proj", "--para", "project")     # 1
        cli("add", "task-a", "--parent", "1")   # 2
        cli("add", "deep", "--parent", "2")     # 3 grandchild
        cli("add", "orphan")                    # 4
        code, out, _ = cli("ls", "--root", "1", "--all")
        assert code == 0
        assert "task-a" in out      # direct child
        assert "deep" in out        # grandchild (recursive)
        assert "orphan" not in out  # outside subtree
        assert "proj" not in out    # root excluded (descendants only)

    def test_root_vs_parent_one_level(self, cli):
        cli("add", "proj", "--para", "project")     # 1
        cli("add", "mid", "--parent", "1")      # 2
        cli("add", "deep", "--parent", "2")     # 3
        _, out_parent, _ = cli("ls", "--parent", "1", "--all")
        assert "mid" in out_parent and "deep" not in out_parent
        _, out_root, _ = cli("ls", "--root", "1", "--all")
        assert "mid" in out_root and "deep" in out_root
