"""Reads that used to be direct SQL: full fields, no terminal-width clipping.

Covers `wl log show` (several ids), `wl logs` (several --id, --match, --log-format full,
local time), `wl ls` / `wl show` identity columns under -q, `wl day` day-node resolution,
`wl sched --deleted`, and `wl prop ls` across nodes with a key prefix.
"""
import json
import re


class TestLogShowSeveral:
    def test_several_ids_print_full_bodies(self, cli):
        cli("add", "a task with a long title that must not be clipped")
        cli("log", "1", "first line\nsecond line")
        cli("log", "1", "only the second entry")
        code, out, _ = cli("log", "show", "#L1", "#L2")
        assert code == 0
        assert "#L1" in out and "#L2" in out
        assert "#1" in out
        assert "a task with a long title that must not be clipped" in out
        assert "first line\nsecond line" in out
        assert "only the second entry" in out
        assert "note" in out
        assert "…" not in out

    def test_show_json_logged_at_is_local(self, cli, tmp_db, monkeypatch):
        monkeypatch.setenv("WORKLOG_TZ", "+08:00")
        cli("add", "t")
        cli("log", "1", "early")
        con = tmp_db.db_connect()
        con.execute("UPDATE log SET logged_at='2026-06-04 17:00:00' WHERE body='early'")
        con.commit()
        con.close()
        d = json.loads(cli("log", "show", "1", "-o", "json")[1])
        assert d["logged_at"] == "2026-06-05 01:00:00"
        assert d["body"] == "early"
        _, text, _ = cli("log", "show", "1")
        assert "2026-06-05 01:00:00" in text


class TestLogsFullRead:
    def test_several_ids_and_match(self, cli):
        cli("add", "alpha task")
        cli("add", "beta task")
        cli("log", "1", "alpha unique body")
        cli("log", "2", "beta unique body")
        _, both, _ = cli("logs", "--id", "1", "2")
        assert "alpha unique body" in both and "beta unique body" in both
        _, matched, _ = cli("logs", "--match", "alpha unique")
        assert "alpha unique body" in matched
        assert "beta unique body" not in matched

    def test_full_format_keeps_title_tag_and_newlines(self, cli, monkeypatch):
        from worklog import helpers
        monkeypatch.setattr(helpers, "_term_width", lambda: 40)
        title = "把 asset 的版本号写进发布说明并且不要截断"
        cli("add", title)
        cli("log", "1", "第一行\n第二行 " + "x" * 80)
        cli("retag", "#L1", "goal")
        _, out, _ = cli("logs", "--id", "1", "--log-format", "full")
        assert title in out
        assert "…" not in out
        assert "第一行\n第二行" in out
        header = next(ln for ln in out.splitlines() if "#L1" in ln)
        assert "goal" in header
        assert re.search(r"\d{2}:\d{2}:\d{2}", header)

    def test_window_uses_local_day_and_json_is_local(self, cli, tmp_db, monkeypatch):
        monkeypatch.setenv("WORKLOG_TZ", "+08:00")
        cli("add", "t")
        cli("log", "1", "early local")
        con = tmp_db.db_connect()
        con.execute(
            "UPDATE log SET logged_at='2026-06-04 17:00:00' WHERE body='early local'"
        )
        con.commit()
        con.close()
        _, on_local, _ = cli("logs", "--id", "1", "--date", "2026-06-05")
        assert "early local" in on_local
        _, on_utc_date, _ = cli("logs", "--id", "1", "--date", "2026-06-04")
        assert "early local" not in on_utc_date
        rows = json.loads(cli("logs", "--id", "1", "--since", "2026-06-01", "-o", "json")[1])
        assert rows[0]["logged_at"] == "2026-06-05 01:00:00"
        assert rows[0]["body"] == "early local"
        assert rows[0]["node_title"] == "t"


