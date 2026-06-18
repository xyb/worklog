"""Tests for add (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestAdd:
    def test_add_task(self, cli):
        code, out, _ = cli("add", "test task", "-p", "A", "-t", "work,P0")
        assert code == 0
        assert "#1" in out
        assert "test task" in out

    def test_add_default_kind_is_task(self, cli):
        code, out, _ = cli("add", "default task")
        assert code == 0
        assert "#1" in out

    def test_add_project(self, cli, tmp_db):
        from worklog import node_types as nt, queries
        cli("add", "test project", "--para", "project", "-p", "A", "-t", "work")
        con = tmp_db.db_connect()
        row = con.execute("SELECT * FROM node WHERE id=1").fetchone()
        assert queries.node_type_from_props(queries.node_props(con, 1)) == "project"
        assert row["priority"] == "A"

    def test_add_time_hierarchy(self, cli, tmp_db):
        """lifetime -> year -> quarter -> month -> week -> day full hierarchy"""
        cli("add", "Lifetime", "--prop", "type.date=lifetime")
        cli("add", "2026", "--prop", "type.date=year", "--parent", "1")
        cli("add", "Q2", "--prop", "type.date=quarter", "--parent", "2")
        cli("add", "2026-05", "--prop", "type.date=month", "--parent", "3")
        cli("add", "W21", "--prop", "type.date=week", "--parent", "4")
        cli("add", "5-18 Monday", "--prop", "type.date=day", "--parent", "5")

        from worklog import node_types as nt, queries
        con = tmp_db.db_connect()
        for nid, kind in [(1, "lifetime"), (2, "year"), (3, "quarter"), (4, "month"), (5, "week"), (6, "day")]:
            assert queries.node_type_from_props(queries.node_props(con, nid)) == kind

        # tree path
        path = con.execute("SELECT label FROM v_node_path WHERE id=6").fetchone()
        assert "Lifetime" in path["label"]
        assert "5-18 Monday" in path["label"]

    def test_add_with_parent(self, cli, tmp_db):
        cli("add", "parent task")
        cli("add", "children", "--parent", "1")
        con = tmp_db.db_connect()
        child = con.execute("SELECT parent_id FROM node WHERE id=2").fetchone()
        assert child["parent_id"] == 1

    def test_add_with_multiple_tags(self, cli, tmp_db):
        cli("add", "multi-tag", "-t", "work,P0,planned,dev")
        con = tmp_db.db_connect()
        tags = {r[0] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert tags == {"work", "P0", "planned", "dev"}

    def test_add_with_proj(self, cli, tmp_db):
        cli("add", "with proj", "--proj", "dev_tooling")
        con = tmp_db.db_connect()
        row = con.execute("SELECT value FROM prop WHERE node_id=1 AND key='project'").fetchone()
        assert row["value"] == "dev_tooling"

    def test_add_chinese_title(self, cli, tmp_db):
        cli("add", "🎯 May Top5 #4 review investment direction", "-t", "personal,investment")
        con = tmp_db.db_connect()
        row = con.execute("SELECT title FROM node WHERE id=1").fetchone()
        assert "review investment direction" in row["title"]
        assert "🎯" in row["title"]
        tags = {r[0] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert "investment" in tags

    def test_task_default_status_todo(self, cli, tmp_db):
        cli("add", "task")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] == "TODO"

    def test_project_no_default_status(self, cli, tmp_db):
        cli("add", "project", "--para", "project")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] is None

    def test_add_stamps_current_time(self, cli):
        # deliberate design: stamp "now" so the caller (esp. an AI whose date drifts to
        # session-start) sees the real current time on every content-creating command.
        import re
        _, out, _ = cli("add", "t")
        assert re.search(r"@\d{4}-\d{2}-\d{2} \d{2}:\d{2}", out)


# ─── log ───


class TestAddAndShowExtras:
    def test_add_with_deadline(self, cli):
        cli("add", "deadly", "--deadline", "2026-12-31")
        _, show, _ = cli("show", "1")
        assert "2026-12-31" in show

    def test_add_with_body(self, cli):
        cli("add", "with-body", "--body", "body content here")
        _, show, _ = cli("show", "1")
        assert "body content" in show

    def test_show_with_ancestors(self, cli):
        cli("add", "parent", "--para", "project")
        cli("add", "child", "--parent", "1")
        _, show, _ = cli("show", "2")
        assert "ancestors" in show
        assert "parent" in show

    def test_show_with_scheduled_at(self, cli):
        cli("add", "t1")
        cli("sched", "1", "2026-06-01")
        # task's scheduled_at goes via the sched table; show renders scheduled event in timeline
        _, show, _ = cli("show", "1")
        # at least don't crash
        assert "t1" in show

    def test_show_with_props(self, cli):
        cli("add", "t1")
        cli("set", "1", "owner", "yanbo")
        _, show, _ = cli("show", "1")
        assert "owner" in show and "yanbo" in show

    def test_ls_filter_by_multiple_tags(self, cli):
        cli("add", "t1", "-t", "work,foo,bar")
        cli("add", "t2", "-t", "work")
        cli("add", "t3", "-t", "foo")
        _, out, _ = cli("ls", "--tag", "work,foo")
        # AND: only t1 has both work + foo
        assert "t1" in out
        assert "t2" not in out
        assert "t3" not in out

    def test_ls_filter_by_prop_habit(self, cli):
        cli("add", "h1", "--prop", "type.habit=true")
        cli("add", "t1")
        _, out, _ = cli("ls", "--prop", "type.habit")
        assert "h1" in out
        assert "t1" not in out

    def test_ls_filter_by_parent(self, cli):
        cli("add", "P1", "--para", "project")
        cli("add", "c1", "--parent", "1")
        cli("add", "c2")
        _, out, _ = cli("ls", "--parent", "1")
        assert "c1" in out
        assert "c2" not in out


class TestAddCompound:
    """wl add --log / --done / --at / --link: one-shot add + log + close + done"""

    def test_add_with_log(self, cli):
        cli("add", "t1", "--log", "kickoff note")
        _, show, _ = cli("show", "1")
        assert "kickoff note" in show

    def test_add_with_done(self, cli):
        cli("add", "retro", "--done")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "closed_at" in show

    def test_add_with_done_and_at(self, cli):
        cli("add", "look back", "--done", "--at", "2025-01-02 09:00")
        _, show, _ = cli("show", "1")
        assert "closed_at 2025-01-02 09:00:00" in show

    def test_add_all_compound(self, cli):
        """one shot: add + done + log + at + link"""
        cli("add", "backfill all", "-p", "B",
            "--log", "outcome in one sentence", "--done", "--at", "2025-01-02 14:30",
            "--link", "vault doc name")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "vault doc name" in show
        assert "outcome in one sentence" in show
        assert "2025-01-02 14:30" in show

    def test_add_invalid_at(self, cli):
        code, _, _ = cli("add", "x", "--at", "garbage")
        assert code != 0

    def test_add_with_link_only(self, cli):
        cli("add", "x", "--link", "DocOnly")
        _, show, _ = cli("show", "1")
        assert "DocOnly" in show


class TestAddDuplicateWarning:
    """wl add warns (without blocking) when a similar open task/project already exists."""

    def test_warns_on_substring_overlap(self, cli):
        cli("add", "biz-agg slack-log merge")
        code, out, _ = cli("add", "slack-log merge")
        assert code == 0  # not blocked — the node is still created
        assert "similar open" in out
        assert "#1" in out  # points at the existing one

    def test_no_warn_when_unrelated(self, cli):
        cli("add", "biz-agg slack-log merge")
        _, out, _ = cli("add", "buy groceries")
        assert "similar open" not in out

    def test_no_warn_on_short_title_noise(self, cli):
        # 3-char titles shouldn't trigger substring matches against each other
        cli("add", "fix")
        _, out, _ = cli("add", "abc")
        assert "similar open" not in out

    def test_done_tasks_excluded_from_dup_check(self, cli):
        cli("add", "duplicate target")
        cli("done", "1")
        _, out, _ = cli("add", "duplicate target")
        assert "similar open" not in out  # the only match is DONE, so no warning

    def test_habit_kind_not_checked(self, cli):
        cli("add", "drink water", "--prop", "type.habit=true")
        _, out, _ = cli("add", "drink water", "--prop", "type.habit=true")
        assert "similar open" not in out  # dedup check is task/project only

    def test_exact_duplicate_warns(self, cli):
        cli("add", "exact same title", "--para", "project")
        _, out, _ = cli("add", "exact same title", "--para", "project")
        assert "similar open" in out


class TestAddSched:
    """add --sched (schedule at creation) + validation (from test_ux)"""
    def test_add_sched_direct(self, cli):
        """wl add --sched today = add task and put it in the sched table at the same time"""
        cli("add", "today task", "--sched", "today")
        _, day, _ = cli("day")
        assert "#1" in day
        assert "today task" in day
        assert "planned" in day  # in the planned section, not unplanned

    def test_add_sched_yesterday(self, cli):
        cli("add", "backfill yesterday", "--sched", "yesterday")
        _, yday, _ = cli("day", "yesterday")
        assert "backfill yesterday" in yday

    def test_add_sched_invalid_date_errors(self, cli):
        code, _, err = cli("add", "work item", "--sched", "not-a-date")
        assert code != 0
        assert "bad date" in err or "bad date" in _ or "✗" in (err + _)

    def test_add_sched_and_scheduled_conflict(self, cli):
        code, _, err = cli("add", "t1", "--sched", "today", "--scheduled", "下周")
        assert code != 0

    def test_empty_title_rejected(self, cli):
        code, _, err = cli("add", "")
        assert code != 0
        code2, _, err2 = cli("add", "   ")
        assert code2 != 0

