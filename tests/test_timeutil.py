"""Tests for timeutil — UTC storage <-> local rendering for *_at instants.

All tests pin $WORKLOG_TZ to a fixed offset so they're reproducible regardless
of the CI/host timezone.
"""
import re

import pytest

from worklog import timeutil as tu


@pytest.fixture
def tz_shanghai(monkeypatch):
    monkeypatch.setenv("WORKLOG_TZ", "+08:00")


@pytest.fixture
def tz_newyork(monkeypatch):
    monkeypatch.setenv("WORKLOG_TZ", "-05:00")


class TestParseOffset:
    @pytest.mark.parametrize("s,secs", [
        ("+08:00", 8 * 3600), ("+8", 8 * 3600), ("+0800", 8 * 3600),
        ("8", 8 * 3600), ("-05:00", -5 * 3600), ("-5", -5 * 3600),
        ("+05:30", 5 * 3600 + 30 * 60), ("+00:00", 0),
    ])
    def test_valid(self, s, secs):
        off = tu._parse_offset(s)
        assert off is not None
        assert off.utcoffset(None).total_seconds() == secs

    @pytest.mark.parametrize("s", ["Asia/Shanghai", "", "+15", "+08:99", "abc"])
    def test_invalid(self, s):
        assert tu._parse_offset(s) is None


class TestUtcNow:
    def test_format(self):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", tu.utc_now())


class TestLocalNowToday:
    def test_local_now_format(self, tz_shanghai):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", tu.local_now())

    def test_today_is_prefix_of_local_now(self, tz_shanghai):
        assert tu.today() == tu.local_now()[:10]

    def test_local_now_is_8h_ahead_of_utc(self, tz_shanghai):
        # local_now (+8) converted back to UTC should match utc_now to the minute
        from datetime import datetime
        ln = datetime.strptime(tu.local_now(), tu.FMT)
        un = datetime.strptime(tu.utc_now(), tu.FMT)
        assert abs((ln - un).total_seconds() - 8 * 3600) < 5


class TestLocalToUtc:
    def test_shanghai_subtracts_8h(self, tz_shanghai):
        # 14:30 local (+8) == 06:30 UTC
        assert tu.local_to_utc("2026-06-05 14:30:00") == "2026-06-05 06:30:00"

    def test_accepts_no_seconds(self, tz_shanghai):
        assert tu.local_to_utc("2026-06-05 14:30") == "2026-06-05 06:30:00"

    def test_crosses_day_backward(self, tz_shanghai):
        # 01:00 local (+8) == previous day 17:00 UTC
        assert tu.local_to_utc("2026-06-05 01:00:00") == "2026-06-04 17:00:00"

    def test_newyork_adds_5h(self, tz_newyork):
        # 20:00 local (-5) == next day 01:00 UTC
        assert tu.local_to_utc("2026-06-05 20:00:00") == "2026-06-06 01:00:00"


class TestUtcToLocal:
    def test_shanghai_adds_8h(self, tz_shanghai):
        assert tu.utc_to_local("2026-06-05 06:30:00") == "2026-06-05 14:30:00"

    def test_crosses_day_forward(self, tz_shanghai):
        # UTC 17:00 -> local +8 == next day 01:00
        assert tu.utc_to_local("2026-06-04 17:00:00") == "2026-06-05 01:00:00"

    def test_passthrough_empty(self, tz_shanghai):
        assert tu.utc_to_local("") == ""
        assert tu.utc_to_local(None) is None

    def test_passthrough_unparseable(self, tz_shanghai):
        assert tu.utc_to_local("not-a-timestamp") == "not-a-timestamp"

    def test_roundtrip(self, tz_shanghai):
        local = "2026-01-15 23:45:12"
        assert tu.utc_to_local(tu.local_to_utc(local)) == local


class TestLocalDayOf:
    def test_same_day(self, tz_shanghai):
        assert tu.local_day_of("2026-06-05 06:30:00") == "2026-06-05"

    def test_late_utc_is_next_local_day(self, tz_shanghai):
        # the crux: UTC 2026-06-04 17:00 belongs to local day 2026-06-05
        assert tu.local_day_of("2026-06-04 17:00:00") == "2026-06-05"

    def test_early_utc_still_same_local_day(self, tz_shanghai):
        assert tu.local_day_of("2026-06-05 15:59:00") == "2026-06-05"


class TestTzSqlModifier:
    def test_fixed_offset(self, tz_shanghai):
        assert tu.tz_sql_modifier() == "+08:00"

    def test_negative_offset(self, tz_newyork):
        assert tu.tz_sql_modifier() == "-05:00"

    def test_default_is_localtime(self, monkeypatch):
        monkeypatch.delenv("WORKLOG_TZ", raising=False)
        assert tu.tz_sql_modifier() == "localtime"

    def test_garbage_env_falls_back_to_localtime(self, monkeypatch):
        monkeypatch.setenv("WORKLOG_TZ", "Asia/Shanghai")
        assert tu.tz_sql_modifier() == "localtime"
