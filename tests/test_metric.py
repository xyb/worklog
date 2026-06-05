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
        # --at local (+08:00) stored UTC: 08:30 local = 00:30 UTC
        assert con.execute("SELECT at FROM metric").fetchone()["at"] == "2026-06-01 00:30:00"

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
        log = con.execute("SELECT tag FROM log").fetchone()
        assert log["tag"] == "metric"

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
        # inherits the log's UTC logged_at (08:00 local = 00:00 UTC)
        assert con.execute("SELECT at FROM metric").fetchone()["at"] == "2026-06-01 00:00:00"

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
        log = con.execute("SELECT tag FROM log").fetchone()
        assert log["tag"] == "metric"  # dedicated carrier
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
        # --at local (+08:00) → metric inherits the UTC instant (08:00 local = 00:00 UTC)
        assert con.execute("SELECT at FROM metric").fetchone()["at"] == "2026-06-01 00:00:00"

    def test_log_metric_empty_spec_errors(self, cli):
        cli("add", "h", "-k", "habit")
        code, _, err = cli("log", "1", "x", "--metric", "   ")
        assert code != 0 and "spec is empty" in err


class TestCheckinViaMetric:
    """`wl tick`/`wl checkin` write a structured checkin metric; habit 'done today'
    detection reads it (not 'any log that day'), fixing the loose old heuristic."""

    def test_tick_writes_checkin_metric(self, cli, tmp_db):
        cli("add", "exercise", "-k", "habit")
        cli("tick", "1")
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric WHERE tag='checkin'").fetchone()
        assert m is not None and m["value_num"] == 1 and m["node_id"] == 1

    def test_tick_idempotent_same_day(self, cli, tmp_db):
        cli("add", "exercise", "-k", "habit")
        cli("tick", "1")
        cli("tick", "1")  # second tick same day
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM metric WHERE tag='checkin'").fetchone()[0] == 1

    def test_day_habit_done_only_with_checkin(self, cli):
        cli("add", "exercise", "-k", "habit")   # node 1
        cli("add", "vitamins", "-k", "habit")   # node 2
        cli("tick", "1")                          # checkin
        cli("log", "2", "just a note")              # a plain note, NOT a check-in
        _, out, _ = cli("day")
        assert "[x] #1" in out          # checked in → done
        assert "[x] #2" not in out      # stray note → NOT done (bug fixed)

    def test_checkin_backfill_migration_sql(self, cli, tmp_db):
        """0003 backfill: a legacy habit log (no checkin metric) gets one synthesized."""
        import pathlib
        cli("add", "exercise", "-k", "habit")
        cli("log", "1", "✓ done")  # plain log, no checkin auto-created
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM metric WHERE tag='checkin'").fetchone()[0] == 0
        # 0003 runs before the log.type→tag rename (0006); recreate its pre-0006 schema
        con.execute("ALTER TABLE log RENAME COLUMN tag TO type")
        # run the 0003 backfill body against this legacy-shaped data
        mig = pathlib.Path(tmp_db.__file__).resolve().parent / "migrations" / "0003_backfill_checkin_metrics.sql"
        con.executescript(mig.read_text())
        con.commit()
        m = con.execute("SELECT * FROM metric WHERE tag='checkin'").fetchone()
        assert m is not None and m["node_id"] == 1 and m["value_num"] == 1


