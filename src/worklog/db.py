"""SQLite connection and migration runner for worklog.

Functions take their DB path / migrations directory as arguments instead of
reading module-level globals — `cli.py` keeps the `DB_PATH` / `MIGRATIONS_DIR`
globals (because `main()` mutates `DB_PATH` per the `--db` flag) and passes
them in via thin wrappers.

This keeps the schema-upgrade logic isolated and importable from anywhere
that already has a path, without depending on cli.py's module state.
"""
from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import os
import shutil
import sqlite3
from pathlib import Path


def is_source_checkout() -> bool:
    """True when worklog is imported from its own source tree (an editable / dev checkout), not an
    installed copy — detected by repo markers (`.git` + `pyproject.toml`) at the checkout root two
    levels above the package (`<root>/src/worklog/db.py` → `<root>`). A pip/uv wheel unpacked into
    site-packages has no such markers → False; the prod uv-tool build is a *copy* of the source
    (installed from a local dir), so it also returns False. Used to fail-closed a dev build that
    would otherwise silently migrate/corrupt the real DB (the recurring 2026-07 incident: an
    unreleased migration in the working tree auto-applied to the live worklog.db)."""
    root = Path(__file__).resolve().parents[2]
    return (root / ".git").is_dir() and (root / "pyproject.toml").is_file()


def _guard_source_build_default_db(db_path: Path) -> None:
    """Fail-closed seatbelt (called from `db_connect`, so EVERY command that opens the DB hits it —
    including `wl migrate`): a dev/source build must never SILENTLY touch the real DB. When worklog
    runs from its own checkout (`is_source_checkout()`) AND the resolved path is the DEFAULT prod DB
    with no `$WORKLOG_DB` opt-in, abort before the file is created / opened / migrated — a working
    tree carrying an unreleased migration would otherwise auto-apply it to the live worklog.db (the
    2026-07 incident that corrupted real data 3× in a day). An installed build (prod uv-tool / wheel)
    is not a source checkout → unaffected. Opt in with `$WORKLOG_DB` (even pointed at the prod path);
    dev/testing should point it at a scratch DB."""
    if not is_source_checkout() or os.environ.get("WORKLOG_DB"):
        return
    from .xdg import _xdg_data_home
    default = (_xdg_data_home() / "worklog" / "worklog.db").resolve()
    if db_path.resolve() != default:
        return
    root = Path(__file__).resolve().parents[2]
    raise SystemExit(
        f"✗ refusing to use the real worklog DB from a dev/source build ({root}).\n"
        f"  Running the working tree against {default} risks auto-applying an unreleased migration\n"
        f"  and corrupting your live data.\n"
        f"  → for dev/testing: export WORKLOG_DB=/tmp/wl-dev.db   (a scratch DB)\n"
        f"  → to really use the live DB: export WORKLOG_DB={default}")


def _db_file_path(con: sqlite3.Connection):
    """The on-disk file backing the 'main' database, or None for an in-memory / temp DB."""
    for _seq, name, file in con.execute("PRAGMA database_list"):
        if name == "main":
            return file or None
    return None


def _backup_before_migrate(con: sqlite3.Connection, current: int, pending: int):
    """Copy the DB file to a same-dir backup BEFORE applying migrations, so a bad migration can't
    lose data irrecoverably (#651). Only for an EXISTING db (`user_version` > 0 — a fresh init has
    no data to protect) backed by a real file, and only when migrations are actually pending.
    Backup name carries the source version: `<db>.pre-v<current>.bak`. Returns the backup path,
    or None when skipped. A copy failure raises (we must NOT migrate an unbackuppable real db)."""
    if current <= 0 or pending <= 0:
        return None
    path = _db_file_path(con)
    if not path or not os.path.exists(path):
        return None   # in-memory / temp / not-yet-on-disk: nothing to snapshot
    bak = f"{path}.pre-v{current}.bak"
    shutil.copy2(path, bak)   # no write txn is open yet, so this is a clean rollback-journal snapshot
    return bak


