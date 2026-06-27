"""Golden snapshot of every command's `--help` text — the byte-identical guard for the `add_cmd`
registry migration. The migration moved name→handler wiring out of a separate HANDLERS dict into
one `add_cmd(...)` call per command; the `_WlSubWrapper.add_parser` it calls has implicit behaviour
(copies help→description when description is omitted, auto-appends a `More: wl help …` epilog), so a
key passed as None/`""` instead of omitted would silently shift a subcommand's help. Top-level
`wl --help` + per-subcommand `wl <cmd> -h` are all snapshotted here; the wider test suite asserts
behaviour, this asserts the help bytes don't drift.

The snapshot is normalised across Python versions (argparse renamed the `optional arguments:`
section header to `options:` in 3.10), so one golden file serves the whole 3.9–3.14 CI matrix.

Regenerate intentionally (pins every input the test pins — width 100, color OFF regardless of TTY,
plain console, no user aliases — and writes through `_norm` so the file is one canonical form
regardless of interpreter):

    uv run python -c "import json,os,argparse; os.environ['COLUMNS']='120'; os.environ['NO_COLOR']='1'; \
        from worklog import cli; from tests.test_cli_help_golden import _norm; \
        cli._init_console('never', None); cli._USER_ALIASES={}; cli._USER_ALIAS_MAP={}; \
        p=cli.build_parser(); s=next(a for a in p._actions if isinstance(a,argparse._SubParsersAction)); \
        h={'__top__':_norm(p.format_help())}; h.update({n:_norm(sp.format_help()) for n,sp in s.choices.items()}); \
        open('tests/golden/cli_help.json','w').write(json.dumps(h,ensure_ascii=False,indent=1,sort_keys=True))"
"""
import argparse
import json
import pathlib

import pytest

from worklog import cli, render

GOLDEN = pathlib.Path(__file__).parent / "golden" / "cli_help.json"


def _norm(text):
    """Canonicalise the one argparse section header that differs by Python version (3.9 emits
    `optional arguments:`, 3.10+ emits `options:`), so the golden is version-independent."""
    return text.replace("\noptional arguments:\n", "\noptions:\n")


def _current_helps():
    p = cli.build_parser()
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    helps = {"__top__": _norm(p.format_help())}
    for name, sp in sub.choices.items():
        helps[name] = _norm(sp.format_help())
    return helps


@pytest.fixture
def _deterministic_help(monkeypatch):
    """Pin every input the help text depends on: width (→ help_width caps at 100), plain console
    (no ANSI), and zero user aliases (the dev's personal ~/.config aliases would otherwise leak
    into sub.choices). Restore the global console afterwards so other tests aren't affected."""
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("NO_COLOR", "1")  # colorize_help resolves color independently of _CONSOLE,
                                         # via stdout.isatty() — pin it off so `pytest -s` (real TTY)
                                         # doesn't bake ANSI into the captured help (same idiom as
                                         # test_help.py); the byte-identical guard mustn't rest on
                                         # pytest's stdout capture being non-TTY by accident.
    monkeypatch.setattr(cli, "_USER_ALIASES", {})
    monkeypatch.setattr(cli, "_USER_ALIAS_MAP", {})
    saved = render._CONSOLE
    cli._init_console("never", None)
    yield
    render._CONSOLE = saved


def test_command_help_matches_golden(_deterministic_help):
    current = _current_helps()
    golden = {k: _norm(v) for k, v in json.loads(GOLDEN.read_text()).items()}
    # same command set (catches a command dropped / renamed / orphaned by the migration)
    assert set(current) == set(golden), (
        f"command set changed — added {set(current) - set(golden)}, "
        f"removed {set(golden) - set(current)}")
    # and byte-identical help for each (after the version-header normalisation)
    drifted = [name for name in golden if current[name] != golden[name]]
    assert not drifted, f"help text drifted for: {drifted} (regenerate golden only if intended)"


def test_handlers_match_parser_choices(_deterministic_help):
    """The cheap structural invariant the registry must hold: every handler maps to a real parser
    subcommand and is callable, and — with user aliases cleared by the fixture, so `choices` holds
    only canonical names — no parser subcommand lacks a handler. Catches a command wired into the
    parser but not HANDLERS (or vice versa) more directly than the byte snapshot.

    The forward subset holds regardless of aliases; the reverse equality relies on the fixture's
    alias clearing (argparse `choices` would otherwise also contain alias keys, which are
    deliberately NOT HANDLERS keys — main() resolves an alias via `args.cmd not in HANDLERS`)."""
    p = cli.build_parser()
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    assert all(callable(h) for h in cli.HANDLERS.values())
    orphan_handlers = set(cli.HANDLERS) - set(sub.choices)  # handler with no parser — always wrong
    assert not orphan_handlers, f"handlers with no parser subcommand: {orphan_handlers}"
    unhandled = set(sub.choices) - set(cli.HANDLERS)         # parser command with no handler (no aliases here)
    assert not unhandled, f"parser subcommands with no handler: {unhandled}"
