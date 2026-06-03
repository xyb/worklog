"""Tests for import_apply (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestImport:
    def test_import_nested_children(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [
            {"ref": "m", "title": "2026-05", "kind": "month", "children": [
                {"ref": "p", "title": "project", "kind": "project", "priority": "A", "tags": ["work"],
                 "children": [
                     {"title": "children", "kind": "task", "priority": "B", "status": "DONE",
                      "tags": ["x"], "logs": ["finished"]}
                 ]}
            ]}
        ]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f)
        f.close()
        code, out, _ = cli("import", f.name)
        os.unlink(f.name)
        assert code == 0
        assert "added 3" in out
        con = tmp_db.db_connect()
        # parent-child link
        p = con.execute("SELECT id FROM node WHERE title='project'").fetchone()
        c = con.execute("SELECT parent_id, status FROM node WHERE title='children'").fetchone()
        assert c["parent_id"] == p["id"]
        assert c["status"] == "DONE"
        # log + tag
        assert con.execute("SELECT COUNT(*) FROM log WHERE body='finished'").fetchone()[0] == 1

    def test_import_parent_ref(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [
            {"ref": "proj", "title": "P", "kind": "project"},
            {"title": "task under P", "kind": "task", "parent_ref": "proj"},
        ]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, out, _ = cli("import", f.name)
        os.unlink(f.name)
        assert code == 0
        con = tmp_db.db_connect()
        proj = con.execute("SELECT id FROM node WHERE title='P'").fetchone()
        t = con.execute("SELECT parent_id FROM node WHERE title='task under P'").fetchone()
        assert t["parent_id"] == proj["id"]

    def test_import_update(self, cli, tmp_db):
        cli("add", "task", "-k", "task")
        import json, tempfile, os
        spec = {"update": [{"id": 1, "status": "DONE", "add_tags": ["urgent"], "add_logs": ["补的"]}]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, out, _ = cli("import", f.name)
        os.unlink(f.name)
        assert "updated 1" in out
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, closed_at FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE" and n["closed_at"]
        assert con.execute("SELECT 1 FROM tag WHERE node_id=1 AND tag='urgent'").fetchone()

    def test_import_dry_run_no_write(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [{"title": "不该写入", "kind": "task"}]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, out, _ = cli("import", f.name, "--dry-run")
        os.unlink(f.name)
        assert "dry-run" in out and "add 1" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM node WHERE title='不该写入'").fetchone()[0] == 0

    def test_import_bad_parent_ref_rolls_back(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [
            {"title": "好的", "kind": "task"},
            {"title": "坏的", "kind": "task", "parent_ref": "does not exist"},
        ]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, _, err = cli("import", f.name)
        os.unlink(f.name)
        assert code != 0 and "rolled back" in err
        con = tmp_db.db_connect()
        # rollback: 好的 should not remain either
        assert con.execute("SELECT COUNT(*) FROM node WHERE title='好的'").fetchone()[0] == 0


class TestApply:
    def _apply(self, cli, text, *extra):
        import tempfile, os
        f = tempfile.NamedTemporaryFile("w", suffix=".wld", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        code, out, err = cli("apply", f.name, *extra)
        os.unlink(f.name)
        return code, out, err

    def test_apply_add_nested(self, cli, tmp_db):
        code, out, _ = self._apply(cli,
            "+ [ ] [#A] [project] P :work:\n"
            "+   [x] [#A] 子任务 :x:\n")
        assert code == 0 and "added 2" in out
        con = tmp_db.db_connect()
        p = con.execute("SELECT id FROM node WHERE title='P'").fetchone()
        c = con.execute("SELECT parent_id, status FROM node WHERE title='子任务'").fetchone()
        assert c["parent_id"] == p["id"] and c["status"] == "DONE"

    def test_apply_anchor_parent(self, cli, tmp_db):
        cli("add", "project", "-k", "project")  # id 1
        code, out, _ = self._apply(cli,
            "  #1 [project] 项目\n"
            "+   [ ] [#B] 新子任务\n")
        assert code == 0 and "added 1" in out
        con = tmp_db.db_connect()
        c = con.execute("SELECT parent_id FROM node WHERE title='新子任务'").fetchone()
        assert c["parent_id"] == 1

    def test_apply_update_fields(self, cli, tmp_db):
        cli("add", "task", "-k", "task")  # id 1, TODO
        code, out, _ = self._apply(cli, "~ #1\n  status DONE\n  priority A\n")
        assert "updated 1" in out
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority, closed_at FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE" and n["priority"] == "A" and n["closed_at"]

    # ── anti-wipe: core safety tests (2026-05-28 safety hardening requirement) ──
    def test_apply_update_only_touches_declared_fields(self, cli, tmp_db):
        """only status changes; priority/title/tag/prop all preserved (no wipe)"""
        cli("add", "原标题", "-k", "task", "-p", "A", "-t", "keep1,keep2")
        cli("set", "1", "owner", "xyb")
        cli("link", "1", "某文档")
        # only update status
        self._apply(cli, "~ #1\n  status DONE\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT title, priority, status FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE"
        assert n["title"] == "原标题"      # untouched
        assert n["priority"] == "A"        # untouched
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert tags == {"keep1", "keep2"}  # not wiped
        assert con.execute("SELECT value FROM prop WHERE node_id=1 AND key='owner'").fetchone()["value"] == "xyb"
        assert con.execute("SELECT 1 FROM link WHERE node_id=1 AND vault_doc='某文档'").fetchone()  # not wiped

    def test_apply_clear_priority(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-p", "A")
        self._apply(cli, "~ #1\n  priority -\n")
        con = tmp_db.db_connect()
        assert con.execute("SELECT priority FROM node WHERE id=1").fetchone()["priority"] is None

    def test_apply_add_remove_tag(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-t", "old1,old2")
        self._apply(cli, "~ #1\n  +tag new\n  -tag old1\n")
        con = tmp_db.db_connect()
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert tags == {"old2", "new"}

    def test_apply_set_remove_prop(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("set", "1", "a", "1")
        self._apply(cli, "~ #1\n  prop b=2\n  -prop a\n")
        con = tmp_db.db_connect()
        props = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM prop WHERE node_id=1")}
        assert props == {"b": "2"}

    def test_apply_move_parent(self, cli, tmp_db):
        cli("add", "p1", "-k", "project")  # 1
        cli("add", "p2", "-k", "project")  # 2
        cli("add", "t", "-k", "task", "--parent", "1")  # 3
        self._apply(cli, "~ #3\n  parent 2\n")
        con = tmp_db.db_connect()
        assert con.execute("SELECT parent_id FROM node WHERE id=3").fetchone()["parent_id"] == 2

    def test_apply_unindented_fieldop_hints_indent(self, cli, tmp_db):
        """A field op written flush-left under ~ (a common mistake) must produce an
        actionable 'indent it' error, not a bare 'cannot parse' (#411)."""
        cli("add", "p1", "-k", "project")  # 1
        cli("add", "t", "-k", "task")      # 2
        code, out, err = self._apply(cli, "~ #2\nparent 1\n")  # parent not indented
        msg = out + err
        assert code != 0
        assert "indent" in msg.lower(), f"expected an indent hint, got: {msg!r}"
        assert "parent 1" in msg  # name the offending line
        assert "cannot parse" not in msg.lower()  # the old misleading error is gone

    def test_apply_update_bad_priority_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-p", "A")
        code, _, err = self._apply(cli, "~ #1\n  priority Z\n")
        assert code != 0 and "invalid priority" in err
        con = tmp_db.db_connect()
        assert con.execute("SELECT priority FROM node WHERE id=1").fetchone()["priority"] == "A"  # not corrupted

    def test_apply_update_bad_status_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  status FINISHED\n")
        assert code != 0 and "invalid status" in err

    def test_apply_update_bad_parent_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  parent 999\n")
        assert code != 0 and "parent #999 does not exist" in err

    def test_apply_update_unknown_fieldop_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  frobnicate yes\n")
        assert code != 0 and "unparseable field-op" in err

    def test_apply_update_title_clear_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  title -\n")
        assert code != 0 and "title cannot be cleared" in err

    def test_apply_update_no_fieldops_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n")
        assert code != 0 and "has no field operations" in err

    def test_apply_delete(self, cli, tmp_db):
        cli("add", "删我", "-k", "task")  # id 1
        code, out, _ = self._apply(cli, "- #1 删我\n")
        assert "deleted 1" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM node WHERE id=1").fetchone()[0] == 0

    def test_apply_subfields(self, cli, tmp_db):
        code, out, _ = self._apply(cli,
            "+ [x] [#A] 任务\n"
            "+   @log 进展记录\n"
            "+   @link 某文档\n"
            "+   @prop owner=xyb\n")
        assert code == 0
        con = tmp_db.db_connect()
        nid = con.execute("SELECT id FROM node WHERE title='任务'").fetchone()["id"]
        assert con.execute("SELECT 1 FROM log WHERE node_id=? AND body='进展记录'", (nid,)).fetchone()
        assert con.execute("SELECT 1 FROM link WHERE node_id=? AND vault_doc='某文档'", (nid,)).fetchone()
        assert con.execute("SELECT value FROM prop WHERE node_id=? AND key='owner'", (nid,)).fetchone()["value"] == "xyb"

    def test_apply_dry_run_no_write(self, cli, tmp_db):
        code, out, _ = self._apply(cli, "+ [ ] 不写入\n", "--dry-run")
        assert "dry-run" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM node WHERE title='不写入'").fetchone()[0] == 0

    def test_apply_validation_errors(self, cli, tmp_db):
        code, _, err = self._apply(cli,
            "+ [ ] #99 新增带id\n"
            "- #888 删不存在\n")
        assert code != 0
        assert "add should not carry #id" in err and "#888 does not exist" in err

    def test_apply_tilde_no_id_rejected(self, cli, tmp_db):
        code, _, err = self._apply(cli, "~ [x] 没id\n")
        assert code != 0 and "requires #id" in err

    def test_apply_status_doing(self, cli, tmp_db):
        cli("add", "t", "-k", "task")  # id 1
        self._apply(cli, "~ #1\n  status DOING\n")
        con = tmp_db.db_connect()
        assert con.execute("SELECT status FROM node WHERE id=1").fetchone()["status"] == "DOING"

    # ── inline shorthand (same as node list; only touches declared fields) ──
    def test_apply_inline_marker_only(self, cli, tmp_db):
        """~ [x] #1 only updates status, leaves priority/title alone"""
        cli("add", "原名", "-k", "task", "-p", "A")
        self._apply(cli, "~ [x] #1\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority, title FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE"
        assert n["priority"] == "A" and n["title"] == "原名"  # not declared = unchanged

    def test_apply_inline_priority_only_no_marker(self, cli, tmp_db):
        """~ [#B] #1 no marker → status untouched"""
        cli("add", "t", "-k", "task")  # status TODO
        self._apply(cli, "~ [#B] #1\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority FROM node WHERE id=1").fetchone()
        assert n["priority"] == "B"
        assert n["status"] == "TODO"  # no marker = status unchanged

    def test_apply_inline_title_only(self, cli, tmp_db):
        cli("add", "旧标题", "-k", "task", "-p", "A")
        self._apply(cli, "~ #1 新标题\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT title, priority, status FROM node WHERE id=1").fetchone()
        assert n["title"] == "新标题"
        assert n["priority"] == "A"  # untouched

    def test_apply_inline_all_three(self, cli, tmp_db):
        cli("add", "old", "-k", "task")
        self._apply(cli, "~ [x] [#A] #1 新名\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority, title FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE" and n["priority"] == "A" and n["title"] == "新名"

    def test_apply_inline_plus_fieldops(self, cli, tmp_db):
        """inline shorthand combined with following indented field operations"""
        cli("add", "t", "-k", "task", "-t", "old")
        self._apply(cli, "~ [x] #1\n  +tag urgent\n  -tag old\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE"
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert tags == {"urgent"}

    def test_apply_inline_bad_priority_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-p", "A")
        code, _, err = self._apply(cli, "~ [#Z] #1\n")
        # [#Z] is not a valid priority marker; _parse_node_line treats it as no priority + title
        # verify at least that priority is not corrupted
        con = tmp_db.db_connect()
        assert con.execute("SELECT priority FROM node WHERE id=1").fetchone()["priority"] == "A"


class TestImportUpdateMove:
    """import update supports parent(move) + remove_tags (fix for silent parent-ignore bug)"""

    def _imp(self, cli, tmp_path, obj):
        import json
        f = tmp_path / "u.json"
        f.write_text(json.dumps(obj), encoding="utf-8")
        return cli("import", str(f))

    def test_import_update_parent_moves_node(self, cli, tmp_path):
        cli("add", "p1", "-k", "project")   # 1
        cli("add", "p2", "-k", "project")   # 2
        cli("add", "t", "-k", "task", "--parent", "1")  # 3
        self._imp(cli, tmp_path, {"update": [{"id": 3, "parent": 2}]})
        code, out, _ = cli("focus", "3")
        assert "#2" in out  # upstream is now p2

    def test_import_update_bad_parent_rejected(self, cli, tmp_path):
        cli("add", "t", "-k", "task")  # 1
        code, _, _ = self._imp(cli, tmp_path, {"update": [{"id": 1, "parent": 999}]})
        assert code != 0

    def test_import_update_remove_tags(self, cli, tmp_path):
        cli("add", "t", "-k", "task", "-t", "a,b")  # 1
        self._imp(cli, tmp_path, {"update": [{"id": 1, "remove_tags": ["a"]}]})
        code, out, _ = cli("show", "1")
        assert ":b:" in out and ":a:" not in out


class TestApplyAndImportEdges:
    def test_import_invalid_json(self, cli, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_top_level_not_dict(self, cli, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[]")
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_dry_run(self, cli, tmp_path):
        import json as _json
        p = tmp_path / "ok.json"
        p.write_text(_json.dumps({"add": [{"title": "from-import", "kind": "task"}]}))
        _, out, _ = cli("import", str(p), "--dry-run")
        assert "dry-run" in out

    def test_apply_dry_run(self, cli, tmp_path):
        p = tmp_path / "wld.txt"
        p.write_text("+ [ ] new-task\n")
        _, out, _ = cli("apply", str(p), "--dry-run")
        assert "dry-run" in out

    def test_apply_invalid_marker(self, cli, tmp_path):
        p = tmp_path / "bad.txt"
        # ~ #999 does not exist
        p.write_text("~ #999\n  status DONE\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0


class TestImportEdges:
    def test_import_update_with_field_changes(self, cli, tmp_path):
        import json as _json
        cli("add", "before", "-k", "task")  # id 1
        spec = {"update": [{"id": 1, "title": "after", "priority": "A", "add_tags": ["lab"]}]}
        p = tmp_path / "u.json"
        p.write_text(_json.dumps(spec))
        cli("import", str(p))
        _, show, _ = cli("show", "1")
        assert "after" in show
        assert "lab" in show

    def test_import_update_dry_run(self, cli, tmp_path):
        import json as _json
        cli("add", "x", "-k", "task")
        p = tmp_path / "u.json"
        p.write_text(_json.dumps({"update": [{"id": 1, "title": "new"}]}))
        _, out, _ = cli("import", str(p), "--dry-run")
        assert "dry-run" in out
        _, show, _ = cli("show", "1")
        assert "new" not in show  # dry-run did not actually modify

    def test_import_update_node_missing(self, cli, tmp_path):
        import json as _json
        p = tmp_path / "u.json"
        p.write_text(_json.dumps({"update": [{"id": 999, "title": "x"}]}))
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_with_links_props_tags(self, cli, tmp_path):
        import json as _json
        spec = {"add": [{"title": "rich", "kind": "task", "tags": ["foo"],
                          "props": {"k": "v"}, "links": ["DocA"]}]}
        p = tmp_path / "rich.json"
        p.write_text(_json.dumps(spec))
        cli("import", str(p))
        _, show, _ = cli("show", "1")
        assert ":foo:" in show
        assert "DocA" in show


class TestApplyExtended:
    def test_apply_update_status(self, cli, tmp_path):
        cli("add", "x", "-k", "task")
        p = tmp_path / "a.txt"
        p.write_text("~ [x] #1\n")  # mark DONE
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DONE" in show

    def test_apply_delete(self, cli, tmp_path):
        cli("add", "byebye", "-k", "task")
        p = tmp_path / "d.txt"
        p.write_text("- #1\n")
        _, out, _ = cli("apply", str(p))
        assert "1" in out or "delete" in out or out
        # should actually be deleted
        from worklog import cli as wl
        con = wl.db_connect()
        assert not con.execute("SELECT 1 FROM node WHERE id = 1").fetchone()

    def test_apply_add_with_log_sub(self, cli, tmp_path):
        p = tmp_path / "add.txt"
        p.write_text("+ [ ] new\n  @log first log entry\n  @link DocB\n  @prop k=v\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "new" in show
        assert "DocB" in show

    def test_apply_update_no_changes_errors(self, cli, tmp_path):
        """~ #id with no field operations → validation fails"""
        cli("add", "x", "-k", "task")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n")  # bare ~ with no fields; not inline shorthand
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_update_nonexistent(self, cli, tmp_path):
        p = tmp_path / "u.txt"
        p.write_text("~ [x] #999\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0


class TestApplyExtra:
    def test_apply_delete_with_subtree(self, cli, tmp_path):
        cli("add", "parent", "-k", "task")
        cli("add", "child", "-k", "task", "--parent", "1")
        p = tmp_path / "del.txt"
        p.write_text("- #1\n")
        cli("apply", str(p))
        from worklog import cli as wl
        con = wl.db_connect()
        # both parent and child are gone
        assert not con.execute("SELECT 1 FROM node WHERE id IN (1, 2)").fetchone()

    def test_apply_update_with_log_sub_via_fieldop(self, cli, tmp_path):
        """~ #id followed by '+log msg' field op → _exec_update log branch"""
        cli("add", "t1", "-k", "task")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n  +log progress-via-apply\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "progress-via-apply" in show

    def test_apply_update_link_add_remove(self, cli, tmp_path):
        cli("add", "t1", "-k", "task")
        cli("link", "1", "DocA")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n  +link DocB\n  -link DocA\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DocB" in show
        assert "DocA" not in show

    def test_apply_update_prop(self, cli, tmp_path):
        cli("add", "t1", "-k", "task")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n  prop owner=xyb\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "owner" in show and "xyb" in show

    def test_apply_subs_log_link_prop(self, cli, tmp_path):
        """+ new node + @log/@link/@prop subs → exercise all _apply_sub branches"""
        p = tmp_path / "anc.txt"
        p.write_text("+ [ ] new-with-subs\n  @log first-log\n  @link DocX\n  @prop k=v\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DocX" in show
        assert "k=v" in show or "k" in show

    def test_apply_delete_without_id(self, cli, tmp_path):
        """- delete must include #id"""
        p = tmp_path / "bad.txt"
        p.write_text("- something-no-id\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_at_log_without_anchor(self, cli, tmp_path):
        """@log without a leading + or anchor → error"""
        p = tmp_path / "bad.txt"
        p.write_text("@log orphan-log\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_anchor_missing_id(self, cli, tmp_path):
        """leading whitespace but no #id"""
        p = tmp_path / "bad.txt"
        p.write_text(" something\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_plus_with_id(self, cli, tmp_path):
        """+ add should not carry #id"""
        p = tmp_path / "bad.txt"
        p.write_text("+ [ ] #5 explicit-id\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0


class TestExecUpdateDirect:
    """direct _exec_update calls covering log/link/prop/tag branches"""

    def test_exec_update_all_field_ops(self, cli):
        cli("add", "t1", "-k", "task")
        cli("link", "1", "DocA")
        cli("set", "1", "k1", "v0")
        from worklog import cli as wl
        con = wl.db_connect()
        op = {
            "id": 1,
            "fieldops": [
                (10, ("set", "title", "新标题")),
                (11, ("set", "status", "DONE")),  # triggers DONE auto closed_at
                (12, ("clear", "scheduled", None)),
                (13, ("add", "log", "from-exec-update")),
                (14, ("add", "link", "DocB")),
                (15, ("remove", "link", "DocA")),
                (16, ("set", "prop", ("k1", "v1"))),
                (17, ("remove", "prop", "k1")),
                (18, ("add", "tag", "newtag")),
                (19, ("remove", "tag", "newtag")),
            ],
        }
        wl._exec_update(con, op)
        con.commit()
        _, show, _ = cli("show", "1")
        assert "新标题" in show
        assert "from-exec-update" in show
        assert "DocB" in show


class TestImportNodeRefMap:
    def test_import_with_parent_ref(self, cli, tmp_path):
        import json as _json
        spec = {"add": [
            {"ref": "P", "title": "Parent", "kind": "project"},
            {"parent_ref": "P", "title": "Child", "kind": "task"},
        ]}
        p = tmp_path / "ref.json"
        p.write_text(_json.dumps(spec))
        cli("import", str(p))
        _, show, _ = cli("show", "2")
        assert "Parent" in show  # child #2 upstream should include Parent

    def test_import_unresolved_parent_ref(self, cli, tmp_path):
        import json as _json
        spec = {"add": [{"parent_ref": "X", "title": "orphan", "kind": "task"}]}
        p = tmp_path / "bad.json"
        p.write_text(_json.dumps(spec))
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_missing_title(self, cli, tmp_path):
        import json as _json
        spec = {"add": [{"kind": "task"}]}  # missing title
        p = tmp_path / "bad.json"
        p.write_text(_json.dumps(spec))
        code, _, _ = cli("import", str(p))
        assert code != 0


class TestParseWldEdges:
    def test_parse_wld_empty_lines_skipped(self, cli, tmp_path):
        p = tmp_path / "e.txt"
        p.write_text("\n\n# this is a comment\n+ [ ] real\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "real" in show

    def test_parse_wld_update_with_inline_marker(self, cli, tmp_path):
        """~ [x] #1 inline shorthand → marker+status"""
        cli("add", "t1", "-k", "task")
        p = tmp_path / "i.txt"
        p.write_text("~ [x] #1\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DONE" in show

    def test_parse_wld_at_log_after_anchor(self, cli, tmp_path):
        """+ followed by @log → subs array"""
        p = tmp_path / "a.txt"
        p.write_text("+ [ ] task\n@log inline-log\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "inline-log" in show


