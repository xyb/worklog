"""`wl add` populates the type.* namespace that is the SOLE source of node
classification (the legacy `kind` column was dropped in migration 0011): --para
writes type.para, --prop sets any other classification (soft type / time level /
custom), and a bare add stays a type-less plain task. Kind is always *derived* from
props via node_types.legacy_kind — never stored in a column."""
from __future__ import annotations

from worklog import node_types as nt, queries


def _props(tmp_db, nid):
    con = tmp_db.db_connect()
    rows = con.execute("SELECT key, value FROM prop WHERE node_id=? AND deleted_at IS NULL",
                       (nid,)).fetchall()
    con.close()
    return {r["key"]: r["value"] for r in rows}


def _kind(tmp_db, nid):
    """The node's DERIVED kind (column-free) — what every reader now uses."""
    con = tmp_db.db_connect()
    k = nt.legacy_kind(queries.node_props(con, nid))
    con.close()
    return k


class TestParaCreate:
    def test_para_writes_type_para(self, cli, tmp_db):
        cli("add", "Website revamp", "--para", "project")
        assert _props(tmp_db, 1)["type.para"] == "project"
        assert _kind(tmp_db, 1) == "project"            # derived from the prop

    def test_para_area(self, cli, tmp_db):
        cli("add", "an area", "--para", "area")
        assert _kind(tmp_db, 1) == "area"

    def test_bare_add_has_no_type_para(self, cli, tmp_db):
        cli("add", "just a task")
        p = _props(tmp_db, 1)
        assert "type.para" not in p                     # loose default: a bare node, no role
        assert _kind(tmp_db, 1) == "task"


class TestKindIsDerivedFromProps:
    def test_add_prop_type_para_derives_kind(self, cli, tmp_db):
        cli("add", "x", "--prop", "type.para=project")     # --prop, not --para
        assert _kind(tmp_db, 1) == "project"
        assert _props(tmp_db, 1)["type.para"] == "project"

    def test_set_type_para_derives_kind(self, cli, tmp_db):
        cli("add", "x")                                    # bare task
        cli("set", "1", "type.para", "project")
        assert _kind(tmp_db, 1) == "project"

    def test_remove_type_para_reverts_to_bare(self, cli, tmp_db):
        cli("add", "x", "--para", "project")
        assert _kind(tmp_db, 1) == "project"
        cli("prop", "rm", "1", "type.para")
        assert _kind(tmp_db, 1) == "task"                  # back to bare default

    def test_add_prop_custom_type_derives_kind(self, cli, tmp_db):
        cli("add", "dinner", "--prop", "type.recipe")
        assert _kind(tmp_db, 1) == "recipe"                # custom kind preserved


class TestNodeEditPara:
    def test_edit_para_project_to_task(self, cli, tmp_db):
        cli("add", "x", "--para", "project")
        assert _props(tmp_db, 1)["type.para"] == "project"
        cli("node", "edit", "1", "--para", "task")
        assert _props(tmp_db, 1)["type.para"] == "task"    # role replaced
        assert _kind(tmp_db, 1) == "task"

    def test_edit_para_task_to_project(self, cli, tmp_db):
        cli("add", "x", "--para", "task")
        cli("node", "edit", "1", "--para", "project")
        assert _props(tmp_db, 1)["type.para"] == "project"


class TestSoftTypeCreate:
    def test_habit_via_prop(self, cli, tmp_db):
        cli("add", "morning workout", "--prop", "type.habit=true")
        assert _props(tmp_db, 1)["type.habit"] == "true"
        assert _kind(tmp_db, 1) == "habit"

    def test_meetlog_via_prop(self, cli, tmp_db):
        cli("add", "[meetlog] sync", "--prop", "type.meetlog=true")
        assert _props(tmp_db, 1)["type.meetlog"] == "true"
        assert _kind(tmp_db, 1) == "meetlog"


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

    def test_time_level_via_prop(self, cli, tmp_db):
        cli("add", "2026-06-14", "--prop", "type.date=day")
        assert _props(tmp_db, 1)["type.date"] == "day"
        assert _kind(tmp_db, 1) == "day"


class TestAddEchoesDerivedKind:
    def test_echo_reflects_prop_classification(self, cli):
        # the ✓ add line reports the DERIVED kind (post --prop), not a stored column
        _, out, _ = cli("add", "morning run", "--prop", "type.habit")
        assert "habit" in out


