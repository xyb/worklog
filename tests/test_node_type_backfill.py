"""Backfill: derive the type.*/date.* namespace for existing nodes from their legacy
kind, through the validated write API. Additive (kind untouched) + idempotent.

The ``kind`` column was dropped in migration 0011, so these tests run against a
**v10** schema (migrations 0001–0010) where the column still exists — that is the
exact state migration 0011 sees when it invokes this backfill module. ``_v10_db``
builds that schema (mirrors ``test_migrations.TestMigration0011DropKind._v10_db``)."""
from __future__ import annotations

from worklog import node_type_backfill as bf, db_table as _db, timeutil as _tu


def _v10_db(tmp_path):
    """A fresh DB brought up to v10 (kind column still present) using only 0001–0010 —
    the pre-0011 state the backfill module is designed to read."""
    import shutil
    import pathlib
    from worklog import db
    real = pathlib.Path(db.__file__).parent / "migrations"
    migs = tmp_path / "migs"
    migs.mkdir()
    for p in db.migration_files(real):
        if int(p.stem.split("_", 1)[0]) <= 10:
            shutil.copy(p, migs / p.name)
    con = db.db_connect(tmp_path / "t.db")
    db.run_migrations(con, migs)
    assert db.db_version(con) == 10
    return con


def _legacy(con, kind, title):
    """Insert a pre-type.* node the old way (kind column only, no type.* props)."""
    return _db.insert(con, "node", {"title": title, "kind": kind, "created_at": _tu.utc_now()})


def _props(con, nid):
    return {r["key"]: r["value"] for r in _db.query(con, "prop", cols="key, value", node_id=nid)}


def _seed(con):
    ids = {
        "area": _legacy(con, "area", "Health"),
        "project": _legacy(con, "project", "Website revamp"),
        "task": _legacy(con, "task", "a plain task"),
        "habit": _legacy(con, "habit", "workout"),
        "meetlog": _legacy(con, "meetlog", "[meetlog] sync"),
        "year": _legacy(con, "year", "2026"),
        "month": _legacy(con, "month", "2026-06"),
        "day": _legacy(con, "day", "2026-06-14"),
        "week": _legacy(con, "week", "2026-W24"),
        "quarter": _legacy(con, "quarter", "2026-Q2"),
        "lifetime": _legacy(con, "lifetime", "lifetime"),
        "signal": _legacy(con, "signal", "some signal"),
    }
    con.commit()
    return ids


