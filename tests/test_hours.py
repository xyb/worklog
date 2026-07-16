"""`wl hours` — reconstruct where time went from the log stream, grouped by
project / task / day. Each adjacent-log interval is attributed to the earlier
log's node, capped at 60 min (so lunch / overnight gaps don't inflate a task).

Honest scope: this measures log-activity time (when work happened on a node,
human or agent), not pure human presence — that's the external multi-source tool.
"""
import json
import pytest


def _j(cli, *args):
    code, out, err = cli(*args)
    assert code == 0, f"exit {code}: {err}"
    return json.loads(out)


def _seed(cli):
    cli("add", "Proj", "--para", "project")        # 1
    cli("add", "T1", "--parent", "1")              # 2
    cli("add", "T2", "--parent", "1")              # 3
    # logs on a single day; intervals: 14:00->14:30 (30, T1), 14:30->15:00 (30, T1),
    # 15:00->15:20 (20, T2). last log -> 0.  => T1=60, T2=20, total=80
    cli("log", "2", "a", "--date", "2026-07-14", "--time", "14:00")
    cli("log", "2", "b", "--date", "2026-07-14", "--time", "14:30")
    cli("log", "3", "c", "--date", "2026-07-14", "--time", "15:00")
    cli("log", "3", "d", "--date", "2026-07-14", "--time", "15:20")


class TestByTask:
    def test_attribution_and_total(self, cli):
        _seed(cli)
        d = _j(cli, "hours", "2026-07-14", "--by", "task", "-o", "json")
        assert d["by"] == "task" and d["total_min"] == 80
        by_title = {g["title"]: g["min"] for g in d["groups"]}
        assert by_title == {"T1": 60, "T2": 20}

    def test_groups_sorted_desc(self, cli):
        _seed(cli)
        d = _j(cli, "hours", "2026-07-14", "--by", "task", "-o", "json")
        mins = [g["min"] for g in d["groups"]]
        assert mins == sorted(mins, reverse=True)


class TestByProject:
    def test_rolls_tasks_into_project(self, cli):
        _seed(cli)
        d = _j(cli, "hours", "2026-07-14", "--by", "project", "-o", "json")
        assert d["by"] == "project"
        g = d["groups"]
        assert len(g) == 1 and g[0]["title"] == "Proj" and g[0]["min"] == 80

    def test_default_by_is_project(self, cli):
        _seed(cli)
        d = _j(cli, "hours", "2026-07-14", "-o", "json")
        assert d["by"] == "project"

    def test_unassigned_bucket(self, cli):
        cli("add", "loner")                        # 1, no project ancestor
        cli("log", "1", "a", "--date", "2026-07-14", "--time", "09:00")
        cli("log", "1", "b", "--date", "2026-07-14", "--time", "09:30")
        d = _j(cli, "hours", "2026-07-14", "--by", "project", "-o", "json")
        assert d["groups"][0]["title"] == "(unassigned)" and d["groups"][0]["min"] == 30


class TestBreakGap:
    def test_gap_over_threshold_is_a_break_dropped(self, cli):
        cli("add", "T")                            # 1
        # 14:00 -> 16:00 is 120 min > 60 -> a break, not counted at all
        cli("log", "1", "a", "--date", "2026-07-14", "--time", "14:00")
        cli("log", "1", "b", "--date", "2026-07-14", "--time", "16:00")
        d = _j(cli, "hours", "2026-07-14", "--by", "task", "-o", "json")
        assert d["total_min"] == 0

    def test_gap_within_threshold_counts_fully(self, cli):
        cli("add", "T")                            # 1
        cli("log", "1", "a", "--date", "2026-07-14", "--time", "14:00")
        cli("log", "1", "b", "--date", "2026-07-14", "--time", "14:55")   # 55 <= 60, counts
        d = _j(cli, "hours", "2026-07-14", "--by", "task", "-o", "json")
        assert d["total_min"] == 55


class TestByDay:
    def test_groups_by_calendar_day(self, cli):
        cli("add", "T")                            # 1
        cli("log", "1", "a", "--date", "2026-07-13", "--time", "10:00")
        cli("log", "1", "b", "--date", "2026-07-13", "--time", "10:40")   # 40 on 07-13
        cli("log", "1", "c", "--date", "2026-07-14", "--time", "10:00")
        cli("log", "1", "e", "--date", "2026-07-14", "--time", "10:15")   # 15 on 07-14
        d = _j(cli, "hours", "--since", "2026-07-13", "--until", "2026-07-14",
               "--by", "day", "-o", "json")
        by = {g["title"]: g["min"] for g in d["groups"]}
        assert by == {"2026-07-13": 40, "2026-07-14": 15}


