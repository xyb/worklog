"""Tests for -o json output across all commands that support it.

Covers commands added in the global -o json feature (#921/#719):
find, focus, ancestors, descendants, agenda, changes, goal (read+ls),
prop ls, log ls, log show, link ls, tag ls, sched ls, metric ls,
add (write), log (write), done, cancel, reopen, wait, defer, start, stop.
"""
import dataclasses
import json
import pathlib
import pytest

from worklog.commands.alias import AliasLsResult
from worklog.commands.sched import SchedLsResult
from worklog.commands.metric import MetricLsResult


def _j(cli, *args):
    """Run CLI with given args, assert exit 0, parse and return JSON."""
    code, out, err = cli(*args)
    assert code == 0, f"exit {code}: {err}"
    return json.loads(out)


class TestFindJson:
    def test_returns_list_with_matched_fields(self, cli):
        cli("add", "keyword task", "-t", "work")
        rows = _j(cli, "find", "keyword", "-o", "json")
        assert isinstance(rows, list) and len(rows) >= 1
        r = rows[0]
        assert r["id"] == 1 and r["title"] == "keyword task"
        assert "matched_fields" in r and "title" in r["matched_fields"]
        assert "type" in r and "status" in r

    def test_prefix_syntax(self, cli):
        cli("add", "pfx task")
        rows = _j(cli, "-o", "json", "find", "pfx")
        assert rows[0]["title"] == "pfx task"

    def test_no_header_in_output(self, cli):
        cli("add", "hdr task")
        code, out, _ = cli("find", "hdr", "-o", "json")
        assert out.strip().startswith("[")

    def test_empty_returns_empty_list(self, cli):
        rows = _j(cli, "find", "zzznomatch", "-o", "json")
        assert rows == []


class TestJSONLIntegration:
    """-o jsonl: same data as -o json, but a top-level array streams one
    compact object per line (for jq -c / streaming pipelines); other payloads
    stay a single line."""

    def _lines(self, cli, *args):
        code, out, err = cli(*args)
        assert code == 0, f"exit {code}: {err}"
        return [json.loads(x) for x in out.splitlines() if x.strip()]

    def test_list_command_streams_lines(self, cli):
        cli("add", "jl one", "-t", "work")
        cli("add", "jl two", "-t", "work")
        code, out, _ = cli("find", "jl", "-o", "jsonl")
        assert not out.lstrip().startswith("[")  # not a JSON array
        rows = [json.loads(x) for x in out.splitlines() if x.strip()]
        assert {r["title"] for r in rows} >= {"jl one", "jl two"}

    def test_prefix_syntax(self, cli):
        cli("add", "jlpfx")
        rows = self._lines(cli, "-o", "jsonl", "find", "jlpfx")
        assert rows[0]["title"] == "jlpfx"

    def test_empty_emits_no_lines(self, cli):
        code, out, _ = cli("find", "zzznomatch", "-o", "jsonl")
        assert code == 0 and out.strip() == ""

    def test_dict_payload_single_line(self, cli):
        cli("add", "jl show")
        code, out, _ = cli("show", "1", "-o", "jsonl")
        assert out.count("\n") == 1
        assert json.loads(out)["id"] == 1

    def test_error_is_rfc9457_on_stderr(self, cli):
        code, out, err = cli("show", "99999", "-o", "jsonl")
        assert code != 0
        assert json.loads(err)["status"] == 404 or json.loads(err)["title"] == "Error"


class TestTOONIntegration:
    """-o toon: compact Token-Oriented Object Notation. Same data as -o json,
    round-trippable, ~40% fewer tokens. Encoder unit-tested in test_toon.py;
    here we only check the CLI wiring end-to-end."""

    def test_uniform_list_is_tabular(self, cli):
        cli("add", "t1", "-t", "work")
        cli("add", "t2", "-t", "work")
        code, out, err = cli("tags", "-o", "toon")
        assert code == 0, err
        # tags → [{tag,count}] → uniform primitive → tabular header + rows
        assert out.splitlines()[0].startswith("[") and "{tag,count}:" in out.splitlines()[0]

    def test_object_command(self, cli):
        cli("add", "solo")
        code, out, _ = cli("show", "1", "-o", "toon")
        assert code == 0
        assert out.splitlines()[0] == "id: 1"

    def test_empty_is_empty_array_token(self, cli):
        code, out, _ = cli("find", "zzznomatch", "-o", "toon")
        assert code == 0 and out.strip() == "[]"

    def test_error_is_rfc9457_on_stderr(self, cli):
        code, out, err = cli("show", "99999", "-o", "toon")
        assert code != 0 and json.loads(err)["title"] == "Error"

    def test_accepted_prefix_and_suffix(self, cli):
        cli("add", "px")
        assert cli("-o", "toon", "find", "px")[0] == 0
        assert cli("find", "px", "-o", "toon")[0] == 0


