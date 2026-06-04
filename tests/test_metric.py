"""Tests for `wl metric` CRUD (node → log → metric structured datapoints)."""
import sqlite3
import pytest

from datetime import date


class TestMetricAdd:
    def _node(self, cli):
        cli("add", "blood sugar", "-k", "habit")  # node 1

    def test_add_numeric_creates_carrier_log_and_metric(self, cli, tmp_db):
        self._node(cli)
        code, out, _ = cli("metric", "add", "1", "glucose", "5.4", "--unit", "mmol/L")
        assert code == 0
        assert "#M1" in out and "glucose" in out and "5.4" in out
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["tag"] == "glucose" and m["value_num"] == 5.4 and m["unit"] == "mmol/L"
        assert m["value_text"] is None and m["node_id"] == 1
        # a carrier log was created and linked
        log = con.execute("SELECT * FROM log WHERE id = ?", (m["log_id"],)).fetchone()
        assert log["node_id"] == 1

    def test_add_marker_stores_value_num_1(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "checkin")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["tag"] == "checkin" and m["value_num"] == 1 and m["value_text"] is None

    def test_add_text_value(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "mood", "good", "--text")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["value_text"] == "good" and m["value_num"] is None

    def test_add_non_numeric_value_falls_back_to_text(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "note", "felt-dizzy")  # not numeric, no --text
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["value_text"] == "felt-dizzy" and m["value_num"] is None

    def test_add_with_at_date_sets_metric_and_carrier_log(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "weight", "70", "--at", "2026-06-01", "--note", "am")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["at"] == "2026-06-01" and m["note"] == "am"
        log = con.execute("SELECT logged_at FROM log WHERE id = ?", (m["log_id"],)).fetchone()
        assert log["logged_at"] == "2026-06-01"

    def test_add_at_keyword_resolved(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "weight", "70", "--at", "yesterday")
        con = tmp_db.db_connect()
        m = con.execute("SELECT at FROM metric").fetchone()
        assert m["at"] != "yesterday"  # resolved to a concrete date

    def test_add_at_with_time(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "glucose", "5", "--at", "2026-06-01 08:30")
        con = tmp_db.db_connect()
        assert con.execute("SELECT at FROM metric").fetchone()["at"] == "2026-06-01 08:30:00"

    def test_add_on_existing_log_no_new_log(self, cli, tmp_db):
        self._node(cli)
        cli("log", "1", "morning reading")  # log #1
        con = tmp_db.db_connect()
        before = con.execute("SELECT COUNT(*) FROM log").fetchone()[0]
        cli("metric", "add", "1", "glucose", "6.1", "--on-log", "1")
        con = tmp_db.db_connect()
        after = con.execute("SELECT COUNT(*) FROM log").fetchone()[0]
        assert after == before  # no carrier log created
        assert con.execute("SELECT log_id FROM metric").fetchone()["log_id"] == 1

    def test_add_on_log_wrong_node_rejected(self, cli):
        self._node(cli)
        cli("add", "other", "-k", "task")  # node 2
        cli("log", "2", "x")               # log on node 2
        code, _, err = cli("metric", "add", "1", "glucose", "6.1", "--on-log", "1")
        assert code != 0 and "belongs to node #2" in err

    def test_add_on_log_missing_rejected(self, cli):
        self._node(cli)
        code, _, err = cli("metric", "add", "1", "glucose", "6.1", "--on-log", "99")
        assert code != 0 and "not found" in err

    def test_add_body_sets_carrier_log_body(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "glucose", "5", "--body", "after lunch")
        con = tmp_db.db_connect()
        m = con.execute("SELECT log_id FROM metric").fetchone()
        assert con.execute("SELECT body FROM log WHERE id = ?", (m["log_id"],)).fetchone()["body"] == "after lunch"

    def test_add_bad_node_rejected(self, cli):
        code, _, err = cli("metric", "add", "99", "glucose", "5")
        assert code != 0 and "node #99 not found" in err

    def test_add_empty_tag_rejected(self, cli):
        self._node(cli)
        code, _, err = cli("metric", "add", "1", "  ")
        assert code != 0 and "tag cannot be empty" in err

    def test_add_bad_at_date_rejected(self, cli):
        self._node(cli)
        code, _, err = cli("metric", "add", "1", "glucose", "5", "--at", "notadate")
        assert code != 0 and "invalid --at date" in err

    def test_add_bad_at_time_rejected(self, cli):
        self._node(cli)
        code, _, err = cli("metric", "add", "1", "glucose", "5", "--at", "2026-06-01 99:99")
        assert code != 0 and "invalid --at time" in err


