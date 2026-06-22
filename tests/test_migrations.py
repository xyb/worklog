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

    def test_backs_up_existing_db_before_migrating(self, tmp_path):
        """An existing DB (user_version > 0) is snapshotted to <db>.pre-v<N>.bak before pending
        migrations apply — so a bad migration is recoverable (#651)."""
        from worklog import db
        import sqlite3
        mig = tmp_path / "migs"; mig.mkdir()
        (mig / "0001_init.sql").write_text("CREATE TABLE t (x);")
        dbf = tmp_path / "x.db"
        con = db.db_connect(dbf); db.run_migrations(con, mig)
        con.execute("INSERT INTO t VALUES (1)"); con.commit(); con.close()
        assert not list(tmp_path.glob("*.bak"))          # fresh v0→1 init: no data to protect
        (mig / "0002_more.sql").write_text("CREATE TABLE t2 (y);")
        con = db.db_connect(dbf); db.run_migrations(con, mig); con.close()
        baks = sorted(p.name for p in tmp_path.glob("*.bak"))
        # pre-v1 = rollback snapshot (before applying 0002); post-v2 = the known-good upgraded result
        assert baks == ["x.db.post-v2.bak", "x.db.pre-v1.bak"]
        b = sqlite3.connect(tmp_path / "x.db.pre-v1.bak")  # the PRE snapshot is the OLD version + data
        assert b.execute("PRAGMA user_version").fetchone()[0] == 1
        assert b.execute("SELECT x FROM t").fetchone()[0] == 1
        b.close()

    def test_no_backup_on_fresh_init_or_noop(self, tmp_path):
        from worklog import db
        mig = tmp_path / "migs"; mig.mkdir()
        (mig / "0001_init.sql").write_text("CREATE TABLE t (x);")
        dbf = tmp_path / "x.db"
        con = db.db_connect(dbf); db.run_migrations(con, mig)      # fresh: v0 → no backup
        con.close()
        con = db.db_connect(dbf); db.run_migrations(con, mig)      # no pending → no backup
        con.close()
        assert not list(tmp_path.glob("*.bak"))

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

    def test_python_migration_runs_and_bumps_version(self, tmp_path):
        """A NNNN_*.py migration runs its migrate(con) and bumps user_version (app logic
        a .sql migration can't express, e.g. writing reserved props through the validator)."""
        from worklog import db
        mig = tmp_path / "migrations"; mig.mkdir()
        (mig / "0001_init.sql").write_text("CREATE TABLE t (x);")
        (mig / "0002_seed.py").write_text(
            "def migrate(con):\n    con.execute(\"INSERT INTO t (x) VALUES (42)\")\n"
        )
        con = db.db_connect(tmp_path / "py.db")
        try:
            db.run_migrations(con, mig)
            assert db.db_version(con) == 2
            assert con.execute("SELECT x FROM t").fetchone()[0] == 42
        finally:
            con.close()

    def test_sql_and_py_migrations_interleave_in_order(self, tmp_path):
        """.sql and .py migrations apply together in NNNN numeric order."""
        from worklog import db
        mig = tmp_path / "migrations"; mig.mkdir()
        (mig / "0001_a.sql").write_text("CREATE TABLE steps (s TEXT);")
        (mig / "0002_b.py").write_text(
            "def migrate(con):\n    con.execute(\"INSERT INTO steps VALUES ('py-2')\")\n"
        )
        (mig / "0003_c.sql").write_text("INSERT INTO steps VALUES ('sql-3');")
        con = db.db_connect(tmp_path / "mix.db")
        try:
            db.run_migrations(con, mig)
            got = [r["s"] for r in con.execute("SELECT s FROM steps ORDER BY rowid")]
            assert got == ["py-2", "sql-3"]
            assert db.db_version(con) == 3
        finally:
            con.close()

    def test_python_migration_rolls_back_on_error(self, tmp_path):
        """A .py migration that raises mid-way rolls back its whole transaction; user_version
        stays at the last good number and its partial writes vanish (atomic like the .sql path)."""
        from worklog import db
        mig = tmp_path / "migrations"; mig.mkdir()
        (mig / "0001_init.sql").write_text("CREATE TABLE t (x);")
        (mig / "0002_boom.py").write_text(
            "def migrate(con):\n"
            "    con.execute(\"INSERT INTO t (x) VALUES (1)\")\n"
            "    raise RuntimeError('boom')\n"
        )
        con = db.db_connect(tmp_path / "pyfail.db")
        try:
            with pytest.raises(RuntimeError):
                db.run_migrations(con, mig)
            assert db.db_version(con) == 1                                  # 0002 rolled back → stays at 1
            assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0  # partial insert gone
        finally:
            con.close()

    def test_python_migration_missing_entrypoint_errors(self, tmp_path):
        """A .py migration without migrate(con) is a clear error, not a silent skip."""
        from worklog import db
        mig = tmp_path / "migrations"; mig.mkdir()
        (mig / "0001_noentry.py").write_text("X = 1  # no migrate() here\n")
        con = db.db_connect(tmp_path / "noentry.db")
        try:
            with pytest.raises(Exception) as exc:
                db.run_migrations(con, mig)
            assert "migrate" in str(exc.value).lower()
            assert db.db_version(con) == 0
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
            "deleted_at",  # soft-delete tombstone (migration 0008)
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
        cli("add", "t")  # node 1
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
        cli("add", "t")
        cli("log", "1", "plain note")
        con = tmp_db.db_connect()
        row = con.execute("SELECT tag FROM log WHERE node_id = 1").fetchone()
        assert row["tag"] is None

    def test_metric_requires_log_id(self, cli, tmp_db):
        """log_id is NOT NULL — a datapoint must have a log carrier."""
        cli("add", "t")
        con = tmp_db.db_connect()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO metric (node_id, tag, value_num) VALUES (1, 'glucose', 5.4)"
            )

    def test_metric_value_check_rejects_empty(self, cli, tmp_db):
        """CHECK: a metric must carry value_num or value_text (markers store 1)."""
        cli("add", "t")
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
        cli("add", "real owner")   # node 1
        cli("add", "other node")   # node 2
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
        cli("add", "real owner")   # node 1
        cli("add", "other node")    # node 2
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
        cli("add", "from")   # node 1
        cli("add", "to")     # node 2
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

    def test_soft_delete_node_tombstones_its_metrics(self, cli, tmp_db):
        """FK enforcement is off; soft-deleting a node tombstones its metrics
        via the app-level cascade (queries.soft_delete_node), not an FK CASCADE."""
        from worklog import graph as q
        cli("add", "t")
        cli("log", "1", "carrier")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        con.execute(
            "INSERT INTO metric (log_id, node_id, tag, value_num) VALUES (?, 1, 'glucose', 5.4)",
            (log_id,),
        )
        con.commit()
        # a raw node DELETE no longer cascades (FK off) — the app does it as a soft-delete
        q.soft_delete_node(con, 1)
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric WHERE deleted_at IS NULL").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM metric").fetchone()[0] == 1  # tombstoned, not removed

    def test_soft_delete_log_tombstones_its_metrics(self, cli, tmp_db):
        """Soft-deleting a carrier log tombstones its metrics (the old metric.log_id
        CASCADE, now app-level via queries.soft_delete_log)."""
        from worklog import graph as q
        cli("add", "t")
        cli("log", "1", "carrier")
        con = tmp_db.db_connect()
        log_id = con.execute("SELECT id FROM log WHERE node_id = 1").fetchone()["id"]
        con.execute(
            "INSERT INTO metric (log_id, node_id, tag, value_num, unit) VALUES (?, 1, 'glucose', 5.4, 'mmol/L')",
            (log_id,),
        )
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric WHERE deleted_at IS NULL").fetchone()[0] == 1
        q.soft_delete_log(con, log_id)
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM metric WHERE deleted_at IS NULL").fetchone()[0] == 0

    def test_metric_allows_multiple_per_day(self, cli, tmp_db):
        """No UNIQUE constraint: a node can carry many metrics (e.g. CGM ~288/day)."""
        cli("add", "t")
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


