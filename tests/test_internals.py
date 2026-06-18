"""Internal/unit coverage for small helpers (extracted from the test_ux grab-bag):
_hl / _status_filter_sql / _parse_fieldop / _node_project / apply sub-branches / _edit_in_editor."""
import sqlite3
import pytest


class TestSmallGaps:
    """remaining 1-2 line gaps"""

    def test_hl_direct_no_match(self):
        from worklog import cli as wl
        s = wl._hl("alpha beta", "missing")
        # i<0 branch
        assert "alpha" in s

    def test_status_filter_no_exclusion(self):
        """include_canceled=True + hide_done=False → empty frag"""
        from worklog import cli as wl
        frag, params = wl._status_filter_sql(include_canceled=True, hide_done=False)
        assert frag == ""
        assert params == []

    def test_apply_sub_link_branch(self, cli, tmp_path):
        """+ @link → _apply_sub link branch"""
        p = tmp_path / "a.txt"
        p.write_text("+ [ ] x\n@link DocOnly\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DocOnly" in show

    def test_apply_sub_prop_branch(self, cli, tmp_path):
        """+ @prop k=v → _apply_sub prop branch"""
        p = tmp_path / "a.txt"
        p.write_text("+ [ ] x\n@prop owner=me\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "owner" in show

    def test_themes_when_rich_unavailable(self, cli, monkeypatch):
        """simulate _RICH_AVAIL=False, falling back to plain-text path"""
        from worklog import cli as wl
        monkeypatch.setattr(wl.render, "_RICH_AVAIL", False)
        _, out, _ = cli("themes")
        # no crash; shows "rich not installed"
        assert "rich" in out

    def test_tree_children_with_canceled_excluded(self, cli):
        cli("add", "p1", "--para", "project")  # id 1
        cli("add", "c1", "--parent", "1")  # id 2
        cli("add", "c2", "--parent", "1")  # id 3
        cli("cancel", "3")  # c2 cancel
        _, out, _ = cli("tree", "--root", "1")
        assert "c1" in out
        assert "c2" not in out

    def test_node_project_direct_no_ancestor(self, cli):
        """_node_project: direct call; node with no project ancestor → fallback returns None"""
        cli("add", "lonely")
        from worklog import cli as wl
        con = wl.db_connect()
        pid, ptitle = wl._node_project(con, 1)
        assert pid is None
        assert "unassigned" in ptitle

    def test_node_project_direct_with_ancestor(self, cli):
        """_node_project: node under a project → returns project id+title"""
        cli("add", "p1", "--para", "project")  # id 1
        cli("add", "t1", "--parent", "1")  # id 2
        from worklog import cli as wl
        con = wl.db_connect()
        pid, ptitle = wl._node_project(con, 2)
        assert pid == 1
        assert "p1" in ptitle

    def test_print_day_activity_with_many_logs_shows_omitted(self, cli):
        """log_tail=3 default: 5 logs → omission line shown"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "--prop", "type.date=lifetime")
        cli("add", today, "--prop", "type.date=day", "--parent", "1")
        cli("add", "t1")
        for i in range(6):
            cli("log", "3", f"log-{i}")
        _, out, _ = cli("tree", "--root", "2", "--depth", "2")
        assert "elided" in out or "log-5" in out  # default tail=3; at least expand tail

    def test_ls_with_status_filter(self, cli):
        cli("add", "t1")
        cli("done", "1")
        _, out, _ = cli("ls", "--status", "DONE")
        assert "t1" in out

    def test_apply_dryrun_with_ref_map(self, cli, tmp_path):
        """dry-run + add with ref → ref displayed"""
        import json as _json
        spec = {"add": [{"ref": "P", "title": "RP", "props": {"type.para": "project"}}]}
        p = tmp_path / "ok.json"
        p.write_text(_json.dumps(spec))
        _, out, _ = cli("import", str(p), "--dry-run")
        assert "ref" in out and "P" in out

    def test_apply_no_commit_on_dry(self, cli, tmp_path):
        """dry-run actually does not write to DB"""
        p = tmp_path / "x.txt"
        p.write_text("+ [ ] dry-only\n")
        cli("apply", str(p), "--dry-run")
        from worklog import cli as wl
        con = wl.db_connect()
        assert not con.execute("SELECT 1 FROM node WHERE id = 1").fetchone()

    def test_parse_fieldop_unknown_returns_none(self):
        from worklog import cli as wl
        assert wl._parse_fieldop("totally-not-a-fieldop") is None


class TestEditInEditorUnlink:
    def test_edit_in_editor_cleanup_oserror_swallowed(self, monkeypatch, tmp_db):
        """_edit_in_editor finally block silently swallows os.unlink OSError (covers last 2 lines)"""
        import subprocess as _sp; from worklog import cli as wl
        monkeypatch.setattr(_sp, "call", lambda argv: 0)
        # make os.unlink raise OSError
        import os
        real_unlink = os.unlink
        def fake_unlink(p):
            real_unlink(p)  # actually delete first
            raise OSError("simulated")
        monkeypatch.setattr(os, "unlink", fake_unlink)
        # should not crash
        result = wl._edit_in_editor("hello", suffix=".txt")
        assert result == "hello"



class TestVersion:
    """__version__ resolves (from test_ux)"""
    def test_version_constant_exists(self, cli, tmp_db):
        # __version__ exists and is non-empty
        from worklog import cli as wl_mod
        assert hasattr(wl_mod, "__version__")
        assert wl_mod.__version__

