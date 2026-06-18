"""Tests for dateinfo (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestDateInfo:
    """date context: weekday auto-computed + date_meta holidays/leave/swap-days"""

    def test_day_header_auto_weekday(self, cli):
        cli("add", "t", "-t", "work")
        cli("log", "1", "x", "--date", "2026-05-01")
        code, out, _ = cli("day", "2026-05-01")
        assert "2026-05-01 Fri" in out  # Fri computed

    def test_dateinfo_set_and_show_in_day(self, cli):
        cli("add", "t", "-t", "work")
        cli("log", "1", "x", "--date", "2026-05-01")
        cli("dateinfo", "2026-05-01", "Labor Day")
        code, out, _ = cli("day", "2026-05-01")
        assert "Labor Day" in out

    def test_dateinfo_import(self, cli, tmp_path):
        f = tmp_path / "d.json"
        f.write_text('{"2026-05-01":"Labor Day","2026-05-21":"Grain Buds"}', encoding="utf-8")
        cli("dateinfo", "--import", str(f))
        code, out, _ = cli("dateinfo")
        assert "Labor Day" in out and "Grain Buds" in out

    def test_dateinfo_clear(self, cli):
        cli("dateinfo", "2026-05-01", "Labor Day")
        cli("dateinfo", "2026-05-01", "--clear")
        code, out, _ = cli("dateinfo", "2026-05-01")
        assert "no label" in out


class TestCnWeekday:
    def test_invalid_date_returns_empty(self, cli):
        """_cn_weekday internal helper; invalid date triggers ValueError → except path"""
        # going through wl day with invalid date is rejected by _resolve_concrete_date; call _cn_weekday directly
        from worklog import cli as wl
        assert wl._cn_weekday("bad-date") == ""
        assert wl._cn_weekday("2026-13-99") == ""


class TestDateGroup:
    """the metric-style `wl date set/ls/rm/import` group (a clean group — no
    default verb, since `date` doesn't collide with any leaf). `wl dateinfo` stays the
    polymorphic everyday shortcut over the same date_meta table."""

    def _labels(self, con):
        return {r[0]: r[1] for r in con.execute(
            "SELECT date, label FROM date_meta WHERE deleted_at IS NULL")}

    def test_date_set(self, cli, tmp_db):
        cli("date", "set", "2026-05-01", "Labor Day")
        assert self._labels(tmp_db.db_connect()) == {"2026-05-01": "Labor Day"}

    def test_date_set_equals_dateinfo(self, cli, tmp_db):
        cli("date", "set", "2026-05-01", "via group")
        cli("dateinfo", "2026-10-01", "via shortcut")
        assert self._labels(tmp_db.db_connect()) == {
            "2026-05-01": "via group", "2026-10-01": "via shortcut"}

    def test_date_set_upsert(self, cli, tmp_db):
        cli("date", "set", "2026-05-01", "first")
        cli("date", "set", "2026-05-01", "second")          # upsert, not duplicate
        con = tmp_db.db_connect()
        assert self._labels(con) == {"2026-05-01": "second"}
        assert con.execute("SELECT COUNT(*) FROM date_meta WHERE date='2026-05-01'").fetchone()[0] == 1

    def test_date_ls_all_and_one(self, cli):
        cli("date", "set", "2026-05-01", "Labor Day")
        _, out, _ = cli("date", "ls")
        assert "Labor Day" in out
        _, one, _ = cli("date", "ls", "2026-05-01")
        assert "Labor Day" in one
        _, none, _ = cli("date", "ls", "2026-09-09")
        assert "no label" in none

    def test_date_ls_empty(self, cli):
        _, out, _ = cli("date", "ls")
        assert "no date metadata" in out

    def test_date_rm_and_dateinfo_clear_equivalent(self, cli, tmp_db):
        cli("date", "set", "2026-05-01", "x")
        cli("dateinfo", "2026-10-01", "y")
        cli("date", "rm", "2026-05-01")                     # group rm
        cli("dateinfo", "2026-10-01", "--clear")            # shortcut clear
        assert self._labels(tmp_db.db_connect()) == {}

    def test_date_rm_absent(self, cli):
        _, out, _ = cli("date", "rm", "2099-01-01")
        assert "had no metadata" in out

    def test_date_import_file(self, cli, tmp_path, tmp_db):
        f = tmp_path / "d.json"
        f.write_text('{"2026-12-25":"Christmas","2027-01-01":"New Year"}', encoding="utf-8")
        cli("date", "import", str(f))
        assert self._labels(tmp_db.db_connect()) == {
            "2026-12-25": "Christmas", "2027-01-01": "New Year"}

    def test_bare_date_usage(self, cli):
        _, _, err = cli("date")
        assert "usage: wl date" in err

    def test_date_help_cross_references_dateinfo(self, tmp_db):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        assert "wl dateinfo" in sa.choices["date"].format_help()
        assert "wl date" in sa.choices["dateinfo"].format_help()
