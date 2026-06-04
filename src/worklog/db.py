"""SQLite connection and migration runner for worklog.

Functions take their DB path / migrations directory as arguments instead of
reading module-level globals — `cli.py` keeps the `DB_PATH` / `MIGRATIONS_DIR`
globals (because `main()` mutates `DB_PATH` per the `--db` flag) and passes
them in via thin wrappers.

This keeps the schema-upgrade logic isolated and importable from anywhere
that already has a path, without depending on cli.py's module state.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def db_connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection (creating parent dirs if missing). Row factory =
    sqlite3.Row, foreign-key enforcement enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def migration_files(migrations_dir: Path) -> list[Path]:
    """Return migration files sorted by NNNN_ numeric prefix; skip filenames
    without a numeric prefix."""
    if not migrations_dir.exists():
        return []
    files = []
    for p in migrations_dir.glob("*.sql"):
        prefix = p.stem.split("_", 1)[0]
        if prefix.isdigit():
            files.append((int(prefix), p))
    files.sort(key=lambda x: x[0])
    return [p for _, p in files]


def db_version(con: sqlite3.Connection) -> int:
    """The highest migration number applied to this DB (`PRAGMA user_version`)."""
    return con.execute("PRAGMA user_version").fetchone()[0]


def run_migrations(con: sqlite3.Connection, migrations_dir: Path, verbose: bool = False) -> list[Path]:
    """Apply every migration whose number > `PRAGMA user_version`.

    Each migration runs as ONE atomic transaction (the script is wrapped in
    BEGIN/COMMIT, with the `user_version` bump inside it). SQLite DDL is
    transactional, so a mid-script failure rolls the whole file back — no
    half-applied schema. `user_version` is bumped per file, so a failure
    leaves the DB at the last fully-applied number — re-run after fixing.

    Why the explicit BEGIN/COMMIT wrap: Python's `executescript()` first
    COMMITs any pending transaction, then runs statements in autocommit, so
    each DDL would commit immediately and a later failure would leave earlier
    statements applied (verified). Wrapping the script in its own transaction
    is what makes the file atomic.

    Downgrade guard: if `PRAGMA user_version` exceeds the highest migration
    number shipped, the DB was written by a newer worklog and must not be
    touched by older code — abort with a clear message.
    """
    files = migration_files(migrations_dir)
    max_n = max((int(p.stem.split("_", 1)[0]) for p in files), default=0)
    current = db_version(con)
    if current > max_n:
        raise SystemExit(
            f"✗ DB at user_version={current} but this worklog build only ships "
            f"migrations up to {max_n}. The DB was written by a newer version; "
            f"upgrade worklog (e.g. `pip install --upgrade pyworklog`) and retry."
        )
    applied = []
    for path in files:
        n = int(path.stem.split("_", 1)[0])
        if n <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            # Wrap the whole file (plus the version bump) in one transaction so
            # a mid-script failure rolls everything back instead of leaving a
            # half-applied schema. executescript() COMMITs pending work first,
            # then runs this BEGIN…COMMIT atomically.
            con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {n};\nCOMMIT;")
        except Exception:
            con.rollback()
            raise
        if verbose:
            print(f"✓ applied migration {path.stem}")
        applied.append(path)
    return applied


def ensure_db(db_path: Path, migrations_dir: Path) -> None:
    """Open the DB (creating the file if missing) and run any pending migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = db_connect(db_path)
    try:
        run_migrations(con, migrations_dir)
    finally:
        con.close()
