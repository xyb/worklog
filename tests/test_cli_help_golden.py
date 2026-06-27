"""Golden snapshot of every command's `--help` text — the byte-identical guard for the `add_cmd`
registry migration. The migration moved name→handler wiring out of a separate HANDLERS dict into
one `add_cmd(...)` call per command; the `_WlSubWrapper.add_parser` it calls has implicit behaviour
(copies help→description when description is omitted, auto-appends a `More: wl help …` epilog), so a
key passed as None/`""` instead of omitted would silently shift a subcommand's help. Top-level
`wl --help` + per-subcommand `wl <cmd> -h` are all snapshotted here; the wider test suite asserts
behaviour, this asserts the help bytes don't drift.

The snapshot is normalised across Python versions (argparse renamed the `optional arguments:`
section header to `options:` in 3.10), so one golden file serves the whole 3.9–3.14 CI matrix.

Regenerate intentionally (same env the test pins: width 100, plain console, no user aliases) with:

    uv run python -c "import json,os,argparse,sys; os.environ['COLUMNS']='120'; \
        from worklog import cli; cli._init_console('never', None); \
        cli._USER_ALIASES={}; cli._USER_ALIAS_MAP={}; \
        p=cli.build_parser(); s=next(a for a in p._actions if isinstance(a,argparse._SubParsersAction)); \
        h={'__top__':p.format_help()}; h.update({n:sp.format_help() for n,sp in s.choices.items()}); \
        open('tests/golden/cli_help.json','w').write(json.dumps(h,ensure_ascii=False,indent=1,sort_keys=True))"

(the test normalises the section header, so regenerating on any 3.9–3.14 interpreter is fine.)
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
    """The cheap structural invariant the registry must hold: the derived HANDLERS keys are exactly
    the parser's real subcommands, and every handler is callable. Cheaper + more direct than the
    byte snapshot at catching a command wired into the parser but not HANDLERS (or vice versa)."""
    p = cli.build_parser()
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    assert set(cli.HANDLERS) == set(sub.choices), (
        f"HANDLERS vs parser choices mismatch — "
        f"only in HANDLERS: {set(cli.HANDLERS) - set(sub.choices)}, "
        f"only in parser: {set(sub.choices) - set(cli.HANDLERS)}")
    assert all(callable(h) for h in cli.HANDLERS.values())
