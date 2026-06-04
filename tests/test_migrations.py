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
            highest = max(int(p.stem.split("_", 1)[0]) for p in wl._migration_files())
            wl.cmd_migrate(type("A", (), {})(), con)
            assert wl._db_version(con) == highest  # all pending now applied
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

    def test_migration_is_atomic_across_statements(self, tmp_path):
        """A multi-statement migration where a LATER statement fails must roll
        back the whole file — no half-applied schema (executescript alone leaks)."""
        from worklog import db
        db_file = tmp_path / "atomic.db"
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        # 1st statement is valid DDL; 2nd fails (duplicate table). Without the
        # BEGIN/COMMIT wrap, the 1st CREATE would survive the rollback.
        (mig_dir / "0001_partial.sql").write_text(
            "CREATE TABLE early (x);\nCREATE TABLE early (x);\n"
        )
        con = db.db_connect(db_file)
        try:
            with pytest.raises(Exception):
                db.run_migrations(con, mig_dir)
            tables = [r["name"] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='early'"
            )]
            assert tables == []          # the early CREATE was rolled back
            assert db.db_version(con) == 0  # version not bumped
        finally:
            con.close()


class TestMetricSchema:
    """Migration 0002: metric table + log.tag column (node → log → metric foundation)."""

    def test_user_version_bumped_to_2(self, cli, tmp_db):
        cli("init")
        con = tmp_db.db_connect()
        assert con.execute("PRAGMA user_version").fetchone()[0] >= 2

    def test_metric_table_has_expected_columns(self, cli, tmp_db):
        cli("init")
        con = tmp_db.db_connect()
        cols = {r["name"] for r in con.execute("PRAGMA table_info(metric)")}
        assert cols == {
            "id", "log_id", "node_id", "tag",
            "value_num", "value_text", "unit", "note", "at",
        }

    def test_log_has_tag_column(self, cli, tmp_db):
        # log.type was renamed to log.tag in 0006 (unified with node.tag / metric.tag)
        cli("init")
        con = tmp_db.db_connect()
        cols = {r["name"] for r in con.execute("PRAGMA table_info(log)")}
        assert "tag" in cols and "type" not in cols

    def test_migration_0006_renames_type_to_tag_preserving_data(self, cli, tmp_db):
        """0006 renames log.type→tag in place, keeping existing typed-log values."""
        import pathlib
        cli("add", "t", "-k", "task")  # node 1
        con = tmp_db.db_connect()
        # recreate the pre-0006 schema, seed a typed log, then replay 0006
        con.execute("ALTER TABLE log RENAME COLUMN tag TO type")
        con.execute("INSERT INTO log (node_id, type, body) VALUES (1, 'goal', 'keep me')")
        con.commit()
        mig = pathlib.Path(tmp_db.__file__).resolve().parent / "migrations" / "0006_rename_log_type_to_tag.sql"
        con.executescript(mig.read_text())
        con.commit()
        cols = {r["name"] for r in con.execute("PRAGMA table_info(log)")}
        assert "tag" in cols and "type" not in cols
        assert con.execute("SELECT body FROM log WHERE tag = 'goal'").fetchone()["body"] == "keep me"

    def test_existing_logs_read_as_untagged(self, cli, tmp_db):
        """Back-compat: a plain `wl log` writes a row whose tag is NULL."""
        cli("add", "t", "-k", "task")
        cli("log", "1", "plain note")
        con = tmp_db.db_connect()
        row = con.execute("SELECT tag FROM log WHERE node_id = 1").fetchone()
        assert row["tag"] is None

    def test_metric_requires_log_id(self, cli, tmp_db):
        """log_id is NOT NULL — a datapoint must have a log carrier."""
        cli("add", "t", "-k", "task")
        con = tmp_db.db_connect()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO metric (node_id, tag, value_num) VALUES (1, 'glucose', 5.4)"
            )

    def test_metric_value_check_rejects_empty(self, cli, tmp_db):
        """CHECK: a metric must carry value_num or value_text (markers store 1)."""
        cli("add", "t", "-k", "task")
        cli("log", "1", "carrier")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO metric (log_id, node_id, tag) VALUES (?, 1, 'checkin')",
                (log_id,),
            )

    def test_metric_node_id_trigger_corrects_wrong_value(self, cli, tmp_db):
        """metric.node_id has no FK; a trigger forces it to the carrier log's node."""
        cli("add", "real owner", "-k", "task")   # node 1
        cli("add", "other node", "-k", "task")   # node 2
        cli("log", "1", "carrier on node 1")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        # deliberately pass the WRONG node_id (2); the trigger must rewrite it to 1
        con.execute(
            "INSERT INTO metric (log_id, node_id, tag, value_num) VALUES (?, 2, 'glucose', 5.4)",
            (log_id,),
        )
        con.commit()
        got = con.execute("SELECT node_id FROM metric").fetchone()["node_id"]
        assert got == 1

    def test_metric_node_id_guard_blocks_direct_desync(self, cli, tmp_db):
        """A direct UPDATE of metric.node_id is snapped back to the carrier log's node."""
        cli("add", "real owner", "-k", "task")   # node 1
        cli("add", "other node", "-k", "task")    # node 2
        cli("log", "1", "carrier on node 1")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        con.execute(
            "INSERT INTO metric (log_id, node_id, tag, value_num) VALUES (?, 1, 'glucose', 5.4)",
            (log_id,),
        )
        con.commit()
        mid = con.execute("SELECT id FROM metric").fetchone()["id"]
        # try to desync node_id directly → guard trigger snaps it back to 1
        con.execute("UPDATE metric SET node_id = 2 WHERE id = ?", (mid,))
        con.commit()
        assert con.execute("SELECT node_id FROM metric WHERE id = ?", (mid,)).fetchone()["node_id"] == 1

    def test_log_reparent_moves_metrics(self, cli, tmp_db):
        """Re-parenting a log updates its metrics' denormalized node_id."""
        cli("add", "from", "-k", "task")   # node 1
        cli("add", "to", "-k", "task")     # node 2
        cli("log", "1", "carrier")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        con.execute(
            "INSERT INTO metric (log_id, node_id, tag, value_num) VALUES (?, 1, 'glucose', 5.4)",
            (log_id,),
        )
        con.commit()
        con.execute("UPDATE log SET node_id = 2 WHERE id = ?", (log_id,))
        con.commit()
        assert con.execute("SELECT node_id FROM metric").fetchone()["node_id"] == 2

    def test_metric_cascades_when_node_deleted(self, cli, tmp_db):
        """No direct node FK, but deleting a node still cleans up its metrics
        via the log_id → log → node cascade chain."""
        cli("add", "t", "-k", "task")
        cli("log", "1", "carrier")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        con.execute(
            "INSERT INTO metric (log_id, node_id, tag, value_num) VALUES (?, 1, 'glucose', 5.4)",
            (log_id,),
        )
        con.commit()
        con.execute("DELETE FROM node WHERE id = 1")
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric").fetchone()[0] == 0

    def test_metric_cascades_when_log_deleted(self, cli, tmp_db):
        """Deleting the carrier log removes its metrics (ON DELETE CASCADE)."""
        cli("add", "t", "-k", "task")
        cli("log", "1", "carrier")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        con.execute(
            "INSERT INTO metric (log_id, node_id, tag, value_num, unit) VALUES (?, 1, 'glucose', 5.4, 'mmol/L')",
            (log_id,),
        )
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric").fetchone()[0] == 1
        con.execute("DELETE FROM log WHERE id = ?", (log_id,))
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric").fetchone()[0] == 0

    def test_metric_allows_multiple_per_day(self, cli, tmp_db):
        """No UNIQUE constraint: a node can carry many metrics (e.g. CGM ~288/day)."""
        cli("add", "t", "-k", "task")
        cli("log", "1", "carrier")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        for v in (5.1, 5.4, 6.0):
            con.execute(
                "INSERT INTO metric (log_id, node_id, tag, value_num, at) "
                "VALUES (?, 1, 'glucose', ?, '2026-06-04')",
                (log_id, v),
            )
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric WHERE tag='glucose'").fetchone()[0] == 3


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