class TestNodeIdentityColumns:
    def test_ls_brief_keeps_status_parent_and_full_date(self, cli, monkeypatch):
        from worklog import helpers
        monkeypatch.setattr(helpers, "_term_width", lambda: 30)
        title = "T" * 80
        cli("add", "parent task")
        cli("add", title, "--parent", "1", "--scheduled", "2026-10-12")
        _, out, _ = cli("ls", "--ids", "2", "-q")
        # wrap may fold a long title; every character is still there (no "…" clip)
        assert "…" not in out
        assert title in out.replace(" ", "").replace("\n", "")
        assert "status=TODO" in out
        assert "parent=1" in out
        assert "scheduled=2026-10-12" in out
        assert "@10-12" not in out
        rows = json.loads(cli("ls", "--ids", "2", "-q", "-o", "json")[1])
        assert rows[0]["id"] == 2
        assert rows[0]["title"] == title
        assert rows[0]["status"] == "TODO"
        assert rows[0]["parent_id"] == 1
        assert rows[0]["scheduled_date"] == "2026-10-12"

    def test_show_labels_parent_id_under_brief(self, cli):
        cli("add", "parent task")
        cli("add", "child task", "--parent", "1", "--scheduled", "2026-10-12")
        _, out, _ = cli("show", "2", "-q")
        assert "parent_id:" in out
        assert "#1" in out
        assert "2026-10-12" in out
        assert "child task" in out
        _, root, _ = cli("show", "1", "-q")
        assert "parent_id:" in root
        assert re.search(r"parent_id:\s+-", root)


class TestDayNodeResolve:
    def test_existing_day_prints_id_title_parent(self, cli):
        cli("add", "2026-W21", "--prop", "type.date=week", "--prop", "date.period=2026-W21")
        cli("add", "2026-05-20 full day title",
            "--prop", "type.date=day", "--prop", "date.period=2026-05-20", "--parent", "1")
        _, out, _ = cli("day", "2026-05-20")
        first = out.splitlines()[0]
        assert "#2" in first
        assert "2026-05-20 full day title" in first
        assert "parent=1" in first
        d = json.loads(cli("day", "2026-05-20", "-o", "json")[1])
        assert d["node_id"] == 2
        assert d["title"] == "2026-05-20 full day title"
        assert d["parent_id"] == 1

    def test_missing_day_node_is_explicit(self, cli):
        _, out, _ = cli("day", "2099-01-01")
        assert "(no day node)" in out
        assert not any(ln.startswith("#") for ln in out.splitlines()[:2])
        d = json.loads(cli("day", "2099-01-01", "-o", "json")[1])
        assert d["node_id"] is None
        assert d["title"] is None
        assert d["parent_id"] is None


class TestSchedDeleted:
    def test_default_hides_deleted_and_flag_shows_deleted_at(self, cli):
        cli("add", "t")
        cli("sched", "1", "2026-06-15")
        cli("sched", "1", "--recur", "weekly:Sat")
        cli("sched", "1", "--clear")
        _, live, _ = cli("sched", "1")
        assert "has no schedule" in live
        assert "2026-06-15" not in live
        _, deleted, _ = cli("sched", "1", "--deleted")
        assert "@2026-06-15" in deleted
        assert "weekly:Sat" in deleted
        assert re.search(r"deleted \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", deleted)
        rows = json.loads(cli("sched", "ls", "1", "--deleted", "-o", "json")[1])["rows"]
        assert rows
        assert all(r["deleted_at"] for r in rows)
        live_json = json.loads(cli("sched", "ls", "1", "-o", "json")[1])
        assert live_json["rows"] == []


class TestPropLsPrefix:
    def test_several_nodes_prefix_and_full_value(self, cli):
        long = "v" * 400 + "\nsecond line of the value"
        cli("add", "a")
        cli("add", "b")
        cli("set", "1", "agent_session.claude", "aaa")
        cli("set", "1", "owner", "xyb")
        cli("set", "1", "note", long)
        cli("set", "2", "agent_session.cursor", "bbb")
        _, out, _ = cli("prop", "ls", "1", "2", "--prefix", "agent_session")
        assert "agent_session.claude=aaa" in out
        assert "agent_session.cursor=bbb" in out
        assert "owner=" not in out
        assert "note=" not in out
        _, full, _ = cli("prop", "ls", "1")
        assert long in full
        assert "…" not in full
        rows = json.loads(cli("prop", "ls", "1", "2", "--prefix", "agent_session", "-o", "json")[1])
        assert {(r["node_id"], r["key"], r["value"]) for r in rows} == {
            (1, "agent_session.claude", "aaa"),
            (2, "agent_session.cursor", "bbb"),
        }
