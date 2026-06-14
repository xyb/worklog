"""Sidecar vector store for semantic search — a PLUGGABLE backend behind a small
module-function API (connect / upsert / clear / load / index_model / search).

A SEPARATE on-disk store (next to the worklog DB, see xdg._resolve_vec_db_path)
holds the chunk embeddings, mirroring the structured/vector split of larger local
stacks. Kept out of the main SQLite DB so it can be deleted/rebuilt freely (it's a
derived index, never the source of truth) and never bloats the worklog DB.

Two interchangeable backends, auto-selected by connect():

* **LanceDB** (``backend="lancedb"``) — the fast path, an embedded columnar store
  that memory-maps its files and opens in ~1ms regardless of size (vs. a linear
  load+unpack that grows with the store). Needs the optional 'semantic' extra
  (``pip install 'pyworklog[semantic]'``). Used automatically when importable.

* **SQLite** (``backend="sqlite"``) — a pure-stdlib fallback (sqlite3 + a Python
  cosine loop, no numpy, no compiled deps), so semantic search works on every
  Python 3.9–3.14 and every platform, including those with no lancedb wheel (Intel
  macOS, musl/Alpine, *BSD, 32-bit). Linear-scan at query time — fine at worklog
  scale (a few thousand chunks), slower than LanceDB at very large stores.

connect() prefers LanceDB and silently falls back to SQLite when it isn't
installed, so nothing the caller does changes — only forcing ``backend="lancedb"``
without the extra raises VectorStoreError."""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

TABLE = "vec"


class VectorStoreError(RuntimeError):
    """A requested backend can't be used (e.g. ``backend="lancedb"`` but the
    'semantic' extra isn't installed)."""


# --- backend selection / module-function API ---------------------------------
# connect() returns a backend INSTANCE; the rest of the module are thin dispatchers
# that call methods on it, so callers (worklog.commands.semantic) stay backend-agnostic.

def _lancedb_available():
    try:
        import lancedb  # noqa: F401
        return True
    except ImportError:
        return False


def connect(path, backend=None):
    """Open (creating dirs as needed) the sidecar store at ``path``.

    ``backend``: ``"lancedb"`` / ``"sqlite"`` to force one, or None (default) to
    auto-select — LanceDB if importable, else the SQLite fallback. Forcing
    ``"lancedb"`` without the extra raises VectorStoreError."""
    if backend is None:
        backend = "lancedb" if _lancedb_available() else "sqlite"
    if backend == "lancedb":
        return _LanceBackend(path)
    if backend == "sqlite":
        return _SqliteBackend(path)
    raise VectorStoreError(f"unknown vector-store backend: {backend!r}")


def backend_name(store):
    """The active backend's name ('lancedb' / 'sqlite') — for status/help output."""
    return store.NAME


def upsert(store, rows):
    store.upsert(rows)


def clear(store):
    store.clear()


def load(store):
    return store.load()


def index_model(store):
    return store.index_model()


def search(store, query_vec, k=10, threshold=None):
    return store.search(query_vec, k, threshold)


# --- shared max-pool aggregation ---------------------------------------------

def _maxpool(scored, k, threshold):
    """Collapse scored chunk rows to one hit per node, keyed on its single best
    (highest-cosine) chunk — so a node surfaces on the strength of its most relevant
    passage, never diluted by an averaged whole-node vector. ``scored`` yields
    (row_dict, cosine_score); rows expose node_id/title/status/priority and (for a
    store built by the current chunk-level reindex) chunk_text/chunk_kind. Returns up
    to k node hits sorted by score desc, dropping nodes whose best chunk < threshold."""
    best = {}
    for r, score in scored:
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


# --- LanceDB backend ----------------------------------------------------------