class TestTypeScalar:
    """`type` is the node's single representative token string everywhere (aligned
    with `find`/`focus`), not the orthogonal facet object — so uniform list/child
    rows stay primitive and render as a TOON table."""

    def test_bare_task_type_is_scalar(self, cli):
        cli("add", "plain")
        assert _j(cli, "show", "1", "-o", "json")["type"] == "task"

    def test_project_type_is_scalar(self, cli):
        cli("add", "proj", "--para", "project")
        assert _j(cli, "show", "1", "-o", "json")["type"] == "project"

    def test_children_scalar_and_toon_tabular(self, cli):
        cli("add", "parent", "--para", "project")
        cli("add", "c1", "--parent", "1")
        cli("add", "c2", "--parent", "1")
        d = _j(cli, "show", "1", "-o", "json")
        assert [c["type"] for c in d["children"]] == ["task", "task"]
        code, out, _ = cli("show", "1", "-o", "toon")
        assert "children[2]{id,title,type,status,priority}:" in out  # uniform → tabular


class TestFocusJson:
    def test_structure(self, cli):
        cli("add", "proj", "--para", "project")
        cli("add", "child", "--parent", "1")
        d = _j(cli, "focus", "1", "-o", "json")
        assert "node" in d and "upstream" in d and "downstream" in d
        assert d["node"]["id"] == 1
        assert d["downstream"][0]["id"] == 2

    def test_prefix_syntax(self, cli):
        cli("add", "top")
        d = _j(cli, "-o", "json", "focus", "1")
        assert d["node"]["id"] == 1


class TestAncestorsJson:
    def test_returns_list(self, cli):
        _, out, _ = cli("add", "root", "-o", "json")
        import json as _json
        root_id = _json.loads(out)["id"]
        _, out2, _ = cli("add", "child", "--parent", str(root_id), "-o", "json")
        child_id = _json.loads(out2)["id"]
        rows = _j(cli, "ancestors", str(child_id), "-o", "json")
        assert isinstance(rows, list)
        assert any(r["id"] == root_id for r in rows)

    def test_each_row_has_required_fields(self, cli):
        _, out, _ = cli("add", "root", "-o", "json")
        import json as _json
        root_id = _json.loads(out)["id"]
        _, out2, _ = cli("add", "child", "--parent", str(root_id), "-o", "json")
        child_id = _json.loads(out2)["id"]
        rows = _j(cli, "ancestors", str(child_id), "-o", "json")
        for r in rows:
            assert {"id", "title", "type", "status", "priority"} <= r.keys()


class TestDescendantsJson:
    def test_flat_list_of_all_descendants(self, cli):
        cli("add", "root")
        cli("add", "child1", "--parent", "1")
        cli("add", "child2", "--parent", "1")
        cli("add", "grandchild", "--parent", "2")
        rows = _j(cli, "descendants", "1", "-o", "json")
        ids = {r["id"] for r in rows}
        assert {2, 3, 4} == ids

    def test_empty_node_returns_empty(self, cli):
        cli("add", "leaf")
        rows = _j(cli, "descendants", "1", "-o", "json")
        assert rows == []


class TestAgendaJson:
    def test_structure(self, cli):
        cli("add", "sched task")
        cli("sched", "1", "2026-07-01")
        d = _j(cli, "agenda", "2026-07-01", "2026-07-01", "-o", "json")
        assert "range" in d and "items" in d and "someday" in d
        assert d["range"]["start"] == "2026-07-01"

    def test_empty_range(self, cli):
        d = _j(cli, "agenda", "2026-01-01", "2026-01-01", "-o", "json")
        assert d["items"] == []

    def test_item_in_range(self, cli):
        cli("add", "agenda task")
        cli("sched", "1", "2026-07-15")
        d = _j(cli, "agenda", "2026-07-01", "2026-07-31", "-o", "json")
        assert any(item["id"] == 1 for item in d["items"])


