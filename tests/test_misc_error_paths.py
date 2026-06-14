"""Small CLI error-path tests for command branches that the happy-path suites skip —
relation id validation, unlink's empty-doc guard, and tag-rm's no-op message."""


class TestRelationErrors:
    def test_add_relation_non_numeric_id_errors(self, cli):
        code, _, err = cli("add", "child", "-k", "task", "--relation", "related abc")
        assert code != 0
        assert "not a node id" in err


class TestUnlinkErrors:
    def test_unlink_empty_doc_errors(self, cli):
        cli("add", "task one", "-k", "task")          # node 1
        code, _, err = cli("unlink", "1", "")
        assert code != 0
        assert "cannot be empty" in err


class TestTagRemoveNoop:
    def test_tag_rm_blank_token_removes_nothing(self, cli):
        cli("add", "task one", "-k", "task")          # node 1
        # a token that strips to empty ('+') removes nothing → the no-op message
        _, out, _ = cli("tag", "rm", "1", "+")
        assert "no tags removed" in out