class TestJsonExposesTypeProps:
    def test_type_props_are_plain_props_no_special_field(self, cli):
        # DESIGN: type.* gets no special treatment — it rides along in `props` with every other
        # key/value. JSON still exposes a DERIVED top-level `kind` (computed from props), but no
        # synthesized `type` / `para` field.
        cli("add", "x", "--para", "project", "--prop", "type.meetlog=dating")
        import json
        _, out, _ = cli("show", "1", "-o", "json")
        d = json.loads(out)
        assert d["props"]["type.para"] == "project"
        assert d["props"]["type.meetlog"] == "dating"
        assert "type" not in d
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


class TestReadersAreColumnFree:
    """Lock the invariant: views/filters derive kind from type.* props. With the kind column gone,
    these readers must work purely off props."""

    def test_projects_filter_day_derive_from_props(self, cli, tmp_db):
        cli("add", "Big Project", "--para", "project")          # 1
        cli("add", "a task", "--parent", "1")                   # 2
        cli("add", "morning run", "--prop", "type.habit=true")  # 3
        # wl projects (raw-SQL reader) finds the project via type.para
        code, out, _ = cli("projects")
        assert code == 0 and "Big Project" in out
        # wl ls --para project filters correctly
        _, lsout, _ = cli("ls", "--para", "project")
        assert "Big Project" in lsout and "morning run" not in lsout
        # wl kinds derives the counts from props
        _, kout, _ = cli("kinds")
        assert "project" in kout and "habit" in kout


class TestWorkitemMatchesLegacyKind:
    def test_para_task_with_date_is_still_a_workitem(self, tmp_db):
        # a node with BOTH type.para=task and type.date=day: legacy_kind='task' (para wins),
        # so workitem_sql MUST include it too (the divergence fix).
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        from worklog import db_table as _db, timeutil as _tu, queries, node_types as nt
        nid = _db.insert(con, "node", {"title": "x", "created_at": _tu.utc_now()})
        queries._upsert_prop(con, nid, "type.para", "task")
        queries._upsert_prop(con, nid, "type.date", "day")
        con.commit()
        props = queries.node_props(con, nid)
        assert nt.legacy_kind(props) == "task"
        row = con.execute(
            f"SELECT 1 FROM node n WHERE n.id=? AND ({queries.workitem_sql('n')})", (nid,)).fetchone()
        assert row is not None      # workitem_sql agrees with legacy_kind — no split-brain
        con.close()


class TestWorkitemSqlEqualsLegacyKind:
    """Drift guard (root fix for the recurring SQL-vs-Python divergence): workitem_sql must equal
    legacy_kind(props) IN (task,habit,meetlog) for EVERY combination of the classification keys —
    exhaustively, so no future edge combo (para+date, habit+custom, bare 'type.', …) can escape."""

    def test_equivalence_exhaustive(self, tmp_db):
        import itertools
        from worklog import db_table as _db, timeutil as _tu, queries, node_types as nt
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        # each dimension's possible presence/value; the cross-product covers every precedence path
        dims = {
            "type.para":    [None, "area", "project", "task"],
            "type.date":    [None, "day"],
            "type.habit":   [None, "true"],
            "type.meetlog": [None, "true"],
            "type.recipe":  [None, "true"],   # a custom type.<x>
            "type.":        [None, "x"],      # the degenerate bare-suffix key
        }
        keys = list(dims)
        n = 0
        for combo in itertools.product(*dims.values()):
            props = {k: v for k, v in zip(keys, combo) if v is not None}
            n += 1
            nid = _db.insert(con, "node", {"title": f"n{n}", "created_at": _tu.utc_now()})
            for k, v in props.items():
                con.execute("INSERT INTO prop (node_id, key, value) VALUES (?,?,?)", (nid, k, v))
            con.commit()
            sql_match = con.execute(
                f"SELECT 1 FROM node n WHERE n.id=? AND ({queries.workitem_sql('n')})", (nid,)).fetchone() is not None
            py_match = nt.legacy_kind(props) in ("task", "habit", "meetlog")
            assert sql_match == py_match, \
                f"combo {props}: workitem_sql={sql_match} but legacy_kind={nt.legacy_kind(props)!r}"
        assert n == 4 * 2 * 2 * 2 * 2 * 2   # 128 combinations, all checked
        con.close()


class TestKindColumnIsGone:
    def test_node_table_has_no_kind_column(self, tmp_db):
        # migration 0011 dropped it; the schema must not carry it anymore.
        tmp_db.ensure_db()
        con = tmp_db.db_connect()
        cols = {r["name"] for r in con.execute("PRAGMA table_info(node)").fetchall()}
        con.close()
        assert "kind" not in cols