class TestChangesJson:
    def test_returns_list(self, cli):
        cli("add", "proj", "--para", "project")
        cli("add", "task", "--parent", "1")
        cli("done", "2")
        rows = _j(cli, "changes", "--month", "2026-06", "-o", "json")
        assert isinstance(rows, list)

    def test_bucket_structure(self, cli):
        cli("add", "proj", "--para", "project")
        cli("add", "work", "--parent", "1")
        cli("done", "2")
        rows = _j(cli, "changes", "--month", "2026-06", "-o", "json")
        if rows:
            b = rows[0]
            assert "project" in b and "done" in b and "added_open" in b and "logged" in b


class TestGoalJson:
    def test_read_null_when_no_goal(self, cli):
        result = _j(cli, "goal", "-o", "json")
        assert result is None

    def test_read_returns_body_and_logged_at(self, cli):
        cli("goal", "ship the feature")
        d = _j(cli, "goal", "-o", "json")
        assert d["body"] == "ship the feature"
        assert "logged_at" in d

    def test_prefix_syntax(self, cli):
        cli("goal", "prefix test")
        d = _j(cli, "-o", "json", "goal")
        assert d["body"] == "prefix test"

    def test_goal_ls_returns_dict_by_field(self, cli):
        cli("add", "node")
        cli("goal", "set", "1", "deliver it")
        d = _j(cli, "goal", "ls", "1", "-o", "json")
        assert isinstance(d, dict) and "goal" in d
        assert d["goal"]["body"] == "deliver it"

    def test_goal_ls_empty_node(self, cli):
        cli("add", "bare node")
        d = _j(cli, "goal", "ls", "1", "-o", "json")
        assert d == {}


class TestPropLsJson:
    def test_returns_key_value_list(self, cli):
        cli("add", "task")
        cli("set", "1", "owner", "alice")
        cli("set", "1", "sprint", "42")
        rows = _j(cli, "prop", "ls", "1", "-o", "json")
        keys = {r["key"] for r in rows}
        assert "owner" in keys and "sprint" in keys
        for r in rows:
            assert "key" in r and "value" in r

    def test_empty_returns_empty_list(self, cli):
        cli("add", "bare")
        rows = _j(cli, "prop", "ls", "1", "-o", "json")
        assert rows == []


class TestLogLsJson:
    def test_returns_logs_with_fields(self, cli):
        cli("add", "task")
        cli("log", "1", "first entry")
        cli("log", "1", "second entry")
        rows = _j(cli, "log", "ls", "1", "-o", "json")
        assert len(rows) == 2
        assert rows[0]["body"] == "first entry"
        for r in rows:
            assert {"id", "logged_at", "tag", "body"} <= r.keys()

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        cli("log", "1", "entry")
        rows = _j(cli, "-o", "json", "log", "ls", "1")
        assert rows[0]["body"] == "entry"


class TestLinkLsJson:
    def test_returns_list_of_doc_names(self, cli):
        cli("add", "task")
        cli("link", "1", "Design doc")
        cli("link", "1", "Meeting notes")
        rows = _j(cli, "link", "ls", "1", "-o", "json")
        assert set(rows) == {"Design doc", "Meeting notes"}

    def test_empty_returns_empty_list(self, cli):
        cli("add", "bare")
        rows = _j(cli, "link", "ls", "1", "-o", "json")
        assert rows == []


class TestTagLsJson:
    def test_returns_tag_list(self, cli):
        cli("add", "task", "-t", "work,P0")
        rows = _j(cli, "tag", "ls", "1", "-o", "json")
        assert set(rows) == {"work", "P0"}

    def test_empty_returns_empty_list(self, cli):
        cli("add", "bare")
        rows = _j(cli, "tag", "ls", "1", "-o", "json")
        assert rows == []


class TestAliasLsJson:
    def test_returns_aliases_and_config_path(self, cli):
        cli("alias", "add", "td", "day today")
        d = _j(cli, "alias", "ls", "-o", "json")
        assert "aliases" in d
        assert "config_path" in d
        assert any(a["name"] == "td" and a["target"] == "day today" for a in d["aliases"])

    def test_empty_returns_empty_aliases_list(self, cli):
        d = _j(cli, "alias", "ls", "-o", "json")
        assert d["aliases"] == []
        assert "config_path" in d


