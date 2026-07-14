"""Tests for timeutil — UTC storage <-> local rendering for *_at instants.

All tests pin $WORKLOG_TZ to a fixed offset so they're reproducible regardless
of the CI/host timezone.
"""
import re
from datetime import datetime

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


class TestLocalDaySqlGuard:
    """local_day_sql converts full instants but leaves bare YYYY-MM-DD values alone —
    a negative offset would otherwise roll a bare midnight date back a day (GPT review)."""

    def test_bare_date_untouched_instant_converted(self, tz_newyork):  # -05:00
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE t (at TEXT, label TEXT)")
        con.execute("INSERT INTO t VALUES ('2026-06-01', 'bare')")            # literal date
        con.execute("INSERT INTO t VALUES ('2026-06-01 02:00:00', 'instant')")  # UTC instant
        got = {r[1]: r[0] for r in con.execute(f"SELECT {tu.local_day_sql('at')}, label FROM t")}
        assert got["bare"] == "2026-06-01"       # NOT shifted to 2026-05-31
        assert got["instant"] == "2026-05-31"    # 02:00 UTC −05:00 = prev-day 21:00


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


class TestParseTs:
    """`parse_ts` is the ONE reader of a stored instant — every hand-rolled strptime is banned
    by test_time_lint, so this is where the format contract is pinned."""

    def test_canonical_stamp(self):
        assert tu.parse_ts("2026-07-14 09:30:00") == datetime(2026, 7, 14, 9, 30, 0)

    def test_iso_t_separator(self):
        assert tu.parse_ts("2026-07-14T09:30:00") == datetime(2026, 7, 14, 9, 30, 0)

    def test_legacy_date_only_is_a_local_date(self, monkeypatch):
        # older DBs carry bare `YYYY-MM-DD` logged_at / metric.at. It's a literal LOCAL date, so it
        # reads as that day's local midnight — same rule local_day_sql's CASE applies, so SQL and
        # Python can't disagree about which day an old row belongs to. (Hand-parsing it as UTC was
        # a real crash: the age column degraded to "—" on exactly the oldest rows.)
        monkeypatch.setenv("WORKLOG_TZ", "+08:00")
        assert tu.parse_ts("2026-06-20") == datetime(2026, 6, 19, 16, 0, 0)   # 00:00 +08 = 16:00Z prev day

    def test_unparseable_is_none_not_a_crash(self):
        assert tu.parse_ts("not a timestamp") is None
        assert tu.parse_ts("") is None
        assert tu.parse_ts(None) is None


class TestElapsedAndAge:
    def test_elapsed_is_signed_so_end_before_start_is_detectable(self):
        # a clamped primitive would hide `wl clock edit --end` landing before --start
        assert tu.elapsed_sec("2026-07-14 10:00:00", "2026-07-14 09:00:00") == -3600
        assert tu.elapsed_sec("2026-07-14 09:00:00", "2026-07-14 10:00:00") == 3600

    def test_elapsed_unparseable_is_none(self):
        assert tu.elapsed_sec("garbage", "2026-07-14 10:00:00") is None

    def test_age_min_clamps_a_future_stamp_to_zero(self):
        assert tu.age_min(tu.shift_ts(tu.utc_now(), minutes=30)) == 0   # clock skew, not negative age

    def test_age_min_unparseable_is_none(self):
        assert tu.age_min("garbage") is None

    def test_shift_ts_roundtrips(self):
        assert tu.shift_ts("2026-07-14 10:00:00", minutes=-90) == "2026-07-14 08:30:00"


class TestDaysAgoFollowsLocalZone:
    def test_cutoff_is_a_local_day_not_a_utc_one(self, monkeypatch):
        # the staleness bug: a UTC-derived cutoff compared against a local activity date is off by
        # a day for the hours the local date leads UTC. days_ago() must agree with today().
        monkeypatch.setenv("WORKLOG_TZ", "+08:00")
        from datetime import date, timedelta
        assert tu.days_ago(3) == (date.fromisoformat(tu.today()) - timedelta(days=3)).isoformat()
        assert tu.days_ago(0) == tu.today()

    def test_days_ahead(self, monkeypatch):
        monkeypatch.setenv("WORKLOG_TZ", "-05:00")
        from datetime import date, timedelta
        assert tu.days_ahead(2) == (date.fromisoformat(tu.today()) + timedelta(days=2)).isoformat()