class TestMetricLs:
    def _seed(self, cli):
        cli("add", "h", "-k", "habit")  # node 1
        cli("metric", "add", "1", "glucose", "5.4", "--at", "2026-06-01")
        cli("metric", "add", "1", "glucose", "6.0", "--at", "2026-06-02")
        cli("metric", "add", "1", "weight", "70", "--at", "2026-06-02", "--note", "am")

    def test_ls_all(self, cli):
        self._seed(cli)
        code, out, _ = cli("metric", "ls", "1", "--all")
        assert code == 0
        assert out.count("#M") == 3 and "glucose" in out and "weight" in out
        assert "am" in out  # note shown

    def test_ls_tag_filter(self, cli):
        self._seed(cli)
        _, out, _ = cli("metric", "ls", "1", "--all", "--tag", "glucose")
        assert out.count("#M") == 2 and "weight" not in out

    def test_ls_date_range_explicit_window(self, cli):
        # both bounds given → exact window, independent of the run date
        self._seed(cli)
        _, out, _ = cli("metric", "ls", "1", "--since", "2026-06-02", "--until", "2026-06-02")
        assert out.count("#M") == 2  # the two on 06-02
        _, out2, _ = cli("metric", "ls", "1", "--since", "2026-06-01", "--until", "2026-06-01")
        assert out2.count("#M") == 1

    def test_ls_default_window_shows_today(self, cli):
        from datetime import date
        cli("add", "h", "-k", "habit")
        cli("metric", "add", "1", "glucose", "5.5")  # at = now (today)
        _, out, _ = cli("metric", "ls", "1")  # default window includes today
        assert "#M1" in out

    def test_ls_empty(self, cli):
        cli("add", "h", "-k", "habit")
        _, out, _ = cli("metric", "ls", "1", "--all")
        assert "no metrics" in out

    def test_ls_empty_with_tag_filter_shows_filter(self, cli):
        cli("add", "h", "-k", "habit")
        _, out, _ = cli("metric", "ls", "1", "--all", "--tag", "glucose")
        assert "tag=glucose" in out

    def test_ls_bad_node(self, cli):
        code, _, err = cli("metric", "ls", "99")
        assert code != 0 and "not found" in err


class TestMetricEdit:
    def _one(self, cli):
        cli("add", "h", "-k", "habit")
        cli("metric", "add", "1", "glucose", "5.4", "--unit", "mmol/L")

    def test_edit_value_autodetect(self, cli, tmp_db):
        self._one(cli)
        cli("metric", "edit", "1", "--value", "5.6")
        con = tmp_db.db_connect()
        assert con.execute("SELECT value_num FROM metric").fetchone()["value_num"] == 5.6

    def test_edit_num_clears_text(self, cli, tmp_db):
        self._one(cli)
        cli("metric", "edit", "1", "--text", "high")  # now text
        cli("metric", "edit", "1", "--num", "5.9")     # back to num, text cleared
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["value_num"] == 5.9 and m["value_text"] is None

    def test_edit_text_clears_num(self, cli, tmp_db):
        self._one(cli)
        cli("metric", "edit", "1", "--text", "high")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["value_text"] == "high" and m["value_num"] is None

    def test_edit_unit_note_tag(self, cli, tmp_db):
        self._one(cli)
        cli("metric", "edit", "1", "--unit", "mg/dL", "--note", "fasting", "--tag", "bg")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["unit"] == "mg/dL" and m["note"] == "fasting" and m["tag"] == "bg"

    def test_edit_clear_unit_and_note(self, cli, tmp_db):
        self._one(cli)
        cli("metric", "edit", "1", "--note", "x")
        cli("metric", "edit", "1", "--unit", "", "--note", "")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["unit"] is None and m["note"] is None

    def test_edit_at(self, cli, tmp_db):
        self._one(cli)
        cli("metric", "edit", "1", "--at", "2026-06-01")
        con = tmp_db.db_connect()
        assert con.execute("SELECT at FROM metric").fetchone()["at"] == "2026-06-01"

    def test_edit_mutually_exclusive(self, cli):
        self._one(cli)
        code, _, err = cli("metric", "edit", "1", "--num", "5", "--text", "x")
        assert code != 0 and "mutually exclusive" in err

    def test_edit_nothing(self, cli):
        self._one(cli)
        code, _, err = cli("metric", "edit", "1")
        assert code != 0 and "nothing to change" in err

    def test_edit_bad_id(self, cli):
        code, _, err = cli("metric", "edit", "99", "--num", "1")
        assert code != 0 and "not found" in err

    def test_edit_empty_tag_rejected(self, cli):
        self._one(cli)
        code, _, err = cli("metric", "edit", "1", "--tag", "  ")
        assert code != 0 and "tag cannot be empty" in err


