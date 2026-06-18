"""The single validated prop-write API: every prop write funnels through
_upsert_prop, which validates the reserved type.* / date.* namespace so no path
can poison it with an out-of-domain value (DESIGN hard-constraint: reserved keys
may not bypass the validator)."""
from __future__ import annotations

import pytest

from worklog import queries, node_types as nt


def _con_with_node(tmp_db):
    """Fresh DB + one task node (#1); return (con, node_id)."""
    tmp_db.ensure_db()
    con = tmp_db.db_connect()
    from worklog import db_table as _db
    from worklog import timeutil as _tu
    nid = _db.insert(con, "node", {"title": "t", "created_at": _tu.utc_now()})
    con.commit()
    return con, nid


class TestUpsertPropValidator:
    def test_rejects_bad_para_value(self, tmp_db):
        con, nid = _con_with_node(tmp_db)
        with pytest.raises(ValueError) as e:
            queries._upsert_prop(con, nid, "type.para", "projekt")
        assert "type.para" in str(e.value)
        con.close()

    def test_rejects_bad_date_level(self, tmp_db):
        con, nid = _con_with_node(tmp_db)
        with pytest.raises(ValueError):
            queries._upsert_prop(con, nid, "type.date", "fortnight")
        con.close()

    def test_accepts_valid_para(self, tmp_db):
        con, nid = _con_with_node(tmp_db)
        queries._upsert_prop(con, nid, "type.para", "project")
        con.commit()
        assert queries._prop_value(con, nid, "type.para") == "project"
        con.close()

    def test_existence_prop_empty_normalized_to_true(self, tmp_db):
        con, nid = _con_with_node(tmp_db)
        queries._upsert_prop(con, nid, "type.habit", "")
        con.commit()
        assert queries._prop_value(con, nid, "type.habit") == "true"
        con.close()

    def test_non_reserved_prop_unchanged(self, tmp_db):
        con, nid = _con_with_node(tmp_db)
        queries._upsert_prop(con, nid, "release", "v0.7.0")
        con.commit()
        assert queries._prop_value(con, nid, "release") == "v0.7.0"
        con.close()


class TestSetCommandValidation:
    def test_set_rejects_bad_reserved_value(self, cli):
        cli("add", "x")
        code, out, err = cli("set", "1", "type.para", "projekt")
        assert code != 0
        assert "type.para" in (out + err)

    def test_set_accepts_valid_reserved_value(self, cli, tmp_db):
        cli("add", "x")
        code, out, err = cli("set", "1", "type.para", "project")
        assert code == 0
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "type.para") == "project"
        con.close()

    def test_generic_prop_path_cannot_bypass_validation(self, cli):
        # the --prop generic route must hit the same value-domain check as --para
        cli("add", "x")
        code, out, err = cli("set", "1", "type.date", "fortnight")
        assert code != 0
        assert "type.date" in (out + err)

    def test_set_type_date_completes_period_and_span(self, cli, tmp_db):
        # regression: `wl set <id> type.date <level>` must COMPLETE the time node — derive
        # date.period (+ start/end for explicit-span levels) from the title — else the node has a
        # level but no period and is unplaceable by any date-range query (the cmd_set gap).
        cli("add", "2026-W25")            # title is a canonical week period
        code, _, _ = cli("set", "1", "type.date", "week")
        assert code == 0
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "type.date") == "week"
        assert queries._prop_value(con, 1, "date.period") == "2026-W25"
        assert queries._prop_value(con, 1, "date.start") == "2026-06-15"
        assert queries._prop_value(con, 1, "date.end") == "2026-06-21"
        con.close()

    def test_set_type_date_no_period_when_title_not_canonical(self, cli, tmp_db):
        # a non-period title can't be completed — only the level is set, no spurious date.period
        cli("add", "some week notes")
        code, _, _ = cli("set", "1", "type.date", "week")
        assert code == 0
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "type.date") == "week"
        assert queries._prop_value(con, 1, "date.period") is None
        con.close()

    def test_set_type_date_level_change_clears_stale_span(self, cli, tmp_db):
        # regression: changing an explicit-span level (week) to a self-describing one (day) must
        # CLEAR the week's date.start/date.end, not leave a 7-day span on a 'day' node.
        cli("add", "2026-06-15")          # a valid day period that is ALSO the Monday of W25
        cli("set", "1", "type.date", "week")   # title isn't a week period → week, no period
        cli("set", "1", "date.period", "2026-W25")  # now a real week w/ span
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "date.start") == "2026-06-15"
        con.close()
        cli("set", "1", "type.date", "day")    # demote to day; title 2026-06-15 IS a valid day
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "type.date") == "day"
        assert queries._prop_value(con, 1, "date.period") == "2026-06-15"
        assert queries._prop_value(con, 1, "date.start") is None   # week span cleared
        assert queries._prop_value(con, 1, "date.end") is None
        con.close()

    def test_set_type_date_lifetime_clears_period_and_span(self, cli, tmp_db):
        # regression: →lifetime must drop the period + span (a lifetime singleton is date-less).
        cli("add", "2026-W25")
        cli("set", "1", "type.date", "week")
        cli("set", "1", "type.date", "lifetime")
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "type.date") == "lifetime"
        assert queries._prop_value(con, 1, "date.period") is None
        assert queries._prop_value(con, 1, "date.start") is None
        con.close()

    def test_set_date_period_directly_derives_span(self, cli, tmp_db):
        # regression: setting date.period on a leveled node re-derives the explicit span.
        cli("add", "my quarter", "--prop", "type.date=quarter")  # leveled, no canonical title
        cli("set", "1", "date.period", "2026-Q2")
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "date.period") == "2026-Q2"
        assert queries._prop_value(con, 1, "date.start") == "2026-04-01"
        assert queries._prop_value(con, 1, "date.end") == "2026-06-30"
        con.close()

    def test_create_explicit_period_gets_span(self, cli, tmp_db):
        # regression: create with an explicit date.period on an explicit-span level must still
        # derive date.start/date.end (the K_PERIOD-in-written path used to skip span entirely).
        cli("add", "sprint", "--prop", "type.date=week", "--prop", "date.period=2026-W25")
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "date.period") == "2026-W25"
        assert queries._prop_value(con, 1, "date.start") == "2026-06-15"
        assert queries._prop_value(con, 1, "date.end") == "2026-06-21"
        con.close()
