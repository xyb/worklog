"""prop and clock entity groups (filling prop-unset and clock-edit/rm).
prop: set/ls/rm (set = wl set, rm = wl unset). clock: ls/edit/rm (create via start/stop/spent)."""
import sqlite3
import pytest


class TestPropGroup:
    def test_set_shortcut_equals_prop_set(self, cli, tmp_db):
        cli("add", "t")
        cli("set", "1", "owner", "xyb")          # shortcut
        cli("prop", "set", "1", "estimate", "30m")  # group
        con = tmp_db.db_connect()
        props = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM prop WHERE node_id=1 AND deleted_at IS NULL")}
        assert props == {"owner": "xyb", "estimate": "30m"}

    def test_prop_ls(self, cli):
        cli("add", "t")
        cli("set", "1", "owner", "xyb")
        _, out, _ = cli("prop", "ls", "1")
        assert "owner=xyb" in out

    def test_namespaced_dotted_keys_supported(self, cli, tmp_db):
        """dotted `group.member` keys are first-class props: they round-trip and a `key LIKE
        'group.%'` prefix finds the whole namespace (the convention prop filters/stats rely on)."""
        cli("add", "t")
        cli("set", "1", "agent_session.claude", "sess-a")
        cli("set", "1", "agent_session.cursor", "sess-c")
        cli("set", "1", "ext.linear", "LUM-1")          # a different namespace, unaffected
        con = tmp_db.db_connect()
        ns = {r["key"]: r["value"] for r in con.execute(
            "SELECT key,value FROM prop WHERE node_id=1 AND key LIKE 'agent_session.%' "
            "AND deleted_at IS NULL")}
        assert ns == {"agent_session.claude": "sess-a", "agent_session.cursor": "sess-c"}
        _, out, _ = cli("prop", "ls", "1")
        assert "agent_session.claude=sess-a" in out and "ext.linear=LUM-1" in out

    def test_reserved_field_names_rejected_as_props(self, cli, tmp_db):
        """a core node field must NOT become a UDA prop (it would shadow the real
        column — e.g. a `status` prop next to the real status). Both `wl set` and `wl prop set`
        reject reserved names with a pointer to the right command; nothing is written."""
        cli("add", "t")
        # `kind` is intentionally NOT here: it's retired (no column to shadow), so a `kind` prop
        # is just a free UDA now, not a rejected reserved field (WL#901).
        for key, val in [("status", "LATER"), ("priority", "A"), ("tags", "work"),
                         ("title", "x"), ("parent", "3"), ("scheduled", "today"),
                         ("deadline", "2026-06-09")]:
            code, _, err = cli("set", "1", key, val)
            assert code != 0 and "reserved" in err, f"{key} not rejected"
            code, _, err = cli("prop", "set", "1", key, val)   # same guard via prop set
            assert code != 0 and "reserved" in err, f"prop set {key} not rejected"
        con = tmp_db.db_connect()
        rows = con.execute("SELECT key FROM prop WHERE node_id=1 AND deleted_at IS NULL").fetchall()
        assert rows == [], "a reserved key leaked into the prop table"

    def test_reserved_field_case_insensitive(self, cli):
        cli("add", "t")
        assert cli("set", "1", "STATUS", "LATER")[0] != 0
        assert cli("set", "1", "Priority", "A")[0] != 0

    def test_set_empty_key_rejected(self, cli):
        cli("add", "t")
        assert cli("set", "1", "", "v")[0] != 0       # empty key
        assert cli("set", "1", "   ", "v")[0] != 0     # whitespace-only key

    def test_reserved_prop_hint_unit(self):
        """The single source of truth: reserved → hint (case/space-insensitive); free key,
        empty, and None → None (so the guard never fires on a legitimate UDA prop)."""
        from worklog.queries import _reserved_prop_hint
        assert _reserved_prop_hint("status")
        assert _reserved_prop_hint("  STATUS ")        # lowercased + stripped before lookup
        assert _reserved_prop_hint("owner") is None    # ordinary UDA prop — allowed
        assert _reserved_prop_hint("") is None
        assert _reserved_prop_hint(None) is None

    def test_import_props_reject_reserved_key(self, cli):
        """The importer shares the same backstop (via _upsert_prop): a `props` block naming a
        reserved field is refused, not silently turned into a shadow prop."""
        import json, tempfile, os
        spec = {"add": [{"title": "child", "props": {"status": "LATER"}}]}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(spec, f); f.close()
        code, _, err = cli("import", f.name)
        os.unlink(f.name)
        assert code != 0 and ("reserved" in err or "shadow" in err)

    def test_prop_rm_and_unset_shortcut(self, cli, tmp_db):
        cli("add", "t")
        cli("set", "1", "a", "1")
        cli("set", "1", "b", "2")
        cli("prop", "rm", "1", "a")    # group form
        cli("unset", "1", "b")          # shortcut form
        con = tmp_db.db_connect()
        live = con.execute("SELECT COUNT(*) FROM prop WHERE node_id=1 AND deleted_at IS NULL").fetchone()[0]
        assert live == 0
        total = con.execute("SELECT COUNT(*) FROM prop WHERE node_id=1").fetchone()[0]
        assert total == 2  # soft-deleted, not removed

    def test_prop_rm_revivable(self, cli, tmp_db):
        # re-set a removed prop revives it with the new value (upsert)
        cli("add", "t")
        cli("set", "1", "k", "v1")
        cli("unset", "1", "k")
        cli("set", "1", "k", "v2")
        con = tmp_db.db_connect()
        rows = con.execute("SELECT value, deleted_at FROM prop WHERE node_id=1 AND key='k'").fetchall()
        assert len(rows) == 1 and rows[0]["value"] == "v2" and rows[0]["deleted_at"] is None

    def test_prop_rm_missing_is_notice(self, cli):
        cli("add", "t")
        _, out, _ = cli("unset", "1", "nope")
        assert "no prop" in out


