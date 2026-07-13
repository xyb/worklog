"""Tests for `wl agent` — bind the current agent session to a node, stored as an
`agent_session.<agent>` prop. CRUD: wl agent <id> / wl agent / wl agent ls / wl agent rm.
The session id comes from $WL_SESSION_ID (preferred) or a runtime's own env var from the
AgentRuntime registry ($CLAUDE_CODE_SESSION_ID / $CURSOR_CONVERSATION_ID); the runtime name
from $WL_AGENT or the registry's env markers (e.g. $CURSOR_AGENT=1 → cursor)."""
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
    @pytest.fixture(autouse=True)
    def _isolate_cursor_env(self, monkeypatch):
        """Tests run inside Cursor set CURSOR_AGENT=1; isolate unless a test opts in."""
        monkeypatch.delenv("CURSOR_AGENT", raising=False)
        monkeypatch.delenv("CURSOR_CONVERSATION_ID", raising=False)

    def _sess(self, monkeypatch, sid="sess-aaa"):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
        monkeypatch.delenv("WL_SESSION_ID", raising=False)
        monkeypatch.delenv("WL_AGENT", raising=False)   # default agent = claude unless a test sets it

    def test_set_show_roundtrip(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t")            # #1
        code, out, _ = cli("agent", "1")          # default verb = set
        assert code == 0 and "#1" in out
        assert _bound_value(tmp_db, 1) == "sess-aaa"   # stored as agent_session.claude
        _, out, _ = cli("agent")                   # bare = show current
        assert "#1" in out and "sess-aaa"[:8] in out

    def test_rebind_moves_prop_off_old_node(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "a"); cli("add", "b")  # #1 #2
        cli("agent", "1")
        cli("agent", "2")                          # same session rebinds to #2
        assert _bound_value(tmp_db, 1) is None     # #1 cleared
        assert _bound_value(tmp_db, 2) == "sess-aaa"

    def test_ls_lists_all_bindings(self, cli, monkeypatch):
        cli("add", "a"); cli("add", "b")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2"); cli("agent", "2")
        _, out, _ = cli("agent", "ls")
        assert "#1" in out and "#2" in out and "claude:" in out

    def test_ls_columns_aligned_across_id_and_agent_widths(self, cli, monkeypatch):
        """ls pads #id and <agent>:<sid> so the ← / · columns line up despite differing widths."""
        self._sess(monkeypatch)
        for i in range(12):
            cli("add", f"t{i}")       # ids #1..#10 (1-digit) and #11/#12 (2-digit)
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
        cli("add", "task one")
        cli("agent", "1")
        code, out, _ = cli("--color", "always", "agent", "ls")
        assert code == 0 and "#1" in out

    def test_ls_interactive_shrinks_long_sid_on_narrow_width(self, cli, monkeypatch):
        # styled + tight width → the sid is uniformly shrunk with an ellipsis (the shrink branch)
        self._sess(monkeypatch, "a-very-long-session-id-that-will-not-fit-0987654321")
        cli("add", "a task with a long enough title to crowd the line")
        cli("agent", "1")
        code, out, _ = cli("--color", "always", "--width", "40", "agent", "ls")
        assert code == 0
        assert "…" in out          # sid truncated to fit the narrow width

    def test_conflict_warns_when_node_held_by_other_session(self, cli, monkeypatch):
        cli("add", "a")
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
        cli("add", "t")
        code, _, err = cli("agent", "1")
        assert code != 0 and "session id" in err
        # the hint names every registry runtime's env var (registry-derived, not hardcoded)
        assert "$CLAUDE_CODE_SESSION_ID" in err and "$CURSOR_CONVERSATION_ID" in err

    def test_cursor_conversation_id_used(self, cli, tmp_db, monkeypatch):
        monkeypatch.delenv("WL_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.setenv("CURSOR_CONVERSATION_ID", "cursor-sess-1")
        monkeypatch.setenv("CURSOR_AGENT", "1")
        cli("add", "t")
        cli("agent", "1")
        con = tmp_db.db_connect()
        row = con.execute(
            "SELECT key, value FROM prop WHERE node_id=1 AND deleted_at IS NULL"
        ).fetchone()
        assert row["key"] == "agent_session.cursor"
        assert row["value"] == "cursor-sess-1"

    def test_cursor_agent_default_when_cursor_agent_set(self, cli, tmp_db, monkeypatch):
        monkeypatch.setenv("CURSOR_CONVERSATION_ID", "cursor-sess-2")
        monkeypatch.setenv("CURSOR_AGENT", "1")
        monkeypatch.delenv("WL_AGENT", raising=False)
        cli("add", "t")
        cli("agent", "1", "--record")
        assert _agent_metrics(tmp_db, 1)[0]["value_text"] == "cursor"

    def test_wl_session_id_takes_priority(self, cli, tmp_db, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-id")
        monkeypatch.setenv("WL_SESSION_ID", "hook-id")
        cli("add", "t")
        cli("agent", "1")
        assert _bound_value(tmp_db, 1) == "hook-id"   # WL_SESSION_ID wins

    def test_rm_unbinds_current_session(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-rm")
        cli("add", "t")
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
        cli("add", "t")
        cli("agent", "1")                          # default → records (so auto-binds capture history)
        assert _bound_value(tmp_db, 1) == "sess-aaa"   # live pointer set
        assert len(_history_metrics(tmp_db, 1)) == 1    # and history recorded by default

    def test_no_record_skips_history(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t")
        cli("agent", "1", "--no-record")           # opt out → pointer only
        assert _bound_value(tmp_db, 1) == "sess-aaa"
        assert _history_metrics(tmp_db, 1) == []

    def test_later_bind_backfills_missing_history(self, cli, tmp_db, monkeypatch):
        # A pair bound without a history record (early auto-bind / --no-record) gets recorded on a
        # later bind — dedup is by the metric, not "is the prop already set".
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t")
        cli("agent", "1", "--no-record")           # bound, no history
        assert _history_metrics(tmp_db, 1) == []
        cli("agent", "1")                          # re-bind same pair → backfills history
        assert len(_history_metrics(tmp_db, 1)) == 1
        cli("agent", "1")                          # and again → no duplicate
        assert len(_history_metrics(tmp_db, 1)) == 1

    def test_record_writes_session_and_agent_metrics(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-full-1234-xyz")
        cli("add", "t")
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
        cli("add", "t")
        cli("agent", "1", "--record")
        assert _agent_metrics(tmp_db, 1)[0]["value_text"] == "codex"   # the agent value, not hardcoded claude
        con = tmp_db.db_connect()
        keys = [r["key"] for r in con.execute(
            "SELECT key FROM prop WHERE node_id=1 AND deleted_at IS NULL").fetchall()]
        assert "agent_session.codex" in keys                   # prop key carries the agent too

    def test_agent_flag_overrides_env(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-cursor-1")
        monkeypatch.setenv("WL_AGENT", "codex")
        cli("add", "t")
        cli("agent", "1", "--record", "--agent", "cursor")     # flag beats env
        assert _agent_metrics(tmp_db, 1)[0]["value_text"] == "cursor"

    def test_agent_name_normalized_lowercase(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-up-1")
        monkeypatch.setenv("WL_AGENT", "  Cursor  ")
        cli("add", "t")
        cli("agent", "1", "--record")
        assert _agent_metrics(tmp_db, 1)[0]["value_text"] == "cursor"   # trimmed + lowercased

    def test_record_rebind_same_pair_no_duplicate(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "t")
        cli("agent", "1", "--record")
        cli("agent", "1", "--record")              # same (node, session) again
        assert len(_history_metrics(tmp_db, 1)) == 1   # not duplicated

    def test_record_history_survives_rebind_to_other_node(self, cli, tmp_db, monkeypatch):
        self._sess(monkeypatch, "sess-aaa")
        cli("add", "a"); cli("add", "b")  # #1 #2
        cli("agent", "1", "--record")
        cli("agent", "2", "--record")              # live pointer moves to #2
        assert _bound_value(tmp_db, 1) is None      # prop cleared off #1
        assert len(_history_metrics(tmp_db, 1)) == 1   # but #1's history stays
        assert len(_history_metrics(tmp_db, 2)) == 1

    # --- `wl agent context` (machine line for integrations) + cache invalidation ---

    def test_context_outputs_machine_line(self, cli, monkeypatch):
        self._sess(monkeypatch, "sess-ctx")
        cli("add", "hello task")
        _, out, _ = cli("agent", "context")
        assert out.strip() == ""                    # not bound yet → empty
        cli("agent", "1")
        _, out, _ = cli("agent", "context")
        assert out.strip() == "1\thello task"       # <id>\t<title>

    def test_context_hook_emits_valid_json(self, cli, monkeypatch):
        import json as _json
        self._sess(monkeypatch, "sess-hook")
        cli("add", 'tricky "quote" task')
        _, out, _ = cli("agent", "context", "--hook")
        assert out.strip() == ""                     # unbound → empty
        cli("agent", "1")
        _, out, _ = cli("agent", "context", "--hook")
        payload = _json.loads(out)                   # valid JSON (title quotes escaped by wl)
        assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "WL#1" in ctx and 'tricky "quote" task' in ctx

    def test_context_hook_cursor_emits_session_start_json(self, cli, monkeypatch):
        import json as _json
        monkeypatch.setenv("WL_SESSION_ID", "cursor-sess-hook")
        cli("add", "cursor task")
        _, out, _ = cli("agent", "context", "--hook", "cursor")
        payload = _json.loads(out)
        assert payload["env"] == {"WL_SESSION_ID": "cursor-sess-hook", "WL_AGENT": "cursor"}
        assert "additional_context" not in payload
        cli("agent", "1")
        _, out, _ = cli("agent", "context", "--hook", "cursor")
        payload = _json.loads(out)
        assert payload["env"]["WL_SESSION_ID"] == "cursor-sess-hook"
        assert "WL#1" in payload["additional_context"]
        assert "cursor task" in payload["additional_context"]

    # --- AgentRuntime registry: adding a new tool = ONE registry entry ---

    def test_registry_derives_hook_choices(self):
        """`--hook` choices come from the registry, so a new runtime auto-registers its hook."""
        from worklog.commands.state import _AGENT_RUNTIMES, AGENT_HOOK_CHOICES
        assert AGENT_HOOK_CHOICES == tuple(rt.name for rt in _AGENT_RUNTIMES)
        assert "claude" in AGENT_HOOK_CHOICES and "cursor" in AGENT_HOOK_CHOICES

    def test_new_registry_entry_is_fully_wired(self, cli, tmp_db, monkeypatch):
        """A hypothetical new runtime appended to the registry works end-to-end with no other
        code change: env-marker detection, session-id resolution, prop key, and --hook JSON all
        derive from the entry. This pins the 'one entry to add a tool' contract."""
        import json as _json
        from worklog.commands import state as _st
        from worklog.commands import agent_runtime as _ar
        from worklog.commands.agent_runtime import AgentRuntime

        def _codex_hook(sid, binding_msg, rt):
            return {"env": {"WL_SESSION_ID": sid, "WL_AGENT": rt.name}}

        codex = AgentRuntime("codex", session_env="CODEX_SESSION_ID",
                             marker_env="CODEX_AGENT", marker_value="1",
                             hook_builder=_codex_hook)
        monkeypatch.setattr(_ar, "AGENT_RUNTIMES", _ar.AGENT_RUNTIMES + (codex,))
        monkeypatch.setattr(_st, "_AGENT_RUNTIMES", _ar.AGENT_RUNTIMES)
        for var in ("WL_SESSION_ID", "WL_AGENT", "CLAUDE_CODE_SESSION_ID",
                    "CURSOR_CONVERSATION_ID", "CURSOR_AGENT"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CODEX_SESSION_ID", "codex-sess-1")
        monkeypatch.setenv("CODEX_AGENT", "1")
        # detection + session-id + prop key all flow from the registry entry
        assert _st._current_agent() == "codex"
        assert _st._current_session_id() == "codex-sess-1"
        cli("add", "t")
        cli("agent", "1")
        con = tmp_db.db_connect()
        row = con.execute(
            "SELECT key, value FROM prop WHERE node_id=1 AND deleted_at IS NULL").fetchone()
        assert row["key"] == "agent_session.codex" and row["value"] == "codex-sess-1"
        # the entry's own hook payload renders via the generic hook_json path
        payload = _json.loads(codex.hook_json("codex-sess-1", "bound"))
        assert payload["env"]["WL_AGENT"] == "codex"

    def test_ls_default_groups_by_day(self, cli, monkeypatch):
        # default `wl agent ls` groups bindings into per-day sections (today / yesterday / date)
        from datetime import date
        cli("add", "a"); cli("add", "b")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2"); cli("agent", "2")
        _, out, _ = cli("agent", "ls")
        assert date.today().isoformat() in out and "today" in out
        assert any(ln.startswith("  #") for ln in out.splitlines())   # rows indented under the header

    def test_ls_flat_has_no_day_header(self, cli, monkeypatch):
        from datetime import date
        cli("add", "a")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        _, out, _ = cli("agent", "ls", "--flat")
        assert "today" not in out and date.today().isoformat() not in out
        assert "#1" in out
        # --no-group is an alias for --flat
        _, out2, _ = cli("agent", "ls", "--no-group")
        assert "today" not in out2

    def test_ls_plain_shows_full_session_id(self, cli, monkeypatch):
        # piped / plain (the test harness is non-TTY) → full session id, never abbreviated
        cli("add", "a")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-full-uuid-0123456789")
        cli("agent", "1")
        _, out, _ = cli("agent", "ls", "--flat")
        assert "sess-full-uuid-0123456789" in out and "…" not in out

    def test_ls_sorts_by_activity_most_recent_first(self, cli, tmp_db, monkeypatch):
        cli("add", "a"); cli("add", "b")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1"); cli("agent", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2"); cli("agent", "2")
        cli("log", "1", "work on a"); cli("log", "2", "work on b")   # real work — a bind alone isn't activity
        con = tmp_db.db_connect()   # backdate ALL of each node's logs to control latest-activity
        con.execute("UPDATE log SET logged_at='2026-06-05 10:00:00' WHERE node_id=1")
        con.execute("UPDATE log SET logged_at='2026-06-10 10:00:00' WHERE node_id=2")
        con.commit()
        _, out, _ = cli("agent", "ls", "--flat")
        rows = [ln for ln in out.splitlines() if "←" in ln]
        assert rows[0].lstrip().startswith("#2") and rows[1].lstrip().startswith("#1")

    def test_ls_by_bound_sorts_by_bind_time(self, cli, tmp_db, monkeypatch):
        cli("add", "a"); cli("add", "b")
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
            cli("add", f"t{i}")
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
        cli("add", "整合质检代码到主分支的一个相当长的任务标题")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-0123456789-abcdef-long-uuid")
        cli("agent", "1")
        _, out, _ = cli("agent", "ls", "--flat")
        row = next(ln for ln in out.splitlines() if "←" in ln)
        assert "…" in row and "sess-0123456789-abcdef-long-uuid" not in row   # sid abbreviated

    def test_set_and_rm_invalidate_the_session_cache(self, cli, tmp_path, monkeypatch):
        state = tmp_path / "state"
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        self._sess(monkeypatch, "sess-inv")
        cli("add", "t")
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


class TestAgentActivity:
    """`wl agent ls` derives an activity column from the node's latest log — how long ago it was
    worked (12d), plus 💤 when that exceeds --stale-days. Derived at read time, never stored."""

    def _sess(self, monkeypatch, sid="sess-act"):
        monkeypatch.setenv("WL_SESSION_ID", sid)
        monkeypatch.setenv("WL_AGENT", "claude")

    def test_fresh_binding_shows_age_and_no_badge(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "a")
        cli("agent", "1")
        cli("log", "1", "working on it")
        _, out, _ = cli("agent", "ls")
        assert "0m" in out and "💤" not in out      # just worked → age, no anomaly badge

    def test_stale_binding_gets_the_badge(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "a")
        cli("agent", "1")
        cli("log", "1", "old work", "-d", "-10d")
        _, out, _ = cli("agent", "ls")
        assert "💤" in out and "10d" in out

    def test_stale_days_threshold_is_tunable(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "a")
        cli("agent", "1")
        cli("log", "1", "two days ago", "-d", "-2d")
        assert "💤" not in cli("agent", "ls")[1]                      # default 3d → not yet stale
        assert "💤" in cli("agent", "ls", "--stale-days", "1")[1]     # 1d → stale

    def test_no_activity_flag_hides_the_column(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "a")
        cli("agent", "1")
        cli("log", "1", "old", "-d", "-10d")
        _, out, _ = cli("agent", "ls", "--no-activity")
        assert "💤" not in out and "10d" not in out and "#1" in out

    def test_activity_in_json(self, cli, monkeypatch):
        import json
        self._sess(monkeypatch)
        cli("add", "a")
        cli("agent", "1")
        cli("log", "1", "fresh")
        rows = json.loads(cli("agent", "ls", "-o", "json")[1])
        assert rows[0]["stale"] is False


class TestAgentGaps:
    """`wl agent gaps` — the reverse view: work that SHOULD have a session pushing it (a DOING P0,
    or a still-open target of today's goal) but has no binding. The risk list."""

    def _sess(self, monkeypatch, sid="sess-gap"):
        monkeypatch.setenv("WL_SESSION_ID", sid)
        monkeypatch.setenv("WL_AGENT", "claude")

    def test_unbound_doing_p0_is_a_gap(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "big thing", "-p", "A")
        cli("start", "1")                        # DOING = "I'm on this" — but nothing is
        _, out, _ = cli("agent", "gaps")
        assert "#1" in out and "big thing" in out

    def test_bound_doing_p0_is_not_a_gap(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "big thing", "-p", "A")
        cli("start", "1")
        cli("agent", "1")
        _, out, _ = cli("agent", "gaps")
        assert "#1" not in out

    def test_idle_p0_is_not_a_gap(self, cli, monkeypatch):
        # a standing P0 you are NOT claiming to work on: a ranking, not work in flight.
        # Counting these sweeps in nearly every open P0, i.e. an alert that always fires.
        self._sess(monkeypatch)
        cli("add", "big thing", "-p", "A")
        _, out, _ = cli("agent", "gaps")
        assert "#1" not in out

    def test_done_p0_is_not_a_gap(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "big thing", "-p", "A")
        cli("start", "1")
        cli("done", "1")
        _, out, _ = cli("agent", "gaps")
        assert "#1" not in out

    def test_recurring_doing_p0_is_not_a_gap(self, cli, monkeypatch):
        # a habit sits at DOING forever (wl tick never moves the status) — it is kept up by
        # checking in, not by a session, so it must not be flagged as unattended every day
        self._sess(monkeypatch)
        cli("add", "morning check", "-p", "A")
        cli("sched", "1", "--recur", "daily")
        cli("start", "1")
        _, out, _ = cli("agent", "gaps")
        assert "#1" not in out

    def test_low_priority_doing_is_not_a_gap(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "small thing", "-p", "C")
        cli("start", "1")
        _, out, _ = cli("agent", "gaps")
        assert "#1" not in out

    def test_todays_goal_target_is_a_gap_even_at_low_priority(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "small but today", "-p", "C")
        cli("goal", "ship it", "1")
        _, out, _ = cli("agent", "gaps")
        assert "#1" in out

    def test_settled_goal_target_is_not_a_gap(self, cli, monkeypatch):
        # a recurring target already ticked today needs no session — it's delivered
        self._sess(monkeypatch)
        cli("add", "daily habit")
        cli("sched", "1", "--recur", "daily")
        cli("goal", "keep it up", "1")
        cli("tick", "1")
        _, out, _ = cli("agent", "gaps")
        assert "#1" not in out

    def test_no_gaps_says_so(self, cli, monkeypatch):
        self._sess(monkeypatch)
        cli("add", "small thing", "-p", "C")
        _, out, _ = cli("agent", "gaps")
        assert "no gaps" in out.lower()

    def test_gaps_json(self, cli, monkeypatch):
        import json
        self._sess(monkeypatch)
        cli("add", "big thing", "-p", "A")
        cli("start", "1")
        rows = json.loads(cli("agent", "gaps", "-o", "json")[1])
        assert [r["id"] for r in rows] == [1] and rows[0]["reason"] == "DOING"
