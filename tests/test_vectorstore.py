"""Tests for the LanceDB-backed sidecar vector store (chunk-level + max-pool).

A node is indexed as MULTIPLE chunk rows (a head chunk + one per log); search
scores chunks and aggregates to a node by its best (max) chunk, returning that
chunk as the match reason. Needs the 'semantic' extra (lancedb, in the dev group).
No network: LanceDB is embedded, each test gets its own tmp directory."""
import sys
import pytest

from worklog import vectorstore as vs


@pytest.fixture
def store(tmp_path):
    return vs.connect(tmp_path / "wl.lancedb")


def _chunk(node_id, vec, *, title="t", status="TODO", priority="A",
           text="chunk text", kind="log", model="m", dim=2):
    return {"node_id": node_id, "title": title, "status": status, "priority": priority,
            "model": model, "dim": dim, "vector": vec, "chunk_text": text, "chunk_kind": kind}


class TestMissingDependency:
    def test_connect_without_lancedb_raises_with_hint(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "lancedb", None)
        with pytest.raises(vs.VectorStoreError) as e:
            vs.connect(tmp_path / "x.lancedb")
        assert "semantic" in str(e.value).lower()


class TestStore:
    def test_upsert_then_load_multiple_chunks_per_node(self, store):
        vs.upsert(store, [
            _chunk(1, [1.0, 0.0], text="head one", kind="head"),
            _chunk(1, [0.0, 1.0], text="log a", kind="log"),
            _chunk(2, [1.0, 1.0], text="head two", kind="head"),
        ])
        rows = vs.load(store)
        assert len(rows) == 3
        n1 = [r for r in rows if r["node_id"] == 1]
        assert {r["chunk_kind"] for r in n1} == {"head", "log"}

    def test_clear(self, store):
        vs.upsert(store, [_chunk(1, [1.0, 0.0])])
        vs.clear(store)
        assert vs.load(store) == []

    def test_load_empty_store(self, store):
        assert vs.load(store) == []

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
            _chunk(1, [0.1, 1.0], title="n1", text="off-topic head", kind="head"),
            _chunk(1, [1.0, 0.0], title="n1", text="the matching log", kind="log"),
            _chunk(2, [0.0, 1.0], title="n2", text="n2 head", kind="head"),
        ])
        hits = vs.search(store, [1.0, 0.0], k=2)
        assert hits[0]["node_id"] == 1
        assert hits[0]["score"] == pytest.approx(1.0, abs=1e-4)        # the log chunk, not diluted
        assert hits[0]["chunk_text"] == "the matching log"            # best chunk = the reason
        assert hits[0]["chunk_kind"] == "log"

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
