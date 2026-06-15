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
    nid = _db.insert(con, "node", {"title": "t", "kind": "task", "created_at": _tu.utc_now()})
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
        cli("add", "x", "-k", "task")
        code, out, err = cli("set", "1", "type.para", "projekt")
        assert code != 0
        assert "type.para" in (out + err)

    def test_set_accepts_valid_reserved_value(self, cli, tmp_db):
        cli("add", "x", "-k", "task")
        code, out, err = cli("set", "1", "type.para", "project")
        assert code == 0
        con = tmp_db.db_connect()
        assert queries._prop_value(con, 1, "type.para") == "project"
        con.close()

    def test_generic_prop_path_cannot_bypass_validation(self, cli):
        # the --prop generic route must hit the same value-domain check as --para
        cli("add", "x", "-k", "task")
        code, out, err = cli("set", "1", "type.date", "fortnight")
        assert code != 0
        assert "type.date" in (out + err)
