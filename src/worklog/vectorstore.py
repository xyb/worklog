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
    """Add chunk rows (the index is rebuilt wholesale by reindex → clear()+upsert(),
    so this only ever appends — no per-row merge). Each row is a dict with
    node_id / title / status / priority / model / dim / vector / chunk_text / chunk_kind.
    A node contributes MANY rows (a head chunk + one per log), so node_id is not unique."""
    data = [
        {"node_id": r["node_id"], "title": r["title"] or "", "status": r["status"] or "",
         "priority": r["priority"] or "", "model": r["model"], "dim": r["dim"],
         "vector": list(r["vector"]), "chunk_text": r["chunk_text"] or "",
         "chunk_kind": r["chunk_kind"] or ""}
        for r in rows
    ]
    if not data:
        return
    tbl = _table(db)
    if tbl is None:
        db.create_table(TABLE, data=data)
    else:
        tbl.add(data)


def clear(db):
    if TABLE in _table_names(db):
        db.drop_table(TABLE)


def load(db):
    """All stored chunk rows as plain dicts."""
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
    """Chunk-level cosine search with **max-pooling to nodes**: score every chunk,
    then collapse to one hit per node keyed on its single best (highest-cosine)
    chunk — so a node surfaces on the strength of its most relevant passage, never
    diluted by an averaged whole-node vector. Returns up to k node hits
    {node_id,title,status,priority,score,chunk_text,chunk_kind} (best chunk = the
    match reason), sorted by score desc, dropping nodes whose best chunk is below
    ``threshold``. All chunks are scored (limit = row count) so the per-node max is
    exact at worklog scale."""
    tbl = _table(db)
    if tbl is None:
        return []
    n = tbl.count_rows()
    if not n:
        return []
    raw = tbl.search(list(query_vec)).metric("cosine").limit(n).to_list()
    best = {}  # node_id -> best chunk hit so far
    for r in raw:
        score = 1.0 - r["_distance"]
        nid = r["node_id"]
        cur = best.get(nid)
        if cur is None or score > cur["score"]:
            best[nid] = {
                "node_id": nid, "title": r["title"], "status": r["status"],
                "priority": r["priority"] or None, "score": score,
                # .get: a store built by an older (node-level, pre-chunk) reindex lacks these
                # columns — degrade to no match-reason rather than crash; `wl reindex` upgrades it.
                "chunk_text": r.get("chunk_text", ""), "chunk_kind": r.get("chunk_kind", ""),
            }
    hits = [h for h in best.values() if threshold is None or h["score"] >= threshold]
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:k]
