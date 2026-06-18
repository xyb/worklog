"""Tests for show (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestShow:
    def test_show_full_node(self, cli, tmp_db):
        cli("add", "strategy pivot", "-p", "A", "-t", "work,P0")
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

    def test_show_timeline_marks_log_tag(self, cli):
        # a tagged log (reserved-tag goal/summary or custom) shows its tag in the timeline,
        # distinguishable from a plain untagged "✎ log"
        import json
        cli("goal", "today's aim")   # writes a tag=goal log on today's day node
        day = json.loads(cli("day", "-o", "json")[1])["node_id"]
        _, out, _ = cli("show", str(day))
        assert "✎ goal" in out
        cli("add", "t")
        cli("log", "2", "plain note", "--keep-status")
        _, out2, _ = cli("show", "2")
        assert "✎ log" in out2       # untagged log still reads "✎ log"

    def test_show_timeline_log_line_fits_width(self, cli, monkeypatch):
        # a long log body in the timeline must truncate to the terminal width (budget against the
        # real prefix `    <ts>  #L<id>  ✎ log  `, not a fixed guess), not overflow to a 2nd line.
        monkeypatch.setenv("COLUMNS", "80")
        cli("add", "t")
        cli("log", "1", "x" * 200)
        _, out, _ = cli("show", "1")
        assert any("✎ log" in l for l in out.splitlines())          # the log row is present
        assert all(len(l) <= 80 for l in out.splitlines())          # …and no line overflows

    def test_show_timeline_fits_narrow_terminal(self, cli, monkeypatch):
        # regression (cross-model review): _truncate_log_body's old max(20,…) floor forced the
        # timeline log line to overflow on any terminal < ~61 cols (prefix ~39 + 20). Now floored
        # at 1, so it fits even narrow widths (body degrades to … when there's no room).
        monkeypatch.setenv("COLUMNS", "55")
        cli("add", "t")
        cli("log", "1", "x" * 200)
        _, out, _ = cli("show", "1")
        assert all(len(l) <= 55 for l in out.splitlines())

    def test_show_nonexistent_fails(self, cli):
        code, _, err = cli("show", "99")
        assert code != 0

    def test_show_upstream_path(self, cli):
        cli("add", "month", "--prop", "type.date=month")
        cli("add", "day", "--prop", "type.date=day", "--parent", "1")
        cli("add", "task", "--parent", "2")
        code, out, _ = cli("show", "3")
        assert "ancestors" in out and "month" in out and "day" in out

    def test_show_subtasks(self, cli):
        cli("add", "parent")
        cli("add", "child1", "--parent", "1")
        cli("add", "child2", "--parent", "1")
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
        assert "⏱ clock" in out  # structured clock interval (start→end)
        assert "✓ DONE" in out


# ─── ls ───


class TestShowSchedule:
    """wl show surfaces the sched table (one-off dates + recurring rules)."""

    def test_show_displays_oneoff_and_recur(self, cli):
        cli("add", "patrol", "--prop", "type.habit=true")
        cli("sched", "1", "2026-06-15")
        cli("sched", "1", "--recur", "daily")
        _, out, _ = cli("show", "1")
        assert "schedule:" in out
        assert "daily" in out
        assert "2026-06-15" in out

    def test_show_no_schedule_section_when_unscheduled(self, cli):
        cli("add", "unscheduled")
        _, out, _ = cli("show", "1")
        assert "schedule:" not in out

    def test_next_sched_fire_computes_next_occurrence(self):
        # the recur line's "(next …)" reuses _sched_fires, so it matches when wl day reappears it
        from datetime import date
        from worklog.commands.query import _next_sched_fire
        assert _next_sched_fire(["weekly:Mon"], date(2026, 6, 6)) == "2026-06-08"   # Sat → next Mon
        assert _next_sched_fire(["daily"], date(2026, 6, 6)) == "2026-06-06"        # daily incl. today
        assert _next_sched_fire(["weekly:Fri", "weekly:Mon"], date(2026, 6, 6)) == "2026-06-08"  # earliest

    def test_show_recur_line_includes_next(self, cli):
        cli("add", "standup", "--prop", "type.habit=true")
        cli("sched", "1", "--recur", "daily")
        _, out, _ = cli("show", "1")
        assert "recur daily (next " in out   # next-occurrence annotation present on the recur rule

    def test_show_dedups_duplicate_oneoff_rows(self, cli):
        # pre-idempotency-fix data can hold two identical (node_id, on_date) rows; show lists once
        import os, sqlite3
        cli("add", "patrol", "--prop", "type.habit=true")
        cli("sched", "1", "2026-06-02")
        con = sqlite3.connect(os.environ["WORKLOG_DB"])   # inject a dirty duplicate row directly
        con.execute("INSERT INTO sched (node_id, on_date, created_at) VALUES (1, '2026-06-02', '2026-06-02 00:00:00')")
        con.commit(); con.close()
        _, out, _ = cli("show", "1")
        sched_line = next(l for l in out.splitlines() if "schedule:" in l)
        assert sched_line.count("2026-06-02") == 1   # deduped at display, not shown twice


class TestShowJson:
    """`wl show -o json` — machine-readable full node + relations."""

    def test_json_is_valid_object_with_core_fields(self, cli):
        cli("add", "json task", "-p", "A", "-t", "work,dev")
        code, out, _ = cli("show", "1", "-o", "json")
        import json
        d = json.loads(out)
        assert code == 0 and isinstance(d, dict)
        # classification is the orthogonal `type` facet (a bare task → {}).
        assert d["id"] == 1 and d["title"] == "json task"
        assert "kind" not in d and d["type"] == {}
        assert d["priority"] == "A"
        assert set(d["tags"]) == {"work", "dev"}

    def test_json_includes_relations(self, cli):
        cli("add", "proj", "--para", "project")               # 1
        cli("add", "child", "--parent", "1") # 2
        cli("set", "1", "owner", "xyb")
        cli("link", "1", "Design doc")
        cli("log", "1", "did work", "--metric", "pullups 8")
        _, out, _ = cli("show", "1", "-o", "json")
        import json
        d = json.loads(out)
        # creating with --para project writes the type.para role into the prop namespace
        assert d["props"] == {"owner": "xyb", "type.para": "project"}
        assert d["links"] == ["Design doc"]
        assert [c["id"] for c in d["children"]] == [2]
        assert d["logs"] and d["logs"][0]["body"] == "did work"
        assert d["metrics"] and d["metrics"][0]["tag"] == "pullups"

    def test_json_single_object_multi_array(self, cli):
        cli("add", "a"); cli("add", "b")
        import json
        _, one, _ = cli("show", "1", "-o", "json")
        assert isinstance(json.loads(one), dict)
        _, many, _ = cli("show", "1", "2", "-o", "json")
        arr = json.loads(many)
        assert isinstance(arr, list) and [n["id"] for n in arr] == [1, 2]

    def test_json_missing_id_errors(self, cli):
        code, _, err = cli("show", "999", "-o", "json")
        assert code != 0 and "not found" in err

    def test_text_default_unchanged(self, cli):
        cli("add", "plain")
        _, out, _ = cli("show", "1")
        assert "#1" in out and "plain" in out
        assert not out.lstrip().startswith("{")   # default is rich text, not json


class TestShowMultiId:
    """show several ids (from test_ux)"""
    def test_show_multiple_ids(self, cli):
        cli("add", "t1")
        cli("add", "t2")
        _, out, _ = cli("show", "1", "2")
        assert "#1" in out and "t1" in out
        assert "#2" in out and "t2" in out



class TestShowMultilineBody:
    """#794: multi-line node.body — continuation lines indent under the value, not column 0."""

    def test_body_continuation_lines_indented(self, cli):
        cli("add", "task", "--body", "first line\nsecond line")
        code, out, _ = cli("show", "1")
        assert code == 0 and "body:" in out and "first line" in out
        lines = out.split("\n")
        cont = next(l for l in lines if l.strip() == "second line")
        assert cont != "second line"                      # not flush-left at column 0
        assert len(cont) - len(cont.lstrip()) >= 12       # aligned under the body value