class TestMetricImport:
    """wl import: a log entry may carry `metrics`; a node may carry node-level
    `metrics` (one carrier log → N datapoints, e.g. a CGM batch)."""

    def _import(self, cli, tmp_path, data):
        import json, pathlib
        p = pathlib.Path(tmp_path) / "imp.json"
        p.write_text(json.dumps(data))
        return cli("import", str(p))

    def test_import_log_with_metrics(self, cli, tmp_db, tmp_path):
        self._import(cli, tmp_path, {"add": [
            {"title": "bg", "kind": "habit", "logs": [
                {"body": "fasting", "date": "2026-06-01",
                 "metrics": [{"tag": "glucose", "value": 5.4, "unit": "mmol/L"}, {"tag": "checkin"}]}
            ]}
        ]})
        con = tmp_db.db_connect()
        rows = con.execute("SELECT * FROM metric ORDER BY id").fetchall()
        assert len(rows) == 2
        assert rows[0]["tag"] == "glucose" and rows[0]["value_num"] == 5.4 and rows[0]["unit"] == "mmol/L"
        assert rows[1]["tag"] == "checkin" and rows[1]["value_num"] == 1
        # both inherit the log's date and hang off the same log
        assert rows[0]["at"] == "2026-06-01" and rows[0]["log_id"] == rows[1]["log_id"]

    def test_import_node_level_metrics_single_carrier(self, cli, tmp_db, tmp_path):
        self._import(cli, tmp_path, {"add": [
            {"title": "cgm", "kind": "habit", "metrics": [
                {"tag": "glucose", "value": 5.1, "at": "2026-06-01 00:05"},
                {"tag": "glucose", "value": 5.3, "at": "2026-06-01 00:10"},
                {"tag": "glucose", "value": 6.0, "at": "2026-06-01 00:15"},
            ]}
        ]})
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM metric").fetchone()[0] == 3
        # one carrier log for the batch
        assert con.execute("SELECT COUNT(DISTINCT log_id) FROM metric").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM log WHERE node_id = 1").fetchone()[0] == 1

    def test_import_text_value(self, cli, tmp_db, tmp_path):
        self._import(cli, tmp_path, {"add": [
            {"title": "mood", "kind": "habit", "metrics": [{"tag": "mood", "value": "good"}]}
        ]})
        con = tmp_db.db_connect()
        m = con.execute("SELECT * FROM metric").fetchone()
        assert m["value_text"] == "good" and m["value_num"] is None

    def test_import_metric_missing_tag_rejected(self, cli, tmp_path):
        code, _, err = self._import(cli, tmp_path, {"add": [
            {"title": "x", "kind": "task", "metrics": [{"value": 5}]}
        ]})
        assert code != 0 and "tag" in err


class TestMetaTypedLogs:
    """goal / summary / overview / top5 are history-preserving typed logs, not props."""

    def test_goal_edit_keeps_history(self, cli, tmp_db):
        cli("goal", "deliver X today")
        cli("goal", "change to Y")
        _, out, _ = cli("goal")
        assert "change to Y" in out and "X" not in out  # reads latest
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM log WHERE tag='goal'").fetchone()[0] == 2  # history kept

    def test_set_meta_key_writes_typed_log_not_prop(self, cli, tmp_db):
        cli("add", "2026-W23", "-k", "week")  # node 1
        _, out, _ = cli("set", "1", "overview", "this week's focus")
        assert "logged" in out
        con = tmp_db.db_connect()
        assert con.execute("SELECT COUNT(*) FROM log WHERE node_id=1 AND tag='overview'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM prop WHERE node_id=1 AND key='overview'").fetchone()[0] == 0

    def test_set_non_meta_key_still_prop(self, cli, tmp_db):
        cli("add", "t", "-k", "task")
        cli("set", "1", "owner", "xyb")
        con = tmp_db.db_connect()
        assert con.execute("SELECT value FROM prop WHERE node_id=1 AND key='owner'").fetchone()["value"] == "xyb"

    def test_meta_prop_migration_sql(self, cli, tmp_db):
        """0004: a legacy goal/summary prop is converted to a typed log + the prop dropped."""
        import pathlib
        cli("add", "2026-06-01", "-k", "day")  # node 1
        con = tmp_db.db_connect()
        # seed legacy props (as the pre-0004 world stored them)
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (1, 'goal', 'legacy goal')")
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (1, 'summary', 'legacy recap')")
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (1, 'summary_at', '2026-06-01 18:00:00')")
        con.commit()
        # 0004 runs before the log.type→tag rename (0006); recreate its pre-0006 schema
        con.execute("ALTER TABLE log RENAME COLUMN tag TO type")
        mig = pathlib.Path(tmp_db.__file__).resolve().parent / "migrations" / "0004_meta_props_to_typed_logs.sql"
        con.executescript(mig.read_text())
        con.commit()
        # props gone, typed logs created
        assert con.execute("SELECT COUNT(*) FROM prop WHERE key IN ('goal','summary','summary_at')").fetchone()[0] == 0
        g = con.execute("SELECT body FROM log WHERE node_id=1 AND type='goal'").fetchone()
        s = con.execute("SELECT body, logged_at FROM log WHERE node_id=1 AND type='summary'").fetchone()
        assert g["body"] == "legacy goal"
        assert s["body"] == "legacy recap" and s["logged_at"] == "2026-06-01 18:00:00"  # summary_at preserved


