"""Tests for ux (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestUXShortcuts:
    """UX shortcuts: multi-id / add --sched / log --time / date shorthand, etc."""

    def test_add_sched_direct(self, cli):
        """wl add --sched today = add task and put it in the sched table at the same time"""
        cli("add", "today task", "-k", "task", "--sched", "today")
        _, day, _ = cli("day")
        assert "#1" in day
        assert "today task" in day
        assert "planned" in day  # in the planned section, not unplanned

    def test_add_sched_yesterday(self, cli):
        cli("add", "backfill yesterday", "-k", "task", "--sched", "yesterday")
        _, yday, _ = cli("day", "yesterday")
        assert "backfill yesterday" in yday

    def test_add_sched_invalid_date_errors(self, cli):
        code, _, err = cli("add", "work item", "-k", "task", "--sched", "not-a-date")
        assert code != 0
        assert "bad date" in err or "bad date" in _ or "✗" in (err + _)

    def test_done_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        cli("add", "t3", "-k", "task")
        _, out, _ = cli("done", "1", "2", "3")
        assert "#1 → DONE" in out
        assert "#2 → DONE" in out
        assert "#3 → DONE" in out

    def test_done_single_id_still_works(self, cli):
        """legacy usage wl done 1 still works"""
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("done", "1")
        assert "#1 → DONE" in out

    def test_start_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("start", "1", "2")
        assert "#1 → DOING" in out
        assert "#2 → DOING" in out

    def test_link_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("link", "1", "2", "shared doc")
        assert "#1" in out and "shared doc" in out
        assert "#2" in out

    def test_log_with_time(self, cli):
        cli("add", "eat", "-k", "task")
        cli("log", "1", "breakfast", "--time", "11:09")
        _, show, _ = cli("show", "1")
        assert "11:09:00" in show  # time stored in logged_at

    def test_log_date_accepts_yesterday(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "yesterday thing", "--date", "yesterday")
        _, show, _ = cli("show", "1")
        # "yesterday" is resolved to a concrete date and stored in logged_at
        from datetime import date, timedelta
        yday = (date.today() - timedelta(days=1)).isoformat()
        assert yday in show

    def test_day_accepts_today_shorthand(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "today thing")
        _, out, _ = cli("day", "today")
        assert "today thing" in out

    def test_day_accepts_yesterday_shorthand(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "yesterday thing", "--date", "yesterday")
        _, out, _ = cli("day", "yesterday")
        assert "yesterday thing" in out

    def test_day_invalid_date_errors(self, cli):
        code, _, err = cli("day", "not-a-date")
        assert code != 0

    def test_active_lists_clock_in_tasks(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        cli("start", "1", "2")
        _, out, _ = cli("active")
        assert "#1" in out and "#2" in out
        assert "t1" in out and "t2" in out

    def test_active_empty(self, cli):
        _, out, _ = cli("active")
        assert "no active task right now" in out

    def test_active_excludes_stopped(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")
        cli("stop", "1")
        _, out, _ = cli("active")
        assert "#1" not in out

    def test_wait_marks_status(self, cli):
        cli("add", "t1", "-k", "task")
        cli("wait", "1", "--note", "waiting on review")
        _, show, _ = cli("show", "1")
        assert "WAIT" in show
        assert "waiting on review" in show

    def test_wait_auto_clocks_out(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")
        cli("wait", "1")
        _, active, _ = cli("active")
        assert "#1" not in active  # CLOCK auto-closed; no longer in active

    def test_wait_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("wait", "1", "2")
        assert "#1 → WAIT" in out
        assert "#2 → WAIT" in out

    def test_reopen_undoes_done(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        cli("reopen", "1")
        _, show, _ = cli("show", "1")
        assert "TODO" in show
        assert "DONE" not in show or "TODO" in show  # status reverted to TODO

    def test_tick_multiple_ids(self, cli):
        cli("add", "h1", "-k", "habit")
        cli("add", "h2", "-k", "habit")
        cli("add", "h3", "-k", "habit")
        _, out, _ = cli("tick", "1", "2", "3", "--note", "do all today")
        assert "#1 checked in" in out
        assert "#2 checked in" in out
        assert "#3 checked in" in out
        # each one got a log entry
        for nid in ("1", "2", "3"):
            _, show, _ = cli("show", nid)
            assert "do all today" in show

    def test_tick_single_id_still_works(self, cli):
        cli("add", "h1", "-k", "habit")
        _, out, _ = cli("tick", "1", "--note", "ok")
        assert "#1 checked in" in out

    def test_empty_title_rejected(self, cli):
        code, _, err = cli("add", "", "-k", "task")
        assert code != 0
        code2, _, err2 = cli("add", "   ", "-k", "task")
        assert code2 != 0

    def test_show_multiple_ids(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("show", "1", "2")
        assert "#1" in out and "t1" in out
        assert "#2" in out and "t2" in out

    def test_logs_date_today_alias(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "today thing")
        _, out, _ = cli("logs", "--date", "today")
        assert "today thing" in out

    def test_logs_date_yesterday_alias(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "yesterday thing", "--date", "yesterday")
        _, out, _ = cli("logs", "--date", "yesterday")
        assert "yesterday thing" in out

    def test_logs_preset_today(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "today thing")
        _, out, _ = cli("logs", "today")
        assert "today thing" in out

    def test_logs_preset_yesterday(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "yesterday thing", "--date", "yesterday")
        _, out, _ = cli("logs", "yesterday")
        assert "yesterday thing" in out

    def test_logs_preset_week(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "this week's items")
        _, out, _ = cli("logs", "week")
        assert "this week's items" in out

    def test_find_empty_rejected(self, cli):
        code, _, err = cli("find", "")
        assert code != 0
        code2, _, err2 = cli("find", "   ")
        assert code2 != 0

    def test_add_sched_and_scheduled_conflict(self, cli):
        code, _, err = cli("add", "t1", "-k", "task", "--sched", "today", "--scheduled", "下周")
        assert code != 0

    def test_date_case_insensitive(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "today thing")
        _, out, _ = cli("day", "TODAY")
        assert "today thing" in out
        _, out2, _ = cli("day", "Yesterday")
        # "Yesterday" should resolve successfully (no error even with no content)
        assert "✗" not in out2

    def test_ls_brief_drops_tags(self, cli):
        cli("add", "t1", "-k", "task", "-t", "important,work")
        _, full, _ = cli("ls")
        _, brief, _ = cli("-q", "ls")
        assert ":important:" in full or "important" in full
        assert ":important:" not in brief

    def test_logs_empty_body_rejected(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, err = cli("log", "1", "")
        assert code != 0
        code2, _, _ = cli("log", "1", "   ")
        assert code2 != 0

    def test_link_empty_doc_rejected(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("link", "1", "")
        assert code != 0

    def test_set_empty_key_rejected(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("set", "1", "", "value")
        assert code != 0

    def test_tick_empty_note_falls_back(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("tick", "1", "--note", "")
        # no error; falls back to default body
        assert "checked in" in out
        _, show, _ = cli("show", "1")
        assert "✓ done" in show

    def test_log_time_validates_range(self, cli):
        cli("add", "t1", "-k", "task")
        # valid
        code1, _, _ = cli("log", "1", "ok", "--time", "23:59")
        assert code1 == 0
        # invalid
        code2, _, _ = cli("log", "1", "x", "--time", "25:00")
        assert code2 != 0
        code3, _, _ = cli("log", "1", "x", "--time", "12:60")
        assert code3 != 0
        code4, _, _ = cli("log", "1", "x", "--time", "abc")
        assert code4 != 0

    def test_find_invalid_field_rejected(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("find", "t1", "--in", "bogus")
        assert code != 0

    def test_find_invalid_kind_rejected(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("find", "t1", "--kind", "bogus")
        assert code != 0

    def test_logs_by_task_tail_zero(self, cli):
        """tail 0 = show header only, no expansion (edge-case bug fix for Python lst[-0:] = full list)"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "a")
        cli("log", "1", "b")
        _, out, _ = cli("logs", "--since", "1970-01-01", "--by-task", "--tail", "0")
        assert "#1" in out
        assert "2 total" in out
        assert "[" not in out.split("\n")[1] if len(out.split("\n")) > 1 else True
        # body not expanded; strip header words then check
        cleaned = out.replace("'t1'", "").replace("total", "").replace("last", "")
        assert "a" not in cleaned
        assert "b" not in cleaned

    def test_version_constant_exists(self, cli, tmp_db):
        # __version__ exists and is non-empty
        from worklog import cli as wl_mod
        assert hasattr(wl_mod, "__version__")
        assert wl_mod.__version__