class TestSchedLsJson:
    def test_one_off_date(self, cli):
        cli("add", "task")
        cli("sched", "1", "2026-09-01")
        d = _j(cli, "sched", "ls", "1", "-o", "json")
        assert d["node_id"] == 1
        assert d["rows"][0]["on_date"] == "2026-09-01"
        assert d["rows"][0]["recurrence"] is None

    def test_recurring_rule(self, cli):
        cli("add", "task")
        cli("sched", "1", "--recur", "weekly:Mon")
        d = _j(cli, "sched", "ls", "1", "-o", "json")
        assert any(r["recurrence"] == "weekly:Mon" for r in d["rows"])

    def test_empty_returns_empty_list(self, cli):
        cli("add", "bare")
        d = _j(cli, "sched", "ls", "1", "-o", "json")
        assert d["node_id"] == 1
        assert d["rows"] == []


class TestMetricLsJson:
    def test_returns_metric_rows(self, cli):
        cli("add", "task")
        cli("metric", "add", "1", "weight", "70", "--unit", "kg")
        cli("metric", "add", "1", "weight", "71")
        d = _j(cli, "metric", "ls", "1", "-o", "json")
        rows = d["rows"]
        assert len(rows) == 2
        assert rows[0]["tag"] == "weight"
        assert rows[0]["value_num"] == 70.0
        assert rows[0]["unit"] == "kg"
        for r in rows:
            assert {"id", "node_id", "log_id", "tag", "value_num", "value_text", "unit", "note", "at"} <= r.keys()

    def test_empty_returns_empty_list(self, cli):
        cli("add", "bare")
        d = _j(cli, "metric", "ls", "1", "-o", "json")
        assert d["rows"] == []

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        cli("metric", "add", "1", "hr", "60")
        d = _j(cli, "-o", "json", "metric", "ls", "1")
        assert d["rows"][0]["tag"] == "hr"


class TestWriteCommandsJson:
    def test_add_returns_node_summary(self, cli):
        d = _j(cli, "add", "new task", "-o", "json")
        assert d["id"] == 1 and d["title"] == "new task"
        assert "status" in d and "type" in d

    def test_add_prefix_syntax(self, cli):
        d = _j(cli, "-o", "json", "add", "pfx task")
        assert d["title"] == "pfx task"

    def test_log_write_returns_log_entry(self, cli):
        cli("add", "task")
        d = _j(cli, "-o", "json", "log", "1", "progress note")
        assert d["node_id"] == 1 and d["body"] == "progress note"
        assert "id" in d and "logged_at" in d

    def test_done_returns_node_list(self, cli):
        cli("add", "task")
        rows = _j(cli, "done", "1", "-o", "json")
        assert isinstance(rows, list) and rows[0]["id"] == 1
        assert rows[0]["status"] == "DONE"

    def test_done_suppresses_text(self, cli):
        cli("add", "task")
        code, out, _ = cli("done", "1", "-o", "json")
        assert "→ DONE" not in out

    def test_cancel_returns_node_list(self, cli):
        cli("add", "task")
        rows = _j(cli, "cancel", "1", "-o", "json")
        assert rows[0]["status"] == "CANCELED"

    def test_reopen_returns_node_list(self, cli):
        cli("add", "task")
        cli("done", "1")
        rows = _j(cli, "reopen", "1", "-o", "json")
        assert rows[0]["status"] == "TODO"

    def test_defer_returns_node_list(self, cli):
        cli("add", "task")
        rows = _j(cli, "defer", "1", "someday", "-o", "json")
        assert rows[0]["status"] == "LATER"

    def test_defer_suppresses_text(self, cli):
        cli("add", "task")
        code, out, _ = cli("defer", "1", "someday", "-o", "json")
        assert "→ LATER" not in out

    def test_start_returns_node_list(self, cli):
        cli("add", "task")
        rows = _j(cli, "start", "1", "-o", "json")
        assert rows[0]["status"] == "DOING"

    def test_start_suppresses_text(self, cli):
        cli("add", "task")
        code, out, _ = cli("start", "1", "-o", "json")
        assert "clocked in" not in out

    def test_stop_returns_node_list(self, cli):
        cli("add", "task")
        cli("start", "1")
        rows = _j(cli, "stop", "1", "-o", "json")
        assert isinstance(rows, list) and rows[0]["id"] == 1

    def test_stop_suppresses_text(self, cli):
        cli("add", "task")
        cli("start", "1")
        code, out, _ = cli("stop", "1", "-o", "json")
        assert "stopped" not in out

    def test_wait_returns_node_list(self, cli):
        cli("add", "task")
        rows = _j(cli, "wait", "1", "-o", "json")
        assert rows[0]["status"] == "WAIT"

    def test_done_multiple_ids(self, cli):
        cli("add", "a")
        cli("add", "b")
        rows = _j(cli, "done", "1", "2", "-o", "json")
        assert len(rows) == 2 and {r["id"] for r in rows} == {1, 2}