class TestMetricShowFolding:
    """wl show timeline folds a log's metrics beneath it; empty metric carriers
    render as a 📊 line (not a blank ✎ log); over-count is elided."""

    def test_metrics_folded_under_log(self, cli):
        cli("add", "bg", "-k", "habit")
        cli("log", "1", "morning", "--metric", "glucose 5.4 mmol/L", "--metric", "mood good")
        _, out, _ = cli("show", "1")
        assert "✎ log" in out and "morning" in out
        assert "↳ [glucose] 5.4 mmol/L" in out
        assert "↳ [mood] good" in out

    def test_empty_carrier_shows_as_metric_line(self, cli):
        cli("add", "bg", "-k", "habit")
        cli("metric", "add", "1", "weight", "70", "--unit", "kg")  # empty carrier
        _, out, _ = cli("show", "1")
        assert "📊 metric" in out and "[weight] 70 kg" in out
        # no blank "✎ log" line for the carrier (only the metric line)
        assert "✎ log" not in out

    def test_over_count_elision(self, cli):
        cli("add", "cgm", "-k", "habit")
        cli("log", "1", "batch")  # log #1
        # attach 8 metrics to the one log
        for i in range(8):
            cli("metric", "add", "1", "glucose", str(5 + i * 0.1), "--on-log", "1")
        _, out, _ = cli("show", "1")
        assert "↳ … 3 more datapoints" in out  # 8 - 5 shown = 3 elided


class TestMetricDayFolding:
    """wl day / wl tree day-expansion fold a node's that-day metrics under it,
    excluding the checkin marker (reflected by [x])."""

    def test_day_folds_metrics(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "bg", "-k", "habit")
        cli("sched", "1", today)
        cli("tick", "1")  # checkin
        cli("log", "1", "morning", "--metric", "glucose 5.4 mmol/L", "--metric", "weight 70 kg")
        _, out, _ = cli("day")
        assert "↳ [glucose] 5.4 mmol/L" in out
        assert "↳ [weight] 70 kg" in out
        assert "[checkin]" not in out  # checkin not repeated as a datapoint line

    def test_day_metric_elision(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "cgm", "-k", "habit")
        cli("sched", "1", today)
        cli("log", "1", "batch")  # log #1 today
        for i in range(8):
            cli("metric", "add", "1", "glucose", str(5 + i), "--on-log", "1")
        _, out, _ = cli("day")
        assert "↳ … 3 more datapoints" in out

    def test_tree_day_expansion_folds_metrics(self, cli):
        from datetime import date
        today = date.today().isoformat()
        cli("add", "Lifetime", "-k", "lifetime")          # 1
        cli("add", today, "-k", "day", "--parent", "1")   # 2
        cli("add", "bg", "-k", "habit")                    # 3
        cli("log", "3", "reading", "--metric", "glucose 6.1 mmol/L")
        _, out, _ = cli("tree", "--root", "2", "--depth", "2")
        assert "↳ [glucose] 6.1 mmol/L" in out


class TestHabitMonthProgress:
    """habit lines in wl day / wl tree show month-to-date completion (this month N/M)."""

    def test_day_shows_habit_month_rate(self, cli):
        from datetime import date, timedelta
        today = date.today()
        cli("add", "exercise", "-k", "habit")
        cli("sched", "1", "--recur", "daily")
        cli("tick", "1")  # today
        # a past day this month: 2 days ago (guard against month boundary by using day-before-yesterday only if same month)
        dby = today - timedelta(days=2)
        if dby.month == today.month:
            cli("log", "1", "past", "--date", dby.isoformat(), "--metric", "checkin")
            expected_done = 2
        else:
            expected_done = 1
        _, out, _ = cli("day")
        assert "this month" in out and f"{expected_done}/" in out

    def test_no_schedule_no_rate(self, cli):
        # a habit with no schedule shows no (this month N/M)
        cli("add", "ad-hoc habit", "-k", "habit")
        cli("tick", "1")
        _, out, _ = cli("day")
        # ticked today's day node exists; the habit line has no this-month rate
        assert "this month" not in out


class TestMetricDispatch:
    def test_metric_no_sub_errors(self, cli):
        code, _, err = cli("metric")
        assert code != 0 and "usage: wl metric" in err

    def test_metric_id_arg_parsing(self):
        from worklog.commands.metric import _metric_id_arg
        assert _metric_id_arg("#M7") == 7
        assert _metric_id_arg("M7") == 7
        assert _metric_id_arg("7") == 7
