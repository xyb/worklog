"""Embedding client — talks to any OpenAI-compatible ``/v1/embeddings`` HTTP server.

The backend is resolved by :mod:`worklog.config` (default = a local HTTP server).
Pure stdlib (urllib), no third-party HTTP dependency. The actual POST is isolated
in ``_http_post`` so the command layer and tests can drive it without a live
server. Failures raise :class:`EmbeddingError` with the endpoint named, which the
command layer turns into a clean, actionable message."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class EmbeddingError(RuntimeError):
    """Embedding backend unreachable or returned an error."""


def _http_post(url, payload, headers, timeout):
    """POST json, return parsed json. Isolated for testing/mocking."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def embed(texts, input_type, cfg, timeout=120):
    """Embed ``texts`` (list[str]) → list[list[float]], order matching the input.

    input_type: 'document' (indexing) or 'query' (searching) — an OpenAI extension
    some servers honor for asymmetric retrieval; harmless to backends that ignore it."""
    payload = {"model": cfg["model"], "input": texts, "input_type": input_type}
    if cfg.get("dimensions") is not None:
        payload["dimensions"] = cfg["dimensions"]
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        resp = _http_post(cfg["endpoint"], payload, headers, timeout)
    except (urllib.error.URLError, OSError) as e:
        raise EmbeddingError(
            f"embedding backend unreachable at {cfg['endpoint']} ({e}). "
            f"Is the server running? Configure it in `wl config` / config.ini "
            f"[embedding], $WORKLOG_EMBED_ENDPOINT, or --endpoint."
        ) from e
    items = sorted(resp["data"], key=lambda d: d["index"])
    return [it["embedding"] for it in items]
