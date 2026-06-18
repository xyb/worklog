"""Tests for goal / recap shortcuts + the goal group (extracted from the test_wl.py monolith)."""
import sqlite3
import pytest


class TestGoalRecapTick:
    """shortcuts: wl goal / wl recap (today) + wl tick (check-in)"""

    def test_goal_set_and_read(self, cli):
        cli("goal", "deliver X today")
        code, out, _ = cli("goal")
        assert "deliver X today" in out

    def test_goal_auto_creates_day(self, cli):
        # wl goal on an empty DB should auto-create today's day node
        cli("goal", "test target")
        from datetime import date
        today = date.today().isoformat()
        code, out, _ = cli("ls", "--prop", "type.date=day")
        assert today in out  # day node exists

    def test_auto_day_builds_full_time_ancestor_chain(self, cli, tmp_db):
        """Auto-created day must hang under week→month→quarter→year, not dangle."""
        from datetime import date
        cli("goal", "g")  # triggers _ensure_today_day on an empty DB
        con = tmp_db.db_connect()
        from worklog import node_types as nt, queries
        today = date.today()
        day = con.execute(
            "SELECT n.id, n.parent_id FROM node n WHERE n.deleted_at IS NULL AND n.title LIKE ? "
            "AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.date' AND value='day' AND deleted_at IS NULL) "
            "ORDER BY n.id LIMIT 1",
            (today.isoformat() + "%",)).fetchone()
        assert day["parent_id"] is not None, "day dangled with no parent"
        from worklog import cli as wl
        kinds = [nt.legacy_kind(queries.node_props(con, n["id"])) for n in wl._ancestors_chain(con, day["id"])]
        for k in ("year", "quarter", "month", "week"):
            assert k in kinds, f"time ancestor '{k}' missing from chain {kinds}"
        # day's direct parent is the week
        assert nt.legacy_kind(queries.node_props(con, day["parent_id"])) == "week"

    def test_auto_day_reuses_existing_year_any_title_style(self, cli, tmp_db):
        """Lenient year lookup: an existing year titled 'YYYY 年' (or any 'YYYY…') is
        reused, not duplicated by a new ISO 'YYYY' node (xyb's chosen behavior)."""
        from datetime import date
        y = date.today().year
        cli("add", f"{y} 年", "--prop", "type.date=year")  # pre-existing Chinese-style year
        cli("goal", "g")
        con = tmp_db.db_connect()
        n_years = con.execute(
            "SELECT COUNT(*) FROM node n WHERE n.deleted_at IS NULL "
            "AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.date' AND value='year' AND deleted_at IS NULL)"
        ).fetchone()[0]
        assert n_years == 1, "lenient lookup should reuse the existing year, not add an ISO duplicate"

    def test_recap_set_and_read(self, cli):
        cli("recap", "daily recap Y")
        code, out, _ = cli("recap")
        assert "daily recap Y" in out

    def test_recap_past_date_writes_and_stamps(self, cli, tmp_db):
        """wl recap --date <past day> back-fills that day's summary + stamps summary_at,
        building the day node if needed."""
        _, wout, _ = cli("recap", "--date", "2026-06-01", "backfilled recap")
        assert "2026-06-01" in wout
        # read it back via --date
        _, rout, _ = cli("recap", "--date", "2026-06-01")
        assert "backfilled recap" in rout
        assert "written at" in rout  # the summary log's logged_at is the write time
        con = tmp_db.db_connect()
        day = con.execute(
            "SELECT n.id FROM node n WHERE n.deleted_at IS NULL AND n.title LIKE '2026-06-01%' "
            "AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.date' AND value='day' AND deleted_at IS NULL) "
            "ORDER BY n.id LIMIT 1").fetchone()
        assert day is not None  # day node was created on demand
        # stored as a history-preserving typed log (not a prop); logged_at = write time
        s = con.execute("SELECT logged_at FROM log WHERE node_id=? AND tag='summary'", (day["id"],)).fetchone()
        assert s is not None and s["logged_at"]

    def test_recap_past_date_read_empty(self, cli):
        _, out, _ = cli("recap", "--date", "2026-06-01")
        assert "no summary set for 2026-06-01" in out

    def test_recap_bad_date_rejected(self, cli):
        code, _, err = cli("recap", "--date", "not-a-date", "x")
        assert code != 0

    def test_recap_empty_default(self, cli):
        code, out, _ = cli("recap")
        assert "no summary set for today" in out

    def test_recap_stamps_summary_at(self, cli):
        # recap writes a stamp; read and wl day both show "written at"; no new changes → no rewrite prompt
        cli("recap", "recap X")
        _, rout, _ = cli("recap")
        assert "written at" in rout
        _, dout, _ = cli("day")
        assert "written at" in dout
        assert "consider rewriting" not in dout

    def test_day_warns_when_changes_after_summary(self, cli, tmp_db):
        # mock recap written long ago; later non-CLOCK log that day → wl day suggests rewriting recap
        from datetime import date
        cli("recap", "recap v1")  # auto-creates today's day (+ its time-ancestor chain)
        con = tmp_db.db_connect()
        day = con.execute(
            "SELECT n.id FROM node n WHERE n.deleted_at IS NULL AND n.title LIKE ? "
            "AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.date' AND value='day' AND deleted_at IS NULL) "
            "ORDER BY n.id LIMIT 1",
            (date.today().isoformat() + "%",)).fetchone()
        # backdate the summary log to simulate "recap written long ago"
        con.execute("UPDATE log SET logged_at='2000-01-01 00:00:00' WHERE node_id=? AND tag='summary'",
                    (day["id"],))
        con.commit()
        cli("add", "work item")
        task = con.execute("SELECT id FROM node WHERE title='work item'").fetchone()
        cli("log", str(task["id"]), "worked more after recap")
        _, out, _ = cli("day")
        assert "consider rewriting" in out

    def test_tick_adds_log(self, cli):
        cli("add", "workout", "--prop", "type.habit=true")
        cli("tick", "1", "--note", "pull-ups x6")
        code, out, _ = cli("show", "1")
        assert "pull-ups x6" in out

    def test_tick_done_flag(self, cli):
        cli("add", "one-off task")
        cli("tick", "1", "--done")
        code, out, _ = cli("show", "1")
        assert "DONE" in out


