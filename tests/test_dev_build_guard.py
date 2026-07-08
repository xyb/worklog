"""The dev/source-build seatbelt: a checkout must never silently touch the real DB.

Regression guard for the 2026-07 incident where an unreleased migration in the working tree
auto-applied to the live worklog.db (3× in a day). The guard lives in `db._guard_source_build_default_db`
and fires from `db_connect`, so every command that opens the DB hits it. The suite runs from the
editable checkout, so `is_source_checkout()` is genuinely True here — we drive the guard directly
with a controlled path / env instead of opening a real connection."""
import pytest
from pathlib import Path

from worklog import db
from worklog.xdg import _xdg_data_home


def _default_prod_db():
    return (_xdg_data_home() / "worklog" / "worklog.db").resolve()


def test_is_source_checkout_true_in_repo():
    # the suite runs from the editable repo checkout → the marker detection must see it
    assert db.is_source_checkout() is True


def test_guard_blocks_dev_build_on_default_db(monkeypatch):
    monkeypatch.delenv("WORKLOG_DB", raising=False)
    with pytest.raises(SystemExit):
        db._guard_source_build_default_db(_default_prod_db())


def test_guard_allows_explicit_worklog_db_env(monkeypatch):
    monkeypatch.setenv("WORKLOG_DB", "/tmp/wl-scratch.db")
    db._guard_source_build_default_db(_default_prod_db())  # opt-in → no raise


def test_guard_noop_on_non_default_db(monkeypatch):
    monkeypatch.delenv("WORKLOG_DB", raising=False)
    db._guard_source_build_default_db(Path("/tmp/some-scratch.db").resolve())  # not prod default → no raise


def test_db_connect_blocks_default_db_from_source_build(monkeypatch):
    # end-to-end through the real chokepoint: db_connect must refuse before opening the file
    monkeypatch.delenv("WORKLOG_DB", raising=False)
    with pytest.raises(SystemExit):
        db.db_connect(_default_prod_db())
