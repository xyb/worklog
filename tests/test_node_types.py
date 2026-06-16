"""Tests for the type.* reserved-property namespace (node_types) — the single
source of truth that replaces the old scattered `kind` constants."""
from __future__ import annotations

import datetime as _dt

import pytest

from worklog import node_types as nt


class TestConstants:
    def test_para_roles(self):
        assert nt.PARA_ROLES == ("area", "project", "task")

    def test_date_levels(self):
        assert nt.DATE_LEVELS == (
            "lifetime", "decade", "year", "quarter", "month", "week", "day")

    def test_canonical_keys(self):
        assert nt.K_PARA == "type.para"
        assert nt.K_DATE == "type.date"
        assert nt.K_HABIT == "type.habit"
        assert nt.K_MEETLOG == "type.meetlog"
        assert nt.K_PERIOD == "date.period"
        assert nt.K_START == "date.start"
        assert nt.K_END == "date.end"

    def test_self_describing_vs_explicit_span_levels_partition_non_lifetime(self):
        # every non-lifetime level is either self-describing or explicit-span, never both
        assert set(nt.SELF_DESCRIBING_LEVELS) == {"year", "month", "day"}
        assert set(nt.EXPLICIT_SPAN_LEVELS) == {"decade", "quarter", "week"}
        covered = set(nt.SELF_DESCRIBING_LEVELS) | set(nt.EXPLICIT_SPAN_LEVELS) | {"lifetime"}
        assert covered == set(nt.DATE_LEVELS)
        assert not (set(nt.SELF_DESCRIBING_LEVELS) & set(nt.EXPLICIT_SPAN_LEVELS))


class TestIsReservedKey:
    @pytest.mark.parametrize("key", [
        "type.para", "type.date", "type.habit", "type.meetlog",
        "date.period", "date.start", "date.end"])
    def test_reserved(self, key):
        assert nt.is_reserved_key(key) is True

    @pytest.mark.parametrize("key", [
        "release", "agent.name", "work", "type.custom", "date", "type"])
    def test_not_reserved(self, key):
        # only the enumerated keys are reserved; type.custom / bare date are free
        assert nt.is_reserved_key(key) is False


class TestValidateProp:
    def test_para_valid(self):
        for r in nt.PARA_ROLES:
            assert nt.validate_prop("type.para", r) == r

    def test_para_invalid_rejected(self):
        with pytest.raises(ValueError) as e:
            nt.validate_prop("type.para", "projekt")
        assert "type.para" in str(e.value)
        assert "project" in str(e.value)  # message lists the valid roles

    def test_date_level_valid(self):
        for lv in nt.DATE_LEVELS:
            assert nt.validate_prop("type.date", lv) == lv

    def test_date_level_invalid_rejected(self):
        with pytest.raises(ValueError):
            nt.validate_prop("type.date", "fortnight")

    def test_existence_prop_empty_becomes_true(self):
        assert nt.validate_prop("type.habit", "") == "true"
        assert nt.validate_prop("type.meetlog", None) == "true"

    def test_existence_prop_keeps_subclass_value(self):
        assert nt.validate_prop("type.meetlog", "dating") == "dating"

    def test_date_start_end_format(self):
        assert nt.validate_prop("date.start", "2026-01-01") == "2026-01-01"
        with pytest.raises(ValueError):
            nt.validate_prop("date.end", "2026/1/1")

    def test_non_reserved_passthrough(self):
        assert nt.validate_prop("release", "v0.7.0") == "v0.7.0"
        assert nt.validate_prop("agent.name", "claude") == "claude"


