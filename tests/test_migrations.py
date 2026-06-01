"""Tests for migrations (extracted from the original test_wl.py monolith)."""
import sqlite3
import pytest


class TestMigrations:
    """SQL migration runner: PRAGMA user_version + sorted NNNN_*.sql files."""

    def test_run_migrations_stamps_user_version(self, cli, tmp_db):
        """After `wl init`, PRAGMA user_version equals the highest migration number."""
        cli("init")
        con = tmp_db.db_connect()
        highest = max(int(p.stem.split("_", 1)[0]) for p in tmp_db._migration_files())
        assert con.execute("PRAGMA user_version").fetchone()[0] == highest

    def test_run_migrations_idempotent(self, cli, tmp_db):
        """Running ensure_db twice does not re-apply migrations."""
        cli("init")
        v1 = tmp_db.db_connect().execute("PRAGMA user_version").fetchone()[0]
        tmp_db.ensure_db()
        v2 = tmp_db.db_connect().execute("PRAGMA user_version").fetchone()[0]
        assert v1 == v2

    def test_wl_migrate_reports_up_to_date(self, cli):
        """`wl migrate` on a fresh DB (auto-applied via ensure_db) reports up-to-date."""
        cli("init")
        code, out, _ = cli("migrate")
        assert code == 0
        assert "no pending migrations" in out

    def test_migration_file_naming_is_strict(self, tmp_db):
        """_migration_files() returns only NNNN_*.sql; non-conforming names are filtered."""
        files = tmp_db._migration_files()
        assert files, "expected at least one migration file"
        for p in files:
            prefix = p.stem.split("_", 1)[0]
            assert prefix.isdigit(), f"non-numeric prefix: {p.name}"

    def test_downgrade_guard_aborts(self, tmp_path, monkeypatch):
        """If PRAGMA user_version > max migration shipped, refuse to run."""
        db = tmp_path / "newer.db"
        monkeypatch.setenv("WORKLOG_DB", str(db))
        import importlib, pytest
        from worklog import cli as wl
        importlib.reload(wl)
        # write a DB that pretends to be at version 999 — way ahead of what
        # this build ships, simulating a downgraded worklog binary.
        db.parent.mkdir(parents=True, exist_ok=True)
        con = wl.db_connect()
        con.execute("PRAGMA user_version = 999")
        con.commit()
        with pytest.raises(SystemExit) as exc:
            wl._run_migrations(con)
        assert "999" in str(exc.value)
        assert "newer" in str(exc.value).lower() or "upgrade" in str(exc.value).lower()
        con.close()

    def test_wl_migrate_applies_pending_on_first_run(self, tmp_path, monkeypatch):
        """Direct invocation of cmd_migrate on a fresh DB (bypassing ensure_db,
        mirroring `wl migrate`'s main() special-case) actually applies 0001."""
        db = tmp_path / "fresh.db"
        monkeypatch.setenv("WORKLOG_DB", str(db))
        import importlib
        from worklog import cli as wl
        importlib.reload(wl)
        # Mirror the main()-bypass path: open the connection WITHOUT ensure_db.
        db.parent.mkdir(parents=True, exist_ok=True)
        con = wl.db_connect()
        try:
            assert wl._db_version(con) == 0  # fresh DB, no migrations applied
            wl.cmd_migrate(type("A", (), {})(), con)
            assert wl._db_version(con) == 1  # 0001 now applied
        finally:
            con.close()


# --- add command ---
