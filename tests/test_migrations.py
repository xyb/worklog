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


    def test_migration_rollback_on_bad_sql(self, tmp_path, monkeypatch):
        """A migration with malformed SQL must rollback that file's transaction
        and leave PRAGMA user_version at the last successful number."""
        from worklog import db, helpers
        db_file = tmp_path / "rollback.db"
        # Simulate a single bad migration in a tmp dir.
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "0001_will_fail.sql").write_text("THIS IS NOT VALID SQL;")
        con = db.db_connect(db_file)
        try:
            with pytest.raises(Exception):
                db.run_migrations(con, mig_dir)
            assert db.db_version(con) == 0  # rollback worked, version not bumped
        finally:
            con.close()


class TestLoadUserAliasesEdges:
    """Edge paths of _load_user_aliases (missing section, malformed file, empty target)."""

    def test_empty_section_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        cfg = tmp_path / ".config" / "worklog"
        cfg.mkdir(parents=True)
        (cfg / "aliases.ini").write_text("# no [aliases] section here\n[other]\nx = y\n")
        import importlib
        from worklog import cli as wl
        importlib.reload(wl)
        assert wl._load_user_aliases() == {}

    def test_malformed_ini_returns_empty(self, tmp_path, monkeypatch):
        """Unclosed section header → configparser.Error → returns {}."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        cfg = tmp_path / ".config" / "worklog"
        cfg.mkdir(parents=True)
        (cfg / "aliases.ini").write_text("[aliases\nd = day\n")  # missing closing bracket
        import importlib
        from worklog import cli as wl
        importlib.reload(wl)
        assert wl._load_user_aliases() == {}

    def test_empty_target_value_skipped(self, tmp_path, monkeypatch):
        """Aliases with empty target are skipped, valid ones still work."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        cfg = tmp_path / ".config" / "worklog"
        cfg.mkdir(parents=True)
        (cfg / "aliases.ini").write_text("[aliases]\nbad =   \nd = day\n")
        import importlib
        from worklog import cli as wl
        importlib.reload(wl)
        loaded = wl._load_user_aliases()
        assert loaded == {"day": ["d"]}
        assert "bad" not in loaded
