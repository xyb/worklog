"""Backfill: derive the type.*/date.* namespace for existing nodes from their legacy
kind, through the validated write API. Additive (kind untouched) + idempotent."""
from __future__ import annotations

from worklog import node_type_backfill as bf, db_table as _db, timeutil as _tu


def _legacy(con, kind, title):
    """Insert a pre-type.* node the old way (kind only, no type.* props)."""
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
    def test_para_roles_backfilled(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert _props(con, ids["area"])["type.para"] == "area"
        assert _props(con, ids["project"])["type.para"] == "project"
        con.close()

    def test_plain_task_stays_bare(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert "type.para" not in _props(con, ids["task"])   # loose default: no role
        con.close()

    def test_soft_types_backfilled(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert _props(con, ids["habit"])["type.habit"] == "true"
        assert _props(con, ids["meetlog"])["type.meetlog"] == "true"
        con.close()

    def test_self_describing_time_levels(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        py = _props(con, ids["year"])
        assert py["type.date"] == "year" and py["date.period"] == "2026"
        assert "date.start" not in py
        pm = _props(con, ids["month"])
        assert pm["type.date"] == "month" and pm["date.period"] == "2026-06"
        pd = _props(con, ids["day"])
        assert pd["date.period"] == "2026-06-14"

    def test_explicit_span_time_levels(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        pw = _props(con, ids["week"])
        assert pw["type.date"] == "week" and pw["date.period"] == "2026-W24"
        assert pw["date.start"] and pw["date.end"]      # explicit span pinned
        pq = _props(con, ids["quarter"])
        assert pq["date.start"] == "2026-04-01" and pq["date.end"] == "2026-06-30"

    def test_lifetime_has_level_no_period(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        p = _props(con, ids["lifetime"])
        assert p["type.date"] == "lifetime"
        assert "date.period" not in p
        con.close()

    def test_signal_and_kind_left_intact(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        assert _props(con, ids["signal"]) == {}          # retired kind → no type.*
        # kind column is left untouched (additive backfill)
        assert _db.get(con, "node", ids["project"])["kind"] == "project"
        con.close()

    def test_decade_gets_level_only(self, tmp_db):
        # decade has no canonical title token we trust to parse → level only (rare; fix up by hand)
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        dc = _legacy(con, "decade", "2020s")
        con.commit()
        bf.backfill_node_types(con)
        p = _props(con, dc)
        assert p["type.date"] == "decade"
        assert "date.period" not in p
        con.close()

    def test_time_node_with_unparseable_title_gets_level_only(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        wk = _legacy(con, "week", "some freeform week title")   # no canonical YYYY-Www token
        con.commit()
        bf.backfill_node_types(con)
        p = _props(con, wk)
        assert p["type.date"] == "week"
        assert "date.period" not in p          # nothing parseable → left unset (kind still has it)
        con.close()

    def test_tombstoned_nodes_are_backfilled(self, tmp_db):
        # a soft-deleted project must still get type.para, so restoring it after the column
        # is dropped doesn't silently reclassify it as a bare task
        tmp_db.ensure_db(); con = tmp_db.db_connect()
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

    def test_idempotent(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        c1 = bf.backfill_node_types(con)
        before = _props(con, ids["project"])
        c2 = bf.backfill_node_types(con)
        after = _props(con, ids["project"])
        assert before == after
        assert c1 == c2                                   # same counts, no double-writing
        con.close()


class TestMigrateAndVerify:
    def test_every_known_node_roundtrips(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
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
                assert nt.legacy_kind(queries.node_props(con, n["id"])) == n["kind"]
        con.close()

    def test_period_loss_is_surfaced(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        wk = _legacy(con, "week", "freeform week with no canonical token")
        con.commit()
        counts, ok, mismatches, retired, period_lost = bf.migrate_and_verify(con)
        assert ok is True                       # kind still round-trips (type.date=week)
        assert any(p[0] == wk and p[1] == "week" for p in period_lost)   # but period loss reported
        con.close()

    def test_verify_catches_a_corrupted_node(self, tmp_db):
        tmp_db.ensure_db(); con = tmp_db.db_connect()
        ids = _seed(con)
        bf.backfill_node_types(con)
        # corrupt: drop the project's type.para so it would derive to "task", not "project"
        _db.delete(con, "prop", node_id=ids["project"], key="type.para")
        con.commit()
        ok, mismatches, retired, period_lost = bf.verify_roundtrip(con)
        assert ok is False
        assert any(m[0] == ids["project"] and m[1] == "project" for m in mismatches)
        con.close()


class TestMigrateTypesCommand:
    def test_command_backfills_and_filter_works(self, cli, tmp_db):
        tmp_db.ensure_db()
        con = tmp_db.db_connect()
        _seed(con)
        con.close()
        code, out, _ = cli("migrate-types")
        assert code == 0
        # after backfill, the type.para filter finds the legacy project
        _, lsout, _ = cli("ls", "--para", "project")
        assert "Website revamp" in lsout