class TestClockGroup:
    def _interval(self, cli):
        cli("add", "t")
        cli("spent", "1", "90m", "--at", "2026-06-06 10:00")

    def test_clock_ls(self, cli):
        self._interval(cli)
        _, out, _ = cli("clock", "ls", "1")
        assert "#C1" in out and "1h30m" in out

    def test_clock_edit_recomputes_duration(self, cli, tmp_db):
        self._interval(cli)
        cli("clock", "edit", "1", "--start", "2026-06-06 09:00", "--end", "2026-06-06 11:00")
        con = tmp_db.db_connect()
        r = con.execute("SELECT elapsed_sec FROM clock WHERE id=1").fetchone()
        assert r["elapsed_sec"] == 2 * 3600  # 2h recomputed

    def test_clock_rm_soft_deletes(self, cli, tmp_db):
        self._interval(cli)
        cli("clock", "rm", "1")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM clock WHERE deleted_at IS NULL").fetchone()[0] == 0
        # and the day's clock total no longer counts it
        _, out, _ = cli("clock", "ls", "1")
        assert "no clock intervals" in out

    def test_clock_edit_nothing_errors(self, cli):
        self._interval(cli)
        code, _, err = cli("clock", "edit", "1")
        assert code != 0 and "nothing to edit" in err

    def test_clock_rm_missing(self, cli):
        code, _, err = cli("clock", "rm", "99")
        assert code != 0 and "not found" in err

    def test_clock_edit_missing_interval(self, cli):
        code, _, err = cli("clock", "edit", "99", "--start", "2026-06-06 09:00")
        assert code != 0 and "not found" in err

    def test_clock_edit_bad_start_timestamp(self, cli):
        self._interval(cli)
        code, _, err = cli("clock", "edit", "1", "--start", "not-a-time")
        assert code != 0 and "--start" in err

    def test_clock_edit_bad_end_timestamp(self, cli):
        self._interval(cli)
        code, _, err = cli("clock", "edit", "1", "--end", "not-a-time")
        assert code != 0 and "--end" in err

    def test_clock_edit_clear_end_reopens(self, cli, tmp_db):
        self._interval(cli)
        cli("clock", "edit", "1", "--end", "")     # clear the end → back to running
        con = tmp_db.db_connect()
        r = con.execute("SELECT end_at, elapsed_sec FROM clock WHERE id=1").fetchone()
        assert r["end_at"] is None and r["elapsed_sec"] is None