def _typed_logs(con, nid, tag):
    return [r[0] for r in con.execute(
        "SELECT body FROM log WHERE node_id=? AND tag=? AND deleted_at IS NULL ORDER BY id", (nid, tag))]


class TestGoalGroup:
    """`wl goal set/ls/rm` — the group for the history-preserving reserved-tag logs
    (goal / summary) on any node, distinct from props. The bare `wl goal` / `wl recap` cover
    today; `wl set`/`wl unset` route goal/summary keys here (key-routed, parallel to prop)."""

    def test_goal_set_writes_typed_log(self, cli, tmp_db):
        cli("add", "w", "--prop", "type.date=week")
        cli("goal", "set", "1", "ship X")                    # week-level goal
        assert _typed_logs(tmp_db.db_connect(), 1, "goal") == ["ship X"]

    def test_set_shortcut_equals_goal_set(self, cli, tmp_db):
        cli("add", "w", "--prop", "type.date=week")
        cli("set", "1", "goal", "via shortcut")              # key-routed shortcut
        assert _typed_logs(tmp_db.db_connect(), 1, "goal") == ["via shortcut"]

    def test_goal_is_history_preserving(self, cli, tmp_db):
        cli("add", "d", "--prop", "type.date=day")
        cli("goal", "set", "1", "first")
        cli("goal", "set", "1", "second")                    # append, not overwrite
        assert _typed_logs(tmp_db.db_connect(), 1, "goal") == ["first", "second"]
        # ls shows the latest as current
        _, out, _ = cli("goal", "ls", "1")
        assert "second" in out and "first" not in out

    def test_goal_set_summary_flag(self, cli, tmp_db):
        cli("add", "d", "--prop", "type.date=day")
        cli("goal", "set", "1", "what happened", "--summary")  # backward recap, not a goal
        con = tmp_db.db_connect()
        assert _typed_logs(con, 1, "summary") == ["what happened"]
        assert _typed_logs(con, 1, "goal") == []

    def test_goal_ls_empty(self, cli):
        cli("add", "t")
        _, out, _ = cli("goal", "ls", "1")
        assert "no goal / summary" in out

    def test_goal_rm_clears(self, cli, tmp_db):
        cli("add", "d", "--prop", "type.date=day")
        cli("goal", "set", "1", "x")
        cli("goal", "rm", "1")
        assert _typed_logs(tmp_db.db_connect(), 1, "goal") == []

    def test_unset_goalkey_routes_to_goal_rm(self, cli, tmp_db):
        cli("add", "d", "--prop", "type.date=day")
        cli("set", "1", "goal", "x")
        cli("unset", "1", "goal")                            # key-routed: clears the typed log
        assert _typed_logs(tmp_db.db_connect(), 1, "goal") == []

    def test_goal_is_not_a_prop(self, cli, tmp_db):
        cli("add", "d", "--prop", "type.date=day")
        cli("goal", "set", "1", "g")                         # → log table
        cli("set", "1", "owner", "me")                       # → prop table
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM prop WHERE node_id=1 AND key='goal'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM prop WHERE node_id=1 AND key='owner'").fetchone()[0] == 1
        assert _typed_logs(con, 1, "goal") == ["g"]

    def test_goal_set_missing_node(self, cli):
        _, _, err = cli("goal", "set", "999", "x")
        assert "not found" in err


