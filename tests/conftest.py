"""pytest fixtures: one isolated SQLite DB per test (under tmp_path)."""
import os
import sys
from pathlib import Path
import pytest

# let tests/ import the wl main module
PROJ_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ_ROOT))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """One temp DB per test; cleaned automatically when the test ends."""
    db_file = tmp_path / "wl-test.db"
    monkeypatch.setenv("WL_DB", str(db_file))
    # reload the wl module so DB_PATH re-reads the env
    import importlib
    import wl
    importlib.reload(wl)
    return wl


def run_cli(wl, *args):
    """Simulate the CLI: run main() with argv; return (exit_code, stdout, stderr) — captures print output."""
    import io
    import contextlib

    parser = wl.build_parser()
    parsed = parser.parse_args(list(args))
    wl._init_console(parsed.color, parsed.theme)

    wl.ensure_db()
    con = wl.db_connect()
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    exit_code = 0
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                wl.HANDLERS[parsed.cmd](parsed, con)
            except SystemExit as e:
                # sys.exit("msg") stores the message in e.code; the default interpreter
                # behavior prints it to stderr. We catch it here, so emit it ourselves.
                if isinstance(e.code, int):
                    exit_code = e.code
                elif e.code is None:
                    exit_code = 0
                else:
                    exit_code = 1
                    print(e.code, file=sys.stderr)
    finally:
        con.close()
    return exit_code, buf_out.getvalue(), buf_err.getvalue()


@pytest.fixture
def cli(tmp_db):
    """Return a partial of run_cli bound to the wl module."""
    def _run(*args):
        return run_cli(tmp_db, *args)
    return _run
