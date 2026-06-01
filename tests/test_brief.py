"""Tests for brief (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestBriefMode:
    """global -q/--brief + each command's compact/distraction-free mode (token optimization)."""

    def test_brief_day_drops_log_bodies(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "做了 A 步")
        cli("log", "1", "做了 B 步")
        _, full, _ = cli("day")
        _, brief, _ = cli("-q", "day")
        assert "做了 A 步" in full
        assert "做了 A 步" not in brief
        assert "#1" in brief        # task row still present
        assert "(2 log)" in brief   # compact hint appears

    def test_day_log_tail_limits(self, cli):
        cli("add", "work item", "-k", "task")
        for i in range(5):
            cli("log", "1", f"进展 {i}")
        _, out, _ = cli("day", "--log-tail", "2")
        assert "进展 4" in out
        assert "进展 3" in out
        assert "进展 0" not in out
        assert "elided" in out

    def test_day_no_logs_equiv_brief(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "细节")
        _, brief, _ = cli("-q", "day")
        _, nologs, _ = cli("day", "--no-logs")
        assert "细节" not in brief
        assert "细节" not in nologs

    def test_brief_show_skips_timeline(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "log A")
        cli("log", "1", "log B")
        _, full, _ = cli("show", "1")
        _, brief, _ = cli("-q", "show", "1")
        assert "timeline" in full
        assert "timeline" not in brief
        assert "#1" in brief

    def test_show_timeline_tail(self, cli):
        cli("add", "work item", "-k", "task")
        for i in range(4):
            cli("log", "1", f"log {i}")
        _, out, _ = cli("show", "1", "--timeline-tail", "2")
        assert "timeline" in out
        assert "log 3" in out
        assert "log 0" not in out
        assert "elided" in out

    def test_brief_logs_drops_body(self, cli):
        cli("add", "work item", "-k", "task")
        cli("log", "1", "这是一段非常具体的 body 内容")
        # use --log-format full to get the full body (default oneline truncates by terminal width)
        _, full, _ = cli("--log-format", "full", "logs", "--since", "1970-01-01")
        _, brief, _ = cli("-q", "logs", "--since", "1970-01-01")
        assert "这是一段非常具体的 body 内容" in full
        assert "这是一段非常具体的 body 内容" not in brief
        assert "#1" in brief

    def test_logs_by_task_tail(self, cli):
        cli("add", "work item", "-k", "task")
        for i in range(5):
            cli("log", "1", f"step {i}")
        _, out, _ = cli("logs", "--since", "1970-01-01", "--by-task", "--tail", "2")
        assert "step 4" in out
        assert "step 3" in out
        assert "step 0" not in out
        assert "5 total" in out

    def test_brief_projects_drops_recent_date(self, cli):
        cli("add", "proj", "-k", "project")
        cli("add", "t1", "-k", "task", "--parent", "1")
        cli("log", "2", "推进")
        _, full, _ = cli("projects")
        _, brief, _ = cli("-q", "projects")
        assert "latest" in full
        assert "latest" not in brief
        assert "proj" in brief

    def test_projects_since_filters(self, cli):
        cli("add", "active", "-k", "project")
        cli("add", "stale", "-k", "project")
        cli("add", "t1", "-k", "task", "--parent", "1")
        cli("add", "t2", "-k", "task", "--parent", "2")
        cli("log", "3", "今天推进")
        cli("log", "4", "古早进展", "--date", "2020-01-01")
        _, since, _ = cli("projects", "--since", "2026-01-01")
        assert "active" in since
        assert "stale" not in since

    def test_summary_dedup_default(self, cli):
        # one task attached to two projects (one via parent + one via tag)
        cli("add", "P1", "-k", "project", "-t", "shared")
        cli("add", "P2", "-k", "project", "-t", "shared")
        # task parent=P1, also carries shared tag → P2 picks it up via tag
        from datetime import date
        cli("add", "重复 task", "-k", "task", "--parent", "1", "-t", "shared")
        cli("done", "3")
        _, out, _ = cli("summary", "--since", "1970-01-01")
        # default dedup: same task appears only once
        assert out.count("重复 task") == 1

    def test_summary_no_dedup_keeps_old(self, cli):
        cli("add", "P1", "-k", "project", "-t", "shared")
        cli("add", "P2", "-k", "project", "-t", "shared")
        cli("add", "重复 task", "-k", "task", "--parent", "1", "-t", "shared")
        cli("done", "3")
        _, out, _ = cli("summary", "--since", "1970-01-01", "--no-dedup")
        # old behaviour: same task listed once under P1 and once under P2
        assert out.count("重复 task") == 2

    def test_summary_projects_only(self, cli):
        cli("add", "proj", "-k", "project")
        cli("add", "活儿 task", "-k", "task", "--parent", "1")
        cli("done", "2")
        _, full, _ = cli("summary", "--since", "1970-01-01")
        _, po, _ = cli("summary", "--since", "1970-01-01", "--projects-only")
        assert "活儿 task" in full
        assert "活儿 task" not in po
        assert "proj" in po
        # -q is equivalent to --projects-only
        _, briefq, _ = cli("-q", "summary", "--since", "1970-01-01")
        assert "活儿 task" not in briefq

    def test_summary_top_n(self, cli):
        for i in range(5):
            cli("add", f"P{i}", "-k", "project")
            cli("add", f"t{i}", "-k", "task", "--parent", str(i + 1))
            cli("done", str(2 * (i + 1)))
        _, out, _ = cli("summary", "--since", "1970-01-01", "--top", "2", "--projects-only")
        # only 2 project headers
        assert out.count("▸") == 2

    def test_window_parent_parser_works(self, cli):
        # after parent parser upgrade, --since/--until still work in summary and logs
        from datetime import date, timedelta
        yday = (date.today() - timedelta(days=1)).isoformat()
        today = date.today().isoformat()
        cli("add", "work item", "-k", "task")
        cli("log", "1", "进展", "--date", yday)
        cli("done", "1")
        _, s_out, _ = cli("summary", "--since", yday, "--until", today)
        assert "work item" in s_out
        _, l_out, _ = cli("logs", "--since", yday, "--until", today)
        assert "进展" in l_out