class TestGoalReadEmpty:
    def test_goal_read_with_none_set(self, cli):
        # bare `wl goal` on a fresh day (auto-created) with no goal log → the empty notice
        _, out, _ = cli("goal")
        assert "no goal set for today" in out


class TestRecapDiff:
    """#685: `wl recap --diff` lists the plain-note logs added AFTER the recap that day,
    excluding checkin/metric-carrier noise — so you can judge if a rewrite is warranted."""

    def _stale_recap(self, cli, tmp_db):
        from datetime import date
        cli("recap", "recap v1")
        con = tmp_db.db_connect()
        day = con.execute(
            "SELECT n.id FROM node n WHERE n.deleted_at IS NULL AND n.title LIKE ? "
            "AND EXISTS(SELECT 1 FROM prop WHERE node_id=n.id AND key='type.date' AND value='day' AND deleted_at IS NULL) "
            "ORDER BY n.id LIMIT 1",
            (date.today().isoformat() + "%",)).fetchone()
        con.execute("UPDATE log SET logged_at='2000-01-01 00:00:00' WHERE node_id=? AND tag='summary'",
                    (day["id"],))
        con.commit()
        return con

    def test_diff_shows_notes_excludes_checkin(self, cli, tmp_db):
        con = self._stale_recap(cli, tmp_db)
        cli("add", "work item")
        task = con.execute("SELECT id FROM node WHERE title='work item'").fetchone()
        cli("log", str(task["id"]), "real missed note", "--keep-status")
        cli("add", "workout", "--prop", "type.habit=true")
        hab = con.execute("SELECT id FROM node WHERE title='workout'").fetchone()
        cli("tick", str(hab["id"]), "--note", "pullups x6")  # checkin carrier — should be filtered
        code, out, _ = cli("recap", "--diff")
        assert code == 0
        assert "real missed note" in out          # genuine content surfaces
        assert "pullups x6" not in out             # checkin/metric noise filtered out
        assert "change(s) after" in out

    def test_diff_clean_when_nothing_after(self, cli, tmp_db):
        cli("recap", "all captured")
        code, out, _ = cli("recap", "--diff")  # nothing logged after
        assert code == 0 and "no changes after" in out.lower()

    def test_diff_no_recap(self, cli):
        code, out, _ = cli("recap", "--date", "2026-06-01", "--diff")
        assert code == 0 and ("no recap" in out.lower() or "nothing to diff" in out.lower())
