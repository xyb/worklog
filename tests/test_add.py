"""Tests for add (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestAdd:
    def test_add_task(self, cli):
        code, out, _ = cli("add", "test task", "-k", "task", "-p", "A", "-t", "work,P0")
        assert code == 0
        assert "#1" in out
        assert "test task" in out

    def test_add_default_kind_is_task(self, cli):
        code, out, _ = cli("add", "default task")
        assert code == 0
        assert "#1" in out

    def test_add_project(self, cli, tmp_db):
        cli("add", "test project", "-k", "project", "-p", "A", "-t", "work")
        con = tmp_db.db_connect()
        row = con.execute("SELECT * FROM node WHERE id=1").fetchone()
        assert row["kind"] == "project"
        assert row["priority"] == "A"

    def test_add_time_hierarchy(self, cli, tmp_db):
        """lifetime -> year -> quarter -> month -> week -> day full hierarchy"""
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "2026", "-k", "year", "--parent", "1")
        cli("add", "Q2", "-k", "quarter", "--parent", "2")
        cli("add", "2026-05", "-k", "month", "--parent", "3")
        cli("add", "W21", "-k", "week", "--parent", "4")
        cli("add", "5-18 Monday", "-k", "day", "--parent", "5")

        con = tmp_db.db_connect()
        for nid, kind in [(1, "lifetime"), (2, "year"), (3, "quarter"), (4, "month"), (5, "week"), (6, "day")]:
            row = con.execute("SELECT kind FROM node WHERE id=?", (nid,)).fetchone()
            assert row["kind"] == kind

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
        cli("add", "task", "-k", "task")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] == "TODO"

    def test_project_no_default_status(self, cli, tmp_db):
        cli("add", "project", "-k", "project")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] is None

    def test_add_stamps_current_time(self, cli):
        # deliberate design: stamp "now" so the caller (esp. an AI whose date drifts to
        # session-start) sees the real current time on every content-creating command.
        import re
        _, out, _ = cli("add", "t", "-k", "task")
        assert re.search(r"@\d{4}-\d{2}-\d{2} \d{2}:\d{2}", out)


# ─── log ───


class TestAddAndShowExtras:
    def test_add_with_deadline(self, cli):
        cli("add", "deadly", "-k", "task", "--deadline", "2026-12-31")
        _, show, _ = cli("show", "1")
        assert "2026-12-31" in show

    def test_add_with_body(self, cli):
        cli("add", "with-body", "-k", "task", "--body", "body content here")
        _, show, _ = cli("show", "1")
        assert "body content" in show

    def test_show_with_ancestors(self, cli):
        cli("add", "parent", "-k", "project")
        cli("add", "child", "-k", "task", "--parent", "1")
        _, show, _ = cli("show", "2")
        assert "ancestors" in show
        assert "parent" in show

    def test_show_with_scheduled_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("sched", "1", "2026-06-01")
        # task's scheduled_at goes via the sched table; show renders scheduled event in timeline
        _, show, _ = cli("show", "1")
        # at least don't crash
        assert "t1" in show

    def test_show_with_props(self, cli):
        cli("add", "t1", "-k", "task")
        cli("set", "1", "owner", "yanbo")
        _, show, _ = cli("show", "1")
        assert "owner" in show and "yanbo" in show

    def test_ls_filter_by_multiple_tags(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work,foo,bar")
        cli("add", "t2", "-k", "task", "-t", "work")
        cli("add", "t3", "-k", "task", "-t", "foo")
        _, out, _ = cli("ls", "--tag", "work,foo")
        # AND: only t1 has both work + foo
        assert "t1" in out
        assert "t2" not in out
        assert "t3" not in out

    def test_ls_filter_by_kind(self, cli):
        cli("add", "h1", "-k", "habit")
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("ls", "--kind", "habit")
        assert "h1" in out
        assert "t1" not in out

    def test_ls_filter_by_parent(self, cli):
        cli("add", "P1", "-k", "project")
        cli("add", "c1", "-k", "task", "--parent", "1")
        cli("add", "c2", "-k", "task")
        _, out, _ = cli("ls", "--parent", "1")
        assert "c1" in out
        assert "c2" not in out


class TestAddCompound:
    """wl add --log / --done / --at / --link: one-shot add + log + close + done"""

    def test_add_with_log(self, cli):
        cli("add", "t1", "-k", "task", "--log", "kickoff note")
        _, show, _ = cli("show", "1")
        assert "kickoff note" in show

    def test_add_with_done(self, cli):
        cli("add", "retro", "-k", "task", "--done")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "closed_at" in show

    def test_add_with_done_and_at(self, cli):
        cli("add", "look back", "-k", "task", "--done", "--at", "2025-01-02 09:00")
        _, show, _ = cli("show", "1")
        assert "closed_at 2025-01-02 09:00:00" in show

    def test_add_all_compound(self, cli):
        """one shot: add + done + log + at + link"""
        cli("add", "backfill all", "-k", "task", "-p", "B",
            "--log", "outcome in one sentence", "--done", "--at", "2025-01-02 14:30",
            "--link", "vault doc name")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "vault doc name" in show
        assert "outcome in one sentence" in show
        assert "2025-01-02 14:30" in show

    def test_add_invalid_at(self, cli):
        code, _, _ = cli("add", "x", "-k", "task", "--at", "garbage")
        assert code != 0

    def test_add_with_link_only(self, cli):
        cli("add", "x", "-k", "task", "--link", "DocOnly")
        _, show, _ = cli("show", "1")
        assert "DocOnly" in show


class TestAddDuplicateWarning:
    """wl add warns (without blocking) when a similar open task/project already exists."""

    def test_warns_on_substring_overlap(self, cli):
        cli("add", "biz-agg slack-log merge", "-k", "task")
        code, out, _ = cli("add", "slack-log merge", "-k", "task")
        assert code == 0  # not blocked — the node is still created
        assert "similar open" in out
        assert "#1" in out  # points at the existing one

    def test_no_warn_when_unrelated(self, cli):
        cli("add", "biz-agg slack-log merge", "-k", "task")
        _, out, _ = cli("add", "buy groceries", "-k", "task")
        assert "similar open" not in out

    def test_no_warn_on_short_title_noise(self, cli):
        # 3-char titles shouldn't trigger substring matches against each other
        cli("add", "fix", "-k", "task")
        _, out, _ = cli("add", "abc", "-k", "task")
        assert "similar open" not in out

    def test_done_tasks_excluded_from_dup_check(self, cli):
        cli("add", "duplicate target", "-k", "task")
        cli("done", "1")
        _, out, _ = cli("add", "duplicate target", "-k", "task")
        assert "similar open" not in out  # the only match is DONE, so no warning

    def test_habit_kind_not_checked(self, cli):
        cli("add", "drink water", "-k", "habit")
        _, out, _ = cli("add", "drink water", "-k", "habit")
        assert "similar open" not in out  # dedup check is task/project only

    def test_exact_duplicate_warns(self, cli):
        cli("add", "exact same title", "-k", "project")
        _, out, _ = cli("add", "exact same title", "-k", "project")
        assert "similar open" in out


class TestAddSched:
    """add --sched (schedule at creation) + validation (from test_ux)"""
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

    def test_add_sched_and_scheduled_conflict(self, cli):
        code, _, err = cli("add", "t1", "-k", "task", "--sched", "today", "--scheduled", "下周")
        assert code != 0

    def test_empty_title_rejected(self, cli):
        code, _, err = cli("add", "", "-k", "task")
        assert code != 0
        code2, _, err2 = cli("add", "   ", "-k", "task")
        assert code2 != 0