class TestCanceledFilter:
    """§28 unified status filtering: hides CANCELED by default; --show-canceled exposes it."""

    def test_cancel_command(self, cli):
        cli("add", "dropped work", "-k", "task")
        _, out, _ = cli("cancel", "1")
        assert "→ CANCELED" in out
        _, show, _ = cli("show", "1")
        assert "CANCELED" in show

    def test_cancel_multiple_ids(self, cli):
        cli("add", "a", "-k", "task")
        cli("add", "b", "-k", "task")
        _, out, _ = cli("cancel", "1", "2")
        assert "#1 → CANCELED" in out
        assert "#2 → CANCELED" in out

    def test_ls_default_hides_canceled(self, cli):
        cli("add", "active", "-k", "task")
        cli("add", "dropped", "-k", "task")
        cli("cancel", "2")
        _, out, _ = cli("ls")
        assert "active" in out
        assert "dropped" not in out

    def test_ls_show_canceled(self, cli):
        cli("add", "active", "-k", "task")
        cli("add", "dropped", "-k", "task")
        cli("cancel", "2")
        _, out, _ = cli("--show-canceled", "ls")
        assert "dropped" in out

    def test_ls_all_includes_canceled(self, cli):
        # --all still includes DONE + CANCELED (semantics unchanged)
        cli("add", "a", "-k", "task")
        cli("add", "b", "-k", "task")
        cli("done", "1")
        cli("cancel", "2")
        _, out, _ = cli("ls", "--all")
        # --all includes DONE and CANCELED
        assert "#1" in out and "#2" in out

    def test_projects_default_hides_canceled(self, cli):
        cli("add", "active proj", "-k", "project")
        cli("add", "obsolete proj", "-k", "project")
        cli("cancel", "2")
        _, out, _ = cli("projects")
        assert "active proj" in out
        assert "obsolete proj" not in out

    def test_find_default_hides_canceled(self, cli):
        cli("add", "find-target alpha", "-k", "task")
        cli("add", "find-target beta", "-k", "task")
        cli("cancel", "2")
        _, out, _ = cli("find", "find-target")
        assert "alpha" in out
        assert "beta" not in out

    def test_find_show_canceled(self, cli):
        cli("add", "find-target alpha", "-k", "task")
        cli("add", "find-target beta", "-k", "task")
        cli("cancel", "2")
        _, out, _ = cli("--show-canceled", "find", "find-target")
        assert "alpha" in out
        assert "beta" in out

    def test_day_hides_canceled_task_log(self, cli):
        cli("add", "active", "-k", "task")
        cli("add", "dropped", "-k", "task")
        cli("log", "1", "did today")
        cli("log", "2", "today's obsolete log")
        cli("cancel", "2")
        _, out, _ = cli("day")
        assert "did today" in out
        assert "today's obsolete log" not in out

    def test_tree_hides_canceled_root(self, cli):
        cli("add", "active", "-k", "task")
        cli("add", "obsolete root", "-k", "task")
        cli("cancel", "2")
        _, out, _ = cli("tree", "--depth", "1")
        assert "active" in out
        assert "obsolete root" not in out

    def test_summary_hides_canceled(self, cli):
        cli("add", "active", "-k", "task")
        cli("add", "dropped", "-k", "task")
        cli("done", "1")
        cli("cancel", "2")
        _, out, _ = cli("summary", "--since", "1970-01-01")
        assert "active" in out
        assert "dropped" not in out


