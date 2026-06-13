"""Sidecar vector store for semantic search, backed by LanceDB.

A SEPARATE on-disk store (``<db>.lancedb/`` dir, see xdg._resolve_vec_db_path)
holds one embedding per node, mirroring the structured/vector split of larger
local stacks. Kept out of the main SQLite DB so it can be deleted/rebuilt freely
(it's a derived index, never the source of truth) and never bloats the worklog
DB or its migrations.

Why LanceDB over packing float32 BLOBs into SQLite: `wl` starts a fresh process
on every invocation, so the dominant cost is per-invocation startup, not the
search algorithm. A benchmark over real vectors showed a SQLite-blob store must
load+unpack *all* vectors on each `wl query` (linear: ~0.2s at 5k rows, ~6s at
100k), while LanceDB memory-maps its columnar
files and opens in ~1ms regardless of size, with an optional ANN index for
large stores. LanceDB is an OPTIONAL extra (`pip install pyworklog[semantic]`);
absent it, connect() raises VectorStoreError with an install hint."""
from __future__ import annotations

from pathlib import Path

TABLE = "vec"


class VectorStoreError(RuntimeError):
    """The vector store can't be used (e.g. the 'semantic' extra isn't installed)."""


def _lancedb():
    """Import lancedb or raise a VectorStoreError pointing at the extra."""
    try:
        import lancedb
    except ImportError as e:
        raise VectorStoreError(
            "semantic search needs the 'semantic' extra — install it with "
            "`pip install 'pyworklog[semantic]'` (pulls in lancedb)."
        ) from e
    return lancedb


def connect(path):
    """Open (creating the dir if needed) the sidecar LanceDB at ``path``."""
    lancedb = _lancedb()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(path))


def _table_names(db):
    """Existing table names as a plain list, across lancedb versions: newer
    `list_tables()` returns a response object with a `.tables` field, older
    exposes `table_names()` -> list[str]."""
    try:
        res = db.list_tables()
    except AttributeError:
        res = db.table_names()
    return list(getattr(res, "tables", res))


def _table(db):
    """The 'vec' table, or None if it hasn't been created yet (empty store)."""
    if TABLE not in _table_names(db):
        return None
    return db.open_table(TABLE)


def upsert(db, rows):
    """Insert/replace vectors keyed by node_id. Each row =
    (node_id, title, status, priority, model, dim, vec_list)."""
    data = [
        {"node_id": nid, "title": title or "", "status": st or "", "priority": pr or "",
         "model": model, "dim": dim, "vector": list(vec)}
        for (nid, title, st, pr, model, dim, vec) in rows
    ]
    if not data:
        return
    tbl = _table(db)
    if tbl is None:
        db.create_table(TABLE, data=data)
        return
    (tbl.merge_insert("node_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(data))


def clear(db):
    if TABLE in _table_names(db):
        db.drop_table(TABLE)


def load(db):
    """All stored rows as plain dicts (node_id/title/status/priority/model/dim/vector)."""
    tbl = _table(db)
    if tbl is None:
        return []
    return tbl.to_arrow().to_pylist()


def index_model(db):
    """(model, dim) the index was built with, or None if empty — lets the caller
    detect a backend/model change and force a reindex instead of mixing spaces."""
    rows = load(db)
    if not rows:
        return None
    return (rows[0]["model"], rows[0]["dim"])


def search(db, query_vec, k=10, threshold=None):
    """Cosine search. Returns up to k dicts {node_id,title,status,priority,score}
    sorted by score (cosine similarity, = 1 − lance distance) desc, dropping
    anything below ``threshold`` when given."""
    tbl = _table(db)
    if tbl is None:
        return []
    raw = tbl.search(list(query_vec)).metric("cosine").limit(k).to_list()
    hits = []
    for r in raw:
        score = 1.0 - r["_distance"]
        if threshold is not None and score < threshold:
            continue
        hits.append({
            "node_id": r["node_id"], "title": r["title"], "status": r["status"],
            "priority": r["priority"] or None, "score": score,
        })
    return hits