class TestMigration0007UTC:
    """0007: rename scheduled_at/deadline_at → *_date; convert *_at instants local(+8)→UTC."""

    def _replay_0007(self, tmp_db, con):
        import pathlib
        # reconstruct the pre-0007 schema (columns still named *_at)
        con.execute("ALTER TABLE node RENAME COLUMN scheduled_date TO scheduled_at")
        con.execute("ALTER TABLE node RENAME COLUMN deadline_date TO deadline_at")
        con.commit()
        mig = pathlib.Path(tmp_db.__file__).resolve().parent / "migrations" / "0007_utc_timestamps.sql"
        con.executescript(mig.read_text())
        con.commit()

    def test_renames_scheduled_deadline_to_date(self, cli, tmp_db):
        cli("add", "t")
        con = tmp_db.db_connect()
        self._replay_0007(tmp_db, con)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(node)")}
        assert "scheduled_date" in cols and "scheduled_at" not in cols
        assert "deadline_date" in cols and "deadline_at" not in cols

    def test_converts_full_instants_minus_8h(self, cli, tmp_db):
        cli("add", "t")  # node 1
        con = tmp_db.db_connect()
        # seed pre-v7 local-time instants on the *_at columns
        con.execute("UPDATE node SET created_at='2026-06-01 08:00:00', closed_at='2026-06-01 09:30:00' WHERE id=1")
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-01 08:00:00', 'has-time')")
        con.commit()
        self._replay_0007(tmp_db, con)
        assert con.execute("SELECT created_at FROM node WHERE id=1").fetchone()[0] == "2026-06-01 00:00:00"
        assert con.execute("SELECT closed_at FROM node WHERE id=1").fetchone()[0] == "2026-06-01 01:30:00"
        assert con.execute("SELECT logged_at FROM log WHERE body='has-time'").fetchone()[0] == "2026-06-01 00:00:00"

    def test_leaves_bare_dates_untouched(self, cli, tmp_db):
        cli("add", "t")
        con = tmp_db.db_connect()
        # a date-only logged_at (from `wl log --date`) and a checkin metric.at backfilled
        # as a bare date must NOT be shifted (subtracting 8h would roll them a day back)
        con.execute("INSERT INTO log (node_id, logged_at, body) VALUES (1, '2026-06-01', 'bare-date')")
        con.commit()
        self._replay_0007(tmp_db, con)
        assert con.execute("SELECT logged_at FROM log WHERE body='bare-date'").fetchone()[0] == "2026-06-01"


