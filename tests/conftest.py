"""pytest fixtures: one isolated SQLite DB per test (under tmp_path)."""
import os
import sys
import time
from pathlib import Path
import pytest

# Pin the timezone for the whole suite so UTC-stored *_at instants render and
# day-group deterministically regardless of the CI/host zone. Asia/Shanghai is
# a fixed +08:00 (no DST), matching where the tool is used; SQLite's
# `datetime(col,'localtime')` and Python's `.astimezone()` both read this.
os.environ["TZ"] = "Asia/Shanghai"
if hasattr(time, "tzset"):
    time.tzset()

# Make the src layout importable (uv sync also installs it editable, but
# running pytest from a fresh checkout pre-`uv sync` still needs this hint).
PROJ_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ_ROOT / "src"))


@pytest.fixture(autouse=True, scope="session")
def _never_touch_real_db(tmp_path_factory):
    """SAFETY NET: pin WORKLOG_DB to a throwaway for the WHOLE session, so a test that forgets the
    tmp_db fixture can NEVER ensure_db / migrate the real database. Per-test tmp_db overrides this
    with its own path; this is the backstop that keeps the suite from ever touching real data
    (a forgotten fixture once let migrations run against the real DB)."""
    os.environ["WORKLOG_DB"] = str(tmp_path_factory.mktemp("wl-safety") / "session-safety.db")
    yield


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """One temp DB per test; cleaned automatically when the test ends."""
    db_file = tmp_path / "wl-test.db"
    monkeypatch.setenv("WORKLOG_DB", str(db_file))
    # isolate config (aliases.ini) to a tmp dir so the real ~/.config/worklog/aliases.ini never
    # leaks into tests — a user alias would otherwise alter parser/completion output (e.g. a
    # `w = day` alias makes `day`'s completion condition `day w`). Tests that need aliases set
    # their own XDG_CONFIG_HOME / HOME after this.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # reload the wl module so DB_PATH re-reads the env
    import importlib
    from worklog import cli as wl
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