class TestLogShowJson:
    def test_returns_log_fields(self, cli):
        cli("add", "task")
        _, out, _ = cli("-o", "json", "log", "1", "full body text")
        import json as _json
        log_id = _json.loads(out)["id"]
        d = _j(cli, "log", "show", str(log_id), "-o", "json")
        assert d["id"] == log_id and d["body"] == "full body text"
        assert {"id", "node_id", "tag", "body", "logged_at"} <= d.keys()

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        _, out, _ = cli("-o", "json", "log", "1", "entry")
        import json as _json
        log_id = _json.loads(out)["id"]
        d = _j(cli, "-o", "json", "log", "show", str(log_id))
        assert d["body"] == "entry"


class TestActiveJson:
    def test_empty_when_nothing_running(self, cli):
        cli("add", "task")
        rows = _j(cli, "active", "-o", "json")
        assert rows == []

    def test_running_task_appears(self, cli):
        cli("add", "task")
        cli("start", "1")
        rows = _j(cli, "active", "-o", "json")
        assert len(rows) == 1
        r = rows[0]
        assert r["node_id"] == 1 and r["title"] == "task"
        assert {"node_id", "title", "status", "priority", "start_at", "elapsed_min"} <= r.keys()

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        cli("start", "1")
        rows = _j(cli, "-o", "json", "active")
        assert rows[0]["node_id"] == 1


class TestRelationJson:
    def test_read_empty(self, cli):
        cli("add", "task")
        d = _j(cli, "relation", "1", "-o", "json")
        assert d == {"block": [], "split": [], "related": [], "blocked_by": [], "split_from": []}

    def test_read_with_relation(self, cli):
        cli("add", "a")
        cli("add", "b")
        cli("relation", "1", "related", "2")
        d = _j(cli, "relation", "1", "-o", "json")
        assert 2 in d["related"]

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        d = _j(cli, "-o", "json", "relation", "1")
        assert "split_from" in d and "related" in d


class TestRecapJson:
    def test_read_null_when_no_recap(self, cli):
        result = _j(cli, "recap", "-o", "json")
        assert result is None

    def test_read_returns_body_and_logged_at(self, cli):
        cli("recap", "shipped the feature")
        d = _j(cli, "recap", "-o", "json")
        assert d["body"] == "shipped the feature" and "logged_at" in d

    def test_prefix_syntax(self, cli):
        cli("recap", "prefix recap")
        d = _j(cli, "-o", "json", "recap")
        assert d["body"] == "prefix recap"


class TestClockLsJson:
    def test_returns_intervals(self, cli):
        cli("add", "task")
        cli("start", "1")
        cli("stop", "1")
        rows = _j(cli, "clock", "ls", "1", "-o", "json")
        assert len(rows) == 1
        assert {"id", "start_at", "end_at", "elapsed_sec"} <= rows[0].keys()
        assert rows[0]["end_at"] is not None

    def test_empty_returns_empty_list(self, cli):
        cli("add", "bare")
        rows = _j(cli, "clock", "ls", "1", "-o", "json")
        assert rows == []

    def test_open_clock_has_null_end(self, cli):
        cli("add", "task")
        cli("start", "1")
        rows = _j(cli, "clock", "ls", "1", "-o", "json")
        assert rows[0]["end_at"] is None


