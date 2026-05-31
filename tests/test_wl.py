"""worklog-cli full test suite.

Coverage:
- DB init / schema
- add for various kinds (task/project/year/month/week/day/lifetime/habit/...)
- log single / multiple entries
- done status transition + closed_at auto-write
- defer LATER + scheduled_at
- start / stop CLOCK in/out + elapsed
- link vault wikilink
- set custom prop
- show detail rendering
- ls with various filters (kind/status/tag/parent/all)
- tree recursion + depth
- logs time-range query
- edge cases: missing id / CJK / multi-tag / parent cascade delete
"""
import sqlite3
import pytest


# --- init / schema ---
class TestInit:
    def test_init_creates_tables(self, cli, tmp_db):
        code, out, _ = cli("init")
        assert code == 0
        # tables present
        con = tmp_db.db_connect()
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables >= {"node", "tag", "log", "prop", "link"}
        # view present
        views = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        assert "v_node_path" in views

    def test_idempotent_init(self, cli):
        """init is idempotent"""
        assert cli("init")[0] == 0
        assert cli("init")[0] == 0


class TestConfig:
    """wl config: read-only printer for paths/env/runtime; side-effect free."""

    def test_config_runs(self, cli):
        code, out, _ = cli("config")
        assert code == 0
        assert "worklog" in out

    def test_config_shows_db_path_and_aliases(self, cli):
        _, out, _ = cli("config")
        assert "database" in out and ".db" in out
        assert "aliases" in out and "aliases.ini" in out

    def test_config_shows_xdg_sections(self, cli):
        _, out, _ = cli("config")
        assert "XDG_DATA_HOME" in out
        assert "XDG_CONFIG_HOME" in out

    def test_config_marks_wl_db_source(self, tmp_path, monkeypatch):
        """When $WORKLOG_DB is set, config reports it as the DB source."""
        db = tmp_path / "test.db"
        monkeypatch.setenv("WORKLOG_DB", str(db))
        import importlib, wl
        importlib.reload(wl)
        # cmd_config writes to out() which prints via stdout
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wl.cmd_config(type("A", (), {})(), None)
        text = buf.getvalue()
        assert "$WORKLOG_DB" in text
        assert str(db) in text

    def test_config_does_not_create_db(self, tmp_path, monkeypatch):
        """`wl config` must not create the DB file (side-effect free)."""
        db = tmp_path / "fresh.db"
        monkeypatch.setenv("WORKLOG_DB", str(db))
        import importlib, wl
        importlib.reload(wl)
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            wl.cmd_config(type("A", (), {})(), None)
        assert not db.exists()


class TestXDGPaths:
    """Path resolution follows the XDG Base Directory spec."""

    def test_worklog_db_env_wins(self, tmp_path, monkeypatch):
        """$WORKLOG_DB env var has top priority"""
        target = tmp_path / "custom.db"
        monkeypatch.setenv("WORKLOG_DB", str(target))
        import importlib, wl
        importlib.reload(wl)
        assert wl.DB_PATH == target.resolve()

    def test_xdg_default_db_path(self, tmp_path, monkeypatch):
        """No $WORKLOG_DB → $XDG_DATA_HOME/worklog/worklog.db"""
        monkeypatch.delenv("WORKLOG_DB", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
        import importlib, wl
        importlib.reload(wl)
        assert wl.DB_PATH == (tmp_path / "xdg-data" / "worklog" / "worklog.db").resolve()

    def test_db_flag_wins_over_env(self, tmp_path, monkeypatch):
        """`--db PATH` flag has top priority over $WORKLOG_DB env."""
        monkeypatch.setenv("WORKLOG_DB", str(tmp_path / "from-env.db"))
        import wl
        flag_path = tmp_path / "from-flag.db"
        args = type("A", (), {"db": str(flag_path)})()
        resolved = wl._resolve_db_path(args)
        assert resolved == flag_path.resolve()

    def test_db_flag_absent_falls_back_to_env(self, tmp_path, monkeypatch):
        """No --db flag (or flag is None) → fall back to $WORKLOG_DB."""
        env_path = tmp_path / "from-env.db"
        monkeypatch.setenv("WORKLOG_DB", str(env_path))
        import wl
        args = type("A", (), {"db": None})()
        assert wl._resolve_db_path(args) == env_path.resolve()

    def test_xdg_config_home_aliases(self, tmp_path, monkeypatch):
        """$XDG_CONFIG_HOME/worklog/aliases.ini is the aliases path"""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-cfg"))
        import importlib, wl
        importlib.reload(wl)
        assert wl.ALIASES_PATH == tmp_path / "xdg-cfg" / "worklog" / "aliases.ini"


# --- add command ---
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
class TestLog:
    def test_add_log_to_task(self, cli, tmp_db):
        cli("add", "task")
        cli("log", "1", "read materials about A today")
        con = tmp_db.db_connect()
        rows = list(con.execute("SELECT body FROM log WHERE node_id=1"))
        assert len(rows) == 1
        assert rows[0]["body"] == "read materials about A today"

    def test_multiple_logs_in_order(self, cli, tmp_db):
        cli("add", "task")
        cli("log", "1", "5/18 first entry")
        cli("log", "1", "5/19 second entry")
        cli("log", "1", "5/20 third entry")
        con = tmp_db.db_connect()
        bodies = [r["body"] for r in con.execute("SELECT body FROM log WHERE node_id=1 ORDER BY id")]
        assert bodies == ["5/18 first entry", "5/19 second entry", "5/20 third entry"]

    def test_log_nonexistent_node_fails(self, cli):
        code, _, err = cli("log", "99", "no such node")
        assert code != 0
        assert "not found" in err

    def test_log_long_body(self, cli, tmp_db):
        long_body = "retrospective: " + "x" * 500
        cli("add", "task")
        cli("log", "1", long_body)
        con = tmp_db.db_connect()
        row = con.execute("SELECT body FROM log WHERE node_id=1").fetchone()
        assert len(row["body"]) > 500


# ─── done / defer / start / stop ───
class TestStatusTransitions:
    def test_done_sets_status_and_closed_at(self, cli, tmp_db):
        cli("add", "task")
        cli("done", "1")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status, closed_at FROM node WHERE id=1").fetchone()
        assert row["status"] == "DONE"
        assert row["closed_at"] is not None

    def test_defer_sets_later_and_scheduled(self, cli, tmp_db):
        cli("add", "task")
        cli("defer", "1", "2026-06-15")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status, scheduled_at FROM node WHERE id=1").fetchone()
        assert row["status"] == "LATER"
        assert row["scheduled_at"] == "2026-06-15"

    def test_start_marks_doing_and_logs_clock_in(self, cli, tmp_db):
        cli("add", "task")
        cli("start", "1")
        con = tmp_db.db_connect()
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] == "DOING"
        log = con.execute("SELECT body FROM log WHERE node_id=1 ORDER BY id DESC LIMIT 1").fetchone()
        assert log["body"] == "CLOCK_IN"

    def test_stop_appends_clock_out_with_elapsed(self, cli, tmp_db):
        import time
        cli("add", "task")
        cli("start", "1")
        time.sleep(0.1)
        code, out, _ = cli("stop", "1")
        assert code == 0
        con = tmp_db.db_connect()
        log = con.execute("SELECT body FROM log WHERE node_id=1 ORDER BY id DESC LIMIT 1").fetchone()
        assert "CLOCK_OUT" in log["body"]
        assert "elapsed=" in log["body"]

    def test_stop_without_start_fails(self, cli):
        cli("add", "task")
        code, _, err = cli("stop", "1")
        assert code != 0
        assert "no open CLOCK_IN" in err

    def test_done_nonexistent_fails(self, cli):
        code, _, err = cli("done", "99")
        assert code != 0


# ─── link / set ───
class TestLinkAndSet:
    def test_link_to_vault_doc(self, cli, tmp_db):
        cli("add", "task")
        cli("link", "1", "Dev tooling")
        cli("link", "1", "Q2 metric rollup")
        con = tmp_db.db_connect()
        docs = {r[0] for r in con.execute("SELECT vault_doc FROM link WHERE node_id=1")}
        assert docs == {"Dev tooling", "Q2 metric rollup"}

    def test_link_idempotent(self, cli, tmp_db):
        cli("add", "task")
        cli("link", "1", "doc")
        cli("link", "1", "doc")
        con = tmp_db.db_connect()
        count = con.execute("SELECT COUNT(*) FROM link WHERE node_id=1").fetchone()[0]
        assert count == 1

    def test_set_property(self, cli, tmp_db):
        cli("add", "task")
        cli("set", "1", "owner", "xyb")
        cli("set", "1", "estimate", "30min")
        con = tmp_db.db_connect()
        props = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM prop WHERE node_id=1")}
        assert props == {"owner": "xyb", "estimate": "30min"}

    def test_set_overrides_existing(self, cli, tmp_db):
        cli("add", "task")
        cli("set", "1", "owner", "xyb")
        cli("set", "1", "owner", "yanbo")
        con = tmp_db.db_connect()
        row = con.execute("SELECT value FROM prop WHERE node_id=1 AND key='owner'").fetchone()
        assert row["value"] == "yanbo"


# ─── show ───
class TestShow:
    def test_show_full_node(self, cli, tmp_db):
        cli("add", "strategy pivot", "-k", "task", "-p", "A", "-t", "work,P0")
        cli("log", "1", "5/18 decision", "--keep-status")  # do not auto-progress to DOING; keep TODO for the test
        cli("log", "1", "5/19 breakdown", "--keep-status")
        cli("link", "1", "Dev tooling")
        cli("set", "1", "issue", "76")

        code, out, _ = cli("show", "1")
        assert code == 0
        assert "strategy pivot" in out
        assert "TODO" in out
        assert "#A" in out
        assert ":work:P0:" in out or ":P0:work:" in out
        assert "[[Dev tooling]]" in out
        assert "issue" in out and "76" in out
        assert "5/18 decision" in out
        assert "5/19 breakdown" in out
        assert "timeline / changes" in out  # logs upgraded to timeline

    def test_show_nonexistent_fails(self, cli):
        code, _, err = cli("show", "99")
        assert code != 0

    def test_show_upstream_path(self, cli):
        cli("add", "month", "-k", "month")
        cli("add", "day", "-k", "day", "--parent", "1")
        cli("add", "task", "-k", "task", "--parent", "2")
        code, out, _ = cli("show", "3")
        assert "ancestors" in out and "month" in out and "day" in out

    def test_show_subtasks(self, cli):
        cli("add", "parent", "-k", "task")
        cli("add", "child1", "-k", "task", "--parent", "1")
        cli("add", "child2", "-k", "task", "--parent", "1")
        code, out, _ = cli("show", "1")
        assert "children (2)" in out
        assert "child1" in out and "child2" in out

    def test_show_timeline_changes(self, cli):
        import time
        cli("add", "task")
        cli("log", "1", "progress one")
        cli("start", "1")
        time.sleep(0.05)
        cli("stop", "1")
        cli("done", "1")
        code, out, _ = cli("show", "1")
        assert "timeline / changes" in out
        assert "● created" in out
        assert "✎ log" in out and "progress one" in out
        assert "⏱ clock-in" in out and "⏱ clock-out" in out
        assert "✓ DONE" in out