@contextlib.contextmanager
def _migration_lock(db_file: str):
    """Exclusive cross-process lock held only while migrations apply, so two worklog processes
    can't migrate the same DB at once. The lock file sits beside the DB; LOCK_EX blocks until any
    other migrator finishes — after which the caller re-reads user_version (it may have just been
    migrated by that other process). SQLite's own txn lock already serializes the writes; this
    just turns a mid-migration race into a clean wait."""
    f = open(f"{db_file}.migrate.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _backup_after_migrate(con: sqlite3.Connection, version: int):
    """Snapshot the DB right AFTER a successful upgrade — a known-good FORWARD restore point
    (the ``pre-v<N>.bak`` is the rollback point; this ``post-v<N>.bak`` is the upgraded one).
    Best-effort: a copy failure must NOT fail an already-applied migration. Returns path or None."""
    path = _db_file_path(con)
    if not path or not os.path.exists(path):
        return None
    bak = f"{path}.post-v{version}.bak"
    try:
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


def db_connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection (creating parent dirs if missing). Row factory =
    sqlite3.Row. Foreign-key enforcement is intentionally left OFF: the
    tables are decoupled and removal is soft (a `deleted_at` tombstone), so there's
    no cascade to enforce — the app keeps consistency (queries.soft_delete_*). The
    schema keeps its REFERENCES / ON DELETE clauses as documentation; they're inert."""
    _guard_source_build_default_db(db_path)   # dev/source build never silently opens the real DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = OFF")
    return con


def migration_files(migrations_dir: Path) -> list[Path]:
    """Return migration files sorted by NNNN_ numeric prefix. Both ``.sql`` and ``.py``
    migrations are picked up (a ``.py`` migration runs app logic raw SQL can't express,
    e.g. writing reserved props through the validator). Skip filenames without a numeric
    prefix (e.g. __init__.py)."""
    if not migrations_dir.exists():
        return []
    files = []
    for p in (*migrations_dir.glob("*.sql"), *migrations_dir.glob("*.py")):
        prefix = p.stem.split("_", 1)[0]
        if prefix.isdigit():
            files.append((int(prefix), p))
    files.sort(key=lambda x: x[0])
    return [p for _, p in files]


def db_version(con: sqlite3.Connection) -> int:
    """The highest migration number applied to this DB (`PRAGMA user_version`)."""
    return con.execute("PRAGMA user_version").fetchone()[0]


def _apply_py_migration(con: sqlite3.Connection, path: Path, n: int) -> None:
    """Load a NNNN_*.py migration and run its ``migrate(con)`` in ONE atomic transaction,
    then bump user_version. The module must define ``migrate(con)``; it runs statements only
    and must NOT commit/rollback — this function owns the transaction so a mid-migration
    failure rolls the whole thing back. Loaded by file path (not package import), so the
    migrations dir needs no __init__ wiring."""
    spec = importlib.util.spec_from_file_location(f"_wl_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Python migration {path.name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    migrate = getattr(mod, "migrate", None)
    if not callable(migrate):
        raise RuntimeError(f"Python migration {path.name} has no migrate(con) entrypoint")
    # Drive the transaction explicitly in autocommit mode, so a .py migration is atomic the same
    # way the .sql path is (executescript wraps its own BEGIN/COMMIT). isolation_level=None stops
    # Python's sqlite3 from auto-managing transactions and clashing with our explicit BEGIN.
    prev = con.isolation_level
    con.isolation_level = None
    try:
        con.execute("BEGIN")
        try:
            migrate(con)
            con.execute(f"PRAGMA user_version = {n}")
            con.execute("COMMIT")
        except Exception:
            if con.in_transaction:   # BEGIN may have failed before a txn opened; don't mask that error
                con.execute("ROLLBACK")
            raise
    finally:
        con.isolation_level = prev


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
    if not [p for p in files if int(p.stem.split("_", 1)[0]) > current]:
        return []   # nothing pending: no lock, no backup — keep the common (every-command) path cheap
    was_fresh = current == 0   # a fresh init has no data to snapshot (neither pre nor post)
    # Hold an exclusive cross-process lock for the whole migration run (only when something is
    # actually pending), so two worklog processes can't migrate the same DB at once.
    db_file = _db_file_path(con)
    lock = _migration_lock(db_file) if db_file else contextlib.nullcontext()
    applied = []
    with lock:
        # Re-read after acquiring the lock: another process may have migrated while we waited.
        current = db_version(con)
        pending = [p for p in files if int(p.stem.split("_", 1)[0]) > current]
        if not pending:
            return []
        # safety: snapshot an existing DB before touching it, so a bad migration is recoverable (#651)
        bak = _backup_before_migrate(con, current, len(pending))
        if bak:
            print(f"↳ backed up DB → {os.path.basename(bak)} before applying {len(pending)} migration(s)")
        for path in files:
            n = int(path.stem.split("_", 1)[0])
            if n <= current:
                continue
            try:
                if path.suffix == ".py":
                    # Python migration: app logic raw SQL can't express (e.g. writing reserved
                    # props through the validator). migrate(con) runs inside one transaction here.
                    _apply_py_migration(con, path, n)
                else:
                    # Wrap the whole file (plus the version bump) in one transaction so
                    # a mid-script failure rolls everything back instead of leaving a
                    # half-applied schema. executescript() COMMITs pending work first,
                    # then runs this BEGIN…COMMIT atomically.
                    sql = path.read_text(encoding="utf-8")
                    con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {n};\nCOMMIT;")
            except Exception:
                con.rollback()
                raise
            if verbose:
                print(f"✓ applied migration {path.stem}")
            applied.append(path)
        # Post-upgrade, STILL UNDER THE LOCK: snapshot the known-good result + announce before any
        # other process can acquire the DB and write to it (a snapshot taken after releasing the
        # lock could capture a concurrent writer's half-written pages). Skip a fresh init (empty
        # DB, nothing to protect). Backup is best-effort — never fail an already-applied run.
        if applied and not was_fresh:
            post = _backup_after_migrate(con, db_version(con))
            if post:
                print(f"↳ post-upgrade snapshot → {os.path.basename(post)}")
            print(f"✓ migrations complete — DB now at v{db_version(con)}")
    return applied


def ensure_db(db_path: Path, migrations_dir: Path) -> None:
    """Open the DB (creating the file if missing) and run any pending migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = db_connect(db_path)
    try:
        run_migrations(con, migrations_dir)
    finally:
        con.close()
