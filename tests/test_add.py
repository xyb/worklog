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
        cli("add", "t1", "-k", "task", "--log", "起步说明")
        _, show, _ = cli("show", "1")
        assert "起步说明" in show

    def test_add_with_done(self, cli):
        cli("add", "事后回顾", "-k", "task", "--done")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "closed_at" in show

    def test_add_with_done_and_at(self, cli):
        cli("add", "回顾过去", "-k", "task", "--done", "--at", "2025-01-02 09:00")
        _, show, _ = cli("show", "1")
        assert "closed_at 2025-01-02 09:00:00" in show

    def test_add_all_compound(self, cli):
        """one shot: add + done + log + at + link"""
        cli("add", "全套补录", "-k", "task", "-p", "B",
            "--log", "结果一句话", "--done", "--at", "2025-01-02 14:30",
            "--link", "vault doc 名")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "vault doc 名" in show
        assert "结果一句话" in show
        assert "2025-01-02 14:30" in show

    def test_add_invalid_at(self, cli):
        code, _, _ = cli("add", "x", "-k", "task", "--at", "garbage")
        assert code != 0

    def test_add_with_link_only(self, cli):
        cli("add", "x", "-k", "task", "--link", "DocOnly")
        _, show, _ = cli("show", "1")
        assert "DocOnly" in show