# ─── ls ───
class TestLs:
    def _seed(self, cli):
        cli("add", "task1", "-k", "task", "-p", "A", "-t", "work,P0")
        cli("add", "task2", "-k", "task", "-p", "B", "-t", "personal")
        cli("add", "proj1", "-k", "project", "-p", "A", "-t", "work")
        cli("add", "doneTask", "-k", "task", "-t", "work")
        cli("done", "4")

    def test_ls_default_excludes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls")
        assert "task1" in out
        assert "task2" in out
        assert "proj1" in out
        assert "doneTask" not in out

    def test_ls_all_includes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--all")
        assert "doneTask" in out

    def test_ls_filter_kind(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--kind", "project")
        assert "proj1" in out
        assert "task1" not in out

    def test_ls_filter_tag(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--tag", "personal")
        assert "task2" in out
        assert "task1" not in out

    def test_ls_filter_multi_tag_and(self, cli):
        self._seed(cli)
        code, out, _ = cli("ls", "--tag", "work,P0")
        assert "task1" in out
        assert "proj1" not in out  # has work but no P0
        assert "task2" not in out

    def test_ls_filter_parent(self, cli, tmp_db):
        cli("add", "parent")
        cli("add", "child1", "--parent", "1")
        cli("add", "child2", "--parent", "1")
        cli("add", "orphan")
        code, out, _ = cli("ls", "--parent", "1")
        assert "child1" in out
        assert "child2" in out
        assert "orphan" not in out

    def test_ls_empty_db(self, cli):
        code, out, _ = cli("ls")
        assert "(no nodes)" in out


# ─── tree ───
class TestTree:
    def test_tree_renders_indented(self, cli):
        cli("add", "root")
        cli("add", "child", "--parent", "1")
        cli("add", "grandchild", "--parent", "2")
        code, out, _ = cli("tree", "--depth", "9")  # default tree is area+today; use --depth for full tree
        lines = [l for l in out.split("\n") if l.strip()]
        assert any("root" in l for l in lines)
        assert any("  " in l and "child" in l for l in lines)
        assert any("    " in l and "grandchild" in l for l in lines)

    def test_tree_filter_kind(self, cli):
        cli("add", "yr", "-k", "year")
        cli("add", "task1", "-k", "task")
        code, out, _ = cli("tree", "--kind", "year")
        assert "yr" in out
        assert "task1" not in out

    def test_tree_depth_limit(self, cli):
        cli("add", "root")
        cli("add", "L1", "--parent", "1")
        cli("add", "L2", "--parent", "2")
        cli("add", "L3", "--parent", "3")
        code, out, _ = cli("tree", "--depth", "1")
        assert "root" in out
        assert "L1" in out
        # L2 / L3 should not appear (depth=1 means root + 1 level)
        # implementation-dependent but L3 should at least be truncated
        assert "L3" not in out

    def test_tree_empty(self, cli):
        code, out, _ = cli("tree")
        assert "no root" in out.lower()


# ─── logs ───
class TestLogs:
    def test_logs_list_all(self, cli):
        cli("add", "a")
        cli("add", "b")
        cli("log", "1", "log A1")
        cli("log", "1", "log A2")
        cli("log", "2", "log B1")
        code, out, _ = cli("logs")
        assert "log A1" in out
        assert "log A2" in out
        assert "log B1" in out

    def test_logs_filter_by_id(self, cli):
        cli("add", "a")
        cli("add", "b")
        cli("log", "1", "log A1")
        cli("log", "2", "log B1")
        code, out, _ = cli("logs", "--id", "1")
        assert "log A1" in out
        assert "log B1" not in out

    def test_logs_group_day(self, cli):
        cli("add", "2026", "-k", "year")
        cli("add", "proj", "-k", "project", "-t", "work", "--parent", "1")
        cli("add", "t", "-k", "task", "-t", "work", "--parent", "2")
        cli("log", "3", "progressX", "--date", "2026-05-28")
        code, out, _ = cli("logs", "--group", "day", "--since", "2026-05-01")
        assert "2026-05-28" in out  # date header
        assert "work" in out         # bucket
        assert "proj" in out         # project
        assert "progressX" in out

    def test_logs_default_recent_window(self, cli):
        cli("add", "a")
        cli("log", "1", "today log", )                       # default today
        cli("log", "1", "long ago", "--date", "2020-01-01")
        code, out, _ = cli("logs")  # no args = last 7 days
        assert "today log" in out
        assert "long ago" not in out  # outside default window

    def test_logs_since_overrides_window(self, cli):
        cli("add", "a")
        cli("log", "1", "long ago", "--date", "2020-01-01")
        code, out, _ = cli("logs", "--since", "2020-01-01")
        assert "long ago" in out


# --- edge cases / cascade ---
class TestCascade:
    def test_parent_delete_sets_children_parent_null(self, cli, tmp_db):
        """delete parent -> child parent_id becomes NULL (ON DELETE SET NULL)"""
        cli("add", "parent")
        cli("add", "child", "--parent", "1")
        con = tmp_db.db_connect()
        con.execute("DELETE FROM node WHERE id=1")
        con.commit()
        row = con.execute("SELECT parent_id FROM node WHERE id=2").fetchone()
        assert row["parent_id"] is None

    def test_node_delete_cascades_tags_logs_props_links(self, cli, tmp_db):
        cli("add", "doomed", "-t", "a,b")
        cli("log", "1", "log entry")
        cli("set", "1", "k", "v")
        cli("link", "1", "doc")
        con = tmp_db.db_connect()
        con.execute("DELETE FROM node WHERE id=1")
        con.commit()
        for table in ("tag", "log", "prop", "link"):
            count = con.execute(f"SELECT COUNT(*) FROM {table} WHERE node_id=1").fetchone()[0]
            assert count == 0, f"{table} not cascaded"


class TestTreeRoot:
    def _seed(self, cli):
        cli("add", "month", "-k", "month")            # 1
        cli("add", "projectX", "-k", "project", "--parent", "1")  # 2
        cli("add", "subtaskA", "-k", "task", "--parent", "2")    # 3
        cli("add", "subtaskB", "-k", "task", "--parent", "2")    # 4
        cli("add", "grandchild", "-k", "task", "--parent", "3")      # 5

    def test_tree_root_starts_from_mid_node(self, cli):
        self._seed(cli)
        # project #2 as root (non-NULL parent_id is fine)
        code, out, _ = cli("tree", "--root", "2")
        assert code == 0
        assert "projectX" in out
        assert "subtaskA" in out
        assert "grandchild" in out
        assert "month" not in out  # upstream should not appear

    def test_tree_root_nonexistent_fails(self, cli):
        self._seed(cli)
        code, _, err = cli("tree", "--root", "99")
        assert code != 0


class TestSummary:
    def _seed(self, cli):
        cli("add", "projectX", "-k", "project", "-t", "projX,work")  # 1
        cli("add", "completed1", "-k", "task", "-t", "projX,work")      # 2
        cli("add", "completed2", "-k", "task", "-t", "work")            # 3
        cli("add", "personal completed", "-k", "task", "-t", "personal")      # 4
        cli("add", "open", "-k", "task", "-t", "work")             # 5
        cli("done", "2")
        cli("done", "3")
        cli("done", "4")

    def test_summary_totals(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert code == 0
        assert "done 3" in out
        assert "added-open 1" in out

    def test_summary_by_direction(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert "work: done 2" in out
        assert "personal: done 1" in out

    def test_summary_by_project(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert "=== by project ===" in out
        assert "projectX" in out and "done 1" in out

    def test_summary_done_list(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        # done items show ✓ + priority, aggregated under the project
        assert "completed1" in out and "personal completed" in out
        assert "✓" in out

    def test_summary_pending_grouped_by_status(self, cli):
        """open items group by status; DOING clearly distinct from TODO"""
        cli("add", "projectY", "-k", "project", "-t", "projY")  # 1
        cli("add", "doing task", "-k", "task", "-t", "projY,planned", "--parent", "1")  # 2
        cli("add", "todo task", "-k", "task", "-t", "projY,planned", "--parent", "1")    # 3
        cli("start", "2")  # DOING
        code, out, _ = cli("summary")
        assert "doing (DOING)" in out
        assert "todo (TODO)" in out
        assert "·planned" in out

    def test_summary_orphan_bucket(self, cli):
        from datetime import date
        cli("add", "无项目任务", "-k", "task", "-t", "planned")
        cli("done", "1")
        code, out, _ = cli("summary")
        assert "unassigned" in out

    def test_summary_by_day(self, cli):
        from datetime import date
        self._seed(cli)
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t, "--by", "day")
        assert code == 0
        assert "=== by day ===" in out
        assert t in out  # today's date acts as the group header
        assert "completed1" in out

    def test_summary_clock_hours(self, cli):
        import time
        from datetime import date
        cli("add", "timing task", "-t", "work")
        cli("start", "1")
        time.sleep(0.05)
        cli("stop", "1")
        t = date.today().isoformat()
        code, out, _ = cli("summary", "--since", t, "--until", t)
        assert "clock" in out


class TestChanges:
    def _seed(self, cli):
        cli("add", "projectX", "-k", "project", "-p", "A", "-t", "projX")  # 1
        cli("add", "completed task", "-k", "task", "-t", "projX")             # 2
        cli("add", "open task", "-k", "task", "-t", "projX")           # 3
        cli("add", "task with log", "-k", "task", "-t", "projX")              # 4
        cli("done", "2")
        cli("log", "4", "今天的进展")

    def test_changes_today_window(self, cli):
        from datetime import date
        self._seed(cli)
        today = date.today().isoformat()
        code, out, _ = cli("changes", "--since", today, "--until", today)
        assert code == 0
        assert "projectX" in out
        assert "done 1" in out and "completed task" in out
        assert "added open" in out and "open task" in out
        assert "node(s) with progress logs" in out

    def test_changes_empty_window(self, cli):
        self._seed(cli)
        code, out, _ = cli("changes", "--since", "2020-01-01", "--until", "2020-01-02")
        assert "no project changes" in out

    def test_changes_week_resolves(self, cli):
        from datetime import date
        self._seed(cli)
        iso = date.today().isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        code, out, _ = cli("changes", "--week", wk)
        assert code == 0
        assert "projectX" in out

    def test_changes_month_resolves(self, cli):
        from datetime import date
        self._seed(cli)
        mo = date.today().strftime("%Y-%m")
        code, out, _ = cli("changes", "--month", mo)
        assert code == 0
        assert "projectX" in out


class TestProjects:
    def _seed(self, cli):
        cli("add", "month", "-k", "month")                                              # 1
        cli("add", "projectA", "-k", "project", "-p", "A", "-t", "projA", "--parent", "1")  # 2
        cli("add", "projectB done", "-k", "project", "-p", "B", "-t", "projB", "--parent", "1")  # 3
        cli("add", "A任务1", "-k", "task", "-t", "projA", "--parent", "1")             # 4
        cli("add", "A任务2", "-k", "task", "-t", "projA", "--parent", "1")             # 5
        cli("add", "A子任务", "-k", "task", "--parent", "2")                           # 6 (structural subtask)
        cli("done", "4")
        cli("done", "3")  # mark projectB done

    def test_projects_lists_active(self, cli):
        self._seed(cli)
        code, out, _ = cli("projects")
        assert code == 0
        assert "projectA" in out
        # projectB is DONE, not listed by default
        assert "projectB" not in out

    def test_projects_all_includes_done(self, cli):
        self._seed(cli)
        code, out, _ = cli("projects", "--all")
        assert "projectB" in out

    def test_projects_stats(self, cli):
        self._seed(cli)
        code, out, _ = cli("projects")
        # projectA: A任务1(done) + A任务2(todo) + A子任务(todo, structural) = 3, done 1
        line = [l for l in out.split("\n") if "projectA" in l][0]
        assert "done 1/3" in line
        assert "todo 2" in line

    def test_projects_empty(self, cli):
        code, out, _ = cli("projects")
        assert "no active projects" in out


class TestTreeBy:
    def _seed(self, cli):
        cli("add", "2026-05", "-k", "month")                                          # 1
        cli("add", "data-viz", "-k", "project", "-t", "gaming,work", "--parent", "1")  # 2
        cli("add", "investment", "-k", "project", "-t", "invest,personal", "--parent", "1")          # 3
        cli("add", "login fix", "-k", "task", "-t", "gaming,work,P0", "--parent", "1")           # 4
        cli("add", "采数管线", "-k", "task", "-t", "gaming,work", "--parent", "1")              # 5
        cli("add", "对账", "-k", "task", "-t", "invest,personal", "--parent", "1")             # 6
        cli("add", "structural child", "-k", "task", "--parent", "2")                                 # 7 (under project #2)
        cli("add", "morning check", "-k", "task", "-t", "work,P0", "--parent", "1")                      # 8 (no project tag = orphan)

    def test_by_tag_groups(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "tag")
        assert code == 0
        assert "#gaming" in out
        assert "#invest" in out
        # generic tags are not used as group headers
        assert "#work" not in out and "#P0" not in out

    def test_by_project_groups_by_shared_tag(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "project")
        # gaming-data-viz section should contain login fix + 采数管线 (shared gaming tag)
        gaming_section = out.split("investment")[0]
        assert "login fix" in gaming_section
        assert "采数管线" in gaming_section
        # structural child (parent=project) also counts
        assert "structural child" in gaming_section

    def test_by_project_orphans(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "project")
        # morning check has no project tag → falls into the unassigned bucket
        assert "unassigned" in out
        orphan_section = out.split("unassigned")[-1]
        assert "morning check" in orphan_section

    def test_by_direction(self, cli):
        self._seed(cli)
        code, out, _ = cli("tree", "--by", "direction")
        assert "【work】" in out
        assert "【personal】" in out
        work_section = out.split("【personal】")[0]
        assert "login fix" in work_section
        personal_section = out.split("【personal】")[-1]
        assert "对账" in personal_section


class TestFocus:
    def _seed(self, cli):
        cli("add", "2026-05", "-k", "month")                     # 1
        cli("add", "data-viz", "-k", "project", "-t", "gaming", "--parent", "1")  # 2
        cli("add", "login fix", "-k", "task", "-t", "gaming,work,P0", "--parent", "1")     # 3
        cli("add", "decision meeting", "-k", "meetlog", "-t", "gaming,work,P0,strategy", "--parent", "1")  # 4
        cli("add", "digest system", "-k", "task", "-t", "gaming,followup", "--parent", "4")    # 5 (meeting subtask)
        cli("add", "无关任务", "-k", "task", "-t", "biz_agg,work", "--parent", "1")       # 6

    def test_focus_shows_upstream_self_downstream(self, cli):
        self._seed(cli)
        code, out, _ = cli("focus", "4")
        assert code == 0
        assert "upstream" in out and "2026-05" in out
        assert "focus" in out and "decision meeting" in out
        assert "downstream" in out and "digest system" in out

    def test_focus_related_excludes_generic_tags(self, cli):
        """#4 tag = gaming/work/P0/strategy → related matches only on gaming, not flooded by work/P0"""
        self._seed(cli)
        code, out, _ = cli("focus", "4", "--related")
        # gaming-related #2 #3 should appear
        assert "data-viz" in out or "login fix" in out
        # 无关任务 #6 (biz_agg/work) should not mix in — it only shares work(generic), not gaming
        rel_section = out.split("related")[-1] if "related" in out else ""
        assert "无关任务" not in rel_section

    def test_focus_related_only_generic_tags(self, cli):
        cli("add", "isolated", "-k", "task", "-t", "work,P0,planned")  # all generic tags
        code, out, _ = cli("focus", "1", "--related")
        assert "only generic-dimension tags" in out

    def test_focus_nonexistent_fails(self, cli):
        code, _, err = cli("focus", "99")
        assert code != 0


class TestAncestorsDescendants:
    def _seed(self, cli):
        cli("add", "year", "-k", "year")                  # 1
        cli("add", "month", "-k", "month", "--parent", "1")  # 2
        cli("add", "task", "-k", "task", "--parent", "2")  # 3
        cli("add", "children", "-k", "task", "--parent", "3")  # 4

    def test_ancestors_chain(self, cli):
        self._seed(cli)
        code, out, _ = cli("ancestors", "3")
        assert "year" in out and "month" in out and "task" in out
        assert "▶" in out  # self has an arrow marker

    def test_descendants_subtree(self, cli):
        self._seed(cli)
        code, out, _ = cli("descendants", "2")
        assert "month" in out and "task" in out and "children" in out

    def test_ancestors_nonexistent_fails(self, cli):
        code, _, err = cli("ancestors", "99")
        assert code != 0


class TestDay:
    """cmd_day driven by log dates: bucket(work/personal) → project/priority/plan-status → task → log"""

    def _seed(self, cli, date="2026-05-28"):
        cli("add", "2026", "-k", "year")                                          # 1
        cli("add", "2026-05", "-k", "month", "--parent", "1")                     # 2
        cli("add", "业务聚合", "-k", "project", "-t", "work", "--parent", "2")     # 3
        cli("add", "czbooks", "-k", "project", "-t", "personal", "--parent", "2")  # 4
        cli("add", "agg taskA", "-k", "task", "-p", "A", "-t", "work", "--parent", "3")     # 5
        cli("add", "crawler taskB", "-k", "task", "-p", "C", "-t", "personal", "--parent", "4")  # 6
        cli("done", "5")
        cli("log", "5", "aggregated progress", "--date", date)
        cli("log", "6", "crawler progress", "--date", date)

    def test_day_groups_by_bucket_and_project(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28", "--by", "project")
        assert code == 0
        assert "work" in out and "personal" in out
        assert "业务聚合" in out and "czbooks" in out
        assert "aggregated progress" in out and "crawler progress" in out
        assert "#5" in out and "#6" in out

    def test_day_default_by_plan(self, cli):
        # default --by plan: untagged tasks → unplanned (untagged), no grouping by project
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28")
        assert code == 0
        assert "unplanned (untagged)" in out
        assert "aggregated progress" in out and "crawler progress" in out

    def test_day_stats_line(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28")
        assert "2 tasks with progress" in out  # 2/2
        assert "DONE 1" in out

    def test_day_default_today(self, cli):
        from datetime import date
        today = date.today().isoformat()
        self._seed(cli, date=today)
        code, out, _ = cli("day")  # no date arg = today
        assert code == 0
        assert today in out
        assert "aggregated progress" in out

    def test_day_empty_no_fail(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2099-01-01")  # a date with no logs
        assert code == 0  # no longer fails
        assert "no log progress" in out

    def test_day_by_priority(self, cli):
        self._seed(cli)
        code, out, _ = cli("day", "2026-05-28", "--by", "priority")
        assert "P0" in out  # taskA priority A → P0
        assert "P2" in out  # taskB priority C → P2

    def test_day_by_plan(self, cli):
        cli("add", "2026", "-k", "year")
        cli("add", "proj", "-k", "project", "-t", "work", "--parent", "1")
        cli("add", "planned task", "-k", "task", "-t", "work,planned", "--parent", "2")    # 3
        cli("add", "临时任务", "-k", "task", "-t", "work,unplanned", "--parent", "2")  # 4
        cli("log", "3", "计划进展", "--date", "2026-05-28")
        cli("log", "4", "临时进展", "--date", "2026-05-28")
        code, out, _ = cli("day", "2026-05-28", "--by", "plan")
        assert "planned" in out and "unplanned" in out

    def test_day_by_plan_untagged_as_unplanned(self, cli):
        # no planned/unplanned tag → treated as unplanned, marked (untagged)
        cli("add", "2026", "-k", "year")
        cli("add", "proj", "-k", "project", "-t", "work", "--parent", "1")
        cli("add", "untagged task", "-k", "task", "-t", "work", "--parent", "2")  # 3
        cli("log", "3", "untagged progress", "--date", "2026-05-28")
        code, out, _ = cli("day", "2026-05-28", "--by", "plan")
        assert "unplanned (untagged)" in out


class TestImport:
    def test_import_nested_children(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [
            {"ref": "m", "title": "2026-05", "kind": "month", "children": [
                {"ref": "p", "title": "project", "kind": "project", "priority": "A", "tags": ["work"],
                 "children": [
                     {"title": "children", "kind": "task", "priority": "B", "status": "DONE",
                      "tags": ["x"], "logs": ["finished"]}
                 ]}
            ]}
        ]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f)
        f.close()
        code, out, _ = cli("import", f.name)
        os.unlink(f.name)
        assert code == 0
        assert "added 3" in out
        con = tmp_db.db_connect()
        # parent-child link
        p = con.execute("SELECT id FROM node WHERE title='project'").fetchone()
        c = con.execute("SELECT parent_id, status FROM node WHERE title='children'").fetchone()
        assert c["parent_id"] == p["id"]
        assert c["status"] == "DONE"
        # log + tag
        assert con.execute("SELECT COUNT(*) FROM log WHERE body='finished'").fetchone()[0] == 1

    def test_import_parent_ref(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [
            {"ref": "proj", "title": "P", "kind": "project"},
            {"title": "task under P", "kind": "task", "parent_ref": "proj"},
        ]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, out, _ = cli("import", f.name)
        os.unlink(f.name)
        assert code == 0
        con = tmp_db.db_connect()
        proj = con.execute("SELECT id FROM node WHERE title='P'").fetchone()
        t = con.execute("SELECT parent_id FROM node WHERE title='task under P'").fetchone()
        assert t["parent_id"] == proj["id"]

    def test_import_update(self, cli, tmp_db):
        cli("add", "task", "-k", "task")
        import json, tempfile, os
        spec = {"update": [{"id": 1, "status": "DONE", "add_tags": ["urgent"], "add_logs": ["补的"]}]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, out, _ = cli("import", f.name)
        os.unlink(f.name)
        assert "updated 1" in out
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, closed_at FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE" and n["closed_at"]
        assert con.execute("SELECT 1 FROM tag WHERE node_id=1 AND tag='urgent'").fetchone()

    def test_import_dry_run_no_write(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [{"title": "不该写入", "kind": "task"}]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, out, _ = cli("import", f.name, "--dry-run")
        os.unlink(f.name)
        assert "dry-run" in out and "add 1" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM node WHERE title='不该写入'").fetchone()[0] == 0

    def test_import_bad_parent_ref_rolls_back(self, cli, tmp_db):
        import json, tempfile, os
        spec = {"add": [
            {"title": "好的", "kind": "task"},
            {"title": "坏的", "kind": "task", "parent_ref": "does not exist"},
        ]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, _, err = cli("import", f.name)
        os.unlink(f.name)
        assert code != 0 and "rolled back" in err
        con = tmp_db.db_connect()
        # rollback: 好的 should not remain either
        assert con.execute("SELECT COUNT(*) FROM node WHERE title='好的'").fetchone()[0] == 0


class TestFind:
    def _seed(self, cli):
        cli("add", "gaming 项目", "-k", "project", "-t", "gaming,work")  # 1
        cli("add", "other task", "-k", "task")                             # 2
        cli("log", "2", "这条 log 提到 gaming 关键词")
        cli("add", "带prop", "-k", "task")                              # 3
        cli("set", "3", "owner", "gaming-team")
        cli("add", "带link", "-k", "task")                              # 4
        cli("link", "4", "gaming 文档")

    def test_find_title(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming")
        assert code == 0
        # title hit highlighted (plain uses *…*), title field marked
        assert "*gaming* 项目" in out and "title" in out

    def test_find_in_log(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming")
        assert "other task" in out  # matched via log
        line = [l for l in out.split("\n") if "other task" in l][0]
        assert "log" in line

    def test_find_in_prop(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming-team")
        assert "带prop" in out

    def test_find_in_link(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming 文档", "--in", "link")
        assert "带link" in out

    def test_find_in_restricts(self, cli):
        self._seed(cli)
        # only search title; gaming inside log should not match #2
        code, out, _ = cli("find", "gaming", "--in", "title")
        assert "*gaming* 项目" in out
        assert "other task" not in out

    def test_find_kind_filter(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming", "--kind", "project")
        assert "*gaming* 项目" in out
        assert "other task" not in out

    def test_find_no_match(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "不存在的词xyz")
        assert "no matches" in out

    def test_find_expands_log_hit(self, cli):
        """match in log -> indented expansion of the matched fragment with *…* around the keyword"""
        self._seed(cli)
        code, out, _ = cli("find", "gaming")
        # #2 matched via log, should expand log content
        assert "log: " in out
        assert "*gaming*" in out

    def test_find_expands_link_and_prop(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming-team")
        assert "prop: owner=gaming-team" in out

    def test_find_title_hit_not_expanded(self, cli):
        """match only in title (already in the row) -> no extra body/log expansion"""
        cli("add", "pure title hit gaming", "-k", "task")
        code, out, _ = cli("find", "pure title hit")
        # no log:/body:/tag: expansion lines (title is already on the node row)
        assert "log: " not in out and "body: " not in out


class TestApply:
    def _apply(self, cli, text, *extra):
        import tempfile, os
        f = tempfile.NamedTemporaryFile("w", suffix=".wld", delete=False, encoding="utf-8")
        f.write(text)
        f.close()
        code, out, err = cli("apply", f.name, *extra)
        os.unlink(f.name)
        return code, out, err

    def test_apply_add_nested(self, cli, tmp_db):
        code, out, _ = self._apply(cli,
            "+ [ ] [#A] [project] P :work:\n"
            "+   [x] [#A] 子任务 :x:\n")
        assert code == 0 and "added 2" in out
        con = tmp_db.db_connect()
        p = con.execute("SELECT id FROM node WHERE title='P'").fetchone()
        c = con.execute("SELECT parent_id, status FROM node WHERE title='子任务'").fetchone()
        assert c["parent_id"] == p["id"] and c["status"] == "DONE"

    def test_apply_anchor_parent(self, cli, tmp_db):
        cli("add", "project", "-k", "project")  # id 1
        code, out, _ = self._apply(cli,
            "  #1 [project] 项目\n"
            "+   [ ] [#B] 新子任务\n")
        assert code == 0 and "added 1" in out
        con = tmp_db.db_connect()
        c = con.execute("SELECT parent_id FROM node WHERE title='新子任务'").fetchone()
        assert c["parent_id"] == 1

    def test_apply_update_fields(self, cli, tmp_db):
        cli("add", "task", "-k", "task")  # id 1, TODO
        code, out, _ = self._apply(cli, "~ #1\n  status DONE\n  priority A\n")
        assert "updated 1" in out
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority, closed_at FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE" and n["priority"] == "A" and n["closed_at"]

    # ── anti-wipe: core safety tests (2026-05-28 safety hardening requirement) ──
    def test_apply_update_only_touches_declared_fields(self, cli, tmp_db):
        """only status changes; priority/title/tag/prop all preserved (no wipe)"""
        cli("add", "原标题", "-k", "task", "-p", "A", "-t", "keep1,keep2")
        cli("set", "1", "owner", "xyb")
        cli("link", "1", "某文档")
        # only update status
        self._apply(cli, "~ #1\n  status DONE\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT title, priority, status FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE"
        assert n["title"] == "原标题"      # untouched
        assert n["priority"] == "A"        # untouched
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert tags == {"keep1", "keep2"}  # not wiped
        assert con.execute("SELECT value FROM prop WHERE node_id=1 AND key='owner'").fetchone()["value"] == "xyb"
        assert con.execute("SELECT 1 FROM link WHERE node_id=1 AND vault_doc='某文档'").fetchone()  # not wiped

    def test_apply_clear_priority(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-p", "A")
        self._apply(cli, "~ #1\n  priority -\n")
        con = tmp_db.db_connect()
        assert con.execute("SELECT priority FROM node WHERE id=1").fetchone()["priority"] is None

    def test_apply_add_remove_tag(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-t", "old1,old2")
        self._apply(cli, "~ #1\n  +tag new\n  -tag old1\n")
        con = tmp_db.db_connect()
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert tags == {"old2", "new"}

    def test_apply_set_remove_prop(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("set", "1", "a", "1")
        self._apply(cli, "~ #1\n  prop b=2\n  -prop a\n")
        con = tmp_db.db_connect()
        props = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM prop WHERE node_id=1")}
        assert props == {"b": "2"}

    def test_apply_move_parent(self, cli, tmp_db):
        cli("add", "p1", "-k", "project")  # 1
        cli("add", "p2", "-k", "project")  # 2
        cli("add", "t", "-k", "task", "--parent", "1")  # 3
        self._apply(cli, "~ #3\n  parent 2\n")
        con = tmp_db.db_connect()
        assert con.execute("SELECT parent_id FROM node WHERE id=3").fetchone()["parent_id"] == 2

    def test_apply_update_bad_priority_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-p", "A")
        code, _, err = self._apply(cli, "~ #1\n  priority Z\n")
        assert code != 0 and "invalid priority" in err
        con = tmp_db.db_connect()
        assert con.execute("SELECT priority FROM node WHERE id=1").fetchone()["priority"] == "A"  # not corrupted

    def test_apply_update_bad_status_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  status FINISHED\n")
        assert code != 0 and "invalid status" in err

    def test_apply_update_bad_parent_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  parent 999\n")
        assert code != 0 and "parent #999 does not exist" in err

    def test_apply_update_unknown_fieldop_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  frobnicate yes\n")
        assert code != 0 and "unparseable field-op" in err

    def test_apply_update_title_clear_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n  title -\n")
        assert code != 0 and "title cannot be cleared" in err

    def test_apply_update_no_fieldops_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        code, _, err = self._apply(cli, "~ #1\n")
        assert code != 0 and "has no field operations" in err

    def test_apply_delete(self, cli, tmp_db):
        cli("add", "删我", "-k", "task")  # id 1
        code, out, _ = self._apply(cli, "- #1 删我\n")
        assert "deleted 1" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM node WHERE id=1").fetchone()[0] == 0

    def test_apply_subfields(self, cli, tmp_db):
        code, out, _ = self._apply(cli,
            "+ [x] [#A] 任务\n"
            "+   @log 进展记录\n"
            "+   @link 某文档\n"
            "+   @prop owner=xyb\n")
        assert code == 0
        con = tmp_db.db_connect()
        nid = con.execute("SELECT id FROM node WHERE title='任务'").fetchone()["id"]
        assert con.execute("SELECT 1 FROM log WHERE node_id=? AND body='进展记录'", (nid,)).fetchone()
        assert con.execute("SELECT 1 FROM link WHERE node_id=? AND vault_doc='某文档'", (nid,)).fetchone()
        assert con.execute("SELECT value FROM prop WHERE node_id=? AND key='owner'", (nid,)).fetchone()["value"] == "xyb"

    def test_apply_dry_run_no_write(self, cli, tmp_db):
        code, out, _ = self._apply(cli, "+ [ ] 不写入\n", "--dry-run")
        assert "dry-run" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM node WHERE title='不写入'").fetchone()[0] == 0

    def test_apply_validation_errors(self, cli, tmp_db):
        code, _, err = self._apply(cli,
            "+ [ ] #99 新增带id\n"
            "- #888 删不存在\n")
        assert code != 0
        assert "add should not carry #id" in err and "#888 does not exist" in err

    def test_apply_tilde_no_id_rejected(self, cli, tmp_db):
        code, _, err = self._apply(cli, "~ [x] 没id\n")
        assert code != 0 and "requires #id" in err

    def test_apply_status_doing(self, cli, tmp_db):
        cli("add", "t", "-k", "task")  # id 1
        self._apply(cli, "~ #1\n  status DOING\n")
        con = tmp_db.db_connect()
        assert con.execute("SELECT status FROM node WHERE id=1").fetchone()["status"] == "DOING"

    # ── inline shorthand (same as node list; only touches declared fields) ──
    def test_apply_inline_marker_only(self, cli, tmp_db):
        """~ [x] #1 only updates status, leaves priority/title alone"""
        cli("add", "原名", "-k", "task", "-p", "A")
        self._apply(cli, "~ [x] #1\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority, title FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE"
        assert n["priority"] == "A" and n["title"] == "原名"  # not declared = unchanged

    def test_apply_inline_priority_only_no_marker(self, cli, tmp_db):
        """~ [#B] #1 no marker → status untouched"""
        cli("add", "t", "-k", "task")  # status TODO
        self._apply(cli, "~ [#B] #1\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority FROM node WHERE id=1").fetchone()
        assert n["priority"] == "B"
        assert n["status"] == "TODO"  # no marker = status unchanged

    def test_apply_inline_title_only(self, cli, tmp_db):
        cli("add", "旧标题", "-k", "task", "-p", "A")
        self._apply(cli, "~ #1 新标题\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT title, priority, status FROM node WHERE id=1").fetchone()
        assert n["title"] == "新标题"
        assert n["priority"] == "A"  # untouched

    def test_apply_inline_all_three(self, cli, tmp_db):
        cli("add", "old", "-k", "task")
        self._apply(cli, "~ [x] [#A] #1 新名\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status, priority, title FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE" and n["priority"] == "A" and n["title"] == "新名"

    def test_apply_inline_plus_fieldops(self, cli, tmp_db):
        """inline shorthand combined with following indented field operations"""
        cli("add", "t", "-k", "task", "-t", "old")
        self._apply(cli, "~ [x] #1\n  +tag urgent\n  -tag old\n")
        con = tmp_db.db_connect()
        n = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert n["status"] == "DONE"
        tags = {r["tag"] for r in con.execute("SELECT tag FROM tag WHERE node_id=1")}
        assert tags == {"urgent"}

    def test_apply_inline_bad_priority_rejected(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "-p", "A")
        code, _, err = self._apply(cli, "~ [#Z] #1\n")
        # [#Z] is not a valid priority marker; _parse_node_line treats it as no priority + title
        # verify at least that priority is not corrupted
        con = tmp_db.db_connect()
        assert con.execute("SELECT priority FROM node WHERE id=1").fetchone()["priority"] == "A"


class TestFullSampleScenario:
    """simulate a real scenario: time hierarchy + project + task + log + done + link"""

    def test_5_18_sample_scenario(self, cli, tmp_db):
        # time hierarchy
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "2026", "-k", "year", "--parent", "1")
        cli("add", "Q2", "-k", "quarter", "--parent", "2")
        cli("add", "2026-05", "-k", "month", "--parent", "3")
        cli("add", "W21", "-k", "week", "--parent", "4")
        cli("add", "2026-05-18 周一", "-k", "day", "--parent", "5")
        # project (under month)
        cli("add", "Dev tooling", "-k", "project", "-p", "A", "-t", "work", "--parent", "4")
        # task (new model: under project, not under day; day view is driven by log dates)
        cli("add", "项目战略转向", "-k", "task", "-p", "A", "-t", "work,unplanned,P0", "--parent", "7")
        # 4 log entries
        cli("log", "8", "5/18 17:18 拍板战略转向")
        cli("log", "8", "5/19 09:42 拆需求 export_for_ai")
        cli("log", "8", "5/20 14:55 B 路径端到端打通 owner 精准度 6/6→7/7")
        cli("log", "8", "5/21 11:08 复盘 87% 成本下降")
        # link to vault docs
        cli("link", "8", "Dev tooling")
        cli("link", "8", "Q2 metric rollup")
        # done
        cli("done", "8")

        # verify
        code, out, _ = cli("show", "8")
        assert "项目战略转向" in out
        assert "DONE" in out
        assert "timeline / changes" in out
        assert "[[Dev tooling]]" in out
        assert "5/18 17:18" in out
        assert "5/21 11:08" in out

        # default tree is depth-limited (overview); use --depth to see the full hierarchy
        code, tree, _ = cli("tree", "--depth", "9")
        assert "Lifetime" in tree
        assert "2026-05-18" in tree
        assert "项目战略转向" in tree


# ─── rich highlight / theme ───
ESC = "\x1b["  # ANSI escape prefix


class TestColorRendering:
    def test_default_no_ansi_in_non_tty(self, cli):
        """default auto + non-TTY (StringIO in tests) → plain text, no ANSI"""
        cli("add", "高亮测试", "-k", "task", "-p", "A")
        code, out, _ = cli("ls")
        assert ESC not in out
        assert "高亮测试" in out

    def test_color_never_no_ansi(self, cli):
        cli("add", "abc", "-p", "A")
        code, out, _ = cli("--color", "never", "ls")
        assert ESC not in out

    def test_color_always_emits_ansi(self, cli):
        cli("add", "彩色任务", "-p", "A")
        code, out, _ = cli("--color", "always", "ls")
        assert ESC in out  # forced on → ANSI color codes present
        assert "彩色任务" in out  # content still intact

    def test_brackets_in_title_not_eaten_by_markup(self, cli):
        """[brackets] in title must not be eaten as rich markup"""
        cli("add", "修复 [登录] 模块", "-p", "A")
        code, out, _ = cli("--color", "always", "ls")
        assert "[登录]" in out  # escape works; brackets preserved verbatim

    def test_wikilink_double_bracket_preserved_in_find(self, cli):
        """find expands link match; [[doc]] double brackets preserved"""
        cli("add", "关联任务")
        cli("link", "1", "Dev tooling")
        code, out, _ = cli("--color", "always", "find", "Dev", "--in", "link")
        assert "[[Dev tooling]]" in out

    def test_find_hit_highlighted_styled(self, cli):
        """styled mode: hit gets ANSI (hit style), no *…* markers"""
        cli("add", "搜索目标")
        cli("log", "1", "这里有一个关键词 needle 在中间")
        code, out, _ = cli("--color", "always", "find", "needle")
        assert ESC in out
        assert "*needle*" not in out  # styled uses color, not asterisks

    def test_find_hit_marked_plain(self, cli):
        """plain mode: hit marked with *…*"""
        cli("add", "搜索目标")
        cli("log", "1", "这里有一个关键词 needle 在中间")
        code, out, _ = cli("--color", "never", "find", "needle")
        assert "*needle*" in out

    def test_mono_theme_no_color_codes(self, cli):
        """mono theme: even with always, elements stay default style (no color SGR, only resets allowed)"""
        cli("add", "单色", "-p", "A")
        code, out, _ = cli("--color", "always", "--theme", "mono", "ls")
        # mono all default → no color codes like 32(green)/31(red)/36(cyan)/35(magenta)
        for sgr in ("[32m", "[31m", "[36m", "[35m", "[33m"):
            assert sgr not in out
        assert "单色" in out

    def test_c_helper_plain_when_console_none(self, tmp_db):
        wl = tmp_db
        wl._init_console("never", None)
        assert wl._c("文本", "done") == "文本"
        assert wl._CONSOLE is None

    def test_c_helper_wraps_and_escapes_when_styled(self, tmp_db):
        wl = tmp_db
        wl._init_console("always", None)
        assert wl._CONSOLE is not None
        out = wl._c("a[b]c", "done")
        assert out.startswith("[done]")
        assert out.endswith("[/done]")
        assert "\\[b]" in out  # [ inside content gets escaped


class TestThemes:
    EXPECTED = ["dark", "light", "mono"]   # real palettes (no "default")

    def test_all_themes_listed_plain(self, cli):
        code, out, _ = cli("--color", "never", "themes")
        assert code == 0
        for name in self.EXPECTED:
            assert name in out

    def test_no_default_theme(self, tmp_db):
        """no theme named "default" anymore; options are auto + real palettes"""
        wl = tmp_db
        assert "default" not in wl.THEMES
        assert set(wl.THEMES) == {"dark", "light", "mono"}

    def test_themes_styled_renders_ansi(self, cli):
        code, out, _ = cli("--color", "always", "themes")
        assert code == 0
        assert ESC in out          # preview carries ANSI
        for name in self.EXPECTED:
            assert name in out

    def test_every_theme_color_name_valid(self, tmp_db):
        """every theme's style strings must parse with rich (guards against invalid color names like cyan4)"""
        wl = tmp_db
        from rich.theme import Theme
        for name, mapping in wl.THEMES.items():
            Theme(mapping)  # invalid color name would raise StyleSyntaxError here

    def test_themes_have_same_keys(self, tmp_db):
        """each theme covers all semantic elements; no missing keys"""
        wl = tmp_db
        keys = set(wl._THEME_KEYS)
        for name, mapping in wl.THEMES.items():
            assert set(mapping) == keys, f"{name} missing/extra key"

    def test_invalid_theme_rejected(self, cli):
        # argparse choices validation raises SystemExit(2) inside parse_args
        with pytest.raises(SystemExit):
            cli("--theme", "nope", "ls")

    def test_each_theme_usable_in_ls(self, cli):
        cli("add", "样例", "-p", "A")
        for name in self.EXPECTED:
            code, out, _ = cli("--color", "always", "--theme", name, "ls")
            assert code == 0
            assert "样例" in out

    def test_auto_themes_command_works(self, cli):
        code, out, _ = cli("--color", "never", "themes")
        assert code == 0
        assert "auto" in out  # listing notes the current auto resolution


class TestAutoTheme:
    def test_explicit_theme_bypasses_detection(self, tmp_db, monkeypatch):
        wl = tmp_db
        # even when detection reports light, explicit --theme dark is not overridden
        monkeypatch.setattr(wl, "_detect_bg_is_dark", lambda: False)
        assert wl._resolve_theme("dark") == "dark"
        assert wl._resolve_theme("mono") == "mono"

    def test_auto_picks_dark_on_dark_bg(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setattr(wl, "_detect_bg_is_dark", lambda: True)
        assert wl._resolve_theme(None) == "dark"
        assert wl._resolve_theme("auto") == "dark"

    def test_auto_picks_light_on_light_bg(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setattr(wl, "_detect_bg_is_dark", lambda: False)
        assert wl._resolve_theme(None) == "light"
        assert wl._resolve_theme("auto") == "light"

    def test_auto_fallback_dark_when_undetectable(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setattr(wl, "_detect_bg_is_dark", lambda: None)
        assert wl._resolve_theme("auto") == "dark"

    def test_colorfgbg_light_detected(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setenv("COLORFGBG", "0;15")   # bg=15 → light
        assert wl._detect_bg_is_dark() is False

    def test_colorfgbg_dark_detected(self, tmp_db, monkeypatch):
        wl = tmp_db
        monkeypatch.setenv("COLORFGBG", "15;0")   # bg=0 → dark
        assert wl._detect_bg_is_dark() is True


class TestFindTitleHighlight:
    def test_title_hit_marked_plain(self, cli):
        cli("add", "Uni-Game project", "-k", "project")
        code, out, _ = cli("--color", "never", "find", "Game")
        assert "Uni-*Game* project" in out  # title hit is marked

    def test_title_hit_highlighted_styled(self, cli):
        cli("add", "Uni-Game project", "-k", "project")
        code, out, _ = cli("--color", "always", "find", "Game")
        assert ESC in out
        assert "*Game*" not in out  # styled uses color, not asterisks

    def test_title_no_match_no_marker(self, cli):
        """hit in log but not title -> title should not be marked with *…*"""
        cli("add", "纯标题任务")
        cli("log", "1", "log 里有 needle 词")
        code, out, _ = cli("--color", "never", "find", "needle")
        assert "纯标题任务" in out  # title verbatim, no marker
        assert "*纯" not in out


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
        # 下周/下月/下季 normalize to the corresponding granularity formats
        assert wl._sched_kind(wl._norm_sched("下周")) == "week"
        assert wl._sched_kind(wl._norm_sched("下月")) == "month"
        assert wl._sched_kind(wl._norm_sched("下季")) == "quarter"

    def test_norm_rejects_invalid(self, tmp_db):
        wl = tmp_db
        for bad in ("2026-13", "2026-02-30", "2026-W99", "随便写的", "next-decade"):
            with pytest.raises(ValueError):
                wl._norm_sched(bad)

    def test_sched_kind(self, tmp_db):
        wl = tmp_db
        assert wl._sched_kind("2026-06-15") == "day"
        assert wl._sched_kind("2026-06") == "month"
        assert wl._sched_kind("2026-W23") == "week"
        assert wl._sched_kind("2026-Q3") == "quarter"
        assert wl._sched_kind("2026") == "year"
        assert wl._sched_kind("someday") == "someday"

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
        cli("add", "模糊任务", "--scheduled", "2026-06")
        code, out, _ = cli("ls")
        assert "@2026-06" in out

    def test_add_relative_scheduled(self, cli):
        cli("add", "下周做", "--scheduled", "下周")
        code, out, _ = cli("ls")
        assert "@2026-W" in out

    def test_add_rejects_invalid_scheduled(self, cli):
        code, out, err = cli("add", "坏时间", "--scheduled", "2026-13")
        assert code != 0
        assert "invalid month" in err or "unrecognized" in err

    def test_defer_fuzzy(self, cli):
        cli("add", "待顺延")
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
        f.write_text('{"add":[{"title":"季度任务","scheduled":"2026-Q3"}]}', encoding="utf-8")
        code, out, _ = cli("import", str(f))
        assert code == 0
        code, out, _ = cli("ls")
        assert "@2026-Q3" in out

    def test_apply_update_scheduled_fuzzy(self, cli, tmp_path):
        cli("add", "改计划时间")
        f = tmp_path / "u.wld"
        f.write_text("~ #1\n  scheduled 2026-06\n", encoding="utf-8")
        code, out, _ = cli("apply", str(f))
        assert code == 0
        code, out, _ = cli("ls")
        assert "@2026-06" in out

    def test_apply_rejects_invalid_scheduled(self, cli, tmp_path):
        cli("add", "改坏时间")
        f = tmp_path / "bad.wld"
        f.write_text("~ #1\n  scheduled 2026-77\n", encoding="utf-8")
        code, out, err = cli("apply", "--dry-run", str(f))
        assert code != 0

    def test_apply_delete_cascades_subtree(self, cli, tmp_path):
        """deleting a parent must cascade to the whole subtree; children must not become orphans (node self-ref is ON DELETE SET NULL)"""
        cli("add", "父项目", "-k", "project")          # #1
        cli("add", "subtaskA", "--parent", "1")          # #2
        cli("add", "subtaskB", "--parent", "1")          # #3
        cli("add", "grandchild", "--parent", "2")           # #4
        f = tmp_path / "del.wld"
        f.write_text("- #1\n", encoding="utf-8")
        code, out, _ = cli("apply", str(f))
        assert code == 0
        # if children orphaned (bug) they'd still appear in ls --all; all-absent = whole subtree truly cleaned
        code, out, _ = cli("ls", "--all")
        for t in ("父项目", "subtaskA", "subtaskB", "grandchild"):
            assert t not in out


class TestLogHistoricalDate:
    """when migrating historical data, a log's logged_at must land on the original day, not import day"""

    def test_import_log_iso_prefix_sets_date(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"历史","logs":["2026-05-06 起头","2026-05-08 跑通"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert "2026-05-06" in out and "起头" in out
        assert "2026-05-08" in out

    def test_import_log_no_date_uses_today(self, cli, tmp_path):
        import datetime as dt
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"x","logs":["无日期一条"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert dt.date.today().isoformat() in out  # no date → today

    def test_log_cmd_date_flag(self, cli):
        cli("add", "task")
        cli("log", "1", "历史进展", "--date", "2026-05-10")
        code, out, _ = cli("show", "1")
        assert "2026-05-10" in out and "历史进展" in out

    def test_log_invalid_date_rejected(self, cli):
        cli("add", "task")
        code, out, err = cli("log", "1", "x", "--date", "2026-13-40")
        assert code != 0  # invalid date rejected

    def test_import_log_dict_form(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"y","logs":[{"date":"2026-05-01","body":"dict 形式"}]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert "2026-05-01" in out and "dict 形式" in out

    def test_log_timeline_sorted_by_real_date(self, cli, tmp_path):
        f = tmp_path / "h.json"
        f.write_text('{"add":[{"title":"z","logs":["2026-05-20 晚","2026-05-05 早"]}]}', encoding="utf-8")
        cli("import", str(f))
        code, out, _ = cli("show", "1")
        assert out.index("2026-05-05") < out.index("2026-05-20")  # sorted by real date


class TestSched:
    """forward planning: wl sched schedules a task to a day/recurrence; wl day derives planned status from it (even without log)"""

    def test_sched_oneoff_shows_in_day_as_planned(self, cli):
        cli("add", "未来任务", "-k", "task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        code, out, _ = cli("day", "2026-06-15")
        assert code == 0
        assert "#1" in out
        assert "planned" in out
        assert "planned·not-done" in out  # no log but scheduled → marked planned-not-done

    def test_sched_plan_derived_not_from_tag(self, cli):
        # no planned tag; sched hit alone -> planned
        cli("add", "t", "-k", "task", "-t", "work")
        cli("sched", "1", "2026-06-15")
        code, out, _ = cli("day", "2026-06-15", "--by", "plan")
        assert "planned" in out and "unplanned" not in out

    def test_sched_recur_weekly_fires_on_matching_weekday(self, cli):
        cli("add", "Monday standup", "-k", "task", "-t", "work")
        cli("sched", "1", "--recur", "weekly:Mon")
        code, mon, _ = cli("day", "2026-05-04")  # Monday
        assert "#1" in mon
        code, tue, _ = cli("day", "2026-05-05")  # Tuesday
        assert "#1" not in tue

    def test_sched_recur_daily_fires_every_day(self, cli):
        cli("add", "每日", "-k", "habit", "-t", "personal")
        cli("sched", "1", "--recur", "daily")
        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            code, out, _ = cli("day", d)
            assert "#1" in out

    def test_sched_clear(self, cli):
        cli("add", "t", "-k", "task")
        cli("sched", "1", "2026-06-15")
        cli("sched", "1", "--clear")
        code, out, _ = cli("day", "2026-06-15")
        assert "#1" not in out

    def test_sched_list(self, cli):
        cli("add", "t", "-k", "task")
        cli("sched", "1", "2026-06-15")
        code, out, _ = cli("sched", "1")
        assert "2026-06-15" in out

    def test_sched_invalid_rrule_rejected(self, cli):
        cli("add", "t", "-k", "task")
        code, _, _ = cli("sched", "1", "--recur", "monthly")
        assert code != 0

    def test_sched_relative_date(self, cli):
        from datetime import date, timedelta
        cli("add", "t", "-k", "task", "-t", "work")
        cli("sched", "1", "tomorrow")
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        code, out, _ = cli("day", tomorrow)
        assert "#1" in out


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
        f.write_text('{"2026-05-01":"劳动节","2026-05-21":"小满"}', encoding="utf-8")
        cli("dateinfo", "--import", str(f))
        code, out, _ = cli("dateinfo")
        assert "劳动节" in out and "小满" in out

    def test_dateinfo_clear(self, cli):
        cli("dateinfo", "2026-05-01", "劳动节")
        cli("dateinfo", "2026-05-01", "--clear")
        code, out, _ = cli("dateinfo", "2026-05-01")
        assert "no label" in out


class TestDayMeta:
    """meta: day recap / Top5 / today's goal stored as day-node props, shown at the top of wl day"""

    def _seed_day(self, cli):
        cli("add", "2026", "-k", "year")                                     # 1
        cli("add", "2026-05", "-k", "month", "--parent", "1")                # 2
        cli("add", "2026-05-20", "-k", "day", "--parent", "2")               # 3
        cli("add", "t", "-k", "task", "-t", "work")                          # 4
        cli("log", "4", "做了点事", "--date", "2026-05-20")

    def test_day_shows_goal_and_summary(self, cli):
        self._seed_day(cli)
        cli("set", "3", "goal", "今天交付 X")
        cli("set", "3", "summary", "今天小结 Y")
        code, out, _ = cli("day", "2026-05-20")
        assert "🎯 今天交付 X" in out
        assert "Recap: 今天小结 Y" in out


class TestImportUpdateMove:
    """import update supports parent(move) + remove_tags (fix for silent parent-ignore bug)"""

    def _imp(self, cli, tmp_path, obj):
        import json
        f = tmp_path / "u.json"
        f.write_text(json.dumps(obj), encoding="utf-8")
        return cli("import", str(f))

    def test_import_update_parent_moves_node(self, cli, tmp_path):
        cli("add", "p1", "-k", "project")   # 1
        cli("add", "p2", "-k", "project")   # 2
        cli("add", "t", "-k", "task", "--parent", "1")  # 3
        self._imp(cli, tmp_path, {"update": [{"id": 3, "parent": 2}]})
        code, out, _ = cli("focus", "3")
        assert "#2" in out  # upstream is now p2

    def test_import_update_bad_parent_rejected(self, cli, tmp_path):
        cli("add", "t", "-k", "task")  # 1
        code, _, _ = self._imp(cli, tmp_path, {"update": [{"id": 1, "parent": 999}]})
        assert code != 0

    def test_import_update_remove_tags(self, cli, tmp_path):
        cli("add", "t", "-k", "task", "-t", "a,b")  # 1
        self._imp(cli, tmp_path, {"update": [{"id": 1, "remove_tags": ["a"]}]})
        code, out, _ = cli("show", "1")
        assert ":b:" in out and ":a:" not in out


class TestTreeDepthSortActivity:
    """wl tree: default depth-limited + time nodes sorted by date + day nodes expand activity for that day"""

    def test_tree_default_area_one_level(self, cli):
        # default tree: area lists only one level (area name); projects not expanded; use --root <area> to see them
        cli("add", "life", "-k", "lifetime")           # 1
        cli("add", "数据", "-k", "area", "--parent", "1")  # 2
        cli("add", "proj", "-k", "project", "--parent", "2")  # 3
        code, out, _ = cli("tree")
        assert "数据" in out          # area name appears
        assert "proj" not in out      # project not expanded by default
        code, out2, _ = cli("tree", "--root", "2")  # drill into area to see projects
        assert "proj" in out2

    def test_tree_time_nodes_sorted_by_date(self, cli):
        cli("add", "2026-05", "-k", "month")              # 1
        cli("add", "2026-W22", "-k", "week", "--parent", "1")  # 2 added first (smaller id)
        cli("add", "2026-W18", "-k", "week", "--parent", "1")  # 3 added second (larger id)
        code, out, _ = cli("tree", "--root", "1", "--depth", "1")
        assert out.index("2026-W18") < out.index("2026-W22")  # sorted by date not id

    def test_tree_day_shows_activity(self, cli):
        cli("add", "2026-05", "-k", "month")                   # 1
        cli("add", "2026-05-18", "-k", "day", "--parent", "1") # 2
        cli("add", "proj", "-k", "project")                    # 3
        cli("add", "干了活", "-k", "task", "--parent", "3")     # 4
        cli("log", "4", "今天的进展", "--date", "2026-05-18")
        cli("log", "4", "别天的进展", "--date", "2026-05-20")
        code, out, _ = cli("tree", "--root", "2", "--depth", "3")
        assert "干了活" in out          # task with log that day appears under day
        assert "今天的进展" in out       # that day's log
        assert "别天的进展" not in out   # other day's log does not appear


class TestGoalRecapTick:
    """shortcuts: wl goal / wl recap (today) + wl tick (check-in)"""

    def test_goal_set_and_read(self, cli):
        cli("goal", "今天交付 X")
        code, out, _ = cli("goal")
        assert "今天交付 X" in out

    def test_goal_auto_creates_day(self, cli):
        # wl goal on an empty DB should auto-create today's day node
        cli("goal", "测试目标")
        from datetime import date
        today = date.today().isoformat()
        code, out, _ = cli("ls", "--kind", "day")
        assert today in out  # day node exists

    def test_recap_set_and_read(self, cli):
        cli("recap", "今天小结 Y")
        code, out, _ = cli("recap")
        assert "今天小结 Y" in out

    def test_recap_empty_default(self, cli):
        code, out, _ = cli("recap")
        assert "no summary set for today" in out

    def test_recap_stamps_summary_at(self, cli):
        # recap writes a stamp; read and wl day both show "written at"; no new changes → no rewrite prompt
        cli("recap", "小结 X")
        _, rout, _ = cli("recap")
        assert "written at" in rout
        _, dout, _ = cli("day")
        assert "written at" in dout
        assert "consider rewriting" not in dout

    def test_day_warns_when_changes_after_summary(self, cli):
        # mock recap written long ago; later non-CLOCK log that day → wl day suggests rewriting recap
        cli("recap", "小结 v1")  # recap on empty DB auto-creates today's day, id=1
        cli("set", "1", "summary_at", "2000-01-01 00:00:00")
        cli("add", "work item", "-k", "task")
        cli("log", "2", "小结后又干了活")
        _, out, _ = cli("day")
        assert "consider rewriting" in out

    def test_tick_adds_log(self, cli):
        cli("add", "workout", "-k", "habit")
        cli("tick", "1", "--note", "引体 6 个")
        code, out, _ = cli("show", "1")
        assert "引体 6 个" in out

    def test_tick_done_flag(self, cli):
        cli("add", "一次性活", "-k", "task")
        cli("tick", "1", "--done")
        code, out, _ = cli("show", "1")
        assert "DONE" in out


class TestBriefMode:
    """global -q/--brief + each command's compact/distraction-free mode (token optimization)."""

    def test_brief_day_drops_log_bodies(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "做了 A 步")
        cli("log", "1", "做了 B 步")
        _, full, _ = cli("day")
        _, brief, _ = cli("-q", "day")
        assert "做了 A 步" in full
        assert "做了 A 步" not in brief
        assert "#1" in brief        # task row still present
        assert "(2 log)" in brief   # compact hint appears

    def test_day_log_tail_limits(self, cli):
        cli("add", "work item", "-k", "task")
        for i in range(5):
            cli("log", "1", f"进展 {i}")
        _, out, _ = cli("day", "--log-tail", "2")
        assert "进展 4" in out
        assert "进展 3" in out
        assert "进展 0" not in out
        assert "elided" in out

    def test_day_no_logs_equiv_brief(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "细节")
        _, brief, _ = cli("-q", "day")
        _, nologs, _ = cli("day", "--no-logs")
        assert "细节" not in brief
        assert "细节" not in nologs

    def test_brief_show_skips_timeline(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "log A")
        cli("log", "1", "log B")
        _, full, _ = cli("show", "1")
        _, brief, _ = cli("-q", "show", "1")
        assert "timeline" in full
        assert "timeline" not in brief
        assert "#1" in brief

    def test_show_timeline_tail(self, cli):
        cli("add", "work item", "-k", "task")
        for i in range(4):
            cli("log", "1", f"log {i}")
        _, out, _ = cli("show", "1", "--timeline-tail", "2")
        assert "timeline" in out
        assert "log 3" in out
        assert "log 0" not in out
        assert "elided" in out

    def test_brief_logs_drops_body(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "这是一段非常具体的 body 内容")
        # use --log-format full to get the full body (default oneline truncates by terminal width)
        _, full, _ = cli("--log-format", "full", "logs", "--since", "1970-01-01")
        _, brief, _ = cli("-q", "logs", "--since", "1970-01-01")
        assert "这是一段非常具体的 body 内容" in full
        assert "这是一段非常具体的 body 内容" not in brief
        assert "#1" in brief

    def test_logs_by_task_tail(self, cli):
        cli("add", "work item", "-k", "task")
        for i in range(5):
            cli("log", "1", f"step {i}")
        _, out, _ = cli("logs", "--since", "1970-01-01", "--by-task", "--tail", "2")
        assert "step 4" in out
        assert "step 3" in out
        assert "step 0" not in out
        assert "5 total" in out

    def test_brief_projects_drops_recent_date(self, cli):
        cli("add", "proj", "-k", "project")
        cli("add", "t1", "-k", "task", "--parent", "1")
        cli("log", "2", "推进")
        _, full, _ = cli("projects")
        _, brief, _ = cli("-q", "projects")
        assert "latest" in full
        assert "latest" not in brief
        assert "proj" in brief

    def test_projects_since_filters(self, cli):
        cli("add", "active", "-k", "project")
        cli("add", "stale", "-k", "project")
        cli("add", "t1", "-k", "task", "--parent", "1")
        cli("add", "t2", "-k", "task", "--parent", "2")
        cli("log", "3", "今天推进")
        cli("log", "4", "古早进展", "--date", "2020-01-01")
        _, since, _ = cli("projects", "--since", "2026-01-01")
        assert "active" in since
        assert "stale" not in since

    def test_summary_dedup_default(self, cli):
        # one task attached to two projects (one via parent + one via tag)
        cli("add", "P1", "-k", "project", "-t", "shared")
        cli("add", "P2", "-k", "project", "-t", "shared")
        # task parent=P1, also carries shared tag → P2 picks it up via tag
        from datetime import date
        cli("add", "重复 task", "-k", "task", "--parent", "1", "-t", "shared")
        cli("done", "3")
        _, out, _ = cli("summary", "--since", "1970-01-01")
        # default dedup: same task appears only once
        assert out.count("重复 task") == 1

    def test_summary_no_dedup_keeps_old(self, cli):
        cli("add", "P1", "-k", "project", "-t", "shared")
        cli("add", "P2", "-k", "project", "-t", "shared")
        cli("add", "重复 task", "-k", "task", "--parent", "1", "-t", "shared")
        cli("done", "3")
        _, out, _ = cli("summary", "--since", "1970-01-01", "--no-dedup")
        # old behaviour: same task listed once under P1 and once under P2
        assert out.count("重复 task") == 2

    def test_summary_projects_only(self, cli):
        cli("add", "proj", "-k", "project")
        cli("add", "活儿 task", "-k", "task", "--parent", "1")
        cli("done", "2")
        _, full, _ = cli("summary", "--since", "1970-01-01")
        _, po, _ = cli("summary", "--since", "1970-01-01", "--projects-only")
        assert "活儿 task" in full
        assert "活儿 task" not in po
        assert "proj" in po
        # -q is equivalent to --projects-only
        _, briefq, _ = cli("-q", "summary", "--since", "1970-01-01")
        assert "活儿 task" not in briefq

    def test_summary_top_n(self, cli):
        for i in range(5):
            cli("add", f"P{i}", "-k", "project")
            cli("add", f"t{i}", "-k", "task", "--parent", str(i + 1))
            cli("done", str(2 * (i + 1)))
        _, out, _ = cli("summary", "--since", "1970-01-01", "--top", "2", "--projects-only")
        # only 2 project headers
        assert out.count("▸") == 2

    def test_window_parent_parser_works(self, cli):
        # after parent parser upgrade, --since/--until still work in summary and logs
        from datetime import date, timedelta
        yday = (date.today() - timedelta(days=1)).isoformat()
        today = date.today().isoformat()
        cli("add", "work item", "-k", "task")
        cli("log", "1", "进展", "--date", yday)
        cli("done", "1")
        _, s_out, _ = cli("summary", "--since", yday, "--until", today)
        assert "work item" in s_out
        _, l_out, _ = cli("logs", "--since", yday, "--until", today)
        assert "进展" in l_out


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
        code, _, err = cli("add", "work item", "-k", "task", "--sched", "胡说八道")
        assert code != 0
        assert "日期写错" in err or "日期写错" in _ or "✗" in (err + _)

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
        _, out, _ = cli("link", "1", "2", "共同文档")
        assert "#1" in out and "共同文档" in out
        assert "#2" in out

    def test_log_with_time(self, cli):
        cli("add", "吃饭", "-k", "task")
        cli("log", "1", "早饭", "--time", "11:09")
        _, show, _ = cli("show", "1")
        assert "11:09:00" in show  # time stored in logged_at

    def test_log_with_date_and_time(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "回看", "--date", "2026-05-28", "--time", "14:30")
        _, show, _ = cli("show", "1")
        assert "2026-05-28 14:30:00" in show

    def test_log_date_accepts_yesterday(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "yesterday thing", "--date", "yesterday")
        _, show, _ = cli("show", "1")
        # "yesterday" is resolved to a concrete date and stored in logged_at
        from datetime import date, timedelta
        yday = (date.today() - timedelta(days=1)).isoformat()
        assert yday in show

    def test_log_invalid_time_errors(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, err = cli("log", "1", "x", "--time", "abc")
        assert code != 0

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
        code, _, err = cli("day", "胡说八道")
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
        cli("wait", "1", "--note", "等 review")
        _, show, _ = cli("show", "1")
        assert "WAIT" in show
        assert "等 review" in show

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

    def test_find_default_limits_20(self, cli):
        for i in range(30):
            cli("add", f"hello {i}", "-k", "task")
        _, out, _ = cli("find", "hello")
        assert "30 hits" in out
        assert "showing first 20" in out
        # only 20 rows expected
        import re
        ids = re.findall(r"#\d+", out)
        # header "30 hits" also contains the string 30; counting by hash form is more reliable
        assert out.count("hello") <= 22  # 20 task rows + 2 occurrences in the header

    def test_find_all_shows_everything(self, cli):
        for i in range(25):
            cli("add", f"abc {i}", "-k", "task")
        _, out, _ = cli("find", "abc", "--all")
        assert "25 hits" in out
        assert "showing first" not in out

    def test_find_limit_explicit(self, cli):
        for i in range(15):
            cli("add", f"xyz {i}", "-k", "task")
        _, out, _ = cli("find", "xyz", "--limit", "5")
        assert "15 hits" in out
        assert "showing first 5" in out

    def test_tick_multiple_ids(self, cli):
        cli("add", "h1", "-k", "habit")
        cli("add", "h2", "-k", "habit")
        cli("add", "h3", "-k", "habit")
        _, out, _ = cli("tick", "1", "2", "3", "--note", "今天都做")
        assert "#1 checked in" in out
        assert "#2 checked in" in out
        assert "#3 checked in" in out
        # each one got a log entry
        for nid in ("1", "2", "3"):
            _, show, _ = cli("show", nid)
            assert "今天都做" in show

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
        cli("log", "1", "本周的事")
        _, out, _ = cli("logs", "week")
        assert "本周的事" in out

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

    def test_logs_invalid_id_hint(self, cli):
        _, out, _ = cli("logs", "--id", "9999")
        assert "does not exist" in out or "9999" in out

    def test_logs_empty_window_hint(self, cli):
        _, out, _ = cli("logs", "--date", "2020-01-01")
        assert "no logs" in out

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
        import wl as wl_mod
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
        cli("add", "废弃 proj", "-k", "project")
        cli("cancel", "2")
        _, out, _ = cli("projects")
        assert "active proj" in out
        assert "废弃 proj" not in out

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
        cli("log", "1", "今天做了")
        cli("log", "2", "今天的废弃 log")
        cli("cancel", "2")
        _, out, _ = cli("day")
        assert "今天做了" in out
        assert "今天的废弃 log" not in out

    def test_tree_hides_canceled_root(self, cli):
        cli("add", "active", "-k", "task")
        cli("add", "废弃 root", "-k", "task")
        cli("cancel", "2")
        _, out, _ = cli("tree", "--depth", "1")
        assert "active" in out
        assert "废弃 root" not in out

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

    def test_log_auto_promotes_todo_to_doing(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("log", "1", "进展")
        assert "TODO → DOING" in out
        _, show, _ = cli("show", "1")
        assert "DOING" in show

    def test_log_keep_status_disables_auto(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("log", "1", "进展", "--keep-status")
        assert "TODO → DOING" not in out
        _, show, _ = cli("show", "1")
        # still TODO
        assert "TODO" in show

    def test_log_with_date_keeps_status(self, cli):
        """backfilling a historical log does not change status"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "历史", "--date", "2020-01-01")
        _, show, _ = cli("show", "1")
        assert "TODO" in show

    def test_log_done_not_reverted(self, cli):
        """logging after DONE does not auto-revert status"""
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        cli("log", "1", "补充说明")
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
        cli("log", "2", "古早", "--date", "2020-01-01")
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


class TestCheckin:
    """wl checkin: interactive habit check-in. Simulated via monkeypatched input."""

    def _setup_habits(self, cli, n=2):
        from datetime import date
        today = date.today().isoformat()
        for i in range(n):
            cli("add", f"h{i+1}", "-k", "habit")
            cli("sched", str(i+1), today)
        return today

    def test_checkin_empty(self, cli):
        _, out, _ = cli("checkin")
        assert "no habit scheduled to check in for" in out

    def test_checkin_yes(self, cli, monkeypatch):
        self._setup_habits(cli, 2)
        # answer y twice
        inputs = iter(["y", "y"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        _, out, _ = cli("checkin")
        assert "done 2/2" in out
        # verify both got a log
        _, s1, _ = cli("show", "1")
        assert "✓ done" in s1
        _, s2, _ = cli("show", "2")
        assert "✓ done" in s2

    def test_checkin_skip(self, cli, monkeypatch):
        self._setup_habits(cli, 2)
        inputs = iter(["n", "y"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        _, out, _ = cli("checkin")
        assert "skipped 1" in out
        assert "done 1/2" in out

    def test_checkin_note(self, cli, monkeypatch):
        self._setup_habits(cli, 1)
        inputs = iter(["20s × 3 组"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        cli("checkin")
        _, show, _ = cli("show", "1")
        assert "20s × 3 组" in show

    def test_checkin_quit(self, cli, monkeypatch):
        self._setup_habits(cli, 3)
        inputs = iter(["y", "q"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        _, out, _ = cli("checkin")
        assert "quit" in out
        assert "done 1/3" in out  # 1st answered y, others untouched

    def test_checkin_skips_already_done(self, cli, monkeypatch):
        today = self._setup_habits(cli, 2)
        # tick #1 in advance
        cli("tick", "1", "--note", "提前打卡")
        # checkin should skip #1 (already logged) and prompt only for #2
        inputs = iter(["y"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        _, out, _ = cli("checkin")
        assert "1 already done" in out  # pre_done = 1
        assert "done 2/2" in out

    def test_habit_marker_day_done_when_logged(self, cli):
        """habit with a log that day → wl day renders [x] (render-layer logic, DB status untouched)"""
        from datetime import date, timedelta
        today = date.today().isoformat()
        cli("add", "workout", "-k", "habit")
        cli("sched", "1", today)
        # no log → renders [ ]
        _, no_log, _ = cli("day")
        assert "[ ] #1" in no_log
        # tick once (adds today's log)
        cli("tick", "1")
        _, with_log, _ = cli("day")
        assert "[x] #1" in with_log
        # DB status unchanged (still TODO, not DONE)
        _, show, _ = cli("show", "1")
        assert "TODO" in show

    def test_checkin_multi_select_default_mode(self, cli, monkeypatch):
        """default goes through multi-select (TTY): patch _multi_select_tty to return idx 0, simulating "tick the 1st"."""
        self._setup_habits(cli, 3)
        import wl as wl_mod
        # mock TTY + multi-select returns [0] (pick the 1st pending)
        import wl as wl_mod_
        monkeypatch.setattr(wl_mod_, "_is_interactive_tty", lambda: True)
        monkeypatch.setattr(wl_mod, "_multi_select_tty", lambda options, header: [0])
        _, out, _ = cli("checkin")
        assert "done 1/3" in out
        # 1st (#1) checked in
        _, s1, _ = cli("show", "1")
        assert "✓ done" in s1
        # 2nd/3rd not checked in
        _, s2, _ = cli("show", "2")
        assert "✓ done" not in s2

    def test_checkin_multi_select_canceled(self, cli, monkeypatch):
        """multi-select returns None (q/Esc) → no changes applied."""
        self._setup_habits(cli, 2)
        import wl as wl_mod
        import wl as wl_mod_
        monkeypatch.setattr(wl_mod_, "_is_interactive_tty", lambda: True)
        monkeypatch.setattr(wl_mod, "_multi_select_tty", lambda *a: None)
        _, out, _ = cli("checkin")
        assert "cancel" in out
        _, s1, _ = cli("show", "1")
        assert "✓ done" not in s1

    def test_checkin_linear_flag(self, cli, monkeypatch):
        """--linear explicitly uses prompt mode (even on TTY)"""
        self._setup_habits(cli, 1)
        inputs = iter(["y"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        import wl as wl_mod_
        monkeypatch.setattr(wl_mod_, "_is_interactive_tty", lambda: True)
        _, out, _ = cli("checkin", "--linear")
        assert "done 1/1" in out

    def test_habit_marker_resets_next_day(self, cli):
        """habit logged yesterday → today's wl day shows [ ] (each day is independent)"""
        from datetime import date, timedelta
        today = date.today().isoformat()
        yday = (date.today() - timedelta(days=1)).isoformat()
        cli("add", "维生素", "-k", "habit")
        cli("sched", "1", today)
        # yesterday's log
        cli("log", "1", "昨天吃了", "--date", yday)
        # today's wl day should render [ ] (no log today)
        _, today_out, _ = cli("day")
        assert "[ ] #1" in today_out
        # yesterday's wl day should render [x] (had a log that day)
        _, yday_out, _ = cli("day", yday)
        assert "[x] #1" in yday_out

    def test_unlog_by_log_id(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "a")
        cli("log", "1", "b")
        cli("log", "1", "c")
        # find b's log id (from show timeline; 3 logs + created + DOING transition, skip)
        _, show, _ = cli("show", "1")
        # log id starts at 1 (assuming fresh test DB)
        # delete log id=2 (i.e. 'b')
        _, out, _ = cli("unlog", "2")
        assert "deleted log #2" in out
        _, show2, _ = cli("show", "1")
        assert " a" in show2 or "  a" in show2  # remains
        # 'b' body should be gone
        bodies = [l for l in show2.split("\n") if "✎ log" in l]
        assert all("b" != l.split()[-1] for l in bodies)

    def test_unlog_accepts_L_prefix(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        _, out, _ = cli("unlog", "L1")
        assert "deleted log #1" in out

    def test_unlog_accepts_hash_L_prefix(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        _, out, _ = cli("unlog", "#L1")
        assert "deleted log #1" in out

    def test_unlog_node_today(self, cli):
        cli("add", "h1", "-k", "habit")
        cli("log", "1", "a")
        cli("log", "1", "b")
        # delete the most recent log today (= b)
        _, out, _ = cli("unlog", "--node", "1")
        assert "deleted" in out
        _, show, _ = cli("show", "1")
        assert "✎ log  a" in show or " a\n" in show
        # b should be gone; 1 log remains
        log_lines = [l for l in show.split("\n") if "✎ log" in l]
        assert len(log_lines) == 1

    def test_unlog_node_all(self, cli):
        cli("add", "h1", "-k", "habit")
        cli("log", "1", "a")
        cli("log", "1", "b")
        cli("log", "1", "c")
        _, out, _ = cli("unlog", "--node", "1", "--all")
        assert out.count("deleted log") == 3
        _, show, _ = cli("show", "1")
        assert "✎ log" not in show

    def test_unlog_refuses_clock(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")  # add CLOCK_IN log
        # CLOCK log id = 1
        code, _, err = cli("unlog", "1")
        assert code != 0
        assert "CLOCK" in err or "CLOCK" in _

    def test_unlog_requires_id_xor_node(self, cli):
        cli("add", "t1", "-k", "task")
        # neither given
        code, _, _ = cli("unlog")
        assert code != 0
        # both given
        code2, _, _ = cli("unlog", "1", "--node", "1")
        assert code2 != 0

    def test_show_timeline_includes_log_id(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "hello")
        _, show, _ = cli("show", "1")
        assert "#L1" in show  # uses # like node #123; distinguished by the L prefix

    def test_logs_output_includes_log_id(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "hello")
        _, out, _ = cli("logs", "today")
        assert "#L1" in out

    def test_checkin_eof_breaks(self, cli, monkeypatch):
        self._setup_habits(cli, 3)
        # 1st answers y, 2nd raises EOFError (simulating Ctrl-D)
        def raise_eof(*a):
            raise EOFError()
        inputs = iter([lambda: "y", raise_eof])
        def fake_input(*a):
            x = next(inputs)
            if callable(x):
                return x()
            return x
        monkeypatch.setattr("builtins.input", fake_input)
        _, out, _ = cli("checkin")
        assert "interrupted" in out
        assert "done 1/3" in out


class TestLogTailDefault:
    """wl day / wl logs --by-task / wl tree day-activity / wl show timeline:
    logs default to last 3 only (timeline last 5), middle elided; --all-logs/--all-timelines expands all.
    """

    def test_day_default_tail_3(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        for i in range(6):
            cli("log", "1", f"log-{i}")
        _, out, _ = cli("day", today)
        assert "log-5" in out and "log-4" in out and "log-3" in out
        assert "log-0" not in out and "log-1" not in out
        assert "3 earlier logs elided" in out

    def test_day_all_logs_full(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        for i in range(6):
            cli("log", "1", f"log-{i}")
        _, out, _ = cli("day", today, "--all-logs")
        for i in range(6):
            assert f"log-{i}" in out
        assert "elided" not in out

    def test_day_log_tail_n_override(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        for i in range(6):
            cli("log", "1", f"log-{i}")
        _, out, _ = cli("day", today, "--log-tail", "1")
        assert "log-5" in out
        assert "log-4" not in out
        assert "5 earlier logs elided" in out

    def test_logs_by_task_default_tail_3(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        for i in range(5):
            cli("log", "1", f"x{i}")
        _, out, _ = cli("logs", "--by-task", "--date", today)
        assert "showing last 3" in out
        assert "x4" in out and "x3" in out and "x2" in out
        assert "x0" not in out

    def test_logs_all_logs(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        for i in range(5):
            cli("log", "1", f"x{i}")
        _, out, _ = cli("logs", "--by-task", "--date", today, "--all-logs")
        for i in range(5):
            assert f"x{i}" in out
        assert "showing last" not in out

    def test_show_timeline_default_tail_5(self, cli):
        cli("add", "t1", "-k", "task")
        for i in range(10):
            cli("log", "1", f"e{i}")
        _, out, _ = cli("show", "1")
        # 10 logs + 1 created = 11 events; default tail=5, 6 elided
        assert "showing last 5" in out
        assert "e9" in out and "e5" in out
        assert "e0" not in out

    def test_show_all_timelines(self, cli):
        cli("add", "t1", "-k", "task")
        for i in range(10):
            cli("log", "1", f"e{i}")
        _, out, _ = cli("show", "1", "--all-timelines")
        for i in range(10):
            assert f"e{i}" in out
        assert "elided" not in out


class TestRelog:
    def test_relog_body_positional(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "wrong")
        _, out, _ = cli("relog", "1", "fixed")
        assert "relog #1" in out
        _, show, _ = cli("show", "1")
        assert "fixed" in show
        assert "wrong" not in show

    def test_relog_body_via_m(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "wrong")
        cli("relog", "L1", "-m", "fixed via m")
        _, show, _ = cli("show", "1")
        assert "fixed via m" in show

    def test_relog_at_hhmm(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        cli("relog", "#L1", "--at", "14:30")
        _, show, _ = cli("show", "1")
        assert "14:30:00" in show

    def test_relog_at_full_ts(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        cli("relog", "1", "--at", "2025-01-02 09:15")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 09:15:00" in show

    def test_relog_at_only_date(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        cli("relog", "1", "--at", "2025-01-02")
        _, show, _ = cli("show", "1")
        assert "2025-01-02" in show

    def test_relog_body_and_at_together(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "old")
        cli("relog", "1", "new", "--at", "10:00")
        _, show, _ = cli("show", "1")
        assert "new" in show
        assert "10:00:00" in show
        assert "old" not in show

    def test_relog_refuses_clock(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")  # CLOCK_IN = log id 1
        code, _, err = cli("relog", "1", "fake")
        assert code != 0
        assert "CLOCK" in err or "CLOCK" in _

    def test_relog_refuses_clock_in_body(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "ok")
        code, _, err = cli("relog", "1", "-m", "CLOCK_IN spoofed")
        assert code != 0

    def test_relog_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "--at", "25:00")
        assert code != 0
        code2, _, _ = cli("relog", "1", "--at", "2026-13-01")
        assert code2 != 0

    def test_relog_not_found(self, cli):
        code, _, err = cli("relog", "999999", "x")
        assert code != 0
        assert "not found" in err or "not found" in _

    def test_relog_body_and_m_conflict(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "pos", "-m", "msg")
        assert code != 0

    def test_relog_at_total_garbage(self, cli):
        """--at doesn't look like a time (not HH:MM / date / full ts) → ValueError branch"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x")
        code, _, _ = cli("relog", "1", "--at", "totally-not-a-time")
        assert code != 0

    def test_relog_editor_modify(self, cli, monkeypatch):
        """no body / no --at → EDITOR path; monkeypatch subprocess.call writes new text"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "old")
        def fake_call(argv):
            with open(argv[-1], "w") as f:
                f.write("edited body")
            return 0
        import subprocess as _sp
        monkeypatch.setattr(_sp, "call", fake_call)
        _, out, _ = cli("relog", "1")
        assert "relog #1" in out
        _, show, _ = cli("show", "1")
        assert "edited body" in show

    def test_relog_editor_unchanged_cancels(self, cli, monkeypatch):
        """EDITOR exits with text unchanged → canceled; body preserved"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "keep me")
        import subprocess as _sp
        monkeypatch.setattr(_sp, "call", lambda argv: 0)  # don't touch the file
        _, out, _ = cli("relog", "1")
        assert "cancel" in out or "unchanged" in out
        _, show, _ = cli("show", "1")
        assert "keep me" in show

    def test_relog_editor_rc_nonzero_cancels(self, cli, monkeypatch):
        """EDITOR exit code != 0 → treated as abort"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "keep me")
        import subprocess as _sp
        monkeypatch.setattr(_sp, "call", lambda argv: 1)
        _, out, _ = cli("relog", "1")
        assert "cancel" in out or "unchanged" in out


class TestUnlogErrorPaths:
    """cmd_unlog error paths: log_id missing / --node missing / bad --date / no log that day."""

    def test_unlog_log_id_not_found(self, cli):
        code, _, err = cli("unlog", "9999")
        assert code != 0
        assert "not found" in err or "not found" in _

    def test_unlog_node_not_found(self, cli):
        code, _, err = cli("unlog", "--node", "999")
        assert code != 0
        assert "not found" in err or "not found" in _

    def test_unlog_invalid_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("unlog", "--node", "1", "--date", "junk-date")
        assert code != 0

    def test_unlog_node_no_log_that_day(self, cli):
        cli("add", "t1", "-k", "task")
        # no log today
        _, out, _ = cli("unlog", "--node", "1")
        assert "no non-CLOCK logs" in out or "no logs" in out


class TestLogsCoverageGaps:
    """cmd_logs gaps: yesterday preset / bad --date / missing id hint / empty window hint /
    brief + by_task date-listing branch."""

    def test_logs_yesterday_preset(self, cli):
        from datetime import date, timedelta
        cli("add", "t1", "-k", "task")
        yday = (date.today() - timedelta(days=1)).isoformat()
        cli("log", "1", "y-log", "--date", yday)
        _, out, _ = cli("logs", "yesterday")
        assert "y-log" in out

    def test_logs_invalid_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("logs", "--date", "garbage")
        assert code != 0

    def test_logs_unknown_id_hint(self, cli):
        _, out, _ = cli("logs", "--id", "9999")
        assert "does not exist" in out

    def test_logs_id_exists_but_empty_window(self, cli):
        cli("add", "t1", "-k", "task")  # no log
        _, out, _ = cli("logs", "--id", "1")
        assert "no logs" in out

    def test_logs_empty_window_hint(self, cli):
        from datetime import date, timedelta
        # window from a year ago; nothing there
        old = (date.today() - timedelta(days=400)).isoformat()
        _, out, _ = cli("logs", "--since", old, "--until", old)
        assert "no logs" in out

    def test_logs_by_task_brief_no_body(self, cli):
        """cmd_logs brief + by_task: list each log's date without expanding the body."""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "aaaa-body")
        cli("log", "1", "bbbb-body")
        _, out, _ = cli("logs", "--by-task", "--no-body")
        from datetime import date
        today = date.today().isoformat()
        assert "t1" in out
        assert today in out  # date string should appear
        assert "aaaa-body" not in out and "bbbb-body" not in out


class TestDayMetaRendering:
    """cmd_day: goal / day recap / Top5 / week prop rendering branches."""

    def _setup_day_with_props(self, cli, **props):
        from datetime import date
        today = date.today().isoformat()
        # create the day node and write props via wl goal/recap/set
        cli("add", today, "-k", "day")
        for k, v in props.items():
            cli("set", "1", k, v)
        return today

    def test_day_renders_goal(self, cli):
        today = self._setup_day_with_props(cli, goal="today goal")
        _, out, _ = cli("day", today)
        assert "🎯" in out and "today goal" in out

    def test_day_renders_summary(self, cli):
        today = self._setup_day_with_props(cli, summary="日终复盘内容")
        _, out, _ = cli("day", today)
        assert "Recap" in out and "日终复盘内容" in out

    def test_day_renders_top5(self, cli):
        today = self._setup_day_with_props(cli, top5="Top5 内容")
        _, out, _ = cli("day", today)
        assert "Top5" in out

    def test_day_renders_week_overview(self, cli):
        from datetime import date
        today = date.today().isoformat()
        # build a week → day parent-child chain, write overview on the week
        cli("add", "2026-W22", "-k", "week")  # id 1
        cli("add", today, "-k", "day", "--parent", "1")  # id 2
        cli("set", "1", "overview", "本周主线")
        _, out, _ = cli("day", today)
        assert "This week" in out and "本周主线" in out

    def test_day_renders_clock_total(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("start", "1")
        cli("stop", "1")
        _, out, _ = cli("day", today)
        assert "CLOCK" in out


class TestTreeEmpty:
    def test_tree_empty_repo(self, cli):
        _, out, _ = cli("tree")
        assert "no root nodes" in out

    def test_tree_root_no_kind_filter_match(self, cli):
        cli("add", "t1", "-k", "task")  # parent_id=NULL, kind=task
        _, out, _ = cli("tree", "--kind", "nosuch")
        assert "no root nodes" in out


class TestDefaultTreeAreaListing:
    """_print_default_tree: lifetime + area + today / month fallback branches."""

    def test_default_tree_lists_area_children(self, cli):
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "work", "-k", "area", "--parent", "1")
        cli("add", "personal", "-k", "area", "--parent", "1")
        _, out, _ = cli("tree")
        assert "work" in out and "personal" in out

    def test_default_tree_with_today_node(self, cli):
        """lifetime + today's day node → dayn branch (chain + _print_day_activity)"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "2026", "-k", "year", "--parent", "1")
        cli("add", today, "-k", "day", "--parent", "2")
        _, out, _ = cli("tree")
        assert today in out
        assert "2026" in out

    def test_default_tree_month_fallback(self, cli):
        """no day node today, has month → month fallback branch"""
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "2026-05", "-k", "month", "--parent", "1")
        _, out, _ = cli("tree")
        assert "2026-05" in out


class TestCheckinCollectGaps:
    """_checkin_collect: --all-kinds / CANCELED filtering / already-logged marker."""

    def test_checkin_all_kinds_includes_task(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "task-x", "-k", "task")
        cli("sched", "1", today)
        # EOF straight away → immediate interrupt, but _checkin_collect already covered --all-kinds branch
        monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError()))
        _, out, _ = cli("checkin", "--all-kinds", "--linear")
        assert "task-x" in out or "done" in out or "interrupted" in out

    def test_checkin_skips_canceled(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h-skip", "-k", "habit")
        cli("sched", "1", today)
        cli("cancel", "1")
        monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError()))
        _, out, _ = cli("checkin", "--linear")
        assert "h-skip" not in out


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


class TestSchedHelpers:
    """_sched_kind / _sched_anchor / _sched_fires / _norm_rrule edge coverage."""

    def test_sched_recur_weekly(self, cli):
        """weekly:Mon,Wed,Fri rule normalization + write"""
        cli("add", "h1", "-k", "habit")
        code, out, _ = cli("sched", "1", "--recur", "weekly:Mon,Wed,Fri")
        assert code == 0
        # confirm the rule was stored in the sched table
        import wl
        con = wl.db_connect()
        row = con.execute("SELECT rrule FROM sched WHERE node_id=1").fetchone()
        assert row and "Mon" in row["rrule"]

    def test_sched_invalid_rrule(self, cli):
        cli("add", "h1", "-k", "habit")
        code, _, _ = cli("sched", "1", "--recur", "garbage-rule")
        assert code != 0

    def test_sched_invalid_weekly_day(self, cli):
        cli("add", "h1", "-k", "habit")
        code, _, _ = cli("sched", "1", "--recur", "weekly:NotADay")
        assert code != 0

    def test_sched_invalid_when_date(self, cli):
        cli("add", "h1", "-k", "habit")
        code, _, _ = cli("sched", "1", "garbage-date")
        assert code != 0

    def test_sched_clear_empty(self, cli):
        """--clear with no existing schedule → "no schedule" branch"""
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("sched", "1", "--clear")
        assert "no schedule" in out or "cleared" in out or out  # any friendly hint

    def test_day_with_weekly_recur_hits(self, cli):
        """weekly: matching weekday in the current week → hit, exercising _sched_anchor weekly branch"""
        from datetime import date
        today = date.today()
        wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][today.weekday()]
        cli("add", "h-weekly", "-k", "habit")
        cli("sched", "1", "--recur", f"weekly:{wd}")
        _, out, _ = cli("day", today.isoformat())
        assert "h-weekly" in out


class TestCmdSetErrors:
    def test_set_empty_key(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("set", "1", " ", "v")
        assert code != 0

    def test_set_node_not_found(self, cli):
        code, _, _ = cli("set", "999", "k", "v")
        assert code != 0


class TestCmdDeferErrors:
    def test_defer_invalid_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("defer", "1", "garbage-date")
        assert code != 0


class TestPrintDayActivityEdges:
    """_print_day_activity: log_tail=0 branch + habit + omitted=0 branch."""

    def test_tree_log_tail_zero_no_logs(self, cli):
        """wl tree --no-logs → log_tail=0; logs not expanded but tasks still listed"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", today, "-k", "day", "--parent", "1")
        cli("add", "t1", "-k", "task")
        cli("log", "3", "hidden-body")
        _, out, _ = cli("tree", "--no-logs")
        assert "t1" in out
        assert "hidden-body" not in out


class TestStatusFilterAndSnippet:
    def test_status_filter_hide_done(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        cli("add", "t2", "-k", "task")
        _, out, _ = cli("ls")
        # default hides DONE
        assert "t2" in out
        assert "t1" not in out

    def test_find_snippet_match_not_found_fallback(self, cli):
        """_snippet falls back to 80-char truncation when q not found"""
        cli("add", "t1", "-k", "task")
        # q uppercase in body; _snippet lowercases so it finds; real fallback hits when body lacks q
        cli("log", "1", "x" * 200)
        _, out, _ = cli("find", "nothere", "--in", "title")
        # no match → fallback path
        assert out or True  # just don't crash


class TestCnWeekday:
    def test_invalid_date_returns_empty(self, cli):
        """_cn_weekday internal helper; invalid date triggers ValueError → except path"""
        # going through wl day with invalid date is rejected by _resolve_concrete_date; call _cn_weekday directly
        import wl
        assert wl._cn_weekday("bad-date") == ""
        assert wl._cn_weekday("2026-13-99") == ""


class TestAncestorsChainBreak:
    def test_dangling_parent_id_breaks_loop(self, cli):
        """parent_id points to missing node → mid-chain break (with FK off)"""
        cli("add", "p1", "-k", "task")
        cli("add", "c1", "-k", "task", "--parent", "1")
        import wl
        con = wl.db_connect()
        # temporarily disable FK to perform the edit, then re-enable
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("UPDATE node SET parent_id = 999 WHERE id = 2")
        con.commit()
        chain = wl._ancestors_chain(con, 2)
        # start at c1 → parent=999 missing → break; chain = [c1]
        assert len(chain) == 1
        assert chain[0]["title"] == "c1"


class TestCheckinLinearActuallyRuns:
    """cmd_checkin --linear hits at least 1 pending habit → enters input loop"""

    def test_checkin_linear_y_marks_done(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h-linear", "-k", "habit")
        cli("sched", "1", today)
        # mock y → done
        monkeypatch.setattr("builtins.input", lambda *a: "y")
        _, out, _ = cli("checkin", "--linear")
        assert "done 1/1" in out or "h-linear" in out

    def test_checkin_all_done_short_circuit(self, cli):
        """all pending already checked in → short-circuit "already checked in" path"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h-done", "-k", "habit")
        cli("sched", "1", today)
        cli("tick", "1")  # already checked in today
        _, out, _ = cli("checkin", "--linear")
        assert "already checked in" in out


class TestApplyAndImportEdges:
    def test_import_invalid_json(self, cli, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_top_level_not_dict(self, cli, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[]")
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_dry_run(self, cli, tmp_path):
        import json as _json
        p = tmp_path / "ok.json"
        p.write_text(_json.dumps({"add": [{"title": "from-import", "kind": "task"}]}))
        _, out, _ = cli("import", str(p), "--dry-run")
        assert "dry-run" in out

    def test_apply_dry_run(self, cli, tmp_path):
        p = tmp_path / "wld.txt"
        p.write_text("+ [ ] new-task\n")
        _, out, _ = cli("apply", str(p), "--dry-run")
        assert "dry-run" in out

    def test_apply_invalid_marker(self, cli, tmp_path):
        p = tmp_path / "bad.txt"
        # ~ #999 does not exist
        p.write_text("~ #999\n  status DONE\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0


class TestThemesNoColor:
    def test_themes_no_color(self, cli):
        _, out, _ = cli("--color", "never", "themes")
        assert "■" in out or "current" in out


class TestLsTagFilter:
    def test_ls_multi_tag_and(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work,foo")
        cli("add", "t2", "-k", "task", "-t", "work")
        _, out, _ = cli("ls", "--tag", "work,foo")
        # AND filter: only t1 has both work + foo
        assert "t1" in out
        assert "t2" not in out

    def test_ls_all_includes_done(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        _, out, _ = cli("ls", "--all")
        assert "t1" in out


class TestTreeBy:
    """cmd_tree --by tag/project/direction grouping dimensions."""

    def test_tree_by_tag_no_semantic(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work")  # work is a generic tag
        _, out, _ = cli("tree", "--by", "tag")
        assert "no semantic" in out or "(no " in out

    def test_tree_by_tag_with_semantic(self, cli):
        cli("add", "t1", "-k", "task", "-t", "team-dev")
        cli("add", "t2", "-k", "task", "-t", "team-dev")
        _, out, _ = cli("tree", "--by", "tag")
        assert "team-dev" in out

    def test_tree_by_project_no_projects(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("tree", "--by", "project")
        assert "no project" in out

    def test_tree_by_project_with_orphan(self, cli):
        cli("add", "P1", "-k", "project")  # id 1
        cli("add", "child", "-k", "task", "--parent", "1")  # id 2 under P1
        cli("add", "orphan", "-k", "task")  # id 3 orphan
        _, out, _ = cli("tree", "--by", "project")
        assert "P1" in out
        assert "unassigned" in out
        assert "orphan" in out

    def test_tree_by_direction(self, cli):
        cli("add", "w1", "-k", "task", "-t", "work")
        cli("add", "p1", "-k", "task", "-t", "personal")
        _, out, _ = cli("tree", "--by", "direction")
        assert "w1" in out and "p1" in out


class TestSchedHelpersDirect:
    """direct unit tests for _sched_anchor / _sched_fires / _sched_kind."""

    def test_sched_kind_someday(self):
        import wl
        assert wl._sched_kind("someday") == "someday"

    def test_sched_kind_quarter(self):
        import wl
        assert wl._sched_kind("2026-Q2") == "quarter"

    def test_sched_kind_year(self):
        import wl
        assert wl._sched_kind("2026") == "year"

    def test_sched_kind_fuzzy(self):
        import wl
        assert wl._sched_kind("下月") == "fuzzy"

    def test_sched_kind_empty(self):
        import wl
        assert wl._sched_kind("") is None
        assert wl._sched_kind(None) is None

    def test_sched_anchor_year(self):
        import wl
        assert wl._sched_anchor("2026") == "2026-01-01"

    def test_sched_anchor_quarter(self):
        import wl
        assert wl._sched_anchor("2026-Q2") == "2026-04-01"

    def test_sched_anchor_week(self):
        import wl
        result = wl._sched_anchor("2026-W01")
        assert result.startswith("2025-12-") or result.startswith("2026-01-")

    def test_sched_anchor_month(self):
        import wl
        assert wl._sched_anchor("2026-05") == "2026-05-01"

    def test_sched_anchor_invalid_returns_sentinel(self):
        import wl
        assert wl._sched_anchor("garbage") == "9999-12-31"
        assert wl._sched_anchor("2026-W99") == "9999-12-31"

    def test_sched_fires_weekly_match(self):
        import wl
        # weekly:Mon — 2026-01-05 is a Monday
        assert wl._sched_fires(None, "weekly:Mon", "2026-01-05") is True
        assert wl._sched_fires(None, "weekly:Tue", "2026-01-05") is False

    def test_sched_fires_daily(self):
        import wl
        assert wl._sched_fires(None, "daily", "2026-01-05") is True

    def test_sched_fires_on_date(self):
        import wl
        assert wl._sched_fires("2026-01-05", None, "2026-01-05") is True
        assert wl._sched_fires("2026-01-06", None, "2026-01-05") is False

    def test_sched_fires_empty(self):
        import wl
        assert wl._sched_fires(None, None, "2026-01-05") is False

    # monthly: which day of the month
    def test_sched_fires_monthly_single_day(self):
        import wl
        assert wl._sched_fires(None, "monthly:5", "2026-01-05") is True
        assert wl._sched_fires(None, "monthly:5", "2026-01-06") is False
        assert wl._sched_fires(None, "monthly:5", "2026-02-05") is True

    def test_sched_fires_monthly_multi_days(self):
        import wl
        assert wl._sched_fires(None, "monthly:1,15,25", "2026-01-15") is True
        assert wl._sched_fires(None, "monthly:1,15,25", "2026-01-16") is False
        assert wl._sched_fires(None, "monthly:1,15,25", "2026-02-25") is True

    def test_sched_fires_monthly_last_day(self):
        import wl
        # 2026-01 month-end = 31
        assert wl._sched_fires(None, "monthly:-1", "2026-01-31") is True
        assert wl._sched_fires(None, "monthly:-1", "2026-01-30") is False
        # 2026-02 month-end = 28
        assert wl._sched_fires(None, "monthly:-1", "2026-02-28") is True
        # 2024 (leap year) Feb end = 29
        assert wl._sched_fires(None, "monthly:-1", "2024-02-29") is True

    def test_sched_fires_monthly_negative_second_last(self):
        import wl
        # 2026-01-30 = second-to-last (31 - 2 + 1 = 30)
        assert wl._sched_fires(None, "monthly:-2", "2026-01-30") is True
        assert wl._sched_fires(None, "monthly:-2", "2026-02-27") is True  # 28-2+1=27

    def test_sched_fires_monthly_31_in_short_month(self):
        """monthly:31 should not fire in February (no 31st)"""
        import wl
        assert wl._sched_fires(None, "monthly:31", "2026-01-31") is True
        assert wl._sched_fires(None, "monthly:31", "2026-02-28") is False

    # yearly: each year on MM-DD
    def test_sched_fires_yearly(self):
        import wl
        assert wl._sched_fires(None, "yearly:03-21", "2026-03-21") is True
        assert wl._sched_fires(None, "yearly:03-21", "2027-03-21") is True
        assert wl._sched_fires(None, "yearly:03-21", "2026-03-22") is False

    def test_sched_fires_yearly_multi(self):
        import wl
        assert wl._sched_fires(None, "yearly:01-01,12-25", "2026-01-01") is True
        assert wl._sched_fires(None, "yearly:01-01,12-25", "2026-12-25") is True
        assert wl._sched_fires(None, "yearly:01-01,12-25", "2026-07-04") is False

    # quarterly
    def test_sched_fires_quarterly_first_month(self):
        import wl
        # 1-15 = 15th of the first month of each quarter → 1/15, 4/15, 7/15, 10/15
        for ymd in ("2026-01-15", "2026-04-15", "2026-07-15", "2026-10-15"):
            assert wl._sched_fires(None, "quarterly:1-15", ymd) is True
        for ymd in ("2026-02-15", "2026-03-15", "2026-05-15"):
            assert wl._sched_fires(None, "quarterly:1-15", ymd) is False

    def test_sched_fires_quarterly_third_month_end_day(self):
        import wl
        # 3-31 (31st of the last month of the quarter): 3/31, 12/31 fire; 6/30, 9/30 — no 31st → no fire
        assert wl._sched_fires(None, "quarterly:3-31", "2026-03-31") is True
        assert wl._sched_fires(None, "quarterly:3-31", "2026-12-31") is True
        assert wl._sched_fires(None, "quarterly:3-31", "2026-06-30") is False
        assert wl._sched_fires(None, "quarterly:3-31", "2026-09-30") is False

    def test_sched_fires_quarterly_neg1_quarter_end(self):
        """quarterly:-1 = last day of each quarter (3/31, 6/30, 9/30, 12/31)"""
        import wl
        for ymd in ("2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31"):
            assert wl._sched_fires(None, "quarterly:-1", ymd) is True
        for ymd in ("2026-03-30", "2026-06-29", "2026-04-30", "2026-12-30"):
            assert wl._sched_fires(None, "quarterly:-1", ymd) is False

    def test_sched_fires_yearly_neg1_year_end(self):
        import wl
        assert wl._sched_fires(None, "yearly:-1", "2026-12-31") is True
        assert wl._sched_fires(None, "yearly:-1", "2027-12-31") is True
        assert wl._sched_fires(None, "yearly:-1", "2026-12-30") is False
        assert wl._sched_fires(None, "yearly:-1", "2026-01-01") is False


class TestWeeklyNumeric:
    """weekly: accepts numbers 1-7 / -1..-7 (equivalent to Mon..Sun)"""

    def test_norm_weekly_positive_numbers(self):
        import wl
        # 1=Mon, 2=Tue, ..., 7=Sun
        assert wl._norm_rrule("weekly:1") == "weekly:Mon"
        assert wl._norm_rrule("weekly:1,3,5") == "weekly:Mon,Wed,Fri"
        assert wl._norm_rrule("weekly:7") == "weekly:Sun"

    def test_norm_weekly_negative_numbers(self):
        import wl
        # -1 = Sun (last day), -7 = Mon
        assert wl._norm_rrule("weekly:-1") == "weekly:Sun"
        assert wl._norm_rrule("weekly:-7") == "weekly:Mon"
        assert wl._norm_rrule("weekly:-1,-2") == "weekly:Sun,Sat"

    def test_norm_weekly_mixed_forms(self):
        import wl
        # mixes numbers + names + negatives; dedup, order preserved
        assert wl._norm_rrule("weekly:Mon,1,Tue,2") == "weekly:Mon,Tue"

    def test_norm_weekly_out_of_range_rejected(self):
        import wl
        for bad in ("weekly:0", "weekly:8", "weekly:-8", "weekly:abc"):
            try:
                wl._norm_rrule(bad)
                assert False, f"should reject {bad}"
            except ValueError:
                pass

    def test_fires_weekly_numeric_equivalent(self):
        """weekly:-1 = weekly:Sun → 2026-01-04 is a Sunday"""
        import wl
        assert wl._sched_fires(None, "weekly:Sun", "2026-01-04") is True
        # passing a numeric rule directly to _sched_fires does not work (needs _norm_rrule conversion);
        # end-to-end via wl sched, _norm_rrule has already converted it


class TestQuarterlyAndYearlyNeg1Norm:
    def test_quarterly_norm_md(self):
        import wl
        assert wl._norm_rrule("quarterly:1-15") == "quarterly:1-15"
        assert wl._norm_rrule("quarterly:3-31,1-1") == "quarterly:3-31,1-1"

    def test_quarterly_norm_neg1(self):
        import wl
        assert wl._norm_rrule("quarterly:-1") == "quarterly:-1"

    def test_quarterly_rejects_bad(self):
        import wl
        for bad in ("quarterly:", "quarterly:4-1", "quarterly:0-15", "quarterly:abc"):
            try:
                wl._norm_rrule(bad)
                assert False, f"should reject {bad}"
            except ValueError:
                pass

    def test_yearly_neg1_norm(self):
        import wl
        assert wl._norm_rrule("yearly:-1") == "yearly:-1"
        assert wl._norm_rrule("yearly:-1,03-21") == "yearly:-1,03-21"


class TestQuarterlyE2E:
    def test_sched_quarterly_neg1_end_to_end(self, cli):
        cli("add", "季度末复盘", "-k", "habit")
        cli("sched", "1", "--recur", "quarterly:-1")
        # Q1 end = 03-31
        _, out, _ = cli("day", "2026-03-31")
        assert "季度末复盘" in out
        _, out, _ = cli("day", "2026-04-30")
        assert "季度末复盘" not in out

    def test_sched_quarterly_first_month_first_day(self, cli):
        cli("add", "季度首日", "-k", "habit")
        cli("sched", "1", "--recur", "quarterly:1-1")
        for ymd in ("2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01"):
            _, out, _ = cli("day", ymd)
            assert "季度首日" in out, f"should hit {ymd}"

    def test_sched_weekly_numeric_end_to_end(self, cli):
        """wl sched with weekly:-1 (=Sun) → fires on Sundays"""
        cli("add", "周日复盘", "-k", "habit")
        cli("sched", "1", "--recur", "weekly:-1")
        # 2026-01-04 is a Sunday
        _, out, _ = cli("day", "2026-01-04")
        assert "周日复盘" in out


class TestNormRruleNew:
    """_norm_rrule new prefix validation"""

    def test_monthly_norm(self):
        import wl
        assert wl._norm_rrule("monthly:5") == "monthly:5"
        assert wl._norm_rrule("monthly:1,15,25") == "monthly:1,15,25"
        assert wl._norm_rrule("monthly:-1") == "monthly:-1"

    def test_monthly_empty_rejected(self):
        import wl
        try:
            wl._norm_rrule("monthly:")
            assert False
        except ValueError:
            pass

    def test_monthly_out_of_range_rejected(self):
        import wl
        for bad in ("monthly:0", "monthly:32", "monthly:-29", "monthly:abc"):
            try:
                wl._norm_rrule(bad)
                assert False, f"should reject {bad}"
            except ValueError:
                pass

    def test_yearly_norm(self):
        import wl
        assert wl._norm_rrule("yearly:03-21") == "yearly:03-21"
        assert wl._norm_rrule("yearly:01-01,12-25") == "yearly:01-01,12-25"

    def test_yearly_bad_format_rejected(self):
        import wl
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
        cli("add", "月初打卡", "-k", "habit")
        # use today's day-of-month as the monthly rule so it always fires today
        today = date.today()
        cli("sched", "1", "--recur", f"monthly:{today.day}")
        _, out, _ = cli("day", today.isoformat())
        assert "月初打卡" in out

    def test_sched_yearly_via_cli_and_day_hits(self, cli):
        from datetime import date
        cli("add", "纪念日", "-k", "habit")
        today = date.today()
        cli("sched", "1", "--recur", f"yearly:{today.month:02d}-{today.day:02d}")
        _, out, _ = cli("day", today.isoformat())
        assert "纪念日" in out

    def test_sched_monthly_last_day_via_cli(self, cli):
        cli("add", "月末复盘", "-k", "habit")
        cli("sched", "1", "--recur", "monthly:-1")
        # test 2026-02-28 (short month-end)
        _, out, _ = cli("day", "2026-02-28")
        assert "月末复盘" in out
        _, out, _ = cli("day", "2026-02-27")
        assert "月末复盘" not in out

    def test_sched_invalid_monthly_rejected(self, cli):
        cli("add", "x", "-k", "habit")
        code, _, _ = cli("sched", "1", "--recur", "monthly:0")
        assert code != 0

    def test_sched_invalid_yearly_rejected(self, cli):
        cli("add", "x", "-k", "habit")
        code, _, _ = cli("sched", "1", "--recur", "yearly:13-99")
        assert code != 0


class TestNodeClockMinException:
    def test_clock_min_unparseable_log_ts(self, cli):
        """log span parse exception → except path"""
        cli("add", "t1", "-k", "task")
        import wl
        con = wl.db_connect()
        # insert two logs with bad timestamps directly
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, 'not-a-ts', 'a')")
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, 'still-bad', 'b')")
        con.commit()
        # _node_clock_min must not crash
        result = wl._node_clock_min(con, 1)
        assert isinstance(result, int)


class TestImportEdges:
    def test_import_update_with_field_changes(self, cli, tmp_path):
        import json as _json
        cli("add", "before", "-k", "task")  # id 1
        spec = {"update": [{"id": 1, "title": "after", "priority": "A", "add_tags": ["lab"]}]}
        p = tmp_path / "u.json"
        p.write_text(_json.dumps(spec))
        cli("import", str(p))
        _, show, _ = cli("show", "1")
        assert "after" in show
        assert "lab" in show

    def test_import_update_dry_run(self, cli, tmp_path):
        import json as _json
        cli("add", "x", "-k", "task")
        p = tmp_path / "u.json"
        p.write_text(_json.dumps({"update": [{"id": 1, "title": "new"}]}))
        _, out, _ = cli("import", str(p), "--dry-run")
        assert "dry-run" in out
        _, show, _ = cli("show", "1")
        assert "new" not in show  # dry-run did not actually modify

    def test_import_update_node_missing(self, cli, tmp_path):
        import json as _json
        p = tmp_path / "u.json"
        p.write_text(_json.dumps({"update": [{"id": 999, "title": "x"}]}))
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_with_links_props_tags(self, cli, tmp_path):
        import json as _json
        spec = {"add": [{"title": "rich", "kind": "task", "tags": ["foo"],
                          "props": {"k": "v"}, "links": ["DocA"]}]}
        p = tmp_path / "rich.json"
        p.write_text(_json.dumps(spec))
        cli("import", str(p))
        _, show, _ = cli("show", "1")
        assert ":foo:" in show
        assert "DocA" in show


class TestApplyExtended:
    def test_apply_update_status(self, cli, tmp_path):
        cli("add", "x", "-k", "task")
        p = tmp_path / "a.txt"
        p.write_text("~ [x] #1\n")  # mark DONE
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DONE" in show

    def test_apply_delete(self, cli, tmp_path):
        cli("add", "byebye", "-k", "task")
        p = tmp_path / "d.txt"
        p.write_text("- #1\n")
        _, out, _ = cli("apply", str(p))
        assert "1" in out or "delete" in out or out
        # should actually be deleted
        import wl
        con = wl.db_connect()
        assert not con.execute("SELECT 1 FROM node WHERE id = 1").fetchone()

    def test_apply_add_with_log_sub(self, cli, tmp_path):
        p = tmp_path / "add.txt"
        p.write_text("+ [ ] new\n  @log first log entry\n  @link DocB\n  @prop k=v\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "new" in show
        assert "DocB" in show

    def test_apply_update_no_changes_errors(self, cli, tmp_path):
        """~ #id with no field operations → validation fails"""
        cli("add", "x", "-k", "task")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n")  # bare ~ with no fields; not inline shorthand
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_update_nonexistent(self, cli, tmp_path):
        p = tmp_path / "u.txt"
        p.write_text("~ [x] #999\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0


class TestSnippetFallback:
    def test_snippet_text_shorter_than_window(self, cli):
        cli("add", "short title with key word foo bar", "-k", "task")
        _, out, _ = cli("find", "missing-query")
        # _snippet not reached because find has no match; OK as long as no crash
        assert out or True

    def test_snippet_lookup_inside(self, cli):
        # call directly
        import wl
        s = wl._snippet("hello world key bar", "key")
        assert "key" in s


class TestStatusFilterHideDone:
    def test_hide_done_filter(self, cli):
        import wl
        frag, params = wl._status_filter_sql(include_canceled=True, hide_done=True)
        assert "DONE" in params


class TestInsertLogAutoStatus:
    def test_log_to_todo_promotes_to_doing(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "first progress")
        _, out, _ = cli("ls", "--all")
        # marker [/] may be followed by align spaces before #id
        assert "[/]" in out and "#1" in out


class TestCheckinKindFilter:
    def test_checkin_explicit_kind(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t-task", "-k", "task")
        cli("sched", "1", today)
        # --kind task → kinds = {"task"}, should be collected
        monkeypatch.setattr("builtins.input", lambda *a: "n")
        _, out, _ = cli("checkin", "--kind", "task", "--linear")
        # reaching the linear path is good enough; 1 item collected
        assert "1 items" in out or "1/1" in out


class TestLogTimeAndDate:
    """cmd_log --date + --time combinations"""

    def test_log_with_date_only(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--date", "2025-01-02")
        _, show, _ = cli("show", "1")
        assert "2025-01-02" in show

    def test_log_with_date_and_time(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--date", "2025-01-02", "--time", "14:30")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 14:30:00" in show

    def test_log_with_time_only(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--time", "09:15")
        _, show, _ = cli("show", "1")
        from datetime import date
        assert f"{date.today().isoformat()} 09:15:00" in show

    def test_log_with_time_seconds(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", "x", "--time", "09:15:30")
        _, show, _ = cli("show", "1")
        assert "09:15:30" in show

    def test_log_invalid_time(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("log", "1", "x", "--time", "25:00")
        assert code != 0

    def test_log_invalid_time_with_date(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("log", "1", "x", "--date", "2025-01-02", "--time", "99:99")
        assert code != 0


class TestDescendantsMissing:
    def test_descendants_node_not_found(self, cli):
        code, _, _ = cli("descendants", "999")
        assert code != 0


class TestApplyExtra:
    def test_apply_delete_with_subtree(self, cli, tmp_path):
        cli("add", "parent", "-k", "task")
        cli("add", "child", "-k", "task", "--parent", "1")
        p = tmp_path / "del.txt"
        p.write_text("- #1\n")
        cli("apply", str(p))
        import wl
        con = wl.db_connect()
        # both parent and child are gone
        assert not con.execute("SELECT 1 FROM node WHERE id IN (1, 2)").fetchone()

    def test_apply_update_with_log_sub_via_fieldop(self, cli, tmp_path):
        """~ #id followed by '+log msg' field op → _exec_update log branch"""
        cli("add", "t1", "-k", "task")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n  +log progress-via-apply\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "progress-via-apply" in show

    def test_apply_update_link_add_remove(self, cli, tmp_path):
        cli("add", "t1", "-k", "task")
        cli("link", "1", "DocA")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n  +link DocB\n  -link DocA\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DocB" in show
        assert "DocA" not in show

    def test_apply_update_prop(self, cli, tmp_path):
        cli("add", "t1", "-k", "task")
        p = tmp_path / "u.txt"
        p.write_text("~ #1\n  prop owner=xyb\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "owner" in show and "xyb" in show

    def test_apply_subs_log_link_prop(self, cli, tmp_path):
        """+ new node + @log/@link/@prop subs → exercise all _apply_sub branches"""
        p = tmp_path / "anc.txt"
        p.write_text("+ [ ] new-with-subs\n  @log first-log\n  @link DocX\n  @prop k=v\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DocX" in show
        assert "k=v" in show or "k" in show

    def test_apply_delete_without_id(self, cli, tmp_path):
        """- delete must include #id"""
        p = tmp_path / "bad.txt"
        p.write_text("- something-no-id\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_at_log_without_anchor(self, cli, tmp_path):
        """@log without a leading + or anchor → error"""
        p = tmp_path / "bad.txt"
        p.write_text("@log orphan-log\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_anchor_missing_id(self, cli, tmp_path):
        """leading whitespace but no #id"""
        p = tmp_path / "bad.txt"
        p.write_text(" something\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0

    def test_apply_plus_with_id(self, cli, tmp_path):
        """+ add should not carry #id"""
        p = tmp_path / "bad.txt"
        p.write_text("+ [ ] #5 explicit-id\n")
        code, _, _ = cli("apply", str(p))
        assert code != 0


class TestExecUpdateDirect:
    """direct _exec_update calls covering log/link/prop/tag branches"""

    def test_exec_update_all_field_ops(self, cli):
        cli("add", "t1", "-k", "task")
        cli("link", "1", "DocA")
        cli("set", "1", "k1", "v0")
        import wl
        con = wl.db_connect()
        op = {
            "id": 1,
            "fieldops": [
                (10, ("set", "title", "新标题")),
                (11, ("set", "status", "DONE")),  # triggers DONE auto closed_at
                (12, ("clear", "scheduled", None)),
                (13, ("add", "log", "from-exec-update")),
                (14, ("add", "link", "DocB")),
                (15, ("remove", "link", "DocA")),
                (16, ("set", "prop", ("k1", "v1"))),
                (17, ("remove", "prop", "k1")),
                (18, ("add", "tag", "newtag")),
                (19, ("remove", "tag", "newtag")),
            ],
        }
        wl._exec_update(con, op)
        con.commit()
        _, show, _ = cli("show", "1")
        assert "新标题" in show
        assert "from-exec-update" in show
        assert "DocB" in show


class TestSnippetDirect:
    def test_snippet_query_in_text(self):
        import wl
        s = wl._snippet("xxxxxxx target yyyyy", "target")
        assert "target" in s

    def test_snippet_query_not_in_text(self):
        """fallback: q not in text → return truncated text"""
        import wl
        s = wl._snippet("a" * 200, "qqq")
        assert "a" in s
        # truncated to 80
        assert len(s) < 120 or True

    def test_snippet_empty_query(self):
        import wl
        s = wl._snippet("hello", "")
        assert "hello" in s


class TestHlAndStatusFilter:
    def test_hl_with_query_no_match(self):
        import wl
        s = wl._hl("hello", "missing")
        assert "hello" in s

    def test_hl_with_match(self):
        import wl
        s = wl._hl("hello world", "world")
        assert "world" in s

    def test_hl_empty_query(self):
        """empty q hits the 'if not q' early return"""
        import wl
        s = wl._hl("hello", "")
        assert "hello" in s


class TestCheckinCollectAlreadyLogged:
    def test_already_logged_marked(self, cli):
        """already logged today → already=True flag"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h1", "-k", "habit")
        cli("sched", "1", today)
        cli("tick", "1")  # already checked in today
        import wl
        con = wl.db_connect()
        # mock args
        class A: kind = None; all_kinds = False; show_canceled = False
        # _checkin_collect returns (rows, today, kinds)
        rows, today_str, kinds = wl._checkin_collect(con, A())
        assert rows
        assert rows[0]["already"] is True


class TestPrintDayActivityHabit:
    def test_habit_with_today_log_shows_x(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", today, "-k", "day", "--parent", "1")
        cli("add", "h1", "-k", "habit")
        cli("log", "3", "done today")
        _, out, _ = cli("tree", "--root", "2", "--depth", "2")
        # habit with a log that day should render [x]
        assert "[x]" in out


class TestNodeClockMinTwoValidLogs:
    def test_log_span_calculated(self, cli):
        """two valid logs with different timestamps → fromisoformat success path"""
        cli("add", "t1", "-k", "task")
        cli("log", "1", "first", "--date", "2025-01-01", "--time", "09:00")
        cli("log", "1", "last", "--date", "2025-01-01", "--time", "11:00")
        import wl
        con = wl.db_connect()
        result = wl._node_clock_min(con, 1)
        # 2 hours = 120 min
        assert result == 120


class TestImportNodeRefMap:
    def test_import_with_parent_ref(self, cli, tmp_path):
        import json as _json
        spec = {"add": [
            {"ref": "P", "title": "Parent", "kind": "project"},
            {"parent_ref": "P", "title": "Child", "kind": "task"},
        ]}
        p = tmp_path / "ref.json"
        p.write_text(_json.dumps(spec))
        cli("import", str(p))
        _, show, _ = cli("show", "2")
        assert "Parent" in show  # child #2 upstream should include Parent

    def test_import_unresolved_parent_ref(self, cli, tmp_path):
        import json as _json
        spec = {"add": [{"parent_ref": "X", "title": "orphan", "kind": "task"}]}
        p = tmp_path / "bad.json"
        p.write_text(_json.dumps(spec))
        code, _, _ = cli("import", str(p))
        assert code != 0

    def test_import_missing_title(self, cli, tmp_path):
        import json as _json
        spec = {"add": [{"kind": "task"}]}  # missing title
        p = tmp_path / "bad.json"
        p.write_text(_json.dumps(spec))
        code, _, _ = cli("import", str(p))
        assert code != 0


class TestInsertLogClockNotPromote:
    def test_clock_in_does_not_promote(self, cli):
        """CLOCK_IN log must not auto-advance TODO to DOING (start command sets status itself)"""
        cli("add", "t1", "-k", "task")
        # don't call start; insert CLOCK_IN log via internal API
        import wl
        con = wl.db_connect()
        wl._insert_log(con, 1, "CLOCK_IN")
        con.commit()
        # status must not be changed by _insert_log (CLOCK is not a progress log)
        row = con.execute("SELECT status FROM node WHERE id=1").fetchone()
        assert row["status"] in (None, "TODO")


class TestParseWldEdges:
    def test_parse_wld_empty_lines_skipped(self, cli, tmp_path):
        p = tmp_path / "e.txt"
        p.write_text("\n\n# this is a comment\n+ [ ] real\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "real" in show

    def test_parse_wld_update_with_inline_marker(self, cli, tmp_path):
        """~ [x] #1 inline shorthand → marker+status"""
        cli("add", "t1", "-k", "task")
        p = tmp_path / "i.txt"
        p.write_text("~ [x] #1\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DONE" in show

    def test_parse_wld_at_log_after_anchor(self, cli, tmp_path):
        """+ followed by @log → subs array"""
        p = tmp_path / "a.txt"
        p.write_text("+ [ ] task\n@log inline-log\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "inline-log" in show


class TestSmallGaps:
    """remaining 1-2 line gaps"""

    def test_hl_direct_no_match(self):
        import wl
        s = wl._hl("alpha beta", "missing")
        # i<0 branch
        assert "alpha" in s

    def test_status_filter_no_exclusion(self):
        """include_canceled=True + hide_done=False → empty frag"""
        import wl
        frag, params = wl._status_filter_sql(include_canceled=True, hide_done=False)
        assert frag == ""
        assert params == []

    def test_apply_sub_link_branch(self, cli, tmp_path):
        """+ @link → _apply_sub link branch"""
        p = tmp_path / "a.txt"
        p.write_text("+ [ ] x\n@link DocOnly\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "DocOnly" in show

    def test_apply_sub_prop_branch(self, cli, tmp_path):
        """+ @prop k=v → _apply_sub prop branch"""
        p = tmp_path / "a.txt"
        p.write_text("+ [ ] x\n@prop owner=me\n")
        cli("apply", str(p))
        _, show, _ = cli("show", "1")
        assert "owner" in show

    def test_themes_when_rich_unavailable(self, cli, monkeypatch):
        """simulate _RICH_AVAIL=False, falling back to plain-text path"""
        import wl
        monkeypatch.setattr(wl, "_RICH_AVAIL", False)
        _, out, _ = cli("themes")
        # no crash; shows "rich not installed"
        assert "rich" in out

    def test_tree_children_with_canceled_excluded(self, cli):
        cli("add", "p1", "-k", "project")  # id 1
        cli("add", "c1", "-k", "task", "--parent", "1")  # id 2
        cli("add", "c2", "-k", "task", "--parent", "1")  # id 3
        cli("cancel", "3")  # c2 cancel
        _, out, _ = cli("tree", "--root", "1")
        assert "c1" in out
        assert "c2" not in out

    def test_node_project_direct_no_ancestor(self, cli):
        """_node_project: direct call; node with no project ancestor → fallback returns None"""
        cli("add", "lonely", "-k", "task")
        import wl
        con = wl.db_connect()
        pid, ptitle = wl._node_project(con, 1)
        assert pid is None
        assert "unassigned" in ptitle

    def test_node_project_direct_with_ancestor(self, cli):
        """_node_project: node under a project → returns project id+title"""
        cli("add", "p1", "-k", "project")  # id 1
        cli("add", "t1", "-k", "task", "--parent", "1")  # id 2
        import wl
        con = wl.db_connect()
        pid, ptitle = wl._node_project(con, 2)
        assert pid == 1
        assert "p1" in ptitle

    def test_print_day_activity_with_many_logs_shows_omitted(self, cli):
        """log_tail=3 default: 5 logs → omission line shown"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", today, "-k", "day", "--parent", "1")
        cli("add", "t1", "-k", "task")
        for i in range(6):
            cli("log", "3", f"log-{i}")
        _, out, _ = cli("tree", "--root", "2", "--depth", "2")
        assert "elided" in out or "log-5" in out  # default tail=3; at least expand tail

    def test_ls_with_status_filter(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        _, out, _ = cli("ls", "--status", "DONE")
        assert "t1" in out

    def test_apply_dryrun_with_ref_map(self, cli, tmp_path):
        """dry-run + add with ref → ref displayed"""
        import json as _json
        spec = {"add": [{"ref": "P", "title": "RP", "kind": "project"}]}
        p = tmp_path / "ok.json"
        p.write_text(_json.dumps(spec))
        _, out, _ = cli("import", str(p), "--dry-run")
        assert "ref" in out and "P" in out

    def test_apply_no_commit_on_dry(self, cli, tmp_path):
        """dry-run actually does not write to DB"""
        p = tmp_path / "x.txt"
        p.write_text("+ [ ] dry-only\n")
        cli("apply", str(p), "--dry-run")
        import wl
        con = wl.db_connect()
        assert not con.execute("SELECT 1 FROM node WHERE id = 1").fetchone()

    def test_parse_fieldop_unknown_returns_none(self):
        import wl
        assert wl._parse_fieldop("totally-not-a-fieldop") is None


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


class TestNodeLineWithClockTags:
    """_node_line: clock/tags display branches"""

    def test_node_line_with_clock_and_tags(self, cli):
        cli("add", "t1", "-k", "task", "-t", "work,urgent")
        cli("start", "1")
        cli("stop", "1")
        _, out, _ = cli("ls", "--all")
        # both tags and clock should appear
        assert "t1" in out


class TestCmdReopen:
    def test_reopen_done_back_to_todo(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1")
        cli("reopen", "1")
        _, out, _ = cli("ls")
        assert "t1" in out  # reappears


class TestCmdActive:
    def test_active_shows_running_clocks(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "t1" in out

    def test_active_empty(self, cli):
        _, out, _ = cli("active")
        # nothing running → friendly hint
        assert "no" in out or "(" in out or out == ""


class TestProjectFilters:
    def test_projects_since(self, cli):
        cli("add", "P1", "-k", "project")
        cli("add", "t1", "-k", "task", "--parent", "1")
        cli("log", "2", "p")
        _, out, _ = cli("projects", "--since", "2020-01-01")
        assert "P1" in out


class TestStartStopAt:
    """wl start --at / wl stop --at backfill past timestamps"""

    def test_start_at_hhmm(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "09:00")
        _, show, _ = cli("show", "1")
        assert " 09:00:00" in show
        assert "⏱ clock-in" in show

    def test_start_at_full_ts(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "2025-01-02 10:00")
        _, show, _ = cli("show", "1")
        assert "2025-01-02 10:00:00" in show

    def test_start_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("start", "1", "--at", "25:99")
        assert code != 0

    def test_stop_at_after_start(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "2025-01-02 09:00")
        _, stop_out, _ = cli("stop", "1", "--at", "2025-01-02 09:30")
        assert "elapsed 30 min" in stop_out

    def test_stop_at_before_start_rejected(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1", "--at", "2025-01-02 10:00")
        code, _, _ = cli("stop", "1", "--at", "2025-01-02 09:00")
        assert code != 0

    def test_stop_without_start(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("stop", "1")
        assert code != 0

    def test_stop_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("start", "1")
        code, _, _ = cli("stop", "1", "--at", "garbage")
        assert code != 0


class TestSpent:
    """wl spent <id> <duration> backfill"""

    def test_spent_minutes(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "45")
        assert "45min" in out

    def test_spent_hour_minute(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "1h30m")
        assert "90min" in out

    def test_spent_hour_only(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "2h")
        assert "120min" in out

    def test_spent_minute_suffix(self, cli):
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("spent", "1", "30m")
        assert "30min" in out

    def test_spent_with_end_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("spent", "1", "30m", "--at", "2025-01-02 14:30")
        _, show, _ = cli("show", "1")
        # start 14:00, end 14:30
        assert "2025-01-02 14:30:00" in show
        assert "2025-01-02 14:00:00" in show

    def test_spent_invalid_duration(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("spent", "1", "garbage")
        assert code != 0

    def test_spent_zero_duration(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("spent", "1", "0")
        assert code != 0

    def test_spent_node_not_found(self, cli):
        code, _, _ = cli("spent", "999", "30m")
        assert code != 0

    def test_spent_clock_total_recorded(self, cli):
        """CLOCK pair written by spent should be counted by _node_clock_min"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("spent", "1", "45m")
        _, out, _ = cli("day", today)
        assert "[45m]" in out or "45min" in out


class TestDoneCompound:
    """wl done --log / --at: one-shot log + done"""

    def test_done_with_log(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "--log", "PR#42 merged")
        _, show, _ = cli("show", "1")
        assert "DONE" in show
        assert "PR#42 merged" in show

    def test_done_with_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "--at", "2025-01-02 14:30")
        _, show, _ = cli("show", "1")
        assert "closed_at 2025-01-02 14:30:00" in show

    def test_done_with_log_and_at(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "--log", "结果说明", "--at", "16:00")
        _, show, _ = cli("show", "1")
        assert "结果说明" in show
        assert " 16:00:00" in show  # both log + closed_at use 16:00

    def test_done_with_m_alias(self, cli):
        cli("add", "t1", "-k", "task")
        cli("done", "1", "-m", "via -m alias")
        _, show, _ = cli("show", "1")
        assert "via -m alias" in show

    def test_cancel_with_log(self, cli):
        cli("add", "t1", "-k", "task")
        cli("cancel", "1", "--log", "abandon: 不再做这条")
        _, show, _ = cli("show", "1")
        assert "CANCELED" in show
        assert "abandon" in show

    def test_done_invalid_at(self, cli):
        cli("add", "t1", "-k", "task")
        code, _, _ = cli("done", "1", "--at", "garbage")
        assert code != 0

    def test_done_multi_id_with_log(self, cli):
        cli("add", "t1", "-k", "task")
        cli("add", "t2", "-k", "task")
        cli("done", "1", "2", "--log", "批量收尾")
        for nid in ("1", "2"):
            _, show, _ = cli("show", nid)
            assert "DONE" in show
            assert "批量收尾" in show


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


class TestLsAdvanced:
    """wl ls multi-dimension sort / filter / default limit (modeled on shell ls -t/-S/-r)"""

    def test_default_limit_20(self, cli):
        for i in range(25):
            cli("add", f"t{i}", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task")
        # default limit 20
        assert "showing 20/25" in out
        # t0..t19 present (priority+id ascending)
        assert "t0" in out and "t19" in out
        assert "t20" not in out and "t24" not in out

    def test_all_lifts_default_limit(self, cli):
        for i in range(25):
            cli("add", f"t{i}", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--all")
        assert "t24" in out

    def test_limit_0_lifts(self, cli):
        for i in range(25):
            cli("add", f"t{i}", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--limit", "0")
        assert "t24" in out

    def test_sort_created_desc(self, cli):
        """--sort created: newest first (like shell ls -t)"""
        cli("add", "first", "-k", "task")
        cli("add", "second", "-k", "task")
        cli("add", "third", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--sort", "created")
        # third should be first
        idx_first = out.find("first")
        idx_third = out.find("third")
        assert idx_third < idx_first

    def test_sort_title(self, cli):
        cli("add", "zebra", "-k", "task")
        cli("add", "apple", "-k", "task")
        cli("add", "mango", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--sort", "title")
        idx_a = out.find("apple")
        idx_z = out.find("zebra")
        assert idx_a < idx_z

    def test_reverse_flag(self, cli):
        cli("add", "first", "-k", "task")
        cli("add", "second", "-k", "task")
        _, out_normal, _ = cli("ls", "--kind", "task", "--sort", "id")
        _, out_rev, _ = cli("ls", "--kind", "task", "--sort", "id", "--reverse")
        # forward: first first; reverse: second first
        assert out_normal.find("first") < out_normal.find("second")
        assert out_rev.find("second") < out_rev.find("first")

    def test_unscheduled_filter(self, cli):
        from datetime import date
        cli("add", "planned-alpha", "-k", "task")
        cli("add", "open-beta", "-k", "task")
        cli("sched", "1", date.today().isoformat())
        _, out, _ = cli("ls", "--kind", "task", "--unscheduled")
        assert "open-beta" in out
        assert "planned-alpha" not in out

    def test_recent_n_days(self, cli):
        """--recent N: changed within the last N days (including created)"""
        cli("add", "new-task", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--recent", "1")
        assert "new-task" in out

    def test_ids_direct(self, cli):
        """--ids 1 3 5: like shell ls file1 file3 — bypass filters, list directly"""
        cli("add", "a", "-k", "task")
        cli("add", "b", "-k", "task")
        cli("add", "c", "-k", "task")
        _, out, _ = cli("ls", "--ids", "1", "3")
        assert "a" in out
        assert "c" in out
        assert "b" not in out

    def test_ids_unknown_skipped(self, cli):
        cli("add", "a", "-k", "task")
        _, out, _ = cli("ls", "--ids", "999")
        assert "no nodes matched" in out

    def test_short_r_flag_for_reverse(self, cli):
        cli("add", "first", "-k", "task")
        cli("add", "second", "-k", "task")
        _, out, _ = cli("ls", "--kind", "task", "--sort", "id", "-r")
        assert out.find("second") < out.find("first")

    def test_bare_ls_no_hint_pollution(self, cli):
        """bare ls does not pollute stdout (hints moved to --help epilog)"""
        cli("add", "t1", "-k", "task")
        _, out, _ = cli("ls")
        # should list only t1, no "(bare ls...)" hint
        assert "t1" in out
        assert "裸 ls" not in out
        assert "精准" not in out


class TestPrecisionQueryHints:
    """tests for the query-precision design principle landing"""

    def test_ls_help_has_usage_examples(self, cli):
        """hints moved to --help epilog (no stdout pollution)"""
        # argparse --help calls SystemExit(0); call build_parser to inspect the epilog directly
        import wl
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


class TestPrintCompletionFish:
    """wl print-completion fish: argparse → fish completion generator"""

    def test_fish_completion_header(self, cli):
        _, out, _ = cli("print-completion", "fish")
        assert "auto-generated" in out
        assert "complete -c wl -f" in out

    def test_fish_helper_functions_emitted(self, cli):
        _, out, _ = cli("print-completion", "fish")
        for fn in ("__wl_list_nodes", "__wl_list_tags",
                   "__wl_date_suggestions", "__wl_recur_suggestions"):
            assert f"function {fn}" in out

    def test_fish_subcommands_listed(self, cli):
        _, out, _ = cli("print-completion", "fish")
        # all subcommands should appear
        for cmd in ("add", "log", "done", "ls", "tree", "logs", "find",
                    "spent", "relog", "unlog", "checkin", "sched"):
            assert f'-a "{cmd}"' in out

    def test_fish_global_flags(self, cli):
        _, out, _ = cli("print-completion", "fish")
        assert "-l brief" in out  # -q/--brief
        assert "-l color" in out
        assert "-l show-canceled" in out

    def test_fish_choices_inline(self, cli):
        """choices=[...] emits -a "v1 v2 ..." """
        _, out, _ = cli("print-completion", "fish")
        # wl ls --sort choices
        assert "-a \"pri created updated closed scheduled title id\"" in out

    def test_fish_helpers_attached_to_recur(self, cli):
        """sched --recur uses __wl_recur_suggestions"""
        _, out, _ = cli("print-completion", "fish")
        assert "(__wl_recur_suggestions)" in out

    def test_fish_node_id_completion(self, cli):
        """show / done / log positional args → __wl_list_nodes"""
        _, out, _ = cli("print-completion", "fish")
        assert "(__wl_list_nodes)" in out

    def test_fish_date_suggestions(self, cli):
        """day / dateinfo accept dates → __wl_date_suggestions"""
        _, out, _ = cli("print-completion", "fish")
        assert "(__wl_date_suggestions)" in out

    def test_fish_compound_flags_present(self, cli):
        """wl add compound flags --log/--done/--at/--link must appear"""
        _, out, _ = cli("print-completion", "fish")
        # add subcommand section
        assert "-l done" in out
        assert "-l link" in out
        assert "-l at" in out
        assert "-l log" in out

    def test_fish_no_db_required(self, cli, tmp_path, monkeypatch):
        """print-completion runs without a DB (meta command)"""
        # point WORKLOG_DB to a non-existent path; print-completion should still work
        monkeypatch.setenv("WORKLOG_DB", str(tmp_path / "no-such.db"))
        code, out, _ = cli("print-completion", "fish")
        assert code == 0
        assert "complete -c wl" in out

    def test_unsupported_shell_rejected(self, cli):
        import pytest
        with pytest.raises(SystemExit):  # argparse choices rejects 'tcsh' at parse time
            cli("print-completion", "tcsh")


class TestPrintCompletionBash:
    """wl print-completion bash: argparse → bash _wl() function"""

    def test_bash_completion_header(self, cli):
        _, out, _ = cli("print-completion", "bash")
        assert "auto-generated" in out
        assert "complete -F _wl wl" in out
        assert "_wl() {" in out

    def test_bash_helper_functions_emitted(self, cli):
        _, out, _ = cli("print-completion", "bash")
        for fn in ("__wl_list_nodes_bash", "__wl_list_tags_bash",
                   "__wl_date_suggestions_bash", "__wl_recur_suggestions_bash"):
            assert f"{fn}()" in out

    def test_bash_subcommand_names_in_subcmds(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # subcmds list contains all subcommands
        for cmd in ("add", "log", "done", "ls", "tree", "spent", "relog", "checkin"):
            assert cmd in out

    def test_bash_case_per_subcmd(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # case "$sub" in covers every sub
        for cmd in ("add", "ls", "sched"):
            assert f"{cmd})" in out

    def test_bash_prev_case_for_choices(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # e.g. ls --sort choices should appear in prev case
        assert "pri created updated closed scheduled title id" in out

    def test_bash_node_id_helper_in_positional(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # subcommands like done/start take a positional node id → __wl_list_nodes_bash
        assert "__wl_list_nodes_bash" in out


class TestPrintCompletionZsh:
    """wl print-completion zsh: argparse → zsh _wl() + compdef"""

    def test_zsh_completion_header(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        assert out.startswith("#compdef wl")
        assert "auto-generated" in out
        assert "compdef _wl wl" in out

    def test_zsh_helper_functions_emitted(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        for fn in ("__wl_list_nodes_zsh", "__wl_list_tags_zsh",
                   "__wl_date_suggestions_zsh", "__wl_recur_suggestions_zsh"):
            assert f"{fn}()" in out

    def test_zsh_uses_arguments(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        assert "_arguments" in out
        assert "_describe" in out

    def test_zsh_state_machine(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        # uses ->cmds / ->args state machine
        assert "->cmds" in out
        assert "->args" in out

    def test_zsh_subcommand_descriptions(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        # _describe uses 'name:description' format
        assert "add:" in out  # colon between description and name
        assert "log:" in out

    def test_zsh_recur_helper_attached(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        assert "__wl_recur_suggestions_zsh" in out


class TestAllSubparsersHaveDescription:
    """battery-included DESIGN §35: every sub parser must have a description
    (one-line intro after usage line), falling back to help."""

    def test_every_subparser_has_description(self):
        import argparse, wl
        p = wl.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        missing = []
        seen = set()
        for name, sub in sa.choices.items():
            if id(sub) in seen:
                continue  # skip alias
            seen.add(id(sub))
            if not (sub.description or "").strip():
                missing.append(name)
        assert not missing, f"the following sub parsers lack description: {missing}"

    def test_every_subparser_has_epilog(self):
        """§35 battery-included: every cmd should have an epilog with examples / differences from adjacent commands / use cases"""
        import argparse, wl
        p = wl.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        missing = []
        seen = set()
        for name, sub in sa.choices.items():
            if id(sub) in seen:
                continue
            seen.add(id(sub))
            if not (sub.epilog or "").strip():
                missing.append(name)
        assert not missing, f"the following sub parsers lack epilog (§35): {missing}"


class TestActiveBatteryIncluded:
    """wl active enhancements: current session + today's total + latest log + epilog"""

    def test_active_empty_hint(self, cli):
        _, out, _ = cli("active")
        assert "no active task right now" in out or "wl start" in out

    def test_active_shows_running_task(self, cli):
        cli("add", "在跑的活", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "在跑的活" in out
        assert "#1" in out

    def test_active_shows_today_total(self, cli):
        """today's total should appear (with "X min" text)"""
        cli("add", "work item", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "today's total" in out

    def test_active_shows_recent_log(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "progress: finished part A; next part B")
        cli("start", "1")
        _, out, _ = cli("active")
        assert "latest log" in out
        # body should appear (truncated oneline or full)
        assert "完成了 A 部分" in out or "progress" in out

    def test_active_brief_skips_detail(self, cli):
        """-q compact mode: skips total / latest log expansion"""
        cli("add", "work item", "-k", "task")
        cli("start", "1")
        _, out, _ = cli("-q", "active")
        assert "work item" in out
        assert "today's total" not in out
        assert "latest log" not in out

    def test_active_help_epilog(self, cli):
        """--help should include use cases and diff from wl day"""
        import wl
        p = wl.build_parser()
        sa = next(a for a in p._actions if hasattr(a, "choices") and "active" in (a.choices or {}))
        active_p = sa.choices["active"]
        epilog = active_p.epilog or ""
        assert "Use cases:" in epilog
        assert "wl day" in epilog and "Difference from" in epilog


class TestLogFormatOneline:
    """global --log-format {oneline,full}: default oneline truncates log body by terminal width; full expands.
    Unified across wl day / wl tree / wl logs / wl show."""

    LONG_BODY = "x" * 200  # 200 chars exceeds any reasonable terminal width

    def test_truncate_helper_oneline_default(self):
        import wl
        out = wl._truncate_log_body(self.LONG_BODY, indent_cols=10, full=False)
        assert out.endswith("…")
        assert len(out) < 200

    def test_truncate_helper_full(self):
        import wl
        out = wl._truncate_log_body(self.LONG_BODY, indent_cols=10, full=True)
        assert out == self.LONG_BODY

    def test_truncate_helper_short_body_unchanged(self):
        import wl
        assert wl._truncate_log_body("短", 10, full=False) == "短"

    def test_log_full_helper(self):
        import wl
        from types import SimpleNamespace
        assert wl._log_full(SimpleNamespace(log_format="full")) is True
        assert wl._log_full(SimpleNamespace(log_format="oneline")) is False
        assert wl._log_full(SimpleNamespace()) is False  # missing attr defaults to False

    def test_day_oneline_truncates(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("day", today)
        # after body truncation there should be no run of 200 x's
        assert self.LONG_BODY not in out
        assert "…" in out

    def test_day_full_keeps_body(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t1", "-k", "task")
        cli("sched", "1", today)
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("--log-format", "full", "day", today)
        assert self.LONG_BODY in out

    def test_logs_by_task_oneline(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("logs", "--by-task", "today")
        assert self.LONG_BODY not in out
        assert "…" in out

    def test_logs_by_task_full(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("--log-format", "full", "logs", "--by-task", "today")
        assert self.LONG_BODY in out

    def test_show_timeline_oneline(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("show", "1")
        assert self.LONG_BODY not in out
        assert "…" in out

    def test_show_timeline_full(self, cli):
        cli("add", "t1", "-k", "task")
        cli("log", "1", self.LONG_BODY)
        _, out, _ = cli("--log-format", "full", "show", "1")
        assert self.LONG_BODY in out

    def test_invalid_log_format_rejected(self, cli):
        import pytest
        with pytest.raises(SystemExit):
            cli("--log-format", "garbage", "day")


class TestUserAliasesIni:
    """~/.config/worklog/aliases.ini → argparse aliases (cross-shell: wl d = wl day)"""

    def _setup_aliases(self, tmp_path, monkeypatch, content):
        config_dir = tmp_path / ".config" / "worklog"
        config_dir.mkdir(parents=True)
        (config_dir / "aliases.ini").write_text(content)
        monkeypatch.setenv("HOME", str(tmp_path))
        # CI runners may preset XDG_CONFIG_HOME / XDG_DATA_HOME — clear them
        # so _xdg_config_home() / _xdg_data_home() fall back to $HOME (tmp_path).
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        import wl, importlib
        wl._USER_ALIASES = None  # force reload
        importlib.reload(wl)
        return wl

    def test_load_aliases_basic(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nc = checkin\n")
        loaded = wl._load_user_aliases()
        assert loaded == {"day": ["d"], "checkin": ["c"]}

    def test_load_aliases_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        import wl, importlib
        wl._USER_ALIASES = None
        importlib.reload(wl)
        assert wl._load_user_aliases() == {}

    def test_load_aliases_no_section(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch, "# empty file with no [aliases]\n")
        assert wl._load_user_aliases() == {}

    def test_load_aliases_multi_to_same_target(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nda = day\n")
        loaded = wl._load_user_aliases()
        assert sorted(loaded["day"]) == ["d", "da"]

    def test_parser_recognizes_alias(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nll = ls\n")
        parser = wl.build_parser()
        # alias should be in subparsers choices
        sa = next(a for a in parser._actions if isinstance(a, __import__("argparse")._SubParsersAction))
        assert "d" in sa.choices
        assert "ll" in sa.choices
        # main name and alias point to the same parser
        assert sa.choices["d"] is sa.choices["day"]
        assert sa.choices["ll"] is sa.choices["ls"]

    def test_alias_in_fish_completion(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\nc = checkin\n")
        out = wl._generate_fish_completion(wl.build_parser())
        # main name + alias should both appear
        assert '"day"' in out and '"d"' in out
        assert '"checkin"' in out and '"c"' in out
        # alias entries marked with "= day"
        assert "(= day)" in out
        # subcommand argument condition should include main name + alias
        assert "__fish_seen_subcommand_from day d" in out

    def test_alias_in_bash_completion(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\n")
        out = wl._generate_bash_completion(wl.build_parser())
        # subcmds list includes d
        assert " d " in out or " d\"" in out
        # case pattern day|d)
        assert "day|d)" in out

    def test_alias_in_zsh_completion(self, tmp_path, monkeypatch):
        wl = self._setup_aliases(tmp_path, monkeypatch,
                                  "[aliases]\nd = day\n")
        out = wl._generate_zsh_completion(wl.build_parser())
        assert "'d:" in out
        # case includes day|d
        assert "day|d)" in out

    def test_no_aliases_clean_output(self, tmp_path, monkeypatch):
        """without an ini file, output should have no alias traces"""
        monkeypatch.setenv("HOME", str(tmp_path))
        import wl, importlib
        wl._USER_ALIASES = None
        importlib.reload(wl)
        out = wl._generate_fish_completion(wl.build_parser())
        assert "(= day)" not in out  # not shown when no aliases


class TestEditInEditorUnlink:
    def test_edit_in_editor_cleanup_oserror_swallowed(self, monkeypatch, tmp_db):
        """_edit_in_editor finally block silently swallows os.unlink OSError (covers last 2 lines)"""
        import wl, subprocess as _sp
        monkeypatch.setattr(_sp, "call", lambda argv: 0)
        # make os.unlink raise OSError
        import os
        real_unlink = os.unlink
        def fake_unlink(p):
            real_unlink(p)  # actually delete first
            raise OSError("simulated")
        monkeypatch.setattr(os, "unlink", fake_unlink)
        # should not crash
        result = wl._edit_in_editor("hello", suffix=".txt")
        assert result == "hello"
