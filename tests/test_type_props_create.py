"""`wl add` populates the type.* namespace that replaces kind: --para writes
type.para, the legacy -k dual-writes the matching type.*, a bare add stays a
type-less plain task, and --prop sets props at creation (validated)."""
from __future__ import annotations

from worklog import queries


def _props(tmp_db, nid):
    con = tmp_db.db_connect()
    rows = con.execute("SELECT key, value FROM prop WHERE node_id=? AND deleted_at IS NULL",
                       (nid,)).fetchall()
    con.close()
    return {r["key"]: r["value"] for r in rows}


def _kind(tmp_db, nid):
    con = tmp_db.db_connect()
    k = con.execute("SELECT kind FROM node WHERE id=?", (nid,)).fetchone()["kind"]
    con.close()
    return k


class TestParaCreate:
    def test_para_writes_type_para_and_kind(self, cli, tmp_db):
        cli("add", "Website revamp", "--para", "project")
        assert _props(tmp_db, 1)["type.para"] == "project"
        assert _kind(tmp_db, 1) == "project"            # dual-write keeps legacy readers green

    def test_para_overrides_default_kind(self, cli, tmp_db):
        cli("add", "an area", "--para", "area")
        assert _kind(tmp_db, 1) == "area"

    def test_legacy_kind_also_dual_writes_type_para(self, cli, tmp_db):
        cli("add", "proj", "-k", "project")
        assert _props(tmp_db, 1)["type.para"] == "project"

    def test_bare_add_has_no_type_para(self, cli, tmp_db):
        cli("add", "just a task")
        p = _props(tmp_db, 1)
        assert "type.para" not in p                     # loose default: a bare node, no role
        assert _kind(tmp_db, 1) == "task"


class TestSoftTypeCreate:
    def test_habit_writes_existence_prop(self, cli, tmp_db):
        cli("add", "morning workout", "-k", "habit")
        assert _props(tmp_db, 1)["type.habit"] == "true"

    def test_meetlog_writes_existence_prop(self, cli, tmp_db):
        cli("add", "[meetlog] sync", "-k", "meetlog")
        assert _props(tmp_db, 1)["type.meetlog"] == "true"


class TestPropAtCreate:
    def test_prop_subclass_value(self, cli, tmp_db):
        cli("add", "dinner notes", "--prop", "type.meetlog=dating")
        assert _props(tmp_db, 1)["type.meetlog"] == "dating"

    def test_prop_existence_bare_key(self, cli, tmp_db):
        cli("add", "habit-ish", "--prop", "type.habit")
        assert _props(tmp_db, 1)["type.habit"] == "true"

    def test_prop_repeatable(self, cli, tmp_db):
        cli("add", "x", "--prop", "release=v0.7.0", "--prop", "type.meetlog=dating")
        p = _props(tmp_db, 1)
        assert p["release"] == "v0.7.0"
        assert p["type.meetlog"] == "dating"

    def test_bad_reserved_prop_rejected_at_create(self, cli, tmp_db):
        code, out, err = cli("add", "x", "--prop", "type.para=projekt")
        assert code != 0
        assert "type.para" in (out + err)


class TestJsonExposesTypeProps:
    def test_type_props_are_plain_props_no_special_field(self, cli):
        # DESIGN: type.* gets no special treatment in JSON — it rides along in `props`
        # with every other key/value, never promoted to its own top-level field.
        cli("add", "x", "--para", "project", "--prop", "type.meetlog=dating")
        import json
        _, out, _ = cli("show", "1", "-o", "json")
        d = json.loads(out)
        assert d["props"]["type.para"] == "project"
        assert d["props"]["type.meetlog"] == "dating"
        assert "type" not in d            # no synthesized top-level "type"/"kind"-style field
        assert "para" not in d


class TestLsParaFilter:
    def _seed(self, cli):
        cli("add", "the project", "--para", "project")   # 1
        cli("add", "a plain task")                        # 2 (no type.para)
        cli("add", "the area", "--para", "area")          # 3

    def test_filter_project(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--para", "project")
        assert "#1" in out
        assert "#2" not in out and "#3" not in out

    def test_filter_area(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--para", "area")
        assert "#3" in out
        assert "#1" not in out and "#2" not in out

    def test_bare_task_not_matched_by_para_task(self, cli):
        self._seed(cli)
        _, out, _ = cli("ls", "--para", "task")
        # #2 is a bare task with no type.para → not matched (loose default has no role)
        assert "#2" not in out