class TestPeriod:
    @pytest.mark.parametrize("period,level", [
        ("2026-06-14", "day"),
        ("2026-06", "month"),
        ("2026", "year"),
        ("2026-W24", "week"),
        ("2026-Q2", "quarter"),
        ("2020s", "decade"),
    ])
    def test_level_of_period(self, period, level):
        assert nt.level_of_period(period) == level

    def test_level_of_period_unknown(self):
        assert nt.level_of_period("garbage") is None
        assert nt.level_of_period(None) is None      # non-str input

    def test_validate_period_invalid_rejected(self):
        with pytest.raises(ValueError):
            nt.validate_prop("date.period", "not-a-period")
        assert nt.validate_prop("date.period", "2026-W24") == "2026-W24"

    @pytest.mark.parametrize("level,period", [
        ("day", "2026-06-14"), ("month", "2026-06"), ("year", "2026"),
        ("week", "2026-W24"), ("quarter", "2026-Q2")])
    def test_valid_period_true(self, level, period):
        assert nt.valid_period(level, period) is True

    def test_valid_period_false(self):
        assert nt.valid_period("day", "2026-06") is False
        assert nt.valid_period("week", "2026-w24") is False  # canonical is uppercase W

    def test_valid_period_rejects_shape_ok_but_unreal(self):
        # matches the regex shape but isn't a real calendar period → rejected
        assert nt.valid_period("week", "2026-W99") is False
        assert nt.valid_period("month", "2026-13") is False
        assert nt.valid_period("day", "2026-02-30") is False

    def test_lifetime_period_exempt(self):
        # lifetime is a singleton with no date; empty period is fine
        assert nt.valid_period("lifetime", "") is True


class TestSpanOf:
    def test_day(self):
        assert nt.span_of("day", "2026-06-14") == ("2026-06-14", "2026-06-14")

    def test_month(self):
        assert nt.span_of("month", "2026-06") == ("2026-06-01", "2026-06-30")

    def test_month_february_leap(self):
        assert nt.span_of("month", "2024-02") == ("2024-02-01", "2024-02-29")

    def test_year(self):
        assert nt.span_of("year", "2026") == ("2026-01-01", "2026-12-31")

    def test_quarter(self):
        assert nt.span_of("quarter", "2026-Q2") == ("2026-04-01", "2026-06-30")
        assert nt.span_of("quarter", "2026-Q1") == ("2026-01-01", "2026-03-31")
        assert nt.span_of("quarter", "2026-Q4") == ("2026-10-01", "2026-12-31")

    def test_week_is_monday_to_sunday(self):
        start, end = nt.span_of("week", "2026-W24")
        s = _dt.date.fromisoformat(start)
        e = _dt.date.fromisoformat(end)
        assert s.isoweekday() == 1      # Monday
        assert e.isoweekday() == 7      # Sunday
        assert (e - s).days == 6
        assert s.isocalendar()[:2] == (2026, 24)

    def test_decade(self):
        assert nt.span_of("decade", "2020s") == ("2020-01-01", "2029-12-31")

    def test_lifetime_no_span(self):
        assert nt.span_of("lifetime", "") == (None, None)

    def test_span_of_unknown_level_raises(self):
        with pytest.raises(ValueError):
            nt.span_of("fortnight", "2026-06")


class TestAccessors:
    def test_para_of(self):
        assert nt.para_of({"type.para": "project"}) == "project"
        assert nt.para_of({"release": "v1"}) is None

    def test_date_level_of(self):
        assert nt.date_level_of({"type.date": "month"}) == "month"
        assert nt.date_level_of({}) is None

    def test_is_habit_meetlog_existence_based(self):
        assert nt.is_habit({"type.habit": "true"}) is True
        assert nt.is_habit({"type.habit": ""}) is True        # existence, not value
        assert nt.is_habit({"type.habit": "1"}) is True
        assert nt.is_habit({}) is False
        assert nt.is_meetlog({"type.meetlog": "dating"}) is True
        assert nt.is_meetlog({}) is False

    def test_type_props_subset(self):
        props = {"type.para": "task", "type.habit": "true",
                 "release": "v1", "date.period": "2026-06"}
        got = nt.type_props(props)
        assert got == {"type.para": "task", "type.habit": "true"}

    def test_legacy_kind_derivation(self):
        assert nt.legacy_kind({"type.para": "project"}) == "project"
        assert nt.legacy_kind({"type.para": "area"}) == "area"
        assert nt.legacy_kind({"type.date": "day"}) == "day"
        assert nt.legacy_kind({"type.habit": "true"}) == "habit"
        assert nt.legacy_kind({"type.meetlog": "dating"}) == "meetlog"
        assert nt.legacy_kind({}) == "task"                      # bare node → plain task
        # precedence: para wins over a co-present soft type
        assert nt.legacy_kind({"type.para": "task", "type.habit": "true"}) == "task"

    def test_display_ranks_ordered(self):
        # para rank: area < project < task; date rank: lifetime < ... < day
        assert nt.para_rank("area") < nt.para_rank("project") < nt.para_rank("task")
        assert nt.date_rank("lifetime") < nt.date_rank("year") < nt.date_rank("day")