class TestAgentJson:
    def test_ls_empty(self, cli):
        rows = _j(cli, "agent", "ls", "-o", "json")
        assert rows == []

    def test_show_unbound_returns_structured(self, cli, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-xyz")
        result = _j(cli, "agent", "-o", "json")
        assert result["node_id"] is None
        assert result["agent"] is None
        assert result["session_id"] == "test-session-xyz"

    def test_ls_after_bind(self, cli, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-xyz")
        cli("add", "bound task")
        cli("agent", "1")
        rows = _j(cli, "agent", "ls", "-o", "json")
        assert len(rows) >= 1
        assert {"id", "agent", "sid", "title", "act", "bound"} <= rows[0].keys()


class TestDateLsJson:
    def test_returns_date_label_list(self, cli):
        cli("dateinfo", "2026-10-01", "National Day")
        cli("dateinfo", "2026-10-07", "last day holiday")
        rows = _j(cli, "date", "ls", "-o", "json")
        dates = {r["date"] for r in rows}
        assert "2026-10-01" in dates
        for r in rows:
            assert "date" in r and "label" in r

    def test_single_date_lookup(self, cli):
        cli("dateinfo", "2026-05-01", "Labor Day")
        d = _j(cli, "date", "ls", "2026-05-01", "-o", "json")
        assert d["date"] == "2026-05-01" and d["label"] == "Labor Day"

    def test_empty_returns_empty_list(self, cli):
        rows = _j(cli, "date", "ls", "-o", "json")
        assert rows == []


class TestSpentJson:
    def test_returns_clock_row(self, cli):
        cli("add", "task")
        d = _j(cli, "spent", "1", "30", "-o", "json")
        assert d["node_id"] == 1
        assert d["elapsed_sec"] == 1800
        assert "start_at" in d and "end_at" in d

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        d = _j(cli, "-o", "json", "spent", "1", "15")
        assert d["node_id"] == 1 and d["elapsed_sec"] == 900


class TestUnlinkJson:
    def test_returns_remaining_links(self, cli):
        cli("add", "task")
        cli("link", "1", "Doc A")
        cli("link", "1", "Doc B")
        links = _j(cli, "unlink", "1", "Doc A", "-o", "json")
        assert "Doc A" not in links
        assert "Doc B" in links

    def test_all_removed_returns_empty_list(self, cli):
        cli("add", "task")
        cli("link", "1", "Only Doc")
        links = _j(cli, "unlink", "1", "Only Doc", "-o", "json")
        assert links == []


class TestRelationWriteJson:
    def test_write_returns_relation_dict(self, cli):
        cli("add", "task A")
        cli("add", "task B")
        d = _j(cli, "relation", "1", "related", "2", "-o", "json")
        assert isinstance(d, dict)
        assert 2 in d["related"]

    def test_rm_returns_updated_dict(self, cli):
        cli("add", "task A")
        cli("add", "task B")
        cli("relation", "1", "related", "2")
        d = _j(cli, "relation", "1", "related", "2", "--rm", "-o", "json")
        assert 2 not in d["related"]


class TestSetJson:
    def test_prop_set_returns_key_value(self, cli):
        cli("add", "task")
        d = _j(cli, "set", "1", "owner", "alice", "-o", "json")
        assert d["key"] == "owner" and d["value"] == "alice"

    def test_goal_set_returns_body_and_logged_at(self, cli):
        cli("add", "task")
        d = _j(cli, "set", "1", "goal", "ship it", "-o", "json")
        assert d["key"] == "goal" and d["body"] == "ship it"
        assert "logged_at" in d

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        d = _j(cli, "-o", "json", "set", "1", "prio", "high")
        assert d["key"] == "prio" and d["value"] == "high"


class TestUnsetJson:
    def test_returns_key_and_removed_count(self, cli):
        cli("add", "task")
        cli("set", "1", "owner", "alice")
        d = _j(cli, "unset", "1", "owner", "-o", "json")
        assert d["key"] == "owner" and d["removed"] == 1

    def test_missing_key_removed_zero(self, cli):
        cli("add", "task")
        d = _j(cli, "unset", "1", "nonexistent", "-o", "json")
        assert d["key"] == "nonexistent" and d["removed"] == 0

    def test_goal_unset_returns_removed_count(self, cli):
        cli("add", "task")
        cli("set", "1", "goal", "ship it")
        d = _j(cli, "unset", "1", "goal", "-o", "json")
        assert d["key"] == "goal" and d["removed"] == 1


class TestTagWriteJson:
    def test_add_returns_updated_tag_list(self, cli):
        cli("add", "task")
        tags = _j(cli, "-o", "json", "tag", "1", "+work", "+P0")
        assert "work" in tags and "P0" in tags

    def test_rm_returns_updated_tag_list(self, cli):
        cli("add", "task")
        cli("tag", "1", "+work", "+P0")
        tags = _j(cli, "tag", "rm", "1", "work", "-o", "json")
        assert "work" not in tags and "P0" in tags

    def test_prefix_syntax(self, cli):
        cli("add", "task")
        tags = _j(cli, "-o", "json", "tag", "1", "urgent")
        assert "urgent" in tags


class TestClockEditRmJson:
    def test_edit_returns_updated_clock_row(self, cli):
        cli("add", "task")
        cli("start", "1")
        cli("stop", "1")
        rows = _j(cli, "clock", "ls", "1", "-o", "json")
        cid = rows[0]["id"]
        d = _j(cli, "clock", "edit", str(cid), "--end", "", "-o", "json")
        assert d["id"] == cid and "end_at" in d and "elapsed_sec" in d

    def test_rm_returns_deleted_ids(self, cli):
        cli("add", "task")
        cli("start", "1")
        cli("stop", "1")
        rows = _j(cli, "clock", "ls", "1", "-o", "json")
        cid = rows[0]["id"]
        d = _j(cli, "clock", "rm", str(cid), "-o", "json")
        assert d["deleted"] == [cid]


class TestMetricWriteJson:
    def test_add_returns_metric_row(self, cli):
        cli("add", "task")
        d = _j(cli, "metric", "add", "1", "weight", "72.5", "-o", "json")
        assert d["node_id"] == 1 and d["tag"] == "weight"
        assert d["value_num"] == pytest.approx(72.5)
        assert "id" in d and "at" in d

    def test_edit_returns_updated_row(self, cli):
        cli("add", "task")
        m = _j(cli, "metric", "add", "1", "steps", "8000", "-o", "json")
        mid = m["id"]
        d = _j(cli, "metric", "edit", str(mid), "--value", "9000", "-o", "json")
        assert d["id"] == mid and d["value_num"] == pytest.approx(9000)

    def test_rm_returns_deleted_ids(self, cli):
        cli("add", "task")
        m = _j(cli, "metric", "add", "1", "score", "5", "-o", "json")
        mid = m["id"]
        d = _j(cli, "metric", "rm", str(mid), "-o", "json")
        assert d["deleted"] == [mid]


class TestSchedWriteJson:
    def test_sched_add_returns_updated_list(self, cli):
        cli("add", "task")
        d = _j(cli, "sched", "1", "2026-09-01", "-o", "json")
        assert any(r["on_date"] == "2026-09-01" for r in d["schedule"])

    def test_sched_rm_returns_cleared_count(self, cli):
        cli("add", "task")
        cli("sched", "1", "2026-09-01")
        d = _j(cli, "sched", "rm", "1", "-o", "json")
        assert d["cleared"] == 1


class TestUnlogJson:
    def test_rm_by_log_id_returns_deleted(self, cli):
        cli("add", "task")
        cli("log", "1", "entry")
        d = _j(cli, "log", "rm", "1", "-o", "json")
        assert d["deleted"] == [1]
        assert d["node_id"] == 1

    def test_rm_by_node_returns_deleted_list(self, cli):
        cli("add", "task")
        cli("log", "1", "entry a")
        cli("log", "1", "entry b")
        d = _j(cli, "log", "rm", "--node", "1", "-o", "json")
        assert len(d["deleted"]) == 1  # default without --all: latest only

    def test_rm_by_node_all_returns_all(self, cli):
        cli("add", "task")
        cli("log", "1", "entry a")
        cli("log", "1", "entry b")
        d = _j(cli, "log", "rm", "--node", "1", "--all", "-o", "json")
        assert len(d["deleted"]) == 2

    def test_no_logs_returns_empty_deleted(self, cli):
        cli("add", "task")
        d = _j(cli, "log", "rm", "--node", "1", "-o", "json")
        assert d["deleted"] == []
        assert d["node_id"] == 1
        assert d["metrics_deleted"] == 0


class TestRelogJson:
    def test_edit_returns_updated_log_row(self, cli):
        cli("add", "task")
        _j(cli, "log", "1", "original body", "-o", "json")
        d = _j(cli, "log", "edit", "1", "fixed body", "-o", "json")
        assert d["id"] == 1
        assert d["body"] == "fixed body"
        assert "logged_at" in d
        assert d["node_id"] == 1


class TestNodeRmJson:
    def test_rm_returns_deleted_ids(self, cli):
        cli("add", "task")
        d = _j(cli, "node", "rm", "1", "-o", "json")
        assert d["deleted"] == [1]

    def test_rm_multiple_returns_all_ids(self, cli):
        cli("add", "task a")
        cli("add", "task b")
        d = _j(cli, "node", "rm", "1", "2", "-o", "json")
        assert set(d["deleted"]) == {1, 2}


class TestNodeEditJson:
    def test_edit_returns_updated_node(self, cli):
        cli("add", "original title")
        d = _j(cli, "node", "edit", "1", "--title", "new title", "-o", "json")
        assert d["id"] == 1
        assert d["title"] == "new title"
        assert "status" in d
        assert "priority" in d
        assert "scheduled_date" in d

    def test_edit_priority_reflected(self, cli):
        cli("add", "task")
        d = _j(cli, "node", "edit", "1", "--priority", "A", "-o", "json")
        assert d["priority"] == "A"


class TestLinkAddJson:
    def test_add_returns_updated_links(self, cli):
        cli("add", "task")
        d = _j(cli, "link", "add", "1", "My Doc", "-o", "json")
        assert d["node_id"] == 1
        assert "My Doc" in d["links"]

    def test_add_multiple_nodes_returns_list(self, cli):
        cli("add", "task a")
        cli("add", "task b")
        result = _j(cli, "-o", "json", "link", "add", "1", "2", "Shared Doc")
        assert isinstance(result, list)
        assert all("links" in r for r in result)


class TestDieJsonMode:
    """die() in JSON mode emits RFC 9457 Problem Details to stderr."""

    def test_not_found_emits_rfc9457_to_stderr(self, cli):
        code, out, err = cli("show", "999", "-o", "json")
        assert code == 1
        assert out == ""
        e = json.loads(err)
        assert e["type"] == "about:blank"
        assert e["status"] == 400
        assert "detail" in e
        assert "999" in e["detail"]

    def test_stdout_empty_on_error(self, cli):
        code, out, err = cli("log", "add", "999", "body", "-o", "json")
        assert code == 1
        assert out == ""

    def test_text_mode_error_is_not_json(self, cli):
        code, out, err = cli("show", "999")
        assert code == 1
        assert "✗" in err
        assert out == ""

    def test_json_mode_does_not_leak_into_next_command(self, cli):
        # After a JSON mode error, subsequent text-mode errors must not emit JSON.
        cli("show", "999", "-o", "json")
        code, out, err = cli("show", "999")
        assert "✗" in err


# ---------------------------------------------------------------------------
# Schema contract tests: JSON top-level keys are derived from the dataclass
# definition, not hardcoded — shape changes in the class break these tests.
# ---------------------------------------------------------------------------

class TestJsonSchemas:
    """Shape contracts pinned to the authoritative dataclass, not to magic strings."""

    def test_alias_ls_top_level_keys(self, cli):
        d = _j(cli, "alias", "ls", "-o", "json")
        assert set(d) == {f.name for f in dataclasses.fields(AliasLsResult)}

    def test_alias_entry_keys(self, cli):
        cli("alias", "add", "td", "day today")
        d = _j(cli, "alias", "ls", "-o", "json")
        from worklog.commands.alias import AliasEntry
        assert set(d["aliases"][0]) == {f.name for f in dataclasses.fields(AliasEntry)}

    def test_sched_ls_top_level_keys(self, cli):
        cli("add", "task")
        d = _j(cli, "sched", "ls", "1", "-o", "json")
        assert set(d) == {f.name for f in dataclasses.fields(SchedLsResult)}

    def test_sched_entry_keys(self, cli):
        cli("add", "task")
        cli("sched", "1", "2026-09-01")
        d = _j(cli, "sched", "ls", "1", "-o", "json")
        from worklog.commands.sched import SchedEntry
        assert set(d["rows"][0]) == {f.name for f in dataclasses.fields(SchedEntry)}

    def test_metric_ls_top_level_keys(self, cli):
        cli("add", "task")
        d = _j(cli, "metric", "ls", "1", "-o", "json")
        assert set(d) == {f.name for f in dataclasses.fields(MetricLsResult)}


# ---------------------------------------------------------------------------
# Convention: no bare print() in command handlers/renderers.
# out() must be used so JSON mode suppression and color control work correctly.
# output.py itself is exempt (JSONFormatter uses print() legitimately).
# ---------------------------------------------------------------------------

def test_no_bare_print_in_commands():
    src = pathlib.Path(__file__).parent.parent / "src" / "worklog" / "commands"
    violations = []
    for path in sorted(src.glob("*.py")):
        if path.name == "output.py":
            continue  # JSONFormatter.emit() legitimately uses print()
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("print(") and "# noqa" not in line and "file=sys.stderr" not in line:
                violations.append(f"{path.name}:{lineno}: {line.rstrip()}")
    assert not violations, "Bare print() bypasses out() suppression:\n" + "\n".join(violations)
