"""Tests for find (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest

ESC = "["  # ANSI escape prefix


class TestFind:
    def _seed(self, cli):
        cli("add", "gaming 项目", "-k", "project", "-t", "gaming,work")  # 1
        cli("add", "other task", "-k", "task")                             # 2
        cli("log", "2", "这条 log 提到 gaming 关键词")
        cli("add", "带prop", "-k", "task")                              # 3
        cli("set", "3", "owner", "gaming-team")
        cli("add", "带link", "-k", "task")                              # 4
        cli("link", "4", "gaming 文档")

    def test_find_title(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming")
        assert code == 0
        # title hit highlighted (plain uses *…*), title field marked
        assert "*gaming* 项目" in out and "title" in out

    def test_find_in_log(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming")
        assert "other task" in out  # matched via log
        line = [l for l in out.split("\n") if "other task" in l][0]
        assert "log" in line

    def test_find_in_prop(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming-team")
        assert "带prop" in out

    def test_find_in_link(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming 文档", "--in", "link")
        assert "带link" in out

    def test_find_in_restricts(self, cli):
        self._seed(cli)
        # only search title; gaming inside log should not match #2
        code, out, _ = cli("find", "gaming", "--in", "title")
        assert "*gaming* 项目" in out
        assert "other task" not in out

    def test_find_kind_filter(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming", "--kind", "project")
        assert "*gaming* 项目" in out
        assert "other task" not in out

    def test_find_no_match(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "不存在的词xyz")
        assert "no matches" in out

    def test_find_expands_log_hit(self, cli):
        """match in log -> indented expansion of the matched fragment with *…* around the keyword"""
        self._seed(cli)
        code, out, _ = cli("find", "gaming")
        # #2 matched via log, should expand log content
        assert "log: " in out
        assert "*gaming*" in out

    def test_find_expands_link_and_prop(self, cli):
        self._seed(cli)
        code, out, _ = cli("find", "gaming-team")
        assert "prop: owner=gaming-team" in out

    def test_find_title_hit_not_expanded(self, cli):
        """match only in title (already in the row) -> no extra body/log expansion"""
        cli("add", "pure title hit gaming", "-k", "task")
        code, out, _ = cli("find", "pure title hit")
        # no log:/body:/tag: expansion lines (title is already on the node row)
        assert "log: " not in out and "body: " not in out


class TestFindTitleHighlight:
    def test_title_hit_marked_plain(self, cli):
        cli("add", "Uni-Game project", "-k", "project")
        code, out, _ = cli("--color", "never", "find", "Game")
        assert "Uni-*Game* project" in out  # title hit is marked

    def test_title_hit_highlighted_styled(self, cli):
        cli("add", "Uni-Game project", "-k", "project")
        code, out, _ = cli("--color", "always", "find", "Game")
        assert ESC in out
        assert "*Game*" not in out  # styled uses color, not asterisks

    def test_title_no_match_no_marker(self, cli):
        """hit in log but not title -> title should not be marked with *…*"""
        cli("add", "纯标题任务")
        cli("log", "1", "log 里有 needle 词")
        code, out, _ = cli("--color", "never", "find", "needle")
        assert "纯标题任务" in out  # title verbatim, no marker
        assert "*纯" not in out
