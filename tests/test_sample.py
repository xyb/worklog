"""Tests for sample (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestFullSampleScenario:
    """simulate a real scenario: time hierarchy + project + task + log + done + link"""

    def test_5_18_sample_scenario(self, cli, tmp_db):
        # time hierarchy
        cli("add", "Lifetime", "-k", "lifetime")
        cli("add", "2026", "-k", "year", "--parent", "1")
        cli("add", "Q2", "-k", "quarter", "--parent", "2")
        cli("add", "2026-05", "-k", "month", "--parent", "3")
        cli("add", "W21", "-k", "week", "--parent", "4")
        cli("add", "2026-05-18 周一", "-k", "day", "--parent", "5")
        # project (under month)
        cli("add", "Dev tooling", "-k", "project", "-p", "A", "-t", "work", "--parent", "4")
        # task (new model: under project, not under day; day view is driven by log dates)
        cli("add", "项目战略转向", "-k", "task", "-p", "A", "-t", "work,unplanned,P0", "--parent", "7")
        # 4 log entries
        cli("log", "8", "5/18 17:18 拍板战略转向")
        cli("log", "8", "5/19 09:42 拆需求 export_for_ai")
        cli("log", "8", "5/20 14:55 B 路径端到端打通 owner 精准度 6/6→7/7")
        cli("log", "8", "5/21 11:08 复盘 87% 成本下降")
        # link to vault docs
        cli("link", "8", "Dev tooling")
        cli("link", "8", "Q2 metric rollup")
        # done
        cli("done", "8")

        # verify
        code, out, _ = cli("show", "8")
        assert "项目战略转向" in out
        assert "DONE" in out
        assert "timeline / changes" in out
        assert "[[Dev tooling]]" in out
        assert "5/18 17:18" in out
        assert "5/21 11:08" in out

        # default tree is depth-limited (overview); use --depth to see the full hierarchy
        code, tree, _ = cli("tree", "--depth", "9")
        assert "Lifetime" in tree
        assert "2026-05-18" in tree
        assert "项目战略转向" in tree


# ─── rich highlight / theme ───
ESC = "\x1b["  # ANSI escape prefix
