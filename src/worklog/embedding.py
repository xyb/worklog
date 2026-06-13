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


def _request(texts, input_type, cfg, timeout):
    """One embedding HTTP call for a batch of texts; raises urllib errors raw."""
    payload = {"model": cfg["model"], "input": texts, "input_type": input_type}
    if cfg.get("dimensions") is not None:
        payload["dimensions"] = cfg["dimensions"]
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    resp = _http_post(cfg["endpoint"], payload, headers, timeout)
    return [it["embedding"] for it in sorted(resp["data"], key=lambda d: d["index"])]


def embed(texts, input_type, cfg, timeout=120):
    """Embed ``texts`` (list[str]) → list[list[float]], order matching the input.

    Sends the whole list as one batched request. If the server **rejects a batch**
    (HTTP 400/422 — some OpenAI-compatible servers accept only a single string), it
    transparently falls back to one request per text. A connection error (server down)
    propagates immediately — no point retrying N times.

    input_type: 'document' (indexing) or 'query' (searching) — an OpenAI extension
    some servers honor for asymmetric retrieval; harmless to backends that ignore it.

    For queries, if cfg['query_prompt'] is set, each text is substituted into that template at
    its `{query}` placeholder (the asymmetric-retrieval prefix; `\\n` in the template = newline).
    Empty template = no wrapping (for a server that applies the instruction itself, e.g. nmem)."""
    prompt = cfg.get("query_prompt")
    if input_type == "query" and prompt:
        prompt = prompt.replace("\\n", "\n")   # let a config value spell the newline as \n
        if "{query}" not in prompt:
            raise EmbeddingError(
                "query_prompt must contain the literal {query} placeholder (where the query text "
                f"goes) — got: {prompt!r}. Fix it in `wl config` / config.ini [embedding], or set "
                "it empty to disable."
            )
        texts = [prompt.replace("{query}", t) for t in texts]
    try:
        return _request(texts, input_type, cfg, timeout)
    except urllib.error.HTTPError as e:
        if len(texts) > 1 and e.code in (400, 422):
            out = []
            for t in texts:   # batch unsupported → one text at a time
                out.extend(embed([t], input_type, cfg, timeout))
            return out
        raise EmbeddingError(
            f"embedding backend returned HTTP {e.code} at {cfg['endpoint']} — "
            f"check the model name / endpoint in `wl config`."
        ) from e
    except (urllib.error.URLError, OSError) as e:
        raise EmbeddingError(
            f"embedding backend unreachable at {cfg['endpoint']} ({e}). "
            f"Is the server running? Configure it in `wl config` / config.ini "
            f"[embedding], $WORKLOG_EMBED_ENDPOINT, or --endpoint."
        ) from e
