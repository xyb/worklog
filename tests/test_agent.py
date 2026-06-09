"""Tests for `wl agent` — bind the current agent session to a node, stored as the
`agent_session.claude` prop (WL#573). CRUD: wl agent <id> / wl agent / wl agent ls / wl agent rm.
The session id comes from $WL_SESSION_ID (preferred) or $CLAUDE_CODE_SESSION_ID."""
import sqlite3
import pytest

KEY = "agent_session.claude"


def _bound_value(tmp_db, nid):
    con = tmp_db.db_connect()
    r = con.execute(
        "SELECT value FROM prop WHERE node_id=? AND key=? AND deleted_at IS NULL", (nid, KEY)
    ).fetchone()
    return r["value"] if r else None


def _history_metrics(tmp_db, nid):
    """The append-only bind-history metrics on a node (WL#580 --record)."""
    con = tmp_db.db_connect()
    return con.execute(
        "SELECT value_text, note FROM metric WHERE node_id=? AND tag='agent_session' "
        "AND deleted_at IS NULL ORDER BY id", (nid,)
    ).fetchall()


class TestAgent:
    def _sess(self, monkeypatch, sid="sess-aaa"):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
        monkeypatch.delenv("WL_SESSION_ID", raising=False)

    def test_set_show_roundtrip(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t", "-k", "task")            # #1
        code, out, _ = cli("agent", "1")          # default verb = set
        assert code == 0 and "#1" in out
        assert _bound_value(tmp_db, 1) == "sess-aaa"   # stored as agent_session.claude
        _, out, _ = cli("agent")                   # bare = show current
        assert "#1" in out and "sess-aaa"[:8] in out

    def test_rebind_moves_prop_off_old_node(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "a", "-k", "task"); cli("add", "b", "-k", "task")  # #1 #2
        cli("agent", "1")
        cli("agent", "2")                          # same session rebinds to #2
        assert _bound_value(tmp_db, 1) is None     # #1 cleared
        assert _bound_value(tmp_db, 2) == "sess-aaa"

    def test_ls_lists_all_bindings(self, cli, monkeypatch):
        cli("add", "a", "-k", "task"); cli("add", "b", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2"); cli("agent", "2")
        _, out, _ = cli("agent", "ls")
        assert "#1" in out and "#2" in out and "claude:" in out

    def test_conflict_warns_when_node_held_by_other_session(self, cli, monkeypatch):
        cli("add", "a", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2")
        _, out, _ = cli("agent", "1")              # #1 already held by s1
        assert "覆盖" in out or "已被" in out

    def test_node_not_found(self, cli, monkeypatch):
        self._sess(monkeypatch)
        code, _, err = cli("agent", "999")
        assert code != 0 and "not found" in err

    def test_no_session_env_fails_closed(self, cli, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("WL_SESSION_ID", raising=False)
        cli("add", "t", "-k", "task")
        code, _, err = cli("agent", "1")
        assert code != 0 and "session id" in err

    def test_wl_session_id_takes_priority(self, cli, tmp_db, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-id")
        monkeypatch.setenv("WL_SESSION_ID", "hook-id")
        cli("add", "t", "-k", "task")
        cli("agent", "1")
        assert _bound_value(tmp_db, 1) == "hook-id"   # WL_SESSION_ID wins

    def test_rm_unbinds_current_session(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-rm")
        cli("add", "t", "-k", "task")
        cli("agent", "1")
        cli("agent", "rm")
        assert _bound_value(tmp_db, 1) is None
        _, out, _ = cli("agent")
        assert "未绑定" in out

    def test_show_unbound_session(self, cli, monkeypatch):
        self._sess(monkeypatch, "sess-none")
        _, out, _ = cli("agent")
        assert "未绑定" in out

    # --- WL#580 light design: prop = live pointer; --record = append-only history trail ---

    def test_plain_bind_writes_no_history(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t", "-k", "task")
        cli("agent", "1")                          # no --record
        assert _bound_value(tmp_db, 1) == "sess-aaa"   # live pointer set
        assert _history_metrics(tmp_db, 1) == []        # but no history metric/log

    def test_record_writes_one_history_metric(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-full-1234-xyz")
        cli("add", "t", "-k", "task")
        code, out, _ = cli("agent", "1", "--record")
        assert code == 0 and "history" in out
        rows = _history_metrics(tmp_db, 1)
        assert len(rows) == 1
        assert rows[0]["value_text"] == "sess-full-1234-xyz"   # FULL sid stored, not truncated
        assert rows[0]["note"] == "claude"

    def test_record_rebind_same_pair_no_duplicate(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t", "-k", "task")
        cli("agent", "1", "--record")
        cli("agent", "1", "--record")              # same (node, session) again
        assert len(_history_metrics(tmp_db, 1)) == 1   # not duplicated

    def test_record_history_survives_rebind_to_other_node(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "a", "-k", "task"); cli("add", "b", "-k", "task")  # #1 #2
        cli("agent", "1", "--record")
        cli("agent", "2", "--record")              # live pointer moves to #2
        assert _bound_value(tmp_db, 1) is None      # prop cleared off #1
        assert len(_history_metrics(tmp_db, 1)) == 1   # but #1's history stays
        assert len(_history_metrics(tmp_db, 2)) == 1
