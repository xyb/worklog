"""Tests for completion (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestPrintCompletionFish:
    """wl print-completion fish: argparse → fish completion generator"""

    def test_fish_completion_header(self, cli):
        _, out, _ = cli("print-completion", "fish")
        assert "auto-generated" in out
        assert "complete -c wl -f" in out

    def test_fish_helper_functions_emitted(self, cli):
        _, out, _ = cli("print-completion", "fish")
        for fn in ("__wl_list_nodes", "__wl_list_tags",
                   "__wl_date_suggestions", "__wl_recur_suggestions"):
            assert f"function {fn}" in out

    def test_fish_subcommands_listed(self, cli):
        _, out, _ = cli("print-completion", "fish")
        # all subcommands should appear
        for cmd in ("add", "log", "done", "ls", "tree", "logs", "find",
                    "spent", "relog", "unlog", "checkin", "sched"):
            assert f'-a "{cmd}"' in out

    def test_fish_global_flags(self, cli):
        _, out, _ = cli("print-completion", "fish")
        assert "-l brief" in out  # -q/--brief
        assert "-l color" in out
        assert "-l show-canceled" in out

    def test_fish_choices_inline(self, cli):
        """choices=[...] emits -a "v1 v2 ..." """
        _, out, _ = cli("print-completion", "fish")
        # wl ls --sort choices
        assert "-a \"pri created updated closed scheduled title id\"" in out

    def test_fish_helpers_attached_to_recur(self, cli):
        """sched --recur uses __wl_recur_suggestions"""
        _, out, _ = cli("print-completion", "fish")
        assert "(__wl_recur_suggestions)" in out

    def test_fish_node_id_completion(self, cli):
        """show / done / log positional args → __wl_list_nodes"""
        _, out, _ = cli("print-completion", "fish")
        assert "(__wl_list_nodes)" in out

    def test_fish_date_suggestions(self, cli):
        """day / dateinfo accept dates → __wl_date_suggestions"""
        _, out, _ = cli("print-completion", "fish")
        assert "(__wl_date_suggestions)" in out

    def test_fish_compound_flags_present(self, cli):
        """wl add compound flags --log/--done/--at/--link must appear"""
        _, out, _ = cli("print-completion", "fish")
        # add subcommand section
        assert "-l done" in out
        assert "-l link" in out
        assert "-l at" in out
        assert "-l log" in out

    def test_fish_no_db_required(self, cli, tmp_path, monkeypatch):
        """print-completion runs without a DB (meta command)"""
        # point WORKLOG_DB to a non-existent path; print-completion should still work
        monkeypatch.setenv("WORKLOG_DB", str(tmp_path / "no-such.db"))
        code, out, _ = cli("print-completion", "fish")
        assert code == 0
        assert "complete -c wl" in out

    def test_unsupported_shell_rejected(self, cli):
        import pytest
        with pytest.raises(SystemExit):  # argparse choices rejects 'tcsh' at parse time
            cli("print-completion", "tcsh")


class TestPrintCompletionBash:
    """wl print-completion bash: argparse → bash _wl() function"""

    def test_bash_completion_header(self, cli):
        _, out, _ = cli("print-completion", "bash")
        assert "auto-generated" in out
        assert "complete -F _wl wl" in out
        assert "_wl() {" in out

    def test_bash_helper_functions_emitted(self, cli):
        _, out, _ = cli("print-completion", "bash")
        for fn in ("__wl_list_nodes_bash", "__wl_list_tags_bash",
                   "__wl_date_suggestions_bash", "__wl_recur_suggestions_bash"):
            assert f"{fn}()" in out

    def test_bash_subcommand_names_in_subcmds(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # subcmds list contains all subcommands
        for cmd in ("add", "log", "done", "ls", "tree", "spent", "relog", "checkin"):
            assert cmd in out

    def test_bash_case_per_subcmd(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # case "$sub" in covers every sub
        for cmd in ("add", "ls", "sched"):
            assert f"{cmd})" in out

    def test_bash_prev_case_for_choices(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # e.g. ls --sort choices should appear in prev case
        assert "pri created updated closed scheduled title id" in out

    def test_bash_node_id_helper_in_positional(self, cli):
        _, out, _ = cli("print-completion", "bash")
        # subcommands like done/start take a positional node id → __wl_list_nodes_bash
        assert "__wl_list_nodes_bash" in out


class TestPrintCompletionZsh:
    """wl print-completion zsh: argparse → zsh _wl() + compdef"""

    def test_zsh_completion_header(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        assert out.startswith("#compdef wl")
        assert "auto-generated" in out
        assert "compdef _wl wl" in out

    def test_zsh_helper_functions_emitted(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        for fn in ("__wl_list_nodes_zsh", "__wl_list_tags_zsh",
                   "__wl_date_suggestions_zsh", "__wl_recur_suggestions_zsh"):
            assert f"{fn}()" in out

    def test_zsh_uses_arguments(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        assert "_arguments" in out
        assert "_describe" in out

    def test_zsh_state_machine(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        # uses ->cmds / ->args state machine
        assert "->cmds" in out
        assert "->args" in out

    def test_zsh_subcommand_descriptions(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        # _describe uses 'name:description' format
        assert "add:" in out  # colon between description and name
        assert "log:" in out

    def test_zsh_recur_helper_attached(self, cli):
        _, out, _ = cli("print-completion", "zsh")
        assert "__wl_recur_suggestions_zsh" in out


class TestAllSubparsersHaveDescription:
    """battery-included DESIGN §35: every sub parser must have a description
    (one-line intro after usage line), falling back to help."""

    def test_every_subparser_has_description(self):
        import argparse; from worklog import cli as wl
        p = wl.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        missing = []
        seen = set()
        for name, sub in sa.choices.items():
            if id(sub) in seen:
                continue  # skip alias
            seen.add(id(sub))
            if not (sub.description or "").strip():
                missing.append(name)
        assert not missing, f"the following sub parsers lack description: {missing}"

    def test_every_subparser_has_epilog(self):
        """§35 battery-included: every cmd should have an epilog with examples / differences from adjacent commands / use cases"""
        import argparse; from worklog import cli as wl
        p = wl.build_parser()
        sa = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        missing = []
        seen = set()
        for name, sub in sa.choices.items():
            if id(sub) in seen:
                continue
            seen.add(id(sub))
            if not (sub.epilog or "").strip():
                missing.append(name)
        assert not missing, f"the following sub parsers lack epilog (§35): {missing}"
