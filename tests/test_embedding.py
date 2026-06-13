"""Tests for the embedding client. The HTTP layer (_http_post) is monkeypatched,
so these never touch a real backend — they verify payload/headers/parsing/errors."""
import json
import urllib.error
import pytest

from worklog import embedding as emb

CFG = {
    "endpoint": "http://backend/v1/embeddings",
    "model": "test-model",
    "dimensions": None,
    "api_key": None,
}


def _fake_resp(vectors):
    """An OpenAI-style response with data deliberately out of index order."""
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {"data": list(reversed(data)), "model": "test-model"}


class TestPayload:
    def test_payload_basics(self, monkeypatch):
        seen = {}

        def fake_post(url, payload, headers, timeout):
            seen["url"], seen["payload"], seen["headers"] = url, payload, headers
            return _fake_resp([[1.0], [2.0]])

        monkeypatch.setattr(emb, "_http_post", fake_post)
        emb.embed(["a", "b"], "document", CFG)
        assert seen["url"] == "http://backend/v1/embeddings"
        assert seen["payload"]["model"] == "test-model"
        assert seen["payload"]["input"] == ["a", "b"]
        assert seen["payload"]["input_type"] == "document"
        assert "dimensions" not in seen["payload"]  # None -> omitted

    def test_dimensions_included_when_set(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(emb, "_http_post", lambda u, p, h, timeout: seen.update(p) or _fake_resp([[1.0]]))
        emb.embed(["a"], "query", {**CFG, "dimensions": 256})
        assert seen["dimensions"] == 256

    def test_api_key_sets_auth_header(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(emb, "_http_post", lambda u, p, h, timeout: seen.update(headers=h) or _fake_resp([[1.0]]))
        emb.embed(["a"], "query", {**CFG, "api_key": "sk-xyz"})
        assert seen["headers"].get("Authorization") == "Bearer sk-xyz"

    def test_no_auth_header_without_key(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(emb, "_http_post", lambda u, p, h, timeout: seen.update(headers=h) or _fake_resp([[1.0]]))
        emb.embed(["a"], "query", CFG)
        assert "Authorization" not in seen["headers"]


class TestParsing:
    def test_orders_by_index(self, monkeypatch):
        # response is reversed; embed() must reorder by 'index' to match input order
        monkeypatch.setattr(emb, "_http_post", lambda u, p, h, timeout: _fake_resp([[1.0], [2.0], [3.0]]))
        out = emb.embed(["x", "y", "z"], "document", CFG)
        assert out == [[1.0], [2.0], [3.0]]


class TestErrors:
    def test_unreachable_raises_embeddingerror(self, monkeypatch):
        def boom(*a, **k):
            raise urllib.error.URLError("Connection refused")
        monkeypatch.setattr(emb, "_http_post", boom)
        with pytest.raises(emb.EmbeddingError) as e:
            emb.embed(["a"], "query", CFG)
        # message must name the endpoint so the user knows what to fix
        assert "http://backend/v1/embeddings" in str(e.value)

    def test_http_error_raises_embeddingerror(self, monkeypatch):
        def boom(*a, **k):
            raise urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
        monkeypatch.setattr(emb, "_http_post", boom)
        with pytest.raises(emb.EmbeddingError):
            emb.embed(["a"], "query", CFG)
