"""Migration 0011: retire the ``node.kind`` column — the ``type.*`` / ``date.*`` namespace is now
the single source of node classification (readers derive kind from props; see node_types.py).

This is a Python migration (not .sql) because the backfill must write reserved props THROUGH the
validator (``_upsert_prop`` → ``validate_prop``), which a raw ``.sql`` file would bypass. Run inside
ONE transaction by ``db._apply_py_migration``: backfill type.*/date.* from kind, verify every
classified node round-trips, then drop the column + its index. A failed round-trip raises, which
rolls the whole migration back — the DB stays at v10 with ``kind`` intact, nothing half-applied.

Needs SQLite >= 3.35 for ``ALTER TABLE DROP COLUMN`` (guarded below).
"""
import re
import sys

from worklog import node_type_backfill as _bf


def migrate(con):
    # ALTER TABLE DROP COLUMN landed in SQLite 3.35.0 (2021-03). Guard so an old sqlite gives a
    # clear, actionable message instead of a cryptic syntax error mid-upgrade. Parse defensively
    # (extract digit runs) so an unusual build version string can't raise a raw ValueError and
    # defeat the friendly guard.
    raw = con.execute("SELECT sqlite_version()").fetchone()[0]
    ver = tuple(int(x) for x in re.findall(r"\d+", raw)[:3])
    if ver < (3, 35, 0):
        raise RuntimeError(
            "migration 0011 needs SQLite >= 3.35 for ALTER TABLE DROP COLUMN; this build links "
            f"{'.'.join(map(str, ver))}. Upgrade Python's sqlite3 (or the SQLite library) and retry."
        )
    # 1) Backfill type.*/date.* from kind, through the validated write path. commit=False: this
    #    migration owns the transaction (db._apply_py_migration), so backfill must not commit early.
    original = _bf.snapshot_kinds(con)        # {id: kind} captured BEFORE any write (see snapshot_kinds)
    _bf.backfill_node_types(con, commit=False)
    # 2) Hard gate: every KNOWN-kind node must derive back to its ORIGINAL kind. A mismatch means
    #    type.* doesn't losslessly represent it — abort, which rolls the whole migration back so the
    #    kind column is NOT dropped on top of an incomplete backfill.
    ok, mismatches, _retired, period_lost = _bf.verify_roundtrip(con, original)
    if not ok:
        raise RuntimeError(
            f"kind→type.* round-trip failed for {len(mismatches)} node(s) "
            f"(e.g. {mismatches[:3]}) — aborting migration; kind column left intact"
        )
    # Non-gating, but don't drop it silently: a legacy time node whose title carried no canonical
    # period (e.g. a week titled "本周冲刺") keeps its level but has no date.period, so it can't be
    # placed by any date-range query. Round-trip still passes (the level survives), so warn instead
    # of aborting — the user can `wl set <id> date.period <YYYY-Www>` to make it findable.
    if period_lost:
        print(f"  ⚠ {len(period_lost)} time node(s) had no canonical period in their title "
              f"(e.g. {period_lost[:3]}) — they keep their level but stay unplaceable by date-range "
              f"queries until you set date.period", file=sys.stderr)
    # 3) Drop the now-redundant column + its index. Readers already derive kind from type.* props
    #    (the column has been an unread cache since the reader cutover).
    con.execute("DROP INDEX IF EXISTS idx_node_kind")
    con.execute("ALTER TABLE node DROP COLUMN kind")
