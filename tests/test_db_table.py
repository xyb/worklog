"""Tests for db_table — the thin zero-dep CRUD helpers (DESIGN G3)."""
import sqlite3

import pytest

from worklog import db_table as dt


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "name TEXT, kind TEXT, n INTEGER, parent INTEGER)")
    return c


def _seed(con, rows):
    for r in rows:
        dt.insert(con, "t", r)
    con.commit()


class TestIdent:
    @pytest.mark.parametrize("ok", ["node", "log.logged_at", "_x", "a1_b"])
    def test_valid(self, ok):
        assert dt._ident(ok) == ok

    @pytest.mark.parametrize("bad", ["1col", "a; DROP TABLE t", "a b", "", "a-b", "a.b.c", None])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            dt._ident(bad)


class TestInsert:
    def test_insert_returns_rowid_and_persists(self, con):
        rid = dt.insert(con, "t", {"name": "a", "n": 1})
        assert rid == 1
        row = con.execute("SELECT * FROM t WHERE id=1").fetchone()
        assert row["name"] == "a" and row["n"] == 1

    def test_insert_empty_rejected(self, con):
        with pytest.raises(ValueError):
            dt.insert(con, "t", {})

    def test_insert_bad_column_rejected(self, con):
        with pytest.raises(ValueError):
            dt.insert(con, "t", {"name; DROP": "x"})


class TestUpdate:
    def test_update_changes_and_rowcount(self, con):
        dt.insert(con, "t", {"name": "a", "n": 1})
        rc = dt.update(con, "t", 1, {"name": "b", "n": 2})
        assert rc == 1
        row = dt.get(con, "t", 1)
        assert row["name"] == "b" and row["n"] == 2

    def test_update_empty_is_noop(self, con):
        dt.insert(con, "t", {"name": "a"})
        assert dt.update(con, "t", 1, {}) == 0


class TestDelete:
    def test_delete_with_conds(self, con):
        _seed(con, [{"name": "a", "kind": "x"}, {"name": "b", "kind": "x"}, {"name": "c", "kind": "y"}])
        rc = dt.delete(con, "t", kind="x")
        assert rc == 2
        assert dt.count(con, "t") == 1

    def test_delete_without_conds_refused(self, con):
        with pytest.raises(ValueError):
            dt.delete(con, "t")


class TestFindFilters:
    def test_eq_and_multiple_anded(self, con):
        _seed(con, [{"name": "a", "kind": "task"}, {"name": "a", "kind": "habit"}])
        rows = dt.find(con, "t", name="a", kind="task")
        assert len(rows) == 1 and rows[0]["kind"] == "task"

    def test_in(self, con):
        _seed(con, [{"kind": "task"}, {"kind": "habit"}, {"kind": "meetlog"}])
        rows = dt.find(con, "t", kind__in=["task", "habit"])
        assert {r["kind"] for r in rows} == {"task", "habit"}

    def test_empty_in_matches_nothing(self, con):
        _seed(con, [{"kind": "task"}])
        assert dt.find(con, "t", kind__in=[]) == []

    def test_none_eq_becomes_is_null(self, con):
        _seed(con, [{"name": "a", "parent": None}, {"name": "b", "parent": 5}])
        rows = dt.find(con, "t", parent=None)
        assert len(rows) == 1 and rows[0]["name"] == "a"

    def test_none_ne_becomes_is_not_null(self, con):
        _seed(con, [{"name": "a", "parent": None}, {"name": "b", "parent": 5}])
        rows = dt.find(con, "t", parent__ne=None)
        assert len(rows) == 1 and rows[0]["name"] == "b"

    @pytest.mark.parametrize("op,val,expect", [
        ("ge", 2, {2, 3}), ("le", 2, {1, 2}), ("gt", 2, {3}), ("lt", 2, {1}), ("ne", 2, {1, 3}),
    ])
    def test_numeric_ops(self, con, op, val, expect):
        _seed(con, [{"n": 1}, {"n": 2}, {"n": 3}])
        rows = dt.find(con, "t", **{f"n__{op}": val})
        assert {r["n"] for r in rows} == expect

    def test_like(self, con):
        _seed(con, [{"name": "alpha"}, {"name": "beta"}])
        rows = dt.find(con, "t", name__like="al%")
        assert len(rows) == 1 and rows[0]["name"] == "alpha"

    def test_unknown_op_rejected(self, con):
        with pytest.raises(ValueError):
            dt.find(con, "t", n__bogus=1)

    def test_bad_column_in_filter_rejected(self, con):
        with pytest.raises(ValueError):
            dt.find(con, "t", **{"n; DROP": 1})


class TestFindShape:
    def test_order_and_limit(self, con):
        _seed(con, [{"n": 3}, {"n": 1}, {"n": 2}])
        rows = dt.find(con, "t", order="n", limit=2)
        assert [r["n"] for r in rows] == [1, 2]

    def test_cols_projection(self, con):
        dt.insert(con, "t", {"name": "a", "n": 7})
        con.commit()
        rows = dt.find(con, "t", cols="n")
        assert rows[0]["n"] == 7 and "name" not in rows[0].keys()


class TestConvenience:
    def test_find_one_and_get(self, con):
        _seed(con, [{"name": "a"}, {"name": "b"}])
        assert dt.find_one(con, "t", name="a")["name"] == "a"
        assert dt.find_one(con, "t", name="zzz") is None
        assert dt.get(con, "t", 2)["name"] == "b"
        assert dt.get(con, "t", 999) is None

    def test_exists(self, con):
        _seed(con, [{"name": "a"}])
        assert dt.exists(con, "t", name="a") is True
        assert dt.exists(con, "t", name="zzz") is False

    def test_count(self, con):
        _seed(con, [{"kind": "x"}, {"kind": "x"}, {"kind": "y"}])
        assert dt.count(con, "t") == 3
        assert dt.count(con, "t", kind="x") == 2

    def test_no_conds_finds_all(self, con):
        _seed(con, [{"name": "a"}, {"name": "b"}])
        assert len(dt.find(con, "t")) == 2