class TestPropClockHelpCrossRefs:
    """The shortcut ↔ canonical cross-reference must appear in both helps (DESIGN §1.2).
    Introspect the parsers (argparse prints --help during parse_args, before capture)."""

    def _help(self, tmp_db, name):
        import argparse
        p = tmp_db.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        return sa.choices[name].format_help()

    def test_prop_group_lists_shortcuts(self, tmp_db):
        h = self._help(tmp_db, "prop")
        assert "wl set" in h and "wl unset" in h

    def test_set_help_names_canonical(self, tmp_db):
        assert "wl prop set" in self._help(tmp_db, "set")

    def test_unset_help_names_canonical(self, tmp_db):
        assert "wl prop rm" in self._help(tmp_db, "unset")

    def test_clock_help_names_create_helpers(self, tmp_db):
        h = self._help(tmp_db, "clock")
        assert "start" in h and "spent" in h


class TestPropClockReviewFixes:
    """Cross-model review (Kimi K2.5) findings."""

    def test_clock_edit_end_before_start_errors(self, cli):
        cli("add", "t")
        cli("spent", "1", "90m", "--at", "2026-06-06 10:00")
        code, _, err = cli("clock", "edit", "1", "--start", "2026-06-06 10:00", "--end", "2026-06-06 08:00")
        assert code != 0 and "before start" in err

    def test_clock_edit_running_start_only_no_elapsed_change(self, cli, tmp_db):
        cli("add", "t")
        cli("start", "1")  # running interval, elapsed NULL
        _, out, _ = cli("clock", "edit", "1", "--start", "2026-06-06 08:00")
        assert "elapsed_sec" not in out  # only start_at reported, not a spurious elapsed
        con = tmp_db.db_connect()
        assert con.execute("SELECT elapsed_sec FROM clock WHERE id=1").fetchone()[0] is None  # still running

    def test_unset_goalkey_routes_to_goal_rm(self, cli, tmp_db):
        # goal/summary are reserved-tag logs, not props — `wl unset <node> goal|summary`
        # is the key-routed shortcut for `wl goal rm` (clears the log), symmetric
        # with `wl set <node> goal|summary` → `wl goal set`.
        cli("add", "t")
        cli("set", "1", "goal", "deliver X")
        _, out, _ = cli("unset", "1", "goal")
        assert "goal cleared" in out
        con = tmp_db.db_connect()
        assert con.execute(
            "SELECT COUNT(*) FROM log WHERE node_id=1 AND tag='goal' AND deleted_at IS NULL"
        ).fetchone()[0] == 0


class TestPropGroupGuards:
    """prop group error/edge paths (ls missing/empty, rm missing/empty-key, no-subcommand)."""
    def test_prop_ls_missing_node(self, cli):
        code, _, err = cli("prop", "ls", "99")
        assert code != 0 and "not found" in err

    def test_prop_ls_empty(self, cli):
        cli("add", "task one")
        _, out, _ = cli("prop", "ls", "1")
        assert "no props" in out

    def test_prop_rm_missing_node(self, cli):
        code, _, err = cli("prop", "rm", "99", "owner")
        assert code != 0 and "not found" in err

    def test_prop_rm_empty_key(self, cli):
        cli("add", "task one")
        code, _, err = cli("prop", "rm", "1", "")
        assert code != 0 and "key cannot be empty" in err

    def test_prop_no_subcommand_shows_usage(self, cli):
        code, _, err = cli("prop")
        assert code != 0 and "usage" in err.lower()
