"""方案 C time model: time nodes are identified by (type.date level + date.period),
find-or-created on demand, never linked by parent_id (belonging is computed from
date). Explicit-span levels (week/quarter/decade) store date.start/date.end;
self-describing levels (year/month/day) don't; lifetime is a date-less singleton."""
from __future__ import annotations

import pytest

from worklog import timemodel as tm, queries, db_table as _db


@pytest.fixture
def con(tmp_db):
    tmp_db.ensure_db()
    c = tmp_db.db_connect()
    yield c
    c.close()


def _props(c, nid):
    return {r["key"]: r["value"] for r in _db.query(c, "prop", cols="key, value", node_id=nid)}


class TestEnsureCreates:
    def test_day_node_shape(self, con):
        nid = tm.ensure_time_node(con, "day", "2026-06-14")
        con.commit()
        node = _db.get(con, "node", nid)
        assert node["title"] == "2026-06-14"
        assert node["parent_id"] is None        # 方案 C: time nodes carry no parent_id
        p = _props(con, nid)
        assert p["type.date"] == "day"
        assert p["date.period"] == "2026-06-14"
        assert "date.start" not in p             # self-describing level: no stored span
        assert "date.end" not in p

    def test_month_self_describing(self, con):
        nid = tm.ensure_time_node(con, "month", "2026-06")
        p = _props(con, nid)
        assert p["type.date"] == "month"
        assert p["date.period"] == "2026-06"
        assert "date.start" not in p

    def test_week_stores_explicit_span(self, con):
        nid = tm.ensure_time_node(con, "week", "2026-W24")
        p = _props(con, nid)
        assert p["type.date"] == "week"
        assert p["date.period"] == "2026-W24"
        assert p["date.start"] == nt_span("week", "2026-W24")[0]
        assert p["date.end"] == nt_span("week", "2026-W24")[1]

    def test_quarter_stores_explicit_span(self, con):
        nid = tm.ensure_time_node(con, "quarter", "2026-Q2")
        p = _props(con, nid)
        assert p["date.start"] == "2026-04-01"
        assert p["date.end"] == "2026-06-30"

    def test_decade_stores_explicit_span(self, con):
        nid = tm.ensure_time_node(con, "decade", "2020s")
        p = _props(con, nid)
        assert p["date.start"] == "2020-01-01"
        assert p["date.end"] == "2029-12-31"


class TestIdempotentFindOrCreate:
    def test_second_ensure_reuses(self, con):
        a = tm.ensure_time_node(con, "day", "2026-06-14")
        con.commit()
        b = tm.ensure_time_node(con, "day", "2026-06-14")
        con.commit()
        assert a == b
        assert _db.count(con, "node") == 1

    def test_find_returns_none_when_absent(self, con):
        assert tm.find_time_node(con, "day", "2026-06-14") is None

    def test_find_returns_existing(self, con):
        nid = tm.ensure_time_node(con, "month", "2026-06")
        con.commit()
        assert tm.find_time_node(con, "month", "2026-06") == nid

    def test_tombstoned_time_node_is_skipped(self, con):
        nid = tm.ensure_time_node(con, "day", "2026-06-14")
        con.commit()
        _db.delete(con, "node", id=nid)   # soft-delete the node row
        con.commit()
        assert tm.find_time_node(con, "day", "2026-06-14") is None
        # and ensure can re-create a fresh one (the dead row doesn't block it)
        again = tm.ensure_time_node(con, "day", "2026-06-14")
        con.commit()
        assert again != nid


class TestStrictDuplicateRejected:
    def test_strict_raises_when_exists(self, con):
        tm.ensure_time_node(con, "day", "2026-06-14")
        con.commit()
        with pytest.raises(ValueError) as e:
            tm.ensure_time_node(con, "day", "2026-06-14", strict=True)
        assert "2026-06-14" in str(e.value)

    def test_strict_creates_when_absent(self, con):
        nid = tm.ensure_time_node(con, "day", "2026-06-14", strict=True)
        con.commit()
        assert _db.get(con, "node", nid) is not None


class TestLifetimeSingleton:
    def test_lifetime_no_period(self, con):
        nid = tm.ensure_time_node(con, "lifetime", None)
        con.commit()
        p = _props(con, nid)
        assert p["type.date"] == "lifetime"
        assert "date.period" not in p

    def test_lifetime_idempotent(self, con):
        a = tm.ensure_time_node(con, "lifetime", None)
        con.commit()
        b = tm.ensure_time_node(con, "lifetime", None)
        con.commit()
        assert a == b
        assert _db.count(con, "node") == 1


class TestValidation:
    def test_bad_level_rejected(self, con):
        with pytest.raises(ValueError):
            tm.ensure_time_node(con, "fortnight", "2026-06")

    def test_bad_period_rejected(self, con):
        with pytest.raises(ValueError):
            tm.ensure_time_node(con, "day", "2026-06")   # month-shaped period for a day level


def nt_span(level, period):
    from worklog import node_types as nt
    return nt.span_of(level, period)


class TestLegacySkeletonDualWrites:
    def test_ensure_day_populates_type_props_on_skeleton(self, con):
        import datetime as _dt
        from worklog.commands import timenodes
        day_id = timenodes._ensure_day(con, _dt.date(2026, 6, 14))
        # the day node + every ancestor level it created carries the new type.date/date.* props
        dp = _props(con, day_id)
        assert dp["type.date"] == "day" and dp["date.period"] == "2026-06-14"
        # walk up: week ancestor has an explicit span pinned
        wk = _db.get(con, "node", _db.get(con, "node", day_id)["parent_id"])
        wp = _props(con, wk["id"])
        assert wp["type.date"] == "week" and wp["date.start"] and wp["date.end"]