class TestMetricRm:
    def _two(self, cli):
        cli("add", "h", "-k", "habit")
        cli("metric", "add", "1", "glucose", "5.4")
        cli("metric", "add", "1", "weight", "70")

    def test_rm_single(self, cli, tmp_db):
        self._two(cli)
        code, out, _ = cli("metric", "rm", "1")
        assert code == 0 and "deleted metric #M1" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM metric").fetchone()[0] == 1

    def test_rm_multiple(self, cli, tmp_db):
        self._two(cli)
        cli("metric", "rm", "1", "2")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM metric").fetchone()[0] == 0

    def test_rm_nonexistent_is_not_an_error(self, cli):
        self._two(cli)
        code, out, _ = cli("metric", "rm", "99")
        assert code == 0 and "not found" in out


class TestMetricReviewFixes:
    """Behaviors added after the cross-model code review of the CRUD."""

    def _node(self, cli):
        cli("add", "h", "-k", "habit")

    def test_add_marker_carrier_log_typed_metric(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "checkin")
        con = tmp_db.db_connect()
        log = con.execute("SELECT type FROM log").fetchone()
        assert log["type"] == "metric"

    def test_rm_removes_empty_carrier_log(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "checkin")  # auto carrier, empty body
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 1
        code, out, _ = cli("metric", "rm", "1")
        assert "carrier log" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 0

    def test_rm_keeps_user_log_when_on_log(self, cli, tmp_db):
        self._node(cli)
        cli("log", "1", "real note")  # user log #1, type NULL
        cli("metric", "add", "1", "glucose", "5.4", "--on-log", "1")
        cli("metric", "rm", "1")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 1  # user log survives

    def test_rm_keeps_carrier_with_body(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "glucose", "5.4", "--body", "after lunch")
        cli("metric", "rm", "1")
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 1  # non-empty carrier kept

    def test_on_log_inherits_log_time(self, cli, tmp_db):
        self._node(cli)
        cli("log", "1", "morning", "--date", "2026-06-01", "--time", "08:00")
        cli("metric", "add", "1", "glucose", "5.4", "--on-log", "1")  # no --at
        con = tmp_db.db_connect()
        assert con.execute("SELECT at FROM metric").fetchone()["at"] == "2026-06-01 08:00:00"

    def test_on_log_rejects_clock(self, cli, tmp_db):
        self._node(cli)
        cli("start", "1")  # creates a CLOCK_IN log (log #1 on a fresh node)
        con = tmp_db.db_connect()
        clock_id = con.execute("SELECT id FROM log WHERE body LIKE 'CLOCK_%'").fetchone()["id"]
        code, _, err = cli("metric", "add", "1", "glucose", "5.4", "--on-log", str(clock_id))
        assert code != 0 and "CLOCK" in err

    def test_inf_value_not_numeric(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "x", "inf")  # inf must not become value_num
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["value_num"] is None and m["value_text"] == "inf"
        # and it renders without crashing
        code, out, _ = cli("metric", "ls", "1", "--all")
        assert code == 0 and "inf" in out

    def test_empty_value_rejected(self, cli):
        self._node(cli)
        code, _, err = cli("metric", "add", "1", "x", "   ")
        assert code != 0 and "cannot be empty" in err

    def test_unit_dropped_on_text_value(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "mood", "good", "--text", "--unit", "scale")
        con = tmp_db.db_connect()
        assert con.execute("SELECT unit FROM metric").fetchone()["unit"] is None

    def test_edit_num_inf_rejected(self, cli):
        self._node(cli)
        cli("metric", "add", "1", "glucose", "5")
        code, _, err = cli("metric", "edit", "1", "--num", "inf")
        assert code != 0 and "finite" in err

    def test_edit_to_text_clears_unit(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "glucose", "5", "--unit", "mmol/L")
        cli("metric", "edit", "1", "--text", "high")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["unit"] is None and m["value_text"] == "high" and m["value_num"] is None

    def test_edit_text_with_unit_rejected(self, cli):
        self._node(cli)
        cli("metric", "add", "1", "glucose", "5")
        code, _, err = cli("metric", "edit", "1", "--text", "high", "--unit", "scale")
        assert code != 0 and "unit only applies" in err

    def test_edit_unit_on_text_metric_rejected(self, cli):
        self._node(cli)
        cli("metric", "add", "1", "mood", "good", "--text")
        code, _, err = cli("metric", "edit", "1", "--unit", "scale")
        assert code != 0 and "unit only applies" in err

    def test_edit_unit_allowed_after_switching_back_to_num(self, cli, tmp_db):
        self._node(cli)
        cli("metric", "add", "1", "glucose", "5", "--unit", "mmol/L")
        cli("metric", "edit", "1", "--text", "high")   # clears unit
        cli("metric", "edit", "1", "--num", "6")        # now numeric again
        cli("metric", "edit", "1", "--unit", "mg/dL")   # unit allowed on numeric
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["value_num"] == 6 and m["unit"] == "mg/dL"

    def test_unlog_reports_metric_count(self, cli):
        self._node(cli)
        cli("log", "1", "carrier")        # log #1
        cli("metric", "add", "1", "glucose", "5.4", "--on-log", "1")
        code, out, _ = cli("unlog", "1")
        assert "metric" in out  # "+ 1 metric(s)"


