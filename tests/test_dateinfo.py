"""Tests for dateinfo (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestDateInfo:
    """date context: weekday auto-computed + date_meta holidays/leave/swap-days"""

    def test_day_header_auto_weekday(self, cli):
        cli("add", "t", "-k", "task", "-t", "work")
        cli("log", "1", "x", "--date", "2026-05-01")
        code, out, _ = cli("day", "2026-05-01")
        assert "2026-05-01 Fri" in out  # Fri computed

    def test_dateinfo_set_and_show_in_day(self, cli):
        cli("add", "t", "-k", "task", "-t", "work")
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
