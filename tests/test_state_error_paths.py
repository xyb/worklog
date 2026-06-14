"""CLI error / edge paths in worklog.commands.state that the happy-path suites skip:
node reparent/rm/edit guards, the prop group (ls / rm / no-subcommand), and the
agent-ls empty case."""


class TestNodeGuards:
    def test_reparent_missing_node_errors(self, cli):
        code, _, err = cli("node", "reparent", "99", "1")
        assert code != 0 and "not found" in err

    def test_rm_missing_node_errors(self, cli):
        code, _, err = cli("node", "rm", "99")
        assert code != 0 and "not found" in err

    def test_edit_bad_scheduled_errors(self, cli):
        cli("add", "task one", "-k", "task")            # node 1
        code, _, err = cli("node", "edit", "1", "--scheduled", "not-a-date")
        assert code != 0


class TestPropGroup:
    def test_prop_ls_missing_node(self, cli):
        code, _, err = cli("prop", "ls", "99")
        assert code != 0 and "not found" in err

    def test_prop_ls_empty(self, cli):
        cli("add", "task one", "-k", "task")            # node 1
        _, out, _ = cli("prop", "ls", "1")
        assert "no props" in out

    def test_prop_ls_lists_value(self, cli):
        cli("add", "task one", "-k", "task")            # node 1
        cli("set", "1", "owner", "me")
        _, out, _ = cli("prop", "ls", "1")
        assert "owner=me" in out

    def test_prop_rm_missing_node(self, cli):
        code, _, err = cli("prop", "rm", "99", "owner")
        assert code != 0 and "not found" in err

    def test_prop_rm_empty_key(self, cli):
        cli("add", "task one", "-k", "task")            # node 1
        code, _, err = cli("prop", "rm", "1", "")
        assert code != 0 and "key cannot be empty" in err

    def test_prop_no_subcommand_shows_usage(self, cli):
        code, _, err = cli("prop")
        assert code != 0 and "usage" in err.lower()


class TestAgentLsEmpty:
    def test_agent_ls_no_bindings(self, cli):
        _, out, _ = cli("agent", "ls")
        assert "no session bindings" in out
