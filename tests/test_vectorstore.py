"""Tests for the LanceDB-backed sidecar vector store.

These need the 'semantic' extra (lancedb) installed — it's in the dev group.
No network: LanceDB is embedded, each test gets its own tmp directory."""
import sys
import pytest

from worklog import vectorstore as vs


@pytest.fixture
def store(tmp_path):
    return vs.connect(tmp_path / "wl.lancedb")


def _row(nid, title, st, pr, vec, model="m", dim=2):
    return (nid, title, st, pr, model, dim, vec)


class TestMissingDependency:
    def test_connect_without_lancedb_raises_with_hint(self, tmp_path, monkeypatch):
        # simulate the extra not being installed
        monkeypatch.setitem(sys.modules, "lancedb", None)
        with pytest.raises(vs.VectorStoreError) as e:
            vs.connect(tmp_path / "x.lancedb")
        assert "semantic" in str(e.value).lower()


class TestStore:
    def test_upsert_then_load(self, store):
        vs.upsert(store, [
            _row(1, "task one", "TODO", "A", [1.0, 0.0]),
            _row(2, "task two", "DONE", "B", [0.0, 1.0]),
        ])
        rows = {r["node_id"]: r for r in vs.load(store)}
        assert set(rows) == {1, 2}
        assert rows[1]["title"] == "task one" and rows[1]["status"] == "TODO"
        assert list(rows[1]["vector"]) == [1.0, 0.0]

    def test_upsert_replaces_same_node(self, store):
        vs.upsert(store, [_row(1, "v1", "TODO", "A", [1.0, 0.0])])
        vs.upsert(store, [_row(1, "v2", "DONE", "A", [0.0, 1.0])])
        rows = vs.load(store)
        assert len(rows) == 1
        assert rows[0]["title"] == "v2"
        assert list(rows[0]["vector"]) == [0.0, 1.0]

    def test_clear(self, store):
        vs.upsert(store, [_row(1, "x", "TODO", None, [1.0, 0.0])])
        vs.clear(store)
        assert vs.load(store) == []

    def test_load_empty_store(self, store):
        assert vs.load(store) == []

    def test_index_model(self, store):
        vs.upsert(store, [_row(1, "x", "TODO", None, [1.0, 0.0], model="model-a", dim=1024)])
        assert vs.index_model(store) == ("model-a", 1024)

    def test_index_model_empty(self, store):
        assert vs.index_model(store) is None


class TestSearch:
    def test_ranks_by_cosine(self, store):
        vs.upsert(store, [
            _row(1, "north", "TODO", "A", [1.0, 0.0]),
            _row(2, "east", "TODO", "A", [0.0, 1.0]),
            _row(3, "northish", "TODO", "A", [0.9, 0.1]),
        ])
        hits = vs.search(store, [1.0, 0.0], k=2)
        assert [h["node_id"] for h in hits] == [1, 3]
        assert hits[0]["score"] > hits[1]["score"]
        assert hits[0]["score"] == pytest.approx(1.0, abs=1e-4)  # identical direction

    def test_search_carries_metadata(self, store):
        vs.upsert(store, [_row(7, "the title", "DOING", "B", [1.0, 0.0])])
        h = vs.search(store, [1.0, 0.0], k=1)[0]
        assert h["node_id"] == 7 and h["title"] == "the title"
        assert h["status"] == "DOING" and h["priority"] == "B"

    def test_threshold_filters(self, store):
        vs.upsert(store, [
            _row(1, "aligned", "TODO", "A", [1.0, 0.0]),
            _row(2, "orthogonal", "TODO", "A", [0.0, 1.0]),
        ])
        hits = vs.search(store, [1.0, 0.0], k=10, threshold=0.5)
        assert [h["node_id"] for h in hits] == [1]  # #2 (cos≈0) cut by threshold

    def test_search_empty_store(self, store):
        assert vs.search(store, [1.0, 0.0], k=5) == []