class TestSpans:
    """--spans: a chronological list of continuous same-node activity spans (for
    overlaying against other presence sources), not aggregate totals."""

    def _seed_spans(self, cli):
        cli("add", "Proj", "--para", "project")        # 1
        cli("add", "T1", "--parent", "1")              # 2
        cli("add", "T2", "--parent", "1")              # 3
        # 09:00-10:00 on T1 (merged), 10:00-10:30 on T2, break, 14:00-14:40 on T1 again
        cli("log", "2", "a", "--date", "2026-07-14", "--time", "09:00")
        cli("log", "2", "b", "--date", "2026-07-14", "--time", "09:30")
        cli("log", "3", "c", "--date", "2026-07-14", "--time", "10:00")
        cli("log", "3", "d", "--date", "2026-07-14", "--time", "10:30")
        cli("log", "2", "e", "--date", "2026-07-14", "--time", "14:00")
        cli("log", "2", "f", "--date", "2026-07-14", "--time", "14:40")

    def test_spans_merge_and_split(self, cli):
        self._seed_spans(cli)
        d = _j(cli, "hours", "2026-07-14", "--spans", "-o", "json")
        seq = [(s["title"], s["min"]) for s in d["spans"]]
        assert seq == [("T1", 60), ("T2", 30), ("T1", 40)]   # same task recurs (2 spans of T1)

    def test_span_fields_and_project(self, cli):
        self._seed_spans(cli)
        d = _j(cli, "hours", "2026-07-14", "--spans", "-o", "json")
        s = d["spans"][0]
        assert set(s) >= {"start", "end", "min", "node_id", "title", "project_id", "project"}
        assert s["project"] == "Proj" and s["node_id"] == 2

    def test_break_between_spans(self, cli):
        self._seed_spans(cli)
        d = _j(cli, "hours", "2026-07-14", "--spans", "-o", "json")
        # span[1] (T2, ends 10:30) and span[2] (T1, starts 14:00) are not contiguous
        assert d["spans"][1]["end"] != d["spans"][2]["start"]
        # adjacent node switch with no break IS contiguous
        assert d["spans"][0]["end"] == d["spans"][1]["start"]

    def test_toon_spans_tabular(self, cli):
        self._seed_spans(cli)
        code, out, _ = cli("hours", "2026-07-14", "--spans", "-o", "toon")
        assert code == 0 and "spans[3]{" in out

    def test_text_shows_break(self, cli):
        self._seed_spans(cli)
        code, out, _ = cli("hours", "2026-07-14", "--spans")
        assert code == 0 and "T1" in out and "break" in out


class TestEmptyAndFormats:
    def test_empty_window(self, cli):
        d = _j(cli, "hours", "2000-01-01", "-o", "json")
        assert d["total_min"] == 0 and d["groups"] == []

    def test_toon_groups_tabular(self, cli):
        _seed(cli)
        code, out, _ = cli("hours", "2026-07-14", "-o", "toon")
        assert code == 0
        assert "groups[1]{id,title,min,pct}:" in out   # uniform primitive rows

    def test_text_output_runs(self, cli):
        _seed(cli)
        code, out, _ = cli("hours", "2026-07-14")
        assert code == 0 and "Proj" in out

    def test_empty_window_text(self, cli):
        code, out, _ = cli("hours", "2000-01-01")
        assert code == 0 and "no log activity" in out

    def test_default_window_is_today(self, cli):
        cli("add", "T")                                  # 1
        cli("log", "1", "a")                             # logged now (today)
        cli("log", "1", "b")
        d = _j(cli, "hours", "-o", "json")               # no date -> today
        code, _, _ = cli("hours")                        # bare command runs
        assert code == 0
        # both logs land in today's window; groups non-empty (task T under no project)
        assert d["since"] == d["until"] and isinstance(d["groups"], list)

    def test_invalid_date_errors(self, cli):
        code, _, err = cli("hours", "notadate")
        assert code != 0