class TestBackfill:
    def test_para_roles_backfilled(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert _props(con, ids["area"])["type.para"] == "area"
        assert _props(con, ids["project"])["type.para"] == "project"
        con.close()

    def test_plain_task_stays_bare(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert "type.para" not in _props(con, ids["task"])   # loose default: no role
        con.close()

    def test_soft_types_backfilled(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert _props(con, ids["habit"])["type.habit"] == "true"
        assert _props(con, ids["meetlog"])["type.meetlog"] == "true"
        con.close()

    def test_self_describing_time_levels(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        py = _props(con, ids["year"])
        assert py["type.date"] == "year" and py["date.period"] == "2026"
        assert "date.start" not in py
        pm = _props(con, ids["month"])
        assert pm["type.date"] == "month" and pm["date.period"] == "2026-06"
        pd = _props(con, ids["day"])
        assert pd["date.period"] == "2026-06-14"
        con.close()

    def test_explicit_span_time_levels(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        pw = _props(con, ids["week"])
        assert pw["type.date"] == "week" and pw["date.period"] == "2026-W24"
        assert pw["date.start"] and pw["date.end"]      # explicit span pinned
        pq = _props(con, ids["quarter"])
        assert pq["date.start"] == "2026-04-01" and pq["date.end"] == "2026-06-30"
        con.close()

    def test_lifetime_has_level_no_period(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        p = _props(con, ids["lifetime"])
        assert p["type.date"] == "lifetime"
        assert "date.period" not in p
        con.close()

    def test_custom_kind_preserved_and_roundtrips(self, tmp_path):
        # a custom kind is preserved as type.<kind> (not collapsed to bare task) → round-trips,
        # so it is NOT reported as retired
        con = _v10_db(tmp_path)
        rid = _legacy(con, "recipe", "carbonara")
        sid = _legacy(con, "signal", "dead kind")
        con.commit()
        counts, ok, mismatches, retired, period_lost = bf.migrate_and_verify(con)
        assert _props(con, rid)["type.recipe"] == "true"
        assert ok is True and mismatches == []
        assert not any(r[0] == rid for r in retired)      # custom preserved, not retired
        assert any(r[0] == sid and r[1] == "signal" for r in retired)  # signal still retired→bare
        con.close()

    def test_signal_and_kind_left_intact(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert _props(con, ids["signal"]) == {}          # retired kind → no type.*
        # kind column is left untouched (additive backfill)
        assert _db.get(con, "node", ids["project"])["kind"] == "project"
        con.close()

    def test_decade_backfills_period_and_span(self, tmp_path):
        con = _v10_db(tmp_path)
        dc = _legacy(con, "decade", "2020s")
        con.commit()
        bf.backfill_node_types(con)
        p = _props(con, dc)
        assert p["type.date"] == "decade"
        assert p["date.period"] == "2020s"
        assert p["date.start"] == "2020-01-01" and p["date.end"] == "2029-12-31"
        con.close()

    def test_decade_unparseable_title_level_only(self, tmp_path):
        con = _v10_db(tmp_path)
        dc = _legacy(con, "decade", "the twenties")   # no canonical token
        con.commit()
        bf.backfill_node_types(con)
        p = _props(con, dc)
        assert p["type.date"] == "decade"
        assert "date.period" not in p
        con.close()

    def test_time_node_with_unparseable_title_gets_level_only(self, tmp_path):
        con = _v10_db(tmp_path)
        wk = _legacy(con, "week", "some freeform week title")   # no canonical YYYY-Www token
        con.commit()
        bf.backfill_node_types(con)
        p = _props(con, wk)
        assert p["type.date"] == "week"
        assert "date.period" not in p          # nothing parseable → left unset (kind still has it)
        con.close()

    def test_tombstoned_nodes_are_backfilled(self, tmp_path):
        # a soft-deleted project must still get type.para, so restoring it after the column
        # is dropped doesn't silently reclassify it as a bare task
        con = _v10_db(tmp_path)
        pid = _legacy(con, "project", "archived project")
        con.commit()
        from worklog import queries
        _db.delete(con, "node", id=pid)        # soft-delete node + its prop spokes
        _db.delete(con, "prop", node_id=pid)   # (soft_delete_node would do this; simulate it)
        con.commit()
        bf.backfill_node_types(con)
        # C1: the type.para is written but kept TOMBSTONED to match the dead node (not live)
        assert "type.para" not in _props(con, pid)                                   # not live
        assert queries.node_props(con, pid, include_deleted=True)["type.para"] == "project"
        row = con.execute("SELECT deleted_at FROM prop WHERE node_id=? AND key='type.para'",
                          (pid,)).fetchone()
        assert row["deleted_at"] is not None    # prop tombstone matches the node's
        ok, mismatches, retired, period_lost = bf.verify_roundtrip(con)
        assert ok is True                       # the tombstoned node still round-trips
        con.close()

    def test_restamp_tombstones_all_backfilled_props_on_dead_node_incl_custom(self, tmp_path):
        # The re-stamp must tombstone EVERY prop backfill wrote on a tombstoned node so prop
        # state matches node state — including a custom type.<x> (an earlier reserved-keys-only
        # scope wrongly left it live). A live prop on a dead node is the inconsistency we fix.
        con = _v10_db(tmp_path)
        rid = _legacy(con, "recipe", "archived recipe")     # custom kind → backfill writes type.recipe
        con.commit()
        _db.delete(con, "node", id=rid)                     # soft-delete node + spoke props
        con.commit()
        bf.backfill_node_types(con)
        row = con.execute("SELECT deleted_at FROM prop WHERE node_id=? AND key='type.recipe'",
                          (rid,)).fetchone()
        assert row is not None and row["deleted_at"] is not None   # tombstoned to match the dead node
        # no LIVE reserved/type/date prop may hang off any tombstoned node
        bad = con.execute(
            "SELECT COUNT(*) c FROM prop p JOIN node n ON n.id=p.node_id "
            "WHERE n.deleted_at IS NOT NULL AND p.deleted_at IS NULL "
            "AND (p.key LIKE 'type.%' OR p.key LIKE 'date.%')").fetchone()["c"]
        assert bad == 0
        con.close()

    def test_idempotent(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        c1 = bf.backfill_node_types(con)
        before = _props(con, ids["project"])
        c2 = bf.backfill_node_types(con)
        after = _props(con, ids["project"])
        assert before == after
        assert c1 == c2                                   # same counts, no double-writing
        con.close()


class TestMigrateAndVerify:
    def test_every_known_node_roundtrips(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        counts, ok, mismatches, retired, period_lost = bf.migrate_and_verify(con)
        assert ok is True
        assert mismatches == []
        # signal is retired by design → reported as retired, not a mismatch
        assert any(r[0] == ids["signal"] and r[1] == "signal" for r in retired)
        # every KNOWN-kind node derives back to its original column kind
        from worklog import node_types as nt, queries
        for n in _db.query(con, "node", cols="id, kind"):
            if n["kind"] in nt.KNOWN_KINDS:
                assert queries.node_type_from_props(queries.node_props(con, n["id"])) == n["kind"]
        con.close()

    def test_period_loss_is_surfaced(self, tmp_path):
        con = _v10_db(tmp_path)
        wk = _legacy(con, "week", "freeform week with no canonical token")
        con.commit()
        counts, ok, mismatches, retired, period_lost = bf.migrate_and_verify(con)
        assert ok is True                       # kind still round-trips (type.date=week)
        assert any(p[0] == wk and p[1] == "week" for p in period_lost)   # but period loss reported
        con.close()

    def test_verify_catches_conflicting_prebackfill_prop(self, tmp_path):
        # A1: a node entering backfill with a CONFLICTING reserved prop (e.g. kind=week but a
        # stray type.para=project) — backfill's sync would rewrite the column week→project, and a
        # verify against the live column would tautologically pass. migrate_and_verify snapshots
        # the ORIGINAL kind first, so it now CATCHES the loss (ok=False).
        con = _v10_db(tmp_path)
        wk = _legacy(con, "week", "2026-W24")
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (?,?,?)", (wk, "type.para", "project"))
        con.commit()
        counts, ok, mismatches, retired, period_lost = bf.migrate_and_verify(con)
        assert ok is False                       # the gate refuses — original 'week' was not preserved
        assert any(m[0] == wk and m[1] == "week" for m in mismatches)
        con.close()

    def test_verify_catches_a_corrupted_node(self, tmp_path):
        con = _v10_db(tmp_path)
        ids = _seed(con)
        bf.backfill_node_types(con)
        # corrupt: drop the project's type.para so it would derive to "task", not "project"
        _db.delete(con, "prop", node_id=ids["project"], key="type.para")
        con.commit()
        ok, mismatches, retired, period_lost = bf.verify_roundtrip(con)
        assert ok is False
        assert any(m[0] == ids["project"] and m[1] == "project" for m in mismatches)
        con.close()