class TestDbHelpers:
    """Edge paths in worklog.db: in-memory connections, missing dirs, non-numeric files."""

    def test_db_file_path_none_for_in_memory(self):
        from worklog import db
        con = sqlite3.connect(":memory:")
        # in-memory main DB has an empty file string → normalized to None
        assert db._db_file_path(con) is None
        con.close()

    def test_backup_skipped_for_in_memory_db(self):
        from worklog import db
        con = sqlite3.connect(":memory:")
        con.execute("PRAGMA user_version = 1")
        # current>0 and pending>0, but no on-disk file → nothing to snapshot, returns None
        assert db._backup_before_migrate(con, current=1, pending=2) is None
        con.close()

    def test_migration_files_empty_when_dir_missing(self, tmp_path):
        from worklog import db
        assert db.migration_files(tmp_path / "does-not-exist") == []

    def test_migration_files_skips_non_numeric_prefix(self, tmp_path):
        from worklog import db
        mig = tmp_path / "migs"; mig.mkdir()
        (mig / "0001_init.sql").write_text("CREATE TABLE a (x);")
        (mig / "notes_helper.sql").write_text("-- not a migration, no numeric prefix")
        files = db.migration_files(mig)
        assert [p.name for p in files] == ["0001_init.sql"]   # the non-numeric one is skipped


class TestMigration0011DropKind:
    """0011 (Python migration): backfill type.* from kind, verify round-trip, then DROP COLUMN kind."""

    def _v10_db(self, tmp_path):
        """Bring a fresh DB up to v10 (kind column still present) using only 0001–0010."""
        import shutil, pathlib
        from worklog import db
        real = pathlib.Path(db.__file__).parent / "migrations"
        migs = tmp_path / "migs"; migs.mkdir()
        for p in db.migration_files(real):
            if int(p.stem.split("_", 1)[0]) <= 10:
                shutil.copy(p, migs / p.name)
        con = db.db_connect(tmp_path / "t.db")
        db.run_migrations(con, migs)
        assert db.db_version(con) == 10
        return con

    def _mig11(self):
        import pathlib
        from worklog import db
        return pathlib.Path(db.__file__).parent / "migrations" / "0011_drop_kind_column.py"

    def test_backfills_then_drops_kind_column(self, tmp_path):
        from worklog import db, node_types as nt, queries
        con = self._v10_db(tmp_path)
        con.execute("INSERT INTO node (title, kind, created_at) VALUES ('Site','project','2026-01-01')")
        con.execute("INSERT INTO node (title, kind, created_at) VALUES ('todo','task','2026-01-01')")
        con.execute("INSERT INTO node (title, kind, created_at) VALUES ('2026-W01','week','2026-01-01')")
        con.commit()
        db._apply_py_migration(con, self._mig11(), 11)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(node)")}
        assert "kind" not in cols                                  # column dropped
        idx = [r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_node_kind'")]
        assert idx == []                                           # index dropped
        assert db.db_version(con) == 11
        pid = con.execute("SELECT id FROM node WHERE title='Site'").fetchone()["id"]
        wid = con.execute("SELECT id FROM node WHERE title='2026-W01'").fetchone()["id"]
        assert queries.node_type_from_props(queries.node_props(con, pid)) == "project"   # derived from type.para
        assert queries.node_type_from_props(queries.node_props(con, wid)) == "week"      # derived from type.date
        con.close()

    def test_aborts_and_keeps_kind_on_roundtrip_failure(self, tmp_path):
        from worklog import db
        con = self._v10_db(tmp_path)
        con.execute("INSERT INTO node (title, kind, created_at) VALUES ('2026-W01','week','2026-01-01')")
        wid = con.execute("SELECT id FROM node WHERE title='2026-W01'").fetchone()["id"]
        # a conflicting reserved prop makes the week derive to 'project' → round-trip fails
        con.execute("INSERT INTO prop (node_id, key, value) VALUES (?,?,?)", (wid, "type.para", "project"))
        con.commit()
        with pytest.raises(RuntimeError, match="round-trip"):
            db._apply_py_migration(con, self._mig11(), 11)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(node)")}
        assert "kind" in cols                # rolled back: column intact
        assert db.db_version(con) == 10      # version not bumped
        con.close()
