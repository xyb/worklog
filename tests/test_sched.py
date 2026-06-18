"""Tests for sched (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestRelativeDelta:
    """signed day/week/month/year deltas for quick entry: +1 / -2 / +1d / -2day / +3w / -2week /
    +1m / -1y — signed number + optional unit (default day). Resolved by _resolve_concrete_date,
    so all date-accepting commands (sched / day / log --date / defer / …) get them."""

    def _today(self, monkeypatch, y=2026, m=6, d=8):
        import worklog.timeutil as tu
        from datetime import date
        monkeypatch.setattr(tu, "today_date", lambda: date(y, m, d))

    def test_resolve_delta_forms(self, monkeypatch):
        self._today(monkeypatch)
        from worklog.helpers import _resolve_concrete_date as r
        assert r("+1") == "2026-06-09" and r("-2") == "2026-06-06"      # bare signed = days
        assert r("+1d") == "2026-06-09" and r("-2day") == "2026-06-06"
        assert r("+3w") == "2026-06-29" and r("-2week") == "2026-05-25"
        assert r("+1m") == "2026-07-08" and r("-1y") == "2025-06-08"
        assert r("2026-06-20") == "2026-06-20" and r("today") == "2026-06-08"  # old forms intact

    def test_add_months_clamps_day(self):
        from worklog.helpers import _add_months
        from datetime import date
        assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)    # clamp to Feb
        assert _add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)   # year rollover
        assert _add_months(date(2026, 3, 15), -4) == date(2025, 11, 15)  # negative back over year

    def test_norm_sched_falls_through_to_delta(self, monkeypatch):
        # defer's fuzzy parser also accepts deltas (precise day hint), keeping fuzzy forms working
        self._today(monkeypatch)
        from worklog.helpers import _norm_sched
        assert _norm_sched("+1") == "2026-06-09"
        assert _norm_sched("-2week") == "2026-05-25"
        assert _norm_sched("someday") == "someday" and _norm_sched("next-week") == "2026-W25"

    def test_delta_rejects_invalid(self):
        # fullmatch is strict: a unit typo / no sign / float / lone sign must NOT be read as a
        # delta — it falls through to ISO validation and raises (GPT review suggestion).
        from worklog.helpers import _resolve_concrete_date as r
        for bad in ("+1x", "+1.5d", "+", "-", "1d", "++1", "+1 2", "w3"):
            with pytest.raises(ValueError):
                r(bad)

    def test_add_months_off_leap_day(self):
        from worklog.helpers import _add_months
        from datetime import date
        assert _add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)   # +1y off Feb 29 clamps
        assert _add_months(date(2026, 8, 31), -6) == date(2026, 2, 28)   # back over a short month

    def test_cli_delta_across_commands(self, cli):
        cli("add", "t")
        assert cli("sched", "1", "+3w")[0] == 0      # positional, +unit
        assert cli("sched", "1", "-2week")[0] == 0   # positional, -unit (not eaten as an option)
        assert cli("defer", "1", "+1m")[0] == 0      # defer fuzzy parser
        assert cli("day", "-2")[0] == 0              # day positional, bare negative


class TestDateWordNormalization:
    """Relative-date *words* are matched case-insensitively and ignoring connector chars
    (hyphen / underscore / whitespace), so 'next-week', 'NEXT_WEEK', 'next week', 'nextweek'
    all parse the same. ISO dates and signed deltas keep their structure (the '-' in '-2w' is a
    sign, not a connector) — _norm_word never strips it from the actual delta/ISO parse."""

    def _today(self, monkeypatch, y=2026, m=6, d=8):
        import worklog.timeutil as tu
        from datetime import date
        monkeypatch.setattr(tu, "today_date", lambda: date(y, m, d))

    def test_norm_word_collapses_variants(self):
        from worklog.helpers import _norm_word
        for v in ("next-week", "Next_Week", "NEXT WEEK", "next  week", " next-week "):
            assert _norm_word(v) == "nextweek"
        assert _norm_word("Day-After-Tomorrow") == "dayaftertomorrow"
        assert _norm_word("-2w") == "2w"   # sign stripped from the *key* — never matches a word

    def test_concrete_words_case_and_connector_insensitive(self, monkeypatch):
        self._today(monkeypatch)
        from worklog.helpers import _resolve_concrete_date as r
        for v in ("day-after-tomorrow", "DAY AFTER TOMORROW", "day_after_tomorrow", "Day-After-Tomorrow"):
            assert r(v) == "2026-06-10"
        assert r("Tomorrow") == "2026-06-09" and r("YESTERDAY") == "2026-06-07"

    def test_concrete_resolves_period_words_to_anchor_day(self, monkeypatch):
        # next-week/next-month/next-quarter resolve to that period's FIRST day (so sched/day take
        # them); connector+case variants too. today = Mon 2026-06-08.
        self._today(monkeypatch)
        from worklog.helpers import _resolve_concrete_date as r
        assert r("next-week") == r("NEXT_WEEK") == r("next week") == "2026-06-15"   # next Monday
        assert r("next-month") == "2026-07-01"
        assert r("next-quarter") == "2026-07-01"   # Q2 → Q3 starts Jul 1
        assert r("下周") == "2026-06-15" and r("下月") == "2026-07-01"

    def test_concrete_still_rejects_someday(self, monkeypatch):
        # someday has no anchor day — concrete commands (day/log/sched) must reject it
        self._today(monkeypatch)
        from worklog.helpers import _resolve_concrete_date as r
        for bad in ("someday", "以后", "garbage", "next-eon"):
            with pytest.raises(ValueError):
                r(bad)

    def test_sched_next_week_variants_equal(self, monkeypatch):
        self._today(monkeypatch)
        from worklog.helpers import _norm_sched as n
        for v in ("next-week", "NEXT-WEEK", "next week", "next_week", "NEXT WEEK", "nextweek"):
            assert n(v) == "2026-W25"
        for v in ("next-month", "NEXT_MONTH", "next month"):
            assert n(v) == "2026-07"
        for v in ("next-quarter", "Next_Quarter"):
            assert n(v) == "2026-Q3"

    def test_sched_delta_sign_survives_normalization(self, monkeypatch):
        # the '-' in -2w is a sign: normalization must not let it be read as a connector word
        self._today(monkeypatch)
        from worklog.helpers import _norm_sched as n
        assert n("-2w") == "2026-05-25" and n("+2weeks") == "2026-06-22"

    def test_plural_unit_tolerance(self, monkeypatch):
        # +2weeks / +2week / +2w all mean the same in both parse paths
        self._today(monkeypatch)
        from worklog.helpers import _resolve_concrete_date as r, _norm_sched as n
        assert r("+2weeks") == r("+2week") == r("+2w") == "2026-06-22"
        assert n("+2weeks") == n("+2week") == n("+2w") == "2026-06-22"
        assert r("+1months") == r("+1month") == r("+1m") == "2026-07-08"

    def test_cli_normalized_words_per_command_semantics(self, cli):
        # sched/day take a CONCRETE date (_resolve_concrete_date, period words → first day);
        # defer takes a fuzzy granularity (_norm_sched, next-week → whole ISO week). someday has
        # no concrete day → defer only.
        cli("add", "t")
        assert cli("sched", "1", "DAY_AFTER_TOMORROW")[0] == 0  # concrete word, connector+case variant
        assert cli("sched", "1", "NEXT_WEEK")[0] == 0           # period word → next Monday (concrete)
        assert cli("defer", "1", "next week")[0] == 0           # fuzzy week bucket
        assert cli("day", "someday")[0] != 0                    # someday has no concrete day
        assert cli("sched", "1", "someday")[0] != 0             # ditto — sched needs a concrete fire date


class TestFuzzySchedule:
    def test_norm_exact_formats(self, tmp_db):
        wl = tmp_db
        assert wl._norm_sched("2026-06-15") == "2026-06-15"
        assert wl._norm_sched("2026-06") == "2026-06"
        assert wl._norm_sched("2026-W23") == "2026-W23"
        assert wl._norm_sched("2026-Q3") == "2026-Q3"
        assert wl._norm_sched("2026") == "2026"
        assert wl._norm_sched("someday") == "someday"
        assert wl._norm_sched(None) is None
        assert wl._norm_sched("  ") is None

    def test_norm_relative_words(self, tmp_db):
        import datetime as dt
        wl = tmp_db
        today = dt.date.today()
        assert wl._norm_sched("today") == today.isoformat()
        assert wl._norm_sched("tomorrow") == (today + dt.timedelta(days=1)).isoformat()
        assert wl._norm_sched("以后") == "someday"
        # 下周/下月/下季 (next week/month/quarter, Chinese date aliases) normalize to the corresponding granularity formats
        assert wl._sched_level(wl._norm_sched("下周")) == "week"
        assert wl._sched_level(wl._norm_sched("下月")) == "month"
        assert wl._sched_level(wl._norm_sched("下季")) == "quarter"

    def test_norm_rejects_invalid(self, tmp_db):
        wl = tmp_db
        for bad in ("2026-13", "2026-02-30", "2026-W99", "garbage-date", "next-decade"):
            with pytest.raises(ValueError):
                wl._norm_sched(bad)

    def test_sched_level(self, tmp_db):
        wl = tmp_db
        assert wl._sched_level("2026-06-15") == "day"
        assert wl._sched_level("2026-06") == "month"
        assert wl._sched_level("2026-W23") == "week"
        assert wl._sched_level("2026-Q3") == "quarter"
        assert wl._sched_level("2026") == "year"
        assert wl._sched_level("someday") == "someday"

    def test_sort_key_exact_before_fuzzy(self, tmp_db):
        wl = tmp_db
        vals = ["someday", "2026-Q3", "2026-06-15", "2026-06", "2026-06-02"]
        ordered = sorted(vals, key=wl._sched_sort_key)
        # fuzzy month 2026-06 anchors to month-start 06-01, first; someday is far future, last
        assert ordered[0] == "2026-06"
        assert ordered[-1] == "someday"
        # anchor order: 2026-06(06-01) < 2026-06-02 < 2026-06-15 < 2026-Q3(07-01)
        assert ordered.index("2026-06") < ordered.index("2026-06-02") < ordered.index("2026-06-15") < ordered.index("2026-Q3")

    def test_display(self, tmp_db):
        wl = tmp_db
        assert wl._sched_display("2026-06-15") == "06-15"  # exact day drops year
        assert wl._sched_display("2026-06") == "2026-06"   # fuzzy kept verbatim
        assert wl._sched_display("someday") == "someday"
        assert wl._sched_display(None) == ""

    def test_add_fuzzy_scheduled_shows_in_ls(self, cli):
        cli("add", "fuzzy task", "--scheduled", "2026-06")
        code, out, _ = cli("ls")
        assert "@2026-06" in out

    def test_add_relative_scheduled(self, cli):
        cli("add", "do next week", "--scheduled", "下周")
        code, out, _ = cli("ls")
        assert "@2026-W" in out

    def test_add_rejects_invalid_scheduled(self, cli):
        code, out, err = cli("add", "bad time", "--scheduled", "2026-13")
        assert code != 0
        assert "invalid month" in err or "unrecognized" in err

    def test_defer_fuzzy(self, cli):
        cli("add", "to defer")
        code, out, _ = cli("defer", "1", "下月")
        assert code == 0
        code, out, _ = cli("show", "1")
        assert "LATER" in out

    def test_import_rejects_invalid_scheduled(self, cli, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text('{"add":[{"title":"x","scheduled":"2026-99"}]}', encoding="utf-8")
        code, out, err = cli("import", "--dry-run", str(f))
        assert code != 0  # invalid scheduled time reported during dry-run

    def test_import_accepts_fuzzy_scheduled(self, cli, tmp_path):
        f = tmp_path / "ok.json"
        f.write_text('{"add":[{"title":"quarter task","scheduled":"2026-Q3"}]}', encoding="utf-8")
        code, out, _ = cli("import", str(f))
        assert code == 0
        code, out, _ = cli("ls")
        assert "@2026-Q3" in out

    def test_apply_update_scheduled_fuzzy(self, cli, tmp_path):
        cli("add", "change plan time")
        f = tmp_path / "u.wld"
        f.write_text("~ #1\n  scheduled 2026-06\n", encoding="utf-8")
        code, out, _ = cli("apply", str(f))
        assert code == 0
        code, out, _ = cli("ls")
        assert "@2026-06" in out

    def test_apply_rejects_invalid_scheduled(self, cli, tmp_path):
        cli("add", "change bad time")
        f = tmp_path / "bad.wld"
        f.write_text("~ #1\n  scheduled 2026-77\n", encoding="utf-8")
        code, out, err = cli("apply", "--dry-run", str(f))
        assert code != 0

    def test_apply_delete_cascades_subtree(self, cli, tmp_path):
        """deleting a parent must cascade to the whole subtree; children must not become orphans (node self-ref is ON DELETE SET NULL)"""
        cli("add", "parent project", "--para", "project")          # #1
        cli("add", "subtaskA", "--parent", "1")          # #2
        cli("add", "subtaskB", "--parent", "1")          # #3
        cli("add", "grandchild", "--parent", "2")           # #4
        f = tmp_path / "del.wld"
        f.write_text("- #1\n", encoding="utf-8")
        code, out, _ = cli("apply", str(f))
        assert code == 0
        # if children orphaned (bug) they'd still appear in ls --all; all-absent = whole subtree truly cleaned
        code, out, _ = cli("ls", "--all")
        for t in ("parent project", "subtaskA", "subtaskB", "grandchild"):
            assert t not in out


class TestSched:
    """forward planning: wl sched schedules a task to a day/recurrence; wl day derives planned status from it (even without log)"""

    def test_sched_oneoff_shows_in_day_as_planned(self, cli):
        cli("add", "future task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        code, out, _ = cli("day", "2026-06-15")
        assert code == 0
        assert "#1" in out
        assert "planned" in out
        assert "planned·not-done" in out  # no log but scheduled → marked planned-not-done

    def test_sched_plan_derived_not_from_tag(self, cli):
        # no planned tag; sched hit alone -> planned
        cli("add", "t", "-t", "work")
        cli("sched", "1", "2026-06-15")
        code, out, _ = cli("day", "2026-06-15", "--by", "plan")
        assert "planned" in out and "unplanned" not in out

    def test_sched_recur_weekly_fires_on_matching_weekday(self, cli):
        cli("add", "Monday standup", "-t", "work")
        cli("sched", "1", "--recur", "weekly:Mon")
        code, mon, _ = cli("day", "2026-05-04")  # Monday
        assert "#1" in mon
        code, tue, _ = cli("day", "2026-05-05")  # Tuesday
        assert "#1" not in tue

    def test_sched_recur_daily_fires_every_day(self, cli):
        cli("add", "daily", "--prop", "type.habit=true", "-t", "personal")
        cli("sched", "1", "--recur", "daily")
        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            code, out, _ = cli("day", d)
            assert "#1" in out

    def test_sched_clear(self, cli):
        cli("add", "t")
        cli("sched", "1", "2026-06-15")
        cli("sched", "1", "--clear")
        code, out, _ = cli("day", "2026-06-15")
        assert "#1" not in out

    def test_sched_list(self, cli):
        cli("add", "t")
        cli("sched", "1", "2026-06-15")
        code, out, _ = cli("sched", "1")
        assert "2026-06-15" in out

    def test_sched_invalid_rrule_rejected(self, cli):
        cli("add", "t")
        code, _, _ = cli("sched", "1", "--recur", "monthly")
        assert code != 0

    def test_sched_relative_date(self, cli):
        from datetime import date, timedelta
        cli("add", "t", "-t", "work")
        cli("sched", "1", "tomorrow")
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        code, out, _ = cli("day", tomorrow)
        assert "#1" in out


class TestSchedHelpers:
    """_sched_level / _sched_anchor / _sched_fires / _norm_rrule edge coverage."""

    def test_sched_recur_weekly(self, cli):
        """weekly:Mon,Wed,Fri rule normalization + write"""
        cli("add", "h1", "--prop", "type.habit=true")
        code, out, _ = cli("sched", "1", "--recur", "weekly:Mon,Wed,Fri")
        assert code == 0
        # confirm the rule was stored in the sched table
        from worklog import cli as wl
        con = wl.db_connect()
        row = con.execute("SELECT rrule FROM sched WHERE node_id=1").fetchone()
        assert row and "Mon" in row["rrule"]

    def test_sched_invalid_rrule(self, cli):
        cli("add", "h1", "--prop", "type.habit=true")
        code, _, _ = cli("sched", "1", "--recur", "garbage-rule")
        assert code != 0

    def test_sched_invalid_weekly_day(self, cli):
        cli("add", "h1", "--prop", "type.habit=true")
        code, _, _ = cli("sched", "1", "--recur", "weekly:NotADay")
        assert code != 0

    def test_sched_invalid_when_date(self, cli):
        cli("add", "h1", "--prop", "type.habit=true")
        code, _, _ = cli("sched", "1", "garbage-date")
        assert code != 0

    def test_sched_clear_empty(self, cli):
        """--clear with no existing schedule → "no schedule" branch"""
        cli("add", "t1")
        _, out, _ = cli("sched", "1", "--clear")
        assert "no schedule" in out or "cleared" in out or out  # any friendly hint

    def test_day_with_weekly_recur_hits(self, cli):
        """weekly: matching weekday in the current week → hit, exercising _sched_anchor weekly branch"""
        from datetime import date
        today = date.today()
        wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][today.weekday()]
        cli("add", "h-weekly", "--prop", "type.habit=true")
        cli("sched", "1", "--recur", f"weekly:{wd}")
        _, out, _ = cli("day", today.isoformat())
        assert "h-weekly" in out


class TestSchedHelpersDirect:
    """direct unit tests for _sched_anchor / _sched_fires / _sched_level."""

    def test_sched_kind_someday(self):
        from worklog import cli as wl
        assert wl._sched_level("someday") == "someday"

    def test_sched_kind_quarter(self):
        from worklog import cli as wl
        assert wl._sched_level("2026-Q2") == "quarter"

    def test_sched_kind_year(self):
        from worklog import cli as wl
        assert wl._sched_level("2026") == "year"

    def test_sched_kind_fuzzy(self):
        from worklog import cli as wl
        assert wl._sched_level("下月") == "fuzzy"

    def test_sched_kind_empty(self):
        from worklog import cli as wl
        assert wl._sched_level("") is None
        assert wl._sched_level(None) is None

    def test_sched_anchor_year(self):
        from worklog import cli as wl
        assert wl._sched_anchor("2026") == "2026-01-01"

    def test_sched_anchor_quarter(self):
        from worklog import cli as wl
        assert wl._sched_anchor("2026-Q2") == "2026-04-01"

    def test_sched_anchor_week(self):
        from worklog import cli as wl
        result = wl._sched_anchor("2026-W01")
        assert result.startswith("2025-12-") or result.startswith("2026-01-")

    def test_sched_anchor_month(self):
        from worklog import cli as wl
        assert wl._sched_anchor("2026-05") == "2026-05-01"

    def test_sched_anchor_invalid_returns_sentinel(self):
        from worklog import cli as wl
        assert wl._sched_anchor("garbage") == "9999-12-31"
        assert wl._sched_anchor("2026-W99") == "9999-12-31"

    def test_sched_fires_weekly_match(self):
        from worklog import cli as wl
        # weekly:Mon — 2026-01-05 is a Monday
        assert wl._sched_fires(None, "weekly:Mon", "2026-01-05") is True
        assert wl._sched_fires(None, "weekly:Tue", "2026-01-05") is False

    def test_sched_fires_daily(self):
        from worklog import cli as wl
        assert wl._sched_fires(None, "daily", "2026-01-05") is True

    def test_sched_fires_on_date(self):
        from worklog import cli as wl
        assert wl._sched_fires("2026-01-05", None, "2026-01-05") is True
        assert wl._sched_fires("2026-01-06", None, "2026-01-05") is False

    def test_sched_fires_empty(self):
        from worklog import cli as wl
        assert wl._sched_fires(None, None, "2026-01-05") is False

    # monthly: which day of the month
    def test_sched_fires_monthly_single_day(self):
        from worklog import cli as wl
        assert wl._sched_fires(None, "monthly:5", "2026-01-05") is True
        assert wl._sched_fires(None, "monthly:5", "2026-01-06") is False
        assert wl._sched_fires(None, "monthly:5", "2026-02-05") is True

    def test_sched_fires_monthly_multi_days(self):
        from worklog import cli as wl
        assert wl._sched_fires(None, "monthly:1,15,25", "2026-01-15") is True
        assert wl._sched_fires(None, "monthly:1,15,25", "2026-01-16") is False
        assert wl._sched_fires(None, "monthly:1,15,25", "2026-02-25") is True

    def test_sched_fires_monthly_last_day(self):
        from worklog import cli as wl
        # 2026-01 month-end = 31
        assert wl._sched_fires(None, "monthly:-1", "2026-01-31") is True
        assert wl._sched_fires(None, "monthly:-1", "2026-01-30") is False
        # 2026-02 month-end = 28
        assert wl._sched_fires(None, "monthly:-1", "2026-02-28") is True
        # 2024 (leap year) Feb end = 29
        assert wl._sched_fires(None, "monthly:-1", "2024-02-29") is True

    def test_sched_fires_monthly_negative_second_last(self):
        from worklog import cli as wl
        # 2026-01-30 = second-to-last (31 - 2 + 1 = 30)
        assert wl._sched_fires(None, "monthly:-2", "2026-01-30") is True
        assert wl._sched_fires(None, "monthly:-2", "2026-02-27") is True  # 28-2+1=27

    def test_sched_fires_monthly_31_in_short_month(self):
        """monthly:31 should not fire in February (no 31st)"""
        from worklog import cli as wl
        assert wl._sched_fires(None, "monthly:31", "2026-01-31") is True
        assert wl._sched_fires(None, "monthly:31", "2026-02-28") is False

    # yearly: each year on MM-DD
    def test_sched_fires_yearly(self):
        from worklog import cli as wl
        assert wl._sched_fires(None, "yearly:03-21", "2026-03-21") is True
        assert wl._sched_fires(None, "yearly:03-21", "2027-03-21") is True
        assert wl._sched_fires(None, "yearly:03-21", "2026-03-22") is False

    def test_sched_fires_yearly_multi(self):
        from worklog import cli as wl
        assert wl._sched_fires(None, "yearly:01-01,12-25", "2026-01-01") is True
        assert wl._sched_fires(None, "yearly:01-01,12-25", "2026-12-25") is True
        assert wl._sched_fires(None, "yearly:01-01,12-25", "2026-07-04") is False

    # quarterly
    def test_sched_fires_quarterly_first_month(self):
        from worklog import cli as wl
        # 1-15 = 15th of the first month of each quarter → 1/15, 4/15, 7/15, 10/15
        for ymd in ("2026-01-15", "2026-04-15", "2026-07-15", "2026-10-15"):
            assert wl._sched_fires(None, "quarterly:1-15", ymd) is True
        for ymd in ("2026-02-15", "2026-03-15", "2026-05-15"):
            assert wl._sched_fires(None, "quarterly:1-15", ymd) is False

    def test_sched_fires_quarterly_third_month_end_day(self):
        from worklog import cli as wl
        # 3-31 (31st of the last month of the quarter): 3/31, 12/31 fire; 6/30, 9/30 — no 31st → no fire
        assert wl._sched_fires(None, "quarterly:3-31", "2026-03-31") is True
        assert wl._sched_fires(None, "quarterly:3-31", "2026-12-31") is True
        assert wl._sched_fires(None, "quarterly:3-31", "2026-06-30") is False
        assert wl._sched_fires(None, "quarterly:3-31", "2026-09-30") is False

    def test_sched_fires_quarterly_neg1_quarter_end(self):
        """quarterly:-1 = last day of each quarter (3/31, 6/30, 9/30, 12/31)"""
        from worklog import cli as wl
        for ymd in ("2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"):
            assert wl._sched_fires(None, "quarterly:-1", ymd) is True
        for ymd in ("2026-03-30", "2026-06-29", "2026-04-30", "2026-12-30"):
            assert wl._sched_fires(None, "quarterly:-1", ymd) is False

    def test_sched_fires_yearly_neg1_year_end(self):
        from worklog import cli as wl
        assert wl._sched_fires(None, "yearly:-1", "2026-12-31") is True
        assert wl._sched_fires(None, "yearly:-1", "2027-12-31") is True
        assert wl._sched_fires(None, "yearly:-1", "2026-12-30") is False
        assert wl._sched_fires(None, "yearly:-1", "2026-01-01") is False


class TestWeeklyNumeric:
    """weekly: accepts numbers 1-7 / -1..-7 (equivalent to Mon..Sun)"""

    def test_norm_weekly_positive_numbers(self):
        from worklog import cli as wl
        # 1=Mon, 2=Tue, ..., 7=Sun
        assert wl._norm_rrule("weekly:1") == "weekly:Mon"
        assert wl._norm_rrule("weekly:1,3,5") == "weekly:Mon,Wed,Fri"
        assert wl._norm_rrule("weekly:7") == "weekly:Sun"

    def test_norm_weekly_negative_numbers(self):
        from worklog import cli as wl
        # -1 = Sun (last day), -7 = Mon
        assert wl._norm_rrule("weekly:-1") == "weekly:Sun"
        assert wl._norm_rrule("weekly:-7") == "weekly:Mon"
        assert wl._norm_rrule("weekly:-1,-2") == "weekly:Sun,Sat"

    def test_norm_weekly_mixed_forms(self):
        from worklog import cli as wl
        # mixes numbers + names + negatives; dedup, order preserved
        assert wl._norm_rrule("weekly:Mon,1,Tue,2") == "weekly:Mon,Tue"

    def test_norm_weekly_out_of_range_rejected(self):
        from worklog import cli as wl
        for bad in ("weekly:0", "weekly:8", "weekly:-8", "weekly:abc"):
            try:
                wl._norm_rrule(bad)
                assert False, f"should reject {bad}"
            except ValueError:
                pass

    def test_fires_weekly_numeric_equivalent(self):
        """weekly:-1 = weekly:Sun → 2026-01-04 is a Sunday"""
        from worklog import cli as wl
        assert wl._sched_fires(None, "weekly:Sun", "2026-01-04") is True
        # passing a numeric rule directly to _sched_fires does not work (needs _norm_rrule conversion);
        # end-to-end via wl sched, _norm_rrule has already converted it


class TestQuarterlyAndYearlyNeg1Norm:
    def test_quarterly_norm_md(self):
        from worklog import cli as wl
        assert wl._norm_rrule("quarterly:1-15") == "quarterly:1-15"
        assert wl._norm_rrule("quarterly:3-31,1-1") == "quarterly:3-31,1-1"

    def test_quarterly_norm_neg1(self):
        from worklog import cli as wl
        assert wl._norm_rrule("quarterly:-1") == "quarterly:-1"

    def test_quarterly_rejects_bad(self):
        from worklog import cli as wl
        for bad in ("quarterly:", "quarterly:4-1", "quarterly:0-15", "quarterly:abc"):
            try:
                wl._norm_rrule(bad)
                assert False, f"should reject {bad}"
            except ValueError:
                pass

    def test_yearly_neg1_norm(self):
        from worklog import cli as wl
        assert wl._norm_rrule("yearly:-1") == "yearly:-1"
        assert wl._norm_rrule("yearly:-1,03-21") == "yearly:-1,03-21"


class TestQuarterlyE2E:
    def test_sched_quarterly_neg1_end_to_end(self, cli):
        cli("add", "quarter-end review", "--prop", "type.habit=true")
        cli("sched", "1", "--recur", "quarterly:-1")
        # Q1 end = 03-31
        _, out, _ = cli("day", "2026-03-31")
        assert "quarter-end review" in out
        _, out, _ = cli("day", "2026-04-30")
        assert "quarter-end review" not in out

    def test_sched_quarterly_first_month_first_day(self, cli):
        cli("add", "quarter first day", "--prop", "type.habit=true")
        cli("sched", "1", "--recur", "quarterly:1-1")
        for ymd in ("2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01"):
            _, out, _ = cli("day", ymd)
            assert "quarter first day" in out, f"should hit {ymd}"

    def test_sched_weekly_numeric_end_to_end(self, cli):
        """wl sched with weekly:-1 (=Sun) → fires on Sundays"""
        cli("add", "Sunday review", "--prop", "type.habit=true")
        cli("sched", "1", "--recur", "weekly:-1")
        # 2026-01-04 is a Sunday
        _, out, _ = cli("day", "2026-01-04")
        assert "Sunday review" in out


class TestNormRruleNew:
    """_norm_rrule new prefix validation"""

    def test_monthly_norm(self):
        from worklog import cli as wl
        assert wl._norm_rrule("monthly:5") == "monthly:5"
        assert wl._norm_rrule("monthly:1,15,25") == "monthly:1,15,25"
        assert wl._norm_rrule("monthly:-1") == "monthly:-1"

    def test_monthly_empty_rejected(self):
        from worklog import cli as wl
        try:
            wl._norm_rrule("monthly:")
            assert False
        except ValueError:
            pass

    def test_monthly_out_of_range_rejected(self):
        from worklog import cli as wl
        for bad in ("monthly:0", "monthly:32", "monthly:-29", "monthly:abc"):
            try:
                wl._norm_rrule(bad)
                assert False, f"should reject {bad}"
            except ValueError:
                pass

    def test_yearly_norm(self):
        from worklog import cli as wl
        assert wl._norm_rrule("yearly:03-21") == "yearly:03-21"
        assert wl._norm_rrule("yearly:01-01,12-25") == "yearly:01-01,12-25"

    def test_yearly_bad_format_rejected(self):
        from worklog import cli as wl
        for bad in ("yearly:", "yearly:3-21", "yearly:13-01", "yearly:02-32", "yearly:abc-de"):
            try:
                wl._norm_rrule(bad)
                assert False, f"should reject {bad}"
            except ValueError:
                pass


class TestRecurEndToEnd:
    """wl sched + wl day end-to-end: monthly/yearly habit fires that day"""

    def test_sched_monthly_via_cli_and_day_hits(self, cli):
        from datetime import date
        cli("add", "month-start check-in", "--prop", "type.habit=true")
        # use today's day-of-month as the monthly rule so it always fires today
        today = date.today()
        cli("sched", "1", "--recur", f"monthly:{today.day}")
        _, out, _ = cli("day", today.isoformat())
        assert "month-start check-in" in out

    def test_sched_yearly_via_cli_and_day_hits(self, cli):
        from datetime import date
        cli("add", "anniversary", "--prop", "type.habit=true")
        today = date.today()
        cli("sched", "1", "--recur", f"yearly:{today.month:02d}-{today.day:02d}")
        _, out, _ = cli("day", today.isoformat())
        assert "anniversary" in out

    def test_sched_monthly_last_day_via_cli(self, cli):
        cli("add", "month-end review", "--prop", "type.habit=true")
        cli("sched", "1", "--recur", "monthly:-1")
        # test 2026-02-28 (short month-end)
        _, out, _ = cli("day", "2026-02-28")
        assert "month-end review" in out
        _, out, _ = cli("day", "2026-02-27")
        assert "month-end review" not in out

    def test_sched_invalid_monthly_rejected(self, cli):
        cli("add", "x", "--prop", "type.habit=true")
        code, _, _ = cli("sched", "1", "--recur", "monthly:0")
        assert code != 0

    def test_sched_invalid_yearly_rejected(self, cli):
        cli("add", "x", "--prop", "type.habit=true")
        code, _, _ = cli("sched", "1", "--recur", "yearly:13-99")
        assert code != 0


class TestSchedListing:
    """`wl sched <id>` with no args lists existing schedules for the node."""

    def test_sched_list_empty_node(self, cli):
        cli("add", "fresh-task")
        _, out, _ = cli("sched", "1")
        assert "has no schedule" in out

    def test_sched_list_after_scheduling(self, cli):
        cli("add", "scheduled-task")
        cli("sched", "1", "2026-06-15")
        _, out, _ = cli("sched", "1")
        assert "2026-06-15" in out


class TestSchedIdempotent:
    """sched must not insert duplicate (node_id, on_date) / (node_id, rrule) rows."""

    def test_oneoff_date_idempotent(self, cli):
        cli("add", "t")
        cli("sched", "1", "2026-06-15")
        _, out, _ = cli("sched", "1", "2026-06-15")
        assert "already scheduled" in out
        # exactly one row for that date
        import sqlite3, os
        con = sqlite3.connect(os.environ["WORKLOG_DB"])
        cnt = con.execute("SELECT COUNT(*) FROM sched WHERE node_id=1 AND on_date=?", ("2026-06-15",)).fetchone()[0]
        con.close()
        assert cnt == 1

    def test_recur_rule_idempotent(self, cli):
        cli("add", "t", "--prop", "type.habit=true")
        cli("sched", "1", "--recur", "daily")
        _, out, _ = cli("sched", "1", "--recur", "daily")
        assert "already on recurring schedule" in out
        import sqlite3, os
        con = sqlite3.connect(os.environ["WORKLOG_DB"])
        cnt = con.execute("SELECT COUNT(*) FROM sched WHERE node_id=1 AND rrule=?", ("daily",)).fetchone()[0]
        con.close()
        assert cnt == 1


class TestAgenda:
    """wl agenda <start> <end>: cross-granularity scheduling overview from both the
    sched table (concrete days) and node.scheduled_at (fuzzy month/someday pins)."""

    def _seed(self, cli):
        cli("add", "exact day")   # 1 → sched table, day
        cli("add", "month pin")    # 2 → scheduled_at, month
        cli("add", "someday item") # 3 → scheduled_at, someday
        cli("add", "july task")    # 4 → sched table, out of range
        cli("sched", "1", "2026-06-15")
        cli("defer", "2", "2026-06")             # month-level → node.scheduled_at
        cli("defer", "3", "someday")
        cli("sched", "4", "2026-07-10")

    def test_agenda_spans_both_sources_in_range(self, cli):
        self._seed(cli)
        _, out, _ = cli("agenda", "2026-06-01", "2026-06-30")
        assert "exact day" in out          # sched-table day in range
        assert "month pin" in out          # scheduled_at month pin in range — the month-pin case
        assert "july task" not in out      # out of range
        assert "someday item" not in out   # someday not shown without --someday

    def test_agenda_month_pin_sorts_before_mid_month_day(self, cli):
        self._seed(cli)
        _, out, _ = cli("agenda", "2026-06-01", "2026-06-30")
        # @2026-06 anchors to 2026-06-01, before the 06-15 exact day
        assert out.index("month pin") < out.index("exact day")

    def test_agenda_someday_flag_appends_someday(self, cli):
        self._seed(cli)
        _, out, _ = cli("agenda", "2026-06-01", "2026-06-30", "--someday")
        assert "someday item" in out
        assert "someday / fuzzy" in out

    def test_agenda_empty_range(self, cli):
        self._seed(cli)
        _, out, _ = cli("agenda", "2025-01-01", "2025-01-31")
        assert "nothing scheduled" in out

    def test_agenda_hides_done_by_default(self, cli):
        cli("add", "done task")
        cli("sched", "1", "2026-06-15")
        cli("done", "1")
        _, out, _ = cli("agenda", "2026-06-01", "2026-06-30")
        assert "done task" not in out
        _, out2, _ = cli("agenda", "2026-06-01", "2026-06-30", "--all")
        assert "done task" in out2

    def test_agenda_swaps_reversed_range(self, cli):
        self._seed(cli)
        _, out, _ = cli("agenda", "2026-06-30", "2026-06-01")  # reversed
        assert "exact day" in out  # still works, range normalized

    def test_agenda_bad_date_rejected(self, cli):
        code, _, err = cli("agenda", "not-a-date", "2026-06-30")
        assert code != 0


class TestSchedLsAndRruleValidation:
    def test_sched_ls_no_schedule_message(self, cli):
        cli("add", "t")
        _, out, _ = cli("sched", "ls", "1")
        assert "has no schedule" in out

    def test_empty_weekly_rule_rejected(self):
        from worklog import cli as wl
        with pytest.raises(ValueError):
            wl._norm_rrule("weekly:")          # no weekday after the colon

    def test_quarterly_day_out_of_range_rejected(self):
        from worklog import cli as wl
        with pytest.raises(ValueError):
            wl._norm_rrule("quarterly:1-99")   # day 99 > 31