class TestMetricHelperParams:
    """--metric shortcut on `wl log` and `wl add` (one-step log + datapoint)."""

    def test_log_metric_attaches_to_the_log(self, cli, tmp_db):
        cli("add", "h", "-k", "habit")  # node 1
        cli("log", "1", "morning", "--metric", "glucose 5.4 mmol/L", "--metric", "mood good")
        con = tmp_db.db_connect()
        rows = con.execute("SELECT * FROM metric ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0]["tag"] == "glucose" and rows[0]["value_num"] == 5.4 and rows[0]["unit"] == "mmol/L"
        assert rows[1]["tag"] == "mood" and rows[1]["value_text"] == "good"
        # both hang off the same (single) log
        assert rows[0]["log_id"] == rows[1]["log_id"]
        assert con.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 1

    def test_log_metric_inherits_log_timestamp(self, cli, tmp_db):
        cli("add", "h", "-k", "habit")
        cli("log", "1", "backfilled", "--date", "2026-06-01", "--metric", "glucose 5.4")
        con = tmp_db.db_connect()
        m = con.execute("SELECT at FROM metric").fetchone()
        assert m["at"] == "2026-06-01"

    def test_log_metric_hint_in_output(self, cli):
        cli("add", "h", "-k", "habit")
        _, out, _ = cli("log", "1", "x", "--metric", "glucose 5")
        assert "1 metric(s)" in out

    def test_add_metric_without_log_creates_carrier(self, cli, tmp_db):
        cli("add", "weigh-in", "-k", "task", "--metric", "weight 70 kg")  # node 1
        con = tmp_db.db_connect()
        log = con.execute("SELECT type FROM log").fetchone()
        assert log["type"] == "metric"  # dedicated carrier
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["tag"] == "weight" and m["value_num"] == 70 and m["unit"] == "kg"

    def test_add_metric_reuses_log_carrier(self, cli, tmp_db):
        cli("add", "run", "-k", "task", "--log", "5k done", "--metric", "distance 5 km", "--metric", "checkin")
        con = tmp_db.db_connect()
        # only one log (the --log one), both metrics on it
        assert con.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 1
        rows = con.execute("SELECT * FROM metric ORDER BY id").fetchall()
        assert len(rows) == 2 and rows[0]["log_id"] == rows[1]["log_id"]
        assert rows[1]["tag"] == "checkin" and rows[1]["value_num"] == 1

    def test_add_metric_at_inherited(self, cli, tmp_db):
        cli("add", "t", "-k", "task", "--at", "2026-06-01 08:00", "--metric", "glucose 5.4")
        con = tmp_db.db_connect()
        assert con.execute("SELECT at FROM metric").fetchone()["at"] == "2026-06-01 08:00:00"

    def test_log_metric_empty_spec_errors(self, cli):
        cli("add", "h", "-k", "habit")
        code, _, err = cli("log", "1", "x", "--metric", "   ")
        assert code != 0 and "spec is empty" in err


class TestMetricDispatch:
    def test_metric_no_sub_errors(self, cli):
        code, _, err = cli("metric")
        assert code != 0 and "usage: wl metric" in err

    def test_metric_id_arg_parsing(self):
        from worklog.commands.metric import _metric_id_arg
        assert _metric_id_arg("#M7") == 7
        assert _metric_id_arg("M7") == 7
        assert _metric_id_arg("7") == 7