class _LanceBackend:
    """Fast embedded-columnar backend (the 'semantic' extra)."""

    NAME = "lancedb"

    def __init__(self, path):
        try:
            import lancedb
        except ImportError as e:
            raise VectorStoreError(
                "semantic search's fast backend needs the 'semantic' extra — install it "
                "with `pip install 'pyworklog[semantic]'` (pulls in lancedb), or it falls "
                "back to the slower pure-Python SQLite backend automatically."
            ) from e
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(path))

    def _table_names(self):
        """Existing table names as a plain list, across lancedb versions: newer
        `list_tables()` returns a response object with a `.tables` field, older
        exposes `table_names()` -> list[str]."""
        try:
            res = self.db.list_tables()
        except AttributeError:
            res = self.db.table_names()
        return list(getattr(res, "tables", res))

    def _table(self):
        """The 'vec' table, or None if it hasn't been created yet (empty store)."""
        if TABLE not in self._table_names():
            return None
        return self.db.open_table(TABLE)

    def upsert(self, rows):
        data = [
            {"node_id": r["node_id"], "title": r["title"] or "", "status": r["status"] or "",
             "priority": r["priority"] or "", "model": r["model"], "dim": r["dim"],
             "vector": list(r["vector"]), "chunk_text": r["chunk_text"] or "",
             "chunk_kind": r["chunk_kind"] or ""}
            for r in rows
        ]
        if not data:
            return
        tbl = self._table()
        if tbl is None:
            self.db.create_table(TABLE, data=data)
        else:
            tbl.add(data)

    def clear(self):
        if TABLE in self._table_names():
            self.db.drop_table(TABLE)

    def load(self):
        tbl = self._table()
        if tbl is None:
            return []
        return tbl.to_arrow().to_pylist()

    def index_model(self):
        rows = self.load()
        if not rows:
            return None
        return (rows[0]["model"], rows[0]["dim"])

    def search(self, query_vec, k=10, threshold=None):
        tbl = self._table()
        if tbl is None:
            return []
        n = tbl.count_rows()
        if not n:
            return []
        # All chunks scored (limit = row count) so the per-node max is exact at worklog scale.
        raw = tbl.search(list(query_vec)).metric("cosine").limit(n).to_list()
        return _maxpool(((r, 1.0 - r["_distance"]) for r in raw), k, threshold)


# --- SQLite fallback backend --------------------------------------------------

def _sqlite_path(path):
    """Sibling .sqlite3 file for a given vec-store path (replacing the .lancedb suffix
    used by the fast backend), so the two backends never share a file."""
    p = Path(path)
    return p.with_suffix(".sqlite3")


class _SqliteBackend:
    """Pure-stdlib fallback: chunk rows in a sqlite3 table, vectors as JSON, cosine
    computed in a Python loop (no numpy). Works on every Python/platform; linear-scan
    at query time, fine at worklog scale."""

    NAME = "sqlite"

    def __init__(self, path):
        self.path = _sqlite_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} ("
            "node_id INTEGER, title TEXT, status TEXT, priority TEXT, "
            "model TEXT, dim INTEGER, vector TEXT, chunk_text TEXT, chunk_kind TEXT)"
        )
        self.con.commit()

    def upsert(self, rows):
        # Whole-store rebuild (reindex does clear()+upsert()), so this only ever appends.
        data = [
            (r["node_id"], r["title"] or "", r["status"] or "", r["priority"] or "",
             r["model"], r["dim"], json.dumps(list(r["vector"])),
             r["chunk_text"] or "", r["chunk_kind"] or "")
            for r in rows
        ]
        if not data:
            return
        self.con.executemany(
            f"INSERT INTO {TABLE} "
            "(node_id, title, status, priority, model, dim, vector, chunk_text, chunk_kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            data,
        )
        self.con.commit()

    def clear(self):
        self.con.execute(f"DELETE FROM {TABLE}")
        self.con.commit()

    def load(self):
        rows = self.con.execute(
            f"SELECT node_id, title, status, priority, model, dim, vector, chunk_text, chunk_kind "
            f"FROM {TABLE}"
        ).fetchall()
        return [
            {"node_id": r["node_id"], "title": r["title"], "status": r["status"],
             "priority": r["priority"], "model": r["model"], "dim": r["dim"],
             "vector": json.loads(r["vector"]), "chunk_text": r["chunk_text"],
             "chunk_kind": r["chunk_kind"]}
            for r in rows
        ]

    def index_model(self):
        r = self.con.execute(f"SELECT model, dim FROM {TABLE} LIMIT 1").fetchone()
        return (r["model"], r["dim"]) if r else None

    def search(self, query_vec, k=10, threshold=None):
        q = list(query_vec)
        qn = math.sqrt(sum(x * x for x in q)) or 1.0

        def scored():
            for r in self.con.execute(
                f"SELECT node_id, title, status, priority, chunk_text, chunk_kind, vector FROM {TABLE}"
            ):
                v = json.loads(r["vector"])
                dot = sum(a * b for a, b in zip(q, v))
                vn = math.sqrt(sum(x * x for x in v)) or 1.0
                yield ({"node_id": r["node_id"], "title": r["title"], "status": r["status"],
                        "priority": r["priority"], "chunk_text": r["chunk_text"],
                        "chunk_kind": r["chunk_kind"]}, dot / (qn * vn))

        return _maxpool(scored(), k, threshold)
