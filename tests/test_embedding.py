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


class TestQueryPrompt:
    def _capture(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(emb, "_http_post", lambda u, p, h, t: seen.update(p) or _fake_resp([[1.0]]))
        return seen

    def test_query_substituted_at_placeholder(self, monkeypatch):
        seen = self._capture(monkeypatch)
        emb.embed(["how to X"], "query", {**CFG, "query_prompt": "search: {query}"})
        assert seen["input"] == ["search: how to X"]

    def test_backslash_n_becomes_newline(self, monkeypatch):
        seen = self._capture(monkeypatch)
        emb.embed(["q"], "query", {**CFG, "query_prompt": "Instruct: do\\nQuery:{query}"})
        assert seen["input"] == ["Instruct: do\nQuery:q"]

    def test_document_is_not_wrapped(self, monkeypatch):
        seen = self._capture(monkeypatch)
        emb.embed(["doc text"], "document", {**CFG, "query_prompt": "search: {query}"})
        assert seen["input"] == ["doc text"]

    def test_empty_prompt_no_wrap(self, monkeypatch):
        seen = self._capture(monkeypatch)
        emb.embed(["q"], "query", {**CFG, "query_prompt": ""})
        assert seen["input"] == ["q"]

    def test_missing_placeholder_raises(self, monkeypatch):
        self._capture(monkeypatch)
        with pytest.raises(emb.EmbeddingError) as e:
            emb.embed(["q"], "query", {**CFG, "query_prompt": "no placeholder here"})
        assert "{query}" in str(e.value)


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
            emb.embed(["a"], "query", CFG)   # single item: nothing to fall back to → error


class TestBatchFallback:
    def test_batch_400_falls_back_to_per_item(self, monkeypatch):
        seen = []

        def post(url, payload, headers, timeout):
            n = len(payload["input"])
            seen.append(n)
            if n > 1:
                raise urllib.error.HTTPError("u", 400, "array not supported", {}, None)
            return {"data": [{"index": 0, "embedding": [float(payload["input"][0] == "b")]}]}

        monkeypatch.setattr(emb, "_http_post", post)
        out = emb.embed(["a", "b"], "document", CFG)
        assert out == [[0.0], [1.0]]        # per-item order preserved
        assert seen == [2, 1, 1]            # tried batch once, then one at a time

    def test_non_400_http_error_does_not_retry(self, monkeypatch):
        calls = []

        def post(*a, **k):
            calls.append(1)
            raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

        monkeypatch.setattr(emb, "_http_post", post)
        with pytest.raises(emb.EmbeddingError):
            emb.embed(["a", "b"], "query", CFG)
        assert len(calls) == 1              # 401 → no per-item retry storm
