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


class TestColumnStaysConsistentWithProps:
    # FINDING 2: the kind column must never diverge from the kind derived from type.* props,
    # or column-reading SQL lookups disagree with prop-deriving readers (split-brain).
    def test_add_prop_type_para_syncs_column(self, cli, tmp_db):
        cli("add", "x", "--prop", "type.para=project")     # --prop, not --para; kind defaulted task
        assert _kind(tmp_db, 1) == "project"               # column re-derived, not left "task"
        assert _props(tmp_db, 1)["type.para"] == "project"

    def test_set_type_para_syncs_column(self, cli, tmp_db):
        cli("add", "x")                                    # bare task, column=task
        cli("set", "1", "type.para", "project")
        assert _kind(tmp_db, 1) == "project"

    def test_remove_type_para_reverts_column(self, cli, tmp_db):
        cli("add", "x", "--para", "project")
        assert _kind(tmp_db, 1) == "project"
        cli("prop", "rm", "1", "type.para")
        assert _kind(tmp_db, 1) == "task"                  # back to bare default

    def test_add_prop_custom_type_syncs_column(self, cli, tmp_db):
        cli("add", "dinner", "--prop", "type.recipe")
        assert _kind(tmp_db, 1) == "recipe"                # custom kind preserved + column synced


class TestNodeEditKindSyncsTypeProps:
    def test_edit_kind_project_to_task_clears_para(self, cli, tmp_db):
        cli("add", "x", "--para", "project")
        assert _props(tmp_db, 1)["type.para"] == "project"
        cli("node", "edit", "1", "-k", "task")
        assert "type.para" not in _props(tmp_db, 1)        # re-derived: task is bare
        assert _kind(tmp_db, 1) == "task"

    def test_edit_kind_task_to_project_adds_para(self, cli, tmp_db):
        cli("add", "x")                                    # bare task
        cli("node", "edit", "1", "-k", "project")
        assert _props(tmp_db, 1)["type.para"] == "project"

    def test_edit_kind_to_habit_sets_existence(self, cli, tmp_db):
        cli("add", "x", "--para", "project")
        cli("node", "edit", "1", "-k", "habit")
        p = _props(tmp_db, 1)
        assert "type.para" not in p and p["type.habit"] == "true"

    def test_edit_from_custom_kind_clears_stale_type_prop(self, cli, tmp_db):
        cli("add", "x", "--prop", "type.recipe")          # custom kind
        assert _kind(tmp_db, 1) == "recipe"
        cli("node", "edit", "1", "-k", "project")
        p = _props(tmp_db, 1)
        assert "type.recipe" not in p                      # stale custom classification cleared
        assert p["type.para"] == "project" and _kind(tmp_db, 1) == "project"


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


class TestReadersAreColumnFree:
    """Lock the invariant: views/filters derive kind from type.* props, never the kind column.
    Corrupt the column to a bogus value; correct readers must be unaffected."""

    def _bogus_column(self, tmp_db):
        con = tmp_db.db_connect()
        con.execute("UPDATE node SET kind='ZZZ'")
        con.commit(); con.close()

    def test_projects_filter_day_derive_from_props(self, cli, tmp_db):
        cli("add", "Big Project", "--para", "project")          # 1
        cli("add", "a task", "--parent", "1")                   # 2
        cli("add", "morning run", "-k", "habit")                # 3
        self._bogus_column(tmp_db)                              # column now lies
        # wl projects (raw-SQL reader) still finds the project via type.para
        code, out, _ = cli("projects")
        assert code == 0 and "Big Project" in out
        # wl ls --para project still filters correctly
        _, lsout, _ = cli("ls", "--para", "project")
        assert "Big Project" in lsout and "morning run" not in lsout
        # wl kinds derives the counts from props, not the bogus column
        _, kout, _ = cli("kinds")
        assert "ZZZ" not in kout and "project" in kout and "habit" in kout


class TestWorkitemMatchesLegacyKind:
    def test_para_task_with_date_is_still_a_workitem(self, tmp_db):
        # a node with BOTH type.para=task and type.date=day: legacy_kind='task' (para wins),
        # so workitem_sql MUST include it too (the divergence fix).
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        from worklog import db_table as _db, timeutil as _tu, queries, node_types as nt
        nid = _db.insert(con, "node", {"title": "x", "kind": "task", "created_at": _tu.utc_now()})
        queries._upsert_prop(con, nid, "type.para", "task")
        queries._upsert_prop(con, nid, "type.date", "day")
        con.commit()
        props = queries.node_props(con, nid)
        assert nt.legacy_kind(props) == "task"
        row = con.execute(
            f"SELECT 1 FROM node n WHERE n.id=? AND ({queries.workitem_sql('n')})", (nid,)).fetchone()
        assert row is not None      # workitem_sql agrees with legacy_kind — no split-brain
        con.close()


class TestUpgradeAutoBackfill:
    def test_legacy_kind_only_node_is_backfilled_on_next_command(self, cli, tmp_db):
        # a pre-migration node (kind only, no type.*) must get type.* auto-populated so the
        # prop-based readers don't misclassify it after an upgrade.
        tmp_db.ensure_db()
        con = tmp_db.db_connect()
        from worklog import db_table as _db, timeutil as _tu
        pid = _db.insert(con, "node", {"title": "legacy proj", "kind": "project",
                                       "priority": "A", "created_at": _tu.utc_now()})
        con.commit(); con.close()
        cli("ls")   # any command → ensure_db → auto-backfill
        from worklog import queries
        con = tmp_db.db_connect()
        assert queries.node_props(con, pid).get("type.para") == "project"
        # and the prop-based reader now finds it
        con.close()
        _, out, _ = cli("projects")
        assert "legacy proj" in out


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
            nid = _db.insert(con, "node", {"title": f"n{n}", "kind": "task", "created_at": _tu.utc_now()})
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


class TestEnsureTypePropsFutureSafe:
    def test_survives_dropped_kind_column(self, tmp_db):
        # after the eventual kind-column drop, the transitional auto-backfill guard must no-op
        # gracefully (not crash) — and must NOT swallow a real OperationalError.
        tmp_db.ensure_db()
        con = tmp_db.db_connect()
        con.execute("DROP INDEX idx_node_kind")   # the index must go before the column can be dropped
        con.execute("ALTER TABLE node DROP COLUMN kind")
        con.commit(); con.close()
        tmp_db.ensure_db()   # guard hits 'no such column: kind' → returns cleanly, no raise
