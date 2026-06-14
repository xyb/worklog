"""Tests for `wl agent` — bind the current agent session to a node, stored as the
`agent_session.claude` prop. CRUD: wl agent <id> / wl agent / wl agent ls / wl agent rm.
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
    """The bind-history `agent_session` metrics on a node (--record): value_text = session id."""
    con = tmp_db.db_connect()
    return con.execute(
        "SELECT value_text, note FROM metric WHERE node_id=? AND tag='agent_session' "
        "AND deleted_at IS NULL ORDER BY id", (nid,)
    ).fetchall()


def _agent_metrics(tmp_db, nid):
    """The bind-history `agent` metrics on a node: value_text = the runtime name (claude/codex/…)."""
    con = tmp_db.db_connect()
    return con.execute(
        "SELECT value_text FROM metric WHERE node_id=? AND tag='agent' "
        "AND deleted_at IS NULL ORDER BY id", (nid,)
    ).fetchall()


class TestAgent:
    def _sess(self, monkeypatch, sid="sess-aaa"):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
        monkeypatch.delenv("WL_SESSION_ID", raising=False)
        monkeypatch.delenv("WL_AGENT", raising=False)   # default agent = claude unless a test sets it

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

    def test_ls_columns_aligned_across_id_and_agent_widths(self, cli, monkeypatch):
        """ls pads #id and <agent>:<sid> so the ← / · columns line up despite differing widths."""
        self._sess(monkeypatch)
        for i in range(12):
            cli("add", f"t{i}", "-k", "task")       # ids #1..#10 (1-digit) and #11/#12 (2-digit)
        monkeypatch.setenv("WL_SESSION_ID", "sess1"); monkeypatch.setenv("WL_AGENT", "claude")
        cli("agent", "1")
        monkeypatch.setenv("WL_SESSION_ID", "sess2"); monkeypatch.setenv("WL_AGENT", "codex")
        cli("agent", "12")                          # 2-digit id + shorter agent name
        _, out, _ = cli("agent", "ls")
        lines = [ln for ln in out.splitlines() if "←" in ln]
        assert len(lines) == 2
        # the ← separator must sit at the same column on every row
        assert len({ln.index("←") for ln in lines}) == 1
        # and so must the · before the title
        assert len({ln.index("·") for ln in lines}) == 1

    def test_ls_interactive_color_renders(self, cli, monkeypatch):
        # styled (non-plain) path: full sid kept when it fits
        self._sess(monkeypatch, "sess-interactive")
        cli("add", "task one", "-k", "task")
        cli("agent", "1")
        code, out, _ = cli("--color", "always", "agent", "ls")
        assert code == 0 and "#1" in out

    def test_ls_interactive_shrinks_long_sid_on_narrow_width(self, cli, monkeypatch):
        # styled + tight width → the sid is uniformly shrunk with an ellipsis (the shrink branch)
        self._sess(monkeypatch, "a-very-long-session-id-that-will-not-fit-0987654321")
        cli("add", "a task with a long enough title to crowd the line", "-k", "task")
        cli("agent", "1")
        code, out, _ = cli("--color", "always", "--width", "40", "agent", "ls")
        assert code == 0
        assert "…" in out          # sid truncated to fit the narrow width

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

    # --- light design: prop = live pointer; --record = append-only history trail ---

    def test_plain_bind_records_history_by_default(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t", "-k", "task")
        cli("agent", "1")                          # default → records (so auto-binds capture history)
        assert _bound_value(tmp_db, 1) == "sess-aaa"   # live pointer set
        assert len(_history_metrics(tmp_db, 1)) == 1    # and history recorded by default

    def test_no_record_skips_history(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t", "-k", "task")
        cli("agent", "1", "--no-record")           # opt out → pointer only
        assert _bound_value(tmp_db, 1) == "sess-aaa"
        assert _history_metrics(tmp_db, 1) == []

    def test_later_bind_backfills_missing_history(self, cli, tmp_db, monkeypatch):
        # A pair bound without a history record (early auto-bind / --no-record) gets recorded on a
        # later bind — dedup is by the metric, not "is the prop already set".
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t", "-k", "task")
        cli("agent", "1", "--no-record")           # bound, no history
        assert _history_metrics(tmp_db, 1) == []
        cli("agent", "1")                          # re-bind same pair → backfills history
        assert len(_history_metrics(tmp_db, 1)) == 1
        cli("agent", "1")                          # and again → no duplicate
        assert len(_history_metrics(tmp_db, 1)) == 1

    def test_record_writes_session_and_agent_metrics(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-full-1234-xyz")
        cli("add", "t", "-k", "task")
        code, out, _ = cli("agent", "1", "--record")
        assert code == 0 and "history" in out
        sess = _history_metrics(tmp_db, 1)
        assert len(sess) == 1
        assert sess[0]["value_text"] == "sess-full-1234-xyz"   # session metric: FULL sid, not truncated
        agent = _agent_metrics(tmp_db, 1)
        assert len(agent) == 1
        assert agent[0]["value_text"] == "claude"              # agent metric: the runtime name (default)

    def test_agent_recorded_as_its_own_metric(self, cli, tmp_db, monkeypatch):
        """$WL_AGENT names the runtime → its own `agent` metric (value) + the prop key suffix."""
        self._sess(monkeypatch, "sess-codex-1")
        monkeypatch.setenv("WL_AGENT", "codex")
        cli("add", "t", "-k", "task")
        cli("agent", "1", "--record")
        assert _agent_metrics(tmp_db, 1)[0]["value_text"] == "codex"   # the agent value, not hardcoded claude
        con = tmp_db.db_connect()
        keys = [r["key"] for r in con.execute(
            "SELECT key FROM prop WHERE node_id=1 AND deleted_at IS NULL").fetchall()]
        assert "agent_session.codex" in keys                   # prop key carries the agent too

    def test_agent_flag_overrides_env(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-cursor-1")
        monkeypatch.setenv("WL_AGENT", "codex")
        cli("add", "t", "-k", "task")
        cli("agent", "1", "--record", "--agent", "cursor")     # flag beats env
        assert _agent_metrics(tmp_db, 1)[0]["value_text"] == "cursor"

    def test_agent_name_normalized_lowercase(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-up-1")
        monkeypatch.setenv("WL_AGENT", "  Cursor  ")
        cli("add", "t", "-k", "task")
        cli("agent", "1", "--record")
        assert _agent_metrics(tmp_db, 1)[0]["value_text"] == "cursor"   # trimmed + lowercased

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

    # --- `wl agent context` (machine line for integrations) + cache invalidation ---

    def test_context_outputs_machine_line(self, cli, monkeypatch):
        self._sess(monkeypatch, "sess-ctx")
        cli("add", "hello task", "-k", "task")
        _, out, _ = cli("agent", "context")
        assert out.strip() == ""                    # not bound yet → empty
        cli("agent", "1")
        _, out, _ = cli("agent", "context")
        assert out.strip() == "1\thello task"       # <id>\t<title>

    def test_context_hook_emits_valid_json(self, cli, monkeypatch):
        import json as _json
        self._sess(monkeypatch, "sess-hook")
        cli("add", 'tricky "quote" task', "-k", "task")
        _, out, _ = cli("agent", "context", "--hook")
        assert out.strip() == ""                     # unbound → empty
        cli("agent", "1")
        _, out, _ = cli("agent", "context", "--hook")
        payload = _json.loads(out)                   # valid JSON (title quotes escaped by wl)
        assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "WL#1" in ctx and 'tricky "quote" task' in ctx

    def test_ls_default_groups_by_day(self, cli, monkeypatch):
        # default `wl agent ls` groups bindings into per-day sections (today / yesterday / date)
        from datetime import date
        cli("add", "a", "-k", "task"); cli("add", "b", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2"); cli("agent", "2")
        _, out, _ = cli("agent", "ls")
        assert date.today().isoformat() in out and "today" in out
        assert any(ln.startswith("  #") for ln in out.splitlines())   # rows indented under the header

    def test_ls_flat_has_no_day_header(self, cli, monkeypatch):
        from datetime import date
        cli("add", "a", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        _, out, _ = cli("agent", "ls", "--flat")
        assert "today" not in out and date.today().isoformat() not in out
        assert "#1" in out
        # --no-group is an alias for --flat
        _, out2, _ = cli("agent", "ls", "--no-group")
        assert "today" not in out2

    def test_ls_plain_shows_full_session_id(self, cli, monkeypatch):
        # piped / plain (the test harness is non-TTY) → full session id, never abbreviated
        cli("add", "a", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-full-uuid-0123456789")
        cli("agent", "1")
        _, out, _ = cli("agent", "ls", "--flat")
        assert "sess-full-uuid-0123456789" in out and "…" not in out

    def test_ls_sorts_by_activity_most_recent_first(self, cli, tmp_db, monkeypatch):
        cli("add", "a", "-k", "task"); cli("add", "b", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2"); cli("agent", "2")
        con = tmp_db.db_connect()   # backdate ALL of each node's logs to control latest-activity
        con.execute("UPDATE log SET logged_at='2026-06-05 10:00:00' WHERE node_id=1")
        con.execute("UPDATE log SET logged_at='2026-06-10 10:00:00' WHERE node_id=2")
        con.commit()
        _, out, _ = cli("agent", "ls", "--flat")
        rows = [ln for ln in out.splitlines() if "←" in ln]
        assert rows[0].lstrip().startswith("#2") and rows[1].lstrip().startswith("#1")

    def test_ls_by_bound_sorts_by_bind_time(self, cli, tmp_db, monkeypatch):
        cli("add", "a", "-k", "task"); cli("add", "b", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2"); cli("agent", "2")
        con = tmp_db.db_connect()   # backdate the bind-history carrier log per node, commit BEFORE
        con.execute("UPDATE log SET logged_at='2026-06-03 09:00:00' WHERE node_id=1 AND tag='metric'")
        con.execute("UPDATE log SET logged_at='2026-06-09 09:00:00' WHERE node_id=2 AND tag='metric'")
        con.commit()                # release the write lock before the read-back cli call
        _, out, _ = cli("agent", "ls", "--by", "bound", "--flat")
        rows = [ln for ln in out.splitlines() if "←" in ln]
        assert rows[0].lstrip().startswith("#2")   # bound 06-09 > 06-03 → first

    def test_ls_caps_then_all_shows_everything(self, cli, monkeypatch):
        # cap/elision only applies in the rich (non-plain) view → force is_plain off
        import worklog.render as render
        monkeypatch.setattr(render, "is_plain", lambda: False)
        for i in range(14):
            cli("add", f"t{i}", "-k", "task")
        for i in range(1, 15):
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", f"s{i}"); cli("agent", str(i))
        _, capped, _ = cli("agent", "ls", "--flat")
        assert len([ln for ln in capped.splitlines() if "←" in ln]) == 12   # default cap
        assert "older" in capped and "wl agent ls --all" in capped
        _, allout, _ = cli("agent", "ls", "--flat", "--all")
        assert len([ln for ln in allout.splitlines() if "←" in ln]) == 14 and "older" not in allout

    def test_ls_shrinks_sid_when_narrow(self, cli, monkeypatch):
        # rich view on a narrow terminal: the sid shrinks (…) so the title keeps room
        import worklog.render as render
        monkeypatch.setattr(render, "is_plain", lambda: False)
        monkeypatch.setenv("COLUMNS", "50")
        cli("add", "整合质检代码到主分支的一个相当长的任务标题", "-k", "task")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-0123456789-abcdef-long-uuid")
        cli("agent", "1")
        _, out, _ = cli("agent", "ls", "--flat")
        row = next(ln for ln in out.splitlines() if "←" in ln)
        assert "…" in row and "sess-0123456789-abcdef-long-uuid" not in row   # sid abbreviated

    def test_set_and_rm_invalidate_the_session_cache(self, cli, tmp_path, monkeypatch):
        state = tmp_path / "state"
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        self._sess(monkeypatch, "sess-inv")
        cli("add", "t", "-k", "task")
        cache = state / "worklog" / "agent" / "sess-inv"
        cache.parent.mkdir(parents=True)
        cache.write_text("stale")
        cli("agent", "1")                           # bind → invalidates the stale cache
        assert not cache.exists()
        cache.write_text("stale-again")
        cli("agent", "rm")                          # unbind → invalidates again
        assert not cache.exists()


class TestAgentLsEmpty:
    def test_agent_ls_no_bindings(self, cli):
        _, out, _ = cli("agent", "ls")
        assert "no session bindings" in out