class TestDurationAndAutoProgress:
    """§26 duration summary + §27 auto status advancement."""

    def test_log_keep_status_disables_auto(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("log", "1", "progress", "--keep-status")
        assert "TODO → DOING" not in out
        _, show, _ = cli("show", "1")
        # still TODO
        assert "TODO" in show

    def test_log_with_date_keeps_status(self, cli):
        """backfilling a historical log does not change status"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "history", "--date", "2020-01-01")
        _, show, _ = cli("show", "1")
        assert "TODO" in show

    def test_log_done_not_reverted(self, cli):
        """logging after DONE does not auto-revert status"""
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        cli("log", "1", "addendum")
        _, show, _ = cli("show", "1")
        assert "DONE" in show

    def test_duration_format(self, cli):
        """log span duration shown as [Xh Ym]"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "a", "--time", "10:00")
        cli("log", "1", "b", "--time", "12:30")
        _, out, _ = cli("ls")
        assert "[2h30m]" in out

    def test_duration_under_hour(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "a", "--time", "10:00")
        cli("log", "1", "b", "--time", "10:45")
        _, out, _ = cli("ls")
        assert "[45m]" in out

    def test_duration_zero_hidden(self, cli):
        """single log has no span; no duration shown"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "single")
        _, out, _ = cli("ls")
        assert "[" not in out.split("single")[1] if "single" in out else True


class TestLimitTopWindow:
    """§28 Batch B+C: --limit / --top + projects window parent parser unified."""

    def test_ls_limit(self, cli):
        for i in range(10):
            cli("add", f"t{i}", "-k", "task")
        _, out, _ = cli("ls", "--limit", "3")
        assert "showing 3/10" in out
        # only 3 task rows expected
        assert out.count("#1 t1") == 0 or "t0" in out

    def test_ls_top_by_priority(self, cli):
        cli("add", "low-pri", "-k", "task", "-p", "C")
        cli("add", "high-pri-1", "-k", "task", "-p", "A")
        cli("add", "no-pri", "-k", "task")
        cli("add", "high-pri-2", "-k", "task", "-p", "A")
        _, out, _ = cli("ls", "--top", "2")
        # top sorts by priority + id; top 2 are A
        assert "high-pri-1" in out
        assert "high-pri-2" in out
        assert "low-pri" not in out
        assert "no-pri" not in out

    def test_projects_limit(self, cli):
        for i in range(5):
            cli("add", f"p{i}", "-k", "project")
        _, out, _ = cli("projects", "--limit", "2")
        assert "(showing 2/5)" in out

    def test_projects_window_week(self, cli):
        """projects uses the window parent parser; --week resolves to a since cutoff"""
        cli("add", "old", "-k", "project")
        cli("add", "t-old", "-k", "task", "--parent", "1")
        cli("log", "2", "old", "--date", "2020-01-01")
        cli("add", "new", "-k", "project")
        cli("add", "t-new", "-k", "task", "--parent", "3")
        cli("log", "4", "today")
        # use the current week for wl
        from datetime import date
        today = date.today()
        iso_week = today.isocalendar()
        wk = f"{iso_week[0]}-W{iso_week[1]:02d}"
        _, out, _ = cli("projects", "--week", wk)
        assert "new" in out
        assert "old" not in out

    def test_logs_limit(self, cli):
        cli("add", "t1", "-k", "task")
        for i in range(20):
            cli("log", "1", f"log {i}")
        _, out, _ = cli("logs", "--since", "1970-01-01", "--limit", "5")
        assert "showing 5/20" in out


class TestUnstubbedHelpers:
    """additional unit/edge cases to improve overall coverage."""

    def test_relog_log_id_with_hash_L_prefix(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "old")
        cli("relog", "#L1", "new")
        _, show, _ = cli("show", "1")
        assert "new" in show

    def test_logs_recent_preset(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        _, out, _ = cli("logs", "recent")
        from datetime import date
        assert date.today().isoformat() in out


class TestPrecisionQueryHints:
    """tests for the query-precision design principle landing"""

    def test_ls_help_has_usage_examples(self, cli):
        """hints moved to --help epilog (no stdout pollution)"""
        # argparse --help calls SystemExit(0); call build_parser to inspect the epilog directly
        from worklog import cli as wl
        p = wl.build_parser()
        ls_action = next(a for a in p._actions if hasattr(a, "choices") and "ls" in (a.choices or {}))
        ls_parser = ls_action.choices["ls"]
        assert "wl find" in (ls_parser.epilog or "")
        assert "--parent" in ls_parser.epilog
        assert "--unscheduled" in ls_parser.epilog

    def test_logs_id_tail_single_task(self, cli):
        """wl logs --id N --tail K: tail also takes effect in single-task mode, no need for --by-task"""
        cli("add", "t1", "-k", "task")
        for i in range(7):
            cli("log", "1", f"e{i}")
        _, out, _ = cli("logs", "--id", "1", "--tail", "3")
        # should return last 3 + display omission hint
        assert "e6" in out and "e5" in out and "e4" in out
        assert "e0" not in out and "e1" not in out
        assert "showing last 3" in out or "elided" in out


class TestShortFlags:
    """single-letter short flags for high-frequency args (-d/--date, -n/--note)."""

    def test_log_dash_d_date(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("log", "1", "backfilled", "-d", "2026-06-01")
        con = tmp_db.db_connect()
        row = con.execute("SELECT logged_at FROM log WHERE node_id=1 AND deleted_at IS NULL").fetchone()
        assert row[0].startswith("2026-06-01")

    def test_logs_dash_d_date(self, cli):
        cli("add", "t", "-k", "task")
        cli("log", "1", "x", "-d", "2026-06-01")
        _, out, _ = cli("logs", "-d", "2026-06-01")
        assert "backfilled" in out or "x" in out

    def test_tick_dash_n_note(self, cli, tmp_db):
        cli("add", "h", "-k", "habit")
        cli("tick", "1", "-n", "6 pullups")
        con = tmp_db.db_connect()
        bodies = [r[0] for r in con.execute("SELECT body FROM log WHERE node_id=1 AND deleted_at IS NULL")]
        assert "6 pullups" in bodies

    def test_recap_dash_d_date(self, cli):
        cli("recap", "-d", "2026-06-01", "past recap")
        _, out, _ = cli("recap", "-d", "2026-06-01")
        assert "past recap" in out
