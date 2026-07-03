"""Tests for the sidecar vector store (chunk-level + max-pool), run against BOTH
pluggable backends — LanceDB (the 'semantic' extra) and the pure-stdlib SQLite
fallback — so they prove behavioral parity.

A node is indexed as MULTIPLE chunk rows (a head chunk + one per log); search
scores chunks and aggregates to a node by its best (max) chunk, returning that
chunk as the match reason. No network: both backends are embedded, each test
gets its own tmp directory. The lancedb cases skip when the extra isn't installed;
the sqlite cases always run (stdlib only)."""
import sys
import pytest

from worklog import vectorstore as vs


@pytest.fixture(params=["lancedb", "sqlite"])
def store(tmp_path, request):
    """Every behavior test runs once per backend (lancedb skipped if absent)."""
    if request.param == "lancedb":
        pytest.importorskip("lancedb")
    return vs.connect(tmp_path / "wl.lancedb", backend=request.param)


def _chunk(node_id, vec, *, title="t", status="TODO", priority="A",
           text="chunk text", field="log", model="m", dim=2):
    return {"node_id": node_id, "title": title, "status": status, "priority": priority,
            "model": model, "dim": dim, "vector": vec, "chunk_text": text, "chunk_field": field}


class TestBackendSelection:
    def test_auto_falls_back_to_sqlite_without_lancedb(self, tmp_path, monkeypatch):
        # Graceful degradation: no lancedb → connect() silently uses the SQLite backend
        # (no exception), so `wl query` still works on platforms with no lancedb wheel.
        monkeypatch.setitem(sys.modules, "lancedb", None)
        store = vs.connect(tmp_path / "x.lancedb")
        assert vs.backend_name(store) == "sqlite"

    def test_forced_lancedb_without_it_raises_with_hint(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "lancedb", None)
        with pytest.raises(vs.VectorStoreError) as e:
            vs.connect(tmp_path / "x.lancedb", backend="lancedb")
        assert "semantic" in str(e.value).lower()

    def test_auto_prefers_lancedb_when_present(self, tmp_path):
        pytest.importorskip("lancedb")
        store = vs.connect(tmp_path / "x.lancedb")
        assert vs.backend_name(store) == "lancedb"

    def test_unknown_backend_raises(self, tmp_path):
        with pytest.raises(vs.VectorStoreError):
            vs.connect(tmp_path / "x.lancedb", backend="bogus")


class TestStore:
    def test_upsert_then_load_multiple_chunks_per_node(self, store):
        vs.upsert(store, [
            _chunk(1, [1.0, 0.0], text="head one", field="head"),
            _chunk(1, [0.0, 1.0], text="log a", field="log"),
            _chunk(2, [1.0, 1.0], text="head two", field="head"),
        ])
        rows = vs.load(store)
        assert len(rows) == 3
        n1 = [r for r in rows if r["node_id"] == 1]
        assert {r["chunk_field"] for r in n1} == {"head", "log"}

    def test_clear(self, store):
        vs.upsert(store, [_chunk(1, [1.0, 0.0])])
        vs.clear(store)
        assert vs.load(store) == []

    def test_load_empty_store(self, store):
        assert vs.load(store) == []

    def test_upsert_empty_is_noop(self, store):
        vs.upsert(store, [])
        assert vs.load(store) == []

    def test_second_upsert_appends(self, store):
        # the index is rebuilt wholesale, but a second upsert onto an existing table must append
        # (LanceDB `tbl.add` / SQLite second insert), not replace.
        vs.upsert(store, [_chunk(1, [1.0, 0.0])])
        vs.upsert(store, [_chunk(2, [0.0, 1.0])])
        assert {r["node_id"] for r in vs.load(store)} == {1, 2}

    def test_delete_nodes_evicts_by_id(self, store):
        # incremental reindex evicts deleted nodes via delete_nodes; all their chunks go
        vs.upsert(store, [_chunk(1, [1.0, 0.0], field="head"),
                          _chunk(1, [0.0, 1.0], field="log"),
                          _chunk(2, [1.0, 1.0])])
        vs.delete_nodes(store, [1])
        assert {r["node_id"] for r in vs.load(store)} == {2}

    def test_delete_nodes_empty_is_noop(self, store):
        vs.upsert(store, [_chunk(1, [1.0, 0.0])])
        vs.delete_nodes(store, [])
        assert len(vs.load(store)) == 1


class TestLanceVersionDrift:
    def test_table_names_falls_back_to_old_api(self, tmp_path):
        # Older lancedb has no list_tables() (only table_names()); _table_names must
        # degrade across that version drift instead of raising AttributeError.
        pytest.importorskip("lancedb")
        store = vs.connect(tmp_path / "x.lancedb", backend="lancedb")

        class OldApiDB:
            def table_names(self):
                return ["vec"]
        store.db = OldApiDB()
        assert store._table_names() == ["vec"]

    def test_index_model(self, store):
        vs.upsert(store, [_chunk(1, [1.0, 0.0], model="model-a", dim=1024)])
        assert vs.index_model(store) == ("model-a", 1024)

    def test_index_model_empty(self, store):
        assert vs.index_model(store) is None


class TestMaxPoolSearch:
    def test_aggregates_to_node_by_best_chunk(self, store):
        # node 1 has a weak head chunk + a strong log chunk aligned with the query;
        # max-pool must score node 1 by its BEST (the log) chunk, not an average.
        vs.upsert(store, [
            _chunk(1, [0.1, 1.0], title="n1", text="off-topic head", field="head"),
            _chunk(1, [1.0, 0.0], title="n1", text="the matching log", field="log"),
            _chunk(2, [0.0, 1.0], title="n2", text="n2 head", field="head"),
        ])
        hits = vs.search(store, [1.0, 0.0], k=2)
        assert hits[0]["node_id"] == 1
        assert hits[0]["score"] == pytest.approx(1.0, abs=1e-4)        # the log chunk, not diluted
        assert hits[0]["chunk_text"] == "the matching log"            # best chunk = the reason
        assert hits[0]["chunk_field"] == "log"

    def test_one_row_per_node(self, store):
        # three chunks for node 1, one for node 2 → exactly two node hits
        vs.upsert(store, [
            _chunk(1, [1.0, 0.0]), _chunk(1, [0.9, 0.1]), _chunk(1, [0.8, 0.2]),
            _chunk(2, [0.0, 1.0]),
        ])
        hits = vs.search(store, [1.0, 0.0], k=10)
        assert [h["node_id"] for h in hits] == [1, 2]

    def test_carries_node_metadata(self, store):
        vs.upsert(store, [_chunk(7, [1.0, 0.0], title="the title", status="DOING", priority="B")])
        h = vs.search(store, [1.0, 0.0], k=1)[0]
        assert h["node_id"] == 7 and h["title"] == "the title"
        assert h["status"] == "DOING" and h["priority"] == "B"

    def test_threshold_filters_on_best_chunk(self, store):
        vs.upsert(store, [
            _chunk(1, [1.0, 0.0]),     # cos 1.0
            _chunk(2, [0.0, 1.0]),     # cos ~0
        ])
        hits = vs.search(store, [1.0, 0.0], k=10, threshold=0.5)
        assert [h["node_id"] for h in hits] == [1]

    def test_search_empty_store(self, store):
        assert vs.search(store, [1.0, 0.0], k=5) == []
