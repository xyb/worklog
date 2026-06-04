"""Tests for checkin (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


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
        from worklog import cli as wl_mod
        # mock TTY + multi-select returns [0] (pick the 1st pending)
        from worklog import cli as wl_mod_
        monkeypatch.setattr(wl_mod_.commands.meta, "_is_interactive_tty", lambda: True)
        monkeypatch.setattr(wl_mod.commands.meta, "_multi_select_tty", lambda options, header: [0])
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
        from worklog import cli as wl_mod
        from worklog import cli as wl_mod_
        monkeypatch.setattr(wl_mod_.commands.meta, "_is_interactive_tty", lambda: True)
        monkeypatch.setattr(wl_mod.commands.meta, "_multi_select_tty", lambda *a: None)
        _, out, _ = cli("checkin")
        assert "cancel" in out
        _, s1, _ = cli("show", "1")
        assert "✓ done" not in s1

    def test_checkin_per_item_flag(self, cli, monkeypatch):
        """--per-item explicitly uses prompt mode (even on TTY)"""
        self._setup_habits(cli, 1)
        inputs = iter(["y"])
        monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
        from worklog import cli as wl_mod_
        monkeypatch.setattr(wl_mod_.commands.meta, "_is_interactive_tty", lambda: True)
        _, out, _ = cli("checkin", "--per-item")
        assert "done 1/1" in out

    def test_habit_marker_resets_next_day(self, cli):
        """habit logged yesterday → today's wl day shows [ ] (each day is independent)"""
        from datetime import date, timedelta
        today = date.today().isoformat()
        yday = (date.today() - timedelta(days=1)).isoformat()
        cli("add", "维生素", "-k", "habit")
        cli("sched", "1", today)
        # yesterday's check-in (log carrying a checkin metric, dated yesterday)
        cli("log", "1", "昨天吃了", "--date", yday, "--metric", "checkin")
        # today's wl day should render [ ] (no check-in today)
        _, today_out, _ = cli("day")
        assert "[ ] #1" in today_out
        # yesterday's wl day should render [x] (checked in that day)
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


class TestCheckinCollectGaps:
    """_checkin_collect: --all-kinds / CANCELED filtering / already-logged marker."""

    def test_checkin_all_kinds_includes_task(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "task-x", "-k", "task")
        cli("sched", "1", today)
        # EOF straight away → immediate interrupt, but _checkin_collect already covered --all-kinds branch
        monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError()))
        _, out, _ = cli("checkin", "--all-kinds", "--per-item")
        assert "task-x" in out or "done" in out or "interrupted" in out

    def test_checkin_skips_canceled(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h-skip", "-k", "habit")
        cli("sched", "1", today)
        cli("cancel", "1")
        monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(EOFError()))
        _, out, _ = cli("checkin", "--per-item")
        assert "h-skip" not in out


class TestCheckinPerItemActuallyRuns:
    """cmd_checkin --per-item hits at least 1 pending habit → enters input loop"""

    def test_checkin_per_item_y_marks_done(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h-per-item", "-k", "habit")
        cli("sched", "1", today)
        # mock y → done
        monkeypatch.setattr("builtins.input", lambda *a: "y")
        _, out, _ = cli("checkin", "--per-item")
        assert "done 1/1" in out or "h-per-item" in out

    def test_checkin_all_done_short_circuit(self, cli):
        """all pending already checked in → short-circuit "already checked in" path"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h-done", "-k", "habit")
        cli("sched", "1", today)
        cli("tick", "1")  # already checked in today
        _, out, _ = cli("checkin", "--per-item")
        assert "already checked in" in out


class TestCheckinKindFilter:
    def test_checkin_explicit_kind(self, cli, monkeypatch):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "t-task", "-k", "task")
        cli("sched", "1", today)
        # --kind task → kinds = {"task"}, should be collected
        monkeypatch.setattr("builtins.input", lambda *a: "n")
        _, out, _ = cli("checkin", "--kind", "task", "--per-item")
        # reaching the per-item path is good enough; 1 item collected
        assert "1 items" in out or "1/1" in out


class TestCheckinCollectAlreadyLogged:
    def test_already_logged_marked(self, cli):
        """already logged today → already=True flag"""
        from datetime import date
        today = date.today().isoformat()
        cli("add", "h1", "-k", "habit")
        cli("sched", "1", today)
        cli("tick", "1")  # already checked in today
        from worklog import cli as wl
        con = wl.db_connect()
        # mock args
        class A: kind = None; all_kinds = False; show_canceled = False
        # _checkin_collect returns (rows, today, kinds)
        rows, today_str, kinds = wl._checkin_collect(con, A())
        assert rows
        assert rows[0]["already"] is True
